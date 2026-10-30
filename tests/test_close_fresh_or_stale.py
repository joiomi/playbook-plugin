#!/usr/bin/env python3
"""The close's review evidence is FRESH or STALE (task 343, PLAN-PLAYBOOK-2.md 4.1 a; DESIGN-PLAYBOOK-2.md §2 and
§3.3 point 12, approved R14 and R17).

The newest impl round either still describes the code — its `**Tree-state:**` stamp equals the tree now (or only the
task's own records were committed since) and its tamper guard was not degraded — or it does not. A stale assertive,
irreversible or unclassified close blocks and names its ways out by the round cap: another impl round, a round past
the cap with the owner's word, `--force --reason`. The post-panel judge as close evidence, tail certification,
`--stale-panel-ok`, `work done --owner-ok`, `guard_paths`, `stale_panel_requires_post_d6`, `fingerprint_exclude` and
the verdicts NO-STAMP / UNREADABLE / TAMPER-GUARD-DEGRADED / EXCLUDE-COVERS-CODE are gone.

Real temporary git projects, the CLI as `python3 -m tasks.cli`; the commit's re-check in this process. Each test
names the row of task 343's contract it proves. Pure stdlib unittest.
Run: python3 -m unittest tests.test_close_fresh_or_stale
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))

from tasks.core import build_panel_snapshot, format_panel_snapshot_line, tree_state_fingerprint  # noqa: E402

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
CHANGED = "the code changed since the round"


def _round(fp: str, project: Path, *, stamp: bool = True, extra: str = "", rid: str = "0123456789ab") -> str:
    """An impl panel round that met its quorum, as review.py writes its head."""
    head = (f"**Round-id:** {rid}\n\n"
            + (f"**Tree-state:** {fp}\n\n{format_panel_snapshot_line(build_panel_snapshot(project, fp))}\n\n"
               if (stamp and fp) else "")
            + extra)
    return ("# Panel Impl Review — .agent/tasks/001-t/task.md\n**Round-format:** 2\n\n"
            "**PANEL VERDICT: PASS** — 5/5 judges succeeded, quorum 3\n\n" + head + "\nbody\n")


class _Close(unittest.TestCase):
    """A git project with `code.py`, `docs/`, `tests/` and `MIND_MAP.md`; a task of the given risk activated with its
    gates ticked; judge.md holding one impl PASS round stamped with the tree at that moment."""

    def g(self, *args, cwd=None):
        return subprocess.run([*GIT, *args], cwd=cwd or self.d, check=True, capture_output=True, text=True)

    def setup_task(self, risk="assertive", *, stamp=True, cfg=None, git=True, round_extra="", rounds=1):
        self.d = Path(tempfile.mkdtemp(prefix="pb343-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = Path(tempfile.mkdtemp(prefix="pb343-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.d / "code.py").write_text("x = 1\n", encoding="utf-8")
        (self.d / "docs").mkdir()
        (self.d / "docs" / "guide.md").write_text("a guide\n", encoding="utf-8")
        (self.d / "tests").mkdir()
        (self.d / "tests" / "test_x.py").write_text("X = 1\n", encoding="utf-8")
        (self.d / "MIND_MAP.md").write_text("[1] **Code** - x\n", encoding="utf-8")
        (self.d / ".agent").mkdir()
        conf = {"panel_required_for": "all"}
        conf.update(cfg or {})
        (self.d / ".agent" / "config.json").write_text(json.dumps(conf), encoding="utf-8")
        if git:
            subprocess.run(["git", "init", "-q"], cwd=self.d, check=True)
            (self.d / ".gitignore").write_text(".agent/sessions/\n.agent/journal/\n", encoding="utf-8")
            self.g("add", "-A")
            self.g("commit", "-qm", "seed")
        self.td = self.d / ".agent" / "tasks" / "001-t"
        self.td.mkdir(parents=True)
        (self.td / "task.md").write_text(
            f"# 001 - T\n\n## Status\npending\n\n## Risk\n{risk}\n\n## Work Plan\n- [x] G1: do it — done\n",
            encoding="utf-8")
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        fp = tree_state_fingerprint(self.d) if git else ""
        text = "\n".join(_round(fp, self.d, stamp=stamp, extra=round_extra, rid=f"{k:012x}") for k in range(rounds))
        (self.td / "judge.md").write_text(text, encoding="utf-8")
        return fp

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE", "GIT_"))}
        env.update({"PYTHONPATH": str(PLUGIN), "HOME": str(self.home), "PLAYBOOK_SESSION_ID": "pid-close-343"})
        return env

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.d, env=self.env(),
                              capture_output=True, text=True, timeout=120)

    def close(self, *flags):
        r = self.cli("work", "done", *flags)
        return r, r.stdout + r.stderr

    def in_process(self, act, *flags):
        """The close in this process, `act` run when the receipt is composed — after the verdict, before the
        commit (as tests/test_red_receipt_part7.py does)."""
        from tasks import core, lifecycle
        real = core.format_verify_receipt

        def meanwhile(*a, **k):
            act()
            return real(*a, **k)
        out = io.StringIO()
        cwd = os.getcwd()
        code = 0
        with mock.patch.object(core, "format_verify_receipt", meanwhile), \
                mock.patch.dict(os.environ, self.env(), clear=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            os.chdir(self.d)
            try:
                lifecycle.cmd_work(["done", *flags])
            except SystemExit as e:
                code = e.code or 0
            finally:
                os.chdir(cwd)
        return code, out.getvalue()

    def task(self):
        return (self.td / "task.md").read_text(encoding="utf-8")

    def done(self):
        return "## Status\ndone" in self.task()

    def assert_blocked_stale(self, out, cause, *, round_left=True):
        self.assertIn("Blocked: cannot close task 001", out)
        self.assertIn(cause, out)
        self.assertIn('tasks work done --force --reason "', out)
        if round_left:
            self.assertIn("another impl round: tasks panel-review 001 --mode impl", out)
        self.assertNotIn("--stale-panel-ok", out)
        self.assertNotIn("post-panel judge", out)
        self.assertFalse(self.done(), out)


class Fresh(_Close):

    def test_a_fresh_round_closes(self):
        """Row 1: the stamp equals the tree now — no flag, the receipt says FRESH."""
        self.setup_task()
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(self.done(), out)
        self.assertIn("FRESH", self.task())

    def test_records_committed_after_the_round_are_fresh(self):
        """Row 2: only the task's own records committed after the round."""
        self.setup_task()
        self.g("add", "-A", ".agent/tasks")
        self.g("commit", "-qm", "the task's records")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("FRESH", self.task())

    def test_outside_git_is_not_judged(self):
        """Row 9: no git and no stamp — one line, the close is not held."""
        self.setup_task(git=False)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("not judged: not a git repository", out)

    def test_old_post_panel_runs_are_not_read(self):
        """Row 20: a 1.6.x ledger's post-panel runs — the newest a FAIL that counted a Critical — touch no FRESH
        close: a single judge's Critical never holds a close."""
        self.setup_task()
        (self.td / "post-panel-reviews.jsonl").write_text(
            json.dumps({"id": "a1", "panel": "000000000000", "status": "reserved"}) + "\n"
            + json.dumps({"id": "a1", "status": "done", "verdict": "FAIL", "critical": 1, "important": 2}) + "\n",
            encoding="utf-8")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("post-D6", self.task())
        self.assertNotIn("post-panel", out)


