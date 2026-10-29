#!/usr/bin/env python3
"""The simple red receipt (task 342, PLAN-PLAYBOOK-2.md 4.1 d; DESIGN-PLAYBOOK-2.md §2, approved R17).

The owner's rule stays (M1415, 2026-10-21: the tests are written first, so the code follows their path); its check is
simpler. `tasks red` runs each `## Test contract` row in the project as it is now and records the result with the
tree's fingerprint (`red.tree_fingerprint`), the test files' digest and the time. The close passes a row seen red and later green on a
different tree — the red may come from a mutant (code broken on purpose, `--mutant "<why>"`). The copy of the old code
(`--base`), `--drop`, the late-row rules and the state carried between runs are gone; `base.json` keeps each
repository's start commit only and the activation writes nothing into `.git/objects` (E023.11); `No behavior changes:`
does not count `__pycache__` (E059.7).

Real temporary git projects (an outer repository and a nested `code_roots` one), the CLI as `python3 -m tasks.cli`.
Each test names the row of task 342's contract it proves. Pure stdlib unittest.
"""
import contextlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
GIT = ["git", "-c", "user.email=a@b", "-c", "user.name=p", "-c", "commit.gpgsign=false"]
NEW = "`tests.test_new.N.test_two`"
OLD = "`tests.test_old.T.test_one`"


def _contract(rows) -> str:
    body = "".join(f"| {n} | {k} | {b} | {t} |\n" for n, k, b, t in rows)
    return ("## Test contract\n<!-- pin -->\n\n| # | Kind | Behavior | Test that proves it |\n"
            "|---|------|----------|---------------------|\n" + body + "\n")


