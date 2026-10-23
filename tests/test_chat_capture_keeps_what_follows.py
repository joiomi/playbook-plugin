#!/usr/bin/env python3
"""Task 297 (stabilization audit 2, grave G4): an activation replaces only the captured messages.

Every `tasks work` that found chat messages deleted everything from the `### Recent Chat (auto-captured ...)` heading
to the first `---` (`lifecycle._inject_chat_into_task`, a regex to the end of References) and wrote the new capture
there. Whatever an agent had written after the block went with it: in five tasks here that was the task's
`## Owner rulings`, his words verbatim (task 295, `evidence/m2_exposure.txt`), and a gate there is his clause 3.
Now the block is its heading and the captured messages under it, and only that is replaced.

Run: python3 tests/test_chat_capture_keeps_what_follows.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins/playbook"
sys.path.insert(0, str(PLUGIN))
from tasks.core import _gate_counts  # noqa: E402
from tasks.lifecycle import _capture_recent_chat, _inject_chat_into_task  # noqa: E402

TASK = ("# 012 - Importer\n\n## Status\nin_progress\n\n## References\n- context\n\n---\n\n"
        "## Work Plan\n- [ ] the one real gate\n")
RULINGS = ("\n## Owner rulings\n<!-- pin -->\n- 2026-10-23 10:00: \"OWNER-RULING-41: keep the CSV order\"\n"
           "- [ ] GATE-12: confirm the BOM scope with him\n")


class _Project(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.project, True)
        (self.project / ".agent" / "tasks" / "012-importer").mkdir(parents=True)
        self.tf = self.project / ".agent" / "tasks" / "012-importer" / "task.md"
        self.tf.write_text(TASK, encoding="utf-8")
        self.log = "# Project Chat Log\n"
        self.n = 0

    def say(self, text):
        self.n += 1
        self.log += f"\n---\n\n**[M{self.n:03d}]** [2026-10-23 10:{self.n:02d}:00 UTC] `HOST` (claude/pid-1)\n\n{text}\n"
        (self.project / ".agent" / "chat_log.md").write_text(self.log, encoding="utf-8")

    def activate(self):
        _inject_chat_into_task(self.tf, _capture_recent_chat(self.project))

    def text(self):
        return self.tf.read_text(encoding="utf-8")

    def write_after_the_block(self, s):
        t = self.text()
        i = t.index("\n---\n")
        self.tf.write_text(t[:i].rstrip("\n") + "\n" + s + t[i:], encoding="utf-8")


class WhatFollowsTheBlockStays(_Project):
    def test_a_section_and_its_gate_after_the_block_stay(self):
        """Row 1: an `## Owner rulings` section with a gate, written after the block, survives the next activation."""
        self.say("build the importer")
        self.activate()
        self.write_after_the_block(RULINGS)
        before = _gate_counts(self.text())
        self.say("and keep it simple")
        self.activate()
        t = self.text()
        self.assertIn("OWNER-RULING-41: keep the CSV order", t)
        self.assertIn("- [ ] GATE-12: confirm the BOM scope with him", t)
        self.assertEqual(_gate_counts(t), before)
        # the capture stays where it was: written after the rulings it would become a subsection of them
        self.assertLess(t.index("### Recent Chat"), t.index("## Owner rulings"))
        self.assertIn("and keep it simple", t[t.index("### Recent Chat"):t.index("## Owner rulings")])

    def test_a_note_right_under_the_messages_stays(self):
        """Row 2: a plain note an agent wrote right under the captured messages survives."""
        self.say("build the importer")
        self.activate()
        self.write_after_the_block("\nNOTE-17: the M001 order is also in the Intent.\n")
        self.say("another message")
        self.activate()
        self.assertIn("NOTE-17: the M001 order is also in the Intent.", self.text())

    def test_the_real_verbs_keep_the_rulings(self):
        """Row 3: through the CLI — `tasks work`, rulings written after the block, `tasks blocked`, a new message,
        `tasks work` (the resume captures again)."""
        home = self.project / "home"
        home.mkdir()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "C.UTF-8",
               "PYTHONPATH": str(PLUGIN), "PLAYBOOK_SESSION_ID": "pid-297", "PYTHONDONTWRITEBYTECODE": "1"}

        def tasks(*args):
            r = subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project, env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (args, r.stdout, r.stderr))
            return r.stdout

        self.say("build the importer")
        self.assertIn("Captured 1 recent chat message", tasks("work", "012"))
        self.write_after_the_block(RULINGS)
        tasks("blocked", "waiting on the owner")
        self.say("the answer: BOM files are out")
        self.assertIn("Captured 2 recent chat message", tasks("work", "012"))
        self.assertIn("OWNER-RULING-41: keep the CSV order", self.text())


class WhatIsNotAMessageStays(_Project):
    def test_a_quote_that_only_looks_like_a_message_stays(self):
        """Row 6: only the capture's exact message shape is the block's own — a `**[M…]** [YYYY-MM-DD HH:MM:SS]` line
        with a fence of text under it. An agent's quote of a message in another shape, right under the block, stays."""
        self.say("build the importer")
        self.activate()
        self.write_after_the_block("\n**[M002]** [last week]\n```text\nQUOTE-32 the old plan\n```\n"
                                   "\n**[M001]** [yesterday] he said: QUOTE-31 keep the CSV order\n")
        self.say("another message")
        self.activate()
        t = self.text()
        self.assertIn("**[M001]** [yesterday] he said: QUOTE-31 keep the CSV order", t)
        self.assertIn("**[M002]** [last week]\n```text\nQUOTE-32 the old plan\n```", t)

    def test_a_heading_quoted_in_a_fence_is_not_the_block(self):
        """Row 8: the block's heading quoted inside a fence in References (an example) is not the block; the real
        block below it is the one replaced, and the quote stays whole."""
        t = self.text()
        i = t.index("- context\n") + len("- context\n")
        self.tf.write_text(t[:i] + "\n```text\n### Recent Chat (auto-captured at activation — review and remove "
                           "unrelated)\nQUOTED-EXAMPLE-4\n```\n" + t[i:], encoding="utf-8")
        self.say("first")
        self.activate()
        self.say("second")
        self.activate()
        t = self.text()
        self.assertIn("QUOTED-EXAMPLE-4\n```", t)
        self.assertEqual(t.count("### Recent Chat"), 2)        # the quoted one and the real one
        self.assertIn("second", t[t.index("QUOTED-EXAMPLE-4"):])

    def test_an_older_unfenced_block_is_kept_under_one_heading(self):
        """Row 7: a block in the older, unfenced format is not taken for captured messages: its lines stay, below the
        new capture, under one heading (the writer fails toward keeping)."""
        t = self.text()
        i = t.index("\n---\n")
        self.tf.write_text(t[:i] + "\n### Recent Chat (auto-captured at activation — review and remove unrelated)\n\n"
                           "- **[M007]** [2026-10-13 04:32:52] \"LEGACY-LINE-9: I'm awake.\"\n" + t[i:], encoding="utf-8")
        self.say("a new message")
        self.activate()
        t = self.text()
        self.assertIn("LEGACY-LINE-9: I'm awake.", t)
        self.assertIn("a new message", t)
        self.assertEqual(t.count("### Recent Chat"), 1)


