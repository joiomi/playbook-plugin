"""tasks/numbers.py — a task's own numbers, composed at its close from the plugin's records only.

PHASE-B item 6, last part (task 235; the owner, 2026-10-13 18:44: "În Faza B (Recomandat)"). Item 8 re-measures B1's
numbers; B1 rebuilt them from the host's session transcripts, which the host deletes after about 30 days. Tasks 232-234
made the plugin record the facts in its own files; this module composes them per closure of a task:

  * the closure's window — from the first `tasks work N` after the task's previous `done` to this close's receipt;
  * the time of the task, per host session, by kind (agent, judges, verify, owner-idle, owner-question, background,
    away, unknown) and by the `##` section the state echo named, without the time the laptop slept;
  * the review rounds and what each found, the seats' runs, seconds and tokens, the verify runs;
  * the owner's prompts, his options dialogs (questions, answers) and the wait for each;
  * how the task closed, each flag and its reason.

Every input is one of the plugin's records: `<lane>/journal/hooks.jsonl` (with its archives and spools),
`<lane>/journal/enforcement.jsonl`, `<task>/post-panel-reviews.jsonl`, `<lane>/chat_log.md`, the task's receipt.
Nothing here writes; `write_closure` (the close's caller) does. The reading layer below never follows a link, never
opens a FIFO, and stops at a deadline (`OutOfTime`).
"""
from __future__ import annotations

import bisect
import calendar
import gzip
import io
import json
import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path

SCHEMA = 1


class OutOfTime(Exception):
    """The composition ran past its time budget (the close must not wait on it)."""


# --- reading ----------------------------------------------------------------------------------------------------------

_ARCHIVE = re.compile(r"hooks\.jsonl\.archived-\d{8}-\d{6}-(\d{1,12})-\d+(\.gz)?")
_SPOOL = re.compile(r"hooks\.jsonl\.spool-[0-9A-Za-z._-]+")
_TS_HEAD = re.compile(r'\{"ts":\s*"([^"]{20})"')


