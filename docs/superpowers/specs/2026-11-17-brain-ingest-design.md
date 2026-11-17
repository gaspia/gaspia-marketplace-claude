# Second Brain Ingest Skill: Design

Date: 2026-11-17
Status: Draft, pending approval

## Intent

Paulo is building a portable, AI-maintained second brain on Obsidian and the Open Knowledge Format (OKF). The `okf-migrate` skill (already merged) makes any vault OKF-valid. This skill is the next step: it imports knowledge from any Obsidian vault or folder into a designated second brain, following Andrej Karpathy's LLM-wiki pattern as closely as possible.

Karpathy's pattern (gist `karpathy/442a6bf555914893e9891c11519de94f`, April 2026) has three layers and three operations:

- **Raw sources**: immutable; the model reads them and never edits them.
- **Wiki**: markdown pages the model owns and maintains: summaries, entity and concept pages, cross-links.
- **Schema**: one instructions file (here `CLAUDE.md`) that defines structure and workflows.
- **Ingest** (this skill): read a source, discuss takeaways, write a summary page, update the entity and concept pages it touches (often 10 to 15), update the index, append to the log.
- **Query** and **Lint**: later, separate skills.

The why: Paulo's knowledge sits in meeting notes, 1:1s, training notes and Notion imports, where ideas are buried. The second brain should hold distilled, linked, cited knowledge that both he and an AI can navigate, and it must stay portable and tool-independent.

## Goals

- Ingest a single note, a folder, or a whole vault into a designated second brain.
- Snapshot every source into the brain's `raw/` folder (immutable), with its attachments, origin and content hash.
- Maintain typed wiki pages: concepts, projects, areas, people, decisions, and one source summary page per ingested source.
- Every wiki claim cites its source; nothing is invented; inferred content is marked.
- Prefer updating existing pages over creating duplicates; ask when two items might be the same.
- Re-ingesting is incremental: unchanged sources are skipped, changed sources are re-snapshotted and only their changes processed.
- Review is configurable (per source, per batch, autonomous); per batch is the default.
- People pages can carry personal and performance sections, each switchable by a setting.
- The brain stays OKF-conformant and portable (plain files, git, relative links).

## Non-goals

