"""Ingest helpers: duplicate candidates for new pages and batch completion."""

import re
import shutil
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

from okfmig import typerules
from okfmig.apply import BlockedPlanError, StaleFileError, apply_plan
from okfmig.brain import WIKI_FOLDERS, load_settings
from okfmig.frontmatter import parse
from okfmig.gitutil import commit_times_under, has_changes, ignored_under, repo_location
from okfmig.links import linked_paths, relative_href
from okfmig.planner import build_plan
from okfmig.reserved import add_log_entry, has_log_entry
from okfmig.snapshot import PREVIOUS, Batch, LedgerEntry, load_batches, load_ledger, save_batches, save_ledger
from okfmig.tasks import (
    BATCH_TASKS,
    SIMILAR_RATIO,
    BatchTask,
    TaskError,
    check_batch_tasks,
    load_batch_tasks,
    load_store,
    merge_batch_tasks,
    save_store,
)
from okfmig.vault import discover

MIN_SUBSTRING = 3
LOG = "log.md"
SECTION_LEVEL = 2  # `## Pages updated` and the headings that end it (level 1 or 2)
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_SEPARATORS = re.compile(r"[\s_-]+")


class BatchError(Exception):
    """A batch cannot be completed."""


# -- similar pages -------------------------------------------------------------


def _normalize(text: str) -> str:
    return _SEPARATORS.sub(" ", text.casefold()).strip()


