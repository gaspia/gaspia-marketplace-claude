"""Source snapshots into `raw/`, the source ledger (`.okf/sources.json`) and pending batches."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from okfmig.brain import RAW, WORKDIR, BrainSettings
from okfmig.frontmatter import parse
from okfmig.gitutil import ignored_under
from okfmig.links import Resolver, embed_targets
from okfmig.planner import kebab
from okfmig.vault import Vault, discover

LEDGER = PurePosixPath(".okf") / "sources.json"
BATCHES = PurePosixPath(WORKDIR) / "batches.json"
PREVIOUS = PurePosixPath(WORKDIR) / "previous"
SOURCES = "sources"

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LEADING_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})[\s._-]*")
_COMPACT_DATE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
_BATCH_ID = re.compile(r"^b(\d+)$")


@dataclass
class LedgerEntry:
    raw_path: str
    origin_sha256: str
    ingested_sha256: str | None
    summary_page: str
    first_seen: str
    last_snapshot: str


@dataclass
class BatchItem:
    origin: str
    raw_path: str
    status: Literal["new", "changed"]
    previous: str | None
    summary_page: str


@dataclass
class Batch:
    id: str
    items: list[BatchItem]


@dataclass
class SnapshotResult:
    name: str
    new: list[str]
    changed: list[str]
    unchanged: list[str]
    missing: list[str]
    attachments: int
    batches: list[str]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    path.write_bytes(text.encode("utf-8"))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: cannot read: {exc}") from exc


def load_ledger(brain: Path) -> dict[str, LedgerEntry]:
    path = brain / LEDGER
    data = _read_json(path, {})
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping of origins")
    try:
        return {origin: LedgerEntry(**entry) for origin, entry in data.items()}
    except TypeError as exc:
        raise ValueError(f"{path}: invalid entry: {exc}") from exc


def save_ledger(brain: Path, ledger: dict[str, LedgerEntry]) -> None:
    _write_json(brain / LEDGER, {origin: asdict(entry) for origin, entry in ledger.items()})


def load_batches(brain: Path) -> list[Batch]:
    path = brain / BATCHES
    data = _read_json(path, [])
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of batches")
    try:
        return [Batch(b["id"], [BatchItem(**item) for item in b["items"]]) for b in data]
    except (TypeError, KeyError) as exc:
        raise ValueError(f"{path}: invalid batch: {exc}") from exc


def save_batches(brain: Path, batches: list[Batch]) -> None:
    _write_json(brain / BATCHES, [asdict(b) for b in batches])


def source_root(source: Path) -> Path:
    """Nearest ancestor-or-self folder holding `.obsidian/`; else the source folder (or the file's folder)."""
    start = source if source.is_dir() else source.parent
    for folder in (start, *start.parents):
        if (folder / ".obsidian").is_dir():
            return folder
    return start


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _within(path: PurePosixPath, folder: PurePosixPath) -> bool:
    return path.is_relative_to(folder)


def _in_a_brain(rel: PurePosixPath, root: Path) -> bool:
    """True when `rel` lies in a brain (a folder holding `.okf/brain.yaml`): a file-system check, so it holds
    for any brain and whatever spelling the brain path was given in."""
    return any((root / p / ".okf" / "brain.yaml").is_file() for p in rel.parents)


def _covered(rel: PurePosixPath, ignored: set[PurePosixPath]) -> bool:
    """True when `rel` or one of its ancestors is in `ignored`."""
    return any(p in ignored for p in (rel, *rel.parents))


def _relative(path: Path, root: Path) -> PurePosixPath | None:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        return None
    return PurePosixPath(resolved.relative_to(root).as_posix())


def _sources(vault: Vault, root: Path, source: Path, brain_rel: PurePosixPath | None) -> list[PurePosixPath]:
    scope = _relative(source, root) or PurePosixPath(".")
    candidates = [n.path for n in (*vault.notes, *vault.reserved)] + list(vault.undecodable)
    return sorted(
        p
        for p in candidates
        if _within(p, scope) and not (brain_rel is not None and _within(p, brain_rel)) and not _in_a_brain(p, root)
    )


def _single_source(
    root: Path, source: Path, brain_rel: PurePosixPath | None, ignored: set[PurePosixPath]
) -> list[PurePosixPath]:
    """The single-note source as a vault path; ValueError when it may not be a source."""
    rel = _relative(source, root)
    if rel is None:
        raise ValueError(f"{source}: outside its vault {root}")
    if brain_rel is not None and _within(rel, brain_rel):
        raise ValueError(f"{source}: inside the brain; brain pages are never sources")
    if _in_a_brain(rel, root):
        raise ValueError(f"{source}: inside a brain; brain pages and snapshots are never sources")
    if _covered(rel, ignored):
        raise ValueError(f"{source}: git-ignored; ignored notes are never sources")
    return [rel]


def _compact_date(stem: str) -> re.Match[str] | None:
    """The first `YYYYMMDD` in `stem` (not part of a longer number) that is a real date."""
    for match in _COMPACT_DATE.finditer(stem):
        try:
            date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        return match
    return None


def _summary_date(rel: PurePosixPath, data: bytes, today: date) -> tuple[str, str]:
    """The summary date and the stem left for the slug: frontmatter `date` or `created`, then a leading
    `YYYY-MM-DD`, then a `YYYYMMDD` anywhere in the stem, then today. A date taken from the stem leaves it."""
    stem = rel.stem
    leading = _LEADING_DATE.match(stem)
    rest = _LEADING_DATE.sub("", stem, count=1)
    try:
        meta = parse(data.decode("utf-8")).data or {}
    except UnicodeDecodeError:
        meta = {}
    for key in ("date", "created"):
        value = meta.get(key)
        if value is not None:
            match = _DATE.search(str(value))
            if match:
                return match.group(0), rest
    if leading:
        return leading.group(1), rest
    compact = _compact_date(stem)
    if compact:
        day = f"{compact.group(1)}-{compact.group(2)}-{compact.group(3)}"
        return day, stem[: compact.start()] + " " + stem[compact.end() :]
    return today.isoformat(), stem


def _summary_page(rel: PurePosixPath, data: bytes, today: date, taken: set[str], brain: Path) -> str:
    day, stem = _summary_date(rel, data, today)
    slug = kebab(stem) or "note"
    base = f"{SOURCES}/{day}-{slug}"
    page, n = f"{base}.md", 1
    while page in taken or (brain / page).exists():
        n += 1
        page = f"{base}-{n}.md"
    taken.add(page)
    return page


def _copy_if_different(data: bytes, dest: Path) -> bool:
    if dest.is_file() and dest.read_bytes() == data:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def _attachments(
    vault: Vault,
    root: Path,
    notes: list[PurePosixPath],
    brain_rel: PurePosixPath | None,
    ignored: set[PurePosixPath],
) -> set[PurePosixPath]:
    """Non-note files embedded by `notes`, excluding git-ignored files and anything under a brain."""
    resolver = Resolver(vault.files, vault.settings.new_file_folder)
    found: set[PurePosixPath] = set()
    for rel in notes:
        try:
            text = (root / rel).read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        for kind, target in embed_targets(text):
            if kind == "wiki":
                path = resolver.resolve(target, rel)[0]
            else:
                path = _relative(root / rel.parent / target, root)
            if (
                path is None
                or path.suffix.lower() == ".md"
                or _covered(path, ignored)
                or (brain_rel is not None and _within(path, brain_rel))
                or _in_a_brain(path, root)
                or not (root / path).is_file()
            ):
                continue
            found.add(path)
    return found


def _next_id(batches: list[Batch]) -> int:
    numbers = [int(m.group(1)) for b in batches if (m := _BATCH_ID.match(b.id))]
    return max(numbers, default=0) + 1


def _validate(source: Path, name: str | None) -> tuple[Path, str]:
    """The source root and the source name; ValueError on a bad source or name."""
    if not source.exists():
        raise ValueError(f"{source}: no such file or folder")
    if source.is_file() and source.suffix.lower() != ".md":
        raise ValueError(f"{source}: a single-note source must be a .md file")
    root = source_root(source).resolve()
    name = kebab(root.name) if name is None else name
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"invalid source name {name!r}")
    return root, name


