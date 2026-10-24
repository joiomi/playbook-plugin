"""Task 302 (stabilization audit 3, G1; the owner's ruling 2026-10-24 12:49, "4 grave, G2 nu"): a task record is
judged by the project that owns it, and a session in a worktree still answers for its task in the main tree.

Every hook found its project by walking up from the hook's working directory (`find_project_root`), and the record
guards (0 a hand-made task.md, 0.5 a batch of bare ticks, 0.6 `## Status`) matched a path only against THAT project's
lanes. Claude Code runs hooks in the current directory, which EnterWorktree moves to `.claude/worktrees/<name>/` — a
checkout with its own task records. So from a worktree the main tree's task.md was just an exempt `.agent/` path: a
`## Status` edit to `done`, six bare ticks and a hand-made task.md all passed, and the stop hook let the turn end with
the session's main task open. With no project at the cwd at all, the gate exited before reading the payload. His blind
session's G1, reproduced on A and B (task 300, `evidence/m2-mine/my_g1.out`).

Now the gate runs once more, in the target's project, for a record of another project and for any target when the cwd
is in no project; and the stop hook in a worktree where the session has no pointer reads the main tree's.

Run: python3 -m unittest tests.test_task_guards_judge_the_files_project
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
SCRIPTS = REPO_ROOT / "plugins" / "playbook" / "scripts"
SID = "pid-g1-task302"
GATES = "\n".join(f"- [ ] g{i}" for i in range(1, 9))
TASK = "# 001 - real\n\n## Status\nin_progress\n\n## Work Plan\n" + GATES + "\n"
DONE = "# 001 - real\n\n## Status\nin_progress\n\n## Work Plan\n" + "\n".join(
    f"- [x] g{i}\n  - 12:00 — done" for i in range(1, 9)) + "\n"


def record(root: Path, text: str = TASK, lane: "str | None" = None, n: str = "001-real") -> Path:
    d = root / ".agent" / (lane + "/tasks" if lane else "tasks") / n
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.md").write_text(text, encoding="utf-8")
    if lane:
        (root / ".agent" / "current_user").write_text(lane + "\n", encoding="utf-8")
    return d / "task.md"


class _Tree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.p = self.root / "proj"
        self.w = self.p / ".claude" / "worktrees" / "w1"
        self.o = self.root / "outside"
        self.o.mkdir()
        for r in (self.p, self.w):                     # a worktree is a checkout: it holds the tracked records
            record(r)
            (r / "src").mkdir(parents=True)
            (r / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.home = self.root / "home"
        self.home.mkdir()

    def run_hook(self, name: str, payload: dict, cwd: Path, ascii_json: bool = False, **env) -> subprocess.CompletedProcess:
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "LANG": "C.UTF-8",
             "PLAYBOOK_SESSION_ID": SID}
        e.update(env)
        return subprocess.run([bash_or_skip(), str(SCRIPTS / name)], cwd=cwd, env=e, text=True,
                              input=json.dumps(payload, ensure_ascii=ascii_json), capture_output=True, timeout=60)

    def edit(self, path: Path, old: str, new: str, cwd: Path, **env) -> int:
        r = self.run_hook("task-gate-hook", {"tool_name": "Edit", "tool_input": {
            "file_path": str(path), "old_string": old, "new_string": new}, "cwd": str(cwd)}, cwd, **env)
        return r.returncode

    def write(self, path: Path, content: str, cwd: Path) -> int:
        return self.run_hook("task-gate-hook", {"tool_name": "Write", "tool_input": {
            "file_path": str(path), "content": content}, "cwd": str(cwd)}, cwd).returncode

    def stop(self, cwd: Path) -> int:
        return self.run_hook("stop-hook", {"stop_hook_active": False, "cwd": str(cwd)}, cwd).returncode

    def activate(self, root: Path, n: str = "001", lane: "str | None" = None):
        s = root / ".agent" / (lane + "/sessions" if lane else "sessions") / SID / "current_state"
        s.parent.mkdir(parents=True, exist_ok=True)
        s.write_text(n + "\n", encoding="utf-8")

    def status_edit(self, task_md: Path, cwd: Path, **env) -> int:
        return self.edit(task_md, "## Status\nin_progress", "## Status\ndone", cwd, **env)

    def gate_lines(self, root: Path) -> "list[dict]":
        f = root / ".agent" / "journal" / "hooks.jsonl"
        if not f.exists():
            return []
        return [x for x in (json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip())
                if x.get("hook") == "task-gate"]


class FromTheWorktree(_Tree):
    def setUp(self):
        super().setUp()
        self.activate(self.p)                          # the session's task is active in the MAIN tree
        self.main_md = self.p / ".agent" / "tasks" / "001-real" / "task.md"

    def test_a_status_edit_of_the_main_record_is_refused(self):
        self.assertEqual(self.status_edit(self.main_md, self.p), 2)            # as from the main tree
        self.assertEqual(self.status_edit(self.main_md, self.w), 2)

    def test_a_batch_of_bare_ticks_in_the_main_record_is_refused(self):
        old = "\n".join(f"- [ ] g{i}" for i in range(1, 7))
        self.assertEqual(self.edit(self.main_md, old, old.replace("- [ ]", "- [x]"), self.p), 2)
        self.assertEqual(self.edit(self.main_md, old, old.replace("- [ ]", "- [x]"), self.w), 2)

    def test_a_hand_made_task_md_in_the_main_lane_is_refused(self):
        fake = self.p / ".agent" / "tasks" / "099-fake" / "task.md"
        self.assertEqual(self.write(fake, "# 099 - fake\n\n## Status\ndone\n", self.p), 2)
        self.assertEqual(self.write(fake, "# 099 - fake\n\n## Status\ndone\n", self.w), 2)


    def test_a_link_in_the_worktree_to_the_main_record_is_judged_by_the_main_tree(self):
        # the written path is an exempt `.agent/` file of the worktree; its resolved reading is the main record
        link = self.w / ".agent" / "main-record.md"
        link.symlink_to(self.main_md)
        self.assertEqual(self.status_edit(link, self.w), 2)


class FromTheMainTree(_Tree):
    def test_a_status_edit_of_the_worktree_record_is_refused(self):
        self.activate(self.p)
        wt_md = self.w / ".agent" / "tasks" / "001-real" / "task.md"
        self.assertEqual(self.status_edit(wt_md, self.w), 2)                    # as from the worktree
        self.assertEqual(self.status_edit(wt_md, self.p), 2)


class AnotherTreeOfTheRepository(_Tree):
    def test_a_status_edit_of_a_sibling_worktrees_record_is_refused(self):
        self.activate(self.p)
        cases = {"single-lane": ("w2", None), "multi-user": ("w3", "alice"), "non-ASCII name": ("ramură-ăîș", None)}
        for label, (name, lane) in cases.items():
            with self.subTest(case=label):
                sib = self.p / ".claude" / "worktrees" / name
                md = record(sib, lane=lane)
                self.assertEqual(self.status_edit(md, sib), 2)                  # as from that worktree
                self.assertEqual(self.status_edit(md, self.w), 2)               # from w1
                self.assertEqual(self.status_edit(md, self.p), 2)               # from the main tree


class AnUnrelatedProject(_Tree):
    def test_an_unrelated_projects_records_are_not_this_guards(self):
        # Retro 107 R6 (the owner's triage, 2026-09-29), task 171: a record of a project that is not a tree of this
        # repository is not this guard's — `tasks new` cannot make a task there (a test fixture, a scratch project).
        self.activate(self.p)
        cases = {"single-lane": (self.root / "b", None), "multi-user": (self.root / "b2", "alice"),
                 "non-ASCII folder": (self.root / "proiect-ăîș", None)}
        for label, (b, lane) in cases.items():
            with self.subTest(case=label):
                md = record(b, lane=lane)
                self.assertEqual(self.status_edit(md, b), 2)                    # its own guard, from inside it
                self.assertEqual(self.status_edit(md, self.p), 0)
                self.assertEqual(self.write(md.parent.parent / "009-x" / "task.md", "# 009 - x\n", self.p), 0)


class FromNoProject(_Tree):
    def test_a_code_edit_is_judged_in_the_files_project(self):
        code = self.p / "src" / "a.py"
        self.assertEqual(self.edit(code, "x = 1", "x = 2", self.o), 2)         # no active task in the file's project
        self.activate(self.p)
        self.assertEqual(self.edit(code, "x = 1", "x = 2", self.o), 0)

    def test_a_record_edit_is_judged_in_its_project(self):
        self.assertEqual(self.status_edit(self.p / ".agent" / "tasks" / "001-real" / "task.md", self.o), 2)
        loose = self.o / "notes.py"
        loose.write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.edit(loose, "x = 1", "x = 2", self.o), 0)         # a file in no project: as today


class TheStop(_Tree):
    def test_the_main_task_holds_the_stop_in_the_worktree(self):
        self.activate(self.p)
        self.assertEqual(self.stop(self.p), 2)
        self.assertEqual(self.stop(self.w), 2)
        record(self.p, DONE)
        self.assertEqual(self.stop(self.w), 0)

    def test_the_worktrees_own_pointer_decides_there(self):
        self.activate(self.p)
        self.activate(self.w)                                                   # `tasks work` in the worktree
        self.assertEqual(self.stop(self.w), 2)                                  # its own task, open
        record(self.w, DONE)
        self.assertEqual(self.stop(self.w), 0)                                  # its own task complete, main open
        self.assertEqual(self.edit(self.w / "src" / "a.py", "x = 1", "x = 2", self.w), 0)
        (self.w / ".agent" / "sessions" / SID / "current_state").unlink()
        self.assertEqual(self.edit(self.w / "src" / "a.py", "x = 1", "x = 2", self.w), 2)   # 298: main's does not open it


    def test_a_worktree_whose_main_tree_is_no_project_is_not_held_by_a_parent(self):
        # the stop goes to the main tree only when that tree is itself a project: a project AROUND the repository is
        # not its main tree (my mutant run: dropping that check let the parent's task hold the stop)
        outer = self.root / "outer"
        record(outer)
        self.activate(outer)                           # the session's task in the project around the repository
        wt = outer / "repo" / ".claude" / "worktrees" / "w9"
        record(wt)
        self.assertEqual(self.stop(outer), 2)
        self.assertEqual(self.stop(wt), 0)


class InItsOwnProject(_Tree):
    def test_nothing_changes_in_the_files_own_project(self):
        self.activate(self.p)
        md = self.p / ".agent" / "tasks" / "001-real" / "task.md"
        self.assertEqual(self.edit(md, "- [ ] g1", "- [x] g1\n  - 12:00 — done", self.p), 0)
        self.assertEqual(self.edit(self.p / "src" / "a.py", "x = 1", "x = 2", self.p), 0)
        self.assertEqual(self.stop(self.p), 2)


class OneHop(_Tree):
    def test_the_gate_hops_once(self):
        self.activate(self.p)
        main_md = self.p / ".agent" / "tasks" / "001-real" / "task.md"
        before_p, before_w = len(self.gate_lines(self.p)), len(self.gate_lines(self.w))
        self.assertEqual(self.status_edit(main_md, self.w), 2)
        self.assertEqual(len(self.gate_lines(self.p)) - before_p, 1, "the judging project journals the call")
        self.assertEqual(len(self.gate_lines(self.w)) - before_w, 0, "the cwd project journals nothing")
        # with the hop already made (its flag set), the gate judges where it runs: no second hop
        self.assertEqual(self.status_edit(main_md, self.w, PB_GATE_HOPPED="1"), 0)
        self.assertEqual(len(self.gate_lines(self.w)) - before_w, 1)


class RoundOne(_Tree):
    """Impl round 1 of task 302 (opus, grok, codex, agy)."""

    def setUp(self):
        super().setUp()
        self.main_md = self.p / ".agent" / "tasks" / "001-real" / "task.md"

    def counters(self, root: Path, writes: int, tools: int):
        d = root / ".agent" / "sessions" / SID
        d.mkdir(parents=True, exist_ok=True)
        (d / "counters").write_text(f"writes={writes}\ntools={tools}\n", encoding="utf-8")

    def test_a_hopped_stop_reads_the_worktrees_activity(self):
        # opus C, grok C (reproduced), codex I: the main tree's counters are frozen when the session leaves
        self.activate(self.p)
        self.counters(self.p, 0, 0)
        self.counters(self.w, 12, 40)
        self.assertEqual(self.stop(self.w), 2)
        self.counters(self.w, 0, 1)                                             # a chat reply, in the worktree
        self.assertEqual(self.stop(self.w), 0)

    def test_a_relative_path_to_another_trees_record_is_refused(self):
        # agy C, opus I: the hop would re-read a relative path from another directory
        self.activate(self.p)
        rel = os.path.relpath(self.main_md, self.w)
        self.assertEqual(self.edit(rel, "## Status\nin_progress", "## Status\ndone", self.w), 2)

    def test_readings_that_name_two_trees_records_are_refused(self):
        # codex I: the written reading is the worktree's record, the resolved one the main tree's
        self.activate(self.p)
        (self.w / "jump").symlink_to(self.p / "src")
        old = "\n".join(f"- [ ] g{i}" for i in range(1, 7))
        path = f"{self.w}/jump/../.agent/tasks/001-real/task.md"
        self.assertEqual(self.edit(path, old, old.replace("- [ ]", "- [x]"), self.p), 2)
        # from a third tree, the resolved reading first names the worktree's record (no pointer there), the written
        # one the main tree's: no single tree can judge it (my mutant run: hopping to the first owner let it through)
        w2 = self.p / ".claude" / "worktrees" / "w2"
        record(w2)
        (self.p / "jump").symlink_to(self.w / "src")
        path = f"{self.p}/jump/../.agent/tasks/001-real/task.md"
        self.assertEqual(self.edit(path, old, old.replace("- [ ]", "- [x]"), w2), 2)

    def test_a_home_relative_path_to_the_main_record_is_judged_there(self):
        # grok I: `~/` is expanded as Guard 0.6 expands it
        self.activate(self.p)
        self.assertEqual(self.edit("~/proj/.agent/tasks/001-real/task.md", "## Status\nin_progress",
                                   "## Status\ndone", self.w, HOME=str(self.root)), 2)

    def test_a_record_through_a_store_the_main_agent_is_linked_to_is_judged_there(self):
        # grok I: the record's `tasks/` folder is a lane of the main tree by identity, with no `.agent` in its text
        store = self.root / "store"
        (self.p / ".agent").rename(store)
        (self.p / ".agent").symlink_to(store)
        self.activate(self.p)
        md = store / "tasks" / "001-real" / "task.md"
        self.assertEqual(self.status_edit(md, self.p), 2)                       # from the main tree
        self.assertEqual(self.status_edit(md, self.w), 2)

    def test_from_no_project_an_escaped_path_or_an_outside_link_is_judged_in_its_project(self):
        # codex I: a JSON `\u` escape, and a link outside any project to a project's record
        q = self.root / "proiect-\u0103"
        md = record(q)
        r = self.run_hook("task-gate-hook", {"tool_name": "Edit", "tool_input": {
            "file_path": str(md), "old_string": "## Status\nin_progress", "new_string": "## Status\ndone"}},
            self.o, ascii_json=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        link = self.o / "rec.md"
        link.symlink_to(self.main_md)
        self.assertEqual(self.status_edit(link, self.o), 2)

    def test_from_no_project_a_shell_call_is_never_hopped(self):
        # agy I: only a file tool's target is followed (a stray `file_path` key in a Bash payload is not)
        before = len(self.gate_lines(self.p))
        r = self.run_hook("task-gate-hook", {"tool_name": "Bash", "tool_input": {
            "command": "ls", "file_path": str(self.p / "src" / "a.py")}}, self.o)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.gate_lines(self.p)), before, "the project judged a call it does not own")

    def test_the_main_tree_is_the_nearest_one(self):
        # agy m, opus m: a project inside another project's worktree has its own main tree
        inner = self.w / "inner"
        record(inner)
        x = inner / ".claude" / "worktrees" / "x"
        record(x)
        self.activate(self.p)                                                   # a task of the OUTER main tree
        self.assertEqual(self.stop(x), 0)
        self.assertEqual(self.status_edit(self.main_md, x), 0)                  # the outer record: not x's repository
        self.assertEqual(self.status_edit(inner / ".agent" / "tasks" / "001-real" / "task.md", x), 2)
        self.activate(inner)
        self.assertEqual(self.stop(x), 2)


if __name__ == "__main__":
    unittest.main()
