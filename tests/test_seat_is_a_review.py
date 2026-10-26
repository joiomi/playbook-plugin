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
            "BOM refusal": "\ufeffI'm sorry, I can't help with that.",
            # impl round 1 (opus, codex, agy): a plan-only stop that echoes the prompt's severities
            "plan echoing severities": ("I'll review the diff and report each finding starting with **Critical** or "
                                        "**Important**."),
            "codex's echo": "I will look for **Critical**, **Important**, and **Minor** issues before reporting findings.",
            # impl round 1 (codex): a typographic apostrophe
            "curly wait": "I\u2019m waiting for the command to finish.\n" + CAP0,
            "curly launched": "I\u2019ve launched the suite in the background.\nWaiting for it.\n" + CAP0,
            # impl round 2 (codex reproduced the first and the third through the CLI; opus, sonnet): a non-review that
            # still writes the CAP line, as agy wrote it under its waits
            "refusal + CAP": "I'm sorry, I can't help with reviewing this.\n" + CAP0,
            "plan + CAP": "I'll read the diff first, then run the tests.\n" + CAP0,
            "lens line, then a wait": ("The change touches no shared or persisted state.\nI have launched `python3 "
                                       "scripts/verify` in the background.\nWaiting for it to finish.\n" + CAP0),
            "the lens line alone": "The change touches no shared or persisted state.\n" + CAP0,
            "a Romanian plan + CAP": "Voi citi implementarea \u0219i voi urm\u0103ri fluxul de date.\n" + CAP0,
            "waiting on": "Waiting on the suite to finish.\n" + CAP0,
            # post-panel run 1 (codex, each reproduced through the CLI): a provisional "none", a bold word that only
            # starts with a severity, a plan that names a file
            "a wait that says no findings yet": "I'm waiting for the command to finish; no findings yet.\n" + CAP0,
            "a wait, nothing found so far": "Waiting for the suite. Nothing found so far.\n" + CAP0,
            "Important context, then a plan": "**Important context**\nI will start by reading the diff and tests.",
            "a plan naming a file + CAP": "I'll read `tasks/review.py` first, then run the tests.\n" + CAP0,
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
            "bold CAP": "No findings in the delta.\n\n**CAP: 0/5 reported, exhausted**",
            "backticked CAP": "No findings in the delta.\n\n`CAP: 0/5 reported, exhausted`",
            "CAP with a period": "Nothing found.\n" + CAP0 + ".",
            "no CAP, bold finding": "- **N3 — row 15.**\n\n**Important** `red.py:12` the cleanup misses SIGKILL.",
            "findings that wait": ("Waiting for the suite took long, but:\n**Important** `a.py:3` x\n"
                                   "CAP: 1/5 reported, exhausted"),
            "trailing blank lines": "No findings.\n" + CAP0 + "\n\n\n",
            "CRLF": "No findings.\r\n\r\n" + CAP0 + "\r\n",
            "BOM": "\ufeffNo findings.\n" + CAP0,
            "BOM on the CAP line": "\ufeff" + CAP0,
            "Romanian": "Nu am găsit probleme Critical sau Important. Au trecut 73 de teste.\n\n" + CAP0,
            "200,000 characters": "No findings. " + ("x" * 200_000) + "\n" + CAP0,
            # impl round 1 (agy, sonnet): a rule or a closing fence after the CAP line
            "CAP then a rule": "No findings.\n" + CAP0 + "\n---",
            "CAP then a fence": "No findings in `a.py`.\n" + CAP0 + "\n```",
            # impl round 1 (agy, opus, sonnet, codex): a review that mentions waiting is still a review
            "a CAP-0 review naming a wait mid-text": ("No findings. The lock is bounded.\n- Waiting for the lock is "
                                                      "capped by `timeout=5`.\n" + CAP0),
            # impl round 2: a CAP-0 answer with prose says it found nothing, or points at what it read
            "CAP 0 citing a line": "The retry at `tasks/review.py:2296` stays bounded.\n" + CAP0,
            "CAP 0 citing a line in a link": "Checked [models_check.py#L180](models_check.py#L180).\n" + CAP0,
            "CAP 0, severities in backticks": "No `**Critical**` or `**Important**` defects found across the lenses.\n" + CAP0,
            "CAP 0, I found no": "I found no Critical or Important problem in the delta.\n" + CAP0,
            "CAP 0, Romanian": "Nu am identificat probleme în diferența revizuită.\n" + CAP0,
            "CAP 0, nothing to report": "Nothing to report: the change is a version bump.\n" + CAP0,
            "CAP 0, two qualifiers": "No new actionable findings. The rollback recipe holds.\n" + CAP0,   # 275's codex
            # impl round 2 (agy, opus, sonnet): a listed CAP line; the stop rule's forms of a finding
            "a listed CAP line": "No findings.\n- CAP: 0/5 reported, exhausted",
            "a numbered CAP line": "No findings.\n1. CAP: 0/5 reported, exhausted",
            "Critical with a colon, no CAP": "**Critical:** `a.py:1` the guard fails open.",
            "bracketed, no CAP": "**[Important]** `a.py:1` x.",
            "lower case, no CAP": "- **important** `a.py:1` x.",
            "a Minor-only review that says waiting": "**Minor** `a.py:3` x.\nWaiting for the suite took long.\n" + CAP0,
            "a numbered finding, no CAP": "1. **Important** — `a.py:1` the check misses x.",
            "a listed finding, no CAP": "- **Minor** `b.py:2` a wording.",
        }
        for name, text in reviews.items():
            with self.subTest(name):
                self.assertIsNone(not_a_review(text), f"{name}: a review read as none")

    def test_a_non_review_is_never_read_as_the_provider_speaking(self):
        """Impl round 1 (opus, codex — codex reproduced it): the failure head carried the judge's own last line, and
        `classify_outage` trusts a failed block's first line, so quoted limit words recorded a healthy seat as out of
        credit and the next panels skipped it. A non-review's head holds none of the judge's text, and neither the
        outage nor the dead-model classifier reads a non-review as the provider speaking."""
        from tasks import models_check, review, seat_outage
        for provider, text in (("claude", "I'll inspect You've hit your usage limit; try again at 11:59 PM."),
                               ("claude", "You've hit your usage limit · resets 7:33pm"),
                               ("codex", "usage balance exhausted, try again at 11:59 PM"),
                               ("claude", "There's an issue with the selected model (claude-x). It may not exist or "
                                          "you may not have access to it.")):
            with self.subTest(text[:30]):
                marked = review._as_review_or_failure(text)
                self.assertTrue(judge_failed(marked))
                self.assertNotIn(text[:20], marked.splitlines()[0], "the judge's words are in the failure head")
                self.assertIsNone(seat_outage.classify_outage(marked, provider=provider))
                self.assertEqual(models_check.classify_failure(marked), models_check.OTHER)
        self.assertEqual(seat_outage._NOT_A_REVIEW_HEAD, models_check.NOT_A_REVIEW_HEAD)   # the one head, twice
        # impl round 2 (codex, reproduced through the CLI): the single judge's failure path formats the marked text
        # under `(FAILED — exit 1)`, so the head is no longer first — the dead-model classifier still reads none of it
        import subprocess
        from provider.sandbox import format_judge_output
        quoted = "invalid_request_error: this model is not supported for this account"
        formatted = format_judge_output(subprocess.CompletedProcess(
            ["codex"], 1, stdout=f"{models_check.NOT_A_REVIEW_HEAD} the answer is empty)\n\n{quoted}\n{quoted}\n",
            stderr=""))
        self.assertTrue(judge_failed(formatted))
        self.assertEqual(models_check.classify_failure(formatted), models_check.OTHER)
        self.assertIsNone(seat_outage.classify_outage(formatted, provider="codex"))

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
