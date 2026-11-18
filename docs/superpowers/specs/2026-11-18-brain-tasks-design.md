# Second Brain Tasks: Design

Date: 2026-11-18
Status: Draft, pending approval

## Intent

The `ingest` skill (already merged) distills notes into cited wiki pages. It handles knowledge well and commitments not at all: a meeting note that says "Paulo to write up the Private Gateway routing decision by Friday" becomes a line in a source summary and then nothing. Decisions get a page; action items evaporate.

This design adds the missing half: action items extracted from the same sources, kept as mutable state beside the wiki, and surfaced daily. The target is a single morning command that answers what is overdue, what is due today, and what Paulo is waiting on from someone else, with every item traceable to the note that produced it.

The why: Paulo runs an engineering team, so most of his list is other people's commitments to him, and the sources that record them are the same 1:1 and sync notes the brain already ingests. A landscape scan (2026-11-18) found nothing to reuse: the OKF ecosystem defines no task, owner, due date or completion state anywhere, and no file-based Obsidian task system models an assignee, so delegation is unoccupied territory. Extraction is a commodity; the state half is not.

## Goals

- Extract action items from each source during the existing ingest flow, under the same discipline as pages: every task cites its source, nothing is invented, guessed fields are marked.
- Keep task state in exactly one place, written only by the script.
- Re-ingesting a changed source updates the tasks it already produced instead of creating duplicates.
- A daily view: overdue, due today, waiting on others, stale, new.
- A per-person view of open items, for 1:1 preparation.
- Let Paulo tick boxes in Obsidian and have the script read them back.
- Keep the brain OKF-conformant and portable; keep the wiki unchanged in character.
- Hold only Paulo's own follow-ups; team-visible work stays in Jira.

## Non-goals

- Replacing Jira, or any two-way sync with it (Phase 3, its own spec).
- Recurring tasks, subtasks, dependencies, priorities, estimates, time tracking.
- Parsing natural-language dates ("next week", "end of sprint").
- Notifications, reminders, or anything that runs without Paulo asking.
- Calendar integration (Phase 3).
- Query and Lint operations (later skills, as in the ingest design).
- Deleting tasks. Dropping one closes it; nothing is removed.

## Anti-goals (failure even if it "works")

- A task created from a vague intention ("we should probably look at X sometime").
- A guessed owner or due date that reads as if the source stated it.
- Two tasks for one commitment because a note was corrected and re-ingested.
- `tasks.json` and the markdown views disagreeing about whether something is done.
- A task orphaned because its snapshot or summary page moved.
- The wiki churning daily because task state leaked into it.
- `log.md` filled with task state changes, drowning the ingest history.

## Constraints

- Reuse the `okfmig` package, Python 3.14 via `uv`, Black and Ruff at line length 120, as in `okf-migrate` and `ingest`.
- The existing hard rules stay in force: `raw/` is immutable, never invent facts, cite every claim, mark inferred content, edit pages in place, never commit automatically.
- `done` refuses while a page sits outside the wiki folders, so any new folder must either be a wiki folder or be excluded from the bundle.
- The brain must stay valid against `okf_brain.py validate` and the inherited OKF checks at every step.
- Phase 1 must work with no network and no connector: a meeting note on disk in, a daily list out.

## Approaches considered

- **A. A seventh page type (`tasks/`, one file per task).** Consistent with the existing model and browsable in Obsidian. Rejected: a task changes state three or four times a week, so the wiki would churn constantly and stop being a stable record; `similar`, the indexes and the log would all have to absorb that churn; and "edit the page, never regenerate" is the wrong rule for a field that is meant to be overwritten.
- **B. A separate store plus generated views (chosen).** `.okf/tasks.json` holds state, written only by the script; markdown views are generated from it and read back. The wiki keeps its character, task state gets a deterministic owner, and the views stay disposable.
- **C. Inline checkboxes on project and person pages as the source of truth.** Closest to how Obsidian users usually work. Rejected: no single place to query from, it fights "never regenerate", and the scan found Obsidian Bases queries whole notes rather than checkboxes inside them, so this approach would hard-depend on Dataview (no release since April 2025) or the experimental Datacore.

