"""The claude judge seats' token usage, from claude's JSON output (task 232, PHASE-B item 6).

Until task 232 every claude judge ran in plain-text mode and its spend record said `usage: unknown` (747 of 747 in
this workspace's journal). The judge call now asks for `--output-format json`; one reader in `provider/usage.py`
takes the review text out of the envelope and COPIES the counts as claude reports them. The fixtures are verbatim
captures of claude 2.1.302 (`tests/fixtures/claude-2.1.302/README.md`): a one-turn success, a three-turn run with
tools, a budget stop and a bad model, plus the plain-mode output of the same two failures. Stdlib unittest.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))

from provider import sandbox  # noqa: E402
from provider.adapters.claude import ClaudeAdapter  # noqa: E402
from provider.usage import (  # noqa: E402
    claude_judge_output, extract_claude, parse_usage, salvage_text,
)
from tasks import review  # noqa: E402
from tasks.models_check import MODEL_UNAVAILABLE, classify_failure, judge_failed  # noqa: E402
from tasks.seat_outage import classify_outage  # noqa: E402

FIX = _HERE / "fixtures" / "claude-2.1.302"


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _cp(stdout, rc=0, stderr=""):
    return subprocess.CompletedProcess(args=["claude"], returncode=rc, stdout=stdout, stderr=stderr)


SUCCESS = {"status": "known", "in": 2, "out": 4, "cache_read": 3974, "cache_write": 3847}
MULTI = {"status": "known", "in": 6, "out": 263, "cache_read": 19884, "cache_write": 4271}
BUDGET = {"status": "known", "in": 2, "out": 38, "cache_read": 3974, "cache_write": 3855}
# task 156's capture of claude's account limit, as plain mode prints it (exit 1, one stdout line)
LIMIT_LINE = "You've hit your weekly limit · resets Oct 20, 3pm (Europe/Bucharest)"


def _limit_envelope(subtype="success") -> str:
    """claude's JSON-mode shape for an account limit cannot be provoked: built like the captured bad-model
    envelope (`subtype` success, `is_error` true, the message in `result`, nothing spent)."""
    obj = json.loads(_fx("bad-model.stdout"))
    obj.update({"subtype": subtype, "result": LIMIT_LINE, "api_error_status": 429})
    return json.dumps(obj)


class ExtractClaude(unittest.TestCase):
    def test_success_copies_the_counts_as_reported(self):
        text, usage, errors = extract_claude(_fx("success.stdout"))
        self.assertEqual(text, "OK")
        self.assertEqual(usage, SUCCESS)
        self.assertEqual(errors, [])

    def test_multi_turn_takes_the_cumulative_counts_not_the_last_call(self):
        # `usage.iterations` holds the last call only (2 / 13): never the source
        text, usage, errors = extract_claude(_fx("multiturn.stdout"))
        self.assertEqual(text, "alpha-123 bravo-456")
        self.assertEqual(usage, MULTI)
        self.assertEqual(errors, [])

    def test_budget_stop_keeps_the_spend_the_top_level_usage_zeroed(self):
        text, usage, errors = extract_claude(_fx("budget-stop.stdout"))
        self.assertEqual(text, "")
        self.assertEqual(usage, BUDGET)
        self.assertEqual(errors, ["Reached maximum budget ($0.00001)"])

    def test_bad_model_is_an_error_with_nothing_spent(self):
        _text, usage, errors = extract_claude(_fx("bad-model.stdout"))
        self.assertIsNone(usage, "modelUsage {} and an all-zero usage: unknown, never 0/0")
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("There's an issue with the selected model"), errors)

    def test_several_models_take_the_top_level_usage(self):
        obj = json.loads(_fx("multiturn.stdout"))
        obj["modelUsage"]["claude-sonnet-5-5"] = dict(obj["modelUsage"]["claude-haiku-5-5"], inputTokens=99)
        _t, usage, _e = extract_claude(json.dumps(obj))
        self.assertEqual(usage, MULTI)            # the top-level usage, as reported — no sum across models

    def test_not_claudes_envelope_is_none(self):
        grok_like = '{"text": "OK", "stopReason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 2}}'
        for raw in ("", "plain prose review\n", grok_like, '{"type":"error","message":"x"}', "[1, 2]",
                    '{"type": "result"', _fx("success.stdout") + "\ntrailing"):
            self.assertIsNone(extract_claude(raw), repr(raw[:60]))

    def test_bad_counts_are_unknown(self):
        obj = json.loads(_fx("success.stdout"))
        for bad in ("13", True, -1, 1.5, None):
            o = json.loads(json.dumps(obj))
            o["modelUsage"]["claude-haiku-5-5"]["inputTokens"] = bad
            o["usage"]["input_tokens"] = bad
            self.assertIsNone(extract_claude(json.dumps(o))[1], repr(bad))

    def test_parse_usage_reads_a_claude_envelope_through_the_same_reader(self):
        # the timeout paths' `parse_usage` must not take `usage.input_tokens` alone (0 on a budget stop)
        self.assertEqual(parse_usage(_fx("success.stdout")), SUCCESS)
        self.assertEqual(parse_usage(_fx("budget-stop.stdout")), BUDGET)

    def test_salvage_never_hands_the_envelope_over(self):
        self.assertEqual(salvage_text("claude", _fx("success.stdout")), "OK")
        self.assertEqual(salvage_text("claude", _fx("budget-stop.stdout")), "")
        self.assertEqual(salvage_text("claude", "partial prose"), "partial prose")


class ClaudeJudgeOutput(unittest.TestCase):
    def test_success_is_the_prose_carrying_usage(self):
        out = claude_judge_output(_cp(_fx("success.stdout")), sandbox.format_judge_output)
        self.assertEqual(out, "OK")
        self.assertEqual(out.usage, SUCCESS)
        self.assertFalse(judge_failed(out))

    def test_budget_stop_is_the_plain_failure_shape_with_the_envelopes_words(self):
        out = claude_judge_output(_cp(_fx("budget-stop.stdout"), rc=1), sandbox.format_judge_output)
        self.assertTrue(out.startswith("(FAILED — exit 1)\n[stdout tail]\nReached maximum budget ($0.00001)"), out)
        self.assertNotIn('"modelUsage"', out, "the envelope is not the failure text")
        self.assertEqual(out.usage, BUDGET, "tokens were spent")
        self.assertEqual(review._judge_status(out), "fail")

    def test_bad_model_reads_as_today_for_the_availability_rules(self):
        out = claude_judge_output(_cp(_fx("bad-model.stdout"), rc=1, stderr=_fx("bad-model.stderr")),
                                  sandbox.format_judge_output)
        plain = sandbox.format_judge_output(_cp(_fx("plain-bad-model.stdout"), rc=1,
                                                stderr=_fx("plain-bad-model.stderr")))
        self.assertTrue(out.startswith("(FAILED — exit 1)\n[stdout tail]\nThere's an issue with the selected model"))
        self.assertEqual(classify_failure(out), MODEL_UNAVAILABLE)
        self.assertEqual(classify_failure(out), classify_failure(plain))
        self.assertIsNone(out.usage)

    def test_account_limit_is_still_recorded_as_an_outage(self):
        for rc in (1, 0):
            for subtype in ("success", "error_during_execution"):
                out = claude_judge_output(_cp(_limit_envelope(subtype), rc=rc), sandbox.format_judge_output)
                self.assertTrue(judge_failed(out), (rc, subtype, out))
                got = classify_outage(out, provider="claude")
                self.assertIsNotNone(got, (rc, subtype, out))
                self.assertTrue(got["reason"].startswith("claude: weekly limit reached"), got)

    def test_an_error_on_exit_0_says_exit_0(self):
        out = claude_judge_output(_cp(_limit_envelope(), rc=0), sandbox.format_judge_output)
        self.assertTrue(out.startswith("(FAILED — exit 0, claude reported an error)\n[stdout tail]\n"), out)

    def test_an_error_envelope_with_no_words_keeps_the_raw_envelope(self):
        obj = json.loads(_fx("budget-stop.stdout"))
        obj.pop("errors")
        obj["subtype"] = "error_never_seen"
        raw = json.dumps(obj)
        out = claude_judge_output(_cp(raw, rc=1), sandbox.format_judge_output)
        self.assertTrue(judge_failed(out))
        self.assertIn("error_never_seen", out, "the first real shape of a new kind becomes a fixture")

    def test_an_account_limit_among_several_errors_is_still_recorded(self):
        """Task 269 (PLAN.md "From task 232", opus): with several lines in `errors` the failure's stdout tail had several
        lines, and the outage rule — a claude limit line only when it is the whole tail — missed the account limit."""
        obj = json.loads(_fx("budget-stop.stdout"))
        obj.update({"errors": ["API Error: 429 rate limited", LIMIT_LINE], "result": "something went wrong"})
        out = claude_judge_output(_cp(json.dumps(obj), rc=1), sandbox.format_judge_output)
        self.assertTrue(judge_failed(out))
        got = classify_outage(out, provider="claude")
        self.assertIsNotNone(got, out)
        self.assertTrue(got["reason"].startswith("claude: weekly limit reached"), got)

    def test_several_errors_with_no_limit_line_are_no_outage(self):
        obj = json.loads(_fx("budget-stop.stdout"))
        obj.update({"errors": ["API Error: 500", "Reached maximum budget ($0.00001)"]})
        out = claude_judge_output(_cp(json.dumps(obj), rc=1), sandbox.format_judge_output)
        self.assertTrue(judge_failed(out))
        self.assertIsNone(classify_outage(out, provider="claude"))
        self.assertIn("API Error: 500", out)
        self.assertIn("Reached maximum budget", out)

    def test_a_success_stopped_short_is_a_failed_seat(self):
        """Task 269 (PLAN.md "From task 232", opus): a `subtype: success` whose `terminal_reason` is not `completed` (a
        `max_tokens` stop) was saved as a whole review; it fails the seat, as grok's non-`end_turn` stop does."""
        obj = json.loads(_fx("success.stdout"))
        obj.update({"terminal_reason": "max_tokens", "stop_reason": "max_tokens", "result": "1. **Important** — a cut"})
        out = claude_judge_output(_cp(json.dumps(obj)), sandbox.format_judge_output)
        self.assertTrue(judge_failed(out), out)
        self.assertIn("max_tokens", out)
        self.assertEqual(out.usage, SUCCESS, "tokens were spent")

    def test_a_max_tokens_stop_is_a_failed_seat_whatever_terminal_reason_says(self):
        """Task 269's impl round 1 (agy Important): the Messages API says a cut-off by `stop_reason: max_tokens`, which
        an envelope can carry with `terminal_reason: completed` or none at all."""
        for term in ("completed", None):
            with self.subTest(terminal_reason=term):
                obj = json.loads(_fx("success.stdout"))
                obj.update({"stop_reason": "max_tokens", "result": "1. **Important** — a cut"})
                if term is None:
                    obj.pop("terminal_reason")
                else:
                    obj["terminal_reason"] = term
                out = claude_judge_output(_cp(json.dumps(obj)), sandbox.format_judge_output)
                self.assertTrue(judge_failed(out), out)
                self.assertIn("max_tokens", out)

    def test_the_limit_line_in_an_error_result_or_after_a_newline_is_found(self):
        """Task 269's impl round 1 (agy, opus, sonnet Minor): the limit beside a generic error in `result`, or inside one
        `errors` entry after a newline, reached the outage rule as part of a several-line tail."""
        # (task 270: `result` is claude's words only when `errors` is empty, as `extract_claude` chooses)
        for case in ({"errors": [], "result": "API Error: 429\n" + LIMIT_LINE},
                     {"errors": ["API Error: 429\n" + LIMIT_LINE]}):
            with self.subTest(case=case):
                obj = json.loads(_fx("budget-stop.stdout"))
                obj.update(case)
                out = claude_judge_output(_cp(json.dumps(obj), rc=1), sandbox.format_judge_output)
                got = classify_outage(out, provider="claude")
                self.assertIsNotNone(got, out)

    def test_a_result_beside_errors_is_not_searched(self):
        """Task 270 (PLAN.md "From task 269" (a), the post-panel judge): beside a non-empty `errors`, `result` may be a
        partial review (a future `error_max_turns`); a line in it quoting the limit is no outage."""
        obj = json.loads(_fx("budget-stop.stdout"))
        obj.update({"subtype": "error_max_turns", "errors": ["Reached maximum number of turns (3)"],
                    "result": "1. **Important** — the panel quoted:\n```\n" + LIMIT_LINE + "\n```\n2. cut"})
        out = claude_judge_output(_cp(json.dumps(obj), rc=1), sandbox.format_judge_output)
        self.assertTrue(judge_failed(out))
        self.assertIsNone(classify_outage(out, provider="claude"), out)

    def test_a_limit_beside_a_budget_stop_is_the_outage(self):
        """Task 269's impl round 1 (opus Minor): with both lines, the account limit is what the tail keeps — a recorded
        outage stops the next panels calling a seat that cannot answer; the budget notice is not recorded."""
        obj = json.loads(_fx("budget-stop.stdout"))
        obj.update({"errors": ["Reached maximum budget ($0.00001)", LIMIT_LINE]})
        out = claude_judge_output(_cp(json.dumps(obj), rc=1), sandbox.format_judge_output)
        self.assertIsNotNone(classify_outage(out, provider="claude"), out)
        self.assertIn(LIMIT_LINE, out)

    def test_the_mirror_finds_the_limit_line_without_the_tasks_package(self):
        """Task 269's impl round 1 (agy Important, sonnet): `provider/` must not import `tasks/` — the Codex hook
        scripts run the mirror with only `scripts/lib` on the path — and a lazy import there degraded quietly."""
        lib = _HERE.parent / "plugins" / "playbook" / "scripts" / "lib"
        obj = json.loads(_fx("budget-stop.stdout"))
        obj.update({"errors": ["API Error: 429", LIMIT_LINE]})
        code = ("import json, subprocess, sys\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "from provider.usage import claude_judge_output\n"
                "from provider.sandbox import format_judge_output\n"
                "assert 'tasks' not in sys.modules\n"
                "r = subprocess.CompletedProcess(['claude'], 1, stdout=sys.argv[2], stderr='')\n"
                "print(claude_judge_output(r, format_judge_output))\n")
        r = subprocess.run([sys.executable, "-I", "-c", code, str(lib), json.dumps(obj)], capture_output=True, text=True,
                           cwd=str(lib), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        tail = r.stdout.split("[stdout tail]\n", 1)[1].splitlines()
        self.assertEqual(tail[0], LIMIT_LINE, r.stdout)
        self.assertNotIn("API Error: 429", r.stdout.split("[stderr tail]")[0])

    def test_a_cut_off_review_quoting_the_limit_line_is_no_outage(self):
        """Task 269's impl round 2 (agy, opus, sonnet Important): the limit search read the object's `result` on the
        cut-off path too, where `result` is the judge's partial review — a review quoting the line on a line of its own
        (in a code block) became a one-line tail and a false outage. "A review that quotes these messages is not an
        outage" (seat_outage.classify_outage)."""
        for field, value in (("stop_reason", "max_tokens"), ("terminal_reason", "max_turns")):
            with self.subTest(**{field: value}):
                obj = json.loads(_fx("success.stdout"))
                obj.update({field: value, "result": "1. **Important** — the panel quotes:\n```\n" + LIMIT_LINE + "\n```\n2. cut"})
                out = claude_judge_output(_cp(json.dumps(obj)), sandbox.format_judge_output)
                self.assertTrue(judge_failed(out), out)
                self.assertIsNone(classify_outage(out, provider="claude"), out)

    def test_a_terminal_reason_that_is_present_must_be_completed(self):
        """Task 269's impl round 2 (opus Minor): a present `terminal_reason` of null read as complete; grok's rule —
        a present field must hold the expected value — fails it."""
        obj = json.loads(_fx("success.stdout"))
        obj["terminal_reason"] = None
        out = claude_judge_output(_cp(json.dumps(obj)), sandbox.format_judge_output)
        self.assertTrue(judge_failed(out), out)

    def test_an_errors_limit_line_wins_over_a_different_result_line(self):
        """Task 269's impl round 2 (sonnet Minor): which line wins is pinned — `errors` first."""
        obj = json.loads(_fx("budget-stop.stdout"))
        obj.update({"errors": [LIMIT_LINE], "result": "You've hit your session limit · resets 1am (Europe/Bucharest)"})
        out = claude_judge_output(_cp(json.dumps(obj), rc=1), sandbox.format_judge_output)
        self.assertTrue(classify_outage(out, provider="claude")["reason"].startswith("claude: weekly limit"), out)

    def test_a_success_with_no_terminal_reason_reads_as_before(self):
        obj = json.loads(_fx("success.stdout"))
        obj.pop("terminal_reason")
        out = claude_judge_output(_cp(json.dumps(obj)), sandbox.format_judge_output)
        self.assertEqual(out, "OK")
        self.assertFalse(judge_failed(out))

    def test_success_without_text_is_a_failure(self):
        obj = json.loads(_fx("success.stdout"))
        obj["result"] = "   "
        out = claude_judge_output(_cp(json.dumps(obj)), sandbox.format_judge_output)
        self.assertTrue(out.startswith("(FAILED — "), out)
        self.assertEqual(out.usage, SUCCESS)

    def test_prose_stays_verbatim_and_json_garbage_fails(self):
        out = claude_judge_output(_cp("1. **Finding** — prose, the flag ignored.\n"), sandbox.format_judge_output)
        self.assertEqual(out, "1. **Finding** — prose, the flag ignored.\n")
        self.assertIsNone(getattr(out, "usage", None))
        out = claude_judge_output(_cp('{"type": "result", "cut'), sandbox.format_judge_output)
        self.assertTrue(out.startswith("(FAILED — "), out)
        self.assertEqual(claude_judge_output(_cp(""), sandbox.format_judge_output), "(no output)")


class AdapterJudge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.adapter = ClaudeAdapter(session_id="judge", project_root=Path(self._tmp.name))
        p = mock.patch("shutil.which", lambda name: f"/usr/bin/{name}")
        p.start()
        self.addCleanup(p.stop)

    def _run(self, result):
        calls = []

        def fake_run(binary, args, **kw):
            calls.append((binary, list(args)))
            return result
        with mock.patch.object(sandbox, "run", fake_run):
            out = self.adapter.run_headless_judge(prompt="Review this.", model="opus", system_context="ctx",
                                                  web_search=False, timeout_secs=30, budget_usd="1")
        return out, calls

    def test_the_judge_call_asks_for_json_and_the_subagent_argv_does_not(self):
        _out, calls = self._run(_cp(_fx("success.stdout")))
        argv = calls[0][1]
        i = argv.index("--output-format")
        self.assertEqual(argv[i:i + 2], ["--output-format", "json"])
        for inv in (self.adapter.headless_argv("p", None, context="c"),
                    self.adapter.headless_argv("p", None, context="c", stream=True)):
            self.assertNotIn("json", inv.argv, inv.argv)

    def test_success_carries_usage_into_the_spend_parser(self):
        out, _ = self._run(_cp(_fx("multiturn.stdout")))
        self.assertEqual(out, "alpha-123 bravo-456")
        self.assertEqual(review._parse_judge_usage(out), MULTI)

    def test_budget_stop_fails_the_seat_with_its_spend(self):
        out, _ = self._run(_cp(_fx("budget-stop.stdout"), rc=1))
        self.assertTrue(judge_failed(out))
        self.assertEqual(review._parse_judge_usage(out), BUDGET)


class JournalKeepsTheCacheCounts(unittest.TestCase):
    def test_append_review_writes_the_copied_counts(self):
        with tempfile.TemporaryDirectory() as d:
            agent = Path(d) / ".agent"
            agent.mkdir()
            pbj = review._load_pb_journal()
            pbj.append_review(agent, seat="claude:opus:high", task="1", round_no=1, kind="panel",
                              duration_ms=5, status="ok", usage=MULTI)
            pbj.append_review(agent, seat="codex:x", task="1", round_no=1, kind="panel",
                              duration_ms=5, status="ok", usage={"status": "known", "in": 1, "out": 2})
            pbj.append_review(agent, seat="claude:opus:high", task="1", round_no=1, kind="panel",
                              duration_ms=5, status="ok", usage=dict(MULTI, cache_read="9", cache_write=-1))
            recs = [json.loads(ln) for ln in (agent / "journal" / "enforcement.jsonl").read_text().splitlines()]
        self.assertEqual(recs[0]["usage"], MULTI)
        self.assertEqual(recs[1]["usage"], {"status": "known", "in": 1, "out": 2})
        self.assertEqual(recs[2]["usage"], {"status": "known", "in": 6, "out": 263},
                         "a bad cache count is dropped, never copied")


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.agent = self.project / ".agent"
        tdir = self.agent / "tasks" / "042-demo"
        tdir.mkdir(parents=True)
        (tdir / "task.md").write_text("# 042 - demo\n## Status\npending\n## Intent\nx\n"
                                      "## Work Plan\n- [ ] a gate\n", encoding="utf-8")
        (self.agent / "models.json").write_text(json.dumps({"panel": ["claude:opus", "claude:sonnet"],
                                                            "default_judge": "claude:opus"}), encoding="utf-8")
        review._PB_JOURNAL_MOD = None
        review._PB_JOURNAL_LOADED = False

    def _journal(self):
        p = self.agent / "journal" / "enforcement.jsonl"
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


@contextlib.contextmanager
def _chdir(p):
    old = os.getcwd()
    os.chdir(p)
    try:
        yield
    finally:
        os.chdir(old)


class SinglePathReadsTheEnvelope(_Project):
    """`tasks plan-review --backend claude` builds its own argv (review.py) — the path tail certification and the
    post-D6 judge share."""

    def _single(self, stdout, rc=0, stderr=""):
        calls = []

        def _run(agent, args, **kw):
            calls.append(list(args))
            return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr, args=list(args))
        buf = io.StringIO()
        with mock.patch.object(sandbox, "run", _run), mock.patch.object(shutil, "which", lambda n: "/usr/bin/" + n):
            with _chdir(self.project), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                with contextlib.suppress(SystemExit):
                    review.cmd_single_review("plan-review", ["042", "--backend", "claude", "--model", "opus"])
        return calls, [r for r in self._journal() if r.get("hook") == "review"], buf.getvalue()

    def test_single_records_usage_and_saves_the_prose(self):
        calls, recs, printed = self._single(_fx("multiturn.stdout"))
        argv = calls[0]
        i = argv.index("--output-format")
        self.assertEqual(argv[i:i + 2], ["--output-format", "json"])
        self.assertEqual(len(recs), 1, recs)
        self.assertEqual(recs[0]["usage"], MULTI)
        self.assertEqual(recs[0]["status"], "ok")
        log = self.agent / "tasks" / "042-demo" / "judge.log"
        body = log.read_text(encoding="utf-8")
        self.assertIn("alpha-123 bravo-456", body)
        self.assertNotIn('"modelUsage"', body)
        self.assertNotIn('"modelUsage"', printed)

    def test_single_budget_stop_records_fail_with_spend_and_saves_nothing(self):
        _calls, recs, _printed = self._single(_fx("budget-stop.stdout"), rc=1)
        self.assertEqual(recs[0]["status"], "fail")
        self.assertEqual(recs[0]["usage"], BUDGET)
        self.assertEqual(recs[0]["error"], "exit 1: Reached maximum budget ($0.00001)")
        self.assertFalse((self.agent / "tasks" / "042-demo" / "judge.log").exists())

    def test_single_budget_stop_still_says_how_to_raise_the_cap(self):
        """Impl round 1 (grok, Important): plain mode's single path read claude's raw `Error: Exceeded USD budget` and
        told the operator to raise the cap; under JSON mode the words are `Reached maximum budget` inside the shaped
        failure text — the notice must still come, for both shapes."""
        for stdout in (_fx("budget-stop.stdout"), _fx("plain-budget-stop.stdout")):
            _calls, _recs, printed = self._single(stdout, rc=1)
            self.assertIn("budget cap and produced no review", printed, stdout[:40])
            self.assertIn("Raise judge_budget_usd", printed)


