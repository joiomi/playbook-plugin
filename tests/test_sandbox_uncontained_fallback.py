#!/usr/bin/env python3
"""No usable containment backend → the UNCONTAINED path, said so — pin it on Linux.

On a Linux host without `bwrap` (or with one that cannot run), `_wrapped_argv`
falls through to a direct exec with bypass flags and no kernel write-denial, and
`containment_available()` returns False. `tasks/review.py` gates its "⚠ judge(s)
running UNCONTAINED" warning on exactly that switch — so pinning it False pins
that the warning path is the one such a host takes. (Up to 1.5.47 the same path
was pinned through a simulated Windows host, which had no backend at all.)

Run: python3 -m unittest tests.test_sandbox_uncontained_fallback
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))
from provider import sandbox  # noqa: E402


class LinuxWithoutBwrapTakesUncontainedPath(unittest.TestCase):
    def _linux(self, which):
        return (
            mock.patch.object(sandbox, "is_sandboxed", return_value=False),
            mock.patch.object(sandbox.shutil, "which", side_effect=which),
        )

    def test_no_bwrap_reports_no_containment(self):
        a, b = self._linux(lambda name: None)
        with a, b:
            self.assertFalse(sandbox.containment_available(),
                             "no bwrap on PATH — must report uncontained")

    def test_no_bwrap_argv_is_the_unwrapped_direct_exec(self):
        a, b = self._linux(lambda name: None)
        with a, b:
            argv = sandbox._wrapped_argv("claude", ["-p", "hi"], Path("/proj"), None, False)
        # Exactly the bypass-injected inner argv — no bwrap wrapper.
        self.assertEqual(argv, sandbox._compose_agent_argv("claude", ["-p", "hi"]))
        self.assertNotIn("bwrap", argv)

    def test_negative_control_bwrap_would_contain(self):
        """The False above is because no backend exists, not vacuously: hand the
        same code a bwrap on PATH and containment flips to True."""
        a, b = self._linux(lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
        with a, b:
            self.assertTrue(sandbox.containment_available())


if __name__ == "__main__":
    unittest.main(verbosity=2)
