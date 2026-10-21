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
        res, why = red._base_check(rec, {"code": {"commit": None, "tree": "t0", "clean": False}}, view, False)
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
        res, why = red._base_check(rec, {"code": {"commit": head, "clean": True}}, view, True)
        self.assertEqual(res, "unchecked")
        self.assertNotIn("written while", why)                                        # the frozen miss's reason
        self.assertIn("names no tree", why)

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
            self.assertEqual(view.digest("code", head, code=True), first)
        self.assertEqual(len(calls), 1)


class Env(unittest.TestCase):
    def test_rows_run_without_colour(self):
        # row 6 (sonnet): a coloured summary line is no summary — the row's environment asks for none
        sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))
        from tasks import red
        env = red._row_env({"FORCE_COLOR": "1", "PATH": "/usr/bin", "TERM": "xterm"}, Path("/h"), Path("/c"))
        self.assertEqual(env.get("NO_COLOR"), "1")
        self.assertNotIn("FORCE_COLOR", env)


class Text(unittest.TestCase):
    def test_the_cli_doc_documents_part_5(self):
        # row 8
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        for word in ("trial", "NO_COLOR", "inferred", "a re-run can fix"):
            self.assertIn(word, body, word)


if __name__ == "__main__":
    unittest.main()
