import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from okfmig.brain import BrainSettings
from okfmig.snapshot import Batch, BatchItem
from okfmig.tasks import (
    BUCKETS,
    BatchTask,
    Task,
    TaskError,
    TaskSource,
    TaskStore,
    batch_tasks_path,
    check_batch_tasks,
    collapse_whitespace,
    load_batch_tasks,
    load_store,
    mark_today_run,
    merge_batch_tasks,
    normalize_text,
    save_store,
    similar_tasks,
    task_id,
    today_buckets,
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


SNAPSHOT = "Paulo to write up the routing\ndecision by Friday.\nAna to send the deck."
ORIGIN = "vault:Sync.md"
SETTINGS = BrainSettings()


def _brain(tmp_path: Path) -> Path:
    raw = tmp_path / "raw" / "vault" / "Sync.md"
    raw.parent.mkdir(parents=True)
    raw.write_text(SNAPSHOT, encoding="utf-8")
    return tmp_path


def _batch(summary: str = "sources/2026-11-17-sync.md", origin: str = ORIGIN) -> Batch:
    return Batch("b001", [BatchItem(origin, "raw/vault/Sync.md", "new", None, summary)])


def _entry(**overrides) -> BatchTask:
    fields = {"origin": ORIGIN, "text": "Write up the routing decision", "quote": "write up the routing decision"}
    fields.update(overrides)
    return BatchTask(**fields)


def test_collapse_whitespace():
    assert collapse_whitespace("  a \n\t b  ") == "a b"


def test_check_quote_matches_across_line_breaks(tmp_path: Path):
    brain = _brain(tmp_path)
    entry = _entry(quote="write up the routing decision by Friday")
    check_batch_tasks(brain, _batch(), [entry], TaskStore(), SETTINGS, TODAY)


def test_check_refusals(tmp_path: Path):
    brain = _brain(tmp_path)
    entries = [
        _entry(origin="vault:Other.md"),
        _entry(quote="never said"),
        _entry(due="Friday"),
        _entry(owner="people/nobody.md"),
        _entry(inferred=["status"]),
        _entry(same_as="deadbeef"),
    ]
    with pytest.raises(TaskError) as exc:
        check_batch_tasks(brain, _batch(), entries, TaskStore(), SETTINGS, TODAY)
    lines = str(exc.value).splitlines()
    assert len(lines) == 6
    for index, line in enumerate(lines, start=1):
        assert f"#{index}" in line


def test_check_refuses_path_escapes(tmp_path: Path):
    brain = _brain(tmp_path)
    (tmp_path.parent / "outside.md").write_text("x", encoding="utf-8")
    entries = [_entry(page="../outside.md"), _entry(page=str(tmp_path / "raw" / "vault" / "Sync.md"))]
    with pytest.raises(TaskError) as exc:
        check_batch_tasks(brain, _batch(), entries, TaskStore(), SETTINGS, TODAY)
    assert len(str(exc.value).splitlines()) == 2


def test_check_too_old(tmp_path: Path):
    brain = _brain(tmp_path)
    batch = _batch("sources/2026-09-01-sync.md")
    with pytest.raises(TaskError, match="too old"):
        check_batch_tasks(brain, batch, [_entry()], TaskStore(), SETTINGS, TODAY)
    check_batch_tasks(brain, batch, [_entry()], TaskStore(), replace(SETTINGS, tasks_max_age_days=90), TODAY)


def test_check_id_collision(tmp_path: Path):
    brain = _brain(tmp_path)
    tid = task_id(ORIGIN, "Write up the routing decision")
    other = _task(id=tid, sources=[TaskSource("vault:Else.md", "raw/e.md", "q")])
    with pytest.raises(TaskError, match="id collision"):
        check_batch_tasks(brain, _batch(), [_entry()], _store(other), SETTINGS, TODAY)
    same = _task(id=tid, sources=[TaskSource(ORIGIN, "raw/vault/Sync.md", "q")])
    check_batch_tasks(brain, _batch(), [_entry()], _store(same), SETTINGS, TODAY)


def test_merge_new_tasks():
    store = TaskStore()
    entries = [
        _entry(owner="me", due="2026-11-21", inferred=["due"]),
        _entry(text="Send the deck", quote="send the deck", owner="Ana"),
    ]
    counts = merge_batch_tasks(store, _batch(), entries, SETTINGS, TODAY)
    assert counts == {ORIGIN: 2}
    first = store.tasks[task_id(ORIGIN, "Write up the routing decision")]
    second = store.tasks[task_id(ORIGIN, "Send the deck")]
    assert (first.status, second.status) == ("open", "waiting")
    assert first.due == "2026-11-21" and first.inferred == ["due"]
    assert first.created == first.updated == "2026-11-18" and first.closed is None
    assert first.sources == [TaskSource(ORIGIN, "raw/vault/Sync.md", "write up the routing decision")]


def test_merge_default_owner_inferred():
    store = TaskStore()
    merge_batch_tasks(store, _batch(), [_entry()], SETTINGS, TODAY)
    task = next(iter(store.tasks.values()))
    assert task.owner == "me" and "owner" in task.inferred


def test_merge_is_idempotent():
    store = TaskStore()
    entries = [
        _entry(due="2026-11-21", inferred=["due"]),
        _entry(text="Send the deck", quote="send the deck", owner="Ana"),
    ]
    merge_batch_tasks(store, _batch(), entries, SETTINGS, TODAY)
    before = json.dumps({k: vars(v) | {"sources": [vars(s) for s in v.sources]} for k, v in store.tasks.items()})
    merge_batch_tasks(store, _batch(), entries, SETTINGS, date(2026, 11, 25))
    after = json.dumps({k: vars(v) | {"sources": [vars(s) for s in v.sources]} for k, v in store.tasks.items()})
    assert before == after


def test_merge_same_as_rewording():
    existing = _task(id="aaaaaaaa", text="Write up the routing decision")
    store = _store(existing)
    batch = Batch("b001", [BatchItem("vault:Two.md", "raw/vault/Two.md", "new", None, "sources/2026-11-17-two.md")])
    entry = _entry(origin="vault:Two.md", text="Write up the Private Gateway routing decision", same_as="aaaaaaaa")
    merge_batch_tasks(store, batch, [entry], SETTINGS, TODAY)
    assert list(store.tasks) == ["aaaaaaaa"]
    assert store.tasks["aaaaaaaa"].text == "Write up the Private Gateway routing decision"
    assert [s.origin for s in store.tasks["aaaaaaaa"].sources] == ["v:a.md", "vault:Two.md"]


def test_merge_respects_manual():
    tid = task_id(ORIGIN, "Write up the routing decision")
    existing = _task(id=tid, due="2026-12-01", manual=["due"], sources=[TaskSource(ORIGIN, "old", "old")])
    store = _store(existing)
    merge_batch_tasks(store, _batch(), [_entry(due="2026-11-21")], SETTINGS, TODAY)
    assert store.tasks[tid].due == "2026-12-01"
    assert store.tasks[tid].sources == [TaskSource(ORIGIN, "raw/vault/Sync.md", "write up the routing decision")]


def test_merge_keeps_closed_task_closed():
    tid = task_id(ORIGIN, "Write up the routing decision")
    existing = _task(id=tid, status="done", closed="2026-11-10", sources=[TaskSource(ORIGIN, "r", "q")])
    store = _store(existing)
    merge_batch_tasks(store, _batch(), [_entry(owner="Ana")], SETTINGS, TODAY)
    assert store.tasks[tid].status == "done" and store.tasks[tid].closed == "2026-11-10"


def test_load_batch_tasks_missing_and_bad(tmp_path: Path):
    assert load_batch_tasks(tmp_path, "b001") == []
    path = batch_tasks_path(tmp_path, "b001")
    assert path == tmp_path / ".okf-ingest" / "batches" / "b001" / "tasks.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"text": 1}), encoding="utf-8")
    with pytest.raises(TaskError):
        load_batch_tasks(tmp_path, "b001")
    path.write_text(json.dumps([{"origin": "o", "text": "t", "quote": "q", "priority": 1}]), encoding="utf-8")
    with pytest.raises(TaskError):
        load_batch_tasks(tmp_path, "b001")
    path.write_text(json.dumps([{"origin": "o", "text": "t"}]), encoding="utf-8")
    with pytest.raises(TaskError):
        load_batch_tasks(tmp_path, "b001")
    path.write_text(json.dumps([{"origin": "o", "text": "t", "quote": "q"}]), encoding="utf-8")
    assert load_batch_tasks(tmp_path, "b001") == [BatchTask("o", "t", "q")]


def test_merge_is_idempotent_with_several_entries_for_one_task():
    existing = _task(id="aaaaaaaa", text="Write up the routing decision")
    store = _store(existing)
    batch = Batch(
        "b001",
        [
            BatchItem("vault:One.md", "raw/vault/One.md", "new", None, "sources/2026-11-17-one.md"),
            BatchItem("vault:Two.md", "raw/vault/Two.md", "new", None, "sources/2026-11-17-two.md"),
        ],
    )
    entries = [
        _entry(origin="vault:One.md", text="Write the routing decision", due="2026-11-20", same_as="aaaaaaaa"),
        _entry(
            origin="vault:Two.md", text="Write up the gateway routing decision", due="2026-11-21", same_as="aaaaaaaa"
        ),
    ]
    merge_batch_tasks(store, batch, entries, SETTINGS, TODAY)
    assert store.tasks["aaaaaaaa"].updated == "2026-11-18"
    merge_batch_tasks(store, batch, entries, SETTINGS, date(2026, 11, 25))
    assert store.tasks["aaaaaaaa"].updated == "2026-11-18"


def test_check_refuses_wrongly_typed_values(tmp_path: Path):
    brain = _brain(tmp_path)
    entries = [
        _entry(page=5),
        _entry(inferred=[["due"]]),
        _entry(same_as=["x"]),
        _entry(owner=7),
        _entry(due=20261121),
        _entry(text=None),
    ]
    with pytest.raises(TaskError) as exc:
        check_batch_tasks(brain, _batch(), entries, TaskStore(), SETTINGS, TODAY)
    lines = str(exc.value).splitlines()
    assert len(lines) == 6
    assert "page must be a string or null" in lines[0]
    assert "inferred must be a list of strings" in lines[1]
    assert "same_as must be a string or null" in lines[2]
    assert "owner must be a string or null" in lines[3]


def _ids(buckets: dict[str, list[Task]], name: str) -> list[str]:
    return [t.id for t in buckets[name]]


def test_buckets_due_today_boundary():
    store = _store(_task(id="aaaaaaaa", due="2026-11-17"), _task(id="bbbbbbbb", due="2026-11-18"))
    buckets = today_buckets(store, TODAY, 14)
    assert list(buckets) == list(BUCKETS)
    assert _ids(buckets, "overdue") == ["aaaaaaaa"]
    assert _ids(buckets, "due today") == ["bbbbbbbb"]


def test_buckets_stale_boundary():
    store = _store(_task(id="aaaaaaaa", updated="2026-11-04"), _task(id="bbbbbbbb", updated="2026-11-05"))
    buckets = today_buckets(store, TODAY, 14)
    assert _ids(buckets, "stale") == ["aaaaaaaa"]


def test_buckets_waiting_and_closed():
    store = _store(
        _task(id="aaaaaaaa", status="waiting", due="2026-11-17", updated="2026-01-01"),
        _task(id="bbbbbbbb", status="done", due="2026-11-17"),
        _task(id="cccccccc", status="dropped", due="2026-11-17"),
    )
    buckets = today_buckets(store, TODAY, 14)
    assert _ids(buckets, "waiting on others") == ["aaaaaaaa"]
    assert sum(len(v) for v in buckets.values()) == 1


def test_buckets_new_since_last_run():
    store = _store(
        _task(id="aaaaaaaa", created="2026-11-16", updated="2026-11-17"),
        _task(id="bbbbbbbb", created="2026-11-17", updated="2026-11-17"),
    )
    store.new_since = "2026-11-16"
    assert _ids(today_buckets(store, TODAY, 14), "new") == ["bbbbbbbb"]
    fresh = _store(_task(id="cccccccc", updated="2026-11-17"))
    assert _ids(today_buckets(fresh, TODAY, 14), "new") == ["cccccccc"]
    store.last_today = "2026-11-17"
    store.new_since = "2026-11-10"
    mark_today_run(store, TODAY)
    assert (store.new_since, store.last_today) == ("2026-11-17", "2026-11-18")
    mark_today_run(store, TODAY)
    assert (store.new_since, store.last_today) == ("2026-11-17", "2026-11-18")
