#!/usr/bin/env python3
"""The plugin's registered hooks, invoked the way Claude Code invokes them (task 184, PLAN S11).

PB-PROVIDER-HOOK-INVOCATION: "advertised provider edit and shell events invoke the applicable
task gate, command guard, state echo, chat logger, and stop behavior rather than merely
generating configuration". So nothing here calls a hook script by its own path: every command
comes from `hooks/hooks.json` as shipped — chosen by event and by the entry's `matcher` against
the tool's name — and runs through `bash -c` with `CLAUDE_PLUGIN_ROOT` set, a Claude Code
event payload on stdin, in a scratch project. What each one did is read back from what it left:
its exit status and message, the enforcement journal, the session's counters, the chat log.

Each behaviour has its control: the same edit with a task active is allowed, a harmless command
passes the guard, a turn that wrote nothing (a chat reply) may end, a task whose gates are all
checked may end.

Run: python3 -m unittest tests.test_hooks_invoked_integration
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

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "playbook"
HOOKS = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
SESSION = "pid-hook-invocation"
GATES = "- [ ] G1: build it\n- [ ] G2: test it\n"


def registered(event: str, tool: "str | None" = None) -> "list[str]":
    """The commands hooks.json registers for this event (and this tool, by each entry's matcher)."""
    out = []
    for entry in HOOKS.get(event, []):
        matcher = entry.get("matcher")
        if tool is None or matcher in (None, "", "*") or re.fullmatch(matcher, tool):
            out += [h["command"] for h in entry.get("hooks", []) if h.get("type", "command") == "command"]
    return out


class _Project(unittest.TestCase):
    active = True
    gates = GATES

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="pb-hookinv-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.p = tmp / "proj"
        tdir = self.p / ".agent" / "tasks" / "001-thing"
        tdir.mkdir(parents=True)
        self.task = tdir / "task.md"
        self.task.write_text("# 001 - Thing\n\n## Status\npending\n\n## Risk\nreversible\n\n## Work Plan\n" + self.gates,
                             encoding="utf-8")
        self.sdir = self.p / ".agent" / "sessions" / SESSION
        self.sdir.mkdir(parents=True)
        if self.active:
            (self.sdir / "current_state").write_text("001\n", encoding="utf-8")
        (self.p / "src").mkdir()
        (self.p / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.p, check=True)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("PLAYBOOK_", "CLAUDE_CODE_", "CLAUDECODE")) and k != "BASH_ENV"}
        self.env.update(CLAUDE_PLUGIN_ROOT=str(PLUGIN), PLAYBOOK_SESSION_ID=SESSION, CLAUDE_PROJECT_DIR=str(self.p))

    def fire(self, event: str, payload: dict, tool: "str | None" = None) -> "list[subprocess.CompletedProcess]":
        cmds = registered(event, tool)
        self.assertTrue(cmds, f"hooks.json registers nothing for {event} {tool or ''}")
        body = json.dumps({"session_id": "s-1", "cwd": str(self.p), "hook_event_name": event, **payload})
        return [subprocess.run(["bash", "-c", c], input=body, cwd=self.p, env=self.env, capture_output=True,
                               text=True, timeout=60) for c in cmds]

    def edit(self):
        return self.fire("PreToolUse", {"tool_name": "Edit", "tool_input": {
            "file_path": str(self.p / "src" / "app.py"), "old_string": "1", "new_string": "2"}}, "Edit")

    def after_edit(self):
        (self.p / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
        return self.fire("PostToolUse", {"tool_name": "Edit", "tool_input": {"file_path": str(self.p / "src" / "app.py")},
                                         "tool_response": {"success": True}}, "Edit")

    def bash(self, command: str):
        return self.fire("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": command}}, "Bash")

    def prompt(self, text: str):
        return self.fire("UserPromptSubmit", {"prompt": text})

    def stop(self):
        return self.fire("Stop", {"stop_hook_active": False})

    def journal(self) -> "list[dict]":
        f = self.p / ".agent" / "journal" / "enforcement.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []

    def blocked(self, results) -> bool:
        return any(r.returncode == 2 for r in results)


class WithNoActiveTask(_Project):
    active = False

    def test_the_task_gate_blocks_a_code_edit_and_journals_it(self):
        r = self.edit()
        self.assertTrue(self.blocked(r), [x.stderr for x in r])
        self.assertIn("No active task", "".join(x.stderr for x in r))
        self.assertTrue(any(j["hook"] == "task-gate" and j["decision"] == "block" and j["tool"] == "Edit"
                            for j in self.journal()), self.journal())


class WithAnActiveTask(_Project):
    def test_control_the_same_edit_is_allowed(self):
        r = self.edit()
        self.assertFalse(self.blocked(r), [x.stderr for x in r])

    def test_the_command_guard_blocks_a_destructive_command_and_journals_it(self):
        r = self.bash("rm -rf /")
        self.assertTrue(self.blocked(r))
        self.assertIn("rm-rf-dangerous-target", "".join(x.stderr for x in r))
        self.assertTrue(any(j["hook"] == "command-guard" and j["decision"] == "block" for j in self.journal()))

    def test_control_a_harmless_command_passes_the_guard(self):
        self.assertFalse(self.blocked(self.bash("ls src")))

    def test_the_state_echo_reports_the_gate_and_counts_the_call(self):
        self.prompt("build it")
        self.edit()
        r = self.after_edit()
        ctx = json.loads(r[0].stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Working on task [001]", ctx)
        self.assertIn("G1: build it", ctx)
        counters = (self.sdir / "counters").read_text(encoding="utf-8")
        self.assertIn("tools=1", counters)
        self.assertIn("writes=1", counters)

    def test_the_chat_logger_records_the_prompt(self):
        self.prompt("please add the feature")
        log = (self.p / ".agent" / "chat_log.md").read_text(encoding="utf-8")
        self.assertIn("please add the feature", log)
        self.assertIn(SESSION, log)

    def test_stop_blocks_a_turn_that_wrote_while_gates_are_open(self):
        self.prompt("build it")
        self.edit()
        self.after_edit()
        r = self.stop()
        self.assertTrue(self.blocked(r))
        self.assertIn("2 unchecked gate(s) in task 001", "".join(x.stderr for x in r))

    def test_control_a_turn_that_wrote_nothing_may_end(self):
        self.prompt("what is the plan?")
        self.assertFalse(self.blocked(self.stop()))


class WithEveryGateChecked(_Project):
    gates = "- [x] G1: build it — done\n- [x] G2: test it — done\n"

    def test_control_a_turn_that_wrote_may_end_when_every_gate_is_checked(self):
        self.prompt("build it")
        self.edit()
        self.after_edit()
        self.assertFalse(self.blocked(self.stop()))


if __name__ == "__main__":
    unittest.main()
