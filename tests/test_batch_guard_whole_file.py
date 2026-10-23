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
        self.assertIn(b"6 gates closed in one write", r.stderr)              # the ceiling: all six are seen

    def test_replace_all_of_the_bare_box_too(self):
        # row 2: `[ ]` → `[x]`
        f = ProjectFixture()
        r = _edit(f, "[ ]", "[x]", replace_all=True)
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        self.assertIn(b"6 gates closed in one write", r.stderr)

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

    def test_a_replace_all_that_ticks_one_gate(self):
        # row 10 (must-not): the gate's text also sits in a quote — two replacements, one gate: a single close
        f = ProjectFixture(gates=[G[0], "> " + G[0], *G[1:]])
        r = _edit(f, G[0], checked(G[0]), replace_all=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_an_edit_elsewhere_in_task_md(self):
        # row 7 (must-not)
        f = ProjectFixture()
        r = _edit(f, "## Status\npending", "## Status\npending\n\nA line of prose.")
        self.assertEqual(r.returncode, 0, r.stderr.decode())


class WhenTheFileCannotShowTheEdit(unittest.TestCase):
    def test_an_old_string_the_guard_cannot_find_does_not_tick(self):
        # row 8 (hostile; edited after impl round 2, opus): an old_string the guard cannot find in a readable file
        # either fails at the tool or matches by a rule the guard does not model — so an edit that ticks a box there
        # is refused, notes or not, and one that ticks nothing passes
        lines = ["- [ ] G8: not in the file", "- [ ] G9: nor this", "- [ ] G10: nor this one"]
        for note in ("", NOTE):
            with self.subTest(note=bool(note)):
                r = _edit(ProjectFixture(), "\n".join(lines), "\n".join(checked(ln, note) for ln in lines))
                self.assertEqual(r.returncode, 2, r.stderr.decode())
                self.assertIn(b"cannot find", r.stderr)
        r = _edit(ProjectFixture(), "- [ ] G0: not in the file", "- [ ] G0: not in the file, still open")
        self.assertEqual(r.returncode, 0, r.stderr.decode())                 # ticks nothing
        r = _edit(ProjectFixture(), "- [ ] G0: not in the file", "- [x] G0: rewritten while ticked")
        self.assertEqual(r.returncode, 2, r.stderr.decode())                 # a line born checked is a tick too
        # a surrogate pair spelled as two escapes (the tool may read it as one character; the guard does not)
        f = ProjectFixture(gates=["- [ ] \U0001F600 G1", "- [ ] \U0001F600 G2"])
        r = _edit(f, "- [ ] \\ud83d\\ude00", "- [x] \\ud83d\\ude00", replace_all=True)
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_a_multiedit_edit_the_guard_cannot_find_does_not_tick(self):
        # row 16 (hostile, impl round 2): the same for one edit of a MultiEdit
        f = ProjectFixture()
        edits = [{"old_string": G[0], "new_string": checked(G[0], NOTE)},
                 {"old_string": "- [ ] G9: not in the file", "new_string": "- [x] G9: not in the file" + NOTE}]
        r = f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "MultiEdit",
                        "tool_input": {"file_path": str(f.task_file), "edits": edits}})
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_an_unreadable_task_md_is_judged_on_the_fragments(self):
        # row 17 (opus Minor, impl round 2): the `except OSError` branch — the helper itself, on a file it cannot read
        import json, os, subprocess
        from tests.test_batch_close_guard import HELPER
        f = ProjectFixture()
        os.chmod(f.task_file, 0)
        self.addCleanup(os.chmod, f.task_file, 0o644)
        if os.access(f.task_file, os.R_OK):
            self.skipTest("running as a user who can read a mode-0 file")
        payload = {"tool_name": "Edit", "tool_input": {"file_path": str(f.task_file), "old_string": "\n".join(G[:2]),
                                                       "new_string": "\n".join(checked(g) for g in G[:2])}}
        r = subprocess.run([sys.executable, str(HELPER), "--tool", "Edit", "--file", str(f.task_file),
                            "--session-dir", str(f.session_dir)], input=json.dumps(payload).encode(),
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout.decode())
        self.assertIn(b"outcome note", r.stdout)

    def test_curly_quotes_do_not_hide_a_replace_all(self):
        # row 11 (hostile; mine, before the judge): the Edit tool matches an old_string across curly and straight
        # quotes, so the guard reads the file and the edit with the quotes made alike and judges the whole file
        f = ProjectFixture(gates=['- [ ] "a" G1', '- [ ] "a" G2', '- [ ] "a" G3'])
        r = _edit(f, "- [ ] “a”", "- [x] “a”", replace_all=True)
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        # and the other way round: curly in the file, straight in the old_string
        f = ProjectFixture(gates=["- [ ] “a” G1", "- [ ] “a” G2", "- [ ] “a” G3"])
        r = _edit(f, '- [ ] "a"', '- [x] "a"', replace_all=True)
        self.assertEqual(r.returncode, 2, r.stderr.decode())


