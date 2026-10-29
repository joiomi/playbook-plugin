#!/usr/bin/env python3
"""The close's check of the test contract (task 286, PHASE-B.md item 9b part 7) — its rules that task 342 kept.

The owner, 2026-10-22 08:26 ("Doar ce e sigur greșit"): the close stops on a contract it can judge wrong and passes the
rest. Since task 342 a row is proved by a red and a later green on another tree (`tests/test_simple_red_receipt.py`);
what stays from part 7: a task with no contract is unchanged; a receipt, contract or config changed while the close
ran is refused at the commit (by files, under the task lock); a receipt line the check cannot read blocks; a receipt
that is not a regular file blocks (never a wait); code written before the commit refuses `No behavior changes:`; a
`Test contract:` gate without its section blocks; a project that is not a git repository is said, not judged.

The class and test names are part 7's own (task 342's contract cites them); the harness is task 342's `_Project`.
Pure stdlib unittest. Run: python3 tests/test_red_receipt_part7.py
"""
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))
from tests.test_simple_red_receipt import NEW, _Project  # noqa: E402

ROW1 = ("1", "normal", "f returns 2", NEW)


def _in_process(project: Path, env: dict, fn, *args):
    """Run a CLI function in this process, in `project`, with `env` (for a patched helper)."""
    with mock.patch.dict(os.environ, env, clear=True):
        cwd = os.getcwd()
        os.chdir(project)
        try:
            return fn(*args)
        except SystemExit as e:
            return e.code
        finally:
            os.chdir(cwd)


class Closing(_Project):
    def proved(self):
        """Row 1 seen red, then green after the code changed: a contract the close passes."""
        self.write_new_test()
        self.assertEqual(self.red().returncode, 0)
        self.fix_code()
        self.assertEqual(self.red().returncode, 0)

    def written(self):
        """What a blocked close must leave alone: task.md and the receipt."""
        import hashlib
        out = {}
        for f in (self.task_md, self.task_dir / "red-receipt.jsonl"):
            try:
                out[f.name] = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
            except OSError:
                out[f.name] = "unreadable"
        return out

    def blocked(self):
        before = self.written()
        r, out = self.close()
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("Blocked: cannot close task 001", out)
        self.assertFalse(self.done())
        self.assertEqual(self.written(), before)
        return out

    def close_meanwhile(self, act):
        """The close in this process, `act` run after its second check and before its commit (task 286)."""
        from tasks import core, lifecycle
        real = core.format_verify_receipt

        def meanwhile(*a, **k):
            act()
            return real(*a, **k)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(core, "format_verify_receipt", meanwhile), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = _in_process(self.d, self.env(), lifecycle.cmd_work, ["done"])
        return code, out.getvalue() + err.getvalue()


class Pass(Closing):
    def test_a_task_without_a_contract_is_unchanged(self):
        # no section, no gate — no check, no receipt line
        self.light()
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("red receipt", out)
        self.assertTrue(self.done())

    def test_a_project_without_git_is_said_not_judged(self):
        # a project that is not a repository at all: said, the close passes
        self.light([ROW1])
        import shutil
        shutil.rmtree(self.d / ".git")
        r, out = self.close()
        self.assertIn("not a git repository", out)


class Race(Closing):
    def test_a_receipt_changed_during_the_close_refuses(self):
        # a `tasks red` writing a record while the close ran — refused at the commit, nothing written
        self.light([ROW1])
        self.proved()
        p = self.task_dir / "red-receipt.jsonl"

        def act():
            line = p.read_text(encoding="utf-8").splitlines()[-1]
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        code, out = self.close_meanwhile(act)
        self.assertEqual(code, 1, out)
        self.assertIn("changed while this close ran", out)
        self.assertFalse(self.done())


class RoundOne(Closing):
    def test_a_contract_or_config_changed_during_the_close_refuses(self):
        # the commit's digest covers the contract and the config, not only the receipt
        self.light([ROW1])
        self.proved()
        for change in ("contract", "config"):
            def act(c=change):
                if c == "contract":
                    self.set_rows([ROW1, ("2", "normal", "a new row", "`tests.test_old`")])
                else:
                    self.config(red_timeout=301)
            code, out = self.close_meanwhile(act)
            self.assertEqual(code, 1, change + out)
            self.assertIn("changed while this close ran", out, change)
            self.assertFalse(self.done())
            if change == "contract":
                self.set_rows([ROW1])


class RoundTwo(Closing):
    def test_a_truncated_record_blocks(self):
        # a crash mid-append left half a record: the check cannot read it and blocks, never reads past it
        self.light([ROW1])
        self.proved()
        p = self.task_dir / "red-receipt.jsonl"
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(p.read_text(encoding="utf-8").splitlines()[-1][:40] + "\n")
        self.assertIn("cannot read", self.blocked())

    def test_a_receipt_that_is_no_regular_file_blocks(self):
        # a FIFO in its place: the check never waits on it, it blocks
        self.light([ROW1])
        self.proved()
        p = self.task_dir / "red-receipt.jsonl"
        p.unlink()
        os.mkfifo(p)
        self.assertIn("cannot be read", self.blocked())

    def test_the_gate_without_its_section_blocks(self):
        from tasks.template import CONTRACT_GATE
        self.light()
        text = self.task_md.read_text(encoding="utf-8")
        self.task_md.write_text(text.replace("## Pre-review\n", f"## Work\n- [x] {CONTRACT_GATE} — done\n\n"
                                             "## Pre-review\n", 1), encoding="utf-8")
        self.assertIn("no live `## Test contract` section", self.blocked())


class PostPanel(Closing):
    def test_code_written_before_the_commit_refuses_no_behavior_changes(self):
        # (task 286's run 3, GRAVE) code written after the second check, before the commit, closed a no-code task
        self.light(no_change="the docs only")

        def act():
            (self.code / "generated.py").write_text("X = 1\n", encoding="utf-8")
        code, out = self.close_meanwhile(act)
        self.assertEqual(code, 1, out)
        self.assertFalse(self.done())


if __name__ == "__main__":
    unittest.main()
