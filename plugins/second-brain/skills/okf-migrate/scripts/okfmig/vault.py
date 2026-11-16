"""Vault discovery: which files Obsidian indexes and which of them are notes."""

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ALWAYS_EXCLUDED_DIRS = frozenset({".obsidian", ".okf", ".git", ".trash", ".okf-migrate"})
DEFAULT_EXCLUDED_FILES = ("**/CLAUDE.md", "**/AGENTS.md")
RESERVED_NAMES = frozenset({"index.md", "log.md"})


@dataclass(frozen=True)
class Note:
    path: PurePosixPath
    text: str


@dataclass
class ObsidianSettings:
    new_file_folder: PurePosixPath


@dataclass
class Vault:
    root: Path
    notes: list[Note]
    reserved: list[Note]
    files: list[PurePosixPath]
    excluded: list[PurePosixPath]
    undecodable: list[PurePosixPath]
    settings: ObsidianSettings


def _read_settings(root: Path) -> ObsidianSettings:
    default = ObsidianSettings(PurePosixPath("."))
    try:
        data = json.loads((root / ".obsidian" / "app.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return default
    if isinstance(data, dict) and data.get("newFileLocation") == "folder":
        folder = data.get("newFileFolderPath")
        if isinstance(folder, str) and folder:
            return ObsidianSettings(PurePosixPath(folder))
    return default


def _is_excluded(rel: PurePosixPath, patterns: Sequence[str]) -> bool:
    return any(rel.full_match(pattern) for pattern in patterns)


def _ignored_by(rel: PurePosixPath, ignored: set[PurePosixPath]) -> PurePosixPath | None:
    """The ignored path that covers `rel` (itself or an ignored ancestor directory), if any."""
    return next((p for p in (rel, *rel.parents) if p in ignored), None)


def discover(root: Path, extra_excludes: Sequence[str] = (), ignored: Iterable[PurePosixPath] = ()) -> Vault:
    """Walk the vault. `ignored` holds git-ignored paths: kept in `files` for link resolution, never notes."""
    patterns = (*DEFAULT_EXCLUDED_FILES, *extra_excludes)
    ignored_set = set(ignored)
    notes: list[Note] = []
    reserved: list[Note] = []
    files: list[PurePosixPath] = []
    excluded: list[PurePosixPath] = []
    undecodable: list[PurePosixPath] = []

    repo_roots: list[PurePosixPath] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ALWAYS_EXCLUDED_DIRS)
        here = Path(dirpath)
        rel_dir = PurePosixPath(here.relative_to(root).as_posix())
        if here != root and (here / ".git").exists():
            repo_roots.append(rel_dir)
            excluded.append(rel_dir)
        in_repo = any(rel_dir.is_relative_to(repo) for repo in repo_roots)
        for name in sorted(filenames):
            rel = rel_dir / name
            files.append(rel)
            if not name.endswith(".md") or in_repo:
                continue
            covering = _ignored_by(rel, ignored_set)
            if covering is not None:
                if covering not in excluded:
                    excluded.append(covering)
                continue
            if _is_excluded(rel, patterns):
                excluded.append(rel)
                continue
            try:
                text = (here / name).read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                undecodable.append(rel)
                continue
            (reserved if name in RESERVED_NAMES else notes).append(Note(rel, text))

    return Vault(
        root=root,
        notes=sorted(notes, key=lambda n: n.path),
        reserved=sorted(reserved, key=lambda n: n.path),
        files=sorted(files),
        excluded=sorted(excluded),
        undecodable=sorted(undecodable),
        settings=_read_settings(root),
    )
