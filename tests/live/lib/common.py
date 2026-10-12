"""The live gauntlet's shared recorder (PLAN S11, task 184).

One `Run` per ledger row. Every step is a real command; the recorder keeps what a
reviewer needs to judge the verdict without re-running it: the command, its
environment overrides, its working directory, the exit status, the FULL stdout and
stderr, and the filesystem delta of the directories the step was told to watch.
A clause is judged from that observed output, never from what an agent reports.

The artifact is one text file per row, `<out>/<ROW-ID>.txt`, ending in
`ROW <ID>: PASS|FAIL|WAITING (<reason>)`. Exit status of a row: 0 PASS, 1 FAIL,
3 WAITING (a target that cannot be run now — grok out of credit, the owner's step).

Stdlib only. Never imported by the test suite: this directory has no `__init__.py`
and no `test_*` file, so `unittest discover` does not collect it, and
`tests/test_shell_fixtures.py` runs only its own six fixtures.
"""
from __future__ import annotations

import atexit
import datetime as _dt
import hashlib
import json
import os
import shlex
import shutil
import signal
import tempfile
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent.parent                    # playbook-plugin/tests
NESTED = TESTS.parent                         # playbook-plugin (the nested repository)
PLUGIN = NESTED / "plugins" / "playbook"      # the CANDIDATE plugin tree

PASS, FAIL, WAITING = 0, 1, 3
_CAP = 200_000                                # characters kept per stream; more is said, never silently cut

# what a nested session must not inherit from the session that runs the gauntlet
_STRIP_PREFIXES = ("PLAYBOOK_", "CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_PLUGIN_", "CODEX_", "GROK_")
_STRIP_NAMES = ("BASH_ENV", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                # the parent session's identity (the product strips the same: sandbox._PARENT_SESSION_ENV)
                "CLAUDE_PID", "CLAUDE_ENV_FILE", "CLAUDE_PROJECT_DIR", "CLAUDE_EFFORT")


