#!/usr/bin/env python3
"""A judge session's SessionStart hook neither fails nor writes (PLAN S12b, task 149).

Every claude judge runs with the user's hooks, in the project read-only and with
`PLAYBOOK_SESSION_ID=judge` (provider/adapters/claude.py). The SessionStart hook
then tried to write the `.claude/bin` wrappers, `mkdir .agent/sessions/judge` and
sweep dead session dirs — on a read-only project — and failed in every judge
session (task 146 F12: 292 times in this workspace's transcripts, 228 in WOW's).

Run: python3 -m unittest tests.test_judge_session_quiet
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


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else b"<dir>")
            for p in sorted(root.rglob("*")) if ".git" not in p.relative_to(root).parts}


class JudgeSessionStartIsQuiet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        # a dead session dir the GC would sweep in an agent session
        (self.project / ".agent" / "sessions" / "old-uuid").mkdir(parents=True)

    def _hook(self, session_id, readonly=False):
        env = dict(os.environ, PLAYBOOK_SESSION_ID=session_id,
                   CLAUDE_ENV_FILE=str(Path(self._tmp.name) / "env-file"))
        env.pop("BASH_ENV", None)
        env.pop("PLAYBOOK_ROLE", None)
        if readonly:
            for d in [self.project, *[p for p in self.project.rglob("*") if p.is_dir()
                                      and ".git" not in p.parts]]:
                d.chmod(0o555)
            self.addCleanup(lambda: [p.chmod(0o755) for p in [self.project, *self.project.rglob("*")]
                                     if p.is_dir()])
        return subprocess.run([bash_or_skip(), str(HOOK)], cwd=self.project, env=env, text=True,
                              input=json.dumps({"session_id": "x", "transcript_path": "/tmp/t.jsonl"}),
                              capture_output=True, timeout=60)

    def test_judge_session_exits_zero_silently_and_writes_nothing(self):
        # `judge` (panel and single judges) and `tail-cert` (the tail-certification
        # judge, impl panel r1 codex-high) are the two judge session ids.
        for sid in ("judge", "tail-cert"):
            with self.subTest(sid=sid):
                before = _snapshot(self.project)
                r = self._hook(sid)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual((r.stdout, r.stderr), ("", ""))
                self.assertEqual(_snapshot(self.project), before, f"the {sid} session's hook wrote")
                self.assertFalse((Path(self._tmp.name) / "env-file").exists())

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs POSIX permissions and a non-root user")
    def test_judge_session_on_a_read_only_project_does_not_fail(self):
        r = self._hook("judge", readonly=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")

    def test_an_agent_session_still_provisions(self):
        # Negative control: an ordinary session id still gets its wrappers, its
        # session dir and the sweep — the judge rule is not a blanket off switch.
        r = self._hook("sess-agent-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.project / ".agent" / "sessions" / "sess-agent-1").is_dir())
        self.assertTrue((self.project / ".claude" / "bin" / "tasks").is_file())
        self.assertIn("PLAYBOOK_SESSION_ID=sess-agent-1",
                      (Path(self._tmp.name) / "env-file").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