def _epoch(ts) -> "int | None":
    """`YYYY-MM-DDTHH:MM:SSZ` → epoch seconds, without strptime (a journal holds tens of thousands of lines)."""
    if not isinstance(ts, str) or len(ts) < 20 or ts[4] != "-" or ts[10] != "T" or not ts.endswith("Z"):
        return None
    try:
        return calendar.timegm((int(ts[0:4]), int(ts[5:7]), int(ts[8:10]), int(ts[11:13]), int(ts[14:16]),
                                int(ts[17:19]), 0, 0, 0))
    except ValueError:
        return None


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _read_regular(path: Path) -> "bytes | None":
    """The bytes of a regular file, opened without following a link and without blocking (a FIFO would hang the
    close); None for anything else."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def _lines_of(path: Path) -> "list[str]":
    data = _read_regular(path)
    if data is None:
        return []
    if path.name.endswith(".gz"):
        try:
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
        except (OSError, EOFError):
            return []
    return data.decode("utf-8", "replace").split("\n")


def read_hook_lines(jdir: Path, start: float, deadline: float, end: "float | None" = None,
                    margin: float = 3600) -> "list[dict]":
    """The hook journal's lines from `start - margin` to `end + margin`: the archives renamed at or after `start -
    margin` (an archive renamed earlier holds only older lines; impl r1, agy), the live file, then the unmerged spools — sorted by
    `ts`, file order kept within a second, exact duplicates dropped (a spool taken over but not yet removed). Each line
    gets `_t` (epoch seconds). The margin keeps the suspend samples just outside the window."""
    lo = _iso(start - margin)
    hi = _iso(end + margin) if end is not None else None
    if Path(jdir).is_symlink():
        return []                           # the writers refuse a linked journal/ (task 222); so does the reader
    with _shared_lock(Path(jdir), deadline):
        return _read_hook_lines_locked(Path(jdir), lo, hi, start, margin, deadline)


def _shared_lock(jdir: Path, deadline: float):
    """A SHARED lock on the journal directory while it is listed and read: rotation renames and compresses under the
    exclusive one (task 222), so a rotation between the listing and the reads cannot drop lines (task 235's last
    round, W5, codex). Waited for until the deadline, then OutOfTime. No directory: nothing to hold."""
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def held():
        try:
            fd = os.open(str(jdir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            yield
            return
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise OutOfTime("the journal is being rotated")
                    time.sleep(0.02)
            yield
        finally:
            os.close(fd)
    return held()


def _read_hook_lines_locked(jdir: Path, lo: str, hi: "str | None", start: float, margin: float,
                            deadline: float) -> "list[dict]":
    files: "list[tuple[int, Path]]" = []
    try:
        names = sorted(os.listdir(jdir))
    except OSError:
        return []
    for name in names:
        m = _ARCHIVE.fullmatch(name)
        if m and int(m.group(1)) >= start - margin:
            files.append((int(m.group(1)), jdir / name))
    files.sort(key=lambda f: f[0])
    paths = [p for _, p in files] + [jdir / "hooks.jsonl"] + [jdir / n for n in names if _SPOOL.fullmatch(n)]
    seen: "set[str]" = set()
    out: "list[dict]" = []
    for path in paths:
        if time.monotonic() > deadline:
            raise OutOfTime(str(path))
        for n, raw in enumerate(_lines_of(path)):
            if n % 2000 == 1999 and time.monotonic() > deadline:
                raise OutOfTime(str(path))      # checked inside a file too (impl r1, codex)
            raw = raw.strip()
            head = _TS_HEAD.match(raw)
            if not head or raw in seen:
                continue
            ts = head.group(1)
            if ts < lo or (hi is not None and ts > hi):
                continue                      # outside the window, decided before any JSON is parsed
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            t = _epoch(rec.get("ts"))
            if t is None:
                continue
            seen.add(raw)
            rec["_t"] = t
            out.append(rec)
    out.sort(key=lambda r: r["_t"])         # stable: file order kept within a second
    return out


_TASK_HOOKS = ("review", "verify", "tasks", "close")
_AGENT_HOOK = "agent-usage"        # task 244: the main agent's tokens, read beside `_TASK_HOOKS`, never one of them


def read_enforcement(lane: Path, deadline: float) -> "list[dict]":
    """The enforcement journal's review / verify / tasks / close records, and the agent's `agent-usage` ones (task
    244), with `_t`. A linked `journal/` is not read (the writers refuse it; task 235's last round, grok)."""
    out = []
    if (Path(lane) / "journal").is_symlink():
        return out
    for n, raw in enumerate(_lines_of(lane / "journal" / "enforcement.jsonl")):
        if n % 2000 == 0 and time.monotonic() > deadline:
            raise OutOfTime("enforcement.jsonl")
        raw = raw.strip()
        if not raw.startswith("{") or not any(f'"hook":"{h}"' in raw or f'"hook": "{h}"' in raw
                                              for h in _TASK_HOOKS + (_AGENT_HOOK,)):
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("hook") in _TASK_HOOKS + (_AGENT_HOOK,):
            t = _epoch(rec.get("ts"))
            if t is not None:
                rec["_t"], rec["_n"] = t, n         # its line: the file's order, inside one second too (Y5)
                out.append(rec)
    return out


def task_records(records: "list[dict]", task: str, start: float, end: float) -> "dict[str, list[dict]]":
    """This task's review, verify, tasks and close records in [start, end] (a record with no `task` is no task's)."""
    got: "dict[str, list[dict]]" = {h: [] for h in _TASK_HOOKS}
    for rec in records:
        if rec["hook"] in got and str(rec.get("task", "")) == str(task) and start <= rec["_t"] <= end:
            got[rec["hook"]].append(rec)
    return got


# --- the closure's window ---------------------------------------------------------------------------------------------

def closure_window(transitions: "list[dict]", task: str, end: float) -> "tuple[float, float] | None":
    """(start, end) of the closure ending at `end`: from the first `work` record of `task` after the last `done` record
    that a later `work` follows (a reopen), else its first `work`; the `done` records after the last `work` are this
    closure's own (a real close writes its `done` in the same second as its receipt). None when it was never activated
    (a task made before the transition records). Records are taken in their file order, sorted stably by time."""
    recs = [r for r in transitions if r.get("hook", "tasks") == "tasks" and str(r.get("task")) == str(task)
            and r.get("reason") in ("work", "done") and (_epoch(r.get("ts")) or 0) <= end]
    recs.sort(key=lambda r: _epoch(r.get("ts")) or 0)
    works = [i for i, r in enumerate(recs) if r.get("reason") == "work"]
    if not works:
        return None
    prev_done = max((i for i, r in enumerate(recs) if r.get("reason") == "done" and i < works[-1]), default=-1)
    first = next(i for i in works if i > prev_done)
    return (_epoch(recs[first].get("ts")), end)


# --- the owner's dialogs ----------------------------------------------------------------------------------------------

_ENTRY_HEAD = re.compile(r"^\*\*\[(M\d+)\]\*\*")


_DIALOG_MARK = re.compile(r"^<!-- dialog:(\S+) -->$")


def dialog_index(log_text: str, deadline: "float | None" = None) -> "dict[str, dict]":
    """One pass over chat_log.md: tool_use_id → {id, questions, answers} of each DIALOG entry, keyed by its last line
    `<!-- dialog:<id> -->` (task 233's format: every body line starts with a fixed prefix, so a `Q<n>` / `A<n>:` at a
    line start is one). A later entry with the same marker wins (the writer's dedup keeps one). Built once per
    composition — a split of the whole log per dialog was the other quadratic path (task 235's last round, W1)."""
    out: "dict[str, dict]" = {}
    head_id, q, a = None, 0, 0
    for n, ln in enumerate(log_text.split("\n")):
        if deadline is not None and n % 5000 == 4999 and time.monotonic() > deadline:
            raise OutOfTime("chat_log.md")          # the log has no bound (Minor, sonnet)
        m = _ENTRY_HEAD.match(ln)
        if m:
            head_id, q, a = m.group(1), 0, 0
            continue
        if re.match(r"Q\d+[ :(]", ln):
            q += 1
        elif re.match(r"A\d+: ", ln):
            a += 1
        mk = _DIALOG_MARK.match(ln.strip())
        if mk and head_id is not None:
            out[mk.group(1)] = {"id": head_id, "questions": q, "answers": a}
    return out


_ID_SAFE = re.compile(r"[^A-Za-z0-9_.:-]")


def _marker_id(tool_use_id: str) -> str:
    """The id as `dialog_log.py` writes it into the marker (`_ID_SAFE`, 120 characters)."""
    return _ID_SAFE.sub("", str(tool_use_id or ""))[:120]


def dialog_counts(log_text: str, tool_use_id: str) -> "dict | None":
    """{id, questions, answers} of the DIALOG entry marked with `tool_use_id`, or None."""
    return dialog_index(log_text).get(_marker_id(tool_use_id)) if tool_use_id else None


# --- the receipt ------------------------------------------------------------------------------------------------------

_BLOCK = re.compile(r"^### (\S+) · risk (\S+)")
# each verdict as `core.format_verify_receipt` words its line, checked in this order
# The verdict sits at a fixed place in the writer's text (`core.format_verify_receipt`): a prefix of its own, or right
# after `<round fp> vs close <close fp> — `. It is read only there, never as a word of the free text after it, so a
# reason that names a verdict is not that verdict (task 236's impl round 2, Q1: opus, sonnet).
_VERDICT_PREFIX = (("no stamp recorded", "NO-STAMP"), ("EXCLUDE-COVERS-CODE —", "EXCLUDE-COVERS-CODE"),
                   ("n/a — the newest single-judge review ran with a", "SINGLE-TAMPER-DEGRADED"))
_VERDICT_AT = re.compile(r"(?:\S+ )?vs close (?:\(unavailable\)|\S*) — "     # a fingerprint can be empty (K2)
                         r"(TAMPER-GUARD-DEGRADED|UNREADABLE|FRESH|STALE, but TAIL-CERTIFIED|STALE)")


def _freshness_of(line: str) -> str:
    body = line.split(":**", 1)[1].strip() if ":**" in line else line.strip()
    for prefix, name in _VERDICT_PREFIX:
        if body.startswith(prefix):
            return name
    m = _VERDICT_AT.match(body)
    if m:
        return "TAIL-CERTIFIED" if m.group(1).endswith("TAIL-CERTIFIED") else m.group(1)
    return "other"


# The panel line's fields after its verdict, in the writer's order (`core.format_verify_receipt`): `, accepted: "…"`,
# then `; <bound>` (`owner-ok: "…"` or `bound to post-D6 PASS …`) and `; <stop rule>`. Each field may hold `;` and
# quotes of its own (the stop rule's clause always holds a `;`), so a field runs to the next of these separators or the
# line's end (task 236's impl round 1, Y1: opus, codex, grok). A reason that itself holds one of the separators — the
# literal `; stop rule (D6): ` — is the one text this split cannot tell apart.
_FIELD_SEP = re.compile(r'; (?=owner-ok: "|bound to post-D6 PASS |stop rule \(D6\): )')
_OK_SEP = re.compile(r'; (?=owner-ok: ")')


def _quoted(field: str, label: str) -> "str | None":
    """`<label>"…"` → the text between the label's opening quote and the field's last quote; a field cut before its
    closing quote (the journal's 200 bytes) keeps what is there."""
    if not field.startswith(label + '"'):
        return None
    body = field[len(label) + 1:]
    return body[:-1] if body.endswith('"') else body


def receipt_close(task_text: str) -> "dict | None":
    """How this closure closed, from the receipt's newest `###` block (the close inserts its block at the top; the
    newest STAMP wins wherever it sits): stamp, epoch, risk, freshness (FRESH / STALE / TAIL-CERTIFIED / NO-STAMP / …),
    the accepted and forced reasons, the bound (`owner_ok`, or `bound` for a post-panel PASS) and the stop rule's
    clause, the post-panel judge's line (runs, last verdict, every owner-ok), the commands' PASS / FAIL counts. A pure
    reader, so it reads fail-OPEN as `core._latest_receipt_line` does: a closed fence hides a quoted receipt (task 235's
    last round, W3), an unclosed fence above does not hide the real one (task 236's impl round 1, Y9: grok)."""
    from tasks.core import _atx_h2_text, _iter_nonfenced, _physical_lines
    lines = _physical_lines(task_text)
    blocks = []
    cur = None
    started = False
    for k, _s in _iter_nonfenced(lines, unclosed_is_live=True):
        h2 = _atx_h2_text(lines[k])
        if not started:
            started = h2 == "## Verification Receipt"
            continue
        if h2 is not None:
            break
        ln = lines[k]
        m = _BLOCK.match(ln)
        if m:
            cur = {"stamp": m.group(1), "risk": m.group(2), "lines": []}
            blocks.append(cur)
        elif cur is not None:
            cur["lines"].append(ln)
    best = None
    for b in blocks:
        b["epoch"] = _iso_epoch(b["stamp"])         # a `Z` stamp too: fromisoformat refuses it on 3.10 (impl r1, agy)
        if b["epoch"] is None:
            continue
        if best is None or b["epoch"] > best["epoch"]:
            best = b
    if best is None:
        return None
    out = {"stamp": best["stamp"], "epoch": best["epoch"], "risk": best["risk"], "freshness": None,
           "accepted_reason": None, "forced_reason": None, "owner_ok": None, "bound": None, "stop_rule": None,
           "post_panel": None, "commands": {"pass": 0, "fail": 0}}
    for ln in best["lines"]:
        s = ln.strip()
        if s.startswith("- **Forced close, reason:**"):
            out["forced_reason"] = s.split(":**", 1)[1].strip()
        elif s.startswith("- **Panel tree-state:**"):
            out["freshness"] = _freshness_of(s)
            head, *fields = _FIELD_SEP.split(s)
            i = head.find(', accepted: "')
            if i >= 0:
                out["accepted_reason"] = _quoted(head[i + 2:], "accepted: ")
            for f in fields:
                if f.startswith('owner-ok: "'):
                    out["owner_ok"] = _quoted(f, "owner-ok: ")
                elif f.startswith("bound to post-D6 PASS "):
                    out["bound"] = f
                elif f.startswith("stop rule (D6): "):
                    out["stop_rule"] = f
        elif s.startswith("- **Post-D6 single judge:**"):
            head, *oks = _OK_SEP.split(s)
            m = re.match(r"- \*\*Post-D6 single judge:\*\* (\d+) run\(s\) after impl panel \S+ — last verdict ([A-Za-z?-]+)", head)
            out["post_panel"] = {"runs": int(m.group(1)) if m else None, "verdict": m.group(2) if m else None,
                                 "owner_ok": [_quoted(o, "owner-ok: ") for o in oks]}
        elif re.match(r"- \[PASS\]", s):
            out["commands"]["pass"] += 1
        elif re.match(r"- \[FAIL", s):          # `[FAIL(exit 1)]`
            out["commands"]["fail"] += 1
    return out


# --- the partition of a session's time --------------------------------------------------------------------------------
# One host session's lines (every task's, sorted, with `_t`) give the gaps between consecutive lines. A gap is task N's
# only when both its ends are N's lines (a command guard's line is joined to its call by `tool_use_id`), so time spent
# on another task between two of N's lines is never N's (plan panel: opus, codex, agy). Each of N's gaps is split, in
# this order: the time the laptop slept (from the `susp` samples), away (over 2 h awake), then by where the session was:
# outside a turn (from a RELEASED stop or a session's start to the next prompt or its end) → owner-idle, or background
# when the prompt ending it is a background task's notification; inside a turn → owner-question while an options dialog
# is open, else agent. Tool and model time are one kind: PreToolUse fires only for Bash, the edit tools and
# AskUserQuestion, so a Read, an MCP call or an Agent call has no start (sonnet, codex). Then the carve-outs: the part of
# a gap where a Bash call is open, or of a background gap, that overlaps the task's review spans is judges, its recorded
# verify spans verify — a polled panel is Bash and background, so it lands in judges, while the agent's own work during
# a panel stays agent (the choice between opus and grok, plan panel).

AWAY_S = 7200               # B1's rule: a gap over 2 h is away, here judged on the awake time
SLEEP_MIN_S = 2             # a `susp` difference under this is the samples' rounding, not sleep
AHEAD_S = 120               # how long after the close a sample still brackets its last gap (Y6)
_SHELL_TOOLS = ("Bash", "Shell", "run_terminal_command", "exec_command")    # exec_command: codex's (Y8, agy)
_TASK_RE = re.compile(r"[0-9]{1,16}")


def _union(spans: "list[tuple[float, float]]") -> "list[tuple[float, float]]":
    """The spans merged, their ends snapped to whole seconds — the journal's lines are whole seconds, so every part of
    the partition stays a whole number and the parts add up exactly (a fractional span broke Q1 by one second)."""
    out: "list[list[float]]" = []
    for a, b in sorted((float(round(a)), float(round(b))) for a, b in spans if round(b) > round(a)):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _overlap(a: float, b: float, spans: "list[tuple[float, float]]") -> float:
    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)