class _Project(unittest.TestCase):
    """An outer repository with a nested `code_roots` repository `code/` (`src.f()` returns 1, `tests/test_old.py`
    passes), a throwaway HOME, and a light task whose gates are ticked — a close needs no review."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb342-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = self.d / "home"
        self.home.mkdir()
        self.g("init", "-q")
        (self.d / ".gitignore").write_text("home/\ncode/\n.agent/journal/\n.agent/sessions/\n", encoding="utf-8")
        (self.d / ".agent" / "tasks").mkdir(parents=True)
        self.config()
        (self.d / "README.md").write_text("outer\n", encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "outer start")
        self.code = self.d / "code"
        (self.code / "tests").mkdir(parents=True)
        self.g("init", "-q", cwd=self.code)
        (self.code / "src.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (self.code / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.code / "tests" / "test_old.py").write_text(
            "import unittest\nimport src\n\n\nclass T(unittest.TestCase):\n"
            "    def test_one(self):\n        self.assertTrue(callable(src.f))\n        src.f()\n", encoding="utf-8")
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "code start", cwd=self.code)

    # -- the project
    def config(self, **extra):
        cfg = {"verify": "true", "code_roots": ["code"], "red_run": "cd code && python3 -m unittest {test}"}
        cfg.update(extra)
        (self.d / ".agent" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    def g(self, *args, cwd=None):
        return subprocess.run([*GIT, *args], cwd=cwd or self.d, capture_output=True, text=True, check=True)

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE", "GIT_"))}
        env.update({"PYTHONPATH": str(_PLAYBOOK), "HOME": str(self.home), "PLAYBOOK_SESSION_ID": "pid-red342"})
        return env

    def cli(self, *args, timeout=300):
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.d, env=self.env(),
                              capture_output=True, text=True, timeout=timeout)

    def light(self, rows=None, no_change=None):
        r = self.cli("new", "light", "probe", "probe the receipt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.task_dir = next((self.d / ".agent" / "tasks").glob("001-*"))
        self.task_md = self.task_dir / "task.md"
        text = self.task_md.read_text(encoding="utf-8").replace("## Risk\nunclassified\n", "## Risk\nreversible\n")
        text = text.replace("- [ ] ", "- [x] ")
        text = "\n".join(ln + (" — done" if ln.startswith("- [x] ") else "") for ln in text.split("\n"))
        if rows is not None:
            text = text.replace("## Pre-review\n", _contract(rows) + "## Pre-review\n", 1)
        if no_change is not None:
            text = text.replace("## Pre-review\n", "## Test contract\n<!-- pin -->\n\nNo behavior changes: "
                                + no_change + "\n\n## Pre-review\n", 1)
        self.task_md.write_text(text, encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "the task")
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def set_rows(self, rows):
        text = self.task_md.read_text(encoding="utf-8")
        text = re.sub(r"(?ms)^## Test contract\n.*?(?=^## )", lambda _m: _contract(rows), text)
        self.task_md.write_text(text, encoding="utf-8")

    def write_new_test(self):
        (self.code / "tests" / "test_new.py").write_text(
            "import unittest\nimport src\n\n\nclass N(unittest.TestCase):\n"
            "    def test_two(self):\n        self.assertEqual(src.f(), 2)\n", encoding="utf-8")

    def fix_code(self):
        # a size of its own: a `.pyc` of the same size and second would read as fresh, and the old code would run
        (self.code / "src.py").write_text("def f():\n    return 2  # fixed\n", encoding="utf-8")

    def break_code(self):
        (self.code / "src.py").write_text("def f():\n    raise RuntimeError('broken on purpose')\n", encoding="utf-8")

    # -- what the CLI wrote
    def red(self, *args):
        return self.cli("red", "1", *args)

    def records(self):
        p = self.task_dir / "red-receipt.jsonl"
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []

    def section(self):
        m = re.search(r"(?ms)^## Red receipt\n(.*?)(?=^## |\Z)", self.task_md.read_text(encoding="utf-8"))
        return m.group(1) if m else ""

    def close(self, *extra):
        r = self.cli("work", "done", *extra, timeout=300)
        return r, r.stdout + r.stderr

    def done(self):
        return "## Status\ndone" in self.task_md.read_text(encoding="utf-8")

    def objects(self, repo):
        out = self.g("count-objects", "-v", cwd=repo).stdout
        return {k: v for k, v in (ln.split(": ") for ln in out.splitlines()) if k in ("count", "in-pack")}

    def call(self, code):
        """`code` in a child process with `red` (tasks.red), `p` (the project) and `cfg` (its config) — what it prints."""
        prog = ("import sys, json; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from tasks import red; "
                "p = Path(sys.argv[2]); cfg = json.loads((p / '.agent' / 'config.json').read_text()); " + code)
        return subprocess.run([sys.executable, "-c", prog, str(_PLAYBOOK), str(self.d)], env=self.env(),
                              capture_output=True, text=True, check=True).stdout.strip()

    def fingerprint(self):
        return self.call("print(red.tree_fingerprint(p, cfg))")

    def tests_now(self):
        return self.call("print(red.tests_digest(p, cfg))")


class Run(_Project):

    def test_a_run_records_each_row_on_the_tree_now(self):
        """Row 1: one v2 record per row, in the project (not a copy), with the tree's fingerprint and the time."""
        self.light([("1", "normal", "the run is in the project", "`pwd -P`"), ("2", "normal", "f returns 2", NEW)])
        self.write_new_test()
        r = self.red()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        recs = self.records()
        self.assertEqual([x.get("row") for x in recs], ["1", "2"], recs)
        tree = self.fingerprint()
        for x in recs:
            self.assertEqual(x.get("v"), 2, x)
            self.assertEqual(x.get("tree"), tree, x)
            self.assertTrue(x.get("at"), x)
        self.assertEqual(recs[0]["status"], "green")
        self.assertEqual(recs[0]["last"], str(self.d))             # cwd = the project itself
        self.assertEqual(recs[1]["status"], "red")
        sec = self.section()
        self.assertIn("never red", sec)
        self.assertIn("red now", sec)

    def test_a_tree_that_moved_during_the_run_counts_for_nothing(self):
        """Row 14: a run during which the tree changed is recorded as moved and proves nothing."""
        self.light([("1", "normal", "f returns 2", "`echo '# x' >> code/src.py; false`")])
        r = self.red()
        recs = self.records()
        self.assertTrue(recs and recs[-1].get("moved") is True, recs)
        self.assertIn("fingerprint_exclude", r.stdout)          # row 33: what the row writes may be left out
        self.config(red_run="cd code && python3 -m unittest {test}")
        self.set_rows([("1", "normal", "f returns 2", "`test 1 = 1`")])
        self.red()
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("never red", out)

    def test_guard_timeout_and_launch_failure_are_neither(self):
        """Row 15: the command guard's refusal, a timeout and a launch failure are neither red nor green."""
        self.config(red_timeout=2)
        self.light([("1", "normal", "a", "`rm -rf /`"), ("2", "normal", "b", "`sleep 30`"),
                    ("3", "normal", "c", "`no_such_command_342 --now`")])
        self.red()
        st = {x["row"]: x["status"] for x in self.records()}
        self.assertEqual(st, {"1": "refused", "2": "timeout", "3": "launch failed"}, self.records())
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("run badly", out)


    def test_python_caches_do_not_move_the_tree(self):
        """Row 17 (plan panel P1): the project does not ignore `__pycache__`; a Python row run twice on the same code
        reads the same tree, and neither run is `moved`."""
        self.light([("1", "must-not", "the old test holds", OLD)])
        self.red()
        self.red()
        recs = self.records()
        self.assertEqual(len(recs), 2, recs)
        self.assertFalse(any(x.get("moved") for x in recs), recs)
        self.assertEqual(recs[0]["tree"], recs[1]["tree"])

    def test_several_commands_fold_into_one_record(self):
        """Row 18 (P3): red if one command is red, green only when all are, a command that ran badly first."""
        self.light([("1", "normal", "two", "`test 1 = 1` and `test 1 = 2`"),
                    ("2", "normal", "both", "`test 1 = 1` and `test 2 = 2`"),
                    ("3", "normal", "bad", "`test 1 = 2` and `no_such_command_342 --x`")])
        self.red()
        st = {x["row"]: (x["status"], len(x.get("sides") or [])) for x in self.records()}
        self.assertEqual(st, {"1": ("red", 2), "2": ("green", 2), "3": ("launch failed", 2)}, self.records())

    def test_two_rows_with_one_test_are_refused(self):
        """Row 25 (P11): two rows with the same test cell would share their records."""
        self.light([("1", "normal", "a", NEW), ("2", "edge", "b", NEW)])
        r = self.red()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("same test", r.stdout + r.stderr)
        self.assertEqual(self.records(), [])

    def test_each_row_records_the_test_files_it_ran_on(self):
        """Row 27 (impl round 1, I2: opus, codex, grok): the test files' digest is read for each row — a row that edits
        a test file moves its own run, and the next row records the test files as it ran on them."""
        self.light([("1", "normal", "edits a test file", "`echo '# edited' >> code/tests/test_old.py`"),
                    ("2", "normal", "f returns 2", NEW)])
        self.write_new_test()
        self.red()
        recs = self.records()
        self.assertEqual(len(recs), 2, recs)
        self.assertTrue(recs[0].get("moved"), recs)
        self.assertFalse(recs[1].get("moved"), recs)
        self.assertNotEqual(recs[0]["tests"], recs[1]["tests"], recs)
        self.assertEqual(recs[1]["tests"], self.tests_now(), recs)

    def test_a_terminated_run_stops_its_row(self):
        """Row 32 (impl round 1, I5: codex): SIGTERM or SIGHUP to `tasks red` kills the running row's process group —
        the row writes nothing after it."""
        marks = Path(tempfile.mkdtemp(prefix="pb342-sig-"))
        self.addCleanup(shutil.rmtree, marks, True)
        started, finished = marks / "started", marks / "finished"
        self.light([("1", "normal", "slow", f"`touch {started}; sleep 3; touch {finished}`")])
        for sig in (signal.SIGTERM, signal.SIGHUP):
            for f in (started, finished):
                if f.exists():
                    f.unlink()
            p = subprocess.Popen([sys.executable, "-m", "tasks.cli", "red", "1"], cwd=self.d, env=self.env(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 60
            while not started.exists() and p.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(started.exists(), sig)
            p.send_signal(sig)
            out, err = p.communicate(timeout=60)
            self.assertNotEqual(p.returncode, 0, (sig, out, err))
            time.sleep(4)
            self.assertFalse(finished.exists(), (sig, out, err))

    def test_a_signal_during_the_launch_stops_the_row(self):
        """Row 36 (post-panel run 1, codex): SIGTERM or SIGHUP right after the row's process is started — before the
        run waits on it — still kills the row's process group."""
        marks = Path(tempfile.mkdtemp(prefix="pb342-launch-"))
        self.addCleanup(shutil.rmtree, marks, True)
        started, finished = marks / "started", marks / "finished"
        self.light([("1", "normal", "slow", f"`touch {started}; sleep 3; touch {finished}`")])
        from tasks import red
        real = subprocess.Popen

        def guard(signum, _frame):                          # the test runner never dies of a signal left unhandled
            raise AssertionError(f"signal {signum} reached no handler of `tasks red`")
        for sig in (signal.SIGTERM, signal.SIGHUP):
            for f in (started, finished):
                if f.exists():
                    f.unlink()
            old = signal.signal(sig, guard)
            self.addCleanup(signal.signal, sig, old)

            def launch(*a, _sig=sig, **k):
                proc = real(*a, **k)
                if k.get("start_new_session"):              # the row's process, not a git call
                    os.kill(os.getpid(), _sig)
                return proc
            out = io.StringIO()
            cwd = os.getcwd()
            with mock.patch.object(red.subprocess, "Popen", launch), \
                    mock.patch.dict(os.environ, self.env(), clear=True), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(out):
                os.chdir(self.d)
                try:
                    with self.assertRaises(SystemExit) as cm:
                        red.cmd_red(["1"])
                finally:
                    os.chdir(cwd)
            self.assertEqual(cm.exception.code, 130, (sig, out.getvalue()))
            time.sleep(4)
            self.assertFalse(finished.exists(), (sig, out.getvalue()))
            signal.signal(sig, old)

    def test_a_signal_after_the_wait_kills_the_group(self):
        """Row 38 (post-panel run 2, codex): SIGTERM or SIGHUP after the row's shell ended — its wait over, the group
        not yet killed — still kills the group: a background child of the row writes nothing after."""
        marks = Path(tempfile.mkdtemp(prefix="pb342-after-"))
        self.addCleanup(shutil.rmtree, marks, True)
        finished = marks / "finished"
        self.light([("1", "normal", "leaves a child", f"`(sleep 2; touch {finished}) >/dev/null 2>&1 &`")])
        from tasks import red

        class Row(subprocess.Popen):
            """The row's process: its exit code, read once its wait is over, brings the signal."""
            sig = None
            armed = False

            @property
            def returncode(self):
                if self.armed:
                    self.armed = False
                    os.kill(os.getpid(), self.sig)
                return self.__dict__.get("_rc")

            @returncode.setter
            def returncode(self, value):
                self.__dict__["_rc"] = value

            def communicate(self, *a, **k):
                out = super().communicate(*a, **k)
                self.armed = True
                return out
        real = subprocess.Popen

        def guard(signum, _frame):                          # the test runner never dies of a signal left unhandled
            raise AssertionError(f"signal {signum} reached no handler of `tasks red`")
        for sig in (signal.SIGTERM, signal.SIGHUP):
            if finished.exists():
                finished.unlink()
            old = signal.signal(sig, guard)
            self.addCleanup(signal.signal, sig, old)
            Row.sig = sig

            def launch(*a, **k):
                return (Row if k.get("start_new_session") else real)(*a, **k)
            out = io.StringIO()
            cwd = os.getcwd()
            with mock.patch.object(red.subprocess, "Popen", launch), \
                    mock.patch.dict(os.environ, self.env(), clear=True), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(out):
                os.chdir(self.d)
                try:
                    with self.assertRaises(SystemExit) as cm:
                        red.cmd_red(["1"])
                finally:
                    os.chdir(cwd)
            self.assertEqual(cm.exception.code, 130, (sig, out.getvalue()))
            time.sleep(3)
            self.assertFalse(finished.exists(), (sig, out.getvalue()))
            signal.signal(sig, old)

    def test_an_unreadable_tests_digest_refuses(self):
        """Row 34 (impl round 1, opus m): a run that cannot say which test files it ran on refuses, writing nothing."""
        self.light([("1", "normal", "f returns 2", NEW)])
        from tasks import red
        out = io.StringIO()
        cwd = os.getcwd()
        with mock.patch.object(red, "tests_digest", return_value=""), \
                mock.patch.dict(os.environ, self.env(), clear=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            os.chdir(self.d)
            try:
                with self.assertRaises(SystemExit) as cm:
                    red.cmd_red(["1"])
            finally:
                os.chdir(cwd)
        self.assertNotEqual(cm.exception.code, 0, out.getvalue())
        self.assertIn("test files", out.getvalue())
        self.assertEqual(self.records(), [])


class Close(_Project):

    def test_red_then_green_on_newer_code_closes(self):
        """Row 2: a row added after a first run (the old 'late row'), seen red, then green after the fix — the start is
        the fix's parent — closes with no flag."""
        self.light([("1", "must-not", "the old test holds", OLD)])
        self.break_code()
        self.red()
        self.g("checkout", "src.py", cwd=self.code)
        self.red()                                               # row 1: a mutant red, then green
        self.set_rows([("1", "must-not", "the old test holds", OLD), ("2", "normal", "f returns 2", NEW)])
        self.write_new_test()
        self.red("--rows", "2")
        self.fix_code()
        self.red()
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(self.done(), out)

    def test_a_row_never_seen_red_blocks(self):
        """Row 3: only green records: the close blocks and names both ways out."""
        self.light([("1", "must-not", "the old test holds", OLD)])
        self.red()
        self.red()
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertFalse(self.done())
        self.assertIn("never red", out)
        self.assertIn("--mutant", out)

    def test_a_row_red_now_blocks(self):
        """Row 4: red, green, then red again: the newest record is red."""
        self.light([("1", "normal", "f returns 2", NEW)])
        self.write_new_test()
        self.red()
        self.fix_code()
        self.red()
        self.break_code()
        self.red()
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("red now", out)

    def test_red_and_green_on_the_same_tree_do_not_prove(self):
        """Row 5: a flaky row — red, then green on the very same tree — proves nothing."""
        marker = Path(tempfile.mkdtemp(prefix="pb342-flaky-")) / "seen"
        self.addCleanup(shutil.rmtree, marker.parent, True)
        # (no `|` in the cell: the table would split it)
        self.light([("1", "normal", "flaky", f"`if test -f {marker}; then true; else touch {marker}; false; fi`")])
        self.red()
        self.red()
        self.assertEqual([x["status"] for x in self.records()], ["red", "green"])
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("same tree", out)

    def test_a_changed_test_starts_over_and_a_reworded_behavior_does_not(self):
        """Row 13: a row's key is its test cell."""
        self.light([("1", "normal", "f returns 2", NEW)])
        self.write_new_test()
        self.red()
        self.fix_code()
        self.red()
        self.set_rows([("1", "normal", "f() gives two, reworded", NEW)])
        st = self.cli("red", "1", "--check")
        self.assertIn("proved", st.stdout + st.stderr)
        self.set_rows([("1", "normal", "f returns 2", OLD)])
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("not run", out)


    def test_red_then_green_before_the_first_commit(self):
        """Row 19 (P5): a code root with no commit yet — a red, a code edit, a green: proved."""
        shutil.rmtree(self.code / ".git")
        self.g("init", "-q", cwd=self.code)                       # unborn: the files are there, nothing committed
        self.light([("1", "normal", "f returns 2", NEW)])
        base = json.loads((self.task_dir / "base.json").read_text(encoding="utf-8"))
        self.assertIsNone(base["scopes"]["code"]["head"], base)
        self.write_new_test()
        self.red()
        self.fix_code()
        self.red()
        recs = self.records()
        self.assertEqual([x["status"] for x in recs], ["red", "green"], recs)
        self.assertNotEqual(recs[0]["tree"], recs[1]["tree"])
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)

    def test_a_red_on_other_test_files_does_not_prove(self):
        """Row 20 (P6): `tasks red` before the test file exists is red ("no such test"); the green after the test and
        the code were written is on other test files — no proof."""
        self.light([("1", "normal", "f returns 2", NEW)])
        self.red()                                                # the test does not exist yet: red
        self.write_new_test()
        self.fix_code()
        self.red()
        recs = self.records()
        self.assertEqual([x["status"] for x in recs], ["red", "green"], recs)
        self.assertNotEqual(recs[0].get("tests"), recs[1].get("tests"))
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("other test files", out)
        self.assertIn("tasks red --mutant", out.split("The way through:", 1)[-1])   # row 33 (impl round 1, I8)

    def test_an_old_pair_does_not_prove_a_green_on_changed_tests(self):
        """Row 28 (impl round 1, I3: codex): red and green on tests A, a red on tests B, then a green on tests C — C was
        never seen red, and the older pair proves nothing about it."""
        self.light([("1", "normal", "f returns 2", NEW)])
        self.write_new_test()
        self.red()                                                # red on A
        self.fix_code()
        self.red()                                                # green on A
        t = self.code / "tests" / "test_new.py"
        t.write_text(t.read_text(encoding="utf-8").replace("src.f(), 2)", "src.f(), 3)"), encoding="utf-8")
        self.red()                                                # red on B
        t.write_text(t.read_text(encoding="utf-8").replace("self.assertEqual(src.f(), 3)", "self.assertTrue(src.f())"),
                     encoding="utf-8")
        self.red()                                                # green on C
        self.assertEqual([x["status"] for x in self.records()], ["red", "green", "red", "green"])
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("other test files", out)

    def test_a_test_file_added_after_the_proof_keeps_it(self):
        """Row 28 (impl round 1, I1: opus, sonnet): a test file written for a later row after this row was proved does
        not undo its proof."""
        self.light([("1", "normal", "f returns 2", NEW)])
        self.write_new_test()
        self.red()
        self.fix_code()
        self.red()
        (self.code / "tests" / "test_later.py").write_text(
            "import unittest\n\n\nclass L(unittest.TestCase):\n    def test_l(self):\n        pass\n", encoding="utf-8")
        self.red()
        recs = self.records()
        self.assertNotEqual(recs[1]["tests"], recs[2]["tests"], recs)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)


class Mutant(_Project):

    def test_a_mutant_red_then_green_proves_and_names_its_reason(self):
        """Row 6: a must-not row shows it can fail on code broken on purpose; the reason is kept and shown."""
        self.light([("1", "must-not", "the old test holds", OLD)])
        self.break_code()
        r = self.red("--mutant", "f raises")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.records()[-1].get("mutant"), "f raises")
        self.g("checkout", "src.py", cwd=self.code)
        self.red()
        self.assertIn("f raises", self.section())
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(self.done())

    def test_a_mutant_needs_its_reason(self):
        self.light([("1", "must-not", "the old test holds", OLD)])
        for args in (("--mutant",), ("--mutant", "   ")):
            r = self.red(*args)
            self.assertNotEqual(r.returncode, 0, args)
        self.assertEqual(self.records(), [])


    def test_a_mutant_that_stays_green_is_said(self):
        """Row 26: the break did not reach the test — said, and the green proves nothing new."""
        self.light([("1", "must-not", "the old test holds", OLD)])
        r = self.red("--mutant", "nothing really broken")
        self.assertIn("came out green", r.stdout + r.stderr)


