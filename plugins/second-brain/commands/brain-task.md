---
description: Change one second brain task: done, defer --until DATE, delegate --to PERSON, or drop
argument-hint: <done|defer|delegate|drop> <id> [--until YYYY-MM-DD] [--to me|people/<slug>.md|Name]
---

Brain: the path in the `SECOND_BRAIN` environment variable (`printenv SECOND_BRAIN`). If it is not set, ask the user for the path and suggest adding `"env": {"SECOND_BRAIN": "<path>"}` to their Claude Code settings.

Run:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/okf_brain.py" task "<brain>" $ARGUMENTS
```

If the user gave a task's text instead of its id, first run `task-similar "<brain>" "<text>"` and confirm which id they mean. For `delegate`, prefer a `people/<slug>.md` path when that person page exists. Report the printed result. Exit 1 means refused (unknown or already closed id): show the message. Exit 2 means a usage error: show the correct form from the argument hint.
