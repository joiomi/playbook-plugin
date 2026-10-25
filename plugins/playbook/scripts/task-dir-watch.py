#!/usr/bin/env python3
"""The task-folder watch (task 196, PLAN S14 T5b, B31 = #12): a task folder made by hand is found AFTER the call that
made it — a shell command, a Write, or an Edit that creates its file (an empty `old_string` creates the file AND its
missing folders, measured in task 196) —, and the session whose call made it is held until the folder is removed or
made known.

Run by `task-dir-watch-hook` with the hook's payload on stdin:

    task-dir-watch.py PROJECT AGENT_DIR SESSION_DIR SESSION_ID

and the event and the tool read from the payload (normalized, so grok's dialect reads the same):

  PreToolUse, a watched call (a shell call, a Write, an Edit/MultiEdit that creates, a NotebookEdit): hold the
              session if it is flagged (exit 2, the reason on stderr), else write the listing of the lane's `tasks/`
              to `SESSION_DIR/task_dirs_before.<tool_use_id>`
  PreToolUse, any other call the hook sees (an edit of an existing file): hold the session if it is flagged
  PostToolUse or PostToolUseFailure (measured: a command that exits non-zero fires only that), a watched call: list
              again; each NEW directory with a `<digits>-` name that is not known — registered (a `reserved` line not
              counting) or committed, `tasks.registry.unknown_now` — is flagged: the flag file, a journal line, and
              the words said to the agent (additionalContext, naming the event)

Only the session's own lane is watched (`AGENT_DIR`, plan panel P1): another lane is its own sessions' and its own
close's. A lane with no registry is watched too (task 197, its plan panel P1): with none, what is not committed is
unknown; a call during which the registry vanished says so, and a standing flag outlives the registry (impl panel r1
I6). A flagged folder that a re-enrolment takes in stays unknown — to this watch and to the close: the registry's
reader reads the lane's flags (`registry.unknown_now`, task 197 impl panel r1 I2) —, and a folder made during a call
whose row is `enrolled` by its end is flagged (I3) — every watched call has its snapshot, with no `tasks/` an empty
one (impl panel r2 J1). A post that
finds no snapshot of its call flags every unknown folder present (fail closed, plan panel P4). A git that does not
answer within the budget proves nothing: the folder is flagged (P7). An unreadable registry never holds — a hold would
block its repair — it is said and journaled, and the close refuses naming the repair (P9).

Every file of the session directory is read, written, listed and removed through ONE descriptor of that directory,
opened from the project root without following a link (`shared._open_dir_inside`; impl panel r1 I1, codex, Critical,
reproduced: a session directory swapped for a link after the hook's check let the sweep delete outside the project).
A lane reached through a link (a shared folder) is used — its session directory opened without following its own
link — and nothing in it is swept.

The way out of a hold, let through while the session is flagged: ONE simple command — quoted literals, `$'…'`
included; no operator, redirection, expansion, glob or newline outside quotes — that is `rm` (-r -R -f -v, --recursive,
--force, --verbose, `--`) or `rmdir` (-v) whose every operand names a flagged folder by its PHYSICAL parent and its name
(as rm reaches it: `link/../x` is where the link's target is, P18), or a `tasks` CLI call by a path
that is, physically, the project's `.claude/bin/tasks` or this plugin's `scripts/tasks` (impl panel r1 I3: the bare
`tasks` is whatever PATH finds; a `cd` is none — impl panel r2 J4). Read-type tools never reach the hook. Before refusing, the flag is re-checked under
its lock: a folder gone or now known leaves it; with none left the flag is removed and the call passes.

Exit codes: 0 = pass; 2 = hold. Anything else (1: a crash, the session directory not openable safely, the flag's lock
not had within LOCK_WAIT) is read by the hook as a hold while a flag stands (fail closed, as the gate's status read —
task 055) and is said after a call.

Stdlib only. Parsed by any python3, and it does nothing below 3.10 (the floor, task 194: the registry reader needs it).
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 10):          # below the floor nothing is watched (task 196 W5)
    sys.exit(0)

import datetime  # noqa: E402
import fcntl  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shlex  # noqa: E402
import stat  # noqa: E402
import time  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOK_DIR.parent))      # the plugin root: `tasks.registry`
sys.path.insert(0, str(HOOK_DIR))             # pb_journal

from tasks import registry  # noqa: E402

FLAG = "task_dir_flag"
FLAG_LOCK = "task_dir_flag.lock"
PENDING = "task_dir_flag.pending."            # a detection whose post could not have the lock (the judge's K1)
BEFORE = "task_dirs_before"
SNAPSHOT_MAX_AGE = 86400                      # a call another hook denied (it never ran) leaves its snapshot
GIT_TIMEOUT = 1.0                             # the hook entries allow 20 s; git stops asking at its first timeout
LOCK_WAIT = 3.0                               # the flag's lock (impl panel r1 I14)
_TUID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_UNQUOTED_META = set(";&|<>()`$*?[]{}~!#\n\r")
_RM = {"rm", "/bin/rm", "/usr/bin/rm"}
_RMDIR = {"rmdir", "/bin/rmdir", "/usr/bin/rmdir"}
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_FILE_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC
_ANSI_C = {"a": 7, "b": 8, "e": 27, "E": 27, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, "'": 39,
           '"': 34, "?": 63}
# `\c`, `\u`, `\U` and an escape bash does not know are REFUSED, never guessed (impl panel r2 J7, codex: `\cA` read
# as two characters named a folder bash's `$'…'` does not — `rm` would remove an unflagged sibling)


class Unsafe(Exception):
    """The session directory cannot be held safely, or the flag's lock was not had — the hook fails closed."""


