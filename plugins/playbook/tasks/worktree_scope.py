"""Work a close cannot see: a Claude Code worktree's (task 321, stabilization audit 5's G1).

Claude Code makes its worktrees in `<repository>/.claude/worktrees/<name>/`, and one that holds no task records answers
to the main tree's task (tasks 298, 302): its edits pass the gate under it. But the close and `tasks verify` find the
project by walking up from the cwd and run the verify contract there, in the main tree. So from inside such a worktree
— or from the main tree while one still holds work the main tree does not have — a `[PASS]` described code that is not
the task's. `stray_work` names each such case; the close blocks on them, before verify and again just before its
commit (`--force --reason` passes, recorded), and `tasks verify` refuses.

The repositories looked at are the project's and each `code_roots` one. A worktree with its own lane — or under a
group folder with one, as the hooks' `pb_main_tree` reads it — answers to its own tasks and is not looked at; a nested
repository is another repository, not a worktree. A worktree holds work when git shows a change in it (untracked files
count), when an index bit (`assume-unchanged`, or `skip-worktree` over a file that is there — a sparse checkout's
absent files are no edit) hides one, or when it has commits the main tree's HEAD does not have, counted from the commit
the worktree was made at (the oldest entry of its HEAD's reflog): what it was made from is not its work, and what it
committed is, pushed or merged upstream or not, until the main tree has it (impl rounds 1-2: agy, opus, codex). A
worktree git cannot read as itself — its `.git` gone, so git answers for the main tree instead — fails closed, and so
does a git that refuses to answer for a repository. Worktree paths are read NUL-separated, as bytes. git runs with the
caller's `GIT_*` repository variables dropped and replace refs off.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tasks.verify_memory import _GIT_ENV_DROP


def _git(cwd: Path, *args: str, binary: bool = False) -> "subprocess.CompletedProcess | None":
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}
    try:
        if binary:
            return subprocess.run(["git", "--no-replace-objects", *args], cwd=str(cwd), capture_output=True, env=env,
                                  timeout=60)
        return subprocess.run(["git", "--no-replace-objects", *args], cwd=str(cwd), capture_output=True, text=True,
                              errors="replace", env=env, timeout=60)
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
    """`d` holds `.agent/tasks` or a `.agent/<lane>/tasks` — the markers `find_project_root` looks for, as it reads
    them: any that exists (impl round 2, opus)."""
    agent = d / ".agent"
    if (agent / "tasks").exists():
        return True
    try:
        return agent.is_dir() and any((s / "tasks").exists() for s in agent.iterdir() if s.is_dir())
    except OSError:
        return False


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _repositories(project: Path) -> "list[Path]":
    """The project and each `code_roots` repository (`core._code_roots`), as folders."""
    found = [project]
    try:
        from tasks.core import _code_roots, load_config
        for rel in _code_roots(load_config(project)):
            d = (project / rel).resolve()
            if d.is_dir() and d not in found:
                found.append(d)
    except Exception:       # noqa: BLE001 — no config, or one that cannot be read: the project's repository alone
        pass
    return found


class _Repo:
    """One repository: its top-level, common git directory, the folders its Claude Code worktrees live in (its own, its
    top's, and the project's — impl round 2, codex: a `code_roots` repository's worktree in the project's folder), and
    its main tree's HEAD."""

    def __init__(self, folder: Path, top: Path, common: Path, project: Path):
        self.top, self.common = top, common
        self.worktree_dirs = sorted({folder / ".claude" / "worktrees", top / ".claude" / "worktrees",
                                     project / ".claude" / "worktrees"})
        self.head = _out(_git(top, "rev-parse", "HEAD"))

    def is_claude_worktree(self, wt: Path) -> bool:
        return any(_inside(wt, d) and wt != d for d in self.worktree_dirs)

    def own_lane(self, wt: Path) -> bool:
        if _has_lane(wt):
            return True
        return any(wt.parent != d and _inside(wt.parent, d) and _has_lane(wt.parent) for d in self.worktree_dirs)

    def work_in(self, wt: Path) -> "str | None":
        """What work `wt` holds that the main tree does not, or None."""
        wtop = _resolved(wt, _out(_git(wt, "rev-parse", "--show-toplevel")))
        wcommon = _resolved(wt, _out(_git(wt, "rev-parse", "--git-common-dir")))
        if wtop != wt or wcommon != self.common:
            return f"the worktree {wt} cannot be read by git as itself, so its work cannot be told apart from the main tree's"
        status = _git(wt, "status", "--porcelain", "--untracked-files=normal")
        bits = _git(wt, "ls-files", "-v", "-z", binary=True)          # names read whole (post-panel run 1, codex)
        sparse = _out(_git(wt, "config", "--bool", "core.sparseCheckout")) == "true"
        wt_head = _out(_git(wt, "rev-parse", "HEAD"))
        if status is None or status.returncode != 0 or bits is None or bits.returncode != 0 or wt_head is None:
            return f"the worktree {wt} cannot be read by git, so its work cannot be told apart from the main tree's"
        if status.stdout.strip():
            return f"the worktree {wt} holds uncommitted changes that the main tree does not have (untracked files count)"
        # a skip-worktree file that is not there is a sparse cone's absence only where a sparse checkout is on; without
        # one it is a hidden deletion (post-panel run 1, codex)
        hidden = []
        for entry in bits.stdout.split(b"\0"):
            tag, name = entry[:1], os.fsdecode(entry[2:])
            if not name:
                continue
            if tag.islower() or (tag == b"S" and (not sparse or os.path.lexists(wt / name))):
                hidden.append(name)
        if hidden:
            return (f"the worktree {wt} has {len(hidden)} file(s) under an assume-unchanged or skip-worktree bit, which "
                    "hides an edit from git status")
        # what the worktree was made at is not its work: the oldest entry of its HEAD's reflog (impl round 2, opus —
        # the remote-tracking refs counted the task's own commits once they were upstream, the main tree not pulled)
        # It counts only as the worktree's creation: `git worktree add` writes that entry with no message; an oldest
        # entry that is something else (an expired reflog's leftover — post-panel run 1, codex) is no base, and then
        # the count is against the main HEAD alone (fail closed)
        oldest = (_out(_git(wt, "reflog", "show", "--format=%H %gs", "HEAD")) or "").splitlines()[-1:]
        made_at = [ln.split(" ", 1)[0] for ln in oldest if ln.strip() and " " not in ln.strip()]
        known = [x for x in [self.head, *made_at] if x]
        ahead = _out(_git(wt, "rev-list", "--count", wt_head, "--not", *known))
        if ahead is None:
            return f"the worktree {wt}: git cannot tell whether its commits are in the main tree"
        if ahead != "0":
            return f"the worktree {wt} holds {ahead} commit(s) that the main tree's HEAD does not have"
        return None


