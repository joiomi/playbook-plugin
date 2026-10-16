"""`/playbook:init` removes the BASH_ENV shell logger an earlier version installed — and only that (task 221).

The owner, 2026-10-16: "Init îl scoate" — init removes only what it once wrote: the 2 lines in the shell rc file,
BASH_ENV in ~/.claude/settings.json, the file ~/.claude/bash-log.sh, keeping a backup copy. Up to 1.6.x init deployed
that logger and every non-interactive bash started with its BASH_ENV sourced it; the shell log is written by the PostToolUse hook now.

Every run here uses a disposable HOME and project (never the real ones: memory `fixture-s15-writes-real-home`).

Held:
  * a fresh HOME gets no logger, no BASH_ENV, no rc line;
  * over the old wiring — bash host (`~/.profile` or `~/.bash_profile`) and zsh host (`~/.zshenv`) — exactly the file,
    the key and the two-line block (with the blank line init put before it) go, and every other byte stays;
  * left alone, and said so where it is init's own kind of file: a BASH_ENV naming another file, a block a person
    changed, a `bash-log.sh` that is not the playbook logger, a link named `bash-log.sh`;
  * an rc file that is a symbolic link is edited at its target, the link kept;
  * the user backup init names holds every changed file as it was;
  * a second init changes nothing more.

Run: python3 -m unittest tests.test_init_retires_shell_logger
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

INIT = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts" / "init"
HEADER = "# claude-playbook: project-scoped command logging (bash)\n"
ZHEADER = "# claude-playbook: project-scoped command logging (zsh)\n"
BLOCK = "\n# claude-playbook: command logging\nexport BASH_ENV=~/.claude/bash-log.sh\n"
ZBLOCK = "\n# claude-playbook: command logging\n[ -f ~/.claude/bash-log.zsh ] && source ~/.claude/bash-log.zsh\n"


class _Init(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.project = root / "proj"
        (self.home / ".claude").mkdir(parents=True)
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)

    def init(self, shell="/bin/bash"):
        env = {k: v for k, v in os.environ.items()
               if k not in ("BASH_ENV", "ENV", "PLAYBOOK_SESSION_ID", "PLAYBOOK_ROLE", "PLAYBOOK_EVAL_CONFIG",
                            "ZDOTDIR", "CLAUDE_CONFIG_DIR")}
        env.update(HOME=str(self.home), SHELL=shell)
        r = subprocess.run([bash_or_skip(), str(INIT), "proj"], cwd=self.project, env=env, capture_output=True,
                           text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
        return r

    def user_backup(self, out: str) -> Path:
        line = next(ln for ln in out.splitlines() if ln.strip().endswith("(user files)"))
        return Path(line.strip().rsplit("  (user files)", 1)[0].strip())

    def settings(self):
        return json.loads((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"))


class AFreshHome(_Init):
    def test_gets_no_logger_no_bash_env_no_rc_line(self):
        r = self.init()
        self.assertFalse((self.home / ".claude" / "bash-log.sh").exists())
        self.assertFalse((self.home / ".claude" / "bash-log.zsh").exists())
        s = self.home / ".claude" / "settings.json"
        self.assertTrue(not s.exists() or "BASH_ENV" not in s.read_text(encoding="utf-8"))
        for rc in (".bash_profile", ".profile", ".zshenv", ".bashrc"):
            self.assertFalse((self.home / rc).exists(), rc)
        self.assertNotIn("Removed", r.stdout)


class TheOldWiringGoes(_Init):
    def test_a_bash_host_loses_exactly_the_logger_its_key_and_its_lines(self):
        claude = self.home / ".claude"
        (claude / "bash-log.sh").write_text(HEADER + "trap '_cpb_log_cmd' DEBUG\n", encoding="utf-8")
        (claude / "settings.json").write_text(json.dumps(
            {"env": {"BASH_ENV": str(claude / "bash-log.sh"), "OTHER": "kept"}, "statusLine": {"x": 1}},
            indent=2) + "\n", encoding="utf-8")
        profile = self.home / ".profile"
        before = "# ~/.profile\nexport PATH=$HOME/bin:$PATH\n"
        after = "\n# my own lines after\nexport EDITOR=vim\n"
        profile.write_text(before + BLOCK + after, encoding="utf-8")
        originals = {p: p.read_bytes() for p in (claude / "bash-log.sh", claude / "settings.json", profile)}
        r = self.init()
        self.assertFalse((claude / "bash-log.sh").exists())
        self.assertEqual(self.settings(), {"env": {"OTHER": "kept"}, "statusLine": {"x": 1}})
        self.assertEqual(profile.read_text(encoding="utf-8"), before + after)
        self.assertIn("Removed", r.stdout)
        for item in ("~/.claude/bash-log.sh", "~/.claude/settings.json", "~/.profile"):
            self.assertIn(item, r.stdout)
        backup = self.user_backup(r.stdout)
        found = {p.name: p.read_bytes() for p in backup.rglob("*") if p.is_file() and p.name != "manifest.json"}
        for p, data in originals.items():
            self.assertIn(data, found.values(), f"the backup lacks {p.name} as it was")
        # a second run finds nothing more to do
        r2 = self.init()
        self.assertNotIn("Removed", r2.stdout)
        self.assertEqual(profile.read_text(encoding="utf-8"), before + after)

    def test_the_block_at_the_end_of_bash_profile_goes_with_its_blank_line(self):
        rc = self.home / ".bash_profile"
        rc.write_text("source ~/.bashrc\n" + BLOCK, encoding="utf-8")
        (self.home / ".claude" / "settings.json").write_text('{"env": {"BASH_ENV": "~/.claude/bash-log.sh"}}',
                                                             encoding="utf-8")
        self.init()
        self.assertEqual(rc.read_text(encoding="utf-8"), "source ~/.bashrc\n")
        self.assertNotIn("env", self.settings(), "an env left empty goes too")

    def test_a_zsh_host_loses_both_files_and_its_zshenv_line(self):
        claude = self.home / ".claude"
        (claude / "bash-log.sh").write_text(HEADER, encoding="utf-8")
        (claude / "bash-log.zsh").write_text(ZHEADER, encoding="utf-8")
        zshenv = self.home / ".zshenv"
        zshenv.write_text("export ZDOTDIR=$HOME/.zsh\n" + ZBLOCK, encoding="utf-8")
        self.init(shell="/usr/bin/zsh")
        self.assertFalse((claude / "bash-log.sh").exists())
        self.assertFalse((claude / "bash-log.zsh").exists())
        self.assertEqual(zshenv.read_text(encoding="utf-8"), "export ZDOTDIR=$HOME/.zsh\n")

    def test_an_rc_file_that_is_a_link_is_edited_at_its_target(self):
        dotfiles = self.home / "dotfiles"
        dotfiles.mkdir()
        target = dotfiles / "profile"
        target.write_text("export A=1\n" + BLOCK, encoding="utf-8")
        target.chmod(0o640)
        (self.home / ".profile").symlink_to(target)
        self.init()
        self.assertTrue((self.home / ".profile").is_symlink(), "the link is kept")
        self.assertEqual(target.read_text(encoding="utf-8"), "export A=1\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o640, "the file's mode is kept")


class WhatIsNotInitsIsLeft(_Init):
    def test_a_foreign_bash_env_a_changed_block_and_a_foreign_logger_are_left(self):
        claude = self.home / ".claude"
        (claude / "bash-log.sh").write_text("# my own logger, not playbook's\n", encoding="utf-8")
        settings = {"env": {"BASH_ENV": "/opt/my-env.sh"}}
        (claude / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        changed = "x\n# claude-playbook: command logging\nexport BASH_ENV=~/.my-own-env.sh\n"
        (self.home / ".profile").write_text(changed, encoding="utf-8")
        r = self.init()
        self.assertEqual((claude / "bash-log.sh").read_text(encoding="utf-8"), "# my own logger, not playbook's\n")
        self.assertEqual(self.settings(), settings)
        self.assertEqual((self.home / ".profile").read_text(encoding="utf-8"), changed)
        self.assertNotIn("Removed", r.stdout)
        self.assertIn("not the playbook logger", r.stdout)
        self.assertIn("a person changed", r.stdout)

    def test_a_link_named_like_the_logger_is_left(self):
        real = self.home / "elsewhere.sh"
        real.write_text(HEADER, encoding="utf-8")
        (self.home / ".claude" / "bash-log.sh").symlink_to(real)
        self.init()
        self.assertTrue((self.home / ".claude" / "bash-log.sh").is_symlink())
        self.assertEqual(real.read_text(encoding="utf-8"), HEADER)

    def test_a_mention_of_the_logger_outside_inits_block_is_left(self):
        text = "# I once ran: export BASH_ENV=~/.claude/bash-log.sh\nexport BASH_ENV=~/.claude/bash-log.sh\n"
        (self.home / ".profile").write_text(text, encoding="utf-8")
        self.init()
        self.assertEqual((self.home / ".profile").read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
