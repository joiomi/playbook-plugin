#!/usr/bin/env python3
"""scripts/verify (and the shell-fixtures runner) must scrub dogfooding env off
the CHILD environment they build, or a host that already exports that state
distorts the very suite meant to be a clean reading.

`PLAYBOOK_SANDBOXED=1` is the sole signal `provider/sandbox.is_sandboxed()`
reads (sandbox.py). A dogfooding host that runs `scripts/verify` from inside a
sandboxed session exports it, and — if verify passed it through — every child
test that probes sandbox/monitor/state-echo behavior would run as if already
sandboxed. Several individual tests scrub it locally
(test_print_argv_is_dry_run, test_launch_monitor_containment); the fix here is
to scrub it once, centrally, where the child env is assembled.

Run: python3 -m unittest tests.test_verify_env_scrub
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_VERIFY = _HERE.parent / "scripts" / "verify"


def _load_verify():
    """Path-load the extension-less `scripts/verify` as a module."""
    loader = importlib.machinery.SourceFileLoader("_pb_verify_under_test", str(_VERIFY))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class VerifyEnvScrub(unittest.TestCase):
    # Every dogfooding key verify's env() must strip off the child suite.
    _SCRUBBED = ("BASH_ENV", "PLAYBOOK_SESSION_ID", "PLAYBOOK_ROLE",
                 "PLAYBOOK_EVAL_CONFIG", "PLAYBOOK_SANDBOXED")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._SCRUBBED}
        for k in self._SCRUBBED:
            os.environ[k] = "injected-by-test"
        # PLAYBOOK_SANDBOXED reads as a flag; give it the flip-triggering value.
        os.environ["PLAYBOOK_SANDBOXED"] = "1"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_verify_env_scrubs_playbook_sandboxed(self):
        verify = _load_verify()
        child = verify.env()
        for k in self._SCRUBBED:
            self.assertNotIn(
                k, child,
                f"scripts/verify env() leaked {k} into the child suite env — "
                "a dogfooding host's value would distort the reading")

    def test_verify_env_turns_off_git_background_maintenance(self):
        """Task 143 (retro 134 (c)): a test repo's auto-maintenance can still be writing
        while TemporaryDirectory removes it (the py3.12 CI flake of the 1.5.46 release).
        Every child gets maintenance.auto=false and gc.auto=0 — and a real git reads them."""
        import subprocess
        verify = _load_verify()
        e = verify.env()
        for key, want in (("maintenance.auto", "false"), ("gc.auto", "0")):
            r = subprocess.run(["git", "config", "--get", key], env=e, capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), want, (key, r.stderr))

    def test_verify_env_keeps_a_callers_own_git_config_entries(self):
        verify = _load_verify()
        os.environ["GIT_CONFIG_COUNT"] = "1"
        os.environ["GIT_CONFIG_KEY_0"] = "user.name"
        os.environ["GIT_CONFIG_VALUE_0"] = "From The Caller"
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in
                                 ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")])
        import subprocess
        e = verify.env()
        r = subprocess.run(["git", "config", "--get", "user.name"], env=e, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "From The Caller")
        r = subprocess.run(["git", "config", "--get", "gc.auto"], env=e, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "0")

    def test_shell_fixtures_clean_env_scrubs_playbook_sandboxed(self):
        # The parallel scrub list in the fixtures runner: a fixture that toggles
        # PLAYBOOK_SANDBOXED internally (gate-logging-failure-fixture) is distorted
        # if the parent env already has it set.
        sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))
        import tests.test_shell_fixtures as tsf  # noqa
        child = tsf._clean_env()
        self.assertNotIn(
            "PLAYBOOK_SANDBOXED", child,
            "tests/test_shell_fixtures _clean_env() leaked PLAYBOOK_SANDBOXED")


if __name__ == "__main__":
    unittest.main()
