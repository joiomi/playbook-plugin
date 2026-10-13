"""Live rows of the session group (task 184, W3): real headless claude sessions on the CANDIDATE.

Each session runs DETACHED in a scratch project that disables the owner's installed copy and
loads the candidate with --plugin-dir (`common.session_project`, `common.claude_session`). What
a clause reads is what the hooks themselves left — the enforcement journal, the chat log, the
files, and the hook responses Claude Code reports in its event stream — never the agent's own
account of what happened. The model is haiku: the sessions test the plugin, not the model."""
from __future__ import annotations

import json
import re
from pathlib import Path

from common import PLUGIN, Run, claude_session, journal, session_project, tempdir, tool_calls, tool_results

RULES = ("This is a test of the Playbook plugin's hooks in a scratch project made for it. Do EXACTLY the numbered "
         "steps, in order, with your tools. If a hook blocks a step, do NOT retry it, do NOT work around it and do NOT "
         "ask: go on to the next step. If a stop hook tells you gates are open, reply with the single word NOTED and "
         "end. Use `.claude/bin/tasks` for every task command.\n\n")


def hook_responses(events: "list[dict]", event: str) -> "list[dict]":
    return [e for e in events if e.get("type") == "system" and e.get("subtype") == "hook_response"
            and e.get("hook_event") == event]


def stop_rounds(events: "list[dict]") -> "list[list[dict]]":
    """The Stop hook responses grouped by stop: every Stop hook of one stop starts before the
    session moves on, so a run of Stop hook events with no message between them is one stop."""
    rounds: "list[list[dict]]" = []
    open_round = False
    for e in events:
        if e.get("type") == "system" and e.get("hook_event") == "Stop":
            if not open_round:
                rounds.append([])
                open_round = True
            if e.get("subtype") == "hook_response":
                rounds[-1].append(e)
        elif e.get("type") in ("assistant", "user", "result"):
            open_round = False
    return rounds


def blocked(stop: "list[dict]") -> bool:
    return any(x.get("exit_code") == 2 for x in stop)


def resume_named(events: "list[dict]") -> "tuple[bool, str]":
    """The PreToolUse:Edit response that refused an edit on the BLOCKED task names `tasks work <N>` as the
    resume. Read in that refusal only: the session's own prompt, echoed back elsewhere, says `tasks work 1`
    too (impl panel I3)."""
    refusals = [str(e.get("output") or "") for e in hook_responses(events, "PreToolUse")
                if str(e.get("hook_name", "")).startswith("PreToolUse:Edit") and "is blocked" in str(e.get("output") or "")]
    named = [x for x in refusals if re.search(r"tasks work 0*1\b", x)]
    return bool(named), f"refusals of the blocked task: {len(refusals)}, naming `tasks work 0*1`: {len(named)}"


def parallel_echoes(events: "list[dict]", name: str = "Read", line: str = "Working on task [001]") -> "tuple[bool, str]":
    """Every call of `name` in the one message that made the most of them got its OWN PostToolUse response
    carrying the gate line (impl panel I4). Hook events carry no tool_use_id; Claude Code reports a call's
    PostToolUse responses after the call and before its result, so a call's window is (its tool_use, its
    tool_result) and each response is given to one call only."""
    use_at: "dict[str, tuple[int, str]]" = {}
    result_at: "dict[str, int]" = {}
    for i, e in enumerate(events):
        for c in (e.get("message") or {}).get("content") or [] if e.get("type") in ("assistant", "user") else []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use" and c.get("name") == name:
                use_at[str(c.get("id"))] = (i, str((e.get("message") or {}).get("id")))
            elif c.get("type") == "tool_result":
                result_at[str(c.get("tool_use_id"))] = i
    per: "dict[str, list[str]]" = {}
    for tid, (_i, mid) in use_at.items():
        per.setdefault(mid, []).append(tid)
    calls = max(per.values(), key=len, default=[])
    taken: "set[int]" = set()
    echoed = 0
    for tid in sorted(calls, key=lambda x: use_at[x][0]):
        lo, hi = use_at[tid][0], result_at.get(tid, -1)
        for i in range(lo + 1, hi):
            e = events[i]
            if (i not in taken and e.get("type") == "system" and e.get("subtype") == "hook_response"
                    and e.get("hook_name") == f"PostToolUse:{name}" and line in str(e.get("output") or "")):
                taken.add(i)
                echoed += 1
                break
    return len(calls) >= 2 and echoed == len(calls), f"{name} calls in one message: {len(calls)}, each with its own echo: {echoed}"


