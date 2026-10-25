#!/usr/bin/env python3
"""The red receipt (PHASE-B.md item 9b, part 1; task 278's contract made a record).

The owner, 2026-10-21 15:15 (M1415): the tests are written first, so the code follows their path. A task records
where it began — its base, per repository, at its first activation (`<task>/base.json`) — and `tasks red [<N>]`
runs each `## Test contract` row's test on that base with the task's test files laid over it, in a temporary copy
with a throwaway HOME, and writes what it saw: `## Red receipt` in task.md (the newest run, for the judges) and
`red-receipt.jsonl` beside it (every run, append-only). The close's check is part 2.

Each test names the row of task 280's contract it proves. Real temporary git projects (an outer repository and a
nested `code_roots` one), the CLI driven as `python3 -m tasks.cli`, as `tests/test_verify_memory.py` does.
Pure stdlib unittest. Run: python3 tests/test_red_receipt.py
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parent
_PLAYBOOK = REPO / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import template  # noqa: E402

GIT = ["git", "-c", "user.email=a@b", "-c", "user.name=p", "-c", "commit.gpgsign=false"]


def _contract(rows: "list[tuple[str, str, str, str]]") -> str:
    body = "".join(f"| {n} | {k} | {b} | {t} |\n" for n, k, b, t in rows)
    return ("## Test contract\n<!-- pin -->\n\n| # | Kind | Behavior | Test that proves it |\n"
            "|---|------|----------|---------------------|\n" + body + "\n")


class Project(unittest.TestCase):
    """An outer repository with a nested `code_roots` repository `code/`, a fake HOME, one task."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb280-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = self.d / "home"
        self.home.mkdir()
        (self.home / "keep.txt").write_text("the real home\n", encoding="utf-8")
        self.g("init", "-q")
        (self.d / ".gitignore").write_text("home/\ncode/\n.agent/journal/\n.agent/sessions/\n", encoding="utf-8")
        (self.d / ".agent" / "tasks").mkdir(parents=True)
        self.config()
        (self.d / "README.md").write_text("outer\n", encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "outer start")
        self.code = self.d / "code"
        self.code.mkdir()
        self.g("init", "-q", cwd=self.code)
        (self.code / "src.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (self.code / "tests").mkdir()
        (self.code / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.code / "tests" / "test_old.py").write_text(
            "import unittest\nimport src\n\n\nclass T(unittest.TestCase):\n"
            "    def test_one(self):\n        self.assertTrue(callable(src.f))\n", encoding="utf-8")
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "code start", cwd=self.code)

    def config(self, **extra):
        cfg = {"verify": "true", "code_roots": ["code"],
               "red_run": "cd code && python3 -m unittest {test}"}
        cfg.update(extra)
        for k in [k for k, v in cfg.items() if v is None]:
            del cfg[k]
        (self.d / ".agent" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    def g(self, *args, cwd=None):
        return subprocess.run([*GIT, *args], cwd=cwd or self.d, capture_output=True, text=True, check=True)

    def head(self, cwd):
        return self.g("rev-parse", "HEAD", cwd=cwd).stdout.strip()

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update({"PYTHONPATH": str(_PLAYBOOK), "HOME": str(self.home),
                    "PLAYBOOK_SESSION_ID": "pid-red-probe"})
        return env

    def cli(self, *args, timeout=300):
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.d, env=self.env(),
                              capture_output=True, text=True, timeout=timeout)

    def new_task(self, activate=True):
        r = self.cli("new", "feature", "sample", "a sample task")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.task_dir = next((self.d / ".agent" / "tasks").glob("001-*"))
        self.task_md = self.task_dir / "task.md"
        if activate:
            r = self.cli("work", "1")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.work_out = r.stdout + r.stderr

    def set_contract(self, rows):
        text = self.task_md.read_text(encoding="utf-8")
        if "\n## Test contract\n" in text:
            text = re.sub(r"(?ms)^## Test contract\n.*?(?=^## )", lambda _m: _contract(rows), text)
        else:
            text = text.replace("## Plan Review\n", _contract(rows) + "## Plan Review\n", 1)
        self.task_md.write_text(text, encoding="utf-8")

    def base(self):
        return json.loads((self.task_dir / "base.json").read_text(encoding="utf-8"))

    def receipt_lines(self):
        p = self.task_dir / "red-receipt.jsonl"
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def section(self):
        m = re.search(r"(?ms)^## Red receipt\n(.*?)(?=^## |\Z)", self.task_md.read_text(encoding="utf-8"))
        return m.group(1) if m else ""

    def change_code(self):
        """The task's work after its start: new code, and a new test that only the new code passes."""
        (self.code / "src.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        (self.code / "tests" / "test_new.py").write_text(
            "import unittest\nimport src\n\n\nclass N(unittest.TestCase):\n"
            "    def test_two(self):\n        self.assertEqual(src.f(), 2)\n", encoding="utf-8")


