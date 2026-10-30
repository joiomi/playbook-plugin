#!/usr/bin/env python3
"""The review round cap (task 209; task 344: 2, whatever the risk) through the CLI: `tasks panel-review --mode impl`
refuses a third impl round (`tasks.stop_rule`). Task 343 (PLAN-PLAYBOOK-2.md 4.1 a) took out its close half — the
close's FRESH / STALE rule is tests/test_close_fresh_or_stale.py. The pure half is tests/test_stop_rule.py.

Run: python3 -m unittest tests.test_stop_rule_cli
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))

from tasks import review_ledger  # noqa: E402

BAR = "═" * 60


def _git(d, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=d, check=True, capture_output=True)


def rnd(mode="Impl", verdict="PASS", judge_lines=("fine",), v2=True, rid=""):
    """A panel round as review.py writes it (its header, a bar, the judges' lines — quoted in format 2)."""
    head = f"# Panel {mode} Review — .agent/tasks/001-demo/task.md\n" + ("**Round-format:** 2\n" if v2 else "")
    head += f"\n**PANEL VERDICT: {verdict}** — 2/2 judges succeeded, quorum 2\n\n"
    if rid:
        head += f"**Round-id:** {rid}\n\n"
    body = "\n".join(("> " + ln if v2 else ln) for ln in judge_lines)
    return head + BAR + "\n  JUDGE: claude:opus\n" + BAR + "\n\n" + body + "\n\n"


class PanelCap(unittest.TestCase):
    """Q1-Q3, Q12 (the refusal's words): `cmd_panel_review` with faked claude judges in a git project."""

    def setUp(self):
        from tasks import review as R
        self.R = R
        self.d = Path(tempfile.mkdtemp(prefix="pb209p-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        _git(self.d, "init", "-q")
        self.td = self.d / ".agent" / "tasks" / "001-demo"
        self.td.mkdir(parents=True)
        self.tf = self.td / "task.md"
        self._task("assertive")
        (self.d / ".agent" / "models.json").write_text(json.dumps(
            {"panel": ["claude:opus", "claude:sonnet"], "default_judge": "claude:opus"}), encoding="utf-8")
        (self.d / "MIND_MAP.md").write_text("# Mind Map\n[1] node\n", encoding="utf-8")
        (self.d / "code.py").write_text("v = 1\n", encoding="utf-8")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-q", "-m", "c0")
        R._PB_JOURNAL_MOD = None
        R._PB_JOURNAL_LOADED = False
        self.calls = []

    def _task(self, risk):
        self.tf.write_text(
            f"# 001 - demo\n\n## Status\nin-progress\n\n## Risk\n{risk}\n\n## Intent\nx\n\n"
            "## Implementation Review\n(implementation review triage appears here)\n\n"
            "## Work Plan\n- [ ] a gate\n", encoding="utf-8")

    def _judge_md(self, *rounds_newest_first, name="judge.md"):
        (self.td / name).write_text("".join(rounds_newest_first), encoding="utf-8")

    def _ledger(self):
        try:
            return (self.td / review_ledger.RUNS_NAME).read_bytes()
        except OSError:
            return b""

    def _panel(self, *extra, mode="impl"):
        from provider.adapters.claude import ClaudeAdapter
        calls = self.calls

        def judge(self_, *a, **kw):
            calls.append(1)
            return "1. **Important** — `code.py:1` fine.\n\nCAP: 1/5 reported, exhausted\n"
        err, out = io.StringIO(), io.StringIO()
        code = None
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            with mock.patch.object(ClaudeAdapter, "is_available", classmethod(lambda cls: True)), \
                 mock.patch.object(ClaudeAdapter, "run_headless_judge", judge), \
                 contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                self.R.cmd_panel_review(["001", "--mode", mode, "--models", "claude:opus,claude:sonnet", *extra])
        except SystemExit as e:
            code = e.code
        finally:
            os.chdir(cwd)
        return code, err.getvalue(), out.getvalue()

    def _refused(self, *extra, mode="impl"):
        before = self._ledger()
        code, err, out = self._panel(*extra, mode=mode)
        self.assertEqual(code, 2, err + out)
        self.assertEqual(self.calls, [], "a judge was spawned past the cap")
        self.assertEqual(self._ledger(), before, "the refused panel wrote to the run ledger")
        self.assertNotIn("in progress", err)
        return err

    def _ran(self, *extra, mode="impl"):
        code, err, out = self._panel(*extra, mode=mode)
        self.assertEqual(len(self.calls), 2, err + out)
        return err + out


    def test_q1_the_second_round_runs(self):
        self._judge_md(rnd(rid="aaaaaaaaaaaa"))
        self._ran()


    def test_q2_a_below_quorum_round_and_a_plan_round_are_not_rounds(self):
        self._judge_md(rnd(verdict="FAIL", rid="cccccccccccc"), rnd("Plan", rid="bbbbbbbbbbbb"),
                       rnd(rid="aaaaaaaaaaaa"))
        self._ran()

    def test_q2_an_archived_round_counts(self):
        self._judge_md(rnd(rid="aaaaaaaaaaaa"), name="judge-archive.md")
        self._judge_md(rnd(rid="bbbbbbbbbbbb"))
        self._refused()

    def test_q2_a_record_that_cannot_be_read_refuses(self):
        self._judge_md(rnd(rid="aaaaaaaaaaaa"))
        (self.td / "judge-archive.md").mkdir()
        err = self._refused()
        self.assertIn("judge-archive.md", err)

    def test_q3_the_owner_allows_a_round_past_the_cap_and_it_is_recorded(self):
        self._judge_md(rnd(rid="bbbbbbbbbbbb"), rnd(rid="aaaaaaaaaaaa"))
        self._ran("--owner-ok", "--reason", "one more, the code changed")
        res = [r for r in review_ledger.read_runs(self.td) if r.get("kind") == "panel"]
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].get("owner_ok"), "one more, the code changed")
        self.assertEqual(res[0].get("risk"), "assertive")
        self.assertNotIn("rounds_cap", res[0])         # task 344 (P2): the reservation records no caps
        self.assertNotIn("guard_paths", res[0])        # task 343 (plan panel P12): no guard list is recorded

    def test_q3_owner_ok_without_a_reason_is_refused_before_anything(self):
        self._judge_md(rnd(rid="bbbbbbbbbbbb"), rnd(rid="aaaaaaaaaaaa"))
        before = self._ledger()
        code, err, out = self._panel("--owner-ok")
        self.assertEqual(code, 2, err)
        self.assertIn("--reason", err)
        self.assertEqual(self.calls, [])
        self.assertEqual(self._ledger(), before)

    def test_a_plan_panel_is_never_capped(self):
        self._judge_md(rnd(rid="bbbbbbbbbbbb"), rnd(rid="aaaaaaaaaaaa"))
        self._ran(mode="plan")


    # ── task 344: one cap of 2, whatever the risk ───────────────────────────────────────────────────────────

    def test_a_reversible_task_takes_two_rounds(self):
        """Row 1: the second round of a reversible task runs (it was refused: cap 1)."""
        self._task("reversible")
        self._judge_md(rnd(rid="aaaaaaaaaaaa"))
        self._ran()

    def test_a_third_round_is_refused_even_after_a_critical(self):
        """Row 2 (P11): two rounds, the second with a Critical, refuse a third for every risk; three rounds with no
        flag refuse too — exit 2, no judge, the ledger untouched."""
        crit = ["**Critical** — `code.py:1` x."]
        for risk in ("reversible", "assertive", "irreversible", "unclassified"):
            with self.subTest(risk=risk):
                self.calls.clear()
                self._task(risk)
                self._judge_md(rnd(rid="bbbbbbbbbbbb", judge_lines=crit), rnd(rid="aaaaaaaaaaaa"))
                err = self._refused()
                self.assertIn('tasks panel-review 001 --mode impl --owner-ok --reason "…"', err)
        self.calls.clear()
        (self.td / review_ledger.RUNS_NAME).unlink(missing_ok=True)
        self._task("assertive")
        self._judge_md(rnd(rid="cccccccccccc", judge_lines=crit), rnd(rid="bbbbbbbbbbbb", judge_lines=crit),
                       rnd(rid="aaaaaaaaaaaa"))
        self._refused()

    def test_review_rounds_changes_nothing(self):
        """Row 4: a config that raises every cap still has the third round refused, and nothing names the key."""
        (self.d / ".agent" / "config.json").write_text(json.dumps(
            {"review_rounds": {"reversible": 5, "assertive": 5, "irreversible": 5}}), encoding="utf-8")
        self._judge_md(rnd(rid="bbbbbbbbbbbb"), rnd(rid="aaaaaaaaaaaa"))
        err = self._refused()
        self.assertNotIn("review_rounds", err)

    def test_the_reservation_records_the_risk_and_no_caps(self):
        """Row 6: the panel's reservation keeps what the close reads — the risk — and no caps."""
        self._judge_md(rnd(rid="aaaaaaaaaaaa"))
        self._ran()
        res = [r for r in review_ledger.read_runs(self.td) if r.get("kind") == "panel"]
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].get("risk"), "assertive")
        self.assertNotIn("rounds_cap", res[0])

    def test_a_risk_lowered_after_one_round_still_gets_the_second(self):
        """Row 7, the panel's half (P8; 343's I6): an assertive task's round, then `## Risk` lowered to reversible —
        the close offers another impl round (its gate risk stays assertive), and `panel-review` must run it."""
        review_ledger.append_run(self.td, {"id": "p0", "kind": "panel", "mode": "impl", "status": "done",
                                           "risk": "assertive"})
        self._judge_md(rnd(rid="aaaaaaaaaaaa"))
        self._task("reversible")
        self._ran()

    def test_an_old_rounds_cap_changes_nothing(self):
        """Row 10 (P4): a 1.6.x reservation's `rounds_cap` decides nothing — one and two rounds give the same answer
        with `{reversible: 1}`, `{assertive: 5}` or no field."""
        for cap in (None, {"reversible": 1}, {"assertive": 5}):
            for n, runs in ((1, True), (2, False)):
                with self.subTest(cap=cap, rounds=n):
                    (self.td / review_ledger.RUNS_NAME).unlink(missing_ok=True)
                    self.calls.clear()
                    rec: dict = {"id": "p0", "kind": "panel", "mode": "impl", "status": "done", "risk": "assertive"}
                    if cap is not None:
                        rec["rounds_cap"] = cap
                    review_ledger.append_run(self.td, rec)
                    self._judge_md(*[rnd(rid=c * 12) for c in "ba"[2 - n:]])
                    if runs:
                        self._ran()
                    else:
                        self._refused()


if __name__ == "__main__":
    unittest.main()
