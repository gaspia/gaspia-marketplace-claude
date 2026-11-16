# OKF Vault Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `second-brain` Claude Code plugin with an `okf-migrate` skill whose bundled Python script migrates (and re-migrates) an Obsidian vault into an OKF-conformant bundle.

**Architecture:** A small Python package (`okfmig`) behind a PEP 723 entry script does the deterministic work in four subcommands: `scan`, `plan`, `apply`, `validate`. `plan` computes the full new content of every file to touch and stores it in `plan.json`; `apply` only verifies hashes and writes what was reviewed. `SKILL.md` drives Claude through the judgment steps (type rules, classifying leftovers, plan review) between subcommands.

**Tech Stack:** Python 3.14, `uv` (script runner and dev env), `ruamel.yaml` (YAML parsing and emitting single values), pytest, Ruff + Black (line-length 120, target py314), git CLI.

**Spec:** `docs/superpowers/specs/2026-11-16-okf-vault-migration-design.md`. Read it before starting any task; this plan argues from it.

## Global Constraints

- Vault root = bundle root. All internal paths are vault-relative `PurePosixPath`.
- Always excluded dirs: `.obsidian/`, `.git/`, `.trash/`, `.okf/`, `.okf-migrate/`, and any directory (below the root) containing its own `.git`. Default excluded files: `CLAUDE.md`, `AGENTS.md` (any folder). Extra globs from `types.yaml` `exclude`.
- Workdir `<vault>/.okf-migrate/` (throwaway; ensured in `.git/info/exclude`). Persistent config `<vault>/.okf/types.yaml` (committed by the user).
- Existing frontmatter keys, values, order, comments are never changed, removed or reordered. Only missing `type`, `title`, `timestamp` are added; an empty/null `type` is filled in place.
- Existing non-empty `type` (any case or spacing) is kept; precedence: existing `type` > `overrides` > `rules` (first match) > `default`.
- Note body text changes only in link syntax (wikilinks, date unlinking, targets of renamed files).
- Links are relative, encoded like Obsidian (`%20` for space; also encode `(`, `)`, `#`, `%`, `^` in the path part); heading anchors use `%20` for spaces.
- `index.md`: no frontmatter, except the root `index.md` whose frontmatter is exactly `okf_version: "0.2"`. `log.md`: no frontmatter.
- Generated index marker, last line: `<!-- okf-migrate:generated -->`.
- Root log lines: `* **Init**: Migrated vault to OKF (N notes typed, M links converted, K indexes created).` and `* **Update**: Brought N notes to OKF (M links converted, K indexes updated).` under `## YYYY-MM-DD`, newest first. Root log title when created: `# Update Log`.
- Nothing is written to notes by `scan` or `plan`. `apply` refuses on blocking items or stale hashes. The tool never commits and never edits `.obsidian/`.
- No em dashes in any generated text (vault convention).
- Code style: Black + Ruff, line-length 120, `target-version = "py314"`, builtin generics (`list[str]`).

## Review Focus

1. CRLF line endings or a UTF-8 BOM in a note: frontmatter is still detected, and untouched lines keep their line endings. Test in Task 3.
2. Wikilinks inside Markdown tables use an escaped pipe (`[[Note\|alias]]`): treated as alias, output `[alias](Note.md)` with no stray backslash. Test in Task 5.
3. Case differences between link and file (`[[embeddings]]` vs `Embeddings.md`): Obsidian matches case-insensitively, so this resolves. Test in Task 5.
4. Titles with YAML-special or non-ASCII characters (`M2. O11 Infras: Portfolios`, `Chat with Architect - João`, `#tag-like`): emitted `title` is valid YAML and round-trips exactly. Test in Task 3.
5. A `.md` file that is not valid UTF-8: reported as blocking with its path, never crashes or gets rewritten. Test in Task 2.

---

## File Structure

```
.claude-plugin/marketplace.json                 # modify: replace hello with second-brain
README.md                                       # modify: install instructions
plugins/hello/                                  # delete
plugins/second-brain/
  .claude-plugin/plugin.json
  pyproject.toml                                # dev only: pytest, ruff, black config; pythonpath
  skills/okf-migrate/
    SKILL.md                                    # workflow for Claude
    references/types-yaml.md                    # types.yaml format and type guidance
    scripts/okf_migrate.py                      # PEP 723 entry: deps + `from okfmig.cli import main`
    scripts/okfmig/__init__.py
    scripts/okfmig/mdtext.py                    # code-span masking, inline tags
    scripts/okfmig/vault.py                     # discovery, exclusions, Obsidian settings
    scripts/okfmig/frontmatter.py               # split, parse, add fields, strip
    scripts/okfmig/typerules.py                 # types.yaml load, type resolution
    scripts/okfmig/links.py                     # wikilink parse, resolve, rewrite, encode
    scripts/okfmig/reserved.py                  # index/log classify, render, update, regroup
    scripts/okfmig/gitutil.py                   # clean check, timestamps, info/exclude
    scripts/okfmig/planner.py                   # Plan model, build_plan, inventory, plan.md
    scripts/okfmig/apply.py                     # hash check, atomic writes, renames
    scripts/okfmig/validate.py                  # conformance checks
    scripts/okfmig/cli.py                       # argparse subcommands
  tests/
    conftest.py                                 # make_vault, git_vault fixtures
    test_cli.py test_vault.py test_frontmatter.py test_typerules.py test_links.py
    test_reserved.py test_gitutil.py test_planner.py test_apply_validate.py test_end_to_end.py
```

All test commands run from `plugins/second-brain/` as `uv run pytest ...` (the pyproject provides `ruamel.yaml` and the `dev` group with pytest, Ruff, Black).

