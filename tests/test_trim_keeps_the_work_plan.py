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
        # task 213 (P8): or a line of the guide — the text the renders carried until then, moved there verbatim
        rendered = set()
        for ty in [None, "quick", "light", *core.PLAYBOOKS]:
            rendered |= set(template.render_template(1, "X", ty).splitlines())
        rendered |= set(template.standing_orders().splitlines())
        rendered |= set(template.guide_text().splitlines())
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
        self.assertTrue(any(ln.startswith("> - For each work section: what could go wrong") for ln in members))


def _guide() -> str:
    """The template's Work Plan guidance line, as the CLI renders it."""
    return next(ln for ln in core.template_commentary_lines()
                if ln.startswith("> - For each work section: what could go wrong"))


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
        # (the fat section is "Notes": a light task's "## Review" is kept since the owner's W4c ruling)
        text = _record(work=_gates(4, 200), work_heading="Work", after=_fat("Notes", 4_000))
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

    def test_the_title_is_kept_whatever_the_newer_section_fills(self):
        # mutation run (W6, M12): with a newer section sized to fill exactly what the kept part leaves, a title
        # that is not in the kept set is the section the newest-first fill drops. The title chunk carries text of
        # its own (an older template's sticker): a bare title is shorter than the marker that would replace it,
        # so the fill kept it anyway and the mutant survived the first version of this test.
        older_sticker = "> **Older sticker:** " + "s" * 300 + "\n\n"
        for d in range(1_000, 2_900, 25):
            text = _record(work=_gates(2, 20), after=_fat("Notes", 30_000) + _fat("Debrief", d))
            text = text.replace("# 007 - Example Task\n\n", "# 007 - Example Task\n\n" + older_sticker, 1)
            out, _ = core.select_task_context(text, 3_000)
            self.assertIn("# 007 - Example Task", out, f"Debrief {d}")

    def test_the_templates_tail_sections_go_before_an_older_section(self):
        # mutation run (W6, M9): the Verification Receipt is newer than Notes, yet it is sent last
        text = _record(work=_gates(2, 20), after=_fat("Notes", 1_500) + _fat("Verification Receipt", 1_500))
        out, receipt = core.select_task_context(text, len(text) - 1_000)
        self.assertIn("## Notes", out)
        self.assertIn("Verification Receipt", receipt.split("· dropped:")[1])

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
        self.assertLessEqual(len(out), 20_000)
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
        self.assertLessEqual(len(out), 20_000)
        self.assertEqual(out.count("- [x] W"), 10)
        self.assertIn("prose cut:", receipt)


