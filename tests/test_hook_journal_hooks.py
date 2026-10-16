"""Every playbook bash hook writes one line per run to the hook journal (PHASE-B item 2, task 222).

The owner, 2026-10-16 10:25: "e important ca playbook sa isi salveze infomratiile legat de ce face" — the line says
which hook ran, for which event and tool call, what it decided and why. Plan panel 2026-10-16 (sonnet I4): a decision
set inside `$(…)` would be lost, so this file is a TABLE — one crafted payload per exit site of distinct meaning in
the task gate, the state echo, the stop hook and the task-folder watch, and the others' main ways out — asserting the
one new line's hook, decision, detail and exit status. The tool-call id comes from the payload's own key (the
normalizer's frame, v4), never a key nested in the tool's input.

The REAL hooks run as subprocesses in a temp project with a /proc that holds no agent and a temp HOME (the review-trap
checklist). Run: python3 -m unittest tests.test_hook_journal_hooks
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"
SID = "pid-hook222x"
TASK = "# 001 - x\n\n## Status\n{status}\n\n## Work Plan\n{gates}\n"


def pre(tool, tool_input, tid="toolu_PRE1", **extra):
    d = {"session_id": "host-1", "transcript_path": "/t", "cwd": "/c", "permission_mode": "default",
         "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "tool_use_id": tid}
    d.update(extra)
    return d


def post(tool, tool_input, tid="toolu_POST1", event="PostToolUse", head=None, **extra):
    """A Post payload in Claude's key order: `head` keys before `tool_input`, `extra` (a `tool_response`, an
    `agent_id`…) after it; a PostToolUse without a given `tool_response` gets `{}`."""
    d = {"session_id": "host-1", "transcript_path": "/t", "cwd": "/c", "permission_mode": "default", **(head or {}),
         "hook_event_name": event, "tool_name": tool, "tool_input": tool_input}
    d.update(extra)
    if event == "PostToolUse":
        d.setdefault("tool_response", {})
    d["tool_use_id"] = tid
    if event == "PostToolUseFailure":
        d["error"] = "Exit code 1"
    return d


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = self.root / "proj"
        self.agent = self.project / ".agent"
        (self.agent / "tasks" / "001-x").mkdir(parents=True)
        self.task_file = self.agent / "tasks" / "001-x" / "task.md"
        self.set_task("in_progress", "- [ ] a gate that is open\n- [ ] another one")
        self.session_dir = self.agent / "sessions" / SID
        self.session_dir.mkdir(parents=True)
        (self.project / "src").mkdir()
        (self.project / "src" / "app.py").write_text("x = 1\n")
        (self.project / "CLAUDE.md").write_text("tasks work\n")
        self.journal = self.agent / "journal" / "hooks.jsonl"
        self.proc = self.root / "proc-no-agent"
        (self.proc / "self").mkdir(parents=True)
        (self.root / "home").mkdir()

    def tearDown(self):
        for p in self.root.rglob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o755)
            except OSError:
                pass
        self._tmp.cleanup()

    def set_task(self, status, gates):
        self.task_file.write_text(TASK.format(status=status, gates=gates), encoding="utf-8")

    def activate(self):
        (self.session_dir / "current_state").write_text("001\n", encoding="utf-8")

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("PLAYBOOK_", "_PB_", "PB_", "CLAUDE_")) and k not in ("BASH_ENV", "ENV")}
        env.update(PLAYBOOK_SESSION_ID=SID, HOME=str(self.root / "home"), PLAYBOOK_PROC_ROOT=str(self.proc),
                   PLAYBOOK_COUNTERS_LOCK_WAIT="1", PYTHONPATH=str(PLUGIN))
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return env

    def stub_python(self, broken=False):
        """A PATH whose `python3` is older than 3.10 (the version probe fails, the rest runs — the hooks' Python
        floor), or, `broken`, fails every call."""
        d = self.root / ("stub-broken" if broken else "stub-old")
        d.mkdir(exist_ok=True)
        real = shutil.which("python3")
        body = "exit 1\n" if broken else f'case "$*" in *version_info*) exit 1 ;; esac\nexec {real} "$@"\n'
        (d / "python3").write_text("#!/bin/sh\n" + body)
        (d / "python3").chmod(0o755)
        return f"{d}:{os.environ['PATH']}"

    def hook(self, name, payload, *, raw=None, scripts=SCRIPTS, **env):
        before = self.read()
        r = subprocess.run([bash_or_skip(), str(scripts / name)], cwd=str(self.project), env=self.env(**env),
                           input=raw if raw is not None else json.dumps(payload), capture_output=True, text=True,
                           timeout=60)
        new = self.read()[len(before):]
        return r, new

    def read(self):
        if not self.journal.exists():
            return []
        return [json.loads(x) for x in self.journal.read_text(encoding="utf-8").splitlines()]

    def one(self, name, payload, rc, decision, detail, **kw):
        r, new = self.hook(name, payload, **kw)
        self.assertEqual(r.returncode, rc, (name, r.stderr[-400:]))
        self.assertEqual(len(new), 1, (name, new))
        rec = new[0]
        self.assertEqual(rec["hook"], name.replace("-hook", ""))
        self.assertEqual(rec["exit"], rc)
        self.assertEqual(rec["decision"], decision, rec)
        self.assertIn(detail, rec["detail"], rec)
        return rec, r


class TheTaskGate(_Project):
    H = "task-gate-hook"

    def test_its_exit_sites(self):
        self.one(self.H, pre("Edit", {"file_path": str(self.project / "README.md")}), 0, "allow", "not a code file")
        self.one(self.H, pre("Edit", {"file_path": str(self.project / ".agent" / "notes.md")}), 0, "allow",
                 "exempt path")
        self.one(self.H, pre("Edit", {"file_path": str(self.project / "src" / "app.py")}), 2, "block",
                 "no active task")
        self.one(self.H, pre("Write", {"file_path": str(self.agent / "tasks" / "009-y" / "task.md"),
                                       "content": "x"}), 2, "block", "task.md made by hand")
        rec, r = self.one(self.H, pre("Bash", {"command": ".claude/bin/tasks work 1"}), 0, "allow",
                          "session id injected")
        self.assertIn("updatedInput", r.stdout)
        self.one(self.H, pre("Bash", {"command": "ls"}), 0, "allow", "")
        self.one(self.H, None, 2, "block", "payload unreadable", raw="not json at all")
        self.activate()
        rec, _ = self.one(self.H, pre("Edit", {"file_path": str(self.project / "src" / "app.py")}), 0, "allow",
                          "active task 001")
        self.assertEqual((rec["event"], rec["tool"], rec["tool_use_id"], rec["session_id"], rec["host_session_id"]),
                         ("PreToolUse", "Edit", "toolu_PRE1", SID, "host-1"))
        self.set_task("blocked", "- [ ] open")
        self.one(self.H, pre("Edit", {"file_path": str(self.project / "src" / "app.py")}), 2, "block",
                 "task 001 blocked")

    def test_a_bare_batch_of_ticks_and_a_judge(self):
        self.activate()
        self.set_task("in_progress", "- [ ] one\n- [ ] two\n- [ ] three")
        self.one(self.H, pre("Edit", {"file_path": str(self.task_file),
                                      "old_string": "- [ ] one\n- [ ] two\n- [ ] three",
                                      "new_string": "- [x] one\n- [x] two\n- [x] three"}), 2, "block",
                 "gate batch refused")
        # a judge's session is refused, and writes no line (impl panel r1, opus: a review must not write the project)
        r, new = self.hook(self.H, pre("Bash", {"command": ".claude/bin/tasks new quick x"}), PLAYBOOK_SESSION_ID="judge")
        self.assertEqual((r.returncode, new), (2, []), r.stderr)

    def test_below_the_python_floor(self):
        self.one(self.H, pre("Edit", {"file_path": str(self.project / "src" / "app.py")}), 2, "block",
                 "python < 3.10", PATH=self.stub_python())

    def test_a_decoy_id_in_the_tool_input_is_not_the_calls(self):
        rec, _ = self.one(self.H, pre("Edit", {"file_path": str(self.project / "README.md"),
                                               "tool_use_id": "decoy", "session_id": "decoy-s"}), 0, "allow", "")
        self.assertEqual((rec["tool_use_id"], rec["host_session_id"]), ("toolu_PRE1", "host-1"))

    def test_a_malformed_marker_blocks_and_writes_no_line(self):
        (self.agent / "current_user").write_text("a\nb\n")
        r, new = self.hook(self.H, pre("Edit", {"file_path": str(self.project / "src" / "app.py")}))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(new, [])


class TheStateEcho(_Project):
    H = "state-echo-hook"

    def test_its_exit_sites(self):
        bash = post("Bash", {"command": "true"})
        self.one(self.H, bash, 0, "context", "echo full")
        self.one(self.H, bash, 0, "ok", "quiet")
        self.one(self.H, post("Bash", {"command": "false"}, event="PostToolUseFailure"), 0, "ok",
                 "failed call counted")
        self.activate()
        self.one(self.H, bash, 0, "context", "echo full")
        self.one(self.H, post("Bash", {"command": "true"}, agent_id="a601", agent_type="general-purpose"), 0,
                 "context", "subagent")
        details = [self.hook(self.H, bash)[1][0]["detail"] for _ in range(10)]
        self.assertIn("echo reminder", details)
        self.assertIn("quiet", details)
        (self.session_dir / "current_state").write_text("099\n")
        self.one(self.H, bash, 0, "context", "task file not found")

    def test_no_session_and_a_linked_session_directory(self):
        self.one(self.H, post("Bash", {"command": "true"}), 0, "skip", "no session", PLAYBOOK_SESSION_ID=None)
        shutil.rmtree(self.session_dir)
        (self.root / "elsewhere").mkdir()
        self.session_dir.symlink_to(self.root / "elsewhere")
        self.one(self.H, post("Bash", {"command": "true"}), 0, "context", "session directory is a link")

    def test_below_the_python_floor(self):
        self.one(self.H, post("Bash", {"command": "true"}), 0, "context", "python < 3.10", PATH=self.stub_python())

    def test_the_frame_gives_the_calls_own_ids(self):
        rec, _ = self.one(self.H, post("mcp__x__y", {"tool_use_id": "decoy-in", "agent_id": "decoy-a"},
                                       tid="toolu_REAL", tool_response={"tool_use_id": "decoy-out"},
                                       head={"meta": {"agent_id": "decoy-head"}}),     # nested, BEFORE tool_input
                          0, "context", "")
        self.assertEqual((rec["tool_use_id"], rec["agent_id"], rec["event"], rec["tool"]),
                         ("toolu_REAL", "", "PostToolUse", "mcp__x__y"))

    def test_the_shell_log_line_is_still_written(self):
        self.one(self.H, post("Bash", {"command": "echo still-logged"}), 0, "context", "")
        self.assertIn("echo still-logged", (self.agent / "bash_history").read_text())


class TheStopHook(_Project):
    H = "stop-hook"

    def counted(self, n=1):
        for _ in range(n):
            self.hook("state-echo-hook", post("Bash", {"command": "true"}))

    def test_its_exit_sites(self):
        self.one(self.H, {"stop_hook_active": False}, 0, "allow", "released: no active task")
        self.activate()
        self.counted()
        self.one(self.H, {"stop_hook_active": False}, 2, "block", "held: 2 unchecked gate(s) in task 001")
        self.one(self.H, {"stop_hook_active": True}, 0, "allow", "valve")
        self.set_task("in_progress", "- [x] done")
        self.one(self.H, {"stop_hook_active": False}, 0, "allow", "all gates of task 001 checked")
        self.set_task("blocked", "- [ ] open")
        self.one(self.H, {"stop_hook_active": False}, 0, "allow", "task 001 blocked")
        self.one(self.H, {"stop_hook_active": False}, 2, "block", "held: no session", PLAYBOOK_SESSION_ID=None)

    def test_a_chat_reply_is_released(self):
        self.activate()
        self.hook("state-echo-hook", post("Read", {"file_path": "x"}))
        self.one(self.H, {"stop_hook_active": False}, 0, "allow", "a chat reply")

    def test_a_blocked_stop_leaves_no_turn_end_and_a_release_does(self):
        """The plan panel's Critical (grok; all five): the turn-end action reads the hook's own status."""
        self.activate()
        self.counted()
        turn_end = self.session_dir / "turn_end"
        self.one(self.H, {"stop_hook_active": False}, 2, "block", "held")
        self.assertFalse(turn_end.exists())
        self.one(self.H, {"stop_hook_active": True}, 0, "allow", "valve")
        self.assertTrue(turn_end.exists())


class TheOthers(_Project):
    def test_the_chat_log(self):
        H = "chat-log-hook"
        p = {"session_id": "host-1", "hook_event_name": "UserPromptSubmit", "prompt": "hello there"}
        rec, _ = self.one(H, p, 0, "ok", "logged M001")
        self.assertEqual((rec["event"], rec["tool"], rec["tool_use_id"]), ("UserPromptSubmit", "", ""))
        self.one(H, dict(p, prompt="<task-notification>done</task-notification>"), 0, "ok",
                 "not logged: a notification")
        self.one(H, dict(p, prompt="<command-name>/clear</command-name>"), 0, "ok", "not logged: a command")

    def test_session_start_and_end(self):
        start = {"session_id": "host-1", "hook_event_name": "SessionStart", "source": "startup"}
        self.one("session-start-hook", start, 0, "ok", "session set up")
        rec, _ = self.one("session-start-hook", start, 0, "skip", "python < 3.10", PATH=self.stub_python())
        self.assertEqual(rec["event"], "SessionStart")                  # gemini r1: the floor arm names its event
        end = {"session_id": "host-1", "hook_event_name": "SessionEnd", "reason": "clear"}
        self.one("session-end-hook", end, 0, "ok", "kept: reason clear")
        self.one("session-end-hook", dict(end, reason="logout"), 0, "ok", "kept: not this process's session")
        other = self.agent / "sessions" / "sess-x"
        other.mkdir()
        self.one("session-end-hook", dict(end, reason="logout"), 0, "ok", "session directory removed",
                 PLAYBOOK_SESSION_ID="sess-x")
        self.assertFalse(other.exists())
        # grok r1: a session directory that is a link inside `sessions` is kept, and the line says so
        (self.root / "elsewhere").mkdir()
        (self.agent / "sessions" / "sess-l").symlink_to(self.root / "elsewhere")
        self.one("session-end-hook", dict(end, reason="logout"), 0, "ok", "kept",
                 PLAYBOOK_SESSION_ID="sess-l")
        self.assertTrue((self.agent / "sessions" / "sess-l").is_symlink())
        if os.geteuid() != 0:                       # a directory rm cannot empty (root removes anyway)
            stuck = self.agent / "sessions" / "sess-r" / "locked"
            stuck.mkdir(parents=True)
            (stuck / "f").write_text("x")
            stuck.chmod(0o555)
            self.one("session-end-hook", dict(end, reason="logout"), 0, "ok", "kept: not removed",
                     PLAYBOOK_SESSION_ID="sess-r")
            stuck.chmod(0o755)
        rec, _ = self.one("session-end-hook", end, 0, "skip", "python < 3.10", PATH=self.stub_python())
        self.assertEqual(rec["event"], "SessionEnd")

    def test_the_task_folder_watch(self):
        H = "task-dir-watch-hook"
        self.one(H, pre("Bash", {"command": "ls"}), 0, "allow", "watched")
        self.one(H, pre("Edit", {"file_path": str(self.project / "src" / "app.py"), "old_string": "x",
                                 "new_string": "y"}), 0, "allow", "an edit of an existing file")
        (self.agent / "tasks" / "002-by-hand").mkdir()
        (self.agent / "tasks" / "002-by-hand" / "task.md").write_text("# 002\n")
        self.one(H, post("Bash", {"command": "mkdir x"}), 0, "context", "flagged")
        self.one(H, pre("Bash", {"command": "ls"}), 2, "block", "held: a task folder made by hand")

    def test_the_watch_failing_is_an_error(self):
        rec, _ = self.one("task-dir-watch-hook", post("Bash", {"command": "ls"}), 0, "error",
                          "the watch could not run", PATH=self.stub_python(broken=True))
        self.assertIn("task-dir-watch.py ended with exit", rec["error"])

    def test_the_command_guards_fail_open_arm(self):
        copy = self.root / "plugin-copy"
        shutil.copytree(SCRIPTS, copy, ignore=shutil.ignore_patterns("command_guard.py", "__pycache__"))
        self.one("command-guard-hook", pre("Bash", {"command": "ls"}), 0, "allow",
                 "guard disabled: helper not readable", scripts=copy)


if __name__ == "__main__":
    unittest.main()
