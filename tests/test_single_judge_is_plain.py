#!/usr/bin/env python3
"""`tasks impl-review` after an impl panel is a plain single judge (task 343, PLAN-PLAYBOOK-2.md 4.1 a; DESIGN-PLAYBOOK-2.md
§2: "`plan-review` și `impl-review` ca judecător unic" stay, the post-panel judge as close evidence goes).

Before task 343 an impl round in judge.md turned `impl-review` into the post-panel judge: it was handed only the delta
since the panel, printed a `POST-D6 VERDICT` the close bound to, and refused a third run after the same panel. Now it
reviews the whole task as before task 108, whatever judge.md holds, and runs as often as it is asked.

End to end through the real `cmd_single_review` in a real git project, the judge's process mocked at `sandbox.run` (as
`test_single_judge_review_keeps_the_first` does). Pure stdlib unittest.
Run: python3 -m unittest tests.test_single_judge_is_plain
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
from tests.test_review_findings_land import _chdir, _stdout_with_review  # noqa: E402

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
REVIEW = "**Important** — `code.py:1` returns the wrong value.\n\nCAP: 1/5 reported, exhausted"


class Plain(unittest.TestCase):

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb343-sj-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        subprocess.run(["git", "init", "-q"], cwd=self.d, check=True)
        (self.d / "code.py").write_text("x = 1\n", encoding="utf-8")
        (self.d / ".gitignore").write_text(".agent/journal/\n", encoding="utf-8")
        subprocess.run([*GIT, "add", "-A"], cwd=self.d, check=True)
        subprocess.run([*GIT, "commit", "-qm", "seed"], cwd=self.d, check=True)
        agent = self.d / ".agent"
        self.td = agent / "tasks" / "042-demo"
        self.td.mkdir(parents=True)
        (agent / "models.json").write_text(json.dumps(
            {"panel": ["agy:gemini-3.8-flash-high"], "default_judge": "agy:gemini-3.8-flash-high"}), encoding="utf-8")
        self.tf = self.td / "task.md"
        self.tf.write_text("# 042 - demo\n## Status\nin_progress\n## Risk\nassertive\n## Intent\nTASK-INTENT-MARK\n"
                           "## Owner rulings\n<!-- pin -->\n- 2026-10-29 17:00: \"RULING-MARK keep it\"\n\n"
                           "## Work Plan\n- [x] a gate\n\n## Implementation Review\n- [ ] review gate\n\n"
                           "(implementation review triage appears here)\n", encoding="utf-8")
        from tasks.core import build_panel_snapshot, format_panel_snapshot_line, tree_state_fingerprint
        fp = tree_state_fingerprint(self.d)
        snap = build_panel_snapshot(self.d, fp)
        (self.td / "judge.md").write_text(
            "# Panel Impl Review — .agent/tasks/042-demo/task.md\n**Round-format:** 2\n\n"
            "**PANEL VERDICT: PASS** — 5/5 judges succeeded, quorum 3\n\n**Round-id:** 0123456789ab\n\n"
            f"**Tree-state:** {fp}\n\n{format_panel_snapshot_line(snap)}\n\nbody\n", encoding="utf-8")
        (self.d / "code.py").write_text("x = 2\n", encoding="utf-8")       # changed after the panel
        review._PB_JOURNAL_MOD = None
        review._PB_JOURNAL_LOADED = False

    def impl_review(self, *extra):
        prompts = []

        def run(agent, args, **kw):
            prompts.append(" ".join(str(a) for a in args) + str(kw.get("input") or ""))
            return subprocess.CompletedProcess(args, 0, stdout=_stdout_with_review(REVIEW), stderr="")
        out = io.StringIO()
        code = 0
        with mock.patch.object(sandbox, "run", run), mock.patch.object(shutil, "which", lambda n: "/usr/bin/" + n), \
                _chdir(self.d), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                review.cmd_single_review("impl-review", ["042", "--backend", "agy", "--model", "gemini-3.8-flash-high",
                                                         *extra])
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), prompts

    def test_impl_review_after_a_panel_is_plain(self):
        """Row 13: three runs after the panel — each runs, each prompt holds the task, none a delta or a verdict.
        Row 15: the SETTLED block, with the task's ruling, rides in the prompt."""
        for n in (1, 2, 3):
            code, out, prompts = self.impl_review()
            self.assertEqual(code, 0, (n, out))
            self.assertEqual(len(prompts), 1, (n, out))
            self.assertIn("TASK-INTENT-MARK", prompts[0])
            # the block itself, before the task — the bare word is also in the prompt's `[SETTLED]` tag rule, and the
            # ruling in task.md's own text (task 343's mutant M15)
            block = prompts[0].find("=== SETTLED (owner rulings")
            self.assertGreaterEqual(block, 0, prompts[0][:400])
            self.assertIn("RULING-MARK", prompts[0][block:prompts[0].index("TASK-INTENT-MARK")])
            self.assertNotIn("post-panel delta", prompts[0].lower())
            self.assertNotIn("POST-D6", out)
            self.assertNotIn("post-D6", out)

    def test_an_option_it_does_not_read_is_refused(self):
        """Row 33 (impl round 1, I5): `--owner-ok` and any other option the single judge does not read is refused
        before a judge runs — never dropped in silence — and the usage line names no removed option."""
        for extra in (("--owner-ok", "--reason", "x"), ("--bogus",)):
            with self.subTest(extra=extra):
                code, out, prompts = self.impl_review(*extra)
                self.assertNotEqual(code, 0, out)
                self.assertEqual(prompts, [], out)
                self.assertIn("unknown option", out)
        out = io.StringIO()
        with contextlib.redirect_stderr(out), self.assertRaises(SystemExit):
            review.cmd_single_review("impl-review", [])
        self.assertNotIn("--owner-ok", out.getvalue())


if __name__ == "__main__":
    unittest.main()
