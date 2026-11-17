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


def _in_brain_raw(rel: PurePosixPath, root: Path) -> bool:
    """True when `rel` lies under the `raw/` folder of a brain (a folder holding `.okf/brain.yaml`)."""
    return any(p.name == RAW and (root / p.parent / ".okf" / "brain.yaml").is_file() for p in rel.parents)


def _relative(path: Path, root: Path) -> PurePosixPath | None:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        return None
    return PurePosixPath(resolved.relative_to(root).as_posix())


def _sources(vault: Vault, root: Path, source: Path, brain_rel: PurePosixPath | None) -> list[PurePosixPath]:
    if source.is_file():
        rel = _relative(source, root)
        return [] if rel is None else [rel]
    scope = _relative(source, root) or PurePosixPath(".")
    candidates = [n.path for n in (*vault.notes, *vault.reserved)] + list(vault.undecodable)
    return sorted(
        p
        for p in candidates
        if _within(p, scope) and not (brain_rel is not None and _within(p, brain_rel)) and not _in_brain_raw(p, root)
    )


def _summary_date(rel: PurePosixPath, data: bytes, today: date) -> str:
    try:
        meta = parse(data.decode("utf-8")).data or {}
    except UnicodeDecodeError:
        meta = {}
    for key in ("date", "created"):
        value = meta.get(key)
        if value is not None:
            match = _DATE.search(str(value))
            if match:
                return match.group(0)
    match = _LEADING_DATE.match(rel.stem)
    return match.group(1) if match else today.isoformat()


def _summary_page(rel: PurePosixPath, data: bytes, today: date, taken: set[str], brain: Path) -> str:
    day = _summary_date(rel, data, today)
    slug = kebab(_LEADING_DATE.sub("", rel.stem, count=1)) or "note"
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


def _attachments(vault: Vault, root: Path, notes: list[PurePosixPath]) -> set[PurePosixPath]:
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
            if path is not None and path.suffix.lower() != ".md" and (root / path).is_file():
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


def _keep_previous(brain: Path, raw_path: str) -> str:
    """Copy the current snapshot to the workdir unless a previous copy is already pending; its brain path."""
    previous = f"{PREVIOUS}/{raw_path}"
    snapshot, kept = brain / raw_path, brain / previous
    if snapshot.is_file() and not kept.exists():
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(snapshot.read_bytes())
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
    """Copy new and changed sources into `raw/<name>/`, update the ledger and queue them in batches."""
    root, name = _validate(source, name)
    brain = brain.resolve()
    vault = discover(root, (), ignored_under(root))
    sources = _sources(vault, root, source, _relative(brain, root))

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
        if entry is not None and digest == entry.origin_sha256:
            result.unchanged.append(origin)
            continue
        if entry is None:
            page = _summary_page(rel, data, today, taken, brain)
            entry = ledger[origin] = LedgerEntry(f"{RAW}/{name}/{rel}", digest, None, page, today_iso, today_iso)
            item = BatchItem(origin, entry.raw_path, "new", None, page)
            result.new.append(origin)
        else:
            previous = _keep_previous(brain, entry.raw_path)
            entry.origin_sha256, entry.last_snapshot = digest, today_iso
            item = BatchItem(origin, entry.raw_path, "changed", previous, entry.summary_page)
            result.changed.append(origin)
        _copy_if_different(data, brain / entry.raw_path)
        if origin not in pending:
            queued.append(item)

    for path in sorted(_attachments(vault, root, sources)):
        if _copy_if_different((root / path).read_bytes(), brain / RAW / name / path):
            result.attachments += 1

    result.missing = _missing(ledger, name, root, source)
    result.batches = _append_batches(batches, queued, settings.effective_batch_size(single_note=source.is_file()))
    save_ledger(brain, ledger)
    save_batches(brain, batches)
    return result
