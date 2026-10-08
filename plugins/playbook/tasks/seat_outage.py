"""Seat outages: a judge seat out of credit is not called again until it can work.

PLAN S12b item 1 (task 146 F1/F15/F21): a panel seat whose provider says the
account is out of credit — grok's `402 Payment Required … usage balance
exhausted`, codex's `You've hit your usage limit … try again at <time>`, agy's
quota stop — failed every later panel the same way, and the panel shrank
silently. Such a failure is now recorded in `<agent dir>/journal/seat-outages.json`
(machine-local account state, gitignored with the journal):
until the reset time when the message gives one (codex, agy), else until the
owner clears it (`tasks models enable <seat>`; grok gives no time). The next
panels skip a seat whose outage is current and say so in one line.

The record is written through `atomic_write` under a lock, and every write
re-reads it inside the lock, so two panels recording at once keep both entries.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Optional

RECORD_NAME = "seat-outages.json"
# Only the provider's OWN error is read (impl panels r1 opus, r2 codex ×2): a
# failed block also carries up to 2000 characters of the seat's stdout, which for
# a judge that grepped this repository holds quotes of these very messages. From
# stdout only the CLI's structured error events count (a judge's own words arrive
# escaped inside other events, never as a raw line); the stderr tail is the
# provider's, read whole (its error can span lines — r2 agy).
# A clock time already passed by less than this is the reset that already
# happened, not tomorrow's (r2 opus: the panel classifies when it ends, after
# the seat failed).
_RECENT_PAST = _dt.timedelta(hours=6)


def _record(agent_dir: Path) -> Path:
    return Path(agent_dir) / "journal" / RECORD_NAME

# The texts below are the providers' own, captured live (task 146's plan panel,
# 2026-10-07: both codex seats and grok; task 111's agy exam).
_GROK_OUT = ("402 payment required", "usage balance exhausted")
_CODEX_OUT = re.compile(r"you(?:'|’)ve hit your usage limit", re.I)
_CODEX_AT = re.compile(r"try again at ([^.\"\n]+?)(?:\.|\"|\n|$)", re.I)
_AGY_OUT = re.compile(r"agy quota exhausted", re.I)
_AGY_IN = re.compile(r"resets in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", re.I)


def _parse_codex_time(raw: str, now: _dt.datetime) -> Optional[_dt.datetime]:
    """`7:33 PM` (the next such local time) or `Oct 10th, 2026 12:11 AM`."""
    raw = raw.strip()
    try:
        t = _dt.datetime.strptime(raw.upper(), "%I:%M %p")
        at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if at > now and now - (at - _dt.timedelta(days=1)) <= _RECENT_PAST:
            return at - _dt.timedelta(days=1)   # 23:55 seen at 00:10: yesterday's, passed (post-D6 run 1)
        if at > now or now - at <= _RECENT_PAST:
            return at
        return at + _dt.timedelta(days=1)
    except ValueError:
        pass
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw)
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p"):
        try:
            return _dt.datetime.strptime(cleaned, fmt).replace(tzinfo=now.tzinfo)
        except ValueError:
            continue
    return None


# Providers whose stdout is the CLI's own JSON event stream: a raw error-event line
# there is the CLI speaking. A claude seat's stdout is the judge's review text,
# where such a line could only be a quote (post-D6 run 1, codex).
_EVENT_STREAM_PROVIDERS = ("codex", "grok", "agy")


def classify_outage(output_text: str, now: Optional[_dt.datetime] = None,
                    provider: Optional[str] = None) -> Optional[dict]:
    """A FAILED seat's output → {"reason", "until"} when the provider says the
    account is out of credit, else None. `until` is an ISO local time, or None
    when the provider gives none (the owner clears it). Only a failure-marked
    text is read: a review that QUOTES these messages is not an outage."""
    text = output_text or ""
    if not text.lstrip().startswith(("(FAILED", "(error")):
        return None
    now = now or _dt.datetime.now().astimezone()
    lines = text.lstrip().splitlines()
    kept, section = lines[:1], ""
    for ln in lines[1:]:
        if ln.strip() in ("[stdout tail]", "[stderr tail]"):
            section = ln.strip()
            continue
        if section == "[stderr tail]":
            kept.append(ln)
        elif provider is None or provider in _EVENT_STREAM_PROVIDERS:
            s = ln.strip()
            if s.startswith("AGY_ERROR:"):
                kept.append(ln)
            elif s.startswith("{"):
                # an event is decoded, not matched byte for byte (post-D6 run 2,
                # codex: spacing and key order may differ)
                try:
                    ev = json.loads(s)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("type") in ("error", "turn.failed"):
                    kept.append(json.dumps(ev, ensure_ascii=False))
    text = "\n".join(kept)
    low = text.lower()
    if any(s in low for s in _GROK_OUT):
        return {"reason": "grok: usage balance exhausted (402)", "until": None}
    if _CODEX_OUT.search(text):
        m = _CODEX_AT.search(text)
        at = _parse_codex_time(m.group(1), now) if m else None
        return {"reason": "codex: usage limit reached"
                + (f" (try again at {m.group(1).strip()})" if m else ""),
                "until": at.isoformat(timespec="minutes") if at else None}
    if _AGY_OUT.search(text):
        m = _AGY_IN.search(text)
        if m and any(m.groups()):
            h, mi, s = (int(x or 0) for x in m.groups())
            at = now + _dt.timedelta(hours=h, minutes=mi, seconds=s)
            return {"reason": "agy: quota exhausted", "until": at.isoformat(timespec="seconds")}
        return {"reason": "agy: quota exhausted", "until": None}
    return None


def _load(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _is_current(entry: dict, now: _dt.datetime) -> bool:
    until = entry.get("until")
    if not until:
        return True                       # no reset time: until the owner clears it
    try:
        at = _dt.datetime.fromisoformat(until)
    except ValueError:
        return True                       # unreadable: keep skipping, visibly
    if at.tzinfo is None:
        at = at.replace(tzinfo=now.tzinfo)
    return at > now


def current_outages(agent_dir: Path, now: Optional[_dt.datetime] = None) -> dict:
    """seat spec → its outage entry, for the outages still in force."""
    now = now or _dt.datetime.now().astimezone()
    try:
        text = _record(agent_dir).read_text(encoding="utf-8")
    except OSError:
        return {}
    return {k: v for k, v in _load(text).items() if _is_current(v, now)}


def record_outage(agent_dir: Path, spec: str, outage: dict,
                  now: Optional[_dt.datetime] = None) -> None:
    """Add or replace `spec`'s entry (expired entries are dropped on the way)."""
    from tasks.atomic import rewrite
    now = now or _dt.datetime.now().astimezone()

    def _t(text: str) -> str:
        data = {k: v for k, v in _load(text).items() if _is_current(v, now)}
        data[spec] = {"reason": outage["reason"], "until": outage.get("until"),
                      "since": now.isoformat(timespec="minutes")}
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

    _record(agent_dir).parent.mkdir(parents=True, exist_ok=True)
    rewrite(_record(agent_dir), _t)


def clear_outage(agent_dir: Path, spec: str) -> bool:
    """Remove `spec`'s entry (the owner's `tasks models enable`). True if one was there."""
    from tasks.atomic import rewrite
    found = []

    def _t(text: str) -> "str | None":
        data = _load(text)
        if spec not in data:
            return None
        found.append(data.pop(spec))
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

    if _record(agent_dir).exists():
        rewrite(_record(agent_dir), _t)
    return bool(found)
