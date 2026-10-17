#!/usr/bin/env python3
"""The owner's options-dialog answers into the lane's `chat_log.md` (task 233, PHASE-B item 6).

The owner, 2026-10-10 20:24 (retro 177, "Da, așa (Recomandat)"): his answers in options dialogs are not prompts and
reached no log — "capture them where the prompts are captured". `dialog-log-hook` runs this module on the
AskUserQuestion tool's PostToolUse, after he answered (claude 2.1.302, measured in task 232: `tool_response` =
`{questions, answers, annotations}`, `answers` keyed by each question's text, a typed answer being the answer string).
A dialog he cancelled fires no PostToolUse (measured): the wrapper's PreToolUse journal line is its only trace.

One entry, in the chat-log hook's frame (`---`, a blank line, the header, a blank line, the body):

    **[M042]** [2026-10-17 20:12:00 UTC] `DIALOG` (claude/pid-123)

    Options dialog — the user's answers (A, exact: JSON string literals), then the agent's questions (Q):
    A1 (Color): "Red"
    Notes1: "said with a smile"
    Q1 (Color): Pick a color
    <!-- dialog:toolu_… -->

His answers come first (Recent Chat cuts an entry at 1,000 characters) and exact: each is its JSON literal, so none of
his characters changes — `json.loads` gives the text back — and no newline of his starts a line. The agent's questions
follow, flattened to one line and cut at 300 characters. Every body line starts with one of those fixed prefixes, so
no line of the entry is a heading, a gate or an entry header. The last line names the tool call: a second delivery
of the same call writes nothing (a call without an id is logged at each delivery).

An answer is not a prompt: nothing of the session's turn (its counters, the stop hook's marks) is touched. The entry
is written under the lock the chat-log hook, the state echo and `tasks tag` hold (`chat_log_counter.lock`, flock(2)):
in one hold, the duplicate check, the counter's step, then the whole entry in one write with its marker last — a run
stopped before the write leaves a gap in the ids, never a half entry that a redelivery would not repeat. The chat-log
hook's sinks are refused (a link, a FIFO, any non-regular file: nothing written, said on stderr). It exits 0.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
TOOL = "AskUserQuestion"
LOG_NAME = "chat_log.md"
COUNTER_NAME = "chat_log_counter"
HEADER = "# Project Chat Log\n\nUser messages logged with timestamps.\n\n"
Q_CAP = 300                   # characters of one question kept (the agent's words; his answers are never cut)
BODY_CAP = 50000              # the chat-log hook's cap on one message
TAIL_BYTES = 262144           # how far back a redelivery is looked for: it comes within seconds
_ID_SAFE = re.compile(r"[^A-Za-z0-9_.:-]")
# Line breaks a JSON literal written with ensure_ascii=False would leave raw, and that str.splitlines() splits on
_RAW_BREAKS = {"\x85": "\\u0085", " ": "\\u2028", " ": "\\u2029"}


def _journal():
    try:
        sys.path.insert(0, str(_HERE))
        import pb_journal
        return pb_journal
    except Exception:
        return None


def literal(value) -> str:
    """His answer as a JSON literal on one line: exact (json.loads gives it back), every line break escaped."""
    s = json.dumps(value, ensure_ascii=False)
    for raw, esc in _RAW_BREAKS.items():
        s = s.replace(raw, esc)
    return s


def flat(text, cap: int = 0) -> str:
    """The agent's words on one line: every line break and run of spaces as one space, cut at `cap` characters."""
    s = " ".join(str(text if text is not None else "").split())
    if cap and len(s) > cap:
        s = s[:cap - 1] + "…"
    return s


def entry_body(response) -> "list[str]":
    """The entry's body lines from the tool's response; one that is not the probed object is logged as given."""
    lines = ["Options dialog — the user's answers (A, exact: JSON string literals), then the agent's questions (Q):"]
    if not isinstance(response, dict):
        lines.append(f"A0 (the tool's response, not the usual object): {literal(response)}")
        return lines
    raw_q = response.get("questions")
    raw_a = response.get("answers")
    raw_n = response.get("annotations")
    questions: list = raw_q if isinstance(raw_q, list) else []
    answers: dict = raw_a if isinstance(raw_a, dict) else {}
    notes: dict = raw_n if isinstance(raw_n, dict) else {}
    asked: "list[tuple[str, str]]" = []
    for q in questions:
        if isinstance(q, dict):
            qt, qh = q.get("question"), q.get("header")
            asked.append((qt if isinstance(qt, str) else "", qh if isinstance(qh, str) else ""))
    known = {t for t, _ in asked}
    for text in answers:                              # an answer to a question the list did not carry
        if text not in known:
            asked.append((str(text), ""))
    a_lines, q_lines = [], []
    for i, (text, head) in enumerate(asked, 1):
        tag = f" ({flat(head, 60)})" if head else ""
        a_lines.append(f"A{i}{tag}: {literal(answers[text])}" if text in answers else f"A{i}{tag}: (no answer)")
        note = notes.get(text)
        if isinstance(note, dict):
            note = note.get("notes")
        if isinstance(note, str) and note:
            a_lines.append(f"Notes{i}: {literal(note)}")
        q_lines.append(f"Q{i}{tag}: {flat(text, Q_CAP)}")
    if not asked:
        a_lines.append("A0: (no question or answer in the tool's response)")
    return lines + a_lines + q_lines


