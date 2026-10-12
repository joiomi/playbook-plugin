"""INTENT.md keeps every ratified entry when two sessions append at once (task 170, impl panel
round 1, P7 — codex).

`append_intent` writes the owner-ratified intent of a task into the project's root INTENT.md:
it reads the file, adds the entry, and replaces the file atomically. Atomic per WRITE, but not
as a transaction: two sessions ratifying at the same moment each read the old file, and the
one that replaces it last erases the entry the other had just written — an entry the user
ratified, lost. Now the read and the replace happen under one lock (`.agent/intent.lock`, the
project's `filelock` protocol), and init's `.gitignore` block keeps that lock file out of the
record.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))

from tasks import intent  # noqa: E402

_spec = importlib.util.spec_from_file_location("claude_md_merge", _PLAYBOOK / "scripts" / "claude-md-merge.py")
cmm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmm)

# One session's append, run as its own process. The write is held back after the read for
# `pause` seconds, and a flag file says when that read has happened — the window two
# ratifications share when they land together.
_DRIVER = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tasks import intent
intent_md, task, run, pause, flag = Path(sys.argv[2]), sys.argv[3], sys.argv[4], float(sys.argv[5]), Path(sys.argv[6])
real = intent.atomic_write
def held(path, text, *a, **k):
    flag.write_text("read\n")
    time.sleep(pause)
    return real(path, text, *a, **k)
intent.atomic_write = held
intent.append_intent(intent_md, task, run, f"- **Confirmed** — the intent of task {task}, run {run}.")
'''


class AppendIntent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.intent_md = self.root / "INTENT.md"
        self.driver = self.root / "drv.py"
        self.driver.write_text(_DRIVER, encoding="utf-8")

    def _start(self, task: str, run: str, pause: float, flag: Path) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, "-I", str(self.driver), str(_PLAYBOOK), str(self.intent_md),
                                 task, run, str(pause), str(flag)])

    def _wait_flag(self, flag: Path, seconds: float = 20) -> None:
        end = time.monotonic() + seconds
        while not flag.exists():
            self.assertLess(time.monotonic(), end, "the first session never reached its write")
            time.sleep(0.02)

    def test_two_sessions_ratifying_at_once_keep_both_entries(self):
        """The first session has read INTENT.md and is about to replace it; the second appends
        meanwhile. Without the lock the second entry is erased (`'T002' not found`)."""
        intent.append_intent(self.intent_md, "001", "r0", "- **Confirmed** — an entry ratified before.")
        a = self._start("042", "r1", 2.0, self.root / "a.flag")
        self._wait_flag(self.root / "a.flag")
        b = self._start("043", "r2", 0.0, self.root / "b.flag")
        self.assertEqual(b.wait(timeout=60), 0)
        self.assertEqual(a.wait(timeout=60), 0)
        text = self.intent_md.read_text(encoding="utf-8")
        for marker in ("<!-- intent:T001:r0 -->", "<!-- intent:T042:r1 -->", "<!-- intent:T043:r2 -->"):
            self.assertIn(marker, text)
        self.assertEqual(text.count("## task "), 3)

    def test_the_same_run_ratified_twice_is_one_entry(self):
        intent.append_intent(self.intent_md, "042", "r1", "- **Confirmed** — once.")
        intent.append_intent(self.intent_md, "042", "r1", "- **Confirmed** — again.")
        text = self.intent_md.read_text(encoding="utf-8")
        self.assertEqual(text.count("<!-- intent:T042:r1 -->"), 1)
        self.assertIn("once.", text)
        self.assertNotIn("again.", text)

    def test_the_lock_lives_in_agent_and_init_keeps_it_out_of_the_record(self):
        intent.append_intent(self.intent_md, "042", "r1", "- **Confirmed** — an entry.")
        self.assertTrue((self.root / ".agent" / "intent.lock").exists())
        self.assertFalse((self.root / "INTENT.md.lock").exists())
        self.assertIn(".agent/intent.lock", cmm.GITIGNORE_ENTRIES)


if __name__ == "__main__":
    unittest.main()
