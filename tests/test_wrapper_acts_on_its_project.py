"""A project's `.claude/bin/tasks` acts on its own project, from any working directory (task 310, audit 4's G2).

The wrapper found the plugin's CLI and ran it in the caller's directory, and the CLI takes its project from the
working directory (`shared.find_project_root`: the nearest `.agent/tasks`, else the directory itself). Run from the
project's PARENT folder — twice in his blind session, 2026-10-25 — `proj/.claude/bin/tasks new` made the task in the
parent's `.agent/tasks`, and `tasks work done` closed it there with no verify contract ("NOTHING verified"), the
project's own `verify` never run (reproduced in task 306, evidence/m2-mine/g2_parent_cwd.sh). The tests make the
plugin's managed wrapper with `create_wrapper`, point a temporary HOME's install manifest at this tree, and run it.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
LIB = PLUGIN / "scripts" / "gate-echo-lib.sh"


class TheWrapperRunsInItsProject(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.home, self.root = base / "home", base / "root"
        self.proj = self.root / "proj"
        (self.home / ".claude" / "plugins").mkdir(parents=True)
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"playbook@mk": [{"scope": "user", "installPath": str(PLUGIN),
                                                         "version": "9.9.9"}]}}), encoding="utf-8")
        (self.proj / ".agent" / "tasks").mkdir(parents=True)
        (self.proj / "src").mkdir()
        (self.proj / ".agent" / "config.json").write_text(json.dumps({"verify": "false"}), encoding="utf-8")
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; create_wrapper "$2" tasks', "-", str(LIB),
                            str(self.proj)], env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.wrapper = self.proj / ".claude" / "bin" / "tasks"
        self.assertTrue(self.wrapper.is_file())

    def env(self):
        return {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb",
                "PLAYBOOK_SESSION_ID": "pid-310", "PYTHONDONTWRITEBYTECODE": "1"}

    def tasks(self, *args, cwd):
        return subprocess.run([str(self.wrapper), *args], cwd=str(cwd), env=self.env(), capture_output=True,
                              text=True, timeout=300)

    def test_new_from_the_parent_folder_makes_the_task_in_the_project(self):
        r = self.tasks("new", "quick", "fix", "fix f", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in (self.proj / ".agent" / "tasks").iterdir()], ["001-fix"])
        self.assertFalse((self.root / ".agent").exists())

    def test_a_close_from_the_parent_folder_runs_the_projects_verify(self):
        for args in (("new", "quick", "fix", "fix f"), ("work", "1")):
            r = self.tasks(*args, cwd=self.root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        found = list((self.proj / ".agent" / "tasks").glob("001-*/task.md"))
        self.assertEqual(len(found), 1, list(self.root.rglob("task.md")))
        md = found[0]
        text = md.read_text(encoding="utf-8").replace("## Risk\nunclassified", "## Risk\nreversible")
        md.write_text(text.replace("- [ ] ", "- [x] "), encoding="utf-8")
        r = self.tasks("work", "done", cwd=self.root)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)          # its verify is `false`
        self.assertNotIn("NOTHING verified", r.stdout + r.stderr)
        self.assertIn("in_progress", md.read_text(encoding="utf-8"))

    def test_a_project_path_with_a_space_and_brackets_is_matched_literally(self):
        odd = self.root / "pro j[1]"
        (odd / ".agent" / "tasks").mkdir(parents=True)
        (odd / ".agent" / "config.json").write_text(json.dumps({"verify": "false"}), encoding="utf-8")
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; create_wrapper "$2" tasks', "-", str(LIB), str(odd)],
                           env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([str(odd / ".claude" / "bin" / "tasks"), "new", "quick", "fix", "fix f"], cwd=str(self.root),
                           env=self.env(), capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in (odd / ".agent" / "tasks").iterdir()], ["001-fix"])
        self.assertFalse((self.root / ".agent").exists())

    def test_the_cli_starts_in_the_callers_folder_inside_the_project_and_in_the_project_from_outside(self):
        # my mutant run: an unquoted pattern, or `pwd` for `pwd -P`, left the project's own subfolders for its root —
        # a stand-in CLI (the manifest points at a tree of its own) prints the folder it was started in
        stand = self.home / "stand"
        (stand / "scripts").mkdir(parents=True)
        (stand / "scripts" / "tasks").write_text("#!/bin/bash\npwd -P\n", encoding="utf-8")
        (stand / "scripts" / "tasks").chmod(0o755)
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"playbook@mk": [{"scope": "user", "installPath": str(stand),
                                                         "version": "9.9.9"}]}}), encoding="utf-8")
        odd = self.root / "pro j[1]"
        (odd / "sub").mkdir(parents=True)
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; create_wrapper "$2" tasks', "-", str(LIB), str(odd)],
                           env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        (self.root / "into-src").symlink_to(self.proj / "src")
        for wrapper, cwd, want in ((self.wrapper, self.proj / "src", self.proj / "src"),
                                   (self.wrapper, self.root / "into-src", self.proj / "src"),
                                   (self.wrapper, self.proj, self.proj),
                                   (self.wrapper, self.root, self.proj),
                                   (odd / ".claude" / "bin" / "tasks", odd / "sub", odd / "sub"),
                                   (odd / ".claude" / "bin" / "tasks", self.root, odd)):
            # PWD as a shell sets it: the path the caller came by, a link included
            r = subprocess.run([str(wrapper)], cwd=str(cwd), env={**self.env(), "PWD": str(cwd)}, capture_output=True,
                               text=True, timeout=60)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, str(want)), (str(cwd), r.stderr))

    def test_control_a_subfolder_of_the_project_still_works(self):
        r = self.tasks("new", "quick", "fix", "fix f", cwd=self.proj / "src")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in (self.proj / ".agent" / "tasks").iterdir()], ["001-fix"])
