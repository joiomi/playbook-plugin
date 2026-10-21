"""The red receipt (task 280, PHASE-B.md item 9b, part 1): tests first, as a record.

The owner, 2026-10-21 15:15 (M1415): "daca am gandit o functionalitate, trebuie sa ii creez toate testele posibile si
imposibile, ca sa ma asigur ca codul pe care il voi scrie urmeaza path-ul lor". Task 278 gave a full task its
`## Test contract`; this module makes "each test was watched failing first" something the CLI saw:

- `record_base` — at a task's FIRST activation (the one that finds its status `pending`), each scope (the project and
  each `code_roots` repository, the scopes a panel's snapshot uses) with its HEAD commit and a tree of its working
  state (`post_d6.worktree_tree_why`: the uncommitted work included, built in a temporary index) goes to
  `<task>/base.json` — or, when the capture fails, the failure. Created exclusively and never rewritten, so a later
  activation cannot record what the task has changed since; it never refuses the activation.
- `cmd_red` (`tasks red [<N>] [--base [<scope>=]<commit>]…`) — each contract row's test (a backticked command, or a
  test id through `red_run` in `.agent/config.json`) runs in a temporary copy of the base (each scope's base tree,
  the fingerprint's excludes left out) with the working tree's test files (`red_tests`) laid over it, and again in a
  copy of the current working state — each under the judges' write containment when bubblewrap is there (root
  read-only; the copy and a throwaway HOME writable), with the live project's environment scrubbed, `red_timeout`
  seconds. Each row's result is appended to `<task>/red-receipt.jsonl` as it ends, and the run is written to
  task.md's `## Red receipt` (pinned; the newest run replaces the last) once all rows are done.

A side is red when the command exited non-zero within its time — but 126/127 (no runner, not executable) is "launch
failed" and a timeout is "timeout", never red. The verdict reads both sides (plan panel P5): "proved" (red on the base,
green now), "already true" (green, green), "broken" (red now after red), "regressed" (green on the base, red now).
What the receipt is checked against at the close is part 2.
"""
from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

BASE_NAME = "base.json"
RECEIPT_NAME = "red-receipt.jsonl"
CONTRACT_HEADING = "## Test contract"
RECEIPT_HEADING = "## Red receipt"
DEFAULT_TIMEOUT = 300
LAST_MAX = 200
# The working tree's files laid over the base: a test directory anywhere in the path, or a test file by its name.
DEFAULT_TEST_DIRS = ("tests", "test", "spec", "__tests__")
DEFAULT_TEST_NAMES = ("test_*", "*_test.*", "*.test.*", "*.spec.*")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _git(repo: Path, args: "list[str]", *, text: bool = True, timeout: float = 120):
    if text:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=timeout)


# ---------------------------------------------------------------------------- the base

def _capture(project_path: Path) -> "tuple[dict | None, str, list[str]]":
    """Each scope's HEAD commit and working-state tree: (scopes, "", exclude) or (None, why, exclude)."""
    from tasks.core import _fingerprint_exclude_pathspecs, _tail_cert_scopes, load_config
    from tasks.post_d6 import worktree_tree_why
    try:
        cfg = load_config(Path(project_path))
    except Exception:                                   # noqa: BLE001 — a bad config is said by its own readers
        cfg = {}
    exclude = _fingerprint_exclude_pathspecs(cfg)
    scopes: "dict[str, dict]" = {}
    for name, repo in _tail_cert_scopes(Path(project_path), cfg).items():
        where = name or "the project"
        try:
            head = _git(repo, ["rev-parse", "--verify", "-q", "HEAD^{commit}"])
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"git could not run in {where} ({e.__class__.__name__})", exclude
        if head.returncode != 0 or not head.stdout.strip():
            return None, f"{where} has no git commit", exclude
        commit = head.stdout.strip()
        tree, why = worktree_tree_why(repo, exclude)
        entry: "dict" = {"commit": commit, "tree": tree}
        if tree:
            ct = _git(repo, ["rev-parse", "--verify", "-q", f"{commit}^{{tree}}"])
            entry["clean"] = ct.returncode == 0 and ct.stdout.strip() == tree
        else:
            entry["tree_error"] = why or "unknown"
            entry["clean"] = False
        scopes[name] = entry
    return scopes, "", exclude


