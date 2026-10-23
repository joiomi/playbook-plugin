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


class _Deleting(Closing):
    def _without_the_heading(self):
        self.task("feature", risk="assertive")
        t = self.task_md.read_text(encoding="utf-8")
        t = re.sub(r"(?ms)^## Risk\n.*?(?=^## )", "", t, count=1)
        self.assertNotIn("## Risk\n", t)
        self.task_md.write_text(t, encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "the Risk heading deleted")


class AHeadingDeleted(_Deleting):
    def test_a_task_without_the_heading_does_not_close_unreviewed(self):
        # row 1 (the bug): every gate ticked with a note, no judge record, the `## Risk` heading deleted
        self._without_the_heading()
        r, out = self.close()
        self.assertEqual(r.returncode, 1, out)
        self.assertNotIn("Task 001 done.", out)
        self.assertIn("left unclassified", out)                    # the block's own words (round 1, sonnet)
        self.assertIn("the heading", out)                          # and how to add a missing field (round 1, opus)

    def test_it_closes_once_classified_or_forced(self):
        # row 3 (must-not): the ways out of an unset Risk are its ways out — a class, or `--force --reason`
        self._without_the_heading()
        r = self.cli("work", "done", "--force", "--reason", "an import with no Risk", timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unclassified", r.stdout + r.stderr)        # the close still says what was skipped


class RoundOne(Closing):
    """Task 294's impl round 1 (opus Important ×2)."""

    def test_a_freehand_task_offers_the_field(self):
        # row 5: `tasks freehand` with no active task writes a record — with its `## Risk` field, as every creator does
        from tasks.core import extract_risk, has_risk_section
        r = self.cli("freehand")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tm = next((self.d / ".agent" / "tasks").glob("*-freehand/task.md"))
        self.assertTrue(has_risk_section(tm), tm.read_text(encoding="utf-8"))
        self.assertEqual(extract_risk(tm), "unclassified")

    def test_an_unset_risk_is_held_to_the_strictest_class_bar(self):
        # row 6: with the panel required for a high-consequence class, an unclassified task needs the panel too — a
        # single-judge record would let "not classifying" lower an assertive task's bar
        for policy, closes in ((["assertive", "irreversible"], False), ([], True)):
            with self.subTest(policy=policy):
                self.setUp()
                self.config(panel_required_for=policy)
                self.task("feature")
                (self.task_dir / "judge-single.md").write_text(
                    "## Single-judge impl review — 2026-10-23 10:00 UTC (`codex:sol`)\n\nRecorded here by the CLI.\n",
                    encoding="utf-8")
                self.g("add", "-A")
                self.g("commit", "-qm", "a single-judge record")
                r, out = self.close()
                self.assertEqual(r.returncode == 0, closes, out)


class RoundTwo(_Deleting):
    """Task 294's impl round 2 (codex, opus, sonnet, agy Important): the other ways not classifying lowered the bar."""

    def _record(self):
        (self.task_dir / "judge-single.md").write_text(
            "## Single-judge impl review — 2026-10-23 11:00 UTC (`codex:sol`)\n\nRecorded here by the CLI.\n",
            encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "a single-judge record")

    def test_the_seeded_policy_still_names_the_field(self):
        # row 7 (agy, codex, grok): under the seeded default the panel's block also names the missing field
        self.config(panel_required_for=["assertive", "irreversible"])
        self._without_the_heading()
        r, out = self.close()
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("the heading", out)

    def test_a_panel_listed_for_any_class_holds_an_unset_risk(self):
        # row 8 (codex, opus): `panel_required_for: ["reversible"]` — an unclassified task needs the panel too
        self.config(panel_required_for=["reversible"])
        self._without_the_heading()
        self._record()
        r, out = self.close()
        self.assertEqual(r.returncode, 1, out)

    _EVERY_CLASS = {"_always": ["true"], "reversible": ["test reversible = extra"],
                    "assertive": ["test assertive = extra"], "irreversible": ["test irreversible = extra"]}

    def test_an_unset_risk_runs_every_class_verify(self):
        # row 9 (opus, sonnet; post-panel run 1, codex Minor): every class's `verify.<class>` extras run for an
        # unclassified close — each fails here, and the close prints each one
        self.config(verify=self._EVERY_CLASS)
        self._without_the_heading()
        self._record()
        r, out = self.close()
        self.assertEqual(r.returncode, 1, out)
        for cls in ("reversible", "assertive", "irreversible"):
            self.assertIn(f"[FAIL exit 1] test {cls} = extra", out)

    def test_a_classified_reversible_runs_its_own_verify_only(self):
        # row 11, row 9's must-not: a reversible task runs `_always` and its own `verify.reversible`, not the others
        self.config(verify=dict(self._EVERY_CLASS, reversible=["true reversible-extra"]))
        self.task("feature", risk="reversible")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("[PASS] true reversible-extra", out)
        self.assertNotIn("test assertive = extra", out)
        self.assertNotIn("test irreversible = extra", out)


class ClassifyingIsTheWayOut(_Deleting):
    def test_restoring_the_field_closes_it(self):
        # row 3 widened (agy Minor): the heading put back with a class closes the task without --force
        self._without_the_heading()
        t = self.task_md.read_text(encoding="utf-8").replace("## Intent", "## Risk\nreversible\n\n## Intent", 1)
        self.task_md.write_text(t, encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "the field restored")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)


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

    def test_the_freshness_passages_hold_no_escape(self):
        # row 10 (sonnet Important): the freshness passages no longer say an unset risk escapes the seeded panel
        doc = (_HERE.parent / "docs" / "configuration.md").read_text(encoding="utf-8")
        self.assertNotIn("`unclassified` is caught only where policy also requires a panel", doc)
        self.assertNotIn("`unclassified` does not require a panel", doc)

    def test_the_seeded_policy_texts_name_an_unset_risk(self):
        # row 13 (grok Important): the texts that describe the seeded `panel_required_for` — the `_doc` init writes into
        # every fresh config.json among them — no longer say only assertive and irreversible work needs the panel
        root = _HERE.parent
        texts = {
            "plugins/playbook/scripts/init": "requires it only for changes to claims-about-the-world",
            "docs/cli.md": "only claims/data/publish require one",
            "plugins/playbook/commands/init.md": "only claims/data/publish need a panel",
            "plugins/playbook/tasks/template.py": "only\n                      those close-gate on a PASS panel",
        }
        for rel, old in texts.items():
            with self.subTest(file=rel):
                s = re.sub(r"\s+", " ", (root / rel).read_text(encoding="utf-8"))
                self.assertFalse(re.sub(r"\s+", " ", old) in s, f"{rel} still says: {old!r}")
                self.assertTrue("unclassified task is held to the strictest class listed" in s, rel)


if __name__ == "__main__":
    unittest.main()
