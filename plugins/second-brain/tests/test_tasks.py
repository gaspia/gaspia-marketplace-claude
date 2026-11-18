from datetime import date
from pathlib import Path

import pytest
from okfmig.tasks import (
    Task,
    TaskError,
    TaskSource,
    TaskStore,
    load_store,
    normalize_text,
    save_store,
    similar_tasks,
    task_id,
    transition,
)

TODAY = date(2026, 11, 18)


def _task(**overrides) -> Task:
    fields = {
        "id": "aaaaaaaa",
        "text": "Write the doc",
        "owner": "me",
        "status": "open",
        "due": None,
        "sources": [TaskSource(origin="v:a.md", raw_path="raw/a.md", quote="write the doc")],
        "page": None,
        "inferred": [],
        "manual": [],
        "created": "2026-11-01",
        "updated": "2026-11-01",
        "closed": None,
    }
    fields.update(overrides)
    return Task(**fields)


def _store(*tasks: Task) -> TaskStore:
    return TaskStore(tasks={t.id: t for t in tasks})


def test_normalize_text():
    assert normalize_text("  Write   the Doc!. ") == "write the doc"


def test_task_id_stable_under_case_and_spacing():
    a = task_id("v:a.md", "Write the doc.")
    assert a == task_id("v:a.md", "write  the doc")
    assert a != task_id("v:b.md", "Write the doc.")
    assert len(a) == 8
    assert all(c in "0123456789abcdef" for c in a)


def test_store_round_trip(tmp_path: Path):
    store = _store(_task(), _task(id="bbbbbbbb", text="Book flights", due="2026-12-01", inferred=["due"]))
    store.last_today = "2026-11-17"
    save_store(tmp_path, store)
    text = (tmp_path / ".okf" / "tasks.json").read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text.index('"last_today"') < text.index('"new_since"') < text.index('"tasks"')
    assert load_store(tmp_path) == store


def test_store_missing_is_empty(tmp_path: Path):
    store = load_store(tmp_path)
    assert store.tasks == {}
    assert store.last_today is None


def test_store_bad_json(tmp_path: Path):
    (tmp_path / ".okf").mkdir()
    (tmp_path / ".okf" / "tasks.json").write_text("[", encoding="utf-8")
    with pytest.raises(ValueError, match=r"tasks\.json"):
        load_store(tmp_path)


def test_store_bad_shape(tmp_path: Path):
    (tmp_path / ".okf").mkdir()
    (tmp_path / ".okf" / "tasks.json").write_text('{"tasks": {"x": {"bogus": 1}}}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"tasks\.json"):
        load_store(tmp_path)


def test_similar_tasks_threshold():
    store = _store(
        _task(id="aaaaaaaa", text="Write up the routing decision"),
        _task(id="bbbbbbbb", text="Book flights"),
        _task(id="cccccccc", text="Write up the routing decision", status="done"),
    )
    hits = similar_tasks(store, "write up routing decision")
    assert [t.id for t, _ in hits] == ["aaaaaaaa"]
    assert hits[0][1] >= 0.75


def test_transition_done():
    store = _store(_task())
    task = transition(Path("."), store, "aaaaaaaa", "done", TODAY)
    assert task.status == "done"
    assert task.closed == task.updated == "2026-11-18"


def test_transition_drop():
    store = _store(_task())
    task = transition(Path("."), store, "aaaaaaaa", "drop", TODAY)
    assert task.status == "dropped"
    assert task.closed == "2026-11-18"


def test_transition_defer():
    store = _store(_task(due="2026-11-20", inferred=["due"]))
    task = transition(Path("."), store, "aaaaaaaa", "defer", TODAY, until=date(2026, 11, 25))
    assert task.due == "2026-11-25"
    assert "due" in task.manual
    assert "due" not in task.inferred
    assert task.status == "open"


def test_transition_delegate(tmp_path: Path):
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "ana.md").write_text("# Ana\n", encoding="utf-8")
    store = _store(_task())
    task = transition(tmp_path, store, "aaaaaaaa", "delegate", TODAY, to="people/ana.md")
    assert task.owner == "people/ana.md"
    assert task.status == "waiting"
    assert "owner" in task.manual
    task = transition(tmp_path, store, "aaaaaaaa", "delegate", TODAY, to="me")
    assert task.status == "open"


def test_transition_delegate_missing_person_refused(tmp_path: Path):
    store = _store(_task())
    with pytest.raises(TaskError):
        transition(tmp_path, store, "aaaaaaaa", "delegate", TODAY, to="people/nobody.md")


def test_transition_requires_arguments():
    store = _store(_task())
    with pytest.raises(TaskError):
        transition(Path("."), store, "aaaaaaaa", "defer", TODAY)
    with pytest.raises(TaskError):
        transition(Path("."), store, "aaaaaaaa", "delegate", TODAY)


def test_transition_unknown_or_closed_refused():
    store = _store(_task(id="dddddddd", status="done"))
    with pytest.raises(TaskError):
        transition(Path("."), store, "nope", "done", TODAY)
    with pytest.raises(TaskError):
        transition(Path("."), store, "dddddddd", "done", TODAY)
