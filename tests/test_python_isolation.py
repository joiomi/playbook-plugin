#!/usr/bin/env python3
"""The plugin's launchers, inline programs and helper files are started isolated from the project and from
the user's Python environment (task 167, PLAN §S11 item 1; task 180, items 19 and 20).

Python puts the WORKING DIRECTORY first on `sys.path` for `python3 -c …`, for
`python3 -` (a program on stdin) and for `python3 -m module`; for `python3 file.py`
it puts the file's directory there instead. The hooks and the launchers run with
the project as their working directory, so a project file named like a module the
call imports was imported in its place:

  * a `json.py` at the project root killed the CLI launcher, `scripts/init` and
    every hook's inline read;
  * a `tasks.py` killed the CLI launcher; a project folder `tasks/` holding a
    `cli.py` RAN in place of the CLI, with exit 0;
  * a project folder `provider/` killed the sandbox launcher and the monitor.

`python3 -I` (isolated mode) keeps the working directory and every `PYTHON*`
variable out. The launchers cannot rely on `PYTHONPATH` under it, so they put the
plugin directory on `sys.path` themselves and run the module through `runpy`.

A helper started as a FILE was left alone by task 167 — the working directory does
not reach it — and that was its row's first bound: the user's PYTHONPATH did, and
PYTHONPATH comes before the standard library. With `PYTHONPATH=.` a project `os.py`
took the destructive-command guard down with exit 1, which a PreToolUse hook does
not take for a block; a broken `.pth` file in the user's own site-packages did the
same. Task 180: every such helper is started `python3 -E -s <file>`, and the
launchers no longer put the plugin's directory on PYTHONPATH for what they start.

Three kinds of proof here:
  1. behaviour — the shipped entry points, run in a temp project that holds the
     shadowing file, each beside a control run in a clean project;
  2. a sweep over every shipped file: an inline program or a module is never
     started without `-I`, a file of the plugin's never without `-E` and `-s`;
  3. the sweep's own rule, on strings it must refuse and strings it must accept.

What is NOT claimed: a helper started by hand through its `#!` line reads the
user's environment (nothing shipped starts one that way); the SYSTEM's site-packages
are read by every helper (`-S` is not used); a codex configuration written before
task 180 keeps its old command until it is regenerated.

What the sweep does NOT see (said in the ledger row too): a call through a
variable (`"$PY" -c …`), and — in Python sources — anything but an argv list that
starts with `sys.executable` or with the interpreter's name as a literal (a command
handed to a shell as one string would not be read; no shipped module does that).

Run: python3 -m unittest tests.test_python_isolation
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tests._bashcheck import bash_or_skip  # noqa: E402

# A module that dies loudly when imported: the test then shows WHICH file ran.
_TRAP = 'raise SystemExit("the project file {name} was imported")\n'


def _trap(project: Path, name: str) -> None:
    (project / name).write_text(_TRAP.format(name=name), encoding="utf-8")


class _Project(unittest.TestCase):
    """A temp project (and a temp HOME, so nothing here touches the real one)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.project = self.new_project("proj")

    def new_project(self, name: str) -> Path:
        p = self.base / name
        p.mkdir()
        return p

    def env(self, **extra) -> dict:
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        # What the run must not inherit from whoever runs the suite. PLAYBOOK_SANDBOXED
        # is the nesting signal: a reviewer's sandbox sets it, and with it the monitor
        # launcher's pre-flight is not refused for want of bubblewrap — the test below
        # that relies on that refusal then went on to start an agent (the tail-
        # certification judge of this task met it; `claude` was not on the test's PATH).
        for k in ("PYTHONPATH", "PLAYBOOK_SESSION_ID", "PLAYBOOK_PROJECT_DIR", "CLAUDE_PROJECT_DIR",
                  "PLAYBOOK_SANDBOXED"):
            env.pop(k, None)
        env.update(extra)
        return env

    def run_in(self, project: Path, argv, *, stdin=None, **extra):
        return subprocess.run([str(a) for a in argv], cwd=project, env=self.env(**extra),
                              input=stdin, text=True, capture_output=True, timeout=120)


# --------------------------------------------------------------------------- #
# 1a. The CLI launcher.
# --------------------------------------------------------------------------- #
class TheCliLauncherIgnoresProjectFiles(_Project):
    LAUNCHER = SCRIPTS / "tasks"

    def _version(self, project: Path):
        return self.run_in(project, [bash_or_skip(), self.LAUNCHER, "--version"])

    def setUp(self):
        super().setUp()
        clean = self._version(self.new_project("clean"))
        self.assertEqual(clean.returncode, 0, clean.stderr)       # the control
        self.assertRegex(clean.stdout.strip(), r"^\d+\.\d+\.\d+")
        self.clean = clean.stdout

    def _same_as_clean(self):
        r = self._version(self.project)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, self.clean, ""))

    def test_a_json_py_at_the_project_root(self):
        _trap(self.project, "json.py")
        self._same_as_clean()

    def test_a_tasks_py_at_the_project_root(self):
        _trap(self.project, "tasks.py")
        self._same_as_clean()

    def test_a_project_package_called_tasks_does_not_run_in_place_of_the_cli(self):
        pkg = self.project / "tasks"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "cli.py").write_text('print("THE PROJECT FILE RAN")\n', encoding="utf-8")
        self._same_as_clean()

    def test_nothing_of_the_project_is_compiled(self):
        # importing the project's file left a __pycache__/ in the project
        _trap(self.project, "json.py")
        self._version(self.project)
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), ["json.py"])

    def test_it_behaves_like_the_module_run_directly(self):
        # the same answers as `python3 -m tasks.cli` gave: the help text (argparse reads
        # argv[0]), and an unknown command's exit code and message
        for args in (["--help"], ["no-such-command", "two words"]):
            via = self.run_in(self.project, [bash_or_skip(), self.LAUNCHER, *args])
            direct = self.run_in(self.project, [sys.executable, "-m", "tasks.cli", *args],
                                 PYTHONPATH=str(PLUGIN))
            self.assertEqual((via.returncode, via.stdout, via.stderr),
                             (direct.returncode, direct.stdout, direct.stderr), args)


