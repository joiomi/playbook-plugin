#!/usr/bin/env python3
"""PB-CLI-HELP-EXHAUSTIVE (PLAN S8b, task 100): EVERY top-level `tasks` command
accepts -h and --help, prints the usage text, exits zero and mutates nothing.

The suite already covered four representative mutating arms (PB-CLI-HELP-TASKS);
this sweep iterates `cli.COMMANDS` itself, so an arm added later is covered the
day it appears. "Mutates nothing" is measured, not assumed: a byte snapshot of
the whole `.agent/` tree plus mtime_ns probes on task.md and chat_log.md, taken
around every invocation, with an ACTIVE task whose gates are all checked — the
shape `work done` would close, `new` would extend, `compact` would rewrite and
`tag` would re-tag if the help intercept in tasks/cli.py::main ever regressed.

Negative controls: the same fixture, run WITHOUT the flag, does mutate (`work
done` closes the task and moves the mtime), and the command tuple the sweep
walks is not trivially small.

Run: python3 tests/test_cli_help_exhaustive.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "plugins/playbook"
sys.path.insert(0, str(PLUGIN))

from tasks.cli import COMMANDS  # noqa: E402
from tasks.template import usage_text  # noqa: E402

SID = "pid-help-sweep"
TASK = ("# 001 - Sweep\n\n## Status\npending\n\n## Risk\nreversible\n\n"
        "## Work Plan\n- [x] G1: done — ok\n\n## Parked\n"
        "<!-- archive:start -->\ncold\n<!-- archive:end -->\n")
CHAT = ("# Project Chat Log\n\n---\n\n"
        "**[M001]** [2026-09-27 10:00:00 UTC] `HOST` (claude/" + SID + ")\n\nhello\n\n---\n")


class EveryCommandHelpIsDry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        agent = self.project / ".agent"
        (agent / "tasks" / "001-sweep").mkdir(parents=True)
        self.task_md = agent / "tasks" / "001-sweep" / "task.md"
        self.task_md.write_text(TASK, encoding="utf-8")
        self.chat_log = agent / "chat_log.md"
        self.chat_log.write_text(CHAT, encoding="utf-8")
        (agent / "config.json").write_text('{"verify": {"_always": []}}\n', encoding="utf-8")
        # Impl panel r1 (opus): arms write OUTSIDE .agent/ too — mindmap-sync and
        # prepare-merge rewrite MIND_MAP*.md at the root, init writes CLAUDE.md,
        # .claude/, .gitignore and ~/.claude. So: seed the mind-map pair (something
        # to change), snapshot the WHOLE project (minus .git) and an isolated HOME.
        (self.project / "MIND_MAP.md").write_text(
            "# Mind Map\n\n[1] **Overview** - see [2].\n\n[2] **Store** ↗ - summary [1].\n", encoding="utf-8")
        (self.project / "MIND_MAP_OVERFLOW.md").write_text(
            "[2] **Store** - the fuller detail [1].\n", encoding="utf-8")
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID=SID,
                        HOME=str(self.home), USERPROFILE=str(self.home))
        self.env.pop("BASH_ENV", None)
        # An ACTIVE task: the pointer is what `work done` would act on.
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        # Age the files so a rewrite that lands in the same second still moves mtime_ns
        # on filesystems with coarse timestamps.
        old = time.time() - 120
        for p in (self.task_md, self.chat_log):
            os.utime(p, (old, old))

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "tasks.cli", *args], cwd=self.project,
            env=self.env, capture_output=True, text=True, timeout=60,
        )

    def snapshot(self):
        tree = {}
        for root in (self.project, self.home):
            for p in sorted(root.rglob("*")):
                rel = p.relative_to(root)
                if rel.parts and rel.parts[0] == ".git":
                    continue
                tree[f"{root.name}/{rel}"] = p.read_bytes() if p.is_file() else None
        return tree, self.task_md.stat().st_mtime_ns, self.chat_log.stat().st_mtime_ns

    def test_every_command_help_prints_usage_and_writes_nothing(self):
        expected_head = usage_text().splitlines()[0]
        before = self.snapshot()
        for cmd in COMMANDS:
            for flag in ("-h", "--help"):
                with self.subTest(command=cmd, flag=flag):
                    r = self.cli(cmd, flag)
                    self.assertEqual(r.returncode, 0,
                                     f"`tasks {cmd} {flag}` exited {r.returncode}: {r.stderr}")
                    # The general usage text, or the one arm with dedicated help
                    # (`merge-doctor`, which prints its own `Usage: tasks merge-doctor …`).
                    self.assertTrue(r.stdout.startswith("Usage: tasks"),
                                    f"`tasks {cmd} {flag}` did not print a usage text:\n{r.stdout[:200]}")
                    if cmd != "merge-doctor":
                        self.assertIn(expected_head, r.stdout)
                    self.assertNotIn("Traceback", r.stdout + r.stderr)
                    self.assertEqual(self.snapshot(), before,
                                     f"`tasks {cmd} {flag}` changed .agent/ (or an mtime)")

    def test_bare_help_forms_print_usage_and_write_nothing(self):
        expected_head = usage_text().splitlines()[0]
        before = self.snapshot()
        for argv in (("-h",), ("--help",), ("help",), ()):
            with self.subTest(argv=argv):
                r = self.cli(*argv)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(expected_head, r.stdout)
                self.assertEqual(self.snapshot(), before)

    def test_control_the_same_fixture_does_mutate_without_the_flag(self):
        # The sweep proves nothing if this fixture cannot be mutated: without
        # --help, `work done` closes the active task and moves task.md's mtime.
        before = self.snapshot()
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("Task 001 done.", r.stdout)
        after = self.snapshot()
        self.assertNotEqual(after, before, "control: `work done` changed nothing")
        self.assertNotEqual(after[1], before[1], "control: task.md mtime did not move")
        self.assertIn("\ndone", self.task_md.read_text(encoding="utf-8"))

    def test_control_the_snapshot_sees_writes_outside_agent_and_in_home(self):
        # The whole-tree snapshot must notice a root-level and a HOME write, or the
        # mindmap/init arms could regress unseen (impl panel r1).
        before = self.snapshot()
        (self.project / "MIND_MAP.md").write_text("changed\n", encoding="utf-8")
        self.assertNotEqual(self.snapshot(), before, "a MIND_MAP.md rewrite went unnoticed")
        before = self.snapshot()
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
        self.assertNotEqual(self.snapshot(), before, "a ~/.claude write went unnoticed")

    def test_control_the_sweep_is_not_trivially_small(self):
        self.assertGreaterEqual(len(COMMANDS), 20)
        for must in ("work", "new", "compact", "tag", "audit", "handoff", "blocked"):
            self.assertIn(must, COMMANDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
