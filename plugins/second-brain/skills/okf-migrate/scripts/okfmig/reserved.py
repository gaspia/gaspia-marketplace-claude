"""OKF reserved files: index.md and log.md handling."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from okfmig.frontmatter import BOM, strip

GENERATED_MARKER = "<!-- okf-migrate:generated -->"
OKF_VERSION = "0.2"
DATED_ITEM_RE = re.compile(r"^[-*][ \t]+(\d{4}-\d{2}-\d{2})(?:[ \t]*:[ \t]*|[ \t]+)(.*)$")

_DATE_HEADING_RE = re.compile(r"^##[ \t]+(\d{4}-\d{2}-\d{2})[ \t]*$")
_UNDATED_HEADING_RE = re.compile(r"^##[ \t]+Undated[ \t]*$")
_UNLISTED_RE = re.compile(r"^#[ \t]+Unlisted[ \t]*$")
_ANY_HEADING_RE = re.compile(r"^#{1,6}[ \t]")
_TOP_HEADING_RE = re.compile(r"^#{1,2}[ \t]")
_H1_RE = re.compile(r"^#[ \t]+\S")
_LIST_ITEM_RE = re.compile(r"^[-*+][ \t]")
_LINK_ITEM_RE = re.compile(r"^[ \t]*[-*+][ \t]+.*(\[[^\]]*\]\([^)]*\)|\[\[[^\]]+\]\])")


@dataclass(frozen=True)
class Entry:
    title: str
    href: str
    description: str | None


def _nl(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _split_lines(text: str) -> list[str]:
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def _content(line: str) -> str:
    return line.rstrip("\r\n")


def _ending(line: str, default: str) -> str:
    return line[len(_content(line)) :] or default


def _is_blank(line: str) -> bool:
    return not line.strip()


def _with_nl(line: str, nl: str) -> str:
    return line if line.endswith("\n") else line + nl


def _trim_blank(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and _is_blank(lines[start]):
        start += 1
    while end > start and _is_blank(lines[end - 1]):
        end -= 1
    return lines[start:end]


def classify(name: Literal["index.md", "log.md"], text: str) -> Literal["index", "log", "note"]:
    lines = [_content(line) for line in _split_lines(text)]
    if name == "index.md":
        if GENERATED_MARKER in text or any(_LINK_ITEM_RE.match(line) for line in lines):
            return "index"
        return "note"
    if any(DATED_ITEM_RE.match(line) or _DATE_HEADING_RE.match(line) for line in lines):
        return "log"
    return "note"


def render_entry(e: Entry) -> str:
    base = f"* [{e.title}]({e.href})"
    return base if e.description is None else f"{base} - {e.description}"


def _root_block(nl: str) -> str:
    return nl.join(["---", f'okf_version: "{OKF_VERSION}"', "---", "", ""])


def render_generated_index(title: str, notes: list[Entry], subfolders: list[Entry], root: bool) -> str:
    out = (f'---\nokf_version: "{OKF_VERSION}"\n---\n\n' if root else "") + f"# {title}\n\n"
    for e in sorted(notes, key=lambda e: e.title.casefold()):
        out += render_entry(e) + "\n"
    if subfolders:
        out += ("\n" if notes else "") + "# Subfolders\n\n"
        for e in sorted(subfolders, key=lambda e: e.title.casefold()):
            out += render_entry(e) + "\n"
    return out + f"\n{GENERATED_MARKER}\n"


def is_generated(text: str) -> bool:
    lines = [line.strip() for line in _split_lines(text) if line.strip()]
    return bool(lines) and lines[-1] == GENERATED_MARKER


def update_curated_index(text: str, missing: list[Entry], root: bool) -> str:
    nl = _nl(text)
    bom = BOM if text.startswith(BOM) else ""
    body = strip(text)[len(bom) :]
    if root:
        body = _root_block(nl) + body
    lines = _split_lines(body)
    if missing:
        lines = _add_unlisted(lines, [render_entry(e) + nl for e in missing], nl)
    return bom + "".join(lines)


def _add_unlisted(lines: list[str], entries: list[str], nl: str) -> list[str]:
    head = next((i for i, line in enumerate(lines) if _UNLISTED_RE.match(_content(line))), None)
    if head is None:
        out = [*lines]
        if out:
            out[-1] = _with_nl(out[-1], nl)
            if not _is_blank(out[-1]):
                out.append(nl)
        return [*out, f"# Unlisted{nl}", nl, *entries]
    end = next(
        (i for i in range(head + 1, len(lines)) if _ANY_HEADING_RE.match(_content(lines[i]))),
        len(lines),
    )
    last = head
    for i in range(head + 1, end):
        if not _is_blank(lines[i]):
            last = i
    lines = [*lines]
    lines[last] = _with_nl(lines[last], nl)
    insert = entries if last != head else [nl, *entries]
    return [*lines[: last + 1], *insert, *lines[last + 1 :]]


def regroup_log(text: str) -> str:
    nl = _nl(text)
    lines = _split_lines(strip(text))
    title, lines = _take_title(lines, nl)
    groups, undated = _collect_groups(lines, nl)

    parts: list[list[str]] = [[title]] if title is not None else []
    for d in sorted(groups, reverse=True):
        body = _trim_blank(groups[d])
        parts.append([f"## {d}{nl}", nl, *body])
    leftover = _trim_blank(_collapse_blank(undated))
    if leftover:
        parts.append([f"## Undated{nl}", nl, *leftover])
    out: list[str] = []
    for n, part in enumerate(parts):
        if n:
            out.append(nl)
        out.extend(_with_nl(line, nl) for line in part)
    return "".join(out)


def _take_title(lines: list[str], nl: str) -> tuple[str | None, list[str]]:
    first = next((i for i, line in enumerate(lines) if not _is_blank(line)), None)
    if first is not None and _H1_RE.match(lines[first]):
        return _content(lines[first]) + nl, lines[first + 1 :]
    return None, lines


def _collect_groups(lines: list[str], nl: str) -> tuple[dict[str, list[str]], list[str]]:
    groups: dict[str, list[str]] = {}
    undated: list[str] = []
    current: str | None = None
    after_heading = False
    i = 0
    while i < len(lines):
        line = lines[i]
        text_line = _content(line)
        heading = _DATE_HEADING_RE.match(text_line)
        item = DATED_ITEM_RE.match(text_line)
        if heading:
            current = heading.group(1)
            groups.setdefault(current, [])
            after_heading = True
        elif _UNDATED_HEADING_RE.match(text_line):
            current = None
        elif item:
            block = [f"* {item.group(2)}{_ending(line, nl)}"]
            end = _item_end(lines, i)
            block.extend(lines[i + 1 : end])
            i = end - 1
            groups.setdefault(item.group(1), []).extend(block)
        elif current is not None and not _TOP_HEADING_RE.match(text_line):
            if not (_is_blank(line) and after_heading):
                groups[current].append(line)
                after_heading = False
        else:
            current = None
            undated.append(line)
        i += 1
    return groups, undated


def _item_end(lines: list[str], i: int) -> int:
    """Index one past the last child/continuation line of the item at lines[i]."""
    j = i + 1
    while j < len(lines):
        if _is_child(lines[j]):
            j += 1
            continue
        k = j
        while k < len(lines) and _is_blank(lines[k]):
            k += 1
        if k > j and k < len(lines) and _is_child(lines[k]) and lines[k][0] in " \t":
            j = k + 1
            continue
        break
    return j


def _is_child(line: str) -> bool:
    content = _content(line)
    if _is_blank(content):
        return False
    if content[0] in " \t":
        return True
    return not (_ANY_HEADING_RE.match(content) or _LIST_ITEM_RE.match(content))


def _collapse_blank(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if _is_blank(line) and out and _is_blank(out[-1]):
            continue
        out.append(line)
    return out


def add_log_entry(text: str | None, today: date, line: str) -> str:
    stamp = today.isoformat()
    if text is None:
        return f"# Update Log\n\n## {stamp}\n\n{line}\n"
    nl = _nl(text)
    lines = _split_lines(text)
    heads = [(i, m.group(1)) for i, ln in enumerate(lines) if (m := _DATE_HEADING_RE.match(_content(ln)))]
    existing = next((i for i, d in heads if d == stamp), None)
    if existing is not None:
        end = next(
            (i for i in range(existing + 1, len(lines)) if _ANY_HEADING_RE.match(_content(lines[i]))),
            len(lines),
        )
        last = existing
        for i in range(existing + 1, end):
            if not _is_blank(lines[i]):
                last = i
        lines[last] = _with_nl(lines[last], nl)
        new = [nl, f"{line}{nl}"] if last == existing else [f"{line}{nl}"]
        return "".join([*lines[: last + 1], *new, *lines[last + 1 :]])
    pos = next((i for i, d in heads if d < stamp), None)
    if pos is None:
        pos = next((i for i, ln in enumerate(lines) if _UNDATED_HEADING_RE.match(_content(ln))), None)
    group = [f"## {stamp}{nl}", nl, f"{line}{nl}"]
    if pos is None:
        if lines:
            lines[-1] = _with_nl(lines[-1], nl)
            if not _is_blank(lines[-1]):
                lines.append(nl)
        return "".join([*lines, *group])
    return "".join([*lines[:pos], *group, nl, *lines[pos:]])
