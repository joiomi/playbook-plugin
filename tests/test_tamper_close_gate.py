#!/usr/bin/env python3
"""A DEGRADED tamper guard cannot certify a high-consequence close (task 059,
plan panel P2 — opus).

Task 059 made a degraded guard (git could not fully run) advisory on the panel
and single-judge paths: the paid verdict is KEPT and the round header records
`**Tamper guard:** degraded — …`. That header is also what the close accepts
as impl-panel evidence — so on a host without bwrap (uncontained: the guard is
the only defense) an assertive/irreversible close would otherwise proceed on a PASS
whose tamper-freedom was never verified. These tests pin the other half: the
close treats such a round as STALE — task 343: one of its causes, blocked with
the same ways out (another impl round / `--force --reason`, recorded), advisory
for `reversible`, and a round WITHOUT the line (pre-059) stays clean.

Pure stdlib unittest. Run: python3 -m unittest tests.test_tamper_close_gate
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins/playbook"
sys.path.insert(0, str(PLUGIN))

from tasks.core import (  # noqa: E402
    format_verify_receipt, freshness_gate_decision, parse_judge_rounds,
    tree_state_fingerprint,
)


def _git(d, *args):
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)


def _repo() -> Path:
    d = Path(tempfile.mkdtemp())
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "x@y.z")
    _git(d, "config", "user.name", "x")
    (d / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "seed")
    return d


DEGRADED = ("**Tamper guard:** degraded — content-hash guard degraded: could not "
            "enumerate dirty files at close (`git status -z` failed)")


class ParserReadsTheLine(unittest.TestCase):
    def _round(self, guard_line):
        return (f"# Panel Impl Review — t\n\n**PANEL VERDICT: PASS** — 5/5\n"
                f"**Commit:** abc\n{guard_line}**Tree-state:** deadbeef\n\nbody\n")

    def test_degraded_is_parsed_with_detail(self):
        r = parse_judge_rounds(self._round(DEGRADED + "\n"))[0]
        self.assertEqual(r["tamper_guard"], "degraded")
        self.assertIn("git status -z", r["tamper_detail"])

    def test_clean_is_parsed(self):
        r = parse_judge_rounds(self._round("**Tamper guard:** clean\n"))[0]
        self.assertEqual(r["tamper_guard"], "clean")

    def test_absent_line_is_none_not_degraded(self):
        r = parse_judge_rounds(self._round(""))[0]
        self.assertIsNone(r["tamper_guard"])

    def test_line_after_the_judge_separator_is_ignored(self):
        # A prompt-injected JUDGE cannot forge (or clear) the guard receipt: only
        # the orchestrator-authored header region counts.
        body = self._round("") + "\n" + "═" * 60 + "\n**Tamper guard:** clean\n"
        r = parse_judge_rounds(body)[0]
        self.assertIsNone(r["tamper_guard"])


class GateDecision(unittest.TestCase):
    """The verdict (task 343, plan panel P11): a degraded guard is a STALE cause, not a verdict of its own — the close
    holds it as it holds any STALE round, and a reversible close is only told."""

    def fresh(self, guard):
        from tasks.core import round_freshness
        rnd = {"tree_state": "a" * 12, "tamper_guard": guard, "tamper_detail": "git status -z failed"}
        return round_freshness(Path("."), rnd, git_here=True, now_fp="a" * 12)

    def test_degraded_reads_stale_with_its_cause(self):
        f = self.fresh("degraded")
        self.assertEqual(f["verdict"], "STALE")
        self.assertEqual(f["cause"], "the round's tamper guard was degraded (git status -z failed)")

    def test_degraded_blocks_every_high_risk_and_tells_a_reversible_one(self):
        for risk, held in (("assertive", True), ("irreversible", True), ("unclassified", True), ("reversible", False)):
            allowed, why = freshness_gate_decision(risk=risk, panel_required=True, evidence_carries=True,
                                                   freshness=self.fresh("degraded"), force=False)
            self.assertEqual(allowed, not held, risk)
            if held:
                self.assertIn("tamper guard was degraded", why)

    def test_clean_guard_changes_nothing(self):
        self.assertEqual(self.fresh("clean")["verdict"], "FRESH")
        self.assertEqual(self.fresh(None)["verdict"], "FRESH")


class Receipt(unittest.TestCase):
    def test_receipt_names_the_degraded_guard(self):
        txt = format_verify_receipt([], "abc", "assertive", reason="inspected by hand", freshness={
            "verdict": "STALE", "round_fp": "abc", "now_fp": "abc",
            "cause": "the round's tamper guard was degraded (git status -z failed)"})
        self.assertIn("STALE: the round's tamper guard was degraded (git status -z failed)", txt)
        self.assertIn("inspected by hand", txt)


class ClosePath(unittest.TestCase):
    """End to end through the real CLI: a PASS impl round whose header says the
    guard was degraded, on a tree that did NOT change (so freshness alone would
    read FRESH)."""

    def _setup(self, *, risk, guard_line=DEGRADED + "\n"):
        d = _repo()
        (d / ".agent").mkdir(exist_ok=True)
        (d / ".agent" / "config.json").write_text(
            json.dumps({"panel_required_for": ["assertive", "irreversible"]}), encoding="utf-8")
        td = d / ".agent" / "tasks" / "001-t"
        td.mkdir(parents=True)
        (td / "task.md").write_text(
            f"# 001 - T\n\n## Status\npending\n\n## Risk\n{risk}\n\n## Work Plan\n- [x] G1: do it\n",
            encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="pid-t059")
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "1"],
                           cwd=d, env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        fp = tree_state_fingerprint(d)
        (td / "judge.md").write_text(
            f"# Panel Impl Review — task 1\n\n**PANEL VERDICT: PASS** — 4/4, quorum 3\n"
            f"**Commit:** abc\n{guard_line}**Tree-state:** {fp}\n\nbody\n", encoding="utf-8")
        return d, td, env

    def _close(self, d, env, *flags):
        return subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done", *flags],
                              cwd=d, env=env, capture_output=True, text=True, timeout=120)

    def test_assertive_degraded_blocks_then_closes_with_recorded_reason(self):
        d, td, env = self._setup(risk="assertive")
        r = self._close(d, env)
        self.assertNotIn("Task 001 done.", r.stdout, r.stdout + r.stderr)
        self.assertIn("the round's tamper guard was degraded", r.stdout + r.stderr)
        r = self._close(d, env, "--force", "--reason", "git -z quirk on this host; tree inspected")
        self.assertIn("Task 001 done.", r.stdout, r.stderr)
        receipt = (td / "task.md").read_text(encoding="utf-8")
        self.assertIn("the round's tamper guard was degraded", receipt)
        self.assertIn("git -z quirk on this host; tree inspected", receipt)

    def test_reversible_degraded_closes_with_an_advisory(self):
        d, td, env = self._setup(risk="reversible")
        r = self._close(d, env)
        self.assertIn("Task 001 done.", r.stdout, r.stderr)
        self.assertIn("tamper guard", (r.stdout + r.stderr).lower())

    def test_pre_059_round_without_the_line_still_closes(self):
        d, td, env = self._setup(risk="assertive", guard_line="")
        r = self._close(d, env)
        self.assertIn("Task 001 done.", r.stdout, r.stderr)
        self.assertNotIn("TAMPER-GUARD-DEGRADED", r.stdout + r.stderr)


class CloseGitProbeHonoursCeilings(unittest.TestCase):
    """059 impl-panel r2 (sonnet #2): the close re-implemented the `.git`
    ancestor walk inline, without the ceiling/mount rules W5 added to
    `review._in_git_repo` — the same T009#2 false positive at a second call
    site. Both paths must now use the ONE shared helper."""

    def test_core_exports_the_shared_probe(self):
        from tasks.core import in_git_repo
        from tasks.review import _in_git_repo
        self.assertIs(_in_git_repo, in_git_repo)

    def test_shared_probe_honours_the_ceiling(self):
        from tasks.core import in_git_repo
        from unittest import mock
        d = _repo()
        app = d / "app"
        app.mkdir()
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(d)}):
            self.assertFalse(in_git_repo(app))
        self.assertTrue(in_git_repo(app))

    def test_lifecycle_uses_it(self):
        import inspect
        from tasks import lifecycle
        src = inspect.getsource(lifecycle)
        self.assertIn("in_git_repo(", src)
        self.assertNotIn('if (_gp / ".git").exists():', src,
                         "the close still carries its own naive ancestor walk")


class SingleJudgeDegradedAtClose(unittest.TestCase):
    """059 impl-panel r1 (sonnet #1/#3): the close-time TAMPER-GUARD-DEGRADED
    protection read only PANEL rounds, so a single-judge review that ran with a
    degraded guard — the common case for `reversible` work, where
    `panel_required_for` demands no panel — produced ZERO close-time signal.
    It now surfaces as an advisory note and a receipt clause."""

    MARK = ("[context] full task.md + mind map delivered (no truncation)\n"
            "[tamper guard] degraded — content-hash guard degraded: `git status -z` failed\n\n"
            "1. **Note** — fine.\n")


    def _task(self, log_body=None, name="judge.log"):   # the REAL claude log name
        d = _repo()
        td = d / ".agent" / "tasks" / "001-t"
        td.mkdir(parents=True)
        tf = td / "task.md"
        tf.write_text("# 001 - T\n\n## Status\npending\n\n## Risk\nreversible\n\n"
                      "## Work Plan\n- [x] G1: do it\n", encoding="utf-8")
        if log_body is not None:
            (td / name).write_text(log_body, encoding="utf-8")
        return d, td, tf


    def test_name_list_matches_reviews_own_mapping(self):
        from tasks.core import judge_log_names
        from tasks.review import _judge_log_name
        for backend in ("claude", "codex", "antigravity", "grok", "pi"):
            self.assertIn(_judge_log_name(backend), judge_log_names(), backend)


if __name__ == "__main__":
    unittest.main()
