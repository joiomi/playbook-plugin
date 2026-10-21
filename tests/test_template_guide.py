#!/usr/bin/env python3
"""The template's instruction text is written once, not into every task.md (PHASE-B.md item 2, task 213).

The owner, 2026-10-15: "Scris o dată, nu în fiecare fișă" — the `>` instructions, the Risk explanation and the
Standing Orders move to one copy (`tasks guide`, printed by `tasks new`); a task.md keeps one pointer line at the
top and, under `## Risk`, its value and one pointer line. Measured before: that text was 6 % (this workspace) /
4 % (a second project) of a task.md (p90 17 % / 13 %), read by no hook and no reader.

`tests/fixtures/template_before_213/` holds the text as the templates wrote it before the move (rendered from
the tree at nested c2f04c8): every line of it must be in the guide, and none of it in a new task.md.

Pure stdlib unittest. Run: python3 tests/test_template_guide.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import template  # noqa: E402
from tasks.core import PLAYBOOKS, extract_risk, extract_risk_from_text, has_risk_section  # noqa: E402

BEFORE = _HERE / "fixtures" / "template_before_213"
# Lines of the old templates that a later task REWORDED in the guide, old -> the new lines that say it (the guide
# holds the new ones; a record written before keeps the old line — one stale line, not worth code that recognises it:
# the owner's rule of 2026-10-16, "design for new projects, not legacy"). Task 219 moved the drift signal to the
# 10-call reminder; task 225 put a gate's note on the lines under its title; task 226 split each line into short
# lines of one fact (the owner, 2026-10-16 09:36: "Liste scurte de la început").
REWORDED = {
    '> **Gate discipline:** One gate → do work → check box → next gate.': (
        '> **Gate discipline:**',
        '> - One gate → do the work → check the box → the next gate.',
    ),
    '> Never backfill. The document IS the execution trace. Closing several ALREADY-DONE gates in one edit is allowed ONLY when each line carries its own outcome note (hook-enforced, max 5; a bare batch tick is blocked; where no hook runs, keep to one gate at a time).': (
        '> - Never backfill: the document IS the execution trace.',
        '> - Closing several ALREADY-DONE gates in one edit: only when each gate carries its own outcome note (hook-enforced, at most 5; a bare batch tick is blocked; where no hook runs, one gate at a time).',
    ),
    '> **Never born checked:** write each gate BEFORE you do its step, then check it — a gate may never be born checked. To record work you already finished, add the gate unchecked, then check it in a SEPARATE edit with its outcome note.': (
        '> - Never born checked: write each gate BEFORE you do its step, then check it.',
        '> - Work you already finished: add its gate unchecked, then check it in a SEPARATE edit, with its outcome.',
    ),
    '> **Closing a gate:** check the box, append your outcome. Never replace the original text.': (
        '> - Closing a gate: check the box and, in the same edit, write its outcome on the lines under it — indented two spaces deeper than the gate, one fact per line (`- [x] <the gate>`, then `  - 283 tests green`).',
        "> - Never replace a gate's original text.",
    ),
    '> **Sanctioned compaction (the one exception):** when this file grows past the review context budget, old *Plan Review / Implementation Review triage narrative* may move VERBATIM to `task-archive.md` (same dir). Never gates, never Intent/Design/Parked/receipts — moving history is not deleting it. Do it mechanically: wrap each cold block in `<!-- archive:start -->` … `<!-- archive:end -->` then run `.claude/bin/tasks compact <N>` (it refuses to move a gate, a pin, or a protected heading, so a mismark fails loud).': (
        '> **Sanctioned compaction (the one exception):**',
        '> - When this file grows past the review context budget, old Plan Review / Implementation Review triage narrative may move VERBATIM to `task-archive.md` (same dir).',
        '> - Never gates, never Intent/Design/Parked/receipts — moving history is not deleting it.',
        '> - Do it mechanically: wrap each cold block in `<!-- archive:start -->` … `<!-- archive:end -->`, then run `.claude/bin/tasks compact <N>`.',
        '> - It refuses to move a gate, a pin or a protected heading, so a mismark fails loud.',
    ),
    '> Design Phase = orientation (one gate, brief answer). Work Plan = real work (one gate, full effort).': (
        '> - Design Phase = orientation (one gate, a brief answer); Work Plan = real work (one gate, full effort).',
    ),
    "> If you see the same gate 5+ times in the hook echo, you're drifting — STOP and update.": (
        "> - If the hook's reminder (one line every 10 calls on the same gate) names the same gate a third time, you're drifting — STOP and update.",
    ),
    "> **Before filling this in:** run `.claude/bin/tasks work <N>` to activate this task. Hooks won't enforce until activated.": (
        '> **Before filling this in:**',
        '> - Run `.claude/bin/tasks work <N>` to activate this task.',
        "> - Hooks won't enforce until it is activated.",
    ),
    '> - `reversible` — the question is whether the WORLD reverts, not the diff: touches only code/config that `git revert` fully undoes. A change that deletes/migrates persisted data, alters an on-disk format, rotates a secret, rewrites history, or publishes/asserts a claim about the world is NOT reversible even when its diff reverts cleanly. Normal bar.': (
        '> - `reversible`: the question is whether the WORLD reverts, not the diff.',
        '>   - It touches only code/config that `git revert` fully undoes; the normal bar.',
        '>   - NOT reversible even when its diff reverts cleanly: deleting or migrating persisted data, changing an on-disk format, rotating a secret, rewriting history, publishing or asserting a claim about the world.',
    ),
    '> - `irreversible` — deletes/migrates data, rotates a secret, rewrites history, or publishes. Needs a named rollback plan + explicit confirmation, and cannot light-close.': (
        '> - `irreversible`: deletes/migrates data, rotates a secret, rewrites history, or publishes.',
        '>   - It needs a named rollback plan and explicit confirmation, and cannot light-close.',
    ),
    '> - `assertive` — changes a **claim about the world** (docs, a calibration, a measurement, a "verified accurate"). Reviewed for the claim AND its instrument regardless of diff size — a docs-only diff can be the most review-worthy thing a task produces. Cannot light-close.': (
        '> - `assertive`: changes a **claim about the world** (docs, a calibration, a measurement, a "verified accurate").',
        ">   - It is reviewed for the claim AND its instrument whatever the diff's size — a docs-only diff can be the most review-worthy thing a task produces.",
        '>   - It cannot light-close.',
    ),
    '> - Leaving it `unclassified` is **not** the cheap way out: an unset gate cannot be evaluated, so the close is held to the same bar as `assertive`/`irreversible` (review evidence, or `--force --reason`). One word here is the whole cost.': (
        '> - Leaving it `unclassified` is **not** the cheap way out: an unset gate cannot be evaluated.',
        '>   - The close is then held to the same bar as `assertive`/`irreversible` (review evidence, or `--force --reason`); one word here is the whole cost.',
    ),
    '> **Write a 1-sentence answer for each gate.** A bare checkmark means you skipped it.': (
        '> - **Answer each gate on the lines under it** (indented two spaces deeper, one fact per line).',
        '> - A bare checkmark means you skipped it.',
    ),
    '> Complete these gates before writing the work plan.': (
        '> - Complete these gates before writing the work plan.',
    ),
    '> (The `/playbook` skill has workflow patterns if you need a reference.)': (
        '> - The `/playbook` skill has workflow patterns if you need a reference.',
    ),
    '> For each work section: what could go wrong? How will you know it worked? (specific check, not "looks good")': (
        '> - For each work section: what could go wrong, and how will you know it worked (a specific check, not "looks good")?',
    ),
    '> Standard feature: 6-8 work gates + tests. Large tasks work fine — if >15 gates, add a mid-point checkpoint to reassess direction.': (
        '> - A standard feature: 6-8 work gates + tests.',
        '> - Large tasks work fine — past 15 gates, add a mid-point checkpoint to reassess direction.',
    ),
    '> Never backfill. The document IS the execution trace. Batch-closing ALREADY-DONE gates needs an outcome note per line (hook-enforced; where no hook runs, one gate at a time).': (
        '> - One gate → do the work → check the box → the next gate.',
        '> - Never backfill: the document IS the execution trace.',
        "> - A gate's outcome goes on the lines under it, indented two spaces deeper, one fact per line.",
        '> - Batch-closing ALREADY-DONE gates needs an outcome note per gate (hook-enforced; where no hook runs, one gate at a time).',
    ),
    '> **Never born checked:** write the gate BEFORE you do its step, then check it — a gate may never be born checked. To record work you already finished, add it unchecked, then check it in a SEPARATE edit with its outcome.': (
        '> - Never born checked: write the gate BEFORE you do its step, then check it.',
        '> - Work you already finished: add its gate unchecked, then check it in a SEPARATE edit, with its outcome.',
    ),
    '> **Light shape** — small, sub-day work; ceremony compressed, review NEVER waived: risk decides review, smallness never does. If this grows beyond small, convert: `tasks new <type> <name>`.': (
        '> **Light shape** — small, sub-day work:',
        '> - Ceremony compressed, review NEVER waived: risk decides review, smallness never does.',
        '> - If this grows beyond small, convert: `tasks new <type> <name>`.',
    ),
    '> Gate discipline unchanged: one gate → do work → check box with its outcome. Never backfill. Batch-closing ALREADY-DONE gates needs an outcome note per line (hook-enforced; where no hook runs, one at a time).': (
        '> - Gate discipline unchanged: one gate → do the work → check the box.',
        "> - A gate's outcome goes on the lines under it, indented two spaces deeper, one fact per line.",
        '> - Never backfill; batch-closing ALREADY-DONE gates needs an outcome note per gate (hook-enforced; where no hook runs, one at a time).',
    ),
    '> Fix/Verify pairs. What could this break?': (
        '> - Fix / Verify pairs.',
        '> - What could this break?',
    ),
    '> Apply lenses consistently. Assess first, decide action after.': (
        '> - Apply the lenses consistently.',
        '> - Assess first, decide the action after.',
    ),
    '> Rounds: hypothesis → test → result → checkpoint. Stop when converging.': (
        '> - Rounds: hypothesis → test → result → checkpoint.',
        '> - Stop when converging.',
    ),
}
MOVED = [ln for ln in (BEFORE / "commentary.txt").read_text(encoding="utf-8").splitlines()
         if ln and ln not in REWORDED]
MOVED_ORDERS = [ln for ln in (BEFORE / "standing_orders.txt").read_text(encoding="utf-8").splitlines() if ln]
TYPES = [None, "quick", "light", *PLAYBOOKS]
OLD_RENDERS = {"default": None, "quick": "quick", "light": "light", "bugfix": "bugfix",
               "research": "research", "audit": "audit"}
POINTER_RE = re.compile(r"^> .*`\.claude/bin/tasks guide")
GATE_RE = re.compile(r"^\s*- \[[ xX]\]")
PLACEHOLDER_RE = re.compile(r"^\(.*\)$")


def _without_contract(lines: "list[str]") -> "list[str]":
    """A render's lines without its `## Test contract` section (task 278), from that heading to the next `## `."""
    out, inside = [], False
    for ln in lines:
        if ln.startswith("## "):
            inside = ln == "## Test contract"
        if not inside:
            out.append(ln)
    return out


