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

import json
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
        self.assertFalse([x for x in self.agent.iterdir() if x.name.startswith(reg.TEMP_PREFIX)],
                         "the collided attempt's temp directory was left behind")


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
        # every folder that exists (the "trusted at first enrolment" bound), and adopt then takes this one's
        # `enrolled` row over (task 197)
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


class NoDescriptorAnchor(_Project):
    """The single judge's run 3 (codex, Critical, reproduced with /proc hidden): without `/proc/self/fd` the creator
    fell back to the lane's PATH — a lane swapped during the build had its replacement's registry overwritten. The
    owner's ruling (2026-10-14): "Repară și închide" — the creator refuses to create then."""

    def test_a_creator_without_the_anchor_refuses_and_creates_nothing(self):
        reg = self.registry_module()
        reg._PROC_SELF_FD = str(self.tmp / "no-proc-self-fd")
        before = (self.agent / "task-registry").exists()
        with self.assertRaises(reg.RegistryUnreadable) as cm:
            reg.create_task_dir(self.p, self.agent, "x", "new", lambda n, name: f"# {n:03d} - x\n")
        self.assertIsInstance(cm.exception, reg.LaneUnanchored)
        said = reg.unreadable_message(cm.exception)
        self.assertIn(reg._PROC_SELF_FD, said)
        self.assertIn("Nothing was created", said)
        self.assertNotIn("move it aside", said)                 # the registry's repair would mislead here
        self.assertEqual(self.task_dirs(), [])
        self.assertEqual((self.agent / "task-registry").exists(), before)

    def test_control_with_the_anchor_it_creates(self):
        reg = self.registry_module()
        made = reg.create_task_dir(self.p, self.agent, "x", "new", lambda n, name: f"# {n:03d} - x\n")
        self.assertTrue(made.is_dir())


class AFlaggedFolderIsNoEnrolment(_Project):
    """Task 197's impl panel r1, I2 (fable, opus, sonnet): an `enrolled` row of a folder a standing task-folder flag
    names is a re-enrolment that took in a hand-made folder — the reader the watch and the close share counts it
    unknown; a flag it cannot read makes every uncommitted `enrolled` folder unknown (fail closed)."""

    def setUp(self):
        super().setUp()
        for n in ("001-a", "999-x"):
            (self.tasks / n).mkdir()
            (self.tasks / n / "task.md").write_text(f"# {n}\n", encoding="utf-8")
        self.rows("enrolled")
        self.sess = self.agent / "sessions" / "s-flagged"
        self.sess.mkdir(parents=True)

    def rows(self, origin_x):
        (self.agent / "task-registry").write_text(
            "# playbook task-registry v1\n001-a\tenrolled\t2026-10-14T00:00:00Z\n"
            f"999-x\t{origin_x}\t2026-10-14T00:00:01Z\n", encoding="utf-8")

    def flag(self, name="task_dir_flag", folders=(".agent/tasks/999-x",)):
        (self.sess / name).write_text(json.dumps({"v": 2, "folders": list(folders), "since": "2026-10-14T00:00:00Z"}),
                                      encoding="utf-8")

    def unknown(self):
        return self.registry_module().unknown_now(self.p, self.agent, no_registry="git")

    def test_a_flag_makes_its_enrolled_folder_unknown(self):
        self.assertEqual(self.unknown(), [])                  # control: no flag stands
        self.flag()
        self.assertEqual(self.unknown(), ["999-x"])

    def test_a_pending_entry_is_a_flag_and_an_unfinished_temp_is_not(self):
        self.flag("task_dir_flag.pending.1.2.abc.tmp")
        self.assertEqual(self.unknown(), [])
        self.flag("task_dir_flag.pending.1.2")
        self.assertEqual(self.unknown(), ["999-x"])

    def test_an_adopted_folder_stays_known_under_a_flag(self):
        self.rows("adopt")
        self.flag()
        self.assertEqual(self.unknown(), [])

    def test_a_pending_entry_merged_while_it_is_read_is_still_seen(self):
        # impl panel r2 J6 (codex, reproduced): the reader read the main flag before a re-check merged a pending entry
        # into it, then found the pending entry gone — the folder was missed and a close passed
        reg = self.registry_module()
        self.flag(folders=())
        self.flag("task_dir_flag.pending.1.2")
        real = reg._flag_names_at
        seen = {"main": 0}

        def interleaved(dfd, fname):
            if fname == "task_dir_flag":
                seen["main"] += 1
                if seen["main"] == 1:
                    return set()                              # read BEFORE the merge: nothing in it yet
            elif fname.startswith("task_dir_flag.pending."):
                self.flag(folders=(".agent/tasks/999-x",))     # the re-check merges it now…
                (self.sess / fname).unlink()                  # …and removes the entry it read
            return real(dfd, fname)
        reg._flag_names_at = interleaved
        self.assertEqual(reg.unknown_now(self.p, self.agent, no_registry="git"), ["999-x"])

    def test_a_flag_it_cannot_read_makes_every_enrolled_folder_unknown(self):
        (self.sess / "task_dir_flag").write_text("not json", encoding="utf-8")
        self.assertEqual(self.unknown(), ["001-a", "999-x"])
        (self.sess / "task_dir_flag").unlink()
        (self.sess / "task_dir_flag").symlink_to(self.tmp / "elsewhere.json")
        self.assertEqual(self.unknown(), ["001-a", "999-x"])
        (self.sess / "task_dir_flag").unlink()
        moved = self.tmp / "moved-session"
        self.sess.rename(moved)
        self.sess.symlink_to(moved)                           # a session directory that is a link
        self.assertEqual(self.unknown(), ["001-a", "999-x"])