- Query and Lint operations (later skills).
- Non-markdown sources (PDF, web pages, `.canvas`, notebooks).
- Editing or deleting anything in a source vault.
- Embeddings, vector search, or any service outside the filesystem and git.
- Deleting wiki pages or raw snapshots.
- Removing existing personal or performance sections when the setting is turned off (that is Lint's job).

## Anti-goals (failure even if it "works")

- A wiki claim with no citation, or a fact that is in no source and is not marked inferred.
- Duplicate pages for the same concept, person, project, area or decision.
- Any change to a snapshot in `raw/` after it is taken, or to a source outside the brain.
- Personal or performance content written while its setting is off.
- Losing content the user wrote by hand in a wiki page (Claude edits pages; it never regenerates them from scratch).
- Pages written in batch or per-source mode before the user approved that review.

## Constraints

- Today the brain is `second-brain/` inside `/Users/paulo.alves/Documents/Obsidian Vault`. It has `CLAUDE.md` (its schema), `concepts/` with one concept, and `index.md` / `log.md` in a pre-OKF format. The main vault is not migrated yet.
- The skill must work with the brain inside another vault or as its own vault.
- The existing `CLAUDE.md` rules stay in force: one idea per concept file, kebab-case filenames, definition first, link related concepts inline, never invent facts, mark unsure content `status: inferred`, prefer editing to duplicating, ask when unsure whether two notes are the same idea, plain punctuation (no em dashes).
- Reuse the `okfmig` package from `okf-migrate` (vault discovery, frontmatter, links, index and log rendering, git helpers, validate).
- Python 3.14 via `uv`, Black and Ruff at line length 120, as in `okf-migrate`.

## Approaches considered

- **A. Instructions only.** Claude does everything with file tools. It is the closest to Karpathy's own setup, but snapshotting, hashing, skip-if-unchanged, attachment copying, batching and index/log upkeep drift across 100 sources and many sessions. Rejected.
- **B. Hybrid (chosen).** A deterministic Python toolkit does the bookkeeping: snapshots, hashes, batching, indexes, log and validation. Claude does the synthesis and edits pages directly after review. This keeps bookkeeping reliable and leaves Claude free to edit pages as the wiki owner.
- **C. Structured change sets.** Claude emits a JSON change set per batch and the script applies it with plan/apply hash safety. It is the most reviewable, but it is rigid for prose edits like weaving a sentence into an existing page or merging notes. Rejected.

## Decisions made with Paulo

- Follow Karpathy's LLM-wiki pattern as closely as possible.
- Meetings, 1:1s and training notes are sources, not wiki pages. Their content flows into other pages, and each source gets a summary page.
- Page types: concept, project, area, person, decision, source (summary).
- People pages include personal and performance sections, each controlled by a setting.
- Sources are snapshotted into `raw/` (not linked in place), for portability.
- Review mode is configurable; per batch is the default.
- Approach B.

## Design

### Packaging

- New skill `plugins/second-brain/skills/ingest/` with `SKILL.md`, plus `references/` for page templates and the schema text.
- The `okfmig` package moves from `skills/okf-migrate/scripts/okfmig/` to `plugins/second-brain/scripts/okfmig/`, shared by both skills. Entry scripts: `plugins/second-brain/scripts/okf_migrate.py` (unchanged behavior) and `plugins/second-brain/scripts/okf_brain.py` (new). Both `SKILL.md` files point to them relative to the plugin. (Designer's call: one package, no duplication.)
- New modules: `okfmig/brain.py` (settings, layout, init), `okfmig/snapshot.py` (source discovery, snapshot, ledger, batches), `okfmig/brainvalidate.py` (brain-specific checks on top of `validate`). The CLI for `okf_brain.py` lives in `okfmig/braincli.py`.

### Brain layout

The brain root is its own OKF bundle root.

```
<brain>/
  CLAUDE.md              schema (excluded from the bundle, as in okf-migrate)
  index.md               root index (okf_version: "0.2")
  log.md                 OKF log; ingest entries go here
  concepts/  projects/  areas/  people/  decisions/   wiki pages
  sources/               one summary page per ingested source (type: source)
  raw/<source-name>/...  immutable snapshots plus their attachments (outside the OKF bundle)
  .okf/brain.yaml        settings
  .okf/types.yaml        type rules (reused by okf-migrate and validate)
  .okf/sources.json      source ledger
```

Every folder with pages gets a generated `index.md` (same rules as `okf-migrate`; the index and log rendering inside the `okf-migrate` planner is refactored into reusable functions as part of the package move, with no behavior change).

`raw/` is outside the OKF bundle (designer's call): it is excluded in `.okf/types.yaml`, gets no indexes, and is skipped by the inherited OKF checks. This mirrors the OKF spec's `references/` folder for cited material, and it is what lets snapshots stay byte-identical: no frontmatter is added, wikilinks and odd frontmatter in sources are kept as they are, and source files named `index.md` or `log.md` need no special handling. Provenance lives in the ledger, not in the snapshot.

### Settings (`.okf/brain.yaml`)

```yaml
review:
  mode: batch        # source | batch | autonomous
  batch_size: 10     # used when mode is batch
people:
  personal: true     # family, interests, personal context
  performance: true  # feedback, self-assessments, growth areas
```

Missing file or missing keys mean these defaults. Batch size by mode (designer's call): `source` uses 1; `batch` and `autonomous` use `batch_size`. Ingesting a single note always uses a batch of one. `done` and `validate` run after every batch in every mode. The person-page defaults are on because Paulo asked for those sections; turning one off stops new content of that kind.

### Pages

Common rules for every wiki page:

- Filename: kebab-case, in the folder for its type. The `type` matches the folder: `concept`, `project`, `area`, `person`, `decision`, `source`.
- Frontmatter: `type`, `title`, `description` (one sentence), `tags`, `timestamp`, and `sources` (OKF v0.2 field): a list of `{resource: <relative path to the raw snapshot>, title: <source title>}` for every source that contributed. `status: inferred` when the page holds inferred content, with the inferred sentences also ending in `(inferred)`.
- Links: standard relative markdown links, as `okf-migrate` produces. Related pages are linked inline.
- Citations: a final `# Citations` section with numbered entries linking the raw snapshots, as the OKF spec requires for claims from external material. Claims reference them as `[1]`.

Per type (section headings; templates go in `references/`):

- **concept**: short plain-language definition first, then detail, examples, related concepts.
- **project** (has a goal and an end): Goal, Status (with date), Timeline, People, Decisions, Open questions.
- **area** (ongoing responsibility, no end): Scope, Projects, People.
- **person**: Role, Team, Works on, Decisions. `## Personal` only if `people.personal`; `## Performance` only if `people.performance`.
- **decision** (ADR style): Decision, Date, Context, Alternatives considered, Consequences, linked project or area.
- **source** (`sources/<YYYY-MM-DD>-<slug>.md`, date from the source's date or snapshot date): Origin (link to the raw snapshot, original vault and path), Summary, Key takeaways, Pages updated (a list linking every page this source created or changed).

### Snapshots and the ledger

- `snapshot` discovers markdown sources under the given path with the same rules as `okf-migrate` (exclusions, nested repos, git-ignored paths). It also excludes the brain root itself if it sits inside the source.
- Source name: `--name`, default is the source folder's name in kebab-case. Each source keeps its relative path: `raw/<source-name>/<relative path>`.
- Attachments embedded by a source (images and other files, resolved with the `okf-migrate` resolver against the source vault) are copied to the same relative path under `raw/<source-name>/`, so embeds keep working.
- The snapshot is a byte-identical copy of the source file (designer's call; see Brain layout).
- `.okf/sources.json` records per origin (`<source-name>:<relative path>`): snapshot path, `origin_sha256`, `ingested_sha256` (the origin hash last fully ingested, or null), `summary_page` (path of its source summary page), and timestamps. Because the snapshot is byte-identical, the snapshot's own hash equals `origin_sha256`.
- `summary_page` is assigned by `snapshot` when a source is first seen (`sources/<YYYY-MM-DD>-<slug>.md`, date from the source's frontmatter `date`/`created` if present, else the snapshot date; `-2`, `-3` on collision) and never changes. A changed source therefore updates its existing summary page; re-ingest never creates a second one (designer's call).
- Classification: **new** (no ledger entry), **changed** (origin hash differs from `ingested_sha256`), **unchanged** (equal; skipped). A changed source overwrites its snapshot. Before overwriting, `snapshot` copies the previous snapshot to the workdir (`.okf-ingest/previous/<raw path>`), so Claude can diff old and new to process only what changed, whether or not the previous snapshot was committed (designer's call).
- Snapshots in `raw/` are never treated as sources.
- Origins that no longer exist are reported, never deleted.

### Workflow (what SKILL.md drives)

1. **Preflight.** Brain path and source path. The brain must be a git repo, or inside one. If `.okf-ingest/batches.json` has pending batches, this is a resume: skip snapshot and continue at step 3, with no clean-tree requirement. Otherwise the tree must be clean (same rule as `okf-migrate`) before `snapshot`. If the brain lacks `.okf/brain.yaml`, run `init`. If `validate` fails, stop and offer `okf-migrate` on the brain first. For Paulo's current `second-brain/`, the intended first run is: `init`, then `okf-migrate` on the brain (its index and log become OKF), then ingest (designer's call).
2. **Snapshot.** `okf_brain.py snapshot <brain> <source> [--name]` copies new and changed sources, updates the ledger, and writes batches to the workdir `<brain>/.okf-ingest/batches.json` (git-excluded). It prints counts.
3. **Per batch** (`okf_brain.py next <brain>` prints the next batch):
   - Claude reads `index.md`, each source snapshot (or its diff, if changed), and the existing pages it may touch.
   - Claude drafts: per source, the key takeaways; the list of pages to create or update, each with a one-line gist; possible duplicates; inferred items; any personal or performance content it will write or skip per settings.
   - Before proposing any new page, Claude runs `okf_brain.py similar <brain> "<title>"`, which lists existing pages whose filename, title, or `aliases` are close (case-insensitive, `difflib` ratio at least 0.75, plus substring matches). Any hit becomes a duplicate question (designer's call; gives autonomous mode a mechanical guard against duplicates).
   - **Review** per `review.mode`: `source` reviews each source's draft; `batch` reviews one combined draft for the batch; `autonomous` skips review. Duplicate questions are always asked, whatever the mode.
   - Claude writes the source summary pages and edits or creates wiki pages, keeping existing content.
   - `okf_brain.py done <brain> <batch-id>` marks those sources ingested, regenerates indexes, and appends one log line per source: `* **Ingest**: [<source title>](sources/<file>.md) - <N> pages updated`, where N counts the links in the summary page's Pages updated list.
   - `okf_brain.py validate <brain>`; fix any errors before the next batch.
4. **Hand-off.** Show `git diff --stat`. The skill never commits; it suggests committing after each batch so one batch can be reverted alone.

### Commands (`okf_brain.py`)

- `init <brain>`: create missing folders, `.okf/brain.yaml` with defaults, `.okf/types.yaml` with folder rules (`concepts/**` to `concept`, and so on) and `exclude: ["raw/**"]` (merged into an existing file without dropping rules), and a schema section in `CLAUDE.md` between `<!-- okf-brain:schema -->` markers (created or replaced; content outside the markers untouched). Idempotent. Does not migrate existing notes (that is `okf-migrate`).
- `snapshot <brain> <source> [--name NAME]`
- `next <brain>`: prints the next pending batch (id; per source: raw path, origin, new or changed, previous-snapshot path if changed, `summary_page`) or "no pending batches".
- `similar <brain> <title>`: prints candidate duplicate pages (see Workflow).
- `done <brain> <batch-id>`
- `validate <brain>`: everything `okf-migrate validate` checks, plus:
  - each page's `type` matches its folder;
  - each `sources` entry and each `# Citations` link points to an existing raw snapshot (these links into `raw/` are allowed even though `raw/` is outside the bundle);
  - each source summary page links an existing snapshot;
  - each snapshot's current hash equals its ledger `origin_sha256` (catches edits to `raw/`);
  - each ledger `summary_page` of an ingested source exists;
  - no `## Personal` / `## Performance` sections on person pages written while the setting is off (checked against the setting at validate time; reported as warnings, since Lint owns removal).
- Exit codes follow `okf-migrate`: 0 ok, 1 validation failure or refused, 2 usage or preflight error.

### Brain inside another vault

The current brain lives inside the main vault. Snapshots of that vault would then appear twice in Obsidian, and `okf-migrate` on the main vault would add frontmatter to them. `init` therefore warns when the brain is inside another git work tree or vault, and `SKILL.md` tells the user to add `<brain-folder>/**` to the outer vault's `.okf/types.yaml` `exclude`, or to move the brain into its own vault.

### Testing

- Script parts: unit tests per new module and an end-to-end test with real git fixtures: init, snapshot a source vault (new, then unchanged, then changed with the previous snapshot kept, then a deleted origin), source files with wikilinks, invalid frontmatter and reserved names, batching by mode and size, resume with pending batches on a dirty tree, `done` (ledger, indexes, log line counts), `similar`, validate (each brain check, including an edited snapshot).
- Moved package: the full `okf-migrate` suite still passes unchanged.
- Claude parts: a rehearsal on scratch copies: init a fresh brain, ingest three real notes from a copy of the main vault in batch mode, check citations, no duplicates, and settings respected.

## Left to the builder

- Internal structure of the new modules, ledger and batches JSON schemas, and CLI flags beyond those named.
- Exact page templates wording in `references/`, and the `CLAUDE.md` schema section text (it must restate the Karpathy layers, page types, citation rules and settings).
- Slug rules for source summary filenames beyond "date plus kebab-case title"; collision handling (append `-2`).
- SKILL.md prose (keep it short; detail goes to `references/`).
