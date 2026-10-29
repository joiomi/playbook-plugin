"""The verify command runs without git's repository variables (task 336, grave C of DESIGN-PLAYBOOK-2.md §7).

Started from a git hook (`pre-commit`) or `git rebase -x`, `tasks verify` and the close inherit `GIT_DIR`,
`GIT_INDEX_FILE` and their kin; a suite that makes temporary repositories then commits into the real one and can write
`core.bare = true` (audit 5's blind session: 6,225 processes, out of memory). `skills/merge/merge-verify.py` — the one
runner the close, `tasks verify` and the merge push gate share — now drops the same eight variables as
`verify_memory._GIT_ENV_DROP` minus `GIT_CEILING_DIRECTORIES`, which only stops git from climbing up and is kept;
every other variable passes.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins" / "playbook"
MERGE_VERIFY = PLUGIN / "skills" / "merge" / "merge-verify.py"

DROPPED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE")


def _load_merge_verify():
    spec = importlib.util.spec_from_file_location("_pb_merge_verify_under_test", MERGE_VERIFY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Env:
    """Set variables for the test's duration, restoring them after."""

    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _seen(text: str) -> "set[str]":
    return {line.split("=", 1)[0] for line in text.splitlines() if "=" in line}


class Runners(unittest.TestCase):
    def setUp(self):
        self.mv = _load_merge_verify()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.hook_vars = {k: str(self.root / ("x-" + k.lower())) for k in DROPPED}

    def test_capture_drops_the_repository_variables(self):
        with _Env(**self.hook_vars):
            rc, out = self.mv.run_command_capture("env", str(self.root), timeout_secs=60)
        self.assertEqual(rc, 0, out)
        self.assertEqual(_seen(out) & set(DROPPED), set())

    def test_main_drops_the_repository_variables(self):
        (self.root / ".agent").mkdir()
        (self.root / ".agent" / "config.json").write_text(
            '{"merge_verify": {"command": "env > seen.txt"}}', encoding="utf-8")
        with _Env(**self.hook_vars):
            rc = self.mv.main(["-C", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(_seen((self.root / "seen.txt").read_text(encoding="utf-8")) & set(DROPPED), set())

    def test_other_variables_pass(self):
        keep = {"PB_TEST_PROJECT_VAR": "1", "GIT_AUTHOR_NAME": "Someone", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
                "GIT_CONFIG_COUNT": "0", "GIT_CEILING_DIRECTORIES": str(self.root)}
        with _Env(**keep, **self.hook_vars):
            rc, out = self.mv.run_command_capture("env", str(self.root), timeout_secs=60)
        self.assertEqual(rc, 0, out)
        seen = _seen(out)
        for k in ("PATH", "HOME", *keep):
            self.assertIn(k, seen)

    def test_one_list(self):
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        from tasks import verify_memory
        self.assertEqual(tuple(self.mv.GIT_REPOSITORY_VARIABLES), DROPPED)
        self.assertEqual(tuple(verify_memory._GIT_ENV_DROP), DROPPED + ("GIT_CEILING_DIRECTORIES",))


class EndToEnd(unittest.TestCase):
    """Row 3: the harm itself. A real repository R; `tasks verify` started with R's `GIT_DIR` and `GIT_INDEX_FILE`, as
    a `pre-commit` hook exports them; the project's verify makes a temporary repository and commits in it. R keeps
    its HEAD, its index and its config."""

    def test_a_verify_under_git_hook_variables_leaves_the_real_repository(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "real"
            real.mkdir()
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
            subprocess.run(["git", "init", "-q", str(real)], check=True)
            (real / "a.txt").write_text("a\n", encoding="utf-8")
            subprocess.run([*git, "-C", str(real), "add", "a.txt"], check=True)
            subprocess.run([*git, "-C", str(real), "commit", "-q", "-m", "one"], check=True)
            head = subprocess.run(["git", "-C", str(real), "rev-parse", "HEAD"], capture_output=True, text=True,
                                  check=True).stdout.strip()
            config = (real / ".git" / "config").read_bytes()
            index = (real / ".git" / "index").read_bytes()
            project = Path(d) / "project"
            (project / ".agent").mkdir(parents=True)
            verify = ("t=$(mktemp -d) && cd \"$t\" && git init -q && git -c user.name=t -c user.email=t@t "
                      "-c commit.gpgsign=false commit -q --allow-empty -m from-the-suite && git config core.bare true")
            (project / ".agent" / "config.json").write_text(
                '{"verify": {"_always": [%s]}}' % __import__("json").dumps(verify), encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="pid-git-env-test",
                       GIT_DIR=str(real / ".git"), GIT_INDEX_FILE=str(real / ".git" / "index"))
            env.pop("BASH_ENV", None)
            r = subprocess.run([sys.executable, "-m", "tasks.cli", "verify"], cwd=project, env=env, text=True,
                               capture_output=True, timeout=300)
            self.assertIn("[PASS]", r.stdout + r.stderr, r.stdout + r.stderr)
            after = subprocess.run(["git", "-C", str(real), "rev-parse", "HEAD"], capture_output=True, text=True,
                                   env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                                   check=True).stdout.strip()
            self.assertEqual(after, head)
            self.assertEqual((real / ".git" / "config").read_bytes(), config)
            self.assertEqual((real / ".git" / "index").read_bytes(), index)


if __name__ == "__main__":
    unittest.main()
