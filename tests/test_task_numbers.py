#!/usr/bin/env python3
"""A task's own numbers, composed at its close from the plugin's records only (PHASE-B item 6, last part — task 235).

The owner, 2026-10-13 18:44, on item 6: "În Faza B (Recomandat)". Every number item 8 needs came from the host's session
transcripts, deleted after about 30 days; tasks 232-234 made the plugin record the facts, and `tasks/numbers.py`
composes them per closure: the time of the task by kind and by `##` section without the laptop's sleep, the review
rounds and what they found, the seats' runs and tokens, the verify runs, the owner's prompts and dialogs with their wait,
and how the task closed.

The reading layer first: the closure's window, the hook journal with its archives and spools, the task's records, the
DIALOG entries and the receipt. Pure stdlib unittest. Run: python3 -m unittest tests.test_task_numbers
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import numbers  # noqa: E402

T0 = 1792260000             # an epoch second inside 2026-10-17


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def hl(t, event, hook="state-echo", **kw):
    """One hook-journal line, as the writers make it (task 222 + 234)."""
    rec = {"ts": iso(t), "hook": hook, "event": event, "tool": kw.pop("tool", ""), "tool_use_id": kw.pop("tid", ""),
           "session_id": kw.pop("sid", "pid-1"), "host_session_id": kw.pop("host", "h-1"), "agent_id": "",
           "decision": kw.pop("decision", "ok"), "detail": kw.pop("detail", ""), "ms": 1, "exit": 0, "error": "",
           "task": kw.pop("task", "007"), "section": kw.pop("section", None), "susp": kw.pop("susp", None),
           "sts": kw.pop("sts", None), "boot": kw.pop("boot", None)}
    assert not kw, kw
    return rec


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb235-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.jdir = self.d / ".agent" / "journal"
        self.jdir.mkdir(parents=True)

    def write(self, name, recs):
        p = self.jdir / name
        p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
        return p


class TheWindow(unittest.TestCase):
    def rec(self, t, reason, task="007"):
        return {"ts": iso(t), "hook": "tasks", "decision": "record", "reason": reason, "task": task}

    def test_from_the_latest_activation_to_the_close(self):
        recs = [self.rec(T0, "work"), self.rec(T0 + 50, "work", "008"), self.rec(T0 + 100, "work")]
        self.assertEqual(numbers.closure_window(recs, "007", T0 + 500), (T0, T0 + 500))

    def test_a_reopened_task_starts_after_its_previous_done(self):
        """sonnet, grok (plan panel): the first life is the first closure's, not this one's."""
        recs = [self.rec(T0, "work"), self.rec(T0 + 100, "done"), self.rec(T0 + 9000, "work"),
                self.rec(T0 + 9100, "work")]
        self.assertEqual(numbers.closure_window(recs, "007", T0 + 9500), (T0 + 9000, T0 + 9500))

    def test_this_closes_own_done_in_the_receipts_second_is_ignored(self):
        """A real close writes its `done` record in the same second as its receipt's stamp (seen on the first real
        close in a scratch project): the done after the last activation is this closure's, not a previous one."""
        recs = [self.rec(T0, "work"), self.rec(T0 + 100, "done")]
        self.assertEqual(numbers.closure_window(recs, "007", T0 + 100), (T0, T0 + 100))
        recs = [self.rec(T0, "work"), self.rec(T0, "done"), self.rec(T0, "work"), self.rec(T0, "done")]
        self.assertEqual(numbers.closure_window(recs, "007", T0), (T0, T0))

    def test_no_activation_record_no_window(self):
        self.assertIsNone(numbers.closure_window([self.rec(T0, "work", "008")], "007", T0 + 10))

    def test_records_after_the_close_do_not_move_it(self):
        recs = [self.rec(T0, "work"), self.rec(T0 + 200, "work")]
        self.assertEqual(numbers.closure_window(recs, "007", T0 + 100), (T0, T0 + 100))


class TheHookJournal(_Tmp):
    def test_the_file_its_archives_and_spools_sorted_without_duplicates(self):
        a = hl(T0 + 1, "PreToolUse", tid="t1")
        b = hl(T0 + 2, "PostToolUse", tid="t1")
        c = hl(T0 + 3, "Stop", hook="stop", decision="allow")
        self.write("hooks.jsonl", [c, b])
        arch = self.jdir / f"hooks.jsonl.archived-20261017-100000-{T0 + 2}-77"
        arch.write_text(json.dumps(a) + "\n" + json.dumps(b) + "\n")            # b again: the duplicate
        (self.jdir / f"hooks.jsonl.spool-20261017-100003-5-1").write_text(json.dumps(c | {"detail": "x"}) + "\n")
        got = numbers.read_hook_lines(self.jdir, T0, time.monotonic() + 30)
        self.assertEqual([(r["event"], r["detail"]) for r in got],
                         [("PreToolUse", ""), ("PostToolUse", ""), ("Stop", ""), ("Stop", "x")])
        self.assertEqual(got[0]["_t"], T0 + 1)

    def test_a_compressed_archive_is_read_and_an_older_one_skipped(self):
        new = self.jdir / f"hooks.jsonl.archived-20261017-100000-{T0 + 10}-1.gz"
        with gzip.open(new, "wt") as f:
            f.write(json.dumps(hl(T0 + 5, "PostToolUse")) + "\n")
        # renamed before the window's margin: never opened (its line, impossible in a real archive, would be read)
        old = self.jdir / f"hooks.jsonl.archived-20261016-100000-{T0 - 7200}-2"
        old.write_text(json.dumps(hl(T0 + 6, "PostToolUse")) + "\n")
        got = numbers.read_hook_lines(self.jdir, T0, time.monotonic() + 30)
        self.assertEqual([r["_t"] for r in got], [T0 + 5])

    def test_within_one_second_the_file_order_is_kept(self):
        recs = [hl(T0, "UserPromptSubmit", hook="chat-log"), hl(T0, "PreToolUse", tid="t"), hl(T0, "PostToolUse", tid="t")]
        self.write("hooks.jsonl", recs)
        got = numbers.read_hook_lines(self.jdir, T0, time.monotonic() + 30)
        self.assertEqual([r["event"] for r in got], ["UserPromptSubmit", "PreToolUse", "PostToolUse"])

    def test_bad_lines_links_and_fifos_are_skipped(self):
        p = self.write("hooks.jsonl", [hl(T0, "PostToolUse")])
        with open(p, "a") as f:
            f.write("not json\n[1]\n{\"ts\": 5}\n")
        target = self.d / "elsewhere"
        target.write_text(json.dumps(hl(T0 + 1, "PostToolUse")) + "\n")
        (self.jdir / f"hooks.jsonl.archived-20261017-100000-{T0 + 1}-3").symlink_to(target)
        os.mkfifo(self.jdir / "hooks.jsonl.spool-20261017-100003-6-1")
        t = time.monotonic()
        got = numbers.read_hook_lines(self.jdir, T0, time.monotonic() + 30)
        self.assertLess(time.monotonic() - t, 5)
        self.assertEqual(len(got), 1)

    def test_a_linked_journal_directory_is_not_read(self):
        """The writers refuse a linked `journal/` (task 222); the reader too."""
        real = self.d / "real-journal"
        real.mkdir()
        (real / "hooks.jsonl").write_text(json.dumps(hl(T0, "PostToolUse")) + "\n")
        link = self.d / "linked"
        link.symlink_to(real)
        self.assertEqual(numbers.read_hook_lines(link, T0, time.monotonic() + 30), [])

    def test_past_the_deadline_it_stops(self):
        self.write("hooks.jsonl", [hl(T0, "PostToolUse")])
        with self.assertRaises(numbers.OutOfTime):
            numbers.read_hook_lines(self.jdir, T0, time.monotonic() - 1)


