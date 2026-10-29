"""A temporary project for the CLI's tests: an outer git repository with a nested `code_roots` repository `code/`, a
fake HOME and one task (moved from tests/test_red_receipt.py, part 1 of the red receipt, when task 342 removed that
file — its fixture stays for the tests that use it). Pure stdlib.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parent
_PLAYBOOK = REPO / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))

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
