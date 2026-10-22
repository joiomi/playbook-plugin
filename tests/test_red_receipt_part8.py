#!/usr/bin/env python3
"""The red receipt, part 8 (PHASE-B.md item 9b; task 287's contract): a row leaves the contract only by `--drop`.

The owner, 2026-10-22 12:07 (M1444): "Ștergere explicită" — a recorded row (its key: kind, behavior, test) missing from
the contract without a recorded removal blocks the close; 12:19 (M1448): "Rând nou + drop la vechi" — an edit after a
run is a new row, its old version leaves by `tasks red --drop <row> --reason "<why>"`. Task 286's inference is gone.

Each test names the row of task 287's contract it proves. Real temporary git projects from part 1's `Project`, the CLI
driven as `python3 -m tasks.cli`. Pure stdlib unittest. Run: python3 tests/test_red_receipt_part8.py
"""
import hashlib
import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from tests.test_red_receipt_part7 import ROW1, Closing  # noqa: E402

RED2 = ("2", "normal", "stays red", "`false 2`")
GREEN2 = ("2", "must-not", "the old test still holds", "`tests.test_old`")


class Part8(Closing):
    def files(self):
        out = {}
        for f in (self.task_md, self.task_dir / "red-receipt.jsonl"):
            out[f.name] = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
        return out

    def run_all(self):
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)

    def no_change(self):
        """The contract replaced by `No behavior changes:` (task 286, M1439)."""
        text = self.task_md.read_text(encoding="utf-8")
        head, rest = text.split("## Test contract", 1)
        tail = rest.split("## Pre-review", 1)[1]
        self.task_md.write_text(head + "## Test contract\n<!-- pin -->\n\nNo behavior changes: the docs only.\n\n"
                                "## Pre-review" + tail, encoding="utf-8")

    def lose_the_start(self):
        b = self.task_dir / "base.json"
        doc = json.loads(b.read_text(encoding="utf-8"))
        doc["scopes"]["code"].update({"tree": None, "clean": False})
        b.write_text(json.dumps(doc), encoding="utf-8")


class Missing(Part8):
    def test_a_missing_row_blocks_with_the_drop_command(self):
        # row 1 (M1444): a recorded row gone from the contract — red or green, it leaves only by `--drop`
        for gone in (RED2, GREEN2):
            with self.subTest(gone=gone[2]):
                self.setUp()
                self.light([ROW1, gone])
                self.run_all()
                self.set_contract([ROW1])
                out = self.blocked()
                self.assertRegex(out, r"row 2 \(" + gone[1] + r", " + re.escape(gone[3])
                                 + r"\) is recorded but no longer in the contract — 2 is its number at its last run")
                self.assertIn('tasks red 001 --drop 2 --reason "', out)

    def test_an_edit_needs_the_old_version_dropped(self):
        # row 4 (M1448): an edit after a run is a new row; the old version blocks until dropped, the new one must run
        self.light([ROW1])
        self.run_all()
        self.set_contract([("1", "normal", "f returns 2, said again", "`tests.test_new`")])
        out = self.blocked()
        self.assertRegex(out, r"row 1 \(normal, `tests\.test_new`\) is recorded but no longer in the contract")
        self.assertRegex(out, r"row 1: stale")
        r = self.cli("red", "1", "--drop", "1", "--reason", "its text was edited")    # row 1 stands: still dropped
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(self.blocked(), r"row 1: no record")
        self.assertEqual(self.cli("red", "1").returncode, 0)
        out = self.blocked()                            # the old version gone; the new row is judged as one added later
        self.assertNotIn("no longer in the contract", out)
        self.assertRegex(out, r"row 1: proved on the task's start only \(run [0-9a-f]+\) — added later")

    def test_the_inferences_holes_all_block(self):
        # row 5: each way task 286's judges found past the inference — by key, nothing to guess
        three = [ROW1, RED2, ("3", "must-not", "the old test", "`tests.test_old`")]
        renumbered = [ROW1, ("2", "must-not", "the old test", "`tests.test_old`")]
        for steps in ("renumber", "renumber, re-run", "partial run, renumber, run"):
            with self.subTest(steps=steps):
                self.setUp()
                self.light(three)
                self.change_code()
                rows = "1,2" if steps.startswith("partial") else "1-3"
                self.assertEqual(self.cli("red", "1", "--rows", rows).returncode, 0)
                self.set_contract(renumbered)
                if "run" in steps.split(", ")[-1] and steps != "renumber":
                    self.assertEqual(self.cli("red", "1", "--rows", "2").returncode, 0)
                self.assertRegex(self.blocked(), r"row 2 \(normal, `false 2`\) is recorded but no longer in the contract")
        self.setUp()                                    # an edit back and forth: the earlier text is a gone row
        self.light([ROW1, ("2", "normal", "A", "`false 2`")])
        self.change_code()
        self.assertEqual(self.cli("red", "1").returncode, 0)
        for row in (("2", "must-not", "B", "`tests.test_old`"), ("2", "normal", "A", "`false 2`"),
                    ("2", "must-not", "B", "`tests.test_old`")):
            self.set_contract([ROW1, row])
            self.assertEqual(self.cli("red", "1", "--rows", "2").returncode, 0)
        self.assertRegex(self.blocked(), r"row 2 \(normal, `false 2`\) is recorded but no longer in the contract")

    def test_a_renumber_needs_no_drop(self):
        # row 7 (must-not): a renumber keeps the key
        self.light([ROW1, GREEN2])
        self.run_all()
        self.set_contract([ROW1, ("3",) + GREEN2[1:]])
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("no longer in the contract", out)

    def test_a_row_never_run_is_not_checked(self):
        # row 9 (must-not; P9): no record, nothing recorded to lose — the anchor run records every design row
        self.light([ROW1, RED2])
        self.change_code()
        self.assertEqual(self.cli("red", "1", "--rows", "1").returncode, 0)
        self.set_contract([ROW1])
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("no longer in the contract", out)

    def test_the_way_through_is_the_drop(self):
        # row 12 (P7, grok): the drop is the way, not "put the row back"; a lost start does not call --force the only one
        from tasks import red
        way = red.close_way(['row 2 (normal, `false 2`) is recorded but no longer in the contract — 2 is its number '
                             'at its last run; `tasks red 001 --drop 2 --reason "<why>"` records its removal'])
        self.assertIn("--drop", way)
        self.assertNotIn("(or the row) back", way)
        self.light([ROW1, GREEN2])
        self.run_all()
        self.set_contract([ROW1])
        self.lose_the_start()
        out = self.blocked()
        self.assertIn('tasks red 001 --drop 2 --reason "', out)
        self.assertNotIn("`--force --reason` is the only way through", out)
        self.assertEqual(self.cli("red", "1", "--drop", "2", "--reason", "gone").returncode, 0)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)