class StandingBefore(_Project):
    """Task 197's impl panel r2, J5: what stood before a session's running calls — the intersection of its standing
    snapshots; None with none; an unfinished temp ignored; one it cannot read, or a session directory that is a
    link, gives an empty set (the close then fails closed)."""

    def snap(self, name, folders):
        d = self.agent / "sessions" / "s-snap"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps({"v": 1, "folders": folders, "registry": True}), encoding="utf-8")

    def test_the_intersection_of_the_standing_snapshots(self):
        reg = self.registry_module()
        self.assertIsNone(reg.standing_before(self.agent, "s-snap"))          # no session directory
        self.snap("task_dirs_before.toolu_1", ["001-a", "002-b"])
        self.snap("task_dirs_before.toolu_2", ["001-a"])
        self.snap("task_dirs_before.toolu_3.x1y2.tmp", [])                     # an unfinished write
        self.assertEqual(reg.standing_before(self.agent, "s-snap"), {"001-a"})

    def test_what_it_cannot_read_stood_nothing(self):
        reg = self.registry_module()
        self.snap("task_dirs_before.toolu_1", ["001-a"])
        (self.agent / "sessions" / "s-snap" / "task_dirs_before.toolu_2").write_text("not json", encoding="utf-8")
        self.assertEqual(reg.standing_before(self.agent, "s-snap"), set())
        moved = self.tmp / "moved"
        (self.agent / "sessions" / "s-snap").rename(moved)
        (self.agent / "sessions" / "s-snap").symlink_to(moved)
        self.assertEqual(reg.standing_before(self.agent, "s-snap"), set())


class EnsureEnrolledHoldsItsLane(_Project):
    """Task 197's impl panel r1, I4 (codex, reproduced): `ensure_enrolled` locked and wrote by PATH — a lane swapped
    for a link after its listing had the replacement's registry overwritten (its reservations lost)."""

    def test_a_lane_swapped_after_the_listing_keeps_the_replacement_registry(self):
        reg = self.registry_module()
        (self.tasks / "001-a").mkdir()
        (self.tasks / "001-a" / "task.md").write_text("# 001\n", encoding="utf-8")
        other = self.tmp / "other-lane"
        (other / "tasks").mkdir(parents=True)
        kept = "# playbook task-registry v1\n007-b\treserved\t2026-10-14T00:00:00Z\n"
        (other / "task-registry").write_text(kept, encoding="utf-8")
        moved = self.tmp / "moved-lane"
        real = reg._enrolment

        def swapped(agent_dir, *a, **k):
            rows = real(agent_dir, *a, **k)
            self.agent.rename(moved)
            self.agent.symlink_to(other)
            return rows
        reg._enrolment = swapped
        self.assertTrue(reg.ensure_enrolled(self.p, self.agent))
        self.assertEqual((other / "task-registry").read_text(encoding="utf-8"), kept)
        self.assertIn("001-a\tenrolled\t", (moved / "task-registry").read_text(encoding="utf-8"))

    def test_control_unswapped_it_enrols(self):
        reg = self.registry_module()
        (self.tasks / "001-a").mkdir()
        (self.tasks / "001-a" / "task.md").write_text("# 001\n", encoding="utf-8")
        self.assertTrue(reg.ensure_enrolled(self.p, self.agent))
        self.assertIn("001-a\tenrolled\t", (self.agent / "task-registry").read_text(encoding="utf-8"))
        self.assertFalse(reg.ensure_enrolled(self.p, self.agent))     # a registry that exists is left alone


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



