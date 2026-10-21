#!/usr/bin/env python3
"""A judge seat out of credit is recognised, recorded and skipped (PLAN S12b, task 149).

Task 146 found that a seat whose provider said the account is out of credit —
grok's `402 Payment Required … usage balance exhausted`, codex's `You've hit your
usage limit … try again at 7:33 PM` — failed every later panel the same way, and
the panel shrank silently. The texts below are the providers' own, captured live
(task 146's plan panel, 2026-10-07; task 111's agy exam).

Run: python3 -m unittest tests.test_seat_outage
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))

from tasks import seat_outage as so  # noqa: E402

NOW = dt.datetime(2026, 10, 7, 18, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))

GROK = ('(FAILED — exit 1)\n[stdout tail]\n{"type":"error","message":"Internal error: {\\n  '
        '\\"message\\": \\"API error (status 402 Payment Required): Grok Build usage balance '
        'exhausted\\",\\n  \\"http_status\\": 402\\n}"}')
CODEX = ('(FAILED — exit 1)\n[stdout tail]\n{"type":"error","message":"You’ve hit your usage '
         'limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/'
         'codex/settings/usage to purchase more credits or try again at 7:33 PM."}')
CODEX_DATE = ('(FAILED — exit 1)\n{"type":"error","message":"You\'ve hit your usage limit. Upgrade to '
              'Pro or try again at Oct 10th, 2026 12:11 AM."}')
AGY = "(FAILED — agy quota exhausted: error: Individual quota reached. Resets in 34m13s.)"
# claude's account limit, byte for byte as both claude seats of a panel returned it on
# 2026-10-08 (task 156's first impl panel, judge.md): one plain line on stdout, exit 1.
CLAUDE_LIMIT = ("(FAILED — exit 1)\n[stdout tail]\n"
                "You've hit your weekly limit · resets Oct 13, 6pm (Europe/Bucharest)\n\n")
# codex's workspace out of credits, byte for byte as both codex seats of task 177's impl round 1 returned it
# (2026-10-10 20:30, its judge.md): no reset time is given (task 203, #34).
CODEX_CREDITS = ('(FAILED — exit 1)\n[stdout tail]\n'
                 '{"type":"error","message":"Your workspace is out of credits. Ask your workspace owner to refill in '
                 'order to continue."}\n'
                 '{"type":"turn.failed","error":{"message":"Your workspace is out of credits. Ask your workspace owner '
                 'to refill in order to continue."}}')
CREDITS_SENTENCE = "Your workspace is out of credits. Ask your workspace owner to refill in order to continue."


def _pin_clock(test, now=NOW):
    """seat_outage reads the wall clock through its `_dt` alias; pin it for a test
    that drives the real panel. A clock-time fixture ("try again at 7:33 PM") must
    mean the same thing at whatever hour the suite runs: on the real clock the
    panel tests failed every day from 19:33 to 01:33 local time, the six hours in
    which 7:33 PM reads as the reset that has just happened (task 158)."""
    import types
    from unittest import mock

    class _Pinned(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    shim = types.SimpleNamespace(datetime=_Pinned, timedelta=dt.timedelta, timezone=dt.timezone)
    patcher = mock.patch.object(so, "_dt", shim)
    patcher.start()
    test.addCleanup(patcher.stop)


class ClassifyOutage(unittest.TestCase):
    def test_provider_texts_are_outages_with_their_reset_time(self):
        self.assertEqual(so.classify_outage(GROK, NOW),
                         {"reason": "grok: usage balance exhausted (402)", "until": None})
        self.assertEqual(so.classify_outage(CODEX, NOW)["until"], "2026-10-07T19:33+03:00")
        self.assertEqual(so.classify_outage(CODEX_DATE, NOW)["until"], "2026-10-10T00:11+03:00")
        self.assertEqual(so.classify_outage(AGY, NOW)["until"], "2026-10-07T18:34:13+03:00")

    def test_a_codex_workspace_out_of_credits_is_an_outage_with_no_end(self):     # task 203, #34
        for provider in ("codex", None):
            with self.subTest(provider=provider):
                self.assertEqual(so.classify_outage(CODEX_CREDITS, NOW, provider=provider),
                                 {"reason": "codex: workspace out of credits", "until": None})

    def test_the_credits_sentence_quoted_is_no_outage(self):              # task 203, #34
        self.assertIsNone(so.classify_outage(CODEX_CREDITS.replace("(FAILED — exit 1)", "A review:"), NOW))
        self.assertIsNone(so.classify_outage(CODEX_CREDITS, NOW, provider="claude"))

    def test_a_clock_time_long_past_today_means_tomorrow(self):
        # passed more than six hours ago → the next such time (a time passed only
        # minutes ago is the reset that already happened — ClassifyOutageProvenance)
        late = NOW.replace(hour=20)
        morning = CODEX.replace("7:33 PM", "9:05 AM")
        self.assertEqual(so.classify_outage(morning, late)["until"], "2026-10-08T09:05+03:00")

    def test_a_review_that_quotes_the_messages_is_not_an_outage(self):
        # Negative control: only a FAILED seat is read — a judge that quotes
        # these texts (this repository's records do) is not out of credit.
        for text in (GROK, CODEX, AGY):
            with self.subTest(text=text[:30]):
                quoted = "**Important** the panel saw: " + text
                self.assertIsNone(so.classify_outage(quoted, NOW))
        self.assertIsNone(so.classify_outage("(FAILED — exit 1)\nTraceback: boom", NOW))

    def test_a_failed_seat_whose_output_quotes_the_messages_is_not_an_outage(self):
        # impl panel r1 (opus): a failed block carries the seat's stdout tail; a
        # judge that grepped this repository before failing for another reason
        # quotes these messages there — only its own terminal error is read.
        tail = ("(FAILED — exit 1)\n[stdout tail]\n"
                '.agent/tasks/146/judge.md:216:{"type":"error","message":"You’ve hit your usage limit … try again at 7:33 PM."}\n'
                "tests/test_seat_outage.py:33:    '402 Payment Required): Grok Build usage balance exhausted'\n"
                '{"type":"error","message":"stream disconnected before completion"}\n')
        self.assertIsNone(so.classify_outage(tail, NOW))


class ClaudeAccountLimit(unittest.TestCase):
    """Task 158: a claude seat at its account limit is an outage like the others. A
    claude seat's stdout is the judge's REVIEW, so the limit line counts only when it
    is the whole of it — the CLI printed nothing else before exiting 1."""

    def test_the_captured_limit_is_an_outage_until_its_reset(self):
        self.assertEqual(so.classify_outage(CLAUDE_LIMIT, NOW, provider="claude"),
                         {"reason": "claude: weekly limit reached (resets Oct 13, 6pm (Europe/Bucharest))",
                          "until": "2026-10-13T18:00+03:00"})

    def test_a_review_that_carries_the_line_is_not_an_outage(self):
        line = "You've hit your weekly limit · resets Oct 13, 6pm (Europe/Bucharest)"
        for name, text in {
            "quoted in a failed review": "(FAILED — exit 1)\n[stdout tail]\n**Important** — the panel printed:\n" + line + "\n",
            "after a cut-off tail line": "(FAILED — exit 1)\n[stdout tail]\nrest of a sentence.\n" + line + "\n",
            "inside a longer line": "(FAILED — exit 1)\n[stdout tail]\nthe seat said: " + line + "\n",
            "a review that did not fail": "**Important** — " + line + "\n",
        }.items():
            with self.subTest(case=name):
                self.assertIsNone(so.classify_outage(text, NOW, provider="claude"))

    def test_the_line_is_claudes_only_for_a_claude_seat(self):
        # another provider's stdout is its JSON event stream: a plain line there is not its voice
        for provider in ("codex", "grok", "agy", None):
            with self.subTest(provider=provider):
                self.assertIsNone(so.classify_outage(CLAUDE_LIMIT, NOW, provider=provider))

    def test_a_bare_clock_time_is_the_next_such_time_in_the_named_zone(self):
        # not captured live — the same line without a date, as a shorter limit would print it
        text = CLAUDE_LIMIT.replace("Oct 13, 6pm", "9:30pm")
        self.assertEqual(so.classify_outage(text, NOW, provider="claude")["until"], "2026-10-07T21:30+03:00")
        # NOW is 18:00 +03:00 = 15:00 UTC: "4pm (UTC)" is one hour ahead, whatever zone the host is in
        try:
            import zoneinfo
            zoneinfo.ZoneInfo("UTC")
        except Exception:
            self.skipTest("no IANA zone database on this host (a named zone then reads as local time)")
        utc = CLAUDE_LIMIT.replace("Oct 13, 6pm (Europe/Bucharest)", "4pm (UTC)")
        self.assertEqual(so.classify_outage(utc, NOW, provider="claude")["until"], "2026-10-07T16:00+00:00")

    def test_a_reset_it_cannot_read_is_still_an_outage_with_no_end(self):
        # like a codex limit with no "try again at": skipped until the owner clears it
        for when in ("soon", "Oct 13, 6pm (Not/AZone) maybe", "Smarch 13, 6pm"):
            with self.subTest(when=when):
                text = CLAUDE_LIMIT.replace("Oct 13, 6pm (Europe/Bucharest)", when)
                got = so.classify_outage(text, NOW, provider="claude")
                self.assertIsNotNone(got)
                self.assertIsNone(got["until"])

    def test_an_unknown_zone_is_read_as_local_time(self):
        text = CLAUDE_LIMIT.replace("Europe/Bucharest", "Mars/Olympus")
        self.assertEqual(so.classify_outage(text, NOW, provider="claude")["until"], "2026-10-13T18:00+03:00")

    def test_a_date_just_behind_us_across_new_year_is_last_years(self):
        # impl panel r1 (codex-medium): on Jan 1, "resets Dec 31, 11:55pm" passed ten
        # minutes ago — it is not next December's. The year is the one that puts the
        # date NEAREST to now, in either direction.
        new_year = dt.datetime(2027, 1, 1, 0, 5, tzinfo=NOW.tzinfo)
        text = CLAUDE_LIMIT.replace("Oct 13, 6pm (Europe/Bucharest)", "Dec 31, 11:55pm")
        got = so.classify_outage(text, new_year, provider="claude")
        self.assertEqual(got["until"], "2026-12-31T23:55+03:00")
        self.assertFalse(so._is_current(got, new_year), "a reset that already happened still skips the seat")

    def test_a_date_already_behind_us_by_months_is_next_years(self):
        late = dt.datetime(2026, 12, 30, 10, 0, tzinfo=NOW.tzinfo)
        text = CLAUDE_LIMIT.replace("Oct 13, 6pm (Europe/Bucharest)", "Jan 2, 9am")
        self.assertEqual(so.classify_outage(text, late, provider="claude")["until"], "2027-01-02T09:00+03:00")


class ClassifyOutageProvenance(unittest.TestCase):
    """Impl panel r2 (task 149): stdout counts only as the CLI's error events,
    the stderr tail is the provider's and is read whole."""

    def test_a_quoted_error_line_in_stdout_with_another_stderr_cause_is_not_an_outage(self):
        text = ("(FAILED — exit 1)\n[stdout tail]\nError: Grok Build usage balance exhausted (402 Payment Required)\n"
                "[stderr tail]\nError: stream disconnected: connection reset by peer\n")
        self.assertIsNone(so.classify_outage(text, NOW))

    def test_an_error_event_is_decoded_not_matched_byte_for_byte(self):
        # post-D6 run 2 (codex): spacing and key order may differ
        text = ('(FAILED — exit 1)\n[stdout tail]\n{"message": "You’ve hit your usage limit. '
                'Try again at 7:33 PM.", "type": "error"}\n')
        self.assertEqual(so.classify_outage(text, NOW, provider="codex")["until"], "2026-10-07T19:33+03:00")

    def test_a_multi_line_stderr_error_is_read_whole(self):
        text = ("(FAILED — exit 1)\n[stderr tail]\nError: Internal error: {\n"
                '  "message": "API error (status 402 Payment Required): Grok Build usage balance exhausted",\n'
                '  "http_status": 402\n}\n')
        self.assertEqual(so.classify_outage(text, NOW)["reason"], "grok: usage balance exhausted (402)")

    def test_the_sentence_in_a_codex_event_that_is_no_error_is_no_outage(self):     # task 203 r1 (fable)
        # a codex seat that failed for another reason: the sentence is only in the review's own message event
        text = ("(FAILED — exit 1)\n[stdout tail]\n"
                + json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": CREDITS_SENTENCE}})
                + "\n" + json.dumps({"type": "error", "message": "stream disconnected"}) + "\n")
        self.assertIsNone(so.classify_outage(text, NOW, provider="codex"))

    def test_a_late_evening_time_seen_after_midnight_is_yesterdays(self):
        # post-D6 run 1 (codex): "try again at 11:55 PM" classified at 00:10 is the
        # reset that passed 15 minutes ago, not tonight's
        after_midnight = NOW.replace(day=8, hour=0, minute=10)
        out = so.classify_outage(CODEX.replace("7:33 PM", "11:55 PM"), after_midnight)
        self.assertEqual(out["until"], "2026-10-07T23:55+03:00")

    def test_a_clock_time_passed_minutes_ago_is_already_expired(self):
        # the panel classifies at its end: a seat that failed at 19:20 with
        # "try again at 7:33 PM", classified at 19:40, is free again — not tomorrow
        late = NOW.replace(hour=19, minute=40)
        out = so.classify_outage(CODEX, late)
        self.assertEqual(out["until"], "2026-10-07T19:33+03:00")
        so_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(so_dir, True))
        so.record_outage(so_dir, "codex:x", out, late)
        self.assertEqual(so.current_outages(so_dir, late), {})


