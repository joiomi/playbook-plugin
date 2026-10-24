#!/usr/bin/env python3
"""A second single-judge review keeps the first one's text and the agent's triage (task 304; audit 3's G4, the
owner's ruling of 2026-10-24 12:49: "4 grave, G2 nu (Recomandat)").

A second `tasks plan-review` / `impl-review` replaced everything between the findings markers in task.md, so the first
review's findings and any triage the agent wrote among them were deleted; judge-single.md held only the heading of a
review whose findings went into task.md, and the judge's log keeps only the newest run — no copy of the first review
survived. Now every single-judge review's text is appended, quoted, to judge-single.md, and the block in task.md ends
with a digest line of the CLI's: a re-review replaces the block only when its lines are still, byte for byte, the ones
the CLI wrote — otherwise every one of them stays, after the new block, under a line that says why (task 303's rule).

Each test names the row of task 304's contract it proves. End to end through the real `cmd_single_review`, the
judge's process mocked at `sandbox.run` (as `test_review_findings_land` does); at the writer, `_write_review_findings`.
Pure stdlib unittest. Run: python3 -m unittest tests.test_single_judge_review_keeps_the_first
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "playbook"))
sys.path.insert(0, str(ROOT))

from provider import sandbox  # noqa: E402
from tasks import review  # noqa: E402
from tasks.core import _gate_counts  # noqa: E402
from tests.test_review_findings_land import _chdir, _stdout_with_review  # noqa: E402

RUN1 = "**Important** — FINDING-RUN-1: `x.py:1` prints instead of returning.\n\nCAP: 1/5 reported, exhausted"
RUN2 = "**Important** — FINDING-RUN-2: `x.py:2` drops the error.\n\nCAP: 1/5 reported, exhausted"
TRIAGE = "- ACCEPT, FINDING-RUN-1: fixed in x.py, red first (row 9)."
GATE = "- [ ] MY-GATE: rerun the review once the fix is in"
OPEN = "<!-- playbook:impl-review-findings -->"
CLOSE = "<!-- /playbook:impl-review-findings -->"


def _task_text(mode_sections: str = "impl") -> str:
    body = "# 042 - demo\n## Status\npending\n## Risk\nreversible\n## Intent\nx\n## Work Plan\n- [x] a gate\n"
    if "plan" in mode_sections:
        body += "\n## Plan Review\n- [ ] plan review gate\n\n(plan review triage appears here)\n"
    body += "\n## Implementation Review\n- [ ] review gate\n\n(implementation review triage appears here)\n"
    return body + "\n## Debrief\n- [ ] debrief gate\n"


class EndToEnd(unittest.TestCase):
    """Two real `impl-review` runs of a task whose `## Implementation Review` holds its placeholder."""

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
            cm.__enter__()
            self.addCleanup(cm.__exit__, None, None, None)
        self.tf = self.tdir / "task.md"
        self.tf.write_text(_task_text(), encoding="utf-8")

    def impl_review(self, text: str) -> int:
        stdout = _stdout_with_review(text)

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

    def judge_single(self) -> str:
        return (self.tdir / "judge-single.md").read_text(encoding="utf-8")

    def test_run_ones_text_and_the_triage_survive_run_two(self):
        # row 1 (the bug): the agent writes its triage right under run 1's finding, inside the block; after run 2
        # the triage is still in task.md and run 1's finding is still on record (judge-single.md), run 2's in the block
        self.assertEqual(self.impl_review(RUN1), 0)
        text = self.tf.read_text(encoding="utf-8")
        i = text.index("FINDING-RUN-1")
        j = text.index("\n", i)
        self.tf.write_text(text[:j + 1] + TRIAGE + "\n" + text[j + 1:], encoding="utf-8")
        self.assertEqual(self.impl_review(RUN2), 0)
        text = self.tf.read_text(encoding="utf-8")
        self.assertIn(TRIAGE, text.split("\n"))
        self.assertIn("FINDING-RUN-2", text[text.index(OPEN):text.index(CLOSE)])
        self.assertIn("FINDING-RUN-1", self.judge_single())

    def test_every_reviews_text_is_on_record_quoted(self):
        # row 2: every single-judge review's text is appended to judge-single.md, quoted, under its heading — also
        # when its findings went into task.md; two runs, two headings, both texts in their order, no unquoted line
        self.assertEqual(self.impl_review(RUN1), 0)
        self.assertEqual(self.impl_review(RUN2), 0)
        jm = self.judge_single()
        self.assertEqual(jm.count("## Single-judge impl review — "), 2)
        self.assertLess(jm.index("> **Important** — FINDING-RUN-1"), jm.index("> **Important** — FINDING-RUN-2"))
        self.assertNotIn("\n**Important**", jm)
        from tasks.core import has_review_evidence
        self.assertTrue(has_review_evidence(self.tf, impl_only=True))

    def test_a_run_cut_between_its_two_writes_keeps_its_text(self):
        # row 12 (impl round 1, codex I): run 1 is killed between its two writes (whichever the writer does second
        # raises); run 2 then runs whole — run 1's text is still in task.md or on record in judge-single.md
        calls = {"n": 0}
        real_write, real_append = review._write_review_findings, review._append_review_to_judge_md

        def second_raises(fn):
            def wrapped(*a, **kw):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise KeyboardInterrupt
                return fn(*a, **kw)
            return wrapped
        with mock.patch.object(review, "_write_review_findings", second_raises(real_write)), \
                mock.patch.object(review, "_append_review_to_judge_md", second_raises(real_append)):
            with self.assertRaises(KeyboardInterrupt):
                self.impl_review(RUN1)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self.impl_review(RUN2), 0)
        js = self.tdir / "judge-single.md"
        on_record = js.read_text(encoding="utf-8") if js.exists() else ""
        self.assertTrue("FINDING-RUN-1" in self.tf.read_text(encoding="utf-8") or "FINDING-RUN-1" in on_record)

    def test_a_run_whose_record_failed_keeps_its_block(self):
        # row 13 (impl round 1, grok I): run 1's record in judge-single.md fails — its append returns None, or its
        # lock times out (a LockTimeout, which escaped) — then run 2: run 1's text is still in task.md
        from tasks import filelock
        real_lock = filelock.task_lock

        def lock_times_out(path, *a, **kw):
            if Path(path).name == "judge-single.md":
                raise filelock.LockTimeout("judge-single.md is held")
            return real_lock(path, *a, **kw)
        for how in ("returns None", "LockTimeout"):
            with self.subTest(how=how):
                self.tf.write_text(_task_text(), encoding="utf-8")
                for f in ("judge-single.md",):
                    (self.tdir / f).unlink(missing_ok=True)
                if how == "returns None":
                    patch = mock.patch.object(review, "_append_review_to_judge_md", lambda *a, **kw: None)
                else:
                    patch = mock.patch.object(filelock, "task_lock", lock_times_out)
                with patch:
                    self.impl_review(RUN1)
                self.assertIn("FINDING-RUN-1", self.tf.read_text(encoding="utf-8"))
                self.assertEqual(self.impl_review(RUN2), 0)
                self.assertIn("FINDING-RUN-1", self.tf.read_text(encoding="utf-8"))

    def test_a_review_with_no_place_is_recorded_once_as_such(self):
        # row 15 (impl round 1, found by my mutant run): with no place for the findings in task.md (no placeholder,
        # no markers), the review is recorded once in judge-single.md, saying task.md had no place for it, and task.md
        # is not touched (already true before: task 117's path)
        self.tf.write_text(_task_text().replace("(implementation review triage appears here)", "my triage"),
                           encoding="utf-8")
        before = self.tf.read_bytes()
        self.assertEqual(self.impl_review(RUN1), 0)
        self.assertEqual(self.tf.read_bytes(), before)
        jm = self.judge_single()
        self.assertEqual(jm.count("## Single-judge impl review — "), 1)
        self.assertIn("task.md has no place for these findings", jm)
        self.assertIn("> **Important** — FINDING-RUN-1", jm)

    def test_a_run_cut_after_its_log_keeps_its_text(self):
        # row 16 (impl round 2, codex I): run 1 is cut after its judge log is saved and before its record (the look
        # for the findings' place raises); run 2 then runs whole — run 1's text is still under .agent
        with mock.patch.object(review, "_findings_place", mock.Mock(side_effect=KeyboardInterrupt)):
            with self.assertRaises(KeyboardInterrupt):
                self.impl_review(RUN1)
        self.assertEqual(self.impl_review(RUN2), 0)
        texts = [f.read_text(encoding="utf-8", errors="replace") for f in self.tdir.iterdir() if f.is_file()]
        self.assertTrue(any("FINDING-RUN-1" in t for t in texts), [f.name for f in self.tdir.iterdir()])

    def test_the_judge_log_keeps_every_run_newest_first(self):
        # row 17 (impl round 2): the judge's log keeps every run, the newest first — its header (the tamper guard's
        # line, which `single_review_tamper_degraded` reads from the top) is the newest run's
        self.assertEqual(self.impl_review(RUN1), 0)
        self.assertEqual(self.impl_review(RUN2), 0)
        log = (self.tdir / "judge-agy.log").read_text(encoding="utf-8")
        self.assertIn("FINDING-RUN-1", log)
        self.assertLess(log.index("FINDING-RUN-2"), log.index("FINDING-RUN-1"))
        self.assertLess(log.index("[tamper guard]"), log.index("FINDING-RUN-2"))


class TheWriter(unittest.TestCase):
    """`_write_review_findings` on a task file, a second run after the agent wrote among the CLI's lines."""

    def task(self, text: str = "") -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "task.md"
        f.write_text(text or _task_text("plan impl"), encoding="utf-8")
        return f

    def block_lines(self, text: str, mode: str = "impl") -> "list[str]":
        o, c = review._findings_markers(mode)
        return text[text.index(o):text.index(c) + len(c)].split("\n")

    def test_the_agents_lines_among_the_findings_stay(self):
        # row 3: a triage line, an open gate and an edited quoted line, written among the CLI's lines, stay after a
        # re-review — every line of the old block, in order, after the new block, under the line that says why; the
        # gates count the same; one marker pair; plan and impl alike
        for mode in ("impl", "plan"):
            with self.subTest(mode=mode):
                f = self.task()
                o, c = review._findings_markers(mode)
                self.assertIsNone(review._write_review_findings(f, mode, RUN1))
                text = f.read_text(encoding="utf-8")
                text = text.replace("> **Important** — FINDING-RUN-1", "> **Important** — FINDING-RUN-1 (seen)", 1)
                i = text.index("FINDING-RUN-1")
                j = text.index("\n", i)
                text = text[:j + 1] + TRIAGE + "\n" + GATE + "\n" + text[j + 1:]
                f.write_text(text, encoding="utf-8")
                old = [ln for ln in self.block_lines(text, mode)[1:-1] if ln.strip()]
                before = _gate_counts(text)
                self.assertIsNone(review._write_review_findings(f, mode, RUN2))
                after = f.read_text(encoding="utf-8")
                self.assertEqual((after.count(o), after.count(c)), (1, 1))
                self.assertIn("FINDING-RUN-2", "\n".join(self.block_lines(after, mode)))
                tail = after[after.index(c) + len(c):].split("\n")
                kept = [ln for ln in tail if ln.strip()]
                self.assertTrue(kept[0].startswith("Kept by the review writer"), kept[:2])
                self.assertEqual(kept[1:1 + len(old)], old)
                self.assertEqual(_gate_counts(after), before)

    def test_a_block_with_no_digest_line_is_kept_whole(self):
        # row 6: a findings block with no digest line (as written before this task) is no block the writer can tell
        # for its own — a re-review keeps every line of it, after the new block
        f = self.task()
        text = f.read_text(encoding="utf-8").replace(
            "(implementation review triage appears here)", f"{OPEN}\n> **Important** — OLD-FINDING.\n{CLOSE}", 1)
        f.write_text(text, encoding="utf-8")
        self.assertIsNone(review._write_review_findings(f, "impl", RUN2))
        after = f.read_text(encoding="utf-8")
        self.assertEqual(after.count(OPEN), 1)
        self.assertIn("> **Important** — OLD-FINDING.", after[after.index(CLOSE):])
        self.assertIn("FINDING-RUN-2", after[after.index(OPEN):after.index(CLOSE)])

    def test_trailing_white_space_does_not_make_the_block_the_agents(self):
        # row 8: an editor's trailing white space on the block's lines does not make it the agent's — a re-review
        # replaces it, so run 1's text is not kept in task.md (it is on record in judge-single.md)
        f = self.task()
        self.assertIsNone(review._write_review_findings(f, "impl", RUN1))
        text = f.read_text(encoding="utf-8")
        a, b = text.index(OPEN), text.index(CLOSE)
        padded = "\n".join(ln + "  " if ln.strip() else ln for ln in text[a:b].split("\n"))
        f.write_text(text[:a] + padded + text[b:], encoding="utf-8")
        self.assertIsNone(review._write_review_findings(f, "impl", RUN2))
        after = f.read_text(encoding="utf-8")
        self.assertNotIn("FINDING-RUN-1", after)
        self.assertNotIn("Kept by the review writer", after)

    def test_a_changed_digest_line_keeps_the_block_and_that_line(self):
        # row 9: a digest line with one hex digit changed is no digest of the CLI's — every line of the old block
        # stays, that line among them (task 303's post-panel Minor)
        f = self.task()
        self.assertIsNone(review._write_review_findings(f, "impl", RUN1))
        lines = f.read_text(encoding="utf-8").split("\n")
        k = next(i for i, ln in enumerate(lines) if ln.startswith("<!-- playbook:impl-review-findings-digest "))
        hexpos = lines[k].rindex(" -->") - 1
        lines[k] = lines[k][:hexpos] + ("0" if lines[k][hexpos] != "0" else "1") + lines[k][hexpos + 1:]
        changed = lines[k]
        f.write_text("\n".join(lines), encoding="utf-8")
        self.assertIsNone(review._write_review_findings(f, "impl", RUN2))
        after = f.read_text(encoding="utf-8")
        tail = after[after.index(CLOSE) + len(CLOSE):]
        self.assertIn("FINDING-RUN-1", tail)
        self.assertIn(changed, tail.split("\n"))

    def test_text_on_the_closers_line_stays_on_its_own_line(self):
        # row 14 (impl round 1, opus Minor): the agent's text written on the closer's own line is not glued to the
        # last kept line after a re-review that keeps the old block
        f = self.task()
        self.assertIsNone(review._write_review_findings(f, "impl", RUN1))
        text = f.read_text(encoding="utf-8")
        text = text.replace(CLOSE, CLOSE + " my note after the closer", 1)
        i = text.index("FINDING-RUN-1")
        j = text.index("\n", i)
        text = text[:j + 1] + GATE + "\n" + text[j + 1:]
        f.write_text(text, encoding="utf-8")
        self.assertIsNone(review._write_review_findings(f, "impl", RUN2))
        lines = f.read_text(encoding="utf-8").split("\n")
        self.assertIn(GATE, lines)
        self.assertIn(" my note after the closer", lines)

    def test_a_task_md_that_is_not_utf8_is_no_place(self):
        # row 18 (impl round 2, opus Minor): a task.md that is no longer UTF-8 when the findings are placed (it was,
        # when the command read it to build the judge's context) is no place for them — the look says why, it does
        # not crash, so the caller records the review in judge-single.md
        f = self.task()
        f.write_bytes(f.read_bytes() + b"\nbad byte: \xff\n")
        why = review._findings_place(f, "impl")
        self.assertIsNotNone(why)
        self.assertIn("could not read task.md", why)


class TheRecord(unittest.TestCase):
    def test_a_heading_in_a_judges_text_on_record_is_no_evidence(self):
        # row 11: a plan review whose findings went into task.md now leaves its text in judge-single.md too; a line of
        # it shaped like an impl review's heading is quoted there, so it is no impl review evidence (already true
        # before: the text was not written there at all)
        from tasks.core import has_review_evidence
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        tf = d / "task.md"
        tf.write_text(_task_text("plan impl"), encoding="utf-8")
        forged = "## Single-judge impl review — 2026-10-24 12:00 UTC (`claude:opus`)"
        self.assertTrue(review._append_review_to_judge_md(d, "plan", "claude:opus", "a finding\n" + forged, "",
                                                          in_task_md=True))
        self.assertTrue(has_review_evidence(tf))                     # a review ran
        self.assertFalse(has_review_evidence(tf, impl_only=True))     # but not of the implementation

if __name__ == "__main__":
    unittest.main()
