#!/usr/bin/env python3
"""agent_usage.py — the main agent's token usage, read from the host's session transcript (task 244, PHASE-B item 6).

The owner, 2026-10-18 (dialog): "Da, după 243 (Recomandat)" — every closure's numbers carry the main agent's own
tokens, not only the judge seats' (task 232 records those). Only the host's session transcript holds them: one
`type: "assistant"` entry per content block of an API call, each repeating that call's `message.usage` (measured on a
1 GB transcript, 2026-10-18: 926 of 1,227 messages had several entries, their usage identical, the entries of one id
contiguous). The host deletes its transcripts after `cleanupPeriodDays` (30 by default), so the plugin records the
counts as they happen (`agent-usage-hook`, the close) and never reads a transcript twice: each read starts at the
offset the previous one ended at.

`read_span(path, offset, last_id, stop=None)` reads `path` from byte `offset` to its last complete line (or to `stop`,
for tests) and returns:

  * `status` — "known", or "unknown" with a `reason` when the file cannot be read (missing, not a regular file, a link,
    an error): never zero counts for what was not read;
  * `models` / `sidechain` — per model, `calls`, `in`, `out`, `cache_read`, `cache_write`, `thinking`; one call counted
    once however many entries carry it (by `message.id`; the id last counted before this span is `last_id`, skipped,
    since one call's entries are contiguous and may straddle two reads); `<synthetic>` entries are no call;
    `isSidechain` entries (a subagent's) count apart;
  * `offset` — where the next read starts (after the last newline read); `last_id` — the last id counted or carried;
  * `restarted` — the file was shorter than `offset` (replaced or cut): read again from 0;
  * `unreadable` — assistant entries whose usage could not be read as whole numbers (never counted as 0);
    `bad_lines` — lines that name an assistant entry (`"assistant"` in their bytes) yet are not JSON objects. Only such
    a line could hide a call: the others are not parsed at all — the host's `api-request` and tool-result lines run to
    hundreds of KB each, and parsing every line made a 20 MB span (86 calls) take 223 ms instead of 9 ms (measured
    2026-10-18; the same 189 assistant entries either way).

Nothing of the conversation leaves this module but the model's name. Stdlib only; never follows a link, never opens a
FIFO (the numbers reader's rule).
"""
from __future__ import annotations

import errno
import fcntl
import glob
import hashlib
import json
import os
import stat
import time

_KEYS = (("in", "input_tokens"), ("out", "output_tokens"), ("cache_read", "cache_read_input_tokens"),
         ("cache_write", "cache_creation_input_tokens"))
_MODEL_MAX = 80          # a model name longer than this is cut (it is the one string that leaves the reader)
_ID_MAX = 48             # an id longer than this is carried as its hash (the record's `last_id` is capped at 48)
FRESH_MAX = 8 * 1024 * 1024     # a file with no record is read from 0 up to this size; larger, it gets a seed
_SCAN_MAX = 32 * 1024 * 1024    # the backward scan of the journal for a file's newest record stops here


