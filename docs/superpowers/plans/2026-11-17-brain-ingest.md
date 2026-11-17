# Second Brain Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An `ingest` skill in the `second-brain` plugin that snapshots sources from any Obsidian vault or folder into a designated second brain and drives Claude through Karpathy-style ingest (summary pages, typed wiki pages, citations, index, log), with deterministic bookkeeping in `okf_brain.py`.

**Architecture:** The shared `okfmig` package moves to plugin level. New modules `brain.py` (settings, init, schema), `snapshot.py` (ledger, snapshots, batches), `ingest.py` (`similar`, batch completion), `brainvalidate.py` and `braincli.py`. Batch completion reuses the `okf-migrate` planner (with its root-log line switched off) to normalize pages and regenerate indexes, then appends `**Ingest**` log lines. Claude writes pages between `next` and `done`, guided by `SKILL.md`.

**Tech Stack:** Python 3.14, `uv`, `ruamel.yaml`, pytest, Ruff + Black (line length 120, py314), git CLI.

**Spec:** `docs/superpowers/specs/2026-11-17-brain-ingest-design.md` (binding). Earlier spec for reused behavior: `docs/superpowers/specs/2026-11-16-okf-vault-migration-design.md`.

## Global Constraints

- Brain root is its own OKF bundle root. Wiki folders and types: `concepts`→`concept`, `projects`→`project`, `areas`→`area`, `people`→`person`, `decisions`→`decision`, `sources`→`source`.
- `raw/` is outside the bundle (`exclude: ["raw/**"]` in `.okf/types.yaml`). Snapshots are byte-identical copies at `raw/<source-name>/<path relative to the source vault root>`; nothing ever edits them after writing.
- Ledger `.okf/sources.json`, keyed by origin `"<source-name>:<relative path>"`, fields: `raw_path`, `origin_sha256`, `ingested_sha256` (null until ingested), `summary_page`, `first_seen`, `last_snapshot`.
- Settings `.okf/brain.yaml` defaults: `review.mode: batch`, `review.batch_size: 10`, `people.personal: true`, `people.performance: true`. Batch size: `source` → 1, `batch`/`autonomous` → `batch_size`; single-note ingest → 1.
- Workdir `<brain>/.okf-ingest/` (git-excluded via `.git/info/exclude`): `batches.json`, `previous/<raw path>`.
- Summary page path: `sources/<YYYY-MM-DD>-<kebab slug>.md`, assigned once at first snapshot, never changed; `-2`, `-3` on collision.
- Log line per ingested source: `* **Ingest**: [<source title>](sources/<file>.md) - <N> pages updated`, N = number of links in the summary page's `## Pages updated` section.
- Schema section in `CLAUDE.md` between `<!-- okf-brain:schema -->` and `<!-- /okf-brain:schema -->`; text outside the markers is never touched.
- Exit codes: 0 ok, 1 validation failure or refused, 2 usage or preflight error. The tool never commits, never edits `.obsidian/`, never writes outside the brain.
- No em dashes in generated text or docs. Code style as `okf-migrate`: Black + Ruff, line length 120, py314, builtin generics.

## Review Focus

1. A single note inside a vault given as the source: origin and raw path are relative to the enclosing vault root (the nearest ancestor with `.obsidian/`, else the given folder), so a later folder-level ingest of the same vault sees the same origin. Test in Task 3.
2. The brain lives inside the source vault: nothing under the brain root is ever snapshotted. Test in Task 3.
3. A source embeds `![[Pasted image 1.png]]` stored in `./attachments` with spaces in names: the attachment is copied to the same relative path under `raw/<name>/`, so the embed still resolves from the snapshot. Test in Task 3.
4. Claude writes a page with a wikilink, no `timestamp`, or no `type`: `done` normalizes it through the planner (folder type rule, relative links) instead of failing later. Test in Task 4.
5. A source deleted after its snapshot: reported as missing by `snapshot`, ledger entry and snapshot kept, `validate` still passes. Test in Task 5.

---

## File Structure

