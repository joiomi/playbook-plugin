#!/usr/bin/env python3
"""`tasks red` rewrites only its own lines of `## Red receipt` (task 303; audit 3's G3, the owner's ruling of
2026-10-24 12:49: "4 grave, G2 nu (Recomandat)").

A second run replaced the whole section, its heading to the next `## `: a note, a `###` subsection, a fenced block
or an open gate the agent wrote under it went with it, with no copy and no message — an open gate there left the
count. Now the CLI ends its lines with an end line of its own, which carries the SHA-256 of its lines above it: every
line below it stays, and the lines above it are replaced only when they are byte for byte the ones it wrote — else
every one of them stays, under the new end line (impl rounds 1 and 2: no line is the CLI's by its shape or by a short
digest). Each test names the row of task 303's contract it proves. Part 1's `Project` (test_red_receipt.py): a
temporary outer repository with a `code_roots` one, a throwaway HOME, the CLI as `python3 -m tasks.cli`.
Pure stdlib unittest. Run: python3 tests/test_red_receipt_keeps_the_agents_lines.py
"""
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from tests.test_red_receipt import Project  # noqa: E402

ROW = ("1", "normal", "one", "`exit 1`")
NOTE = "MY-NOTE: row 1 is already true; a.py predates the task."
NOTE_RO = "Notă: rândul 1 e deja adevărat — ă î ș ț, păstrat octet cu octet."
SUB = "### Why row 1 cannot be red"
SUB_LINE = "MY-NOTE-2: the file is older than the task."
GATE = "- [ ] MY-GATE: replace row 1 with a test that fails first"
TABLE_HEAD = "| # | Kind | Test | On the base | Now | State | On the base, its first error (or last line) | Run |"


