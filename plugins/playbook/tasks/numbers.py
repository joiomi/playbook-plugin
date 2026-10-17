"""tasks/numbers.py — a task's own numbers, composed at its close from the plugin's records only.

PHASE-B item 6, last part (task 235; the owner, 2026-10-13 18:44: "În Faza B (Recomandat)"). Item 8 re-measures B1's
numbers; B1 rebuilt them from the host's session transcripts, which the host deletes after about 30 days. Tasks 232-234
made the plugin record the facts in its own files; this module composes them per closure of a task:

  * the closure's window — from the latest `tasks work N` after the task's previous `done` to this close's receipt;
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
    """The hook journal's lines from `start - margin` to `end + margin`: the archives renamed at or after the window's
    start (an archive renamed before it holds only older lines), the live file, then the unmerged spools — sorted by
    `ts`, file order kept within a second, exact duplicates dropped (a spool taken over but not yet removed). Each line
    gets `_t` (epoch seconds). The margin keeps the suspend samples just outside the window."""
    lo = _iso(start - margin)
    hi = _iso(end + margin) if end is not None else None
    files: "list[tuple[int, Path]]" = []
    if Path(jdir).is_symlink():
        return []                           # the writers refuse a linked journal/ (task 222); so does the reader
    try:
        names = sorted(os.listdir(jdir))
    except OSError:
        return []
    for name in names:
        m = _ARCHIVE.fullmatch(name)
        if m and int(m.group(1)) >= start:
            files.append((int(m.group(1)), jdir / name))
    files.sort(key=lambda f: f[0])
    paths = [p for _, p in files] + [jdir / "hooks.jsonl"] + [jdir / n for n in names if _SPOOL.fullmatch(n)]
    seen: "set[str]" = set()
    out: "list[dict]" = []
    for path in paths:
        if time.monotonic() > deadline:
            raise OutOfTime(str(path))
        for raw in _lines_of(path):
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


def read_enforcement(lane: Path, deadline: float) -> "list[dict]":
    """The enforcement journal's review / verify / tasks / close records (with `_t`)."""
    out = []
    for raw in _lines_of(lane / "journal" / "enforcement.jsonl"):
        if time.monotonic() > deadline:
            raise OutOfTime("enforcement.jsonl")
        raw = raw.strip()
        if not raw.startswith("{") or not any(f'"hook":"{h}"' in raw or f'"hook": "{h}"' in raw for h in _TASK_HOOKS):
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("hook") in _TASK_HOOKS:
            t = _epoch(rec.get("ts"))
            if t is not None:
                rec["_t"] = t
                out.append(rec)
    return out


def read_task_records(lane: Path, task: str, start: float, end: float, deadline: float) -> "dict[str, list[dict]]":
    """This task's review, verify, tasks and close records in [start, end] (a record with no `task` is no task's)."""
    got: "dict[str, list[dict]]" = {h: [] for h in _TASK_HOOKS}
    for rec in read_enforcement(lane, deadline):
        if str(rec.get("task", "")) == str(task) and start <= rec["_t"] <= end:
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


def dialog_counts(log_text: str, tool_use_id: str) -> "dict | None":
    """{id, questions, answers} of the DIALOG entry whose last line is `<!-- dialog:<tool_use_id> -->` (task 233's
    format: every body line starts with a fixed prefix, so a `Q<n>`/`A<n>:` at a line start is one)."""
    if not tool_use_id:
        return None
    lines = log_text.split("\n")
    mark = f"<!-- dialog:{tool_use_id} -->"
    try:
        k = max(i for i, ln in enumerate(lines) if ln.strip() == mark)
    except ValueError:
        return None
    head = next((i for i in range(k, -1, -1) if _ENTRY_HEAD.match(lines[i])), None)
    if head is None:
        return None
    body = lines[head + 1:k]
    m = _ENTRY_HEAD.match(lines[head])
    return {"id": m.group(1) if m else "",
            "questions": sum(1 for ln in body if re.match(r"Q\d+[ :(]", ln)),
            "answers": sum(1 for ln in body if re.match(r"A\d+: ", ln))}


