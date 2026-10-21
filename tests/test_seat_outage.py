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


class RetrySeatsRecord(unittest.TestCase):
    """Task 276 (the owner, 2026-10-21): a seat recorded with NO reset time is tried again by
    every panel instead of waiting for `tasks models enable`. The record keeps the entry while
    the seat still refuses, counts the tries, takes a reset time when a refusal names one, and
    drops the entry when the seat answers — only the entry the answered call was made under."""

    OUT = {"reason": "grok: usage balance exhausted (402)", "until": None}
    SEC = dt.timedelta(seconds=1)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.agent = Path(self._tmp.name)

    def _data(self):
        return json.loads(so._record(self.agent).read_text(encoding="utf-8"))

    def test_the_seats_with_no_reset_time_are_the_retry_seats(self):
        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        so.record_outage(self.agent, "codex:y", so.classify_outage(CODEX, NOW), NOW)
        skip, retry = so.split_outages(so.current_outages(self.agent, NOW))
        self.assertEqual(list(skip), ["codex:y"])
        self.assertEqual(list(retry), ["grok:x"])

    def test_since_is_written_to_the_second(self):
        # the answered-call clear compares `since` with a call's start: minutes made an
        # entry recorded at 18:00:50 read as older than a call started at 18:00:20
        so.record_outage(self.agent, "grok:x", self.OUT, NOW + 50 * self.SEC)
        self.assertEqual(self._data()["grok:x"]["since"], "2026-10-07T18:00:50+03:00")

    def test_a_refused_retry_keeps_the_entry_and_counts_the_try(self):
        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        first = NOW + dt.timedelta(hours=1)
        self.assertTrue(so.record_outage(self.agent, "grok:x", self.OUT, first + 5 * self.SEC,
                                         started=first, retried=True))
        entry = self._data()["grok:x"]
        self.assertEqual(entry["since"], "2026-10-07T18:00:00+03:00")      # out since then, still
        self.assertEqual((entry["tried"], entry["last_tried"]), (1, "2026-10-07T19:00:00+03:00"))
        second = NOW + dt.timedelta(hours=2)
        so.record_outage(self.agent, "grok:x", self.OUT, second, started=second, retried=True)
        entry = self._data()["grok:x"]
        self.assertEqual((entry["tried"], entry["last_tried"]), (2, "2026-10-07T20:00:00+03:00"))
        self.assertIsNone(entry["until"])

    def test_a_retried_refusal_that_names_a_reset_time_sets_it(self):          # S3
        so.record_outage(self.agent, "codex:y", so.classify_outage(CODEX_CREDITS, NOW), NOW)
        later = NOW + dt.timedelta(minutes=30)
        so.record_outage(self.agent, "codex:y", so.classify_outage(CODEX, later), later,
                         started=later, retried=True)
        entry = self._data()["codex:y"]
        self.assertEqual(entry["until"], "2026-10-07T19:33+03:00")
        skip, retry = so.split_outages(so.current_outages(self.agent, later))
        self.assertEqual((list(skip), list(retry)), (["codex:y"], []))

    def test_an_answer_removes_the_entry_it_was_made_under(self):              # S2
        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        call = NOW + dt.timedelta(hours=1)
        self.assertTrue(so.clear_answered(self.agent, "grok:x", call))
        self.assertEqual(so.current_outages(self.agent, call + dt.timedelta(minutes=9)), {})

    def test_a_newer_outage_than_the_answered_call_stays(self):                 # S7
        # a parallel panel recorded the seat out of credit after this panel's call to it began
        call = NOW + dt.timedelta(hours=1)
        so.record_outage(self.agent, "grok:x", self.OUT, call + 30 * self.SEC)
        self.assertFalse(so.clear_answered(self.agent, "grok:x", call))
        self.assertEqual(list(so.current_outages(self.agent, call + dt.timedelta(minutes=9))), ["grok:x"])

    def test_a_newer_refusal_than_the_answered_call_stays(self):                # S7, the other order
        # plan panel (codex): call A began first and answers last; call B began later and was refused before A
        # ended. B's refusal keeps the old `since`, so `since` alone would let A remove B's newer evidence.
        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        a = NOW + dt.timedelta(hours=1)
        b = a + 20 * self.SEC
        so.record_outage(self.agent, "grok:x", self.OUT, b + 4 * self.SEC, started=b, retried=True)
        self.assertFalse(so.clear_answered(self.agent, "grok:x", a))
        self.assertEqual(list(so.current_outages(self.agent, b + dt.timedelta(minutes=9))), ["grok:x"])

    def test_a_refusal_after_the_owners_enable_during_the_call_is_not_recorded(self):   # S15
        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        call = NOW + dt.timedelta(hours=1)
        so.clear_outage(self.agent, "grok:x", call + 2 * self.SEC)          # `tasks models enable` mid-call
        self.assertFalse(so.record_outage(self.agent, "grok:x", self.OUT, call + 9 * self.SEC,
                                          started=call, retried=True))
        self.assertEqual(so.current_outages(self.agent, call + dt.timedelta(minutes=9)), {})

    def test_a_refusal_newer_than_another_panels_answer_is_recorded_fresh(self):
        # plan panel (opus): A answered and cleared; B's call began after A's and was refused — news
        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        a = NOW + dt.timedelta(hours=1)
        so.clear_answered(self.agent, "grok:x", a)
        b = a + 30 * self.SEC
        self.assertTrue(so.record_outage(self.agent, "grok:x", self.OUT, b + 4 * self.SEC, started=b, retried=True))
        entry = self._data()["grok:x"]
        self.assertEqual((entry["tried"], entry["since"]), (1, "2026-10-07T19:00:34+03:00"))

    def test_an_answer_never_moves_a_later_clear_back(self):
        so.clear_outage(self.agent, "grok:x", NOW + dt.timedelta(hours=2))  # the owner's enable, later
        so.clear_answered(self.agent, "grok:x", NOW + dt.timedelta(hours=1))
        self.assertFalse(so.record_outage(self.agent, "grok:x", self.OUT, NOW + dt.timedelta(hours=3),
                                          started=NOW + dt.timedelta(minutes=90), retried=True))

    def test_an_answer_is_dated_at_its_calls_start(self):
        # a failure of a call that began after the answered one is news, and is recorded
        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        call = NOW + dt.timedelta(hours=1)
        so.clear_answered(self.agent, "grok:x", call)
        self.assertTrue(so.record_outage(self.agent, "grok:x", self.OUT, call + dt.timedelta(minutes=10),
                                         started=call + 5 * self.SEC))
        # one that began before it is not: the seat worked as of the answered call's start
        so.clear_answered(self.agent, "codex:y", call)
        self.assertFalse(so.record_outage(self.agent, "codex:y", self.OUT, call + dt.timedelta(minutes=10),
                                          started=call - 5 * self.SEC))

    def test_the_old_reader_reads_a_record_with_the_new_keys(self):             # S10
        # 1.6.7's reader (tasks/seat_outage.py at fa164b8), its two functions copied verbatim
        def old_load(text):
            try:
                data = json.loads(text) if text.strip() else {}
            except ValueError:
                return {}
            return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}

        def old_is_current(entry, now):
            until = entry.get("until")
            if not until:
                return True
            try:
                at = dt.datetime.fromisoformat(until)
            except ValueError:
                return True
            if at.tzinfo is None:
                at = at.replace(tzinfo=now.tzinfo)
            return at > now

        so.record_outage(self.agent, "grok:x", self.OUT, NOW)
        so.record_outage(self.agent, "grok:x", self.OUT, NOW + self.SEC, started=NOW + self.SEC, retried=True)
        so.record_outage(self.agent, "codex:y", so.classify_outage(CODEX, NOW), NOW)
        text = so._record(self.agent).read_text(encoding="utf-8")
        old = sorted(k for k, v in old_load(text).items() if old_is_current(v, NOW))
        self.assertEqual(old, sorted(so.current_outages(self.agent, NOW)))
        self.assertEqual(old, ["codex:y", "grok:x"])


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

    def _panel(self, models, judge, codex_available=False, tamper=None):
        import contextlib
        import io
        from unittest import mock
        from provider.adapters.claude import ClaudeAdapter
        from provider.adapters.codex import CodexAdapter
        out, err, code = io.StringIO(), io.StringIO(), None
        old = os.getcwd()
        guard = tamper or {"mutations": [], "cautions": [], "degraded": False}
        with mock.patch.object(ClaudeAdapter, "is_available", classmethod(lambda cls: True)), \
                mock.patch.object(CodexAdapter, "is_available", classmethod(lambda cls: codex_available)), \
                mock.patch.object(ClaudeAdapter, "run_headless_judge", judge), \
                mock.patch.object(CodexAdapter, "run_headless_judge", judge), \
                mock.patch.object(self.R, "_detect_tamper_full", lambda pp, t, b: guard):
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
        self.calls.clear()
        code, out, _ = self._panel(models, self._judge(), codex_available=True)
        self.assertNotIn("gpt-5.5", self.calls, "a seat out of credit was called again")
        self.assertIn("Skipped out of credit: codex:gpt-5.5", out)

    def test_a_codex_workspace_out_of_credits_is_recorded_then_tried_again(self):     # task 203, #34; task 276
        # no reset time is given: until task 276 the seat waited for `tasks models enable`; now the next panel
        # tries it again, and it comes back by itself once the account works
        models = self.CLAUDE[:2] + ("codex:gpt-5.5",)
        code, out, _ = self._panel(models, self._judge(outage=("gpt-5.5",), outage_text=CODEX_CREDITS),
                                   codex_available=True)
        self.assertIn("Recorded codex:gpt-5.5 as out of credit", out)
        record = json.loads(so._record(self.d / ".agent").read_text(encoding="utf-8"))
        self.assertEqual(record["codex:gpt-5.5"]["until"], None)
        self.calls.clear()
        code, out, _ = self._panel(models, self._judge(), codex_available=True)
        self.assertIn("gpt-5.5", self.calls, "a seat with no reset time was not tried again")
        self.assertIn("Back: codex:gpt-5.5", out)

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
        code, out, _ = self._panel(self.CLAUDE, self._judge())
        self.assertNotIn("claude-fable-5", self.calls, "a claude seat at its limit was called again")
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
        code, out, err = self._panel(self.CLAUDE, self._judge())
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [], "a judge was called although the panel could not reach its quorum")
        self.assertIn("nothing was spent", err)
        self.assertIn("tasks models enable <seat>", err)

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
        # "no available judges — install a provider CLI". (Task 276: a reset time ahead, so both are skipped — a
        # seat with none is tried again, the rare case below.)
        for model in self.CLAUDE[:2]:
            so.record_outage(self.d / ".agent", model, {"reason": "r", "until": "2026-10-08T18:00+03:00"}, NOW)
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

    # --- Task 276: a seat recorded with NO reset time is tried again by every panel -----------------------------

    def _out(self, *seats, text=CODEX_CREDITS):
        for seat in seats:
            so.record_outage(self.d / ".agent", seat, so.classify_outage(text, NOW, provider=seat.split(":")[0]),
                             NOW - dt.timedelta(hours=1))

    def _review_seats(self):
        p = self.d / ".agent" / "journal" / "enforcement.jsonl"
        if not p.exists():
            return []
        rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return [r.get("seat") for r in rows if r.get("hook") == "review"]

    def _runs(self):
        p = self.d / ".agent" / "tasks" / "042-demo" / "post-panel-reviews.jsonl"
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []

    def _entry(self, seat):
        return json.loads(so._record(self.d / ".agent").read_text(encoding="utf-8")).get(seat)

    def _rare_judge(self, still_working=(), refuse=(), fail=(), usage_refuse=(), slow_refuse=()):
        """Fake judges for the rare case (task 276): `still_working` seats take longer than the panel's first
        wait (shortened by `_short_wait`) and then answer a review; `refuse` seats refuse for credit at once
        (a free refusal); `usage_refuse` seats refuse for credit with tokens reported; `fail` seats fail at once
        for another reason; every other seat answers a review."""
        import time as _time
        from provider.usage import JudgeOutput
        calls = self.calls
        self.starts = {}

        def judge(adapter, prompt, model, system_context, **kw):
            calls.append(model)
            self.starts.setdefault(model, _time.monotonic())
            if any(m in model for m in slow_refuse):
                _time.sleep(0.4)
                return CODEX_CREDITS
            if any(m in model for m in refuse):
                return CODEX_CREDITS
            if any(m in model for m in usage_refuse):
                return JudgeOutput(CODEX_CREDITS, usage={"status": "known", "in": 12000, "out": 40})
            if any(m in model for m in fail):
                return "(FAILED — exit 1)\nTraceback: something else broke"
            if any(m in model for m in still_working):
                _time.sleep(0.6)
            return "1. **Note** — fine.\n"
        return judge

    def _short_wait(self):
        from unittest import mock
        patcher = mock.patch.object(self.R, "_RETRY_FIRST_TRY_SECS", 0.25)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _rare_setup(self, skipped=(3,)):
        # five requested, quorum 3: the CLAUDE seats listed in `skipped` are at a reset time ahead (skipped), two
        # codex seats have none (tried again), the remaining claude seats are live
        live = [c for i, c in enumerate(self.CLAUDE[:2] + (self.CLAUDE[3],)) if (i if i < 2 else 3) not in skipped]
        for i in skipped:
            so.record_outage(self.d / ".agent", self.CLAUDE[i],
                             so.classify_outage(CLAUDE_LIMIT, NOW, provider="claude"), NOW)
        self._out("codex:gpt-5.5", "codex:gpt-5.4")
        self._short_wait()
        return self.CLAUDE[:2] + (self.CLAUDE[3], "codex:gpt-5.5", "codex:gpt-5.4"), live

    def _refused_rounds(self):
        from tasks import numbers as N
        from tasks.post_d6 import read_runs
        runs = read_runs(self.d / ".agent" / "tasks" / "042-demo")
        return N.rounds_table(runs, [], 0, 10 ** 12)["panels"]

    def test_a_seat_with_no_reset_time_that_still_refuses_is_a_skip(self):          # S1 + S11
        models = self.CLAUDE[:3] + ("codex:gpt-5.5",)
        self._out("codex:gpt-5.5")
        code, out, err = self._panel(models, self._judge(outage=("gpt-5.5",), outage_text=CODEX_CREDITS),
                                     codex_available=True)
        self.assertIn("gpt-5.5", self.calls, "the seat was not tried again")
        self.assertIn("Still out of credit: codex:gpt-5.5", out)
        self.assertNotIn("[codex:gpt-5.5] done", out)
        md = self._judge_md()
        self.assertIn("3/3 judges succeeded, quorum 3", md)                 # as if it had been skipped
        self.assertNotIn("codex:gpt-5.5", md)                                # no section, no receipt, no failed line
        self.assertNotIn("codex:gpt-5.5", self._review_seats())              # no spend record
        done = [r for r in self._runs() if r.get("status") == "done"]
        self.assertEqual(done[-1]["measures"]["seated"], 3)
        entry = self._entry("codex:gpt-5.5")
        self.assertEqual((entry["tried"], entry["since"]), (1, "2026-10-07T17:00:00+03:00"))
        self.assertIsNone(entry["until"])

    def test_a_seat_with_no_reset_time_that_answers_comes_back(self):              # S2
        models = self.CLAUDE[:3] + ("codex:gpt-5.5",)
        self._out("codex:gpt-5.5")
        code, out, err = self._panel(models, self._judge(), codex_available=True)
        self.assertIn("Back: codex:gpt-5.5", out)
        self.assertIn("4/4 judges succeeded, quorum 3", self._judge_md())
        self.assertIn("JUDGE: codex:gpt-5.5", self._judge_md())
        self.assertIn("codex:gpt-5.5", self._review_seats())
        self.assertIsNone(self._entry("codex:gpt-5.5"))

    def test_a_retried_seat_that_fails_otherwise_is_a_failed_seat_and_stays_out(self):   # S8
        # plan panel (sonnet, codex): only an answer clears — a credit refusal in a wording `classify_outage`
        # does not know would otherwise erase a real outage
        models = self.CLAUDE[:3] + ("codex:gpt-5.5",)
        self._out("codex:gpt-5.5")
        code, out, err = self._panel(models, self._judge(fail=("gpt-5.5",)), codex_available=True)
        md = self._judge_md()
        self.assertIn("3/4 judges succeeded, quorum 3", md)
        self.assertIn("Failed judges:** codex:gpt-5.5", md)
        self.assertEqual(self._entry("codex:gpt-5.5")["tried"], 1)

    def test_the_live_seats_do_not_wait_for_a_retried_seat(self):                  # S9
        import threading
        import time as _time
        models = self.CLAUDE[:3] + ("codex:gpt-5.5",)
        self._out("codex:gpt-5.5")
        starts, lock = {}, threading.Lock()

        def judge(adapter, prompt, model, system_context, **kw):
            with lock:
                starts.setdefault(model, _time.monotonic())
            if "gpt-5.5" in model:
                _time.sleep(0.8)
                return CODEX_CREDITS
            return "1. **Note** — fine.\n"

        self._panel(models, judge, codex_available=True)
        self.assertIn("gpt-5.5", starts, "the seat was not tried again")
        first = min(starts.values())
        late = {m: round(t - first, 2) for m, t in starts.items() if t - first > 0.5}
        self.assertEqual(late, {}, "a seat waited for the retried one")

    def test_the_rare_case_tries_the_retry_seats_first_and_refuses_without_a_round(self):   # S4 + S11
        models, live = self._rare_setup()
        code, out, err = self._panel(models, self._rare_judge(refuse=("gpt-5",)), codex_available=True)
        self.assertEqual(code, 1, out + err)
        self.assertEqual(sorted(self.calls), ["gpt-5.4", "gpt-5.5"], "a live seat was spent on a refused panel")
        self.assertFalse((self.d / ".agent" / "tasks" / "042-demo" / "judge.md").exists())
        self.assertEqual(self._review_seats(), [])
        done = [r for r in self._runs() if r.get("status") == "done"]
        self.assertEqual(done[-1].get("outcome"), "refused")
        self.assertEqual(self._refused_rounds(), [], "`tasks numbers` lists the refused panel as a round")
        self.assertIn("Still out of credit: codex:gpt-5.5", out)
        self.assertEqual(self._entry("codex:gpt-5.4")["tried"], 1)
        self.assertIn("quorum is 3", err)

    def test_the_rare_case_starts_the_panel_when_enough_come_back(self):            # S5
        models, live = self._rare_setup()
        code, out, err = self._panel(models, self._rare_judge(still_working=("gpt-5.5",), refuse=("gpt-5.4",)),
                                     codex_available=True)
        md = self._judge_md()
        self.assertIn("3/3 judges succeeded, quorum 3", md)
        self.assertEqual(self.calls.count("gpt-5.5"), 1, "the working seat was cut and started again")
        self.assertNotIn("gpt-5.4", md)
        self.assertEqual(sorted(self._review_seats()),
                         sorted(["claude:claude-opus-5-5:high", "claude:claude-sonnet-5:high", "codex:gpt-5.5"]))
        self.assertIsNone(self._entry("codex:gpt-5.5"))
        self.assertIn("Back: codex:gpt-5.5", out)
        # the live seats start at the first wait's end, not when the working seat's review ends (0.6 s)
        t0 = min(self.starts.values())
        live = {m: round(self.starts[m] - t0, 2) for m in ("claude-opus-5-5", "claude-sonnet-5")}
        self.assertTrue(all(0.2 <= d < 0.5 for d in live.values()), live)

    def test_the_rare_case_refuses_when_too_few_come_back(self):                   # S6 + S11
        models, live = self._rare_setup(skipped=(1, 3))
        code, out, err = self._panel(models, self._rare_judge(still_working=("gpt-5.5",), refuse=("gpt-5.4",)),
                                     codex_available=True)
        self.assertEqual(code, 1, out + err)
        self.assertEqual(sorted(self.calls), ["gpt-5.4", "gpt-5.5"], "the live seat was started")
        self.assertFalse((self.d / ".agent" / "tasks" / "042-demo" / "judge.md").exists())
        self.assertEqual(self._review_seats(), ["codex:gpt-5.5"], "the working seat's run is not recorded")
        self.assertEqual(self._refused_rounds(), [])
        self.assertIsNone(self._entry("codex:gpt-5.5"), "the seat that came back keeps its outage")
        self.assertEqual(self._entry("codex:gpt-5.4")["tried"], 1)
        self.assertIn("codex:gpt-5.5", err)

    def test_a_fast_failure_of_another_kind_is_not_back(self):                    # S13
        models, live = self._rare_setup()
        code, out, err = self._panel(models, self._rare_judge(fail=("gpt-5.5",), refuse=("gpt-5.4",)),
                                     codex_available=True)
        self.assertEqual(code, 1, out + err)
        self.assertEqual(sorted(self.calls), ["gpt-5.4", "gpt-5.5"], "the live seats were started")
        self.assertEqual(self._entry("codex:gpt-5.5")["tried"], 1, "a failure of another kind removed the outage")

    def test_a_credit_refusal_that_cost_tokens_is_a_failed_run(self):              # S14
        models = self.CLAUDE[:3] + ("codex:gpt-5.5",)
        self._out("codex:gpt-5.5")
        code, out, err = self._panel(models, self._rare_judge(usage_refuse=("gpt-5.5",)), codex_available=True)
        md = self._judge_md()
        self.assertIn("3/4 judges succeeded, quorum 3", md)
        self.assertIn("codex:gpt-5.5", self._review_seats(), "the spent call left no record")
        self.assertEqual(self._entry("codex:gpt-5.5")["tried"], 1)

    def test_a_credit_refusal_as_slow_as_the_first_wait_is_a_failed_run(self):     # S14, by time
        models = self.CLAUDE[:3] + ("codex:gpt-5.5",)
        self._out("codex:gpt-5.5")
        self._short_wait()
        code, out, err = self._panel(models, self._rare_judge(slow_refuse=("gpt-5.5",)), codex_available=True)
        self.assertIn("3/4 judges succeeded, quorum 3", self._judge_md())
        self.assertIn("codex:gpt-5.5", self._review_seats())
        self.assertEqual(self._entry("codex:gpt-5.5")["tried"], 1)

    def test_a_write_during_the_first_wait_voids_the_panel(self):                 # S12
        models, live = self._rare_setup()
        dirty = {"mutations": ["a judge wrote a.py"], "cautions": [], "degraded": False}
        code, out, err = self._panel(models, self._rare_judge(refuse=("gpt-5",)), codex_available=True,
                                     tamper=dirty)
        self.assertEqual(code, 1)
        self.assertIn("a judge wrote a.py", err)
        done = [r for r in self._runs() if r.get("status") == "done"]
        self.assertEqual(done[-1].get("outcome"), "void")
        self.assertIsNone(self._entry("codex:gpt-5.4").get("tried"), "a voided panel noted its tries")


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
