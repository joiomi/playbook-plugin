---
description: Enter freehand mode — user drives, agent executes, no gate pressure
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep]
---

# Freehand

Enter freehand mode for user-directed work without gate pressure.

## Instructions

Run (via the project's wrapper — it resolves the same plugin copy the hooks run):

```bash
.claude/bin/tasks freehand
```

If that fails (a plugin source checkout with no installed wrapper), run the checkout's own launcher:

```bash
plugins/playbook/scripts/tasks freehand
```

This will either:
- **If a task is active:** insert a Freehand block before the next unchecked gate
- **If no task is active:** create and activate a new task, then insert the Freehand block

Once in freehand mode:
- Wait for user instructions — don't work autonomously
- The drift counter and the stop hook's memento are suppressed (the stop hook never blocks since task 340)
- When the user says done: check `[x] Freehand — <summary>`, run `tasks freehand log`, retro-add checked gates for work done
