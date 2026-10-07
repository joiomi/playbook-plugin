#!/usr/bin/env python3
"""The write log: after an Edit/Write, append a copy of the edited file to a log
OUTSIDE the project — `~/.local/share/playbook/<project-slug>/write_log` — so the
last content an agent wrote can be recovered when the project copy is lost.

    write_log.py <log_dir> <project_dir>      (the hook's JSON on stdin)

Bounded (task 141, owner decision Q8 of 2026-10-07; gauntlet 2 G2-15 — it grew
without limit, 135 MB for one project, and nothing reads it):

  * a file over MAX_FILE (1 MB) is named in the log, not copied;
  * when the next entry would take the log past ROTATE_AT (10 MB) the log is renamed
    to `write_log.1`, replacing the previous one — at most two files of 10 MB each;
  * a log that predates this cap is renamed aside ONCE, to
    `write_log.pre-cap-<UTC>-<pid>`, and never rotated or deleted: nothing that
    existed before the cap is lost to it. `write_log.capped` marks that this ran;
  * `"write_log": false` in the project's `.agent/config.json` turns the log off.

The log may hold secrets (an edited `.env` is copied like any other file): the
directory is made 0700 and every log file 0600 (impl panel r1 — they were 0644). It
is the user's to delete at any time.

Parking, rotation and the append run under one lock per log directory
(`write_log.lock`; impl panel r1: two hooks at once could rotate twice and drop the
old file early, or park a NEW entry as pre-cap). The wait is bounded (LOCK_WAIT):
a lock that is never released costs that wait and this one copy, never the tool call.

Best-effort and silent: a hook helper must never fail the tool call. Exit 0 always.
"""
from __future__ import annotations

import errno
import json
import os
import sys
import time

MAX_FILE = 1024 * 1024
ROTATE_AT = 10 * 1024 * 1024
LOG_NAME = "write_log"
MARKER = "write_log.capped"
LOCK_NAME = "write_log.lock"
# well inside the state-echo hook's 5 s timeout (hooks.json); an append or a rotation
# (a rename) holds the lock for milliseconds
LOCK_WAIT = 1.5
STALE_LOCK = 30.0


class _DirLock:
    """An exclusive advisory lock on `<log_dir>/write_log.lock`: flock on POSIX,
    msvcrt on Windows. `acquired` is False after LOCK_WAIT seconds without it."""

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        fd = os.open(os.path.join(log_dir, LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
        self.fh = os.fdopen(fd, "r+b")
        self.acquired = False
        self.unsupported = False
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                return
            except OSError as exc:
                # only CONTENTION waits; a filesystem without lock support (ENOLCK on
                # NFS without lockd, EOPNOTSUPP/ENOSYS on some FUSE mounts) means no lock
                # at all — go on unlocked rather than stall every edit and log nothing
                # (impl panel r2)
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES, errno.EDEADLK):
                    # no OS lock here: the atomic-mkdir fallback (post-D6 run 1 — unlocked
                    # hooks could both decide an entry fits and pass the cap together)
                    self.unsupported = True
                    self.acquired = self._mkdir_lock(deadline)
                    return
                if time.monotonic() >= deadline:
                    return
                time.sleep(0.05)

    def _mkdir_lock(self, deadline: float) -> bool:
        """`write_log.lock.d` as the lock: mkdir is atomic on every filesystem. One left
        by a killed hook goes stale after STALE_LOCK seconds (the section it guards takes
        milliseconds) and is cleared."""
        self.lockdir = os.path.join(self.log_dir, LOCK_NAME + ".d")
        while True:
            try:
                os.mkdir(self.lockdir, 0o700)
                return True
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.lockdir) > STALE_LOCK:
                        os.rmdir(self.lockdir)
                        continue
                except OSError:
                    pass
            except OSError:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def release(self) -> None:
        if self.unsupported:
            if self.acquired:
                try:
                    os.rmdir(self.lockdir)
                except OSError:
                    pass
            self.fh.close()
            return
        try:
            if self.acquired:
                if os.name == "nt":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.fh.close()


def _private(log_dir: str) -> None:
    """0700 on the directory, 0600 on every file in it (no-ops on Windows)."""
    try:
        os.chmod(log_dir, 0o700)
        for name in os.listdir(log_dir):
            path = os.path.join(log_dir, name)
            if os.path.isfile(path) and not os.path.islink(path):
                os.chmod(path, 0o600)
    except OSError:
        pass


_OFF = {"false", "off", "no", "0"}


