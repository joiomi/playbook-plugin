"""Judge token usage from a CLI's STRUCTURED output (task 056). Stdlib only.

Four judge CLIs report per-call token counts when asked for structured output:

  codex exec --json            JSONL events; the LAST ``turn.completed`` line carries
                               ``usage.input_tokens`` / ``usage.output_tokens``; the
                               review text is the LAST completed ``agent_message`` item
                               (codex's own ``-o`` semantics: the final message).
  grok --output-format json    ONE JSON object: ``text`` + ``usage.input_tokens`` /
                               ``usage.output_tokens``.
  claude -p --output-format json   ONE JSON object, ``type: result``: the review in
                               ``result``; the counts for the whole run in ``modelUsage``
                               (per model) and the top-level ``usage`` (task 232 — the
                               claude section below).
  agy --output-format stream-json   NDJSON events; the terminal ``result`` event carries
                               ``response`` (the review) and ``usage.input_tokens`` /
                               ``usage.output_tokens`` for the whole turn (task 111 — the
                               agy section below; an EXPERIMENTAL judge seat).

Shapes were captured from live probes (codex-cli 0.153.4, grok 1.0.13, 2026-09-09;
agy 1.2.17, 2026-10-05; claude 2.1.302, 2026-10-17) and are pinned VERBATIM in
tests/test_judge_usage.py, tests/fixtures/agy-1.2.17/ (tests/test_agy_judge.py) and
tests/fixtures/claude-2.1.302/ (tests/test_claude_judge_usage.py). Numbers are COPIED from the
CLI's JSON — never derived, estimated or clamped here: a non-int, bool or negative
count is ``None`` (the journal writes ``{"status":"unknown"}``). Free-form review
prose never parses as one of these envelopes, so a judge that quotes a usage-shaped
string cannot poison the field (task 042's anchored-envelope rule, kept).

``in`` is the vendor's reported ``input_tokens`` AS REPORTED (codex's includes
cached input; grok's is the billed input; agy's excludes ``cache_read_tokens`` and its
``output_tokens`` includes ``thinking_tokens``; claude's excludes the cache, whose two
counts are copied beside it as ``cache_read`` and ``cache_write``) — recorded, not
normalized across vendors.
"""
from __future__ import annotations

import json
import re
from typing import Optional


class JudgeOutput(str):
    """A judge's review text that CARRIES its usage. It is a plain ``str`` for every
    existing caller and test double (``run_headless_judge`` keeps returning str);
    ``tasks.review._parse_judge_usage`` honours ``.usage`` first. Any str operation
    yields a plain str (usage None) — timeout/error strings never carry a number."""
    usage: Optional[dict]
    # task 261: "resumed" when agy's turn was cut at the soft deadline and its review written in the resumed
    # turn, "timed-out" when the cut turn could not be resumed; None otherwise. The seat's spend record carries it.
    soft_cut: Optional[str]
    # task 264: playbook's own line about the call (agy's soft deadline), written by the CLI apart from the judge's
    # quoted text — a judge could write the same words inside its review (PLAN.md "From task 261" (b))
    note: Optional[str]

    def __new__(cls, text: str, usage: Optional[dict] = None, soft_cut: Optional[str] = None,
                note: Optional[str] = None):
        obj = super().__new__(cls, text)
        obj.usage = usage if _valid_known(usage) else None
        obj.soft_cut = soft_cut if soft_cut in ("resumed", "timed-out") else None
        obj.note = " ".join(note.split()) if isinstance(note, str) and note.strip() else None
        return obj


def _is_count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _valid_known(usage) -> bool:
    return (isinstance(usage, dict) and usage.get("status") == "known"
            and _is_count(usage.get("in")) and _is_count(usage.get("out")))


def _usage_from_obj(usage) -> Optional[dict]:
    """``{"input_tokens":N,"output_tokens":N}`` → known usage, else None."""
    if not isinstance(usage, dict):
        return None
    _in, _out = usage.get("input_tokens"), usage.get("output_tokens")
    if _is_count(_in) and _is_count(_out):
        return {"status": "known", "in": _in, "out": _out}
    return None


def _json_object(raw: str) -> Optional[dict]:
    s = raw.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _jsonl_events(raw: str, *, lenient_tail: bool = False) -> Optional[list]:
    """Parse codex ``--json`` JSONL: every non-blank line must be a JSON object with
    a ``type`` — otherwise this is not the codex envelope (None). ``lenient_tail``
    (salvage only) ignores ONE incomplete trailing line — the shape a hard-timeout
    kill leaves behind; the usage/extraction parse stays strict.

    Lines are split on "\n" ONLY (task 111): `str.splitlines()` also breaks on U+0085,
    U+2028, U+2029, VT, FF and the C1 separators, and codex writes U+2028 / U+2029 raw
    inside a JSON string — a real stream with one of each in a command-output frame was
    rejected whole, twice, as "malformed or unrecognized structured judge output"."""
    lines = [ln.strip(" \t\r") for ln in raw.split("\n") if ln.strip(" \t\r")]
    events = []
    for i, line in enumerate(lines):
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            if lenient_tail and i == len(lines) - 1 and events:
                break
            return None
        if not isinstance(ev, dict) or not isinstance(ev.get("type"), str):
            return None
        events.append(ev)
    return events or None


def codex_protocol_detected(raw: str) -> bool:
    """True when the FIRST non-blank stdout line is a codex event object — i.e.
    the CLI did emit the ``--json`` protocol. A protocol stream the strict parser
    then rejects is MALFORMED structured output, not prose: it must fail the
    seat, never be returned verbatim as a "review" (impl round 1, opus/codex)."""
    for ln in (raw or "").split("\n"):          # "\n" only — see _jsonl_events
        ln = ln.strip(" \t\r")
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except (ValueError, TypeError):
            return False
        return isinstance(ev, dict) and isinstance(ev.get("type"), str)
    return False


