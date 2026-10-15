#!/usr/bin/env python3
"""The judges' trim keeps the Work Plan (PHASE-B.md item 2, task 212).

The owner, 2026-10-15: what a judge must never lose is Intent, Design, Risk, his rulings
and the Work Plan; the template's own text and the older audit receipts go first; and when
even the kept part does not fit, every gate stays, with the start of its note and a pointer
to the full task.md. Measured before: at the 50K argv window 21 of 41 WoW task.md and 18 of
202 here lost the whole Work Plan (`select_task_context` filled from the END, where the
template's Standing Orders and the stacked audit receipts sit).

Pure stdlib unittest. Run: python3 tests/test_trim_keeps_the_work_plan.py
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))
from tasks import core  # noqa: E402
from tasks import template  # noqa: E402


class TemplateCommentary(unittest.TestCase):
    """W1 (T1): the set stage 1 may remove is the template's COMMENTARY — its `> ` lines —
    never a field value, a separator, a placeholder, a heading or a gate."""

    def test_every_member_is_a_rendered_blockquote_line(self):
        rendered = set()
        for ty in [None, "quick", "light", *core.PLAYBOOKS]:
            rendered |= set(template.render_template(1, "X", ty).splitlines())
        rendered |= set(template.standing_orders().splitlines())
        members = core.template_commentary_lines()
        self.assertTrue(members, "the set is empty")
        for ln in members:
            self.assertTrue(ln.startswith(">"), ln)
            self.assertIn(ln, rendered, ln)

    def test_values_separators_placeholders_headings_and_gates_are_not_members(self):
        members = core.template_commentary_lines()
        for ln in ("pending", "unclassified", "---", "(optional)", "(write work gates here)",
                   "## Work Plan", "- [ ] Do the work"):
            self.assertNotIn(ln, members, ln)

    def test_the_work_plan_guidance_is_a_member(self):
        members = core.template_commentary_lines()
        self.assertTrue(any(ln.startswith("> For each work section: what could go wrong?") for ln in members))


def _guide() -> str:
    """The template's Work Plan guidance line, as the CLI renders it."""
    return next(ln for ln in core.template_commentary_lines()
                if ln.startswith("> For each work section: what could go wrong?"))


def _fat(heading: str, chars: int) -> str:
    unit = f"body-of-{heading.replace(' ', '-')} "
    return f"## {heading}\n\n{(unit * (chars // len(unit) + 1))[:chars]}\n\n"


def _gates(n: int, note: int, prefix: str = "W") -> str:
    return "".join(f"- [x] {prefix}{i} step {i} — " + ("n" * note) + f" end-of-note-{prefix}{i}\n" for i in range(1, n + 1))


def _record(*, work: str, before: str = "", after: str = "", risk: str = "unclassified",
            status: str = "pending", work_heading: str = "Work Plan") -> str:
    return ("# 007 - Example Task\n\n> **Gate discipline:** One gate → do work → check box → next gate.\n\n"
            f"## Status\n{status}\n\n## Risk\n{risk}\n\n## Intent\nmake the thing\n\n## Why\nbecause\n\n"
            + before + f"## {work_heading}\n\n{_guide()}\n\n" + work + "\n---\n\n" + after)


def _receipts(n: int) -> str:
    return "## Pre-Panel Audit\n\n" + "".join(
        f"### 2026-10-1{5 - i}T10:00:00+03:00 · PASS · commit {i:040d}\n    - [CLEAN] conflict-markers\n" + "    x\n" * 40 + "\n"
        for i in range(n))


class StageOneTemplateText(unittest.TestCase):
    """W2 / Q2-Q3: template commentary, the standing orders and the older audit receipts go first."""

    def test_commentary_goes_values_quotes_and_fenced_lines_stay(self):
        chat = ("## References\n\n### Recent Chat\n\n```text\n" + _guide() + "\n---\n```\n\n")
        review = "## Implementation Review\n\n> **Important** `x.py:1` — a judge's finding\n\n"
        text = _record(work=_gates(3, 40), before=chat, after=review + _fat("Notes", 30_000))
        out, receipt = core.select_task_context(text, 8_000)
        self.assertNotIn(_guide() + "\n\n- [x] W1", out, "the Work Plan's template guidance was sent")
        self.assertIn("```text\n" + _guide() + "\n---\n```", out, "a fenced (pasted) line was removed")
        self.assertIn("## Risk\nunclassified\n", out)
        self.assertIn("## Status\npending\n", out)
        self.assertIn("\n---\n", out.split("## Work Plan", 1)[1])
        self.assertIn("> **Important** `x.py:1` — a judge's finding", out)
        self.assertIn("template text not sent:", receipt)
        self.assertLess(receipt.index("template text not sent:"), receipt.index("· kept:"))

    def test_older_audit_receipts_and_the_standing_orders_go_first(self):
        text = _record(work=_gates(3, 40), after=template.standing_orders() + "\n" + _receipts(3))
        text += _fat("Debrief", 200)
        out, receipt = core.select_task_context(text, len(text) - 1_000)
        self.assertEqual(out.count("### 2026-10-1"), 1, "more than the newest receipt was sent")
        self.assertIn("### 2026-10-15T10:00:00+03:00", out, "the newest receipt is not the one sent")
        self.assertIn("2 older audit receipt(s) not sent", out)
        so = out.split("## Standing Orders", 1)[1].split("## ", 1)[0]
        self.assertLessEqual(len(so.strip().splitlines()), 1, so)
        self.assertIn("audit receipts: the newest of 3", receipt)


