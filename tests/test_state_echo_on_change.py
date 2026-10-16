#!/usr/bin/env python3
"""PHASE-B item 2 (task 219), the owner's 2026-10-16 decision "La schimbare + amintire": the PostToolUse state echo
prints in full only when something changed — the open gate or the task, a warning, the first call of a session, the
first call after a SessionStart (a compaction) — and on the same gate one short line every 10 calls; otherwise `{}`.

"The gate changed" is decided by what the gate IS (its place among the live gates and its text, read fence-aware
through `scripts/task-status.py --echo`), not by its line: lines added above the open gate, or a fenced checklist,
change nothing, and only the previous gate's own line, now checked, is a tick logged as `[x]` in `chat_log.md`.

The hooks run as subprocesses in a temp project, on a real task.md and session pointer (no mocks).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins/playbook"
SCRIPTS = PLUGIN / "scripts"
BASH = shutil.which("bash") or ""
FLOCK = shutil.which("flock") or ""

TASK = """# 001 - Probe

## Status
in_progress

## Risk
reversible

## Intent
probe

## Work
{gates}
"""
GATES = "- [ ] Alpha step\n- [ ] Beta step\n- [ ] Gamma step"
BASH_PAYLOAD = '{"tool_name":"Bash","tool_input":{"command":"ls"},"tool_response":{}}'


def _start_time(pid: int) -> str:
    """A process's start time (/proc/<pid>/stat field 22), what a claim binds its pid to (task 220)."""
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return stat.rsplit(")", 1)[1].split()[19]


