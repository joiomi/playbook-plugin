"""The task registry — which task directories the CLI made (task 195, PLAN §S14 T5b).

B31's provenance, by the owner's answer of 2026-10-13 12:27 — "Registru scris de CLI, folder creat atomic
(Recomandat)": a directory under `tasks/` that the registry does not name is hand-made. Detecting one after a shell
command or a Write, and holding the session that made it, is task 196's watch (`scripts/task-dir-watch.py`); it and
the close read this module through `unknown_now`.

One file per lane, BESIDE `tasks/` — `.agent[/<lane>]/task-registry` — never inside it: about ten readers list
`tasks/` itself and must not meet anything that is not a task directory. It is MACHINE-LOCAL (gitignored with its lock
and the creators' temporary directories — task 195's plan panel, T1: tracked, every write would dirty the tree the
freshness fingerprint and the tamper guard read, and two branches would conflict). A clone starts without one; what it
brought is known by the git rule below. The format is read by bash with `cut -f1`:

    # playbook task-registry v1
    <directory name>\t<origin>\t<UTC time>

`origin` is `new`, `freehand`, `retro` (the creators), `adopt` (`tasks adopt`), `enrolled` (a directory that existed
when the lane's registry was first written) or `reserved` — a number a creator took for a directory it has not yet put
in place. A `reserved` line burns its number (never reused) and is NEVER membership: a crash between reserving and
publishing leaves a name nothing may claim (plan panel T3).

Every write happens under ONE per-lane lock (`task-registry.lock`, `filelock.named_lock`) — the lock each creator also
holds while it takes its number, so two creators never take one (measured before: six `tasks new` at once made five
tasks numbered 001; two `tasks freehand` shared one directory). A creator reserves, builds the directory complete under
a temporary name in the lane, renames it into `tasks/`, then writes its origin over the reservation: no reader ever
sees a half-made task.

Reads never write and never wait on the lock (plan panel T2): `read` and `is_known` only look; `unknown_now` tries
the lock SHARED without waiting, never creating its file, to tell a creation in progress from a crash's leftover. The writers — the creators,
`tasks adopt`, `prepare-merge`'s renames — enrol what exists when they find no registry. `is_known` also counts a
directory whose task.md is in the COMMITTED tree (`git ls-tree HEAD`, a regular blob — never the index: a staged
hand-made task.md is not provenance; plan panel T4).

The reader fails closed: a registry that is a link, not a regular file or not in this format raises
`RegistryUnreadable`, never "empty"; a creator then stops and names the repair (`unreadable_message`).
"""
from __future__ import annotations

import errno
import fcntl
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

HEADER = "# playbook task-registry v1"
REGISTRY_NAME = "task-registry"
LOCK_NAME = "task-registry.lock"
TEMP_PREFIX = ".task-creating."
# a task directory's name: its number, a dash, a slug — no whitespace, no separator, nothing bash cannot carry.
# Always `fullmatch`: `$` alone accepts a trailing newline, and a name with one would split its registry line (task
# 195, impl panel r2).
TASK_DIR_RE = re.compile(r"^(\d+)-[A-Za-z0-9][A-Za-z0-9._-]*$")
# what a folder under `tasks/` must look like to be a task folder at all — the CLI's own readers glob `NNN-*` — so a
# folder named outside the grammar is still listed as unknown (never registrable: removing it is the way out)
LOOSE_TASK_DIR_RE = re.compile(r"^\d+-")
ORIGINS = ("new", "freehand", "retro", "adopt", "enrolled", "reserved")


class RegistryUnreadable(Exception):
    """The registry is there but cannot be read as one (a link, not a regular file, another format)."""


class LaneUnreadable(Exception):
    """The lane's `tasks/` (or an entry of it) cannot be read — never taken for an empty one (task 196's impl panel r2
    J9, codex: a `tasks/` whose read bit was taken off listed as empty, and the close passed over a folder in it)."""