def _fake_python(bindir: Path) -> None:
    """A `python3` that answers the version probe and otherwise prints what it was given:
    its PYTHONPATH, then one argument per line."""
    bindir.mkdir()
    fake = bindir / "python3"
    fake.write_text('#!/bin/bash\ncase "$*" in *version_info*) exit 0 ;; esac\n'
                    'printf "PYTHONPATH=%s\\n" "${PYTHONPATH-<unset>}"\n'
                    'for a in "$@"; do printf "ARG=%s\\n" "${a//$\'\\n\'/<NL>}"; done\n',
                    encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)


class WhatTheLaunchersHandToPython(_Project):
    """The launch line itself, seen from the interpreter's side."""

    def _given(self, launcher: Path, *args, **extra):
        bindir = self.base / "fakebin"
        _fake_python(bindir)
        r = self.run_in(self.project, [bash_or_skip(), launcher, *args],
                        PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}", **extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        return lines[0], [ln[4:] for ln in lines[1:] if ln.startswith("ARG=")]

    def test_the_cli_launcher(self):
        pypath, args = self._given(SCRIPTS / "tasks", "work", "two words", "--flag")
        self.assertEqual(args[:2], ["-I", "-c"])
        self.assertIn('runpy.run_module("tasks.cli"', args[2])
        self.assertEqual(args[3:], [str(PLUGIN), "work", "two words", "--flag"])
        # NOT exported any more (task 180, PLAN S11 item 20): the plugin's packages are
        # called `tasks` and `provider`, and everything the CLI starts — a project's own
        # verify command among them — was handed them in front of its own
        self.assertEqual(pypath, "PYTHONPATH=<unset>")

    def test_the_users_own_pythonpath_is_passed_on_as_it_was(self):
        for launcher, arg in ((SCRIPTS / "tasks", "list"), (SCRIPTS / "sandbox", "--list-agents")):
            for mine in ("/somewhere/else", f"/a{os.pathsep}/b", ""):
                with self.subTest(launcher=launcher.name, mine=mine):
                    try:
                        pypath, _ = self._given(launcher, arg, PYTHONPATH=mine)
                    finally:
                        shutil.rmtree(self.base / "fakebin", ignore_errors=True)
                    self.assertEqual(pypath, f"PYTHONPATH={mine}")

    def test_the_sandbox_launcher(self):
        pypath, args = self._given(SCRIPTS / "sandbox", "--list-agents")
        self.assertEqual(args[:2], ["-I", "-c"])
        self.assertIn('runpy.run_module("provider.sandbox"', args[2])
        self.assertEqual(args[3:], [str(PLUGIN), "--list-agents"])
        self.assertEqual(pypath, "PYTHONPATH=<unset>")

    def test_the_monitor_launcher_exports_nothing_either(self):
        # its two launch lines would start an agent; what they say is read from the file
        text = (SCRIPTS / "monitor-lib" / "launch-monitor").read_text(encoding="utf-8")
        self.assertEqual(text.count("python3 -I -c 'import runpy"), 2)
        self.assertNotIn("PYTHONPATH=", "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#")))


class WhatTheCliStartsIsNotHandedThePluginsPackages(_Project):
    """Item (20), through the real launcher: a project with its OWN package called `tasks`,
    which its verify command reaches through the user's PYTHONPATH. The launcher used to
    put the plugin's directory in FRONT of that path for every child, so the project's
    check imported the plugin's `tasks` and the close failed — or, with a check that only
    imports, passed against the wrong code."""

    def setUp(self):
        super().setUp()
        p = self.project
        subprocess.run(["git", "init", "-q", str(p)], check=True)
        (p / "tasks").mkdir()
        (p / "tasks" / "__init__.py").write_text('WHO = "the project"\n', encoding="utf-8")
        (p / "tools").mkdir()
        (p / "tools" / "check.py").write_text(
            "import sys\nimport tasks\n"
            'print("imported:", getattr(tasks, "WHO", tasks.__file__))\n'
            'sys.exit(0 if getattr(tasks, "WHO", None) == "the project" else 1)\n', encoding="utf-8")
        (p / ".agent" / "tasks" / "001-t").mkdir(parents=True)
        (p / ".agent" / "config.json").write_text(
            json.dumps({"verify": "python3 tools/check.py", "panel_required_for": []}), encoding="utf-8")
        (p / ".agent" / "tasks" / "001-t" / "task.md").write_text(
            "# 001 - T\n\n## Status\npending\n\n## Risk\nreversible\n\n## Work Plan\n- [x] G1: do it\n",
            encoding="utf-8")

    def _cli(self, *args, **extra):
        return self.run_in(self.project, [bash_or_skip(), SCRIPTS / "tasks", *args],
                           PLAYBOOK_SESSION_ID="sess-task-180", **extra)

    def test_the_check_is_what_this_test_thinks_it_is(self):
        # CONTROL, outside the CLI: with the user's path the project's package is found;
        # with the plugin's directory in front of it — what the launcher did — it is not
        mine = self.run_in(self.project, [sys.executable, "tools/check.py"], PYTHONPATH=str(self.project))
        self.assertEqual((mine.returncode, mine.stdout.strip()), (0, "imported: the project"), mine.stderr)
        theirs = self.run_in(self.project, [sys.executable, "tools/check.py"],
                             PYTHONPATH=f"{PLUGIN}{os.pathsep}{self.project}")
        self.assertEqual(theirs.returncode, 1, theirs.stdout)
        self.assertIn(str(PLUGIN / "tasks" / "__init__.py"), theirs.stdout)

    def test_a_close_runs_the_projects_check_against_the_projects_package(self):
        r = self._cli("work", "1", PYTHONPATH=str(self.project))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self._cli("work", "done", PYTHONPATH=str(self.project))
        self.assertIn("Task 001 done.", r.stdout, r.stdout[-800:] + r.stderr[-800:])
        receipt = (self.project / ".agent" / "tasks" / "001-t" / "task.md").read_text(encoding="utf-8")
        self.assertIn("python3 tools/check.py", receipt)


