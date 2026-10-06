#!/usr/bin/env python3
"""The agy (Antigravity) JUDGE path against agy 1.2.17 (task 111).

Every fixture under tests/fixtures/agy-1.2.17/ is a VERBATIM capture of agy's
stdout/stderr (2026-10-05; the README there names each command) — except the two
pairs named `*.constructed-not-captured.*` (a depleted credit balance and a
mid-turn error could not be provoked). `quota-exhausted.*` is REAL: the exam of
task 111 ran into the 5-hour limit, and the end of that call's stream replaced
the pair I had constructed (whose wording turned out wrong). The parser recognizes exactly these envelopes; numbers are copied from
agy's own JSON, never derived. Stdlib unittest.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(_HERE.parent))

from provider import sandbox, usage  # noqa: E402
from provider.adapters import antigravity  # noqa: E402
from provider.adapters.antigravity import AntigravityAdapter  # noqa: E402
from provider.usage import JudgeOutput  # noqa: E402
from tasks import models_check as mc  # noqa: E402
from tasks import review  # noqa: E402

FIX = _HERE / "fixtures" / "agy-1.2.17"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _cp(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess(["agy"], rc, stdout=stdout, stderr=stderr)


def _result(stdout: str) -> dict:
    """The terminal `result` object of a captured stream (test-side reader, so the
    expected numbers come from the fixture file and not from a literal I typed)."""
    last = None
    for ln in stdout.splitlines():
        if ln.strip():
            ev = json.loads(ln)
            if ev.get("event") == "result":
                last = ev["result"]
    return last


def _adapter(root="/tmp/proj"):
    return AntigravityAdapter(session_id="judge", project_root=Path(root))


def _no_web() -> str:
    """The one-line steering the seat gets when web search is off (read at call
    time so a test fails on its own assertion while the constant does not exist)."""
    return antigravity._NO_WEB_TOOLS


def _run_judge(result=None, *, model="gemini-3.8-flash-high", timeout=90, prompt="REVIEW",
               context="CTX", raises=None, web_search=False, clock=None, adapter=None):
    """Drive run_headless_judge with `sandbox.run` replaced; return (output, captured)."""
    captured = {}

    def fake_run(binary, args, **kw):
        captured.update(binary=binary, args=list(args), kw=kw)
        if raises is not None:
            raise raises
        return result

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch("shutil.which", return_value="/usr/bin/agy"))
        stack.enter_context(mock.patch("provider.sandbox.run", side_effect=fake_run))
        if clock is not None:                      # (start, end) of the wall clock around the call
            stack.enter_context(mock.patch.object(antigravity.time, "monotonic", side_effect=list(clock)))
        out = (adapter or _adapter()).run_headless_judge(
            prompt, model, context, web_search=web_search, timeout_secs=timeout, budget_usd="10")
    return out, captured


# ── (a) invocation shape ─────────────────────────────────────────────────────
class JudgeInvocationShape(unittest.TestCase):
    def test_judge_argv_carries_no_prompt_and_stdin_is_one_ndjson_line(self):
        inv = _adapter().headless_argv("REVIEW", "gemini-3.8-flash-high", context="CTX",
                                       structured=True)
        self.assertEqual(inv.argv, ["--input-format", "stream-json", "--output-format", "stream-json",
                                    "--model", "gemini-3.8-flash-high", "--mode", "plan"])
        self.assertNotIn("--print", inv.argv)      # `-p` is a string flag: it would eat the next token
        self.assertNotIn("-p", inv.argv)
        self.assertTrue(inv.stdin.endswith("\n"))
        self.assertEqual(inv.stdin.count("\n"), 1)
        msg = json.loads(inv.stdin)
        self.assertEqual(msg, {"event": "user",
                               "message": {"role": "user", "content": "CTX\n\n---\n\nREVIEW"}})

    def test_a_pin_is_a_whole_model_id(self):
        a = _adapter()
        self.assertNotIn("--model", a.headless_argv("P", None, structured=True).argv)
        argv = a.headless_argv("P", " gemini-3.1-pro-high ", structured=True).argv
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.1-pro-high")
        self.assertNotIn("--effort", argv)                # the id carries the effort; agy gets no other
        # `<base>:<effort>` is refused, with the id to use instead (it cannot be verified: with
        # `--model gemini-3.8-flash --effort high` agy's stream names only `gemini-3.8-flash`)
        for bad in ("gemini-3.8-flash:high", "gemini-3.8-flash-high:high", "gemini-3.8-flash-low:high",
                    "gemini-3.8-flash:turbo", ":high", "gemini-3.8-flash:", "two words"):
            with self.assertRaises(ValueError, msg=bad) as ctx:
                a.headless_argv("P", bad, structured=True)
            self.assertIn("agy models", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            a.headless_argv("P", "gemini-3.8-flash:high", structured=True)
        self.assertIn("gemini-3.8-flash-high", str(ctx.exception))     # names the id it probably meant

    def test_judge_context_rides_stdin(self):
        self.assertEqual(AntigravityAdapter.context_transport(), "stdin")

    def test_non_judge_shape_is_unchanged(self):
        # The agent/subagent path is out of this task's scope: byte-for-byte as before.
        inv = _adapter().headless_argv("REVIEW", None, context="CTX")
        self.assertEqual(inv.argv, ["--add-dir", str(Path("/tmp/proj")), "--print", "CTX\n\n---\n\nREVIEW"])
        self.assertIsNone(inv.stdin)
        inv = _adapter().headless_argv("JUST", "gemini-3.8-flash-high", context="", bare=True)
        self.assertEqual(inv.argv, ["--add-dir", str(Path("/tmp/proj")), "--print", "JUST"])

    def test_run_passes_stdin_pin_plan_mode_and_the_print_timeout(self):
        out, cap = _run_judge(_cp(fx("success-pong.stdout")), timeout=90)
        self.assertEqual(cap["binary"], "agy")
        self.assertEqual(cap["args"], ["--input-format", "stream-json", "--output-format", "stream-json",
                                       "--model", "gemini-3.8-flash-high", "--mode", "plan",
                                       "--print-timeout", "90s"])
        self.assertEqual(json.loads(cap["kw"]["input"])["message"]["content"],
                         f"CTX\n\n---\n\n{_no_web()}\n\nREVIEW")
        self.assertIs(cap["kw"]["project_writable"], False)
        self.assertEqual(cap["kw"]["timeout"], 120)        # the killer sits 30 s above agy's own limit
        self.assertEqual(cap["kw"]["env"]["PLAYBOOK_SESSION_ID"], "judge")

    def test_unlimited_timeout_omits_the_flag_and_the_killer(self):
        _out, cap = _run_judge(_cp(fx("success-pong.stdout")), timeout=None)
        self.assertNotIn("--print-timeout", cap["args"])
        self.assertIsNone(cap["kw"]["timeout"])

    def test_the_sandbox_prepends_the_bypass_flag(self):
        inv = _adapter().headless_argv("P", "gemini-3.8-flash-high", structured=True)
        self.assertEqual(sandbox._compose_agent_argv("agy", inv.argv)[:3],
                         ["agy", "--dangerously-skip-permissions", "--input-format"])


# ── (b) big prompt ───────────────────────────────────────────────────────────
_FAKE_AGY = r'''#!/usr/bin/env python3
import hashlib, json, sys
raw = sys.stdin.buffer.read()
lines = [ln for ln in raw.split(b"\n") if ln.strip()]
msg = json.loads(lines[0])
content = msg["message"]["content"]
digest = hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()
out = {"lines": len(lines), "chars": len(content), "sha256": digest, "event": msg["event"],
       "argv_bytes": sum(len(a.encode()) for a in sys.argv[1:]), "argv": sys.argv[1:]}
model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
print(json.dumps({"event": "init", "conversation_id": "fake", "init": {"model": model, "cwd": "."}}))
print(json.dumps({"event": "result", "result": {"conversation_id": "fake", "status": "SUCCESS",
      "response": json.dumps(out), "duration_seconds": 0.1, "num_turns": 1,
      "usage": {"input_tokens": 11, "output_tokens": 7, "thinking_tokens": 0,
                "cache_read_tokens": 0, "total_tokens": 18}}}))
'''


def _big_prompt(n_bytes: int) -> str:
    unit = "gate ledger ș ț ă î — ✓ 日本 😀 \t\"quote\" \\back\\ \x07bell\n"
    body = unit * (n_bytes // len(unit.encode("utf-8")) + 1)
    return "MARKER-A " + body + " MARKER-Z"


class BigPrompt(unittest.TestCase):
    def test_stdin_line_round_trips_any_prompt(self):
        a = _adapter()
        cases = ["", "x", "line1\nline2\r\n", "\u2028\u2029 separators", "lone surrogate \ud83d here",
                 "nul \x00 byte", _big_prompt(300_000)]
        for prompt in cases:
            inv = a.headless_argv(prompt, "gemini-3.8-flash-high", context="", bare=True, structured=True)
            self.assertEqual(inv.stdin.count("\n"), 1, repr(prompt[:20]))
            self.assertTrue(inv.stdin.isascii())           # no dependence on the pipe's encoding
            self.assertEqual(json.loads(inv.stdin)["message"]["content"], prompt)

    def test_300kb_prompt_leaves_argv_small(self):
        from provider.argv_guard import argv_byte_error
        prompt = _big_prompt(300_000)
        self.assertGreater(len(prompt.encode("utf-8")), 300_000)
        inv = _adapter().headless_argv(prompt, "gemini-3.8-flash-high", context="C" * 50_000,
                                       structured=True)
        self.assertLess(max(len(a.encode("utf-8")) for a in inv.argv), 64)
        self.assertIsNone(argv_byte_error(inv.argv, "agy"))

    def test_windows_has_no_command_line_guard_on_the_stdin_path(self):
        a = _adapter()                    # pathlib refuses to build a path once os.name says "nt"
        with mock.patch.object(os, "name", "nt"):
            out, cap = _run_judge(_cp(fx("success-pong.stdout")), context="X" * 40_000, adapter=a)
        self.assertEqual(str(out), "PONG")
        self.assertGreater(len(cap["kw"]["input"]), 40_000)

    @unittest.skipIf(os.name == "nt", "the fake agy executable is a POSIX script")
    def test_prompt_physically_crosses_the_pipe_through_sandbox_run(self):
        prompt = _big_prompt(300_000)
        full = f"CTX\n\n---\n\n{_no_web()}\n\n{prompt}"
        want = hashlib.sha256(full.encode("utf-8", "surrogatepass")).hexdigest()
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "bin" / "agy"
            fake.parent.mkdir()
            fake.write_text(_FAKE_AGY, encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            proj = Path(d) / "proj"
            proj.mkdir()
            env = {"PATH": f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}",
                   # run the real sandbox.run but skip the OS wrapper (bwrap/seatbelt vary per host)
                   "PLAYBOOK_SANDBOXED": "1"}
            with mock.patch.dict(os.environ, env):
                out = AntigravityAdapter("judge", proj).run_headless_judge(
                    prompt, "gemini-3.8-flash-high", "CTX", web_search=False,
                    timeout_secs=60, budget_usd="10")
        got = json.loads(str(out))
        self.assertEqual(got["sha256"], want)
        self.assertEqual(got["chars"], len(full))
        self.assertEqual((got["lines"], got["event"]), (1, "user"))
        self.assertLess(got["argv_bytes"], 400)             # nothing of the prompt on argv
        self.assertEqual(got["argv"][0], "--dangerously-skip-permissions")
        self.assertEqual(out.usage, {"status": "known", "in": 11, "out": 7})


# ── (c) the stream parser, on captured output ────────────────────────────────
class ExtractAgy(unittest.TestCase):
    def test_plain_answer(self):
        raw = fx("success-pong.stdout")
        want = _result(raw)
        text, use, errors = usage.extract_agy(raw)
        self.assertEqual(text, "PONG\n")
        self.assertEqual(errors, [])
        self.assertEqual(use, {"status": "known", "in": want["usage"]["input_tokens"],
                               "out": want["usage"]["output_tokens"]})
        self.assertEqual((use["in"], use["out"]), (12555, 214))      # the capture's own numbers

    def test_tool_using_turn_reports_the_turn_totals(self):
        raw = fx("success-tools.stdout")
        want = _result(raw)
        text, use, errors = usage.extract_agy(raw)
        self.assertEqual(text, "CODE=HERON-5518\nCMD=42\n")
        self.assertEqual(errors, [])
        # the result event's totals, not any single step's numbers
        self.assertEqual((use["in"], use["out"]),
                         (want["usage"]["input_tokens"], want["usage"]["output_tokens"]))
        self.assertEqual((use["in"], use["out"]), (50748, 6650))
        steps = [json.loads(ln)["step_update"]["usage"]["input_tokens"] for ln in raw.splitlines()
                 if '"usage"' in ln and '"step_update"' in ln]
        self.assertGreater(len(steps), 1)
        self.assertNotIn(use["in"], steps)

    def test_parse_usage_recognizes_the_stream(self):
        self.assertEqual(usage.parse_usage(fx("success-pong.stdout")),
                         {"status": "known", "in": 12555, "out": 214})

    def test_error_result_is_an_error_with_no_text(self):
        text, use, errors = usage.extract_agy(fx("error-missing-event.stdout"))
        self.assertEqual(text, "")
        self.assertIsNone(use)                              # zeros are not a measurement
        self.assertEqual(errors, ['stream input message is missing the "event" field'])

    def test_stream_without_a_result_event_is_incomplete(self):
        cut = "\n".join(fx("success-pong.stdout").splitlines()[:-1]) + "\n"
        text, use, errors = usage.extract_agy(cut)
        self.assertEqual((text, use), ("", None))
        self.assertEqual(errors, ["incomplete agy event stream: no result event"])

    def test_auto_denied_command_names_the_denial(self):
        text, _use, errors = usage.extract_agy(fx("denied-command.stdout"))
        self.assertEqual(text, "")
        self.assertEqual(len(errors), 1)
        self.assertIn("RunCommand", errors[0])
        self.assertIn("auto-denied", errors[0])

    def test_all_zero_usage_is_unknown_never_a_measured_zero(self):
        # agy prints zeros for a turn that DID run (the print-timeout capture, num_turns 1)
        raw = fx("print-timeout.stdout")
        self.assertEqual(_result(raw)["num_turns"], 1)
        self.assertEqual(_result(raw)["usage"]["input_tokens"], 0)
        self.assertIsNone(usage.extract_agy(raw)[1])
        self.assertIsNone(usage.parse_usage(raw))

    def test_prose_and_other_envelopes_are_not_the_agy_stream(self):
        self.assertIsNone(usage.extract_agy("1. **Finding** — plain prose.\n"))
        self.assertIsNone(usage.extract_agy('{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}\n'))
        self.assertIsNone(usage.extract_agy(fx("success-pong.stdout") + "trailing noise\n"))
        self.assertIsNone(usage.extract_agy(""))

    def test_last_result_event_wins(self):
        raw = fx("error-missing-event.stdout") + fx("success-pong.stdout").splitlines()[-1] + "\n"
        text, use, errors = usage.extract_agy(raw)
        self.assertEqual((text, errors), ("PONG\n", []))
        self.assertEqual(use["in"], 12555)

    def test_the_stream_names_the_model_that_ran(self):
        self.assertEqual(usage.agy_stream_model(fx("success-pong.stdout")), "gemini-3.8-flash-high")
        self.assertIsNone(usage.agy_stream_model(fx("error-missing-event.stdout")))   # unpinned run: no field
        self.assertIsNone(usage.agy_stream_model("prose"))


# ── (c..i) the judge output rule ─────────────────────────────────────────────
class JudgeOutputRule(unittest.TestCase):
    def test_success_returns_the_review_carrying_usage(self):
        out, _ = _run_judge(_cp(fx("success-tools.stdout")))
        self.assertIsInstance(out, JudgeOutput)
        self.assertEqual(str(out), "CODE=HERON-5518\nCMD=42")
        self.assertEqual(out.usage, {"status": "known", "in": 50748, "out": 6650})
        self.assertEqual(review._parse_judge_usage(out), {"status": "known", "in": 50748, "out": 6650})
        self.assertEqual(review._judge_status(out), "ok")

    def test_not_signed_in_fails_the_seat_and_names_the_sign_in(self):
        out, _ = _run_judge(_cp(fx("not-signed-in.stdout"), rc=1, stderr=fx("not-signed-in.stderr")))
        first = str(out).splitlines()[0]
        self.assertTrue(first.startswith("(FAILED — agy is not signed in"), first)
        self.assertIn("authentication required", first)
        self.assertTrue(mc.judge_failed(str(out)))
        self.assertEqual(review._judge_status(out), "fail")
        self.assertEqual(review._judge_error(str(out), "fail"), first)     # the journal's `error`
        self.assertIn("(FAILED — exit 1)", str(out))                       # the standard block follows
        self.assertIsNone(out.usage)

    def test_quota_exhausted_fails_the_seat_and_names_the_quota(self):
        # REAL capture (the exam's first halt): exit 3, status ERROR, an AGY_ERROR line.
        out, _ = _run_judge(_cp(fx("quota-exhausted.stdout"), rc=3, stderr=fx("quota-exhausted.stderr")),
                            model="gemini-3.1-pro-high")
        first = str(out).splitlines()[0]
        self.assertTrue(first.startswith(
            "(FAILED — agy quota exhausted: error: Individual quota reached."), first)
        self.assertIn("Resets in 34m13s.", first)             # agy's plain line, with the reset time
        self.assertNotIn("AGY_ERROR", first)
        self.assertTrue(mc.judge_failed(str(out)))
        self.assertEqual(review._judge_status(out), "fail")
        self.assertEqual(review._judge_error(str(out), "fail"), first)
        self.assertIn("(FAILED — exit 3)", str(out))
        # the turn ran five minutes before the limit: those tokens were spent and are recorded
        self.assertEqual(out.usage, {"status": "known", "in": 144619, "out": 30527})

    def test_depleted_credits_read_as_quota_too(self):
        # CONSTRUCTED (fixtures README): the binary's message inside the real quota envelope.
        stem = "credits-too-low.constructed-not-captured"
        out, _ = _run_judge(_cp(fx(stem + ".stdout"), rc=3, stderr=fx(stem + ".stderr")),
                            model="gemini-3.1-pro-high")
        first = str(out).splitlines()[0]
        self.assertTrue(first.startswith("(FAILED — agy quota exhausted"), first)
        self.assertIn("AI credits balance is too low", first)
        self.assertEqual(review._judge_status(out), "fail")

    def test_quota_cause_is_found_on_stderr_alone(self):
        out, _ = _run_judge(_cp("", rc=1, stderr="error: You have exhausted your quota on this model.\n"))
        self.assertTrue(str(out).startswith("(FAILED — agy quota exhausted"), str(out)[:80])

    NOISE = ("W1005 13:17:25.320057      85 cache.go:135] Cache(loadCodeAssistResponse): Singleflight "
             "refresh failed: error getting token source: You are not logged into Antigravity.\n"
             "E1005 13:17:25.320203      85 errorreport.go:224] error getting token source: You are not "
             "logged into Antigravity.\n")

    def _interrupted(self):
        """The exam's real mid-turn failure, rebuilt as a stream: exit 0, a status other than
        SUCCESS, agy's error text, a partial response (only the seat's rendered text was kept)."""
        line = json.loads(fx("success-pong.stdout").splitlines()[-1])
        line["result"].update(status="ERROR", response="FINDINGS:\nNONE\nEND FINDINGS",
                              error="The stream was interrupted. Please continue the task you were working on.")
        return "\n".join(fx("success-pong.stdout").splitlines()[:-1] + [json.dumps(line)]) + "\n"

    def test_log_noise_does_not_rename_another_failure(self):
        # impl panel r1 (opus): agy logs "You are not logged into Antigravity." while it signs in
        # silently. A call that then fails for ANOTHER reason must not be reported as a sign-in problem.
        out, _ = _run_judge(_cp(self._interrupted(), rc=0, stderr=self.NOISE))
        first = str(out).splitlines()[0]
        self.assertTrue(first.startswith("(FAILED — the judge CLI reported an error: The stream was interrupted"),
                        first)
        self.assertNotIn("not signed in", str(out).split("[partial text")[0])
        self.assertEqual(review._judge_error(str(out), "fail"), first)
        # the real quota stop with the same noise in front of it is still a quota stop
        out, _ = _run_judge(_cp(fx("quota-exhausted.stdout"), rc=3,
                                stderr=self.NOISE + fx("quota-exhausted.stderr")), model="gemini-3.1-pro-high")
        self.assertTrue(str(out).startswith("(FAILED — agy quota exhausted: error: Individual quota reached."),
                        str(out)[:90])

    def test_only_agys_error_lines_can_name_a_cause(self):
        for stderr in ("You have exhausted your quota on this model.\n",          # not an error: line
                       "a tool said: error: Individual quota reached.\n",         # mid-line
                       "I1005 15:22:57.688085 241 quota_manager.go:45] quota reached\n"):
            self.assertIsNone(usage.agy_failure_cause("", stderr), stderr)
        self.assertIn("quota exhausted", usage.agy_failure_cause("", "error: Individual quota reached.\n"))
        self.assertIn("quota exhausted", usage.agy_failure_cause(
            "", 'AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached. …"}\n'))
        self.assertIsNone(usage.agy_failure_cause(                    # the code alone is not a quota stop (r2)
            "", 'AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): …"}\n'))
        self.assertIn("not signed in", usage.agy_failure_cause(
            "", "Error: authentication required. Run 'agy' to log in, then retry.\n"))
        self.assertIn("not signed in", usage.agy_failure_cause(fx("not-signed-in.stdout"), ""))   # result.error

    def test_rejected_model_fails_the_seat_and_classifies_as_unavailable(self):
        out, _ = _run_judge(_cp(fx("error-model-rejected.stdout"), rc=1,
                                stderr=fx("error-model-rejected.stderr")), model="gemini-9.9-nope")
        first = str(out).splitlines()[0]
        self.assertTrue(first.startswith("(FAILED — agy rejects the model selection"), first)
        self.assertIn("gemini-9.9-nope", first)
        self.assertEqual(mc.classify_failure(str(out)), mc.MODEL_UNAVAILABLE)

    def test_a_review_quoting_the_failure_strings_stays_a_review(self):
        quoted = ("1. **Important** — the adapter matches the text `You have exhausted your quota on "
                  "this model.` and `authentication required. Run 'agy' to log in` and "
                  "`invalid model selection`; see provider/usage.py.\n")
        line = json.loads(fx("success-pong.stdout").splitlines()[-1])
        line["result"]["response"] = quoted
        raw = "\n".join(fx("success-pong.stdout").splitlines()[:-1] + [json.dumps(line)]) + "\n"
        out, _ = _run_judge(_cp(raw))
        self.assertEqual(str(out), quoted.strip())
        self.assertFalse(mc.judge_failed(str(out)))
        self.assertEqual(review._judge_status(out), "ok")
        self.assertEqual(mc.classify_failure(str(out)), mc.OTHER)

    def test_a_failure_for_another_reason_is_not_blamed_on_text_the_review_quoted(self):
        # W3 mutation check: searching the review text for a cause survived until this
        # test. The turn failed mid-way (constructed fixture); its PARTIAL text quotes
        # the quota and sign-in strings. The seat fails — but not "for quota".
        stem = "midturn-error.constructed-not-captured"
        line = json.loads(fx(stem + ".stdout").splitlines()[-1])
        line["result"]["response"] = ("1. **Important** — the message `You have exhausted your quota on "
                                      "this model.` and `authentication required` are matched in")
        raw = "\n".join(fx(stem + ".stdout").splitlines()[:-1] + [json.dumps(line)]) + "\n"
        out, _ = _run_judge(_cp(raw, rc=0, stderr=fx(stem + ".stderr")))
        first = str(out).splitlines()[0]
        self.assertTrue(first.startswith("(FAILED — the agy turn reported an error"), first)
        self.assertNotIn("quota exhausted", first)
        self.assertNotIn("not signed in", first)

    def test_stderr_noise_never_fails_a_review_that_succeeded(self):
        # W3 mutation check: applying the cause to a successful call survived until
        # this test. agy logs lines like this one while it signs in silently; if one
        # reaches stderr on a call that then answers, the review stands.
        noise = ("W1005 13:17:25.320057      85 cache.go:135] Cache(loadCodeAssistResponse): Singleflight "
                 "refresh failed: error getting token source: You are not logged into Antigravity.\n")
        out, _ = _run_judge(_cp(fx("success-pong.stdout"), rc=0, stderr=noise))
        self.assertEqual(str(out), "PONG")
        self.assertEqual(review._judge_status(out), "ok")

    def test_print_timeout_is_a_timeout_never_a_review(self):
        # agy exits 0 with status SUCCESS here; only its stderr line says the text is partial.
        self.assertEqual(_result(fx("print-timeout.stdout"))["status"], "SUCCESS")
        with self.assertRaises(subprocess.TimeoutExpired) as ctx:
            _run_judge(_cp(fx("print-timeout.stdout"), rc=0, stderr=fx("print-timeout.stderr")), timeout=8)
        self.assertEqual(ctx.exception.timeout, 8)
        self.assertEqual(ctx.exception.output, fx("print-timeout.stdout"))

    def test_print_timeout_marker_is_anchored_to_agys_own_line(self):
        self.assertTrue(usage.agy_print_timed_out(fx("print-timeout.stderr")))
        self.assertTrue(usage.agy_print_timed_out(
            "noise\n[agy] print timeout after 20m0s with turn in progress; returning partial output\n"))
        self.assertFalse(usage.agy_print_timed_out(""))
        self.assertFalse(usage.agy_print_timed_out(None))
        self.assertFalse(usage.agy_print_timed_out(
            "a tool printed: [agy] print timeout after 8s with turn in progress; returning partial output"))
        self.assertFalse(usage.agy_print_timed_out(fx("denied-command.stderr")))

    def test_partial_text_of_a_timed_out_stream_is_salvaged(self):
        lines = fx("success-pong.stdout").splitlines()
        cut = "\n".join(lines[:3]) + "\n" + lines[3][:40]          # killed mid-frame, after "PONG"
        for name in ("agy", "antigravity"):
            self.assertEqual(usage.salvage_text(name, cut), "PONG")
        # a self-timeout: complete stream, empty response → nothing readable, never the raw frames
        self.assertEqual(usage.salvage_text("agy", fx("print-timeout.stdout")), "")
        self.assertEqual(usage.salvage_text("agy", "plain prose"), "plain prose")

    def test_auto_denied_empty_response_is_a_failed_seat(self):
        out, _ = _run_judge(_cp(fx("denied-command.stdout"), rc=0, stderr=fx("denied-command.stderr")))
        self.assertTrue(str(out).startswith("(FAILED — "), str(out)[:60])
        self.assertIn("RunCommand", str(out))
        self.assertEqual(review._judge_status(out), "fail")
        self.assertEqual(out.usage, {"status": "known", "in": 12586, "out": 1760})   # the turn was billed

    def test_no_result_event_and_non_envelope_stdout_are_failed_seats(self):
        cut = "\n".join(fx("success-pong.stdout").splitlines()[:-1]) + "\n"
        for raw in (cut, '{"some":"other json"}\n', fx("success-pong.stdout") + "stray line\n"):
            out, _ = _run_judge(_cp(raw))
            self.assertTrue(str(out).startswith("(FAILED — "), str(out)[:80])
            self.assertTrue(mc.judge_failed(str(out)))

    def test_prose_without_the_stream_envelope_is_a_failed_seat(self):
        # impl panel r1 (convergent): the shared parser returns non-JSON prose verbatim (a CLI
        # that ignored the flag). For agy the envelope is how the model, the web tools and a
        # finished turn are known at all — without it there is nothing to accept.
        for raw in ("FINDINGS:\nNONE\nEND FINDINGS\n", "Welcome to agy 9.9\n1. **Important** — a finding\n"):
            out, _ = _run_judge(_cp(raw, rc=0))
            self.assertTrue(str(out).startswith("(FAILED — agy returned no stream-json envelope"), str(out)[:80])
            self.assertEqual(review._judge_status(out), "fail")
            self.assertIn(raw.splitlines()[0], str(out))            # kept as a diagnostic
        # … on the unpinned seat too
        out, _ = _run_judge(_cp("FINDINGS:\nNONE\nEND FINDINGS\n", rc=0), model=None)
        self.assertTrue(str(out).startswith("(FAILED — agy returned no stream-json envelope"), str(out)[:80])

    def test_empty_stdout_is_no_output(self):
        out, _ = _run_judge(_cp(""))
        self.assertEqual(str(out), "(no output)")

    def test_a_different_model_than_the_pin_fails_the_seat(self):
        out, _ = _run_judge(_cp(fx("success-pong.stdout")), model="gemini-3.1-pro-high")
        self.assertTrue(str(out).startswith("(FAILED — agy ran model 'gemini-3.8-flash-high'"), str(out)[:90])
        self.assertIn("gemini-3.1-pro-high", str(out).splitlines()[0])

    def test_no_pin_form_escapes_the_check(self):
        # impl panel r1 (codex-high reproduced it): a `<base>:<effort>` pin switched the check
        # off — `gemini-3.1-pro:high` accepted a flash stream. The form no longer exists: the
        # seat never starts (the panel reports the adapter's ValueError as an `(error: …)` seat).
        with self.assertRaises(ValueError):
            _run_judge(_cp(fx("success-pong.stdout")), model="gemini-3.1-pro:high")
        self.assertEqual(antigravity.pinned_model_id("gemini-3.8-flash-high"), "gemini-3.8-flash-high")
        self.assertIsNone(antigravity.pinned_model_id(None))
        with self.assertRaises(ValueError):
            antigravity.pinned_model_id("gemini-3.8-flash:high")

    def test_what_the_stream_names_is_the_model_value_agy_accepted(self):
        # Live check 2026-10-05 23:31 (task 111 record): `--model gemini-3.8-flash --effort high`
        # → init.model "gemini-3.8-flash". init.model ECHOES the accepted --model value; it is
        # compared with the value the seat passed, nothing more is read into it.
        lines = fx("success-pong.stdout").splitlines()
        init = json.loads(lines[0]); init["init"]["model"] = "gemini-3.8-flash"
        raw = "\n".join([json.dumps(init)] + lines[1:]) + "\n"
        out, _ = _run_judge(_cp(raw), model="gemini-3.8-flash-high")
        self.assertTrue(str(out).startswith("(FAILED — agy ran model 'gemini-3.8-flash', not the pinned"),
                        str(out)[:90])

    def test_a_pinned_seat_fails_when_the_stream_does_not_name_its_model(self):
        # impl panel r1 (sonnet, codex×2): no `init`, or an `init` without `model`, used to pass.
        lines = fx("success-pong.stdout").splitlines()
        no_init = "\n".join(lines[1:]) + "\n"
        init = json.loads(lines[0]); del init["init"]["model"]
        no_model = "\n".join([json.dumps(init)] + lines[1:]) + "\n"
        for raw in (no_init, no_model):
            out, _ = _run_judge(_cp(raw))
            first = str(out).splitlines()[0]
            self.assertTrue(first.startswith("(FAILED — agy did not report the model it ran"), first)
            self.assertIn("gemini-3.8-flash-high", first)
            self.assertEqual(review._judge_status(out), "fail")
            # an UNPINNED seat has nothing to compare: the review stands
            out, _ = _run_judge(_cp(raw), model=None)
            self.assertEqual(str(out), "PONG")

    def test_missing_cli(self):
        with mock.patch("shutil.which", return_value=None):
            out = _adapter().run_headless_judge("P", None, "", web_search=False, timeout_secs=5,
                                                budget_usd="10")
        self.assertEqual(out, "(error: agy not found on PATH)")


# ── plan panel P1 / P3 / P9: a call that looks fine and is not ───────────────
class LooksFineButIsNot(unittest.TestCase):
    MID = "midturn-error.constructed-not-captured"

    def test_midturn_error_on_exit_0_is_a_failed_seat(self):
        # CONSTRUCTED (fixtures README): agy documents that a stream "warns and continues".
        raw, err = fx(self.MID + ".stdout"), fx(self.MID + ".stderr")
        self.assertEqual(_result(raw)["status"], "SUCCESS")           # read naively: a review
        out, _ = _run_judge(_cp(raw, rc=0, stderr=err))
        self.assertTrue(str(out).startswith("(FAILED — "), str(out)[:80])
        self.assertEqual(review._judge_status(out), "fail")
        self.assertIn("first finding, complete", str(out))            # kept as a diagnostic
        # each signal alone is enough
        out, _ = _run_judge(_cp(raw, rc=0, stderr=""))                # the ERROR step
        self.assertTrue(str(out).startswith("(FAILED — "), str(out)[:80])
        out, _ = _run_judge(_cp(fx("success-pong.stdout"), rc=0, stderr=err))   # the AGY_ERROR line
        self.assertTrue(str(out).startswith("(FAILED — "), str(out)[:80])

    def test_agy_error_line_is_anchored_and_tool_errors_are_benign(self):
        self.assertEqual(usage.agy_stream_errors(fx("success-tools.stdout"), ""), [])
        self.assertEqual(usage.agy_stream_errors(fx("success-pong.stdout"),
                                                 "note: a tool printed AGY_ERROR: {} mid-line\n"), [])
        # a TOOL step in state ERROR is the tool's own failure (e.g. a refused write), not the turn's
        tool_err = json.dumps({"event": "step_update", "step_update": {
            "step_index": 2, "state": "ERROR", "step_type": "tool", "tool_name": "write_to_file",
            "tool_info": {"error": {"type": "TOOL_ERROR", "message": "read-only file system"}}}})
        lines = fx("success-pong.stdout").splitlines()
        raw = "\n".join(lines[:2] + [tool_err] + lines[2:]) + "\n"
        self.assertEqual(usage.agy_stream_errors(raw, ""), [])
        out, _ = _run_judge(_cp(raw))
        self.assertEqual(str(out), "PONG")

    def test_web_tool_use_fails_the_seat_when_web_search_is_off(self):
        raw = fx("web-tool.stdout")
        self.assertEqual(usage.agy_web_tool_calls(raw), ["search_web"])
        self.assertEqual(usage.agy_web_tool_calls(fx("success-tools.stdout")), [])
        out, cap = _run_judge(_cp(raw))
        first = str(out).splitlines()[0]
        self.assertTrue(first.startswith("(FAILED — agy used a web tool (search_web)"), first)
        self.assertEqual(review._judge_status(out), "fail")
        self.assertEqual(out.usage, {"status": "known", "in": 26490, "out": 721})
        self.assertIn(_no_web(), json.loads(cap["kw"]["input"])["message"]["content"])

    def test_web_tool_use_is_allowed_when_the_caller_asked_for_web_search(self):
        out, cap = _run_judge(_cp(fx("web-tool.stdout")), web_search=True)
        self.assertEqual(str(out), "VERSION=3.14.8")
        self.assertEqual(json.loads(cap["kw"]["input"])["message"]["content"], "CTX\n\n---\n\nREVIEW")

    def test_browser_tools_count_as_web_tools(self):
        for name in ("read_url_content", "open_browser_url", "browser_click_element",
                     "capture_browser_screenshot", "read_browser_page"):
            ev = json.dumps({"event": "step_update", "step_update": {
                "step_index": 2, "state": "DONE", "step_type": "tool", "tool_name": name}})
            raw = "\n".join(fx("success-pong.stdout").splitlines()[:2] + [ev]
                            + fx("success-pong.stdout").splitlines()[2:]) + "\n"
            self.assertEqual(usage.agy_web_tool_calls(raw), [name], name)

    def test_returning_at_the_limit_without_a_finished_result_is_a_timeout_even_without_agys_line(self):
        # agy's self-timeout capture (SUCCESS, empty text, all-zero usage) with its stderr line LOST:
        # the wall clock alone still says timeout.
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_judge(_cp(fx("print-timeout.stdout"), stderr=""), timeout=8, clock=(1000.0, 1008.0))
        # a stream cut before its result event, at the limit: timeout
        cut = "\n".join(fx("success-pong.stdout").splitlines()[:-1]) + "\n"
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_judge(_cp(cut, stderr=""), timeout=90, clock=(1000.0, 1090.0))
        # under the limit the same two are ordinary failed seats, not timeouts
        out, _ = _run_judge(_cp(cut, stderr=""), timeout=90, clock=(1000.0, 1089.0))
        self.assertTrue(str(out).startswith("(FAILED — "))

    def test_a_finished_review_that_arrives_at_the_limit_is_kept(self):
        # impl panel r1 (opus): the clock starts before the sandbox and agy start up, agy's own
        # limit does not — a COMPLETE result (SUCCESS, real usage) a few seconds "late" is a review.
        for clock in ((1000.0, 1090.0), (1000.0, 1093.5)):
            out, _ = _run_judge(_cp(fx("success-pong.stdout"), stderr=""), timeout=90, clock=clock)
            self.assertEqual(str(out), "PONG")
            self.assertEqual(out.usage, {"status": "known", "in": 12555, "out": 214})
        out, _ = _run_judge(_cp(fx("success-pong.stdout"), stderr=""), timeout=None, clock=(0.0, 99999.0))
        self.assertEqual(str(out), "PONG")
        # agy's own line still wins over a complete-looking result
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_judge(_cp(fx("success-pong.stdout"), stderr=fx("print-timeout.stderr")), timeout=8,
                       clock=(1000.0, 1001.0))


# ── plan panel P4: the subscription, not a key ───────────────────────────────
class SubscriptionNotAKey(unittest.TestCase):
    ENV = {"HOME": "/home/u", "PATH": "/usr/bin", "GEMINI_API_KEY": "k1", "GOOGLE_API_KEY": "k2",
           "GOOGLE_APPLICATION_CREDENTIALS": "/home/u/adc.json"}

    def test_judge_env_drops_the_api_keys(self):
        with mock.patch.dict(os.environ, self.ENV):
            _out, cap = _run_judge(_cp(fx("success-pong.stdout")))
        env = cap["kw"]["env"]
        self.assertNotIn("GEMINI_API_KEY", env)
        self.assertNotIn("GOOGLE_API_KEY", env)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)       # a billed path too (impl panel r2)
        self.assertEqual(env["HOME"], "/home/u")

    def test_probe_env_drops_the_api_keys(self):
        seen = {}

        def fake(argv, **kw):
            seen.update(kw)
            return _cp(fx("success-pong.stdout"))

        with mock.patch.dict(os.environ, self.ENV), \
                mock.patch.object(mc.subprocess, "run", side_effect=fake):
            mc.probe_agy_model("gemini-3.8-flash-high", timeout=30)
        self.assertNotIn("GEMINI_API_KEY", seen["env"])
        self.assertNotIn("GOOGLE_API_KEY", seen["env"])

    def test_credits_reader_on_the_captured_output(self):
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/agy"), \
                mock.patch.object(mc.subprocess, "run", return_value=_cp(fx("credits.stdout"))):
            self.assertEqual(mc.agy_credits(), 0)
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/agy"), \
                mock.patch.object(mc.subprocess, "run", return_value=_cp("nope", rc=1)):
            self.assertIsNone(mc.agy_credits())


# ── plan panel P2: three different "invalid model selection" errors ──────────
class ModelSelectionErrors(unittest.TestCase):
    def _failed(self, stem, model):
        out, _ = _run_judge(_cp(fx(stem + ".stdout"), rc=1, stderr=fx(stem + ".stderr")), model=model)
        return str(out)

    def test_only_an_unknown_id_classifies_as_model_unavailable(self):
        self.assertEqual(mc.classify_failure(self._failed("error-model-rejected", "gemini-9.9-nope")),
                         mc.MODEL_UNAVAILABLE)
        for stem, model in (("error-model-needs-effort", "gemini-3.8-flash"),
                            ("error-model-effort-conflict", "gemini-3.8-flash-low")):
            text = self._failed(stem, model)
            self.assertTrue(text.startswith("(FAILED — agy rejects the model selection"), text[:70])
            self.assertEqual(mc.classify_failure(text), mc.OTHER, stem)

    def test_probe_tells_the_three_apart(self):
        def probe(stem, model):
            with mock.patch.object(mc.subprocess, "run",
                                   return_value=_cp(fx(stem + ".stdout"), 1, fx(stem + ".stderr"))):
                return mc.probe_agy_model(model, timeout=30)[0]
        self.assertEqual(probe("error-model-rejected", "gemini-9.9-nope"), mc.GONE)
        self.assertEqual(probe("error-model-needs-effort", "gemini-3.8-flash"), mc.BAD_EFFORT)
        self.assertEqual(probe("error-model-effort-conflict", "gemini-3.8-flash-low"), mc.BAD_EFFORT)

    def test_an_effort_suffix_is_refused_at_parse_time(self):
        for spec in ("agy:gemini-3.8-flash-low:high", "agy:gemini-3.8-flash-high:high", "agy:gemini-3.8-flash:high"):
            self.assertIsNotNone(mc.spec_error(spec), spec)
        self.assertIsNone(mc.spec_error("agy:gemini-3.8-flash-high"))
        self.assertIsNone(mc.spec_error("agy"))


# ── judgebench wiring (plan panel P1 / P10 / P14) ────────────────────────────
class JudgebenchWiring(unittest.TestCase):
    def setUp(self):
        from bench.lib import runner, transport
        self.runner, self.transport = runner, transport

    def test_presets_name_the_two_gemini_seats(self):
        cands = self.runner.parse_candidates("gem-flash-high,gem-pro-high")
        self.assertEqual([(c.label, c.backend, c.variant) for c in cands],
                         [("gem-flash-high", "agy", "gemini-3.8-flash-high"),
                          ("gem-pro-high", "agy", "gemini-3.1-pro-high")])

    def test_transport_judges_the_agy_seat_as_a_stdin_seat(self):
        (cand,) = self.runner.parse_candidates("gem-flash-high")
        big = "x" * 150_000                                  # over the POSIX per-argument cap
        v = self.transport.seat_verdict(cand, big, self.runner.REPO_ROOT, platform_nt=False)
        self.assertEqual((v["transport"], v["fits"]), ("stdin", True), v)
        v = self.transport.seat_verdict(cand, big, self.runner.REPO_ROOT, platform_nt=True)
        self.assertEqual((v["transport"], v["fits"]), ("stdin", True), v)

    def test_quota_failure_halts_like_a_quota_refusal(self):
        for stem in ("quota-exhausted", "credits-too-low.constructed-not-captured"):
            out, _ = _run_judge(_cp(fx(stem + ".stdout"), rc=3, stderr=fx(stem + ".stderr")),
                                model="gemini-3.1-pro-high")
            self.assertTrue(self.runner.is_quota_exhausted(str(out), backend="agy"), stem)
            self.assertEqual(self.runner.classify(str(out), backend="agy"), ("dnf", False))
            self.assertEqual(self.runner.finish(str(out), timed_out=False, duration_ms=1, retries=0,
                                                backend="agy").note, self.runner.QUOTA_NOTE)
            # without the backend an agy seat is not matched by the generic wording lists
            self.assertFalse(self.runner.is_quota_exhausted(str(out)), stem)
        # not signed in is NOT a quota refusal
        out, _ = _run_judge(_cp(fx("not-signed-in.stdout"), 1, fx("not-signed-in.stderr")))
        self.assertFalse(self.runner.is_quota_exhausted(str(out), backend="agy"))

    def _live(self, buckets):
        calls = []

        def invoke(backend, variant, prompt, tree, timeout, budget):
            calls.append((backend, variant))
            return "FINDINGS\n(none)\n"

        r = self.runner.LiveRunner(self.runner.REPO_ROOT, invoke=invoke, quota_reader=lambda: buckets)
        (cand,) = self.runner.parse_candidates("gem-flash-high")
        pkg = type("P", (), {"prompt": "p"})()
        case = type("C", (), {"id": "c1"})()
        inv = r.invoke(case, cand, pkg, "/tmp", soft_timeout=1, hard_timeout=2)
        return inv, calls

    def test_an_interrupted_stream_is_a_transient_not_a_zero_finding_review(self):
        # The seat text of the exam's one `fail` (hf-007-r3, gem-pro-high), verbatim.
        seat = ("(FAILED — the judge CLI reported an error: The stream was interrupted. Please continue "
                "the task you were working on.)\n\n[partial text before the failure]\nFINDINGS:\nNONE\nEND FINDINGS")
        self.assertTrue(self.runner.is_transient_provider_error(seat, backend="agy"))
        self.assertFalse(self.runner.is_quota_exhausted(seat, backend="agy"))
        self.assertEqual(self.runner.classify(seat, backend="agy"), ("dnf", True))      # retried once, then --resume
        # … and of its last flash call (pb-039-r4): a 503 from the provider, verbatim first line
        seat503 = ("(FAILED — the judge CLI reported an error: API error (attempt 1): UNAVAILABLE (code 503): "
                   "The service is currently unavailable.)\n\n[partial text before the failure]\nFINDINGS:\nNONE\nEND FINDINGS")
        self.assertEqual(self.runner.classify(seat503, backend="agy"), ("dnf", True))
        review_text = "FINDINGS:\n1. the stream was interrupted is a string the adapter matches\nEND FINDINGS"
        self.assertFalse(self.runner.is_transient_provider_error(review_text, backend="agy"))   # only inside a failure envelope

    def test_quota_preflight_halts_before_the_call(self):
        low = [{"id": "gemini-weekly", "group": "Gemini Models", "window": "weekly",
                "remaining_fraction": 0.61, "reset_time": "2026-10-12T07:57:04Z"},
               {"id": "gemini-5h", "group": "Gemini Models", "window": "5h",
                "remaining_fraction": 0.02, "reset_time": "2026-10-05T17:57:04Z"},
               {"id": "3p-5h", "group": "Claude and GPT models", "window": "5h",
                "remaining_fraction": 0.0, "reset_time": "2026-10-05T15:16:12Z"}]
        inv, calls = self._live(low)
        self.assertEqual(calls, [])                           # no model turn was spent
        self.assertEqual((inv.status, inv.note), ("dnf", self.runner.QUOTA_NOTE))
        self.assertIn("2026-10-05T17:57:04Z", inv.raw)
        self.assertIn("5h", inv.raw)

    def test_quota_preflight_lets_a_healthy_or_unreadable_quota_through(self):
        ok = [{"id": "gemini-5h", "group": "Gemini Models", "window": "5h",
               "remaining_fraction": 0.5, "reset_time": "x"},
              {"id": "3p-5h", "group": "Claude and GPT models", "window": "5h",
               "remaining_fraction": 0.0, "reset_time": "y"}]    # another group's empty bucket is not ours
        for buckets in (ok, None, []):
            _inv, calls = self._live(buckets)
            self.assertEqual(calls, [("agy", "gemini-3.8-flash-high")], buckets)

    def test_validate_transport_takes_candidates(self):
        import io
        from bench import judgebench
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = judgebench.main(["corpus", "validate", "--transport",
                                  "--candidates", "gem-flash-high,gem-pro-high"])
        text = buf.getvalue()
        self.assertEqual(rc, 0, text[-600:])
        self.assertIn("gem-flash-high", text)
        self.assertIn("gem-pro-high", text)
        self.assertNotIn("sol-med", text)
        self.assertIn("19/19 cases fit every seat", text)


# ── impl panel round 2 ───────────────────────────────────────────────────────
def _pong_with(**result_updates) -> str:
    """The captured plain-answer stream with fields of its `result` event replaced,
    written the way agy's Go encoder writes it (non-ASCII raw)."""
    lines = fx("success-pong.stdout").splitlines()
    last = json.loads(lines[-1])
    last["result"].update(result_updates)
    return "\n".join(lines[:-1] + [json.dumps(last, ensure_ascii=False)]) + "\n"