def freehand_logged(task_text: str, results: "list[str]") -> "tuple[bool, str]":
    """`tasks freehand log` put the span's messages right under the `Freehand log` gate: counted only in the
    block between that gate line and the next gate or heading (the activation's Recent Chat, elsewhere in the
    file, holds messages too — impl panel I9), and the CLI said it inserted at least one."""
    m = re.search(r"(?m)^- \[[ x]\] Freehand log\b.*$", task_text)
    block = ""
    if m:
        rest = task_text[m.end():]
        stop = re.search(r"(?m)^(- \[[ x]\] |#{1,6} )", rest)
        block = rest[:stop.start()] if stop else rest
    n = len(re.findall(r"(?m)^\*\*\[M\d+\]\*\*", block))
    said = [int(x) for r in results for x in re.findall(r"Inserted (\d+) chat_log messages", r)]
    return n >= 1 and any(x >= 1 for x in said), f"messages under the gate: {n}; the CLI said inserted: {said}"


def _proj(prefix: str, **kw) -> Path:
    p = session_project(tempdir(prefix) / "proj", **kw)
    (p / "src").mkdir(exist_ok=True)
    (p / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    return p


def _blocks(rows, reason_part):
    return [j for j in rows if j.get("hook") == "task-gate" and j.get("decision") == "block"
            and reason_part in str(j.get("reason", ""))]


def task_activate(out: Path) -> int:
    run = Run("PB-TASK-ACTIVATE", "a real session reaches the activation / edit-gate boundary", out, ["claude"])
    p = _proj("pb184-act-")
    _r, ev = claude_session(run, "no task, then a pending one, then a blocked one", p, RULES + (
        "1. Edit src/app.py: change `x = 1` to `x = 2`.\n"
        "2. Run: .claude/bin/tasks new quick act-probe \"a probe task\"\n"
        "3. Run: .claude/bin/tasks work 1\n"
        "4. Edit src/app.py: change `x = 1` to `x = 2` (read it first).\n"
        "5. Run: .claude/bin/tasks blocked \"waiting for the owner\"\n"
        "6. Edit src/app.py: change `x = 2` to `x = 3` (read it first).\n"
        "Then reply DONE.\n"))
    j = journal(p)
    run.note("journal:\n" + "\n".join(json.dumps(x) for x in j))
    run.clause("with no active task the session's code edit is refused at the gate (journaled)",
               len(_blocks(j, "no active task")) >= 1, f"no-active-task blocks: {len(_blocks(j, 'no active task'))}")
    run.clause("with the session's pointer on a real pending task the same edit goes through",
               (p / "src" / "app.py").read_text(encoding="utf-8").startswith("x = 2"),
               f"src/app.py now: {(p / 'src' / 'app.py').read_text(encoding='utf-8').strip()!r}")
    blocked = _blocks(j, "pointer to blocked task")
    named, said = resume_named(ev)
    run.clause("with the task blocked (paused on the owner) the next edit is refused, and the refusal names "
               "`tasks work <N>` as the resume", bool(blocked) and named
               and (p / "src" / "app.py").read_text(encoding="utf-8").startswith("x = 2"),
               f"blocked-task refusals (journal): {[x.get('reason') for x in blocked]}; {said}")
    return run.finish()


def hook_invocation(out: Path) -> int:
    run = Run("PB-PROVIDER-HOOK-INVOCATION", "real allow/block, prompt attribution, state echo and stop events",
              out, ["claude"])
    p = _proj("pb184-inv-")
    _r, ev = claude_session(run, "an active task, an allowed edit, a destructive command, a stop", p, RULES + (
        "1. Run: .claude/bin/tasks new quick inv-probe \"a probe task\"\n"
        "2. Run: .claude/bin/tasks work 1\n"
        "3. Edit src/app.py: change `x = 1` to `x = 2` (read it first).\n"
        "4. Run exactly this shell command — this scratch project has NO remote, so it cannot push anything, and a "
        "hook is EXPECTED to refuse it: git push --force origin main\n"
        "Then reply DONE.\n"))
    j = journal(p)
    run.note("journal:\n" + "\n".join(json.dumps(x) for x in j))
    log = (p / ".agent" / "chat_log.md").read_text(encoding="utf-8") if (p / ".agent" / "chat_log.md").exists() else ""
    post = hook_responses(ev, "PostToolUse")
    stops = stop_rounds(ev)
    run.clause("the edit is allowed through the task gate (the file changed)",
               (p / "src" / "app.py").read_text(encoding="utf-8").startswith("x = 2"), "src/app.py x = 2")
    pushed = [i.get("command", "") for n, i in tool_calls(ev) if n == "Bash" and "push --force" in i.get("command", "")]
    run.clause("the shell event invokes the command guard, which blocks a force push (journaled) — the model issued the "
               "call, so the hook saw the event", bool(pushed) and any(x.get("hook") == "command-guard"
                                                                          and x.get("decision") == "block"
                                                                          and x.get("reason") == "git-push-force" for x in j),
               f"calls: {pushed}; guard decisions: {[(x.get('decision'), x.get('reason')) for x in j if x.get('hook') == 'command-guard']}")
    run.clause("the prompt is attributed in the chat log to this claude session", "a probe task" in log
               and "(claude/pid-" in log, f"chat log has the prompt: {'a probe task' in log}; tag: {'(claude/pid-' in log}")
    echoed = [x for x in post if "Working on task [001]" in json.dumps(x)]
    run.clause("the state echo runs after the tool events and reports the active gate", bool(echoed),
               f"{len(post)} PostToolUse hook responses, {len(echoed)} naming task [001]")
    run.clause("the stop event invokes the stop hook (its gate line blocks the first stop)",
               bool(stops) and any("unchecked gate" in (x.get("stderr") or "") for x in stops[0]),
               f"stops: {[[(x.get('exit_code'), (x.get('stderr') or '')[:60]) for x in s] for s in stops]}")
    return run.finish()


def state_echo(out: Path) -> int:
    run = Run("PB-HOOK-STATE-ECHO", "the state echo after real tool events, parallel ones too", out, ["claude"])
    p = _proj("pb184-echo-")
    (p / "src" / "b.py").write_text("y = 1\n", encoding="utf-8")
    _r, ev = claude_session(run, "two reads issued in ONE message, then a gate ticked", p, RULES + (
        "1. Run: .claude/bin/tasks new quick echo-probe \"a probe task\"\n"
        "2. Run: .claude/bin/tasks work 1\n"
        "3. In ONE single message, call your Read tool TWICE in parallel: on src/app.py and on src/b.py.\n"
        "4. Edit .agent/tasks/001-echo-probe/task.md: change the first `- [ ]` on a line that starts a gate in "
        "`## Risk Routing` or `## Work` to `- [x]` and append ` — probe` to that line.\n"
        "Then reply DONE.\n"))
    post = hook_responses(ev, "PostToolUse")
    ctx = [json.dumps(x) for x in post]
    both, said = parallel_echoes(ev)
    run.clause("after each real tool event the state echo reports the task and its current gate",
               sum("Working on task [001]" in c for c in ctx) >= 2, f"{len(post)} PostToolUse responses, "
               f"{sum('Working on task [001]' in c for c in ctx)} with the gate line")
    run.clause("two tool calls issued in one message are both echoed, each by its own response", both, said)
    done = [c for c in ctx if "previous gate done" in c or "✓" in c]
    run.clause("ticking a gate is noticed by the echo (the completion line)", bool(done),
               f"echo lines with a completion: {len(done)}")
    run.clause("no malformed payload broke the session's state (every PostToolUse response succeeded)",
               all(x.get("outcome") == "success" for x in post), f"outcomes: {sorted({x.get('outcome') for x in post})}")
    return run.finish()


def stop_close(out: Path) -> int:
    run = Run("PB-HOOK-STOP-CLOSE", "provider-native stop with an OPEN task and with a COMPLETE one", out, ["claude"])
    p = _proj("pb184-stop-")
    _r, ev = claude_session(run, "an open task and an edit, then the turn ends", p, RULES + (
        "1. Run: .claude/bin/tasks new quick stop-probe \"a probe task\"\n"
        "2. Run: .claude/bin/tasks work 1\n"
        "3. Edit src/app.py: change `x = 1` to `x = 2` (read it first).\n"
        "Then reply DONE.\n"))
    stops = stop_rounds(ev)
    run.note("stops (each: its hooks' exit codes and stderr): " + json.dumps(
        [[(x.get("exit_code"), (x.get("stderr") or "")[:160]) for x in s] for s in stops]))
    first = stops[0] if stops else []
    run.clause("with gates open after a turn that wrote, the FIRST stop is blocked by the hook's own line",
               blocked(first) and any("unchecked gate" in (x.get("stderr") or "") for x in first),
               f"first stop: {[(x.get('exit_code'), (x.get('stderr') or '')[:120]) for x in first]}")
    run.clause("the one-shot valve: the re-issued stop is let through, the session is not trapped",
               len(stops) >= 2 and not blocked(stops[-1]) and _r.returncode == 0,
               f"stops blocked: {[blocked(s) for s in stops]}; session rc {_r.returncode}")
    # a COMPLETE task: every gate checked before the turn ends
    p2 = _proj("pb184-stop2-")
    _r2, ev2 = claude_session(run, "a task whose gates are all checked, then an edit and the turn ends", p2, RULES + (
        "1. Run: .claude/bin/tasks new quick done-probe \"a probe task\"\n"
        "2. Run: .claude/bin/tasks work 1\n"
        "3. Edit src/app.py: change `x = 1` to `x = 2` (read it first).\n"
        "4. Read .agent/tasks/001-done-probe/task.md. Then, for EACH line that contains `- [ ]`, make ONE separate "
        "Edit that changes that line's `- [ ]` to `- [x]` and appends ` — done in the probe session` to the end of "
        "that same line. One Edit per line, one line per Edit, top to bottom.\n"
        "Then reply DONE.\n"))
    stops2 = stop_rounds(ev2)
    text = (p2 / ".agent" / "tasks" / "001-done-probe" / "task.md").read_text(encoding="utf-8")
    open_gates = re.findall(r"(?m)^\s*- \[ \]", text)
    run.clause("with every gate checked, the first stop is let through", not open_gates and bool(stops2)
               and not blocked(stops2[0]), f"open gate lines left: {len(open_gates)}; "
               f"stops blocked: {[blocked(s) for s in stops2]}")
    # task 193 (PLAN S14 T4, #38): a `claude -p` the session's agent starts resolves to the SAME session, and its
    # prompt reset the outer turn's counts (measured before the fix: `writes=2 tools=3`, then 0) — a turn that had
    # worked could end as a chat reply with its gates open. Its prompt must leave them as they were.
    p3 = _proj("pb193-nest-")
    nested = (f"claude -p --plugin-dir {PLUGIN} --model haiku --setting-sources project,local "
              "--dangerously-skip-permissions 'Reply with the single word OK.'")
    _r3, ev3 = claude_session(run, "the agent starts a nested claude -p in the middle of its turn", p3, RULES + (
        "1. Run: .claude/bin/tasks new quick nest-probe \"a probe task\"\n"
        "2. Run: .claude/bin/tasks work 1\n"
        "3. Read src/app.py.\n"
        "4. Run exactly: cat .agent/sessions/*/counters\n"
        f"5. Run exactly: {nested}\n"
        "6. Run exactly: cat .agent/sessions/*/counters\n"
        "Then reply DONE.\n"), timeout=600)
    seen = [int(m.group(1)) for out in tool_results(ev3) for m in [re.search(r"(?m)^tools=(\d+)$", out)] if m]
    nested_ran = any("--plugin-dir" in i.get("command", "") and "claude -p" in i.get("command", "")
                     for n, i in tool_calls(ev3) if n == "Bash")
    run.clause("a nested `claude -p`'s prompt leaves the outer turn's counts as they were (task 193, #38): the "
               "count read after it is higher than the one read before it", nested_ran and len(seen) >= 2
               and seen[-1] > seen[0], f"nested call issued: {nested_ran}; `tools` read before/after: {seen}")
    return run.finish()


def freehand(out: Path) -> int:
    run = Run("PB-TASK-FREEHAND", "freehand, then a tracked task: the gates re-arm", out, ["claude"])
    p = _proj("pb184-free-")
    _r, ev = claude_session(run, "freehand: an edit, the turn ends", p, RULES + (
        "1. Run: .claude/bin/tasks freehand\n"
        "2. Edit src/app.py: change `x = 1` to `x = 2` (read it first).\n"
        "Then reply DONE.\n"))
    stops = stop_rounds(ev)
    task = next((p / ".agent" / "tasks").glob("*-freehand/task.md"), None)
    run.clause("freehand starts a task whose first live gate is `Freehand`, with its timestamped start marker",
               task is not None and "<!-- freehand-start:" in task.read_text(encoding="utf-8"),
               f"task: {task}")
    run.clause("in freehand the edit is allowed and the stop is released (no block)",
               (p / "src" / "app.py").read_text(encoding="utf-8").startswith("x = 2") and bool(stops)
               and not any(blocked(s) for s in stops), f"stops blocked: {[blocked(s) for s in stops]}")
    # the transition: the freehand log captured, then work goes to a tracked task and the gates hold again
    _r2, ev2 = claude_session(run, "the transition: log the freehand span, then a tracked task and an edit", p, RULES + (
        "1. Run: .claude/bin/tasks work 1\n"
        "2. Run: .claude/bin/tasks freehand log\n"
        "3. Run: .claude/bin/tasks new quick tracked \"the tracked task\"\n"
        "4. Run: .claude/bin/tasks work 2 --force\n"
        "5. Edit src/app.py: change `x = 2` to `x = 3` (read it first).\n"
        "Then reply DONE.\n"))
    text = task.read_text(encoding="utf-8") if task else ""
    stops2 = stop_rounds(ev2)
    logged, said = freehand_logged(text, tool_results(ev2))
    run.clause("`tasks freehand log` records the span from the chat log under the `Freehand log` gate", logged, said)
    run.clause("after the switch to a tracked task, a turn that wrote is stopped by the re-armed gates",
               bool(stops2) and blocked(stops2[0]), f"stops blocked: {[blocked(s) for s in stops2]}")
    return run.finish()


def lane_resolution(out: Path) -> int:
    run = Run("PB-LANE-RESOLUTION", "a two-lane repository with a real session; and a fresh clone with no marker",
              out, ["claude"])
    p = _proj("pb184-lane-", lanes=("alice", "bob"), marker="alice")
    (p / ".agent" / "bob" / "tasks" / "001-bobs").mkdir(parents=True)
    (p / ".agent" / "bob" / "tasks" / "001-bobs" / "task.md").write_text("# 001 - Bobs\n\n## Status\npending\n")
    _r, ev = claude_session(run, "alice's lane: a task, an edit, a command", p, RULES + (
        "1. Run: .claude/bin/tasks list\n"
        "2. Run: .claude/bin/tasks new quick alices \"alice's task\"\n"
        "3. Run: .claude/bin/tasks work 1\n"
        "4. Edit src/app.py: change `x = 1` to `x = 2` (read it first).\n"
        "Then reply DONE.\n"))
    a, root = p / ".agent" / "alice", p / ".agent"
    listed = " ".join(tool_results(ev))
    run.clause("the session's state lands in alice's lane: her task, her chat log, her journal",
               (a / "tasks" / "001-alices" / "task.md").exists() and (a / "chat_log.md").exists()
               and bool(journal(p, "alice")), f"task {(a / 'tasks' / '001-alices').exists()}, chat log "
               f"{(a / 'chat_log.md').exists()}, journal rows {len(journal(p, 'alice'))}")
    run.clause("nothing is invented at the root: no root tasks, sessions, chat log or journal",
               not (root / "tasks").exists() and not (root / "sessions").exists() and not (root / "chat_log.md").exists()
               and not (root / "journal").exists(), f"root entries: {sorted(x.name for x in root.iterdir())}")
    run.clause("the CLI in the session reads alice's lane (bob's task is not listed)", "Bobs" not in listed
               and "001-bobs" not in listed, "bob's task absent from `tasks list`")
    # a fresh clone: the lanes are there, the gitignored marker is not
    q = _proj("pb184-lane2-", lanes=("alice", "bob"))
    _r2, ev2 = claude_session(run, "a fresh multi-user clone with no marker", q, RULES + (
        "1. Run: .claude/bin/tasks list\n"
        "2. Edit src/app.py: change `x = 1` to `x = 2` (read it first).\n"
        "Then reply DONE.\n"))
    rootq = q / ".agent"
    run.clause("on a fresh clone with no marker no surface invents a root lane: no root tasks, chat log, journal, "
               "shell history or session state", not any((rootq / n).exists() for n in
                                                          ("tasks", "chat_log.md", "journal", "bash_history"))
               and not [f for f in (rootq / "sessions").rglob("*") if not f.is_dir()],
               f"root entries: {sorted(x.name for x in rootq.iterdir())}; files under a root sessions/: "
               f"{[str(f.relative_to(q)) for f in (rootq / 'sessions').rglob('*') if not f.is_dir()]}")
    # task 192 (#41): the state echo used to leave an EMPTY per-session directory at the root on every call (the
    # bound task 184 recorded here); it now makes none there
    left = sorted(str(d.relative_to(q)) for d in (rootq / "sessions").glob("*")) if (rootq / "sessions").exists() else []
    run.clause("…and no hook leaves a session directory at the root (task 192, #41)", not (rootq / "sessions").exists(),
               "root sessions/: " + (", ".join(left) or ("present, empty" if (rootq / "sessions").exists() else "absent")))
    run.clause("…and the session is told why (the missing marker is named)", "current_user" in " ".join(tool_results(ev2))
               + json.dumps(hook_responses(ev2, "SessionStart")) + json.dumps(hook_responses(ev2, "PreToolUse")),
               "the marker named in a tool result or a hook response")
    return run.finish()


ROWS = {
    "PB-TASK-ACTIVATE": task_activate,
    "PB-PROVIDER-HOOK-INVOCATION": hook_invocation,
    "PB-HOOK-STATE-ECHO": state_echo,
    "PB-HOOK-STOP-CLOSE": stop_close,
    "PB-TASK-FREEHAND": freehand,
    "PB-LANE-RESOLUTION": lane_resolution,
}
