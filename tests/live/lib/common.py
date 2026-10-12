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

import datetime as _dt
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent.parent                    # playbook-plugin/tests
NESTED = TESTS.parent                         # playbook-plugin (the nested repository)
PLUGIN = NESTED / "plugins" / "playbook"      # the CANDIDATE plugin tree

PASS, FAIL, WAITING = 0, 1, 3
_CAP = 200_000                                # characters kept per stream; more is said, never silently cut

# what a nested session must not inherit from the session that runs the gauntlet
_STRIP_PREFIXES = ("PLAYBOOK_", "CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_PLUGIN_", "CODEX_", "GROK_")
_STRIP_NAMES = ("BASH_ENV", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")


def clean_env(extra: "dict[str, str] | None" = None, drop: "tuple[str, ...]" = ()) -> "dict[str, str]":
    """This process's environment without the variables that would make a nested
    run take this session's identity, logger or interpreter settings."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(_STRIP_PREFIXES) and k not in _STRIP_NAMES and k not in drop}
    env.update(extra or {})
    return env


def snapshot(root: Path) -> "dict[str, tuple[int, str]]":
    """path -> (size, sha256 of the first 1 MiB) for every regular file under root."""
    out: "dict[str, tuple[int, str]]" = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                with p.open("rb") as fh:
                    digest = hashlib.sha256(fh.read(1 << 20)).hexdigest()[:16]
                out[str(p.relative_to(root))] = (p.stat().st_size, digest)
            except OSError:
                continue
    return out


def delta(before: "dict[str, tuple[int, str]]", after: "dict[str, tuple[int, str]]") -> "list[str]":
    lines = []
    for k in sorted(set(before) | set(after)):
        if k not in before:
            lines.append(f"+ {k} ({after[k][0]} B)")
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
        try:
            r = subprocess.run(cmd, cwd=cwd, env=env, input=stdin, capture_output=True, text=True,
                               timeout=timeout, errors="replace")
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            r = subprocess.CompletedProcess(cmd, 124, out, err + f"\n[timed out after {timeout} s]")
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
