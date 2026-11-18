# Brain Tasks (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract action items during ingest into a script-owned task store, and answer "what is overdue, due today, waiting, stale or new" with one command.

**Architecture:** A new `okfmig/tasks.py` holds the record, the store (`.okf/tasks.json`), id derivation, `task-similar`, state transitions, the per-batch extraction file and its merge, and the `today` bucketing. `ingest.complete_batch` checks and merges the batch's `tasks.json` and adds an action count to the log line. `braincli.py` gains `today`, `task` and `task-similar`; `brainvalidate.py` gains the task checks. Claude writes the extraction file under the contract in a new `skills/ingest/references/task-rules.md`.

**Tech Stack:** Python 3.14 via `uv`, stdlib (`json`, `hashlib`, `difflib`, `dataclasses`), `ruamel.yaml` (settings), pytest, Black and Ruff at line length 120.

**Spec:** `docs/superpowers/specs/2026-11-18-brain-tasks-design.md` (Phase 1 only: store, extraction, `today`, transitions, `task-similar`, validation, settings). Out of scope here: `tasks/` views, `tasks render`, `task sync`, `agenda`, `digest` (Phase 2); the daily path and `done --commit` (Phase 3).

All paths below are relative to `plugins/second-brain/`. Run commands from there.

## Global Constraints

- Reuse the `okfmig` package; no second entry script. Python 3.14 via `uv`, Black and Ruff at line length 120.
- `raw/` is immutable; never invent facts; cite every claim; mark inferred content; never commit automatically.
- Task state lives only in `.okf/tasks.json`, written only by the script. Task state changes never touch `log.md`.
- Exit codes: 0 ok, 1 refused or validation failure, 2 usage or preflight error.
- Phase 1 works with no network and no connector.
- Settings defaults: `tasks.stale_days: 14`, `tasks.default_owner: me`, `tasks.max_age_days: 30`. Missing keys mean these defaults.
- Similarity threshold 0.75 (`ingest.SIMILAR_RATIO`).
- Id: first 8 hex characters of `sha256(origin + "\n" + normalized_text)`, computed once at creation, never recomputed.
- The full existing suite passes unchanged.
- Plain punctuation in all docs and output: no em dashes.

## Review Focus

- A quote that wraps across lines in the snapshot, or has double spaces, must still match: compare whitespace-collapsed text (Task 3 test `test_check_quote_matches_across_line_breaks`).
- A batch with no `tasks.json` (a source with no actions) completes exactly as today, log line unchanged (Task 4 test `test_done_without_tasks_file_unchanged`).
- Batch ids restart at `b001`: a leftover `.okf-ingest/batches/b001/tasks.json` from an undone batch must never be merged into a new `b001` (Task 4 test `test_snapshot_clears_leftover_batch_tasks`).
- Re-ingesting a source whose task Paulo closed must not reopen it (Task 3 test `test_merge_keeps_closed_task_closed`).
- Date boundaries: due exactly today is "due today" not "overdue"; `updated` exactly `stale_days` ago is stale (Task 5 tests `test_buckets_due_today_boundary`, `test_buckets_stale_boundary`).

---

### Task 1: Task settings

**Files:**
- Modify: `scripts/okfmig/brain.py` (`DEFAULT_BRAIN_YAML`, `BrainSettings`, `load_settings`)
- Modify: `scripts/okfmig/templates/schema.md` (Settings section)
- Test: `tests/test_brain.py`

**Interfaces:**
- Produces: `BrainSettings` gains `tasks_stale_days: int = 14`, `tasks_default_owner: str = "me"`, `tasks_max_age_days: int = 30` (appended after the existing fields so positional construction keeps working).

- [ ] **Step 1: Write the failing tests** in `tests/test_brain.py`:
  - `test_task_settings_defaults`: brain with no `tasks:` section → `load_settings(brain)` has `tasks_stale_days == 14`, `tasks_default_owner == "me"`, `tasks_max_age_days == 30`.
  - `test_task_settings_read`: `tasks: {stale_days: 7, default_owner: people/ana.md, max_age_days: 90}` → those values.
  - `test_task_settings_invalid`: each of `stale_days: 0`, `max_age_days: true`, `default_owner: ""` → `pytest.raises(ValueError, match="tasks\\.")`.
  - `test_init_writes_task_settings`: after `init_brain`, `.okf/brain.yaml` contains `stale_days: 14` and `max_age_days: 30`.
