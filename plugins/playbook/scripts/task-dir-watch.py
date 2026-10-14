#!/usr/bin/env python3
"""The task-folder watch (task 196, PLAN S14 T5b, B31 = #12): a task folder made by hand is found AFTER the call that
made it — a shell command or a Write —, and the session whose call made it is held until the folder is removed or made
known.

Run by `task-dir-watch-hook` with the hook's payload on stdin:

    task-dir-watch.py PROJECT AGENT_DIR SESSION_DIR SESSION_ID

and the event and the tool read from the payload (normalized, so grok's dialect reads the same):

  PreToolUse, a shell call or a Write: hold the session if it is flagged (exit 2, the reason on stderr), else write
              the listing of the lane's `tasks/` to `SESSION_DIR/task_dirs_before.<tool_use_id>`
  PreToolUse, an edit: hold the session if it is flagged
  PostToolUse or PostToolUseFailure (measured: a command that exits non-zero fires only that), a shell call or a
              Write: list again; each NEW folder that is not known — registered (a `reserved`
              line not counting) or committed, `tasks.registry.unknown_now` — is flagged: the flag file, a journal
              line, and the words said to the agent (additionalContext, naming the event)

Only the session's own lane is watched (`AGENT_DIR`, plan panel P1): another lane is its own sessions' and its own
close's. A lane with no registry is not watched. A task folder is a name starting `<digits>-` (`LOOSE_TASK_DIR_RE`).
A post that finds no snapshot of its call flags every unknown folder present (fail closed, plan panel P4). A git that
does not answer within the budget proves nothing: the folder is flagged (P7). An unreadable registry never holds — a
hold would block its repair — it is said and journaled, and the close refuses naming the repair (P9).

The way out of a hold, let through while the session is flagged: ONE simple command — quoted literals allowed; no
operator, redirection, expansion, glob or newline outside quotes — that is `rm` (-r -R -f -v, --recursive, --force,
--verbose, `--`) or `rmdir` (-v) whose every operand names a flagged folder by its PHYSICAL parent and its name (as rm
reaches it: `link/../x` is where the link's target is, P18), a bare `cd <dir>`, or a `tasks` CLI call (the bare `tasks`,
the project's `.claude/bin/tasks`, or this plugin's `scripts/tasks`). Read-type tools never reach the hook. Before
refusing, the flag is re-checked under its lock: a folder gone or now known leaves it; with none left the flag is
removed and the call passes.

Exit codes: 0 = pass; 2 = hold. Anything else is a crash, which the hook reads as a hold while a flag stands (fail
closed, as the gate's status read — task 055) and as nothing otherwise.

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
BEFORE = "task_dirs_before"
SNAPSHOT_MAX_AGE = 86400                      # a call denied before it ran leaves its snapshot; swept after a day
GIT_TIMEOUT = 1.0                             # the hook entries allow 20 s; git stops asking at its first timeout
_TUID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_UNQUOTED_META = set(";&|<>()`$*?[]{}~!#\n\r")
_WATCHED = {"Bash", "Write"}                 # after normalization (grok's names map onto these)
_RM = {"rm", "/bin/rm", "/usr/bin/rm"}
_RMDIR = {"rmdir", "/bin/rmdir", "/usr/bin/rmdir"}
_OPEN_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC


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


# ---------------------------------------------------------------- files in the session directory

def _read_json(path: Path):
    """A JSON file of ours, never through a link: None when absent; ValueError when not a regular JSON file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | _OPEN_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ValueError(f"{path}: {e}") from e
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise ValueError(f"{path} is not a regular file")
        try:
            return json.loads(fh.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"{path}: {e}") from e


def _write_json(path: Path, obj) -> None:
    """Put `obj` at `path` all at once (`tasks.atomic.atomic_write`: a fresh temporary file beside it, then a rename —
    a link standing at `path` is replaced, never written through). No fsync: it runs at every watched call."""
    from tasks.atomic import atomic_write
    atomic_write(path, json.dumps(obj, ensure_ascii=False), fsync=False)


def _unlink(path: Path) -> None:
    try:
        os.unlink(path)                       # a link is removed, never what it names
    except FileNotFoundError:
        pass


