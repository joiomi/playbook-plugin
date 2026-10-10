#!/usr/bin/env python3
"""bash-log.sh logs what the agent ran — not every loop iteration, not the statusline (PLAN S5b).

The DEBUG trap fires for every simple command of every non-interactive bash in a
playbook project. On 2026-09-21 that meant 195,168 x 3 rows of loop iterations and
~40 statusline assignments per render (~710 renders a day), and the history reached
136 MB. The logger now writes each distinct command text once per shell process,
installs no trap in a shell that exports PLAYBOOK_NO_BASHLOG=1 or runs a script named
statusline, and rotates a history past 50 MB. Every probe runs the REAL bash-log.sh
through BASH_ENV, the way the harness does.

Run: python3 -m unittest tests.test_bash_log_scope
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

BL = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts" / "bash-log.sh"


class BashLogScope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.proj = Path(self._tmp.name) / "proj"
        (self.proj / ".agent" / "tasks").mkdir(parents=True)
        self.hist = self.proj / ".agent" / "bash_history"

    def _bash(self, script, extra_env=None, argv=None):
        env = dict(os.environ, BASH_ENV=str(BL))
        env.pop("PLAYBOOK_NO_BASHLOG", None)
        env.update(extra_env or {})
        cmd = argv or [bash_or_skip(), "-c", script]
        return subprocess.run(cmd, cwd=self.proj, env=env, capture_output=True, text=True, timeout=60)

    def _lines(self):
        return self.hist.read_text(encoding="utf-8", errors="replace").splitlines() if self.hist.exists() else []

    def test_a_loop_logs_each_command_text_once(self):
        self._bash("for i in 1 2 3 4 5; do echo $i >/dev/null; done; echo done >/dev/null")
        cmds = [ln.split(" | AGENT | ", 1)[1] for ln in self._lines()]
        # Exactly the header, the body ONCE, and the next command (panel r1 P9:
        # `<= 3` + one assertIn also passed with the loop body dropped).
        self.assertEqual(cmds, ["for i in 1 2 3 4 5", "echo $i > /dev/null", "echo done > /dev/null"])

    def test_distinct_commands_are_all_logged(self):
        self._bash("echo one >/dev/null; echo two >/dev/null; echo three >/dev/null")
        text = "\n".join(self._lines())
        for w in ("one", "two", "three"):
            self.assertIn(f"echo {w}", text)

    def test_a_shell_that_opts_out_logs_nothing(self):
        self._bash("echo statusline-shaped >/dev/null", {"PLAYBOOK_NO_BASHLOG": "1"})
        self.assertNotIn("statusline-shaped", "\n".join(self._lines()))
        self._bash("echo opted-in >/dev/null", {"PLAYBOOK_NO_BASHLOG": "0"})
        self.assertIn("opted-in", "\n".join(self._lines()))

    def test_a_script_named_statusline_is_not_logged(self):
        script = self.proj / "statusline.sh"
        script.write_text("#!/bin/bash\necho by-name >/dev/null\n", encoding="utf-8")
        script.chmod(0o755)
        self._bash("", argv=[bash_or_skip(), str(script)])
        self.assertNotIn("by-name", "\n".join(self._lines()))

    def test_a_directory_named_statusline_does_not_hide_its_scripts(self):
        # Only the script's own name counts: a script INSIDE a directory called
        # `statusline-tests` is an ordinary script, and its commands are logged.
        (self.proj / "statusline-tests").mkdir()
        (self.proj / "statusline-tests" / "run.sh").write_bytes(b"#!/bin/bash\necho in-a-statusline-dir >/dev/null\n")
        self._bash("", argv=[bash_or_skip(), "statusline-tests/run.sh"])
        self.assertIn("in-a-statusline-dir", "\n".join(self._lines()))

    def test_a_backslash_is_part_of_the_script_name(self):
        # Linux only (task 159): `sub\statusline.sh` is ONE file name — it is not the
        # statusline, and `w\statusline-tests\run.sh` is not a script in a directory.
        # Up to 1.5.47 the logger also cut the name at its last backslash (Git Bash
        # handed it `bash C:\…\statusline.sh`), which dropped the first script's commands.
        for rel, marker in (("sub\\statusline.sh", "by-backslash-name"),
                            ("w\\statusline-tests\\run.sh", "in-a-statusline-named-name")):
            with self.subTest(name=rel):
                (self.proj / rel).write_bytes(f"#!/bin/bash\necho {marker} >/dev/null\n".encode())
                self._bash("", argv=[bash_or_skip(), rel])
                self.assertIn(marker, "\n".join(self._lines()))

    def test_a_repeated_lifecycle_command_is_logged_each_time(self):
        # Panel r1 P5: `tasks work 7; tasks work 8; tasks work 7` in one shell
        # lost the second activation to the dedupe, and retro/timeline windows
        # are built from these lines. `tasks …` commands are never deduped.
        bindir = self.proj / "bin"
        bindir.mkdir()
        stub = bindir / "tasks"
        stub.write_bytes(b"#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
        path = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        self._bash("tasks work 7; tasks work 8; tasks work 7", {"PATH": path})
        work = [ln.split(" | AGENT | ", 1)[1] for ln in self._lines() if " | AGENT | tasks work" in ln]
        self.assertEqual(work, ["tasks work 7", "tasks work 8", "tasks work 7"])

    def test_a_loop_that_only_mentions_the_task_dir_is_still_deduped(self):
        # Panel r3 (grok): the r2 exemption (`*tasks*`) also exempted any loop
        # whose body names `.agent/tasks`, writing every iteration. Only
        # activation-shaped text (`tasks … work` / `tasks … new`) is exempt.
        self._bash("for i in 1 2 3; do echo .agent/tasks/x >/dev/null; done")
        body = [ln for ln in self._lines() if ln.endswith("| AGENT | echo .agent/tasks/x > /dev/null")]
        self.assertEqual(len(body), 1, self._lines())

    def test_a_quoted_tasks_path_is_never_deduped(self):
        # Panel r2 (sol-high, grok): the exemption matched only a bare `tasks `
        # or `/tasks `, so `"$B/tasks" work 7; … 8; … 7` lost the second 7. Any
        # command text containing `tasks` is now exempt (logging more is the
        # safe direction).
        bindir = self.proj / "b i n"
        bindir.mkdir()
        stub = bindir / "tasks"
        stub.write_bytes(b"#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
        self._bash('"$B/tasks" work 7; "$B/tasks" work 8; "$B/tasks" work 7', {"B": str(bindir)})
        work = [ln.split(" | AGENT | ", 1)[1] for ln in self._lines() if '/tasks" work' in ln]
        self.assertEqual(work, ['"$B/tasks" work 7', '"$B/tasks" work 8', '"$B/tasks" work 7'])

    def _second_project(self):
        other = self.proj.parent / "other"
        (other / ".agent" / "tasks").mkdir(parents=True)
        return other

    def test_the_same_command_in_two_projects_is_logged_in_both(self):
        # Panel r2 (sonnet, sol-high): the seen-set was global to the shell, so
        # one shell that ran the same text in two projects logged it only in the
        # first. The key now includes the working directory.
        other = self._second_project()
        self._bash('echo same >/dev/null; cd "$O"; echo same >/dev/null', {"O": str(other)})
        self.assertIn("echo same", "\n".join(self._lines()))
        other_hist = other / ".agent" / "bash_history"
        self.assertIn("echo same", other_hist.read_text(encoding="utf-8") if other_hist.exists() else "")

    def test_rotation_is_checked_for_each_project(self):
        # Panel r2 (sonnet, sol-high): the once-per-process rotation flag was set
        # by the FIRST project, so a second project's oversized history was never
        # rotated by that shell.
        other = self._second_project()
        other_hist = other / ".agent" / "bash_history"
        with open(other_hist, "wb") as fh:
            fh.truncate(51 * 1024 * 1024)
        self._bash('echo here >/dev/null; cd "$O"; echo there >/dev/null', {"O": str(other)})
        self.assertTrue([p for p in other_hist.parent.iterdir() if p.name.startswith("bash_history.archived-")])
        self.assertLess(other_hist.stat().st_size, 1024 * 1024)

    def test_rotation_carries_the_task_activations_forward(self):
        # Panel r2 (sol-high, sol-medium): retro builds each task's window from
        # its EARLIEST `tasks work N` line and reads only the live file, so a
        # rotation that archived the active task's activation made its window
        # vanish. The `tasks work|new` lines are copied into the fresh file.
        with open(self.hist, "wb") as fh:
            fh.write(b"2026-09-20 10:00:00 | AGENT | tasks new bugfix x\n"
                     b"2026-09-20 10:00:01 | AGENT | .claude/bin/tasks work 7\n"
                     b"2026-09-20 10:00:02 | AGENT | echo unrelated\n")
            fh.truncate(51 * 1024 * 1024)
        self._bash("echo after-rotation >/dev/null")
        live = self.hist.read_text(encoding="utf-8", errors="replace").splitlines()
        self.assertEqual(live[:2], ["2026-09-20 10:00:00 | AGENT | tasks new bugfix x",
                                    "2026-09-20 10:00:01 | AGENT | .claude/bin/tasks work 7"])
        self.assertTrue(live[2].endswith(" | AGENT | echo after-rotation > /dev/null"), live)
        self.assertEqual(len(live), 3, live)

    def test_rotation_under_errexit_keeps_the_shell_alive(self):
        # Panel r1 P3: the rotation branch (wc/mv/date) ran in no errexit test.
        with open(self.hist, "wb") as fh:
            fh.truncate(51 * 1024 * 1024)
        r = self._bash("set -euo pipefail; echo rotate-errexit >/dev/null; echo still-alive")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("still-alive", r.stdout)
        self.assertTrue([p for p in self.hist.parent.iterdir() if p.name.startswith("bash_history.archived-")])

    def test_a_history_past_50_mb_is_rotated(self):
        with open(self.hist, "wb") as fh:
            fh.truncate(51 * 1024 * 1024)
        self._bash("echo rotate >/dev/null")
        archived = [p.name for p in self.hist.parent.iterdir() if p.name.startswith("bash_history.archived-")]
        self.assertEqual(len(archived), 1, archived)
        # The archive IS the old history (panel r1 P9), not an empty stand-in.
        self.assertEqual((self.hist.parent / archived[0]).stat().st_size, 51 * 1024 * 1024)
        self.assertLess(self.hist.stat().st_size, 1024 * 1024)
        self.assertIn("echo rotate", self.hist.read_text(encoding="utf-8", errors="replace"))

    def test_a_small_history_is_not_rotated(self):
        self.hist.write_text("2026-09-24 00:00:00 | AGENT | echo old\n", encoding="utf-8")
        self._bash("echo new >/dev/null")
        self.assertFalse([p for p in self.hist.parent.iterdir() if p.name.startswith("bash_history.archived-")])
        self.assertIn("echo old", self.hist.read_text(encoding="utf-8"))

    def test_a_set_e_shell_survives_the_trap(self):
        r = self._bash("set -e; [ -d /nonexistent ] || true; false || true; echo still-alive")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("still-alive", r.stdout)


class RotationIsOneAtATimeAndLosesNoActivation(unittest.TestCase):
    """PLAN S11 (task 174; retro 107 R13: "Lock + prepared-temp replace"). The rotation
    was a `mv` of the history followed by an append of its activation lines to a new
    file: two shells that found it big together both rotated, a shell killed between
    the two steps left the activations only in the archive — and `tasks retro` /
    `tasks timeline` read only the live file — and every rotation carried every
    activation line ever written.

    These are orderings, so each test makes its ordering HAPPEN: a small script first
    on PATH stands in for one command the rotation calls and, at that call, holds it
    until a second shell has run, dies as a killed process would, or writes what
    another shell would write just then."""

    ACTIVATION = " | AGENT | .claude/bin/tasks work "
    BIG = 51 * 1024 * 1024

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.proj = Path(self._tmp.name) / "proj"
        (self.proj / ".agent" / "tasks").mkdir(parents=True)
        self.hist = self.proj / ".agent" / "bash_history"
        self.bindir = Path(self._tmp.name) / "shims"
        self.bindir.mkdir()

    def _env(self, shims=False, bash_env=None):
        env = dict(os.environ, BASH_ENV=str(bash_env or BL))
        env.pop("PLAYBOOK_NO_BASHLOG", None)
        if shims:
            env["PATH"] = f"{self.bindir}{os.pathsep}{os.environ['PATH']}"
        return env

    def _bash(self, script, **kw):
        return subprocess.run([bash_or_skip(), "-c", script], cwd=self.proj, env=self._env(**kw),
                              capture_output=True, text=True, timeout=120)

    def _big_history(self, activations=(7, 8)):
        """A history past 50 MB of whole lines: the given activations first, then filler."""
        with open(self.hist, "wb") as fh:
            for n in activations:
                fh.write(f"2026-09-20 10:00:00{self.ACTIVATION}{n}\n".encode())
            filler = b"2026-09-20 10:00:02 | AGENT | echo filler " + b"x" * 950 + b"\n"
            fh.write(filler * (self.BIG // len(filler) + 1))
        self.assertGreater(self.hist.stat().st_size, 50 * 1024 * 1024)

    def _archives(self):
        return sorted(p for p in self.hist.parent.iterdir()
                      if p.name.startswith("bash_history.archived-") and not p.name.endswith(".new")
                      and p.name != "bash_history.archived-owes")

    def _leftovers(self):
        return sorted(p.name for p in self.hist.parent.iterdir() if p.name.endswith(".new"))

    @property
    def owes(self):
        """The marker: the name of the archive that still owes the live file its
        activation lines. There from before anything is moved until they are back."""
        return self.hist.parent / "bash_history.archived-owes"

    def _settled(self):
        """The shell that rotates leaves the marker on purpose: the NEXT shell gives
        back once more — a line another shell had in flight at the rename lands in
        the archive after the first giving-back — and only then removes it (owner,
        2026-10-10). So: one more shell, and then nothing of a rotation is left, and
        nothing was given twice."""
        before = self._live_activations()
        if self.owes.exists():
            self._bash("echo the-next-shell >/dev/null")
        self.assertEqual(self._leftovers(), [])
        self.assertFalse(self.owes.exists(), "after the next shell something is still owed")
        self.assertEqual(self._live_activations(), before, "the second giving-back gave a line twice")

    def _live_activations(self):
        text = self.hist.read_text(encoding="utf-8", errors="replace") if self.hist.exists() else ""
        return [ln.split(self.ACTIVATION, 1)[1].split()[0] for ln in text.splitlines() if self.ACTIVATION in ln]

    def _shim(self, tool, body):
        """`tool` first on PATH: `body` (POSIX sh) runs with REAL=<the real binary>; it
        decides. A `/bin/sh` script on purpose, never a bash one: a bash script started
        from the logger is itself a logged shell (BASH_ENV is inherited), finds the same
        oversized history and starts a rotation of its own — which calls this script
        again. The first version of these tests did exactly that and ran the machine out
        of processes (`BlockingIOError: Resource temporarily unavailable` in the tests
        that came after)."""
        import shutil
        real = shutil.which(tool)
        self.assertTrue(real, tool)
        (self.bindir / tool).write_text(f'#!/bin/sh\nREAL="{real}"\n{body}\nexec "$REAL" "$@"\n', encoding="utf-8")
        (self.bindir / tool).chmod(0o755)

    # -- (1) one rotator at a time ------------------------------------------------------
    def test_two_shells_that_find_it_big_together_make_one_archive(self):
        import time
        self._big_history()
        hold, release = Path(self._tmp.name) / "hold", Path(self._tmp.name) / "release"
        # the first shell to reach the archive's time stamp stops there until released
        self._shim("date", f"""case "$*" in *%Y%m%d-%H%M%S*)
    if [ ! -e "{hold}" ]; then
        : > "{hold}"
        for _ in $(seq 1 400); do [ -e "{release}" ] && break; /bin/sleep 0.05; done
    fi ;;