class TheTaskRecords(_Tmp):
    def test_the_tasks_review_verify_tasks_and_close_records(self):
        recs = [{"ts": iso(T0 + 1), "hook": "review", "task": "007", "seat": "a", "duration_ms": 5000},
                {"ts": iso(T0 + 2), "hook": "review", "task": "008", "seat": "a", "duration_ms": 5000},
                {"ts": iso(T0 + 3), "hook": "verify", "task": "007", "rc": 0, "how": "ran", "seconds": 2.5},
                {"ts": iso(T0 + 4), "hook": "close", "task": "007", "reason": "verify contract"},
                {"ts": iso(T0 + 5), "hook": "close", "reason": "verify contract"},
                {"ts": iso(T0 + 6), "hook": "task-gate", "task": "007", "decision": "block"},
                {"ts": iso(T0 - 9), "hook": "review", "task": "007", "seat": "old"}]
        self.write("enforcement.jsonl", recs)
        got = numbers.task_records(numbers.read_enforcement(self.d / ".agent", time.monotonic() + 30), "007", T0,
                                   T0 + 100)
        self.assertEqual([r["seat"] for r in got["review"]], ["a"])
        self.assertEqual(len(got["verify"]), 1)
        self.assertEqual(len(got["close"]), 1)                    # the record without a task is not this task's
        self.assertNotIn("task-gate", got)


class TheDialogs(unittest.TestCase):
    LOG = ("---\n\n**[M001]** [2026-10-17 20:00:00 UTC] `DIALOG` (claude/s)\n\nOptions dialog — …\nA1: \"Red\"\n"
           "Notes1: \"warm\"\nA2: \"Square\"\nQ1 (Color): Pick a color\nQ2 (Shape): Pick a shape\n"
           "<!-- dialog:toolu_A -->\n\n---\n\n**[M002]** [2026-10-17 20:01:00 UTC] `DIALOG` (claude/s)\n\n"
           "A1: \"Q2 (x): a fake\"\nQ1 (One): one\n<!-- dialog:toolu_B -->\n")

    def test_questions_and_answers_counted_by_the_dialogs_marker(self):
        self.assertEqual(numbers.dialog_counts(self.LOG, "toolu_A"), {"id": "M001", "questions": 2, "answers": 2})
        self.assertEqual(numbers.dialog_counts(self.LOG, "toolu_B"), {"id": "M002", "questions": 1, "answers": 1})
        self.assertIsNone(numbers.dialog_counts(self.LOG, "toolu_C"))


class TheReceipt(unittest.TestCase):
    TASK = ("# 007 - x\n\n## Status\ndone\n\n## Verification Receipt\n\n"
            "### 2026-10-17T20:00:00+03:00 · risk assertive · commit abc\n"
            "- **Panel tree-state:** a vs close b — STALE, accepted: \"docs only\"\n- **Commands:**\n"
            "    - [PASS] `x` (verify) — ok\n\n"
            "### 2026-10-18T00:34:11+03:00 · risk assertive · commit def (+3 uncommitted file(s) — …)\n"
            "- **Forced close, reason:** the owner said so\n"
            "- **Panel tree-state:** c vs close d — STALE, but TAIL-CERTIFIED: non-behavioral tail (delta: PLAN.md)\n"
            "- **Commands:**\n    - [PASS] `cd p && v` (verify) — ok\n    - [FAIL] `y` (lint) — no\n")

    def test_the_newest_block_is_this_closure(self):
        got = numbers.receipt_close(self.TASK)
        self.assertEqual(got["stamp"], "2026-10-18T00:34:11+03:00")
        self.assertEqual(got["epoch"], 1792272851)
        self.assertEqual((got["risk"], got["freshness"], got["forced_reason"], got["accepted_reason"]),
                         ("assertive", "TAIL-CERTIFIED", "the owner said so", None))
        self.assertEqual(got["commands"], {"pass": 1, "fail": 1})

    def test_the_newest_stamp_wins_wherever_its_block_sits(self):
        """The close inserts its block at the TOP of the receipt (task 106's record holds two, newest first)."""
        blocks = self.TASK.split("### ")
        flipped = blocks[0] + "### " + blocks[2] + "\n### " + blocks[1]
        self.assertEqual(numbers.receipt_close(flipped)["stamp"], "2026-10-18T00:34:11+03:00")

    def test_no_receipt_no_close(self):
        self.assertIsNone(numbers.receipt_close("# 007 - x\n\n## Status\ndone\n"))


