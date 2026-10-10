"""`tasks intent` does not spend judge calls unasked (gauntlet flag C4, task 073; owner's
decision D3-C4 "(fix)", 2026-09-24; PLAN S11; task 173).

`tasks intent <N>` starts up to four blind extractions — four calls to the default
judge, each as long as the review timeout allows. It started them as soon as it had
printed which layers have evidence: nothing said what it was about to spend and nothing
could stop it, short of knowing `--collect-only` beforehand.

Now the bare command prints what it would spend — how many calls, on which seat, the
time limit of each — spends nothing, writes nothing and exits 2; the same command with
`--yes` runs. (The CLI is run by an agent, not at a terminal: it asks the way it asks
elsewhere, by refusing with the flag to pass. `commands/intent.md` tells the agent to
put the number to the user first.)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))

from tasks import history, review  # noqa: E402


@contextlib.contextmanager
def _chdir(d: Path):
    prev = Path.cwd()
    os.chdir(d)
    try:
        yield
    finally:
        os.chdir(prev)


class _Project(unittest.TestCase):
    """A project with task 042 and a chat file: two layers have evidence (chat, the
    task record), two have none (no commit range) — so a run is two judge calls."""

    DEFAULT_JUDGE = "claude:opus"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        self.agent = self.project / ".agent"
        self.task_dir = self.agent / "tasks" / "042-demo"
        self.task_dir.mkdir(parents=True)
        (self.task_dir / "task.md").write_text(
            "# 042 - demo\n## Status\ndone\n## Intent\nmake the thing\n"
            "## Work Plan\n- [x] a gate\n", encoding="utf-8")
        (self.agent / "models.json").write_text(
            json.dumps({"panel": ["claude:opus", "claude:sonnet"],
                        "default_judge": self.DEFAULT_JUDGE}), encoding="utf-8")
        self.chat = Path(self._tmp.name) / "chat.md"
        self.chat.write_text("**[M001]** [2026-01-01 10:00:00 UTC] `HOST`\n\nplease make the thing\n",
                             encoding="utf-8")
        review._PB_JOURNAL_MOD = None
        review._PB_JOURNAL_LOADED = False
        from provider.adapters.claude import ClaudeAdapter
        self.calls = []

        def run(adapter_self, **kw):
            self.calls.append(kw)
            return "# Intent inferred\n- make the thing\n"
        p = mock.patch.object(ClaudeAdapter, "run_headless_judge", run)
        p.start()
        self.addCleanup(p.stop)

    def _intent(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with _chdir(self.project), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                history.cmd_intent(["42", "--chat-file", str(self.chat), *args])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def _spend_records(self):
        p = self.agent / "journal" / "enforcement.jsonl"
        if not p.exists():
            return []
        rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return [r for r in rows if r.get("kind") == "intent"]

    def _runs(self):
        d = self.task_dir / "intent"
        return sorted(x.name for x in d.iterdir()) if d.exists() else []


class TheBareCommandSpendsNothing(_Project):
    def test_it_says_what_it_would_spend_and_exits_2(self):
        code, out, err = self._intent()
        self.assertEqual(code, 2, err)
        self.assertEqual(self.calls, [], "a judge was called before anything was asked")
        self.assertIn("this would run 2 judge call(s) on claude:opus:high", err)
        self.assertIn("Nothing was spent and nothing was written.", err)
        self.assertIn("--yes", err)
        self.assertIn("--collect-only", err)

    def test_it_leaves_no_run_directory_and_no_spend_record(self):
        self._intent()
        self.assertEqual(self._runs(), [])
        self.assertEqual(self._spend_records(), [])

    def test_it_still_shows_which_layers_have_evidence(self):
        # the free, local part — what the number in the line is counted from
        code, out, err = self._intent()
        marks = {ln.split()[0]: ln.split()[1] for ln in out.splitlines() if ln.startswith("  ")}
        self.assertEqual(marks, {"chat": "✓", "taskmd": "✓", "code": "✗", "tests": "✗"})

    def test_the_line_says_the_time_limit_of_each_call(self):
        code, out, err = self._intent("--timeout", "77")
        self.assertEqual(code, 2)
        self.assertIn("time limit 77s each", err)

    def test_for_a_claude_seat_it_says_the_budget_cap_of_each_call(self):
        with mock.patch.dict(os.environ, {"PLAYBOOK_JUDGE_BUDGET_USD": "3.5"}):
            code, out, err = self._intent()
        self.assertIn("each capped at $3.5", err)


class WithYesItRuns(_Project):
    def test_it_makes_the_calls_it_announced(self):
        code, out, err = self._intent("--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self._runs()), 1)
        self.assertEqual([r["seat"] for r in self._spend_records()], ["claude:opus:high"] * 2)
        self.assertNotIn("this would run", err)

    def test_the_flag_may_come_before_the_other_options(self):
        out, err = io.StringIO(), io.StringIO()
        with _chdir(self.project), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            history.cmd_intent(["42", "--yes", "--chat-file", str(self.chat)])
        self.assertEqual(len(self.calls), 2)


class TheSeatInTheLineIsTheSeatThatRuns(_Project):
    DEFAULT_JUDGE = "claude:sonnet"

    def test_another_default_judge(self):
        code, out, err = self._intent()
        self.assertIn("judge call(s) on claude:sonnet:high", err)
        self._intent("--yes")
        self.assertEqual({r["seat"] for r in self._spend_records()}, {"claude:sonnet:high"})
        self.assertEqual({c["model"] for c in self.calls}, {"sonnet"})


class WhatNeverSpentIsNotAsked(_Project):
    def test_collect_only_writes_its_prompts_as_before(self):
        code, out, err = self._intent("--collect-only")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self._runs()), 1)
        self.assertNotIn("this would run", err)

    def test_no_evidence_is_still_the_error_it_was(self):
        (self.task_dir / "task.md").unlink()
        out, err = io.StringIO(), io.StringIO()
        with _chdir(self.project), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                history.cmd_intent(["42"])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("no available evidence on any layer", err.getvalue())
        self.assertNotIn("this would run", err.getvalue())


class ThroughTheCommandLine(_Project):
    """The real CLI in a subprocess, with a `claude` on PATH that leaves a file behind if
    it is ever started: the refusal is decided before any judge binary is looked for."""

    def _cli(self, *args):
        bindir = Path(self._tmp.name) / "bin"
        bindir.mkdir(exist_ok=True)
        fake = bindir / "claude"
        fake.write_text(f"#!/bin/sh\ntouch '{self._tmp.name}/JUDGE-STARTED'\necho '# Intent inferred'\n",
                        encoding="utf-8")
        fake.chmod(0o755)
        env = dict(os.environ, PYTHONPATH=str(_PLAYBOOK), PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
                   PLAYBOOK_SESSION_ID="pid-intent-cost")
        return subprocess.run([sys.executable, "-m", "tasks.cli", "intent", *args], cwd=self.project,
                              env=env, capture_output=True, text=True, timeout=120)

    def test_the_bare_command_exits_2_and_starts_no_judge(self):
        r = self._cli("042", "--chat-file", str(self.chat))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("this would run 2 judge call(s) on claude:opus:high", r.stderr)
        self.assertFalse((Path(self._tmp.name) / "JUDGE-STARTED").exists())
        self.assertEqual(self._runs(), [])

    def test_the_usage_names_the_flag(self):
        r = self._cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("[--yes]", r.stderr)


if __name__ == "__main__":
    unittest.main()