def clean_env(extra: "dict[str, str] | None" = None, drop: "tuple[str, ...]" = ()) -> "dict[str, str]":
    """This process's environment without the variables that would make a nested
    run take this session's identity, logger or interpreter settings."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(_STRIP_PREFIXES) and k not in _STRIP_NAMES and k not in drop}
    env.update(extra or {})
    return env


def snapshot(root: Path) -> "dict[str, tuple[int, str]]":
    """path -> (size, sha256 of the whole file) for every regular file under root, and
    `path/` -> (-1, "dir") for every directory (an empty directory left behind is a change too)."""
    out: "dict[str, tuple[int, str]]" = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for d in dirnames:
            out[str((Path(dirpath) / d).relative_to(root)) + "/"] = (-1, "dir")
        for name in filenames:
            p = Path(dirpath) / name
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                h = hashlib.sha256()
                with p.open("rb") as fh:
                    # the whole file: the rollback row asserts byte identity with this (impl panel round 2, T16)
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                digest = h.hexdigest()[:16]
                out[str(p.relative_to(root))] = (p.stat().st_size, digest)
            except OSError:
                continue
    return out


def delta(before: "dict[str, tuple[int, str]]", after: "dict[str, tuple[int, str]]") -> "list[str]":
    lines = []
    for k in sorted(set(before) | set(after)):
        if k not in before:
            lines.append(f"+ {k}" if after[k][0] < 0 else f"+ {k} ({after[k][0]} B)")
        elif k not in after:
            lines.append(f"- {k}")
        elif before[k] != after[k]:
            lines.append(f"~ {k} ({before[k][0]} -> {after[k][0]} B)")
    return lines


def _cut(text: str) -> str:
    if len(text) <= _CAP:
        return text
    return text[:_CAP] + f"\n[… {len(text) - _CAP} more characters not kept in this artifact]\n"


def tool_versions() -> "list[str]":
    out = []
    for name, argv in (("claude", ["claude", "--version"]), ("codex", ["codex", "--version"]),
                       ("grok", ["grok", "--version"]), ("agy", ["agy", "--version"]),
                       ("bwrap", ["bwrap", "--version"]), ("git", ["git", "--version"]),
                       ("docker", ["docker", "--version"]), ("bash", ["bash", "--version"])):
        if not shutil.which(argv[0]):
            out.append(f"{name}: not installed")
            continue
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            first = (r.stdout or r.stderr).strip().splitlines()
            out.append(f"{name}: {first[0] if first else '(no output)'}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            out.append(f"{name}: {exc}")
    out.append(f"python3 (runner): {sys.version.split()[0]}")
    return out


def candidate() -> str:
    head = subprocess.run(["git", "-C", str(NESTED), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(NESTED), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    return f"{head} ({'DIRTY working tree' if dirty else 'clean'})"


class Run:
    """The record of one row's live run."""

    def __init__(self, row: str, title: str, out_dir: Path, targets: "list[str]"):
        self.row, self.title, self.out_dir, self.targets = row, title, Path(out_dir), list(targets)
        self.lines: "list[str]" = []
        self.clauses: "list[tuple[str, bool, str]]" = []
        self.waiting: "list[str]" = []
        self.started = _dt.datetime.now().astimezone()
        self.lines += [f"# {row} — live run", f"title: {title}", f"targets: {', '.join(self.targets)}",
                       f"candidate (nested HEAD): {candidate()}", f"plugin tree: {PLUGIN}",
                       f"started: {self.started.isoformat(timespec='seconds')}", "tools:"]
        self.lines += [f"  {v}" for v in tool_versions()]
        self._n = 0

    def note(self, text: str) -> None:
        self.lines.append(text)

    def step(self, title: str, argv, *, cwd: "Path | str | None" = None, env: "dict[str, str] | None" = None,
             env_shown: "dict[str, str] | None" = None, timeout: float = 600, stdin: "str | None" = None,
             watch: "list[Path] | tuple[Path, ...]" = ()) -> subprocess.CompletedProcess:
        """Run one command for real and record it whole. `argv` is a list, or a string run by bash."""
        self._n += 1
        shown = argv if isinstance(argv, str) else " ".join(shlex.quote(str(a)) for a in argv)
        cmd = ["bash", "-c", argv] if isinstance(argv, str) else [str(a) for a in argv]
        before = {str(w): snapshot(Path(w)) for w in watch}
        self.lines += ["", f"## step {self._n}: {title}", f"$ {shown}", f"cwd: {cwd or os.getcwd()}"]
        if env_shown:
            self.lines.append("env: " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env_shown.items()))
        t0 = _dt.datetime.now()
        # no step reads the harness's own stdin: given text, or nothing (a CLI that reads stdin sees EOF).
        # Each step runs in its own session, so a timeout or an interrupted run can end everything it started,
        # not only the direct child (impl panel round 2, T13)
        # every process of the step carries the step's token in its environment, so one that detached and was
        # reparented before the timeout is still found and ended (single judge after round 2, J1)
        token = uuid.uuid4().hex
        env = dict(os.environ if env is None else env, **{STEP_TOKEN: token})
        p = None
        try:
            # the spawn is inside the protected block: a signal that lands as it returns, before `p` is held, is
            # cleaned up by the token alone (task 184's L1)
            p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 errors="replace", start_new_session=True,
                                 stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL)
            out, err = p.communicate(input=stdin, timeout=timeout)
            r = subprocess.CompletedProcess(cmd, p.returncode, out, err)
        except subprocess.TimeoutExpired:
            ended = end_tree(p.pid, token)
            try:
                out, err = p.communicate(timeout=30)
            except subprocess.TimeoutExpired:            # a process outside the tree still holds the pipes
                p.kill()
                out, err = "", "[the step's output could not be collected after the timeout]"
            r = subprocess.CompletedProcess(cmd, 124, out, err + f"\n[timed out after {timeout} s; {ended} process(es) of the step ended]")
        except BaseException:
            end_tree(p.pid if p is not None else None, token)
            raise
        secs = (_dt.datetime.now() - t0).total_seconds()
        self.lines += [f"rc: {r.returncode}   ({secs:.1f} s)", "--- stdout ---", _cut(r.stdout).rstrip("\n"),
                       "--- stderr ---", _cut(r.stderr).rstrip("\n")]
        for w in watch:
            d = delta(before[str(w)], snapshot(Path(w)))
            self.lines.append(f"--- filesystem delta of {w} ({len(d)}) ---")
            self.lines += d or ["(none)"]
        return r

    def clause(self, name: str, ok: bool, evidence: str) -> bool:
        self.clauses.append((name, bool(ok), evidence))
        self.lines += ["", f"CLAUSE {'PASS' if ok else 'FAIL'}: {name} — {evidence}"]
        return bool(ok)

    def wait_for(self, target: str, reason: str) -> None:
        """A target that cannot be run now. The row is WAITING, not passed, while any remains."""
        self.waiting.append(f"{target}: {reason}")
        self.lines += ["", f"WAITING: {target} — {reason}"]

    def finish(self) -> int:
        failed = [c for c in self.clauses if not c[1]]
        if failed:
            verdict, code = f"FAIL ({len(failed)} of {len(self.clauses)} clauses)", FAIL
        elif not self.clauses:
            verdict, code = "FAIL (no clause was judged)", FAIL
        elif self.waiting:
            verdict, code = f"WAITING ({'; '.join(self.waiting)})", WAITING
        else:
            verdict, code = f"PASS ({len(self.clauses)} clauses)", PASS
        ended = _dt.datetime.now().astimezone()
        self.lines += ["", "## clauses", *[f"- {'PASS' if c[1] else 'FAIL'}: {c[0]}" for c in self.clauses],
                       f"ended: {ended.isoformat(timespec='seconds')}", f"ROW {self.row}: {verdict}"]
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{self.row}.txt").write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        print(f"ROW {self.row}: {verdict} — {self.out_dir / (self.row + '.txt')}")
        return code