def extract_codex(raw: str, *, lenient_tail: bool = False) -> "Optional[tuple[str, Optional[dict], list[str]]]":
    """codex ``exec --json`` stdout → ``(review_text, usage, error_messages)``;
    None when the stdout is not that envelope (e.g. plain prose from a CLI that
    ignored the flag). ``review_text`` is the LAST completed ``agent_message``
    (``""`` when none — e.g. a failed turn); ``usage`` from the LAST
    ``turn.completed`` — the last one WINS even when invalid (a malformed final
    frame yields None, never an earlier stale count); ``error_messages`` from
    ``error`` / ``turn.failed`` events and ``item.completed`` items of type
    ``error``."""
    events = _jsonl_events(raw or "", lenient_tail=lenient_tail)
    if events is None:
        return None
    text, usage, errors = "", None, []
    completed = False
    for ev in events:
        t = ev.get("type")
        if t == "item.completed":
            item = ev.get("item")
            if isinstance(item, dict):
                if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    text = item["text"]
                # item-level `error` items are DIAGNOSTICS (codex emits non-fatal
                # app-server-lag notices before a good message — round 2); only
                # `error` / `turn.failed` EVENTS are fatal.
        elif t == "turn.completed":
            completed = True
            usage = _usage_from_obj(ev.get("usage"))   # last wins, None if invalid
        elif t in ("error", "turn.failed"):
            # ANY fatal event fails the review (round 3): a malformed payload is
            # still a failure — stringify it, bounded, so the reason survives.
            payload = ev.get("message") if t == "error" else ev.get("error")
            if isinstance(payload, dict) and isinstance(payload.get("message"), str):
                errors.append(payload["message"])
            elif isinstance(payload, str) and payload.strip():
                errors.append(payload)
            else:
                errors.append(f"{t}: {json.dumps(payload, ensure_ascii=False)[:300]}")
    if not completed and not errors and not lenient_tail:
        # A complete prefix that never reached turn.completed is not a finished
        # review (round 2, codex-medium); the salvage path (lenient_tail) is
        # exactly the place this shape is legitimate.
        errors.append("incomplete codex event stream: no turn.completed")
    return text, usage, errors


def extract_grok(raw: str) -> "Optional[tuple[str, Optional[dict], list[str]]]":
    """grok ``--output-format json`` stdout → ``(review_text, usage, error_messages)``;
    None when the stdout is not one JSON object. A ``{"type":"error","message":…}``
    object (grok's failure shape) yields ``""`` text + the message."""
    obj = _json_object(raw or "")
    if obj is None:
        return None
    if obj.get("type") == "error":
        msg = obj.get("message")
        # a late failure can still report spend (round 3): carry its usage
        return "", _usage_from_obj(obj.get("usage")), [msg if isinstance(msg, str) else json.dumps(obj)[:500]]
    text = obj.get("text")
    if not isinstance(text, str):
        return None          # some other JSON object — not grok's envelope
    errors = []
    if "stopReason" in obj and obj.get("stopReason") != "end_turn":
        # present → must be exactly end_turn (null / non-string / max_tokens /
        # refusal / cancelled … fail); an ABSENT key is accepted so a future CLI
        # dropping the field cannot fail every seat (round 2 + 3, disclosed).
        errors.append(f"grok stopped: {obj.get('stopReason')!r}")
    return text, _usage_from_obj(obj.get("usage")), errors


# ── claude `-p --output-format json` (task 232, PHASE-B item 6) ──────────────
#
# Measured on claude 2.1.302 (2026-10-17; fixtures in tests/fixtures/claude-2.1.302/): stdout is ONE object,
# `type: "result"`. The review is `result`. The counts for the whole run are in `modelUsage` (one entry per model:
# `inputTokens`, `outputTokens`, `cacheReadInputTokens`, `cacheCreationInputTokens`) and in the top-level `usage`
# (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`), which a three-turn run
# showed cumulative and equal to the one `modelUsage` entry; `usage.iterations` is the LAST call only and is never
# read. A budget stop zeroes the top-level `usage` while `modelUsage` keeps what was spent, so `modelUsage` is read
# first when it names exactly one model; with several, the top-level `usage` as reported (a sum would be derived).
# A failure is `is_error: true` or a `subtype` other than `success`: a bad model says so in `result` (subtype
# `success`), a budget stop in `errors` (subtype `error_max_budget_usd`, no `result`).

_CLAUDE_MODEL_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
_CLAUDE_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _claude_counts(obj, keys) -> Optional[dict]:
    """One counts object → known usage with the cache counts beside it; None unless `in` and `out` are counts and
    something was counted (claude writes zeros where nothing ran: zero is not a measurement here)."""
    if not isinstance(obj, dict):
        return None
    _in, _out, _read, _write = (obj.get(k) for k in keys)
    if not (_is_count(_in) and _is_count(_out)):
        return None
    usage = {"status": "known", "in": _in, "out": _out}
    if _is_count(_read):
        usage["cache_read"] = _read
    if _is_count(_write):
        usage["cache_write"] = _write
    if not any(usage[k] for k in ("in", "out", "cache_read", "cache_write") if k in usage):
        return None
    return usage


def _claude_usage(obj: dict) -> Optional[dict]:
    models = obj.get("modelUsage")
    if isinstance(models, dict) and len(models) == 1:
        got = _claude_counts(next(iter(models.values())), _CLAUDE_MODEL_KEYS)
        if got is not None:
            return got
    return _claude_counts(obj.get("usage"), _CLAUDE_USAGE_KEYS)


# claude's account limit, byte for byte as both claude seats of a panel returned it on 2026-10-08 (task 156): "You've hit
# your weekly limit · resets Oct 13, 6pm (Europe/Bucharest)". Here, not in `tasks/`: the provider layer never imports
# `tasks` (the Codex hook scripts run its mirror with only `scripts/lib` on the path — task 269's impl round 1, agy);
# `tasks.seat_outage` takes it from here.
CLAUDE_LIMIT_LINE = re.compile(r"^you(?:'|’)ve hit your ([a-z0-9][a-z0-9 -]*?) limit\s*·\s*resets (.+?)\s*$", re.I)