class Stale(_Close):

    def test_a_code_change_blocks_each_high_risk(self):
        """Row 3: assertive, irreversible and unclassified each block and name the ways out."""
        for risk in ("assertive", "irreversible", "unclassified"):
            with self.subTest(risk=risk):
                self.setup_task(risk)
                (self.d / "code.py").write_text("x = 2\n", encoding="utf-8")
                r, out = self.close()
                self.assertNotEqual(r.returncode, 0, out)
                self.assert_blocked_stale(out, CHANGED)

    def test_a_docs_change_blocks_and_starts_no_judge(self):
        """Row 4: a docs or a tests change is no exception, and the close calls no judge (no tail certification)."""
        for rel in ("docs/guide.md", "tests/test_x.py"):
            with self.subTest(rel=rel):
                self.setup_task()
                (self.d / rel).write_text("changed\n", encoding="utf-8")
                r, out = self.close()
                self.assert_blocked_stale(out, CHANGED)
                self.assertNotIn("tail certification", out)
                self.assertNotIn("tail-cert", out.lower())

    def test_no_stamp_and_unreadable_read_stale(self):
        """Row 5: no stamp; git cannot fingerprint the tree now; a stamped round whose repository git no longer
        finds — each STALE with its cause."""
        self.setup_task(stamp=False)
        r, out = self.close()
        self.assert_blocked_stale(out, "the round has no tree stamp")
        self.assertNotIn("NO-STAMP", out)
        self.setup_task()
        (self.d / ".git" / "index").write_bytes(b"not an index")
        r, out = self.close()
        self.assert_blocked_stale(out, "git cannot fingerprint the tree now")
        self.assertNotIn("UNREADABLE", out)
        self.setup_task()
        (self.d / ".git").rename(self.d.parent / (self.d.name + "-git-moved"))
        self.addCleanup(shutil.rmtree, self.d.parent / (self.d.name + "-git-moved"), True)
        r, out = self.close()
        self.assert_blocked_stale(out, "git cannot fingerprint the tree now")

    def test_a_degraded_guard_reads_stale(self):
        """Row 11: a round whose tamper guard was degraded is STALE with that cause — no verdict of its own."""
        self.setup_task(round_extra="**Tamper guard:** degraded — the guard could not fully run\n\n")
        r, out = self.close()
        self.assert_blocked_stale(out, "the round's tamper guard was degraded")
        self.assertNotIn("TAMPER-GUARD-DEGRADED", out)

    def test_force_closes_and_the_receipt_says_stale(self):
        """Row 6."""
        self.setup_task()
        (self.d / "code.py").write_text("x = 2\n", encoding="utf-8")
        r, out = self.close("--force", "--reason", "the owner read the change")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("STALE", self.task())
        self.assertIn("the owner read the change", self.task())

    def test_a_reversible_stale_close_is_advisory(self):
        """Row 7."""
        self.setup_task("reversible")
        (self.d / "code.py").write_text("x = 2\n", encoding="utf-8")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("STALE", self.task())

    def test_fingerprint_exclude_is_ignored(self):
        """Row 10: a path the config's `fingerprint_exclude` lists still moves the tree."""
        self.setup_task(cfg={"fingerprint_exclude": ["docs/"]})
        (self.d / "docs" / "guide.md").write_text("changed\n", encoding="utf-8")
        r, out = self.close()
        self.assert_blocked_stale(out, CHANGED)
        self.assertNotIn("EXCLUDE-COVERS-CODE", out)

    def test_a_mind_map_change_after_the_round_is_stale(self):
        """Row 23: the mind map changed after the round is no record — left in the tree, or committed with the
        task's records (the uncommitted case: task 343's mutant M23)."""
        for commit in (False, True):
            with self.subTest(commit=commit):
                self.setup_task()
                (self.d / "MIND_MAP.md").write_text("[1] **Code** - x, updated\n", encoding="utf-8")
                if commit:
                    self.g("add", "-A")
                    self.g("commit", "-qm", "the records and the map")
                r, out = self.close()
                self.assert_blocked_stale(out, CHANGED)

    def test_at_the_cap_the_block_names_the_owners_round(self):
        """Row 24: an assertive task with its two rounds spent — the block offers a round past the cap with the
        owner's word, not another round; the panel's own refusal names nothing removed."""
        self.setup_task(rounds=2)
        (self.d / "code.py").write_text("x = 2\n", encoding="utf-8")
        r, out = self.close()
        self.assert_blocked_stale(out, CHANGED, round_left=False)
        self.assertNotIn("another impl round", out)
        self.assertIn('tasks panel-review 001 --mode impl --owner-ok --reason "', out)
        from tasks import stop_rule
        rounds = stop_rule.impl_rounds(self.td)
        ok, why = stop_rule.panel_decision("assertive", rounds, stop_rule.resolve_rounds({}), task="001")
        self.assertFalse(ok)
        for gone in ("guard_paths", "post-panel", "single judge"):
            self.assertNotIn(gone, why)

    def _single_only(self, risk, ledger_text):
        """A task with no saved panel round, one single-judge impl review, and the given run ledger."""
        self.setup_task(risk, cfg={"panel_required_for": ["assertive", "irreversible"]})
        (self.td / "judge.md").unlink()
        (self.td / "judge-single.md").write_text("## Single-judge impl review — 2026-10-30 09:00\n\n> fine\n",
                                                 encoding="utf-8")
        ledger = self.td / "post-panel-reviews.jsonl"
        ledger.write_text(ledger_text, encoding="utf-8")
        return ledger

    def test_a_bad_ledger_holds_the_close_and_names_itself(self):
        """Row 32 (impl round 1, I2, as the post-panel judge's run 1 corrected it): a run ledger that cannot be
        trusted holds the close to `unclassified` whatever task.md says or judge.md holds — a panel that reserved but
        saved no round has recorded its risk there too — and every block it causes names the ledger, never only
        "set ## Risk"."""
        self._single_only("reversible", json.dumps({"id": "s1", "kind": "single", "status": "reserved"}) + "\n"
                          + '{"id": "s1", "sta\n')
        r, out = self.close()
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("Blocked: cannot close task 001", out)
        self.assertIn("run ledger", out)
        self.assertFalse(self.done(), out)

    def test_an_unsaved_panel_keeps_its_risk_through_a_bad_ledger(self):
        """Row 32 (the post-panel judge's run 1, codex, reproduced twice): an assertive panel reserved — its risk
        recorded — and saved no round; the ledger then spoiled and `## Risk` lowered to reversible. The close holds."""
        for spoil in ("corrupt", "unreadable"):
            with self.subTest(spoil=spoil):
                ledger = self._single_only("reversible", json.dumps(
                    {"id": "p1", "kind": "panel", "mode": "impl", "status": "reserved", "risk": "assertive"}) + "\n")
                if spoil == "corrupt":
                    with open(ledger, "a", encoding="utf-8") as fh:
                        fh.write('{"id": "p1", "sta\n')
                else:
                    ledger.chmod(0)
                    self.addCleanup(ledger.chmod, 0o644)
                r, out = self.close()
                self.assertNotEqual(r.returncode, 0, out)
                self.assertIn("run ledger", out)
                self.assertFalse(self.done(), out)

    def test_an_unreadable_ledger_keeps_the_gate(self):
        """Row 27: an assertive panel recorded in the run ledger, `## Risk` lowered to reversible, the ledger made
        untrustworthy — the recorded risk is unknown, so the stale close still blocks."""
        for spoil in ("corrupt", "unreadable"):
            with self.subTest(spoil=spoil):
                self.setup_task()
                ledger = self.td / "post-panel-reviews.jsonl"
                ledger.write_text(json.dumps({"id": "p1", "kind": "panel", "mode": "impl", "status": "done",
                                              "risk": "assertive"}) + "\n", encoding="utf-8")
                t = self.task().replace("## Risk\nassertive", "## Risk\nreversible")
                (self.td / "task.md").write_text(t, encoding="utf-8")
                if spoil == "corrupt":
                    with open(ledger, "a", encoding="utf-8") as fh:
                        fh.write('{"id": "p2", "kind": "pan\n')
                else:
                    ledger.chmod(0)
                    self.addCleanup(ledger.chmod, 0o644)
                (self.d / "code.py").write_text("x = 2\n", encoding="utf-8")
                r, out = self.close()
                self.assertNotEqual(r.returncode, 0, out)
                self.assertIn("Blocked: cannot close task 001", out)
                self.assertFalse(self.done(), out)

    def test_a_risk_lowered_after_the_panel_still_blocks(self):
        """Row 27: an assertive panel recorded in the run ledger, `## Risk` lowered to reversible after it — the
        stricter of the two holds the stale close (task 209's P7, kept by task 343)."""
        self.setup_task()
        (self.td / "post-panel-reviews.jsonl").write_text(json.dumps(
            {"id": "p1", "kind": "panel", "mode": "impl", "status": "done", "risk": "assertive"}) + "\n",
            encoding="utf-8")
        (self.td / "task.md").write_text(self.task().replace("## Risk\nassertive", "## Risk\nreversible"),
                                         encoding="utf-8")
        (self.d / "code.py").write_text("x = 2\n", encoding="utf-8")
        r, out = self.close()
        self.assert_blocked_stale(out, CHANGED)

    def test_the_receipt_names_the_cause(self):
        """Row 29: a forced no-stamp close's receipt says why it was STALE; a records-only close's says what moved."""
        self.setup_task(stamp=False)
        r, out = self.close("--force", "--reason", "no stamp, read by hand")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("the round has no tree stamp", self.task())
        self.setup_task()
        self.g("add", "-A", ".agent/tasks")
        self.g("commit", "-qm", "the task's records")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("only workflow records changed since the round", self.task())

    def test_the_note_and_the_receipt_name_the_cause(self):
        """Row 34 (impl round 1, I7, I9): a STALE close that goes on prints its own cause, never "the code changed"
        for a round with no stamp; the records-only receipt names workflow records, not the task's own."""
        self.setup_task(stamp=False)
        r, out = self.close("--force", "--reason", "no stamp, read by hand")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("the round has no tree stamp", out)
        self.assertNotIn("code state changed", out)
        self.setup_task()
        self.g("add", "-A", ".agent/tasks")
        self.g("commit", "-qm", "the task's records")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn(".agent tasks, sessions, journal, chat log, rulings", self.task())
        self.assertNotIn("task's own records", self.task())


