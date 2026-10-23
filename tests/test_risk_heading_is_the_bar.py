#!/usr/bin/env python3
"""A task with no `## Risk` heading is held to the same review bar as one whose Risk is unset (task 294).

Found during task 290 (2026-10-23, reproduced in a temporary project): deleting the `## Risk` heading took the path kept
for pre-1.5.0 tasks — the close only warned — so an agent could drop one heading and close high-consequence work with no
review. A fresh project has no pre-1.5.0 task (the owner's rule of 2026-10-16, design for new projects), so the two facts
are one: the risk was not classified. Each test names the row of task 294's contract.

Run: python3 -m unittest tests.test_risk_heading_is_the_bar
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))
from tests.test_review_evidence_records_only import Closing  # noqa: E402
from tasks.core import close_decision  # noqa: E402


class AHeadingDeleted(Closing):
    def _without_the_heading(self):
        self.task("feature", risk="assertive")
        t = self.task_md.read_text(encoding="utf-8")
        t = re.sub(r"(?ms)^## Risk\n.*?(?=^## )", "", t, count=1)
        self.assertNotIn("## Risk\n", t)
        self.task_md.write_text(t, encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "the Risk heading deleted")

    def test_a_task_without_the_heading_does_not_close_unreviewed(self):
        # row 1 (the bug): every gate ticked with a note, no judge record, the `## Risk` heading deleted
        self._without_the_heading()
        r, out = self.close()
        self.assertEqual(r.returncode, 1, out)
        self.assertNotIn("Task 001 done.", out)
        self.assertIn("unclassified", out)

    def test_it_closes_once_classified_or_forced(self):
        # row 3 (must-not): the ways out of an unset Risk are its ways out — a class, or `--force --reason`
        self._without_the_heading()
        r = self.cli("work", "done", "--force", "--reason", "an import with no Risk", timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unclassified", r.stdout + r.stderr)        # the close still says what was skipped


class ThePolicy(unittest.TestCase):
    def test_unclassified_needs_evidence_whatever_the_file_holds(self):
        # row 2: `close_decision` has one answer for an unclassified risk — the high-consequence bar
        allowed, why = close_decision(risk="unclassified", verify_declared=True, verify_failed=False,
                                      has_review_evidence=False, force=False, reason=None)
        self.assertFalse(allowed)
        self.assertIn("unclassified", why)
        allowed, _ = close_decision(risk="unclassified", verify_declared=True, verify_failed=False,
                                    has_review_evidence=True, force=False, reason=None)
        self.assertTrue(allowed)


class TheDocsSayIt(unittest.TestCase):
    def test_the_docs_and_the_ledger_hold_no_legacy_path(self):
        # row 4: docs/configuration.md and guarantee PB-RISK-CLASSIFY
        root = _HERE.parent
        doc = (root / "docs" / "configuration.md").read_text(encoding="utf-8")
        self.assertNotIn("a pre-1.5.0 task that was never offered", doc)
        self.assertIn("no `## Risk` heading at all is held to the same bar", doc)
        ledger = json.loads((root / "docs" / "guarantee-ledger.json").read_text(encoding="utf-8"))
        g = next(g for g in ledger["guarantees"] if g["id"] == "PB-RISK-CLASSIFY")
        self.assertNotIn("keeps the warning-only path", g["statement"])
        self.assertIn("a task with no Risk heading is held to the same bar", g["statement"])


if __name__ == "__main__":
    unittest.main()