class StageTwoMustKeep(unittest.TestCase):
    """W3 / Q4, Q5, Q8, Q12, Q13."""

    def test_the_work_plan_survives_a_fat_newer_section_which_is_named_with_its_gates(self):
        fat = "## Pre-review\n\n" + _gates(2, 20, "R") + ("f" * 40_000) + "\n\n"
        text = _record(work=_gates(6, 300), after=fat)
        out, receipt = core.select_task_context(text, 10_000)
        for i in range(1, 7):
            self.assertIn(f"end-of-note-W{i}", out)
        self.assertNotIn("f" * 100, out)
        self.assertIn("Pre-review", receipt.split("· dropped:")[1])
        self.assertIn("Pre-review — not sent: 2 gate(s), 2 checked", out)

    def test_the_review_triage_survives_a_newer_section(self):
        # the owner at W4c (task 212): the review sections are kept too — what the previous round found and
        # what was done with it reaches the next judge only through task.md (the SETTLED block reads judge.md)
        triage = ("## Implementation Review\n\n- [x] Run the panel — done\n\n### Triage\n"
                  + "".join(f"- **T{i}** a finding — REJECT: reason {i} " + "r" * 200 + "\n" for i in range(1, 12)))
        text = _record(work=_gates(4, 100), after=triage + "\n" + _fat("Debrief", 3_000))
        out, receipt = core.select_task_context(text, len(text) - 2_000)
        self.assertIn("**T11** a finding — REJECT: reason 11", out)
        self.assertIn("Debrief", receipt.split("· dropped:")[1])

    def test_title_status_risk_why_and_rulings_survive(self):
        # a newer section that fills the room: today's newest-first fill kept it and dropped the oldest
        rulings = "## Owner rulings\n- 2026-10-15: \"Da\"\n\n"
        text = _record(work=_gates(2, 20), before=rulings, risk="assertive",
                       after=_fat("Notes", 30_000) + _fat("Debrief", 2_700))
        out, receipt = core.select_task_context(text, 3_000)
        for must in ("# 007 - Example Task", "## Status", "## Risk\nassertive", "## Why", "## Owner rulings"):
            self.assertIn(must, out, must)
        self.assertLessEqual(len(out), 3_000)
        self.assertNotIn("WARNING", receipt, "the elision markers pushed a fitting selection over the budget")

    def test_a_light_tasks_work_section_is_kept(self):
        text = _record(work=_gates(4, 200), work_heading="Work", after=_fat("Review", 4_000))
        out, _ = core.select_task_context(text, 5_000)
        for i in range(1, 5):
            self.assertIn(f"end-of-note-W{i}", out)

    def test_a_fenced_heading_does_not_detach_the_gates_after_it(self):
        # the gates after a fenced `## Example` are the Work Plan's: with a fence-blind split they become
        # a section of their own, newer than the Work Plan, and a newer Debrief outranks them
        work = _gates(2, 20) + "```\n## Example\n```\n" + _gates(2, 1_500, "V")
        text = _record(work=work, after=_fat("Debrief", 4_500))
        out, _ = core.select_task_context(text, 5_200)
        self.assertIn("end-of-note-V1", out)
        self.assertIn("end-of-note-V2", out)

    def test_a_small_newer_section_survives_a_fat_older_one(self):
        text = _record(work=_gates(2, 20), after=_fat("Notes", 30_000) + _fat("Debrief", 300))
        out, receipt = core.select_task_context(text, 3_000)
        self.assertIn("## Debrief", out)
        self.assertIn("Notes", receipt.split("· dropped:")[1])