def _sample(rec: dict):
    s, t, boot = rec.get("susp"), rec.get("sts"), rec.get("boot")
    if (isinstance(s, int) and not isinstance(s, bool) and isinstance(t, int) and not isinstance(t, bool)
            and isinstance(boot, str) and re.fullmatch(r"[0-9a-f]{8}", boot)):
        return s, t, boot
    return None


def _task_of_lines(lines: "list[dict]"):
    """task_of(line): the line's own task (digits), else its call's by `tool_use_id` — a command guard's allow line
    names no task (it resolves no session), the same call's task-gate line does."""
    by_tid: "dict[str, str]" = {}
    for r in lines:
        t = r.get("task")
        if isinstance(t, str) and _TASK_RE.fullmatch(t) and r.get("tool_use_id"):
            by_tid.setdefault(r["tool_use_id"], t)

    def task_of(r) -> "str | None":
        t = r.get("task")
        if isinstance(t, str) and _TASK_RE.fullmatch(t):
            return t
        return by_tid.get(r.get("tool_use_id") or "")
    return task_of


_TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure")


def _step(state: dict, r: dict) -> None:
    """Advance a session's turn state by one line: a prompt opens a turn and ends every call left open (an interrupted
    call, a cancelled dialog, a blocked command fire no PostToolUse — impl r1, opus, sonnet); a tool event proves a
    turn (the turn's prompt may be older than the read margin — impl r1, opus, sonnet); a released stop or a session's
    start or end closes it."""
    ev = r.get("event")
    if ev == "UserPromptSubmit":
        state["in_turn"] = True
        keep = state.get("keep")    # the close's own call runs on through a notification (task 236's r2, Q4: grok)
        state["pending"] = {k: v for k, v in state["pending"].items() if keep and k == keep}
    elif ev in ("SessionStart", "SessionEnd") or (ev == "Stop" and r.get("decision") == "allow"):
        state["in_turn"] = False
        state["pending"].clear()
    elif ev in _TOOL_EVENTS:
        state["in_turn"] = True
        closed = state.setdefault("closed", set())
        tid = r.get("tool_use_id")
        if ev == "PreToolUse" and tid:
            if tid not in closed:            # a spooled Pre sorted after its Post in the same second (task 235, opus)
                state["pending"][tid] = (r.get("tool") or "", r["_t"], r)
        elif ev != "PreToolUse":
            state["pending"].pop(tid, None)
            if tid:
                closed.add(tid)


def open_calls(lines: "list[dict]", end: float) -> "dict[str, tuple]":
    """The calls still open at `end` in one session: tool_use_id → (tool, its PreToolUse time, its PreToolUse line)."""
    state = {"in_turn": False, "pending": {}}
    for r in lines:
        if r["_t"] <= end:
            _step(state, r)
    return dict(state["pending"])


def closing_session(sessions: "dict[str, list[dict]]", task: str, end: float) -> "str | None":
    """The session that holds the close's own call: the latest call still open at `end` whose line is THIS task's, of
    any tool (a codex `exec_command` too) — never another task's newer call (task 235's last round, W9, grok)."""
    best, latest = None, None
    for key, sl in sessions.items():
        task_of = _task_of_lines(sl)
        for _tool, t, line in open_calls(sl, end).values():
            if task_of(line) == str(task) and (latest is None or t > latest):
                best, latest = key, t
    return best


def _minus(spans: "list[tuple[float, float]]", cut: "list[tuple[float, float]]") -> "list[tuple[float, float]]":
    """`spans` without the time `cut` covers (both unions)."""
    out = []
    for a, b in spans:
        pieces = [(a, b)]
        for x, y in cut:
            nxt = []
            for p, q in pieces:
                if y <= p or x >= q:
                    nxt.append((p, q))
                    continue
                if p < x:
                    nxt.append((p, x))
                if y < q:
                    nxt.append((y, q))
            pieces = nxt
        out.extend(pieces)
    return out


