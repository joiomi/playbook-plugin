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
        shutil.rmtree(self.code / ".git")
        self.g("init", "-q", cwd=self.code)                     # a repository with no commit yet
        self.new_task()
        b = self.base()
        self.assertIn("failed", b)
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
        self.assertRegex(sec, r"\| 1 \| normal \| `tests\.test_new` \| red \(exit [1-9][^|]*\| green \(exit 0[^|]*\| proved \|")
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
        self.assertEqual(sum(1 for r in runs if r in sec), 1)
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
                                    "GIT_CEILING_DIRECTORIES": "/tmp/c"})

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
        self.assertIn("AssertionError: 1 != 2", self.section())
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