class RoundOneFixes(_Project):
    """Task 195's impl panel round 1 (its `## Implementation Review`)."""

    def test_a_title_with_nothing_to_slug_never_poisons_the_registry(self):
        # I1 (all five judges): `tasks new quick "!!!"` made `001-`, a line the reader refuses
        r = self.cli("new", "quick", "!!!")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.task_dirs(), ["001-task"])
        self.cli("new", "quick", "next", check=True)                   # the registry still reads
        self.assertEqual(sorted(self.registered()), ["001-task", "002-next"])
        reg = self.registry_module()
        with self.assertRaises(ValueError):
            reg.create_task_dir(self.p, self.agent, "", "new", lambda n, name: "# x\n")
        self.assertEqual(sorted(self.registered()), ["001-task", "002-next"], "a refused name was written")

    def test_the_git_rule_in_a_project_below_its_repository_top(self):
        # I2 (fable, opus, codex): `--full-tree` read the project-relative path from the repository top
        repo = self.tmp / "repo"
        app = repo / "app"
        (app / ".agent" / "tasks" / "001-a").mkdir(parents=True)
        (app / ".agent" / "tasks" / "001-a" / "task.md").write_text("# 001\n", encoding="utf-8")
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
        subprocess.run(git + ["add", "-A"], cwd=repo, check=True)
        subprocess.run(git + ["commit", "-qm", "a"], cwd=repo, check=True)
        (app / ".agent" / "tasks" / "002-b").mkdir()
        (app / ".agent" / "tasks" / "002-b" / "task.md").write_text("# 002\n", encoding="utf-8")
        subprocess.run(git + ["add", "-A"], cwd=repo, check=True)
        reg = self.registry_module()
        self.assertTrue(reg.is_known(app, app / ".agent", "001-a"))
        self.assertFalse(reg.is_known(app, app / ".agent", "002-b"))
        self.assertEqual(reg.unknown_task_dirs(app, app / ".agent"), ["002-b"])

    def test_the_freehand_rename_then_adopt(self):
        # I3 (opus, codex): the gate says rename then adopt; adopt refused — the old row still owned the number
        self.cli("freehand", check=True)
        (self.tasks / "001-freehand").rename(self.tasks / "001-finished")
        r = self.cli("adopt", "001-finished")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.registered(), {"001-finished": "adopt"})

    def test_adopt_refuses_a_number_another_present_folder_has(self):
        # I3: a committed `005-x` beside a hand-made `005-y` — the registry does not name `005-x`
        self.cli("new", "quick", "first", check=True)
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git + ["init", "-q"], cwd=self.p, check=True)
        self.handmade("005-x")
        subprocess.run(git + ["add", ".agent/tasks/005-x/task.md"], cwd=self.p, check=True)
        subprocess.run(git + ["commit", "-qm", "x"], cwd=self.p, check=True)
        self.handmade("005-y")
        r = self.cli("adopt", "005-y")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("005", r.stdout + r.stderr)
        self.assertNotIn("005-y", self.registered())

    def test_prepare_merge_refuses_before_moving_anything(self):
        # I4 (opus, codex): an unreadable registry stopped the renumbering halfway (folders moved, refs not rewritten)
        self.cli("new", "quick", "a", check=True)
        (self.agent / "task-registry").write_text("not a registry\n", encoding="utf-8")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
                "import tasks.merge_prep as m\n"
                "from tasks.registry import RegistryUnreadable\n"
                "try:\n"
                "    m._rename_colliding_tasks(Path(sys.argv[2]), Path(sys.argv[2]) / '.agent', {1: '001-a'}, {1: 7})\n"
                "except RegistryUnreadable:\n"
                "    sys.exit(3)\n")
        r = subprocess.run([sys.executable, "-c", code, str(PLUGIN), str(self.p)], env=self.env(), text=True,
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(self.task_dirs(), ["001-a"], "a folder was moved before the registry was read")

    def test_a_render_error_is_not_taken_for_a_collision(self):
        # I5 (sonnet): a FileExistsError raised inside render looped, reserving and burning numbers
        reg = self.registry_module()

        def render(num, name):
            raise FileExistsError("a custom playbook's own error")
        with self.assertRaises(FileExistsError):
            reg.create_task_dir(self.p, self.agent, "x", "new", render)
        self.assertEqual(len([o for o in self.registered().values() if o == "reserved"]), 1)
        self.assertFalse([x for x in self.agent.iterdir() if x.name.startswith(".task-creating.")])

    def test_a_full_folder_appearing_at_rename_time_is_a_collision(self):
        # I5 (opus): rename(2) onto a non-empty folder raises ENOTEMPTY, not FileExistsError
        import errno
        reg = self.registry_module()
        real = reg._publish
        calls = []

        def publish(*args):
            if not calls:
                calls.append(args)
                raise OSError(errno.ENOTEMPTY, "Directory not empty")
            return real(*args)
        reg._publish = publish
        try:
            final = reg.create_task_dir(self.p, self.agent, "x", "new", lambda n, name: f"# {n:03d}\n")
        finally:
            reg._publish = real
        self.assertEqual(final.name, "002-x")
        self.assertEqual(self.registered(), {"001-x": "reserved", "002-x": "new"})

    def test_a_leftover_temp_of_the_same_name_does_not_stop_a_creator(self):
        # I5 (opus): `os.mkdir(tmp)` outside the try — a leftover the sweep cannot remove escaped as a traceback
        reg = self.registry_module()
        real = reg._sweep_temps_at
        reg._sweep_temps_at = lambda *a, **k: None              # a leftover the sweep did not remove
        try:
            planted = self.agent / f"{reg.TEMP_PREFIX}{os.getpid()}.0"      # the temp name it would take first
            planted.mkdir()
            final = reg.create_task_dir(self.p, self.agent, "x", "new", lambda n, name: f"# {n:03d}\n")
        finally:
            reg._sweep_temps_at = real
        self.assertEqual(final.name, "001-x")
        self.assertTrue((final / "task.md").is_file())
        self.assertTrue(planted.is_dir(), "the leftover was not stepped over")



class RoundTwoFixes(_Project):
    """Task 195's impl panel round 2 (its `## Implementation Review`)."""

    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]

    def test_a_failed_creation_removes_its_temp_only_through_the_lane_it_holds(self):
        # J1 (codex, Critical): the temp was removed BY PATH — a lane swapped for a link sent the delete elsewhere
        reg = self.registry_module()
        victim = self.tmp / "victim-lane"
        victim.mkdir()

        def render(num, name):
            temp = next(x.name for x in self.agent.iterdir() if x.name.startswith(reg.TEMP_PREFIX))
            (victim / temp).mkdir()
            (victim / temp / "keep.txt").write_text("the owner's data\n", encoding="utf-8")
            self.agent.rename(self.agent.with_name(".agent.orig"))
            self.agent.symlink_to(victim, target_is_directory=True)
            raise RuntimeError("the template failed")
        with self.assertRaises(RuntimeError):
            reg.create_task_dir(self.p, self.agent, "x", "new", render)
        kept = [x for x in victim.iterdir() if x.name.startswith(reg.TEMP_PREFIX)]
        self.assertTrue(kept and (kept[0] / "keep.txt").is_file(), "the delete followed the swapped lane")

    def test_a_lane_replaced_by_another_real_directory_keeps_its_contents(self):
        # K1 (codex, Critical, round 3, reproduced): the cleanup REOPENED the lane — a real directory put in its place,
        # holding a folder of the temp's name, lost that folder's contents
        reg = self.registry_module()

        def render(num, name):
            temp = next(x.name for x in self.agent.iterdir() if x.name.startswith(reg.TEMP_PREFIX))
            self.agent.rename(self.agent.with_name(".agent.orig"))
            (self.agent / temp).mkdir(parents=True)
            (self.agent / temp / "keep.txt").write_text("the owner's data\n", encoding="utf-8")
            raise RuntimeError("the template failed")
        with self.assertRaises(RuntimeError):
            reg.create_task_dir(self.p, self.agent, "x", "new", render)
        kept = [x for x in self.agent.iterdir() if x.name.startswith(reg.TEMP_PREFIX)]
        self.assertTrue(kept and (kept[0] / "keep.txt").is_file(), "the cleanup removed through the replaced lane")
        orig = self.agent.with_name(".agent.orig")
        self.assertFalse([x for x in orig.iterdir() if x.name.startswith(reg.TEMP_PREFIX)],
                         "the creator's own temp was left in the lane it built in")

    def test_a_lane_replaced_during_a_successful_build_keeps_its_own_registry(self):
        # the single judge after round 3 (codex, Critical, reproduced): the build went through the held descriptors but
        # the registry was written BY PATH — into the replacement lane's registry, its row lost, the made task left
        # `reserved` where it really is
        reg = self.registry_module()
        self.cli("new", "quick", "first", check=True)                      # the lane's registry exists
        replacement = self.tmp / "replacement"
        (replacement / "tasks" / "009-theirs").mkdir(parents=True)
        (replacement / "tasks" / "009-theirs" / "task.md").write_text("# 009\n", encoding="utf-8")
        theirs = "# playbook task-registry v1\n009-theirs\tnew\t2026-10-14T00:00:00Z\n"
        (replacement / "task-registry").write_text(theirs, encoding="utf-8")
        orig = self.agent.with_name(".agent.orig")

        def render(num, name):
            self.agent.rename(orig)
            replacement.rename(self.agent)
            return f"# {num:03d} - x\n"
        final = reg.create_task_dir(self.p, self.agent, "x", "new", render)
        self.assertEqual((self.agent / "task-registry").read_text(encoding="utf-8"), theirs,
                         "the replacement lane's registry was overwritten")
        mine = (orig / "task-registry").read_text(encoding="utf-8")
        self.assertIn("002-x\tnew\t", mine, "the made task is not registered where it was made")
        self.assertEqual(final.resolve(), (orig / "tasks" / "002-x").resolve(), "the returned path is not where it is")

    def test_a_second_number_after_a_swap_is_taken_in_the_held_lane(self):
        # the allocator lists the HELD lane too: a collision after the lane was swapped takes the next number of the
        # lane the task is made in, not of the replacement (whose 009 would make it 010)
        reg = self.registry_module()
        self.cli("new", "quick", "first", check=True)
        replacement = self.tmp / "replacement"
        (replacement / "tasks" / "009-theirs").mkdir(parents=True)
        (replacement / "tasks" / "009-theirs" / "task.md").write_text("# 009\n", encoding="utf-8")
        orig = self.agent.with_name(".agent.orig")
        calls = []

        def render(num, name):
            if not calls:
                calls.append(name)
                (self.tasks / name).mkdir()                 # the final name stands: a collision, a second number
                self.agent.rename(orig)
                replacement.rename(self.agent)
            return f"# {num:03d} - x\n"
        final = reg.create_task_dir(self.p, self.agent, "x", "new", render)
        self.assertEqual(calls, ["002-x"])
        self.assertEqual(final.name, "003-x", "the second number was taken from the replacement lane")

    def test_a_linked_lane_is_used_but_never_swept(self):
        # the single judge's run 2 (codex, Critical, reproduced): the lane opened by a plain open followed a linked
        # `.agent`, and the sweep deleted `.task-creating.*` folders in the link's target
        external = self.tmp / "external-lane"
        (external / "tasks").mkdir(parents=True)
        foreign = external / ".task-creating.999.0"
        foreign.mkdir()
        (foreign / "keep.txt").write_text("the owner's data\n", encoding="utf-8")
        import shutil as _sh
        _sh.rmtree(self.agent)
        self.agent.symlink_to(external, target_is_directory=True)
        self.cli("new", "quick", "a", check=True)                   # a linked lane (a shared folder) still works
        self.assertTrue((foreign / "keep.txt").is_file(), "the sweep deleted inside a linked lane's target")
        self.assertTrue((external / "tasks" / "001-a" / "task.md").is_file())

    def test_a_tasks_folder_swapped_during_a_build(self):
        # run 2 (Important, reproduced): the allocator and the returned path resolved `tasks/` by name while the
        # publication used the held descriptor — a swap of `tasks/` alone gave a path that did not exist, and a retry
        # allocated from the replacement
        reg = self.registry_module()
        self.cli("new", "quick", "first", check=True)
        replacement = self.tmp / "replacement-tasks"
        (replacement / "009-theirs").mkdir(parents=True)
        (replacement / "009-theirs" / "task.md").write_text("# 009\n", encoding="utf-8")
        held = self.agent / "tasks.held"
        calls = []

        def render(num, name):
            if not calls:
                calls.append(name)
                (self.tasks / name).mkdir()                 # a collision: a second number is taken
                self.tasks.rename(held)
                replacement.rename(self.tasks)
            return f"# {num:03d} - x\n"
        final = reg.create_task_dir(self.p, self.agent, "x", "new", render)
        self.assertEqual(final.name, "003-x", "the retry allocated from the replacement tasks/")
        self.assertTrue((final / "task.md").is_file(), "the returned path is not where the task is")
        self.assertEqual(final.resolve(), (held / "003-x").resolve())

    def test_a_long_title_is_created(self):
        # J6 (sonnet): the temp name carried the task's name, ~25 bytes longer — ENAMETOOLONG for a 230-byte name
        title = "a" * 230
        r = self.cli("new", "quick", title)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.task_dirs(), [f"001-{title}"])

    def test_a_folder_name_ending_in_a_newline_is_never_enrolled(self):
        # J2 (sonnet, codex): `$` accepts a trailing newline — enrolled, the line split, the registry unreadable
        self.handmade("001-x\n")
        self.cli("new", "quick", "y", check=True)
        self.cli("new", "quick", "z", check=True)                    # still readable
        self.assertNotIn("001-x\n", self.registered())
        r = self.cli("adopt", "001-x\n")
        self.assertNotEqual(r.returncode, 0)

    def test_an_empty_name_list_asks_git_nothing(self):
        # J8 (gemini): names=[] ran `ls-tree` with no pathspec — the whole tree
        subprocess.run(self.git + ["init", "-q"], cwd=self.p, check=True)
        self.handmade("001-a")
        subprocess.run(self.git + ["add", "-A"], cwd=self.p, check=True)
        subprocess.run(self.git + ["commit", "-qm", "a"], cwd=self.p, check=True)
        reg = self.registry_module()
        self.assertEqual(reg._committed_task_mds(self.p, self.agent, []), set())

    def test_prepare_merge_keeps_a_folder_known_only_by_git(self):
        # J3 (gemini, fable, opus): a folder pulled in after the registry existed has no row; after `git mv` its new
        # path is staged, not in HEAD
        subprocess.run(self.git + ["init", "-q"], cwd=self.p, check=True)
        self.cli("new", "quick", "first", check=True)
        self.handmade("005-x")
        subprocess.run(self.git + ["add", "-A"], cwd=self.p, check=True)
        subprocess.run(self.git + ["commit", "-qm", "pulled"], cwd=self.p, check=True)
        self.assertNotIn("005-x", self.registered())
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
                "import tasks.merge_prep as m\n"
                "m._rename_colliding_tasks(Path(sys.argv[2]), Path(sys.argv[2]) / '.agent', {5: '005-x'}, {5: 9})\n")
        r = subprocess.run([sys.executable, "-c", code, str(PLUGIN), str(self.p)], env=self.env(), text=True,
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        reg = self.registry_module()
        self.assertTrue(reg.is_known(self.p, self.agent, "009-x"), "the renamed folder reads unknown")

    def test_a_rename_over_a_reserved_row_of_the_new_name(self):
        # J4 (fable): two rows of one name, the later (reserved) one won
        self.cli("new", "quick", "a", check=True)
        f = self.agent / "task-registry"
        f.write_text(f.read_text(encoding="utf-8") + "007-a\treserved\t2026-10-14T00:00:00Z\n", encoding="utf-8")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
                "import tasks.merge_prep as m\n"
                "m._rename_colliding_tasks(Path(sys.argv[2]), Path(sys.argv[2]) / '.agent', {1: '001-a'}, {1: 7})\n")
        r = subprocess.run([sys.executable, "-c", code, str(PLUGIN), str(self.p)], env=self.env(), text=True,
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = [ln.split("\t")[0] for ln in self.registry_lines()[1:]]
        self.assertEqual(names.count("007-a"), 1, self.registry_lines())
        self.assertEqual(self.registered()["007-a"], "new")

    def test_a_stopped_merge_brings_its_folders_known(self):
        # J5 (opus): the other side's committed folders are in the tree but not in HEAD until the merge commit
        subprocess.run(self.git + ["init", "-q", "-b", "main"], cwd=self.p, check=True)
        (self.p / "notes.md").write_text("base\n", encoding="utf-8")
        subprocess.run(self.git + ["add", "-A"], cwd=self.p, check=True)
        subprocess.run(self.git + ["commit", "-qm", "base"], cwd=self.p, check=True)
        subprocess.run(self.git + ["checkout", "-qb", "other"], cwd=self.p, check=True)
        self.handmade("005-b")
        (self.p / "notes.md").write_text("theirs\n", encoding="utf-8")
        subprocess.run(self.git + ["add", "-A"], cwd=self.p, check=True)
        subprocess.run(self.git + ["commit", "-qm", "theirs"], cwd=self.p, check=True)
        subprocess.run(self.git + ["checkout", "-q", "main"], cwd=self.p, check=True)
        (self.p / "notes.md").write_text("ours\n", encoding="utf-8")
        subprocess.run(self.git + ["commit", "-qam", "ours"], cwd=self.p, check=True)
        subprocess.run(self.git + ["merge", "-q", "other"], cwd=self.p, capture_output=True)   # stops on notes.md
        self.assertTrue((self.p / ".git" / "MERGE_HEAD").exists(), "the merge did not stop: the test proves nothing")
        reg = self.registry_module()
        self.assertTrue(reg.is_known(self.p, self.agent, "005-b"))

    def test_adopt_takes_a_trailing_slash_and_a_tasks_path_but_not_another_lane(self):
        # J7 (gemini): tab completion gives `005-x/` or `.agent/tasks/005-x`
        self.cli("new", "quick", "first", check=True)
        self.handmade("005-x")
        self.handmade("006-y")
        self.cli("adopt", "005-x/", check=True)
        self.cli("adopt", ".agent/tasks/006-y", check=True)
        self.assertEqual(self.registered().get("005-x"), "adopt")
        self.assertEqual(self.registered().get("006-y"), "adopt")
        # a path outside the lane's tasks/ — even when the lane holds a folder of the same name — is refused, said why
        self.handmade("007-z")
        elsewhere = self.tmp / "elsewhere" / "007-z"
        elsewhere.mkdir(parents=True)
        (elsewhere / "task.md").write_text("# 7\n", encoding="utf-8")
        r = self.cli("adopt", str(elsewhere))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("not a folder of this lane", r.stderr)
        self.assertNotIn("007-z", self.registered())

    def test_doctor_does_not_ask_to_adopt_before_a_registry_exists(self):
        # J9 (gemini): INFO "the first writer enrols" followed by a WARN to adopt
        self.handmade("001-by-hand")
        r = self.cli("doctor")
        self.assertIn("no task registry yet", r.stdout)
        self.assertNotIn("folders the task registry does not know", r.stdout)


if __name__ == "__main__":
    unittest.main()
