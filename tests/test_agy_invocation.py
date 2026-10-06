#!/usr/bin/env python3
"""The agy (Antigravity) NON-JUDGE headless shape, pinned (tasks 013 and 111).

`headless_argv` without `structured=True` is what the sandbox CLI and the
streaming subagent use. Task 111 rewrote the JUDGE path for agy 1.2.17 (prompt
on stdin, pinned model — see tests/test_agy_judge.py) and left this shape
untouched on purpose: the main-agent side of the adapter stays experimental and
unchanged. `--print` is a STRING flag, so the prompt must be the token right
after it (the task-013 bug: `--print --print-timeout 90s` made agy review the
string "--print-timeout").

Pure stdlib unittest. Run: python3 tests/test_agy_invocation.py
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))

from provider.adapters.antigravity import AntigravityAdapter  # noqa: E402


class AgyNonJudgeInvocationTest(unittest.TestCase):
    def setUp(self):
        self.a = AntigravityAdapter(session_id="judge", project_root=Path("/tmp/proj"))

    def test_prompt_is_print_value_not_stdin(self):
        inv = self.a.headless_argv("REVIEW", None, context="CTX")
        self.assertIsNone(inv.stdin)
        i = inv.argv.index("--print")
        self.assertEqual(inv.argv[i + 1], "CTX\n\n---\n\nREVIEW")

    def test_bare_no_context_prompt_only(self):
        inv = self.a.headless_argv("JUST THE PROMPT", None, context="", bare=True)
        i = inv.argv.index("--print")
        self.assertEqual(inv.argv[i + 1], "JUST THE PROMPT")

    def test_whole_argv_is_unchanged(self):
        inv = self.a.headless_argv("P", "gemini-3.8-flash-high", context="C", stream=True)
        self.assertEqual(inv.argv, ["--add-dir", str(Path("/tmp/proj")), "--print", "C\n\n---\n\nP"])

    def test_the_judge_no_longer_uses_this_shape(self):
        inv = self.a.headless_argv("P", None, context="C", structured=True)
        self.assertNotIn("--print", inv.argv)
        self.assertIsNotNone(inv.stdin)


if __name__ == "__main__":
    unittest.main(verbosity=2)