# --------------------------------------------------------------------------- #
# 1b. The sandbox launcher and the monitor launcher.
# --------------------------------------------------------------------------- #
def _shadow_provider(project: Path) -> None:
    (project / "provider").mkdir()
    (project / "provider" / "__init__.py").write_text("", encoding="utf-8")


class TheSandboxLauncherIgnoresProjectFiles(_Project):
    def _agents(self, project: Path):
        return self.run_in(project, [bash_or_skip(), SCRIPTS / "sandbox", "--list-agents"])

    def test_a_project_folder_called_provider(self):
        clean = self._agents(self.new_project("clean"))
        self.assertEqual(clean.returncode, 0, clean.stderr)       # the control
        self.assertIn("claude", clean.stdout)
        _shadow_provider(self.project)
        r = self._agents(self.project)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, clean.stdout, clean.stderr))


def _path_without_bwrap(bindir: Path) -> str:
    """A one-directory PATH holding the tools the monitor launcher uses up to its
    pre-flight, a real python3 — and no bubblewrap. The pre-flight then REFUSES (the
    sandbox module's own "no containment" answer) and nothing is launched."""
    bindir.mkdir()
    for name in ("bash", "sh", "cat", "grep", "sed", "head", "tail", "find", "dirname",
                 "basename", "printf", "echo", "uname", "tr", "awk", "mkdir", "rm", "mv",
                 "cp", "ls", "env", "date", "wc", "cut", "sort", "ps", "chmod", "expr",
                 "id", "touch", "readlink", "pwd", "test", "git", "stat", "sleep"):
        real = shutil.which(name)
        if real is None:
            if name in ("bash", "cat", "grep", "sed", "dirname", "ls", "env"):
                raise unittest.SkipTest(f"{name!r} is not on PATH")
            continue
        os.symlink(real, bindir / name)
    py = bindir / "python3"            # by absolute path: a symlink could confuse a venv
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    if shutil.which("bwrap", path=str(bindir)) is not None:
        raise unittest.SkipTest("could not build a PATH without bubblewrap")
    return str(bindir)


class TheMonitorLauncherIgnoresProjectFiles(_Project):
    """Its pre-flight is the first of its two calls of `provider.sandbox`; the second
    would start an agent, so it is covered by the sweep and the shared-text test."""

    def _preflight(self, project: Path, path: str):
        (project / ".agent" / "tasks").mkdir(parents=True)
        return self.run_in(project, [bash_or_skip(), SCRIPTS / "monitor-lib" / "launch-monitor",
                                     "--session-id", "pid-1"],
                           PATH=path, PLAYBOOK_PROJECT_DIR=str(project))

    def test_a_project_folder_called_provider(self):
        path = _path_without_bwrap(self.base / "bin")
        clean = self._preflight(self.new_project("clean"), path)
        self.assertEqual(clean.returncode, 2, clean.stderr)       # the control: refused …
        self.assertIn("no containment", clean.stderr)             # … by the real module
        _shadow_provider(self.project)
        r = self._preflight(self.project, path)
        self.assertNotIn("No module named", r.stderr)
        self.assertEqual((r.returncode, r.stderr), (2, clean.stderr))
        self.assertFalse((self.project / ".agent" / "monitor").exists())