# --- the receipt ------------------------------------------------------------------------------------------------------

_BLOCK = re.compile(r"^### (\S+) · risk (\S+)")


def receipt_close(task_text: str) -> "dict | None":
    """How this closure closed, from the receipt's newest `###` block (the close inserts its block at the top; the
    newest STAMP wins wherever it sits): stamp, epoch, risk, freshness (FRESH / STALE / TAIL-CERTIFIED / NO-STAMP / …),
    the accepted and forced reasons, the commands' PASS / FAIL counts."""
    lines = task_text.split("\n")
    try:
        sec = next(i for i, ln in enumerate(lines) if ln.rstrip() == "## Verification Receipt")
    except StopIteration:
        return None
    blocks = []
    cur = None
    for ln in lines[sec + 1:]:
        if ln.startswith("## "):
            break
        m = _BLOCK.match(ln)
        if m:
            cur = {"stamp": m.group(1), "risk": m.group(2), "lines": []}
            blocks.append(cur)
        elif cur is not None:
            cur["lines"].append(ln)
    best = None
    for b in blocks:
        try:
            b["epoch"] = int(datetime.fromisoformat(b["stamp"]).timestamp())
        except ValueError:
            continue
        if best is None or b["epoch"] > best["epoch"]:
            best = b
    if best is None:
        return None
    out = {"stamp": best["stamp"], "epoch": best["epoch"], "risk": best["risk"], "freshness": None,
           "accepted_reason": None, "forced_reason": None, "commands": {"pass": 0, "fail": 0}}
    for ln in best["lines"]:
        s = ln.strip()
        if s.startswith("- **Forced close, reason:**"):
            out["forced_reason"] = s.split(":**", 1)[1].strip()
        elif s.startswith("- **Panel tree-state:**"):
            if "TAIL-CERTIFIED" in s:
                out["freshness"] = "TAIL-CERTIFIED"
            else:
                m = re.search(r" — (FRESH|STALE|NO-STAMP)\b", s) or re.search(r"\b(EXCLUDE-COVERS-CODE|n/a)\b", s)
                out["freshness"] = m.group(1) if m else "other"
            m = re.search(r', accepted: "(.*)"$', s)
            if m:
                out["accepted_reason"] = m.group(1)
        elif s.startswith("- [PASS]"):
            out["commands"]["pass"] += 1
        elif s.startswith("- [FAIL]"):
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


