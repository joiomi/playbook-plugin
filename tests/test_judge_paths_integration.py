#!/usr/bin/env python3
"""The review paths, driven end to end at the process boundary (task 184, PLAN S11).

`tasks impl-review`, `tasks plan-review` and `tasks panel-review` are run as a user runs
them — the real CLI, the real review path, the real sandbox with real bubblewrap — with a
stand-in judge CLI (`tests/_judge_stub.py`) on PATH in place of the provider's. The stand-in
saves the prompt it received, tries one harmless write into the project, and answers with a
finding (or fails, when told to). Three ledger rows rest on what is observed here:

  * PB-JUDGE-READONLY — a single judge and every panel judge run with the project read-only:
    the write fails ("Read-only file system") and the file never appears; the CONTROL — the
    same stand-in launched as a worker, project writable — writes it, so the denial is the
    containment's and not the stand-in's;
  * PB-REVIEW-TRIAGE — the prompt a single judge receives asks for findings on stdout and
    forbids editing; the panel's prompt carries its own unchanged sentence; the CONTROL shows
    the capture would have recorded an editing instruction had one been sent;
  * PB-PANEL-QUORUM — a real panel of three, one or two of them failing on purpose, resolved
    against majority, all, an absolute number, a fraction, and an invalid value that falls
    back to majority and says so.

Skipped where bubblewrap cannot start a sandbox. Run:
    python3 -m unittest tests.test_judge_paths_integration
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tests._bwrap_standin import bwrap_usable  # noqa: E402
from tests._judge_stub import PLUGIN, TASKS, clean_env, results, stub_bin  # noqa: E402

SANDBOX = PLUGIN / "scripts" / "sandbox"


class _Project(unittest.TestCase):
    PANEL = ["opus", "sonnet", "haiku"]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.p = self.base / "proj"
        self.p.mkdir()
        env = clean_env()
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.p, check=True, env=env)
        (self.p / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.p, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "c0"], cwd=self.p, check=True, env=env)
        (self.p / ".agent" / "tasks").mkdir(parents=True)
        (self.p / ".agent" / "models.json").write_text(
            json.dumps({"panel": self.PANEL, "default_judge": "sonnet"}), encoding="utf-8")
        self.stub_out = self.base / "stub-out"
        self.stub_out.mkdir()
        self.env = clean_env(PATH=f"{stub_bin(self.base / 'bin')}{os.pathsep}{env['PATH']}",
                             PB_STUB_DIR=str(self.stub_out))
        r = self.tasks("new", "light", "probe", "a probe task for the judges")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tasks(self, *args, env=None):
        return subprocess.run([str(TASKS), *args], cwd=self.p, env=env or self.env, capture_output=True,
                              text=True, timeout=600)

    def written(self) -> bool:
        return (self.p / "judge-was-here.txt").exists()


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class JudgesRunReadOnly(_Project):
    def test_a_single_judge_cannot_write_the_project(self):
        r = self.tasks("impl-review", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = results(self.stub_out, "result")
        self.assertEqual(len(got), 1, got)
        self.assertIn("Read-only file system", got[0])
        self.assertIn("sandboxed=1", got[0])
        self.assertFalse(self.written())
        landed = (self.p / ".agent" / "tasks" / "001-probe" / "judge-single.md").read_text(encoding="utf-8")
        self.assertIn("WRITE-ATTEMPT: DENIED", landed)          # the review the judge gave is the one recorded

    def test_every_panel_judge_cannot_write_the_project(self):
        r = self.tasks("panel-review", "1", "--mode", "impl")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PANEL PASS: 3/3", r.stdout)
        got = results(self.stub_out, "result")
        self.assertEqual(len(got), 3, got)
        for g in got:
            self.assertIn("Read-only file system", g)
        self.assertFalse(self.written())

    def test_control_the_same_judge_launched_as_a_worker_writes(self):
        # the stand-in's write is real: in a launch whose project is writable it goes through
        r = subprocess.run([str(SANDBOX), "--agent", "claude", "--project-root", str(self.p), "--", "-p", "--model",
                            "worker"], cwd=self.p, env=self.env, input="write something", capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WRITE-ATTEMPT: WROTE", r.stdout)
        self.assertTrue(self.written())


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class AJudgesTextIsQuotedInTheRecord(_Project):
    """Task 200 (#43, #3), at the process boundary: the single claude judge runs with the panel seat's tool limit,
    and a real panel writes a format-2 round whose judges' lines are quoted."""

    def tool_values(self, argv_text):
        lines = argv_text.splitlines()
        return [lines[lines.index(f) + 1] if f in lines else None for f in ("--tools", "--allowedTools")]

    def test_the_single_claude_judge_runs_with_read_glob_grep_only(self):
        r = self.tasks("impl-review", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        argv = results(self.stub_out, "argv")
        self.assertEqual(len(argv), 1, argv)
        self.assertEqual(self.tool_values(argv[0]), ["Read,Glob,Grep", "Read,Glob,Grep"], argv[0])

    def test_control_every_panel_seat_runs_with_it(self):
        r = self.tasks("panel-review", "1", "--mode", "impl")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        argv = results(self.stub_out, "argv")
        self.assertEqual(len(argv), 3, argv)
        for a in argv:
            self.assertEqual(self.tool_values(a), ["Read,Glob,Grep", "Read,Glob,Grep"], a)

    def test_a_panel_round_is_format_2_with_its_judges_quoted(self):
        r = self.tasks("panel-review", "1", "--mode", "impl")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        jm = (self.p / ".agent" / "tasks" / "001-probe" / "judge.md").read_text(encoding="utf-8")
        lines = jm.splitlines()
        self.assertTrue(lines[0].startswith("# Panel Impl Review — "), lines[:2])
        self.assertEqual(lines[1], "**Round-format:** 2")
        finding = "**Important** — `src/x.py:1` a finding from the stand-in judge"
        self.assertEqual(sum(ln.startswith("> " + finding) for ln in lines), 3, jm)
        self.assertFalse(any(ln.startswith(finding) for ln in lines), jm)


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class TheSingleJudgeIsAskedForFindingsOnly(_Project):
    def test_the_prompt_a_single_judge_receives_forbids_editing(self):
        for verb in ("plan-review", "impl-review"):
            with self.subTest(verb=verb):
                for f in self.stub_out.glob("prompt-*"):
                    f.unlink()
                r = self.tasks(verb, "1")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                prompts = results(self.stub_out, "prompt")
                self.assertEqual(len(prompts), 1)
                self.assertIn("DO NOT edit any files", prompts[0])
                self.assertIn("stdout only", prompts[0])
                self.assertNotIn("Then edit", prompts[0])
                self.assertNotIn("revise the ## Work Plan gates", prompts[0])

    def test_the_panel_prompt_keeps_its_own_sentence(self):
        r = self.tasks("panel-review", "1", "--mode", "impl")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        prompts = results(self.stub_out, "prompt")
        self.assertEqual(len(prompts), 3)
        for p in prompts:
            self.assertIn("DO NOT edit any files. Output your findings to stdout only.", p)

    def test_control_the_capture_records_an_editing_instruction_when_one_is_sent(self):
        r = subprocess.run([str(SANDBOX), "--agent", "claude", "--project-root", str(self.p), "--ro-project", "--",
                            "-p", "--model", "control"], cwd=self.p, env=self.env,
                           input="Then edit task.md and revise the ## Work Plan gates.", capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Then edit", results(self.stub_out, "prompt")[-1])


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class APanelIsResolvedAgainstItsQuorum(_Project):
    """haiku fails on purpose in each case below (two of three answer), unless said otherwise."""

    def panel(self, quorum: "str | None", fail=("claude-haiku-4-5",)):
        env = dict(self.env, PB_STUB_FAIL_MODELS=" ".join(fail))
        if quorum is not None:
            env["PLAYBOOK_PANEL_QUORUM"] = quorum
        return self.tasks("panel-review", "1", "--mode", "impl", env=env)

    def verdict(self, r) -> str:
        line = next((x for x in r.stdout.splitlines() if x.startswith("PANEL ")), "")
        self.assertTrue(line, r.stdout + r.stderr)
        return line

    def test_majority_passes_two_of_three(self):
        self.assertIn("PANEL PASS: 2/3 judges succeeded, quorum 2", self.verdict(self.panel(None)))

    def test_all_fails_two_of_three(self):
        self.assertIn("PANEL FAIL: 2/3", self.verdict(self.panel("all")))

    def test_an_absolute_number_is_not_rounded(self):
        self.assertIn("PANEL FAIL: 2/3", self.verdict(self.panel("3")))
        self.assertIn("PANEL PASS: 2/3", self.verdict(self.panel("2")))

    def test_a_fraction_rounds_up(self):
        self.assertIn("PANEL PASS: 2/3 judges succeeded, quorum 2", self.verdict(self.panel("0.5")))
        self.assertIn("PANEL FAIL: 2/3", self.verdict(self.panel("0.9")))

    def test_an_invalid_value_falls_back_to_majority_and_says_so(self):
        r = self.panel("banana")
        self.assertIn("PANEL PASS: 2/3 judges succeeded, quorum 2", self.verdict(r))
        self.assertIn("banana", r.stderr)

    def test_control_majority_fails_when_two_of_three_fail(self):
        self.assertIn("PANEL FAIL: 1/3", self.verdict(self.panel(None, fail=("claude-haiku-4-5", "claude-sonnet-5"))))


if __name__ == "__main__":
    unittest.main()