# --------------------------------------------------------------------------- #
# 1c. init and a hook.
# --------------------------------------------------------------------------- #
class InitIgnoresProjectFiles(_Project):
    def _init(self, project: Path):
        return self.run_in(project, [bash_or_skip(), SCRIPTS / "init", "proj"])

    def test_a_json_py_at_the_project_root(self):
        clean = self._init(self.new_project("clean"))
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)   # the control
        _trap(self.project, "json.py")
        r = self._init(self.project)
        self.assertEqual(r.returncode, 0, r.stdout[-600:] + r.stderr[-600:])
        settings = json.loads((self.project / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("TodoWrite", json.dumps(settings))
        self.assertFalse((self.project / "__pycache__").exists(),
                         "the project's json.py was imported (and compiled) by init")


class AHooksInlineReadIgnoresProjectFiles(_Project):
    """session-end-hook reads the end reason with an inline program; unread, the reason is
    empty, which means KEEP — so the session directory outlived every logout."""

    def _logout(self, project: Path) -> bool:
        session = project / ".agent" / "sessions" / "judge"
        session.mkdir(parents=True)
        (project / ".agent" / "tasks").mkdir()
        r = self.run_in(project, [bash_or_skip(), SCRIPTS / "session-end-hook"],
                        stdin=json.dumps({"reason": "logout"}), PLAYBOOK_SESSION_ID="judge")
        self.assertEqual(r.returncode, 0, r.stderr)
        return session.exists()

    def test_a_json_py_at_the_project_root(self):
        self.assertFalse(self._logout(self.new_project("clean")))   # the control: cleaned up
        _trap(self.project, "json.py")
        self.assertFalse(self._logout(self.project), "the session directory was kept")


class HelpersRunAsFilesAreNotGivenTheWorkingDirectory(_Project):
    """For a file, Python puts the FILE's directory first, not the working directory —
    the reason task 167 left every `python3 <file>` call as it was. What that left open
    is in the next class."""

    def setUp(self):
        super().setUp()
        for name in ("json.py", "tasks.py", "os.py", "re.py"):
            _trap(self.project, name)

    def test_the_payload_normalizer(self):
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "/x/a.py"}})
        r = self.run_in(self.project, [sys.executable, SCRIPTS / "hook-payload-normalize.py"], stdin=payload)
        self.assertEqual((r.returncode, json.loads(r.stdout)["tool_name"]), (0, "Edit"), r.stderr)

    def test_the_status_reader(self):
        task = self.project / ".agent" / "tasks" / "001-x" / "task.md"
        task.parent.mkdir(parents=True)
        task.write_text("# 001 - X\n\n## Status\nin_progress\n", encoding="utf-8")
        r = self.run_in(self.project, [sys.executable, SCRIPTS / "task-status.py", task])
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "in_progress"), r.stderr)