class AsTheEditToolMatches(unittest.TestCase):
    """Impl round 1 (codex, opus Important), measured on the Edit tool itself (2026-10-23 05:20, scratchpad probes):
    it tries `\\uXXXX` escapes as their characters, it deletes the newline after a text replaced by nothing, and with
    curly and straight quotes read alike it replaces the file's own first spelling — the guard reads an edit the same
    way, or the ticks the tool makes are not the ones it judges."""

    def test_an_escaped_space_in_the_old_string(self):
        # row 12: `- [ ]` (a backslash escape, as the hook receives it) matches every `- [ ]`
        f = ProjectFixture()
        r = _edit(f, "- [\\u0020]", "- [x]", replace_all=True)
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        # and one gate closed that way, with its note, is found and passes (not refused as text the guard cannot find)
        r = _edit(ProjectFixture(), G[0].replace("[ ]", "[\\u0020]"), checked(G[0], NOTE))
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_a_deletion_takes_the_newline_after_it(self):
        # row 13: deleting `[ ] TODO` everywhere joins `- ` and the next line — two gates born checked
        f = ProjectFixture(gates=["- [ ] TODO", "[x] G1: x", "- [ ] TODO", "[x] G2: y"])
        r = _edit(f, "[ ] TODO", "", replace_all=True)
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_the_files_first_spelling_is_the_one_replaced(self):
        # row 14 (must-not): with the quotes read alike, the tool replaces the spelling it found first — one gate here,
        # a single close; reading every spelling at once over-blocked it
        f = ProjectFixture(gates=['- [ ] "a" "b" G1', "- [ ] “a” “b” G2", *G[:2]])
        r = _edit(f, '- [ ] “a” "b"', '- [x] “a” "b"', replace_all=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_a_multiedit_is_read_the_same_way(self):
        # row 15 (opus): a MultiEdit's old_string with other quotes, or an escape, ticks two gates bare — blocked
        f = ProjectFixture(gates=['- [ ] "a" G1', '- [ ] "a" G2', *G[:2]])
        for edits in ([{"old_string": "- [ ] “a” G1", "new_string": "- [x] “a” G1"},
                       {"old_string": "- [ ] “a” G2", "new_string": "- [x] “a” G2"}],
                      [{"old_string": '- [\\u0020] "a" G1', "new_string": '- [x] "a" G1'},
                       {"old_string": '- [\\u0020] "a" G2', "new_string": '- [x] "a" G2'}]):
            with self.subTest(edits=edits[0]["old_string"]):
                r = f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "MultiEdit",
                                "tool_input": {"file_path": str(f.task_file), "edits": edits}})
                self.assertEqual(r.returncode, 2, r.stderr.decode())
        # one gate closed that way, with its note, is found and passes
        one = [{"old_string": "- [ ] “a” G1", "new_string": "- [x] “a” G1" + NOTE}]
        r = f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "MultiEdit",
                        "tool_input": {"file_path": str(f.task_file), "edits": one}})
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
