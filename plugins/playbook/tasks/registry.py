"""The task registry — which task directories the CLI made (task 195, PLAN §S14 T5b).

B31's provenance, by the owner's answer of 2026-10-13 12:27 — "Registru scris de CLI, folder creat atomic
(Recomandat)": a directory under `tasks/` that the registry does not name is hand-made. Detecting one after a shell
command, and what then, is the next task's (the detector); this module is what it reads.

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

Reads never write and never lock (plan panel T2): `read` and `is_known` only look. The writers — the creators,
`tasks adopt`, `prepare-merge`'s renames — enrol what exists when they find no registry. `is_known` also counts a
directory whose task.md is in the COMMITTED tree (`git ls-tree HEAD`, a regular blob — never the index: a staged
hand-made task.md is not provenance; plan panel T4).

The reader fails closed: a registry that is a link, not a regular file or not in this format raises
`RegistryUnreadable`, never "empty"; a creator then stops and names the repair (`unreadable_message`).
"""
from __future__ import annotations

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
# a task directory's name: its number, a dash, a slug — no whitespace, no separator, nothing bash cannot carry
TASK_DIR_RE = re.compile(r"^(\d+)-[A-Za-z0-9][A-Za-z0-9._-]*$")
ORIGINS = ("new", "freehand", "retro", "adopt", "enrolled", "reserved")


class RegistryUnreadable(Exception):
    """The registry is there but cannot be read as one (a link, not a regular file, another format)."""


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
    m = TASK_DIR_RE.match(name)
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
        if len(parts) != 3 or not TASK_DIR_RE.match(parts[0]) or parts[1] not in ORIGINS:
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


