# gaspia-marketplace-claude

## Usage

```
/plugin marketplace add gaspia/gaspia-marketplace-claude
/plugin install second-brain@gaspia-marketplace
```

## Plugins

### second-brain

Tools for an Obsidian second brain kept in the [Open Knowledge Format](https://okf.md/spec/) (OKF). Its first skill, `okf-migrate`, converts a vault (a clean git repo) into an OKF bundle and can be re-run as the vault grows: every note gets `type`, `title` and `timestamp` frontmatter (existing keys are kept as written), wikilinks and embeds become relative markdown links, every folder gets an `index.md`, and the root gets an `okf_version` index and a `log.md` entry. A bundled Python script (`uv run plugins/second-brain/scripts/okf_migrate.py scan|plan|apply|validate <vault>`) does the mechanical work, so it also runs without Claude. Type rules live in `<vault>/.okf/types.yaml`. Nothing is written before you review the plan, and the tool never commits and never deletes notes (a reserved-name rename moves a file). Tests: `cd plugins/second-brain && uv run pytest`.

## Development

Add new plugins under `plugins/<name>/` and register them in `.claude-plugin/marketplace.json`.
