"""Task 140: four owner decisions of 2026-10-07 on the tasks CLI.

  * Q6  — `tasks work <N>` on a DONE task reopened it silently (status back from `done`, the
    close receipt still in place). It is refused now; `tasks work <N> --reopen` reopens.
  * Q3b — a bare `tasks models` ran the live check (a real CLI launch, ~5 s). It prints usage;
    the probe is asked for by name (`tasks models check`).
  * Q3c — `tasks blocked "<new>"` on an already-blocked task replaced the recorded reason. The
    new reason is added under the old one.
  * Q10 — an activated task's `## Status` stayed `pending` until its close. `tasks work <N>`
    writes `in_progress`.

Every case runs the real CLI as a subprocess.

Run: python3 -m unittest tests.test_owner_cli_decisions
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
SESSION_ID = "pid-owner-cli-test"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))       # the in-process tests import the command modules

TASK_MD = """# {num} - Fixture

## Status
{status}

## Risk
reversible

## Intent
Fixture.

## Work Plan
- [{mark}] the only gate
"""


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        (self.project / ".agent" / "tasks").mkdir(parents=True)

    def task(self, num: str, status: str = "pending", done_gates: bool = False) -> Path:
        d = self.project / ".agent" / "tasks" / f"{num}-fixture"
        d.mkdir(parents=True)
        tf = d / "task.md"
        tf.write_text(TASK_MD.format(num=num, status=status, mark="x" if done_gates else " "),
                      encoding="utf-8")
        return tf

    def run_tasks(self, *args: str, path_prefix: str = "") -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID=SESSION_ID)
        env.pop("BASH_ENV", None)
        if path_prefix:
            env["PATH"] = path_prefix + os.pathsep + env.get("PATH", "")
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project,
                              env=env, capture_output=True, text=True, timeout=120)

    def status(self, tf: Path) -> str:
        return tf.read_text(encoding="utf-8").split("## Status\n", 1)[1].split("\n", 1)[0]

    def pointer(self) -> Path:
        return self.project / ".agent" / "sessions" / SESSION_ID / "current_state"


class ReopenIsExplicit(_Project):
    def test_work_on_a_done_task_is_refused_and_changes_nothing(self):
        tf = self.task("001", status="done", done_gates=True)
        dead = self.project / ".agent" / "sessions" / "pid-999995"
        dead.mkdir(parents=True)
        (dead / "current_state").write_text("001\n", encoding="utf-8")
        before = tf.read_bytes()
        r = self.run_tasks("work", "001")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("--reopen", r.stderr)
        self.assertIn("Nothing changed", r.stderr)
        self.assertEqual(tf.read_bytes(), before)
        self.assertFalse(self.pointer().exists(), "a refused activation left a pointer")
        self.assertTrue(dead.is_dir(), "the refusal ran after the session GC")

    def test_the_command_function_refuses_it_too(self):
        """The backstop for a caller that imports `cmd_work` (it never passes `cli.main`)."""
        import contextlib
        import io
        from unittest import mock
        if str(PLUGIN) not in sys.path:
            sys.path.insert(0, str(PLUGIN))
        from tasks.lifecycle import cmd_work
        tf = self.task("001", status="done", done_gates=True)
        before = tf.read_bytes()
        prev = os.getcwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, prev)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"PLAYBOOK_SESSION_ID": SESSION_ID}), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as cm:
            cmd_work(["001"])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("--reopen", err.getvalue())
        self.assertEqual(tf.read_bytes(), before)

    def test_reopen_reopens(self):
        tf = self.task("001", status="done", done_gates=False)
        r = self.run_tasks("work", "001", "--reopen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("reopening", r.stdout)
        self.assertEqual(self.status(tf), "in_progress")
        self.assertEqual(self.pointer().read_text(encoding="utf-8").strip(), "001")

    def test_reopen_on_a_task_that_is_not_done_just_activates_it(self):
        tf = self.task("002")
        r = self.run_tasks("work", "002", "--reopen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("reopening", r.stdout)
        self.assertEqual(self.status(tf), "in_progress")

    def test_an_unknown_option_is_still_refused(self):
        self.task("002")
        r = self.run_tasks("work", "002", "--reopne")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--reopne", r.stderr)
        self.assertFalse(self.pointer().exists())


class BareModelsPrintsUsage(_Project):
    def _fake_bin(self) -> Path:
        """A directory of fake provider CLIs that leave a marker when launched."""
        d = self.project / "fakebin"
        d.mkdir()
        for name in ("claude", "codex", "agy", "grok"):
            f = d / name
            f.write_text(f'#!/bin/sh\necho launched >> "{self.project}/LAUNCHED"\nexit 0\n', encoding="utf-8")
            f.chmod(f.stat().st_mode | stat.S_IXUSR)
        return d

    def test_bare_models_prints_usage_and_launches_nothing(self):
        fake = self._fake_bin()
        r = self.run_tasks("models", path_prefix=str(fake))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for word in ("check", "detect", "set", "select"):
            self.assertIn(f"tasks models {word}", r.stdout)
        self.assertFalse((self.project / "LAUNCHED").exists(), "a bare `tasks models` launched a CLI")

    def test_flags_without_a_subcommand_are_refused(self):
        r = self.run_tasks("models", "--no-probe")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tasks models check", r.stderr)

    def test_check_by_name_still_runs(self):
        r = self.run_tasks("models", "check", "--no-probe")
        self.assertNotIn("Usage: tasks models", r.stdout + r.stderr)


class BlockedTwiceKeepsBothReasons(_Project):
    def test_a_second_reason_is_added_under_the_first(self):
        tf = self.task("003")
        self.assertEqual(self.run_tasks("work", "003").returncode, 0)
        self.assertEqual(self.run_tasks("blocked", "waiting for the owner on Q1").returncode, 0)
        r = self.run_tasks("blocked", "and the grok credit is out")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = tf.read_text(encoding="utf-8")
        self.assertEqual(self.status(tf), "blocked")
        self.assertEqual(text.count("## Blocked"), 1)
        first, second = text.index("> waiting for the owner on Q1"), text.index("> and the grok credit is out")
        self.assertLess(first, second)

    def test_after_a_resume_a_new_block_starts_a_new_record(self):
        # unchanged: the old pause was resolved — its reason is not a reason of the new one
        tf = self.task("003")
        self.assertEqual(self.run_tasks("work", "003").returncode, 0)
        self.assertEqual(self.run_tasks("blocked", "first pause").returncode, 0)
        self.assertEqual(self.run_tasks("work", "003").returncode, 0)        # resume
        self.assertEqual(self.run_tasks("blocked", "second pause").returncode, 0)
        text = tf.read_text(encoding="utf-8")
        self.assertIn("> second pause", text)
        self.assertNotIn("> first pause", text)


class ActivationWritesInProgress(_Project):
    def test_work_writes_in_progress_and_the_close_still_works(self):
        tf = self.task("004", done_gates=True)
        (self.project / ".agent" / "tasks" / "005-open").mkdir()
        other = self.project / ".agent" / "tasks" / "005-open" / "task.md"
        other.write_text(TASK_MD.format(num="005", status="pending", mark=" "), encoding="utf-8")
        self.assertEqual(self.run_tasks("work", "005").returncode, 0)
        self.assertEqual(self.status(other), "in_progress")
        self.assertEqual(self.status(tf), "pending")                         # never activated
        self.assertEqual(self.run_tasks("work", "004", "--force").returncode, 0)
        self.assertEqual(self.status(tf), "in_progress")
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.status(tf), "done")

    def test_a_stub_is_in_progress_after_its_expansion(self):
        r = self.run_tasks("new", "--stub", "light", "later", "--", "do", "it", "later")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (tf,) = (self.project / ".agent" / "tasks").glob("*-later/task.md")
        self.assertEqual(self.status(tf), "pending")
        self.assertEqual(self.run_tasks("work", tf.parent.name.split("-")[0]).returncode, 0)
        self.assertNotIn("<!-- stub:", tf.read_text(encoding="utf-8"))
        self.assertEqual(self.status(tf), "in_progress")

    def test_retro_still_names_an_activated_task_that_was_never_closed(self):
        tf = self.task("006", done_gates=True)
        self.assertEqual(self.run_tasks("work", "006").returncode, 0)
        self.assertEqual(self.status(tf), "in_progress")
        r = self.run_tasks("retro", "--since", "0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        made = sorted((self.project / ".agent" / "tasks").glob("*-retro*/task.md"))
        self.assertTrue(made, r.stdout)
        self.assertIn("done but not closed", made[-1].read_text(encoding="utf-8"))


class ImplPanelRound1(_Project):
    """Task 140, implementation panel round 1 (opus, sonnet, codex-high, codex-medium)."""

    def _core(self):
        if str(PLUGIN) not in sys.path:
            sys.path.insert(0, str(PLUGIN))
        from tasks import core
        return core

    def test_the_activation_write_only_ever_moves_pending(self):
        # F1 (4 judges): `pending` was read OUTSIDE the task lock and `in_progress` written
        # whatever was there by then — a close or a pause committed in between was undone.
        # The decision is inside the locked transform now: any other status is left alone.
        core = self._core()
        for i, status in enumerate(("done", "done (forced)", "blocked", "in_progress")):
            tf = self.task(f"01{i}", status=status)
            before = tf.read_bytes()
            self.assertFalse(core.mark_in_progress(tf), status)
            self.assertEqual(tf.read_bytes(), before, status)
        tf = self.task("030")
        self.assertTrue(core.mark_in_progress(tf))
        self.assertEqual(self.status(tf), "in_progress")

    def test_the_reopen_write_only_ever_moves_done(self):
        core = self._core()
        for i, status in enumerate(("pending", "blocked", "in_progress")):
            tf = self.task(f"04{i}", status=status)
            before = tf.read_bytes()
            self.assertFalse(core.reopen_done_task(tf), status)
            self.assertEqual(tf.read_bytes(), before, status)

    def test_a_refused_switch_leaves_the_done_task_done(self):
        # F2 (3 judges): --reopen wrote in_progress BEFORE the check that refuses to leave an
        # active task with open gates — the command failed and the task stayed reopened
        done = self.task("001", status="done", done_gates=True)
        self.task("007")
        self.assertEqual(self.run_tasks("work", "007").returncode, 0)
        before = done.read_bytes()
        r = self.run_tasks("work", "001", "--reopen")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(done.read_bytes(), before, "a refused activation reopened the task")
        self.assertEqual(self.pointer().read_text(encoding="utf-8").strip(), "007")

    def test_a_refused_switch_leaves_the_blocked_task_blocked(self):
        blocked = self.task("008")
        self.assertEqual(self.run_tasks("work", "008").returncode, 0)
        self.assertEqual(self.run_tasks("blocked", "waiting").returncode, 0)
        self.task("007")
        self.assertEqual(self.run_tasks("work", "007", "--force").returncode, 0)
        before = blocked.read_bytes()
        r = self.run_tasks("work", "008")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(blocked.read_bytes(), before, "a refused activation resumed the task")
        r = self.run_tasks("work", "008", "--force")              # the control: it does resume
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.status(blocked), "in_progress")

    def test_a_done_task_named_by_its_folder_is_refused_and_reopened_the_same_way(self):
        # F3 (codex-high): `tasks work 001-fixture --reopen` said "not found"
        tf = self.task("001", status="done", done_gates=False)
        r = self.run_tasks("work", "001-fixture")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--reopen", r.stderr)
        self.assertEqual(self.status(tf), "done")
        r = self.run_tasks("work", "001-fixture", "--reopen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.status(tf), "in_progress")

    def test_the_retro_prints_the_status_a_task_has(self):
        # F4 (codex-high): an activated task was listed as "pending … (not started)"
        self.task("006")
        self.assertEqual(self.run_tasks("work", "006").returncode, 0)
        self.task("009")                                          # never activated
        self.assertEqual(self.run_tasks("retro", "--since", "0").returncode, 0)
        made = sorted((self.project / ".agent" / "tasks").glob("*-retro*/task.md"))
        text = made[-1].read_text(encoding="utf-8")
        line6 = next(l for l in text.splitlines() if l.startswith("- T006"))
        line9 = next(l for l in text.splitlines() if l.startswith("- T009"))
        self.assertIn("in_progress", line6)
        self.assertNotIn("not started", line6)
        self.assertIn("pending", line9)
        self.assertIn("not started", line9)

    def test_a_handoff_on_a_blocked_task_is_still_found(self):
        # F5 (opus): `handoff` was appended UNDER the first reason and the reader took only
        # the first line, so bootstrap stopped showing that handoff
        core = self._core()
        self.task("003")
        self.assertEqual(self.run_tasks("work", "003").returncode, 0)
        self.assertEqual(self.run_tasks("blocked", "waiting for the owner").returncode, 0)
        r = self.run_tasks("handoff")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        found = core.find_unconsumed_handoff(self.project)
        self.assertIsNotNone(found)
        self.assertEqual(found[0], 3)


class ImplPanelRound2(_Project):
    """Task 140, implementation panel round 2 (opus, codex-high, codex-medium)."""

    def _in_process(self, args, patches=()):
        import contextlib
        import io
        from unittest import mock
        if str(PLUGIN) not in sys.path:
            sys.path.insert(0, str(PLUGIN))
        from tasks.lifecycle import cmd_work
        prev = os.getcwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, prev)
        out, err, code = io.StringIO(), io.StringIO(), 0
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.dict(os.environ, {"PLAYBOOK_SESSION_ID": SESSION_ID}))
            for pt in patches:
                st.enter_context(pt)
            st.enter_context(contextlib.redirect_stdout(out))
            st.enter_context(contextlib.redirect_stderr(err))
            try:
                cmd_work(list(args))
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def test_a_name_that_matches_several_tasks_is_refused_not_guessed(self):
        # R2-1 (opus): `_named[0]` — the first folder whose name CONTAINS the word — was
        # reopened or resumed; with two matches that is somebody else's task
        a = self.task("001", status="done", done_gates=True)                 # 001-fixture
        d = self.project / ".agent" / "tasks" / "002-fixture-two"
        d.mkdir()
        b = d / "task.md"
        b.write_text(TASK_MD.format(num="002", status="done", mark="x"), encoding="utf-8")
        before = (a.read_bytes(), b.read_bytes())
        r = self.run_tasks("work", "fixture", "--reopen")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("001-fixture", r.stderr)
        self.assertIn("002-fixture-two", r.stderr)
        self.assertEqual((a.read_bytes(), b.read_bytes()), before)
        # the whole folder name is not ambiguous, although it is a prefix of the other
        r = self.run_tasks("work", "001-fixture", "--reopen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.status(a), "in_progress")
        self.assertEqual(self.status(b), "done")

    def test_a_task_closed_or_paused_during_the_activation_is_not_activated(self):
        # R2-2 (codex-high, codex-medium): the status write could decline (the task moved to
        # done/blocked after the lookup) and the activation still published its pointer
        from unittest import mock
        from tasks import core
        for num, moved_to in (("011", "done"), ("012", "blocked")):
            tf = self.task(num)
            real = core._find_active_task

            def lookup_then_another_session_moves_it(project, name="", _tf=tf, _to=moved_to, _real=real):
                found = _real(project, name)
                _tf.write_text(_tf.read_text(encoding="utf-8").replace("## Status\npending", "## Status\n" + _to),
                               encoding="utf-8")
                return found
            code, out, err = self._in_process(
                [num], [mock.patch.object(core, "_find_active_task", lookup_then_another_session_moves_it)])
            self.assertNotEqual(code, 0, (moved_to, out))
            self.assertIn(moved_to, err)
            self.assertEqual(self.status(tf), moved_to)
            self.assertFalse(self.pointer().exists(), f"a pointer to a {moved_to} task was published")
            # refused AFTER the pointer was prepared: the prepared copy is removed
            self.assertEqual(list((self.project / ".agent" / "sessions").rglob("*.tmp")), [], moved_to)

    def test_a_pointer_that_cannot_be_prepared_stops_the_activation_before_any_write(self):
        # R2-3 (codex-medium) and post-D6 run 1 (codex): the first fix reopened, failed on the
        # pointer and then UNDID the reopen — incomplete (a pending or a resumed task was left
        # changed) and unsafe (it could close a task another session had activated meanwhile).
        # The pointer file is prepared BEFORE task.md is touched and only renamed into place
        # after; nothing is ever undone.
        from unittest import mock
        from tasks import lifecycle

        def no_space(*a, **kw):
            raise OSError(28, "No space left on device")
        done = self.task("001", status="done (forced: owner ok)", done_gates=False)
        pending = self.task("002")
        blocked = self.task("003")
        self.assertEqual(self.run_tasks("work", "003").returncode, 0)
        self.assertEqual(self.run_tasks("blocked", "waiting").returncode, 0)
        self.pointer().unlink()
        for tf, args in ((done, ["001", "--reopen"]), (pending, ["002"]), (blocked, ["003"])):
            before = tf.read_bytes()
            code, out, err = self._in_process(args, [mock.patch.object(lifecycle, "_prepare_pointer", no_space)])
            self.assertNotEqual(code, 0, (args, out))
            self.assertIn("Nothing changed", err, args)
            self.assertEqual(tf.read_bytes(), before, args)
            self.assertFalse(self.pointer().exists(), args)
        self.assertEqual(list((self.project / ".agent" / "sessions").rglob("*.tmp")), [])

    def test_a_refused_activation_leaves_no_prepared_pointer_behind(self):
        self.task("001", status="done", done_gates=True)
        code, _out, _err = self._in_process(["001"])                  # refused: no --reopen
        self.assertNotEqual(code, 0)
        sessions = self.project / ".agent" / "sessions"
        self.assertEqual(list(sessions.rglob("*.tmp")) if sessions.exists() else [], [])


class PostD6Run1(_Project):
    def test_a_whole_folder_name_wins_over_an_open_task_that_contains_it(self):
        # codex: `tasks work 001-foo --reopen` activated the OPEN task `002-001-foo-more`
        # (the open-task search is by substring and ran first), and the done one stayed done
        d1 = self.project / ".agent" / "tasks" / "001-foo"
        d2 = self.project / ".agent" / "tasks" / "002-001-foo-more"
        for d, status, mark in ((d1, "done", "x"), (d2, "pending", " ")):
            d.mkdir(parents=True)
            (d / "task.md").write_text(TASK_MD.format(num=d.name[:3], status=status, mark=mark), encoding="utf-8")
        r = self.run_tasks("work", "001-foo")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("--reopen", r.stderr)
        self.assertFalse(self.pointer().exists())
        r = self.run_tasks("work", "001-foo", "--reopen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.pointer().read_text(encoding="utf-8").strip(), "001")
        self.assertEqual(self.status(d1 / "task.md"), "in_progress")
        self.assertEqual(self.status(d2 / "task.md"), "pending")

class PostD6Run2(_Project):
    def test_a_folder_named_exactly_is_never_swapped_for_its_numbers_twin(self):
        # codex: with `001-a` open and `001-b` done, `tasks work 001-b --reopen` became
        # "001", activated `001-a` and left `001-b` done. The session pointer holds a NUMBER,
        # so two folders with one number cannot be told apart: refused, both named.
        d1 = self.project / ".agent" / "tasks" / "001-a"
        d2 = self.project / ".agent" / "tasks" / "001-b"
        for d, status, mark in ((d1, "pending", " "), (d2, "done", "x")):
            d.mkdir(parents=True)
            (d / "task.md").write_text(TASK_MD.format(num="001", status=status, mark=mark), encoding="utf-8")
        before = ((d1 / "task.md").read_bytes(), (d2 / "task.md").read_bytes())
        for args in (("work", "001-b", "--reopen"), ("work", "001-a")):
            r = self.run_tasks(*args)
            self.assertNotEqual(r.returncode, 0, (args, r.stdout))
            self.assertIn("001-a", r.stderr, args)
            self.assertIn("001-b", r.stderr, args)
            self.assertIn("Nothing changed", r.stderr, args)
        self.assertEqual(((d1 / "task.md").read_bytes(), (d2 / "task.md").read_bytes()), before)
        self.assertFalse(self.pointer().exists())


if __name__ == "__main__":
    unittest.main()