class ImplRoundOne(unittest.TestCase):
    """Impl panel round 1 (R1-R9): the cases its judges built against the first version."""

    def _gate_lines(self, text):
        return [ln for ln in text.splitlines() if ln.lstrip().startswith(("- [ ]", "- [x]"))]

    def test_nested_gates_stay_when_the_parent_note_is_cut(self):                      # R1
        work = "".join(f"- [x] W{i} parent — " + "p" * 1_500 + "\n"
                       + "".join(f"  - [x] W{i}.{k} child — " + "c" * 400 + "\n" for k in range(1, 4))
                       for i in range(1, 9))
        text = _record(work=work)
        out, _ = core.select_task_context(text, 9_000)
        self.assertLessEqual(len(out), 9_000)
        self.assertEqual(len(self._gate_lines(out)), len(self._gate_lines(text)))
        self.assertIn("  - [x] W8.3 child", out)

    def test_an_outcome_written_below_its_gate_keeps_its_start(self):                  # R2
        work = "".join(f"- [x] W{i} step\n  outcome-start-{i} " + "o" * 1_500 + "\n" for i in range(1, 11))
        text = _record(work=work)
        out, _ = core.select_task_context(text, 6_000)
        self.assertLessEqual(len(out), 6_000)
        for i in range(1, 11):
            self.assertIn(f"outcome-start-{i}", out)

    def test_a_checkbox_after_an_unclosed_fence_is_still_a_gate(self):                # R7
        work = "intro\n```python\nx = 1\n" + _gates(8, 600)
        text = _record(work=work)
        out, _ = core.select_task_context(text, 3_500)
        self.assertLessEqual(len(out), 3_500)
        self.assertEqual(out.count("- [x] W"), 8)

    def test_subheadings_stay_above_their_gates(self):                                 # R8
        design = ("## Design Phase\n\n### Understand\n" + "u" * 3_000 + "\n" + _gates(2, 40, "U")
                  + "### Structure\n" + "s" * 3_000 + "\n" + _gates(2, 40, "S"))
        text = _record(work=_gates(2, 40), before=design)
        out, _ = core.select_task_context(text, 3_000)
        self.assertLessEqual(len(out), 3_000)
        self.assertIn("### Structure", out)
        self.assertLess(out.index("### Structure"), out.index("- [x] S1"))

    def test_rulings_stay_whole_when_cutting_notes_is_enough(self):                   # R5
        rulings = "## Owner rulings\n<!-- pin -->\n" + "".join(f"- ruling-{i} " + "r" * 200 + "\n" for i in range(1, 14))
        text = _record(work=_gates(30, 1_500), before=rulings)
        out, _ = core.select_task_context(text, 12_000)
        self.assertLessEqual(len(out), 12_000)
        for i in range(1, 14):
            self.assertIn(f"- ruling-{i} ", out)
        self.assertNotIn("prose cut", out)

    def test_the_intent_is_cut_before_the_rulings(self):                              # R5 (mutation run M21)
        rulings = "## Owner rulings\n" + "".join(f"- ruling-{i} " + "r" * 220 + "\n" for i in range(1, 14))
        text = _record(work=_gates(3, 40), before=rulings).replace("make the thing", "i" * 20_000)
        out, receipt = core.select_task_context(text, 6_000)
        self.assertLessEqual(len(out), 6_000)
        for i in range(1, 14):
            self.assertIn(f"- ruling-{i} ", out)
        self.assertIn("Intent", receipt.split("· prose cut: ")[1].split(" · ")[0])

    def test_the_newest_ruling_survives_a_rulings_cut(self):                           # R5 (grok)
        rulings = "## Owner rulings\n" + "".join(f"- ruling-{i} " + "r" * 400 + "\n" for i in range(1, 41))
        text = _record(work=_gates(3, 40), before=rulings)
        out, _ = core.select_task_context(text, 5_000)
        self.assertLessEqual(len(out), 5_000)
        self.assertIn("- ruling-40 ", out)

    def test_the_newest_triage_survives_a_review_cut(self):                           # R4
        triage = "## Implementation Review\n\n" + "".join(f"- **T{i}** a finding " + "t" * 300 + "\n" for i in range(1, 31))
        text = _record(work=_gates(3, 40), after=triage)
        out, _ = core.select_task_context(text, 4_000)
        self.assertLessEqual(len(out), 4_000)
        self.assertIn("**T30** a finding", out)

    def test_no_other_section_is_sent_once_kept_content_is_cut(self):                 # R3
        text = _record(work=_gates(20, 1_000), after=_fat("Debrief", 800))
        out, receipt = core.select_task_context(text, len(text) - 1_000)
        self.assertIn("gate notes cut:", receipt)
        self.assertIn("Debrief", receipt.split("· dropped:")[1])
        self.assertIn("n" * 500, out, "notes were cut further than the room needed")

    def test_gates_are_not_sliced_while_prose_remains(self):                           # R6 (codex)
        text = _record(work="w" * 20_000 + "\n" + _gates(30, 300)).replace("make the thing", "i" * 20_000)
        out, receipt = core.select_task_context(text, 5_000)
        self.assertLessEqual(len(out), 5_000)
        self.assertEqual(out.count("- [x] W"), 30)

    def test_titles_alone_over_the_room_say_so(self):                                  # R6
        text = _record(work=_gates(400, 50), risk="assertive")
        out, receipt = core.select_task_context(text, 3_000)
        self.assertLessEqual(len(out), 3_000)
        self.assertIn("gate titles of the kept and pinned sections alone exceed the budget", receipt)
        self.assertIn("## Risk\nassertive", out, "the last step cut the Risk value")

    def test_an_authors_own_line_in_the_standing_orders_stays(self):                  # R9
        so = template.standing_orders() + "\n- [ ] S1 my own standing gate\n\n"
        text = _record(work=_gates(2, 20), after=so + _fat("Notes", 30_000))
        out, _ = core.select_task_context(text, 3_000)
        self.assertIn("- [ ] S1 my own standing gate", out)