def session_partition(lines: "list[dict]", task: str, end: float, reviews: "list[tuple[float, float]]",
                      verifies: "list[tuple[float, float]]", start: "float | None" = None) -> dict:
    """Task `task`'s share of one host session's time up to `end` (see the block comment above). Returns integer
    seconds: wall (the session's span in the window), total (the task's), not_task, slept, away, unknown,
    sleep_unmeasured (the task's time no two samples of one boot bracket), by_kind and by_section (both without zeros).
    Holds Q1: sum(by_kind) + slept + away + unknown == total; total + not_task == wall."""
    task = str(task)
    lines = [r for r in lines if r["_t"] <= end]
    if not lines:
        return {"wall": 0, "total": 0, "not_task": 0, "slept": 0, "away": 0, "unknown": 0, "sleep_unmeasured": 0,
                "by_kind": {}, "by_section": {}}
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

    # an open call at `end` (the close's own Bash) ends there: a virtual line closes the session
    pend: "dict[str, str]" = {}
    for r in lines:
        ev = r.get("event")
        if ev == "PreToolUse" and r.get("tool_use_id"):
            pend[r["tool_use_id"]] = r.get("tool") or ""
        elif ev in ("PostToolUse", "PostToolUseFailure"):
            pend.pop(r.get("tool_use_id"), None)
        elif ev in ("SessionStart", "SessionEnd") or (ev == "Stop" and r.get("decision") == "allow"):
            pend.clear()
    if pend and lines[-1]["_t"] < end:
        lines = lines + [{"_t": end, "event": "End", "task": task_of(lines[-1]), "_virtual": True}]

    # sleep, per gap: the `susp` difference between two samples of one boot, to the gaps between them, longest first,
    # each capped at its length; across a boot change those gaps are unknown
    n = len(lines) - 1
    slept = [0.0] * n
    unknown = [False] * n
    bracketed = [False] * n
    samples = [(i, s) for i, r in enumerate(lines) if (s := _sample(r)) is not None]
    for (i1, s1), (i2, s2) in zip(samples, samples[1:]):
        idx = list(range(i1, i2))
        if s1[2] != s2[2]:
            for k in idx:
                unknown[k] = True
            continue
        for k in idx:
            bracketed[k] = True
        left = s2[0] - s1[0]
        if left < SLEEP_MIN_S:
            continue
        for k in sorted(idx, key=lambda k: lines[k + 1]["_t"] - lines[k]["_t"], reverse=True):
            room = (lines[k + 1]["_t"] - lines[k]["_t"]) - slept[k]
            take = min(room, left)
            slept[k] += take
            left -= take
            if left <= 0:
                break

    review_u, verify_u = _union(list(reviews)), _union(list(verifies))
    acc = {"wall": 0.0, "total": 0.0, "not_task": 0.0, "slept": 0.0, "away": 0.0, "unknown": 0.0,
           "sleep_unmeasured": 0.0}
    by_kind: "dict[str, float]" = {}
    by_section: "dict[str, dict[str, float]]" = {}
    in_turn = False
    pending: "dict[str, str]" = {}
    section: "dict[str, str]" = {}

    def add(kind, sec, v):
        if v > 0:
            by_kind[kind] = by_kind.get(kind, 0.0) + v
            d = by_section.setdefault(sec, {})
            d[kind] = d.get(kind, 0.0) + v

    for k in range(n):
        a, b = lines[k], lines[k + 1]
        ev = a.get("event")
        if ev == "UserPromptSubmit":
            in_turn = True
        elif ev in ("SessionStart", "SessionEnd") or (ev == "Stop" and a.get("decision") == "allow"):
            in_turn = False
            pending.clear()
        elif ev == "PreToolUse" and a.get("tool_use_id"):
            pending[a["tool_use_id"]] = a.get("tool") or ""
        elif ev in ("PostToolUse", "PostToolUseFailure"):
            pending.pop(a.get("tool_use_id"), None)
        ta = task_of(a)
        if a.get("hook") == "state-echo" and ta and isinstance(a.get("section"), str):
            section[ta] = a["section"]
        gap = b["_t"] - a["_t"]
        if gap <= 0 or (start is not None and a["_t"] < start):
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
        s = slept[k]
        acc["slept"] += s
        awake = gap - s
        if awake > AWAY_S:
            acc["away"] += awake
            continue
        sec = section.get(task, "?")
        if not in_turn:
            note = str(b.get("detail") or "")
            kind = ("background" if b.get("event") == "UserPromptSubmit"
                    and note.startswith("not logged: a notification") else "owner-idle")
            carve = kind == "background"
        elif "AskUserQuestion" in pending.values():
            kind, carve = "owner-question", False
        else:
            kind, carve = "agent", any(v in ("Bash", "Shell", "run_terminal_command") for v in pending.values())
        if carve and awake > 0:
            j = min(awake, _overlap(a["_t"], b["_t"], review_u))
            v = min(awake - j, _overlap(a["_t"], b["_t"], verify_u))
            add("judges", sec, j)
            add("verify", sec, v)
            awake -= j + v
        add(kind, sec, awake)

    def r(v):
        return int(round(v))
    out = {k: r(v) for k, v in acc.items()}
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


def verify_table(verifies: "list[dict]") -> dict:
    """The recorded verify runs (the close's, `tasks verify`): runs, ran, reused, failed, seconds."""
    out = {"runs": 0, "ran": 0, "reused": 0, "failed": 0, "seconds": 0.0}
    for r in verifies:
        out["runs"] += 1
        out["reused" if r.get("how") == "reused" else "ran"] += 1
        rc = r.get("rc")
        if not (isinstance(rc, int) and rc == 0):
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