Note on the spec's "single file" packaging: the entry script stays a single PEP 723 file runnable with `uv run`, but the logic lives in a sibling `okfmig/` package so each module has one responsibility. `ruamel.yaml` is used to parse and to emit individual new values; existing frontmatter is never re-dumped (new keys are inserted as text), which is a stronger guarantee than round-trip dumping.

---

### Task 1: Plugin scaffold, dev tooling, CLI skeleton

**Files:**
- Delete: `plugins/hello/`
- Modify: `.claude-plugin/marketplace.json`, `README.md`
- Create: `plugins/second-brain/.claude-plugin/plugin.json`, `plugins/second-brain/pyproject.toml`, `scripts/okf_migrate.py`, `scripts/okfmig/__init__.py`, `scripts/okfmig/cli.py`, `tests/conftest.py`, `tests/test_cli.py` (paths under `plugins/second-brain/skills/okf-migrate/` for `scripts/`)

**Interfaces:**
- Produces: `okfmig.cli.main(argv: list[str] | None = None) -> int` with subparsers `scan`, `plan`, `apply`, `validate` (each takes positional `vault: Path`; later tasks add flags and handlers). Fixture `make_vault(tmp_path) -> Callable[[dict[str, str | bytes]], Path]` writing files (str as UTF-8, bytes raw) under `tmp_path / "vault"` and returning the root. Fixture `git_vault` = same but runs `git init -q`, sets `user.email`/`user.name`, and commits all files.

- [ ] **Step 1: Branch.** `git checkout -b feat/okf-migrate`
- [ ] **Step 2: Scaffold.** Remove `plugins/hello`. Marketplace entry: `{"name": "second-brain", "source": "./plugins/second-brain", "description": "Manage an Obsidian second brain in the Open Knowledge Format (OKF)", "version": "0.1.0"}`. `plugin.json` with same name/description/version and `author.name: "gaspia"`. README install line becomes `/plugin install second-brain@gaspia-marketplace`.
- [ ] **Step 3: pyproject.** `requires-python = ">=3.14"`, dependency `ruamel.yaml>=0.18`, dependency-group `dev = ["pytest>=8", "ruff", "black"]`, `[tool.pytest.ini_options] pythonpath = ["skills/okf-migrate/scripts"]`, `testpaths = ["tests"]`, Black/Ruff config per Global Constraints. Entry script header:

```python
# /// script
# requires-python = ">=3.14"
# dependencies = ["ruamel.yaml>=0.18"]
# ///
```

- [ ] **Step 4: Failing test** `test_cli.py::test_help_lists_subcommands`: `subprocess.run(["uv", "run", SCRIPT, "--help"])` exits 0 and stdout contains `scan`, `plan`, `apply`, `validate`. `test_unknown_vault_exits_2`: `main(["scan", "/nonexistent"]) == 2`.
- [ ] **Step 5: Run** `uv run pytest tests/test_cli.py -v`. Expected: FAIL (no module).
- [ ] **Step 6: Implement** `okf_migrate.py` (insert its own dir on `sys.path`, call `sys.exit(main())`) and `cli.main`: argparse, validate the vault path is a directory (print error to stderr, return 2), dispatch stubs returning 0.
- [ ] **Step 7: Run** the tests. Expected: PASS. Also `uv run ruff check . && uv run black --check .` clean. Run `claude plugin validate .` from repo root: passes.
- [ ] **Step 8: Commit.** `git add -A && git commit -m "feat(second-brain): scaffold plugin and okf-migrate CLI"`

---

### Task 2: Vault discovery and markdown text helpers

**Files:**
- Create: `scripts/okfmig/mdtext.py`, `scripts/okfmig/vault.py`, `tests/test_vault.py`

