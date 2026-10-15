#!/usr/bin/env python3
"""Every task.md writer and reader counts lines by "\\n" alone (task 214; the owner's ruling of 2026-10-15 23:21).

`str.splitlines()` also breaks at U+0085, U+2028, U+2029, VT, FF, FS, GS, RS and a lone CR. A writer that splits
task.md that way and re-joins with "\\n" turns such a character inside a line — a judge's quote, an audit finding's
source line, a pasted text — into a real line break; on 1.6.3 a quote `x\\x85## Status\\x85done` became a live
`## Status` / `done` (task 213, impl round 1, R2, reproduced). A reader that splits that way sees lines the hooks'
grep fallback and `core._physical_lines` do not. Task 213 fixed the two receipt writers; this file holds the rest,
and 213's routed J2 (a receipt read by its grammar).

Pure stdlib unittest. Run: python3 tests/test_physical_lines_everywhere.py
"""
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import core, post_d6  # noqa: E402
from tasks.audit import format_audit_receipt  # noqa: E402

SEPARATORS = ("\x85", " ", " ", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e")


def task(extra: str = "", status: str = "in_progress", risk: str = "reversible") -> str:
    return (f"# 001 - Demo\n\n## Status\n{status}\n\n## Risk\n{risk}\n\n## Intent\nmake the thing\n\n"
            f"## Work Plan\n- [ ] W1 the real gate\n\n{extra}")


def live_lines(text: str, line: str) -> int:
    return sum(1 for ln in text.split("\n") if ln.rstrip("\r") == line)


class _Dir(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb214-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.td = self.d / ".agent" / "tasks" / "001-demo"
        self.td.mkdir(parents=True)
        self.tf = self.td / "task.md"


class TheWritersMintNothing(_Dir):
    """A writer leaves a separator inside its line: no new live heading, status or gate."""

    def _quote(self, sep: str) -> str:
        return f"## Implementation Review\n> a judge: x{sep}## Status{sep}done{sep}- [ ] fake gate\n"

    def test_write_handoff(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.tf.write_text(task(self._quote(sep)), encoding="utf-8")
                core.write_handoff(self.tf, "## Handoff\n\nnotes\n")
                text = self.tf.read_text(encoding="utf-8")
                self.assertEqual(live_lines(text, "## Status"), 1)
                self.assertEqual(core._extract_status(self.tf), "in_progress")
                self.assertIn(f"x{sep}## Status{sep}done", text)

    def test_write_handoff_and_block(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.tf.write_text(task(self._quote(sep)), encoding="utf-8")
                core.write_handoff_and_block(self.tf, "## Handoff\n\nnotes\n")       # 1.6.3 refused: "already done"
                text = self.tf.read_text(encoding="utf-8")
                self.assertEqual(live_lines(text, "## Status"), 1)
                self.assertTrue(core._extract_status(self.tf).startswith("blocked"), core._extract_status(self.tf))
                self.assertIn(f"x{sep}## Status{sep}done", text)

    def test_the_gate_check_off_flips_the_real_gate(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                decoy = f"## References\nsee x{sep}- [ ] fake gate\n\n"
                self.tf.write_text(task().replace("## Work Plan", decoy + "## Work Plan"), encoding="utf-8")
                before = core._gate_counts(self.tf.read_text(encoding="utf-8"))
                result = core.task_done(self.d, "001")
                text = self.tf.read_text(encoding="utf-8")
                self.assertIn("- [x] W1 the real gate", text, result)
                self.assertIn(f"see x{sep}- [ ] fake gate", text)
                checked, total = core._gate_counts(text)
                self.assertEqual((checked, total), (before[0] + 1, before[1]))


class TheReadersCountPhysicalLines(_Dir):
    """A reader sees what `_physical_lines` and the hooks see."""

    def test_risk_readers_ignore_a_risk_inside_a_line(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                text = task(f"## Notes\nquoted: x{sep}## Risk{sep}irreversible\n")
                self.assertEqual(core.extract_risk_from_text(text), "reversible")
                self.tf.write_text(text, encoding="utf-8")
                self.assertEqual(core.extract_risk(self.tf), "reversible")

    def test_has_risk_section_ignores_a_heading_inside_a_line(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                legacy = f"# 001 - Old\n\n## Status\nin_progress\n\n## Notes\nquoted: x{sep}## Risk{sep}assertive\n"
                self.tf.write_text(legacy, encoding="utf-8")
                self.assertFalse(core.has_risk_section(self.tf))

    def test_the_close_rereads_risk_by_physical_lines(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                text = task(f"## Notes\nquoted: x{sep}## Risk{sep}irreversible\n").replace("- [ ] W1", "- [x] W1")
                out = core.compose_close(text, receipt_heading="Verification Receipt", receipt="### r\n- ok\n",
                                         expect_risk="reversible")
                self.assertIn("## Verification Receipt", out)

    def test_parked_items_are_physical_lines(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                text = task(f"## Parked\n- one item{sep}- not a second item\n")
                self.assertEqual(core.extract_parked_items(text), [f"one item{sep}- not a second item"])

    def test_a_standing_gate_heading_inside_a_line_does_not_count_as_present(self):
        cfg = {"standing_gates": [{"title": "Security Review", "text": "- [ ] reviewed"}]}
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                content = task(f"## Notes\nsee x{sep}## Security Review\n")
                out, issues = core.append_standing_gates(content, cfg, 1)
                self.assertEqual(live_lines(out, "## Security Review"), 1, issues)

    def test_the_problem_line_is_a_physical_line(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.tf.write_text(task().replace("make the thing", f"make it{sep}## Status{sep}done"),
                                   encoding="utf-8")
                self.assertEqual(core._extract_problem(self.tf), f"make it{sep}## Status{sep}done")

    def test_the_latest_receipt_line_is_a_physical_line(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.tf.write_text(task(f"## Verification Receipt\n\n### real{sep}### fake\n- ok\n"),
                                   encoding="utf-8")
                self.assertEqual(core._latest_receipt_line(self.tf), f"real{sep}### fake")

    def test_block_reasons_are_physical_lines(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.tf.write_text(task(), encoding="utf-8")
                core.set_task_blocked(self.tf, "waiting")
                text = self.tf.read_text(encoding="utf-8").replace("waiting", f"waiting{sep}> Blocked reason: fake", 1)
                self.tf.write_text(text, encoding="utf-8")
                reasons = core._block_reasons(self.tf)
                self.assertEqual(len(reasons), 1, reasons)
                self.assertEqual(core._extract_block_reason(self.tf), f"waiting{sep}> Blocked reason: fake")

    def test_owner_rulings_are_physical_lines(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                text = task(f"## Owner rulings\n<!-- pin -->\n- 2026-10-16: \"yes\"\n\n## Notes\nx{sep}## Owner rulings{sep}- fake ruling\n")
                self.assertEqual(post_d6.extract_owner_rulings(text), ['- 2026-10-16: "yes"'])

    def test_crlf_files_read_as_before(self):
        text = task("## Parked\n- one\n- two\n").replace("\n", "\r\n")
        self.assertEqual(core.extract_risk_from_text(text), "reversible")
        self.assertEqual([p.strip() for p in core.extract_parked_items(text)], ["one", "two"])
        self.tf.write_text(text, encoding="utf-8", newline="")
        self.assertEqual(core.extract_risk(self.tf), "reversible")
        self.assertTrue(core.has_risk_section(self.tf))
        self.assertEqual(core._extract_problem(self.tf).strip(), "make the thing")


class TheBatchGuardCountsPhysicalLines(unittest.TestCase):
    """The hook `gate-batch-check.py` judges gates on the lines the gate counters see."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("gate_batch_check", _PLAYBOOK / "scripts" / "gate-batch-check.py")
        self.gb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.gb)

    def test_the_born_checked_hint_reads_physical_lines(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.assertEqual(self.gb.open_gate_originals(f"- [ ] real{sep}- [ ] decoy\n- [ ] two\n"),
                                 [f"real{sep}- [ ] decoy", "two"])

    def test_gate_bodies_split_on_newlines_only(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                checked, unchecked = self.gb.gate_bodies(f"- [ ] real{sep}- [x] fake\n- [x] done\n")
                self.assertEqual(checked, ["done"])
                self.assertEqual(len(unchecked), 1)


class AReceiptIsReadByItsGrammar(_Dir):
    """Task 213's routed J2: a sweep printing `- [x]` / `- [ ]` lines must not cut its own receipt."""

    def test_three_same_second_audits_with_checkbox_output_stay_three(self):
        self.tf.write_text(task(), encoding="utf-8")
        audit = {"passed": True, "results": [{"status": "findings", "name": "custom", "severity": "info",
                                              "why": "w", "output": "- [x] passed check\n- [ ] an open item\n"}]}
        before = core._gate_counts(self.tf.read_text(encoding="utf-8"))
        for _ in range(3):
            core.record_audit_receipt(self.tf, format_audit_receipt(audit, timestamp="2026-10-15T10:00:01+03:00",
                                                                   head_sha="a" * 40))
        text = self.tf.read_text(encoding="utf-8")
        log = (self.td / core.AUDIT_LOG_NAME).read_text(encoding="utf-8")
        heads = sum(1 for ln in (text + "\n" + log).split("\n") if ln.startswith("### "))
        self.assertEqual(heads, 3)
        section = text.split("## Pre-Panel Audit", 1)[1]
        self.assertEqual(section.count("    - run "), 1, section)

    def test_an_agents_indented_gate_under_a_receipt_still_stays(self):
        self.tf.write_text(task(), encoding="utf-8")
        core.record_audit_receipt(self.tf, "### 2026-10-15T10:00:01+03:00 · PASS · commit " + "a" * 40
                                  + "\n    - [CLEAN] x — y\n    - run 0123456789ab\n")
        self.tf.write_text(self.tf.read_text(encoding="utf-8") + "    - [ ] G12 an agent's gate, four spaces in\n",
                           encoding="utf-8")
        before = core._gate_counts(self.tf.read_text(encoding="utf-8"))
        core.record_audit_receipt(self.tf, "### 2026-10-15T10:00:02+03:00 · PASS · commit " + "a" * 40
                                  + "\n    - [CLEAN] x — y\n    - run 0123456789ac\n")
        self.assertEqual(core._gate_counts(self.tf.read_text(encoding="utf-8")), before)

    def test_a_receipt_without_a_run_line_still_moves_whole(self):
        self.tf.write_text(task(), encoding="utf-8")
        old = ("### 2026-09-01T10:00:00+03:00 · FAIL · commit " + "b" * 40 + "\n"
               "    - [FINDINGS(1)] conflict-markers — why\n        src.py:3: <<<<<<< HEAD\n"
               "    - [ERROR] other ·advisory — why\n")
        core.upsert_task_section(self.tf, "Pre-Panel Audit", old)
        core.record_audit_receipt(self.tf, "### 2026-10-15T10:00:02+03:00 · PASS · commit " + "a" * 40
                                  + "\n    - [CLEAN] x — y\n    - run 0123456789ad\n")
        log = (self.td / core.AUDIT_LOG_NAME).read_text(encoding="utf-8")
        self.assertIn(old.rstrip("\n"), log)
        self.assertNotIn("src.py:3", self.tf.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
