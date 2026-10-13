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


def hook_env(tmp: Path, **extra) -> dict:
    """The environment of a hook run: none of the caller's session, sandbox or role (impl panel r1, gemini: a
    sandboxed run hid the write-failure line), a temp HOME, and a /proc with no agent in it, so a run under a nested
    agent — a judge running the suite — reads the chain these tests mean (gemini: the stop hook then wrote no mark and
    a test passed for the wrong reason)."""
    proc = tmp / "proc-no-agent"
    (proc / "self").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE", "PLAYBOOK_SANDBOXED", "PLAYBOOK_ROLE")}
    env.update(PLAYBOOK_SESSION_ID=SID, HOME=str(tmp), PLAYBOOK_PROC_ROOT=str(proc))
    env.update(extra)
    return env


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
        env = hook_env(self.tmp)
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
        env = hook_env(self.tmp)
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / "stop-hook")], cwd=p, env=env, text=True,
                           input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)          # every gate ticked: the stop is let through
        self.assertEqual(sorted(f.name for f in elsewhere.iterdir()), before, "a mark was written into the target")

    def test_control_the_stop_hooks_mark_in_a_plain_session(self):
        p = self.project("plain-stop")
        (p / ".agent" / "tasks" / "001-x" / "task.md").write_text(TASK.replace("- [ ] b", "- [x] b"), encoding="utf-8")
        env = hook_env(self.tmp)
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
                            str(self.tmp / "plain-counters")], text=True, capture_output=True, timeout=30,
                           env=hook_env(self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(f.name for f in elsewhere.iterdir()), ["counters"])
        self.assertEqual((elsewhere / "counters").read_text(encoding="utf-8"), "tools=1\n")
        self.assertEqual((self.tmp / "plain-counters").read_text(encoding="utf-8"), "tools=7\n")   # the control


