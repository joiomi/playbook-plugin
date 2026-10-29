"""The `tasks merge-doctor` arm: the thin delegate to `tasks.core.run_merge_doctor`.

Task 338 (PLAN-PLAYBOOK-2.md 4.1 c, DESIGN-PLAYBOOK-2.md §7 A) removed `tasks prepare-merge` from this module — its task
renumbering, chat-log re-sequencing and mind-map collision report. It rewrote ordinary text in every task.md and in
chat_log.md ("2-hour" became "3-hour"), and nothing called it; merging lanes comes with the lanes (plan 7.4).
Imports stdlib + tasks.core + tasks.shared; never a command module (design-1.5.9.md §4).
"""
from __future__ import annotations

import sys
from tasks.core import run_merge_doctor
from tasks.shared import find_project_root


def cmd_merge_doctor(cmd_args):
    """The `tasks merge-doctor` arm — body moved verbatim from cli.py (1.5.9 split)."""
    if cmd_args and cmd_args[0] in ("--help", "-h"):
        print("Usage: tasks merge-doctor <source-branch> [target-branch]")
        print()
        print("  Audits a cross-namespace merge for per-user contamination,")
        print("  stranded conflict markers, and legacy .agent/ paths.")
        print("  Inspects the in-progress merge (MERGE_HEAD present) or the most")
        print("  recent merge commit reachable from HEAD. <source>/<target> are")
        print("  used for cross-comparison only — no branch is checked out.")
        print()
        print("  Exit: 1 if any actionable findings, else 0.")
        sys.exit(0)
    if not cmd_args:
        print("Usage: tasks merge-doctor <source-branch> [target-branch]", file=sys.stderr)
        print("  Audits a cross-namespace merge for per-user contamination,", file=sys.stderr)
        print("  stranded conflict markers, and legacy .agent/ paths.", file=sys.stderr)
        print("  Run 'tasks merge-doctor --help' for details.", file=sys.stderr)
        sys.exit(2)
    source = cmd_args[0]
    target = cmd_args[1] if len(cmd_args) > 1 else "main"
    project_path = find_project_root()
    if not (project_path / ".git").exists():
        print(f"Error: not a git repository: {project_path}", file=sys.stderr)
        sys.exit(2)
    # Validate both refs up front. A bad ref otherwise collapses to an empty
    # user set in _md_user_dirs and the doctor can exit 0 ("SAFE") while
    # silently skipping the intended cross-branch comparison.
    import subprocess as _sp
    for _ref in (source, target):
        if _sp.run(["git", "rev-parse", "--verify", "--quiet", f"{_ref}^{{commit}}"],
                   cwd=str(project_path), capture_output=True).returncode != 0:
            print(f"Error: not a valid git ref: {_ref}", file=sys.stderr)
            print("Usage: tasks merge-doctor <source-branch> [target-branch]", file=sys.stderr)
            sys.exit(2)
    findings = run_merge_doctor(project_path, source, target)
    sys.exit(1 if findings else 0)