- [ ] **Step 2: Run** `uv run pytest tests/test_brain.py -v`. Expected: the four new tests FAIL.
- [ ] **Step 3: Implement.** Add to `DEFAULT_BRAIN_YAML`:
  ```yaml
  tasks:
    stale_days: 14     # no state change for this long shows in today's stale bucket
    default_owner: me  # owner when a source states a commitment with no actor
    max_age_days: 30   # sources older than this at ingest yield no tasks
  ```
  Parse with the existing `_section`; positive int checks as for `batch_size`; `default_owner` a non-empty string. Error messages name the key as `tasks.<key>`. In `schema.md` Settings, add one bullet per key, and a `## Tasks` section of three lines: the store is `.okf/tasks.json`, written only by `okf_brain.py`; extraction follows `references/task-rules.md` of the ingest skill; never edit the store by hand.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run black --check .`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(tasks): task settings in brain.yaml`

---

### Task 2: Task record, store, ids, similarity and transitions

**Files:**
- Create: `scripts/okfmig/tasks.py`
- Test: `tests/test_tasks.py`

**Interfaces:**
- Consumes: `snapshot._write_json`, `snapshot._read_json` (same JSON style as the ledger: sorted keys, indent 2, `ensure_ascii=False`), `ingest.SIMILAR_RATIO`.
- Produces (all in `okfmig.tasks`):
  - `STORE = PurePosixPath(".okf") / "tasks.json"`
  - `Status = Literal["open", "waiting", "done", "dropped"]`; `ACTIVE = ("open", "waiting")`; `FIELDS = ("owner", "due")`
  - `@dataclass class TaskSource: origin: str; raw_path: str; quote: str`
  - `@dataclass class Task: id: str; text: str; owner: str; status: Status; due: str | None; sources: list[TaskSource]; page: str | None; inferred: list[str]; manual: list[str]; created: str; updated: str; closed: str | None`
  - `@dataclass class TaskStore: tasks: dict[str, Task]; last_today: str | None = None; new_since: str | None = None`
  - `class TaskError(Exception)`: a refused task operation.
  - `normalize_text(text: str) -> str`: casefold, collapse whitespace runs to one space, strip, then strip trailing `.!?;:,` and whitespace.
  - `task_id(origin: str, text: str) -> str`
  - `load_store(brain: Path) -> TaskStore` (empty store when the file is missing; `ValueError` naming the path on bad JSON or shape) and `save_store(brain: Path, store: TaskStore) -> None`. On disk: `{"last_today": ..., "new_since": ..., "tasks": {<id>: {<every field but id>}}}`.
  - `similar_tasks(store: TaskStore, text: str) -> list[tuple[Task, float]]`: active tasks whose `SequenceMatcher` ratio on normalized texts is `>= SIMILAR_RATIO`, best first, ties by id. No substring shortcut (unlike `similar_pages`): short verbs would match everything.
  - `transition(brain: Path, store: TaskStore, tid: str, action: Literal["done", "defer", "delegate", "drop"], today: date, *, to: str | None = None, until: date | None = None) -> Task`

