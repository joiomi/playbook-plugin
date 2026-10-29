#!/usr/bin/env python3
"""The red receipt's readers of a run's output — kept as they were by task 342 (the simple receipt).

A side that passed no test is `skipped`, never green; a green that skipped a test is `partly skipped`; every summary of
unittest's and pytest's in an output is read (each ends before the next), a colour escape is read as plain text, a
non-ASCII digit is no number. These are part 2-5's unit tests (task 281-284), moved here verbatim from the old test
files (their CLI tests went with the parts task 342 removed); each keeps its class and name as a prefix.
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))
from tasks import red  # noqa: E402,F401


class Readers(unittest.TestCase):
    # from test_red_receipt_state.Skips.test_pytest_skips_are_read
    def test_state_Skips_pytest_skips_are_read(self):
        # row 3: pytest's summary — skipped with no passed is skipped; a pass beside a skip is a pass with a skip
        from tasks import red
        self.assertTrue(red._no_test_passed(b"t.py s\n=================== 1 skipped in 0.01s ===================\n"))
        self.assertTrue(red._no_test_passed(b"1 skipped, 1 warning in 0.01s\n"))
        self.assertTrue(red._no_test_passed(b"======= 1 xfailed in 0.02s =======\n"))
        self.assertTrue(red._no_test_passed(b"==== 2 deselected in 0.01s ====\n"))
        self.assertFalse(red._no_test_passed(b"========= 1 passed, 1 skipped in 0.03s =========\n"))
        self.assertFalse(red._no_test_passed(b"1 passed in 0.01s\n"))
        self.assertFalse(red._no_test_passed(b"== 3 passed, 2 skipped in 12.04s (0:00:12) ==\n"))
        self.assertEqual(red._side_status(0, b"== 3 passed, 2 skipped in 0.04s ==\n"), ("green", "pytest", 2))

    # from test_red_receipt_state.RoundOne.test_only_pytest_outcome_words_make_a_summary
    def test_state_RoundOne_only_pytest_outcome_words_make_a_summary(self):
        # row 3 (sonnet m): `5 files in 1.2s` is no summary; `xpassed` is a pass
        from tasks import red
        self.assertEqual(red._side_status(0, b"5 files in 1.2s\n"), ("green", "", 0))
        self.assertFalse(red._no_test_passed(b"=== 1 xpassed in 0.01s ===\n"))
        self.assertTrue(red._no_test_passed(b"=== 1 xfailed, 1 warning in 0.01s ===\n"))
        # a later shape-alike line does not beat unittest's real summary
        self.assertEqual(red._side_status(0, b"Ran 1 test in 0.0s\n\nOK (skipped=1)\n3 items in 0.1s\n")[0],
                         "skipped")

    # from test_red_receipt_part4.Summaries.test_an_unclosed_summary_is_a_failure
    def test_part4_Summaries_an_unclosed_summary_is_a_failure(self):
        # row 1 (agy, impl round 2 of 282): a run killed after `Ran N` — chained with `; true` it returns 0
        from tasks import red
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n")[0], "red")
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\n=== 2 passed in 0.1s ===\n")[0], "red")
        self.assertEqual(red._side_status(0, b"Ran 0 tests in 0.0s\n")[0], "skipped")      # nothing ran: no pass
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nOK\n")[0], "green")
        self.assertEqual(red._summaries(b"Ran 3 tests in 0.0s\n")[0][1], 0)       # it passed no test (agy)

    # from test_red_receipt_part4.Summaries.test_non_ascii_digits_are_no_summary
    def test_part4_Summaries_non_ascii_digits_are_no_summary(self):
        # row 13 (sonnet): a test's output controls these digits — `\\d` read `٣` as 3, and `int('²')` raised
        from tasks import red
        self.assertEqual(red._side_status(0, "Ran ٣ tests in 0.0s\n\nOK\n".encode()), ("green", "", 0))
        self.assertEqual(red._side_status(0, "Ran 2 tests in 0.0s\n\nFAILED (failures=²)\n".encode())[0], "red")
        self.assertEqual(red._side_status(0, "=== ٢ passed in 0.1s ===\n".encode()), ("green", "", 0))

    # from test_red_receipt_part4.Summaries.test_no_tests_ran_is_a_summary
    def test_part4_Summaries_no_tests_ran_is_a_summary(self):
        # row 2 (agy): a `NO TESTS RAN` line is unittest's word for a run that passed no test
        from tasks import red
        self.assertEqual(red._side_status(0, b"NO TESTS RAN\n=== 2 passed in 0.1s ===\n"),
                         ("skipped", "pytest+unittest", 0))

    # from test_red_receipt_part4.RoundOne.test_many_summaries_are_read_quickly
    def test_part4_RoundOne_many_summaries_are_read_quickly(self):
        # row 28 (sonnet): a test controls how many summaries it prints — the next one is found by bisect, once per
        # summary, never by a scan from the start (structural since task 284: a wall-clock bound flakes under load)
        from unittest import mock
        from tasks import red
        calls = []
        real = red.bisect.bisect_right

        def counted(*a, **k):
            calls.append(1)
            return real(*a, **k)
        with mock.patch.object(red.bisect, "bisect_right", counted):
            self.assertEqual(red._side_status(0, b"Ran 1 test in 0.0s\n\nOK\n" * 2000)[0], "green")
        self.assertEqual(len(calls), 2000)

    # from test_red_receipt_part5.Summaries.test_trial_passed_closes_a_summary
    def test_part5_Summaries_trial_passed_closes_a_summary(self):
        # row 1 (opus, task 283's impl round 2): Twisted trial closes with PASSED, not OK
        from tasks import red
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nPASSED (successes=3)\n"), ("green", "unittest", 0))
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nPASSED (skips=3)\n")[0], "skipped")
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nPASSED (expectedFailures=1, successes=2)\n"),
                         ("green", "unittest", 1))
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nFAILED (failures=1, successes=2)\n")[0], "red")
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nPASSED (unexpectedSuccesses=1, successes=2)\n")[0],
                         "green")

    # from test_red_receipt_part5.Summaries.test_coloured_summaries_are_read
    def test_part5_Summaries_coloured_summaries_are_read(self):
        # row 9 (opus, sonnet, codex): a forced colour wraps the summary in ANSI escapes — read as plain
        from tasks import red
        self.assertEqual(red._side_status(0, b"\x1b[33m=== 1 skipped in 0.01s ===\x1b[0m\n")[0], "skipped")
        self.assertEqual(red._side_status(0, b"Ran 2 tests in 0.0s\n\n\x1b[32mOK\x1b[0m\n")[0], "green")
        self.assertEqual(red._side_status(0, b"Ran 1 test in 0.0s\n\n\x1b[1;33mOK (skipped=1)\x1b[0m\n")[0], "skipped")

    # from test_red_receipt_part5.Summaries.test_the_next_summary_is_found_by_bisect
    def test_part5_Summaries_the_next_summary_is_found_by_bisect(self):
        # row 11 (sonnet, agy, opus, codex): one bisect per unittest summary — no rescan from the start (already true
        # before: part 4 uses bisect; this replaces its wall-clock bound)
        from unittest import mock
        from tasks import red
        calls = []
        real = red.bisect.bisect_right

        def counted(*a, **k):
            calls.append(1)
            return real(*a, **k)
        with mock.patch.object(red.bisect, "bisect_right", counted):
            self.assertEqual(red._side_status(0, b"Ran 1 test in 0.0s\n\nOK\n" * 50)[0], "green")
        self.assertEqual(len(calls), 50)

    # from test_red_receipt_part5.RoundOne.test_every_terminal_escape_is_taken_out
    def test_part5_RoundOne_every_terminal_escape_is_taken_out(self):
        # row 12 (codex): `:` parameters, a charset switch, a title — each hid an all-skipped summary, read green
        from tasks import red
        skip = b"=== 1 skipped in 0.01s ===\n"
        for pre in (b"\x1b[38:5:3m", b"\x1b[38:2::255:0:0m", b"\x1b(B\x1b[m", b"\x1b]0;title\x07",
                    b"\x1b]8;;http://x\x1b\\"):
            self.assertEqual(red._side_status(0, pre + skip)[0], "skipped", pre)
        self.assertEqual(red._side_status(0, b"\x1b]0;title\n" + skip)[0], "skipped")     # an OSC never ends past its line
        self.assertEqual(red._side_status(0, b"Ran 2 tests in 0.0s\n\n\x1b[38:2::0:255:0mOK\x1b(B\x1b[m\n")[0], "green")

    # from test_red_receipt_part5.RoundOne.test_the_last_line_and_the_error_are_plain
    def test_part5_RoundOne_the_last_line_and_the_error_are_plain(self):
        # row 15 (opus, sonnet): escapes reached the table, and a coloured `E   AssertionError` was not found
        from tasks import red
        self.assertEqual(red._last_line(b"x\n\x1b[31mFAILED (failures=1)\x1b[0m\n"), "FAILED (failures=1)")
        self.assertEqual(red._first_error(b"\x1b[31mE   AssertionError: 1 != 2\x1b[0m\n"), "AssertionError: 1 != 2")

    # from test_red_receipt_part5.RoundOne.test_each_end_is_read_after_its_own_summary
    def test_part5_RoundOne_each_end_is_read_after_its_own_summary(self):
        # row 19 (sonnet): a search for each end from the text's start read the first summary's OK twice
        from tasks import red
        out = b"Ran 2 tests in 0.0s\n\nOK\nRan 1 test in 0.0s\n\nOK (skipped=1)\n"
        self.assertEqual(red._side_status(0, out)[0], "skipped")

    # from test_red_receipt.RoundTwo.test_a_pytest_error_line_is_found
    def test_part1_RoundTwo_a_pytest_error_line_is_found(self):
        # row 56 (agy): pytest prints the exception as `E   AssertionError: …`
        from tasks import red
        self.assertEqual(red._first_error(b"____ test ____\nE   AssertionError: 1 != 2\nFAILED x\n"),
                         "AssertionError: 1 != 2")

    # from test_red_receipt_part3.Summaries.test_skips_add_up
    def test_part3_Summaries_skips_add_up(self):
        # row 8
        from tasks import red
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nOK (skipped=1)\nRan 2 tests in 0.0s\n\n"
                                             b"OK (skipped=1)\n"), ("green", "unittest", 2))
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nOK (skipped=1)\n"), ("green", "unittest", 1))
        self.assertEqual(red._side_status(0, b"== 2 passed, 1 skipped in 0.1s ==\n"), ("green", "pytest", 1))

    # from test_red_receipt_part3.Summaries.test_exit_5_without_tests_is_skipped
    def test_part3_Summaries_exit_5_without_tests_is_skipped(self):
        # row 9 (opus, impl round 2): exit 5 is pytest's "no tests collected" and Python 3.12 unittest's "NO TESTS RAN"
        from tasks import red
        self.assertEqual(red._side_status(5, b"collected 0 items\n\n=== no tests ran in 0.01s ===\n")[0], "skipped")
        self.assertEqual(red._side_status(5, b"\n----\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n")[0], "skipped")
        self.assertEqual(red._side_status(5, b"make: *** [all] Error 5\n")[0], "red")
        self.assertEqual(red._side_status(1, b"=== no tests ran in 0.01s ===\n")[0], "red")

    # from test_red_receipt_part3.Summaries.test_each_summary_ends_before_the_next
    def test_part3_Summaries_each_summary_ends_before_the_next(self):
        # row 24 (sonnet): `NO TESTS RAN` has no OK line — it must not take the next summary's
        from tasks import red
        out = b"Ran 0 tests in 0.0s\n\nNO TESTS RAN\nRan 5 tests in 0.1s\n\nOK (skipped=1)\n"
        self.assertEqual(red._side_status(0, out), ("skipped", "unittest", 1))

    # from test_red_receipt_part3.RoundOne.test_a_failure_beside_a_return_of_5_is_red
    def test_part3_RoundOne_a_failure_beside_a_return_of_5_is_red(self):
        # row 32 (agy, opus): `a ; b` — a failed, b collected nothing and returned 5
        from tasks import red
        self.assertEqual(red._side_status(5, b"Ran 1 test in 0.0s\n\nFAILED (failures=1)\n"
                                             b"=== no tests ran in 0.01s ===\n")[0], "red")
        self.assertEqual(red._side_status(5, b"=== 1 failed in 0.1s ===\n\nRan 0 tests in 0.0s\n\nNO TESTS RAN\n")[0],
                         "red")

    # from test_red_receipt_part3.RoundOne.test_an_empty_pytest_run_in_a_chain_is_skipped
    def test_part3_RoundOne_an_empty_pytest_run_in_a_chain_is_skipped(self):
        # row 33 (codex): `pytest target; pytest smoke` — the target collected nothing, smoke passed, return 0
        from tasks import red
        self.assertEqual(red._side_status(0, b"=== no tests ran in 0.01s ===\n=== 3 passed in 0.1s ===\n")[0],
                         "skipped")

    # from test_red_receipt_part3.RoundOne.test_a_unittest_end_stops_at_a_pytest_summary
    def test_part3_RoundOne_a_unittest_end_stops_at_a_pytest_summary(self):
        # row 34 (agy): an unclosed unittest summary cannot take an `OK` that comes after a pytest one
        from tasks import red
        out = b"Ran 0 tests in 0.0s\n\nNO TESTS RAN\n=== 2 passed in 0.1s ===\nOK (skipped=7)\n"
        self.assertEqual(red._side_status(0, out), ("skipped", "pytest+unittest", 0))


if __name__ == "__main__":
    unittest.main()
