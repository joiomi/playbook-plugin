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


class _HookRun(unittest.TestCase):
    """The hook run with a chosen HOME, for the home-relative paths (rounds 1 and 2)."""

    def _home_fixture(self):
        f = ProjectFixture()
        return f, f.proj.parent                         # HOME such that `~/proj/…` is the project

    def _run(self, f, payload, home=None):
        import os
        import subprocess
        from tests._bashcheck import bash_or_skip
        from tests.test_batch_close_guard import HOOK, SESSION
        env = dict(os.environ, PLAYBOOK_SESSION_ID=SESSION)
        env.pop("PLAYBOOK_ROLE", None)
        if home is not None:
            env["HOME"] = str(home)
        return subprocess.run([bash_or_skip(), str(HOOK)], input=__import__("json").dumps(payload).encode(),
                              cwd=f.proj, env=env, capture_output=True, timeout=60)



class RoundOne(_HookRun):
    """Task 292's impl round 1 (opus, codex Important; agy, opus, grok Minor)."""

    def test_a_home_relative_path(self):
        # row 15 (codex): `~/proj/.agent/tasks/001-thing/task.md` — the tool expands `~` (measured: Read did), the hook
        # read it as text and skipped the guard
        f, home = self._home_fixture()
        path = "~/" + str(f.task_file.relative_to(home))
        r = self._run(f, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                          "tool_input": {"file_path": path, "old_string": "## Status\npending",
                                         "new_string": "## Status\ndone"}}, home=home)
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_a_decoy_where_the_text_and_the_kernel_disagree(self):
        # row 16 (codex): `<proj>/jump/../.agent/tasks/001-thing/task.md` with `jump → <out>/landing` — the kernel
        # reads `<out>/.agent/…` (a decoy that says done), the text `<proj>/.agent/…` (the record): every reading in a
        # lane is checked
        import tempfile
        f = ProjectFixture()
        out = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, out, True)
        (out / "landing").mkdir()
        decoy = out / ".agent" / "tasks" / "001-thing" / "task.md"
        decoy.parent.mkdir(parents=True)
        decoy.write_text("# 001 - Thing\n\n## Status\ndone\n", encoding="utf-8")
        (f.proj / "jump").symlink_to(out / "landing")
        path = str(f.proj / "jump") + "/../.agent/tasks/001-thing/task.md"
        # a Write of the record's text with `done`: against the decoy it changes nothing, against the record it does
        text = f.task_file.read_text(encoding="utf-8").replace("## Status\npending", "## Status\ndone")
        r = f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Write",
                        "tool_input": {"file_path": path, "content": text}})
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_both_readings_in_lanes_each_checked(self):
        # row 16, my mutant N3: the kernel's reading in another lane of the project (a decoy that says done), the
        # text's the record — the first reading passes, the second is the one written
        f = ProjectFixture()
        bob = f.proj / ".agent" / "bob"
        (bob / "landing").mkdir(parents=True)
        decoy = bob / "tasks" / "001-thing" / "task.md"
        decoy.parent.mkdir(parents=True)
        text = f.task_file.read_text(encoding="utf-8").replace("## Status\npending", "## Status\ndone")
        decoy.write_text(text, encoding="utf-8")
        (f.proj / ".agent" / "jump").symlink_to(bob / "landing")
        path = str(f.proj / ".agent" / "jump") + "/../tasks/001-thing/task.md"
        r = f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Write",
                        "tool_input": {"file_path": path, "content": text}})
        self.assertEqual(r.returncode, 2, r.stderr.decode())


class RoundTwo(_HookRun):
    """Task 292's impl round 2 (opus, codex, sonnet Important): `~` with `..` or `./`, and a BOM before the marker."""

    def test_a_home_relative_path_with_dot_segments(self):
        # row 18: `~/jump/../proj/…` (jump outside, or missing) and `~/proj/.agent/tasks/001-thing/./task.md`
        import tempfile
        for shape in ("dotdot-outside", "dotdot-missing", "dot"):
            with self.subTest(shape=shape):
                f, home = self._home_fixture()
                rel = str(f.task_file.relative_to(home))
                if shape == "dot":
                    path = "~/" + rel.replace("/001-thing/task.md", "/001-thing/./task.md")
                else:
                    if shape == "dotdot-outside":
                        out = Path(tempfile.mkdtemp())
                        self.addCleanup(__import__("shutil").rmtree, out, True)
                        (home / "jump").symlink_to(out)
                    path = "~/jump/../" + rel
                r = self._run(f, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                                  "tool_input": {"file_path": path, "old_string": "## Status\npending",
                                                 "new_string": "## Status\ndone"}}, home=home)
                self.assertEqual(r.returncode, 2, r.stderr.decode())


class PostPanelOne(_HookRun):
    """Task 292's post-panel run 1 (codex Important, reproduced): a `~/` path through a link to the task directory."""

    def test_a_home_relative_path_through_a_link_to_the_task_directory(self):
        # row 22: `.agent/record-alias` → `.agent/tasks/001-thing`; the lane test reads the record's grandparent on
        # the text of the path, so only the resolved reading of the expanded path names the lane
        f, home = self._home_fixture()
        (f.proj / ".agent" / "record-alias").symlink_to(f.task_file.parent)
        path = "~/" + str((f.proj / ".agent" / "record-alias" / "task.md").relative_to(home))
        r = self._run(f, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                          "tool_input": {"file_path": path, "old_string": "## Status\npending",
                                         "new_string": "## Status\ndone"}}, home=home)
        self.assertEqual(r.returncode, 2, r.stderr.decode())


