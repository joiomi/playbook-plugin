#!/usr/bin/env python3
"""The review stop rule as the plugin's default (PHASE-B.md item 1, task 209) — its CLI halves: `tasks panel-review
--mode impl` refuses a round past the D6 cap (`tasks.stop_rule`), and `tasks work done` decides a stale close by
what changed after the panel. The pure half is tests/test_stop_rule.py.

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

from tasks import post_d6  # noqa: E402

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
            return (self.td / post_d6.RUNS_NAME).read_bytes()
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

    def test_q1_a_third_round_without_a_critical_in_round_2_is_refused(self):
        self._judge_md(rnd(rid="bbbbbbbbbbbb"), rnd(rid="aaaaaaaaaaaa"))
        err = self._refused()
        self.assertIn("D6 round cap", err)
        self.assertIn("round 2 counted no Critical", err)
        self.assertIn('tasks panel-review 001 --mode impl --owner-ok --reason "…"', err)

    def test_q1_the_second_round_runs(self):
        self._judge_md(rnd(rid="aaaaaaaaaaaa"))
        self._ran()

    def test_q2_a_quoted_critical_in_round_2_allows_a_third(self):
        self._judge_md(rnd(rid="bbbbbbbbbbbb", judge_lines=["**Critical** — `code.py:1` x."]), rnd(rid="aaaaaaaaaaaa"))
        self._ran()

    def test_q2_an_unquoted_critical_in_a_format_1_round_2_allows_a_third(self):
        self._judge_md(rnd(v2=False, judge_lines=["1. **Critical** code.py:1 x"]), rnd(v2=False))
        self._ran()

    def test_q2_a_settled_critical_does_not(self):
        self._judge_md(rnd(rid="bbbbbbbbbbbb", judge_lines=["**Critical** [SETTLED] code.py:1 x"]),
                       rnd(rid="aaaaaaaaaaaa"))
        self._refused()

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
        self._ran("--owner-ok", "--reason", "one more, the guard changed")
        res = [r for r in post_d6.read_runs(self.td) if r.get("kind") == "panel"]
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].get("owner_ok"), "one more, the guard changed")
        self.assertEqual(res[0].get("risk"), "assertive")
        self.assertEqual(res[0].get("rounds_cap"), {"reversible": 1, "assertive": 2, "irreversible": 2})
        self.assertEqual(res[0].get("guard_paths"), [])

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

    def test_a_reversible_task_takes_one_round(self):
        self._task("reversible")
        self._judge_md(rnd(rid="aaaaaaaaaaaa"))
        self.assertIn("cap 1 for a reversible task", self._refused())

    def test_the_project_changes_the_cap(self):
        (self.d / ".agent" / "config.json").write_text(json.dumps({"review_rounds": {"assertive": 3}}),
                                                       encoding="utf-8")
        self._judge_md(rnd(rid="bbbbbbbbbbbb"), rnd(rid="aaaaaaaaaaaa"))
        self._ran()
        res = [r for r in post_d6.read_runs(self.td) if r.get("kind") == "panel"]
        self.assertEqual(res[0].get("rounds_cap", {}).get("assertive"), 3)



class _CloseFixture(unittest.TestCase):
    """A git project with an impl panel round on record and a change after it (tests/test_stale_close_bound.py's
    shape): `setup_task` writes the rounds, the newest one stamped, and the panel's reservation as review.py
    records it since task 209 (risk, caps, guard list); closes run as real subprocesses."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = self.d = Path(self._tmp.name) / "proj"
        d.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        for rel, text in (("code.py", "x = 1\n"), ("guard/g.py", "g = 1\n"), ("hooks/hooks.json", "{}\n"),
                          ("tests/test_x.py", "# t\n"), ("tests/guard_fixture.py", "# f\n"), ("MIND_MAP.md", "# map\n")):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text, encoding="utf-8")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "seed")
        self.cfg = {"panel_required_for": ["assertive", "irreversible"]}
        self.td = d / ".agent" / "tasks" / "001-t"
        self.td.mkdir(parents=True)
        # never the real HOME (the review trap checklist: a fixture once wrote the owner's logger); a judge's or a
        # hook's role variables would change what the CLI does — dropped
        home = Path(self._tmp.name) / "home"
        home.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("PLAYBOOK_SANDBOXED", "PLAYBOOK_ROLE", "CLAUDE_SESSION_ID", "CODEX_SESSION_ID")}
        self.env.update(PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="pid-t209", HOME=str(home))

    def _write_cfg(self):
        (self.d / ".agent").mkdir(exist_ok=True)
        (self.d / ".agent" / "config.json").write_text(json.dumps(self.cfg), encoding="utf-8")

    def _set_risk(self, risk):
        (self.td / "task.md").write_text(
            f"# 001 - T\n\n## Status\npending\n\n## Risk\n{risk}\n\n## Work Plan\n- [x] G1: do it\n",
            encoding="utf-8")

    def setup_task(self, risk="assertive", rounds=2, rec=None, change="code.py", critical_last=False,
                   on_top="", tamper="", top_rec=None, older_rec=None, stamp=True, archive_pass=False):
        from tasks.core import build_panel_snapshot, format_panel_snapshot_line, tree_state_fingerprint
        self._write_cfg()
        self._set_risk(risk)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "1"], cwd=self.d, env=self.env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        assert r.returncode == 0, r.stderr
        fp = tree_state_fingerprint(self.d)
        lines = ["**Critical** — `code.py:1` x."] if critical_last else ["fine"]
        stamp_lines = (f"**Tree-state:** {fp}\n\n{format_panel_snapshot_line(build_panel_snapshot(self.d, fp))}\n\n"
                       if stamp else "")
        newest = rnd(rid="aaaaaaaaaaaa", judge_lines=lines).replace(
            "**Round-id:** aaaaaaaaaaaa\n\n",
            "**Round-id:** aaaaaaaaaaaa\n\n" + stamp_lines
            + (f"**Tamper guard:** {tamper}\n\n" if tamper else ""))
        older = "".join(rnd(rid=f"{i:012x}") for i in range(rounds - 1, 0, -1))
        if archive_pass:
            # the reviewed round lives in judge-archive.md, five newer plan rounds in judge.md (round 2, S4)
            (self.td / "judge-archive.md").write_text(newest + older, encoding="utf-8")
            (self.td / "judge.md").write_text(
                "".join(rnd("Plan", rid=f"{0xbbbbbbbbbbb0 + i:012x}") for i in range(5)), encoding="utf-8")
        else:
            (self.td / "judge.md").write_text(on_top + newest + older, encoding="utf-8")
        for rid, extra in (("ffffffffffff", top_rec), ("000000000001", older_rec)):
            if extra is not None:
                post_d6.append_run(self.td, {"id": rid, "kind": "panel", "status": "reserved", "mode": "impl", **extra})
                post_d6.append_run(self.td, {"id": rid, "kind": "panel", "status": "done"})
        record = {"risk": risk, "rounds_cap": {"reversible": 1, "assertive": 2, "irreversible": 2},
                  "guard_paths": list(self.cfg.get("guard_paths") or [])}
        record.update(rec or {})
        post_d6.append_run(self.td, {"id": "aaaaaaaaaaaa", "kind": "panel", "status": "reserved", "mode": "impl",
                                     **record})
        post_d6.append_run(self.td, {"id": "aaaaaaaaaaaa", "kind": "panel", "status": "done"})
        self.panel = "aaaaaaaaaaaa"            # the reviewed round's key (post-D6 runs after it key to it)
        if change:
            p = self.d / change
            p.write_text(p.read_text(encoding="utf-8") + "# changed after the panel\n", encoding="utf-8")

    def _post_d6(self, verdict="PASS"):
        from tasks.core import build_panel_snapshot, config_sha, tree_state_fingerprint
        fp = tree_state_fingerprint(self.d)
        post_d6.append_run(self.td, {
            "id": f"run{verdict}", "panel": self.panel, "status": "done", "verdict": verdict,
            "critical": 0, "important": 0 if verdict == "PASS" else 1,
            "reviewed_snapshot": build_panel_snapshot(self.d, fp), "config_sha": config_sha(self.d)})

    def _close(self, *flags):
        return subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done", *flags],
                              cwd=self.d, env=self.env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def _done(self, r):
        return "Task 001 done." in r.stdout

    def _receipt(self):
        return (self.td / "task.md").read_text(encoding="utf-8")

    def _close_in_process(self, during, *flags):
        from tasks import cli as tcli
        from tasks import core as tcore
        real = tcore.format_verify_receipt

        def fake(*a, **kw):
            during()
            return real(*a, **kw)
        err, out = io.StringIO(), io.StringIO()
        code = None
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            with mock.patch.dict(os.environ, {"PLAYBOOK_SESSION_ID": "pid-t209", "HOME": self.env["HOME"]}), \
                 mock.patch.object(tcore, "format_verify_receipt", fake), \
                 mock.patch.object(sys, "argv", ["tasks", "work", "done", *flags]), \
                 contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                tcli.main()
        except SystemExit as e:
            code = e.code
        finally:
            os.chdir(cwd)
        return code, err.getvalue(), out.getvalue()

    def assertClosed(self, r):
        self.assertTrue(self._done(r), r.stdout + r.stderr)

    def assertHeld(self, r, *words):
        self.assertFalse(self._done(r), r.stdout + r.stderr)
        for w in words:
            self.assertIn(w, r.stderr)


