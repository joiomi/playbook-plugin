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
import shutil
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
        self.base = base = Path(self._tmp.name).resolve()
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
        self.assertRegex(r.stdout + r.stderr, r"\[FAIL[^\]]*\] false\n")      # impl round 1 (opus): refused by it
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
        (odd / ".agent").mkdir()                       # a project, as init makes one
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

    def test_a_project_whose_claude_folder_is_a_link_keeps_its_tasks(self):
        # impl round 1 (opus, codex reproduced it twice): with `proj/.claude` a link to a shared folder, the wrapper's
        # resolved folder named the shared folder's parent, and callers inside the project were sent there
        shared = self.base / "shared"
        (shared / ".claude").mkdir(parents=True)
        shutil.rmtree(self.proj / ".claude")
        (self.proj / ".claude").symlink_to(shared / ".claude")
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; create_wrapper "$2" tasks', "-", str(LIB),
                            str(self.proj)], env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        for n, cwd in ((1, self.proj / "src"), (2, self.root)):
            r = self.tasks("new", "quick", f"fix{n}", "fix f", cwd=cwd)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(p.name for p in (self.proj / ".agent" / "tasks").iterdir()), ["001-fix1", "002-fix2"])
        self.assertFalse((shared / ".agent").exists())
        self.assertFalse((self.root / ".agent").exists())

    def test_a_sibling_whose_name_begins_with_the_projects_is_outside_it(self):
        # impl round 1 (grok): the pattern's `/` keeps `proj-other` outside `proj`
        other = self.root / "proj-other"
        other.mkdir()
        r = self.tasks("new", "quick", "fix", "fix f", cwd=other)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in (self.proj / ".agent" / "tasks").iterdir()], ["001-fix"])
        self.assertFalse((other / ".agent").exists())

    def test_a_wrapper_called_through_a_link_to_it_runs_where_it_is_called(self):
        # impl round 1 (opus, sonnet): linked onto PATH (`~/.local/bin/tasks`), the wrapper took the folder two levels
        # above the link for its project; it enters nothing then, and a caller inside the project gets the project
        link = self.home / ".local" / "bin" / "tasks"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.wrapper)
        (self.home / ".agent" / "tasks").mkdir(parents=True)  # a home with an `.agent` of its own (his has one)
        r = subprocess.run([str(link), "new", "quick", "fix", "fix f"], cwd=str(self.proj / "src"), env=self.env(),
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in (self.proj / ".agent" / "tasks").iterdir()], ["001-fix"])
        self.assertEqual(list((self.home / ".agent" / "tasks").iterdir()), [])

    def test_a_wrapper_in_a_folder_without_agent_enters_nothing(self):
        # my mutant run on impl round 1's fix: a folder holding the wrapper but no `.agent` is not a project to enter
        stand = self.home / "stand"
        (stand / "scripts").mkdir(parents=True)
        (stand / "scripts" / "tasks").write_text("#!/bin/bash\npwd -P\n", encoding="utf-8")
        (stand / "scripts" / "tasks").chmod(0o755)
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"playbook@mk": [{"scope": "user", "installPath": str(stand),
                                                         "version": "9.9.9"}]}}), encoding="utf-8")
        shutil.rmtree(self.proj / ".agent")
        r = subprocess.run([str(self.wrapper)], cwd=str(self.root), env={**self.env(), "PWD": str(self.root)},
                           capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(self.root)), r.stderr)

    def test_a_link_to_homes_own_wrapper_runs_where_it_is_called(self):
        # impl round 2 (codex, reproduced twice): HOME a project with its own wrapper, linked onto PATH — the `-ef` guard
        # held, and callers from another project were sent into HOME
        (self.home / ".agent" / "tasks").mkdir(parents=True)
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; create_wrapper "$2" tasks', "-", str(LIB),
                            str(self.home)], env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        link = self.home / ".local" / "bin" / "tasks"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.home / ".claude" / "bin" / "tasks")
        r = subprocess.run([str(link), "new", "quick", "fix", "fix f"], cwd=str(self.proj / "src"), env=self.env(),
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in (self.proj / ".agent" / "tasks").iterdir()], ["001-fix"])
        self.assertEqual(list((self.home / ".agent" / "tasks").iterdir()), [])

    def test_an_exported_cdpath_does_not_change_the_project(self):
        # impl round 2 (codex, opus): a relative call looked `cd` up through CDPATH, which printed the folder too
        r = subprocess.run(["proj/.claude/bin/tasks", "new", "quick", "fix", "fix f"], cwd=str(self.root),
                           env={**self.env(), "CDPATH": str(self.root)}, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in (self.proj / ".agent" / "tasks").iterdir()], ["001-fix"])
        self.assertFalse((self.root / ".agent").exists())

    def test_an_inherited_physical_option_keeps_a_linked_claude_folder_s_project(self):
        # impl round 2 (codex, reproduced twice): `SHELLOPTS=physical` in the environment made the `cd` physical
        shared = self.base / "shared"
        (shared / ".claude").mkdir(parents=True)
        shutil.rmtree(self.proj / ".claude")
        (self.proj / ".claude").symlink_to(shared / ".claude")
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; create_wrapper "$2" tasks', "-", str(LIB),
                            str(self.proj)], env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([str(self.wrapper), "new", "quick", "fix", "fix f"], cwd=str(self.root),
                           env={**self.env(), "SHELLOPTS": "physical"}, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in (self.proj / ".agent" / "tasks").iterdir()], ["001-fix"])
        self.assertFalse((shared / ".agent").exists())
        self.assertFalse((self.root / ".agent").exists())

    def test_control_a_close_from_the_parent_folder_with_a_passing_verify_closes_in_the_project(self):
        # impl round 2 (opus): the close itself, not only its refusal, happens in the project
        (self.proj / ".agent" / "config.json").write_text(json.dumps({"verify": "true"}), encoding="utf-8")
        for args in (("new", "quick", "fix", "fix f"), ("work", "1")):
            r = self.tasks(*args, cwd=self.root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        md = next((self.proj / ".agent" / "tasks").glob("001-*/task.md"))
        text = md.read_text(encoding="utf-8").replace("## Risk\nunclassified", "## Risk\nreversible")
        md.write_text(text.replace("- [ ] ", "- [x] "), encoding="utf-8")
        r = self.tasks("work", "done", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(md.read_text(encoding="utf-8"), r"## Status\n(done|closed)")
        self.assertFalse((self.root / ".agent").exists())

    def test_control_a_subfolder_of_the_project_still_works(self):
        r = self.tasks("new", "quick", "fix", "fix f", cwd=self.proj / "src")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in (self.proj / ".agent" / "tasks").iterdir()], ["001-fix"])