class TheAgentsLines(Project):
    def started(self, rows=(ROW,)):
        self.new_task()
        self.set_contract(list(rows))
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def text(self):
        return self.task_md.read_text(encoding="utf-8")

    def span(self, text):
        m = re.search(r"(?ms)^## Red receipt\n.*?(?=^## |\Z)", text)
        if m is None:
            self.fail(text)
        return m

    def add_at_section_end(self, block):
        """The agent's lines where it writes them: under everything in the section, before the next heading."""
        text = self.text()
        m = self.span(text)
        body = m.group(0).rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n\n"
        self.task_md.write_text(text[:m.start()] + body + text[m.end():], encoding="utf-8")

    def add_after(self, prefix, block):
        """The agent's lines right after the section's first line that starts with `prefix`."""
        lines = self.text().split("\n")
        a = lines.index("## Red receipt")
        k = next(i for i in range(a, len(lines)) if lines[i].startswith(prefix))
        lines[k + 1:k + 1] = block.rstrip("\n").split("\n")
        self.task_md.write_text("\n".join(lines), encoding="utf-8")

    def gates(self):
        from tasks.core import _gate_counts
        return _gate_counts(self.text())

    def end_at(self, sec):
        """The index of the CLI's (first) end line in the section's lines."""
        return next(i for i, ln in enumerate(sec) if ln.startswith("<!-- the end of tasks red"))

    def test_the_agents_lines_under_the_table_stay(self):
        # row 1 (the bug): a note, a `###` subsection with its line and an open gate under the table stay after a
        # second run, byte for byte and in order (a non-ASCII line too); the open gates count the same
        self.started()
        block = "\n".join([NOTE, NOTE_RO, "", SUB, SUB_LINE, "", GATE])
        self.add_at_section_end(block)
        before = self.gates()
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn(block, self.section())
        self.assertEqual(self.gates(), before)
        self.assertEqual(self.text().count("\n## Red receipt\n"), 1)

    def test_a_line_between_its_own_lines_moves_under_them(self):
        # row 2: an open gate, a Note line with no full stop and a table row of 2 cells, written by the agent between
        # the CLI's State line and its table, stay — in their order, under the table; the gates count the same
        self.started()
        mine = [GATE, "Note: mine, with no full stop", "| mine | two cells |"]
        self.add_after("State: ", "\n".join(mine))
        before = self.gates()
        self.assertEqual(self.cli("red").returncode, 0)
        sec = self.section().split("\n")
        self.assertIn("\n".join(mine), "\n".join(sec))
        self.assertGreater(sec.index(GATE), self.end_at(sec))
        self.assertEqual(self.gates(), before)

    def test_a_fenced_quote_of_its_own_shapes_stays(self):
        # row 3: a fenced block inside the CLI's lines that quotes its shapes (a State line, a table row, a pin, and
        # an exact copy of its table's header, which comes after it) stays byte for byte — a line in a code fence is
        # never the CLI's, so the copy does not take the header's place
        self.started()
        quote = "\n".join(["```text", "State: proved: 9 (row 9).", "<!-- pin -->", TABLE_HEAD,
                           "| 9 | normal | `x` | red | green | proved |  | `r` |", "```"])
        self.add_after("State: ", quote)
        self.assertEqual(self.cli("red").returncode, 0)
        self.assertIn(quote, self.section())

    def test_its_own_shapes_below_its_lines_stay(self):
        # row 4: lines under the CLI's lines that have its shapes (a Note, a State line, a table row) stay — below
        # its end line nothing is read as its own
        self.started()
        block = "\n".join(["Note: row 1 predates the task.", "State: proved: 1 (row 1).",
                           "| 7 | normal | `mine` | — | — | mine |  | — |"])
        self.add_at_section_end(block)
        self.assertEqual(self.cli("red").returncode, 0)
        self.assertIn(block, self.section())
        self.assertEqual(len([ln for ln in self.section().split("\n") if ln.startswith("State: ")]), 2)

    def test_a_section_without_its_end_line_keeps_every_line(self):
        # row 14 (impl round 1, opus and codex): a section with no end line (as written before this task, or with the
        # end line taken out) tells nothing for the CLI's own — every line it held stays, in order, under the new end
        # line and a line that says why; nothing is removed, the CLI's old lines among them
        self.started()
        text = self.text()
        m = self.span(text)
        old = [ln for ln in m.group(0).split("\n") if not ln.startswith("<!-- the end of tasks red")]
        self.task_md.write_text(text[:m.start()] + "\n".join(old) + text[m.end():], encoding="utf-8")
        self.add_after("State: ", "Note: row 1 predates the task.")
        self.add_at_section_end(GATE)
        held = self.span(self.text()).group(0).strip("\n").split("\n")[1:]
        held = [ln for k, ln in enumerate(held) if ln.strip() or (k and held[k - 1].strip())]   # one blank per run
        before = self.gates()
        self.assertEqual(self.cli("red").returncode, 0)
        sec = self.section().strip("\n").split("\n")
        end = next(i for i, ln in enumerate(sec) if ln.startswith("<!-- the end of tasks red"))
        self.assertEqual(sec[end + 1], "")
        self.assertTrue(sec[end + 2].startswith("Kept by `tasks red`"), sec[end + 1:end + 4])
        self.assertEqual(sec[end + 3:], [""] + held)
        self.assertEqual(self.gates(), before)

    def test_a_third_run_changes_only_its_own_values(self):
        # row 6 (repeat): a third run's section is the second's but for the run's own values (its id, its time,
        # each side's seconds) — one end line, no blank line added, the agent's lines still there
        self.started()
        self.add_at_section_end("\n".join([NOTE, "", GATE]))
        self.assertEqual(self.cli("red").returncode, 0)
        second = self.section()
        self.assertEqual(self.cli("red").returncode, 0)
        third = self.section()

        def values_out(sec):
            return [("<values>" if ln.startswith(("Written by `tasks red`", "Runs: ", "| 1 |",
                                                  "<!-- the end of tasks red")) else ln)
                    for ln in sec.split("\n")]
        self.assertEqual(values_out(third), values_out(second))
        self.assertIn(NOTE, third.split("\n"))
        self.assertIn(GATE, third.split("\n"))
        self.assertEqual(len([ln for ln in third.split("\n") if re.fullmatch(r"<!-- .* -->", ln)
                              and ln != "<!-- pin -->"]), 1)

    def test_a_drop_keeps_the_agents_lines(self):
        # row 7: `tasks red --drop` writes the section the same way — the agent's lines stay
        self.started(rows=(ROW, ("2", "normal", "two", "`exit 2`")))
        self.set_contract([ROW])
        block = "\n".join([NOTE, "", GATE])
        self.add_at_section_end(block)
        r = self.cli("red", "--drop", "2", "--reason", "row 2 is gone")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("Dropped: row 2", self.section())
        self.assertIn(block, self.section())

    def test_an_unclosed_fence_in_the_last_section_keeps_what_follows(self):
        # row 9: the section is the file's last and the agent opened a fence in the CLI's lines that it never
        # closed — the opener and every line after it stay (fail closed: they read as fenced, never as the CLI's)
        self.started()
        text = self.text()
        m = self.span(text)
        self.task_md.write_text(text[:m.start()] + text[m.end():].rstrip("\n") + "\n\n" + m.group(0).rstrip("\n")
                                + "\n", encoding="utf-8")
        quote = ["```text", "my unclosed quote", "", "", "State: quoted, not its own."]
        self.add_after("State: ", "\n".join(quote))
        old = self.span(self.text()).group(0).split("\n")
        old_first = next(ln for ln in old if ln.startswith("Written by"))
        old_end = next(ln for ln in old if ln.startswith("<!-- the end of tasks red"))
        r = self.cli("red")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        sec = self.section().split("\n")
        k = sec.index("```text")
        self.assertEqual(sec[k:k + 5], quote)                  # inside the fence its blank lines are its text
        self.assertEqual(sec[0], "<!-- pin -->")
        self.assertLess(next(i for i, ln in enumerate(sec) if ln.startswith("State: ")), k)
        # its end line is inside the fence, so no line is told for the CLI's: its old lines stay, before the opener
        # and after it, the fenced end line among them
        self.assertIn(old_first, sec[:k])
        self.assertIn(TABLE_HEAD, sec[k:])
        self.assertIn(old_end, sec[k:])

    def test_its_own_shapes_between_its_lines_stay(self):
        # row 12 (impl round 1, codex and opus): the agent's complete sentences of the CLI's shapes, between its
        # State line and its table — a Note, a State line, an 8-cell row, a copy of the CLI's State line word for
        # word (each listed digest goes once) — stay, in order, under its table
        self.started()
        state = next(ln for ln in self.text().split("\n") if ln.startswith("State: "))
        mine = ["Note: row 1 predates the task.", "State: checked by hand.",
                "| 7 | normal | `mine` | — | — | mine |  | — |", state]
        self.add_after("State: ", "\n".join(mine))
        self.assertEqual(self.cli("red").returncode, 0)
        sec = self.section().split("\n")
        self.assertIn("\n".join(mine), "\n".join(sec))
        self.assertGreater(sec.index(mine[0]), self.end_at(sec))

    def test_a_line_of_its_own_the_agent_edited_stays(self):
        # row 13 (impl round 1): a line the CLI wrote and the agent then edited is the agent's — it stays as edited,
        # under the table, beside the CLI's new State line
        self.started()
        lines = self.text().split("\n")
        k = next(i for i, ln in enumerate(lines) if ln.startswith("State: "))
        edited = lines[k] + " Checked by hand: row 1 needs a test that fails first."
        lines[k] = edited
        self.task_md.write_text("\n".join(lines), encoding="utf-8")
        self.assertEqual(self.cli("red").returncode, 0)
        sec = self.section().split("\n")
        self.assertIn(edited, sec)
        self.assertGreater(sec.index(edited), self.end_at(sec))
        self.assertEqual(len([ln for ln in sec if ln.startswith("State: ")]), 2)

    def test_trailing_white_space_on_its_lines_keeps_them_its_own(self):
        # row 15 (impl round 1, opus and sonnet Minor): an editor's trailing white space on the CLI's lines and on
        # its end line does not make them the agent's — after a run, one pin, one State line, one end line
        self.started()
        text = self.text()
        m = self.span(text)
        padded = [ln + "  " if ln.strip() and not ln.startswith("## ") else ln for ln in m.group(0).split("\n")]
        self.task_md.write_text(text[:m.start()] + "\n".join(padded) + text[m.end():], encoding="utf-8")
        self.assertEqual(self.cli("red").returncode, 0)
        sec = [ln.rstrip() for ln in self.section().split("\n")]
        self.assertEqual(sec.count("<!-- pin -->"), 1)
        self.assertEqual(len([ln for ln in sec if ln.startswith("State: ")]), 1)
        self.assertEqual(len([ln for ln in sec if ln.startswith("<!-- the end of tasks red")]), 1)


    def test_an_edited_line_and_the_agents_copy_of_it_both_stay(self):
        # row 16 (impl round 2, codex I2): the agent edits the CLI's State line, then copies its old text into a
        # note of its own above the end line — after a run both stay, under the new end line; the gates count the same
        self.started()
        lines = self.text().split("\n")
        k = next(i for i, ln in enumerate(lines) if ln.startswith("State: "))
        original = lines[k]
        lines[k] = original + " Edited by hand."
        self.task_md.write_text("\n".join(lines), encoding="utf-8")
        self.add_after("| 1 |", "\n".join(["", "### My copy of the State line", original, GATE]))
        before = self.gates()
        self.assertEqual(self.cli("red").returncode, 0)
        sec = self.section().split("\n")
        below = sec[self.end_at(sec) + 1:]
        self.assertIn(original + " Edited by hand.", below)
        self.assertIn("\n".join(["### My copy of the State line", original, GATE]), "\n".join(below))
        self.assertEqual(self.gates(), before)

    def test_an_agents_line_whose_short_digest_is_a_line_of_its_own_stays(self):
        # row 17 (impl round 2, codex I1): `- [ ] Review case 155123` and the CLI's line `Dropped: row 2 (normal,
        # 'exit 2') — replaced case 4486.` share the first 8 hex digits of their SHA-256; the agent's gate, written
        # right before that line, stays after a run, and the gates count the same
        self.started(rows=(ROW, ("2", "normal", "two", "`exit 2`")))
        self.set_contract([ROW])
        r = self.cli("red", "--drop", "2", "--reason", "replaced case 4486")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        dropped = "Dropped: row 2 (normal, 'exit 2') — replaced case 4486."
        lines = self.text().split("\n")
        k = lines.index(dropped)
        gate = "- [ ] Review case 155123"
        lines[k:k] = [gate]
        self.task_md.write_text("\n".join(lines), encoding="utf-8")
        before = self.gates()
        self.assertEqual(self.cli("red").returncode, 0)
        self.assertIn(gate, self.section().split("\n"))
        self.assertEqual(self.gates(), before)

    def test_an_end_line_the_agent_changed_keeps_every_line(self):
        # row 18 (impl round 2, sonnet Minor): an end line the agent changed (words put in before its digest) is no
        # end line the CLI can read — every line the section held stays, the changed end line among them, under the
        # line that says why
        self.started()
        lines = self.text().split("\n")
        k = next(i for i, ln in enumerate(lines) if ln.startswith("<!-- the end of tasks red"))
        changed = lines[k].replace(" — ", " — (mine) — ", 1)
        self.assertNotEqual(changed, lines[k])
        lines[k] = changed
        self.task_md.write_text("\n".join(lines), encoding="utf-8")
        self.assertEqual(self.cli("red").returncode, 0)
        sec = self.section().split("\n")
        below = sec[self.end_at(sec) + 1:]
        self.assertTrue(any(ln.startswith("Kept by `tasks red`") for ln in below), below[:3])
        self.assertIn(changed, below)


