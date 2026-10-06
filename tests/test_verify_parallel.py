"""Task 113: the unittest step of scripts/verify runs one process per test module, several at a time.

The step defines green, so what is pinned here is the VERDICT, not the speed: every module on
discover's list is run once, a failure anywhere fails the step and is named in the detail, and
silence — a child that dies, times out, or is never started — is a failure too. One real
end-to-end run on a tiny temporary suite keeps the scripted fake from being the only witness.

Run: python3 -m unittest tests.test_verify_parallel
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ENV_LOG = "PLAYBOOK_VERIFY_FULL_LOG"
ENV_JOBS = "PLAYBOOK_VERIFY_JOBS"


def _load_verify():
    path = ROOT / "scripts" / "verify"
    loader = importlib.machinery.SourceFileLoader("verify_parallel_under_test", str(path))
    spec = importlib.util.spec_from_loader("verify_parallel_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


V = _load_verify()

OK_1 = "----------------------------------------------------------------------\nRan 1 test in 0.001s\n\nOK\n"


def _ok(n, extra=""):
    return f"{'-' * 70}\nRan {n} tests in 0.010s\n\nOK{extra}\n"


def _failed(n, mod, parts):
    return (f"{'=' * 70}\nFAIL: test_x ({mod}.T)\n{'-' * 70}\nTraceback (most recent call last):\n"
            f"AssertionError: boom in {mod}\n\n{'-' * 70}\nRan {n} tests in 0.010s\n\nFAILED ({parts})\n")


class _Suite(unittest.TestCase):
    """A temporary project root with an empty `tests/` holding named module files; `run` is
    replaced by a script keyed on the module a child was asked to run."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        (self.root / "tests").mkdir()
        self.calls = []
        self._lock = threading.Lock()

    def modules(self, *names, sizes=None):
        for i, n in enumerate(names):
            (self.root / "tests" / f"{n}.py").write_text("#" * ((sizes or {}).get(n, 10 + i)), encoding="utf-8")

    def fake(self, script, default=(0, OK_1)):
        def fake_run(cmd, cwd=None, timeout=900, **kw):
            mod = cmd[cmd.index("-p") + 1][:-3]
            with self._lock:
                self.calls.append({"mod": mod, "cmd": list(cmd), "cwd": cwd, "timeout": timeout, "kw": kw})
            got = script.get(mod, default)
            if isinstance(got, BaseException):
                raise got
            if callable(got):
                return got(cmd, timeout, kw)
            return got
        return mock.patch.object(V, "run", side_effect=fake_run)

    def suite(self, jobs=3, verbosity="-q", budget=None):
        kw = {"root": self.root}
        if budget is not None:
            kw["budget"] = budget
        return V.run_suite(jobs, verbosity, **kw)