class TheBlockIsStillReplaced(_Project):
    def test_the_new_capture_replaces_the_old_messages(self):
        """Row 4: the block's own messages are replaced, under one heading (what an activation is for)."""
        self.say("OLD-MESSAGE-1")
        self.activate()
        self.log = "# Project Chat Log\n"
        self.n = 1
        self.say("NEW-MESSAGE-2")
        self.activate()
        t = self.text()
        self.assertNotIn("OLD-MESSAGE-1", t)
        self.assertIn("NEW-MESSAGE-2", t)
        self.assertEqual(t.count("### Recent Chat"), 1)

    def test_with_nothing_after_the_block_the_bytes_are_as_before(self):
        """Row 5: a block with nothing after it comes out byte for byte as before the fix."""
        self.say("first")
        self.activate()
        self.say("second")
        self.activate()
        norm = re.sub(r"`{3,}", "F", self.text())
        self.assertEqual(norm, "# 012 - Importer\n\n## Status\nin_progress\n\n## References\n- context\n\n"
                               "### Recent Chat (auto-captured at activation — review and remove unrelated)\n\n"
                               "**[M001]** [2026-10-23 10:01:00]\nFtext\nfirst\nF\n\n"
                               "**[M002]** [2026-10-23 10:02:00]\nFtext\nsecond\nF\n\n---\n\n"
                               "## Work Plan\n- [ ] the one real gate\n")


if __name__ == "__main__":
    unittest.main()
