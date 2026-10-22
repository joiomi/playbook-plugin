#!/usr/bin/env python3
"""The red receipt, part 6 (PHASE-B.md item 9b; task 285's contract): what part 5's last review left.

A start gone from the repository (a dirty activation's tree `git gc` pruned, a clean one's commit gone) is said as gone —
no "asks again", no refresh; each note says what helps (`--check` again, a re-run of the row, or nothing); without
base.json no hint names a removed scope, and a start dirty only in docs or tests keeps its hint, worked out before the
task lock; a design row on the start's code needs no now-tree.

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
from tests.test_red_receipt_part5 import ROW1, ROW2, Base_, View, _rows  # noqa: E402


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


class GoneView(View):
    """A stub view where the named treeishes are gone and ancestry is not known."""

    def __init__(self, digests, gone):
        super().__init__(digests, newer=None)
        self.gone = gone

    def has(self, scope, treeish):
        return treeish not in self.gone


class CauseView(View):
    """A stub view whose misses carry their own cause, per treeish."""

    def __init__(self, digests, causes):
        super().__init__(digests)
        self.causes = causes

    def digest(self, scope, treeish, **_k):
        self.last_none = self.causes.get(treeish, "")
        return self.digests.get(treeish)


def _rec(red, rows, n, base, now):
    keys = [red.row_key(r) for r in rows]
    return {"row": str(n), "run": f"r{n}", "verdict": "proved", "row_sha": "x", "row_key": keys[n - 1],
            "contract": keys, "command": f"true {n}", "base": {"code": {"commit": base, "used": "commit"}},
            "now_tree": now}


class Gone(Base_):
    def _gone(self, ref_tree):
        r = subprocess.run(["git", "cat-file", "-e", f"{ref_tree}^{{tree}}"], cwd=self.code, capture_output=True)
        self.assertNotEqual(r.returncode, 0)            # pruned
        out = self.check()
        self.assertRegex(out, r"row 1: proved on an unchecked base")
        self.assertIn("start is gone", out)
        self.assertNotIn("asks again", out)
        self.assertNotIn("tasks red 1 --rows", out)
        self.assertNotIn("added later, each needs", out)
        self.assertRegex(out, r"refresh: rows 2 cannot be refreshed — code's start is gone")    # row 2 never ran

    def test_a_pruned_activation_tree_is_said_gone(self):
        # row 1 (opus, sonnet): `git gc` pruned the dirty activation's tree — "git could not list", "asks again"
        (self.code / "extra.py").write_text("X = 1\n", encoding="utf-8")
        self.started([ROW1, ROW2], change=False)        # the activation: extra.py not committed
        self.assertIs(self.base()["scopes"]["code"]["clean"], False)
        tree = self.base()["scopes"]["code"]["tree"]
        (self.code / "helper.py").write_text("Y = 2\n", encoding="utf-8")
        later = self.commit_code("the activation's work and more, before the fix")   # a tree of its own
        self.change_code()
        self.assertEqual(self.cli("red", "--rows", "1", "--base", f"code={later}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertRegex(self.check(), r"row 1: proved\b")
        self.g("gc", "--prune=now", "-q", cwd=self.code)
        self._gone(tree)
        from tasks import red                           # gone only when git says so (agy)
        self.assertIs(red._tree_state(self.code, tree), False)
        self.assertIs(red._tree_state(self.code, later), True)
        self.assertIsNone(red._tree_state(self.d / "nowhere", tree))

    def test_a_clean_start_whose_commit_is_gone(self):
        # row 1 (opus): a clean start's tree is its commit's — a reset, reflog expiry and `git gc` take both
        first = self.head(self.code)
        (self.code / "extra.py").write_text("X = 1\n", encoding="utf-8")
        self.commit_code("the start")
        self.started([ROW1, ROW2], change=False)        # a clean activation on that commit
        self.assertIs(self.base()["scopes"]["code"]["clean"], True)
        tree = self.base()["scopes"]["code"]["tree"]
        (self.code / "helper.py").write_text("Y = 2\n", encoding="utf-8")
        later = self.commit_code("more code, before the fix")
        self.change_code()
        self.assertEqual(self.cli("red", "--rows", "1", "--base", f"code={later}").returncode, 0)
        self.assertRegex(self.check(), r"row 1: proved\b")
        self.g("reset", "-q", "--hard", first, cwd=self.code)
        self.g("reflog", "expire", "--expire=now", "--all", cwd=self.code)
        self.g("gc", "--prune=now", "-q", cwd=self.code)
        self.change_code()
        self._gone(tree)
        # the start's tree still listed but its commit gone: ancestry is not known — asked which one is gone
        from tasks import red
        rec = {"base": {"code": {"commit": "c1", "used": "commit"}}, "now_tree": {"code": "tn"}}
        ref = {"code": {"commit": "c0", "tree": "t0", "clean": True}}
        digests = {"t0": "s", "c1": "b", "tn": "n"}
        res, why, helps = red._base_check(rec, ref, GoneView(digests, {"c0"}), False)
        self.assertEqual((res, helps), ("unchecked", ""))
        self.assertIn("start is gone", why)
        res, why, helps = red._base_check(rec, ref, GoneView(digests, {"c1"}), False)     # the record's commit
        self.assertEqual((res, helps), ("unchecked", "rerun"))


class Notes(Base_):
    def test_a_missing_now_tree_points_to_a_rerun(self):
        # row 2 (opus): `--check` re-reads the same record — only a re-run of the row records its tree
        from tasks import red
        rows = _rows(red, ["1"])
        rec = _rec(red, rows, 1, "c9", {})
        st = red.compute_state(rows, [rec], {}, View({"t0": "s", "c9": "b"}, last_none="tree"), None, 0,
                               {"code": {"commit": "c0", "tree": "t0", "clean": True}})
        out = "\n".join(red.render_state(st, "001", "head"))
        self.assertIn("names no tree", out)
        self.assertIn("a re-run of the row", out)
        self.assertNotIn("asks again", out)
        self.assertIn("refresh: tasks red 001 --rows 1", out)

    def test_a_note_says_each_remedy(self):
        # row 2 (sonnet): rows with different causes — the note names what helps each, and both keep the refresh
        from tasks import red
        rows = _rows(red, ["1", "2"])
        recs = [_rec(red, rows, 1, "c9", {}), _rec(red, rows, 2, "c8", {"code": "tq"})]
        view = CauseView({"t0": "s", "c9": "b", "c8": "b2"}, {None: "tree", "tq": "frozen"})
        st = red.compute_state(rows, recs, {}, view, None, 0, {"code": {"commit": "c0", "tree": "t0", "clean": True}})
        out = "\n".join(red.render_state(st, "001", "head"))
        self.assertIn("`tasks red --check` asks again", out)
        self.assertIn("a re-run of the row", out)
        self.assertIn("refresh: tasks red 001 --rows 1,2", out)
        # a code root removed: a re-run writes only today's scopes, so it helps (sonnet; part 5 said nothing did)
        st = red.compute_state(rows[:1], recs[:1], {}, View({"t0": "s", "c9": "b"}, last_none="scope"), None, 0,
                               {"code": {"commit": "c0", "tree": "t0", "clean": True}})
        out = "\n".join(red.render_state(st, "001", "head"))
        self.assertIn("no longer a scope", out)
        self.assertIn("a re-run of the row", out)
        self.assertIn("refresh: tasks red 001 --rows 1", out)

    def test_the_now_tree_is_asked_only_when_used(self):
        # row 4 (opus): a design row on the start's code read unchecked because the now-tree was not known
        from tasks import red
        rec = {"base": {"code": {"commit": "c1", "used": "commit"}}, "now_tree": {"code": "tn"}}
        ref = {"code": {"commit": "c0", "tree": "t0", "clean": True}}
        self.assertEqual(red._base_check(rec, ref, View({"t0": "s", "c1": "s"}), False)[0], "ok")
        # a late row on the start's code still needs it: start only, or proved — not known
        self.assertEqual(red._base_check(rec, ref, View({"t0": "s", "c1": "s"}), True)[0], "unchecked")


class Hints(Base_):
    def _docs_dirty_start(self):
        """A code commit before the start, then a start whose only uncommitted file is docs; a run on base.json."""
        before = self.head(self.code)
        (self.code / "helper.py").write_text("Y = 2\n", encoding="utf-8")
        start = self.commit_code("the start's code")
        (self.code / "README.md").write_text("notes\n", encoding="utf-8")       # dirty only in docs
        self.started(change=False)
        self.assertIs(self.base()["scopes"]["code"]["clean"], False)
        self.change_code()
        self.assertEqual(self.cli("red").returncode, 0)
        (self.task_dir / "base.json").unlink()
        return before, start

    def test_a_removed_scope_is_not_hinted(self):
        # row 3 (opus, codex): `_plan_base` refuses `--base` for a scope no longer configured
        from tasks import red
        ref = {"": {"commit": "c0", "used": "commit"}, "gone": {"commit": "c1", "used": "commit"}}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_hint(red, Path(d), ref, _view({}, [""])), " --base c0")
            only = {"gone": {"commit": "c1", "used": "commit"}}                 # no scope left (sonnet, agy)
            self.assertIsNone(_hint(red, Path(d), only, _view({}, [""])))
            self.assertIn("no longer", red._base_hint(Path(d), only, _view({}, [""]))[1])

    def test_a_start_dirty_only_in_docs_keeps_its_hint(self):
        # row 5 (codex, opus): its commit has the same code, so a run there is on the start's code
        _before, start = self._docs_dirty_start()
        p = self.code / "tests" / "test_old.py"
        p.write_text(p.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        self.assertRegex(self.check(), r"refresh: tasks red \S+ --rows 1 [^\n]*--base code=" + start)
        from tasks import red
        ref = {"code": {"commit": "c0", "tree": "t0", "used": "tree", "clean": False}}
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_hint(red, Path(d), ref, _view({"t0": "s", "c0": "x"}, ["code"])))   # code differs
            self.assertIsNone(_hint(red, Path(d), ref, _view({"c0": "s"}, ["code"])))              # tree not known

    def test_a_runs_hint_is_worked_out_before_the_lock(self):
        # row 7 (sonnet, agy): the section is written under the lock from a frozen view — the hint's digests must be
        # asked before it, or the run prints a different refresh line than `--check`
        before, start = self._docs_dirty_start()
        r = self.cli("red", "--rows", "1", "--base", f"code={before}")    # older code: the row needs a refresh
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("proved on older code", r.stdout)
        self.assertRegex(r.stdout, r"refresh: tasks red \S+ --rows 1 [^\n]*--base code=" + start)
        self.assertNotIn("written while", r.stdout)


class Text(unittest.TestCase):
    def test_the_cli_doc_documents_part_6(self):
        # row 6
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        for word in ("pruned activation tree", "re-run of the row", "not `verify`'s"):
            self.assertIn(word, body, word)


if __name__ == "__main__":
    unittest.main()
