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
                  "CLAUDE_PROJECT_DIR", "CLAUDE_ENV_FILE", "PLAYBOOK_PROC_ROOT", "PLAYBOOK_NO_BASHLOG", "BASH_ENV")


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


# --------------------------------------------------------------------------- #
# PB-SESSION-CLEANUP — "No executable integration proof crosses the real
# SessionStart GC boundary; the policy is proved at the predicate level only."
#
# The SessionStart side has such a proof, never bound until task 169: fixture
# scenario S18 (tests/wrapper-multiuser-fixture.sh) runs the real hook over every
# case and carries its own mutant controls. What nothing showed is the other
# sweeper at ITS real boundary: that an actual `tasks` invocation runs the policy
# over the project's tree. A2 of S18 and the unit tests call the function.
# --------------------------------------------------------------------------- #
class TheCliSweepsSessionsAtARealInvocation(unittest.TestCase):
    STALE = 1577836800          # 2020-01-01: far from the 24 h boundary on purpose

    def setUp(self):
        from tests._fake_agent import agent_proc_root, spawn_fake_agent, stop
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.sessions = self.project / ".agent" / "sessions"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        self.sessions.mkdir()
        (self.tmp / "home").mkdir()
        # our own session: a `pid-N` id is honoured only while N is a live AGENT
        # process, and only an agent the walk does not contradict (task 106) — so a
        # stand-in agent, and a /proc fixture that lists it alone (the same answer
        # here, under a real agent, and on CI, where there is none)
        (self.tmp / "agent").mkdir()
        agent = spawn_fake_agent(self.tmp / "agent")
        self.addCleanup(stop, agent)
        self.own = f"pid-{agent.pid}"
        self.proc_root = agent_proc_root(self.tmp, agent.pid)
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        try:
            os.kill(gone.pid, 0)
        except OSError:
            pass
        else:
            self.skipTest("the harvested pid is alive again (pid reuse)")
        self.live, self.dead = f"pid-{os.getpid()}", f"pid-{gone.pid}"
        for name, stale in ((self.own, True), (self.live, True), (self.dead, False),
                            ("pid-12ab", False), ("uuid-stale", True), ("uuid-fresh", False)):
            d = self.sessions / name
            d.mkdir()
            (d / "current_state").write_text("001\n", encoding="utf-8")
            if stale:
                os.utime(d / "current_state", (self.STALE, self.STALE))
        (self.sessions / "stray-file").write_text("", encoding="utf-8")
        self.precious = self.tmp / "precious"
        self.precious.mkdir()
        (self.precious / "keepme.txt").write_text("PRECIOUS\n", encoding="utf-8")
        os.symlink(self.precious, self.sessions / "pid-77zz")     # a name the policy calls dead

    def _tasks(self, *args):
        return subprocess.run(
            [bash_or_skip(), str(SCRIPTS / "tasks"), *args], cwd=self.project,
            capture_output=True, text=True, timeout=120,
            env=child_env(HOME=str(self.tmp / "home"), PLAYBOOK_SESSION_ID=self.own,
                          PLAYBOOK_PROC_ROOT=self.proc_root))

    def _names(self):
        return sorted(p.name for p in self.sessions.iterdir())

    def test_a_listing_keeps_the_live_set_and_spares_a_links_target(self):
        r = self._tasks("list")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # kept: our own (stale pointer), a live foreign pid (stale pointer), a fresh
        # legacy name, the stray file, the link itself. Gone: the dead pid (fresh
        # pointer), the non-numeric pid name, the stale legacy name.
        self.assertEqual(self._names(), sorted([self.own, self.live, "uuid-fresh", "stray-file", "pid-77zz"]))
        self.assertEqual((self.precious / "keepme.txt").read_text(encoding="utf-8"), "PRECIOUS\n")

    def test_control_an_invocation_that_returns_before_the_sweep_removes_nothing(self):
        # not every invocation sweeps: `--version` answers before it (so do `--help`,
        # `dashboard` and the dry runs) — which also shows the tree above is not
        # swept by anything but the command under test
        before = self._names()
        r = self._tasks("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._names(), before)


# --------------------------------------------------------------------------- #
# PB-INIT-SCAFFOLD — "No packaged-install test"; "no test establishes that an
# initialized machine runs the shipped logger"; and (task 100) no test asserted the
# wrappers on a clean init.
# --------------------------------------------------------------------------- #
class InitFromAPackagedInstall(unittest.TestCase):
    """The plugin as Claude Code installs it — a versioned directory under the cache,
    named by `installed_plugins.json` — and ITS init, run in an empty project."""
    INSTALLED, NEWER_BESIDE_IT = "9.9.8", "9.9.9"

    def setUp(self):
        import json
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        plugins = self.home / ".claude" / "plugins"
        cache = plugins / "cache" / "a-marketplace" / "playbook"
        for version in (self.INSTALLED, self.NEWER_BESIDE_IT):
            shutil.copytree(PLUGIN, cache / version, ignore=shutil.ignore_patterns("__pycache__"))
            manifest = cache / version / ".claude-plugin" / "plugin.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["version"] = version
            manifest.write_text(json.dumps(data), encoding="utf-8")
        self.packaged = cache / self.INSTALLED
        # the INSTALLED copy is the OLDER one on purpose: a wrapper that scanned the
        # cache instead of reading the manifest would answer from the newer directory
        self.manifest = plugins / "installed_plugins.json"
        self.manifest.write_text(json.dumps({"version": 2, "plugins": {"playbook@a-marketplace": [
            {"scope": "user", "installPath": str(self.packaged), "version": self.INSTALLED,
             "lastUpdated": "2026-01-01T00:00:00.000Z"}]}}), encoding="utf-8")
        self.project = self.tmp / "proj"
        self.project.mkdir()

    def _run(self, argv, **extra):
        return subprocess.run([str(a) for a in argv], cwd=self.project, capture_output=True, text=True,
                              timeout=180, env=child_env(HOME=str(self.home), **extra))

    def _init(self):
        r = self._run([bash_or_skip(), self.packaged / "scripts" / "init", "proj"])
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        return r

    def test_init_creates_everything_the_row_lists(self):
        import json
        self._init()
        p, claude = self.project, self.home / ".claude"
        self.assertTrue((p / ".agent" / "tasks").is_dir())                                   # task structure
        self.assertIn("TodoWrite", (p / ".claude" / "settings.json").read_text(encoding="utf-8"))   # settings
        self.assertIn("panel_required_for", json.loads((p / ".agent" / "config.json").read_text(encoding="utf-8")))
        self.assertIn(".claude/bin/tasks", (p / "CLAUDE.md").read_text(encoding="utf-8"))    # CLAUDE.md
        self.assertEqual((p / "MIND_MAP.md").read_text(encoding="utf-8").splitlines()[0], "# Mind Map — proj")
        ignored = (p / ".gitignore").read_text(encoding="utf-8").splitlines()                # gitignore block
        for line in ("# --- playbook runtime state (machine-local; managed by playbook init) ---",
                     ".agent/sessions/", ".agent/bash_history", ".agent/chat_log.md"):
            self.assertIn(line, ignored)
        # logger wiring: the copy init deployed IS the shipped logger, and the setting names it
        self.assertEqual((claude / "bash-log.sh").read_bytes(),
                         (self.packaged / "scripts" / "bash-log.sh").read_bytes())
        self.assertEqual(json.loads((claude / "settings.json").read_text(encoding="utf-8"))["env"]["BASH_ENV"],
                         str(claude / "bash-log.sh"))

    def _version_through_the_wrapper(self):
        r = self._run([self.project / ".claude" / "bin" / "tasks", "--version"])
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_the_wrappers_exist_and_answer_from_the_installed_copy(self):
        self._init()
        for name in ("tasks", "monitor", "sandbox"):
            wrapper = self.project / ".claude" / "bin" / name
            self.assertTrue(wrapper.is_file() and os.access(wrapper, os.X_OK), name)
        self.assertEqual(self._version_through_the_wrapper(), self.INSTALLED)

    def test_control_without_the_manifest_the_wrapper_falls_back_to_the_newest_directory(self):
        # the other side of the test above: what it would have answered had it scanned
        self._init()
        self.manifest.unlink()
        self.assertEqual(self._version_through_the_wrapper(), self.NEWER_BESIDE_IT)

    def _logged(self, **extra):
        import json
        setting = json.loads((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"))["env"]["BASH_ENV"]
        r = self._run([bash_or_skip(), "-c", "echo reached-the-history >/dev/null"], BASH_ENV=setting, **extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        history = self.project / ".agent" / "bash_history"
        return history.read_text(encoding="utf-8") if history.exists() else ""

    def test_a_shell_started_with_the_setting_init_wrote_reaches_the_history(self):
        # plan panel (both codex seats): bytes and a setting do not show that the
        # logger RUNS — a shell started the way the harness starts one has to log
        self._init()
        self.assertRegex(self._logged(),
                         r"(?m)^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \| AGENT \| echo reached-the-history > /dev/null$")

    def test_control_a_shell_that_opts_out_leaves_no_line(self):
        self._init()
        self.assertNotIn("reached-the-history", self._logged(PLAYBOOK_NO_BASHLOG="1"))

    def test_init_replaces_an_older_logger_copy(self):
        (self.home / ".claude").mkdir(exist_ok=True)
        (self.home / ".claude" / "bash-log.sh").write_text("# an older logger\n", encoding="utf-8")
        self._init()
        self.assertEqual((self.home / ".claude" / "bash-log.sh").read_bytes(),
                         (self.packaged / "scripts" / "bash-log.sh").read_bytes())


if __name__ == "__main__":
    unittest.main()
