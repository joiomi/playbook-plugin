#!/usr/bin/env python3
"""The red receipt, part 3 (PHASE-B.md item 9b; task 282's contract): what part 2's last review found it could still
claim falsely.

A row added after the code proves only on code newer than the task's activation (never the start again, never older
code); a side's every test summary is read, so a chained command cannot hide an all-skipped run, and exit 5 with no
test run is a skip; the run, the section and `--check` say the same; the section reads the config and builds its trees
outside the task lock; a spent budget is said; `--rows` ranges are capped; a `#` must be a number; a bare `tasks red`
or `tasks numbers` takes this session's task.

Each test names the row of task 282's contract it proves. Real temporary git projects from part 1's `Project`, the CLI
driven as `python3 -m tasks.cli`. Pure stdlib unittest. Run: python3 tests/test_red_receipt_part3.py
"""
import contextlib
import io
import subprocess
import sys
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from tests.test_red_receipt import Project, _in_process  # noqa: E402

G_CODE = "def f():\n    return 2\n\n\ndef g():\n    return 3\n"
G_TEST = ("import unittest\nimport src\n\n\nclass G(unittest.TestCase):\n"
          "    def test_g(self):\n        self.assertEqual(src.g(), 3)\n")
SKIP_TEST = ("import unittest\nimport src\n\n\nclass S(unittest.TestCase):\n"
             "    @unittest.skip('not written yet')\n    def test_later(self):\n        self.fail()\n")
ROW1 = ("1", "normal", "f returns 2", "`tests.test_new`")
ROW2 = ("2", "edge", "f returns 2 by its own test", "`tests.test_new.N.test_two`")


class Base(Project):
    def started(self, rows=None, change=True):
        self.new_task()
        if change:
            self.change_code()
        self.set_contract(rows or [ROW1])

    def check(self, task="1"):
        r = self.cli("red", task, "--check")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout

    def commit_code(self, msg):
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", msg, cwd=self.code)
        return self.head(self.code)


