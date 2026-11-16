"""Wikilink resolution (Obsidian rules) and rewriting to relative markdown links."""

import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import unquote

from okfmig.mdtext import code_ranges, in_ranges

Status = Literal["resolved", "ambiguous", "unresolved", "date"]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$")

_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_PIPE = re.compile(r"\\?\|")
_MD_LINK = re.compile(
    r"(?P<prefix>!?\[(?:\\.|[^\[\]\\\n])*\]\(\s*)"
    r"(?P<target><[^<>\n]*>|[^\s()<>]*(?:\([^\s()]*\)[^\s()<>]*)*)"
    r"(?P<suffix>(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\s*\))"
)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
_PATH_ESCAPES = {" ": "%20", "(": "%28", ")": "%29", "#": "%23", "%": "%25", "^": "%5E"}


@dataclass(frozen=True)
class LinkChange:
    original: str
    replacement: str
    status: Status
    target: PurePosixPath | None
    candidates: tuple[PurePosixPath, ...] = ()


def encode_path(p: str) -> str:
    return "".join(_PATH_ESCAPES.get(ch, ch) for ch in p)


def encode_anchor(h: str) -> str:
    return h.replace(" ", "%20")


def relative_href(source: PurePosixPath, target: PurePosixPath) -> str:
    return encode_path(posixpath.relpath(str(target), str(source.parent)))


def _extension(target: str) -> str:
    """Lowercased file extension of a link target; dotted names like `Release v1.2` have none."""
    match = _EXTENSION.search(PurePosixPath(target).name)
    return match.group(0).lower() if match else ""


