#!/usr/bin/env python3
"""PB-STALE-CLOSE-BOUND (PLAN S12b, task 149): a stale-panel close needs a post-D6
PASS bound to the tree being closed, or the owner's recorded `--owner-ok`.

The owner's D6-amended rule (CLAUDE.md, 2026-09-25) allows `--stale-panel-ok`
only after the post-D6 single judge's PASS; the CLI asked for nothing but a
reason, and the run records did not say which tree the PASS reviewed (task 146
F20). Where `.agent/config.json` opts in, the close now checks it. Closes run as
real subprocesses (tests/test_tail_certification.py's fixture shape).

Run: python3 -m unittest tests.test_stale_close_bound
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))

from tasks import post_d6  # noqa: E402
from tasks.core import (  # noqa: E402
    build_panel_snapshot, config_sha, format_panel_snapshot_line, tree_state_fingerprint)

TODAY = dt.date.today().isoformat()


def _git(d, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=d, check=True, capture_output=True)


class _BoundFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = self.d = Path(self._tmp.name) / "proj"
        d.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        (d / "code.py").write_text("x = 1\n", encoding="utf-8")
        (d / "tests").mkdir()
        (d / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
        (d / "MIND_MAP.md").write_text("# map\n", encoding="utf-8")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "seed")
        self.cfg = {"panel_required_for": "all", "stale_panel_requires_post_d6": TODAY}
        self._write_cfg()
        self.td = d / ".agent" / "tasks" / "001-t"
        self.td.mkdir(parents=True)
        (self.td / "task.md").write_text(
            "# 001 - T\n\n## Status\npending\n\n## Risk\nassertive\n\n"
            "## Work Plan\n- [x] G1: do it\n", encoding="utf-8")
        self.env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="pid-t149")
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "1"], cwd=d, env=self.env,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        # the impl panel: a stamped PASS round + its reservation record (today)
        fp = tree_state_fingerprint(d)
        (self.td / "judge.md").write_text(
            "# Panel Impl Review — task 1\n\n**PANEL VERDICT: PASS** — 4/4, quorum 3\n\n"
            f"**Round-id:** aaaaaaaaaaaa\n\n**Tree-state:** {fp}\n\n"
            f"{format_panel_snapshot_line(build_panel_snapshot(d, fp))}\n\nbody\n", encoding="utf-8")
        # since task 149 a round's Round-id IS its panel reservation id
        post_d6.append_run(self.td, {"id": "aaaaaaaaaaaa", "kind": "panel", "status": "reserved"})
        post_d6.append_run(self.td, {"id": "aaaaaaaaaaaa", "kind": "panel", "status": "done"})
        self.panel = post_d6.panel_key(post_d6.newest_impl_round(self.td))
        assert self.panel, "fixture: the round has no panel key"
        # a code change after the panel → the panel is stale
        (d / "code.py").write_text("x = 2\n", encoding="utf-8")

    def _write_cfg(self):
        (self.d / ".agent").mkdir(exist_ok=True)
        (self.d / ".agent" / "config.json").write_text(json.dumps(self.cfg), encoding="utf-8")

    def _post_d6(self, verdict="PASS", snapshot=True):
        """A post-D6 run on the CURRENT tree, as review.py records it."""
        fp = tree_state_fingerprint(self.d)
        post_d6.append_run(self.td, {
            "id": f"run{verdict}", "panel": self.panel, "status": "done", "verdict": verdict,
            "critical": 0, "important": 0 if verdict == "PASS" else 1,
            "reviewed_snapshot": build_panel_snapshot(self.d, fp) if snapshot else None,
            "config_sha": config_sha(self.d)})

    def _close(self, *flags):
        return subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done", *flags],
                              cwd=self.d, env=self.env, capture_output=True, text=True, timeout=120)

    def _done(self, r):
        return "Task 001 done." in r.stdout

    def _receipt(self):
        return (self.td / "task.md").read_text(encoding="utf-8")


class StaleCloseBound(_BoundFixture):
    def test_a_bare_stale_panel_ok_is_refused(self):
        r = self._close("--stale-panel-ok", "--reason", "only docs changed")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("no post-D6 run follows the newest impl panel", r.stderr)

    def test_force_is_refused_too(self):
        r = self._close("--force", "--reason", "just close it")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("no post-D6 run follows", r.stderr)

    def test_a_failing_post_d6_run_is_refused(self):
        self._post_d6("FAIL")
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r))
        self.assertIn("is FAIL — a stale close needs a PASS", r.stderr)

    def test_a_pass_without_a_recorded_tree_is_refused(self):
        self._post_d6("PASS", snapshot=False)
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r))
        self.assertIn("recorded no reviewed tree", r.stderr)

    def test_a_bound_pass_closes_with_records_only_drift(self):
        self._post_d6("PASS")
        # after the PASS: a mind-map edit and an `.agent/` record — allowed drift
        (self.d / "MIND_MAP.md").write_text("# map\n[1] node\n", encoding="utf-8")
        (self.td / "notes.md").write_text("triage\n", encoding="utf-8")
        r = self._close("--stale-panel-ok", "--reason", "post-D6 PASS")
        self.assertTrue(self._done(r), r.stdout + r.stderr)
        self.assertIn("bound to post-D6 PASS runPASS", self._receipt())

    def test_a_test_file_change_after_the_pass_is_refused(self):
        self._post_d6("PASS")
        (self.d / "tests" / "test_x.py").write_text("# changed after the PASS\n", encoding="utf-8")
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r))
        self.assertIn("changed since the post-D6 PASS (runPASS): tests/test_x.py", r.stderr)

    def test_a_code_change_after_the_pass_is_refused(self):
        self._post_d6("PASS")
        (self.d / "code.py").write_text("x = 3\n", encoding="utf-8")
        r = self._close("--force", "--reason", "x")
        self.assertFalse(self._done(r))
        self.assertIn("code.py", r.stderr)

    def test_a_config_change_after_the_pass_is_refused(self):
        self._post_d6("PASS")
        self.cfg["verify"] = "true"
        self._write_cfg()
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r))
        self.assertIn(".agent/config.json changed after the post-D6 PASS", r.stderr)

    def test_owner_ok_is_recorded(self):
        r = self._close("--owner-ok", "--reason", "owner ruled 2026-10-08: close")
        self.assertTrue(self._done(r), r.stdout + r.stderr)
        self.assertIn('owner-ok: "owner ruled 2026-10-08: close"', self._receipt())
        journal = "\n".join(p.read_text(encoding="utf-8")
                            for p in (self.d / ".agent").rglob("enforcement*.jsonl"))
        self.assertIn("stale close owner-ok", journal)

    def test_a_task_whose_panel_predates_the_opt_in_keeps_the_old_rule(self):
        # Negative control: the same bare --stale-panel-ok closes when the
        # opt-in date is after the task's panel.
        self.cfg["stale_panel_requires_post_d6"] = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        self._write_cfg()
        r = self._close("--stale-panel-ok", "--reason", "old rule")
        self.assertTrue(self._done(r), r.stdout + r.stderr)

    def test_without_the_opt_in_nothing_changes(self):
        del self.cfg["stale_panel_requires_post_d6"]
        self._write_cfg()
        r = self._close("--stale-panel-ok", "--reason", "no opt-in")
        self.assertTrue(self._done(r), r.stdout + r.stderr)


class StaleCloseBoundEdges(_BoundFixture):
    """Impl panel r1 (task 149) findings, each a close that used to slip through
    or a close that was refused for the wrong reason."""

    def _ledger_lines(self, *records):
        with open(self.td / post_d6.RUNS_NAME, "a", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    def _reset_ledger(self, *records):
        (self.td / post_d6.RUNS_NAME).write_text("", encoding="utf-8")
        self._ledger_lines(*records)

    def test_an_unclassified_task_is_held_to_the_same_bar(self):
        tf = self.td / "task.md"
        tf.write_text(tf.read_text(encoding="utf-8").replace("## Risk\nassertive", "## Risk\nunclassified"),
                      encoding="utf-8")
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("no post-D6 run follows", r.stderr)

    def test_a_degraded_tamper_guard_round_is_held_too(self):
        jm = self.td / "judge.md"
        jm.write_text(jm.read_text(encoding="utf-8").replace(
            "**Tree-state:**", "**Tamper guard:** degraded — could not enumerate dirty files\n\n**Tree-state:**"),
            encoding="utf-8")
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r), r.stdout + r.stderr)
        self.assertIn("no post-D6 run follows", r.stderr)

    def test_owner_ok_closes_a_task_from_before_the_opt_in(self):
        self.cfg["stale_panel_requires_post_d6"] = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        self._write_cfg()
        r = self._close("--owner-ok", "--reason", "owner: close")
        self.assertTrue(self._done(r), r.stdout + r.stderr)

    def test_removing_the_opt_in_after_the_panel_does_not_free_the_task(self):
        self._reset_ledger({"id": "aaaaaaaaaaaa", "kind": "panel", "status": "reserved", "mode": "impl",
                            "bound_since": TODAY, "ts": dt.datetime.now(dt.timezone.utc).isoformat()})
        del self.cfg["stale_panel_requires_post_d6"]
        self._write_cfg()
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("no post-D6 run follows", r.stderr)

    def test_a_plan_panel_after_the_opt_in_does_not_pull_in_an_older_impl_panel(self):
        yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._reset_ledger({"id": "aaaaaaaaaaaa", "kind": "panel", "status": "reserved", "mode": "impl", "ts": yesterday},
                           {"id": "p1", "kind": "panel", "status": "reserved", "mode": "plan", "ts": now})
        r = self._close("--stale-panel-ok", "--reason", "old rule")
        self.assertTrue(self._done(r), r.stdout + r.stderr)

    def test_a_panel_reserved_before_the_date_and_finished_after_keeps_the_old_rule(self):
        yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._reset_ledger({"id": "aaaaaaaaaaaa", "kind": "panel", "status": "reserved", "ts": yesterday},
                           {"id": "aaaaaaaaaaaa", "kind": "panel", "status": "done", "ts": now})
        r = self._close("--stale-panel-ok", "--reason", "old rule")
        self.assertTrue(self._done(r), r.stdout + r.stderr)

    def test_an_unfinished_newer_run_blocks_an_older_pass(self):
        self._post_d6("PASS")
        self._ledger_lines({"id": "r9", "panel": self.panel, "status": "reserved",
                            "ts": dt.datetime.now(dt.timezone.utc).isoformat()})
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("has not finished", r.stderr)

    def test_moving_the_opt_in_date_later_after_the_panel_does_not_free_the_task(self):
        # r2 (opus, codex ×2): the earliest of the config's date and the panel's
        self._reset_ledger({"id": "aaaaaaaaaaaa", "kind": "panel", "status": "reserved", "mode": "impl",
                            "bound_since": TODAY, "ts": dt.datetime.now(dt.timezone.utc).isoformat()})
        self.cfg["stale_panel_requires_post_d6"] = (dt.date.today() + dt.timedelta(days=5)).isoformat()
        self._write_cfg()
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("no post-D6 run follows", r.stderr)

    def test_an_older_reservation_does_not_stand_in_for_the_carrying_rounds(self):
        # post-D6 run 2 (codex): the carrying round's own reservation is gone, an
        # older pre-opt-in one remains — the round cannot be dated → refused
        yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
        self._reset_ledger({"id": "bbbbbbbbbbbb", "kind": "panel", "status": "reserved", "mode": "impl",
                            "ts": yesterday})
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("belongs to this task's newest impl round", r.stderr)

    def test_no_reservation_dating_the_panel_refuses(self):
        # r2 (opus): the rule is on and a carrying panel exists, but nothing dates it
        (self.td / post_d6.RUNS_NAME).write_text("", encoding="utf-8")
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("no panel reservation", r.stderr)

    def test_owner_ok_without_the_opt_in_is_still_recorded(self):
        # r2 (agy): the owner's decision reaches the receipt whether or not the rule is on
        del self.cfg["stale_panel_requires_post_d6"]
        self._write_cfg()
        r = self._close("--owner-ok", "--reason", "owner: close it")
        self.assertTrue(self._done(r), r.stdout + r.stderr)
        self.assertIn('owner-ok: "owner: close it"', self._receipt())

    def test_a_run_reserved_while_the_close_commits_refuses(self):
        # r2 (codex-high): the binding is re-checked inside the commit's task lock.
        # Simulated: the binding holds at the gate and the pre-commit check, and
        # fails at the in-lock re-check (a run reserved in between).
        import contextlib
        import io
        from unittest import mock
        from tasks import core
        from tasks.lifecycle import cmd_work
        self._post_d6("PASS")
        real = core.bound_stale_close
        calls = []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) >= 3:
                return False, "a newer post-D6 run was reserved", ""
            return real(*a, **k)
        out, err = io.StringIO(), io.StringIO()
        old_cwd, old_env = os.getcwd(), dict(os.environ)
        os.chdir(self.d)
        os.environ.update(self.env)
        try:
            with mock.patch.object(core, "bound_stale_close", side_effect=flaky), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with contextlib.suppress(SystemExit):
                    cmd_work(["done", "--stale-panel-ok", "--reason", "x"])
        finally:
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
        self.assertGreaterEqual(len(calls), 3, err.getvalue())
        self.assertIn("binding no longer holds", err.getvalue())
        self.assertNotIn("## Status\ndone", self._receipt())

    def test_owner_ok_does_not_pass_a_malformed_date(self):
        # post-D6 run 1 (codex): the date is validated before the owner's decision
        self.cfg["stale_panel_requires_post_d6"] = "tomorrow"
        self._write_cfg()
        r = self._close("--owner-ok", "--reason", "owner: close")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("is not a YYYY-MM-DD date", r.stderr)

    def test_a_docs_change_after_the_pass_is_tail_certified_from_the_pass_tree(self):
        # post-D6 run 1 (codex) / PLAN S12b: a plain close after a post-D6 PASS
        # sends a docs change to tail certification, measured from the tree the
        # post-D6 judge reviewed (the code change before it is NOT in the delta)
        import contextlib
        import io
        from unittest import mock
        from tasks.lifecycle import cmd_work
        self._post_d6("PASS")
        (self.d / "notes.md").write_text("# a doc written after the PASS\n", encoding="utf-8")
        seen = {}

        def judge(project_path, snapshot, non_behavioral, panel_summary, **kw):
            seen["delta"] = list(non_behavioral)
            seen["summary"] = panel_summary
            return "PASS"
        out, err = io.StringIO(), io.StringIO()
        old_cwd, old_env = os.getcwd(), dict(os.environ)
        os.chdir(self.d)
        os.environ.update(self.env)
        try:
            with mock.patch("tasks.review.run_tail_cert_judge", side_effect=judge), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with contextlib.suppress(SystemExit):
                    cmd_work(["done"])
        finally:
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
        self.assertEqual(seen.get("delta"), ["notes.md"], err.getvalue())
        self.assertIn("post-D6 single judge then PASSED", seen.get("summary", ""))
        self.assertIn("Task 001 done.", out.getvalue(), err.getvalue())

    def test_a_run_reserved_during_tail_certification_refuses_the_close(self):
        # post-D6 run 2 (codex): the PASS tail-cert started from must still be the
        # newest finished run when the close commits (checked under the task lock)
        import contextlib
        import io
        from unittest import mock
        from tasks.lifecycle import cmd_work
        self._post_d6("PASS")
        (self.d / "notes.md").write_text("# doc after the PASS\n", encoding="utf-8")
        td, panel = self.td, self.panel

        def judge(*a, **kw):
            post_d6.append_run(td, {"id": "late1", "panel": panel, "status": "reserved"})
            return "PASS"
        out, err = io.StringIO(), io.StringIO()
        old_cwd, old_env = os.getcwd(), dict(os.environ)
        os.chdir(self.d)
        os.environ.update(self.env)
        try:
            with mock.patch("tasks.review.run_tail_cert_judge", side_effect=judge), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with contextlib.suppress(SystemExit):
                    cmd_work(["done"])
        finally:
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
        self.assertNotIn("Task 001 done.", out.getvalue())
        self.assertIn("no longer the newest finished run", err.getvalue())

    def test_a_corrupt_run_ledger_refuses(self):
        self._post_d6("PASS")
        with open(self.td / post_d6.RUNS_NAME, "a", encoding="utf-8") as fh:
            fh.write('{"id": "r9", "panel": "tru\n')
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertFalse(self._done(r), r.stdout)
        self.assertIn("corrupt lines", r.stderr)


class PostD6RunRecordsTheReviewedTree(unittest.TestCase):
    """B5: a REAL post-D6 single-judge run (tests/test_post_d6.EndToEnd's driver:
    a faked panel, then `tasks impl-review` with the sandbox faked) records the
    tree it reviewed and the config it ran under — and that record is what binds
    the close."""

    def test_the_run_record_carries_the_reviewed_tree_and_binds_the_close(self):
        from tests.test_post_d6 import EndToEnd
        e2e = EndToEnd("test_a_tree_edit_between_delta_and_tamper_baseline_sends_nothing")
        e2e.setUp()
        self.addCleanup(e2e.doCleanups)
        d = e2e.d
        e2e._panel()
        (d / "edit.py").write_text("v = 2\n", encoding="utf-8")       # post-panel change
        code, err, out = e2e._single("CAP: 0/5 reported, exhausted\n")
        self.assertIn("POST-D6 VERDICT: PASS", out + err)
        run = e2e._single_runs()[-1]
        self.assertEqual(run.get("verdict"), "PASS")
        snap = run.get("reviewed_snapshot")
        self.assertIsInstance(snap, dict)
        self.assertEqual(snap.get("tree_fp"), tree_state_fingerprint(d))
        self.assertEqual(run.get("config_sha"), config_sha(d))
        # bind: opt in, then the stale close rests on that PASS
        (d / ".agent" / "config.json").write_text(json.dumps(
            {"panel_required_for": "all", "stale_panel_requires_post_d6": TODAY}), encoding="utf-8")
        # the opt-in itself changed the config after the PASS → refused, as designed
        from tasks.core import bound_stale_close
        ok, msg, _ = bound_stale_close(d, e2e.tf.parent, owner_ok_reason=None)
        self.assertFalse(ok)
        self.assertIn(".agent/config.json changed after the post-D6 PASS", msg)
        # a PASS taken under the opted-in config binds
        code, err, out = e2e._single("CAP: 0/5 reported, exhausted\n")
        ok, msg, note = bound_stale_close(d, e2e.tf.parent, owner_ok_reason=None)
        self.assertTrue(ok, msg)
        self.assertIn("bound to post-D6 PASS", note)


class TailCertSavesWhatItsJudgeSaid(unittest.TestCase):
    """B7 (task 147): a tail-cert refusal used to leave no readable reason, which
    invited the override; the judge's output now lands beside the task."""

    def test_the_tail_cert_output_is_saved_in_the_task_dir(self):
        from unittest import mock
        from tasks import review as R
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp) / ".agent" / "tasks" / "001-t"
            td.mkdir(parents=True)
            tf = td / "task.md"
            tf.write_text("# 001\n", encoding="utf-8")
            said = "The delta changes tests/test_x.py in a way that weakens the check.\nTAIL-CERT x: FAIL"
            with mock.patch.object(R, "_tail_cert_review_diff", return_value="diff --git a b"), \
                    mock.patch.object(R, "_run_tail_cert_judge_raw", return_value=said), \
                    mock.patch.object(R, "_snapshot_repo_state", return_value={}), \
                    mock.patch.object(R, "_detect_tamper_full",
                                      return_value={"mutations": [], "cautions": [], "degraded": False}), \
                    mock.patch.object(R, "_journal_review_spend"), \
                    mock.patch("sys.stderr"):
                R.run_tail_cert_judge(Path(tmp), {}, ["tests/test_x.py"], "summary", task_file=tf)
            log = td / R.TAIL_CERT_LOG
            self.assertTrue(log.is_file(), "the tail-cert judge's output was not saved")
            self.assertIn("weakens the check", log.read_text(encoding="utf-8"))

    def test_a_tampered_run_saves_nothing(self):
        # Negative control: past a tamper hit nothing from that judge is kept.
        from unittest import mock
        from tasks import review as R
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp) / ".agent" / "tasks" / "001-t"
            td.mkdir(parents=True)
            tf = td / "task.md"
            tf.write_text("# 001\n", encoding="utf-8")
            with mock.patch.object(R, "_tail_cert_review_diff", return_value="diff"), \
                    mock.patch.object(R, "_run_tail_cert_judge_raw", return_value="x"), \
                    mock.patch.object(R, "_snapshot_repo_state", return_value={}), \
                    mock.patch.object(R, "_detect_tamper_full",
                                      return_value={"mutations": ["?? rogue"], "cautions": [], "degraded": False}):
                self.assertIsNone(R.run_tail_cert_judge(Path(tmp), {}, [], "s", task_file=tf))
            self.assertFalse((td / R.TAIL_CERT_LOG).exists())


if __name__ == "__main__":
    unittest.main()