_FRAME = ("This is the project owner's own test of the Playbook plugin's sandbox, on a scratch project made for it. "
          "Your shell tool runs INSIDE the sandbox being tested. Run EXACTLY the shell commands below with your shell "
          "tool, in order, each as written. Some are EXPECTED to fail — the failure is what the test measures — so "
          "do not fix, skip or explain anything. When all have run, reply with the single word DONE.\n\n")


def judge_runs(run: "Run", title: str, project: Path, commands: "list[str]", *, models=("haiku", "sonnet"),
               extra: "tuple[str, ...]" = ("--ro-project",), env=None, watch=()) -> subprocess.CompletedProcess:
    """A real judge (a claude model through `sandbox --prompt`, the subagent path the judges share) is asked to
    run `commands`. A model that declines (no DONE) is not the product failing: the step is retried once with the
    next model, and both replies stay in the artifact."""
    prompt = _FRAME + "\n".join(f"{i}. {c}" for i, c in enumerate(commands, 1)) + "\n"
    r = None
    for model in models:
        r = run.step(f"{title} — judge {model}", [str(PLUGIN / "scripts" / "sandbox"), "--model", model,
                     "--project-root", str(project), *extra, "--bare", "--prompt", prompt], cwd=project,
                     env=env or clean_env(), timeout=900, watch=list(watch))
        if r.returncode == 0 and "DONE" in r.stdout:
            break
        run.note(f"(the {model} judge did not run the commands — reply above; retried with the next model)")
    assert r is not None
    return r


def scratch_project(path: Path, *, lanes: "tuple[str, ...]" = ()) -> Path:
    """A project as a user has it after init — a git repository with a first commit, the
    playbook CLAUDE.md and the `.gitignore` block that keeps runtime state out of the record
    (written by the candidate's own `claude-md-merge.py`, the project half of init; the
    HOME half of init is never run here) — and `.agent/tasks/`, or one lane per name."""
    path.mkdir(parents=True, exist_ok=True)
    env = clean_env()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "live@gauntlet"], cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "live gauntlet"], cwd=path, check=True, env=env)
    (path / "README.md").write_text("A scratch project for the live gauntlet.\n", encoding="utf-8")
    subprocess.run(["python3", "-E", "-s", str(PLUGIN / "scripts" / "claude-md-merge.py"),
                    str(PLUGIN / "scripts" / "CLAUDE.md.template"), str(path), path.name],
                   check=True, capture_output=True, text=True, env=env)
    if lanes:
        for lane in lanes:
            (path / ".agent" / lane / "tasks").mkdir(parents=True, exist_ok=True)
    else:
        (path / ".agent" / "tasks").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "scratch project"], cwd=path, check=True, env=env)
    return path


