#!/usr/bin/env python3
"""The review stop rule as the plugin's default (PHASE-B.md item 1, task 209): its pure half — the caps, the round
and Critical counts, the guard list, and the two decisions (`tasks/stop_rule.py`). The CLI halves (the panel cap,
the close) are tested in tests/test_stop_rule_cli.py.

Run: python3 -m unittest tests.test_stop_rule
"""
from __future__ import annotations

import io
import contextlib
import shutil
import tempfile
import unittest
from pathlib import Path

import sys

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))

from tasks import stop_rule as SR  # noqa: E402

BAR = "═" * 60


def rnd(mode="Impl", verdict="PASS", judge_lines=(), v2=True, rid=""):
    """One panel round as review.py writes it: the header, a bar, the judges' lines (quoted in format 2)."""
    head = f"# Panel {mode} Review — .agent/tasks/001-t/task.md\n" + ("**Round-format:** 2\n" if v2 else "")
    head += f"\n**PANEL VERDICT: {verdict}** — 2/2 judges succeeded, quorum 2\n\n"
    if rid:
        head += f"**Round-id:** {rid}\n\n"
    body = "\n".join(("> " + ln if v2 else ln) for ln in judge_lines)
    return head + BAR + "\n  JUDGE: claude:opus\n" + BAR + "\n\n" + body + "\n\n"


def judge_md(*rounds_newest_first):
    return "".join(rounds_newest_first)


class Caps(unittest.TestCase):
    def _quiet(self, fn):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = fn()
        return out, err.getvalue()

    def test_the_defaults_are_the_owners_D6(self):
        self.assertEqual(SR.resolve_rounds({}), {"reversible": 1, "assertive": 2, "irreversible": 2})
        self.assertEqual(SR.resolve_rounds(None), {"reversible": 1, "assertive": 2, "irreversible": 2})

    def test_a_project_changes_a_cap(self):
        self.assertEqual(SR.resolve_rounds({"review_rounds": {"assertive": 3}})["assertive"], 3)
        self.assertEqual(SR.resolve_rounds({"review_rounds": {"assertive": 3}})["irreversible"], 2)

    def test_a_value_that_is_not_a_positive_integer_keeps_the_default_and_says_so(self):
        for bad in (0, -1, "2", 2.0, True, None.__class__):
            caps, err = self._quiet(lambda: SR.resolve_rounds({"review_rounds": {"assertive": bad}}))
            self.assertEqual(caps["assertive"], 2, bad)
            self.assertIn("review_rounds.assertive", err, bad)
        caps, err = self._quiet(lambda: SR.resolve_rounds({"review_rounds": [1, 2]}))
        self.assertEqual(caps, SR.DEFAULT_ROUNDS)
        self.assertIn("not an object", err)

    def test_an_unset_risk_takes_the_higher_high_cap(self):
        caps = {"reversible": 1, "assertive": 2, "irreversible": 4}
        for risk in ("unclassified", "", "bogus"):
            self.assertEqual(SR.cap_for(risk, caps), 4, risk)
        self.assertEqual(SR.cap_for("reversible", caps), 1)


