"""CLI entry point for standalone tasks management — DISPATCH ONLY.

Boundary (the 1.5.9 split, design-1.5.9.md): this module parses argv, runs
the session-GC sweep, and routes each command to its owning module — nothing
else. Command bodies live in: tasks/lifecycle.py (work/close/new/blocked/
parked/freehand), tasks/review.py (panel + single-judge), tasks/history.py
(context/intent/timeline/tagger/tag/retro/log), tasks/diagnostics.py
(doctor/audit), tasks/project_setup.py (init/bootstrap), tasks/mindmap.py
(mindmap-sync + map parsing), tasks/merge_prep.py (prepare-merge/
merge-doctor); shared helpers in tasks/shared.py. The trivial list/status/
models delegates stay inline. Dispatch branches import lazily (house style;
also keeps startup flat and cycles impossible). The if/elif chain's shape is
load-bearing: tests/test_cli_dispatch.py parses it against COMMANDS, and the
readme-audit skill greps it to count subcommands. `python3 -m tasks.cli` is
the shipped entry (scripts/tasks execs it) — main() stays here forever.
"""
from __future__ import annotations

import sys
from tasks.core import list_tasks, task_status
from tasks.shared import find_project_root, _gc_dead_sessions

# Every top-level command the dispatcher accepts, aliases included. Pinned two
# ways by tests/test_cli_dispatch.py: this tuple must equal the dispatch
# chain's literals, and every entry must reach its arm through a real
# `python3 -m tasks.cli <cmd>` invocation — so a module peel can never orphan
# an arm silently. Keep it in dispatch order.
COMMANDS = (
    "work", "new", "init", "bootstrap", "list", "ls", "panel-review",
    "models", "plan-review", "impl-review", "judge", "context", "intent",
    "timeline", "tagger", "tag", "retro", "status", "audit", "blocked",
    "handoff", "parked", "freehand", "doctor", "environment", "detect-verify", "merge-doctor",
    "mindmap-sync", "log", "prepare-merge", "compact", "recall", "dashboard",
)


def print_usage():
    from tasks.template import usage_text
    print(usage_text())


