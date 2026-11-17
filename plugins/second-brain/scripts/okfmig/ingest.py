"""Ingest helpers: duplicate candidates for new pages and batch completion."""

import re
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

from okfmig import typerules
from okfmig.apply import BlockedPlanError, StaleFileError, apply_plan
from okfmig.brain import WIKI_FOLDERS
from okfmig.frontmatter import parse
from okfmig.gitutil import commit_times_under, ignored_under
from okfmig.links import linked_paths, relative_href
from okfmig.planner import build_plan
from okfmig.reserved import add_log_entry
from okfmig.snapshot import PREVIOUS, load_batches, load_ledger, save_batches, save_ledger
from okfmig.vault import discover

SIMILAR_RATIO = 0.75
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
    except OSError, UnicodeDecodeError:
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


def _log_line(brain: Path, page: str) -> str:
    text = (brain / page).read_bytes().decode("utf-8")
    path = PurePosixPath(page)
    title = _title(_read_meta(brain / page), path.stem)
    href = relative_href(PurePosixPath(LOG), path)
    count = pages_updated(text, path)
    return f"* **Ingest**: [{_escape_title(title)}]({href}) - {count} pages updated"


def _remove_previous(brain: Path, previous: str) -> None:
    """Delete a kept previous snapshot and the folders it leaves empty under the workdir's `previous/`."""
    path = brain / previous
    path.unlink(missing_ok=True)
    stop = brain / PREVIOUS
    folder = path.parent
    while folder != stop and folder.is_relative_to(stop) and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def complete_batch(brain: Path, batch_id: str, today: date) -> list[str]:
    """Mark a batch ingested: normalize pages and indexes, update the ledger, log one line per source.

    Every check runs before the first write, so a refused batch leaves the brain untouched.
    """
    batches = load_batches(brain)
    batch = next((b for b in batches if b.id == batch_id), None)
    if batch is None:
        raise BatchError(f"unknown or already completed batch {batch_id!r}")
    missing = [item.summary_page for item in batch.items if not (brain / item.summary_page).is_file()]
    if missing:
        raise BatchError(f"batch {batch_id}: missing summary pages: {', '.join(missing)}")
    ledger = load_ledger(brain)
    unknown = [item.origin for item in batch.items if item.origin not in ledger]
    if unknown:
        raise BatchError(f"batch {batch_id}: origins missing from the ledger: {', '.join(unknown)}")

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
    lines = [_log_line(brain, item.summary_page) for item in batch.items]  # after apply: normalized titles

    for item in batch.items:
        entry = ledger[item.origin]
        entry.ingested_sha256 = entry.origin_sha256
    save_ledger(brain, ledger)

    log = brain / LOG
    text = log.read_bytes().decode("utf-8") if log.exists() else None
    for line in lines:
        text = add_log_entry(text, today, line)
    assert text is not None
    log.write_bytes(text.encode("utf-8"))

    save_batches(brain, [b for b in batches if b.id != batch_id])
    for item in batch.items:
        if item.previous is not None:
            _remove_previous(brain, item.previous)
    return lines
