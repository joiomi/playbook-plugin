"""Seat outages: a judge seat out of credit is not called again until it can work.

PLAN S12b item 1 (task 146 F1/F15/F21): a panel seat whose provider says the
account is out of credit — grok's `402 Payment Required … usage balance
exhausted`, codex's `You've hit your usage limit … try again at <time>`, agy's
quota stop — failed every later panel the same way, and the panel shrank
silently. Such a failure is now recorded in `<agent dir>/journal/seat-outages.json`
(machine-local account state, gitignored with the journal). When the message
gives a reset time (codex, agy, claude) the next panels skip the seat until then
and say so in one line (`tasks models enable <seat>` clears it sooner). When it
gives none (grok, codex's workspace out of credits) every panel tries the seat
again (task 276): a free refusal counts the try, an answer removes the entry —
`split_outages`, `record_outage(retried=True)`, `clear_answered`.

The record is written through `atomic_write` under a lock, and every write
re-reads it inside the lock, so two panels recording at once keep both entries.

A clear leaves its time behind (task 165): a panel whose call to a seat was already
under way when the owner cleared that seat reports the failure afterwards, and
without the time it put the seat straight back.
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
# a codex WORKSPACE out of credits gives no reset time (task 177's capture, 2026-10-10; task 203, #34)
_CODEX_CREDITS = re.compile(r"your workspace is out of credits", re.I)
_AGY_OUT = re.compile(r"agy quota exhausted", re.I)
_AGY_IN = re.compile(r"resets in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", re.I)
# claude prints its account limit as ONE plain line on stdout and exits 1 (both
# claude seats of task 156's panel, 2026-10-08: "You've hit your weekly limit ·
# resets Oct 13, 6pm (Europe/Bucharest)").
from provider.usage import CLAUDE_LIMIT_LINE as _CLAUDE_OUT  # noqa: E402 — one pattern, kept in the provider layer (task 269)
_CLAUDE_AT = re.compile(r"^(?:([A-Za-z]{3,9}) (\d{1,2}), )?(\d{1,2})(?::(\d{2}))?\s*(am|pm)"
                        r"(?: \(([A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*)\))?$", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _nearest_clock_time(at: _dt.datetime, now: _dt.datetime) -> _dt.datetime:
    """A bare clock time, placed on today's date: the next such time — unless it
    passed less than _RECENT_PAST ago, then it is the reset that already happened."""
    if at > now and now - (at - _dt.timedelta(days=1)) <= _RECENT_PAST:
        return at - _dt.timedelta(days=1)   # 23:55 seen at 00:10: yesterday's, passed (post-D6 run 1)
    if at > now or now - at <= _RECENT_PAST:
        return at
    return at + _dt.timedelta(days=1)


def _parse_claude_time(raw: str, now: _dt.datetime) -> Optional[_dt.datetime]:
    """`Oct 13, 6pm (Europe/Bucharest)`; without a date, the nearest such clock
    time. An unknown zone (or a host without the zone database) reads as local."""
    m = _CLAUDE_AT.match(raw.strip())
    if not m:
        return None
    mon, day, hour, minute, ampm, zone = m.groups()
    if not 1 <= int(hour) <= 12 or int(minute or 0) > 59:
        return None
    tz = now.tzinfo
    if zone:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(zone)
        except Exception:
            tz = now.tzinfo
    here = now.astimezone(tz)
    at = here.replace(hour=int(hour) % 12 + (12 if ampm.lower() == "pm" else 0),
                      minute=int(minute or 0), second=0, microsecond=0)
    if mon is None:
        return _nearest_clock_time(at, here)
    month = _MONTHS.get(mon[:3].lower())
    # The line carries no year: take the one that puts the date NEAREST to now —
    # "Jan 2" read on Dec 30 is next year's, "Dec 31, 11:55pm" read ten minutes
    # into Jan 1 is last year's and has passed (impl panel r1, codex-medium).
    candidates = []
    for year in (here.year - 1, here.year, here.year + 1):
        try:
            candidates.append(at.replace(year=year, month=month or 0, day=int(day)))
        except ValueError:                           # no such month, or Feb 29 in that year
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(c - here))


def _parse_codex_time(raw: str, now: _dt.datetime) -> Optional[_dt.datetime]:
    """`7:33 PM` (the next such local time) or `Oct 10th, 2026 12:11 AM`."""
    raw = raw.strip()
    try:
        t = _dt.datetime.strptime(raw.upper(), "%I:%M %p")
        at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        return _nearest_clock_time(at, now)
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
# where such a line could only be a quote (post-D6 run 1, codex) — claude's own
# limit line is read by a narrower rule in classify_outage (task 158).
_EVENT_STREAM_PROVIDERS = ("codex", "grok", "agy")

# What provider/usage.py's judge_output_from_result writes when a codex stream ends
# badly on exit 0: the malformed-output head quotes the stdout it could not parse,
# and the error head is followed by the review's partial text — a review's words,
# never the provider's (task 203 r1, codex).
_MALFORMED_HEAD = "(FAILED — malformed or unrecognized structured judge output:"
_ERROR_HEAD = "(FAILED — the judge CLI reported an error:"
_PARTIAL_TEXT = "[partial text before the failure]"


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
    if lines[0].startswith(_MALFORMED_HEAD):
        return None
    if lines[0].startswith(_ERROR_HEAD):
        lines = lines[:next((i for i, ln in enumerate(lines) if ln.strip() == _PARTIAL_TEXT), len(lines))]
    kept, section, claude_stdout = lines[:1], "", []
    for ln in lines[1:]:
        if ln.strip() in ("[stdout tail]", "[stderr tail]"):
            section = ln.strip()
            continue
        if section == "[stderr tail]":
            kept.append(ln)
        elif provider == "claude":
            if section == "[stdout tail]" and ln.strip():
                claude_stdout.append(ln.strip())
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
    # A claude seat's stdout is the judge's review, so its limit line counts only
    # when it is the WHOLE of it: a review that quotes the line has other lines.
    if len(claude_stdout) == 1:
        m = _CLAUDE_OUT.match(claude_stdout[0])
        if m:
            at = _parse_claude_time(m.group(2), now)
            return {"reason": f"claude: {m.group(1).lower()} limit reached (resets {m.group(2).strip()})",
                    "until": at.isoformat(timespec="minutes") if at else None}
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
    if _CODEX_CREDITS.search(text):
        return {"reason": "codex: workspace out of credits", "until": None}
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


def now() -> _dt.datetime:
    """The clock this module reads — a panel takes each seat's call start from
    here, so that it is compared with a clear's time on one clock."""
    return _dt.datetime.now().astimezone()