# --- the partition (W4) -----------------------------------------------------------------------------------------------

def L(t, event, **kw):
    """A line with `_t`, as read_hook_lines returns it."""
    hook = kw.pop("hook", {"UserPromptSubmit": "chat-log", "Stop": "stop", "SessionStart": "session-start",
                           "SessionEnd": "session-end"}.get(event, "state-echo"))
    rec = hl(T0 + t, event, hook=hook, **kw)
    rec["_t"] = T0 + t
    return rec


def part(lines, end=None, reviews=(), verifies=(), task="007"):
    return numbers.session_partition(lines, task, T0 + (end if end is not None else lines[-1]["_t"] - T0),
                                     [(T0 + a, T0 + b) for a, b in reviews], [(T0 + a, T0 + b) for a, b in verifies])


class ThePartition(unittest.TestCase):
    def test_a_turn_is_agent_and_the_wait_after_it_the_owners(self):
        got = part([L(0, "UserPromptSubmit", detail="logged M001"), L(2, "PreToolUse", tool="Bash", tid="b1"),
                    L(5, "PostToolUse", tool="Bash", tid="b1"), L(7, "Stop", decision="allow"),
                    L(60, "UserPromptSubmit", detail="logged M002")])
        self.assertEqual(got["by_kind"], {"agent": 7, "owner-idle": 53})
        self.assertEqual((got["total"], got["not_task"], got["wall"]), (60, 0, 60))

    def test_a_held_stop_does_not_end_the_turn(self):
        got = part([L(0, "UserPromptSubmit"), L(5, "Stop", decision="block"), L(9, "Stop", decision="allow"),
                    L(20, "UserPromptSubmit")])
        self.assertEqual(got["by_kind"], {"agent": 9, "owner-idle": 11})

    def test_a_notification_wait_is_background_and_judges_inside_a_review_span(self):
        lines = [L(0, "UserPromptSubmit"), L(10, "Stop", decision="allow"),
                 L(100, "UserPromptSubmit", detail="not logged: a notification")]
        self.assertEqual(part(lines)["by_kind"], {"agent": 10, "background": 90})
        self.assertEqual(part(lines, reviews=[(20, 80)])["by_kind"], {"agent": 10, "background": 30, "judges": 60})

    def test_a_polled_panel_is_judges_and_the_agents_own_work_is_not(self):
        lines = [L(0, "UserPromptSubmit"), L(10, "PreToolUse", tool="Bash", tid="p"),
                 L(70, "PostToolUse", tool="Bash", tid="p"), L(90, "PostToolUse", tool="Read", tid="r"),
                 L(95, "Stop", decision="allow")]
        got = part(lines, reviews=[(0, 100)])
        self.assertEqual(got["by_kind"], {"agent": 35, "judges": 60})          # 0-10, 70-90, 90-95 stay agent

    def test_an_open_dialog_is_the_owners_question(self):
        lines = [L(0, "UserPromptSubmit"), L(10, "PreToolUse", tool="AskUserQuestion", tid="q", hook="dialog-log"),
                 L(40, "PostToolUse", tool="AskUserQuestion", tid="q", hook="dialog-log"),
                 L(45, "Stop", decision="allow")]
        self.assertEqual(part(lines)["by_kind"], {"agent": 15, "owner-question": 30})

    def test_sleep_goes_to_the_longest_gap_between_the_samples(self):
        lines = [L(0, "UserPromptSubmit", susp=100, sts=T0, boot="aaaaaaaa"), L(10, "Stop", decision="allow"),
                 L(990, "UserPromptSubmit"), L(1000, "PostToolUse", tid="x", susp=700, sts=T0 + 1000, boot="aaaaaaaa")]
        got = part(lines)
        self.assertEqual((got["slept"], got["by_kind"]), (600, {"agent": 20, "owner-idle": 380}))

    def test_a_one_second_difference_is_rounding_not_sleep(self):
        lines = [L(0, "UserPromptSubmit", susp=100, sts=T0, boot="aaaaaaaa"),
                 L(50, "PostToolUse", tid="x", susp=101, sts=T0 + 50, boot="aaaaaaaa")]
        got = part(lines)
        self.assertEqual((got["slept"], got["by_kind"]), (0, {"agent": 50}))

    def test_sleep_longer_than_a_gap_is_capped_and_carried(self):
        lines = [L(0, "PostToolUse", tid="a", susp=0, sts=T0, boot="aaaaaaaa"), L(300, "PostToolUse", tid="b"),
                 L(500, "PostToolUse", tid="c", susp=450, sts=T0 + 500, boot="aaaaaaaa")]
        got = part([L(-1, "UserPromptSubmit")] + lines)
        self.assertEqual((got["slept"], got["by_kind"]), (450, {"agent": 51}))     # 300 + 150 slept, 1 + 50 awake

    def test_away_is_judged_on_the_awake_time(self):
        """opus, sonnet, agy (plan panel): sleep first, then the 2 h rule."""
        nine_h, eight_h = 9 * 3600, 8 * 3600
        lines = [L(0, "Stop", decision="allow", susp=0, sts=T0, boot="aaaaaaaa"),
                 L(nine_h, "UserPromptSubmit", susp=eight_h, sts=T0 + nine_h, boot="aaaaaaaa")]
        got = part(lines)
        self.assertEqual((got["slept"], got["away"], got["by_kind"]), (eight_h, 0, {"owner-idle": 3600}))
        lines = [L(0, "Stop", decision="allow", susp=0, sts=T0, boot="aaaaaaaa"),
                 L(4 * 3600, "UserPromptSubmit", susp=3600, sts=T0 + 4 * 3600, boot="aaaaaaaa")]
        got = part(lines)
        self.assertEqual((got["slept"], got["away"], got["by_kind"]), (3600, 3 * 3600, {}))

    def test_across_a_boot_change_the_time_is_unknown(self):
        lines = [L(0, "Stop", decision="allow", susp=500, sts=T0, boot="aaaaaaaa"),
                 L(900, "UserPromptSubmit", susp=3, sts=T0 + 900, boot="bbbbbbbb")]
        got = part(lines)
        self.assertEqual((got["unknown"], got["slept"], got["by_kind"]), (900, 0, {}))

    def test_another_tasks_time_and_the_switching_call_are_not_this_tasks(self):
        lines = [L(0, "UserPromptSubmit"), L(10, "PreToolUse", tool="Bash", tid="s"),
                 L(12, "PostToolUse", tool="Bash", tid="s", task="008"), L(50, "PostToolUse", tid="r", task="008"),
                 L(60, "PreToolUse", tool="Bash", tid="s2", task="008"), L(61, "PostToolUse", tool="Bash", tid="s2"),
                 L(70, "Stop", decision="allow")]
        got = part(lines)
        self.assertEqual((got["by_kind"], got["not_task"], got["wall"]), ({"agent": 19}, 51, 70))

    def test_the_command_guards_line_joins_its_call_by_tool_use_id(self):
        lines = [L(0, "UserPromptSubmit"), L(1, "PreToolUse", hook="command-guard", tool="Bash", tid="g", task=None),
                 L(1, "PreToolUse", hook="task-gate", tool="Bash", tid="g"), L(9, "PostToolUse", tool="Bash", tid="g"),
                 L(10, "Stop", decision="allow")]
        self.assertEqual(part(lines)["by_kind"], {"agent": 10})

    def test_an_open_call_at_the_close_ends_there_and_its_verify_is_carved(self):
        lines = [L(0, "UserPromptSubmit"), L(50, "PreToolUse", tool="Bash", tid="close")]
        got = part(lines, end=80, verifies=[(55, 78)])
        self.assertEqual((got["by_kind"], got["wall"]), ({"agent": 57, "verify": 23}, 80))

    def test_the_time_by_section(self):
        lines = [L(0, "UserPromptSubmit"), L(5, "PostToolUse", tid="a", section="Design Phase"),
                 L(30, "PostToolUse", tid="b", section="Work Plan"), L(32, "PostToolUse", tid="c", section=None),
                 L(40, "Stop", decision="allow")]
        got = part(lines)
        self.assertEqual(got["by_section"], {"?": {"agent": 5}, "Design Phase": {"agent": 25},
                                             "Work Plan": {"agent": 10}})   # a null section keeps the last one

    def test_a_session_start_and_end_outside_a_turn_are_the_owners(self):
        lines = [L(0, "SessionStart"), L(13, "UserPromptSubmit"), L(20, "Stop", decision="allow"), L(50, "SessionEnd")]
        self.assertEqual(part(lines)["by_kind"], {"owner-idle": 43, "agent": 7})

    def test_the_parts_always_add_up(self):
        """Q1: per session, the task's parts plus the time that is not the task's equal the session's wall time; none is
        negative — over many built timelines with every kind of line."""
        import random
        rnd = random.Random(235)
        events = ["UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SessionStart",
                  "SessionEnd"]
        for case in range(400):
            t, lines, open_ids = 0, [], []
            boot = "aaaaaaaa"
            susp = 0
            for k in range(rnd.randint(2, 40)):
                t += rnd.choice([0, 1, 3, 30, 600, 5000, 9000])
                ev = rnd.choice(events)
                kw = {"task": rnd.choice(["007", "007", "007", "008", None]),
                      "detail": rnd.choice(["", "not logged: a notification"])}
                if ev == "PreToolUse":
                    tid = f"t{k}"
                    open_ids.append(tid)
                    kw.update(tool=rnd.choice(["Bash", "Edit", "AskUserQuestion"]), tid=tid)
                elif ev.startswith("PostToolUse") and open_ids and rnd.random() < 0.7:
                    kw.update(tid=open_ids.pop(0))
                if ev == "Stop":
                    kw["decision"] = rnd.choice(["allow", "block"])
                if rnd.random() < 0.3:
                    if rnd.random() < 0.05:
                        boot, susp = "bbbbbbbb", 0
                    susp += rnd.choice([0, 0, 1, 50, 4000])
                    kw.update(susp=susp, sts=T0 + t, boot=boot)
                kw["section"] = rnd.choice([None, "Work Plan", "Design Phase"])
                lines.append(L(t, ev, **kw))
            end = t + rnd.choice([0, 0, 20])
            # review and verify spans come from milliseconds and fractional seconds in the records
            got = part(lines, end=end, reviews=[(rnd.randint(0, t + 1) + 0.37, t + 5.61), (1.5, 2.25)],
                       verifies=[(0.75, rnd.randint(0, t + 1) + 0.5)])
            with self.subTest(case=case):
                parts = sum(got["by_kind"].values()) + got["slept"] + got["away"] + got["unknown"]
                self.assertEqual(parts, got["total"])
                self.assertEqual(got["total"] + got["not_task"], got["wall"])
                self.assertTrue(all(v >= 0 for v in list(got["by_kind"].values()) + [got["slept"], got["away"],
                                                                                        got["unknown"], got["not_task"]]))
                self.assertEqual(sum(sum(v.values()) for v in got["by_section"].values()),
                                 sum(got["by_kind"].values()))