def rounds_table(runs: "list[dict]", reviews: "list[dict]", start: float, end: float) -> dict:
    """The closure's review rounds: panels (mode, outcome, critical, important — from the merged run ledger,
    `post_d6.read_runs`), post-panel runs (verdict, critical, important), and the single-judge and tail-certification
    runs from the review records (a round's seats are one run)."""
    panels, post = [], []
    for r in runs:
        t = _iso_epoch(r.get("ts"))
        if t is None or not (start <= t <= end):
            continue
        if r.get("kind") == "panel":
            m = r.get("measures") if isinstance(r.get("measures"), dict) else {}
            panels.append({"mode": r.get("mode"), "outcome": r.get("outcome"), "critical": m.get("critical"),
                           "important": m.get("important")})
        elif "verdict" in r:
            post.append({"verdict": r.get("verdict"), "critical": r.get("critical"), "important": r.get("important")})
    kinds = {(str(r.get("kind")), r.get("round")) for r in reviews}
    return {"panels": panels, "post_panel": post, "single": sum(1 for k, _ in kinds if k == "single"),
            "tail_cert": sum(1 for k, _ in kinds if k == "tail-cert")}


def _task_of_lines(lines: "list[dict]"):
    by_tid = {}
    for r in lines:
        t = r.get("task")
        if isinstance(t, str) and _TASK_RE.fullmatch(t) and r.get("tool_use_id"):
            by_tid.setdefault(r["tool_use_id"], t)

    def task_of(r):
        t = r.get("task")
        if isinstance(t, str) and _TASK_RE.fullmatch(t):
            return t
        return by_tid.get(r.get("tool_use_id") or "")
    return task_of


def owner_table(lines: "list[dict]", task: str, log_text: "str | None") -> dict:
    """The owner's part: his prompts (the chat-log hook's `logged M<n>` lines of this task), the background
    notifications, his options dialogs — answered (the dialog hook's `logged M<n>` PostToolUse), cancelled (a
    PreToolUse with no answer), their questions and answers counted from the DIALOG entry in chat_log.md (task 233),
    and the time he took to answer (PreToolUse → PostToolUse)."""
    task_of = _task_of_lines(lines)
    out = {"prompts": 0, "notifications": 0, "dialogs": 0, "cancelled": 0, "questions": 0, "answers": 0, "wait": 0,
           "ids": []}
    opened: "dict[str, int]" = {}
    answered: "set[str]" = set()
    for r in lines:
        if task_of(r) != str(task):
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
            elif ev == "PostToolUse" and detail.startswith("logged M"):
                answered.add(tid)
                out["dialogs"] += 1
                out["ids"].append(detail.split()[1])
                if tid in opened:
                    out["wait"] += r["_t"] - opened[tid]
                c = dialog_counts(log_text or "", tid)
                if c:
                    out["questions"] += c["questions"]
                    out["answers"] += c["answers"]
    out["cancelled"] = sum(1 for t in opened if t not in answered)
    return out


# --- one closure ------------------------------------------------------------------------------------------------------

CLOSE_RECORD_LAG_S = 600    # the close writes its `hook=close` records seconds after the receipt's stamp


