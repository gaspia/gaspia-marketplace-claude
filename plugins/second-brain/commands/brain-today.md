---
description: Show today's second brain tasks: overdue, due today, waiting on others, stale and new
argument-hint: "[YYYY-MM-DD]"
---

Brain: the path in the `SECOND_BRAIN` environment variable (`printenv SECOND_BRAIN`). If it is not set, ask the user for the path and suggest adding `"env": {"SECOND_BRAIN": "<path>"}` to their Claude Code settings.

Run, adding `--today $ARGUMENTS` only when a date was given:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/okf_brain.py" today "<brain>"
```

Show the output as printed, grouped by bucket. Do not reword task text. If a bucket needs a decision (an overdue task, a stale one), say which `/second-brain:brain-task` command would close, defer or delegate it. Change nothing yourself.
