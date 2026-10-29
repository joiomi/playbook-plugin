"""The chat log's duplicate check includes the session (task 337, grave E of DESIGN-PLAYBOOK-2.md §7).

`chat-log-hook` skipped a prompt whose second and text equalled the last entry's, whatever its session, so two
sessions that received the same "continue" in one second logged only one of them: an owner's message was missing from
chat_log.md. A prompt is now skipped only when the newest `M` entry has the same second, the same
`(provider/session)` and the same text (the hook's own double run); with the session unresolved nothing is skipped.

A `date` stand-in first in PATH fixes the hook's second (`date -u +"%Y-%m-%d %H:%M:%S UTC"`); every other `date`
call reaches the real one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "plugins" / "playbook" / "scripts" / "chat-log-hook"
SECOND = "2026-10-29 07:00:00 UTC"


class SameSecond(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.project = root / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        self.log = self.project / ".agent" / "chat_log.md"
        self.bin = root / "bin"
        self.bin.mkdir()
        real = shutil.which("date")
        shim = self.bin / "date"
        shim.write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = "-u" ] && [ "$2" = "+%Y-%m-%d %H:%M:%S UTC" ]; then echo "{SECOND}"; exit 0; fi\n'
            f'exec {real} "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        self.proc = root / "proc-no-agent"            # a /proc with no agent in it (as test_chat_log_writer)
        (self.proc / "self").mkdir(parents=True)

    def _run(self, prompt, session):
        env = dict(os.environ)
        env.pop("BASH_ENV", None)
        env.pop("PLAYBOOK_SANDBOXED", None)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["PLAYBOOK_PROC_ROOT"] = str(self.proc)
        if session:
            env["PLAYBOOK_SESSION_ID"] = session
        else:
            env.pop("PLAYBOOK_SESSION_ID", None)
        r = subprocess.run([bash_or_skip(), str(HOOK)], cwd=self.project, env=env, text=True,
                           input=json.dumps({"prompt": prompt}), capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _entries(self):
        text = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        return re.findall(r"^\*\*\[M\d+\]\*\* \[([^\]]+)\] `HOST` \(([^)]*)\)$", text, re.M)

    def test_two_sessions_both_logged(self):
        self._run("continue", "pid-one")
        self._run("continue", "pid-two")
        self.assertEqual(self._entries(), [(SECOND, "claude/pid-one"), (SECOND, "claude/pid-two")])

    def test_one_session_twice_logged_once(self):
        self._run("continue", "pid-one")
        self._run("continue", "pid-one")
        self.assertEqual(self._entries(), [(SECOND, "claude/pid-one")])

    def test_unresolved_session_never_skipped(self):
        self._run("continue", None)
        self._run("continue", None)
        entries = self._entries()
        self.assertEqual(len(entries), 2, entries)

    def test_different_text_both_logged(self):
        self._run("continue", "pid-one")
        self._run("stop", "pid-one")
        self.assertEqual(len(self._entries()), 2)


if __name__ == "__main__":
    unittest.main()