def record_base(project_path: Path, task_dir: Path, task_num: str, first: bool) -> "tuple[bool, str]":
    """At a task's first activation (`first`: its status was `pending`), create `<task_dir>/base.json` exclusively —
    the capture, or its failure. (written, a line to print — empty when nothing needs saying)."""
    p = Path(task_dir) / BASE_NAME
    if p.exists() or p.is_symlink():
        return False, ""
    if not first:
        # the base is where the task began: a task that is not `pending` began before this activation (an old task
        # re-activated after the upgrade would record its changed state as its start)
        return False, ("note: no base recorded — this task already began (its status was not `pending` at this "
                       "activation); `tasks red` will need `--base [<scope>=]<commit>`")
    scopes, why, exclude = _capture(Path(project_path))
    doc: "dict" = {"v": 1, "task": task_num, "recorded_at": _now(), "exclude": sorted(exclude)}
    if scopes is None:
        doc["failed"] = why
    else:
        doc["scopes"] = scopes
    tmp = Path(task_dir) / f".{BASE_NAME}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.link(tmp, p)                             # exclusive: two first activations write one base (P7)
        except FileExistsError:
            return False, ""
    except OSError as e:
        return False, f"note: no base recorded for this task — {p.name} could not be written ({e})"
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    if scopes is None:
        return True, (f"note: no base recorded for this task — {why}; `tasks red` will need "
                      "`--base [<scope>=]<commit>`")
    return True, ""


