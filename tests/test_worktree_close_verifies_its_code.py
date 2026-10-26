#!/usr/bin/env python3
"""A close certifies only the code that is where the work is (task 321, audit 5's G1).

Claude Code makes its worktrees in `.claude/worktrees/<name>/`, and a worktree that holds no task records answers to the
main tree's task (tasks 298, 302): its edits pass the gate under it. But `tasks work done` and `tasks verify` find the
project by walking up, so from inside such a worktree they ran the verify contract in the main tree and printed
`[PASS]` and "Task 001 done." over code verify never ran on (the blind session's reproduction; task 319's
`evidence/m2-repro/g1.out`). The same false close came from the main tree when the worktree still held work it had not
brought back. Run:
    python3 -m unittest tests.test_worktree_close_verifies_its_code
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "playbook"
SID = "pid-worktree-close"
DONE_TASK = ("# 001 - Fixit\n\n## Status\nin_progress\n\n## Risk\nreversible\n\n## Work Plan\n"
             "- [x] Change app\n  done in the worktree\n")


class _Project(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.p = self.base / "proj"
        self.p.mkdir()
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "LANG": "C.UTF-8",
                    "PYTHONPATH": str(PLUGIN), "PLAYBOOK_SESSION_ID": SID, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t"}
        self.git(self.p, "init", "-q", "-b", "main")
        (self.p / "src").mkdir()
        (self.p / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
        (self.p / ".agent").mkdir()
        (self.p / ".agent" / "config.json").write_text(
            json.dumps({"verify": "grep -q 'print(1)' src/app.py"}), encoding="utf-8")
        (self.p / ".gitignore").write_text(".agent/tasks/\n.agent/sessions/\n.agent/journal/\n.claude/worktrees/\n",
                                           encoding="utf-8")
        self.git(self.p, "add", "-A")
        self.git(self.p, "commit", "-qm", "base")
        td = self.p / ".agent" / "tasks" / "001-fixit"
        td.mkdir(parents=True)
        self.tf = td / "task.md"
        self.tf.write_text(DONE_TASK.replace("- [x] Change app\n  done in the worktree\n", "- [ ] Change app\n"),
                           encoding="utf-8")
        r = self.tasks(self.p, "work", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.tf.write_text(DONE_TASK, encoding="utf-8")

    def git(self, cwd, *args, check=True):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True, check=check)

    def tasks(self, cwd, *args):
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=cwd, env=self.env, capture_output=True,
                              text=True, timeout=300)

    def worktree(self, name="w1", branch="wt1", base=None):
        wt = self.p / ".claude" / "worktrees" / name
        self.git(self.p, "worktree", "add", "-q", str(wt), "-b", branch, *([base] if base else []))
        return wt

    def status(self) -> str:
        t = self.tf.read_text(encoding="utf-8")
        return t.split("## Status\n", 1)[1].split("\n", 1)[0]

    def assert_blocked(self, r, *names):
        said = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, said)
        self.assertNotIn("Task 001 done", said)
        self.assertEqual(self.status(), "in_progress")
        for n in names:
            self.assertIn(n, said)

    def assert_closed(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.status().startswith("done"), self.status())


class TheWorkMustBeWhereTheCloseLooks(_Project):
    def test_a_close_from_inside_the_worktree_is_refused(self):
        """Row 1: the blind session's G1 — the worktree's code breaks the contract, the main tree's keeps it."""
        wt = self.worktree()
        (wt / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
        self.assert_blocked(self.tasks(wt, "work", "done"), str(wt))

    def test_tasks_verify_from_inside_the_worktree_is_refused(self):
        """Row 2: `tasks verify` there printed `[PASS]` for the main tree's code."""
        wt = self.worktree()
        (wt / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
        r = self.tasks(wt, "verify")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("[PASS]", r.stdout + r.stderr)
        self.assertIn(str(wt), r.stdout + r.stderr)

    def test_a_close_from_the_main_tree_is_refused_while_a_worktree_holds_uncommitted_work(self):
        """Row 3: after ExitWorktree the session is back in the main tree; the work is still in the worktree (a name
        with a space)."""
        wt = self.worktree(name="w 1", branch="wt-space")
        (wt / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
        self.assert_blocked(self.tasks(self.p, "work", "done"), str(wt))

    def test_a_close_from_the_main_tree_is_refused_while_a_worktree_holds_unmerged_commits(self):
        """Row 4: the worktree committed its work on its branch; the main tree's HEAD does not have it."""
        wt = self.worktree()
        (wt / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
        self.git(wt, "commit", "-qam", "the work")
        self.assert_blocked(self.tasks(self.p, "work", "done"), str(wt))

    def test_control_a_merged_clean_worktree_does_not_block(self):
        """Row 5 (must-not): the worktree's commit is merged into the main tree and its tree is clean."""
        wt = self.worktree()
        (wt / "src" / "new.py").write_text("y = 1\n", encoding="utf-8")
        self.git(wt, "add", "-A")
        self.git(wt, "commit", "-qm", "the work")
        self.git(self.p, "merge", "-q", "--ff-only", "wt1")
        self.assert_closed(self.tasks(self.p, "work", "done"))

    def test_control_a_worktree_with_its_own_lane_is_not_this_tasks_work(self):
        """Row 6 (must-not): a worktree that holds task records answers to its own tasks (task 298), not to this one."""
        wt = self.worktree()
        (wt / ".agent" / "tasks" / "001-own").mkdir(parents=True)
        (wt / ".agent" / "tasks" / "001-own" / "task.md").write_text("# 001 - Own\n", encoding="utf-8")
        (wt / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
        self.assert_closed(self.tasks(self.p, "work", "done"))

    def test_control_a_nested_repository_is_no_worktree(self):
        """Row 7 (must-not): a nested repository (`code_roots`) is another repository, not a worktree of this one; a
        close from inside it is the project's close."""
        nested = self.p / "lib"
        nested.mkdir()
        self.git(nested, "init", "-q", "-b", "main")
        (nested / "m.py").write_text("z = 1\n", encoding="utf-8")
        self.git(nested, "add", "-A")
        self.git(nested, "commit", "-qm", "nested")
        self.assert_closed(self.tasks(nested, "work", "done"))

    def test_control_force_with_a_reason_still_closes_and_records_it(self):
        """Row 8 (must-not): the refusal is a close check, so `--force --reason` passes it and the receipt says why."""
        wt = self.worktree()
        (wt / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
        r = self.tasks(self.p, "work", "done", "--force", "--reason", "the worktree is another session's spike")
        self.assert_closed(r)
        self.assertIn("another session's spike", self.tf.read_text(encoding="utf-8"))

    def test_a_worktree_git_cannot_read_blocks(self):
        """Row 9 (hostile): a worktree whose `.git` file points nowhere cannot be judged — fail closed, named."""
        wt = self.worktree()
        (wt / ".git").write_text("gitdir: /nonexistent/nowhere\n", encoding="utf-8")
        self.assert_blocked(self.tasks(self.p, "work", "done"), str(wt))

    def test_control_commits_a_remote_has_are_not_the_tasks_work(self):
        """Row 10 (must-not): a worktree made from a remote branch ahead of the main tree's HEAD (Claude Code's
        `worktree.baseRef: fresh`) holds commits that are not this task's; with nothing of its own it does not block."""
        remote = self.base / "remote.git"
        self.git(self.base, "init", "-q", "--bare", "-b", "main", str(remote))
        self.git(self.p, "remote", "add", "origin", str(remote))
        self.git(self.p, "push", "-q", "origin", "main")
        other = self.base / "other"
        self.git(self.base, "clone", "-q", str(remote), str(other))
        (other / "upstream.txt").write_text("u\n", encoding="utf-8")
        self.git(other, "add", "-A")
        self.git(other, "commit", "-qm", "upstream")
        self.git(other, "push", "-q", "origin", "main")
        self.git(self.p, "fetch", "-q", "origin")
        self.worktree(base="origin/main")
        self.assert_closed(self.tasks(self.p, "work", "done"))


if __name__ == "__main__":
    unittest.main()
