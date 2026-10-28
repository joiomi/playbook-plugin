"""A task record that is not valid UTF-8 is refused, never rewritten (task 335, grave B of DESIGN-PLAYBOOK-2.md §7).

I10 (verification-report-1.5.9): one `0xE9` byte (a cp1252 `é`, a real field scenario) made the task UNOPENABLE and
UNCLOSEABLE with a raw traceback, and the fix then read leniently (errors="replace") so the task stayed usable. That
leniency wrote U+FFFD back over the byte at the first CLI write, so the byte someone wrote was gone (rule v3,
criterion 1). Since task 335 the write primitive refuses to replace a text file whose bytes are not valid UTF-8: the
command says `Refused:`, names the byte, exits 1, and the file stays byte-identical. Still no traceback.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
SID = "pid-utf8-test"

# A task.md with a raw 0xE9 (cp1252 'é') byte — NOT valid UTF-8.
TASK_BYTES = (
    b"# 001 - x\n\n## Status\nin_progress\n\n## Risk\nreversible\n\n"
    b"## Work Plan\n- [x] caf\xe9 gate done\n"
)
BAD = b"\xe9"                                       # the byte that is not UTF-8


class NonUtf8Task(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        d = self.project / ".agent" / "tasks" / "001-x"
        d.mkdir(parents=True)
        (d / "task.md").write_bytes(TASK_BYTES)

    def run_tasks(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PLUGIN)
        env["PLAYBOOK_SESSION_ID"] = SID
        env.pop("BASH_ENV", None)
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args],
                              cwd=self.project, env=env, text=True,
                              capture_output=True)

    def _assert_refused(self, r):
        task_md = self.project / ".agent" / "tasks" / "001-x" / "task.md"
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        self.assertNotIn("UnicodeDecodeError", r.stderr, r.stderr)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("Refused:", r.stderr)
        self.assertIn("task.md", r.stderr)
        self.assertIn(f"byte {TASK_BYTES.index(BAD)}", r.stderr)
        self.assertEqual(task_md.read_bytes(), TASK_BYTES)

    def test_activate_refuses_cleanly(self):
        self._assert_refused(self.run_tasks("work", "001"))

    def test_close_refuses_cleanly(self):
        # activation refuses such a file too (the test above), so the task is activated while its file is valid, and
        # the byte lands before the close, as a Bash write would put it there
        task_md = self.project / ".agent" / "tasks" / "001-x" / "task.md"
        task_md.write_bytes(TASK_BYTES.replace(BAD, b"e"))
        r = self.run_tasks("work", "001")
        self.assertEqual(r.returncode, 0, r.stderr)
        task_md.write_bytes(TASK_BYTES)
        self._assert_refused(self.run_tasks("work", "done"))


# A task.md that `tasks compact` has something to move in, with the same 0xE9 byte.
COMPACT_BYTES = (
    b"# 001 - x\n\n## Status\nin_progress\n\n## Risk\nreversible\n\n## Work Plan\n- [x] caf\xe9 gate done\n\n"
    b"## Implementation Review\n<!-- archive:start -->\n### Round 1\nold narrative\n<!-- archive:end -->\n\n"
    b"## Parked\nNothing.\n"
)


class _CliProject(unittest.TestCase):
    """A git project with one task folder, and the CLI run in it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        self.task_dir = self.project / ".agent" / "tasks" / "001-x"
        self.task_dir.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)

    def run_tasks(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PLUGIN)
        env["PLAYBOOK_SESSION_ID"] = SID
        env.pop("BASH_ENV", None)
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args],
                              cwd=self.project, env=env, text=True,
                              capture_output=True)

    def _assert_refused(self, r, body):
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Refused:", r.stderr)
        self.assertIn(f"byte {body.index(BAD)}", r.stderr)
        self.assertEqual((self.task_dir / "task.md").read_bytes(), body)


