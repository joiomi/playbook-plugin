#!/usr/bin/env python3
"""The red receipt, part 6 (PHASE-B.md item 9b; task 285's contract): what part 5's last review left.

A dirty activation tree that `git gc` pruned is said as gone — no "asks again", no refresh; a record that names no
now-tree points to a re-run of the row; without base.json no hint names a removed scope, and a start dirty only in
docs or tests keeps its hint; a design row on the start's code needs no now-tree.

Each test names the row of task 285's contract it proves. Real temporary git projects from part 1's `Project`, the CLI
driven as `python3 -m tasks.cli`. Pure stdlib unittest. Run: python3 tests/test_red_receipt_part6.py
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from tests.test_red_receipt_part5 import Base_, View, _rows  # noqa: E402


def _hint(red, d, ref, view):
    """`_base_hint`'s suffix, on part 6's signature (with the view) or part 5's (without)."""
    try:
        got = red._base_hint(d, ref, view)
    except TypeError:                                   # part 5's took no view
        got = red._base_hint(d, ref)
    return got[0] if isinstance(got, tuple) else got


def _view(digests, scopes):
    v = View(digests)
    v.scopes = dict.fromkeys(scopes)
    return v


class Gone(Base_):
    def test_a_pruned_activation_tree_is_said_gone(self):
        # row 1 (opus, sonnet): `git gc` pruned the dirty activation's tree — "git could not list", "asks again"
        (self.code / "extra.py").write_text("X = 1\n", encoding="utf-8")
        self.started(change=False)                      # the activation: extra.py not committed
        self.assertIs(self.base()["scopes"]["code"]["clean"], False)
        tree = self.base()["scopes"]["code"]["tree"]
        (self.code / "helper.py").write_text("Y = 2\n", encoding="utf-8")
        later = self.commit_code("the activation's work and more, before the fix")   # a tree of its own
        self.change_code()
        self.assertEqual(self.cli("red", "--base", f"code={later}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertRegex(self.check(), r"row 1: proved\b")
        self.g("gc", "--prune=now", "-q", cwd=self.code)
        gone = subprocess.run(["git", "cat-file", "-e", f"{tree}^{{tree}}"], cwd=self.code, capture_output=True)
        self.assertNotEqual(gone.returncode, 0)          # pruned
        out = self.check()
        self.assertRegex(out, r"row 1: proved on an unchecked base")
        self.assertIn("activation tree is gone", out)
        self.assertNotIn("asks again", out)
        self.assertNotIn("tasks red 1 --rows", out)
        self.assertNotIn("added later, each needs", out)


class Notes(Base_):
    def test_a_missing_now_tree_points_to_a_rerun(self):
        # row 2 (opus): `--check` re-reads the same record — only a re-run of the row records its tree
        from tasks import red
        rows = _rows(red, ["1"])
        key = red.row_key(rows[0])
        rec = {"row": "1", "run": "r1", "verdict": "proved", "row_sha": "x", "row_key": key, "contract": [key],
               "command": "true 1", "base": {"code": {"commit": "c9", "used": "commit"}}, "now_tree": {}}
        st = red.compute_state(rows, [rec], {}, View({"t0": "s", "c9": "b"}, last_none="tree"), None, 0,
                               {"code": {"commit": "c0", "tree": "t0", "clean": True}})
        out = "\n".join(red.render_state(st, "001", "head"))
        self.assertIn("names no tree", out)
        self.assertIn("a re-run of the row", out)
        self.assertNotIn("asks again", out)
        self.assertIn("refresh: tasks red 001 --rows 1", out)

    def test_the_now_tree_is_asked_only_when_used(self):
        # row 4 (opus): a design row on the start's code read unchecked because the now-tree was not known
        from tasks import red
        rec = {"base": {"code": {"commit": "c1", "used": "commit"}}, "now_tree": {"code": "tn"}}
        ref = {"code": {"commit": "c0", "tree": "t0", "clean": True}}
        self.assertEqual(red._base_check(rec, ref, View({"t0": "s", "c1": "s"}), False)[0], "ok")
        # a late row on the start's code still needs it: start only, or proved — not known
        self.assertEqual(red._base_check(rec, ref, View({"t0": "s", "c1": "s"}), True)[0], "unchecked")


class Hints(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))

    def test_a_removed_scope_is_not_hinted(self):
        # row 3 (opus, codex): `_plan_base` refuses `--base` for a scope no longer configured
        from tasks import red
        ref = {"": {"commit": "c0", "used": "commit"}, "gone": {"commit": "c1", "used": "commit"}}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_hint(red, Path(d), ref, _view({}, [""])), " --base c0")

    def test_a_start_dirty_only_in_docs_keeps_its_hint(self):
        # row 5 (codex): its commit has the same code, so a run there is on the start's code
        from tasks import red
        ref = {"code": {"commit": "c0", "tree": "t0", "used": "tree", "clean": False}}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_hint(red, Path(d), ref, _view({"t0": "s", "c0": "s"}, ["code"])), " --base code=c0")
            self.assertIsNone(_hint(red, Path(d), ref, _view({"t0": "s", "c0": "x"}, ["code"])))   # code differs


class Text(unittest.TestCase):
    def test_the_cli_doc_documents_part_6(self):
        # row 6
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        for word in ("pruned activation tree", "re-run of the row", "not `verify`'s"):
            self.assertIn(word, body, word)


if __name__ == "__main__":
    unittest.main()