esac""")
        a = subprocess.Popen([bash_or_skip(), "-c", "echo from-A >/dev/null"], cwd=self.proj,
                             env=self._env(shims=True), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(400):
                if hold.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(hold.exists(), "the first shell never reached its rotation")
            b = self._bash("echo from-B >/dev/null", shims=True)       # a second shell, meanwhile
            self.assertEqual(b.returncode, 0, b.stderr)
        finally:
            release.write_text("", encoding="utf-8")
            a.communicate(timeout=60)
        archives = self._archives()
        self.assertEqual(len(archives), 1, [p.name for p in archives])
        self.assertGreater(archives[0].stat().st_size, 50 * 1024 * 1024)      # it IS the old history
        self.assertLess(self.hist.stat().st_size, 1024 * 1024)
        self.assertEqual(self._live_activations(), ["7", "8"])
        everything = self.hist.read_bytes() + archives[0].read_bytes()[-4096:]
        for line in (b"echo from-A", b"echo from-B"):
            self.assertIn(line, everything)                                   # logged, in one or the other
        self._settled()

    def test_a_shell_that_looked_before_another_rotated_does_not_rotate_again(self):
        # The second look, under the lock. Shell A has seen the history big — its
        # first look has answered — and is held right there; shell B rotates
        # completely; A then opens the live name (the fresh, small file), gets its
        # lock, and must look again, by name, or it would rotate the file B just
        # made. (Six shells at once do not show this: the ones that lose the
        # non-blocking lock simply skip. A break without the second look passed that
        # test, which is why this one exists.)
        import time
        self._big_history()
        hold, release = Path(self._tmp.name) / "hold", Path(self._tmp.name) / "release"
        import shutil
        real_find = shutil.which("find")
        (self.bindir / "find").write_text(f"""#!/bin/sh