def extract_claude(raw: str) -> "Optional[tuple[str, Optional[dict], list[str]]]":
    """claude ``-p --output-format json`` stdout → ``(review_text, usage, error_messages)``; None when the stdout is
    not one claude result object. A failure (``is_error``, or a ``subtype`` other than ``success``) yields its own
    words — ``errors`` joined, else ``result`` — as the error, and ``""`` as the text. So does a ``success`` whose
    ``terminal_reason`` is a string other than ``completed`` (a ``max_tokens`` stop: its review was cut off — task
    269, PLAN.md "From task 232"; the captured success says ``completed``, and an object without the field reads as
    before)."""
    obj = _json_object(raw or "")
    if obj is None or obj.get("type") != "result" or not ("subtype" in obj or "modelUsage" in obj):
        return None
    usage = _claude_usage(obj)
    said_result = obj.get("result")
    result: str = said_result if isinstance(said_result, str) else ""
    if obj.get("is_error") is True or obj.get("subtype") != "success":
        listed = obj.get("errors")
        said: "list[str]" = [e for e in listed if isinstance(e, str) and e.strip()] if isinstance(listed, list) else []
        if not said and result.strip():
            said = [result.strip()]
        if not said:
            said = [f"claude reported an error: {json.dumps(obj, ensure_ascii=False)[:1500]}"]
        return "", usage, said
    term, stop = obj.get("terminal_reason"), obj.get("stop_reason")
    # a cut-off: the turn did not complete, or the model ran out of output tokens — the Messages API's `max_tokens`
    # stop, which an envelope may carry beside `terminal_reason: completed` or none (task 269's impl round 1, agy).
    # Any other `terminal_reason` fails the seat too, on purpose: an unknown ending is not a whole review, and the
    # seat's text says which it was
    # a present `terminal_reason` must say `completed` (null included — grok's rule for its own stop field; impl
    # round 2, opus); an absent one reads as before
    if ("terminal_reason" in obj and term != "completed") or stop == "max_tokens":
        return "", usage, [f"claude stopped before its answer was complete (terminal_reason: {term}; stop_reason: "
                           f"{stop}) — playbook's words"]
    return result, usage, []


def claude_judge_output(result, format_judge_output) -> JudgeOutput:
    """A claude judge's subprocess result → the review text the callers expect, carrying the usage — the shared rule
    of ``judge_output_from_result`` with one difference for a FAILURE: it reads exactly as plain mode's did, ``(FAILED —
    exit N)`` + ``[stdout tail]`` holding claude's own words (the envelope's ``errors`` or ``result``) + the stderr
    tail, so the outage rule (which reads a claude limit line only when it is the whole stdout tail) and the
    model-availability anchors see what they saw before JSON mode. An error on exit 0 says ``exit 0``."""
    import subprocess
    raw = result.stdout or ""
    got = extract_claude(raw) if raw.strip() else None
    if got is None:
        return judge_output_from_result(result, extract_claude, format_judge_output)
    text, usage, errors = got
    if errors:
        rc = result.returncode
        # claude's own words on a failure (never the review): its account-limit line alone, as the outage rule reads a
        # claude limit only when it is the whole stdout tail (task 269, PLAN.md "From task 232") — sought in every
        # line of every `errors` entry and of the object's `result` beside them (its impl round 1: agy, opus, sonnet)
        obj = _json_object(raw)
        # `result` is claude's own words only on an error object; on a cut-off success it is the judge's partial
        # review, which may quote the line (impl round 2: agy, opus, sonnet — a false outage)
        is_error_object = isinstance(obj, dict) and (obj.get("is_error") is True or obj.get("subtype") != "success")
        said = obj.get("result") if is_error_object else None
        lines = [ln.strip() for e in errors for ln in str(e).splitlines()]
        lines += [ln.strip() for ln in said.splitlines()] if isinstance(said, str) else []
        hit = [ln for ln in lines if CLAUDE_LIMIT_LINE.match(ln)]
        if hit:
            errors = hit[:1]
        shaped = subprocess.CompletedProcess(args=getattr(result, "args", ["claude"]), returncode=rc or 1,
                                             stdout="\n".join(errors), stderr=result.stderr or "")
        out = format_judge_output(shaped)
        if not rc:
            out = out.replace("(FAILED — exit 1)", "(FAILED — exit 0, claude reported an error)", 1)
        return JudgeOutput(out, usage=usage)
    if result.returncode != 0:
        return JudgeOutput(format_judge_output(result), usage=usage)
    if text.strip():
        return JudgeOutput(text.strip(), usage=usage)
    return JudgeOutput("(FAILED — structured judge output carried no review text)", usage=usage)


# ── agy (Antigravity) `--output-format stream-json` (task 111) ───────────────
#
# Measured on agy 1.2.17 (2026-10-05; fixtures in tests/fixtures/agy-1.2.17/):
# stdout is NDJSON — `{"event":"init",…}`, `{"event":"step_update",…}`…, and ONE
# terminal `{"event":"result","result":{status, response, usage, error?,
# denied_actions?}}`. `result.usage` is the turn's total across every model call
# (input_tokens excludes cache_read_tokens; thinking_tokens is inside
# output_tokens). Three things agy does that a judge runner must not take at face
# value, each pinned by a captured fixture:
#   * `--print-timeout` expiry: exit 0, `status: "SUCCESS"`, the PARTIAL text and an
#     all-zero usage object; the only signal is one line on stderr.
#   * a tool request headless mode cannot approve: exit 0, `status: "SUCCESS"`, an
#     EMPTY response and `denied_actions`.
#   * a failure before the turn (not signed in, a rejected `--model`): exit 1, a lone
#     `result` event with `status: "ERROR"` and the reason in `error`.
#   * the quota running out mid-turn: exit 3, `status: "ERROR"`, the tokens already
#     spent in `usage`, and agy's structured `AGY_ERROR: {...}` line on stderr.
# Two more are fail-closed without a capture (the fixtures README says which files are
# constructed): agy's changelog says a stream-json session "warns and continues" after a
# model or agent error mid-turn, so an `AGY_ERROR:` stderr line or an `agent_response`
# step in state `ERROR` fails the seat even on exit 0; and agy has no flag to switch its
# web and browser tools off, so their use is read from the stream.

