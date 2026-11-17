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

## Development

Add new plugins under `plugins/<name>/` and register them in `.claude-plugin/marketplace.json`.