def tempdir(prefix: str) -> Path:
    """A scratch directory removed when the row's process exits (one row runs per process). Its name
    holds letters and digits only: `mkdtemp`'s alphabet has `_`, and a project path with one meets the
    adapter's transcript-folder bound (parked on task 184) in about one run in five (impl panel I5)."""
    while True:
        d = Path(tempfile.gettempdir()) / f"{prefix}{uuid.uuid4().hex[:10]}"
        try:
            d.mkdir(mode=0o700)
            break
        except FileExistsError:
            continue
    atexit.register(shutil.rmtree, d, True)
    return d


INSTALLED_PLUGIN = "playbook@playbook-x-marketplace"


def session_project(path: Path, *, lanes: "tuple[str, ...]" = (), marker: "str | None" = None) -> Path:
    """A scratch project where a real claude session runs the CANDIDATE: the owner's installed
    copy is disabled for this project only (its `.claude/settings.json`), the candidate is loaded
    with `--plugin-dir` by `claude_session`, and `.claude/bin/tasks` runs the candidate's CLI.
    (Verified 2026-10-12 in the session's debug log: "Read hooks.json for plugin playbook
    (enabled=true): <tree>…" and, for the installed copy, "will NOT register, plugin is disabled".)"""
    scratch_project(path, lanes=lanes)
    if marker is not None:
        (path / ".agent" / "current_user").write_text(marker + "\n", encoding="utf-8")
    settings = path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    data.setdefault("enabledPlugins", {})[INSTALLED_PLUGIN] = False
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    wrapper = path / ".claude" / "bin" / "tasks"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(f'#!/bin/bash\nexec "{PLUGIN}/scripts/tasks" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return path


def claude_session(run: "Run", title: str, project: Path, prompt: str, *, model: str = "haiku",
                   extra: "tuple[str, ...]" = (), timeout: float = 900) -> "tuple[subprocess.CompletedProcess, list[dict]]":
    """One real headless claude session in `project`, the candidate loaded with --plugin-dir. The
    stream of events (tool calls, their results, hook feedback, the final result) is kept whole in
    the artifact; the parsed events are returned for the clauses."""
    # DETACHED (`setsid -f`): the hooks attribute a session to the ROOT agent of their process chain (by
    # design — a sub-session belongs to its session), so a session started under the one running the
    # gauntlet would carry ITS identity. Detached, the chain is `bash → systemd` (measured) and the
    # session is a user's own, as in a terminal.
    work = tempdir("pb184-sess-")
    (work / "prompt").write_text(prompt, encoding="utf-8")
    claude = ["claude", "-p", "--plugin-dir", str(PLUGIN), "--model", model, "--dangerously-skip-permissions",
              "--output-format", "stream-json", "--verbose", "--include-hook-events",
              # the user's own settings stay out: their hooks (a Stop sound) would run in every test session
              "--setting-sources", "project,local", *extra]
    # the detached shell writes its pid first: `setsid` makes it the leader of the session's process group
    inner = (f"echo $$ > {shlex.quote(str(work / 'pid'))}; cd {shlex.quote(str(project))} && {' '.join(shlex.quote(a) for a in claude)} "
             f"< {shlex.quote(str(work / 'prompt'))} > {shlex.quote(str(work / 'out'))} 2> {shlex.quote(str(work / 'err'))}; "
             f"echo $? > {shlex.quote(str(work / 'rc.tmp'))} && mv {shlex.quote(str(work / 'rc.tmp'))} {shlex.quote(str(work / 'rc'))}")
    waiter = (f"setsid -f bash -c {shlex.quote(inner)}; n=0; while [ ! -f {shlex.quote(str(work / 'rc'))} ]; do "
              f"sleep 1; n=$((n+1)); [ $n -gt {int(timeout)} ] && {{ echo 'timed out' >&2; exit 124; }}; done; "
              f"cat {shlex.quote(str(work / 'out'))}; cat {shlex.quote(str(work / 'err'))} >&2; exit $(cat {shlex.quote(str(work / 'rc'))})")
    run.note(f"session prompt:\n{prompt.rstrip()}")
    try:
        r = run.step(title + " (claude " + " ".join(claude[1:]) + ")", waiter, cwd=project, env=clean_env(),
                     timeout=timeout + 60, watch=[project / ".agent", project / "src"])
    finally:
        # a session cut by the timeout, or by an interrupted gauntlet, must not keep running (and spending)
        # after its project is gone (impl panel I13): its whole process group is ended before cleanup
        _end_group(run, work / "pid")
        shutil.rmtree(work, ignore_errors=True)
    events = []
    for line in r.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return r, events


def _descendants(pid: int) -> "list[int]":
    """Every live descendant of `pid`, read from /proc (a child that made its own session or group is one too)."""
    children: "dict[int, list[int]]" = {}
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(d.name))
    out, todo = [], [pid]
    while todo:
        for c in children.get(todo.pop(), []):
            out.append(c)
            todo.append(c)
    return out