class TheJudgeWhereItIsNeeded(_CloseFixture):
    """Q4, Q7, Q10, Q11: a stale close whose change needs the post-panel judge — at the cap and below it, in a
    project WITHOUT `stale_panel_requires_post_d6`."""

    def test_q4_a_guard_file_at_the_cap(self):
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="guard/g.py")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows",
                        "guard/g.py", "tasks impl-review")
        self.assertHeld(self._close("--force", "--reason", "x"), "no post-D6 run follows")
        self._post_d6("PASS")
        r = self._close("--stale-panel-ok", "--reason", "the judge passed it")
        self.assertClosed(r)
        self.assertIn("bound to post-D6 PASS runPASS", self._receipt())

    def test_q4_a_guard_file_below_the_cap(self):                          # plan panel P1
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=1, change="guard/g.py")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")

    def test_q4_an_irreversible_tasks_code_below_the_cap(self):            # plan panel P1: WoW's case
        self.setup_task(risk="irreversible", rounds=1, change="code.py")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows",
                        "irreversible")
        self.assertHeld(self._close("--force", "--reason", "x"), "no post-D6 run follows")
        r = self._close("--owner-ok", "--reason", "the owner read it")
        self.assertClosed(r)
        self.assertIn('owner-ok: "the owner read it"', self._receipt())

    def test_q4_an_irreversible_tasks_docs_alone_need_no_judge(self):
        self.setup_task(risk="irreversible", rounds=1, change="MIND_MAP.md")
        self.assertClosed(self._close("--stale-panel-ok", "--reason", "only the map"))

    def test_q4_an_unclassified_tasks_code(self):
        self.setup_task(risk="unclassified", rounds=1, change="code.py")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")

    def test_q7_a_guard_list_emptied_after_the_panel(self):
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="guard/g.py")
        self.cfg["guard_paths"] = []
        self._write_cfg()
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows", "guard/g.py")

    def test_q7_a_risk_lowered_after_the_panel(self):                      # plan panel P7
        self.setup_task(risk="irreversible", rounds=1, change="code.py")
        for lower in ("assertive", "reversible"):
            self._set_risk(lower)
            self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")

    def _no_snapshot(self):
        jm = self.td / "judge.md"
        jm.write_text("\n".join(ln for ln in jm.read_text(encoding="utf-8").splitlines()
                                if not ln.startswith("**Panel-snapshot:**")) + "\n", encoding="utf-8")

    def test_q7_a_delta_that_cannot_be_listed_with_a_guard_list(self):
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="code.py")
        self._no_snapshot()
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows",
                        "cannot be listed")

    def test_q7_a_delta_that_cannot_be_listed_on_an_irreversible_task(self):
        self.setup_task(risk="irreversible", rounds=2, change="code.py")
        self._no_snapshot()
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")

    def test_q7_with_nothing_the_delta_could_decide_it_keeps_the_old_rule(self):
        # impl round 2, S1: the no-judge exit is for a plain STALE with a LISTED delta; an unlistable one keeps
        # the project's own rule — a reason without the opt-in, the binding with it
        self.setup_task(rounds=2, change="code.py")              # assertive, no guard list
        self._no_snapshot()
        self.assertClosed(self._close("--stale-panel-ok", "--reason", "x"))
        self.assertNotIn("no judge read after the last panel", self._receipt())

    def test_q7_an_unlistable_delta_at_the_cap_stays_bound_with_the_opt_in(self):
        self.cfg["stale_panel_requires_post_d6"] = "2026-01-01"
        self.setup_task(rounds=2, change="code.py")
        self._no_snapshot()
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")

    def test_q10_a_listed_json_and_a_listed_test_file(self):               # plan panel P9
        for listed in ("hooks/hooks.json", "tests/guard_fixture.py"):
            with self.subTest(listed=listed):
                self.setUp()
                self.cfg["guard_paths"] = ["hooks/", "tests/guard_fixture.py"]
                self.cfg["stale_panel_requires_post_d6"] = "2026-01-01"
                self.setup_task(rounds=2, change=listed)
                self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows", listed)

    def test_q11_a_reversible_task_that_changed_a_guard_file(self):        # plan panel P10
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(risk="reversible", rounds=1, change="guard/g.py")
        self.assertHeld(self._close(), "guard/g.py", "tasks impl-review")
        self._post_d6("PASS")
        self.assertClosed(self._close())

    def test_q11_a_reversible_task_that_changed_other_code_closes_as_before(self):
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(risk="reversible", rounds=1, change="code.py")
        self.assertClosed(self._close())