def _risk_block(text: str) -> "list[str]":
    lines = text.splitlines()
    i = lines.index("## Risk")
    out = []
    for ln in lines[i + 1:]:
        if ln.startswith("## ") or ln == "---":
            break
        out.append(ln)
    while out and not out[-1].strip():
        out.pop()
    return out


class TheGuideHoldsEveryMovedLine(unittest.TestCase):
    """Q6: nothing the templates said is lost — it is in the single copy, verbatim."""

    def test_every_moved_line_is_in_the_guide(self):
        guide = template.guide_text().splitlines()
        for ln in MOVED + MOVED_ORDERS:
            with self.subTest(line=ln[:60]):
                self.assertIn(ln, guide)
        for old, new in REWORDED.items():
            with self.subTest(reworded=old[:60]):
                self.assertNotIn(old, guide)
                for ln in new:
                    self.assertIn(ln, guide)

    def test_one_fact_per_line(self):
        # task 226: a guide line says one thing — the longest new line is 209 characters; the *GATE_NOTES
        # tables (what a long gate title used to say, quoted verbatim under its short title) are the exception
        tables = {f"> {said}" for notes in (template.GATE_NOTES, template.RETRO_GATE_NOTES,
                                            template.FREEHAND_GATE_NOTES) for _title, said in notes}
        for ty in (None, "quick", "light", "retro", "freehand", *PLAYBOOKS):
            for ln in template.guide_text(ty).splitlines():
                if ln in tables:
                    continue
                with self.subTest(type=ty, line=ln[:60]):
                    self.assertLessEqual(len(ln), 240)

    def test_a_type_guide_carries_its_own_shape(self):
        quick, light, feature = (template.guide_text(t) for t in ("quick", "light", "feature"))
        self.assertIn("**Selection rule:** quick is for", quick)
        self.assertNotIn("Light shape", quick)
        self.assertIn("**Light shape**", light)
        self.assertIn("**Sanctioned compaction", feature)
        for text in (quick, light, feature):
            self.assertIn("the WORLD reverts, not the diff", text)
        self.assertIn("**Expand dynamically**", feature)

    def test_an_unknown_type_gets_the_whole_guide(self):
        self.assertEqual(template.guide_text("sp-eval"), template.guide_text())


