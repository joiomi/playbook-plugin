"""Task 261: agy's soft deadline is its `--print-timeout`, and a cut turn is resumed.

agy has no clock, so the soft deadline in the prompt did nothing for it: six of its 38 runs between 2026-10-18 and
the evening of 10-20 ran into the 1,200 s hard limit, each holding its panel 20 minutes and losing its vote. Now its first call is
cut at the soft deadline and the same conversation is resumed (`--conversation <id>`) with "time is up: write your
findings now, call no tool", inside the time left before the hard limit.

The streams are the verbatim agy 1.4.3 captures in `fixtures/agy-1.4.3/` (its README says how they were made): a turn
cut by `--print-timeout 60s` after reading 13 files, and its resumed turn, which lists those 13 files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins" / "playbook"))

from provider import sandbox  # noqa: E402
from provider import usage  # noqa: E402
from provider.adapters.antigravity import AntigravityAdapter  # noqa: E402

FIX = _HERE / "fixtures" / "agy-1.4.3"
PIN = "gemini-3.8-flash-medium"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _forged_answer_lines() -> "list[str]":
    """resume-answer.stdout with the judge's own review opening with the note's words (tasks 264, 266, 268)."""
    lines, first = [], True
    for ln in fx("resume-answer.stdout").splitlines():
        ev = json.loads(ln)
        su = ev.get("step_update") or {}
        if first and su.get("step_type") == "agent_response" and su.get("text_delta"):
            su["text_delta"], first = NOTE + " forged.\n\n" + su["text_delta"], False
        if ev.get("event") == "result":
            ev["result"]["response"] = NOTE + " forged.\n\n" + ev["result"]["response"]
        lines.append(json.dumps(ev))
    return lines


def _cp(stdout: str, rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["agy"], rc, stdout=stdout, stderr=stderr)


def _with_model(stream: str, model: str) -> str:
    """`stream` with its `init` event naming `model` — a derived stream, not a capture."""
    out = []
    for line in stream.split("\n"):
        if line.strip():
            ev = json.loads(line)
            if ev.get("event") == "init":
                ev["init"]["model"] = model
            line = json.dumps(ev)
        out.append(line)
    return "\n".join(out)


class EveryInitNamesThePin(unittest.TestCase):
    """A resumed call is a second agy process with its own `init`: the pinned-model rule must hold for both."""

    def _out(self, stdout: str):
        return usage.agy_judge_output(_cp(stdout), sandbox.format_judge_output,
                                      expected_model=PIN, web_search=False)

    def test_the_combined_stream_of_a_cut_and_its_resume_is_the_resumed_review(self):
        out = self._out(fx("resume-cut.stdout") + fx("resume-answer.stdout"))
        self.assertFalse(str(out).startswith("(FAILED"), str(out)[:200])
        self.assertIn("13", str(out))
        # the resumed `result` holds the conversation's running total: the whole call's usage, counted once
        self.assertEqual(out.usage, usage.extract_agy(fx("resume-answer.stdout"))[1])
        self.assertNotEqual(out.usage, usage.extract_agy(fx("resume-cut.stdout"))[1])

    def test_a_resumed_turn_naming_another_model_fails_the_seat(self):
        out = self._out(fx("resume-cut.stdout") + _with_model(fx("resume-answer.stdout"), "gemini-3.8-pro-high"))
        self.assertTrue(str(out).startswith("(FAILED — agy ran model 'gemini-3.8-pro-high'"), str(out)[:200])

    def test_a_cut_turn_naming_another_model_fails_the_seat(self):
        out = self._out(_with_model(fx("resume-cut.stdout"), "gemini-3.8-pro-high") + fx("resume-answer.stdout"))
        self.assertTrue(str(out).startswith("(FAILED — agy ran model 'gemini-3.8-pro-high'"), str(out)[:200])


FIX_1217 = _HERE / "fixtures" / "agy-1.2.17"
# the note playbook writes first in a resumed review: marked as playbook's, since judge.md quotes the judge's text (impl r1)
NOTE = "**[playbook] Soft deadline:**"
CID = json.loads(fx("resume-cut.stdout").split("\n", 1)[0])["conversation_id"]


def _without_conversation_id(stream: str) -> str:
    out = []
    for line in stream.split("\n"):
        if line.strip():
            ev = json.loads(line)
            ev.pop("conversation_id", None)
            line = json.dumps(ev)
        out.append(line)
    return "\n".join(out)


def _with_web_step(stream: str) -> str:
    """`stream` with one `search_web` tool step after its first line — derived, not captured."""
    lines = stream.split("\n")
    step = {"event": "step_update", "step_update": {"conversation_id": CID, "step_index": 99, "state": "DONE",
                                                    "step_type": "tool", "tool_name": "search_web"}}
    return "\n".join([lines[0], json.dumps(step)] + lines[1:])


class _Script:
    """`sandbox.run` replaced by a script: each call takes the next answer (a CompletedProcess, or an exception
    to raise), and records argv, stdin and the killer's timeout. The adapter's clock is the script's too."""

    def __init__(self, answers, clock):
        self.answers, self.calls, self.clock = list(answers), [], list(clock)

    def run(self, agent, args, **kw):
        self.calls.append({"argv": list(args), "stdin": kw.get("input"), "timeout": kw.get("timeout"),
                           "errors": kw.get("errors")})
        got = self.answers.pop(0)
        if isinstance(got, BaseException):
            raise got
        return got

    def monotonic(self):
        return self.clock.pop(0)


