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


class GuardPaths(unittest.TestCase):
    def test_absent_is_empty(self):
        self.assertEqual(SR.resolve_guard_paths({}), [])

    def test_a_list_that_cannot_be_read_guards_everything(self):
        for bad in ("hooks/", [1], [""], {"a": 1}):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                pats = SR.resolve_guard_paths({"guard_paths": bad})
            self.assertEqual(pats, ["*"], bad)
            self.assertTrue(SR.guarded("anything/at/all.py", pats))
            self.assertIn("guard_paths", err.getvalue())

    def test_a_pattern_matches_itself_what_is_below_it_and_a_glob(self):
        pats = ["plugins/x/hooks", "plugins/x/scripts/*-hook", "plugins/x/tasks/core.py"]
        self.assertTrue(SR.guarded("plugins/x/hooks", pats))
        self.assertTrue(SR.guarded("plugins/x/hooks/a/b.sh", pats))
        self.assertTrue(SR.guarded("plugins/x/scripts/stop-hook", pats))
        self.assertTrue(SR.guarded("plugins/x/tasks/core.py", pats))
        self.assertFalse(SR.guarded("plugins/x/hooksmith.py", pats))       # a prefix of a name is not below it
        self.assertFalse(SR.guarded("plugins/x/tasks/core.py.bak", pats))
        self.assertFalse(SR.guarded("plugins/x/scripts/stop-hook.md", ["plugins/x/scripts/*-hook"]))
        self.assertTrue(SR.guarded("plugins/x/hooks/z", ["plugins/x/hooks/"]))   # a trailing slash is the same

    def test_both_sides_are_normalised(self):                         # impl round 1, R5
        self.assertTrue(SR.guarded("src/hooks/x.sh", ["./src/hooks/"]))
        self.assertTrue(SR.guarded("src/hooks/x.sh", ["/src//hooks"]))
        self.assertTrue(SR.guarded("./src//hooks/x.sh", ["src/hooks/"]))
        self.assertFalse(SR.guarded("src/hooksmith.sh", ["./src/hooks/"]))

    def test_a_root_level_glob_names_something(self):                  # impl round 2, S8 (gemini)
        d = Path(tempfile.mkdtemp(prefix="pb209g-"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "CLAUDE.md").write_text("x\n", encoding="utf-8")
        (d / "hooks").mkdir()
        self.assertEqual(SR.names_nothing(d, ["CLAUDE*.md", "hooks/*.sh", "./hooks/"]), [])
        self.assertEqual(SR.names_nothing(d, ["nothere/*.py", "gone.py"]), ["nothere/*.py", "gone.py"])

    def test_an_empty_pattern_matches_nothing(self):
        self.assertFalse(SR.guarded("a.py", ["", "/"]))


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
        return SR.round_criticals(rs[0])

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


class JudgeRequired(unittest.TestCase):
    def test_irreversible_and_unclassified_when_their_code_changed(self):
        for risk in ("irreversible", "unclassified", ""):
            self.assertTrue(SR.judge_required(risk, True, ["a.py"], ["a.py"], [])[0], risk)
            # docs and tests alone: tail certification's, not the post-panel judge's
            self.assertFalse(SR.judge_required(risk, True, [], ["README.md", "tests/t.py"], [])[0], risk)

    def test_assertive_and_reversible_only_on_a_guard_path(self):
        for risk in ("assertive", "reversible"):
            self.assertFalse(SR.judge_required(risk, True, ["a.py", "b/c.py"], ["a.py", "b/c.py"], ["hooks/"])[0])
            ok, why = SR.judge_required(risk, True, ["a.py", "hooks/x.sh"], ["a.py", "hooks/x.sh"], ["hooks/"])
            self.assertTrue(ok, risk)
            self.assertIn("hooks/x.sh", why)
            self.assertNotIn("a.py", why)

    def test_a_listed_file_of_any_class_needs_the_judge(self):
        # hooks.json wires the hooks; a .json and a tests/ file are non-behavioral by class (plan panel P9)
        for f in ("hooks/hooks.json", "tests/guard_fixture.py"):
            self.assertTrue(SR.judge_required("assertive", True, [], [f], ["hooks/", "tests/guard_fixture.py"])[0], f)

    def test_a_delta_that_cannot_be_listed_needs_the_judge_where_the_answer_depends_on_it(self):
        for risk in ("reversible", "assertive", "irreversible"):
            ok, why = SR.judge_required(risk, False, [], [], ["hooks/"], cause="git could not list it")
            self.assertTrue(ok, risk)
            self.assertIn("git could not list it", why)
        self.assertTrue(SR.judge_required("irreversible", False, [], [], [])[0])
        # no guard list and no automatic judge for the risk: nothing the delta could change
        for risk in ("reversible", "assertive"):
            self.assertFalse(SR.judge_required(risk, False, [], [], [])[0], risk)

    def test_the_automatic_judge_needs_no_configuration(self):        # impl round 1, R3: the owner's words
        self.assertTrue(SR.judge_required("irreversible", True, ["a.py"], ["a.py"], [])[0])
        self.assertTrue(SR.judge_required("unclassified", True, ["a.py"], ["a.py"], [])[0])

    def test_a_longer_guard_list_never_frees_a_close(self):
        beh = ["a.py", "hooks/x.sh", "lib/y.py"]
        lists = [[], ["lib/"], ["hooks/"], ["hooks/", "lib/"], ["*"]]
        for short in lists:
            for long_ in lists:
                if not set(short) <= set(long_):
                    continue
                for risk in ("reversible", "assertive", "irreversible"):
                    a = SR.judge_required(risk, True, beh, beh, short)[0]
                    b = SR.judge_required(risk, True, beh, beh, long_)[0]
                    self.assertTrue(b or not a, (short, long_, risk))


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

    def v(self, text):
        from tasks.post_d6 import parse_verdict
        return parse_verdict(text)

    def test_minors_are_counted_apart_and_never_fail_a_run(self):
        r = self.v("**Important** — `a.py:1` x.\n\n**Minor** — wording.\n**Minor** — a cheap hardening.\n\n"
                   "CAP: 1/5 reported, exhausted\n")
        self.assertFalse(r["unparsed"])
        self.assertEqual((r["important"], r["minor"], r["verdict"]), (1, 2, "FAIL"))
        r = self.v("**Minor** — wording.\n\nCAP: 0/5 reported, exhausted\n")
        self.assertEqual((r["verdict"], r["minor"], r["unparsed"]), ("PASS", 1, False))

    def test_the_cap_line_counts_critical_and_important_only(self):
        r = self.v("**Important** — `a.py:1` x.\n**Minor** — y.\n**Minor** — z.\n\nCAP: 3/5 reported, exhausted\n")
        self.assertTrue(r["unparsed"])
        self.assertEqual(r["verdict"], "FAIL")

    def test_an_unmarked_finding_is_not_hidden_by_a_minor(self):
        # plan panel P6 (opus): found 0 + minor 1 = CAP 1 must not pass
        r = self.v("Important: `a.py:1` x.\n**Minor** — y.\n\nCAP: 1/5 reported, exhausted\n")
        self.assertTrue(r["unparsed"])
        self.assertEqual(r["verdict"], "FAIL")

    def test_the_verdict_line_names_the_minors(self):
        from tasks.post_d6 import parse_verdict, verdict_line
        line = verdict_line(parse_verdict("**Minor** — y.\n\nCAP: 0/5 reported, exhausted\n"))
        self.assertIn("not counted: 0 SETTLED, 0 PRE-EXISTING, 1 Minor", line)

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
            # the instruction itself, not only the marker (post_d6_clauses names it too — the mutation run, M30)
            self.assertEqual(text.count("each starting with `**Minor**`"), 1, name)
            self.assertIn("never counted", text, name)
            self.assertIn("the CAP line counts the Critical and Important ones", text, name)
            self.assertIn("`more remain` only when you dropped a Critical or Important one", text, name)

    def test_a_minor_ends_the_finding_before_it(self):                 # impl round 1, R8 (codex)
        from tasks.post_d6 import parse_verdict
        text = ("**Important** [PRE-EXISTING] [file: keep.py] keep.py has an old flaw.\n"
                "**Minor** — `a.py:1` reads oddly.\n\nCAP: 1/5 reported, exhausted\n")
        r = parse_verdict(text, {"a.py"}, exists=lambda p: p == "keep.py")
        self.assertEqual((r["pre_existing"], r["important"], r["minor"], r["verdict"]), (1, 0, 1, "PASS"))

    def test_a_second_finding_on_the_same_line_is_not_the_firsts_text(self):   # impl round 2, S9 (gemini)
        from tasks.post_d6 import parse_verdict
        text = ("**Important** [PRE-EXISTING] [file: keep.py] an old flaw. **Important** `a.py:1` a new one.\n\n"
                "CAP: 2/5 reported, exhausted\n")
        r = parse_verdict(text, {"a.py"}, exists=lambda p: p == "keep.py")
        self.assertEqual((r["pre_existing"], r["important"]), (1, 1))

    def test_a_minor_marker_is_not_a_counted_finding(self):
        from tasks.post_d6 import _FINDING_RE
        self.assertIsNone(_FINDING_RE.search("**Minor** — x"))


if __name__ == "__main__":
    unittest.main()
