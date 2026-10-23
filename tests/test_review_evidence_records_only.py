#!/usr/bin/env python3
"""Review evidence comes only from the records the CLI writes, never from a tick (task 290; audit 1, G2).

His blind check (2026-10-22): a checked task.md gate that mentions `panel-review` and `impl` counted as implementation
review evidence, and six templates carry such a gate ("Run the impl panel … `tasks panel-review <N> --mode impl`"). So a
task whose `## Risk` was left unset — held to the high-consequence bar — closed with no judge at all once its boxes
were ticked, and so did a high-risk task with `panel_required_for: []`. Each test names the row of task 290's contract.

Real temporary projects from part 1's `Project` (tests/test_red_receipt.py), the CLI driven as `python3 -m tasks.cli`.
Run: python3 tests/test_review_evidence_records_only.py
"""
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))
from tests.test_red_receipt import Project  # noqa: E402
from tasks.core import has_review_evidence  # noqa: E402

TEMPLATES = ("bugfix", "build", "cleanup", "feature", "ops", "refactor")


class Closing(Project):
    def task(self, kind, risk="unclassified"):
        r = self.cli("new", kind, "probe", "probe the close")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.task_dir = next((self.d / ".agent" / "tasks").glob("001-*"))
        self.task_md = self.task_dir / "task.md"
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.task_md.read_text(encoding="utf-8")
        t = re.sub(r"(?m)^## Risk\n[^\n]*\n", f"## Risk\n{risk}\n", t, count=1)
        if "## Test contract" in t:                     # nothing to test: the contract's own exit (task 286)
            t = re.sub(r"(?ms)^## Test contract\n.*?(?=^## )",
                       "## Test contract\n<!-- pin -->\n\nNo behavior changes: the probe changes nothing.\n\n", t)
        self.assertIn("Run the impl panel", t, kind)    # the gate this task is about
        t = "\n".join((ln.replace("- [ ] ", "- [x] ", 1) + "\n  - done") if ln.lstrip().startswith("- [ ] ") else ln
                      for ln in t.split("\n"))
        self.task_md.write_text(t, encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "the task, every gate ticked")

    def close(self):
        r = self.cli("work", "done", timeout=180)
        return r, r.stdout + r.stderr


class NoJudgeNoClose(Closing):
    def test_an_unset_risk_does_not_close_on_ticked_boxes(self):
        # row 1 (the bug): each template that carries the impl-panel gate
        for kind in TEMPLATES:
            with self.subTest(kind=kind):
                self.setUp()
                self.task(kind)
                r, out = self.close()
                self.assertEqual(r.returncode, 1, out)
                self.assertIn("unclassified", out)
                self.assertNotIn("Task 001 done.", out)

    def test_a_high_risk_task_without_the_panel_bar_needs_a_record(self):
        # row 2: `panel_required_for: []` (a documented setting) leaves the review requirement to the evidence check
        for risk in ("irreversible", "assertive"):
            with self.subTest(risk=risk):
                self.setUp()
                self.config(panel_required_for=[])
                self.task("feature", risk=risk)
                r, out = self.close()
                self.assertEqual(r.returncode, 1, out)
                self.assertNotIn("Task 001 done.", out)


class AReversibleTaskStillCloses(Closing):
    def test_a_reversible_task_closes_with_no_review(self):
        # row 7 (must-not): the bar is for the high-consequence classes; a reversible task closes on verify, as before
        self.task("feature", risk="reversible")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("Task 001 done.", out)


class TheDocsSayIt(unittest.TestCase):
    def test_the_docs_and_the_ledger_name_the_records_only(self):
        # row 8: docs/configuration.md and guarantee PB-RISK-REVIEW-REQUIRED say a tick is never evidence
        root = _HERE.parent
        doc = (root / "docs" / "configuration.md").read_text(encoding="utf-8")
        self.assertIn("a ticked gate is never review evidence", doc)
        import json
        ledger = json.loads((root / "docs" / "guarantee-ledger.json").read_text(encoding="utf-8"))
        g = next(g for g in ledger["guarantees"] if g["id"] == "PB-RISK-REVIEW-REQUIRED")
        self.assertNotIn("checked task.md line naming impl-review", g["statement"])
        self.assertIn("a ticked gate is never evidence", g["statement"])
        self.assertTrue(any(p["reference"] == "NoJudgeNoClose.test_an_unset_risk_does_not_close_on_ticked_boxes"
                            for p in g["proofs"]))


class TheRecordsAreTheEvidence(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb290-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.tf = self.d / "task.md"
        self.tf.write_text("# 001 - X\n\n## Status\nin_progress\n\n## Implementation Review\n"
                           "- [x] Run the impl panel in the background — `.claude/bin/tasks panel-review <N> --mode impl` — then read `judge.md`.\n"
                           "- [x] Run `.claude/bin/tasks impl-review 1`\n"
                           "- [x] Run `.claude/bin/tasks plan-review 1`\n"
                           "- [x] Run `.claude/bin/tasks panel-review 1 --mode impl`\n", encoding="utf-8")

    def test_no_tick_is_evidence(self):
        # row 3 (hostile): every review-like line ticked, no record
        self.assertFalse(has_review_evidence(self.tf, impl_only=True))
        self.assertFalse(has_review_evidence(self.tf))

    def test_an_impl_pass_round_is_evidence(self):
        # row 4 (must-not): judge.md's newest round an impl PASS
        (self.d / "judge.md").write_text("# Panel Impl Review — task.md\n**Round-format:** 2\n\n"
                                         "**PANEL VERDICT: PASS** — 3/3 judges succeeded, quorum 2\n\n**Round-id:** 1234567890ab\n",
                                         encoding="utf-8")
        self.assertTrue(has_review_evidence(self.tf, impl_only=True))

    def test_a_single_judge_impl_record_is_evidence(self):
        # row 5 (must-not): judge-single.md's CLI heading
        (self.d / "judge-single.md").write_text("## Single-judge impl review — 2026-10-23 · codex\n\nok\n", encoding="utf-8")
        self.assertTrue(has_review_evidence(self.tf, impl_only=True))

    def test_a_plan_round_is_no_impl_evidence(self):
        # row 6 (hostile): a plan-only judge.md beside ticked impl gates
        (self.d / "judge.md").write_text("# Panel Plan Review — task.md\n**Round-format:** 2\n\n"
                                         "**PANEL VERDICT: PASS** — 3/3 judges succeeded, quorum 2\n\n**Round-id:** 1234567890ab\n",
                                         encoding="utf-8")
        self.assertFalse(has_review_evidence(self.tf, impl_only=True))


if __name__ == "__main__":
    unittest.main()
