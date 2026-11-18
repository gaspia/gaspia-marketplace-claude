# gaspia-marketplace-claude

## Usage

```
/plugin marketplace add gaspia/gaspia-marketplace-claude
/plugin install second-brain@gaspia-marketplace
```

## Plugins

### second-brain

Tools for an Obsidian second brain kept in the [Open Knowledge Format](https://okf.md/spec/) (OKF). Its first skill, `okf-migrate`, converts a vault (a clean git repo) into an OKF bundle and can be re-run as the vault grows: every note gets `type`, `title` and `timestamp` frontmatter (existing keys are kept as written), wikilinks and embeds become relative markdown links, every folder gets an `index.md`, and the root gets an `okf_version` index and a `log.md` entry. A bundled Python script (`uv run plugins/second-brain/scripts/okf_migrate.py scan|plan|apply|validate <vault>`) does the mechanical work, so it also runs without Claude. Type rules live in `<vault>/.okf/types.yaml`. Nothing is written before you review the plan, and the tool never commits and never deletes notes (a reserved-name rename moves a file).

Its second skill, `ingest`, distills notes from any Obsidian vault, folder or single note into a second brain in the style of Karpathy's LLM wiki. `uv run plugins/second-brain/scripts/okf_brain.py init|snapshot|next|similar|done|validate <brain>` does the bookkeeping: `snapshot` copies each source byte for byte into the brain's `raw/` folder (immutable, with its attachments), records hashes in a ledger so re-runs skip unchanged notes and process only what changed, and queues batches; `done` regenerates indexes and appends to `log.md`; `validate` checks OKF conformance, citations and snapshot integrity. Claude reads each batch, drafts a review (takeaways, pages to create or update, possible duplicates found with `similar`, inferred items) and then edits concept, project, area, person, decision and source summary pages, citing the raw snapshots. Settings live in `<brain>/.okf/brain.yaml`: `review.mode` (`source`, `batch` (default) or `autonomous`; duplicate questions are always asked), `review.batch_size` (default 10), and `people.personal` / `people.performance` (default on), which allow `## Personal` and `## Performance` sections on person pages. The brain must be in git; the skill never commits and never edits a source vault. Tests: `cd plugins/second-brain && uv run pytest`.

Its third skill, `concept`, creates or enriches one concept page, also as the command `/second-brain:new-concept <name>`. It checks for duplicates with `similar`, gathers what the brain already has, and searches outside sources (those set in the brain's `CLAUDE.md` under `## Concept sources`, other connected sources, the web, or any the user names) only when the brain lacks a cited definition or enough facts. It recommends which sources to ingest or skip, shows one review draft per `review.mode`, ingests approved sources through the `ingest` workflow, writes the concept with citations, and links it from the concepts index, related concepts and the projects and areas that mention it.

`ingest` also extracts action items: commitments with a stated actor, from sources dated within `tasks.max_age_days` (default 30), limited to the user's own follow-ups (team backlog work stays in Jira). Each one cites a verbatim quote from its snapshot and lands in `<brain>/.okf/tasks.json`, a store only the script writes. `okf_brain.py today <brain>` lists overdue, due today, waiting on others, stale (`tasks.stale_days`, default 14) and new tasks; `task <brain> done|defer|delegate|drop <id>` changes one; `task-similar` finds likely duplicates; `validate` checks the store. Commands: `/second-brain:brain-init`, `/second-brain:brain-ingest <source>`, `/second-brain:brain-today` and `/second-brain:brain-task <action> <id>`. They read the brain path from the `SECOND_BRAIN` environment variable (set it under `env` in your Claude Code settings) and ask for it when unset.

## Development

Add new plugins under `plugins/<name>/` and register them in `.claude-plugin/marketplace.json`.