@contextmanager
def _flag_lock(session_dir: Path):
    """The session's flag transaction (plan panel P17: two posts at once must not lose a folder, a prune must not
    remove a flag written after its read). A lock file that is a link or not a regular file is refused."""
    fd = os.open(session_dir / FLAG_LOCK, os.O_RDWR | os.O_CREAT | _OPEN_FLAGS, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{session_dir / FLAG_LOCK} is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# ---------------------------------------------------------------- what is watched

def _lane_rel(project: Path, lane: Path) -> str:
    try:
        return lane.relative_to(project).as_posix()
    except ValueError:
        return lane.as_posix()


def _listing(lane: Path) -> "list[str]":
    try:
        return sorted(n for n in os.listdir(lane / "tasks") if registry.LOOSE_TASK_DIR_RE.match(n))
    except OSError:
        return []


def _registry_present(lane: Path) -> bool:
    return os.path.lexists(registry.registry_path(lane))


def _sweep_snapshots(session_dir: Path) -> None:
    now = time.time()
    try:
        names = os.listdir(session_dir)
    except OSError:
        return
    for n in names:
        if n == BEFORE or n.startswith(BEFORE + "."):
            try:
                if now - os.lstat(session_dir / n).st_mtime > SNAPSHOT_MAX_AGE:
                    os.unlink(session_dir / n)
            except OSError:
                pass


# ---------------------------------------------------------------- the way out

def _words(cmd: str) -> "list[str] | None":
    """The words of ONE simple command with no expansion. None when the text holds anything else outside single
    quotes — an operator, a redirection, an expansion, a glob, a comment, a newline, an unterminated quote — or `$`,
    a backtick or a backslash inside double quotes (plan panel P3: quoted literals name any folder)."""
    words: "list[str]" = []
    cur: "list[str]" = []
    have = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c in " \t":
            if have:
                words.append("".join(cur))
                cur, have = [], False
            i += 1
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                return None
            cur.append(cmd[i + 1:j])
            have, i = True, j + 1
        elif c == '"':
            j = i + 1
            while j < len(cmd) and cmd[j] != '"':
                if cmd[j] in "$`\\":
                    return None
                j += 1
            if j >= len(cmd):
                return None
            cur.append(cmd[i + 1:j])
            have, i = True, j + 1
        elif c == "\\":
            if i + 1 >= len(cmd) or cmd[i + 1] in "\n\r":
                return None
            cur.append(cmd[i + 1])
            have, i = True, i + 2
        elif c in _UNQUOTED_META:
            return None
        else:
            cur.append(c)
            have, i = True, i + 1
    if have:
        words.append("".join(cur))
    return words


def _is_tasks_cli(word: str, cwd: str, project: Path) -> bool:
    if word == "tasks":
        return True
    if "/" not in word:
        return False
    p = os.path.normpath(os.path.join(cwd, word))
    return p in (str(project / ".claude" / "bin" / "tasks"), str(HOOK_DIR / "tasks"))


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
    if words[0] == "cd":
        return len(words) == 2                    # `cd <dir>` changes nothing on disk
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


def _recheck(project: Path, lane: Path, session_dir: Path) -> "tuple[list[str], list[str]]":
    """(the flagged folders still standing and unknown, notes) — the flag rewritten, or removed when none is left.
    Under the flag's lock. An unreadable flag stands for every unknown folder of the lane; an unreadable registry
    holds nothing (P9) and is said."""
    lane_rel = _lane_rel(project, lane)
    path = session_dir / FLAG
    with _flag_lock(session_dir):
        if not os.path.lexists(path):
            return [], []
        try:
            data = _read_json(path)
        except ValueError:
            data = None
        names = _flagged_names(data, lane_rel)
        try:
            left = registry.unknown_now(project, lane, names, git_timeout=GIT_TIMEOUT)
        except registry.RegistryUnreadable as e:
            return [], [registry.unreadable_message(e)]
        if not left:
            _unlink(path)
            return [], []
        folders = [f"{lane_rel}/tasks/{n}" for n in left]
        if not isinstance(data, dict) or data.get("folders") != folders:
            _write_json(path, {"v": 1, "folders": folders, "since": _now()})
        return left, []


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
        if other != name and registry.task_number(other) == num and o.is_dir() and (o / "task.md").is_file():
            return f"its number belongs to {other}"
    return None


def _shown(s: str) -> str:
    return json.dumps(s, ensure_ascii=False) if any(ord(c) < 32 or ord(c) == 127 for c in s) else s


def _rel_from(cwd: str, target: Path) -> str:
    try:
        return os.path.relpath(target, cwd)
    except ValueError:
        return str(target)


def _hold_message(project: Path, lane: Path, left: "list[str]", cwd: str) -> str:
    lane_rel = _lane_rel(project, lane)
    out = ["BLOCKED: a task folder appeared during this session's shell command or Write, and the playbook CLI did "
           "not make it (the task registry does not name it and it is not committed):", ""]
    out += [f"  {_shown(f'{lane_rel}/tasks/{n}')}" for n in left]
    out += ["", "Only `.claude/bin/tasks new` makes task folders. This session's next actions wait until each folder "
            "above is made known or removed. Run ONE of these, alone in its command (written for the current "
            f"directory, {_shown(cwd)}):", ""]
    cli = shlex.quote(_rel_from(cwd, project / ".claude" / "bin" / "tasks"))
    for n in left:
        where = shlex.quote(_rel_from(cwd, lane / "tasks" / n))
        why_not = _adoptable(lane, n)
        out.append(f"  {_shown(f'{lane_rel}/tasks/{n}')}")
        if why_not is None:
            out.append(f"    keep it:    {cli} adopt {where}    (recommended — removing deletes its content)")
            out.append(f"    remove it:  rm -r {where}")
        else:
            out.append(f"    remove it:  rm -r {where}    (adopt cannot take it: {why_not})")
    out += ["", "`cd <dir>` alone is let through too. If you did not make it, another session working in this lane at "
            "the same time may have — adopt it rather than remove it. Read tools still work; other sessions are not "
            "held."]
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
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(notes)}},
                         ensure_ascii=False))