def session_partition(lines: "list[dict]", task: str, end: float, reviews: "list[tuple[float, float]]",
                      verifies: "list[tuple[float, float]]", start: "float | None" = None,
                      close_open: bool = True, deadline: "float | None" = None, live: "tuple | None" = None,
                      known_close: bool = False) -> dict:
    """Task `task`'s share of one host session's time up to `end` (see the block comment above). Returns integer
    seconds: wall (the session's span in the window), total (the task's), not_task, slept, away, unknown,
    sleep_unmeasured (the task's time no two samples of one boot bracket), sleep_unplaced (sleep the samples recorded
    that no gap could hold — counted, never dropped), by_kind and by_section (both without zeros). `close_open`: this
    session holds the close's own call, which ends at `end` (only that session — impl r1, opus). `known_close`: the
    close's `done` record names this session — its span runs to `end`, and its own call (this task's last shell call
    with no Post) stays open through any prompt. `live`: the close's own sample `(susp, sts, boot)`, taken as it
    composes. Holds Q1: sum(by_kind) + slept + away + unknown == total;
    total + not_task == wall."""
    task = str(task)
    # samples just after the close bracket its last gap (W10, agy): the close's own (`live`) at the close, its
    # PostToolUse's on a later recompose — both within seconds. One TAKEN later (its `sts`, the moment that counts —
    # a spooled line is written later than it was taken) is not used: sleep after the close would be placed in its
    # last gap (task 236's impl round 1, Y6: opus)
    ahead = [r for r in lines if r["_t"] > end]
    if live is not None:
        ahead.append({"susp": live[0], "sts": live[1], "boot": live[2]})
    lines = [r for r in lines if r["_t"] <= end]
    empty = {"wall": 0, "total": 0, "not_task": 0, "slept": 0, "away": 0, "unknown": 0, "sleep_unmeasured": 0,
             "sleep_unplaced": 0, "by_kind": {}, "by_section": {}}
    if not lines:
        return empty
    task_of = _task_of_lines(lines)
    keep = None
    if known_close:
        finished = {r.get("tool_use_id") for r in lines if r.get("event") != "PreToolUse" and r.get("tool_use_id")}
        keep = next((r["tool_use_id"] for r in reversed(lines) if r.get("event") == "PreToolUse"
                     and r.get("tool") in _SHELL_TOOLS and r.get("tool_use_id")
                     and r["tool_use_id"] not in finished and task_of(r) == task), None)
    if close_open and (known_close or open_calls(lines, end)) and lines[-1]["_t"] < end:
        lines = lines + [{"_t": end, "event": "End", "task": task, "_virtual": True}]      # the closure's own (W9, agy)

    # sleep, per gap: between two samples of one boot, the `susp` difference goes to the gaps overlapping the samples'
    # own [sts1, sts2] — a sample can be written later than it was taken, so the line's time is not its moment (impl
    # r1, codex, grok) — largest overlap first, each gap capped at its length; what no gap can hold is
    # `sleep_unplaced`; across a boot change the overlapped gaps are unknown
    n = len(lines) - 1
    slept = [0.0] * n
    alloc: "list[list[tuple[float, float, float]]]" = [[] for _ in range(n)]     # (sleep, sts1, sts2) per gap
    unknown = [False] * n
    bracketed = [False] * n
    unplaced = 0.0
    samples = sorted(((smp[1], smp[0], smp[2], i) for i, r in enumerate(lines + ahead)
                      if (smp := _sample(r)) is not None and smp[1] <= end + AHEAD_S), key=lambda x: (x[0], x[3]))
    w_lo = start if start is not None else lines[0]["_t"]
    # the gaps a [t1, t2] can overlap are found by bisect on the sorted gap ends and starts — linear overall, where a
    # scan of every gap per pair was quadratic (13.7 s on 6,000 lines — task 235's last round, W1)
    starts = [lines[k]["_t"] for k in range(n)]
    ends = [lines[k + 1]["_t"] for k in range(n)]
    for m, ((t1, s1, b1, _), (t2, s2, b2, _)) in enumerate(zip(samples, samples[1:])):
        if deadline is not None and m % 1000 == 999 and time.monotonic() > deadline:
            raise OutOfTime("sleep placement")
        if t2 <= t1:
            continue
        lo, hi = bisect.bisect_right(ends, t1), bisect.bisect_left(starts, t2)
        over = [(k, max(0.0, min(ends[k], t2) - max(starts[k], t1))) for k in range(lo, hi)]
        over = [(k, o) for k, o in over if o > 0]
        if b1 != b2:
            for k, _o in over:
                unknown[k] = True
            continue
        for k, _o in over:
            bracketed[k] = True
        left = s2 - s1
        if left < SLEEP_MIN_S:
            continue
        for k, o in sorted(over, key=lambda x: x[1], reverse=True):
            room = min(o, (lines[k + 1]["_t"] - lines[k]["_t"]) - slept[k])
            take = max(0.0, min(room, left))
            slept[k] += take
            if take > 0:
                alloc[k].append((take, t1, t2))
            left -= take
            if left <= 0:
                break
        if t2 > w_lo and t1 < end:      # a pair wholly outside the window is not this closure's (Y7, codex)
            unplaced += max(0.0, left)

    review_u = _union(list(reviews))
    verify_u = _minus(_union(list(verifies)), review_u)     # an overlap counts once, as judges (impl r1, codex)
    acc = {"wall": 0.0, "total": 0.0, "not_task": 0.0, "slept": 0.0, "away": 0.0, "unknown": 0.0,
           "sleep_unmeasured": 0.0}
    by_kind: "dict[str, float]" = {}
    by_section: "dict[str, dict[str, float]]" = {}
    state = {"in_turn": False, "pending": {}, "keep": keep}
    section: "dict[str, str]" = {}

    def add(kind, sec, v):
        if v > 0:
            by_kind[kind] = by_kind.get(kind, 0.0) + v
            d = by_section.setdefault(sec, {})
            d[kind] = d.get(kind, 0.0) + v

    for k in range(n):
        if deadline is not None and k % 2000 == 1999 and time.monotonic() > deadline:
            raise OutOfTime("partition")
        a, b = lines[k], lines[k + 1]
        _step(state, a)
        ta = task_of(a)
        if a.get("hook") == "state-echo" and ta and isinstance(a.get("section"), str):
            section[ta] = a["section"]
        a_t = a["_t"] if start is None else max(a["_t"], start)    # a gap crossing the window's start is clipped
        gap = b["_t"] - a_t
        if gap <= 0:
            continue
        acc["wall"] += gap
        if not (ta == task and task_of(b) == task):
            acc["not_task"] += gap
            continue
        acc["total"] += gap
        if unknown[k]:
            acc["unknown"] += gap
            continue
        if not bracketed[k]:
            acc["sleep_unmeasured"] += gap
        sl = slept[k]                # never more than the gap: the placement caps each share at its room
        if a_t > a["_t"]:
            # a gap clipped at the window's start keeps the sleep its window part can hold, and only where each
            # share's own samples allow it: sleep they prove fell before the window is not charged to it (task 237's
            # impl round 1, codex)
            sl = min(gap, sum(min(tk, max(0.0, min(s2_, b["_t"]) - max(s1_, a_t))) for tk, s1_, s2_ in alloc[k]))
        acc["slept"] += sl
        awake = gap - sl
        if awake > AWAY_S:
            acc["away"] += awake
            continue
        sec = section.get(task, "?")
        tools = [v[0] for v in state["pending"].values()]
        if not state["in_turn"]:
            note = str(b.get("detail") or "")
            kind = ("background" if b.get("event") == "UserPromptSubmit"
                    and note.startswith("not logged: a notification") else "owner-idle")
            carve = kind == "background"
        elif "AskUserQuestion" in tools:
            kind, carve = "owner-question", False
        else:
            kind, carve = "agent", any(v in _SHELL_TOOLS for v in tools)
        if carve and awake > 0:
            j = min(awake, _overlap(a_t, b["_t"], review_u))
            v = min(awake - j, _overlap(a_t, b["_t"], verify_u))
            add("judges", sec, j)
            add("verify", sec, v)
            awake -= j + v
        add(kind, sec, awake)

    def r(v):
        return int(round(v))
    out = {k: r(v) for k, v in acc.items()}
    out["sleep_unplaced"] = r(unplaced)
    out["by_kind"] = {k: r(v) for k, v in by_kind.items() if r(v)}
    out["by_section"] = {s: {k: r(v) for k, v in d.items() if r(v)} for s, d in by_section.items()}
    out["by_section"] = {s: d for s, d in out["by_section"].items() if d}
    return out


# --- the side tables (their own units: seat seconds are summed over parallel seats, not wall time) --------------------