class NoJudgeAfterTheLastRound(_CloseFixture):
    """Q5, Q6, Q7 (the cap): a change no judge needs, after the last round D6 allows — even WITH the opt-in."""

    def setUp(self):
        super().setUp()
        self.cfg["stale_panel_requires_post_d6"] = "2026-01-01"
        self.cfg["guard_paths"] = ["guard/"]

    def test_q5_the_reason_closes_and_the_receipt_names_what_no_judge_read(self):
        self.setup_task(rounds=2, change="code.py")
        r = self._close("--stale-panel-ok", "--reason", "outside the guard list")
        self.assertClosed(r)
        rec = self._receipt()
        self.assertIn("no judge read after the last panel", rec)
        self.assertIn("code.py", rec)
        self.assertIn("2 of 2", rec)

    def test_q5_without_the_flag_the_block_names_it(self):                 # plan panel P5
        self.setup_task(rounds=2, change="code.py")
        r = self._close()
        self.assertHeld(r, "Stop rule (D6)", "--stale-panel-ok", "a fresh impl panel needs --owner-ok")

    def test_q5_a_critical_in_round_2_leaves_round_3_open(self):
        self.setup_task(rounds=2, change="code.py", critical_last=True)
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertHeld(r, "no post-D6 run follows")                       # below the cap: the opt-in binds
        self.assertIn("impl round 3 of 3 is still allowed", self._close().stderr)

    def test_q6_below_the_cap_the_opt_in_still_binds(self):
        self.setup_task(rounds=1, change="code.py")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")
        self.assertIn("impl round 2 of 2 is still allowed", self._close().stderr)

    def test_q7_a_cap_lowered_after_the_panel_frees_nothing(self):
        self.setup_task(rounds=2, change="code.py",
                        rec={"rounds_cap": {"reversible": 1, "assertive": 3, "irreversible": 2}})
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")


