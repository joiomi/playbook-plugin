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
                      if p.name.startswith("bash_history.archived-") and not p.name.endswith(".new"))

    def _leftovers(self):
        return sorted(p.name for p in self.hist.parent.iterdir() if p.name.endswith(".new"))

    def _live_activations(self):
        text = self.hist.read_text(encoding="utf-8", errors="replace") if self.hist.exists() else ""
        return [ln.split(self.ACTIVATION, 1)[1] for ln in text.splitlines() if self.ACTIVATION in ln]

    def _shim(self, tool, body):
        """`tool` first on PATH: `body` (bash) runs with REAL=<the real binary>; it decides."""
        import shutil
        real = shutil.which(tool)
        self.assertTrue(real, tool)
        (self.bindir / tool).write_text(f'#!/bin/bash\nREAL="{real}"\n{body}\nexec "$REAL" "$@"\n', encoding="utf-8")
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
        self.assertEqual(self._leftovers(), [])

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
        self.assertEqual(self._leftovers(), [])

    # -- (2) a rotation that dies ---------------------------------------------------------
    def test_a_rotation_that_dies_at_any_step_leaves_the_activations_in_the_live_file(self):
        dies = 'case "$*" in *{mark}*) exit 137 ;; esac'       # gone, as a killed process is, having done nothing
        for tool, mark in (("grep", "tasks"), ("ln", "bash_history.archived-"), ("mv", "bash_history.archived-")):
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
                self.assertEqual(self._leftovers(), [])

    def test_a_rotator_killed_outright_is_cleaned_up_after_by_the_next(self):
        # SIGKILL at the rename: nothing of the rotation's own gets to tidy up
        self._big_history()
        self._shim("mv", 'case "$*" in *bash_history.archived-*) kill -9 $PPID; exit 137 ;; esac')
        r = self._bash("echo while-it-was-killed >/dev/null; echo still-alive", shims=True)
        self.assertIn("still-alive", r.stdout)
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertEqual(len(self._leftovers()), 1, "the case under test: a prepared file was left behind")
        self._bash("echo after-it >/dev/null")
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertLess(self.hist.stat().st_size, 1024 * 1024)
        self.assertEqual(self._leftovers(), [])
        for arch in self._archives():                           # every archive name holds the old history
            self.assertGreater(arch.stat().st_size, 50 * 1024 * 1024)

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

    # -- (3) the bound ------------------------------------------------------------------------
    def test_only_the_newest_5000_activations_are_carried(self):
        self._big_history(activations=range(1, 6001))
        self._bash("echo rotate >/dev/null")
        carried = self._live_activations()
        self.assertEqual(len(carried), 5000)
        self.assertEqual((carried[0], carried[-1]), ("1001", "6000"))

    # -- where the new sequence cannot run ------------------------------------------------------
    def test_without_flock_on_the_host_it_still_rotates_the_old_way(self):
        wrapper = Path(self._tmp.name) / "no-flock.sh"
        wrapper.write_text('command() { if [ "$1" = -v ] && [ "$2" = flock ]; then return 1; fi; '
                           f'builtin command "$@"; }}\n. "{BL}"\n', encoding="utf-8")
        self._big_history()
        r = self._bash("set -e; echo rotate >/dev/null; echo still-alive", bash_env=wrapper)
        self.assertIn("still-alive", r.stdout)
        self.assertEqual(len(self._archives()), 1)
        self.assertEqual(self._live_activations(), ["7", "8"])
        self.assertLess(self.hist.stat().st_size, 1024 * 1024)

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
        self.assertEqual(self._leftovers(), [])


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
                        ".agent/alice/bash_history.archived-20260924-101010-42.new"):
                r = subprocess.run([git, "-C", str(repo), "check-ignore", "-q", rel])
                self.assertEqual(r.returncode, 0, f"{rel} is not ignored")


if __name__ == "__main__":
    unittest.main()