class EveryModuleOnce(_Suite):
    def test_each_module_is_run_once_with_the_old_command_narrowed_by_a_pattern(self):
        self.modules("test_a", "test_b", "test_c", "testd")              # discover's pattern is test*.py
        (self.root / "tests" / "_helper.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "tests" / "fixtures").mkdir()
        (self.root / "tests" / "fixtures" / "test_data.py").write_text("", encoding="utf-8")   # no package: not discovered
        with self.fake({}):
            rc, out = self.suite(jobs=2)
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(c["mod"] for c in self.calls), ["test_a", "test_b", "test_c", "testd"])
        for c in self.calls:
            self.assertEqual(c["cmd"][1:6], ["-m", "unittest", "discover", "-s", "tests"])
            self.assertEqual(c["cmd"][-1], "-q")
            self.assertEqual(Path(c["cwd"]), self.root)
        self.assertIn("Ran 4 tests in ", out)
        self.assertEqual(V.ut_tail(out).split(" / ")[-1], "OK")

    def test_a_test_module_the_list_would_miss_fails_the_step_closed(self):
        self.modules("test_a")
        pkg = self.root / "tests" / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "test_nested.py").write_text("", encoding="utf-8")        # discover WOULD run this one
        with self.fake({}):
            with self.assertRaises(RuntimeError) as cm:
                self.suite()
        self.assertIn("pkg", str(cm.exception))
        self.assertEqual(self.calls, [])                                # nothing ran under a wrong list
        # impl review: discover ALSO runs TestCases defined in a package's own __init__.py —
        # a package with no test*.py inside is just as much outside the list
        os.remove(pkg / "test_nested.py")
        (pkg / "__init__.py").write_text("import unittest\n\n\nclass Hidden(unittest.TestCase):\n"
                                         "    def test_in_init(self):\n        self.fail('in __init__')\n",
                                         encoding="utf-8")
        with self.fake({}):
            with self.assertRaises(RuntimeError) as cm:
                self.suite()
        self.assertIn("pkg", str(cm.exception))
        self.assertEqual(self.calls, [])
        os.remove(pkg / "__init__.py")
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        with self.fake({}):
            with self.assertRaises(RuntimeError) as cm:
                self.suite()
        self.assertIn("__init__.py", str(cm.exception))

    def test_no_test_module_at_all_fails_the_step(self):
        # review run 3: an empty tests/ gave `Ran 0 tests` / OK — green for nothing
        with self.fake({}):
            rc, out = self.suite()
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])
        detail = "\n".join(V.ut_fails(out))
        self.assertIn("no test module", detail)
        self.assertEqual(out.strip().splitlines()[-1], "FAILED (errors=1)")

    def test_a_module_that_binds_load_tests_sends_the_step_back_to_the_single_run(self):
        # review run 4: `-p <module>.py` changes the pattern discover hands a load_tests hook, so a
        # hook can run different tests than in the single run. Such a module (or `import *`, or a
        # package below tests/) is not split: the step runs the old single command instead.
        self.modules("test_a", "test_b")
        for src in ("def load_tests(loader, tests, pattern):\n    return tests\n",
                    "from somewhere import load_tests\n", "import hooks as load_tests\n",
                    "import load_tests\n", "load_tests = make_hook()\n",
                    "from helpers import *\n"):
            (self.root / "tests" / "test_b.py").write_text(src, encoding="utf-8")
            with self.fake({}):
                with self.assertRaises(V.SingleRunNeeded) as cm:
                    self.suite()
            self.assertIn("test_b", str(cm.exception), src)
            self.assertEqual(self.calls, [])
        # text that only MENTIONS it is not a binding
        (self.root / "tests" / "test_b.py").write_text('"""load_tests = x"""\nX = "def load_tests"\n',
                                                        encoding="utf-8")
        with self.fake({}):
            rc, _ = self.suite()
        self.assertEqual(rc, 0)

    def test_the_step_falls_back_to_the_single_command_and_says_why(self):
        seen = []

        def fake_run(cmd, cwd=V.ROOT, timeout=900, **kw):
            seen.append(list(cmd))
            return 0, "Ran 7 tests in 1.0s\n\nOK\n"
        with mock.patch.dict(os.environ, {ENV_JOBS: "4"}), mock.patch.object(V, "run", side_effect=fake_run), \
                mock.patch.object(V, "run_suite", side_effect=V.SingleRunNeeded("tests/test_x.py binds load_tests")):
            status, detail = V._unittest()[:2]
        self.assertEqual(status, V.PASS)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("-p", seen[0])
        self.assertIn("single run", detail)
        self.assertIn("test_x.py binds load_tests", detail)

    def test_the_real_list_is_what_discover_finds(self):
        # the loader's own discovery, names only (no module is imported for this)
        import fnmatch
        names = sorted(p.stem for p in (ROOT / "tests").iterdir()
                       if p.is_file() and fnmatch.fnmatch(p.name, "test*.py"))
        self.assertEqual(V.suite_modules(ROOT), names)
        self.assertIn("test_verify_parallel", names)
        self.assertGreater(len(names), 100)


