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


class CliRefuses(unittest.TestCase):
    """Rows 4-5: the commands that write a task record say `Refused:` and change nothing."""

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

    def test_audit_refuses(self):
        (self.task_dir / "task.md").write_bytes(TASK_BYTES)
        for _ in range(2):                     # a second run refuses the same way
            self._assert_refused(self.run_tasks("audit", "001"), TASK_BYTES)

    def test_compact_refuses(self):
        (self.task_dir / "task.md").write_bytes(COMPACT_BYTES)
        self._assert_refused(self.run_tasks("compact", "001"), COMPACT_BYTES)
        self.assertFalse((self.task_dir / "task-archive.md").exists())


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
