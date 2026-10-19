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

    def test_the_closes_background_run_after_the_turn_is_carved_into_judges_and_verify(self):
        # task 249: the agent ended its turn while the close ran as a background command (tasks 247 and 248, seen
        # live: 204 s and 213 s of owner-idle while the owner slept) — the close's own verify and tail-cert spans
        # before its stamp are the machine's; what no span covers stays the owner's
        lines = [L(0, "UserPromptSubmit"), L(5, "PreToolUse", tool="Bash", tid="bg"),
                 L(6, "PostToolUse", tool="Bash", tid="bg"), L(8, "Stop", decision="allow")]
        got = numbers.session_partition(lines, "007", T0 + 200, [(T0 + 170, T0 + 195)], [(T0 + 10, T0 + 160)],
                                        known_close=True)
        self.assertEqual(got["by_kind"], {"agent": 8, "verify": 150, "judges": 25, "owner-idle": 17})
        self.assertEqual((got["total"], got["wall"]), (200, 200))

    def test_a_recompose_after_the_closes_notification_reads_the_same(self):
        # task 249: the background command's notification lands in the stamp's own second with no task (the close
        # ended it) — a recompose took the whole wait for another task's time
        lines = [L(0, "UserPromptSubmit"), L(5, "PreToolUse", tool="Bash", tid="bg"),
                 L(6, "PostToolUse", tool="Bash", tid="bg"), L(8, "Stop", decision="allow"),
                 L(200, "UserPromptSubmit", detail="not logged: a notification", task="")]
        got = numbers.session_partition(lines, "007", T0 + 200, [(T0 + 170, T0 + 195)], [(T0 + 10, T0 + 160)],
                                        known_close=True)
        self.assertEqual(got["by_kind"], {"agent": 8, "verify": 150, "judges": 25, "owner-idle": 17})
        self.assertEqual((got["total"], got["not_task"]), (200, 0))

    def test_the_tasks_own_line_in_the_stamps_second_is_kept(self):
        # only a line that is no task's is dropped there: the close's own call, started in the stamp's second, still
        # ends the wait before it as rule 4 says (the owner's, uncarved), with no virtual End in its place
        lines = [L(0, "UserPromptSubmit"), L(8, "Stop", decision="allow"),
                 L(200, "PreToolUse", tool="Bash", tid="close")]
        got = numbers.session_partition(lines, "007", T0 + 200, [], [(T0 + 10, T0 + 160)], known_close=True)
        self.assertEqual(got["by_kind"], {"agent": 8, "owner-idle": 192})

    def test_an_owner_wait_a_prompt_ends_is_not_carved(self):
        lines = [L(0, "UserPromptSubmit"), L(8, "Stop", decision="allow"), L(60, "UserPromptSubmit")]
        got = numbers.session_partition(lines, "007", T0 + 60, [(T0 + 20, T0 + 30)], [(T0 + 10, T0 + 50)])
        self.assertEqual(got["by_kind"], {"agent": 8, "owner-idle": 52})

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

    def test_the_parts_add_up_on_the_branches_the_close_adds(self):
        """Q1 again, on what `part()` never passes (task 237's impl round 2, opus and sonnet): a window start inside the
        session (clipped gaps, sleep shares charged by their own samples), the close's own session (`known_close`, its
        call kept through a prompt), a live sample, and samples taken before their line was written."""
        import random
        events = ["UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SessionStart",
                  "SessionEnd"]
        for case in range(1500):
            rnd = random.Random(1000 + case)
            t, lines, open_ids = 0, [], []
            boot, susp = "aaaaaaaa", 0
            for k in range(rnd.randint(2, 40)):
                t += rnd.choice([0, 1, 3, 30, 600, 5000, 9000])
                ev = rnd.choice(events)
                kw = {"task": rnd.choice(["007", "007", "007", "008", None]),
                      "detail": rnd.choice(["", "not logged: a notification", "logged M001"])}
                if ev == "PreToolUse":
                    tid = f"t{k}"
                    open_ids.append(tid)
                    kw.update(tool=rnd.choice(["Bash", "Edit", "AskUserQuestion", "exec_command"]), tid=tid)
                elif ev.startswith("PostToolUse") and open_ids and rnd.random() < 0.7:
                    kw.update(tid=open_ids.pop(0))
                if ev == "Stop":
                    kw["decision"] = rnd.choice(["allow", "block"])
                if rnd.random() < 0.3:
                    if rnd.random() < 0.05:
                        boot, susp = "bbbbbbbb", 0
                    susp += rnd.choice([0, 0, 1, 50, 4000])
                    kw.update(susp=susp, sts=T0 + t - rnd.choice([0, 0, 0, 5, 50, 700]), boot=boot)
                kw["section"] = rnd.choice([None, "Work Plan", "Design Phase"])
                lines.append(L(t, ev, **kw))
            end = t + rnd.choice([0, 0, 20])
            start = None if rnd.random() < 0.3 else T0 + rnd.randint(0, max(1, t))
            live = ((susp + rnd.choice([0, 0, 30]), T0 + end + rnd.choice([1, 5, 200]), boot) if rnd.random() < 0.5
                    else None)
            got = numbers.session_partition(lines, "007", T0 + end,
                                            [(T0 + rnd.randint(0, t + 1) + 0.37, T0 + t + 5.61)],
                                            [(T0 + 0.75, T0 + rnd.randint(0, t + 1) + 0.5)], start=start,
                                            close_open=rnd.random() < 0.7, known_close=rnd.random() < 0.5, live=live)
            with self.subTest(case=case):
                parts = sum(got["by_kind"].values()) + got["slept"] + got["away"] + got["unknown"]
                self.assertEqual(parts, got["total"])
                self.assertEqual(got["total"] + got["not_task"], got["wall"])
                self.assertTrue(all(v >= 0 for v in list(got["by_kind"].values()) + [
                    got["slept"], got["away"], got["unknown"], got["not_task"], got["sleep_unplaced"]]))
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
             "command": 'owner-ok: "the owner\'s word"'},          # as lifecycle writes it: the bound, labelled
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
        # the writers' own bound and stop-rule forms (task 236's impl round 1: made-up ones passed here)
        from tasks.stop_rule import receipt_clause
        clause = receipt_clause({"rounds": 2, "allowed": 2, "risk": "assertive", "can": True, "changed": ["x.md"]})
        got = numbers.receipt_close(self.task(freshness={"verdict": "STALE", "round_fp": "a", "now_fp": "b",
                                                         "accepted_reason": 'docs "only"',
                                                         "bound": "bound to post-D6 PASS r1 on tree t (unchanged since)",
                                                         "stop_rule": clause}))
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


