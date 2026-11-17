---
name: ingest
description: Ingest notes from any Obsidian vault or folder into the second brain, Karpathy LLM-wiki style: snapshot sources, then create and update concept, project, area, person, decision and source pages with citations. Use when the user asks to ingest, import, or distill notes into their second brain.
---

# ingest

The brain is a wiki you own and maintain: raw sources are snapshotted into `raw/` (immutable), and you distill them into concept, project, area, person, decision and source pages with citations. A bundled script does the bookkeeping (snapshots, hashes, ledger, batches, indexes, log, validation). You do the reading, the review draft and the page edits.

Run every command as:

```bash
uv run <this skill's base directory>/../../scripts/okf_brain.py <subcommand> "<brain>" ...
```

Subcommands: `init <brain>`, `snapshot <brain> <source> [--name NAME]`, `next <brain>`, `similar <brain> "<title>"`, `done <brain> <batch-id>`, `validate <brain>`. Exit codes: 0 ok, 1 refused or validation errors, 2 usage or preflight error. The workdir `<brain>/.okf-ingest/` is git-excluded.

## Hard rules

- Never edit, rename or delete anything under `raw/` or in a source vault.
- Never write a page in `source` or `batch` mode before the user approved that review.
- Never invent facts. Every claim cites a source; inferred sentences end with `(inferred)` and the page gets `status: inferred`.
- Edit existing pages in place; never regenerate one. Keep text the user wrote by hand.
- Run `similar` before proposing any new page. Every hit is a duplicate question for the user, in every mode, autonomous included.
- No `## Personal` or `## Performance` content while its setting is off.
- Never commit. Suggest one commit per batch.

## Workflow

1. **Preflight.** Ask for the brain path and the source (a vault, a folder or one `.md` note). The brain must be a git repo or inside one.
   - No `.okf/brain.yaml`: run `init`. It is idempotent and writes the folders, settings, type rules and the schema section in `CLAUDE.md`.
   - `init` warns when the brain sits inside another Obsidian vault. Tell the user to add the printed glob (`<brain-folder>/**`) to that vault's `.okf/types.yaml` `exclude`, or to move the brain into its own vault. Otherwise snapshots show twice in Obsidian and `okf-migrate` on the outer vault would add frontmatter to them.
   - First run on an existing brain folder that already holds notes (for example a `second-brain/` with a `concepts/` folder and a pre-OKF `index.md` / `log.md`): `init`, then run the `okf-migrate` skill on the brain so its notes, index and log become OKF, have the user commit, then ingest.
   - `validate` fails before any ingest: stop and offer `okf-migrate` on the brain first.
   - Resume: if `next` prints a batch, batches are pending. Skip step 2 and continue at step 3 (no clean tree needed). `snapshot` refuses while batches are pending.
   - Read the settings in `.okf/brain.yaml` (`review.mode`, `review.batch_size`, `people.personal`, `people.performance`) and tell the user the mode.