class Removed(_Project):

    def test_base_and_drop_are_refused(self):
        """Row 7."""
        self.light([("1", "normal", "f returns 2", NEW)])
        for args in (("--base", "code=HEAD"), ("--drop", "1", "--reason", "gone")):
            r = self.red(*args)
            self.assertNotEqual(r.returncode, 0, (args, r.stdout))
        self.assertEqual(self.records(), [])
        self.assertFalse((self.task_dir / "red-receipt.jsonl").exists())


class Base(_Project):

    def test_the_activation_records_heads_and_writes_no_object(self):
        """Row 8: base.json holds each scope's HEAD only; untracked files are never written into `.git/objects`."""
        (self.code / "untracked_note.txt").write_text("not in git\n", encoding="utf-8")
        (self.d / "outer_note.txt").write_text("not in git either\n", encoding="utf-8")
        before = (self.objects(self.d), self.objects(self.code))
        self.light([("1", "normal", "f returns 2", NEW)])
        outer_after = self.objects(self.d)
        # the light task's own commit adds objects to the outer repository; the code root gets none
        self.assertEqual(self.objects(self.code), before[1])
        base = json.loads((self.task_dir / "base.json").read_text(encoding="utf-8"))
        self.assertEqual(base.get("v"), 2, base)
        self.assertEqual(set(base["scopes"]), {"", "code"}, base)
        for scope, rec in base["scopes"].items():
            self.assertEqual(set(rec), {"head", "dirty"}, (scope, rec))   # the start's files by content (plan panel P8)
        self.assertIn("untracked_note.txt", base["scopes"]["code"]["dirty"])
        self.assertEqual(len(base["scopes"]["code"]["dirty"]["untracked_note.txt"]), 64)
        self.assertEqual(base["scopes"]["code"]["head"], self.g("rev-parse", "HEAD", cwd=self.code).stdout.strip())
        self.assertIsNotNone(outer_after)

    def test_a_run_writes_no_object(self):
        self.light([("1", "normal", "f returns 2", NEW)])
        (self.code / "untracked_note.txt").write_text("not in git\n", encoding="utf-8")
        self.write_new_test()
        before = (self.objects(self.d), self.objects(self.code))
        self.red()
        self.assertEqual((self.objects(self.d), self.objects(self.code)), before)


