---
description: Create or update the second brain layout, settings, type rules and schema (safe to re-run after a plugin update)
argument-hint: "[brain path]"
---

Brain: `$ARGUMENTS` if given, else the path in the `SECOND_BRAIN` environment variable (`printenv SECOND_BRAIN`). If neither is set, ask the user for the path and suggest adding `"env": {"SECOND_BRAIN": "<path>"}` to their Claude Code settings.

Follow the Preflight step of the `second-brain:ingest` skill for this brain: run

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/okf_brain.py" init "<brain>"
```

report the paths it created or changed, relay any warning about an outer Obsidian vault, then run `validate "<brain>"` and report the result. Never commit; suggest a commit when files changed.