class ANewTaskMdCarriesNoneOfIt(unittest.TestCase):
    """Q6: a render of every type — no moved line, one pointer at the top, Risk = value + one pointer."""

    def test_no_moved_line_in_any_render(self):
        for ty in TYPES:
            text = template.render_template(1, "X", ty).splitlines()
            for ln in MOVED + MOVED_ORDERS:
                with self.subTest(type=ty, line=ln[:60]):
                    self.assertNotIn(ln, text)
            self.assertNotIn("## Standing Orders", text)

    def test_one_pointer_at_the_top(self):
        for ty in TYPES:
            lines = template.render_template(1, "X", ty).splitlines()
            with self.subTest(type=ty):
                self.assertTrue(POINTER_RE.match(lines[2]), lines[:4])
                self.assertEqual(sum(bool(POINTER_RE.match(ln)) for ln in lines[:lines.index("## Status")]), 1)
                self.assertIn(f"tasks guide {ty or 'feature'}`", lines[2])

    def test_risk_is_its_value_and_one_pointer(self):
        for ty in TYPES:
            block = _risk_block(template.render_template(1, "X", ty))
            with self.subTest(type=ty):
                self.assertEqual(block[0], "unclassified")
                rest = [ln for ln in block[1:] if ln.strip()]
                self.assertEqual(len(rest), 1, block)
                self.assertTrue(rest[0].startswith("> "), rest)
                self.assertIn("tasks guide", rest[0])
                for word in ("`reversible`", "`irreversible`", "`assertive`"):
                    self.assertIn(word, rest[0])

    def test_every_gate_heading_and_placeholder_stays(self):
        # Every heading and placeholder in its place, and a gate at every place a gate was; task 223 shortened the
        # gates' titles (`tests/test_short_gate_titles.py` maps each old title to its new one).
        # Task 278 ADDED one section, the test contract (`tests/test_test_contract.py`); the rest is compared as before.
        for name, ty in OLD_RENDERS.items():
            old = (BEFORE / f"render-{name}.md").read_text(encoding="utf-8").splitlines()
            new = _without_contract(template.render_template(1, "X", ty).splitlines())
            kept = lambda ls: ["- [ ] <gate>" if GATE_RE.match(ln) else ln for ln in ls
                               if GATE_RE.match(ln) or PLACEHOLDER_RE.match(ln)
                               or (ln.startswith("#") and ln != "## Standing Orders")]
            with self.subTest(type=name):
                self.assertEqual(kept(new), kept(old))

    def test_the_stub_and_the_retro_scaffold_risk(self):
        stub = template.render_stub_template(1, "X", "", "feature")
        block = _risk_block(stub)
        self.assertEqual(block[0], "unclassified")
        self.assertEqual(len([ln for ln in block[1:] if ln.strip()]), 1, block)
        self.assertEqual(template.risk().splitlines()[:2], ["## Risk", "unclassified"])
        for ln in MOVED:
            self.assertNotIn(ln, template.risk().splitlines())

    def test_the_arena_switch_turns_the_pointer_off(self):
        d = Path(tempfile.mkdtemp(prefix="pb213arena-"))
        self.addCleanup(shutil.rmtree, d, True)
        cfg = d / "eval.json"
        cfg.write_text(json.dumps({"sticker": "off"}), encoding="utf-8")
        old = os.environ.get("PLAYBOOK_EVAL_CONFIG")
        os.environ["PLAYBOOK_EVAL_CONFIG"] = str(cfg)
        try:
            off = template.render_template(1, "X", "feature").splitlines()
        finally:
            if old is None:
                os.environ.pop("PLAYBOOK_EVAL_CONFIG", None)
            else:
                os.environ["PLAYBOOK_EVAL_CONFIG"] = old
        on = template.render_template(1, "X", "feature").splitlines()
        self.assertEqual([ln for ln in on if ln not in off], [on[2]])      # the switch removes the pointer alone
        self.assertTrue(POINTER_RE.match(on[2]), on[:4])