class BudgetExceeded(unittest.TestCase):
    """`models_check.budget_exceeded` reads claude's budget line at the start of the text (claude's older exit-0 form)
    or as the FIRST line of a failure block's stdout tail — plain mode's and JSON mode's words."""

    def test_the_shapes(self):
        from tasks.models_check import budget_exceeded
        shaped = claude_judge_output(_cp(_fx("budget-stop.stdout"), rc=1), sandbox.format_judge_output)
        plain = sandbox.format_judge_output(_cp(_fx("plain-budget-stop.stdout"), rc=1))
        self.assertTrue(budget_exceeded(shaped))
        self.assertTrue(budget_exceeded(plain))
        self.assertTrue(budget_exceeded("Error: Exceeded USD budget (1)"))

    def test_a_quote_is_not_a_budget_stop(self):
        from tasks.models_check import budget_exceeded
        self.assertFalse(budget_exceeded("1. **Important** — claude printed `Reached maximum budget ($1)` here."))
        self.assertFalse(budget_exceeded("(FAILED — exit 1)\n[stdout tail]\nA review line.\nReached maximum budget ($1)"))
        self.assertFalse(budget_exceeded("(FAILED — exit 1)\n[stderr tail]\nReached maximum budget ($1)"))


class PanelSeatRecordsUsage(_Project):
    def test_a_claude_panel_seat_journals_its_counts(self):
        def _run(binary, args, **kw):
            return _cp(_fx("multiturn.stdout"))
        with mock.patch.object(ClaudeAdapter, "is_available", classmethod(lambda cls: True)), \
                mock.patch.object(sandbox, "run", _run), \
                mock.patch("shutil.which", lambda n: "/usr/bin/" + n):
            with _chdir(self.project), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with contextlib.suppress(SystemExit):
                    review.cmd_panel_review(["042", "--models", "claude:opus,claude:sonnet"])
        recs = [r for r in self._journal() if r.get("hook") == "review"]
        self.assertEqual(len(recs), 2, recs)
        for r in recs:
            self.assertEqual(r["usage"], MULTI, r)
        jm = (self.agent / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8")
        self.assertIn("alpha-123 bravo-456", jm)
        self.assertNotIn('"modelUsage"', jm)


if __name__ == "__main__":
    unittest.main()