def seats_table(reviews: "list[dict]") -> "dict[str, dict]":
    """Per seat: runs, ok, failed, seconds (summed over its runs), tokens in / out / cache_read / cache_write from the
    runs whose usage is known, how many were unknown, and the runs by kind (panel / single / tail-cert / …)."""
    out: "dict[str, dict]" = {}
    for r in reviews:
        s = out.setdefault(str(r.get("seat") or "?"), {"runs": 0, "ok": 0, "failed": 0, "seconds": 0.0, "in": 0,
                                                       "out": 0, "cache_read": 0, "cache_write": 0, "unknown": 0,
                                                       "kinds": {}})
        s["runs"] += 1
        s["ok" if r.get("status") == "ok" else "failed"] += 1
        d = r.get("duration_ms")
        if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0:
            s["seconds"] += d / 1000.0
        u = r.get("usage") if isinstance(r.get("usage"), dict) else {}
        if u.get("status") == "known":
            for k in ("in", "out", "cache_read", "cache_write"):
                v = u.get(k)
                if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                    s[k] += v
        else:
            s["unknown"] += 1
        kind = str(r.get("kind") or "?")
        s["kinds"][kind] = s["kinds"].get(kind, 0) + 1
    for s in out.values():
        s["seconds"] = round(s["seconds"], 3)
    return out


_AGENT_KEYS = ("calls", "in", "out", "cache_read", "cache_write", "thinking", "unreadable")


def agent_table(records: "list[dict]") -> "dict | None":
    """The main agent's tokens in a closure (task 244), from its `agent-usage` records, or None when it has none (never
    zero calls for what was not recorded). Per model — a subagent's (`side`) as "<model> (subagent)" — calls, in, out,
    cache_read, cache_write, thinking (a PART of out, as the host counts it: never added to it) and unreadable entries;
    `unknown`: spans not counted (a seed, a file not readable then); `dropped`: an exact duplicate of a record already
    counted (the same file, span, model and side). Spans that overlap are NOT dropped: the recorder writes only under
    the lane lock, so overlapping spans are a file read again from 0 — another file put in its place, which counts
    (impl round 1 of task 244, codex: the overlap rule dropped such a file's calls)."""
    if not records:
        return None
    models: "dict[str, dict]" = {}
    seen: set = set()
    unknown = dropped = 0
    for r in sorted(records, key=lambda x: x.get("_n", 0)):
        if r.get("status") == "unknown":
            unknown += 1
            continue
        a, b = r.get("from"), r.get("to")
        model, side = str(r.get("model") or "?"), 1 if r.get("side") else 0
        if all(isinstance(v, int) and not isinstance(v, bool) for v in (a, b)):
            key = (r.get("file"), str(r.get("ino")), r.get("head"), a, b, model, side)
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
        m = models.setdefault(model + (" (subagent)" if side else ""), {k: 0 for k in _AGENT_KEYS})
        for k in _AGENT_KEYS:
            v = r.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                m[k] += v
    return {"models": models, "unknown": unknown, "dropped": dropped}


def verify_table(verifies: "list[dict]") -> dict:
    """The recorded verify runs (the close's, `tasks verify`): runs, ran, reused, failed, seconds."""
    out = {"runs": 0, "ran": 0, "reused": 0, "failed": 0, "seconds": 0.0}
    for r in verifies:
        out["runs"] += 1
        out["reused" if r.get("how") == "reused" else "ran"] += 1
        rc = r.get("rc")
        if not (isinstance(rc, int) and not isinstance(rc, bool) and rc == 0):     # a JSON `false` is no exit 0
            out["failed"] += 1
        s = r.get("seconds")
        if isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0:
            out["seconds"] += s
    out["seconds"] = round(out["seconds"], 3)
    return out


def _iso_epoch(ts) -> "int | None":
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def rounds_table(runs: "list[dict]", reviews: "list[dict]", start: float, end: float, failed: int = 0) -> dict:
    """The closure's review rounds: panels (mode, outcome, critical, important, owner_ok — from the merged run ledger,
    `post_d6.read_runs`), post-panel runs (verdict, critical, important, owner_ok), the single-judge calls and the
    tail certifications counted one per review record — a single-judge record within 120 s of a post-panel row is that
    run, not another one (impl r1, agy, codex, grok) — and the runs the ledger marks failed (which read_runs drops)."""
    panels, post = [], []
    for r in runs:
        t = _iso_epoch(r.get("ts"))
        if t is None or not (start <= t <= end):
            continue
        if r.get("kind") == "panel":
            m = r.get("measures") if isinstance(r.get("measures"), dict) else {}
            panels.append({"mode": r.get("mode"), "outcome": r.get("outcome"), "critical": m.get("critical"),
                           "important": m.get("important"), "owner_ok": r.get("owner_ok")})
        elif "verdict" in r:
            post.append({"verdict": r.get("verdict"), "critical": r.get("critical"), "important": r.get("important"),
                         "owner_ok": r.get("owner_ok"), "_t": t})
    unmatched = [p["_t"] for p in post]
    single = 0
    for rec in sorted((r for r in reviews if r.get("kind") == "single"), key=lambda r: r.get("_t") or 0):
        t = rec.get("_t")
        hit = next((u for u in unmatched if t is not None and abs(u - t) <= 120), None)
        if hit is not None:
            unmatched.remove(hit)
        else:
            single += 1
    for p in post:
        p.pop("_t", None)
    return {"panels": panels, "post_panel": post, "single": single,
            "tail_cert": sum(1 for r in reviews if r.get("kind") == "tail-cert"), "failed": failed}


def owner_table(lines: "list[dict]", task: str, log_text: "str | None", start: "float | None" = None,
                deadline: "float | None" = None) -> dict:
    """The owner's part: his prompts (the chat-log hook's `logged M<n>` lines of this task, and the prompt that started
    the closure — logged before the pointer moved, the last prompt before the session's first line of the task with no
    other task's line between; impl r1, grok), the background notifications, his options dialogs — answered (the dialog
    hook's `logged M<n>` PostToolUse, once per call), cancelled (a PreToolUse with no answer), their questions and
    answers counted from the DIALOG entry in chat_log.md (task 233), and the time he took to answer. `lines` may reach
    before `start` (the read margin): only the closure's first prompt is taken from there (it is logged before the
    `work` record that opens the window — seen live, W8)."""
    task = str(task)
    task_of = _task_of_lines(lines)
    out = {"prompts": 0, "notifications": 0, "dialogs": 0, "cancelled": 0, "questions": 0, "answers": 0, "wait": 0,
           "ids": []}
    index = dialog_index(log_text, deadline) if log_text else {}
    if deadline is not None and time.monotonic() > deadline:
        raise OutOfTime("owner")
    opened: "dict[str, int]" = {}
    answered: "set[str]" = set()
    first_prompt: "set[int]" = set()
    by_session: "dict[str, list[int]]" = {}
    for i, r in enumerate(lines):
        by_session.setdefault(str(r.get("host_session_id") or r.get("session_id") or "?"), []).append(i)
    for idx in by_session.values():
        first = next((i for i in idx if task_of(lines[i]) == task
                      and (start is None or lines[i]["_t"] >= start)), None)     # this closure's, not an earlier life's
        if first is None:
            continue
        act = lines[first].get("tool_use_id")
        # back from the closure's first line to the prompt that opened its turn: the activating call's own lines are
        # skipped (they carry the task before the switch — W8, agy); a released stop, a session's edge, an earlier
        # life's line of this task (a reopen with no new prompt — W7, grok) or another task's line ends the search.
        # A prompt that switches tasks therefore counts for both tasks.
        for i in reversed([j for j in idx if j < first]):
            r = lines[i]
            if r.get("hook") == "chat-log" and str(r.get("detail") or "").startswith("logged M"):
                first_prompt.add(i)
                break
            if (r.get("hook") == "chat-log" and r.get("event") == "UserPromptSubmit"
                    and r.get("detail") != "not logged: a duplicate"):
                break           # a notification or a command opened the turn, not his prompt (Y12, codex)
            if act and r.get("tool_use_id") == act:
                continue
            if r.get("event") in ("SessionStart", "SessionEnd") or (r.get("event") == "Stop"
                                                                     and r.get("decision") == "allow"):
                break
            t = task_of(r)
            if t == task and start is not None and r["_t"] < start:
                break
            if t and t != task:
                break
    for i, r in enumerate(lines):
        if i not in first_prompt and (task_of(r) != task or (start is not None and r["_t"] < start)):
            continue
        hook, ev, detail = r.get("hook"), r.get("event"), str(r.get("detail") or "")
        if hook == "chat-log" and ev == "UserPromptSubmit":
            if detail.startswith("logged M"):
                out["prompts"] += 1
                out["ids"].append(detail.split()[1])
            elif detail.startswith("not logged: a notification"):
                out["notifications"] += 1
        elif hook == "dialog-log" and r.get("tool_use_id"):
            tid = r["tool_use_id"]
            if ev == "PreToolUse":
                opened.setdefault(tid, r["_t"])
            elif ev == "PostToolUse" and detail.startswith("logged M") and tid not in answered:
                answered.add(tid)
                out["dialogs"] += 1
                out["ids"].append(detail.split()[1])
                if tid in opened:
                    out["wait"] += r["_t"] - opened[tid]
                c = index.get(_marker_id(tid))
                if c:
                    out["questions"] += c["questions"]
                    out["answers"] += c["answers"]
    out["cancelled"] = sum(1 for t in opened if t not in answered)
    return out