if [ ! -e "{hold}" ]; then
    : > "{hold}"
    "{real_find}" "$@"
    for _ in $(seq 1 400); do [ -e "{release}" ] && break; /bin/sleep 0.05; done
    exit 0
fi
exec "{real_find}" "$@"
""", encoding="utf-8")
        (self.bindir / "find").chmod(0o755)
        a = subprocess.Popen([bash_or_skip(), "-c", "echo from-A >/dev/null"], cwd=self.proj,
                             env=self._env(shims=True), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(400):
                if hold.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(hold.exists(), "the first shell never looked")
            b = self._bash("echo from-B >/dev/null", shims=True)       # rotates, start to end
            self.assertEqual(b.returncode, 0, b.stderr)
            self.assertEqual(len(self._archives()), 1, "the case under test: B has rotated")
        finally:
            release.write_text("", encoding="utf-8")
            a.communicate(timeout=60)
        archives = self._archives()
        self.assertEqual(len(archives), 1, [p.name for p in archives])
        self.assertGreater(archives[0].stat().st_size, 50 * 1024 * 1024)
        self.assertEqual(self._live_activations(), ["7", "8"])
        live = self.hist.read_text(encoding="utf-8", errors="replace")
        self.assertIn("echo from-A", live)
        self.assertIn("echo from-B", live)
        self._settled()

    def test_a_lock_on_a_file_that_is_no_longer_the_history_is_no_lock(self):
        # Shell A opened the history and is held just before it takes the lock; B
        # rotates completely. The lock A then gets is on the OLD file — the archive.
        # Whatever is to be done in this lane is done under the lock of the file that
        # has the live name, and that is not what A holds: here something is owed
        # (planted) and another shell (this test) holds the live file's lock.
        import fcntl
        import time
        self._big_history()
        hold, release = Path(self._tmp.name) / "hold", Path(self._tmp.name) / "release"
        self._shim("flock", f"""if [ ! -e "{hold}" ]; then
    : > "{hold}"
    for _ in $(seq 1 400); do [ -e "{release}" ] && break; /bin/sleep 0.05; done
