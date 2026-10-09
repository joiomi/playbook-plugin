"""I12 (verification-report-1.5.9): `_capture_recent_chat` must parse the modern
chat-log header that carries a `(provider/pid)` suffix.

The regex required the header to end at `` `\\w+` `` immediately before the
newline, but the chat-log producer has appended a ` (provider/pid)` suffix since
1.4.3. Two sibling parsers were fixed; this third was not — so the "Recent Chat
auto-captured at activation" feature (and the Chat-Log-Research design gate that
depends on it) was silently DEAD on any lane whose hook writes the suffix.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))
from tasks.lifecycle import _capture_recent_chat  # noqa: E402

MODERN = """# Project Chat Log

---

**[M001]** [2026-08-15 10:00:00 UTC] `HOST` (claude/pid-123)

first modern message

---

**[M002]** [2026-08-15 10:00:05 UTC] `HOST` (codex/pid-123)

second modern message
"""

LEGACY = """# Project Chat Log

---

**[M001]** [2026-08-15 10:00:00 UTC] `HOST`

legacy message
"""


class CaptureRecentChat(unittest.TestCase):
    def _capture(self, content):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        (d / ".agent").mkdir()
        (d / ".agent" / "chat_log.md").write_text(content, encoding="utf-8")
        return _capture_recent_chat(d)

    def test_modern_header_with_provider_suffix_is_captured(self):
        got = self._capture(MODERN)
        blob = "\n".join(got)
        self.assertIn("first modern message", blob,
                      "modern (provider/pid) entries were not captured (I12)")
        self.assertIn("second modern message", blob)

    def test_legacy_header_still_captured(self):
        # Negative control: the pre-suffix format must still parse.
        got = self._capture(LEGACY)
        self.assertIn("legacy message", "\n".join(got))


class TheOwnersWordsAreNotCutMidSentence(unittest.TestCase):
    """PLAN S11 item 9 (task 166; retro 161). The capture cut every message at 200
    characters: the owner's order of 2026-10-08 (327 characters) reached three task records
    without its second half, while the judge prompt says these excerpts "carry the user's
    own words". Three of four of his messages are longer than 200 (measured on this
    workspace's chat log)."""

    ORDER = ("pai as vrea sa fixezi daca mai e ceva de fixat, si dupa sa faci playbookul sa fie oficial doar "
             "linux, sa scoti ce mai e de windows si mac daca a mai ramas ceva, si sa te asiguri ca poti "
             "continua dezvoltarea in continuare la playbook, inainte sa te apuci, ca sa ai terenul verificat "
             "ca poti lucra linistit in continuare la plan.")

    def _capture(self, *texts):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        (d / ".agent").mkdir()
        log = "# Project Chat Log\n"
        for i, text in enumerate(texts, 1):
            log += f"\n---\n\n**[M{i:03d}]** [2026-10-08 20:45:{i:02d} UTC] `HOST` (claude/pid-1)\n\n{text}\n"
        (d / ".agent" / "chat_log.md").write_text(log, encoding="utf-8")
        return _capture_recent_chat(d)

    def test_an_order_of_ordinary_length_is_captured_whole(self):
        self.assertGreater(len(self.ORDER), 300)
        got = self._capture(self.ORDER)
        self.assertEqual(got[0].split("\n", 1)[1], self.ORDER)

    def test_a_very_long_message_is_cut_and_says_what_was_left_out(self):
        from tasks.lifecycle import RECENT_CHAT_CUT
        long = "word " * 600                                   # 3,000 characters, stripped to 2,999
        body = self._capture(long)[0].split("\n", 1)[1]
        self.assertTrue(body.startswith(long[:RECENT_CHAT_CUT]))
        self.assertLess(len(body), RECENT_CHAT_CUT + 200)
        left_out = len(long.strip()) - RECENT_CHAT_CUT
        self.assertIn(f"{left_out:,}", body)                   # how much is missing …
        self.assertIn("M001", body)                            # … and where the whole text is

    def test_the_judge_is_told_the_same_number(self):
        from tasks.lifecycle import RECENT_CHAT_CUT
        from tasks.template import _intent_check
        said = _intent_check(".agent/tasks/007-x/task.md")
        self.assertIn(f"{RECENT_CHAT_CUT:,}", said)
        self.assertNotIn("200-character", said)

if __name__ == "__main__":
    unittest.main()
