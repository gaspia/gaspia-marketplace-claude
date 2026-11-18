# Task rules

Read this before extracting any action item. Tasks are the user's follow-ups, kept in `.okf/tasks.json`. You never write that store: you write one batch file, and `done` checks and merges it. The brain's `CLAUDE.md` schema section points here. The examples assume the user is Paulo.

## When a commitment is a task

- **A stated commitment and an actor.** "Paulo to write up the routing decision by Friday" is a task. "We should probably look at multi-infrastructure support sometime" is not. This is the same judgment as "When a page earns its place" in `references/page-types.md`: the source must say it, you do not read it in.
- **No stated actor.** A commitment in the user's own notes with no actor ("send the deck to X") is a task with `owner` null. `done` gives it `tasks.default_owner` (default `me`) and marks `owner` inferred.
- **Anything vaguer is not a task.** Wishes, ideas, open questions and "someone should" stay on the wiki pages, if anywhere.

## Only the user's own follow-ups

- The user's own commitments are tasks.
- Someone else's commitment is a task only when it was made to the user or the user must chase it ("Ana to send the deck to Paulo"). It gets that person as `owner` and becomes a `waiting` task.
- Team backlog work (bugs, features, tickets the team tracks) belongs in Jira, not here. List it in the draft as "not made tasks: Jira work".

## Only recent sources

- The source date is the date of its summary page, the `YYYY-MM-DD` prefix of the `summary page` path from `next`.
- A source whose date is older than `tasks.max_age_days` (default 30) before today yields no tasks: `done` refuses the whole file if any entry comes from one. List its commitments in the draft as "not made tasks: too old".
- A source with no date of its own (no `date` or `created` in its frontmatter, no date in its filename) got the snapshot date. Extract from it as usual, and flag it as undated in the draft so the user can judge its age.

## Fields

- **`origin`**: the source's `origin` exactly as `next` prints it. It must be in the batch.
- **`text`**: the task as a short imperative ("Write up the routing decision"), without the owner or the date.
- **`quote`**: the citation. A short verbatim span of the snapshot that states the commitment, copied from the file under `raw/`. Line breaks and runs of spaces may differ; every other character must match, case included. A task with no quote, or one that is not in the snapshot, is refused.
- **`owner`**: `me` for the user; a `people/<slug>.md` path (from the brain root) when that person page exists; else the bare name ("Ana") with `owner` in `inferred`. `null` when the source states no actor (see above).
- **`due`**: an ISO date (`2026-11-20`), only when the source states one. A relative date ("by Friday") resolves against the source's date, not today, and puts `due` in `inferred`. No stated date: `null`.
- **`page`**: the project or area page the task belongs to (`projects/<slug>.md`, from the brain root), only when the source states which one and the page exists (or you write it in this batch). Else `null`.
- **`inferred`**: the fields you resolved rather than copied: a subset of `["owner", "due"]`.
- **`same_as`**: the id of an existing task this entry is, when the user answered "same task" (below). Else `null`.

## Duplicates

- Run `task-similar <brain> "<text>"` before adding any task, in every review mode, autonomous included. It prints `<score>  <id>  <status>  <owner>  <text>` for each open or waiting task close to the text, or "no similar tasks".
- Every hit is a duplicate question for the user, as `similar` is for pages. Also compare the tasks of this draft with each other.
- "Same task" becomes an entry with `same_as: <id>`. "Different task" becomes a new entry with `same_as` null.

## The file

Write `.okf-ingest/batches/<batch-id>/tasks.json` before running `done`: a JSON list, one object per task, in the order you want them merged. Omit the file when the batch has no actions; `done` then leaves the store alone. `done` deletes it once the batch completes.

```json
[
  {
    "origin": "main:Work/Meetings/Sync 20261117.md",
    "text": "Write up the routing decision",
    "quote": "Paulo to write up the routing decision by Friday.",
    "owner": "me",
    "due": "2026-11-20",
    "page": "projects/private-gateway.md",
    "inferred": ["due"],
    "same_as": null
  },
  {
    "origin": "main:Work/Meetings/Sync 20261124.md",
    "text": "Write up the Private Gateway routing decision",
    "quote": "Paulo to write up the Private Gateway routing decision",
    "owner": "me",
    "due": null,
    "page": "projects/private-gateway.md",
    "inferred": [],
    "same_as": "3f9a01c2"
  }
]
```

`done` refuses the whole file, writing nothing, and lists every problem as `#<n> <origin>: <problem>` when an entry has: an origin not in the batch; empty `text`; a `quote` that is empty or not in the snapshot; a `due` that is not an ISO date; an empty `owner`, or a `people/` owner with no such page; a `page` that is not an existing file inside the brain; `inferred` naming anything but `owner` and `due`; a `same_as` naming no task; a source older than `tasks.max_age_days`; or a computed id that belongs to a task from another source (use `same_as`). Fix the file and re-run `done`.

How `done` merges each entry:

- **`same_as`, or an id already in the store:** updates `text`, `page` (when given), and the entry's source and quote, adding the source when it is new. `owner` and `due` are replaced by the entry's values unless the user set them by hand (`task delegate`, `task defer`), so repeat them on every update. `status` stays, except that an open or waiting task follows its owner.
- **Otherwise:** a new task, `open` when the owner is `me`, `waiting` otherwise.

Merging is idempotent: re-running `done` after a crash duplicates nothing.

## Review draft

Add an "Actions extracted" section per source, after the pages:

- **Tasks to create:** text, owner, due, page, and the quote.
- **Tasks to update:** with their ids, and what changes.
- **Not made tasks:** each with its reason ("not a commitment", "Jira work", "too old").
- **Duplicate questions:** every `task-similar` hit, and any two tasks of the draft that might be the same.
- Flag an undated source here.

## Common mistakes

- A `quote` paraphrased or fixed up: `done` refuses it. Copy it from the snapshot.
- A relative date resolved against today: resolve it against the source's date.
- Turning a team's bug or ticket into a task: it is Jira work.
- Leaving `due` out of an update entry: it clears the due date the source gave.
- A bare name as `owner` while that person's page exists: use the `people/<slug>.md` path.
