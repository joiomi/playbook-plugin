#!/usr/bin/env python3
"""An Edit of task.md is judged by the whole file it leaves, as a Write is (task 291; audit 1, G1 and item 9).

His blind check (2026-10-22): one Edit with `replace_all` turning `- [ ]` into `- [x]` ticked every gate with no note,
and the batch-close guard let it through — it judged the Edit by its fragment, where `- [ ]` is one empty gate. And
289's panel (item 9): a fragment that starts in the middle of a line (`[ ] A` …) hides a tick, so two gates closed
bare read as one. Each test names the row of task 291's contract. The real hook path (`task-gate-hook`) on a real
task.md, through part of the batch guard's own fixture.

Run: python3 -m unittest tests.test_batch_guard_whole_file
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from tests.test_batch_close_guard import G, NOTE, ProjectFixture, checked  # noqa: E402


def _edit(f, old, new, replace_all=False):
    return f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Edit",
                       "tool_input": {"file_path": str(f.task_file), "old_string": old, "new_string": new,
                                      "replace_all": replace_all}})


class ReplaceAllTicksEveryGate(unittest.TestCase):
    def test_replace_all_of_the_box_is_judged_on_every_gate(self):
        # row 1 (the bug): six gates, `- [ ]` → `- [x]` with replace_all
        f = ProjectFixture()
        r = _edit(f, "- [ ]", "- [x]", replace_all=True)
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_replace_all_of_the_bare_box_too(self):
        # row 2: `[ ]` → `[x]`
        f = ProjectFixture()
        r = _edit(f, "[ ]", "[x]", replace_all=True)
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_two_gates_ticked_bare_by_replace_all(self):
        # row 3: a file of two open gates — a bare batch of two
        f = ProjectFixture(gates=G[:2])
        r = _edit(f, "- [ ]", "- [x]", replace_all=True)
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        self.assertIn(b"outcome note", r.stderr)


class AFragmentFromMidLine(unittest.TestCase):
    def test_a_fragment_that_starts_inside_a_line_hides_no_tick(self):
        # row 4 (item 9): old_string starts after `- `, so the fragment's first line is no gate
        f = ProjectFixture()
        old = G[0][len("- "):] + "\n" + G[1]
        new = checked(G[0])[len("- "):] + "\n" + checked(G[1])
        r = _edit(f, old, new)
        self.assertEqual(r.returncode, 2, r.stderr.decode())


class TheFreedomsStay(unittest.TestCase):
    def test_one_gate_ticked_bare_or_with_its_note(self):
        # row 5 (must-not)
        for note in ("", NOTE):
            with self.subTest(note=bool(note)):
                f = ProjectFixture()
                r = _edit(f, G[0], checked(G[0], note))
                self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_five_gates_with_their_notes_in_one_edit(self):
        # row 6 (must-not): the annotated batch, notes on the lines under each gate
        f = ProjectFixture()
        old = "\n".join(G[:5])
        new = "\n".join(checked(g) + "\n  - " + NOTE.strip(" —") for g in G[:5])
        r = _edit(f, old, new)
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_an_edit_elsewhere_in_task_md(self):
        # row 7 (must-not)
        f = ProjectFixture()
        r = _edit(f, "## Status\npending", "## Status\npending\n\nA line of prose.")
        self.assertEqual(r.returncode, 0, r.stderr.decode())


class WhenTheFileCannotShowTheEdit(unittest.TestCase):
    def test_an_old_string_not_in_the_file_is_judged_on_its_fragment(self):
        # row 8 (hostile): the Edit tool can match what the file does not hold literally (quotes it normalises) — the
        # guard then judges the fragment, as before, never passes it unseen
        f = ProjectFixture()
        lines = [g.replace(":", "“:") for g in G[:3]]                   # not in the file byte for byte
        r = _edit(f, "\n".join(lines), "\n".join(checked(ln) for ln in lines))
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        r = _edit(ProjectFixture(), "\n".join(lines), "\n".join(checked(ln, NOTE) for ln in lines))
        self.assertEqual(r.returncode, 0, r.stderr.decode())


class TheDocsSayIt(unittest.TestCase):
    def test_the_docs_and_the_ledger_say_an_edit_is_judged_on_the_whole_file(self):
        # row 9: docs/architecture.md (Guard 0.5) and guarantee PB-BATCH-CLOSE
        import json
        root = _HERE.parent
        doc = (root / "docs" / "architecture.md").read_text(encoding="utf-8")
        self.assertIn("an `Edit` is judged on the whole file it leaves", doc)
        ledger = json.loads((root / "docs" / "guarantee-ledger.json").read_text(encoding="utf-8"))
        g = next(g for g in ledger["guarantees"] if g["id"] == "PB-BATCH-CLOSE")
        self.assertIn("an Edit judged on the whole file it leaves", g["statement"])
        self.assertTrue(any(p["reference"] == "ReplaceAllTicksEveryGate.test_replace_all_of_the_box_is_judged_on_every_gate"
                            for p in g["proofs"]))


if __name__ == "__main__":
    unittest.main()
