"""Frontmatter parsing and additive, byte-preserving edits."""

import io
import re
from dataclasses import dataclass

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import SingleQuotedScalarString

BOM = "﻿"
_OPEN = re.compile(r"---[ \t]*(\r?\n)")
_H1 = re.compile(r"^# +(.+?)\s*$", re.MULTILINE)


@dataclass
class Parsed:
    has_block: bool
    data: dict | None
    error: str | None
    body: str
    newline: str


@dataclass
class _Block:
    bom: str
    opener: str  # "---" line including its newline
    inner: str  # text between delimiters
    closer: str  # closing "---" line including its newline (if any)
    body: str
    newline: str


def _split(text: str) -> _Block | None:
    bom = BOM if text.startswith(BOM) else ""
    rest = text[len(bom) :]
    m = _OPEN.match(rest)
    if not m:
        return None
    nl = m.group(1)
    pos = m.end()
    while pos < len(rest):
        end = rest.find("\n", pos)
        end = len(rest) if end == -1 else end + 1
        line = rest[pos:end]
        if line.rstrip("\r\n").rstrip(" \t") == "---":
            return _Block(bom, rest[: m.end()], rest[m.end() : pos], line, rest[end:], nl)
        pos = end
    return None


def _load(inner: str) -> tuple[dict | None, str | None]:
    try:
        data = YAML(typ="safe").load(inner)
    except Exception as exc:
        return None, str(exc)
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, "frontmatter is not a mapping"
    return data, None


def parse(text: str) -> Parsed:
    block = _split(text)
    if block is None:
        return Parsed(False, None, None, text[len(BOM) :] if text.startswith(BOM) else text, "\n")
    data, error = _load(block.inner)
    return Parsed(True, data, error, block.body, block.newline)


def is_empty_value(v: object) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _round_trips(probe: YAML, source: str, value: str, key: str | None = None) -> bool:
    try:
        loaded = probe.load(source)
        if key is not None:
            loaded = loaded.get(key)
    except Exception:
        return False
    return loaded == value and isinstance(loaded, str)


def _render(key: str, value: str) -> str:
    probe = YAML(typ="safe")
    scalar: str = value
    if not _round_trips(probe, value, value):
        scalar = SingleQuotedScalarString(value)
    yaml = YAML(typ="rt")
    yaml.width = 1_000_000
    buf = io.StringIO()
    yaml.dump({key: scalar}, buf)
    line = buf.getvalue().rstrip("\n")
    if not _round_trips(probe, line, value, key):
        buf = io.StringIO()
        yaml.dump({key: SingleQuotedScalarString(value)}, buf)
        line = buf.getvalue().rstrip("\n")
    return line


def ensure_fields(text: str, fields: dict[str, str]) -> str:
    block = _split(text)
    if block is None:
        if not fields:
            return text
        bom = BOM if text.startswith(BOM) else ""
        nl = "\r\n" if "\r\n" in text else "\n"
        lines = "".join(_render(k, v) + nl for k, v in fields.items())
        return f"{bom}---{nl}{lines}---{nl}{text[len(bom) :]}"

    data, error = _load(block.inner)
    if data is None or error:
        return text
    nl = block.newline
    inner = block.inner
    for key, value in fields.items():
        if key in data:
            if not is_empty_value(data[key]):
                continue
            pattern = re.compile(
                rf"^{re.escape(key)}[ \t]*:[ \t]*(?:\"[ \t]*\"|'[ \t]*'|null|Null|NULL|~)?"
                r"([ \t]+#[^\r\n]*)?[ \t]*(\r?\n|$)",
                re.MULTILINE,
            )
            m = pattern.search(inner)
            if not m:
                raise ValueError(f"cannot fill empty frontmatter key {key!r} in place: not a simple top-level line")
            inner = inner[: m.start()] + _render(key, value) + (m.group(1) or "") + m.group(2) + inner[m.end() :]
            continue
        if inner and not inner.endswith("\n"):
            inner += nl
        inner += _render(key, value) + nl
    if inner == block.inner:
        return text
    return f"{block.bom}{block.opener}{inner}{block.closer}{block.body}"


def strip(text: str) -> str:
    block = _split(text)
    if block is None:
        return text
    body = block.body
    for blank in ("\r\n", "\n"):
        if body.startswith(blank):
            body = body[len(blank) :]
            break
    return block.bom + body


def first_h1(body: str) -> str | None:
    m = _H1.search(body)
    return m.group(1) if m else None
