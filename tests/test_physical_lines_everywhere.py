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

SEPARATORS = ("\x85", "\u2028", "\u2029", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\r")   # the lone CR: T4


def task(extra: str = "", status: str = "in_progress", risk: str = "reversible") -> str:
    return (f"# 001 - Demo\n\n## Status\n{status}\n\n## Risk\n{risk}\n\n## Intent\nmake the thing\n\n"
            f"## Work Plan\n- [ ] W1 the real gate\n\n{extra}")


def raw(path: Path) -> str:
    """The file as written — no newline translation, so a lone CR stays inside its line (T4)."""
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


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
                text = raw(self.tf)
                self.assertEqual(live_lines(text, "## Status"), 1)
                self.assertEqual(core._extract_status(self.tf), "in_progress")
                self.assertIn(f"x{sep}## Status{sep}done", text)

    def test_write_handoff_and_block(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.tf.write_text(task(self._quote(sep)), encoding="utf-8")
                core.write_handoff_and_block(self.tf, "## Handoff\n\nnotes\n")       # 1.6.3 refused: "already done"
                text = raw(self.tf)
                self.assertEqual(live_lines(text, "## Status"), 1)
                self.assertTrue(core._extract_status(self.tf).startswith("blocked"), core._extract_status(self.tf))
                self.assertIn(f"x{sep}## Status{sep}done", text)

    def test_the_gate_check_off_flips_the_real_gate(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                decoy = f"## References\nsee x{sep}- [ ] fake gate\n\n"
                self.tf.write_text(task().replace("## Work Plan", decoy + "## Work Plan"), encoding="utf-8")
                before = core._gate_counts(raw(self.tf))
                result = core.task_done(self.d, "001")
                text = raw(self.tf)
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


class CrlfFilesReadAndWriteAsBefore(_Dir):
    """Impl round 1, T7 (gemini, grok — reproduced by grok): physical lines keep a `\r`, so a CRLF
    `## Owner rulings\r` no longer matched and the SETTLED block lost every ruling."""

    def test_owner_rulings_of_a_crlf_text(self):
        text = task("## Owner rulings\n<!-- pin -->\n- 2026-10-16: \"yes\"\n").replace("\n", "\r\n")
        self.assertEqual(post_d6.extract_owner_rulings(text), ['- 2026-10-16: "yes"'])

    def test_the_born_checked_hint_of_a_crlf_text(self):
        spec = importlib.util.spec_from_file_location("gate_batch_check", _PLAYBOOK / "scripts" / "gate-batch-check.py")
        gb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gb)
        self.assertEqual(gb.open_gate_originals("- [ ] one\r\n- [ ] two\r\n"), ["one", "two"])

    def test_a_writer_leaves_a_crlf_file_with_one_line_ending(self):
        self.tf.write_text(task().replace("\n", "\r\n"), encoding="utf-8", newline="")
        core.write_handoff(self.tf, "## Handoff\n\nnotes\n")
        text = raw(self.tf)
        self.assertNotIn("\r", text)
        self.assertEqual(core._extract_status(self.tf), "in_progress")


class ASeparatorAtTheStartOfALine(_Dir):
    """Impl round 1, T6 (opus, sonnet, grok): `str.strip()` removes U+0085, U+2028, VT, FF, FS-RS and CR, so a
    physical line that STARTS with one passed for a gate or a heading where the counters and the hooks see none."""

    def test_the_check_off_flips_the_real_gate(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                body = task().replace("## Work Plan\n", f"## Work Plan\n{sep}- [ ] fake gate\n")
                self.tf.write_text(body, encoding="utf-8", newline="")
                core.task_done(self.d, "001")
                text = raw(self.tf)
                self.assertIn("- [x] W1 the real gate", text)
                self.assertIn(f"{sep}- [ ] fake gate", text)

    def test_a_heading_after_a_separator_is_not_a_present_standing_gate(self):
        cfg = {"standing_gates": [{"title": "Security Review", "text": "- [ ] reviewed"}]}
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                out, issues = core.append_standing_gates(task(f"## Notes\n{sep}## Security Review\n"), cfg, 1)
                self.assertEqual(live_lines(out, "## Security Review"), 1, issues)

    def test_the_batch_guard_counts_no_gate_after_a_separator(self):
        spec = importlib.util.spec_from_file_location("gate_batch_check", _PLAYBOOK / "scripts" / "gate-batch-check.py")
        gb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gb)
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                checked, unchecked = gb.gate_bodies(f"{sep}- [x] fake\n- [ ] real\n")
                self.assertEqual((checked, unchecked), ([], ["real"]))


class TheOtherReadersAndCompact(_Dir):
    """Impl round 1, T5: the judges' trim, `split_md_sections`, the verify-contract drift sweep and `tasks compact`
    split task.md with `splitlines(keepends=True)` / `splitlines()` — my inventory had searched `splitlines()` only."""

    def test_the_trim_keeps_every_gate_of_the_work_plan(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                gates = "".join(f"- [x] G{i} step {i} — {'n' * 1500}{' x' + sep + '## Fake heading' if i == 2 else ''}\n"
                                for i in range(1, 7))
                text = (f"# 007 - T\n\n## Status\nin_progress\n\n## Risk\nreversible\n\n## Intent\ndo it\n\n"
                        f"## Work Plan\n{gates}\n## Notes\n{'n' * 30_000}\n")
                out, _receipt = core.select_task_context(text, 6_000)
                for i in range(1, 7):
                    self.assertIn(f"- [x] G{i} step {i}", out)

    def test_split_md_sections_reads_physical_lines(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                sections = core.split_md_sections(f"## Notes\nsee x{sep}## Fake\nmore\n")
                self.assertEqual([h for h, _ in sections], ["Notes"])

    def test_the_drift_sweep_sees_every_recorded_command(self):
        from tasks import audit
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.tf.write_text(task("## Verification Receipt\n\n### 2026-10-15T10:00:00+03:00 · risk reversible · commit abc\n"
                                        f"> source: x{sep}## Notes\n- [PASS] `python3 -m unittest`\n"),
                                   encoding="utf-8", newline="")
                recorded, _ts = audit._recorded_verify_commands(self.d)
                self.assertEqual(recorded.get("reversible"), {"python3 -m unittest"}, recorded)

    def test_compact_never_cuts_a_physical_line(self):
        import subprocess
        import os
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                body = (task() + "\n## Plan Review\n<!-- archive:start -->\nold narrative\n"
                        f"<!-- archive:end -->{sep}tail on the same line\n")
                self.tf.write_text(body, encoding="utf-8", newline="")
                env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
                env.update({"PYTHONPATH": str(_PLAYBOOK), "HOME": str(self.d / "home"), "PYTHONDONTWRITEBYTECODE": "1"})
                (self.d / "home").mkdir(exist_ok=True)
                subprocess.run(["git", "init", "-q"], cwd=self.d, check=False)
                subprocess.run([sys.executable, "-m", "tasks.cli", "compact", "1"], cwd=self.d, env=env,
                               capture_output=True, text=True, timeout=60)
                after = raw(self.tf)
                archive = self.td / "task-archive.md"
                archived = raw(archive) if archive.exists() else ""
                whole = f"<!-- archive:end -->{sep}tail on the same line"
                self.assertTrue(whole in after or whole in archived,
                                f"the physical line was cut: task.md={after[-120:]!r} archive={archived[-120:]!r}")
                if archive.exists():
                    archive.unlink()


class ALoneCarriageReturnIsNotALineBreak(_Dir):
    """Impl round 1, T4 (codex, pre-existing on 1.6.3): `read_text()` translated a lone CR into a newline, so a quote
    `x\r## Status\rdone` read as a live `done` in the status reader and a rewrite stored it as real lines."""

    QUOTE = "## Notes\n> q: x\r## Status\rdone\r- [ ] fake gate\n"

    def test_the_readers(self):
        self.tf.write_text(task(self.QUOTE), encoding="utf-8", newline="")
        self.assertEqual(core._extract_status(self.tf), "in_progress")
        self.assertEqual(core._extract_progress(self.tf), "0/1")
        self.assertEqual(core.extract_risk(self.tf), "reversible")

    def test_the_status_hook(self):
        import subprocess
        self.tf.write_text(task(self.QUOTE), encoding="utf-8", newline="")
        r = subprocess.run([sys.executable, str(_PLAYBOOK / "scripts" / "task-status.py"), str(self.tf)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[0].strip(), "in_progress", r.stdout)

    def test_a_rewrite_keeps_it_inside_its_line(self):
        self.tf.write_text(task(self.QUOTE), encoding="utf-8", newline="")
        core.set_task_blocked(self.tf, "waiting")
        with open(self.tf, encoding="utf-8", newline="") as fh:
            raw = fh.read()
        self.assertIn("> q: x\r## Status\rdone", raw)
        self.assertTrue(core._extract_status(self.tf).startswith("blocked"))

    def test_crlf_is_still_normalised_by_a_rewrite(self):
        self.tf.write_text(task().replace("\n", "\r\n"), encoding="utf-8", newline="")
        core.set_task_blocked(self.tf, "waiting")
        with open(self.tf, encoding="utf-8", newline="") as fh:
            raw = fh.read()
        self.assertNotIn("\r", raw)


class ReviewEvidenceIsAPhysicalGate(_Dir):
    """Impl round 1, T3 (gemini): a quote carrying a checked review gate after a separator made
    `has_review_evidence` True with no review — the close's bar for assertive / irreversible work."""

    def test_a_review_gate_inside_a_line_is_no_evidence(self):
        for sep in SEPARATORS:
            for where in ("inside", "at the start"):
                with self.subTest(sep=repr(sep), where=where):
                    gate = "- [x] Run `.claude/bin/tasks impl-review 1` — done"
                    line = f"> q: x{sep}{gate}" if where == "inside" else f"{sep}{gate}"
                    self.tf.write_text(task(f"## Notes\n{line}\n", risk="assertive"), encoding="utf-8", newline="")
                    self.assertFalse(core.has_review_evidence(self.tf, impl_only=True))

    def test_a_real_review_gate_still_counts(self):
        self.tf.write_text(task("## Implementation Review\n- [x] Run `.claude/bin/tasks impl-review 1` — done\n"),
                           encoding="utf-8")
        self.assertTrue(core.has_review_evidence(self.tf, impl_only=True))


class TheCodexHookReadsTheLiveStatus(_Dir):
    """Impl round 1, T2 (grok Critical): `provider/codex_hooks._task_status` read `done` from a quote, an 8-space
    finding pair or a fenced example — the codex Stop decision then skipped the gate check."""

    def test_only_the_live_status_counts(self):
        from provider import codex_hooks
        decoys = {
            "a separator in a quote": "## Notes\n> q: x\x85## Status\x85done\n",
            "a lone CR in a quote": "## Notes\n> q: x\r## Status\rdone\n",
            "an 8-space pair": "## Pre-Panel Audit\n\n### t · PASS · commit a\n        ## Status\n        done\n",
            "a fenced example": "## Notes\n```text\n## Status\ndone\n```\n",
        }
        for name, extra in decoys.items():
            with self.subTest(decoy=name):
                self.tf.write_text(task(extra), encoding="utf-8", newline="")
                self.assertEqual(codex_hooks._task_status(self.tf), "in_progress")
                self.assertFalse(codex_hooks._task_status_is_done(self.tf))

    def test_crlf_status_still_read(self):
        from provider import codex_hooks
        self.tf.write_text(task().replace("\n", "\r\n"), encoding="utf-8", newline="")
        self.assertEqual(codex_hooks._task_status(self.tf), "in_progress")


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
        self.assertEqual(core._gate_counts(text), before)            # a finding line never reads as a gate (T1)
        section = text.split("## Pre-Panel Audit", 1)[1]
        self.assertEqual(section.count("    - run "), 1, section)

    def test_an_eight_space_gate_after_a_receipt_stays(self):                       # impl round 1, T1
        self.tf.write_text(task(), encoding="utf-8")
        core.record_audit_receipt(self.tf, "### 2026-10-15T10:00:01+03:00 · PASS · commit " + "a" * 40
                                  + "\n    - [CLEAN] x — y\n    - run 0123456789ab\n")
        self.tf.write_text(self.tf.read_text(encoding="utf-8") + "\n        - [ ] G12 eight spaces in\n",
                           encoding="utf-8")
        before = core._gate_counts(self.tf.read_text(encoding="utf-8"))
        core.record_audit_receipt(self.tf, "### 2026-10-15T10:00:02+03:00 · PASS · commit " + "a" * 40
                                  + "\n    - [CLEAN] x — y\n    - run 0123456789ac\n")
        self.assertEqual(core._gate_counts(self.tf.read_text(encoding="utf-8")), before)
        self.assertNotIn("G12", (self.td / core.AUDIT_LOG_NAME).read_text(encoding="utf-8"))

    def test_a_gate_shaped_finding_line_is_escaped(self):                          # impl round 1, T1
        audit = {"passed": True, "results": [{"status": "findings", "name": "custom", "severity": "info",
                                              "why": "w", "output": "- [x] passed\n  - [ ] open\n"}]}
        receipt = format_audit_receipt(audit, timestamp="2026-10-15T10:00:01+03:00", head_sha="a" * 40)
        self.assertEqual(core._gate_counts(receipt), (0, 0), receipt)
        self.assertIn("passed", receipt)

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

    LEGACY = ("### 2026-09-01T10:00:00+03:00 · PASS · commit " + "c" * 40 + "\n    - [CLEAN] conflict-markers — why\n")
    NEW = "### 2026-10-15T10:00:09+03:00 · PASS · commit " + "a" * 40 + "\n    - [CLEAN] x — y\n    - run 0123456789ff\n"

    def _after_move(self, receipt_and_tail: str) -> "tuple[str, str]":
        self.tf.write_text(task() + "\n## Pre-Panel Audit\n\n" + receipt_and_tail, encoding="utf-8")
        core.record_audit_receipt(self.tf, self.NEW)
        return self.tf.read_text(encoding="utf-8"), (self.td / core.AUDIT_LOG_NAME).read_text(encoding="utf-8")

    def test_the_run_line_ends_a_receipt(self):                                    # K8: M19 alone
        text, log = self._after_move(self.NEW.replace("0123456789ff", "0123456789aa") + "\n        an agent's indented note\n")
        self.assertIn("an agent's indented note", text)
        self.assertNotIn("an agent's indented note", log)

    def test_an_eight_space_gate_right_under_an_old_receipt_stays(self):           # K8: M18 alone (no run line)
        before = core._gate_counts(task() + "\n        - [ ] G13 eight spaces\n")
        text, log = self._after_move(self.LEGACY + "        - [ ] G13 eight spaces\n")
        self.assertEqual(core._gate_counts(text), before)
        self.assertNotIn("G13", log)

    def test_an_indented_line_off_the_grammar_is_not_a_receipt(self):             # K8: M17 alone
        text, log = self._after_move(self.LEGACY + "   a three-space note\n")
        self.assertIn("a three-space note", text)
        self.assertNotIn("a three-space note", log)

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