def _lane(project: Path) -> "Path | None":
    """The lane the chat-log hook writes the prompts to (`core.resolve_agent_dir`) — and nothing when it cannot be
    resolved cleanly: a `current_user` marker that is not valid, or lanes without a marker (a fresh clone of a
    multi-user repository), write nothing rather than another lane's log."""
    agent = project / ".agent"
    if not (agent / "current_user").exists() and not (agent / "tasks").is_dir():
        try:
            if any((c / "tasks").is_dir() for c in agent.iterdir()):
                return None
        except OSError:
            return None
    try:
        sys.path.insert(0, str(_HERE.parent))
        from tasks.core import resolve_agent_dir
        lane = Path(resolve_agent_dir(project))
    except BaseException:                             # resolve_agent_dir exits on an invalid marker
        return None
    return lane if lane.is_dir() and not lane.is_symlink() else None


def _session_tag() -> str:
    try:
        sys.path.insert(0, str(_HERE.parent))
        from tasks.core import resolve_session_id
        sid = resolve_session_id(quiet=True) or ""
    except BaseException:
        sid = ""
    return _ID_SAFE.sub("", sid)[:80] or "no-session"


def _sink_ok(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def _open(path: Path, flags: int, mode: int = 0o644) -> int:
    fd = os.open(str(path), flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{path.name} is not a regular file")
    return fd


def _append(fd: int, data: bytes) -> None:
    """The entry's one write (a seam the tests use to stop a run between its steps)."""
    os.write(fd, data)


def write_entry(lane: Path, tool_use_id: str, body: "list[str]", provider: str, session: str) -> str:
    """Append the entry under the chat log's lock; return what happened ("logged M<n>", "not logged: …")."""
    log, counter, lock = lane / LOG_NAME, lane / COUNTER_NAME, lane / (COUNTER_NAME + ".lock")
    for p in (log, counter, lock, lane / (LOG_NAME + ".new"), lane / (LOG_NAME + ".bak")):
        if not _sink_ok(p):
            return f"not logged: {p.name} is a link or not a regular file"
    safe_id = _ID_SAFE.sub("", tool_use_id)[:120]
    mark = f"<!-- dialog:{safe_id} -->" if safe_id else ""
    lfd = _open(lock, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(lfd, fcntl.LOCK_EX)
        if mark and log.exists():
            fd = _open(log, os.O_RDONLY)
            try:
                size = os.fstat(fd).st_size
                os.lseek(fd, max(0, size - TAIL_BYTES), os.SEEK_SET)
                tail = os.read(fd, TAIL_BYTES)
            finally:
                os.close(fd)
            if ("\n" + mark + "\n").encode() in tail:
                return "not logged: a duplicate"
        try:
            cfd = _open(counter, os.O_RDONLY)
            try:
                raw = os.read(cfd, 64).decode("ascii", "replace").strip()
            finally:
                os.close(cfd)
        except FileNotFoundError:
            raw = ""
        n = (int(raw) if raw.isdigit() else 0) + 1
        msg = f"M{n:03d}"
        cfd = _open(counter, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        try:
            os.write(cfd, f"{n}\n".encode())
        finally:
            os.close(cfd)
        text = "\n".join(body)
        if len(text) > BODY_CAP:
            text = text[:BODY_CAP] + f"...[{len(text) - BODY_CAP} chars removed]"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        entry = (f"---\n\n**[{msg}]** [{stamp}] `DIALOG` ({provider}/{session})\n\n{text}\n"
                 + (mark + "\n" if mark else "") + "\n")
        fresh = not log.exists()
        fd = _open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            _append(fd, ((HEADER if fresh else "") + entry).encode("utf-8", "surrogateescape"))
        finally:
            os.close(fd)
        return f"logged {msg}"
    finally:
        os.close(lfd)


def main() -> int:
    j = _journal()
    if j is None:
        return 0
    run = j.HookRun("dialog-log")

    def _main() -> int:
        try:
            raw = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
            payload = json.loads(raw) if raw.strip() else {}
        except Exception:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        run.payload = payload
        if payload.get("tool_name") != TOOL or payload.get("hook_event_name") != "PostToolUse":
            run.note("skip", "not the dialog's answer")
            return 0
        root = j._cwd_project_root()
        lane = _lane(Path(root)) if root else None
        if lane is None:
            run.note("skip", "not logged: no lane")
            return 0
        run.root = root
        session = _session_tag()
        run.session_id = "" if session == "no-session" else session
        try:
            done = write_entry(lane, str(payload.get("tool_use_id") or ""), entry_body(payload.get("tool_response")),
                               _ID_SAFE.sub("", os.environ.get("PLAYBOOK_PROVIDER", "claude"))[:24] or "claude",
                               session)
        except Exception as exc:
            print(f"playbook: the dialog's answer was not logged ({type(exc).__name__})", file=sys.stderr)
            run.note("error", "not logged", type(exc).__name__)
            return 0
        if done.startswith("not logged") and done != "not logged: a duplicate":
            print(f"playbook: {done} — the dialog's answer was not logged", file=sys.stderr)
        run.note("ok", done)
        return 0

    try:
        j.run_hook(run, _main)
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