## Decisions made with Paulo

- Tasks get their own layer rather than a seventh page type.
- Extraction rides inside the existing ingest flow rather than a parallel workflow.
- Three phases: (1) store, extraction, `today`, validation; (2) markdown round-trip, `agenda`, `digest`; (3) Calendar then a narrow Jira link.
- Team-visible work lives in Jira; the brain holds only Paulo's own follow-ups.
- Build rather than adopt, on the scan's finding that nothing reusable exists. The one piece worth plugging in rather than writing is capture (Granola Sync, if Paulo uses Granola).
- Approach B.

## Design

### Packaging

- New modules: `okfmig/tasks.py` (the record, the store, state transitions, `task-similar`) and `okfmig/taskviews.py` (rendering the markdown views and reading them back). The CLI extends `okfmig/braincli.py`; no second entry script.
- No new skill in Phase 1 (designer's call). The `ingest` skill gains an "actions extracted" step in its review draft and a `references/task-rules.md` holding the extraction contract, because extraction is one more thing Claude does with a source it is already reading. A separate `tasks` skill may follow in Phase 2 if the daily path grows beyond a command.

### Layer and layout

```
<brain>/
  .okf/tasks.json        task store (written only by the script)
  .okf-ingest/batches/<batch-id>/tasks.json   per-batch extraction, merged by `done`
  tasks/open.md          generated view, checkbox per open task
  tasks/waiting.md       generated view, checkbox per delegated task
```

`tasks/` is generated output, so it is excluded from the bundle in `.okf/types.yaml` alongside `raw/**` (designer's call). It therefore gets no frontmatter, no index, and no normalization from `done`, and a regenerated view never shows up as a wiki page or churns the indexes. The cost is that the views are not browsable from `index.md`; `today` is the real interface, and the views exist for ticking boxes on a phone.

### The task record

One entry per task in `.okf/tasks.json`:

| Field | Meaning |
| --- | --- |
| `id` | First 8 hex characters of `sha256(origin + "\n" + normalized_text)`. Normalized text is casefolded, whitespace-collapsed, trailing punctuation stripped. |
| `text` | The commitment, as an imperative sentence. |
| `owner` | `me`, a `people/<slug>.md` path, or a bare name when no person page exists. |
| `status` | `open`, `waiting`, `done`, `dropped`. |
| `due` | ISO date, or null. Never a guess. |
| `origin`, `raw_path` | The source and its snapshot, in the ledger's `<source-name>:<relative path>` form. |
| `page` | The project or area page this belongs to, when a source states one. |
| `inferred` | Which of `owner` and `due` Claude inferred rather than read. |
| `created`, `closed` | ISO dates. `closed` is null while open or waiting. |

The id is derived rather than assigned so that re-snapshotting a corrected note produces the same id for an unchanged commitment, which is what makes re-ingest safe. A reworded commitment produces a new id, which is why `task-similar` exists.

### Extraction contract

Claude writes `.okf-ingest/batches/<batch-id>/tasks.json` while processing the batch, and `done` merges it. The contract, which belongs in `references/task-rules.md`:

- A task needs a stated commitment and an actor. "Paulo to write up the routing decision by Friday" is a task; "we should probably look at multi-infrastructure support sometime" is not. The same judgment as "when a page earns its place".
- Cite the source. A task with no citation is a validation error, exactly as for a page.
- Record a due date only when the source states one. A relative date the source states ("by Friday") resolves against the source's own date and is marked inferred.
- Resolve the owner to a person page when one exists, otherwise keep the bare name and mark it inferred.
- Run `task-similar` before adding any task, in every review mode. Every hit is a duplicate question for Paulo, exactly as `similar` is for pages.
- The review draft gains an "actions extracted" section listing, per source: tasks to create, tasks to update, items deliberately not made tasks, and the duplicate questions.

`done` merges by id: a known id updates `text` and `page` and leaves state alone; an unknown id is added as `open` (or `waiting` when the owner is not `me`). Merging is idempotent, so re-running after a crash completes the batch without duplicating, as `done` already does for pages.

### Commands

Added to `okf_brain.py`:

- `today <brain>`: five buckets, in order: overdue, due today, waiting on others, stale (no state change in N days, N from settings), new since the last run.
- `task done|defer|delegate|drop <id> [--to <person>] [--until <date>]`: one state transition, written to the store. Refuses an unknown or already-closed id.
- `task-similar <brain> "<text>"`: open and waiting tasks whose normalized text is close, reusing the `difflib` approach and the 0.75 threshold from `similar`.
- `agenda <brain> <person>`: every open and waiting task owned by that person, with its source, for 1:1 preparation (Phase 2).
- `digest <brain> --since <date>`: decisions recorded, tasks opened, tasks closed, in the period (Phase 2).
- `tasks render <brain>` and `task sync <brain>` (Phase 2, see below).

Exit codes follow the existing convention: 0 ok, 1 refused or validation failure, 2 usage or preflight error.

### Markdown round-trip (Phase 2)

`tasks render` writes `tasks/open.md` and `tasks/waiting.md`, one checkbox per task, each line ending in a block id `^t-<id>`. `task sync` reads them back and applies only the checkbox state: a ticked box closes the task with today's date. Text edited by hand in a view is not read back; `sync` reports any line whose text has drifted from the store and leaves it alone (designer's call: a view is disposable, so hand edits there must never become the source of truth silently). `done` and every `task` transition re-render the views, so they are never stale for long.

### Log policy

Task state changes do not touch `log.md` (designer's call). The log is the brain's knowledge history, and a few hundred state transitions a month would bury the ingest entries. The existing per-source line gains a count: `* **Ingest**: [<title>](sources/<file>.md) - N pages updated, M actions`. Task history lives in `created` and `closed` plus git.

### Validation

`validate` gains, as errors unless noted:

- every task's `raw_path` exists under `raw/` and its `origin` is in the source ledger;
- every `owner` that names a person page resolves to an existing file;
- every `due` parses as an ISO date;
- ids are unique, and each matches the hash of its own `origin` and `text` (catches hand edits to the store);
- no task references a summary page that no longer exists;
- a warning for any open task whose source was re-ingested and whose text no longer appears in the current snapshot.

### Daily path

An `inbox/` folder in the brain, a `--mode` flag on `snapshot` and `done` overriding `review.mode` for one run, and a `/brain-daily` command that snapshots `inbox/`, ingests autonomously, runs `today`, and writes the result into Paulo's Obsidian daily note. The bulk-import path keeps its review gate; only the daily path runs unattended, and duplicate questions are still asked and surface in the morning output.

### Settings

Added to `.okf/brain.yaml`:

```yaml
tasks:
  stale_days: 14     # no state change for this long shows in today's stale bucket
  default_owner: me  # owner when a source states a commitment with no actor
```

Missing keys mean these defaults, as elsewhere.

### Testing

- Unit tests per new module: id derivation and normalization, merge by id (new, known, reworded), every state transition including refusals, `task-similar` thresholds, view rendering and the sync read-back, each validation check.
- End-to-end with real git fixtures: ingest a source producing three candidate actions, re-snapshot it corrected, confirm one task updated and none duplicated; tick a box in a view and confirm `sync` closes exactly that task; confirm `tasks/` is excluded from indexes and `done` does not normalize it.
- The full existing suite still passes unchanged.
- A rehearsal on scratch copies: ingest three real meeting notes, check every task cites its source, no invented tasks, and `today` and `agenda` read correctly.

## Left to the builder

- Internal structure of the new modules and the `tasks.json` schema beyond the fields named.
- The exact wording of `references/task-rules.md` and the "actions extracted" section in the ingest review draft.
- Output formatting of `today`, `agenda` and `digest` (plain text, grouped by bucket; no colour).
- Whether `tasks render` runs automatically after `done` or only on demand.
- Collision handling if two different sources produce the same id (prefix with the source name, or lengthen the hash).
