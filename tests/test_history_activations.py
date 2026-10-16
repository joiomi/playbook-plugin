#!/usr/bin/env python3
"""The task transitions come from the CLI's own records in the journal (task 221).

Before, `tasks timeline`, `tagger`, `tag`, retro and intent found activations by reading command lines of the shell
log — and the plan panel of task 221 showed why that cannot be made right once a line is a whole command: a skipped
`&&` branch (`false && tasks work 7`), a heredoc or a grep naming `tasks work 5`, a wrapper or an assignment in
front, all read wrong. Now `tasks work N`, `tasks new` and `tasks work done` each append a record
(`pb_journal.append_lifecycle`) when the transition HAPPENED, and every reader reads those (`history.activations`,
`retro.lifecycle_records`).

Held here: every activation is shown (back and forth, the same task again, several in one second — task 171's
cases); `tasks tag` closes a task at its `done`; the real CLI writes a record for an activation, a creation and a
close and none for a refused activation; two activations in one command are two records in order and two windows in
order; a shell log naming `tasks work 5` adds nothing; a journal that is a link or a FIFO reads as none.

Run: python3 -m unittest tests.test_history_activations
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
CHAT = ("**[M001]** [2026-10-01 07:00:01 UTC] `HOST` (claude/pid-1)\n\nfirst message\n\n---\n\n")


def rec(stamp: str, event: str, task, detail: str = "") -> str:
    r = {"ts": stamp.replace(" ", "T") + "Z", "session_id": "pid-1", "hook": "tasks", "decision": "record",
         "reason": event, "task": str(task)}
    if detail:
        r["detail"] = detail
    return json.dumps(r, separators=(",", ":")) + "\n"


BACK_AND_FORTH = (rec("2026-10-01 10:00:00", "work", 7) + rec("2026-10-01 10:05:00", "work", 8)
                  + rec("2026-10-01 10:09:00", "work", 7))
THE_SAME_TASK_AGAIN = rec("2026-10-01 10:00:00", "work", 7) + rec("2026-10-01 10:00:30", "work", 7)
ALL_IN_ONE_SECOND = (rec("2026-10-01 12:00:00", "work", 7) + rec("2026-10-01 12:00:00", "work", 8)
                     + rec("2026-10-01 12:00:00", "work", 7))
WITH_A_CREATION = rec("2026-10-01 11:00:00", "new", 9, "quick x") + rec("2026-10-01 11:00:05", "work", 9)
# what else a journal holds: other hooks' records, a broken line, a record of another shape
NOISE = ('{"ts":"2026-10-01T10:01:00Z","session_id":"","hook":"close","decision":"record","reason":"verify contract"}\n'
         'not json\n'
         '{"ts":"2026-10-01T10:02:00Z","hook":"tasks","decision":"allow","reason":"work","task":"5"}\n'
         '{"ts":"yesterday","hook":"tasks","decision":"record","reason":"work","task":"6"}\n'
         '{"ts":"2026-10-01T10:03:00Z","hook":"tasks","decision":"record","reason":"work","task":"x5"}\n')


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve() / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        (self.project / ".agent" / "journal").mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / ".agent" / "chat_log.md").write_text(CHAT, encoding="utf-8")
        self.journal = self.project / ".agent" / "journal" / "enforcement.jsonl"

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "PLAYBOOK_PROJECT_DIR", "CLAUDE_PROJECT_DIR", "PLAYBOOK_SANDBOXED",
                            "BASH_ENV")}
        env["PYTHONPATH"] = str(PLUGIN)
        env["PLAYBOOK_SESSION_ID"] = "pid-act221"
        env["HOME"] = self._tmp.name
        return env

    def cli(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project, env=self.env(),
                           capture_output=True, text=True, timeout=120)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def run_cli(self, journal: str, *args: str) -> str:
        self.journal.write_text(journal, encoding="utf-8")
        return self.cli(*args).stdout

    def records(self) -> "list[tuple[str, str, str]]":
        sys.path.insert(0, str(PLUGIN))
        try:
            from tasks.retro import lifecycle_records
        finally:
            sys.path.remove(str(PLUGIN))
        return [(event, task, detail) for _, event, task, detail in lifecycle_records(self.journal)]


class TimelineShowsEveryActivation(_Project):
    def lines(self, journal: str) -> "list[str]":
        return [ln for ln in self.run_cli(journal, "timeline").splitlines() if "tasks " in ln]

    def test_going_back_to_a_task_is_shown(self):
        self.assertEqual(self.lines(BACK_AND_FORTH),
                         ["2026-10-01 10:00:00 UTC  tasks work 7",
                          "2026-10-01 10:05:00 UTC  tasks work 8",
                          "2026-10-01 10:09:00 UTC  tasks work 7"])

    def test_the_same_activation_again_later_and_several_in_one_second_are_shown(self):
        self.assertEqual(len(self.lines(THE_SAME_TASK_AGAIN)), 2)
        self.assertEqual([ln.split("  ", 1)[1] for ln in self.lines(ALL_IN_ONE_SECOND)],
                         ["tasks work 7", "tasks work 8", "tasks work 7"])

    def test_a_creation_is_shown_and_noise_is_not(self):
        self.assertEqual([ln.split("  ", 1)[1] for ln in self.lines(NOISE + WITH_A_CREATION)],
                         ["tasks new quick x (task 9)", "tasks work 9"])

    def test_a_shell_log_naming_an_activation_adds_nothing(self):
        (self.project / ".agent" / "bash_history").write_text(
            "2026-10-01 10:00:00 | AGENT | rg \"tasks work 5\" && .claude/bin/tasks work 6\n", encoding="utf-8")
        r = self.cli("timeline")
        self.assertEqual([ln for ln in r.stdout.splitlines() if "tasks " in ln], [])
        self.assertIn("no task transition recorded", r.stderr)


class TaggerShowsEveryActivation(_Project):
    def test_going_back_to_a_task_is_shown(self):
        out = self.run_cli(BACK_AND_FORTH, "tagger").splitlines()
        self.assertEqual([ln for ln in out if ln.startswith("---") and "tasks " in ln],
                         ["--- tasks work 7 ---", "--- tasks work 8 ---", "--- tasks work 7 ---"])


class TagAttributesEveryActivation(_Project):
    """`tasks tag` writes the task spans into the chat log from the same records: the SECOND `done` of a history
    closes its task too, and a return to an earlier task is seen (task 171's cases)."""

    JOURNAL = (rec("2026-10-01 10:00:00", "work", 7) + rec("2026-10-01 10:10:00", "done", 7)
               + rec("2026-10-01 10:20:00", "work", 8) + rec("2026-10-01 10:30:00", "done", 8)
               + rec("2026-10-01 10:40:00", "work", 7))

    def setUp(self):
        super().setUp()
        chat = "".join(f"**[M00{n}]** [2026-10-01 10:{minute}:00 UTC] `HOST` (claude/pid-1)\n\nmessage {n}\n\n---\n\n"
                       for n, minute in enumerate(("05", "15", "25", "35", "45"), 1))
        (self.project / ".agent" / "chat_log.md").write_text(chat, encoding="utf-8")

    def attribution(self) -> "dict[str, str | None]":
        self.run_cli(self.JOURNAL, "tag")
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

    def test_control_one_task_opened_and_closed(self):
        self.JOURNAL = "".join(self.JOURNAL.splitlines(keepends=True)[:2])
        self.assertEqual(self.attribution(),
                         {"M001": "007", "M002": None, "M003": None, "M004": None, "M005": None})

    def test_no_record_says_so(self):
        self.journal.write_text("", encoding="utf-8")
        r = self.cli("tag", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("No task activation recorded", r.stderr)


class TheCliRecordsWhatHappened(_Project):
    def test_new_work_and_done_are_recorded_and_a_refused_activation_is_not(self):
        self.cli("new", "quick", "first-thing")
        self.cli("work", "1")
        r = self.cli("work", "99", ok=False)          # no such task: refused
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.records(), [("new", "001", "quick first-thing"), ("work", "001", "")])
        self.cli("work", "done", "--force", "--reason", "test close")
        self.assertEqual(self.records()[-1], ("done", "001", ""))

    def test_two_activations_in_one_command_are_two_records_and_two_windows_in_order(self):
        self.cli("new", "quick", "a")
        self.cli("new", "quick", "b")
        sh = subprocess.run(["bash", "-c", f'"{sys.executable}" -m tasks.cli work 1 >/dev/null && '
                                           f'"{sys.executable}" -m tasks.cli work 2 --force >/dev/null'],
                            cwd=self.project, env=self.env(), capture_output=True, text=True, timeout=120)
        self.assertEqual(sh.returncode, 0, sh.stderr)
        works = [t for e, t, _ in self.records() if e == "work"]
        self.assertEqual(works, ["001", "002"])
        sys.path.insert(0, str(PLUGIN))
        try:
            from tasks.retro import build_task_windows
        finally:
            sys.path.remove(str(PLUGIN))
        w = build_task_windows(self.project / ".agent" / "chat_log.md", self.journal)
        self.assertEqual(sorted(w, key=lambda n: w[n][0]), [1, 2])
        self.assertLessEqual(w[1][0], w[2][0])
        self.assertEqual(w[1][1], w[2][0], "task 1's window ends where task 2's begins")


class AJournalThatIsNotTheLanesOwn(_Project):
    def test_a_linked_or_fifo_journal_reads_as_no_records(self):
        outside = Path(self._tmp.name) / "outside.jsonl"
        outside.write_text(BACK_AND_FORTH, encoding="utf-8")
        self.journal.symlink_to(outside)
        self.assertEqual(self.records(), [])
        self.journal.unlink()
        os.mkfifo(self.journal)
        self.assertEqual(self.records(), [])        # returns: a FIFO is not read


if __name__ == "__main__":
    unittest.main()