_AGY_PRINT_TIMEOUT_RE = re.compile(
    r"^\[agy\] print timeout after \S+ with turn in progress; returning partial output\s*$", re.M)

# Failure causes, matched ONLY in agy's ERROR channels and only for a call that already
# failed: stderr lines that START with `error:` or `AGY_ERROR:` (how agy reports every
# failure captured so far), and the `error` field of the `result` event. Never the
# review text — a review that quotes these strings stays a review — and never any other
# stderr line: agy logs `… You are not logged into Antigravity.` while it signs in
# silently, and that noise in front of an unrelated failure must not rename it (impl
# panel r1). The not-signed-in,
# rejected-model and "quota reached" strings are from captured output (the quota stop
# was captured when task 111's exam ran into the 5-hour limit: exit 3, stderr
# `error: Individual quota reached. … Resets in 34m13s.` + an `AGY_ERROR:` line with
# `RESOURCE_EXHAUSTED (code 429)`); the other two quota strings are message texts of
# the 1.2.17 binary that were never seen live.
_AGY_MODEL_REJECTED = ("invalid model selection",)
_AGY_QUOTA_EXHAUSTED = (
    "quota reached",
    "exhausted your quota",
    "ai credits balance is too low",
)
# NOT in the list: a bare `RESOURCE_EXHAUSTED` / 429. Google answers a short rate limit
# with the same code, and a retryable call must not be named "quota exhausted" (impl
# panel r2) — the wording decides, the code does not.
_AGY_NOT_SIGNED_IN = (
    "authentication required",
    "authentication failed or timed out",
    "not logged into antigravity",
    "not logged in",
    "please sign in",
    "not signed in",
)


def agy_print_timed_out(stderr) -> bool:
    """True when agy's stderr carries its own print-timeout line — the response it
    returned (exit 0, `status: SUCCESS`) is then PARTIAL. Anchored to a whole line
    that starts with `[agy] `, so text a tool printed mid-line cannot match."""
    return isinstance(stderr, str) and bool(_AGY_PRINT_TIMEOUT_RE.search(stderr))


def agy_stderr_without_timeout(stderr) -> str:
    """`stderr` without agy's own print-timeout line(s) — a cut turn's stderr kept for the resumed call's result
    (task 261, plan r1 P1): an `AGY_ERROR:` or `error:` line agy wrote before the cut still fails the seat, while
    the timeout line that only says the turn was cut is not read as the resumed call's own timeout."""
    if not isinstance(stderr, str):
        return ""
    return "".join(ln for ln in stderr.splitlines(keepends=True)
                   if not _AGY_PRINT_TIMEOUT_RE.search(ln))


def agy_resumed_stream_problem(stdout, conversation: str, expected_model: Optional[str]) -> Optional[str]:
    """Why the RESUMED call's own stream does not stand as its own evidence (task 261, plan r1 P2), or None. It
    must open with an `init` naming the same conversation (and the pinned model, when pinned) and end with a
    `result`: once streams are combined, the cut turn's events must not supply what the resumed call lacks."""
    events = _agy_events(stdout or "", lenient_tail=False) if isinstance(stdout, str) else None
    if not events:
        return "the resumed agy call returned no stream-json events"
    inits = [ev for ev in events if ev.get("event") == "init"]
    if not inits:
        return "the resumed agy call's stream has no init event"
    if inits[0].get("conversation_id") != conversation:
        return f"the resumed agy call answered another conversation ({inits[0].get('conversation_id')!r})"
    if expected_model:
        init = inits[0].get("init")
        model = init.get("model") if isinstance(init, dict) else None
        if model != expected_model:
            return (f"the resumed agy call ran model {model!r}, not the pinned {expected_model!r}" if model else
                    f"the resumed agy call did not report its model (pinned: {expected_model!r})")
    results = [ev.get("result") for ev in events if ev.get("event") == "result"]
    if not results:
        return "the resumed agy call's stream has no result event"
    last = results[-1]
    if isinstance(last, dict) and last.get("conversation_id") not in (None, conversation):
        # the review is the last result's: it must be this conversation's too (impl r2, sol)
        return f"the resumed agy call's result names another conversation ({last.get('conversation_id')!r})"
    return None


def _agy_last_result(stdout):
    """The stream's last `result` object, or None (no stream, or no result event)."""
    events = _agy_events(stdout or "", lenient_tail=True) if isinstance(stdout, str) else None
    last = None
    for ev in events or []:
        if ev.get("event") == "result" and isinstance(ev.get("result"), dict):
            last = ev["result"]
    return last


def agy_success_without_usage(stdout) -> bool:
    """True for a `result` that says SUCCESS but carries no real token usage — the
    shape of agy's own `--print-timeout` expiry (captured: `status: SUCCESS`, the
    partial text, an all-zero usage object). Every FINISHED turn captured so far
    carried usage, so this shape is never a finished turn — at any elapsed time."""
    res = _agy_last_result(stdout)
    return res is not None and res.get("status") == "SUCCESS" and _agy_usage(res.get("usage")) is None


