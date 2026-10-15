"""The `reversible` risk class must ask whether the WORLD reverts, not the diff.

Field evidence (F11, StrataDB task 006 — third judge-driven reclassification):
the template defined `reversible` as "`git revert` undoes it completely", which
reads as *diff*-revertibility. An agent classified data-loss-class work as
`reversible` reasoning "the DIFF is git-revertible" while its own notes said
"blast radius is data-loss-class"; a judge had to correct it — three times
across the record. The operative question is whether the WORLD (persisted
data, on-disk formats, secrets, history, published claims) reverts.

These tests pin the corrected wording at both teaching sites: the `## Risk`
explanation and the CLAUDE.md template `init` seeds. The old bare definition is
asserted ABSENT — it is the misreading itself.

Task 213 (the owner, 2026-10-15: "Scris o dată, nu în fiecare fișă"): the
explanation moved, verbatim, from every rendered task.md into the single copy
`tasks guide` prints (and `tasks new` prints for the type); the `## Risk` block
of a task.md keeps its value and one line pointing there. The class below reads
the guide; it was `TestRiskBlockWording` (on a render) before the move.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK = REPO_ROOT / "plugins" / "playbook"
sys.path.insert(0, str(PLAYBOOK))

from tasks.template import guide_text, render_template  # noqa: E402

CLAUDE_MD_TEMPLATE = PLAYBOOK / "scripts" / "CLAUDE.md.template"

# The misreadable definitions this fix removes. If either resurfaces, the
# three-times-corrected misclassification comes back with it.
OLD_TEMPLATE_WORDING = "`git revert` undoes it completely"
OLD_CLAUDE_MD_WORDING = "(git revert undoes it)"

# What the world-vs-diff correction must state, at each site.
WORLD_QUESTION = "the WORLD reverts, not the diff"
# The concrete non-reversible triggers the definition must name, so the reader
# tests their change against the world, not against `git revert`.
EXCLUSION_TOKENS = ("data", "secret", "history", "claim")


class TestGuideRiskWording(unittest.TestCase):
    """The guide's Risk section — printed for every task type — teaches world-revertibility."""

    def _risk_block(self, task_type: str) -> str:
        content = guide_text(task_type)
        self.assertIn("### Risk\n", content)
        block = content.split("### Risk\n", 1)[1]
        # The section ends at the next one; keep only the Risk teaching text.
        return block.split("\n### ", 1)[0]

    def test_every_rendered_risk_block_points_to_it(self):
        for task_type in ("feature", "quick", "light"):
            with self.subTest(task_type=task_type):
                block = render_template(1, "wording probe", task_type).split("\n## Risk\n", 1)[1]
                block = block.split("\n## ", 1)[0]
                self.assertIn("`.claude/bin/tasks guide`", block)
                self.assertNotIn(WORLD_QUESTION, block)

    def test_reversible_asks_the_world_question(self):
        for task_type in ("feature", "quick"):
            with self.subTest(task_type=task_type):
                block = self._risk_block(task_type)
                self.assertIn(WORLD_QUESTION, block)

    def test_reversible_names_the_world_side_exclusions(self):
        for task_type in ("feature", "quick"):
            with self.subTest(task_type=task_type):
                block = self._risk_block(task_type).lower()
                for token in EXCLUSION_TOKENS:
                    self.assertIn(token, block)

    def test_old_diff_revertibility_wording_is_gone(self):
        for task_type in ("feature", "quick"):
            with self.subTest(task_type=task_type):
                block = self._risk_block(task_type)
                self.assertNotIn(OLD_TEMPLATE_WORDING, block)

    def test_irreversible_and_assertive_survive_the_reword(self):
        block = self._risk_block("feature")
        self.assertIn("`irreversible`", block)
        self.assertIn("`assertive`", block)
        self.assertIn("rollback plan", block)
        self.assertIn("claim about the world", block)


class TestClaudeMdTemplateWording(unittest.TestCase):
    """The CLAUDE.md `init` seeds must teach the same corrected definition."""

    def setUp(self):
        self.text = CLAUDE_MD_TEMPLATE.read_text(encoding="utf-8")

    def test_reversible_asks_the_world_question(self):
        self.assertIn("the WORLD reverts", self.text)

    def test_old_wording_is_gone(self):
        self.assertNotIn(OLD_CLAUDE_MD_WORDING, self.text)

    def test_exclusions_named(self):
        risk_line = next(
            line for line in self.text.splitlines() if "`## Risk`" in line
        )
        for token in EXCLUSION_TOKENS:
            self.assertIn(token, risk_line.lower())


if __name__ == "__main__":
    unittest.main()
