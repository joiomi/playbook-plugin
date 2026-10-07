"""`tasks work <N>` must be able to re-adopt a finished-but-unclosed task (task 027).

The field report that opened task 027 ended in a state the CLI could not express:
all 41 gates checked, `## Status` still `pending`, and no session pointer (the
SessionStart GC had deleted it). Both documented routes were closed —

  * `tasks work done` reads the pointer; absent → "No active task." and it
    never touches `## Status`;
  * `tasks work 56` was REFUSED, because `_find_active_task` only returns tasks
    that still have unchecked gates and the fallback only re-activated a task
    whose status was already `done` (reopen) or a stub.

The only sanctioned writer of `## Status` needed a pointer, and the only way to
get a pointer was refused, so the pointer had to be hand-written. This module
pins the third fallback arm that closes that hole, plus the branches it must not
disturb.

Everything runs the real CLI as a subprocess: the bug lived in argv dispatch and
process-level state (pointer file, exit code, stderr), which an in-process call
would not reproduce.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"

SESSION_ID = "pid-readopt-test"

# A task.md with the two structural features the arms key on: a `## Status`
# block and gate checkboxes. Gate text is irrelevant; only checked-ness is.
TASK_MD = """# {num} - Readopt Fixture

## Status
{status}

## Intent
Fixture task for the re-adoption arm.

## Work Plan
- [{g1}] first gate
- [{g2}] second gate
"""


def write_task(project: Path, num: str, status: str, gates_checked: bool,
               stub: bool = False) -> Path:
    """Create .agent/tasks/<num>-readopt/task.md and return its path."""
    d = project / ".agent" / "tasks" / f"{num}-readopt"
    d.mkdir(parents=True, exist_ok=True)
    mark = "x" if gates_checked else " "
    body = TASK_MD.format(num=num, status=status, g1=mark, g2=mark)
    if stub:
        body = body.replace("## Intent", "<!-- stub:bugfix -->\n\n## Intent")
    tf = d / "task.md"
    tf.write_text(body, encoding="utf-8")
    return tf


def status_of(task_file: Path) -> str:
    lines = task_file.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Status" and i + 1 < len(lines):
            return lines[i + 1].strip()
    return ""


class WorkReadoptBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def run_tasks(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PLUGIN)
        # Pin the session id so the pointer lands somewhere we can assert on,
        # and so _gc_dead_sessions self-excludes it (a non-numeric name that
        # `kill -0` would reject).
        env["PLAYBOOK_SESSION_ID"] = SESSION_ID
        return subprocess.run(
            [sys.executable, "-m", "tasks.cli", *args],
            cwd=self.project, env=env, capture_output=True, text=True,
        )

    @property
    def pointer(self) -> Path:
        return self.project / ".agent" / "sessions" / SESSION_ID / "current_state"


class TestReadoptFullyGated(WorkReadoptBase):
    """The state the field report got stuck in."""

    def test_pending_fully_gated_task_is_readopted(self):
        tf = write_task(self.project, "056", "pending", gates_checked=True)
        r = self.run_tasks("work", "056")
        self.assertEqual(r.returncode, 0, f"work was refused: {r.stderr or r.stdout}")
        self.assertNotIn("has no open gates", r.stderr)
        self.assertIn("re-adopting", r.stdout)
        self.assertTrue(self.pointer.exists(), "no session pointer was written")
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), "056")
        # Re-adoption must NOT close the task behind the user's back — `work
        # done` stays the only writer of `done` (task 140: activation writes in_progress).
        self.assertEqual(status_of(tf), "in_progress")

    def test_in_progress_fully_gated_task_is_readopted(self):
        """`_is_done` only special-cases `done`, and `_find_active_task` skips
        every fully-gated task regardless of status — so `in_progress` reaches
        the same dead end as `pending` and must recover the same way."""
        tf = write_task(self.project, "057", "in_progress", gates_checked=True)
        r = self.run_tasks("work", "057")
        self.assertEqual(r.returncode, 0, f"work was refused: {r.stderr or r.stdout}")
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), "057")
        self.assertEqual(status_of(tf), "in_progress")

    def test_readopt_then_work_done_closes_the_task(self):
        """The whole point: the recovery has to reach a closed task through the
        CLI, with the CLI writing ## Status."""
        tf = write_task(self.project, "058", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "058").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(status_of(tf), "done")

    def test_readopt_is_idempotent_on_the_already_pointed_task(self):
        """Re-running `work <N>` on the task already in the pointer takes the
        `prev_task == task_num` path, which skips the auto-close block. It must
        not corrupt status or fail."""
        tf = write_task(self.project, "059", "pending", gates_checked=True)
        first = self.run_tasks("work", "059")
        second = self.run_tasks("work", "059")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), "059")
        self.assertEqual(status_of(tf), "in_progress")

    def test_pointer_loss_mid_task_is_recoverable_end_to_end(self):
        """The field scenario in full: activate, lose the pointer the way the GC
        used to lose it, then recover through the CLI alone."""
        tf = write_task(self.project, "060", "pending", gates_checked=False)
        self.assertEqual(self.run_tasks("work", "060").returncode, 0)
        # All gates get checked during the work...
        tf.write_text(tf.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
                      encoding="utf-8")
        # ...and then the sweep deletes the session dir under a live session.
        import shutil
        shutil.rmtree(self.pointer.parent)
        self.assertFalse(self.pointer.exists())

        readopt = self.run_tasks("work", "060")
        self.assertEqual(readopt.returncode, 0,
                         f"the dead end is back: {readopt.stderr or readopt.stdout}")
        done = self.run_tasks("work", "done")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(status_of(tf), "done")


