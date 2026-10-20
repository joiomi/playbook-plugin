"""
AntigravityAdapter — provider adapter for Google's Antigravity CLI (`agy`).

Two halves with different standing:

JUDGE PATH — rewritten for agy 1.2.17 and measured on 2026-10-05 (task 111; Linux,
a Google AI Pro sign-in; the complete outputs are in that task's record and the
captured fixtures in tests/fixtures/agy-1.2.17/). The seat is `judge: experimental`:
it works on the machine it was measured on and carries no support claim.

  * Why agy at all: the `gemini` CLI (0.62.0) refuses a personal Google login —
    the owner's attempt answered "This client is no longer supported for Gemini
    Code Assist for individuals … migrate to the Antigravity suite" — so a Gemini
    judge paid by a consumer subscription, not an API key, goes through `agy`.
  * Prompt on stdin: `agy --input-format stream-json --output-format stream-json`
    with NO `-p/--print` (a string flag — it would take the next token as the
    prompt) reads one NDJSON line per turn: `{"event":"user","message":{"role":
    "user","content":…}}`. A 150 KB prompt arrives whole. Nothing of the prompt is
    on argv, so the per-argument cap does not apply.
  * Pinned model: a seat pins a WHOLE model id (`gemini-3.8-flash-high` — the id
    carries the effort; `<base>:<effort>` is refused). agy rejects an unknown id
    before any turn, and the stream's `init` event echoes the model value agy
    accepted: a pinned seat whose stream carries another value, or none, fails.
  * Read-only is the OS sandbox's doing, NOT `--mode plan`'s. Plan mode is passed
    and agy applies it, but under `--dangerously-skip-permissions` it did not stop
    a file write (measured); and WITHOUT that flag headless agy auto-denies the
    first tool request and ends the turn with an empty response. So the seat runs
    like every other judge — `sandbox.run(..., project_writable=False)` prepends
    the bypass flag and the read-only project bind refuses the write. Where no OS
    sandbox is usable the seat is uncontained, exactly as the other judges are.
  * Credentials in place: agy keeps its sign-in in the OS keyring (Secret Service
    over the session D-Bus — an empty HOME alone stays signed in) and its state in
    ~/.gemini, which the sandbox binds read-write at its real path. Nothing is
    copied. The judge environment drops GEMINI_API_KEY, GOOGLE_API_KEY and
    GOOGLE_APPLICATION_CREDENTIALS: this seat is for a signed-in subscription, never
    per-call or per-project billing.
  * Output: NDJSON events ending in `{"event":"result","result":{status, response,
    usage,…}}`; `provider/usage.py` (the agy section) is the one parser and lists
    the ways an agy call can exit 0 and still not be a review — its own
    `--print-timeout` expiry (exit 0, `status: SUCCESS`, partial text), an
    auto-denied tool, a mid-turn error, a web or browser tool call when web search
    is off. agy has no flag that disables its web tools.
  * Live check, same day and machine: a one-seat agy panel and a single-judge
    review returned reviews and left spend records whose token counts equal agy's.
  * A real quota stop (captured in that task's exam): exit 3 mid-turn, "Individual
    quota reached. … Resets in 34m13s." — the seat fails with that sentence first.
  * Not measured: an expired sign-in and a depleted AI-credit
    balance (their handling follows agy's documented messages — the fixtures
    README says which files are constructed).

MAIN-AGENT PATH — written for agy 1.0.2, experimental, NOT touched by task 111
(the non-judge `headless_argv` shape, hooks, bootstrap, launch, the session log).
What follows describes that older CLI and may be out of date:

agy v1.0.2 (Go-based, brew cask) stores state under ~/.gemini/antigravity/ —
bootstrap file is GEMINI.md (auto-loaded by agy from project cwd, same convention
as ~/.gemini/GEMINI.md at user scope).

Hook surface: agy v1.0.2 has a Claude-compatible plugin loader that accepts
PreToolUse / PostToolUse / UserPromptSubmit / Stop hooks via project-local
plugin manifests. install_hooks writes the manifest (T134 W5a).

Session identity: no AGY_SESSION_ID env var. Resolution order:
  1. $PLAYBOOK_SESSION_ID (set by scripts/playbook-agy wrapper — preferred)
  2. PID-walk fallback: find 'agy' in parent process chain, use pid-<N>

Session transcript: JSONL at
    ~/.gemini/antigravity/brain/<uuid>/.system_generated/logs/transcript.jsonl
Records of interest: source=USER_EXPLICIT, type=USER_INPUT — content wrapped
in <USER_REQUEST>...</USER_REQUEST>, optionally followed by <ADDITIONAL_METADATA>
and <USER_SETTINGS_CHANGE> blocks.

Panel-review participation: a configured seat names its model (`agy:<id>`); the
legacy no-config fan-out contributes one unpinned seat (None) that runs whatever
model is selected in agy.
"""

