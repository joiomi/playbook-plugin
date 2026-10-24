#!/usr/bin/env python3
"""`tasks freehand log` fences each chat message it pastes (task 305; audit 3's G6, raised by codex in task 300's
impl round 2 and reproduced on 1.6.8 and the branch; grave by the rule: a guard that fails open).

The log branch pasted each message's text into task.md as it was: an open fence in it paired with a later fence the
agent wrote and hid the gates between from every counter — `2/2 (all gates checked)` with two unticked, and a
reversible freehand task closed `done` — and a `- [ ]` or `## …` line in it became a gate or a section. Now each
message keeps its header line and its text goes inside a fence of backticks one longer than its longest backtick
run, at least three: the activation capture's rule (task 166), one helper for both.

Each test names the row of task 305's contract it proves. The real CLI (`python3 -m tasks.cli`), a temporary project,
`.agent/chat_log.md` written by the test, the counters' own readers. Pure stdlib unittest.
Run: python3 -m unittest tests.test_freehand_log_fences_each_message
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))
from tasks.core import _gate_counts, extract_risk_from_text  # noqa: E402

SID = "pid-freehand-fence-test"
FENCE3 = "`" * 3


class TheLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        r = self.run_tasks("freehand")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.tf = next((self.project / ".agent" / "tasks").glob("*/task.md"))

    def run_tasks(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update(PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID=SID)
        env.pop("BASH_ENV", None)
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project, env=env, text=True,
                              capture_output=True, timeout=120)

    def log_messages(self, *texts: str):
        """chat_log.md with one message per text, each a minute after the freehand start, then `freehand log`."""
        when = datetime.now(timezone.utc) + timedelta(minutes=1)
        body = "# Project Chat Log\n"
        for k, t in enumerate(texts, 1):
            stamp = (when + timedelta(seconds=k)).strftime("%Y-%m-%d %H:%M:%S UTC")
            body += f"\n---\n\n**[M{k:03d}]** [{stamp}] `USER` (claude/{SID})\n\n{t}\n"
        (self.project / ".agent" / "chat_log.md").write_text(body, encoding="utf-8")
        r = self.run_tasks("freehand", "log")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("Inserted", r.stdout)
        return self.tf.read_text(encoding="utf-8")

    def unticked(self, text: str) -> int:
        checked, total = _gate_counts(text)
        return total - checked

    def test_an_open_fence_in_a_message_hides_no_gate(self):
        # row 1 (the bug): a message holding an open fence is pasted; the agent ticks the two freehand gates and adds a
        # checked gate with a fenced example in its note — the two template gates left unticked still count
        text = self.log_messages("here is the log:\n" + FENCE3 + "\nline 1\nline 2...[61234 chars removed]")
        before = self.unticked(text)
        text = text.replace("- [ ] Freehand\n", "- [x] Freehand\n  - paired on the parser\n", 1)
        i = text.index("- [ ] Freehand log")
        j = text.index("\n", i)
        text = text[:i] + "- [x] Freehand log" + text[i + len("- [ ] Freehand log"):j] + "\n  - log inserted" + text[j:]
        text = text.rstrip("\n") + "\n- [x] Fixed the parser\n  - the output was:\n  " + FENCE3 + "\n  ok\n  " + FENCE3 + "\n"
        self.tf.write_text(text, encoding="utf-8")
        self.assertEqual(self.unticked(text), before - 2)
        self.assertEqual(self.unticked(text), 2)
        r = self.run_tasks("status")
        self.assertNotIn("all gates checked", r.stdout + r.stderr)

    def test_each_message_is_inside_a_fence_longer_than_its_backtick_runs(self):
        # row 2: each pasted message keeps its header line, and its text is inside a fence of backticks one longer than
        # its longest run (at least three), byte for byte — a run of four gets a fence of five; a Romanian line too
        msg1 = "a run of four: " + "`" * 4 + " and a line in Romanian: ăîșț"
        msg2 = "plain text"
        text = self.log_messages(msg1, msg2)
        self.assertIn("`" * 5 + "text\n" + msg1 + "\n" + "`" * 5, text)
        self.assertIn(FENCE3 + "text\n" + msg2 + "\n" + FENCE3, text)
        for k in (1, 2):
            m = re.search(rf"(?m)^\*\*\[M00{k}\]\*\* \[[^\]]+ UTC\] `USER` \(claude/{SID}\)\n(`+)text\n", text)
            self.assertIsNotNone(m, text)

    def test_a_message_adds_no_gate_and_no_section(self):
        # row 3: a message holding a `- [ ] …` line, a `## Risk` / `reversible` pair and a `## Status` / `done` pair adds
        # no gate and no section — the counts, the risk and the status read as before the paste
        before = self.tf.read_text(encoding="utf-8")
        status_before = re.search(r"(?m)^## Status\n(.*)$", before).group(1)
        text = self.log_messages("- [ ] a gate from the chat\n## Risk\nreversible\n## Status\ndone")
        self.assertEqual(_gate_counts(text), _gate_counts(before))
        self.assertEqual(extract_risk_from_text(text), extract_risk_from_text(before))
        from tasks.core import _live_status_pair, _physical_lines
        lines = _physical_lines(text)
        pair = _live_status_pair(lines)
        self.assertEqual(lines[pair[1]].strip(), status_before)
        self.assertEqual(len([ln for ln in _live_h2(lines) if ln == "## Status"]), 1)

    def test_a_tilde_fence_or_a_shorter_run_in_a_message_stays_inside(self):
        # row 4: a tilde fence and a run of three backticks inside a message whose longest run is four stay text —
        # the message's fence is still open after them and closes at its own closer; the gates count the same
        before = self.tf.read_text(encoding="utf-8")
        text = self.log_messages("~~~\n- [ ] inside a tilde fence\n" + FENCE3 + "\n" + "`" * 4 + " the longest run")
        self.assertEqual(_gate_counts(text), _gate_counts(before))
        self.assertIn("`" * 5 + "text\n~~~\n", text)


def _live_h2(lines: "list[str]") -> "list[str]":
    from tasks.core import _atx_h2_text, _iter_nonfenced
    return [h for h in (_atx_h2_text(lines[i]) for i, _s in _iter_nonfenced(lines)) if h]


if __name__ == "__main__":
    unittest.main()