- [ ] **Step 1: Write the failing tests** in `tests/test_tasks.py` (build `Task` values with a small `_task(**overrides)` helper; `TODAY = date(2026, 11, 18)`):
  - `test_normalize_text`: `normalize_text("  Write   the Doc!. ") == "write the doc"`.
  - `test_task_id_stable_under_case_and_spacing`: `task_id("v:a.md", "Write the doc.") == task_id("v:a.md", "write  the doc")`; differs for origin `"v:b.md"`; `len == 8` and all hex.
  - `test_store_round_trip`: save then load a store with two tasks and `last_today="2026-11-17"` → equal; file ends with `\n` and keys sorted.
  - `test_store_missing_is_empty`: `load_store(tmp)` → `tasks == {}`, `last_today is None`.
  - `test_store_bad_json`: `.okf/tasks.json` containing `[` → `pytest.raises(ValueError, match="tasks.json")`.
  - `test_similar_tasks_threshold`: active tasks "Write up the routing decision" and "Book flights"; query "write up routing decision" → one hit, score `>= 0.75`; a `done` task with the same text is not returned.
  - `test_transition_done`: open task → status `done`, `closed == updated == "2026-11-18"`.
  - `test_transition_drop`: → `dropped`, `closed` set.
  - `test_transition_defer`: `until=date(2026, 11, 25)` on a task with `inferred=["due"]` → `due == "2026-11-25"`, `"due" in manual`, `"due" not in inferred`, status unchanged.
  - `test_transition_delegate`: `to="people/ana.md"` (file exists) → `owner == "people/ana.md"`, `status == "waiting"`, `"owner" in manual`; delegating back with `to="me"` → `status == "open"`.
  - `test_transition_delegate_missing_person_refused`: `to="people/nobody.md"` → `TaskError`.
  - `test_transition_unknown_or_closed_refused`: unknown id → `TaskError`; `done` on a `done` task → `TaskError`.
- [ ] **Step 2: Run** `uv run pytest tests/test_tasks.py -v`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** per Interfaces. `transition` mutates the task in `store` and sets `updated = today`; it does not save (the CLI saves). `defer` requires `until`, `delegate` requires `to` (`TaskError` otherwise). A `to` starting with `people/` must be an existing file under the brain.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run black --check .`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(tasks): task store, ids, similarity and transitions`

---

### Task 3: Batch extraction file: check and merge

**Files:**
- Modify: `scripts/okfmig/tasks.py`
- Test: `tests/test_tasks.py`

**Interfaces:**
- Consumes: `Task`, `TaskStore`, `TaskSource`, `task_id`, `normalize_text`, `TaskError` (Task 2); `snapshot.Batch`, `snapshot.BatchItem`; `brain.BrainSettings`, `brain.WORKDIR`.
- Produces:
  - `collapse_whitespace(text: str) -> str`: whitespace runs to one space, stripped (used for quote matching here and in validation).
  - `BATCH_TASKS = PurePosixPath(WORKDIR) / "batches"`; `batch_tasks_path(brain: Path, batch_id: str) -> Path` → `<brain>/.okf-ingest/batches/<batch_id>/tasks.json`.
  - `@dataclass class BatchTask: origin: str; text: str; quote: str; owner: str | None = None; due: str | None = None; page: str | None = None; inferred: list[str] = field(default_factory=list); same_as: str | None = None`
  - `load_batch_tasks(brain: Path, batch_id: str) -> list[BatchTask]`: `[]` when the file is missing; `TaskError` on bad JSON, a non-list, or an entry with unknown or missing keys.
  - `check_batch_tasks(brain: Path, batch: Batch, entries: list[BatchTask], store: TaskStore, settings: BrainSettings, today: date) -> None`: raises `TaskError` listing every problem, writes nothing.
  - `merge_batch_tasks(store: TaskStore, batch: Batch, entries: list[BatchTask], settings: BrainSettings, today: date) -> dict[str, int]`: mutates `store`; returns actions per origin (entries counted per origin, for the log line).

Check rules, each a refusal:
- `origin` is one of the batch's item origins.
- `text` normalizes to non-empty; `quote` is non-empty and, whitespace-collapsed, is a substring of the whitespace-collapsed snapshot (`brain / item.raw_path`, UTF-8).
- `due` is null or `date.fromisoformat` parses it; `owner` is null or a non-empty string, and one starting `people/` is an existing file; `page`, when set, is an existing file; `inferred` is a subset of `FIELDS`.
- `same_as` names a task in the store.
- Age: the item's summary date is the `YYYY-MM-DD` at the start of the `summary_page` file name (`sources/<date>-<slug>.md`, always written by `snapshot`). Refuse when it is before `today - timedelta(days=settings.tasks_max_age_days)`, with a message naming the origin and "too old".
- Collision: no `same_as`, the computed id is in the store, and none of that task's sources has this origin → refuse ("id collision").