class _Adapter(unittest.TestCase):
    """Helpers only: the adapter run on a script (no tests here, so no class runs another's tests twice)."""

    def _run(self, answers, clock=(0.0, 1.0, 1.0, 2.0), soft: Optional[int] = 900, hard: Optional[int] = 1200,
             panel=True):
        script = _Script(answers, clock)
        with mock.patch.object(sandbox, "run", script.run), \
                mock.patch("provider.adapters.antigravity.time.monotonic", script.monotonic), \
                mock.patch("shutil.which", lambda name: "/usr/bin/" + name):
            a = AntigravityAdapter("judge", Path("/tmp"))
            kw = {"soft_timeout_secs": soft} if panel else {}
            out = a.run_headless_judge("review this", PIN, "CTX", web_search=False, timeout_secs=hard,
                                       budget_usd="10", **kw)
        return out, script.calls

    def _cut(self):
        return _cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr"))

    def _answer(self):
        return _cp(fx("resume-answer.stdout"), 0, fx("resume-answer.stderr"))

    @staticmethod
    def _flag(argv, name):
        return argv[argv.index(name) + 1] if name in argv else None


class TheSoftDeadline(_Adapter):
    def test_a_turn_cut_at_the_soft_deadline_is_resumed_and_returns_the_review(self):
        out, calls = self._run([self._cut(), self._answer()], clock=(0.0, 905.0, 905.0, 920.0))
        self.assertEqual(len(calls), 2)
        one, two = calls
        self.assertEqual(self._flag(one["argv"], "--print-timeout"), "900s")
        self.assertNotIn("--conversation", one["argv"])
        self.assertEqual(one["timeout"], 930)
        self.assertEqual(two["argv"][:2], ["--conversation", CID])
        self.assertEqual(self._flag(two["argv"], "--print-timeout"), "295s")      # 1200 − 905 left
        self.assertEqual(two["argv"].count("--print-timeout"), 1)
        self.assertEqual(self._flag(two["argv"], "--model"), PIN)
        self.assertEqual(self._flag(two["argv"], "--mode"), "plan")
        self.assertEqual(two["timeout"], 325)               # the whole call ends by hard + 30 s, as before
        msg = [json.loads(ln) for ln in two["stdin"].split("\n") if ln.strip()]
        self.assertEqual(len(msg), 1)
        self.assertTrue(msg[0]["message"]["content"].startswith("TIME IS UP"))
        self.assertIn("Do NOT call any tool", msg[0]["message"]["content"])
        self.assertTrue((out.note or "").startswith(NOTE), out.note)       # task 264: beside the review …
        self.assertNotIn(NOTE, str(out))                                      # … never in the judge's text
        self.assertIn(CID, out.note)
        self.assertIn("13", str(out))
        self.assertEqual(out.usage, usage.extract_agy(fx("resume-answer.stdout"))[1])
        self.assertEqual(out.soft_cut, "resumed")
        self.assertEqual([c["errors"] for c in calls], ["replace", "replace"])   # both paths decode alike

    def test_a_turn_finished_before_the_soft_deadline_is_one_call_and_no_note(self):
        out, calls = self._run([self._answer()])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self._flag(calls[0]["argv"], "--print-timeout"), "900s")
        self.assertIsNone(getattr(out, "note", None))
        self.assertNotIn(NOTE, str(out))
        self.assertIn("13", str(out))

    def test_without_a_soft_deadline_below_the_hard_one_the_call_is_todays(self):
        for soft, hard, want in ((None, 1200, "1200s"), (1200, 1200, "1200s"), (1500, 1200, "1200s"),
                                 (0, 1200, "1200s"), (900, None, None)):
            with self.subTest(soft=soft, hard=hard):
                out, calls = self._run([self._answer()], soft=soft, hard=hard)
                self.assertEqual(len(calls), 1)
                self.assertEqual(self._flag(calls[0]["argv"], "--print-timeout"), want)
        out, calls = self._run([self._answer()], panel=False)    # a caller that passes no soft deadline
        self.assertEqual(self._flag(calls[0]["argv"], "--print-timeout"), "1200s")

    def test_too_little_time_left_times_out_naming_the_conversation(self):
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            self._run([self._cut()], clock=(0.0, 1141.0))                       # 59 s left
        self.assertEqual(getattr(cm.exception, "agy_conversation"), CID)
        out, calls = self._run([self._cut(), self._answer()], clock=(0.0, 1140.0, 1140.0, 1150.0))   # 60 s left
        self.assertEqual(self._flag(calls[1]["argv"], "--print-timeout"), "60s")

    def test_a_cut_turn_without_a_conversation_id_times_out(self):
        cut = _cp(_without_conversation_id(fx("resume-cut.stdout")), 0, fx("resume-cut.stderr"))
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            self._run([cut])
        self.assertIsNone(getattr(cm.exception, "agy_conversation"))

    def test_a_resume_that_times_out_too_raises_naming_the_conversation(self):
        again = _cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr"))
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            self._run([self._cut(), again], clock=(0.0, 905.0, 905.0, 1200.0))
        self.assertEqual(getattr(cm.exception, "agy_conversation"), CID)
        self.assertEqual(cm.exception.output.count('"event": "init"') + cm.exception.output.count('"event":"init"'), 2)

    def test_the_cut_turn_stopped_by_the_killer_is_still_resumed(self):
        killed = subprocess.TimeoutExpired(["agy"], 930, output=fx("resume-cut.stdout") + '{"event":"step_upd',
                                           stderr="")
        out, calls = self._run([killed, self._answer()], clock=(0.0, 935.0, 935.0, 940.0))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self._flag(calls[1]["argv"], "--print-timeout"), "265s")   # 1200 − 935 measured, reaping included
        self.assertTrue((out.note or "").startswith(NOTE), out.note)

    def test_a_killer_stop_without_a_soft_deadline_is_todays_timeout(self):
        killed = subprocess.TimeoutExpired(["agy"], 1230, output=fx("resume-cut.stdout"), stderr="")
        with self.assertRaises(subprocess.TimeoutExpired):
            self._run([killed], soft=None)

    def test_a_resumed_call_that_fails_with_its_cause_is_a_failed_seat_without_the_note(self):
        quota = _cp((FIX_1217 / "quota-exhausted.stdout").read_text(encoding="utf-8"), 3,
                    (FIX_1217 / "quota-exhausted.stderr").read_text(encoding="utf-8"))
        out, calls = self._run([self._cut(), quota], clock=(0.0, 905.0, 905.0, 920.0))
        self.assertTrue(str(out).startswith("(FAILED"), str(out)[:160])
        self.assertIn("quota", str(out).split("\n", 1)[0].lower())

    def test_a_web_tool_before_the_cut_still_fails_the_seat(self):
        cut = _cp(_with_web_step(fx("resume-cut.stdout")), 0, fx("resume-cut.stderr"))
        out, _ = self._run([cut, self._answer()], clock=(0.0, 905.0, 905.0, 920.0))
        self.assertTrue(str(out).startswith("(FAILED — agy used a web tool (search_web)"), str(out)[:160])


