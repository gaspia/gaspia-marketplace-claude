"""Thin git helpers for the vault (the vault root is the repo top-level)."""

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


def ignored_under(root: Path) -> list[PurePosixPath]:
    """Git-ignored paths under `root`, relative to `root` (which need not be the repo top-level); [] outside git.

    When `root` itself lies inside an ignored directory, the result is `[.]` (everything is ignored).
    """
    try:
        top = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()
        ignored = ignored_paths(top)
    except subprocess.CalledProcessError, OSError:
        return []
    prefix = PurePosixPath(root.resolve().relative_to(top).as_posix())
    if prefix == PurePosixPath("."):
        return ignored
    if any(prefix.is_relative_to(p) for p in ignored):
        return [PurePosixPath(".")]
    return sorted(PurePosixPath(p.relative_to(prefix).as_posix()) for p in ignored if p.is_relative_to(prefix))


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