class TheDecisionIsMadeAgainAtTheCommit(_CloseFixture):
    """Q12 (plan panel P11): a "no judge" decision re-made inside the task lock at the commit. The change is made
    between the decision and the commit — inside `format_verify_receipt`, which the close calls between them —
    with the close run in-process."""

    def setUp(self):
        super().setUp()
        self.cfg["guard_paths"] = ["guard/"]

    def test_q12_a_guard_entry_added_while_the_close_runs(self):
        self.setup_task(rounds=2, change="code.py")

        def add():
            self.cfg["guard_paths"] = ["guard/", "code.py"]
            self._write_cfg()
        code, err, out = self._close_in_process(add, "--stale-panel-ok", "--reason", "x")
        self.assertNotIn("Task 001 done.", out, err)
        self.assertIn("code.py", err)

    def test_q12_a_guard_file_changed_under_force_while_the_close_runs(self):
        self.setup_task(rounds=2, change="code.py")

        def touch():
            (self.d / "guard" / "g.py").write_text("g = 2\n", encoding="utf-8")
        code, err, out = self._close_in_process(touch, "--force", "--reason", "x")
        self.assertNotIn("Task 001 done.", out, err)
        self.assertIn("guard/g.py", err)

    def test_control_nothing_changes_while_it_runs(self):
        self.setup_task(rounds=2, change="code.py")
        code, err, out = self._close_in_process(lambda: None, "--stale-panel-ok", "--reason", "x")
        self.assertIn("Task 001 done.", out, err)



