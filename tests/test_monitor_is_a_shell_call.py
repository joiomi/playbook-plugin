"""Claude Code's Monitor tool runs a shell command, and the hooks judge it as one (task 307, audit 4's G1).

Monitor runs its `command` in the same shell as Bash, its stdout streamed back as events (Claude Code's own schema:
`command` or `ws`, a WebSocket that runs no shell; `description` and `timeout_ms` required). No PreToolUse matcher in
hooks.json named it, so the command interlock, the task gate and the task-folder watch never saw its command — a
`git reset --hard` sent as a Monitor ran unasked (his blind session, 2026-10-25; reproduced live: a Monitor call was
journaled only by state-echo at PostToolUse). And the state echo counted it as a tool but not as a write, so a turn
whose only calls were one to four Monitors was released by the stop as a chat reply with the gates open.

The tests fire the hooks hooks.json wires for each event and tool (its matcher read with `re.fullmatch`, as Claude
Code reads it), on a task made and activated by the tree CLI, each beside the same call made as Bash.
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
SID = "s307-monitor"


def _monitor(command):
    return {"command": command, "description": "t", "timeout_ms": 60000}


def _bash(command):
    return {"command": command, "description": "t"}


class TheMatchers(unittest.TestCase):

    def test_every_entry_that_takes_bash_takes_monitor(self):
        hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
        seen = []
        for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            for entry in hooks.get(event, []):
                m = entry.get("matcher")
                if m in (None, "", "*") or not re.fullmatch(m, "Bash"):
                    continue
                names = [h["command"].split("/scripts/")[1].split('"')[0] for h in entry["hooks"]]
                seen.append((event, names))
                self.assertTrue(re.fullmatch(m, "Monitor"), f"{event} {names}: matcher {m!r} does not take Monitor")
        # the entries this task is about are among them
        flat = {(e, n) for e, ns in seen for n in ns}
        for want in (("PreToolUse", "task-gate-hook"), ("PreToolUse", "command-guard-hook"),
                     ("PreToolUse", "task-dir-watch-hook"), ("PostToolUse", "task-dir-watch-hook"),
                     ("PostToolUseFailure", "task-dir-watch-hook")):
            self.assertIn(want, flat)


class AMonitorIsAShellCall(unittest.TestCase):

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
        self.prompt()

    def _env(self, sid=SID):
        # a /proc with no agent: the session is the env id alone, as on CI — a judge's nested run must not decide
        proc = self.base / "proc-no-agent"
        (proc / "self").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PLAYBOOK_SESSION_ID=sid, HOME=str(self.home), CLAUDE_PLUGIN_ROOT=str(PLUGIN),
                   PLAYBOOK_PROC_ROOT=str(proc))
        for k in ("BASH_ENV", "PLAYBOOK_SANDBOXED", "CLAUDE_PROJECT_DIR", "PLAYBOOK_EVAL_CONFIG", "PLAYBOOK_ROLE",
                  "PLAYBOOK_ALLOW_DANGEROUS"):
            env.pop(k, None)
        return env

    def fire(self, event, payload, sid=SID):
        """Every hook hooks.json runs for `event` and this tool: [(script, exit code, stdout, stderr)]."""
        payload = {"session_id": sid, "cwd": str(self.proj), "hook_event_name": event, **payload}
        out = []
        for entry in json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"].get(event, []):
            m = entry.get("matcher")
            if m not in (None, "", "*") and not re.fullmatch(m, payload.get("tool_name", "")):
                continue
            for h in entry["hooks"]:
                r = subprocess.run([bash_or_skip(), "-c", h["command"]], cwd=self.proj, env=self._env(sid),
                                   input=json.dumps(payload), text=True, capture_output=True, timeout=60)
                out.append((h["command"].split("/scripts/")[1].split('"')[0], r.returncode, r.stdout, r.stderr))
        return out

    def prompt(self):
        self.fire("UserPromptSubmit", {"prompt": "go on"})

    def pre(self, tool, tool_input, tuid="toolu_M1", sid=SID):
        return self.fire("PreToolUse", {"tool_name": tool, "tool_input": tool_input, "tool_use_id": tuid}, sid)

    def call(self, tool, tool_input, effect=lambda: None, failed=False, tuid="toolu_M1"):
        """One call as the host runs it: the PreToolUse hooks (none may refuse it), its effect, the PostToolUse
        hooks (PostToolUseFailure when it failed)."""
        pre = self.pre(tool, tool_input, tuid)
        self.assertFalse([o for o in pre if o[1] == 2], pre)
        effect()
        if failed:
            return self.fire("PostToolUseFailure", {"tool_name": tool, "tool_input": tool_input, "tool_use_id": tuid,
                                                    "error": "Exit code 1", "is_interrupt": False})
        return self.fire("PostToolUse", {"tool_name": tool, "tool_input": tool_input, "tool_use_id": tuid,
                                         "tool_response": {"stdout": "", "stderr": ""}})

    def ran(self, results, script):
        return [o for o in results if o[0] == script]

    def counters(self):
        p = self.proj / ".agent" / "sessions" / SID / "counters"
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        return {k: int(v) for k, v in re.findall(r"(?m)^(tools|writes)=(\d+)$", text)}

    def stop(self):
        return self.fire("Stop", {"stop_hook_active": False})

    def held(self, stop):
        return any(rc == 2 and "unchecked gate" in err for _, rc, _, err in stop)

    def hook_journal(self):
        p = self.proj / ".agent" / "journal" / "hooks.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    # -- the rows

    def test_the_interlock_blocks_a_monitor_command(self):
        bash = self.ran(self.pre("Bash", _bash("git reset --hard HEAD~1")), "command-guard-hook")
        self.assertEqual([rc for _, rc, _, _ in bash], [2], bash)              # the control: as Bash
        mon = self.ran(self.pre("Monitor", _monitor("git reset --hard HEAD~1")), "command-guard-hook")
        self.assertEqual([rc for _, rc, _, _ in mon], [2], mon)
        self.assertIn("BLOCKED", mon[0][3])
        self.assertIn("git reset --hard", mon[0][3])
        ok = self.pre("Monitor", _monitor("git status"), tuid="toolu_M2")
        self.assertTrue(self.ran(ok, "command-guard-hook"), ok)
        self.assertEqual([rc for _, rc, _, _ in ok], [0] * len(ok), ok)

    def test_a_judge_session_cannot_work_a_task_through_monitor(self):
        bash = self.ran(self.pre("Bash", _bash("tasks work 1"), sid="judge"), "task-gate-hook")
        self.assertEqual([rc for _, rc, _, _ in bash], [2], bash)              # the control: as Bash
        mon = self.ran(self.pre("Monitor", _monitor("tasks work 1"), sid="judge"), "task-gate-hook")
        self.assertEqual([rc for _, rc, _, _ in mon], [2], mon)
        self.assertIn("Judge session", mon[0][3])

    def test_a_monitor_only_turn_is_held_by_the_stop(self):
        self.call("Monitor", _monitor("sed -i s/1/2/ src/a.py; tail -f src/a.py"),
                  lambda: (self.proj / "src" / "a.py").write_text("x = 2\n", encoding="utf-8"))
        self.assertEqual(self.counters(), {"tools": 1, "writes": 1})
        stop = self.stop()
        self.assertTrue(self.held(stop), stop)
        # a failed Monitor (its command exited at once) is a write too, as a failed shell call is (task 196)
        self.prompt()
        self.call("Monitor", _monitor("sed -i s/2/3/ src/a.py && false"),
                  lambda: (self.proj / "src" / "a.py").write_text("x = 3\n", encoding="utf-8"), failed=True,
                  tuid="toolu_M2")
        self.assertEqual(self.counters(), {"tools": 1, "writes": 1})
        stop = self.stop()
        self.assertTrue(self.held(stop), stop)

    def test_a_task_folder_made_by_monitor_is_flagged(self):
        def made():
            d = self.proj / ".agent" / "tasks" / "009-by-hand"
            d.mkdir()
            (d / "task.md").write_text("# 009\n\n## Status\npending\n", encoding="utf-8")
        post = self.call("Monitor", _monitor("mkdir .agent/tasks/009-by-hand && echo made"), made)
        self.assertIn(".agent/tasks/009-by-hand", "".join(o for n, _, o, _ in post if n == "task-dir-watch-hook"))
        flag = self.proj / ".agent" / "sessions" / SID / "task_dir_flag"
        self.assertTrue(flag.exists(), post)
        self.assertEqual(json.loads(flag.read_text(encoding="utf-8")).get("folders"), [".agent/tasks/009-by-hand"])
        nxt = self.ran(self.pre("Bash", _bash("ls"), tuid="toolu_B2"), "task-dir-watch-hook")
        self.assertEqual([rc for _, rc, _, _ in nxt], [2], nxt)

    def test_the_session_id_is_injected_into_a_monitor_command(self):
        mon = self.ran(self.pre("Monitor", _monitor(".claude/bin/tasks status")), "task-gate-hook")
        self.assertEqual(len(mon), 1, mon)
        self.assertEqual(mon[0][1], 0, mon)
        upd = json.loads(mon[0][2])["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(upd, {"command": f"export PLAYBOOK_SESSION_ID={SID}; .claude/bin/tasks status",
                               "description": "t", "timeout_ms": 60000})

    def test_a_websocket_monitor_runs_no_shell_and_passes(self):
        ws = {"ws": {"url": "wss://events.example.invalid/stream"}, "description": "t", "timeout_ms": 60000}
        pre = self.pre("Monitor", ws)
        for script in ("task-gate-hook", "command-guard-hook"):
            got = self.ran(pre, script)
            self.assertEqual([rc for _, rc, _, _ in got], [0], (script, pre))
        gate = [j for j in self.hook_journal() if j.get("hook") == "task-gate" and j.get("event") == "PreToolUse"]
        self.assertEqual([(j.get("tool"), j.get("decision")) for j in gate], [("Monitor", "allow")], gate)
        self.assertIn("ws", gate[0].get("detail", ""))

    def test_the_journal_names_the_monitor(self):
        self.pre("Monitor", _monitor("git status"))
        got = {(j.get("hook"), j.get("tool")) for j in self.hook_journal() if j.get("event") == "PreToolUse"}
        for hook in ("task-gate", "command-guard", "task-dir-watch"):
            self.assertIn((hook, "Monitor"), got)


if __name__ == "__main__":
    unittest.main()