_ZERO = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0}


class ImplPanelRound2(unittest.TestCase):
    def test_a_line_separator_inside_a_response_does_not_void_the_stream(self):
        # R2-A (opus): str.splitlines() also splits on U+0085, U+2028, U+2029; agy writes them raw.
        for ch in ("\u0085", "\u2028", "\u2029"):
            raw = _pong_with(response=f"1. first line{ch}second line\n")
            self.assertIn(ch, raw)                                   # really raw in the stream
            got = usage.extract_agy(raw)
            self.assertIsNotNone(got, repr(ch))
            self.assertEqual(got[0], f"1. first line{ch}second line\n")
            self.assertEqual(got[1], {"status": "known", "in": 12555, "out": 214})
            out, _ = _run_judge(_cp(raw))
            self.assertEqual(str(out), f"1. first line{ch}second line")
            self.assertEqual(review._judge_status(out), "ok")
            self.assertEqual(usage.salvage_text("agy", raw), f"1. first line{ch}second line")

    # CONSTRUCTED: the real quota capture's envelope with a rate-limit sentence in place of
    # "Individual quota reached …" — no rate-limit refusal was ever captured.
    RATE = "Rate limit exceeded. Retry in 12s."

    def _rate_limited(self):
        real = "Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 34m13s."
        return (fx("quota-exhausted.stdout").replace(real, self.RATE),
                fx("quota-exhausted.stderr").replace(real, self.RATE))

    def test_a_bare_resource_exhausted_is_not_a_quota_stop(self):
        # R2-B (opus): Google answers a short rate limit with the same RESOURCE_EXHAUSTED / 429.
        stdout, stderr = self._rate_limited()
        self.assertIn("RESOURCE_EXHAUSTED (code 429)", stderr)
        self.assertIsNone(usage.agy_failure_cause(stdout, stderr))
        out, _ = _run_judge(_cp(stdout, rc=3, stderr=stderr), model="gemini-3.1-pro-high")
        self.assertTrue(str(out).startswith(
            "(FAILED — agy reported: RESOURCE_EXHAUSTED (code 429): Rate limit exceeded. Retry in 12s.)"),
            str(out)[:110])
        self.assertIn("(FAILED — exit 3)", str(out))
        self.assertNotIn("quota exhausted", str(out))
        from bench.lib import runner
        self.assertFalse(runner.is_quota_exhausted(str(out), backend="agy"))
        self.assertEqual(runner.classify(str(out), backend="agy"), ("dnf", True))     # one retry, no halt

    def test_an_error_result_after_the_limit_is_a_failure_with_its_cause(self):
        # R2-F a (codex-medium): the captured quota stop, returned after the limit, was called a
        # timeout — and the exam would have missed its quota halt.
        out, _ = _run_judge(_cp(fx("quota-exhausted.stdout"), rc=3, stderr=fx("quota-exhausted.stderr")),
                            model="gemini-3.1-pro-high", timeout=90, clock=(1000.0, 1400.0))
        self.assertTrue(str(out).startswith("(FAILED — agy quota exhausted"), str(out)[:60])
        out, _ = _run_judge(_cp(fx("not-signed-in.stdout"), rc=1, stderr=fx("not-signed-in.stderr")),
                            timeout=90, clock=(1000.0, 1400.0))
        self.assertTrue(str(out).startswith("(FAILED — agy is not signed in"), str(out)[:60])

    def test_a_success_without_usage_is_never_a_finished_turn(self):
        # R2-F b / R2-D: agy's self-timeout shape (SUCCESS, all-zero usage) carrying partial text,
        # its stderr line lost, the clock UNDER the limit. Not a review — whatever the clock says.
        raw = _pong_with(response="1. **Important** — a finding that was still being wri", usage=dict(_ZERO))
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_judge(_cp(raw, stderr=""), timeout=90, clock=(1000.0, 1010.0))
        for no_usage in (dict(_ZERO), None, {"input_tokens": "many"}):
            raw = _pong_with(response="1. **Important** — partial", usage=no_usage)
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_judge(_cp(raw, stderr=""), timeout=90, clock=(1000.0, 1010.0))
        # with no limit set agy cannot have timed out itself: a failed seat that says why
        raw = _pong_with(response="1. **Important** — partial", usage=dict(_ZERO))
        out, _ = _run_judge(_cp(raw, stderr=""), timeout=None)
        self.assertTrue(str(out).startswith("(FAILED — agy reported a turn with no token usage"), str(out)[:70])
        self.assertEqual(review._judge_status(out), "fail")

    def test_the_probe_uses_the_judges_timeout_rule(self):
        # R2-F b (codex-medium reproduced it): the self-timeout envelope with a partial "ok" read OK.
        raw = _pong_with(response="ok", usage=dict(_ZERO))
        with mock.patch.object(mc.subprocess, "run", return_value=_cp(raw, 0, "")):
            verdict, detail = mc.probe_agy_model("gemini-3.8-flash-high", timeout=30)
        self.assertEqual(verdict, mc.UNKNOWN)
        self.assertIn("timed out", detail)

    def test_billed_credential_variables_never_reach_agy(self):
        # R2-E (sonnet): GOOGLE_APPLICATION_CREDENTIALS is a service-account (billed) path.
        env = {"HOME": "/home/u", "PATH": "/usr/bin", "GEMINI_API_KEY": "k1", "GOOGLE_API_KEY": "k2",
               "GOOGLE_APPLICATION_CREDENTIALS": "/home/u/sa.json", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}
        with mock.patch.dict(os.environ, env):
            _out, cap = _run_judge(_cp(fx("success-pong.stdout")))
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
            self.assertNotIn(var, cap["kw"]["env"], var)
        self.assertEqual(cap["kw"]["env"]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/x")
        seen = {}

        def fake(argv, **kw):
            seen.update(kw)
            return _cp(fx("success-pong.stdout"))

        with mock.patch.dict(os.environ, env), mock.patch.object(mc.subprocess, "run", side_effect=fake):
            mc.probe_agy_model("gemini-3.8-flash-high", timeout=30)
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
            self.assertNotIn(var, seen["env"], var)

    QUOTING = ("(FAILED — the judge CLI reported an error: The stream was interrupted. Please continue the "
               "task you were working on.)\n\n[partial text before the failure]\nFINDINGS:\n1. Important — "
               "bench/lib/runner.py matches `Individual quota reached` and `payment required` anywhere\n")

    def test_judgebench_reads_an_agy_failure_from_agys_error_channels_only(self):
        # R2-G (both codex seats reproduced it): a partial review that QUOTES quota wording halted the exam.
        from bench.lib import runner
        self.assertFalse(runner.is_quota_exhausted(self.QUOTING, backend="agy"))
        self.assertTrue(runner.is_transient_provider_error(self.QUOTING, backend="agy"))
        self.assertEqual(runner.classify(self.QUOTING, backend="agy"), ("dnf", True))
        # a non-zero exit whose STDOUT tail (tool output, text deltas) carries the wording, while
        # agy's own error line says 503
        tool = json.dumps({"event": "step_update", "step_update": {"step_type": "tool", "state": "DONE", "tool_info": {
            "output": "QUOTA_SIGNATURES = ('quota reached', 'payment required')"}}})
        out, _ = _run_judge(_cp(tool + "\n", rc=1, stderr=(
            "error: Eligibility check failed: UNAVAILABLE (code 503): The service is currently unavailable.\n")))
        seat = str(out)
        self.assertIn("quota reached", seat)                            # the wording IS in the seat text
        self.assertFalse(runner.is_quota_exhausted(seat, backend="agy"))
        self.assertEqual(runner.classify(seat, backend="agy"), ("dnf", True))
        # the real quota stop is still a quota stop
        out, _ = _run_judge(_cp(fx("quota-exhausted.stdout"), rc=3, stderr=fx("quota-exhausted.stderr")),
                            model="gemini-3.1-pro-high")
        self.assertTrue(runner.is_quota_exhausted(str(out), backend="agy"))
        self.assertEqual(runner.classify(str(out), backend="agy"), ("dnf", False))
        # … and so is the pre-flight's own envelope
        pre = "(error: agy quota exhausted: 2.7% left in the Gemini Models 5h bucket (floor 3%), resets x)"
        self.assertEqual(runner.classify(pre, backend="agy"), ("dnf", False))

    def test_the_live_runner_classifies_by_backend(self):
        from bench.lib import runner
        calls = []

        def invoke(backend, variant, prompt, tree, timeout, budget):
            calls.append(backend)
            return self.QUOTING

        r = runner.LiveRunner(runner.REPO_ROOT, invoke=invoke, quota_reader=lambda: None)
        (cand,) = runner.parse_candidates("gem-flash-high")
        inv = r.invoke(type("C", (), {"id": "c1"})(), cand, type("P", (), {"prompt": "p"})(), "/tmp",
                       soft_timeout=1, hard_timeout=2)
        self.assertEqual(calls, ["agy", "agy"])                       # retried once: a transient
        self.assertEqual((inv.status, inv.note), ("dnf", runner.TRANSIENT_NOTE))


# ── after round 2: three findings recovered from the post-D6 judge's unparsed first review ───
class PostPanelFindings(unittest.TestCase):
    BILLED = {"HOME": "/home/u", "PATH": "/usr/bin", "GEMINI_API_KEY": "k1", "GOOGLE_API_KEY": "k2",
              "GOOGLE_APPLICATION_CREDENTIALS": "/home/u/sa.json"}

    def test_every_agy_call_of_the_models_commands_uses_the_sanitized_environment(self):
        # J1: the quota, credits and listing reads still inherited the billed-credential variables,
        # so the quota guard could read another credential's quota than the judge spends.
        seen = []

        def fake(argv, **kw):
            seen.append((list(argv), kw.get("env")))
            return _cp(fx("quota.stdout") if "/quota" in argv else
                       fx("credits.stdout") if "/credits" in argv else fx("models.stdout"))

        with mock.patch.dict(os.environ, self.BILLED), \
                mock.patch.object(mc.shutil, "which", return_value="/usr/bin/agy"), \
                mock.patch.object(mc.subprocess, "run", side_effect=fake):
            self.assertEqual(len(mc.list_agy_models()), 18)
            self.assertEqual(len(mc.agy_quota()), 4)
            self.assertEqual(mc.agy_credits(), 0)
        self.assertEqual(len(seen), 3)
        for argv, env in seen:
            self.assertIsNotNone(env, argv)
            for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
                self.assertNotIn(var, env, (argv, var))
            self.assertEqual(env.get("HOME"), "/home/u")

    def test_nothing_at_the_limit_is_a_timeout(self):
        # J2: empty stdout (or prose) with no stderr line, returned at the limit, was a failed seat.
        for raw in ("", "   \n", "some prose but no stream\n"):
            with self.assertRaises(subprocess.TimeoutExpired, msg=repr(raw)):
                _run_judge(_cp(raw, stderr=""), timeout=90, clock=(1000.0, 1090.0))
        # under the limit the same outputs are ordinary failures
        out, _ = _run_judge(_cp("", stderr=""), timeout=90, clock=(1000.0, 1001.0))
        self.assertEqual(str(out), "(no output)")
        # an explicit ERROR result at the limit is still a failure with its cause (round 2)
        out, _ = _run_judge(_cp(fx("not-signed-in.stdout"), rc=1, stderr=fx("not-signed-in.stderr")),
                            timeout=90, clock=(1000.0, 1090.0))
        self.assertTrue(str(out).startswith("(FAILED — agy is not signed in"))

    UNAVAILABLE = "error: Eligibility check failed: UNAVAILABLE (code 503): The service is currently unavailable.\n"

    def test_a_failed_call_carries_agys_own_error_in_its_first_line(self):
        # The real 503 of the exam's re-run (exit 1, a lone ERROR result, this stderr line).
        stdout = fx("not-signed-in.stdout").replace(
            "authentication failed or timed out",
            "Eligibility check failed: UNAVAILABLE (code 503): The service is currently unavailable.")
        out, _ = _run_judge(_cp(stdout, rc=1, stderr=self.UNAVAILABLE))
        first = str(out).splitlines()[0]
        self.assertEqual(first, "(FAILED — agy reported: Eligibility check failed: UNAVAILABLE (code 503): "
                                "The service is currently unavailable.)")
        self.assertIn("(FAILED — exit 1)", str(out))
        self.assertEqual(review._judge_error(str(out), "fail"), first)
        # a failed call about which agy said nothing keeps the plain block
        out, _ = _run_judge(_cp("garbage", rc=1, stderr="I1006 00:00:00.000 1 x.go:1] log line\n"))
        self.assertTrue(str(out).startswith("(FAILED — exit 1)"), str(out)[:40])

    def test_judgebench_cannot_be_steered_by_text_inside_stdout(self):
        # J3: a `[stderr tail]` line INSIDE the untrusted stdout was read as the section boundary,
        # so stdout text could fake an `error: Individual quota reached.` and halt the exam.
        from bench.lib import runner
        fake = "not a stream\n[stderr tail]\nerror: Individual quota reached.\nAGY_ERROR: {\"short_error\":\"RESOURCE_EXHAUSTED (code 429): Individual quota reached.\"}\n"
        out, _ = _run_judge(_cp(fake, rc=1, stderr=self.UNAVAILABLE))
        seat = str(out)
        self.assertIn("[stderr tail]\nerror: Individual quota reached.", seat)      # the bait is in the seat text
        self.assertFalse(runner.is_quota_exhausted(seat, backend="agy"))
        self.assertEqual(runner.classify(seat, backend="agy"), ("dnf", True))        # what agy really said: 503
        # the same bait with NOTHING on the real stderr: no cause at all — a plain failure, not a halt
        out, _ = _run_judge(_cp(fake, rc=1, stderr=""))
        self.assertEqual(runner.classify(str(out), backend="agy"), ("fail", False))
        # bait that imitates the transport-failure marker does not earn a retry either
        out, _ = _run_judge(_cp("x\n(no output captured)\n", rc=1, stderr=""))
        self.assertEqual(runner.classify(str(out), backend="agy"), ("fail", False))
        # a real quota stop is still a quota stop, a real transport failure still a retry
        out, _ = _run_judge(_cp(fx("quota-exhausted.stdout"), rc=3, stderr=fx("quota-exhausted.stderr")),
                            model="gemini-3.1-pro-high")
        self.assertEqual(runner.classify(str(out), backend="agy"), ("dnf", False))
        out, _ = _run_judge(_cp("", rc=1, stderr=""))
        self.assertEqual(runner.classify(str(out), backend="agy"), ("dnf", True))


# ── post-D6 run 1 (codex:gpt-6-sol:high, FAIL 0 Critical / 2 Important) ─────────────────────
class PostD6Run1(unittest.TestCase):
    def setUp(self):
        from bench.lib import runner
        self.runner = runner

    BAIT = "Individual quota reached. The service is currently unavailable. the stream was interrupted"

    def test_only_a_line_the_adapter_built_from_agys_error_channels_is_classified(self):
        # D1: a malformed stream is QUOTED into the seat's first line, so "first line only"
        # still let stdout text halt the exam (reproduced by the judge with exit 0, empty stderr).
        raw = json.dumps({"not": "the envelope", "text": self.BAIT}) + "\n"
        out, _ = _run_judge(_cp(raw, rc=0, stderr=""))
        seat = str(out)
        first = seat.splitlines()[0]
        self.assertTrue(first.startswith("(FAILED — malformed or unrecognized structured judge output"), first)
        self.assertIn("Individual quota reached", first)                 # the bait IS in the first line
        self.assertFalse(self.runner.is_quota_exhausted(seat, backend="agy"))
        self.assertFalse(self.runner.is_transient_provider_error(seat, backend="agy"))
        self.assertEqual(self.runner.classify(seat, backend="agy"), ("fail", False))
        # every other first line that carries text agy did not put in an error channel
        # (the header for a turn error agy reported is built from agy's error channels too —
        # PostD6Run2 covers it; run 1's repair had wrongly left it out)
        for line in ("(FAILED — agy returned no stream-json envelope although one was requested)",
                     "(FAILED — agy used a web tool (search_web) although web search is off for this review)",
                     "(FAILED — agy ran model 'x quota reached', not the pinned 'y')",
                     "(FAILED — exit 1)", "(FAILED — something else: Individual quota reached)"):
            self.assertEqual(self.runner.classify(line + "\n\n" + self.BAIT, backend="agy"), ("fail", False), line)

    def test_what_the_trusted_lines_still_decide(self):
        r = self.runner
        quota, _ = _run_judge(_cp(fx("quota-exhausted.stdout"), rc=3, stderr=fx("quota-exhausted.stderr")),
                              model="gemini-3.1-pro-high")
        self.assertEqual(r.classify(str(quota), backend="agy"), ("dnf", False))
        self.assertEqual(r.classify("(error: agy quota exhausted: 2.7% left … pre-flight)", backend="agy"),
                         ("dnf", False))
        unavailable = ("(FAILED — agy reported: Eligibility check failed: UNAVAILABLE (code 503): "
                       "The service is currently unavailable.)\n\n(FAILED — exit 1)")
        self.assertEqual(r.classify(unavailable, backend="agy"), ("dnf", True))
        interrupted = ("(FAILED — the judge CLI reported an error: The stream was interrupted. Please continue "
                       "the task you were working on.)\n\n[partial text before the failure]\nFINDINGS:\nNONE")
        self.assertEqual(r.classify(interrupted, backend="agy"), ("dnf", True))
        # agy's own statement that is NOT a known transient is a plain failure
        self.assertEqual(r.classify("(FAILED — agy reported: something new went wrong)\n\n(FAILED — exit 1)",
                                    backend="agy"), ("fail", False))
        # a quota phrase inside agy's reported (non-quota) statement does not make it a quota stop:
        # for agy, quota is what the ADAPTER named, nothing else
        self.assertFalse(r.is_quota_exhausted("(FAILED — agy reported: payment required)", backend="agy"))

    def _fake_run(self, entry, cand="gem-flash-high"):
        fake = self.runner.FakeRunner({"default": entry})
        (c,) = self.runner.parse_candidates(cand)
        return fake.invoke(type("C", (), {"id": "c1"})(), c, type("P", (), {"prompt": "p"})(), None,
                           soft_timeout=1, hard_timeout=2)

    def test_the_fake_runner_classifies_like_the_live_one(self):
        # D2: FakeRunner did not pass the backend, so a replay of an agy transient whose partial
        # review quotes quota wording recorded a quota halt while the live runner called it transient.
        raw = ("(FAILED — the judge CLI reported an error: The stream was interrupted. Please continue the "
               "task you were working on.)\n\n[partial text before the failure]\nFINDINGS:\n1. the harness "
               "matches `Individual quota reached` and `payment required` anywhere\n")
        inv = self._fake_run({"raw": raw})
        self.assertEqual((inv.status, inv.note), ("dnf", self.runner.TRANSIENT_NOTE))
        # the same text under a codex seat keeps the whole-envelope rule (pre-existing behaviour,
        # out of this task's scope: "payment required" is one of its quota signatures)
        inv = self._fake_run({"raw": raw}, cand="sol-med")
        self.assertEqual((inv.status, inv.note), ("dnf", self.runner.QUOTA_NOTE))

    def test_scripted_quota_and_transient_still_work_for_an_agy_candidate(self):
        inv = self._fake_run({"status": "quota"})
        self.assertEqual((inv.status, inv.note), ("dnf", self.runner.QUOTA_NOTE))
        self.assertTrue(inv.raw.startswith("(FAILED — agy quota exhausted"), inv.raw[:50])
        inv = self._fake_run({"status": "transient"})
        self.assertEqual((inv.status, inv.note), ("dnf", self.runner.TRANSIENT_NOTE))
        self.assertTrue(inv.raw.startswith("(FAILED — agy reported: "), inv.raw[:50])
        # and for a codex candidate exactly as before
        inv = self._fake_run({"status": "quota"}, cand="sol-med")
        self.assertEqual((inv.status, inv.note), ("dnf", self.runner.QUOTA_NOTE))
        self.assertIn("usage limit", inv.raw)
        self.assertEqual(self.runner.FakeRunner.render({"status": "quota"})[0],
                         self.runner.FakeRunner.render({"status": "quota"}, backend="codex")[0])


class PostD6Run2(unittest.TestCase):
    """Post-D6 run 2: run 1's whitelist left out the header the adapter writes for a turn agy
    reported as failed while the call exited 0 with a SUCCESS result — so a provider outage
    reported that way was scored as a zero-finding review and never re-run."""

    def setUp(self):
        from bench.lib import runner
        self.runner = runner

    MID = "midturn-error.constructed-not-captured.stdout"
    MID_MESSAGE = "the connection to the agent was interrupted before the response finished"
    # agy's wording for an outage as CAPTURED in `result.error` and on a stderr `error:` line during
    # the exam. The AGY_ERROR line that carries it here is CONSTRUCTED on the shape of the captured
    # 429 line (tests/fixtures/agy-1.2.17/quota-exhausted.stderr): no such call was captured.
    OUTAGE = "UNAVAILABLE (code 503): The service is currently unavailable."

    def _agy_error_line(self, short):
        return "AGY_ERROR: " + json.dumps({"short_error": short, "status": "UNAVAILABLE", "error_code": 503,
                                           "code_kind": "http", "retryable": True}) + "\n"

    def test_an_outage_agy_reported_on_a_call_that_exited_0_is_retried(self):
        out, _ = _run_judge(_cp(fx("success-tools.stdout"), rc=0, stderr=self._agy_error_line(self.OUTAGE)))
        seat = str(out)
        self.assertTrue(seat.startswith("(FAILED — the agy turn reported an error: AGY_ERROR:"), seat[:80])
        self.assertEqual(self.runner.classify(seat, backend="agy"), ("dnf", True))
        inv = self.runner.finish(seat, timed_out=False, duration_ms=1, retries=1, backend="agy")
        self.assertEqual((inv.status, inv.note), ("dnf", self.runner.TRANSIENT_NOTE))

    def test_an_outage_in_the_response_step_is_retried_too(self):
        mid = fx(self.MID).replace(self.MID_MESSAGE, self.OUTAGE)
        out, _ = _run_judge(_cp(mid, rc=0, stderr=""))
        seat = str(out)
        self.assertTrue(seat.startswith("(FAILED — the agy turn reported an error: agent_response step"), seat[:80])
        self.assertEqual(self.runner.classify(seat, backend="agy"), ("dnf", True))

    def test_what_that_header_does_not_excuse(self):
        r = self.runner
        # a turn error with no transient wording is a failed review, as before
        plain = fx(self.MID).replace(self.MID_MESSAGE, "the model returned an invalid response")
        out, _ = _run_judge(_cp(plain, rc=0, stderr=""))
        self.assertTrue(str(out).startswith("(FAILED — the agy turn reported an error"), str(out)[:60])
        self.assertEqual(r.classify(str(out), backend="agy"), ("fail", False))
        # text the judge wrote stays out of it: the partial review quotes outage wording, agy's error does not
        quoting = plain.replace("first finding, complete.",
                                "first finding: the service is currently unavailable, 503 Service Unavailable.")
        out, _ = _run_judge(_cp(quoting, rc=0, stderr=""))
        self.assertIn("currently unavailable", str(out))
        self.assertNotIn("currently unavailable", str(out).split("\n", 1)[0])
        self.assertEqual(r.classify(str(out), backend="agy"), ("fail", False))
        # judgebench itself never reads a quota stop from WORDING, not even in that header: for agy
        # only the adapter's own quota line is one …
        by_hand = "(FAILED — the agy turn reported an error: agent_response step 1 ended in ERROR: Individual quota reached.)"
        self.assertFalse(r.is_quota_exhausted(by_hand, backend="agy"))
        self.assertEqual(r.classify(by_hand, backend="agy"), ("fail", False))
        # … and the ADAPTER names a quota stop that agy states in a response step (post-D6 run 4: I had
        # pinned `fail` for it here; a step's error is one of agy's channels like the other three)
        quota_step = fx(self.MID).replace(self.MID_MESSAGE, "Individual quota reached.")
        out, _ = _run_judge(_cp(quota_step, rc=0, stderr=""))
        self.assertTrue(str(out).startswith("(FAILED — agy quota exhausted: Individual quota reached."), str(out)[:70])
        self.assertEqual(r.classify(str(out), backend="agy"), ("dnf", False))
        # and a quota stop agy states on its stderr of such a call is still named by the adapter
        out, _ = _run_judge(_cp(fx("success-tools.stdout"), rc=0, stderr=fx("quota-exhausted.stderr")))
        self.assertTrue(str(out).startswith("(FAILED — agy quota exhausted"), str(out)[:60])
        self.assertEqual(r.classify(str(out), backend="agy"), ("dnf", False))


class PostD6Run3(unittest.TestCase):
    """Post-D6 run 3 (owner-approved): a cause agy states was lost to the timeout rule and to the
    no-usage fallback, and wording this task added for agy was also matched for other providers."""

    def setUp(self):
        from bench.lib import runner
        self.runner = runner

    @staticmethod
    def _cut():
        # the captured stream without its terminal `result` event: what a cut or killed call leaves
        return "\n".join(fx("success-pong.stdout").split("\n")[:2]) + "\n"

    # CONSTRUCTED on the shape of the captured 429 line (quota-exhausted.stderr): no AGY_ERROR line
    # for a 503 was captured, only the wording (in `result.error` and on a stderr `error:` line).
    OUTAGE_LINE = ('AGY_ERROR: {"short_error":"UNAVAILABLE (code 503): The service is currently unavailable.",'
                   '"status":"UNAVAILABLE","error_code":503,"code_kind":"http","retryable":true}\n')

    def test_an_error_agy_states_on_stderr_is_never_read_as_a_timeout(self):
        # F1a: a cut stream that arrives at the limit WITH agy's quota statement on stderr
        cut, quota = self._cut(), fx("quota-exhausted.stderr")
        self.assertIsNone(usage.extract_agy(cut, lenient_tail=True)[1])          # really no result event
        self.assertFalse(usage.agy_timed_out(quota, 90, 95.0, cut))
        for rc in (3, 0):
            out, _ = _run_judge(_cp(cut, rc=rc, stderr=quota), timeout=90, clock=(1000.0, 1095.0))
            self.assertTrue(str(out).startswith("(FAILED — agy quota exhausted"), (rc, str(out)[:70]))
            self.assertEqual(self.runner.classify(str(out), backend="agy"), ("dnf", False), rc)   # the exam halts
        # nothing on stdout at all, the statement on stderr
        out, _ = _run_judge(_cp("", rc=3, stderr=quota), timeout=90, clock=(1000.0, 1095.0))
        self.assertTrue(str(out).startswith("(FAILED — agy quota exhausted"), str(out)[:70])
        # the same cut stream on an exit-0 call, with an outage on stderr instead: the outage leads the
        # seat (not the parser's "no result event"), so judgebench retries it
        for stdout in (cut, "", "not a stream at all\n"):
            out, _ = _run_judge(_cp(stdout, rc=0, stderr=self.OUTAGE_LINE), timeout=90, clock=(1000.0, 1095.0))
            seat = str(out)
            self.assertTrue(seat.startswith("(FAILED — the agy turn reported an error: AGY_ERROR:"),
                            (stdout[:20], seat[:80]))
            self.assertEqual(self.runner.classify(seat, backend="agy"), ("dnf", True), stdout[:20])
        # an exit-0 ERROR result leads with its own `result.error` when stderr states nothing …
        err = fx("not-signed-in.stdout").replace("authentication failed or timed out", "The stream was interrupted.")
        out, _ = _run_judge(_cp(err, rc=0, stderr=""))
        self.assertTrue(str(out).startswith("(FAILED — the judge CLI reported an error: The stream was interrupted."),
                        str(out)[:80])
        # … and with agy's stderr statement when there is one — the same order as on a non-zero exit
        for rc in (0, 1):
            out, _ = _run_judge(_cp(err, rc=rc, stderr=self.OUTAGE_LINE))
            self.assertEqual(str(out).split("\n", 1)[0],
                             "(FAILED — agy reported: UNAVAILABLE (code 503): The service is currently unavailable.; "
                             "The stream was interrupted.)", rc)

    def test_a_turn_error_is_reported_before_the_no_usage_fallback(self):
        # F1b: a SUCCESS result with all-zero usage AND an AGY_ERROR line — the provider's cause was lost
        zero = fx("print-timeout.stdout")
        self.assertTrue(usage.agy_success_without_usage(zero))
        self.assertFalse(usage.agy_timed_out(self.OUTAGE_LINE, 90, 95.0, zero))
        for clock in ((1000.0, 1095.0), (1000.0, 1001.0)):
            out, _ = _run_judge(_cp(zero, rc=0, stderr=self.OUTAGE_LINE), timeout=90, clock=clock)
            seat = str(out)
            self.assertTrue(seat.startswith("(FAILED — the agy turn reported an error: AGY_ERROR:"), seat[:80])
            self.assertEqual(self.runner.classify(seat, backend="agy"), ("dnf", True))            # retried once
        # the same rule where no limit is set (the acceptance rule itself, not the caller's timeout)
        out = usage.agy_judge_output(_cp(zero, rc=0, stderr=self.OUTAGE_LINE), sandbox.format_judge_output,
                                     expected_model="gemini-3.8-flash-high", web_search=False)
        self.assertTrue(str(out).startswith("(FAILED — the agy turn reported an error: AGY_ERROR:"), str(out)[:80])

    def test_what_is_still_a_timeout(self):
        cut, zero = self._cut(), fx("print-timeout.stdout")
        self.assertTrue(usage.agy_timed_out("", 90, 95.0, cut))                 # cut at the limit, agy said nothing
        self.assertTrue(usage.agy_timed_out("", 90, 1.0, zero))                 # the self-timeout shape, no stderr line
        self.assertTrue(usage.agy_timed_out(fx("print-timeout.stderr"), 90, 1.0, zero))
        # agy's own timeout line stays decisive, even next to an error line
        self.assertTrue(usage.agy_timed_out(fx("print-timeout.stderr") + self.OUTAGE_LINE, 90, 1.0, zero))
        # a log line that merely contains "error:" is not agy stating an error
        self.assertTrue(usage.agy_timed_out("I1006 00:00:00.000 1 x.go:1] error: not at the line start\n", 90, 95.0, cut))
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_judge(_cp(cut, rc=0, stderr=""), timeout=90, clock=(1000.0, 1095.0))

    def test_this_tasks_agy_wording_does_not_reach_other_providers(self):
        # F2: the three signatures this task added sat in the list every backend's WHOLE envelope is
        # searched with, so a failed codex review that merely quoted one of them earned a retry.
        r = self.runner
        for phrase in ("RESOURCE_EXHAUSTED (code 429)", "The stream was interrupted.",
                       "The service is currently unavailable."):
            quoted = "(FAILED — exit 1)\n[stdout tail]\nthe review quotes: " + phrase + "\n[stderr tail]\nboom"
            for backend in ("codex", "grok", None):
                self.assertEqual(r.classify(quoted, backend=backend), ("fail", False), (phrase, backend))
            trusted = "(FAILED — agy reported: " + phrase + ")\n\n(FAILED — exit 1)"
            self.assertEqual(r.classify(trusted, backend="agy"), ("dnf", True), phrase)
        for sig in ("the stream was interrupted", "currently unavailable", "resource_exhausted"):
            self.assertNotIn(sig, r.TRANSIENT_SIGNATURES)                       # the shared list is as it was
        # the shared signatures still work for every backend, on agy's trusted line too
        capacity = "(FAILED — exit 1)\n[stderr tail]\nERROR: Selected model is at capacity. Please try a different model."
        self.assertEqual(r.classify(capacity, backend="codex"), ("dnf", True))
        self.assertEqual(r.classify("(FAILED — agy reported: 503 Service Unavailable)\n\n(FAILED — exit 1)",
                                    backend="agy"), ("dnf", True))


class FailureShapeMatrix(unittest.TestCase):
    """Every shape an agy call can come back in, against rules written from intent — not one rule
    per finding. Four post-D6 reviews in a row each found another shape whose cause was lost
    (a quota stop read as a timeout, an outage scored as a zero-finding review); patching them one
    at a time did not converge, so the whole space is enumerated here: exit code x stdout shape x
    what agy says on stderr x under / at the time limit = 480 calls. agy can state an error on
    FOUR channels — an `AGY_ERROR:` stderr line, an `error:` stderr line, the `error` of a result
    that is not SUCCESS, the error of a response step in ERROR — and the channels may disagree or
    only one may carry the cause; every row below is one such combination. The label is what the
    seat's caller sees (TIMEOUT / REVIEW) and, for a failed seat, what judgebench does with it."""

    ZERO = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0}
    QUOTA = "Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 34m13s."
    OUTAGE = "UNAVAILABLE (code 503): The service is currently unavailable."

    @classmethod
    def setUpClass(cls):
        from bench.lib import runner
        cls.runner = runner
        pong = fx("success-pong.stdout")
        lines = pong.strip().split("\n")
        head = "\n".join(lines[:2]) + "\n"
        last = json.loads(lines[-1])

        def with_result(**upd):
            ev = json.loads(json.dumps(last))
            ev["result"].update(upd)
            return head + json.dumps(ev) + "\n"

        def step_error(message):
            # the shape of tests/fixtures/agy-1.2.17/midturn-error.constructed-not-captured.stdout
            return json.dumps({"event": "step_update", "step_update": {
                "conversation_id": last["result"]["conversation_id"], "step_index": 1, "state": "ERROR",
                "step_type": "agent_response", "text_delta": "1. **Imp",
                "error": {"type": "MODEL_ERROR", "message": message}}}) + "\n"

        def tool_error(message):
            return json.dumps({"event": "step_update", "step_update": {
                "conversation_id": last["result"]["conversation_id"], "step_index": 2, "state": "ERROR",
                "step_type": "tool", "tool_name": "run_command",
                "error": {"type": "TOOL_ERROR", "message": message}}}) + "\n"

        cls.STDOUT = {
            "empty": "",
            "prose": "not a stream\n",
            "cut": head,                                                    # no `result` event
            "ok+usage": pong,                                               # a finished turn (captured)
            "ok+zero,text": with_result(usage=cls.ZERO, response="partial"),
            "ok+zero,empty": with_result(usage=cls.ZERO, response=""),      # agy's self-timeout shape (captured)
            "ok+usage,empty": with_result(response=""),
            "ERROR quota": with_result(status="ERROR", response="", usage=cls.ZERO, error=cls.QUOTA),
            "ERROR 503": with_result(status="ERROR", response="", usage=cls.ZERO,
                                     error="API error (attempt 1): " + cls.OUTAGE),
            "ERROR other": with_result(status="ERROR", response="", usage=cls.ZERO, error="something new went wrong"),
            # a response step in ERROR (constructed), in a cut stream and before a finished result
            "cut,step 503": head + step_error(cls.OUTAGE),
            "cut,step quota": head + step_error(cls.QUOTA),
            "ok+usage,step 503": head + step_error(cls.OUTAGE) + lines[-1] + "\n",
            "ok+usage,step quota": head + step_error(cls.QUOTA) + lines[-1] + "\n",
            # a TOOL step in ERROR is that tool's own failure (a refused write, a failed command) and
            # its text is whatever the command printed — never agy's statement about the turn
            "ok+usage,tool error": head + tool_error(cls.QUOTA + " " + cls.OUTAGE) + lines[-1] + "\n",
        }
        cls.STDERR = {
            "-": "",
            "log": "I1006 00:00:00.000 1 x.go:1] error: a log line, not a statement\n",
            "timeout": fx("print-timeout.stderr"),                          # captured
            "quota": fx("quota-exhausted.stderr"),                          # captured: `error:` + `AGY_ERROR:`
            "503": "error: Eligibility check failed: " + cls.OUTAGE + "\n",  # captured wording
            "AGY503": "AGY_ERROR: " + json.dumps({"short_error": cls.OUTAGE, "status": "UNAVAILABLE",
                                                  "error_code": 503}) + "\n",   # constructed on the 429 line's shape
            "auth": fx("not-signed-in.stderr"),                             # captured
            "vague": "error: Eligibility check failed\n",                   # a statement that names no cause
        }

    def _label(self, rc, so, se, at_limit):
        res = _cp(self.STDOUT[so], rc=rc, stderr=self.STDERR[se])
        if usage.agy_timed_out(res.stderr, 90, 95.0 if at_limit else 5.0, res.stdout):
            return "TIMEOUT", ""
        seat = str(usage.agy_judge_output(res, sandbox.format_judge_output,
                                          expected_model="gemini-3.8-flash-high", web_search=False))
        if not seat.lstrip().startswith(("(FAILED", "(no output)", "(error:")):
            return "REVIEW", seat
        r = self.runner
        status, retry = r.classify(seat, backend="agy")
        if r.is_quota_exhausted(seat, backend="agy"):
            return "QUOTA-HALT", seat
        if status == "dnf" and retry:
            return ("TRANSIENT" if r.is_transient_provider_error(seat, backend="agy") else "DNF-RETRY"), seat
        return status.upper(), seat

    @staticmethod
    def _expected(rc, so, se, at_limit):
        """The rules, from intent, in order. `stated` means: on ANY of agy's four channels."""
        if so == "ok+usage,tool error":
            so = "ok+usage"                     # a tool's own error changes nothing: the same rules as without it
        if se == "timeout":
            return "TIMEOUT"                    # agy's own timeout line is decisive
        if rc == 0 and so == "ok+usage" and se in ("-", "log", "503", "auth", "vague"):
            return "REVIEW"                     # a finished turn; a bare `error:` line or a log line does not fail it
        if se == "quota" or so in ("ERROR quota", "cut,step quota", "ok+usage,step quota"):
            return "QUOTA-HALT"                 # agy states the quota stop, wherever it states it
        if se == "auth":
            return "FAIL"                       # named "not signed in"; judgebench has no halt for it (parked)
        if se in ("503", "AGY503") or so in ("ERROR 503", "cut,step 503", "ok+usage,step 503"):
            return "TRANSIENT"                  # agy states an outage on some channel: retried
        if se == "vague" or so == "ERROR other":
            return "FAIL"                       # agy states an error that is no known transient
        # from here on agy stated NOTHING: the shape of the output decides
        if so in ("ok+zero,text", "ok+zero,empty"):
            return "TIMEOUT"                    # SUCCESS without usage = the self-timeout shape (a limit is set)
        if so in ("empty", "prose", "cut") and at_limit:
            return "TIMEOUT"                    # no result at the limit
        if rc != 0 and so == "empty" and se == "-":
            return "DNF-RETRY"                  # nothing captured at all: the transport, retried once
        return "FAIL"

    def test_every_shape_gets_the_label_the_rules_give_it(self):
        wrong, n = [], 0
        for rc in (0, 1):
            for so in self.STDOUT:
                for se in self.STDERR:
                    for at_limit in (False, True):
                        n += 1
                        got, seat = self._label(rc, so, se, at_limit)
                        want = self._expected(rc, so, se, at_limit)
                        if got != want:
                            wrong.append(f"rc{rc} | {so} | stderr {se} | {'at' if at_limit else 'under'} the limit: "
                                         f"{got}, expected {want} | {seat.split(chr(10), 1)[0][:90]}")
        self.assertEqual(n, 480)
        self.assertEqual(wrong, [], f"\n{len(wrong)} rows:\n" + "\n".join(wrong))

    def test_agy_reported_carries_only_what_agy_said(self):
        # the line `agy reported: X` is trusted by its consumers: every part of X must be text from
        # one of agy's four channels — never a sentence this parser wrote
        seen = 0
        for rc in (0, 1):
            for so in self.STDOUT:
                for se in self.STDERR:
                    _got, seat = self._label(rc, so, se, False)
                    first = seat.split("\n", 1)[0]
                    if not first.startswith("(FAILED — agy reported: "):
                        continue
                    seen += 1
                    for part in first[len("(FAILED — agy reported: "):-1].split("; "):
                        self.assertTrue(part in self.STDERR[se] or part in self.STDOUT[so], (rc, so, se, part))
        self.assertGreater(seen, 40)

    def test_a_first_line_that_already_holds_everything_is_left_alone(self):
        # an exit-0 ERROR result whose own error contains what the AGY_ERROR line says: one header, nothing in front
        _got, seat = self._label(0, "ERROR 503", "AGY503", False)
        self.assertEqual(seat.split("\n", 1)[0],
                         "(FAILED — the judge CLI reported an error: API error (attempt 1): " + self.OUTAGE + ")")
        # the turn-error header holds the AGY_ERROR line it was built from
        _got, seat = self._label(0, "ok+usage", "AGY503", False)
        self.assertTrue(seat.startswith("(FAILED — the agy turn reported an error: AGY_ERROR:"), seat[:70])

    def test_the_three_shapes_of_post_d6_run_4(self):
        r = self.runner
        # G1: stderr states an error without the cause, the result's error names the outage
        got, seat = self._label(1, "ERROR 503", "vague", False)
        self.assertEqual(got, "TRANSIENT", seat[:120])
        self.assertIn("Eligibility check failed", seat.split("\n", 1)[0])
        self.assertIn("currently unavailable", seat.split("\n", 1)[0])       # both statements, one line
        # G2: a response step in ERROR that states the quota stop
        got, seat = self._label(0, "ok+usage,step quota", "-", False)
        self.assertEqual(got, "QUOTA-HALT", seat[:120])
        self.assertTrue(seat.startswith("(FAILED — agy quota exhausted"), seat[:60])
        # G3: a non-zero exit, a cut stream, the outage only in a response step, nothing on stderr
        got, seat = self._label(1, "cut,step 503", "-", False)
        self.assertEqual(got, "TRANSIENT", seat[:120])
        self.assertEqual(r.classify(seat, backend="agy"), ("dnf", True))


