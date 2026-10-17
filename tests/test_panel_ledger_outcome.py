"""A panel's `done` record says how the panel ended and what it found (task 232, PHASE-B item 6).

Until task 232 the panel's reservation was finished with `{"id", "kind": "panel", "status": "done"}` on EVERY exit —
a tamper void, a crash, a judge.md that could not be written — so the run ledger could not tell a delivered round
from an aborted one, and kept none of what the judges found. Now the record carries `outcome` (completed / void /
unsaved / incomplete) and, for a completed round only, `measures`: the round id, the verdict, how many judges
succeeded of how many seated, the quorum, and the Critical / Important findings the judges wrote — counted by the
stop rule's own reading (`stop_rule.round_findings`, which `round_criticals` now delegates to). No reader of the
ledger changes: `status` stays `done` and the new keys collide with none a reader takes. Stdlib unittest.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))

from provider.adapters.claude import ClaudeAdapter  # noqa: E402
from tasks import post_d6, review, stop_rule  # noqa: E402

OPUS = ("1. **Critical** — the close skips verify on a reused key.\n"
        "2. **Important** — the receipt names the wrong command.\n\nCAP: 2/5 reported, exhausted\n")
SONNET = ("1. **Important** [SETTLED] — the owner ruled on this already.\n"
          "2. **Important** — a missing test for the reuse.\n"
          "3. **Minor** — a typo.\n\nCAP: 2/5 reported, exhausted\n")


@contextlib.contextmanager
def _chdir(p):
    old = os.getcwd()
    os.chdir(p)
    try:
        yield
    finally:
        os.chdir(old)


class _Panel(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.agent = self.project / ".agent"
        self.tdir = self.agent / "tasks" / "042-demo"
        self.tdir.mkdir(parents=True)
        (self.tdir / "task.md").write_text("# 042 - demo\n## Status\npending\n## Intent\nx\n"
                                           "## Work Plan\n- [ ] a gate\n", encoding="utf-8")
        (self.agent / "models.json").write_text(json.dumps({"panel": ["claude:opus", "claude:sonnet"],
                                                            "default_judge": "claude:opus"}), encoding="utf-8")
        review._PB_JOURNAL_MOD = None
        review._PB_JOURNAL_LOADED = False
        for p in (mock.patch.object(ClaudeAdapter, "is_available", classmethod(lambda cls: True)),):
            p.start()
            self.addCleanup(p.stop)

    def _run(self, outputs=None, extra_patches=(), expect=None):
        outputs = outputs or {"opus": OPUS, "sonnet": SONNET}
        judge = mock.patch.object(ClaudeAdapter, "run_headless_judge",
                                  lambda self, **kw: outputs[str(kw.get("model"))])
        with judge, contextlib.ExitStack() as stack:
            for p in extra_patches:
                stack.enter_context(p)
            with _chdir(self.project), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                if expect is not None:
                    with self.assertRaises(expect):
                        review.cmd_panel_review(["042", "--models", "claude:opus,claude:sonnet"])
                else:
                    with contextlib.suppress(SystemExit):
                        review.cmd_panel_review(["042", "--models", "claude:opus,claude:sonnet"])

    def _done(self) -> dict:
        lines = (self.tdir / post_d6.RUNS_NAME).read_text(encoding="utf-8").splitlines()
        recs = [json.loads(ln) for ln in lines]
        done = [r for r in recs if r.get("status") == "done"]
        self.assertEqual(len(done), 1, recs)
        self.assertEqual(done[0]["kind"], "panel")
        return done[0]


class CompletedRound(_Panel):
    def test_the_measures_match_judge_md(self):
        self._run()
        rec = self._done()
        self.assertEqual(rec["outcome"], "completed")
        m = rec["measures"]
        jm = (self.tdir / "judge.md").read_text(encoding="utf-8")
        self.assertIn(f"**Round-id:** {m['round_id']}\n", jm)
        self.assertEqual(m["round_id"], rec["id"], "the round is the reservation")
        self.assertIn(f"**PANEL VERDICT: {m['verdict']}** — {m['succeeded']}/{m['seated']} judges succeeded, "
                      f"quorum {m['quorum']}", jm)
        self.assertEqual((m["verdict"], m["succeeded"], m["seated"]), ("PASS", 2, 2))
        # opus: 1 Critical + 1 Important; sonnet: one Important [SETTLED] (not counted) + 1 Important; Minor never
        self.assertEqual((m["critical"], m["important"]), (1, 2))

    def test_the_count_is_the_stop_rules_own(self):
        self._run()
        m = self._done()["measures"]
        rounds = stop_rule._read_rounds(self.tdir)
        self.assertEqual(stop_rule.round_criticals(rounds[0]), m["critical"])
        self.assertEqual(stop_rule.round_findings(rounds[0]), {"critical": m["critical"], "important": m["important"]})

    def test_words_outside_the_judges_quotes_are_not_counted(self):
        # the writer's own triage frame and headings are unquoted; only a judge's quoted lines count (format 2)
        rnd = {"format": 2, "body": "═══════\n**Critical** in the writer's frame\n> 1. **Important** — a judge's\n"}
        self.assertEqual(stop_rule.round_findings(rnd), {"critical": 0, "important": 1})
        self.assertEqual(stop_rule.round_criticals(rnd), 0)

    def test_a_count_that_fails_keeps_the_verdict_and_says_so(self):
        """Impl round 1 (sonnet, Minor): the counts' failure must not erase the verdict fields, nor pass silently."""
        def _boom(rnd):
            raise ValueError("unreadable round")
        self._run(extra_patches=[mock.patch.object(stop_rule, "round_findings", _boom)])
        m = self._done()["measures"]
        self.assertEqual((m["verdict"], m["succeeded"], m["seated"]), ("PASS", 2, 2))
        self.assertNotIn("critical", m)
        self.assertEqual(m["counts_error"], "unreadable round")

    def test_below_quorum_is_a_completed_fail(self):
        (self.agent / "config.json").write_text(json.dumps({"panel_quorum": 2}), encoding="utf-8")
        self._run(outputs={"opus": OPUS, "sonnet": "(FAILED — exit 1)\n[stderr tail]\nboom"})
        rec = self._done()
        self.assertEqual(rec["outcome"], "completed")
        self.assertEqual((rec["measures"]["verdict"], rec["measures"]["succeeded"], rec["measures"]["seated"],
                          rec["measures"]["quorum"]), ("FAIL", 1, 2, 2))