# ---------------------------------------------------------------- the payload

def _payload(raw: str) -> dict:
    """The payload in the host's dialect, normalized as every hook reads it (hook-payload-normalize.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pb_normalize", HOOK_DIR / "hook-payload-normalize.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("hook-payload-normalize.py cannot be loaded")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = json.loads(raw)
    d = mod.normalize(d) if isinstance(d, dict) else {}
    return d if isinstance(d, dict) else {}


def _str(d: dict, key: str) -> str:
    v = d.get(key)
    return v if isinstance(v, str) else ""


def _dict(d: dict, key: str) -> dict:
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def _watched(p: dict) -> bool:
    """A call that can make a folder: a shell call (Bash, or Claude Code's Monitor, which runs its command in the same
    shell — task 307), a Write, an Edit/MultiEdit with an empty `old_string` (it creates the file and its folders — impl
    panel r1 I12, measured), a NotebookEdit. An edit of an existing file cannot."""
    tool, ti = _str(p, "tool_name"), _dict(p, "tool_input")
    if tool in ("Bash", "Monitor", "Write", "NotebookEdit"):
        return True
    if tool == "Edit":
        return ti.get("old_string") == ""
    if tool == "MultiEdit":
        edits = ti.get("edits")
        return isinstance(edits, list) and any(isinstance(e, dict) and e.get("old_string") == "" for e in edits)
    return False


# ---------------------------------------------------------------- the session directory, held

def _open_session(project: Path, lane: Path, sid: str) -> "tuple[int, bool]":
    """(a descriptor of `lane/sessions/<sid>`, made if missing; whether the lane lies physically inside the project).
    The lane is opened from the project root without following a link (else — a lane reached through a link — as it
    is), `sessions` and the session directory without following their own links. Raises Unsafe."""
    from tasks.shared import _open_dir_inside
    lfd = _open_dir_inside(project, lane)
    inside = lfd is not None
    try:
        if lfd is None:
            lfd = os.open(lane, _DIR_FLAGS)
        fd = lfd
        for part in ("sessions", sid):
            try:
                os.mkdir(part, dir_fd=fd)
            except FileExistsError:
                pass
            nfd = os.open(part, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=fd)
            if fd != lfd:
                os.close(fd)
            fd = nfd
        return fd, inside
    except OSError as e:
        raise Unsafe(f"the session directory cannot be held safely ({e})") from e
    finally:
        if lfd is not None:
            os.close(lfd)


def _read_json(sfd: int, name: str):
    """A JSON file of ours in the held directory, never through a link: None when absent; ValueError when it is not a
    regular JSON file."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | _FILE_FLAGS, dir_fd=sfd)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ValueError(f"{name}: {e}") from e
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise ValueError(f"{name} is not a regular file")
        try:
            return json.loads(fh.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"{name}: {e}") from e


