#!/usr/bin/env python3
"""What the shell log's append costs (task 221; PB-PERFORMANCE-HOOKS). Dev-only, a wall-clock driver, not a budget.

(a) `shell_log_append` alone: N calls, each its own bash process (as each hook run is), against the same bash
    process that only sources the library;
(b) the real state-echo-hook on a Bash payload with a command (one line written) against the same payload without
    one (nothing written), M runs each, interleaved.
Prints medians (and the hook's p90). Usage:
    python3 scripts/bench_shell_log.py <plugins/playbook/scripts> <empty work dir> [N=100] [M=60]
2026-10-16 on the owner's Linux host: (a) +3.30 ms, (b) +5.5 ms median."""
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

scripts, work = Path(sys.argv[1]), Path(sys.argv[2])
N = int(sys.argv[3]) if len(sys.argv) > 3 else 100
M = int(sys.argv[4]) if len(sys.argv) > 4 else 60
proj = work / "proj"
(proj / ".agent/tasks/001-x").mkdir(parents=True)
(proj / ".agent/tasks/001-x/task.md").write_text("# 001 - x\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] g\n")
(proj / ".agent/sessions/pid-m221").mkdir(parents=True)
(proj / ".agent/sessions/pid-m221/current_state").write_text("001\n")
proc = work / "proc"
(proc / "self").mkdir(parents=True)
env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV", "PLAYBOOK_ROLE")}
env.update(PLAYBOOK_SESSION_ID="pid-m221", HOME=str(work), PLAYBOOK_PROC_ROOT=str(proc))
lane = str(proj / ".agent")


def run(argv, inp=None):
    t0 = time.perf_counter()
    subprocess.run(argv, input=inp, env=env, cwd=proj, capture_output=True, text=True)
    return (time.perf_counter() - t0) * 1000



a_on, a_off = [], []
for i in range(N):
    a_on.append(run(["bash", "-c", 'source "$1"; shell_log_append "$2" "ls -la $3"', "_", str(scripts / "gate-echo-lib.sh"), lane, str(i)]))
    a_off.append(run(["bash", "-c", 'source "$1"; true', "_", str(scripts / "gate-echo-lib.sh")]))
with_cmd = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
no_cmd = json.dumps({"tool_name": "Bash", "tool_input": {}})
b_on, b_off = [], []
for i in range(M):
    b_on.append(run(["bash", str(scripts / "state-echo-hook")], with_cmd))
    b_off.append(run(["bash", str(scripts / "state-echo-hook")], no_cmd))
lines = (proj / ".agent/bash_history").read_text().count("\n")
med = statistics.median
print(f"(a) shell_log_append in its own bash: median {med(a_on):.2f} ms vs empty {med(a_off):.2f} ms -> +{med(a_on)-med(a_off):.2f} ms (n={N})")
print(f"(b) state-echo-hook, Bash with command: median {med(b_on):.1f} ms vs without: {med(b_off):.1f} ms -> +{med(b_on)-med(b_off):.1f} ms (n={M}; p90 {sorted(b_on)[int(M*.9)]:.1f} vs {sorted(b_off)[int(M*.9)]:.1f})")
print(f"lines written: {lines} (expected {N + M})")