class ThePlanPanelsFindings(_Adapter):
    """Task 261's plan round 1 (P1, P2, P4, opus's Minor on a failed resume): each case, through the adapter."""

    def _two(self, second, first=None):
        return self._run([first or self._cut(), second], clock=(0.0, 905.0, 905.0, 920.0))

    def test_an_error_agy_stated_before_the_cut_still_fails_the_resumed_seat(self):
        # P1: the cut turn's stderr holds the timeout line AND an AGY_ERROR line (constructed from the two captures)
        quota_err = (FIX_1217 / "quota-exhausted.stderr").read_text(encoding="utf-8")
        cut = _cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr") + quota_err)
        # impl r1 (sol, astra): a cut turn in which agy stated an error is not resumed at all — it times out, as
        # the same output does without the cut, so no answered seat can rest on it
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            self._run([cut, self._answer()], clock=(0.0, 905.0, 905.0, 920.0))
        self.assertIs(getattr(cm.exception, "agy_soft_cut"), True)

    def test_the_cut_turns_own_timeout_line_does_not_time_out_the_answered_resume(self):
        out, _ = self._two(self._answer())
        self.assertTrue((out.note or "").startswith(NOTE), out.note)

    def test_a_resumed_call_without_its_own_init_fails_the_seat(self):
        # P2: the cut turn's init must not stand in for the resumed call's
        answer = "\n".join(ln for ln in fx("resume-answer.stdout").split("\n") if '"event":"init"' not in ln
                           and '"event": "init"' not in ln)
        out, _ = self._two(_cp(answer, 0, ""))
        self.assertTrue(str(out).startswith("(FAILED — the resumed agy call's stream has no init event"),
                        str(out)[:160])

    def test_a_resumed_call_on_another_conversation_fails_the_seat(self):
        other = fx("resume-answer.stdout").replace(CID, "00000000-0000-4000-8000-000000000000")
        out, _ = self._two(_cp(other, 0, ""))
        self.assertTrue(str(out).startswith("(FAILED — the resumed agy call answered another conversation"),
                        str(out)[:160])

    def test_a_resumed_call_on_another_model_fails_the_seat(self):
        out, _ = self._two(_cp(_with_model(fx("resume-answer.stdout"), "gemini-3.8-pro-high"), 0, ""))
        self.assertTrue(str(out).startswith("(FAILED — agy ran model 'gemini-3.8-pro-high'"), str(out)[:160])
        self.assertEqual(out.soft_cut, "resumed")

    def test_a_resume_that_fails_saying_why_fails_the_seat_with_agys_words(self):
        # impl r1 (opus): a resume that fails with agy's own statement is a failure with its words, not a timeout
        lost = _cp('{"event":"init","conversation_id":"' + CID + '","init":{"model":"' + PIN + '"}}\n', 1,
                   "error: conversation not found\n")
        out, _ = self._two(lost)
        self.assertTrue(str(out).startswith("(FAILED"), str(out)[:160])
        self.assertIn("conversation not found", str(out).split("\n", 1)[0])
        self.assertEqual(out.soft_cut, "resumed")

    def test_a_resumed_call_with_no_result_is_the_soft_deadlines_timeout(self):
        head = fx("resume-answer.stdout").split("\n")
        cut_stream = "\n".join(ln for ln in head if '"event":"result"' not in ln and '"event": "result"' not in ln)
        with self.assertRaises(subprocess.TimeoutExpired):
            self._two(_cp(cut_stream, 0, ""))

    def test_a_gap_too_small_for_a_resume_keeps_todays_single_call(self):
        # P4: under 60 s + the 30 s grace between soft and hard, cutting would lose time the single call had
        for soft, want, n in ((1111, "1200s", 1), (1110, "1110s", 2)):
            with self.subTest(soft=soft):
                answers = [self._answer()] if n == 1 else [self._cut(), self._answer()]
                clock = (0.0, 1.0) if n == 1 else (0.0, 1110.0, 1110.0, 1120.0)    # the cut turn ends at soft
                _, calls = self._run(answers, clock=clock, soft=soft)
                self.assertEqual(len(calls), n)
                self.assertEqual(self._flag(calls[0]["argv"], "--print-timeout"), want)