class RoundOneFindings(_CloseFixture):
    """Task 209's impl panel round 1 (R1-R10): each a close that slipped through, red on b136443."""

    def test_r1_a_plan_round_on_top_does_not_hide_a_guard_change(self):          # grok, codex
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(risk="reversible", rounds=1, change="guard/g.py", on_top=rnd("Plan", rid="eeeeeeeeeeee"))
        self.assertHeld(self._close(), "guard/g.py")

    def test_r1_a_below_quorum_round_on_top_does_not_free_an_irreversible_task(self):
        self.setup_task(risk="irreversible", rounds=1, change="code.py",
                        on_top=rnd(verdict="FAIL", rid="ffffffffffff"))
        self.assertHeld(self._close("--force", "--reason", "x"), "below-quorum")

    def test_r2_a_fresh_panel_under_force_with_a_guard_file_changed_meanwhile(self):   # codex
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="")

        def touch():
            (self.d / "guard" / "g.py").write_text("g = 2\n", encoding="utf-8")
        code, err, out = self._close_in_process(touch, "--force", "--reason", "x")
        self.assertNotIn("Task 001 done.", out, err)
        self.assertIn("guard/g.py", err)

    def test_r3_the_automatic_judge_needs_no_panel_policy(self):                   # codex, grok: the owner's words
        del self.cfg["panel_required_for"]
        self.setup_task(risk="irreversible", rounds=1, change="code.py")
        self.assertHeld(self._close(), "irreversible")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "irreversible")

    def test_r4_a_degraded_tamper_guard_does_not_free_a_guard_list(self):          # opus
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="", tamper="degraded — the porcelain could not be read")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")

    def test_r4_an_exclude_over_code_does_not_free_an_irreversible_task(self):     # opus
        self.setup_task(risk="irreversible", rounds=1, change="")
        self.cfg["fingerprint_exclude"] = ["code.py"]
        self._write_cfg()
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")

    def test_r5_a_pattern_spelled_with_dot_slash_still_guards(self):              # opus, sonnet
        self.cfg["guard_paths"] = ["./guard//"]
        self.setup_task(rounds=2, change="guard/g.py")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "guard/g.py")

    def test_r5_a_pattern_that_names_nothing_is_said(self):
        self.cfg["guard_paths"] = ["nothere/", "guard/"]
        self.setup_task(rounds=2, change="code.py")
        r = self._close("--stale-panel-ok", "--reason", "x")
        self.assertIn("`guard_paths` entry 'nothere/' names nothing in the project", r.stderr)

    def _at_cap_with_opt_in(self):
        self.cfg["stale_panel_requires_post_d6"] = "2026-01-01"
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="code.py")

    def test_r6_a_post_panel_run_that_counted_a_critical_holds_the_no_judge_close(self):   # sonnet
        self._at_cap_with_opt_in()
        post_d6.append_run(self.td, {"id": "runC", "panel": self.panel, "status": "done", "verdict": "FAIL",
                                     "critical": 1, "important": 0})
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "counted a Critical")

    def test_r6_an_unfinished_run_or_a_corrupt_ledger_holds_it(self):
        self._at_cap_with_opt_in()
        post_d6.append_run(self.td, {"id": "runR", "panel": self.panel, "status": "reserved"})
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "has not finished")
        post_d6.append_run(self.td, {"id": "runR", "status": "failed"})
        with open(self.td / post_d6.RUNS_NAME, "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "corrupt")

    def test_r6_a_run_with_only_important_findings_does_not_hold_it(self):        # ruling (b): not grave
        self._at_cap_with_opt_in()
        post_d6.append_run(self.td, {"id": "runI", "panel": self.panel, "status": "done", "verdict": "FAIL",
                                     "critical": 0, "important": 2})
        self.assertClosed(self._close("--stale-panel-ok", "--reason", "x"))

    def test_r9_a_reversible_tasks_close_names_what_no_judge_read(self):          # codex: D2's words
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(risk="reversible", rounds=1, change="code.py")
        self.assertClosed(self._close())
        self.assertIn("no judge read after the last panel", self._receipt())
        self.assertIn("code.py", self._receipt())

    def test_r10_a_cap_raised_while_the_close_runs(self):                        # grok
        self._at_cap_with_opt_in()

        def raise_cap():
            self.cfg["review_rounds"] = {"assertive": 3}
            self._write_cfg()
        code, err, out = self._close_in_process(raise_cap, "--stale-panel-ok", "--reason", "x")
        self.assertNotIn("Task 001 done.", out, err)
        self.assertIn("changed while this close ran", err)

    def test_r10_the_files_changed_under_force_while_the_close_runs(self):
        self._at_cap_with_opt_in()

        def another():
            (self.d / "MIND_MAP.md").write_text("# map, changed during the close\n", encoding="utf-8")
        code, err, out = self._close_in_process(another, "--force", "--reason", "x")
        self.assertNotIn("Task 001 done.", out, err)
        self.assertIn("changed while this close ran", err)



class RoundTwoFindings(_CloseFixture):
    """Task 209's impl panel round 2 (S1-S9): each red on nested 057a47b."""

    def _opt_in_no_guards(self):
        self.cfg["stale_panel_requires_post_d6"] = "2026-01-01"

    def test_s1_an_exclude_over_code_at_the_cap_stays_bound(self):              # gemini, opus, sonnet
        self._opt_in_no_guards()
        self.setup_task(rounds=2, change="")
        self.cfg["fingerprint_exclude"] = ["code.py"]
        self._write_cfg()
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")

    def test_s1_a_degraded_tamper_guard_at_the_cap_stays_bound(self):
        self._opt_in_no_guards()
        self.setup_task(rounds=2, change="code.py", tamper="degraded — the porcelain could not be read")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "no post-D6 run follows")

    def test_s1_control_a_plain_stale_at_the_cap_still_closes_on_the_reason(self):
        self._opt_in_no_guards()
        self.setup_task(rounds=2, change="code.py")
        self.assertClosed(self._close("--stale-panel-ok", "--reason", "x"))

    def test_s2_a_below_quorum_round_on_top_cannot_shed_the_guard_list(self):    # codex, grok, sonnet
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(risk="reversible", rounds=1, change="", on_top=rnd(verdict="FAIL", rid="ffffffffffff"),
                        top_rec={"risk": "reversible", "guard_paths": [],
                                 "rounds_cap": {"reversible": 1, "assertive": 2, "irreversible": 2}})
        self.cfg["guard_paths"] = []
        self._write_cfg()
        (self.d / "guard" / "g.py").write_text("g = 3\n", encoding="utf-8")
        self.assertHeld(self._close(), "guard/g.py")

    def test_s2_the_hold_reads_the_reviewed_rounds_runs(self):
        # a below-quorum round on top must not hide a post-D6 run that counted a Critical — filed under the
        # reviewed round's key or, as `impl-review` files it, under the round on top (impl round 3, T1: such a
        # close is refused outright)
        for key in ("aaaaaaaaaaaa", "ffffffffffff"):
            with self.subTest(key=key):
                self.setUp()
                self.cfg["guard_paths"] = ["guard/"]
                self.setup_task(risk="reversible", rounds=1, change="code.py",
                                on_top=rnd(verdict="FAIL", rid="ffffffffffff"))
                post_d6.append_run(self.td, {"id": "runC", "panel": key, "status": "done", "verdict": "FAIL",
                                             "critical": 1, "important": 0})
                self.assertHeld(self._close(), "meets its quorum")

    def test_s4_the_owner_closes_an_archived_rounds_guard_change(self):
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(risk="reversible", rounds=1, change="guard/g.py", archive_pass=True)
        r = self._close("--owner-ok", "--reason", "the owner read the guard change")
        self.assertClosed(r)
        self.assertIn('owner-ok: "the owner read the guard change"', self._receipt())

    def test_s2_a_risk_lowered_between_two_panels_frees_nothing(self):          # opus
        self.setup_task(risk="assertive", rounds=2, change="code.py",
                        older_rec={"risk": "irreversible", "guard_paths": [],
                                   "rounds_cap": {"reversible": 1, "assertive": 2, "irreversible": 2}})
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "irreversible")

    def test_s3_an_unstamped_round_without_a_panel_policy(self):                # codex
        del self.cfg["panel_required_for"]
        self.setup_task(risk="irreversible", rounds=1, change="code.py", stamp=False)
        self.assertHeld(self._close(), "cannot be listed")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "cannot be listed")

    def test_s4_the_reviewed_round_in_the_archive_still_counts(self):           # grok, codex
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(risk="reversible", rounds=1, change="guard/g.py", archive_pass=True)
        self.assertHeld(self._close(), "guard/g.py")

    def test_s5_a_guarded_test_file_is_not_left_to_tail_certification(self):    # opus
        self.cfg["guard_paths"] = ["tests/guard_fixture.py"]
        # no judge can start here: a missing default judge and a PATH without the judge programs
        (self.d / ".agent" / "models.json").write_text('{"panel": ["pi"], "default_judge": "pi"}', encoding="utf-8")
        self.setup_task(rounds=2, change="tests/guard_fixture.py")
        self.env["PATH"] = "/usr/bin:/bin"
        r = self._close()
        self.assertHeld(r, "Stop rule (D6)", "tests/guard_fixture.py")
        self.assertNotIn("tail certification", r.stderr)

    def test_s6_a_run_that_counts_a_critical_while_the_close_runs(self):        # codex
        self.cfg["stale_panel_requires_post_d6"] = "2026-01-01"
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="code.py")

        def a_critical():
            post_d6.append_run(self.td, {"id": "runLate", "panel": self.panel, "status": "done", "verdict": "FAIL",
                                         "critical": 1, "important": 0})
        code, err, out = self._close_in_process(a_critical, "--stale-panel-ok", "--reason", "x")
        self.assertNotIn("Task 001 done.", out, err)
        self.assertIn("counted a Critical", err)



