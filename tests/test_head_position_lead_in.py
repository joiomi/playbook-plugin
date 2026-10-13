"""A bold lead-in line never blocks a close (task 190, PLAN S14 T2c; owner ruling (a) of 2026-10-13 16:30, item #50).

`_extract_head_position` scanned the WHOLE file for an "empty required field" (`- **X**…:`) once every gate was
checked, and `_gate_bounce` refused `tasks work done` on it with "0 open gate(s) … Next open gate: - **Ceva**, în
scratch:" — the owner's WoW project had to rewrite eight such lines (2026-10-06). The close now decides from the shared
gate scan (`_live_gate_scan`), and its message carries that scan's count.
"""
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))
from tasks import core, lifecycle  # noqa: E402

HEAD = "# T\n\n## Status\nin_progress\n\n## Intent\n"


class GateBounceReadsTheGates(unittest.TestCase):
    def _file(self, text):
        d = Path(tempfile.mkdtemp(prefix="pb190-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = d / "task.md"
        p.write_text(text, encoding="utf-8")
        return p

    def bounce(self, p):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            blocked = lifecycle._gate_bounce("190", p, "closing this task")
        return blocked, err.getvalue()

    def test_a_lead_in_after_the_gates_does_not_block_the_close(self):
        p = self._file(HEAD + "x\n\n## Work Plan\n- [x] A — done\n- [x] B — done\n\n## Notes\n"
                       "- **Ceva**, în scratch:\n  - **The rebuild**, in scratch:\n    - y\n")
        self.assertEqual(core._extract_head_position(p), "(all gates checked)")
        self.assertEqual(self.bounce(p), (False, ""))

    def test_a_lead_in_above_the_first_gate_does_not_block_the_close(self):
        p = self._file(HEAD + "- **Constraint**, from the owner:\n  - z\n\n## Work Plan\n- [x] A — done\n")
        self.assertEqual(core._extract_head_position(p), "(all gates checked)")
        self.assertEqual(self.bounce(p), (False, ""))

    def test_a_fenced_example_is_not_an_open_gate(self):
        p = self._file(HEAD + "x\n\n## Work Plan\n- [x] A — done\n\n```\n- [ ] an example\n```\n")
        self.assertEqual(self.bounce(p), (False, ""))

    def test_an_unreadable_record_still_refuses(self):
        # A record that cannot be read is not "no open gate": the close is refused, with an unknown count.
        p = self._file(HEAD + "x\n\n## Work Plan\n- [x] A — done\n")
        p.unlink()
        p.mkdir()
        blocked, err = self.bounce(p)
        self.assertTrue(blocked)
        self.assertIn("has ? open gate(s)", err)

    def test_a_scan_error_still_refuses(self):
        # Not only an OSError: any failure of the gate scan is an unknown count, and the close is refused.
        from unittest import mock
        p = self._file(HEAD + "x\n\n## Work Plan\n- [x] A — done\n")
        with mock.patch.object(core, "_live_gate_scan", side_effect=ValueError("scan broke")):
            blocked, err = self.bounce(p)
        self.assertTrue(blocked)
        self.assertIn("has ? open gate(s)", err)

    def test_a_lead_in_after_an_open_gate_is_not_the_head(self):
        # With a gate still open, a bold lead-in BELOW it is never reported as the next gate.
        p = self._file(HEAD + "x\n\n## Work Plan\n- [x] A — done\n- [ ] B\n\n## Notes\n- **Ceva**, în scratch:\n")
        self.assertEqual(core._extract_head_position(p), "B")
        blocked, err = self.bounce(p)
        self.assertTrue(blocked)
        self.assertIn("Next open gate: B", err)

    def test_the_refusal_counts_the_live_gates(self):
        p = self._file(HEAD + "x\n\n## Work Plan\n- [x] A — done\n- [ ] B\n\n```\n- [ ] an example\n```\n")
        blocked, err = self.bounce(p)
        self.assertTrue(blocked)
        self.assertIn("has 1 open gate(s)", err)
        self.assertIn("Next open gate: B", err)


if __name__ == "__main__":
    unittest.main()
