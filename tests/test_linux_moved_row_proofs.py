#!/usr/bin/env python3
"""Linux proofs for the ledger rows the Linux-only decision moved to PLAN S11 (task 169).

Seven rows lost their only live evidence when macOS and Windows stopped being
claimed (owner decision 2026-10-08); the plan gives them to S11: "each is verified
by a test or carries an owner WAIVER before S13". Each class below starts from the
sentence of one row's `missing_evidence_or_limitation` and crosses the boundary that
sentence names — a real launcher, a real hook, real bubblewrap. The method is task
148's: every test here was watched failing on one deliberate break of the product
line it depends on, in a throwaway copy of the tree (the failures are in task 169's
`## Red-first` table).

What a child process must not inherit from whoever runs the suite is dropped in
`child_env`: `PLAYBOOK_SANDBOXED` above all — it is the nesting signal, with it the
launchers deliberately start no sandbox, and a "real bubblewrap" test would then
contain nothing (plan panel, task 169; met for real in task 167).

Run: python3 -m unittest tests.test_linux_moved_row_proofs
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"
for _p in (str(_HERE.parent), str(PLUGIN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests._bashcheck import bash_or_skip  # noqa: E402
from tests._bwrap_standin import bwrap_usable  # noqa: E402

_NOT_INHERITED = ("PLAYBOOK_SANDBOXED", "PYTHONPATH", "PLAYBOOK_SESSION_ID", "PLAYBOOK_PROJECT_DIR",
                  "CLAUDE_PROJECT_DIR", "CLAUDE_ENV_FILE", "PLAYBOOK_PROC_ROOT")


def child_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _NOT_INHERITED}
    env.update(extra)
    return env


def tree_digest(root: Path) -> dict:
    """path → sha256 of every regular file under `root` (links by their text)."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            out[rel] = "link:" + os.readlink(p)
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def git(project: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", *args],
                       cwd=project, capture_output=True, text=True, check=True)
    return r.stdout.strip()


# --------------------------------------------------------------------------- #
# PB-SANDBOX-WRITE — "Full worker-mode .git mutation matrix under real bubblewrap
# is incomplete."
# --------------------------------------------------------------------------- #
# What a stand-in agent tries inside the sandbox; one line of answer per attempt.
_AGENT_TRIES = r"""#!/bin/sh
try() { name="$1"; shift; if "$@" 2>/dev/null; then echo "$name: WROTE"; else echo "$name: denied"; fi; }
try project        sh -c 'printf x > "$PWD/in-project"'
try git-new-file   sh -c 'printf x > "$PWD/.git/new-file"'
try git-config     sh -c 'printf x >> "$PWD/.git/config"'
try git-hook       sh -c 'printf x > "$PWD/.git/hooks/pre-commit"'
try git-head       sh -c 'printf "ref: refs/heads/evil\n" > "$PWD/.git/HEAD"'
try git-ref-delete sh -c 'rm "$PWD"/.git/refs/heads/*'
try allowed        sh -c 'test -d "$PWD/allowed" && printf x > "$PWD/allowed/wrote"'
"""
_GIT_TRIES = ("git-new-file", "git-config", "git-hook", "git-head", "git-ref-delete")


class _SandboxedProject(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "parent" / "proj"
        self.project.mkdir(parents=True)
        git(self.project, "init", "-q")
        git(self.project, "commit", "-q", "--allow-empty", "-m", "first")   # so a ref file exists
        (self.project / ".git" / "hooks").mkdir(exist_ok=True)
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        agent = self.bindir / "claude"
        agent.write_text(_AGENT_TRIES, encoding="utf-8")
        agent.chmod(0o755)

    def launch(self, *flags: str) -> dict:
        """The shipped launcher, a real sandbox, the stand-in agent → {attempt: outcome}."""
        r = subprocess.run(
            [bash_or_skip(), str(SCRIPTS / "sandbox"), "--project-root", str(self.project),
             "--agent", "claude", *flags, "--prompt", "hello"],
            cwd=self.project, capture_output=True, text=True, timeout=120,
            env=child_env(PATH=f"{self.bindir}{os.pathsep}{os.environ.get('PATH', '')}"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = dict(line.split(": ", 1) for line in r.stdout.splitlines() if ": " in line)
        self.assertIn("project", got, r.stdout + r.stderr)        # the stand-in really ran
        return got


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class TheSandboxKeepsGitReadOnly(_SandboxedProject):
    """Worker mode: the project is writable and `.git` is not — whatever `--rw` says."""

    def _assert_git_untouched(self, *flags: str):
        before = tree_digest(self.project / ".git")
        got = self.launch(*flags)
        self.assertEqual(got["project"], "WROTE", (flags, got))
        self.assertEqual({k: got[k] for k in _GIT_TRIES}, dict.fromkeys(_GIT_TRIES, "denied"), flags)
        self.assertEqual(tree_digest(self.project / ".git"), before, flags)

    def test_worker_mode_writes_the_project_and_cannot_change_git(self):
        self._assert_git_untouched()

    def test_a_writable_path_that_covers_git_does_not_open_it(self):
        # plan panel, task 169 (codex-high, codex-medium): the extra writable binds were
        # laid AFTER the read-only `.git` bind, and the later bind won — `--rw <project>`
        # was enough to make `.git` writable.
        for rw in (self.project, self.project.parent, self.project / ".git",
                   self.project / ".git" / "hooks"):
            with self.subTest(rw=str(rw.relative_to(self.tmp))):
                self._assert_git_untouched("--rw", str(rw))

    def test_control_a_writable_path_beside_git_still_takes_writes(self):
        # the other side: the fix must not turn every `--rw` path read-only
        allowed = self.project / "allowed"
        allowed.mkdir()
        got = self.launch("--ro-project", "--rw", str(allowed))
        self.assertEqual((got["project"], got["allowed"]), ("denied", "WROTE"), got)
        self.assertEqual({k: got[k] for k in _GIT_TRIES}, dict.fromkeys(_GIT_TRIES, "denied"))
        self.assertFalse((self.project / "in-project").exists())
        self.assertEqual((allowed / "wrote").read_text(encoding="utf-8"), "x")


class TheGitBindIsLaidLast(unittest.TestCase):
    """The same rule where no sandbox can start: in the argv, the read-only `.git`
    bind comes after every writable bind, so it wins whatever they cover."""

    def test_git_is_bound_read_only_after_every_writable_bind(self):
        from provider.sandbox import build_bwrap_argv
        project = Path(tempfile.mkdtemp()).resolve() / "proj"
        (project / ".git" / "hooks").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, project.parent, ignore_errors=True)
        gitdir = str(project / ".git")
        for writable in (True, False):
            for rw in (None, project, project.parent, project / ".git", project / ".git" / "hooks"):
                with self.subTest(project_writable=writable, rw=rw and rw.name):
                    argv = build_bwrap_argv(project, gitdir, ["true"], [str(rw)] if rw else None,
                                            project_writable=writable)
                    binds = [(i, a, argv[i + 1]) for i, a in enumerate(argv[:-2])
                             if a in ("--bind", "--ro-bind")]
                    last_writable = max(i for i, kind, _ in binds if kind == "--bind")
                    git_ro = [i for i, kind, src in binds if kind == "--ro-bind" and src == gitdir]
                    self.assertTrue(git_ro, "no read-only bind of .git at all")
                    self.assertGreater(max(git_ro), last_writable,
                                       "a writable bind is laid after .git's read-only bind")


if __name__ == "__main__":
    unittest.main()