def agy_timed_out(stderr, timeout_secs, elapsed_secs, stdout=None) -> bool:
    """Did this agy call hit its `--print-timeout`? Three signals, any one is enough:

      1. agy's own stderr line — decisive whenever it is present;
      2. with a limit set, a `result` that says SUCCESS but carries no token usage —
         the shape of agy's self-timeout, for the case its stderr line is missing (it
         is one string from one agy version). Independent of the clock;
      3. no `result` event at all when the call returns at or after its limit — a
         stream that was cut, or no output whatever.

    What is NOT a timeout (impl panel rounds 1 and 2): a finished result — SUCCESS
    with usage — that arrives at the limit (the caller's clock starts before the
    sandbox and agy start up, agy's own limit does not); and an explicit ERROR result,
    however late: that is a failure with its cause (the quota stop arrives minutes
    into a turn). Known residual: an agy that returned PARTIAL text with non-zero
    usage and no stderr line would pass. No limit set → only the stderr line can say
    so (agy cannot time itself out without one).

    Signals 2 and 3 are INFERENCES from the shape of the output, so they yield to
    anything agy states outright (post-D6 run 3): a stderr line starting `error:` /
    `AGY_ERROR:`, or a response step in ERROR. Such a call is a failure with its
    cause — a quota stop whose stream was cut, an outage reported on a zero-usage
    result — and the caller must see that cause, not "timed out"."""
    if agy_print_timed_out(stderr):
        return True
    if timeout_secs is None:
        return False
    if _agy_stderr_error_lines(stderr) or agy_stream_errors(stdout, stderr):
        return False
    if agy_success_without_usage(stdout):
        return True
    if elapsed_secs is None or elapsed_secs < timeout_secs:
        return False
    return _agy_last_result(stdout) is None


def _agy_events(raw: str, *, lenient_tail: bool = False) -> Optional[list]:
    """agy stream-json stdout → event list; None unless EVERY non-blank line is a
    JSON object with a string `event` (so codex JSONL, grok's object and prose are
    not this envelope). `lenient_tail` (salvage only) ignores one cut last line.

    Lines are split on "\n" ONLY. `str.splitlines()` also breaks on U+0085, U+2028,
    U+2029, VT, FF and the C1 separators, and agy's Go encoder writes U+0085 raw inside
    a JSON string — one such character in a review would cut its line in two and void
    the whole stream (impl panel r2)."""
    lines = [ln.strip(" \t\r") for ln in raw.split("\n") if ln.strip(" \t\r")]
    events = []
    for i, line in enumerate(lines):
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            if lenient_tail and i == len(lines) - 1 and events:
                break
            return None
        if not isinstance(ev, dict) or not isinstance(ev.get("event"), str):
            return None
        events.append(ev)
    return events or None


def _agy_usage(usage) -> Optional[dict]:
    """`result.usage` → known usage. An all-zero object is NOT a measurement: agy
    prints zeros for a turn that did run (the print-timeout capture) and for a call
    that never reached the model — both are `unknown`, never a recorded zero."""
    got = _usage_from_obj(usage)
    if got is not None and got["in"] == 0 and got["out"] == 0:
        return None
    return got


def extract_agy(raw: str, *, lenient_tail: bool = False) -> "Optional[tuple[str, Optional[dict], list[str]]]":
    """agy `--output-format stream-json` stdout → ``(review_text, usage, error_messages)``;
    None when the stdout is not that envelope. The LAST `result` event is the
    answer: `response` is the review text, `usage` its token totals as reported.
    Errors: no `result` event (a cut stream), `status` other than `SUCCESS` (the
    `error` text), or an empty response that agy explains with `denied_actions`."""
    events = _agy_events(raw or "", lenient_tail=lenient_tail)
    if events is None:
        return None
    seen, result = False, None
    for ev in events:
        if ev.get("event") == "result":
            seen, result = True, ev.get("result")          # last wins
    if not seen:
        return "", None, ["incomplete agy event stream: no result event"]
    if not isinstance(result, dict):
        return "", None, ["agy result event carries no result object"]
    response = result.get("response")
    text = response if isinstance(response, str) else ""
    errors: list[str] = []
    status = result.get("status")
    if status != "SUCCESS":
        err = result.get("error")
        errors.append(err if isinstance(err, str) and err.strip() else f"agy status {status!r}")
    elif not text.strip():
        denied = result.get("denied_actions")
        names = [str(d.get("display_name") or d.get("action") or "?")
                 for d in denied if isinstance(d, dict)] if isinstance(denied, list) else []
        if names:
            errors.append("agy auto-denied " + ", ".join(names)
                          + " (headless mode cannot prompt) and produced no response")
    return text, _agy_usage(result.get("usage")), errors


def agy_stream_model(raw) -> Optional[str]:
    """The model id agy says it ran: `init.model` of the first `init` event (present
    when `--model` was passed — measured 1.2.17). None when the stream does not say."""
    events = _agy_events(raw or "", lenient_tail=True) if isinstance(raw, str) else None
    for ev in events or []:
        if ev.get("event") == "init":
            init = ev.get("init")
            model = init.get("model") if isinstance(init, dict) else None
            return model if isinstance(model, str) and model else None
    return None


def agy_stream_models(raw) -> list:
    """The model each `init` event names, in stream order (None where one names none). A turn resumed after
    agy's soft deadline (task 261) is a second agy process with its own `init`, so a judge call's stream can
    hold two; [] when the stdout is not the agy stream or has no `init`."""
    events = _agy_events(raw or "", lenient_tail=True) if isinstance(raw, str) else None
    models = []
    for ev in events or []:
        if ev.get("event") == "init":
            init = ev.get("init")
            model = init.get("model") if isinstance(init, dict) else None
            models.append(model if isinstance(model, str) and model else None)
    return models


# Tools that reach the network. agy's tool list (the `init` event, 1.2.17) names its
# browser tools with `browser` somewhere in the name; the two below have no such marker.
_AGY_WEB_TOOLS = frozenset({"search_web", "read_url_content"})


def agy_web_tool_calls(raw) -> list:
    """Names of the web/browser tools the stream shows agy calling, in first-use
    order ([] when none, or when the stdout is not the agy stream)."""
    events = _agy_events(raw or "", lenient_tail=True) if isinstance(raw, str) else None
    seen: list = []
    for ev in events or []:
        step = ev.get("step_update") if ev.get("event") == "step_update" else None
        if not isinstance(step, dict) or step.get("step_type") != "tool":
            continue
        name = step.get("tool_name")
        if isinstance(name, str) and (name in _AGY_WEB_TOOLS or "browser" in name) and name not in seen:
            seen.append(name)
    return seen


