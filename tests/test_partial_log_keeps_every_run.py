#!/usr/bin/env python3
"""Task 312 (stabilization audit 4, finding G8): a judge cut at its hard timeout keeps every run in its partial log.

A single judge killed at its hard timeout has its text saved to `<stem>.partial.log` beside the main log (task 261).
Each cut run wrote that file whole, so a second cut run of the same judge replaced the first one's text: a review
that was paid for, and the only trace of what that judge saw, was lost. Since task 304 the main log keeps every run,
the newest first; the partial log now does the same.

Driven as a user runs it: the real `tasks plan-review`, the real review path and sandbox, a temporary HOME and a
stand-in `claude` on PATH that prints a marked first line and then sleeps past a short `--timeout`.

Skipped where bubblewrap cannot start a sandbox. Run:
    python3 -m unittest tests.test_partial_log_keeps_every_run
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tests._bwrap_standin import bwrap_usable  # noqa: E402
from tests._judge_stub import TASKS, clean_env  # noqa: E402

sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))

# answers the dead-pin probe; otherwise prints its run's mark and sleeps past the review's timeout
_STUB = r'''#!/bin/bash
for a in "$@"; do [ "$a" = "reply with exactly: ok" ] && { echo ok; exit 0; }; done
cat > /dev/null
echo "PARTIAL-$(cat "$PB_RUN_MARK") the judge's first words"
sleep 30
'''


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class TwoCutRuns(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        home = self.base / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".config" / "git").mkdir(parents=True)
        for f in (home / ".gitconfig", home / ".config" / "git" / "config"):    # git's settings, kept read-only
            f.write_text("", encoding="utf-8")
        bindir = self.base / "bin"
        bindir.mkdir()
        (bindir / "claude").write_text(_STUB, encoding="utf-8")
        (bindir / "claude").chmod(0o755)
        self.mark = self.base / "run-mark"
        self.p = self.base / "proj"
        self.p.mkdir()
        env = clean_env(HOME=str(home))
        self.env = clean_env(HOME=str(home), PATH=f"{bindir}{os.pathsep}{env['PATH']}", PB_RUN_MARK=str(self.mark))
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.p, check=True, env=env)
        (self.p / "README.md").write_text("x\n", encoding="utf-8")
        (self.p / ".gitignore").write_text(".agent/journal/\n", encoding="utf-8")    # judges read a fixed copy
        subprocess.run(["git", "add", "-A"], cwd=self.p, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "c0"], cwd=self.p, check=True, env=env)
        (self.p / ".agent" / "tasks").mkdir(parents=True)
        (self.p / ".agent" / "models.json").write_text(
            json.dumps({"panel": ["opus", "sonnet"], "default_judge": "sonnet"}), encoding="utf-8")
        r = self.tasks("new", "feature", "probe", "a probe task for a judge cut at its timeout")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.task_dir = next((self.p / ".agent" / "tasks").glob("001-*"))
        self.partial = self.task_dir / "judge.partial.log"

    def tasks(self, *args):
        return subprocess.run([str(TASKS), *args], cwd=self.p, env=self.env, capture_output=True, text=True,
                              timeout=300)

    def cut_run(self, mark):
        self.mark.write_text(mark, encoding="utf-8")
        r = self.tasks("plan-review", "1", "--timeout", "4")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("hard timeout", r.stdout + r.stderr)
        return r

    def test_the_partial_log_keeps_both_runs_newest_first(self):
        self.cut_run("ONE")
        self.assertIn("PARTIAL-ONE", self.partial.read_text(encoding="utf-8"))
        self.cut_run("TWO")
        text = self.partial.read_text(encoding="utf-8")
        self.assertIn("PARTIAL-TWO", text)
        self.assertIn("PARTIAL-ONE", text, "the first cut run's text was lost at the second")
        self.assertLess(text.index("PARTIAL-TWO"), text.index("PARTIAL-ONE"), "the newest run is not first")

    def test_its_header_is_the_newest_runs(self):
        self.cut_run("ONE")
        self.cut_run("TWO")
        lines = self.partial.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("# INCOMPLETE "), lines[:3])
        first_mark = next(ln for ln in lines if ln.startswith("PARTIAL-"))
        self.assertTrue(first_mark.startswith("PARTIAL-TWO"), first_mark)

    def test_a_linked_partial_log_is_not_read_through(self):
        self.cut_run("ONE")
        outside = self.base / "outside.txt"
        outside.write_text("OUTSIDE-TEXT not this judge's\n", encoding="utf-8")
        self.partial.unlink()
        self.partial.symlink_to(outside)
        self.cut_run("TWO")
        self.assertFalse(self.partial.is_symlink(), "the write went through the link")
        text = self.partial.read_text(encoding="utf-8")
        self.assertIn("PARTIAL-TWO", text)
        self.assertNotIn("OUTSIDE-TEXT", text, "the earlier runs were read through a link")
        self.assertEqual(outside.read_text(encoding="utf-8"), "OUTSIDE-TEXT not this judge's\n")



def _save_in_a_process(task_file, log, text, a_read, b_go, slow):
    """One save of a cut run's text, in a process of its own; `slow` holds it between its read and its write."""
    sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))
    from tasks import review
    if slow:
        real = review._earlier_runs

        def held(path):
            got = real(path)
            a_read.set()
            b_go.wait(5)
            time.sleep(0.5)                              # the other save would read the same old text now
            return got
        review._earlier_runs = held
    else:
        a_read.wait(5)
        b_go.set()
    review._save_partial_log(Path(task_file), Path(log), text)


class TheSave(unittest.TestCase):
    """Impl round 1: the save is one helper, `review._save_partial_log`, so it can be held at its read."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name).resolve() / ".agent" / "tasks" / "001-x"
        d.mkdir(parents=True)
        self.task_file = d / "task.md"
        self.task_file.write_text("# 001 - x\n", encoding="utf-8")
        self.log = d / "judge.partial.log"
        self.log.write_text("RUN-ZERO the first cut\n", encoding="utf-8")

    def test_two_saves_at_once_keep_both_runs(self):
        # codex Important: two cut runs of one task read, read, replace, replace — without the lock one paid run is lost
        import multiprocessing
        ctx = multiprocessing.get_context("fork")
        a_read, b_go = ctx.Event(), ctx.Event()
        a = ctx.Process(target=_save_in_a_process, args=(self.task_file, self.log, "RUN-A\n", a_read, b_go, True))
        b = ctx.Process(target=_save_in_a_process, args=(self.task_file, self.log, "RUN-B\n", a_read, b_go, False))
        a.start()
        b.start()
        a.join(30)
        b.join(30)
        self.assertEqual((a.exitcode, b.exitcode), (0, 0))
        text = self.log.read_text(encoding="utf-8")
        for mark in ("RUN-A", "RUN-B", "RUN-ZERO"):
            self.assertIn(mark, text, "a run was lost between two saves at once")

    def test_an_earlier_log_that_cannot_be_read_still_saves_the_new_run(self):
        # opus: an unreadable old partial log raised inside the lock, and the new run's text was not saved at all
        if os.geteuid() == 0:
            self.skipTest("root reads a file of mode 000")
        from tasks import review
        self.log.chmod(0)
        self.addCleanup(lambda: self.log.chmod(0o644) if self.log.exists() else None)
        review._save_partial_log(self.task_file, self.log, "RUN-NEW the newest cut\n")
        self.log.chmod(0o644)
        self.assertTrue(self.log.read_text(encoding="utf-8").startswith("RUN-NEW the newest cut"))


if __name__ == "__main__":
    unittest.main()