Merge rules, per entry, in file order:
- `owner` null → `settings.tasks_default_owner`, and `"owner"` added to the entry's inferred.
- Target: `same_as` if set, else the computed id if in the store, else a new task.
- New task: id = computed id; status `open` when owner is `me`, else `waiting`; `created = updated = today`; `closed = None`; `manual = []`; `sources = [TaskSource(origin, item.raw_path, quote)]`.
- Existing task: set `text`; set `page` when the entry has one; add the source when no source has this origin, else refresh that source's `raw_path` and `quote`; for each of `owner`, `due` not in `manual`, take the entry's value and its inferred flag; when status is active and `owner` is not in `manual`, set status from the owner as above. Never change a `done` or `dropped` status. Set `updated = today` only when some field actually changed (so a re-run changes nothing).

- [ ] **Step 1: Write the failing tests** (fixture: brain with `.okf/sources.json` ledger entry for origin `vault:Sync.md`, snapshot `raw/vault/Sync.md` containing `"Paulo to write up the routing\ndecision by Friday.\nAna to send the deck."`, a `Batch("b001", [BatchItem("vault:Sync.md", "raw/vault/Sync.md", "new", None, "sources/2026-11-17-sync.md")])`, `TODAY = date(2026, 11, 18)`):
  - `test_check_quote_matches_across_line_breaks`: quote `"write up the routing decision by Friday"` passes.
  - `test_check_refusals`: one entry each with: origin not in batch; quote not in snapshot; `due="Friday"`; owner `people/nobody.md`; `inferred=["status"]`; `same_as="deadbeef"` unknown. `TaskError` message names all six.
  - `test_check_too_old`: summary page `sources/2026-09-01-sync.md`, default `max_age_days` → `TaskError` matching `"too old"`; with `max_age_days=90` it passes.
  - `test_merge_new_tasks`: entries "Write up the routing decision" (owner `me`, due `2026-11-21`, inferred `["due"]`) and "Send the deck" (owner `Ana`) → two tasks, statuses `open` and `waiting`, ids equal `task_id("vault:Sync.md", text)`, counts `{"vault:Sync.md": 2}`.
  - `test_merge_default_owner_inferred`: owner null → `owner == "me"`, `"owner" in inferred`.
  - `test_merge_is_idempotent`: merging the same entries twice → same store, `updated` unchanged on the second run with a later `today`.
  - `test_merge_same_as_rewording`: existing task X; entry `same_as=X.id`, new text "Write up the Private Gateway routing decision" from a second origin in the batch → one task, text updated, two sources, id still X.
  - `test_merge_respects_manual`: task with `manual=["due"]`, `due="2026-12-01"`; re-ingest entry with `due="2026-11-21"` → due stays `2026-12-01`.
  - `test_merge_keeps_closed_task_closed`: task `done`; re-ingest same text → status still `done`, `closed` unchanged.
  - `test_load_batch_tasks_missing_and_bad`: no file → `[]`; file with `{"text": 1}` (not a list) → `TaskError`; entry with key `"priority"` → `TaskError`.
