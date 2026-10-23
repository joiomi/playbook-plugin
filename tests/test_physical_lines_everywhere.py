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
        # task 224 (impl round 2): `extract_risk_from_text` takes the text as `read_task_text` returns it — CRLF folded
        # once (folding again is not idempotent) — and a CR left in it is a separator, so the buffer is folded here
        self.assertEqual(core.extract_risk_from_text(text.replace("\r\n", "\n")), "reversible")
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

    def test_a_write_cannot_launder_gates_through_a_lone_cr_quote(self):          # impl round 2, U4
        import json
        import subprocess
        d = Path(tempfile.mkdtemp(prefix="pb214gb-"))
        self.addCleanup(shutil.rmtree, d, True)
        tf = d / ".agent" / "tasks" / "001-t" / "task.md"
        tf.parent.mkdir(parents=True)
        names = "ABCDEF"
        quote = "> q: x" + "".join(f"\r- [x] {n} done" for n in names) + "\n"
        tf.write_text(task("## Notes\n" + quote), encoding="utf-8", newline="")
        new = task("## Notes\n" + quote + "".join(f"- [x] {n} done\n" for n in names))
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(tf), "content": new}}
        r = subprocess.run([sys.executable, str(_PLAYBOOK / "scripts" / "gate-batch-check.py"), "--tool", "Write",
                            "--file", str(tf), "--session-dir", str(d / "nope")],
                           input=json.dumps(payload).encode(), capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout.decode(errors="replace")[:400])

    def test_gate_bodies_split_on_newlines_only(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                checked, unchecked = self.gb.gate_bodies(f"- [ ] real{sep}- [x] fake\n- [x] done\n")
                self.assertEqual(checked, ["done"])
                self.assertEqual(len(unchecked), 1)


class ASeparatorOnlyLineIsNotBlank(_Dir):
    """Impl round 2, U1 (grok Critical): `str.strip()` made a separator-only line blank, so `## Status` / `\x85` /
    `done` read as `done` in the core status reader — the stop hook then skipped the gate check."""

    def test_the_status_reader(self):
        for sep in SEPARATORS[:-1]:                      # not the lone CR: `\r\n` is a CRLF blank line
            with self.subTest(sep=repr(sep)):
                self.tf.write_text(f"# 001 - D\n\n## Status\n{sep}\ndone\n\n## Work Plan\n- [ ] W1\n",
                                   encoding="utf-8", newline="")
                self.assertNotEqual(core._extract_status(self.tf), "done")

    def test_a_real_blank_line_is_still_skipped(self):
        self.tf.write_text("# 001 - D\n\n## Status\n \t\nin_progress\n\n## Work Plan\n- [ ] W1\n", encoding="utf-8")
        self.assertEqual(core._extract_status(self.tf), "in_progress")


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

    def test_a_heading_needs_a_space_or_a_tab(self):
        """Impl round 2, U6 (grok): `^#{1,2}\\s` took `##\\x85Phantom` at the start of a line for a section — `\\s` is
        every Unicode space and the lone CR; an ATX heading's `#`s are followed by a space, a tab or the line end."""
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.assertEqual([h for h, _ in core.split_md_sections(f"## Notes\nx\n##{sep}Fake\nmore\n")], ["Notes"])
                gates = "".join(f"- [x] G{i} step {i} — {'n' * 1500}\n" + (f"##{sep}Phantom\n" if i == 3 else "")
                                for i in range(1, 7))
                text = (f"# 007 - T\n\n## Status\nin_progress\n\n## Risk\nreversible\n\n## Intent\ndo it\n\n"
                        f"## Work Plan\n{gates}\n## Notes\n{'n' * 30_000}\n")
                out, _receipt = core.select_task_context(text, 6_000)
                for i in range(1, 7):
                    self.assertIn(f"- [x] G{i} step {i}", out)
        # the headings that are headings (pins): a space, a tab, an empty `##` before the line end, CRLF
        for head in ("## Notes\n", "##\tNotes\n", "##\n", "## Notes\r\n", "##\r\n"):
            with self.subTest(head=repr(head)):
                self.assertEqual(len(core.split_md_sections(f"intro\n{head}")), 2)

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

    def test_the_merge_renumber_keeps_it_inside_its_line(self):
        """L5: `prepare-merge` rewrites every task.md that names a renumbered task; it read through `read_text()`."""
        from tasks import merge_prep
        self.tf.write_text(task("## Notes\nsee task 133\n" + self.QUOTE), encoding="utf-8", newline="")
        merge_prep._rewrite_task_refs(self.d, self.d / ".agent", {133: 135})
        text = raw(self.tf)
        self.assertIn("see task 135", text)
        self.assertIn("> q: x\r## Status\rdone\r- [ ] fake gate", text)
        self.assertEqual(core._extract_status(self.tf), "in_progress")


class TheCliReadsLikeItsWriters(unittest.TestCase):
    """Impl round 2, U5 (opus, sonnet, codex, grok): K4 left the close, the SETTLED block, the judges' payloads, the
    review write-back, freehand and the stub expansion reading task.md WITH newline translation, against the
    untranslated `atomic.rewrite` — a finished task with a lone CR in a quote could not close, even with `--force`."""

    QUOTE = "## Notes\n> q: x\r- [ ] y\r## Owner rulings\r- fake ruling\n"

    def setUp(self):
        self.proj = Path(tempfile.mkdtemp(prefix="pb214cli-")) / "proj"
        (self.proj / ".agent" / "tasks").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.proj.parent, True)

    def run_cli(self, *args):
        import os
        import subprocess
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update({"PYTHONPATH": str(_PLAYBOOK), "PLAYBOOK_SESSION_ID": "pid-214-cli", "HOME": str(self.proj.parent),
                    "PYTHONDONTWRITEBYTECODE": "1"})
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.proj, env=env,
                              capture_output=True, text=True, timeout=180)

    def make_task(self, extra: str) -> Path:
        r = self.run_cli("new", "light", "small change")
        self.assertEqual(r.returncode, 0, r.stderr)
        tf = next((self.proj / ".agent" / "tasks").glob("001-*/task.md"))
        text = tf.read_text(encoding="utf-8").replace("## Risk\nunclassified", "## Risk\nreversible", 1)
        tf.write_text(text.replace("- [ ]", "- [x]") + "\n" + extra, encoding="utf-8", newline="")
        return tf

    def test_a_finished_task_with_a_lone_cr_quote_closes(self):
        for force in (False, True):
            with self.subTest(force=force):
                self.proj = Path(tempfile.mkdtemp(prefix="pb214cli-")) / "proj"      # a fresh project each time
                (self.proj / ".agent" / "tasks").mkdir(parents=True)
                self.addCleanup(shutil.rmtree, self.proj.parent, True)
                tf = self.make_task(self.QUOTE)
                self.assertEqual(self.run_cli("work", "1").returncode, 0)
                args = ["work", "done"] + (["--force", "--reason", "test"] if force else [])
                r = self.run_cli(*args)
                self.assertEqual(r.returncode, 0, r.stdout[-600:] + r.stderr[-600:])
                self.assertTrue(core._extract_status(tf).startswith("done"))

    def test_the_settled_block_keeps_the_real_rulings(self):
        tf = self.make_task("## Owner rulings\n<!-- pin -->\n- real ruling\n\n" + self.QUOTE)
        block = post_d6.settled_block(tf)
        self.assertIn("real ruling", block)
        self.assertNotIn("fake ruling", block)

    def test_a_stub_with_a_lone_cr_expands(self):
        r = self.run_cli("new", "--stub", "bugfix", "later")
        self.assertEqual(r.returncode, 0, r.stderr)
        tf = next((self.proj / ".agent" / "tasks").glob("001-*/task.md"))
        tf.write_text(tf.read_text(encoding="utf-8").replace("## Why\n", "## Why\nbecause x\ry\n", 1),
                      encoding="utf-8", newline="")
        r = self.run_cli("work", "1")
        self.assertIn("Expanded stub", r.stdout, r.stdout[-400:] + r.stderr[-400:])

    def test_the_close_and_the_handoff_refuse_a_status_hidden_behind_a_lone_cr(self):     # L8: mutants N8, N14
        """Their preflights read the status as the writers do: `## Status\rin_progress` is one line and no heading, so
        both refuse before anything is written (read with translation, they passed and the writer refused later)."""
        import re
        for verb in (("work", "done"), ("handoff",)):
            with self.subTest(verb=verb):
                self.proj = Path(tempfile.mkdtemp(prefix="pb214cli-")) / "proj"
                (self.proj / ".agent" / "tasks").mkdir(parents=True)
                self.addCleanup(shutil.rmtree, self.proj.parent, True)
                tf = self.make_task("")
                self.assertEqual(self.run_cli("work", "1").returncode, 0)
                text, n = re.subn(r"^## Status\n(\S+)$", r"## Status\r\1", raw(tf), count=1, flags=re.M)
                self.assertEqual(n, 1)
                tf.write_text(text, encoding="utf-8", newline="")
                r = self.run_cli(*verb)
                self.assertNotEqual(r.returncode, 0, r.stdout[-400:])
                self.assertIn("no live `## Status` heading", r.stderr)
                self.assertEqual(raw(tf), text)

    def test_the_close_counts_a_parked_item_by_its_physical_line(self):                     # L8: mutant N11
        self.make_task("## Parked\n- P1 one item\r- P2 inside the same line\n")
        self.assertEqual(self.run_cli("work", "1").returncode, 0)
        r = self.run_cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout[-600:] + r.stderr[-600:])
        self.assertIn("1 open parked item(s)", r.stdout)

    def test_freehand_log_finds_the_gate_on_a_physical_line(self):                  # impl round 3, V14 (N15)
        """`freehand log` checks for its `- [ ] Freehand log` gate before the locked insert: both read untranslated,
        so a quote `x\r- [ ] Freehand log` is no gate to either — it neither receives the messages nor stands in for
        a real gate that is gone."""
        from datetime import datetime, timezone
        tf = self.make_task("")
        self.assertEqual(self.run_cli("work", "1").returncode, 0)
        self.assertEqual(self.run_cli("freehand").returncode, 0)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        (self.proj / ".agent" / "chat_log.md").write_text(
            f"# Project Chat Log\n\n---\n\n**[M001]** [{now}] `HOST` (claude/pid-214-cli)\n\nthe owner's words\n",
            encoding="utf-8")
        quote = "> q: x\r- [ ] Freehand log (a quote)\n"
        text = raw(tf)
        self.assertEqual(text.count("\n- [ ] Freehand log"), 1, text[-800:])
        text = text + "\n## Notes\n" + quote
        no_gate = text.replace("\n- [ ] Freehand log", "\n- [x] Freehand log", 1)
        tf.write_text(no_gate, encoding="utf-8", newline="")
        r = self.run_cli("freehand", "log")
        self.assertNotEqual(r.returncode, 0, r.stdout[-300:])
        self.assertIn("no '- [ ] Freehand log' gate found", r.stderr)
        self.assertEqual(raw(tf), no_gate)
        tf.write_text(text, encoding="utf-8", newline="")
        r = self.run_cli("freehand", "log")
        self.assertEqual(r.returncode, 0, r.stdout[-300:] + r.stderr[-300:])
        after = raw(tf)
        self.assertIn(quote, after)
        self.assertGreater(after.index("the owner's words"), after.index("\n- [ ] Freehand log"))
        self.assertLess(after.index("the owner's words"), after.index(quote))

    def test_freehand_on_a_task_with_a_lone_cr(self):
        tf = self.make_task(self.QUOTE)
        self.assertEqual(self.run_cli("work", "1").returncode, 0)
        r = self.run_cli("freehand")
        self.assertNotIn("changed while the freehand block was being composed", r.stderr, r.stderr[-400:])
        self.assertIn("### Freehand", tf.read_text(encoding="utf-8"))


