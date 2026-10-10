"""Writer-format test for chat-log-hook (§2.3): the chat-log writer had no
tests, and its `(provider/pid)` header suffix (added 1.4.3) is what silently
broke a downstream parser (I12). Pinning the writer's OUTPUT FORMAT means a
future drift can fail a test instead of a reader.
"""

from __future__ import annotations

import json
import os
import subprocess
from tests._bashcheck import bash_or_skip
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "plugins" / "playbook" / "scripts" / "chat-log-hook"
SID = "pid-clw"


class _ChatLogFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        self.log = self.project / ".agent" / "chat_log.md"

    def _run(self, prompt, provider=None, extra_env=None):
        env = dict(os.environ)
        env["PLAYBOOK_SESSION_ID"] = SID
        env.pop("BASH_ENV", None)
        if provider:
            env["PLAYBOOK_PROVIDER"] = provider
        env.update(extra_env or {})
        return subprocess.run(
            [bash_or_skip(), str(HOOK)], cwd=self.project, env=env, text=True,
            input=json.dumps({"prompt": prompt}), capture_output=True)


class ChatLogWriter(_ChatLogFixture):
    def test_entry_format_and_sequence(self):
        self._run("first message")
        self._run("second message")
        text = self.log.read_text(encoding="utf-8")
        # Header format: **[MNNN]** [<ts> UTC] `HOST` (provider/sid)
        self.assertRegex(
            text,
            r"\*\*\[M001\]\*\* \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\] "
            r"`HOST` \(claude/pid-clw\)")
        self.assertIn("**[M002]**", text)
        self.assertIn("first message", text)
        self.assertIn("second message", text)

    def test_provider_suffix_reflects_env(self):
        self._run("hi", provider="codex")
        text = self.log.read_text(encoding="utf-8")
        self.assertRegex(text, r"`HOST` \(codex/pid-clw\)")

    def test_a_long_message_is_kept_whole_up_to_50000_chars(self):
        # Task 127 (gauntlet 2 R19, owner Q-C (b) 2026-09-29): every message was cut at
        # 500 characters — a long instruction reached the log and Recent Chat mutilated.
        long = "start " + ("word " * 2000) + "END-MARK"                 # ~10,000 chars
        self._run(long)
        self.assertIn("END-MARK", self.log.read_text(encoding="utf-8"))

    def test_beyond_50000_chars_the_message_is_cut_and_says_so(self):
        huge = "x" * 60000
        self._run(huge)
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("x" * 50000 + "...[10000 chars removed]", text)
        self.assertNotIn("x" * 50001, text)

    def test_the_cap_counts_characters_not_bytes_under_a_c_locale(self):
        # Task 138 G2-2 (sonnet, codex-medium): bash's ${#v} and ${v:0:N} count BYTES under
        # LC_ALL=C (and on Git-Bash without a UTF-8 LANG): 30,000 `ă` (60,000 bytes) were cut
        # although under the cap, and a cut could split a character into invalid UTF-8.
        c = {"LC_ALL": "C", "LANG": "C"}
        self._run("ă" * 30000 + "END", extra_env=c)
        text = self.log.read_bytes().decode("utf-8")                      # strict: whole characters
        self.assertIn("ă" * 30000 + "END", text)
        self.assertNotIn("chars removed", text)
        self._run("ă" * 50001, extra_env=c)
        text = self.log.read_bytes().decode("utf-8")
        self.assertIn("ă" * 50000 + "...[1 chars removed]", text)
        self.assertNotIn("ă" * 50001, text)


class CodexChatLogWriter(unittest.TestCase):
    """Task 138 G2-1 (opus): the Codex prompt writer kept its own 500-character cap, so
    task 127's 50,000 (owner Q-C (b): per message, whatever the provider) missed it."""

    def setUp(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT / "plugins" / "playbook"))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        (self.project / ".agent" / "tasks").mkdir(parents=True)

    def _log(self, prompt):
        from provider import codex_hooks
        self.assertTrue(codex_hooks.append_prompt_to_chat_log(self.project, SID, prompt))
        return (self.project / ".agent" / "chat_log.md").read_text(encoding="utf-8")

    def test_a_long_codex_prompt_is_kept_whole_up_to_50000_chars(self):
        text = self._log("start " + ("word " * 2000) + "END-MARK")
        self.assertIn("END-MARK", text)

    def test_beyond_50000_chars_a_codex_prompt_is_cut_and_says_so(self):
        text = self._log("y" * 60000)
        self.assertIn("y" * 50000 + "...[10000 chars removed]", text)
        self.assertNotIn("y" * 50001, text)


