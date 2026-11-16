# types.yaml

Persistent type rules for `okf-migrate`, stored at `<vault>/.okf/types.yaml` and committed with the vault.

## Format

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
reserved:                  # optional: how to treat an existing index.md / log.md
  "Work/index.md": note    # index | log | note
```

`overrides` and `reserved` keys are vault-relative paths with forward slashes.

## Precedence

1. An existing non-empty `type` in the note's frontmatter is kept exactly as written (reason `existing`). An empty or null `type` counts as missing.
2. `overrides` entry for the file (reason `override`).
3. First matching entry in `rules`, in order (reason `rule N: <kind> <pattern>`).
4. `default`, if set (reason `default`).
5. Otherwise the note is `unclassified`.

Rule kinds:

- `folder`: glob matched against the vault-relative path (`**` spans folders).
- `tag`: case-insensitive, `#` ignored, matched against frontmatter `tags` (a list, or a comma or space separated string) and inline `#tags` in the body.
- `field`: frontmatter field `name` whose value, as a string, equals `value`.
- `filename`: regular expression searched in the file's basename.

## Type names

Types assigned by the tool are lowercase kebab-case (`one-on-one`, not `One on One`). A rule, override, or default with any other spelling is rejected when the file loads. Existing `type` values in notes are never rewritten.

The OKF example types are suggested where they fit: `concept`, `howto`, `reference`, `decision`, `metric`. Other names such as `meeting`, `one-on-one`, `training`, `initiative` are fine, because OKF does not register types.

## Vault definition of `concept`

From the vault's own CLAUDE.md: "One idea per file. Filename is the concept in kebab-case." A concept note starts with a short plain-language definition, then adds detail, and links related concepts. It is a reusable idea, not tied to a date or meeting.