from __future__ import annotations
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Mapping, Optional

from ..adapter import ProviderAdapter, Invocation
from ..capabilities import ProviderCapabilities


_USER_REQUEST_RE = re.compile(
    r"<USER_REQUEST>(.*?)</USER_REQUEST>",
    re.DOTALL,
)

# ── judge path (agy 1.2.17, task 111) ────────────────────────────────────────

# agy has no flag that switches its web and browser tools off, so with web search
# off the seat is told in one line ahead of the prompt (and the stream is checked
# afterwards — provider.usage.agy_web_tool_calls).
_NO_WEB_TOOLS = ("Web search is off for this review: do not call search_web, "
                 "read_url_content or any browser tool.")

# Never handed to a judge or probe: credentials through which agy could bill per
# call instead of using the signed-in subscription — the two API-key variables, and
# GOOGLE_APPLICATION_CREDENTIALS (a service-account file: a project-billed path; no
# measurement shows agy prefers its keyring sign-in over it — impl panel r2). The
# cost: an agy signed in through ADC cannot serve as this experimental judge seat.
_API_KEY_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")

# The killer sits this far above agy's own `--print-timeout`, so agy can return its
# partial text first (it then exits 0 — provider.usage.agy_timed_out catches that).
_KILL_GRACE_SECS = 30

# A turn cut at the soft deadline is resumed only with at least this long left before the hard limit: the
# resumed turn re-reads the conversation and writes the review (8-10 s on a small one, measured 2026-10-20).
_RESUME_MIN_SECS = 60


def _text(raw) -> str:
    """A subprocess stream as text (TimeoutExpired carries bytes or str, or None)."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw if isinstance(raw, str) else ""


def _complete_events(stdout: str) -> str:
    """`stdout`'s lines that are whole agy events (JSON objects with a string `event`), each ending in a newline.
    A turn the killer stopped can end in a cut line, and the parser reads a stream only when every line is one."""
    kept = []
    for line in stdout.split("\n"):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        # only an event: a JSON object of any other shape would void the parser's strict read (impl r1)
        if isinstance(ev, dict) and isinstance(ev.get("event"), str):
            kept.append(line + "\n")
    return "".join(kept)


def _conversation_id(stdout: str) -> Optional[str]:
    """The conversation id of agy's first `init` event (its first line, measured on 1.4.3), or None."""
    for line in stdout.split("\n"):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue        # a stray line before the stream (W6's live review, agy) does not lose the id
        if isinstance(ev, dict) and ev.get("event") == "init":
            cid = ev.get("conversation_id")
            # only an id that can be one argv element: no option, no path, no space
            return cid if isinstance(cid, str) and re.fullmatch(r"[A-Za-z0-9-]{1,100}", cid) and not cid.startswith("-") else None
    return None


