#!/usr/bin/env python3
"""`tasks timeline` and `tasks tagger` show every activation the shell history holds
(PLAN S11, task 171; retro 107 R12, from task 088).

The logger once wrote a command twice — the agent's line and the script's echo, in the
same second, one under the other — and both readers hid that by dropping every SECOND
sight of a command. Since task 088 the logger writes a command once, so that rule hid
every second genuine activation: `tasks work 7`, `tasks work 8`, `tasks work 7` showed
two entries.

Run: python3 -m unittest tests.test_history_activations
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"

BACK_AND_FORTH = (
    "2026-10-01 10:00:00 | AGENT | .claude/bin/tasks work 7\n"
    "2026-10-01 10:00:05 | AGENT | ls\n"
    "2026-10-01 10:05:00 | AGENT | .claude/bin/tasks work 8\n"
    "2026-10-01 10:09:00 | AGENT | .claude/bin/tasks work 7\n")
THE_SAME_TASK_AGAIN = (
    "2026-10-01 10:00:00 | AGENT | .claude/bin/tasks work 7\n"
    "2026-10-01 10:00:30 | AGENT | .claude/bin/tasks work 7\n")
A_LEGACY_ECHO = (
    "2026-10-01 11:00:00 | AGENT | .claude/bin/tasks work 9\n"
    "2026-10-01 11:00:00 | SCRIPT | .claude/bin/tasks work 9\n"
    "2026-10-01 11:20:00 | AGENT | .claude/bin/tasks new quick x\n"
    "2026-10-01 11:20:00 | SCRIPT | /home/u/.claude/plugins/cache/p/scripts/tasks new quick x\n")
# impl panel round 1 (both codex seats, grok): my first rule called any repeat of a command
# within two lines and the same second an echo — these are real, written by one fast script
ALL_IN_ONE_SECOND = (
    "2026-10-01 12:00:00 | AGENT | .claude/bin/tasks work 7\n"
    "2026-10-01 12:00:00 | AGENT | .claude/bin/tasks work 8\n"
    "2026-10-01 12:00:00 | AGENT | .claude/bin/tasks work 7\n")
THE_SAME_COMMAND_TWICE_IN_ONE_SECOND = (
    "2026-10-01 12:00:00 | AGENT | .claude/bin/tasks work 7\n"
    "2026-10-01 12:00:00 | AGENT | .claude/bin/tasks work 7\n")
A_SCRIPT_LINE_THAT_ECHOES_NOTHING = (
    "2026-10-01 12:00:00 | AGENT | .claude/bin/tasks work 7\n"
    "2026-10-01 12:00:00 | SCRIPT | .claude/bin/tasks work 8\n"
    "2026-10-01 12:00:00 | SCRIPT | .claude/bin/tasks work 7\n")
# impl panel round 2 (sonnet, codex-medium): "directly before it" was the previous
# ACTIVATION line, not the previous line — a command in between did not end the pairing
A_SCRIPT_LINE_WITH_A_COMMAND_IN_BETWEEN = (
    "2026-10-01 12:00:00 | AGENT | .claude/bin/tasks work 7\n"
    "2026-10-01 12:00:00 | AGENT | ls\n"
    "2026-10-01 12:00:00 | SCRIPT | .claude/bin/tasks work 7\n")
CHAT = ("**[M001]** [2026-10-01 07:00:01 UTC] `HOST` (claude/pid-1)\n\nfirst message\n\n---\n\n")


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve() / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / ".agent" / "chat_log.md").write_text(CHAT, encoding="utf-8")

    def run_cli(self, history: str, *args: str) -> str:
        (self.project / ".agent" / "bash_history").write_text(history, encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "PLAYBOOK_PROJECT_DIR", "CLAUDE_PROJECT_DIR", "PLAYBOOK_SANDBOXED")}
        env["PYTHONPATH"] = str(PLUGIN)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


class TimelineShowsEveryActivation(_Project):
    def lines(self, history: str) -> "list[str]":
        return [ln for ln in self.run_cli(history, "timeline").splitlines() if "tasks " in ln]

    def test_going_back_to_a_task_is_shown(self):
        self.assertEqual(self.lines(BACK_AND_FORTH),
                         ["2026-10-01 10:00:00  tasks work 7",
                          "2026-10-01 10:05:00  tasks work 8",
                          "2026-10-01 10:09:00  tasks work 7"])

    def test_the_same_activation_run_again_later_is_shown(self):
        self.assertEqual(len(self.lines(THE_SAME_TASK_AGAIN)), 2)

    def test_control_an_old_echo_pair_is_still_one_entry(self):
        # a history of the old logger: the agent's line, and right under it the SCRIPT
        # line of the same command in the same second — one activation, then and now
        self.assertEqual(self.lines(A_LEGACY_ECHO),
                         ["2026-10-01 11:00:00  tasks work 9",
                          "2026-10-01 11:20:00  tasks new quick x"])

    def test_commands_of_one_second_are_all_shown(self):
        self.assertEqual([ln.split("  ", 1)[1] for ln in self.lines(ALL_IN_ONE_SECOND)],
                         ["tasks work 7", "tasks work 8", "tasks work 7"])
        self.assertEqual(len(self.lines(THE_SAME_COMMAND_TWICE_IN_ONE_SECOND)), 2)

    def test_only_a_script_line_right_under_its_agent_line_is_an_echo(self):
        # SCRIPT lines that repeat nothing directly above them are activations
        self.assertEqual([ln.split("  ", 1)[1] for ln in self.lines(A_SCRIPT_LINE_THAT_ECHOES_NOTHING)],
                         ["tasks work 7", "tasks work 8", "tasks work 7"])
        # … and "directly above" is the line above in the FILE: any line in between ends it
        self.assertEqual([ln.split("  ", 1)[1] for ln in self.lines(A_SCRIPT_LINE_WITH_A_COMMAND_IN_BETWEEN)],
                         ["tasks work 7", "tasks work 7"])


class TaggerShowsEveryActivation(_Project):
    def transitions(self, history: str) -> "list[str]":
        return [ln for ln in self.run_cli(history, "tagger").splitlines() if ln.startswith("---") and "tasks " in ln]

    def test_going_back_to_a_task_is_shown(self):
        self.assertEqual(self.transitions(BACK_AND_FORTH),
                         ["--- tasks work 7 ---", "--- tasks work 8 ---", "--- tasks work 7 ---"])

    def test_control_an_old_echo_pair_is_still_one_entry(self):
        self.assertEqual(self.transitions(A_LEGACY_ECHO),
                         ["--- tasks work 9 ---", "--- tasks new quick x ---"])


class TagAttributesEveryActivation(_Project):
    """`tasks tag` writes the task spans into the chat log from the same lines. With
    every second identical command dropped, the SECOND `tasks work done` of a history
    never closed its task — every later message was attributed to it — and a return to
    an earlier task was not seen at all."""

    HISTORY = ("2026-10-01 10:00:00 | AGENT | .claude/bin/tasks work 7\n"
               "2026-10-01 10:10:00 | AGENT | .claude/bin/tasks work done\n"
               "2026-10-01 10:20:00 | AGENT | .claude/bin/tasks work 8\n"
               "2026-10-01 10:30:00 | AGENT | .claude/bin/tasks work done\n"
               "2026-10-01 10:40:00 | AGENT | .claude/bin/tasks work 7\n")

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        from tasks.retro import bash_history_ts_to_utc     # the history is local time, the chat log UTC
        chat = "".join(f"**[M00{n}]** [{bash_history_ts_to_utc(f'2026-10-01 10:{minute}:00')} UTC] `HOST` (claude/pid-1)"
                       f"\n\nmessage {n}\n\n---\n\n" for n, minute in enumerate(("05", "15", "25", "35", "45"), 1))
        (self.project / ".agent" / "chat_log.md").write_text(chat, encoding="utf-8")

    def attribution(self) -> "dict[str, str | None]":
        self.run_cli(self.HISTORY, "tag")
        current, out = None, {}
        for line in (self.project / ".agent" / "chat_log.md").read_text(encoding="utf-8").splitlines():
            if line.startswith("<!-- T"):
                current = line[6:9]
            elif line.startswith("<!-- /T"):
                current = None
            elif line.startswith("**[M"):
                out[line[3:7]] = current
        return out

    def test_a_second_close_ends_its_task_and_a_return_is_seen(self):
        self.assertEqual(self.attribution(),
                         {"M001": "007", "M002": None, "M003": "008", "M004": None, "M005": "007"})

    def test_a_history_written_within_one_second_closes_its_tasks_too(self):
        # (codex-high, grok) work 7 / done / work 8 / done, all in one second: the second
        # close was dropped again by my first rule, and task 8 stayed open
        self.HISTORY = "".join(ln[:11] + "10:00:00" + ln[19:] for ln in self.HISTORY.splitlines(keepends=True)[:4])
        self.assertEqual(self.HISTORY.count("10:00:00"), 4)
        self.assertEqual(self.attribution(),
                         {"M001": None, "M002": None, "M003": None, "M004": None, "M005": None})

    def test_control_one_task_opened_and_closed(self):
        self.HISTORY = "".join(self.HISTORY.splitlines(keepends=True)[:2])
        self.assertEqual(self.attribution(),
                         {"M001": "007", "M002": None, "M003": None, "M004": None, "M005": None})


if __name__ == "__main__":
    unittest.main()