def _blank() -> dict:
    return {"calls": 0, "in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "thinking": 0}


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _counts(usage) -> "dict | None":
    """The call's counts, or None when any of the four is not a whole number (then the entry is unreadable)."""
    if not isinstance(usage, dict):
        return None
    got = {}
    for k, src in _KEYS:
        v = usage.get(src)
        if not _whole(v):
            return None
        got[k] = v
    det = usage.get("output_tokens_details")
    t = det.get("thinking_tokens") if isinstance(det, dict) else None
    got["thinking"] = t if _whole(t) else 0
    return got


def idkey(mid: str) -> str:
    """The form an id is carried in: itself, or `h:` + 16 hex of its sha1 when longer than the record keeps."""
    return mid if len(mid) <= _ID_MAX else "h:" + hashlib.sha1(mid.encode("utf-8", "surrogatepass")).hexdigest()[:16]


HEAD_MAX = 4096          # the bytes of a file's start its records fingerprint (`head`)


def head_of(path: str, n: int) -> str:
    """8 hex of the sha1 of the file's first `n` bytes (at most HEAD_MAX), or "" when it cannot be read. A transcript
    only grows at its end, so its start never changes: a record's `head`, taken over min(HEAD_MAX, its `to`) bytes, tells
    the same file from one put in its place even when the system gives the new file the old inode number (Linux reuses
    a freed inode at once — measured by task 244's test)."""
    n = max(0, min(int(n), HEAD_MAX))
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        data = os.read(fd, n) if n else b""
        return hashlib.sha1(data).hexdigest()[:8] if len(data) == n else ""
    finally:
        os.close(fd)


def file_key(path: str) -> str:
    """A transcript file's identity in the journal: 16 hex of its absolute path's sha1 (with the record's `ino`)."""
    return hashlib.sha1(os.path.abspath(path).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def read_span(path: str, offset: int = 0, last_id: str = "", stop: "int | None" = None) -> dict:
    out = {"status": "unknown", "reason": "", "models": {}, "sidechain": {}, "offset": max(int(offset or 0), 0),
           "last_id": str(last_id or ""), "restarted": False, "unreadable": 0, "bad_lines": 0}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError as e:
        out["reason"] = f"cannot open the transcript ({e.strerror or e})"
        return out
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            out["reason"] = "the transcript is not a regular file"
            return out
        start = out["offset"]
        if st.st_size < start:                  # replaced or cut since the last read: from the start again, said
            start, out["restarted"], out["last_id"] = 0, True, ""
        end = st.st_size if stop is None else min(st.st_size, max(int(stop), start))
        os.lseek(fd, start, os.SEEK_SET)
        pos, carry = start, b""
        seen = {out["last_id"]} if out["last_id"] else set()
        last = out["last_id"]
        while pos < end:
            chunk = os.read(fd, min(1 << 20, end - pos))
            if not chunk:
                break
            pos += len(chunk)
            buf = carry + chunk
            *whole, carry = buf.split(b"\n")
            for raw in whole:
                last = _take(raw, out, seen, last)
            out["offset"] = pos - len(carry)
        out["last_id"] = last
        out["status"] = "known"
        return out
    except OSError as e:
        out["reason"] = f"cannot read the transcript ({e.strerror or e})"
        out["status"] = "unknown"
        return out
    finally:
        os.close(fd)


def _take(raw: bytes, out: dict, seen: set, last: str) -> str:
    """Count one complete line; return the id last counted or carried."""
    if b'"assistant"' not in raw:              # not an assistant entry: never parsed (see `bad_lines`)
        return last
    try:
        rec = json.loads(raw)
    except ValueError:
        out["bad_lines"] += 1
        return last
    if not isinstance(rec, dict):
        out["bad_lines"] += 1
        return last
    if rec.get("type") != "assistant":
        return last
    msg = rec.get("message")
    if not isinstance(msg, dict):
        out["unreadable"] += 1
        return last
    mid = msg.get("id")
    mid = idkey(mid) if isinstance(mid, str) and mid else ""
    model = msg.get("model")
    model = model[:_MODEL_MAX] if isinstance(model, str) and model else "?"
    if model == "<synthetic>":
        return last
    if not mid:                      # no id: one call or several cannot be told (impl round 1, sonnet) — never guessed
        out["unreadable"] += 1
        return last
    if mid in seen:
        return mid
    seen.add(mid)                    # read or not, this call is done: its other blocks are not counted (impl round 2, agy)
    counts = _counts(msg.get("usage"))
    if counts is None:
        out["unreadable"] += 1
        return mid
    bucket = out["sidechain" if rec.get("isSidechain") is True else "models"].setdefault(model, _blank())
    bucket["calls"] += 1
    for k, v in counts.items():
        bucket[k] += v
    return mid


# --- recording: the journal is the read position (design v2, plan round 1) --------------------------------------------

def _newest(journal: str, keys: "set[str]", scan_max: int = _SCAN_MAX) -> "tuple[dict[str, dict], bool]":
    """Each file key's newest `agent-usage` record in `journal`, read backwards in blocks (at most `scan_max` bytes),
    and whether the scan reached the journal's start: ({key: record}, complete). A key absent from a COMPLETE scan has
    no record; absent from a cut one, it may have one further back (impl round 1, sonnet / opus) — the caller seeds it.
    A missing journal is complete and empty; one it cannot read is incomplete. Never follows a link."""
    found: "dict[str, dict]" = {}
    try:
        fd = os.open(journal, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return found, True
    except OSError:
        return found, False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return found, False
        pos = os.lseek(fd, 0, os.SEEK_END)
        scanned, tail = 0, b""
        while pos > 0 and scanned < scan_max and len(found) < len(keys):
            n = min(1 << 16, pos, max(scan_max - scanned, 1))
            pos -= n
            os.lseek(fd, pos, os.SEEK_SET)
            block = os.read(fd, n) + tail
            scanned += n
            parts = block.split(b"\n")
            tail = parts[0] if pos > 0 else b""
            for raw in reversed(parts[1:] if pos > 0 else parts):
                if b'"agent-usage"' not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("hook") != "agent-usage":
                    continue                    # an odd line never stops the recorder (impl round 1, opus / sonnet)
                k = rec.get("file")
                if isinstance(k, str) and k in keys and k not in found:
                    found[k] = rec
        return found, pos == 0 or len(found) == len(keys)
    finally:
        os.close(fd)


def _lock(lane: str, wait: float):
    """The lane's agent-usage lock (`journal/agent-usage.lock`, flock), or None when it cannot be taken in `wait`."""
    jdir = os.path.join(lane, "journal")
    try:
        if os.path.islink(jdir):                # a linked journal dir would put the lock outside the lane
            return None
        os.makedirs(jdir, exist_ok=True)        # inside the existing lane, as `pb_journal._write_record` does
        fd = os.open(os.path.join(jdir, "agent-usage.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                     0o600)
    except OSError:
        return None
    end = time.monotonic() + max(wait, 0)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() >= end:
                os.close(fd)
                return None
            time.sleep(0.02)


def session_files(paths: "list[str]") -> "list[str]":
    """The main transcripts given (the payload's, the session's pointer) and each one's subagents' files
    (`<main without .jsonl>/subagents/agent-*.jsonl`), each once, in a stable order."""
    out: "list[str]" = []
    for p in paths:
        if not p or not isinstance(p, str):
            continue
        p = os.path.abspath(p)
        if p not in out:
            out.append(p)
        if p.endswith(".jsonl"):
            for sub in sorted(glob.glob(glob.escape(p[:-6]) + "/subagents/agent-*.jsonl")):
                if sub not in out and not os.path.islink(sub):
                    out.append(sub)
    return out


def record_spans(lane, *, task, paths: "list[str]", session_id: str, pbj, lock_wait: float = 2.0,
                 fresh_max: int = FRESH_MAX, scan_max: int = _SCAN_MAX) -> dict:
    """Read every file of the session from its newest record's `to` and append what it holds, under the lane lock.

    `paths` are the main transcripts (the Stop payload's, the session dir's pointer); their subagents' files are added.
    A file with no record (or another file in its place: inode or `head` differ) is read from 0 when at most
    `fresh_max` bytes, else gets a seed — a record `unknown`, `from = to = size` — so no history is charged to one
    task; so does a file the journal scan could not reach back to (`scan_max`). A span's records (one per model and
    side) go in ONE append; a span with neither calls nor unreadable entries writes nothing. `pbj` is the loaded
    `pb_journal` module. `task` is the task's number, or a callable asked INSIDE the lock (impl round 2, opus: read
    before it, a close holding the lock could leave this span after its `done`); a callable answering None — a
    session not resolved — records nothing (impl round 2, sonnet). Returns {"files", "calls", "skipped"}."""
    lane = str(lane)
    out = {"files": 0, "calls": 0, "skipped": "", "unwritten": 0}
    files = session_files(paths)
    if not files:
        out["skipped"] = "no transcript"
        return out
    fd = _lock(lane, lock_wait)
    if fd is None:
        out["skipped"] = "lock"
        return out
    try:
        if callable(task):
            task = task()
            if task is None:
                out["skipped"] = "the session is not resolved"
                return out
        keys = {file_key(f): f for f in files}
        newest, complete = _newest(os.path.join(lane, "journal", "enforcement.jsonl"), set(keys), scan_max)
        for key, f in keys.items():
            try:
                st = os.lstat(f)
            except OSError:
                continue                       # gone (a subagent file removed): nothing to read
            if not stat.S_ISREG(st.st_mode):
                continue
            prev = newest.get(key)
            same = _same_file(prev, f, st)
            if same:
                start, last = int(prev["to"]), str(prev.get("last_id") or "")
            elif (prev is not None or complete) and st.st_size <= fresh_max:
                start, last = 0, ""            # a new file, or another one in its place: from its start
            else:
                why = ("no earlier record for this transcript: usage before now not counted" if complete or prev
                       else "the journal scan did not reach this transcript's last record: usage before now not counted")
                if pbj.append_agent_usage(lane, task=task, file=key, ino=st.st_ino, span_from=st.st_size,
                                          span_to=st.st_size, head=head_of(f, min(HEAD_MAX, st.st_size)), known=False,
                                          session_id=session_id, why=why) is False:
                    out["unwritten"] += 1
                continue
            r = read_span(f, start, last)
            out["files"] += 1
            if r["status"] != "known":
                continue                       # not readable now: the span stays for a later read
            frm = 0 if r["restarted"] else start
            side_file = "/subagents/" in f
            span = dict(task=task, file=key, ino=st.st_ino, span_from=frm, span_to=r["offset"],
                        head=head_of(f, min(HEAD_MAX, r["offset"])), last_id=r["last_id"], session_id=session_id)
            bad = r["unreadable"] + r["bad_lines"]     # a broken assistant line is unreadable too (impl round 1, codex)
            merged: "dict[tuple, dict]" = {}          # per (model, final side): a subagent file's two buckets are one
            for side, bucket in ((False, r["models"]), (True, r["sidechain"])):  # side (impl round 2, agy)
                for model, c in bucket.items():
                    if c["calls"]:
                        m = merged.setdefault((model, bool(side or side_file)), {k: 0 for k in c})
                        for k, v in c.items():
                            m[k] += v
            items = []
            span_calls = 0
            for (model, side), c in sorted(merged.items()):
                items.append(dict(span, model=model, side=side, calls=c["calls"], counts=c))
                span_calls += c["calls"]
            if bad:
                if items:
                    items[0]["unreadable"] = bad
                else:
                    items.append(dict(span, model="?", side=side_file, calls=0, counts={}, unreadable=bad))
            if not items and not same:
                # a file's first read found no call: one zero-call record, so later scans find its position instead of
                # searching the whole journal for it at every Stop (impl round 2, opus)
                items.append(dict(span, model="?", side=side_file, calls=0, counts={}))
            if items:
                # one write: the span's `to` lands with all its models; a span counts as recorded only once it landed
                if pbj.append_agent_usage_span(lane, items) is False:
                    out["unwritten"] += 1
                else:
                    out["calls"] += span_calls
        return out
    finally:
        os.close(fd)


_SESSION_RE = __import__("re").compile(r"[A-Za-z0-9._-]+")


def _pointer(lane: str, session: str) -> str:
    """The session dir's `transcript_path` (session-start-hook writes it, the state echo refreshes it), or ""."""
    if not session or not _SESSION_RE.fullmatch(session) or session in (".", ".."):
        return ""
    path = os.path.join(lane, "sessions", session, "transcript_path")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        line = os.read(fd, 4096).split(b"\n", 1)[0].strip()
        return line.decode("utf-8", "surrogateescape") if line.startswith(b"/") else ""
    finally:
        os.close(fd)


def load_journal():
    """pb_journal beside this file, loaded by its path (the hooks' and the CLI's idiom)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_pb_journal_agent_usage",
                                                  os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                               "pb_journal.py"))
    if spec is None or spec.loader is None:
        raise ImportError("pb_journal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def paths_of(payload: dict, lane: str, session: str) -> "list[str]":
    """The session's main transcripts: the payload's `transcript_path`, then the session dir's pointer."""
    tp = payload.get("transcript_path")
    return [tp if isinstance(tp, str) else "", _pointer(lane, session)]


def _same_file(prev, f: str, st) -> bool:
    """The recorder's identity check: the newest record `prev` was taken on this very file — its inode, and the hash of
    its first min(HEAD_MAX, `to`) bytes (a transcript only grows at its end, so another file in its place differs in
    one of them)."""
    p_to = prev.get("to") if prev else None
    return (prev is not None and str(prev.get("ino")) == str(st.st_ino)
            and isinstance(p_to, int) and not isinstance(p_to, bool)
            and prev.get("head") == head_of(f, min(HEAD_MAX, p_to)))


def _unread(lane: str, paths: "list[str]") -> bool:
    """Whether any of the session's files holds a span the recorder would read — no record, another file in its place
    (`_same_file`, task 247: the size alone took a replaced transcript no larger than the old `to` for read), or bytes
    past the newest record's `to` — read without the lock, for the busy-lock marker only: a clean exit whose last Stop
    read everything is no gap (task 246's impl round 2, opus)."""
    files = session_files(paths)
    if not files:
        return False
    keys = {file_key(f): f for f in files}
    newest, _complete = _newest(os.path.join(lane, "journal", "enforcement.jsonl"), set(keys))
    for key, f in keys.items():
        try:
            st = os.lstat(f)
        except OSError:
            continue                           # gone: nothing the recorder would read
        if not stat.S_ISREG(st.st_mode):
            continue
        prev = newest.get(key)
        if not _same_file(prev, f, st) or st.st_size > prev["to"]:
            return True
    return False


def hook_main(payload_text: str) -> str:
    """The recorder (`agent-usage-hook`, Stop): one line saying what it recorded or why not."""
    try:
        payload = json.loads(payload_text) if payload_text.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    lane, session = os.environ.get("PB_AU_LANE", ""), os.environ.get("PB_AU_SESSION", "")
    if not lane or not os.path.isdir(lane):
        return "skipped: no lane"
    pbj = load_journal()
    from pathlib import Path
    seen_task: list = []

    def task():
        # asked inside the lane lock; None (a session not resolved) records nothing — the span stays for a reader
        # that knows its session (a CLI transition, the close)
        t = pbj._hj_session_task(Path(lane), session)
        seen_task.append(t)
        return None if t is None else (t or "-")
    res = record_spans(lane, task=task, paths=paths_of(payload, lane, session), session_id=session, pbj=pbj,
                       lock_wait=2.0)
    if res["skipped"] == "lock" and os.environ.get("PB_AU_MARK_BUSY") == "1" and not _unread(lane, paths_of(payload, lane, session)):
        return "skipped: lock (nothing unread since the last record: no marker)"
    if res["skipped"] == "lock" and os.environ.get("PB_AU_MARK_BUSY") == "1":
        # task 246 (impl round 1, codex): at the session's end there is no later reader of this transcript — the dir
        # and its pointer go next, and after a `/clear` SessionStart points the session at the new transcript, which
        # the next Stop's payload names too (task 247: every end reason) — so a busy lock leaves an `unknown` marker
        # for the session's task (a marker holds no position: the lock is not needed for it). A Stop's skip needs
        # none: its next Stop reads the span.
        t = pbj._hj_session_task(Path(lane), session)
        if t is None:
            return "skipped: lock (the session is not resolved: no marker)"
        marked = pbj.append_agent_usage(lane, task=t or "-", known=False, session_id=session,
                                        why="the lane lock was busy at the session's end: its last span was not counted")
        return "skipped: lock (" + ("marked unknown" if marked else "the unknown marker could not be written") + ")"
    if res["skipped"]:
        return f"skipped: {res['skipped']}"
    said = f"recorded {res['calls']} call(s) from {res['files']} file(s) for task {(seen_task or ['-'])[0] or '-'}"
    if res.get("unwritten"):
        said += f"; {res['unwritten']} span(s) NOT written (the journal refused them)"
    return said


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a[:1] == ["hook"]:
        print(hook_main(sys.stdin.read()))
    elif a[:1] == ["read"] and len(a) > 1:          # a manual read: agent_usage.py read <transcript> [offset] [last_id]
        print(json.dumps(read_span(a[1], int(a[2]) if len(a) > 2 else 0, a[3] if len(a) > 3 else ""), indent=1))
    else:
        print("usage: agent_usage.py hook < payload | read <transcript> [offset] [last_id]", file=sys.stderr)
        sys.exit(2)