```
plugins/second-brain/
  pyproject.toml                         # modify: pythonpath -> scripts
  scripts/okf_migrate.py                 # moved from skills/okf-migrate/scripts/
  scripts/okf_brain.py                   # new PEP 723 entry -> okfmig.braincli.main
  scripts/okfmig/                        # moved package (all existing modules)
  scripts/okfmig/planner.py              # modify: build_plan(..., root_log: bool = True)
  scripts/okfmig/brain.py                # settings, layout, init, schema
  scripts/okfmig/templates/schema.md     # schema text written into CLAUDE.md
  scripts/okfmig/snapshot.py             # source discovery, snapshots, ledger, batches
  scripts/okfmig/ingest.py               # similar, complete_batch
  scripts/okfmig/brainvalidate.py        # validate_brain
  scripts/okfmig/braincli.py             # CLI for okf_brain.py
  skills/okf-migrate/SKILL.md            # modify: script path
  skills/ingest/SKILL.md                 # new
  skills/ingest/references/page-types.md # per-type templates and rules
  tests/test_brain.py test_snapshot.py test_ingest.py test_brainvalidate.py test_braincli.py
README.md                                # modify: ingest skill paragraph
```

Run tests from `plugins/second-brain/` with `uv run pytest`.

---

### Task 1: Move the shared package and add the root-log switch