# --- task 236: the findings task 235's last round routed ---------------------------------------------------------------

def big_session(n, step=2):
    """A session of `n` lines, a suspend sample on every one (what pb_journal's python writers and the state echo
    leave), turns every 50 lines."""
    out = []
    for i in range(n):
        ev = "UserPromptSubmit" if i % 50 == 0 else ("PreToolUse" if i % 2 else "PostToolUse")
        rec = hl(T0 + i * step, ev, tool="Bash", tid=f"t{i // 2}", section="Work Plan", susp=100, sts=T0 + i * step,
                 boot="aaaaaaaa")
        rec["_t"] = T0 + i * step
        out.append(rec)
    return out


class TheScale(unittest.TestCase):
    """W1 (opus, sonnet, codex; measured 13.7 s on 6,000 lines): the partition is linear and keeps the deadline."""

    def test_twenty_thousand_lines_inside_the_budget(self):
        lines = big_session(20000)
        t = time.monotonic()
        got = numbers.session_partition(lines, "007", lines[-1]["_t"], [], [], deadline=time.monotonic() + 30)
        self.assertLess(time.monotonic() - t, 3)
        self.assertEqual(got["total"], lines[-1]["_t"] - lines[0]["_t"])

    def test_a_past_deadline_stops_the_partition_itself(self):
        lines = big_session(5000)
        with self.assertRaises(numbers.OutOfTime):
            numbers.session_partition(lines, "007", lines[-1]["_t"], [], [], deadline=time.monotonic() - 1)

    def test_the_dialog_entries_are_indexed_once(self):
        idx = numbers.dialog_index(TheDialogs.LOG)
        self.assertEqual(idx["toolu_A"], {"id": "M001", "questions": 2, "answers": 2})
        self.assertEqual(idx["toolu_B"], {"id": "M002", "questions": 1, "answers": 1})
        self.assertNotIn("toolu_C", idx)


class TheRoutedFindings(unittest.TestCase):
    """Task 235's last round, W2-W10 and the Minor ones — each built from the writers' output where a writer exists."""

    def receipt_task(self, ts="2026-10-18T01:00:00+03:00", **kw):
        from tasks.core import format_verify_receipt
        block = format_verify_receipt([("verify", "x", 0, "ok")], "abc", "assertive", timestamp=ts, **kw)
        return "# 007 - x\n\n## Status\ndone\n\n## Verification Receipt\n\n" + block + "\n"

    def test_w2_owner_ok_stop_rule_and_post_panel_reasons_whole_from_the_receipt(self):
        from tasks.post_d6 import receipt_line
        long = "the owner, 2026-10-18 02:30: " + "a reason that runs well past two hundred bytes " * 6
        post = receipt_line([{"panel": "fp", "verdict": "FAIL", "owner_ok": None},
                             {"panel": "fp", "verdict": "PASS", "owner_ok": long}], "fp")
        got = numbers.receipt_close(self.receipt_task(
            freshness={"verdict": "STALE", "round_fp": "a", "now_fp": "b", "accepted_reason": long,
                       "bound": f'owner-ok: "{" ".join(long.split())}"',      # as core.bound_stale_close writes it
                       "stop_rule": "stop rule (D6): 2 of 2 impl rounds used (assertive); no judge read after x"},
            post_d6=post))
        self.assertEqual(got["owner_ok"], " ".join(long.split()))
        self.assertTrue(got["stop_rule"].startswith("stop rule (D6): 2 of 2 impl rounds used"))
        self.assertEqual(got["post_panel"], {"runs": 2, "verdict": "PASS", "owner_ok": [" ".join(long.split())]})
        text = "\n".join(numbers.format_closure({"closed_at": "x", "window": {}, "time": None, "close": got}))
        self.assertEqual(text.count("owner-ok"), 1)                # the label once, the reason whole

    def test_w3_a_receipt_quoted_in_a_fence_is_not_the_receipt(self):
        real = self.receipt_task()
        fenced = ("# 007 - x\n\n## Recent Chat\n```text\n## Verification Receipt\n\n"
                  "### 2026-12-31T00:00:00+03:00 · risk reversible · commit zzz\n- **Forced close, reason:** quoted\n```\n\n")
        got = numbers.receipt_close(fenced + real.split("\n", 2)[2])
        self.assertEqual((got["stamp"], got["forced_reason"]), ("2026-10-18T01:00:00+03:00", None))

    def test_w7_a_reopen_with_no_new_prompt_counts_no_earlier_prompt(self):
        lines = [L(0, "UserPromptSubmit", detail="logged M001", task=""), L(2, "PostToolUse", tid="w"),
                 L(50, "PreToolUse", tool="Bash", tid="c"), L(52, "PostToolUse", tool="Bash", tid="c", task=""),
                 L(60, "PreToolUse", tool="Bash", tid="r", task=""), L(61, "PostToolUse", tool="Bash", tid="r"),
                 L(70, "PostToolUse", tid="z")]
        self.assertEqual(numbers.owner_table(lines, "007", None, start=T0 + 61)["ids"], [])

    def test_w8_a_task_activated_while_another_was_active_keeps_its_prompt(self):
        lines = [L(0, "UserPromptSubmit", detail="logged M005", task="234"),
                 L(1, "PreToolUse", hook="command-guard", tool="Bash", tid="w", task=None),
                 L(1, "PreToolUse", hook="task-gate", tool="Bash", tid="w", task="234"),
                 L(3, "PostToolUse", tool="Bash", tid="w", task="235"), L(9, "PostToolUse", tid="x", task="235")]
        self.assertEqual(numbers.owner_table(lines, "235", None, start=T0 + 3)["ids"], ["M005"])

    def test_w9_the_closing_session_is_the_one_whose_open_call_is_this_tasks(self):
        lines_1 = [L(0, "UserPromptSubmit", host="h-1"), L(40, "PreToolUse", tool="Bash", tid="close", host="h-1")]
        lines_2 = [L(0, "UserPromptSubmit", host="h-2", task="008"),
                   L(45, "PreToolUse", tool="Bash", tid="other", host="h-2", task="008")]
        self.assertEqual(numbers.closing_session({"h-1": lines_1, "h-2": lines_2}, "007", T0 + 60), "h-1")
        # the closing session's own call runs to the close: the gap from its Pre to `end` is the task's agent time
        # (agy's "the end line carries the task" changes nothing observable: that gap counts only when its first line
        # is the task's, and the end line took that line's task — kept for clarity, noted as equivalent)
        got = numbers.session_partition(lines_1, "007", T0 + 60, [], [])
        self.assertEqual((got["by_kind"], got["not_task"], got["wall"]), ({"agent": 60}, 0, 60))

    def test_w10_a_sample_after_the_close_brackets_the_closing_gap(self):
        lines = [L(0, "UserPromptSubmit", susp=0, sts=T0, boot="aaaaaaaa"), L(10, "PreToolUse", tool="Bash", tid="c"),
                 L(30, "PostToolUse", tool="Bash", tid="c", susp=0, sts=T0 + 30, boot="aaaaaaaa")]
        got = numbers.session_partition(lines, "007", T0 + 20, [], [])
        self.assertEqual((got["total"], got["sleep_unmeasured"]), (20, 0))

    def test_a_pre_sorted_after_its_post_leaves_no_call_open(self):
        lines = [L(0, "UserPromptSubmit"), L(5, "PostToolUse", tool="Bash", tid="x"),
                 L(5, "PreToolUse", tool="Bash", tid="x"), L(9, "Stop", decision="allow")]
        self.assertEqual(part(lines, reviews=[(0, 20)])["by_kind"], {"agent": 9})

    def test_a_gap_crossing_the_window_start_is_clipped_not_dropped(self):
        lines = [L(-10, "UserPromptSubmit"), L(20, "PostToolUse", tid="a"), L(30, "Stop", decision="allow")]
        got = numbers.session_partition(lines, "007", T0 + 30, [], [], start=T0)
        self.assertEqual((got["by_kind"], got["wall"]), ({"agent": 30}, 30))

    def test_the_dialog_marker_is_read_as_the_writer_wrote_it(self):
        log = TheDialogs.LOG.replace("<!-- dialog:toolu_A -->", "<!-- dialog:toolu_Aweird -->")
        self.assertEqual(numbers.dialog_counts(log, "toolu_A weird!")["id"], "M001")

    def test_bound_is_displayed(self):          # the bound as core.bound_stale_close writes it (task 237)
        text = "\n".join(numbers.format_closure({"closed_at": "x", "window": {}, "time": None,
                                                "close": {"freshness": "STALE",
                                                          "bound": "bound to post-D6 PASS z on tree t"}}))
        self.assertIn("bound to post-D6 PASS z on tree t", text)