class TheShapes(unittest.TestCase):
    def test_every_line_it_writes_is_its_own(self):
        # row 8: every line `_section` renders, for a state with every part (each table row shape too), reads as
        # the CLI's own — its end line's digest is theirs (impl rounds 1 and 2), or they would stay as the agent's
        # and grow on each run; its last line is its end line
        from tasks import red
        rec = {"run": "r1", "status": "red", "exit": 1, "seconds": 0.1, "now": {"status": "green", "exit": 0,
                                                                                "seconds": 0.1}, "last": "x | y"}
        tests2 = [{"base": {"status": "red"}, "now": {"status": "green"}, "verdict": "proved"},
                  {"base": {"status": "green", "last": "ok"}, "now": {"status": "green"}, "verdict": "already true"}]
        rows = [
            {"row": "1", "kind": "normal", "test": "`a | b`", "state": "broken", "verdict": "broken", "rec": rec,
             "late": True, "noreader": True, "moved": True, "why": ""},
            {"row": "2", "kind": "edge", "test": "`t.x`", "state": "no record", "verdict": "", "rec": None,
             "late": True, "why": ""},
            {"row": "3", "kind": "hostile", "test": "`t.y`", "state": "stale", "verdict": "proved", "why": "its text",
             "rec": {"run": "r2", "status": "unrunnable", "why": "no runner"}, "late": False},
            {"row": "4", "kind": "must-not", "test": "`t.z`, `t.w`", "state": "partly proved", "verdict": "x",
             "rec": {"run": "r3", "tests": tests2}, "late": False, "why": ""},
            {"row": "5", "kind": "hostile", "test": "`git x`", "state": "refused", "verdict": "refused",
             "rec": {"run": "r4", "why": "the command guard", "tests": [{"base": {}, "now": {}, "verdict": "refused"}]},
             "late": False, "why": ""},
            {"row": "6", "kind": "normal", "test": "`ok`", "state": "proved", "verdict": "proved", "rec": rec,
             "late": False, "why": ""},
        ]
        for anchor_after in (False, True):
            st = {"rows": rows, "newest": {"run": "r4", "at": "2026-10-24T19:00:00+03:00"},
                  "runs": [{"run": "r1", "at": "t1", "base": "(project) abc"}, {"run": "r2", "at": "t2", "base": "b"}],
                  "notes": ["a note", "another | note"], "unchecked": "the time budget ran out",
                  "anchor_after": anchor_after,
                  "missing": [{"row": "8", "kind": "edge", "test": "`gone`"}],
                  "dropped": [{"row": "9", "kind": "normal", "test": "`old`", "reason": "it went"}]}
            for contained in ("Each row ran under the judges' write containment (root read-only).",
                              "Uncontained (no bwrap): each row ran as `tasks verify` runs the project's command."):
                lines = red._section(st, contained).split("\n")
                self.assertEqual(lines[0], red.RECEIPT_HEADING)
                self.assertTrue(lines[-1].startswith(red.END_HEAD), lines[-1])
                # read back as written, every line of it is the CLI's own: a rewrite keeps none of it
                self.assertEqual(red._kept(lines + ["## Next"], 0, len(lines)), [""])


if __name__ == "__main__":
    unittest.main()
