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
from tasks.core import _gate_counts  # noqa: E402


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

    def stub(self, edit=None):
        r = self.tasks("new", "--stub", "feature", "importer", "build the importer")
        self.assertEqual(r.returncode, 0, r.stderr)
        (self.tf,) = (self.project / ".agent" / "tasks").glob("*/task.md")
        if edit:
            self.tf.write_text(edit(self.tf.read_text(encoding="utf-8")), encoding="utf-8")
        return self.tf

    def expand(self):
        r = self.tasks("work", "001")
        return r

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
        self.assertRegex(self.text(), r"(?m)^## Risk\nassertive$")


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


if __name__ == "__main__":
    unittest.main()