fi""")
        a = subprocess.Popen([bash_or_skip(), "-c", "echo from-A >/dev/null"], cwd=self.proj,
                             env=self._env(shims=True), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(400):
                if hold.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(hold.exists(), "the first shell never reached the lock")
            b = self._bash("echo from-B >/dev/null")                   # rotates, start to end
            self.assertEqual(b.returncode, 0, b.stderr)
            (archive,) = self._archives()
            with open(archive, "ab") as fh:                            # … and the archive owes one line
                fh.write(f"2026-09-20 11:11:11{self.ACTIVATION}55\n".encode())
            self.owes.write_text(archive.name + "\n", encoding="utf-8")
            with open(self.hist, "ab") as holder:
                fcntl.flock(holder, fcntl.LOCK_EX)                     # the live file's lock is taken
                release.write_text("", encoding="utf-8")
                a.communicate(timeout=60)
                self.assertTrue(self.owes.exists(), "a shell holding the OLD file's lock settled what is owed")
                self.assertNotIn("55", self._live_activations())
        finally:
            release.write_text("", encoding="utf-8")
            if a.poll() is None:
                a.communicate(timeout=60)
        self._bash("echo after-them >/dev/null")                       # nobody holds it now
        self.assertEqual(sorted(self._live_activations()), ["55", "7", "8"])
        self.assertEqual(len(self._archives()), 1)
        self._settled()

    def test_six_shells_at_once_make_one_archive(self):
        # not the proof (the test above is) — the same thing with no script in the way
        self._big_history()
        procs = [subprocess.Popen([bash_or_skip(), "-c", f"echo from-{i} >/dev/null"], cwd=self.proj,
                                  env=self._env(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                 for i in range(6)]
        for p in procs:
            _, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
            self.assertEqual(err, "")
        self.assertEqual(len(self._archives()), 1, [p.name for p in self._archives()])
        self.assertEqual(self._live_activations(), ["7", "8"])
        self._settled()

    # -- (2) a rotation that dies ---------------------------------------------------------
    def test_a_rotation_that_dies_at_any_step_leaves_the_activations_in_the_live_file(self):
        dies = 'case "$*" in *{mark}*) exit 137 ;; esac'       # gone, as a killed process is, having done nothing
        for tool, mark in (("grep", "tasks"), ("tail", "5242880"), ("ln", "bash_history.archived-"),
                           ("mv", "bash_history.archived-")):
            with self.subTest(dies_at=tool):
                self.setUp()
                self._big_history()
                self._shim(tool, dies.format(mark=mark))
                r = self._bash("set -e; echo while-it-died >/dev/null; echo still-alive", shims=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("still-alive", r.stdout)
                self.assertEqual(r.stderr, "")
                self.assertEqual(self._live_activations(), ["7", "8"],
                                 "the activations are no longer in the file retro and timeline read")
                # … and the next shell, with nothing in its way, rotates
                self._bash("echo after-it >/dev/null")
                self.assertTrue(self._archives())
                self.assertEqual(self._live_activations(), ["7", "8"])
                self.assertLess(self.hist.stat().st_size, 1024 * 1024)
                self._settled()

    # -- (2b) … and what it had not given back yet, the next shell gives ---------------------
    def _dies_reading_an_archive(self):
        """`grep` dies whenever it is asked to read an ARCHIVE — which is how the lines
        a rotation carried, or that were written meanwhile, get back into the live file."""
        self._shim("grep", 'case "$*" in *bash_history.archived-*) exit 137 ;; esac')

    def test_a_death_after_the_rename_is_finished_by_the_next_shell(self):
        # impl panel round 1 (codex-high, codex-medium): another shell writes an
        # activation after the lines were read out, the rename happens, and the
        # rotator dies before it has given that line back. It is in the archive only.
        self._big_history()
        line = f"2026-09-20 11:11:11{self.ACTIVATION}99"
        self._shim("ln", f"""case "$*" in *bash_history.archived-*)
    [ -e "{self._tmp.name}/wrote" ] || {{ : > "{self._tmp.name}/wrote"; echo '{line}' >> "{self.hist}"; }} ;;