class AUsersPythonpathDoesNotReachTheHelpers(_Project):
    """Item (19). A helper run as a file still read PYTHONPATH, and PYTHONPATH comes
    before the standard library: with the user's own path covering the project (`.` —
    direnv, an IDE's run configuration) a project file named like a module a helper
    imports was imported in its place. At the destructive-command guard that meant
    exit 1, which a PreToolUse hook does not take for a block. Task 167 stated it as the
    first bound of its ledger row and pinned it here the other way round; every helper
    is started `python3 -E -s <file>` now. Each entry point beside a control run
    WITHOUT the variable."""
    TRAPS = ("json.py", "os.py", "re.py", "tasks.py", "pathlib.py", "subprocess.py", "provider.py")
    SID = "sess-task-180"      # not `pid-<number>`: that form counts only while the process is a live agent

    def setUp(self):
        super().setUp()
        self.clean = self.new_project("clean")
        for p in (self.project, self.clean):
            task = p / ".agent" / "tasks" / "001-real" / "task.md"
            task.parent.mkdir(parents=True)
            task.write_text("# 001 - real\n## Status\nin_progress\n## Work Plan\n- [ ] first gate\n", encoding="utf-8")
            (p / ".agent" / "sessions" / self.SID).mkdir(parents=True)
            (p / ".agent" / "sessions" / self.SID / "current_state").write_text("001\n", encoding="utf-8")
            # activity, so that the stop hook does not leave by its conversational exit
            # (no write and under five tool calls) before it reads the task
            (p / ".agent" / "sessions" / self.SID / "counters").write_text("writes=9\ntools=40\n", encoding="utf-8")
        for name in self.TRAPS:
            _trap(self.project, name)

    def _paths(self):
        return (".", str(self.project), f"/nowhere{os.pathsep}{self.project}")

    def _hook(self, project, name, payload, **extra):
        return self.run_in(project, [bash_or_skip(), SCRIPTS / name], stdin=json.dumps(payload),
                           PLAYBOOK_SESSION_ID=self.SID, **extra)

    def test_the_command_guard_still_blocks(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
        clean = self._hook(self.clean, "command-guard-hook", payload)
        self.assertEqual(clean.returncode, 2, clean.stderr)                    # the control
        self.assertIn("BLOCKED", clean.stdout + clean.stderr)
        for path in self._paths():
            with self.subTest(PYTHONPATH=path):
                r = self._hook(self.project, "command-guard-hook", payload, PYTHONPATH=path)
                self.assertNotIn("was imported", r.stdout + r.stderr)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_the_users_site_packages_do_not_reach_the_guard_either(self):
        # `-s`: a `.pth` file in the user's own site-packages is run by every interpreter
        # that starts in his environment — one that fails takes the guard down with it
        where = self.run_in(self.clean, [sys.executable, "-c", "import site; print(site.getusersitepackages())"])
        user_site = Path(where.stdout.strip())
        self.assertTrue(str(user_site).startswith(str(self.home)), user_site)   # the temp HOME's, not the real one
        user_site.mkdir(parents=True)
        (user_site / "broken.pth").write_text("import sys; sys.exit('the user site ran')\n", encoding="utf-8")
        seen = self.run_in(self.clean, [sys.executable, "-c", "pass"])
        if "the user site ran" not in seen.stderr:
            self.skipTest("this interpreter does not read a user site (a virtual environment)")
        payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
        r = self._hook(self.clean, "command-guard-hook", payload)
        self.assertNotIn("the user site ran", r.stdout + r.stderr)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_the_edit_gate_lets_an_authorized_edit_through(self):
        # it failed CLOSED here: the status reader died and every edit was refused
        def edit(project, **extra):
            return self._hook(project, "task-gate-hook",
                              {"tool_name": "Edit", "tool_input": {"file_path": str(project / "src" / "main.py")}},
                              **extra)
        clean = edit(self.clean)
        self.assertEqual(clean.returncode, 0, clean.stderr)                    # the control
        for path in self._paths():
            with self.subTest(PYTHONPATH=path):
                r = edit(self.project, PYTHONPATH=path)
                self.assertEqual((r.returncode, r.stderr), (0, clean.stderr), r.stdout)

    def test_the_stop_hook_reads_the_task_as_it_does_without_it(self):
        clean = self._hook(self.clean, "stop-hook", {"stop_hook_active": False})
        self.assertEqual(clean.returncode, 2, clean.stderr)                    # the control: an open gate blocks
        self.assertIn("first gate", clean.stdout + clean.stderr)
        self.assertNotIn("could not read the task state", clean.stderr)
        for path in self._paths():
            with self.subTest(PYTHONPATH=path):
                r = self._hook(self.project, "stop-hook", {"stop_hook_active": False}, PYTHONPATH=path)
                self.assertNotIn("could not read the task state", r.stderr)
                self.assertEqual((r.returncode, r.stdout, r.stderr.replace(str(self.project), "<P>")),
                                 (clean.returncode, clean.stdout, clean.stderr.replace(str(self.clean), "<P>")))

    def test_init_initialises_the_project(self):
        fresh = self.new_project("fresh")
        for name in self.TRAPS:
            _trap(fresh, name)
        r = self.run_in(fresh, [bash_or_skip(), SCRIPTS / "init", "proj"], PYTHONPATH=".")
        self.assertEqual(r.returncode, 0, r.stdout[-600:] + r.stderr[-600:])
        self.assertNotIn("was imported", r.stdout + r.stderr)
        self.assertIn("TodoWrite", (fresh / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertFalse((fresh / "__pycache__").exists(), "a project file was imported (and compiled) by init")

    def test_the_command_the_codex_adapter_writes_into_a_configuration(self):
        if str(PLUGIN) not in sys.path:
            sys.path.insert(0, str(PLUGIN))
        from provider import codex_hooks
        for script in ("codex-stop-hook", "codex-user-prompt-hook", "codex-apply-patch-hook"):
            with self.subTest(script=script):
                cmd = codex_hooks._command_for(script)
                self.assertEqual(cmd, f"python3 -E -s {shlex.quote(str(SCRIPTS / script))}")
                r = self.run_in(self.project, [bash_or_skip(), "-c", cmd], stdin="{}",
                                PYTHONPATH=".", PLAYBOOK_SESSION_ID=self.SID)
                self.assertNotIn("was imported", r.stdout + r.stderr)

    def test_the_flags_take_the_variable_away_from_one_interpreter_only(self):
        # the merge skill's helper runs the PROJECT's commands: they must still get the
        # user's environment — `-E` is a flag of that one interpreter, not an `env -u`
        probe = self.base / "probe.py"
        probe.write_text("import os, subprocess, sys\n"
                         "print(os.environ.get('PYTHONPATH'))\n"
                         "print(subprocess.run([sys.executable, '-c', 'import os; print(os.environ.get(\"PYTHONPATH\"))'],"
                         " capture_output=True, text=True).stdout.strip())\n", encoding="utf-8")
        r = self.run_in(self.clean, [sys.executable, "-E", "-s", probe], PYTHONPATH="/mine")
        self.assertEqual(r.stdout.split(), ["/mine", "/mine"], r.stderr)


# --------------------------------------------------------------------------- #
# 2. The sweep.
# --------------------------------------------------------------------------- #
# The PROJECT's own test runners, named in the texts that suggest a verify command.
# They must NOT be isolated: a project's tests need the project on sys.path.
PROJECT_RUNNERS = {"pytest", "unittest"}
# The plugin's own packages: started through a launcher's bootstrap, never with -m.
PLUGIN_PACKAGES = {"tasks", "provider"}

_PY = re.compile(r"(?<![\w.-])python3(?![\w.-])")
_SHORT_FLAGS = re.compile(r"-[A-Za-z]+")
# What the interpreter is handed when it is started on a FILE of the plugin's: a
# variable, an absolute or a dotted path, or the skill's `<…-dir>/` placeholder. A bare
# relative path (`scripts/verify`) is a PROJECT's own file in the texts that suggest a
# command, and is not this rule's.
_FILE_START = re.compile(r"""["']?(?:\$|/|\./|\.\./|~/|<[\w-]+>/)""")


def calls_on(line: str) -> list[tuple[str, str]]:
    """Every `python3` on one line of shell or markdown, as (kind, detail):
    `ok-inline` (inline code or a module, isolated), `ok-file` (a file of the plugin's,
    started with -E and -s, or -I), `ok-runner` (a project runner), `other` (`--version`,
    a project's own file, prose) or `bad` (with the reason)."""
    found = []
    for m in _PY.finditer(line):
        # `"$(command -v python3)" -c …`: what closes a substitution or a quote right
        # after the word is not an argument
        after = line[m.end():].lstrip(")\"'`")
        toks = after.split()
        isolated, no_env, no_user_site, i, verdict = False, False, False, 0, ("other", "")
        while i < len(toks):
            t = toks[i]
            if t == "\\":
                verdict = ("bad", "the call continues on the next line before its mode flag — "
                                  "keep `-I -c` / `-I -` on the line of `python3`")
                break
            if t == "-":
                verdict = ("ok-inline", "-") if isolated else \
                    ("bad", "a program on stdin (`python3 -`) without -I")
                break
            if not _SHORT_FLAGS.fullmatch(t):
                # a file, a long option, prose. It is a call only after whitespace
                # (`python3/the hook scripts` is prose) and a file of the plugin's only
                # when it begins like one (task 180)
                if after[:1].isspace() and _FILE_START.match(t):
                    verdict = ("ok-file", t) if isolated or (no_env and no_user_site) else \
                        ("bad", "a file started without -E -s: the user's PYTHONPATH and site-packages "
                                "come before the standard library for it")
                break
            flags = t[1:]
            isolated = isolated or "I" in flags
            no_env = no_env or "E" in flags
            no_user_site = no_user_site or "s" in flags
            if flags[-1] == "c":
                verdict = ("ok-inline", "-c") if isolated else ("bad", "inline code (`-c`) without -I")
                break
            if flags[-1] == "m":
                mod = re.sub(r"[^\w.].*", "", toks[i + 1].lstrip("\"'`")) if i + 1 < len(toks) else ""
                if mod.split(".")[0] in PLUGIN_PACKAGES:
                    verdict = ("bad", f"the plugin's `{mod}` started with -m: the working directory "
                                      "comes first without -I, and with -I the plugin is not on "
                                      "sys.path — use the launcher's bootstrap")
                elif mod in PROJECT_RUNNERS:
                    verdict = ("ok-runner", mod)
                elif isolated:
                    verdict = ("ok-inline", f"-m {mod}")
                else:
                    verdict = ("bad", f"`-m {mod or '?'}` without -I, and not a named project runner")
                break
            if flags[-1] in "XW":                       # these two take a value
                i += 1
            i += 1
        found.append(verdict)
    return found


_ARGV = re.compile(r"""\[\s*(?:sys\.executable|["']python3?["'])\s*,([^\]]*)\]""", re.S)


def argv_lists_in(source: str) -> list[tuple[str, str]]:
    """Every argv list in a Python source that starts with `sys.executable` or with
    the interpreter's name as a literal."""
    found = []
    for m in _ARGV.finditer(source):
        mode, isolated = None, False
        for element in m.group(1).split(","):       # the flags that come straight after it
            lit = re.fullmatch(r"""["'](-[^"']*)["']""", element.strip())
            if lit is None:
                break                                # a file, a variable: not a flag any more
            flag = lit.group(1)
            if flag == "-":
                mode = "-"
                break
            if not _SHORT_FLAGS.fullmatch(flag):
                break                                # a long option is not a mode
            # short flags may be bundled, and the mode flag ends its bundle: `-Bc`, `-Ic`
            isolated = isolated or "I" in flag
            if flag[-1] in "cm":
                mode = "-" + flag[-1]
                break
        if mode is None:
            found.append(("other", ""))
        elif isolated:
            found.append(("ok-inline", mode))
        else:
            found.append(("bad", f"an argv list that starts the interpreter with {mode!r} and no '-I' before it"))
    return found


def _is_python_source(path: Path, text: str) -> bool:
    return path.suffix == ".py" or (text.startswith("#!") and "python" in text.split("\n", 1)[0])


def sweep() -> tuple[list[str], dict[str, int], dict[str, int], dict[str, int]]:
    """(problems, counts by kind, isolated inline calls per file, isolated file calls
    per file) over every shipped text file."""
    problems, counts, inline, files = [], {}, {}, {}
    for path in sorted(PLUGIN.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(PLUGIN).as_posix()
        if _is_python_source(path, text):
            hits = [(0, v) for v in argv_lists_in(text)]
        else:
            # a `#` line is a comment in shell and in the Python of a heredoc; markdown
            # has no comments, so a heading is read like any other line
            md = path.suffix == ".md"
            hits = [(n, v) for n, line in enumerate(text.split("\n"), 1)
                    if md or not line.lstrip().startswith("#")
                    for v in calls_on(line)]
        for n, (kind, detail) in hits:
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "bad":
                problems.append(f"{rel}:{n}: {detail}" if n else f"{rel}: {detail}")
            elif kind == "ok-inline":
                inline[rel] = inline.get(rel, 0) + 1
            elif kind == "ok-file":
                files[rel] = files.get(rel, 0) + 1
    return problems, counts, inline, files


class EveryShippedInlinePythonIsIsolated(unittest.TestCase):
    def test_the_sweep_finds_nothing(self):
        problems, _, _, _ = sweep()
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    # Every isolated inline call the sweep sees, file by file — 55 when this was written.
    # A rule that stopped SEEING some of them would still find "nothing wrong" (impl
    # panel r1, sonnet: the first guard here asked only for "at least 40", and a rule
    # that lost the eight stdin programs passed it). Adding or removing a call means
    # changing this table, on purpose.
    ISOLATED_INLINE_CALLS = {
        "commands/init.md": 1,
        "commands/upgrade.md": 2,
        "hooks/monitor-nudge.sh": 2,
        "scripts/chat-log-hook": 2,
        "scripts/command-guard-hook": 1,
        "scripts/gate-echo-lib.sh": 1,
        "scripts/init": 4,
        "scripts/monitor-lib/bootstrap.sh": 4,
        "scripts/monitor-lib/launch-monitor": 3,
        "scripts/playbook-agy": 1,
        "scripts/playbook-codex": 1,
        "scripts/playbook-grok": 1,
        "scripts/playbook-pi": 2,
        "scripts/sandbox": 2,
        "scripts/session-end-hook": 1,
        "scripts/session-start-hook": 1,
        "scripts/state-echo-hook": 8,
        "scripts/stop-hook": 3,
        "scripts/task-gate-hook": 12,
        "scripts/tasks": 2,
        "tasks/plugin_copies.py": 1,
    }

    def test_the_sweep_sees_every_isolated_call_file_by_file(self):
        _, counts, inline, _ = sweep()
        self.assertEqual(inline, self.ISOLATED_INLINE_CALLS)
        self.assertEqual(counts.get("ok-inline"), sum(self.ISOLATED_INLINE_CALLS.values()))
        self.assertEqual(sum(self.ISOLATED_INLINE_CALLS.values()), 55)

    # Every call that starts a FILE of the plugin's, file by file — 22 when task 180
    # isolated them (18 in the hooks and scripts, 4 that the merge skill gives the agent).
    # The same reason as above: a rule that stopped SEEING a call would find nothing wrong.
    ISOLATED_FILE_CALLS = {
        "scripts/chat-log-hook": 1,
        "scripts/command-guard-hook": 1,
        "scripts/gate-echo-lib.sh": 1,
        "scripts/init": 3,
        "scripts/monitor-lib/bootstrap.sh": 2,
        "scripts/state-echo-hook": 2,
        "scripts/stop-hook": 1,
        "scripts/task-gate-hook": 7,
        "skills/merge/SKILL.md": 4,
    }

    def test_the_sweep_sees_every_file_call_file_by_file(self):
        _, counts, _, files = sweep()
        self.assertEqual(files, self.ISOLATED_FILE_CALLS)
        self.assertEqual(counts.get("ok-file"), sum(self.ISOLATED_FILE_CALLS.values()))
        self.assertEqual(sum(self.ISOLATED_FILE_CALLS.values()), 22)

    def test_no_shipped_python_source_starts_a_file_through_an_argv_list(self):
        # the sweep reads an argv list only for its mode flag; a list that hands the
        # interpreter a FILE would be `other` and unjudged. There is none — one command is
        # built as a string instead (`provider/codex_hooks._command_for`, tested above).
        seen = []
        for path in sorted(PLUGIN.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if _is_python_source(path, text):
                seen += [f"{path.relative_to(PLUGIN).as_posix()}: {' '.join(m.group(0).split())[:90]}"
                         for m in _ARGV.finditer(text) if argv_lists_in(m.group(0)) == [("other", "")]]
        self.assertEqual(seen, [])

    def test_no_shipped_script_reaches_python_through_a_variable(self):
        # the sweep reads the word `python3`; a call through a variable would be unseen
        seen = []
        for path in sorted(PLUGIN.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in (".py", ".md"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if _is_python_source(path, text):
                continue
            for n, line in enumerate(text.split("\n"), 1):
                if re.search(r"\$\{?(PYTHON3?|PY|PYBIN|PYTHON_BIN)\b", line) and "PYTHONPATH" not in line:
                    seen.append(f"{path.relative_to(PLUGIN).as_posix()}:{n}: {line.strip()[:90]}")
        self.assertEqual(seen, [])

    def test_the_four_launch_lines_share_one_bootstrap(self):
        bodies = {}
        for rel in ("tasks", "sandbox", "monitor-lib/launch-monitor"):
            text = (SCRIPTS / rel).read_text(encoding="utf-8")
            got = re.findall(r"python3 -I -c '([^']*)'", text)
            bodies[rel] = [b for b in got if "runpy" in b]
        self.assertEqual({k: len(v) for k, v in bodies.items()},
                         {"tasks": 1, "sandbox": 1, "monitor-lib/launch-monitor": 2})
        shapes = {re.sub(r'"(tasks\.cli|provider\.sandbox)"', '"<module>"', b)
                  for v in bodies.values() for b in v}
        self.assertEqual(len(shapes), 1, shapes)
        shape = shapes.pop()
        self.assertIn("sys.path.insert(0, sys.argv.pop(1))", shape)
        self.assertIn('runpy.run_module("<module>", run_name="__main__", alter_sys=True)', shape)


class TheSweepsRule(unittest.TestCase):
    def _kinds(self, line):
        return [k for k, _ in calls_on(line)]

    def test_lines_it_refuses(self):
        for line in (
            'X=$(echo "$IN" | python3 -c "import sys,json; print(1)")',
            "python3 - \"$PWD\" <<'PY'",
            "python3 -u -c 'pass'",
            "python3 -B - <<'PY'",
            'PYTHONPATH="$P" exec python3 -m tasks.cli "$@"',
            "env X=1 python3 -m provider.sandbox --agent claude",
            "python3 -I -m tasks.cli list",            # cannot work: the plugin is not on sys.path
            "python3 -m some.unknown.module",
            "/usr/bin/python3 -c 'pass'",
            "local dev path: `PYTHONPATH=src python3 -m tasks.cli freehand`.",
            "python3 -X utf8 -c 'pass'",
            "FOO=$(python3 \\",
            "python3 -c 'a' && python3 -I -c 'b'",
            '"$(command -v python3)" -c \'pass\'',
            "python3 -E -s -c 'pass'",                  # -E and -s do not remove the working directory
            # task 180: a FILE of the plugin's without -E and -s
            'python3 "$HOOK_DIR/task-status.py" "$RESOLVED"',
            'exec python3 "$GUARD"',
            "python3 $MONITOR_SRC/sensor.py $JSONL --wait-once",
            "> running it: `python3 <this-skill-dir>/merge-verify.py --plan` — exit 4 means",
            'python3 -E "$HOOK_DIR/task-status.py"',    # one of the two is not enough
            'python3 -s "$HOOK_DIR/task-status.py"',
            'python3 -B /opt/playbook/scripts/write_log.py',
            "python3 ./helper.py",
            'X=$(printf %s "$IN" | PB_PROJECT="$P" python3 "$HOOK_DIR/task-dir-target.py" --creates)',
        ):
            self.assertIn("bad", self._kinds(line), line)

    def test_lines_it_accepts(self):
        for line, kinds in (
            ("python3 -I -c 'import sys; sys.exit(0)'", ["ok-inline"]),
            ('SCRIPT="$(python3 -I - "$PROJECT_ROOT" 2>/dev/null <<\'PYRESOLVE\'', ["ok-inline"]),
            ("python3 -IB -c 'pass'", ["ok-inline"]),
            ("python3 -Ic 'pass'", ["ok-inline"]),
            ("python3 -I -X utf8 -c 'pass'", ["ok-inline"]),
            ("python3 -I -m json.tool", ["ok-inline"]),
            ('python3 -E -s "$HOOK_DIR/task-status.py" "$RESOLVED"', ["ok-file"]),
            ('exec python3 -E -s "$GUARD"', ["ok-file"]),
            ('python3 -Es "$HOOK_DIR/task-status.py"', ["ok-file"]),
            ('python3 -s -B -E $MONITOR_SRC/sensor.py $JSONL', ["ok-file"]),
            ('python3 -I "$HOOK_DIR/task-status.py"', ["ok-file"]),
            ("( python3 -E -s <this-skill-dir>/merge-verify.py; echo x )", ["ok-file"]),
            # prose that names the interpreter beside something path-like is not a call
            ("Retry the tool call after restoring python3/the Playbook hook scripts.", ["other"]),
            ("could not read the task state via python3 ($HOOK_DIR/task-status.py --fields) — treating", ["other"]),
            # a PROJECT's own script, in a text that suggests a command: not the plugin's to isolate
            ('cmd = "python3 scripts/verify"', ["other"]),
            ("echo \"found: $(python3 --version 2>&1)\"", ["other"]),
            ("Playbook needs python3 >= 3.10 — python3 is missing", ["other", "other"]),
            ("where `python3 -m pytest --version` succeeds", ["ok-runner"]),
            ('{ "verify": "python3 -m pytest && mypy ." }', ["ok-runner"]),
            ("python3 -m unittest discover -s tests", ["ok-runner"]),
            ("python3.11 -c 'pass'  # another interpreter is not this rule's word", []),
            ("#!/usr/bin/env python3", ["other"]),
        ):
            self.assertEqual(self._kinds(line), kinds, line)

    def test_argv_lists(self):
        ok = 'subprocess.run([sys.executable, "-I", "-", os.path.realpath(p)], input=src)'
        self.assertEqual(argv_lists_in(ok), [("ok-inline", "-")])
        for bad in ('subprocess.run([sys.executable, "-c", code])',
                    "subprocess.run([sys.executable, '-m', 'tasks.cli', 'list'])",
                    'subprocess.run([sys.executable, "-B", "-", x])',
                    'subprocess.run([\n    sys.executable,\n    "-c", code, "-I"])',
                    'subprocess.run(["python3", "-c", code])',
                    "subprocess.run(['python', '-m', 'tasks.cli'])",
                    # impl panel r1 (codex-high): a bundle that ENDS in the mode flag
                    'subprocess.run([sys.executable, "-Bc", "import json"])',
                    'subprocess.run([sys.executable, "-B", "-um", "json.tool"])'):
            self.assertEqual([k for k, _ in argv_lists_in(bad)], ["bad"], bad)
        self.assertEqual(argv_lists_in('subprocess.run([sys.executable, str(script), "-c"])'),
                         [("other", "")])
        self.assertEqual(argv_lists_in('subprocess.run(["python3", "-I", "-c", code])'),
                         [("ok-inline", "-c")])
        for ok in ('subprocess.run([sys.executable, "-Ic", code])',
                   'subprocess.run([sys.executable, "-IB", "-c", code])',
                   'subprocess.run([sys.executable, "-B", "-Ic", code])'):
            self.assertEqual(argv_lists_in(ok), [("ok-inline", "-c")], ok)
        # a long option is not a mode, and what follows a file is the file's own
        self.assertEqual(argv_lists_in('subprocess.run([sys.executable, "--version"])'), [("other", "")])


if __name__ == "__main__":
    unittest.main()