def compose(lane: Path, task_dir: Path, task: str, deadline: float, end: "float | None" = None) -> dict:
    """One closure of `task`, from the plugin's records only. `end` defaults to the receipt's newest stamp (a closed
    task) or now (an open task: a snapshot). Raises OutOfTime past the deadline; the caller catches everything."""
    task = str(task)
    lane, task_dir = Path(lane), Path(task_dir)
    data = _read_regular(task_dir / "task.md")
    text = data.decode("utf-8", "replace") if data is not None else ""
    rc = receipt_close(text)
    snapshot = end is None and rc is None
    if end is None:
        end = rc["epoch"] if rc else time.time()
    end = float(int(end))
    enf = read_enforcement(lane, deadline)
    window = closure_window([r for r in enf if r.get("hook") == "tasks"], task, end)
    start = window[0] if window else None
    mine = [r for r in enf if str(r.get("task", "")) == task]
    in_win = (lambda r: start <= r["_t"] <= end) if start is not None else (lambda r: False)
    reviews = [r for r in mine if r["hook"] == "review" and in_win(r)]
    verifies = [r for r in mine if r["hook"] == "verify" and in_win(r)]
    closes = [r for r in mine if r["hook"] == "close" and start is not None
              and start <= r["_t"] <= end + CLOSE_RECORD_LAG_S]
    late = sum(1 for r in mine if r["hook"] in ("review", "verify") and r["_t"] > end)
    review_spans = [(r["_t"] - r["duration_ms"] / 1000.0, r["_t"]) for r in reviews
                    if isinstance(r.get("duration_ms"), (int, float)) and not isinstance(r.get("duration_ms"), bool)]
    verify_spans = [(r["_t"] - r["seconds"], r["_t"]) for r in verifies if r.get("how") != "reused"
                    and isinstance(r.get("seconds"), (int, float)) and not isinstance(r.get("seconds"), bool)]
    out: dict = {"schema": SCHEMA, "task": task, "snapshot": snapshot, "closed_at": rc["stamp"] if rc else None,
                 "window": ({"start": _iso(start), "end": _iso(end)} if start is not None else None), "time": None}
    lines: "list[dict]" = []
    if start is not None:
        lines = read_hook_lines(lane / "journal", start, deadline, end=end)
        sessions: "dict[str, list[dict]]" = {}
        for r in lines:
            key = str(r.get("host_session_id") or r.get("session_id") or "?")
            sessions.setdefault(key, []).append(r)
        tot = {"wall": 0, "total": 0, "not_task": 0, "slept": 0, "away": 0, "unknown": 0, "sleep_unmeasured": 0}
        by_kind: "dict[str, int]" = {}
        by_section: "dict[str, dict[str, int]]" = {}
        used = 0
        for key, sl in sessions.items():
            if time.monotonic() > deadline:
                raise OutOfTime("partition")
            p = session_partition(sl, task, end, review_spans, verify_spans, start=start)
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
                       "sleep_unmeasured": tot["sleep_unmeasured"]}
    out["judges_wall"] = int(sum(b - a for a, b in _union(review_spans)))
    out["seats"] = seats_table(reviews)
    out["verify"] = verify_table(verifies)
    try:
        from tasks.post_d6 import read_runs
        runs = read_runs(task_dir)
    except Exception:                       # noqa: BLE001 — a ledger that cannot be read leaves the rounds empty
        runs = []
    out["rounds"] = rounds_table(runs, reviews, start if start is not None else end, end)
    log = None
    if any(r.get("hook") == "dialog-log" for r in lines):
        raw = _read_regular(lane / "chat_log.md")
        log = raw.decode("utf-8", "replace") if raw is not None else None
    out["owner"] = owner_table([r for r in lines if start is not None and start <= r["_t"] <= end], task, log)
    close = dict(rc) if rc else {}
    close.pop("epoch", None)
    for r in closes:
        reason, cmd = str(r.get("reason") or ""), r.get("command")
        if reason == "stale close owner-ok":
            close["owner_ok"] = cmd
        elif reason == "stale close bound":
            close["bound"] = cmd
        elif reason.startswith("stale close no judge"):
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
    return doc if isinstance(doc, dict) and isinstance(doc.get("closures"), list) else None


def write_closure(lane: Path, task_dir: Path, task: str, budget: float = BUDGET_S) -> "Path | None":
    """Compose this closure and keep it in `<task>/numbers.json` (`closures[]`, one per close, keyed by the receipt's
    stamp). Under the task lock, and only while the task's newest receipt is the one composed for — a close that paused
    before writing never replaces a later close's numbers (plan panel, codex). None when nothing was written."""
    from tasks.atomic import atomic_write
    from tasks.filelock import task_lock
    task_dir = Path(task_dir)
    closure = compose(lane, task_dir, task, deadline=time.monotonic() + budget)
    if closure.get("snapshot") or not closure.get("closed_at"):
        return None
    path = task_dir / FILE_NAME
    with task_lock(task_dir / "task.md"):
        data = _read_regular(task_dir / "task.md")
        newest = receipt_close(data.decode("utf-8", "replace")) if data is not None else None
        if not newest or newest["stamp"] != closure["closed_at"]:
            return None
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return None
        doc = _read_doc(path) or {"schema": SCHEMA, "task": str(task), "closures": []}
        kept = [c for c in doc["closures"] if isinstance(c, dict) and c.get("closed_at") != closure["closed_at"]]
        kept.append(closure)
        kept.sort(key=lambda c: _iso_epoch(c.get("closed_at")) or 0)
        doc.update(schema=SCHEMA, task=str(task), closures=kept)
        atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return path