esac""")
        self._dies_reading_an_archive()
        r = self._bash("set -e; echo while-it-died >/dev/null; echo still-alive", shims=True)
        self.assertIn("still-alive", r.stdout)
        self.assertEqual(r.stderr, "")
        # the case under test: renamed (the carried lines are there), the late one is not
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertEqual(len(self._archives()), 1)
        self._bash("echo the-next-shell >/dev/null")
        self.assertEqual(self._live_activations(), ["7", "8", "99"])
        self.assertEqual(len(self._archives()), 1)
        self._settled()

    def test_a_death_after_the_move_where_no_hard_link_can_be_made_is_finished_by_the_next_shell(self):
        # impl panel round 1 (sonnet, codex-medium): the fallback moves the history
        # away and then gives the lines back — a death between the two left every
        # activation in the archive, the defect this task exists for.
        self._big_history()
        self._shim("ln", 'case "$*" in *bash_history.archived-*) exit 1 ;; esac')
        self._dies_reading_an_archive()
        self._shim("cat", 'case "$*" in *bash_history.archived-*) exit 137 ;; esac')
        r = self._bash("set -e; echo while-it-died >/dev/null; echo still-alive", shims=True)
        self.assertIn("still-alive", r.stdout)
        self.assertEqual(self._live_activations(), [], "the case under test: moved away, nothing given back")
        self.assertEqual(len(self._archives()), 1)
        self._bash("echo the-next-shell >/dev/null")
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertEqual(len(self._archives()), 1)
        self.assertGreater(self._archives()[0].stat().st_size, 50 * 1024 * 1024)
        self._settled()

    def test_without_a_lock_what_is_owed_is_left_alone(self):
        # impl panel round 2 (all four seats) and the owner's ruling on it: where no
        # lock can be had nothing in the lane is settled either — two shells there
        # would give back at once, and one would remove the other's marker.
        archive = self.hist.parent / "bash_history.archived-20260101-000000-1"
        archive.write_text(f"2026-09-20 11:11:11{self.ACTIVATION}9\n", encoding="utf-8")
        self.hist.write_text(f"2026-09-20 10:00:00{self.ACTIVATION}7\n", encoding="utf-8")
        self.owes.write_text(archive.name + "\n", encoding="utf-8")
        env = self._env()
        env["PATH"] = self._path_without("flock")
        r = subprocess.run([bash_or_skip(), "-c", "set -e; echo a-line >/dev/null; echo still-alive"],
                           cwd=self.proj, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip(), "still-alive", r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertTrue(self.owes.exists(), "without a lock the marker was settled")
        self.assertEqual(self._live_activations(), ["7"])
        self.assertIn("echo a-line", self.hist.read_text(encoding="utf-8"))      # logging itself goes on
        # … and on a host with one, the next shell gives it
        self._bash("echo with-a-lock >/dev/null")
        self.assertEqual(self._live_activations(), ["7", "9"])
        self._settled()

    def test_nothing_is_given_back_without_the_lock_of_the_new_live_file(self):
        # After the rename the live name is another file, and the lock the rotator
        # holds is on the old one. It takes the new file's lock before it gives
        # anything back; if another shell has it, that shell sees what is owed.
        self._big_history()
        line = f"2026-09-20 11:11:11{self.ACTIVATION}99"
        self._shim("ln", f"""case "$*" in *bash_history.archived-*)
    [ -e "{self._tmp.name}/wrote" ] || {{ : > "{self._tmp.name}/wrote"; echo '{line}' >> "{self.hist}"; }} ;;
esac""")
        count = Path(self._tmp.name) / "flock-calls"
        self._shim("flock", f"""echo x >> "{count}"
