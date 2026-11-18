# Second Brain Tasks: Design

Date: 2026-11-18
Status: Approved

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

- Replacing Jira, or any two-way sync with it (Phase 4, its own spec).
- Recurring tasks, subtasks, dependencies, priorities, estimates, time tracking.
- Parsing natural-language dates ("next week", "end of sprint").
- Notifications, reminders, or anything that runs without Paulo asking.
- Calendar integration (Phase 4).
- The unattended daily path (Phase 3, its own spec; see Daily path).
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
- Four phases: (1) store, extraction, `today`, validation; (2) markdown round-trip, `agenda`, `digest`; (3) the unattended daily path; (4) Calendar then a narrow Jira link.
- The daily path may commit automatically, through `done --commit`, and only there. The bulk-import path keeps its review gate and manual commits.
- Team-visible work lives in Jira; the brain holds only Paulo's own follow-ups.
- Build rather than adopt, on the scan's finding that nothing reusable exists. The one piece worth plugging in rather than writing is capture (Granola Sync, if Paulo uses Granola).
- Approach B.
- Old sources do not produce tasks: only a source dated within `tasks.max_age_days` (default 30) of the ingest date is extracted from, so a bulk import of years of notes does not bury `today`.
- The boundary with Jira: a delegated item becomes a `waiting` task only when it was committed to Paulo personally or he is the one who must chase it. Team backlog work (bugs, features, tickets the team tracks) is skipped.

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