class TestUntouchedBranches(WorkReadoptBase):
    """The new arm is last in the chain; nothing above or below may shift."""

    def test_done_task_still_reopens(self):
        tf = write_task(self.project, "061", "done", gates_checked=True)
        r = self.run_tasks("work", "061", "--reopen")     # task 140: a reopen is asked for
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("reopening", r.stdout)
        self.assertEqual(status_of(tf), "in_progress",
                         "the reopen branch must still rewrite ## Status")

    def test_stub_still_activates(self):
        write_task(self.project, "062", "pending", gates_checked=True, stub=True)
        r = self.run_tasks("work", "062")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("re-adopting", r.stdout,
                         "a stub must take the stub arm, not the re-adoption arm")

    def test_task_with_open_gates_activates_normally(self):
        tf = write_task(self.project, "063", "pending", gates_checked=False)
        r = self.run_tasks("work", "063")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("re-adopting", r.stdout)
        self.assertEqual(status_of(tf), "in_progress")

    def test_missing_task_still_fails(self):
        r = self.run_tasks("work", "099")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)

    def test_work_done_without_a_pointer_still_reports_no_active_task(self):
        """Fix B deliberately does not change this: `work done` still refuses to
        guess which task was active."""
        write_task(self.project, "064", "pending", gates_checked=True)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0)
        self.assertIn("No active task", r.stdout)

    def test_gate_bounce_still_blocks_closing_a_task_with_open_gates(self):
        tf = write_task(self.project, "065", "pending", gates_checked=False)
        self.assertEqual(self.run_tasks("work", "065").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 1, "open gates must still bounce")
        self.assertNotEqual(status_of(tf), "done")


