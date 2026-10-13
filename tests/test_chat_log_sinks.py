"""The chat hook writes nothing through a planted link and never waits on a FIFO (task 193, PLAN S14 T4; task 192's class).

Measured on the candidate (task 193's evidence, `chatlog_probe.sh`): with `.agent/chat_log.md` a link, the user's prompt
was appended to the link's target; with `.agent/chat_log_counter` a link, the target was TRUNCATED to `1`; with either a
FIFO the hook waited until it was killed. A cloned repository can ship any of them under `.agent/`. Now the hook checks
every file it writes (the log, its counter, the counter's lock, the migration's `.new` and `.bak`) before it writes any:
one that is a link or not a regular file is not this hook's — it says so and logs nothing; the prompt goes through.
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
LEAVES = ("chat_log.md", "chat_log_counter", "chat_log_counter.lock", "chat_log.md.new", "chat_log.md.bak")


class TheChatHooksSinks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def project(self, name):
        p = self.tmp / name / "proj"
        (p / ".agent" / "tasks").mkdir(parents=True)
        return p

    def hook(self, project):
        env = {k: v for k, v in os.environ.items() if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE")}
        env.update(PLAYBOOK_SESSION_ID="chat-sinks", HOME=str(self.tmp))
        t0 = time.monotonic()
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / "chat-log-hook")], cwd=project, env=env, text=True,
                           input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "a prompt of the user's"}),
                           capture_output=True, timeout=60)
        return r, time.monotonic() - t0

    def test_no_write_through_a_link_and_no_wait_on_a_fifo(self):
        for leaf in LEAVES:
            for kind in ("link", "fifo"):
                with self.subTest(leaf=leaf, kind=kind):
                    p = self.project(f"{leaf}-{kind}")
                    victim = p.parent / "victim"
                    victim.write_text("keep\n", encoding="utf-8")
                    if kind == "link":
                        (p / ".agent" / leaf).symlink_to(victim)
                    else:
                        os.mkfifo(p / ".agent" / leaf)
                    r, secs = self.hook(p)
                    self.assertLess(secs, 4.0, "the chat hook waited on its sink")
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(victim.read_text(encoding="utf-8"), "keep\n")
                    self.assertIn(leaf, r.stderr)

    # impl panel r1 (fable, sonnet): the reset writes the session's own files too — `counters` in place, and
    # `counts_unsure` when the counters lock is not given
    def test_the_session_counters_are_not_written_through_a_link_or_into_a_fifo(self):
        for kind in ("link", "fifo", "linked-dir"):
            with self.subTest(kind=kind):
                p = self.project(f"counters-{kind}")
                sessions = p / ".agent" / "sessions"
                sessions.mkdir()
                victim = p.parent / "victim"
                victim.write_text("keep\n", encoding="utf-8")
                if kind == "linked-dir":
                    elsewhere = p.parent / "elsewhere"
                    elsewhere.mkdir()
                    (elsewhere / "counters").write_text("keep\n", encoding="utf-8")
                    (sessions / "chat-sinks").symlink_to(elsewhere, target_is_directory=True)
                    victim = elsewhere / "counters"
                else:
                    (sessions / "chat-sinks").mkdir()
                    if kind == "link":
                        (sessions / "chat-sinks" / "counters").symlink_to(victim)
                    else:
                        os.mkfifo(sessions / "chat-sinks" / "counters")
                r, secs = self.hook(p)
                self.assertLess(secs, 4.0, "the reset waited on the counters")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(victim.read_text(encoding="utf-8"), "keep\n")

    def test_the_unsure_mark_is_not_written_through_a_link(self):
        p = self.project("unsure-link")
        sess = p / ".agent" / "sessions" / "chat-sinks"
        sess.mkdir(parents=True)
        (sess / "counters.lock").mkdir()            # no plain file in the lock's place: the lock is not given
        victim = p.parent / "victim"
        victim.write_text("keep\n", encoding="utf-8")
        (sess / "counts_unsure").symlink_to(victim)
        r, secs = self.hook(p)
        self.assertLess(secs, 6.0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep\n")

    def test_control_a_plain_project_is_logged(self):
        p = self.project("plain")
        r, _ = self.hook(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("a prompt of the user's", (p / ".agent" / "chat_log.md").read_text(encoding="utf-8"))
        self.assertEqual((p / ".agent" / "chat_log_counter").read_text(encoding="utf-8").strip(), "1")


if __name__ == "__main__":
    unittest.main()