class TheRiskValueIsStillRead(unittest.TestCase):
    """Q7: the pointer line under the value changes nothing for the readers."""

    def test_extract_risk_reads_the_value_above_the_pointer(self):
        for ty in TYPES:
            text = template.render_template(1, "X", ty)
            with self.subTest(type=ty):
                self.assertEqual(extract_risk_from_text(text), "unclassified")
                for value in ("reversible", "irreversible", "assertive"):
                    set_ = text.replace("## Risk\nunclassified\n", f"## Risk\n{value}\n", 1)
                    self.assertEqual(extract_risk_from_text(set_), value)

    def test_has_risk_section_and_extract_risk_on_a_file(self):
        d = Path(tempfile.mkdtemp(prefix="pb213risk-"))
        self.addCleanup(shutil.rmtree, d, True)
        tf = d / "task.md"
        tf.write_text(template.render_template(1, "X", "light").replace(
            "## Risk\nunclassified\n", "## Risk\nassertive\n", 1), encoding="utf-8")
        self.assertTrue(has_risk_section(tf))
        self.assertEqual(extract_risk(tf), "assertive")


class OldRecordsAreStillTrimmed(unittest.TestCase):
    """Q8 (P11): 212's trim still drops the template text a task.md written BEFORE the move carries — the lines the
    guide still holds verbatim (`MOVED`, `MOVED_ORDERS`); a line a later task REWORDED stays in an old record."""

    def _over_budget(self, text: str) -> "tuple[str, str]":
        from tasks.core import select_task_context
        return select_task_context(text + "\n## Notes\n\n" + "n" * 30_000 + "\n", 8_000)

    def test_an_old_record_loses_its_instructions_and_standing_orders_and_no_gate(self):
        old = (BEFORE / "render-default.md").read_text(encoding="utf-8")
        out, receipt = self._over_budget(old)
        lines = out.splitlines()
        for ln in MOVED + MOVED_ORDERS:
            with self.subTest(line=ln[:60]):
                self.assertNotIn(ln, lines)
        self.assertIn("template text not sent:", receipt)
        kept = old.split("## Design Phase", 1)[1].split("## Debrief", 1)[0]
        for gate in (ln for ln in kept.splitlines() if GATE_RE.match(ln)):
            with self.subTest(gate=gate[:60]):
                self.assertIn(gate, lines)

    def test_the_new_pointers_are_droppable(self):
        for ty in ("feature", "light", "quick"):
            new = template.render_template(1, "X", ty)
            out, _ = self._over_budget(new)
            with self.subTest(type=ty):
                self.assertNotIn(template.pointer(ty), out.splitlines())
                self.assertIn("## Risk\nunclassified\n", out)
                self.assertFalse(any("`.claude/bin/tasks guide` (Risk)" in ln for ln in out.splitlines()), out[:600])