# --- the side tables and the composition (W4) -------------------------------------------------------------------------

class TheSideTables(unittest.TestCase):
    def test_seats_with_their_tokens_and_kinds(self):
        recs = [{"hook": "review", "kind": "panel", "seat": "claude:opus", "round": 1, "duration_ms": 60000,
                 "status": "ok", "usage": {"status": "known", "in": 10, "out": 5, "cache_read": 100, "cache_write": 7}},
                {"hook": "review", "kind": "panel", "seat": "claude:opus", "round": 2, "duration_ms": 30500,
                 "status": "failed", "usage": {"status": "unknown"}},
                {"hook": "review", "kind": "tail-cert", "seat": "codex:x", "round": 3, "duration_ms": 1000,
                 "status": "ok", "usage": {"status": "known", "in": 1, "out": 2}}]
        got = numbers.seats_table(recs)
        self.assertEqual(got["claude:opus"], {"runs": 2, "ok": 1, "failed": 1, "seconds": 90.5, "in": 10, "out": 5,
                                              "cache_read": 100, "cache_write": 7, "unknown": 1,
                                              "kinds": {"panel": 2}})
        self.assertEqual(got["codex:x"]["kinds"], {"tail-cert": 1})

    def test_verify_runs(self):
        recs = [{"hook": "verify", "rc": 0, "how": "ran", "seconds": 80.25},
                {"hook": "verify", "rc": 0, "how": "reused", "seconds": 0},
                {"hook": "verify", "rc": 1, "how": "ran", "seconds": 3.5}]
        self.assertEqual(numbers.verify_table(recs), {"runs": 3, "ran": 2, "reused": 1, "failed": 1, "seconds": 83.75})

    def test_rounds_from_the_merged_run_ledger(self):
        runs = [{"id": "p1", "kind": "panel", "mode": "plan", "status": "done", "outcome": "completed",
                 "measures": {"critical": 3, "important": 19}, "ts": "2026-10-17T19:47:13+00:00"},
                {"id": "p2", "kind": "panel", "mode": "impl", "status": "done", "outcome": "void",
                 "ts": "2026-10-17T20:53:47+00:00"},
                {"id": "s1", "panel": "fp", "status": "done", "verdict": "FAIL", "critical": 0, "important": 2,
                 "ts": "2026-10-17T21:00:00+00:00"},
                {"id": "old", "kind": "panel", "mode": "plan", "status": "done", "outcome": "completed",
                 "ts": "2026-10-01T00:00:00+00:00"}]
        start = numbers._epoch("2026-10-17T00:00:00Z")
        got = numbers.rounds_table(runs, [{"kind": "single", "round": 4, "_t": start + 10},
                                          {"kind": "single", "round": 4, "_t": start + 20},
                                          {"kind": "tail-cert", "round": 5, "_t": start + 30}], start, start + 86400)
        self.assertEqual(got["panels"], [{"mode": "plan", "outcome": "completed", "critical": 3, "important": 19,
                                          "owner_ok": None},
                                         {"mode": "impl", "outcome": "void", "critical": None, "important": None,
                                          "owner_ok": None}])
        self.assertEqual(got["post_panel"], [{"verdict": "FAIL", "critical": 0, "important": 2, "owner_ok": None}])
        self.assertEqual((got["single"], got["tail_cert"]), (2, 1))         # each single-judge call is a run (V6)

    def test_the_owners_prompts_and_dialogs_with_their_wait(self):
        lines = [L(0, "UserPromptSubmit", detail="logged M001"), L(5, "UserPromptSubmit", detail="not logged: a notification"),
                 L(10, "PreToolUse", hook="dialog-log", tool="AskUserQuestion", tid="toolu_A"),
                 L(40, "PostToolUse", hook="dialog-log", tool="AskUserQuestion", tid="toolu_A", detail="logged M002"),
                 L(50, "PreToolUse", hook="dialog-log", tool="AskUserQuestion", tid="toolu_X"),       # cancelled
                 L(60, "UserPromptSubmit", detail="logged M003", task="008")]
        got = numbers.owner_table(lines, "007", TheDialogs.LOG.replace("M001", "M002"))
        self.assertEqual(got, {"prompts": 1, "notifications": 1, "dialogs": 1, "cancelled": 1, "questions": 2,
                               "answers": 2, "wait": 30, "ids": ["M001", "M002"]})