def _load_base(task_dir: Path) -> "dict | None":
    try:
        doc = json.loads((Path(task_dir) / BASE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and (isinstance(doc.get("scopes"), dict) or "failed" in doc) else None


# ---------------------------------------------------------------------------- the contract

def _cells(line: str) -> "list[str]":
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def parse_contract(text: str) -> "tuple[list[dict] | None, str]":
    """The `## Test contract` rows: [{"row", "kind", "behavior", "test", "line"}] — None with the reason when there
    is no live section. A table line after the header and its `---` separator is a row; a cell holding a `|` is
    not supported (the table's own grammar)."""
    from tasks.core import _live_section_span, _physical_lines
    lines = _physical_lines(text)
    sp = _live_section_span(lines, CONTRACT_HEADING)
    if sp is None:
        return None, f"no live `{CONTRACT_HEADING}` section in task.md"
    rows: "list[dict]" = []
    seen_sep = False
    for ln in lines[sp[0] + 1:sp[1]]:
        s = ln.strip()
        if not s.startswith("|"):
            if seen_sep and rows:
                break                                   # the table ended
            continue
        cells = _cells(s)
        if not seen_sep:
            if all(set(c) <= set("-: ") and c for c in cells):
                seen_sep = True
            continue
        if len(cells) < 4:
            continue
        rows.append({"row": cells[0], "kind": cells[1], "behavior": cells[2], "test": " | ".join(cells[3:]),
                     "line": s})
    return rows, ""


def _spans(cell: str) -> "list[str]":
    """The backticked spans of a cell, in order (a lone backtick ends nothing)."""
    out, i = [], 0
    while True:
        a = cell.find("`", i)
        if a < 0:
            return out
        b = cell.find("`", a + 1)
        if b < 0:
            return out
        if b > a + 1:
            out.append(cell[a + 1:b])
        i = b + 1


def resolve(cell: str, cfg: dict) -> "tuple[list[str], str]":
    """The commands a row's test cell names — ([commands], "") or ([], why). A span with white space is a command;
    one without is a test id run through `red_run` (its `{test}` replaced)."""
    spans = _spans(cell)
    if not spans:
        return [], "no test named (the cell holds no backticked test id or command)"
    run = cfg.get("red_run")
    cmds = []
    for sp in spans:
        if any(ch.isspace() for ch in sp):
            cmds.append(sp)
        elif isinstance(run, str) and "{test}" in run:
            cmds.append(run.replace("{test}", sp))
        else:
            return [], f"`{sp}` is a test id and there is no red_run (with `{{test}}`) in .agent/config.json"
    return cmds, ""


# ---------------------------------------------------------------------------- the copy

def _is_test_path(rel: str, cfg: dict) -> bool:
    globs = cfg.get("red_tests")
    if isinstance(globs, list) and globs:
        return any(fnmatch.fnmatchcase(rel, g) for g in globs if isinstance(g, str))
    parts = rel.split("/")
    if any(p in DEFAULT_TEST_DIRS for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatchcase(parts[-1], g) for g in DEFAULT_TEST_NAMES)


def _extract(repo: Path, treeish: str, dest: Path, exclude: "list[str]") -> None:
    """`git archive <treeish>` of `repo` into `dest` (the excludes left out). A member that would land outside
    `dest` is refused — git writes none, but the copy is checked, not trusted."""
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as fh:
        r = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", treeish, "--", ".", *exclude],
                           stdout=fh, stderr=subprocess.PIPE, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"git archive {treeish[:12]} failed: "
                               f"{r.stderr.decode('utf-8', 'replace').strip()[:200]}")
        fh.seek(0)
        with tarfile.open(fileobj=fh, mode="r:") as tar:
            root = dest.resolve()
            for m in tar.getmembers():
                target = (dest / m.name).resolve()
                if target != root and root not in target.parents:
                    raise RuntimeError(f"git archive member outside the copy: {m.name}")
            tar.extractall(dest)                        # noqa: S202 — each member checked above


def _overlay_tests(repo: Path, dest: Path, cfg: dict) -> int:
    """Copy the working tree's test files (tracked or untracked, not ignored) over the base copy; a link stays a
    link. Returns how many were laid."""
    r = _git(repo, ["ls-files", "-co", "--exclude-standard", "-z"], text=False)
    if r.returncode != 0:
        raise RuntimeError("git ls-files failed in " + str(repo))
    n = 0
    for raw in r.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        if not _is_test_path(rel, cfg):
            continue
        src = repo / rel
        if not (src.is_symlink() or src.is_file()):
            continue                                    # deleted in the working tree: the base's copy stays
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)
        n += 1
    return n


