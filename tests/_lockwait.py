"""Waiting for another process to hold a lock, for the tests that need a reader or holder in place first.

A fixed sleep (0.3 s) let a slow runner give the code under test the lock first, a false red blamed on the code (task
263's impl round 1, opus). `until_held` polls instead: another process holds `path`'s lock once an exclusive take that
does not wait fails. Past its limit it raises, so a holder that never took the lock is said as such (task 266's impl
round 1, opus and sonnet) instead of letting the old race back in.

The leading underscore keeps unittest's ``test*.py`` discovery from collecting this module as a test.
"""
from __future__ import annotations

import os
import subprocess
import time


def until_held(path, limit: float = 10.0) -> None:
    """Return once another process holds `path`'s lock (flock); raise AssertionError after `limit` seconds. The path
    must exist: `flock` would make a missing one, and the wait would then run out on a mistyped name (task 268's impl
    round 1, sonnet)."""
    if not os.path.exists(path):
        raise AssertionError(f"no lock at {path}: a holder cannot have taken it")
    t0 = time.monotonic()
    while subprocess.run(["flock", "-n", "-x", str(path), "true"]).returncode == 0:
        if time.monotonic() - t0 >= limit:
            raise AssertionError(f"the lock holder never took the lock on {path} within {limit:g} s")
        time.sleep(0.02)


class Waits:
    """`until_held` for a wait inside a stub that the code under test calls within `try … except Exception` (the
    journals' `_gzip_to` / `_gz_holds`): its AssertionError would be swallowed there and the test fail later on an
    unrelated line (task 268's impl round 1, opus). Call it like `until_held`, then `check()` after the code under test
    returns: the first wait that never saw the lock held is raised there."""

    def __init__(self) -> None:
        self.errors: "list[AssertionError]" = []

    def __call__(self, path, limit: float = 10.0) -> None:
        try:
            until_held(path, limit)
        except AssertionError as e:
            self.errors.append(e)

    def check(self) -> None:
        if self.errors:
            raise self.errors[0]
