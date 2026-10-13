"""The state echo writes nothing through a planted link and never waits on a FIFO (task 194, PLAN S14 T5a).

Tasks 192 and 193 measured the class on the journal, the chat hook and the counters' reset: a link planted under
`.agent/` (a cloned repository can ship one) was written through — a counter's target truncated, a prompt appended to
another file — and a FIFO held the hook until the host's timeout. The state echo runs after every tool call and writes
its transcript pointer, its gate log in `chat_log.md` (under `chat_log_counter.lock`) and the session's counters. Each
of those sinks is now checked first; one that is a link or not a regular file — or a session directory that is itself
a link — is not the echo's: nothing is written there, and the echo answers at once.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts"
SID = "echo-sinks"
TASK = "# 001 - x\n\n## Status\nin_progress\n\n## Work Plan\n- [x] a — done\n- [ ] b\n"


class TheEchosSinks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def project(self, name, session_dir=None):
        p = self.tmp / name / "proj"
        task = p / ".agent" / "tasks" / "001-x"
        task.mkdir(parents=True)
        (task / "task.md").write_text(TASK, encoding="utf-8")
        sess = session_dir or (p / ".agent" / "sessions" / SID)
        sess.mkdir(parents=True, exist_ok=True)
        (sess / "current_state").write_text("001\n", encoding="utf-8")
        # the previous gate is `a` (line 7): this call logs its completion into chat_log.md
        (sess / "counters").write_text("tools=1\nwrites=1\ngate_key=001:7\ngate_count=3\ngate_text=a\n",
                                       encoding="utf-8")
        return p

    def echo(self, project):
        env = {k: v for k, v in os.environ.items() if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE")}
        env.update(PLAYBOOK_SESSION_ID=SID, HOME=str(self.tmp))
        payload = {"tool_name": "Read", "tool_input": {"file_path": "x"},
                   "transcript_path": str(self.tmp / "transcript.jsonl")}
        t0 = time.monotonic()
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / "state-echo-hook")], cwd=project, env=env, text=True,
                           input=json.dumps(payload), capture_output=True, timeout=60)
        return r, time.monotonic() - t0

    def test_control_a_plain_session_is_written(self):
        p = self.project("plain")
        r, _ = self.echo(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        sess = p / ".agent" / "sessions" / SID
        self.assertEqual((sess / "transcript_path").read_text(encoding="utf-8").strip(), str(self.tmp / "transcript.jsonl"))
        self.assertIn("- [x] a", (p / ".agent" / "chat_log.md").read_text(encoding="utf-8"))
        self.assertIn("tools=2", (sess / "counters").read_text(encoding="utf-8"))

    def test_no_write_through_a_link_and_no_wait_on_a_fifo(self):
        for leaf in ("sessions/echo-sinks/transcript_path", "chat_log.md", "chat_log_counter.lock"):
            for kind in ("link", "fifo"):
                with self.subTest(leaf=leaf, kind=kind):
                    p = self.project(f"{leaf.replace('/', '_')}-{kind}")
                    victim = p.parent / "victim"
                    victim.write_text("keep\n", encoding="utf-8")
                    target = p / ".agent" / leaf
                    if kind == "link":
                        target.symlink_to(victim)
                    else:
                        os.mkfifo(target)
                    r, secs = self.echo(p)
                    self.assertLess(secs, 4.0, "the echo waited on a FIFO")
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(victim.read_text(encoding="utf-8"), "keep\n")
                    if leaf != "sessions/echo-sinks/transcript_path":
                        # the entry not written is said, and the gate moves on
                        out = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
                        self.assertIn("gate-logging write FAILED", out)
                        self.assertIn("gate_key=001:8", (p / ".agent" / "sessions" / SID / "counters").read_text(encoding="utf-8"))

    def test_a_linked_session_directory_is_not_written(self):
        elsewhere = self.tmp / "elsewhere"
        p = self.project("linked-dir", session_dir=elsewhere)
        (p / ".agent" / "sessions").mkdir(parents=True, exist_ok=True)
        (p / ".agent" / "sessions" / SID).symlink_to(elsewhere, target_is_directory=True)
        before = {f.name: f.read_bytes() for f in elsewhere.iterdir()}
        r, secs = self.echo(p)
        self.assertLess(secs, 4.0)
        self.assertEqual(r.returncode, 0, r.stderr)
        after = {f.name: f.read_bytes() for f in elsewhere.iterdir()}
        self.assertEqual(after, before, "the echo wrote into the linked session directory's target")
        self.assertIn("is a link", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])


    def test_the_stop_hooks_mark_is_not_written_into_a_linked_session_directory(self):
        # the routed half (PLAN S14, "Routed by task 192"): `write_session_mark`'s temp file and rename landed in a
        # LINKED session directory's target — the stop hook's `turn_end` after a stop it lets through
        elsewhere = self.tmp / "elsewhere-stop"
        p = self.project("linked-stop", session_dir=elsewhere)
        (p / ".agent" / "tasks" / "001-x" / "task.md").write_text(TASK.replace("- [ ] b", "- [x] b"), encoding="utf-8")
        (p / ".agent" / "sessions").mkdir(parents=True, exist_ok=True)
        (p / ".agent" / "sessions" / SID).symlink_to(elsewhere, target_is_directory=True)
        before = sorted(f.name for f in elsewhere.iterdir())
        env = {k: v for k, v in os.environ.items() if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE")}
        env.update(PLAYBOOK_SESSION_ID=SID, HOME=str(self.tmp))
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / "stop-hook")], cwd=p, env=env, text=True,
                           input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)          # every gate ticked: the stop is let through
        self.assertEqual(sorted(f.name for f in elsewhere.iterdir()), before, "a mark was written into the target")

    def test_control_the_stop_hooks_mark_in_a_plain_session(self):
        p = self.project("plain-stop")
        (p / ".agent" / "tasks" / "001-x" / "task.md").write_text(TASK.replace("- [ ] b", "- [x] b"), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE")}
        env.update(PLAYBOOK_SESSION_ID=SID, HOME=str(self.tmp))
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / "stop-hook")], cwd=p, env=env, text=True,
                           input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((p / ".agent" / "sessions" / SID / "turn_end").is_file())


    def test_write_counter_writes_nothing_inside_a_linked_session_directory(self):
        # the lib's own check (the echo stops before it on a linked directory; another caller would not)
        elsewhere = self.tmp / "elsewhere-lib"
        elsewhere.mkdir()
        (elsewhere / "counters").write_text("tools=1\n", encoding="utf-8")
        sessions = self.tmp / "lib" / ".agent" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / SID).symlink_to(elsewhere, target_is_directory=True)
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; write_counter "$2" tools 7; write_counter "$3" tools 7',
                            "x", str(SCRIPTS / "gate-echo-lib.sh"), str(sessions / SID / "counters"),
                            str(self.tmp / "plain-counters")], text=True, capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(f.name for f in elsewhere.iterdir()), ["counters"])
        self.assertEqual((elsewhere / "counters").read_text(encoding="utf-8"), "tools=1\n")
        self.assertEqual((self.tmp / "plain-counters").read_text(encoding="utf-8"), "tools=7\n")   # the control


if __name__ == "__main__":
    unittest.main()
