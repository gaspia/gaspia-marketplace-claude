"""Action-item store: task records, ids, similarity and state transitions."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Literal

from okfmig.brain import BrainSettings
from okfmig.snapshot import BATCH_WORK, Batch, _read_json, _write_json

SIMILAR_RATIO = 0.75
STORE = PurePosixPath(".okf") / "tasks.json"
BATCH_TASKS = BATCH_WORK

Status = Literal["open", "waiting", "done", "dropped"]
ACTIVE = ("open", "waiting")
FIELDS = ("owner", "due")


class TaskError(Exception):
    """A refused task operation."""


@dataclass
class TaskSource:
    origin: str
    raw_path: str
    quote: str


@dataclass
class Task:
    id: str
    text: str
    owner: str
    status: Status
    due: str | None
    sources: list[TaskSource]
    page: str | None
    inferred: list[str]
    manual: list[str]
    created: str
    updated: str
    closed: str | None


@dataclass
class TaskStore:
    tasks: dict[str, Task] = field(default_factory=dict)
    last_today: str | None = None
    new_since: str | None = None


def normalize_text(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text.casefold()).strip()
    return collapsed.rstrip(".!?;:, \t\n").strip()


def task_id(origin: str, text: str) -> str:
    return hashlib.sha256(f"{origin}\n{normalize_text(text)}".encode()).hexdigest()[:8]


def load_store(brain: Path) -> TaskStore:
    path = brain / STORE
    data = _read_json(path, None)
    if data is None:
        return TaskStore()
    if not isinstance(data, dict) or not isinstance(data.get("tasks", {}), dict):
        raise ValueError(f"{path}: expected a mapping with a tasks mapping")
    try:
        tasks = {
            tid: Task(id=tid, **{**body, "sources": [TaskSource(**s) for s in body["sources"]]})
            for tid, body in data.get("tasks", {}).items()
        }
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f"{path}: invalid task: {exc}") from exc
    return TaskStore(tasks=tasks, last_today=data.get("last_today"), new_since=data.get("new_since"))


def save_store(brain: Path, store: TaskStore) -> None:
    tasks = {}
    for tid, task in store.tasks.items():
        body = asdict(task)
        del body["id"]
        tasks[tid] = body
    _write_json(brain / STORE, {"last_today": store.last_today, "new_since": store.new_since, "tasks": tasks})


def similar_tasks(store: TaskStore, text: str) -> list[tuple[Task, float]]:
    """Active tasks similar to `text`. No substring shortcut: short verbs would match everything."""
    wanted = normalize_text(text)
    hits = []
    for task in store.tasks.values():
        if task.status not in ACTIVE:
            continue
        score = SequenceMatcher(None, wanted, normalize_text(task.text)).ratio()
        if score >= SIMILAR_RATIO:
            hits.append((task, score))
    return sorted(hits, key=lambda hit: (-hit[1], hit[0].id))


def transition(  # noqa: PLR0913
    brain: Path,
    store: TaskStore,
    tid: str,
    action: Literal["done", "defer", "delegate", "drop"],
    today: date,
    *,
    to: str | None = None,
    until: date | None = None,
) -> Task:
    """Apply `action` to the task in `store` (not saved; the caller saves)."""
    task = store.tasks.get(tid)
    if task is None:
        raise TaskError(f"unknown task: {tid}")
    if task.status not in ACTIVE:
        raise TaskError(f"task {tid} is already {task.status}")
    if action == "defer" and until is None:
        raise TaskError("defer needs a date (until)")
    if action == "delegate":
        if not to:
            raise TaskError("delegate needs an owner (to)")
        if to.startswith("people/") and not (brain / to).is_file():
            raise TaskError(f"no such person page: {to}")
    stamp = today.isoformat()
    if action in ("done", "drop"):
        task.status = "done" if action == "done" else "dropped"
        task.closed = stamp
    elif action == "defer":
        task.due = until.isoformat()
        _mark_manual(task, "due")
    else:
        task.owner = to
        task.status = "open" if to == "me" else "waiting"
        _mark_manual(task, "owner")
    task.updated = stamp
    return task


def _mark_manual(task: Task, name: str) -> None:
    if name in task.inferred:
        task.inferred.remove(name)
    if name not in task.manual:
        task.manual.append(name)


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class BatchTask:
    origin: str
    text: str
    quote: str
    owner: str | None = None
    due: str | None = None
    page: str | None = None
    inferred: list[str] = field(default_factory=list)
    same_as: str | None = None


def batch_tasks_path(brain: Path, batch_id: str) -> Path:
    return brain / BATCH_TASKS / batch_id / "tasks.json"


def load_batch_tasks(brain: Path, batch_id: str) -> list[BatchTask]:
    path = batch_tasks_path(brain, batch_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TaskError(f"{path}: cannot read: {exc}") from exc
    if not isinstance(data, list):
        raise TaskError(f"{path}: expected a list of tasks")
    entries = []
    for position, body in enumerate(data, start=1):
        if not isinstance(body, dict):
            raise TaskError(f"{path}: entry {position} is not an object")
        try:
            entries.append(BatchTask(**body))
        except TypeError as exc:
            raise TaskError(f"{path}: entry {position}: {exc}") from exc
    return entries


def _file_problem(brain: Path, rel: str, label: str) -> str | None:
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts:
        return f"{label} must be a path inside the brain: {rel}"
    if not (brain / rel).is_file():
        return f"{label} does not exist: {rel}"
    return None


def _type_problems(entry: BatchTask) -> list[str]:
    problems = [
        f"{name} must be a string" for name in ("origin", "text", "quote") if not isinstance(getattr(entry, name), str)
    ]
    problems += [
        f"{name} must be a string or null"
        for name in ("owner", "due", "page", "same_as")
        if getattr(entry, name) is not None and not isinstance(getattr(entry, name), str)
    ]
    if not isinstance(entry.inferred, list) or not all(isinstance(name, str) for name in entry.inferred):
        problems.append("inferred must be a list of strings")
    return problems


def _entry_problems(  # noqa: PLR0912, PLR0913, PLR0917
    brain: Path, batch: Batch, entry: BatchTask, store: TaskStore, settings: BrainSettings, today: date
) -> list[str]:
    problems = _type_problems(entry)
    if problems:
        return problems
    item = next((i for i in batch.items if i.origin == entry.origin), None)
    if item is None:
        problems.append(f"origin is not in batch {batch.id}")
    if not normalize_text(entry.text):
        problems.append("text is empty")
    if not collapse_whitespace(entry.quote):
        problems.append("quote is empty")
    elif item is not None:
        try:
            snapshot = (brain / item.raw_path).read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            problems.append(f"cannot read snapshot {item.raw_path}: {exc}")
        else:
            if collapse_whitespace(entry.quote) not in collapse_whitespace(snapshot):
                problems.append("quote is not in the snapshot")
    if entry.due is not None:
        try:
            date.fromisoformat(entry.due)
        except ValueError:
            problems.append(f"due is not an ISO date: {entry.due!r}")
    if entry.owner is not None:
        if not entry.owner.strip():
            problems.append("owner is empty")
        elif entry.owner.startswith("people/"):
            problem = _file_problem(brain, entry.owner, "owner page")
            if problem:
                problems.append(problem)
    if entry.page is not None:
        problem = _file_problem(brain, entry.page, "page")
        if problem:
            problems.append(problem)
    if not set(entry.inferred) <= set(FIELDS):
        problems.append(f"inferred must be a subset of {list(FIELDS)}")
    if entry.same_as is not None and entry.same_as not in store.tasks:
        problems.append(f"same_as names no task: {entry.same_as}")
    if item is not None:
        problems.extend(_age_and_collision_problems(item.summary_page, entry, store, settings, today))
    return problems


def _age_and_collision_problems(
    summary_page: str, entry: BatchTask, store: TaskStore, settings: BrainSettings, today: date
) -> list[str]:
    problems = []
    try:
        summary_date = date.fromisoformat(PurePosixPath(summary_page).name[:10])
    except ValueError:
        problems.append(f"summary page has no date prefix: {summary_page}")
    else:
        if summary_date < today - timedelta(days=settings.tasks_max_age_days):
            problems.append(f"source is too old ({summary_date.isoformat()}, limit {settings.tasks_max_age_days} days)")
    if entry.same_as is None:
        existing = store.tasks.get(task_id(entry.origin, entry.text))
        if existing is not None and all(source.origin != entry.origin for source in existing.sources):
            problems.append(f"id collision with task {existing.id}")
    return problems


def check_batch_tasks(  # noqa: PLR0913, PLR0917
    brain: Path, batch: Batch, entries: list[BatchTask], store: TaskStore, settings: BrainSettings, today: date
) -> None:
    """Refuse the whole file, listing every problem, one per line. Writes nothing."""
    lines = []
    for position, entry in enumerate(entries, start=1):
        for problem in _entry_problems(brain, batch, entry, store, settings, today):
            lines.append(f"#{position} {entry.origin}: {problem}")
    if lines:
        raise TaskError("\n".join(lines))


def merge_batch_tasks(
    store: TaskStore, batch: Batch, entries: list[BatchTask], settings: BrainSettings, today: date
) -> dict[str, int]:
    """Apply checked entries to `store` in file order; returns entries merged per origin."""
    counts: dict[str, int] = {}
    stamp = today.isoformat()
    initial: dict[str, dict] = {}
    for entry in entries:
        item = next(i for i in batch.items if i.origin == entry.origin)
        inferred = list(entry.inferred)
        owner = entry.owner
        if owner is None:
            owner = settings.tasks_default_owner
            inferred.append("owner")
        quote = collapse_whitespace(entry.quote)
        tid = entry.same_as or task_id(entry.origin, entry.text)
        task = store.tasks.get(tid)
        if task is None:
            store.tasks[tid] = Task(
                id=tid,
                text=entry.text,
                owner=owner,
                status="open" if owner == "me" else "waiting",
                due=entry.due,
                sources=[TaskSource(entry.origin, item.raw_path, quote)],
                page=entry.page,
                inferred=[name for name in FIELDS if name in inferred],
                manual=[],
                created=stamp,
                updated=stamp,
                closed=None,
            )
        else:
            initial.setdefault(tid, asdict(task))
            _merge_existing(task, entry, item.raw_path, quote, owner, inferred)
        counts[entry.origin] = counts.get(entry.origin, 0) + 1
    for tid, before in initial.items():
        if asdict(store.tasks[tid]) != before:
            store.tasks[tid].updated = stamp
    return counts


def _merge_existing(  # noqa: PLR0913, PLR0917
    task: Task, entry: BatchTask, raw_path: str, quote: str, owner: str, inferred: list[str]
) -> None:
    task.text = entry.text
    if entry.page is not None:
        task.page = entry.page
    source = next((s for s in task.sources if s.origin == entry.origin), None)
    if source is None:
        task.sources.append(TaskSource(entry.origin, raw_path, quote))
    else:
        source.raw_path = raw_path
        source.quote = quote
    for name, value in (("owner", owner), ("due", entry.due)):
        if name in task.manual:
            continue
        setattr(task, name, value)
        if name in inferred and name not in task.inferred:
            task.inferred.append(name)
        elif name not in inferred and name in task.inferred:
            task.inferred.remove(name)
    if task.status in ACTIVE and "owner" not in task.manual:
        task.status = "open" if task.owner == "me" else "waiting"
