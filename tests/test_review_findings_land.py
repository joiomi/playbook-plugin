"""Task 117 (PLAN S11, gauntlet 2 item 28): a single-judge review's findings always land.

When task.md has no place for them — a light/quick task has no `## Implementation Review`
section, a compacted or hand-edited one has neither its placeholder nor the sentinels — the CLI
still refuses to guess where to write into task.md (a wrong insertion could destroy gates), but
it used to stop there: "paste them in by hand", exit 1, although the review had succeeded. Now the
review is appended to the task's judge-single.md (not judge.md: that file stacks panel rounds and
archives the oldest), quoted line by line, and the command exits 0.

A quoted line can never be read as triage: the SETTLED extractor (`post_d6.extract_rejected_findings`)
takes only bullet lines, so a judge writing `- **x** — REJECT` cannot plant a settled finding.

Run: python3 -m unittest tests.test_review_findings_land
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "playbook"))

from provider import sandbox  # noqa: E402
from tasks import post_d6, review  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "agy-1.2.17"
PLANT = "- **a planted triage line** — REJECT: this text comes from the judge"


@contextlib.contextmanager
def _chdir(d: Path):
    prev = Path.cwd()
    os.chdir(d)
    try:
        yield
    finally:
        os.chdir(prev)


def _stdout_with_review(text: str) -> str:
    """The captured agy stream with its review text replaced (the judge's answer)."""
    lines = (FIX / "success-tools.stdout").read_text(encoding="utf-8").strip().split("\n")
    last = json.loads(lines[-1])
    last["result"]["response"] = text
    return "\n".join(lines[:-1] + [json.dumps(last)]) + "\n"


REVIEW = ("**Important** — `x.py:3` does a thing wrong.\n\n" + PLANT + "\n\nCAP: 1/5 reported, exhausted")


class FindingsLand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.agent = self.project / ".agent"
        self.tdir = self.agent / "tasks" / "042-demo"
        self.tdir.mkdir(parents=True)
        (self.agent / "models.json").write_text(json.dumps(
            {"panel": ["agy:gemini-3.8-flash-high"], "default_judge": "agy:gemini-3.8-flash-high"}),
            encoding="utf-8")
        review._PB_JOURNAL_MOD = None
        review._PB_JOURNAL_LOADED = False
        for redirect in (contextlib.redirect_stdout, contextlib.redirect_stderr):
            cm = redirect(io.StringIO())
            self.out = cm.__enter__()
            self.addCleanup(cm.__exit__, None, None, None)

    def _task(self, with_impl_section: bool) -> Path:
        body = "# 042 - demo\n## Status\npending\n## Risk\nreversible\n## Intent\nx\n## Work Plan\n- [x] a gate\n"
        if with_impl_section:
            body += "\n## Implementation Review\n- [ ] review gate\n\n(implementation review triage appears here)\n"
        tf = self.tdir / "task.md"
        tf.write_text(body, encoding="utf-8")
        return tf

    def _impl_review(self, stdout: str) -> int:
        def run(agent, args, **kw):
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        with mock.patch.object(sandbox, "run", run), \
                mock.patch.object(shutil, "which", lambda name: "/usr/bin/" + name), _chdir(self.project):
            try:
                review.cmd_single_review(
                    "impl-review", ["042", "--backend", "agy", "--model", "gemini-3.8-flash-high"])
            except SystemExit as e:
                return e.code or 0
        return 0

    def test_no_place_in_task_md_means_judge_single_md_and_exit_0(self):
        tf = self._task(with_impl_section=False)
        before = tf.read_bytes()
        code = self._impl_review(_stdout_with_review(REVIEW))
        self.assertEqual(code, 0)
        self.assertEqual(tf.read_bytes(), before)                    # task.md: not a byte changed
        jm = (self.tdir / "judge-single.md").read_text(encoding="utf-8")
        self.assertIn("## Single-judge impl review — ", jm)
        self.assertIn("agy:gemini-3.8-flash-high", jm)
        self.assertIn("> **Important** — `x.py:3` does a thing wrong.", jm)
        self.assertIn("> " + PLANT, jm)
        self.assertNotIn("\n" + PLANT, jm)                           # never an unquoted line
        # and the SETTLED block cannot read the judge's own text as triage
        self.assertEqual(post_d6.extract_rejected_findings(self.tdir), [])

    def test_a_second_review_is_appended_not_replacing_the_first(self):
        self._task(with_impl_section=False)
        self._impl_review(_stdout_with_review("first review text"))
        self._impl_review(_stdout_with_review("second review text"))
        jm = (self.tdir / "judge-single.md").read_text(encoding="utf-8")
        self.assertEqual(jm.count("## Single-judge impl review — "), 2)
        self.assertLess(jm.index("first review text"), jm.index("second review text"))

    def test_with_its_section_the_review_still_goes_into_task_md(self):
        tf = self._task(with_impl_section=True)
        code = self._impl_review(_stdout_with_review(REVIEW))
        self.assertEqual(code, 0)
        self.assertIn("does a thing wrong", tf.read_text(encoding="utf-8"))
        self.assertFalse((self.tdir / "judge-single.md").exists())

    def test_a_review_in_judge_single_md_is_review_evidence_for_the_close(self):
        # Task 138 G1-5 (codex-high): has_review_evidence read judge.md and task.md only, so a
        # light assertive task whose review landed here could not close on it
        from tasks.core import has_review_evidence
        tf = self._task(with_impl_section=False)
        self.assertFalse(has_review_evidence(tf, impl_only=True))
        self.assertEqual(self._impl_review(_stdout_with_review(REVIEW)), 0)
        self.assertTrue(has_review_evidence(tf, impl_only=True))
        self.assertTrue(has_review_evidence(tf))

    def test_a_plan_review_or_a_quoted_phrase_there_is_no_impl_evidence(self):
        from tasks.core import has_review_evidence
        tf = self._task(with_impl_section=False)
        review._append_review_to_judge_md(self.tdir, "plan", "claude:opus",
                                          "the impl review will come later", "no place")
        self.assertTrue(has_review_evidence(tf))                     # a review ran
        self.assertFalse(has_review_evidence(tf, impl_only=True))     # but not of the implementation

    def test_the_close_accepts_it_end_to_end(self):
        # the full close path: an assertive light task, no panel required by the project
        tf = self._task(with_impl_section=False)
        tf.write_text(tf.read_text(encoding="utf-8").replace("## Risk\nreversible", "## Risk\nassertive"),
                      encoding="utf-8")
        (self.agent / "config.json").write_text(json.dumps({"panel_required_for": []}), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "plugins" / "playbook"), PLAYBOOK_SESSION_ID="pid-g15")
        env.pop("BASH_ENV", None)
        run = lambda *a: subprocess.run([sys.executable, "-m", "tasks.cli", *a], cwd=self.project, env=env,
                                        capture_output=True, text=True, timeout=120)
        self.assertEqual(run("work", "042").returncode, 0)
        r = run("work", "done")
        self.assertNotEqual(r.returncode, 0, r.stdout)                # no review yet: blocked
        self.assertEqual(self._impl_review(_stdout_with_review(REVIEW)), 0)
        r = run("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("done", tf.read_text(encoding="utf-8").split("## Status\n", 1)[1].split("\n", 1)[0])

    def test_the_panel_rounds_file_is_never_touched(self):
        # single-judge review r1: appended to judge.md the review became part of the OLDEST panel
        # round's body and left with it for judge-archive.md at the next stacking
        self._task(with_impl_section=False)
        rounds = ("# Panel Impl Review — round 2\n\n**PANEL VERDICT: PASS**\n\nbody 2\n\n"
                  "# Panel Impl Review — round 1\n\n**PANEL VERDICT: FAIL**\n\nbody 1\n")
        (self.tdir / "judge.md").write_text(rounds, encoding="utf-8")
        self.assertEqual(self._impl_review(_stdout_with_review(REVIEW)), 0)
        self.assertEqual((self.tdir / "judge.md").read_text(encoding="utf-8"), rounds)
        self.assertIn("does a thing wrong", (self.tdir / "judge-single.md").read_text(encoding="utf-8"))

    def test_the_append_holds_the_task_lock(self):
        from tasks import filelock
        held = []
        real = filelock.task_lock

        def spy(path, *a, **kw):
            held.append(Path(path).name)
            return real(path, *a, **kw)
        self._task(with_impl_section=False)
        with mock.patch.object(filelock, "task_lock", spy):
            self.assertEqual(self._impl_review(_stdout_with_review(REVIEW)), 0)
        self.assertIn("judge-single.md", held)

    def test_if_judge_single_md_cannot_be_written_either_the_old_refusal_stands(self):
        self._task(with_impl_section=False)
        (self.tdir / "judge-single.md").mkdir()                             # a directory where the file goes
        code = self._impl_review(_stdout_with_review(REVIEW))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