class TestAdjacentBehaviour(WorkReadoptBase):
    """Side effects the re-adoption arm must not disturb."""

    def test_switching_away_from_a_fully_gated_task_bounces_to_work_done(self):
        """POLICY CHANGE (1.5.5, F14 blind-judge Finding 1): this branch used
        to AUTO-CLOSE — writing `done` directly with no risk check, no review
        evidence, no verify contract, no receipt. A policy-free second close
        path defeats the evidence contract, so the switch now bounces to
        `tasks work done` (the only closer, per 1.4.7), and --force switches
        away leaving the task honestly open."""
        prev = write_task(self.project, "070", "pending", gates_checked=True)
        nxt = write_task(self.project, "071", "pending", gates_checked=False)
        self.assertEqual(self.run_tasks("work", "070").returncode, 0)
        r = self.run_tasks("work", "071")
        self.assertEqual(r.returncode, 1,
                         "the policy-free auto-close is back: " + r.stdout)
        self.assertIn("tasks work done", r.stderr + r.stdout)
        self.assertEqual(status_of(prev), "in_progress",
                         "the switch path wrote ## Status again")
        forced = self.run_tasks("work", "071", "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(status_of(prev), "in_progress",
                         "--force must leave the task open, never done")
        self.assertEqual(status_of(nxt), "in_progress")

    def test_status_reports_a_readopted_task(self):
        """`tasks status` reads the pointer, so a re-adopted task must show up
        there — that is how the agent sees it is active again."""
        write_task(self.project, "072", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "072").returncode, 0)
        r = self.run_tasks("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("072", r.stdout)


class TestForceInteraction(unittest.TestCase):
    """`--force` shares `cmd work` with the re-adoption arm.

    Verify B1 listed force as a regression arm and the first suite did not cover
    it (impl panel caught the gap), so a force-interaction bug would have stayed
    green.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    run_tasks = WorkReadoptBase.run_tasks
    pointer = WorkReadoptBase.pointer

    def test_force_switch_from_an_open_task_onto_a_readopt_target(self):
        """Without --force this bounces; with it, the switch lands on the
        fully-gated target through the new arm and leaves the abandoned task
        in_progress rather than silently closing it."""
        open_task = write_task(self.project, "080", "pending", gates_checked=False)
        target = write_task(self.project, "081", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "080").returncode, 0)

        bounced = self.run_tasks("work", "081")
        self.assertEqual(bounced.returncode, 1, "open gates must still bounce without --force")

        forced = self.run_tasks("work", "081", "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertIn("re-adopting", forced.stdout)
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), "081")
        self.assertNotEqual(status_of(open_task), "done",
                            "--force must not silently close the abandoned task")
        self.assertEqual(status_of(target), "in_progress")

    def test_force_work_done_needs_a_reason(self):
        """A forced close must be self-documenting (the 046 fix): --force alone is
        refused; --force --reason lands and records the reason in the receipt."""
        tf = write_task(self.project, "082", "pending", gates_checked=False)
        self.assertEqual(self.run_tasks("work", "082").returncode, 0)

        bare = self.run_tasks("work", "done", "--force")
        self.assertEqual(bare.returncode, 1, "bare --force must be refused")
        self.assertIn("--reason", bare.stderr)
        self.assertNotEqual(status_of(tf), "done")

        ok = self.run_tasks("work", "done", "--force", "--reason", "owner accepts, hotfix")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(status_of(tf), "done")
        self.assertIn("owner accepts, hotfix", tf.read_text(encoding="utf-8"))


class TestEvidenceContract(WorkReadoptBase):
    """The close is earned, not asserted (P1/P2): a declared verify runs, a
    failing one blocks, a passing one leaves a receipt, and an assertive task
    with no review cannot light-close."""

    def _config(self, cfg: dict) -> None:
        import json
        p = self.project / ".agent" / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")

    def _risk_task(self, num: str, risk: str) -> Path:
        d = self.project / ".agent" / "tasks" / f"{num}-risk"
        d.mkdir(parents=True, exist_ok=True)
        tf = d / "task.md"
        tf.write_text(
            f"# {num} - Risk Fixture\n\n## Status\npending\n\n## Risk\n{risk}\n\n"
            "## Work Plan\n- [x] only gate\n", encoding="utf-8")
        return tf

    def test_failing_verify_blocks_close(self):
        self._config({"verify": "exit 7"})
        tf = write_task(self.project, "070", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "070").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("verification failed", r.stderr)
        self.assertNotEqual(status_of(tf), "done")

    def test_failing_verify_overridable_with_reason(self):
        self._config({"verify": "exit 7"})
        tf = write_task(self.project, "071", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "071").returncode, 0)
        r = self.run_tasks("work", "done", "--force", "--reason", "known-flaky, tracked")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(status_of(tf), "done")

    def test_passing_verify_writes_receipt(self):
        self._config({"verify": "echo checks-green"})
        tf = write_task(self.project, "072", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "072").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = tf.read_text(encoding="utf-8")
        self.assertIn("## Verification Receipt", body)
        self.assertIn("[PASS]", body)
        self.assertEqual(status_of(tf), "done")

    def test_assertive_without_review_blocks_then_closes_with_evidence(self):
        tf = self._risk_task("073", "assertive")
        self.assertEqual(self.run_tasks("work", "073").returncode, 0)
        blocked = self.run_tasks("work", "done")
        self.assertEqual(blocked.returncode, 1, blocked.stdout)
        self.assertIn("assertive", blocked.stderr)
        self.assertNotEqual(status_of(tf), "done")
        # A PLAN-phase artifact must NOT satisfy the gate (impl_only, A4)…
        (tf.parent / "judge.md").write_text("# Panel Plan Review — task\n", encoding="utf-8")
        still = self.run_tasks("work", "done")
        self.assertEqual(still.returncode, 1, "plan review must not vouch for what was built")
        # …an IMPL review does.
        (tf.parent / "judge.md").write_text("# Panel Impl Review — task\n", encoding="utf-8")
        ok = self.run_tasks("work", "done")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(status_of(tf), "done")

    def test_dirty_close_warns_and_marks_receipt(self):
        """StrataDB F6 e2e: closing with uncommitted work must warn out loud and
        mark the receipt — a crash between close and commit loses 'done' work."""
        self._git_project()
        tf = write_task(self.project, "077", "pending", gates_checked=True)
        (self.project / "wal.py").write_text("x = 1\n", encoding="utf-8")  # uncommitted work
        self.assertEqual(self.run_tasks("work", "077").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("UNCOMMITTED work", r.stdout)
        self.assertIn("uncommitted file(s)", tf.read_text(encoding="utf-8"))
        # task 128: the task's own records are not counted — only wal.py
        self.assertIn("⚠ 1 modified/untracked file(s)", r.stdout)
        self.assertIn("+1 uncommitted file(s)", tf.read_text(encoding="utf-8"))

    def _git_project(self):
        """A git project with init's runtime-state ignore (session pointers are not
        work), committed — the shape a real `/playbook:init` project has."""
        import subprocess as sp
        sp.run(["git", "init", "-q"], cwd=self.project, check=True)
        (self.project / ".gitignore").write_text(".agent/sessions/\n", encoding="utf-8")
        sp.run(["git", "add", ".gitignore"], cwd=self.project, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-q", "-m", "seed"], cwd=self.project, check=True)

    def test_only_the_tasks_own_records_dirty_means_no_warning(self):
        """Task 128 (gauntlet 2 R20): the count included the task's own task.md and judge
        files, so every close warned (17 of 17 in tasks 091-106) and the warning meant
        nothing. Only paths outside the closing task's directory count."""
        self._git_project()
        tf = write_task(self.project, "078", "pending", gates_checked=True)
        (tf.parent / "judge single.md").write_text("x\n", encoding="utf-8")   # a space in the name too
        self.assertEqual(self.run_tasks("work", "078").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("UNCOMMITTED work", r.stdout)
        self.assertNotIn("uncommitted file(s)", tf.read_text(encoding="utf-8"))

    def test_a_rename_counts_once(self):
        import subprocess as sp
        self._git_project()
        (self.project / "old.py").write_text("x = 1\n", encoding="utf-8")
        sp.run(["git", "add", "old.py"], cwd=self.project, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"],
               cwd=self.project, check=True)
        sp.run(["git", "mv", "old.py", "new.py"], cwd=self.project, check=True)
        tf = write_task(self.project, "080", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "080").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertIn("⚠ 1 modified/untracked file(s)", r.stdout)

    def test_a_rename_into_the_task_dir_still_counts(self):
        """Task 138 G4-1: a tracked file moved INTO the task's directory left the project
        (a deletion outside it) — counting only the destination reported a clean close."""
        import subprocess as sp
        self._git_project()
        (self.project / "notes.py").write_text("x = 1\n", encoding="utf-8")
        sp.run(["git", "add", "notes.py"], cwd=self.project, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"],
               cwd=self.project, check=True)
        tf = write_task(self.project, "081", "pending", gates_checked=True)
        sp.run(["git", "mv", "notes.py", str(tf.parent / "notes.py")], cwd=self.project, check=True)
        self.assertEqual(self.run_tasks("work", "081").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertIn("⚠ 1 modified/untracked file(s)", r.stdout)

    def test_a_dirty_file_with_a_space_outside_the_task_still_counts(self):
        self._git_project()
        tf = write_task(self.project, "079", "pending", gates_checked=True)
        (self.project / "my notes.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.run_tasks("work", "079").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertIn("⚠ 1 modified/untracked file(s)", r.stdout)

    def test_reclose_stacks_entries_under_one_receipt_heading(self):
        """Close → reopen → close must not accrete duplicate `## Verification
        Receipt` headings (A3) — one section, newest entry first."""
        self._config({"verify": "echo checks-green"})
        tf = write_task(self.project, "076", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "076").returncode, 0)
        self.assertEqual(self.run_tasks("work", "done").returncode, 0)
        self.assertEqual(self.run_tasks("work", "076", "--reopen").returncode, 0)  # reopen
        self.assertEqual(self.run_tasks("work", "done").returncode, 0)
        body = tf.read_text(encoding="utf-8")
        self.assertEqual(body.count("## Verification Receipt"), 1, body)
        self.assertEqual(body.count("### "), 2)

    def test_hanging_verify_times_out_and_blocks(self):
        """A verify command that hangs must not hang the close (A1): the ceiling
        kills it, the close is BLOCKED (a verify that cannot finish is not a
        pass), and the output names the timeout."""
        self._config({"verify": "sleep 5", "verify_timeout_secs": 1})
        tf = write_task(self.project, "075", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "075").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("verification failed", r.stderr)
        self.assertIn("FAIL", r.stdout)
        self.assertNotEqual(status_of(tf), "done")

    def test_reversible_no_contract_closes_clean(self):
        tf = self._risk_task("074", "reversible")
        self.assertEqual(self.run_tasks("work", "074").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(status_of(tf), "done")
        self.assertIn("NONE DECLARED", tf.read_text(encoding="utf-8"))


class TestAuditCommand(WorkReadoptBase):
    """`tasks audit` runs sweeps, writes a receipt, and exits non-zero on breakage."""

    def _task(self, num):
        d = self.project / ".agent" / "tasks" / f"{num}-audit"
        d.mkdir(parents=True, exist_ok=True)
        (d / "task.md").write_text(
            f"# {num} - a\n\n## Status\npending\n\n## Work Plan\n- [x] g\n", encoding="utf-8")
        return d / "task.md"

    def test_clean_repo_passes_and_writes_receipt(self):
        tf = self._task("040")
        (self.project / "src.py").write_text("x = 1\n", encoding="utf-8")
        r = self.run_tasks("audit", "040")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AUDIT PASS", r.stdout)
        self.assertIn("## Pre-Panel Audit", tf.read_text(encoding="utf-8"))

    def test_reaudit_stacks_entries_under_one_heading(self):
        tf = self._task("042")
        (self.project / "src.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.run_tasks("audit", "042").returncode, 0)
        self.assertEqual(self.run_tasks("audit", "042").returncode, 0)
        body = tf.read_text(encoding="utf-8")
        self.assertEqual(body.count("## Pre-Panel Audit"), 1, body)
        self.assertEqual(body.count("### "), 2)

    def test_conflict_marker_fails_the_audit(self):
        self._task("041")
        (self.project / "broken.py").write_text(
            "a = 1\n<<<<<<< HEAD\nb = 2\n>>>>>>> feat\n", encoding="utf-8")
        r = self.run_tasks("audit", "041")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FINDINGS", r.stdout)
        self.assertIn("conflict-markers", r.stdout)


class TestGateCountGuard(WorkReadoptBase):
    """Issue #09: a prose `- [ ]` must not inflate the count or block a close, and
    the line-anchored count is an independent guard against closing with open
    gates."""

    def _task(self, num, body):
        d = self.project / ".agent" / "tasks" / f"{num}-gate"
        d.mkdir(parents=True, exist_ok=True)
        tf = d / "task.md"
        tf.write_text(body, encoding="utf-8")
        return tf

    def test_prose_marker_does_not_block_a_complete_task(self):
        tf = self._task("085",
            "# 085 - g\n\n## Status\npending\n\n## Work Plan\n"
            "- [x] real gate one\n- [x] real gate two\n\n"
            "The convention is `- [ ]` until the gate's work lands.\n")
        self.assertEqual(self.run_tasks("work", "085").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0, r.stderr)   # prose no longer counts as a gate
        self.assertEqual(status_of(tf), "done")

    def test_real_open_gate_still_blocks_on_the_count(self):
        self._task("086",
            "# 086 - g\n\n## Status\npending\n\n## Work Plan\n"
            "- [x] done one\n- [ ] genuinely open\n")
        self.assertEqual(self.run_tasks("work", "086").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 1)
        self.assertNotEqual(status_of(self.project / ".agent" / "tasks" / "086-gate" / "task.md"), "done")


class TestParkedAndRetro(WorkReadoptBase):
    """Parked items become un-swallowable and the retro loop gets a trigger."""

    def _parked_task(self, num: str, items, status="pending") -> Path:
        d = self.project / ".agent" / "tasks" / f"{num}-parked"
        d.mkdir(parents=True, exist_ok=True)
        tf = d / "task.md"
        body = (f"# {num} - Parked Fixture\n\n## Status\n{status}\n\n"
                "## Work Plan\n- [x] g\n\n## Parked\n"
                + "\n".join(f"- {it}" for it in items) + "\n")
        tf.write_text(body, encoding="utf-8")
        return tf

    def test_parked_command_lists_open_only(self):
        self._parked_task("090", ["donut collision", "old thing [dismissed: wontfix]"])
        r = self.run_tasks("parked")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("donut collision", r.stdout)
        self.assertNotIn("old thing", r.stdout)  # dismissed hidden without --all

    def test_parked_all_shows_resolved(self):
        self._parked_task("091", ["gone [promoted → 092]"])
        self._parked_task("092", [])      # the target must exist to be resolved (PLAN S4, task 087)
        r = self.run_tasks("parked", "--all")
        self.assertIn("gone", r.stdout)
        # ASCII only: this runner decodes the CLI's UTF-8 output with the locale code
        # page on Windows, so the arrow would not survive (CI 35992879061)
        self.assertIn("  [promoted]", r.stdout)

    def test_close_surfaces_open_parked_items(self):
        self._parked_task("092", ["label vs DOM donut collision"], status="pending")
        self.assertEqual(self.run_tasks("work", "092").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("open parked item", r.stdout)
        self.assertIn("donut collision", r.stdout)

    def test_close_nudges_retro_at_threshold(self):
        # Ten already-closed tasks + one active task to close → the close nudges.
        for i in range(1, 11):
            write_task(self.project, f"{i:03d}", "done", gates_checked=True)
        tf = write_task(self.project, "030", "pending", gates_checked=True)
        self.assertEqual(self.run_tasks("work", "030").returncode, 0)
        r = self.run_tasks("work", "done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("tasks retro", r.stdout)


class TestDirtyOutside(unittest.TestCase):
    """`_dirty_outside` on its own (task 138): the repo shapes the CLI tests above do not
    build — a project in a subdirectory of its repo, and `code_roots` nested checkouts."""

    def setUp(self):
        if str(PLUGIN) not in sys.path:
            sys.path.insert(0, str(PLUGIN))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def git(self, cwd, *args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                       cwd=cwd, check=True, capture_output=True)

    def count(self, project, task_dir):
        from tasks.lifecycle import _dirty_outside
        return _dirty_outside(project, task_dir)

    def test_a_project_in_a_subdirectory_reads_root_relative_paths(self):
        """Measured 2026-10-07 (task 138, rejecting a Critical that said otherwise):
        `git status --porcelain -z` run from a subdirectory prints paths relative to the
        repository ROOT, so they are joined onto `--show-toplevel`."""
        self.git(self.root, "init", "-q")
        project = self.root / "sub"
        own = project / ".agent" / "tasks" / "081-x"
        own.mkdir(parents=True)
        (own / "task.md").write_text("t\n", encoding="utf-8")
        (project / "a.py").write_text("a\n", encoding="utf-8")
        (self.root / "top.txt").write_text("b\n", encoding="utf-8")
        self.assertEqual(self.count(project, own), 2)        # a.py and top.txt, not task.md

    def test_dirty_files_in_a_code_root_count(self):
        """Task 138 G4-2: verified code in a `code_roots` checkout (gitignored by the outer
        repo) is the code a close most needs to warn about; the outer status cannot see it."""
        self.git(self.root, "init", "-q")
        (self.root / ".gitignore").write_text("nested/\n.agent/\n", encoding="utf-8")
        self.git(self.root, "add", ".gitignore")
        self.git(self.root, "commit", "-q", "-m", "seed")
        (self.root / ".agent").mkdir()
        (self.root / ".agent" / "config.json").write_text('{"code_roots": ["nested"]}', encoding="utf-8")
        own = self.root / ".agent" / "tasks" / "081-x"
        own.mkdir(parents=True)
        nested = self.root / "nested"
        nested.mkdir()
        self.git(nested, "init", "-q")
        (nested / "seed.py").write_text("s\n", encoding="utf-8")
        self.git(nested, "add", "seed.py")
        self.git(nested, "commit", "-q", "-m", "seed")
        self.assertEqual(self.count(self.root, own), 0)
        (nested / "seed.py").write_text("changed\n", encoding="utf-8")
        (nested / "new.py").write_text("n\n", encoding="utf-8")
        self.assertEqual(self.count(self.root, own), 2)

    def test_a_code_root_git_cannot_answer_for_is_skipped(self):
        # Task 139 (opus Minor): an empty `--show-toplevel` became Path("") — the cwd — and
        # the cwd's repo was counted
        from unittest import mock
        from tasks import lifecycle
        self.git(self.root, "init", "-q")
        (self.root / ".agent").mkdir()
        (self.root / ".agent" / "config.json").write_text('{"code_roots": ["gone"]}', encoding="utf-8")
        (self.root / ".gitignore").write_text(".agent/\n", encoding="utf-8")
        own = self.root / ".agent" / "tasks" / "081-x"
        own.mkdir(parents=True)
        real = lifecycle._git_out
        fake = lambda cwd, *a: "" if Path(cwd).name == "gone" else real(cwd, *a)
        other = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(other, True))
        self.git(other, "init", "-q")
        (other / "noise.py").write_text("x\n", encoding="utf-8")
        prev = os.getcwd()
        os.chdir(other)
        self.addCleanup(os.chdir, prev)
        with mock.patch.object(lifecycle, "_git_out", fake):
            self.assertEqual(self.count(self.root, own), 1)            # .gitignore only

    def test_a_code_root_that_is_not_its_own_repo_is_not_counted_twice(self):
        """A `code_roots` entry that is just a directory of the outer repo: its files are
        already in the outer status — counting them again would double the number."""
        self.git(self.root, "init", "-q")
        (self.root / ".agent").mkdir()
        (self.root / ".agent" / "config.json").write_text('{"code_roots": ["plain"]}', encoding="utf-8")
        (self.root / ".gitignore").write_text(".agent/\n", encoding="utf-8")
        own = self.root / ".agent" / "tasks" / "081-x"
        own.mkdir(parents=True)
        (self.root / "plain").mkdir()
        (self.root / "plain" / "a.py").write_text("a\n", encoding="utf-8")
        self.assertEqual(self.count(self.root, own), 2)       # .gitignore + plain/a.py


if __name__ == "__main__":
    unittest.main()
