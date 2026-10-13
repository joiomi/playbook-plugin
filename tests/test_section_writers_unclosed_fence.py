"""Section writers never run past an unclosed code fence (task 189, PLAN S14 T2b; Gemini #10 #13 #18 #19 of task 188).

An UNCLOSED fence inside `## Blocked` or `## Handoff` makes the fail-closed scan fence every later line, so the
section's span ran to EOF. On 1.6.1 that meant: blocking again after a resume deleted every section after it; adding
a reason to an already-blocked task put it where the reader cannot see it (a `handoff` lost to
`find_unconsumed_handoff`); the resume stamp and a new handoff were spliced past the following sections, and
`write_handoff` moved them into `### Archived handoff`. Each writer now refuses (ValueError) and leaves the file
byte-identical — the fail-closed rule the status splice already follows. A fence that hides nothing (the section is
the last one) is not refused.

Also: `_git_repo_summary` labels a detached HEAD `(detached)` (#14 #17) and `_extract_block_reason` returns None when
the task is not blocked (#15).
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))
from tasks import core  # noqa: E402

TAIL = "\n## Parked\n- keep me\n"


class UnclosedFenceInsideASection(unittest.TestCase):
    def _file(self, text):
        d = Path(tempfile.mkdtemp(prefix="pb189-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "task.md"
        p.write_text(text, encoding="utf-8")
        return p

    def refuses(self, p, write, msg="unclosed code fence"):
        before = p.read_bytes()
        with self.assertRaises(ValueError) as cm:
            write(p)
        self.assertIn(msg, str(cm.exception))
        self.assertEqual(p.read_bytes(), before, "the refused write changed the file")

    def test_blocking_again_after_a_resume_refuses_instead_of_deleting(self):
        p = self._file("# T\n\n## Status\nin_progress\n\n## Blocked\n> r1\n```\nunclosed\n" + TAIL)
        self.refuses(p, lambda f: core.set_task_blocked(f, "r2"))

    def test_a_reason_for_an_already_blocked_task_refuses_instead_of_hiding(self):
        p = self._file("# T\n\n## Status\nblocked\n\n## Blocked\n> r1\n```\nunclosed\n" + TAIL)
        self.refuses(p, lambda f: core.set_task_blocked(f, "handoff"))

    def test_resume_refuses_instead_of_stamping_past_the_next_section(self):
        p = self._file("# T\n\n## Status\nblocked\n\n## Blocked\n> r1\n```\nunclosed\n" + TAIL)
        self.refuses(p, core.resume_blocked_task)

    def test_handoff_refuses_instead_of_archiving_the_next_section(self):
        p = self._file("# T\n\n## Status\nin_progress\n\n## Handoff\nold note\n```\nunclosed code\n" + TAIL)
        self.refuses(p, lambda f: core.write_handoff(f, "## Handoff\nnew note\n"))

    def test_the_handoff_verb_refuses_on_the_handoff_span(self):
        # the handoff half refuses here; that the PAIR lands as one when the block half refuses after a composed
        # handoff is `test_blocked_state.py::test_a_handoff_that_lands_after_the_close_refuses` (impl panel r1, fable)
        p = self._file("# T\n\n## Status\nin_progress\n\n## Handoff\nold note\n```\nunclosed code\n" + TAIL)
        self.refuses(p, lambda f: core.write_handoff_and_block(f, "## Handoff\nnew note\n"))

    def test_a_last_handoff_holding_an_unclosed_fence_refuses_the_next_handoff(self):
        # impl panel r1 (fable, opus, codex): a LAST `## Handoff` hides no section, but archiving it moved its open
        # fence above the fresh `## Handoff` and `## Blocked`, which then no writer or fail-closed reader could find.
        # Opus's sequence through the real functions: a task already blocked gets a handoff (it lands LAST), the
        # agent's notes leave a fence open, the task is resumed, the next handoff refuses.
        p = self._file("# T\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] G\n")
        core.set_task_blocked(p, "waiting")
        core.write_handoff_and_block(p, "## Handoff\nfirst\n\n### Agent notes\n")
        with p.open("a", encoding="utf-8") as fh:
            fh.write("```python\nhalf a snippet\n")
        heads = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, ["## Status", "## Work Plan", "## Blocked", "## Handoff"])   # the handoff is LAST
        core.resume_blocked_task(p)
        self.refuses(p, lambda f: core.write_handoff_and_block(f, "## Handoff\nsecond\n"))
        self.refuses(p, lambda f: core.write_handoff(f, "## Handoff\nsecond\n"))

    def test_a_handoff_archived_under_an_existing_history_refuses_too(self):
        # codex's shape: the history already exists, the archived note is inserted at its top
        p = self._file("# T\n\n## Status\nin_progress\n")
        core.write_handoff(p, "## Handoff\nfirst\n")
        core.write_handoff(p, "## Handoff\nsecond\n")
        with p.open("a", encoding="utf-8") as fh:
            fh.write("```\nunclosed\n")
        self.refuses(p, lambda f: core.write_handoff(f, "## Handoff\nthird\n"))

    def test_a_handoff_below_an_open_fence_elsewhere_refuses(self):
        # the property, not the one shape: the `## Handoff` just written must be LIVE, wherever the open fence is
        p = self._file("# T\n\n## Status\nin_progress\n\n## Notes\n```\nunclosed\n")
        self.refuses(p, lambda f: core.write_handoff(f, "## Handoff\nnote\n"), "above the new `## Handoff`")

    def test_an_open_fence_in_the_handoff_is_never_archived(self):
        # post-D6 single judge (codex, 16:21): the archived note moves UP into `## Handoff history`; its open fence
        # then pairs with a CLOSED fence further down (here the Notes snippet), the fresh `## Handoff` stays live,
        # and `## Blocked` / `## Parked` / `## Notes` vanish for every reader — the block writer then replaced the
        # visible reasons with `handoff` alone. A note holding an open fence is refused before it is moved.
        p = self._file("# T\n\n## Status\nin_progress\n\n## Handoff history\n\n### Archived handoff\nolder\n\n"
                       "## Blocked\n> r1\n\n## Parked\n- keep me\n\n## Notes\n```python\nx = 1\n```\n\n"
                       "## Handoff\nlast note\n```python\nhalf a snippet\n")
        self.refuses(p, lambda f: core.write_handoff(f, "## Handoff\nnew\n"), "inside the current `## Handoff`")
        self.refuses(p, lambda f: core.write_handoff_and_block(f, "## Handoff\nnew\n"),
                     "inside the current `## Handoff`")

    def test_a_blocked_section_that_hides_nothing_keeps_working(self):
        # `## Blocked` LAST with an open fence: the replace path deletes the fence with the section, the append path
        # and the resume stamp land inside the section, where the fail-open readers read them — pinned on what the
        # readers see, not only on the status
        p = self._file("# T\n\n## Status\nblocked\n\n## Blocked\n> r1\n```\nunclosed\n")
        core.set_task_blocked(p, "r2")                                     # already blocked → append path
        self.assertEqual(core._block_reasons(p), ["r1", "r2"])
        core.resume_blocked_task(p)
        self.assertFalse(core._is_blocked(p))
        self.assertTrue(p.read_text(encoding="utf-8").rstrip().splitlines()[-1].startswith("> Resumed "))
        core.set_task_blocked(p, "r3")                                     # resumed → replace path
        lines = p.read_text(encoding="utf-8").splitlines()
        self.assertIsNotNone(core._live_section_span(lines, "## Blocked"), "the new `## Blocked` is hidden")
        self.assertEqual(core._block_reasons(p), ["r3"])
        self.assertNotIn("```", p.read_text(encoding="utf-8"))

    def test_the_section_after_a_closed_fence_survives_every_writer(self):
        # the normal case, pinned structurally (task 188's lesson: order, not substring presence)
        p = self._file("# T\n\n## Status\nin_progress\n\n## Blocked\n> r1\n```\nclosed\n```\n" + TAIL)
        core.set_task_blocked(p, "r2")
        core.resume_blocked_task(p)
        heads = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, ["## Status", "## Parked", "## Blocked"])
        core.write_handoff_and_block(p, "## Handoff\nnote\n")              # impl panel r1, opus: the handoff too
        heads = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, ["## Status", "## Parked", "## Handoff", "## Blocked"])   # resumed → block replaced
        core.write_handoff_and_block(p, "## Handoff\nnote 2\n")            # already blocked → reason appended
        heads = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, ["## Status", "## Parked", "## Blocked", "## Handoff history", "## Handoff"])
        self.assertEqual(core._block_reasons(p), ["handoff", "handoff"])


class DetachedHeadLabel(unittest.TestCase):
    def test_a_detached_head_reads_detached(self):
        d = Path(tempfile.mkdtemp(prefix="pb189g-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"], "HOME": str(d)}
        for a in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "a"]):
            subprocess.run(["git", *a], cwd=d, check=True, env=env)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, env=env, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "checkout", "-q", "--detach", sha], cwd=d, check=True, env=env)
        self.assertEqual(core._git_repo_summary(d)[0], "(detached)")


class BlockReasonOnlyWhileBlocked(unittest.TestCase):
    def test_a_resumed_task_has_no_block_reason(self):
        d = Path(tempfile.mkdtemp(prefix="pb189r-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "task.md"
        p.write_text("# T\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] G\n", encoding="utf-8")
        core.set_task_blocked(p, "old reason")
        self.assertEqual(core._extract_block_reason(p), "old reason")
        core.resume_blocked_task(p)
        self.assertIsNone(core._extract_block_reason(p))


if __name__ == "__main__":
    unittest.main()