def agy_stream_errors(stdout, stderr) -> list:
    """Errors agy reported about the TURN on a call that may still have exited 0:
    an `AGY_ERROR:` line (at the start of a stderr line — agy's structured error
    line), and an `agent_response` step in state `ERROR`. A TOOL step in state
    `ERROR` is that tool's own failure (a refused write, a non-zero command) and is
    not an error of the turn."""
    errors: list = []
    for ln in (stderr or "").splitlines() if isinstance(stderr, str) else []:
        if ln.startswith("AGY_ERROR:"):
            errors.append(ln.strip()[:300])
    events = _agy_events(stdout or "", lenient_tail=True) if isinstance(stdout, str) else None
    for ev in events or []:
        step = ev.get("step_update") if ev.get("event") == "step_update" else None
        if (isinstance(step, dict) and step.get("step_type") == "agent_response"
                and step.get("state") == "ERROR"):
            err = step.get("error")
            msg = err.get("message") if isinstance(err, dict) else err
            errors.append(f"agent_response step {step.get('step_index')} ended in ERROR"
                          + (f": {msg}"[:300] if isinstance(msg, str) and msg.strip() else ""))
    return errors


def _agy_stderr_error_lines(stderr) -> list:
    """The stderr lines on which agy STATES an error: those that start with `error:`
    or `AGY_ERROR:`. agy also logs on stderr; a log line is not a statement."""
    if not isinstance(stderr, str):
        return []
    return [ln.strip() for ln in stderr.split("\n")
            if ln.lower().startswith("error:") or ln.startswith("AGY_ERROR:")]


def _agy_step_error_messages(stdout) -> list:
    """The error text of every `agent_response` step in state ERROR: what agy states
    about the turn INSIDE the stream. Read for every exit code and from a cut stream
    too (a TOOL step in ERROR is that tool's own failure, not the turn's)."""
    events = _agy_events(stdout or "", lenient_tail=True) if isinstance(stdout, str) else None
    out: list = []
    for ev in events or []:
        step = ev.get("step_update") if ev.get("event") == "step_update" else None
        if (isinstance(step, dict) and step.get("step_type") == "agent_response"
                and step.get("state") == "ERROR"):
            err = step.get("error")
            msg = err.get("message") if isinstance(err, dict) else err
            if isinstance(msg, str) and msg.strip():
                out.append(msg.strip()[:300])
    return out


def _agy_error_lines(stdout, stderr) -> list:
    """Every line the CAUSE signatures are matched against: stderr lines that START
    with `error:` or `AGY_ERROR:`, the error text of the stream's `result` event, and
    the error of a response step in ERROR. Nothing else on stderr (agy logs there),
    nothing of the response."""
    lines = _agy_stderr_error_lines(stderr)
    got = extract_agy(stdout or "", lenient_tail=True) if isinstance(stdout, str) else None
    for err in (got[2] if got else []):
        lines.extend(ln.strip() for ln in err.split("\n") if ln.strip())
    lines.extend(_agy_step_error_messages(stdout))
    return lines


def _agy_statements(stdout, stderr) -> list:
    """EVERYTHING agy itself stated about what went wrong, from all four channels it
    has, for every exit code: the `short_error` of an `AGY_ERROR:` stderr line (the
    whole line when it has none), the body of an `error:` stderr line, the `error` of
    a `result` that is not SUCCESS, the error of a response step in ERROR. The
    channels usually repeat one sentence, so a statement contained in one already
    kept is dropped — but when they DIFFER every one is kept: the cause may be on any
    of them (post-D6 run 4). Never a sentence this parser wrote."""
    out: list = []

    def add(text) -> None:
        s = text.strip()[:300] if isinstance(text, str) else ""
        if s and not any(s in kept for kept in out):
            out.append(s)

    plain = []
    for ln in _agy_stderr_error_lines(stderr):
        if ln.startswith("AGY_ERROR:"):
            try:
                obj = json.loads(ln[len("AGY_ERROR:"):].strip())
            except (ValueError, TypeError):
                obj = None
            short = obj.get("short_error") if isinstance(obj, dict) else None
            add(short if isinstance(short, str) and short.strip() else ln)
        else:
            plain.append(ln[len("error:"):])
    for body in plain:
        add(body)
    res = _agy_last_result(stdout)
    if res is not None and res.get("status") != "SUCCESS":
        err = res.get("error")
        for ln in (err.split("\n") if isinstance(err, str) else []):
            if ln.strip():
                add(ln)
                break
    for msg in _agy_step_error_messages(stdout):
        add(msg)
    return out


def agy_error_statement(stdout, stderr) -> Optional[str]:
    """ONE line with what agy itself stated went wrong, for a failed call that has no
    named cause: every statement of `_agy_statements`, joined — its structured
    `AGY_ERROR:` short error first (it carries the canonical status and code —
    measured: `RESOURCE_EXHAUSTED (code 429): …`). None when agy said nothing — the
    sentences this parser writes about a broken stream ("no result event") are not
    agy's statement and never come back from here."""
    stated = _agy_statements(stdout, stderr)
    return "; ".join(stated)[:600] if stated else None


def agy_failure_cause(stdout, stderr) -> Optional[str]:
    """One line naming WHY a failed agy call failed, when agy itself says so — a
    rejected model selection, an exhausted quota, or a missing sign-in — else None.
    Reads agy's error channels only: stderr lines starting `error:` / `AGY_ERROR:`
    , the `result` event's `error` field and the error of a response step in ERROR
    (see the signature tables above). Call it only for a call that already failed."""
    lines = _agy_error_lines(stdout, stderr)

    def _hit(signs) -> Optional[str]:
        for ln in lines:
            low = ln.lower()
            if any(s in low for s in signs):
                return ln[:240]
        return None

    hit = _hit(_AGY_MODEL_REJECTED)
    if hit:
        return f"agy rejects the model selection: {hit}"
    hit = _hit(_AGY_QUOTA_EXHAUSTED)
    if hit:
        return f"agy quota exhausted: {hit} — `agy -p /quota` shows when it resets"
    hit = _hit(_AGY_NOT_SIGNED_IN)
    if hit:
        return f"agy is not signed in: {hit} — run `agy` once in a terminal to sign in"
    return None