# ── (j) the sandbox uses agy's real state, in place ──────────────────────────
class SandboxUsesTheRealCredentials(unittest.TestCase):
    def test_gemini_state_dir_is_bound_read_write_at_its_real_path(self):
        self.assertIn(".gemini", sandbox._HOME_RW_SUBPATHS)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(Path, "home", return_value=Path(d) / "home"):
            argv = sandbox.build_bwrap_argv(Path(d) / "proj", None, ["agy"], project_writable=False)
            real = str(Path(d) / "home" / ".gemini")
        pairs = [(argv[i], argv[i + 1], argv[i + 2]) for i in range(len(argv) - 2)
                 if argv[i] in ("--bind", "--ro-bind")]
        self.assertIn(("--bind", real, real), pairs)                 # same path in and out: no copy
        self.assertEqual(argv[1:4], ["--ro-bind", "/", "/"])         # the keyring's D-Bus socket stays reachable

    def test_judge_env_keeps_the_keyring_route_and_home(self):
        env = {"HOME": "/home/u", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
               "XDG_RUNTIME_DIR": "/run/user/1000", "PATH": "/usr/bin"}
        with mock.patch.dict(os.environ, env):
            _out, cap = _run_judge(_cp(fx("success-pong.stdout")))
        for key, val in env.items():
            self.assertEqual(cap["kw"]["env"].get(key), val, key)
        child = sandbox._child_env(cap["kw"]["env"])
        for key, val in env.items():
            self.assertEqual(child.get(key), val, key)