def _committed_task_mds(project_path, agent_dir, names: "list[str] | None" = None) -> "set[str]":
    """The names of task directories whose task.md is a regular blob in HEAD (one `git ls-tree`). Empty outside git."""
    try:
        rel = (Path(agent_dir) / "tasks").relative_to(project_path).as_posix()
    except ValueError:
        return set()
    paths = [f"{rel}/{n}/task.md" for n in names] if names is not None else [rel]
    try:
        r = subprocess.run(["git", "--no-replace-objects", "-c", "core.quotepath=off", "-C", str(project_path),
                            "ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", *paths],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    if r.returncode != 0:
        return set()
    found = set()
    for rec in r.stdout.split(b"\0"):
        meta, _, path = rec.partition(b"\t")
        fields = meta.split()
        if len(fields) != 3 or fields[1] != b"blob" or fields[0] not in (b"100644", b"100755"):
            continue
        p = path.decode("utf-8", "surrogateescape")
        if not p.startswith(rel + "/") or not p.endswith("/task.md"):
            continue
        name = p[len(rel) + 1:-len("/task.md")]
        if "/" not in name and TASK_DIR_RE.match(name):
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
        names = sorted(n for n in os.listdir(tasks_dir) if TASK_DIR_RE.match(n))
    except OSError:
        return []
    entries = read(agent_dir) or {}
    left = [n for n in names if entries.get(n) in (None, "reserved")]
    if not left:
        return []
    committed = _committed_task_mds(project_path, agent_dir)
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
        if not TASK_DIR_RE.match(name):
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


def _sweep_temps_locked(project_path, agent_dir) -> None:
    """Remove every `.task-creating.*` in the lane: under the lane lock each one is a dead creator's — no pid is looked
    at (plan panel T5: pid reuse). Through descriptors, never through a link (task 194's remover)."""
    from tasks.shared import _open_dir_inside, _remove_tree_at
    afd = _open_dir_inside(Path(project_path), Path(agent_dir))
    if afd is None:
        return
    try:
        with os.scandir(afd) as it:
            temps = [e.name for e in it if e.name.startswith(TEMP_PREFIX)]
        for name in temps:
            _remove_tree_at(afd, name)
    except OSError:
        pass
    finally:
        os.close(afd)


def _publish(tmp: Path, final: Path) -> None:
    """Put the built directory in place. (A test replaces this to kill the creator at exactly this point.)"""
    os.rename(tmp, final)


def create_task_dir(project_path, agent_dir, slug: str, origin: str,
                    render: "Callable[[int, str], str]", number: "int | None" = None) -> Path:
    """Make `tasks/<NNN>-<slug>/task.md` the one way every creator does: under the lane lock, enrol if the lane has no
    registry, remove dead creators' temp directories, take the next number (`core._next_task_number`, which counts the
    registry's numbers too), reserve it, build the directory in the lane, rename it in, register its origin. A name
    that stands under `tasks/` when the built directory is to be renamed in is never published over (POSIX `rename`
    replaces an EMPTY directory: a hand-made one would be laundered; plan panel T6): that number stays reserved and the
    next is taken. `number`, when given (`tasks new NNN-name`), must be the next one, read under the lock, or
    NumberTaken is raised. Raises RegistryUnreadable before creating anything."""
    from tasks import core
    from tasks.atomic import atomic_write
    from tasks.filelock import named_lock
    agent_dir = Path(agent_dir)
    tasks_dir = agent_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    if origin not in ORIGINS or origin in ("reserved", "enrolled"):
        raise ValueError(f"not a creator's origin: {origin!r}")
    with named_lock(lock_path(agent_dir)):
        rows = _rows_locked(agent_dir)
        _sweep_temps_locked(project_path, agent_dir)
        for _attempt in range(1000):
            num = core._next_task_number(tasks_dir)
            if number is not None and number != num:
                raise NumberTaken(number, num)
            name = f"{num:03d}-{slug}"
            rows.append([name, "reserved", _now()])
            _write(agent_dir, rows)
            final = tasks_dir / name
            tmp = agent_dir / f"{TEMP_PREFIX}{os.getpid()}.{name}"
            os.mkdir(tmp)
            try:
                atomic_write(tmp / "task.md", render(num, name))
                if os.path.lexists(final):                 # stands there (made meanwhile): never published over
                    raise FileExistsError(final)
                _publish(tmp, final)
            except FileExistsError:
                _sweep_temps_locked(project_path, agent_dir)
                if number is not None:
                    raise NumberTaken(number, num + 1)
                continue
            except BaseException:
                _sweep_temps_locked(project_path, agent_dir)
                raise
            rows[-1] = [name, origin, rows[-1][2]]
            _write(agent_dir, rows)
            return final
    raise RuntimeError("no free task number after 1000 tries")


def adopt(project_path, agent_dir, name: str) -> str:
    """Register a task directory the CLI did not make (`tasks adopt`). 'adopted', or 'known' when it already is
    (idempotent — plan panel T7). Raises AdoptRefused naming why, or RegistryUnreadable."""
    from tasks.filelock import named_lock
    agent_dir = Path(agent_dir)
    if not TASK_DIR_RE.match(name):
        raise AdoptRefused(f"{name!r} is not a task directory name (<number>-<slug>)")
    d = agent_dir / "tasks" / name
    tm = d / "task.md"
    if d.is_symlink():
        raise AdoptRefused(f"{d} is a link — only a real directory is adopted")
    if not d.is_dir():
        raise AdoptRefused(f"no directory {d}")
    if tm.is_symlink() or not tm.is_file():
        raise AdoptRefused(f"{tm} is missing or not a regular file — a task directory holds its task.md")
    num = task_number(name)
    with named_lock(lock_path(agent_dir)):
        rows = _rows_locked(agent_dir)
        mine = [r for r in rows if r[0] == name]
        if mine and mine[0][1] != "reserved":
            return "known"
        other = [r[0] for r in rows if r[0] != name and r[1] != "reserved" and task_number(r[0]) == num]
        if other:
            raise AdoptRefused(f"task number {num:03d} already belongs to {other[0]}")
        if mine:
            mine[0][1] = "adopt"
        else:
            rows.append([name, "adopt", _now()])
        _write(agent_dir, rows)
    return "adopted"


def rename(project_path, agent_dir, old: str, new: str) -> None:
    """A registered directory renamed by the CLI (`prepare-merge`) keeps its line under the new name (plan panel T8).
    Enrols first when the lane has no registry; an unregistered `old` stays unregistered."""
    from tasks.filelock import named_lock
    with named_lock(lock_path(agent_dir)):
        rows = _rows_locked(agent_dir)
        for r in rows:
            if r[0] == old:
                r[0] = new
        _write(agent_dir, rows)