STEP_TOKEN = "PB_LIVE_STEP"


def _carriers(token: str) -> "list[int]":
    """Every process of this user whose environment holds the step's token (it survives setsid and reparenting)."""
    mark, out = f"{STEP_TOKEN}={token}".encode(), []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if mark in (d / "environ").read_bytes().split(b"\0"):
                    out.append(int(d.name))
            except OSError:
                continue
    return out


def end_tree(pid: "int | None", token: "str | None" = None) -> int:
    """SIGKILL a step's whole tree: its process group, every descendant, and — given the step's token — every
    process that carries it, wherever it moved. With no pid (the spawn never returned one), the token alone.
    Returns how many."""
    victims = (set(_descendants(pid)) | {pid} if pid is not None else set()) | (set(_carriers(token)) if token else set())
    if pid is not None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    for v in victims:
        try:
            os.kill(v, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    # a victim that is this process's own child (one whose Popen never returned) is reaped, not left a zombie
    end = time.monotonic() + 2
    for v in victims:
        while True:
            try:
                if os.waitpid(v, os.WNOHANG)[0] or time.monotonic() > end:
                    break
            except ChildProcessError:            # not our child, or already reaped
                break
            time.sleep(0.05)
    return len(victims)


def _end_group(run: "Run", pidfile: Path) -> None:
    try:
        pgid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return
    for sig, grace in ((signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return
        run.note(f"the session's process group {pgid} was still running: {sig.name} sent")
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        end = time.monotonic() + grace
        while time.monotonic() < end:
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.2)
    run.note(f"the session's process group {pgid} survived SIGKILL")


def tool_calls(events: "list[dict]") -> "list[tuple[str, dict]]":
    """(tool name, input) of every tool call the session made, in order."""
    out = []
    for e in events:
        if e.get("type") == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    out.append((c.get("name", ""), c.get("input") or {}))
    return out


def tool_results(events: "list[dict]") -> "list[str]":
    """The text of every tool result the session received (hook refusals come back here)."""
    out = []
    for e in events:
        if e.get("type") == "user":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    body = c.get("content")
                    if isinstance(body, list):
                        body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                    out.append(str(body))
    return out


def journal(project: Path, lane: "str | None" = None) -> "list[dict]":
    base = project / ".agent" / lane if lane else project / ".agent"
    f = base / "journal" / "enforcement.jsonl"
    rows = []
    if f.exists():
        for x in f.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(x))
            except ValueError:
                continue
    return rows


def owner_home_guard() -> "dict[str, str]":
    """Hashes of the owner's plugin and settings files: taken before a group and compared
    after it, as a second line behind the disposable HOME (task 184 plan panel P7)."""
    home = Path.home()
    files = [home / ".claude" / "settings.json", home / ".claude" / "plugins" / "installed_plugins.json",
             home / ".claude" / "plugins" / "known_marketplaces.json", home / ".claude.json",
             home / ".claude" / "bash-log.sh", home / ".profile", home / ".bashrc"]
    out = {}
    for f in files:
        try:
            out[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        except OSError:
            out[str(f)] = "(absent)"
    return out
