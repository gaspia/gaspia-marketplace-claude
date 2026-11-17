"""Thin git helpers. A vault or brain is any folder inside a git work tree; `repo_location` gives its prefix."""

import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath

_MIN_ENTRY = 4  # "XY p"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def is_repo(root: Path) -> bool:
    """True only when `root` is the top-level of a git work tree."""
    try:
        top = _git(root, "rev-parse", "--show-toplevel").strip()
    except subprocess.CalledProcessError, OSError:
        return False
    return Path(top).resolve() == root.resolve()


def dirty_paths(root: Path) -> list[str]:
    """Paths with uncommitted changes, ignoring .obsidian/ and .okf/."""
    out = _git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude).obsidian",
        ":(exclude).okf",
    )
    entries = out.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < _MIN_ENTRY:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if "R" in status or "C" in status:
            i += 1  # skip the original path that follows a rename/copy
    return paths


def ignored_paths(root: Path) -> list[PurePosixPath]:
    """Untracked paths git ignores; a wholly ignored directory is listed once, without its contents."""
    out = _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z")
    paths = {PurePosixPath(entry.rstrip("/")) for entry in out.split("\0") if entry}
    # git also lists the contents of a directory that holds only ignored files; keep just the directory
    return sorted(p for p in paths if not any(parent in paths for parent in p.parents))


def repo_location(folder: Path) -> tuple[Path, PurePosixPath] | None:
    """The top-level of the git work tree holding `folder` and the folder's path from it (`.` at the top), in the
    letter case git and the file system use; None outside a work tree."""
    try:
        top, prefix, *_ = [*_git(folder, "rev-parse", "--show-toplevel", "--show-prefix").split("\n"), "", ""]
    except subprocess.CalledProcessError, OSError:
        return None
    if not top:
        return None
    return Path(top).resolve(), PurePosixPath(prefix.rstrip("/") or ".")


def _strip(path: PurePosixPath, prefix: PurePosixPath) -> PurePosixPath | None:
    """`path` (relative to the repo top) relative to `prefix`, or None when it lies outside."""
    if prefix == PurePosixPath("."):
        return path
    return PurePosixPath(path.relative_to(prefix).as_posix()) if path.is_relative_to(prefix) else None


def dirty_under(folder: Path) -> list[str]:
    """Uncommitted changes under `folder`, relative to it, ignoring its `.obsidian/` and `.okf/`; [] outside git."""
    location = repo_location(folder)
    if location is None:
        return []
    _, prefix = location
    paths: list[str] = []
    for path in dirty_paths(folder):
        rel = _strip(PurePosixPath(path.rstrip("/")), prefix)
        if rel is not None:
            paths.append(rel.as_posix() + ("/" if path.endswith("/") else ""))
    return paths


def ignored_under(root: Path) -> list[PurePosixPath]:
    """Git-ignored paths under `root`, relative to `root` (which need not be the repo top-level); [] outside git.

    When `root` itself lies inside an ignored directory, the result is `[.]` (everything is ignored).
    """
    location = repo_location(root)
    if location is None:
        return []
    top, prefix = location
    ignored = ignored_paths(top)
    if any(prefix.is_relative_to(p) for p in ignored):
        return [PurePosixPath(".")]
    return sorted(rel for p in ignored if (rel := _strip(p, prefix)) is not None)


def last_commit_times(root: Path) -> dict[PurePosixPath, str]:
    """Newest commit time (ISO 8601 with offset) per tracked path."""
    out = _git(root, "log", "--format=%x00%cI", "--name-only")
    times: dict[PurePosixPath, str] = {}
    for chunk in out.split("\0"):
        lines = [line for line in chunk.split("\n") if line]
        if not lines:
            continue
        stamp, files = lines[0], lines[1:]
        for name in files:
            times.setdefault(PurePosixPath(name), stamp)
    return times


def commit_times_under(root: Path) -> dict[PurePosixPath, str]:
    """`last_commit_times` keyed relative to `root` (which need not be the repo top-level); {} outside git or
    in a repo without commits."""
    location = repo_location(root)
    if location is None:
        return {}
    top, prefix = location
    try:
        times = last_commit_times(top)
    except subprocess.CalledProcessError:
        return {}
    return {rel: t for p, t in times.items() if (rel := _strip(p, prefix)) is not None}


def mtime_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def ensure_info_exclude(root: Path, entry: str = ".okf-migrate/") -> None:
    exclude = root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if entry in text.splitlines():
        return
    if text and not text.endswith("\n"):
        text += "\n"
    exclude.write_text(text + entry + "\n", encoding="utf-8")