- [ ] **Step 2: Run** `uv run pytest tests/test_tasks.py -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces and the rules above.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run black --check .`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(tasks): check and merge per-batch extraction`

---

### Task 4: `done` merges tasks; `snapshot` clears leftovers

**Files:**
- Modify: `scripts/okfmig/ingest.py` (`_log_line`, `complete_batch`)
- Modify: `scripts/okfmig/snapshot.py` (`take_snapshot`)
- Test: `tests/test_ingest.py`, `tests/test_snapshot.py`

**Interfaces:**
- Consumes: `load_store`, `save_store`, `load_batch_tasks`, `check_batch_tasks`, `merge_batch_tasks`, `batch_tasks_path`, `BATCH_TASKS`, `TaskError` (Tasks 2-3); `brain.load_settings`.
- Produces: `complete_batch(brain, batch_id, today)` signature unchanged. Log line becomes `* **Ingest**: [<title>](sources/<file>.md) - N pages updated, M actions` when M > 0 (`1 action` singular); unchanged when M is 0.

Order inside `complete_batch`: after `_check`, load settings, store and entries, run `check_batch_tasks` (a `TaskError` becomes `BatchError(f"batch {batch_id}: {exc}")`), all before `apply_plan`. After `apply_plan`: merge and `save_store`, then the ledger, log, dequeue as now, then delete `.okf-ingest/batches/<batch_id>/` (`shutil.rmtree(..., ignore_errors=True)`) last. `take_snapshot`: when no batches were pending on entry, delete `<brain>/.okf-ingest/batches/` before queueing.

- [ ] **Step 1: Write the failing tests:**
  - `test_ingest.py::test_done_merges_tasks_and_counts_actions`: the existing happy-path fixture plus a `.okf-ingest/batches/b001/tasks.json` with two entries citing a quote present in the snapshot → `.okf/tasks.json` has two tasks; the returned log line ends with `- 1 pages updated, 2 actions`; `.okf-ingest/batches/b001/` no longer exists.
  - `test_ingest.py::test_done_without_tasks_file_unchanged`: no tasks file → log line ends with `pages updated` (no actions suffix); no `.okf/tasks.json` written.
  - `test_ingest.py::test_done_refuses_bad_tasks_before_writing`: tasks file with a quote not in the snapshot → `BatchError` matching `"quote"`; brain tree byte-identical to before (use the existing `_tree` helper).
  - `test_ingest.py::test_done_rerun_after_crash_merges_once`: with `_fail_once(monkeypatch, "save_ledger")` style crash after the store save, re-run → still two tasks, one log line.
  - `test_snapshot.py::test_snapshot_clears_leftover_batch_tasks`: no pending batches and a leftover `.okf-ingest/batches/b001/tasks.json` → after `take_snapshot`, that file is gone.
- [ ] **Step 2: Run** `uv run pytest tests/test_ingest.py tests/test_snapshot.py -v`. Expected: new tests FAIL; existing pass.
- [ ] **Step 3: Implement** per Interfaces. `_log_line` gains an `actions: int` parameter.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run black --check .`. Expected: PASS, including every pre-existing test unchanged.
- [ ] **Step 5: Commit** `feat(tasks): done merges extracted actions`

---

### Task 5: `today` buckets and the CLI commands

**Files:**
- Modify: `scripts/okfmig/tasks.py` (bucketing)
- Modify: `scripts/okfmig/braincli.py` (`COMMANDS`, parser, handlers)
- Test: `tests/test_tasks.py`, `tests/test_braincli.py`

