#!/usr/bin/env python3
"""Task 296 (stabilization audit 2, grave G5): a block after a resume keeps what an agent wrote in `## Blocked`.

`tasks blocked` on a task that had been blocked and resumed deleted the whole earlier `## Blocked` section and wrote
a fresh one (`core._compose_blocked`). Replacing the earlier reason and its `Resumed` stamp, the CLI's own blockquote
lines, is the design ("a new block starts a new record"). Deleting the lines an agent wrote in the section — a note,
a fenced block, a gate — is not: the ledger's PB-TASK-BLOCKED says a block records its reason "without touching
gates", and a deleted open gate makes the task look closer to closed (his clauses 3 and 4; his answer of 2026-10-23
23:33, task 295). Now those lines move into the new section, under the new reason.

Run: python3 tests/test_block_keeps_an_agents_lines.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins/playbook"
sys.path.insert(0, str(PLUGIN))
from tasks.core import (  # noqa: E402
    _block_reasons, _extract_block_reason, _extract_status, _gate_counts, resume_blocked_task, set_task_blocked,
)

TASK = """# 012 - Decide

## Status
pending

## Work Plan
- [x] G1: measure current p99
- [ ] G2: if p99 > 200ms rewrite the index; if under, cancel
"""
APPENDED = "- PARKED-ITEM-88: BOM files are out of scope here\n- [ ] Re-check the ordering test\n"


class _Task(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.project, True)
        td = self.project / ".agent" / "tasks" / "012-decide"
        td.mkdir(parents=True)
        self.tf = td / "task.md"
        self.tf.write_text(TASK, encoding="utf-8")

    def text(self):
        return self.tf.read_text(encoding="utf-8")

    def append(self, s):
        with open(self.tf, "a", encoding="utf-8") as f:
            f.write(s)


class AnAgentsLinesStay(_Task):
    def test_a_line_and_a_gate_written_in_the_section_stay(self):
        """Row 1: a parked line and a gate appended after `## Blocked` survive the next block after a resume, and the
        reason read is the new one."""
        set_task_blocked(self.tf, "first pause")
        self.append(APPENDED)
        before = _gate_counts(self.text())
        resume_blocked_task(self.tf)
        set_task_blocked(self.tf, "second pause")
        t = self.text()
        self.assertIn("- PARKED-ITEM-88: BOM files are out of scope here", t)
        self.assertIn("- [ ] Re-check the ordering test", t)
        self.assertEqual(_gate_counts(t), before)
        self.assertEqual(_extract_block_reason(self.tf), "second pause")
        self.assertEqual(_block_reasons(self.tf), ["second pause"])
        self.assertEqual(_extract_status(self.tf), "blocked")
        self.assertEqual(t.count("## Blocked"), 1)

    def test_a_fenced_block_in_the_section_stays_whole(self):
        """Row 2: a fenced block an agent quoted in the section, a `>` line inside it included, survives as it was."""
        set_task_blocked(self.tf, "first pause")
        self.append("```text\n> QUOTED-LINE-5 from the CI log\nexit 2\n```\n")
        resume_blocked_task(self.tf)
        set_task_blocked(self.tf, "second pause")
        self.assertIn("```text\n> QUOTED-LINE-5 from the CI log\nexit 2\n```", self.text())
        self.assertEqual(_block_reasons(self.tf), ["second pause"])

    def test_the_real_verbs_keep_the_gate(self):
        """Row 3: through the CLI — `tasks work`, `tasks blocked`, a gate appended, `tasks work`, `tasks blocked`."""
        home = self.project / "home"
        home.mkdir()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "C.UTF-8",
               "PYTHONPATH": str(PLUGIN), "PLAYBOOK_SESSION_ID": "pid-296", "PYTHONDONTWRITEBYTECODE": "1"}

        def tasks(*args):
            r = subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project, env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (args, r.stdout, r.stderr))

        tasks("work", "012")
        tasks("blocked", "waiting on the owner")
        self.append("- [ ] GATE-41: re-run the import once he answers\n")
        tasks("work", "012")
        tasks("blocked", "waiting on CI")
        self.assertIn("- [ ] GATE-41: re-run the import once he answers", self.text())
        self.assertEqual(_extract_block_reason(self.tf), "waiting on CI")


class TheDesignStays(_Task):
    def test_the_earlier_reason_is_still_replaced(self):
        """Row 4: after a resume the earlier reason and its stamp are replaced, as designed (task 140 appends only on
        a task that is already blocked)."""
        set_task_blocked(self.tf, "FIRST-PAUSE")
        resume_blocked_task(self.tf)
        set_task_blocked(self.tf, "SECOND-PAUSE")
        t = self.text()
        self.assertNotIn("FIRST-PAUSE", t)
        self.assertNotIn("> Resumed", t)
        self.assertEqual(_block_reasons(self.tf), ["SECOND-PAUSE"])

    def test_an_agents_quote_stays(self):
        """Row 6 (edited after impl round 1, grok): only the CLI's two line shapes are replaced — `> … (since …)` and
        `> Resumed …`; a quote an agent wrote, indented or not, stays, and the reason read first is the new one."""
        set_task_blocked(self.tf, "first pause")
        self.append("> QUOTE-7: he said the column stays\n  > an indented quote\n")
        resume_blocked_task(self.tf)
        set_task_blocked(self.tf, "second pause")
        t = self.text()
        self.assertIn("> QUOTE-7: he said the column stays", t)
        self.assertIn("  > an indented quote", t)
        self.assertNotIn("first pause", t)
        self.assertNotIn("> Resumed", t)
        self.assertEqual(_extract_block_reason(self.tf), "second pause")


    def test_with_nothing_but_the_clis_lines_the_bytes_are_as_before(self):
        """Row 5: a section holding only the CLI's lines comes out byte for byte as before the fix."""
        set_task_blocked(self.tf, "one")
        resume_blocked_task(self.tf)
        set_task_blocked(self.tf, "two")
        norm = re.sub(r"20\d\d-\d\d-\d\dT[0-9:+\-]+", "TS", self.text())
        self.assertEqual(norm, TASK.replace("pending", "blocked") + "\n## Blocked\n> two  (since TS)\n\n")


