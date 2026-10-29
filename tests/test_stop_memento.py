"""The stop hook never blocks: it leaves a memento (task 340, PLAN-PLAYBOOK-2.md 4.1 b, part 2).

The owner approved it with the plan (R14; PLAN line 283: "hook-ul de oprire „Îl păstrăm” (auditul 1): rămâne ca
memento, doar blocarea pleacă (214 alarme false din 223)"). Where the old hook exited 2 — a working turn that ended with
the active task's gates open, an unresolved session, a malformed lane marker, the same below the python floor — it now
exits 0 and prints Claude Code's `{"systemMessage": …}`, shown to the person. A chat reply (writes 0, tools < 5), a
blocked task, a task with every gate checked, no active task and the Freehand gate print nothing. The notification
release and its three marks (`turn_end`, `notif_start`, `prompt_gen`) are gone: there is nothing to release.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip
from tests._oldpython import make_oldpython_path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "playbook" / "scripts"
SID = "s340-stop"
GATES = "## Work Plan\n- [x] first done\n  - noted\n- [ ] Fix the parser\n- [ ] Verify it\n"


class _Project(unittest.TestCase):
    """A project whose session points at task 001, with the counters of a working turn."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.proj = self.base / "proj"
        self.task = self.proj / ".agent" / "tasks" / "001-x" / "task.md"
        self.task.parent.mkdir(parents=True)
        self.write_task(GATES)
        self.sess = self.proj / ".agent" / "sessions" / SID
        self.sess.mkdir(parents=True)
        (self.sess / "current_state").write_text("001\n", encoding="utf-8")
        self.counts(tools=12, writes=4)
        self.proc = self.base / "proc-no-agent"          # a /proc with no agent: the session is the env id alone
        (self.proc / "self").mkdir(parents=True)

    def write_task(self, body, status="in_progress"):
        self.task.write_text(f"# 001 - x\n\n## Status\n{status}\n\n## Risk\nreversible\n\n{body}", encoding="utf-8")

    def counts(self, tools, writes):
        (self.sess / "counters").write_text(f"tools={tools}\nwrites={writes}\n", encoding="utf-8")

    def env(self, sid=SID, path=None):
        env = dict(os.environ, PLAYBOOK_PROC_ROOT=str(self.proc))
        for k in ("BASH_ENV", "PLAYBOOK_SANDBOXED", "CLAUDE_PROJECT_DIR", "PLAYBOOK_EVAL_CONFIG", "PLAYBOOK_ROLE",
                  "PLAYBOOK_SESSION_ID", "PLAYBOOK_PROVIDER"):
            env.pop(k, None)
        if sid:
            env["PLAYBOOK_SESSION_ID"] = sid
        if path is not None:
            env["PATH"] = path
        return env

    def hook(self, name, payload, **kw):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / name)], cwd=self.proj, env=self.env(**kw), text=True,
                              input=json.dumps(payload), capture_output=True, timeout=60)

    def stop(self, active=False, **kw):
        return self.hook("stop-hook", {"session_id": SID, "hook_event_name": "Stop", "stop_hook_active": active}, **kw)

    def memento(self, r) -> str:
        """The stop's memento: exit 0 and exactly one JSON object with a `systemMessage` on stdout."""
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = [line for line in r.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, r.stdout)
        msg = json.loads(lines[0])["systemMessage"]
        self.assertIsInstance(msg, str)
        return msg

    def silent(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "", r.stdout)


class TheMemento(_Project):

    def test_a_working_turn_with_open_gates_ends_with_the_memento(self):
        msg = self.memento(self.stop())
        self.assertIn("task 001", msg)
        self.assertIn("2 open gate", msg)
        self.assertIn("Fix the parser", msg)

    def test_no_session_ends_with_a_memento(self):
        msg = self.memento(self.stop(sid=None))
        self.assertIn("could not", msg)

    def test_an_invalid_marker_ends_with_a_memento(self):
        (self.proj / ".agent" / "current_user").write_text("alice@evil\n", encoding="utf-8")
        (self.proj / ".agent" / "alice" / "tasks").mkdir(parents=True)
        msg = self.memento(self.stop())
        self.assertIn("current_user", msg)

    def test_below_the_floor_the_memento_comes_from_grep(self):
        old = make_oldpython_path(self.base / "oldpy")
        msg = self.memento(self.stop(path=old))
        self.assertIn("task 001", msg)
        self.assertIn("2 open gate", msg)
        self.assertIn("Fix the parser", msg)

    def test_the_memento_is_json_whatever_the_gate_says(self):
        for gate in ('a "quoted" one \\ with\ta tab', "a control \x01 byte", "ăîșț — non-ASCII", "x" * 10000):
            with self.subTest(gate=gate[:30]):
                self.write_task(f"## Work Plan\n- [ ] {gate}\n")
                msg = self.memento(self.stop())
                self.assertIn("task 001", msg)
                self.assertNotIn("\x01", msg)
                self.assertLess(len(msg), 1000)
        self.write_task("## Work Plan\n- [ ] ăîșț — non-ASCII\n")
        self.assertIn("ăîșț", self.memento(self.stop()))

    def test_a_reissued_stop_is_no_different(self):
        msg = self.memento(self.stop(active=True))
        self.assertIn("2 open gate", msg)


class NoMemento(_Project):

    def test_a_chat_reply(self):
        self.counts(tools=3, writes=0)
        self.silent(self.stop())

    def test_a_blocked_task(self):
        self.write_task(GATES, status="blocked")
        self.silent(self.stop())

    def test_all_gates_checked(self):
        self.write_task("## Work Plan\n- [x] done\n  - noted\n")
        self.silent(self.stop())

    def test_no_active_task(self):
        (self.sess / "current_state").unlink()
        self.silent(self.stop())

    def test_the_freehand_gate(self):
        self.write_task("## Work Plan\n- [x] done\n  - noted\n\n## Debrief\n- [ ] Freehand — stay to discuss\n")
        self.silent(self.stop())


class TheMarksAreGone(_Project):

    def prompt(self, text):
        return self.hook("chat-log-hook", {"session_id": SID, "hook_event_name": "UserPromptSubmit", "prompt": text})

    def test_no_hook_writes_a_turn_mark(self):
        self.assertEqual(self.prompt("fix the parser").returncode, 0)
        self.counts(tools=12, writes=4)
        self.stop()
        self.assertEqual(self.prompt("<task-notification>a background job ended</task-notification>").returncode, 0)
        self.stop()
        self.assertEqual(sorted(p.name for p in self.sess.iterdir()
                                if p.name in ("turn_end", "notif_start", "prompt_gen")), [])


class TheCounters(_Project):

    def prompt(self, text):
        return self.hook("chat-log-hook", {"session_id": SID, "hook_event_name": "UserPromptSubmit", "prompt": text})

    def read(self):
        return dict(line.split("=", 1) for line in (self.sess / "counters").read_text(encoding="utf-8").split())

    def test_a_prompt_resets_and_a_notification_keeps_the_counts(self):
        self.assertEqual(self.prompt("<task-notification>a background job ended</task-notification>").returncode, 0)
        self.assertEqual(self.read(), {"tools": "12", "writes": "4"})
        self.assertEqual(self.prompt("now the tests").returncode, 0)
        self.assertEqual(self.read(), {"tools": "0", "writes": "0"})


if __name__ == "__main__":
    unittest.main()