class Flags(_Close):

    def test_gone_and_unknown_options_are_refused(self):
        """Row 8: refused before anything runs; nothing written; a reason's value is no option."""
        for flags in (("--stale-panel-ok", "--reason", "r"), ("--owner-ok", "--reason", "r"), ("--bogus",),
                      ("--force", "--reason=why")):
            with self.subTest(flags=flags):
                self.setup_task()
                (self.d / "code.py").write_text("x = 2\n", encoding="utf-8")
                before = self.task()
                r, out = self.close(*flags)
                self.assertNotEqual(r.returncode, 0, out)
                self.assertIn("unknown option", out)
                self.assertEqual(self.task(), before)
        from tasks import cli
        self.assertNotIn("--stale-panel-ok", cli._USAGE.get("work", ""))
        self.assertNotIn("--owner-ok", cli._USAGE.get("work", ""))


    def test_a_reason_without_its_value_is_refused(self):
        """Row 31 (impl round 1, I1): `--reason` with no value — last, or before another option — is refused before
        anything runs, forced or not; nothing is written."""
        for flags in (("--reason",), ("--reason", "--force"), ("--force", "--reason")):
            with self.subTest(flags=flags):
                self.setup_task()
                before = self.task()
                r, out = self.close(*flags)
                self.assertNotEqual(r.returncode, 0, out)
                self.assertIn("--reason", out)
                self.assertEqual(self.task(), before)
                self.assertFalse(self.done(), out)


