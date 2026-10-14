"""The task registry: which task directories the CLI made (task 195, PLAN §S14 T5b — B31's provenance).

The owner's answer of 2026-10-13 12:27, "Registru scris de CLI, folder creat atomic (Recomandat)": a registry the CLI
writes beside each lane's `tasks/`; every creator (`tasks new`, `tasks freehand`, `tasks retro`) reserves a number,
builds the directory under a temporary name in the lane, renames it in and then registers it — under ONE per-lane lock
that also serialises the number (task 186 r3: two creators could take one number; `tasks freehand` then SHARED the
directory). Reads never write; the writers enrol what exists; a directory whose task.md git has COMMITTED counts as
known; `tasks adopt` takes in one that should stay. The plan panel's findings (task 195's `## Plan Review`) are cited
on the tests they shaped.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"

# one CLI run whose number step waits (up to 3 s) until N contenders have reached it: with no lock all of them pass it
# together and take one number; with the lane lock the second never reaches it while the first holds the lock, the
# first's wait runs out, and the numbers come out distinct (plan panel T10: a barrier inside the locked step must time
# out, not deadlock)
RACE = r'''
import os, sys, time, runpy
sys.path.insert(0, sys.argv[1])
bar, me, n = sys.argv[2], sys.argv[3], int(sys.argv[4])
import tasks.core as core
real = core._next_task_number
def waiting(*a, **k):
    open(os.path.join(bar, me), "w").close()
    t0 = time.monotonic()
    while len(os.listdir(bar)) < n and time.monotonic() - t0 < 3:
        time.sleep(0.02)
    together = len(os.listdir(bar)) >= n
    number = real(*a, **k)
    if together:
        time.sleep(0.3)            # every contender holds its number before any of them makes its directory
    return number
core._next_task_number = waiting
sys.argv = ["tasks"] + sys.argv[5:]
runpy.run_module("tasks.cli", run_name="__main__", alter_sys=True)
'''


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "home").mkdir()
        self.p = self.tmp / "proj"
        (self.p / ".agent" / "tasks").mkdir(parents=True)
        self.agent = self.p / ".agent"
        self.tasks = self.agent / "tasks"

    def env(self, sid="reg-sess", **extra):
        proc = self.tmp / "proc-no-agent"
        (proc / "self").mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE", "PLAYBOOK_SANDBOXED", "PLAYBOOK_ROLE")}
        env.update(HOME=str(self.tmp / "home"), PLAYBOOK_SESSION_ID=sid, PLAYBOOK_PROC_ROOT=str(proc))
        env.update(extra)
        return env

    def cli(self, *args, sid="reg-sess", check=False):
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / "tasks"), *args], cwd=self.p, env=self.env(sid), text=True,
                           capture_output=True, timeout=120)
        if check:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def registry_lines(self, agent=None):
        f = (agent or self.agent) / "task-registry"
        if not f.exists():
            return None
        return f.read_text(encoding="utf-8").splitlines()

    def registered(self, agent=None):
        lines = self.registry_lines(agent) or []
        return {ln.split("\t")[0]: ln.split("\t")[1] for ln in lines[1:] if ln}

    def task_dirs(self):
        return sorted(d.name for d in self.tasks.iterdir() if d.is_dir())

    def handmade(self, name, task_md=True):
        d = self.tasks / name
        d.mkdir()
        if task_md:
            (d / "task.md").write_text(f"# {name.split('-')[0]} - hand made\n\n## Status\npending\n", encoding="utf-8")
        return d

    def registry_module(self):
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(lambda: sys.path.remove(str(PLUGIN)) if str(PLUGIN) in sys.path else None)
        import importlib
        import tasks.registry as reg       # noqa: E402 — the module under test
        return importlib.reload(reg)


class TwoCreatorsAtOnce(_Project):
    def race(self, a, b, sid_a="sess-a", sid_b="sess-b"):
        bar = self.tmp / "barrier"
        bar.mkdir()
        procs = [subprocess.Popen([sys.executable, "-c", RACE, str(PLUGIN), str(bar), name, "2", *args], cwd=self.p,
                                  env=self.env(sid), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for name, args, sid in (("a", a, sid_a), ("b", b, sid_b))]
        outs = [pr.communicate(timeout=120) for pr in procs]
        return [(pr.returncode, o + e) for pr, (o, e) in zip(procs, outs)]

    def assertTwoDistinctTasks(self, results):
        for rc, out in results:
            self.assertEqual(rc, 0, out)
        dirs = self.task_dirs()
        self.assertEqual(len(dirs), 2, f"two creators made {dirs}")
        self.assertEqual(len({d.split("-")[0] for d in dirs}), 2, f"one number taken twice: {dirs}")
        reg = self.registered()
        self.assertEqual(sorted(n for n, o in reg.items() if o != "reserved"), dirs)

    def test_control_one_creator_alone(self):
        # the control: one creator with the same barrier (it times out alone) makes one task, registered as `new`
        results = self.race(["new", "quick", "only"], ["list"])
        for rc, out in results:
            self.assertEqual(rc, 0, out)
        self.assertEqual(self.task_dirs(), ["001-only"])
        self.assertEqual(self.registered(), {"001-only": "new"})

    def test_new_and_new(self):
        self.assertTwoDistinctTasks(self.race(["new", "quick", "a"], ["new", "quick", "b"]))

    def test_freehand_and_freehand(self):
        # today freehand mkdirs with exist_ok=True: two at once SHARED one directory
        self.assertTwoDistinctTasks(self.race(["freehand"], ["freehand"]))

    def test_new_and_retro(self):
        self.cli("new", "quick", "first", check=True)
        bar_results = self.race(["new", "quick", "second"], ["retro"])
        for rc, out in bar_results:
            self.assertEqual(rc, 0, out)
        dirs = self.task_dirs()
        self.assertEqual(len(dirs), 3, dirs)
        self.assertEqual(len({d.split("-")[0] for d in dirs}), 3, f"one number taken twice: {dirs}")

    def test_six_processes_without_a_barrier(self):
        # real flock contention between sessions: six `tasks new` started together
        procs = [subprocess.Popen([bash_or_skip(), str(SCRIPTS / "tasks"), "new", "quick", f"t{i}"], cwd=self.p,
                                  env=self.env(f"s{i}"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(6)]
        for pr in procs:
            _o, e = pr.communicate(timeout=120)
            self.assertEqual(pr.returncode, 0, e)
        dirs = self.task_dirs()
        self.assertEqual(len({d.split("-")[0] for d in dirs}), 6, dirs)


class ACrashedCreator(_Project):
    CRASH = r'''
import os, sys, runpy
sys.path.insert(0, sys.argv[1])
import tasks.registry as reg
reg._publish = lambda *a, **k: os._exit(9)          # killed after reserving, before the rename
sys.argv = ["tasks", "new", "quick", "doomed"]
runpy.run_module("tasks.cli", run_name="__main__", alter_sys=True)
'''

    def test_a_reserved_name_is_never_known_and_its_number_is_not_reused(self):
        r = subprocess.run([sys.executable, "-c", self.CRASH, str(PLUGIN)], cwd=self.p, env=self.env(), text=True,
                           capture_output=True, timeout=120)
        self.assertEqual(r.returncode, 9, r.stdout + r.stderr)
        self.assertEqual(self.task_dirs(), [], "a half-made task appeared under tasks/")
        self.assertEqual(self.registered(), {"001-doomed": "reserved"})
        reg = self.registry_module()
        self.assertFalse(reg.is_known(self.p, self.agent, "001-doomed"))
        self.handmade("001-doomed")                       # a later hand-made directory of that name
        self.assertFalse(reg.is_known(self.p, self.agent, "001-doomed"), "a reservation authorized a hand-made dir")
        self.cli("new", "quick", "next", check=True)
        self.assertIn("002-next", self.task_dirs())
        # the way out for a reserved name that now has a folder (a kill after the rename, or one made by hand):
        # `tasks adopt` — one line for it, its origin `adopt`
        self.cli("adopt", "001-doomed", check=True)
        self.assertEqual([ln.split("\t")[0] for ln in self.registry_lines()[1:]].count("001-doomed"), 1)
        self.assertEqual(self.registered()["001-doomed"], "adopt")
        self.assertTrue(reg.is_known(self.p, self.agent, "001-doomed"))
        self.assertFalse([x for x in self.agent.iterdir() if x.name.startswith(".task-creating.")],
                         "the dead creator's temp directory was left")


class TheRenameTarget(_Project):
    def test_an_empty_directory_appearing_at_the_final_name_is_not_laundered(self):
        reg = self.registry_module()
        made = []

        def render(num, name):
            if not made:                                    # a hand-made empty dir appears at the final name
                (self.tasks / name).mkdir()
                made.append(name)
            return f"# {num:03d} - x\n\n## Status\npending\n"
        final = reg.create_task_dir(self.p, self.agent, "x", "new", render)
        self.assertEqual(made, ["001-x"])
        self.assertEqual(final.name, "002-x")
        self.assertTrue((self.tasks / "001-x").is_dir() and not any((self.tasks / "001-x").iterdir()))
        self.assertFalse(reg.is_known(self.p, self.agent, "001-x"), "the hand-made empty dir was laundered")
        self.assertTrue(reg.is_known(self.p, self.agent, "002-x"))


class Enrolment(_Project):
    def test_the_first_writer_enrols_the_directories_that_hold_a_task_md(self):
        self.handmade("001-a")
        self.handmade("002-b")
        self.handmade("003-c", task_md=False)
        self.assertIsNone(self.registry_lines(), "a registry existed before any writer ran")
        self.cli("list")                                          # a READ: writes nothing (plan panel T2)
        self.assertIsNone(self.registry_lines(), "a read wrote the registry")
        self.cli("new", "quick", "d", check=True)
        self.assertEqual(self.registered(), {"001-a": "enrolled", "002-b": "enrolled", "004-d": "new"})
        reg = self.registry_module()
        self.assertFalse(reg.is_known(self.p, self.agent, "003-c"))


class TheGitRule(_Project):
    def git(self, *args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=self.p, check=True,
                       capture_output=True)

    def test_a_committed_task_is_known_a_staged_one_is_not(self):
        self.git("init", "-q")
        self.handmade("001-a")
        self.git("add", "-A")
        self.git("commit", "-qm", "a")
        self.handmade("002-b")
        self.git("add", "-A")                               # staged, never committed (plan panel T4)
        reg = self.registry_module()
        self.assertTrue(reg.is_known(self.p, self.agent, "001-a"))
        self.assertFalse(reg.is_known(self.p, self.agent, "002-b"), "the index made a hand-made dir known")
        self.assertIsNone(self.registry_lines(), "is_known wrote")


class Adopt(_Project):
    def test_adopt_registers_once_and_refuses_what_is_not_a_task_directory(self):
        # the lane has a registry already (a task was made); with none, the first writer — adopt included — enrols
        # every folder that exists (the "trusted at first enrolment" bound), so this one would read `enrolled`
        self.cli("new", "quick", "first", check=True)
        self.handmade("005-x")
        r = self.cli("adopt", "005-x", check=True)
        self.assertEqual(self.registered().get("005-x"), "adopt", r.stdout + r.stderr)
        self.cli("adopt", "005-x", check=True)                       # idempotent (plan panel T7)
        self.assertEqual([ln.split("\t")[0] for ln in self.registry_lines()[1:]].count("005-x"), 1)
        journal = (self.agent / "journal" / "enforcement.jsonl")
        self.assertIn("005-x", journal.read_text(encoding="utf-8") if journal.exists() else "")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "task.md").write_text("# 6\n", encoding="utf-8")
        (self.tasks / "006-link").symlink_to(elsewhere, target_is_directory=True)
        self.handmade("007-empty", task_md=False)
        self.handmade("005-y")                                       # the number of a registered one
        for name, why in (("006-link", "link"), ("007-empty", "task.md"), ("not-a-task", "name"), ("005-y", "005")):
            with self.subTest(name=name):
                r = self.cli("adopt", name)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn(why, r.stdout + r.stderr)
                self.assertNotIn(name, self.registered())


class EveryCreatorRegisters(_Project):
    def test_new_freehand_and_retro(self):
        self.cli("new", "quick", "a", check=True)
        self.cli("freehand", sid="free-sess", check=True)
        self.cli("retro", check=True)
        reg = self.registered()
        origins = sorted(reg.values())
        self.assertEqual(sorted(self.task_dirs()), sorted(reg), reg)
        self.assertEqual(origins, ["freehand", "new", "retro"], reg)


class TheExplicitNumber(_Project):
    def test_the_check_counts_a_burned_number(self):
        self.cli("new", "quick", "a", check=True)                     # 001
        f = self.agent / "task-registry"
        f.write_text(f.read_text(encoding="utf-8") + "002-burned\treserved\t2026-10-14T00:00:00Z\n", encoding="utf-8")
        r = self.cli("new", "quick", "002-x")
        self.assertNotEqual(r.returncode, 0, "a burned number was taken")
        self.cli("new", "quick", "003-x", check=True)
        self.assertIn("003-x", self.task_dirs())


class ACorruptRegistry(_Project):
    def test_a_creator_stops_and_names_the_repair(self):
        (self.agent / "task-registry").write_text("not a registry\n", encoding="utf-8")
        r = self.cli("new", "quick", "a")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("task-registry", r.stderr)
        self.assertIn("move it aside", r.stderr)
        self.assertEqual(self.task_dirs(), [])


class MachineLocal(_Project):
    def test_a_creator_leaves_only_the_task_in_git_status(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("cmm", SCRIPTS / "claude-md-merge.py")
            cmm = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cmm)
        finally:
            sys.path.remove(str(SCRIPTS))
        (self.p / ".gitignore").write_text("\n".join(cmm.GITIGNORE_ENTRIES) + "\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.p, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=self.p, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], cwd=self.p,
                       check=True)
        self.cli("new", "quick", "a", check=True)
        st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.p, text=True,
                            capture_output=True).stdout.split("\n")
        self.assertEqual([ln for ln in st if ln], ["?? .agent/tasks/001-a/task.md"])


class TempDirectoriesInTheLane(_Project):
    def test_a_left_temp_directory_is_removed_and_ignored(self):
        left = self.agent / ".task-creating.4242.009-x"
        left.mkdir()
        (left / "task.md").write_text("# 009\n", encoding="utf-8")
        r = self.cli("doctor")
        self.assertNotIn(".task-creating", r.stdout + r.stderr)
        self.cli("new", "quick", "a", check=True)
        self.assertFalse(left.exists(), "the left temp directory was not removed under the lock")
        self.assertIn("001-a", self.task_dirs())


class TheDoctorAdvisory(_Project):
    def test_doctor_names_the_folders_the_registry_does_not_know(self):
        r = self.cli("doctor")
        self.assertIn("no task registry yet", r.stdout)
        self.cli("new", "quick", "a", check=True)
        self.handmade("002-by-hand")
        r = self.cli("doctor")
        self.assertIn("folders the task registry does not know", r.stdout)
        self.assertIn("002-by-hand", r.stdout)
        self.assertNotIn("001-a", r.stdout.split("folders the task registry does not know", 1)[1].split("\n", 1)[0])


class Renames(_Project):
    def test_prepare_merge_renames_a_registered_directory_in_the_registry(self):
        self.cli("new", "quick", "a", check=True)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
                "import tasks.merge_prep as m\n"
                "m._rename_colliding_tasks(Path(sys.argv[2]), Path(sys.argv[2]) / '.agent', {1: '001-a'}, {1: 7})\n")
        r = subprocess.run([sys.executable, "-c", code, str(PLUGIN), str(self.p)], env=self.env(), text=True,
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.task_dirs(), ["007-a"])
        self.assertEqual(self.registered(), {"007-a": "new"})

    def test_the_freehand_rename_gate_names_adopt(self):
        self.cli("freehand", check=True)
        md = next(self.tasks.glob("*/task.md")).read_text(encoding="utf-8")
        self.assertIn("tasks adopt", md)


if __name__ == "__main__":
    unittest.main()