class PostPanelTwo(_HookRun):
    """Task 292's post-panel run 2 (codex Important, reproduced): a missing component and a link to the task
    directory in one `~/` path — the three readings of a `~/` path are those of a plain one."""

    def test_a_missing_component_then_a_link_to_the_task_directory(self):
        # row 23: `~/missing/../proj/.agent/record-alias/task.md`, and the same as an absolute path (the control)
        f, home = self._home_fixture()
        (f.proj / ".agent" / "record-alias").symlink_to(f.task_file.parent)
        tail = str((f.proj / ".agent" / "record-alias" / "task.md").relative_to(home))
        for path in ("~/missing/../" + tail, str(home) + "/missing/../" + tail):
            with self.subTest(path=path[:24]):
                r = self._run(f, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                                  "tool_input": {"file_path": path, "old_string": "## Status\npending",
                                                 "new_string": "## Status\ndone"}}, home=home)
                self.assertEqual(r.returncode, 2, r.stderr.decode())


class AnIntentAfterRoundTwo(Project):
    def _new(self, *args):
        r = self.cli("new", "feature", *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return next((self.d / ".agent" / "tasks").glob("001-*")) / "task.md"

    def test_a_bom_before_the_marker(self):
        # row 19 (sonnet, codex Important): the readers skip a byte-order mark; the escape must look past it too
        bom = chr(0xFEFF)
        for intent in (bom + "## Status", bom + "- [x] a gate", " " + bom + "## Risk"):
            with self.subTest(intent=repr(intent)):
                self.setUp()
                tm = self._new("thing", intent)
                self.assertEqual(_live_status_headings(tm), 1)
                self.assertEqual(_extract_status(tm), "pending")
                lines = _physical_lines(tm.read_text(encoding="utf-8"))
                self.assertTrue(lines[lines.index("## Intent") + 1].startswith("\\"))

    def test_everyday_intents_keep_their_text(self):
        # row 20 (opus, sonnet Minor): only what the readers take as structure is escaped
        for intent in ("[WIP] x", "_foo_ bar", "1) fix it", "*x* first", "(a parenthesis)"):
            with self.subTest(intent=intent):
                self.setUp()
                tm = self._new("thing", intent)
                self.assertIn("## Intent\n" + intent + "\n", tm.read_text(encoding="utf-8"))

    def test_the_list_shows_the_words(self):
        # row 21 (sonnet Minor): `tasks list` shows an escaped intent without its backslash
        from tasks.core import _extract_problem
        tm = self._new("thing", "## Status")
        self.assertEqual(_extract_problem(tm), "## Status")
        # and past a byte-order mark, as the escape reads it (post-panel run 1, codex Minor)
        self.setUp()
        tm = self._new("thing", chr(0xFEFF) + "## Status")
        shown = _extract_problem(tm)
        self.assertFalse(shown.startswith("\\"), repr(shown))
        self.assertIn("## Status", shown)


class AnIntentOrANameWithStructure(Project):
    def _new(self, *args):
        r = self.cli("new", "feature", *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return next((self.d / ".agent" / "tasks").glob("001-*")) / "task.md"

    def test_an_intent_that_starts_with_structure(self):
        # row 13 (opus Important): one line, but it starts with a heading, a gate, a fence or a quote
        from tasks.core import _live_gate_scan
        baseline = None
        for intent in ("## Status", "  ## Risk", "- [x] a gate ticked at birth", "~~~", "```", "> quoted"):
            with self.subTest(intent=intent):
                self.setUp()
                tm = self._new("thing", intent)
                self.assertEqual(_live_status_headings(tm), 1)
                self.assertEqual(_extract_status(tm), "pending")
                lines = _physical_lines(tm.read_text(encoding="utf-8"))
                line = lines[lines.index("## Intent") + 1]
                self.assertTrue(line.startswith("\\"), line)     # its structure marker escaped: plain text
                self.assertIn(intent.strip(), line)              # and its words kept
                unchecked, total, _first, _i = _live_gate_scan(lines)
                self.assertEqual(unchecked, total)               # no gate born checked
                if baseline is None:
                    baseline = total
                self.assertEqual(total, baseline)                # no fence hid a gate

    def test_a_multi_line_name(self):
        # row 14 (agy, opus Minor): the title is one line too
        tm = self._new("fix\n## Status\ndone", "an intent")
        self.assertEqual(_live_status_headings(tm), 1)
        self.assertTrue(tm.read_text(encoding="utf-8").splitlines()[0].startswith("# 001 - "))
        self.assertNotIn("\n## Status\ndone", tm.read_text(encoding="utf-8"))


class TheJournalNamesTheGuard(unittest.TestCase):
    def test_a_status_refusal_is_journaled_as_the_status_guard(self):
        # row 17 (agy, opus, grok Minor): not as a batch close
        f = ProjectFixture()
        r = _edit(f, f.task_file, "## Status\npending", "## Status\ndone")
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        journal = f.proj / ".agent" / "journal" / "enforcement.jsonl"
        rows = [__import__("json").loads(ln) for ln in journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(rows[-1].get("hook"), "status-guard", rows[-1])


class TheDocsSayIt(unittest.TestCase):
    def test_architecture_names_the_guard(self):
        # row 12: docs/architecture.md says `## Status` is the CLI's
        doc = (_HERE.parent / "docs" / "architecture.md").read_text(encoding="utf-8")
        self.assertIn("Guard 0.6", doc)
        self.assertIn("`## Status` is the CLI's", doc)


if __name__ == "__main__":
    unittest.main()
