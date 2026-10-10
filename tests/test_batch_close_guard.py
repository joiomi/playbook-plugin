#!/usr/bin/env python3
"""Guard 0.5 v2 — annotated batch-close (F1), blind-judge-reviewed design.

The old guard hard-blocked 3+ newly-checked gates per write and warned at 2 —
6/6 field journals called out the tax (one logical step forced into split
writes, sharpest at review triage). The redesign keeps the anti-backfill
invariant and releases the tax:

  * delta ≤ 1: exactly the old behavior (silent) — singles stay free;
  * a batch (2–5 newly-checked gates) is allowed ONLY when every newly-checked
    line extends its unchecked original by ≥ 8 unicode non-whitespace chars
    (its own outcome note; a pointer note like "— see Round 2 Result" counts);
  * a bare or sub-floor batch blocks (v1 only WARNED at 2 — tightened);
  * 6+ blocks even fully annotated (ceiling: fabricating notes for the whole
    plan in one write is the checkbox-theater scenario);
  * a checked line with no unchecked counterpart (born-checked) blocks the
    batch — gates are added OPEN, then closed;
  * already-checked lines carried through the edit are IGNORED (not
    born-checked), and unchecking lines cannot launder the batch size — all
    tiers key on the newly-checked count, not the raw x-delta;
  * two batch-closing writes with NO tool call between them block the second
    (the two-writes-of-5 end-of-task pattern, killed mechanically — the
    judge's condition on the design's PASS).

Pairing (judge Finding 1): checked-in-new matching checked-in-old (multiset)
= carried; else prefix-paired to an unchecked-in-old original (longest wins,
empty originals excluded) = newly-checked; else born-checked.

Covers the pure helper (scripts/gate-batch-check.py) and the REAL hook path
(bash task-gate-hook subprocess).

Run: python3 -m unittest tests.test_batch_close_guard
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from tests._bashcheck import bash_or_skip
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins/playbook"
HOOK = PLUGIN / "scripts" / "task-gate-hook"
HELPER = PLUGIN / "scripts" / "gate-batch-check.py"

SESSION = "pid-batch-test"

G = ["- [ ] G1: run the suite",
     "- [ ] G2: update the mind map",
     "- [ ] G3: write the journal",
     "- [ ] G4: commit the work",
     "- [ ] G5: check the docs",
     "- [ ] G6: close the loop"]


def checked(line: str, note: str = "") -> str:
    return line.replace("- [ ]", "- [x]", 1) + note


class ProjectFixture:
    def __init__(self, gates: "list[str]" = G):
        self.proj = Path(tempfile.mkdtemp()) / "proj"
        task_dir = self.proj / ".agent" / "tasks" / "001-thing"
        task_dir.mkdir(parents=True)
        self.task_file = task_dir / "task.md"
        self.task_file.write_text(
            "# 001 - Thing\n\n## Status\npending\n\n## Work Plan\n"
            + "\n".join(gates) + "\n", encoding="utf-8")
        sess = self.proj / ".agent" / "sessions" / SESSION
        sess.mkdir(parents=True)
        (sess / "current_state").write_text("001\n", encoding="utf-8")
        self.session_dir = sess

    def set_tools_counter(self, n: int):
        (self.session_dir / "counters").write_text(
            f"tools={n}\nwrites=0\n", encoding="utf-8")

    def run_hook(self, payload: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ, PLAYBOOK_SESSION_ID=SESSION)
        env.pop("PLAYBOOK_ROLE", None)
        return subprocess.run([bash_or_skip(), str(HOOK)],
                              input=json.dumps(payload).encode(),
                              cwd=self.proj, env=env, capture_output=True,
                              timeout=60)

    def edit_payload(self, old: "list[str]", new: "list[str]",
                     replace_all: bool = False) -> dict:
        return {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                "tool_input": {"file_path": str(self.task_file),
                               "old_string": "\n".join(old),
                               "new_string": "\n".join(new),
                               "replace_all": replace_all}}

    def write_payload(self, content: str) -> dict:
        return {"hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": str(self.task_file),
                               "content": content}}


NOTE = " — 283 tests green, receipts stamped"     # comfortably above floor
PTR = " — see Round 2 Result"                      # the pointer idiom


class BatchAllowances(unittest.TestCase):
    def test_annotated_3_batch_allowed(self):
        f = ProjectFixture()
        p = f.edit_payload(G[:3], [checked(g, NOTE) for g in G[:3]])
        r = f.run_hook(p)
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_pointer_idiom_accepted(self):
        f = ProjectFixture()
        p = f.edit_payload(G[:2], [checked(g, PTR) for g in G[:2]])
        r = f.run_hook(p)
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_judge_accept_case_arrow_283_green(self):
        f = ProjectFixture()
        p = f.edit_payload(G[:2], [checked(g, " → 283 green") for g in G[:2]])
        r = f.run_hook(p)
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_single_bare_close_stays_silent(self):
        f = ProjectFixture()
        p = f.edit_payload(G[:1], [checked(G[0])])
        r = f.run_hook(p)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(r.stderr.decode().strip(), "")

    def test_carried_checked_lines_are_not_counted(self):
        # Judge Finding 1 regression: an edit spanning previously-closed gates
        # must not read them as born-checked.
        f = ProjectFixture(gates=[checked(G[0], NOTE), *G[1:3]])
        old = [checked(G[0], NOTE), G[1]]
        new = [checked(G[0], NOTE), checked(G[1], NOTE)]
        r = f.run_hook(f.edit_payload(old, new))
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_duplicate_gate_text_multiset_pairing(self):
        dup = "- [ ] Checkpoint: converging or scattering?"
        f = ProjectFixture(gates=[dup, dup, G[0]])
        old = [dup, dup]
        new = [checked(dup, NOTE), checked(dup, PTR)]
        r = f.run_hook(f.edit_payload(old, new))
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_annotation_only_edit_never_counted(self):
        f = ProjectFixture(gates=[checked(G[0]), *G[1:]])
        old = [checked(G[0])]
        new = [checked(G[0], NOTE)]
        r = f.run_hook(f.edit_payload(old, new))
        self.assertEqual(r.returncode, 0, r.stderr.decode())


class Cp1252StdoutDoesNotFailOpen(unittest.TestCase):
    """Linux robustness — a non-UTF-8 stdout must not fail open (found on the
    Windows lane, CI run 32454916957): gate-batch-check printed its block message
    to a cp1252 stdout, and BARE_MSG carries "→" (U+2192, absent from cp1252). The
    UnicodeEncodeError was swallowed as exit 1, which the hook treats as ALLOW — so
    every BARE batch close slipped through while annotated/born-checked ones
    (message encodable as cp1252) blocked. Forced with PYTHONIOENCODING=cp1252;
    red against the pre-fix helper, which exits 1 here instead of 2.
    """

    def _run(self, payload: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        return subprocess.run(
            [sys.executable, str(HELPER), "--tool", "Edit",
             "--file", "/x/.agent/tasks/001-t/task.md", "--session-dir", "/nope"],
            input=json.dumps(payload).encode(), capture_output=True, env=env,
            timeout=60)

    def test_bare_batch_blocks_even_when_stdout_is_cp1252(self):
        payload = {"tool_name": "Edit", "tool_input": {
            "file_path": "/x/.agent/tasks/001-t/task.md",
            "old_string": "\n".join(G[:2]),
            "new_string": "\n".join(checked(g) for g in G[:2])}}
        r = self._run(payload)
        self.assertEqual(r.returncode, 2,
                         "bare batch fail-opened on a cp1252 stdout")
        # The message that carries the arrow must have been emitted (as UTF-8).
        self.assertIn("outcome note", r.stdout.decode("utf-8"))


class BatchBlocks(unittest.TestCase):
    def test_bare_3_batch_blocked(self):
        # THE negative control: the fabricated bare batch stays forbidden.
        f = ProjectFixture()
        r = f.run_hook(f.edit_payload(G[:3], [checked(g) for g in G[:3]]))
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        self.assertIn(b"outcome note", r.stderr)

    def test_bare_2_batch_blocked_tightened_from_warn(self):
        f = ProjectFixture()
        r = f.run_hook(f.edit_payload(G[:2], [checked(g) for g in G[:2]]))
        self.assertEqual(r.returncode, 2,
                         "v1 only warned at 2 bare closes; v2 must block")

    def test_subfloor_notes_blocked(self):
        # Judge reject case: "— done" is 5 non-ws chars, under the floor of 8.
        f = ProjectFixture()
        r = f.run_hook(f.edit_payload(
            G[:3], [checked(g, " — done") for g in G[:3]]))
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_annotated_6_batch_blocked_by_ceiling(self):
        f = ProjectFixture()
        r = f.run_hook(f.edit_payload(
            G[:6], [checked(g, NOTE) for g in G[:6]]))
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        self.assertIn(b"smaller", r.stderr)

    def test_born_checked_line_blocks_the_batch(self):
        f = ProjectFixture()
        new = [checked(G[0], NOTE),
               "- [x] G-new: minted already closed — with a long note"]
        r = f.run_hook(f.edit_payload(G[:1], new))
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        self.assertIn(b"born-checked", r.stderr.lower())

    def test_born_checked_block_teaches_the_rewrite_case(self):
        # Batch-5 field finding (task 011 journal): the agent's first batches
        # were born-checked-blocked because it REWROTE the gate text while
        # checking, then had to infer the cause. The block message must name
        # the likely cause and the fix (restore original text, APPEND).
        f = ProjectFixture()
        rewritten = ["- [x] G1: suite ran and everything is green now",
                     "- [x] G2: mind map refreshed in place, node 10"]
        r = f.run_hook(f.edit_payload(G[:2], rewritten))
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        err = r.stderr.decode()
        self.assertIn("rewrote", err.lower())
        self.assertIn("append", err.lower())

    def test_born_checked_block_shows_the_closest_original(self):
        # Batch-6 journal one-change (F20): the agent truncated a parenthetical
        # while appending and "had to eyeball what I'd dropped". The block must
        # SHOW the closest open gate so restoration is copy-paste, not memory.
        f = ProjectFixture()
        truncated = [
            # G1 with its tail dropped, G2 rewritten — both near-misses
            "- [x] G1: run the → 283 green",
            "- [x] G2: mind map refreshed, node 10",
        ]
        r = f.run_hook(f.edit_payload(G[:2], truncated))
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        err = r.stderr.decode()
        self.assertIn("G1: run the suite", err,
                      "the dropped original must be shown for restoration")
        self.assertIn("G2: update the mind map", err)

    def test_genuinely_new_minted_line_gets_no_closest_hint(self):
        # Negative control: a fabricated gate with no near-miss original must
        # not be blamed on some unrelated open gate.
        f = ProjectFixture()
        new = [checked(G[0], NOTE),
               "- [x] Deployed the flux capacitor to production regions"]
        r = f.run_hook(f.edit_payload(G[:1], new))
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        err = r.stderr.decode()
        self.assertNotIn("closest open gate", err.split("flux capacitor")[-1],
                         "no near-miss original exists — no hint may be minted")

    def test_uncheck_cannot_launder_batch_size(self):
        # Uncheck 2 + check 3 bare in one write: raw x-delta is 1, but the
        # newly-checked count is 3 — must block as a bare batch.
        f = ProjectFixture(gates=[checked(G[0]), checked(G[1]), *G[2:5]])
        old = [checked(G[0]), checked(G[1]), *G[2:5]]
        new = [G[0], G[1],
               checked(G[2]), checked(G[3]), checked(G[4])]
        r = f.run_hook(f.edit_payload(old, new))
        self.assertEqual(r.returncode, 2,
                         "unchecking laundered the batch size:\n"
                         + r.stderr.decode())

    def test_replace_all_multiplication_counted(self):
        dup = "- [ ] Checkpoint: same text twice"
        f = ProjectFixture(gates=[dup, dup, *G])
        p = f.edit_payload([dup], [checked(dup)], replace_all=True)
        r = f.run_hook(p)
        self.assertEqual(r.returncode, 2,
                         "replace_all closing 2 occurrences bare must block")


class ConsecutiveBatchGuard(unittest.TestCase):
    def test_second_batch_with_no_intervening_work_blocked(self):
        f = ProjectFixture()
        f.set_tools_counter(10)
        r = f.run_hook(f.edit_payload(G[:2], [checked(g, NOTE) for g in G[:2]]))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        # The edit itself bumps tools by 1 (PostToolUse); nothing else ran.
        f.set_tools_counter(11)
        r = f.run_hook(f.edit_payload(G[2:4], [checked(g, NOTE) for g in G[2:4]]))
        self.assertEqual(r.returncode, 2,
                         "back-to-back batch closes with no work between "
                         "must block:\n" + r.stderr.decode())
        self.assertIn(b"no ", r.stderr.lower())

    def test_second_batch_after_real_work_allowed(self):
        f = ProjectFixture()
        f.set_tools_counter(10)
        r = f.run_hook(f.edit_payload(G[:2], [checked(g, NOTE) for g in G[:2]]))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        f.set_tools_counter(14)  # the edit + three real tool calls since
        r = f.run_hook(f.edit_payload(G[2:4], [checked(g, NOTE) for g in G[2:4]]))
        self.assertEqual(r.returncode, 0, r.stderr.decode())


class WritePathAndScope(unittest.TestCase):
    def test_write_tool_annotated_batch_allowed_bare_blocked(self):
        f = ProjectFixture()
        base = f.task_file.read_text(encoding="utf-8")
        annotated = base
        for g in G[:3]:
            annotated = annotated.replace(g, checked(g, NOTE))
        r = f.run_hook(f.write_payload(annotated))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        bare = base
        for g in G[:3]:
            bare = bare.replace(g, checked(g))
        r = f.run_hook(f.write_payload(bare))
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_other_files_not_guarded(self):
        f = ProjectFixture()
        p = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": str(f.proj / "notes.md"),
                            "old_string": "- [ ] a\n- [ ] b\n- [ ] c",
                            "new_string": "- [x] a\n- [x] b\n- [x] c"}}
        r = f.run_hook(p)
        self.assertEqual(r.returncode, 0, r.stderr.decode())


class AnotherSpellingOfThePathIsStillGuarded(unittest.TestCase):
    """Task 158 (found by task 156's impl panel, opus): Guard 0 and the batch-close
    guard matched the RAW `file_path`. `…/001-thing/./task.md`, a doubled slash or an
    `x/..` hop name the same file while matching neither pattern — and Guard 1 then
    exempted the edit as an `.agent/` path. The guards now judge the path the hook
    already normalises (lexically, like Guard 1)."""

    SPELLINGS = {
        "dot segment": lambda p, d: p.replace(f"/{d}/", f"/{d}/./"),
        "doubled slash": lambda p, d: p.replace("/tasks/", "/tasks//"),
        "hop and back": lambda p, d: p.replace(f"/{d}/", f"/{d}/x/../"),
        "dot before tasks": lambda p, d: p.replace("/.agent/", "/.agent/./"),
    }

    def test_a_bare_batch_is_blocked_under_every_spelling(self):
        for name, spell in self.SPELLINGS.items():
            with self.subTest(spelling=name):
                f = ProjectFixture()
                payload = f.edit_payload(G[:3], [checked(g) for g in G[:3]])
                payload["tool_input"]["file_path"] = spell(str(f.task_file), "001-thing")
                self.assertNotEqual(payload["tool_input"]["file_path"], str(f.task_file))
                r = f.run_hook(payload)
                self.assertEqual(r.returncode, 2, r.stderr.decode())
                self.assertIn(b"outcome note", r.stderr)

    def test_control_an_annotated_batch_is_still_allowed_under_them(self):
        for name, spell in self.SPELLINGS.items():
            with self.subTest(spelling=name):
                f = ProjectFixture()
                payload = f.edit_payload(G[:3], [checked(g, NOTE) for g in G[:3]])
                payload["tool_input"]["file_path"] = spell(str(f.task_file), "001-thing")
                r = f.run_hook(payload)
                self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_creating_a_task_md_by_hand_is_blocked_under_every_spelling(self):
        # Guard 0: only `tasks new` creates a task.md. The plain spelling is the
        # control — it was blocked before this task too.
        spellings = dict(self.SPELLINGS, plain=lambda p, d: p)
        for name, spell in spellings.items():
            with self.subTest(spelling=name):
                f = ProjectFixture()
                new = str(f.proj / ".agent" / "tasks" / "002-by-hand" / "task.md")
                payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                           "tool_input": {"file_path": spell(new, "002-by-hand"),
                                          "content": "# 002 - by hand\n"}}
                r = f.run_hook(payload)
                self.assertEqual(r.returncode, 2, r.stderr.decode())
                self.assertIn(b"creates task.md files", r.stderr)
                self.assertFalse(Path(new).exists())

    def test_control_rewriting_an_existing_task_md_is_not_a_creation(self):
        for name, spell in self.SPELLINGS.items():
            with self.subTest(spelling=name):
                f = ProjectFixture()
                payload = f.write_payload(f.task_file.read_text(encoding="utf-8"))
                payload["tool_input"]["file_path"] = spell(str(f.task_file), "001-thing")
                r = f.run_hook(payload)
                self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_the_recovery_path_judges_the_same_normalised_path(self):
        # The hook reads its fields in one fused call and, when that yields no
        # sentinel, extracts them one by one. Both branches must hand the guards
        # the same normalised path: here the fused call answers nothing, in a
        # COPY of scripts/ (the repo is not touched).
        import shutil
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        scripts = tmp / "scripts"
        shutil.copytree(PLUGIN / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__"))
        norm = scripts / "hook-payload-normalize.py"
        src = norm.read_text(encoding="utf-8")
        marker = 'if __name__ == "__main__":'
        self.assertEqual(src.count(marker), 1)
        norm.write_text(src.replace(
            marker, 'if "--emit-fields" in sys.argv[1:]:\n    sys.exit(0)\n' + marker), encoding="utf-8")
        probe = subprocess.run([sys.executable, str(norm), "--emit-fields"], input=b"{}",
                               capture_output=True, timeout=60)
        self.assertEqual(probe.stdout, b"", "control: the copied normaliser still emits fields")
        f = ProjectFixture()
        env = dict(os.environ, PLAYBOOK_SESSION_ID=SESSION)
        env.pop("PLAYBOOK_ROLE", None)
        for note, want in (("", 2), (NOTE, 0)):
            with self.subTest(annotated=bool(note)):
                payload = f.edit_payload(G[:3], [checked(g, note) for g in G[:3]])
                payload["tool_input"]["file_path"] = str(f.task_file).replace("/001-thing/", "/001-thing/./")
                r = subprocess.run([bash_or_skip(), str(scripts / "task-gate-hook")],
                                   input=json.dumps(payload).encode(), cwd=f.proj, env=env,
                                   capture_output=True, timeout=60)
                self.assertEqual(r.returncode, want, r.stderr.decode())


class MultiEditIsJudgedLikeAWrite(unittest.TestCase):
    """PLAN S11 item 6 (task 166; from task 158). The batch-close guard looked at `Edit` and
    `Write` only: three gates ticked with no outcome note in ONE `MultiEdit` went through,
    where the same ticks as one `Edit` are refused. The owner chose to cover the tool
    although the Claude Code he runs offers none ("Acopăr totuși, în S11")."""

    @staticmethod
    def _multi(f, pairs, path=None):
        return {"hook_event_name": "PreToolUse", "tool_name": "MultiEdit",
                "tool_input": {"file_path": path or str(f.task_file),
                               "edits": [{"old_string": o, "new_string": n} for o, n in pairs]}}

    def test_three_bare_ticks_are_blocked(self):
        f = ProjectFixture()
        r = f.run_hook(self._multi(f, [(g, checked(g)) for g in G[:3]]))
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        self.assertIn(b"outcome note", r.stderr)

    def test_control_three_annotated_ticks_are_allowed(self):
        f = ProjectFixture()
        r = f.run_hook(self._multi(f, [(g, checked(g, NOTE)) for g in G[:3]]))
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_control_one_tick_is_not_a_batch(self):
        f = ProjectFixture()
        r = f.run_hook(self._multi(f, [(G[0], checked(G[0]))]))
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_six_ticks_are_over_the_ceiling_even_annotated(self):
        f = ProjectFixture()
        r = f.run_hook(self._multi(f, [(g, checked(g, NOTE)) for g in G]))
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_the_edits_are_applied_in_order_to_the_file_as_it_is(self):
        # the second edit ticks the text the FIRST edit wrote: judged on the file before and
        # after the whole call, that gate was rewritten while it was closed (born checked)
        f = ProjectFixture()
        rewritten = "- [ ] G1: run the whole suite twice"
        r = f.run_hook(self._multi(f, [(G[0], rewritten),
                                       (rewritten, checked(rewritten, NOTE)),
                                       (G[1], checked(G[1], NOTE))]))
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_another_spelling_of_the_path_is_still_guarded(self):
        f = ProjectFixture()
        path = str(f.task_file).replace("/001-thing/", "/001-thing/./")
        r = f.run_hook(self._multi(f, [(g, checked(g)) for g in G[:3]], path=path))
        self.assertEqual(r.returncode, 2, r.stderr.decode())

    def test_a_payload_it_cannot_read_fails_open(self):
        # the helper's contract: its own errors never block an edit
        f = ProjectFixture()
        payload = self._multi(f, [])
        payload["tool_input"]["edits"] = "not a list"
        self.assertEqual(f.run_hook(payload).returncode, 0)


class GuardZeroIsAStatedGuarantee(unittest.TestCase):
    """PLAN S11 item 5 (task 166; from task 158). Guard 0 — only `tasks new` creates a
    task.md — was enforced and tested, and stated nowhere in the guarantee ledger."""

    def test_one_ledger_row_rests_on_guard_zeros_tests(self):
        ledger = json.loads((_HERE.parent / "docs" / "guarantee-ledger.json").read_text(encoding="utf-8"))
        cite = "AnotherSpellingOfThePathIsStillGuarded.test_creating_a_task_md_by_hand_is_blocked_under_every_spelling"
        rows = [g for g in ledger["guarantees"]
                if any(p.get("reference") == cite for p in g.get("proofs", []))]
        self.assertEqual([g["id"] for g in rows], ["PB-TASK-MD-GUARD"])
        row = rows[0]
        self.assertEqual(row["status"], "verified_by_current_executable_evidence")
        proof = next(p for p in row["proofs"] if p.get("reference") == cite)
        self.assertEqual(proof["negative_control"]["reference"],
                         "AnotherSpellingOfThePathIsStillGuarded."
                         "test_control_rewriting_an_existing_task_md_is_not_a_creation")
        self.assertEqual(row["required_live_evidence"], [], "the row joined the live spine")
        # task 171: the guard has a project scope now, and the row cites its proof
        cited = {p.get("reference") for p in row["proofs"]}
        self.assertIn("GuardZeroIsAStatedGuarantee.test_a_task_md_of_another_project_is_not_this_guards", cited)
        self.assertIn("this project", row["statement"])

    # Impl panel round 1 (opus): the row says `.agent[/<lane>]/tasks/…` and its proof made a
    # task.md under `.agent/tasks/` only — the lane arm of the hook's pattern had no test.
    def _create(self, f, *parts):
        new = f.proj.joinpath(".agent", *parts, "task.md")
        r = f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Write",
                        "tool_input": {"file_path": str(new), "content": "# by hand\n"}})
        return r, new

    def test_a_task_md_in_a_lane_is_guarded_too(self):
        f = ProjectFixture()
        r, new = self._create(f, "alice", "tasks", "002-by-hand")
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        self.assertIn(b"creates task.md files", r.stderr)
        self.assertFalse(new.exists())

    def test_control_the_pattern_is_one_lane_deep(self):
        # `.agent/<a>/<b>/tasks/…` is not a task directory of any layout: not this guard's
        f = ProjectFixture()
        r, _new = self._create(f, "alice", "deeper", "tasks", "002-by-hand")
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_the_row_cites_the_lane_proof(self):
        ledger = json.loads((_HERE.parent / "docs" / "guarantee-ledger.json").read_text(encoding="utf-8"))
        row = next(g for g in ledger["guarantees"] if g["id"] == "PB-TASK-MD-GUARD")
        cited = {p["reference"]: (p.get("negative_control") or {}).get("reference") for p in row["proofs"]}
        self.assertEqual(cited.get("GuardZeroIsAStatedGuarantee.test_a_task_md_in_a_lane_is_guarded_too"),
                         "GuardZeroIsAStatedGuarantee.test_control_the_pattern_is_one_lane_deep")

    # Task 171 (PLAN S11; retro 107 R6, from task 080): the guard had no project scope —
    # a Write creating `<elsewhere>/.agent/tasks/003-x/task.md` was refused with "only
    # `tasks new` creates task.md files", and `tasks new` cannot create a task in another
    # project. The row stated it as a bound; it is a guarantee now.
    def _write_new(self, f, path, cwd=None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": str(path), "content": "# 003\n"}}
        if cwd is None:
            return f.run_hook(payload)
        env = dict(os.environ, PLAYBOOK_SESSION_ID=SESSION)
        env.pop("PLAYBOOK_ROLE", None)
        return subprocess.run([bash_or_skip(), str(HOOK)], input=json.dumps(payload).encode(),
                              cwd=cwd, env=env, capture_output=True, timeout=60)

    def test_a_task_md_of_another_project_is_not_this_guards(self):
        f = ProjectFixture()
        elsewhere = Path(tempfile.mkdtemp()).resolve()
        for parts in ((".agent", "tasks", "003-elsewhere"), (".agent", "alice", "tasks", "003-elsewhere")):
            with self.subTest(parts="/".join(parts)):
                r = self._write_new(f, elsewhere.joinpath(*parts, "task.md"))
                self.assertEqual(r.returncode, 0, r.stderr.decode())
                self.assertNotIn(b"creates task.md files", r.stderr)

    def test_a_path_that_reaches_this_project_another_way_is_still_refused(self):
        # the inside test is the task-directory guard's: lexical OR physical, and a path
        # it cannot place (relative, `~`) counts as inside. (Not here, because it was never
        # guarded: a path that reaches `.agent` through a link NOT called `.agent` — the
        # guard's pattern reads the path's text. Parked in task 171.)
        f = ProjectFixture()
        outside = Path(tempfile.mkdtemp()).resolve()
        os.symlink(f.proj, outside / "a-link-to-the-project")
        os.makedirs(f.proj / "src")
        os.symlink(f.proj / "src", outside / "a-link-into-it")
        shapes = {
            "through a link to the project": outside / "a-link-to-the-project" / ".agent" / "tasks" / "004-x" / "task.md",
            # impl panel round 1 (opus): collapsed as TEXT this is `<outside>/.agent/…`; the
            # kernel follows the link first and lands in this project. The guard handed its
            # helper the collapsed path, so the helper never saw the path as written.
            "up and out of a link into it": Path(str(outside / "a-link-into-it") + "/../.agent/tasks/004-x/task.md"),
            "relative": Path(".agent/tasks/004-x/task.md"),
            "home-relative": Path("~/.agent/tasks/004-x/task.md"),
        }
        for name, path in shapes.items():
            with self.subTest(shape=name):
                r = self._write_new(f, path)
                self.assertEqual(r.returncode, 2, name + ": " + r.stderr.decode())
                self.assertIn(b"creates task.md files", r.stderr)

    def test_what_exists_is_asked_of_the_path_as_written_too(self):
        # impl panel round 2 (grok): the guard asked "does it exist already?" of the
        # COLLAPSED path and "is it inside?" of the path as written. With a file planted at
        # the collapsed path, a creation inside this project passed as a rewrite.
        f = ProjectFixture()
        outside = Path(tempfile.mkdtemp()).resolve()
        os.makedirs(f.proj / "src")
        os.symlink(f.proj / "src", outside / "a-link-into-it")
        written = str(outside / "a-link-into-it") + "/../.agent/tasks/004-x/task.md"
        decoy = outside / ".agent" / "tasks" / "004-x" / "task.md"          # where the TEXT collapses to
        os.makedirs(decoy.parent)
        decoy.write_text("# a decoy\n", encoding="utf-8")
        r = self._write_new(f, written)
        self.assertEqual(r.returncode, 2, "a creation in this project passed as a rewrite: " + r.stderr.decode())
        self.assertIn(b"creates task.md files", r.stderr)
        # the other side: the project's file exists (only the kernel's reading finds it) —
        # writing over it is a rewrite, not a creation
        shutil.rmtree(outside / ".agent")
        real = f.proj / ".agent" / "tasks" / "004-x"
        os.makedirs(real)
        (real / "task.md").write_text("# 004\n", encoding="utf-8")
        r = self._write_new(f, written)
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    BORN_CHECKED = "# 001 - theirs\n\n## Work Plan\n- [x] one\n- [x] two\n- [x] three\n"

    def _write(self, f, path, content):
        return f.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Write",
                           "tool_input": {"file_path": str(path), "content": content}})

    def test_the_batch_close_guard_is_about_this_projects_records_too(self):
        # impl panel round 2 (opus): one guard further down, the Write this task released
        # was refused again — a new task.md of ANOTHER project, born with ticked gates,
        # whose directory carries this project's active task number (001)
        f = ProjectFixture()
        elsewhere = Path(tempfile.mkdtemp()).resolve()
        r = self._write(f, elsewhere / ".agent" / "tasks" / "001-theirs" / "task.md", self.BORN_CHECKED)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertNotIn(b"born-checked", r.stderr)

    def test_control_the_batch_close_guard_still_holds_for_this_projects_active_task(self):
        f = ProjectFixture()
        r = self._write(f, f.task_file, f.task_file.read_text(encoding="utf-8") + "- [x] born one\n- [x] born two\n")
        self.assertEqual(r.returncode, 2, r.stderr.decode())
        self.assertIn(b"born-checked", r.stderr)

    def test_without_its_helper_the_guard_keeps_refusing(self):
        # "could not tell" is not "outside": a hook whose helper is missing or broken
        # refuses the creation, as it did before it had a scope at all
        f = ProjectFixture()
        elsewhere = Path(tempfile.mkdtemp()).resolve()
        target = elsewhere / ".agent" / "tasks" / "003-elsewhere" / "task.md"
        for how in ("missing", "broken"):
            with self.subTest(helper=how):
                scripts = Path(tempfile.mkdtemp()) / "scripts"
                shutil.copytree(HOOK.parent, scripts)
                helper = scripts / "task-dir-target.py"
                if how == "missing":
                    helper.unlink()
                else:
                    helper.write_text("raise SystemExit('broken on purpose')\n", encoding="utf-8")
                payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                           "tool_input": {"file_path": str(target), "content": "# 003\n"}}
                env = dict(os.environ, PLAYBOOK_SESSION_ID=SESSION)
                env.pop("PLAYBOOK_ROLE", None)
                r = subprocess.run([bash_or_skip(), str(scripts / "task-gate-hook")],
                                   input=json.dumps(payload).encode(), cwd=f.proj, env=env,
                                   capture_output=True, timeout=60)
                self.assertEqual(r.returncode, 2, r.stderr.decode())
                self.assertIn(b"creates task.md files", r.stderr)


if __name__ == "__main__":
    unittest.main()
