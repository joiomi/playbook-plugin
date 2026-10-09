#!/usr/bin/env python3
"""The sandbox LAUNCHER refuses to start an agent when there is no containment (task 164).

With no usable bubblewrap the launcher used to start the agent with its permission-bypass
flag and nothing around it, and said nothing. Owner ruling 2026-10-09: it refuses instead —
a run, a `--prompt` run and `--print-argv` alike — and says how to install bubblewrap.

Only the launcher's entry point (`provider.sandbox._main`, reached through `scripts/sandbox`,
`python3 -m provider.sandbox` and the monitor) changes. The judge paths call `run()` directly
and keep the uncontained path with its warning: `tests/test_sandbox_uncontained_fallback.py`
pins that, untouched.

Two layers: `_main` with the backend switched by mocks, and the REAL launcher run with a PATH
that has no `bwrap` and a fake agent that leaves a marker file if it is ever executed.

Run: python3 -m unittest tests.test_sandbox_refuses_uncontained
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins/playbook"
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(PLUGIN))
from provider import sandbox, subagent  # noqa: E402
from tests._bashcheck import bash_or_skip  # noqa: E402

_REAL_WHICH = shutil.which
INSTALL = "apt install bubblewrap"


class _Launcher(unittest.TestCase):
    """Run `_main` with a chosen backend; every way of starting an agent is replaced by a
    recorder, so "nothing was launched" is an observation and not an absence of a crash."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="pb-refuse-")).resolve()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def main(self, argv, *, bwrap=None, start_error=None, nested=False):
        """`bwrap`: the path `which("bwrap")` answers (None = not installed). `start_error`:
        what the start probe reports (None = it starts). Returns (rc, stdout, stderr, calls)."""
        calls = []

        def which(name, *a, **k):
            return bwrap if name == "bwrap" else _REAL_WHICH(name, *a, **k)

        class _Started(types.SimpleNamespace):
            def __iter__(self):            # `--stream` iterates what stream_subagent returns
                return iter(())

        def launched(kind):
            def _f(*_a, **_k):
                calls.append(kind)
                return _Started(returncode=0, text="agent output", stdout="", stderr="")
            return _f

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sandbox, "is_sandboxed", return_value=nested), \
                mock.patch.object(sandbox.shutil, "which", side_effect=which), \
                mock.patch.object(sandbox, "bwrap_start_error", return_value=start_error, create=True), \
                mock.patch.object(sandbox, "run", side_effect=launched("run")), \
                mock.patch.object(sandbox, "popen", side_effect=launched("popen")), \
                mock.patch.object(subagent, "run_subagent", side_effect=launched("run_subagent")), \
                mock.patch.object(subagent, "stream_subagent", side_effect=launched("stream_subagent")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sandbox._main(["--project-root", str(self.project), *argv])
        return rc, out.getvalue(), err.getvalue(), calls

    def assertRefused(self, result, *needles):
        rc, out, err, calls = result
        self.assertEqual(calls, [], f"an agent was started although there is no containment: {calls}")
        self.assertEqual(rc, 2, f"expected the launcher's refusal (exit 2), got {rc}; stderr: {err!r}")
        self.assertEqual(out, "", "a refusal prints nothing on stdout — no argv, no agent output")
        for n in ("bubblewrap", *needles):
            self.assertIn(n, err)


class NoBubblewrapInstalled(_Launcher):
    def test_a_run_is_refused_and_says_how_to_install(self):
        self.assertRefused(self.main(["--agent", "claude", "--", "-p", "hi"]), "not installed", INSTALL)

    def test_a_prompt_run_is_refused(self):
        self.assertRefused(self.main(["--agent", "claude", "--prompt", "hi"]), INSTALL)

    def test_a_streamed_prompt_run_is_refused(self):
        self.assertRefused(self.main(["--agent", "claude", "--prompt", "hi", "--stream"]), INSTALL)

    def test_print_argv_is_refused_and_shows_no_argv(self):
        # the preview must not print the bare argv as if it were a wrapped one
        self.assertRefused(self.main(["--agent", "claude", "--print-argv", "--", "-p", "hi"]), INSTALL)

    def test_print_argv_of_a_prompt_run_is_refused(self):
        self.assertRefused(self.main(["--agent", "claude", "--prompt", "hi", "--print-argv"]), INSTALL)

    def test_the_read_only_observer_shape_is_refused(self):
        # what scripts/monitor-lib/launch-monitor execs
        self.assertRefused(self.main(["--agent", "claude", "--ro-project", "--keep-records",
                                      "--rw", str(self.project), "--", "--safe-mode", "seed"]), INSTALL)

    def test_the_message_states_what_would_have_happened(self):
        _, _, err, _ = self.main(["--agent", "claude", "--", "-p", "hi"])
        self.assertIn("refusing", err.lower())
        self.assertIn("permission prompts off", err)

    def test_the_inspection_flags_still_answer(self):
        rc, out, err, calls = self.main(["--list-agents"])
        self.assertEqual((rc, calls), (0, []), err)
        self.assertIn("claude", out)
        rc, out, err, calls = self.main(["--list-models"])
        self.assertEqual((rc, calls), (0, []), err)
        self.assertIn("opus", out)
        with self.assertRaises(SystemExit) as cm:
            self.main(["--help"])
        self.assertEqual(cm.exception.code, 0)


class InsideASandbox(_Launcher):
    def test_a_nested_run_is_not_refused(self):
        # the outer sandbox already contains it; `run()` re-uses that (the nesting guard)
        rc, out, err, calls = self.main(["--agent", "claude", "--", "-p", "hi"], nested=True)
        self.assertEqual((rc, calls), (0, ["run"]), err)


class BubblewrapThatCannotStart(_Launcher):
    WHY = "bwrap: setting up uid map: Permission denied"

    def test_it_is_refused_with_bubblewraps_own_error(self):
        r = self.main(["--agent", "claude", "--", "-p", "hi"], bwrap="/usr/bin/bwrap", start_error=self.WHY)
        self.assertRefused(r, self.WHY, "/usr/bin/bwrap", "could not start")
        self.assertNotIn(INSTALL, r[2], "it IS installed — the install hint would mislead")

    def test_print_argv_is_refused_too(self):
        self.assertRefused(self.main(["--agent", "claude", "--print-argv", "--", "-p", "hi"],
                                     bwrap="/usr/bin/bwrap", start_error=self.WHY), self.WHY)


class WorkingBubblewrap(_Launcher):
    """The negative control of everything above: the same calls DO launch when the cage exists."""

    def test_a_run_is_launched(self):
        rc, out, err, calls = self.main(["--agent", "claude", "--", "-p", "hi"], bwrap="/usr/bin/bwrap")
        self.assertEqual((rc, calls), (0, ["run"]), err)

    def test_a_prompt_run_is_launched(self):
        rc, out, err, calls = self.main(["--agent", "claude", "--prompt", "hi"], bwrap="/usr/bin/bwrap")
        self.assertEqual((rc, calls), (0, ["run_subagent"]), err)

    def test_print_argv_prints_the_wrapped_argv_and_launches_nothing(self):
        rc, out, err, calls = self.main(["--agent", "claude", "--print-argv", "--", "-p", "hi"],
                                        bwrap="/usr/bin/bwrap")
        self.assertEqual((rc, calls), (0, []), err)
        self.assertEqual(out.splitlines()[0], "bwrap")


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class TheStartProbe(unittest.TestCase):
    """`bwrap_start_error(exe)`: None when the binary starts a trivial sandbox, otherwise ONE
    line — the binary's own last stderr line where it gave one."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb-probe-"))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_a_binary_that_starts_reports_nothing(self):
        self.assertIsNone(sandbox.bwrap_start_error(str(_script(self.d / "bwrap", "exit 0\n"))))

    def test_a_failing_binary_reports_its_own_last_line(self):
        exe = _script(self.d / "bwrap", "echo 'bwrap: first line' >&2\necho 'bwrap: No permissions to create new namespace' >&2\nexit 1\n")
        self.assertEqual(sandbox.bwrap_start_error(str(exe)), "bwrap: No permissions to create new namespace")

    def test_a_silent_failure_reports_the_exit_code(self):
        self.assertIn("7", sandbox.bwrap_start_error(str(_script(self.d / "bwrap", "exit 7\n"))))

    def test_a_binary_that_cannot_be_run_is_an_error_not_a_crash(self):
        self.assertIsInstance(sandbox.bwrap_start_error(str(self.d / "missing-bwrap")), str)

    def test_the_probe_asks_for_the_mounts_every_launch_uses(self):
        # read-only root, /proc and /dev are the first mounts of build_bwrap_argv: a host that
        # refuses them refuses the real launch too
        log = self.d / "argv"
        exe = _script(self.d / "bwrap", f'printf "%s\\n" "$@" > "{log}"\nexit 0\n')
        self.assertIsNone(sandbox.bwrap_start_error(str(exe)))
        asked = log.read_text(encoding="utf-8").split("\n")
        launch = sandbox.build_bwrap_argv(self.d, None, ["true"], None, project_writable=True)
        self.assertEqual(asked[:7], launch[1:8])

    def test_the_probe_runs_a_command_given_by_absolute_path(self):
        # a PATH without `true` must not make a working bubblewrap look broken
        log = self.d / "argv"
        exe = _script(self.d / "bwrap", f'printf "%s\\n" "$@" > "{log}"\nexit 0\n')
        with mock.patch.dict(os.environ, {"PATH": str(self.d)}):
            self.assertIsNone(sandbox.bwrap_start_error(str(exe)))
        command = [a for a in log.read_text(encoding="utf-8").split("\n") if a][7]
        self.assertTrue(os.path.isabs(command) and os.access(command, os.X_OK), command)

    @unittest.skipUnless(shutil.which("bwrap"), "bwrap not installed")
    def test_the_real_bubblewrap_here_starts_or_the_launcher_refuses(self):
        # LIVE, on whatever host this is. Where bubblewrap starts there is nothing more to say;
        # where it cannot (user namespaces denied) the launcher must refuse with that error —
        # the suite passes on both kinds of host.
        real = shutil.which("bwrap")
        why = sandbox.bwrap_start_error(real)
        if why is None:
            with mock.patch.dict(os.environ, {"PATH": "/nonexistent-dir-for-this-test"}):
                self.assertIsNone(sandbox.bwrap_start_error(real),
                                  "a working bubblewrap looked broken because PATH had no `true`")
            return
        with mock.patch.object(sandbox, "is_sandboxed", return_value=False):
            self.assertIn(why, sandbox.launch_refusal() or "")


class TheLaunchItselfFailsClosed(unittest.TestCase):
    """`launch_refusal()` is the check that explains; the launch must not rest on it alone. If
    bubblewrap is gone by the time the argv is built, the launcher's own launches still refuse
    (`require_containment`) instead of exec'ing the bare agent."""

    def _main(self, argv):
        project = Path(tempfile.mkdtemp(prefix="pb-toctou-")).resolve()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)

        def never(*a, **k):
            raise AssertionError(f"an agent was executed with no containment: {a[:1]}")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sandbox, "is_sandboxed", return_value=False), \
                mock.patch.object(sandbox.shutil, "which",
                                  side_effect=lambda n, *a, **k: None if n == "bwrap" else _REAL_WHICH(n, *a, **k)), \
                mock.patch.object(sandbox, "launch_refusal", return_value=None, create=True), \
                mock.patch.object(sandbox.subprocess, "run", side_effect=never), \
                mock.patch.object(sandbox.subprocess, "Popen", side_effect=never), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sandbox._main(["--project-root", str(project), *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_a_run_whose_bubblewrap_vanished_after_the_check_is_refused(self):
        rc, out, err = self._main(["--agent", "claude", "--", "-p", "hi"])
        self.assertEqual((rc, out), (2, ""), err)
        self.assertIn("no containment", err)

    def test_a_prompt_run_whose_bubblewrap_vanished_is_refused(self):
        rc, out, err = self._main(["--agent", "claude", "--prompt", "hi"])
        self.assertEqual((rc, out), (2, ""), err)
        self.assertIn("no containment", err)

    def test_a_streamed_prompt_run_whose_bubblewrap_vanished_is_refused(self):
        rc, out, err = self._main(["--agent", "claude", "--prompt", "hi", "--stream"])
        self.assertEqual((rc, out), (2, ""), err)

    def test_the_judge_path_is_not_asked_for_it(self):
        # run() called directly — what tasks/review.py does — keeps the uncontained path
        with mock.patch.object(sandbox, "is_sandboxed", return_value=False), \
                mock.patch.object(sandbox.shutil, "which",
                                  side_effect=lambda n, *a, **k: None if n == "bwrap" else _REAL_WHICH(n, *a, **k)):
            argv = sandbox._wrapped_argv("claude", ["-p", "hi"], Path("/proj"), None, False)
        self.assertEqual(argv, sandbox._compose_agent_argv("claude", ["-p", "hi"]))


class TheNestingSignalIsTrue(unittest.TestCase):
    """`run()` used to export PLAYBOOK_SANDBOXED=1 to EVERY child — also to one it had not
    wrapped, on a host without bubblewrap. The launcher takes that variable as "an outer
    sandbox contains me", so an uncontained judge's own `sandbox --prompt` would not have been
    refused. A child is told it is sandboxed only when it is."""

    def _env_given_to_the_child(self, *, bwrap, nested, how="run"):
        project = Path(tempfile.mkdtemp(prefix="pb-signal-")).resolve()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        seen = {}

        def started(argv, **kw):
            seen["env"] = dict(kw.get("env") or {})
            return types.SimpleNamespace(returncode=0, stdout="", stderr="", stdin=None, args=argv)

        with mock.patch.object(sandbox, "is_sandboxed", return_value=nested), \
                mock.patch.object(sandbox.shutil, "which",
                                  side_effect=lambda n, *a, **k: bwrap if n == "bwrap" else _REAL_WHICH(n, *a, **k)), \
                mock.patch.object(sandbox.subprocess, "run", side_effect=started), \
                mock.patch.object(sandbox.subprocess, "Popen", side_effect=started):
            getattr(sandbox, how)("claude", ["-p", "hi"], project,
                                  env={"PATH": os.environ.get("PATH", ""), "HOME": str(project)})
        return seen["env"]

    def test_an_unwrapped_child_is_not_told_it_is_sandboxed(self):
        for how in ("run", "popen"):
            with self.subTest(how=how):
                self.assertNotIn("PLAYBOOK_SANDBOXED", self._env_given_to_the_child(bwrap=None, nested=False, how=how))

    def test_an_unwrapped_child_does_not_inherit_a_stale_signal_either(self):
        project = Path(tempfile.mkdtemp(prefix="pb-signal-")).resolve()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        seen = {}
        with mock.patch.object(sandbox, "is_sandboxed", return_value=False), \
                mock.patch.object(sandbox.shutil, "which",
                                  side_effect=lambda n, *a, **k: None if n == "bwrap" else _REAL_WHICH(n, *a, **k)), \
                mock.patch.object(sandbox.subprocess, "run",
                                  side_effect=lambda argv, **kw: seen.update(env=dict(kw["env"])) or
                                  types.SimpleNamespace(returncode=0)):
            sandbox.run("claude", ["-p", "hi"], project, env={"PATH": os.environ.get("PATH", ""),
                                                              "HOME": str(project), "PLAYBOOK_SANDBOXED": "1"})
        self.assertNotIn("PLAYBOOK_SANDBOXED", seen["env"])

    def test_a_wrapped_child_is_told(self):
        for how in ("run", "popen"):
            with self.subTest(how=how):
                env = self._env_given_to_the_child(bwrap="/usr/bin/bwrap", nested=False, how=how)
                self.assertEqual(env.get("PLAYBOOK_SANDBOXED"), "1")

    def test_a_nested_child_is_told(self):
        env = self._env_given_to_the_child(bwrap=None, nested=True)
        self.assertEqual(env.get("PLAYBOOK_SANDBOXED"), "1")


class TheRealLauncherWithoutBubblewrap(unittest.TestCase):
    """LIVE, no mock: `scripts/sandbox` itself, a PATH with everything but `bwrap`, and a fake
    `claude` on that PATH that writes a marker file if it is ever executed."""

    @classmethod
    def setUpClass(cls):
        cls.bash = bash_or_skip()
        cls.root = Path(tempfile.mkdtemp(prefix="pb-nobwrap-")).resolve()
        cls.bin = cls.root / "bin"
        cls.bin.mkdir()
        os.symlink(sys.executable, cls.bin / "python3")          # the interpreter this suite runs on
        for d in ("/usr/bin", "/bin"):
            if not os.path.isdir(d):
                continue
            for e in os.scandir(d):
                dest = cls.bin / e.name
                if e.name != "bwrap" and not os.path.lexists(dest):
                    os.symlink(e.path, dest)
        (cls.bin / "claude").unlink(missing_ok=True)
        _script(cls.bin / "claude", 'printf ran > "$MARKER"\n')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def launch(self, *argv, extra_env=None, extra_bin=None):
        case = Path(tempfile.mkdtemp(prefix="case-", dir=self.root))
        (case / "home").mkdir()
        (case / "project").mkdir()
        marker = case / "agent-ran"
        path = str(self.bin) if extra_bin is None else f"{extra_bin}{os.pathsep}{self.bin}"
        env = {"PATH": path, "HOME": str(case / "home"), "MARKER": str(marker), "LANG": "C.UTF-8"}
        env.update(extra_env or {})
        r = subprocess.run([self.bash, str(PLUGIN / "scripts" / "sandbox"), "--agent", "claude", *argv],
                           cwd=case / "project", env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=120)
        return r, marker

    def test_the_harness_has_no_bubblewrap(self):
        self.assertIsNone(shutil.which("bwrap", path=str(self.bin)))

    def test_a_run_is_refused_and_the_agent_never_starts(self):
        r, marker = self.launch("--", "-p", "hi")
        self.assertFalse(marker.exists(), f"the agent was executed with no containment\nstderr: {r.stderr}")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("bubblewrap", r.stderr)
        self.assertIn(INSTALL, r.stderr)

    def test_a_prompt_run_is_refused_and_the_agent_never_starts(self):
        r, marker = self.launch("--prompt", "hi")
        self.assertFalse(marker.exists(), f"the agent was executed with no containment\nstderr: {r.stderr}")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_print_argv_is_refused_and_prints_no_argv(self):
        r, marker = self.launch("--print-argv", "--", "-p", "hi")
        self.assertEqual((r.returncode, r.stdout), (2, ""), r.stderr)
        self.assertFalse(marker.exists())

    def test_a_bubblewrap_that_cannot_start_is_refused_with_its_error(self):
        broken = Path(tempfile.mkdtemp(prefix="broken-", dir=self.root))
        _script(broken / "bwrap", "echo 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n")
        r, marker = self.launch("--", "-p", "hi", extra_bin=broken)
        self.assertFalse(marker.exists())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("bwrap: setting up uid map: Permission denied", r.stderr)
        self.assertIn("could not start", r.stderr)

    def test_what_an_uncontained_run_starts_cannot_use_the_launcher_either(self):
        # A judge on this host runs uncontained: tasks/review.py calls run() directly, and that
        # stays. But what THAT process starts through the launcher must still be refused.
        case = Path(tempfile.mkdtemp(prefix="chain-", dir=self.root))
        for d in ("home", "project", "bin"):
            (case / d).mkdir()
        marker = case / "inner-agent-ran"
        _script(case / "bin" / "claude",
                'case "$*" in *inner*) printf ran > "$MARKER"; exit 0 ;; esac\n'
                '"$BASH_EXE" "$SANDBOX" --agent claude -- inner 2> "$MARKER.err"\n'
                'printf "%s" "$?" > "$MARKER.rc"\n')
        env = {"PATH": f"{case / 'bin'}{os.pathsep}{self.bin}", "HOME": str(case / "home"),
               "MARKER": str(marker), "LANG": "C.UTF-8", "BASH_EXE": self.bash,
               "SANDBOX": str(PLUGIN / "scripts" / "sandbox")}
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from provider import sandbox; "
                "sys.exit(sandbox.run('claude', ['outer'], sys.argv[2]).returncode)")
        r = subprocess.run([sys.executable, "-c", code, str(PLUGIN), str(case / "project")],
                           cwd=case / "project", env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=120)
        rc_file = Path(str(marker) + ".rc")
        self.assertTrue(rc_file.exists(), f"the outer (uncontained) run did not reach the fake agent: {r.stderr}")
        self.assertFalse(marker.exists(), "an uncontained process started an agent through the launcher")
        self.assertEqual(rc_file.read_text(encoding="utf-8"), "2")
        self.assertIn("no containment", Path(str(marker) + ".err").read_text(encoding="utf-8"))

    def test_the_monitor_is_refused_before_it_writes_anything(self):
        case = Path(tempfile.mkdtemp(prefix="monitor-", dir=self.root))
        (case / "home").mkdir()
        project = case / "project"
        (project / ".agent" / "tasks").mkdir(parents=True)
        marker = case / "agent-ran"
        env = {"PATH": str(self.bin), "HOME": str(case / "home"), "MARKER": str(marker),
               "LANG": "C.UTF-8", "PLAYBOOK_PROJECT_DIR": str(project)}
        r = subprocess.run([self.bash, str(PLUGIN / "scripts" / "monitor-lib" / "launch-monitor"),
                            "--session-id", "pid-1"],
                           cwd=project, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=120)
        self.assertFalse(marker.exists(), f"the monitor's agent was executed with no containment\n{r.stderr}")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("no containment", r.stderr)
        self.assertFalse((project / ".agent" / "monitor").exists(),
                         "the refusal came after the monitor had already written its state")

    def test_negative_control_the_monitor_harness_does_see_a_launch(self):
        case = Path(tempfile.mkdtemp(prefix="monitor-", dir=self.root))
        (case / "home").mkdir()
        project = case / "project"
        (project / ".agent" / "tasks").mkdir(parents=True)
        marker = case / "agent-ran"
        env = {"PATH": str(self.bin), "HOME": str(case / "home"), "MARKER": str(marker),
               "LANG": "C.UTF-8", "PLAYBOOK_PROJECT_DIR": str(project), "PLAYBOOK_SANDBOXED": "1"}
        r = subprocess.run([self.bash, str(PLUGIN / "scripts" / "monitor-lib" / "launch-monitor"),
                            "--session-id", "pid-1"],
                           cwd=project, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=120)
        self.assertTrue(marker.exists(), r.stderr)
        self.assertTrue((project / ".agent" / "monitor").is_dir())

    def test_negative_control_the_same_harness_does_see_a_launch(self):
        # PLAYBOOK_SANDBOXED=1 set by hand is the operator's own statement that an outer cage
        # exists; the launcher takes his word and starts the agent — so the missing marker in
        # the tests above means "refused", not "this harness cannot launch"
        r, marker = self.launch("--", "-p", "hi", extra_env={"PLAYBOOK_SANDBOXED": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(marker.exists(), r.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "ran")


if __name__ == "__main__":
    unittest.main(verbosity=2)