**Interfaces:**
- Consumes: everything in `okfmig.tasks` above; `snapshot.load_ledger` (to show each task's summary page).
- Produces:
  - `BUCKETS = ("overdue", "due today", "waiting on others", "stale", "new")`
  - `today_buckets(store: TaskStore, today: date, stale_days: int) -> dict[str, list[Task]]`: every key of `BUCKETS` present, each task in the first bucket it matches, in this order:
    - overdue: status `open`, `due < today`.
    - due today: status `open`, `due == today`.
    - waiting on others: status `waiting` (any due), sorted by due (null last) then created.
    - stale: active, `updated <= today - stale_days`.
    - new: active, and `created > store.new_since` (every remaining active task when `new_since` is null).
    Other buckets are sorted by due (null last), then id.
  - `mark_today_run(store: TaskStore, today: date) -> None`: when `last_today != today`, set `new_since = last_today` and `last_today = today` (a second run on the same day keeps the same "new" list).
  - CLI, added to `COMMANDS` (each takes `brain` like every command):
    - `today <brain> [--today YYYY-MM-DD]`: buckets, then `mark_today_run` and `save_store`. Prints each non-empty bucket as a header with its first letter capitalized and the count (`Overdue (2)`, `Waiting on others (1)`) and lines `  <id>  <due or ->  <owner>  <text>  (<summary page of the first source, from the ledger>)`; prints `nothing due, waiting, stale or new` when all are empty. Exit 0.
    - `task <brain> {done,defer,delegate,drop} <id> [--to PERSON] [--until YYYY-MM-DD] [--today YYYY-MM-DD]`: one `transition`, save, print `<id> <status>: <text>`. `TaskError` → exit 1 `refused: ...`. `defer` without `--until`, `delegate` without `--to`, or either flag on another action → exit 2.
    - `task-similar <brain> "<text>"`: lines `<score:.2f>  <id>  <status>  <owner>  <text>`, or `no similar tasks`. Exit 0.

- [ ] **Step 1: Write the failing tests:**
  - `test_tasks.py::test_buckets_due_today_boundary`: open tasks due `2026-11-17` and `2026-11-18`, today `2026-11-18` → first in "overdue", second in "due today".
  - `test_tasks.py::test_buckets_stale_boundary`: open tasks with no due, `updated` `2026-11-04` and `2026-11-05`, `stale_days=14` → only the first is stale.
  - `test_tasks.py::test_buckets_waiting_and_closed`: a `waiting` task due yesterday is in "waiting on others" only; `done` and `dropped` tasks appear nowhere.
  - `test_tasks.py::test_buckets_new_since_last_run`: `new_since="2026-11-16"`, tasks created `2026-11-16` and `2026-11-17` (no due, fresh) → only the second is "new"; `mark_today_run` twice on the same day leaves `new_since` unchanged.
  - `test_braincli.py::test_today_prints_buckets`: brain with a store of one overdue and one waiting task → `main(["today", str(brain), "--today", "2026-11-18"])` returns 0; output contains `Overdue (1)` and `Waiting on others (1)` and the task ids; store `last_today == "2026-11-18"`.
  - `test_braincli.py::test_today_empty`: no store → output `nothing due, waiting, stale or new`, exit 0.
  - `test_braincli.py::test_task_transitions_cli`: `task <brain> done <id>` → exit 0 and the store shows `done`; repeating it → exit 1 with `refused`; `task <brain> defer <id>` with no `--until` → exit 2.
  - `test_braincli.py::test_task_similar_cli`: prints the matching id with a score; unrelated text → `no similar tasks`.
- [ ] **Step 2: Run** `uv run pytest tests/test_tasks.py tests/test_braincli.py -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces. The existing parser loop adds `brain` to every command; add the extra arguments per command after it, as for `snapshot` and `done`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run black --check .`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(tasks): today, task and task-similar commands`

---

### Task 6: Validation of the task store

**Files:**
- Modify: `scripts/okfmig/brainvalidate.py` (`validate_brain`, new `_check_tasks`)
- Test: `tests/test_brainvalidate.py`

**Interfaces:**
- Consumes: `load_store`, `ACTIVE` (Task 2), `collapse_whitespace` (Task 3); `snapshot.load_ledger`.
- Produces: `validate_brain` reports, as errors: a task with no sources; a source whose `origin` is not in the ledger; a source whose `raw_path` is not an existing file under `raw/`; an `owner` starting `people/` that is not an existing file; a `page` that is not an existing file; a `due`, `created`, `updated` or non-null `closed` that is not an ISO date; an id not matching `^[0-9a-f]{8}$`; a status outside the four; `closed` set on an active task or null on a closed one. As a warning: an active task where no source's quote (whitespace-collapsed) appears in its current snapshot. Each message starts with `tasks.json: <id>:`. An unreadable store raises `ValueError` (exit 2), like the ledger. Ids are unique by construction (the store is keyed by id).

- [ ] **Step 1: Write the failing tests** (reuse `_done_brain`; write `.okf/tasks.json` directly):
  - `test_valid_tasks_clean`: one valid task citing the done brain's snapshot → no errors or warnings starting `tasks.json`.
  - `test_task_errors`: one task per defect above (no sources; origin unknown; raw path missing; `people/nobody.md`; page `projects/gone.md`; due `Friday`; id `XYZ`; status `later`; `closed` on an open task) → one `tasks.json:` error each, nine in total.
  - `test_task_quote_gone_warns`: active task whose quote is not in the snapshot → one warning; the same task with status `done` → no warning.
- [ ] **Step 2: Run** `uv run pytest tests/test_brainvalidate.py -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run black --check .`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(tasks): validate the task store`

---

### Task 7: Extraction contract, skill wiring and end-to-end

**Files:**
- Create: `skills/ingest/references/task-rules.md`
- Modify: `skills/ingest/SKILL.md`, `scripts/okfmig/templates/schema.md` (Ingest workflow, Log format)
- Test: `tests/test_end_to_end.py` (or `tests/test_braincli.py`, whichever already holds the brain ingest flow)

**Interfaces:**
- Consumes: the CLI from Task 5 and `done` from Task 4.
- Produces: the contract Claude follows; no code.

`task-rules.md` holds, in this order:
1. **When a commitment is a task:** a stated commitment and an actor ("Paulo to write up the routing decision by Friday" is one; "we should probably look at multi-infrastructure support sometime" is not). A commitment in Paulo's own notes with no stated actor ("send the deck to X") leaves `owner` null, which the script turns into `default_owner`, marked inferred. Anything vaguer is not a task.
2. **Only Paulo's follow-ups:** someone else's commitment is a task only when it was made to Paulo or he must chase it. Team backlog work (bugs, features, tickets the team tracks) belongs in Jira: list it as "not made tasks: Jira work".
3. **Only recent sources:** a source whose summary date is older than `tasks.max_age_days` yields no tasks (`done` refuses them); list its commitments as "not made tasks: too old". A source with no date of its own got the snapshot date; extract from it and flag it as undated in the draft.
4. **Fields:** `due` only when the source states one; a relative date ("by Friday") resolves against the source's date and goes in `inferred`. Owner: `me`, a `people/<slug>.md` path when the page exists, else the bare name with `owner` in `inferred`. `quote`: a short verbatim span of the snapshot (line breaks may differ). `page`: the project or area page when the source states one.
5. **Duplicates:** run `task-similar` before adding any task, in every review mode. Every hit is a duplicate question. "Same task" becomes `same_as: <id>`.
6. **The file:** `.okf-ingest/batches/<batch-id>/tasks.json`, a JSON list, written before `done`, omitted when the batch has no actions. One example entry with every key (`origin`, `text`, `quote`, `owner`, `due`, `page`, `inferred`, `same_as`) and one `same_as` entry.
7. **Review draft:** an "Actions extracted" section per source: tasks to create, tasks to update (with ids), not made tasks with the reason, duplicate questions.

`SKILL.md` changes: add `today`, `task`, `task-similar` to the subcommand line; hard rule "Run `task-similar` before proposing any task; every hit is a duplicate question, in every mode"; step 3.1 also reads `references/task-rules.md`; step 3.4 adds the "Actions extracted" section; step 3.6 writes the batch `tasks.json`; step 3.7 describes the `, M actions` suffix and that `done` refuses an invalid or too-old action; step 4 hand-off suggests `today`. `schema.md` Ingest workflow and Log format get the same two facts.

- [ ] **Step 1: Write the failing end-to-end test** `test_ingest_with_actions_then_corrected`, with real git, through `main([...])` only: init a brain; snapshot a source `Sync 20261117.md` with three action lines ("Paulo to write up the routing decision by Friday.", "Ana to send the deck to Paulo.", "We should look at multi-infra sometime."); write the summary page and a batch `tasks.json` with the first two; `done --today 2026-11-18` → exit 0, store has 2 tasks, log line ends `2 actions`; commit; edit the source so the first line reads "Paulo to write up the Private Gateway routing decision by Friday."; snapshot again; `task-similar` on the new wording prints the first task's id; write the summary update and a `tasks.json` with `same_as` for it; `done` → store still has 2 tasks and the first has the new text; `task done <id>` → exit 0; `validate` → exit 0; `today --today 2026-11-18` lists the Ana task under "Waiting on others (1)".
- [ ] **Step 2: Run** `uv run pytest tests/test_end_to_end.py -v -k actions`. Expected: PASS, since the code exists and this test pins the whole flow. A failure is a gap in an earlier task: fix it in the owning module with a unit test.
- [ ] **Step 3: Write** `task-rules.md` and the `SKILL.md` and `schema.md` changes above. Re-read them once as Claude would at step 3.1: every rule in the spec's Extraction contract appears, with no em dashes.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run black --check .`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(tasks): extraction contract and ingest skill wiring`

---

### Task 8: Rehearsal on real notes

**Files:** none committed.

- [ ] **Step 1:** Copy a scratch brain (or `init` a fresh one in the scratchpad) and three real meeting notes from the last 30 days. Ingest them through the skill in `batch` mode.
- [ ] **Step 2:** Check: every task's quote is in its snapshot; no task from a vague intention; no team backlog item; inferred owners and dues marked; `validate` exit 0.
- [ ] **Step 3:** Run `today` and read it as Paulo would at 9am: correct buckets, every line traceable to a source summary.
- [ ] **Step 4:** Report findings to Paulo. Fix anything found in the owning task's module, with a test, and commit.
