#!/usr/bin/env python3
"""PB-INIT-DENY-LIST (PLAN S8b, task 100): scripts/init merges TodoWrite, Task and
EnterPlanMode into the project's `.claude/settings.json` permissions.deny list —
creating the file when absent, preserving every existing entry and key, skipping
(SKIPPED, byte-identical) when all three are already present, and refusing rather
than clobbering a malformed settings file.

Hermetic: HOME is a temp dir (init also deploys ~/.claude/bash-log.*), so the
developer's real settings are never touched.

Run: python3 tests/test_init_deny_list.py
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT = REPO_ROOT / "plugins" / "playbook" / "scripts" / "init"
REQUIRED = ["TodoWrite", "Task", "EnterPlanMode"]


class InitDenyList(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.settings = self.project / ".claude" / "settings.json"

    def _init(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)  # never touch the real ~/.claude
        return subprocess.run([bash_or_skip(), str(INIT), "proj"], cwd=self.project,
                              env=env, text=True, capture_output=True, timeout=300)

    def _deny(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))["permissions"]["deny"]

    def test_fresh_project_gets_the_three_denies(self):
        r = self._init()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.settings.is_file(), "init did not create .claude/settings.json")
        self.assertEqual(self._deny(), REQUIRED)
        self.assertIn("settings.json deny: TodoWrite,Task,EnterPlanMode", r.stdout)

    def test_existing_entries_and_keys_are_preserved_and_only_missing_ones_added(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({
            "keepme": {"nested": True},
            "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Custom", "Task"]},
        }, indent=2) + "\n", encoding="utf-8")
        r = self._init()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(data["keepme"], {"nested": True}, "an unrelated top-level key was lost")
        self.assertEqual(data["permissions"]["allow"], ["Bash(ls:*)"], "permissions.allow was lost")
        self.assertEqual(data["permissions"]["deny"], ["Custom", "Task", "TodoWrite", "EnterPlanMode"],
                         "existing denies must stay first and in order; only the missing two are appended")
        self.assertIn("settings.json deny: TodoWrite,EnterPlanMode", r.stdout,
                      "the summary must name only what it added")

    def test_second_run_is_skipped_and_byte_identical(self):
        self.assertEqual(self._init().returncode, 0)
        before = self.settings.read_bytes()
        r = self._init()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.settings.read_bytes(), before, "a re-run rewrote settings.json")
        self.assertIn("settings.json (already has deny list)", r.stdout)
        self.assertNotIn("settings.json deny:", r.stdout)

    def test_malformed_json_is_refused_not_clobbered(self):
        self.settings.parent.mkdir(parents=True)
        broken = b'{"permissions": {"deny": ["Custom"]}'   # unterminated
        self.settings.write_bytes(broken)
        r = self._init()
        self.assertNotEqual(r.returncode, 0, "init must report a FAILED step on a malformed settings file")
        self.assertIn("malformed JSON", r.stdout + r.stderr)
        self.assertEqual(self.settings.read_bytes(), broken,
                         "a malformed settings.json was rewritten — the owner's file is gone")

    def test_non_object_root_is_refused_not_clobbered(self):
        self.settings.parent.mkdir(parents=True)
        odd = b'["not", "an", "object"]\n'
        self.settings.write_bytes(odd)
        r = self._init()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not an object", r.stdout + r.stderr)
        self.assertEqual(self.settings.read_bytes(), odd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
