"""The session sweepers delete nothing through a planted link (task 194, impl panel r1; grave, pre-existing).

Measured before the fix (task 194's `evidence/gc_link_probe.py`): in a project whose `.agent/sessions` is a link to a
directory holding `photos/ projects/ notes/ pid-999999/`, `session-start-hook` removed all four, and so did ANY tasks
CLI call (`_gc_dead_sessions` runs before every verb): each sweeper skipped a link AMONG the session directories but
never asked whether the sessions directory itself — or `.agent`, or a lane above it — is one, and a name that is not
`pid-*` with no fresh `current_state` reads as a dead session. A cloned repository can ship such a link (`.gitignore`'s
`.agent/sessions/` matches directories only). Now neither sweeper, nor session-end's removal of its own directory, acts
on a sessions directory that does not lie physically inside the project: nothing is removed, and the hook says why.
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

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"
DIRS = ("photos", "projects", "notes", "pid-999999")


class TheSweepersFollowNoLink(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "home").mkdir()

    def victim(self, name="victim"):
        v = self.tmp / name
        for d in DIRS:
            (v / d).mkdir(parents=True)
            (v / d / "keep.txt").write_text("the owner's data\n", encoding="utf-8")
        return v

    def project(self, name):
        p = self.tmp / name
        (p / ".agent" / "tasks").mkdir(parents=True)
        return p

    def env(self):
        proc = self.tmp / "proc-no-agent"           # a /proc with no agent: the chain these tests mean
        (proc / "self").mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE", "PLAYBOOK_SANDBOXED", "PLAYBOOK_ROLE")}
        env.update(HOME=str(self.tmp / "home"), PLAYBOOK_SESSION_ID="sweep-sess",
                   CLAUDE_ENV_FILE=str(self.tmp / "envfile"), PLAYBOOK_PROC_ROOT=str(proc))
        return env

    def start(self, p):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / "session-start-hook")], cwd=p, env=self.env(), text=True,
                              input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                              capture_output=True, timeout=60)

    def cli(self, p, *verb):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / "tasks"), *verb], cwd=p, env=self.env(), text=True,
                              capture_output=True, timeout=120)

    def end(self, p):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / "session-end-hook")], cwd=p, env=self.env(), text=True,
                              input=json.dumps({"hook_event_name": "SessionEnd", "reason": "exit",
                                                "session_id": "sweep-sess"}),
                              capture_output=True, timeout=60)

    def assertUntouched(self, v):
        for d in DIRS:
            self.assertTrue((v / d / "keep.txt").is_file(), f"{d} was removed through the link")

    def test_a_linked_sessions_directory_is_not_swept(self):
        for who in ("session-start", "cli list", "cli status"):
            with self.subTest(sweeper=who):
                v = self.victim(f"v-{who.replace(' ', '-')}")
                p = self.project(f"p-{who.replace(' ', '-')}")
                (p / ".agent" / "sessions").symlink_to(v, target_is_directory=True)
                r = self.start(p) if who == "session-start" else self.cli(p, who.split()[1])
                self.assertUntouched(v)
                if who == "session-start":
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertIn("is a link", r.stderr)

    def test_a_linked_agent_or_lane_directory_is_not_swept(self):
        for shape in ("agent", "lane"):
            with self.subTest(shape=shape):
                v = self.tmp / f"state-{shape}"
                self.victim(f"state-{shape}/sessions")
                (v / "tasks").mkdir(parents=True, exist_ok=True)
                p = self.tmp / f"p-{shape}"
                if shape == "agent":
                    p.mkdir()
                    (p / ".agent").symlink_to(v, target_is_directory=True)
                else:
                    (p / ".agent").mkdir(parents=True)
                    (p / ".agent" / "current_user").write_text("alice\n", encoding="utf-8")
                    (p / ".agent" / "alice").symlink_to(v, target_is_directory=True)
                self.start(p)
                self.cli(p, "list")
                self.assertUntouched(v / "sessions")

    def test_session_end_removes_nothing_through_a_linked_sessions_directory(self):
        v = self.victim()
        (v / "sweep-sess").mkdir()
        (v / "sweep-sess" / "keep.txt").write_text("the owner's data\n", encoding="utf-8")
        p = self.project("p-end")
        (p / ".agent" / "sessions").symlink_to(v, target_is_directory=True)
        r = self.end(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((v / "sweep-sess" / "keep.txt").is_file(), "session-end removed a directory through the link")

    def test_control_a_plain_project_is_swept_as_before(self):
        for who in ("session-start", "cli"):
            with self.subTest(sweeper=who):
                p = self.project(f"plain-{who}")
                sessions = p / ".agent" / "sessions"
                (sessions / "old-legacy").mkdir(parents=True)
                (sessions / "old-legacy" / "current_state").write_text("001\n", encoding="utf-8")
                old = time.time() - 3 * 86400
                os.utime(sessions / "old-legacy" / "current_state", (old, old))
                (sessions / "fresh-legacy").mkdir()
                (sessions / "fresh-legacy" / "current_state").write_text("001\n", encoding="utf-8")
                self.start(p) if who == "session-start" else self.cli(p, "list")
                self.assertFalse((sessions / "old-legacy").exists(), "a dead session was not swept")
                self.assertTrue((sessions / "fresh-legacy").is_dir())

    def test_control_session_end_removes_its_own_plain_directory(self):
        p = self.project("plain-end")
        (p / ".agent" / "sessions" / "sweep-sess").mkdir(parents=True)
        r = self.end(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((p / ".agent" / "sessions" / "sweep-sess").exists())


if __name__ == "__main__":
    unittest.main()