def _tree_exists(repo: Path, tree: str) -> bool:
    try:
        return _git(repo, ["cat-file", "-e", f"{tree}^{{tree}}"]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _commit_of(repo: Path, rev: str) -> "str | None":
    try:
        r = _git(repo, ["rev-parse", "--verify", "-q", f"{rev}^{{commit}}"])
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


# ---------------------------------------------------------------------------- one row

# The live project's environment a row must not see (plan panel P6): a repository override would point git at the
# real repository, a PYTHONPATH could load the current code into the base copy, the host's and the CLI's own
# variables name the real project and the session.
_ENV_DROP_PREFIXES = ("GIT_", "CLAUDE", "PLAYBOOK_")
_ENV_DROP = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV_PROMPT")
LAUNCH_FAILED = (126, 127)
_SAID: "set[str]" = set()


def _row_env(environ: "dict[str, str]", home: Path, ceiling: Path, caged: bool = False) -> "dict[str, str]":
    """A row's environment. `caged`: the row runs in a sandbox (this run's, or the one the CLI is in) — then it gets
    the sandbox's own signal, PLAYBOOK_SANDBOXED=1, so a launcher inside it does not try to nest (provider/sandbox's
    `_child_env` rule)."""
    env = {k: v for k, v in environ.items() if not k.startswith(_ENV_DROP_PREFIXES) and k not in _ENV_DROP}
    env.update({"HOME": str(home), "PLAYBOOK_RED": "1", "GIT_CEILING_DIRECTORIES": str(ceiling)})
    if caged:
        env["PLAYBOOK_SANDBOXED"] = "1"
    return env


def _last_line(data: bytes) -> str:
    text = data.decode("utf-8", "replace")
    for ln in reversed(text.split("\n")):
        if ln.strip():
            return ln.strip()[:LAST_MAX]
    return ""


_ERROR_LINE = re.compile(r"^(?:[A-Za-z_][\w.]*\.)?[A-Z]\w*(?:Error|Exception|Failure|Exit|Interrupt)\b.*")


def _first_error(data: bytes) -> str:
    """The first line of the output that names an exception (`AssertionError: 1 != 2`, `ModuleNotFoundError: …`) —
    what a runner's summary line (`FAILED (errors=1)`) does not say (plan panel P5, opus)."""
    for ln in data.decode("utf-8", "replace").split("\n"):
        m = _ERROR_LINE.match(ln.strip())
        if m:
            return m.group(0)[:LAST_MAX]
    return ""


def _containment() -> "tuple[bool, str]":
    """(available, why not) — the judges' bubblewrap containment, when this host has it and is not inside a cage."""
    try:
        from provider import sandbox as _sb
    except Exception as e:                              # noqa: BLE001
        return False, f"the sandbox module cannot load ({e.__class__.__name__})"
    if _sb.is_sandboxed():
        return False, "already inside a sandbox (no nesting)"
    if not _sb._bwrap_exe():
        return False, "bubblewrap is not installed"
    return True, ""


def _argv(bash: str, cmd: str, root: Path, home: Path, contained: bool) -> "list[str]":
    if not contained:
        return [bash, "-c", cmd]
    from provider import sandbox as _sb
    # the launcher prepares an agent's home subpaths under $HOME: built with the row's throwaway HOME, so nothing is
    # made in the caller's home (a row has no agent)
    saved = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):           # the launcher's host warning: said once per run, not per row
            return _sb.build_bwrap_argv(root, None, [bash, "-c", cmd], extra_rw=[str(home)], project_writable=True)
    finally:
        if saved is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved
        said = buf.getvalue().strip()
        if said and said not in _SAID:
            _SAID.add(said)
            print(said, file=sys.stderr, flush=True)


