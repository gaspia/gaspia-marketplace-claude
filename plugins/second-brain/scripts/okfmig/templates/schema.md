# Second brain schema

This folder is an AI-maintained second brain that follows the LLM-wiki pattern. It is an OKF bundle: plain markdown,
relative links, git. Claude owns the wiki and edits it; the user reads, reviews and steers.

## Three layers

1. **Raw sources** (`raw/`): immutable snapshots of ingested notes, with their attachments. Read-only: never edit,
   rename or delete anything under `raw/`. It is outside the OKF bundle, so it has no indexes or frontmatter.
2. **Wiki** (`concepts/`, `projects/`, `areas/`, `people/`, `decisions/`, `sources/`): the pages Claude writes and
   maintains.
3. **Schema** (this section of `CLAUDE.md`): the rules below. Settings live in `.okf/brain.yaml`, type rules in
   `.okf/types.yaml`, the source ledger in `.okf/sources.json`.

## Page types and folders

| Type | Folder | What it is |
| --- | --- | --- |
| `concept` | `concepts/` | One idea per file. Definition first, then detail, examples, related concepts. |
| `project` | `projects/` | Has a goal and an end. Goal, Status (with date), Timeline, People, Decisions, Open questions. |
| `area` | `areas/` | Ongoing responsibility, no end. Scope, Projects, People. |
| `person` | `people/` | Role, Team, Works on, Decisions. Optional `## Personal` and `## Performance` sections. |
| `decision` | `decisions/` | ADR style. Decision, Date, Context, Alternatives considered, Consequences, linked project or area. |
| `source` | `sources/` | One summary per ingested source, at `sources/<YYYY-MM-DD>-<slug>.md` (path assigned by `snapshot`, printed by `next`): Origin, Summary, Key takeaways, Pages updated, Citations. |

Meetings, 1:1s and training notes are sources, not wiki pages. Their content flows into the pages above.

## Page rules

- Filenames are kebab-case, in the folder of their type. The `type` field matches the folder.
- Frontmatter: `type`, `title`, `description` (one sentence), `tags`, `timestamp`, and `sources`, a list of
  `{resource, title}` entries (`resource` is the relative path to the raw snapshot) for every source that contributed.
  Add `status: inferred` when a page holds inferred content, and end each inferred sentence with `(inferred)`.
- Links are standard relative markdown links, never wikilinks. Link related pages inline.
- Citations: end every page that uses source material, source summaries included, with a `# Citations` section, a
  numbered list linking the raw snapshots. Claims cite them as `[1]`.
- When a page earns its place: create a person, project or area page only when a source states at least one cited
  fact beyond the name, or for the counterpart of a 1:1. A bare name stays inline as plain text.
- Sections: use the headings of the type, in order. Omit empty sections, never placeholder text ("None recorded").
- Never invent facts. If a fact is in no source, leave it out or mark it inferred.
- Prefer editing an existing page to creating a duplicate. Keep content the user wrote by hand; edit, never regenerate.
- Ask when unsure whether two notes describe the same idea, person, project, area or decision.
- Use plain punctuation: no em dashes.

## Settings (`.okf/brain.yaml`)

- `review.mode`: `source` (review each source), `batch` (review one draft per batch, the default) or `autonomous`
  (no review). Duplicate questions are always asked.
- `review.batch_size`: sources per batch in `batch` and `autonomous` modes. `source` mode uses 1. A single note is
  always a batch of one.
- `people.personal`: write `## Personal` sections (family, interests, personal context) on person pages only when true.
- `people.performance`: write `## Performance` sections (feedback, self-assessments, growth areas) only when true.
  Turning a setting off stops new content of that kind; it does not remove existing sections.

## Ingest workflow

1. `snapshot`: copy new and changed sources into `raw/<source-name>/`, update the ledger, write the batches.
2. `next`: print the next batch. Read `index.md`, each snapshot (or its diff against the previous snapshot) and the
   pages it may touch. Run `similar` before proposing any new page.
3. Review: draft the takeaways, the pages to create or update, possible duplicates and inferred items, then get
   approval per `review.mode`. Write nothing in `source` or `batch` mode before the review is approved.
4. Write the source summary pages and create or edit the wiki pages. A changed source updates its existing summary.
5. `done <batch-id>`: mark the sources ingested, regenerate indexes, append the log.
6. `validate`: fix every error before the next batch. Never commit automatically; suggest one commit per batch.

## Log format

`log.md` is an OKF log. Each ingested source adds one line:

`* **Ingest**: [<source title>](sources/<file>.md) - <N> pages updated`

where N counts the links in the summary page's Pages updated list.