def _project() -> "tuple[Path, dict]":
    d = Path(tempfile.mkdtemp(prefix="pb213cli-"))
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    (d / ".agent" / "tasks").mkdir(parents=True)
    home = d / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
    env.update({"PYTHONPATH": str(_PLAYBOOK), "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
    return d, env


class ThroughTheCli(unittest.TestCase):
    """Q6: `tasks new` prints the guide for every type; `tasks guide` prints it on demand."""

    def setUp(self):
        self.d, self.env = _project()
        self.addCleanup(shutil.rmtree, self.d, True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.d, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_tasks_new_prints_the_guide_for_every_type(self):
        for ty in ("quick", "light", "feature", "bugfix", "research", "audit"):
            r = self.run_cli("new", ty, f"probe {ty}")
            with self.subTest(type=ty):
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("=== TASK GUIDE", r.stdout)
                self.assertIn(template.guide_text(ty), r.stdout)

    def test_tasks_guide_prints_the_whole_copy(self):
        r = self.run_cli("guide")
        self.assertEqual(r.returncode, 0, r.stderr)
        for ln in MOVED + MOVED_ORDERS + [n for new in REWORDED.values() for n in new]:
            self.assertIn(ln, r.stdout.splitlines())

    def test_tasks_guide_of_a_type_and_wrong_usage(self):
        r = self.run_cli("guide", "light")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(template.guide_text("light"), r.stdout)
        r = self.run_cli("guide", "light", "quick")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_a_stub_prints_the_guide_when_it_is_expanded(self):
        r = self.run_cli("new", "--stub", "bugfix", "later")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("=== TASK GUIDE", r.stdout)
        r = self.run_cli("work", "1")
        self.assertIn("Expanded stub", r.stdout, r.stdout + r.stderr)
        self.assertIn(template.guide_text("bugfix"), r.stdout)


if __name__ == "__main__":
    unittest.main()
