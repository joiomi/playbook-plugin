"""Remember the last green verify (task 228, PHASE-B.md item 3).

The owner, 2026-10-16 09:42 (task 218): "Ține minte ultima verde (Recomandat)" — when exactly the same code already
passed the project's verify contract through the CLI, the close does not run it again.

The code key, per scope the panel fingerprints (`core._tail_cert_scopes`: the outer tree and each `code_roots` repo):
a code root's HEAD commit (what an outer gitlink points at), and every path `git ls-files --cached --others
--exclude-standard` lists — tracked or not, for a tracked one its index mode and blob (a staged change counts: `git diff
--check` reads the index), its mode, the sha256 of its RAW working bytes (a link: its target and the mode and bytes of the
file it points at; a tracked file gone: "missing"); an inner scope's path is its gitlink entry. Left out: every lane's task
records (`.agent/tasks/`, `.agent/<user>/tasks/`) and untracked `.agent` files — so ticking gates after a green run keeps
the key; a tracked `.agent/config.json` counts. Raw bytes, not git's tree: git normalises text by `.gitattributes`, records a nested
repository as the commit it points at, and hides an edit under a skip-worktree or assume-unchanged bit. No key — so
nothing is reused — when a scope cannot be listed or is not its own repository (a `code_roots` dir with no `.git` would
be listed through the outer one), an index entry is unmerged, intent-to-add, a gitlink or an untracked repository that is
not itself a scope, or carries one of those bits, a link leaves the scope or points at no file, or anything goes wrong
building it. The owner's
`fingerprint_exclude` is not applied: it may cover code.

The memory: `<lane>/journal/verify-green.json` — per command, its newest run `{key, command, started, ok, ts, seconds}`,
read and written under `<lane>/journal/verify-green.lock`. Before a command runs, its start is written as a red mark
(`ok: false`), so a crash, a red result or an older green run finishing later never leaves a green record for that code
(task 228's impl round 1); if that mark cannot be written, the record is removed. After the run, a green command is
recorded only over its own red mark (same start, same key — so a removed or newer record is never turned green) and
only when the key before equals the key after; only a run that started later replaces a record. `tasks verify` always
runs the commands (and records them); only the close reuses. The close
reuses a command whose record is green with the code's key, ran under the current timeout, finds bash usable and the
journal folder writable — a memory that could not be ended is never trusted (else the command runs, and a missing bash
fails it as before).

Limits: what the key cannot see — git-ignored files, untracked `.agent` state and the task records the tests read, the
outer repository's HEAD and the history,
the environment, the clock, an edit made and undone while the run ran — can make the same code red another time; a
backward clock step can keep an older record; the journal is agent-writable, like every journal (the receipt names the
reused run and its key).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path

FILE_NAME = "verify-green.json"
LOCK_NAME = "verify-green.lock"
VERSION = 3
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES")


def _journal(project: "Path | str") -> Path:
    from tasks.core import resolve_agent_dir
    return resolve_agent_dir(Path(project)) / "journal"


def _git(repo: Path, args: "list[str]") -> "bytes | None":
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}
    try:
        r = subprocess.run(["git", "--no-replace-objects", *args], cwd=repo, capture_output=True, timeout=120, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_TASK_RECORDS = re.compile(r"^\.agent/(?:[^/]+/)?tasks(?:/|$)")      # every lane's: `.agent/tasks/`, `.agent/<user>/tasks/`


def _skipped(p: str, tracked: bool) -> bool:
    """The task records (every lane's), and untracked `.agent` files, are outside the key."""
    if _TASK_RECORDS.match(p):
        return True
    return not tracked and (p == ".agent" or p.startswith(".agent/"))


_FLAGS = re.compile(rb"flags: ([0-9a-f]+)")
_INTENT_TO_ADD = 0x20000000


def _intent_to_add(repo: Path) -> "bool | None":
    """Whether any index entry carries the intent-to-add flag (`git add -N`) — read from the index itself
    (`ls-files --debug`), since its blob is the empty one either way and the porcelain status changes with the worktree
    (post-panel run 1: an intent-to-add file removed reads ` D`)."""
    out = _git(repo, ["ls-files", "--debug"])
    if out is None:
        return None
    return any(int(m.group(1), 16) & _INTENT_TO_ADD for m in _FLAGS.finditer(out))


def _scope_digest(repo: Path, inner: "set[str]", own_repo: bool) -> "str | None":
    """The digest of one scope's working files, or None when it cannot be trusted (see the module docstring)."""
    top = _git(repo, ["rev-parse", "--show-toplevel"])
    if top is None:
        return None
    if own_repo and Path(top.decode("utf-8", "surrogateescape").strip()).resolve() != repo.resolve():
        return None                                              # a code root listed through another repository
    staged = _git(repo, ["ls-files", "-z", "-s", "--cached"])
    tags = _git(repo, ["ls-files", "-z", "-v", "--cached"])
    others = _git(repo, ["ls-files", "-z", "--others", "--exclude-standard"])
    ita = _intent_to_add(repo)
    if staged is None or tags is None or others is None or ita is None or ita:
        return None
    h = hashlib.sha256()
    if own_repo:                                                 # a code root's commit (round 2: what a gitlink sees);
        head = _git(repo, ["rev-parse", "-q", "--verify", "HEAD"])  # not the outer HEAD: a records commit is not code
        h.update(b"head\0" + (head.strip() if head else b"none") + b"\n")
    index: "dict[str, str]" = {}
    for rec in staged.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        mode, blob, stage = meta.split(b" ")[:3]
        p = path.decode("utf-8", "surrogateescape")
        if stage != b"0":
            return None                                          # an unfinished merge
        if mode == b"160000" and p not in inner:
            return None                                          # a gitlink outside the scopes
        index[p] = f"{mode.decode()} {blob.decode()}"
    for rec in tags.split(b"\0"):
        if rec and (rec[:1] == b"S" or rec[:1].islower()):
            return None                                          # skip-worktree / assume-unchanged
    untracked: "set[str]" = set()
    for rec in others.split(b"\0"):
        if not rec:
            continue
        p = rec.decode("utf-8", "surrogateescape")
        if p.endswith("/"):
            if p.rstrip("/") in inner:
                continue
            return None                                          # an untracked repository outside the scopes
        untracked.add(p)
    root = repo.resolve()
    for p in sorted(set(index) | untracked):
        tracked = p in index
        if p in inner:
            h.update(f"{p}\0{index.get(p, 'u')}\0scope\n".encode("utf-8", "surrogateescape"))   # its gitlink entry
            continue
        if _skipped(p, tracked):
            continue
        full = repo / p
        try:
            st = os.lstat(full)
        except FileNotFoundError:
            h.update(f"{p}\0{index.get(p, 'u')}\0missing\n".encode("utf-8", "surrogateescape"))
            continue
        if stat.S_ISLNK(st.st_mode):
            target = os.readlink(full)
            resolved = (full.parent / target).resolve()
            if (resolved != root and root not in resolved.parents) or not resolved.is_file():
                return None                                      # out of the scope, a directory, or dangling
            body = (f"link:{target}\0{stat.S_IMODE(resolved.stat().st_mode):o}\0"   # what it points at (round 2),
                    + _file_sha(resolved))                       # its mode too (post-panel run 1)
        elif stat.S_ISREG(st.st_mode):
            body = _file_sha(full)
        else:
            return None                                          # a directory or a special file where a file was
        h.update(f"{p}\0{index.get(p, 'u')}\0{stat.S_IMODE(st.st_mode):o}\0{body}\n"
                 .encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def code_key(project: "Path | str") -> "str | None":
    """The key of the code as it stands now, or None when it cannot be trusted. Never raises."""
    try:
        from tasks.core import _tail_cert_scopes, load_config
        project = Path(project)
        try:
            cfg = load_config(project)
        except Exception:                                         # noqa: BLE001
            cfg = {}
        scopes = _tail_cert_scopes(project, cfg)
        h = hashlib.sha256(f"v{VERSION}\n".encode("utf-8"))
        for name, repo in sorted(scopes.items()):
            base = Path(repo).resolve()
            inner = set()
            for other in scopes.values():
                o = Path(other).resolve()
                if o != base and base in o.parents:
                    inner.add(o.relative_to(base).as_posix())
            part = _scope_digest(Path(repo), inner, own_repo=bool(name))
            if part is None:
                return None
            h.update(f"{name}\0{part}\n".encode("utf-8"))
        return h.hexdigest()
    except BaseException as e:                                    # noqa: BLE001 — no key is the safe answer
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return None


def _load(project) -> dict:
    try:
        data = json.loads((_journal(project) / FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    runs = data.get("runs") if isinstance(data, dict) and data.get("v") == VERSION else None
    if not isinstance(runs, dict):
        return {}
    return {c: r for c, r in runs.items()
            if isinstance(r, dict) and r.get("command") == c and isinstance(r.get("key"), str)
            and isinstance(r.get("started"), (int, float)) and isinstance(r.get("seconds"), (int, float))
            and isinstance(r.get("ts"), str) and isinstance(r.get("ok"), bool)}


def reusable(project: "Path | str", key: "str | None", command: str, timeout: "float | None") -> "dict | None":
    """The green record the close may reuse for `command` on code `key`, or None."""
    if not key or not command:
        return None
    from tasks import bash_resolver
    if not bash_resolver.usable_bash()[0]:
        return None
    j = _journal(project)
    if not os.access(j, os.W_OK | os.X_OK):
        return None                                              # a memory that cannot be ended is never trusted
    rec = _load(project).get(command)                            # (post-panel run 2: a read-only journal folder)
    if rec is None or not rec["ok"] or rec["key"] != key:
        return None
    if timeout and rec["seconds"] >= timeout:
        return None
    return rec


def _write(project, mutate) -> str:
    """Under the lock: load, `mutate(runs) -> what | None` (None = no change), write. "unwritable" on any failure —
    and then the record file is removed if it can be (a stale green must not outlive a failed write)."""
    from tasks import atomic
    from tasks.filelock import named_lock
    j = _journal(project)
    try:
        j.mkdir(parents=True, exist_ok=True)
        with named_lock(j / LOCK_NAME):
            runs = _load(project)
            what = mutate(runs)
            if what is None:
                return "kept"
            try:
                atomic.atomic_write(j / FILE_NAME, json.dumps({"v": VERSION, "runs": runs}, sort_keys=True) + "\n")
            except Exception:                                     # noqa: BLE001
                try:
                    (j / FILE_NAME).unlink()
                except OSError:
                    pass
                return "unwritable"
            return what
    except Exception:                                             # noqa: BLE001 — the memory never breaks a close
        try:
            (j / FILE_NAME).unlink()
        except OSError:
            pass
        return "unwritable"


def begin(project: "Path | str", key: "str | None", command: str, started: float) -> str:
    """Before a command runs: its start as a red mark, so nothing but this run's own green result can make it green."""
    if not command:
        return "skipped"

    def mutate(runs):
        rec = runs.get(command)
        if rec is not None and rec["started"] > started:
            return None
        runs[command] = {"key": key or "", "command": command, "started": started, "ok": False,
                         "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": 0.0}
        return "marked"
    return _write(project, mutate)


def record(project: "Path | str", key_before: "str | None", key_after: "str | None", command: str, ok: bool,
           *, started: float, seconds: float) -> str:
    """After one command ran: "recorded" (green, the code unchanged while it ran, no later run since), "marked" (red),
    "kept", "skipped" or "unwritable"."""
    if not command:
        return "skipped"
    if ok and (not key_before or key_before != key_after):
        return "skipped"

    def mutate(runs):
        rec = runs.get(command)
        if rec is not None and rec["started"] > started:
            return None                                           # a run that started later has the last word
        if ok and (rec is None or rec["ok"] or rec["started"] != started or rec["key"] != key_before):
            return None                                           # green only over this run's own red mark (round 2)
        runs[command] = {"key": key_before or "", "command": command, "started": started, "ok": bool(ok),
                         "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(seconds, 3)}
        return "recorded" if ok else "marked"
    return _write(project, mutate)


def reused_note(rec: dict) -> str:
    """The receipt's first output line for a reused command."""
    return f"reused: the green run of {rec['ts']} on the same code (key {rec['key'][:12]})"


NOT_REMEMBERED = "not remembered: the code changed while the verify ran"
NO_KEY = "not remembered: no code key (a nested repository, a hidden index bit, an unfinished merge or a link out of the tree)"
UNWRITABLE = "not remembered: the journal cannot be written"


def _journal_run(project: "Path | str", task: "str | None", cmd: str, rc: int, how: str, seconds: float) -> None:
    """One verify-run record in the plugin's journal (task 232, PHASE-B item 6) — best-effort, never raises."""
    try:
        import importlib.util as _ilu
        from tasks.core import resolve_agent_dir, resolve_session_id
        spec = _ilu.spec_from_file_location("_pb_journal_verify",
                                            Path(__file__).resolve().parent.parent / "scripts" / "pb_journal.py")
        if spec is None or spec.loader is None:
            return
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        first = cmd.strip().splitlines()[0] if cmd.strip() else cmd
        mod.append_verify(resolve_agent_dir(Path(project)), task=task or "-", command=first, rc=rc,
                          how="reused" if how == "reused" else "ran", seconds=seconds,
                          session_id=resolve_session_id(quiet=True) or "")
    except Exception:
        pass


def run_contract(project: "Path | str", commands: "list[tuple[str, str]]", timeout, run, on_result=None,
                 reuse: bool = True, task: "str | None" = None) -> "list[tuple]":
    """Run each `(label, command)` as the close runs it — `run(command) -> (rc, output)` — reusing a green run of the
    same code. Before a command runs its start is written as a red mark; right after, its result. Returns `(label,
    command, rc, output, how)` per command — `how` "reused", "ran", or a "not remembered: …" note; `on_result` gets each
    as soon as it is known. Task 232: each one is also journaled for `task` (its number, or None) with the seconds it
    ran — 0 when reused."""
    out = []
    for label, cmd in commands:
        before = code_key(project)
        rec = reusable(project, before, cmd, timeout) if reuse else None
        seconds = 0.0
        if rec is not None:
            res = (label, cmd, 0, reused_note(rec), "reused")
        else:
            started = time.time()
            marked = begin(project, before, cmd, started)
            t0 = time.monotonic()
            rc, output = run(cmd)
            seconds = time.monotonic() - t0
            after = code_key(project)
            what = record(project, before, after, cmd, rc == 0, started=started, seconds=seconds)
            how = "ran"
            if rc == 0:
                if before is None:
                    how = NO_KEY
                elif before != after:
                    how = NOT_REMEMBERED
                elif "unwritable" in (marked, what):
                    how = UNWRITABLE
            res = (label, cmd, rc, output, how)
        try:
            _journal_run(project, task, cmd, res[2], res[4], seconds)
        except Exception:
            pass
        out.append(res)
        if on_result is not None:
            on_result(res)
    return out


def print_line(label: str, cmd: str, rc: int, how: str) -> None:
    first = cmd.strip().splitlines()[0] if cmd.strip() else cmd
    tag = "PASS" if rc == 0 else f"FAIL exit {rc}"
    if how == "reused":
        tag += ", reused"
    note = f"  ({how})" if how.startswith("not remembered") else ""
    print(f"  [{tag}] {first}{note}", flush=True)


def cmd_verify(cmd_args: "list[str]") -> None:
    """`tasks verify [<N>]` — run the project's verify contract as the close runs it (for task N's risk, or the active
    task's, else the `_always` bar) and remember a green run, so a close of the same code does not run it again."""
    import sys
    from tasks.core import (DEFAULT_RISK, _find_active_task, extract_risk, resolve_agent_dir,
                            resolve_verify_commands, resolve_verify_timeout)
    from tasks.shared import find_project_root
    if len(cmd_args) > 1 or (cmd_args and not cmd_args[0].isdigit()):
        print("Error: usage: tasks verify [<N>]", file=sys.stderr)
        raise SystemExit(2)
    from tasks.core import resolve_session_id
    from tasks.intent import find_task_dir
    project = find_project_root()
    tasks_dir = resolve_agent_dir(project) / "tasks"
    task_file = None
    if cmd_args:
        d = find_task_dir(tasks_dir, cmd_args[0].zfill(3))           # `tasks verify 1` finds 001-* (impl round 1)
        if d is None or not (d / "task.md").is_file():
            print(f"Error: no task {cmd_args[0]}", file=sys.stderr)
            raise SystemExit(2)
        task_file = d / "task.md"
    else:
        sid = resolve_session_id(quiet=True)       # this session's task first (read only — no session dir is made)
        state = resolve_agent_dir(project) / "sessions" / sid / "current_state" if sid else None
        num = state.read_text(encoding="utf-8", errors="replace").strip() if state and state.is_file() else ""
        d = find_task_dir(tasks_dir, num.zfill(3)) if num.isdigit() else None
        task_file = (d / "task.md") if d is not None and (d / "task.md").is_file() else _find_active_task(project)
        if task_file is None:                      # every gate ticked: the newest task not yet done (impl round 1)
            from tasks.core import _is_done
            def _num(t):
                head = t.parent.name.split("-", 1)[0]
                return int(head) if head.isdigit() else -1
            open_ = sorted((t for t in tasks_dir.glob("*/task.md") if not _is_done(t)), key=_num) \
                if tasks_dir.is_dir() else []
            task_file = open_[-1] if open_ else None
    risk = extract_risk(task_file) if task_file is not None else DEFAULT_RISK
    commands = resolve_verify_commands(project, risk)
    if not commands:
        print("(no verify contract declared — nothing to verify)")
        return
    from tasks.lifecycle import _merge_verify_module
    mv = _merge_verify_module()
    if mv is None:
        print("Error: cannot load the verify runner (skills/merge/merge-verify.py missing).", file=sys.stderr)
        raise SystemExit(1)
    timeout = resolve_verify_timeout(project)
    print(f"Verifying ({len(commands)} declared command(s), risk={risk})...", flush=True)
    def show(res):
        label, cmd, rc, output, how = res
        print_line(label, cmd, rc, how)
        if rc != 0:
            print((output or "").rstrip(), flush=True)
    task_num = task_file.parent.name.split("-", 1)[0] if task_file is not None else None    # task 232: the record's task
    results = run_contract(project, commands, timeout,                 # always run: the agent asked (round 2)
                           lambda c: mv.run_command_capture(c, str(project), timeout_secs=timeout), on_result=show,
                           reuse=False, task=task_num)
    raise SystemExit(1 if any(r[2] != 0 for r in results) else 0)
