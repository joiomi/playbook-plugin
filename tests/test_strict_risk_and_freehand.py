#!/usr/bin/env python3
"""The `## Risk` value and a gate's `Freehand` start are read with spaces and tabs only (task 224).

Task 214's impl round 3 (grok) found two readings that took every character `str.isspace()` accepts for white space:
`## Risk` + `\\x85irreversible` read `irreversible` (V2 — the close's bar and the command guard's acknowledgement follow
that value), and `- [ ] \\x85Freehand now` released the stop hook (V3). The owner, 2026-10-16 09:48 (task 218):
"1 respins, 2 și 3 reparate (Recomandat)" — "Riscul și „Freehand" se citesc strict în Faza B": a separator other than
spaces or tabs leaves the value unrecognised (`unclassified`) and releases nothing.

Pure stdlib unittest. Run: python3 tests/test_strict_risk_and_freehand.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import template  # noqa: E402
from tasks.core import RISK_CLASSES, _freehand_release_allowed, _live_gate_state, extract_risk_from_text  # noqa: E402

# Every character `str.isspace()` accepts but the two the ruling keeps (space, tab) and the line feed that ends a line.
SEPARATORS = [chr(c) for c in range(0x110000) if chr(c).isspace() and chr(c) not in " \t\n"]


def _risk(value: str) -> str:
    return extract_risk_from_text(f"# 001 - X\n\n## Status\nin_progress\n\n## Risk\n{value}\n\n## Intent\nx\n")


def _freehand_lines(first: str) -> "list[str]":
    """A feature render with every gate checked but the debrief, whose text after the box is `first`."""
    text = template.render_template(1, "X", "feature")
    out = []
    for ln in text.split("\n"):
        if ln.startswith("- [ ] Freehand"):
            out.append(f"- [ ]{first}")
        else:
            out.append(ln.replace("- [ ]", "- [x]"))
    return out


class TheRiskValue(unittest.TestCase):

    def test_a_separator_before_the_value_leaves_it_unclassified(self):
        self.assertGreater(len(SEPARATORS), 20)
        for value in RISK_CLASSES:
            for sep in SEPARATORS:
                with self.subTest(value=value, sep=hex(ord(sep))):
                    self.assertEqual(_risk(f"{sep}{value}"), "unclassified")

    def test_a_separator_after_the_value_leaves_it_unclassified(self):
        for value in RISK_CLASSES:
            for sep in SEPARATORS:
                if sep == "\r":
                    continue        # a CR at the very end of the line is its line end (a CRLF file read raw)
                with self.subTest(value=value, sep=hex(ord(sep))):
                    self.assertEqual(_risk(f"{value}{sep}why"), "unclassified")
                    self.assertEqual(_risk(f"{value}{sep}"), "unclassified")

    def test_the_plain_forms_still_read(self):
        for value in RISK_CLASSES:
            for form in (value, f"  {value}", f"\t{value}", f"`{value}`", f"**{value}**", f"{value} — the reason",
                         f"{value}\tthe reason", value.capitalize(), f"{value}  ", f"{value}\r",
                         f"` {value} `", f"** {value} **", f"_ {value}_"):        # marks around spaces (impl round 1)
                with self.subTest(form=form):
                    self.assertEqual(_risk(form), value)
        crlf = "# 001 - X\r\n\r\n## Risk\r\nassertive\r\n\r\n## Intent\r\nx\r\n"
        self.assertEqual(extract_risk_from_text(crlf), "assertive")
        self.assertEqual(_risk("assertive\r\r"), "unclassified")    # one CR is the line's end; CRLF never leaves two

    def test_the_command_guard_does_not_take_such_a_value_as_irreversible(self):
        d = Path(tempfile.mkdtemp(prefix="pb224cg-"))
        self.addCleanup(shutil.rmtree, d, True)
        agent = d / ".agent"
        (agent / "sessions" / "pid-strict-test").mkdir(parents=True)
        (agent / "sessions" / "pid-strict-test" / "current_state").write_text("001", encoding="utf-8")
        (agent / "tasks" / "001-t").mkdir(parents=True)
        tf = agent / "tasks" / "001-t" / "task.md"
        spec = importlib.util.spec_from_file_location("cg_224", _PLAYBOOK / "scripts" / "command_guard.py")
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        for value, want in (("irreversible", True), ("\x85irreversible", False), ("\u2028irreversible", False),
                            ("irreversible\xa0x", False)):
            tf.write_text(f"# 001\n\n## Status\nin_progress\n\n## Risk\n{value}\n", encoding="utf-8")
            with self.subTest(value=repr(value)), mock.patch.dict(os.environ, {"PLAYBOOK_SESSION_ID": "pid-strict-test"}):
                self.assertEqual(g._active_task_is_irreversible(str(d)), want)


class TheFreehandStart(unittest.TestCase):

    def test_a_separator_before_freehand_releases_nothing(self):
        for sep in SEPARATORS:                 # a CR too (impl round 1): it is a line's end only at its very end
            with self.subTest(sep=hex(ord(sep))):
                self.assertFalse(_freehand_release_allowed(_freehand_lines(f" {sep}Freehand now")))

    def test_the_plain_forms_still_release(self):
        for first in (" Freehand now", "\tFreehand now", "  Freehand — stay to discuss the work with the user."):
            with self.subTest(first=repr(first)):
                self.assertTrue(_freehand_release_allowed(_freehand_lines(first)))

    def test_the_first_open_gate_text_keeps_such_a_character(self):
        """What codex's echo reads (`_scan_gates` → `_live_gate_state`) and its `_FREEHAND_RE` then matches."""
        from provider.codex_hooks import _FREEHAND_RE
        _u, _t, first = _live_gate_state(_freehand_lines(" \x85Freehand now"))
        self.assertEqual(first, "\x85Freehand now")
        self.assertIsNone(_FREEHAND_RE.match(first))

    def _task_status(self, *args) -> "list[str]":
        r = subprocess.run([sys.executable, str(_PLAYBOOK / "scripts" / "task-status.py"), *args],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split("\n")

    def test_the_stop_hooks_fields_and_the_echo(self):
        d = Path(tempfile.mkdtemp(prefix="pb224ts-"))
        self.addCleanup(shutil.rmtree, d, True)
        tf = d / "task.md"
        for first, release in ((" \x85Freehand now", "hold"), (" \u2028Freehand now", "hold"),
                               (" \rFreehand now", "hold"), (" Freehand now", "release")):
            tf.write_text("\n".join(_freehand_lines(first)) + "\n", encoding="utf-8")
            with self.subTest(first=repr(first)):
                fields = self._task_status("--fields", str(tf))
                self.assertEqual(fields[3], release)
                echo = self._task_status("--echo", str(tf))
                self.assertEqual(echo[7].startswith("Freehand"), release == "release", echo[7])


if __name__ == "__main__":
    unittest.main(verbosity=2)