class TheWritersTempPaths(unittest.TestCase):
    """impl panel r1 (codex, reproduced): `write_counter` and `write_session_mark` wrote `${file}.tmp.$$` without
    looking at it — a link planted there was written through, then renamed onto the state path. And gemini: a sink
    `write_counter` refuses is said (PLAYBOOK_WRITE_FAILED), not dropped in silence."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.sess = self.tmp / ".agent" / "sessions" / SID
        self.sess.mkdir(parents=True)
        self.victim = self.tmp / "victim"
        self.victim.write_text("keep\n", encoding="utf-8")

    def lib(self, script, *args):
        return subprocess.run([bash_or_skip(), "-c", 'source "$1"; shift; ' + script, "x",
                               str(SCRIPTS / "gate-echo-lib.sh"), *args], text=True, capture_output=True, timeout=30,
                              env=hook_env(self.tmp))

    def test_a_link_at_the_temp_path_is_not_written_through(self):
        for writer, call in (("write_counter", 'write_counter "$1" tools 7'),
                             ("write_session_mark", 'write_session_mark "$1" "3 abc"')):
            with self.subTest(writer=writer):
                target = self.sess / ("counters" if writer == "write_counter" else "turn_end")
                # the temp path is `<file>.tmp.<the shell's pid>`: planted from inside that shell, before the call
                r = self.lib('ln -s "$2" "$1.tmp.$$"; ' + call + '; echo "failed=${PLAYBOOK_WRITE_FAILED:-}"',
                             str(target), str(self.victim))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.victim.read_text(encoding="utf-8"), "keep\n", "written through the temp link")
                self.assertFalse(target.is_symlink(), "the temp link was renamed onto the state path")
                if writer == "write_counter":
                    self.assertIn("failed=true", r.stdout)

    def test_a_refused_counters_sink_is_said(self):
        (self.sess / "counters").symlink_to(self.victim)
        r = self.lib('PLAYBOOK_WRITE_FAILED=false; write_counter "$1" gate_key 001:9; '
                     'echo "failed=$PLAYBOOK_WRITE_FAILED"', str(self.sess / "counters"))
        self.assertIn("failed=true", r.stdout)
        self.assertEqual(self.victim.read_text(encoding="utf-8"), "keep\n")

    def test_control_the_writers_on_plain_paths(self):
        r = self.lib('PLAYBOOK_WRITE_FAILED=false; write_counter "$1" tools 7; write_session_mark "$2" "3 abc"; '
                     'echo "failed=$PLAYBOOK_WRITE_FAILED"', str(self.sess / "counters"), str(self.sess / "turn_end"))
        self.assertIn("failed=false", r.stdout)
        self.assertEqual((self.sess / "counters").read_text(encoding="utf-8"), "tools=7\n")
        self.assertEqual((self.sess / "turn_end").read_text(encoding="utf-8"), "3 abc\n")


class ALinkedSessionsDirectory(unittest.TestCase):
    """impl panel r1 (fable, opus, codex — codex reproduced it): with `.agent/sessions` ITSELF a link the leaf check
    saw a real `sessions/<id>`, and every hook wrote into the link's target. `.gitignore`'s `.agent/sessions/` matches
    directories only, so the link is committable, and it needs no session id."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.build("first")

    def build(self, name):
        """A project of its own whose `.agent/sessions` links to an empty victim of its own."""
        self.victim = self.tmp / name / "victim"
        self.victim.mkdir(parents=True)
        self.p = self.tmp / name / "proj"
        task = self.p / ".agent" / "tasks" / "001-x"
        task.mkdir(parents=True)
        (task / "task.md").write_text(TASK, encoding="utf-8")
        (self.p / ".agent" / "sessions").symlink_to(self.victim, target_is_directory=True)

    def run_hook(self, name, payload):
        env = hook_env(self.tmp, CLAUDE_ENV_FILE=str(self.tmp / "envfile"))
        return subprocess.run([bash_or_skip(), str(SCRIPTS / name)], cwd=self.p, env=env, text=True,
                              input=json.dumps(payload), capture_output=True, timeout=60)

    def test_no_hook_writes_into_the_links_target(self):
        calls = [("session-start-hook", {"hook_event_name": "SessionStart", "source": "startup",
                                         "transcript_path": str(self.tmp / "t.jsonl")}),
                 ("chat-log-hook", {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}),
                 ("task-gate-hook", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                     "tool_input": {"command": "ls"}}),
                 ("state-echo-hook", {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                      "tool_input": {"command": "ls"}, "transcript_path": str(self.tmp / "t.jsonl")}),
                 ("stop-hook", {"hook_event_name": "Stop", "stop_hook_active": False})]
        for name, payload in calls:
            with self.subTest(hook=name):
                self.build(name)
                r = self.run_hook(name, payload)
                self.assertIn(r.returncode, (0, 2), r.stderr)
                self.assertEqual(sorted(x.name for x in self.victim.iterdir()), [], f"{name} wrote into the target")

    def test_a_prompt_touches_nothing_of_a_session_behind_the_link(self):
        # the chat hook's mark removal and new generation ran on a session directory that already stands in the
        # target (the leaf is a real directory there): with `sessions` a link, nothing of it is touched
        planted = self.victim / SID
        planted.mkdir()
        # a stop's mark of the current generation and no baseline yet: what a notification would note (`notif_start`)
        for name, body in (("counters", "tools=4\nwrites=2\n"), ("turn_end", "4 g1-n1\n"), ("prompt_gen", "0 g1\n")):
            (planted / name).write_text(body, encoding="utf-8")
        before = {f.name: f.read_bytes() for f in planted.iterdir()}
        # a logged prompt (the reset), a notification (its baseline), one that is empty once its IDE tag is filtered
        # (the marks alone — nothing is logged for it), and a prompt whose chat log is refused as a sink (the skip)
        for prompt, sink in (("hello", None), ("<task-notification>done</task-notification>", None),
                             ("<ide_opened_file>a.py</ide_opened_file>", None), ("hello again", "link")):
            with self.subTest(prompt=prompt[:12], sink=sink):
                log = self.p / ".agent" / "chat_log.md"
                if sink and not log.is_symlink():
                    if log.exists():
                        log.unlink()
                    log.symlink_to(self.tmp / "elsewhere-log")
                r = self.run_hook("chat-log-hook", {"hook_event_name": "UserPromptSubmit", "prompt": prompt})
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual({f.name: f.read_bytes() for f in planted.iterdir()}, before)

    def test_control_session_start_records_its_pointer_in_a_plain_project(self):
        # the pointer session-start writes, through the inline python it carries (task 194: a re-indent once broke it,
        # silently — nothing else tested this hook's pointer)
        plain = self.tmp / "plain-start"
        (plain / ".agent" / "tasks").mkdir(parents=True)
        p, self.p = self.p, plain
        try:
            r = self.run_hook("session-start-hook", {"hook_event_name": "SessionStart", "source": "startup",
                                                     "transcript_path": str(self.tmp / "t.jsonl")})
        finally:
            self.p = p
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((plain / ".agent" / "sessions" / SID / "transcript_path").read_text(encoding="utf-8").strip(),
                         str(self.tmp / "t.jsonl"))

    def test_the_write_log_is_still_kept(self):
        # impl panel r2 (fable): the out-of-tree write log (under HOME, checked by write_log.py) is not session state —
        # the echo's stop for a linked `sessions` must not switch it off for every Edit/Write
        target = self.tmp / "first" / "proj" / "src" / "a.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1\n", encoding="utf-8")          # a PostToolUse runs after the Write: the file is there
        r = self.run_hook("state-echo-hook", {"hook_event_name": "PostToolUse", "tool_name": "Write",
                                              "tool_input": {"file_path": str(target), "content": "x = 1\n"}})
        self.assertEqual(r.returncode, 0, r.stderr)
        logs = [f for f in (self.tmp / ".local").rglob("*") if f.is_file()] if (self.tmp / ".local").exists() else []
        self.assertTrue(any(str(target) in f.read_text(encoding="utf-8", errors="replace") for f in logs),
                        f"no write-log entry for the Write: {logs}")
        self.assertEqual(sorted(x.name for x in self.victim.iterdir()), [])

    def test_the_stop_hook_holds_on_a_linked_sessions_directory(self):
        # what a planted session would read: counts that release (0 and 0) — the stop must hold
        (self.victim / SID).mkdir()
        (self.victim / SID / "current_state").write_text("001\n", encoding="utf-8")
        (self.victim / SID / "counters").write_text("tools=0\nwrites=0\n", encoding="utf-8")
        r = self.run_hook("stop-hook", {"hook_event_name": "Stop", "stop_hook_active": False})
        self.assertEqual(r.returncode, 2, "the low-activity release was taken through the link")


if __name__ == "__main__":
    unittest.main()
