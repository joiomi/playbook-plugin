#!/usr/bin/env python3
"""What the hook journal costs a hook run (task 222; PB-PERFORMANCE-HOOKS). Dev-only, a wall-clock driver, not a budget.

The REAL hooks run on crafted payloads in a temp project, M times each, interleaved: from the plugin's scripts as they
are (each run writes its line) against a copy where only the LINE is switched off: `hook_journal_append` returns at
once (the exit runner still runs the hooks' own exit actions — the shell log's line, the stop hook's mark — which the
hooks ran before this journal too), and `command_guard.py` runs `main()` alone. Prints each hook's medians and the
added cost. Usage:
    python3 scripts/bench_hook_journal.py <plugins/playbook/scripts> <empty work dir> [M=60]
2026-10-16 on the owner's Linux host, 80 runs each, CPU time added: before the append lock (load 9.6) task-gate
+0.89 ms, state-echo +0.39, stop +0.88, command-guard +4.31 (its python loads pb_journal and pathlib); with each append
under a shared `flock` (the owner, 22:21; load 0.6) task-gate +1.57, state-echo +1.88, stop +1.94, command-guard +3.79."""
import json
import os
import resource
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

scripts, work = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
M = int(sys.argv[3]) if len(sys.argv) > 3 else 60
# the whole plugin is copied: the hooks' helpers import `tasks` from beside `scripts/` (a copy of `scripts/` alone made
# task-status.py fail there, and state-echo took its short warning path — a first run of this bench measured that)
shutil.copytree(scripts.parent, work / "plugin-off", ignore=shutil.ignore_patterns("__pycache__"))
off = work / "plugin-off" / scripts.name
lib = off / "gate-echo-lib.sh"
text = lib.read_text()
mark = "hook_journal_append() {\n"
assert text.count(mark) == 1
lib.write_text(text.replace(mark, mark + "    return 0      # bench: the line switched off\n", 1))
guard = off / "command_guard.py"
text = guard.read_text()
mark = '    sys.exit(_journal_main())'
assert text.count(mark) == 1
guard.write_text(text.replace(mark, "    sys.exit(main())", 1))

proj = work / "proj"
(proj / ".agent/tasks/001-x").mkdir(parents=True)
(proj / ".agent/tasks/001-x/task.md").write_text("# 001 - x\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] g\n")
(proj / ".agent/sessions/pid-b222x").mkdir(parents=True)
(proj / ".agent/sessions/pid-b222x/current_state").write_text("001\n")
proc = work / "proc"
(proc / "self").mkdir(parents=True)
env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV", "PLAYBOOK_ROLE")}
env.update(PLAYBOOK_SESSION_ID="pid-b222x", HOME=str(work), PLAYBOOK_PROC_ROOT=str(proc))
pre = {"session_id": "h", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
       "tool_use_id": "toolu_B"}
post = dict(pre, hook_event_name="PostToolUse", tool_response={})
cases = [("task-gate-hook", pre), ("command-guard-hook", pre), ("state-echo-hook", post),
         ("stop-hook", {"session_id": "h", "hook_event_name": "Stop", "stop_hook_active": True})]


def run(d, hook, payload):
    """(wall ms, CPU ms): the CPU time (user + system) of the hook and every process it started — far less moved by
    what else the host is doing than the wall clock (a loaded host showed -4 ms "added" on the wall clock)."""
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.perf_counter()
    subprocess.run(["bash", str(d / hook)], input=json.dumps(payload), env=env, cwd=proj, capture_output=True,
                   text=True)
    t1 = time.perf_counter()
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (t1 - t0) * 1000, (r1.ru_utime + r1.ru_stime - r0.ru_utime - r0.ru_stime) * 1000


for hook, payload in cases:
    on, offs = [], []
    for _ in range(M):
        on.append(run(scripts, hook, payload))
        offs.append(run(off, hook, payload))
    aw, bw = statistics.median(x for x, _ in on), statistics.median(x for x, _ in offs)
    ac, bc = statistics.median(c for _, c in on), statistics.median(c for _, c in offs)
    print(f"{hook:<20} wall {aw:7.2f} vs {bw:7.2f} ms ({aw - bw:+6.2f})   CPU {ac:7.2f} vs {bc:7.2f} ms "
          f"({ac - bc:+6.2f})  median of {M}")
lines = (proj / ".agent/journal/hooks.jsonl").read_text().splitlines()
print(f"journal lines written by the 'with' runs: {len(lines)} (expected {M * len(cases)})")