def stray_work(project: Path, cwd: "Path | None" = None) -> "list[str]":
    """One sentence per piece of work a close of `project` would certify without having read it (empty: none)."""
    project = Path(project).resolve()
    cwd = Path(cwd if cwd is not None else os.getcwd()).resolve()
    found: "list[str]" = []
    seen: "set[Path]" = set()
    for folder in _repositories(project):
        asked = _git(folder, "rev-parse", "--show-toplevel")
        if asked is not None and asked.returncode != 0 and "not a git repository" in (asked.stderr or ""):
            continue                                # no git repository here: nothing to compare
        top = _resolved(folder, _out(asked))
        common = _resolved(folder, _out(_git(folder, "rev-parse", "--git-common-dir")))
        if top is None or common is None:
            # impl round 2 (sonnet): a git that refuses (dubious ownership) or does not answer is no "no repository"
            said = ((asked.stderr or "").strip().splitlines() or ["no answer"])[0] if asked is not None else "no answer"
            found.append(f"git cannot answer for {folder} ({said}), so its worktrees cannot be checked")
            continue
        repo = _Repo(folder, top, common, project)
        # (a) the cwd is another worktree of this repository, holding work
        if cwd != top and (_inside(cwd, folder) or _inside(cwd, top)):
            ctop = _resolved(cwd, _out(_git(cwd, "rev-parse", "--show-toplevel")))
            ccommon = _resolved(cwd, _out(_git(cwd, "rev-parse", "--git-common-dir")))
            in_wt_dir = any(_inside(cwd, d) for d in repo.worktree_dirs)
            if (ctop is None or ccommon is None) and in_wt_dir:
                found.append(f"the current folder {cwd} is in a worktree folder and git cannot read its checkout")
            elif ctop is not None and ctop != top and ccommon == common and not repo.own_lane(ctop):
                why = repo.work_in(ctop)
                if why and ctop not in seen:
                    seen.add(ctop)
                    found.append(f"you are in the worktree {ctop}, and this task's verify runs in the main tree: {why}")
        # (b) a lane-less Claude Code worktree that holds work the main tree does not have
        listed = _git(top, "worktree", "list", "--porcelain", "-z", binary=True)
        if listed is None or listed.returncode != 0:
            found.append(f"git cannot list the worktrees of {top}, so their work cannot be checked")
            continue
        for field in listed.stdout.split(b"\0"):      # a path is read whole (impl round 2: agy, codex)
            if not field.startswith(b"worktree ") or not field[len(b"worktree "):]:
                continue
            wt = Path(os.fsdecode(field[len(b"worktree "):])).resolve()
            if wt == top or wt in seen or not repo.is_claude_worktree(wt) or not wt.is_dir() or repo.own_lane(wt):
                continue
            why = repo.work_in(wt)
            if why:
                seen.add(wt)
                found.append(why)
    return found


def advice(project: Path) -> str:
    """What to do about stray work, after the list of it."""
    return (f"Bring the work into the main tree of {Path(project).resolve()} (commit it there, or merge the worktree's "
            "branch) or remove the worktree, and close from the main tree — a close certifies the code it runs verify "
            "on.")