class CliRefuses(_CliProject):
    """Rows 4-5: the commands that write a task record say `Refused:` and change nothing."""

    def test_audit_refuses(self):
        (self.task_dir / "task.md").write_bytes(TASK_BYTES)
        for _ in range(2):                     # a second run refuses the same way
            self._assert_refused(self.run_tasks("audit", "001"), TASK_BYTES)

    def test_compact_refuses(self):
        (self.task_dir / "task.md").write_bytes(COMPACT_BYTES)
        self._assert_refused(self.run_tasks("compact", "001"), COMPACT_BYTES)
        self.assertFalse((self.task_dir / "task-archive.md").exists())


# Rows 8-12 (task 335, impl round 1): a command that writes a second file, runs verify or pays judges refuses at
# its start, before any of that — the primitive refused only at its own write, after audit-log.md, red-receipt.jsonl,
# an archive or a paid panel.
AUDIT_BYTES = (
    b"# 001 - x\n\n## Status\nin_progress\n\n## Risk\nreversible\n\n## Work Plan\n- [x] caf\xe9 gate done\n\n"
    b"## Pre-Panel Audit\n\n### 2026-10-15T10:00:01+03:00 \xc2\xb7 PASS \xc2\xb7 commit " + b"a" * 40 +
    b"\n    - [CLEAN] conflict-markers \xe2\x80\x94 an older receipt\n"
)
RED_BYTES = (                     # `pending`: the first activation records base.json, which `tasks red` needs
    b"# 001 - x\n\n## Status\npending\n\n## Risk\nreversible\n\n## Work Plan\n- [ ] caf\xe9 gate\n\n"
    b"## Test contract\n\n| # | Kind | Behavior | Test that proves it |\n|---|------|----------|---------------------|\n"
    b"| 1 | normal | true | `true` |\n"
)


class CommandsRefuseFirst(_CliProject):

    def test_audit_with_an_older_receipt_writes_no_log(self):
        (self.task_dir / "task.md").write_bytes(AUDIT_BYTES)
        self._assert_refused(self.run_tasks("audit", "001"), AUDIT_BYTES)
        self.assertFalse((self.task_dir / "audit-log.md").exists())

    def test_red_refuses_before_any_row(self):
        # activated while valid (that records base.json, which `tasks red` needs), then the byte lands
        (self.task_dir / "task.md").write_bytes(RED_BYTES.replace(BAD, b"e"))
        self.assertEqual(self.run_tasks("work", "001").returncode, 0)
        self.assertTrue((self.task_dir / "base.json").exists())
        bad = (self.task_dir / "task.md").read_bytes().replace(b"cafe gate", b"caf\xe9 gate")
        (self.task_dir / "task.md").write_bytes(bad)
        self._assert_refused(self.run_tasks("red", "001"), bad)
        self.assertFalse((self.task_dir / "red-receipt.jsonl").exists())

    def test_close_refuses_before_verify(self):
        (self.project / ".agent" / "config.json").write_text(
            '{"verify": {"_always": ["touch verify-ran"]}}', encoding="utf-8")
        valid = TASK_BYTES.replace(BAD, b"e")
        (self.task_dir / "task.md").write_bytes(valid)
        self.assertEqual(self.run_tasks("work", "001").returncode, 0)
        (self.task_dir / "task.md").write_bytes(TASK_BYTES)
        self._assert_refused(self.run_tasks("work", "done"), TASK_BYTES)
        self.assertFalse((self.project / "verify-ran").exists())

    def test_activation_refuses_before_its_note(self):
        (self.task_dir / "task.md").write_bytes(TASK_BYTES)        # all gates checked: the re-adopt branch
        r = self.run_tasks("work", "001")
        self._assert_refused(r, TASK_BYTES)
        self.assertNotIn("re-adopting", r.stdout + r.stderr)

    def test_panel_refuses_before_any_seat(self):
        """A judge.md that is not UTF-8: refused before a seat is launched (no seat log), judge.md byte-identical.
        PATH holds only python and git, so the old code's seats fail without spending anything."""
        (self.task_dir / "task.md").write_bytes(TASK_BYTES.replace(BAD, b"e"))
        judge = b"# Panel Impl Review \xe2\x80\x94 old\n\ncaf\xe9\n"
        (self.task_dir / "judge.md").write_bytes(judge)
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir()
        import shutil
        for tool in (sys.executable, shutil.which("git"), shutil.which("bash")):
            os.symlink(tool, bin_dir / Path(tool).name)
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID=SID, PATH=str(bin_dir),
                   HOME=str(Path(self._tmp.name) / "home"))
        env.pop("BASH_ENV", None)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "panel-review", "001", "--mode", "impl"],
                           cwd=self.project, env=env, text=True, capture_output=True, timeout=300)
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Refused:", r.stderr)
        self.assertIn(f"byte {judge.index(BAD)}", r.stderr)
        self.assertEqual((self.task_dir / "judge.md").read_bytes(), judge)
        self.assertEqual(sorted(x.name for x in self.task_dir.iterdir()), ["judge.md", "task.md"])