def _read_meta(path: Path) -> dict:
    try:
        data = parse(path.read_bytes().decode("utf-8")).data
    except (OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _title(meta: dict, stem: str) -> str:
    title = meta.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else stem


def _candidates(meta: dict, stem: str) -> list[str]:
    names = [stem]
    title = meta.get("title")
    if isinstance(title, str):
        names.append(title)
    aliases = meta.get("aliases")
    if isinstance(aliases, str):
        aliases = [aliases]
    if isinstance(aliases, list):
        names.extend(a for a in aliases if isinstance(a, str))
    return [n for n in (_normalize(name) for name in names) if n]


def _score(query: str, candidate: str) -> float:
    ratio = SequenceMatcher(None, query, candidate).ratio()
    shorter, longer = sorted((query, candidate), key=len)
    if len(shorter) >= MIN_SUBSTRING and shorter in longer:
        return 1.0
    return ratio


def similar_pages(brain: Path, title: str) -> list[tuple[str, str, float]]:
    """Wiki pages whose file stem, `title` or `aliases` are close to `title`: `(path, title, score)`, best first."""
    query = _normalize(title)
    if not query:
        return []
    hits: list[tuple[str, str, float]] = []
    for folder in WIKI_FOLDERS:
        for path in sorted((brain / folder).rglob("*.md")):
            if path.name == "index.md" or not path.is_file():
                continue
            meta = _read_meta(path)
            score = max((_score(query, c) for c in _candidates(meta, path.stem)), default=0.0)
            if score >= SIMILAR_RATIO:
                hits.append((path.relative_to(brain).as_posix(), _title(meta, path.stem), round(score, 3)))
    return sorted(hits, key=lambda h: (-h[2], h[0]))


# -- batch completion ----------------------------------------------------------


def pages_updated(summary_text: str, summary_path: PurePosixPath) -> int:
    """Distinct local link targets under the `## Pages updated` heading, up to the next heading of level 1 or 2."""
    section: list[str] = []
    inside = False
    for line in parse(summary_text).body.splitlines():
        heading = _HEADING.match(line)
        if heading and len(heading.group(1)) <= SECTION_LEVEL:
            inside = len(heading.group(1)) == SECTION_LEVEL and heading.group(2).strip().casefold() == "pages updated"
            continue
        if inside:
            section.append(line)
    return len(linked_paths("\n".join(section), summary_path))


def _escape_title(title: str) -> str:
    return re.sub(r"([\\\[\]])", r"\\\1", title)


def _log_line(brain: Path, page: str, actions: int) -> str:
    text = (brain / page).read_bytes().decode("utf-8")
    path = PurePosixPath(page)
    title = _title(_read_meta(brain / page), path.stem)
    href = relative_href(PurePosixPath(LOG), path)
    count = pages_updated(text, path)
    line = f"* **Ingest**: [{_escape_title(title)}]({href}) - {count} pages updated"
    if actions:
        line += f", {actions} action{'' if actions == 1 else 's'}"
    return line


def _remove_previous(brain: Path, previous: str) -> None:
    """Delete a kept previous snapshot and the folders it leaves empty under the workdir's `previous/`."""
    path = brain / previous
    path.unlink(missing_ok=True)
    stop = brain / PREVIOUS
    folder = path.parent
    while folder != stop and folder.is_relative_to(stop) and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def _check(brain: Path, batch: Batch, ledger: dict[str, LedgerEntry]) -> None:
    """Refuse a batch whose summary pages are missing or untouched, or whose origins left the ledger."""
    missing = [item.summary_page for item in batch.items if not (brain / item.summary_page).is_file()]
    if missing:
        raise BatchError(f"batch {batch.id}: missing summary pages: {', '.join(missing)}")
    if repo_location(brain) is not None:  # snapshot needs a clean brain: an untouched summary was not processed
        stale = [item.summary_page for item in batch.items if not has_changes(brain, item.summary_page)]
        if stale:
            raise BatchError(
                f"batch {batch.id}: summary pages not updated since the snapshot (process each source and record "
                f"what changed in its summary): {', '.join(stale)}"
            )
    unknown = [item.origin for item in batch.items if item.origin not in ledger]
    if unknown:
        raise BatchError(f"batch {batch.id}: origins missing from the ledger: {', '.join(unknown)}")


def _append_log(brain: Path, today: date, lines: list[str]) -> None:
    """Append the lines under today's heading, skipping any already there (a re-run after a crash)."""
    log = brain / LOG
    old = log.read_bytes().decode("utf-8") if log.exists() else None
    text = old
    for line in lines:
        if not has_log_entry(text, today, line):
            text = add_log_entry(text, today, line)
    if text is not None and text != old:
        log.write_bytes(text.encode("utf-8"))


def _batch_entries(brain: Path, batch: Batch) -> list[BatchTask]:
    try:
        return load_batch_tasks(brain, batch.id)
    except TaskError as exc:
        raise BatchError(f"batch {batch.id}: {exc}") from exc


def complete_batch(brain: Path, batch_id: str, today: date) -> list[str]:
    """Mark a batch ingested: normalize pages and indexes, update the ledger, log one line per source.

    Every check runs before the first write, so a refused batch leaves the brain untouched. Each write is
    idempotent and the batch is dequeued last, so a re-run after a crash completes it without duplicate log lines.
    """
    batches = load_batches(brain)
    batch = next((b for b in batches if b.id == batch_id), None)
    if batch is None:
        raise BatchError(f"unknown or already completed batch {batch_id!r}")
    ledger = load_ledger(brain)
    _check(brain, batch, ledger)
    entries = _batch_entries(brain, batch)
    if entries:  # no actions file: leave `.okf/tasks.json` alone
        settings = load_settings(brain)
        store = load_store(brain)
        try:
            check_batch_tasks(brain, batch, entries, store, settings, today)
        except TaskError as exc:
            raise BatchError(f"batch {batch_id}: {exc}") from exc

    cfg = typerules.load(brain / ".okf" / "types.yaml")
    vault = discover(brain, cfg.exclude, ignored_under(brain))
    plan = build_plan(vault, cfg, commit_times_under(brain), today, root_log=False)
    if plan.blocking:
        items = "\n".join(f"  {b}" for b in plan.blocking)
        raise BatchError(f"batch {batch_id}: the brain has blocking items:\n{items}")
    try:
        apply_plan(brain, plan)
    except (BlockedPlanError, StaleFileError) as exc:
        raise BatchError(f"batch {batch_id}: {exc}") from exc
    counts: dict[str, int] = {}
    if entries:
        counts = merge_batch_tasks(store, batch, entries, settings, today)
        save_store(brain, store)
    lines = [_log_line(brain, item.summary_page, counts.get(item.origin, 0)) for item in batch.items]  # after apply

    for item in batch.items:
        entry = ledger[item.origin]
        entry.ingested_sha256 = entry.origin_sha256
    save_ledger(brain, ledger)
    _append_log(brain, today, lines)
    save_batches(brain, [b for b in batches if b.id != batch_id])
    for item in batch.items:
        if item.previous is not None:
            _remove_previous(brain, item.previous)
    shutil.rmtree(brain / BATCH_TASKS / batch_id, ignore_errors=True)
    return lines
