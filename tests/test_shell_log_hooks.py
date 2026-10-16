"""The shell log is written by the hook that receives the agent's exact command (PHASE-B item 2, task 221).

The owner, 2026-10-16: "Doar comenzile mele" (only the agent's commands) and "Hook-ul scrie" (the hook writes them).
`state-echo-hook` runs after every tool call of Claude, grok, agy and pi; `codex-apply-patch-hook` after codex's
`exec_command`. Each hands the command to the one writer, `shell_log_append` (tests/test_shell_log_writer.py).

Held here, with the REAL hooks on crafted payloads in a scratch project:
  * a Bash or Monitor call — succeeded or failed, the main agent's or a subagent's, Claude's or grok's — is one line,
    equal to the command as the normaliser delivers it (cut at a NUL); codex's string or argv command too;
  * nothing else is a line: an Edit, a Read, a Bash payload without a command;
  * it is written on every way out of the hook (an unresolved session, a linked session directory) and after the
    count: with the log's lock held by another process the call is still counted and the hook ends in time;
  * where the hook may not write (a read-only `.agent`, as in a judge's sandbox) there is no line and the hook answers;
  * where the fused payload read dies, the fallback path still finds the command.

Run: python3 -m unittest tests.test_shell_log_hooks
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import time
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip
from tests.test_notification_turn import SCRIPTS, SID, _Session
from tests.test_shell_log_writer import LINE_RE, decode


class _Logged(_Session):
    def setUp(self):
        super().setUp()
        self.hist = self.project / ".agent" / "bash_history"

    def env(self, **extra):
        env = dict(os.environ)
        env["PLAYBOOK_SESSION_ID"] = SID
        env["HOME"] = self._tmp.name
        env["PLAYBOOK_PROC_ROOT"] = str(self.proc_root)
        for k in ("BASH_ENV", "ENV", "PLAYBOOK_ROLE", "PLAYBOOK_EVAL_CONFIG", "PLAYBOOK_SHELL_LOG_LOCK_WAIT",
                  "PLAYBOOK_PROJECT_ROOT"):
            env.pop(k, None)
        env.update(extra)
        return env

    def hook(self, payload, name="state-echo-hook", scripts=SCRIPTS, unset=(), **extra):
        env = self.env(**extra)
        for k in unset:
            env.pop(k, None)
        t0 = time.time()
        r = subprocess.run([bash_or_skip(), str(scripts / name)], cwd=self.project, env=env, text=True,
                           input=payload if isinstance(payload, str) else json.dumps(payload), capture_output=True,
                           timeout=60)
        r.elapsed = time.time() - t0      # type: ignore[attr-defined]
        return r

    def logged(self):
        if not self.hist.exists():
            return []
        out = []
        for ln in self.hist.read_text(encoding="utf-8").split("\n")[:-1]:
            m = LINE_RE.match(ln)
            assert m is not None, ln
            out.append(decode(m.group(2)))
        return out


class TheAgentsCommands(_Logged):
    def test_a_bash_call_is_one_line(self):
        r = self.hook({"tool_name": "Bash", "tool_input": {"command": "git status --short"}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.logged(), ["git status --short"])

    def test_a_failed_bash_call_is_one_line(self):
        r = self.hook({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                       "tool_input": {"command": "false && echo never"}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.logged(), ["false && echo never"])

    def test_a_monitor_call_is_one_line(self):
        self.hook({"tool_name": "Monitor", "tool_input": {"command": "until test -f x; do sleep 5; done"}})
        self.assertEqual(self.logged(), ["until test -f x; do sleep 5; done"])

    def test_a_multi_line_command_reads_back_whole(self):
        cmd = "cd /tmp && python3 - <<'EOF'\nprint('a\\nb')\nEOF"
        self.hook({"tool_name": "Bash", "tool_input": {"command": cmd}})
        self.assertEqual(self.logged(), [cmd])
        self.assertEqual(self.hist.read_text(encoding="utf-8").count("\n"), 1)

    def test_a_subagents_call_is_one_line(self):
        self.hook({"tool_name": "Bash", "agent_id": "a1b2", "tool_input": {"command": "ls sub"}})
        self.assertEqual(self.logged(), ["ls sub"])

    def test_a_grok_shell_call_is_one_line(self):
        self.hook({"hookEventName": "PostToolUse", "toolName": "Shell", "sessionId": "g1",
                   "toolInput": {"command": "ls grok"}})
        self.hook({"tool_name": "run_terminal_command", "tool_input": {"command": "ls hybrid"}})
        self.assertEqual(self.logged(), ["ls grok", "ls hybrid"])

    def test_the_session_prefix_the_task_gate_adds_is_left_out(self):
        # task 118's injection, measured live in task 221 W5; only THIS session's
        self.hook({"tool_name": "Bash", "tool_input": {"command": f"export PLAYBOOK_SESSION_ID={SID}; .claude/bin/tasks work 1"}})
        self.hook({"tool_name": "Bash", "tool_input": {"command": "export PLAYBOOK_SESSION_ID=pid-other; ls"}})
        self.hook({"tool_name": "Bash", "tool_input": {"command": f"echo export PLAYBOOK_SESSION_ID={SID}; ls"}})
        self.assertEqual(self.logged(), [".claude/bin/tasks work 1", "export PLAYBOOK_SESSION_ID=pid-other; ls",
                                         f"echo export PLAYBOOK_SESSION_ID={SID}; ls"])

    def test_a_command_is_cut_at_a_nul_as_the_normaliser_cuts_it(self):
        self.hook({"tool_name": "Bash", "tool_input": {"command": "echo a\u0000b"}})
        self.assertEqual(self.logged(), ["echo a"])

    def test_calls_that_are_not_shell_commands_are_not_lines(self):
        self.hook({"tool_name": "Edit", "tool_input": {"file_path": str(self.project / "x.py"),
                                                      "old_string": "a", "new_string": "b", "command": "no"}})
        self.hook({"tool_name": "Read", "tool_input": {"file_path": str(self.project / "x.py")}})
        self.hook({"tool_name": "Bash", "tool_input": {}})
        self.hook({"tool_name": "Bash", "tool_input": {"command": "  \n"}})
        self.assertEqual(self.logged(), [])
        self.assertEqual(self.counts()[0], 4, "every call still counted")


class EveryWayOut(_Logged):
    def test_an_unresolved_session_still_logs(self):
        r = self.hook({"tool_name": "Bash", "tool_input": {"command": "ls unresolved"}}, unset=("PLAYBOOK_SESSION_ID",))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.logged(), ["ls unresolved"])

    def test_a_linked_session_directory_still_logs(self):
        elsewhere = Path(self._tmp.name) / "elsewhere"
        elsewhere.mkdir()
        shutil.rmtree(self.session_dir)
        self.session_dir.symlink_to(elsewhere, target_is_directory=True)
        r = self.hook({"tool_name": "Bash", "tool_input": {"command": "ls linked"}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.logged(), ["ls linked"])
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_read_only_agent_dir_gets_no_line_and_the_hook_answers(self):
        agent = self.project / ".agent"
        mode = agent.stat().st_mode
        agent.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            if os.access(agent, os.W_OK):
                self.skipTest("running as a user that writes anyway (root)")
            r = self.hook({"tool_name": "Bash", "tool_input": {"command": "ls ro"}})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(r.stdout.strip(), "the hook still answers")
        finally:
            agent.chmod(mode)
        self.assertEqual(self.logged(), [])

    @unittest.skipUnless(shutil.which("flock"), "no flock(1) on this host")
    def test_a_held_log_lock_costs_neither_the_count_nor_the_time_limit(self):
        before = self.counts()[0] if self.counters_file.exists() else 0
        fd = os.open(self.project / ".agent", os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            r = self.hook({"tool_name": "Bash", "tool_input": {"command": "ls held"}},
                          PLAYBOOK_SHELL_LOG_LOCK_WAIT="2")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(r.elapsed, 5.0)       # type: ignore[attr-defined]
        self.assertEqual(self.counts()[0], before + 1)
        self.assertFalse(self.hist.exists())
        self.hook({"tool_name": "Bash", "tool_input": {"command": "ls next"}})
        self.assertEqual(self.logged(), ["ls held", "ls next"])

    def test_the_fallback_path_finds_the_command(self):
        copy = Path(self._tmp.name) / "scripts-copy"
        shutil.copytree(SCRIPTS, copy, ignore=shutil.ignore_patterns("__pycache__"))
        (copy / "hook-payload-normalize.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
        r = self.hook({"tool_name": "Bash", "tool_input": {"command": "ls fallback\u0000x"}}, scripts=copy)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.logged(), ["ls fallback"])


class Codex(_Logged):
    def codex(self, command):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "exec_command", "tool_input": {"command": command}}
        r = subprocess.run(["python3", "-E", "-s", str(SCRIPTS / "codex-apply-patch-hook")], cwd=self.project,
                           env=self.env(PLAYBOOK_PROJECT_ROOT=str(self.project)), text=True,
                           input=json.dumps(payload), capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_codex_commands_are_lines(self):
        self.codex("ls codex")
        self.codex(["bash", "-lc", "echo one && echo two"])
        self.codex(["/bin/sh", "-c", "pwd"])
        self.codex(["git", "log", "--format=%h %s"])
        self.assertEqual(self.logged(), ["ls codex", "echo one && echo two", "pwd", "git log '--format=%h %s'"])

    def test_a_codex_apply_patch_is_not_a_line(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "apply_patch", "tool_input": {"command": "x"}}
        subprocess.run(["python3", "-E", "-s", str(SCRIPTS / "codex-apply-patch-hook")], cwd=self.project,
                       env=self.env(PLAYBOOK_PROJECT_ROOT=str(self.project)), text=True,
                       input=json.dumps(payload), capture_output=True, timeout=60)
        self.assertEqual(self.logged(), [])


if __name__ == "__main__":
    unittest.main()
