#!/usr/bin/env python3
"""`## Status` is written only by the CLI (task 292; audit 1, G4 and item 4).

His blind check (2026-10-22): an Edit wrote `done` under `## Status` and the task gate passed it — every edit under
`.agent/` was exempt — so the task read done with its gates open, left bootstrap's open list and let the session stop.
And 289's panel (item 4): a multi-line intent at `tasks new` (`fix\\n## Status\\ndone`) wrote a second live `## Status`,
the same lie made by the CLI. Each test names the row of task 292's contract. The real hook path (`task-gate-hook`)
on real files, through the batch guard's fixture; `tasks new` through the CLI in a temporary project.

Run: python3 -m unittest tests.test_status_is_the_clis
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))
from tests.test_batch_close_guard import ProjectFixture  # noqa: E402
from tests.test_red_receipt import Project  # noqa: E402
from tasks.core import _atx_h2_text, _extract_status, _iter_fenced_flags, _physical_lines  # noqa: E402


def _edit(f, path, old, new, replace_all=False):
    return f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Edit",
                       "tool_input": {"file_path": str(path), "old_string": old, "new_string": new,
                                      "replace_all": replace_all}})


def _live_status_headings(path):
    lines = _physical_lines(Path(path).read_text(encoding="utf-8"))
    flags = _iter_fenced_flags(lines, unclosed_is_live=False)
    return sum(1 for i, ln in enumerate(lines) if not flags[i] and _atx_h2_text(ln) == "## Status")


class AStatusEditIsRefused(unittest.TestCase):
    def test_an_edit_that_writes_done(self):
        # row 1 (the bug): the active task's `## Status` from pending to done by an Edit
        f = ProjectFixture()
        r = _edit(f, f.task_file, "## Status\npending", "## Status\ndone")
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        self.assertIn(b"tasks work done", r.stderr)

    def test_a_write_that_writes_done(self):
        # row 2: the whole file rewritten with another status
        f = ProjectFixture()
        text = f.task_file.read_text(encoding="utf-8").replace("## Status\npending", "## Status\ndone")
        r = f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Write",
                        "tool_input": {"file_path": str(f.task_file), "content": text}})
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_a_multiedit_that_writes_blocked(self):
        # row 3: a MultiEdit, the status changed in its second edit
        f = ProjectFixture()
        edits = [{"old_string": "## Work Plan", "new_string": "## Work Plan\n"},
                 {"old_string": "## Status\npending", "new_string": "## Status\nblocked"}]
        r = f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "MultiEdit",
                        "tool_input": {"file_path": str(f.task_file), "edits": edits}})
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_a_second_live_status_added_elsewhere(self):
        # row 4: a new `## Status` heading further down — the last live one is the one the CLI reads
        f = ProjectFixture()
        r = _edit(f, f.task_file, "## Work Plan", "## Status\ndone\n\n## Work Plan")
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        # even one that repeats the same value: a second live heading is structure the CLI did not write
        g = ProjectFixture()
        r = _edit(g, g.task_file, "## Work Plan", "## Status\npending\n\n## Work Plan")
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_another_tasks_status(self):
        # row 5: a task.md of this project that is not the active one
        f = ProjectFixture()
        other = f.task_file.parent.parent / "002-other" / "task.md"
        other.parent.mkdir()
        other.write_text("# 002 - Other\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] a gate\n", encoding="utf-8")
        r = _edit(f, other, "## Status\nin_progress", "## Status\ndone")
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_an_edit_the_guard_cannot_place_in_a_task_md(self):
        # row 6 (hostile): another task's task.md, an old_string the guard cannot find — it cannot tell what changes
        f = ProjectFixture()
        other = f.task_file.parent.parent / "002-other" / "task.md"
        other.parent.mkdir()
        other.write_text("# 002 - Other\n\n## Status\nin_progress\n", encoding="utf-8")
        r = _edit(f, other, "## Status\nin progress", "## Status\ndone")
        self.assertEqual(r.returncode, 2, r.stderr.decode())


class TheOtherEditsPass(unittest.TestCase):
    def test_ticks_notes_and_prose(self):
        # row 7 (must-not)
        f = ProjectFixture()
        for old, new in (("- [ ] G1: run the suite", "- [x] G1: run the suite\n  - 283 tests green"),
                         ("## Work Plan", "## Work Plan\n\nA line of prose."),):
            with self.subTest(old=old):
                r = _edit(f, f.task_file, old, new)
                self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_a_fenced_status_example(self):
        # row 8 (must-not): a `## Status` inside a fence is content, not the status
        f = ProjectFixture()
        f.task_file.write_text(f.task_file.read_text(encoding="utf-8")
                               + "\n## Notes\n```text\n## Status\nblocked\n```\n", encoding="utf-8")
        r = _edit(f, f.task_file, "```text\n## Status\nblocked\n```", "```text\n## Status\ndone\n```")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        # and a new fenced example of the heading is no live heading added (my mutant M3)
        g = ProjectFixture()
        r = _edit(g, g.task_file, "## Work Plan", "## Work Plan\n\n```text\n## Status\ndone\n```")
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_a_task_md_of_another_project(self):
        # row 9 (must-not): the guard is for this project's records
        f = ProjectFixture()
        g = ProjectFixture()
        r = _edit(f, g.task_file, "## Status\npending", "## Status\ndone")
        self.assertEqual(r.returncode, 0, r.stderr.decode())


class AMultiLineIntent(Project):
    def test_the_intent_adds_no_structure(self):
        # row 10 (item 4): `tasks new … 'fix\n## Status\ndone'` — one live `## Status`, the task reads pending
        r = self.cli("new", "feature", "thing", "fix\n## Status\ndone")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tm = next((self.d / ".agent" / "tasks").glob("001-*")) / "task.md"
        self.assertEqual(_live_status_headings(tm), 1)
        self.assertEqual(_extract_status(tm), "pending")
        self.assertIn("fix ## Status done", tm.read_text(encoding="utf-8"))  # the words kept, on one line

    def test_a_one_line_intent_is_unchanged(self):
        # row 11 (must-not)
        r = self.cli("new", "feature", "thing", "fix the thing")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tm = next((self.d / ".agent" / "tasks").glob("001-*")) / "task.md"
        self.assertIn("## Intent\nfix the thing\n", tm.read_text(encoding="utf-8"))


class TheDocsSayIt(unittest.TestCase):
    def test_architecture_names_the_guard(self):
        # row 12: docs/architecture.md says `## Status` is the CLI's
        doc = (_HERE.parent / "docs" / "architecture.md").read_text(encoding="utf-8")
        self.assertIn("Guard 0.6", doc)
        self.assertIn("`## Status` is the CLI's", doc)


if __name__ == "__main__":
    unittest.main()