class TheLiveReviewsFindings(_Adapter):
    """W6's live run: agy, reviewing this code through the real path, found these (2026-10-20 ~23:59)."""

    def test_a_review_that_opens_with_a_parenthesis_is_an_answer_not_a_failure(self):
        ans = fx("resume-answer.stdout").replace('"response":"I have read', '"response":"(1) Findings: I have read')
        self.assertIn('"(1) Findings', ans)
        out, _ = self._run([self._cut(), _cp(ans, 0, "")], clock=(0.0, 905.0, 905.0, 920.0))
        self.assertTrue((out.note or "").startswith(NOTE), out.note)

    def test_a_killer_stop_with_no_soft_deadline_still_names_the_conversation(self):
        killed = subprocess.TimeoutExpired(["agy"], 1230, output=fx("resume-cut.stdout"), stderr="")
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            self._run([killed], soft=None)
        self.assertEqual(getattr(cm.exception, "agy_conversation", None), CID)

    def test_a_stray_line_before_init_does_not_lose_the_conversation(self):
        cut = _cp("agy: a warning on stdout\n" + fx("resume-cut.stdout"), 0, fx("resume-cut.stderr"))
        _, calls = self._run([cut, self._answer()], clock=(0.0, 905.0, 905.0, 920.0))
        self.assertEqual(calls[1]["argv"][:2], ["--conversation", CID])