class Commit(_Close):

    def test_a_tree_that_moves_during_the_close_refuses(self):
        """Row 12: code written after the verdict, before the commit, refuses the close."""
        self.setup_task()
        code, out = self.in_process(lambda: (self.d / "code.py").write_text("x = 3\n", encoding="utf-8"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("changed while this close was running", out)
        self.assertFalse(self.done())

    def test_force_covers_a_tree_that_moves(self):
        """Row 28: under `--force` the commit re-checks nothing of the removed rules — an irreversible task's tree
        moving during the close closes."""
        self.setup_task("irreversible")
        code, out = self.in_process(lambda: (self.d / "code.py").write_text("x = 3\n", encoding="utf-8"),
                                    "--force", "--reason", "the owner's call")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.done(), out)


class Excludes(_Close):

    def test_every_caller_uses_the_same_specs(self):
        """Row 25: with `fingerprint_exclude` set, the fingerprint, records-only's snapshot, `tasks red` and the
        fixed copy leave out `.agent` only."""
        self.setup_task(cfg={"fingerprint_exclude": ["docs/"]})
        from tasks import core, red
        cfg = core.load_config(self.d)
        self.assertEqual(core._fingerprint_exclude_pathspecs(cfg), [":(exclude).agent"])
        before = (tree_state_fingerprint(self.d), red.tree_fingerprint(self.d, cfg))
        snap = build_panel_snapshot(self.d, before[0])
        self.assertEqual(snap.get("exclude"), [":(exclude).agent"])
        (self.d / "docs" / "guide.md").write_text("changed\n", encoding="utf-8")
        after = (tree_state_fingerprint(self.d), red.tree_fingerprint(self.d, cfg))
        self.assertNotEqual(before[0], after[0])
        self.assertNotEqual(before[1], after[1])


class Template(unittest.TestCase):

    def test_pre_review_comes_before_the_review(self):
        """Row 22: the tests, the debug artifacts and the mind map come before the round that reads them."""
        from tasks.template import render_template
        for kind, review in (("build", "## Implementation Review"), ("bugfix", "## Implementation Review"),
                             ("light", "## Review")):
            with self.subTest(kind=kind):
                text = render_template(7, "x", kind)
                self.assertIn("## Pre-review", text)
                self.assertLess(text.index("## Pre-review"), text.index(review), kind)


class Keys(unittest.TestCase):

    def test_no_code_reads_the_gone_keys(self):
        """Row 30: no plugin code holds `guard_paths`, `stale_panel_requires_post_d6` or `fingerprint_exclude` —
        neither a read nor a write (the drift test cannot see a key read through a constant)."""
        gone = re.compile(r"guard_paths|stale_panel_requires_post_d6|fingerprint_exclude(?!_pathspecs)")
        hits = []
        for p in sorted(PLUGIN.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix in (".md", ".json", ".txt"):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits += [f"{p.relative_to(PLUGIN)}:{i}" for i, ln in enumerate(text.splitlines(), 1) if gone.search(ln)]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