**Files:**
- Move: `skills/okf-migrate/scripts/okf_migrate.py` and `skills/okf-migrate/scripts/okfmig/` → `scripts/` (with `git mv`)
- Modify: `pyproject.toml` (`pythonpath = ["scripts"]`), `tests/test_cli.py` (script path), `skills/okf-migrate/SKILL.md` (command becomes `uv run <this skill's base directory>/../../scripts/okf_migrate.py ...`), `scripts/okfmig/planner.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- Produces: `build_plan(vault, cfg, timestamps, today, root_log: bool = True) -> Plan`. With `root_log=False` no root `log.md` op is emitted (other logs are still regrouped as before).

- [ ] **Step 1: Move** with `git mv`, update the three paths. Run `uv run pytest`. Expected: 200 passed, unchanged.
- [ ] **Step 2: Failing test** `test_planner.py::test_root_log_switch_off`: the existing raw-vault fixture planned with `root_log=False` has no op for `log.md` and the same other ops as with `True` minus the log op.
- [ ] **Step 3: Run** it. Expected: FAIL (unexpected keyword).
- [ ] **Step 4: Implement** the parameter (skip `_root_log()` when false).
- [ ] **Step 5: Run** full suite, ruff, black. Expected: 201 passed, clean. `claude plugin validate .` from repo root passes.
- [ ] **Step 6: Commit** `refactor(second-brain): share okfmig at plugin level, add root_log switch`

---

### Task 2: Brain settings, layout and init

**Files:**
- Create: `scripts/okfmig/brain.py`, `scripts/okfmig/templates/schema.md`, `tests/test_brain.py`

**Interfaces:**
- Consumes: `typerules.load`, `gitutil.ensure_info_exclude`, `gitutil.is_repo`.
- Produces:
  - `WIKI_FOLDERS: dict[str, str]` (folder → type, per Global Constraints); `RAW = "raw"`; `WORKDIR = ".okf-ingest"`.
  - `@dataclass(frozen=True) class BrainSettings: review_mode: Literal["source", "batch", "autonomous"] = "batch"; batch_size: int = 10; people_personal: bool = True; people_performance: bool = True` with `effective_batch_size(self, single_note: bool) -> int`.
  - `load_settings(brain: Path) -> BrainSettings` (missing file or keys → defaults; unknown mode, non-positive size, non-bool flags → `ValueError` naming the key and file).
  - `init_brain(brain: Path) -> list[str]`: returns created or changed paths. Creates missing wiki folders, `raw/`, `.okf/brain.yaml` (defaults, commented like the spec example) only if absent, merges `.okf/types.yaml` (adds missing folder rules `{folder: "<f>/**", type: <t>}` and `raw/**` to `exclude`, keeps existing rules and order; written with ruamel round-trip), writes or replaces the schema block in `CLAUDE.md` between the markers (appends the block if markers are absent; creates the file if missing), calls `ensure_info_exclude(<git top-level>, "<brain rel>/.okf-ingest/")` when inside a git repo. Idempotent: second call returns `[]`.
  - `outer_vault(brain: Path) -> Path | None`: nearest ancestor (not the brain) containing `.obsidian/`, for the "brain inside another vault" warning.
  - `templates/schema.md` content: the three Karpathy layers, the page types and folders, frontmatter (`type`, `title`, `description`, `tags`, `timestamp`, `sources` list of `{resource, title}`, `status: inferred`), citations rule (`# Citations` numbered, claims cite `[n]`), relative markdown links only, never invent facts, prefer editing to duplicating, ask when unsure, personal/performance sections only when enabled in `.okf/brain.yaml`, raw/ is read-only, plain punctuation.

- [ ] **Step 1: Failing tests** in `test_brain.py`:
  - `test_settings_defaults_and_overrides`: missing file → defaults; `{review: {mode: source}}` → mode source, `effective_batch_size(False) == 1`; `batch_size: 5` with mode autonomous → 5; any mode with `single_note=True` → 1.
  - `test_settings_invalid`: `mode: weekly` → ValueError mentioning `review.mode`; `batch_size: 0` → ValueError.
  - `test_init_creates_layout`: empty dir → all six wiki folders, `raw/`, `.okf/brain.yaml`, `.okf/types.yaml` whose `load()` has 6 folder rules and `exclude == ["raw/**"]`, `CLAUDE.md` containing both markers.
  - `test_init_merges_existing`: existing `CLAUDE.md` with `# Mine\nkeep me\n` and existing `types.yaml` with a custom rule → both kept verbatim; schema block appended after `keep me`; re-run returns `[]` and files are byte-identical.
  - `test_init_replaces_schema_block`: CLAUDE.md with markers around old text → only the text between markers changes.
  - `test_outer_vault`: brain at `V/second-brain` with `V/.obsidian/` → `V`; standalone → None.
- [ ] **Step 2: Run** `uv run pytest tests/test_brain.py -v`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** tests, full suite, ruff, black. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ingest): brain settings, layout and init`

---

### Task 3: Snapshots, ledger and batches

**Files:**
- Create: `scripts/okfmig/snapshot.py`, `tests/test_snapshot.py`

**Interfaces:**
- Consumes: `vault.discover` (with `ignored`), `links.Resolver`, `mdtext.code_ranges`, `frontmatter.parse`, `planner.kebab`, `planner.sha256`-style hashing over raw bytes, `brain.BrainSettings`.
- Produces:
  - `@dataclass class LedgerEntry: raw_path: str; origin_sha256: str; ingested_sha256: str | None; summary_page: str; first_seen: str; last_snapshot: str`
  - `load_ledger(brain: Path) -> dict[str, LedgerEntry]`, `save_ledger(brain: Path, ledger: dict[str, LedgerEntry]) -> None` (JSON, sorted keys, indent 2, `ensure_ascii=False`).
  - `@dataclass class BatchItem: origin: str; raw_path: str; status: Literal["new", "changed"]; previous: str | None; summary_page: str`
  - `@dataclass class Batch: id: str; items: list[BatchItem]` (ids `b001`, `b002`, ... continuing after existing ids).
  - `load_batches(brain) -> list[Batch]`, `save_batches(brain, batches) -> None` (`<brain>/.okf-ingest/batches.json`).
  - `@dataclass class SnapshotResult: name: str; new: list[str]; changed: list[str]; unchanged: list[str]; missing: list[str]; attachments: int; batches: list[str]`
  - `source_root(source: Path) -> Path`: nearest ancestor-or-self directory containing `.obsidian/`; else `source` if a directory, else its parent.
  - `take_snapshot(brain: Path, source: Path, name: str | None, settings: BrainSettings, today: date) -> SnapshotResult`:
    - `name` default: `kebab(source_root(source).name)`.
    - Sources: if `source` is a file, just that note (must be `.md`); else every note and reserved file under it from `discover(source_root, excludes, ignored)` whose path is under `source`. Excludes: everything under the brain root when the brain is inside the source root; `raw/` of any brain is never a source.
    - Per source: hash raw bytes; ledger lookup by origin. No entry → `new`. Hash equals the entry's `origin_sha256` (the current snapshot) → `unchanged`. Otherwise → `changed`: copy the existing snapshot to `.okf-ingest/previous/<raw_path>` (only if no previous copy is already pending, so the diff stays against the last ingested version), overwrite the snapshot, update `origin_sha256`. Snapshot bytes are copied exactly.
    - Attachments: for each embed outside code (`![[x]]`, `![...](x)` local), resolve against the source vault (`Resolver(vault.files, ...)` for wikilinks; relative path for markdown) and copy the file to `raw/<name>/<same relative path>` if missing or different. Count copies.
    - Summary page for new origins: date = frontmatter `date` or `created` (first `YYYY-MM-DD` in the value), else a leading `YYYY-MM-DD` in the filename, else `today`; slug = `kebab(stem)` after removing a leading `YYYY-MM-DD` and separators (empty → `note`); path `sources/<date>-<slug>.md`, adding `-2`, `-3` if taken in the ledger or on disk.
    - Missing: ledger origins with this `name` under the source path that no longer exist.
    - Batches: new + changed items, in path order, chunked by `settings.effective_batch_size(single_note=source.is_file())`, appended after existing pending batches; an origin already in a pending batch is not added twice.
    - Saves ledger and batches. Never writes outside the brain.

- [ ] **Step 1: Failing tests** (`make_vault`/`git_vault` for a source vault with `.obsidian/`, a separate brain dir after `init_brain`, fixed `today`):
  - `test_first_snapshot_copies_bytes`: two notes (one CRLF, one with `[[wikilinks]]` and invalid frontmatter) and an `index.md` → snapshots byte-identical at `raw/<name>/...`; ledger has 3 entries with `ingested_sha256 None`; one batch of 3 (batch mode, size 10).
  - `test_unchanged_skipped_and_changed_keeps_previous`: second run → all unchanged, no new batch (still pending ones untouched); then mark one ingested in the ledger, modify that source → `changed`, previous copy at `.okf-ingest/previous/<raw_path>` equals the old snapshot.
  - `test_summary_page_assignment`: `Onboarding.md` with frontmatter `created: 2026-10-21T10:00` → `sources/2026-10-21-onboarding.md`; `2026-11-09 Sync.md` → `sources/2026-11-09-sync.md`; `2026-11-09.md` → `sources/2026-11-09-note.md`; no date → `today`; two notes with the same date and slug in different folders → second gets `-2`.
  - `test_single_note_source_relative_to_vault` (Review Focus 1): source = `V/Work/a.md` → origin `<name>:Work/a.md`, raw `raw/<name>/Work/a.md`, batch of 1.
  - `test_brain_inside_source_never_snapshotted` (Review Focus 2): brain at `V/second-brain`, source `V` → no origin under `second-brain/`.
  - `test_attachments_copied` (Review Focus 3): `Work/Meet.md` embeds `![[Pasted image 1.png]]` stored at `Work/attachments/Pasted image 1.png` → copied to `raw/<name>/Work/attachments/Pasted image 1.png`, `attachments == 1`; second run → 0.
  - `test_batches_by_mode`: 12 new notes: batch mode size 10 → 2 batches (10, 2); source mode → 12 batches; autonomous size 5 → 3 batches.
  - `test_missing_origin_reported`: delete a source after snapshot → in `missing`, ledger entry and snapshot kept.
- [ ] **Step 2: Run** `uv run pytest tests/test_snapshot.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** tests, full suite, ruff, black. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ingest): snapshots, ledger and batches`

---

### Task 4: Similar pages and batch completion

**Files:**
- Create: `scripts/okfmig/ingest.py`, `tests/test_ingest.py`

**Interfaces:**
- Consumes: `snapshot.load_ledger/save_ledger/load_batches/save_batches`, `brain.WIKI_FOLDERS`, `vault.discover`, `frontmatter.parse`, `typerules.load`, `planner.build_plan(..., root_log=False)`, `apply.apply_plan`, `reserved.add_log_entry`, `links.linked_paths`, `gitutil.last_commit_times` (CalledProcessError → `{}`), `gitutil.ignored_paths`.
- Produces:
  - `similar_pages(brain: Path, title: str) -> list[tuple[str, str, float]]` → `(path, title, score)` for wiki pages (all `WIKI_FOLDERS`) whose stem, frontmatter `title` or any `aliases` entry matches: case-insensitive, `difflib.SequenceMatcher` ratio ≥ 0.75 against the kebab or plain form, or one contains the other; sorted by score descending then path.
  - `class BatchError(Exception)`.
  - `pages_updated(summary_text: str, summary_path: PurePosixPath) -> int`: number of distinct local links under the `## Pages updated` heading (until the next heading).
  - `complete_batch(brain: Path, batch_id: str, today: date) -> list[str]` returns the log lines written. Steps: unknown or already-completed id → `BatchError`; any item whose `summary_page` does not exist → `BatchError` naming it (nothing written); build plan over the brain with `typerules.load(<brain>/.okf/types.yaml)` and `root_log=False`, then `apply_plan` (blocking → `BatchError` listing them; nothing else written); set each item's `ingested_sha256 = origin_sha256` and save ledger; append one log line per item to `<brain>/log.md` via `add_log_entry` (title = summary page `title`, else stem); remove the batch from `batches.json`; delete its `previous/` copies.

- [ ] **Step 1: Failing tests** (brain after `init_brain` + `take_snapshot` from a source vault fixture; tests write the summary and wiki pages directly, standing in for Claude):
  - `test_similar_pages`: pages `concepts/embeddings.md` (title Embeddings, aliases `[vector embeddings]`) and `people/orlando-ramos.md` → `similar("Embedding")` returns embeddings first; `similar("Vector Embeddings")` hits via alias; `similar("Orlando")` hits by substring; `similar("Kubernetes")` → `[]`.
  - `test_pages_updated_count`: summary with `## Pages updated` listing 3 links (one duplicated) then `## Other` with a link → 3.
  - `test_complete_batch_happy_path`: write summary page and a concept page; `complete_batch` → ledger `ingested_sha256` set, batch removed, `log.md` contains `* **Ingest**: [Onboarding](sources/2026-10-21-onboarding.md) - 2 pages updated` under `## <today>`, `concepts/index.md` and `sources/index.md` generated, root `index.md` has `okf_version`.
  - `test_complete_batch_normalizes_pages` (Review Focus 4): concept page written with no frontmatter and a `[[Embeddings]]` wikilink → after `complete_batch` it has `type: concept`, a `timestamp`, and a relative markdown link.
  - `test_complete_batch_missing_summary_refused`: no summary page → `BatchError`, ledger and batches unchanged, no files written.
  - `test_complete_batch_unknown_id`.
- [ ] **Step 2: Run** `uv run pytest tests/test_ingest.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** tests, full suite, ruff, black. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ingest): similar pages and batch completion`

---

### Task 5: Brain validation, CLI and end-to-end

**Files:**
- Create: `scripts/okfmig/brainvalidate.py`, `scripts/okfmig/braincli.py`, `scripts/okf_brain.py`, `tests/test_brainvalidate.py`, `tests/test_braincli.py`

**Interfaces:**
- Consumes: `validate.validate`, `validate.Report`, everything from Tasks 2 to 4.
- Produces:
  - `validate_brain(brain: Path) -> Report`: `validate(brain, cfg, ignored=...)` plus errors for: wiki page whose `type` differs from its folder's type; `sources` entry (`resource`) or `# Citations` link pointing to a file that is not under `raw/` or does not exist; source summary page (in `sources/`) with no link into `raw/`; snapshot whose current hash differs from the ledger `origin_sha256` (message names the raw path: "raw snapshot was edited"); ingested ledger entry whose `summary_page` is missing. Warnings for `## Personal` / `## Performance` headings on person pages when the matching setting is off; for ledger origins whose snapshot file is missing.
  - `okf_brain.py` (PEP 723 header as `okf_migrate.py`) → `braincli.main(argv) -> int` with subcommands:
    - `init <brain>`: prints created/changed paths; warns (stderr, exit 0) when `outer_vault` is not None, naming the glob to exclude in the outer vault.
    - `snapshot <brain> <source> [--name NAME] [--today YYYY-MM-DD]`: preflight: brain must be inside a git repo (exit 2); if pending batches exist → print "pending batches exist; run next/done first" and exit 1; else require a clean tree via `dirty_paths` (exit 2). Prints counts, missing origins, batch ids.
    - `next <brain>`: prints the next pending batch as readable lines per item (origin, raw path, status, previous path, summary page) or `no pending batches`; exit 0.
    - `similar <brain> <title>`: prints `score  path  title` lines.
    - `done <brain> <batch-id> [--today YYYY-MM-DD]`: `complete_batch`; prints log lines; `BatchError` → exit 1 with the message.
    - `validate <brain>`: prints report; exit 1 on errors.
    - Settings errors (`ValueError`) and a missing brain dir → exit 2.

- [ ] **Step 1: Failing tests** in `test_brainvalidate.py`: a completed-batch brain validates clean; then each break gives exactly one expected error: concept page moved into `people/`; `sources` resource pointing at `raw/x/missing.md`; a raw snapshot edited; summary page deleted; `## Performance` on a person page with `performance: false` → warning only; deleted source origin (snapshot kept) → no error (Review Focus 5).
- [ ] **Step 2: Failing tests** in `test_braincli.py` (real git repos, `main([...])`): full flow init → commit → snapshot → next → write summary + page → done → validate exit 0; `snapshot` with pending batches → exit 1; dirty tree without pending batches → exit 2; `similar` output; `done` unknown id → exit 1; `uv run scripts/okf_brain.py --help` lists all six subcommands.
- [ ] **Step 3: Run** both files. Expected: FAIL.
- [ ] **Step 4: Implement** per Interfaces.
- [ ] **Step 5: Run** full suite, ruff, black. Expected: PASS.
- [ ] **Step 6: Commit** `feat(ingest): brain validation and CLI`

---

### Task 6: SKILL.md, page templates, docs and rehearsal

**Files:**
- Create: `skills/ingest/SKILL.md`, `skills/ingest/references/page-types.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the `okf_brain.py` CLI (read `braincli.py` for exact flags).

- [ ] **Step 1: Write `references/page-types.md`:** for each of concept, project, area, person, decision, source: purpose, folder, required frontmatter, section headings exactly as in the spec's Pages section, a short filled example, and the common rules (citations `[n]` with `# Citations`, `sources` frontmatter, `status: inferred` plus `(inferred)` suffix, relative links, kebab-case filenames, prefer editing, Personal/Performance sections only when enabled).
- [ ] **Step 2: Write `SKILL.md`:** frontmatter `name: ingest`, `description: Ingest notes from any Obsidian vault or folder into the second brain, Karpathy LLM-wiki style: snapshot sources, then create and update concept, project, area, person, decision and source pages with citations. Use when the user asks to ingest, import, or distill notes into their second brain.` Body (under ~120 lines): commands as `uv run <this skill's base directory>/../../scripts/okf_brain.py <subcommand> ...`; the spec's workflow steps 1 to 4, including resume, first-run path (init, `okf-migrate` on the brain, then ingest), the outer-vault exclude advice, reading `references/page-types.md` before writing pages, running `similar` before proposing any new page, the review behavior per mode (duplicates always asked), using the `previous` copy to diff changed sources, writing the summary page with `## Pages updated`, `done` then `validate` after each batch, never committing and suggesting a commit per batch, and the undo (`git restore` / `git clean -nd` preview, before creating other notes).
- [ ] **Step 3: README:** one paragraph describing the `ingest` skill and its settings.
- [ ] **Step 4: Rehearse on scratch copies only** (never the real vault): copy the main vault to the scratchpad; create a fresh brain dir in the scratchpad (`git init`), `init`, commit; `snapshot` three real notes (one meeting, one 1:1, one training note) as a folder or individually; act as Claude for one batch following SKILL.md (draft review summary, `similar` checks, write pages and summaries per `page-types.md`), `done`, `validate` exit 0, re-run `snapshot` → all unchanged. Save the review draft and resulting page list to the scratchpad and report paths. Fix any tool defect found with a regression test in the owning module.
- [ ] **Step 5: Validate plugin:** `claude plugin validate .` from repo root passes; full suite, ruff, black clean.
- [ ] **Step 6: Commit** `feat(ingest): skill workflow, page templates and docs`