def agy_judge_output(result, format_judge_output, *, expected_model: Optional[str] = None,
                     web_search: bool = True) -> JudgeOutput:
    """`judge_output_from_result` for agy, plus the agy-specific rules. In order:

      1. the shared rule (`judge_output_from_result` over `extract_agy`) — except
         its "the CLI ignored the flag and printed prose" fallback: for agy, stdout
         that is not the stream-json envelope is a FAILED seat, because the envelope
         is the only source of the model that ran, the tools it used and whether the
         turn finished (impl panel r1);
      2. a turn that agy says went wrong although the call exited 0
         (`agy_stream_errors`) is a FAILED seat, its text kept as a diagnostic — and
         that statement leads the seat even when the output had already failed for
         its shape (empty, cut, not the envelope), unless the `result` itself is an
         ERROR result, whose own `error` leads;
      2b. a SUCCESS result without token usage is not a finished turn (the shape of
          agy's self-timeout; the caller's timeout rule catches it first when a limit
          is set — this is the same rule for a call with no limit). It comes AFTER
          rule 2: an error agy states is the cause, the missing usage only a shape;
      3. with `web_search=False`, a stream that shows a web or browser tool call is a
         FAILED seat — agy has no flag to switch those tools off, and a judge that
         browsed did not review under the conditions the caller set;
      4. on a PINNED seat the stream must name the pinned model: another model, or
         no `init.model` at all, is a FAILED seat (fail closed — impl panel r1). EVERY
         `init` must name it: a turn resumed after the soft deadline is a second agy
         process with its own `init` (task 261);
      5. any FAILED call whose cause agy names (`agy_failure_cause`) gets that cause
         as its FIRST line — `(FAILED — agy quota exhausted: …)` — ahead of the
         standard block, so judge.md, the spend record's `error` and a quota-aware
         caller read it without parsing tails; the cause is looked for on all four
         channels agy has (`AGY_ERROR:` and `error:` stderr lines, an ERROR result's
         error, a response step in ERROR). Any other failed call leads with
         `(FAILED — agy reported: <everything agy stated, joined>)` unless its first
         line already holds all of it — never a sentence this parser wrote, whatever
         the exit code. The first line is the ONLY part of a failed agy seat a consumer
         should classify from: the tails contain untrusted stdout.

    The caller handles agy's timeout BEFORE this (see `agy_timed_out`)."""
    out = judge_output_from_result(result, extract_agy, format_judge_output)
    text, usage = str(out), out.usage
    failed = text.lstrip().startswith(("(FAILED", "(no output)"))
    if not failed and extract_agy(result.stdout or "") is None:
        failed = True
        text = ("(FAILED — agy returned no stream-json envelope although one was requested)"
                f"\n\n[stdout, not accepted as a review]\n{text}")
    errs = agy_stream_errors(result.stdout, result.stderr) if result.returncode == 0 else []
    res = _agy_last_result(result.stdout)
    if errs and not (res is not None and res.get("status") != "SUCCESS"):
        # What agy says about the turn LEADS the seat — also when the output had already
        # failed for its shape (no review text, a cut stream, no envelope, nothing at
        # all): the stated cause must not be lost behind the shape (post-D6 run 3). A
        # `result` that is not SUCCESS already leads with agy's own `result.error`.
        label = "[the output, not a review]" if failed else "[partial text before the failure]"
        text = (f"(FAILED — the agy turn reported an error: {'; '.join(errs)[:600]})"
                f"\n\n{label}\n{text}")
        failed = True
    if not failed and agy_success_without_usage(result.stdout):
        # after the turn's own errors: a stated cause beats this inference (post-D6 run 3)
        failed = True
        text = ("(FAILED — agy reported a turn with no token usage: the shape of its own timeout, "
                f"not of a finished turn)\n\n[text, possibly partial]\n{text}")
    if not failed and not web_search:
        web = agy_web_tool_calls(result.stdout)
        if web:
            return JudgeOutput(
                f"(FAILED — agy used a web tool ({', '.join(web)}) although web search is off "
                f"for this review)\n\n[text written with web access, not this seat's review]\n{text}",
                usage=usage)
    if not failed and expected_model:
        # every `init` must name the pin: a resumed turn (task 261) is a second process with its own
        models = agy_stream_models(result.stdout)
        ran = next((m for m in models if m != expected_model), expected_model) if models else None
        if ran != expected_model:
            said = (f"agy ran model {ran!r}, not the pinned {expected_model!r}" if ran else
                    f"agy did not report the model it ran (pinned: {expected_model!r})")
            return JudgeOutput(
                f"(FAILED — {said})\n\n"
                f"[text of an unconfirmed model, not this seat's review]\n{text}", usage=usage)
    if failed:
        cause = agy_failure_cause(result.stdout, result.stderr)
        if cause is None:
            # No named cause: the first line must carry EVERYTHING agy itself stated,
            # read from its real channels (`_agy_statements`) — a consumer (the journal,
            # judgebench) then never has to look in the tails, where stdout text could
            # imitate it, and a cause that only ONE channel names is not dropped. One
            # rule for every exit code and every shape of the output (the failure-shape
            # matrix in tests/test_agy_judge.py). A seat whose first line already holds
            # all of it — an exit-0 ERROR result's own error, the turn-error header —
            # is left as it is.
            stated = _agy_statements(result.stdout, result.stderr)
            first = text.lstrip().split("\n", 1)[0]
            if stated and not all(s in first for s in stated):
                cause = "agy reported: " + "; ".join(stated)[:600]
        if cause:
            text = f"(FAILED — {cause})\n\n{text}"
        return JudgeOutput(text, usage=usage)
    return out


