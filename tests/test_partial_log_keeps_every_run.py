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
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tests._bwrap_standin import bwrap_usable  # noqa: E402
from tests._judge_stub import TASKS, clean_env  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