class TheReviewWriteBackAndTheFenceScanner(_Dir):
    """L8 (mutants N18, N2): the review findings writer rewrites the whole task.md; the fence scanner's blank line."""

    def test_the_review_write_back_keeps_a_lone_cr_inside_its_line(self):
        from tasks import review
        quote = "## Notes\n> q: x\r## Status\rdone\r- [ ] fake gate\n"
        self.tf.write_text(task("## Implementation Review\n(implementation review findings appear here)\n\n" + quote),
                           encoding="utf-8", newline="")
        self.assertIsNone(review._write_review_findings_locked(self.tf, "impl", "a finding"))
        text = raw(self.tf)
        self.assertIn("a finding", text)
        self.assertIn("> q: x\r## Status\rdone\r- [ ] fake gate", text)
        self.assertEqual(core._extract_status(self.tf), "in_progress")

    def test_a_separator_only_line_does_not_open_an_indented_code_block(self):
        # CommonMark: a blank line holds spaces and tabs only; after any other line a 4-space line continues the
        # paragraph — it is not code, so `has_review_evidence` and the other indented-code readers see it as text
        for sep in SEPARATORS:
            if sep == "\r":
                continue                                     # "\r" alone is a CRLF blank line's ending
            with self.subTest(sep=repr(sep)):
                flags = core._iter_fenced_flags(["para", sep, "    - [x] indented"], unclosed_is_live=False)
                self.assertEqual(flags, [False, False, False])
        for blank in ("", " \t", "\r"):
            with self.subTest(blank=repr(blank)):
                flags = core._iter_fenced_flags(["para", blank, "    - [x] indented"], unclosed_is_live=False)
                self.assertEqual(flags[2], True)


