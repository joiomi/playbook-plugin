#!/usr/bin/env python3
"""The test contract (PHASE-B.md item 9a, task 278): tests first, as the specification.

The owner, 2026-10-21 15:15 (M1415): "daca am gandit o functionalitate, trebuie sa ii creez toate testele posibile
si imposibile, ca sa ma asigur ca codul pe care il voi scrie urmeaza path-ul lor". A full task (the Build and Fix
patterns) carries a `## Test contract` written at design, before any code: one row per behavior naming the test
that proves it, and a fixed list of hostile classes, each answered by rows or "does not apply: <why>". The plan
panel reviews the contract as its own lens; the impl panel asks each row for its test. The red receipt and the
edit hook's gate are 9b and 9c.

Each test here names the contract row of task 278 it proves. Pure stdlib unittest. Run:
python3 tests/test_test_contract.py
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parent
sys.path.insert(0, str(REPO / "plugins/playbook"))
from tasks import core  # noqa: E402
from tasks import template  # noqa: E402

# The specification, written here and not read from the code: the eight classes and the gate's words.
NAMES = ("Concurrency and order", "Repeat and retry", "Time", "Partial failure", "Paths and permissions", "Bad or old input",
         "Outages and missing tools", "Size", "Encodings")
GATE = ("- [ ] Test contract: fill `## Test contract` below — a row per behavior, and each hostile class a row "
        "or a reason.")
FULL = ("feature", "build", "refactor", "ops", "bugfix", "cleanup", None)
OTHER = ("research", "audit", "eval", "light", "quick")
PLAN_BUILDERS = (template.plan_review_prompt, template.panel_plan_review_prompt)
IMPL_BUILDERS = (template.impl_review_prompt, template.panel_impl_review_prompt)
TASK = ".agent/tasks/007-example/task.md"


def _section(text: str) -> str:
    """The `## Test contract` section's body: from its heading to the next `## ` heading ("" when there is none)."""
    m = re.search(r"(?ms)^## Test contract\n(.*?)(?=^## |\Z)", text)
    return m.group(1) if m else ""


def _render(ty) -> str:
    return template.render_template(1, "X", ty)


def _reset_render_caches() -> None:
    """core builds two caches from the renders once per process; a test that patches the renders resets them."""
    core._TEMPLATE_COMMENTARY = None
    core._TEMPLATE_GATE_TITLES = None


class Render(unittest.TestCase):
    def test_full_types_render_the_contract(self):
        # row 1: one section, the table header and the eight classes, after the design gates, before the plan
        for ty in FULL:
            text = _render(ty)
            self.assertEqual(text.count("\n## Test contract\n"), 1, ty)
            sec = _section(text)
            self.assertEqual(sec.split("\n", 1)[0], "<!-- pin -->", ty)       # tier 1 in the shrink (plan panel P3)
            self.assertIn("| # | Kind | Behavior | Test that proves it |", sec, ty)
            for n in NAMES:                                                    # row 14: no examples in the task.md
                self.assertEqual(sec.count(f'\n- {n}: (the rows that cover it, or "does not apply: <why>")\n'), 1,
                                 (ty, n))
            self.assertEqual(len(re.findall(r"(?m)^- ", sec)), len(NAMES), ty)
            pos = text.index("\n## Test contract\n")
            fix = core.PLAYBOOKS.get(ty or "") == "Fix"
            self.assertLess(text.index("### Fix Orientation" if fix else "### Verify"), pos, ty)
            self.assertLess(pos, text.index("## Work Plan" if fix else "## Plan Review"), ty)

    def test_other_types_render_none(self):
        # row 2
        for ty in OTHER:
            self.assertNotIn("## Test contract", _render(ty), ty)

    def test_the_test_gate_is_the_contract_gate(self):
        # row 3: in the full types the design test gate is the contract gate; the report-shaped types keep theirs
        for ty in FULL:
            text = _render(ty)
            self.assertEqual(text.count(GATE), 1, ty)
            self.assertNotIn("- [ ] Test strategy", text, ty)
        for ty in ("research", "audit", "eval"):
            text = _render(ty)
            self.assertIn("- [ ] Test strategy", text, ty)
            self.assertNotIn(GATE, text, ty)

    def test_the_arena_switch_drops_the_contract(self):
        # row 13: the arena's `design_phase: off` drops the contract with the design phase; Fix has no such switch
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "eval.json")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"design_phase": "off"}, fh)
            with mock.patch.dict(os.environ, {"PLAYBOOK_EVAL_CONFIG": cfg}):
                self.assertNotIn("## Test contract", _render("feature"))
                self.assertNotIn("## Design Phase", _render("feature"))
                self.assertIn("## Test contract", _render("bugfix"))

    def test_no_contract_line_is_template_commentary(self):
        # row 10: the trim's first stage drops the template's `>` lines; a class line must never be one
        members = core.template_commentary_lines()
        for ty in FULL:
            sec = _section(_render(ty))
            self.assertTrue(sec, ty)
            for ln in sec.splitlines():
                self.assertFalse(ln.startswith(">"), (ty, ln))
                self.assertNotIn(ln, members, (ty, ln))