def _write_json(sfd: int, name: str, obj) -> None:
    """Put `obj` at `name` in the held directory all at once (`tasks.atomic.atomic_write` through
    `/proc/self/fd/<sfd>`: a temporary file beside it, then a rename — a link standing at `name` is replaced, never
    written through). ASCII JSON: a name that is not UTF-8 round-trips as its escaped surrogate (impl panel r1 I4,
    codex, reproduced: a folder named with byte 0xff broke the write and nothing persisted). No fsync: it runs at every
    watched call."""
    from tasks.atomic import atomic_write
    atomic_write(Path(f"/proc/self/fd/{sfd}") / name, json.dumps(obj), fsync=False)


def _unlink(sfd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=sfd)          # a link is removed, never what it names
    except OSError:
        pass                                 # best effort: an old snapshot is swept after a day


def _lexists(sfd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=sfd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


@contextmanager
def _flag_lock(sfd: int):
    """The session's flag transaction (plan panel P17: two posts at once must not lose a folder, a prune must not
    remove a flag written after its read), waited for LOCK_WAIT at most (impl panel r1 I14: a hook the host stops at
    its deadline lets the call through). A lock file that is a link or not a regular file is refused."""
    try:
        fd = os.open(FLAG_LOCK, os.O_RDWR | os.O_CREAT | _FILE_FLAGS, 0o600, dir_fd=sfd)
    except OSError as e:
        raise Unsafe(f"{FLAG_LOCK}: {e}") from e
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Unsafe(f"{FLAG_LOCK} is not a regular file")
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Unsafe(f"{FLAG_LOCK} not had within {LOCK_WAIT:.0f} s")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _record_flag(sfd: int, lane_rel: str, folders: "list[str]") -> "str | None":
    """Add `folders` to the session's flag; None when recorded, else why not. Under the flag's lock — and when that
    lock is not had within its wait (impl panel r2 J5/J8: a hold the lock kept from being written held nothing), as a
    PENDING entry under a name of its own, which the hook and `_pre` count as a flag and the next re-check merges under
    the lock (the single judge's run 1, K1, codex: a write without the lock raced a re-check that had read the flag
    before it, and the re-check removed the whole flag — the folder left unflagged)."""
    def merge() -> None:
        try:
            old = _read_json(sfd, FLAG)
        except ValueError:
            old = None
        have = _flagged_names(old, lane_rel) or []
        merged = sorted(set(f"{lane_rel}/tasks/{n}" for n in have) | set(folders))
        times = _flag_times(old)
        now = _now()
        for f in folders:
            times.setdefault(f, now)
        _write_json(sfd, FLAG, {"v": 2, "folders": merged, "flagged_at": {f: times[f] for f in merged if f in times},
                                "since": now})
    try:
        try:
            with _flag_lock(sfd):
                merge()
        except Unsafe:
            _write_json(sfd, f"{PENDING}{os.getpid()}.{time.time_ns()}",
                        {"v": 1, "folders": sorted(set(folders)), "since": _now()})
        return None
    except OSError as e:
        return str(e)


def _flag_times(data) -> "dict[str, str]":
    """When each folder of a flag (or a pending entry) was flagged: its `flagged_at`, else the entry's `since` — a
    record for a person; no rule reads it (task 197, impl panel r1: the registry's reader needs no time)."""
    if not isinstance(data, dict):
        return {}
    since = data.get("since") if isinstance(data.get("since"), str) else ""
    times = data.get("flagged_at") if isinstance(data.get("flagged_at"), dict) else {}
    out = {}
    for f in data.get("folders") or []:
        if isinstance(f, str):
            t = times.get(f)
            out[f] = t if isinstance(t, str) else since
    return out


def _pending(sfd: int) -> "list[str]":
    try:
        return sorted(n for n in os.listdir(sfd) if n.startswith(PENDING))
    except OSError:
        return []


def _flag_present(sfd: int) -> bool:
    return _lexists(sfd, FLAG) or bool(_pending(sfd))


def _sweep_snapshots(sfd: int) -> None:
    now = time.time()
    try:
        names = os.listdir(sfd)
    except OSError:
        return
    for n in names:
        if n == BEFORE or n.startswith(BEFORE + "."):
            try:
                if now - os.stat(n, dir_fd=sfd, follow_symlinks=False).st_mtime > SNAPSHOT_MAX_AGE:
                    os.unlink(n, dir_fd=sfd)
            except OSError:
                pass


# ---------------------------------------------------------------- what is watched

def _lane_rel(project: Path, lane: Path) -> str:
    try:
        return lane.relative_to(project).as_posix()
    except ValueError:
        return lane.as_posix()


def _listing(lane: Path) -> "list[str]":
    """The `<digits>-` DIRECTORIES under the lane's `tasks/` (a file is not a task folder — impl panel r1 I11). Raises
    registry.LaneUnreadable when `tasks/` or an entry cannot be read (impl panel r2 J9: an empty listing let a folder
    made in the same call through)."""
    return registry.task_dir_names(lane / "tasks")


def _registry_present(lane: Path) -> bool:
    return os.path.lexists(registry.registry_path(lane))


def _unknown(project: Path, lane: Path, names: "list[str] | None", before: "set[str] | None" = None) -> "list[str]":
    """`unknown_now` for the watch: with no registry nothing knows a folder but a commit (impl panel r1 I6); `before`,
    the call's own snapshot, makes a folder enrolled during the call unknown (task 197, impl panel r1 I3)."""
    return registry.unknown_now(project, lane, names, git_timeout=GIT_TIMEOUT, no_registry="git", before=before) or []


# ---------------------------------------------------------------- the way out

def _ansi_c_word(text: str, i: int) -> "tuple[bytes, int] | None":
    """The bytes of `$'…'` starting at `text[i]` (the `$`), and the index after its closing quote; None when it does
    not close or holds an escape outside `_ANSI_C`, `\\xHH` and `\\NNN` (each read as bash reads it)."""
    out = bytearray()
    j = i + 2
    while j < len(text):
        c = text[j]
        if c == "'":
            return bytes(out), j + 1
        if c != "\\":
            out += c.encode("utf-8", "surrogateescape")
            j += 1
            continue
        if j + 1 >= len(text):
            return None
        e = text[j + 1]
        if e in _ANSI_C:
            out.append(_ANSI_C[e])
            j += 2
        elif e == "x":
            m = re.match(r"[0-9A-Fa-f]{1,2}", text[j + 2:])
            if not m:
                return None
            else:
                out.append(int(m.group(0), 16))
                j += 2 + len(m.group(0))
        elif e in "01234567":
            m = re.match(r"[0-7]{1,3}", text[j + 1:])
            assert m
            out.append(int(m.group(0), 8) & 0xFF)
            j += 1 + len(m.group(0))
        else:
            return None
    return None


def _words(cmd: str) -> "list[str] | None":
    """The words of ONE simple command with no expansion. None when the text holds anything else outside quotes — an
    operator, a redirection, an expansion, a glob, a comment, a newline, an unterminated quote — or `$`, a backtick or
    a backslash inside double quotes (plan panel P3: quoted literals name any folder; impl panel r1 I4: `$'…'` names
    one whose name is not UTF-8, as a byte)."""
    words: "list[str]" = []
    cur = bytearray()
    have = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c in " \t":
            if have:
                words.append(cur.decode("utf-8", "surrogateescape"))
                cur, have = bytearray(), False
            i += 1
        elif c == "$" and cmd[i + 1:i + 2] == "'":
            got = _ansi_c_word(cmd, i)
            if got is None:
                return None
            cur += got[0]
            have, i = True, got[1]
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                return None
            cur += cmd[i + 1:j].encode("utf-8", "surrogateescape")
            have, i = True, j + 1
        elif c == '"':
            j = i + 1
            while j < len(cmd) and cmd[j] != '"':
                if cmd[j] in "$`\\":
                    return None
                j += 1
            if j >= len(cmd):
                return None
            cur += cmd[i + 1:j].encode("utf-8", "surrogateescape")
            have, i = True, j + 1
        elif c == "\\":
            if i + 1 >= len(cmd) or cmd[i + 1] in "\n\r":
                return None
            cur += cmd[i + 1].encode("utf-8", "surrogateescape")
            have, i = True, i + 2
        elif c in _UNQUOTED_META:
            return None
        else:
            cur += c.encode("utf-8", "surrogateescape")
            have, i = True, i + 1
    if have:
        words.append(cur.decode("utf-8", "surrogateescape"))
    return words


def _is_tasks_cli(word: str, cwd: str, project: Path) -> bool:
    """A path that is, physically, the project's `.claude/bin/tasks` or this plugin's `scripts/tasks` (impl panel r1
    I3: matched lexically, `link/../.claude/bin/tasks` ran another project's program; a bare `tasks` is what PATH
    finds)."""
    if "/" not in word:
        return False
    try:
        real = os.path.realpath(os.path.join(cwd, word))
        return real in (os.path.realpath(project / ".claude" / "bin" / "tasks"), os.path.realpath(HOOK_DIR / "tasks"))
    except (OSError, ValueError):
        return False


def _names_flagged(op: str, cwd: str, tasks_real: str, names: "set[str]") -> bool:
    """True when the operand reaches a flagged folder as rm reaches it: its parent resolved physically (`..` after a
    link goes where the link's target is) is the lane's `tasks/`, and its last component is the folder's name — rm
    removes a link standing there, never its target; `link/` with a slash would reach through it (plan panel P18)."""
    if not op or op.strip("/") == "":
        return False
    trail = op.endswith("/")
    full = os.path.join(cwd, op.rstrip("/"))
    parent, name = os.path.split(full)
    if name not in names:
        return False
    try:
        if os.path.realpath(parent) != tasks_real:
            return False
    except (OSError, ValueError):
        return False
    return not (trail and os.path.islink(full))


def _is_way_out(cmd: str, cwd: str, project: Path, lane: Path, names: "list[str]") -> bool:
    words = _words(cmd)
    if not words:
        return False
    if _is_tasks_cli(words[0], cwd, project):
        return True
    if words[0] in _RM:
        short, longs = set("rRfv"), {"--recursive", "--force", "--verbose"}
    elif words[0] in _RMDIR:
        short, longs = set("v"), {"--verbose"}
    else:
        return False
    ops: "list[str]" = []
    ended = False
    for w in words[1:]:
        if not ended and w == "--":
            ended = True
        elif not ended and w.startswith("--"):
            if w not in longs:
                return False
        elif not ended and w.startswith("-") and len(w) > 1:
            if not set(w[1:]) <= short:
                return False
        else:
            ops.append(w)
    if not ops:
        return False
    try:
        tasks_real = os.path.realpath(lane / "tasks")
    except (OSError, ValueError):
        return False
    wanted = set(names)
    return all(_names_flagged(op, cwd, tasks_real, wanted) for op in ops)


# ---------------------------------------------------------------- the flag

def _flagged_names(data, lane_rel: str) -> "list[str] | None":
    """The folder names a flag holds (its entries are `<lane>/tasks/<name>`); None when it is not ours to read."""
    if not isinstance(data, dict) or not isinstance(data.get("folders"), list):
        return None
    prefix = lane_rel + "/tasks/"
    return [f[len(prefix):] for f in data["folders"]
            if isinstance(f, str) and f.startswith(prefix) and "/" not in f[len(prefix):]]


def _recheck(project: Path, lane: Path, sfd: int) -> "tuple[list[str], str, str]":
    """(the flagged folders still standing and unknown, the registry's state — "ok", "gone" or "unreadable" —, a note)
    — the flag rewritten, or removed when none is left. Under the flag's lock. An unreadable flag stands for every
    unknown folder of the lane; an unreadable registry holds nothing (P9); with the registry gone the folders still
    stand flagged — nothing knows them (impl panel r1 I6, fable: the hold ended there silently)."""
    lane_rel = _lane_rel(project, lane)
    with _flag_lock(sfd):
        pending = _pending(sfd)
        if not _lexists(sfd, FLAG) and not pending:
            return [], "ok", ""
        try:
            data = _read_json(sfd, FLAG) if _lexists(sfd, FLAG) else {"v": 1, "folders": []}
        except ValueError:
            data = None
        names = _flagged_names(data, lane_rel)
        times = _flag_times(data)
        for pend in pending:                      # merged under the lock; removed below only once read (K1)
            try:
                pdata = _read_json(sfd, pend)
                more = _flagged_names(pdata, lane_rel)
            except ValueError:
                pdata, more = None, None
            for f, t_ in _flag_times(pdata).items():
                times.setdefault(f, t_)
            names = None if names is None or more is None else sorted(set(names) | set(more))
        state, note = "ok", ""
        if not _registry_present(lane):
            state = "gone"
            note = ("the task registry is gone: nothing knows these folders — `.claude/bin/tasks adopt <it>` writes a "
                    "new registry that does")
        try:
            left = _unknown(project, lane, names)       # a re-enrolled flagged folder stays unknown (I2)
        except registry.RegistryUnreadable as e:
            return [], "unreadable", registry.unreadable_message(e)
        if not left:
            _unlink(sfd, FLAG)
        else:
            folders = [f"{lane_rel}/tasks/{n}" for n in left]
            if pending or not isinstance(data, dict) or data.get("folders") != folders or data.get("v") != 2:
                now = _now()
                _write_json(sfd, FLAG, {"v": 2, "folders": folders,
                                        "flagged_at": {f: times.get(f) or now for f in folders}, "since": now})
        for pend in pending:                      # only the entries read above — a newer one stands
            _unlink(sfd, pend)
        return left, state, note


def _adoptable(lane: Path, name: str) -> "str | None":
    """None when `tasks adopt` can take the folder; else why it cannot (registry.adopt's own checks)."""
    if not registry.TASK_DIR_RE.fullmatch(name):
        return "its name is not <number>-<slug>"
    d = lane / "tasks" / name
    tm = d / "task.md"
    if d.is_symlink() or not d.is_dir():
        return "it is not a real directory"
    if tm.is_symlink() or not tm.is_file():
        return "it holds no task.md"
    num = registry.task_number(name)
    for other in _listing(lane):
        o = lane / "tasks" / other
        if other != name and registry.task_number(other) == num and (o / "task.md").is_file():
            return f"its number belongs to {other}"
    return None


def _plain(s: str) -> bool:
    return all(32 <= ord(c) < 127 for c in s)


def _ansi_c_quote(s: str) -> str:
    """`$'…'` for a name that is not printable ASCII: every other byte as `\\xHH` (what the way out reads back)."""
    raw = s.encode("utf-8", "surrogateescape")
    return "$'" + "".join(chr(b) if 32 <= b < 127 and b not in (39, 92) else f"\\x{b:02x}" for b in raw) + "'"


def _shell_word(s: str) -> str:
    return shlex.quote(s) if _plain(s) else _ansi_c_quote(s)


def _shown(s: str) -> str:
    return s if _plain(s) else _ansi_c_quote(s)


def _rel_from(cwd: str, target: Path) -> str:
    try:
        return os.path.relpath(target, cwd)
    except ValueError:
        return str(target)


def _hold_message(project: Path, lane: Path, left: "list[str]", cwd: str, notes: "list[str]") -> str:
    lane_rel = _lane_rel(project, lane)
    out = ["BLOCKED: a task folder appeared during this session's shell command, Write or Edit, and the playbook CLI "
           "did not make it (the task registry does not name it and it is not committed):", ""]
    out += [f"  {_shown(f'{lane_rel}/tasks/{n}')}" for n in left]
    out += [""] + [f"({n})" for n in notes] + ([""] if notes else [])
    out += ["Only `.claude/bin/tasks new` makes task folders. This session's next actions wait until each folder "
            "above is made known or removed. Run ONE of these, alone in its command (written for the current "
            f"directory, {_shown(cwd)}):", ""]
    cli = _shell_word(_rel_from(cwd, project / ".claude" / "bin" / "tasks"))
    if "/" not in cli:
        cli = "./" + cli
    for n in left:
        folder = lane / "tasks" / n
        where = _shell_word(_rel_from(cwd, folder))
        try:                                      # from inside the folder `rm -r .` works for nothing (195's run 3 N3)
            real_cwd, real_folder = os.path.realpath(cwd), os.path.realpath(folder)
            if real_cwd == real_folder or real_cwd.startswith(real_folder + os.sep):
                where = _shell_word(str(folder))
        except (OSError, ValueError):
            pass
        why_not = _adoptable(lane, n)
        out.append(f"  {_shown(f'{lane_rel}/tasks/{n}')}")
        if why_not is None:
            out.append(f"    keep it:    {cli} adopt {where}    (recommended — removing deletes its content)")
            out.append(f"    remove it:  rm -r {where}")
        else:
            out.append(f"    remove it:  rm -r {where}    (adopt cannot take it: {why_not}; removing deletes its "
                       "content — if it was there before this call, it may be someone's work)")
    out += ["", "If you did not make it, another session working in this lane at the same time may have — adopt it rather "
            "than remove it. Read tools still work; other sessions are not held."]
    return "\n".join(out)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _journal(agent_dir: Path, decision: str, reason: str, sid: str, tool: str, path: str, cmd: str = "") -> None:
    """One enforcement-journal line (`hook="task-dir-watch"`), in the ratified decisions: `record` when a folder is
    flagged (or the registry is unreadable or removed), `block` when a call is held, `allow` for the way out."""
    try:
        import pb_journal
        pb_journal.append(agent_dir, "task-dir-watch", decision, reason, session_id=sid, tool=tool, path=path,
                          command=cmd)
    except Exception:
        pass


def _say(event: str, notes: "list[str]") -> None:
    if notes:
        event = event if event in ("PostToolUse", "PostToolUseFailure") else "PostToolUse"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(notes)}}))