[ "$(wc -l < "{count}")" -ge 2 ] && exit 1""")                 # the second lock: held by another shell
        r = self._bash("set -e; echo rotating >/dev/null; echo still-alive", shims=True)
        self.assertIn("still-alive", r.stdout)
        asked = len(count.read_text().splitlines()) if count.exists() else 0
        self.assertEqual(asked, 2, "the case under test: the lock was asked for twice")
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertTrue(self.owes.exists(), "what is owed must stay written down for the shell that has the lock")
        self._bash("echo the-shell-with-the-lock >/dev/null")
        self.assertEqual(self._live_activations(), ["7", "8", "99"])
        self._settled()

    def test_a_marker_that_names_no_archive_of_this_lane_is_dropped(self):
        # The marker is a file in a directory the project's user writes; what it
        # says is read as a NAME in this lane and nothing else.
        for said in ("../../outside", "../outside", "/etc/hostname", "bash_history", "not-an-archive",
                     "bash_history.archived-20260101-000000-1", "bash_history.archived-owes", ""):
            with self.subTest(said=said):
                self.setUp()
                outside = self.proj / "outside"
                outside.write_text(f"2026-09-20 11:11:11{self.ACTIVATION}666\n", encoding="utf-8")
                self.hist.write_text(f"2026-09-20 10:00:00{self.ACTIVATION}7\n", encoding="utf-8")
                self.owes.write_text(said + "\n", encoding="utf-8")
                r = self._bash("set -e; echo a-line >/dev/null; echo still-alive")
                self.assertIn("still-alive", r.stdout)
                self.assertEqual(r.stderr, "")
                self.assertFalse(self.owes.exists(), "a marker nothing can come of stays for ever")
                self.assertEqual(self._live_activations(), ["7"])
                self.assertEqual(outside.read_text(encoding="utf-8"), f"2026-09-20 11:11:11{self.ACTIVATION}666\n")
                self.assertEqual(self._archives(), [])

    def test_a_marker_that_names_a_link_is_dropped(self):
        outside = self.proj / "outside"
        outside.write_text(f"2026-09-20 11:11:11{self.ACTIVATION}666\n", encoding="utf-8")
        self.hist.write_text(f"2026-09-20 10:00:00{self.ACTIVATION}7\n", encoding="utf-8")
        link = self.hist.parent / "bash_history.archived-20260101-000000-1"
        link.symlink_to(outside)
        self.owes.write_text(link.name + "\n", encoding="utf-8")
        r = self._bash("set -e; echo a-line >/dev/null; echo still-alive")
        self.assertIn("still-alive", r.stdout)
        self.assertFalse(self.owes.exists())
        self.assertEqual(self._live_activations(), ["7"])
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), f"2026-09-20 11:11:11{self.ACTIVATION}666\n")

    def test_a_flock_that_cannot_lock_means_no_rotation(self):
        # flock(1) answers 1 when the lock is held, and something else when it
        # cannot lock at all (no lock manager on the mount). Round 1 sent the second
        # case to an order without a lock; round 2 showed that order unsafe; the
        # owner's ruling (2026-10-10): no lock, no rotation — the history grows and
        # nothing in it is lost.
        self._big_history()
        size = self.hist.stat().st_size
        self._shim("flock", "exit 71")
        r = self._bash("set -e; echo not-rotated >/dev/null; echo still-alive", shims=True)
        self.assertEqual(r.stdout.strip(), "still-alive", r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertEqual(self._archives(), [])
        self.assertGreater(self.hist.stat().st_size, size)                     # the line was logged
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertFalse(self.owes.exists())
        self.assertEqual(self._leftovers(), [])

    def test_a_rename_that_happened_but_reported_failure_does_not_cost_the_archive(self):
        # impl panel round 1 (opus): `mv` put the prepared file under the live name
        # and then said it failed. The archive is then the ONLY name of the old
        # history — what is done next is read off the files, not off `mv`.
        self._big_history()
        self._shim("mv", 'case "$*" in *bash_history.archived-*.new*) "$REAL" "$@"; exit 1 ;; esac')
        r = self._bash("set -e; echo rotate >/dev/null; echo still-alive", shims=True)
        self.assertIn("still-alive", r.stdout)
        archives = self._archives()
        self.assertEqual(len(archives), 1, [p.name for p in archives])
        self.assertGreater(archives[0].stat().st_size, 50 * 1024 * 1024)
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertLess(self.hist.stat().st_size, 1024 * 1024)
        self._settled()

    def test_a_rotator_killed_outright_is_cleaned_up_after_by_the_next(self):
        # SIGKILL at the rename: nothing of the rotation's own gets to tidy up
        self._big_history()
        self._shim("mv", 'case "$*" in *bash_history.archived-*) kill -9 $PPID; exit 137 ;; esac')
        r = self._bash("echo while-it-was-killed >/dev/null; echo still-alive", shims=True)
        self.assertIn("still-alive", r.stdout)
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertEqual(len(self._leftovers()), 1, "the case under test: a prepared file was left behind")
        self.assertTrue(self.owes.exists(), "the case under test: it had written down what it was about to do")
        self.assertEqual(len(self._archives()), 1, "the case under test: the archive's name is on the live file")
        self._bash("echo after-it >/dev/null")
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertLess(self.hist.stat().st_size, 1024 * 1024)
        # ONE archive: the name the killed rotator had given the history — a second
        # name of the file still in use — is taken back before the next rotation
        archives = self._archives()
        self.assertEqual(len(archives), 1, [p.name for p in archives])
        self.assertGreater(archives[0].stat().st_size, 50 * 1024 * 1024)
        self._settled()

    # -- the shell it runs in is somebody's script -----------------------------------------
    def test_functions_of_the_host_script_named_like_its_tools_are_not_called(self):
        # The rotation runs inside the DEBUG trap of whatever script the shell runs —
        # and that script may define functions called `mv`, `grep`, `rm` …
        self._big_history()
        hijack = "; ".join(f"{t}() {{ echo hijacked-{t}; return 0; }}" for t in
                           ("mv", "grep", "ln", "tail", "cat", "rm", "wc", "flock", "find", "printf", "read"))
        r = self._bash(hijack + "; echo first >/dev/null; echo still-alive")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "still-alive")            # none of them ran, nothing printed
        archives = self._archives()
        self.assertEqual(len(archives), 1, [p.name for p in archives])
        self.assertGreater(archives[0].stat().st_size, 50 * 1024 * 1024)
        self.assertLess(self.hist.stat().st_size, 1024 * 1024)
        self.assertEqual(self._live_activations(), ["7", "8"])
        self._settled()

    def test_a_tool_that_is_itself_a_bash_script_is_not_a_logged_shell(self):
        # A wrapper on PATH written in bash is a shell started from inside the
        # rotation: with BASH_ENV inherited it would be logged like any other, and
        # look at the same oversized history. It starts with logging off.
        import shutil
        self._big_history()
        real = shutil.which("grep")
        (self.bindir / "grep").write_text(
            f'#!/bin/bash\n: wrapper-marker-7f3a\nexec "{real}" "$@"\n', encoding="utf-8")
        (self.bindir / "grep").chmod(0o755)
        r = self._bash("echo rotating >/dev/null; echo still-alive", shims=True)
        self.assertIn("still-alive", r.stdout)
        archives = self._archives()
        self.assertEqual(len(archives), 1, [p.name for p in archives])
        self.assertEqual(self._live_activations(), ["7", "8"])
        logged = self.hist.read_bytes() + archives[0].read_bytes()[-65536:]
        self.assertNotIn(b"wrapper-marker-7f3a", logged, "a command of the wrapper itself was logged")

    # -- an activation written while the rotation is under way ------------------------------
    def test_an_activation_written_during_the_rotation_reaches_the_live_file(self):
        self._big_history()
        line = f"2026-09-20 11:11:11{self.ACTIVATION}99"
        # another shell's write, landing after the rotation read the history and
        # before the fresh file is in place
        self._shim("ln", f"""case "$*" in *bash_history.archived-*)
    [ -e "{self._tmp.name}/wrote" ] || {{ : > "{self._tmp.name}/wrote"; echo '{line}' >> "{self.hist}"; }} ;;