def _file_sha256(path: Path) -> str | None:
    return _sha256(path.read_bytes()) if path.is_file() else None


def _keep_previous(brain: Path, entry: LedgerEntry) -> None:
    """Before the snapshot is overwritten: copy it to the workdir when it is the ingested version."""
    if entry.ingested_sha256 is None or _file_sha256(brain / entry.raw_path) != entry.ingested_sha256:
        return
    kept = brain / PREVIOUS / entry.raw_path
    if _file_sha256(kept) != entry.ingested_sha256:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes((brain / entry.raw_path).read_bytes())


def _previous(brain: Path, entry: LedgerEntry) -> str | None:
    """Brain path of the kept copy of the ingested version, or None when no valid copy exists."""
    previous = f"{PREVIOUS}/{entry.raw_path}"
    if entry.ingested_sha256 is None or _file_sha256(brain / previous) != entry.ingested_sha256:
        return None
    return previous


def _missing(ledger: dict[str, LedgerEntry], name: str, root: Path, source: Path) -> list[str]:
    """Ledger origins of this source name, within the source path, whose file no longer exists."""
    scope = _relative(source, root) or PurePosixPath(".")
    prefix = f"{name}:"
    missing: list[str] = []
    for origin in sorted(ledger):
        if not origin.startswith(prefix):
            continue
        rel = PurePosixPath(origin[len(prefix) :])
        in_scope = rel == scope if source.is_file() else _within(rel, scope)
        if in_scope and not (root / rel).exists():
            missing.append(origin)
    return missing


