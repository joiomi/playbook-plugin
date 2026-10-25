"""verify acts as no one: its children get a HOME of the run's own and reach no sign-in, and no test it runs starts
an agent (task 316).

Measured in task 315: three tests of the spend journal's StructuredUsageE2E simulate a failed judge, and the review
then probed the pin live — a real `codex exec -m gpt-5.6-sol … "reply with exactly: ok"`, signed in with the HOME of
whoever ran verify (977 such sessions in his `~/.codex/sessions` since 2026-10-06), and grok twice. agy signs in
through the keyring on the session bus, so a throwaway HOME alone did not cut it (`agy models` listed his models; with
the bus disabled it asked to sign in). 20 modules left files in the HOME they ran with.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

SIGN_INS = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "GROK_HOME", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS")
XDG = (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"),
       ("XDG_STATE_HOME", ".local/state"))
FAILED_JUDGE_TESTS = (
    "tests.test_review_spend_journal.StructuredUsageE2E.test_single_nonzero_exit_streams_the_failure_text_not_raw_json",
    "tests.test_review_spend_journal.StructuredUsageE2E.test_single_structured_error_with_exit_0_is_a_failed_review",
    "tests.test_review_spend_journal.StructuredUsageE2E.test_panel_no_text_structured_seat_journals_fail_not_dnf",
)


def _load_verify():
    path = ROOT / "scripts" / "verify"
    loader = importlib.machinery.SourceFileLoader("verify_under_test_316", str(path))
    spec = importlib.util.spec_from_loader("verify_under_test_316", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _var_tmp_usable() -> bool:
    return os.path.isdir("/var/tmp") and os.access("/var/tmp", os.W_OK | os.X_OK)


class TheVerifyEnvironment(unittest.TestCase):

    def setUp(self):
        self.caller = tempfile.mkdtemp(prefix="pb316-caller-")      # stands for the person's own HOME
        self.addCleanup(shutil.rmtree, self.caller, True)
        self.v = _load_verify()

    def env(self, **extra):
        with mock.patch.dict(os.environ, dict(extra, HOME=self.caller)):
            e = self.v.env()
        home = e.get("HOME", "")
        if home and home != self.caller:
            self.addCleanup(shutil.rmtree, home, True)
        return e

    def test_a_child_s_home_is_the_run_s_own(self):
        e = self.env()
        home = e["HOME"]
        self.assertNotEqual(home, self.caller)
        self.assertTrue(os.path.isdir(home), home)
        self.assertEqual(sorted(os.listdir(home)), [".config", ".gitconfig"])     # git's two files, row 8
        if _var_tmp_usable():
            self.assertTrue(home.startswith("/var/tmp/"), home)   # a HOME under /tmp is refused by the sandbox
        for key, sub in XDG:
            self.assertEqual(e.get(key), os.path.join(home, sub), key)

    def test_a_child_reaches_no_sign_in(self):
        e = self.env(DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus", **{k: "planted" for k in SIGN_INS})
        self.assertEqual(e.get("DBUS_SESSION_BUS_ADDRESS"), "disabled:")
        self.assertEqual([k for k in SIGN_INS if k in e], [])

    def test_the_run_s_home_is_one_and_goes_at_exit(self):
        code = ("import importlib.machinery, importlib.util, sys\n"
                f"loader = importlib.machinery.SourceFileLoader('v316', {str(ROOT / 'scripts' / 'verify')!r})\n"
                "spec = importlib.util.spec_from_loader('v316', loader)\n"
                "v = importlib.util.module_from_spec(spec); loader.exec_module(v)\n"
                "print(v.env()['HOME']); print(v.env()['HOME'])\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, HOME=self.caller))
        homes = r.stdout.split()
        self.assertEqual(len(homes), 2, r.stdout + r.stderr)
        self.assertEqual(homes[0], homes[1])
        self.assertNotEqual(homes[0], self.caller)
        self.assertFalse(os.path.lexists(homes[0]), homes[0])

    def test_one_home_serves_children_started_at_once(self):
        # impl round 1 (agy, opus; codex reproduced twice): the first calls come from verify's pool of module threads,
        # and an unlocked check-then-make gave two of them two HOMEs — one never removed
        real = self.v.tempfile.mkdtemp

        def slow_mkdtemp(*a, **k):
            d = real(*a, **k)
            self.addCleanup(shutil.rmtree, d, True)
            time.sleep(0.05)
            return d
        start, homes = threading.Barrier(8), []

        def call():
            start.wait()
            homes.append(self.v.env()["HOME"])
        with mock.patch.object(self.v.tempfile, "mkdtemp", slow_mkdtemp), \
                mock.patch.dict(os.environ, {"HOME": self.caller}):
            threads = [threading.Thread(target=call) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(len(homes), 8)
        self.assertEqual(len(set(homes)), 1, homes)

    def test_a_sign_in_the_list_was_not_written_for_is_cut_too(self):
        # impl round 1 (all four judges): a fixed list missed OPENROUTER_API_KEY (read by the plugin's own pi adapter),
        # CODEX_API_KEY, AWS credentials with Bedrock or Vertex, and the runtime folder where the session bus lives
        planted = ("OPENROUTER_API_KEY", "CODEX_API_KEY", "GROK_API_KEY", "SOMEVENDOR_API_KEY", "X_AUTH_TOKEN",
                   "SOME_OAUTH_TOKEN", "SOME_ACCESS_TOKEN", "AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                   "AWS_SESSION_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
        e = self.env(XDG_RUNTIME_DIR="/run/user/1000", **{k: "planted" for k in planted})
        self.assertEqual([k for k in planted if k in e], [])
        self.assertTrue(e.get("XDG_RUNTIME_DIR", "").startswith(e["HOME"] + "/"), e.get("XDG_RUNTIME_DIR"))

    def test_the_run_s_home_has_git_s_two_files_wherever_it_is_made(self):
        # impl round 1 (agy): with no usable /var/tmp the HOME is made under /tmp, and a sandboxed launch refuses a HOME
        # under /tmp that lacks git's two settings files
        real_access = os.access
        with mock.patch.object(self.v.os, "access",
                               lambda path, mode, *a, **k: False if str(path) == "/var/tmp" else real_access(path, mode)):
            e = self.env()
        home = Path(e["HOME"])
        for rel in (".gitconfig", ".config/git/config"):
            self.assertEqual((home / rel).read_text(encoding="utf-8") if (home / rel).is_file() else None, "", rel)

    def test_a_child_still_finds_the_agents_on_path(self):
        path = os.pathsep.join(["/opt/agents/bin", os.environ.get("PATH", "")])
        self.assertEqual(self.env(PATH=path).get("PATH"), path)


class TheSpendJournal(unittest.TestCase):

    def test_its_failed_judge_tests_start_no_agent(self):
        base = Path(tempfile.mkdtemp(prefix="pb316-shims-")).resolve()
        self.addCleanup(shutil.rmtree, base, True)
        shims, home, calls = base / "bin", base / "home", base / "calls.log"
        shims.mkdir()
        home.mkdir()
        for agent in ("codex", "grok", "agy", "claude", "pi"):
            (shims / agent).write_text(f'#!/bin/sh\necho "{agent} $*" >> "{calls}"\nexit 1\n', encoding="utf-8")
            (shims / agent).chmod(0o755)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PLAYBOOK_")}
        env.update(HOME=str(home), PATH=f"{shims}{os.pathsep}{os.environ.get('PATH', '')}",
                   PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-m", "unittest", *FAILED_JUDGE_TESTS], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertEqual(calls.read_text(encoding="utf-8") if calls.exists() else "", "")


if __name__ == "__main__":
    unittest.main()