class AdoptRefused(Exception):
    """`tasks adopt` was given something that is not a task directory it may register."""


class NumberTaken(Exception):
    """A number given to `tasks new NNN-name` is not the next one, read under the lane lock."""

    def __init__(self, given: int, next_number: int):
        super().__init__(f"provided task number {given:03d} doesn't match next number {next_number:03d}")
        self.given, self.next_number = given, next_number


def registry_path(agent_dir) -> Path:
    return Path(agent_dir) / REGISTRY_NAME


def lock_path(agent_dir) -> Path:
    return Path(agent_dir) / LOCK_NAME


def task_number(name: str) -> "int | None":
    m = TASK_DIR_RE.fullmatch(name)
    return int(m.group(1)) if m else None


def unreadable_message(e: Exception) -> str:
    """What a creator says when the registry cannot be read (plan panel T11): the file, why, the repair."""
    return (f"playbook: the task registry cannot be read — {e}. Nothing was created: move it aside "
            f"(`mv <it> <it>.bad`) and run the command again — the next creator rebuilds it from the task directories.")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load(agent_dir) -> "list[list[str]] | None":
    """The registry's lines as [name, origin, time], in order; None when there is none. Raises RegistryUnreadable."""
    path = registry_path(agent_dir)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise RegistryUnreadable(f"{path}: {e}") from e
    if not stat.S_ISREG(st.st_mode):
        raise RegistryUnreadable(f"{path} is a link or not a regular file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise RegistryUnreadable(f"{path} is not a regular file")
            text = fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise RegistryUnreadable(f"{path}: {e}") from e
    lines = text.split("\n")
    if lines[0] != HEADER:
        raise RegistryUnreadable(f"{path}: not a task registry (its first line is {lines[0][:60]!r})")
    rows: "list[list[str]]" = []
    for n, line in enumerate(lines[1:], 2):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3 or not TASK_DIR_RE.fullmatch(parts[0]) or parts[1] not in ORIGINS:
            raise RegistryUnreadable(f"{path}:{n}: not a registry line")
        rows.append(parts)
    return rows


def read(agent_dir) -> "dict[str, str] | None":
    """{directory name: origin}, or None when the lane has no registry yet. Raises RegistryUnreadable. Never writes."""
    rows = _load(agent_dir)
    return None if rows is None else {r[0]: r[1] for r in rows}


def registered_numbers(agent_dir) -> "list[int]":
    """Every number the registry holds, `reserved` ones included (a burned number is never reused). Raises
    RegistryUnreadable; [] with no registry."""
    rows = _load(agent_dir) or []
    return [n for n in (task_number(r[0]) for r in rows) if n is not None]


def _committed_task_mds(project_path, agent_dir, names: "list[str] | None" = None,
                        timeout: float = 30.0) -> "set[str]":
    """The names of task directories whose task.md is a regular blob in HEAD (one `git ls-tree`). Empty outside git.
    The paths are given and read RELATIVE TO THE PROJECT (`-C <project>`, no `--full-tree`): a project may lie below
    its repository's top (task 195, impl panel r1 — `--full-tree` read them from the top and missed every one).
    `timeout` bounds each `git` run; the first one that does not answer ends the asking — what it would have proved
    stays unproven (task 196, plan panel P7: a hook the host kills at its deadline must not wait five times 30 s)."""
    if names is not None and not names:
        return set()                      # never `ls-tree` with no pathspec: that lists the whole tree (impl panel r2)
    try:
        rel = (Path(agent_dir) / "tasks").relative_to(project_path).as_posix()
    except ValueError:
        return set()
    paths = [f"{rel}/{n}/task.md" for n in names] if names is not None else [rel]
    out = b""
    # HEAD, and the commits a stopped merge, cherry-pick, revert or rebase is bringing in: their folders stand in the
    # tree before the commit that ends it (impl panel r2, opus). Commits only — never the index (plan panel T4).
    for ref in ("HEAD", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD"):
        try:
            r = subprocess.run(["git", "--no-replace-objects", "--literal-pathspecs", "-c", "core.quotepath=off",
                                "-C", str(project_path), "ls-tree", "-r", "-z", ref, "--", *paths],
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            break
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            out += r.stdout
    found = set()
    for rec in out.split(b"\0"):
        meta, _, path = rec.partition(b"\t")
        fields = meta.split()
        if len(fields) != 3 or fields[1] != b"blob" or fields[0] not in (b"100644", b"100755"):
            continue
        p = path.decode("utf-8", "surrogateescape")
        if not p.startswith(rel + "/") or not p.endswith("/task.md"):
            continue
        name = p[len(rel) + 1:-len("/task.md")]
        if "/" not in name and TASK_DIR_RE.fullmatch(name):
            found.add(name)
    return found


def is_known(project_path, agent_dir, name: str) -> bool:
    """True when the CLI made `name` (registered, not merely reserved) or git has COMMITTED its task.md. A pure read:
    no lock, no write. Raises RegistryUnreadable — what an unreadable registry means is the caller's to decide."""
    entries = read(agent_dir)
    if entries is not None and entries.get(name) not in (None, "reserved"):
        return True
    return name in _committed_task_mds(project_path, agent_dir, [name])


def unknown_task_dirs(project_path, agent_dir) -> "list[str]":
    """The task directories under `tasks/` that are not known (registered or committed) — for `tasks doctor` and the
    next task's detector. A pure read. Raises RegistryUnreadable."""
    tasks_dir = Path(agent_dir) / "tasks"
    try:
        names = sorted(n for n in os.listdir(tasks_dir) if LOOSE_TASK_DIR_RE.match(n))
    except OSError:
        return []
    entries = read(agent_dir) or {}
    left = [n for n in names if entries.get(n) in (None, "reserved")]
    if not left:
        return []
    committed = _committed_task_mds(project_path, agent_dir)
    return [n for n in left if n not in committed]


def _creating(agent_dir) -> "tuple[bool, int | None]":
    """(a creator holds the lane lock now, the descriptor holding it SHARED — or None). Opens the lock file read-only,
    never followed, never created; tries the shared lock without waiting. The caller closes the descriptor."""
    try:
        fd = os.open(lock_path(agent_dir), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return False, None                # no lock file: no creator has ever run, none is running
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return False, None
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return True, None
    except OSError:
        os.close(fd)
        return False, None
    return False, fd


def task_dir_names(tasks_dir) -> "list[str]":
    """The `<digits>-` DIRECTORIES under `tasks_dir`, sorted ([] when there is no `tasks_dir`). Raises LaneUnreadable
    when it cannot be listed or an entry cannot be examined."""
    tasks_dir = Path(tasks_dir)
    try:
        names = os.listdir(tasks_dir)
    except FileNotFoundError:
        return []
    except OSError as e:
        raise LaneUnreadable(f"{tasks_dir} cannot be listed: {e}") from e
    return sorted(n for n in names if LOOSE_TASK_DIR_RE.match(n) and _is_dir(tasks_dir / n))


def _is_dir(path: Path) -> bool:
    """A directory (a link to one counts — the CLI reads through it); gone → False; unexaminable → LaneUnreadable."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except FileNotFoundError:
        return False
    except OSError as e:
        if os.path.islink(path):
            return False                  # a dangling or looping link is no folder
        raise LaneUnreadable(f"{path} cannot be examined: {e}") from e


def unknown_now(project_path, agent_dir, names: "list[str] | None" = None, *,
                git_timeout: float = 30.0, no_registry: str = "none") -> "list[str] | None":
    """The task folders standing under the lane's `tasks/` (all of them, or those among `names`) that are not known
    — registered (a `reserved` line not counting) or committed (the git rule) — with a creation in progress left out:
    the registry is read holding the lane lock SHARED when it can be had at once (then no creator is between its
    rename and its registration), and while a creator holds it, a `reserved` folder is that creation (task 196, plan
    panel P14; read under the lock, a creator that registers and lets go between two reads cannot make a folder look
    hand-made). With no registry: None (`no_registry="none"`, the close — nothing is known by provenance then, plan
    panel P2), or what the git rule alone does not know (`"git"`, the watch — a call during which the registry vanished,
    a hold that outlives it: task 196's impl panel r1 I6). Only DIRECTORIES count (a file `12-notes.md` is no task
    folder — impl panel r1 I11). A name the git rule cannot answer within `git_timeout` is unknown. Never waits, never
    writes. Raises RegistryUnreadable, and LaneUnreadable when `tasks/` or an entry cannot be read (r2 J9)."""
    lane = Path(agent_dir)
    tasks_dir = lane / "tasks"
    if names is None:
        names = task_dir_names(tasks_dir)
    creating, fd = _creating(lane)
    try:
        entries = read(lane)
    finally:
        if fd is not None:
            os.close(fd)                  # lets the shared lock go
    if entries is None:
        if no_registry != "git":
            return None
        entries = {}
    left = [n for n in names
            if LOOSE_TASK_DIR_RE.match(n) and _is_dir(tasks_dir / n)
            and entries.get(n) in (None, "reserved") and not (creating and entries.get(n) == "reserved")]
    strict = [n for n in left if TASK_DIR_RE.fullmatch(n)]   # only such a name can be a committed task
    committed = _committed_task_mds(project_path, lane, strict, timeout=git_timeout) if strict else set()
    return [n for n in left if n not in committed]


# ---------------------------------------------------------------- writers (each under the lane lock)

def _write(agent_dir, rows: "list[list[str]]") -> None:
    from tasks.atomic import atomic_write
    atomic_write(registry_path(agent_dir), HEADER + "\n" + "".join("\t".join(r) + "\n" for r in rows))


def _enrolment(agent_dir) -> "list[list[str]]":
    """Every `NNN-slug` directory under `tasks/` that holds a regular task.md (none of them a link)."""
    tasks_dir = Path(agent_dir) / "tasks"
    rows = []
    try:
        names = sorted(os.listdir(tasks_dir))
    except OSError:
        return rows
    stamp = _now()
    for name in names:
        if not TASK_DIR_RE.fullmatch(name):
            continue
        d = tasks_dir / name
        tm = d / "task.md"
        if d.is_symlink() or not d.is_dir() or tm.is_symlink() or not tm.is_file():
            continue
        rows.append([name, "enrolled", stamp])
    return rows


def _rows_locked(agent_dir) -> "list[list[str]]":
    """The registry's rows, enrolling the lane's directories first when it has none. Hold the lane lock."""
    rows = _load(agent_dir)
    if rows is None:
        rows = _enrolment(agent_dir)
        _write(agent_dir, rows)
    return rows


def _sweep_temps_at(afd: int) -> None:
    """Remove every `.task-creating.*` of the lane held open as `afd`: under the lane lock each one is a dead
    creator's — no pid is looked at (plan panel T5: pid reuse). Through descriptors, never through a link."""
    from tasks.shared import _remove_tree_at
    try:
        with os.scandir(afd) as it:
            temps = [e.name for e in it if e.name.startswith(TEMP_PREFIX)]
        for name in temps:
            _remove_tree_at(afd, name)
    except OSError:
        pass


def _held(afd: int, agent_dir: Path) -> Path:
    """The lane held open as `afd`, as a path every path-taking helper resolves THROUGH the descriptor
    (`/proc/self/fd/<afd>`): the lock, the registry's read and write, the allocator's listing then all stay in the lane
    the creator opened, whatever is swapped in its path later (the single judge after round 3, codex, Critical,
    reproduced: the build went through the held descriptors but the registry was written by path — into a replacement
    lane's registry). Without /proc, the lane's path (the unanchored shape of before)."""
    p = Path(f"/proc/self/fd/{afd}")
    return p if p.is_dir() else agent_dir


def _final_path(tfd: int, agent_dir: Path, name: str) -> Path:
    """The created task's path: the lane's own path while its `tasks/` is still the directory held open as `tfd` — a
    lane reached through a link stays a path inside the project, as its callers print it (the single judge's run 2:
    the resolved path broke `tasks new` in a linked lane) — and, after a swap, where the held `tasks/` really is."""
    lexical = agent_dir / "tasks" / name
    try:
        if os.path.samestat(os.stat(agent_dir / "tasks"), os.fstat(tfd)):
            return lexical
        return Path(os.readlink(f"/proc/self/fd/{tfd}")) / name
    except OSError:
        return lexical


def _tasks_view(tfd: int, lane: Path) -> Path:
    """The held `tasks/` as a path the allocator lists THROUGH the descriptor (the single judge's run 2: it listed
    `tasks/` by name, so a swap of `tasks/` alone sent a retry's number to the replacement)."""
    p = Path(f"/proc/self/fd/{tfd}")
    return p if p.is_dir() else lane / "tasks"


class _Collision(Exception):
    """The final name stands under `tasks/` (task 195, impl panel r1: only THIS is a collision — a FileExistsError
    from a template's own read is an error, not a reason to take the next number)."""


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)


def _make_temp_at(afd: int) -> str:
    """A fresh temporary directory in the lane held open as `afd`; its name. A leftover of the same name (one the
    sweep could not remove) is stepped over, never an error (impl panel r1). The name does not carry the task's: a
    230-byte folder name must not make a temp name past the 255-byte limit (impl panel r2, sonnet)."""
    for k in range(100):
        name = f"{TEMP_PREFIX}{os.getpid()}.{k}"
        try:
            os.mkdir(name, dir_fd=afd)
            return name
        except FileExistsError:
            continue
    raise RuntimeError("no free temporary directory name in the lane")


def _write_task_md_at(afd: int, tmpname: str, text: str) -> None:
    """Write `<temp>/task.md` through the held lane — a new file in a directory only this creator knows (it becomes
    visible only by the rename), so no temp-and-replace is needed; flushed to disk before the rename."""
    dfd = os.open(tmpname, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=afd)
    try:
        fd = os.open("task.md", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                     0o666, dir_fd=dfd)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.fsync(dfd)        # its entry in the temp directory, before the rename publishes it (the single judge, run 1)
    finally:
        os.close(dfd)


def _exists_at(dfd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dfd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _publish(afd: int, tmpname: str, tfd: int, name: str) -> None:
    """Put the built directory in place, lane to `tasks/`, both held. (A test replaces this to kill the creator at
    exactly this point.)"""
    os.rename(tmpname, name, src_dir_fd=afd, dst_dir_fd=tfd)


def create_task_dir(project_path, agent_dir, slug: str, origin: str,
                    render: "Callable[[int, str], str]", number: "int | None" = None) -> Path:
    """Make `tasks/<NNN>-<slug>/task.md` the one way every creator does: under the lane lock, enrol if the lane has no
    registry, remove dead creators' temp directories, take the next number (`core._next_task_number`, which counts the
    registry's numbers too), reserve it, build the directory in the lane, rename it in, register its origin. A name
    that stands under `tasks/` when the built directory is to be renamed in is never published over (POSIX `rename`
    replaces an EMPTY directory: a hand-made one would be laundered; plan panel T6): that number stays reserved and the
    next is taken. `number`, when given (`tasks new NNN-name`), must be the next one, read under the lock, or
    NumberTaken is raised. Raises RegistryUnreadable before creating anything.

    The lane and its `tasks/` are opened ONCE, under the lock, and held through the build and its cleanup: the temp
    is made, its task.md written, the final name checked, the rename done and a failed build's temp removed all
    through those descriptors, so a swap of either path afterwards changes nothing this creator writes, renames or
    removes (task 195, impl panel r3 — codex, Critical, reproduced: a cleanup that reopened the lane by path deleted
    inside the directory a swap had put there)."""
    from tasks import core
    from tasks.filelock import named_lock
    agent_dir = Path(agent_dir)
    tasks_dir = agent_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    if origin not in ORIGINS or origin in ("reserved", "enrolled"):
        raise ValueError(f"not a creator's origin: {origin!r}")
    # a name the reader would refuse is never written (task 195, impl panel r1 — all five judges: an empty slug made
    # `001-`, and that one line stopped every later read of the lane's registry)
    if not TASK_DIR_RE.fullmatch(f"1-{slug}"):
        raise ValueError(f"no task folder name can be made from the slug {slug!r}")
    # The lane opened from the project root without following a link when it lies inside the project; a lane reached
    # through a link (a shared folder) is opened as it is and USED — but nothing in it is ever swept (the single judge's
    # run 2, codex, Critical, reproduced: a plain open of a linked `.agent` let the sweep delete `.task-creating.*`
    # folders in the link's target).
    from tasks.shared import _open_dir_inside
    afd = _open_dir_inside(Path(project_path), agent_dir)
    inside = afd is not None
    if afd is None:
        afd = os.open(agent_dir, _DIR_FLAGS)
    try:
        lane = _held(afd, agent_dir)
        with named_lock(lock_path(lane)):
            tfd = os.open("tasks", _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=afd)
            try:
                return _create_locked(afd, tfd, lane, agent_dir, slug, origin, render, number, core, sweep=inside)
            finally:
                os.close(tfd)
    finally:
        os.close(afd)


def _create_locked(afd: int, tfd: int, lane: Path, agent_dir: Path, slug: str, origin: str,
                   render: "Callable[[int, str], str]", number: "int | None", core, sweep: bool) -> Path:
    """`create_task_dir`'s body, under the lane lock, every step through the held lane (`afd`, `tfd`, `lane`)."""
    from tasks.shared import _remove_tree_at
    rows = _rows_locked(lane)
    if sweep:
        _sweep_temps_at(afd)
    for _attempt in range(1000):
        num = core._next_task_number(_tasks_view(tfd, lane), agent_dir=lane)
        if number is not None and number != num:
            raise NumberTaken(number, num)
        name = f"{num:03d}-{slug}"
        rows.append([name, "reserved", _now()])
        _write(lane, rows)
        tmpname = None
        try:
            tmpname = _make_temp_at(afd)
            _write_task_md_at(afd, tmpname, render(num, name))
            if _exists_at(tfd, name):              # stands there (made meanwhile): never published over
                raise _Collision()
            try:
                _publish(afd, tmpname, tfd, name)
            except OSError as e:
                # rename(2) onto a folder made meanwhile: EEXIST, or ENOTEMPTY for a full one (impl panel r1)
                if e.errno in (errno.EEXIST, errno.ENOTEMPTY):
                    raise _Collision() from e
                raise
        except _Collision:
            if tmpname is not None:
                _remove_tree_at(afd, tmpname)
            if number is not None:
                raise NumberTaken(number, num + 1)
            continue
        except BaseException:
            if tmpname is not None:
                _remove_tree_at(afd, tmpname)
            raise
        for d in (tfd, afd):                  # the moved entry, on both sides (the single judge, run 1)
            os.fsync(d)
        rows[-1] = [name, origin, rows[-1][2]]
        _write(lane, rows)
        return _final_path(tfd, agent_dir, name)
    raise RuntimeError("no free task number after 1000 tries")


def adopt(project_path, agent_dir, name: str) -> str:
    """Register a task directory the CLI did not make (`tasks adopt`). 'adopted', or 'known' when it already is
    (idempotent — plan panel T7). Raises AdoptRefused naming why, or RegistryUnreadable."""
    from tasks.filelock import named_lock
    agent_dir = Path(agent_dir)
    if not TASK_DIR_RE.fullmatch(name):
        raise AdoptRefused(f"{name!r} is not a task directory name (<number>-<slug>)")
    d = agent_dir / "tasks" / name
    tm = d / "task.md"
    num = task_number(name)
    tasks_dir = agent_dir / "tasks"
    with named_lock(lock_path(agent_dir)):
        # checked UNDER the lock: a folder `prepare-merge` renames while adopt waits is not registered by its old name
        # (impl panel r2, codex)
        if d.is_symlink():
            raise AdoptRefused(f"{d} is a link — only a real directory is adopted")
        if not d.is_dir():
            raise AdoptRefused(f"no directory {d}")
        if tm.is_symlink() or not tm.is_file():
            raise AdoptRefused(f"{tm} is missing or not a regular file — a task directory holds its task.md")
        rows = _rows_locked(agent_dir)
        mine = [r for r in rows if r[0] == name]
        if mine and mine[0][1] != "reserved":
            return "known"
        # The number is another folder's when another FOLDER holds it — registered, committed or not (task 195,
        # impl panel r1: the rows alone missed a committed `005-x` and refused a renamed folder whose old row stayed).
        for other in sorted(os.listdir(tasks_dir)):
            d = tasks_dir / other
            if (other != name and task_number(other) == num and not d.is_symlink() and d.is_dir()
                    and not (d / "task.md").is_symlink() and (d / "task.md").is_file()):
                raise AdoptRefused(f"task number {num:03d} already belongs to {other}")
        # a row of this number whose folder is gone is the renamed folder's old name — dropped
        rows = [r for r in rows if not (r[0] != name and r[1] != "reserved" and task_number(r[0]) == num
                                        and not os.path.lexists(tasks_dir / r[0]))]
        mine = [r for r in rows if r[0] == name]
        if mine:
            mine[0][1] = "adopt"
        else:
            rows.append([name, "adopt", _now()])
        _write(agent_dir, rows)
    return "adopted"


def ready_for_renames(agent_dir, new_names=()) -> None:
    """Before `prepare-merge` moves any folder: read the registry (RegistryUnreadable now, before anything moved —
    task 195, impl panel r1), enrol the lane as it stands when it has none, refuse a target name another row already
    holds, and drop a crashed creator's `reserved` row of a target name (impl panel r2, fable: two rows of one name,
    the later reserved one winning). Call it holding the lane lock, which the renumbering keeps until its last rename."""
    rows = _rows_locked(agent_dir)
    targets = set(new_names)
    taken = [r[0] for r in rows if r[0] in targets and r[1] != "reserved"]
    if taken:
        raise RegistryUnreadable(f"the task registry already names {taken[0]}, a folder prepare-merge would rename to")
    kept = [r for r in rows if not (r[0] in targets and r[1] == "reserved")]
    if len(kept) != len(rows):
        _write(agent_dir, kept)


def rename(project_path, agent_dir, old: str, new: str) -> None:
    """A registered directory renamed by the CLI (`prepare-merge`) keeps its line under the new name (plan panel T8);
    one known only by the git rule gets a row under the new name. Enrols first when the lane has no registry; a
    `reserved` row of the new name is dropped; an unknown `old` stays unknown."""
    from tasks.filelock import named_lock
    with named_lock(lock_path(agent_dir)):
        rows = [r for r in _rows_locked(agent_dir) if not (r[0] == new and r[1] == "reserved")]
        moved = False
        for r in rows:
            if r[0] == old:
                r[0] = new
                moved = True
        if not moved and old in _committed_task_mds(project_path, agent_dir, [old]):
            # known only by the git rule (pulled in after the registry existed): after `git mv` its new path is staged,
            # not committed — it gets a row (impl panel r2, gemini, fable, opus)
            rows.append([new, "enrolled", _now()])
        _write(agent_dir, rows)
