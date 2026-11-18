---
description: Ingest notes (a vault, folder or single note) into the second brain, extracting pages and action items
argument-hint: <vault, folder or note> [--name SOURCE-NAME]
---

Brain: the path in the `SECOND_BRAIN` environment variable (`printenv SECOND_BRAIN`). If it is not set, ask the user for the path and suggest adding `"env": {"SECOND_BRAIN": "<path>"}` to their Claude Code settings.

Use the `second-brain:ingest` skill to ingest this source into that brain: $ARGUMENTS

Skip asking for the brain path in Preflight. Ask for the source only if none was given.
