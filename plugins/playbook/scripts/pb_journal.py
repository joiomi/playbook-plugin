#!/usr/bin/env python3
"""Best-effort, append-only enforcement-event journal (log-only).

Branch feat/enforcement-journal — an OWNER-APPROVED feature-freeze exception
(decision 2026-08-21). One JSON object per enforcement DECISION, appended to the
lane-resolved `.agent/<lane>/journal/enforcement.jsonl` (root lane:
`.agent/journal/enforcement.jsonl`). No reader tooling ships here — that is
deferred to 1.6; this module only writes.

HARD CONTRACT — a journal failure must NEVER change an enforcement decision:
  * every public function swallows ALL errors (a one-line stderr warning at
    most). Callers additionally wrap the call, so this is defence in depth.
  * one O_APPEND write per record: no fsync, no locking, no read-modify-write.
    A record is emitted with a single os.write() of one line. POSIX guarantees
    that a write of at most PIPE_BUF bytes to a regular file opened O_APPEND is
    atomic against concurrent appenders (PIPE_BUF is >= 512 everywhere and 4096
    on Linux), so lines from separate hook processes do not interleave. This
    holds only while a record stays under PIPE_BUF; the command/path fields are
    capped (see _head) which keeps real records well under 512 bytes, but a
    pathological path could exceed it — an honest, accepted bound for a
    best-effort log, never a correctness risk (the decision was already made).
  * writes ONLY when the resolved lane dir already exists (the project is
    playbook-managed). The `journal/` subdir is created inside that existing
    lane; `.agent` itself is NEVER created as a side effect.

Stdlib only. Python 3.10+ floor (same as the rest of the plugin).
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from pathlib import Path

# Same username grammar as gate-echo-lib.sh::resolve_agent_dir and
# provider/paths.validate_username — kept deliberately in lockstep.
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

_HEAD_LIMIT = 200


def resolve_lane_dir(project_root) -> "Path | None":
    """Resolve the `.agent` lane dir WITHOUT creating anything.

    Multi-user: a valid `.agent/current_user` → `.agent/<name>`.
    Legacy / absent / unvalidatable marker → the root `.agent`.

    Fresh-clone shape (per-user lanes exist, no marker, no root `.agent/tasks`)
    → None: the true lane is unresolvable and answering the root would mint
    phantom root-lane state, exactly the leak the enforcing surfaces refuse
    (see gate-echo-lib.sh::lanes_without_marker and the S15 fixture). A
    best-effort log is never worth that, so skip.

    This is a best-effort logger, never an authority: where the enforcing
    resolvers would *raise* on an unreadable/invalid marker, here we fall back
    to the root lane so a decision still gets logged somewhere sane. Returns
    None on the fresh-clone shape or an unexpected internal error.
    """
    try:
        agent = Path(project_root) / ".agent"
        marker = agent / "current_user"
        if not marker.exists() and not (agent / "tasks").is_dir() and agent.is_dir():
            # No marker and no root lane: if any child lane has tasks/, this is a
            # fresh clone — refuse rather than write the shared root.
            for child in agent.iterdir():
                if (child / "tasks").is_dir():
                    return None
        try:
            raw = marker.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError, OSError):
            return agent
        lines = raw.splitlines()
        content = [ln for ln in lines if ln.strip()]
        name = lines[0].strip() if lines else ""
        if (len(content) == 1 and name not in (".", "..")
                and "/" not in name and _USER_RE.match(name)):
            return agent / name
        return agent
    except Exception:
        return None


def append(agent_dir, hook, decision, reason, session_id="",
           tool="", path="", command="", task="") -> None:
    """Append one enforcement-journal record. Never raises; returns None.

    `agent_dir` is the ALREADY-RESOLVED lane dir. The write is skipped silently
    unless it exists (playbook-managed project). The `journal/` subdir is made
    inside it; `.agent` is never minted here.
    """
    try:
        rec = {
            "ts": _utcnow(),
            "session_id": session_id or "",
            "hook": hook,
            "decision": decision,
            "reason": reason,
        }
        if tool:
            rec["tool"] = tool
        if path:
            rec["path"] = path
        if command:
            rec["command"] = _head(command)
        if task:
            rec["task"] = _head(str(task), 16)      # task 235: a close's records name their task
        _write_record(agent_dir, rec)
    except Exception as exc:                # defence in depth: never propagate
        _warn(exc)


def append_review(agent_dir, *, session_id="", seat="", task="", round_no=0,
                  kind="", duration_ms=None, status="", usage=None,
                  error="") -> None:
    """Append one REVIEW-SPEND record and return None. Never raises.

    Same HARD CONTRACT as `append`: a write failure must NEVER change or break a
    review or any decision, one O_APPEND write of a single line kept under
    PIPE_BUF, and the write is skipped unless the resolved lane dir already
    exists. This records what a judge invocation COST — nothing it decides.

    The record shares the enforcement envelope (`hook="review"`,
    `decision="record"`, `reason="review spend"`) so a journal reader sees it as
    one more `record` line, plus review-specific fields:
      * `kind`        — "panel" | "single" | "tail-cert" | "intent" (task 120)
      * `seat`        — the judge spec, e.g. "claude:opus" / "codex:gpt-5.6:medium"
      * `task`        — task number ("042") or "-" for a taskless/--prompt review
      * `round`       — the review iteration this spend belongs to (int; 0 = unknown)
      * `duration_ms` — wall time of the judge subprocess, milliseconds (omitted if unknown)
      * `status`      — "ok" | "fail" | "timeout" | "dnf" (did-not-finish/spawn error)
      * `error`       — task 096: WHY a non-`ok` invocation failed (`fail`/`dnf`/
                        `timeout` only; never on `ok`). Sanitized (control chars
                        dropped, `"`→`'`, `\\`→`/`, token-shaped strings
                        redacted) and cut to the byte budget the rest of the
                        record leaves under the 512-byte atomic floor; omitted
                        when that budget is under 8 bytes.
      * `usage`       — token usage WHERE the CLI reports it, else the explicit
                        marker `{"status":"unknown"}`. Numbers are NEVER fabricated:
                        codex/grok/agy judge seats carry the CLI's own per-call counts
                        (structured output, task 056), claude's since task 232
                        (`-p --output-format json`, with `cache_read` and
                        `cache_write` beside `in` and `out`); every timeout without a
                        complete frame, spawn error or unrecognized output stays
                        `unknown`.

    Every field is bounded so REAL records stay well under the 512-byte
    atomic-write floor: strings are byte-capped with control characters stripped,
    numerics are magnitude-capped, `session_id` is capped, and `usage` is
    normalized to the fixed `{status[,in,out[,cache_read,cache_write]]}` schema (arbitrary caller dicts are
    NOT copied verbatim). This is the same HONEST bound `append` documents: a
    pathological caller could still exceed PIPE_BUF (e.g. a field stuffed with
    `"`/`\\`, which JSON escapes 2×) — an accepted best-effort bound, never a
    correctness risk, because the write never affects a decision (impl-panel
    rounds 2-4, task 042).
    """
    try:
        rec = {
            "ts": _utcnow(),
            "session_id": _head(session_id or "", 80),
            "hook": "review",
            "decision": "record",
            "reason": "review spend",
            "kind": _head(kind, 24),
            "seat": _head(seat, 80),
            "task": _head(str(task), 16),
            "round": _cap_int(int(round_no)) if _is_int(round_no) else 0,
        }
        if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
            rec["duration_ms"] = _cap_int(int(duration_ms))
        if status:
            rec["status"] = _head(status, 16)
        rec["usage"] = _normalize_usage(usage)
        if error and status and status != "ok":
            _err = _fit_error(rec, error)
            if _err:
                rec["error"] = _err
        _write_record(agent_dir, rec)
    except Exception as exc:                # defence in depth: never propagate
        _warn(exc)


def append_verify(agent_dir, *, task="", command="", rc=0, how="", seconds=0.0, session_id="") -> None:
    """Append one VERIFY-RUN record and return None. Never raises (task 232, PHASE-B item 6).

    One per command the verify contract ran or reused — at the close and in `tasks verify` alike
    (`tasks.verify_memory.run_contract`): `task` ("-" when none), the command's first line (capped at 120 bytes), its exit `rc`,
    `how` ("ran" or "reused") and `seconds` — the monotonic time the run took, 0 for a reused one (that time was spent
    once, by the run it reuses). Same HARD CONTRACT and envelope as `append` (`hook="verify"`, `decision="record"`,
    `reason="verify run"`); every field bounded."""
    try:
        rec = {
            "ts": _utcnow(),
            "session_id": _head(session_id or "", 80),
            "hook": "verify",
            "decision": "record",
            "reason": "verify run",
            "task": _head(str(task or "-"), 16),
            "command": _head(command, 120),      # 120: JSON may double each `"`/`\` and the line must stay under 512
            # signed: a command killed by a signal exits negative, and `_cap_int` would turn that into a green 0
            "rc": max(-_INT_CAP, min(int(rc), _INT_CAP)) if _is_int(rc) else -1,
            "how": "reused" if how == "reused" else "ran",
            "seconds": (round(min(float(seconds), 10 ** 9), 3)
                        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0
                        else 0),
        }
        _write_record(agent_dir, rec)
    except Exception as exc:                # defence in depth: never propagate
        _warn(exc)


LIFECYCLE_EVENTS = ("work", "new", "done")


def append_lifecycle(agent_dir, *, event, task, detail="", session_id="") -> None:
    """Append one TASK-TRANSITION record and return None. Never raises (task 221).

    The CLI writes one when a transition HAPPENED: `tasks work N` activated task N
    (`work`), `tasks new` made it (`new`, `detail` = "<type> <name>"), `tasks work
    done` closed it (`done`). The readers that build task windows — retro,
    timeline, tagger, tag, intent — read these instead of parsing the shell log:
    a command line is not an event (a skipped `&&` branch, a heredoc or a grep
    naming `tasks work 5` ran nothing), and the shell log is written by a hook,
    which a host may not run. Same HARD CONTRACT as `append`; same envelope
    (`hook="tasks"`, `decision="record"`, `reason` = the event) plus `task` and
    an optional `detail`, each byte-capped."""
    try:
        if event not in LIFECYCLE_EVENTS:
            return
        rec = {
            "ts": _utcnow(),
            "session_id": _head(session_id or "", 80),
            "hook": "tasks",
            "decision": "record",
            "reason": event,
            "task": _head(str(task), 16),
        }
        if detail:
            rec["detail"] = _head(detail, 120)
        _write_record(agent_dir, rec)
    except Exception as exc:                # defence in depth: never propagate
        _warn(exc)


AGENT_USAGE_KEYS = ("in", "out", "cache_read", "cache_write", "thinking")


def _agent_usage_rec(*, task="", file="", ino=0, span_from=0, span_to=0, head="", last_id="", model="", side=False,
                     calls=0, counts=None, unreadable=0, known=True, why="", session_id="") -> dict:
    """One AGENT-USAGE record, every field bounded (see `append_agent_usage`)."""
    rec: dict = {
        "ts": _utcnow(),
        "session_id": _head(session_id or "", 40),
        "hook": "agent-usage",
        "decision": "record",
        "reason": "agent usage",
        "task": _head(str(task or "-"), 16),
        "file": _head(str(file or ""), 16),
        # as text: an inode number can pass the integer cap (Lustre, 64-bit NFS ids — impl round 1, opus)
        "ino": _head(str(int(ino)) if _is_int(ino) else "", 24),
        "from": _cap_int(int(span_from)) if _is_int(span_from) else 0,
        "to": _cap_int(int(span_to)) if _is_int(span_to) else 0,
        "head": _head(str(head or ""), 8),
        "last_id": _head(str(last_id or ""), 48),
    }
    if not known:
        rec["status"] = "unknown"
        rec["why"] = _head(why or "?", 80)
    else:
        rec["model"] = _head(model or "?", 40)
        rec["side"] = 1 if side else 0
        rec["calls"] = _cap_int(int(calls)) if _is_int(calls) else 0
        c = counts if isinstance(counts, dict) else {}
        for k in AGENT_USAGE_KEYS:
            v = c.get(k)
            rec[k] = _cap_int(v) if isinstance(v, int) and not isinstance(v, bool) else 0
        if _is_int(unreadable) and int(unreadable) > 0:
            rec["unreadable"] = _cap_int(int(unreadable))
    return rec


def append_agent_usage(agent_dir, **fields) -> bool:
    """Append one AGENT-USAGE record; return whether it landed. Never raises (task 244, PHASE-B item 6).

    The main agent's tokens over one span of one host transcript file, for one model (`scripts/agent_usage.py` reads
    the span and writes this under its lane lock; the Stop recorder and the CLI's transitions call it). The owner,
    2026-10-18: "Da, după 243 (Recomandat)" — the closure's numbers carry the agent's own tokens beside the judge seats'.
    Same HARD CONTRACT and envelope as `append` (`hook="agent-usage"`, `decision="record"`, `reason="agent usage"`),
    plus `task` (the task the span belongs to, "-" for none) and the span itself — `file` (16 hex of the path's sha1),
    `ino` (as text), `from`, `to` (byte offsets), `head` (8 hex of the sha1 of the file's first min(4096, `to`) bytes:
    the same file, not one put in its place under a reused inode) and `last_id` (the last call counted): the record IS
    the read position, the next read of that file starts at its newest record's `to`. Then either the counts —
    `model`, `side` (1 for a subagent's file or sidechain entries), `calls`, `in`, `out`, `cache_read`, `cache_write`,
    `thinking` (a part of `out`, never added to it), `unreadable` when entries could not be read — or
    `status: "unknown"` with `why` (a span not counted: never zero counts for it). Every field bounded, the line under
    the 512-byte floor; no conversation text is ever passed here, only the model's name. Returns whether the record
    landed (task 246: the close's busy-lock line says so)."""
    try:
        return bool(_write_record(agent_dir, _agent_usage_rec(**fields)))
    except Exception as exc:                # defence in depth: never propagate
        _warn(exc)
        return False


def append_agent_usage_span(agent_dir, items) -> bool:
    """Append the records of ONE span (one per model and side) in one write; return whether every line landed (task
    246's impl round 2, codex / sonnet: the recorder must not say a span was recorded that the journal refused). Never
    raises.

    Each record carries the span's `to`, which is the next read's start, so the records must land together: one
    `os.write` of their lines (impl round 1 of task 244, codex / opus / sonnet: appended one by one, a kill between them
    lost the later models with no trace). A span holds a few models at most, a few hundred bytes each — far under
    PIPE_BUF; past 4,096 bytes the lines go in more than one write (said, never cut)."""
    try:
        recs = [_agent_usage_rec(**f) for f in items]
        if not recs:
            return True
        lines = [(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8") for r in recs]
        chunk: "list[bytes]" = []
        landed = True
        for ln in lines:
            if chunk and sum(map(len, chunk)) + len(ln) > 4096:
                landed = bool(_write_record(agent_dir, recs[0], data=b"".join(chunk))) and landed
                chunk = []
            chunk.append(ln)
        return bool(_write_record(agent_dir, recs[0], data=b"".join(chunk))) and landed
    except Exception as exc:                # defence in depth: never propagate
        _warn(exc)
        return False


# --- The hook journal (task 222, PHASE-B item 2) ---------------------------------------------------------------------
# The owner, 2026-10-16 10:26: "Istoric curat + jurnal de hook-uri". One line per run of a playbook hook, in
# `<lane>/journal/hooks.jsonl` — the same fields, caps, line bound, lane and sink rules as the bash writer
# (`gate-echo-lib.sh::hook_journal_append`; tests/test_hook_journal_python.py holds the two, and monitor-nudge.sh's
# inline copy, to the same answers). Python writes it for the hooks that are python: codex's three and
# command_guard.py. Past 20 MB the file is closed — renamed, compressed, kept (the owner, 2026-10-16 18:04: "Comprimate,
# toate") — so a project whose only hooks are python rotates too (plan panel 2026-10-16: codex, grok, opus).
# Task 234 (PHASE-B item 6): five keys after `error` — the session's task (read by the writer itself), the open gate's
# `##` section (the state echo's line only) and the suspend sample: `susp` (CLOCK_BOOTTIME − CLOCK_MONOTONIC, whole
# seconds the machine had slept since boot), `sts` (the wall time of that sample) and `boot` (the boot id's first 8).
HOOK_FIELDS = ("ts", "hook", "event", "tool", "tool_use_id", "session_id", "host_session_id", "agent_id", "decision",
               "detail", "ms", "exit", "error", "task", "section", "susp", "sts", "boot")
_HJ_LIMIT = 20971520
_HJ_LINE_MAX = 2048                     # bytes, the newline included (one append; Linux's PIPE_BUF is 4096)
_HJ_ID_DROP = re.compile(r"[^A-Za-z0-9._:@/+=-]")
_HJ_SESSION = re.compile(r"[A-Za-z0-9._-]+")
_HJ_TASK = re.compile(rb"[0-9]{1,16}")
_HJ_BOOT = re.compile(r"[0-9a-f]{8}")
_HJ_COUNT_MAX = 999_999_999_999         # the bash writer's `^[0-9]{1,12}$`
_HJ_SAMPLE_SLACK_NS = 5_000_000         # a bracketed clock reading is kept only when it spans at most 5 ms
_HJ_ARCHIVE = re.compile(r"hooks\.jsonl\.archived-\d{8}-\d{6}-(\d{1,12})-\d+")


def _hj_id(value, cap: int) -> str:
    """Only the characters an id or a name is made of, at most `cap` bytes (nothing left to escape)."""
    return _HJ_ID_DROP.sub("", str(value or "")[: cap * 2])[:cap]


def _hj_text(value, cap: int) -> str:
    """Control characters as spaces, cut so that the JSON-escaped text is at most `cap` bytes, on a whole character
    (the bash writer's rule: `\\` and `"` take two bytes once escaped)."""
    out, used = [], 0
    for ch in str(value or "")[: cap * 2]:
        if ch < " " or ch == "\x7f":
            ch = " "
        n = 2 if ch in '"\\' else len(ch.encode("utf-8", "surrogatepass"))
        if used + n > cap:
            break
        out.append(ch)
        used += n
    return "".join(out).encode("utf-8", "replace").decode("utf-8")


def suspend_sample():
    """(susp, sts, boot): the seconds the machine had slept since boot (CLOCK_BOOTTIME − CLOCK_MONOTONIC, whole), the
    wall time of that reading (epoch seconds) and the boot id's first 8 characters — None where they cannot be read.
    The time-by-phase adder pairs `susp` with `sts`, never with a line's `ts` (plan panel of task 234: codex, grok,
    sonnet). ONE bracketed reading, in nanoseconds — wall, MONOTONIC, BOOTTIME, MONOTONIC, wall — kept only when both
    brackets span at most 5 ms: a delay inside it would read as sleep, a suspend or a clock step inside it
    would pair the total with the wrong moment (impl r1: codex measured 25573 → 25575 → 25573 with a 2 s delay). Up to
    three tries, then None. `susp` is BOOTTIME minus the bracket's middle."""
    susp = sts = None
    try:
        t = time
        for _ in range(3):
            w1 = t.time_ns()
            m1 = t.clock_gettime_ns(t.CLOCK_MONOTONIC)
            b = t.clock_gettime_ns(t.CLOCK_BOOTTIME)
            m2 = t.clock_gettime_ns(t.CLOCK_MONOTONIC)
            w2 = t.time_ns()
            dm, dw = m2 - m1, w2 - w1
            if 0 <= dm <= _HJ_SAMPLE_SLACK_NS and 0 <= dw <= _HJ_SAMPLE_SLACK_NS:     # (so they agree within 5 ms too)
                susp, sts = max(0, (b - (m1 + m2) // 2) // 1_000_000_000), w2 // 1_000_000_000
                break
    except (AttributeError, OSError, ValueError, OverflowError, TypeError):
        susp = sts = None
    try:
        with open("/proc/sys/kernel/random/boot_id", "rb") as f:
            b8 = f.read(8).decode("ascii", "replace")
        boot = b8 if _HJ_BOOT.fullmatch(b8) else None
    except OSError:
        boot = None
    return susp, sts, boot


def _hj_count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= _HJ_COUNT_MAX else None


def _hj_session_task(lane: Path, session_id) -> "str | None":
    """The session's task, as the bash writer reads it: the first line of `<lane>/sessions/<id>/current_state`, its
    spaces dropped, when that is a task number (digits, at most 16); "" where the session is known and has none; None
    where it is not known — no id or one resolve_session_id would refuse, a link on the way, a pointer that is not a
    regular file (opened without blocking: a FIFO would hang the hook)."""
    try:
        sid = str(session_id or "")
        if not _HJ_SESSION.fullmatch(sid) or sid in (".", ".."):
            return None
        d = lane / "sessions"
        if d.is_symlink():
            return None
        if not d.is_dir():
            return ""
        d = d / sid
        if d.is_symlink():
            return None
        if not d.is_dir():
            return ""
        try:
            fd = os.open(str(d / "current_state"),
                         os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            return ""
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            data = os.read(fd, 4096)
        finally:
            os.close(fd)
        t = re.sub(rb"[ \t\n\r\f\v]", b"", data.split(b"\n", 1)[0])
        return t.decode("ascii") if _HJ_TASK.fullmatch(t) else ""
    except (OSError, ValueError):
        return None


def resolve_hook_lane(project_root) -> "Path | None":
    """The lane a hook-journal line may go to, or None — STRICTER than `resolve_lane_dir`, as the bash writer is: a
    directory that is not a playbook project (no `.agent/tasks`, no lane with `tasks/` — codex's hooks fall back to
    their working directory: impl panel r1, opus) is None, an invalid marker is None (not the root lane), the
    fresh-clone shape is None, and so is a lane that is missing, a symbolic link or not writable."""
    try:
        if not project_root:
            return None
        agent = Path(project_root) / ".agent"
        if not (agent / "tasks").is_dir() and not (agent.is_dir() and any(
                (c / "tasks").is_dir() for c in agent.iterdir())):
            return None
        marker = agent / "current_user"
        lane = resolve_lane_dir(project_root)
        if lane is None:
            return None
        if marker.exists() or marker.is_symlink():
            if lane == agent:               # resolve_lane_dir's fallback on a marker it could not validate
                return None
        if lane.is_symlink() or not lane.is_dir() or not os.access(lane, os.W_OK):
            return None
        return lane
    except Exception:
        return None


def append_hook_run(project_root, *, hook, event="", tool="", tool_use_id="", session_id="", host_session_id="",
                    agent_id="", decision="", detail="", ms=None, exit_code=0, error="", timing=None, task=None,
                    section=None, susp=None, sts=None, boot=None) -> None:
    """Append one hook-journal line for a run of `hook` in the project at `project_root`. Never raises; a write that
    cannot be made is skipped. `ms` is the run's duration (None when unknown); `timing` = (start stamp, fallback
    time) instead makes the duration be taken AFTER the journal's own rotation (impl panel r1, codex). No line for a
    review's session (`PLAYBOOK_SESSION_ID` judge / tail-cert: a review must not write the project — opus).
    Task 234 — null = not on this line, "" = read and none: `task` None → read from the session's pointer (as the bash
    writer does), else written once digits ("" kept, anything else null); `section` None → null; `susp`, `sts` and
    `boot` all None → this process's own sample; given values are written once valid (digits / 8 hex), else null."""
    try:
        if os.environ.get("PLAYBOOK_SESSION_ID", "") in ("judge", "tail-cert"):
            return
        lane = resolve_hook_lane(project_root)
        if lane is None:
            return
        jdir = lane / "journal"
        if jdir.is_symlink():
            return
        try:
            jdir.mkdir(exist_ok=True)           # the directory first: a first append holds the lock too (post-panel run 4)
        except OSError:
            return
        _hj_rotate(jdir)
        fd, got = _hj_share(jdir)               # before the end time: a wait for the lock counts (post-panel run 3)
        if fd is None:
            return                              # never an unlocked append (the owner, 2026-10-16 22:21)
        try:
            if timing is not None:
                ms = hook_ms(*timing)
            if not (isinstance(ms, int) and not isinstance(ms, bool) and ms >= 0):
                ms = None
            code = exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else 255
            rec = {"ts": _utcnow(), "hook": _hj_id(hook, 32), "event": _hj_id(event, 32), "tool": _hj_id(tool, 64),
                   "tool_use_id": _hj_id(tool_use_id, 64), "session_id": _hj_id(session_id, 64),
                   "host_session_id": _hj_id(host_session_id, 64), "agent_id": _hj_id(agent_id, 64),
                   "decision": _hj_id(decision, 16), "detail": _hj_text(detail, 128),
                   "ms": _cap_int(ms) if ms is not None else None, "exit": max(0, min(code, 255)),
                   "error": _hj_text(error, 240)}
            if task is None:
                task = _hj_session_task(lane, session_id)
            if susp is None and sts is None and boot is None:
                susp, sts, boot = suspend_sample()
            rec["task"] = (task if isinstance(task, str) and (task == "" or _HJ_TASK.fullmatch(task.encode("ascii",
                           "replace"))) else None)
            rec["section"] = _hj_text(section, 128) if isinstance(section, str) else None
            rec["susp"], rec["sts"] = _hj_count(susp), _hj_count(sts)
            rec["boot"] = boot if isinstance(boot, str) and _HJ_BOOT.fullmatch(boot) else None
            data = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(data) > _HJ_LINE_MAX:
                rec["detail"] = rec["error"] = ""
                rec["section"] = None                   # a dropped section: unknown, not "none"
                data = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                if len(data) > _HJ_LINE_MAX:
                    return
            if not got:
                _hj_spool(jdir, data)                   # never appended without the lock (the owner, 00:13)
            else:
                _write_record(lane, rec, name="hooks.jsonl", data=data)
        finally:
            os.close(fd)
    except OSError:
        return                              # a sink that cannot be written (a FIFO: ENXIO) is skipped quietly, as in bash
    except Exception as exc:                # defence in depth: never propagate
        _warn(exc)


def _hj_share(jdir: Path):
    """(fd, got): a SHARED lock on the journal directory, waited for up to 1 s (the owner, 2026-10-16 22:21: "Lacăt la
    fiecare rând" — renaming and compressing hold it exclusively, so no line is in flight while a closed file is
    compressed, whatever the clock). fd is None when the directory cannot be opened: then nothing is written."""
    import fcntl
    try:
        fd = os.open(str(jdir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return None, False
    deadline = time.monotonic() + 1.0
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return fd, True
        except OSError:
            if time.monotonic() >= deadline:
                return fd, False
            time.sleep(0.01)


def _hj_spool(jdir: Path, data: bytes) -> None:
    """A line that could not have the shared lock goes to a spool file of its own (the owner, 2026-10-17 00:13:
    "Bilet în cutie"), written whole under a name no taker matches, then linked in without clobbering; `_hj_rotate`
    moves spools into the journal under the exclusive lock."""
    _spool_line(jdir, data, "hooks.jsonl")


def _spool_line(jdir: Path, data: bytes, journal: str, tag: bool = False) -> bool:
    """`data` as a spool of `journal` (`<journal>.spool-…`, written as `<journal>.spooling-…`); whether it landed.
    `tag` (the enforcement journal, plan r1 P2): each line carries `"spool":"<the spool's name>"`, its identity through
    the merge — a merge killed before its unlink is not appended twice, and readers keep one line per id."""
    if jdir.is_symlink():
        return False
    name = f"{journal}.spool-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{int.from_bytes(os.urandom(4), 'big')}"
    tmp = jdir / (f"{journal}.spooling-" + name[len(f"{journal}.spool-"):])
    if tag:     # each line its own id, `<name>.<index>` (impl r1 Q1: one id for a multi-line spool kept one line)
        data = b"".join(ln[:-1] + b',"spool":"' + f"{name}.{i}".encode() + b'"}\n' if ln.endswith(b"}") else ln + b"\n"
                        for i, ln in enumerate(x for x in data.split(b"\n") if x))
    made = landed = False
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        made = True
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.link(tmp, jdir / name)
        landed = True
    except OSError:
        pass
    finally:
        if made:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return landed


def hook_ms(start: str = "", fallback: float = 0.0):
    """The run's duration in whole ms from a start stamp in bash's `EPOCHREALTIME` form (`<secs>.<micro>`, a comma
    under some locales) — the wrapper's exported `_PB_HJ_T0` — else from `fallback` (a `time.time()`), else None."""
    now = time.time()
    m = re.fullmatch(r"(\d{1,12})[.,](\d{6})", (start or "").strip())
    t0 = int(m.group(1)) + int(m.group(2)) / 1e6 if m else fallback
    if not t0:
        return None
    ms = int((now - t0) * 1000)
    return ms if ms >= 0 else None


def _cwd_project_root():
    """The project a hook runs in: PLAYBOOK_PROJECT_ROOT (codex's wrapper sets it), else the first directory up from the
    working directory with `.agent/tasks` or `.agent/<lane>/tasks`; None outside one."""
    env = os.environ.get("PLAYBOOK_PROJECT_ROOT", "")
    if env:
        return env
    d = os.getcwd()
    while True:
        agent = os.path.join(d, ".agent")
        if os.path.isdir(os.path.join(agent, "tasks")):
            return d
        if os.path.isdir(agent):
            try:
                if any(os.path.isdir(os.path.join(agent, s, "tasks")) for s in os.listdir(agent)):
                    return d
            except OSError:
                pass
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


class HookRun:
    """One run of a python hook, for the hook journal (task 222). The hook notes the payload it read, its project root,
    playbook's session id where it resolved one, and what it decided (`note`); `finish(rc)` writes the line. The
    decision defaults by exit status as the bash runner's does: 2 block; 0 allow on PreToolUse, ok elsewhere; else
    error. Nothing here raises."""

    def __init__(self, hook: str):
        self.hook = hook
        self.t0 = time.time()
        self.start = os.environ.get("_PB_HJ_T0", "")     # command-guard-hook's start, exported before its `exec`
        self.payload = {}
        self.root = None
        self.session_id = ""
        self.decision = self.detail = self.error = ""
        self.done = False

    def note(self, decision: str = "", detail: str = "", error: str = "") -> None:
        if decision:
            self.decision = decision
        if detail:
            self.detail = detail
        if error:
            self.error = error

    def finish(self, rc, error: str = "") -> None:
        try:
            if self.done:
                return
            self.done = True
            p = self.payload if isinstance(self.payload, dict) else {}

            def s(*keys):
                for k in keys:
                    v = p.get(k)
                    if isinstance(v, str):
                        return v
                return ""

            code = rc if isinstance(rc, int) and not isinstance(rc, bool) else 1
            event = s("hook_event_name", "hookEventName")
            decision = self.decision or ("block" if code == 2 else
                                         ("allow" if event == "PreToolUse" else "ok") if code == 0 else "error")
            append_hook_run(self.root or _cwd_project_root(), hook=self.hook, event=event,
                            tool=s("tool_name", "toolName"), tool_use_id=s("tool_use_id", "toolUseId"),
                            session_id=self.session_id, host_session_id=s("session_id", "sessionId"),
                            agent_id=s("agent_id", "agentId"), decision=decision, detail=self.detail,
                            exit_code=code, error=self.error or error, timing=(self.start, self.t0))
        except Exception as exc:
            _warn(exc)


def run_hook(run: "HookRun", main) -> int:
    """Run a python hook's `main()` and write its journal line; its exit status and its exceptions are unchanged (an
    exception is recorded as the error, then raised again)."""
    try:
        rc = main()
    except SystemExit as e:
        run.finish(e.code if isinstance(e.code, int) else (0 if e.code is None else 1))
        raise
    except BaseException as e:
        run.finish(1, type(e).__name__)          # the type only: a message can carry a path (impl panel r1, sonnet)
        raise
    run.finish(rc)
    return rc


class NoRun:
    """What a hook notes into when pb_journal could not be loaded: nothing happens."""
    payload = root = None
    session_id = ""

    def note(self, *a, **k):
        return None

    def finish(self, *a, **k):
        return None


def _hj_rotate(jdir: Path) -> None:
    """About one run in 32 (PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK, 1-1000) looks at the journal directory. Under a lock on
    it taken without waiting (busy: nothing this run): past the limit, after a second look, the file is renamed to an
    archive name that does not exist yet; a closed file is compressed (stdlib gzip) only once idle — its mtime over 10
    minutes old — since a hook that opened it before the rename can still append (impl panel r1: codex, grok, opus;
    the bash writer's rule). Every archive is kept. Never raises."""
    try:
        every = os.environ.get("PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK", "32")
        every = int(every) if every.isdigit() and len(every) <= 4 and 1 <= int(every) <= 1000 else 32
        if int.from_bytes(os.urandom(2), "big") % every != 0:     # not `random`: its import costs the guard ~0.5 ms
            return
        jfile = jdir / "hooks.jsonl"
        try:
            st = os.lstat(jfile)
            big = stat.S_ISREG(st.st_mode) and st.st_size > _HJ_LIMIT
        except OSError:
            big = False

        try:
            spools = [jdir / n for n in sorted(os.listdir(jdir)) if n.startswith("hooks.jsonl.spool")]
        except OSError:
            spools = []

        def closed():
            try:
                names = os.listdir(jdir)
            except OSError:
                return []
            return [jdir / n for n in sorted(names) if n.startswith("hooks.jsonl.archived-")
                    and not n.endswith(".gz") and ".gz.tmp-" not in n]
        idle = bool(closed())
        if not (big or idle or spools):
            return
        import fcntl
        fd = os.open(str(jdir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return
            _hj_take_spools(jdir, jfile)           # first, into the live file: this hold is exclusive
            if big:
                st = os.lstat(jfile)
                if stat.S_ISREG(st.st_mode) and st.st_size > _HJ_LIMIT:
                    # the name records the rename time, written by the rename itself (post-panel judge run 1, grave)
                    arch = jdir / f"hooks.jsonl.archived-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time())}-{os.getpid()}"
                    if not os.path.lexists(arch):
                        os.link(jfile, arch)            # no clobber, as `mv -n`
                        os.unlink(jfile)
                        os.utime(arch, None)            # idle counts from the rename too (impl panel r2, grave)
            now = time.time()
            for arch in closed():
                m = _HJ_ARCHIVE.fullmatch(arch.name)     # 10 minutes after the rename its name records; none: never
                if not m or now - int(m.group(1)) <= 600:
                    continue
                try:
                    st = os.lstat(arch)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and now - st.st_mtime > 600:
                    _hj_compress(arch)
        finally:
            os.close(fd)
    except Exception:
        return


def _hj_take_spools(jdir: Path, jfile: Path) -> None:
    """Under the exclusive lock: every spool, in name order, appended whole to the journal and removed (a link removed
    unread, one past 4 KiB not ours); a writer's temporary whose writer is gone is taken the same way."""
    _take_spools(jdir, jfile, "hooks.jsonl")


def _take_spools(jdir: Path, jfile: Path, journal: str, seen_ids: "set | None" = None, cap: int = 4096) -> None:
    """`_hj_take_spools` for the spools of `journal` (the hook journal's, and since task 257 the enforcement one's).
    `seen_ids` (the enforcement journal, plan r1 P2): the `spool` ids already in the journal — a spool whose lines all
    carry one of them was appended by a merge killed before its unlink, and is only unlinked now."""
    try:
        names = sorted(os.listdir(jdir))
    except OSError:
        return
    for n in names:
        f = jdir / n
        try:
            st = os.lstat(f)
        except OSError:
            continue
        if n.startswith(f"{journal}.spooling-"):
            # never removed for its age (post-panel judge run 4: a clock step made a live writer's look old); one whose
            # writer, the pid in its name, is gone is taken as a spool: its one write is the whole line or nothing
            m = re.search(r"-(\d{1,10})-\d+$", n)
            if not m or os.path.isdir(f"/proc/{m.group(1)}") or not stat.S_ISREG(st.st_mode) or st.st_size > cap:
                continue
            if st.st_size == 0:
                f.unlink()
                continue
            data = f.read_bytes()
            if not data.endswith(b"\n"):
                continue
        elif n.startswith(f"{journal}.spool-"):
            if stat.S_ISLNK(st.st_mode):
                f.unlink()
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
                continue
            data = f.read_bytes()
        else:
            continue
        ids = set(_EJ_SPOOL_ID.findall(data)) if seen_ids is not None else set()
        if ids and ids <= seen_ids:
            f.unlink()
            continue
        out = os.open(str(jfile), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NONBLOCK", 0)
                      | getattr(os, "O_NOFOLLOW", 0), 0o644)
        try:
            st = os.fstat(out)
            if not stat.S_ISREG(st.st_mode):
                return
            try:
                whole = os.write(out, data) == len(data)
            except OSError:
                whole = False
            if not whole:
                # a short write (a full disk, a file-size limit): rolled back — this hold is exclusive, no other line
                # is behind it — and the spool kept for a later run (post-panel judge run 5, codex — reproduced)
                os.ftruncate(out, st.st_size)
                return
        finally:
            os.close(out)
        if seen_ids is not None:
            seen_ids |= ids
        f.unlink()


def _hj_compress(arch: Path) -> None:
    import gzip
    gz = Path(str(arch) + ".gz")
    tmp = Path(f"{arch}.gz.tmp-{os.getpid()}")
    if os.path.lexists(gz) or os.path.lexists(tmp):
        return
    made = False
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        made = True
        with os.fdopen(fd, "wb") as raw, open(arch, "rb") as src, gzip.GzipFile(fileobj=raw, mode="wb",
                                                                               compresslevel=6) as out:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        os.link(tmp, gz)
        os.unlink(tmp)
        made = False
        os.unlink(arch)
    except Exception:
        if made:
            try:
                if not tmp.is_symlink():
                    tmp.unlink()
            except OSError:
                pass


# --- The enforcement journal's rotation (task 257) -------------------------------------------------------------------
# The owner, 2026-10-19: "Da, în Faza B (Recomandat)" — `enforcement.jsonl` rotates like `hooks.jsonl`: past 20 MB the
# live file is renamed to an archive, compressed once idle, every archive kept ("Comprimate, toate"), so a project used
# for years keeps the close's read and the agent recorder's backward scan bounded. The recorder's read positions live
# in this journal (`agent-usage` records, scripts/agent_usage.py), so the new live file starts with a horizon line and
# each transcript's newest position (a `carry` line): its next read is the same as before the rotation. Positions older
# than the horizon (30 days) are not carried — a transcript the recorder finds no position for, begun before it, gets a
# seed instead of a read from 0 (agent_usage.record_spans). The rotation runs only in python, under the recorder's
# `agent-usage.lock` (no new position can appear meanwhile) and, for its rename and its compression's swap, the
# enforcement lock held exclusively — every append holds it shared (`_write_record`, `journal_enforcement`).
_EJ_LIMIT = 20971520
_EJ_HORIZON_S = 30 * 86400
_EJ_IDLE_S = 600
_EJ_ARCHIVE = re.compile(r"enforcement\.jsonl\.archived-\d{8}-\d{6}-(\d{1,12})-(\d{1,12})")   # epoch, sequence
_EJ_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_EJ_TASK = re.compile(rb'"task":\s*"(\d{1,16})"')
_EJ_SPOOL_ID = re.compile(rb'"spool":"(enforcement\.jsonl\.spool-[0-9A-Za-z._-]{1,80})"')
_EJ_CARRY_KEYS = ("task", "file", "ino", "to", "head", "last_id")
# the rotation's own two lines are `record`s told by their reason (the journal's decisions stay the ratified four —
# PB-ENFORCEMENT-JOURNAL): read positions, never events
EJ_CARRY_REASON = "read position carried"
EJ_HORIZON_REASON = "read positions horizon"


def rotate_enforcement(lane, held: bool = False, limit: "int | None" = None) -> str:
    """Rotate `<lane>/journal/enforcement.jsonl` when it is past `limit` (20 MB), merge its spools, repair a rotation
    that was killed, compress the archives that are idle. `held`: the caller holds the lane's `agent-usage.lock` (the
    recorder); else it is taken for at most 0.5 s and the pass skipped when busy — its holder rotates. Says what it did
    ("" for nothing). Never raises."""
    try:
        limit = _EJ_LIMIT if limit is None else int(limit)
        jdir = Path(lane) / "journal"
        if jdir.is_symlink() or not jdir.is_dir():
            return ""
        live = jdir / EJ_NAME
        try:
            st = os.lstat(live)
            big = stat.S_ISREG(st.st_mode) and st.st_size > limit
        except OSError:
            big = False
        names = os.listdir(jdir)
        pending = any(n.startswith((EJ_NAME + ".spool-", EJ_NAME + ".spooling-", EJ_NAME + ".carry-"))
                      or ".gz.tmp-" in n and n.startswith(EJ_NAME) or _EJ_ARCHIVE.fullmatch(n) for n in names)
        if not (big or pending):
            return ""
        au = None
        if not held:
            au = _au_lock(jdir, 0.5)
            if au is None:
                return "skipped: the recorder's lock is busy"
        try:
            if not _ej_probe(jdir):
                return "skipped: the journal is busy"     # a reader holds it: not even the pre-scan (plan r1, P9)
            pre = _ej_prescan(live) if big else None
            said = _ej_rotate_locked(jdir, live, limit, pre)
            if said.startswith("skipped"):
                return said                 # no repair ran: an archive twin of the live file may still be there (R1)
            done = _ej_compress_idle(jdir)
            return "; ".join(x for x in (said, done) if x)
        finally:
            if au is not None:
                os.close(au)
    except Exception:                       # noqa: BLE001 — a journal's upkeep never fails its caller
        return ""


def _au_lock(jdir: Path, wait: float):
    """The lane's `journal/agent-usage.lock` (the recorder's, `agent_usage._lock`) held exclusively, or None."""
    import fcntl
    try:
        fd = os.open(str(jdir / "agent-usage.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0), 0o600)
    except OSError:
        return None
    if _flock_wait(fd, fcntl.LOCK_EX, wait):
        return fd
    os.close(fd)
    return None


def _ej_probe(jdir: Path) -> bool:
    """Whether the enforcement lock can be had exclusively right now (taken and let go at once)."""
    import fcntl
    lk = _ej_lock_fd(jdir)
    if lk is None:
        return False
    try:
        return _flock_wait(lk, fcntl.LOCK_EX, 0)
    finally:
        os.close(lk)


def _flock_wait(fd: int, mode: int, wait: float) -> bool:
    import fcntl
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)


class _Prescan:
    def __init__(self, ino: int, end: int, found: dict, horizon: str, tasks: set):
        self.ino, self.end, self.found, self.horizon, self.tasks = ino, end, found, horizon, tasks


def _ej_prescan(live: Path) -> "_Prescan | None":
    """Each transcript's newest position in the live file up to its last complete line, and its newest horizon — read
    before the exclusive hold (0.3-0.4 s for 20 MB, measured), under the recorder's lock."""
    try:
        fd = os.open(str(live), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        found: dict = {}
        tasks: set = set()
        end, horizon = _ej_positions(fd, 0, st.st_size, found, "", tasks)
        return _Prescan(st.st_ino, end, found, horizon, tasks)
    finally:
        os.close(fd)


def _ej_positions(fd: int, start: int, size: int, found: dict, horizon: str, tasks: set) -> "tuple[int, str]":
    """Read [start, size) of the open journal: each `agent-usage` line with a `file` key replaces that key's entry in
    `found` (what `agent_usage._newest` would answer: the newest such line); a horizon line raises `horizon`; every
    `"task":"<digits>"` goes into `tasks` (the archive's sidecar, plan r1 P7). Returns (the end of the last complete
    line read, the horizon)."""
    os.lseek(fd, start, os.SEEK_SET)
    pos, carry = start, b""
    while pos < size:
        chunk = os.read(fd, min(1 << 20, size - pos))
        if not chunk:
            break
        pos += len(chunk)
        *whole, carry = (carry + chunk).split(b"\n")
        for raw in whole:
            rec = None
            if b'"agent-usage"' in raw:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    rec = None
            ours = isinstance(rec, dict) and rec.get("hook") == "agent-usage" \
                and rec.get("reason") in (EJ_CARRY_REASON, EJ_HORIZON_REASON)
            m = _EJ_TASK.search(raw)
            if m and not ours:
                # a carried line names an old task, no record of it here (Q13) — told by its parsed hook and reason,
                # never by its wording, which a command can print (impl r2 R2)
                tasks.add(m.group(1).decode())
            if not isinstance(rec, dict) or rec.get("hook") != "agent-usage":
                continue
            if rec.get("reason") == EJ_HORIZON_REASON:
                h = rec.get("horizon")
                if isinstance(h, str) and _EJ_STAMP.fullmatch(h) and h > horizon:
                    horizon = h
                continue
            k = rec.get("file")
            if isinstance(k, str) and k:
                found[k] = rec
    return pos - len(carry), horizon


def _ej_rotate_locked(jdir: Path, live: Path, limit: int, pre: "_Prescan | None") -> str:
    """Under the enforcement lock held exclusively (≤ 1 s, else skipped): repair, merge the spools, and with a
    pre-scan the swap — the tail since the pre-scan scanned, the carry file written, the live file linked to its
    archive name and the carry file renamed over it."""
    import fcntl
    lk = _ej_lock_fd(jdir)
    if lk is None:
        return ""
    try:
        if not _flock_wait(lk, fcntl.LOCK_EX, 0.5):
            return "skipped: the journal is busy"
        _ej_repair(jdir, live)
        _ej_take_spools(jdir, live)
        if pre is None:
            return ""
        try:
            st = os.lstat(live)
        except OSError:
            return ""
        if not stat.S_ISREG(st.st_mode) or st.st_ino != pre.ino or st.st_size <= limit:
            return ""                       # rotated meanwhile, or no longer past the limit
        fd = os.open(str(live), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            _, horizon = _ej_positions(fd, pre.end, st.st_size, pre.found, pre.horizon, pre.tasks)
        finally:
            os.close(fd)
        now = time.time()
        horizon = max(horizon, _utc(now - _EJ_HORIZON_S))
        lines = [_ej_line({"ts": _utc(now), "session_id": "", "hook": "agent-usage", "decision": "record",
                           "reason": EJ_HORIZON_REASON, "horizon": horizon})]
        for rec in pre.found.values():
            of = rec.get("of") if rec.get("reason") == EJ_CARRY_REASON else rec.get("ts")
            if isinstance(of, str) and _EJ_STAMP.fullmatch(of) and of < horizon:
                continue                    # older than the horizon: the recorder seeds such a transcript
            c = {"ts": _utc(now), "session_id": rec.get("session_id", ""), "hook": "agent-usage",
                 "decision": "record", "reason": EJ_CARRY_REASON}
            for k in _EJ_CARRY_KEYS:
                if k in rec:
                    c[k] = rec[k]
            c["from"] = rec.get("to")
            c["of"] = of
            line = _ej_line(c)
            if len(line) <= 4096:
                lines.append(line)
        tmp = jdir / f"{EJ_NAME}.carry-{os.getpid()}-{int.from_bytes(os.urandom(4), 'big')}"
        # the last field: a sequence, one past the highest — readers order archives by it, not by the clock (impl r1
        # Q6: a clock set back named a later archive earlier); the time stays for the idle rule and for people
        seq = 1 + max((int(m.group(2)) for m in (_EJ_ARCHIVE.fullmatch(n[:-3] if n.endswith(".gz") else n)
                                                  for n in os.listdir(jdir)) if m), default=0)
        arch = jdir / f"{EJ_NAME}.archived-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now))}-{int(now)}-{seq}"
        if os.path.lexists(arch):
            return ""
        out = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        try:
            data = b"".join(lines)
            if os.write(out, data) != len(data):
                raise OSError("short write")
            os.fsync(out)                   # durable before it replaces the live file (impl r1 Q4)
        except OSError:
            os.close(out)
            tmp.unlink()
            return ""
        os.close(out)
        # the archive's index first (plan r1, P7): the task numbers its lines name — killed before the link, an orphan
        # `_ej_repair` removes
        side = Path(str(arch) + ".tasks")
        try:
            sfd = os.open(str(side), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            with os.fdopen(sfd, "w", encoding="utf-8") as f:
                # its size too: an older writer holding its descriptor across the rename can still append (Q9)
                f.write(json.dumps({"tasks": sorted(pre.tasks), "size": os.lstat(live).st_size},
                                   separators=(",", ":")) + "\n")
        except OSError:
            tmp.unlink()
            return ""
        os.link(live, arch)                 # no clobber; killed after it: `_ej_repair` unlinks this name again
        os.rename(tmp, live)                # the live name always names a whole file
        os.utime(arch, None)                # idle counts from the rename (the hook journal's impl r2, grave)
        return f"rotated to {arch.name} ({len(lines) - 1} position(s) carried)"
    finally:
        os.close(lk)


def _ej_line(rec: dict) -> bytes:
    return (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _utc(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _ej_repair(jdir: Path, live: Path) -> None:
    """Under the exclusive hold: an archive name on the live file's inode (a swap killed between its link and its
    rename) is unlinked, and every carry file is a dead rotator's (a living one writes it only inside this hold); then
    an index whose archive (plain or `.gz`) is gone — a swap killed before its link, or the name just unlinked."""
    try:
        live_ino = os.lstat(live).st_ino
    except OSError:
        live_ino = None
    for n in sorted(os.listdir(jdir)):
        p = jdir / n
        try:
            if n.startswith(EJ_NAME + ".carry-"):
                if not p.is_symlink() and p.is_file():
                    p.unlink()
            elif live_ino is not None and _EJ_ARCHIVE.fullmatch(n) and os.lstat(p).st_ino == live_ino:
                p.unlink()
        except OSError:
            continue
    names = set(os.listdir(jdir))
    for n in names:
        if n.endswith(".tasks") and _EJ_ARCHIVE.fullmatch(n[:-6]) and n[:-6] not in names \
                and n[:-6] + ".gz" not in names:
            try:
                (jdir / n).unlink()
            except OSError:
                pass


def _ej_take_spools(jdir: Path, live: Path) -> None:
    """Under the exclusive hold: the spools merged once each (plan r1, P2) — the ids already in the live file read
    first, only when there is a spool to take."""
    try:
        names = [n for n in os.listdir(jdir) if n.startswith((EJ_NAME + ".spool-", EJ_NAME + ".spooling-"))]
    except OSError:
        return
    if not names:
        return
    if not any(_ej_takeable(jdir / n) for n in names):
        _take_spools(jdir, live, EJ_NAME, seen_ids=set(), cap=16384)    # links removed; the live file not read (R6)
        return
    seen: set = set()
    try:
        fd = os.open(str(live), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        fd = None
    if fd is not None:
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                tail = b""
                while True:
                    chunk = os.read(fd, 1 << 20)
                    if not chunk:
                        break
                    buf = tail + chunk
                    seen.update(_EJ_SPOOL_ID.findall(buf))
                    tail = buf[-160:]       # an id cut by the chunk's end is found with the next one
        finally:
            os.close(fd)
    _take_spools(jdir, live, EJ_NAME, seen_ids=seen, cap=16384)     # a tagged 4 KiB span passes 4 KiB (impl r1 Q2)


def _ej_takeable(p: Path) -> bool:
    """Whether `_take_spools` would take this spool: a regular file of at most 16 KiB — a writer's temporary only once
    its writer (the pid in its name) is gone and its line is whole."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_size > 16384:
        return False
    if ".spooling-" in p.name:
        m = re.search(r"-(\d{1,10})-\d+$", p.name)
        if not m or os.path.isdir(f"/proc/{m.group(1)}") or st.st_size == 0:
            return False
        try:
            fd = os.open(str(p), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return False
        try:
            return os.pread(fd, 1, st.st_size - 1) == b"\n"
        finally:
            os.close(fd)
    return True


def _ej_compress_idle(jdir: Path) -> str:
    """Each closed archive idle for 10 minutes — by the rename time its name records and by its mtime — compressed
    OUTSIDE the lock (the appends never wait on it), then swapped in under the exclusive hold only when the archive is
    unchanged since; a reader holds the lock shared while it lists and reads, so it sees the archive or its `.gz`,
    never neither. A temporary whose compressor is gone is removed. Every archive is kept."""
    import fcntl
    done = 0
    now = time.time()
    try:
        live_ino = os.lstat(jdir / EJ_NAME).st_ino
    except OSError:
        live_ino = None
    for n in sorted(os.listdir(jdir)):
        p = jdir / n
        if n.startswith(EJ_NAME) and ".gz.tmp-" in n:
            m = re.search(r"\.gz\.tmp-(\d{1,10})$", n)
            if m and not os.path.isdir(f"/proc/{m.group(1)}") and not p.is_symlink():
                try:
                    p.unlink()
                except OSError:
                    pass
            continue
        m = _EJ_ARCHIVE.fullmatch(n)
        if not m or now - int(m.group(1)) <= _EJ_IDLE_S:
            continue
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode) or now - st.st_mtime <= _EJ_IDLE_S or st.st_ino == live_ino:
            continue                        # the live file's twin (a rotation killed half-way): never (impl r2 R1)
        gz = jdir / (n + ".gz")
        lk = _ej_lock_fd(jdir)
        if lk is None:
            return ""
        try:
            if os.path.lexists(gz):
                # a swap killed after its link: the .gz is whole (linked only once written); the archive goes when it
                # was not written after it
                if _flock_wait(lk, fcntl.LOCK_EX, 0.2) and not gz.is_symlink() \
                        and os.lstat(p).st_mtime <= os.lstat(gz).st_mtime and _gz_isize(gz) == st.st_size % (1 << 32):
                    p.unlink()
                continue
            tmp = jdir / f"{n}.gz.tmp-{os.getpid()}"
            if not _gzip_to(p, tmp):
                os.utime(p, None)           # failed (a full disk): tried again after the idle time (impl r2 R7)
                continue
            if not _flock_wait(lk, fcntl.LOCK_EX, 0.2):
                tmp.unlink()
                continue
            now_st = os.lstat(p)
            if (now_st.st_size, now_st.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                tmp.unlink()                # written since: compressed again later
                continue
            if _gz_isize(tmp) != st.st_size % (1 << 32):
                tmp.unlink()                # not all of it (Q4): after the idle time again (R7)
                os.utime(p, None)
                continue
            os.link(tmp, gz)
            p.unlink()
            tmp.unlink()
            done += 1
            break                           # one archive a pass: the recorder's lock is held (impl r1 Q14)
        except OSError:
            continue
        finally:
            os.close(lk)
    return f"compressed {done} archive(s)" if done else ""


def _gz_isize(gz: Path) -> "int | None":
    """The `.gz`'s ISIZE (its last 4 bytes: the uncompressed size mod 2^32), or None — opened without blocking or
    following a link, a regular file only (impl r1 Q5: a FIFO there hung the pass)."""
    try:
        fd = os.open(str(gz), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size < 4:
            return None
        return int.from_bytes(os.pread(fd, 4, st.st_size - 4), "little")
    except OSError:
        return None
    finally:
        os.close(fd)


def _gzip_to(src: Path, tmp: Path) -> bool:
    import gzip
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except OSError:
        return False
    try:
        try:
            sfd = os.open(str(src), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            os.close(fd)                    # not leaked (impl r2 R5)
            raise
        with os.fdopen(fd, "wb") as raw, os.fdopen(sfd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise OSError("not a regular file")
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6) as out:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
            raw.flush()
            os.fsync(raw.fileno())          # durable before the archive it replaces goes (impl r1 Q4)
        return True
    except Exception:                       # noqa: BLE001
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# The atomic-append floor every journal line must stay under (PIPE_BUF's POSIX
# minimum), newline included.
_LINE_FLOOR = 512
# A known key prefix, or a 32+ run of letters AND digits with no `_` or `/`
# (hex / base64-shaped): paths and long snake_case test ids stay readable
# (impl-panel r1, sonnet).
_TOKENISH = re.compile(r"\b(?:sk|xai|ghp|gho|ghs|glpat)[-_][A-Za-z0-9_\-]{8,}"
                       r"|(?=[A-Za-z0-9+\-]*[0-9])(?=[A-Za-z0-9+\-]*[A-Za-z])[A-Za-z0-9+\-]{32,}")


def _fit_error(rec: dict, error) -> str:
    """Task 096: the reason a judge invocation failed, fitted to what the rest of
    `rec` leaves under the 512-byte floor. Control characters are dropped and
    `"`/`\\` replaced, so the encoded length equals the byte length; the budget
    counts the newline as the one byte the journal writes, so it is exact.
    Token-shaped runs are redacted
    (the reason comes from a CLI's stderr). Returns "" when fewer than 8 bytes
    remain."""
    s = str(error).strip().split("\n", 1)[0]
    s = "".join(ch for ch in s if ch >= " " and ch != "\x7f")
    s = s.replace('"', "'").replace("\\", "/")
    s = _TOKENISH.sub("<redacted>", s)
    base = len((json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    budget = _LINE_FLOOR - base - len(',"error":""'.encode("utf-8"))
    if budget < 8:
        return ""
    return s.encode("utf-8")[:budget].decode("utf-8", "ignore")


# Numeric magnitude cap (impl-panel round 2): the string fields are byte-capped,
# but a pathological caller could pass an arbitrarily large `round`/`duration_ms`/
# token int whose DECIMAL LENGTH blows the PIPE_BUF atomic-write bound. Clamp
# every numeric to this ceiling so "every field is bounded" is literally true.
# 10**15 - 1 (15 digits) sits far above every real value — round is < 10^4, a
# multi-hour review is ~10^7 ms, token counts are < ~10^8 — so the clamp is only
# ever reached by a pathological/synthetic call; even then the line stays small.
_INT_CAP = 10 ** 15 - 1


def _cap_int(v: int) -> int:
    if v < 0:
        return 0
    return v if v <= _INT_CAP else _INT_CAP


def _normalize_usage(usage) -> dict:
    """Coerce any caller `usage` into the fixed, bounded shape the record shape
    doc pins — never a verbatim copy of an arbitrary dict (which could blow the
    PIPE_BUF bound). Returns `{"status":"known","in":<int>,"out":<int>}` only
    when both token counts are real ints; otherwise `{"status":"unknown"}`.
    Numbers are never fabricated — a non-int in/out degrades to unknown. Task 232:
    claude's cache counts (`cache_read`, `cache_write`) ride beside them when each is
    a non-negative int; anything else in them is dropped, never copied."""
    if isinstance(usage, dict) and usage.get("status") == "known":
        _in, _out = usage.get("in"), usage.get("out")
        if isinstance(_in, int) and not isinstance(_in, bool) \
                and isinstance(_out, int) and not isinstance(_out, bool):
            # Magnitude-capped like every other numeric so a pathological token
            # count cannot blow the PIPE_BUF line bound (impl-panel round 2).
            got = {"status": "known", "in": _cap_int(_in), "out": _cap_int(_out)}
            for k in ("cache_read", "cache_write"):
                v = usage.get(k)
                if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                    got[k] = _cap_int(v)
            return got
    return {"status": "unknown"}


def _is_int(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    try:
        return str(v).strip().lstrip("-").isdigit()
    except Exception:
        return False


def _write_record(agent_dir, rec, name="enforcement.jsonl", data=None) -> bool:
    """Write ONE record dict as a single O_APPEND line into the lane's journal.

    Shared by `append`, `append_review`, `append_lifecycle` and (`name="hooks.jsonl"`, `data` its encoded line)
    `append_hook_run`. Skips silently unless the resolved
    lane dir already exists (playbook-managed project); the `journal/` subdir is
    created inside that existing lane, never `.agent` itself. One `os.write` of
    one line: POSIX makes an O_APPEND write of at most PIPE_BUF bytes atomic
    against concurrent appenders, so lines from separate processes (multi-lane,
    parallel panel seats) do not interleave. The caller is responsible for
    keeping the record under that bound (the field caps above do).

    Hostile-tree safe (impl-panel round 3): the journal file is opened
    O_NONBLOCK|O_NOFOLLOW and its fd fstat'd to confirm a regular file before any
    write. Without this, a rogue judge that swapped `enforcement.jsonl` for a
    FIFO would turn the next review's O_WRONLY open into an indefinite HANG
    (worse than the "a write never affects the review" contract allows), and a
    symlinked `journal/` could write outside the lane. A non-regular / symlinked
    sink is silently skipped instead."""
    if not agent_dir:
        return False
    agent_dir = Path(agent_dir)
    if not agent_dir.is_dir():              # playbook-managed lane only
        return False
    jdir = agent_dir / "journal"
    # A symlinked `journal` DIRECTORY would let the file open below resolve
    # OUTSIDE the lane — O_NOFOLLOW on the leaf file does NOT protect an
    # intermediate symlinked component (impl-panel round 4, unanimous). lstat the
    # journal component and skip if it is a symlink. (Residual, honestly bounded:
    # a TOCTOU swap of the dir between this lstat and the open below is a tiny
    # window on a best-effort log with no reader waiting — far under this
    # feature's threat model; a dirfd/openat anchor would close it fully.)
    jfile = jdir / name
    # Skip a symlinked `journal` DIRECTORY or a symlinked leaf FILE — either would
    # let the open below resolve OUTSIDE the lane. lstat both components: this is
    # the first guard (O_NOFOLLOW below may be absent — getattr → 0 — and then
    # these islink checks do the work). Residual, honestly
    # bounded: a TOCTOU swap between an lstat and the open is a tiny window on a
    # best-effort log with no reader waiting — far under this feature's threat
    # model; a dirfd/openat anchor would close it fully (impl-panel round 4).
    try:
        if jdir.is_symlink():
            return False
    except OSError:
        return False
    try:
        jdir.mkdir(exist_ok=True)           # inside an existing lane; not `.agent`
    except OSError:
        pass                                # e.g. path is a file — the open below fails, swallowed
    try:
        if jfile.is_symlink():
            return False
    except OSError:
        return False
    if data is None:
        data = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if name != EJ_NAME:
        return _append_line(jfile, data)
    # task 257: every append to the enforcement journal holds its lock shared — the rotation renames and compresses
    # under the exclusive one (the owner's "Lacăt la fiecare rând", as the hook journal); past the wait, a spool
    # ("Bilet în cutie"), never an unlocked append. A lock that cannot be had as a regular file: nothing written.
    # The CLI's records wait longer (plan r1, P3): a spool is placed back only by its second, so a transition, a verify
    # run, a review's spend or a close is not spooled for a hold of a few seconds (holds last milliseconds).
    lock, got = _ej_share(jdir, 5.0 if isinstance(rec, dict) and rec.get("hook") in _CLI_HOOKS else 1.0)
    if lock is None:
        return False
    try:
        return _append_line(jfile, data) if got else _spool_line(jdir, data, EJ_NAME, tag=True)
    finally:
        os.close(lock)


EJ_NAME = "enforcement.jsonl"
EJ_LOCK = "enforcement.lock"
_CLI_HOOKS = ("tasks", "verify", "review", "close", "adopt")


def _ej_lock_fd(jdir: Path):
    """The enforcement journal's lock file, opened (made inside the journal directory when missing), or None: a link,
    a FIFO or anything else that is not a regular file is no lock."""
    try:
        fd = os.open(str(jdir / EJ_LOCK), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0), 0o644)
    except OSError:
        return None
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


def _ej_share(jdir: Path, wait: float = 1.0):
    """(fd, got): the enforcement lock held SHARED, waited for up to `wait` s; fd None when there is no lock file to
    hold (then nothing is written)."""
    import fcntl
    fd = _ej_lock_fd(jdir)
    if fd is None:
        return None, False
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return fd, True
        except OSError:
            if time.monotonic() >= deadline:
                return fd, False
            time.sleep(0.01)


def _append_line(jfile: Path, data: bytes) -> bool:
    """One O_APPEND write of `data` to the journal file `jfile`; whether the whole line landed."""
    # O_NONBLOCK: a FIFO with no reader fails ENXIO here rather than blocking.
    # O_NOFOLLOW: a symlinked final component fails ELOOP (no lane escape).
    # Read with getattr(…, 0): a platform without them gets no-ops (the islink
    # check above and the fstat regular-file check below still apply). Using
    # os.O_NONBLOCK directly once raised AttributeError where it was missing,
    # which the caller swallowed and silently dropped EVERY journal write.
    fd = os.open(str(jfile),
                 os.O_WRONLY | os.O_APPEND | os.O_CREAT
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
                 0o644)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):    # FIFO/device/dir/socket → not our sink
            return False
        # task 246: says whether the whole line landed (a caller that reports a gap must not claim one it
        # could not write); every skip above returns False
        return os.write(fd, data) == len(data)   # single append; atomic under PIPE_BUF
    finally:
        os.close(fd)


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())     # not `datetime`: ~0.7 ms of import on every guard run


def _head(command, limit: int = _HEAD_LIMIT) -> str:
    """Command head only — first line, capped to `limit` BYTES (UTF-8), never the
    full payload. Byte-capping (not character-capping) is what actually keeps the
    record under PIPE_BUF: 200 multi-byte characters (e.g. emoji) are up to 800
    bytes and would blow the 512-byte atomic-write bound (panel). A truncated
    trailing multi-byte sequence is dropped (errors='ignore'), never emitted
    half-encoded.

    Control characters are stripped FIRST (impl-panel round 3): a byte cap alone
    does not bound the SERIALIZED line, because json.dumps escapes each control
    char to a 6-byte `\\uXXXX`, so an all-NUL field within the byte cap could
    still expand the record past PIPE_BUF. These fields (seat/kind/task/status/
    session_id/command-head) never legitimately carry control chars, so dropping
    them keeps the encoded line bounded (the only remaining JSON expansion is `"`
    and `\\` → 2 bytes, which the small caps already absorb)."""
    s = str(command).strip().split("\n", 1)[0]
    s = "".join(ch for ch in s if ch >= " " and ch != "\x7f")
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _warn(exc: Exception) -> None:
    try:
        sys.stderr.write(f"[pb_journal] warn: {type(exc).__name__}: {exc}\n")
    except Exception:
        pass
