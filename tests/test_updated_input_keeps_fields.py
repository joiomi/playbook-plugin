"""Task 118 (PLAN S11, gauntlet 2 G2-04, G2-28): the session-id injection keeps every tool field.

task-gate-hook prefixes a `tasks work|status|bootstrap|freehand|retro|new` Bash call with
`export PLAYBOOK_SESSION_ID=<id>; ` through `updatedInput`. A host REPLACES the tool input with
updatedInput, and the hook sent `{"command": …}` alone:
  - grok schema-checks it and denied every such call ("the updatedInput failed the tool's schema:
    missing field `description`") — a grok agent or judge could not run the tasks CLI at all;
  - Claude Code dropped the call's `description`, `timeout` and `run_in_background` (measured
    2026-10-07: a background `tasks status` ran in the foreground).
The eval-mode path also pasted the command into Python source (`json.dumps('$UPDATED_CMD')`): a
command holding a `'` produced no JSON, so the hook printed a broken `"command":}` object.

Now updatedInput is the call's own tool input (the RAW payload's — grok's `toolInput`, never the
normalized copy with its added `file_path`/`content` aliases) with only `command` replaced.

Run: python3 -m unittest tests.test_updated_input_keeps_fields
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

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "playbook"
HOOK = PLUGIN / "scripts" / "task-gate-hook"
SID = "sess-118"
PREFIX = f"export PLAYBOOK_SESSION_ID={SID}; "

HOSTILE = [
    ".claude/bin/tasks status",
    ".claude/bin/tasks new quick it's-a-name",
    '.claude/bin/tasks new quick t1 -- say "hi" \\ there',
    ".claude/bin/tasks status; echo $(date) `id`",
    ".claude/bin/tasks status\necho second line",
    ".claude/bin/tasks new quick t1 -- ăîșț — 日本",
    "cd sub && ../.claude/bin/tasks work 3 --force",
]


class _Hook(unittest.TestCase):
    EVAL = False

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb-118-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        (self.d / ".agent" / "tasks").mkdir(parents=True)          # the hook's project marker
        (self.d / "CLAUDE.md").write_text("Use `.claude/bin/tasks work <N>`.\n", encoding="utf-8")
        self.env = dict(os.environ, PLAYBOOK_SESSION_ID=SID, CLAUDE_PLUGIN_ROOT=str(PLUGIN),
                        CLAUDE_PROJECT_DIR=str(self.d))
        for k in ("BASH_ENV", "PLAYBOOK_ALLOW_DANGEROUS", "CLAUDE_ENV_FILE", "PLAYBOOK_EVAL_CONFIG"):
            self.env.pop(k, None)
        if self.EVAL:
            cfg = self.d / "eval.json"
            cfg.write_text(json.dumps({"pretool_guard": "off"}), encoding="utf-8")
            self.env["PLAYBOOK_EVAL_CONFIG"] = str(cfg)

    def run_hook(self, payload: dict) -> subprocess.CompletedProcess:
        # raw UTF-8 like a real host (json.dumps' default \uXXXX escapes would hide a lossy decode)
        return subprocess.run([bash_or_skip(), str(HOOK)], input=json.dumps(payload, ensure_ascii=False), cwd=self.d,
                              env=self.env, capture_output=True, text=True, encoding="utf-8",
                              timeout=60)

    def updated(self, payload: dict):
        r = self.run_hook(payload)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        out = r.stdout.strip()
        if not out or out == "{}":
            return None
        return json.loads(out)["hookSpecificOutput"]["updatedInput"]

    def claude(self, tool_input: dict) -> dict:
        return {"session_id": "s", "cwd": str(self.d), "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": tool_input}

    def grok(self, tool_input: dict) -> dict:
        return {"sessionId": "s", "workspaceRoot": str(self.d), "hookEventName": "PreToolUse",
                "toolName": "run_terminal_command", "toolInput": tool_input}


class KeepsEveryField(_Hook):
    def test_claude_call_keeps_description_timeout_and_background(self):
        ti = {"command": ".claude/bin/tasks status", "description": "Show task status",
              "timeout": 600000, "run_in_background": True}
        self.assertEqual(self.updated(self.claude(ti)), dict(ti, command=PREFIX + ti["command"]))

    def test_grok_call_keeps_description_and_gets_no_alias_keys(self):
        # G2-04's call as grok LOGGED it (109 evidence/grok-hook-denials.jsonl row 1) — taken as
        # its hook `toolInput`; no real grok hook payload is captured yet (task 118 live-check gate)
        ti = {"command": ".claude/bin/tasks bootstrap", "description": "Bootstrap playbook tasks"}
        self.assertEqual(self.updated(self.grok(ti)), dict(ti, command=PREFIX + ti["command"]))

    def test_grok_call_with_a_path_key_gets_no_file_path_back(self):
        # the normalizer ADDS file_path for a grok `path`; that alias must never reach the tool
        ti = {"command": ".claude/bin/tasks status", "description": "d", "path": "sub"}
        self.assertEqual(self.updated(self.grok(ti)), dict(ti, command=PREFIX + ti["command"]))

    def test_hybrid_snake_case_grok_call_keeps_its_fields(self):
        p = {"session_id": "s", "cwd": str(self.d), "hook_event_name": "PreToolUse",
             "tool_name": "run_terminal_command",
             "tool_input": {"command": ".claude/bin/tasks status", "description": "d"}}
        self.assertEqual(self.updated(p), {"command": PREFIX + ".claude/bin/tasks status",
                                           "description": "d"})

    def test_every_hostile_command_round_trips_exactly(self):
        for cmd in HOSTILE:
            for build in (self.claude, self.grok):
                with self.subTest(cmd=cmd, host=build.__name__):
                    ti = {"command": cmd, "description": "x"}
                    self.assertEqual(self.updated(build(ti)), {"command": PREFIX + cmd, "description": "x"})

    def test_a_call_that_is_not_a_tasks_call_gets_no_updated_input(self):
        for build in (self.claude, self.grok):
            with self.subTest(host=build.__name__):
                self.assertIsNone(self.updated(build({"command": "ls -la", "description": "list"})))


class KeepsEveryFieldInEvalMode(KeepsEveryField):
    """The `pretool_guard: off` path (PLAYBOOK_EVAL_CONFIG) injects too, by its own code."""
    EVAL = True


if __name__ == "__main__":
    unittest.main()