esac""")
        self._bash("echo rotating >/dev/null", shims=True)
        self.assertTrue((Path(self._tmp.name) / "wrote").exists(), "the write under test never happened")
        self.assertEqual(len(self._archives()), 1)
        self.assertEqual(self._live_activations(), ["7", "8", "99"])
        self._settled()

    # -- (3) the bound ------------------------------------------------------------------------
    BOUND = 5 * 1024 * 1024

    def test_the_carried_lines_are_bounded_by_size_and_are_the_newest_whole_ones(self):
        # impl panel round 1 (codex-high) and a measurement: on this plugin's own
        # workspace an activation-shaped line is 583 bytes on average and 42,680 at
        # the longest — a bound in LINES bounds neither the fresh file nor how far
        # back it reaches. The newest 5 MiB of them, whole lines.
        width = 1000                 # not a divisor of the bound: the cut falls inside a line
        with open(self.hist, "wb") as fh:
            for n in range(1, 6001):                                    # 6,000,000 bytes of activations
                head = f"2026-09-20 10:00:00{self.ACTIVATION}{n} #".encode()
                fh.write(head + b"p" * (width - len(head) - 1) + b"\n")
            filler = b"2026-09-20 10:00:02 | AGENT | echo filler " + b"x" * 950 + b"\n"
            fh.write(filler * (self.BIG // len(filler) + 1))
        self._bash("echo rotate >/dev/null")
        first = self.hist.read_bytes().split(b"\n", 1)[0]
        self.assertTrue(len(first) + 1 == width and first.startswith(b"2026-09-20 10:00:00 | AGENT | "),
                        f"the fresh file starts with a piece of a line: {first[:40]!r}… ({len(first)} bytes)")
        lines = [ln for ln in self.hist.read_bytes().split(b"\n") if self.ACTIVATION.encode() in ln]
        size = sum(len(ln) + 1 for ln in lines)
        self.assertLessEqual(size, self.BOUND)
        self.assertGreater(size, self.BOUND - 2 * width, "fewer lines were carried than the bound has room for")
        self.assertTrue(all(len(ln) + 1 == width and ln.startswith(b"2026-09-20 10:00:00") for ln in lines),
                        "a carried line is not a whole one")
        numbers = [int(n) for n in self._live_activations()]
        self.assertEqual(numbers, list(range(numbers[0], 6001)))        # the newest, none missing
        self.assertLess(self.hist.stat().st_size, self.BOUND + 1024 * 1024)
        self._settled()

    def test_six_thousand_short_activation_lines_are_all_carried(self):
        self._big_history(activations=range(1, 6001))
        self._bash("echo rotate >/dev/null")
        carried = self._live_activations()
        self.assertEqual(len(carried), 6000)
        self.assertEqual((carried[0], carried[-1]), ("1", "6000"))

    def _history_of_wide_activations(self, count, width=1024):
        with open(self.hist, "wb") as fh:
            for n in range(1, count + 1):
                head = f"2026-09-20 10:00:00{self.ACTIVATION}{n} #".encode()
                fh.write(head + b"p" * (width - len(head) - 1) + b"\n")
            filler = b"2026-09-20 10:00:02 | AGENT | echo filler " + b"x" * 950 + b"\n"
            fh.write(filler * (self.BIG // len(filler) + 1))

    def test_activation_lines_of_exactly_the_bound_are_all_carried(self):
        # impl panel round 2 (codex-high): 5,120 lines of 1,024 bytes ARE 5 MiB, and
        # one of them was dropped — the first, a whole line, taken for the cut one.
        for count, kept in ((5120, 5120), (5121, 5120)):
            with self.subTest(lines=count):
                self.setUp()
                self._history_of_wide_activations(count)
                self._bash("echo rotate >/dev/null")
                numbers = [int(n) for n in self._live_activations()]
                self.assertEqual(len(numbers), kept)
                self.assertEqual(numbers, list(range(count - kept + 1, count + 1)))
                self.assertEqual(kept * 1024, self.BOUND)

    # -- what the next shell is left to do --------------------------------------------------
    def test_the_marker_is_left_for_the_next_shell_which_gives_back_once_more(self):
        # impl panel round 2 (codex-high, codex-medium) and the owner's ruling on it:
        # a shell that had the old file open at the rename writes its line into the
        # archive AFTER the rotator gave back. The rotator therefore does not say
        # "nothing is owed"; the next shell looks once more, and says it.
        self._big_history()
        self._bash("echo rotate >/dev/null")
        (archive,) = self._archives()
        self.assertTrue(self.owes.exists(), "the shell that rotated said itself that nothing is owed")
        self.assertEqual(self.owes.read_text(encoding="utf-8"), archive.name + "\n")
        self.assertEqual(self._live_activations(), ["7", "8"])
        with open(archive, "ab") as fh:                                # the line that was in flight
            fh.write(f"2026-09-20 11:11:11{self.ACTIVATION}77\n".encode())
        self._bash("echo the-next-shell >/dev/null")
        self.assertEqual(self._live_activations(), ["7", "8", "77"])
        self.assertFalse(self.owes.exists())
        self.assertEqual(len(self._archives()), 1)
        self.assertEqual(self._leftovers(), [])

    def test_a_live_file_that_ends_inside_a_line_is_closed_before_anything_is_given_back(self):
        # impl panel round 2 (codex-high): a giving-back killed inside a line leaves a
        # piece of it; the repeat appended the whole line straight after that piece —
        # one line nothing can read, and the marker gone.
        whole = f"2026-09-20 10:00:00{self.ACTIVATION}7"
        archive = self.hist.parent / "bash_history.archived-20260101-000000-1"
        archive.write_text(whole + "\n", encoding="utf-8")
        self.hist.write_text("2026-10-10 10:00:00 | AGENT | echo earlier\n" + whole[:-12], encoding="utf-8")
        self.owes.write_text(archive.name + "\n", encoding="utf-8")
        r = self._bash("set -e; echo a-line >/dev/null; echo still-alive")
        self.assertIn("still-alive", r.stdout)
        lines = self.hist.read_text(encoding="utf-8").split("\n")
        self.assertIn(whole, lines, "the activation is not a line of its own")
        self.assertIn(whole[:-12], lines)                              # the piece stays, on its own line
        self.assertFalse(self.owes.exists())

    # -- where the new sequence cannot run ------------------------------------------------------
    def _path_without(self, tool):
        """A PATH holding every command of the real one except `tool`."""
        d = Path(self._tmp.name) / f"path-without-{tool}"
        d.mkdir()
        for src in os.environ["PATH"].split(os.pathsep):
            try:
                names = os.listdir(src)
            except OSError:
                continue
            for name in names:
                if name != tool and not os.path.lexists(d / name):
                    os.symlink(os.path.join(src, name), d / name)
        return str(d)

    def test_without_flock_on_the_host_nothing_is_rotated(self):
        # The owner's ruling of 2026-10-10 ("varianta 1"): where no lock can be had
        # the history is not rotated at all — it grows, and nothing in it is lost.
        # (A Linux host without the `flock` program: a minimal image. The logger
        # before this task rotated there, with nothing to keep two shells apart.)
        env = self._env()
        env["PATH"] = self._path_without("flock")
        self._big_history()
        size = self.hist.stat().st_size
        # … and a FUNCTION of the host's script called `flock` is not the tool
        r = subprocess.run([bash_or_skip(), "-c", "flock() { echo hijacked-flock; }; set -e; "
                            "echo not-rotated >/dev/null; echo still-alive"],
                           cwd=self.proj, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip(), "still-alive", r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertEqual(self._archives(), [])
        self.assertGreater(self.hist.stat().st_size, size)                     # the line was logged
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertFalse(self.owes.exists())
        self.assertEqual(self._leftovers(), [])

    def test_an_archive_that_already_exists_is_never_replaced(self):
        # The archive's name is the second and the shell's pid. Should that name be
        # taken already, the hard link fails — and that failure must not be read as
        # "no hard links here": the fallback's `mv -f` would replace the archive.
        self._big_history()
        self._shim("date", 'case "$*" in *%Y%m%d-%H%M%S*) echo 20260101-000000; exit 0 ;; esac')
        elsewhere = Path(self._tmp.name) / "elsewhere"
        elsewhere.mkdir()
        taken = f'"{self.hist.parent}/bash_history.archived-20260101-000000-$$"'
        r = subprocess.run([bash_or_skip(), "-c",
                            f"echo an-older-archive > {taken}; cd '{self.proj}'; set -e; "
                            "echo in-the-project >/dev/null; echo still-alive"],
                           cwd=elsewhere, env=self._env(shims=True), capture_output=True, text=True, timeout=120)
        self.assertIn("still-alive", r.stdout)
        self.assertEqual(r.stderr, "")
        archives = self._archives()
        self.assertEqual(len(archives), 1, [p.name for p in archives])
        self.assertEqual(archives[0].read_text(encoding="utf-8"), "an-older-archive\n")
        self.assertGreater(self.hist.stat().st_size, 50 * 1024 * 1024)      # not rotated this time
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertEqual(self._leftovers(), [])
        # the next shell — another name — rotates
        self._bash("echo after-it >/dev/null")
        self.assertEqual(len(self._archives()), 2)
        self.assertLess(self.hist.stat().st_size, 1024 * 1024)
        self.assertEqual(self._live_activations(), ["7", "8"])

    def test_where_a_hard_link_cannot_be_made_it_still_rotates(self):
        self._big_history()
        self._shim("ln", 'case "$*" in *bash_history.archived-*) echo "ln: failed to create hard link: Operation not permitted" >&2; exit 1 ;; esac')
        r = self._bash("set -e; echo rotate >/dev/null; echo still-alive", shims=True)
        self.assertIn("still-alive", r.stdout)
        self.assertEqual(r.stderr, "")
        self.assertEqual(len(self._archives()), 1)
        self.assertGreater(self._archives()[0].stat().st_size, 50 * 1024 * 1024)
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertLess(self.hist.stat().st_size, 1024 * 1024)
        self._settled()


class RotationArchiveIsIgnored(unittest.TestCase):
    """Panel r3 (opus): rotation mints `bash_history.archived-<date>-<pid>`, but the
    ignore block init seeds listed only `bash_history`, so an archive was an
    untracked ~50 MB file in `git status` — and in the close's tree fingerprint."""

    def test_the_seeded_ignore_block_covers_both_archive_names(self):
        import importlib.util
        import shutil
        git = shutil.which("git")
        if not git:
            self.skipTest("git not on PATH")
        merge = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts" / "claude-md-merge.py"
        spec = importlib.util.spec_from_file_location("claude_md_merge", merge)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run([git, "init", "-q", str(repo)], check=True)
            (repo / ".gitignore").write_bytes(("\n".join(mod.GITIGNORE_ENTRIES) + "\n").encode())
            for rel in (".agent/bash_history.archived-20260924-101010-42",
                        ".agent/alice/bash_history.archived-20260924-101010-42",
                        # the file a rotation prepares beside the history (task 174)
                        ".agent/bash_history.archived-20260924-101010-42.new",
                        ".agent/alice/bash_history.archived-20260924-101010-42.new",
                        # … what it reads out while it gives lines back, and the marker
                        # that says an archive still owes some (impl panel round 1)
                        ".agent/bash_history.archived-20260924-101010-42.owed.new",
                        ".agent/bash_history.archived-20260924-101010-42.held.new",
                        ".agent/bash_history.archived-owes",
                        ".agent/alice/bash_history.archived-owes"):
                r = subprocess.run([git, "-C", str(repo), "check-ignore", "-q", rel])
                self.assertEqual(r.returncode, 0, f"{rel} is not ignored")


if __name__ == "__main__":
    unittest.main()