class TheVerdict(_Suite):
    def test_counts_are_added_up_in_unittests_own_shape(self):
        self.modules("test_a", "test_b", "test_c")
        script = {"test_a": (0, _ok(5, " (skipped=2)")),
                  "test_b": (0, _ok(7, " (skipped=1, expected failures=3)")),
                  "test_c": (0, _ok(1))}
        with self.fake(script):
            rc, out = self.suite()
        self.assertEqual(rc, 0)
        tail = out.strip().splitlines()
        self.assertTrue(tail[-3].startswith("Ran 13 tests in "), tail[-3:])
        self.assertEqual(tail[-1], "OK (skipped=3, expected failures=3)")
        self.assertTrue(V.ut_tail(out).startswith("Ran 13 tests in "), V.ut_tail(out))

    def test_one_failing_module_fails_the_step_and_its_block_reaches_the_detail(self):
        self.modules("test_a", "test_b", "test_c")
        script = {"test_b": (1, _failed(4, "test_b", "failures=1, skipped=1")), "test_a": (0, _ok(2, " (skipped=1)"))}
        with self.fake(script):
            rc, out = self.suite()
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip().splitlines()[-1], "FAILED (failures=1, skipped=2)")
        self.assertIn("Ran 7 tests in ", out)
        detail = "\n".join(V.ut_fails(out))
        self.assertIn("FAIL: test_x (test_b.T)", detail)
        self.assertIn("AssertionError: boom in test_b", detail)
        self.assertEqual(len(self.calls), 3)                            # the others still ran, as in a single run

    def test_silence_is_failure(self):
        self.modules("test_dies", "test_mute", "test_liar", "test_ok")
        script = {"test_dies": (-11, "Segmentation fault\n"),            # no verdict, non-zero
                  "test_mute": (0, "printed something, no verdict\n"),   # exit 0 without a verdict
                  "test_liar": (3, _ok(2))}                              # says OK, exits non-zero
        with self.fake(script):
            rc, out = self.suite()
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip().splitlines()[-1], "FAILED (errors=3)")
        detail = "\n".join(V.ut_fails(out))
        for mod in ("test_dies", "test_mute", "test_liar"):
            self.assertIn(f"ERROR: {mod} ", detail)
        self.assertIn("Segmentation fault", detail)                      # the child's own last words
        self.assertNotIn("ERROR: test_ok", detail)

    def test_a_module_that_runs_no_tests_fails_the_step_on_every_python(self):
        # Review run 2: an "empty module" exception (Python 3.12 exits 5 for one) let a child that
        # printed the right words and then died with 5 pass. There is no way to tell the two apart
        # from outside, and a test module with nothing to run is itself a defect — so it fails, the
        # same on 3.10 (exit 0, "OK") and 3.12 (exit 5, "NO TESTS RAN"). The single run would not
        # notice an empty module; this is the one place the step is STRICTER than it.
        self.modules("test_empty", "test_ok")
        for rc_empty, text in ((0, f"{'-' * 70}\nRan 0 tests in 0.000s\n\nOK\n"),
                               (5, f"{'-' * 70}\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n")):
            self.calls.clear()
            with self.fake({"test_empty": (rc_empty, text)}):
                rc, out = self.suite()
            self.assertEqual(rc, 1, (rc_empty, out))
            detail = "\n".join(V.ut_fails(out))
            self.assertIn("ERROR: test_empty ", detail)
            self.assertIn("ran no tests", detail)
            self.assertNotIn("ERROR: test_ok", detail)

    def test_every_combination_of_exit_code_and_last_words(self):
        # impl review: green must need BOTH a zero exit and a matching verdict line. Every combination
        # of exit code x `Ran` line x verdict line, with the rule written from intent: the step is
        # green only for exit 0 + OK + (tests were run, or every one was SKIPPED — a setUpModule /
        # setUpClass skip reads `Ran 0 tests` / `OK (skipped=1)`, review run 4). Everything else is
        # red (run 2 removed the empty-module exception), and a red module is always NAMED.
        self.modules("test_m")
        wrong = []
        for rc in (0, 1, 5, -9):
            for ran in (None, 0, 3):
                for verdict in ("", "OK", "OK (skipped=1)", "FAILED (failures=1)", "NO TESTS RAN"):
                    out = "some output\n" if ran is None else f"{'-' * 70}\nRan {ran} tests in 0.1s\n\n{verdict}\n"
                    if ran is None and verdict:
                        out += verdict + "\n"                       # a verdict word without a Ran line
                    self.calls.clear()
                    with self.fake({"test_m": (rc, out)}):
                        got_rc, text = self.suite()
                    green = (rc == 0 and ran is not None and verdict.startswith("OK")
                             and (ran > 0 or "skipped=" in verdict))
                    named = "ERROR: test_m " in "\n".join(V.ut_fails(text))
                    if (got_rc == 0) != green or (not green and not named):
                        wrong.append((rc, ran, verdict, "step rc", got_rc, "named", named))
        self.assertEqual(wrong, [], "\n" + "\n".join(map(str, wrong)))

    def test_the_output_is_in_module_order_whatever_finishes_first(self):
        self.modules("test_a", "test_b", "test_c")
        gate = threading.Event()

        def slow(cmd, timeout, kw):
            gate.wait(5)
            return 0, "A-OUTPUT\n" + OK_1

        def fast(tag):
            def f(cmd, timeout, kw):
                if tag == "C":
                    gate.set()
                return 0, f"{tag}-OUTPUT\n" + OK_1
            return f
        with self.fake({"test_a": slow, "test_b": fast("B"), "test_c": fast("C")}):
            rc, out = self.suite(jobs=3)
        self.assertEqual(rc, 0)
        self.assertLess(out.index("A-OUTPUT"), out.index("B-OUTPUT"))
        self.assertLess(out.index("B-OUTPUT"), out.index("C-OUTPUT"))

    def test_ut_tail_reads_the_last_verdict_not_the_first_identical_one(self):
        # plan panel P2: `lines.index(ln)` found the FIRST "OK" — the first child's count
        text = _ok(1) + _ok(5) + f"{'-' * 70}\nRan 6 tests in 0.5s\n\nOK\n"
        self.assertEqual(V.ut_tail(text), "Ran 6 tests in 0.5s / OK")
        self.assertEqual(V.ut_tail(_ok(3, " (skipped=1)")), "Ran 3 tests in 0.010s / OK (skipped=1)")