def _run_row(cmd: str, root: Path, home: Path, ceiling: Path, timeout: float,
             contained: bool) -> "dict":
    """One command in one copy: {status, exit, seconds, last}. Its own process group: a timeout or an interrupt
    kills the group."""
    from tasks.bash_resolver import resolve_bash
    bash, _how = resolve_bash()
    if not bash:
        return {"status": "launch failed", "exit": None, "seconds": 0.0, "last": "no bash found to run the command",
                "error": ""}
    env = _row_env(dict(os.environ), home, ceiling, caged=contained or os.environ.get("PLAYBOOK_SANDBOXED") == "1")
    t0 = time.monotonic()
    p = subprocess.Popen(_argv(bash, cmd, root, home, contained), cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        out, _ = p.communicate()
        return {"status": "timeout", "exit": None, "seconds": round(time.monotonic() - t0, 2),
                "last": _last_line(out or b""), "error": _first_error(out or b"")}
    except BaseException:
        try:
            os.killpg(p.pid, signal.SIGKILL)            # an interrupt stops the row's process group too
        except OSError:
            pass
        p.wait()
        raise
    code = p.returncode
    status = "green" if code == 0 else ("launch failed" if code in LAUNCH_FAILED else "red")
    return {"status": status, "exit": code, "seconds": round(time.monotonic() - t0, 2),
            "last": _last_line(out or b""), "error": _first_error(out or b"")}


def verdict(base: str, now: str) -> str:
    """What a row's two sides prove (plan panel P5)."""
    for s in ("unrunnable", "launch failed", "timeout"):
        if s in (base, now):
            return s
    if now == "green":
        return "proved" if base == "red" else "already true"
    return "regressed" if base == "green" else "broken"


# ---------------------------------------------------------------------------- the records

def _append(task_file: Path, record: dict) -> None:
    from tasks.filelock import task_lock
    p = Path(task_file).parent / RECEIPT_NAME
    with task_lock(task_file):
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def _side(r: dict) -> str:
    if r["status"] in ("red", "green", "launch failed") and r.get("exit") is not None:
        return f"{r['status']} (exit {r['exit']}, {r['seconds']} s)"
    if r["status"] == "timeout":
        return f"timeout ({r['seconds']} s)"
    return r["status"]


def _cell(text: str) -> str:
    """A value inside a table row: no `|` to end the cell, no backtick to open a span (plan panel P8)."""
    return (text or "").replace("\\", "\\\\").replace("|", "\\|").replace("`", "'")


def _section(run: str, at: str, base_line: str, contained: str, results: "list[dict]") -> str:
    head = [RECEIPT_HEADING, "<!-- pin -->",
            f"Written by `tasks red` — run `{run}`, {at}; every run is in `{RECEIPT_NAME}` beside this file.",
            f"On the base: {base_line}. Now: the working state at the run. {contained}", "",
            "| # | Kind | Test | On the base | Now | Verdict | On the base, its first error (or last line) |",
            "|---|------|------|-------------|-----|---------|---------------------------------------------|"]
    for r in results:
        if r["status"] == "unrunnable":
            sides, last = ("unrunnable", "—"), r.get("why", "")
        else:
            sides, last = (_side(r), _side(r["now"])), r.get("error") or r.get("last", "")
        head.append(f"| {_cell(r['row'])} | {_cell(r['kind'])} | {r['test'].replace('|', chr(92) + '|')} | "
                    f"{sides[0]} | {sides[1]} | {r['verdict']} | {_cell(last)} |")
    return "\n".join(head)


def _write_section(task_file: Path, entry: str) -> None:
    """Replace `## Red receipt` (or put it right after `## Test contract`) under the task's lock."""
    from tasks.atomic import atomic_write
    from tasks.core import _physical_lines, _writer_section_span, read_task_text
    from tasks.filelock import task_lock
    with task_lock(task_file):
        text = read_task_text(task_file)
        lines = _physical_lines(text)
        new = _physical_lines(entry) + [""]
        sp = _writer_section_span(lines, RECEIPT_HEADING)
        if sp is not None:
            lines[sp[0]:sp[1]] = new
        else:
            cp = _writer_section_span(lines, CONTRACT_HEADING)
            at = cp[1] if cp is not None else len(lines)
            if at and lines[at - 1].strip():
                new = [""] + new
            lines[at:at] = new
        atomic_write(task_file, "\n".join(lines))


# ---------------------------------------------------------------------------- the command

def _parse_base_flags(args: "list[str]") -> "tuple[list[str], dict[str, str], str]":
    rest, flags = [], {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--base":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return [], {}, "--base needs a value: [<scope>=]<commit>"
            scope, _, rev = args[i + 1].rpartition("=")
            flags[scope] = rev
            i += 2
            continue
        rest.append(a)
        i += 1
    return rest, flags, ""


def _refuse(msg: str) -> None:
    print(f"Refused: {msg}. Nothing written.", file=sys.stderr)
    raise SystemExit(1)


def _plan_base(scopes: "dict[str, Path]", base: "dict | None", flags: "dict[str, str]") -> "dict[str, dict]":
    """Each scope's base for this run: a --base commit, the recorded tree (or its commit when the tree is gone and
    the base was clean), or HEAD for a scope neither names."""
    unknown = [s for s in flags if s not in scopes]
    if unknown:
        _refuse(f"--base names no scope {unknown!r} (the scopes: {sorted(scopes)!r})")
    plan: "dict[str, dict]" = {}
    recorded = (base or {}).get("scopes") or {}
    for name, repo in scopes.items():
        where = name or "the project"
        if name in flags:
            c = _commit_of(repo, flags[name])
            if not c:
                _refuse(f"--base {name + '=' if name else ''}{flags[name]} is no commit in {where}")
            plan[name] = {"commit": c, "used": "commit", "why": "--base"}
            continue
        b = recorded.get(name)
        if isinstance(b, dict):
            tree, commit = b.get("tree"), b.get("commit")
            if tree and _tree_exists(repo, tree):
                plan[name] = {"commit": commit, "tree": tree, "used": "tree"}
            elif commit and _commit_of(repo, commit) and b.get("clean"):
                plan[name] = {"commit": commit, "tree": tree, "used": "commit",
                              "why": "the working-state tree is gone (git gc?); the base was clean, so its commit "
                                     "is the same tree"}
            elif commit and _commit_of(repo, commit):
                _refuse(f"the base of {where} began from uncommitted work whose tree is gone (git gc?) — the base's "
                        "uncommitted part is gone; name a base with --base")
            else:
                _refuse(f"the base of {where} ({commit}) is not in that repository any more — name one with --base")
            continue
        c = _commit_of(repo, "HEAD")
        if not c:
            _refuse(f"{where} has no HEAD commit to run on")
        plan[name] = {"commit": c, "used": "commit", "why": "HEAD (not in the base, not named)"}
    return plan


def _tests_digest(scopes: "dict[str, Path]", cfg: dict) -> str:
    """A digest of the test files laid over the base (each scope's path and bytes, a link by its target)."""
    h = hashlib.sha256()
    for name, repo in sorted(scopes.items()):
        r = _git(repo, ["ls-files", "-co", "--exclude-standard", "-z"], text=False)
        for raw in sorted(x for x in r.stdout.split(b"\0") if x):
            rel = raw.decode("utf-8", "surrogateescape")
            if not _is_test_path(rel, cfg):
                continue
            src = repo / rel
            h.update(f"{name}/{rel}\0".encode("utf-8", "surrogateescape"))
            try:
                h.update(os.readlink(src).encode() if src.is_symlink() else src.read_bytes())
            except OSError:
                h.update(b"<gone>")
            h.update(b"\0")
    return h.hexdigest()[:16]


def cmd_red(cmd_args: "list[str]") -> None:
    """`tasks red [<N>] [--base [<scope>=]<commit>]…` — see the module docstring."""
    from tasks.core import _fingerprint_exclude_pathspecs, _find_active_task, _tail_cert_scopes, load_config
    from tasks.core import read_task_text, resolve_agent_dir
    from tasks.intent import find_task_dir
    from tasks.post_d6 import worktree_tree_why
    from tasks.shared import find_project_root
    args, flags, err = _parse_base_flags(list(cmd_args))
    if err or len(args) > 1 or (args and not args[0].isdigit()):
        print(f"Error: {err or 'usage: tasks red [<N>] [--base [<scope>=]<commit>]'}", file=sys.stderr)
        raise SystemExit(2)
    project = Path(find_project_root())
    if args:
        d = find_task_dir(resolve_agent_dir(project) / "tasks", args[0].zfill(3))
    else:
        tf = _find_active_task(project)
        d = tf.parent if tf is not None else None
    if d is None or not (d / "task.md").is_file():
        print(f"Error: tasks red: no task {args[0] if args else '(none active)'}", file=sys.stderr)
        raise SystemExit(2)
    task_file = d / "task.md"
    rows, why = parse_contract(read_task_text(task_file))
    if rows is None:
        _refuse(f"{why} — `tasks red` runs the contract's rows")
    if not rows:
        _refuse(f"`{CONTRACT_HEADING}` has no row (a table line after its `---` separator)")
    try:
        cfg = load_config(project)
    except Exception:                                   # noqa: BLE001
        cfg = {}
    scopes = _tail_cert_scopes(project, cfg)
    exclude = _fingerprint_exclude_pathspecs(cfg)
    base = _load_base(d)
    if (base is None or "failed" in base) and not flags:
        _refuse(f"task {d.name} has no recorded base ("
                + (f"its first activation could not record one: {base['failed']}" if base else
                   f"no {BASE_NAME}: it began before the base was recorded")
                + ") — name it: `tasks red <N> --base [<scope>=]<commit>` (a scope not named runs on its HEAD "
                "commit)")
    plan = _plan_base(scopes, base if base and "failed" not in base else None, flags)
    now_tree: "dict[str, str]" = {}
    for name, repo in scopes.items():
        t, why = worktree_tree_why(repo, exclude)
        if not t:
            _refuse(f"the current working state of {name or 'the project'} cannot be read ({why})")
        now_tree[name] = t
    timeout = cfg.get("red_timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        timeout = DEFAULT_TIMEOUT
    contained, why_not = _containment()
    contained_line = ("Each row ran under the judges' write containment (root read-only)." if contained else
                      f"Uncontained ({why_not}): each row ran as `tasks verify` runs the project's command.")
    run = hashlib.sha256(f"{os.getpid()} {time.time_ns()}".encode()).hexdigest()[:12]
    at = _now()
    base_line = ", ".join(
        f"{n or '(project)'} {e['commit'][:7]}" + (f" (tree {e['tree'][:7]})" if e["used"] == "tree" else
                                                   f" ({e.get('why', 'commit')})")
        for n, e in plan.items())
    tests_sha = _tests_digest(scopes, cfg)
    tmp = Path(tempfile.mkdtemp(prefix="pb-red-"))
    results: "list[dict]" = []
    try:
        broot, nroot, home = tmp / "base", tmp / "now", tmp / "home"
        home.mkdir()
        laid = 0
        for name, repo in scopes.items():
            e = plan[name]
            _extract(repo, e["tree"] if e["used"] == "tree" else e["commit"], broot / name if name else broot,
                     exclude)
            laid += _overlay_tests(repo, broot / name if name else broot, cfg)
            _extract(repo, now_tree[name], nroot / name if name else nroot, exclude)
        print(f"tasks red — task {d.name}, run {run}; base copy: {broot}", flush=True)
        print(f"  on the base: {base_line}; {laid} test file(s) from the working tree laid over it; "
              + ("contained" if contained else f"UNCONTAINED ({why_not})"), flush=True)
        for row in rows:
            cmds, why = resolve(row["test"], cfg)
            rec = {"at": _now(), "run": run, "task": d.name, "row": row["row"], "kind": row["kind"],
                   "row_sha": hashlib.sha256(row["line"].encode("utf-8")).hexdigest()[:16], "test": row["test"],
                   "base": plan, "now_tree": now_tree, "tests_sha": tests_sha, "contained": contained}
            if not cmds:
                rec.update({"status": "unrunnable", "why": why, "exit": None, "seconds": 0.0, "last": "", "error": "",
                            "command": "", "now": {"status": "unrunnable", "exit": None, "seconds": 0.0, "last": "",
                                                   "error": ""},
                            "verdict": "unrunnable"})
            else:
                command = " && ".join(f"( {c} )" for c in cmds) if len(cmds) > 1 else cmds[0]
                b = _run_row(command, broot, home, tmp, timeout, contained)
                n = _run_row(command, nroot, home, tmp, timeout, contained)
                rec.update(b)
                rec.update({"command": command, "now": n, "verdict": verdict(b["status"], n["status"])})
            _append(task_file, rec)
            results.append(rec)
            print(f"  row {rec['row']} ({rec['kind']}): {rec['verdict']} — base {rec['status']}, now "
                  f"{rec['now']['status']}" + (f" — {rec['last']}" if rec.get("last") else "")
                  + (f" — {rec['why']}" if rec.get("why") else ""), flush=True)
        _write_section(task_file, _section(run, at, base_line, contained_line, results))
        proved = sum(1 for r in results if r["verdict"] == "proved")
        print(f"  {len(results)} row(s): {proved} proved (red on the base, green now); the receipt is in task.md "
              f"(`{RECEIPT_HEADING}`) and {RECEIPT_NAME}.", flush=True)
    except KeyboardInterrupt:
        print("  interrupted: the rows that ended are in " + RECEIPT_NAME + "; task.md was not written.",
              file=sys.stderr, flush=True)
        raise SystemExit(130)
    except RuntimeError as e:
        print(f"Refused: {e}. The rows that ended are in {RECEIPT_NAME}; task.md was not written.", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
