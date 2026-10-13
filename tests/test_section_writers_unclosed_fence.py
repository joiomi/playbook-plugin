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

    def refuses(self, p, write):
        before = p.read_bytes()
        with self.assertRaises(ValueError) as cm:
            write(p)
        self.assertIn("unclosed code fence", str(cm.exception))
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

    def test_handoff_and_block_together_refuse_as_one(self):
        p = self._file("# T\n\n## Status\nin_progress\n\n## Handoff\nold note\n```\nunclosed code\n" + TAIL)
        self.refuses(p, lambda f: core.write_handoff_and_block(f, "## Handoff\nnew note\n"))

    def test_a_fence_that_hides_nothing_is_not_refused(self):
        # the section is the LAST one: no section can be lost, the writers keep working as before
        p = self._file("# T\n\n## Status\nin_progress\n\n## Blocked\n> r1\n```\nunclosed\n")
        core.set_task_blocked(p, "r2")
        self.assertEqual(core._extract_status(p), "blocked")

    def test_the_section_after_a_closed_fence_survives_every_writer(self):
        # the normal case, pinned structurally (task 188's lesson: order, not substring presence)
        p = self._file("# T\n\n## Status\nin_progress\n\n## Blocked\n> r1\n```\nclosed\n```\n" + TAIL)
        core.set_task_blocked(p, "r2")
        core.resume_blocked_task(p)
        heads = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, ["## Status", "## Parked", "## Blocked"])


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
