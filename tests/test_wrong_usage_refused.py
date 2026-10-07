"""Task 116 (PLAN S11 group 4): wrong usage of the tasks CLI is refused before anything is written.

Gauntlet 2 (task 109) found commands that took wrong usage silently — some of them WRITING:
`tasks new quick t1 --bogus` made a task whose Intent is `--bogus`, `retro --bogus` and
`freehand --bogus` created tasks, `audit --bogus` appended a receipt, `audit 99` and a bare
`audit` with no active task printed `AUDIT PASS` and recorded nothing, seven read-only commands
ignored unknown flags, and `--force` without `--reason` ran the whole verify before refusing.
The precedent is `tasks handoff`: arguments it does not take are refused, nothing changed.

Every case runs the real CLI as a subprocess and compares the project tree before and after.

Run: python3 -m unittest tests.test_wrong_usage_refused
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
SESSION_ID = "pid-wrong-usage-test"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))       # the in-process backstop tests import the command modules

TASK_MD = """# {num} - Fixture

## Status
pending

## Risk
reversible

## Intent
Fixture.

## Work Plan
- [ ] a gate
"""


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        # a dead session the CLI's session GC would delete — a refusal must come BEFORE it
        # (single-judge review: "Nothing changed." was false whenever such leftovers existed)
        dead = self.project / ".agent" / "sessions" / "pid-999999"
        dead.mkdir(parents=True)
        (dead / "current_state").write_text("001\n", encoding="utf-8")

    def task(self, num: str, gates_done: bool = False) -> Path:
        d = self.project / ".agent" / "tasks" / f"{num}-fixture"
        d.mkdir(parents=True)
        text = TASK_MD.format(num=num)
        (d / "task.md").write_text(text.replace("- [ ]", "- [x]") if gates_done else text, encoding="utf-8")
        return d / "task.md"

    def activate(self, num: str):
        r = self.run_tasks("work", num)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def run_tasks(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID=SESSION_ID)
        env.pop("PLAYBOOK_VERIFY_JOBS", None)
        env.pop("BASH_ENV", None)              # the shell logger writes .agent/bash_history (verify strips it too)
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project,
                              env=env, capture_output=True, text=True, timeout=120)

    def tree(self) -> dict:
        out = {}
        for p in sorted(self.project.rglob("*")):
            if ".git" in p.relative_to(self.project).parts or not p.is_file():
                continue
            out[p.relative_to(self.project).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out

    def assert_refused(self, *args: str, says: str = ""):
        before = self.tree()
        r = self.run_tasks(*args)
        self.assertNotEqual(r.returncode, 0, f"tasks {' '.join(args)} was accepted:\n{r.stdout}{r.stderr}")
        self.assertIn("Error", r.stderr, args)
        if says:
            self.assertIn(says, r.stderr, args)
        self.assertEqual(self.tree(), before, f"tasks {' '.join(args)} changed the project")
        return r


class UnknownOptionsOnReadOnlyCommands(_Project):
    def test_each_is_refused_and_its_real_options_still_work(self):
        self.task("001")
        for cmd, good in (("status", []), ("list", ["--pending"]), ("ls", ["--pending"]),
                          ("parked", ["--all"]), ("bootstrap", []), ("dashboard", ["--no-detect"]),
                          ("timeline", []), ("mindmap-sync", [])):
            self.assert_refused(cmd, "--bogus", says="--bogus")
            self.assert_refused(cmd, *good, "extra-word")
            r = self.run_tasks(cmd, *good)
            self.assertNotIn("unknown option", r.stderr, (cmd, r.stderr))


class CommandsThatCreateTasks(_Project):
    def test_new_refuses_an_option_looking_intent_word_and_keeps_the_dash_dash_form(self):
        self.assert_refused("new", "quick", "t1", "--bogus", says="--bogus")
        self.assert_refused("new", "quick", "t1", "fix", "the", "--force", "flag")
        r = self.run_tasks("new", "quick", "t1", "--", "explain", "--force", "usage")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        made = list((self.project / ".agent" / "tasks").glob("*-t1/task.md"))
        self.assertEqual(len(made), 1)
        intent = made[0].read_text(encoding="utf-8").split("## Intent\n", 1)[1].split("\n", 1)[0]
        self.assertEqual(intent, "explain --force usage")          # the `--` itself is not intent text
        # a `--stub` AFTER the separator is intent text, not the flag
        r = self.run_tasks("new", "quick", "t3", "--", "please", "--stub", "this")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (t3,) = (self.project / ".agent" / "tasks").glob("*-t3/task.md")
        self.assertNotIn("<!-- stub:", t3.read_text(encoding="utf-8"))
        self.assertIn("please --stub this", t3.read_text(encoding="utf-8"))
        # type and name must come before the separator — refused BEFORE the session GC
        # (task 138 G1-1: a dead session dir was collected, then cmd_new refused)
        dead = self.project / ".agent" / "sessions" / "pid-999997"
        dead.mkdir(parents=True)
        (dead / "current_state").write_text("001\n", encoding="utf-8")
        self.assert_refused("new", "quick", "--", "t4", "hello", says="before")
        self.assert_refused("new", "--", "quick", "t4")
        self.assertTrue(dead.is_dir(), "the refusal ran after the session GC")
        # task 138 G1-8: after `--` a `--help` is intent text, not a request for usage
        r = self.run_tasks("new", "quick", "t5", "--", "what", "--help", "prints")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (t5,) = (self.project / ".agent" / "tasks").glob("*-t5/task.md")
        self.assertIn("what --help prints", t5.read_text(encoding="utf-8"))
        r = self.run_tasks("new", "quick", "t6", "--help")             # before it, still usage
        self.assertEqual(list((self.project / ".agent" / "tasks").glob("*-t6")), [])
        r = self.run_tasks("new", "quick", "t2", "--stub")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_retro_and_freehand_refuse_what_they_do_not_take(self):
        self.assert_refused("retro", "--bogus", says="--bogus")
        self.assert_refused("retro", "--since", says="needs a value")  # a value is missing
        self.assert_refused("retro", "--since", "soon", says="whole number")
        self.assert_refused("freehand", "--bogus", says="--bogus")
        self.assert_refused("freehand", "name", "and-another")       # one session name at most
        # task 138 G1-3: the only word freehand takes is `log` — any other was ignored and
        # created a freehand task (`tasks freehand lgo`)
        self.assert_refused("freehand", "lgo", says="lgo")


class Audit(_Project):
    def test_audit_refuses_a_missing_task_no_task_and_unknown_options(self):
        tf = self.task("004")
        self.assert_refused("audit", "99", says="99")                 # no such task
        self.assert_refused("audit", "four")                          # not a task number
        # a bare audit is also a plain scan (SKILL.md: "a mechanical scan (`tasks audit` …)"): it runs,
        # writes nothing — and now SAYS it recorded nothing, instead of a silent PASS
        # (an ACCEPTED command: the CLI's session GC may run, so sessions are left out of this compare)
        no_sessions = lambda tr: {k: v for k, v in tr.items() if not k.startswith(".agent/sessions/")}
        before = no_sessions(self.tree())
        r = self.run_tasks("audit")
        self.assertIn("no receipt recorded", r.stderr)
        self.assertEqual(no_sessions(self.tree()), before)
        self.activate("004")
        self.assert_refused("audit", "--bogus", says="--bogus")       # used to append a receipt
        before = tf.read_text(encoding="utf-8")
        r = self.run_tasks("audit", "004")                            # the control
        self.assertIn("AUDIT", r.stdout)
        self.assertIn("## Pre-Panel Audit", tf.read_text(encoding="utf-8"))
        self.assertNotEqual(tf.read_text(encoding="utf-8"), before)


class ForceWithoutReason(_Project):
    def test_is_refused_before_the_verify_runs(self):
        self.task("007", gates_done=True)          # nothing but the hatch stands between it and the verify
        self.activate("007")
        (self.project / ".agent" / "config.json").write_text(
            '{"verify": {"_always": ["python3 -c \\"open(\'VERIFY-RAN\', \'w\').close()\\""]}}', encoding="utf-8")
        dead = self.project / ".agent" / "sessions" / "pid-999998"     # activation's GC took the first
        dead.mkdir(parents=True)
        (dead / "current_state").write_text("007\n", encoding="utf-8")
        r = self.assert_refused("work", "done", "--force", says="--reason")
        self.assertTrue(dead.is_dir(), "the refusal ran after the session GC")
        self.assertFalse((self.project / "VERIFY-RAN").exists(), "the verify ran before the refusal")
        r = self.run_tasks("work", "done", "--stale-panel-ok")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--reason", r.stderr)
        self.assertFalse((self.project / "VERIFY-RAN").exists(), "the verify ran before the refusal")
        # the control: with its reason the close goes on and the verify runs
        r = self.run_tasks("work", "done", "--force", "--reason", "fixture close")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.project / "VERIFY-RAN").exists(), "the control never reached the verify")



class BackstopsForInProcessCallers(_Project):
    """The CLI refuses these before its session GC; the command functions keep the same rule
    for a caller that imports them (an in-process caller never passes through `cli.main`)."""

    def _in_process(self, fn, args):
        import contextlib
        import io
        cwd = os.getcwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, cwd)
        env = {"PLAYBOOK_SESSION_ID": SESSION_ID}
        err = io.StringIO()
        from unittest import mock
        with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            fn(args)
        self.assertNotEqual(cm.exception.code, 0)
        return err.getvalue()

    def test_cmd_audit_refuses_a_missing_task(self):
        from tasks.diagnostics import cmd_audit
        before = self.tree()
        self.assertIn("no task 99", self._in_process(cmd_audit, ["99"]))
        self.assertEqual(self.tree(), before)

    def test_cmd_work_refuses_each_hatch_without_its_reason(self):
        from tasks.lifecycle import cmd_work
        for hatch in ("--force", "--stale-panel-ok"):
            self.assertIn("requires --reason", self._in_process(cmd_work, ["done", hatch]), hatch)
            # task 138 G1-6: a flag is not a reason
            self.assertIn("requires --reason",
                          self._in_process(cmd_work, ["done", hatch, "--reason", "-f"]), hatch)


class FlagIsNotAReason(_Project):
    def test_a_flag_after_reason_is_refused_before_the_verify(self):
        # task 138 G1-6 (codex-high): `--force --reason -f` took `-f` as the reason
        self.task("008", gates_done=True)
        self.activate("008")
        (self.project / ".agent" / "config.json").write_text(
            '{"verify": {"_always": ["python3 -c \\"open(\'VERIFY-RAN\', \'w\').close()\\""]}}', encoding="utf-8")
        dead = self.project / ".agent" / "sessions" / "pid-999996"     # activation's GC took the first
        dead.mkdir(parents=True)
        (dead / "current_state").write_text("008\n", encoding="utf-8")
        for flag in ("-f", "--force", "--stale-panel-ok"):
            self.assert_refused("work", "done", "--force", "--reason", flag, says="--reason")
        self.assertTrue(dead.is_dir(), "the refusal ran after the session GC")
        self.assertFalse((self.project / "VERIFY-RAN").exists())

if __name__ == "__main__":
    unittest.main()
