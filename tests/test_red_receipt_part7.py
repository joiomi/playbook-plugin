#!/usr/bin/env python3
"""The red receipt, part 7 (PHASE-B.md item 9b; task 286's contract): the close's check, on the owner's answers.

A task with a test contract does not close while a row is certainly wrong (not run, stale, skipped, run badly, red now,
or a late row proved only on the start) — the owner, 2026-10-22 08:26: "Doar ce e sigur greșit"; the other states pass,
said; a receipt that cannot be judged blocks; `--force --reason` overrides, recorded; a receipt changed during the close
refuses it at the commit. Plus task 285's last review.

Each test names the row of task 286's contract it proves. Real temporary git projects from part 1's `Project`, the CLI
driven as `python3 -m tasks.cli`. Pure stdlib unittest. Run: python3 tests/test_red_receipt_part7.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
import tests.test_red_receipt_state as _state  # noqa: E402
from tests.test_red_receipt import Project, _in_process  # noqa: E402
from tests.test_red_receipt_part5 import View, _rows  # noqa: E402

ROW1 = ("1", "normal", "f returns 2", "`tests.test_new`")
ROW2 = ("2", "must-not", "the old test still holds", "`tests.test_old`")
LATE = ("2", "edge", "f returns 2 by its own test", "`tests.test_new.N.test_two`")


class Closing(Project):
    def light(self, contract=None, gate=False, risk=None):
        _state.Close.light(self, contract=contract, gate=gate)
        if risk:
            text = self.task_md.read_text(encoding="utf-8").replace("## Risk\nreversible\n", f"## Risk\n{risk}\n")
            self.task_md.write_text(text, encoding="utf-8")

    def commit_code(self, msg):
        self.g("add", "-A", cwd=self.code)
        self.g("commit", "-qm", msg, cwd=self.code)
        return self.head(self.code)

    def close(self, *extra):
        r = self.cli("work", "done", *extra, timeout=120)
        return r, r.stdout + r.stderr

    def done(self):
        return "## Status\ndone" in self.task_md.read_text(encoding="utf-8")

    def written(self):
        """What a blocked close must leave alone: task.md, the receipt, the enforcement journal (sonnet)."""
        import hashlib
        out = {}
        for f in (self.task_md, self.task_dir / "red-receipt.jsonl", self.d / ".agent" / "journal" / "enforcement.jsonl"):
            try:
                out[f.name] = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
            except OSError:
                out[f.name] = "unreadable"
        return out

    def blocked(self, *extra, after_verify=False):
        before = self.written()
        r, out = self.close(*extra)
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("Blocked: cannot close task 001", out)
        self.assertFalse(self.done())
        now = self.written()
        if after_verify:                                # verify's own run is journaled before the second check
            now.pop("enforcement.jsonl"), before.pop("enforcement.jsonl")
        self.assertEqual(now, before)
        return out

    def rewrite(self, edit):
        p = self.task_dir / "red-receipt.jsonl"
        recs = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        for rec in recs:
            edit(rec)
        os.chmod(p, 0o644)
        p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


class Block(Closing):
    def test_a_row_not_run_blocks_until_it_is_run(self):
        # row 1 (Q1): a row with no record is not run — the close stops, names it and its refresh; once run it closes
        self.light([ROW1])
        self.change_code()
        out = self.blocked()
        self.assertRegex(out, r"row 1: no record")
        self.assertIn("tasks red 001 --rows 1", out)
        self.assertEqual(self.cli("red", "1").returncode, 0)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(self.done())

    def test_each_certainly_wrong_state_blocks(self):
        # row 1 (Q1): each state of his list stops the close — the record's verdict set to it, then a stale row
        first = self.head(self.code)
        (self.code / "helper.py").write_text("Y = 2\n", encoding="utf-8")
        self.commit_code("the start's own code")                   # the start sits on a commit after `first`
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        for verdict, why in (("skipped", "skipped"), ("partly skipped", "skipped"), ("refused", "run badly"),
                             ("unrunnable", "run badly"), ("launch failed", "run badly"), ("timeout", "run badly"),
                             ("broken", "red now"), ("regressed", "red now")):
            self.rewrite(lambda rec, v=verdict: rec.update(verdict=v))
            out = self.blocked()
            self.assertRegex(out, rf"the test contract: row 1: {verdict} \({why}\)", verdict)
        self.rewrite(lambda rec: rec.update(verdict="proved"))
        p = self.code / "tests" / "test_old.py"                     # any test edit makes the row stale
        p.write_text(p.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        self.assertRegex(self.blocked(), r"row 1: stale")
        # a late row with no base of its own: proved on the task's start only
        p.write_text(p.read_text(encoding="utf-8").replace("\n# edited\n", ""), encoding="utf-8")
        self.set_contract([ROW1, LATE])
        self.assertEqual(self.cli("red", "1", "--rows", "2").returncode, 0)
        self.assertRegex(self.blocked(), r"row 2: proved on the task's start only")
        # a late row on another base that is not its own (opus): older code, an unchecked base
        for base in (first, "1" * 40):
            self.rewrite(lambda rec, b=base: rec.update(base={"code": {"commit": b, "used": "commit"}})
                         if rec["row"] == "2" else None)
            self.assertRegex(self.blocked(), r"row 2: proved on (older code|an unchecked base)")

    def test_a_lost_start_says_force_is_the_way(self):
        # row 1: no run can be judged against an uncaptured start — the block says so
        self.light([ROW1])
        b = self.task_dir / "base.json"
        doc = json.loads(b.read_text(encoding="utf-8"))
        doc["scopes"]["code"].update({"tree": None, "clean": False})
        b.write_text(json.dumps(doc), encoding="utf-8")
        out = self.blocked()
        self.assertIn("cannot be refreshed", out)
        self.assertIn("no run can be judged against the start — `--force --reason` is the only way through", out)

    def test_the_receipt_blocks_before_verify_and_the_review(self):
        # row 7 (sonnet): an assertive task with no panel and a row not run — the receipt's block, before verify runs
        # and before any judge is asked
        self.light([ROW1], risk="assertive")
        self.change_code()
        out = self.blocked()
        self.assertIn("row 1: no record", out)
        self.assertIn("the test contract", out)
        self.assertNotIn("Verifying close", out)


class Pass(Closing):
    def test_the_other_states_pass(self):
        # row 2: already true, partly proved, a base the receipt places on older code or cannot judge, a code change
        # since the run (Q3), no skip reader — each said, none stops the close
        before = self.head(self.code)
        (self.code / "helper.py").write_text("Y = 2\n", encoding="utf-8")
        self.commit_code("code before the start")
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        from tasks import red
        for verdict in ("already true", "partly proved"):
            self.rewrite(lambda rec, v=verdict: rec.update(verdict=v))
            got = _check(self, red)
            self.assertEqual(got["blocks"], [], verdict)
        self.rewrite(lambda rec: rec.update(verdict="proved", base={"code": {"commit": before, "used": "commit"}}))
        got = _check(self, red)
        self.assertIn("proved on older code", "\n".join(got["lines"]))
        self.assertEqual(got["blocks"], [])
        self.rewrite(lambda rec: rec.update(base={"code": {"commit": "1" * 40, "used": "commit"}}))
        got = _check(self, red)
        self.assertIn("proved on an unchecked base", "\n".join(got["lines"]))
        self.assertEqual(got["blocks"], [])
        self.rewrite(lambda rec: rec.update(base={"code": {"commit": before, "used": "commit"}}))
        (self.code / "src.py").write_text("def f():\n    return 2  # moved\n", encoding="utf-8")     # Q3: a flag
        got = _check(self, red)
        self.assertIn("the working state changed since its run", "\n".join(got["lines"]))
        self.assertEqual(got["blocks"], [])

        def no_reader(rec):
            rec["reader"] = ""
            rec["now"]["reader"] = ""
            for t in rec.get("tests") or []:
                t["base"]["reader"] = t["now"]["reader"] = ""
        self.rewrite(no_reader)
        got = _check(self, red)
        self.assertIn("no skip reader", "\n".join(got["lines"]).lower())
        self.assertEqual(got["blocks"], [])
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(self.done())
        # the table (sonnet, agy, opus): a spent budget alone blocks nothing; the tests' digest not read does
        rows = [{"row": "1", "state": "proved on an unchecked base", "late": False}]
        self.assertEqual(red.close_blocks({"rows": rows, "notes": [], "undecided": ["budget"]}), [])
        self.assertNotEqual(red.close_blocks({"rows": rows, "notes": [], "undecided": ["tests"]}), [])

    def test_a_project_without_git_is_said_not_judged(self):
        # row 15 (owner, M1439): no git at all — `tasks red` can never run; said, and the close passes
        import shutil
        from tests.test_red_receipt import _contract
        shutil.rmtree(self.d / ".git")
        shutil.rmtree(self.code)
        self.config(code_roots=[])
        r = self.cli("new", "light", "probe", "probe the close")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.task_dir = next((self.d / ".agent" / "tasks").glob("001-*"))
        self.task_md = self.task_dir / "task.md"
        text = self.task_md.read_text(encoding="utf-8").replace("## Risk\nunclassified\n", "## Risk\nreversible\n")
        text = "\n".join(ln.replace("- [ ] ", "- [x] ") + (" — done" if ln.startswith("- [ ] ") else "")
                         for ln in text.split("\n"))
        text = text.replace("## Pre-review\n", _contract([ROW1]) + "## Pre-review\n", 1)
        self.task_md.write_text(text, encoding="utf-8")
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("not judged: not a git repository", out)
        self.assertTrue(self.done())
        from tasks import red                           # any `.git` on the path is a repository, git able or not
        (self.d / ".git").mkdir()
        self.assertFalse(red._not_a_repository(self.d))

    def test_no_behavior_changes_needs_no_code_change(self):
        # row 16 (owner, M1439): a task with nothing to test says why; the CLI holds it to no code change
        self.light([ROW1])
        text = self.task_md.read_text(encoding="utf-8")
        head, rest = text.split("## Test contract", 1)
        tail = rest.split("## Pre-review", 1)[1]
        self.task_md.write_text(head + "## Test contract\n<!-- pin -->\n\nNo behavior changes: the docs only.\n\n"
                                "## Pre-review" + tail, encoding="utf-8")
        (self.code / "README.md").write_text("docs\n", encoding="utf-8")    # not code
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("No behavior changes", out)
        self.assertTrue(self.done())

    def test_no_behavior_changes_with_a_code_change_blocks(self):
        # row 16: the reason holds only while no code file changed since the start
        self.light([ROW1])
        text = self.task_md.read_text(encoding="utf-8")
        head, rest = text.split("## Test contract", 1)
        tail = rest.split("## Pre-review", 1)[1]
        self.task_md.write_text(head + "## Test contract\n<!-- pin -->\n\nNo behavior changes: the docs only.\n\n"
                                "## Pre-review" + tail, encoding="utf-8")
        self.change_code()
        self.assertIn("src.py", self.blocked())

    def test_a_task_without_a_contract_is_unchanged(self):
        # row 6 (must-not): no section, no gate — no check, no red-receipt line
        self.light()
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("red receipt", out)
        self.assertTrue(self.done())


def _check(case, red):
    """`close_check` in this process, on the case's project (part 7's function; part 6 had only the report)."""
    fn = getattr(red, "close_check", None)
    case.assertIsNotNone(fn, "the close has no check (part 6: the report only)")
    return _in_process(case.d, case.env(), fn, case.d, case.task_md)


class FailClosed(Closing):
    def test_a_receipt_that_cannot_be_judged_blocks(self):
        # row 3 (fail closed): a guard that cannot judge refuses
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        p = self.task_dir / "red-receipt.jsonl"
        keep = p.read_bytes()
        p.unlink()
        os.mkfifo(p)                                                # a FIFO in its place: never a wait
        self.assertIn("cannot be read", self.blocked())
        p.unlink()
        os.symlink(self.d / "elsewhere.jsonl", p)                   # a link in its place
        (self.d / "elsewhere.jsonl").write_bytes(keep)
        self.assertIn("cannot be read", self.blocked())
        p.unlink()
        p.mkdir()                                                   # a folder in its place
        self.assertIn("cannot be read", self.blocked())
        p.rmdir()
        p.write_bytes(keep)
        from tasks import red
        got = _in_process(self.d, self.env(), getattr(red, "close_check", lambda *a, **k: None), self.d,
                          self.task_md, 0.0)                         # a spent budget: the staleness not checked
        self.assertIsNotNone(got, "the close has no check")
        self.assertTrue(any("not checked" in b for b in got["blocks"]), got["blocks"])
        with mock.patch.object(red, "state_of", side_effect=RuntimeError("boom")):
            got = _in_process(self.d, self.env(), red.close_check, self.d, self.task_md)
        self.assertTrue(any("boom" in b for b in got["blocks"]), got["blocks"])
        self.set_contract([ROW1, ("1", "normal", "again", "`tests.test_old`")])    # a row number twice
        self.assertIn("refuses the contract", self.blocked())
        self.set_contract([ROW1, ("4b", "normal", "a label", "`tests.test_old`")])  # a non-numeric number
        self.assertRegex(self.blocked(), r"the test contract: row 4b is not a number")
        self.set_contract([ROW1, ("", "normal", "no number", "`tests.test_old`")])  # no number
        self.assertRegex(self.blocked(), r"the test contract: a row with no number")
        self.set_contract([ROW1])
        self.rewrite(lambda rec: rec.update(verdict="made up"))     # a state the close does not know: fail closed
        self.assertIn("a state the close does not know", self.blocked())
        text = self.task_md.read_text(encoding="utf-8")
        self.task_md.write_text(text.split("## Test contract")[0] + "## Test contract\n<!-- pin -->\n\n"
                                "| # | Kind | Behavior | Test that proves it |\n|---|---|---|---|\n\n## Pre-review"
                                + text.split("## Pre-review", 1)[1], encoding="utf-8")     # no row
        self.assertIn("no row", self.blocked())

    def test_a_removed_contract_still_blocks(self):
        # row 14 (sonnet): the activation recorded a contract; deleting the section does not turn the check off
        self.light([ROW1])
        text = self.task_md.read_text(encoding="utf-8")
        head, rest = text.split("## Test contract", 1)
        self.task_md.write_text(head + "## Pre-review" + rest.split("## Pre-review", 1)[1], encoding="utf-8")
        self.assertIn("recorded a test contract", self.blocked())

    def test_the_gate_without_its_section_blocks(self):
        # row 3: the gate says the task has a contract; the section is gone
        self.light(gate=True)
        self.assertIn("no live `## Test contract` section", self.blocked())


class Force(Closing):
    def test_force_names_what_it_overrode(self):
        # row 4: the one way through, written in the receipt; the narrow hatches do not open it
        self.light([ROW1])
        self.change_code()
        self.blocked("--stale-panel-ok", "--reason", "not this one")
        self.blocked("--owner-ok", "--reason", "not this one either")
        r, out = self.close("--force", "--reason", "the owner accepts it")
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(self.done())
        receipt = self.task_md.read_text(encoding="utf-8").split("## Verification Receipt", 1)[1]
        self.assertIn("the owner accepts it", receipt)
        self.assertRegex(receipt, r"(?i)test contract.*row 1.*no record")


class Race(Closing):
    def test_a_receipt_changed_during_the_close_refuses(self):
        # row 5: a `tasks red` writing a record while the close ran — refused at the commit, nothing written
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        from tasks import core, lifecycle
        real = core.format_verify_receipt                # after the second check, before the commit (task 286)
        p = self.task_dir / "red-receipt.jsonl"

        def meanwhile(*a, **k):
            line = p.read_text(encoding="utf-8").splitlines()[-1]
            os.chmod(p, 0o644)
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            return real(*a, **k)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(core, "format_verify_receipt", meanwhile), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = _in_process(self.d, self.env(), lifecycle.cmd_work, ["done"])
        self.assertEqual(code, 1, out.getvalue() + err.getvalue())
        self.assertIn("changed while this close ran", out.getvalue() + err.getvalue())
        self.assertFalse(self.done())


class Everything(dict):
    """Every scope is configured (a stub view's `scopes`)."""

    def __contains__(self, key):
        return True


class Minors(Closing):
    """Task 285's impl round 2 (rows 8-12)."""

    def _state(self, ref, recs, rows, view):
        from tasks import red
        st = red.compute_state(rows, recs, {}, view, None, 0, ref)
        return "\n".join(red.render_state(st, "001", "head"))

    def _rec(self, red, rows, n, keys, base="c9", now=None):
        return {"row": str(n), "run": f"r{n}", "verdict": "proved", "row_sha": "x", "row_key": red.row_key(rows[n - 1]),
                "contract": keys, "command": f"true {n}", "base": {"code": {"commit": base, "used": "commit"}},
                "now_tree": {"code": "tn"} if now is None else now}

    def test_a_lost_start_drops_the_remedies(self):
        # row 8 (opus): the note said "asks again" while the refresh line said "cannot be refreshed"
        from tasks import red
        rows = _rows(red, ["1"])
        rec = self._rec(red, rows, 1, [red.row_key(rows[0])])
        view = View({"t0": "s", "c9": "b"}, last_none="frozen")
        view.scopes = Everything()
        out = self._state({"code": {"commit": "c0", "tree": "t0", "clean": True},
                           "lib": {"commit": "c1", "tree": None, "clean": False}}, [rec], rows, view)
        self.assertIn("cannot be refreshed", out)
        self.assertNotIn("asks again", out)
        self.assertNotIn("a re-run of the row", out)

    def test_a_lost_start_says_why_for_late_rows(self):
        # row 9 (sonnet): a late row got no line at all
        from tasks import red
        rows = _rows(red, ["1", "2"])
        rec = self._rec(red, rows, 1, [red.row_key(rows[0])])
        view = View({"t0": "s", "c9": "s", "tn": "n"})
        view.scopes = Everything()
        out = self._state({"code": {"commit": "c0", "tree": "t0", "clean": True},
                           "lib": {"commit": "c1", "tree": None, "clean": False}}, [rec], rows, view)
        self.assertIn("row 2: no record", out)
        self.assertRegex(out, r"added later: rows? 2 cannot be refreshed — lib's activation tree was not captured")

    def test_a_removed_scope_is_not_lost(self):
        # row 10 (sonnet): a code root no longer configured blocked every refresh with its old half capture
        from tasks import red
        rows = _rows(red, ["1"])
        view = View({"t0": "s"})
        view.scopes = {"code": None}
        out = self._state({"code": {"commit": "c0", "tree": "t0", "clean": True},
                           "gone": {"commit": "c1", "tree": None, "clean": False}}, [], rows, view)
        self.assertIn("refresh: tasks red 001 --rows 1", out)
        self.assertNotIn("cannot be refreshed", out)

    def test_a_nul_is_not_known(self):
        # row 11 (agy): `subprocess.run` raises ValueError on a NUL in an argument
        from tasks import red
        self.light()
        try:
            got = red._tree_state(self.code, "ab\x00cd")
        except ValueError:
            got = "a crash"
        self.assertIsNone(got)

    def test_dash_values_never_reach_git(self):
        # row 12 (opus, sonnet): a record's or a flag's value starting with `-` read as an option
        from tasks import red
        self.light()
        head = self.head(self.code)
        calls = []
        real = red._git

        def spy(repo, args, **k):
            calls.append(list(args))
            return real(repo, args, **k)
        view = red._Trees({"code": self.code}, [":(exclude).agent"], {})
        with mock.patch.object(red, "_git", spy):
            self.assertIsNone(red._commit_of(self.code, "-v"))
            self.assertIsNone(view.entries("code", "--all"))
            self.assertIsNone(view.newer("code", "-x", head))
            self.assertIsNone(view.older("code", head, "--output=x"))
        self.assertEqual(calls, [])


class RoundOne(Closing):
    """Task 286's impl round 1 (rows 17-23)."""

    def nochange(self, text_line="No behavior changes: the docs only."):
        text = self.task_md.read_text(encoding="utf-8")
        head, rest = text.split("## Test contract", 1)
        tail = rest.split("## Pre-review", 1)[1]
        self.task_md.write_text(head + "## Test contract\n<!-- pin -->\n\n" + text_line + "\n\n## Pre-review" + tail,
                                encoding="utf-8")

    def test_a_late_partly_proved_row_needs_its_own_base(self):
        # row 17 (opus, GRAVE): a late row of two tests — one proved, one already true — was never judged for its base
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        self.set_contract([ROW1, ("2", "edge", "two tests", "`tests.test_new.N.test_two` `tests.test_old`")])
        self.assertEqual(self.cli("red", "1", "--rows", "2").returncode, 0)       # on the start: no base of its own
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "partly proved")
        out = self.blocked()
        self.assertRegex(out, r"the test contract: row 2: partly proved")

    def test_a_damaged_repository_blocks(self):
        # row 18 (opus, GRAVE): git says "not a git repository" for a broken one too — that is git failing, not no git
        import shutil
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        shutil.move(str(self.d / ".git"), str(self.d / ".git-real"))

        def restore():
            g = self.d / ".git"
            if g.is_dir() and not g.is_symlink():
                shutil.rmtree(g)
            elif g.exists() or g.is_symlink():
                g.unlink()
            shutil.move(str(self.d / ".git-real"), str(g))
        self.addCleanup(restore)
        (self.d / ".git").write_text("gitdir: /nowhere/at/all\n", encoding="utf-8")   # a moved worktree's link
        r, out = self.close()
        self.assertEqual(r.returncode, 1, out)
        self.assertNotIn("not judged: not a git repository", out)
        self.assertIn("the test contract", out)
        (self.d / ".git").unlink()
        (self.d / ".git").mkdir()                                                    # a .git git cannot read
        r, out = self.close()
        self.assertEqual(r.returncode, 1, out)
        self.assertNotIn("not judged: not a git repository", out)
        from tasks import red
        env = dict(self.env(), LC_ALL="fr_FR.UTF-8", LANG="fr_FR.UTF-8")             # any locale
        got = _in_process(self.d, env, red._not_a_repository, self.d)
        self.assertFalse(got)
        (self.d / "sub").mkdir()                                                    # a folder inside a repository
        self.assertFalse(red._not_a_repository(self.d / "sub"))

    def test_a_removed_red_row_still_blocks(self):
        # row 19 (opus, GRAVE): deleting a broken row's line from the contract made it pass, unsaid
        self.light([ROW1, ("2", "normal", "stays red", "`false 2`")])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        self.assertEqual([r["verdict"] for r in self.receipt_lines()], ["proved", "broken"])
        self.set_contract([ROW1])
        self.assertRegex(self.blocked(), r"row 2 .*removed from the contract.*broken")
        self.set_contract([("3", "normal", "f returns 2", "`tests.test_new`"), ("2", "normal", "stays red", "`false 2`")])
        r, out = self.close()                                       # row 1 renumbered to 3: not removed
        self.assertNotIn("row 1 was removed", out)
        self.assertIn("row 2: broken (red now)", out)
        self.assertNotIn("row 2 was removed", out)                  # a row in the table is not also "removed"
        self.set_contract([ROW1, ("2", "normal", "stays red, said again", "`false 2`")])
        r, out = self.close()                                       # row 2 edited, its number kept: not removed
        self.assertNotIn("row 2 was removed", out)
        self.assertIn("row 2: stale", out)

    def test_no_behavior_changes_as_a_bullet(self):
        # row 20 (agy, sonnet): a list marker was not read; a fenced example was
        self.light([ROW1])
        self.nochange("- No behavior changes: the docs only.")
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(self.done())

    def test_a_fenced_no_behavior_changes_is_no_reason(self):
        # row 20: an example inside a fence is no reason
        self.light([ROW1])
        self.nochange("```\nNo behavior changes: an example\n```")
        self.assertIn("no row", self.blocked())

    def test_no_behavior_changes_reads_every_scope(self):
        # row 21 (agy, opus, sonnet): a recorded scope no longer configured was skipped — "no code changed", unread
        self.light([ROW1])
        self.nochange()
        self.config(code_roots=[])
        self.assertIn("no longer configured", self.blocked())
        # the working-state trees are kept as each is built, a failed tests digest or not (agy)
        from tasks import red
        from tasks.core import load_config
        self.config()
        cfg = load_config(self.d)
        view = red._Trees(red._scopes(self.d, cfg), [":(exclude).agent"], cfg)
        with mock.patch.object(red, "_digest_trees", side_effect=RuntimeError("no digest")):
            tests, why = red._today(view.scopes, cfg, view.exclude, view)
        self.assertIsNone(tests)
        self.assertEqual(sorted(view.today_trees), ["", "code"])

    def test_an_added_code_root_counts_as_changed(self):
        # row 21: a code root added since the start holds code no start had
        self.config(code_roots=[])
        self.light([ROW1])
        self.nochange()
        self.config()
        self.assertIn("src.py", self.blocked())

    def test_a_contract_or_config_changed_during_the_close_refuses(self):
        # row 22 (opus): the commit's digest covers the contract and the config, not only the receipt
        from tasks import core, lifecycle
        real = core.format_verify_receipt                # after the second check, before the commit
        for change in ("contract", "config"):
            self.light([ROW1]) if change == "contract" else None
            if change == "contract":
                self.change_code()
                self.assertEqual(self.cli("red", "1").returncode, 0)

            def meanwhile(*a, c=change, **k):
                if c == "contract":
                    self.set_contract([ROW1, ("2", "normal", "a new row", "`tests.test_old`")])
                else:
                    self.config(red_timeout=301)
                return real(*a, **k)
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(core, "format_verify_receipt", meanwhile), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = _in_process(self.d, self.env(), lifecycle.cmd_work, ["done"])
            self.assertEqual(code, 1, change + out.getvalue() + err.getvalue())
            self.assertIn("changed while this close ran", out.getvalue() + err.getvalue(), change)
            self.assertFalse(self.done())
            if change == "contract":
                self.set_contract([ROW1])

    def test_the_block_names_the_way_through(self):
        # row 23 (opus, sonnet): "Run the `tasks red` named above" where none is named
        self.light([ROW1])
        self.change_code()
        self.assertRegex(self.blocked(), r"run the `tasks red` the check names above")
        self.assertEqual(self.cli("red", "1").returncode, 0)
        self.rewrite(lambda rec: rec.update(verdict="broken"))
        self.assertRegex(self.blocked(), r"make its test run and pass")
        self.set_contract([ROW1, ("1", "normal", "twice", "`tests.test_old`")])
        self.assertRegex(self.blocked(), r"fix `## Test contract`")


