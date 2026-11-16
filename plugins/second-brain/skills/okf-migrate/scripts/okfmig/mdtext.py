"""Markdown text helpers: code ranges and Obsidian inline tags."""

import re

_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_TAG = re.compile(r"(?<!\S)#([\w/-]+)")


def code_ranges(text: str) -> list[tuple[int, int]]:
    """Char ranges of fenced code blocks and inline code spans, sorted and non-overlapping."""
    ranges: list[tuple[int, int]] = []
    prose_start = 0
    fence: str | None = None
    fence_start = 0
    pos = 0
    for line in text.splitlines(keepends=True):
        line_end = pos + len(line)
        if fence is None:
            match = _FENCE_OPEN.match(line)
            if match:
                _add_inline(text, prose_start, pos, ranges)
                fence = match.group(1)
                fence_start = pos
        else:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                ranges.append((fence_start, line_end))
                fence = None
                prose_start = line_end
        pos = line_end
    if fence is not None:
        ranges.append((fence_start, len(text)))
    else:
        _add_inline(text, prose_start, len(text), ranges)
    return ranges


def _add_inline(text: str, start: int, end: int, ranges: list[tuple[int, int]]) -> None:
    for match in _INLINE_CODE.finditer(text, start, end):
        ranges.append((match.start(), match.end()))


def in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def inline_tags(body: str) -> set[str]:
    """Obsidian #tags outside code, lowercased and without the leading '#'."""
    ranges = code_ranges(body)
    tags: set[str] = set()
    for match in _TAG.finditer(body):
        tag = match.group(1)
        if in_ranges(match.start(), ranges) or tag.isdigit():
            continue
        tags.add(tag.lower())
    return tags
