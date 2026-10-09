#!/usr/bin/env python3
"""Shipped Python is started isolated from the project it runs in (task 167, PLAN §S11 item 1).

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

Three kinds of proof here:
  1. behaviour — the shipped entry points, run in a temp project that holds the
     shadowing file, each beside a control run in a clean project;
  2. a sweep over every shipped file: an inline program or a module is never
     started without `-I`;
  3. the sweep's own rule, on strings it must refuse and strings it must accept.

What the sweep does NOT see (said in the ledger row too): a call through a
variable (`"$PY" -c …`), and — in Python sources — anything but an argv list that
starts with `sys.executable`.

Run: python3 -m unittest tests.test_python_isolation
"""
from __future__ import annotations

import json
import os
import re
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
        for k in ("PYTHONPATH", "PLAYBOOK_SESSION_ID", "PLAYBOOK_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
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
        # still exported, for whatever the CLI starts — the CLI itself no longer reads it
        self.assertEqual(pypath, f"PYTHONPATH={PLUGIN}")

    def test_the_users_own_pythonpath_is_still_passed_on(self):
        pypath, _ = self._given(SCRIPTS / "tasks", "list", PYTHONPATH="/somewhere/else")
        self.assertEqual(pypath, f"PYTHONPATH={PLUGIN}{os.pathsep}/somewhere/else")

    def test_the_sandbox_launcher(self):
        pypath, args = self._given(SCRIPTS / "sandbox", "--list-agents")
        self.assertEqual(args[:2], ["-I", "-c"])
        self.assertIn('runpy.run_module("provider.sandbox"', args[2])
        self.assertEqual(args[3:], [str(PLUGIN), "--list-agents"])
        self.assertEqual(pypath, f"PYTHONPATH={PLUGIN}")


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


class HelpersRunAsFilesWereNeverExposed(_Project):
    """The control behind leaving every `python3 <file>` call as it is: for a file,
    Python puts the FILE's directory first, not the working directory."""

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


def calls_on(line: str) -> list[tuple[str, str]]:
    """Every `python3` on one line of shell or markdown, as (kind, detail):
    `ok-inline` (inline code or a module, isolated), `ok-runner` (a project runner),
    `other` (a file, `--version`, prose) or `bad` (with the reason)."""
    found = []
    for m in _PY.finditer(line):
        toks = line[m.end():].split()
        isolated, i, verdict = False, 0, ("other", "")
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
                break                                   # a file, a long option, prose
            flags = t[1:]
            isolated = isolated or "I" in flags
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


_ARGV = re.compile(r"\[\s*sys\.executable\s*,([^\]]*)\]", re.S)


def argv_lists_in(source: str) -> list[tuple[str, str]]:
    """Every argv list in a Python source that starts with `sys.executable`."""
    found = []
    for m in _ARGV.finditer(source):
        lead = []
        for element in m.group(1).split(","):       # the flags that come straight after it
            lit = re.fullmatch(r"""["'](-[^"']*)["']""", element.strip())
            if lit is None:
                break                                # a file, a variable: not a flag any more
            lead.append(lit.group(1))
        mode = next((x for x in lead if x in ("-c", "-m", "-")), None)
        if mode is None:
            found.append(("other", ""))
        elif "-I" in lead[:lead.index(mode)]:
            found.append(("ok-inline", mode))
        else:
            found.append(("bad", f"[sys.executable, …, {mode!r}] without '-I' before it"))
    return found


def _is_python_source(path: Path, text: str) -> bool:
    return path.suffix == ".py" or (text.startswith("#!") and "python" in text.split("\n", 1)[0])


def sweep() -> tuple[list[str], dict[str, int]]:
    """(problems, counts by kind) over every shipped text file."""
    problems, counts = [], {}
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
    return problems, counts


class EveryShippedInlinePythonIsIsolated(unittest.TestCase):
    def test_the_sweep_finds_nothing(self):
        problems, _ = sweep()
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_sweep_is_not_blind(self):
        # a rule that matched nothing would pass the test above
        _, counts = sweep()
        self.assertGreaterEqual(counts.get("ok-inline", 0), 40, counts)
        self.assertGreaterEqual(counts.get("other", 0), 40, counts)
        self.assertGreaterEqual(counts.get("ok-runner", 0), 2, counts)

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
            ('python3 "$HOOK_DIR/task-status.py" "$RESOLVED"', ["other"]),
            ('exec python3 "$GUARD"', ["other"]),
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
                    'subprocess.run([\n    sys.executable,\n    "-c", code, "-I"])'):
            self.assertEqual([k for k, _ in argv_lists_in(bad)], ["bad"], bad)
        self.assertEqual(argv_lists_in('subprocess.run([sys.executable, str(script), "-c"])'),
                         [("other", "")])


if __name__ == "__main__":
    unittest.main()