def current_outages(agent_dir: Path, now: Optional[_dt.datetime] = None) -> dict:
    """seat spec → its outage entry, for the outages still in force."""
    now = now or _dt.datetime.now().astimezone()
    try:
        text = _record(agent_dir).read_text(encoding="utf-8")
    except OSError:
        return {}
    return {k: v for k, v in _load(text).items() if _is_current(v, now)}


# When the owner cleared a seat (`tasks models enable`), per seat — PLAN S11 item 3,
# task 165: two panels start, the seat fails in the first and is recorded, the owner
# tops the account up and clears it, and the second panel then reports the same old
# failure. The value under this key is a LIST of [seat, time] pairs, not an object:
# `_load` — and the reader shipped in 1.6.0 — keep object values only, so no reader,
# old or new, takes it for a seat out of credit.
# A clear is never dropped: a review's timeout may be unlimited, so no age makes it
# safe to forget one (impl panel r1) — and there is one pair per seat ever cleared.
_CLEARED = "_cleared"


def _aware(at: _dt.datetime, now: _dt.datetime) -> _dt.datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=now.tzinfo)


def _cleared(text: str, now: _dt.datetime) -> dict:
    """seat spec → when the owner last cleared it (anything unreadable is skipped;
    `now` only gives a zone to a time written without one)."""
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    pairs = data.get(_CLEARED) if isinstance(data, dict) else None
    out = {}
    for pair in pairs if isinstance(pairs, list) else ():
        if not (isinstance(pair, list) and len(pair) == 2
                and all(isinstance(x, str) for x in pair)):
            continue
        try:
            out[pair[0]] = _aware(_dt.datetime.fromisoformat(pair[1]), now)
        except ValueError:
            continue
    return out


def _dump(entries: dict, cleared: dict) -> str:
    data = dict(entries)
    if cleared:
        data[_CLEARED] = sorted([seat, at.isoformat()] for seat, at in cleared.items())
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def split_outages(outages: dict) -> "tuple[dict, dict]":
    """(skipped, retried) — task 276: an outage with a reset time ahead is skipped until then; one with none
    (grok's 402, codex's workspace out of credits) is tried again by every panel, which is how it comes back
    without `tasks models enable`."""
    skipped = {k: v for k, v in outages.items() if v.get("until")}
    return skipped, {k: v for k, v in outages.items() if k not in skipped}


