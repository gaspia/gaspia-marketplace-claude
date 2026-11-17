# Page types

Read this before writing any page. The brain's `CLAUDE.md` schema section states the same rules in short.

## Common rules (every page)

- **Folder and filename.** One folder per type (table below). Filenames are kebab-case (`team-north-star.md`). The `type` value matches the folder; `done` refuses while a page sits outside the wiki folders, so move it into one.
- **Prefer editing.** Run `similar` before creating a page. Edit an existing page in place: add sentences, list items and citations; keep everything already there, including text the user wrote by hand. Never regenerate a page from scratch.
- **When a page earns its place.** Create a person, project or area page only when a source states at least one cited fact beyond the name (a role, a team, what they work on, decided or own), or when the person is the counterpart of a 1:1. A name with nothing else stays inline, as plain text, on the page where it appears. List such name-only mentions in the review draft as a judgment call, not as a duplicate question.
- **Duplicate questions** are only for a `similar` hit, or for two items in the same draft that might be the same thing.
- **Sections.** Use the headings listed for the type, in that order, and only those with something cited to say: omit a section that would be empty, and never write placeholder text such as "Not stated in the source" or "None recorded". Extra sections (for example `## Success criteria` on a project) may follow the listed ones. `## Open questions` holds only questions.
- **Frontmatter.**
  - `type`, `title`, `description` (one sentence saying who or what the page is about), `tags` (a list, may be empty).
  - `timestamp`: ISO 8601 date-time of this edit (`2026-11-17T10:30:00+00:00`). `done` fills a missing `type`, `title` or `timestamp` from the folder rules, but write them anyway.
  - `sources`: one `{resource, title}` entry per source that contributed to the page. `resource` is the relative path to the raw snapshot, either from the page (`../raw/...`) or from the brain root (`raw/...`); use the page-relative form. Add an entry when a new source contributes; never drop one.
  - `status: inferred` when the page holds any inferred sentence. Each inferred sentence also ends with `(inferred)`. Inferred means a reasonable reading that no source states outright; a fact in no source and not inferable is left out.
  - `aliases` (optional list): other names the page is known by (nicknames, acronyms). `similar` matches them.
- **Links.** Standard relative markdown links, never wikilinks: `[Quota Service](../projects/quota-service.md)`. Percent-encode spaces and parentheses in targets (`%20`, `%28`, `%29`). Link related pages inline where they are mentioned.
- **Citations.** Every claim from a source cites it as `[1]`, `[2]`. The page ends with a level-1 `# Citations` section: a numbered list linking the raw snapshots. Numbers are per page; reuse a source's existing number when you edit a page. `validate` checks each citation link resolves to a file under `raw/`.
- **Personal and performance content.** Only on person pages, only under `## Personal` (family, interests, personal context) when `people.personal` is true, and `## Performance` (feedback, self-assessments, growth areas) when `people.performance` is true. With a setting off, write none of that content anywhere, and say in the review what you skipped.
- **Punctuation.** Plain punctuation, no em dashes.

## Types

| Type | Folder | Use for |
| --- | --- | --- |
| `concept` | `concepts/` | One idea, technique, model or term. |
| `project` | `projects/` | Work with a goal and an end. |
| `area` | `areas/` | An ongoing responsibility with no end (a team, a domain, a practice). |
| `person` | `people/` | One person. Filename is their name (`david-santos.md`). |
| `decision` | `decisions/` | One decision, ADR style. |
| `source` | `sources/` | The summary of one ingested source. Path given by `next`. |

Meetings, 1:1s and training notes are sources, never wiki pages of their own. Their content flows into the types above, and each gets one source summary page.

### concept

Short plain-language definition first (one or two sentences, no heading), then `## Details`, `## Examples`, `## Related concepts`.

```markdown
---
type: concept
title: Situation-Behavior-Impact
description: A feedback model that describes a situation, the observed behavior and its impact.
tags: [feedback, leadership]
timestamp: 2026-11-17T10:30:00+00:00
sources:
  - resource: ../raw/main/Work/Training/Take-Off%20leadership%20training.md
    title: Take-Off leadership training
---

Situation-Behavior-Impact (SBI) is a way to give feedback by naming the situation, the specific behavior and its impact on the team and business [1].

## Details

It is the recommended model for the [Recognize Results](recognize-results.md) moment [1].

## Related concepts

- [Recognize Results](recognize-results.md)

# Citations

1. [Take-Off leadership training](../raw/main/Work/Training/Take-Off%20leadership%20training.md)
```

### project

Sections: `## Goal`, `## Status` (state plus the date it was true: "Almost production ready (as of 2026-10-26) [1]"), `## Timeline`, `## People`, `## Decisions`, `## Open questions`.

```markdown
## Goal

Build v1 of the Quota Service and hand it over to [Cloud Enablement Services](../areas/cloud-enablement-services.md) [1].

## Status

MVP almost production ready, deployed but not live (as of 2026-10-26) [1].

## People

- [David Santos](../people/david-santos.md) [1]
```

### area

Sections: `## Scope`, `## Projects`, `## People`.

### person

Sections: `## Role`, `## Team`, `## Works on`, `## Decisions`, then `## Personal` only if `people.personal`, `## Performance` only if `people.performance`. A 1:1 with someone feeds their person page; write only what the note states about them.

```markdown
## Role

Product manager for the Quota Service handover [1].

## Works on

- [Quota Service](../projects/quota-service.md) [1]
```

### decision

Sections: `## Decision`, `## Date`, `## Context`, `## Alternatives considered`, `## Consequences`, and a link to its project or area (under `## Project` or `## Area`). Only for decisions a source states as made; a proposal or question goes on the project page under Open questions.

### source

Path: exactly the `summary page` path printed by `next` (`sources/<YYYY-MM-DD>-<slug>.md`). Never create another one for the same source; a changed source updates this page. Sections, in order:

- `## Origin`: a link to the raw snapshot, plus the original source name and path (from `origin` in `next`).
- `## Summary`: a short paragraph.
- `## Key takeaways`: a list, cited.
- `## Pages updated`: a list linking every page this source created or changed. `done` counts these links for the log line ("N pages updated"), so list each page once and nothing else.

```markdown
---
type: source
title: David Santos 1o1 20261026
description: Handover 1:1 on the Quota Service built by AI Platform.
tags: [meeting, one-on-one]
timestamp: 2026-11-17T10:30:00+00:00
sources:
  - resource: ../raw/main/Work/Initiatives/Asset%20Handover/AI%20Platform/David%20Santos%201o1%2020261026.md
    title: David Santos 1o1 20261026
---

## Origin

[Raw snapshot](../raw/main/Work/Initiatives/Asset%20Handover/AI%20Platform/David%20Santos%201o1%2020261026.md), from `main:Work/Initiatives/Asset Handover/AI Platform/David Santos 1o1 20261026.md`.

## Summary

...

## Key takeaways

- The Quota Service MVP is deployed but not live [1].

## Pages updated

- [Quota Service](../projects/quota-service.md)
- [David Santos](../people/david-santos.md)

# Citations

1. [David Santos 1o1 20261026](../raw/main/Work/Initiatives/Asset%20Handover/AI%20Platform/David%20Santos%201o1%2020261026.md)
```
