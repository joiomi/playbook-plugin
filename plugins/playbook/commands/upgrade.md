---
description: Upgrade the playbook plugin to the latest version
allowed-tools: [Bash, Read, Edit]
---

# Upgrade Playbook Plugin

Upgrade the installed playbook plugin to the latest release.

## Instructions

Run these commands in sequence. Stop on any failure.

### 1. Check current version

```bash
.claude/bin/tasks --version 2>/dev/null || echo "unknown (no .claude/bin/tasks here yet, or it cannot find the plugin)"
```

Tell the user this version now: the restart in step 3 ends this session.

### 2. Refresh the marketplace you installed from

Update it IN PLACE from its own source — a GitHub repository or a local directory, whichever the user added. Never remove it, never add another one: re-adding under the same name would silently point the install at a different repository.

A local directory is only re-read by `marketplace update`, so when that directory is a git clone (a release clone), pull it first. This block does nothing for any other source; if the pull refuses (local changes, a diverged branch), stop and tell the user — never reset their clone:

```bash
DIR=$(python3 -c 'import json, os
p = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "known_marketplaces.json")
try:
    with open(p, encoding="utf-8") as fh:
        src = json.load(fh).get("playbook-x-marketplace", {}).get("source", {})
except Exception:
    src = {}
if src.get("source") == "directory":
    print(src.get("path", ""))' 2>/dev/null)
# only when the directory IS the clone's top level — not a folder inside some other repository
TOP=$( [ -n "$DIR" ] && git -C "$DIR" rev-parse --show-toplevel 2>/dev/null )
if [ -n "$TOP" ] && python3 -c 'import os, sys; sys.exit(0 if os.path.samefile(sys.argv[1], sys.argv[2]) else 1)' "$DIR" "$TOP" 2>/dev/null; then
    git -C "$DIR" pull --ff-only
fi
```

Then refresh the marketplace:

```bash
claude plugin marketplace update playbook-x-marketplace
```

### 3. Update the plugin

```bash
claude plugin update playbook@playbook-x-marketplace
```

A running Claude Code session keeps the copy it started with: tell the user to restart it, then to run `/playbook:upgrade` steps 4-5 (or just `/playbook:init`) in the new session.

### 4. Run /playbook:init to update project files

Run `/playbook:init` to merge any new CLAUDE.md sections and update project wrappers, hooks, and `.gitignore`. This is safe to re-run — it's idempotent. (Note: the plugin's initializer is `/playbook:init`, which runs `scripts/init`; Claude Code's built-in `/init` is a different, generic CLAUDE.md generator that does none of this mechanical upgrade work.)

### 5. Verify

```bash
.claude/bin/tasks --version
```

Report the old and new version numbers. If they are the same, the marketplace's source has nothing newer — say so (for a local directory that is not a git clone, the user updates that directory themselves); do not try another source.
