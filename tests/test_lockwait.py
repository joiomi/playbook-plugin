#!/usr/bin/env python3
"""The tests' lock-holder wait (`tests/_lockwait.py`, task 268): it returns once another process holds the lock, and
past its limit it says the holder never took it, so a race the wait is meant to close is never blamed on the code
(PLAN.md "From task 266" (a)).

Run: python3 -m unittest tests.test_lockwait
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._lockwait import Waits, until_held


@unittest.skipUnless(shutil.which("flock"), "no flock(1)")
class UntilHeld(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lock = Path(self._tmp.name) / "lock"
        self.lock.write_text("")

    def test_it_returns_once_a_holder_has_the_lock(self):
        holder = subprocess.Popen(["flock", "-s", str(self.lock), "sleep", "30"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        until_held(self.lock)
        self.assertNotEqual(subprocess.run(["flock", "-n", "-x", str(self.lock), "true"]).returncode, 0)

    def test_a_missing_lock_path_is_said_at_once(self):
        with self.assertRaisesRegex(AssertionError, "no lock at"):
            until_held(self.lock.parent / "no-such-lock")
        self.assertFalse((self.lock.parent / "no-such-lock").exists(), "the wait made the lock it waited for")

    def test_a_wait_inside_a_swallowing_stub_is_raised_at_its_check(self):
        """Task 268's impl round 1 (opus Important): inside `try … except Exception` the helper's error was lost."""
        wait = Waits()
        try:
            wait(self.lock, limit=0.2)                 # no holder: it never sees the lock held
            raise RuntimeError("the code under test went on")
        except Exception:                              # noqa: BLE001 — what pb_journal does around its stubs
            pass
        with self.assertRaisesRegex(AssertionError, "never took the lock"):
            wait.check()

    def test_a_holder_that_never_takes_the_lock_is_said_as_such(self):
        with self.assertRaisesRegex(AssertionError, "never took the lock"):
            until_held(self.lock, limit=0.2)


if __name__ == "__main__":
    unittest.main()