class Drop(Part8):
    def test_drop_records_the_removal_and_the_close_passes(self):
        # row 2: the removal written with its reason, as is; its output, the section and `--check` name it; it passes
        self.light([ROW1, RED2])
        self.run_all()
        ran = self.receipt_lines()[-1]
        self.set_contract([ROW1])
        r = self.cli("red", "1", "--drop", "2", "--reason", "rândul nu mai e nevoie: comportamentul a ieșit")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("row 2 (normal, `false 2`)", r.stdout)
        rec = self.receipt_lines()[-1]
        self.assertEqual((rec["verdict"], rec["row"], rec["reason"], rec["kind"], rec["test"]),
                         ("dropped", "2", "rândul nu mai e nevoie: comportamentul a ieșit", "normal", "`false 2`"))
        self.assertEqual((rec["row_key"], rec["row_sha"]), (ran["row_key"], ran["row_sha"]))
        self.assertEqual(rec["contract"], [ran["contract"][0]])
        check = self.cli("red", "1", "--check").stdout
        self.assertIn("dropped: row 2 (normal, `false 2`)", check)
        self.assertIn("rândul nu mai e nevoie", check)
        self.assertIn("rândul nu mai e nevoie", self.section())
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)

    def test_drop_refuses_and_writes_nothing(self):
        # row 3: each refusal before anything is written
        self.light([ROW1, RED2])
        self.run_all()
        self.set_contract([ROW1])
        before = self.files()
        for args in (["--drop", "2"], ["--drop", "2", "--reason", "  "], ["--drop", "1", "--reason", "x"],
                     ["--drop", "9", "--reason", "x"], ["--drop", "2", "--reason", "x", "--rows", "1"],
                     ["--drop", "2", "--reason", "x", "--base", "code=HEAD"], ["--drop", "2", "--reason", "x", "--check"]):
            with self.subTest(args=args):
                r = self.cli("red", "1", *args)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertRegex(r.stderr, r"Refused|Error: --drop")
                self.assertIn("drop", r.stderr)
                self.assertEqual(self.files(), before)
        r = self.cli("red", "1", "--drop", "02", "--reason", "gone")                # a number: 02 is 2
        self.assertEqual(r.returncode, 0, r.stderr)
        before = self.files()
        r = self.cli("red", "1", "--drop", "2", "--reason", "again")                # a second time: nothing left
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Refused", r.stderr)
        self.assertEqual(self.files(), before)

    def test_a_dropped_row_put_back_must_run_again(self):
        # row 6: its newest record says it left; back in the contract, it has no run
        self.light([ROW1, GREEN2])
        self.run_all()
        self.set_contract([ROW1])
        self.assertEqual(self.cli("red", "1", "--drop", "2", "--reason", "for now").returncode, 0)
        self.set_contract([ROW1, GREEN2])
        self.assertRegex(self.blocked(), r"row 2: no record")
        self.assertEqual(self.cli("red", "1", "--rows", "2").returncode, 0)         # a run after the drop supersedes it
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.assertRegex(out, r"row 2: already true")

    def test_a_drop_is_not_a_run(self):
        # row 10 (P2): a drop as the last line — the newest run, the runs, the count and the rebased note as before it
        self.light([ROW1, RED2])
        self.run_all()
        self.g("checkout", "-q", "--orphan", "other", cwd=self.code)    # the activation's commit off HEAD
        self.g("commit", "-qm", "another history", cwd=self.code)
        self.set_contract([ROW1])

        def seen():
            out = self.cli("red", "1", "--check").stdout.splitlines()
            sec = [ln for ln in self.section().splitlines() if ln.startswith(("Written by", "Runs:"))]
            return [ln for ln in out if "row 2" not in ln and "dropped" not in ln], sec
        self.assertEqual(self.cli("red", "1", "--rows", "1").returncode, 0)     # the section from the contract now
        before = seen()
        self.assertIn("not an ancestor of HEAD", "\n".join(before[0]))
        self.assertNotIn("is not known", "\n".join(before[0]))
        self.assertEqual(self.cli("red", "1", "--drop", "2", "--reason", "gone").returncode, 0)
        self.assertEqual(self.receipt_lines()[-1]["verdict"], "dropped")
        self.assertEqual(seen(), before)

    def test_drop_needs_no_valid_contract(self):
        # row 11 (P4, P5): `No behavior changes:` does not let a recorded row through, and the drop works there
        self.light([ROW1, RED2])
        self.assertEqual(self.cli("red", "1").returncode, 0)                    # recorded, no code changed
        self.no_change()
        out = self.blocked()
        self.assertRegex(out, r"row 2 \(normal, `false 2`\) is recorded but no longer in the contract")
        self.assertRegex(out, r"row 1 \(normal, `tests\.test_new`\) is recorded but no longer in the contract")
        for n in ("1", "2"):
            r = self.cli("red", "1", "--drop", n, "--reason", "nothing to test")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r, out = self.close()
        self.assertEqual(r.returncode, 0, out)
        self.setUp()                                    # a number twice: the contract is refused, the drop is not
        self.light([ROW1, RED2])
        self.run_all()
        self.set_contract([ROW1, ("1", "normal", "twice", "`tests.test_old`")])
        r = self.cli("red", "1", "--drop", "2", "--reason", "gone")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_drop_reads_under_the_lock(self):
        # row 13 (P6): the row put back while `--drop` waits for the task lock — refused, nothing written
        from tasks.filelock import task_lock
        self.light([ROW1, RED2])
        self.run_all()
        self.set_contract([ROW1])
        with task_lock(self.task_md):
            p = subprocess.Popen([sys.executable, "-m", "tasks.cli", "red", "1", "--drop", "2", "--reason", "x"],
                                 cwd=self.d, env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(2)                               # it has read whatever it reads before the lock
            self.set_contract([ROW1, RED2])
            before = self.files()
        out, err = p.communicate(timeout=120)
        self.assertNotEqual(p.returncode, 0, out + err)
        self.assertIn("Refused", err)
        self.assertEqual(self.files(), before)


class Text(unittest.TestCase):
    def test_the_docs_say_drop(self):
        # row 8
        doc = (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8")
        body = "\n".join(p for p in doc.split("\n\n") if p.startswith("**`tasks red"))
        for word in ("--drop", "is recorded but no longer in the contract", "an edit after a run is a new row",
                     "a row never run"):
            self.assertIn(word, body, word)
        self.assertNotIn("a key it never shared a contract with", body)


if __name__ == "__main__":
    unittest.main()
