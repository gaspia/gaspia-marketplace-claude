"""Vault discovery: which files Obsidian indexes and which of them are notes."""

import json
import os
from collections.abc import Sequence
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


def discover(root: Path, extra_excludes: Sequence[str] = ()) -> Vault:
    patterns = (*DEFAULT_EXCLUDED_FILES, *extra_excludes)
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