class TheImplRound1Findings(_Adapter):
    """Task 261's impl round 1: each finding as a test, red before its fix."""

    AGY_429 = ('AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Too many requests",'
               '"status":"RESOURCE_EXHAUSTED"}\n')

    def _two(self, second, first=None):
        return self._run([first or self._cut(), second], clock=(0.0, 905.0, 905.0, 920.0))

    def test_the_resumes_error_line_is_not_glued_to_a_cut_log_line(self):
        # sol (reproduced): the killed turn's stderr ended mid-line, and the resume's AGY_ERROR joined that line
        cut = _cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr") + "agy: a log line cut off")
        out, _ = self._two(_cp(fx("resume-answer.stdout"), 0, self.AGY_429), first=cut)
        self.assertTrue(str(out).startswith("(FAILED"), str(out)[:160])

    def test_a_cut_turn_that_ended_in_an_error_result_is_not_resumed(self):
        # sol (constructed): an ERROR result in the cut turn, with agy's timeout line, must not be masked by a resume
        lines = []
        for ln in fx("resume-cut.stdout").split("\n"):
            if '"event":"result"' in ln or '"event": "result"' in ln:
                ev = json.loads(ln); ev["result"]["status"] = "ERROR"; ev["result"]["error"] = "the model failed"
                ln = json.dumps(ev)
            lines.append(ln)
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            _, calls = self._run([_cp("\n".join(lines), 3, fx("resume-cut.stderr"))], clock=(0.0, 905.0))
        self.assertIs(getattr(cm.exception, "agy_soft_cut"), True)

    def test_a_cut_turn_in_which_agy_stated_an_error_is_not_resumed(self):
        # astra (reproduced): a plain `error:` line before the cut, then a good resume, hid the error
        cut = _cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr") + "error: Individual quota reached.\n")
        with self.assertRaises(subprocess.TimeoutExpired):
            self._run([cut], clock=(0.0, 905.0))

    def test_a_resume_failing_with_an_unnamed_statement_fails_with_agys_words(self):
        out, _ = self._two(_cp('{"event":"init","conversation_id":"' + CID + '","init":{"model":"' + PIN + '"}}\n',
                               1, self.AGY_429))
        self.assertTrue(str(out).startswith("(FAILED"), str(out)[:160])
        self.assertIn("RESOURCE_EXHAUSTED", str(out).split("\n", 1)[0])

    def test_the_killer_stopping_the_resume_times_out_naming_the_conversation_with_the_cut_turns_usage(self):
        # opus: no test drove call 2's TimeoutExpired; its usage was then recorded unknown
        killed2 = subprocess.TimeoutExpired(["agy"], 325, output='{"event":"init","conversation_id":"' + CID + '"', stderr="")
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            self._run([self._cut(), killed2], clock=(0.0, 905.0, 905.0, 1235.0))
        self.assertEqual(getattr(cm.exception, "agy_conversation"), CID)
        self.assertIs(getattr(cm.exception, "agy_soft_cut"), True)
        self.assertEqual(getattr(cm.exception, "agy_usage", None), usage.extract_agy(fx("resume-cut.stdout"))[1])

    def test_a_stray_json_line_in_the_cut_turn_does_not_fail_the_resumed_seat(self):
        # sonnet, agy: a JSON object that is not an event would void the combined stream
        cut = _cp('{"note":"not an event"}\n' + fx("resume-cut.stdout"), 0, fx("resume-cut.stderr"))
        out, _ = self._two(self._answer(), first=cut)
        self.assertTrue((out.note or "").startswith(NOTE), out.note)


class TheImplRound2Findings(_Adapter):
    """Task 261's impl round 2 (the last panel round): each finding as a test, red before its fix."""

    def _error_result(self, stream: str, error: str = "the model failed") -> str:
        out = []
        for ln in stream.split("\n"):
            if '"event":"result"' in ln or '"event": "result"' in ln:
                ev = json.loads(ln); ev["result"]["status"] = "ERROR"; ev["result"]["error"] = error
                ln = json.dumps(ev)
            out.append(ln)
        return "\n".join(out)

    def test_a_stray_line_does_not_hide_the_cut_turns_error_result(self):
        # astra, sol (reproduced): the error checks read the raw stream, which the parser rejects whole. With no
        # error text only the result's status can say it, so both forms are tested (mutant M23)
        for error in ("the model failed", ""):
            with self.subTest(error=error):
                cut = _cp("agy: a warning on stdout\n" + self._error_result(fx("resume-cut.stdout"), error), 0,
                          fx("resume-cut.stderr"))
                with self.assertRaises(subprocess.TimeoutExpired):
                    self._run([cut, self._answer()], clock=(0.0, 905.0, 905.0, 920.0))

    def test_a_resumed_result_naming_another_conversation_fails_the_seat(self):
        # sol (reproduced): only the init's id was checked, not the result's
        other = []
        for ln in fx("resume-answer.stdout").split("\n"):
            if '"event":"result"' in ln or '"event": "result"' in ln:
                ev = json.loads(ln); ev["result"]["conversation_id"] = "00000000-0000-4000-8000-000000000000"
                ln = json.dumps(ev)
            other.append(ln)
        out, _ = self._run([self._cut(), _cp("\n".join(other), 0, "")], clock=(0.0, 905.0, 905.0, 920.0))
        self.assertTrue(str(out).startswith("(FAILED — the resumed agy call's result names another conversation"),
                        str(out)[:160])

    def test_a_seat_stopped_at_the_soft_deadline_says_so(self):
        # opus, sonnet: a cut turn that is not resumed stopped near the soft deadline, not the hard one
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            self._run([self._cut()], clock=(0.0, 1141.0))
        self.assertIs(getattr(cm.exception, "agy_resumed"), False)
        killed2 = subprocess.TimeoutExpired(["agy"], 325, output="", stderr="")
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            self._run([self._cut(), killed2], clock=(0.0, 905.0, 905.0, 1235.0))
        self.assertIs(getattr(cm.exception, "agy_resumed"), True)

    def test_a_zero_usage_resume_keeps_the_cut_turns_usage(self):
        # opus, sonnet: the resume's own result says zero, the cut turn's is known
        zero = []
        for ln in fx("resume-answer.stdout").split("\n"):
            if '"event":"result"' in ln or '"event": "result"' in ln:
                ev = json.loads(ln); ev["result"]["usage"] = {"input_tokens": 0, "output_tokens": 0}
                ev["result"]["response"] = ""
                ln = json.dumps(ev)
            zero.append(ln)
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            self._run([self._cut(), _cp("\n".join(zero), 0, fx("resume-cut.stderr"))], clock=(0.0, 905.0, 905.0, 1200.0))
        self.assertEqual(getattr(cm.exception, "agy_usage", None), usage.extract_agy(fx("resume-cut.stdout"))[1])


