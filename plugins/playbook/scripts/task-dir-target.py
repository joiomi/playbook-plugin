#!/usr/bin/env python3
"""Is a FILE path inside the project, or may it be? Guard 0 of task-gate-hook asks it (task 171).

Usage:   printf '%s' <file path> | PB_PROJECT=<project root> python3 task-dir-target.py --path
         printf '%s' <file path> | PB_PROJECT=<project root> python3 task-dir-target.py --creates
`--path`     exit 0: outside the project under every reading; 1: inside, or may be.
`--creates`  exit 0: a Write of it creates nothing inside the project; 1: it does, or may.
No mode      exit 2 (usage). Task 197 removed the Bash mode — the pre-run check that judged a shell command's TEXT
             for a task directory (tasks 080-110): a folder made by hand is caught after the call now, by
             task-dir-watch-hook (ledger PB-TASK-DIR-WATCH; the owner's answer of 2026-10-13 12:27).
Other    (a crash) — the hook decides as it did before it had a scope.

A path is inside when ANY of these says so (so no single view can let it out): its `..`-collapsed spelling under the
project's spelling or its realpath; the realpath of the raw path (kernel `..` semantics) or of the collapsed one under
the project's realpath (symlinks into any subdirectory); or `samefile` between the project and an existing ancestor of
either realpath (case variants on a case-insensitive disk).
"""
from __future__ import annotations

import os
import posixpath
import sys


def _canon(path: str) -> str:
    """Lexical form: forward slashes and `..` collapsed."""
    return posixpath.normpath(path.replace("\\", "/"))


def _is_absolute(token: str) -> bool:
    return token.replace("\\", "/").startswith("/")


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _lexically_inside(path: str, project: str) -> bool:
    return _under(_canon(path), _canon(project))


def _existing_ancestor(path: str) -> str | None:
    probe = path
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent
    return probe


def _physically_inside(path: str, project: str) -> bool:
    proj_real = _canon(os.path.realpath(project))
    candidates = {os.path.realpath(path),
                  os.path.realpath(posixpath.normpath(path.replace("\\", "/")))}
    for cand in candidates:
        if _under(_canon(cand), proj_real):
            return True
        probe = _existing_ancestor(cand)
        while probe is not None:
            try:
                if os.path.samefile(probe, project):
                    return True
            except OSError:
                pass
            parent = os.path.dirname(probe)
            probe = None if parent == probe else parent
    return False


def path_may_be_inside(path: str, project: str) -> bool:
    """`--path` (task 171, Guard 0): is this FILE path inside the project, or may it be?
    The same inside test a task-directory target gets — lexical or physical — and the
    same answer for a path that cannot be placed against the project (relative, `~`)."""
    if not path or path.startswith("~") or not _is_absolute(path):
        return True
    return _lexically_inside(path, project) or _physically_inside(path, project)


def write_may_create_inside(path: str, project: str) -> bool:
    """`--creates` (task 171, Guard 0): would a `Write` of this path CREATE a file inside
    the project — or may it? The tool that opens the path is not ours to know, so the
    path is read both ways: as the kernel reads it (links followed, then `..`; a missing
    directory is taken as it will be made) and as text (collapsed first). A creation
    inside the project under EITHER reading counts; a file that exists is a rewrite. So
    `<link into the project>/../.agent/tasks/N/task.md` with a decoy at the place its
    text collapses to is still a creation (impl panel r2), and a rewrite of an existing
    record under any lexical spelling is not. A path that cannot be placed (relative,
    `~`) is judged on existence alone, as the guard always did."""
    if not path:
        return True
    if path.startswith("~") or not _is_absolute(path):
        return not os.path.isfile(path)
    for reading in (os.path.realpath(path), _canon(path)):
        if not os.path.isfile(reading) and (
                _lexically_inside(reading, project) or _physically_inside(reading, project)):
            return True
    return False


def main() -> int:
    # The path arrives on STDIN, never in the environment (task 080).
    if sys.argv[1:] not in (["--path"], ["--creates"]):
        print("usage: task-dir-target.py --path | --creates  (a file path on stdin, PB_PROJECT=<project root>)",
              file=sys.stderr)
        return 2
    path = sys.stdin.read()
    project = os.environ.get("PB_PROJECT", "")
    if not path or not project:
        return 1
    if sys.argv[1:] == ["--path"]:
        return 1 if path_may_be_inside(path, project) else 0
    return 1 if write_may_create_inside(path, project) else 0


if __name__ == "__main__":
    sys.exit(main())