def _shared_leading(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    count = 0
    for x, y in zip(a, b, strict=False):
        if x.lower() != y.lower():
            break
        count += 1
    return count


class Resolver:
    """Resolves wikilink targets against vault files the way Obsidian does (case-insensitive)."""

    def __init__(self, files: Iterable[PurePosixPath], new_file_folder: PurePosixPath) -> None:
        self.files = list(files)
        self.new_file_folder = new_file_folder
        self._by_path: dict[str, list[PurePosixPath]] = {}
        self._by_name: dict[str, list[PurePosixPath]] = {}
        for f in self.files:
            self._by_path.setdefault(str(f).lower(), []).append(f)
            self._by_name.setdefault(f.name.lower(), []).append(f)

    def resolve(self, target: str, source: PurePosixPath) -> tuple[PurePosixPath | None, tuple[PurePosixPath, ...]]:
        target = target.strip().lstrip("/")
        if not target:
            return None, ()
        for name in self._lookup_names(target):
            found = self._match(name)
            if found:
                ranked = sorted(found, key=lambda p: self._rank(p, source))
                return ranked[0], tuple(ranked)
        return None, ()

    @staticmethod
    def _lookup_names(target: str) -> list[str]:
        suffix = _extension(target)
        if suffix == ".md":
            return [target]
        if suffix:
            return [target, target + ".md"]
        return [target + ".md", target]

    def _match(self, name: str) -> list[PurePosixPath]:
        key = name.lower()
        if "/" not in key:
            return list(self._by_name.get(key, []))
        exact = self._by_path.get(key)
        if exact:
            return list(exact)
        parts = tuple(PurePosixPath(key).parts)
        return [f for f in self.files if tuple(p.lower() for p in f.parts[-len(parts) :]) == parts]

    @staticmethod
    def _rank(path: PurePosixPath, source: PurePosixPath) -> tuple[int, int, str]:
        return len(path.parts), -_shared_leading(path.parts, source.parent.parts), str(path)


def _escape_text(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _split_wikilink(inner: str) -> tuple[str, str, str | None]:
    """Split `target#heading|alias` (pipe may be `\\|`) into target, heading, alias."""
    alias: str | None = None
    pipe = _PIPE.search(inner)
    if pipe:
        inner, alias = inner[: pipe.start()], inner[pipe.end() :]
    target, _, heading = inner.partition("#")
    return target.strip(), heading.strip(), alias


def _unresolved_path(target: str, new_file_folder: PurePosixPath) -> PurePosixPath:
    path = PurePosixPath(target)
    if not _extension(target):
        path = path.with_name(path.name + ".md")
    if "/" in target:
        return path
    return new_file_folder / path


def rewrite_wikilinks(
    body: str,
    source: PurePosixPath,
    resolver: Resolver,
    renames: Mapping[PurePosixPath, PurePosixPath],
) -> tuple[str, list[LinkChange]]:
    ranges = code_ranges(body)
    changes: list[LinkChange] = []

    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        if in_ranges(match.start(), ranges):
            return original
        embed = match.group(1) == "!"
        target, heading, alias = _split_wikilink(match.group(2))
        anchor = "#" + encode_anchor(heading) if heading else ""

        if not target:
            text = alias if alias is not None else heading
            replacement = f"[{_escape_text(text)}]({anchor})"
            changes.append(LinkChange(original, replacement, "resolved", source))
            return replacement

        resolved, candidates = resolver.resolve(target, source)
        status: Status
        if resolved is not None:
            path = renames.get(resolved, resolved)
            status = "ambiguous" if len(candidates) > 1 else "resolved"
        elif not embed and not heading and DATE_RE.match(target):
            replacement = alias if alias is not None else target
            changes.append(LinkChange(original, replacement, "date", None))
            return replacement
        else:
            path = _unresolved_path(target, resolver.new_file_folder)
            status = "unresolved"

        if embed:
            text = target + (f"#{heading}" if heading else "") + (f"|{alias}" if alias is not None else "")
        elif alias is not None:
            text = alias
        elif heading and not heading.startswith("^"):
            text = f"{target} > {heading}"
        else:
            text = target
        bang = "!" if embed else ""
        replacement = f"{bang}[{_escape_text(text)}]({relative_href(source, path)}{anchor})"
        changes.append(LinkChange(original, replacement, status, path, candidates))
        return replacement

    return _WIKILINK.sub(replace, body), changes


def _local_target(raw: str) -> tuple[str, str, bool] | None:
    """Split a relative local link target into (decoded path, '#anchor' suffix, angle form); None if not local."""
    angle = raw.startswith("<") and raw.endswith(">")
    href = raw[1:-1] if angle else raw
    if not href or href.startswith(("#", "/")) or _SCHEME.match(href):
        return None
    path, sep, anchor = href.partition("#")
    if not path:
        return None
    return (path if angle else unquote(path)), sep + anchor, angle


def _vault_path(source: PurePosixPath, rel: str) -> PurePosixPath | None:
    joined = posixpath.normpath(posixpath.join(str(source.parent), rel))
    if joined == ".." or joined.startswith("../"):
        return None
    return PurePosixPath(joined)


def rewrite_md_targets(
    body: str,
    source_old: PurePosixPath,
    source_new: PurePosixPath,
    renames: Mapping[PurePosixPath, PurePosixPath],
) -> tuple[str, int]:
    ranges = code_ranges(body)
    moved = source_old.parent != source_new.parent
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        original = match.group(0)
        if in_ranges(match.start(), ranges):
            return original
        local = _local_target(match.group("target"))
        if local is None:
            return original
        rel, anchor, _ = local
        old_path = _vault_path(source_old, rel)
        if old_path is None:
            return original
        new_path = renames.get(old_path, old_path)
        if new_path == old_path and not moved:
            return original
        replacement = match.group("prefix") + relative_href(source_new, new_path) + anchor + match.group("suffix")
        if replacement != original:
            changed += 1
        return replacement

    return _MD_LINK.sub(replace, body), changed


def linked_paths(body: str, source: PurePosixPath) -> set[PurePosixPath]:
    ranges = code_ranges(body)
    paths: set[PurePosixPath] = set()
    for match in _MD_LINK.finditer(body):
        if in_ranges(match.start(), ranges):
            continue
        local = _local_target(match.group("target"))
        if local is None:
            continue
        path = _vault_path(source, local[0])
        if path is not None:
            paths.add(path)
    return paths


def wikilinks_in(text: str) -> list[str]:
    ranges = code_ranges(text)
    return [m.group(0) for m in _WIKILINK.finditer(text) if not in_ranges(m.start(), ranges)]