def parse_usage(raw) -> Optional[dict]:
    """Best-effort token usage from a CLI's structured stdout — the ONE parser.
    Recognizes (a) a single JSON object carrying ``usage.input_tokens`` /
    ``usage.output_tokens`` (grok json), (b) codex JSONL whose
    LAST ``turn.completed`` carries them, (c) the agy stream, whose terminal
    ``result`` event carries them and (d) claude's result object (``extract_claude``,
    task 232). Anything else → None. Never fabricates."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    got = extract_agy(raw)            # before (a): a one-line agy stream is also one JSON object
    if got is not None:
        return got[1]
    got = extract_claude(raw)         # before (a) too: its top-level `usage` is zero on a budget stop (task 232)
    if got is not None:
        return got[1]
    obj = _json_object(raw)
    if obj is not None:
        return _usage_from_obj(obj.get("usage"))
    got = extract_codex(raw)
    if got is not None:
        return got[1]
    return None


def judge_output_from_result(result, extract, format_judge_output) -> JudgeOutput:
    """Turn a judge subprocess result into the review text the callers expect,
    CARRYING the usage parsed from the CLI's structured stdout. One rule for both
    adapters (task 056, plan-panel + impl-round-1 convergent findings):

      * rc != 0  → `format_judge_output(result)` unchanged (`(FAILED — exit N)` +
                   labeled tails, so failure signatures stay scannable) — but the
                   usage frame, if the CLI emitted one before failing, is still
                   recorded: the tokens were spent.
      * empty    → `(no output)` (the T139 rule, via format_judge_output).
      * stdout is genuinely NON-JSON prose (a CLI that ignored the flag) →
                   returned verbatim, usage None — legacy behaviour.
      * JSON-looking stdout that is not the recognized envelope (a truncated
                   object, another schema, a codex protocol stream with a stray
                   line or a missing terminal `turn.completed`) → `(FAILED — …)`:
                   structured output was requested, so garbage never becomes a
                   "review" (round 2).
      * recognized envelope WITH error events → `(FAILED — …reported an error: …)`
                   even when a message text exists (kept after the marker as a
                   diagnostic); usage carried.
      * recognized envelope WITHOUT review text → `(FAILED — … no review text)`.
      * recognized envelope WITH review text and no errors → that text, usage carried.

    Every failure marker starts with `(FAILED — ` so `judge_failed` fails the seat
    AND `_judge_status` journals `fail` (tokens spent) on the panel, single and
    tail-cert paths alike — never the no-cost `dnf`, never a clean PASS.
    """
    raw = result.stdout or ""
    got = extract(raw) if raw.strip() else None
    usage = got[1] if got else None
    if result.returncode != 0:
        return JudgeOutput(format_judge_output(result), usage=usage)
    if not raw.strip():
        return JudgeOutput(format_judge_output(result))          # "(no output)"
    if got is None:
        looks_json = any(ln.lstrip()[:1] in ("{", "[") for ln in raw.splitlines() if ln.strip())
        if looks_json or (extract is extract_codex and codex_protocol_detected(raw)):
            # Structured output was REQUESTED: stdout with ANY JSON-looking line
            # that is not the recognized envelope (truncated object, another
            # schema, a stray line before or inside the event stream) fails
            # closed — never a verbatim "review" (rounds 2-3, convergent). Only
            # stdout with NO JSON-looking line (a CLI that ignored the flag and
            # printed prose) keeps the verbatim legacy path.
            return JudgeOutput("(FAILED — malformed or unrecognized structured judge output: "
                               f"{raw.strip()[:300]!r})")
        return JudgeOutput(format_judge_output(result))          # verbatim prose
    text, _usage, errors = got
    detail = ("; ".join(e.strip() for e in errors if e.strip()))[:800]
    if errors:
        msg = f"(FAILED — the judge CLI reported an error: {detail})"
        if text.strip():
            msg += f"\n\n[partial text before the failure]\n{text.strip()}"
        return JudgeOutput(msg, usage=usage)
    if text.strip():
        return JudgeOutput(text.strip(), usage=usage)
    return JudgeOutput("(FAILED — structured judge output carried no review text)", usage=usage)


def salvage_text(provider: str, raw: str) -> str:
    """What to persist from a judge killed at the hard timeout. For codex the
    partial stdout is JSONL frames; hand the operator the last COMPLETED
    ``agent_message`` when there is one (readable partial findings), else the raw
    frames. grok's json mode emits its one object at the end, so a timed-out grok
    seat has nothing structured to salvage — raw (usually empty) is returned as
    today. agy (`agy` on the panel path, `antigravity` on the single-judge path)
    emits NDJSON: the `result` event's `response` when there is one (agy's own
    timeout returns the partial text there), else the text deltas of the last
    answer it was writing; a stream with no text at all yields "" — the protocol
    frames are never handed over as findings. claude prints its one result object at
    the end: when it is there, its review (or nothing); otherwise the raw text (task 232).
    Other providers pass through."""
    text = (raw or "").strip()
    if provider == "codex" and text:
        got = extract_codex(text, lenient_tail=True)   # a kill leaves one cut frame
        if got is not None and got[0].strip():
            return got[0].strip()
    if provider == "claude" and text:
        got = extract_claude(text)                     # its one object comes at the end (task 232)
        if got is not None:
            return got[0].strip()                      # the review, or nothing — never the envelope
    if provider in ("agy", "antigravity") and text:
        events = _agy_events(text, lenient_tail=True)
        if events is not None:
            got = extract_agy(text, lenient_tail=True)
            if got is not None and got[0].strip():
                return got[0].strip()
            deltas: dict = {}
            for ev in events:
                step = ev.get("step_update") if ev.get("event") == "step_update" else None
                if (isinstance(step, dict) and step.get("step_type") == "agent_response"
                        and isinstance(step.get("text_delta"), str)):
                    deltas.setdefault(step.get("step_index"), []).append(step["text_delta"])
            if deltas:
                return "".join(deltas[list(deltas)[-1]]).strip()
            return ""
    return text