class HarnessPromptsAreNotUserWords(_ChatLogFixture):
    """PLAN S5a (task 088): a UserPromptSubmit payload that BEGINS with a harness
    marker is a harness event (a background-task notification, a slash-command
    echo), not the user's words — on 2026-09-23, 498 of 833 chat_log entries began
    with `<task-notification>`, and attribution/intent/retro read them as the user."""

    def _logged(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_task_notification_prompt_is_not_logged(self):
        r = self._run("<task-notification> <task-id>b1</task-id> probe-harness-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("probe-harness-1", self._logged())

    def test_plain_prompt_is_still_logged(self):
        self._run("plain probe-user-1")
        self.assertIn("plain probe-user-1", self._logged())
        self.assertIn("**[M001]**", self._logged())

    def test_every_harness_marker_is_skipped(self):
        for n, marker in enumerate(("<local-command-caveat>", "<command-name>", "[SYSTEM NOTIFICATION - NOT USER INPUT]",
                                    "  <task-notification>"), start=2):
            with self.subTest(marker=marker):
                self._run(f"{marker} probe-harness-{n}")
                self.assertNotIn(f"probe-harness-{n}", self._logged())

    def test_a_host_without_jq_still_skips_a_spaced_harness_payload(self):
        # Panel r1 (sol-high, sol-medium): without jq the hook's fallback only
        # recognised compact `"prompt":"..."` JSON; `json.dumps` output is
        # spaced, so the raw JSON was logged and the harness filter saw `{`.
        nojq = Path(self._tmp.name) / "nojq-bin"
        nojq.mkdir()
        seen = set()
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if not d or not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                src = os.path.join(d, name)
                if name == "jq" or name in seen or not (os.path.isfile(src) and os.access(src, os.X_OK)):
                    continue
                seen.add(name)
                os.symlink(src, nojq / name)
        env = {"PATH": str(nojq)}
        r = self._run("<task-notification> probe-nojq-harness", extra_env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self._run("plain probe-nojq-user \u00e9", extra_env=env)
        text = self._logged()
        self.assertNotIn("probe-nojq-harness", text)
        self.assertIn("\nplain probe-nojq-user \u00e9\n", text)
        self.assertNotIn('{"prompt"', text)

    def test_a_skipped_notification_leaves_the_session_counters_alone(self):
        # Until task 173 this test pinned the opposite: a skipped harness prompt
        # performed the same counter reset a logged prompt does. Owner's Q1
        # (2026-09-24): an automatic notification no longer resets the counters
        # the stop hook's bypass reads — the file comes out byte for byte as it
        # went in. (The sequences that rule is about: tests/test_notification_turn.py.)
        counters = self.project / ".agent" / "sessions" / SID / "counters"
        counters.parent.mkdir(parents=True)
        before = b"tools=7\nwrites=3\ngate_x=1\n"
        for marker in ("<task-notification>", "[SYSTEM NOTIFICATION - NOT USER INPUT]"):
            with self.subTest(marker):
                counters.write_bytes(before)
                r = self._run(f"{marker} probe-harness-reset")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("probe-harness-reset", self._logged())
                self.assertEqual(counters.read_bytes(), before)

    def test_a_skipped_echo_of_the_users_command_still_resets_them(self):
        # `<command-name>` / `<local-command-caveat>`: skipped from the log like a
        # notification, but the user's own act — a fresh count, gate fields kept.
        # … and it ends whatever a notification began: the two one-line files of that
        # rule (the last stop let through, the notification's baseline) go with it,
        # and a new prompt generation begins (the one file it leaves beside counters).
        counters = self.project / ".agent" / "sessions" / SID / "counters"
        counters.parent.mkdir(parents=True)
        for marker in ("<command-name>", "<local-command-caveat>"):
            with self.subTest(marker):
                counters.write_bytes(b"tools=7\nwrites=3\ngate_x=1\n")
                for name in ("turn_end", "notif_start"):
                    (counters.parent / name).write_text("7\n", encoding="utf-8")
                r = self._run(f"{marker} probe-command-echo")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("probe-command-echo", self._logged())
                self.assertEqual(counters.read_text(encoding="utf-8"), "tools=0\nwrites=0\ngate_x=1\n")
                self.assertEqual(sorted(p.name for p in counters.parent.iterdir()), ["counters", "prompt_gen"])

    def test_a_user_prompt_that_merely_mentions_a_marker_is_logged(self):
        self._run("why do I see <task-notification> lines? probe-user-2")
        self.assertIn("probe-user-2", self._logged())


if __name__ == "__main__":
    unittest.main()
