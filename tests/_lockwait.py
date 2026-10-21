"""Waiting for another process to hold a lock, for the tests that need a reader or holder in place first.

A fixed sleep (0.3 s) let a slow runner give the code under test the lock first, a false red blamed on the code (task
263's impl round 1, opus). `until_held` polls instead: another process holds `path`'s lock once an exclusive take that
does not wait fails. Past its limit it raises, so a holder that never took the lock is said as such (task 266's impl
round 1, opus and sonnet) instead of letting the old race back in.

The leading underscore keeps unittest's ``test*.py`` discovery from collecting this module as a test.
"""
from __future__ import annotations

import subprocess
import time


def until_held(path, limit: float = 10.0) -> None:
    """Return once another process holds `path`'s lock (flock); raise AssertionError after `limit` seconds."""
    t0 = time.monotonic()
    while subprocess.run(["flock", "-n", "-x", str(path), "true"]).returncode == 0:
        if time.monotonic() - t0 >= limit:
            raise AssertionError(f"the lock holder never took the lock on {path} within {limit:g} s")
        time.sleep(0.02)