class Guide(unittest.TestCase):
    def test_the_guide_explains_the_contract(self):
        # row 4
        for ty in ("feature", "bugfix", None):
            g = template.guide_text(ty)
            self.assertIn("### Test contract\n", g, ty)
            sec = g.split("### Test contract\n", 1)[1].split("\n### ", 1)[0]
            for words in ("before any code", "does not apply: <why>", "watch"):
                self.assertIn(words, sec, (ty, words))
            for name, what in template.HOSTILE_CLASSES:                       # row 14: the examples live here
                self.assertIn(f"> - {name}: {what}.", sec, (ty, name))
            self.assertEqual(len(template.HOSTILE_CLASSES), len(NAMES))
        for ty in ("quick", "light", "research", "audit"):
            self.assertNotIn("### Test contract\n", template.guide_text(ty), ty)


class Prompts(unittest.TestCase):
    def test_the_four_prompts_carry_the_contract_lens(self):
        # row 5: the plan lens names every class; the impl test lens asks each row for its test
        for f in PLAN_BUILDERS:
            p = f(TASK)
            self.assertIn("(4) Test contract", p, f.__name__)
            self.assertNotIn("(4) Test coverage", p, f.__name__)
            for n in NAMES:
                self.assertIn(n, p, (f.__name__, n))
            self.assertIn("One defect is one finding: a sequence the hostile-sequences lens already raised is not raised again here.",
                          p, f.__name__)                                       # row 17 (plan panel P8)
            self.assertIn("a reason that holds is not", p, f.__name__)
        for f in IMPL_BUILDERS:
            p = f(TASK)
            self.assertIn("(5) Test quality", p, f.__name__)
            self.assertIn("Each row of the task's `## Test contract` needs its test in the change", p, f.__name__)

    def test_one_constant_feeds_render_and_prompts(self):
        # row 6: change the one constant and the render and both plan prompts follow it
        fake = (("Gremlins", "a gremlin in the wire"),) + tuple(template.HOSTILE_CLASSES[1:])
        self.addCleanup(_reset_render_caches)
        _reset_render_caches()                         # a cache built under the patch must not outlive it (plan P11)
        with mock.patch.object(template, "HOSTILE_CLASSES", fake):
            sec = _section(_render("feature"))
            self.assertIn("\n- Gremlins: (", sec)
            self.assertNotIn("- Concurrency and order", sec)
            self.assertIn("> - Gremlins: a gremlin in the wire.", template.guide_text("feature"))
            for f in PLAN_BUILDERS:
                p = f(TASK)
                self.assertIn("Gremlins", p, f.__name__)
                self.assertNotIn("Concurrency and order", p, f.__name__)

    def test_a_task_without_a_contract_is_judged_on_its_plan(self):
        # row 7: a light task or an old record has no contract; the lens says what the judge does then
        missing = ("A task with the `Test contract:` design gate but no `## Test contract` section is missing its "
                   "contract: that is a finding.")
        neither = ("With neither (a light, quick, research, audit or eval task, or a record from before the "
                   "contract), ")
        for f in PLAN_BUILDERS:
            p = f(TASK)
            self.assertIn(missing, p, f.__name__)
            self.assertIn(neither + "judge the test plan it has.", p, f.__name__)
        for f in IMPL_BUILDERS:
            p = f(TASK)
            self.assertIn(missing, p, f.__name__)
            self.assertIn(neither + "judge the tests against the Intent.", p, f.__name__)

    def test_the_delta_review_counts_only_the_rows_it_touched(self):
        # row 15 (plan panel P6): the post-panel judge reads a delta; a row it did not touch was the panel's
        clause = ("In this post-panel delta review, a contract row counts only if the delta added or changed it or "
                  "its test.")
        for f in IMPL_BUILDERS:
            self.assertIn(clause, f(TASK, delta=True), f.__name__)
            self.assertNotIn(clause, f(TASK), f.__name__)
        for f in PLAN_BUILDERS:
            self.assertNotIn(clause, f(TASK, delta=True), f.__name__)

    def test_every_hostile_sequence_has_its_class(self):
        # row 16 (plan panel P7): lens 3's six sequences, each named by a class of the contract
        sequences = {
            "two concurrent requests": "Concurrency and order",
            "events arriving reordered": "Concurrency and order",
            "the same logical event delivered twice under DISTINCT ids": "Repeat and retry",
            "a lost response causing the caller to retry": "Repeat and retry",
            "the external call succeeding while the local transaction rolls back": "Partial failure",
            "a crash after commit but before any post-commit step": "Partial failure",
        }
        names = [n for n, _ in template.HOSTILE_CLASSES]
        self.assertEqual(tuple(names), NAMES)
        for f in PLAN_BUILDERS:
            p = f(TASK)
            for words, cls in sequences.items():
                self.assertIn(words, p, (f.__name__, words))
                self.assertIn(cls, names, words)