# --- one closure ------------------------------------------------------------------------------------------------------

CLOSE_RECORD_LAG_S = 600    # the close writes its `hook=close` records seconds after the receipt's stamp


def _is_done_text(text: str) -> bool:
    """The task's live `## Status` says done (core's reader: fence-aware, physical lines)."""
    try:
        from tasks.core import _physical_lines, _status_from_lines
        return str(_status_from_lines(_physical_lines(text))).strip().lower().startswith("done")
    except Exception:                       # noqa: BLE001 — unreadable: not known to be done
        return False


def _failed_runs(task_dir: Path, start: float, end: float) -> int:
    """Runs the ledger marks `failed` (read_runs drops them) whose reservation falls in the window (impl r1, opus)."""
    rows: "dict[str, dict]" = {}
    for raw in _lines_of(Path(task_dir) / "post-panel-reviews.jsonl"):
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("id"):
            rows.setdefault(str(r["id"]), {}).update(r)
    return sum(1 for r in rows.values() if r.get("status") == "failed"
               and (t := _iso_epoch(r.get("ts"))) is not None and start <= t <= end)


def _life(trans: "list[dict]", end: float, newest: bool):
    """(first work, done, next work's line) of the closure that ends at `end`, in the journal's FILE order — two closes
    in one second, with a reopen between, are told apart only by it (task 236's impl round 1, Y5: codex, grok). The
    closure's `done` is the task's last one for the newest close, else the first at or after `end` (a close writes its
    `done` after its receipt's stamp); its life starts at the first `work` after the `done` before it. None when no
    `done` and `work` fit (records from before the transition records, a `done` the journal failed to keep)."""
    dones = [r for r in trans if r.get("reason") == "done"]
    done = (dones[-1] if dones and dones[-1]["_t"] >= end else None) if newest \
        else next((r for r in dones if r["_t"] >= end), None)
    if done is None:
        return None
    prev = max((r["_n"] for r in dones if r["_n"] < done["_n"]), default=-1)
    first = next((r for r in trans if r.get("reason") == "work" and prev < r["_n"] < done["_n"]), None)
    if first is None:
        return None
    nxt = next((r["_n"] for r in trans if r.get("reason") == "work" and r["_n"] > done["_n"]), None)
    return first, done, nxt


def compose(lane: Path, task_dir: Path, task: str, deadline: float, end: "float | None" = None,
            live: "tuple | None" = None, closed: bool = False) -> dict:
    """One closure of `task`, from the plugin's records only. `end` defaults to the receipt's newest stamp (a closed
    task) or now (an open task: a snapshot). `live`: the close's own suspend sample (`write_closure` takes it).
    `closed`: the caller is the close, so the newest receipt is this closure's even if a reopen came since (Q6).
    Raises OutOfTime past the deadline; the caller catches everything."""
    task = str(task)
    newest = end is None
    lane, task_dir = Path(lane), Path(task_dir)
    data = _read_regular(task_dir / "task.md")
    text = data.decode("utf-8", "replace") if data is not None else ""
    rc = receipt_close(text)
    if end is None and rc is not None and not closed and not _is_done_text(text):
        rc = None                   # a reopened task: its old receipt is an earlier closure's (task 235, W6, codex)
    snapshot = end is None and rc is None
    if end is None:
        end = rc["epoch"] if rc else time.time()
    end = float(int(end))
    enf = read_enforcement(lane, deadline)
    mine = [r for r in enf if str(r.get("task", "")) == task]
    trans = [r for r in mine if r["hook"] == "tasks" and r.get("reason") in ("work", "done")]
    life = _life(trans, end, newest) if rc is not None else None
    if life is not None:
        # the closure in file order: its close records lie after its first `work` and before the next `work` after
        # its `done` (they come seconds after the receipt, up to the lag); its number is its `done`'s rank (W4, Y5)
        first, done, nxt = life
        start = first["_t"]
        closes = [r for r in mine if r["hook"] == "close" and first["_n"] < r["_n"] and (nxt is None or r["_n"] < nxt)
                  and r["_t"] <= end + CLOSE_RECORD_LAG_S]
        closure_no = sum(1 for r in trans if r.get("reason") == "done" and r["_n"] <= done["_n"])
        # its review / verify records too, by the same file-order bounds (task 236's impl round 2, Q3: codex)
        # — before its `done` the closure's, after it the late ones, even inside one second (K4, codex)
        in_life = [r for r in mine if first["_n"] < r["_n"] and (nxt is None or r["_n"] < nxt)]
        reviews = [r for r in in_life if r["hook"] == "review" and r["_n"] < done["_n"]]
        verifies = [r for r in in_life if r["hook"] == "verify" and r["_n"] < done["_n"]]
        late = sum(1 for r in in_life if r["hook"] in ("review", "verify") and r["_n"] > done["_n"])
        agents = [r for r in in_life if r["hook"] == _AGENT_HOOK and r["_n"] < done["_n"]]
        closing_sid = str(done.get("session_id") or "")
    else:
        # no `done` to anchor on (an open task's snapshot, records from before the transition records): by time
        window = closure_window([r for r in enf if r.get("hook") == "tasks"], task, end)
        start = window[0] if window else None
        nxt_t = min((r["_t"] for r in trans if r.get("reason") == "work" and r["_t"] > end), default=float("inf"))
        upto = min(end + CLOSE_RECORD_LAG_S, nxt_t)
        closes = [r for r in mine if r["hook"] == "close" and start is not None and start <= r["_t"] <= upto]
        # a receipt with no `done` to anchor on is still a close: at least the first (Minor, opus)
        closure_no = max(1, sum(1 for r in trans if r.get("reason") == "done" and r["_t"] <= upto))
        win = task_records(enf, task, start, end) if start is not None else {h: [] for h in _TASK_HOOKS}
        reviews, verifies = win["review"], win["verify"]
        late = sum(1 for r in mine if r["hook"] in ("review", "verify") and end < r["_t"] < nxt_t)
        agents = [r for r in mine if r["hook"] == _AGENT_HOOK and start is not None and start <= r["_t"] <= end]
        closing_sid = ""
    review_spans = [(r["_t"] - r["duration_ms"] / 1000.0, r["_t"]) for r in reviews
                    if isinstance(r.get("duration_ms"), (int, float)) and not isinstance(r.get("duration_ms"), bool)]
    verify_spans = [(r["_t"] - r["seconds"], r["_t"]) for r in verifies if r.get("how") != "reused"
                    and isinstance(r.get("seconds"), (int, float)) and not isinstance(r.get("seconds"), bool)]
    out: dict = {"schema": SCHEMA, "task": task, "snapshot": snapshot, "closed_at": rc["stamp"] if rc else None,
                 "closure_no": closure_no if rc else None,
                 "window": ({"start": _iso(start), "end": _iso(end)} if start is not None else None), "time": None}
    lines: "list[dict]" = []
    if start is not None:
        lines = read_hook_lines(lane / "journal", start, deadline, end=end)
        sessions: "dict[str, list[dict]]" = {}
        for r in lines:
            key = str(r.get("host_session_id") or r.get("session_id") or "?")
            sessions.setdefault(key, []).append(r)
        tot = {"wall": 0, "total": 0, "not_task": 0, "slept": 0, "away": 0, "unknown": 0, "sleep_unmeasured": 0,
               "sleep_unplaced": 0}
        by_kind: "dict[str, int]" = {}
        by_section: "dict[str, dict[str, int]]" = {}
        used = 0
        # the close's own call ends at `end` only in the session that holds it (impl r1, opus; W9, grok): the one its
        # `done` record names (the close writes it from that session — task 236's impl round 2, Q4: codex, grok);
        # without one, this task's latest open call
        # — by its lines up to the close only: a host session after a `/clear` keeps the same `pid-N` (K3, sonnet)
        named = []
        for key, sl in sessions.items():
            seen = [r["_t"] for r in sl if closing_sid and r.get("session_id") == closing_sid and r["_t"] <= end]
            if seen:
                named.append((max(seen), key))
        closing = max(named)[1] if named else closing_session(sessions, task, end)
        for key, sl in sessions.items():
            if time.monotonic() > deadline:
                raise OutOfTime("partition")
            p = session_partition(sl, task, end, review_spans, verify_spans, start=start, close_open=key == closing,
                                  deadline=deadline, live=live if key == closing else None,
                                  known_close=bool(named) and key == closing)
            if not p["total"]:
                continue
            used += 1
            for k in tot:
                tot[k] += p[k]
            for k, v in p["by_kind"].items():
                by_kind[k] = by_kind.get(k, 0) + v
            for s, d in p["by_section"].items():
                dd = by_section.setdefault(s, {})
                for k, v in d.items():
                    dd[k] = dd.get(k, 0) + v
        out["time"] = {"sessions": used, "total": tot["total"], "by_kind": by_kind, "by_section": by_section,
                       "slept": tot["slept"], "away": tot["away"], "unknown": tot["unknown"],
                       "sleep_unmeasured": tot["sleep_unmeasured"], "sleep_unplaced": tot["sleep_unplaced"]}
    out["judges_wall"] = int(sum(b - a for a, b in _union(review_spans)))
    out["seats"] = seats_table(reviews)
    out["agent"] = agent_table(agents)
    out["verify"] = verify_table(verifies)
    try:
        from tasks.post_d6 import read_runs
        runs = read_runs(task_dir)
    except Exception:                       # noqa: BLE001 — a ledger that cannot be read leaves the rounds empty
        runs = []
    out["rounds"] = rounds_table(runs, reviews, start if start is not None else end, end,
                                 failed=_failed_runs(task_dir, start if start is not None else end, end))
    log = None
    if any(r.get("hook") == "dialog-log" for r in lines):
        raw = _read_regular(lane / "chat_log.md")
        log = raw.decode("utf-8", "replace") if raw is not None else None
    out["owner"] = (owner_table([r for r in lines if r["_t"] <= end], task, log, start=start, deadline=deadline)
                    if start is not None else owner_table([], task, log))
    close = dict(rc) if rc else {}
    close.pop("epoch", None)
    # the receipt holds the reasons whole; the journal's records (their `command` cut to 200 bytes) only fill a gap
    # (task 235's last round, W2, opus)
    for r in closes:
        reason, cmd = str(r.get("reason") or ""), str(r.get("command") or "")
        if reason == "stale close owner-ok" and not close.get("owner_ok"):
            close["owner_ok"] = _quoted(cmd, "owner-ok: ") or cmd    # cut at 200 bytes: no closing quote (Y10, opus)
        elif reason == "stale close bound" and not close.get("bound"):
            close["bound"] = cmd
        elif reason.startswith("stale close no judge") and not close.get("stop_rule"):
            close["stop_rule"] = cmd
    out["close"] = close or None
    out["late_records"] = late
    return out


