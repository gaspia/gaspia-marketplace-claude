"""Action-item store: task records, ids, similarity and state transitions."""

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Literal

from okfmig.ingest import SIMILAR_RATIO
from okfmig.snapshot import _read_json, _write_json

STORE = PurePosixPath(".okf") / "tasks.json"

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
