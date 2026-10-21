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
        self.assertRegex(self.check(), r"row 2: proved on the task's start only")

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

            def digest(self, scope, treeish):
                return {"t0": "d-start", "c2": "d-late", "tn": "d-now"}.get(treeish)

            def newer(self, scope, old, new):
                return self._newer
        rec = {"base": {"code": {"commit": "c2", "used": "commit"}}, "now_tree": {"code": "tn"}}
        unborn = {"code": {"commit": None, "tree": "t0", "used": "tree"}}
        self.assertTrue(red._own_base(rec, unborn, View(None)))
        born = {"code": {"commit": "c0", "tree": "t0", "used": "tree"}}
        self.assertFalse(red._own_base(rec, born, View(None)))
        self.assertTrue(red._own_base(rec, born, View(True)))
        self.assertFalse(red._own_base(rec, born, View(False)))


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
        with mock.patch.object(filelock, "task_lock", spy_lock), mock.patch.object(post_d6, "worktree_tree_why",
                                                                                    spy_tree):
            code = _in_process(self.d, self.env(), red.cmd_red, [])
        self.assertIn(code, (None, 0))
        self.assertTrue(under, "no tree was built")
        self.assertNotIn(True, under)


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