class ClassifyOutageThroughTheCodexResult(unittest.TestCase):
    """Task 203 r1 (codex): an exit-0 codex stream that ends badly becomes a failure text in
    `usage.judge_output_from_result`; what of it a review wrote — the excerpt in the malformed-output
    header, the partial text after an error — is never read as the provider speaking. The texts are
    built by the real function, so its wording is what the classifier sees."""

    def _text(self, stdout):
        from provider import usage
        from provider.sandbox import format_judge_output
        result = subprocess.CompletedProcess(["codex"], 0, stdout=stdout, stderr="")
        return str(usage.judge_output_from_result(result, usage.extract_codex, format_judge_output))

    def _message(self, text):
        return json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}})

    def test_a_quote_inside_the_malformed_output_header_is_no_outage(self):
        text = self._text(self._message("A review that quotes: " + CREDITS_SENTENCE) + "\na stray line\n")
        self.assertTrue(text.startswith("(FAILED — malformed"), text[:80])
        self.assertIn("out of credits", text.splitlines()[0])
        self.assertIsNone(so.classify_outage(text, NOW, provider="codex"))

    def test_a_quote_in_the_partial_review_text_is_no_outage(self):
        quoted = json.dumps({"type": "error", "message": CREDITS_SENTENCE})
        text = self._text(self._message("A review that quotes the event:\n" + quoted) + "\n"
                          + json.dumps({"type": "error", "message": "stream disconnected"}) + "\n")
        self.assertIn("\n[partial text before the failure]\n", text)
        self.assertIn("\n" + quoted, text)
        self.assertIsNone(so.classify_outage(text, NOW, provider="codex"))

    def test_the_credits_error_event_on_exit_0_is_still_an_outage(self):
        text = self._text(json.dumps({"type": "error", "message": CREDITS_SENTENCE}) + "\n")
        self.assertEqual(so.classify_outage(text, NOW, provider="codex"),
                         {"reason": "codex: workspace out of credits", "until": None})