def _enabled(project_dir: str) -> bool:
    """On unless the project's config turns it off: `"write_log": false` — or the same
    written as text (`"false"`, `"off"`, `"no"`, `"0"`, `0`; impl panel r2: a quoted
    false left the copies running). No config file = on, the default. A config file
    that exists but cannot be read or parsed = OFF: it may be a half-written
    `{"write_log": false}`, and copying a secret is the wrong side to fail on."""
    path = os.path.join(project_dir, ".agent", "config.json")
    # opened directly (post-D6 run 1): exists() is also False for a config that cannot
    # be REACHED (an untraversable `.agent`) — only a missing file is the default "on"
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        # truly absent = the default "on"; a dangling symlink there is a config that
        # exists and cannot be read (post-D6 run 2) — off
        return not os.path.lexists(path)
    except (OSError, ValueError):
        return False
    if not isinstance(cfg, dict):
        return True
    value = cfg.get("write_log", True)
    if value is False or (type(value) is int and value == 0):
        return False
    return not (isinstance(value, str) and value.strip().lower() in _OFF)


def _park_a_log_that_predates_the_cap(log_dir: str) -> None:
    """Once per log directory, under the directory lock. The rename comes BEFORE the
    marker and its name is unique (time + pid): a parked log is never overwritten."""
    marker = os.path.join(log_dir, MARKER)
    if os.path.exists(marker):
        return
    log = os.path.join(log_dir, LOG_NAME)
    if os.path.exists(log):
        # under the directory lock: no other hook can append to the log in between
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        try:
            os.rename(log, f"{log}.pre-cap-{stamp}-{os.getpid()}")
        except OSError:
            return                      # could not park it: leave everything as it is
    try:
        os.close(os.open(marker, os.O_WRONLY | os.O_CREAT, 0o600))
    except OSError:
        pass


def _rotate(log_dir: str, incoming: int) -> bool:
    """Rotate when the log plus the entry about to be appended would pass ROTATE_AT,
    so the live log never does (impl panel r2: the check came before the append and
    a 1 MB entry took the log to ~11 MB). False = a rotation was needed and failed:
    the caller skips this copy rather than take the log past the cap (impl panel r2
    re-run — both codex seats reproduced it)."""
    log = os.path.join(log_dir, LOG_NAME)
    try:
        size = os.path.getsize(log)
    except FileNotFoundError:
        return True                     # no log yet: nothing to rotate
    except OSError:
        return False                    # cannot tell how full it is: skip (post-D6 run 1)
    if size + incoming <= ROTATE_AT:
        return True
    try:
        os.replace(log, log + ".1")
    except OSError:
        return False
    return True


def main(argv: "list[str]") -> int:
    if len(argv) < 3:
        return 0
    log_dir, project_dir = argv[1], argv[2]
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        tool_input = data.get("tool_input") if isinstance(data, dict) else None
        file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    except ValueError:
        return 0
    if isinstance(file_path, str) and file_path and not os.path.isabs(file_path):
        file_path = os.path.normpath(os.path.join(project_dir, file_path))   # the project's
    if not isinstance(file_path, str) or not file_path or not os.path.isfile(file_path):
        return 0
    if not _enabled(project_dir):
        return 0
    try:
        os.makedirs(log_dir, mode=0o700, exist_ok=True)
        _private(log_dir)
        lock = _DirLock(log_dir)
    except OSError:
        return 0
    try:
        if lock.acquired:
            _append_locked(log_dir, file_path)
    finally:
        lock.release()
    return 0


def _append_locked(log_dir: str, file_path: str) -> None:
    try:
        _park_a_log_that_predates_the_cap(log_dir)
        if not os.path.exists(os.path.join(log_dir, MARKER)):
            return                      # the old log could not be parked: do not grow it
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # ONE bounded read decides (never the stat alone: the file can grow after it)
        with open(file_path, "rb") as fh:
            body = fh.read(MAX_FILE + 1)
        if len(body) > MAX_FILE:
            size = os.path.getsize(file_path)   # how big it is — it was not copied
            entry = (f"=== {stamp} {file_path} ({size} bytes) — not copied: over 1 MB ===\n"
                     ).encode("utf-8", "surrogateescape")
        else:
            # the header counts the bytes copied (impl panel r2: a stat taken before the
            # read could disagree with them)
            entry = (f"=== {stamp} {file_path} ({len(body)} bytes) ===\n"
                     ).encode("utf-8", "surrogateescape") + body + b"\n"
        if not _rotate(log_dir, len(entry)):
            return                      # over the cap and cannot rotate: skip this copy
        fd = os.open(os.path.join(log_dir, LOG_NAME), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "ab") as out:
            out.write(entry)
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv))
