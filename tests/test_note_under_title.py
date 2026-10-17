#!/usr/bin/env python3
"""A gate's note goes on the lines under its title, and the readers take it so (task 225).

The owner, 2026-10-16 09:36 (task 218): "Liste scurte de la început (Recomandat)" — the template, the guide and the map
write one fact per line, and a gate's note goes on the lines under its title. The batch-tick guard used to count only
what the gate's own line gained, and the echo called a tick bare when its text equalled the open gate's — so a note
under the title was blocked and warned about. Now the note is the lines indented deeper than the gate, the rule the
judges' trim already had (`core.gate_note_ends`).

Pure stdlib unittest. Run: python3 tests/test_note_under_title.py
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import core  # noqa: E402

GUARD = _PLAYBOOK / "scripts" / "gate-batch-check.py"
FENCE = "`" * 3


def _guard_module():
    spec = importlib.util.spec_from_file_location("gate_batch_check_225", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GuardCase(unittest.TestCase):
    """The guard run as task-gate-hook runs it: the payload on stdin, exit 2 = block."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb225g-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.sd = self.d / "session"
        self.sd.mkdir()
        self.file = self.d / "task.md"

    def run_guard(self, tool, payload):
        (self.sd / "last_batch_close").unlink(missing_ok=True)     # each case is its own batch
        r = subprocess.run([sys.executable, str(GUARD), "--tool", tool, "--file", str(self.file),
                            "--session-dir", str(self.sd)], input=json.dumps({"tool_input": payload}),
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout

    def edit(self, old, new):
        return self.run_guard("Edit", {"old_string": old, "new_string": new})[0]

    def write(self, old, new):
        self.file.write_text(old, encoding="utf-8")
        return self.run_guard("Write", {"content": new})[0]

    def multiedit(self, old, new):
        self.file.write_text(old, encoding="utf-8")
        return self.run_guard("MultiEdit", {"edits": [{"old_string": old, "new_string": new}]})[0]

    def all_tools(self, old, new, want):
        for name, fn in (("Edit", self.edit), ("Write", self.write), ("MultiEdit", self.multiedit)):
            with self.subTest(tool=name):
                self.assertEqual(fn(old, new), want)


OPEN = "- [ ] A one\n- [ ] B two\n"


class TheGuardReadsTheNoteUnderTheTitle(GuardCase):

    def test_notes_under_the_titles_pass(self):
        self.all_tools(OPEN, "- [x] A one\n  - built a.py, three tests\n- [x] B two\n  - verify 9/9 green\n", 0)

    def test_bare_ticks_still_block(self):
        self.all_tools(OPEN, "- [x] A one\n- [x] B two\n", 2)

    def test_a_same_line_note_still_counts(self):
        self.all_tools(OPEN, "- [x] A one — built a.py\n- [x] B two — verify 9/9\n", 0)

    def test_the_floor_is_over_the_sum(self):
        """P10: a 3-character suffix and a 5-character line under it make 8."""
        self.assertEqual(self.edit(OPEN, "- [x] A one abc\n  - defgh\n- [x] B two\n  - verify 9/9\n"), 0)
        self.assertEqual(self.edit(OPEN, "- [x] A one\n  - ok\n- [x] B two\n  - verify 9/9 green\n"), 2)

    def test_an_unindented_line_under_the_title_is_no_note(self):
        """P3: only lines indented deeper than the gate are its note."""
        self.all_tools(OPEN, "- [x] A one\nbuilt a.py, three tests\n- [x] B two\nverify 9/9 green\n", 2)

    def test_a_blank_line_then_a_deeper_note_counts(self):
        self.assertEqual(self.edit(OPEN, "- [x] A one\n\n  - built a.py, three tests\n- [x] B two\n  - verify 9/9\n"), 0)

    def test_a_nested_gate_is_no_note(self):
        self.all_tools(OPEN, "- [x] A one\n  - [ ] a sub step to do\n- [x] B two\n  - [ ] another sub step\n", 2)

    def test_a_note_written_before_the_tick_does_not_count(self):
        """P1: the note lines of OLD add nothing; a line going on from one adds only what follows."""
        old = "- [ ] A one\n  - planned: build it all\n- [ ] B two\n  - planned: verify all\n"
        self.all_tools(old, "- [x] A one\n  - planned: build it all\n- [x] B two\n  - planned: verify all\n", 2)
        self.all_tools(old, "- [x] A one\n  - planned: build it all!\n- [x] B two\n  - planned: verify all!\n", 2)
        self.all_tools(old, "- [x] A one\n  - planned: build it all\n  - built a.py, 3 tests\n"
                            "- [x] B two\n  - planned: verify all\n  - verify 9/9 green\n", 0)

    def test_two_open_copies_of_one_title(self):
        """P1 (agy's Critical): a note under one copy of a title never counts for the other."""
        old = "- [ ] X\n  - a long note written while it was open\n- [ ] X\n- [ ] Y\n"
        new = "- [x] X\n  - a long note written while it was open\n- [x] X\n- [x] Y\n  - verify 9/9 green\n"
        self.all_tools(old, new, 2)

    def test_a_note_moved_from_one_gate_to_another_adds_nothing(self):
        """P1 (sonnet): moving a note is not an outcome — stated, and blocked."""
        old = "- [ ] A one\n  - built a.py, three tests\n- [ ] B two\n- [ ] C three\n"
        new = "- [x] A one\n- [x] B two\n  - built a.py, three tests\n- [x] C three\n  - verify 9/9 green\n"
        self.assertEqual(self.write(old, new), 2)

    def test_a_fenced_example_in_a_note_is_no_gate(self):
        """P6 (codex): a checklist inside a code fence is an example, not a born-checked gate."""
        new = (f"- [x] A one\n  - the shape:\n  {FENCE}\n  - [x] an example line\n  {FENCE}\n"
               "- [x] B two\n  - verify 9/9 green\n")
        self.all_tools(OPEN, new, 0)

    def test_an_unclosed_fence_hides_no_tick(self):
        """P6, the counters' polarity: an unclosed fence's lines stay live, so it can never hide a bare batch."""
        self.all_tools(f"{FENCE}\n" + OPEN, f"{FENCE}\n- [x] A one\n- [x] B two\n", 2)

    def test_the_messages_say_under_the_title(self):
        code, out = self.run_guard("Edit", {"old_string": OPEN, "new_string": "- [x] A one\n- [x] B two\n"})
        self.assertEqual(code, 2)
        self.assertIn("lines under", out)
        self.assertIn("indented two spaces deeper", out)
        self.assertNotIn("appended to the gate text", out)
        code, out = self.run_guard("Edit", {"old_string": OPEN, "new_string": "- [x] A one rewritten\n- [x] B 2\n"})
        self.assertEqual(code, 2)
        self.assertNotIn("APPEND your outcome after it", out)
        self.assertIn("lines under", out)


class TheEchoReadsTheNoteUnderTheTitle(unittest.TestCase):
    """`task-status.py --echo`: a tick is `done-bare` only when nothing was written — not after the title and not under
    it. The previous gate T was `A one`, open at position 0 (P=0 B=0 OPENS=1 CHECKS=0 ABOVE=0)."""

    def transition(self, text):
        d = Path(tempfile.mkdtemp(prefix="pb225e-"))
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "task.md"
        f.write_text(text, encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", str(_PLAYBOOK / "scripts" / "task-status.py"), "--echo", str(f),
                            "0", "0", "1", "0", "0", "A one"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split("\n")[5]

    def test_a_note_under_the_tick_is_no_bare_tick(self):
        self.assertEqual(self.transition("## Work\n- [x] A one\n  - built a.py\n- [ ] B two\n"), "done")
        self.assertEqual(self.transition("## Work\n- [x] A one\n\n  - built a.py\n- [ ] B two\n"), "done")

    def test_nothing_written_is_still_bare(self):
        self.assertEqual(self.transition("## Work\n- [x] A one\n- [ ] B two\n"), "done-bare")
        self.assertEqual(self.transition("## Work\n- [x] A one\nbuilt it, unindented\n- [ ] B two\n"), "done-bare")
        self.assertEqual(self.transition("## Work\n- [x] A one\n  - [ ] a nested gate\n- [ ] B two\n"), "done-bare")

    def test_a_note_after_the_title_still_counts(self):
        self.assertEqual(self.transition("## Work\n- [x] A one — built\n- [ ] B two\n"), "done")


class TheInstructionsSayUnderTheTitle(unittest.TestCase):
    """W5 / P2 / P3: every instruction the plugin gives says where the note goes, with its indentation; none still asks
    for it on the gate's line."""

    OLD = ("append your outcome", "appending your observations", "same line", "on this line", "each line carries",
           "outcome note per line", "per-line outcome", "appended to the gate text", "append what happened")

    def texts(self):
        from tasks import template
        yield "guide", template.guide_text()
        yield "workflow_briefing", template.workflow_briefing()
        yield "agents_md_template", template.agents_md_template()
        yield "antigravity_md_template", template.antigravity_md_template()
        yield "light render", template.render_template(1, "X", "light")
        yield "CLAUDE.md.template", (_PLAYBOOK / "scripts" / "CLAUDE.md.template").read_text(encoding="utf-8")
        yield "gate-batch-check.py", GUARD.read_text(encoding="utf-8")
        yield "state-echo-hook", (_PLAYBOOK / "scripts" / "state-echo-hook").read_text(encoding="utf-8")

    def test_none_asks_for_a_same_line_note(self):
        for name, text in self.texts():
            low = text.lower()
            for phrase in self.OLD:
                with self.subTest(text=name, phrase=phrase):
                    self.assertNotIn(phrase, low)

    def test_each_says_under_it_with_the_indentation(self):
        for name, text in self.texts():
            if name in ("light render", "gate-batch-check.py", "state-echo-hook"):
                continue
            with self.subTest(text=name):
                self.assertIn("lines under it", text)
                self.assertIn("indented two", text.replace("\n", " "))


class TheGuardsRuleIsCoresRule(unittest.TestCase):
    """P4: the guard keeps a copy (it imports nothing it could fail on); this holds it equal to `core.gate_note_ends`."""

    SHAPES = [
        ["- [x] A", "  - note", "- [ ] B"],
        ["- [x] A", "", "  - note after a blank", "- [ ] B"],
        ["- [x] A", "", "- [ ] B"],
        ["- [x] A", "plain prose", "- [ ] B"],
        ["- [x] A", "  - [ ] nested", "  - note of the nested gate", "- [ ] B"],
        ["  - [x] A", "    - deeper note", "  - [ ] B", "- [ ] C"],
        ["- [x] A", "  - note", "### heading", "  - not a note"],
        ["- [x] A", "\t- tab note", "- [ ] B"],
        ["- [x] A", "  - note", "", ""],
    ]

    def test_the_two_rules_agree(self):
        gb = _guard_module()
        for lines in self.SHAPES:
            gates = {i for i, ln in enumerate(lines) if gb.GATE_RE.match(ln)}
            ends = core.gate_note_ends(lines, gates)
            for k in sorted(gates):
                with self.subTest(lines=lines, gate=k):
                    want = [ln.strip() for ln in lines[k + 1:ends[k]] if ln.strip()]
                    self.assertEqual(gb._note_lines(lines, k, gates), want)


if __name__ == "__main__":
    unittest.main(verbosity=2)