class TasklessPanel(_CliProject):
    """Row 15 (task 335, post-panel run 1, codex): a taskless consultation (`panel-review --bare --prompt`) writes the
    project's `.agent/judge.md`; when that is not UTF-8 it is refused before any seat, as a task's panel is."""

    def test_a_consultation_refuses_before_any_seat(self):
        agent = self.project / ".agent"
        judge = b"# Panel Consultation \xe2\x80\x94 old\n\ncaf\xe9\n"
        (agent / "judge.md").write_bytes(judge)
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir()
        import shutil
        for tool in (sys.executable, shutil.which("git"), shutil.which("bash")):
            os.symlink(tool, bin_dir / Path(tool).name)
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID=SID, PATH=str(bin_dir),
                   HOME=str(Path(self._tmp.name) / "home"))
        env.pop("BASH_ENV", None)
        before = sorted(x.name for x in agent.iterdir())
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "panel-review", "--bare", "--prompt", "a question"],
                           cwd=self.project, env=env, text=True, capture_output=True, timeout=300)
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Refused:", r.stderr)
        self.assertIn(f"byte {judge.index(BAD)}", r.stderr)
        self.assertEqual((agent / "judge.md").read_bytes(), judge)


class JudgeRoundKept(unittest.TestCase):
    """Row 12: a paid round is never dropped by the refusal — an archive that is not UTF-8 keeps every round in
    judge.md, untrimmed, as an archive that cannot be written did before."""

    def setUp(self):
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_a_bad_archive_keeps_the_round_in_judge_md(self):
        from tasks.core import stack_judge_round
        judge_md, archive = self.dir / "judge.md", self.dir / "judge-archive.md"
        judge_md.write_text("# Panel Impl Review — round one\n\nold verdicts\n", encoding="utf-8")
        archive_bytes = b"# Archive\n\ncaf\xe9\n"
        archive.write_bytes(archive_bytes)
        stack_judge_round(judge_md, "# Panel Impl Review — round two\n\nnew paid verdicts\n", max_rounds=1)
        text = judge_md.read_text(encoding="utf-8")
        self.assertIn("new paid verdicts", text)
        self.assertIn("old verdicts", text)
        self.assertEqual(archive.read_bytes(), archive_bytes)

    def test_a_crlf_judge_md_is_read_as_before(self):
        """Row 16 (post-panel run 1, Minor): the strict read translates newlines as `read_text` did, so a CRLF
        judge.md is rewritten with LF only, as before task 335 (the archive's dedup key reads the same text)."""
        from tasks.core import stack_judge_round
        judge_md = self.dir / "judge.md"
        judge_md.write_bytes(b"# Panel Impl Review \xe2\x80\x94 round one\r\n\r\nold verdicts\r\n")
        stack_judge_round(judge_md, "# Panel Impl Review — round two\n\nnew verdicts\n")
        out = judge_md.read_bytes()
        self.assertNotIn(b"\r", out)
        self.assertIn(b"old verdicts", out)
        self.assertIn(b"new verdicts", out)