**Interfaces:**
- Produces (`mdtext.py`):
  - `code_ranges(text: str) -> list[tuple[int, int]]`: char ranges of fenced blocks (``` and ~~~, until matching fence or EOF) and inline code spans.
  - `in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool`
  - `inline_tags(body: str) -> set[str]`: Obsidian `#tag` / `#a/b` outside code and outside links/headings syntax (a `#` preceded by start or whitespace, followed by a tag char, not all digits), lowercased, without `#`.
- Produces (`vault.py`):
  - `@dataclass(frozen=True) class Note: path: PurePosixPath; text: str`
  - `@dataclass class ObsidianSettings: new_file_folder: PurePosixPath` (from `.obsidian/app.json` `newFileFolderPath` when `newFileLocation == "folder"`, else `PurePosixPath(".")`)
  - `@dataclass class Vault: root: Path; notes: list[Note]; reserved: list[Note]; files: list[PurePosixPath]; excluded: list[PurePosixPath]; undecodable: list[PurePosixPath]; settings: ObsidianSettings`
  - `discover(root: Path, extra_excludes: list[str] = []) -> Vault`. `notes`: included `.md` not named `index.md`/`log.md`, sorted. `reserved`: included `index.md`/`log.md`. `files`: every file Obsidian would index (everything except the always-excluded dot dirs; nested repos and excluded files ARE included, since links may point to them). `excluded`: excluded `.md` files and nested repo roots. Globs use `PurePosixPath.full_match`. `undecodable`: `.md` files failing UTF-8 decode (not in `notes`).
  - Constants `ALWAYS_EXCLUDED_DIRS`, `DEFAULT_EXCLUDED_FILES`.

- [ ] **Step 1: Failing tests** in `test_vault.py`:
  - `test_discovers_notes_and_reserved`: files `a.md`, `x/index.md`, `x/log.md`, `x/b.md` → notes `[a.md, x/b.md]`, reserved `[x/index.md, x/log.md]`.
  - `test_excludes_dot_dirs_nested_repo_and_claude_md`: `.obsidian/app.json`, `.okf/types.yaml`, `repo/.git/HEAD`, `repo/r.md`, `CLAUDE.md`, `sub/AGENTS.md`, `n.md` → notes `[n.md]`; `excluded` contains `CLAUDE.md`, `sub/AGENTS.md`, `repo`; `files` contains `repo/r.md` and not `.obsidian/app.json`.
  - `test_extra_excludes_glob`: `Templates/t.md` with `extra_excludes=["Templates/**"]` → not in notes, in excluded.
  - `test_undecodable_md_reported` (Review Focus 5): `bad.md` = `b"\xff\xfe\x00bad"` → `undecodable == [bad.md]`, not in notes, no exception.
  - `test_new_file_folder_setting`: app.json `{"newFileLocation": "folder", "newFileFolderPath": "Inbox"}` → `PurePosixPath("Inbox")`; missing app.json → `"."`.
  - `test_code_ranges_and_inline_tags`: body ``"#real and `#code`\n```\n#fenced\n```\n# Heading\n#2026\n#a/b"`` → `inline_tags == {"real", "a/b"}`.
- [ ] **Step 2: Run** `uv run pytest tests/test_vault.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement** `mdtext.py` and `vault.py` per Interfaces. Walk with `os.walk`, pruning always-excluded dirs; a dir with a `.git` entry (file or dir) below root is recorded in `excluded` and its files go to `files` but never to `notes`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -am "feat(okf-migrate): vault discovery and markdown helpers"` (add new files first).

---

### Task 3: Frontmatter

**Files:**
- Create: `scripts/okfmig/frontmatter.py`, `tests/test_frontmatter.py`

**Interfaces:**
- Produces:
  - `@dataclass class Parsed: has_block: bool; data: dict | None; error: str | None; body: str; newline: str` (`newline` is `"\r\n"` if the delimiter line uses CRLF else `"\n"`; BOM is not part of `body` and is preserved by writers).
  - `parse(text: str) -> Parsed`. A block exists only if the text (after optional BOM) starts with `---` + newline and has a closing `---` line. Empty block → `data == {}`. YAML that fails, or parses to a non-mapping → `data None`, `error` set.
  - `ensure_fields(text: str, fields: dict[str, str]) -> str`. For each key in order: skip if present with a non-empty value; if present and empty/null (`type:`, `type: ""`, `type: null`, `type: ~`), replace that top-level line in place; otherwise append a new line before the closing delimiter. Without a block, prepend `---{nl}...{nl}---{nl}` (keys in given order) and keep the rest of the text unchanged. Values are emitted with ruamel (`YAML(typ="rt")`, width very large) as a single-key mapping so quoting is correct. Returns `text` unchanged when nothing is added.
  - `strip(text: str) -> str`: remove the block (and one following blank line, if any).
  - `first_h1(body: str) -> str | None`.
  - `is_empty_value(v: object) -> bool` (None or whitespace-only string).

- [ ] **Step 1: Failing tests:**
  - `test_adds_block_when_missing`: `ensure_fields("# Hi\nbody\n", {"type": "note", "title": "Hi", "timestamp": "2026-11-05T10:00:00+00:00"})` → output starts with `"---\ntype: note\ntitle: Hi\ntimestamp: "`, ends with `"\n---\n# Hi\nbody\n"`, and `parse(out).data == {"type": "note", "title": "Hi", "timestamp": "2026-11-05T10:00:00+00:00"}` (timestamp stays a string, so quoting is whatever ruamel needs for that).
  - `test_preserves_existing_block_verbatim`: input `"---\nnotion-id: 2c5  # keep\nGoal: x\n---\nbody\n"` → output starts with `"---\nnotion-id: 2c5  # keep\nGoal: x\n"` and adds `type`, `title`, `timestamp` lines before `---`.
  - `test_fills_empty_type_in_place`: `"---\ntype:\ntags: [a]\n---\n"` with `{"type": "note"}` → `"---\ntype: note\ntags: [a]\n---\n"`.
  - `test_keeps_capital_Type_and_adds_type`: `"---\nType: Meeting\n---\n"` → contains both `Type: Meeting` and `type: meeting`.
  - `test_noop_when_complete`: returns identical string object content.
  - `test_invalid_yaml_reported`: `"---\na: [\n---\n"` → `data is None`, `error` non-empty. `"---\n- a\n---\n"` → error (non-mapping).
  - `test_crlf_and_bom` (Review Focus 1): `"﻿---\r\nGoal: x\r\n---\r\nbody\r\n"` → parse has block; `ensure_fields(..., {"type": "note"})` == `"﻿---\r\nGoal: x\r\ntype: note\r\n---\r\nbody\r\n"`.
  - `test_special_titles_round_trip` (Review Focus 4): for each of `"M2. O11 Infras: Portfolios"`, `"Chat with Architect - João"`, `"#tag-like"`, `"yes"`, `"2026-11-09"`: `parse(ensure_fields("", {"title": t})).data["title"] == t` and is a `str`.
  - `test_empty_file`: `ensure_fields("", {"type": "note"}) == "---\ntype: note\n---\n"`.
  - `test_strip`: `strip("---\ntype: index\n---\n\n# C\n") == "# C\n"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_frontmatter.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces. Parse with `YAML(typ="safe")` and convert dates to strings is not needed (only `type`/`title` emptiness is inspected); emit new values with `YAML(typ="rt")` dumping `{key: value}` with `default_style=None` and stripping the trailing newline, forcing strings that ruamel would resolve to non-strings (`yes`, dates) to be quoted (dump via `ruamel.yaml.scalarstring.SingleQuotedScalarString` when `safe_load(value) != value`).
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(okf-migrate): frontmatter parse and additive edits"`

---

### Task 4: Type rules

**Files:**
- Create: `scripts/okfmig/typerules.py`, `tests/test_typerules.py`, `skills/okf-migrate/references/types-yaml.md`

**Interfaces:**
- Produces:
  - `@dataclass class Rule: kind: Literal["folder", "tag", "field", "filename"]; pattern: str; type: str; field_value: str | None = None`
  - `@dataclass class TypeConfig: default: str | None = None; exclude: list[str]; rules: list[Rule]; overrides: dict[str, str]; reserved: dict[str, Literal["index", "log", "note"]]` (all collections default empty)
  - `load(path: Path) -> TypeConfig` (missing file → empty config; unknown rule shape or non-kebab-case `type` in a rule/override/default → `ValueError` naming the entry).
  - `resolve_type(cfg: TypeConfig, path: PurePosixPath, data: dict, body_tags: set[str]) -> tuple[str | None, str]` returning `(type, reason)`; reason strings: `"existing"`, `"override"`, `"rule N: <kind> <pattern>"` (N 1-based), `"default"`, `"unclassified"`. Existing non-empty `data["type"]` returns it as-is (stringified). `folder`: `path.full_match(pattern)`. `tag`: case-insensitive, `#` stripped, against frontmatter `tags` (list, or comma/space separated string) plus `body_tags`. `field`: `str(data.get(name)) == value`. `filename`: `re.search(pattern, path.name)`.
  - `is_kebab(s: str) -> bool` (`^[a-z0-9]+(-[a-z0-9]+)*$`).
  - YAML file format exactly as in the spec, plus `reserved:` mapping `path -> index|log|note`.

- [ ] **Step 1: Failing tests:**
  - `test_precedence`: config with override for `a.md` → `meeting`, rule folder `**` → `note`, default `x`. Existing `type: Concept` → `("Concept", "existing")`; `a.md` without type → `("meeting", "override")`; `b.md` → `("note", "rule 1: folder **")`.
  - `test_empty_type_is_missing`: `data={"type": ""}` with default `note` → `("note", "default")`.
  - `test_each_rule_kind`: folder `second-brain/concepts/**` matches `second-brain/concepts/embeddings.md`; tag `meeting` matches `tags: "Meeting, x"` and body tag `meeting`; field `{name: Type, value: Meeting}` matches `{"Type": "Meeting"}`; filename `^o2o ` matches `Work/o2o Batista.md`.
  - `test_unclassified`: no rules, no default → `(None, "unclassified")`.
  - `test_load_rejects_bad_type`: rule with `type: Meeting Notes` → `ValueError`.
  - `test_load_missing_file_is_empty`.
- [ ] **Step 2: Run** `uv run pytest tests/test_typerules.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces (YAML via `YAML(typ="safe")`).
- [ ] **Step 4: Write** `references/types-yaml.md`: the format (copy the spec's example), precedence, the OKF example types, the vault's `concept` definition ("one reusable idea per file, not tied to a date or meeting"), and the guidance that types are lowercase kebab-case.
- [ ] **Step 5: Run** the tests. Expected: PASS.
- [ ] **Step 6: Commit.** `git commit -m "feat(okf-migrate): type rules"`

---

### Task 5: Link resolution and rewriting

**Files:**
- Create: `scripts/okfmig/links.py`, `tests/test_links.py`

**Interfaces:**
- Consumes: `mdtext.code_ranges`, `mdtext.in_ranges`.
- Produces:
  - `@dataclass(frozen=True) class LinkChange: original: str; replacement: str; status: Literal["resolved", "ambiguous", "unresolved", "date"]; target: PurePosixPath | None; candidates: tuple[PurePosixPath, ...] = ()`
  - `class Resolver: __init__(self, files: Iterable[PurePosixPath], new_file_folder: PurePosixPath)`; `resolve(self, target: str, source: PurePosixPath) -> tuple[PurePosixPath | None, tuple[PurePosixPath, ...]]`. Rules: no extension → append `.md` for lookup (but also try exact name, for attachments); target containing `/` → exact vault path, else unique suffix match on path components; otherwise match by filename. All comparisons case-insensitive. Several candidates → pick shortest path (fewest parts), tie → most shared leading parts with `source.parent`, tie → lexical; return all candidates.
  - `DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$")`
  - `encode_path(p: str) -> str` (encode ` `, `(`, `)`, `#`, `%`, `^`; nothing else) and `encode_anchor(h: str) -> str` (space → `%20`).
  - `relative_href(source: PurePosixPath, target: PurePosixPath) -> str` (`os.path.relpath` on posix parts, then `encode_path`).
  - `rewrite_wikilinks(body: str, source: PurePosixPath, resolver: Resolver, renames: Mapping[PurePosixPath, PurePosixPath]) -> tuple[str, list[LinkChange]]`: rewrites every `[[...]]` / `![[...]]` outside code ranges per the spec table; `\|` counts as the alias separator; resolved target is mapped through `renames`; unresolved non-date targets link to `new_file_folder / (target + ".md")`; date rule per spec (non-embed, unresolved, `DATE_RE` matches target → plain text alias-or-target, status `"date"`); link text escapes `[` and `]`.
  - `rewrite_md_targets(body: str, source_old: PurePosixPath, source_new: PurePosixPath, renames: Mapping[PurePosixPath, PurePosixPath]) -> tuple[str, int]`: rewrites relative targets of existing `[..](..)` links, outside code, that point to a renamed file, and re-bases relative targets when the source itself moved. Returns count changed.
  - `linked_paths(body: str, source: PurePosixPath) -> set[PurePosixPath]`: vault paths of local relative md links (percent-decoded, normalized, anchors dropped).
  - `wikilinks_in(text: str) -> list[str]` (for reporting frontmatter wikilinks and validate).

- [ ] **Step 1: Failing tests** (`files` = `Work/Note.md`, `Work/attachments/img.png`, `Other/Note.md`, `Notes/Embeddings.md`, `Eval&Community/My Page.md`, `2026-01-01.md`; source `Work/Meeting.md` unless stated):
  - `test_table_forms` parametrized over the spec table, exact outputs:
    - `[[Notes/Embeddings]]` → `[Notes/Embeddings](../Notes/Embeddings.md)`
    - `[[Embeddings|emb]]` → `[emb](../Notes/Embeddings.md)`
    - `[[Embeddings#How it works]]` → `[Embeddings > How it works](../Notes/Embeddings.md#How%20it%20works)`
    - `[[#Intro]]` → `[Intro](#Intro)`
    - `[[Embeddings#^abc]]` → `[Embeddings](../Notes/Embeddings.md#^abc)`
    - `![[img.png]]` → `![img.png](attachments/img.png)`
    - `![[img.png|300]]` → `![img.png|300](attachments/img.png)`
    - `![[Embeddings]]` → `![Embeddings](../Notes/Embeddings.md)`
    - `[[My Page]]` → `[My Page](../Eval&Community/My%20Page.md)`
  - `test_ambiguous_prefers_closest`: `[[Note]]` from `Work/Meeting.md` → `[Note](Note.md)`, status `ambiguous`, candidates both.
  - `test_case_insensitive` (Review Focus 3): `[[embeddings]]` → `[embeddings](../Notes/Embeddings.md)`, status `resolved`.
  - `test_unresolved_links_to_new_file_folder`: with `new_file_folder=PurePosixPath(".")`, `[[Ghost Note]]` → `[Ghost Note](../Ghost%20Note.md)`, status `unresolved`.
  - `test_dates`: `[[2026-11-09]]` → `2026-11-09`; `[[2026-11-09|2026-11-09 16:04]]` → `2026-11-09 16:04`; status `date`. `[[2026-01-01]]` (exists) → `[2026-01-01](../2026-01-01.md)`. `![[2026-11-09]]` is not unlinked (becomes an unresolved embed link).
  - `test_escaped_pipe_in_table` (Review Focus 2): `"| [[Embeddings\\|emb]] |"` → `"| [emb](../Notes/Embeddings.md) |"`.
  - `test_code_untouched`: `` "`[[Note]]`\n```\n[[Note]]\n```\n" `` returned unchanged, no changes.
  - `test_existing_md_links_untouched`: `"[x](../Notes/Embeddings.md)"` unchanged by `rewrite_wikilinks`.
  - `test_renames_applied`: renames `{Work/index.md: Work/work-index.md}`, files include `Work/index.md`; `[[Work/index]]` → `[Work/index](work-index.md)`; `rewrite_md_targets("[i](index.md)", ...)` → `"[i](work-index.md)", 1`.
  - `test_linked_paths`: `"[a](My%20Page.md#x) [b](https://x.y) [c](../Notes/Embeddings.md)"` from `Eval&Community/index.md` → `{Eval&Community/My Page.md, Notes/Embeddings.md}`.
- [ ] **Step 2: Run** `uv run pytest tests/test_links.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces. Wikilink regex: `(!?)\[\[([^\[\]\n]+?)\]\]`; split target/alias on the first unescaped-or-escaped pipe (`\|` or `|`), then target/heading on the first `#`. Heading `#^id` keeps `^` unencoded in the anchor.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(okf-migrate): wikilink resolution and rewriting"`

---

### Task 6: Reserved files (index and log)

**Files:**
- Create: `scripts/okfmig/reserved.py`, `tests/test_reserved.py`

**Interfaces:**
- Consumes: `frontmatter.strip`, `frontmatter.parse`.
- Produces:
  - `GENERATED_MARKER = "<!-- okf-migrate:generated -->"`, `OKF_VERSION = "0.2"`, `DATED_ITEM_RE` (top-level `- ` or `* ` item starting with `YYYY-MM-DD` followed by `:`/space).
  - `classify(name: Literal["index.md", "log.md"], text: str) -> Literal["index", "log", "note"]`: `index.md` → `"index"` if it has the marker or at least one list item containing a link (markdown or wiki), else `"note"`; `log.md` → `"log"` if it has a dated item or a `## YYYY-MM-DD` heading, else `"note"`.
  - `@dataclass(frozen=True) class Entry: title: str; href: str; description: str | None`
  - `render_entry(e: Entry) -> str` → `* [title](href) - description` or `* [title](href)`.
  - `render_generated_index(title: str, notes: list[Entry], subfolders: list[Entry], root: bool) -> str`: optional root frontmatter `---\nokf_version: "0.2"\n---\n\n`, then `# {title}\n\n` + entries sorted by `title.casefold()`, then `\n# Subfolders\n\n` + subfolder entries (omitted if none), then `\n` + marker + `\n`.
  - `is_generated(text: str) -> bool` (last non-blank line is the marker).
  - `update_curated_index(text: str, missing: list[Entry], root: bool) -> str`: strip frontmatter (root: set frontmatter to exactly the `okf_version` block), keep every other line; append missing entries to an existing `# Unlisted` section (at its end) or a new trailing `# Unlisted` section. No-op shape when `missing` is empty and frontmatter is already correct.
  - `regroup_log(text: str) -> str`: algorithm from the spec (title kept; `## YYYY-MM-DD` sections kept and merged with dated items of the same date; dated item's date prefix and following `:`/whitespace removed and item rendered as `* <rest>` keeping its child lines; groups newest first; leftovers in original order under trailing `## Undated`). Idempotent.
  - `add_log_entry(text: str | None, today: date, line: str) -> str`: `None` → `# Update Log\n\n## {today}\n\n{line}\n`; otherwise insert under an existing `## {today}` heading (appended to that group) or a new group placed before the first older date heading.

- [ ] **Step 1: Failing tests:**
  - `test_classify`: the real `second-brain/concepts/index.md` content (copy from spec constraints: frontmatter + `- [Embeddings](embeddings.md): ...`) → `index`; `"# Notes on indexes\nSome prose."` as `index.md` → `note`; `second-brain/log.md` content → `log`; `"# Logging\ntext"` as `log.md` → `note`.
  - `test_generated_index_exact`: `render_generated_index("Work", [Entry("b", "b.md", None), Entry("A", "A%20x.md", "Desc")], [Entry("Sub", "Sub/index.md", None)], root=False) == "# Work\n\n* [A](A%20x.md) - Desc\n* [b](b.md)\n\n# Subfolders\n\n* [Sub](Sub/index.md)\n\n<!-- okf-migrate:generated -->\n"`.
  - `test_root_generated_index_frontmatter`: starts with `---\nokf_version: "0.2"\n---\n`.
  - `test_curated_index_keeps_lines_and_adds_unlisted`: the real concepts index + missing `[Entry("Vector DB", "vector-db.md", None)]` → frontmatter gone, `# Concepts` and the Embeddings line byte-identical, ends with `# Unlisted\n\n* [Vector DB](vector-db.md)\n`. Applying again with another missing entry adds to the same section; `text.count("# Unlisted") == 1`.
  - `test_regroup_real_log`: the real `second-brain/log.md` → `"# Log\n\n## 2026-11-05\n\n* Added concept [Embeddings](concepts/embeddings.md). Created `concepts/index.md` and `log.md`.\n"`.
  - `test_regroup_mixed`: items for 2026-11-01 (with a child line), 2026-11-05, an undated item, and a prose paragraph → groups `2026-11-05` then `2026-11-01` (child line follows its item), then `## Undated` with the undated item and paragraph in original order; multiset of non-heading, non-blank lines (with date prefixes removed and bullets normalized) equals the input's. `regroup_log(out) == out`.
  - `test_add_log_entry`: `None` case exact output; existing log with `## 2026-11-05` and today `2026-11-16` → new heading above it; same-day call twice → both lines under one heading.
- [ ] **Step 2: Run** `uv run pytest tests/test_reserved.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(okf-migrate): OKF index and log handling"`

---

### Task 7: Git helpers

**Files:**
- Create: `scripts/okfmig/gitutil.py`, `tests/test_gitutil.py`

**Interfaces:**
- Produces:
  - `is_repo(root: Path) -> bool`
  - `dirty_paths(root: Path) -> list[str]`: `git status --porcelain=v1 -z --untracked-files=all -- . ':(exclude).obsidian' ':(exclude).okf'`, returning paths.
  - `last_commit_times(root: Path) -> dict[PurePosixPath, str]`: one `git log --format=%x00%cI --name-only` pass; first (newest) time per path wins; ISO 8601 with offset.
  - `mtime_iso(path: Path) -> str`: local mtime as ISO 8601 with offset, seconds precision.
  - `ensure_info_exclude(root: Path, entry: str = ".okf-migrate/") -> None`: append the line to `.git/info/exclude` if absent.

- [ ] **Step 1: Failing tests** (use `git_vault`):
  - `test_clean_ignores_obsidian_and_okf`: after commit, modify `.obsidian/workspace.json` and create `.okf/types.yaml` → `dirty_paths == []`; modify `a.md` → `["a.md"]`.
  - `test_last_commit_times`: two commits touching `a.md` → time equals `git log -1 --format=%cI -- a.md`; untracked file absent from dict.
  - `test_info_exclude_idempotent`: call twice → line appears once; `git status --porcelain` does not list `.okf-migrate/x`.
  - `test_not_a_repo`: plain dir → `is_repo` False.
- [ ] **Step 2: Run** `uv run pytest tests/test_gitutil.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `subprocess.run([...], cwd=root, check=True, capture_output=True, text=True)`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(okf-migrate): git helpers"`

---

### Task 8: Planner, scan inventory, plan report

**Files:**
- Create: `scripts/okfmig/planner.py`, `tests/test_planner.py`

**Interfaces:**
- Consumes: everything from Tasks 2 to 7.
- Produces:
  - `@dataclass class FileOp: path: str; action: Literal["create", "modify", "rename"]; old_sha256: str | None; new_content: str; new_path: str | None = None`
  - `@dataclass class Plan: ops: list[FileOp]; blocking: list[str]; unclassified: list[str]; ambiguous: list[dict]; unresolved: list[dict]; dates_unlinked: list[dict]; frontmatter_wikilinks: list[dict]; nonkebab_types: list[dict]; renames: dict[str, str]; excluded: list[str]; types_assigned: dict[str, dict]; counts: dict[str, int]` (`counts` keys: `notes_typed`, `links_converted`, `indexes_created`, `indexes_updated`, `dates_unlinked`). `types_assigned[path] = {"type": ..., "reason": ...}` for notes getting a new type.
  - `plan_to_json(plan: Plan) -> str`, `plan_from_json(s: str) -> Plan`.
  - `build_plan(vault: Vault, cfg: TypeConfig, timestamps: Mapping[PurePosixPath, str], today: date) -> Plan`
  - `render_plan_md(plan: Plan) -> str`: sections Summary (counts), Blocking, Unclassified, Renames, Types assigned (grouped by type with reason), Ambiguous links, Unresolved links, Dates unlinked, Frontmatter wikilinks (not changed), Non-kebab existing types (informational), Indexes, Excluded (outside bundle), Files changed. Each link item shows `file: before -> after`.
  - `build_inventory(vault: Vault, cfg: TypeConfig) -> dict` and `render_inventory_summary(inv: dict) -> str`: counts of notes, with/without frontmatter, parse errors, undecodable, existing `type` histogram, top-level and second-level folder histogram, tag histogram (frontmatter + inline), frontmatter key histogram, wikilink/embed/date-link counts, reserved files with `classify` result, excluded paths.
- Algorithm for `build_plan` (order matters):
  1. Blocking: each `vault.undecodable` path.
  2. Reserved files: kind = `cfg.reserved.get(path)` else `classify`. Kind `note` → rename to `<parent-name-kebab>-index.md` / `-log.md` (root: vault dir name), blocking if the new path exists. Renamed files are then treated as notes.
  3. Resolver over `vault.files` (pre-rename names), renames mapping.
  4. For each note (new path): parse; parse error → blocking (`"<path>: invalid frontmatter: <error>"`), skip. `resolve_type`; `None` → `unclassified` (also a blocking entry `"<path>: unclassified"`). Fields to ensure: `type`, `title` (`data.title` kept; else `first_h1(body)` else stem), `timestamp` (`timestamps.get(old_path)` else `mtime_iso`). Then `rewrite_wikilinks` and `rewrite_md_targets` on the body. Record changes in the lists. Op if content or path changed. Existing non-kebab `type` → `nonkebab_types`. Wikilinks in frontmatter → `frontmatter_wikilinks`.
  5. Logs (kind `log`): `strip` then `regroup_log`, plus `rewrite_wikilinks`; op if changed.
  6. Indexes, per included directory that (recursively) contains notes, deepest first: titles and descriptions come from each note's post-change frontmatter. Existing index generated → re-render; curated → `update_curated_index` with notes and subfolder indexes not in `linked_paths` (wikilinks in it converted first); missing → create. Root directory uses `root=True` and title = vault dir name. Count `indexes_created` / `indexes_updated` only for ops.
  7. If any op exists: root `log.md` op with `add_log_entry` (Init if the root `index.md` did not exist with `okf_version` before this plan, else Update), using the counts. Notes typed = notes whose frontmatter changed.
  8. Deterministic: ops sorted by path.

- [ ] **Step 1: Failing tests** (fixed `today=date(2026, 11, 16)`, timestamps dict supplied):
  - `test_raw_vault_plan`: vault `a.md` (no FM, wikilink to `b`), `b.md` (Notion FM `notion-id: 1`), `Work/c.md` (`[[2026-11-09]]`), types.yaml `default: note`. Assert: ops for `a.md`, `b.md`, `Work/c.md`, `index.md` (create, root frontmatter), `Work/index.md` (create), `log.md` (create, contains `* **Init**: Migrated vault to OKF (3 notes typed, 1 links converted, 2 indexes created).`); `b.md` new content starts with `---\nnotion-id: 1\n`; `dates_unlinked` has one item; `blocking == []`.
  - `test_unclassified_blocks`: no default, no rules → `unclassified == ["a.md", ...]` and blocking non-empty.
  - `test_invalid_frontmatter_blocks`.
  - `test_reserved_note_renamed_and_links_updated`: `Work/index.md` with prose only, `a.md` with `[[Work/index]]` → op `rename` to `Work/work-index.md`, `a.md` links to `Work/work-index.md`, a generated `Work/index.md` create op exists.
  - `test_reserved_override`: same but `cfg.reserved = {"Work/index.md": "index"}` → no rename.
  - `test_idempotent_after_apply`: write all `new_content` to disk (helper applying ops directly), rediscover, re-plan → `ops == []` (no log entry).
  - `test_rerun_new_note`: after first apply, add `Work/d.md` → ops only `Work/d.md`, `Work/index.md`, `log.md`; log gains `* **Update**: Brought 1 notes to OKF (0 links converted, 1 indexes updated).` under `## 2026-11-16` (same heading as Init).
  - `test_plan_json_round_trip`.
  - `test_inventory_summary_mentions_counts`: summary text contains note count and a tag from the fixture.
- [ ] **Step 2: Run** `uv run pytest tests/test_planner.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces and Algorithm.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(okf-migrate): migration planner and reports"`

---

### Task 9: Apply, validate, CLI wiring, end-to-end

**Files:**
- Create: `scripts/okfmig/apply.py`, `scripts/okfmig/validate.py`, `tests/test_apply_validate.py`, `tests/test_end_to_end.py`
- Modify: `scripts/okfmig/cli.py`

**Interfaces:**
- Consumes: `Plan`, `plan_from_json`, `build_plan`, `build_inventory`, `discover`, `typerules.load`, gitutil.
- Produces:
  - `class BlockedPlanError(Exception)`, `class StaleFileError(Exception)`
  - `apply_plan(root: Path, plan: Plan) -> list[str]`: raise `BlockedPlanError` if `plan.blocking`; verify every op's `old_sha256` against disk (`None` means must not exist) before writing anything, raise `StaleFileError` listing paths; then write each `new_content` atomically (temp file in same dir + `os.replace`), performing renames by writing to `new_path` and removing `path`. Write bytes encoded UTF-8 exactly as `new_content` (no newline translation: open with `newline=""`).
  - `@dataclass class Report: errors: list[str]; warnings: list[str]`; `validate(root: Path, cfg: TypeConfig, expected_unresolved: set[str] | None = None) -> Report`. Errors: note without parseable mapping frontmatter or with empty `type`; `index.md` with frontmatter (root: anything other than exactly `okf_version`); `log.md` with frontmatter; any wikilink outside code in an included file; undecodable `.md`. Broken local relative links: error if `expected_unresolved` is given and the link is not in it, else warning. Excluded paths reported as warnings `"outside bundle: <path>"`.
  - CLI:
    - `scan <vault> [--workdir PATH]`: preflight (`is_repo` else exit 2; `dirty_paths` non-empty → print them, exit 2), `ensure_info_exclude`, load `<vault>/.okf/types.yaml` if present (for excludes), write `inventory.json`, print summary. Exit 0.
    - `plan <vault> [--types PATH] [--workdir PATH] [--today YYYY-MM-DD]`: writes `plan.json` and `plan.md`, prints counts and blocking items. Exit 0 (blocking is reported, not an error).
    - `apply <vault> [--plan PATH]`: preflight clean check, `apply_plan`; on `BlockedPlanError` / `StaleFileError` print reason, exit 1. Prints changed paths.
    - `validate <vault> [--types PATH] [--plan PATH]`: prints report; exit 1 if errors.
    - Defaults: workdir `<vault>/.okf-migrate`, types `<vault>/.okf/types.yaml`, plan `<workdir>/plan.json`.

- [ ] **Step 1: Failing tests** (`test_apply_validate.py`):
  - `test_apply_refuses_blocking`, `test_apply_refuses_stale` (modify a file after planning; assert no file was written), `test_apply_rename_and_crlf_preserved` (rename op removes old path; CRLF content written byte-exact).
  - `test_validate_conformant_and_failures`: a migrated mini vault → no errors; add `x.md` without frontmatter → error mentions `x.md`; add `[[y]]` to a note → error; give `Work/index.md` frontmatter → error; `log.md` with frontmatter → error.
  - `test_validate_unresolved_expected`: broken link listed in plan → no error; not listed → error.
- [ ] **Step 2: Failing tests** (`test_end_to_end.py`, real git vault, CLI via `main([...])`): a fixture covering the spec's Testing list (no FM; Notion FM with comment; invalid YAML; each table link form; ambiguous and unresolved; dates with/without alias and one real daily note; links in code; spaces and `&` in paths; nested repo; reserved index and log as index/log and as a note; empty file; `CLAUDE.md`).
  - `test_full_flow`: `scan` exit 0 and `inventory.json` exists; `plan` reports blocking for invalid YAML; fix that file and commit; `plan` again → no blocking; `apply` exit 0; `validate --plan` exit 0. Golden checks: exact expected content for the empty file, the Notion note, the table-forms note, the curated `second-brain/concepts/index.md`, the regrouped `second-brain/log.md`, and root `log.md`. `CLAUDE.md` and nested repo files byte-identical. `git status` shows no change under `.obsidian/`.
  - `test_rerun_flow`: after `test_full_flow` state committed, add a note and delete one, `plan` + `apply` → generated index lists the new note and drops the deleted one; curated index keeps its single `# Unlisted`; root log has an `Update` line; a further `plan` has zero ops.
  - `test_dirty_tree_refused`: uncommitted note change → `scan` exit 2.
- [ ] **Step 3: Run** `uv run pytest tests/test_apply_validate.py tests/test_end_to_end.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement** `apply.py`, `validate.py`, and CLI handlers per Interfaces.
- [ ] **Step 5: Run** `uv run pytest -v`. Expected: all PASS. `uv run ruff check . && uv run black --check .` clean.
- [ ] **Step 6: Commit.** `git commit -m "feat(okf-migrate): apply, validate, CLI"`

---

### Task 10: SKILL.md and a rehearsal on a copy of the real vault

**Files:**
- Create: `plugins/second-brain/skills/okf-migrate/SKILL.md`
- Modify: `README.md` (one paragraph describing `second-brain` / `okf-migrate`)

**Interfaces:**
- Consumes: the CLI from Task 9; `references/types-yaml.md` from Task 4.

- [ ] **Step 1: Write SKILL.md.** Frontmatter `name: okf-migrate`, `description: Migrate an Obsidian vault to the Open Knowledge Format (OKF), or re-run to bring new notes up to OKF. Use when the user asks to migrate, convert, or make a vault OKF-compliant, or to normalize new notes in an OKF vault.` Body (under ~120 lines): the spec's 9-step workflow, with exact commands `uv run <skill-base-dir>/scripts/okf_migrate.py <subcommand> "<vault>"`; where `types.yaml` lives and to read `references/types-yaml.md` before drafting it; how to classify unclassified notes (read the note, propose a type and a one-line reason, write into `overrides`, user reviews); how to handle reserved-name files flagged as notes (confirm with the user, record in `reserved:`); the review checklist (blocking, renames, ambiguous, unresolved, dates unlinked); state the undo command before apply; never commit; hand-off reminders (Obsidian "New link format" → "Relative path to file", "Use [[Wikilinks]]" off, pause `github-sync` during the run).
- [ ] **Step 2: Rehearse on a copy.** `cp -R "/Users/paulo.alves/Documents/Obsidian Vault" <scratchpad>/vault-copy`, commit any pending `.obsidian` changes in the copy only, then run `scan`, draft a `types.yaml` (at least `default: note` plus folder rules for `second-brain/concepts/**` → `concept`), `plan`, `apply`, `validate --plan`. Expected: `validate` exit 0, nested `notebooks/` untouched, `second-brain/` files match the formats verified in Task 9. Run `plan` again: zero ops. Record any surprises (crashes, odd link conversions) and fix them with a regression test in the owning module before continuing. Never write to the real vault.
- [ ] **Step 3: Validate plugin.** From repo root `claude plugin validate .`. Expected: `✔ Validation passed`.
- [ ] **Step 4: Commit.** `git add -A && git commit -m "feat(okf-migrate): skill workflow and docs"`