class RoundThreeFindings(_CloseFixture):
    """Task 209's impl panel round 3 (T1-T4): each red on nested 2f54579."""

    def test_t1_a_below_quorum_round_on_top_refuses_even_a_no_judge_close(self):   # codex, sonnet, opus
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(risk="reversible", rounds=1, change="code.py", on_top=rnd(verdict="FAIL", rid="ffffffffffff"))
        self.assertHeld(self._close(), "meets its quorum")
        self.assertClosed(self._close("--owner-ok", "--reason", "the owner read it"))

    def test_t1_an_archived_reviewed_round_refuses_too(self):                     # gemini, codex, grok
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(risk="reversible", rounds=1, change="code.py", archive_pass=True)
        self.assertHeld(self._close(), "meets its quorum")

    def test_t2_a_guarded_file_after_a_post_d6_pass_is_not_tail_certified(self):   # grok, opus
        self.cfg["guard_paths"] = ["tests/guard_fixture.py"]
        (self.d / ".agent" / "models.json").write_text('{"panel": ["pi"], "default_judge": "pi"}', encoding="utf-8")
        self.setup_task(rounds=2, change="code.py")
        self._post_d6("PASS")
        (self.d / "tests" / "guard_fixture.py").write_text("# changed after the PASS\n", encoding="utf-8")
        self.env["PATH"] = "/usr/bin:/bin"
        r = self._close()
        self.assertHeld(r, "under `guard_paths`")

    def test_t3_an_unreadable_ledger_keeps_what_the_panels_recorded(self):        # codex
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="guard/g.py")
        self.cfg["guard_paths"] = []
        self._write_cfg()
        led = self.td / post_d6.RUNS_NAME
        led.unlink()
        led.mkdir()
        # refused at the decision (the judge required), not only by the commit's re-check (mutant M61)
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "cannot be read",
                        "the stop rule needs the post-panel judge")

    def test_t3_a_corrupt_ledger_too(self):
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="guard/g.py")
        self.cfg["guard_paths"] = []
        self._write_cfg()
        with open(self.td / post_d6.RUNS_NAME, "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "corrupt")

    def test_t4_a_guarded_map_file_is_not_free_drift_after_the_pass(self):        # grok
        self.cfg["guard_paths"] = ["MIND_MAP.md"]
        self.setup_task(rounds=2, change="MIND_MAP.md")
        self._post_d6("PASS")
        (self.d / "MIND_MAP.md").write_text("# map, changed again after the PASS\n", encoding="utf-8")
        self.assertHeld(self._close("--stale-panel-ok", "--reason", "x"), "MIND_MAP.md")



class SingleJudgeRunOne(_CloseFixture):
    """Task 209's post-panel single judge, run 1 (codex, 2 Critical): red on nested b106a56."""

    def test_u1_an_untrusted_ledger_at_the_commit_refuses(self):
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="")                       # a FRESH panel; the list recorded with it
        self.cfg["guard_paths"] = []
        self._write_cfg()

        def break_it():
            led = self.td / post_d6.RUNS_NAME
            led.unlink()
            led.mkdir()
            (self.d / "guard" / "g.py").write_text("g = 9\n", encoding="utf-8")
        code, err, out = self._close_in_process(break_it, "--force", "--reason", "x")
        self.assertNotIn("Task 001 done.", out, err)
        self.assertIn("cannot be read", err)

    def test_u3_a_ledger_deleted_or_emptied_during_the_close_keeps_what_was_read(self):   # judge run 2
        for how in ("delete", "empty"):
            with self.subTest(how=how):
                self.setUp()
                self.cfg["guard_paths"] = ["guard/"]
                self.setup_task(rounds=2, change="")
                self.cfg["guard_paths"] = []
                self._write_cfg()

                def lose_it(how=how):
                    led = self.td / post_d6.RUNS_NAME
                    if how == "delete":
                        led.unlink()
                    else:
                        led.write_text("", encoding="utf-8")
                    (self.d / "guard" / "g.py").write_text("g = 9\n", encoding="utf-8")
                code, err, out = self._close_in_process(lose_it, "--force", "--reason", "x")
                self.assertNotIn("Task 001 done.", out, err)
                self.assertIn("guard/g.py", err)

    def test_u4_no_tail_certification_while_the_ledger_is_untrusted(self):      # mutant M64
        # an unguarded docs change after a PASS read from a ledger that cannot be trusted is not certified either
        self.cfg["guard_paths"] = ["guard/"]
        self.setup_task(rounds=2, change="code.py")
        self._post_d6("PASS")
        (self.d / "NOTES.md").write_text("# a note after the PASS\n", encoding="utf-8")
        from tasks import review as R
        from tasks import stop_rule as SRm
        with mock.patch.object(SRm, "ledger_untrusted", lambda d: "the run ledger x cannot be read (PermissionError)"), \
             mock.patch.object(R, "tail_cert_unavailable", lambda *a, **k: ""), \
             mock.patch.object(R, "run_tail_cert_judge", lambda *a, **k: "PASS"):
            code, err, out = self._close_in_process(lambda: None)
        self.assertNotIn("Task 001 done.", out, err)

    def test_u2_an_untrusted_ledger_keeps_the_guards_and_no_tail_certification(self):
        self.cfg["guard_paths"] = ["tests/guard_fixture.py"]
        self.setup_task(rounds=2, change="code.py")
        self._post_d6("PASS")
        (self.d / "tests" / "guard_fixture.py").write_text("# changed after the PASS\n", encoding="utf-8")
        from tasks import review as R
        from tasks import stop_rule as SRm
        with mock.patch.object(SRm, "ledger_untrusted", lambda d: "the run ledger x cannot be read (PermissionError)"), \
             mock.patch.object(R, "tail_cert_unavailable", lambda *a, **k: ""), \
             mock.patch.object(R, "run_tail_cert_judge", lambda *a, **k: "PASS"):
            code, err, out = self._close_in_process(lambda: None)
        self.assertNotIn("Task 001 done.", out, err)


if __name__ == "__main__":
    unittest.main()
