#!/usr/bin/env python3
"""A judge's answer counts as a review by its form, never by its words (task 347, DESIGN-PLAYBOOK-2.md §3).

Task 320's word check (`models_check.not_a_review`) erred both ways at audit 6: it counted a plan, a refusal and an
error (G1) and failed clean reviews in ordinary words (G2). Each judge now answers in one block that carries its run's
code — `REVIEW <code>`, FINDING / NONE records with one `key: value` field a line, `END <code>`, then the CAP line —
and `tasks/review_form.py` checks the form: the frame, each record alone, and that every place a record names exists.
The panel re-runs a seat whose answer fails the form once, then counts it failed; the CLI writes each seat's records
into judge.md numbered F1… across the round, and the run ledger counts those lines only. Run:
    python3 -m unittest tests.test_review_form
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))

from tests._bwrap_standin import bwrap_usable  # noqa: E402
from tests._judge_stub import TASKS, clean_env, stub_bin, stub_home  # noqa: E402

CODE = "3fa2b9c1d0e4"
FIND = ("FINDING\nseverity: Important\nsettled: no\nwhere: a.py:2\n"
        "scenario: f(1) is called → it returns 1, not 2 → tests/test_a.py fails\ndrum: tests/test_a.py:3\nfix: add one.")
NONE = "NONE\nread: a.py:1-3\nchecked: f's return value and its only caller."
MINOR = ("FINDING\nseverity: Minor\nsettled: no\nwhere: a.py:1\n"
         "scenario: a reader opens a.py → the name f says nothing → it is read twice\ndrum: none\nfix: rename it.")


def block(*records, code=CODE, cap="CAP 1/5 exhausted", before="", after=""):
    return before + "\n".join([f"REVIEW {code}", *records, f"END {code}", cap]) + "\n" + after


def form():
    from tasks import review_form
    return review_form


def kinds(answer):
    return [r.kind for r in answer.records]


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


class _Tree(unittest.TestCase):
    """A project with a code root (`lib`, its own repository) and a task whose start (base.json) is recorded; since
    the start, `a.py` changed and `lib/old.py` was deleted."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.p = Path(tmp.name).resolve() / "proj"
        self.p.mkdir()
        git(self.p, "init", "-q")
        (self.p / ".gitignore").write_text("lib/\n", encoding="utf-8")
        (self.p / "a.py").write_text("def f(x):\n    return x\n\n", encoding="utf-8")
        (self.p / "docs").mkdir()
        (self.p / "docs" / "x.md").write_text("x\n", encoding="utf-8")
        git(self.p, "add", "-A")
        git(self.p, "commit", "-qm", "c0")
        self.c0 = git(self.p, "rev-parse", "HEAD")
        lib = self.p / "lib"
        lib.mkdir()
        git(lib, "init", "-q")
        (lib / "mod.py").write_text("A = 1\nB = 2\n", encoding="utf-8")
        (lib / "old.py").write_text("gone = True\n", encoding="utf-8")
        git(lib, "add", "-A")
        git(lib, "commit", "-qm", "l0")
        self.l0 = git(lib, "rev-parse", "HEAD")
        (self.p / ".agent" / "tasks" / "001-t").mkdir(parents=True)
        (self.p / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["lib"]}), encoding="utf-8")
        self.task_dir = self.p / ".agent" / "tasks" / "001-t"
        (self.task_dir / "base.json").write_text(json.dumps({
            "v": 2, "task": "001", "scopes": {"": {"head": self.c0, "dirty": {}},
                                              "lib": {"head": self.l0, "dirty": {}}}}), encoding="utf-8")
        # the task's work
        (self.p / "a.py").write_text("def f(x):\n    return x + 1\n\n", encoding="utf-8")
        (lib / "old.py").unlink()

    def places(self, root=None, **kw):
        return form().Places.for_task(self.p, root or self.p, self.task_dir, **kw)


