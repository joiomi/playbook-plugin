"""`/playbook:init` stops and restores everything when it cannot finish removing the old shell logger (task 243).

Task 221 made init remove the BASH_ENV shell logger an earlier version installed, and wrote the removal as "never
fails": a file it could not change was listed under "Skipped" and init returned 0, so with `~/.claude` read-only the
rc file lost its two lines while `~/.claude/settings.json` kept BASH_ENV — the logger half removed, the project's own
changes kept, against PB-INSTALL-ROLLBACK's promise. The owner, 2026-10-18: "Oprește și reface tot (Recomandat)".

The rule held here: init stops and restores everything when it cannot finish removing the old logger, or cannot tell
whether it is there; what is not init's stays as it is, said, and does not fail.

Held:
  * T1 `~/.claude` read-only over the old logger: init fails, says it restores, both trees byte-identical;
  * T2 `~/.claude` not searchable: the pre-init snapshot refuses before any change, naming the path;
  * T3 a write that fails AFTER others succeeded (the HOME read-only, `~/.claude` writable): the logger file and its
    BASH_ENV come back too;
  * T4 `~/.claude` read-only with nothing to remove: init succeeds (the new-project case);
  * T5 `~/.claude/settings.json` not valid JSON: a failure when its text names `bash-log.sh`, otherwise left and said;
  * T6 an rc file that is not UTF-8: the block goes, every other byte stays;
  * T7 a crash of the removal step: a failure, restored;
  * T8 `init_txn.py begin` alone refuses a listed file it cannot look at, creating nothing;
  * T9 with the transaction off, the removal step itself refuses a file it cannot look at, before it changes
    anything (the file comes last in its order);
  * T10 two inits on one HOME at once: the second waits for the first, and the logger stays removed;
  * T11 a file already gone when its removal comes is done, not a failure;
  * T12 an rc file that is a link loop is absent to both looks, no failure;
  * T13 a settings.json with a UTF-8 BOM loses the key and keeps the BOM;
  * T14 a wait for the HOME lock ended by a signal stops init before any change.

Every run uses a disposable HOME (empty git configs) and project — never the real ones (memory
`fixture-s15-writes-real-home`).

Run: python3 -m unittest tests.test_init_fails_and_restores
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts"
INIT = SCRIPTS / "init"
TXN = SCRIPTS / "init_txn.py"
HEADER = "# claude-playbook: project-scoped command logging (bash)\n"
BLOCK = "\n# claude-playbook: command logging\nexport BASH_ENV=~/.claude/bash-log.sh\n"
OLD_SETTINGS = '{\n  "env": {"KEEP": "1", "BASH_ENV": "~/.claude/bash-log.sh"}\n}\n'

# Where the removal step goes from looking to changing — the seam T10 and T11 inject at (a python3 shim rewrites
# that one `python3 -I -c` call; every other call runs untouched). A step without it fails the shim loudly (rc 99).
SEAM = "# 2. make the changes, stopping at the first that fails"
SHIM = """#!{py}
import os, sys
code_at = sys.argv.index("-c") + 1 if "-c" in sys.argv else None
if code_at is not None and "claude-playbook: command logging" in sys.argv[code_at]:
    code = sys.argv[code_at]
    if {seam!r} not in code:
        sys.stderr.write("shim: the removal step has no seam\\n")
        sys.exit(99)
    sys.argv[code_at] = code.replace({seam!r}, open({inject!r}).read() + {seam!r}, 1)
os.execv({py!r}, [{py!r}] + sys.argv[1:])
"""


def _tree(root: Path) -> "dict[str, tuple[str, int]]":
    """Every file under `root` → (sha256, mode), the git dir and init's kept backups aside."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [d for d in dirnames if os.path.normpath(os.path.join(rel_dir, d)) not in
                       (".git", ".playbook-init-backups", os.path.join(".agent", "backups"))]
        for name in filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                out[os.path.relpath(p, root)] = ("link:" + os.readlink(p), 0)
                continue
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = (hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mode & 0o7777)
    return out