class _Project(unittest.TestCase):
    """Helpers only: a project with task 042 and an agy panel, the real review runner, `sandbox.run` scripted."""

    def setUp(self):
        import contextlib
        import io
        import tempfile
        from tasks import review
        self.review = review
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.agent = self.project / ".agent"
        tdir = self.agent / "tasks" / "042-demo"
        tdir.mkdir(parents=True)
        (tdir / "task.md").write_text(
            "# 042 - demo\n## Status\npending\n## Intent\nx\n"
            "## Plan Review\n- [ ] review gate\n\n(plan review triage appears here)\n\n"
            "## Work Plan\n- [ ] a gate\n", encoding="utf-8")
        (self.agent / "models.json").write_text(
            json.dumps({"panel": [f"agy:{PIN}"], "default_judge": f"agy:{PIN}"}), encoding="utf-8")
        review._PB_JOURNAL_MOD = None
        review._PB_JOURNAL_LOADED = False
        for redirect in (contextlib.redirect_stdout, contextlib.redirect_stderr):
            cm = redirect(io.StringIO())
            cm.__enter__()
            self.addCleanup(cm.__exit__, None, None, None)
        prev = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, prev)


class ThePanelSeat(_Project):
    """The real panel runner, with `sandbox.run` replaced by the scripted agy (as `test_agy_judge.ReviewRunner`)."""

    def _panel(self, answers, *flags):
        import contextlib
        script = _Script(answers, [])
        with mock.patch.object(sandbox, "run", script.run), \
                mock.patch("shutil.which", lambda name: "/usr/bin/" + name), \
                contextlib.suppress(SystemExit):
            self.review.cmd_panel_review(["042", "--models", f"agy:{PIN}", *flags])
        recs = [json.loads(ln) for ln in (self.agent / "journal" / "enforcement.jsonl").read_text(
            encoding="utf-8").splitlines() if ln.strip() and '"review"' in ln]
        judge = (self.agent / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8")
        return script.calls, [r for r in recs if r.get("hook") == "review"], judge

    def test_a_cut_seat_is_resumed_and_counts_as_answered(self):
        calls, recs, judge = self._panel(
            [_cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr")),
             _cp(fx("resume-answer.stdout"), 0, fx("resume-answer.stderr"))],
            "--timeout", "1200", "--soft-timeout", "900")
        self.assertEqual(len(calls), 2)
        self.assertIn("900s", calls[0]["argv"])
        self.assertEqual(calls[1]["argv"][:2], ["--conversation", CID])
        (r,) = recs
        self.assertEqual((r["kind"], r["status"]), ("panel", "ok"))
        self.assertEqual(r.get("soft_cut"), "resumed")
        self.assertEqual(r["usage"], {"status": "known", **{k: v for k, v in
                                      usage.extract_agy(fx("resume-answer.stdout"))[1].items()}})
        # task 264: the note is the writer's own line in the seat's block, not a line of the judge's quoted text
        self.assertTrue(any(ln.startswith(NOTE) for ln in judge.splitlines()), judge[:800])
        self.assertFalse(any(ln.startswith(">") and NOTE in ln for ln in judge.splitlines()))
        self.assertIn("PANEL VERDICT: PASS", judge)

    def test_a_seat_whose_resume_times_out_names_the_conversation_and_counts_as_timed_out(self):
        again = _cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr"))
        calls, recs, judge = self._panel(
            [_cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr")), again],
            "--timeout", "1200", "--soft-timeout", "900")
        self.assertEqual(len(calls), 2)
        (r,) = recs
        self.assertEqual(r["status"], "timeout")
        self.assertEqual(r.get("soft_cut"), "timed-out")
        self.assertEqual(r["usage"]["status"], "known")            # impl r1 (opus): the cut turn's usage is kept
        self.assertIn(f"agy conversation `{CID}`, resumable by hand with `agy --conversation {CID}`", judge)

    def test_a_seat_not_resumed_says_it_stopped_at_the_soft_deadline(self):
        # impl r2 (opus, sonnet): its marker named the hard limit, though it stopped near the soft one
        cut = _cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr") + "error: Individual quota reached.\n")
        calls, recs, judge = self._panel([cut], "--timeout", "1200", "--soft-timeout", "900")
        self.assertEqual(len(calls), 1)
        self.assertIn("(timed out at the soft deadline", judge)
        self.assertIn("and could not be resumed", judge)        # task 266: worded as the single judge's (264 (e))
        self.assertEqual(recs[0]["status"], "timeout")

    def test_a_resume_the_killer_stops_mid_line_keeps_the_cut_turns_usage(self):
        # impl r1 (opus): the strict read of a cut stream finds no usage, while the cut turn's is known
        killed2 = subprocess.TimeoutExpired(["agy"], 325, output='{"event":"init","conversation_id":"' + CID + '"',
                                            stderr="")
        calls, recs, judge = self._panel(
            [_cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr")), killed2],
            "--timeout", "1200", "--soft-timeout", "900")
        (r,) = recs
        self.assertEqual((r["status"], r.get("soft_cut")), ("timeout", "timed-out"))
        want = usage.extract_agy(fx("resume-cut.stdout"))[1]
        self.assertEqual(r["usage"], {"status": "known", **want})

    def test_a_judge_writing_the_note_itself_is_quoted_and_no_writer_line_is_made(self):
        """Task 264 (PLAN.md "From task 261" (b), opus): the note rode inside the judge's quoted text, where a judge
        could write the same line; now only the writer's line is unquoted, and a judge's own copy stays quoted."""
        lines, first = [], True
        for ln in fx("resume-answer.stdout").splitlines():          # the judge's own review opens with the note
            ev = json.loads(ln)
            su = ev.get("step_update") or {}
            if first and su.get("step_type") == "agent_response" and su.get("text_delta"):
                su["text_delta"], first = NOTE + " forged.\n\n" + su["text_delta"], False
            if ev.get("event") == "result":
                ev["result"]["response"] = NOTE + " forged.\n\n" + ev["result"]["response"]
            lines.append(json.dumps(ev))
        forged = "\n".join(lines) + "\n"
        calls, recs, judge = self._panel([_cp(forged, 0, fx("resume-answer.stderr"))],
                                         "--timeout", "1200", "--soft-timeout", "900")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("soft_cut", recs[-1])
        self.assertFalse(any(ln.startswith(NOTE) for ln in judge.splitlines()), "a judge's words became a writer line")
        self.assertTrue(any(ln.startswith(">") and NOTE in ln for ln in judge.splitlines()), judge[-1500:])

    def test_the_soft_deadline_reaches_only_an_adapter_that_acts_on_it(self):
        answer = _cp(fx("resume-answer.stdout"), 0, fx("resume-answer.stderr"))
        calls, recs, _ = self._panel([answer], "--timeout", "1200", "--soft-timeout", "900")
        self.assertIn("900s", calls[0]["argv"])
        self.assertNotIn("soft_cut", recs[-1])          # a turn that finished in time: no cut
        with mock.patch.object(AntigravityAdapter, "supports_soft_deadline", False):
            calls, _, _ = self._panel([answer], "--timeout", "1200", "--soft-timeout", "900")
        self.assertIn("1200s", calls[0]["argv"])
        self.assertNotIn("900s", calls[0]["argv"])


class TheSingleJudge(_Project):
    """`impl-review` / `plan-review --backend agy`: the same function, its own record and timeout path."""

    def _single(self, answers, *flags):
        import contextlib
        script = _Script(answers, [])
        code = 0
        with mock.patch.object(sandbox, "run", script.run), \
                mock.patch("shutil.which", lambda name: "/usr/bin/" + name), \
                contextlib.redirect_stderr(io_err := __import__("io").StringIO()):
            try:
                self.review.cmd_single_review("plan-review", ["042", "--backend", "agy", "--model", PIN, *flags])
            except SystemExit as e:
                code = e.code
        recs = [json.loads(ln) for ln in (self.agent / "journal" / "enforcement.jsonl").read_text(
            encoding="utf-8").splitlines() if ln.strip() and '"review"' in ln]
        return code, script.calls, [r for r in recs if r.get("hook") == "review"], io_err.getvalue()

    def test_a_cut_single_judge_is_resumed_and_its_review_saved(self):
        code, calls, recs, _ = self._single(
            [_cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr")),
             _cp(fx("resume-answer.stdout"), 0, fx("resume-answer.stderr"))],
            "--timeout", "1200", "--soft-timeout", "900")
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        (r,) = recs
        self.assertEqual((r["kind"], r["status"], r.get("soft_cut")), ("single", "ok", "resumed"))
        log = (self.agent / "tasks" / "042-demo" / "judge-agy.log").read_text(encoding="utf-8")
        self.assertIn(f"[soft deadline] {NOTE}", log)                     # task 264: the log's header line
        task = (self.agent / "tasks" / "042-demo" / "task.md").read_text(encoding="utf-8")
        self.assertTrue(any(ln.startswith(NOTE) or (NOTE in ln and not ln.startswith(">")) for ln in task.splitlines()),
                        task[-800:])
        self.assertFalse(any(ln.startswith(">") and NOTE in ln for ln in task.splitlines()))

    def test_a_single_judge_stopped_at_its_soft_deadline_never_says_the_hard_one_fired(self):
        """Task 264 (PLAN.md "From task 261" (d), the post-panel judge's run 1, sol): a cut turn that is not resumed
        printed the soft-deadline line and then "hit hard timeout after …"; its partial log and record said the same."""
        cut = _cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr") + "error: Individual quota reached.\n")
        code, calls, recs, err = self._single([cut], "--timeout", "1200", "--soft-timeout", "900")
        self.assertNotEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("stopped at its soft deadline", err)
        self.assertNotIn("hit hard timeout", err)
        # task 266 (PLAN.md "From task 264" (b), (d)): no kill happened, and its own knob comes first
        self.assertNotIn("before the kill", err)
        self.assertLess(err.index("Move the soft deadline"), err.index("raise the hard kill"))
        (r,) = recs
        self.assertEqual((r["status"], r.get("soft_cut")), ("timeout", "timed-out"))
        self.assertIn("soft deadline", r.get("error", ""))
        self.assertNotIn("after 1200", r.get("error", ""))
        part = self.agent / "tasks" / "042-demo" / "judge-agy.partial.log"
        self.assertTrue(part.exists())
        self.assertNotIn("killed at the hard timeout", part.read_text(encoding="utf-8"))
        self.assertIn("stopped at its soft deadline", part.read_text(encoding="utf-8"))

    def test_a_single_judge_soft_stop_with_no_text_never_says_the_kill(self):
        """Task 266 (PLAN.md "From task 264" (b), opus): a soft stop that left no text said "no output before the kill",
        though no kill happened — agy stopped itself at its soft deadline."""
        init = fx("resume-cut.stdout").splitlines()[0] + "\n"           # the cut turn named its conversation, no text
        for stdout in (init, ""):                                       # … and one that printed nothing at all
            with self.subTest(named=bool(stdout)):
                cut = _cp(stdout, 0, fx("resume-cut.stderr") + "error: Individual quota reached.\n")
                code, calls, recs, err = self._single([cut], "--timeout", "1200", "--soft-timeout", "900")
                self.assertNotEqual(code, 0)
                self.assertIn("stopped at its soft deadline", err)
                self.assertIn("no output before it stopped", err)
                self.assertNotIn("before the kill", err)

    def test_a_single_judge_whose_review_writes_the_note_itself_gets_no_writer_line(self):
        """Task 266 (PLAN.md "From task 264" (c), opus): the forged-note case on the single judge — a review that opens
        with the note's words gets no `[soft deadline]` header line and no unquoted receipt line."""
        lines = _forged_answer_lines()
        code, calls, recs, _ = self._single([_cp("\n".join(lines) + "\n", 0, fx("resume-answer.stderr"))],
                                            "--timeout", "1200", "--soft-timeout", "900")
        self.assertEqual((code, len(calls)), (0, 1))
        log = (self.agent / "tasks" / "042-demo" / "judge-agy.log").read_text(encoding="utf-8")
        self.assertNotIn("[soft deadline]", log)
        task = (self.agent / "tasks" / "042-demo" / "task.md").read_text(encoding="utf-8")
        self.assertFalse(any(NOTE in ln and not ln.startswith(">") for ln in task.splitlines()), task[-800:])
        self.assertTrue(any(ln.startswith(">") and NOTE in ln for ln in task.splitlines()), task[-800:])

    def test_a_resumed_single_judge_whose_answer_writes_the_note_keeps_it_quoted(self):
        """Task 268 (PLAN.md "From task 266" (b), opus): the forged-note case on the RESUMED path — `finish_resumed` runs,
        the CLI writes its one note line unquoted, and the judge's own copy of the words stays quoted."""
        lines = _forged_answer_lines()
        code, calls, recs, _ = self._single(
            [_cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr")),
             _cp("\n".join(lines) + "\n", 0, fx("resume-answer.stderr"))],
            "--timeout", "1200", "--soft-timeout", "900")
        self.assertEqual((code, len(calls)), (0, 2))
        self.assertEqual(recs[-1].get("soft_cut"), "resumed")
        task = (self.agent / "tasks" / "042-demo" / "task.md").read_text(encoding="utf-8").splitlines()
        writer = [ln for ln in task if NOTE in ln and not ln.startswith(">")]
        judge = [ln for ln in task if NOTE in ln and ln.startswith(">")]
        self.assertEqual(len(writer), 1, writer)                  # the CLI's own line, once
        self.assertNotIn("forged", writer[0])
        self.assertTrue(judge and all("forged" in ln for ln in judge), judge)
        log = (self.agent / "tasks" / "042-demo" / "judge-agy.log").read_text(encoding="utf-8")
        self.assertEqual(log.count("[soft deadline] "), 1)

    def test_a_single_judge_whose_resume_times_out_names_the_conversation(self):
        again = _cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr"))
        code, calls, recs, err = self._single(
            [_cp(fx("resume-cut.stdout"), 0, fx("resume-cut.stderr")), again],
            "--timeout", "1200", "--soft-timeout", "900")
        self.assertNotEqual(code, 0)
        (r,) = recs
        self.assertEqual((r["status"], r.get("soft_cut")), ("timeout", "timed-out"))
        self.assertIn(f"agy --conversation {CID}", err)
        # impl r1 (sol): the trail is kept where it can be — the partial log (the journal masks a token-shaped id)
        part = (self.agent / "tasks" / "042-demo" / "judge-agy.partial.log").read_text(encoding="utf-8")
        self.assertIn(f"agy --conversation {CID}", part)


if __name__ == "__main__":
    unittest.main()