class Frame(unittest.TestCase):
    def test_audit_6_g1_texts_are_no_review(self):
        # audit 6's G1 texts (`322-*/evidence/m2-repro/g1g2_fn.py`) and the shapes real judges returned (task 319's
        # sweeps): none holds a block with the run's code, so none is a review — whatever its words
        texts = {
            "G1-A no-problem plan": "No problem! I'll start by reading the diff and the tests.\nCAP: 0/5 reported, more remain",
            "G1-B plan naming a tracker": "There is no issue tracker link; I'll read the code next.\nCAP: 0/5 reported, more remain",
            "G1-C refusal saying no issues": "I can't help with that, and there are no issues I can discuss.\nCAP: 0/5 reported, exhausted",
            "G1-D CAP>0, no finding": "Let me read the files first.\nCAP: 2/5 reported, more remain",
            "G1-E severities as a plan list": ("Plan:\n- **Critical** paths: review.py, lifecycle.py\n- **Minor** paths: docs\n"
                                               "I'll start with the first.\nCAP: 0/5 reported, more remain"),
            "G1-F sandbox error citing file:line": "Permission denied reading tasks/review.py:1\nCAP: 0/5 reported, exhausted",
            "refusal": "I'm sorry, I can't help with reviewing this.",
            "gemini filter": "This request was blocked by Gemini's filters. Please try rephrasing your prompt.",
            "agy wait": ("I have launched `python3 scripts/verify` in the background.\nWaiting for it.\n"
                         "CAP: 0/5 reported, exhausted"),
            "an old-style finding": "**Important** — `src/x.py:1` a finding.\n\nCAP: 1/5 reported, exhausted",
            "empty": "",
        }
        for name, text in texts.items():
            with self.subTest(name):
                a = form().read_answer(text, CODE)
                self.assertFalse(a.ok, name)
                self.assertTrue(a.why.strip())

    def test_audit_6_g2_review_counts_in_the_form(self):
        g2 = {
            "G2-a no blocking issues": "I read app.py and the task; the plan is sound.\nNo blocking issues found.\nCAP: 0/5 reported, exhausted",
            "G2-b LGTM": "LGTM — the change is correct.\nCAP: 0/5 reported, exhausted",
            "G2-c sound + covered": "The fix is sound and the new test covers the regression.\nCAP: 0/5 reported, exhausted",
        }
        for name, text in g2.items():
            with self.subTest(name):
                self.assertFalse(form().read_answer(text, CODE).ok, "plain words are no form")
        with tempfile.TemporaryDirectory() as t:
            p = Path(t).resolve()
            git(p, "init", "-q")
            (p / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
            (p / "test_app.py").write_text("import app\nassert app.f() == 2\n", encoding="utf-8")
            git(p, "add", "-A")
            git(p, "commit", "-qm", "c0")
            td = p / ".agent" / "tasks" / "001-t"
            td.mkdir(parents=True)
            (td / "base.json").write_text(json.dumps({"v": 2, "scopes": {"": {"head": git(p, "rev-parse", "HEAD"),
                                                                              "dirty": {}}}}), encoding="utf-8")
            (p / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")
            places = form().Places.for_task(p, p, td)
            text = block("NONE", "read: app.py:1-2, test_app.py",
                         "checked: f now returns 2 and test_app.py asserts it.", cap="CAP 0/5 exhausted",
                         before="I read app.py and test_app.py; f now returns 2 and the test checks it.\n")
            a = form().read_answer(text, CODE, places)
            self.assertTrue(a.ok, a.why)
            self.assertEqual(kinds(a), ["NONE"])
            self.assertEqual(a.cap[:2], (0, 5))

    def test_frame_rules(self):
        rf = form()
        bad = {
            "another run's code": block(FIND, code="ffffffffffff"),
            "two blocks": block(FIND) + block(FIND),
            "END before REVIEW": f"END {CODE}\n{FIND}\nREVIEW {CODE}\nCAP 1/5 exhausted\n",
            "no END": f"REVIEW {CODE}\n{FIND}\nCAP 1/5 exhausted\n",
            "no CAP": f"REVIEW {CODE}\n{FIND}\nEND {CODE}\n",
            "prose before CAP": f"REVIEW {CODE}\n{FIND}\nEND {CODE}\nThat is all.\nCAP 1/5 exhausted\n",
            "k over n": block(FIND, cap="CAP 6/5 exhausted"),
            "no records": block(cap="CAP 0/5 exhausted"),
            "REVIEW inside a sentence": f"I write REVIEW {CODE} now.\n{FIND}\nEND {CODE}\nCAP 1/5 exhausted\n",
        }
        for name, text in bad.items():
            with self.subTest(name):
                a = rf.read_answer(text, CODE)
                self.assertFalse(a.ok, name)
                self.assertTrue(a.why.strip())
        good = {
            "plain": block(FIND),
            "a fence around the block": "```\n" + block(FIND, cap="```\nCAP 1/5 exhausted"),
            "a rule before CAP": block(FIND, cap="---\n\nCAP 1/5 exhausted"),
            "the old CAP form": block(FIND, cap="CAP: 1/5 reported, exhausted"),
            "the old more remain": block(FIND, cap="CAP: 5/5 reported, more remain"),
            "more-remain": block(FIND, cap="CAP 5/5 more-remain"),
            "frame lines indented": f"  REVIEW {CODE}  \n{FIND}\n END {CODE}\nCAP 1/5 exhausted\n",
            "words after CAP": block(FIND, after="Let me know if you want a deeper pass.\n"),
            "a bold CAP": block(FIND, cap="**CAP 1/5 exhausted**"),
            "a backticked old CAP": block(FIND, cap="`CAP: 1/5 reported, exhausted`"),
        }
        for name, text in good.items():
            with self.subTest(name):
                a = rf.read_answer(text, CODE)
                self.assertTrue(a.ok, f"{name}: {a.why}")
        self.assertEqual(rf.read_answer(block(FIND, cap="CAP 5/5 more-remain"), CODE).cap, (5, 5, "more-remain"))
        self.assertEqual(rf.read_answer(block(FIND, cap="CAP: 2/5 reported, exhausted"), CODE).cap, (2, 5, "exhausted"))

    def test_a_preamble_glued_to_the_review_line(self):
        # task 347's live probe: grok:grok-4.7:medium ran its preamble into the frame line, in both its answers
        grok = ("I'll review the completed `add()` change against the task intent by reading the changed sources and "
                "how they're tested.REVIEW 39a93f78f24c\nFINDING\nseverity: Critical\nsettled: no\nwhere: calc.py:3\n"
                "scenario: add(2, 3) → body computes a - b and returns -1 → test_calc.py:3 raises AssertionError "
                "(python3 test_calc.py exits 1)\ndrum: test_calc.py:3 calls calc.add(2, 3) at import\n"
                "fix: Return a + b so add returns the sum of its arguments.\nEND 39a93f78f24c\nCAP 1/5 exhausted\n")
        rf = form()
        a = rf.read_answer(grok, "39a93f78f24c")
        self.assertTrue(a.ok, a.why)
        self.assertEqual((kinds(a), a.records[0].severity), (["FINDING"], "Critical"))
        # a quoted line never opens a block (the re-run note quotes an earlier answer): here only the REVIEW line is
        # the quote, the rest a whole block of its own
        first, rest = grok.split("\n", 1)
        self.assertFalse(rf.read_answer("> " + first + "\n" + rest, "39a93f78f24c").ok)
        self.assertFalse(rf.read_answer("\n".join("> " + ln for ln in grok.splitlines()) + "\n", "39a93f78f24c").ok)
        # a whole REVIEW line is the frame: a sentence of the reasoning that ends with the code is not a second one
        said = "Below, the block opens with REVIEW 39a93f78f24c\n" + grok.split("tested.", 1)[1]
        self.assertTrue(rf.read_answer(said, "39a93f78f24c").ok)

    def test_an_echoed_prompt_is_no_block(self):
        from tasks import template as T
        rf = form()
        clause = rf.prompt_clause(CODE)
        self.assertIn(CODE, clause)
        self.assertFalse(rf.read_answer(clause, CODE).ok)
        prompt = T.panel_impl_review_prompt(".agent/tasks/001-t/task.md", code=CODE)
        self.assertFalse(rf.read_answer(prompt, CODE).ok)
        self.assertFalse(rf.read_answer(prompt + "\n\n" + prompt, CODE).ok)
        # the re-run note quotes an earlier answer that held the frame lines (it lacked only the CAP line)
        earlier = f"REVIEW {CODE}\n{FIND}\nEND {CODE}\n"
        first = rf.read_answer(earlier, CODE)
        self.assertFalse(first.ok)
        note = rf.rerun_note(first.why, earlier)
        self.assertIn(f"> REVIEW {CODE}", note)
        self.assertFalse(rf.read_answer(note, CODE).ok)
        self.assertFalse(rf.read_answer(prompt + "\n\n" + note, CODE).ok)


class Records(unittest.TestCase):
    def read(self, *records):
        return form().read_answer(block(*records), CODE)

    def test_finding_fields(self):
        a = self.read(FIND)
        self.assertTrue(a.ok, a.why)
        r = a.records[0]
        self.assertEqual((r.kind, r.severity, r.settled, r.counted), ("FINDING", "Important", "no", True))
        self.assertEqual(r.fields["where"], "a.py:2")
        shuffled = ("FINDING\nFIX: add one.\nScenario: f(1) → 1 → test_a fails\nDRUM: none\nWhere: a.py:2\n"
                    "settled: settled-contradicted\nseverity: critical")
        r = self.read(shuffled).records[0]
        self.assertEqual((r.kind, r.severity, r.settled, r.counted), ("FINDING", "Critical", "SETTLED-CONTRADICTED", True))
        r = self.read(FIND.replace("settled: no", "settled: SETTLED")).records[0]
        self.assertEqual((r.kind, r.counted), ("FINDING", False))
        r = self.read(FIND.replace("→ it returns 1, not 2 →", "-> it returns 1, not 2 ->")).records[0]
        self.assertEqual(r.kind, "FINDING", r.why)
        broken = {
            "missing fix": (FIND.replace("\nfix: add one.", ""), "fix"),
            "repeated where": (FIND + "\nwhere: a.py:1", "where"),
            "unknown key": (FIND + "\nnote: x", "note"),
            "bad severity": (FIND.replace("Important", "High"), "severity"),
            "bad settled": (FIND.replace("settled: no", "settled: yes"), "settled"),
            "a scenario of two parts": (FIND.replace("f(1) is called → it returns 1, not 2 → tests/test_a.py fails",
                                                     "f(1) returns 1 → test_a fails"), "scenario"),
            "a stray line": (FIND + "\nThis one is important.", "field"),
            "an empty fix": (FIND.replace("fix: add one.", "fix:"), "fix"),
            "a where with no line": (FIND.replace("where: a.py:2", "where: a.py"), "where"),
        }
        for name, (rec, word) in broken.items():
            with self.subTest(name):
                a = self.read(rec, MINOR)
                self.assertTrue(a.ok, a.why)
                self.assertEqual(kinds(a), ["BROKEN", "FINDING"], name)
                self.assertIn(word, a.records[0].why.lower())
                self.assertTrue(a.records[0].lines)

    def test_a_broken_record_stands_alone(self):
        a = self.read(FIND.replace("Important", "High"), FIND)
        self.assertTrue(a.ok, a.why)
        self.assertEqual(kinds(a), ["BROKEN", "FINDING"])
        a = self.read(FIND.replace("Important", "High"), "NONE\nread: a.py:1")
        self.assertFalse(a.ok)
        self.assertIn("broken", a.why.lower())
        a = self.read("Here is my review.", FIND)
        self.assertTrue(a.ok, a.why)
        self.assertEqual(kinds(a), ["BROKEN", "FINDING"])
        self.assertEqual(a.records[0].lines, ["Here is my review."])

    def test_none_rules(self):
        a = self.read(NONE, NONE)
        self.assertTrue(a.ok, a.why)
        self.assertEqual(kinds(a), ["NONE", "BROKEN"])
        a = self.read(NONE, FIND.replace("Important", "Critical"))
        self.assertTrue(a.ok, a.why)
        self.assertEqual(kinds(a), ["BROKEN", "FINDING"])
        self.assertIn("none", a.records[0].why.lower())
        a = self.read(MINOR, NONE)
        self.assertEqual(kinds(a), ["FINDING", "NONE"])
        a = self.read(NONE, FIND.replace("settled: no", "settled: SETTLED"))
        self.assertEqual(kinds(a), ["NONE", "FINDING"], "a settled re-raise is not counted: the NONE stands")

    def test_where_none_needs_a_file(self):
        a = self.read(FIND.replace("where: a.py:2", "where: none"))
        self.assertEqual(kinds(a), ["FINDING"], a.records[0].why)
        no_file = FIND.replace("where: a.py:2", "where: none").replace(
            "f(1) is called → it returns 1, not 2 → tests/test_a.py fails", "a caller passes 1 → it gets 1, e.g. not 2 → it fails")
        a = self.read(no_file, MINOR)
        self.assertEqual(kinds(a), ["BROKEN", "FINDING"])
        self.assertIn("where", a.records[0].why.lower())
        named = no_file.replace("it fails", "tests/test_round.py has no test for it")
        self.assertEqual(kinds(self.read(named)), ["FINDING"])

    def test_lenient_places_and_cap(self):
        # impl round 1 (sonnet, opus, agy): several places in one `where:`, a column, a CAP line with a period
        rf = form()
        for where in ("a.py:2, b.py:10-12", "a.py:2:7", "`a.py:2`, `b.py:3`"):
            with self.subTest(where):
                self.assertEqual(kinds(self.read(FIND.replace("where: a.py:2", f"where: {where}"))), ["FINDING"])
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.py").write_text("1\n2\n3\n", encoding="utf-8")
            pl = rf.Places(Path(d))
            a = rf.read_answer(block(FIND.replace("where: a.py:2", "where: a.py:2, nope.py:1"), MINOR), CODE, pl)
            self.assertEqual(kinds(a), ["BROKEN", "FINDING"], "each place of the list is checked")
            self.assertEqual(kinds(rf.read_answer(block(FIND.replace("where: a.py:2", "where: a.py:2:5")), CODE, pl)),
                             ["FINDING"])
            self.assertEqual(kinds(rf.read_answer(block(FIND.replace("where: a.py:2", "where: a.py:2, a.py:3")), CODE,
                                                  pl)), ["FINDING"], "each place of the list is read on its own")
        for cap in ("CAP 1/5 exhausted.", "CAP 5/5 more remain", "**CAP 1/5 exhausted.**"):
            with self.subTest(cap):
                self.assertTrue(rf.read_answer(block(FIND, cap=cap), CODE).ok, cap)

    def test_where_none_names_short_and_bare_files(self):
        # impl round 1 (grok): a one-letter extension, or a bare name an existing project file has
        rf = form()
        none = FIND.replace("where: a.py:2", "where: none")
        short = none.replace("f(1) is called → it returns 1, not 2 → tests/test_a.py fails",
                             "a caller includes calc.h → it lacks add's prototype → the build warns")
        self.assertEqual(kinds(self.read(short)), ["FINDING"])
        eg = none.replace("f(1) is called → it returns 1, not 2 → tests/test_a.py fails",
                          "a caller → e.g. it fails → nothing shows")
        self.assertEqual(kinds(self.read(eg, MINOR)), ["BROKEN", "FINDING"])
        bare = none.replace("f(1) is called → it returns 1, not 2 → tests/test_a.py fails",
                            "make test runs → the Makefile has no test target → nothing is tested")
        self.assertEqual(kinds(self.read(bare, MINOR)), ["BROKEN", "FINDING"], "no places: a bare word is no file")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Makefile").write_text("all:\n", encoding="utf-8")
            git(d, "init", "-q")
            pl = rf.Places(Path(d), scopes={"": (Path(d), None)})
            self.assertEqual(kinds(rf.read_answer(block(bare), CODE, pl)), ["FINDING"])

    def test_line_breaks_and_bom(self):
        rf = form()
        crlf = block(FIND, MINOR).replace("\n", "\r\n")
        a = rf.read_answer(crlf, CODE)
        self.assertTrue(a.ok, a.why)
        self.assertEqual(kinds(a), ["FINDING", "FINDING"])
        self.assertTrue(rf.read_answer("\ufeff" + block(FIND), CODE).ok)
        for sep in ("\u2028", "\u0085"):
            with self.subTest(repr(sep)):
                split = FIND.replace("it returns 1, not 2", "it returns 1," + sep + "not 2")
                a = rf.read_answer(block(split, MINOR), CODE)
                self.assertTrue(a.ok, a.why)
                self.assertEqual(kinds(a), ["BROKEN", "FINDING"])

    def test_size_bounds(self):
        rf = form()
        a = rf.read_answer(block(*([MINOR] * 31)), CODE)
        self.assertTrue(a.ok, a.why)
        self.assertEqual(kinds(a), ["FINDING"] * 30 + ["BROKEN"])
        self.assertIn("30", a.records[-1].why)
        a = rf.read_answer(block(FIND.replace("add one.", "x" * 4001), MINOR), CODE)
        self.assertEqual(kinds(a), ["BROKEN", "FINDING"])
        self.assertIn("4,000", a.records[0].why)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t).resolve()
            with open(p / "big.log", "wb") as f:
                f.truncate(33 * 1024 * 1024)
            (p / "a.py").write_text("x\n", encoding="utf-8")
            places = rf.Places(p)
            path, why = places.locate("big.log:1", whole_file=False)
            self.assertIsNone(path)
            self.assertIn("MiB", why)
            self.assertEqual(places.locate("a.py:1", whole_file=False), ("a.py", ""))


class Places(_Tree):
    def test_current_side(self):
        pl = self.places()
        out = self.p.parent / "outside.py"
        out.write_text("x\n", encoding="utf-8")
        (self.p / "link.py").symlink_to(out)
        broken = {"a missing file": "nope.py:1", "past the end": "a.py:4", "a reversed range": "a.py:3-1",
                  "line 0": "a.py:0", "a directory": "docs:1", "out by ..": "../outside.py:1",
                  "a symlink out": "link.py:1", "absolute outside": f"{out}:1", "no line": "a.py:"}
        for name, loc in broken.items():
            with self.subTest(name):
                path, why = pl.locate(loc, whole_file=False)
                self.assertIsNone(path, name)
                self.assertTrue(why)
        good = {"a line": ("a.py:2", "a.py"), "a range": ("a.py:1-3", "a.py"),
                "absolute inside": (f"{self.p}/a.py:1", "a.py"), "code-root relative": ("mod.py:2", "lib/mod.py"),
                "project relative in a code root": ("lib/mod.py:1", "lib/mod.py"),
                "backticked": ("`a.py:2`", "a.py"), "./": ("./a.py:1", "a.py")}
        for name, (loc, want) in good.items():
            with self.subTest(name):
                self.assertEqual(pl.locate(loc, whole_file=False), (want, ""), name)
        self.assertEqual(pl.locate("docs/x.md", whole_file=True), ("docs/x.md", ""))
        self.assertIsNone(pl.locate("docs/x.md", whole_file=False)[0])
        # a partial path resolves by its unique suffix among the project's files; two files make it broken
        for d in ("one", "two"):
            (self.p / "docs" / d).mkdir()
            (self.p / "docs" / d / "z.py").write_text("z = 1\n", encoding="utf-8")
        (self.p / "cr.txt").write_bytes(b"a\rb\rc")
        (self.task_dir / "task.md").write_text("# t\n\n## Intent\nx\ny\n", encoding="utf-8")
        older = self.p / ".agent" / "tasks" / "000-older"       # a project holds many task.md: no unique suffix
        older.mkdir()
        (older / "task.md").write_text("# older\n" * 9, encoding="utf-8")
        pl = self.places()
        self.assertEqual(pl.locate("x.md:1", whole_file=False), ("docs/x.md", ""))
        self.assertEqual(pl.locate("one/z.py:1", whole_file=False), ("docs/one/z.py", ""))
        path, why = pl.locate("z.py:1", whole_file=False)
        self.assertIsNone(path)
        self.assertIn("docs/one/z.py", why)
        self.assertIn("docs/two/z.py", why)
        self.assertEqual(pl.locate("task.md:5", whole_file=False), (".agent/tasks/001-t/task.md", ""))
        self.assertIsNone(pl.locate("task.md:6", whole_file=False)[0])
        self.assertEqual(pl.locate("cr.txt:3", whole_file=False), ("cr.txt", ""), "a lone CR ends a line")
        # the tree the judges read, when it is another folder (the review's fixed copy)
        copy = self.p.parent / "copy"
        copy.mkdir()
        (copy / "a.py").write_text("one line\n", encoding="utf-8")
        pc = form().Places.for_task(self.p, copy, self.task_dir)
        self.assertEqual(pc.locate("a.py:1", whole_file=False), ("a.py", ""))
        self.assertIsNone(pc.locate("a.py:2", whole_file=False)[0])
        # an entry the copy binds live is an empty mountpoint in the copy: it is read from the project
        (self.p / "out.log").write_text("1\n2\n3\n", encoding="utf-8")
        (copy / "out.log").touch()                      # `review_view._make_mountpoints`' stand-in for a file
        self.assertIsNone(pc.locate("out.log:2", whole_file=False)[0])
        pc = form().Places.for_task(self.p, copy, self.task_dir, live=["out.log"])
        self.assertEqual(pc.locate("out.log:2", whole_file=False), ("out.log", ""))

    def test_base_side(self):
        pl = self.places()
        self.assertEqual(pl.locate("base:lib/old.py:1", whole_file=False), ("lib/old.py", ""))
        self.assertEqual(pl.locate("base:old.py:1", whole_file=False), ("lib/old.py", ""))
        self.assertEqual(pl.locate("base:a.py:2", whole_file=False), ("a.py", ""))
        self.assertIsNone(pl.locate("base:a.py:9", whole_file=False)[0])
        self.assertIsNone(pl.locate("base:nope.py:1", whole_file=False)[0])
        self.assertIsNone(pl.locate("lib/old.py:1", whole_file=False)[0], "deleted: not on the current side")
        (self.task_dir / "base.json").unlink()
        pl = self.places()
        path, why = pl.locate("base:a.py:1", whole_file=False)
        self.assertIsNone(path)
        self.assertIn("start", why)
        self.assertEqual(pl.locate("a.py:1", whole_file=False), ("a.py", ""))
        (self.task_dir / "base.json").write_text(json.dumps({"v": 2, "scopes": {"": {"head": "0" * 40, "dirty": {}},
                                                                                "lib": {"head": self.l0}}}),
                                                 encoding="utf-8")
        pl = self.places()
        self.assertIsNone(pl.locate("base:a.py:1", whole_file=False)[0])
        self.assertEqual(pl.locate("base:old.py:1", whole_file=False), ("lib/old.py", ""))

    def test_read_names_a_changed_file(self):
        rf = form()
        pl = self.places()
        self.assertEqual(pl.changed, {"a.py", "lib/old.py"})
        names_none = block("NONE", "read: docs/x.md:1", "checked: the docs.", cap="CAP 0/5 exhausted")
        a = rf.read_answer(names_none, CODE, pl)
        self.assertFalse(a.ok)
        self.assertEqual(kinds(a), ["BROKEN"])
        self.assertIn("changed", a.records[0].why)
        for read in ("a.py:2", "base:lib/old.py:1", "docs/x.md:1, base:old.py", "mod.py, a.py"):
            with self.subTest(read):
                a = rf.read_answer(block("NONE", f"read: {read}", "checked: x.", cap="CAP 0/5 exhausted"), CODE, pl)
                self.assertTrue(a.ok, a.why)
        # in a plan review the rule is not applied
        self.assertIsNone(self.places(impl=False).changed)
        self.assertTrue(rf.read_answer(names_none, CODE, self.places(impl=False)).ok)
        # the impl prompt names the files; a long list is cut and says how many more
        clause = rf.prompt_clause(CODE, changed=sorted(pl.changed))
        self.assertIn("`a.py`", clause)
        self.assertIn("`lib/old.py`", clause)
        many = rf.prompt_clause(CODE, changed=[f"f{i}.py" for i in range(45)])
        self.assertIn("and 5 more", many)
        self.assertNotIn("`f44.py`", many)
        # a file untracked at the start and deleted since is on neither side: it is not asked for
        doc = json.loads((self.task_dir / "base.json").read_text(encoding="utf-8"))
        doc["scopes"][""]["dirty"] = {"tmp.txt": "0" * 64}
        (self.task_dir / "base.json").write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(self.places().changed, {"a.py", "lib/old.py"})
        doc["scopes"][""]["dirty"] = {}
        (self.task_dir / "base.json").write_text(json.dumps(doc), encoding="utf-8")
        # nothing changed since the start: no file to name
        git(self.p, "checkout", "-q", "--", "a.py")
        git(self.p / "lib", "checkout", "-q", "--", "old.py")
        pl = self.places()
        self.assertEqual(pl.changed, set())
        self.assertTrue(rf.read_answer(names_none, CODE, pl).ok)
        # the changes cannot be read (no start recorded): the rule is not applied
        (self.task_dir / "base.json").unlink()
        pl = self.places()
        self.assertIsNone(pl.changed)
        self.assertTrue(rf.read_answer(names_none, CODE, pl).ok)


    def test_a_project_in_a_subdirectory_of_its_repository(self):
        # impl round 1 (codex): `base:` and the changed files read through the project's prefix in its repository
        rf = form()
        top = self.p.parent / "mono"
        app = top / "app"
        app.mkdir(parents=True)
        git(top, "init", "-q")
        (top / "other.py").write_text("o = 1\n", encoding="utf-8")
        (app / "src.py").write_text("s = 1\ns = 2\n", encoding="utf-8")
        (app / "keep.py").write_text("k = 1\n", encoding="utf-8")
        git(top, "add", "-A")
        git(top, "commit", "-qm", "m0")
        td = app / ".agent" / "tasks" / "001-t"
        td.mkdir(parents=True)
        (td / "base.json").write_text(json.dumps({"v": 2, "scopes": {"": {"head": git(top, "rev-parse", "HEAD"),
                                                                          "dirty": {}}}}), encoding="utf-8")
        (app / "src.py").unlink()
        pl = rf.Places.for_task(app, app, td)
        self.assertEqual(pl.changed, {"src.py"})
        self.assertEqual(pl.locate("base:src.py:2", whole_file=False), ("src.py", ""))
        self.assertIsNone(pl.locate("base:other.py:1", whole_file=False)[0], "outside the project")
        a = rf.read_answer(block("NONE", "read: base:src.py:1-2", "checked: x.", cap="CAP 0/5 exhausted"), CODE, pl)
        self.assertTrue(a.ok, a.why)

    def test_the_sealed_copy_and_a_base_suffix(self):
        # impl round 1 (codex, opus): what the copy lacks is read live only under a live bind; the suffix index is
        # read when Places is built; a `base:` suffix is looked up in the start commit
        rf = form()
        copy = self.p.parent / "copy"
        (copy / "docs").mkdir(parents=True)
        (copy / "docs" / "x.md").write_text("x\n", encoding="utf-8")
        (self.p / "late.py").write_text("late\n", encoding="utf-8")       # made after the seal: not in the copy
        pc = rf.Places.for_task(self.p, copy, self.task_dir)
        self.assertIsNone(pc.locate("late.py:1", whole_file=False)[0])
        self.assertEqual(rf.Places.for_task(self.p, copy, self.task_dir, live=["late.py"]).locate(
            "late.py:1", whole_file=False), ("late.py", ""))
        pl = self.places()
        (self.p / "other").mkdir()
        (self.p / "other" / "x.md").write_text("y\n", encoding="utf-8")   # after the Places was built
        self.assertEqual(pl.locate("x.md:1", whole_file=False), ("docs/x.md", ""))
        # a deleted file cited by a partial path on the base side
        (self.p / "docs" / "deep").mkdir()
        (self.p / "docs" / "deep" / "gone.py").write_text("g = 1\n", encoding="utf-8")
        git(self.p, "add", "-A")
        git(self.p, "commit", "-qm", "c1")
        doc = json.loads((self.task_dir / "base.json").read_text(encoding="utf-8"))
        doc["scopes"][""]["head"] = git(self.p, "rev-parse", "HEAD")
        (self.task_dir / "base.json").write_text(json.dumps(doc), encoding="utf-8")
        git(self.p, "rm", "-q", "docs/deep/gone.py")          # a staged deletion: the index no longer holds it
        self.assertEqual(self.places().locate("base:deep/gone.py:1", whole_file=False), ("docs/deep/gone.py", ""))

    def test_links_inside_the_project(self):
        # impl round 1 (codex, sonnet): an absolute link into the project, in the copy; an alias of a changed file
        rf = form()
        copy = self.p.parent / "copy"
        copy.mkdir()
        (copy / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
        (copy / "abs.py").symlink_to(self.p / "a.py")                       # the judges' mount maps it to the copy
        pc = rf.Places.for_task(self.p, copy, self.task_dir)
        self.assertEqual(pc.locate("abs.py:2", whole_file=False), ("abs.py", ""))
        (self.p / "alias.py").symlink_to("a.py")                            # unchanged since the start: committed
        git(self.p, "add", "alias.py")
        git(self.p, "commit", "-qm", "alias")
        doc = json.loads((self.task_dir / "base.json").read_text(encoding="utf-8"))
        doc["scopes"][""]["head"] = git(self.p, "rev-parse", "HEAD")
        (self.task_dir / "base.json").write_text(json.dumps(doc), encoding="utf-8")
        pl = self.places()
        self.assertEqual(pl.changed, {"a.py", "lib/old.py"}, "a.py changed; its alias did not")
        a = rf.read_answer(block("NONE", "read: alias.py:1", "checked: x.", cap="CAP 0/5 exhausted"), CODE, pl)
        self.assertTrue(a.ok, a.why)

    def test_round_1_minors(self):
        rf = form()
        # a longer token that ends with REVIEW is no frame line (grok)
        self.assertFalse(rf.read_answer(f"PREVIEW {CODE}\n{FIND}\nEND {CODE}\nCAP 1/5 exhausted\n", CODE).ok)
        # a base candidate in a scope with no start goes on to the next one (grok)
        pl = rf.Places(self.p, scopes={"": (self.p, None), "lib": (self.p / "lib", self.l0)})
        self.assertEqual(pl.locate("base:old.py:1", whole_file=False), ("lib/old.py", ""))
        # the answer's reason quotes no judge text — it reaches the spend journal (sonnet)
        a = rf.read_answer(block("FINDING\nSECRET-WORDS-123 here"), CODE)
        self.assertFalse(a.ok)
        self.assertNotIn("SECRET-WORDS-123", a.why)


class Prompts(unittest.TestCase):
    def test_every_review_prompt_asks_for_the_form(self):
        from tasks import rulings
        from tasks import template as T
        for name in ("plan_review_prompt", "impl_review_prompt", "panel_plan_review_prompt", "panel_impl_review_prompt"):
            with self.subTest(name):
                p = getattr(T, name)(".agent/tasks/001-t/task.md", code=CODE, settled=True)
                self.assertIn(f"`{CODE}`", p)
                for word in ("REVIEW", "END", "FINDING", "NONE", "severity:", "settled:", "where:", "scenario:",
                             "drum:", "fix:", "read:", "checked:", "CAP <k>/5 exhausted", "CAP 5/5 more-remain",
                             "SETTLED-CONTRADICTED", "base:"):
                    self.assertIn(word, p)
                self.assertNotIn("Start every finding with its severity in bold", p)
                self.assertFalse(any(ln.strip() in (f"REVIEW {CODE}", f"END {CODE}") for ln in p.splitlines()))
        self.assertIn("settled:", rulings.SETTLED_HEADER)
        self.assertNotIn("[SETTLED]", rulings.SETTLED_HEADER)


# ── through the CLI, with the stand-in judge (tests/_judge_stub.py) ──────────────────────────────────────────────────

_PLAN_ONLY = "I will start by reading the task file and the changed code.\n"
_NONE_OK = "REVIEW {CODE}\nNONE\nread: README.md:1\nchecked: the readme's one line.\nEND {CODE}\nCAP 0/5 exhausted\n"


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class _Cli(unittest.TestCase):
    PANEL = ["opus", "sonnet", "haiku"]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.p = self.base / "proj"
        self.p.mkdir()
        home, self.stub_out = stub_home(self.base)
        env = clean_env(HOME=str(home))
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.p, check=True, env=env)
        (self.p / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.p, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "c0"], cwd=self.p, check=True, env=env)
        (self.p / ".agent" / "tasks").mkdir(parents=True)
        (self.p / ".agent" / "models.json").write_text(
            json.dumps({"panel": self.PANEL, "default_judge": "sonnet"}), encoding="utf-8")
        self.env = clean_env(HOME=str(home), PATH=f"{stub_bin(self.base / 'bin')}{os.pathsep}{env['PATH']}",
                             PB_STUB_DIR=str(self.stub_out))
        r = self.tasks("new", "light", "probe", "a probe task for the judges")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tasks(self, *args, env=None):
        return subprocess.run([str(TASKS), *args], cwd=self.p, env=env or self.env, capture_output=True,
                              text=True, timeout=600)

    def say(self, **texts):
        """The stand-in's answers: `haiku="…"` for every call, `haiku_2="…"` for its second call; `{CODE}` becomes
        the code the prompt carries."""
        d = self.stub_out / "say"
        d.mkdir(exist_ok=True)
        for key, text in texts.items():
            model, _, n = key.partition("_")
            (d / f"{self.model(model)}{'.' + n if n else ''}.txt").write_text(text, encoding="utf-8")
        return dict(self.env, PB_STUB_SAY=str(d))

    @staticmethod
    def model(name):
        """The model the panel passes the stand-in for a seat (opus's carries its 1M context)."""
        return f"claude-{name}-5-5" + ("[1m]" if name == "opus" else "")

    def task_dir(self):
        return next((self.p / ".agent" / "tasks").glob("001-*"))

    def judge_md(self) -> str:
        return (self.task_dir() / "judge.md").read_text(encoding="utf-8")

    def prompts(self, model):
        """The prompts the stand-in saved for one model, oldest first."""
        sent = sorted((p for p in self.stub_out.iterdir() if p.name.startswith(f"prompt-{self.model(model)}-")),
                      key=lambda p: p.stat().st_mtime_ns)
        return [p.read_text(encoding="utf-8", errors="replace") for p in sent]