# ── (k) tasks models ─────────────────────────────────────────────────────────
class ModelsCommands(unittest.TestCase):
    def test_parser_reads_the_ids_of_the_captured_listing(self):
        ids = mc.parse_agy_models(fx("models.stdout"))
        self.assertEqual(len(ids), 18)
        self.assertEqual(ids[0], "gemini-3.8-flash-high")
        self.assertIn("gemini-3.1-pro-high", ids)
        self.assertTrue(all("\t" not in i and " " not in i for i in ids))
        # agy's progress line (stderr in 1.2.17) must never become an id if it reaches stdout
        self.assertEqual(mc.parse_agy_models("Fetching available models...\n" + fx("models.stdout")), ids)

    def _probe(self, result=None, raises=None, model="gemini-3.8-flash-high"):
        seen = {}

        def fake(argv, **kw):
            seen.update(argv=list(argv), kw=kw)
            if raises:
                raise raises
            return result

        with mock.patch.object(mc.subprocess, "run", side_effect=fake):
            return mc.probe_agy_model(model, timeout=30), seen

    def test_probe_runs_one_real_turn_through_the_judge_stdin_path(self):
        (verdict, _detail), seen = self._probe(_cp(fx("success-pong.stdout")))
        self.assertEqual(verdict, mc.OK)
        self.assertEqual(seen["argv"][0], "agy")
        self.assertEqual(seen["argv"][1:5], ["--input-format", "stream-json", "--output-format", "stream-json"])
        self.assertIn("gemini-3.8-flash-high", seen["argv"])
        self.assertNotIn("--dangerously-skip-permissions", seen["argv"])   # an unsandboxed probe never bypasses
        self.assertEqual(json.loads(seen["kw"]["input"])["event"], "user")
        self.assertEqual(seen["kw"]["env"]["PLAYBOOK_SESSION_ID"], "models-check")

    def test_probe_verdicts(self):
        gone, _ = self._probe(_cp(fx("error-model-rejected.stdout"), 1, fx("error-model-rejected.stderr")),
                              model="gemini-9.9-nope")
        self.assertEqual(gone[0], mc.GONE)
        auth, _ = self._probe(_cp(fx("not-signed-in.stdout"), 1, fx("not-signed-in.stderr")))
        self.assertEqual(auth[0], mc.UNKNOWN)
        self.assertIn("not signed in", auth[1])
        quota, _ = self._probe(_cp(fx("quota-exhausted.stdout"), 3, fx("quota-exhausted.stderr")),
                               model="gemini-3.1-pro-high")
        self.assertEqual(quota[0], mc.UNKNOWN)
        self.assertIn("quota", quota[1])
        slow, _ = self._probe(raises=subprocess.TimeoutExpired("agy", 30))
        self.assertEqual(slow[0], mc.UNKNOWN)
        self.assertIn("timed out", slow[1])
        selfslow, _ = self._probe(_cp(fx("print-timeout.stdout"), 0, fx("print-timeout.stderr")))
        self.assertEqual(selfslow[0], mc.UNKNOWN)
        # impl panel r1 (codex×2): OK needs an ANSWER. A turn that ended on an auto-denied tool
        # with an empty response, a turn agy flags with AGY_ERROR, or the wrong model is not one.
        denied, _ = self._probe(_cp(fx("denied-command.stdout"), 0, fx("denied-command.stderr")))
        self.assertEqual(denied[0], mc.UNKNOWN)
        self.assertIn("RunCommand", denied[1])
        flagged, _ = self._probe(_cp(fx("success-pong.stdout"), 0,
                                     'AGY_ERROR: {"status":"UNAVAILABLE","error_code":503}\n'))
        self.assertEqual(flagged[0], mc.UNKNOWN)
        other, _ = self._probe(_cp(fx("success-pong.stdout")), model="gemini-3.1-pro-high")
        self.assertEqual(other[0], mc.UNKNOWN)
        self.assertIn("gemini-3.8-flash-high", other[1])
        prose, _ = self._probe(_cp("ok\n"))
        self.assertEqual(prose[0], mc.UNKNOWN)
        bad, _ = self._probe(model="gemini-3.8-flash:turbo")
        self.assertEqual(bad[0], mc.BAD_EFFORT)

    def _check(self, specs, *, probe, listed, probe_result=(mc.OK, "responds")):
        ids = mc.parse_agy_models(fx("models.stdout")) if listed else None
        avail = type("A", (), {"is_available": staticmethod(lambda: True)})
        calls = []

        def fake_probe(variant, timeout=mc.PROBE_TIMEOUT_SECS):
            calls.append(variant)
            return probe_result

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(mc, "list_agy_models", return_value=ids), \
                mock.patch.object(mc, "agy_quota", return_value=None), \
                mock.patch.object(mc, "list_grok_models", return_value=None), \
                mock.patch.object(mc, "load_codex_cache", return_value=None), \
                mock.patch.object(mc, "installed_cli_version", return_value=None), \
                mock.patch.object(mc, "probe_agy_model", side_effect=fake_probe), \
                mock.patch.object(mc, "_adapter_classes", return_value={
                    k: avail for k in ("claude", "codex", "agy", "pi", "grok")}), \
                mock.patch("provider.sandbox.load_judge_config",
                           return_value={"default_judge": None, "panel": list(specs)}):
            report = mc.check_pins(Path(d), probe=probe)
        return {e["spec"]: e for e in report["entries"]}, calls, report

    def test_check_live_probes_a_listed_pin(self):
        by, calls, _ = self._check(["agy:gemini-3.8-flash-high"], probe=True, listed=True)
        self.assertEqual(by["agy:gemini-3.8-flash-high"]["verdict"], mc.OK)
        self.assertEqual(calls, ["gemini-3.8-flash-high"])           # OK only after a real turn

    def test_check_reports_what_the_probe_found(self):
        by, _calls, _ = self._check(["agy:gemini-3.8-flash-high"], probe=True, listed=True,
                                    probe_result=(mc.UNKNOWN, "agy quota exhausted: …"))
        self.assertEqual(by["agy:gemini-3.8-flash-high"]["verdict"], mc.UNKNOWN)
        self.assertIn("quota", by["agy:gemini-3.8-flash-high"]["detail"])

    def test_check_without_probe_is_listing_only_and_says_so(self):
        by, calls, _ = self._check(["agy:gemini-3.8-flash-high", "agy:gemini-9.9-nope", "agy",
                                    "agy:gemini-3.8-flash:high", "agy:gemini-3.8-flash:turbo"],
                                   probe=False, listed=True)
        self.assertEqual(calls, [])
        self.assertEqual(by["agy:gemini-3.8-flash-high"]["verdict"], mc.LISTED)
        self.assertEqual(by["agy:gemini-9.9-nope"]["verdict"], mc.GONE)
        self.assertEqual(by["agy"]["verdict"], mc.LISTED)       # signed in (the listing answered), not probed
        self.assertEqual(by["agy:gemini-3.8-flash:high"]["verdict"], mc.BAD_EFFORT)     # not a whole id
        self.assertIn("gemini-3.8-flash-high", by["agy:gemini-3.8-flash:high"]["detail"])
        self.assertEqual(by["agy:gemini-3.8-flash:turbo"]["verdict"], mc.BAD_EFFORT)

    def test_an_unlisted_id_is_gone_without_spending_a_turn(self):
        by, calls, _ = self._check(["agy:gemini-9.9-nope"], probe=True, listed=True)
        self.assertEqual(by["agy:gemini-9.9-nope"]["verdict"], mc.GONE)
        self.assertEqual(calls, [])

    def test_no_listing_falls_back_to_the_probe(self):
        by, calls, _ = self._check(["agy:gemini-3.8-flash-high"], probe=True, listed=False)
        self.assertEqual(calls, ["gemini-3.8-flash-high"])
        by2, _c, _ = self._check(["agy:gemini-3.8-flash-high"], probe=False, listed=False)
        self.assertEqual(by2["agy:gemini-3.8-flash-high"]["verdict"], mc.UNKNOWN)

    def test_a_bare_agy_seat_is_probed_too(self):
        # impl panel r1 (codex-high): bare `agy` used to read OK with no call at all — even signed out.
        by, calls, _ = self._check(["agy"], probe=True, listed=True)
        self.assertEqual(calls, [None])
        self.assertEqual(by["agy"]["verdict"], mc.OK)
        by, calls, _ = self._check(["agy"], probe=True, listed=False,
                                   probe_result=(mc.UNKNOWN, "agy is not signed in: …"))
        self.assertEqual(calls, [None])
        self.assertEqual(by["agy"]["verdict"], mc.UNKNOWN)
        by, calls, _ = self._check(["agy"], probe=False, listed=False)
        self.assertEqual((calls, by["agy"]["verdict"]), ([], mc.UNKNOWN))

    def test_set_refuses_an_unlisted_agy_pin(self):
        self.assertIsNone(mc.spec_error("agy:gemini-3.8-flash-high"))
        self.assertIsNotNone(mc.spec_error("agy:gemini-3.8-flash:high"))
        self.assertIsNotNone(mc.spec_error("agy:gemini-3.8-flash:turbo"))
        by, _calls, report = self._check(["agy:gemini-9.9-nope"], probe=False, listed=True)
        self.assertEqual([e["spec"] for e in mc.bad_pins(report)], ["agy:gemini-9.9-nope"])

    def test_detect_lists_ids_and_how_to_pin_them(self):
        with mock.patch.object(mc.shutil, "which", side_effect=lambda n: "/usr/bin/agy" if n == "agy" else None), \
                mock.patch.object(mc, "list_agy_models", return_value=mc.parse_agy_models(fx("models.stdout"))):
            agy = {p["name"]: p for p in mc.detect_providers()["providers"]}["agy"]
        self.assertEqual(agy["models"][0], {"id": "gemini-3.8-flash-high", "efforts": []})
        self.assertIn("agy:<id>", agy["note"])
        self.assertNotIn("NOT selectable", agy["note"])

    def test_quota_reader_on_the_captured_output(self):
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/agy"), \
                mock.patch.object(mc.subprocess, "run", return_value=_cp(fx("quota.stdout"))):
            buckets = mc.agy_quota()
        self.assertEqual(len(buckets), 4)
        first = buckets[0]
        self.assertEqual((first["group"], first["window"]), ("Gemini Models", "weekly"))
        self.assertEqual(first["reset_time"], "2026-10-12T07:57:04Z")
        self.assertTrue(0.0 <= first["remaining_fraction"] <= 1.0)
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/agy"), \
                mock.patch.object(mc.subprocess, "run", return_value=_cp("not json", rc=1)):
            self.assertIsNone(mc.agy_quota())

    def test_a_dead_agy_pin_is_probe_confirmable(self):
        out, _ = _run_judge(_cp(fx("error-model-rejected.stdout"), rc=1,
                                stderr=fx("error-model-rejected.stderr")), model="gemini-9.9-nope")
        failed = str(out)                                   # the seat's text for agy's captured refusal
        self.assertEqual(mc.classify_failure(failed), mc.MODEL_UNAVAILABLE)
        got = mc.confirm_dead_specs({"agy:gemini-9.9-nope": failed},
                                    {"agy:gemini-9.9-nope": ("agy", "gemini-9.9-nope")},
                                    probe_agy=lambda v: (mc.GONE, "agy rejects this model id"))
        self.assertEqual(got, {"agy:gemini-9.9-nope": (mc.GONE, "agy rejects this model id")})
        alive = mc.confirm_dead_specs({"agy:gemini-9.9-nope": failed},
                                      {"agy:gemini-9.9-nope": ("agy", "gemini-9.9-nope")},
                                      probe_agy=lambda v: (mc.OK, "responds"))
        self.assertEqual(alive, {})