class OutageRecord(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.agent = Path(self._tmp.name)

    def test_record_current_expiry_and_clear(self):
        so.record_outage(self.agent, "grok:grok-4.7:medium", so.classify_outage(GROK, NOW), NOW)
        so.record_outage(self.agent, "codex:gpt-6-sol:high", so.classify_outage(CODEX, NOW), NOW)
        self.assertEqual(sorted(so.current_outages(self.agent, NOW)),
                         ["codex:gpt-6-sol:high", "grok:grok-4.7:medium"])
        # codex's reset time passes; grok (no time) stays until cleared
        after = NOW + dt.timedelta(hours=2)
        self.assertEqual(sorted(so.current_outages(self.agent, after)), ["grok:grok-4.7:medium"])
        self.assertTrue(so.clear_outage(self.agent, "grok:grok-4.7:medium"))
        self.assertFalse(so.clear_outage(self.agent, "grok:grok-4.7:medium"))
        self.assertEqual(so.current_outages(self.agent, after), {})

    def test_the_record_is_a_file_of_the_lanes_journal(self):
        # PB-PANEL-SEAT-OUTAGE says WHERE the record is — `<lane>/journal/seat-outages.json`
        # — and that it is machine-local: the journal directory is in init's gitignore
        # block (tests/test_init_claude_md.py, MergeGitignore). Every other test here
        # reaches the file through the module's own `_record()`, so the place itself was
        # asserted nowhere (task 170's binding audit; task 172).
        so.record_outage(self.agent, "grok:grok-4.7:medium", so.classify_outage(GROK, NOW), NOW)
        written = sorted(p.relative_to(self.agent).as_posix() for p in self.agent.rglob("*") if p.is_file())
        self.assertIn("journal/seat-outages.json", written)
        # and nothing of it lands outside that directory (the lock file is in it too)
        self.assertEqual([p for p in written if not p.startswith("journal/")], [])

    def test_an_unreadable_record_reads_as_no_outage(self):
        so._record(self.agent).parent.mkdir(parents=True, exist_ok=True)
        so._record(self.agent).write_text("{not json", encoding="utf-8")
        self.assertEqual(so.current_outages(self.agent, NOW), {})

    def test_two_processes_recording_at_once_keep_both_entries(self):
        # The record is a read-modify-write under the shared task lock: two
        # panels finishing together must not lose one seat's entry.
        code = ("import sys, datetime as dt; sys.path.insert(0, sys.argv[1]);"
                "from tasks import seat_outage as so;"
                "now = dt.datetime.now().astimezone();"
                "[so.record_outage(sys.argv[2], f'{sys.argv[3]}:{i}', {'reason': 'r', 'until': None}, now)"
                " for i in range(25)]")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(PLUGIN), str(self.agent), who])
                 for who in ("codex", "grok")]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=120), 0)
        data = json.loads(so._record(self.agent).read_text(encoding="utf-8"))
        self.assertEqual(len(data), 50, sorted(data))

    # PLAN S11 item 3 (task 165; task 149 impl panel r1, codex-high). Two panels start; the
    # seat fails in the first and is recorded; the owner tops the account up and runs
    # `tasks models enable`; the second panel, which had started before that, then reports
    # the same old failure — and the seat was recorded out of credit again.
    OUT = {"reason": "grok: usage balance exhausted", "until": None}
    MIN = dt.timedelta(minutes=1)

    def test_a_failure_from_a_panel_older_than_the_owners_clear_is_not_recorded(self):
        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        self.assertTrue(so.clear_outage(self.agent, "grok:x", NOW + 2 * self.MIN))
        self.assertFalse(so.record_outage(self.agent, "grok:x", self.OUT, NOW + 3 * self.MIN,
                                          started=NOW + self.MIN))
        self.assertEqual(so.current_outages(self.agent, NOW + 3 * self.MIN), {})
        # a panel that started AFTER the clear is news
        self.assertTrue(so.record_outage(self.agent, "grok:x", self.OUT, NOW + 9 * self.MIN,
                                         started=NOW + 5 * self.MIN))
        self.assertEqual(list(so.current_outages(self.agent, NOW + 9 * self.MIN)), ["grok:x"])

    def test_a_clear_with_nothing_recorded_binds_the_older_panels_too(self):
        # the owner's statement is "this seat works as of now" whether or not a panel
        # has got as far as recording its failure
        self.assertFalse(so.clear_outage(self.agent, "grok:x", NOW))           # nothing was there
        self.assertFalse(so.record_outage(self.agent, "grok:x", self.OUT, NOW + self.MIN,
                                          started=NOW - self.MIN))
        self.assertEqual(so.current_outages(self.agent, NOW + self.MIN), {})

    def test_a_clear_is_about_one_seat(self):
        so.clear_outage(self.agent, "grok:x", NOW + 2 * self.MIN)
        self.assertTrue(so.record_outage(self.agent, "codex:y", self.OUT, NOW + 3 * self.MIN,
                                         started=NOW + self.MIN))
        self.assertEqual(list(so.current_outages(self.agent, NOW + 3 * self.MIN)), ["codex:y"])

    def test_a_caller_that_gives_no_start_time_records_as_before(self):
        so.clear_outage(self.agent, "grok:x", NOW)
        self.assertTrue(so.record_outage(self.agent, "grok:x", self.OUT, NOW + self.MIN))
        self.assertEqual(list(so.current_outages(self.agent, NOW + self.MIN)), ["grok:x"])

    def test_the_time_of_a_clear_is_never_listed_as_a_seat(self):
        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        so.clear_outage(self.agent, "grok:x", NOW + self.MIN)
        so.record_outage(self.agent, "codex:y", self.OUT, NOW + 2 * self.MIN)
        self.assertEqual(list(so.current_outages(self.agent, NOW + 2 * self.MIN)), ["codex:y"])
        data = json.loads(so._record(self.agent).read_text(encoding="utf-8"))
        extra = {k: v for k, v in data.items() if k != "codex:y"}
        self.assertEqual(len(extra), 1, data)
        # the reader shipped in 1.6.0 keeps OBJECT values only (`_load`): what holds the
        # clear times must not be one, or an old reader lists it as a seat out of credit
        self.assertNotIsInstance(next(iter(extra.values())), dict)
        # …and the later write kept it: the older panel's failure is still refused
        self.assertFalse(so.record_outage(self.agent, "grok:x", self.OUT, NOW + 3 * self.MIN, started=NOW))

    def test_a_record_file_written_by_1_6_0_still_reads(self):
        so._record(self.agent).parent.mkdir(parents=True, exist_ok=True)
        so._record(self.agent).write_text(json.dumps(
            {"grok:x": {"reason": "grok: usage balance exhausted", "until": None,
                        "since": "2026-10-07T18:00+03:00"}}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assertEqual(list(so.current_outages(self.agent, NOW)), ["grok:x"])
        self.assertTrue(so.clear_outage(self.agent, "grok:x", NOW))
        self.assertEqual(so.current_outages(self.agent, NOW), {})

    def test_a_clear_is_kept_however_long_a_panel_runs(self):
        # impl panel round 1 (codex ×2): the first version dropped a clear after a day,
        # while a review's timeout may be unlimited — a panel that outlived the day
        # put the seat back
        so.clear_outage(self.agent, "grok:x", NOW)
        so.record_outage(self.agent, "codex:y", self.OUT, NOW + dt.timedelta(days=2))   # any later write
        self.assertFalse(so.record_outage(self.agent, "grok:x", self.OUT, NOW + dt.timedelta(days=3),
                                          started=NOW - self.MIN))
        self.assertEqual(list(so.current_outages(self.agent, NOW + dt.timedelta(days=3))), ["codex:y"])

    def test_an_older_clear_never_replaces_a_newer_one(self):
        # impl panel round 1 (codex ×2): a clear takes its time before it takes the lock,
        # so two `tasks models enable` can commit out of order
        so.clear_outage(self.agent, "grok:x", NOW + 2 * self.MIN)
        so.clear_outage(self.agent, "grok:x", NOW + self.MIN)               # the earlier one lands last
        self.assertFalse(so.record_outage(self.agent, "grok:x", self.OUT, NOW + 3 * self.MIN,
                                          started=NOW + 1.5 * self.MIN))


class PanelSkipsOutagesAndHoldsTheQuorum(unittest.TestCase):
    """A real `cmd_panel_review` (judges faked at the adapter, the tamper guard
    stubbed clean), as tests/test_tamper_scope.PanelStampEndToEnd drives it."""

    CLAUDE = ("claude:claude-opus-5-5", "claude:claude-sonnet-5", "claude:claude-haiku-4-5",
              "claude:claude-fable-5")

    def setUp(self):
        from tasks import review as R
        self.R = R
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = self.d = Path(self._tmp.name) / "proj"
        d.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "x@y.z"), ("config", "user.name", "x")):
            subprocess.run(["git", *args], cwd=d, check=True)
        tdir = d / ".agent" / "tasks" / "042-demo"
        tdir.mkdir(parents=True)
        (tdir / "task.md").write_text("# 042 - demo\n## Status\npending\n## Intent\nx\n"
                                      "## Work Plan\n- [ ] a gate\n", encoding="utf-8")
        (d / "MIND_MAP.md").write_text("# Mind Map\n[1] node\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=d, check=True)
        self.calls = []
        R._PB_JOURNAL_MOD = None
        R._PB_JOURNAL_LOADED = False
        _pin_clock(self)
        # the project lives under /tmp, which every sandbox leaves writable: the probe would never run here (task
        # 262's impl round 3) — its exposure check reads a place that covers none of it, unless a test says otherwise
        self._writable = [Path(self._tmp.name) / "rw"]
        from unittest import mock
        roots = mock.patch("provider.sandbox.broad_writable_roots", lambda home=None: list(self._writable))
        roots.start()
        self.addCleanup(roots.stop)

    def _panel(self, models, judge, codex_available=False, real_tamper_guard=False):
        import contextlib
        import io
        from unittest import mock
        from provider.adapters.claude import ClaudeAdapter
        from provider.adapters.codex import CodexAdapter
        out, err, code = io.StringIO(), io.StringIO(), None
        old = os.getcwd()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(ClaudeAdapter, "is_available", classmethod(lambda cls: True)))
            stack.enter_context(mock.patch.object(CodexAdapter, "is_available",
                                                  classmethod(lambda cls: codex_available)))
            stack.enter_context(mock.patch.object(ClaudeAdapter, "run_headless_judge", judge))
            stack.enter_context(mock.patch.object(CodexAdapter, "run_headless_judge", judge))
            if not real_tamper_guard:
                stack.enter_context(mock.patch.object(
                    self.R, "_detect_tamper_full",
                    lambda pp, t, b: {"mutations": [], "cautions": [], "degraded": False}))
            os.chdir(self.d)
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.R.cmd_panel_review(["042", "--mode", "impl"]
                                            + (["--models", ",".join(models)] if models is not None else []))
            except SystemExit as e:
                code = e.code
            finally:
                os.chdir(old)
        return code, out.getvalue(), err.getvalue()

    def _judge(self, fail=(), outage=(), outage_text=CODEX):
        calls = self.calls

        def judge(adapter, prompt, model, system_context, **kw):
            calls.append(model)
            if any(m in model for m in outage):
                return outage_text
            if any(m in model for m in fail):
                return "(FAILED — exit 1)\nTraceback: something else broke"
            return "1. **Note** — fine.\n"
        return judge

    def _judge_md(self):
        return (self.d / ".agent" / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8")

    def test_an_out_of_credit_seat_is_recorded_then_skipped(self):
        # a codex seat: its stdout is codex's JSON event stream, where the
        # usage-limit event is codex speaking
        models = self.CLAUDE[:2] + ("codex:gpt-5.5",)
        code, out, _ = self._panel(models, self._judge(outage=("gpt-5.5",)), codex_available=True)
        self.assertIn("Recorded codex:gpt-5.5 as out of credit", out)
        record = json.loads(so._record(self.d / ".agent").read_text(encoding="utf-8"))
        self.assertEqual(list(record), ["codex:gpt-5.5"])
        # task 262: a recorded seat whose outage has a reset time is asked first. While it still answers out of
        # credit it stays out, and only the probe is spent
        self.calls.clear()
        code, out, _ = self._panel(models, self._judge(outage=("gpt-5.5",)), codex_available=True)
        self.assertEqual(self.calls.count("gpt-5.5"), 1, "only the probe may call a seat still out of credit")
        self.assertIn("Probed codex:gpt-5.5: the probe answered out of credit again.", out)
        self.assertIn("Skipped out of credit: codex:gpt-5.5", out)
        self.assertIn("codex:gpt-5.5", json.loads(so._record(self.d / ".agent").read_text(encoding="utf-8")))
        # a third impl round on one task is past the D6 cap, which refuses before any probe (task 262); this test
        # is about the probe, so the earlier rounds are taken off the record
        judge_md = self.d / ".agent" / "tasks" / "042-demo" / "judge.md"
        self.calls.clear()
        code, out, err = self._panel(models, self._judge(), codex_available=True)
        self.assertIn("D6 round cap", err)
        self.assertNotIn("gpt-5.5", self.calls, "a probe was spent on a panel the round cap refuses")
        judge_md.unlink()
        # … and once it answers (the account switched, the limit reset early), it is cleared and seated
        self.calls.clear()
        code, out, _ = self._panel(models, self._judge(), codex_available=True)
        self.assertEqual(self.calls.count("gpt-5.5"), 2, "the probe, then the seat's review")
        self.assertIn("Seated codex:gpt-5.5", out)
        self.assertEqual(so.current_outages(self.d / ".agent", NOW + dt.timedelta(minutes=5)), {})

    def test_a_codex_workspace_out_of_credits_is_recorded_then_skipped(self):     # task 203, #34
        models = self.CLAUDE[:2] + ("codex:gpt-5.5",)
        code, out, _ = self._panel(models, self._judge(outage=("gpt-5.5",), outage_text=CODEX_CREDITS),
                                   codex_available=True)
        self.assertIn("Recorded codex:gpt-5.5 as out of credit", out)
        record = json.loads(so._record(self.d / ".agent").read_text(encoding="utf-8"))
        self.assertEqual(record["codex:gpt-5.5"]["until"], None)
        self.calls.clear()
        code, out, _ = self._panel(models, self._judge(), codex_available=True)
        self.assertNotIn("gpt-5.5", self.calls, "a seat out of credit was called again")

    def test_a_seat_the_owner_enabled_while_the_panel_ran_is_not_recorded_again(self):
        # PLAN S11 item 3 (task 165): the panel that started before the owner's
        # `tasks models enable` reports the seat's old failure after it
        models = self.CLAUDE[:2] + ("codex:gpt-5.5",)
        agent = self.d / ".agent"
        inner = self._judge(outage=("gpt-5.5",))

        def judge(adapter, prompt, model, system_context, **kw):
            if "gpt-5.5" in model:          # the owner's enable lands while this panel runs
                so.clear_outage(agent, "codex:gpt-5.5", NOW + dt.timedelta(minutes=2))
            return inner(adapter, prompt, model, system_context, **kw)

        code, out, _ = self._panel(models, judge, codex_available=True)
        self.assertEqual(so.current_outages(agent, NOW + dt.timedelta(minutes=5)), {}, out)
        self.assertNotIn("Recorded codex:gpt-5.5 as out of credit", out)
        self.assertIn("codex:gpt-5.5 answered out of credit", out)       # it says what it saw …
        self.assertIn("not recorded", out)                                # … and what it did not do
        # the next panel calls the seat again
        self.calls.clear()
        self._panel(models, self._judge(), codex_available=True)
        self.assertIn("gpt-5.5", self.calls)

    def test_a_seat_enabled_before_its_call_started_is_recorded_when_it_fails(self):
        # impl panel round 1 (codex-high): the first version took ONE time for the whole
        # panel, before it chose its seats. An enable that lands after that moment but
        # before the seat is called is older than the call: the failure is NEW and must
        # be recorded. The clock steps one second per reading, so the order is exact.
        import itertools
        import types
        from unittest import mock
        ticks = itertools.count()

        class _Stepping(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                at = NOW + dt.timedelta(seconds=next(ticks))
                return at if tz is None else at.astimezone(tz)

        shim = types.SimpleNamespace(datetime=_Stepping, timedelta=dt.timedelta, timezone=dt.timezone)
        patcher = mock.patch.object(so, "_dt", shim)
        patcher.start()
        self.addCleanup(patcher.stop)
        models = self.CLAUDE[:2] + ("codex:gpt-5.5",)
        agent = self.d / ".agent"
        real_current = so.current_outages

        def choose_then_enable(agent_dir, now=None):
            seen = real_current(agent_dir, now)
            so.clear_outage(agent_dir, "codex:gpt-5.5")      # after the panel chose, before any call
            return seen

        with mock.patch.object(so, "current_outages", choose_then_enable):
            code, out, _ = self._panel(models, self._judge(outage=("gpt-5.5",)), codex_available=True)
        self.assertIn("Recorded codex:gpt-5.5 as out of credit", out)
        self.assertEqual(list(real_current(agent, NOW + dt.timedelta(minutes=5))), ["codex:gpt-5.5"])

    def test_a_claude_seat_at_its_account_limit_is_recorded_then_skipped(self):
        # Task 158: on 2026-10-08 both claude seats of a five-seat panel answered with
        # the limit line; the panel fell below quorum and the next run called them again.
        code, out, _ = self._panel(self.CLAUDE, self._judge(outage=("fable",), outage_text=CLAUDE_LIMIT))
        self.assertIn("Recorded claude:claude-fable-5 as out of credit", out)
        record = json.loads(so._record(self.d / ".agent").read_text(encoding="utf-8"))
        self.assertEqual(list(record), ["claude:claude-fable-5"])
        self.assertEqual(record["claude:claude-fable-5"]["until"], "2026-10-13T18:00+03:00")
        self.calls.clear()
        # task 262: still at its limit, the probe says so and the seat stays out
        code, out, _ = self._panel(self.CLAUDE, self._judge(outage=("fable",), outage_text=CLAUDE_LIMIT))
        self.assertEqual(self.calls.count("claude-fable-5"), 1, "only the probe may call a seat at its limit")
        self.assertIn("Probed claude:claude-fable-5: the probe answered out of credit again.", out)
        self.assertIn("Skipped out of credit: claude:claude-fable-5", out)
        # impl panel r1 (opus): this outage lasts days, and the owner may switch accounts
        # the same hour — the way to clear it must be on the line, not only in the docs.
        skipped = next(ln for ln in out.splitlines() if "Skipped out of credit: claude:claude-fable-5" in ln)
        self.assertIn("until 2026-10-13T18:00+03:00", skipped)
        self.assertIn("`tasks models enable claude:claude-fable-5`", skipped)

    def test_the_quorum_refusal_says_how_to_clear_a_seat_that_works_again(self):
        # Two of four seats at the account limit: 2 live < quorum 3, the panel refuses
        # before spending — and names the command that clears a recorded seat.
        code, out, _ = self._panel(self.CLAUDE, self._judge(outage=("fable", "haiku"), outage_text=CLAUDE_LIMIT))
        self.calls.clear()
        # task 262: both are asked first and still answer at their limit, so the panel refuses — the two probes
        # are all it spent
        code, out, err = self._panel(self.CLAUDE, self._judge(outage=("fable", "haiku"), outage_text=CLAUDE_LIMIT))
        self.assertEqual(code, 1)
        self.assertEqual(sorted(self.calls), ["claude-fable-5", "claude-haiku-4-5"],
                         "a review was called although the panel could not reach its quorum")
        self.assertIn("nothing was spent but the probes", err)
        self.assertIn("tasks models enable <seat>", err)

    def test_a_probe_that_fails_another_way_keeps_the_seat_out(self):
        # task 262: only a real answer clears a record; a failed probe says what it saw
        code, out, _ = self._panel(self.CLAUDE, self._judge(outage=("fable",), outage_text=CLAUDE_LIMIT))
        self.calls.clear()
        code, out, _ = self._panel(self.CLAUDE, self._judge(fail=("fable",)))
        self.assertEqual(self.calls.count("claude-fable-5"), 1)
        self.assertIn("Skipped out of credit: claude:claude-fable-5", out)
        self.assertIn("probe", next(ln for ln in out.splitlines() if "claude:claude-fable-5" in ln and "probe" in ln))
        self.assertIn("claude:claude-fable-5", json.loads(so._record(self.d / ".agent").read_text(encoding="utf-8")))

    def test_an_outage_with_no_reset_time_is_not_probed(self):
        # task 262: grok's 402 and a codex workspace out of credits wait for a top-up and the owner's enable
        so.record_outage(self.d / ".agent", "claude:claude-fable-5", {"reason": "r", "until": None})
        code, out, _ = self._panel(self.CLAUDE, self._judge())
        self.assertNotIn("claude-fable-5", self.calls)
        self.assertIn("Skipped out of credit: claude:claude-fable-5", out)

    # task 262's impl round 1

    UNTIL = (NOW + dt.timedelta(days=2)).isoformat(timespec="minutes")

    def _record(self, *seats, until=UNTIL, reason="weekly limit"):
        for s in seats:
            so.record_outage(self.d / ".agent", s, {"reason": reason, "until": until})

    def _spend(self, kind="probe"):
        p = self.d / ".agent" / "journal" / "enforcement.jsonl"
        recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return [r for r in recs if r.get("hook") == "review" and r.get("kind") == kind]

    def test_a_probe_that_raises_keeps_the_seat_out_and_the_panel_runs(self):
        """agy's Critical, sol: `subprocess` was not imported where the probe caught its timeout, so any exception
        from the seat's adapter raised NameError and stopped the whole panel."""
        for exc, said in ((subprocess.TimeoutExpired(["claude"], 120), "the probe timed out"),
                          (OSError("no such file"), "the probe could not run (OSError)")):
            with self.subTest(exc=type(exc).__name__):
                self._record("claude:claude-fable-5")
                self.calls.clear()
                inner = self._judge()

                def judge(adapter, prompt, model, system_context, _exc=exc, **kw):
                    if "fable" in model:
                        self.calls.append(model)
                        raise _exc
                    return inner(adapter, prompt, model, system_context, **kw)
                code, out, err = self._panel(self.CLAUDE, judge)
                self.assertIn(f"Probed claude:claude-fable-5: {said}.", out, err)
                self.assertIn("Skipped out of credit: claude:claude-fable-5", out)
                self.assertEqual(self.calls.count("claude-fable-5"), 1)
                self.assertIn("claude-opus-5-5", self.calls, "the panel did not go on to its live seats")
                (self.d / ".agent" / "tasks" / "042-demo" / "judge.md").unlink(missing_ok=True)

    def test_the_probe_reads_nothing_of_the_project(self):
        """Opus (Important) and sol: the probe ran in the project — a claude seat read its CLAUDE.md and memory under
        a $0.5 budget, and a contained seat's writable project was the owner's. It runs in a throwaway folder now."""
        self._record("claude:claude-fable-5")
        roots = []
        inner = self._judge()

        def judge(adapter, prompt, model, system_context, **kw):
            if "fable" in model and "availability check" in prompt:
                roots.append(Path(adapter.project_root))
            return inner(adapter, prompt, model, system_context, **kw)
        self._panel(self.CLAUDE, judge)
        self.assertEqual(len(roots), 1)
        self.assertNotEqual(roots[0].resolve(), self.d.resolve())
        self.assertNotIn(self.d.resolve(), roots[0].resolve().parents)
        self.assertFalse(roots[0].exists(), "the probe's folder was left behind")

    def test_a_probe_that_writes_the_project_voids_the_panel_before_any_review(self):
        """Sol (Important): the probe ran before the panel's tamper snapshot, so without an OS sandbox a probe's write
        became the snapshot's baseline. The tree is now snapshotted before the probes and checked after each one."""
        self._record("claude:claude-fable-5")
        inner = self._judge()

        def judge(adapter, prompt, model, system_context, **kw):
            if "fable" in model and "availability check" in prompt:
                (self.d / "MIND_MAP.md").write_text("# Mind Map\n[1] changed by a probe\n", encoding="utf-8")
            return inner(adapter, prompt, model, system_context, **kw)
        code, out, err = self._panel(self.CLAUDE, judge, real_tamper_guard=True)
        self.assertEqual(code, 1, out + err)
        self.assertIn("TAMPER DETECTED", err)
        self.assertEqual(self.calls, ["claude-fable-5"], "a review was called after a probe changed the project")
        self.assertIn("claude:claude-fable-5", so.current_outages(self.d / ".agent"),
                      "a probe that changed the project cleared its seat's record")
        self.assertEqual(self._spend(), [], "a tamper stop records no spend")

    def test_a_newer_outage_recorded_while_the_probe_ran_stands(self):
        """Astra and sol (Important): the probe's clear removed whatever entry was there when it answered —
        a newer outage another panel recorded meanwhile too."""
        self._record("claude:claude-fable-5")
        inner = self._judge()

        def judge(adapter, prompt, model, system_context, **kw):
            if "fable" in model and "availability check" in prompt:
                self._record("claude:claude-fable-5", reason="newer limit")     # another panel, meanwhile
            return inner(adapter, prompt, model, system_context, **kw)
        code, out, _ = self._panel(self.CLAUDE, judge)
        self.assertEqual(so.current_outages(self.d / ".agent")["claude:claude-fable-5"]["reason"], "newer limit")
        self.assertIn("Skipped out of credit: claude:claude-fable-5", out)
        self.assertEqual(self.calls.count("claude-fable-5"), 1, "the seat was seated over a newer outage")

    def test_a_failure_of_a_call_begun_during_the_probe_is_still_recorded(self):
        """Astra and sol (Important): the clear took its time when the probe had answered, so a call begun while the
        probe ran, failing out of credit, was taken for news from before the clear. The clear's time is the probe's
        start now. The clock steps one second per reading, so the order is exact."""
        import itertools
        import types
        from unittest import mock
        ticks = itertools.count()

        class _Stepping(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                at = NOW + dt.timedelta(seconds=next(ticks))
                return at if tz is None else at.astimezone(tz)
        patcher = mock.patch.object(so, "_dt", types.SimpleNamespace(datetime=_Stepping, timedelta=dt.timedelta,
                                                                       timezone=dt.timezone))
        patcher.start()
        self.addCleanup(patcher.stop)
        self._record("claude:claude-fable-5")
        began = []
        inner = self._judge()

        def judge(adapter, prompt, model, system_context, **kw):
            if "fable" in model and "availability check" in prompt:
                began.append(so.now())          # another panel's call of this seat begins while the probe runs
            return inner(adapter, prompt, model, system_context, **kw)
        self._panel(self.CLAUDE, judge)
        self.assertTrue(so.record_outage(self.d / ".agent", "claude:claude-fable-5",
                                         {"reason": "r", "until": self.UNTIL}, started=began[0]),
                        "a failure of a call begun while the probe ran was dropped as older than the clear")

    def test_each_probe_is_a_spend_record(self):
        """Sol (Important), opus and astra: a probe is a paid call, and it left no spend record."""
        self._record("claude:claude-fable-5", "claude:claude-haiku-4-5")
        code, out, _ = self._panel(self.CLAUDE, self._judge(outage=("haiku",), outage_text=CLAUDE_LIMIT))
        got = sorted((r["seat"], r["status"]) for r in self._spend())
        self.assertEqual(got, [("claude:claude-fable-5:high", "ok"), ("claude:claude-haiku-4-5:high", "fail")])
        self.assertEqual({r["task"] for r in self._spend()}, {"042"})

    def test_a_seat_that_answers_its_probe_is_cleared_and_seated(self):
        """Sonnet (Minor): the claude seat's own clear-and-seat path."""
        self._record("claude:claude-fable-5")
        code, out, _ = self._panel(self.CLAUDE, self._judge())
        self.assertIn("Seated claude:claude-fable-5", out)
        self.assertEqual(self.calls.count("claude-fable-5"), 2, "the probe, then the seat's review")
        self.assertEqual(so.current_outages(self.d / ".agent"), {})

    def test_no_probe_when_the_project_lies_where_every_sandbox_leaves_writing_open(self):
        """Task 262's impl round 3 (sol, Important): the probe runs in a throwaway folder, so the sandbox binds and
        guards that folder, not the project — a project under /tmp (or an agent's home state) was writable to it, its
        `.git/hooks` included, and the tamper check reads HEAD and porcelain only. Such a project is not probed."""
        self._writable = [self.d.parent]
        self._record("claude:claude-fable-5")
        code, out, _ = self._panel(self.CLAUDE, self._judge())
        self.assertEqual(self.calls.count("claude-fable-5"), 0, out)
        self.assertIn("Skipped out of credit: claude:claude-fable-5", out)
        said = next((ln for ln in out.splitlines() if "Not probed" in ln), "")
        self.assertIn(str(self.d.resolve()), said)
        self.assertIn(str(self.d.parent.resolve()), said)
        self.assertIn("claude:claude-fable-5", so.current_outages(self.d / ".agent"))
        self.assertEqual(self._spend(), [])

    def test_no_probe_when_a_git_source_lies_where_every_sandbox_leaves_writing_open(self):
        """Task 262's impl round 3 (sol): the project's git sources — here `core.hooksPath` — are guarded by a judge's
        sandbox, not by the probe's; one under a writable place could plant a hook the host runs at its next commit."""
        rw = Path(self._tmp.name) / "rw"
        (rw / "hooks").mkdir(parents=True)
        subprocess.run(["git", "config", "core.hooksPath", str(rw / "hooks")], cwd=self.d, check=True)
        self._record("claude:claude-fable-5")
        code, out, _ = self._panel(self.CLAUDE, self._judge())
        self.assertEqual(self.calls.count("claude-fable-5"), 0, out)
        self.assertIn(str((rw / "hooks").resolve()), next((ln for ln in out.splitlines() if "Not probed" in ln), ""))
        self.assertIn("claude:claude-fable-5", so.current_outages(self.d / ".agent"))

    def test_no_probe_when_a_git_source_has_a_hard_link_elsewhere(self):
        """Task 262's post-panel judge run 1 (sol, Important): `.git/hooks/pre-commit` lies outside every writable root,
        but a hard link under one (`~/.cache` in the finding) is the same file — writing that name plants the hook. A
        judge's sandbox refuses such a source (`build_bwrap_argv`); the probe is not run there."""
        hook = self.d / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        alias = Path(self._tmp.name) / "rw" / "alias"
        alias.parent.mkdir(parents=True)
        os.link(hook, alias)
        self._record("claude:claude-fable-5")
        code, out, _ = self._panel(self.CLAUDE, self._judge())
        self.assertEqual(self.calls.count("claude-fable-5"), 0, out)
        said = next((ln for ln in out.splitlines() if "Not probed" in ln), "")
        self.assertIn(str(hook.resolve()), said)
        self.assertIn("2 hard links", said)
        self.assertIn("claude:claude-fable-5", so.current_outages(self.d / ".agent"))

    def test_no_probe_when_git_cannot_say_where_its_sources_are(self):
        """Task 262's impl round 3 (sol): what git could not answer cannot be shown to lie outside — not probed."""
        from unittest import mock
        self._record("claude:claude-fable-5")
        with mock.patch("provider.sandbox._git_sources", lambda p: ([], [], [], [str(self.d.resolve())])):
            code, out, _ = self._panel(self.CLAUDE, self._judge())
        self.assertEqual(self.calls.count("claude-fable-5"), 0, out)
        self.assertIn("Not probed", out)

    def test_no_probe_when_the_containment_check_raises(self):
        """Task 262's impl round 3: a check that cannot run is not a pass — the seat is not probed."""
        from unittest import mock

        def boom(p):
            raise OSError("no git")
        self._record("claude:claude-fable-5")
        with mock.patch("provider.sandbox._git_paths_to_protect", boom):
            code, out, _ = self._panel(self.CLAUDE, self._judge())
        self.assertEqual(self.calls.count("claude-fable-5"), 0, out)
        self.assertIn("containment could not be checked (OSError)", out)

    def test_no_probe_when_the_round_history_cannot_be_read(self):
        """Opus (Minor), astra: a cap peek that raised probed as usual, and the locked check then refused with
        "Nothing was spent"."""
        from unittest import mock
        from tasks import stop_rule
        self._record("claude:claude-fable-5")

        def unreadable(*a, **k):
            raise stop_rule.RoundsUnreadable("judge.md is unreadable")
        with mock.patch.object(stop_rule, "impl_rounds", unreadable):
            code, out, err = self._panel(self.CLAUDE, self._judge())
        self.assertEqual(code, 2, out + err)
        self.assertEqual(self.calls, [])
        self.assertIn("Nothing was spent", err)

    def test_no_probe_when_the_quorum_cannot_be_reached_even_if_every_probe_answers(self):
        """Opus (Minor): the probes ran although the panel would refuse whatever they answered."""
        self._record("claude:claude-fable-5")
        self._record("claude:claude-haiku-4-5", "claude:claude-sonnet-5", until=None)
        code, out, err = self._panel(self.CLAUDE, self._judge())
        self.assertEqual(code, 1, out + err)
        self.assertEqual(self.calls, [])
        self.assertIn("nothing was spent.", err)

    def test_a_refused_reservation_after_the_probes_says_they_ran(self):
        """Astra and sol (Important): a reservation refused after the probes (another review of the task under way)
        said nothing of them."""
        from unittest import mock
        from tasks import post_d6
        self._record("claude:claude-fable-5")
        with mock.patch.object(post_d6, "reserve_panel", lambda *a, **k: (False, None)), \
                mock.patch.object(post_d6, "panel_refusal", lambda tf: "Refused: another review of this task runs."):
            code, out, err = self._panel(self.CLAUDE, self._judge())
        self.assertEqual(code, 2, out + err)
        self.assertEqual(self.calls, ["claude-fable-5"])
        self.assertIn("1 probe of a recorded seat ran before this refusal", err)

    def test_a_claude_seat_quoting_the_event_is_not_recorded(self):
        # post-D6 run 1 (codex): a claude seat's stdout is the review text; a line
        # there that looks like codex's error event is a quote
        code, out, _ = self._panel(self.CLAUDE[:3], self._judge(outage=("haiku",)))
        self.assertNotIn("Recorded", out)
        self.assertFalse(so._record(self.d / ".agent").exists())
        self.assertIn("PANEL VERDICT: PASS", self._judge_md())

    def test_a_claude_seat_quoting_the_credits_event_is_not_recorded(self):     # task 203 r1 (gemini)
        code, out, _ = self._panel(self.CLAUDE[:3], self._judge(outage=("haiku",), outage_text=CODEX_CREDITS))
        self.assertNotIn("Recorded", out)
        self.assertFalse(so._record(self.d / ".agent").exists())
        self.assertIn("PANEL VERDICT: PASS", self._judge_md())

    def test_too_few_live_seats_fail_before_anything_is_spent(self):
        # Three of six requested seats cannot run (codex missing): the default
        # majority quorum of the REQUESTED six is four, so the panel stops
        # before any judge is called — resolved against the three that could
        # launch it would have been two (task 147 impl panel r1).
        models = self.CLAUDE[:3] + ("codex:gpt-5.5", "codex:gpt-5.4", "codex:gpt-5.3-codex")
        code, out, err = self._panel(models, self._judge())
        self.assertEqual(code, 1, out + err)
        self.assertIn("only 3 of the 6 requested seats can run, and the quorum is 4", err)
        self.assertEqual(self.calls, [], "judges were spent on a panel that cannot reach quorum")

    def test_the_verdict_counts_the_requested_panel(self):
        # Four of six seats launch (two codex missing): quorum = majority of six
        # = 4. One launched seat fails → 3/4 succeeded → FAIL. Against the four
        # that launched, majority would have been 3 → PASS.
        models = self.CLAUDE + ("codex:gpt-5.5", "codex:gpt-5.4")
        self._panel(models, self._judge(fail=("fable",)))
        judge_md = self._judge_md()
        self.assertIn("PANEL VERDICT: FAIL", judge_md)
        self.assertIn("3/4 judges succeeded, quorum 4", judge_md)

    def test_no_live_seat_at_all_is_reported_as_the_quorum_failure(self):
        # r2 agy: every requested seat out of credit used to fall through to
        # "no available judges — install a provider CLI".
        for model in self.CLAUDE[:2]:
            so.record_outage(self.d / ".agent", model, {"reason": "r", "until": None})
        code, out, err = self._panel(self.CLAUDE[:2], self._judge())
        self.assertEqual(code, 1)
        self.assertIn("only 0 of the 2 requested seats can run", err)
        self.assertEqual(self.calls, [])

    def test_the_configured_panel_is_counted_like_a_models_selection(self):
        # The row names two sources of the REQUESTED seats — the configured panel, or the
        # --models selection — and every test above passes --models (task 170's binding
        # audit; task 172). The same six seats, three of them unable to run, this time
        # as the panel `.agent/models.json` configures: the same refusal, nothing spent.
        # Read by the real loader from the project's `.agent/models.json` (impl panel r1,
        # opus and codex-high: the first version handed the panel in through a mocked
        # loader, so it proved nothing about where the configured seats come from).
        seats = list(self.CLAUDE[:3]) + ["codex:gpt-5.5", "codex:gpt-5.4", "codex:gpt-5.3-codex"]
        (self.d / ".agent" / "models.json").write_text(json.dumps({"panel": seats}), encoding="utf-8")
        code, out, err = self._panel(None, self._judge())
        self.assertEqual(code, 1, out + err)
        self.assertIn("only 3 of the 6 requested seats can run, and the quorum is 4", err)
        self.assertEqual(self.calls, [], "judges were spent on a panel that cannot reach quorum")

    def test_enough_live_seats_still_pass(self):
        # Negative control: the same six-seat request, every launched seat
        # answering, passes — the requested-panel quorum does not fail a panel
        # that reaches it.
        models = self.CLAUDE + ("codex:gpt-5.5", "codex:gpt-5.4")
        self._panel(models, self._judge())
        self.assertIn("4/4 judges succeeded, quorum 4", self._judge_md())


class ModelsEnableClearsAnOutage(unittest.TestCase):
    def test_enable_clears_the_seat_and_says_so(self):
        import contextlib
        import io
        from tasks.models_check import cli_models
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / ".agent" / "tasks").mkdir(parents=True)
            so.record_outage(proj / ".agent", "grok:grok-4.7:medium", so.classify_outage(GROK, NOW), NOW)
            buf = io.StringIO()
            old = os.getcwd()
            os.chdir(proj)
            try:
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(cli_models(["enable", "grok:grok-4.7:medium"], proj), 0)
                    self.assertEqual(cli_models(["enable", "grok:grok-4.7:medium"], proj), 0)
                    self.assertEqual(cli_models(["enable"], proj), 2)
            finally:
                os.chdir(old)
            self.assertEqual(so.current_outages(proj / ".agent"), {})
            self.assertIn("enabled", buf.getvalue())
            self.assertIn("no outage recorded", buf.getvalue())

    def test_enable_with_nothing_recorded_still_binds_a_panel_that_is_running(self):
        # PLAN S11 item 3 (task 165): the command's second answer used to end "nothing changed"
        import contextlib
        import io
        from tasks.models_check import cli_models
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / ".agent" / "tasks").mkdir(parents=True)
            began = dt.datetime.now().astimezone() - dt.timedelta(minutes=1)
            buf = io.StringIO()
            old = os.getcwd()
            os.chdir(proj)
            try:
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(cli_models(["enable", "grok:grok-4.7:medium"], proj), 0)
            finally:
                os.chdir(old)
            self.assertIn("no outage recorded", buf.getvalue())
            self.assertNotIn("nothing changed", buf.getvalue())
            self.assertFalse(so.record_outage(proj / ".agent", "grok:grok-4.7:medium",
                                              {"reason": "r", "until": None}, started=began))
            self.assertEqual(so.current_outages(proj / ".agent"), {})


if __name__ == "__main__":
    unittest.main()
