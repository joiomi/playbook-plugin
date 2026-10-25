#!/usr/bin/env python3
"""Task 313 (stabilization audit 4, finding G6): init keeps a .gitignore's own bytes.

init's merge step (`scripts/claude-md-merge.py`) adds its marker-guarded block to the project's `.gitignore`. It read
the file with `errors="replace"` and universal newlines, so a pattern holding a byte that is not UTF-8 came back with
U+FFFD in its place and CRLF became LF. The file that pattern ignored was no longer ignored, and `git add -A` would
commit it — a secret, say. The merge now reads the bytes, carries a byte that is not UTF-8 as itself
(`surrogateescape`, as CLAUDE.md already is) and keeps the file's line ending.

Run:
    python3 -m unittest tests.test_init_keeps_gitignore_bytes
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "playbook" / "scripts"
MERGE = SCRIPTS / "claude-md-merge.py"
TEMPLATE = SCRIPTS / "CLAUDE.md.template"
MARKER = b"# --- playbook runtime state (machine-local; managed by playbook init) ---"
SECRET = b"secret\xe9.env"                     # a Latin-1 byte: not UTF-8


class TheMerge(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.p = Path(tmp.name).resolve()
        home = self.p.parent / (self.p.name + "-home")
        home.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PLAYBOOK_"))},
                    "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
        for cmd in (["git", "init", "-q"], ["git", "config", "core.quotePath", "false"]):
            subprocess.run(cmd, cwd=self.p, check=True, env=self.env)
        self.gi = self.p / ".gitignore"

    def merge(self):
        r = subprocess.run([sys.executable, "-E", "-s", str(MERGE), str(TEMPLATE), str(self.p), "P"], cwd=self.p,
                           env=self.env, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.decode("utf-8", "replace")

    def ignored(self, name: bytes) -> bool:
        (self.p / os.fsdecode(name)).write_bytes(b"k\n")
        r = subprocess.run(["git", "check-ignore", "-q", "--", os.fsdecode(name)], cwd=self.p, env=self.env)
        return r.returncode == 0

    def test_a_byte_that_is_not_utf8_is_kept_and_still_ignores(self):
        before = b"build/\n" + SECRET + b"\n"
        self.gi.write_bytes(before)
        self.assertTrue(self.ignored(SECRET), "the control: git ignores the secret before init")
        self.assertIn(".gitignore:APPENDED", self.merge())
        after = self.gi.read_bytes()
        self.assertTrue(after.startswith(before), after[:80])
        self.assertNotIn("\ufffd".encode("utf-8"), after)
        self.assertIn(MARKER, after)
        self.assertTrue(self.ignored(SECRET), "init stopped ignoring the secret")

    def test_a_crlf_file_stays_crlf(self):
        before = b"build/\r\n*.pyc\r\n"          # ASCII only: the line ending alone
        self.gi.write_bytes(before)
        self.merge()
        after = self.gi.read_bytes()
        self.assertTrue(after.startswith(before), after[:80])
        self.assertIn(MARKER + b"\r\n", after)
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""), "a bare LF in a CRLF file")
        self.assertTrue(self.ignored(b"x.pyc"))

    def test_a_pattern_ending_in_a_carriage_return_and_the_blank_lines_after_it_are_kept(self):
        # impl round 1 (codex, reproduced by reading git's dir.c; opus, grok): the append did `rstrip("\r\n")`, which
        # took the CR this pattern means (git strips only the CR before LF) and the blank lines after it
        before = b"build/\n" + SECRET + b"\r\r\n\n\n"
        self.gi.write_bytes(before)
        target = SECRET + b"\r"                      # a name ending in CR: ignored by that pattern only
        self.assertTrue(self.ignored(target), "the control: git ignores it before init")
        self.merge()
        after = self.gi.read_bytes()
        self.assertTrue(after.startswith(before), after[:80])
        self.assertTrue(self.ignored(target), "init stopped ignoring the file the pattern names")

    def test_the_upgrade_path_keeps_the_bytes_too(self):
        # impl round 1 (opus): with the marker already there, missing entries go under it — the same decode and encode
        before = b"build/\r\n" + SECRET + b"\r\n\r\n" + MARKER + b"\r\n.agent/sessions/\r\n"
        self.gi.write_bytes(before)
        self.merge()
        after = self.gi.read_bytes()
        self.assertTrue(after.startswith(b"build/\r\n" + SECRET + b"\r\n\r\n" + MARKER + b"\r\n"), after[:120])
        self.assertIn(b".agent/sessions/\r\n", after)
        self.assertGreater(after.count(b"\r\n"), before.count(b"\r\n"), "no entry was added")
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
        self.assertTrue(self.ignored(SECRET))

    def test_a_marker_on_the_last_line_without_a_newline_keeps_its_line(self):
        # impl round 1 (opus, sonnet) [PRE-EXISTING]: the first missing entry was glued onto the marker
        before = b"build/\n\n" + MARKER
        self.gi.write_bytes(before)
        self.merge()
        lines = self.gi.read_bytes().split(b"\n")
        self.assertIn(MARKER, lines, "the marker is no longer a line of its own")
        self.assertIn(b".agent/sessions/", lines)

    def test_control_a_plain_file_gets_the_block_as_before(self):
        before = b"build/\n*.pyc\n"
        self.gi.write_bytes(before)
        self.assertIn(".gitignore:APPENDED", self.merge())
        after = self.gi.read_bytes()
        self.assertTrue(after.startswith(before + b"\n" + MARKER + b"\n"), after[:120])
        self.assertNotIn(b"\r", after)
        self.assertIn(".gitignore:UNCHANGED", self.merge(), "a second init changed the merged file")


if __name__ == "__main__":
    unittest.main()
