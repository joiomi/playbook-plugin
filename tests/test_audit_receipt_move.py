#!/usr/bin/env python3
"""task.md keeps only the newest audit receipt (PHASE-B.md item 2, task 213).

The owner, 2026-10-15: "Doar cea nouă în fișă" — the older receipts move to an `audit-log.md`
beside task.md, kept for item 8's re-measurement. Measured before: the stacked receipts were a
median 14 % (this workspace) / 22 % (a second project) of a task.md, read by nothing but
`audit_freshness_note`, which reads the newest.

Pure stdlib unittest. Run: python3 tests/test_audit_receipt_move.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import core  # noqa: E402
from tasks.audit import audit_freshness_note  # noqa: E402

TASK = textwrap.dedent("""\
    # 001 - Demo

    ## Status
    in_progress

    ## Work Plan
    - [x] W1 done — note

    ## Parked
    (none)
    """)


def receipt(stamp: str, commit: str = "a" * 40, body: str = "clean") -> str:
    return f"### 2026-10-15T{stamp}+03:00 · PASS · commit {commit}\n    - [CLEAN] conflict-markers — {body}\n"


class _Dir(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb213-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.td = self.d / ".agent" / "tasks" / "001-demo"
        self.td.mkdir(parents=True)
        self.tf = self.td / "task.md"
        self.tf.write_text(TASK, encoding="utf-8")
        self.log = self.td / core.AUDIT_LOG_NAME

    def entries(self, text):
        return [ln for ln in text.splitlines() if ln.startswith("### ")]


class TheMove(_Dir):
    """Q1, Q5."""

    def test_task_md_keeps_the_newest_the_log_the_older_newest_first(self):
        for stamp in ("10:00:01", "10:00:02", "10:00:03"):
            core.record_audit_receipt(self.tf, receipt(stamp))
        self.assertEqual(self.entries(self.tf.read_text(encoding="utf-8")),
                         ["### 2026-10-15T10:00:03+03:00 · PASS · commit " + "a" * 40])
        self.assertEqual([e[:29] for e in self.entries(self.log.read_text(encoding="utf-8"))],
                         ["### 2026-10-15T10:00:02+03:00", "### 2026-10-15T10:00:01+03:00"])
        self.assertIn("## Work Plan", self.tf.read_text(encoding="utf-8"))
        self.assertIn("## Parked", self.tf.read_text(encoding="utf-8"))

    def test_the_freshness_note_reads_the_newest(self):
        core.record_audit_receipt(self.tf, receipt("10:00:01", commit="b" * 40))
        core.record_audit_receipt(self.tf, receipt("10:00:02", commit="c" * 40))
        text = self.tf.read_text(encoding="utf-8")
        self.assertIsNone(audit_freshness_note(text, "c" * 40))
        self.assertIn("STALE", audit_freshness_note(text, "b" * 40))

    def test_verification_receipts_still_stack(self):                               # P5
        core.upsert_task_section(self.tf, "Verification Receipt", "### close 1\n- ok\n")
        core.upsert_task_section(self.tf, "Verification Receipt", "### close 2\n- ok\n")
        self.assertEqual(self.entries(self.tf.read_text(encoding="utf-8")), ["### close 2", "### close 1"])


class NothingLostOrDoubled(_Dir):
    """Q2, Q3."""

    def test_a_crash_between_the_two_writes_doubles_nothing(self):
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        core.record_audit_receipt(self.tf, receipt("10:00:02"))
        real = core.atomic_write

        def fail_on_task_md(path, text, **kw):
            if Path(path).name == "task.md":
                raise OSError("simulated crash before task.md is written")
            return real(path, text, **kw)
        core.atomic_write = fail_on_task_md
        try:
            with self.assertRaises(OSError):
                core.record_audit_receipt(self.tf, receipt("10:00:03"))
        finally:
            core.atomic_write = real
        core.record_audit_receipt(self.tf, receipt("10:00:04"))
        allr = self.entries(self.tf.read_text(encoding="utf-8")) + self.entries(self.log.read_text(encoding="utf-8"))
        stamps = [e[:30] for e in allr]
        self.assertEqual(sorted(stamps), sorted(set(stamps)), stamps)
        for s in ("10:00:01", "10:00:02", "10:00:04"):
            self.assertEqual(sum(s in e for e in allr), 1, (s, allr))

    def test_a_death_at_the_second_write_loses_nothing(self):                       # W6: the order of the writes
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        core.record_audit_receipt(self.tf, receipt("10:00:02"))
        real, calls = core.atomic_write, []

        def die_at_the_second_write(path, text, **kw):
            calls.append(Path(path).name)
            if len(calls) == 2:
                raise SystemExit("simulated: the process dies at its second write")
            return real(path, text, **kw)
        core.atomic_write = die_at_the_second_write
        try:
            with self.assertRaises(SystemExit):
                core.record_audit_receipt(self.tf, receipt("10:00:03"))
        finally:
            core.atomic_write = real
        self.assertEqual(calls, [core.AUDIT_LOG_NAME, "task.md"])
        core.record_audit_receipt(self.tf, receipt("10:00:04"))
        allr = self.entries(self.tf.read_text(encoding="utf-8")) + self.entries(self.log.read_text(encoding="utf-8"))
        for s in ("10:00:01", "10:00:02", "10:00:04"):
            self.assertEqual(sum(s in e for e in allr), 1, (s, allr))

    def test_two_receipts_with_the_same_heading_and_different_bodies_both_survive(self):
        core.record_audit_receipt(self.tf, receipt("10:00:01", body="first"))
        core.record_audit_receipt(self.tf, receipt("10:00:01", body="second"))
        core.record_audit_receipt(self.tf, receipt("10:00:02"))
        both = self.tf.read_text(encoding="utf-8") + self.log.read_text(encoding="utf-8")
        self.assertIn("— first", both)
        self.assertIn("— second", both)

    def test_two_concurrent_auditors_lose_nothing(self):
        script = textwrap.dedent(f"""\
            import sys
            sys.path.insert(0, {str(_PLAYBOOK)!r})
            from pathlib import Path
            from tasks import core
            tag = sys.argv[1]
            for i in range(15):
                core.record_audit_receipt(Path({str(self.tf)!r}),
                    f"### 2026-10-15T10:00:00+03:00 · PASS · commit {{tag}}{{i:039d}}\\n    - [CLEAN] x — {{tag}}{{i}}\\n")
            """)
        procs = [subprocess.Popen([sys.executable, "-c", script, t]) for t in ("a", "b")]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        allr = self.entries(self.tf.read_text(encoding="utf-8")) + self.entries(self.log.read_text(encoding="utf-8"))
        self.assertEqual(len(allr), 30, allr)
        self.assertEqual(len(set(allr)), 30)


class TheSectionIsFoundSafely(_Dir):
    """Q4."""

    def test_free_text_in_the_section_stays_in_task_md(self):
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        text = self.tf.read_text(encoding="utf-8").replace(
            "## Pre-Panel Audit\n", "## Pre-Panel Audit\n\nA note the owner wrote here.\n", 1)
        self.tf.write_text(text, encoding="utf-8")
        core.record_audit_receipt(self.tf, receipt("10:00:02"))
        self.assertIn("A note the owner wrote here.", self.tf.read_text(encoding="utf-8"))
        self.assertNotIn("A note the owner", self.log.read_text(encoding="utf-8"))

    def test_a_note_after_a_receipt_stays_in_task_md(self):                          # W6: an entry's end
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        text = self.tf.read_text(encoding="utf-8").replace(
            "    - [CLEAN] conflict-markers — clean\n", "    - [CLEAN] conflict-markers — clean\nThe owner's note after it.\n", 1)
        self.tf.write_text(text, encoding="utf-8")
        core.record_audit_receipt(self.tf, receipt("10:00:02"))
        self.assertIn("The owner's note after it.", self.tf.read_text(encoding="utf-8"))
        self.assertNotIn("The owner's note", self.log.read_text(encoding="utf-8"))

    def test_a_fenced_receipt_line_inside_the_section_is_not_a_receipt(self):         # W6: fences in the section
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        quoted = "```text\n### 2026-01-01T00:00:00+00:00 · PASS · commit quoted\n```\n"
        text = self.tf.read_text(encoding="utf-8").replace("## Pre-Panel Audit\n", "## Pre-Panel Audit\n\n" + quoted, 1)
        self.tf.write_text(text, encoding="utf-8")
        core.record_audit_receipt(self.tf, receipt("10:00:02"))
        self.assertIn(quoted, self.tf.read_text(encoding="utf-8"))
        self.assertNotIn("commit quoted", self.log.read_text(encoding="utf-8"))

    def test_a_fenced_heading_is_not_the_section(self):
        self.tf.write_text(TASK + "\n```text\n## Pre-Panel Audit\n### quoted · PASS · commit x\n```\n",
                           encoding="utf-8")
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        core.record_audit_receipt(self.tf, receipt("10:00:02"))
        text = self.tf.read_text(encoding="utf-8")
        self.assertIn("### quoted · PASS · commit x", text)
        self.assertEqual(text.count("```"), 2)

    def test_an_unclosed_fence_that_hides_a_section_refuses_and_changes_nothing(self):
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        core.record_audit_receipt(self.tf, receipt("10:00:02"))
        text = self.tf.read_text(encoding="utf-8").replace(
            "    - [CLEAN] conflict-markers — clean\n", "    - [CLEAN] conflict-markers — clean\n```\n", 1)
        text += "\n## After\nmust survive\n"
        self.tf.write_text(text, encoding="utf-8")
        before_task, before_log = self.tf.read_bytes(), self.log.read_bytes()
        with self.assertRaises(ValueError):
            core.record_audit_receipt(self.tf, receipt("10:00:03"))
        self.assertEqual(self.tf.read_bytes(), before_task)
        self.assertEqual(self.log.read_bytes(), before_log)

    def test_a_log_that_cannot_be_read_leaves_the_history_in_task_md(self):           # P4
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        self.log.write_bytes(b"### broken \xff\xfe\n")
        warning = core.record_audit_receipt(self.tf, receipt("10:00:02"))
        self.assertTrue(warning)
        self.assertEqual(len(self.entries(self.tf.read_text(encoding="utf-8"))), 2)
        self.assertEqual(self.log.read_bytes(), b"### broken \xff\xfe\n")

    def test_a_log_that_cannot_be_written_leaves_the_history_in_task_md(self):       # P4
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        self.log.mkdir()
        warning = core.record_audit_receipt(self.tf, receipt("10:00:02"))
        self.assertTrue(warning)
        self.assertEqual(len(self.entries(self.tf.read_text(encoding="utf-8"))), 2)

    def test_a_log_readable_but_not_writable_leaves_the_history_in_task_md(self):    # W6: the WRITE error path
        core.record_audit_receipt(self.tf, receipt("10:00:01"))
        core.record_audit_receipt(self.tf, receipt("10:00:02"))
        before_log = self.log.read_bytes()
        real = core.atomic_write

        def log_write_fails(path, text, **kw):
            if Path(path).name == core.AUDIT_LOG_NAME:
                raise OSError("simulated: no space for the log")
            return real(path, text, **kw)
        core.atomic_write = log_write_fails
        try:
            warning = core.record_audit_receipt(self.tf, receipt("10:00:03"))
        finally:
            core.atomic_write = real
        self.assertIn("cannot be written", warning or "")
        heads = [e[:29] for e in self.entries(self.tf.read_text(encoding="utf-8"))]
        self.assertEqual(heads, ["### 2026-10-15T10:00:03+03:00", "### 2026-10-15T10:00:02+03:00"])
        self.assertEqual(self.log.read_bytes(), before_log)


class ThroughTheCli(unittest.TestCase):
    """Q1 through the real `tasks audit <N>`."""

    def test_two_audits_leave_one_receipt_and_a_log(self):
        d = Path(tempfile.mkdtemp(prefix="pb213cli-"))
        self.addCleanup(shutil.rmtree, d, True)
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        td = d / ".agent" / "tasks" / "001-demo"
        td.mkdir(parents=True)
        (td / "task.md").write_text(TASK, encoding="utf-8")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=d, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"], cwd=d, check=True)
        home = d / "home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update({"PYTHONPATH": str(_PLAYBOOK), "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
        for _ in range(2):
            r = subprocess.run([sys.executable, "-m", "tasks.cli", "audit", "1"], cwd=d, env=env,
                               capture_output=True, text=True, timeout=120)
            self.assertIn("Receipt recorded", r.stdout + r.stderr, r.stdout + r.stderr)
        text = (td / "task.md").read_text(encoding="utf-8")
        self.assertEqual(sum(ln.startswith("### ") for ln in text.splitlines()), 1)
        log = (td / core.AUDIT_LOG_NAME).read_text(encoding="utf-8")
        self.assertEqual(sum(ln.startswith("### ") for ln in log.splitlines()), 1)


class ThePanelIsNotVoided(unittest.TestCase):
    """Q11 (P1): a newly created audit-log.md in a tracked task folder is the task's own record."""

    def test_a_new_audit_log_does_not_void_a_panel(self):
        sys.path.insert(0, str(_HERE))
        from test_tamper_scope import R, _commit_all, _repo
        d = _repo()
        td = d / ".agent" / "tasks" / "001-x"
        td.mkdir(parents=True)
        tf = td / "task.md"
        tf.write_text("gate\n", encoding="utf-8")
        _commit_all(d)
        before = R._snapshot_repo_state(d, tf)
        (td / "audit-log.md").write_text("### old · PASS · commit x\n", encoding="utf-8")
        full = R._detect_tamper_full(d, tf, before)
        self.assertEqual(full["mutations"], [], full)


if __name__ == "__main__":
    unittest.main()
