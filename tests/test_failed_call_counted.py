"""A tool call that FAILS is counted (task 196, found while building the task-folder watch; pre-existing).

Claude Code fires only PostToolUseFailure after a call that fails — a command that exits non-zero, an Edit whose old
string is not there (measured in task 196 on 2.1.296: `echo third; exit 3` and `mkdir -p made/x; false` gave no
PostToolUse). `state-echo-hook`, which counts the calls the stop hook's turn rules read, was registered for
PostToolUse only, so a failed call was never counted: a turn whose only call was `sed -i … src/a.py && false` changed
the file, left `tools=0 writes=0`, and the stop hook let it end with its gates open — the same call succeeding was
held (measured with the tree's hooks, task 196 `evidence/measure_stop_failed.py`). A guard that failed open.

The tests fire the hooks hooks.json wires for each event, on a task made and activated by the tree CLI.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
CLI = PLUGIN / "scripts" / "tasks"
SID = "s196-failed-call"


class AFailedCallIsCounted(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.proj = self.base / "proj"
        (self.proj / ".agent" / "tasks").mkdir(parents=True)
        (self.proj / "src").mkdir()
        (self.proj / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        for args in (("new", "quick", "probe"), ("work", "1")):
            r = subprocess.run([bash_or_skip(), str(CLI), *args], cwd=self.proj, env=self._env(), text=True,
                               capture_output=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.fire("UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": "change a.py"})

    def _env(self):
        # a /proc with no agent: the session is the env id alone, as on CI — a judge's nested run must not decide
        proc = self.base / "proc-no-agent"
        (proc / "self").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PLAYBOOK_SESSION_ID=SID, HOME=str(self.home), CLAUDE_PLUGIN_ROOT=str(PLUGIN),
                   PLAYBOOK_PROC_ROOT=str(proc))
        for k in ("BASH_ENV", "PLAYBOOK_SANDBOXED", "CLAUDE_PROJECT_DIR", "PLAYBOOK_EVAL_CONFIG", "PLAYBOOK_ROLE"):
            env.pop(k, None)
        return env

    def fire(self, event, payload):
        """Every hook hooks.json runs for `event` and this tool: [(script, exit code, stdout, stderr)]."""
        payload = {"session_id": SID, "cwd": str(self.proj), **payload}
        out = []
        for entry in json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"].get(event, []):
            m = entry.get("matcher")
            if m not in (None, "", "*") and not re.fullmatch(m, payload.get("tool_name", "")):
                continue
            for h in entry["hooks"]:
                r = subprocess.run([bash_or_skip(), "-c", h["command"]], cwd=self.proj, env=self._env(),
                                   input=json.dumps(payload), text=True, capture_output=True, timeout=60)
                out.append((h["command"].split("/scripts/")[1].split('"')[0], r.returncode, r.stdout, r.stderr))
        return out

    def call(self, tool, tool_input, effect, failed, tuid="toolu_F1"):
        self.fire("PreToolUse", {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                                 "tool_use_id": tuid})
        effect()
        if failed:
            return self.fire("PostToolUseFailure", {"hook_event_name": "PostToolUseFailure", "tool_name": tool,
                                                    "tool_input": tool_input, "tool_use_id": tuid,
                                                    "error": "Exit code 1", "is_interrupt": False})
        return self.fire("PostToolUse", {"hook_event_name": "PostToolUse", "tool_name": tool,
                                         "tool_input": tool_input, "tool_use_id": tuid,
                                         "tool_response": {"stdout": "", "stderr": ""}})

    def sed_call(self, failed):
        cmd = {"command": "sed -i s/1/2/ src/a.py && false" if failed else "sed -i s/1/2/ src/a.py",
               "description": "t"}
        return self.call("Bash", cmd, lambda: (self.proj / "src" / "a.py").write_text("x = 2\n", encoding="utf-8"),
                         failed)

    def counters(self):
        p = self.proj / ".agent" / "sessions" / SID / "counters"
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        return {k: int(v) for k, v in re.findall(r"(?m)^(tools|writes)=(\d+)$", text)}

    def stop(self):
        return self.fire("Stop", {"hook_event_name": "Stop", "stop_hook_active": False})

    def test_a_turn_whose_only_call_failed_is_held_by_the_stop(self):
        self.sed_call(failed=True)
        stop = self.stop()
        self.assertTrue(any(rc == 2 and "unchecked gate" in err for _, rc, _, err in stop), stop)

    def test_control_the_same_call_succeeding_is_held(self):
        self.sed_call(failed=False)
        stop = self.stop()
        self.assertTrue(any(rc == 2 and "unchecked gate" in err for _, rc, _, err in stop), stop)

    def test_a_failed_call_is_counted_as_a_tool_and_a_write(self):
        self.sed_call(failed=True)
        self.assertEqual(self.counters(), {"tools": 1, "writes": 1})

    def test_a_failed_call_gets_no_gate_echo(self):
        post = self.sed_call(failed=True)
        echo = [o for name, _, o, _ in post if name == "state-echo-hook"]
        self.assertEqual(len(echo), 1, post)
        self.assertNotIn("Working on task", echo[0])
        for line in filter(None, echo[0].splitlines()):
            self.assertEqual(json.loads(line)["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")

    def test_a_failed_edit_is_a_tool_but_not_a_write(self):
        # impl panel r1 I5 (fable): a failed Edit changed nothing — a write only when a shell call failed
        self.call("Edit", {"file_path": str(self.proj / "src" / "a.py"), "old_string": "zzz", "new_string": "y"},
                  lambda: None, failed=True, tuid="toolu_E1")
        self.assertEqual(self.counters(), {"tools": 1, "writes": 0})

    def test_a_failed_write_is_not_write_logged(self):
        # the write log copies the file from DISK after a Write: after a failed one that copy is the OLD content,
        # logged as if the agent had written it
        target = self.proj / "notes.md"
        target.write_text("old content\n", encoding="utf-8")
        self.call("Write", {"file_path": str(target), "content": "never written\n"}, lambda: None, failed=True,
                  tuid="toolu_W1")
        root = self.home / ".local" / "share" / "playbook"
        logs = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
        self.assertFalse(any("old content" in p.read_text(encoding="utf-8", errors="replace") for p in logs),
                         [str(p) for p in logs])
        self.assertEqual(self.counters().get("tools"), 1)

    def test_control_a_successful_write_is_write_logged(self):
        target = self.proj / "notes.md"
        self.call("Write", {"file_path": str(target), "content": "written\n"},
                  lambda: target.write_text("written\n", encoding="utf-8"), failed=False, tuid="toolu_W2")
        root = self.home / ".local" / "share" / "playbook"
        logs = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
        self.assertTrue(any("written" in p.read_text(encoding="utf-8", errors="replace") for p in logs),
                        [str(p) for p in logs])


if __name__ == "__main__":
    unittest.main()
