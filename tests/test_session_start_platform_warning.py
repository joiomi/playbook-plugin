#!/usr/bin/env python3
"""SessionStart warns, without blocking, on a platform other than Linux (owner, 2026-10-08).

Playbook is Linux-only since 2026-10-08; 1.5.47 (branch `multiplatform`, tag
`last-multiplatform`) is the last version tested on macOS and Windows/Git Bash.
A session started anywhere else gets one English sentence (owner ruling
2026-10-08) on stderr and, for the host, as a JSON `systemMessage` +
`additionalContext` on stdout — and the hook
still exits 0. The platform comes from `uname -s`; a fake `uname` first on PATH
plays the other platforms here.

Run: python3 -m unittest tests.test_session_start_platform_warning
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

HOOK = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts" / "session-start-hook"
MESSAGE = ("playbook: unsupported platform ({}); Linux only since 2026-10-08 — "
           "on Windows/macOS use the `multiplatform` branch (1.5.47)")


class SessionStartPlatformWarning(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.project = root / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.fakebin = root / "fakebin"
        self.fakebin.mkdir()
        self.env_file = root / "env-file"

    def _hook(self, uname_s, session_id="sess-agent-1"):
        uname = self.fakebin / "uname"
        uname.write_text(f"#!/bin/sh\necho '{uname_s}'\n", encoding="utf-8")
        uname.chmod(0o755)
        env = dict(os.environ, PLAYBOOK_SESSION_ID=session_id, CLAUDE_ENV_FILE=str(self.env_file),
                   PATH=f"{self.fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
        env.pop("BASH_ENV", None)
        env.pop("PLAYBOOK_ROLE", None)
        return subprocess.run([bash_or_skip(), str(HOOK)], cwd=self.project, env=env,
                              input=json.dumps({"session_id": "x", "transcript_path": "/tmp/t.jsonl"}),
                              capture_output=True, timeout=60, encoding="utf-8")

    def test_another_platform_is_warned_and_the_session_still_starts(self):
        for platform in ("Darwin", "MINGW64_NT-10.0-26100", "CYGWIN_NT-10.0"):
            with self.subTest(platform=platform):
                r = self._hook(platform)
                want = MESSAGE.format(platform)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(want, r.stderr.splitlines())
                out = json.loads(r.stdout)        # exactly one JSON document on stdout
                self.assertEqual(out["systemMessage"], want)
                self.assertEqual(out["hookSpecificOutput"],
                                 {"hookEventName": "SessionStart", "additionalContext": want})
                # not a block: the session is provisioned as on Linux
                self.assertTrue((self.project / ".agent" / "sessions" / "sess-agent-1").is_dir())

    def test_linux_is_not_warned(self):
        # Control: the same hook, the same fake-uname mechanism, a Linux answer.
        r = self._hook("Linux")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertNotIn("unsupported platform", r.stderr)

    def test_a_judge_session_stays_silent_on_any_platform(self):
        # Task 149: a judge's SessionStart prints nothing — its context is the review prompt.
        for sid in ("judge", "tail-cert"):
            with self.subTest(sid=sid):
                r = self._hook("Darwin", session_id=sid)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual((r.stdout, r.stderr), ("", ""))


if __name__ == "__main__":
    unittest.main()
