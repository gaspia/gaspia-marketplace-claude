# OKF Vault Migration Skill: Design

Date: 2026-11-16
Status: Draft, pending approval

## Intent

Paulo is building a portable, AI-assisted second brain with Claude Code, Obsidian, and the Open Knowledge Format (OKF, spec at https://okf.md/spec/, v0.1 plus v0.2 additions). The long-term goals are: knowledge as linked markdown concepts with metadata and indexes, Claude capturing and maintaining it, AI querying the graph, and everything staying portable, versioned, and tool-independent.

The first step is a skill that migrates any existing Obsidian vault into an OKF-conformant bundle, so later skills (concept extraction, enrichment, querying) can rely on a uniform format.

## Goals

- Turn an existing Obsidian vault into an OKF-conformant bundle (vault root = bundle root).
- Format change only: every note keeps its location, filename, and content meaning.
- Every non-excluded `.md` note gets parseable YAML frontmatter with a non-empty `type`.
- Every Obsidian wikilink and wiki-embed in note bodies becomes a standard markdown link with a relative path.
- Reserved files (`index.md`, `log.md`) follow the OKF structure. An `index.md` exists in every directory that contains notes.
- The migration is reviewable before it is applied, deterministic, and idempotent (a second run on a migrated vault changes nothing).
- The mechanical part runs without Claude (portability goal).

## Non-goals

- Extracting concepts out of existing notes (a later, separate skill).
- Writing or inventing `description` values, summaries, or other content.
- Moving, renaming, or restructuring notes, except where a reserved filename forces a rename (see below).
- Converting inline `#tags` in bodies into frontmatter `tags`.
- Adding OKF v0.2 trust fields (`generated`, `verified`, `sources`, `stale_after`) to notes.
- Editing `.obsidian/` config.
- Touching non-markdown files (`.canvas`, images, PDFs, notebooks).

## Anti-goals (would be a failure even if the output validates)

- Lost or reordered existing frontmatter keys, or changed values (for example Notion `notion-id`).
- Changed note body text other than link syntax (including unlinking date wikilinks to plain text).
- Links that worked in Obsidian before and do not resolve after.
- Changes written without the user having seen the plan.
- An un-revertable state: the user must always be able to `git restore` back.

## Constraints

- Target vault today: `/Users/paulo.alves/Documents/Obsidian Vault` (git repo, ~106 notes, ~248 wikilinks, many `![[Pasted image ...]]` embeds, Notion-imported frontmatter, a nested cloned git repo at `Training/HuggingFace_NLP/notebooks/`, a hand-curated OKF-ish subtree at `second-brain/`). The skill must work on any vault, not just this one.
- `.obsidian/` files change constantly (Obsidian + `github-sync` plugin). Do not edit them, and do not count them when checking that the tree is clean.
- The vault's `second-brain/CLAUDE.md` defines conventions: plain punctuation, no em dashes; `concept` = one idea per file.
- Plugin lives in this marketplace repo (`gaspia-marketplace-claude`).

## Approaches considered

1. **Instructions-only skill.** Claude reads and edits each note following SKILL.md rules. Fast to write; non-deterministic over 100+ notes and 248 links, risky edits, dry-run is only a description. Rejected.
2. **Skill + bundled deterministic script (chosen).** A Python script does scan, plan, apply, validate. Claude does the judgment: proposes type rules, classifies leftovers, walks the user through the plan. Exact, repeatable, reviewable, and the script runs without Claude.
3. **Standalone CLI tool + thin skill.** Most portable, but packaging and release overhead with no current user outside this plugin. Can evolve from 2 later without a rewrite.

Paulo chose approach 2.

## Decisions made with Paulo

- Use the OKF-compliant format strictly, including for his existing `second-brain/index.md` and `log.md` (no frontmatter in `index.md`; `log.md` grouped under `## YYYY-MM-DD` headings).
- Migration is format-only (OKF-valid); concept extraction is out of scope.
- Types: rule-based (folders, tags, existing frontmatter) confirmed by the user, with Claude classifying notes no rule covers and the user reviewing those. `concept` is one of the types, meaning a single reusable idea as defined in `second-brain/CLAUDE.md`.
- Links: relative markdown links (not `/`-prefixed bundle-absolute), because Obsidian resolves relative links reliably.
- Re-runnable: the same skill is run again as the vault grows. Later runs bring new or changed notes up to OKF (frontmatter, links), keep indexes current, and log what changed. See "Re-runs" below.

## Design

### Packaging

- New plugin `plugins/second-brain/` in this marketplace, registered in `.claude-plugin/marketplace.json`. It is the home for all future second-brain skills. (Designer's call: it replaces the placeholder `hello` plugin, which is removed.)
- Skill: `plugins/second-brain/skills/okf-migrate/SKILL.md`.
- Script: `plugins/second-brain/skills/okf-migrate/scripts/okf_migrate.py`, a single file with PEP 723 inline metadata, run via `uv run`. Dependency: `ruamel.yaml` (round-trip preserves key order, quoting, and comments; PyYAML does not).
- Tests: `plugins/second-brain/tests/` with pytest and fixture vaults.

### Workflow (what the skill walks Claude and the user through)

1. **Preflight.** Ask for the vault path. Require a git repo whose working tree is clean, ignoring `.obsidian/`. If not clean, stop and tell the user to commit or stash. Tell the user to pause Obsidian sync plugins (for example `github-sync`) during migration.
2. **Scan.** `okf_migrate.py scan <vault>` writes `inventory.json` to the workdir: every note, its frontmatter (or parse error), inline tags, links found, link resolution result, reserved-name files, excluded paths. Prints a summary.
3. **Type rules.** If `<vault>/.okf/types.yaml` exists (a re-run), use it and only show the user new unmatched notes. Otherwise Claude reads the summary and drafts it (format below), shows it to the user, and adjusts until confirmed.
4. **Plan.** `okf_migrate.py plan <vault> --types types.yaml` writes `plan.json` (machine) and `plan.md` (human): per-file frontmatter additions, every link rewrite (before -> after), reserved-file handling, renames, indexes to create, and unresolved items. Notes no rule matched are listed as `unclassified`.
5. **Classify leftovers.** Claude reads each unclassified note and proposes a type with a one-line reason, written as overrides in `types.yaml`. The user reviews. Re-run `plan`.
6. **Review.** Claude summarizes `plan.md` (counts, risky items: ambiguous links, renames, parse errors). The user approves. Nothing is written to notes before this.
7. **Apply.** `okf_migrate.py apply <vault> --plan plan.json`. Refuses if any planned file changed since `plan` (content hash check).
8. **Validate.** `okf_migrate.py validate <vault>` checks OKF conformance and that every converted link resolves to an existing file (or was already unresolved before). Non-zero exit on failure.
9. **Hand-off.** Show `git diff --stat`. The skill does not commit; it suggests a commit message. Tell the user to set Obsidian's "New link format" to "Relative path to file" and keep "Use [[Wikilinks]]" off, so new links stay OKF-compatible.

Workdir: `<vault>/.okf-migrate/`, added to `.git/info/exclude` (local, never committed) so the vault's `.gitignore` is not touched. It holds throwaway files (`inventory.json`, `plan.json`, `plan.md`).

Persistent config: `<vault>/.okf/types.yaml`, committed with the vault, so type rules and per-file overrides survive between runs and stay portable (designer's call). `.okf/` is excluded from the bundle and Obsidian ignores dot-folders.

### Scope and exclusions

Always excluded: `.obsidian/`, `.git/`, `.trash/`, the workdir, any directory containing its own `.git` (nested repos). Default excluded files: `CLAUDE.md`, `AGENTS.md` (tool instructions, not knowledge; designer's call). The user can add glob excludes in `types.yaml`. Excluded paths are listed in the plan and in validate output as "outside bundle".

### Type rules file (`types.yaml`)

```yaml
default: note              # used when nothing else matches, only if the user accepts it
exclude: ["Templates/**"]  # extra globs
rules:                     # first match wins, evaluated in order
  - folder: "second-brain/concepts/**"
    type: concept
  - tag: meeting           # frontmatter tags or inline #tag
    type: meeting
  - field: { name: Type, value: "Meeting" }
    type: meeting
  - filename: "^o2o "      # regex on basename
    type: one-on-one
overrides:                 # per-file, from Claude classification or the user
  "Work/AI/Agentic/AI Show & Tell.md": meeting
```

Type names assigned by the tool: lowercase, kebab-case. The OKF example types (`concept`, `howto`, `reference`, `decision`, `metric`) are suggested where they fit. Other names (`meeting`, `one-on-one`, `training`, `initiative`) are allowed (OKF does not register types).

Existing `type` values (designer's call): a note that already has any non-empty `type` string keeps it exactly as written, whatever its case or spacing (for example `Meeting Notes`). Rules do not override it, and `validate` accepts it, because OKF only requires a non-empty string and changing values is an anti-goal. The plan lists non-kebab-case existing types as informational, so the user can clean them up by hand if they want. An empty `type:` (or `null`) counts as missing and is filled from the rules.

### Frontmatter changes

- No frontmatter: add a block with `type`, `title` (first `# H1` if present, else the filename without `.md`), and `timestamp`.
- Existing frontmatter: preserve every key, value, order, and comment. Add only the missing `type`, `title`, `timestamp`. Never remove keys. A capitalized `Type` key is left as is, and lowercase `type` is added.
- `timestamp`: the date of the last git commit touching the file, ISO 8601 with offset. Fallback to file mtime for untracked files. Existing values are kept.
- Frontmatter that fails to parse: listed in the plan as blocking. Apply refuses until it is fixed. Claude may propose a fix; the user approves it.
- Empty files get frontmatter only.

### Link conversion

Applies to note bodies only. Skips fenced code blocks, inline code, and frontmatter (frontmatter wikilinks are reported, not changed).

| Obsidian | Result |
|---|---|
| `[[Note]]` | `[Note](rel/path/Note.md)` |
| `[[Note\|alias]]` | `[alias](rel/path/Note.md)` |
| `[[Note#Heading]]` | `[Note > Heading](rel/path/Note.md#Heading)` |
| `[[#Heading]]` | `[Heading](#Heading)` |
| `[[Note#^block]]` | `[Note](rel/path/Note.md#^block)` |
| `![[img.png]]` | `![img.png](rel/path/img.png)` |
| `![[img.png\|300]]` | `![img.png\|300](rel/path/img.png)` |
| `![[Note]]` | `![Note](rel/path/Note.md)` |

- Resolution follows Obsidian: a target containing `/` is a vault path. Otherwise match by filename (with or without extension). With several matches, prefer the one Obsidian would pick (shortest path, then closest folder); report it as ambiguous in the plan.
- **Date wikilinks** (Paulo's decision): a wikilink whose target is an ISO date (`YYYY-MM-DD`, optionally followed by a time) and that does not resolve to an existing note is a date, not a link. It becomes plain text: the alias if there is one, else the target. `[[2026-11-09]]` becomes `2026-11-09`, and `[[2026-11-09|2026-11-09 16:04]]` becomes `2026-11-09 16:04`. Embeds (`![[2026-11-09]]`) are not affected. If a note with that name exists (for example a real daily note), it is converted as a normal link, so no working link is lost. The plan lists these under "dates unlinked".
- Other unresolved targets become links to the path Obsidian would create the note at (vault root unless `.obsidian/app.json` sets `newFileFolderPath`). OKF allows broken links, and they are listed in the plan.
- Paths are relative to the linking note, encoded the way Obsidian writes markdown links (spaces as `%20`; also encode `(`, `)`, `#`, `%`, `^` in the path part). Heading anchors use Obsidian's form (`%20` for spaces), not GitHub slugs.
- Existing standard markdown links are left alone.

### Reserved files

- **Existing `index.md` that acts as an index** (for example `second-brain/concepts/index.md`), designer's call: strip frontmatter, convert wikilinks like any note, and keep every other line verbatim (prose, headings, entry wording and bullet style). OKF's index structure is loose (headings grouping link lists), so `validate` checks only "no frontmatter except root" for indexes. Notes in that folder that the index does not link to are appended under a `# Unlisted` section (one entry each, same format as generated indexes). No `# Subfolders` section is added to curated indexes unless subfolders are unlinked, in which case they go in `# Unlisted` too.
- **Existing `log.md` that acts as a log** (for example `second-brain/log.md`), designer's call: strip frontmatter and keep the `# ...` title. A top-level list item that starts with an ISO date (`- 2026-11-05: ...`, `- 2026-11-05 ...`) becomes an entry under a `## 2026-11-05` heading with the date prefix removed; existing `## YYYY-MM-DD` sections are kept. Child items and continuation lines move with their parent item. Date groups are ordered newest first; entries within a date keep their original order. Anything that cannot be tied to a date (prose, undated items) is kept verbatim, in original order, under a final `## Undated` section. No text is dropped; a test asserts that the multiset of non-heading lines is preserved.
- **An existing `index.md` / `log.md` that is really a note** (the script flags files with no list of links or dated entries; Claude confirms with the user): rename to `<folder-name>-index.md` / `<folder-name>-log.md` (kebab-case) and update every link to it.
- **Generated `index.md`**: in every included directory that contains notes or included subdirectories and has no index. Format: `# <Folder name>`, one `* [Title](file.md) - description` entry per note (description omitted if absent), sorted by title, then a `# Subfolders` section linking each subfolder's `index.md`.
- **Root `index.md`**: the only one with frontmatter, containing exactly `okf_version: "0.2"`.
- **Root `log.md`**: created or appended, with one entry under today's date: `* **Init**: Migrated vault to OKF (N notes typed, M links converted, K indexes created).`

### Re-runs

The same `scan` / `plan` / `apply` / `validate` flow runs on a vault that was migrated before, after new notes are added in Obsidian.

- **Notes:** any note without frontmatter, without `type`, or with wikilinks is handled exactly as on the first run. Notes already conformant are untouched (no `timestamp` refresh).
- **Generated indexes** carry a marker as the last line: `<!-- okf-migrate:generated -->`. On a re-run they are regenerated from scratch, so new notes appear and deleted notes disappear. The plan shows them only if their content would change.
- **Curated indexes** (no marker): kept as described above. New unlisted notes are appended to the existing `# Unlisted` section; a second `# Unlisted` section is never created. Links to deleted notes are left (OKF allows broken links) and reported in the plan.
- **Root `log.md`:** the first run adds an `* **Init**: ...` entry. Later runs that change anything add `* **Update**: Brought N notes to OKF (M links converted, K indexes updated).` under today's date, merging into an existing heading for today. A run with an empty plan writes nothing.
- **Root `index.md`:** `okf_version` is set once and kept.
- **Renamed reserved-name notes:** only happens to new notes, same rule as the first run.

### Idempotency and safety

- `scan` + `plan` on an already-migrated vault with no new notes produce an empty plan. Tested. A run after adding notes touches only those notes, indexes whose content changes, and the root log. Tested.
- `apply` writes files atomically (temp file + rename) and only files in the plan.
- Undo is `git restore . && git clean -fd` (excluding `.obsidian/`). The skill states this before apply.

### Testing

Fixture vaults covering: no frontmatter, Notion frontmatter with odd keys and comments, invalid YAML, every link form in the table, ambiguous and unresolved targets, date wikilinks with and without alias (and one that resolves to a real daily note), links inside code blocks, spaces and `&` in paths, nested git repo, reserved files as index and as a note, an empty file. Golden-output tests for `plan` and `apply`. An idempotency test. A re-run test (migrate, add and delete notes, migrate again; check generated and curated indexes and the log). A validate test against conformance rules.

## Left to the builder

- Internal structure of `okf_migrate.py`, CLI flag names beyond those above, and JSON schemas of `inventory.json` / `plan.json`.
- Exact heuristics for "this reserved-name file is really a note".
- Wording and layout of `plan.md` and the SKILL.md prose (keep SKILL.md short; put detail in a `references/` file if needed).
- How many fixture vaults and how they are organized.