class TheRetroReadsPhysicalLines(unittest.TestCase):
    """Impl round 3, V12 (opus): `retro._parse_task` split task.md with `splitlines()` — a quote made it list an
    in-progress task as a done loose end, and a parked bullet holding a separator became two retro gates."""

    def test_its_task_parser(self):
        from tasks import retro
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                content = task(f"## Parked\n- one item{sep}- inside the same line\n\n"
                               f"## Notes\n> q: x{sep}## Status{sep}done{sep}- [x] fake\n{sep}- [ ] at the start\n")
                r = retro._parse_task(1, "demo", content)
                self.assertEqual(r["status"], "in_progress")
                self.assertEqual(r["parked_items"], [f"one item{sep}- inside the same line"])
                self.assertEqual((r["checked_count"], r["gate_count"]), core._gate_counts(content))


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

    def test_a_fenced_review_gate_is_no_evidence(self):                          # impl round 2, U3
        for fence in ("```text\n{g}\n```\n", "~~~\n{g}\n~~~\n", "```\n{g}\n"):     # closed, tilde, unclosed
            with self.subTest(fence=fence[:4]):
                body = fence.format(g="- [x] Run `.claude/bin/tasks impl-review 1` — done")
                self.tf.write_text(task(f"## Notes\n{body}", risk="assertive"), encoding="utf-8")
                self.assertFalse(core.has_review_evidence(self.tf, impl_only=True))

    def test_a_real_review_gate_is_no_evidence_either(self):
        # task 290 (audit 1 G2): since then no tick is evidence, a real gate's included — only the CLI's records
        self.tf.write_text(task("## Implementation Review\n- [x] Run `.claude/bin/tasks impl-review 1` — done\n"),
                           encoding="utf-8")
        self.assertFalse(core.has_review_evidence(self.tf, impl_only=True))


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

    def test_it_reads_what_core_reads(self):                                     # impl round 2, U2
        from provider import codex_hooks
        cases = {
            "a backtick info string with a backtick": task("```x`y\n\n## Status\nblocked\n"),
            "a 4-space done": "# 001 - D\n\n## Status\n    done\n\n## Work Plan\n- [ ] W1\n",
            "a tab done": "# 001 - D\n\n## Status\n\tdone\n\n## Work Plan\n- [ ] W1\n",
            "a separator-only line": "# 001 - D\n\n## Status\n\x85\ndone\n\n## Work Plan\n- [ ] W1\n",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                self.tf.write_text(text, encoding="utf-8", newline="")
                core_value = core._extract_status(self.tf)
                self.assertEqual(codex_hooks._task_status(self.tf), core_value)
                self.assertEqual(codex_hooks._task_status_is_done(self.tf), core_value.startswith("done"))

    def test_its_gate_count_is_cores(self):                                      # impl round 2, U2
        from provider import codex_hooks
        self.tf.write_text(task("## Notes\nx\x85- [ ] fake\n```text\n- [ ] fenced\n```\n"), encoding="utf-8", newline="")
        checked, total = core._gate_counts(raw(self.tf))
        done, n, first = codex_hooks._scan_gates(self.tf)
        self.assertEqual((done, n), (checked, total))
        self.assertEqual(first, "W1 the real gate")

    def test_the_mirror_used_by_the_codex_hook_scripts_reads_core_too(self):    # impl round 2, U2
        import subprocess
        self.tf.write_text(task("```x`y\n\n## Status\nblocked\n"), encoding="utf-8")
        code = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                "from provider import codex_hooks; print(codex_hooks._task_status(Path(sys.argv[2])))")
        r = subprocess.run([sys.executable, "-I", "-c", code, str(_PLAYBOOK / "scripts" / "lib"), str(self.tf)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), core._extract_status(self.tf), r.stderr)

    BROKEN_CORE = ("import importlib.abc, runpy, sys\n"
                   "class Broken(importlib.abc.MetaPathFinder):\n"
                   "    def find_spec(self, name, path=None, target=None):\n"
                   "        if name == 'tasks.core':\n"
                   "            raise ImportError('simulated broken install')\n"
                   "sys.meta_path.insert(0, Broken())\n"
                   "script = sys.argv[1]; sys.argv = [script]\n"
                   "runpy.run_path(script, run_name='__main__')\n")

    def _blocked_project(self):
        sid = "sess-214-v9"
        pointer = self.d / ".agent" / "sessions" / sid / "current_state"
        pointer.parent.mkdir(parents=True)
        pointer.write_text("001\n", encoding="utf-8")
        self.tf.write_text(task(status="blocked"), encoding="utf-8")
        return sid

    PATCH = "*** Begin Patch\n*** Update File: src/main.py\n@@\n-a\n+b\n*** End Patch\n"

    def test_an_unimportable_core_holds_a_blocked_tasks_edit(self):               # impl round 3, V9 (GRAVE)
        """`_core()` returned None when `tasks.core` could not be imported; the readers then read `""` / (0, 0), a
        blocked task looked active, and its code edit was ALLOWED, silently. The import error now reaches the caller."""
        import importlib
        from unittest import mock
        from provider import codex_hooks
        sid = self._blocked_project()
        patch = {"tool_input": {"command": self.PATCH}}
        self.assertEqual(codex_hooks.apply_patch_pre_decision(patch, self.d, sid)["decision"], "block")   # control
        real = importlib.import_module

        def broken(name, *a, **k):
            if name == "tasks.core":
                raise ImportError("simulated broken install")
            return real(name, *a, **k)
        with mock.patch("importlib.import_module", broken):
            for call in (lambda: codex_hooks.apply_patch_pre_decision(patch, self.d, sid),
                         lambda: codex_hooks._task_status(self.tf), lambda: codex_hooks._scan_gates(self.tf)):
                with self.assertRaises(ImportError):
                    call()
            # no plugin root above the module either (a copy of the hook module alone): an ImportError, not None
            with mock.patch.object(codex_hooks, "__file__", str(self.d / "alone" / "codex_hooks.py")):
                with self.assertRaises(ImportError):
                    codex_hooks._core()

    def test_the_apply_patch_hook_denies_when_core_cannot_be_imported(self):     # impl round 3, V9 — end to end
        import json
        import os
        import subprocess
        sid = self._blocked_project()
        payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"command": self.PATCH}}
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE", "CODEX"))}
        env.update({"PLAYBOOK_PROJECT_ROOT": str(self.d), "PLAYBOOK_SESSION_ID": sid, "PYTHONDONTWRITEBYTECODE": "1"})
        hook = str(_PLAYBOOK / "scripts" / "codex-apply-patch-hook")
        clean = subprocess.run([sys.executable, "-E", "-s", hook], input=json.dumps(payload), text=True,
                               capture_output=True, env=env, cwd=self.d, timeout=60)
        self.assertEqual(clean.returncode, 2, clean.stderr)                                    # control: blocked task
        self.assertIn("is blocked", clean.stderr)
        r = subprocess.run([sys.executable, "-E", "-s", "-c", self.BROKEN_CORE, hook], input=json.dumps(payload),
                           text=True, capture_output=True, env=env, cwd=self.d, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("simulated broken install", r.stderr)

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

    def test_an_eight_space_note_under_a_clean_line_is_not_a_receipt(self):         # impl round 2, U7 (opus, sonnet)
        text, log = self._after_move(self.LEGACY + "        an agent's eight-space note\n")
        self.assertIn("an agent's eight-space note", text)
        self.assertNotIn("an agent's eight-space note", log)

    def test_a_finding_line_is_one_physical_line_for_every_reader(self):           # impl round 2, U7 (opus)
        breaks = "\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
        audit = {"passed": False, "results": [{"status": "findings", "name": "custom\n- [ ] in the name", "severity": "info",
                                               "why": "w\x85- [ ] in the why",
                                               "output": "a\x85- [ ] x\r\nb\u2028- [ ] y\nc\rd\n"}]}
        receipt = format_audit_receipt(audit, timestamp="2026-10-15T10:00:01+03:00", head_sha="a" * 40)
        self.assertFalse(any(ch in receipt for ch in breaks), repr(receipt))
        self.assertEqual(core._gate_counts(receipt), (0, 0), receipt)
        self.assertFalse(any(core._GATE_LINE_RE.match(ln) for ln in receipt.splitlines()), receipt)   # an old reader too
        self.assertIn("        a[U+0085]- [ ] x\n        b[U+2028]- [ ] y\n        c[U+000D]d\n", receipt)
        self.assertIn("[FINDINGS(3)]", receipt)
        self.assertIn("custom[U+000A]- [ ] in the name ·info — w[U+0085]- [ ] in the why", receipt)

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