class RoundTwo(Closing):
    """Task 286's impl round 2 (rows 24-30)."""

    def in_process_close(self, meanwhile_fn):
        from tasks import core, lifecycle
        real = core.has_review_evidence

        def meanwhile(*a, **k):
            meanwhile_fn()
            return real(*a, **k)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(core, "has_review_evidence", meanwhile), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = _in_process(self.d, self.env(), lifecycle.cmd_work, ["done"])
        return code, out.getvalue() + err.getvalue()

    def test_a_deleted_row_cannot_hide_behind_a_renumber(self):
        # row 24 (opus, GRAVE): rows 1 proved, 2 broken, 3 green; delete 2, renumber 3 to 2 — the close passed
        self.light([ROW1, ("2", "normal", "stays red", "`false 2`"), ("3", "must-not", "the old test", "`tests.test_old`")])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        self.set_contract([ROW1, ("2", "must-not", "the old test", "`tests.test_old`")])
        self.assertRegex(self.blocked(), r"row 2 was removed from the contract.*broken")

    def test_a_superseded_record_is_not_removed(self):
        # row 24 (codex, agy): a row edited and re-run green, then renumbered — its older red version is not read
        self.light([ROW1, ("2", "normal", "stays red", "`false 2`")])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        self.set_contract([ROW1, ("2", "must-not", "now green", "`tests.test_old`")])
        self.assertEqual(self.cli("red", "1", "--rows", "2").returncode, 0)
        self.set_contract([ROW1, ("3", "must-not", "now green", "`tests.test_old`")])
        r, out = self.close()
        self.assertNotIn("was removed from the contract", out)

    def test_a_removed_late_row_keeps_its_base_rule(self):
        # row 25 (codex, grok, GRAVE): a late row proved only on the start, deleted, passed on its raw `proved`
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        self.set_contract([ROW1, LATE])
        self.assertEqual(self.cli("red", "1", "--rows", "2").returncode, 0)
        self.assertRegex(self.blocked(), r"row 2: proved on the task's start only")
        self.set_contract([ROW1])
        self.assertRegex(self.blocked(), r"row 2 was removed from the contract.*proved on the task's start only")

    def test_a_contract_written_during_the_close_is_judged(self):
        # row 26 (codex, grok, opus, GRAVE): no contract at the check, one written while the close ran — never judged
        from tests.test_red_receipt import _contract
        self.light()

        def write_contract():
            text = self.task_md.read_text(encoding="utf-8")
            self.task_md.write_text(text.replace("## Pre-review\n", _contract([ROW1]) + "## Pre-review\n", 1),
                                    encoding="utf-8")
        code, out = self.in_process_close(write_contract)
        self.assertEqual(code, 1, out)
        self.assertFalse(self.done())
        from tasks import red                           # base.json is in the digest
        before = red.close_digest(self.d, self.task_md)
        b = self.task_dir / "base.json"
        b.write_text(b.read_text(encoding="utf-8").replace('"v": 1', '"v": 1, "x": 1'), encoding="utf-8")
        self.assertNotEqual(red.close_digest(self.d, self.task_md), before)
        before = red.close_digest(self.d, self.task_md)               # the gate line too
        from tasks.template import CONTRACT_GATE
        text = self.task_md.read_text(encoding="utf-8")
        self.task_md.write_text(text.replace("## Work\n", f"## Work\n- [x] {CONTRACT_GATE}\n", 1),
                                encoding="utf-8")                  # outside the contract section
        self.assertNotEqual(red.close_digest(self.d, self.task_md), before)

    def test_code_written_by_verify_blocks_no_behavior_changes(self):
        # row 27 (codex, GRAVE): `No behavior changes:` judged before verify — a verify that writes code closed
        self.light([ROW1])
        text = self.task_md.read_text(encoding="utf-8")
        head, rest = text.split("## Test contract", 1)
        self.task_md.write_text(head + "## Test contract\n<!-- pin -->\n\nNo behavior changes: the docs only.\n\n"
                                "## Pre-review" + rest.split("## Pre-review", 1)[1], encoding="utf-8")
        self.config(verify="python3 -c \"open('code/gen.py', 'w').write('X = 1')\"")
        out = self.blocked(after_verify=True)
        self.assertIn("gen.py", out)

    def test_every_block_has_its_way_through(self):
        # row 28 (agy, codex, grok, opus): a late partly-proved row's refresh named; the way through for each cause
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        self.set_contract([ROW1, ("2", "edge", "two tests", "`tests.test_new.N.test_two` `tests.test_old`")])
        self.assertEqual(self.cli("red", "1", "--rows", "2").returncode, 0)
        self.assertRegex(self.blocked(), r"added later, each needs its own base: tasks red 001 --rows 2")
        from tasks import red
        self.assertIn("section back", red.close_way(["no live `## Test contract` section in task.md"]))
        self.assertIn("write the contract's rows",
                      red.close_way(["`No behavior changes:` while code changed since the start (code/src.py)"]))

    def test_a_truncated_record_blocks(self):
        # row 29 (opus): a crash mid-append left half a record; the older `proved` stood in for it, unsaid
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        p = self.task_dir / "red-receipt.jsonl"
        os.chmod(p, 0o644)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(p.read_text(encoding="utf-8").splitlines()[-1][:40] + "\n")
        self.assertIn("cannot read", self.blocked())

    def test_an_indented_gate_is_a_gate(self):
        # row 30 (sonnet): core's own reader counts an indented gate; the check did not
        self.light(gate=True)
        (self.task_dir / "base.json").unlink()                      # no recorded contract to fall back on
        from tasks.template import CONTRACT_GATE
        text = self.task_md.read_text(encoding="utf-8")
        self.task_md.write_text(text.replace(f"- [x] {CONTRACT_GATE}", f"  - [x] {CONTRACT_GATE}", 1), encoding="utf-8")
        self.assertIn("no live `## Test contract` section", self.blocked())