# ---------------------------------------------------------------- the modes

def _pre(watched: bool, p: dict, project: Path, lane: Path, sfd: int, inside: bool, sid: str) -> int:
    tool = _str(p, "tool_name")
    cmd = _str(_dict(p, "tool_input"), "command")
    cwd = _str(p, "cwd")
    cwd = cwd if os.path.isabs(cwd) else str(project)
    if _flag_present(sfd):
        left, state, note = _recheck(project, lane, sfd)
        if state == "unreadable":                 # impl panel r1 I7: journaled (not held — its repair must run)
            _journal(lane, "record", "task registry unreadable", sid, tool, "", cmd)
            print(note, file=sys.stderr)
        elif state == "gone":
            _journal(lane, "record", "task registry removed", sid, tool,
                     f"{_lane_rel(project, lane)}/{registry.REGISTRY_NAME}", cmd)
        notes = [note] if note and state == "gone" else []
        if left:
            first = f"{_lane_rel(project, lane)}/tasks/{left[0]}"
            if tool in ("Bash", "Monitor") and _is_way_out(cmd, cwd, project, lane, left):
                _journal(lane, "allow", "unknown task folder: the way out", sid, tool, first, cmd)
            else:
                _journal(lane, "block", "unknown task folder", sid, tool, first, cmd)
                print(_hold_message(project, lane, left, cwd, notes), file=sys.stderr)
                return 2
    if watched:          # every lane, a registry or not (P1), `tasks/` or not — an empty listing (impl panel r2 J1)
        if inside:
            _sweep_snapshots(sfd)
        tuid = _str(p, "tool_use_id")
        name = f"{BEFORE}.{tuid}" if _TUID_RE.fullmatch(tuid) else BEFORE
        _write_json(sfd, name, {"v": 1, "folders": _listing(lane), "registry": _registry_present(lane)})
    return 0