class ImplRoundTwo(unittest.TestCase):
    """Impl panel round 2 (S1-S8): its judges' reproductions."""

    def _count(self, text, prefix="- [x] W"):
        return text.count(prefix)

    def test_a_long_template_title_does_not_crowd_out_the_notes(self):               # S1
        # Task 223: no template title is over the 120 characters a trimmed gate keeps any more (S1 was about the
        # 612-character ones); the longest of today's still leaves every note its start. A record written before
        # keeps its old long titles, which are now read like any long title an agent wrote (its first 120).
        long_title = max(core._template_gate_titles(), key=len)
        self.assertLessEqual(len(long_title), core.TRIM_TITLE_MAX)
        self.assertGreater(len(long_title), 100)
        work = "".join(f"- [x] {long_title} — note-start-{i} " + "n" * 500 + "\n" for i in range(1, 21))
        text = _record(work=work)
        out, _ = core.select_task_context(text, 8_000)
        self.assertLessEqual(len(out), 8_000)
        for i in range(1, 21):
            self.assertIn(f"note-start-{i} ", out)

    def test_a_retro_records_phases_are_kept(self):                                    # S2
        phases = "## Phase 2: Per-Task Review\n" + _gates(6, 200, "P") + "\n## Findings\n" + _gates(3, 100, "F")
        # a newer Debrief that fills the room: the newest-first fill kept it and dropped the phases
        text = _record(work=_gates(2, 20), after=phases + _fat("Debrief", 3_800))
        out, _ = core.select_task_context(text, 5_000)
        self.assertLessEqual(len(out), 5_000)
        self.assertEqual(out.count("- [x] P"), 6)
        self.assertEqual(out.count("- [x] F"), 3)

    def test_a_nested_gate_after_a_blank_line_stays(self):                           # S3
        work = "".join(f"- [x] W{i} parent — " + "p" * 900 + f"\n\n    - [x] W{i}.1 child — c\n" for i in range(1, 6))
        text = _record(work=work)
        out, _ = core.select_task_context(text, 2_500)
        self.assertLessEqual(len(out), 2_500)
        for i in range(1, 6):
            self.assertIn(f"- [x] W{i}.1 child", out)

    def test_a_long_risk_explanation_does_not_cost_gates(self):                      # S4
        text = _record(work=_gates(10, 20), risk="assertive\n" + "e" * 10_000)
        out, _ = core.select_task_context(text, 5_000)
        self.assertLessEqual(len(out), 5_000)
        self.assertIn("## Risk\nassertive", out)
        self.assertEqual(self._count(out), 10)

    def test_the_risk_value_survives_when_all_ordinary_prose_goes(self):             # S4 (mutation run M29)
        # a big pinned section forces the unpinned prose to 0 before the pinned cut: the Risk value stays
        pinned = "## Pinned big\n<!-- pin -->\n" + "b" * 20_000 + "\n\n"
        text = _record(work=_gates(5, 20), risk="assertive\n" + "e" * 10_000, after=pinned)
        out, _ = core.select_task_context(text, 1_500)
        self.assertLessEqual(len(out), 1_500)
        self.assertIn("## Risk\nassertive", out)
        self.assertEqual(self._count(out), 5)

    def test_many_dropped_sections_do_not_cost_gates(self):                          # S5
        after = "".join(f"## Notes section number {k} with a long heading of eighty characters …\n" + "x" * 3_000 + "\n"
                        for k in range(30))       # none fits: every heading lands in the marker
        text = _record(work=_gates(100, 4), before=after)   # the marker stands before the Work Plan
        out, _ = core.select_task_context(text, 5_000)
        self.assertLessEqual(len(out), 5_000)
        self.assertEqual(self._count(out), 100)

    def test_short_pinned_sections_do_not_break_the_search(self):                    # S6
        # codex's shape: a cut marker is longer than a short pinned section's prose, so cutting every pinned
        # section to 0 is LARGER than cutting only the big one — the room is set where only the latter fits
        pins = "".join(f"## Pinned {k}\n<!-- pin -->\nshort pinned prose {k}\n\n" for k in range(30))
        small = _record(work=_gates(130, 4), after=pins + "## Pinned big\n<!-- pin -->\n" + "b" * 100 + "\n\n")
        text = _record(work=_gates(130, 4), after=pins + "## Pinned big\n<!-- pin -->\n" + "b" * 10_000 + "\n\n")
        room = len(small) + 200
        out, _ = core.select_task_context(text, room)
        self.assertLessEqual(len(out), room)
        self.assertEqual(self._count(out), 130)
        self.assertIn("short pinned prose 29", out)

    def test_a_fenced_subheading_does_not_leave_a_fence_open(self):                  # S7
        # the cut lands on the fence's line after its `### inside` (gemini's shape): the fence must go whole
        body = ["```text\n"] + [f"line {k} " + "q" * 80 + "\n" for k in range(40)] + ["### inside\n", "more\n", "```\n",
                                                                                         "i" * 4_000 + "\n"]
        lines = [(1, "## Intent\n")] + [(k + 2, ln) for k, ln in enumerate(body)]
        keep = sum(len(ln) for ln in body[:41]) + 2        # opener + 40 lines fit; the next line does not
        out, did = core._trim_prose(lines, keep, False, False)
        self.assertTrue(did)
        fences = [ln for _, ln in out if ln.startswith("```")]
        self.assertEqual(len(fences) % 2, 0, "".join(ln for _, ln in out)[-300:])

    def test_the_users_words_go_before_a_machine_receipt(self):                     # S8 (References)
        # at the smallest room that carries the owner's words, the (smaller, newer) audit receipt must not have
        # been sent before them — it was, while References was a send-last section
        refs = "## References\n\n### Recent Chat\n\n```text\nthe owner's own words " + "w" * 1_000 + "\n```\n\n"
        text = _record(work=_gates(2, 20), before=refs, after=_receipts(1) + _fat("Notes", 30_000))
        out, room = "", 0
        for room in range(800, 3_000, 10):
            out, _ = core.select_task_context(text, room)
            if "the owner's own words" in out:
                break
        self.assertIn("the owner's own words", out)
        self.assertNotIn("### 2026-10-15T10:00", out, f"room {room}")

    def test_the_notice_carries_a_hard_cut(self):                                    # S8 (grok)
        receipt = ("task.md 9,000 → 3,000 chars · gate notes cut: 48 · kept: Work Plan · dropped: (none)"
                   " · WARNING: the headings and gate titles … hard-truncated — raise the budget")
        self.assertIn("WARNING: hard-truncated", core.trim_notice(receipt, "t.md"))

    def test_a_long_section_is_cut_quickly(self):                                    # S8 (speed)
        import time
        notes = "## Notes\n" + "".join(f"log line {k} " + "z" * 3 + "\n" for k in range(4_000))
        text = _record(work=_gates(10, 50), after=notes + _fat("Debrief", 70_000))
        t0 = time.monotonic()
        core.select_task_context(text, 50_000)
        self.assertLess(time.monotonic() - t0, 2.0)


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
