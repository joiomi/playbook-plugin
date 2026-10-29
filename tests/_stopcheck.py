"""What a stop answered (task 340): the stop hook never blocks, it exits 0 and leaves a memento or nothing.

Before task 340 a test asserted a held stop as exit 2 and a released one as exit 0. Now `held(r)` is the memento —
exit 0 and one JSON line on stdout with a `systemMessage` — and `released(r)` is exit 0 with nothing on stdout. A stop
that exits anything else is neither, so a test that expects one of them fails.
"""
from __future__ import annotations

import json


def _stdout(r) -> str:
    out = r.stdout
    return out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")


def memento(r) -> "str | None":
    """The memento's text when the stop exited 0 with one; None otherwise."""
    if r.returncode != 0:
        return None
    lines = [line for line in _stdout(r).splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    try:
        msg = json.loads(lines[0]).get("systemMessage")
    except (ValueError, AttributeError):
        return None
    return msg if isinstance(msg, str) else None


def held(r) -> bool:
    """The stop ended with its memento: what a held (exit 2) stop was before task 340."""
    return memento(r) is not None


def released(r) -> bool:
    """The stop ended with nothing said: a released stop."""
    return r.returncode == 0 and not _stdout(r).strip()