class NoChange(_Project):

    def test_pycache_is_no_code_change(self):
        """Row 9: the files verify writes are not code."""
        self.light(no_change="a records-only task")
        cache = self.code / "__pycache__"
        cache.mkdir()
        (cache / "src.cpython-310.pyc").write_bytes(b"\x00compiled")
        (self.code / "tests" / "__pycache__").mkdir()
        (self.code / "tests" / "__pycache__" / "test_old.cpython-310.pyc").write_bytes(b"\x00compiled")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(self.done(), out)
        line = self.call("print(red.numbers_line(p, next((p / '.agent' / 'tasks').glob('001-*'))))")
        self.assertIn("No behavior changes: a records-only task", line)     # row 33 (impl round 1, agy m)

    def test_a_code_change_blocks(self):
        """Row 10: a tracked edit, or a new untracked code file, still blocks `No behavior changes:`."""
        self.light(no_change="a records-only task")
        (self.code / "helper.py").write_text("x = 1\n", encoding="utf-8")
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("code changed since the start", out)
        (self.code / "helper.py").unlink()
        self.fix_code()
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("src.py", out)


    def test_docs_and_tests_are_no_code_change(self):
        """Row 21 (P7): only code counts."""
        self.light(no_change="a docs-only task")
        (self.d / "README.md").write_text("outer, edited\n", encoding="utf-8")
        (self.code / "tests" / "test_more.py").write_text("x = 1\n", encoding="utf-8")
        (self.code / "README.md").write_text("a readme\n", encoding="utf-8")   # a nested root's doc name
        (self.code / "docs").mkdir()
        (self.code / "docs" / "guide.md").write_text("a guide\n", encoding="utf-8")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)

    def test_a_file_dirty_at_the_start_is_no_change(self):
        """Row 22 (P8): a code file uncommitted before the activation, untouched since, is not a change."""
        (self.code / "wip.py").write_text("x = 1\n", encoding="utf-8")
        self.light(no_change="the task touches no code")
        base = json.loads((self.task_dir / "base.json").read_text(encoding="utf-8"))
        self.assertIn("wip.py", base["scopes"]["code"].get("dirty", {}), base)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)

    def test_a_code_file_renamed_into_docs_counts(self):
        """Row 23 (P9): the source of a rename is a change."""
        self.light(no_change="claims no code change")
        (self.code / "docs").mkdir()
        self.g("mv", "src.py", "docs/src.md", cwd=self.code)
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("src.py", out)

    def test_flagged_files_count_by_their_content(self):
        """Row 24 (P9): an edit hidden by assume-unchanged or skip-worktree still counts."""
        self.light(no_change="claims no code change")
        self.g("update-index", "--assume-unchanged", "src.py", cwd=self.code)
        self.fix_code()
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("src.py", out)
        self.g("update-index", "--no-assume-unchanged", "src.py", cwd=self.code)
        self.g("checkout", "src.py", cwd=self.code)
        self.g("update-index", "--skip-worktree", "src.py", cwd=self.code)
        self.fix_code()
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("src.py", out)

    def test_a_file_dirty_at_the_start_and_restored_counts(self):
        """Row 29 (impl round 1, I4: codex): a code file dirty at the activation and restored to HEAD since, or an
        untracked one deleted since, changed the code."""
        self.fix_code()                                           # dirty before the activation
        (self.code / "wip.py").write_text("x = 1\n", encoding="utf-8")
        self.light(no_change="claims no code change")
        self.g("checkout", "src.py", cwd=self.code)               # back to HEAD's bytes
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("src.py", out)
        self.fix_code()                                           # as at the start again
        (self.code / "wip.py").unlink()                           # an untracked code file gone
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("wip.py", out)

    def test_a_name_that_is_not_utf8_counts(self):
        """Row 30 (impl round 1, I6: codex, grok): a Linux file name that is not UTF-8, dirty at the start and edited
        since, moves the tree and counts under `No behavior changes:`."""
        name = os.fsencode(str(self.code)) + b"/legacy-\xff.py"
        with open(name, "wb") as fh:
            fh.write(b"X = 1\n")
        self.light(no_change="claims no code change")
        before = self.fingerprint()
        with open(name, "wb") as fh:
            fh.write(b"X = 2\n")
        self.assertNotEqual(self.fingerprint(), before)
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("legacy-", out)

    def test_a_commit_over_a_start_dirty_file_counts(self):
        """Row 35 (post-panel run 1, codex): a file dirty at the start, a further edit committed, the work tree back to
        the start's bytes — HEAD holds the task's edit; committing the start's own dirty bytes is no change."""
        self.fix_code()                                           # dirty at the start
        self.light(no_change="claims no code change")
        (self.code / "src.py").write_text("def f():\n    return 3\n", encoding="utf-8")
        self.g("commit", "-qam", "a further edit", cwd=self.code)
        self.fix_code()                                           # the start's bytes again
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("src.py", out)
        self.g("commit", "-qam", "the start's own bytes", cwd=self.code)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)

    def test_a_commit_over_a_start_dirty_file_counts_before_the_first_commit(self):
        """Row 35: the same in a code root with no commit at the start — its first commit is read from the empty tree."""
        shutil.rmtree(self.code / ".git")
        self.g("init", "-q", cwd=self.code)                       # unborn: every file is dirty at the start
        self.light(no_change="claims no code change")
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "the start's own bytes", cwd=self.code)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.cli("work", "1", "--reopen")
        (self.code / "src.py").write_text("def f():\n    return 3\n", encoding="utf-8")
        self.g("commit", "-qam", "a further edit", cwd=self.code)
        (self.code / "src.py").write_text("def f():\n    return 1\n", encoding="utf-8")   # the start's bytes
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("src.py", out)

    def test_a_lost_executable_bit_counts(self):
        """Row 37 (post-panel run 2, codex): a script dirty at the start loses its executable bit — in the work tree,
        then committed — and the code changed; committed with the bit kept, it is no change."""
        sh = self.code / "run.sh"
        sh.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        os.chmod(sh, 0o755)                                        # untracked, dirty at the start
        self.light(no_change="claims no code change")
        os.chmod(sh, 0o644)
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("run.sh", out)
        self.g("add", "run.sh", cwd=self.code)
        self.g("commit", "-qm", "the script, not executable", cwd=self.code)
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("run.sh", out)
        os.chmod(sh, 0o755)
        self.g("add", "run.sh", cwd=self.code)
        self.g("commit", "-qm", "the script as it was", cwd=self.code)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)

    def test_a_committed_edit_undone_in_the_work_tree_counts(self):
        """Row 31 (impl round 1, I7: grok): HEAD holds a code edit even when the work tree is back to the start's
        bytes."""
        self.light(no_change="claims no code change")
        self.fix_code()
        self.g("commit", "-qam", "the edit", cwd=self.code)
        (self.code / "src.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("src.py", out)


class Readers(_Project):

    def test_a_run_that_passed_no_test_is_skipped_and_blocks(self):
        """Row 12: a run that passed no test is `skipped`, never green; the close blocks on it."""
        (self.code / "tests" / "test_skip.py").write_text(
            "import unittest\n\n\nclass S(unittest.TestCase):\n    @unittest.skip('not yet')\n"
            "    def test_later(self):\n        self.fail()\n", encoding="utf-8")
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "a skipped test", cwd=self.code)
        self.light([("1", "normal", "later", "`tests.test_skip`")])
        self.red()
        self.assertEqual(self.records()[-1]["status"], "skipped")
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("skipped", out)
        # a green run that skipped a test (plan panel, grok) is no green either
        self.set_rows([("1", "normal", "later", "`cd code && python3 -m unittest tests.test_skip tests.test_old`")])
        self.red()
        self.assertEqual(self.records()[-1]["status"], "partly skipped")


if __name__ == "__main__":
    unittest.main()