def _append_batches(batches: list[Batch], queued: list[BatchItem], size: int) -> list[str]:
    number = _next_id(batches)
    ids: list[str] = []
    for start in range(0, len(queued), size):
        batch = Batch(f"b{number + len(ids):03}", queued[start : start + size])
        batches.append(batch)
        ids.append(batch.id)
    return ids


def take_snapshot(brain: Path, source: Path, name: str | None, settings: BrainSettings, today: date) -> SnapshotResult:
    """Copy sources into `raw/<name>/`, update the ledger and queue the ones not yet ingested in batches.

    The snapshot follows the origin (`origin_sha256`); queueing follows the ledger's `ingested_sha256`, so a
    lost workdir never loses work: anything not ingested and not pending is queued again.
    """
    root, name = _validate(source, name)
    brain = brain.resolve()
    brain_rel = _relative(brain, root)
    ignored = set(ignored_under(root))
    vault = discover(root, (), ignored)
    if source.is_file():
        sources = _single_source(root, source, brain_rel, ignored)
    else:
        sources = _sources(vault, root, source, brain_rel)

    ledger = load_ledger(brain)
    batches = load_batches(brain)
    pending = {item.origin for b in batches for item in b.items}
    taken = {e.summary_page for e in ledger.values()}
    today_iso = today.isoformat()
    result = SnapshotResult(name, [], [], [], [], 0, [])
    queued: list[BatchItem] = []

    for rel in sources:
        origin = f"{name}:{rel}"
        data = (root / rel).read_bytes()
        digest = _sha256(data)
        entry = ledger.get(origin)
        if entry is None:
            page = _summary_page(rel, data, today, taken, brain)
            entry = ledger[origin] = LedgerEntry(f"{RAW}/{name}/{rel}", digest, None, page, today_iso, today_iso)
            _copy_if_different(data, brain / entry.raw_path)
        elif digest != entry.origin_sha256:
            _keep_previous(brain, entry)
            _copy_if_different(data, brain / entry.raw_path)
            entry.origin_sha256, entry.last_snapshot = digest, today_iso
        if digest == entry.ingested_sha256 or origin in pending:
            result.unchanged.append(origin)
        elif entry.ingested_sha256 is None:
            queued.append(BatchItem(origin, entry.raw_path, "new", None, entry.summary_page))
            result.new.append(origin)
        else:
            queued.append(BatchItem(origin, entry.raw_path, "changed", _previous(brain, entry), entry.summary_page))
            result.changed.append(origin)

    for path in sorted(_attachments(vault, root, sources, brain_rel, ignored)):
        if _copy_if_different((root / path).read_bytes(), brain / RAW / name / path):
            result.attachments += 1

    result.missing = _missing(ledger, name, root, source)
    result.batches = _append_batches(batches, queued, settings.effective_batch_size(single_note=source.is_file()))
    save_ledger(brain, ledger)
    save_batches(brain, batches)
    return result
