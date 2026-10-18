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
    anything (the file comes last in its order).

Every run uses a disposable HOME (empty git configs) and project — never the real ones (memory
`fixture-s15-writes-real-home`).

Run: python3 -m unittest tests.test_init_fails_and_restores
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts"
INIT = SCRIPTS / "init"
TXN = SCRIPTS / "init_txn.py"
HEADER = "# claude-playbook: project-scoped command logging (bash)\n"
BLOCK = "\n# claude-playbook: command logging\nexport BASH_ENV=~/.claude/bash-log.sh\n"
OLD_SETTINGS = '{\n  "env": {"KEEP": "1", "BASH_ENV": "~/.claude/bash-log.sh"}\n}\n'


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
                                      "  *'claude-playbook: command logging'*) exit 1 ;;\nesac; done\n"
                                      f"exec '{real}' \"$@\"\n", encoding="utf-8")
        (shim / "python3").chmod(0o755)
        before = self.snapshot()
        r = self.init(PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
        self.assertIn("FAILED:", r.stdout, r.stdout[-2500:])
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