2. **Snapshot.** The brain subtree must be clean (`.okf/` and `.obsidian/` are ignored); if not, ask the user to commit or stash. Run `snapshot <brain> <source>`. It prints new, changed and unchanged counts, copied attachments, origins no longer found, and the batch ids. The source name defaults to the source folder name in kebab-case; two vaults with the same folder name share it, so pass `--name` (no `/`, `\` or `:`) to keep them apart, and reuse the same name on every re-run of a source. A single note is always a batch of one, and the next `snapshot` refuses until it is done, so ingest several scattered notes from their common folder or one at a time.
3. **Per batch.** Run `next`. Per source it prints `origin`, `raw` (the snapshot), `status` (new or changed), `previous` (changed only) and `summary page`.
   1. Read `references/page-types.md` (once per session) before writing any page.
   2. Read `index.md`, each snapshot, and the existing pages the sources may touch (find them through the indexes and `similar`).
   3. For a `changed` source with a `previous` path, diff it against the new snapshot (`diff -u "<brain>/<previous>" "<brain>/<raw>"`) and process only what changed. If `previous` says none kept, re-read the whole snapshot and compare it with the existing summary page.
   4. Draft the review: per source, the key takeaways; the pages to create or update, each with a one-line gist; `similar` results for every new page and the resulting duplicate questions (also compare the new pages of this draft with each other: `similar` only sees pages already written); name-only mentions you will keep inline instead of giving a page; inferred items; personal and performance content you will write, or skip because its setting is off.
   5. Review per `review.mode`: `source` shows the draft for each source; `batch` shows one combined draft for the batch; `autonomous` shows no draft but still asks the duplicate questions and waits for the answers. Apply the user's corrections to the draft.
   6. Write the pages per `references/page-types.md`. Each source's summary goes to exactly the `summary page` path from `next` (`sources/<YYYY-MM-DD>-<slug>.md`), links its raw snapshot under `## Origin`, and lists every page it created or changed under `## Pages updated`. For a `changed` source, update its existing summary page as part of processing it (for example add what changed and the pages it touched); `done` refuses a summary page left untouched since the snapshot.
   7. Run `done <brain> <batch-id>`. It normalizes pages (fills a missing `type`, `title` or `timestamp` from the folder rules, converts wikilinks), regenerates indexes (a hand-written root `index.md` keeps its text; links to folders it does not list yet, such as a new `people/`, go under a `# Unlisted` section at its end, and the user may move them into their own sections), marks the sources ingested and appends one log line per source: `* **Ingest**: [<title>](sources/<file>.md) - N pages updated`, where N counts the `## Pages updated` links. It refuses, writing nothing, when a summary page is missing or unchanged since the snapshot (in git terms: neither modified nor untracked), or a page sits outside a wiki folder (move it into the right one) or another blocking item exists; fix it and re-run.
   8. Run `validate <brain>`. Fix every error before the next batch. Warnings about personal or performance sections while a setting is off are for the user; do not delete those sections.
   9. Show `git -C "<brain>" diff --stat` and `git status --short`, suggest a commit naming the sources (for example `docs(brain): ingest Weekly Team Q&A`; batch ids restart at `b001` once no batches are pending, so they do not identify a batch in history), and continue with `next` once the user has committed.
4. **Hand-off.** When `next` prints "no pending batches", summarize the pages created and updated across the run and any open duplicate or inferred items.

## Undo a batch

Before committing, and before creating any other notes in the brain (`git clean` deletes every untracked file in its scope), scoped to the brain folder:

```bash
git -C "<brain>" restore -- .
git -C "<brain>" clean -nd -- .    # preview what would be deleted
git -C "<brain>" clean -fd -- .
```

Show the preview to the user before the final command. This reverts pages, indexes, log, ledger and any uncommitted snapshots, but not the git-excluded batch queue. Then delete `<brain>/.okf-ingest/batches.json` and run `snapshot` again on the same source: everything not ingested in the committed ledger is queued again.

## Re-runs

Run `snapshot` again on the same source with the same `--name`. Unchanged sources are skipped; changed ones are re-snapshotted, their previous ingested version is kept under `.okf-ingest/previous/`, and they are queued with status `changed`. Origins no longer found are reported and kept; nothing is deleted.

## Common mistakes

- Writing the summary page under a name of your own: `done` refuses. Use the `summary page` path from `next`.
- Listing the source summary itself, or a page twice, under `## Pages updated`: the log count is wrong.
- Citation or `sources` links with raw spaces: percent-encode them (`%20`), or the link does not resolve.
- Re-snapshotting a source under a different name: every note is queued again as new.
- Stub pages: a person, project or area page for a bare name, or headings left empty or filled with placeholder text. See "When a page earns its place" and "Sections" in `references/page-types.md`.
- Turning a 1:1 or meeting note into its own wiki page: it is a source; its content goes to people, project, area, concept and decision pages.
