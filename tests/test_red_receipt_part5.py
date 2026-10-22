#!/usr/bin/env python3
"""The red receipt, part 5 (PHASE-B.md item 9b; task 284's contract): what part 4's last review left.

A Twisted `trial` run's `PASSED (successes=N)` closes its summary (green, not an unclosed failure); without base.json a
reference that ran on a recorded tree is checked for dirty work from its own commit and tree; a note says
"`tasks red --check` asks again", and a row gets a hint, only for a cause a re-run can fix; the dirty path named is the
first sorted; a tree a record does not name is said so; rows run with `NO_COLOR=1`; a digest is worked out once.

Each test names the row of task 284's contract it proves. Real temporary git projects from part 1's `Project`, the CLI
driven as `python3 -m tasks.cli`. Pure stdlib unittest. Run: python3 tests/test_red_receipt_part5.py
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from tests.test_red_receipt import Project  # noqa: E402

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
    """A stub view: digests by treeish, listings by treeish (None: not known), ancestry as given."""

    def __init__(self, digests, listings=None, newer=True, last_none=""):
        self.digests, self.listings, self._newer, self.last_none = digests, listings or {}, newer, last_none
        self.today: dict = {}

    def digest(self, scope, treeish, **_k):
        return self.digests.get(treeish)

    def entries(self, scope, treeish, **_k):
        return self.listings.get(treeish)

    def newer(self, scope, old, new):
        return self._newer

    def older(self, scope, old, new):
        return False

    def on_head(self, scope, commit):
        return True

    def has(self, scope, treeish):                       # task 285: every stub tree is there
        return True


def _rows(red, labels):
    rows, _ = red.parse_contract("## Test contract\n\n| # | Kind | Behavior | Test |\n|---|---|---|---|\n"
                                 + "".join(f"| {n} | normal | b{n} | `true {n}` |\n" for n in labels))
    return rows


class Summaries(Base_):
    def test_trial_passed_closes_a_summary(self):
        # row 1 (opus, task 283's impl round 2): Twisted trial closes with PASSED, not OK
        from tasks import red
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nPASSED (successes=3)\n"), ("green", "unittest", 0))
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nPASSED (skips=3)\n")[0], "skipped")
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nPASSED (expectedFailures=1, successes=2)\n"),
                         ("green", "unittest", 1))
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nFAILED (failures=1, successes=2)\n")[0], "red")
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nPASSED (unexpectedSuccesses=1, successes=2)\n")[0],
                         "green")

    def test_coloured_summaries_are_read(self):
        # row 9 (opus, sonnet, codex): a forced colour wraps the summary in ANSI escapes — read as plain
        from tasks import red
        self.assertEqual(red._side_status(0, b"\x1b[33m=== 1 skipped in 0.01s ===\x1b[0m\n")[0], "skipped")
        self.assertEqual(red._side_status(0, b"Ran 2 tests in 0.0s\n\n\x1b[32mOK\x1b[0m\n")[0], "green")
        self.assertEqual(red._side_status(0, b"Ran 1 test in 0.0s\n\n\x1b[1;33mOK (skipped=1)\x1b[0m\n")[0], "skipped")

    def test_the_next_summary_is_found_by_bisect(self):
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


class Base(Base_):
    def test_without_base_json_the_dirty_work_is_still_checked(self):
        # row 2 (codex): base.json gone after the first run — its base entry (a recorded tree) still tells the work
        (self.code / "extra.py").write_text("X = 1\n", encoding="utf-8")
        self.started(change=False)
        self.change_code()
        self.assertEqual(self.cli("red").returncode, 0)
        (self.task_dir / "base.json").unlink()
        (self.code / "README.md").write_text("docs only\n", encoding="utf-8")
        docs_only = self.commit_code("docs, without the activation's work", "README.md")
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={docs_only}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertRegex(self.check(), r"row 2: proved on older code")
        self.assertIs(self.receipt_lines()[0]["base"]["code"]["clean"], False)      # carried from base.json
        # a record written before `clean` was carried (part 4's): the dirty work is inferred from its listings
        from tasks import red
        view = View({"t0": "s", "c1": "b", "tn": "n"}, listings={"c0": {}, "t0": {"x.py": (b"100644", b"w")},
                                                                 "c1": {}})
        rec = {"base": {"code": {"commit": "c1", "used": "commit"}}, "now_tree": {"code": "tn"}}
        old_ref = {"code": {"commit": "c0", "tree": "t0", "used": "tree"}}              # no `clean`
        self.assertEqual(red._base_check(rec, old_ref, view, True)[0], "older")

    def test_a_reference_whose_tree_is_gone_is_not_listed(self):
        # row 2 (opus): `used: commit` because the recorded tree was collected — the tree is not asked for
        from tasks import red
        view = View({"c0": "s", "c1": "b", "tn": "n"}, listings={"c0": {}, "c1": {}})
        rec = {"base": {"code": {"commit": "c1", "used": "commit"}}, "now_tree": {"code": "tn"}}
        ref = {"code": {"commit": "c0", "tree": "tgone", "used": "commit"}}
        self.assertEqual(red._base_check(rec, ref, view, True)[0], "ok")

    def test_without_base_json_a_hint_names_the_base(self):
        # row 10 (opus): `tasks red` refuses a run without --base when there is no base.json — the hint names one
        self.started()
        (self.task_dir / "base.json").unlink()
        start = self.head(self.code)
        self.assertEqual(self.cli("red", "--base", f"code={start}").returncode, 0)
        p = self.code / "tests" / "test_old.py"
        p.write_text(p.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        out = self.check()
        self.assertRegex(out, r"refresh: tasks red \S+ --rows 1 [^\n]*--base code=" + start)

    def test_only_a_fixable_cause_gets_a_hint(self):
        # row 3 (agy, opus, sonnet): a half capture cannot be judged by a re-run; a frozen miss can
        from tasks import red
        rows = _rows(red, ["1"])
        key = red.row_key(rows[0])
        rec = {"row": "1", "run": "r1", "verdict": "proved", "row_sha": "x", "row_key": key, "contract": [key],
               "command": "true 1", "base": {"code": {"commit": "c0", "used": "commit"}}, "now_tree": {"code": "tn"}}
        half = {"code": {"commit": "c0", "tree": None, "clean": False}}
        st = red.compute_state(rows, [rec], {}, View({}), None, 0, half)
        out = "\n".join(red.render_state(st, "001", "head"))
        self.assertNotIn("refresh:", out)
        self.assertNotIn("asks again", out)
        rec2 = dict(rec, base={"code": {"commit": "c9", "used": "commit"}})
        st = red.compute_state(rows, [rec2], {}, View({}, last_none="frozen"), None, 0,
                               {"code": {"commit": "c0", "tree": "t0", "clean": True}})
        out = "\n".join(red.render_state(st, "001", "head"))
        self.assertIn("asks again", out)
        self.assertIn("refresh:", out)

    def test_the_named_dirty_path_is_the_first_sorted(self):
        # row 4 (agy): many paths lack the work — the note names the first one sorted, whatever the hash seed
        from tasks import red
        work = {f"m{n:02}.py": (b"100644", b"w") for n in range(30)}
        view = View({"t0": "s", "cb": "b", "tn": "n"}, listings={"t0": work, "cb": {}})
        rec = {"base": {"code": {"commit": "cb", "used": "commit"}}, "now_tree": {"code": "tn"}}
        res, why, _fixable = red._base_check(rec, {"code": {"commit": None, "tree": "t0", "clean": False}}, view,
                                             False)
        self.assertEqual(res, "older")
        self.assertIn("(m00.py)", why)

    def test_a_missing_tree_says_so(self):
        # row 5 (opus): a record that names no tree for a scope is not "written while the section was written"
        from tasks import red
        self.started(change=False)
        head = self.head(self.code)
        view = red._Trees({"code": self.code}, [":(exclude).agent"], {})
        view.frozen = True
        self.assertIsNone(view.digest("code", "0" * 40))                   # an earlier frozen miss
        view.frozen = False
        rec = {"base": {"code": {"commit": head, "used": "commit"}}, "now_tree": {}}      # no tree named for `code`
        res, why, _fixable = red._base_check(rec, {"code": {"commit": head, "clean": True}}, view, True)
        self.assertEqual(res, "unchecked")
        self.assertNotIn("written while", why)                                        # the frozen miss's reason
        self.assertIn("names no tree", why)
        self.assertIsNone(view.digest("gone", head))                                  # a scope no longer configured
        self.assertIn("no longer", red._why_none(view, "gone"))
        # a clean reference skips the dirty-work listing: there the unknown now tree must stop the proof by itself
        stub = View({"t0": "s", "c2": "b"})                                           # "tn" not known
        late = {"base": {"code": {"commit": "c2", "used": "commit"}}, "now_tree": {"code": "tn"}}
        self.assertEqual(red._base_check(late, {"code": {"commit": "c0", "tree": "t0", "clean": True}}, stub, True)[0],
                         "unchecked")

    def test_a_digest_is_worked_out_once(self):
        # row 7 (codex m): the listing was cached, its digest hashed again on every call
        from unittest import mock
        from tasks import red
        self.started(change=False)
        head = self.head(self.code)
        view = red._Trees({"code": self.code}, [":(exclude).agent"], {})
        real, calls = red._digest_entries, []

        def counted(entries):
            calls.append(1)
            return real(entries)
        with mock.patch.object(red, "_digest_entries", counted):
            first = view.digest("code", head, code=True)
            self.assertEqual(view.digest("code", head, code=True), first)
            view.frozen = True
            self.assertEqual(view.digest("code", head, code=True), first)          # cached: no frozen miss
        self.assertEqual(len(calls), 1)
        self.assertFalse(view.missed)


class RoundOne(Base_):
    """Task 284's impl round 1 (rows 12-20)."""

    def test_every_terminal_escape_is_taken_out(self):
        # row 12 (codex): `:` parameters, a charset switch, a title — each hid an all-skipped summary, read green
        from tasks import red
        skip = b"=== 1 skipped in 0.01s ===\n"
        for pre in (b"\x1b[38:5:3m", b"\x1b[38:2::255:0:0m", b"\x1b(B\x1b[m", b"\x1b]0;title\x07",
                    b"\x1b]8;;http://x\x1b\\"):
            self.assertEqual(red._side_status(0, pre + skip)[0], "skipped", pre)
        self.assertEqual(red._side_status(0, b"\x1b]0;title\n" + skip)[0], "skipped")     # an OSC never ends past its line
        self.assertEqual(red._side_status(0, b"Ran 2 tests in 0.0s\n\n\x1b[38:2::0:255:0mOK\x1b(B\x1b[m\n")[0], "green")

    def test_a_tree_that_is_no_string_is_unknown(self):
        # row 13 (sonnet, codex): a list as a cache key raised TypeError — `--check` ended in a traceback
        import json
        from tasks import red
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        p = self.task_dir / "red-receipt.jsonl"
        recs = self.receipt_lines()
        for rec in recs:
            rec["now_tree"] = {"code": [], "": {"a": 1}}
        p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
        self.assertRegex(self.check(), r"row 1: ")
        view = red._Trees({"code": self.code}, [":(exclude).agent"], {})
        self.assertIsNone(view.digest("code", ["x"], code=True))
        self.assertEqual(view.last_none, "tree")

    def test_an_uncommitted_start_names_no_base(self):
        # row 14 (opus, sonnet, codex): without base.json the hint named a dirty start's commit — "older" again
        (self.code / "extra.py").write_text("X = 1\n", encoding="utf-8")
        self.started(change=False)
        self.change_code()
        self.assertEqual(self.cli("red").returncode, 0)
        self.assertIs(self.receipt_lines()[0]["base"]["code"]["clean"], False)
        (self.task_dir / "base.json").unlink()
        p = self.code / "tests" / "test_old.py"
        p.write_text(p.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        out = self.check()
        self.assertNotRegex(out, r"refresh: tasks red")
        self.assertIn("code's start was uncommitted work", out)
        # a record written before `clean` was carried (part 4's): not known to be clean, so no `--base` either
        import tempfile
        from tasks import red
        with tempfile.TemporaryDirectory() as d:
            got = red._base_hint(Path(d), {"code": {"commit": "c0", "tree": "t0", "used": "tree"}})
        self.assertIsNone(got[0] if isinstance(got, tuple) else got)

    def test_the_last_line_and_the_error_are_plain(self):
        # row 15 (opus, sonnet): escapes reached the table, and a coloured `E   AssertionError` was not found
        from tasks import red
        self.assertEqual(red._last_line(b"x\n\x1b[31mFAILED (failures=1)\x1b[0m\n"), "FAILED (failures=1)")
        self.assertEqual(red._first_error(b"\x1b[31mE   AssertionError: 1 != 2\x1b[0m\n"), "AssertionError: 1 != 2")

    def test_a_reference_naming_no_code_is_not_asked_again(self):
        # row 16 (opus): no commit and no tree was "the record names no tree" and "asks again"
        from tasks import red
        rec = {"base": {"code": {"commit": "c1", "used": "commit"}}, "now_tree": {"code": "tn"}}
        res, why, fixable = red._base_check(rec, {"code": {"clean": True}}, View({"c1": "b", "tn": "n"}), False)
        self.assertEqual(res, "unchecked")
        self.assertIn("names no commit or tree", why)
        self.assertFalse(fixable)

    def test_a_half_capture_gets_no_refresh_command(self):
        # row 17 (codex): stale and unrecorded rows were told a `--rows` run that `_plan_base` refuses
        import json
        self.started()
        b = self.task_dir / "base.json"
        doc = json.loads(b.read_text(encoding="utf-8"))
        doc["scopes"]["code"].update({"tree": None, "clean": False})
        b.write_text(json.dumps(doc), encoding="utf-8")
        self.set_contract([ROW1, ROW2])
        out = self.check()
        self.assertRegex(out, r"row 1: no record")
        self.assertNotIn("tasks red 1 --rows", out)
        self.assertIn("activation tree was not captured", out)
        # a row added after a run (on `--base`) gets no added-later command either
        from tasks import red
        rows = _rows(red, ["1", "2"])
        k1 = red.row_key(rows[0])
        rec = {"row": "1", "run": "r1", "verdict": "proved", "row_sha": "x", "row_key": k1, "contract": [k1],
               "command": "true 1", "base": {"code": {"commit": "c9", "used": "commit"}}, "now_tree": {"code": "tn"}}
        st = red.compute_state(rows, [rec], {}, View({}), None, 0, {"code": {"commit": "c0", "tree": None, "clean": False}})
        out = "\n".join(red.render_state(st, "001", "head"))
        self.assertIn("row 2: no record", out)
        self.assertNotIn("added later, each needs", out)

    def test_scope_names_are_shell_quoted(self):
        # row 18 (codex): `--base backend api=c1` splits into two words
        import shlex
        import tempfile
        from tasks import red
        with tempfile.TemporaryDirectory() as d:
            got = red._base_hint(Path(d), {"": {"commit": "c0", "used": "commit"},
                                           "backend api": {"commit": "c1", "used": "commit"}})
        got = got[0] if isinstance(got, tuple) else got                 # (part 4's returned the suffix alone)
        self.assertEqual(shlex.split(got), ["--base", "c0", "--base", "backend api=c1"])

    def test_each_end_is_read_after_its_own_summary(self):
        # row 19 (sonnet): a search for each end from the text's start read the first summary's OK twice
        from tasks import red
        out = b"Ran 2 tests in 0.0s\n\nOK\nRan 1 test in 0.0s\n\nOK (skipped=1)\n"
        self.assertEqual(red._side_status(0, out)[0], "skipped")

    def test_the_cli_doc_documents_round_one(self):
        # row 20
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        for word in ("tput sgr0", "no refresh command", "shell-quoted"):
            self.assertIn(word, body, word)


class Env(unittest.TestCase):
    def test_rows_run_without_colour(self):
        # row 6 (sonnet): a coloured summary line is no summary — the row's environment asks for none
        sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))
        from tasks import red
        env = red._row_env({"FORCE_COLOR": "1", "PATH": "/usr/bin", "TERM": "xterm"}, Path("/h"), Path("/c"))
        self.assertEqual(env.get("NO_COLOR"), "1")
        self.assertNotIn("FORCE_COLOR", env)
        env = red._row_env({"PY_COLORS": "1", "PYTHON_COLORS": "1", "PATH": "/usr/bin"}, Path("/h"), Path("/c"))
        self.assertEqual((env.get("PY_COLORS"), env.get("PYTHON_COLORS")), ("0", "0"))      # they outrank NO_COLOR


class Text(unittest.TestCase):
    def test_the_cli_doc_documents_part_5(self):
        # row 8
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        for word in ("trial", "NO_COLOR", "inferred", "a re-run can fix"):
            self.assertIn(word, body, word)


if __name__ == "__main__":
    unittest.main()
