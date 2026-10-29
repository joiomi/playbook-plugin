"""PLAN-PLAYBOOK-2.md 4.1 (c) and grave A (task 338): two mechanisms are gone, and neither blocks or exists.

- Task 321's worktree check (`worktree_scope.stray_work`) refused `tasks verify` and the close while a lane-less Claude
  Code worktree held work; audit 6 found it too loose with `.agent/tasks` (G3) and too tight without (G5). The model
  uses no worktrees; the task-folder link comes with the lanes (7.4).
- `tasks prepare-merge` rewrote ordinary text in every task.md and chat_log.md ("2-hour" → "3-hour"; DESIGN-PLAYBOOK-2.md
  §7 A) and nothing called it. `tasks merge-doctor` stays.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


class Removed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".agent" / "tasks" / "001-x").mkdir(parents=True)
        (self.project / ".agent" / "tasks" / "001-x" / "task.md").write_text(
            "# 001 - x\n\n## Status\nin_progress\n\n## Risk\nreversible\n\nA 2-hour job, task 1 of the plan.\n",
            encoding="utf-8")
        (self.project / ".agent" / "config.json").write_text('{"verify": {"_always": ["true"]}}', encoding="utf-8")
        (self.project / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.project / ".gitignore").write_text(".agent/\n", encoding="utf-8")   # audit 6's G5: no task folders in git
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, env=self.clean)
        subprocess.run([*GIT, "-C", str(self.project), "add", "-A"], check=True, env=self.clean)
        subprocess.run([*GIT, "-C", str(self.project), "commit", "-q", "-m", "one"], check=True, env=self.clean)

    def run_tasks(self, *args):
        env = dict(self.clean, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="pid-removed-4-1c")
        env.pop("BASH_ENV", None)
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project, env=env, text=True,
                              capture_output=True, timeout=300)

    def test_a_worktree_does_not_block_verify(self):
        wt = self.project / ".claude" / "worktrees" / "w1"
        subprocess.run([*GIT, "-C", str(self.project), "worktree", "add", "-q", "-b", "w1", str(wt)], check=True,
                       env=self.clean)
        (wt / "NOTES.txt").write_text("another session's note\n", encoding="utf-8")   # G5: only an untracked note
        r = self.run_tasks("verify")
        self.assertNotIn("not where the work is", r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[PASS]", r.stdout + r.stderr)

    def test_prepare_merge_is_gone(self):
        log = self.project / ".agent" / "chat_log.md"
        log.write_text("# Project Chat Log\n\n---\n\n**[M001]** [2026-10-29 07:00:00 UTC] `HOST` (claude/pid-a)\n\n"
                       "a 2-hour job\n\n", encoding="utf-8")
        task_md = self.project / ".agent" / "tasks" / "001-x" / "task.md"
        before = (task_md.read_bytes(), log.read_bytes())
        r = self.run_tasks("prepare-merge", "main")
        self.assertIn("Unknown command: prepare-merge", r.stdout + r.stderr)
        self.assertEqual((task_md.read_bytes(), log.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
