"""Task 123 (PLAN S11; gauntlet 2 G2-18): `tasks status` names a stub.

A stub (`tasks new --stub`) has no gates until `tasks work <N>` expands it, so the status
line read `- | (all gates checked)` — a claim about gates that do not exist yet. It now
says it is a stub and how to expand it. Display only: the lifecycle's own stub check
(activation expands it) and the "(all gates checked)" guards are unchanged.

Run: python3 -m unittest tests.test_stub_status
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "playbook"))

from tasks import core  # noqa: E402


class StubStatus(unittest.TestCase):
    def _status(self, body: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            td = p / ".agent" / "tasks" / "003-f1"
            td.mkdir(parents=True)
            (td / "task.md").write_text(body, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                core.task_status(p)
            return out.getvalue()

    def test_a_stub_is_named_not_reported_all_checked(self):
        from tasks.template import render_stub_template
        out = self._status(render_stub_template(num=3, title="F1", intent_text="x", task_type="feature"))
        self.assertNotIn("(all gates checked)", out)
        self.assertIn("(stub — `tasks work 003` expands it)", out)

    def test_a_task_whose_gates_are_all_checked_still_says_so(self):
        out = self._status("# 003 - F1\n\n## Status\nin_progress\n\n## Work\n- [x] a gate\n")
        self.assertIn("(all gates checked)", out)


if __name__ == "__main__":
    unittest.main()
