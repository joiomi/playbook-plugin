"""Work a close cannot see: a Claude Code worktree's (task 321, stabilization audit 5's G1).

Claude Code makes its worktrees in `<project>/.claude/worktrees/<name>/`, and one that holds no task records answers to
the main tree's task (tasks 298, 302): its edits pass the gate under it. But the close and `tasks verify` find the
project by walking up from the cwd and run the verify contract there, in the main tree. So from inside such a worktree
— or from the main tree while one still holds work not in the main tree's HEAD — a `[PASS]` described code that is not
the task's. `stray_work` names each such case; the close blocks on them (`--force --reason` passes, recorded) and
`tasks verify` refuses.

A worktree with its own lane answers to its own tasks and is not looked at; a nested repository (`code_roots`) is
another repository, not a worktree. Commits a remote-tracking ref has are not counted as the task's work (a worktree
made from `origin/<branch>` ahead of the main tree). git runs with the caller's `GIT_*` repository variables dropped.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tasks.verify_memory import _GIT_ENV_DROP


def _git(cwd: Path, *args: str) -> "subprocess.CompletedProcess[str] | None":
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, errors="replace",
                              env=env, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None


def _out(r: "subprocess.CompletedProcess[str] | None") -> "str | None":
    return r.stdout.strip() if r is not None and r.returncode == 0 else None


def _resolved(base: Path, path: "str | None") -> "Path | None":
    if not path:
        return None
    p = Path(path)
    return (p if p.is_absolute() else base / p).resolve()


def _has_lane(d: Path) -> bool:
    """`d` holds `.agent/tasks` or a `.agent/<lane>/tasks` — the markers `find_project_root` looks for."""
    agent = d / ".agent"
    if (agent / "tasks").is_dir():
        return True
    try:
        return agent.is_dir() and any((s / "tasks").is_dir() for s in agent.iterdir() if s.is_dir())
    except OSError:
        return False


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def stray_work(project: Path, cwd: "Path | None" = None) -> "list[str]":
    """One sentence per piece of work a close of `project` would certify without having read it (empty: none)."""
    project = Path(project).resolve()
    cwd = Path(cwd if cwd is not None else os.getcwd()).resolve()
    top = _resolved(project, _out(_git(project, "rev-parse", "--show-toplevel")))
    common = _resolved(project, _out(_git(project, "rev-parse", "--git-common-dir")))
    if top is None or common is None:
        return []                                   # no git repository: nothing to compare
    worktrees_dir = project / ".claude" / "worktrees"
    found: "list[str]" = []
    # (a) the cwd is another worktree of the project's repository
    if cwd != top and _inside(cwd, project):
        ctop = _resolved(cwd, _out(_git(cwd, "rev-parse", "--show-toplevel")))
        ccommon = _resolved(cwd, _out(_git(cwd, "rev-parse", "--git-common-dir")))
        if ctop is None or ccommon is None:
            if _inside(cwd, worktrees_dir):
                found.append(f"the current folder {cwd} is under {worktrees_dir} and git cannot read its checkout")
        elif ctop != top and ccommon == common:
            found.append(f"you are in the worktree {ctop}: this task's verify runs in {project}, and the code there is "
                         "not this worktree's")
    # (b) a lane-less Claude Code worktree that holds work the main tree's HEAD does not have
    listing = _out(_git(project, "worktree", "list", "--porcelain"))
    head = _out(_git(project, "rev-parse", "HEAD"))
    for line in (listing or "").splitlines():
        if not line.startswith("worktree "):
            continue
        wt = Path(line[len("worktree "):]).resolve()
        if wt == top or not _inside(wt, worktrees_dir) or not wt.is_dir() or _has_lane(wt):
            continue
        status = _git(wt, "status", "--porcelain", "--untracked-files=normal")
        wt_head = _out(_git(wt, "rev-parse", "HEAD"))
        if status is None or status.returncode != 0 or wt_head is None:
            found.append(f"the worktree {wt} cannot be read by git, so its work cannot be told apart from the main "
                         "tree's")
            continue
        if status.stdout.strip():
            found.append(f"the worktree {wt} holds uncommitted changes that {project} does not have")
            continue
        exclude = ["--not", "--remotes"] + ([head] if head else [])
        ahead = _out(_git(wt, "rev-list", "--count", wt_head, *exclude))
        if ahead is None:
            found.append(f"the worktree {wt}: git cannot tell whether its commits are in {project}")
        elif ahead != "0":
            found.append(f"the worktree {wt} holds {ahead} commit(s) that {project}'s HEAD does not have")
    return found


def advice(project: Path) -> str:
    """What to do about stray work, after the list of it."""
    return (f"Bring the work into {Path(project).resolve()} (commit it there, or merge the worktree's branch) or remove "
            "the worktree, and close from the main tree — a close certifies the code it runs verify on.")