def _post(p: dict, project: Path, lane: Path, sfd: int, sid: str) -> int:
    tool = _str(p, "tool_name")
    ti = _dict(p, "tool_input")
    cmd = _str(ti, "command") or _str(ti, "file_path") or _str(ti, "notebook_path")
    event = _str(p, "hook_event_name")
    tuid = _str(p, "tool_use_id")
    before = f"{BEFORE}.{tuid}" if _TUID_RE.fullmatch(tuid) else BEFORE
    try:
        snap = _read_json(sfd, before)
    except ValueError:
        snap = None
    # the snapshot stays until the detection is recorded: a close in a parallel call reads it as "this call is still
    # running" (the single judge's run 1, K2 — codex: removed first, a close in that gap passed the folder)
    try:
        return _post_with(p, project, lane, sfd, sid, snap, tool, cmd, event)
    finally:
        _unlink(sfd, before)


def _post_with(p: dict, project: Path, lane: Path, sfd: int, sid: str, snap, tool: str, cmd: str, event: str) -> int:
    notes: "list[str]" = []
    had_registry = isinstance(snap, dict) and bool(snap.get("registry"))
    if not _registry_present(lane) and had_registry:
        # still watched — with no registry what the git rule does not know is unknown (task 197, P1)
        _journal(lane, "record", "task registry removed during this call", sid, tool,
                 f"{_lane_rel(project, lane)}/{registry.REGISTRY_NAME}", cmd)
        notes.append(f"⚠ playbook: this call removed the lane's task registry ({registry.REGISTRY_NAME}): until a new "
                     "one is written, what is not committed counts as made by hand.")
    now = _listing(lane)
    if isinstance(snap, dict) and isinstance(snap.get("folders"), list):
        new = sorted(set(now) - set(f for f in snap["folders"] if isinstance(f, str)))
    else:
        new = now                             # no snapshot of this call: every unknown folder present (P4)
    if not new:
        _say(event, notes)
        return 0
    try:
        # what stood before the call; with no snapshot nothing is known either way and the rows are trusted — a
        # successful close removes its session directory, the call's snapshot with it, and reading every `enrolled`
        # folder as made by the call then held the session after every close (the single judge's run 1, K1); the
        # one-lane project with no `.agent/tasks` (no project to the hooks before the call) is a stated bound
        stood = (set(f for f in snap["folders"] if isinstance(f, str))
                 if isinstance(snap, dict) and isinstance(snap.get("folders"), list) else None)
        left = _unknown(project, lane, new, before=stood)
    except registry.RegistryUnreadable as e:
        _journal(lane, "record", "task registry unreadable", sid, tool, "", cmd)
        notes.append("⚠ playbook: the task-folder watch could not read the task registry — "
                     + registry.unreadable_message(e))
        _say(event, notes)
        return 0
    if left:
        lane_rel = _lane_rel(project, lane)
        folders = [f"{lane_rel}/tasks/{n}" for n in left]
        recorded = _record_flag(sfd, lane_rel, folders)
        for f in folders:
            _journal(lane, "record", "unknown task folder" if recorded is None
                     else "unknown task folder (the hold could not be recorded)", sid, tool, f, cmd)
        if recorded is None:
            notes.append("⚠ playbook: this call made task folder(s) the playbook CLI did not make: "
                         + ", ".join(_shown(f) for f in folders) + ". This session's next actions wait until each is "
                         "adopted (`.claude/bin/tasks adopt <it>`) or removed (`rm -r <it>`). Only `.claude/bin/tasks "
                         "new` makes task folders.")
        else:
            notes.append("⚠ playbook: this call made task folder(s) the playbook CLI did not make: "
                         + ", ".join(_shown(f) for f in folders) + f" — and the hold could not be recorded ({recorded})"
                         ". Adopt (`.claude/bin/tasks adopt <it>`) or remove (`rm -r <it>`) each now: `tasks work "
                         "done` refuses while one stands.")
    _say(event, notes)
    return 0


