"""Task 298 (stabilization audit 2, grave G3): a code edit inside a Claude Code worktree needs an active task.

The code-edit gate exempted every path holding a `.agent` or `.claude` component (task-gate-hook's management case;
its Python twin `provider.policy._is_management_path`). Claude Code makes its worktrees inside the repository, under
`.claude/worktrees/<name>/` (EnterWorktree, subagents run with worktree isolation), so every code file of a worktree
passed the gate with no active task — and the README promises "The agent can't edit code without an active task".
Now `.claude/worktrees/<name>/` is a checkout, not configuration: what is inside it is judged as the worktree's own
path (its own `.agent/` and `.claude/` stay exempt).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "playbook" / "scripts"
sys.path.insert(0, str(REPO_ROOT / "plugins" / "playbook"))
from provider.policy import _is_management_path  # noqa: E402

SID = "pid-worktree-gate"
TASK = "# 001 - real\n## Status\nin_progress\n## Work Plan\n- [ ] g\n"


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        self.wt = self.project / ".claude" / "worktrees" / "w1"
        for root in (self.project, self.wt):          # a worktree is a checkout: it holds the tracked task records
            d = root / ".agent" / "tasks" / "001-real"
            d.mkdir(parents=True)
            (d / "task.md").write_text(TASK, encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()

    def gate(self, tool, file_path, cwd=None):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "LANG": "C.UTF-8",
               "PLAYBOOK_SESSION_ID": SID}
        tool_input = {"file_path": str(file_path), "old_string": "x = 1", "new_string": "x = 2"} if tool == "Edit" \
            else {"file_path": str(file_path), "content": "y = 1\n"}
        return subprocess.run([bash_or_skip(), str(SCRIPTS / "task-gate-hook")], cwd=cwd or self.project, env=env,
                              text=True, input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
                              capture_output=True)

    def activate(self, root):
        pointer = root / ".agent" / "sessions" / SID / "current_state"
        pointer.parent.mkdir(parents=True)
        pointer.write_text("001\n", encoding="utf-8")


class AWorktreeIsCode(_Project):
    def test_an_edit_of_a_worktree_code_file_is_blocked(self):
        """Row 1: no active task, an Edit of `.claude/worktrees/w1/src/a.py` is blocked, as the main tree's is."""
        self.assertEqual(self.gate("Edit", self.project / "src" / "a.py").returncode, 2)
        r = self.gate("Edit", self.wt / "src" / "a.py")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_a_new_code_file_in_a_worktree_is_blocked(self):
        """Row 2: no active task, a Write of a new code file in the worktree is blocked."""
        r = self.gate("Write", self.wt / "src" / "new_module.py")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_a_session_inside_the_worktree_is_blocked_too(self):
        """Row 3: the session's directory is the worktree itself (EnterWorktree moves it there): still blocked."""
        r = self.gate("Edit", self.wt / "src" / "a.py", cwd=self.wt)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_the_python_twin_agrees(self):
        """Row 4: `_is_management_path` (the codex gate) says the same — a worktree's code is not management, also
        under a worktree name with a `/` in it."""
        self.assertFalse(_is_management_path(str(self.wt / "src" / "a.py")))
        self.assertFalse(_is_management_path("/p/.claude/worktrees/feat/x/src/a.py"))
        self.assertFalse(_is_management_path("/p/.claude/worktrees/w1/.claude/worktrees/w2/src/a.py"))


    def test_the_hook_agrees_with_the_twin_on_nested_and_slashed_names(self):
        """Row 8 (my mutant run): the hook strips the worktree segments as often as the twin does — a worktree inside a
        worktree, and a worktree whose name holds a `/` — so their code needs a task too, while their records stay
        exempt."""
        for p in (self.wt / ".claude" / "worktrees" / "w2" / "src" / "a.py",
                  self.project / ".claude" / "worktrees" / "feat" / "x" / "src" / "a.py"):
            with self.subTest(path=str(p)):
                self.assertEqual(self.gate("Edit", p).returncode, 2)
                self.assertFalse(_is_management_path(str(p)))
        rec = self.wt / ".claude" / "worktrees" / "w2" / ".agent" / "tasks" / "001-real" / "notes.py"
        self.assertEqual(self.gate("Write", rec).returncode, 0)
        self.assertTrue(_is_management_path(str(rec)))


class WhatStaysExempt(_Project):
    def test_the_worktrees_own_records_stay_exempt(self):
        """Row 5: inside a worktree, its own `.agent/` and `.claude/` stay exempt, in the hook and in the twin."""
        for p in (self.wt / ".agent" / "tasks" / "001-real" / "notes.py", self.wt / ".claude" / "hooks" / "x.sh"):
            with self.subTest(path=str(p)):
                self.assertEqual(self.gate("Write", p).returncode, 0)
                self.assertTrue(_is_management_path(str(p)))

    def test_the_projects_own_records_stay_exempt(self):
        """Row 6: the project's `.agent/` and `.claude/` (its settings, its bin wrappers) stay exempt."""
        for p in (self.project / ".claude" / "bin" / "tasks.sh", self.project / ".agent" / "tasks" / "001-real" / "x.py"):
            with self.subTest(path=str(p)):
                self.assertEqual(self.gate("Write", p).returncode, 0)
                self.assertTrue(_is_management_path(str(p)))

    def test_an_active_task_lets_the_worktree_edit_through(self):
        """Row 7: with an active task the worktree edit passes — the gate asks for a task, nothing more."""
        self.activate(self.project)
        r = self.gate("Edit", self.wt / "src" / "a.py")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