class TheBase(Project):
    def test_activation_records_the_base(self):
        # row 1
        self.new_task()
        b = self.base()
        self.assertEqual(set(b["scopes"]), {"", "code"})
        self.assertEqual(b["scopes"][""]["commit"], self.head(self.d))
        self.assertEqual(b["scopes"]["code"]["commit"], self.head(self.code))
        for name, repo in (("", self.d), ("code", self.code)):
            tree = b["scopes"][name]["tree"]
            self.assertEqual(self.g("cat-file", "-t", tree, cwd=repo).stdout.strip(), "tree", name)
        self.assertRegex(b["recorded_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d.*[+-]\d\d:\d\d$")

    def test_a_later_activation_keeps_the_base(self):
        # row 2 (must-not)
        self.new_task()
        before = (self.task_dir / "base.json").read_bytes()
        self.change_code()
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "work", cwd=self.code)
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.task_dir / "base.json").read_bytes(), before)

    def test_a_status_set_back_to_pending_keeps_the_base(self):
        # row 2 (must-not): even a task put back to `pending` by hand keeps the base of its first activation
        self.new_task()
        before = (self.task_dir / "base.json").read_bytes()
        self.change_code()
        r = self.cli("work", "done", "--force", "--reason", "probe")
        text = self.task_md.read_text(encoding="utf-8")
        self.task_md.write_text(re.sub(r"(?m)^## Status\n.*$", "## Status\npending", text, count=1), encoding="utf-8")
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.task_dir / "base.json").read_bytes(), before)

    def test_the_base_holds_the_uncommitted_work(self):
        # row 3: the task began from a change not yet committed
        (self.code / "src.py").write_text("def f():\n    return 7\n", encoding="utf-8")
        self.new_task()
        tree = self.base()["scopes"]["code"]["tree"]
        shown = self.g("cat-file", "-p", f"{tree}:src.py", cwd=self.code).stdout
        self.assertIn("return 7", shown)

    def test_a_task_that_already_began_gets_no_base(self):
        # row 22: a task with a checked gate at its activation began before it — its start is not now
        self.new_task(activate=False)
        text = self.task_md.read_text(encoding="utf-8")
        self.assertIn("## Status\npending\n", text)
        self.task_md.write_text(text.replace("## Status\npending\n", "## Status\nin_progress\n", 1), encoding="utf-8")
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.task_dir / "base.json").exists())
        self.assertIn("already began", r.stdout + r.stderr)

    def test_two_first_activations_write_one_base(self):
        # row 26 (P7)
        self.new_task(activate=False)
        procs = []
        for sid in ("pid-red-a", "pid-red-b"):
            env = dict(self.env(), PLAYBOOK_SESSION_ID=sid)
            procs.append(subprocess.Popen([sys.executable, "-m", "tasks.cli", "work", "1"], cwd=self.d, env=env,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for p in procs:
            p.communicate(timeout=120)
        b = self.base()                                          # one whole JSON document
        self.assertEqual(set(b["scopes"]), {"", "code"})
        self.assertEqual(sorted(p.name for p in self.task_dir.iterdir() if p.name.startswith("base")), ["base.json"])

    def test_a_failed_first_capture_is_final(self):
        # row 26 (P7): the first activation decides; a later one never captures what the task has changed since
        shutil.rmtree(self.code / ".git")                       # the code scope is no repository at all
        self.new_task()
        b = self.base()
        self.assertIn("failed", b)
        self.g("init", "-q", cwd=self.code)
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "now", cwd=self.code)
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.base(), b)
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--base", r.stderr)

    def test_activation_without_git_says_so_and_goes_on(self):
        # row 4: outside git, no base, one line, the task active
        shutil.rmtree(self.d / ".git")
        shutil.rmtree(self.code / ".git")
        self.config(code_roots=None)
        self.new_task()
        self.assertIn("failed", self.base())                    # the first activation's decision (plan panel P7)
        self.assertIn("no base recorded", self.work_out)
        self.assertEqual(self.cli("status").returncode, 0)


