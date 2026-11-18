"""Second brain validation: the OKF checks plus the brain's own rules."""

import hashlib
import posixpath
import re
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from okfmig import typerules
from okfmig.brain import RAW, WIKI_FOLDERS, BrainSettings, load_settings
from okfmig.frontmatter import parse
from okfmig.gitutil import ignored_under
from okfmig.links import linked_paths
from okfmig.snapshot import SOURCES, LedgerEntry, load_ledger
from okfmig.tasks import ACTIVE, Task, collapse_whitespace, load_store
from okfmig.validate import Report, validate
from okfmig.vault import Note, discover

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_RAW = PurePosixPath(RAW)
_PEOPLE = "people"
_PERSON_SECTIONS = {"personal": "Personal", "performance": "Performance"}
_TASK_ID = re.compile(r"^[0-9a-f]{8}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STATUSES = ("open", "waiting", "done", "dropped")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _in_raw(brain: Path, path: PurePosixPath | None) -> bool:
    return path is not None and path.is_relative_to(_RAW) and path != _RAW and (brain / path).is_file()


def _brain_path(base: PurePosixPath, target: str) -> PurePosixPath | None:
    """`target` resolved against the brain-relative folder `base`; None when it climbs out of the brain."""
    joined = posixpath.normpath(posixpath.join(str(base), target))
    if joined == ".." or joined.startswith("../") or posixpath.isabs(joined):
        return None
    return PurePosixPath(joined)


def _resource_ok(brain: Path, page: PurePosixPath, resource: str) -> bool:
    """A `sources` resource, relative to the page's folder or to the brain root, names an existing raw file."""
    for target in dict.fromkeys((resource, unquote(resource))):
        for base in (page.parent, PurePosixPath(".")):
            if _in_raw(brain, _brain_path(base, target)):
                return True
    return False


def _resources(value: object) -> list[str] | None:
    """Resource strings of a `sources` value (entries are `{resource: ...}` mappings or plain strings)."""
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    found: list[str] = []
    for entry in value:
        resource = entry.get("resource") if isinstance(entry, dict) else entry
        found.append(resource if isinstance(resource, str) else "")
    return found


def _section(body: str, level: int, title: str) -> str:
    """Lines after a heading of `level` named `title` (any case), up to the next heading of that level or higher."""
    lines: list[str] = []
    inside = False
    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading and len(heading.group(1)) <= level:
            inside = len(heading.group(1)) == level and heading.group(2).strip().casefold() == title.casefold()
            continue
        if inside:
            lines.append(line)
    return "\n".join(lines)


def _has_heading(body: str, level: int, title: str) -> bool:
    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading and len(heading.group(1)) == level and heading.group(2).strip().casefold() == title.casefold():
            return True
    return False


def _check_page(brain: Path, note: Note, settings: BrainSettings, report: Report) -> None:
    folder = note.path.parts[0]
    parsed = parse(note.text)
    data = parsed.data if isinstance(parsed.data, dict) else {}
    expected = WIKI_FOLDERS[folder]
    actual = data.get("type")
    if isinstance(actual, str) and actual.strip() and actual != expected:
        report.errors.append(
            f"{note.path}: type {actual!r} does not match its folder {folder}/ (expected {expected!r})"
        )

    resources = _resources(data.get("sources"))
    if resources is None:
        report.errors.append(f"{note.path}: sources must be a list")
    for resource in resources or []:
        if _SCHEME.match(resource):
            continue  # external material, nothing to check on disk
        if not _resource_ok(brain, note.path, resource):
            report.errors.append(f"{note.path}: sources entry {resource} is not an existing file under raw/")

    for target in sorted(linked_paths(_section(parsed.body, 1, "Citations"), note.path)):
        if not _in_raw(brain, target):
            report.errors.append(f"{note.path}: citation {target} is not an existing file under raw/")

    if folder == SOURCES and not any(_in_raw(brain, p) for p in linked_paths(parsed.body, note.path)):
        report.errors.append(f"{note.path}: source summary page links no raw snapshot")

    if folder == _PEOPLE:
        enabled = {"personal": settings.people_personal, "performance": settings.people_performance}
        for key, title in _PERSON_SECTIONS.items():
            if not enabled[key] and _has_heading(parsed.body, 2, title):
                report.warnings.append(f"{note.path}: has a ## {title} section while people.{key} is off")


def _check_ledger(brain: Path, report: Report) -> None:
    for origin, entry in sorted(load_ledger(brain).items()):
        raw = brain / entry.raw_path
        if not raw.is_file():
            report.warnings.append(f"{entry.raw_path}: raw snapshot of {origin} is missing")
        elif _sha256(raw) != entry.origin_sha256:
            report.errors.append(f"{entry.raw_path}: raw snapshot was edited")
        if entry.ingested_sha256 is not None and not (brain / entry.summary_page).is_file():
            report.errors.append(f"{entry.summary_page}: summary page of ingested source {origin} is missing")


def _iso_date(value: str) -> bool:
    if not _ISO_DATE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _brain_file(brain: Path, rel: str) -> bool:
    path = _brain_path(PurePosixPath("."), rel)
    return path is not None and (brain / path).is_file()


def _task_problems(brain: Path, ledger: dict[str, LedgerEntry], tid: str, task: Task) -> list[str]:
    problems = []
    if not _TASK_ID.match(tid):
        problems.append("id is not 8 lowercase hex characters")
    if task.status not in _STATUSES:
        problems.append(f"status {task.status!r} is not one of {', '.join(_STATUSES)}")
    elif task.status in ACTIVE and task.closed is not None:
        problems.append(f"closed is set on an {task.status} task")
    elif task.status not in ACTIVE and task.closed is None:
        problems.append(f"closed is missing on a {task.status} task")
    for name in ("due", "created", "updated", "closed"):
        value = getattr(task, name)
        if value is not None and not _iso_date(value):
            problems.append(f"{name} {value!r} is not an ISO date")
    if task.owner.startswith("people/") and not _brain_file(brain, task.owner):
        problems.append(f"owner {task.owner} is not an existing file")
    if task.page is not None and not _brain_file(brain, task.page):
        problems.append(f"page {task.page} is not an existing file")
    if not task.sources:
        problems.append("has no sources")
    for source in task.sources:
        if source.origin not in ledger:
            problems.append(f"source origin {source.origin} is not in the ledger")
        if not _in_raw(brain, _brain_path(PurePosixPath("."), source.raw_path)):
            problems.append(f"source raw_path {source.raw_path} is not an existing file under raw/")
    return problems


def _quote_gone(brain: Path, task: Task) -> bool:
    """True when snapshots exist for the sources and none of them still holds a quote."""
    snapshots = []
    for source in task.sources:
        if _in_raw(brain, _brain_path(PurePosixPath("."), source.raw_path)):
            text = collapse_whitespace((brain / source.raw_path).read_text(encoding="utf-8", errors="replace"))
            snapshots.append(collapse_whitespace(source.quote) in text)
    return bool(snapshots) and not any(snapshots)


def _check_tasks(brain: Path, report: Report) -> None:
    ledger = load_ledger(brain)
    for tid, task in sorted(load_store(brain).tasks.items()):
        report.errors.extend(f"tasks.json: {tid}: {p}" for p in _task_problems(brain, ledger, tid, task))
        if task.status in ACTIVE and _quote_gone(brain, task):
            report.warnings.append(f"tasks.json: {tid}: no source quote appears in its snapshot any more")


def validate_brain(brain: Path) -> Report:
    """`validate` on the brain plus the brain rules; ValueError on unreadable settings, type rules or ledger."""
    cfg = typerules.load(brain / ".okf" / "types.yaml")
    settings = load_settings(brain)
    ignored = ignored_under(brain)
    report = validate(brain, cfg, ignored=ignored)
    # raw/ is outside the bundle by design; one warning per snapshot would only bury the real warnings
    report.warnings = [w for w in report.warnings if not w.startswith(f"outside bundle: {RAW}/")]
    for note in discover(brain, cfg.exclude, ignored).notes:
        if len(note.path.parts) > 1 and note.path.parts[0] in WIKI_FOLDERS:
            _check_page(brain, note, settings, report)
    _check_ledger(brain, report)
    _check_tasks(brain, report)
    return report
