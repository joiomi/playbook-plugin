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
import time
import unittest
from pathlib import Path

from tests._lockwait import until_held


@unittest.skipUnless(shutil.which("flock"), "no flock(1)")
class UntilHeld(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lock = Path(self._tmp.name) / "lock"
        self.lock.write_text("")

    def test_it_returns_once_a_holder_has_the_lock(self):
        holder = subprocess.Popen(["flock", "-s", str(self.lock), "sleep", "3"])
        self.addCleanup(holder.wait)
        t0 = time.monotonic()
        until_held(self.lock)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertNotEqual(subprocess.run(["flock", "-n", "-x", str(self.lock), "true"]).returncode, 0)

    def test_a_holder_that_never_takes_the_lock_is_said_as_such(self):
        with self.assertRaisesRegex(AssertionError, "never took the lock"):
            until_held(self.lock, limit=0.2)


if __name__ == "__main__":
    unittest.main()