def main(argv: "list[str]") -> int:
    if len(argv) == 4 and argv[1] == "--enrol":    # the session start (task 197, P1): a lane's registry, if missing
        try:
            registry.ensure_enrolled(Path(argv[2]), Path(argv[3]))
            return 0
        except Exception as e:                # never a refusal of the session start
            print(f"task-dir-watch: the task registry could not be written: {e}", file=sys.stderr)
            return 1
    if len(argv) != 5:
        print("usage: task-dir-watch.py PROJECT AGENT_DIR SESSION_DIR SESSION_ID (the payload on stdin)",
              file=sys.stderr)
        return 64
    project, lane, session_dir, sid = Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4]
    if session_dir != lane / "sessions" / sid or "/" in sid or sid in ("", ".", ".."):
        print("task-dir-watch: SESSION_DIR must be AGENT_DIR/sessions/SESSION_ID", file=sys.stderr)
        return 64
    try:
        p = _payload(sys.stdin.read())
        event, tool = _str(p, "hook_event_name"), _str(p, "tool_name")
        if event not in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            return 0
        watched = _watched(p)
        if event != "PreToolUse" and not watched:
            return 0
        sfd, inside = _open_session(project, lane, sid)
        try:
            if event == "PreToolUse":
                return _pre(watched, p, project, lane, sfd, inside, sid)
            return _post(p, project, lane, sfd, sid)
        finally:
            os.close(sfd)
    except Exception as e:                    # the hook reads 1 as "could not run": a standing flag holds
        print(f"task-dir-watch: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