class TheBudget(_Suite):
    def test_a_child_that_runs_out_is_named_and_the_rest_is_kept(self):
        self.modules("test_hang", "test_bad", "test_ok")
        script = {"test_hang": subprocess.TimeoutExpired(cmd="x", timeout=3, output=b"started test_h1 ...\n"),
                  "test_bad": (1, _failed(2, "test_bad", "failures=1"))}
        with self.fake(script):
            rc, out = self.suite()
        self.assertEqual(rc, 1)
        detail = "\n".join(V.ut_fails(out))
        self.assertIn("ERROR: test_hang ", detail)
        self.assertIn("timed out", detail)
        self.assertIn("started test_h1", detail)                         # what it printed before the kill
        self.assertIn("FAIL: test_x (test_bad.T)", detail)               # a real failure is not lost with it
        self.assertEqual(out.strip().splitlines()[-1], "FAILED (failures=1, errors=1)")

    def test_nothing_starts_after_the_deadline_and_what_did_not_run_is_an_error(self):
        self.modules("test_a", "test_b", "test_c", sizes={"test_a": 300, "test_b": 200, "test_c": 100})

        def burn(cmd, timeout, kw):
            time.sleep(0.6)
            return 0, OK_1
        with self.fake({"test_a": burn}):
            rc, out = self.suite(jobs=1, budget=0.3)          # one at a time: the order is the list's order
        self.assertEqual(rc, 1)
        self.assertEqual([c["mod"] for c in self.calls], ["test_a"])       # largest first; the others never started
        detail = "\n".join(V.ut_fails(out))
        self.assertIn("ERROR: test_b ", detail)
        self.assertIn("ERROR: test_c ", detail)
        self.assertIn("not started", detail)

    def test_every_child_gets_the_time_that_is_left(self):
        self.modules("test_a", "test_b")
        with self.fake({}):
            self.suite(jobs=2, budget=50)
        for c in self.calls:
            self.assertGreater(c["timeout"], 0)
            self.assertLessEqual(c["timeout"], 50)
            self.assertGreater(c["timeout"], 40)