# --- the file and the command -----------------------------------------------------------------------------------------

BUDGET_S = 5.0              # the close does not wait longer than this on its numbers
FILE_NAME = "numbers.json"


def _read_doc(path: Path) -> "dict | None":
    data = _read_regular(path)
    if data is None:
        return None
    try:
        doc = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not (isinstance(doc, dict) and isinstance(doc.get("closures"), list)):
        return None
    doc["closures"] = [c for c in doc["closures"] if isinstance(c, dict)]     # a stray element is skipped (agy)
    return doc


def _live_sample() -> "tuple | None":
    """`pb_journal.suspend_sample()` taken now, loaded like the close's other journal helper; None on any fault."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_pb_journal_numbers", Path(__file__).resolve().parent.parent / "scripts" / "pb_journal.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return tuple(mod.suspend_sample())
    except Exception:                       # noqa: BLE001 — no sample: the closing gap is unmeasured, as before
        return None


def write_closure(lane: Path, task_dir: Path, task: str, budget: float = BUDGET_S) -> "Path | None":
    """Compose this closure and keep it in `<task>/numbers.json` (`closures[]`, one per close, keyed by the receipt's
    stamp). Under the task lock, and only while the task's newest receipt is the one composed for — a close that paused
    before writing never replaces a later close's numbers (plan panel, codex). None when nothing was written."""
    from tasks.atomic import atomic_write
    from tasks.filelock import task_lock
    task_dir = Path(task_dir)
    deadline = time.monotonic() + budget     # ONE deadline: the reading, the lock wait and the write (impl r1, codex)
    live = _live_sample()                    # the close's own sample brackets its last gap (task 236's impl r1, Y6)
    path = task_dir / FILE_NAME
    left = deadline - time.monotonic()
    if left <= 0:
        raise OutOfTime("before the lock")
    # composed UNDER the task lock: a reopen or a second close cannot come between the receipt it reads and the
    # journal it reads (task 236's impl round 1, Y5: codex)
    with task_lock(task_dir / "task.md", timeout=left):     # LockTimeout past it
        closure = compose(lane, task_dir, task, deadline=deadline, live=live, closed=True)   # Q6: even if reopened
        if closure.get("snapshot") or not closure.get("closed_at"):
            return None
        data = _read_regular(task_dir / "task.md")
        newest = receipt_close(data.decode("utf-8", "replace")) if data is not None else None
        if not newest or newest["stamp"] != closure["closed_at"]:
            return None
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return None
        doc = _read_doc(path) or {"schema": SCHEMA, "task": str(task), "closures": []}
        # one entry per close: replaced only by the same close (its stamp AND its number), so two closes within one
        # second keep two entries (W4, codex)
        kept = [c for c in doc["closures"] if isinstance(c, dict)
                and (c.get("closed_at"), c.get("closure_no")) != (closure["closed_at"], closure.get("closure_no"))]
        kept.append(closure)
        kept.sort(key=lambda c: (_iso_epoch(c.get("closed_at")) or 0, c.get("closure_no") or 0))
        doc.update(schema=SCHEMA, task=str(task), closures=kept)
        atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return path


def _dur(s, tenths: bool = False) -> str:
    """Seconds as `1h05m` / `3m07s` / `9s`; `tenths` keeps one decimal of the seconds (seat and verify seconds)."""
    v = float(s or 0)
    if v >= 3600:
        return f"{int(v) // 3600}h{int(v) % 3600 // 60:02d}m"
    if tenths:
        m, rest = divmod(round(v, 1), 60)
        return f"{int(m)}m{rest:04.1f}s" if m else f"{rest:.1f}s"
    v = int(v)
    if v >= 60:
        return f"{v // 60}m{v % 60:02d}s"
    return f"{v}s"


def _n(v) -> str:
    return "-" if v is None else str(v)


_KINDS = ("agent", "judges", "verify", "owner-idle", "owner-question", "background")


