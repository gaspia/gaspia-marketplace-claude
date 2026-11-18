"""Second brain: settings, layout and init."""

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from okfmig.gitutil import ensure_info_exclude, repo_location

WIKI_FOLDERS: dict[str, str] = {
    "concepts": "concept",
    "projects": "project",
    "areas": "area",
    "people": "person",
    "decisions": "decision",
    "sources": "source",
}
RAW = "raw"
WORKDIR = ".okf-ingest"

SCHEMA_TEMPLATE = Path(__file__).parent / "templates" / "schema.md"
MARKER_START = "<!-- okf-brain:schema -->"
MARKER_END = "<!-- /okf-brain:schema -->"
_BLOCK = re.compile(re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END) + r"(?:\r?\n)?", re.DOTALL)

DEFAULT_BRAIN_YAML = """\
review:
  mode: batch        # source | batch | autonomous
  batch_size: 10     # used when mode is batch
people:
  personal: true     # family, interests, personal context
  performance: true  # feedback, self-assessments, growth areas
tasks:
  stale_days: 14     # no state change for this long shows in today's stale bucket
  default_owner: me  # owner when a source states a commitment with no actor
  max_age_days: 30   # sources older than this at ingest yield no tasks
"""

_MODES = ("source", "batch", "autonomous")


@dataclass(frozen=True)
class BrainSettings:
    review_mode: Literal["source", "batch", "autonomous"] = "batch"
    batch_size: int = 10
    people_personal: bool = True
    people_performance: bool = True
    tasks_stale_days: int = 14
    tasks_default_owner: str = "me"
    tasks_max_age_days: int = 30

    def effective_batch_size(self, single_note: bool) -> int:
        if single_note or self.review_mode == "source":
            return 1
        return self.batch_size


def _section(raw: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: {key}: expected a mapping")
    return value


def load_settings(brain: Path) -> BrainSettings:
    path = brain / ".okf" / "brain.yaml"
    if not path.exists():
        return BrainSettings()
    try:
        raw = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ValueError(f"{path}: cannot read: {exc}") from exc
    except YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    defaults = BrainSettings()
    review = _section(raw, "review", path)
    people = _section(raw, "people", path)
    tasks = _section(raw, "tasks", path)
    mode = review.get("mode", defaults.review_mode)
    if mode not in _MODES:
        raise ValueError(f"{path}: review.mode {mode!r} must be one of {', '.join(_MODES)}")
    size = review.get("batch_size", defaults.batch_size)
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError(f"{path}: review.batch_size {size!r} must be a positive integer")
    flags: dict[str, bool] = {}
    for key in ("personal", "performance"):
        value = people.get(key, True)
        if not isinstance(value, bool):
            raise ValueError(f"{path}: people.{key} {value!r} must be true or false")
        flags[key] = value
    days: dict[str, int] = {}
    for key, default in (("stale_days", defaults.tasks_stale_days), ("max_age_days", defaults.tasks_max_age_days)):
        value = tasks.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{path}: tasks.{key} {value!r} must be a positive integer")
        days[key] = value
    owner = tasks.get("default_owner", defaults.tasks_default_owner)
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError(f"{path}: tasks.default_owner {owner!r} must be a non-empty string")
    return BrainSettings(
        mode, size, flags["personal"], flags["performance"], days["stale_days"], owner, days["max_age_days"]
    )


def outer_vault(brain: Path) -> Path | None:
    """Nearest ancestor of the brain (not the brain itself) that holds an `.obsidian/` folder."""
    for parent in brain.resolve().parents:
        if (parent / ".obsidian").is_dir():
            return parent
    return None


def _write_types(path: Path) -> bool:
    """Merge the wiki folder rules and the raw exclude into `.okf/types.yaml`; True when the file changed."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    if path.exists():
        data = yaml.load(path.read_text(encoding="utf-8"))
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a mapping at top level")
    else:
        data = {}
    changed = False
    if data.get("rules") is None:
        data["rules"] = []
        changed = True
    rules = data["rules"]
    if not isinstance(rules, list):
        raise ValueError(f"{path}: rules: expected a list")
    present = {r["folder"] for r in rules if isinstance(r, dict) and "folder" in r}
    for folder, type_ in WIKI_FOLDERS.items():
        pattern = f"{folder}/**"
        if pattern not in present:
            rules.append({"folder": pattern, "type": type_})
            changed = True
    if data.get("exclude") is None:
        data["exclude"] = []
        changed = True
    exclude = data["exclude"]
    if not isinstance(exclude, list):
        raise ValueError(f"{path}: exclude: expected a list")
    if f"{RAW}/**" not in exclude:
        exclude.append(f"{RAW}/**")
        changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fh:
            yaml.dump(data, fh)
    return changed


def _write_schema(path: Path) -> bool:
    """Create or replace the schema block in CLAUDE.md; True when the file changed."""
    body = SCHEMA_TEMPLATE.read_text(encoding="utf-8").strip("\n")
    old = path.read_bytes().decode("utf-8") if path.exists() else ""
    eol = "\r\n" if "\r\n" in old else "\n"
    block = f"{MARKER_START}{eol}{body.replace(chr(10), eol)}{eol}{MARKER_END}{eol}"
    if _BLOCK.search(old):
        new = _BLOCK.sub(lambda _m: block, old, count=1)
    elif old:
        sep = "" if old.endswith(eol) else eol
        new = f"{old}{sep}{eol}{block}"
    else:
        new = block
    if new == old:
        return False
    path.write_bytes(new.encode("utf-8"))
    return True


def init_brain(brain: Path) -> list[str]:
    """Create or update the brain layout; return the created or changed paths (relative to the brain)."""
    changed: list[str] = []
    brain.mkdir(parents=True, exist_ok=True)
    for name in (*WIKI_FOLDERS, RAW):
        folder = brain / name
        if not folder.is_dir():
            folder.mkdir(parents=True)
            changed.append(name)
    settings = brain / ".okf" / "brain.yaml"
    if not settings.exists():
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(DEFAULT_BRAIN_YAML, encoding="utf-8")
        changed.append(".okf/brain.yaml")
    if _write_types(brain / ".okf" / "types.yaml"):
        changed.append(".okf/types.yaml")
    if _write_schema(brain / "CLAUDE.md"):
        changed.append("CLAUDE.md")
    location = repo_location(brain)
    if location is not None:
        top, prefix = location
        entry = f"{WORKDIR}/" if prefix == PurePosixPath(".") else f"{prefix}/{WORKDIR}/"
        ensure_info_exclude(top, entry)
    return changed
