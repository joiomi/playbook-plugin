#!/usr/bin/env python3
"""The red receipt's state (PHASE-B.md item 9b, part 2; task 281's contract).

Part 1 (task 280) made `tasks red` run each `## Test contract` row on the task's base and now, and record what it saw.
Part 2 reads those records: a skipped test is no proof; `--rows` gives a row added after the code its own run (with
`--base`, the code before its fix); `## Red receipt` is the state across runs (each row's newest record); `--check`
and the close PRINT each row's state — proved, already true, skipped, not proved, no record, stale — and never block;
`tasks numbers` shows the contract; the activation keeps the base of a task that started.

Each test names the row of task 281's contract it proves. Real temporary git projects from part 1's `Project`, the CLI
driven as `python3 -m tasks.cli`. Pure stdlib unittest. Run: python3 tests/test_red_receipt_state.py
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from tests.test_red_receipt import Project, _contract, _in_process  # noqa: E402

SKIP_TEST = ("import unittest\nimport src\n\n\nclass S(unittest.TestCase):\n"
             "    @unittest.skip('not written yet')\n    def test_later(self):\n        self.fail()\n")
SKIP_UNLESS_TEST = ("import unittest\nimport src\n\n\nclass U(unittest.TestCase):\n"
                    "    @unittest.skipUnless(hasattr(src, 'g'), 'no g on this code')\n"
                    "    def test_g(self):\n        self.assertEqual(src.g(), 3)\n")
G_CODE = "def f():\n    return 2\n\n\ndef g():\n    return 3\n"


def _files(d: Path) -> "dict[str, str]":
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file()}


class Skips(Project):
    def test_a_skipped_test_is_no_proof(self):
        # row 1: a test that skips on a side never makes a proof — skipped now, skipped on the base, or one of a row's two
        self.new_task()
        self.change_code()
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        (self.code / "tests" / "test_skip.py").write_text(SKIP_TEST, encoding="utf-8")
        (self.code / "tests" / "test_unless.py").write_text(SKIP_UNLESS_TEST, encoding="utf-8")
        self.set_contract([("1", "normal", "skipped now", "`tests.test_skip`"),
                           ("2", "normal", "skipped on the base", "`tests.test_unless`"),
                           ("3", "normal", "f returns 2", "`tests.test_new`"),
                           ("4", "normal", "two tests, one skips", "`tests.test_new` `tests.test_skip`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        by = {x["row"]: x for x in self.receipt_lines()}
        self.assertEqual(by["1"]["now"]["status"], "skipped")
        self.assertEqual(by["1"]["verdict"], "skipped")
        self.assertEqual(by["2"]["status"], "skipped")             # the base side
        self.assertEqual(by["2"]["now"]["status"], "green")
        self.assertEqual(by["2"]["verdict"], "skipped")
        self.assertEqual(by["3"]["verdict"], "proved")
        self.assertEqual([t["verdict"] for t in by["4"]["tests"]], ["proved", "skipped"])
        self.assertEqual(by["4"]["verdict"], "skipped")             # never "partly proved"
        self.assertIn("skipped: 3 (rows 1, 2, 4)", self.section())

    def test_a_partly_skipped_run_is_green(self):
        # row 2: no pass is `ran − skipped − expected failures ≤ 0`; a pass beside a skip is green with its skips, and a
        # now side with a skip cannot prove; a red side stays red whatever it skipped
        from tasks import red
        self.assertFalse(red._no_test_passed(
            b"...\n----------------------------------------------------------------------\n"
            b"Ran 3 tests in 0.002s\n\nOK (skipped=1)\n"))
        self.assertFalse(red._no_test_passed(b"Ran 3 tests in 0.000s\n\nOK (skipped=1, expected failures=1)\n"))
        self.assertTrue(red._no_test_passed(b"Ran 1 test in 0.000s\n\nOK (skipped=1)\n"))
        self.assertTrue(red._no_test_passed(b"Ran 1 test in 0.000s\n\nOK (expected failures=1)\n"))
        self.assertTrue(red._no_test_passed(b"Ran 2 tests in 0.000s\n\nOK (skipped=1, expected failures=1)\n"))
        self.assertTrue(red._no_test_passed(b"\nRan 0 tests in 0.000s\n\nOK\n"))
        self.assertTrue(red._no_test_passed(b"\nRan 0 tests in 0.000s\n\nOK (skipped=1)\n"))   # a class-level skip
        self.assertFalse(red._no_test_passed(b"Ran 2 tests in 0.000s\n\nOK\n"))
        # every summary counts (task 282, row 7): an all-skipped one anywhere is no pass — a chained command's, or a
        # test's own printed one (the cautious side)
        self.assertTrue(red._no_test_passed(b"Ran 1 test in 0.0s\n\nOK (skipped=1)\nRan 4 tests in 0.1s\n\nOK\n"))
        self.assertFalse(red._no_test_passed(b"all good\n"))         # no summary read: the exit code stands
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nOK (skipped=1)\n"), ("green", "unittest", 1))
        self.assertEqual(red._side_status(0, b"Ran 1 test in 0.0s\n\nOK (skipped=1)\n"), ("skipped", "unittest", 1))
        self.assertEqual(red._side_status(1, b"Ran 2 tests in 0.0s\n\nFAILED (failures=1, skipped=1)\n")[0], "red")
        self.assertEqual(red._side_status(0, b"done\n"), ("green", "", 0))
        self.assertEqual(red.verdict("red", "green", now_skips=1), "partly skipped")
        self.assertEqual(red.verdict("green", "green", now_skips=1), "partly skipped")
        self.assertEqual(red.verdict("red", "green"), "proved")
        self.assertEqual(red.verdict("skipped", "green"), "skipped")
        self.assertEqual(red._combine(["proved", "partly skipped"]), "partly skipped")

    def test_pytest_skips_are_read(self):
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

    def test_each_side_names_its_skip_reader(self):
        # row 15: a side records the summary it read; a row no reader matched is counted, never taken for a pass
        self.new_task()
        self.change_code()
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`"),
                           ("2", "edge", "a plain command", "`test -f code/src.py`")])
        self.assertEqual(self.cli("red").returncode, 0)
        by = {x["row"]: x for x in self.receipt_lines()}
        self.assertEqual(by["1"]["reader"], "unittest")
        self.assertEqual(by["1"]["now"]["reader"], "unittest")
        self.assertEqual(by["2"]["now"]["reader"], "")
        r = self.cli("red", "1", "--check")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertRegex(r.stdout, r"no skip reader 1 \(row 2\)")
        self.assertRegex(self.section(), r"(?m)^No skip reader matched[^\n]*row 2")


class State(Project):
    def rows(self):
        return [("1", "normal", "f returns 2", "`tests.test_new`"),
                ("2", "must-not", "the old test still holds", "`tests.test_old`")]

    def started(self, rows=None):
        self.new_task()
        self.change_code()
        self.set_contract(rows or self.rows())

    def check(self):
        r = self.cli("red", "1", "--check")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout

    def test_rows_runs_only_the_named_rows(self):
        # row 4
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        first = self.receipt_lines()
        r = self.cli("red", "--rows", "2")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        lines = self.receipt_lines()
        self.assertEqual([x["row"] for x in lines], ["1", "2", "2"])
        self.assertEqual(lines[:2], first)                         # row 1's record is the one it had
        self.assertNotEqual(lines[2]["run"], first[0]["run"])
        self.assertEqual(self.cli("red", "--rows", "1,2").returncode, 0)
        self.assertEqual([x["row"] for x in self.receipt_lines()][3:], ["1", "2"])
        self.assertEqual(self.cli("red", "--rows", "1-2").returncode, 0)
        section = self.task_md.read_bytes()
        r = self.cli("red", "--rows", "9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no row 9", r.stderr)
        for bad in ("two", "2-1", "", ",", "1,,2", "-1"):
            r = self.cli("red", "--rows", bad)
            self.assertEqual(r.returncode, 2, (bad, r.stdout, r.stderr))
        r = self.cli("red", "--rows")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(self.receipt_lines()), 7)             # the refusals ran nothing
        self.assertEqual(self.task_md.read_bytes(), section)       # and wrote nothing

    def test_the_section_is_the_state_across_runs(self):
        # row 5: the newest record of each row, whichever run wrote it
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        run1 = self.receipt_lines()[0]["run"]
        self.assertEqual(self.cli("red", "--rows", "2").returncode, 0)
        run2 = self.receipt_lines()[-1]["run"]
        sec = self.section()
        self.assertIn("proved: 1 (row 1)", sec)                    # from run 1 — run 2 did not run row 1
        self.assertIn("already true: 1 (row 2)", sec)
        self.assertRegex(sec, r"(?m)^Runs: .*`" + run1 + r"`[^;\n]*on the base[^;\n]*code [0-9a-f]{7}")
        self.assertIn(run2, sec)
        self.assertRegex(sec, r"(?m)^\| 2 \| must-not \|.*\| already true \|.*" + run2)

    def test_a_late_row_is_marked_with_its_base(self):
        # row 6: a row the contract did not have at the task's first run, run with --base on the code before its fix
        self.started(rows=[("1", "normal", "f returns 2", "`tests.test_new`")])
        self.assertEqual(self.cli("red").returncode, 0)
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "the change", cwd=self.code)
        before_fix = self.head(self.code)
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        (self.code / "tests" / "test_g.py").write_text(
            "import unittest\nimport src\n\n\nclass G(unittest.TestCase):\n"
            "    def test_g(self):\n        self.assertEqual(src.g(), 3)\n", encoding="utf-8")
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`"),
                           ("2", "hostile", "g returns 3 (a judge's row)", "`tests.test_g`")])
        r = self.cli("red", "--rows", "2", "--base", f"code={before_fix}")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        late = self.receipt_lines()[-1]
        self.assertEqual(late["base"]["code"]["commit"], before_fix)
        self.assertEqual(late["verdict"], "proved")
        self.assertEqual(len(late["contract"]), 2)
        self.assertIn(late["row_key"], late["contract"])
        self.assertNotIn(late["row_key"], self.receipt_lines()[0]["contract"])
        out = self.check()
        self.assertRegex(out, r"row 2: proved [^\n]*added later[^\n]*code=" + before_fix[:7])
        self.assertNotRegex(out, r"row 1: [^\n]*added later")
        self.assertRegex(self.section(), r"(?m)^Added later[^\n]*row 2[^\n]*code=" + before_fix[:7])

    def test_a_late_row_without_its_base_is_proved_on_the_start_only(self):
        # row 6: run on the task's start, a late row's proof is not counted as proved — at the check, the close, numbers
        self.started(rows=[("1", "normal", "f returns 2", "`tests.test_new`")])
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`"),
                           ("2", "edge", "f returns 2 by its own test", "`tests.test_new.N.test_two`")])
        self.assertEqual(self.cli("red", "--rows", "2").returncode, 0)
        out = self.check()
        self.assertRegex(out, r"row 1: proved")
        self.assertRegex(out, r"row 2: proved on the task's start only[^\n]*added later")
        self.assertRegex(out, r"proved 1, ")
        self.assertRegex(out, r"on the task's start only 1")
        self.assertRegex(out, r"--rows 2 --base")                  # the way to its own base is printed
        r = self.cli("numbers", "1")
        # this fixture's first run comes after the code, so "added later" is not known (task 282, row 5)
        self.assertRegex(r.stdout, r"contract: 2 row\(s\) — proved 1, [^\n]*on the task's start only 1[^\n]*"
                                   r"added later not known \(1 after the first run\)")

    def test_renumbering_marks_no_other_row(self):
        # row 6: a row inserted in the middle is late; the row it pushed down keeps its record and is not late
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`"),
                           ("2", "edge", "f returns 2 by its own test", "`tests.test_new.N.test_two`"),
                           ("3", "must-not", "the old test still holds", "`tests.test_old`")])
        out = self.check()
        self.assertRegex(out, r"row 3: already true")
        self.assertNotRegex(out, r"row 3: [^\n]*added later")
        self.assertRegex(out, r"row 2: no record[^\n]*added later")

    def test_a_row_in_the_first_runs_contract_is_not_late(self):
        # row 6: a first run of ONE row (`--rows 1`) does not make the contract's other rows late
        self.started()
        self.assertEqual(self.cli("red", "--rows", "1").returncode, 0)
        self.assertEqual(self.cli("red", "--rows", "2").returncode, 0)
        self.assertNotIn("added later", self.check().split("\n  proved ")[0])

    def test_check_prints_the_state_and_writes_nothing(self):
        # row 7: nothing in the task's folder, no lock file, no session GC (its legacy flat file stays)
        self.started(rows=self.rows() + [("3", "edge", "never run", "`tests.test_old`")])
        self.assertEqual(self.cli("red", "--rows", "1,2").returncode, 0)
        sentinel = self.d / ".agent" / "current_state"
        sentinel.write_text("001\n", encoding="utf-8")
        before = _files(self.task_dir)
        agent_before = sorted(p.relative_to(self.d).as_posix() for p in (self.d / ".agent").rglob("*"))
        out = self.check()
        self.assertEqual(_files(self.task_dir), before)
        self.assertEqual(sorted(p.relative_to(self.d).as_posix() for p in (self.d / ".agent").rglob("*")),
                         agent_before)
        self.assertTrue(sentinel.exists())
        self.assertRegex(out, r"row 1: proved")
        self.assertRegex(out, r"row 2: already true")
        self.assertRegex(out, r"row 3: no record")
        self.assertRegex(out, r"proved 1, already true 1, skipped 0, not proved 0, no record 1, stale 0")
        r = self.cli("red", "1", "--check", "--base", "code=HEAD")
        self.assertEqual(r.returncode, 2)                          # a check runs nothing, so it takes no base
        r = self.cli("red", "1", "--check", "--rows", "1")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.cli("red", "--rows", "1").returncode, 0)
        self.assertFalse(sentinel.exists())                        # the sentinel is one the GC removes

    def test_check_without_a_base_or_a_receipt_still_prints(self):
        # row 7: a task with a contract and no run yet — every row has no record; nothing is made
        self.started()
        (self.task_dir / "base.json").unlink()
        before = _files(self.task_dir)
        out = self.check()
        self.assertRegex(out, r"row 1: no record")
        self.assertRegex(out, r"row 2: no record")
        self.assertEqual(_files(self.task_dir), before)

    def test_an_edited_row_or_test_is_stale(self):
        # row 8: the row's text, its resolved command, or any test file changed since the row's newest record
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.assertRegex(self.check(), r"stale 0")
        text = self.task_md.read_text(encoding="utf-8")
        self.task_md.write_text(text.replace("| f returns 2 |", "| f returns 2, always |"), encoding="utf-8")
        out = self.check()
        self.assertRegex(out, r"row 1: stale — the row's text changed since its run \(was: proved\)")
        self.assertRegex(out, r"row 2: already true")
        self.assertRegex(out, r"tasks red \S+ --rows 1\b")         # the command that refreshes it
        self.task_md.write_text(text, encoding="utf-8")
        self.assertRegex(self.check(), r"row 1: proved")
        self.config(red_run="cd code && python3 -m unittest -q {test}")
        out = self.check()
        self.assertRegex(out, r"row 1: stale — the command changed since its run \(was: proved\)")
        self.config()
        self.assertRegex(self.check(), r"row 1: proved")
        p = self.code / "tests" / "test_old.py"
        p.write_text(p.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        out = self.check()
        self.assertRegex(out, r"row 1: stale — the test files changed since its run \(was: proved\)")
        self.assertRegex(out, r"row 2: stale — the test files changed since its run \(was: already true\)")
        self.assertRegex(out, r"stale 2")
        self.assertRegex(out, r"tasks red \S+ --rows 1,2\b")

    def test_the_tests_digest_is_the_same_from_a_run_and_from_a_check(self):
        # row 8: with nothing changed, the check's digest is the run's (else every row would read stale)
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        out = self.check()
        self.assertRegex(out, r"row 1: proved")
        self.assertRegex(out, r"stale 0")

    def test_a_code_change_is_named_not_stale(self):
        # row 16: the green side was seen on an older tree — said, and counted apart; the row keeps its state
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.assertRegex(self.check(), r"working state changed since the run 0")
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        out = self.check()
        self.assertRegex(out, r"row 1: proved [^\n]*the working state changed since its run")
        self.assertRegex(out, r"stale 0")
        self.assertRegex(out, r"working state changed since the run 2")

    def test_a_malformed_receipt_line_is_skipped(self):
        # row 14: bad lines and wrong shapes are skipped; a torn last line does not swallow the next record
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        p = self.task_dir / "red-receipt.jsonl"
        good = p.read_bytes()
        p.write_bytes(b"not json\n" + good + b"[1, 2]\n{\"row\": 5}\n\xff\xfe{\"row\": \"1\"}\n"
                      b"{\"row\": \"1\", \"verdict\": \"proved\", \"base\": [], \"now\": 3}\n\n")
        out = self.check()
        self.assertRegex(out, r"row 1: proved")
        self.assertRegex(out, r"row 2: already true")
        p.write_bytes(good + b"{\"row\": \"2\", \"verdict\": \"bro")     # a run killed mid-line
        self.assertEqual(self.cli("red", "--rows", "2").returncode, 0)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8").splitlines()[-1])["row"], "2")
        self.assertRegex(self.check(), r"row 2: already true")
        p.write_bytes(b"\x00\x01garbage")
        out = self.check()
        self.assertRegex(out, r"row 1: no record")

    def test_a_record_without_a_row_key_is_not_read(self):
        # row 14 (old input; found by the dogfood on task 280): a record written before the row key claims nothing —
        # not "the row's text changed", not "added later"; the check says how many it left out
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        p = self.task_dir / "red-receipt.jsonl"
        old = []
        for ln in p.read_text(encoding="utf-8").splitlines():
            rec = json.loads(ln)
            rec.pop("row_key")
            rec.pop("contract")
            old.append(json.dumps(rec))
        p.write_text("\n".join(old) + "\n", encoding="utf-8")
        out = self.check()
        self.assertRegex(out, r"row 1: no record\n")
        self.assertNotIn("added later (", out)
        self.assertNotIn("text changed", out)
        self.assertIn("2 record(s) without a row key", out)

    def test_a_duplicate_row_label_is_refused(self):
        # row 17: two rows labelled 1, or a row with no label — the state cannot tell them apart
        self.started(rows=[("1", "normal", "f returns 2", "`tests.test_new`"),
                           ("1", "must-not", "the old test still holds", "`tests.test_old`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 1)
        self.assertIn("row 1 appears twice", r.stderr)
        self.assertFalse((self.task_dir / "red-receipt.jsonl").exists())
        r = self.cli("red", "1", "--check")
        self.assertIn("row 1 appears twice", r.stdout + r.stderr)
        self.set_contract([("", "normal", "f returns 2", "`tests.test_new`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 1)
        self.assertIn("a row with no number", r.stderr)


class RoundOne(Project):
    """Impl round 1's findings (task 281), each red on nested 9c766c7 before its fix."""

    def started(self, rows=None):
        self.new_task()
        self.change_code()
        self.set_contract(rows or [("1", "normal", "f returns 2", "`tests.test_new`"),
                                   ("2", "must-not", "the old test still holds", "`tests.test_old`")])

    def check(self):
        r = self.cli("red", "1", "--check")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r.stdout

    def test_an_expected_failure_beside_a_pass_cannot_prove(self):
        # row 2 (codex I): `OK (expected failures=1)` after `Ran 2` — one passed, the other failed as expected
        from tasks import red
        self.assertEqual(red._side_status(0, b"Ran 2 tests in 0.0s\n\nOK (expected failures=1)\n"),
                         ("green", "unittest", 1))
        self.assertEqual(red.verdict("red", "green", now_skips=1), "partly skipped")

    def test_only_pytest_outcome_words_make_a_summary(self):
        # row 3 (sonnet m): `5 files in 1.2s` is no summary; `xpassed` is a pass
        from tasks import red
        self.assertEqual(red._side_status(0, b"5 files in 1.2s\n"), ("green", "", 0))
        self.assertFalse(red._no_test_passed(b"=== 1 xpassed in 0.01s ===\n"))
        self.assertTrue(red._no_test_passed(b"=== 1 xfailed, 1 warning in 0.01s ===\n"))
        # a later shape-alike line does not beat unittest's real summary
        self.assertEqual(red._side_status(0, b"Ran 1 test in 0.0s\n\nOK (skipped=1)\n3 items in 0.1s\n")[0],
                         "skipped")

    def test_a_late_row_on_the_first_runs_base_is_start_only(self):
        # row 6 (opus I): a task with no base.json names --base on every run; the late row's run on the SAME base
        self.started(rows=[("1", "normal", "f returns 2", "`tests.test_new`")])
        start = self.head(self.code)
        (self.task_dir / "base.json").unlink()
        self.assertEqual(self.cli("red", "--base", f"code={start}").returncode, 0)
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`"),
                           ("2", "edge", "f returns 2 by its own test", "`tests.test_new.N.test_two`")])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={start}").returncode, 0)
        self.assertRegex(self.check(), r"row 2: proved on the task's start only")

    def test_a_base_on_an_unchanged_scope_does_not_count(self):
        # row 6 (codex I): `--base <HEAD>` names the project, which the task did not change; the code is on its start
        self.started(rows=[("1", "normal", "f returns 2", "`tests.test_new`")])
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`"),
                           ("2", "edge", "f returns 2 by its own test", "`tests.test_new.N.test_two`")])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", self.head(self.d)).returncode, 0)
        self.assertRegex(self.check(), r"row 2: proved on the task's start only")

    def test_an_empty_contract_list_makes_no_row_late(self):
        # agy m: a record whose contract list is empty says nothing about the first run's rows
        from tasks import red
        rows, _ = red.parse_contract(
            "## Test contract\n\n| # | Kind | Behavior | Test |\n|---|---|---|---|\n| 1 | normal | b | `t.x` |\n")
        rec = {"row": "1", "run": "r1", "verdict": "proved", "row_sha": "x", "row_key": red.row_key(rows[0]),
               "contract": [], "command": "t.x"}
        st = red.compute_state(rows, [rec], {}, None, None)
        self.assertFalse(st["rows"][0]["late"])

    def test_a_record_without_its_sides_does_not_break_the_section(self):
        # row 14 (codex I): a record with the fields the reader checks but no `status` / `now`
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        p = self.task_dir / "red-receipt.jsonl"
        first = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
        bare = {k: first[k] for k in ("row", "run", "row_sha", "row_key", "contract", "command", "tests_sha",
                                      "now_tree")}
        bare.update(verdict="broken", run="bare00000000")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(bare) + "\n")
        r = self.cli("red", "--rows", "2")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertRegex(self.section(), r"(?m)^\| 1 \| normal \|.*\| broken \|")
        self.assertRegex(self.check(), r"row 1: broken")

    def test_a_fifo_receipt_does_not_hang_the_close_or_the_check(self):
        # row 9 (codex I): a FIFO where the receipt should be — one line, never a wait
        import os
        Close.light(self, contract=[("1", "normal", "f returns 2", "`tests.test_new`")])
        os.mkfifo(self.task_dir / "red-receipt.jsonl")
        r = self.cli("red", "1", "--check", timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot be read", r.stderr)
        r = self.cli("work", "done", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("red receipt (advisory", r.stdout + r.stderr)

    def test_the_report_comes_after_the_close_is_done(self):
        # row 9 (sonnet m): a slow report never holds the close's own lines
        Close.light(self, contract=[("1", "normal", "f returns 2", "`tests.test_new`")])
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout
        self.assertGreater(out.index("red receipt (advisory"), out.index("Task 001 done."))

    def test_committing_the_tasks_records_is_no_working_state_change(self):
        # row 16 (opus m): the tree keeps the index's `.agent` entries — committing the task's records moved it
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.g("add", ".agent/tasks")
        self.g("commit", "-qm", "the task's records")
        self.assertRegex(self.check(), r"working state changed since the run 0")

    def test_numbers_reads_no_tree(self):
        # row 12 (opus m): `tasks numbers` stays read-only — no git object is written for the contract line
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        (self.code / "fresh.py").write_text("x = 1\n", encoding="utf-8")
        objects = self.code / ".git" / "objects"

        def loose():
            return sorted(str(q) for q in objects.glob("??/*"))
        before = loose()
        r = self.cli("numbers", "1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("contract: 2 row(s)", r.stdout)
        self.assertEqual(loose(), before)

    def test_the_section_shows_rows_added_while_the_run_ran(self):
        # row 18 (codex I): run A knows only row 1; row 2 is added before A writes — A's section shows it
        from unittest import mock
        from tasks import red
        self.started(rows=[("1", "normal", "f returns 2", "`tests.test_new`")])
        real = red._side_run
        done = []

        def add_a_row(*a, **k):
            if not done:
                done.append(1)
                self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`"),
                                   ("2", "must-not", "the old test still holds", "`tests.test_old`")])
            return real(*a, **k)
        with mock.patch.object(red, "_side_run", add_a_row):
            code = _in_process(self.d, self.env(), red.cmd_red, ["--rows", "1"])
        self.assertIn(code, (None, 0))
        self.assertRegex(self.section(), r"(?m)^\| 2 \| must-not \|.*\| no record \|")


class Close(Project):
    def light(self, contract=None, gate=False, fenced_gate=False):
        """A light task, its gates ticked with a note, `reversible`, committed, active — a close needs no review."""
        r = self.cli("new", "light", "probe", "probe the close")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.task_dir = next((self.d / ".agent" / "tasks").glob("001-*"))
        self.task_md = self.task_dir / "task.md"
        text = self.task_md.read_text(encoding="utf-8").replace("## Risk\nunclassified\n", "## Risk\nreversible\n")
        self.assertIn("## Work\n", text)
        self.assertIn("## Pre-review\n", text)
        if gate:
            from tasks.template import CONTRACT_GATE
            text = text.replace("## Work\n", f"## Work\n- [ ] {CONTRACT_GATE}\n", 1)
        text = text.replace("- [ ] ", "- [x] ")
        text = "\n".join(ln + (" — done" if ln.startswith("- [x] ") else "") for ln in text.split("\n"))
        if contract:                                                # a light task has no `## Plan Review` to sit above
            text = text.replace("## Pre-review\n", _contract(contract) + "## Pre-review\n", 1)
        if fenced_gate:                                             # an example of the gate, inside a fence
            from tasks.template import CONTRACT_GATE
            text = text.replace("## Pre-review\n", f"```\n- [ ] {CONTRACT_GATE}\n```\n\n## Pre-review\n", 1)
        self.task_md.write_text(text, encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "the task")
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_close_reports_the_receipt(self):
        # row 9: the state is printed at the close, which still closes (advisory) — a row with no record too
        self.light(contract=[("1", "normal", "f returns 2", "`tests.test_new`")])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)          # its gates are all ticked: name the task
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`"),
                           ("2", "normal", "never run", "`tests.test_old`")])
        self.g("add", "-A")
        self.g("commit", "-qm", "the receipt")
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("red receipt (advisory", out)
        self.assertRegex(out, r"row 1: proved")
        self.assertRegex(out, r"row 2: no record")
        self.assertIn("## Status\ndone", self.task_md.read_text(encoding="utf-8"))

    def test_a_contract_gate_without_its_section_is_reported(self):
        # row 9: the gate says the task has a contract; the section is gone — the close says so, and closes
        self.light(gate=True)
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("red receipt (advisory", r.stdout + r.stderr)
        self.assertIn("no live `## Test contract` section", r.stdout + r.stderr)

    def test_a_broken_receipt_does_not_stop_the_close(self):
        # row 9 (hostile): a receipt that cannot be read is one line, never a failed close
        self.light(contract=[("1", "normal", "f returns 2", "`tests.test_new`")])
        (self.task_dir / "red-receipt.jsonl").mkdir()                # a folder where the file should be
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertIn("red receipt", r.stdout + r.stderr)

    def test_a_spent_budget_skips_the_trees_and_says_so(self):
        # row 9 (time): past its budget the report reads no tree — the rows' states without staleness, said
        from tasks import red
        self.light(contract=[("1", "normal", "f returns 2", "`tests.test_new`")])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        lines = _in_process(self.d, self.env(), red.close_report, self.d, self.task_md, 0.0)
        text = "\n".join(lines)
        self.assertIn("red receipt (advisory", text)
        self.assertRegex(text, r"row 1: proved")
        self.assertIn("staleness not checked", text)

    def test_a_task_without_the_contract_gate_gets_no_report(self):
        # row 10 (must-not)
        self.light()
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("red receipt", (r.stdout + r.stderr).lower())

    def test_a_fenced_gate_line_is_no_gate(self):
        # row 10 (must-not): the gate's line quoted inside a code fence is an example, not the task's gate
        self.light(fenced_gate=True)
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("red receipt", (r.stdout + r.stderr).lower())

    def test_a_started_task_keeps_its_base(self):
        # row 11: the status moved to in_progress, then the activation raised — the base stays
        from unittest import mock
        from tasks import lifecycle
        self.new_task(activate=False)

        def started_then_interrupted(task_file, *_a, **_k):
            p = Path(task_file)
            p.write_text(p.read_text(encoding="utf-8").replace("## Status\npending\n", "## Status\nin_progress\n", 1),
                         encoding="utf-8")
            raise KeyboardInterrupt()
        with mock.patch.object(lifecycle, "_activate_status_then_publish", started_then_interrupted):
            try:
                _in_process(self.d, self.env(), lifecycle.cmd_work, ["1"])
            except KeyboardInterrupt:
                pass
        self.assertTrue((self.task_dir / "base.json").is_file())
        self.assertIn("scopes", self.base())

    def test_a_task_still_pending_loses_the_base_it_wrote(self):
        # row 11: the negative control — part 1's cleanup stays for an activation that never moved the status, and for
        # one whose status cannot be read (no base fails closed: `tasks red` then asks for --base)
        from unittest import mock
        from tasks import lifecycle
        self.new_task(activate=False)

        def refused(*_a, **_k):
            raise SystemExit(1)
        with mock.patch.object(lifecycle, "_activate_status_then_publish", refused):
            _in_process(self.d, self.env(), lifecycle.cmd_work, ["1"])
        self.assertFalse((self.task_dir / "base.json").exists())

        def unreadable(task_file, *_a, **_k):
            Path(task_file).write_bytes(b"\xff\xfe no status here\n")
            raise SystemExit(1)
        with mock.patch.object(lifecycle, "_activate_status_then_publish", unreadable):
            _in_process(self.d, self.env(), lifecycle.cmd_work, ["1"])
        self.assertFalse((self.task_dir / "base.json").exists())

    def test_numbers_shows_the_contract(self):
        # row 12
        self.new_task()
        self.change_code()
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`"),
                           ("2", "must-not", "the old test still holds", "`tests.test_old`"),
                           ("3", "edge", "the old test again", "`tests.test_old`")])
        self.assertEqual(self.cli("red").returncode, 0)
        r = self.cli("numbers", "1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertRegex(r.stdout, r"contract: 3 row\(s\) — proved 1, already true 2, skipped 0, not proved 0, "
                                   r"no record 0, stale 0;")
        self.assertIn("test files not checked", r.stdout)

    def test_numbers_of_a_task_without_a_contract_has_no_contract_line(self):
        # row 12: the negative control
        self.light()
        r = self.cli("numbers", "1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("contract:", r.stdout)

    def test_the_cli_doc_documents_rows_and_check(self):
        # row 13
        doc = (Path(__file__).resolve().parent.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        self.assertIn("--check`**", body, "docs/cli.md has no `tasks red --check` entry")
        for word in ("--rows", "--check", "skipped", "partly skipped", "stale", "no record", "added later",
                     "on the task's start only", "advisory", "expected failure", "unittest", "pytest",
                     "working state changed"):
            self.assertIn(word, body, word)


if __name__ == "__main__":
    unittest.main()