def format_closure(c: dict) -> "list[str]":
    """A closure as a few lines of text."""
    out = []
    w = c.get("window") or {}
    head = ("snapshot up to now — not closed, nothing written" if c.get("snapshot")
            else f"closure {'#' + str(c['closure_no']) + ' ' if c.get('closure_no') else ''}{c.get('closed_at')}")
    out.append(f"{head}  (window {w.get('start', '?')} → {w.get('end', '?')})")
    t = c.get("time")
    if t:
        bk = t.get("by_kind") or {}
        parts = [f"{k} {_dur(bk[k])}" for k in _KINDS if bk.get(k)]
        parts += [f"{k} {_dur(t[k])}" for k in ("slept", "away", "unknown") if t.get(k)]
        out.append(f"  time {_dur(t.get('total'))} in {t.get('sessions', 0)} session(s): " + (" · ".join(parts) or "—"))
        secs = {s: sum(d.values()) for s, d in (t.get("by_section") or {}).items()}
        if secs:
            # "" is the echo's "no section": no gate open (the time after the last tick) or an open gate under no `##`
            # heading — named, not printed blank (task 248; its impl round 1, opus / codex: one label true of both)
            out.append("  by section: " + " · ".join(f"{s or '(no section)'} {_dur(v)}" for s, v in secs.items()))
    else:
        out.append("  time: no activation record in the journal for this closure")
    r = c.get("rounds") or {}
    bits = [f"{p.get('mode')} panel {p.get('outcome')} (C{_n(p.get('critical'))} I{_n(p.get('important'))})"
            + (f" owner-ok: {p['owner_ok']}" if p.get("owner_ok") else "") for p in r.get("panels") or []]
    bits += [f"post-panel {p.get('verdict')} (C{_n(p.get('critical'))} I{_n(p.get('important'))})"
             + (f" owner-ok: {p['owner_ok']}" if p.get("owner_ok") else "") for p in r.get("post_panel") or []]
    if r.get("failed"):
        bits.append(f"failed runs ×{r['failed']}")
    if r.get("single"):
        bits.append(f"single judge ×{r['single']}")
    if r.get("tail_cert"):
        bits.append(f"tail-cert ×{r['tail_cert']}")
    out.append("  rounds: " + (" · ".join(bits) or "none") + f"  — judges' wall time {_dur(c.get('judges_wall'))}")
    for seat, s in sorted((c.get("seats") or {}).items()):
        out.append(f"  seat {seat}: {s['runs']} run(s), {s['failed']} failed, {_dur(s['seconds'], True)}, "
                   f"in {s['in']} out {s['out']} cache {s['cache_read']}/{s['cache_write']}"
                   + (f", {s['unknown']} without usage" if s.get("unknown") else ""))
    if "agent" in c:                         # task 244; a closure written before it has no such key: nothing said
        a = c.get("agent")
        if not a:
            out.append("  agent: no record of its tokens (no recorder ran in this closure)")
        else:
            for model, m in sorted((a.get("models") or {}).items()):
                out.append(f"  agent {model}: {m['calls']} call(s), in {m['in']} out {m['out']} "
                           f"(of which thinking {m['thinking']}) cache {m['cache_read']}/{m['cache_write']}"
                           + (f", {m['unreadable']} unreadable" if m.get("unreadable") else ""))
            if a.get("unknown") or a.get("dropped"):
                out.append(f"  agent: {a.get('unknown', 0)} span(s) not counted, "
                           f"{a.get('dropped', 0)} duplicate span(s) dropped")
    v = c.get("verify") or {}
    out.append(f"  verify: {v.get('runs', 0)} run(s), {v.get('reused', 0)} reused, {v.get('failed', 0)} failed, "
               f"{_dur(v.get('seconds'), True)}")
    o = c.get("owner") or {}
    out.append(f"  owner: {o.get('prompts', 0)} prompt(s), {o.get('dialogs', 0)} dialog(s) "
               f"({o.get('questions', 0)} question(s)) answered in {_dur(o.get('wait'))}, {o.get('cancelled', 0)} cancelled")
    cl = c.get("close") or {}
    if cl:
        bits = [str(cl.get("freshness") or "no panel check")]          # a forced close's receipt has no freshness line
        for k, label in (("forced_reason", "forced"), ("accepted_reason", "accepted"), ("owner_ok", "owner-ok"),
                         ("bound", ""), ("stop_rule", "no judge after the last round")):
            if cl.get(k):               # a bound reads "bound to post-D6 PASS …" on its own (Minor, agy)
                bits.append(f"{label}: {cl[k]}" if label else str(cl[k]))
        out.append("  close: " + " · ".join(bits))
    return out


def cmd_numbers(cmd_args: "list[str]") -> None:
    """`tasks numbers [N]` — read-only: the task's closures from its numbers.json, or, for a task not closed (or closed
    before numbers.json existed), a composition from the records marked as a snapshot. Never writes."""
    import sys
    from tasks.core import _find_active_task, resolve_agent_dir
    from tasks.intent import find_task_dir
    from tasks.shared import find_project_root
    if len(cmd_args) > 1 or (cmd_args and not cmd_args[0].isdigit()):
        print("Error: usage: tasks numbers [N]", file=sys.stderr)
        raise SystemExit(2)
    project = find_project_root()
    lane = resolve_agent_dir(project)
    tasks_dir = lane / "tasks"
    if cmd_args:
        d = find_task_dir(tasks_dir, cmd_args[0].zfill(3))
    else:
        tf = _find_active_task(project)
        d = tf.parent if tf is not None else None
    if d is None or not (d / "task.md").is_file():
        print(f"Error: no task {cmd_args[0] if cmd_args else '(none active)'}", file=sys.stderr)
        raise SystemExit(2)
    task = d.name.split("-", 1)[0]
    print(f"Task {task} — {d.name}")
    doc = _read_doc(d / FILE_NAME)
    if doc and doc["closures"]:
        for c in doc["closures"]:
            for ln in format_closure(c):
                print(ln)
        data = _read_regular(d / "task.md")
        text = data.decode("utf-8", "replace") if data is not None else ""
        newest = receipt_close(text)
        saved = {(c.get("closed_at"), c.get("closure_no")) for c in doc["closures"] if isinstance(c, dict)}
        try:            # read once: the newest close's number and the late records both come from it (Minor, agy)
            mine = [r for r in read_enforcement(lane, time.monotonic() + BUDGET_S) if str(r.get("task", "")) == task]
        except OutOfTime:
            mine = None
        # the newest close's number as compose gives it: the task's count of `done` records, at least 1 (K1, opus);
        # unknown when the read ran out of time — then nothing is claimed missing
        dones = [r for r in mine or () if r["hook"] == "tasks" and r.get("reason") == "done"]
        n_done = max(1, len(dones)) if mine is not None else None
        # the life in progress of a reopened task, or a newest close whose write failed — by its stamp AND its number,
        # so a second close in the same second is not taken for the first (task 235, W6; task 236's impl r1, Y4)
        if not _is_done_text(text) or (newest and n_done is not None and (newest["stamp"], n_done) not in saved):
            try:
                c = compose(lane, d, task, deadline=time.monotonic() + BUDGET_S * 4)
            except OutOfTime:
                print("(the records took too long to read)")
                return
            if not c.get("snapshot"):
                print("(the newest close is not in numbers.json — composed now from the records)")
            for ln in format_closure(c):
                print(ln)
            return
        # written after the last close: after its `done` in file order, inside its second too (K4, codex)
        last = _iso_epoch(doc["closures"][-1].get("closed_at")) or 0
        late = sum(1 for r in mine or () if r["hook"] in ("review", "verify")
                   and (r["_n"] > dones[-1]["_n"] if dones else r["_t"] > last))
        if late:
            print(f"  ({late} review / verify record(s) written after the last close are in no closure)")
        return
    try:
        c = compose(lane, d, task, deadline=time.monotonic() + BUDGET_S * 4)
    except OutOfTime:
        print("(the records took too long to read — nothing shown)")
        return
    if not c.get("snapshot"):
        print("(no numbers.json — closed before it existed, or its write failed; composed now from the records)")
    for ln in format_closure(c):
        print(ln)