@unittest.skipUnless(BASH, "bash not available")
class _Project(unittest.TestCase):
    sid = "pid-echo219"

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb219-"))
        self.addCleanup(self._cleanup)
        # the review-trap checklist: no real HOME, a /proc with no agent in it (a judge's nested run would otherwise
        # walk ITS chain), and none of the host's playbook roles or sandbox flags
        proc = self.d / "proc-no-agent"
        (proc / "self").mkdir(parents=True)
        self.env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID=self.sid,
                        CLAUDE_PLUGIN_ROOT=str(PLUGIN), PLAYBOOK_COUNTERS_LOCK_WAIT="1",
                        HOME=str(self.d / "home"), PLAYBOOK_PROC_ROOT=str(proc))
        (self.d / "home").mkdir()
        for k in ("BASH_ENV", "PLAYBOOK_ROLE", "PLAYBOOK_EVAL_CONFIG", "PLAYBOOK_SANDBOXED"):
            self.env.pop(k, None)
        self.cli("init")
        self.assertEqual(self.cli("new", "quick", "probe", "probe").returncode, 0)
        self.task_md = next((self.d / ".agent" / "tasks").glob("001-*/task.md"))
        self.write(GATES)

    def _cleanup(self):
        for p in self.d.rglob("*"):
            try:
                p.chmod(0o755 if p.is_dir() else 0o644)
            except OSError:
                pass
        shutil.rmtree(self.d, ignore_errors=True)

    def cli(self, *a):
        return subprocess.run([sys.executable, "-m", "tasks.cli", *a], cwd=self.d, env=self.env,
                              capture_output=True, text=True, timeout=60)

    def write(self, gates, intent="probe"):
        self.task_md.write_text(TASK.format(gates=gates).replace("## Intent\nprobe", f"## Intent\n{intent}"),
                                encoding="utf-8")

    def edit(self, old, new):
        s = self.task_md.read_text(encoding="utf-8")
        self.assertIn(old, s)
        self.task_md.write_text(s.replace(old, new, 1), encoding="utf-8")

    def work(self):
        self.assertEqual(self.cli("work", "1").returncode, 0)

    def echo(self, payload=BASH_PAYLOAD, env=None) -> str:
        """The hook's additionalContext, or "" for `{}`."""
        r = subprocess.run([BASH, str(SCRIPTS / "state-echo-hook")], input=payload, cwd=self.d,
                           env=env or self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip() or "{}")
        return (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""

    def side_by_side(self, n, payload=BASH_PAYLOAD) -> "list[str]":
        """n hooks that really overlap (task 219's round 2, codex: `communicate` one by one ran them in turn): every
        process gets its whole stdin before any output is read."""
        procs = [subprocess.Popen([BASH, str(SCRIPTS / "state-echo-hook")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=self.d, env=self.env,
                                  text=True) for _ in range(n)]
        for p in procs:
            assert p.stdin is not None
            p.stdin.write(payload)
            p.stdin.close()
        outs = []
        for p in procs:
            assert p.stdout is not None
            o = p.stdout.read()
            p.wait(timeout=60)
            outs.append((json.loads(o.strip() or "{}").get("hookSpecificOutput") or {}).get("additionalContext") or "")
        return outs

    def chat_log(self):
        p = self.d / ".agent" / "chat_log.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def session_dir(self):
        return self.d / ".agent" / "sessions" / self.sid


class OneGate(_Project):
    def test_30_calls_one_full_echo_then_a_line_every_10(self):
        self.work()
        outs = [self.echo() for _ in range(30)]
        self.assertIn("# Working on task [001]", outs[0])
        self.assertIn("Alpha step", outs[0])
        printed = [i for i, o in enumerate(outs) if o]
        self.assertEqual(printed, [0, 10, 20], outs)
        for i in (10, 20):
            self.assertEqual(len(outs[i].splitlines()), 1, outs[i])
            self.assertIn("still at gate", outs[i])
            self.assertIn("Alpha step", outs[i])
            self.assertIn("task.md:", outs[i])

    def test_the_first_call_of_a_session_says_no_transition(self):
        # the counters hold no gate yet: no "switched" / "done" line and no chat_log entry (an empty task field once
        # read as "0" through a tab-split `read` — measured during the build)
        self.work()
        out = self.echo()
        self.assertTrue(out.startswith("# Working on task [001]"), out)
        self.assertNotIn("switched", out)
        self.assertNotIn("**[G", self.chat_log())

    def test_printed_count_for_n_calls(self):
        self.work()
        for n in range(1, 36):
            out = self.echo()
            if out:
                self.assertTrue(n == 1 or (n - 1) % 10 == 0, (n, out))
            else:
                self.assertFalse(n == 1 or (n - 1) % 10 == 0, n)


class Transitions(_Project):
    def setUp(self):
        super().setUp()
        self.work()
        self.assertIn("Alpha step", self.echo())

    def test_a_tick_with_a_note_prints_in_full_and_logs_x(self):
        self.edit("- [ ] Alpha step", "- [x] Alpha step — done, checked")
        out = self.echo()
        self.assertIn("previous gate done", out)
        self.assertIn("Beta step", out)
        self.assertEqual(self.chat_log().count("- [x] Alpha step"), 1, self.chat_log())

    def test_lines_inserted_above_change_nothing(self):
        before = self.chat_log()
        self.edit("## Intent\nprobe", "## Intent\nprobe\none\ntwo\nthree\nfour\nfive")
        self.assertEqual(self.echo(), "")
        self.assertEqual(self.chat_log(), before)

    def test_a_fenced_checklist_above_changes_nothing(self):
        before = self.chat_log()
        self.edit("## Intent\nprobe", "## Intent\nprobe\n```text\n- [x] fake done\n- [ ] fake open\n```")
        self.assertEqual(self.echo(), "")
        self.assertEqual(self.chat_log(), before)

    def test_a_gate_added_above_is_a_change_not_a_tick(self):
        self.edit("- [ ] Alpha step", "- [ ] Zero step\n- [ ] Alpha step")
        out = self.echo()
        self.assertIn("Zero step", out)
        self.assertIn("open gate changed", out)
        self.assertNotIn("previous gate done", out)
        log = self.chat_log()
        self.assertNotIn("- [x] Alpha step", log)
        self.assertIn("- [ ] Alpha step (left open", log)

    def test_the_open_gate_reworded_prints_in_full_without_an_entry(self):
        before = self.chat_log()
        self.edit("- [ ] Alpha step", "- [ ] Alpha step, reworded")
        out = self.echo()
        self.assertIn("Alpha step, reworded", out)
        self.assertNotIn("previous gate done", out)
        self.assertEqual(self.chat_log(), before)

    def test_a_gate_below_ticked_warns_and_logs_nothing_done(self):
        self.edit("- [ ] Beta step", "- [x] Beta step — early")
        out = self.echo()
        self.assertIn("Alpha step", out)
        self.assertIn("# ⚠ A gate below the open one", out)
        self.assertIn("was checked while it stays open", out)
        self.assertNotIn("previous gate done", out)
        self.assertNotIn("- [x] Alpha step", self.chat_log())

    def test_a_bare_tick_after_lines_moved_names_its_line(self):
        self.edit("## Intent\nprobe", "## Intent\nprobe\nx\ny\nz")
        self.edit("- [ ] Alpha step", "- [x] Alpha step")
        out = self.echo()
        self.assertIn("previous gate done", out)
        line = next(i for i, l in enumerate(self.task_md.read_text(encoding="utf-8").split("\n"), 1)
                    if l == "- [x] Alpha step")
        self.assertIn("bare", out)
        self.assertIn(f"line {line}", out)


class Identity(_Project):
    def test_duplicate_gate_texts_the_first_ticked_is_done(self):
        self.write("- [ ] Same step\n- [ ] Same step\n- [ ] Gamma step")
        self.work()
        self.echo()
        self.edit("- [ ] Same step", "- [x] Same step — one")
        out = self.echo()
        self.assertIn("previous gate done", out)
        self.assertEqual(self.chat_log().count("- [x] Same step"), 1)

    def test_gates_that_differ_after_200_characters_are_told_apart(self):
        a, b = "L" * 210 + " one", "L" * 210 + " two"
        self.write(f"- [ ] {a}\n- [ ] {b}")
        self.work()
        self.echo()
        self.edit(f"- [ ] {a}", f"- [x] {a} — done")
        out = self.echo()
        self.assertIn("previous gate done", out)

    def test_a_task_switch_keeps_todays_switch_entry(self):
        self.assertEqual(self.cli("new", "quick", "other", "other").returncode, 0)
        self.work()
        self.echo()
        self.assertEqual(self.cli("work", "2", "--force").returncode, 0)
        out = self.echo()
        self.assertIn("switched from task 001", out)
        self.assertIn("(left open — switched to task 002)", self.chat_log())


class Resets(_Project):
    def test_a_session_start_makes_the_next_call_print_in_full(self):
        self.work()
        self.echo()
        self.assertEqual(self.echo(), "")
        r = subprocess.run([BASH, str(SCRIPTS / "session-start-hook")],
                           input=json.dumps({"session_id": "x", "source": "compact"}),
                           cwd=self.d, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.echo()
        self.assertTrue(out)
        self.assertIn("# Working on task [001]", out)
        self.assertEqual(self.echo(), "")

    def test_a_prompt_does_not_reset_the_quiet_calls(self):
        self.work()
        self.echo()
        self.assertEqual(self.echo(), "")
        r = subprocess.run([BASH, str(SCRIPTS / "chat-log-hook")], input=json.dumps({"prompt": "hello"}),
                           cwd=self.d, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.echo(), "")


class OtherStates(_Project):
    def _shape(self, first_marker):
        outs = [self.echo() for _ in range(11)]
        self.assertIn(first_marker, outs[0])
        self.assertEqual([i for i, o in enumerate(outs) if o], [0, 10], outs)
        self.assertEqual(len(outs[10].splitlines()), 1, outs[10])
        return outs

    def test_no_active_task(self):
        self._shape("No active task")
        self.work()
        self.assertIn("# Working on task [001]", self.echo())

    def test_freehand(self):
        self.write("- [x] Alpha step — done\n- [ ] Freehand — discuss with the user")
        self.work()
        self._shape("Freehand mode")

    def test_all_gates_done(self):
        self.write("- [x] Alpha step — done\n- [x] Beta step — done")
        self.work()
        self._shape("all gates done")


class AlwaysFull(_Project):
    def test_eval_mode_prints_every_call(self):
        cfg = self.d / "eval.json"
        cfg.write_text('{"gate_echo": "on", "nudges": "typed"}', encoding="utf-8")
        env = dict(self.env, PLAYBOOK_EVAL_CONFIG=str(cfg))
        self.work()
        for _ in range(4):
            self.assertIn("# Working on task [001]", self.echo(env=env))

    def test_a_subagent_call_prints_in_full_and_leaves_the_main_memory(self):
        self.work()
        self.echo()
        sub = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {},
                          "agent_id": "sub-1", "agent_type": "general-purpose"})
        for _ in range(3):
            self.assertIn("# Working on task [001]", self.echo(payload=sub))
        self.assertEqual(self.echo(), "")

    @unittest.skipUnless(FLOCK, "flock not available")
    def test_the_counters_lock_not_given_prints_every_call(self):
        self.work()
        self.echo()
        lock = self.session_dir() / "counters.lock"
        holder = subprocess.Popen([FLOCK, str(lock), "sleep", "30"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        time.sleep(0.3)
        for _ in range(2):
            out = self.echo()
            self.assertTrue(out)
            self.assertIn("Alpha step", out)
            self.assertIn("counters lock", out)

    def test_an_unwritable_agent_dir_prints_every_call(self):
        self.work()
        (self.d / ".agent").chmod(0o555)
        try:
            for _ in range(3):
                out = self.echo()
                self.assertTrue(out)
                self.assertIn("Alpha step", out)
        finally:
            (self.d / ".agent").chmod(0o755)

    def test_a_failed_json_step_prints_nothing_and_the_next_call_prints_in_full(self):
        self.work()
        shim = self.d / "shim"
        shim.mkdir()
        real = shutil.which("python3")
        (shim / "python3").write_text(
            "#!/bin/bash\n"
            'case "$*" in *json.dumps*) exit 1 ;; esac\n'
            f'exec "{real}" "$@"\n', encoding="utf-8")
        (shim / "python3").chmod(0o755)
        broken = dict(self.env, PATH=f"{shim}:{self.env['PATH']}")
        self.assertEqual(self.echo(env=broken), "")
        out = self.echo()
        self.assertTrue(out)
        self.assertIn("# Working on task [001]", out)


class SideBySide(_Project):
    def test_six_hooks_after_a_tick_print_one_full_echo(self):
        self.work()
        self.echo()
        self.edit("- [ ] Alpha step", "- [x] Alpha step — done")
        outs = self.side_by_side(6)
        self.assertEqual(sum(1 for o in outs if o and "previous gate done" in o), 1, outs)
        self.assertEqual(sum(1 for o in outs if o), 1, outs)
        self.assertEqual(self.chat_log().count("- [x] Alpha step"), 1)
        rest = [self.echo() for _ in range(5)]
        self.assertEqual([i for i, o in enumerate(rest) if o], [4], rest)


class Round2(_Project):
    """Impl round 1's findings at the hook (B1, B4-B7, B10, B11)."""

    def test_a_tick_with_the_note_inside_the_title_is_logged(self):
        self.write("- [ ] W3 build it\n- [ ] W4 next")
        self.work()
        self.echo()
        self.edit("- [ ] W3 build it", "- [x] W3 — 10:57 (`date`): done — build it")
        out = self.echo()
        self.assertIn("previous gate done", out)
        self.assertIn("W4 next", out)
        self.assertEqual(self.chat_log().count("- [x] W3 build it"), 1, self.chat_log())

    def test_a_tick_a_subagent_sees_first_reaches_the_main_agent(self):
        self.work()
        self.echo()
        self.edit("- [ ] Alpha step", "- [x] Alpha step — done")
        sub = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}, "tool_response": {},
                          "agent_id": "sub-1"})
        out = self.echo(payload=sub)
        self.assertIn("Beta step", out)
        self.assertNotIn("previous gate done", out)
        self.assertNotIn("- [x] Alpha step", self.chat_log())
        out = self.echo()
        self.assertIn("previous gate done", out)
        self.assertEqual(self.chat_log().count("- [x] Alpha step"), 1)

    def test_a_hook_killed_while_answering_a_tick_leaves_it_to_the_next_call(self):
        self.work()
        self.echo()
        self.edit("- [ ] Alpha step", "- [x] Alpha step — done")
        shim = self.d / "killshim"
        shim.mkdir()
        real = shutil.which("python3")
        # on the answer's JSON step, kill THIS test's hook: climb only the UNBROKEN chain of the hook's own
        # subshells from the parent up (each names the hook in its command line) and kill the top of it — never
        # an unrelated ancestor that merely mentions the hook's name
        (shim / "python3").write_text(
            "#!/bin/bash\n"
            'case "$*" in *json.dumps*)\n'
            '  p=$PPID; top=""\n'
            '  while [ "$p" -gt 1 ] && tr "\\0" " " < /proc/$p/cmdline 2>/dev/null | grep -qE "^[^ ]*bash [^ ]*/state-echo-hook( |$)"; do\n'
            "    top=$p; p=$(awk '{print $4}' /proc/$p/stat 2>/dev/null || echo 1)\n"
            "  done\n"
            '  [ -n "$top" ] && kill -9 "$top"; exit 1 ;;\n'
            "esac\n"
            f'exec "{real}" "$@"\n', encoding="utf-8")
        (shim / "python3").chmod(0o755)
        env = dict(self.env, PATH=f"{shim}:{self.env['PATH']}")
        r = subprocess.run([BASH, str(SCRIPTS / "state-echo-hook")], input=BASH_PAYLOAD, cwd=self.d, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0, "the hook was not killed")
        out = self.echo()
        self.assertIn("previous gate done", out)
        self.assertIn("Beta step", out)
        self.assertIsNotNone(out)
        self.assertEqual(self.echo(), "")

    def test_a_burst_that_crosses_10_still_gives_the_reminder(self):
        self.work()
        self.echo()
        for _ in range(7):
            self.assertEqual(self.echo(), "")
        outs = self.side_by_side(4)
        later = [self.echo() for _ in range(10)]
        reminders = [o for o in outs + later if "still at gate" in o]
        self.assertGreaterEqual(len(reminders), 1, (outs, later))
        self.assertTrue(any("still at gate" in o for o in outs + later[:2]), (outs, later))

    def test_writes_that_fail_never_leave_a_quiet_call(self):
        self.work()
        self.echo()
        self.session_dir().chmod(0o555)
        try:
            for _ in range(3):
                out = self.echo()
                self.assertIn("write FAILED", out)
                self.assertIn("Alpha step", out)
        finally:
            self.session_dir().chmod(0o755)

    def test_the_reminder_cuts_characters_not_bytes(self):
        self.write("- [ ] " + "ș" * 70)
        self.work()
        outs = [self.echo() for _ in range(11)]
        self.assertIn("still at gate", outs[10])
        self.assertIn("ș" * 60 + "…", outs[10])
        self.assertNotIn("\ufffd", outs[10])

    def _gate_key(self, pos, text):
        s = subprocess.run(["cksum"], input=text.encode("utf-8"), capture_output=True, check=True).stdout.decode()
        return f"gate|001|{pos}|" + s.strip().replace(" ", ".")

    def _set_counter(self, key, value):
        f = self.session_dir() / "counters"
        lines = [l for l in f.read_text(encoding="utf-8").splitlines() if not l.startswith(key + "=")]
        f.write_text("\n".join(lines + [f"{key}={value}"]) + "\n", encoding="utf-8")

    def test_a_change_a_live_hook_has_claimed_is_not_printed_twice(self):
        # the side-by-side call while another hook prints the change: quiet; its claimer dead: printed (B5, deterministic)
        self.work()
        self.echo()
        self.edit("- [ ] Alpha step", "- [x] Alpha step — done")
        self.echo()                                     # the tick printed and acknowledged; now on Beta (position 1)
        self.edit("- [ ] Beta step", "- [ ] Beta step, reworded")
        live = subprocess.Popen(["sleep", "30"])
        self.addCleanup(live.wait)
        self.addCleanup(live.kill)
        key = self._gate_key(1, "Beta step, reworded")
        self._set_counter("gate_echo_claim", f"{key} {live.pid} {_start_time(live.pid)}")
        self.assertEqual(self.echo(), "")
        dead = subprocess.Popen(["true"])
        dead.wait()
        self._set_counter("gate_echo_claim", f"{key} {dead.pid} 1")
        self.assertIn("Beta step, reworded", self.echo())

    def test_a_quiet_call_whose_counters_write_fails_prints_in_full(self):
        # the count of the call is written, then the gate + memory write fails: said, never a quiet call (B7)
        self.work()
        self.echo()
        self.assertEqual(self.echo(), "")
        shim = self.d / "mvshim"
        shim.mkdir()
        real = shutil.which("mv")
        seen = self.d / "mv-seen"
        (shim / "mv").write_text(
            "#!/bin/bash\n"
            f'echo "$PPID" >> "{seen}"\n'
            f'[ "$(grep -c "^$PPID$" "{seen}")" -ge 2 ] && exit 1\n'
            f'exec "{real}" "$@"\n', encoding="utf-8")
        (shim / "mv").chmod(0o755)
        # a Read: the count step writes once (a Bash call writes `tools` and `writes` — its second write would fail
        # first and say the warning for another reason)
        read = '{"tool_name":"Read","tool_input":{"file_path":"x"},"tool_response":{}}'
        out = self.echo(payload=read, env=dict(self.env, PATH=f"{shim}:{self.env['PATH']}"))
        self.assertIn("write FAILED", out)
        self.assertIn("Alpha step", out)

    def test_write_counters_with_no_pair_changes_nothing(self):
        f = self.d / "counters"
        f.write_text("=odd\nk=v\n", encoding="utf-8")
        r = subprocess.run([BASH, "-c", f'source "{SCRIPTS / "gate-echo-lib.sh"}"; write_counters "$1"', "x", str(f)],
                           capture_output=True, text=True, timeout=60, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(f.read_text(encoding="utf-8"), "=odd\nk=v\n")


class Round3(_Project):
    """Task 219's round-2 findings at the hook (task 220: C1, C4 and the Minors)."""

    def _python_shim(self, body: str) -> dict:
        shim = self.d / ("pyshim-" + str(len(list(self.d.glob("pyshim-*")))))
        shim.mkdir()
        real = shutil.which("python3")
        (shim / "python3").write_text("#!/bin/bash\n" + 'case "$*" in *json.dumps*)\n' + body + "\nesac\n"
                                      + f'exec "{real}" "$@"\n', encoding="utf-8")
        (shim / "python3").chmod(0o755)
        return dict(self.env, PATH=f"{shim}:{self.env['PATH']}")

    def _mv_shim(self, fail_from: int) -> dict:
        """an `mv` that fails from the hook's fail_from-th rename on (counted per hook process)"""
        shim = self.d / ("mvshim-" + str(len(list(self.d.glob("mvshim-*")))))
        shim.mkdir()
        seen = shim / "seen"
        (shim / "mv").write_text(
            "#!/bin/bash\n" + f'echo "$PPID" >> "{seen}"\n'
            + f'[ "$(grep -c "^$PPID$" "{seen}")" -ge {fail_from} ] && exit 1\n' + f'exec "{shutil.which("mv")}" "$@"\n',
            encoding="utf-8")
        (shim / "mv").chmod(0o755)
        return {"PATH": str(shim)}

    def test_a_skip_whose_print_failed_is_said_by_the_next_call(self):
        self.work()
        self.echo()
        self.edit("- [ ] Beta step", "- [x] Beta step — early")
        self.assertEqual(self.echo(env=self._python_shim("  exit 1 ;;")), "")
        out = self.echo()
        self.assertIn("# ⚠ A gate below the open one", out)     # C1 (opus, codex)

    def test_a_hook_whose_claim_was_taken_prints_nothing(self):
        self.work()
        self.echo()
        self.edit("- [ ] Alpha step", "- [x] Alpha step — done")
        live = subprocess.Popen(["sleep", "30"])
        self.addCleanup(live.wait)
        self.addCleanup(live.kill)
        counters = self.session_dir() / "counters"
        # while this hook makes its answer, another (live) hook takes the claim
        env = self._python_shim(
            f'  sed -i "s/^gate_echo_claim=\\(.*\\) [0-9]* [0-9]*$/gate_echo_claim=\\1 {live.pid} {_start_time(live.pid)}/" '
            f'"{counters}" ;;')
        self.assertEqual(self.echo(env=env), "")                 # C4 (codex)

    def _stale_shim(self) -> str:
        """a `flock` that, at the hook's SECOND request for the counters lock (the gate's hold), touches task.md first —
        the read made before the hold is then stale, every time"""
        shim = self.d / ("flockshim-" + str(len(list(self.d.glob("flockshim-*")))))
        shim.mkdir()
        seen = shim / "seen"
        (shim / "flock").write_text(
            "#!/bin/bash\n"
            'case " $* " in *" 204 "*)\n'
            f'  echo "$PPID" >> "{seen}"\n'
            f'  [ "$(grep -c "^$PPID$" "{seen}")" -eq 2 ] && touch -d "+1 sec" "{self.task_md}" ;;\n'
            "esac\n"
            f'exec "{FLOCK}" "$@"\n', encoding="utf-8")
        (shim / "flock").chmod(0o755)
        return str(shim)

    @unittest.skipUnless(FLOCK, "flock not available")
    def test_a_stale_call_in_eval_mode_prints_in_full(self):
        cfg = self.d / "eval.json"
        cfg.write_text('{"gate_echo": "on", "nudges": "typed"}', encoding="utf-8")
        self.work()
        env = dict(self.env, PLAYBOOK_EVAL_CONFIG=str(cfg), PATH=f"{self._stale_shim()}:{self.env['PATH']}")
        for _ in range(2):
            self.assertIn("# Working on task [001]", self.echo(env=env))   # opus

    @unittest.skipUnless(FLOCK, "flock not available")
    def test_a_stale_call_after_a_failed_count_write_says_it(self):
        self.work()
        self.echo()
        read = '{"tool_name":"Read","tool_input":{"file_path":"x"},"tool_response":{}}'
        mv = self._mv_shim(1)                                        # the count's write fails
        env = dict(self.env, PATH=f"{self._stale_shim()}:{mv['PATH']}:{self.env['PATH']}")
        self.assertIn("write FAILED", self.echo(payload=read, env=env))   # sonnet, grok

    def test_a_tick_whose_counters_write_fails_leaves_no_pending_lines(self):
        # the gate is not stored, so the next call finds the tick again — nothing must wait in echo_pending (sonnet)
        self.work()
        self.echo()
        self.edit("- [ ] Alpha step", "- [x] Alpha step — done")
        read = '{"tool_name":"Read","tool_input":{"file_path":"x"},"tool_response":{}}'
        mv = self._mv_shim(2)
        self.assertIn("write FAILED", self.echo(payload=read, env=dict(self.env, PATH=f"{mv['PATH']}:{self.env['PATH']}")))
        self.assertFalse((self.session_dir() / "echo_pending").exists())
        self.assertIn("previous gate done", self.echo())

    def _kill_after_mv_shim(self, at: int) -> str:
        """an `mv` that does its rename and then, at the hook's at-th rename, kills the hook (its own subshell chain)"""
        shim = self.d / ("killmv-" + str(len(list(self.d.glob("killmv-*")))))
        shim.mkdir()
        seen = shim / "seen"
        (shim / "mv").write_text(
            "#!/bin/bash\n"
            f'"{shutil.which("mv")}" "$@"; rc=$?\n'
            f'echo "$PPID" >> "{seen}"\n'
            f'if [ "$(grep -c "^$PPID$" "{seen}")" -eq {at} ]; then\n'
            '  p=$PPID; top=""\n'
            '  while [ "$p" -gt 1 ] && tr "\\0" " " < /proc/$p/cmdline 2>/dev/null | grep -qE "^[^ ]*bash [^ ]*/state-echo-hook( |$)"; do\n'
            "    top=$p; p=$(awk '{print $4}' /proc/$p/stat 2>/dev/null || echo 1)\n"
            "  done\n"
            '  [ -n "$top" ] && kill -9 "$top"\n'
            "fi\n"
            "exit $rc\n", encoding="utf-8")
        (shim / "mv").chmod(0o755)
        return str(shim)

    def test_a_hook_killed_right_after_storing_a_skip_leaves_it_to_the_next_call(self):
        # codex: the gate stored, then the pending line — a kill between lost the skip; now the line is written first
        self.work()
        self.echo()
        self.edit("- [ ] Beta step", "- [x] Beta step — early")
        read = '{"tool_name":"Read","tool_input":{"file_path":"x"},"tool_response":{}}'
        env = dict(self.env, PATH=f"{self._kill_after_mv_shim(2)}:{self.env['PATH']}")
        r = subprocess.run([BASH, str(SCRIPTS / "state-echo-hook")], input=read, cwd=self.d, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0, "the hook was not killed")
        self.assertIn("# ⚠ A gate below the open one", self.echo())

    def _kill_before_mv_shim(self, at: int) -> str:
        """an `mv` that, at the hook's at-th rename, kills the hook BEFORE renaming (nothing of that write lands)"""
        shim = self.d / ("killbmv-" + str(len(list(self.d.glob("killbmv-*")))))
        shim.mkdir()
        seen = shim / "seen"
        (shim / "mv").write_text(
            "#!/bin/bash\n"
            f'echo "$PPID" >> "{seen}"\n'
            f'if [ "$(grep -c "^$PPID$" "{seen}")" -eq {at} ]; then\n'
            '  p=$PPID; top=""\n'
            '  while [ "$p" -gt 1 ] && tr "\\0" " " < /proc/$p/cmdline 2>/dev/null | grep -qE "^[^ ]*bash [^ ]*/state-echo-hook( |$)"; do\n'
            "    top=$p; p=$(awk '{print $4}' /proc/$p/stat 2>/dev/null || echo 1)\n"
            "  done\n"
            '  [ -n "$top" ] && kill -9 "$top"; exit 1\n'
            "fi\n"
            f'exec "{shutil.which("mv")}" "$@"\n', encoding="utf-8")
        (shim / "mv").chmod(0o755)
        return str(shim)

    def test_a_line_written_then_seen_again_is_said_once(self):
        # killed after the pending line was written and before the gate was stored: the next call sees the skip again
        self.work()
        self.echo()
        self.edit("- [ ] Beta step", "- [x] Beta step — early")
        read = '{"tool_name":"Read","tool_input":{"file_path":"x"},"tool_response":{}}'
        env = dict(self.env, PATH=f"{self._kill_before_mv_shim(2)}:{self.env['PATH']}")
        r = subprocess.run([BASH, str(SCRIPTS / "state-echo-hook")], input=read, cwd=self.d, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0, "the hook was not killed")
        out = self.echo()
        self.assertEqual(out.count("# ⚠ A gate below the open one"), 1, out)

    def test_the_no_task_path_prints_waiting_lines(self):
        self.echo()                                     # no task: the session directory exists now
        (self.session_dir() / "echo_pending").write_text("# ⚠ A gate below the open one (line 9) was checked",
                                                         encoding="utf-8")
        out = self.echo()
        self.assertIn("No active task", out)
        self.assertIn("A gate below the open one (line 9)", out)    # grok
        self.assertFalse((self.session_dir() / "echo_pending").exists())

    def test_a_tick_whose_counters_write_fails_is_logged_once(self):
        # grok: the `[x]` entry was appended before the counters write; when it failed, the retry appended it again
        self.work()
        self.echo()
        self.edit("- [ ] Alpha step", "- [x] Alpha step — done")
        read = '{"tool_name":"Read","tool_input":{"file_path":"x"},"tool_response":{}}'
        mv = self._mv_shim(2)
        self.echo(payload=read, env=dict(self.env, PATH=f"{mv['PATH']}:{self.env['PATH']}"))
        self.assertEqual(self.chat_log().count("- [x] Alpha step"), 0, self.chat_log())
        self.echo()
        self.assertEqual(self.chat_log().count("- [x] Alpha step"), 1, self.chat_log())

    def test_an_odd_write_counters_says_it(self):
        f = self.d / "counters"
        f.write_text("a=0\n", encoding="utf-8")
        r = subprocess.run([BASH, "-c", f'source "{SCRIPTS / "gate-echo-lib.sh"}"; PLAYBOOK_WRITE_FAILED=false; '
                            f'write_counters "$1" a 1 b; echo "$PLAYBOOK_WRITE_FAILED"', "x", str(f)],
                           capture_output=True, text=True, timeout=60, env=self.env)
        self.assertEqual(r.stdout.strip(), "true", r.stderr)          # agy

    def test_write_counters_refuses_an_odd_number_of_words(self):
        f = self.d / "counters"
        f.write_text("a=0\n", encoding="utf-8")
        r = subprocess.run([BASH, "-c", f'source "{SCRIPTS / "gate-echo-lib.sh"}"; write_counters "$1" a 1 b', "x",
                            str(f)], capture_output=True, text=True, timeout=60, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(f.read_text(encoding="utf-8"), "a=0\n")

    def test_the_no_task_reminder_keeps_a_write_failure_warning(self):
        read = '{"tool_name":"Read","tool_input":{"file_path":"x"},"tool_response":{}}'
        for _ in range(10):
            self.echo(payload=read)
        mv = self._mv_shim(2)
        out = self.echo(payload=read, env=dict(self.env, PATH=f"{mv['PATH']}:{self.env['PATH']}"))
        self.assertIn("No active task", out)
        self.assertIn("write FAILED", out)                        # sonnet

    def test_a_failed_reminder_is_given_again_by_the_next_call(self):
        self.work()
        for _ in range(10):
            self.echo()
        self.assertEqual(self.echo(env=self._python_shim("  exit 1 ;;")), "")
        self.assertIn("still at gate", self.echo())                # grok

    def test_a_claim_of_a_recycled_pid_is_not_live(self):
        self.work()
        self.echo()
        self.edit("- [ ] Alpha step", "- [ ] Alpha step, reworded")
        live = subprocess.Popen(["sleep", "30"])
        self.addCleanup(live.wait)
        self.addCleanup(live.kill)
        key = f"gate|001|0|" + subprocess.run(["cksum"], input=b"Alpha step, reworded", capture_output=True,
                                              check=True).stdout.decode().strip().replace(" ", ".")
        self._set_counter("gate_echo_claim", f"{key} {live.pid} 1")   # a live pid, not the start time it had
        self.assertIn("Alpha step, reworded", self.echo())          # grok

    def _set_counter(self, key, value):
        f = self.session_dir() / "counters"
        lines = [l for l in f.read_text(encoding="utf-8").splitlines() if not l.startswith(key + "=")]
        f.write_text("\n".join(lines + [f"{key}={value}"]) + "\n", encoding="utf-8")


class EchoFields(unittest.TestCase):
    """`scripts/task-status.py --echo <task.md> [<P> <B> <OPENS> <CHECKS> <T>]` — twelve lines: total, checked, the first
    open gate's line, its live position, the checked live gates below it, the transition, the checked line (done only),
    the first open gate's full text, its first 200 and 60 characters, and that text's OPENS (open gates with exactly it)
    and CHECKS (checked gates that are it with one span inserted) — what the next call passes back (task 220)."""

    def run_echo(self, text, *prev):
        d = Path(tempfile.mkdtemp(prefix="pb219e-"))
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "task.md"
        f.write_text(text, encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", str(SCRIPTS / "task-status.py"), "--echo", str(f),
                            *map(str, prev)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.split("\n")
        self.assertEqual(len(lines), 14, r.stdout)         # thirteen fields and the final newline
        keys = ("total", "checked", "line", "pos", "below", "transition", "ticked", "text", "text200", "text60",
                "opens", "checks", "above")
        return dict(zip(keys, lines[:13]))

    def test_no_previous(self):
        f = self.run_echo("## Work\n- [x] A — ok\n- [ ] B\n- [ ] C\n")
        self.assertEqual((f["total"], f["checked"], f["line"], f["pos"], f["below"], f["transition"], f["text"]),
                         ("3", "1", "3", "1", "0", "none", "B"))

    def test_same_and_skip(self):
        self.assertEqual(self.run_echo("## Work\n- [ ] A\n- [ ] B\n", 0, 0, 1, 0, 0, "A")["transition"], "same")
        self.assertEqual(self.run_echo("## Work\n- [ ] A\n- [x] B — early\n", 0, 0, 1, 0, 0, "A")["transition"], "skip")

    def test_done_and_done_bare(self):
        f = self.run_echo("## Work\nnew line\n- [x] A — ok\n- [ ] B\n", 0, 0, 1, 0, 0, "A")
        self.assertEqual((f["transition"], f["ticked"], f["text"]), ("done", "3", "B"))
        f = self.run_echo("## Work\n- [x] A\n- [ ] B\n", 0, 0, 1, 0, 0, "A")
        self.assertEqual((f["transition"], f["ticked"]), ("done-bare", "2"))

    def test_moved_and_reworded(self):
        self.assertEqual(self.run_echo("## Work\n- [ ] Z\n- [ ] A\n", 0, 0, 1, 0, 0, "A")["transition"], "moved")
        self.assertEqual(self.run_echo("## Work\n- [ ] A, reworded\n- [ ] B\n", 0, 0, 1, 0, 0, "A")["transition"], "reworded")

    def test_fenced_gates_are_not_gates(self):
        f = self.run_echo("## Work\n```text\n- [x] fake\n- [ ] fake\n```\n- [ ] A\n", 0, 0, 1, 0, 0, "A")
        self.assertEqual((f["total"], f["checked"], f["pos"], f["transition"]), ("1", "0", "0", "same"))

    def test_all_done(self):
        f = self.run_echo("## Work\n- [x] A — ok\n", 0, 0, 1, 0, 0, "A")
        self.assertEqual((f["line"], f["pos"], f["transition"], f["ticked"], f["text"]), ("", "-1", "done", "2", ""))

    def test_a_prefix_is_not_a_tick(self):
        # the previous gate "A" is still open further down; "A step" above it, checked, is another gate
        f = self.run_echo("## Work\n- [x] A step — other\n- [ ] Z\n- [ ] A\n", 1, 0, 1, 1, 1, "A")
        self.assertEqual(f["transition"], "moved")
        # a checked gate whose text only STARTS with the previous one's ("Apple" for "A") is not its tick
        f = self.run_echo("## Work\n- [x] Apple — other\n- [ ] B\n", 1, 0, 1, 1, 1, "A")
        self.assertEqual(f["transition"], "reworded")


    # -- impl round 1's findings on the tick rule (B1-B3) and the cut texts (B10) --
    def test_a_note_inside_the_text_is_a_tick(self):
        # this task's own W-gate style: the note between the label and the rest of the title (opus, sonnet)
        f = self.run_echo("## Work\n- [x] W3 — 10:57 (`date`): built — do the thing\n- [ ] W4 next\n", 0, 0, 1, 0, 0,
                          "W3 do the thing")
        self.assertEqual((f["transition"], f["ticked"]), ("done", "2"))
        f = self.run_echo("## Work\n- [x] Alpha: done it\n- [ ] B\n", 0, 0, 1, 0, 0, "Alpha")
        self.assertEqual(f["transition"], "done")

    def test_a_checked_gate_removed_or_added_above_is_the_same_gate(self):
        self.assertEqual(self.run_echo("## Work\n- [ ] A\n", 1, 0, 1, 0, 0, "A")["transition"], "same")
        self.assertEqual(self.run_echo("## Work\n- [x] X — ok\n- [x] Y — ok\n- [ ] A\n", 1, 0, 1, 0, 0, "A")["transition"],
                         "same")

    def test_a_skip_is_judged_below_the_open_gate_at_any_position(self):
        # a checked gate above removed AND a gate below checked in one edit: still a skip (the count is BELOW it)
        self.assertEqual(self.run_echo("## Work\n- [ ] A\n- [x] B — early\n", 1, 0, 1, 0, 0, "A")["transition"], "skip")

    def test_a_checked_prefix_gate_added_above_the_open_one_is_no_tick(self):
        f = self.run_echo("## Work\n- [x] Alpha step extra — other\n- [ ] Alpha step\n", 0, 0, 1, 0, 0, "Alpha step")
        self.assertEqual(f["transition"], "same")       # codex

    def test_a_checked_sibling_is_not_the_tick_of_a_reworded_or_deleted_gate(self):
        sib = "- [x] Write tests for the parser — done\n"
        self.assertEqual(self.run_echo("## Work\n- [ ] Write tests now\n" + sib, 0, 1, 1, 1, 0, "Write tests")["transition"],
                         "reworded")                    # grok
        # deleted: the checked sibling moved up into its place — it was already a checked match (CHECKS = 1)
        self.assertEqual(self.run_echo("## Work\n" + sib, 0, 1, 1, 1, 0, "Write tests")["transition"], "reworded")

    # -- task 220: the tick decided by counts (task 219's round 2, C2 / C3 and the parked duplicate case) --
    def test_a_note_inside_the_text_with_a_gate_added_is_a_tick(self):
        f = self.run_echo("## Work\n- [x] W3 — 10:57: built — do the thing\n- [ ] W3b\n- [ ] W4 next\n", 0, 0, 1, 0, 0,
                          "W3 do the thing")
        self.assertEqual((f["transition"], f["ticked"]), ("done", "2"))

    def test_a_checked_prefix_gate_moved_onto_the_open_gates_place_is_no_tick(self):
        f = self.run_echo("## Work\n- [x] Write tests for the parser\n- [ ] Write tests\n- [ ] Middle\n", 0, 1, 1, 1, 0,
                          "Write tests")
        self.assertEqual(f["transition"], "same")     # grok

    def test_an_identical_checked_gate_is_no_tick_of_a_reworded_one(self):
        f = self.run_echo("## Work\n- [ ] Alpha, reworded\n- [x] Alpha\n", 0, 1, 1, 1, 0, "Alpha")
        self.assertEqual(f["transition"], "reworded")  # grok

    def test_a_duplicate_ticked_in_the_edit_that_adds_a_gate_above(self):
        f = self.run_echo("## Work\n- [ ] New\n- [x] Same — one\n- [ ] Same\n- [ ] G\n", 0, 0, 2, 0, 0, "Same")
        self.assertEqual((f["transition"], f["ticked"]), ("done", "3"))   # agy, parked by task 219

    def test_the_tick_named_is_the_match_at_the_previous_place(self):
        f = self.run_echo("## Work\n- [x] Same — new\n- [x] Same — older\n", 0, 0, 1, 1, 0, "Same")
        self.assertEqual((f["transition"], f["ticked"]), ("done", "2"))

    def test_the_counts_the_next_call_passes_back(self):
        f = self.run_echo("## Work\n- [x] Same — first\n- [ ] Same\n- [ ] Same\n- [x] Same — old\n- [x] Other\n")
        self.assertEqual((f["text"], f["opens"], f["checks"], f["above"]), ("Same", "2", "2", "1"))

    # -- task 220, impl round 1 --
    def test_a_later_duplicate_ticked_out_of_order_is_a_skip(self):
        prev = (1, 0, 2, 0, 0, "Verify")              # Verify open twice, the first one shown (opus, codex)
        f = self.run_echo("## Work\n- [x] Fix A\n- [ ] Verify\n- [ ] Fix B\n- [x] Verify — early\n", *prev)
        self.assertEqual(f["transition"], "skip")
        f = self.run_echo("## Work\n- [x] Fix A\n- [x] Verify — ok\n- [ ] Fix B\n- [ ] Verify\n", *prev)
        self.assertEqual((f["transition"], f["ticked"]), ("done", "3"))

    def test_a_lone_cr_inside_a_gates_text_still_matches_its_stored_copy(self):
        f = self.run_echo("## Work\n- [ ] Alpha\rstep\n- [ ] B\n", 0, 0, 1, 0, 0, "Alphastep")
        self.assertEqual(f["transition"], "same")      # sonnet

    def test_the_cut_texts_count_characters(self):
        f = self.run_echo("## Work\n- [ ] " + "ș" * 250 + "\n")
        self.assertEqual((len(f["text200"]), len(f["text60"])), (200, 60))
        self.assertNotIn("\ufffd", f["text60"] + f["text200"])


if __name__ == "__main__":
    unittest.main()
