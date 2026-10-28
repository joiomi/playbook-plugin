"""The one atomic-write primitive for the tasks package.

Every consequence-relevant file write (task.md, models.json, hook config,
counters) should route through `atomic_write` rather than a plain
`write_text`/`open(..., "w")`. A plain writer truncates then writes, so a
concurrent reader — a second user, a hook, `tasks status` — can observe an
EMPTY or half-written file in the window between truncate and write, and a
crash mid-write leaves the file permanently truncated.

`atomic_write` closes both: it writes a temp file IN THE SAME DIRECTORY as the
target (so the final `os.replace` is a rename within one filesystem, which is
atomic), fsyncs the temp before the rename when durable
consequence warrants it, preserves the target's permission bits across the
rename, and unlinks the temp on any failure so an interrupt never litters
`.tmp` files.

Why `os.replace` and not `Path.rename`: `os.replace` overwrites an existing
target atomically everywhere, and concurrent read+write of the SAME path is
torn-read-safe on Linux (tests/test_atomic_write_torn_read.py). (Up to 1.5.47
Windows was also supported, where `rename` onto an existing target raises and a
file held open cannot be replaced.)

Stdlib only; no dependency on any other tasks module, so every module
(including tasks.core) can import it without a cycle.
"""

from __future__ import annotations

import codecs
import os
import stat
import tempfile
from pathlib import Path
from typing import Union


class NotUtf8Error(Exception):
    """A text write refused because the file it would replace is not valid in its encoding (task 335, grave B of
    DESIGN-PLAYBOOK-2.md §7). Read leniently, such a byte became U+FFFD and was written back, so the byte someone wrote
    was gone. Raised before anything is written: the file stays byte-identical. Not a `ValueError` or an `OSError`, so
    the callers' `except ValueError` / `except OSError` cannot swallow the refusal; `cli.main` prints it."""


def _not_decodable(p: Path, raw: bytes, encoding: str, err: UnicodeDecodeError) -> NotUtf8Error:
    return NotUtf8Error(
        f"{p} is not valid {encoding.upper() if encoding.lower().startswith('utf') else encoding} "
        f"(byte {err.start}: 0x{raw[err.start]:02x}); nothing was written. Convert the file "
        "(for example `iconv -f cp1252 -t utf-8`) and run the command again.")


def _refuse_if_not_decodable(p: Path, encoding: str) -> None:
    """Refuse a text write over an existing regular file whose bytes do not decode in `encoding`. A missing, unreadable
    or non-regular target keeps the write's old behavior (no read of a FIFO, no new refusal for a permission)."""
    if codecs.lookup(encoding).name not in ("utf-8", "utf-8-sig"):
        return
    try:
        if not stat.S_ISREG(os.stat(p).st_mode):
            return
        raw = p.read_bytes()
    except OSError:
        return
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise _not_decodable(p, raw, "utf-8", err) from None


def read_text_strict(path: Union[str, "os.PathLike[str]"], encoding: str = "utf-8") -> str:
    """A record's text read for a rewrite (task 335): its bytes decoded strictly, no newline translation (as
    `newline=""`), `NotUtf8Error` when they do not decode. For a command that writes elsewhere before it rewrites the
    record (`tasks compact`'s archive), so it refuses before its first write."""
    p = Path(path)
    raw = p.read_bytes()
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError as err:
        raise _not_decodable(p, raw, encoding, err) from None