def record_outage(agent_dir: Path, spec: str, outage: dict,
                  now: Optional[_dt.datetime] = None, *,
                  started: Optional[_dt.datetime] = None, retried: bool = False,
                  keep_current: bool = False) -> bool:
    """Add or replace `spec`'s entry (expired entries are dropped on the way).
    True when it was recorded.

    `started` is when the CALL that failed began — the seat's own, not the panel's
    (impl panel r1: a seat enabled after the panel chose its seats but before it was
    called has failed AFTER the clear, and that is news). A failure of a call that
    began no later than the owner's last clear of this seat is from before that
    clear — it is not recorded (False), and the next panel calls the seat. Without
    `started` the entry is recorded, as it always was.

    `retried` (task 276): the call was a panel's new try of a seat recorded with no reset time, and it was
    refused again. Its entry keeps the time it has been out since, counts the try (`tried`, `last_tried` = the
    call's start) and takes the reset time the refusal names, if any."""
    from tasks.atomic import rewrite
    now = now or _dt.datetime.now().astimezone()

    def _t(text: str) -> "str | None":
        cleared = _cleared(text, now)
        at = cleared.get(spec)
        if started is not None and at is not None and _aware(started, now) <= at:
            return None
        data = {k: v for k, v in _load(text).items() if _is_current(v, now)}
        # full precision (impl r1, codex): `clear_answered` compares these with a call's start, which has it
        entry = {"reason": outage["reason"], "until": outage.get("until"), "since": now.isoformat()}
        old = data.get(spec)
        tries = old.get("tried") if old else 0
        tries = tries if isinstance(tries, int) and not isinstance(tries, bool) and tries > 0 else 0
        began = _aware(started or now, now)
        seen = [at for at in (_parse_at(old.get("since"), now), _parse_at(old.get("last_tried"), now))
                if at is not None] if old else []
        if keep_current:
            # task 277 release round 1 (codex): a retried seat failed for another reason — no news about credit. Only
            # the try is counted, on the entry as it is now: its reason and reset time stay (another panel's newer
            # refusal may have set them while this call ran); with no entry left (cleared meanwhile) nothing is written.
            if not old:
                return None
            current: dict = dict(old)
            current["tried"] = tries + 1
            current["last_tried"] = max(seen + [began]).isoformat()
            data[spec] = current
            return _dump(data, cleared)
        if old and started is not None and seen and max(seen) > began:
            # impl r1 (opus, codex), post-panel run 1 (codex): an OLDER call's failure — a retried seat's or a live
            # one's — landing after newer evidence of this seat (recorded, or refused, after this call began) changes
            # nothing but the count of tries: the newer reset time and reason stay, or a later answer could clear
            # newer evidence and the next panels would try the seat before its reset
            newer: dict = dict(old)
            if retried:
                newer["tried"] = tries + 1
            data[spec] = newer
            return _dump(data, cleared)
        if retried:
            entry["tried"] = tries + 1
            entry["last_tried"] = began.isoformat()
            if old and isinstance(old.get("since"), str):
                entry["since"] = old["since"]
        data[spec] = entry
        return _dump(data, cleared)

    _record(agent_dir).parent.mkdir(parents=True, exist_ok=True)
    return rewrite(_record(agent_dir), _t) is not None


def _parse_at(raw, now: _dt.datetime) -> "_dt.datetime | None":
    """An entry's ISO time, zone-aware; None when absent or unreadable."""
    if not isinstance(raw, str):
        return None
    try:
        return _aware(_dt.datetime.fromisoformat(raw), now)
    except ValueError:
        return None


def clear_answered(agent_dir: Path, spec: str, started: _dt.datetime,
                   now: Optional[_dt.datetime] = None) -> str:
    """A panel's new try of a seat recorded out of credit was ANSWERED (task 276): the account works again.
    Remove the entry only if no newer evidence is in it: it was recorded (`since`) and last refused
    (`last_tried`, a parallel panel's later try) no later than this call began — a newer record or refusal
    stays (plan panel, codex: a refusal keeps the old `since`, so `since` alone let an older answer remove it).
    The clear is dated at the call's START, not now: a failure of a call that began after it is news and is
    recorded (`record_outage`). Returns `removed`, `kept` (newer evidence is in the entry) or `absent` (nothing
    was recorded — the owner's `enable` or another panel's answer came first)."""
    from tasks.atomic import rewrite
    now = now or _dt.datetime.now().astimezone()
    begun = _aware(started, now)
    found = []

    def _t(text: str) -> str:
        data = _load(text)
        entry = data.get(spec)
        if entry is not None:
            # an unreadable time is no evidence: the answer is the newer fact
            newer = [at for at in (_parse_at(entry.get("since"), now), _parse_at(entry.get("last_tried"), now))
                     if at is not None and at > begun]
            found.append("kept" if newer else "removed")
            if not newer:
                data.pop(spec)
        cleared = _cleared(text, now)
        cleared[spec] = max(cleared.get(spec, begun), begun)
        return _dump(data, cleared)

    _record(agent_dir).parent.mkdir(parents=True, exist_ok=True)
    rewrite(_record(agent_dir), _t)
    return found[-1] if found else "absent"


def clear_outage(agent_dir: Path, spec: str,
                 now: Optional[_dt.datetime] = None) -> bool:
    """The owner's `tasks models enable`: remove `spec`'s entry and leave the time of
    the clear, so that a panel already running cannot record the seat again for a
    failure from before it (`record_outage`). True if an entry was there — the time
    is left either way: "this seat works as of now" holds whether or not a panel
    has got as far as recording its failure."""
    from tasks.atomic import rewrite
    now = now or _dt.datetime.now().astimezone()
    found = []

    def _t(text: str) -> str:
        data = _load(text)
        if spec in data:
            found.append(data.pop(spec))
        cleared = _cleared(text, now)
        # the LATEST clear stands: this one took its time before it took the lock,
        # so a later `enable` may already be written (impl panel r1)
        cleared[spec] = max(cleared.get(spec, now), now)
        return _dump(data, cleared)

    _record(agent_dir).parent.mkdir(parents=True, exist_ok=True)
    rewrite(_record(agent_dir), _t)
    return bool(found)
