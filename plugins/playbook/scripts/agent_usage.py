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

import json
import os
import stat

_KEYS = (("in", "input_tokens"), ("out", "output_tokens"), ("cache_read", "cache_read_input_tokens"),
         ("cache_write", "cache_creation_input_tokens"))
_MODEL_MAX = 80          # a model name longer than this is cut (it is the one string that leaves the reader)


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
    mid = mid if isinstance(mid, str) else ""
    model = msg.get("model")
    model = model[:_MODEL_MAX] if isinstance(model, str) and model else "?"
    if model == "<synthetic>":
        return last
    if mid and mid in seen:
        return mid
    counts = _counts(msg.get("usage"))
    if counts is None:
        out["unreadable"] += 1
        return last
    if mid:
        seen.add(mid)
    bucket = out["sidechain" if rec.get("isSidechain") is True else "models"].setdefault(model, _blank())
    bucket["calls"] += 1
    for k, v in counts.items():
        bucket[k] += v
    return mid or last


if __name__ == "__main__":                       # a manual read: agent_usage.py <transcript> [offset] [last_id]
    import sys
    a = sys.argv[1:]
    print(json.dumps(read_span(a[0], int(a[1]) if len(a) > 1 else 0, a[2] if len(a) > 2 else ""), indent=1))