class OtherExits(_Panel):
    def test_a_tamper_void_has_no_measures(self):
        self._run(extra_patches=[mock.patch.object(review, "_detect_tamper_full",
                                                   lambda pp, t, b: {"mutations": ["dirty"], "cautions": []})])
        rec = self._done()
        self.assertEqual(rec["outcome"], "void")
        self.assertNotIn("measures", rec)

    def test_an_unwritable_judge_md_is_unsaved(self):
        def _boom(*a, **k):
            raise OSError("read-only")
        self._run(extra_patches=[mock.patch("tasks.core.stack_judge_round", _boom)])
        rec = self._done()
        self.assertEqual(rec["outcome"], "unsaved")
        self.assertNotIn("measures", rec)

    def test_a_crash_after_the_reservation_is_incomplete(self):
        def _crash(*a, **k):
            raise RuntimeError("the snapshot broke")
        self._run(extra_patches=[mock.patch.object(review, "_snapshot_repo_state", _crash)], expect=RuntimeError)
        rec = self._done()
        self.assertEqual(rec["outcome"], "incomplete")
        self.assertNotIn("measures", rec)


class ReadersUnchanged(unittest.TestCase):
    """The ledger's readers take the post-D6 runs (`panel` key, `kind` not panel) and the panel reservations' risk,
    guards and caps: the new keys change none of their answers."""

    def _ledger(self, d: Path, with_outcome: bool) -> None:
        done: dict = {"id": "p1", "kind": "panel", "status": "done", "ts": "2026-10-17T10:10:00+00:00"}
        if with_outcome:
            done.update({"outcome": "completed", "measures": {"round_id": "p1", "verdict": "FAIL", "succeeded": 1,
                                                              "seated": 5, "quorum": 3, "critical": 4,
                                                              "important": 9}})
        rows = [{"id": "p1", "kind": "panel", "status": "reserved", "mode": "impl", "risk": "assertive",
                 "rounds_cap": {"assertive": 2, "irreversible": 2, "reversible": 1}, "guard_paths": ["g/"],
                 "expires_after": 900.0, "ts": "2026-10-17T10:00:00+00:00"},
                done,
                {"id": "r1", "panel": "p1", "status": "reserved", "owner_ok": None, "expires_after": 900.0,
                 "ts": "2026-10-17T10:20:00+00:00"},
                {"id": "r1", "panel": "p1", "status": "done", "verdict": "PASS", "critical": 0, "important": 0,
                 "ts": "2026-10-17T10:25:00+00:00"},
                {"id": "r2", "panel": "p1", "status": "reserved", "owner_ok": None, "expires_after": 900.0,
                 "ts": "2026-10-17T10:30:00+00:00"},
                {"id": "r2", "panel": "p1", "status": "done", "verdict": "FAIL", "critical": 1, "important": 0,
                 "ts": "2026-10-17T10:35:00+00:00"}]
        (d / post_d6.RUNS_NAME).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_the_answers_are_the_same(self):
        got = []
        for with_outcome in (False, True):
            with tempfile.TemporaryDirectory() as t:
                d = Path(t)
                self._ledger(d, with_outcome)
                runs = post_d6.read_runs(d)
                got.append((post_d6.gate_run(runs, "p1", None), post_d6.receipt_line(runs, "p1"),
                            stop_rule.panel_record(d)))
        self.assertEqual(got[0], got[1])
        self.assertEqual(got[0][0][:2], (False, 3), "run 3 needs the owner: the post-D6 runs alone are counted")


if __name__ == "__main__":
    unittest.main()
