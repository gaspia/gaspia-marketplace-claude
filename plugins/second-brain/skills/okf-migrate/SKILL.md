---
name: okf-migrate
description: Migrate an Obsidian vault to the Open Knowledge Format (OKF), or re-run to bring new notes up to OKF. Use when the user asks to migrate, convert, or make a vault OKF-compliant, or to normalize new notes in an OKF vault.
---

# okf-migrate

A bundled script does the mechanical work (scan, plan, apply, validate). You do the judgment: draft type rules, classify leftovers, walk the user through the plan. The change is format only: notes keep their location, name, and content; they gain frontmatter (`type`, `title`, `timestamp`), wikilinks become relative markdown links, and every folder gets an OKF `index.md`.

Run every command as:

```bash
uv run <this skill's base directory>/../../scripts/okf_migrate.py <subcommand> "<vault>"
```

The base directory is shown when this skill loads. Throwaway output goes to `<vault>/.okf-migrate/` (git-excluded). Persistent type rules live in `<vault>/.okf/types.yaml` and are committed with the vault.

## Hard rules

- Never write notes before the user has approved the plan.
- Never commit, and never delete notes (a reserved-name rename moves a file). The tool does neither; the user commits.
- Never edit `.obsidian/`.
- State the undo command before running `apply`.

## Workflow

1. **Preflight.** Ask for the vault path. It must be inside a git repo (the repo top or any folder in it) with no uncommitted changes under the vault (changes outside the vault, and in its `.obsidian/` and `.okf/`, are ignored). If not clean, stop and ask the user to commit or stash. Ask the user to pause Obsidian sync plugins (for example `github-sync`) until the hand-off.
2. **Scan.** `scan "<vault>"` writes `inventory.json` and prints a summary: note counts, frontmatter parse errors, undecodable files, folders, tags, existing `type` and field values, link resolution, reserved files (`index.md`, `log.md`) and how each was classified, excluded paths (nested git repos, git-ignored paths, `CLAUDE.md`, `AGENTS.md`).
3. **Type rules.** If `.okf/types.yaml` exists (a re-run), reuse it; only new unmatched notes need classifying. Otherwise read `references/types-yaml.md` first, then draft the file from the scan summary: folder rules for clear folders (`**/concepts/**` -> `concept`, meeting folders -> `meeting`), tag and field rules where the vault already tags notes, filename regexes for naming patterns, and `default: note` only if the user accepts a catch-all. Prefer the OKF example types (`concept`, `howto`, `reference`, `decision`, `metric`) where they fit; other kebab-case names are fine. Show the draft and adjust until the user confirms.
4. **Plan.** `plan "<vault>"` (rules default to `.okf/types.yaml`, or pass `--types <file>`) writes `plan.json` and `plan.md` and prints counts and blocking items.
5. **Fix blocking items and classify leftovers.** `apply` refuses while any blocking item exists. Handle each, then re-run `plan`:
   - **Unclassified note:** read it, propose a type with a one-line reason, write it under `overrides:` (vault-relative path). The user reviews the list.
   - **Invalid frontmatter / a key that can't be filled in place / undecodable file:** propose a fix to the note, apply it only after the user approves, and have the user commit it (the tree must be clean for `apply`).
   - **Case-insensitive create clash** (for example a note named `Index.md` where an `index.md` would be created): propose renaming the note; the user decides.
   - **Reserved-name file:** the scan flags `index.md` / `log.md` files that look like plain notes. Confirm with the user and record the decision under `reserved:` (`index`, `log`, or `note`). A `note` is renamed to `<folder>-index.md` / `<folder>-log.md` and links to it are updated. A rename collision is blocking; resolve it by hand.
   - **Reserved kind mismatch:** a `reserved:` entry that names a kind other than the file's own (for example `log` for an `index.md`). Fix the entry: use the file's own kind or `note`.
6. **Review.** Summarize `plan.md` for the user, using this checklist:
   - Blocking: must be zero.
   - Renames: every reserved-name file that will be renamed.
   - Types assigned: spot-check the per-type lists and reasons.
   - Ambiguous links: the target Obsidian would pick versus the other candidates.
   - Unresolved links: they stay broken (OKF allows it) and point where Obsidian would create the note. Call out names that look like files (for example `[[Node.js]]`), which become links without `.md`.
   - Dates unlinked: `[[2026-11-09]]` and `[[2026-11-09|...]]` become plain text.
   - Also mention: stale generated indexes (delete by hand), broken links already present, frontmatter wikilinks (left unchanged), non-kebab existing types (informational).
   Get explicit approval before step 7.
7. **Apply.** First tell the user how to undo: before committing, and before creating or editing any notes (`git clean` would delete new untracked notes too), run `git restore . && git clean -fd`, excluding `.obsidian/` (and `.okf/`, so an uncommitted `types.yaml` survives). Preview what `git clean` would delete first. In full: `git -C "<vault>" restore -- . ':!.obsidian' ':!.okf'`, then `git -C "<vault>" clean -nd -- . ':!.obsidian' ':!.okf'` (preview), then `git -C "<vault>" clean -fd -- . ':!.obsidian' ':!.okf'`. Then `apply "<vault>"`. It refuses if a planned file changed since `plan`, or if blocking items exist; re-run `plan` in that case.
8. **Validate.** `validate "<vault>" --plan "<vault>/.okf-migrate/plan.json"`. Exit 0 means conformant and every converted link resolves (or was already unresolved in the plan). On errors, report them; do not patch around them silently.
9. **Hand-off.** Show `git -C "<vault>" diff --stat`. Suggest a commit message (for example `chore: migrate vault to OKF`) and include `.okf/types.yaml` if it is new. Remind the user to:
   - set Obsidian "Files and links" -> "New link format" to "Relative path to file",
   - keep "Use [[Wikilinks]]" off,
   - resume `github-sync` after committing.

## Re-runs

Same flow. `types.yaml` is reused, so usually only new unmatched notes need classifying. Conformant notes are left alone, generated indexes are rebuilt only if their content changes, curated indexes get new notes under `# Unlisted`, and the root `log.md` gets an `**Update**` entry. A `plan` on an up-to-date vault shows zero files changed.

## Exit codes

All commands: 2 when the vault directory is missing. `scan`, `plan`: 0 ok, 2 usage error (not a repo, dirty tree for `scan`, invalid or unreadable `types.yaml`). `apply`: 0 ok, 1 refused (blocking or stale), 2 usage error (not a repo, dirty tree, invalid or unreadable `plan.json`). `validate`: 0 conformant, 1 when errors exist, 2 for invalid `types.yaml` or an invalid or unreadable `--plan` file.

## Common mistakes

- Running `apply` with uncommitted edits from fixing blocking items: commit those first.
- Adding a type with spaces or capitals to `types.yaml`: it is rejected; use kebab-case.
- Changing an existing `type` value in a note: the tool keeps it as written; leave it unless the user asks.