class TheComposition(_Tmp):
    def project(self):
        lane = self.d / ".agent"
        td = lane / "tasks" / "007-x"
        td.mkdir(parents=True)
        (td / "task.md").write_text(TheReceipt.TASK.replace("2026-10-18T00:34:11+03:00", "2026-10-17T21:01:40+03:00"),
                                    encoding="utf-8")
        close_t = numbers._epoch("2026-10-17T18:01:40Z")
        self.write("enforcement.jsonl", [
            {"ts": iso(close_t - 100), "hook": "tasks", "reason": "work", "task": "007"},
            {"ts": iso(close_t - 40), "hook": "review", "task": "007", "kind": "panel", "seat": "s", "round": 1,
             "duration_ms": 20000, "status": "ok", "usage": {"status": "known", "in": 1, "out": 1}},
            {"ts": iso(close_t - 2), "hook": "verify", "task": "007", "rc": 0, "how": "ran", "seconds": 5},
            {"ts": iso(close_t + 1), "hook": "close", "task": "007", "reason": "stale close owner-ok",
             "command": "the owner's word"},
            {"ts": iso(close_t + 60), "hook": "review", "task": "007", "kind": "panel", "seat": "late", "round": 2,
             "duration_ms": 1000}])
        self.write("hooks.jsonl", [
            hl(close_t - 99, "UserPromptSubmit", hook="chat-log", detail="logged M001"),
            hl(close_t - 98, "PostToolUse", section="Work Plan", susp=10, sts=close_t - 98, boot="aaaaaaaa"),
            hl(close_t - 70, "Stop", hook="stop", decision="allow"),
            hl(close_t - 15, "UserPromptSubmit", hook="chat-log", detail="logged M002"),
            hl(close_t - 10, "PreToolUse", tool="Bash", tid="close", hook="task-gate")])
        return lane, td, close_t

    def test_one_closure_from_the_records(self):
        lane, td, close_t = self.project()
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual(got["closed_at"], "2026-10-17T21:01:40+03:00")
        self.assertEqual(got["window"], {"start": iso(close_t - 100), "end": iso(close_t)})
        t = got["time"]
        self.assertEqual(t["sessions"], 1)
        self.assertEqual(t["by_kind"], {"agent": 39, "owner-idle": 55, "verify": 5})
        self.assertEqual(t["by_section"]["Work Plan"], {"agent": 38, "owner-idle": 55, "verify": 5})
        self.assertEqual(got["judges_wall"], 20)
        self.assertEqual(got["seats"]["s"]["runs"], 1)
        self.assertEqual(got["verify"]["runs"], 1)
        self.assertEqual(got["owner"]["prompts"], 2)
        self.assertEqual(got["close"]["freshness"], "TAIL-CERTIFIED")
        self.assertEqual(got["close"]["owner_ok"], "the owner's word")
        self.assertEqual(got["late_records"], 1)                  # the review written after the close
        self.assertEqual(got["schema"], numbers.SCHEMA)

    def test_only_the_closing_sessions_open_call_ends_at_the_close(self):
        """V3 (opus): a second session that died with a call open does not run on to the close."""
        lane, td, close_t = self.project()
        with open(self.jdir / "hooks.jsonl", "a") as f:
            for rec in (hl(close_t - 90, "UserPromptSubmit", hook="chat-log", host="h-2", detail="logged M009"),
                        hl(close_t - 80, "PreToolUse", tool="Bash", tid="dies", hook="task-gate", host="h-2")):
                f.write(json.dumps(rec) + "\n")
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual(got["time"]["sessions"], 2)
        self.assertEqual(got["time"]["by_kind"]["agent"], 39 + 10)          # h-2: 10 s, not 90

    def test_failed_runs_and_unplaced_sleep_reach_the_closure(self):
        lane, td, close_t = self.project()
        (td / "post-panel-reviews.jsonl").write_text(
            json.dumps({"id": "f1", "kind": "panel", "mode": "impl", "status": "reserved",
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(close_t - 50))}) + "\n"
            + json.dumps({"id": "f1", "status": "failed"}) + "\n")
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual(got["rounds"]["failed"], 1)
        self.assertIn("sleep_unplaced", got["time"])

    def test_a_task_never_activated_has_no_time(self):
        lane, td, close_t = self.project()
        (self.jdir / "enforcement.jsonl").write_text("")
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertIsNone(got["time"])
        self.assertEqual(got["close"]["freshness"], "TAIL-CERTIFIED")

    def test_out_of_time(self):
        lane, td, _ = self.project()
        with self.assertRaises(numbers.OutOfTime):
            numbers.compose(lane, td, "007", deadline=time.monotonic() - 1)