`tasks/` is generated output, so it is excluded from the bundle in `.okf/types.yaml` alongside `raw/**` (designer's call). The clean-tree check (`gitutil.dirty_under`) ignores `tasks/` as it ignores `.okf/`, so a ticked box or a re-rendered view never blocks the next `snapshot`. It therefore gets no frontmatter, no index, and no normalization from `done`, and a regenerated view never shows up as a wiki page or churns the indexes. The cost is that the views are not browsable from `index.md`; `today` is the real interface, and the views exist for ticking boxes on a phone.

### The task record

One entry per task in `.okf/tasks.json`:

| Field | Meaning |
| --- | --- |
| `id` | First 8 hex characters of `sha256(origin + "\n" + normalized_text)`, computed once at creation from the first source and never recomputed. Normalized text is casefolded, whitespace-collapsed, trailing punctuation stripped. |
| `text` | The commitment, as an imperative sentence. |
| `owner` | `me`, a `people/<slug>.md` path, or a bare name when no person page exists. |
| `status` | `open`, `waiting`, `done`, `dropped`. |
| `due` | ISO date, or null. Never a guess. |
| `sources` | One or more `{origin, raw_path, quote}`: the source and its snapshot in the ledger's `<source-name>:<relative path>` form, and a short verbatim quote of the commitment from that source. The first entry is the one the id came from. |
| `page` | The project or area page this belongs to, when a source states one. |
| `inferred` | Which of `owner` and `due` Claude inferred rather than read. |
| `manual` | Which of `owner` and `due` Paulo set through a `task` transition. Re-ingest never overwrites these. |
| `created`, `updated`, `closed` | ISO dates. `updated` is the last state or field change (drives the stale bucket). `closed` is null while open or waiting. |

The id is derived at creation so that re-snapshotting a corrected note produces the same id for an unchanged commitment, which is what makes re-ingest safe without any lookup. It is then frozen: a reworded commitment, or the same commitment recorded in a second source, is attached to the existing task through an explicit `same_as` (below), never by recomputing the id (designer's call). This keeps "one task per commitment" true across rewording and across sources.

The store also records the date `today` last ran, for its "new" bucket. The rest of the schema is left to the builder.

### Extraction contract

Claude writes `.okf-ingest/batches/<batch-id>/tasks.json` while processing the batch, and `done` merges it. The contract, which belongs in `references/task-rules.md`:

- A task needs a stated commitment and an actor. "Paulo to write up the routing decision by Friday" is a task; "we should probably look at multi-infrastructure support sometime" is not. The same judgment as "when a page earns its place".
- Cite the source. A task with no citation is a validation error, exactly as for a page.
- Only Paulo's own follow-ups. A commitment by someone else is a task only when it was made to Paulo or he must chase it; team backlog work is Jira's and is listed as "not made tasks: Jira work".
- Only recent sources. The source date is the one ingest already uses for the summary page; a source older than `tasks.max_age_days` yields no tasks, and its commitments are listed as "not made tasks: too old". An undated source is extracted from, and flagged as undated in the review draft (designer's call).
- Record a due date only when the source states one. A relative date the source states ("by Friday") resolves against the source's own date and is marked inferred.
- Resolve the owner to a person page when one exists, otherwise keep the bare name and mark it inferred. A commitment in Paulo's own notes with no stated actor ("send the deck to X") takes `default_owner`, marked inferred (designer's call); anything vaguer is not a task.
- Run `task-similar` before adding any task, in every review mode. Every hit is a duplicate question for Paulo, exactly as `similar` is for pages. When he answers "same task", the batch entry carries `same_as: <id>`.
- The review draft gains an "actions extracted" section listing, per source: tasks to create, tasks to update, items deliberately not made tasks (with the reason), and the duplicate questions.

`done` merges each batch entry (designer's call):

- `same_as: <id>`: updates `text` and `page` on that task, adds the source to `sources` if absent, and leaves `status`, and any field in `manual`, alone.
- otherwise, the computed id is known: same update as above.
- otherwise: added as `open` (or `waiting` when the owner is not `me`).

`due` and `owner` from the source update the task only when not in `manual`. Merging is idempotent, so re-running after a crash completes the batch without duplicating, as `done` already does for pages. A `same_as` naming an unknown id refuses the batch.

### Commands

Added to `okf_brain.py`:

- `today <brain>`: five buckets, in order: overdue, due today, waiting on others, stale (no state change in N days, N from settings), new since the last run.
- `task <brain> done|defer|delegate|drop <id> [--to <person>] [--until <date>]`: one state transition, written to the store, setting `updated`. Refuses an unknown or already-closed id. `defer --until` sets `due` and adds it to `manual` (it is no longer inferred). `delegate --to` sets `owner`, adds it to `manual`, and sets `waiting`; delegating to `me` sets `open`.
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

- every task has at least one source; each source's `raw_path` exists under `raw/` and its `origin` is in the source ledger;
- every `owner` that names a person page resolves to an existing file;
- every `page` resolves to an existing wiki page;
- every `due` parses as an ISO date;
- ids are unique and are 8 lowercase hex characters (they are frozen, so they are not re-hashed);
- a warning for any open or waiting task where none of its sources' `quote`s appears in the current snapshot any more (whitespace-normalized), meaning the commitment may have been removed from the note.

### Daily path (Phase 3, its own spec)

The intent: an inbox folder, a `--mode` flag on `snapshot` and `done` overriding `review.mode` for one run, and a `/brain-daily` command that snapshots the inbox, ingests autonomously, runs `today`, and writes the result into Paulo's Obsidian daily note. The bulk-import path keeps its review gate; only the daily path runs unattended.

Decided now: the daily path commits automatically through `done --commit`, one commit per batch, made only after the batch fully succeeds, with a pathspec limited to the brain folder (including `.okf/`) so nothing else staged in the repo is swept in, a message generated from the source names, and never a push. Each run therefore ends clean and the next morning's `snapshot` is not blocked. The ingest skill's hard rule becomes "never commit, except `done --commit` on the daily path".

Left for the Phase 3 spec, because the current code blocks them: where the inbox lives (`snapshot` refuses a source inside a brain, and `done` refuses pages outside the wiki folders); what happens to processed inbox notes; where the daily note is and whether it is appended or a section replaced; and what an unattended run does with a duplicate question nobody is there to answer.

### Settings

Added to `.okf/brain.yaml`:

```yaml
tasks:
  stale_days: 14     # no state change for this long shows in today's stale bucket
  max_age_days: 30   # sources older than this at ingest yield no tasks
  default_owner: me  # owner when a source states a commitment with no actor
```

Missing keys mean these defaults, as elsewhere.

### Testing

- Unit tests per new module: id derivation and normalization, merge by id (new, known, reworded), every state transition including refusals, `task-similar` thresholds, view rendering and the sync read-back, each validation check.
- End-to-end with real git fixtures: ingest a source producing three candidate actions, re-snapshot it corrected, confirm one task updated and none duplicated; attach a reworded commitment with `same_as` and confirm its state and manual fields survive; confirm a ticked or re-rendered view does not block `snapshot`; tick a box in a view and confirm `sync` closes exactly that task; confirm `tasks/` is excluded from indexes and `done` does not normalize it.
- The full existing suite still passes unchanged.
- A rehearsal on scratch copies: ingest three real meeting notes, check every task cites its source, no invented tasks, and `today` and `agenda` read correctly.

## Left to the builder

- Internal structure of the new modules and the `tasks.json` schema beyond the fields named.
- The exact wording of `references/task-rules.md` and the "actions extracted" section in the ingest review draft.
- Output formatting of `today`, `agenda` and `digest` (plain text, grouped by bucket; no colour).
- Whether `tasks render` runs automatically after `done` or only on demand.
- Collision handling if two different sources produce the same id (prefix with the source name, or lengthen the hash).