class PanelForm(_Cli):
    def test_a_failed_form_is_rerun_once(self):
        import re
        r = self.tasks("panel-review", "1", "--mode", "impl",
                       env=self.say(haiku_1=_PLAN_ONLY, haiku_2=_NONE_OK, sonnet_1=_PLAN_ONLY, sonnet_2=_NONE_OK))
        self.assertIn("PANEL PASS: 3/3", r.stdout, r.stdout + r.stderr)
        self.assertEqual(len(self.prompts("opus")), 1, "only the failed seats are re-run")
        for model in ("haiku", "sonnet"):
            with self.subTest(model):
                sent = self.prompts(model)
                self.assertEqual(len(sent), 2, "re-run once")
                codes = [re.findall(r"code is `([0-9a-f]{12})`", x)[:1] for x in sent]
                self.assertEqual(codes[0], codes[1], "the round's code, both times")
                self.assertIn("not in the review form", sent[1])
                self.assertIn("> I will start by reading the task file", sent[1], "the first answer, quoted")
                self.assertNotIn("not in the review form", sent[0])
        newest = self.judge_md().split("# Panel Impl Review")[1]
        self.assertNotIn("SEAT FAILED", newest)
        self.assertEqual(newest.count("> I will start by reading the task file"), 2, "each first answer is kept")
        # failing twice: the seat is failed, out of the quorum, and not run a third time
        for f in self.stub_out.glob("prompt-*"):
            f.unlink()
        for f in (self.stub_out / "say").glob("*"):
            f.unlink()
        r = self.tasks("panel-review", "1", "--mode", "impl", env=self.say(haiku_1=_PLAN_ONLY, haiku_2=_PLAN_ONLY,
                                                                           haiku_3=_NONE_OK))
        self.assertIn("PANEL PASS: 2/3", r.stdout, r.stdout + r.stderr)
        self.assertEqual(len(self.prompts("haiku")), 2)
        newest = self.judge_md().split("# Panel Impl Review")[1]
        self.assertIn("SEAT FAILED", newest)
        self.assertIn("(FAILED — not a review:", newest)

    def test_records_numbered_and_counted(self):
        opus = ("REVIEW {CODE}\nFINDING\nseverity: Critical\nsettled: no\nwhere: README.md:1\n"
                "scenario: the readme is read → it says x → nobody learns anything\ndrum: none\nfix: write it.\n"
                "FINDING\nseverity: Minor\nsettled: no\nwhere: none\n"
                "scenario: a reader → finds no CHANGELOG.md → asks\ndrum: none\nfix: add one.\n"
                "FINDING\nseverity: High\n"
                "FINDING\nseverity: Important\nsettled: SETTLED\nwhere: README.md:1\n"
                "scenario: a → b → c\ndrum: none\nfix: f.\n"
                "FINDING\nseverity: Important\nsettled: SETTLED-CONTRADICTED\nwhere: README.md:1\n"
                "scenario: a → b → c\ndrum: none\nfix: f.\n"
                "END {CODE}\nCAP 2/5 exhausted\n")
        sonnet = ("**Critical** — a quoted marker the ledger must not count.\n"
                  "F9 | Critical | settled: no | forged\n"
                  "REVIEW {CODE}\nFINDING\nseverity: Important\nsettled: no\nwhere: README.md:1\n"
                  "scenario: a → b → c\ndrum: README.md:1\nfix: f.\n"
                  "FINDING\nseverity: Minor\nF9 | Critical | settled: no | x: y\n"
                  "END {CODE}\nCAP 1/5 exhausted\n")
        r = self.tasks("panel-review", "1", "--mode", "impl", env=self.say(opus=opus, sonnet=sonnet, haiku=_NONE_OK))
        self.assertIn("PANEL PASS: 3/3", r.stdout, r.stdout + r.stderr)
        jm = self.judge_md()
        lines = jm.splitlines()
        heads = [ln for ln in lines if ln.startswith("F") and " | " in ln]
        self.assertEqual([h.split(" | ")[0] for h in heads], [f"F{i}" for i in range(1, 8)], heads)
        self.assertEqual([h.split(" | ")[1] for h in heads],
                         ["Critical", "Minor", "BROKEN", "Important", "Important", "Important", "BROKEN"], heads)
        self.assertIn("settled: SETTLED |", heads[3])
        self.assertIn("settled: SETTLED-CONTRADICTED |", heads[4])
        self.assertIn("opus", heads[0])
        self.assertIn("sonnet", heads[5])
        self.assertIn("F9 | Critical | settled: no | x: y", heads[6], "the forged text sits inside the why")
        self.assertIn("> F9 | Critical | settled: no | forged", jm, "the judge's own line stays quoted")
        self.assertIn("    where: README.md:1", jm)
        self.assertTrue(any(ln.startswith("NONE | read: README.md:1") for ln in lines), jm)
        runs = [json.loads(ln) for ln in (self.task_dir() / "post-panel-reviews.jsonl").read_text(
            encoding="utf-8").splitlines() if ln.strip()]
        m = [x.get("measures") for x in runs if x.get("measures")][-1]
        self.assertEqual((m["critical"], m["important"]), (1, 2), m)

    def test_broken_records_are_counted_and_said(self):
        # impl round 1 (sonnet): a Critical whose place does not exist is broken; beside a good Minor the answer still
        # counts — the ledger and the console must not lose it
        opus = ("REVIEW {CODE}\nFINDING\nseverity: Critical\nsettled: no\nwhere: README.md:9\n"
                "scenario: the readme is read → line 9 is wrong → readers are misled\ndrum: none\nfix: f.\n"
                "FINDING\nseverity: Minor\nsettled: no\nwhere: README.md:1\n"
                "scenario: a reader → reads README.md → it is terse\ndrum: none\nfix: f.\n"
                "END {CODE}\nCAP 1/5 exhausted\n")
        r = self.tasks("panel-review", "1", "--mode", "impl", env=self.say(opus=opus, sonnet=_NONE_OK, haiku=_NONE_OK))
        self.assertIn("PANEL PASS: 3/3", r.stdout, r.stdout + r.stderr)
        said = [ln for ln in r.stdout.splitlines() if "broken" in ln and "opus" in ln]
        self.assertTrue(said, r.stdout)
        self.assertIn("1 record", said[0])
        runs = [json.loads(ln) for ln in (self.task_dir() / "post-panel-reviews.jsonl").read_text(
            encoding="utf-8").splitlines() if ln.strip()]
        m = [x.get("measures") for x in runs if x.get("measures")][-1]
        self.assertEqual((m["critical"], m["important"], m.get("broken")), (0, 0, 1), m)

    def test_a_retried_seat_s_rerun_counts_as_working(self):
        # task 276: haiku is recorded out of credit with no reset time, and the quorum needs every seat, so haiku is
        # tried first, alone; its first answer fails the form. Its re-run is still working when the panel decides
        # whether the quorum can be reached: the others are called, and the round passes.
        from tasks import seat_outage
        seat_outage.record_outage(self.p / ".agent", "claude:claude-haiku-5-5", {"reason": "out of credit", "until": None})
        (self.p / ".agent" / "config.json").write_text(json.dumps({"panel_quorum": "all"}), encoding="utf-8")
        r = self.tasks("panel-review", "1", "--mode", "impl", env=self.say(haiku_1=_PLAN_ONLY, haiku_2=_NONE_OK))
        self.assertIn("PANEL PASS: 3/3", r.stdout, r.stdout + r.stderr)
        self.assertEqual(len(self.prompts("haiku")), 2)
        self.assertEqual(len(self.prompts("opus")), 1)

    def test_a_consultation_is_not_read_for_the_form(self):
        r = self.tasks("panel-review", "--prompt", "Reply with the single word OK.",
                       env=self.say(opus="OK\n", sonnet="OK\n", haiku="OK\n"))
        self.assertIn("PANEL PASS: 3/3", r.stdout, r.stdout + r.stderr)
        self.assertEqual(len(self.prompts("haiku")), 1)

    def test_a_failure_is_not_rerun(self):
        env = dict(self.env, PB_STUB_FAIL_MODELS="claude-haiku-5-5")
        r = self.tasks("panel-review", "1", "--mode", "impl", env=env)
        self.assertIn("PANEL PASS: 2/3", r.stdout, r.stdout + r.stderr)
        self.assertEqual(len(self.prompts("haiku")), 1, "a failure is not the form's: no re-run")
        from tasks import seat_outage
        from tasks.models_check import NOT_A_REVIEW_HEAD, OTHER, classify_failure
        text = f"{NOT_A_REVIEW_HEAD} there is no `REVIEW <code>` line)\n\nYou've hit your usage limit · resets 5pm"
        self.assertEqual(classify_failure(text), OTHER)
        self.assertIsNone(seat_outage.classify_outage(text, provider="claude"))


class SingleJudgeForm(_Cli):
    def test_the_form_decides_the_single_judge(self):
        r = self.tasks("impl-review", "1", env=self.say(sonnet=_NONE_OK))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Saved:", r.stdout)
        log = self.task_dir() / "judge.log"
        first = log.read_text(encoding="utf-8")
        self.assertIn("NONE", first)
        landed = (self.task_dir() / "judge-single.md").read_text(encoding="utf-8")
        self.assertIn("checked: the readme's one line.", landed)
        r = self.tasks("impl-review", "1", env=self.say(sonnet=_PLAN_ONLY))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(FAILED — not a review:", r.stdout + r.stderr)
        self.assertNotIn("Saved:", r.stdout)
        self.assertEqual(log.read_text(encoding="utf-8"), first, "the earlier log is kept")


if __name__ == "__main__":
    unittest.main()