def _record(contract: str, newer: int) -> str:
    """A task.md whose contract sits before a newer section of `newer` characters — the trim fills the room newest
    first, so without the contract among the kept headings the newer section takes the room and the contract goes."""
    unit = "body-of-notes "
    notes = "## Notes\n\n" + (unit * (newer // len(unit) + 1))[:newer] + "\n\n"
    return ("# 007 - Example Task\n\n## Status\npending\n\n## Risk\nassertive\n\n## Intent\nmake the thing\n\n"
            "## Why\nbecause\n\n" + contract + "## Work Plan\n\n- [ ] W1 step one\n\n---\n\n" + notes)


def _contract(heading: str = "") -> str:
    """The rendered section, filled: twelve rows and every class answered. `heading`: an old record's own, unpinned."""
    rows = "".join(f"| {i} | hostile | behavior {i} " + "b" * 150 + f" | test_row_{i} |\n" for i in range(1, 13))
    text = template.contract_section()
    text = re.sub(r"(?m)^\| 1 \| \(normal.*\n?", lambda _m: rows, text)
    for i, n in enumerate(NAMES):
        text = text.replace(f'- {n}: (the rows that cover it, or "does not apply: <why>")',
                            f"- {n}: does not apply — reason {i}")
    if heading:
        text = text.replace("## Test contract\n<!-- pin -->\n", heading + "\n")
    return text + "\n\n"


class Trim(unittest.TestCase):
    def _assert_kept(self, heading: str):
        contract = _contract(heading)
        text = _record(contract, newer=len(contract) - 200)
        out, receipt = core.select_task_context(text, len(contract) + 1_500)
        for i in range(1, 13):
            self.assertIn(f"test_row_{i} |", out, (heading, i, receipt))
        for n in NAMES:
            self.assertIn(f"- {n}: does not apply", out, (heading, n))
        self.assertNotIn("body-of-notes " * 20, out, receipt)       # the newer section is what gave way

    def test_the_trim_keeps_the_contract(self):
        # row 8 (a): over budget, the newer section goes and the contract stays whole
        self._assert_kept("")

    def test_the_shrink_cuts_unpinned_prose_before_the_contract(self):
        # row 8 (b), plan panel P3: when even the kept sections do not fit, an unpinned kept section's prose (tier 0)
        # is cut first; the rendered contract carries its pin (tier 1) and is sent whole
        contract = _contract()
        why = "## Why\n\n" + "".join(f"reason line {i} " + "w" * 120 + "\n" for i in range(60)) + "\n"
        text = ("# 007 - Example Task\n\n## Status\npending\n\n## Risk\nassertive\n\n## Intent\nmake the thing\n\n"
                + why + contract + "## Work Plan\n\n- [ ] W1 step one\n\n---\n")
        out, receipt = core.select_task_context(text, len(contract) + 2_500)
        self.assertNotIn("reason line 59 ", out, receipt)                     # the Why's prose was cut
        for i in range(1, 13):
            self.assertIn(f"test_row_{i} |", out, (i, receipt))
        for n in NAMES:
            self.assertIn(f"- {n}: does not apply", out, n)

    def test_the_trim_keeps_a_hand_written_contract(self):
        # row 9: an old record wrote the heading in another case, with no pin
        self._assert_kept("## Test Contract")


class Docs(unittest.TestCase):
    def test_the_cli_doc_names_the_plan_lenses(self):
        # row 12: docs/cli.md's lens list says what the prompts number
        doc = (REPO / "docs" / "cli.md").read_text(encoding="utf-8")
        line = next(ln for ln in doc.splitlines() if ln.startswith("A plan review runs six lenses"))
        self.assertIn("the test contract", line)
        self.assertNotIn("test coverage", line)


if __name__ == "__main__":
    unittest.main()
