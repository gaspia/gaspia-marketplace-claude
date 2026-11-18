---
name: concept
description: Use when the user asks to add, create, define or document a concept in their second brain (for example "/second-brain:new-concept Solution Enabler", "add a concept for X", "we need a concept for X"), or to enrich an existing concept with more sources.
argument-hint: <concept name> [seed facts, URLs or page IDs]
---

# concept

Creates or enriches one concept page in an OKF second brain. Every fact stays cited to a raw snapshot: when outside sources are worth keeping, they are ingested through the `ingest` skill's workflow first, so each one gets a snapshot and a summary page.

## Setup

- Brain: the folder with `.okf/brain.yaml` (ask when there is none or more than one). Read its `CLAUDE.md`: brain conventions win over the schema, and a `## Concept sources` section, when present, sets where and how to search and export sources. Read `.okf/brain.yaml` for `review.mode`.
- Script: `uv run <this skill's base directory>/../../scripts/okf_brain.py <subcommand> "<brain>" ...`.
- Page rules: read `<this skill's base directory>/../ingest/references/page-types.md` once per session.
- Before step 6, load the `ingest` skill when there are sources to ingest.

## Workflow

1. **Name it.** Take the concept name and any seed facts, URLs or page IDs from the arguments. The filename is `concepts/<kebab-case>.md`. Collect aliases (plural, other names, and acronyms of 3 or more letters).
2. **Check for duplicates.** Run `similar` for the name and each alias (skip acronyms shorter than 3 letters). A hit on a concept means asking the user: enrich that page, or create a separate one? A hit on a project, area or source is context, not a duplicate; say so in the draft.
3. **Gather what the brain already has.** Grep `raw/` and the wiki folders for the exact phrase and each alias, case-insensitive (`grep -rniE '\bsolution enablers?\b'`, not a lone common word like `enabler`). Read each hit and drop ordinary-language uses. Note each usable fact with its raw path. Note every page in `concepts/`, `projects/`, `areas/`, `people/` and `decisions/` that mentions the term: these are link targets in step 7. `sources/` summaries are never link targets.
4. **Decide whether to enrich.** Search outside the brain when it has no cited definition, fewer than 3 distinct cited facts beyond the definition, or the user asks for it. Search by exact phrase, in this order, and stop once you have strong candidates:
   1. The sources and search order in the brain's `## Concept sources` section.
   2. Any other connected source that fits the topic, such as a wiki, a ticket tracker or a document store.
   3. The web (WebSearch, then WebFetch), for public or industry terms, or when internal sources come up empty.
   4. Any source the user names, which always takes precedence.

   Read up to 6 candidates in full. For each, recommend:
   - **Ingest**: an authoritative definition, process, workflow, template or reference page that adds facts the brain lacks.
   - **Skip**: meeting notes, status updates, team-specific variants, instance pages (one specific item rather than the idea), pages superseded by another candidate, pages that repeat what the brain already has, and low-authority web pages such as forums, SEO articles or undated posts.
5. **Review draft.** Show one draft, then wait for approval when `review.mode` is `source` or `batch`. In `autonomous` mode, write without waiting, but still ask duplicate questions and still wait for the answers. The draft has these parts, in order:
   1. The definition sentence you will write, and its citation.
   2. Sources to ingest, each with its ID or URL and a one-line reason, and sources skipped, each with a one-line reason.
   3. Facts from the user, which will be written as "(per the user, YYYY-MM-DD)".
   4. Pages to link: the concepts index, related concepts, and projects or areas that mention the term.
   5. Duplicate questions, and any inferred items.
   6. Questions on conventions and existing pages: a convention gap (for example, a type that the brain's conventions do not cover), or a wording error in an existing page that you found while reading it. Each is a question, not a silent edit.
6. **Ingest and write.**
   - With sources to ingest:
     1. Save each source as markdown outside the brain, at the folder and with the source name that `## Concept sources` sets. Without that section, use `<vault>/Sources/<origin>/<Title>.md` (origin is, for example, `confluence` or the web domain) with `--name sources-<origin>`.
     2. Each file starts with `Source: <url>`, then a blank line, then the content. Strip tool status lines from the content. For web pages, save the page's main text; if you only have a summary, say so on a second line, `Note: summarized by the fetch tool`.
     3. Run `snapshot`, then `next`. Write the concept and the other page edits, and write each source summary at the path `next` prints. Then run `done`.
   - Without new sources: write the concept from existing raw snapshots and user facts only.
   - Concept format: a definition first, then `## Details`, `## Examples`, `## Related concepts`, `# Citations`. Frontmatter has `type`, `title`, `description`, `aliases`, `tags`, `timestamp`, `sources`, and `resource` (the main source URL) when there is one.
7. **Enforce links.** Every item below is required:
   - `concepts/index.md` lists the concept. `done` adds it under `# Unlisted`; without an ingest, add the line yourself.
   - Every related concept gets the new concept under its `## Related concepts` and an inline link at the first relevant mention. The new concept links back to each of them.
   - Every project or area from step 3 that names the term gets an inline link at its first mention.
   - Concepts with conventions in the brain's `CLAUDE.md` (for example, a hierarchy of work-item types) follow them. When `CLAUDE.md` does not cover the type, the step 5 draft already asked about it; apply the user's answer.
   - Bump `timestamp` on every page you edit. Add the raw snapshot to `sources:` and to `# Citations` on any page that gains a cited fact.
8. **Log and check.** Without an ingest, append `* Added concept [<Title>](concepts/<file>.md), from <sources>.` under today's date in `log.md`; `done` writes the log for ingests. Run `validate` and fix every error. Show `git status --short`, suggest a commit message, and do not commit.

## Quick reference

| Situation | Action |
| --- | --- |
| `similar` hits a concept | Ask: enrich it or create a separate page |
| The brain has enough facts and the user names no source | No outside search |
| The user gives a URL or page ID | Read it; recommend ingesting unless it fails the Skip criteria |
| The term is public or industry-wide | Include the web in step 4 |
| The user states a fact with no source | Write it with "(per the user, YYYY-MM-DD)" |
| A fact is in no source and not stated by the user | Leave it out, or write it with `(inferred)` and set `status: inferred` |
| `snapshot` refuses because batches are pending | Finish the pending batch first (see the `ingest` skill) |
| The brain tree is dirty before `snapshot` | Ask the user to commit or stash |

## Common mistakes

- A colon inside an unquoted frontmatter value breaks YAML (`done` refuses with "mapping values are not allowed"). Quote the value.
- Raw paths in links must be percent-encoded: spaces as `%20`, `&` as `%26`, parentheses as `%28` and `%29`.
- A citation must point to a source that states the fact. Words like "first" or "always" that are not in the source make it a different claim: drop them, or cite the user.
- Citing a source the page does not list: add it to `sources:` and `# Citations` first, reusing its number when it is already there.
- Leaving tool output such as status or progress lines in a saved source: once snapshotted, raw files are immutable.
- Leaving the new concept unlinked: a concept with no inbound links from related concepts is not done.
- In zsh, a bare `=====` argument triggers equals expansion. Use `echo "#####"` as a separator.
