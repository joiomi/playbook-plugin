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
        procs = [subprocess.Popen([BASH, str(SCRIPTS / "state-echo-hook")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.d, env=self.env,
                                  text=True) for _ in range(6)]
        outs = []
        for p in procs:
            o, _e = p.communicate(BASH_PAYLOAD, timeout=60)
            outs.append((json.loads(o.strip() or "{}").get("hookSpecificOutput") or {}).get("additionalContext") or "")
        self.assertEqual(sum(1 for o in outs if o and "previous gate done" in o), 1, outs)
        self.assertEqual(sum(1 for o in outs if o), 1, outs)
        self.assertEqual(self.chat_log().count("- [x] Alpha step"), 1)
        rest = [self.echo() for _ in range(5)]
        self.assertEqual([i for i, o in enumerate(rest) if o], [4], rest)


class EchoFields(unittest.TestCase):
    """`scripts/task-status.py --echo <task.md> [<P> <B> <T>]` — eight lines: total, checked, the first open gate's
    line, its live position, the checked live gates below it, the transition, the checked line (done only), and the
    first open gate's full text."""

    def run_echo(self, text, *prev):
        d = Path(tempfile.mkdtemp(prefix="pb219e-"))
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "task.md"
        f.write_text(text, encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", str(SCRIPTS / "task-status.py"), "--echo", str(f),
                            *map(str, prev)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.split("\n")
        self.assertEqual(len(lines), 9, r.stdout)          # eight fields and the final newline
        keys = ("total", "checked", "line", "pos", "below", "transition", "ticked", "text")
        return dict(zip(keys, lines[:8]))

    def test_no_previous(self):
        f = self.run_echo("## Work\n- [x] A — ok\n- [ ] B\n- [ ] C\n")
        self.assertEqual((f["total"], f["checked"], f["line"], f["pos"], f["below"], f["transition"], f["text"]),
                         ("3", "1", "3", "1", "0", "none", "B"))

    def test_same_and_skip(self):
        self.assertEqual(self.run_echo("## Work\n- [ ] A\n- [ ] B\n", 0, 0, "A")["transition"], "same")
        self.assertEqual(self.run_echo("## Work\n- [ ] A\n- [x] B — early\n", 0, 0, "A")["transition"], "skip")

    def test_done_and_done_bare(self):
        f = self.run_echo("## Work\nnew line\n- [x] A — ok\n- [ ] B\n", 0, 0, "A")
        self.assertEqual((f["transition"], f["ticked"], f["text"]), ("done", "3", "B"))
        f = self.run_echo("## Work\n- [x] A\n- [ ] B\n", 0, 0, "A")
        self.assertEqual((f["transition"], f["ticked"]), ("done-bare", "2"))

    def test_moved_and_reworded(self):
        self.assertEqual(self.run_echo("## Work\n- [ ] Z\n- [ ] A\n", 0, 0, "A")["transition"], "moved")
        self.assertEqual(self.run_echo("## Work\n- [ ] A, reworded\n- [ ] B\n", 0, 0, "A")["transition"], "reworded")

    def test_fenced_gates_are_not_gates(self):
        f = self.run_echo("## Work\n```text\n- [x] fake\n- [ ] fake\n```\n- [ ] A\n", 0, 0, "A")
        self.assertEqual((f["total"], f["checked"], f["pos"], f["transition"]), ("1", "0", "0", "same"))

    def test_all_done(self):
        f = self.run_echo("## Work\n- [x] A — ok\n", 0, 0, "A")
        self.assertEqual((f["line"], f["pos"], f["transition"], f["ticked"], f["text"]), ("", "-1", "done", "2", ""))

    def test_a_prefix_is_not_a_tick(self):
        # the previous gate "A" is still open further down; "A step" above it, checked, is another gate
        f = self.run_echo("## Work\n- [x] A step — other\n- [ ] Z\n- [ ] A\n", 1, 0, "A")
        self.assertEqual(f["transition"], "moved")
        # a checked gate whose text only STARTS with the previous one's ("Apple" for "A") is not its tick
        f = self.run_echo("## Work\n- [x] Apple — other\n- [ ] B\n", 1, 0, "A")
        self.assertEqual(f["transition"], "reworded")


if __name__ == "__main__":
    unittest.main()
