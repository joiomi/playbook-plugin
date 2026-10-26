#!/usr/bin/env python3
"""A judge's exit-0 text counts as a review only when it is one (task 320, audit 5's G6).

`models_check.judge_failed` knows the failure markers only, so a panel counted a refusal, a
filter block, a plan-only stop or a wait as a judge that "succeeded". `not_a_review` gives the
reason such a text is no review; the shapes below are the ones real judges returned in this
repository and in WoW (task 319's sweeps, task 320's `rule_probe.out`). Run:
    python3 -m unittest tests.test_seat_is_a_review
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "playbook"))

from tasks.models_check import judge_failed, not_a_review  # noqa: E402

CAP0 = "CAP: 0/5 reported, exhausted"


class NotAReview(unittest.TestCase):
    def test_each_measured_shape_is_not_a_review(self):
        shapes = {
            "refusal": "I'm sorry, I can't help with reviewing this.",
            "gemini filter": ("This request was blocked by Gemini's filters. They can occasionally trigger by mistake "
                              "on safe coding, security, or biology-related queries. Please try rephrasing your prompt."),
            "grok plan-only": ("I'll review the completed S2 reagent-rows work against the task intent and the code "
                               "that landed, starting with the full task trace and the changed sources."),
            "claude note": "I have enough grounded findings now.",
            "claude rule": "---",
            "claude writing": "I have enough grounded findings now. Writing up the review.",
            "agy launched": ("I have launched `python3 scripts/verify` in the background to verify the suite and will "
                             "continue once it finishes.\nWaiting for `scripts/verify` to complete in the background.\n"
                             + CAP0),
            "agy please wait": ("Please wait while `pnpm verify` completes execution. I will evaluate the results once "
                                "finished.\nThe change touches no shared or persisted state.\n" + CAP0),
            "agy i'm waiting": ("I have launched `python3 tests/test_guarantee_ledger.py` and am waiting for the test "
                                "output.\nI'm waiting for the unit tests to complete.\n" + CAP0),
            "agy wait for": "Wait for task-24 completion.\nThe change touches no shared or persisted state.\n\n" + CAP0,
            "garbage": "lol",
            "empty-ish": "   \n\n",
            "CAP mid-text": "CAP: 0/5 reported, exhausted\nI'll start by reading the task file.",
            "CRLF note": "I have enough grounded evidence now to write findings.\r\n",
            "BOM refusal": "﻿I'm sorry, I can't help with that.",
        }
        for name, text in shapes.items():
            with self.subTest(name):
                self.assertFalse(judge_failed(text), "the old predicate lets it pass — the defect")
                why = not_a_review(text)
                self.assertIsInstance(why, str, f"{name}: counted as a review")
                self.assertTrue(why.strip())

    def test_a_review_is_a_review(self):
        reviews = {
            "finding + CAP": "**Important** — `src/x.py:1` a finding.\n\nCAP: 1/5 reported, exhausted",
            "bare CAP 0": CAP0,
            "no findings + CAP": "No Critical or Important findings.\n\n" + CAP0,
            "more remain": "**Important** `a.py:1` x\n**Important** `b.py:2` y\nCAP: 5/5 reported, more remain",
            "CAP with words after": "Checked.\nCAP: 0/5 reported, exhausted — no further Critical/Important findings",
            "bold CAP": "Checked the delta.\n\n**CAP: 0/5 reported, exhausted**",
            "backticked CAP": "Checked the delta.\n\n`CAP: 0/5 reported, exhausted`",
            "CAP with a period": "Nothing found.\n" + CAP0 + ".",
            "no CAP, bold finding": "- **N3 — row 15.**\n\n**Important** `red.py:12` the cleanup misses SIGKILL.",
            "findings that wait": ("Waiting for the suite took long, but:\n**Important** `a.py:3` x\n"
                                   "CAP: 1/5 reported, exhausted"),
            "trailing blank lines": "No findings.\n" + CAP0 + "\n\n\n",
            "CRLF": "No findings.\r\n\r\n" + CAP0 + "\r\n",
            "BOM": "﻿No findings.\n" + CAP0,
            "BOM on the CAP line": "﻿" + CAP0,
            "Romanian": "Nu am găsit probleme Critical sau Important. Au trecut 73 de teste.\n\n" + CAP0,
            "200,000 characters": ("x" * 200_000) + "\n" + CAP0,
        }
        for name, text in reviews.items():
            with self.subTest(name):
                self.assertIsNone(not_a_review(text), f"{name}: a review read as none")

    def test_a_failure_is_left_to_judge_failed(self):
        failures = [
            "(FAILED — exit 1)\nstdout: API Error",
            "(timed out after hard 1200s)\n\n**INCOMPLETE** — killed mid-response: I'll review",
            "(error: the sandbox could not start)",
            "(no output)",
            "Error: Exceeded USD budget (0.05)",
        ]
        for text in failures:
            with self.subTest(text[:20]):
                self.assertTrue(judge_failed(text))
                self.assertIsNone(not_a_review(text), "a failure gets no second reason")


if __name__ == "__main__":
    unittest.main()