class StageThreeEveryGateStays(unittest.TestCase):
    """W4 / Q6, Q7: when the kept part alone does not fit, no Work Plan gate line is lost."""

    def test_notes_are_cut_every_gate_kept_with_its_original_line(self):
        text = _record(work=_gates(40, 1_500))
        out, receipt = core.select_task_context(text, 20_000)
        self.assertLessEqual(len(out), 20_000 + 200)
        self.assertEqual(out.count("- [x] W"), 40)
        lines = text.splitlines()
        for i in (1, 17, 40):
            n = next(k for k, ln in enumerate(lines, 1) if ln.startswith(f"- [x] W{i} "))
            self.assertIn(f"task.md line {n}]", out.split(f"- [x] W{i} ", 1)[1].split("\n", 1)[0])
        self.assertIn("gate notes cut:", receipt)
        self.assertLess(receipt.index("gate notes cut:"), receipt.index("· kept:"))

    def test_a_huge_intent_does_not_push_out_the_gates(self):
        text = _record(work=_gates(10, 300)).replace("make the thing", "i" * 60_000)
        out, receipt = core.select_task_context(text, 20_000)
        self.assertLessEqual(len(out), 20_000 + 200)
        self.assertEqual(out.count("- [x] W"), 10)
        self.assertIn("prose cut:", receipt)


class TheJudgesNotice(unittest.TestCase):
    """Q10: the notice a judge reads says what was removed, cut and dropped."""

    def test_cut_notes_with_no_section_dropped_are_told(self):
        receipt = ("task.md 90,000 → 49,000 chars · gate notes cut: 12 · kept: Intent, Work Plan"
                   " · dropped: (none)")
        notice = core.trim_notice(receipt, ".agent/tasks/007-x/task.md")
        self.assertIn("gate notes cut: 12", notice)
        self.assertNotIn("dropped sections: (none)", notice)
        self.assertIn(".agent/tasks/007-x/task.md", notice)

    def test_dropped_sections_are_named(self):
        receipt = ("task.md 90,000 → 49,000 chars · kept: Intent, Work Plan · dropped: Notes, Debrief"
                   " · WARNING: x")
        notice = core.trim_notice(receipt, "t.md")
        self.assertIn("dropped sections: Notes, Debrief", notice)
        self.assertNotIn("WARNING", notice)


class TheNoticeReachesTheJudges(unittest.TestCase):
    """Q10 through the real panel path: `cmd_panel_review` with faked claude judges, a task.md whose
    gate notes must be cut — the prompt each judge receives says so (T2)."""

    def test_a_panel_judge_is_told_its_notes_were_cut(self):
        import contextlib
        import io
        import json
        import os
        import shutil
        import subprocess
        import tempfile
        from unittest import mock
        from provider.adapters.claude import ClaudeAdapter
        from tasks import review as R
        d = Path(tempfile.mkdtemp(prefix="pb212-"))
        self.addCleanup(shutil.rmtree, d, True)
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        td = d / ".agent" / "tasks" / "007-example"
        td.mkdir(parents=True)
        (td / "task.md").write_text(_record(work=_gates(30, 2_000), risk="assertive"), encoding="utf-8")
        (d / ".agent" / "models.json").write_text(json.dumps(
            {"panel": ["claude:opus", "claude:sonnet"], "default_judge": "claude:opus"}), encoding="utf-8")
        (d / ".agent" / "config.json").write_text(json.dumps({"review_context_chars_stdin": 40_000}),
                                                  encoding="utf-8")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=d, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c0"], cwd=d,
                       check=True)
        R._PB_JOURNAL_MOD = None
        R._PB_JOURNAL_LOADED = False
        seen = []

        def judge(self_, prompt, model, system_context, **kw):
            seen.append(prompt + "\n" + system_context)
            return "No findings.\n\nCAP: 0/5 reported, exhausted\n"
        cwd = os.getcwd()
        os.chdir(d)
        try:
            with mock.patch.object(ClaudeAdapter, "is_available", classmethod(lambda cls: True)), \
                 mock.patch.object(ClaudeAdapter, "run_headless_judge", judge), \
                 mock.patch.dict(os.environ, {"PLAYBOOK_REVIEW_CONTEXT_CHARS_STDIN": "40000"}), \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                try:
                    R.cmd_panel_review(["007", "--mode", "plan", "--models", "claude:opus,claude:sonnet"])
                except SystemExit:
                    pass
        finally:
            os.chdir(cwd)
        self.assertEqual(len(seen), 2, "the faked judges were not called")
        for payload in seen:
            self.assertIn("was TRIMMED", payload)
            self.assertIn("gate notes cut:", payload)
            self.assertNotIn("dropped sections: (none)", payload)
            self.assertEqual(payload.count("- [x] W"), 30, "a Work Plan gate was not sent")


if __name__ == "__main__":
    unittest.main()