class Rounds(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb209-"))
        self.addCleanup(shutil.rmtree, self.d, True)

    def _write(self, name, text):
        (self.d / name).write_text(text, encoding="utf-8")

    def test_only_impl_rounds_that_met_their_quorum_count(self):
        self._write("judge.md", judge_md(rnd(verdict="FAIL"), rnd("Plan"), rnd(), rnd("Plan")))
        self.assertEqual(len(SR.impl_rounds(self.d)), 1)

    def test_archived_rounds_count_and_come_first(self):
        self._write("judge-archive.md", judge_md(rnd(rid="bbbbbbbbbbbb"), rnd(rid="aaaaaaaaaaaa")))
        self._write("judge.md", judge_md(rnd(rid="cccccccccccc")))
        self.assertEqual([r["round_id"] for r in SR.impl_rounds(self.d)],
                         ["aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"])

    def test_a_legacy_round_in_both_files_is_one_round_whatever_its_trailing_spaces(self):
        body = rnd(judge_lines=["**Important** x"])           # no Round-id: a round from before task 108
        self._write("judge-archive.md", body.replace("\n", "  \n"))
        self._write("judge.md", judge_md(rnd(rid="bbbbbbbbbbbb"), body))
        self.assertEqual(len(SR.impl_rounds(self.d)), 2)

    def test_a_round_in_both_files_is_one_round(self):
        self._write("judge-archive.md", judge_md(rnd(rid="aaaaaaaaaaaa")))
        self._write("judge.md", judge_md(rnd(rid="bbbbbbbbbbbb"), rnd(rid="aaaaaaaaaaaa")))
        self.assertEqual(len(SR.impl_rounds(self.d)), 2)

    def test_no_record_is_no_round(self):
        self.assertEqual(SR.impl_rounds(self.d), [])

    def test_a_record_that_cannot_be_read_raises(self):
        (self.d / "judge-archive.md").mkdir()
        with self.assertRaises(SR.RoundsUnreadable):
            SR.impl_rounds(self.d)


class Criticals(unittest.TestCase):
    def _one(self, text):
        from tasks.core import parse_judge_rounds
        rs = parse_judge_rounds(text)
        self.assertEqual(len(rs), 1)
        return SR.round_findings(rs[0])["critical"]   # task 344, P1

    def test_a_quoted_critical_counts_in_a_format_2_round(self):
        self.assertEqual(self._one(rnd(judge_lines=["**Critical** — `a.py:1` x.", "**Important** y"])), 1)

    def test_an_unquoted_line_in_a_format_2_round_is_the_writers(self):
        text = rnd(judge_lines=["**Important** y"]) + "**Critical** — the agent's own triage note\n"
        self.assertEqual(self._one(text), 0)

    def test_a_format_1_round_counts_its_unquoted_lines(self):
        self.assertEqual(self._one(rnd(v2=False, judge_lines=["1. **Critical** [PRE-EXISTING] a.py:1 x"])), 1)

    def test_a_pre_existing_critical_counts_in_a_panel(self):
        self.assertEqual(self._one(rnd(judge_lines=["**Critical** [PRE-EXISTING] a.py:1 x"])), 1)

    def test_a_settled_critical_is_not_counted(self):
        self.assertEqual(self._one(rnd(judge_lines=["**Critical** [SETTLED] a.py:1 x"])), 0)
        self.assertEqual(self._one(rnd(judge_lines=["**Critical** [SETTLED-CONTRADICTED] a.py:1 x"])), 1)

    def test_a_critical_in_the_header_is_not_a_judges(self):
        text = rnd(judge_lines=["fine"]).replace("**PANEL VERDICT", "**Critical** x\n\n**PANEL VERDICT", 1)
        self.assertEqual(self._one(text), 0)

    def test_words_are_not_findings(self):
        self.assertEqual(self._one(rnd(judge_lines=["No Critical found.", "0 Critical, 2 Important"])), 0)


class PanelDecision(unittest.TestCase):
    def _rounds(self, *crits):
        from tasks.core import parse_judge_rounds
        return [parse_judge_rounds(rnd(judge_lines=(["**Critical** a.py:1 x"] * c) or ["fine"]))[0] for c in crits]

    def test_assertive_takes_two_rounds(self):
        self.assertTrue(SR.panel_decision("assertive", self._rounds(), SR.DEFAULT_ROUNDS)[0])
        self.assertTrue(SR.panel_decision("assertive", self._rounds(0), SR.DEFAULT_ROUNDS)[0])
        ok, text = SR.panel_decision("assertive", self._rounds(0, 0), SR.DEFAULT_ROUNDS, task="007")
        self.assertFalse(ok)
        self.assertIn("--owner-ok", text)
        self.assertIn("tasks panel-review 007 --mode impl", text)
        self.assertIn("round 2 counted no Critical", text)

    def test_a_critical_in_the_caps_last_round_allows_one_more(self):
        self.assertTrue(SR.panel_decision("assertive", self._rounds(0, 1), SR.DEFAULT_ROUNDS)[0])
        ok, text = SR.panel_decision("assertive", self._rounds(0, 1, 3), SR.DEFAULT_ROUNDS)
        self.assertFalse(ok)                                   # past the one extension: the owner only
        self.assertIn("plus one because round 2 counted a Critical", text)

    def test_a_critical_in_an_earlier_round_does_not_extend(self):
        self.assertFalse(SR.panel_decision("assertive", self._rounds(1, 0), SR.DEFAULT_ROUNDS)[0])

    def test_reversible_takes_one(self):
        # impl round 1, R7 (sonnet): the owner's words give the extension only to the two-round risks
        self.assertFalse(SR.panel_decision("reversible", self._rounds(0), SR.DEFAULT_ROUNDS)[0])
        self.assertFalse(SR.panel_decision("reversible", self._rounds(1), SR.DEFAULT_ROUNDS)[0])
        caps = {**SR.DEFAULT_ROUNDS, "reversible": 2}
        self.assertTrue(SR.panel_decision("reversible", self._rounds(0, 1), caps)[0])

    def test_unclassified_is_the_high_bar(self):
        self.assertTrue(SR.panel_decision("unclassified", self._rounds(0), SR.DEFAULT_ROUNDS)[0])
        self.assertFalse(SR.panel_decision("unclassified", self._rounds(0, 0), SR.DEFAULT_ROUNDS)[0])

    def test_a_larger_cap_never_refuses_what_a_smaller_allows(self):
        for crits in ((), (0,), (0, 0), (0, 1), (1, 1, 0), (0, 0, 0, 0)):
            rs = self._rounds(*crits)
            for small in (1, 2, 3):
                for big in range(small, 5):
                    a = SR.panel_decision("assertive", rs, {**SR.DEFAULT_ROUNDS, "assertive": small})[0]
                    b = SR.panel_decision("assertive", rs, {**SR.DEFAULT_ROUNDS, "assertive": big})[0]
                    self.assertTrue(b or not a, (crits, small, big))


class OneCap(unittest.TestCase):
    """Task 344 (PLAN-PLAYBOOK-2.md 4.1 a part 2; DESIGN §3.3 point 12): at most 2 impl rounds before the close,
    whatever the risk and whatever the rounds counted; a third only with the owner's word."""

    def _rounds(self, *crits):
        from tasks.core import parse_judge_rounds
        return [parse_judge_rounds(rnd(judge_lines=(["**Critical** a.py:1 x"] * c) or ["fine"]))[0] for c in crits]

    def test_two_rounds_for_every_risk(self):
        """Row 1: no risk is asked — 0 and 1 rounds allow another."""
        self.assertEqual(SR.ROUND_CAP, 2)
        for crits in ((), (0,), (3,)):
            self.assertTrue(SR.panel_decision(self._rounds(*crits), task="007")[0], crits)

    def test_a_third_round_is_refused_whatever_the_risk_or_the_criticals(self):
        """Row 2: two rounds — a Critical in round 2 extends nothing — and three refuse."""
        for crits in ((0, 0), (0, 1), (1, 1), (0, 0, 0), (0, 1, 2)):
            self.assertFalse(SR.panel_decision(self._rounds(*crits), task="007")[0], crits)

    def test_the_refusal_names_the_ways_out(self):
        """Row 11: the cap of 2, the last round's close, the owner's round — and no config key."""
        ok, text = SR.panel_decision(self._rounds(0, 1), task="007")
        self.assertFalse(ok)
        self.assertIn("2 impl panel round(s)", text)
        self.assertIn('tasks work done --force --reason "…"', text)
        self.assertIn('tasks panel-review 007 --mode impl --owner-ok --reason "…"', text)
        for gone in ("review_rounds", "config.json", "Critical", "reversible", "assertive"):
            self.assertNotIn(gone, text)


class Keys(unittest.TestCase):

    def test_no_code_names_the_gone_keys(self):
        """Row 5 (plan panel P7): no file under plugins/playbook but `.md`, `.json` and `.txt` — code, hooks, scripts
        with no extension, `.template`, the provider mirror — names `review_rounds` or `rounds_cap`. No exemption."""
        import re
        gone = re.compile(r"review_rounds|rounds_cap")
        hits = []
        for p in sorted(PLUGIN.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix in (".md", ".json", ".txt"):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits += [f"{p.relative_to(PLUGIN)}:{i}" for i, ln in enumerate(text.splitlines(), 1) if gone.search(ln)]
        self.assertEqual(hits, [])


class Stricter(unittest.TestCase):
    def test_the_order(self):
        self.assertEqual(SR.stricter("reversible", "assertive"), "assertive")
        self.assertEqual(SR.stricter("irreversible", "assertive"), "irreversible")
        self.assertEqual(SR.stricter("irreversible", "unclassified"), "unclassified")
        self.assertEqual(SR.stricter("assertive", "bogus"), "bogus")      # unknown = the high bar
        self.assertEqual(SR.stricter(None, "reversible"), "reversible")   # nothing recorded: the current one
        self.assertEqual(SR.stricter(None, None), "unclassified")

    def test_symmetric(self):
        rs = ["reversible", "assertive", "irreversible", "unclassified"]
        for a in rs:
            for b in rs:
                self.assertEqual(SR.stricter(a, b), SR.stricter(b, a))


class MinorSeverity(unittest.TestCase):
    """D3 (the owner, 2026-10-15: "Da, „Minor” separat, nenumărat"): a third severity, written apart and never
    counted; the CAP line keeps counting the Critical and Important findings exactly (plan panel P6)."""


    def test_every_review_prompt_asks_for_minors_apart(self):
        from tasks import template as T
        prompts = {
            "plan": T.plan_review_prompt(".agent/tasks/001-t/task.md"),
            "impl": T.impl_review_prompt(".agent/tasks/001-t/task.md"),
            "panel plan": T.panel_plan_review_prompt(".agent/tasks/001-t/task.md"),
            "panel impl": T.panel_impl_review_prompt(".agent/tasks/001-t/task.md"),
        }
        for name, text in prompts.items():
            self.assertNotIn("drop Minor", text, name)
            # the instruction itself, not only the marker (finding_clauses names it too — the mutation run, M30)
            self.assertEqual(text.count("each starting with `**Minor**`"), 1, name)
            self.assertIn("never counted", text, name)
            self.assertIn("the CAP line counts the Critical and Important ones", text, name)
            self.assertIn("`more remain` only when you dropped a Critical or Important one", text, name)


    def test_a_minor_marker_is_not_a_counted_finding(self):
        from tasks.stop_rule import _FINDING_RE
        self.assertIsNone(_FINDING_RE.search("**Minor** — x"))


if __name__ == "__main__":
    unittest.main()