# ---------------------------------------------------------------- the modes

def _pre(mode: str, p: dict, project: Path, lane: Path, session_dir: Path, sid: str) -> int:
    tool = _str(p, "tool_name")
    cmd = _str(_dict(p, "tool_input"), "command")
    cwd = _str(p, "cwd")
    cwd = cwd if os.path.isabs(cwd) else str(project)
    if os.path.lexists(session_dir / FLAG):
        left, notes = _recheck(project, lane, session_dir)
        for n in notes:
            print(n, file=sys.stderr)
        if left:
            first = f"{_lane_rel(project, lane)}/tasks/{left[0]}"
            if tool == "Bash" and _is_way_out(cmd, cwd, project, lane, left):
                _journal(lane, "allow", "unknown task folder: the way out", sid, tool, first, cmd)
            else:
                _journal(lane, "block", "unknown task folder", sid, tool, first, cmd)
                print(_hold_message(project, lane, left, cwd), file=sys.stderr)
                return 2
    if mode == "pre-watch" and _registry_present(lane):
        _sweep_snapshots(session_dir)
        tuid = _str(p, "tool_use_id")
        name = f"{BEFORE}.{tuid}" if _TUID_RE.fullmatch(tuid) else BEFORE
        _write_json(session_dir / name, {"v": 1, "folders": _listing(lane), "registry": True})
    return 0


def _post(p: dict, project: Path, lane: Path, session_dir: Path, sid: str) -> int:
    tool = _str(p, "tool_name")
    ti = _dict(p, "tool_input")
    cmd = _str(ti, "command") or _str(ti, "file_path")
    event = _str(p, "hook_event_name")
    tuid = _str(p, "tool_use_id")
    before = session_dir / (f"{BEFORE}.{tuid}" if _TUID_RE.fullmatch(tuid) else BEFORE)
    try:
        snap = _read_json(before)
    except ValueError:
        snap = None
    _unlink(before)
    notes: "list[str]" = []
    if not _registry_present(lane):
        if isinstance(snap, dict) and snap.get("registry"):
            reason = "task registry removed during this call"
            _journal(lane, "record", reason, sid, tool, f"{_lane_rel(project, lane)}/{registry.REGISTRY_NAME}", cmd)
            notes.append(f"⚠ playbook: this call removed the lane's task registry ({registry.REGISTRY_NAME}): the "
                         "task-folder watch is off until a `tasks` creator writes a new one, which trusts every "
                         "folder then present.")
        _say(event, notes)
        return 0
    now = _listing(lane)
    if isinstance(snap, dict) and isinstance(snap.get("folders"), list):
        new = sorted(set(now) - set(f for f in snap["folders"] if isinstance(f, str)))
    else:
        new = now                             # no snapshot of this call: every unknown folder present (P4)
    if not new:
        return 0
    try:
        left = registry.unknown_now(project, lane, new, git_timeout=GIT_TIMEOUT) or []
    except registry.RegistryUnreadable as e:
        _journal(lane, "record", "task registry unreadable", sid, tool, "", cmd)
        notes.append("⚠ playbook: the task-folder watch could not read the task registry — "
                     + registry.unreadable_message(e))
        _say(event, notes)
        return 0
    if left:
        lane_rel = _lane_rel(project, lane)
        folders = [f"{lane_rel}/tasks/{n}" for n in left]
        with _flag_lock(session_dir):
            try:
                old = _read_json(session_dir / FLAG)
            except ValueError:
                old = None
            have = _flagged_names(old, lane_rel) or []
            merged = sorted(set(f"{lane_rel}/tasks/{n}" for n in have) | set(folders))
            _write_json(session_dir / FLAG, {"v": 1, "folders": merged, "since": _now()})
        for f in folders:
            _journal(lane, "record", "unknown task folder", sid, tool, f, cmd)
        notes.append("⚠ playbook: this call made task folder(s) the playbook CLI did not make: "
                     + ", ".join(_shown(f) for f in folders) + ". This session's next actions wait until each is "
                     "adopted (`.claude/bin/tasks adopt <it>`) or removed (`rm -r <it>`). Only `.claude/bin/tasks "
                     "new` makes task folders.")
    _say(event, notes)
    return 0


def main(argv: "list[str]") -> int:
    if len(argv) != 5:
        print("usage: task-dir-watch.py PROJECT AGENT_DIR SESSION_DIR SESSION_ID (the payload on stdin)",
              file=sys.stderr)
        return 64
    project, lane, session_dir, sid = Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4]
    p = _payload(sys.stdin.read())
    event, tool = _str(p, "hook_event_name"), _str(p, "tool_name")
    session_dir.mkdir(parents=True, exist_ok=True)
    if event in ("PostToolUse", "PostToolUseFailure"):
        return _post(p, project, lane, session_dir, sid) if tool in _WATCHED else 0
    if event == "PreToolUse":
        return _pre("pre-watch" if tool in _WATCHED else "pre-hold", p, project, lane, session_dir, sid)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