class PostPanel(Closing):
    """Task 286's post-panel judge, run 1 (rows 31-32)."""

    in_process_close = RoundTwo.in_process_close

    def test_a_config_changed_after_verify_refuses(self):
        # row 31 (GRAVE): verify ran on the old config; the second check took the new one's digest — closed
        self.light([ROW1])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        code, out = self.in_process_close(lambda: self.config(verify="false"))
        self.assertEqual(code, 1, out)
        self.assertIn("the config changed while this close ran", out)
        self.assertFalse(self.done())

    def test_a_superseded_key_is_not_removed(self):
        # row 32: row 2 red, renumbered to 3, re-run green, then deleted — its old red record was read as removed
        self.light([ROW1, ("2", "normal", "red, then green", "`tests.test_new.N.test_two`")])
        self.assertEqual(self.cli("red", "1").returncode, 0)          # the code not changed yet: row 2 red now
        self.change_code()
        self.set_contract([ROW1, ("3", "normal", "red, then green", "`tests.test_new.N.test_two`")])
        self.assertEqual(self.cli("red", "1", "--rows", "1,3").returncode, 0)
        self.set_contract([ROW1])
        r, out = self.close()
        self.assertNotIn("was removed from the contract", out)

    def test_a_renumbered_row_rerun_cannot_hide_a_deleted_one(self):
        # row 33 (run 2, GRAVE): delete broken row 2, renumber green row 3 to 2, re-run row 2 — the red record vanished
        self.light([ROW1, ("2", "normal", "stays red", "`false 2`"), ("3", "must-not", "the old test", "`tests.test_old`")])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        self.set_contract([ROW1, ("2", "must-not", "the old test", "`tests.test_old`")])
        self.assertEqual(self.cli("red", "1", "--rows", "2").returncode, 0)
        self.assertRegex(self.blocked(), r"row 2 was removed from the contract.*broken")


class Text(unittest.TestCase):
    def test_the_docs_say_the_close_blocks(self):
        # row 13
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        for word in ("certainly wrong", "cannot be judged", "`--force --reason`", "not a git repository",
                     "No behavior changes:"):
            self.assertIn(word, body, word)


if __name__ == "__main__":
    unittest.main()