def _children(pid: int, name: str) -> "list[int]":
    """The pids whose parent is `pid` and whose command name is `name` (read from /proc — no clock involved)."""
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat", encoding="utf-8", errors="replace") as fh:
                st = fh.read()
        except OSError:
            continue
        rp = st.rfind(")")
        if st[st.find("(") + 1:rp] == name and int(st[rp + 2:].split()[1]) == pid:
            out.append(int(d))
    return out


def _until(cond, limit=90.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < limit:
        time.sleep(0.05)
    return cond()


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._writable_then_cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "proj"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".config" / "git").mkdir(parents=True)
        (self.home / ".gitconfig").write_text("", encoding="utf-8")
        (self.home / ".config" / "git" / "config").write_text("", encoding="utf-8")
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, env=self.env())

    def _writable_then_cleanup(self):
        for dirpath, dirnames, _ in os.walk(self.root):
            for d in [dirpath] + [os.path.join(dirpath, n) for n in dirnames]:
                try:
                    os.chmod(d, 0o700)
                except OSError:
                    pass
        self._tmp.cleanup()

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("BASH_ENV", "ENV", "PLAYBOOK_SESSION_ID", "PLAYBOOK_ROLE", "PLAYBOOK_EVAL_CONFIG",
                            "ZDOTDIR", "CLAUDE_CONFIG_DIR", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")}
        env.update(HOME=str(self.home), SHELL="/bin/bash", **extra)
        return env

    def old_logger(self):
        """The wiring an earlier version's init left: the file, its BASH_ENV, its block in ~/.profile."""
        (self.home / ".claude" / "bash-log.sh").write_text(HEADER + "echo log\n", encoding="utf-8")
        (self.home / ".claude" / "settings.json").write_text(OLD_SETTINGS, encoding="utf-8")
        (self.home / ".profile").write_text("export A=1\n" + BLOCK, encoding="utf-8")

    def lock(self, directory: Path, mode: int):
        """chmod `directory` and check the OS enforces it (root does not: skip, never assert the impossible)."""
        os.chmod(directory, mode)
        try:
            (directory / ".pb_probe").write_text("x", encoding="utf-8")
        except OSError:
            return
        (directory / ".pb_probe").unlink()
        os.chmod(directory, 0o700)
        self.skipTest(f"directory permissions not enforced for this user ({directory})")

    def init(self, **env_extra):
        return subprocess.run([bash_or_skip(), str(INIT), "proj"], cwd=self.project, env=self.env(**env_extra),
                              capture_output=True, text=True, timeout=180)

    def shim(self, name: str, inject: str) -> Path:
        """A PATH dir whose python3 inserts `inject` (python, at top level of the removal step) before SEAM."""
        d = self.root / name
        d.mkdir()
        (d / "inject.py").write_text(inject, encoding="utf-8")
        (d / "python3").write_text(SHIM.format(py=sys.executable, seam=SEAM, inject=str(d / "inject.py")),
                                   encoding="utf-8")
        (d / "python3").chmod(0o755)
        return d

    def start(self, project: Path, **env_extra):
        return subprocess.Popen([bash_or_skip(), str(INIT), project.name], cwd=project, env=self.env(**env_extra),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def assert_logger_gone(self):
        self.assertFalse((self.home / ".claude" / "bash-log.sh").exists(), "the logger file stays removed")
        settings = (self.home / ".claude" / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("bash-log.sh", settings, "its BASH_ENV stays removed")
        self.assertEqual((self.home / ".profile").read_text(encoding="utf-8"), "export A=1\n", "its block stays removed")

    def snapshot(self):
        return _tree(self.home), _tree(self.project)

    def assert_failed_and_restored(self, r, before, says):
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out[-2500:])
        self.assertIn(says, out, out[-2500:])
        for d in (self.home / ".claude", self.home):
            os.chmod(d, 0o700)
        home, project = self.snapshot()
        self.assertEqual(home, before[0], "the HOME is byte-identical to right before")
        self.assertEqual(project, before[1], "the project is byte-identical to right before")


class WhenInitCannotFinishItRestores(_Case):
    def test_t1_a_read_only_claude_dir_over_the_old_logger_fails_and_restores(self):
        self.old_logger()
        # impl round 2 (codex): init deletes a stale `.agent/monitor/sandbox.sb`; the rollback must bring it back
        (self.project / ".agent" / "monitor").mkdir(parents=True)
        (self.project / ".agent" / "monitor" / "sandbox.sb").write_text("(version 1)\n", encoding="utf-8")
        before = self.snapshot()
        self.lock(self.home / ".claude", stat.S_IRUSR | stat.S_IXUSR)
        r = self.init()
        self.assertIn("FAILED:", r.stdout, r.stdout[-2500:])
        self.assertIn("~/.claude/bash-log.sh", r.stdout.split("FAILED:", 1)[-1])
        self.assertNotIn("~/.profile (the old shell logger lines)", r.stdout, "it stops at the first failed change")
        self.assert_failed_and_restored(r, before, "restoring the pre-init state")

    def test_t2_a_claude_dir_that_cannot_be_searched_stops_init_before_any_change(self):
        self.old_logger()
        before = self.snapshot()
        self.lock(self.home / ".claude", stat.S_IRUSR | stat.S_IWUSR)
        r = self.init()
        self.assert_failed_and_restored(r, before, "could not take the pre-init snapshot")
        self.assertIn(str(self.home / ".claude"), r.stderr)
        self.assertFalse((self.project / "CLAUDE.md").exists(), "nothing was written in the project")

    def test_t3_a_later_write_failing_brings_back_the_file_and_the_key_already_removed(self):
        self.old_logger()
        (self.home / ".playbook-init-backups").mkdir()
        before = self.snapshot()
        self.lock(self.home, stat.S_IRUSR | stat.S_IXUSR)        # ~/.profile cannot be replaced; ~/.claude still can
        r = self.init()
        self.assertIn("~/.profile", r.stdout.split("FAILED:", 1)[-1], r.stdout[-2500:])
        self.assertNotIn("Removed (installed by an earlier version", r.stdout,
                         "what the restore puts back is not listed as removed (impl round 2, opus)")
        self.assert_failed_and_restored(r, before, "restoring the pre-init state")
        self.assertEqual((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"), OLD_SETTINGS)
        self.assertTrue((self.home / ".claude" / "bash-log.sh").is_file())

    def test_t5_unparsable_settings_naming_the_logger_fail_and_restore(self):
        self.old_logger()
        (self.home / ".claude" / "settings.json").write_text('{"env": {"BASH_ENV": "~/.claude/bash-log.sh"},}\n',
                                                             encoding="utf-8")
        before = self.snapshot()
        r = self.init()
        self.assertIn("~/.claude/settings.json", r.stdout.split("FAILED:", 1)[-1], r.stdout[-2500:])
        self.assert_failed_and_restored(r, before, "restoring the pre-init state")

    def test_t7_a_crash_of_the_removal_step_fails_and_restores(self):
        self.old_logger()
        shim = self.root / "shim"
        shim.mkdir()
        real = subprocess.run(["bash", "-c", "command -v python3"], capture_output=True, text=True).stdout.strip()
        (shim / "python3").write_text("#!/bin/bash\nfor a in \"$@\"; do case \"$a\" in\n"
                                      "  *'claude-playbook: command logging'*) echo 'Traceback (most recent call last):';"
                                      " echo 'BoomError: the step died here'; exit 1 ;;\nesac; done\n"
                                      f"exec '{real}' \"$@\"\n", encoding="utf-8")
        (shim / "python3").chmod(0o755)
        before = self.snapshot()
        r = self.init(PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
        self.assertIn("FAILED:", r.stdout, r.stdout[-2500:])
        self.assertIn("stopped (exit 1: BoomError: the step died here)", r.stdout, "the crash's last line is kept")
        self.assert_failed_and_restored(r, before, "restoring the pre-init state")


    def test_t9_with_the_transaction_off_the_removal_step_still_refuses_what_it_cannot_see(self):
        # The snapshot refuses such a file first (T2, T8); with it off, the removal step's own look must hold. The
        # file it cannot see comes LAST in its order (~/.profile, through a link into a directory it may not search),
        # after two it could change — so this also shows it looks at everything before it changes anything.
        self.old_logger()
        locked = self.home / "locked"
        locked.mkdir()
        (locked / "profile").write_text("export A=1\n" + BLOCK, encoding="utf-8")
        (self.home / ".profile").unlink()
        (self.home / ".profile").symlink_to(locked / "profile")
        self.lock(locked, stat.S_IRUSR | stat.S_IWUSR)
        r = self.init(PB_INIT_NO_TXN="1")
        self.assertNotEqual(r.returncode, 0, (r.stdout + r.stderr)[-2500:])
        self.assertIn("~/.profile: could not be looked at", r.stdout.split("FAILED:", 1)[-1], r.stdout[-2500:])
        self.assertEqual((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"), OLD_SETTINGS)
        self.assertTrue((self.home / ".claude" / "bash-log.sh").is_file(), "nothing changed before the look failed")


class TwoInitsOnOneHome(_Case):
    def test_t10_a_second_init_waits_for_the_first_and_nothing_comes_back(self):
        # Impl round 1 (codex, Important): B looked and planned the removal; A then ran whole and removed the logger;
        # B's unlink found it gone, failed, and its rollback restored its own snapshot — the logger back. One lock on
        # the HOME makes A wait for B.
        self.old_logger()
        other = self.root / "projb"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True, env=self.env())
        looked, go = self.root / "b-looked", self.root / "b-go"
        shim = self.shim("shim-b", f"open({str(looked)!r}, 'w').close()\nimport time as _t\n_t0 = _t.time()\n"
                                   f"while not os.path.exists({str(go)!r}) and _t.time() - _t0 < 90:\n"
                                   "    _t.sleep(0.05)\n")
        b = self.start(other, PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
        try:
            t0 = time.time()
            while not looked.exists() and b.poll() is None and time.time() - t0 < 90:
                time.sleep(0.05)
            self.assertTrue(looked.exists(), "B reached the removal step's seam")
            # impl round 2 (opus, sonnet): no clock in the verdict — A either waits on the HOME lock (a `flock` child)
            # or, with no lock, reaches its own seam (its marker)
            a_looked = self.root / "a-looked"
            shim_a = self.shim("shim-a", f"open({str(a_looked)!r}, 'w').close()\n")
            a = self.start(self.project, PATH=f"{shim_a}{os.pathsep}{os.environ.get('PATH', '')}")
            _until(lambda: a_looked.exists() or _children(a.pid, "flock") or a.poll() is not None)
            a_waits = bool(_children(a.pid, "flock")) and not a_looked.exists()
        finally:
            go.write_text("", encoding="utf-8")
        b_out, b_err = b.communicate(timeout=240)
        a_out, a_err = a.communicate(timeout=240)
        self.assertEqual(b.returncode, 0, (b_out + b_err)[-2500:])
        self.assertEqual(a.returncode, 0, (a_out + a_err)[-2500:])
        self.assertTrue(a_waits, "A waited on the HOME lock while B held it, before its own removal step")
        self.assert_logger_gone()

    def test_t11_a_file_already_gone_when_its_removal_comes_is_no_failure(self):
        self.old_logger()
        shim = self.shim("shim-gone", "for _op in plan:\n    if _op[0] == 'remove':\n        os.remove(_op[1])\n")
        r = self.init(PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2500:])
        self.assertNotIn("FAILED", r.stdout)
        self.assertIn("~/.claude/bash-log.sh (the old shell logger): another process removed it first", r.stdout)
        self.assert_logger_gone()

    def test_t14_a_wait_for_the_home_lock_ended_by_a_signal_stops_init_before_any_change(self):
        # impl round 2 (agy): a `flock` ended by a signal read as "this flock cannot wait", and init went on unlocked
        self.old_logger()
        before = self.snapshot()
        holder = subprocess.Popen(["flock", str(self.home), "sleep", "120"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            self.assertTrue(_until(lambda: bool(_children(holder.pid, "sleep"))), "the test holds the HOME lock")
            a = self.start(self.project)
            self.assertTrue(_until(lambda: bool(_children(a.pid, "flock")) or a.poll() is not None))
            for pid in _children(a.pid, "flock"):
                os.kill(pid, 15)
            out, err = a.communicate(timeout=120)
        finally:
            os.killpg(holder.pid, 9)       # flock and its sleep, which holds the lock too
            holder.wait()
        self.assertNotEqual(a.returncode, 0, (out + err)[-2500:])
        self.assertIn("the wait for the HOME lock was interrupted", err)
        self.assertEqual(self.snapshot(), before, "nothing was changed")


class WhatNeedsNoWriteStillSucceeds(_Case):
    def test_t4_a_read_only_claude_dir_with_nothing_to_remove_is_no_failure(self):
        (self.home / ".claude" / "settings.json").write_text('{\n  "env": {"KEEP": "1"}\n}\n', encoding="utf-8")
        (self.home / ".profile").write_text("export A=1\n", encoding="utf-8")
        self.lock(self.home / ".claude", stat.S_IRUSR | stat.S_IXUSR)
        r = self.init()
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2500:])
        self.assertNotIn("FAILED", r.stdout)

    def test_t5_unparsable_settings_not_naming_the_logger_are_left_and_said(self):
        text = '{"env": {"KEEP": "1"},}\n'
        (self.home / ".claude" / "settings.json").write_text(text, encoding="utf-8")
        r = self.init()
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2500:])
        self.assertEqual((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"), text)
        self.assertIn("~/.claude/settings.json (not valid JSON", r.stdout)

    def test_t12_an_rc_file_that_is_a_link_loop_is_no_failure(self):
        # Impl round 1 (opus, sonnet): the snapshot read a link loop as absent, the removal step's look as FAILED.
        (self.home / ".zshenv").symlink_to(self.home / ".zshenv")
        r = self.init()
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2500:])
        self.assertNotIn("FAILED", r.stdout)
        self.assertTrue((self.home / ".zshenv").is_symlink(), "the loop is left as it is")

    def test_t13_a_settings_file_with_a_bom_loses_the_key_and_keeps_the_bom(self):
        # impl round 2 (opus): json.loads refused the BOM, and the text names the logger — init failed
        (self.home / ".claude" / "settings.json").write_bytes(b"\xef\xbb\xbf" + OLD_SETTINGS.encode())
        r = self.init()
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2500:])
        data = (self.home / ".claude" / "settings.json").read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"), "the BOM is kept")
        self.assertNotIn(b"bash-log.sh", data)
        self.assertIn(b'"KEEP": "1"', data)

    def test_t6_an_rc_file_that_is_not_utf8_loses_only_the_block(self):
        head, tail = b"export A='\xe9t\xe9'\n", b"\nalias x='\xff'\n"
        (self.home / ".profile").write_bytes(head + BLOCK.encode() + tail)
        r = self.init()
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2500:])
        self.assertEqual((self.home / ".profile").read_bytes(), head + tail)
        self.assertIn("~/.profile (the old shell logger lines)", r.stdout)


class TheSnapshotRefusesWhatItCannotSee(_Case):
    def test_t8_begin_refuses_a_listed_file_it_cannot_look_at_and_creates_nothing(self):
        (self.home / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
        self.lock(self.home / ".claude", stat.S_IRUSR | stat.S_IWUSR)
        env = self.env(PB_TXN_PROJECT_ROOT=str(self.project), PB_TXN_HOME=str(self.home), PB_TXN_STAMP="t8",
                       PB_TXN_FILES=f"{self.project}/CLAUDE.md\n{self.home}/.claude/settings.json",
                       PB_TXN_DIRS=f"{self.project}/.agent\n{self.home}/.claude")
        r = subprocess.run(["python3", "-E", "-s", str(TXN), "begin"], env=env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ERROR:snapshot failed", r.stderr)
        self.assertIn(str(self.home / ".claude" / "settings.json"), r.stderr)
        self.assertFalse((self.project / ".agent").exists(), "no project backup root")
        self.assertFalse((self.home / ".playbook-init-backups").exists(), "no user backup root")


if __name__ == "__main__":
    unittest.main()
