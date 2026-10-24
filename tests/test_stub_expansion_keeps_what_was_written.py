#!/usr/bin/env python3
"""Task 299 (stabilization audit 2, grave G6): `tasks work` on a stub keeps what was written into it.

`tasks new --stub` writes a short record — Status, Risk, Intent, Why `(fill in before expanding)`, References
`(optional)`. `tasks work` expands it into the full template and kept only Intent and Why, and References only when
it no longer held `(optional)` (`lifecycle.py`, the stub expansion). A reference written under the placeholder, a
`## Risk` set in the stub, an added section and its gates were gone without a word — a lost open gate makes a task
look closer to closed (his clauses 3 and 4; his answer of 2026-10-23 23:33, task 295). Now each of them is carried
into the expanded file; an unclosed fence in the added text refuses the expansion, nothing written.

Run: python3 tests/test_stub_expansion_keeps_what_was_written.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins/playbook"
sys.path.insert(0, str(PLUGIN))
from tasks.core import _gate_counts, _has_unclosed_fence, extract_parked_items, extract_risk  # noqa: E402


class _Stub(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.project, True)
        (self.project / ".agent").mkdir()
        self.home = self.project / "home"
        self.home.mkdir()

    def tasks(self, *args):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "LANG": "C.UTF-8",
               "PYTHONPATH": str(PLUGIN), "PLAYBOOK_SESSION_ID": "pid-299", "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project, env=env,
                              capture_output=True, text=True, timeout=120)

    def stub(self, edit=None, kind="feature"):
        r = self.tasks("new", "--stub", kind, "importer", "build the importer")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.tf = sorted((self.project / ".agent" / "tasks").glob("*/task.md"))[-1]     # the newest stub
        if edit:
            self.tf.write_text(edit(self.tf.read_text(encoding="utf-8")), encoding="utf-8")
        return self.tf

    def expand(self):
        return self.tasks("work", self.tf.parent.name[:3])

    def text(self):
        return self.tf.read_text(encoding="utf-8")


class WhatWasWrittenIsKept(_Stub):
    def test_a_reference_under_the_placeholder_is_kept(self):
        """Row 1: a reference written under `(optional)` is in the expanded References; the placeholder line goes."""
        self.stub(lambda t: t.replace("(optional)", "(optional)\n- REF-77: the API contract is in docs/api.md"))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Expanded stub", r.stdout)
        refs = self.text().split("## References", 1)[1].split("\n---", 1)[0]
        self.assertIn("- REF-77: the API contract is in docs/api.md", refs)
        self.assertNotIn("(optional)", refs)

    def test_an_added_section_and_its_gate_are_kept(self):
        """Row 2: a `## Notes` section with an open gate, added to the stub, is in the expanded file, and the open
        gate is counted."""
        self.stub(lambda t: t.rstrip("\n") + "\n\n## Notes\nSTUB-NOTE-64: use the v2 endpoint\n"
                                             "- [ ] GATE-5: ask about rate limits\n")
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.text()
        self.assertIn("STUB-NOTE-64: use the v2 endpoint", t)
        self.assertIn("- [ ] GATE-5: ask about rate limits", t)
        before = _gate_counts(self.text())       # after the expansion: the template's gates + GATE-5
        self.assertEqual(before[1], _gate_counts(t.replace("- [ ] GATE-5: ask about rate limits\n", ""))[1] + 1)

    def test_the_risk_set_in_the_stub_is_kept(self):
        """Row 3: a `## Risk` set in the stub (here `assertive`) is the expanded task's risk, not `unclassified`."""
        self.stub(lambda t: t.replace("## Risk\nunclassified", "## Risk\nassertive", 1))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(self.text(), r"(?m)^## Risk\nassertive\n\n> One word")   # over the template's `unclassified`


    def test_the_lines_under_the_risk_are_kept(self):
        """Row 6 (my sibling pass): what an agent wrote under the stub's `## Risk` beside the class — an irreversible
        task's rollback plan — is kept with it."""
        self.stub(lambda t: t.replace("## Risk\nunclassified", "## Risk\nirreversible\nROLLBACK-8: restore the dump", 1))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(self.text(), r"(?m)^## Risk\nirreversible\nROLLBACK-8: restore the dump$")

    def test_a_note_above_the_first_section_is_kept(self):
        """Row 7 (my sibling pass): a note an agent wrote between the stub's title and its first section is kept."""
        self.stub(lambda t: t.replace("<!-- stub:feature -->\n", "<!-- stub:feature -->\n\nPREAMBLE-NOTE-3: split this "
                                                                   "into two tasks if the API is late\n", 1))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PREAMBLE-NOTE-3: split this into two tasks if the API is late", self.text())


    def test_the_standing_gates_stay_the_last_gates(self):
        """Row 8 (my mutant run): with a standing gate configured, a section the stub added (and its gate) goes in
        before it — the standing gates stay the LAST gates (PB-TASK-STANDING-GATES)."""
        import json
        (self.project / ".agent" / "config.json").write_text(json.dumps(
            {"standing_gates": [{"title": "Changelog", "text": "Add the user-visible change to CHANGELOG.md"}]}),
            encoding="utf-8")
        self.stub(lambda t: t.rstrip("\n") + "\n\n## Notes\n- [ ] GATE-5: ask about rate limits\n")
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gates = [ln for ln in self.text().splitlines() if ln.startswith("- [ ]")]
        self.assertIn("- [ ] GATE-5: ask about rate limits", gates)
        self.assertTrue(gates[-1].startswith("- [ ] Add the user-visible change"), gates[-3:])


    def test_an_agents_quote_under_the_risk_stays(self):
        """Row 9 (my sibling pass, 296's lesson): only the stub template's own guidance line under `## Risk` is the
        template's; a `>` quote an agent wrote there stays with the class."""
        self.stub(lambda t: t.replace("## Risk\nunclassified", "## Risk\nassertive\n> QUOTE-4: he said the claim is the docs", 1))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.text()
        self.assertRegex(t, r"(?m)^## Risk\nassertive\n> QUOTE-4: he said the claim is the docs$")
        self.assertEqual(t.count("> One word: `reversible`"), 1)   # the template's guidance, not carried twice

    def test_a_note_under_the_status_stays(self):
        """Row 10 (my sibling pass): a note an agent wrote under the stub's `## Status`, beside its value, stays."""
        self.stub(lambda t: t.replace("## Status\npending\n", "## Status\npending\nSTATUS-NOTE-6: waits for the API\n", 1))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("STATUS-NOTE-6: waits for the API", self.text())

    def test_a_carried_note_is_not_parked_debt(self):
        """Row 11 (impl round 1: agy, opus, sonnet): a `- ` note carried from above the stub's first section or from
        under its `## Status` sits under a heading of its own, not inside the template's `## Parked` at the end of the
        file, so `tasks parked` does not list it; its blank line stays (grok)."""
        self.stub(lambda t: t.replace("<!-- stub:feature -->\n", "<!-- stub:feature -->\n- PRE-9: ask the vendor\n\n"
                                      "PRE-10: a second paragraph\n", 1)
                  .replace("## Status\npending\n", "## Status\npending\n- STAT-2: waits for the API\n", 1))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.text()
        self.assertIn("- PRE-9: ask the vendor\n\nPRE-10: a second paragraph", t)
        self.assertIn("- STAT-2: waits for the API", t)
        self.assertEqual(extract_parked_items(t), [])

    def test_the_references_are_copied_byte_for_byte(self):
        """Row 14 (impl round 1: agy, opus, codex, grok): a reference's backslashes are text, not a replacement
        template's escapes — `\\n` stays two characters and `\\d` does not stop `tasks work` with a traceback."""
        refs = r"- REF-12: split on `\n` alone; C:\Users\dev; \d+ and \1"
        self.stub(lambda t: t.replace("(optional)", "(optional)\n" + refs))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(refs, self.text())

    def test_a_risk_heading_with_closing_hashes_keeps_its_class(self):
        """Row 16 (impl round 1, codex): `## Risk ##` is the Risk heading for every reader; its class and the rollback
        under it are kept."""
        self.stub(lambda t: t.replace("## Risk\nunclassified", "## Risk ##\nirreversible\nROLLBACK-8: restore the dump", 1))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(extract_risk(self.tf), "irreversible")
        self.assertIn("ROLLBACK-8: restore the dump", self.text())

    def test_a_fenced_block_under_the_risk_is_kept_whole(self):
        """Row 17 (impl round 1, codex): a closed fence under `## Risk` that quotes `## Recovery` is kept whole, and
        the expanded file has no fence left open."""
        block = "```\n## Recovery\nrestore the dump\n```"
        self.stub(lambda t: t.replace("## Risk\nunclassified", "## Risk\nirreversible\n" + block, 1))
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.text()
        self.assertIn(block, t)
        self.assertFalse(_has_unclosed_fence(t.split("\n")))
        self.assertEqual(extract_risk(self.tf), "irreversible")

    def test_a_light_stubs_why_and_a_quick_stubs_references_are_kept(self):
        """Row 18 (impl round 1: codex, grok): a template without the section — light has no `## Why`, quick no
        `## References` — gets the stub's section, not nothing."""
        self.stub(lambda t: t.replace("## Why\n(fill in before expanding)", "## Why\nWHY-31: the vendor's deadline", 1),
                  kind="light")
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WHY-31: the vendor's deadline", self.text())
        self.setUp()
        self.stub(lambda t: t.replace("(optional)", "- REF-40: the runbook"), kind="quick")
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("- REF-40: the runbook", self.text())

    def test_a_custom_playbooks_task_keeps_the_stubs_risk(self):
        """Row 19 (impl round 1: agy, opus, sonnet, grok): a custom playbook without `## Risk` / `unclassified` keeps
        the stub's Risk, not nothing."""
        pb = self.project / ".agent" / "playbooks"
        pb.mkdir()
        (pb / "speval.md").write_text("# {{NNN}} - {{TITLE}}\n\n## Status\npending\n\n## Intent\n(custom)\n\n"
                                      "- [ ] ONLY_CUSTOM_GATE\n", encoding="utf-8")
        self.stub(lambda t: t.replace("## Risk\nunclassified", "## Risk\nassertive", 1), kind="speval")
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("- [ ] ONLY_CUSTOM_GATE", self.text())
        self.assertEqual(extract_risk(self.tf), "assertive")