def _without_print_timeout(argv: list) -> list:
    """`argv` without its `--print-timeout <value>` pair."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a == "--print-timeout":
            skip = True
        else:
            out.append(a)
    return out


def _timed_out(cmd: list, timeout, stdout: str, stderr: str, conversation: Optional[str], *,
               soft_cut: bool = False, resumed: bool = False, fallback: str = "") -> subprocess.TimeoutExpired:
    """The `TimeoutExpired` the callers' timeout paths take, naming agy's conversation when it is known, saying
    whether the turn had been cut at the soft deadline (the seat's spend record carries it), and carrying the
    usage of the stream's last complete `result` — a resume the killer stopped leaves a cut stream whose strict
    read finds none, while the cut turn's own usage is known (impl r1). `resumed` says the timeout is the
    resume's (it ended near the hard limit), not a cut turn's that was never resumed (near the soft one, impl
    r2); `fallback` is the cut turn's stream, read when the combined one gives no usage (a zero-usage resume)."""
    from provider import usage as _usage
    exc = subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
    exc.agy_conversation = conversation   # type: ignore[attr-defined]
    exc.agy_soft_cut = soft_cut           # type: ignore[attr-defined]
    exc.agy_resumed = resumed             # type: ignore[attr-defined]
    exc.agy_usage = ((_usage.parse_usage(_complete_events(stdout or ""))
                      or _usage.parse_usage(_complete_events(fallback or "")))
                     if soft_cut else None)   # type: ignore[attr-defined]
    return exc


def finish_resumed(out, result, conversation: Optional[str], soft_timeout_secs: "int | None"):
    """The parsed seat text of a call whose turn was cut at the soft deadline and resumed (`run_judge_turns`
    returned a conversation id); any other call's text is returned as it is. In order:
      * a seat that already failed keeps its marker first;
      * a resumed call whose own stream does not stand as its evidence (`agy_resume_problem`) fails;
      * an answered seat's text gets one line first, saying it was written after the soft deadline.
    Its usage is kept, and `soft_cut` says the turn was resumed."""
    if not conversation:
        return out
    from provider.usage import JudgeOutput
    from tasks.core import human_duration
    from tasks.models_check import judge_failed
    usage, text = getattr(out, "usage", None), str(out)
    problem = getattr(result, "agy_resume_problem", None)
    # the panel's own failure predicate: a review may open with "(1) …" (W6's live review, agy)
    if judge_failed(text):
        return JudgeOutput(text, usage=usage, soft_cut="resumed")
    if problem:
        return JudgeOutput(f"(FAILED — {problem})\n\n[the resumed call's text, not this seat's review]\n{text}",
                           usage=usage, soft_cut="resumed")
    soft = f" (~{human_duration(soft_timeout_secs)})" if soft_timeout_secs else ""
    note = (f"**[playbook] Soft deadline:** agy reached its soft deadline{soft} mid-turn; the findings below were written "
            f"in a resumed turn of the same conversation (`{conversation}`), told to call no tool and to say what "
            "it did not check.")
    return JudgeOutput(f"{note}\n\n{text}", usage=usage, soft_cut="resumed")


def validate_model_id(model: str) -> str:
    """An agy judge pin → the model id handed to agy's model flag, or ValueError.

    A pin is a WHOLE id as `agy models` lists it: the id already carries the effort
    (`gemini-3.8-flash-high`). The `<base>:<effort>` form other providers use is
    refused: agy would accept `--model <base>` plus its effort flag, but its stream
    then names only `<base>` (measured), so the seat could not check which variant it
    was given."""
    mid = (model or "").strip()
    if not mid or ":" in mid or any(ch.isspace() for ch in mid):
        base, _, effort = mid.partition(":")
        hint = (f" — did you mean {base.strip()}-{effort.strip()}?"
                if base.strip() and effort.strip() and not any(c.isspace() for c in mid) else "")
        raise ValueError(
            f"bad agy pin {model!r}: pin a whole model id from `agy models` (the id carries "
            f"the effort, e.g. gemini-3.8-flash-high), not <base>:<effort>{hint}")
    return mid


def pinned_model_id(model: Optional[str]) -> Optional[str]:
    """The value agy's `init` event must carry for this seat: the pinned id, or None
    for an unpinned seat. `init.model` echoes the model value agy ACCEPTED (measured);
    that agy really serves it rests on agy's own behaviour — it refuses an unknown or
    contradictory selection before any turn (also measured)."""
    return validate_model_id(model) if model else None


def judge_env(env: "Mapping[str, str]", session_id: str = "judge") -> "dict[str, str]":
    """A copy of `env` for a judge/probe call: the billed-credential variables dropped, the
    Playbook session id set. HOME, DBUS_SESSION_BUS_ADDRESS and XDG_RUNTIME_DIR pass
    through untouched — they are how agy reaches its real sign-in."""
    out = dict(env)
    for var in _API_KEY_ENV:
        out.pop(var, None)
    out["PLAYBOOK_SESSION_ID"] = session_id or "judge"
    return out


class AntigravityAdapter(ProviderAdapter):
    """Provider adapter for Antigravity CLI (`agy`)."""

    _BRAIN_DIR = Path.home() / ".gemini" / "antigravity" / "brain"

    def __init__(self, session_id: str, project_root: Path) -> None:
        self._session_id = session_id
        self._project_root = project_root
        self._transcript_path: Optional[Path] = None  # cached after first lookup

    # ── CLI identity ─────────────────────────────────────────────────────────

    @classmethod
    def binary_name(cls) -> str:
        return "agy"

    @classmethod
    def context_transport(cls) -> str:
        # The judge prompt rides stdin (stream-json, task 111) — no argv size cap.
        return "stdin"

    @classmethod
    def panel_variants(cls) -> list[Optional[str]]:
        # Legacy no-config fan-out only: one unpinned seat (agy's selected model).
        # A configured panel names its model: `agy:<id>`.
        return [None]

    def headless_argv(
        self,
        prompt: str,
        model: Optional[str],
        *,
        context: str = "",
        bare: bool = False,
        stream: bool = False,
        structured: bool = False,
    ) -> Invocation:
        if structured:
            return self._judge_argv(prompt, model, context=context, bare=bare)
        # agy 1.1.x `--print`/`--prompt` is a STRING flag: the prompt is its
        # VALUE, not stdin. (Bare `agy --print` errors "flag needs an argument:
        # -print"; agy has no stdin prompt path in 1.1.1 — `--print -` just
        # treats "-" as the literal prompt.) The prompt therefore rides right
        # after `--print` on argv; keeping it adjacent is load-bearing, because
        # run_headless_judge appends `--print-timeout <secs>s` afterwards — if
        # `--print` had no value, it would swallow `--print-timeout` as the
        # prompt (the bug that made every agy judge investigate the string
        # "--print-timeout" instead of reviewing; task 013). --print ignores cwd
        # so --add-dir exposes the project tree; no model flag (rejected).
        # Bypass flag (--dangerously-skip-permissions) prepended by sandbox.
        full_prompt = prompt if (bare or not context) else f"{context}\n\n---\n\n{prompt}"
        argv = ["--add-dir", str(self._project_root), "--print", full_prompt]
        return Invocation(argv, stdin=None)

    def _judge_argv(self, prompt: str, model: Optional[str], *, context: str = "",
                    bare: bool = False) -> Invocation:
        """The JUDGE invocation (agy 1.2.17): everything the model reads is ONE NDJSON
        line on stdin; argv carries only flags. `-p/--print` is deliberately absent —
        it is a string flag, and `--input-format stream-json` already means print
        mode. No `--add-dir`: agy uses the cwd, which sandbox.run sets to the project.
        ASCII-escaped JSON (the json.dumps default) keeps the line independent of the
        pipe's encoding and survives a lone surrogate."""
        full_prompt = prompt if (bare or not context) else f"{context}\n\n---\n\n{prompt}"
        argv = ["--input-format", "stream-json", "--output-format", "stream-json"]
        if model:
            argv += ["--model", validate_model_id(model)]
        argv += ["--mode", "plan"]
        line = json.dumps({"event": "user", "message": {"role": "user", "content": full_prompt}})
        return Invocation(argv, stdin=line + "\n")

    def judge_invocation(self, prompt: str, model: Optional[str], *, context: str = "",
                         web_search: bool = False,
                         timeout_secs: "int | None" = None) -> Invocation:
        """`_judge_argv` plus the two judge-only extras, shared by the panel seat
        (`run_headless_judge`) and the single-judge arm in tasks/review.py so the two
        cannot drift: the no-web line ahead of the prompt when web search is off, and
        `--print-timeout` when the review has a finite hard timeout (omitted when
        unlimited — agy's default waits for the turn). Raises ValueError on a bad pin."""
        if not web_search:
            prompt = f"{_NO_WEB_TOOLS}\n\n{prompt}"
        inv = self._judge_argv(prompt, model, context=context)
        argv = list(inv.argv)
        if timeout_secs is not None:
            argv += ["--print-timeout", f"{timeout_secs}s"]
        return Invocation(argv, stdin=inv.stdin)

    # Task 261: agy has no clock, so a soft deadline in the prompt did nothing for it. Where the review has a soft
    # deadline below a finite hard one, the judge call is cut at the soft deadline and resumed (`run_judge_turns`).
    # The panel passes `soft_timeout_secs` only to an adapter that says so here.
    supports_soft_deadline = True

    def _judge_call(self, argv: list, stdin: Optional[str], timeout_secs: "int | None", *, project_view, env):
        """One sandboxed agy judge call → (CompletedProcess, elapsed seconds). The subprocess killer sits above
        agy's own `--print-timeout`, so agy can return first; unlimited stays unlimited (no arithmetic on None),
        and sandbox.run then skips its process-group killer. encoding="utf-8" guards the decode against a
        non-UTF-8 locale codec, and errors="replace" keeps a stray byte from failing the call: both paths
        decode the same way (the single judge always did). Raises `subprocess.TimeoutExpired` when the killer
        fired."""
        from provider import sandbox as _sandbox
        run_timeout = None if timeout_secs is None else timeout_secs + _KILL_GRACE_SECS
        t0 = time.monotonic()
        try:
            result = _sandbox.run(
                "agy", argv,
                project_root=self._project_root,
                project_writable=False,   # judge is read-only — cannot mutate repo/task.md
                project_view=project_view,   # task 231: the review's fixed copy, mounted at the project path
                env=env,
                input=stdin,
                capture_output=True, text=True, timeout=run_timeout, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired as expired:
            expired.agy_elapsed = time.monotonic() - t0     # type: ignore[attr-defined]  # reaping included
            raise
        return result, time.monotonic() - t0

    def run_judge_turns(self, prompt: str, model: Optional[str], *, context: str, web_search: bool,
                        timeout_secs: "int | None", soft_timeout_secs: "int | None" = None,
                        project_view=None, env=None) -> "tuple[subprocess.CompletedProcess, Optional[str]]":
        """The judge call, cut at the soft deadline and resumed (task 261) → (result, resumed conversation id).

        With a finite hard limit and a soft deadline below it, the first call gets `--print-timeout <soft>`. A
        turn cut there is resumed in the same conversation (`--conversation <id>`, the id from its first `init`
        event) with `soft_deadline_resume_instruction` under the time left before the hard limit, if at least
        `_RESUME_MIN_SECS` remain; agy still holds everything the turn read (measured on 1.4.3). The result's
        stdout is the cut turn's complete event lines followed by the resumed stream, its stderr and exit status
        the resumed call's: the last `result` is the review and the whole call's usage (agy's resumed `result`
        holds the conversation's running total), and the parser's web-tool and model rules see both turns.

        Anything else is today's single call, unchanged. A call that times out — the single call, a cut turn
        that cannot be resumed, or its resume — raises `subprocess.TimeoutExpired` as before, carrying
        `agy_conversation` when the conversation id is known, so the seat's record can name it."""
        from provider import usage as _usage
        if env is None:
            env = judge_env(os.environ, self._session_id)
        hard = timeout_secs
        # the cut only where its resume has room: under _RESUME_MIN_SECS + the killer's grace between the two,
        # cutting would lose time the single call had (plan r1 P4)
        soft = (soft_timeout_secs if soft_timeout_secs is not None and hard is not None
                and 0 < soft_timeout_secs and hard - soft_timeout_secs >= _RESUME_MIN_SECS + _KILL_GRACE_SECS
                else None)
        first = soft if soft is not None else hard
        inv = self.judge_invocation(prompt, model, context=context, web_search=web_search, timeout_secs=first)
        result = None
        try:
            result, elapsed = self._judge_call(inv.argv, inv.stdin, first, project_view=project_view, env=env)
            cut = _usage.agy_timed_out(result.stderr, first, elapsed, result.stdout)
            out1, err1 = result.stdout or "", result.stderr or ""
        except subprocess.TimeoutExpired as expired:
            if soft is None:
                # today's timeout, now naming the conversation too (W6's live review, agy)
                _out = _text(expired.stdout or expired.output)
                raise _timed_out(list(expired.cmd) if isinstance(expired.cmd, (list, tuple)) else [str(expired.cmd)],
                                 expired.timeout, _out, _text(expired.stderr), _conversation_id(_out))
            # the killer, not agy, stopped the cut turn: its conversation can still be resumed. The time it took is
            # measured (`_judge_call`), reaping included, so the resume never runs past the hard limit + the grace
            elapsed, cut = max(float(getattr(expired, "agy_elapsed", 0.0)), float(soft + _KILL_GRACE_SECS)), True
            out1, err1 = _text(expired.stdout or expired.output), _text(expired.stderr)
        if not cut and result is not None:
            return result, None
        conversation = _conversation_id(out1)
        if soft is None or hard is None:
            raise _timed_out(["agy", *inv.argv], first or 0, out1, err1, conversation)
        left = int(hard - elapsed)
        if conversation is None or left < _RESUME_MIN_SECS:
            raise _timed_out(["agy", *inv.argv], soft, out1, err1, conversation, soft_cut=True)
        # a cut turn in which agy stated an error — a non-zero exit, a result that is not SUCCESS, an `error:` or
        # `AGY_ERROR:` line, a response step in ERROR — is not resumed: a good resume would mask it, so it times
        # out as before (impl r1: sol, astra)
        # read on the cut turn's EVENTS: a stray line before the stream would make the parser reject the raw
        # stdout whole, and with it these checks, while the cleaned stream joins the resume (impl r2: astra, sol)
        norm1 = _complete_events(out1)
        last1 = _usage._agy_last_result(norm1)
        if ((result is not None and result.returncode != 0)
                or (last1 is not None and last1.get("status") != "SUCCESS")
                or _usage.agy_error_statement(norm1, _usage.agy_stderr_without_timeout(err1))):
            raise _timed_out(["agy", *inv.argv], soft, out1, err1, conversation, soft_cut=True)
        from tasks.template import soft_deadline_resume_instruction
        argv2 = ["--conversation", conversation] + _without_print_timeout(inv.argv) + ["--print-timeout", f"{left}s"]
        stdin2 = json.dumps({"event": "user", "message": {"role": "user",
                             "content": soft_deadline_resume_instruction(soft)}}) + "\n"
        head = _complete_events(out1)
        # what agy said in the cut turn stays (an `AGY_ERROR:` there still fails the seat); only the line saying
        # the turn was cut goes (plan r1 P1)
        err_head = _usage.agy_stderr_without_timeout(err1)
        if err_head and not err_head.endswith("\n"):
            err_head += "\n"   # a killed turn's last log line must not swallow the resume's first line (impl r1, sol)
        try:
            result2, elapsed2 = self._judge_call(argv2, stdin2, left, project_view=project_view, env=env)
        except subprocess.TimeoutExpired as expired:
            raise _timed_out(["agy", *argv2], left, head + _text(expired.stdout or expired.output),
                             err_head + _text(expired.stderr), conversation, soft_cut=True, resumed=True,
                             fallback=head)
        out2, err2 = result2.stdout or "", result2.stderr or ""
        # the resumed call's own timeout signals only: the cut turn's stderr always carries agy's timeout line
        if _usage.agy_timed_out(err2, left, elapsed2, out2):
            raise _timed_out(["agy", *argv2], left, head + out2, err_head + err2, conversation, soft_cut=True,
                             resumed=True, fallback=head)
        last = _usage._agy_last_result(out2)
        if (result2.returncode != 0 or last is None or last.get("status") != "SUCCESS") \
                and _usage.agy_failure_cause(out2, err2) is None and _usage.agy_error_statement(out2, err2) is None:
            # the resume failed and agy said nothing about why: the seat timed out at its soft deadline, as before,
            # and its record names the conversation (plan r1, opus Minor). When agy says why, the parser fails the
            # seat with agy's words instead (impl r1, opus)
            raise _timed_out(["agy", *argv2], left, head + out2, err_head + err2, conversation, soft_cut=True,
                             resumed=True, fallback=head)
        combined = subprocess.CompletedProcess(["agy", *argv2], result2.returncode, stdout=head + out2,
                                               stderr=err_head + err2)
        # the resumed call stands on its own evidence, not on what the cut turn's events supply (plan r1 P2)
        combined.agy_resume_problem = _usage.agy_resumed_stream_problem(   # type: ignore[attr-defined]
            out2, conversation, pinned_model_id(model))
        return combined, conversation

    def run_headless_judge(
        self,
        prompt: str,
        model: Optional[str],
        system_context: str,
        *,
        web_search: bool,
        timeout_secs: "int | None",
        budget_usd: str,
        project_view=None,
        soft_timeout_secs: "int | None" = None,
    ) -> str:
        import shutil
        if not shutil.which(self.binary_name()):
            return f"(error: {self.binary_name()} not found on PATH)"
        from provider import sandbox as _sandbox
        from provider import usage as _usage
        # agy's own timeout exits 0 with `status: SUCCESS` and PARTIAL text: `run_judge_turns` hands it to the
        # callers' timeout paths (marker first, partial text salvaged, journal `timeout`) exactly like a judge
        # the killer stopped — after resuming a turn cut at the soft deadline, when it can.
        result, resumed = self.run_judge_turns(
            prompt, model, context=system_context, web_search=web_search, timeout_secs=timeout_secs,
            soft_timeout_secs=soft_timeout_secs, project_view=project_view,
            env=judge_env(os.environ, self._session_id))
        out = _usage.agy_judge_output(
            result, _sandbox.format_judge_output,
            expected_model=pinned_model_id(model), web_search=web_search)
        return finish_resumed(out, result, resumed, soft_timeout_secs)

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def project_root(self) -> Path:
        return self._project_root

    # ── Bootstrap ────────────────────────────────────────────────────────────

    def bootstrap_file_name(self) -> str:
        return "GEMINI.md"

    def install_bootstrap(self, project_root: Path) -> None:
        """Write GEMINI.md teaching agy the Playbook workflow.

        agy auto-loads ~/.gemini/GEMINI.md at user scope; project-local
        GEMINI.md is read by agy when run from project cwd. Does not
        overwrite an existing GEMINI.md.
        """
        from tasks.atomic import atomic_write
        from tasks.template import antigravity_md_template
        target = project_root / "GEMINI.md"
        if not target.exists():
            atomic_write(target, antigravity_md_template())

    # ── Hooks ─────────────────────────────────────────────────────────────────

    _PLUGIN_NAME = "claude-playbook"

    def install_hooks(self, project_root: Path) -> None:
        """Install Playbook hooks globally with agy via plugin manifest.

        agy plugins live in ~/.gemini/config/plugins/<name>/ and must be registered
        via `agy plugin install <src>` — direct file writes are not picked up.
        This method builds a cached manifest in ~/.cache/claude-playbook/agy-plugin/
        then invokes `agy plugin install` to register it globally. Idempotent —
        re-install if the plugin already exists (refreshes hook script paths).
        """
        import shutil
        agy_bin = shutil.which("agy")
        if not agy_bin:
            print("  agy plugin   skipped: 'agy' not on PATH")
            return

        scripts_dir = self._resolve_playbook_scripts_dir()
        if scripts_dir is None:
            print("  agy plugin   skipped: could not resolve Playbook scripts dir")
            return

        cache_dir = self._build_plugin_manifest(scripts_dir)
        self._register_with_agy(agy_bin, cache_dir)

    def uninstall_hooks(self, project_root: Path) -> None:
        """Remove Playbook agy plugin registration."""
        import shutil
        agy_bin = shutil.which("agy")
        if not agy_bin:
            return
        subprocess.run(
            [agy_bin, "plugin", "uninstall", self._PLUGIN_NAME],
            capture_output=True, text=True,
        )

    def _resolve_playbook_scripts_dir(self) -> Optional[Path]:
        """Locate the directory containing Playbook hook scripts.

        Resolution order:
          1. $CLAUDE_PLUGIN_ROOT/scripts (set by Claude plugin loader)
          2. Walk up from this file: src/provider/adapters/antigravity.py
             → <repo>/scripts when running from the dev checkout.
        """
        env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
        if env_root:
            candidate = Path(env_root) / "scripts"
            if candidate.exists():
                return candidate
        # Walk up from src/provider/adapters/antigravity.py
        here = Path(__file__).resolve()
        # adapters → provider → src → repo root
        for parent in here.parents:
            candidate = parent / "scripts"
            if (candidate / "task-gate-hook").exists():
                return candidate
        return None

    def _build_plugin_manifest(self, scripts_dir: Path) -> Path:
        """Write the agy plugin manifest under ~/.cache/claude-playbook/agy-plugin/.

        Returns the manifest root path suitable for `agy plugin install <path>`.
        """
        from tasks.core import VERSION
        # Deferred import — setup-path only (adapter manifest build), where the
        # tasks package is importable (see provider/paths.py; same context that
        # already imports tasks.core.VERSION just above). Atomic so a crash can't
        # leave truncated installed-metadata / hook JSON that agy would reject.
        from tasks.atomic import atomic_write
        cache_dir = Path.home() / ".cache" / "claude-playbook" / "agy-plugin" / self._PLUGIN_NAME
        cache_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            cache_dir / "plugin.json",
            json.dumps({"name": self._PLUGIN_NAME, "version": VERSION}, indent=2),
        )
        hooks_dir = cache_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)

        def _entry(script: str, matcher: Optional[str] = None) -> dict:
            entry: dict = {
                "hooks": [{
                    "type": "command",
                    "command": str(scripts_dir / script),
                    "timeout": 5000,
                }],
            }
            if matcher is not None:
                entry["matcher"] = matcher
            return entry

        hooks_doc = {
            "hooks": {
                "PreToolUse":        [_entry("task-gate-hook",  matcher=".*")],
                "PostToolUse":       [_entry("state-echo-hook", matcher=".*")],
                "UserPromptSubmit":  [_entry("chat-log-hook")],
                "Stop":              [_entry("stop-hook")],
            }
        }
        atomic_write(hooks_dir / "hooks.json", json.dumps(hooks_doc, indent=2))
        return cache_dir

    def _register_with_agy(self, agy_bin: str, cache_dir: Path) -> None:
        """Invoke `agy plugin install <cache_dir>`. Idempotent w.r.t. agy's state."""
        # Uninstall first to guarantee a refresh (script paths may have changed
        # since the previous install). Ignore errors — plugin may not exist yet.
        subprocess.run(
            [agy_bin, "plugin", "uninstall", self._PLUGIN_NAME],
            capture_output=True, text=True,
        )
        result = subprocess.run(
            [agy_bin, "plugin", "install", str(cache_dir)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  agy plugin   installed ({self._PLUGIN_NAME})")
        else:
            print(f"  agy plugin   install failed: {result.stderr.strip()}")

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def launch_interactive(self, project_root: Path, **kwargs) -> int:
        """Launch `agy` TUI with PLAYBOOK_SESSION_ID pre-set."""
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        result = subprocess.run(["agy"], cwd=project_root, env=env, **kwargs)
        return result.returncode

    def launch_headless(self, project_root: Path, prompt: str, **kwargs) -> str:
        """Run `agy --print <prompt>` for a single non-interactive prompt.

        Uses --add-dir to expose the project tree — agy --print mode runs in
        its own scratch dir and ignores cwd otherwise. agy 1.1.x `--print` is a
        string flag taking the prompt as its value (no stdin path); the prompt
        must sit immediately after `--print`, before `--print-timeout` (else
        `--print` swallows `--print-timeout` as its value — see headless_argv).
        """
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        result = subprocess.run(
            ["agy", "--add-dir", str(project_root),
             "--print", prompt, "--print-timeout", "300s"],
            cwd=project_root, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **kwargs,
        )
        return result.stdout

    # ── Capabilities ─────────────────────────────────────────────────────────

    def detect_capabilities(self) -> ProviderCapabilities:
        """agy v1.0.2: plugin-system hooks (Claude-compatible schema, probed).

        Hook surface confirmed via `agy plugin validate` accepting Claude-shape
        hooks/hooks.json + binary strings (`PreToolUse`, `PostToolUse`, `Stop`,
        `HooksPanel`). Capability flags reflect plugin-installable hooks; actual
        hook firing under agy requires install_hooks (W5a) + smoke test (W6b).
        """
        log_base = self._BRAIN_DIR
        return ProviderCapabilities(
            provider="antigravity",
            has_user_prompt_hook=True,
            has_pre_tool_hook=True,
            has_post_tool_hook=True,
            has_stop_hook=True,
            session_id_in_payload=False,
            session_log_format="jsonl",
            session_log_base=log_base if log_base.exists() else None,
        )

    # ── Chat log ─────────────────────────────────────────────────────────────

    def session_log_path(self) -> Optional[Path]:
        """Find most recent transcript JSONL referencing the project cwd.

        Walks ~/.gemini/antigravity/brain/<uuid>/.system_generated/logs/transcript.jsonl
        and returns the file with newest mtime whose content mentions project_root.
        Verification is content-based (cwd appears in early USER_INPUT or tool_calls)
        because agy doesn't tag transcripts with cwd metadata directly.
        """
        if self._transcript_path is not None:
            return self._transcript_path
        if not self._BRAIN_DIR.exists():
            return None
        cwd_str = str(self._project_root)
        candidates: list[tuple[float, Path]] = []
        for brain_dir in self._BRAIN_DIR.iterdir():
            transcript = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if transcript.exists():
                candidates.append((transcript.stat().st_mtime, transcript))
        candidates.sort(reverse=True)  # newest first
        for _, path in candidates:
            try:
                # Read first ~8KB to check for cwd reference
                with open(path, "rb") as f:
                    head = f.read(8192).decode("utf-8", errors="replace")
                if cwd_str in head:
                    self._transcript_path = path
                    return path
            except OSError:
                continue
        return None

    def read_new_messages(self, since_offset: int) -> tuple[list[str], int]:
        """Read user messages from agy transcript since byte offset.

        Filters: source=USER_EXPLICIT, type=USER_INPUT.
        Cleans: unwraps <USER_REQUEST>...</USER_REQUEST>; strips trailing
        <ADDITIONAL_METADATA> / <USER_SETTINGS_CHANGE> blocks.
        Returns ([], since_offset) if no transcript found.
        """
        log_path = self.session_log_path()
        if log_path is None:
            return [], since_offset

        messages: list[str] = []
        new_offset = since_offset

        try:
            with open(log_path, "rb") as f:
                f.seek(since_offset)
                for raw_line in f:
                    new_offset += len(raw_line)
                    try:
                        obj = json.loads(raw_line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    # I18: skip non-dict records rather than AttributeError.
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("source") != "USER_EXPLICIT":
                        continue
                    if obj.get("type") != "USER_INPUT":
                        continue
                    content = obj.get("content", "")
                    if not isinstance(content, str):
                        continue
                    # Prefer the explicit <USER_REQUEST> wrapper when present;
                    # fall back to raw content stripped of trailing metadata blocks.
                    m = _USER_REQUEST_RE.search(content)
                    if m:
                        text = m.group(1).strip()
                    else:
                        # Strip trailing <ADDITIONAL_METADATA>...</ADDITIONAL_METADATA>
                        # and <USER_SETTINGS_CHANGE>...</USER_SETTINGS_CHANGE> blocks.
                        text = re.sub(
                            r"<(ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>.*?</\1>",
                            "",
                            content,
                            flags=re.DOTALL,
                        ).strip()
                    if text:
                        messages.append(text)
        except OSError:
            pass

        return messages, new_offset

    # ── Class method ─────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, project_root: Path) -> "AntigravityAdapter":
        """Construct adapter using best available session ID source.

        Priority:
        1. PLAYBOOK_SESSION_ID (set by scripts/playbook-agy wrapper)
        2. PID-walk to find 'agy' parent process
        """
        from .codex import _pid_walk_session_id
        session_id = os.environ.get("PLAYBOOK_SESSION_ID", "")
        if not session_id:
            session_id = _pid_walk_session_id(provider_names=["agy"])
        return cls(session_id=session_id, project_root=project_root)
