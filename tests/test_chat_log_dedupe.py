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
            f'if [ "$1" = "-u" ] && [ "$2" = "+%Y-%m-%d %H:%M:%S UTC" ]; then echo "${{PB_TEST_SECOND:-{SECOND}}}"; exit 0; fi\n'
            f'exec {real} "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        self.proc = root / "proc-no-agent"            # a /proc with no agent in it (as test_chat_log_writer)
        (self.proc / "self").mkdir(parents=True)

    def _run(self, prompt, session, second=None, provider=None):
        env = dict(os.environ)
        env.pop("PLAYBOOK_PROVIDER", None)
        if second:
            env["PB_TEST_SECOND"] = second
        if provider:
            env["PLAYBOOK_PROVIDER"] = provider
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
        self.assertEqual(self._entries(), [(SECOND, "claude/no-session"), (SECOND, "claude/no-session")])

    def test_the_same_text_a_second_later_is_logged(self):
        """Row 5 (impl round 1, opus): the second is part of the key — a mutant without it would drop a session's
        "continue" sent again later."""
        self._run("continue", "pid-one")
        self._run("continue", "pid-one", second="2026-10-29 07:00:59 UTC")
        self.assertEqual(self._entries(), [(SECOND, "claude/pid-one"), ("2026-10-29 07:00:59 UTC", "claude/pid-one")])

    def test_another_provider_with_the_same_session_id_is_logged(self):
        """Row 6 (impl round 1, sonnet): the provider is part of the key."""
        self._run("continue", "pid-one")
        self._run("continue", "pid-one", provider="codex")
        self.assertEqual(self._entries(), [(SECOND, "claude/pid-one"), (SECOND, "codex/pid-one")])

    def test_the_compared_text_is_the_newest_prompts_own(self):
        """Row 7 (impl round 1, codex, opus): the key is one whole entry. The newest prompt entry is A's `start`, and an
        entry after it that is not a prompt ends the file with `continue`; A's `continue` in the same second is its
        own prompt, logged — the old check took the header and the last line from two entries and dropped it."""
        self.log.write_text(
            "# Project Chat Log\n\nUser messages logged with timestamps.\n\n"
            f"---\n\n**[M001]** [{SECOND}] `HOST` (claude/pid-a)\n\nstart\n\n"
            f"---\n\n**[G7:12]** [{SECOND}] `HOST` (3 tool calls)\n\ncontinue\n\n", encoding="utf-8")
        (self.project / ".agent" / "chat_log_counter").write_text("1\n", encoding="utf-8")
        self._run("continue", "pid-a")
        self.assertEqual(self._entries(), [(SECOND, "claude/pid-a"), (SECOND, "claude/pid-a")])

    def test_a_multi_line_prompt_run_twice_is_logged_once(self):
        """Row 8: the whole body is compared, so a multi-line prompt's double run is one entry too."""
        self._run("line one\nline two", "pid-one")
        self._run("line one\nline two", "pid-one")
        self.assertEqual(self._entries(), [(SECOND, "claude/pid-one")])

    def test_different_text_both_logged(self):
        self._run("continue", "pid-one")
        self._run("stop", "pid-one")
        self.assertEqual(len(self._entries()), 2)


if __name__ == "__main__":
    unittest.main()