class TheExpansionStaysSafe(_Stub):
    def test_an_unclosed_fence_in_the_added_text_refuses(self):
        """Row 4: an unclosed fence in what the stub added would cover the template's gates: the expansion refuses,
        says why, and writes nothing."""
        self.stub(lambda t: t.rstrip("\n") + "\n\n## Notes\n```\na fence never closed\n")
        before = self.text()
        r = self.expand()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("fence", (r.stdout + r.stderr).lower())
        self.assertEqual(self.text(), before)

    def test_an_untouched_stub_expands_with_nothing_added(self):
        """Row 5: a stub nobody edited expands to the template alone — no section the template lacks, no
        `(optional)`, the risk `unclassified`."""
        self.stub()
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.text()
        self.assertNotIn("(optional)", t)
        self.assertNotIn("## Notes", t)
        self.assertRegex(t, r"(?m)^## Risk\nunclassified$")
        self.assertEqual(t.count("## References"), 1)
        self.assertRegex(t, r"(?m)^## Intent\nbuild the importer\n\n## Why\n")       # over the template's placeholder

    def test_an_unclosed_fence_anywhere_in_the_stub_refuses(self):
        """Row 12 (impl round 1: agy, opus, grok, sonnet): a fence left open under `## Why` or `## References` would
        fence the template's gates and hide the sections after it (`## Notes` and its gate): the expansion refuses and
        writes nothing."""
        for heading, old in (("## Why", "(fill in before expanding)"), ("## References", "(optional)")):
            with self.subTest(heading=heading):
                self.setUp()                                  # a project of its own for each case
                self.stub(lambda t: t.replace(f"{heading}\n{old}", f"{heading}\n```\nsnippet", 1).rstrip("\n")
                          + "\n\n## Notes\n- [ ] GATE-5: ask about rate limits\n")
                before = self.text()
                r = self.expand()
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("fence", (r.stdout + r.stderr).lower())
                self.assertEqual(self.text(), before)

    def test_a_section_the_expanded_task_writes_is_one_section(self):
        """Row 13 (impl round 1: agy, opus, sonnet, grok): a stub section with the heading of one the template writes
        (`## Parked`) goes into the template's, so its item is a parked item and the heading is there once; a standing
        gate's heading (`## Changelog`) would skip that gate: the expansion refuses, names it and writes nothing."""
        import json
        self.stub(lambda t: t.rstrip("\n") + "\n\n## Parked\n- ITEM-3: later\n")
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.text()
        self.assertEqual(t.count("\n## Parked\n"), 1)
        self.assertEqual(extract_parked_items(t), ["ITEM-3: later"])
        self.setUp()
        (self.project / ".agent" / "config.json").write_text(json.dumps(
            {"standing_gates": [{"title": "Changelog", "text": "Add the user-visible change to CHANGELOG.md"}]}),
            encoding="utf-8")
        self.stub(lambda t: t.rstrip("\n") + "\n\n## Changelog\n- ITEM-3: later\n")
        before = self.text()
        r = self.expand()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("## Changelog", r.stdout + r.stderr)
        self.assertEqual(self.text(), before)

    def test_the_risk_reads_the_same_after_the_expansion(self):
        """Row 15 (impl round 1, codex's Critical): what the stub's `## Risk` reads (two Risk sections, a U+0085 before
        the class, a blank line above it: `unclassified`, the strict bar), the expanded task reads too — or the
        expansion refuses and writes nothing. It never lowers the class."""
        cases = {"two Risk sections": (lambda t: t.replace("## Risk\nunclassified", "## Risk\nreversible", 1).rstrip("\n")
                                       + "\n\n## Risk\nirreversible\n", False),
                 "U+0085": (lambda t: t.replace("## Risk\nunclassified", "## Risk\n\x85reversible", 1), True),
                 "a blank line first": (lambda t: t.replace("## Risk\nunclassified", "## Risk\n\nreversible", 1), True)}
        for name, (edit, expands) in cases.items():
            with self.subTest(name):
                self.setUp()
                self.stub(edit)
                before, read = self.text(), extract_risk(self.tf)
                self.assertEqual(read, "unclassified")
                r = self.expand()
                if expands:                            # nothing is wrong with it: it expands, read as the stub was
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertEqual(extract_risk(self.tf), read)
                else:                                  # one of the two would be lost
                    self.assertNotEqual(r.returncode, 0, r.stdout)
                    self.assertEqual(self.text(), before)

    def test_a_second_section_of_the_stubs_own_refuses(self):
        """Row 21 (my mutant run after impl round 1): a second `## Why` would be lost — only the first is carried —
        so the expansion refuses, names it and writes nothing."""
        self.stub(lambda t: t.rstrip("\n") + "\n\n## Why\nWHY-2: the second reason\n")
        before = self.text()
        r = self.expand()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("two `## Why` sections", r.stdout + r.stderr)
        self.assertEqual(self.text(), before)

    def test_a_fenced_risk_example_is_not_the_class(self):
        """Row 20 (impl round 1, sonnet): a `## Risk` quoted inside a closed fence in an added section is an example,
        not the stub's class — with no live `## Risk`, the task reads `unclassified`, as the stub does."""
        self.stub(lambda t: t.replace("## Risk\n", "## Risk notes\n", 1).rstrip("\n")
                  + "\n\n## Notes\n```\n## Risk\nreversible\n```\n")
        self.assertEqual(extract_risk(self.tf), "unclassified")
        r = self.expand()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.text()
        self.assertEqual(extract_risk(self.tf), "unclassified")
        self.assertIn("```\n## Risk\nreversible\n```", t)
        self.assertFalse(_has_unclosed_fence(t.split("\n")))       # the template's gates stay outside any fence


if __name__ == "__main__":
    unittest.main()
