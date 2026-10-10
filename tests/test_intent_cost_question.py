"""`tasks intent` does not spend judge calls unasked (gauntlet flag C4, task 073; owner's
decision D3-C4 "(fix)", 2026-09-24; PLAN S11; task 173).

`tasks intent <N>` starts up to four blind extractions — four calls to the default
judge, each as long as the review timeout allows. It started them as soon as it had
printed which layers have evidence: nothing said what it was about to spend and nothing
could stop it, short of knowing `--collect-only` beforehand.

Now the bare command prints what it would spend — how many calls, on which seat, the
time limit of each — spends nothing, writes nothing and exits 2; the same command with
the `--yes …` it printed runs. (The CLI is run by an agent, not at a
terminal: it asks the way it asks elsewhere, by refusing with the flag to pass.
`commands/intent.md` tells the agent to put the number to the user first.)

The approval is the QUOTE, not a bare yes (impl panel r1, codex-high): the evidence is
collected again when the command is re-run, and it can have grown in between — the
user's own "yes" in the chat can be what makes a task's chat layer available — so a
bare `--yes` given for one call could start two. And it is the WHOLE quote (round 2,
codex-high and codex-medium): the line also states a time limit and a budget cap, and
an approval of calls and seat alone would have run after either was raised. The flag
is `--yes <calls>@<seat>@<limit>@<cap>` — the four figures themselves, so that two
different lines cannot share an approval (post-D6 run 1: an eight-digit digest of
them did, the judge found two time limits with one id); if a run would by then be
quoted differently in any of the four, the new line is printed and nothing is spent.
And what was approved is what runs: the judge and the cap that were checked are
handed to the runner, which no longer resolves them a second time.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
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

    def _quote(self, *args):
        """The id the bare command prints for these arguments (and that it spent nothing)."""
        code, out, err = self._intent(*args)
        self.assertEqual(code, 2, err)
        m = re.search(r"Re-run with `--yes (.+?)` to spend exactly that", err)
        self.assertIsNotNone(m, err)
        (token,) = shlex.split(m.group(1))        # as a shell would hand it over
        return token

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
        self.assertIn("Re-run with `--yes 2@claude:opus:high@1200s@10` to spend exactly that", err)
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
        code, out, err = self._intent("--yes", self._quote())
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self._runs()), 1)
        self.assertEqual([r["seat"] for r in self._spend_records()], ["claude:opus:high"] * 2)
        self.assertNotIn("this would run", err)

    def test_the_flag_may_come_before_the_other_options(self):
        quote = self._quote()
        out, err = io.StringIO(), io.StringIO()
        with _chdir(self.project), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            history.cmd_intent(["42", "--yes", quote, "--chat-file", str(self.chat)])
        self.assertEqual(len(self.calls), 2)


class TheApprovalIsTheQuote(_Project):
    """`--yes` carries the id of the line that was approved and runs only if that line
    is still what a run would be quoted as — calls, seat, time limit, budget cap."""

    def _refused(self, *args, env=None):
        with mock.patch.dict(os.environ, env or {}):
            code, out, err = self._intent(*args)
        self.assertEqual(code, 2, err)
        self.assertEqual(self.calls, [], "a judge was called on an approval that does not match")
        self.assertEqual(self._runs(), [])
        self.assertEqual(self._spend_records(), [])
        self.assertRegex(err, r"Re-run with `--yes .+` to spend exactly that")
        return err

    def test_the_same_quote_runs_when_nothing_changed(self):
        code, out, err = self._intent("--yes", self._quote())
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.calls), 2)

    def test_evidence_that_grew_since_the_quote_is_asked_about_again(self):
        # round 1's scenario: quoted with one layer available, re-run after a second
        # one became available (here the chat file appears in between)
        text = self.chat.read_text(encoding="utf-8")
        self.chat.unlink()
        code, out, err = self._intent()
        self.assertIn("this would run 1 judge call(s) on claude:opus:high", err)
        quote = self._quote()
        self.chat.write_text(text, encoding="utf-8")
        err = self._refused("--yes", quote)
        self.assertIn("this would run 2 judge call(s)", err)
        self.assertIn(f"`--yes {quote}` does not approve what a run would spend now", err)

    def test_a_longer_time_limit_than_the_one_quoted_is_asked_about_again(self):
        # round 2 (codex-high, codex-medium): the line quotes the limit too
        quote = self._quote("--timeout", "60")
        err = self._refused("--yes", quote, "--timeout", "6000")
        self.assertIn("time limit 6000s each", err)
        code, out, err = self._intent("--yes", quote, "--timeout", "60")     # as quoted: runs
        self.assertEqual(code, 0, err)
        self.assertEqual({c["timeout_secs"] for c in self.calls}, {60})

    def test_a_raised_budget_cap_is_asked_about_again(self):
        with mock.patch.dict(os.environ, {"PLAYBOOK_JUDGE_BUDGET_USD": "2"}):
            quote = self._quote()
        err = self._refused("--yes", quote, env={"PLAYBOOK_JUDGE_BUDGET_USD": "50"})
        self.assertIn("each capped at $50", err)
        with mock.patch.dict(os.environ, {"PLAYBOOK_JUDGE_BUDGET_USD": "2"}):
            code, out, err = self._intent("--yes", quote)
        self.assertEqual(code, 0, err)
        self.assertEqual({c["budget_usd"] for c in self.calls}, {"2"})

    def test_another_default_judge_than_the_one_quoted_is_asked_about_again(self):
        quote = self._quote()
        (self.agent / "models.json").write_text(
            json.dumps({"panel": ["claude:opus", "claude:sonnet"], "default_judge": "claude:sonnet"}),
            encoding="utf-8")
        err = self._refused("--yes", quote)
        self.assertIn("judge call(s) on claude:sonnet:high", err)

    def test_a_bare_yes_approves_nothing(self):
        err = self._refused("--yes")
        self.assertIn("`--yes` needs what it approves", err)

    def test_a_yes_followed_by_another_option_approves_nothing(self):
        # `--yes --timeout 77`: the option after it is not taken for the id
        err = self._refused("--yes", "--timeout", "77")
        self.assertIn("time limit 77s each", err)

    def test_an_approval_that_was_never_printed_approves_nothing(self):
        for made_up in ("yes", "2", "00000000", "2@claude:opus:high", "2@claude:opus:high@1200s",
                        "2@claude:opus:high@1200s@10@", " 2@claude:opus:high@1200s@10"):
            with self.subTest(made_up):
                self._refused("--yes", made_up)

    def test_two_different_lines_never_share_an_approval(self):
        # post-D6 run 1: with an eight-digit digest, time limits of 23069s and 30214s
        # (two calls on claude:opus:high, cap $2) had the same id, so the approval of
        # one ran the other. The approval is the figures themselves now.
        with mock.patch.dict(os.environ, {"PLAYBOOK_JUDGE_BUDGET_USD": "2"}):
            first = self._quote("--timeout", "23069")
            second = self._quote("--timeout", "30214")
            self.assertNotEqual(first, second)
            self.assertEqual(first, "2@claude:opus:high@23069s@2")
            err = self._refused("--yes", first, "--timeout", "30214")
        self.assertIn("time limit 30214s each", err)

    def test_the_approval_is_one_to_one_with_the_four_figures(self):
        # even a seat that holds the separator: the last two fields have a fixed shape
        from tasks.history import intent_approval
        figures = [(calls, seat, limit, cap)
                   for calls in (1, 2, 12)
                   for seat in ("claude:opus:high", "codex:gpt-6-sol:high", "x@1200s", "x@1200s@5", "x")
                   for limit in ("1200s", "5s", "unlimited")
                   for cap in ("", "5", "10", "2.5")]
        tokens = {intent_approval(*f) for f in figures}
        self.assertEqual(len(tokens), len(figures))


class WhatWasApprovedIsWhatRuns(_Project):
    """Post-D6 run 1. The command checked the approval against a seat and a cap it had
    resolved — and the runner then resolved both again, on its own: a change of the
    configuration between the two reads ran another judge, or another cap, under the
    approval. The runner is handed what was checked."""

    def test_a_judge_that_changes_after_the_check_is_not_the_one_that_runs(self):
        from tasks import intent as intent_mod
        quote = self._quote()
        real = intent_mod.resolve_default_seat
        answers = iter([real(self.project)])           # the command's one read

        def changed_since(project_path):
            return next(answers, ("claude", "sonnet", "claude:sonnet:high"))   # any later read
        with mock.patch.object(intent_mod, "resolve_default_seat", changed_since):
            code, out, err = self._intent("--yes", quote)
        self.assertEqual(code, 0, err)
        self.assertEqual({c["model"] for c in self.calls}, {"opus"})
        self.assertEqual({r["seat"] for r in self._spend_records()}, {"claude:opus:high"})

    def test_a_cap_that_changes_after_the_check_is_not_the_one_that_runs(self):
        from tasks import core
        quote = self._quote()
        self.assertTrue(quote.endswith("@10"), quote)
        real = core.resolve_judge_budget
        answers = iter([real(self.project)])

        def raised_since(project_path, cli_value=None):
            return next(answers, "500")
        with mock.patch.object(core, "resolve_judge_budget", raised_since):
            code, out, err = self._intent("--yes", quote)
        self.assertEqual(code, 0, err)
        self.assertEqual({c["budget_usd"] for c in self.calls}, {"10"})


class TheSeatInTheLineIsTheSeatThatRuns(_Project):
    DEFAULT_JUDGE = "claude:sonnet"

    def test_another_default_judge(self):
        code, out, err = self._intent()
        self.assertIn("judge call(s) on claude:sonnet:high", err)
        self._intent("--yes", self._quote())
        self.assertEqual({r["seat"] for r in self._spend_records()}, {"claude:sonnet:high"})
        self.assertEqual({c["model"] for c in self.calls}, {"sonnet"})


class TheFlagIsSafeToPasteWhateverTheSeat(_Project):
    # the owner's opus seat is `claude:claude-opus-5-5[1m]` — `[1m]` is a glob to a
    # shell (zsh refuses the command outright when nothing matches)
    DEFAULT_JUDGE = "claude:claude-opus-5-5[1m]"

    def test_the_flag_is_printed_quoted_for_a_shell_and_runs(self):
        code, out, err = self._intent()
        self.assertEqual(code, 2)
        self.assertIn("Re-run with `--yes '2@claude:claude-opus-5-5[1m]:high@1200s@10'` to spend exactly that", err)
        code, out, err = self._intent("--yes", self._quote())
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.calls), 2)


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
        self.assertIn("[--yes <calls>@<seat>@<limit>@<cap>]", r.stderr)


if __name__ == "__main__":
    unittest.main()