class TheRoutedCloseFindings(_CliProject):
    def test_w4_two_closes_in_one_second_keep_two_entries(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        doc = self.numbers_doc()
        second = dict(doc["closures"][0], closure_no=doc["closures"][0]["closure_no"] + 1)
        orig = numbers.compose
        numbers.compose = lambda *a, **k: second
        try:
            numbers.write_closure(self.project / ".agent", self.task_dir(), "001")
        finally:
            numbers.compose = orig
        self.assertEqual([c["closure_no"] for c in self.numbers_doc()["closures"]], [1, 2])

    def test_w6_a_reopened_task_shows_a_snapshot_of_its_new_life(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        time.sleep(1.1)
        self.cli("work", "1", "--reopen")
        c = numbers.compose(self.project / ".agent", self.task_dir(), "001", deadline=time.monotonic() + 30)
        self.assertTrue(c["snapshot"])
        self.assertIsNone(c["closed_at"])
        r = self.cli("numbers", "1")
        self.assertIn("closure ", r.stdout)                         # the saved closure
        self.assertIn("snapshot", r.stdout)                         # and the life in progress

    def test_the_close_records_stop_at_the_next_activation(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        first = self.numbers_doc()["closures"][0]
        end = numbers._iso_epoch(first["closed_at"])
        with open(self.journal, "a") as f:
            f.write(json.dumps({"ts": iso(end + 30), "hook": "tasks", "decision": "record", "reason": "work",
                                "task": "001"}) + "\n")
            f.write(json.dumps({"ts": iso(end + 60), "hook": "close", "decision": "record",
                                "reason": "stale close owner-ok", "command": "the second close's", "task": "001"}) + "\n")
        c = numbers.compose(self.project / ".agent", self.task_dir(), "001", deadline=time.monotonic() + 30, end=end)
        self.assertNotEqual((c["close"] or {}).get("owner_ok"), "the second close's")


class TheLinkedJournal(_Tmp):
    def test_w5_a_rotation_held_lock_makes_the_reader_wait_not_skip(self):
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.write("hooks.jsonl", [hl(T0, "PostToolUse")])
        holder = subprocess.Popen(["flock", "-x", str(self.jdir), "sleep", "3"])
        try:
            time.sleep(0.3)
            t = time.monotonic()
            with self.assertRaises(numbers.OutOfTime):
                numbers.read_hook_lines(self.jdir, T0, time.monotonic() + 1)
            self.assertLess(time.monotonic() - t, 2.5)
        finally:
            holder.wait()
        self.assertEqual(len(numbers.read_hook_lines(self.jdir, T0, time.monotonic() + 5)), 1)

    def test_read_enforcement_refuses_a_linked_journal(self):
        real = self.d / "other"
        real.mkdir()
        (real / "enforcement.jsonl").write_text(json.dumps({"ts": iso(T0), "hook": "tasks", "reason": "work",
                                                            "task": "007"}) + "\n")
        lane = self.d / "lane2"
        lane.mkdir()
        (lane / "journal").symlink_to(real)
        self.assertEqual(numbers.read_enforcement(lane, time.monotonic() + 5), [])


class TheMutationGaps(_Tmp):
    """Task 236's mutation pass: what the first tests did not hold apart (X3, X4, X7, X8, X9)."""

    def test_the_sleep_placement_keeps_the_deadline_by_itself(self):
        # nine gaps (the gap loop checks every 2,000) and 1,500 samples in the 120 s after the close (the placement
        # counts pairs, equal seconds too): only the placement can stop
        lines = big_session(10)
        end = lines[-1]["_t"]
        ahead = []
        for i in range(1500):
            t = end + 1 + i % 100
            rec = hl(t, "PostToolUse", tool="Bash", tid=f"a{i}", susp=100, sts=t, boot="aaaaaaaa")
            rec["_t"] = t
            ahead.append(rec)
        with self.assertRaisesRegex(numbers.OutOfTime, "sleep placement"):
            numbers.session_partition(lines + ahead, "007", end, [], [], deadline=time.monotonic() - 1)

    def test_the_gap_loop_keeps_the_deadline_by_itself(self):
        # 5,000 lines with no sample: nothing to place, only the gap loop can stop
        lines = [{k: v for k, v in r.items() if k not in ("susp", "sts", "boot")} for r in big_session(5000)]
        with self.assertRaisesRegex(numbers.OutOfTime, "partition"):
            numbers.session_partition(lines, "007", lines[-1]["_t"], [], [], deadline=time.monotonic() - 1)

    def test_a_dialog_with_an_unsafe_long_id_found_as_the_writer_marked_it(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("dialog_log_x", _PLAYBOOK / "scripts" / "dialog_log.py")
        dlog = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dlog)
        tid = "toolu/" + "A" * 130                 # the writer drops the `/` and keeps 120 characters
        lane = self.d / ".agent"                  # made by _Tmp, with its journal/
        self.assertTrue(dlog.write_entry(lane, tid, ["Options dialog — x", 'A1: "Red"', "Q1 (Color): Pick a color"],
                                         "claude", "s").startswith("logged M"))
        lines = [L(10, "PreToolUse", hook="dialog-log", tool="AskUserQuestion", tid=tid),
                 L(40, "PostToolUse", hook="dialog-log", tool="AskUserQuestion", tid=tid, detail="logged M001")]
        got = numbers.owner_table(lines, "007", (lane / "chat_log.md").read_text(encoding="utf-8"))
        self.assertEqual((got["dialogs"], got["questions"], got["answers"], got["wait"]), (1, 1, 1, 30))

    def test_a_block_fenced_inside_the_receipt_is_not_a_close(self):
        real = TheRoutedFindings.receipt_task(None)
        quoted = ("```text\n### 2026-12-31T00:00:00+03:00 · risk reversible · commit zzz\n"
                  "- **Forced close, reason:** quoted\n```\n\n")
        text = real.replace("## Verification Receipt\n\n", "## Verification Receipt\n\n" + quoted, 1)
        got = numbers.receipt_close(text)
        self.assertEqual((got["stamp"], got["forced_reason"]), ("2026-10-18T01:00:00+03:00", None))

    def test_a_clipped_gap_keeps_only_the_sleep_its_window_part_holds(self):
        # 80 s slept inside a 100 s gap and the window starting at its middle: the window's 50 s hold 50 of it (X17)
        lines = [L(-50, "PostToolUse", tid="a", susp=100, sts=T0 - 50, boot="aaaaaaaa"),
                 L(50, "Stop", decision="allow", susp=180, sts=T0 + 50, boot="aaaaaaaa")]
        got = numbers.session_partition(lines, "007", T0 + 50, [], [], start=T0)
        self.assertEqual((got["slept"], got["total"], got["wall"]), (50, 50, 50))

    def test_a_turn_a_notification_opened_takes_no_earlier_prompt(self):
        # M001's turn ended (a released stop); a background notification opened the turn that activated the task (X19)
        lines = [L(0, "UserPromptSubmit", detail="logged M001", task=""), L(5, "Stop", decision="allow", task=""),
                 L(20, "UserPromptSubmit", detail="not logged: a notification", task=""),
                 L(22, "PreToolUse", tool="Bash", tid="w", task=""), L(23, "PostToolUse", tool="Bash", tid="w"),
                 L(30, "PostToolUse", tid="z")]
        self.assertEqual(numbers.owner_table(lines, "007", None, start=T0 + 23)["ids"], [])

    def receipt(self, ts="2026-10-17T21:01:40+03:00", accepted="docs only", bound='owner-ok: "the receipt\'s whole word"'):
        from tasks.core import format_verify_receipt
        block = format_verify_receipt([("verify", "x", 0, "ok")], "abc", "assertive", timestamp=ts,
                                      freshness={"verdict": "STALE", "round_fp": "a", "now_fp": "b",
                                                 "accepted_reason": accepted, "bound": bound})
        return "# 007 - x\n\n## Status\ndone\n\n## Verification Receipt\n\n" + block + "\n"

    def test_the_receipts_owner_ok_wins_over_the_journals(self):
        lane, td, _ = TheComposition.project(self)
        (td / "task.md").write_text(self.receipt(), encoding="utf-8")
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual(got["close"]["owner_ok"], "the receipt's whole word")          # X24

    def test_an_owner_ok_quoted_inside_the_accepted_reason_is_not_the_bound(self):
        got = numbers.receipt_close(self.receipt(accepted='the note quotes owner-ok: "x" as text'))
        self.assertEqual(got["owner_ok"], "the receipt's whole word")                    # X30

    def test_a_done_record_seconds_after_the_stamp_still_numbers_the_close(self):
        lane, td, close_t = TheComposition.project(self)
        with open(self.jdir / "enforcement.jsonl", "a") as f:       # a slow close: its `done` 2 s after the receipt
            f.write(json.dumps({"ts": iso(close_t + 2), "hook": "tasks", "reason": "done", "task": "007"}) + "\n")
        self.assertEqual(numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)["closure_no"], 1)   # X29


class TheMutationCloseGaps(_CliProject):
    def closed(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        return self.numbers_doc()

    def test_a_newest_close_missing_from_the_file_is_composed_and_shown(self):
        doc = self.closed()            # the file holds an earlier closure only: this close's write failed
        older = dict(doc["closures"][0], closed_at="2026-01-01T00:00:00+02:00")
        (self.task_dir() / "numbers.json").write_text(json.dumps(dict(doc, closures=[older])), encoding="utf-8")
        r = self.cli("numbers", "1")
        self.assertIn("the newest close is not in numbers.json", r.stdout)                # X27
        self.assertIn("forced: one", r.stdout)

    def test_entries_of_one_second_sorted_by_their_number(self):
        doc = self.closed()
        entry = doc["closures"][0]
        (self.task_dir() / "numbers.json").write_text(json.dumps(dict(doc, closures=[dict(entry, closure_no=2)])),
                                                      encoding="utf-8")
        orig = numbers.compose
        numbers.compose = lambda *a, **k: dict(entry, closure_no=1)
        try:
            numbers.write_closure(self.project / ".agent", self.task_dir(), "001")
        finally:
            numbers.compose = orig
        self.assertEqual([c["closure_no"] for c in self.numbers_doc()["closures"]], [1, 2])   # X31


class TheRoundOneFindings(_Tmp):
    """Task 236's impl round 1 (judge.md, "Triage — impl round 1"): Y1-Y13, each from the writers' own output."""

    HEAD = "# 007 - x\n\n## Status\ndone\n\n## Verification Receipt\n\n"

    def block(self, ts="2026-10-18T03:00:00+03:00", post_d6="", **fresh):
        from tasks.core import format_verify_receipt
        fresh = dict({"verdict": "STALE", "round_fp": "a", "now_fp": "b"}, **fresh)
        return format_verify_receipt([("verify", "x", 0, "ok")], "abc", "assertive", timestamp=ts, freshness=fresh,
                                     post_d6=post_d6)

    def test_y1_the_panel_line_split_only_at_the_writers_separators(self):
        from tasks.stop_rule import receipt_clause
        clause = receipt_clause({"rounds": 2, "allowed": 2, "risk": "assertive", "can": True,
                                 "changed": ["a.md", "b.py"]})
        accepted = 'the ruling "Not grave"; W1 routed'
        reason = 'he said "go"; then stop rule (D6): quoted'           # Y3: the owner-ok text names a stop rule
        got = numbers.receipt_close(self.HEAD + self.block(accepted_reason=accepted, bound=f'owner-ok: "{reason}"',
                                                           stop_rule=clause))
        self.assertEqual((got["accepted_reason"], got["owner_ok"], got["stop_rule"]), (accepted, reason, clause))

    def test_y2_a_post_panel_bound_read_from_the_receipt(self):
        bound = "bound to post-D6 PASS r7 on tree abc; since then only: PLAN.md"
        got = numbers.receipt_close(self.HEAD + self.block(accepted_reason="docs", bound=bound))
        self.assertEqual((got["accepted_reason"], got["bound"], got["owner_ok"], got["stop_rule"]),
                         ("docs", bound, None, None))

    def test_y1_post_panel_owner_oks_with_a_quoted_phrase(self):
        from tasks.post_d6 import receipt_line
        post = receipt_line([{"panel": "fp", "verdict": "FAIL", "owner_ok": 'first "a"; b'},
                             {"panel": "fp", "verdict": "PASS", "owner_ok": "second"}], "fp")
        got = numbers.receipt_close(self.HEAD + self.block(accepted_reason="x", post_d6=post))
        self.assertEqual(got["post_panel"], {"runs": 2, "verdict": "PASS", "owner_ok": ['first "a"; b', "second"]})

    def test_y9_an_unclosed_fence_above_does_not_hide_the_receipt(self):
        text = ("# 007 - x\n\n## Status\ndone\n\n## Notes\n```text\nan example never closed\n\n"
                "## Verification Receipt\n\n" + self.block(accepted_reason="x") + "\n")
        got = numbers.receipt_close(text)
        self.assertEqual((got or {}).get("stamp"), "2026-10-18T03:00:00+03:00")

    def two_closes_in_one_second(self):
        """Close 1, a reopen and close 2 in ONE second, in the order the CLI writes them (close records, then done)."""
        lane = self.d / ".agent"
        td = lane / "tasks" / "007-x"
        td.mkdir(parents=True)
        stamp = "2026-10-18T03:00:00+03:00"
        (td / "task.md").write_text(self.HEAD.replace("done", "done", 1) + "### " + stamp
                                    + " · risk assertive · commit abc\n- **Forced close, reason:** second\n",
                                    encoding="utf-8")
        t = numbers._iso_epoch(stamp)
        self.write("enforcement.jsonl", [
            {"ts": iso(t - 50), "hook": "tasks", "reason": "work", "task": "007"},
            {"ts": iso(t), "hook": "close", "task": "007", "reason": "stale close owner-ok",
             "command": 'owner-ok: "first life"'},
            {"ts": iso(t), "hook": "tasks", "reason": "done", "task": "007"},
            {"ts": iso(t), "hook": "tasks", "reason": "work", "task": "007"},
            {"ts": iso(t), "hook": "tasks", "reason": "done", "task": "007"}])
        self.write("hooks.jsonl", [hl(t - 49, "PostToolUse")])
        return lane, td, t

    def test_y5_the_newest_of_two_same_second_closes_takes_none_of_the_first(self):
        lane, td, t = self.two_closes_in_one_second()
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual((got["closure_no"], got["close"].get("owner_ok"), got["close"]["forced_reason"]),
                         (2, None, "second"))

    def test_y5_the_first_of_two_same_second_closes_by_its_stamp(self):
        lane, td, t = self.two_closes_in_one_second()
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30, end=t)
        self.assertEqual((got["closure_no"], got["close"].get("owner_ok"), got["window"]["start"]),
                         (1, "first life", iso(t - 50)))

    def test_y6_the_closes_own_sample_brackets_the_closing_gap(self):
        lines = [L(0, "UserPromptSubmit", susp=100, sts=T0, boot="aaaaaaaa"), L(10, "PreToolUse", tool="Bash", tid="c")]
        got = numbers.session_partition(lines, "007", T0 + 100, [], [], live=(150, T0 + 102, "aaaaaaaa"))
        self.assertEqual((got["slept"], got["by_kind"], got["total"]), (50, {"agent": 50}, 100))

    def test_y6_sleep_long_after_the_close_is_not_pulled_into_it(self):
        lines = [L(0, "UserPromptSubmit", susp=100, sts=T0, boot="aaaaaaaa"), L(10, "PreToolUse", tool="Bash", tid="c"),
                 L(4000, "PostToolUse", tool="Bash", tid="c", susp=3000, sts=T0 + 4000, boot="aaaaaaaa")]
        got = numbers.session_partition(lines, "007", T0 + 100, [], [])
        self.assertEqual((got["slept"], got["sleep_unplaced"]), (0, 0))

    def test_y6_a_sample_taken_long_after_the_close_is_not_used(self):
        lines = [L(0, "UserPromptSubmit", susp=100, sts=T0, boot="aaaaaaaa"), L(10, "PreToolUse", tool="Bash", tid="c")]
        got = numbers.session_partition(lines, "007", T0 + 100, [], [], live=(150, T0 + 300, "aaaaaaaa"))
        self.assertEqual((got["slept"], got["sleep_unplaced"]), (0, 0))

    def test_y6_the_live_sample_is_one_reading_of_this_machine(self):
        susp, sts, boot = numbers._live_sample()
        self.assertIsInstance(susp, int)
        self.assertLess(abs(sts - time.time()), 5)
        self.assertRegex(boot, r"^[0-9a-f]{8}$")

    def test_y5_a_done_the_journal_did_not_keep_falls_back_to_the_time(self):
        lane = self.d / ".agent"
        td = lane / "tasks" / "007-x"
        td.mkdir(parents=True)
        stamp = "2026-10-18T03:00:00+03:00"
        t = numbers._iso_epoch(stamp)
        (td / "task.md").write_text(self.HEAD + "### " + stamp + " · risk assertive · commit abc\n"
                                    "- **Forced close, reason:** second\n", encoding="utf-8")
        self.write("enforcement.jsonl", [                         # close 1, a reopen, close 2 whose `done` is lost
            {"ts": iso(t - 100), "hook": "tasks", "reason": "work", "task": "007"},
            {"ts": iso(t - 90), "hook": "tasks", "reason": "done", "task": "007"},
            {"ts": iso(t - 80), "hook": "tasks", "reason": "work", "task": "007"},
            {"ts": iso(t + 30), "hook": "tasks", "reason": "work", "task": "007"},           # then a reopen and
            {"ts": iso(t + 60), "hook": "close", "task": "007", "reason": "stale close owner-ok",  # a later close's
             "command": 'owner-ok: "a later life"'}])                                         # record (X22)
        self.write("hooks.jsonl", [hl(t - 79, "PostToolUse")])
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual((got["window"]["start"], got["close"].get("owner_ok")), (iso(t - 80), None))

    def test_y6_the_live_sample_reaches_the_closing_sessions_last_gap(self):
        lane, td, close_t = TheComposition.project(self)          # its last line: the close's own open Bash call
        before = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30, live=(40, close_t + 1, "aaaaaaaa"))
        self.assertEqual((before["time"]["slept"], got["time"]["slept"]), (0, 30))

    def test_x19_a_released_stop_ends_the_walk_when_the_prompt_line_is_missing(self):
        # the next turn's prompt line never reached the journal (its hook timed out): the stop still ends the search
        lines = [L(0, "UserPromptSubmit", detail="logged M001", task=""), L(5, "Stop", decision="allow", task=""),
                 L(22, "PreToolUse", tool="Bash", tid="w", task=""), L(23, "PostToolUse", tool="Bash", tid="w")]
        self.assertEqual(numbers.owner_table(lines, "007", None, start=T0 + 23)["ids"], [])

    def test_y7_a_sample_pair_after_the_close_adds_no_unplaced_sleep(self):
        lines = [L(0, "UserPromptSubmit", susp=100, sts=T0, boot="aaaaaaaa"), L(20, "Stop", decision="allow",
                                                                                 susp=100, sts=T0 + 20, boot="aaaaaaaa"),
                 L(30, "UserPromptSubmit", task="", susp=100, sts=T0 + 30, boot="aaaaaaaa"),
                 L(60, "Stop", decision="allow", task="", susp=130, sts=T0 + 60, boot="aaaaaaaa")]
        got = numbers.session_partition(lines, "007", T0 + 20, [], [])
        self.assertEqual(got["sleep_unplaced"], 0)

    def test_y8_codexs_shell_inside_a_review_span_is_judges(self):
        got = part([L(0, "UserPromptSubmit"), L(1, "PreToolUse", tool="exec_command", tid="x"),
                    L(11, "PostToolUse", tool="exec_command", tid="x"), L(12, "Stop", decision="allow")],
                   reviews=[(0, 20)])
        self.assertEqual(got["by_kind"], {"agent": 2, "judges": 10})

    def test_y10_a_journal_owner_ok_cut_without_its_quote_loses_its_label(self):
        lane, td, close_t = TheComposition.project(self)
        recs = [json.loads(x) for x in (self.jdir / "enforcement.jsonl").read_text().splitlines()]
        for r in recs:
            if r.get("reason") == "stale close owner-ok":
                r["command"] = 'owner-ok: "a long reason that the journal cut at two hundred'
        self.write("enforcement.jsonl", recs)
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual(got["close"]["owner_ok"], "a long reason that the journal cut at two hundred")

    def test_y12_a_turn_an_unlogged_prompt_opened_takes_no_earlier_prompt(self):
        lines = [L(0, "UserPromptSubmit", detail="logged M001", task=""), L(3, "PostToolUse", task=""),
                 L(20, "UserPromptSubmit", detail="not logged: a command of the user's", task=""),
                 L(22, "PreToolUse", tool="Bash", tid="w", task=""), L(23, "PostToolUse", tool="Bash", tid="w")]
        self.assertEqual(numbers.owner_table(lines, "007", None, start=T0 + 23)["ids"], [])

    def test_y12_a_duplicate_delivery_does_not_hide_the_logged_prompt(self):
        lines = [L(0, "UserPromptSubmit", detail="logged M001", task=""),
                 L(0, "UserPromptSubmit", detail="not logged: a duplicate", task=""),
                 L(2, "PreToolUse", tool="Bash", tid="w", task=""), L(3, "PostToolUse", tool="Bash", tid="w")]
        self.assertEqual(numbers.owner_table(lines, "007", None, start=T0 + 3)["ids"], ["M001"])

    def test_y13_the_closures_number_is_shown(self):
        out = numbers.format_closure({"closed_at": "2026-10-18T03:00:00+03:00", "closure_no": 2, "window": {},
                                      "time": None, "close": {}})
        self.assertTrue(out[0].startswith("closure #2 2026-10-18T03:00:00+03:00"), out[0])

    def test_the_time_with_no_section_is_named(self):
        # task 248: the echo's "" section — no gate open (the time after the last tick) or an open gate under no `##`
        # heading (its impl round 1, opus / codex) — printed as `(no section)`, not as an empty label
        out = numbers.format_closure({"closed_at": "x", "window": {}, "close": {},
                                      "time": {"total": 30, "sessions": 1, "by_kind": {"agent": 30},
                                               "by_section": {"?": {"agent": 1}, "Work": {"agent": 20},
                                                              "": {"verify": 9}}}})
        line = next(x for x in out if x.lstrip().startswith("by section:"))
        self.assertEqual(line, "  by section: ? 1s · Work 20s · (no section) 9s")


class TheRoundOneCloseFindings(_CliProject):
    def closed(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        return self.numbers_doc()

    def test_y4_a_same_second_close_missing_from_the_file_is_composed(self):
        doc = self.closed()
        t = numbers._iso_epoch(doc["closures"][0]["closed_at"])
        with open(self.journal, "a") as f:           # a reopen and a second close in that second, whose write failed
            for reason in ("work", "done"):
                f.write(json.dumps({"ts": iso(t), "hook": "tasks", "decision": "record", "reason": reason,
                                    "task": "001"}) + "\n")
        r = self.cli("numbers", "1")
        self.assertIn("the newest close is not in numbers.json", r.stdout)

    def test_y6_the_close_hands_its_own_sample_to_the_composition(self):
        self.closed()
        seen = []
        orig_c, orig_s = numbers.compose, numbers._live_sample
        numbers._live_sample = lambda: (7, 1, "abcdef01")

        def probe(*a, **k):
            seen.append(k.get("live"))
            return orig_c(*a, **k)
        numbers.compose = probe
        try:
            numbers.write_closure(self.project / ".agent", self.task_dir(), "001")
        finally:
            numbers.compose, numbers._live_sample = orig_c, orig_s
        self.assertEqual(seen, [(7, 1, "abcdef01")])

    def test_y5_the_closure_is_composed_under_the_task_lock(self):
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.closed()
        from tasks.filelock import lock_path_for
        seen = []
        orig = numbers.compose

        def probe(*a, **k):         # another process — a reopen in another session — tries the task's lock
            r = subprocess.run(["flock", "-n", str(lock_path_for(self.task_dir() / "task.md")), "true"])
            seen.append("held" if r.returncode else "free")
            return orig(*a, **k)
        numbers.compose = probe
        try:
            numbers.write_closure(self.project / ".agent", self.task_dir(), "001")
        finally:
            numbers.compose = orig
        self.assertEqual(seen, ["held"])


class TheRoundThreeFindings(_Tmp):
    """Task 237: task 236's impl round 2 (judge.md of 236, "Triage — impl round 2"), each from the writers' output."""

    HEAD = TheRoundOneFindings.HEAD
    block = TheRoundOneFindings.block
    two_closes_in_one_second = TheRoundOneFindings.two_closes_in_one_second

    def closed_project(self):
        """TheComposition's project with the close's `done` record where a close writes it (after its close records),
        naming the session the close ran in (`pid-1`, as `hl` writes it)."""
        lane, td, close_t = TheComposition.project(self)
        recs = [json.loads(x) for x in (self.jdir / "enforcement.jsonl").read_text().splitlines()]
        at = next(i for i, r in enumerate(recs) if r.get("hook") == "close") + 1
        recs.insert(at, {"ts": iso(close_t + 1), "hook": "tasks", "reason": "done", "task": "007",
                         "session_id": "pid-1"})
        self.write("enforcement.jsonl", recs)
        return lane, td, close_t

    def test_q1_a_reason_naming_a_verdict_is_not_the_verdict(self):
        for reason in ("re-sealed after TAMPER-GUARD-DEGRADED", "was TAIL-CERTIFIED before", "the tree — FRESH since",
                       "EXCLUDE-COVERS-CODE was fixed"):
            with self.subTest(reason=reason):
                got = numbers.receipt_close(self.HEAD + self.block(accepted_reason=reason))
                self.assertEqual(got["freshness"], "STALE")
        got = numbers.receipt_close(self.HEAD + self.block(verdict="NO-STAMP", accepted_reason="after TAIL-CERTIFIED"))
        self.assertEqual(got["freshness"], "NO-STAMP")

    def test_q3_two_same_second_lives_keep_their_own_verify_and_late_records(self):
        lane, td, t = self.two_closes_in_one_second()
        recs = [json.loads(x) for x in (self.jdir / "enforcement.jsonl").read_text().splitlines()]
        verify = {"ts": iso(t), "hook": "verify", "task": "007", "rc": 0, "how": "reused", "seconds": 0}
        recs.insert(1, dict(verify))                     # life 1's, before its close records
        recs.insert(5, dict(verify))                     # life 2's, after the reopen, before its `done`
        recs.append({"ts": iso(t + 5), "hook": "review", "task": "007", "kind": "single", "seat": "s", "round": 1,
                     "duration_ms": 1000, "status": "ok"})           # written after close 2
        self.write("enforcement.jsonl", recs)
        second = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        first = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30, end=t)
        self.assertEqual((first["verify"]["runs"], first["late_records"]), (1, 0))
        self.assertEqual((second["verify"]["runs"], second["late_records"]), (1, 1))

    def test_q4_the_closes_own_session_keeps_its_tail_against_a_newer_call(self):
        lane, td, close_t = self.closed_project()
        with open(self.jdir / "hooks.jsonl", "a") as f:     # another session: a newer open call on the same task
            for rec in (hl(close_t - 20, "UserPromptSubmit", hook="chat-log", host="h-2", sid="pid-2",
                           detail="logged M009"),
                        hl(close_t - 5, "PreToolUse", tool="Bash", tid="other", hook="task-gate", host="h-2",
                           sid="pid-2")):
                f.write(json.dumps(rec) + "\n")
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual(got["time"]["by_kind"], {"agent": 54, "owner-idle": 55, "verify": 5})

    def test_q4_a_notification_during_the_close_does_not_end_its_call(self):
        lane, td, close_t = self.closed_project()
        with open(self.jdir / "hooks.jsonl", "a") as f:
            f.write(json.dumps(hl(close_t - 5, "UserPromptSubmit", hook="chat-log",
                                  detail="not logged: a notification")) + "\n")
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual(got["time"]["by_kind"], {"agent": 39, "owner-idle": 55, "verify": 5})

    def test_sleep_over_a_long_gap_before_the_window_stays_there(self):
        # REJECTED (grok, Minor): placing sleep on the clipped gaps would put 40 s of this sleep in the window
        lines = [L(-100, "PostToolUse", tid="a", susp=100, sts=T0 - 100, boot="aaaaaaaa"),
                 L(-10, "PostToolUse", tid="b"), L(20, "PostToolUse", tid="c"),
                 L(40, "Stop", decision="allow", susp=180, sts=T0 + 40, boot="aaaaaaaa")]
        got = numbers.session_partition(lines, "007", T0 + 40, [], [], start=T0)
        self.assertEqual((got["slept"], got["total"]), (0, 40))

    def test_minor_a_post_panel_bound_is_shown_once(self):
        text = "\n".join(numbers.format_closure({"closed_at": "x", "window": {}, "time": None,
                                                "close": {"bound": "bound to post-D6 PASS r1 on tree t"}}))
        self.assertIn("bound to post-D6 PASS r1 on tree t", text)
        self.assertNotIn("bound: bound", text)

    def test_minor_a_json_false_is_not_exit_zero(self):
        self.assertEqual(numbers.verify_table([{"rc": False, "how": "ran", "seconds": 1}])["failed"], 1)

    def test_minor_a_receipt_with_no_done_record_is_still_a_first_close(self):
        lane, td, close_t = TheComposition.project(self)         # its journal holds no `done` record
        self.assertEqual(numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)["closure_no"], 1)

    def test_minor_the_dialog_index_keeps_the_deadline(self):
        log = "\n".join(f"line {i}" for i in range(20000))
        with self.assertRaises(numbers.OutOfTime):
            numbers.dialog_index(log, deadline=time.monotonic() - 1)