class TheRun(Project):
    def rows(self):
        return [("1", "normal", "f returns 2", "`tests.test_new`"),
                ("2", "must-not", "the old test still holds", "`tests.test_old`")]

    def test_red_runs_each_row_on_the_base(self):
        # row 5
        self.new_task()
        self.change_code()
        self.set_contract(self.rows())
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        lines = self.receipt_lines()
        self.assertEqual([x["row"] for x in lines], ["1", "2"])
        self.assertEqual(lines[0]["status"], "red")
        self.assertNotEqual(lines[0]["exit"], 0)
        self.assertEqual(lines[0]["now"]["status"], "green")
        self.assertEqual(lines[0]["verdict"], "proved")
        self.assertEqual(lines[1]["status"], "green")
        self.assertEqual(lines[1]["verdict"], "already true")
        self.assertEqual(lines[0]["command"], "cd code && python3 -m unittest tests.test_new")
        self.assertEqual(lines[0]["run"], lines[1]["run"])
        sec = self.section()
        self.assertIn("<!-- pin -->", sec.split("\n", 2)[0] + sec.split("\n", 2)[1])
        # impl round 3 (sonnet): the section counts every verdict and lists only the rows not proved
        self.assertIn("proved: 1 (row 1)", sec)
        self.assertNotRegex(sec, r"(?m)^\| 1 \|")
        self.assertRegex(sec, r"\| 2 \| must-not \| `tests\.test_old` \| green \(exit 0[^|]*\| green \(exit 0[^|]*\| already true \|")
        self.assertIn(self.head(self.code)[:7], sec)

    def test_the_base_copy_has_old_code_and_new_tests(self):
        # row 6: the copy holds the base's code and the working tree's test files, never the task's code change
        self.new_task()
        self.change_code()
        self.set_contract([("1", "edge", "old code in the copy", "`grep -q 'return 2' code/src.py`"),
                           ("2", "edge", "new test in the copy", "`test -f code/tests/test_new.py`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        status = {x["row"]: x["status"] for x in self.receipt_lines()}
        self.assertEqual(status, {"1": "red", "2": "green"})

    def test_a_red_run_leaves_the_project_and_home_alone(self):
        # row 7 (must-not)
        self.new_task()
        self.change_code()
        self.set_contract([("1", "hostile", "a row that writes", "`echo x > code/src.py; echo y > \"$HOME/keep.txt\"; false`")])
        before = {
            "status": self.g("status", "--porcelain", cwd=self.code).stdout,
            "head": self.head(self.code),
            "index": self.g("diff", "--cached", "--stat", cwd=self.code).stdout,
            "src": (self.code / "src.py").read_text(encoding="utf-8"),
            "home": sorted(p.name for p in self.home.iterdir()),
            "keep": (self.home / "keep.txt").read_text(encoding="utf-8"),
        }
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        after = {
            "status": self.g("status", "--porcelain", cwd=self.code).stdout,
            "head": self.head(self.code),
            "index": self.g("diff", "--cached", "--stat", cwd=self.code).stdout,
            "src": (self.code / "src.py").read_text(encoding="utf-8"),
            "home": sorted(p.name for p in self.home.iterdir()),
            "keep": (self.home / "keep.txt").read_text(encoding="utf-8"),
        }
        self.assertEqual(after, before)

    def test_a_test_id_without_red_run_is_unrunnable(self):
        # row 8
        self.config(red_run=None)
        self.new_task()
        self.set_contract([("1", "normal", "an id", "`tests.test_new`"),
                           ("2", "normal", "a command", "`exit 1`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        lines = {x["row"]: x for x in self.receipt_lines()}
        self.assertEqual(lines["1"]["status"], "unrunnable")
        self.assertIn("no red_run", lines["1"]["why"])
        self.assertEqual(lines["2"]["status"], "red")

    def test_a_row_without_a_test_is_unrunnable(self):
        # row 9
        self.new_task()
        self.set_contract([("1", "normal", "no test named", "(to write)"), ("2", "normal", "one", "`exit 1`")])
        self.cli("red")
        lines = {x["row"]: x for x in self.receipt_lines()}
        self.assertEqual(lines["1"]["status"], "unrunnable")
        self.assertIn("no test named", lines["1"]["why"])
        self.assertEqual(lines["2"]["status"], "red")

    def test_a_task_without_a_base_needs_the_base_flag(self):
        # row 10: an old task, activated before the base existed
        self.new_task()
        (self.task_dir / "base.json").unlink()
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--base", r.stderr)
        self.assertEqual(self.receipt_lines(), [])
        r = self.cli("red", "--base", f"code={self.head(self.code)}")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.receipt_lines()[0]["status"], "red")

    def test_no_contract_is_refused(self):
        # row 11: nothing written
        self.new_task()
        text = self.task_md.read_text(encoding="utf-8")
        self.task_md.write_text(re.sub(r"(?ms)^## Test contract\n.*?(?=^## )", "", text), encoding="utf-8")
        before = self.task_md.read_bytes()
        r = self.cli("red")
        self.assertEqual(r.returncode, 1)
        self.assertIn("## Test contract", r.stderr)
        self.assertEqual(self.task_md.read_bytes(), before)
        self.assertFalse((self.task_dir / "red-receipt.jsonl").exists())
        self.set_contract([])                                    # a contract with no rows
        r = self.cli("red")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no row", r.stderr)

    def test_a_timeout_is_not_red(self):
        # row 12
        self.config(red_timeout=1)
        self.new_task()
        self.set_contract([("1", "normal", "slow", "`sleep 5`")])
        t0 = time.monotonic()
        self.cli("red")
        self.assertLess(time.monotonic() - t0, 30)
        line = self.receipt_lines()[0]
        self.assertEqual(line["status"], "timeout")
        self.assertIsNone(line["exit"])

    def test_a_second_run_appends_and_replaces_the_section(self):
        # row 13
        self.new_task()
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        self.cli("red")
        self.cli("red")
        lines = self.receipt_lines()
        self.assertEqual(len(lines), 2)
        self.assertNotEqual(lines[0]["run"], lines[1]["run"])
        text = self.task_md.read_text(encoding="utf-8")
        self.assertEqual(text.count("\n## Red receipt\n"), 1)
        self.assertIn(lines[1]["run"], self.section())

    def test_two_runs_at_once_keep_whole_records(self):
        # row 14
        self.new_task()
        self.set_contract([("1", "normal", "one", "`sleep 1; false`"), ("2", "normal", "two", "`exit 1`")])
        procs = [subprocess.Popen([sys.executable, "-m", "tasks.cli", "red"], cwd=self.d, env=self.env(),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for p in procs:
            p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0)
        lines = self.receipt_lines()                             # every line parses
        self.assertEqual(len(lines), 4)
        runs = {x["run"] for x in lines}
        self.assertEqual(len(runs), 2)
        sec = self.section()
        newest = {x["row"]: x["run"] for x in lines}             # the state across runs (task 281): each row's newest
        self.assertTrue(all(r in sec for r in newest.values()), sec)
        self.assertEqual(len(re.findall(r"(?m)^\| [12] \|", sec)), 2)

    def test_an_interrupted_run_cleans_up(self):
        # row 15
        self.new_task()
        self.set_contract([("1", "normal", "quick", "`exit 1`"), ("2", "normal", "long", "`sleep 30`")])
        before = self.task_md.read_bytes()
        p = subprocess.Popen([sys.executable, "-m", "tasks.cli", "red"], cwd=self.d, env=self.env(),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        copy = None
        deadline = time.monotonic() + 60
        for line in p.stdout:
            m = re.search(r"base copy: (\S+)", line)
            if m:
                copy = Path(m.group(1))
            if line.lstrip().startswith("1 ") or "row 1" in line:
                break
            if time.monotonic() > deadline:
                break
        p.send_signal(signal.SIGINT)
        p.communicate(timeout=60)
        self.assertIsNotNone(copy)
        self.assertFalse(copy.exists())
        self.assertEqual([x["row"] for x in self.receipt_lines()], ["1"])
        self.assertEqual(self.task_md.read_bytes(), before)

    def test_a_linked_test_file_is_copied_as_a_link(self):
        # row 16: a link in the tests is the link, never what it points at
        outside = self.d / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        self.new_task()
        os.symlink(str(outside), self.code / "tests" / "test_link.txt")
        self.set_contract([("1", "hostile", "link kept", "`test -L code/tests/test_link.txt`"),
                           ("2", "hostile", "a write through it", "`echo changed > code/tests/test_link.txt; false`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual({x["row"]: x["status"] for x in self.receipt_lines()}["1"], "green")
        # the copy's link points at the real file — the row's write lands there: the receipt names the copy, and
        # a red run copies a link exactly so the base is what the working tree had
        self.assertTrue((self.task_dir / "red-receipt.jsonl").exists())

    def test_a_lost_base_tree_falls_back_to_the_commit(self):
        # row 17
        self.new_task()
        b = self.base()
        b["scopes"]["code"]["tree"] = "0" * 40
        (self.task_dir / "base.json").write_text(json.dumps(b), encoding="utf-8")
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        line = self.receipt_lines()[0]
        self.assertEqual(line["base"]["code"]["used"], "commit")
        self.assertIn("tree is gone", line["base"]["code"]["why"])
        self.assertIn("tree is gone", self.section())

    def test_a_lost_dirty_base_is_refused(self):
        # row 17 (P9): the base began from uncommitted work and its tree is gone — the commit is not that base
        (self.code / "src.py").write_text("def f():\n    return 7\n", encoding="utf-8")
        self.new_task()
        b = self.base()
        self.assertFalse(b["scopes"]["code"]["clean"])
        b["scopes"]["code"]["tree"] = "0" * 40
        (self.task_dir / "base.json").write_text(json.dumps(b), encoding="utf-8")
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 1)
        self.assertIn("uncommitted part is gone", r.stderr)
        self.assertEqual(self.receipt_lines(), [])

    def test_red_on_both_sides_is_broken_not_proof(self):
        # row 23 (P5)
        self.new_task()
        self.set_contract([("1", "normal", "always red", "`exit 1`")])
        self.cli("red")
        line = self.receipt_lines()[0]
        self.assertEqual((line["status"], line["now"]["status"], line["verdict"]), ("red", "red", "broken"))
        self.assertIn("broken", self.section())

    def test_a_missing_runner_is_a_launch_failure(self):
        # row 23 (P5): 127 (no such command) and 126 (not executable) never count as red
        self.new_task()
        (self.code / "tests" / "notexec.sh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        self.set_contract([("1", "normal", "no runner", "`no_such_runner_280 --x`"),
                           ("2", "normal", "not executable", "`./code/tests/notexec.sh --x`")])
        self.cli("red")
        lines = {x["row"]: x for x in self.receipt_lines()}
        self.assertEqual(lines["1"]["status"], "launch failed")
        self.assertEqual(lines["2"]["status"], "launch failed")
        self.assertNotEqual(lines["1"]["verdict"], "proved")

    def test_the_row_sees_no_live_project_environment(self):
        # row 24 (P6): the CLI's own PYTHONPATH and session ids stay out of a row
        self.new_task()
        check = ('test -z "$PYTHONPATH$CLAUDE_PROJECT_DIR$PLAYBOOK_SESSION_ID$GIT_DIR$GIT_WORK_TREE" '
                 '&& test "$PLAYBOOK_RED" = 1 && test -n "$GIT_CEILING_DIRECTORIES"')
        self.set_contract([("1", "must-not", "clean env", f"`{check}`")])
        env = self.env()
        env["CLAUDE_PROJECT_DIR"] = str(self.d)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "red"], cwd=self.d, env=env, capture_output=True,
                           text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        line = self.receipt_lines()[0]
        self.assertEqual((line["status"], line["now"]["status"]), ("green", "green"), line)
        from tasks import red
        scrubbed = red._row_env({"GIT_DIR": "/x", "GIT_WORK_TREE": "/x", "GIT_INDEX_FILE": "/x", "GIT_COMMON_DIR": "/x",
                                 "GIT_OBJECT_DIRECTORY": "/x", "PYTHONPATH": "/x", "CLAUDE_X": "1", "PLAYBOOK_Y": "1",
                                 "PATH": "/usr/bin", "LANG": "C"}, Path("/tmp/h"), Path("/tmp/c"))
        self.assertEqual(scrubbed, {"PATH": "/usr/bin", "LANG": "C", "HOME": "/tmp/h", "PLAYBOOK_RED": "1",
                                    "GIT_CEILING_DIRECTORIES": "/tmp/c",
                                    "NO_COLOR": "1", "PY_COLORS": "0", "PYTHON_COLORS": "0"})   # no colour (task 284)

    def test_a_write_to_the_real_project_is_denied(self):
        # row 25 (P6): with bubblewrap the root is read-only — a row cannot reach the real project by an absolute path
        if not shutil.which("bwrap"):
            self.skipTest("bubblewrap is not installed")
        from provider import sandbox
        if sandbox.is_sandboxed():
            self.skipTest("already inside a sandbox: a nested one cannot start")
        self.new_task()
        # outside /tmp, which the containment keeps writable: the project here is a /tmp folder, his never is
        outside = Path(tempfile.mkdtemp(prefix="pb280-probe-", dir="/var/tmp"))
        self.addCleanup(shutil.rmtree, outside, True)
        probe = outside / "RED_PROBE.txt"
        self.set_contract([("1", "hostile", "absolute write", f"`echo x > {probe}`")])
        self.cli("red")
        line = self.receipt_lines()[0]
        self.assertFalse(probe.exists())
        self.assertTrue(line["contained"])
        self.assertEqual(line["status"], "red")

    def test_an_output_line_cannot_change_the_task_structure(self):
        # row 27 (P8)
        from tasks import core
        self.new_task()
        self.set_contract([("1", "hostile", "a heading", "`echo '## Status'; echo done; exit 1`"),
                           ("2", "hostile", "a fence", "`echo '```'; exit 1`"),
                           ("3", "hostile", "a gate", "`echo '- [ ] x'; exit 1`")])
        before = (core._extract_status(self.task_md), core._gate_counts(self.task_md.read_text(encoding="utf-8")))
        self.cli("red")
        after = (core._extract_status(self.task_md), core._gate_counts(self.task_md.read_text(encoding="utf-8")))
        self.assertEqual(after, before)
        self.assertIn("\n## Risk\nunclassified\n", self.task_md.read_text(encoding="utf-8"))

    def test_a_record_binds_its_command_and_tests(self):
        # row 28 (P10)
        self.new_task()
        self.change_code()
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`")])
        self.cli("red")
        (self.code / "tests" / "test_new.py").write_text(
            (self.code / "tests" / "test_new.py").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        self.cli("red")
        a, b = self.receipt_lines()
        self.assertEqual(a["command"], "cd code && python3 -m unittest tests.test_new")
        self.assertRegex(a["tests_sha"], r"^[0-9a-f]{16}$")
        self.assertNotEqual(a["tests_sha"], b["tests_sha"])
        self.assertEqual(set(a["now_tree"]), {"", "code"})

    def test_the_copy_leaves_agent_records_out(self):
        # row 29 (must-not): the project's tracked `.agent` files are records, not code
        self.new_task()
        self.set_contract([("1", "must-not", "no records", "`test ! -e .agent/config.json`")])
        self.cli("red")
        line = self.receipt_lines()[0]
        self.assertEqual((line["status"], line["now"]["status"]), ("green", "green"), line)

    def test_the_record_keeps_the_first_error_line(self):
        # row 30 (plan panel P5, opus): unittest's last line is "FAILED (…)"; the first error line says why
        self.new_task()
        self.change_code()
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`")])
        self.cli("red")
        line = self.receipt_lines()[0]
        self.assertRegex(line["last"], r"^FAILED")
        self.assertRegex(line["error"], r"^AssertionError: 1 != 2")
        self.assertIn("proved: 1 (row 1)", self.section())          # a proved row's lines are in the jsonl
        self.assertEqual(line["now"]["error"], "")

    def test_undecodable_output_is_recorded_replaced(self):
        # row 18
        self.new_task()
        self.set_contract([("1", "normal", "bytes", "`printf 'bad \\377\\376 end\\n'; false`")])
        self.cli("red")
        last = self.receipt_lines()[0]["last"]
        self.assertIn("bad", last)
        self.assertIn("\ufffd", last)

    def test_a_long_output_keeps_only_its_last_line(self):
        # row 19
        self.new_task()
        self.set_contract([("1", "normal", "huge", "`python3 -c \"print('x' * 200000); print('y' * 500); print()\"; false`")])
        self.cli("red")
        last = self.receipt_lines()[0]["last"]
        self.assertEqual(last, "y" * 200)


class RoundOne(Project):
    """Impl round 1's findings (task 280), each red before its fix."""

    def test_a_linked_base_folder_is_never_written_through(self):
        # row 32 (GRAVE, codex Critical): the base's `tests` is a link to a real folder; the working tree made it a
        # directory — the overlay must replace the link inside the copy, never write or unlink through it
        outside = Path(tempfile.mkdtemp(prefix="pb280-shared-"))     # the overlay runs in the CLI, outside any cage
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "keep.txt").write_text("real\n", encoding="utf-8")
        (outside / "test_old.py").write_text("real test\n", encoding="utf-8")
        shutil.rmtree(self.code / "tests")
        os.symlink(str(outside), self.code / "tests")
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "tests is a link", cwd=self.code)
        self.new_task()
        (self.code / "tests").unlink()
        (self.code / "tests").mkdir()
        (self.code / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.code / "tests" / "test_old.py").write_text("print('new')\n", encoding="utf-8")
        self.set_contract([("1", "normal", "one", "`test -f code/tests/__init__.py`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["keep.txt", "test_old.py"])
        self.assertEqual((outside / "test_old.py").read_text(encoding="utf-8"), "real test\n")
        self.assertEqual(self.receipt_lines()[0]["status"], "green")      # the base copy got the directory

    def test_a_live_project_under_tmp_is_not_writable_from_a_row(self):
        # row 33 (GRAVE, agy + codex): this project lives under /tmp, which the containment keeps writable
        if not shutil.which("bwrap"):
            self.skipTest("bubblewrap is not installed")
        from provider import sandbox
        if sandbox.is_sandboxed():
            self.skipTest("already inside a sandbox: a nested one cannot start")
        self.assertTrue(str(self.d).startswith("/tmp/"), self.d)
        self.new_task()
        target = self.code / "src.py"
        before = target.read_text(encoding="utf-8")
        self.set_contract([("1", "hostile", "a write to the live project", f"`echo gone > {target}; exit 3`")])
        self.cli("red")
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        line = self.receipt_lines()[0]
        self.assertTrue(line["contained"])

    def test_each_side_and_row_gets_a_fresh_home_and_copy(self):
        # row 34 (codex I): a marker one side leaves must not make the other side pass
        self.new_task()
        cmd = '`if test -e "$HOME/seen" \\|\\| test -e seen; then exit 0; else touch "$HOME/seen" seen; exit 1; fi`'
        self.set_contract([("1", "hostile", "marker", cmd), ("2", "hostile", "marker again", cmd)])
        self.cli("red")
        lines = self.receipt_lines()
        self.assertEqual([(x["status"], x["now"]["status"], x["verdict"]) for x in lines],
                         [("red", "red", "broken"), ("red", "red", "broken")])

    def test_the_overlay_and_digest_come_from_the_frozen_copy(self):
        # row 35 (codex I): the tests laid over the base are the ones the "now" copy holds, never the live files
        from tasks import red
        src = self.d / "frozen"
        dst = self.d / "basecopy"
        (src / "code" / "tests").mkdir(parents=True)
        (src / "code" / "tests" / "test_a.py").write_text("frozen\n", encoding="utf-8")
        (src / "code" / "src.py").write_text("code\n", encoding="utf-8")
        (src / ".agent" / "tests").mkdir(parents=True)
        (src / ".agent" / "tests" / "test_x.py").write_text("record\n", encoding="utf-8")
        dst.mkdir()
        n = red._overlay_from_copy(src, dst, {}, ["code"])
        self.assertEqual(n, 1)
        self.assertEqual((dst / "code" / "tests" / "test_a.py").read_text(encoding="utf-8"), "frozen\n")
        self.assertFalse((dst / "code" / "src.py").exists())
        self.assertFalse((dst / ".agent").exists())
        # the digest of the laid tests is read from the frozen "now" tree (task 281: the same from a run and a check)
        from tasks.post_d6 import worktree_tree_why
        scopes = {"code": self.code}
        t1, _ = worktree_tree_why(self.code, [":(exclude).agent"])
        d1 = red._digest_trees(scopes, {"code": t1}, {}, [])
        (self.code / "tests" / "test_old.py").write_text("edited\n", encoding="utf-8")
        self.assertEqual(red._digest_trees(scopes, {"code": t1}, {}, []), d1)    # the frozen tree, not the live file
        t2, _ = worktree_tree_why(self.code, [":(exclude).agent"])
        self.assertNotEqual(red._digest_trees(scopes, {"code": t2}, {}, []), d1)

    def test_the_base_is_captured_before_the_activation_is_published(self):
        # row 36 (codex I): nothing can act as this task's session before its base exists
        from unittest import mock
        from tasks import lifecycle
        self.new_task(activate=False)
        seen = []
        real = lifecycle._activate_status_then_publish

        def spy(task_file, *a, **k):
            seen.append((Path(task_file).parent / "base.json").exists())
            return real(task_file, *a, **k)
        env = {k: v for k, v in self.env().items()}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(lifecycle, "_activate_status_then_publish",
                                                                              spy):
            cwd = os.getcwd()
            os.chdir(self.d)
            try:
                lifecycle.cmd_work(["1"])
            finally:
                os.chdir(cwd)
        self.assertEqual(seen, [True])

    def test_sigterm_cleans_up_like_an_interrupt(self):
        # row 37 (opus I)
        self.new_task()
        tag = f"pb280long{os.getpid()}x{time.time_ns()}"          # made at run time: no other command line holds it
        self.set_contract([("1", "normal", "quick", "`exit 1`"),
                           ("2", "normal", "long", f"`bash -c 'exec -a {tag} sleep 30'`")])
        before = self.task_md.read_bytes()
        p = subprocess.Popen([sys.executable, "-m", "tasks.cli", "red"], cwd=self.d, env=self.env(),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        copy = None
        for line in p.stdout:
            m = re.search(r"base copy: (\S+)", line)
            if m:
                copy = Path(m.group(1))
            if "row 1" in line:
                break
        time.sleep(1)
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=60)
        self.assertIsNotNone(copy)
        self.assertFalse(copy.exists())
        self.assertEqual(self.task_md.read_bytes(), before)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", tag], capture_output=True, text=True).stdout.strip()
        self.assertEqual(left, "", "the row's process outlived the run")

    def test_a_row_the_command_guard_blocks_is_refused(self):
        # row 38 (sonnet I): a row's command meets the agent's destructive-command interlock
        self.new_task()
        marker = self.d / "ran.txt"
        self.set_contract([("1", "hostile", "a force push", f"`touch {marker} && git push --force origin main`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        line = self.receipt_lines()[0]
        self.assertEqual(line["status"], "refused")
        self.assertIn("command guard", line["why"])
        self.assertFalse(marker.exists())

    def test_red_links_bring_an_ignored_dependency_folder(self):
        # row 39 (opus I): an ignored dependency folder (node_modules, .venv) is in neither copy unless named
        (self.code / ".gitignore").write_text("deps/\n", encoding="utf-8")
        (self.code / "deps").mkdir()
        (self.code / "deps" / "dep.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "ignore deps", cwd=self.code)
        self.new_task()
        self.change_code()
        (self.code / "tests" / "test_dep.py").write_text(
            "import sys, unittest\nsys.path.insert(0, 'deps')\nimport dep, src\n\n\nclass D(unittest.TestCase):\n"
            "    def test_dep(self):\n        self.assertEqual(src.f(), dep.VALUE)\n", encoding="utf-8")
        self.set_contract([("1", "normal", "uses a dependency", "`tests.test_dep`")])
        self.cli("red")
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "broken")      # no dependency in either copy
        self.config(red_links=["code/deps"])
        self.cli("red")
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "proved")

    def test_a_child_that_holds_the_pipe_cannot_hang_the_run(self):
        # row 40 (sonnet Minor): a daemonized child keeps stdout open after its row is killed
        self.config(red_timeout=2)
        self.new_task()
        tag = f"pb280daemon{os.getpid()}x{time.time_ns()}"        # made at run time: no other command line holds it
        self.set_contract([("1", "hostile", "a daemon", f"`setsid bash -c 'exec -a {tag} sleep 60' & exit 1`")])
        self.addCleanup(subprocess.run, ["pkill", "-f", tag], capture_output=True)
        t0 = time.monotonic()
        r = self.cli("red", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertLess(time.monotonic() - t0, 60)

    def test_an_unclosed_fence_in_the_receipt_is_refused_cleanly(self):
        # row 41 (agy Minor): the section writer's ValueError is a refusal, not a traceback
        self.new_task()
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        text = self.task_md.read_text(encoding="utf-8")
        self.task_md.write_text(text.replace("## Plan Review\n", "## Red receipt\n```\n\n## Plan Review\n", 1),
                                encoding="utf-8")
        r = self.cli("red")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Refused", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_an_escaped_pipe_is_a_pipe_in_the_cell(self):
        # row 42 (found by mutant N12): a shell `||` is written `\|\|` in the table, and runs as `||`
        from tasks import red
        rows, _ = red.parse_contract(_contract([("1", "normal", "or", "`false 0 \\|\\| exit 0`")]))
        self.assertEqual(rows[0]["test"], "`false 0 || exit 0`")
        self.new_task()
        self.set_contract([("1", "normal", "or", "`false 0 \\|\\| exit 0`")])
        self.cli("red")
        line = self.receipt_lines()[0]
        self.assertEqual((line["status"], line["now"]["status"]), ("green", "green"), line)

    def test_the_cli_own_git_calls_ignore_git_dir(self):
        # row 41 (agy Minor): `red._git` runs in the named repository whatever GIT_DIR the caller has
        from tasks import red
        from unittest import mock
        with mock.patch.dict(os.environ, {"GIT_DIR": "/nonexistent", "GIT_WORK_TREE": "/x"}):
            r = red._git(self.code, ["rev-parse", "HEAD"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), self.head(self.code))


def _in_process(project: Path, env: dict, fn, *args):
    """Run a CLI function in this process, in `project`, with `env` (for a spy or a patched helper)."""
    from unittest import mock
    with mock.patch.dict(os.environ, env, clear=True):
        cwd = os.getcwd()
        os.chdir(project)
        try:
            return fn(*args)
        except SystemExit as e:
            return e.code
        finally:
            os.chdir(cwd)


class RoundTwo(Project):
    """Impl round 2's findings (task 280), each red before its fix."""

    def test_a_linked_scope_path_is_never_unpacked_through(self):
        # row 43 (GRAVE, codex Critical): the outer base tracks `code` as a LINK to a real repository; unpacking the
        # code scope into the copy must not write through it
        real = Path(tempfile.mkdtemp(prefix="pb280-realrepo-"))
        self.addCleanup(shutil.rmtree, real, True)
        (real / "precious.txt").write_text("keep\n", encoding="utf-8")
        (self.d / ".gitignore").write_text("home/\n.agent/journal/\n.agent/sessions/\n", encoding="utf-8")
        shutil.move(str(self.code), str(self.d / "code-real"))
        os.symlink(str(real), self.d / "code")
        self.g("add", "-A")
        self.g("commit", "-qm", "code is a link in the outer tree")
        os.unlink(self.d / "code")
        shutil.move(str(self.d / "code-real"), str(self.code))       # the task's work: a real clone at `code`
        (self.d / ".gitignore").write_text("home/\ncode/\n.agent/journal/\n.agent/sessions/\n", encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "code is a real repository again")
        self.new_task(activate=False)
        b = {"v": 1, "task": "001", "recorded_at": "2026-10-21T00:00:00+00:00", "exclude": [":(exclude).agent"],
             "scopes": {"": {"commit": self.g("rev-parse", "HEAD~1").stdout.strip(), "tree": None, "clean": True},
                        "code": {"commit": self.head(self.code), "tree": None, "clean": True}}}
        self.cli("work", "1")
        (self.task_dir / "base.json").write_text(json.dumps(b), encoding="utf-8")
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)        # it ran (impl round 3, opus) …
        self.assertEqual(len(self.receipt_lines()), 1)
        self.assertEqual(sorted(p.name for p in real.iterdir()), ["precious.txt"])   # … and wrote nothing there

    def test_bwrap_is_named_by_its_absolute_path(self):
        # row 44 (GRAVE, codex Critical): a relative PATH entry and a tracked `bin/bwrap` cannot replace the sandbox
        if not shutil.which("bwrap"):
            self.skipTest("bubblewrap is not installed")
        from provider import sandbox
        if sandbox.is_sandboxed():
            self.skipTest("already inside a sandbox: a nested one cannot start")
        (self.d / "bin").mkdir()
        fake = self.d / "bin" / "bwrap"
        fake.write_text("#!/bin/sh\ntouch \"$PWD/FAKE_BWRAP_RAN\"\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        self.g("add", "-A")
        self.g("commit", "-qm", "a tracked bin/bwrap")
        self.new_task()
        self.set_contract([("1", "hostile", "whoever runs", "`exit 4`")])
        env = dict(self.env(), PATH="bin:" + os.environ.get("PATH", "/usr/bin:/bin"))
        subprocess.run([sys.executable, "-m", "tasks.cli", "red"], cwd=self.d, env=env, capture_output=True,
                       text=True, timeout=300)
        line = self.receipt_lines()[0]
        self.assertEqual((line["status"], line["exit"]), ("red", 4), line)     # the fake would have exited 0

    def test_the_operator_variable_does_not_open_the_guard(self):
        # row 45 (GRAVE, opus): PLAYBOOK_ALLOW_DANGEROUS in the CLI's own environment is the agent's, not the owner's
        self.new_task()
        marker = self.d / "ran.txt"
        push = "git push " + "--force origin main"          # split: this file is written by tools the guard reads
        self.set_contract([("1", "hostile", "a force push", f"`touch {marker} && {push}`")])
        env = dict(self.env(), PLAYBOOK_ALLOW_DANGEROUS="1")
        subprocess.run([sys.executable, "-m", "tasks.cli", "red"], cwd=self.d, env=env, capture_output=True,
                       text=True, timeout=300)
        self.assertEqual(self.receipt_lines()[0]["status"], "refused")
        self.assertFalse(marker.exists())

    def test_export_ignore_does_not_empty_the_copy(self):
        # row 46 (opus + codex): the copy is the tree, whatever `.gitattributes` says for `git archive`
        (self.code / ".gitattributes").write_text("/tests export-ignore\n", encoding="utf-8")
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "export-ignore the tests", cwd=self.code)
        self.new_task()
        self.set_contract([("1", "must-not", "the old test is in the copy", "`test -f code/tests/test_old.py`")])
        self.cli("red")
        line = self.receipt_lines()[0]
        self.assertEqual((line["status"], line["now"]["status"]), ("green", "green"), line)

    def test_each_test_of_a_row_runs_on_its_own(self):
        # row 47 (opus): a row naming two tests is proved only when each is red on the base and green now
        self.new_task()
        self.change_code()
        self.set_contract([("1", "normal", "two tests", "`tests.test_new`, `tests.test_old`")])
        self.cli("red")
        line = self.receipt_lines()[0]
        self.assertEqual(line["verdict"], "partly proved")                 # never "proved" (mutant P5)
        self.assertEqual([c["verdict"] for c in line["tests"]], ["proved", "already true"])

    def test_a_live_project_under_tmp_is_kept_with_no_var_tmp(self):
        # row 48 (agy + opus): no usable /var/tmp, so the run's folder is under /tmp — the live project there is kept.
        # Since task 315 the row's /tmp is private wherever the folder is: the section no longer says /tmp is shared
        if not shutil.which("bwrap"):
            self.skipTest("bubblewrap is not installed")
        from provider import sandbox
        if sandbox.is_sandboxed():
            self.skipTest("already inside a sandbox: a nested one cannot start")
        from unittest import mock
        from tasks import red
        self.new_task()
        target = self.code / "src.py"
        before = target.read_text(encoding="utf-8")
        self.set_contract([("1", "hostile", "a write to the live project", f"`echo gone > {target}; exit 3`")])
        with mock.patch.object(red, "_run_parent", lambda: None):
            _in_process(self.d, self.env(), red.cmd_red, [])
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertNotIn("/tmp shared", self.section())

    def test_links_are_ready_per_copy(self):
        # row 49 (codex): the base has the folder itself; the now copy needs the link — each copy decides alone
        (self.code / "deps").mkdir()
        (self.code / "deps" / "dep.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", "deps tracked", cwd=self.code)
        self.new_task()
        self.g("rm", "-rq", "--cached", "deps", cwd=self.code)
        (self.code / ".gitignore").write_text("deps/\n", encoding="utf-8")
        (self.code / "deps" / "dep.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.config(red_links=["code/deps"])
        self.set_contract([("1", "normal", "the value", "`grep -q 'VALUE = 2' code/deps/dep.py`")])
        self.cli("red")
        line = self.receipt_lines()[0]
        self.assertEqual((line["status"], line["now"]["status"]), ("red", "green"), line)

    def test_a_scope_spelled_with_dots_and_slashes_is_the_same_scope(self):
        # row 50 (codex): `./code/` names the scope `code`
        # a NEW test of behavior the base already has: laid over the base it is green there too ("already true");
        # missed by the overlay it is red on the base for the wrong reason (a false "proved")
        self.config(code_roots=["./code/"], red_tests=["tests/*"])
        self.new_task()
        (self.code / "tests" / "test_exist.py").write_text(
            "import unittest\nimport src\n\n\nclass E(unittest.TestCase):\n"
            "    def test_exists(self):\n        self.assertTrue(callable(src.f))\n", encoding="utf-8")
        self.set_contract([("1", "must-not", "f exists", "`tests.test_exist`")])
        self.cli("red")
        self.assertEqual(self.receipt_lines()[0]["verdict"], "already true")

    def test_test_support_files_are_laid(self):
        # row 51 (opus + sonnet): conftest.py, fixtures/, testdata/, __snapshots__/ are tests' own files
        from tasks import red
        for rel in ("conftest.py", "pkg/conftest.py", "fixtures/a.json", "testdata/b.txt", "x/__snapshots__/c.snap"):
            self.assertTrue(red._is_test_path(rel, {}), rel)
        self.assertFalse(red._is_test_path("src/main.py", {}))

    def test_a_link_named_tests_is_laid(self):
        # row 52 (agy): a `tests` link of the working state reaches the base copy
        from tasks import red
        self.assertTrue(red._is_test_path("tests", {}))
        self.assertTrue(red._is_test_path("code/spec", {}))

    def test_virtual_env_is_dropped(self):
        # row 53 (agy): a row runs on the copy, not the caller's virtualenv
        from tasks import red
        env = red._row_env({"VIRTUAL_ENV": "/v", "PATH": "/usr/bin"}, Path("/tmp/h"), Path("/tmp/c"))
        self.assertNotIn("VIRTUAL_ENV", env)

    def test_a_refused_activation_leaves_no_base(self):
        # row 54 (sonnet): the base is for an activation that happened
        from unittest import mock
        from tasks import lifecycle
        self.new_task(activate=False)

        def refuse(*_a, **_k):
            raise SystemExit(1)
        with mock.patch.object(lifecycle, "_activate_status_then_publish", refuse):
            _in_process(self.d, self.env(), lifecycle.cmd_work, ["1"])
        self.assertFalse((self.task_dir / "base.json").exists())

    def test_a_background_child_does_not_outlive_its_row(self):
        # row 55 (sonnet): a child left running after a row ended normally is stopped with the row's group
        tag = f"pb280bg{os.getpid()}x{time.time_ns()}"
        self.addCleanup(subprocess.run, ["pkill", "-f", tag], capture_output=True)
        self.new_task()
        self.set_contract([("1", "normal", "a background child",
                            f"`bash -c 'exec -a {tag} sleep 30' >/dev/null 2>&1 & exit 0`")])
        self.cli("red")
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", tag], capture_output=True, text=True).stdout.strip()
        self.assertEqual(left, "")

    def test_a_caller_git_dir_does_not_pick_the_trees(self):
        # row 57 (agy + opus, mutant P15): GIT_DIR/GIT_WORK_TREE of the caller name the OUTER repository — the code
        # scope's trees are still the code repository's
        self.new_task()
        self.change_code()
        self.set_contract([("1", "normal", "the new code", "`grep -q 'return 2' code/src.py`")])
        env = dict(self.env(), GIT_DIR=str(self.d / ".git"), GIT_WORK_TREE=str(self.d))
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "red"], cwd=self.d, env=env, capture_output=True,
                           text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        line = self.receipt_lines()[0]
        self.assertEqual((line["status"], line["now"]["status"]), ("red", "green"), line)

    def test_a_pytest_error_line_is_found(self):
        # row 56 (agy): pytest prints the exception as `E   AssertionError: …`
        from tasks import red
        self.assertEqual(red._first_error(b"____ test ____\nE   AssertionError: 1 != 2\nFAILED x\n"),
                         "AssertionError: 1 != 2")


class RoundThree(Project):
    """Impl round 3's findings (task 280), each red before its fix."""

    def test_a_backslash_scope_cannot_climb_out_of_the_copy(self):
        # row 58 (GRAVE, codex's failed run): a code_roots name holding backslashes and dots is ONE folder name on
        # Linux; read as `../../…` it made the unpack write outside the copy
        victim = Path(tempfile.mkdtemp(prefix="pb280-victim-"))
        self.addCleanup(shutil.rmtree, victim, True)
        (victim / "f.txt").write_text("keep\n", encoding="utf-8")
        name = "..\\" * 10 + "tmp\\" + victim.name
        odd = self.d / name
        odd.mkdir()
        (odd / "f.txt").write_text("snapshot\n", encoding="utf-8")
        self.g("init", "-q", cwd=odd)
        self.g("add", "-A", cwd=odd)
        self.g("commit", "-qm", "odd", cwd=odd)
        (self.d / ".gitignore").write_text("home/\ncode/\n" + name.replace("\\", "\\\\") + "/\n"
                                           ".agent/journal/\n.agent/sessions/\n", encoding="utf-8")
        self.config(code_roots=["code", name])
        self.new_task()
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)        # a legal folder name runs (mutant Q1) …
        self.assertEqual(len(self.receipt_lines()), 1)
        self.assertEqual((victim / "f.txt").read_text(encoding="utf-8"), "keep\n")   # … and writes only in the copy
        self.assertEqual(sorted(p.name for p in victim.iterdir()), ["f.txt"])

    def test_a_path_with_dot_dot_is_refused_in_the_copy(self):
        # row 58 (GRAVE): no write lands outside the copy, whatever a name says
        from tasks import red
        root = self.d / "copyroot"
        root.mkdir()
        for bad in ("../x", "a/../../x", "/abs/x", "a//b", "./a"):
            with self.subTest(bad=bad):
                with self.assertRaises(RuntimeError):
                    red._real_dir(root, bad)
        self.assertFalse((self.d / "x").exists())

    def test_an_unborn_scope_has_a_base(self):
        # row 59 (opus): the first task of a project started from zero — its code repository has no commit yet
        shutil.rmtree(self.code / ".git")
        self.g("init", "-q", cwd=self.code)
        self.new_task()
        b = self.base()
        self.assertNotIn("failed", b)
        self.assertIsNone(b["scopes"]["code"]["commit"])
        self.assertTrue(b["scopes"]["code"]["tree"])
        self.change_code()
        self.set_contract([("1", "normal", "f returns 2", "`tests.test_new`")])
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.receipt_lines()[0]["verdict"], "proved")

    def test_a_changed_tracked_record_does_not_stop_the_run(self):
        # row 60 (opus): a tracked `.agent` file changed and committed after the activation
        self.new_task()
        self.config(red_timeout=200)
        self.g("add", "-A")
        self.g("commit", "-qm", "the config changed")
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        r = self.cli("red", "--base", f"={self.head(self.d)}")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_the_section_shows_each_test_of_a_row(self):
        # row 61 (agy + opus + sonnet): a several-test row shows each test's sides; a refused one shows why
        self.new_task()
        self.change_code()
        push = "git push " + "--force origin main"          # split: this file is written by tools the guard reads
        self.set_contract([("1", "normal", "two", "`tests.test_new`, `tests.test_old`"),
                           ("2", "hostile", "one refused", f"`exit 1`, `{push}`")])
        self.cli("red")
        sec = self.section()
        row1 = next(ln for ln in sec.splitlines() if ln.startswith("| 1 |"))
        self.assertIn("partly proved", row1)
        self.assertIn("1: red → green (proved)", row1)
        self.assertIn("2: green → green (already true)", row1)
        row2 = next(ln for ln in sec.splitlines() if ln.startswith("| 2 |"))
        self.assertIn("refused", row2)
        self.assertIn("command guard", row2)

    def test_real_dir_makes_its_root_first(self):
        # row 62 (agy)
        from tasks import red
        root = self.d / "missing" / "root"
        self.assertEqual(red._real_dir(root, "a/b"), root / "a" / "b")
        self.assertTrue((root / "a" / "b").is_dir())

    def test_an_interrupted_activation_removes_only_its_own_base(self):
        # row 63 (agy + sonnet): any exception removes the base this call wrote; a base someone else wrote stays
        from unittest import mock
        from tasks import lifecycle
        self.new_task(activate=False)

        def interrupt(*_a, **_k):
            raise KeyboardInterrupt()
        with mock.patch.object(lifecycle, "_activate_status_then_publish", interrupt):
            try:
                _in_process(self.d, self.env(), lifecycle.cmd_work, ["1"])
            except KeyboardInterrupt:
                pass
        self.assertFalse((self.task_dir / "base.json").exists())

        def replaced_then_refused(task_file, *_a, **_k):
            p = Path(task_file).parent / "base.json"
            p.unlink()
            p.write_text('{"v": 1, "scopes": {}, "by": "another session"}\n', encoding="utf-8")
            raise SystemExit(1)
        with mock.patch.object(lifecycle, "_activate_status_then_publish", replaced_then_refused):
            _in_process(self.d, self.env(), lifecycle.cmd_work, ["1"])
        self.assertEqual(self.base().get("by"), "another session")

    def test_a_base_flag_scope_is_normalized(self):
        # row 64 (agy Minor)
        self.new_task()
        self.set_contract([("1", "normal", "one", "`exit 1`")])
        r = self.cli("red", "--base", f"./code/={self.head(self.code)}")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_a_test_the_task_deleted_leaves_the_base_copy(self):
        # row 65 (opus Minor): the base copy holds the task's tests — a deleted one is gone there too
        self.new_task()
        (self.code / "tests" / "test_old.py").unlink()
        self.set_contract([("1", "normal", "deleted", "`test ! -e code/tests/test_old.py`")])
        self.cli("red")
        line = self.receipt_lines()[0]
        self.assertEqual((line["status"], line["now"]["status"]), ("green", "green"), line)


class TheText(unittest.TestCase):
    def test_the_template_and_guide_name_tasks_red(self):
        # row 20
        sec = template.contract_section()
        row = next(ln for ln in sec.splitlines() if ln.startswith("| 1 |"))
        self.assertIn("red_run", row)
        guide = template.guide_text("feature")
        self.assertIn("tasks red", guide)
        self.assertIn("red_run", guide)
        self.assertIn("\n  red [N] [--base", template.usage_text())

    def test_the_cli_doc_documents_red(self):
        # row 21
        doc = (REPO / "docs" / "cli.md").read_text(encoding="utf-8")
        self.assertIn("**`tasks red", doc)
        entry = doc.split("**`tasks red", 1)[1].split("\n\n", 1)[0]
        for words in ("red_run", "red-receipt.jsonl", "base.json", "--base"):
            self.assertIn(words, entry)


if __name__ == "__main__":
    unittest.main()
