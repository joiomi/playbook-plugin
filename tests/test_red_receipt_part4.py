#!/usr/bin/env python3
"""The red receipt, part 4 (PHASE-B.md item 9b; task 283's contract): what part 3's last review found it could still
claim falsely.

Every proof — a design row's as much as a late row's — needs a base that is the task's start or newer code: a row run
on code older than the start reads `proved on older code`, and a dirty activation's HEAD without its uncommitted code is
older code. A digest git could not give proves nothing and claims no change. An unclosed unittest run is a failure and
`NO TESTS RAN` a summary; a config changed twice while the section is prepared is a refusal; a task number is ASCII
digits; "added later" is said "per the first run" when that run came after the code.

Each test names the row of task 283's contract it proves. Real temporary git projects from part 1's `Project`, the CLI
driven as `python3 -m tasks.cli`. Pure stdlib unittest. Run: python3 tests/test_red_receipt_part4.py
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from tests.test_red_receipt import Project, _in_process  # noqa: E402

G_CODE = "def f():\n    return 2\n\n\ndef g():\n    return 3\n"
G_TEST = ("import unittest\nimport src\n\n\nclass G(unittest.TestCase):\n"
          "    def test_g(self):\n        self.assertEqual(src.g(), 3)\n")
KEEP_TEST = ("import unittest\nimport src\n\n\nclass K(unittest.TestCase):\n"
             "    def test_one(self):\n        self.assertEqual(src.f(), 1)\n")
ROW1 = ("1", "normal", "f returns 2", "`tests.test_new`")
ROW2 = ("2", "edge", "f returns 2 by its own test", "`tests.test_new.N.test_two`")


class Base_(Project):
    def started(self, rows=None, change=True):
        self.new_task()
        if change:
            self.change_code()
        self.set_contract(rows or [ROW1])

    def check(self):
        r = self.cli("red", "1", "--check")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout

    def commit_code(self, msg, *paths):
        self.g("add", *(paths or ("-A",)), cwd=self.code)
        self.g("commit", "-qm", msg, cwd=self.code)
        return self.head(self.code)


class View:
    """A stub `_Trees`: digests from a table (a treeish it lacks is not known), ancestry and HEAD answered as given."""

    def __init__(self, table, newer=True, on_head=True):
        self.table, self._newer, self._on_head = table, newer, on_head
        self.today: dict = {}

    def digest(self, scope, treeish, **_k):
        return self.table.get(treeish)

    def newer(self, scope, old, new):
        return self._newer

    def on_head(self, scope, commit):
        return self._on_head


def _rows(red, lines):
    rows, _ = red.parse_contract("## Test contract\n\n| # | Kind | Behavior | Test |\n|---|---|---|---|\n"
                                 + "".join(f"| {n} | normal | b{n} | `true {n}` |\n" for n in lines))
    return rows


class Summaries(Base_):
    def test_an_unclosed_summary_is_a_failure(self):
        # row 1 (agy, impl round 2 of 282): a run killed after `Ran N` — chained with `; true` it returns 0
        from tasks import red
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n")[0], "red")
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\n=== 2 passed in 0.1s ===\n")[0], "red")
        self.assertEqual(red._side_status(0, b"Ran 0 tests in 0.0s\n")[0], "skipped")      # nothing ran: no pass
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nOK\n")[0], "green")
        self.assertEqual(red._summaries(b"Ran 3 tests in 0.0s\n")[0][1], 0)       # it passed no test (agy)

    def test_non_ascii_digits_are_no_summary(self):
        # row 13 (sonnet): a test's output controls these digits — `\\d` read `٣` as 3, and `int('²')` raised
        from tasks import red
        self.assertEqual(red._side_status(0, "Ran ٣ tests in 0.0s\n\nOK\n".encode()), ("green", "", 0))
        self.assertEqual(red._side_status(0, "Ran 2 tests in 0.0s\n\nFAILED (failures=²)\n".encode())[0], "red")
        self.assertEqual(red._side_status(0, "=== ٢ passed in 0.1s ===\n".encode()), ("green", "", 0))

    def test_no_tests_ran_is_a_summary(self):
        # row 2 (agy): a `NO TESTS RAN` line is unittest's word for a run that passed no test
        from tasks import red
        self.assertEqual(red._side_status(0, b"NO TESTS RAN\n=== 2 passed in 0.1s ===\n"),
                         ("skipped", "pytest+unittest", 0))


class Section(Base_):
    def test_a_config_changed_twice_is_a_refusal(self):
        # row 3 (agy, codex, opus): the config changes during BOTH preparations — no section of two configs
        from unittest import mock
        from tasks import red
        self.started()
        real, calls = red._prepare, []

        def prepare_then_change(*a, **k):
            out = real(*a, **k)
            calls.append(1)
            self.config(red_run="cd code && python3 -m unittest " + "-q " * len(calls) + "{test}")
            return out
        before = self.task_md.read_text(encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(red, "_prepare", prepare_then_change), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = _in_process(self.d, self.env(), red.cmd_red, [])
        self.assertEqual(code, 1, out.getvalue() + err.getvalue())
        self.assertIn("the config kept changing", err.getvalue())
        self.assertEqual(self.task_md.read_text(encoding="utf-8"), before)      # the section was not written

    def test_added_later_is_said_per_the_first_run(self):
        # row 11 (sonnet m): with the first run after the code, "added later" is only what the first run shows
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2").returncode, 0)
        self.assertRegex(self.check(), r"row 2: [^\n]*added later \(per the first run\)")
        self.assertRegex(self.section(), r"(?m)^Added later \(per the first run")


class Base(Base_):
    def test_a_change_is_claimed_only_when_known(self):
        # row 4 (agy): the newest record's tree cannot be listed — no "the task changed code there" from that
        from tasks import red
        rows = _rows(red, ["1"])
        key = red.row_key(rows[0])
        rec = {"row": "1", "run": "r1", "verdict": "already true", "row_sha": "x", "row_key": key, "contract": [key],
               "command": "true 1", "base": {"code": {"commit": "c0", "used": "commit"}}, "now_tree": {"code": "tn"}}
        ref = {"code": {"commit": "c0", "tree": "t0"}}
        st = red.compute_state(rows, [rec], {}, View({"t0": "s", "c0": "s"}, on_head=False), None, 0, ref)
        self.assertEqual(st["rewritten"], [])
        self.assertFalse(any("cannot prove" in n for n in st["notes"]), st["notes"])

    def test_an_unknown_now_tree_proves_nothing(self):
        # row 5 (codex): a late row whose now tree cannot be listed is not shown to have run before its fix
        from tasks import red
        rows = _rows(red, ["1", "2"])
        k1, k2 = red.row_key(rows[0]), red.row_key(rows[1])
        common = {"row_sha": "x", "now_tree": {"code": "tn"}}
        first = dict(common, row="1", run="r1", verdict="proved", row_key=k1, contract=[k1], command="true 1",
                     base={"code": {"commit": "c0", "tree": "t0", "used": "tree"}})
        late = dict(common, row="2", run="r2", verdict="proved", row_key=k2, contract=[k1, k2], command="true 2",
                    base={"code": {"commit": "c2", "used": "commit"}})
        ref = {"code": {"commit": "c0", "tree": "t0"}}
        st = red.compute_state(rows, [first, late], {}, View({"t0": "s", "c2": "b"}), None, 0, ref)
        self.assertEqual(st["rows"][1]["state"], "proved on the task's start only")

    def test_a_base_without_the_activations_work_is_older(self):
        # row 6 (opus): the activation held uncommitted code; a commit after its HEAD that lacks it is older code
        (self.code / "extra.py").write_text("X = 1\n", encoding="utf-8")
        self.started(change=False)
        self.assertFalse(self.base()["scopes"]["code"]["clean"])
        (self.code / "README.md").write_text("docs only\n", encoding="utf-8")
        docs_only = self.commit_code("docs, without the activation's work", "README.md")
        self.change_code()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={docs_only}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertNotRegex(self.check(), r"row 2: proved \(run")

    def test_a_contract_row_on_older_code_is_no_proof(self):
        # row 7 (sonnet): a design row run on a commit OLDER than the start is red for the code's age
        (self.code / "src.py").write_text("def f():\n    return 0\n", encoding="utf-8")
        older = self.commit_code("an older f")
        (self.code / "src.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        self.commit_code("f as the task finds it")
        self.started(rows=[("1", "normal", "f is 1", "`tests.test_keep`")], change=False)
        (self.code / "tests" / "test_keep.py").write_text(KEEP_TEST, encoding="utf-8")
        self.assertEqual(self.cli("red", "--base", f"code={older}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        out = self.check()
        self.assertRegex(out, r"row 1: proved on older code")
        self.assertRegex(out, r"proved 0, ")
        self.assertRegex(out, r"on older code 1")

    def test_a_contract_row_on_newer_code_proves(self):
        # row 8: a design row run on a commit after the start (before its fix) proves (already true before: part 3
        # judges design rows by their verdict alone; the row keeps it under the stricter rule)
        self.started(rows=[("1", "hostile", "g returns 3", "`tests.test_g`")], change=False)
        (self.code / "tests" / "test_g.py").write_text(G_TEST, encoding="utf-8")
        (self.code / "src.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        part = self.commit_code("part of the change", "src.py")
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        self.assertEqual(self.cli("red", "--base", f"code={part}").returncode, 0)
        self.assertRegex(self.check(), r"row 1: proved \(run")

    def test_a_design_row_on_the_start_still_proves(self):
        # row 9 (must-not): the ordinary case — a design row on the activation base
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        out = self.check()
        self.assertRegex(out, r"row 1: proved \(run")
        self.assertRegex(out, r"proved 1, ")

    def test_dirty_work_carried_further_proves(self):
        # row 14 (opus, sonnet): the activation's uncommitted file, edited further and committed — the work is there
        (self.code / "extra.py").write_text("X = 1\n", encoding="utf-8")
        self.started(change=False)
        (self.code / "extra.py").write_text("X = 2\n", encoding="utf-8")
        carried = self.commit_code("the activation's work, carried further", "extra.py")
        self.change_code()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={carried}").returncode, 0)
        self.assertRegex(self.check(), r"row 2: proved \(run")

    def test_a_dirty_mode_change_is_part_of_the_work(self):
        # row 15 (codex): an executable bit set before the activation, uncommitted — a base without it lacks the work
        (self.code / "run.sh").write_text("#!/bin/sh\necho run\n", encoding="utf-8")
        self.commit_code("a script", "run.sh")
        (self.code / "run.sh").chmod(0o755)
        self.started(change=False)
        self.assertFalse(self.base()["scopes"]["code"]["clean"])
        (self.code / "README.md").write_text("docs only\n", encoding="utf-8")
        docs_only = self.commit_code("docs, the script's mode not in it", "README.md")
        self.change_code()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={docs_only}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertNotRegex(self.check(), r"row 2: proved \(run")

    def test_a_sibling_branch_base_is_unrelated(self):
        # row 16 (opus): a base that is neither an ancestor nor a descendant of the start is not "older"
        root = self.head(self.code)
        (self.code / "NOTES.md").write_text("on main\n", encoding="utf-8")
        self.commit_code("main moves on", "NOTES.md")
        self.g("branch", "side", root, cwd=self.code)
        self.g("checkout", "-q", "side", cwd=self.code)
        (self.code / "side.py").write_text("SIDE = 1\n", encoding="utf-8")
        sibling = self.commit_code("a sibling", "side.py")
        self.g("checkout", "-q", "-", cwd=self.code)
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={sibling}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        out = self.check()
        self.assertRegex(out, r"row 2: proved on unrelated code")
        self.assertNotIn("older code (run", out)

    def test_a_design_row_on_the_start_needs_no_git(self):
        # row 17 (opus): the base IS the reference — no digest asked, so a spent budget cannot take the proof away
        import time
        from tasks import red
        from tasks.core import load_config, read_task_text
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        rows, _ = red.parse_contract(read_task_text(self.task_md))
        st = _in_process(self.d, self.env(), red.state_of, self.d, self.task_dir, rows, load_config(self.d),
                         time.monotonic() - 1, False)
        self.assertEqual(st["rows"][0]["state"], "proved")

    def test_an_unjudged_base_is_unchecked(self):
        # row 18 (opus, sonnet): a design row on another base, and git cannot answer — counted apart, said
        from tasks import red
        rows = _rows(red, ["1"])
        key = red.row_key(rows[0])
        rec = {"row": "1", "run": "r1", "verdict": "proved", "row_sha": "x", "row_key": key, "contract": [key],
               "command": "true 1", "base": {"code": {"commit": "c9", "used": "commit"}}, "now_tree": {"code": "tn"}}
        ref = {"code": {"commit": "c0", "tree": "t0"}}
        st = red.compute_state(rows, [rec], {}, View({}), None, 0, ref)
        self.assertEqual(st["rows"][0]["state"], "proved on an unchecked base")
        self.assertIn("on an unchecked base 1", red._counts(st["rows"], st))

    def test_a_scope_added_later_is_unchecked(self):
        # row 19 (codex): a code root the reference does not have is not judged — an old base there would pass
        from tasks import red
        rows = _rows(red, ["1"])
        key = red.row_key(rows[0])
        rec = {"row": "1", "run": "r1", "verdict": "proved", "row_sha": "x", "row_key": key, "contract": [key],
               "command": "true 1", "now_tree": {"code": "tn", "extra": "xn"},
               "base": {"code": {"commit": "c0", "tree": "t0", "used": "tree"}, "extra": {"commit": "e1",
                                                                                         "used": "commit"}}}
        ref = {"code": {"commit": "c0", "tree": "t0"}}
        st = red.compute_state(rows, [rec], {}, View({"t0": "s", "e1": "e", "tn": "n", "xn": "x"}), None, 0, ref)
        self.assertEqual(st["rows"][0]["state"], "proved on an unchecked base")

    def test_the_new_states_have_their_hints(self):
        # row 20 (agy, opus, sonnet): a design row on older code is to run on the start again
        (self.code / "src.py").write_text("def f():\n    return 0\n", encoding="utf-8")
        older = self.commit_code("an older f")
        (self.code / "src.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        self.commit_code("f as the task finds it")
        self.started(rows=[("1", "normal", "f is 1", "`tests.test_keep`")], change=False)
        (self.code / "tests" / "test_keep.py").write_text(KEEP_TEST, encoding="utf-8")
        self.assertEqual(self.cli("red", "--base", f"code={older}").returncode, 0)
        self.assertRegex(self.check(), r"refresh: tasks red \S+ --rows 1\b")


class Cli(Base_):
    def test_the_task_number_is_ascii(self):
        # row 10 (opus m, sonnet m): `²` passes str.isdigit — a wrong call, as `--rows ²` is
        self.started()
        for args, word in ((("red", "²"), "usage: tasks red"), (("numbers", "²"), "usage: tasks numbers")):
            r = self.cli(*args)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(word, r.stderr)


class Text(unittest.TestCase):
    def test_the_cli_doc_documents_part_4(self):
        # row 12
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        for word in ("proved on older code", "child", "unclosed", "per the first run", "start or newer"):
            self.assertIn(word, body, word)


if __name__ == "__main__":
    unittest.main()