def main():
    """CLI entry point. Task 058: a contended task lock raises `LockTimeout` with
    a message written for the operator ("another playbook holder (pid …) has held
    …"); catching it here is what turns that into the message rather than a
    traceback (impl panel r2, opus F1). The raise happens BEFORE any read or
    write, so nothing was changed when it fires."""
    from tasks.filelock import LockTimeout
    try:
        return _main()
    except LockTimeout as exc:
        print(f"Blocked: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)


def _main():
    # Force utf-8 on Windows where the default console encoding (cp1252) chokes on → and emoji.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print_usage()
        return
    if args[0] in ("--version", "-V", "version"):   # task 073 (C6)
        import json as _json
        from pathlib import Path as _Path
        _pj = _Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
        try:
            print(_json.loads(_pj.read_text(encoding="utf-8")).get("version", "unknown"))
        except Exception:
            print("unknown")
        return

    # Inspection is globally side-effect free, regardless of where a command
    # arm would otherwise interpret the token.  Previously `work done --help`
    # closed the active task, `new ... --help` created one, `compact ... --help`
    # rewrote files, and even the one arm with dedicated help ran session GC
    # before printing it.  Intercept before GC and before all dispatch.
    # Only before a `--`: after it every word is literal (task 138 G1-8 — `tasks new …
    # -- what --help prints` is intent text).
    _flags = args[1:args.index("--")] if "--" in args else args[1:]
    if any(a in ("-h", "--help") for a in _flags):
        if args[0] == "merge-doctor":
            from tasks.merge_prep import cmd_merge_doctor
            cmd_merge_doctor(["--help"])
        print_usage()
        return

    cmd = args[0]
    cmd_args = args[1:]

    # `handoff` takes no arguments (task 080, S1b). Refuse BEFORE the session GC
    # below, which unlinks files — a refused command must change nothing at all
    # (round-1 panel). cmd_handoff keeps the same check as defense in depth.
    if cmd == "handoff" and cmd_args:
        print(f"Error: tasks handoff takes no arguments (got: {' '.join(cmd_args)}). "
              "Nothing changed.", file=sys.stderr)
        print("Usage: tasks handoff", file=sys.stderr)
        sys.exit(1)

    # Task 116 (gauntlet 2 G2-08/09/19/30): wrong usage is refused BEFORE anything runs —
    # several of these used to ignore an unknown word, and some then wrote (a task whose
    # Intent was `--bogus`, an audit receipt). The table holds what each parser accepts.
    _err = _wrong_usage(cmd, cmd_args)
    if _err:
        print(f"Error: tasks {cmd}: {_err}. Nothing changed.", file=sys.stderr)
        usage = _USAGE.get(cmd)
        if usage:
            print(f"Usage: {usage}", file=sys.stderr)
        # exit 2 = wrong usage; a close hatch without its reason keeps the exit 1 that
        # tests/test_work_readopt.py pins for it (task 027)
        sys.exit(1 if cmd == "work" else 2)

    # `dashboard` is read-only END TO END (task 053, plan-panel codex#1): the
    # session GC every other command runs first unlinks legacy flat files and
    # dead session dirs — a read-only screen must not have that side effect.
    if cmd != "dashboard":
        _gc_dead_sessions(find_project_root())

    if cmd == "work":
        from tasks.lifecycle import cmd_work
        cmd_work(cmd_args)

    elif cmd == "new":
        from tasks.lifecycle import cmd_new
        cmd_new(cmd_args)

    elif cmd == "init":
        from tasks.project_setup import cmd_init
        cmd_init(cmd_args)

    elif cmd == "bootstrap":
        from tasks.project_setup import cmd_bootstrap
        cmd_bootstrap(cmd_args)

    elif cmd in ("list", "ls"):
        project_path = find_project_root()
        pending_only = "--pending" in cmd_args
        list_tasks(project_path, pending_only=pending_only)

    elif cmd == "panel-review":
        from tasks.review import cmd_panel_review
        cmd_panel_review(cmd_args)

    elif cmd == "models":
        # Model-availability discovery + panel selection (task 012; detect/set 1.5.14).
        # `tasks models check [--no-probe]` audits every models.json pin;
        # `tasks models detect [--json]` inventories installed agents + models;
        # `tasks models select [--no-probe]` interactively rewrites the panel;
        # `tasks models set --panel … --default-judge …` writes it non-interactively.
        from tasks.models_check import cli_models
        sys.exit(cli_models(cmd_args, find_project_root()))

    elif cmd in ("plan-review", "impl-review", "judge"):
        from tasks.review import cmd_single_review
        cmd_single_review(cmd, cmd_args)

    elif cmd == "context":
        from tasks.history import cmd_context
        cmd_context(cmd_args)

    elif cmd == "intent":
        from tasks.history import cmd_intent
        cmd_intent(cmd_args)

    elif cmd == "timeline":
        from tasks.history import cmd_timeline
        cmd_timeline(cmd_args)

    elif cmd == "tagger":
        from tasks.history import cmd_tagger
        cmd_tagger(cmd_args)

    elif cmd == "tag":
        from tasks.history import cmd_tag
        cmd_tag(cmd_args)

    elif cmd == "retro":
        from tasks.history import cmd_retro
        cmd_retro(cmd_args)

    elif cmd == "status":
        project_path = find_project_root()
        task_status(project_path)

    elif cmd == "audit":
        from tasks.diagnostics import cmd_audit
        cmd_audit(cmd_args)

    elif cmd == "blocked":
        from tasks.lifecycle import cmd_blocked
        cmd_blocked(cmd_args)

    elif cmd == "handoff":
        # Session handoff (C1): write the mechanical ~80% into the active task's
        # ## Handoff section, block with reason "handoff"; bootstrap surfaces it.
        from tasks.lifecycle import cmd_handoff
        cmd_handoff(cmd_args)

    elif cmd == "parked":
        from tasks.lifecycle import cmd_parked
        cmd_parked(cmd_args)

    elif cmd == "freehand":
        from tasks.lifecycle import cmd_freehand
        cmd_freehand(cmd_args)

    elif cmd == "doctor":
        from tasks.diagnostics import cmd_doctor
        cmd_doctor(cmd_args)

    elif cmd == "environment":
        # Advisory: which optional tools would improve this setup + how to get
        # them (extra judge seats, sandbox containment, verify tooling,
        # command logging). Never fails — informational (1.5.15).
        from tasks.environment import cli_environment
        sys.exit(cli_environment(cmd_args, find_project_root()))

    elif cmd == "detect-verify":
        # Deterministic suggestion of a project's full verify command (typecheck
        # AND tests AND lint) for /playbook:init to confirm with the user (1.5.19).
        from tasks.verify_detect import cli_detect_verify
        sys.exit(cli_detect_verify(cmd_args, find_project_root()))

    elif cmd == "merge-doctor":
        from tasks.merge_prep import cmd_merge_doctor
        cmd_merge_doctor(cmd_args)

    elif cmd == "mindmap-sync":
        from tasks.mindmap import cmd_mindmap_sync
        cmd_mindmap_sync(cmd_args)

    elif cmd == "log":
        from tasks.history import cmd_log
        cmd_log(cmd_args)

    elif cmd == "prepare-merge":
        from tasks.merge_prep import cmd_prepare_merge
        cmd_prepare_merge(cmd_args)

    elif cmd == "compact":
        # Move agent-marked cold review narrative out of a bloated task.md into
        # task-archive.md (verbatim), keeping the hot trace reviewable (1.5.21).
        from tasks.compact import cmd_compact
        cmd_compact(cmd_args)

    elif cmd == "dashboard":
        from tasks.dashboard import cmd_dashboard
        cmd_dashboard(cmd_args)

    elif cmd == "recall":
        # Cross-tier mind-map retrieval: fetch a node (main + overflow) by id, or
        # locate node ids by keyword — the fetch half of the bootstrap index (1.5.22).
        from tasks.mindmap import cmd_recall
        cmd_recall(cmd_args)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print_usage()
        sys.exit(1)



# What each command takes (task 116). Flags, value-taking flags, and what the positional
# arguments may be: an int (at most that many), a tuple of allowed words, or "taskno" (one
# optional task number). Commands not listed here check their own arguments.
_ARGS = {
    "status": ((), (), 0),
    "bootstrap": ((), (), 0),
    "timeline": ((), (), 0),
    "list": (("--pending",), (), 0),
    "ls": (("--pending",), (), 0),
    "parked": (("--all",), (), 0),
    "dashboard": (("--no-detect",), (), 0),
    "mindmap-sync": (("--fix",), (), 0),
    "retro": ((), ("--since",), 0),
    "freehand": ((), (), ("log",)),   # task 138 G1-3: any other word was ignored and made a freehand task
    "audit": ((), (), "taskno"),
}
def _ARGS_TASK_EXISTS(num: str) -> bool:
    """Does task `num` exist in this project's lane? (read-only)"""
    from tasks.core import resolve_agent_dir
    try:
        root = find_project_root()
        return any((resolve_agent_dir(root) / "tasks").glob(f"{num.zfill(3)}-*/task.md"))
    except (SystemExit, OSError):
        return True                       # no project / unreadable: the command says so itself


_USAGE = {
    "status": "tasks status", "bootstrap": "tasks bootstrap", "timeline": "tasks timeline",
    "list": "tasks list [--pending]", "ls": "tasks ls [--pending]", "parked": "tasks parked [--all]",
    "dashboard": "tasks dashboard [--no-detect]", "mindmap-sync": "tasks mindmap-sync [--fix]",
    "retro": "tasks retro [--since N]", "freehand": "tasks freehand [log]", "audit": "tasks audit [<N>]",
    "new": "tasks new <type> <name> [intent words …]  (intent words starting with `--` go after a `--`)",
    "work": 'tasks work done [--force|--stale-panel-ok --reason "why"]',
}


def _wrong_usage(cmd: str, args: list) -> "str | None":
    """Why `args` is wrong usage of `cmd`, or None (task 116). Runs BEFORE the session GC,
    so a refusal really changes nothing."""
    if cmd == "work" and args[:1] == ["done"]:
        rest = args[1:]
        hatch = next((h for h in ("--force", "-f", "--stale-panel-ok") if h in rest), None)
        reason = None
        if "--reason" in rest:
            i = rest.index("--reason")
            if i + 1 < len(rest) and not rest[i + 1].startswith("-"):    # `-f` is no reason (G1-6)
                reason = rest[i + 1]
        if hatch and not (reason and reason.strip()):
            return f'{hatch} requires --reason "why" — a forced or stale-panel close must record why'
        return None
    if cmd == "audit":
        nums = [a for a in args if a.isdigit()]
        if nums and not _ARGS_TASK_EXISTS(nums[0]):
            return f"no task {nums[0]}"
    if cmd == "new":
        rest = list(args)
        if "--" in rest:
            rest = rest[:rest.index("--")]
            # task 138 G1-1: refused here, before the session GC, not in cmd_new after it
            if len([a for a in rest if a != "--stub"]) != 2:
                return "takes <type> <name> before `--`, the intent after it"
        bad = [a for a in rest if a.startswith("--") and a != "--stub"]
        return f"unknown option {bad[0]!r}" if bad else None
    spec = _ARGS.get(cmd)
    if spec is None:
        return None
    flags, valued, positional = spec
    words = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in flags:
            i += 1
            continue
        if a in valued:
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return f"{a} needs a value"
            if a == "--since" and not args[i + 1].isdigit():
                return f"{a} needs a whole number, not {args[i + 1]!r}"
            i += 2
            continue
        if a.startswith("-"):
            return f"unknown option {a!r}"
        words.append(a)
        i += 1
    if positional == "taskno":
        if len(words) > 1 or (words and not words[0].isdigit()):
            return f"unexpected argument(s) {' '.join(words)!r} (one task number at most)"
        return None
    if isinstance(positional, tuple):
        if len(words) > 1 or (words and words[0] not in positional):
            return f"unexpected argument(s) {' '.join(words)!r}"
        return None
    if len(words) > positional:
        return f"unexpected argument(s) {' '.join(words)!r}"
    return None

if __name__ == "__main__":
    main()