class TheRoundThreeCloseFindings(_CliProject):
    def test_q6_a_reopen_before_the_numbers_write_still_writes_the_closure(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        stamp = self.numbers_doc()["closures"][0]["closed_at"]
        (self.task_dir() / "numbers.json").unlink()          # as if the close's own write had not happened yet
        self.cli("work", "1", "--reopen")
        path = numbers.write_closure(self.project / ".agent", self.task_dir(), "001")
        self.assertIsNotNone(path)
        c = self.numbers_doc()["closures"][0]
        self.assertEqual((c["closed_at"], c["snapshot"], c["closure_no"]), (stamp, False, 1))

    def test_minor_tasks_numbers_reads_the_journal_once(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        driver = ("from tasks import numbers\n_c = [0]\n_o = numbers.read_enforcement\n"
                  "def _w(*a, **k):\n    _c[0] += 1\n    return _o(*a, **k)\n"
                  "numbers.read_enforcement = _w\nimport atexit\natexit.register(lambda: print('READS', _c[0]))")
        r = self.cli("numbers", "1", driver=driver)
        self.assertIn("READS 1", r.stdout)


class TheRoundThreeRoundOne(_Tmp):
    """Task 237's impl round 1 (its judge.md, "Triage — impl round 1"): K2-K4 and the Minor ones."""

    HEAD = TheRoundOneFindings.HEAD
    block = TheRoundOneFindings.block
    closed_project = TheRoundThreeFindings.closed_project

    def test_k2_an_empty_fingerprint_still_names_the_verdict(self):
        for fresh, want in (({"verdict": "TAMPER-GUARD-DEGRADED", "round_fp": "", "now_fp": "x"}, "TAMPER-GUARD-DEGRADED"),
                            ({"verdict": "TAMPER-GUARD-DEGRADED", "round_fp": "a", "now_fp": ""}, "TAMPER-GUARD-DEGRADED"),
                            ({"verdict": "STALE", "round_fp": "", "now_fp": "x"}, "STALE")):
            with self.subTest(fresh=fresh):
                got = numbers.receipt_close(self.HEAD + self.block(**fresh))
                self.assertEqual(got["freshness"], want)

    def test_k3_a_later_host_session_with_the_same_pid_is_not_the_close(self):
        lane, td, close_t = self.closed_project()
        with open(self.jdir / "hooks.jsonl", "a") as f:     # after a /clear: a new host session, the same pid-1
            for rec in (hl(close_t + 30, "UserPromptSubmit", hook="chat-log", host="h-3", detail="logged M010"),
                        hl(close_t + 31, "PostToolUse", host="h-3")):
                f.write(json.dumps(rec) + "\n")
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual(got["time"]["by_kind"], {"agent": 39, "owner-idle": 55, "verify": 5})

    def test_k4_a_review_written_after_the_done_in_its_second_is_late(self):
        lane, td, close_t = self.closed_project()
        recs = [json.loads(x) for x in (self.jdir / "enforcement.jsonl").read_text().splitlines()]
        at = next(i for i, r in enumerate(recs) if r.get("reason") == "done")
        recs[at]["ts"] = iso(close_t)                       # the close's `done` in its receipt's second
        recs.insert(at + 1, {"ts": iso(close_t), "hook": "review", "task": "007", "kind": "single", "seat": "t",
                             "round": 3, "duration_ms": 4000, "status": "ok"})
        recs.insert(at + 2, {"ts": iso(close_t), "hook": "verify", "task": "007", "rc": 0, "how": "ran", "seconds": 3})
        self.write("enforcement.jsonl", recs)
        got = numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)
        self.assertEqual((list(got["seats"]), got["verify"]["runs"], got["late_records"]), (["s"], 1, 3))

    def test_minor_on_the_time_path_late_stops_at_the_next_work(self):
        lane, td, close_t = TheComposition.project(self)         # no `done`: the time path
        with open(self.jdir / "enforcement.jsonl", "a") as f:
            f.write(json.dumps({"ts": iso(close_t + 100), "hook": "tasks", "reason": "work", "task": "007"}) + "\n")
            f.write(json.dumps({"ts": iso(close_t + 200), "hook": "review", "task": "007", "kind": "panel",
                                "seat": "s", "round": 3, "duration_ms": 1000}) + "\n")
        self.assertEqual(numbers.compose(lane, td, "007", deadline=time.monotonic() + 30)["late_records"], 1)

    def test_minor_sleep_proved_before_the_window_is_not_charged_to_it(self):
        # codex: the line at +40 carries a sample TAKEN at -10; the 80 s fell within [-100, -10], before the window
        lines = [L(-100, "PostToolUse", tid="a", susp=100, sts=T0 - 100, boot="aaaaaaaa"),
                 L(40, "Stop", decision="allow", susp=180, sts=T0 - 10, boot="aaaaaaaa")]
        got = numbers.session_partition(lines, "007", T0 + 40, [], [], start=T0)
        self.assertEqual((got["slept"], got["total"]), (0, 40))

    def test_minor_sleep_the_samples_allow_inside_the_window_stays_its_rule(self):
        # sonnet: samples at -100 and +20; the window part holds what it can (the documented rule)
        lines = [L(-100, "PostToolUse", tid="a", susp=0, sts=T0 - 100, boot="aaaaaaaa"),
                 L(20, "Stop", decision="allow", susp=100, sts=T0 + 20, boot="aaaaaaaa")]
        got = numbers.session_partition(lines, "007", T0 + 20, [], [], start=T0)
        self.assertEqual((got["slept"], got["total"]), (20, 20))


class TheRoundThreeRoundOneClose(_CliProject):
    def closed(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")
        self.cli("work", "done", "--force", "--reason", "one")
        return self.numbers_doc()

    def test_k1_a_close_whose_done_was_lost_is_not_composed_again(self):
        self.closed()
        kept = [x for x in self.journal.read_text().splitlines() if '"reason":"done"' not in x]
        self.journal.write_text("\n".join(kept) + "\n")
        r = self.cli("numbers", "1")
        self.assertNotIn("the newest close is not in numbers.json", r.stdout)

    def test_k4_tasks_numbers_counts_a_review_after_the_done_in_its_second(self):
        doc = self.closed()
        t = numbers._iso_epoch(doc["closures"][0]["closed_at"])
        with open(self.journal, "a") as f:
            f.write(json.dumps({"ts": iso(t), "hook": "review", "decision": "record", "reason": "review spend",
                                "task": "001", "kind": "single", "seat": "s", "round": 1, "duration_ms": 1000,
                                "status": "ok"}) + "\n")
        r = self.cli("numbers", "1")
        self.assertIn("(1 review / verify record(s) written after the last close", r.stdout)

    def test_k1_a_journal_read_out_of_time_claims_nothing_missing(self):
        self.closed()
        driver = ("from tasks import numbers\n"
                  "def _slow(*a, **k):\n    raise numbers.OutOfTime('enforcement.jsonl')\n"
                  "numbers.read_enforcement = _slow")
        r = self.cli("numbers", "1", driver=driver)
        self.assertNotIn("the newest close is not in numbers.json", r.stdout)
        self.assertNotIn("the records took too long", r.stdout)           # no recompose was tried
        self.assertIn("closure #1", r.stdout)

    def test_minor_a_non_dict_closure_in_the_file_is_skipped(self):
        doc = self.closed()
        (self.task_dir() / "numbers.json").write_text(json.dumps(dict(doc, closures=doc["closures"] + ["junk"])),
                                                      encoding="utf-8")
        r = self.cli("numbers", "1")
        self.assertIn("closure #1", r.stdout)


if __name__ == "__main__":
    unittest.main()