class UnsavedRoundKept(unittest.TestCase):
    """Row 17 (task 335, post-panel run 2, codex): a panel round whose save into judge.md fails late (the file became
    unreadable as UTF-8 after the preflight, or cannot be written) is kept whole in a new file beside it, and the
    command says where — the paid answers are never lost."""

    def setUp(self):
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_a_refused_save_keeps_the_round_in_its_own_file(self):
        from tasks import review
        judge_md = self.dir / "judge.md"
        judge = b"# Panel Impl Review \xe2\x80\x94 old\n\ncaf\xe9\n"
        judge_md.write_bytes(judge)
        failed, note = review._save_panel_round(judge_md, "# Panel Impl Review — new\n\npaid findings\n", "abc123def456")
        self.assertTrue(failed)
        self.assertEqual(judge_md.read_bytes(), judge)
        kept = self.dir / "judge.unsaved-abc123def456.md"
        self.assertEqual(kept.read_text(encoding="utf-8"), "# Panel Impl Review — new\n\npaid findings\n")
        self.assertIn(kept.name, note)

    def test_a_saved_round_writes_judge_md_only(self):
        from tasks import review
        judge_md = self.dir / "judge.md"
        failed, note = review._save_panel_round(judge_md, "# Panel Impl Review — new\n\npaid findings\n", "abc123def456")
        self.assertFalse(failed)
        self.assertIn("paid findings", judge_md.read_text(encoding="utf-8"))
        self.assertEqual(sorted(x.name for x in self.dir.iterdir() if not x.name.endswith(".lock")), ["judge.md"])


class WriteRefuses(unittest.TestCase):
    """Rows 1-3 and 7: the primitive itself."""

    def setUp(self):
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_atomic_write_refuses_and_leaves_the_file(self):
        from tasks import atomic
        p = self.dir / "task.md"
        p.write_bytes(TASK_BYTES)
        with self.assertRaises(atomic.NotUtf8Error) as cm:
            atomic.atomic_write(p, "new text\n")
        msg = str(cm.exception)
        self.assertIn(str(p), msg)
        self.assertIn(f"byte {TASK_BYTES.index(BAD)}", msg)
        self.assertIn("0xe9", msg)
        self.assertEqual(p.read_bytes(), TASK_BYTES)
        self.assertEqual(sorted(x.name for x in self.dir.iterdir()), ["task.md"])   # no temp file left

    def test_atomic_write_still_writes(self):
        from tasks import atomic
        fresh = self.dir / "fresh.md"
        atomic.atomic_write(fresh, "a\n")
        self.assertEqual(fresh.read_text(encoding="utf-8"), "a\n")
        bom = self.dir / "bom.md"
        bom.write_bytes("\ufeffcafé\n".encode("utf-8"))
        atomic.atomic_write(bom, "b\n")
        self.assertEqual(bom.read_text(encoding="utf-8"), "b\n")
        big = self.dir / "big.md"
        big.write_text("é" * (5 * 1024 * 1024 // 2), encoding="utf-8")
        atomic.atomic_write(big, "c\n")
        self.assertEqual(big.read_text(encoding="utf-8"), "c\n")
        raw = self.dir / "raw.log"
        raw.write_bytes(TASK_BYTES)
        atomic.atomic_write(raw, b"\xff bytes as before\n")
        self.assertEqual(raw.read_bytes(), b"\xff bytes as before\n")

    def test_rewrite_refuses_before_the_transform(self):
        from tasks import atomic
        p = self.dir / "task.md"
        p.write_bytes(TASK_BYTES)
        called = []
        with self.assertRaises(atomic.NotUtf8Error):
            atomic.rewrite(p, lambda text: called.append(text) or text + "x\n")
        self.assertEqual(called, [])
        self.assertEqual(p.read_bytes(), TASK_BYTES)

    def test_encoded_surrogate_is_refused(self):
        from tasks import atomic
        p = self.dir / "task.md"
        body = b"# 001\n\xed\xa0\x80\n"           # CESU-8 for a lone surrogate: not UTF-8
        p.write_bytes(body)
        with self.assertRaises(atomic.NotUtf8Error):
            atomic.atomic_write(p, "x\n")
        self.assertEqual(p.read_bytes(), body)


if __name__ == "__main__":
    unittest.main()
