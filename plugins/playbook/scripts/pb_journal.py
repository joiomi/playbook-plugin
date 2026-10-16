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
           tool="", path="", command="") -> None:
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
                        codex/grok judge seats carry the CLI's own per-call counts
                        (structured output, task 056); the claude judge runs in
                        plain-text mode, so its seats — and every timeout, spawn
                        error or unrecognized output — stay `unknown`.

    Every field is bounded so REAL records stay well under the 512-byte
    atomic-write floor: strings are byte-capped with control characters stripped,
    numerics are magnitude-capped, `session_id` is capped, and `usage` is
    normalized to the fixed `{status[,in,out]}` schema (arbitrary caller dicts are
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


# --- The hook journal (task 222, PHASE-B item 2) ---------------------------------------------------------------------
# The owner, 2026-10-16 10:26: "Istoric curat + jurnal de hook-uri". One line per run of a playbook hook, in
# `<lane>/journal/hooks.jsonl` — the same fields, caps, line bound, lane and sink rules as the bash writer
# (`gate-echo-lib.sh::hook_journal_append`; tests/test_hook_journal_parity.py holds the two, and monitor-nudge.sh's
# inline copy, to the same answers). Python writes it for the hooks that are python: codex's three and
# command_guard.py. Past 20 MB the file is closed — renamed, compressed, kept (the owner, 2026-10-16 18:04: "Comprimate,
# toate") — so a project whose only hooks are python rotates too (plan panel 2026-10-16: codex, grok, opus).
HOOK_FIELDS = ("ts", "hook", "event", "tool", "tool_use_id", "session_id", "host_session_id", "agent_id", "decision",
               "detail", "ms", "exit", "error")
_HJ_LIMIT = 20971520
_HJ_LINE_MAX = 1024                     # bytes, the newline included (one append; Linux's PIPE_BUF is 4096)
_HJ_ID_DROP = re.compile(r"[^A-Za-z0-9._:@/+=-]")


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


def resolve_hook_lane(project_root) -> "Path | None":
    """The lane a hook-journal line may go to, or None — STRICTER than `resolve_lane_dir`, as the bash writer is: an
    invalid marker is None (not the root lane), the fresh-clone shape is None, and so is a lane that is missing, a
    symbolic link or not writable."""
    try:
        if not project_root:
            return None
        agent = Path(project_root) / ".agent"
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
                    agent_id="", decision="", detail="", ms=None, exit_code=0, error="") -> None:
    """Append one hook-journal line for a run of `hook` in the project at `project_root`. Never raises; a write that
    cannot be made is skipped. `ms` is the run's duration (None when unknown)."""
    try:
        lane = resolve_hook_lane(project_root)
        if lane is None:
            return
        if not (isinstance(ms, int) and not isinstance(ms, bool) and ms >= 0):
            ms = None
        code = exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else 255
        rec = {"ts": _utcnow(), "hook": _hj_id(hook, 32), "event": _hj_id(event, 32), "tool": _hj_id(tool, 64),
               "tool_use_id": _hj_id(tool_use_id, 64), "session_id": _hj_id(session_id, 64),
               "host_session_id": _hj_id(host_session_id, 64), "agent_id": _hj_id(agent_id, 64),
               "decision": _hj_id(decision, 16), "detail": _hj_text(detail, 128), "ms": _cap_int(ms) if ms is not None
               else None, "exit": max(0, min(code, 255)), "error": _hj_text(error, 240)}
        data = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(data) > _HJ_LINE_MAX:
            rec["detail"] = rec["error"] = ""
            data = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(data) > _HJ_LINE_MAX:
                return
        _hj_rotate(lane / "journal")
        _write_record(lane, rec, name="hooks.jsonl", data=data)
    except OSError:
        return                              # a sink that cannot be written (a FIFO: ENXIO) is skipped quietly, as in bash
    except Exception as exc:                # defence in depth: never propagate
        _warn(exc)


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
                            ms=hook_ms(self.start, self.t0), exit_code=code, error=self.error or error)
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
        run.finish(1, f"{type(e).__name__}: {e}")
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
    """About one run in 32 (PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK, 1-1000) looks at the size; past the limit, under a lock
    on the journal directory taken without waiting and after a second look, the file is renamed to an archive name that
    does not exist yet and compressed beside it (stdlib gzip); the uncompressed copy is removed only once the compressed
    one is in place. Every archive is kept. A busy lock: no rotation this run. Never raises."""
    try:
        every = os.environ.get("PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK", "32")
        every = int(every) if every.isdigit() and len(every) <= 4 and 1 <= int(every) <= 1000 else 32
        if int.from_bytes(os.urandom(2), "big") % every != 0:     # not `random`: its import costs the guard ~0.5 ms
            return
        jfile = jdir / "hooks.jsonl"
        st = os.lstat(jfile)
        if not stat.S_ISREG(st.st_mode) or st.st_size <= _HJ_LIMIT or jdir.is_symlink():
            return
        import fcntl
        fd = os.open(str(jdir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return
            st = os.lstat(jfile)
            if not stat.S_ISREG(st.st_mode) or st.st_size <= _HJ_LIMIT:
                return
            arch = jdir / f"hooks.jsonl.archived-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
            if os.path.lexists(arch):
                return
            os.link(jfile, arch)            # no clobber, as `mv -n`
            os.unlink(jfile)
            _hj_compress(arch)
        finally:
            os.close(fd)
    except Exception:
        return


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
    Numbers are never fabricated — a non-int in/out degrades to unknown."""
    if isinstance(usage, dict) and usage.get("status") == "known":
        _in, _out = usage.get("in"), usage.get("out")
        if isinstance(_in, int) and not isinstance(_in, bool) \
                and isinstance(_out, int) and not isinstance(_out, bool):
            # Magnitude-capped like every other numeric so a pathological token
            # count cannot blow the PIPE_BUF line bound (impl-panel round 2).
            return {"status": "known", "in": _cap_int(_in), "out": _cap_int(_out)}
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


def _write_record(agent_dir, rec, name="enforcement.jsonl", data=None) -> None:
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
        return
    agent_dir = Path(agent_dir)
    if not agent_dir.is_dir():              # playbook-managed lane only
        return
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
            return
    except OSError:
        return
    try:
        jdir.mkdir(exist_ok=True)           # inside an existing lane; not `.agent`
    except OSError:
        pass                                # e.g. path is a file — the open below fails, swallowed
    try:
        if jfile.is_symlink():
            return
    except OSError:
        return
    if data is None:
        data = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
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
            return
        os.write(fd, data)                  # single append; atomic under PIPE_BUF
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