class Late(Base):
    def test_an_older_base_is_no_proof(self):
        # row 1 (opus, impl round 2): `--base` on a commit older than the task's start makes the digest differ — the
        # red side then ran on code from before the task began
        older = self.head(self.code)
        (self.code / "NOTES.md").write_text("a commit before the task\n", encoding="utf-8")
        self.commit_code("before the task")
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        r = self.cli("red", "--rows", "2", "--base", f"code={older}")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertRegex(self.check(), r"row 2: proved on older code")

    def test_the_reference_is_the_activation_base(self):
        # row 2 (codex, impl round 2): activation A, a first run on `--base B`, a late row run on A — still the start
        self.started()
        b = self.commit_code("the change, committed")
        self.assertEqual(self.cli("red", "--base", f"code={b}").returncode, 0)
        self.set_contract([ROW1, ROW2])
        r = self.cli("red", "--rows", "2")                         # on base.json: the activation base
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertRegex(self.check(), r"row 2: proved on the task's start only")

    def test_a_commit_between_the_activation_and_the_first_runs_base_proves(self):
        # row 2, its other side: the reference is the activation, so a commit after it — before the first run's base —
        # is the code before a late row's fix, and proves (the first run's base as the reference would refuse it)
        self.started()
        (self.code / "src.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        between = self.commit_code("part of the change")
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        (self.code / "tests" / "test_g.py").write_text(G_TEST, encoding="utf-8")
        later = self.commit_code("the rest")
        self.assertEqual(self.cli("red", "--base", f"code={later}").returncode, 0)
        self.set_contract([ROW1, ("2", "hostile", "g returns 3", "`tests.test_g`")])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={between}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertRegex(self.check(), r"row 2: proved \(run [0-9a-f]+\) — added later")

    def test_a_newer_base_proves(self):
        # row 3: the code before the late row's fix, committed after the activation — proved (already true before:
        # part 2 proves it too; this row keeps it under the stricter rule)
        self.started()
        before_fix = self.commit_code("the change, committed")
        self.assertEqual(self.cli("red").returncode, 0)
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        (self.code / "tests" / "test_g.py").write_text(G_TEST, encoding="utf-8")
        self.set_contract([ROW1, ("2", "hostile", "g returns 3 (a judge's row)", "`tests.test_g`")])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={before_fix}").returncode, 0)
        self.assertRegex(self.check(), r"row 2: proved \(run [0-9a-f]+\) — added later")

    def test_an_unborn_reference_and_an_unknown_ancestry(self):
        # row 4: a reference with no commit takes any commit as newer; an ancestry git cannot answer is no proof
        from tasks import red

        class View:
            def __init__(self, newer):
                self._newer = newer

            def digest(self, scope, treeish, **_k):
                return {"t0": "d-start", "c2": "d-late", "tn": "d-now"}.get(treeish)

            def newer(self, scope, old, new):
                return self._newer

            def older(self, scope, old, new):         # task 283's base rule also asks the other way
                return None

            def entries(self, scope, treeish, **_k):  # task 284: a tree-naming reference is listed (all empty here)
                return {}

            def has(self, scope, treeish):            # task 285: every stub tree is there
                return True
        rec = {"base": {"code": {"commit": "c2", "used": "commit"}}, "now_tree": {"code": "tn"}}
        unborn = {"code": {"commit": None, "tree": "t0", "used": "tree"}}
        self.assertTrue(red._own_base(rec, unborn, View(None)))
        born = {"code": {"commit": "c0", "tree": "t0", "used": "tree"}}
        self.assertFalse(red._own_base(rec, born, View(None)))
        self.assertTrue(red._own_base(rec, born, View(True)))
        self.assertFalse(red._own_base(rec, born, View(False)))

    def test_a_dirty_activation_and_its_head(self):
        # row 21 (agy, opus, sonnet): base.json's tree held uncommitted work; its HEAD is the activation minus it
        start = self.head(self.code)
        (self.code / "extra.py").write_text("X = 1\n", encoding="utf-8")     # uncommitted at the activation
        self.started()
        self.assertFalse(self.base()["scopes"]["code"]["clean"])
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={start}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertRegex(self.check(), r"row 2: proved on older code")      # task 283: named exactly
        self.assertEqual(self.cli("red", "--rows", "2").returncode, 0)          # on the recorded tree itself
        self.assertRegex(self.check(), r"row 2: proved on the task's start only")

    def test_the_activations_work_committed_is_still_the_start(self):
        # row 21 (sonnet): the uncommitted work of the activation, committed right after — that commit IS the start
        (self.code / "extra.py").write_text("X = 1\n", encoding="utf-8")
        self.started(change=False)
        self.assertFalse(self.base()["scopes"]["code"]["clean"])
        c1 = self.commit_code("the activation's own work, committed")
        self.change_code()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={c1}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertRegex(self.check(), r"row 2: proved on the task's start only")

    def test_a_rewritten_history_is_said(self):
        # row 28 (sonnet m): the activation's commit amended away — it is no ancestor of anything now
        self.started(change=False)
        self.g("commit", "-q", "--amend", "-m", "code start, amended", cwd=self.code)
        self.change_code()
        b = self.commit_code("the change")
        self.assertEqual(self.cli("red").returncode, 0)
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        (self.code / "tests" / "test_g.py").write_text(G_TEST, encoding="utf-8")
        self.set_contract([ROW1, ("2", "hostile", "g returns 3", "`tests.test_g`")])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={b}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        out = self.check()
        self.assertRegex(out, r"row 2: proved on unrelated code")
        self.assertIn("is not an ancestor of HEAD", out)          # said as it is (task 282's impl round 1)
        self.assertNotIn("each needs its own base", out)

    def test_a_late_row_without_base_json_still_proves(self):
        # row 29 (must-not): 281's case — no base.json, a first run on `--base` its start, a late row on a newer commit
        start = self.head(self.code)
        self.started()
        (self.task_dir / "base.json").unlink()
        b = self.commit_code("the change")
        self.assertEqual(self.cli("red", "--base", f"code={start}").returncode, 0)
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        (self.code / "tests" / "test_g.py").write_text(G_TEST, encoding="utf-8")
        self.set_contract([ROW1, ("2", "hostile", "g returns 3", "`tests.test_g`")])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={b}").returncode, 0)
        self.assertRegex(self.check(), r"row 2: proved \(run [0-9a-f]+\) — added later")


class Anchor(Base):
    def test_a_first_run_after_the_code_is_said(self):
        # row 5 (opus, impl round 2): the first run found non-test files changed since its base — rows added before it
        # cannot be told from rows written at design
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        out = self.check()
        self.assertIn("the first run came after the code", out)
        self.assertRegex(out, r"added later not known")

    def test_a_first_run_before_the_code_says_nothing(self):
        # row 6 (must-not): only the tests written when the first run ran — no note, then or after the code
        self.started(change=False)
        (self.code / "tests" / "test_new.py").write_text(
            "import unittest\nimport src\n\n\nclass N(unittest.TestCase):\n"
            "    def test_two(self):\n        self.assertEqual(src.f(), 2)\n", encoding="utf-8")
        self.assertEqual(self.cli("red").returncode, 0)
        self.assertNotIn("the first run came after the code", self.check())
        (self.code / "src.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        self.assertNotIn("the first run came after the code", self.check())

    def test_a_docs_only_change_is_no_code(self):
        # row 22 (opus, sonnet): MIND_MAP, a root `*.md` or `docs/` changing is no code — no note, no base needed
        self.started(change=False)
        (self.code / "tests" / "test_new.py").write_text(
            "import unittest\nimport src\n\n\nclass N(unittest.TestCase):\n"
            "    def test_two(self):\n        self.assertEqual(src.f(), 2)\n", encoding="utf-8")
        (self.code / "README.md").write_text("what the code does\n", encoding="utf-8")
        (self.d / "NOTES.md").write_text("a root doc\n", encoding="utf-8")
        self.assertEqual(self.cli("red").returncode, 0)
        self.assertNotIn("the first run came after the code", self.check())
        (self.code / "src.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        b = self.commit_code("the change")
        (self.d / "MIND_MAP.md").write_text("# the map\n", encoding="utf-8")          # the project: docs only
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        (self.code / "tests" / "test_g.py").write_text(G_TEST, encoding="utf-8")
        self.set_contract([ROW1, ("2", "hostile", "g returns 3", "`tests.test_g`")])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={b}").returncode, 0)
        self.assertRegex(self.check(), r"row 2: proved \(run [0-9a-f]+\) — added later")


class Summaries(Base):
    def test_any_all_skipped_summary_makes_the_side_skipped(self):
        # row 7 (codex, sonnet, impl round 2): `unittest target && unittest smoke` — the target skipped, smoke passed
        from tasks import red
        self.assertEqual(red._side_status(0, b"Ran 1 test in 0.0s\n\nOK (skipped=1)\nRan 4 tests in 0.1s\n\nOK\n")[0],
                         "skipped")
        self.started()
        (self.code / "tests" / "test_skip.py").write_text(SKIP_TEST, encoding="utf-8")
        cmd = "cd code && python3 -m unittest tests.test_skip && python3 -m unittest tests.test_old"
        self.set_contract([("1", "normal", "a chained command", f"`{cmd}`")])
        self.assertEqual(self.cli("red").returncode, 0)
        rec = self.receipt_lines()[-1]
        self.assertEqual(rec["now"]["status"], "skipped")
        self.assertEqual(rec["verdict"], "skipped")

    def test_skips_add_up(self):
        # row 8
        from tasks import red
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nOK (skipped=1)\nRan 2 tests in 0.0s\n\n"
                                             b"OK (skipped=1)\n"), ("green", "unittest", 2))
        self.assertEqual(red._side_status(0, b"Ran 3 tests in 0.0s\n\nOK (skipped=1)\n"), ("green", "unittest", 1))
        self.assertEqual(red._side_status(0, b"== 2 passed, 1 skipped in 0.1s ==\n"), ("green", "pytest", 1))

    def test_exit_5_without_tests_is_skipped(self):
        # row 9 (opus, impl round 2): exit 5 is pytest's "no tests collected" and Python 3.12 unittest's "NO TESTS RAN"
        from tasks import red
        self.assertEqual(red._side_status(5, b"collected 0 items\n\n=== no tests ran in 0.01s ===\n")[0], "skipped")
        self.assertEqual(red._side_status(5, b"\n----\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n")[0], "skipped")
        self.assertEqual(red._side_status(5, b"make: *** [all] Error 5\n")[0], "red")
        self.assertEqual(red._side_status(1, b"=== no tests ran in 0.01s ===\n")[0], "red")

    def test_a_red_now_side_is_never_skipped(self):
        # row 18 (must-not): a failure now is never read as a skip, whatever the base did
        from tasks import red
        self.assertEqual(red.verdict("skipped", "red"), "broken")
        self.assertEqual(red.verdict("green", "red"), "regressed")
        self.assertEqual(red._side_status(1, b"Ran 2 tests in 0.0s\n\nFAILED (failures=1, skipped=1)\n")[0], "red")
        self.assertEqual(red._side_status(1, b"Ran 1 test in 0.0s\n\nOK (skipped=1)\nRan 1 test in 0.0s\n\n"
                                             b"FAILED (failures=1)\n")[0], "red")

    def test_a_hidden_failed_summary_is_red(self):
        # row 23 (opus, sonnet): `a ; b` — a's FAILED summary, b's OK, exit 0
        from tasks import red
        self.assertEqual(red._side_status(0, b"Ran 1 test in 0.0s\n\nFAILED (failures=1)\nRan 1 test in 0.0s\n\nOK\n")[0],
                         "red")
        self.assertEqual(red._side_status(0, b"=== 1 failed in 0.1s ===\n=== 1 passed in 0.1s ===\n")[0], "red")
        self.started()
        (self.code / "tests" / "test_fail.py").write_text(
            "import unittest\n\n\nclass F(unittest.TestCase):\n    def test_no(self):\n        self.fail()\n",
            encoding="utf-8")
        cmd = "cd code && python3 -m unittest tests.test_fail ; python3 -m unittest tests.test_old"
        self.set_contract([("1", "normal", "a failure hidden by a later pass", f"`{cmd}`")])
        self.assertEqual(self.cli("red").returncode, 0)
        rec = self.receipt_lines()[-1]
        self.assertEqual(rec["now"]["exit"], 0)
        self.assertEqual(rec["now"]["status"], "red")

    def test_each_summary_ends_before_the_next(self):
        # row 24 (sonnet): `NO TESTS RAN` has no OK line — it must not take the next summary's
        from tasks import red
        out = b"Ran 0 tests in 0.0s\n\nNO TESTS RAN\nRan 5 tests in 0.1s\n\nOK (skipped=1)\n"
        self.assertEqual(red._side_status(0, out), ("skipped", "unittest", 1))


class Section(Base):
    def test_the_run_prints_the_state(self):
        # row 10 (codex, impl round 2): the run's closing lines are the state's — a late row on the start
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        r = self.cli("red", "--rows", "2")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertRegex(r.stdout, r"row 2: proved on the task's start only")
        self.assertNotRegex(r.stdout, r"row\(s\) run: 1 proved")

    def test_the_section_reads_the_config_at_its_write(self):
        # row 11 (codex, impl round 2): `red_run` changed while the run ran — the section says stale, as --check does
        from unittest import mock
        from tasks import red
        self.started()
        real, done = red._side_run, []

        def change_the_runner(*a, **k):
            out = real(*a, **k)
            if not done:
                done.append(1)
                self.config(red_run="cd code && python3 -m unittest -q {test}")
            return out
        with mock.patch.object(red, "_side_run", change_the_runner):
            code = _in_process(self.d, self.env(), red.cmd_red, [])
        self.assertIn(code, (None, 0))
        self.assertRegex(self.section(), r"(?m)^\| 1 \| normal \|.*stale — the command changed")
        self.assertRegex(self.check(), r"row 1: stale — the command changed")

    def test_an_unreadable_receipt_at_the_section_is_a_note(self):
        # row 12 (opus, impl round 2): an OSError while the section is written — a note and exit 1, no traceback
        from unittest import mock
        from tasks import red
        self.started()

        def unreadable(*_a, **_k):
            raise OSError("the receipt is gone")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(red, "_read_records", unreadable), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = _in_process(self.d, self.env(), red.cmd_red, [])
        self.assertEqual(code, 1, out.getvalue() + err.getvalue())
        self.assertIn("the receipt is gone", out.getvalue() + err.getvalue())
        self.assertEqual(len(self.receipt_lines()), 1)               # the row's record was written before

    def test_trees_are_built_outside_the_lock(self):
        # row 13 (sonnet, impl round 2): `git add -A` per scope while the task lock is held made other lock takers wait
        from unittest import mock
        from tasks import filelock, post_d6, red
        self.started()
        held, under = [], []
        real_lock, real_tree = filelock.task_lock, post_d6.worktree_tree_why

        @contextlib.contextmanager
        def spy_lock(*a, **k):
            with real_lock(*a, **k):
                held.append(1)
                try:
                    yield
                finally:
                    held.pop()

        def spy_tree(*a, **k):
            under.append(bool(held))
            return real_tree(*a, **k)
        real_git = red._git

        def spy_git(*a, **k):
            under.append(bool(held))
            return real_git(*a, **k)
        with mock.patch.object(filelock, "task_lock", spy_lock), mock.patch.object(post_d6, "worktree_tree_why",
                                                                                    spy_tree), \
                mock.patch.object(red, "_git", spy_git):
            code = _in_process(self.d, self.env(), red.cmd_red, [])
        self.assertIn(code, (None, 0))
        self.assertTrue(under, "no tree was built")
        self.assertNotIn(True, under)
        self.assertNotIn("was not compared", self.section())                # worked out before the lock, not skipped

    def test_the_run_never_prints_a_raw_proved(self):
        # row 25 (opus, agy, sonnet): a row's line prints its sides, never a verdict word; the count is the state's
        import re
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        r = self.cli("red", "--rows", "2")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertRegex(r.stdout, r"row 2 \(edge\): red → green")
        self.assertNotRegex(r.stdout, r"(?m)^  row 2 \(edge\):[^\n]*\bproved\b")
        self.assertNotIn("proved (red on the base", r.stdout)
        self.assertIsNotNone(re.search(r"row 2: proved on the task's start only", r.stdout))


    def test_a_frozen_view_runs_no_git(self):
        # row 13 (opus, sonnet): under the lock the view answers only from what it already worked out
        from unittest import mock
        from tasks import red
        self.started()
        view = red._Trees({"code": self.code}, [":(exclude).agent"], {})
        head = self.head(self.code)
        self.assertIsNotNone(view.digest("code", head))                        # worked out before
        view.frozen = True
        with mock.patch.object(red, "_git", side_effect=AssertionError("git ran under the lock")):
            self.assertIsNotNone(view.digest("code", head))                    # from the cache
            self.assertIsNone(view.digest("code", head, code=True))            # not worked out: not known
            self.assertIsNone(view.newer("code", head + "x", head))
        self.assertTrue(view.missed)


class Budget(Base):
    def test_a_spent_budget_is_said(self):
        # row 14 (opus, impl round 2): past the budget a late row reads start-only — the state must say why
        from tasks import red
        from tasks.core import load_config, read_task_text
        self.started()
        before_fix = self.commit_code("the change, committed")
        self.assertEqual(self.cli("red").returncode, 0)
        (self.code / "src.py").write_text(G_CODE, encoding="utf-8")
        (self.code / "tests" / "test_g.py").write_text(G_TEST, encoding="utf-8")
        self.set_contract([ROW1, ("2", "hostile", "g returns 3", "`tests.test_g`")])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={before_fix}").returncode, 0)
        rows, _ = red.parse_contract(read_task_text(self.task_md))
        st = _in_process(self.d, self.env(), red.state_of, self.d, self.task_dir, rows, load_config(self.d),
                         time.monotonic() - 1, False)
        self.assertIn("time budget ran out", st["unchecked"])


    def test_the_section_says_what_was_not_checked(self):
        # row 14 (agy m): the reason a check was skipped is in `## Red receipt` too, not only at `--check`
        from tasks import red
        st = red.compute_state([], [], {}, None, None)
        st["unchecked"] = "the time budget ran out"
        self.assertIn("the time budget ran out", red._section(st, "contained"))


class Rows(Base):
    def test_a_huge_range_is_refused(self):
        # row 15 (sonnet, impl round 2): a typo's range is refused before it is expanded
        self.started()
        t0 = time.monotonic()
        r = self.cli("red", "--rows", "1-3000000")
        self.assertEqual(r.returncode, 2, r.stderr[:300])
        self.assertIn("range", r.stderr)
        self.assertLess(len(r.stderr), 2000)
        self.assertLess(time.monotonic() - t0, 20)

    def test_a_non_digit_label_is_refused(self):
        # row 16 (agy, impl round 2): `--rows` and the refresh line can only name a number
        self.started(rows=[("1a", "normal", "f returns 2", "`tests.test_new`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 1)
        self.assertIn("row 1a is not a number", r.stderr)
        self.assertFalse((self.task_dir / "red-receipt.jsonl").exists())
        self.assertIn("row 1a is not a number", self.check())

    def test_row_numbers_are_ascii_and_bounded(self):
        # row 27 (agy, opus, sonnet): the total is capped, only ASCII digits, no huge number — and never a traceback
        self.started()
        for bad in ("1-9000,9001-18000", "²", "1-" + "9" * 5000, "١"):
            r = self.cli("red", "--rows", bad)
            self.assertEqual(r.returncode, 2, (bad[:20], r.stderr[:300]))
            self.assertNotIn("Traceback", r.stderr)
            self.assertLess(len(r.stderr), 2000)


class Default(Base):
    def test_the_sessions_task_is_the_default(self):
        # row 17 (281's parked item): a bare `tasks red` / `tasks numbers` took the FIRST open task with an unticked
        # gate — here task 1, never activated — not this session's task 2, whose gates are all ticked
        self.assertEqual(self.cli("new", "feature", "one", "the first task").returncode, 0)
        self.assertEqual(self.cli("new", "feature", "two", "the session's task").returncode, 0)
        self.assertEqual(self.cli("work", "2").returncode, 0)
        self.task_dir = next((self.d / ".agent" / "tasks").glob("002-*"))
        self.task_md = self.task_dir / "task.md"
        self.change_code()
        self.set_contract([ROW1])
        self.task_md.write_text(self.task_md.read_text(encoding="utf-8").replace("- [ ] ", "- [x] "),
                                encoding="utf-8")
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("task 002-two", r.stdout)
        self.assertTrue((self.task_dir / "red-receipt.jsonl").is_file())
        r = self.cli("numbers")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("Task 002", r.stdout)

    def test_no_session_task_is_a_refusal(self):
        # row 26 (opus, sonnet): with no session task a bare `tasks red` / `tasks numbers` refuses — never a guess
        self.started()
        sessions = self.d / ".agent" / "sessions"
        pointer = sessions / "pid-red-probe" / "current_state"
        self.assertTrue(pointer.is_file())

        def refused(why, env=None):
            for args in (("red",), ("numbers",)):
                r = subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.d, env=env or self.env(),
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 2, (why, args, r.stdout[-300:], r.stderr[-300:]))
                self.assertIn("name the task", r.stderr, (why, args))
        text = self.task_md.read_text(encoding="utf-8")
        for status in ("done", "blocked"):
            self.task_md.write_text(text.replace("## Status\nin_progress\n", f"## Status\n{status}\n", 1),
                                    encoding="utf-8")
            refused(f"a {status} task")
        self.task_md.write_text(text, encoding="utf-8")
        pointer.write_text("009\n", encoding="utf-8")
        refused("a missing task")
        pointer.unlink()
        refused("no pointer")
        env = {k: v for k, v in self.env().items() if k != "PLAYBOOK_SESSION_ID"}
        before = sorted(p.name for p in sessions.iterdir())
        refused("no session id", env)
        self.assertFalse(set(p.name for p in sessions.iterdir()) - set(before))   # nothing created (the GC may remove)


class RoundOne(Base):
    """Task 282's impl round 1, each red on nested c1f9693 before its fix (rows 32-41)."""

    def test_a_failure_beside_a_return_of_5_is_red(self):
        # row 32 (agy, opus): `a ; b` — a failed, b collected nothing and returned 5
        from tasks import red
        self.assertEqual(red._side_status(5, b"Ran 1 test in 0.0s\n\nFAILED (failures=1)\n"
                                             b"=== no tests ran in 0.01s ===\n")[0], "red")
        self.assertEqual(red._side_status(5, b"=== 1 failed in 0.1s ===\n\nRan 0 tests in 0.0s\n\nNO TESTS RAN\n")[0],
                         "red")

    def test_an_empty_pytest_run_in_a_chain_is_skipped(self):
        # row 33 (codex): `pytest target; pytest smoke` — the target collected nothing, smoke passed, return 0
        from tasks import red
        self.assertEqual(red._side_status(0, b"=== no tests ran in 0.01s ===\n=== 3 passed in 0.1s ===\n")[0],
                         "skipped")

    def test_a_unittest_end_stops_at_a_pytest_summary(self):
        # row 34 (agy): an unclosed unittest summary cannot take an `OK` that comes after a pytest one
        from tasks import red
        out = b"Ran 0 tests in 0.0s\n\nNO TESTS RAN\n=== 2 passed in 0.1s ===\nOK (skipped=7)\n"
        self.assertEqual(red._side_status(0, out), ("skipped", "pytest+unittest", 0))

    def test_an_older_base_in_an_unchanged_scope_is_no_proof(self):
        # row 35 (sonnet): the task changed no code in `code`; a late row run there on an OLDER commit is red only
        # because that code is older — already true at the start, not a proof
        (self.code / "src.py").write_text("def f():\n    return 0\n", encoding="utf-8")
        older = self.commit_code("an older f")
        (self.code / "src.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        self.commit_code("f as the task finds it")
        self.started(rows=[("1", "must-not", "the old test still holds", "`tests.test_old`")], change=False)
        (self.code / "tests" / "test_keep.py").write_text(
            "import unittest\nimport src\n\n\nclass K(unittest.TestCase):\n"
            "    def test_one(self):\n        self.assertEqual(src.f(), 1)\n", encoding="utf-8")
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([("1", "must-not", "the old test still holds", "`tests.test_old`"),
                           ("2", "edge", "f stays 1 (a judge's row)", "`tests.test_keep`")])
        self.assertEqual(self.cli("red", "--rows", "2", "--base", f"code={older}").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")
        self.assertRegex(self.check(), r"row 2: proved on older code")

    def test_a_first_run_on_a_post_code_base_is_after_the_code(self):
        # row 36 (codex): activation A, the code committed as B, a first run on `--base B` — after the code
        self.started()
        b = self.commit_code("the change")
        self.assertEqual(self.cli("red", "--base", f"code={b}").returncode, 0)
        self.assertIn("the first run came after the code", self.check())

    def test_a_config_changed_during_preparation_is_read(self):
        # row 37 (codex): `red_run` changes after the section's trees were prepared — the lock re-reads it
        from unittest import mock
        from tasks import red
        self.started()
        real, done = red._prepare, []

        def prepare_then_change(*a, **k):
            out = real(*a, **k)
            if not done:
                done.append(1)
                self.config(red_run="cd code && python3 -m unittest -q {test}")
            return out
        with mock.patch.object(red, "_prepare", prepare_then_change):
            code = _in_process(self.d, self.env(), red.cmd_red, [])
        self.assertIn(code, (None, 0))
        self.assertRegex(self.section(), r"(?m)^\| 1 \| normal \|.*stale — the command changed")

    def test_red_tests_changed_during_preparation_is_read(self):
        # row 37, the trees too: `red_tests` changes while the trees are prepared — they are prepared once more, so the
        # section's tests digest is the new one's (stale, as --check says), not the old one's
        from unittest import mock
        from tasks import red
        self.started()
        real, done = red._prepare, []

        def prepare_then_change(*a, **k):
            out = real(*a, **k)
            if not done:
                done.append(1)
                self.config(red_tests=["tests/test_old.py"])
            return out
        with mock.patch.object(red, "_prepare", prepare_then_change):
            code = _in_process(self.d, self.env(), red.cmd_red, [])
        self.assertIn(code, (None, 0))
        self.assertRegex(self.check(), r"row 1: stale — the test files changed")
        self.assertRegex(self.section(), r"(?m)^\| 1 \| normal \|.*stale — the test files changed")

    def test_the_run_prints_every_tests_sides(self):
        # row 38 (opus, agy): a row of two tests shows each one's sides; the state's header line is printed
        self.started(rows=[("1", "normal", "two tests", "`tests.test_new` `tests.test_old`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertRegex(r.stdout, r"row 1 \(normal\): 1: red → green; 2: green → green")
        self.assertIn("the state of the 1 row(s) run", r.stdout)

    def test_a_reference_off_head_is_said_as_it_is(self):
        # row 39 (opus, sonnet): an amended or switched branch is said as "not an ancestor of HEAD"; a scope the task
        # did not change being so does not withhold the hint
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.g("commit", "-q", "--amend", "-m", "outer start, amended")              # the project, no code of the task
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2").returncode, 0)              # on the start: start-only
        out = self.check()
        self.assertIn("is not an ancestor of HEAD", out)
        self.assertIn("the project", out)
        self.assertNotIn("history rewritten", out)
        self.assertIn("each needs its own base", out)

    def test_numbers_says_what_was_not_checked(self):
        # row 40 (codex m): numbers' line carries the reason a check was skipped
        from tasks import red
        self.started()
        self.assertEqual(self.cli("red").returncode, 0)
        self.set_contract([ROW1, ROW2])
        self.assertEqual(self.cli("red", "--rows", "2").returncode, 0)
        line = _in_process(self.d, self.env(), red.numbers_line, self.d, self.task_dir, 0.0)
        self.assertIn("time budget ran out", line)

    def test_the_docs_say_the_strict_rule(self):
        # row 41 (opus m, sonnet m)
        from tasks import red
        self.assertNotIn("a `--base` its run named", red.__doc__)
        self.assertNotIn("proves only on a `--base`", red.__doc__)
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        self.assertIn("runs on its HEAD", body)


class Text(unittest.TestCase):
    def test_the_guide_says_when_to_run_red_first(self):
        # row 19
        sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))
        from tasks import template
        lines = [ln for ln in template.contract_note().splitlines() if "`tasks red`" in ln]
        self.assertTrue(any("before the code" in ln and "first run" in ln for ln in lines), lines)

    def test_the_cli_doc_documents_part_3(self):
        # row 20
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        for word in ("ancestor", "every summary", "NO TESTS RAN", "no tests ran", "came after the code",
                     "this session's task", "activation base"):
            self.assertIn(word, body, word)


if __name__ == "__main__":
    unittest.main()