class Neighbours(_Suite):
    def test_each_child_has_a_private_temp_dir_that_is_removed_afterwards(self):
        self.modules("test_a", "test_b")
        seen = {}

        def note(cmd, timeout, kw):
            env = kw["extra_env"]
            mod = cmd[cmd.index("-p") + 1][:-3]
            seen[mod] = (env["TMPDIR"], os.path.isdir(env["TMPDIR"]))
            self.assertEqual(env["TMPDIR"], env["TEMP"])
            self.assertEqual(env["TMPDIR"], env["TMP"])
            Path(env["TMPDIR"], "left-behind.txt").write_text("x", encoding="utf-8")
            return 0, OK_1
        with self.fake({}, default=note):
            rc, _ = self.suite(jobs=2)
        self.assertEqual(rc, 0)
        self.assertEqual(len({d for d, _ in seen.values()}), 2)          # not shared
        for d, existed in seen.values():
            self.assertTrue(existed)
            self.assertFalse(os.path.exists(d))                          # and cleaned up, leftovers included

    def test_the_full_log_keeps_every_block_whole(self):
        # plan panel P3: test_shell_fixtures appends to the log from INSIDE a child. With the log on,
        # each child writes to a private file and the parent folds it into that module's block.
        self.modules(*[f"test_m{i}" for i in range(6)])
        log = self.root / "full.txt"
        big = {f"test_m{i}": (f"<{i}>" * 20000) for i in range(6)}       # ~100 KB each: far past any pipe buffer

        def child(cmd, timeout, kw):
            mod = cmd[cmd.index("-p") + 1][:-3]
            private = kw["extra_env"][ENV_LOG]
            self.assertNotEqual(os.path.abspath(private), os.path.abspath(str(log)))
            with open(private, "ab") as fh:                              # what test_shell_fixtures does
                fh.write(f"===== fixture of {mod} =====\n{big[mod]}\n\n".encode("utf-8"))
            return 0, f"OUTPUT-OF-{mod}\n" + OK_1
        with mock.patch.dict(os.environ, {ENV_LOG: str(log)}), self.fake({}, default=child):
            rc, _ = self.suite(jobs=6, verbosity="-v")
        self.assertEqual(rc, 0)
        text = log.read_text(encoding="utf-8")
        for mod, payload in big.items():
            self.assertEqual(text.count(payload), 1, mod)               # whole, once
            i = text.index(f"OUTPUT-OF-{mod}")
            j = text.index(f"===== fixture of {mod} =====")
            self.assertLess(i, j)
            between = text[i:j]
            self.assertNotIn("OUTPUT-OF-", between[len(f"OUTPUT-OF-{mod}"):])    # no other module in between

    def test_two_threads_logging_at_once_keep_their_blocks_whole(self):
        # A platform whose append is not ONE atomic write is simulated: every write goes out in two
        # halves with a pause between them. The blocks must still come out whole — the lock's job.
        log = self.root / "full.txt"
        real_open = open

        class Halves:
            def __init__(self, fh):
                self.fh = fh

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.fh.close()

            def write(self, data):
                mid = len(data) // 2
                self.fh.write(data[:mid])
                self.fh.flush()
                time.sleep(0.05)
                self.fh.write(data[mid:])
                self.fh.flush()

        def slow_open(path, mode="r", *a, **kw):
            return Halves(real_open(path, mode, *a, **kw))

        with mock.patch.dict(os.environ, {ENV_LOG: str(log)}), mock.patch.object(V, "open", slow_open, create=True):
            threads = [threading.Thread(target=V.full_log, args=(f"block {i}", f"<{i}>" * 2000)) for i in range(4)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        text = log.read_text(encoding="utf-8")
        for i in range(4):
            self.assertEqual(text.count(f"===== block {i} =====\n" + f"<{i}>" * 2000 + "\n\n"), 1, i)

    def test_without_the_log_no_private_log_is_handed_out(self):
        self.modules("test_a")
        base = {k: v for k, v in os.environ.items() if k != ENV_LOG}
        with mock.patch.dict(os.environ, base, clear=True), self.fake({}):
            self.suite()
        self.assertNotIn(ENV_LOG, self.calls[0]["kw"]["extra_env"])


class Jobs(unittest.TestCase):
    def _jobs(self, value, cpus=16):
        env = {k: v for k, v in os.environ.items() if k != ENV_JOBS}
        if value is not None:
            env[ENV_JOBS] = value
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(V.os, "cpu_count", return_value=cpus):
            return V.suite_jobs()

    def test_default_leaves_a_core_free_and_is_capped(self):
        self.assertEqual(self._jobs(None, cpus=16), 8)
        self.assertEqual(self._jobs(None, cpus=4), 3)
        self.assertEqual(self._jobs(None, cpus=2), 1)
        self.assertEqual(self._jobs(None, cpus=1), 1)
        self.assertEqual(self._jobs(None, cpus=None), 1)

    def test_the_variable_wins_and_nonsense_falls_back_loudly(self):
        self.assertEqual(self._jobs("1"), 1)
        self.assertEqual(self._jobs("12"), 12)
        import contextlib
        import io
        for bad in ("0", "-2", "many", "1.5", " "):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(self._jobs(bad, cpus=4), 3, bad)
            if bad.strip():
                self.assertIn(ENV_JOBS, err.getvalue(), bad)

    def test_one_job_is_the_old_single_command(self):
        seen = []

        def fake_run(cmd, cwd=V.ROOT, timeout=900, **kw):
            seen.append((list(cmd), timeout, kw))
            return 0, "Ran 1 test\n\nOK\n"
        with mock.patch.dict(os.environ, {ENV_JOBS: "1"}), mock.patch.object(V, "run", side_effect=fake_run):
            status = V._unittest()[0]
        self.assertEqual(status, V.PASS)
        self.assertEqual(len(seen), 1)
        cmd, timeout, kw = seen[0]
        self.assertEqual(cmd[1:6], ["-m", "unittest", "discover", "-s", "tests"])
        self.assertNotIn("-p", cmd)
        self.assertEqual(timeout, V.UNITTEST_BUDGET_SECS)
        self.assertEqual(kw, {})


class RealProcesses(unittest.TestCase):
    """The same step through real children, on a three-module suite in a temporary root."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_green.py").write_text(textwrap.dedent('''
            import os, tempfile, unittest
            class T(unittest.TestCase):
                def test_one(self): self.assertTrue(True)
                @unittest.skip("demo")
                def test_two(self): pass
                def test_tmp_is_private(self):
                    self.assertIn("pb-verify-", tempfile.gettempdir())
                    open(os.path.join(tempfile.gettempdir(), "mine.txt"), "w").close()
        '''), encoding="utf-8")
        (tests / "test_red.py").write_text(textwrap.dedent('''
            import unittest
            class T(unittest.TestCase):
                def test_boom(self): self.assertEqual(1, 2, "planted failure")
        '''), encoding="utf-8")
        (tests / "test_broken_import.py").write_text("import no_such_module_anywhere\n", encoding="utf-8")

    def test_a_red_module_and_a_broken_import_fail_the_step_by_name(self):
        rc, out = V.run_suite(3, "-q", root=self.root)
        self.assertEqual(rc, 1)
        detail = "\n".join(V.ut_fails(out))
        self.assertIn("test_boom", detail)
        self.assertIn("planted failure", detail)
        self.assertIn("no_such_module_anywhere", detail)
        last = out.strip().splitlines()[-1]
        self.assertTrue(last.startswith("FAILED ("), last)
        self.assertIn("skipped=1", last)
        self.assertIn("Ran 5 tests in ", out)                            # 3 green + 1 red + 1 import error

    def test_an_empty_module_and_a_child_that_fakes_the_empty_exit_both_fail(self):
        os.remove(self.root / "tests" / "test_red.py")
        os.remove(self.root / "tests" / "test_broken_import.py")
        (self.root / "tests" / "test_empty.py").write_text("# no tests here\n", encoding="utf-8")
        (self.root / "tests" / "test_fake5.py").write_text(textwrap.dedent('''
            import os, sys
            sys.stderr.write("-" * 70 + "\\nRan 0 tests in 0.000s\\n\\nNO TESTS RAN\\n")
            sys.stderr.flush()
            os._exit(5)
        '''), encoding="utf-8")
        rc, out = V.run_suite(3, "-q", root=self.root)
        self.assertEqual(rc, 1, out)
        detail = "\n".join(V.ut_fails(out))
        self.assertIn("ERROR: test_empty ", detail)
        self.assertIn("ERROR: test_fake5 ", detail)
        self.assertNotIn("ERROR: test_green", detail)

    def test_a_whole_module_skipped_is_green(self):
        os.remove(self.root / "tests" / "test_red.py")
        os.remove(self.root / "tests" / "test_broken_import.py")
        (self.root / "tests" / "test_skipmod.py").write_text(textwrap.dedent('''
            import unittest
            def setUpModule():
                raise unittest.SkipTest("no bash on this lane")
            class T(unittest.TestCase):
                def test_x(self): self.fail("must not run")
        '''), encoding="utf-8")
        rc, out = V.run_suite(2, "-q", root=self.root)
        self.assertEqual(rc, 0, out)

    def test_green_alone_is_green(self):
        os.remove(self.root / "tests" / "test_red.py")
        os.remove(self.root / "tests" / "test_broken_import.py")
        rc, out = V.run_suite(2, "-v", root=self.root)
        self.assertEqual(rc, 0, out)
        self.assertEqual(V.ut_tail(out).split(" / ")[-1], "OK (skipped=1)")

    def test_a_hanging_child_is_killed_at_the_budget_and_named(self):
        (self.root / "tests" / "test_hang.py").write_text(textwrap.dedent('''
            import time, unittest
            class T(unittest.TestCase):
                def test_sleep(self): time.sleep(60)
        '''), encoding="utf-8")
        os.remove(self.root / "tests" / "test_red.py")
        os.remove(self.root / "tests" / "test_broken_import.py")
        t0 = time.monotonic()
        rc, out = V.run_suite(2, "-q", root=self.root, budget=4)
        took = time.monotonic() - t0
        self.assertEqual(rc, 1)
        self.assertLess(took, 30)
        detail = "\n".join(V.ut_fails(out))
        self.assertIn("ERROR: test_hang ", detail)
        self.assertIn("timed out", detail)
        self.assertIn("Ran 3 tests in ", out)                            # the green module's result is kept


if __name__ == "__main__":
    unittest.main()
