#!/usr/bin/env python3
"""`tasks compact` refuses a block holding a gate the counters read, a byte-order mark before it too (audit 4's G9).

`compact._validate` refused a block with a gate line by `^\\s*- \\[[ xX]\\]`, on the line as written. The counters
read a gate after any leading U+FEFF (`core._gate_counts`, BOM-tolerant since task 072). So a block holding
`\\ufeff- [ ] …` moved to task-archive.md: the open gate left task.md, the counters no longer saw it, and a task could
close with it never ticked (his blind session, 2026-10-25; probed in task 306, evidence/m2-mine/g7_g9_probes.py).
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))
from tasks.compact import cmd_compact  # noqa: E402
from tasks.core import _gate_counts  # noqa: E402

BODY = """# 012 - Example

## Status
in_progress

## Intent
Ship the thing.

## Work Plan
- [x] build it — done, works

## Implementation Review
<!-- archive:start -->
### Round 1 findings
GATE
<!-- archive:end -->

## Parked
Nothing.
"""


class ABomLedGateStays(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        d = self.root / ".agent" / "tasks" / "012-example"
        d.mkdir(parents=True)
        self.task_md = d / "task.md"
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)

    def compact(self, gate_line):
        text = BODY.replace("GATE", gate_line)
        self.task_md.write_text(text, encoding="utf-8")
        before = _gate_counts(text)
        out, err, code = io.StringIO(), io.StringIO(), 0
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                cmd_compact(["12"])
        except SystemExit as e:
            code = e.code or 0
        return code, err.getvalue(), text, before

    def test_an_open_gate_after_a_byte_order_mark_is_refused(self):
        for line in ("﻿- [ ] a gate after a byte-order mark", "﻿﻿  - [ ] after two marks and spaces",
                     "﻿- [x] a ticked one after a mark"):
            code, err, text, before = self.compact(line)
            self.assertEqual(code, 1, line)
            self.assertIn("gate checkbox", err, line)
            self.assertEqual(self.task_md.read_text(encoding="utf-8"), text, line)       # nothing moved
            self.assertEqual(_gate_counts(self.task_md.read_text(encoding="utf-8")), before, line)
            self.assertFalse((self.task_md.parent / "task-archive.md").exists(), line)

    def test_a_receipt_entry_after_a_byte_order_mark_is_refused(self):
        # impl round 1 (opus, sonnet): the same strip covers the receipt-entry check; it only refuses more
        line = "\ufeff### 2026-10-25T10:00:00+02:00 · risk assertive · commit abc1234"
        code, err, text, _before = self.compact(line)
        self.assertEqual(code, 1)
        self.assertIn("verification-receipt entry", err)
        self.assertEqual(self.task_md.read_text(encoding="utf-8"), text)                  # nothing moved
        self.assertFalse((self.task_md.parent / "task-archive.md").exists())

    def test_control_the_counters_count_that_gate(self):
        self.assertEqual(_gate_counts(BODY.replace("GATE", "﻿- [ ] a gate")), (1, 2))


if __name__ == "__main__":
    unittest.main()
