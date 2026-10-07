"""Task 129 (task 090 R18; retro 107): `tasks doctor` names the shell logger copy.

`/playbook:init` deploys `~/.claude/bash-log.sh` (BASH_ENV) — a copy of the plugin's
`scripts/bash-log.sh` that no plugin update refreshes. It went stale once as a "fifth copy"
nobody listed (090). Doctor already lists the hook, installed, launcher and doctor copies;
now it also compares the deployed logger (and `bash-log.zsh` when deployed) with this copy's
`scripts/` file: current, stale (both hashes + how to refresh), or not deployed.

Run: python3 -m unittest tests.test_doctor_shell_logger
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))

from tasks import plugin_copies  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class ShellLoggerLines(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="pb-129-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir()

    def lines(self):
        return plugin_copies.shell_logger_lines(self.home, PLUGIN)

    def test_a_current_logger_passes_with_its_hash(self):
        shutil.copy(PLUGIN / "scripts" / "bash-log.sh", self.home / ".claude" / "bash-log.sh")
        (tag, text), = self.lines()
        self.assertEqual(tag, "PASS")
        self.assertIn("bash-log.sh", text)
        self.assertIn(_sha(PLUGIN / "scripts" / "bash-log.sh")[:12], text)

    def test_a_stale_logger_warns_with_both_hashes_and_the_remedy(self):
        stale = self.home / ".claude" / "bash-log.sh"
        stale.write_text("# an old logger\n", encoding="utf-8")
        (tag, text), = self.lines()
        self.assertEqual(tag, "WARN")
        self.assertIn(_sha(stale)[:12], text)
        self.assertIn(_sha(PLUGIN / "scripts" / "bash-log.sh")[:12], text)
        self.assertIn("/playbook:init", text)

    def test_not_deployed_is_said_not_failed(self):
        (tag, text), = self.lines()
        self.assertEqual(tag, "INFO")
        self.assertIn("not deployed", text)

    def test_the_zsh_logger_is_listed_only_when_deployed(self):
        shutil.copy(PLUGIN / "scripts" / "bash-log.sh", self.home / ".claude" / "bash-log.sh")
        (self.home / ".claude" / "bash-log.zsh").write_text("# old zsh logger\n", encoding="utf-8")
        tags = {text.split(" — ")[0]: tag for tag, text in self.lines()}
        self.assertEqual(tags, {"shell logger: ~/.claude/bash-log.sh": "PASS",
                                "shell logger: ~/.claude/bash-log.zsh": "WARN"})


class DoctorPrintsIt(unittest.TestCase):
    def test_doctor_shows_the_shell_logger_line(self):
        from tasks import diagnostics
        home = Path(tempfile.mkdtemp(prefix="pb-129h-"))
        self.addCleanup(shutil.rmtree, home, True)
        (home / ".claude").mkdir()
        (home / ".claude" / "bash-log.sh").write_text("# stale\n", encoding="utf-8")
        proj = Path(tempfile.mkdtemp(prefix="pb-129p-"))
        self.addCleanup(shutil.rmtree, proj, True)
        (proj / ".agent" / "tasks").mkdir(parents=True)
        out = io.StringIO()
        prev = os.getcwd()
        os.chdir(proj)
        self.addCleanup(os.chdir, prev)
        with mock.patch.dict(os.environ, {"HOME": str(home)}), mock.patch.object(Path, "home", lambda: home), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                diagnostics.cmd_doctor([])
            except SystemExit:
                pass
        self.assertIn("shell logger: ~/.claude/bash-log.sh — stale", out.getvalue())


if __name__ == "__main__":
    unittest.main()
