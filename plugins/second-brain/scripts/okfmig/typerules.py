"""Type rules: load types.yaml and resolve a note's OKF type."""

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from okfmig.frontmatter import is_empty_value

_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_KINDS = ("folder", "tag", "filename")
_RESERVED = ("index", "log", "note")


@dataclass
class Rule:
    kind: Literal["folder", "tag", "field", "filename"]
    pattern: str
    type: str
    field_value: str | None = None


@dataclass
class TypeConfig:
    default: str | None = None
    exclude: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    overrides: dict[str, str] = field(default_factory=dict)
    reserved: dict[str, Literal["index", "log", "note"]] = field(default_factory=dict)


def is_kebab(s: str) -> bool:
    return bool(_KEBAB.match(s))


def _check_type(value: Any, where: str) -> str:
    if not isinstance(value, str) or not is_kebab(value):
        raise ValueError(f"{where}: type {value!r} must be lowercase kebab-case")
    return value


def _parse_rule(raw: Any, n: int) -> Rule:
    where = f"rule {n}"
    if not isinstance(raw, dict) or "type" not in raw:
        raise ValueError(f"{where}: expected a mapping with a 'type' key, got {raw!r}")
    type_ = _check_type(raw["type"], where)
    kinds = [k for k in (*_KINDS, "field") if k in raw]
    if len(kinds) != 1 or set(raw) - {kinds[0], "type"}:
        raise ValueError(f"{where}: unknown rule shape {dict(raw)!r}")
    kind = kinds[0]
    if kind == "field":
        spec = raw["field"]
        if not isinstance(spec, dict) or "name" not in spec or "value" not in spec:
            raise ValueError(f"{where}: field needs name and value, got {spec!r}")
        return Rule("field", str(spec["name"]), type_, str(spec["value"]))
    if not isinstance(raw[kind], str):
        raise ValueError(f"{where}: {kind} pattern must be a string, got {raw[kind]!r}")
    if kind == "filename":
        try:
            re.compile(raw[kind])
        except re.error as e:
            raise ValueError(f"{where}: invalid filename regex {raw[kind]!r}: {e}") from e
    return Rule(kind, raw[kind], type_)  # type: ignore[arg-type]


def load(path: Path) -> TypeConfig:
    if not path.exists():
        return TypeConfig()
    try:
        raw = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ValueError(f"{path}: cannot read: {exc}") from exc
    except YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    cfg = TypeConfig()
    if raw.get("default") is not None:
        cfg.default = _check_type(raw["default"], "default")
    exclude = raw.get("exclude") or []
    if not isinstance(exclude, list) or not all(isinstance(e, str) for e in exclude):
        raise ValueError("exclude: expected a list of glob strings")
    cfg.exclude = list(exclude)
    rules = raw.get("rules") or []
    if not isinstance(rules, list):
        raise ValueError("rules: expected a list")
    cfg.rules = [_parse_rule(r, i) for i, r in enumerate(rules, 1)]
    overrides = raw.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError("overrides: expected a mapping")
    cfg.overrides = {str(k): _check_type(v, f"override {k!r}") for k, v in overrides.items()}
    reserved = raw.get("reserved") or {}
    if not isinstance(reserved, dict):
        raise ValueError("reserved: expected a mapping")
    for k, v in reserved.items():
        if v not in _RESERVED:
            raise ValueError(f"reserved {k!r}: {v!r} must be one of index, log, note")
    cfg.reserved = {str(k): v for k, v in reserved.items()}
    return cfg


def _norm_tag(t: Any) -> str:
    return str(t).strip().lstrip("#").strip().casefold()


def _note_tags(data: dict, body_tags: set[str]) -> set[str]:
    raw = data.get("tags")
    items: list[Any]
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, str):
        items = re.split(r"[,\s]+", raw)
    else:
        items = []
    return {n for n in (_norm_tag(t) for t in [*items, *body_tags]) if n}


def _matches(rule: Rule, path: PurePosixPath, data: dict, tags: set[str]) -> bool:
    if rule.kind == "folder":
        return path.full_match(rule.pattern)
    if rule.kind == "tag":
        return _norm_tag(rule.pattern) in tags
    if rule.kind == "field":
        return str(data.get(rule.pattern)) == rule.field_value
    return re.search(rule.pattern, path.name) is not None


def resolve_type(cfg: TypeConfig, path: PurePosixPath, data: dict, body_tags: set[str]) -> tuple[str | None, str]:
    existing = data.get("type")
    if not is_empty_value(existing):
        return str(existing), "existing"
    if str(path) in cfg.overrides:
        return cfg.overrides[str(path)], "override"
    tags = _note_tags(data, body_tags)
    for n, rule in enumerate(cfg.rules, 1):
        if _matches(rule, path, data, tags):
            return rule.type, f"rule {n}: {rule.kind} {rule.pattern}"
    if cfg.default is not None:
        return cfg.default, "default"
    return None, "unclassified"