def _dur(s) -> str:
    s = int(s or 0)
    if s >= 3600:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


_KINDS = ("agent", "judges", "verify", "owner-idle", "owner-question", "background")


def format_closure(c: dict) -> "list[str]":
    """A closure as a few lines of text."""
    out = []
    w = c.get("window") or {}
    head = ("snapshot up to now — not closed, nothing written" if c.get("snapshot")
            else f"closure {c.get('closed_at')}")
    out.append(f"{head}  (window {w.get('start', '?')} → {w.get('end', '?')})")
    t = c.get("time")
    if t:
        bk = t.get("by_kind") or {}
        parts = [f"{k} {_dur(bk[k])}" for k in _KINDS if bk.get(k)]
        parts += [f"{k} {_dur(t[k])}" for k in ("slept", "away", "unknown") if t.get(k)]
        out.append(f"  time {_dur(t.get('total'))} in {t.get('sessions', 0)} session(s): " + (" · ".join(parts) or "—"))
        secs = {s: sum(d.values()) for s, d in (t.get("by_section") or {}).items()}
        if secs:
            out.append("  by section: " + " · ".join(f"{s} {_dur(v)}" for s, v in secs.items()))
    else:
        out.append("  time: no activation record in the journal for this closure")
    r = c.get("rounds") or {}
    bits = [f"{p.get('mode')} panel {p.get('outcome')} (C{p.get('critical')} I{p.get('important')})"
            for p in r.get("panels") or []]
    bits += [f"post-panel {p.get('verdict')} (C{p.get('critical')} I{p.get('important')})" for p in r.get("post_panel") or []]
    if r.get("single"):
        bits.append(f"single judge ×{r['single']}")
    if r.get("tail_cert"):
        bits.append(f"tail-cert ×{r['tail_cert']}")
    out.append("  rounds: " + (" · ".join(bits) or "none") + f"  — judges' wall time {_dur(c.get('judges_wall'))}")
    for seat, s in sorted((c.get("seats") or {}).items()):
        out.append(f"  seat {seat}: {s['runs']} run(s), {s['failed']} failed, {_dur(s['seconds'])}, "
                   f"in {s['in']} out {s['out']} cache {s['cache_read']}/{s['cache_write']}"
                   + (f", {s['unknown']} without usage" if s.get("unknown") else ""))
    v = c.get("verify") or {}
    out.append(f"  verify: {v.get('runs', 0)} run(s), {v.get('reused', 0)} reused, {v.get('failed', 0)} failed, "
               f"{_dur(v.get('seconds'))}")
    o = c.get("owner") or {}
    out.append(f"  owner: {o.get('prompts', 0)} prompt(s), {o.get('dialogs', 0)} dialog(s) "
               f"({o.get('questions', 0)} question(s)) answered in {_dur(o.get('wait'))}, {o.get('cancelled', 0)} cancelled")
    cl = c.get("close") or {}
    if cl:
        bits = [str(cl.get("freshness") or "no panel check")]          # a forced close's receipt has no freshness line
        for k, label in (("forced_reason", "forced"), ("accepted_reason", "accepted"), ("owner_ok", "owner-ok"),
                         ("stop_rule", "no judge after the last round")):
            if cl.get(k):
                bits.append(f"{label}: {cl[k]}")
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
        try:
            last = _iso_epoch(doc["closures"][-1].get("closed_at")) or 0
            late = sum(1 for r in read_enforcement(lane, time.monotonic() + BUDGET_S)
                       if str(r.get("task", "")) == task and r["hook"] in ("review", "verify") and r["_t"] > last)
            if late:
                print(f"  ({late} review / verify record(s) written after the last close are in no closure)")
        except OutOfTime:
            pass
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
