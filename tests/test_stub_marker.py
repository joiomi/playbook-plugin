"""Task 144 (retro 134 finding (a)): a stub is a stub by its marker LINE.

`tasks new --stub` writes `<!-- stub:TYPE -->` on a line of its own. Five places decided "this is
an unexpanded stub" by finding the TEXT `<!-- stub:` anywhere in task.md — `tasks work`'s two
activation arms, `core._is_stub_file` (`tasks status`), the retro scanner and the command guard's
irreversible acknowledgement — so a record that merely quotes the marker read as a stub. Live on
2026-10-07: this task's own Intent quoted it, and `tasks work 144` "expanded" the light task into
the full feature template. One reader now: the whole line, outside a code fence.

Run: python3 -m unittest tests.test_stub_marker
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))

from tasks import core, retro  # noqa: E402
from tasks.template import render_stub_template  # noqa: E402

QUOTING = ("# 007 - Notes\n\n## Status\nin_progress\n\n## Intent\nExplain the `<!-- stub:feature -->` "
           "marker, written as '<!-- stub:TYPE -->'.\n\n## Example\n```\n<!-- stub:feature -->\n```\n\n"
           "## Work\n- [x] done\n")


class OneReader(unittest.TestCase):
    def test_the_writers_marker_line_is_a_stub(self):
        self.assertEqual(core.stub_marker_type(render_stub_template(3, "F", "x", "feature")), "feature")
        self.assertEqual(core.stub_marker_type("# 1 - x\n\n<!-- stub:sp-eval -->\n"), "sp-eval")

    def test_a_quoted_or_fenced_marker_is_not(self):
        self.assertIsNone(core.stub_marker_type(QUOTING))
        self.assertIsNone(core.stub_marker_type("text <!-- stub:feature --> more\n"))
        self.assertIsNone(core.stub_marker_type("<!-- stub:feature --> is the line the writer emits\n"))

    def test_status_does_not_call_a_quoting_record_a_stub(self):
        with tempfile.TemporaryDirectory() as d:
            tf = Path(d) / "task.md"
            tf.write_text(QUOTING, encoding="utf-8")
            self.assertFalse(core._is_stub_file(tf))
            tf.write_text(render_stub_template(3, "F", "x", "feature"), encoding="utf-8")
            self.assertTrue(core._is_stub_file(tf))

    def test_the_retro_does_not_either(self):
        self.assertFalse(retro._detect_type(QUOTING).startswith("stub"))
        self.assertEqual(retro._detect_type(render_stub_template(3, "F", "x", "light")), "stub:light")


class TheLiveCase(unittest.TestCase):
    def run_tasks(self, cwd, *args):
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="pid-stub-marker")
        env.pop("BASH_ENV", None)
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=120)

    def project(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="pb-144-"))
        self.addCleanup(__import__("shutil").rmtree, d, True)
        (d / ".agent").mkdir()
        return d

    def test_an_intent_quoting_the_marker_is_activated_not_expanded(self):
        d = self.project()
        r = self.run_tasks(d, "new", "light", "quoting", "--", "explain", "the", "'<!-- stub:TYPE -->'", "marker")
        self.assertEqual(r.returncode, 0, r.stderr)
        (tf,) = (d / ".agent" / "tasks").glob("*/task.md")
        before_gates = tf.read_text(encoding="utf-8").count("- [ ]")
        r = self.run_tasks(d, "work", "001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Expanded stub", r.stdout)
        text = tf.read_text(encoding="utf-8")
        self.assertEqual(text.count("- [ ]"), before_gates)            # still the light template
        self.assertIn("'<!-- stub:TYPE -->'", text)

    def test_a_real_stub_still_expands(self):
        d = self.project()
        self.assertEqual(self.run_tasks(d, "new", "--stub", "light", "later", "--", "do", "it").returncode, 0)
        r = self.run_tasks(d, "work", "001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Expanded stub", r.stdout)


class ImplPanelRound1(unittest.TestCase):
    """Task 144, implementation panel round 1 (opus, codex-high)."""

    def project(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="pb-144b-"))
        self.addCleanup(__import__("shutil").rmtree, d, True)
        (d / ".agent").mkdir()
        return d

    def test_only_the_place_the_writer_uses_counts(self):
        # opus: a marker ALONE on a line of the Intent still read as a stub — and expansion
        # keeps the Intent, so every later `tasks work` would re-expand. The writer puts it
        # between the title and the first section; nowhere else counts.
        self.assertIsNone(core.stub_marker_type(
            "# 7 - x\n\n## Status\npending\n\n## Intent\n<!-- stub:feature -->\n"))
        self.assertEqual(core.stub_marker_type(render_stub_template(3, "F", "<!-- stub:feature -->", "light")),
                         "light")

    def test_a_dotted_custom_type_is_read_whole(self):
        # codex-high: `tasks new --stub sp.eval` writes `<!-- stub:sp.eval -->`; the reader dropped it
        self.assertEqual(core.stub_marker_type("# 1 - x\n\n<!-- stub:sp.eval -->\n\n## Status\npending\n"),
                         "sp.eval")

    def test_an_expanded_stub_whose_intent_holds_the_marker_is_not_expanded_again(self):
        d = self.project()
        run = TheLiveCase.run_tasks
        self.assertEqual(run(self, d, "new", "--stub", "light", "s", "--", "<!-- stub:feature -->").returncode, 0)
        r = run(self, d, "work", "001")
        self.assertIn("Expanded stub", r.stdout)
        (tf,) = (d / ".agent" / "tasks").glob("*/task.md")
        tf.write_text(tf.read_text(encoding="utf-8").replace("- [ ]", "- [x]", 1), encoding="utf-8")
        before = tf.read_text(encoding="utf-8")
        r = run(self, d, "work", "001")
        self.assertNotIn("Expanded stub", r.stdout)
        self.assertEqual(tf.read_text(encoding="utf-8").count("- [x]"), before.count("- [x]"))

    def test_an_edit_that_lands_during_the_expansion_is_kept(self):
        # opus, codex-high [PRE-EXISTING]: the locked rewrite wrote content built from text read
        # BEFORE the lock, so an edit in between was overwritten. It declines instead.
        import contextlib
        import io
        from unittest import mock
        from tasks import lifecycle
        d = self.project()
        self.assertEqual(TheLiveCase.run_tasks(self, d, "new", "--stub", "light", "s", "--", "x").returncode, 0)
        (tf,) = (d / ".agent" / "tasks").glob("*/task.md")
        real = core.append_standing_gates

        def an_edit_meanwhile(*a, **k):
            tf.write_text(tf.read_text(encoding="utf-8") + "\nCONCURRENT EDIT\n", encoding="utf-8")
            return real(*a, **k)
        prev = os.getcwd()
        os.chdir(d)
        self.addCleanup(os.chdir, prev)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"PLAYBOOK_SESSION_ID": "pid-stub-marker"}), \
                mock.patch.object(core, "append_standing_gates", an_edit_meanwhile), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                lifecycle.cmd_work(["001"])
            except SystemExit:
                pass
        text = tf.read_text(encoding="utf-8")
        self.assertIn("CONCURRENT EDIT", text)
        self.assertIn("changed while", err.getvalue())
        # impl panel r2 (opus, codex-high, codex-medium): the refusal came AFTER the pointer was
        # published and the status written — "nothing written" was false, and the session was
        # left active on an unexpanded stub. Now the expansion runs before both.
        self.assertFalse((d / ".agent" / "sessions" / "pid-stub-marker" / "current_state").exists())
        self.assertIn("## Status\npending", text)
        self.assertIsNotNone(core.stub_marker_type(text))


class PostD6Run1(unittest.TestCase):
    """Task 144, post-D6 single judge run 1 (codex): the expansion now runs before the status
    steps, and the expanded template says `pending` — so a blocked stub's resume or a done
    stub's reopen then failed, leaving a changed task and no pointer."""

    def _stub(self, status):
        d = Path(tempfile.mkdtemp(prefix="pb-144d-"))
        self.addCleanup(__import__("shutil").rmtree, d, True)
        (d / ".agent").mkdir()
        self.assertEqual(TheLiveCase.run_tasks(self, d, "new", "--stub", "light", "s", "--", "x").returncode, 0)
        (tf,) = (d / ".agent" / "tasks").glob("*/task.md")
        tf.write_text(tf.read_text(encoding="utf-8").replace("## Status\npending", "## Status\n" + status), encoding="utf-8")
        return d, tf

    def test_a_blocked_stub_is_expanded_and_resumed(self):
        d, tf = self._stub("blocked")
        r = TheLiveCase.run_tasks(self, d, "work", "001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Expanded stub", r.stdout)
        self.assertIn("## Status\nin_progress", tf.read_text(encoding="utf-8"))

    def test_a_custom_playbooks_live_status_is_the_one_carried(self):
        # post-D6 run 2: a literal "## Status\npending" replace missed a `## Status ##` heading
        # and changed a fenced example instead — the resume then failed half-way
        d = Path(tempfile.mkdtemp(prefix="pb-144e-"))
        self.addCleanup(__import__("shutil").rmtree, d, True)
        (d / ".agent" / "playbooks").mkdir(parents=True)
        (d / ".agent" / "playbooks" / "sp-eval.md").write_text(
            "# {{NNN}} - {{TITLE}}\n\n## Example\n```\n## Status\npending\n```\n\n## Status ##\npending\n\n"
            "## Risk\nreversible\n\n## Intent\n(one line — what to do and what proves it worked)\n\n"
            "## Work\n- [ ] the gate\n", encoding="utf-8")
        self.assertEqual(TheLiveCase.run_tasks(self, d, "new", "--stub", "sp-eval", "s", "--", "x").returncode, 0)
        (tf,) = (d / ".agent" / "tasks").glob("*/task.md")
        tf.write_text(tf.read_text(encoding="utf-8").replace("## Status\npending", "## Status\nblocked", 1),
                      encoding="utf-8")
        r = TheLiveCase.run_tasks(self, d, "work", "001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = tf.read_text(encoding="utf-8")
        # the live status (the shared writer writes its heading in the canonical form)
        self.assertEqual(core._status_from_lines(core._physical_lines(text)), "in_progress")
        self.assertIn("```\n## Status\npending\n```", text)            # the example untouched

    def test_a_done_stub_is_expanded_and_reopened(self):
        d, tf = self._stub("done")
        r = TheLiveCase.run_tasks(self, d, "work", "001", "--reopen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Expanded stub", r.stdout)
        self.assertIn("## Status\nin_progress", tf.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