# --- the close writes them; `tasks numbers` shows them (W5) -----------------------------------------------------------

import subprocess  # noqa: E402


class _CliProject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve() / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        (self.project / ".agent" / "journal").mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.journal = self.project / ".agent" / "journal" / "enforcement.jsonl"

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "PLAYBOOK_PROJECT_DIR", "CLAUDE_PROJECT_DIR", "PLAYBOOK_SANDBOXED",
                            "BASH_ENV", "PLAYBOOK_ROLE")}
        env.update(PYTHONPATH=str(_PLAYBOOK), PLAYBOOK_SESSION_ID="pid-num235", HOME=self._tmp.name,
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        return env

    def cli(self, *args, ok=True, driver=None):
        argv = [sys.executable, "-m", "tasks.cli", *args]
        if driver:            # the CLI run in-process after a patch (a fault injected into the composer)
            argv = [sys.executable, "-c", driver + "\nimport sys\nfrom tasks import cli\nsys.argv = ['tasks'] + "
                    + repr(list(args)) + "\ncli.main()\n"]
        r = subprocess.run(argv, cwd=self.project, env=self.env(), capture_output=True, text=True, timeout=120)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def task_dir(self):
        return next((self.project / ".agent" / "tasks").glob("001-*"))

    def numbers_doc(self):
        return json.loads((self.task_dir() / "numbers.json").read_text(encoding="utf-8"))

    def close_records(self):
        return [json.loads(x) for x in self.journal.read_text().splitlines() if '"hook":"close"' in x]


class TheClose(_CliProject):
    def test_a_close_writes_its_numbers_and_names_the_file(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        r = self.cli("work", "done", "--force", "--reason", "test close")
        self.assertIn("numbers: .agent/tasks/001-first/numbers.json", r.stdout)
        doc = self.numbers_doc()
        self.assertEqual((doc["schema"], doc["task"], len(doc["closures"])), (1, "001", 1))
        c = doc["closures"][0]
        self.assertEqual(c["close"]["forced_reason"], "test close")
        self.assertTrue(c["closed_at"] and c["window"] and c["time"] is not None)
        self.assertTrue(self.close_records())
        self.assertTrue(all(rec.get("task") == "001" for rec in self.close_records()))   # agy, opus, grok

    def test_a_second_close_after_a_reopen_adds_a_closure(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        time.sleep(1.1)                                       # the receipt's stamp has whole seconds
        self.cli("work", "1", "--reopen")
        self.cli("work", "done", "--force", "--reason", "two")
        doc = self.numbers_doc()
        self.assertEqual([c["close"]["forced_reason"] for c in doc["closures"]], ["one", "two"])
        self.assertNotEqual(doc["closures"][0]["window"]["start"], doc["closures"][1]["window"]["start"])

    def test_a_composer_that_fails_leaves_the_close_as_it_was(self):
        for n, exc in enumerate(("RuntimeError('boom')", "SystemExit(3)", "numbers.OutOfTime('slow')")):
            with self.subTest(exc=exc):
                self.project = Path(self._tmp.name).resolve() / f"proj{n}"          # a project of its own
                (self.project / ".agent" / "tasks").mkdir(parents=True)
                subprocess.run(["git", "init", "-q", str(self.project)], check=True)
                self.cli("new", "quick", "first")
                self.cli("work", "1")
                driver = ("from tasks import numbers\n"
                          f"def boom(*a, **k):\n    raise {exc}\n"
                          "numbers.compose = boom\n")
                r = self.cli("work", "done", "--force", "--reason", "x", driver=driver)
                self.assertIn("Task 001 done.", r.stdout)
                self.assertIn("numbers: not written", r.stderr)
                self.assertIn("## Status\ndone", (self.task_dir() / "task.md").read_text())
                self.assertFalse((self.task_dir() / "numbers.json").exists())

    def test_an_older_writer_never_replaces_a_newer_closure(self):
        """codex (plan panel): a close that paused before writing must not overwrite the numbers of a later close."""
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        before = (self.task_dir() / "numbers.json").read_bytes()
        stale = numbers.compose(self.project / ".agent", self.task_dir(), "001", deadline=time.monotonic() + 30)
        stale["closed_at"] = "2026-01-01T00:00:00+00:00"      # what a delayed writer composed, for an older receipt
        orig = numbers.compose
        numbers.compose = lambda *a, **k: stale
        try:
            self.assertIsNone(numbers.write_closure(self.project / ".agent", self.task_dir(), "001"))
        finally:
            numbers.compose = orig
        self.assertEqual((self.task_dir() / "numbers.json").read_bytes(), before)


class TheNumbersCommand(_CliProject):
    def listing(self):
        return sorted(str(p.relative_to(self.project)) for p in self.project.rglob("*") if ".git" not in p.parts)

    def test_it_prints_the_closures_writes_nothing_and_sweeps_no_session(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "test close")
        dead = self.project / ".agent" / "sessions" / "s-dead"
        dead.mkdir(parents=True)
        before = self.listing()
        r = self.cli("numbers", "1")
        self.assertIn("closure", r.stdout)
        self.assertIn("forced: test close", r.stdout)
        self.assertEqual(self.listing(), before)               # no session sweep, no write (agy, codex)

    def test_an_open_task_is_a_snapshot_not_written(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        r = self.cli("numbers", "1")
        self.assertIn("snapshot", r.stdout)
        self.assertFalse((self.task_dir() / "numbers.json").exists())

    def test_wrong_usage_is_refused(self):
        r = self.cli("numbers", "1", "2", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tasks numbers [N]", r.stdout + r.stderr)       # refused as wrong usage, not as unknown


# --- impl round 1's findings (V1-V8) ----------------------------------------------------------------------------------

class TheRealReceipt(unittest.TestCase):
    """V1 (opus, grok, agy): the receipt as `core.format_verify_receipt` really writes it."""

    def task(self, ts="2026-10-18T01:00:00+03:00", **kw):
        from tasks.core import format_verify_receipt
        entries = kw.pop("entries", [("verify", "x", 0, "ok"), ("lint", "y", 1, "bad")])
        block = format_verify_receipt(entries, "abc", "assertive", timestamp=ts, **kw)
        return "# 007 - x\n\n## Status\ndone\n\n## Verification Receipt\n\n" + block + "\n"

    def test_an_accepted_reason_followed_by_a_bound_and_a_stop_rule(self):
        got = numbers.receipt_close(self.task(freshness={"verdict": "STALE", "round_fp": "a", "now_fp": "b",
                                                         "accepted_reason": 'docs "only"', "bound": "post-panel PASS x",
                                                         "stop_rule": "stop rule: no judge needed"}))
        self.assertEqual((got["freshness"], got["accepted_reason"]), ("STALE", 'docs "only"'))

    def test_each_verdicts_own_wording(self):
        for v, want in (("NO-STAMP", "NO-STAMP"), ("UNREADABLE", "UNREADABLE"), ("FRESH", "FRESH"),
                        ("TAMPER-GUARD-DEGRADED", "TAMPER-GUARD-DEGRADED"), ("TAIL-CERT-PASS", "TAIL-CERTIFIED"),
                        ("EXCLUDE-COVERS-CODE", "EXCLUDE-COVERS-CODE"), ("SINGLE-TAMPER-DEGRADED", "SINGLE-TAMPER-DEGRADED"),
                        ("STALE", "STALE")):
            with self.subTest(v=v):
                got = numbers.receipt_close(self.task(freshness={"verdict": v, "round_fp": "a", "now_fp": "b",
                                                                 "cert_clause": "c", "tamper_degraded_detail": "d"}))
                self.assertEqual(got["freshness"], want)

    def test_a_failed_command_is_counted(self):
        self.assertEqual(numbers.receipt_close(self.task())["commands"], {"pass": 1, "fail": 1})

    def test_a_utc_stamp_with_z(self):
        got = numbers.receipt_close(self.task(ts="2026-10-17T22:00:00Z"))
        self.assertEqual(got["epoch"], numbers._epoch("2026-10-17T22:00:00Z"))


class RoundOneFindings(unittest.TestCase):
    def test_a_window_that_starts_mid_turn_is_agent_time(self):
        """V2 (opus, sonnet): the turn's prompt is older than the read margin — a tool event proves a turn."""
        got = part([L(0, "PostToolUse", tid="a"), L(10, "PreToolUse", tool="Bash", tid="b"),
                    L(20, "PostToolUse", tool="Bash", tid="b"), L(30, "Stop", decision="allow"),
                    L(90, "UserPromptSubmit")])
        self.assertEqual(got["by_kind"], {"agent": 30, "owner-idle": 60})

    def test_a_new_prompt_ends_a_call_that_never_ended(self):
        """V3 (opus, sonnet): a cancelled dialog fires no PostToolUse; the next prompt starts clean."""
        got = part([L(0, "UserPromptSubmit"), L(10, "PreToolUse", tool="AskUserQuestion", tid="q", hook="dialog-log"),
                    L(50, "UserPromptSubmit"), L(60, "PostToolUse", tid="x"), L(70, "Stop", decision="allow")])
        self.assertEqual(got["by_kind"], {"agent": 30, "owner-question": 40})

    def test_sleep_goes_where_its_samples_were_taken(self):
        """V4 (codex's example): a sample written 60 s after it was taken (sts 1, ts 61) — the 60 s slept fall in the gap
        that holds them, not in the last short gap."""
        got = part([L(-1, "UserPromptSubmit"), L(0, "PostToolUse", tid="a", susp=0, sts=T0, boot="aaaaaaaa"),
                    L(61, "PostToolUse", tid="b", susp=0, sts=T0 + 1, boot="aaaaaaaa"),
                    L(62, "PostToolUse", tid="c", susp=60, sts=T0 + 62, boot="aaaaaaaa")])
        self.assertEqual((got["slept"], got["by_kind"], got["sleep_unplaced"]), (60, {"agent": 3}, 0))

    def test_sleep_that_cannot_be_placed_is_counted_not_dropped(self):
        got = part([L(-1, "UserPromptSubmit"), L(0, "PostToolUse", tid="a", susp=0, sts=T0, boot="aaaaaaaa"),
                    L(10, "PostToolUse", tid="b", susp=50, sts=T0 + 10, boot="aaaaaaaa")])
        self.assertEqual((got["slept"], got["sleep_unplaced"]), (10, 40))

    def test_overlapping_review_and_verify_spans_count_once(self):
        """V5 (codex): a 100 s Bash gap under both spans for 50 s is 50 judges and 50 agent."""
        got = part([L(0, "UserPromptSubmit"), L(0, "PreToolUse", tool="Bash", tid="b"),
                    L(100, "PostToolUse", tool="Bash", tid="b")], reviews=[(0, 50)], verifies=[(0, 50)])
        self.assertEqual(got["by_kind"], {"judges": 50, "agent": 50})

    def test_rounds_count_each_call_and_keep_overrides(self):
        """V6 (agy, codex, grok)."""
        t = numbers._epoch("2026-10-17T21:00:00Z")
        runs = [{"id": "p1", "kind": "panel", "mode": "impl", "status": "done", "outcome": "completed",
                 "measures": {"critical": 0, "important": 2}, "owner_ok": "the owner said a third round",
                 "ts": "2026-10-17T20:00:00+00:00"},
                {"id": "s1", "panel": "fp", "status": "done", "verdict": "PASS", "critical": 0, "important": 0,
                 "owner_ok": None, "ts": "2026-10-17T21:00:30+00:00"}]
        reviews = [{"kind": "single", "round": 4, "_t": t}, {"kind": "single", "round": 4, "_t": t + 3000},
                   {"kind": "tail-cert", "round": 5, "_t": t + 4000}, {"kind": "tail-cert", "round": 5, "_t": t + 5000}]
        got = numbers.rounds_table(runs, reviews, t - 7200, t + 7200)
        self.assertEqual(got["panels"][0]["owner_ok"], "the owner said a third round")
        self.assertEqual((got["single"], got["tail_cert"]), (1, 2))        # the record at t is the post-panel run

    def test_the_closures_first_prompt_is_counted(self):
        """grok (Minor): the prompt that asked for `tasks work N` was logged before the pointer moved."""
        lines = [L(0, "UserPromptSubmit", detail="logged M001", task=""), L(2, "PostToolUse", tid="w"),
                 L(9, "UserPromptSubmit", detail="logged M002")]
        self.assertEqual(numbers.owner_table(lines, "007", None)["ids"], ["M001", "M002"])
        lines = [L(0, "UserPromptSubmit", detail="logged M001", task="008"), L(1, "PostToolUse", tid="z", task="008"),
                 L(2, "PostToolUse", tid="w")]
        self.assertEqual(numbers.owner_table(lines, "007", None)["ids"], [])      # another task's prompt is not this one's
        # the live shape (W8): that prompt is logged seconds BEFORE the window starts at the `work` record
        lines = [L(0, "UserPromptSubmit", detail="logged M001", task=""), L(3, "PostToolUse", tid="w"),
                 L(9, "UserPromptSubmit", detail="logged M002"), L(-5, "UserPromptSubmit", detail="logged M000", task=""),
                 L(-9, "UserPromptSubmit", detail="logged M099"), L(-8, "PostToolUse", tid="old")]   # the earlier life
        lines.sort(key=lambda r: r["_t"])
        self.assertEqual(numbers.owner_table(lines, "007", None, start=T0 + 3)["ids"], ["M001", "M002"])

    def test_a_dialog_answer_delivered_twice_counts_once(self):
        post = L(40, "PostToolUse", hook="dialog-log", tool="AskUserQuestion", tid="toolu_A", detail="logged M002")
        lines = [L(10, "PreToolUse", hook="dialog-log", tool="AskUserQuestion", tid="toolu_A"), post, dict(post)]
        got = numbers.owner_table(lines, "007", TheDialogs.LOG.replace("M001", "M002"))
        self.assertEqual((got["dialogs"], got["wait"], got["questions"]), (1, 30, 2))

    def test_the_display(self):
        c = {"closed_at": "x", "window": {}, "time": None, "judges_wall": 0,
             "rounds": {"panels": [{"mode": "impl", "outcome": "void", "critical": None, "important": None}],
                        "post_panel": [], "single": 0, "tail_cert": 0},
             "seats": {"s": {"runs": 2, "ok": 1, "failed": 1, "seconds": 90.5, "in": 1, "out": 1, "cache_read": 0,
                             "cache_write": 0, "unknown": 0, "kinds": {}}},
             "verify": {"runs": 1, "ran": 1, "reused": 0, "failed": 0, "seconds": 83.75}, "owner": {}, "close": None}
        text = "\n".join(numbers.format_closure(c))
        self.assertIn("impl panel void (C- I-)", text)
        self.assertIn("1m30.5s", text)
        self.assertIn("1m23.8s", text)


class TheArchiveMargin(_Tmp):
    def test_an_archive_renamed_just_before_the_window_is_read(self):
        """V8 (agy): its lines hold the samples that bracket the window's first gaps."""
        arch = self.jdir / f"hooks.jsonl.archived-20261017-100000-{T0 - 100}-1"
        arch.write_text(json.dumps(hl(T0 - 200, "PostToolUse", susp=5, sts=T0 - 200, boot="aaaaaaaa")) + "\n")
        got = numbers.read_hook_lines(self.jdir, T0, time.monotonic() + 30)
        self.assertEqual([r["_t"] for r in got], [T0 - 200])


class TheBudget(_CliProject):
    def test_a_held_lock_cannot_hold_the_close_past_its_budget(self):
        """V7 (codex, opus): one deadline through the composition, the lock wait and the write."""
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        from tasks.filelock import lock_path_for
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        tf = self.task_dir() / "task.md"
        holder = subprocess.Popen(["flock", str(lock_path_for(tf)), "sleep", "8"])
        try:
            time.sleep(0.3)
            t = time.monotonic()
            with self.assertRaises(Exception):
                numbers.write_closure(self.project / ".agent", self.task_dir(), "001", budget=1.0)
            self.assertLess(time.monotonic() - t, 4)
        finally:
            holder.kill()
            holder.wait()

    def test_a_write_that_did_not_happen_says_so(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        r = self.cli("work", "done", "--force", "--reason", "x",
                     driver="from tasks import numbers\nnumbers.write_closure = lambda *a, **k: None\n")
        self.assertIn("numbers: not written", r.stderr)


if __name__ == "__main__":
    unittest.main()