def atomic_write(
    path: Union[str, "os.PathLike[str]"],
    data: Union[str, bytes, bytearray],
    *,
    encoding: str = "utf-8",
    newline: "str | None" = None,
    fsync: bool = True,
) -> None:
    """Write `data` to `path` all-or-nothing via a same-directory temp + os.replace.

    `data` may be text (written with `encoding`/`newline`) or bytes (written
    verbatim; `encoding`/`newline` are then ignored). `newline=""` disables
    newline translation, preserving a CRLF file byte-for-byte — the rest of the
    tasks package uses the default (`None`, i.e. `\\n` → `os.linesep` on write).

    `fsync=True` (default) flushes and fsyncs the temp file before the rename so
    the bytes are durable across a power loss, not merely a process crash; the
    parent directory is fsynced best-effort on POSIX so the rename itself
    survives too. Pass `fsync=False` only for a hot path where the atomicity
    guarantee matters but power-loss durability does not.

    Permission bits: if `path` already exists, its mode is preserved across the
    replace (a plain mkstemp temp is 0600, which would otherwise silently strip
    group/other read from a shared config on every rewrite); a brand-new file
    gets the normal umask-masked mode, never mkstemp's private 0600.

    On any failure the temp file is removed before the exception propagates, so
    an interrupt between write and replace leaves neither a torn target nor a
    stray temp.

    Text over an existing file that is not valid UTF-8 is refused (`NotUtf8Error`, task 335) before any temp file is
    made: replacing it would lose the bytes that no decoder read. Bytes data is written as before.
    """
    p = Path(path)
    parent = p.parent
    is_bytes = isinstance(data, (bytes, bytearray))
    if not is_bytes:
        _refuse_if_not_decodable(p, encoding)

    # Capture the target's current mode BEFORE we create the temp, so a rewrite
    # preserves it (see docstring). None means "new file".
    try:
        prev_mode: "int | None" = os.stat(p).st_mode & 0o777
    except OSError:
        prev_mode = None

    fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=p.name + ".", suffix=".tmp")
    try:
        if is_bytes:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                if fsync:
                    os.fsync(fh.fileno())
        else:
            with os.fdopen(fd, "w", encoding=encoding, newline=newline) as fh:
                fh.write(data)
                fh.flush()
                if fsync:
                    os.fsync(fh.fileno())

        if prev_mode is not None:
            os.chmod(tmp, prev_mode)
        else:
            _umask = os.umask(0)
            os.umask(_umask)
            os.chmod(tmp, 0o666 & ~_umask)

        os.replace(tmp, str(p))

        if fsync:
            # Best-effort: fsync the directory so the rename entry itself is
            # durable. Not guaranteed (some filesystems reject O_DIRECTORY
            # fsync) — a failure here
            # never invalidates the already-completed atomic replace.
            try:
                dir_fd = os.open(str(parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    except BaseException:
        # BaseException (not just OSError/Exception): a KeyboardInterrupt landing
        # between the write and the replace is exactly the interruption this
        # primitive exists to survive — clean up the temp before re-raising.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def rewrite(
    path: Union[str, "os.PathLike[str]"],
    transform,
    *,
    encoding: str = "utf-8",
    newline: "str | None" = None,
    fsync: bool = True,
    timeout: "float | None" = None,
) -> "str | None":
    """The read-transform-write TRANSACTION for a task's records (task 058).

    `atomic_write` makes each WRITE all-or-nothing; this makes the whole
    read → transform → write indivisible against another process, which is the
    gap that actually bit: the close reads task.md, runs the verify contract for
    minutes, then writes the receipt, and a `tasks blocked` landing in that
    window was silently lost (measured 5/5, with the `## Blocked` record gone).

    `transform(text) -> str | None` may:
      * return new text → it is written atomically and returned;
      * return `None` → the write is DECLINED, the file is byte-identical, and
        `None` is returned (the `_set_status`-returns-False shape: "nothing to
        write" is an outcome, not an error);
      * raise → the file is byte-identical and the exception propagates.

    The lock is re-entrant, so a caller that already holds `task_lock(path)` can
    compose several `rewrite` calls into ONE transaction — which is how a
    receipt and a status change land together or not at all. A missing file is
    read as empty text, so a transform can create it.
    """
    from tasks.filelock import task_lock

    p = Path(path)
    # `timeout=None` lets filelock apply its own default (and the
    # PLAYBOOK_LOCK_TIMEOUT_SECS override); passing the constant here bypassed it.
    with task_lock(p, timeout=timeout):
        try:
            # no newline translation (task 214, T4): a lone CR inside a line stays there — `Path.read_text`'s newline translation made it a
            # line break that every transform then wrote back as one; CRLF still becomes LF, as before
            raw = p.read_bytes()
        except FileNotFoundError:
            text = ""
        else:
            # strict (task 335): read with errors="replace", a byte that is not UTF-8 became U+FFFD and the transform's
            # text wrote that back — refuse before the transform runs; the file stays byte-identical
            try:
                text = raw.decode(encoding).replace("\r\n", "\n")
            except UnicodeDecodeError as err:
                raise _not_decodable(p, raw, encoding, err) from None
        new = transform(text)
        if new is None:
            return None
        atomic_write(p, new, encoding=encoding, newline=newline, fsync=fsync)
        return new