class NothingMovesPastAnotherSection(_Task):
    def test_a_fence_in_a_later_section_does_not_swallow_the_block(self):
        """Row 7 (impl round 1: opus, codex, grok): a fenced block in the old `## Blocked` and an unclosed fence in a
        later section — after the next block the later section's open gate is still counted, the new reason is read,
        and the fenced block is still there (moved past the stray opener, its closer used to close it)."""
        set_task_blocked(self.tf, "first pause")
        self.append("```text\nLOG-LINE-3\n```\n\n## Notes\n```\n- [ ] GATE-9: a gate below a stray fence\n")
        before = _gate_counts(self.text())
        resume_blocked_task(self.tf)
        set_task_blocked(self.tf, "second pause")
        t = self.text()
        self.assertEqual(_gate_counts(t), before)
        self.assertEqual(_extract_block_reason(self.tf), "second pause")
        self.assertIn("```text\nLOG-LINE-3\n```", t)

    def test_the_section_stays_where_it_was(self):
        """Row 8 (impl round 1: sonnet, grok): with an agent's lines in it the section keeps its place — a section
        after it stays after it, so the order of the gates does not change."""
        set_task_blocked(self.tf, "first pause")
        self.append("- [ ] GATE-1: in the block section\n\n## Notes\n- [ ] GATE-2: in a later section\n")
        resume_blocked_task(self.tf)
        set_task_blocked(self.tf, "second pause")
        t = self.text()
        self.assertLess(t.index("## Blocked"), t.index("## Notes"))
        self.assertLess(t.index("GATE-1"), t.index("GATE-2"))
        self.assertEqual(t.count("## Blocked"), 1)

    def test_two_blocked_sections_both_lose_their_old_records(self):
        """Row 9 (my mutant run after impl round 1): a file with two `## Blocked` sections (by hand, or an older
        version) — after the next block neither keeps its old reason, an agent's gates in both stay, and the reason
        read is the new one."""
        self.tf.write_text(TASK + "\n## Blocked\n> OLD-A  (since 2026-01-01T00:00+00:00)\n- [ ] GATE-A\n\n## Notes\nx\n"
                           "\n## Blocked\n> OLD-B  (since 2026-01-02T00:00+00:00)\n- [ ] GATE-B\n", encoding="utf-8")
        before = _gate_counts(self.text())
        set_task_blocked(self.tf, "new pause")
        t = self.text()
        self.assertNotIn("OLD-A", t)
        self.assertNotIn("OLD-B", t)
        self.assertIn("- [ ] GATE-A", t)
        self.assertIn("- [ ] GATE-B", t)
        self.assertEqual(_gate_counts(t), before)
        self.assertEqual(_extract_block_reason(self.tf), "new pause")


if __name__ == "__main__":
    unittest.main()