# ── (l)(m) the review runner: single-judge arm + one spend record per call ───
@contextlib.contextmanager
def _chdir(d: Path):
    prev = Path.cwd()
    os.chdir(d)
    try:
        yield
    finally:
        os.chdir(prev)


def _journal(agent: Path) -> list:
    p = agent / "journal" / "enforcement.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


class ReviewRunner(unittest.TestCase):
    def setUp(self):
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
            json.dumps({"panel": ["agy:gemini-3.8-flash-high"],
                        "default_judge": "agy:gemini-3.8-flash-high"}), encoding="utf-8")
        review._PB_JOURNAL_MOD = None
        review._PB_JOURNAL_LOADED = False
        self.calls = []

    def _patched(self, result=None, raises=None):
        def fake_run(agent, args, **kw):
            self.calls.append((agent, list(args), kw))
            if raises is not None:
                raise raises
            return result
        return (mock.patch.object(sandbox, "run", fake_run),
                mock.patch.object(shutil, "which", lambda name: "/usr/bin/" + name))

    def _reviews(self):
        return [r for r in _journal(self.agent) if r["hook"] == "review"]

    def test_panel_seat_writes_one_record_with_agys_token_counts(self):
        p1, p2 = self._patched(_cp(fx("success-tools.stdout")))
        with p1, p2, _chdir(self.project), contextlib.suppress(SystemExit):
            review.cmd_panel_review(["042", "--models", "agy:gemini-3.8-flash-high"])
        recs = self._reviews()
        self.assertEqual(len(recs), 1, recs)
        r = recs[0]
        self.assertEqual((r["kind"], r["seat"], r["status"]), ("panel", "agy:gemini-3.8-flash-high", "ok"))
        self.assertEqual(r["usage"], {"status": "known", "in": 50748, "out": 6650})
        agent, args, kw = self.calls[0]
        self.assertEqual(agent, "agy")
        self.assertIn("stream-json", args)
        self.assertNotIn("--print", args)
        self.assertEqual(json.loads(kw["input"])["event"], "user")
        self.assertIn("CODE=HERON-5518", (self.agent / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8"))

    def test_panel_seat_not_signed_in_records_fail_with_the_cause(self):
        p1, p2 = self._patched(_cp(fx("not-signed-in.stdout"), 1, fx("not-signed-in.stderr")))
        with p1, p2, _chdir(self.project), contextlib.suppress(SystemExit):
            review.cmd_panel_review(["042", "--models", "agy:gemini-3.8-flash-high"])
        (r,) = self._reviews()
        self.assertEqual(r["status"], "fail")
        self.assertIn("agy is not signed in", r["error"])

    def test_panel_seat_self_timeout_records_timeout(self):
        p1, p2 = self._patched(_cp(fx("print-timeout.stdout"), 0, fx("print-timeout.stderr")))
        with p1, p2, _chdir(self.project), contextlib.suppress(SystemExit):
            review.cmd_panel_review(["042", "--models", "agy:gemini-3.8-flash-high", "--timeout", "8"])
        (r,) = self._reviews()
        self.assertEqual(r["status"], "timeout")
        self.assertEqual(r["usage"], {"status": "unknown"})
        text = (self.agent / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8")
        self.assertIn("(timed out after hard", text)

    def _single(self, result=None, raises=None, extra=()):
        p1, p2 = self._patched(result, raises)
        code = 0
        with p1, p2, _chdir(self.project):
            try:
                review.cmd_single_review(
                    "plan-review", ["042", "--backend", "agy", "--model", "gemini-3.8-flash-high", *extra])
            except SystemExit as e:
                code = e.code
        return code

    def test_single_judge_arm_uses_the_adapters_judge_invocation(self):
        code = self._single(_cp(fx("success-tools.stdout")))
        self.assertEqual(code, 0)
        agent, args, kw = self.calls[0]
        self.assertEqual(agent, "agy")
        self.assertEqual(args[:8], ["--input-format", "stream-json", "--output-format", "stream-json",
                                    "--model", "gemini-3.8-flash-high", "--mode", "plan"])
        self.assertNotIn("--add-dir", args)
        self.assertNotIn("--print", args)
        self.assertEqual(json.loads(kw["input"])["event"], "user")
        self.assertIs(kw["project_writable"], False)
        log = (self.agent / "tasks" / "042-demo" / "judge-agy.log").read_text(encoding="utf-8")
        self.assertIn("CODE=HERON-5518", log)
        self.assertNotIn('"event"', log)                    # the review prose, not the protocol frames
        task_text = (self.agent / "tasks" / "042-demo" / "task.md").read_text(encoding="utf-8")
        self.assertIn("CODE=HERON-5518", task_text)         # written back as the review
        self.assertNotIn('"step_update"', task_text)
        (r,) = self._reviews()
        self.assertEqual((r["kind"], r["seat"], r["status"]), ("single", "agy:gemini-3.8-flash-high", "ok"))
        self.assertEqual(r["usage"], {"status": "known", "in": 50748, "out": 6650})

    def test_single_judge_print_timeout_and_killer(self):
        self._single(_cp(fx("success-pong.stdout")), extra=("--timeout", "1500"))
        _agent, args, kw = self.calls[0]
        self.assertEqual(args[args.index("--print-timeout") + 1], "1500s")
        self.assertEqual(kw["timeout"], 1530)

    def test_single_judge_self_timeout_bails_as_a_timeout(self):
        code = self._single(_cp(fx("print-timeout.stdout"), 0, fx("print-timeout.stderr")),
                            extra=("--timeout", "1500"))
        self.assertNotEqual(code, 0)
        (r,) = self._reviews()
        self.assertEqual(r["status"], "timeout")
        self.assertFalse((self.agent / "tasks" / "042-demo" / "judge-agy.log").exists())

    def test_single_judge_failure_names_the_cause_and_saves_no_review(self):
        code = self._single(_cp(fx("not-signed-in.stdout"), 1, fx("not-signed-in.stderr")))
        self.assertNotEqual(code, 0)
        (r,) = self._reviews()
        self.assertEqual(r["status"], "fail")
        self.assertIn("agy is not signed in", r["error"])
        self.assertFalse((self.agent / "tasks" / "042-demo" / "judge-agy.log").exists())

    def test_single_judge_bad_effort_fails_before_the_spawn(self):
        p1, p2 = self._patched(_cp(fx("success-pong.stdout")))
        with p1, p2, _chdir(self.project), self.assertRaises(SystemExit) as ctx:
            review.cmd_single_review("plan-review", ["042", "--backend", "agy", "--model",
                                                     "gemini-3.8-flash:turbo"])
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
