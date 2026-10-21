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
DEFAULT_TEST_DIRS = ("tests", "test", "spec", "__tests__", "fixtures", "testdata", "__snapshots__")
DEFAULT_TEST_NAMES = ("test_*", "*_test.*", "*.test.*", "*.spec.*", "conftest.py")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


_GIT_OVERRIDES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                  "GIT_NAMESPACE", "GIT_ALTERNATE_OBJECT_DIRECTORIES")


def _git(repo: Path, args: "list[str]", *, text: bool = True, timeout: float = 120):
    """git in `repo` — whatever repository a caller's GIT_DIR / GIT_WORK_TREE names (impl round 1, agy)."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_OVERRIDES}
    if text:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=timeout, env=env)


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
    for name, repo in _scopes(Path(project_path), cfg).items():
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
    with _no_git_overrides():
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
    """A table line's cells; `\\|` is a `|` inside a cell (markdown's escape — a shell `||` is written `\\|\\|`)."""
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


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
    if any(p in DEFAULT_TEST_DIRS for p in parts):      # a `tests` link itself counts (impl round 2, agy)
        return True
    return any(fnmatch.fnmatchcase(parts[-1], g) for g in DEFAULT_TEST_NAMES)


def _real_dir(root: Path, rel: str) -> Path:
    """`root/rel` as a real directory of the copy: each ancestor and the folder itself, when a link or a file stands
    there (the outer tree's own entry), is replaced — a scope is never written THROUGH a link (impl round 2, codex
    Critical, GRAVE)."""
    cur = root
    for comp in [c for c in rel.split("/") if c] if rel else []:
        cur = cur / comp
        if cur.is_symlink() or (cur.exists() and not cur.is_dir()):
            cur.unlink()
        if not cur.exists():
            cur.mkdir()
    root.mkdir(parents=True, exist_ok=True)
    return cur


def _extract(repo: Path, treeish: str, root: Path, rel: str, exclude: "list[str]") -> None:
    """Write `treeish` of `repo` at `root/rel`: `read-tree` into a temporary index, the excludes removed from it,
    then `checkout-index --prefix` — the tree as git stores it, whatever `.gitattributes` says for `git archive`
    (`export-ignore`, `export-subst`; impl round 2, opus and codex)."""
    dest = _real_dir(root, rel)
    env = {k: v for k, v in os.environ.items() if k not in _GIT_OVERRIDES}
    with tempfile.TemporaryDirectory(prefix="pb-red-index-") as td:
        env["GIT_INDEX_FILE"] = str(Path(td) / "index")
        steps = [["read-tree", treeish]]
        drop = [spec[len(":(exclude)"):] for spec in exclude if spec.startswith(":(exclude)")]
        if drop:
            steps.append(["rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", *drop])
        steps.append(["checkout-index", "-a", "-f", f"--prefix={dest}/"])
        for args in steps:
            r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env, timeout=600)
            if r.returncode != 0:
                raise RuntimeError(f"git {args[0]} {treeish[:12]} failed in {repo}: {r.stderr.strip()[:200]}")


def _scope_of(rel: str, scope_names: "list[str]") -> "tuple[str, str]":
    """(scope, path inside it) for a path of the project copy: the longest scope name it lies under, else ""."""
    best = ""
    for name in scope_names:
        if name and (rel == name or rel.startswith(name + "/")) and len(name) > len(best):
            best = name
    return best, (rel[len(best) + 1:] if best else rel)


def _excluded(rel_in_scope: str, exclude: "list[str]") -> bool:
    for spec in exclude:
        if spec.startswith(":(exclude)"):
            p = spec[len(":(exclude)"):].strip("/")
            if p and (rel_in_scope == p or rel_in_scope.startswith(p + "/")):
                return True
    return False


def _place(dst_root: Path, rel: str) -> Path:
    """The path `rel` inside `dst_root`, every ancestor a real directory of the copy: a link or a file in the way is
    removed (it is the copy's own entry) — so nothing is ever written THROUGH a link the base had (impl round 1,
    codex Critical). The leaf is cleared too."""
    parts = rel.split("/")
    cur = dst_root
    for comp in parts[:-1]:
        cur = cur / comp
        if cur.is_symlink() or (cur.exists() and not cur.is_dir()):
            cur.unlink()
        if not cur.exists():
            cur.mkdir()
    leaf = cur / parts[-1]
    if leaf.is_symlink() or leaf.is_file():
        leaf.unlink()
    elif leaf.is_dir():
        shutil.rmtree(leaf)
    return leaf


def _test_files(root: Path, cfg: dict, scope_names: "list[str]", exclude: "list[str]") -> "list[str]":
    """The test files of a frozen copy, as paths from its root, sorted (a link counts by itself, never entered)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        names = list(filenames) + [d for d in dirnames if (here / d).is_symlink()]
        for name in names:
            rel = (here / name).relative_to(root).as_posix()
            scope, inner = _scope_of(rel, scope_names)
            if _excluded(inner, exclude) or not _is_test_path(inner, cfg):
                continue
            out.append(rel)
    return sorted(out)


def _overlay_from_copy(src_root: Path, dst_root: Path, cfg: dict, scope_names: "list[str]",
                       exclude: "list[str] | None" = None) -> int:
    """Lay the test files of the frozen "now" copy over the base copy — the same snapshot the "now" side runs, so a
    test edited during the run cannot be one thing on each side (impl round 1, codex). Returns how many were laid."""
    ex = list(exclude) if exclude else [":(exclude).agent"]
    n = 0
    for rel in _test_files(src_root, cfg, scope_names, ex):
        src = src_root / rel
        dst = _place(dst_root, rel)
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)
        n += 1
    return n


def _digest_copy(root: Path, cfg: dict, scope_names: "list[str]", exclude: "list[str] | None" = None) -> str:
    """A digest of the test files laid over the base, read from the frozen copy (a path, then its bytes or a link's
    target)."""
    ex = list(exclude) if exclude else [":(exclude).agent"]
    h = hashlib.sha256()
    for rel in _test_files(root, cfg, scope_names, ex):
        p = root / rel
        h.update(rel.encode("utf-8", "surrogateescape") + b"\0")
        h.update(os.readlink(p).encode("utf-8", "surrogateescape") if p.is_symlink() else p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:16]


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
_ENV_DROP = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT")
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


_ERROR_LINE = re.compile(r"^(?:E\s+)?((?:[A-Za-z_][\w.]*\.)?[A-Z]\w*(?:Error|Exception|Failure|Exit|Interrupt)\b.*)")


def _first_error(data: bytes) -> str:
    """The first line of the output that names an exception (`AssertionError: 1 != 2`, `ModuleNotFoundError: …`) —
    what a runner's summary line (`FAILED (errors=1)`) does not say (plan panel P5, opus)."""
    for ln in data.decode("utf-8", "replace").split("\n"):
        m = _ERROR_LINE.match(ln.strip())
        if m:
            return m.group(1)[:LAST_MAX]
    return ""


def _abs_exe(name: str) -> "str | None":
    """`name` found only on the ABSOLUTE entries of PATH, as a real path: a relative entry (`bin:`) would let a file
    of the copy, or of the project, stand in for the sandbox or the shell (impl round 2, codex Critical, GRAVE)."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or not os.path.isabs(d):
            continue
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.realpath(cand)
    return None


def _containment() -> "tuple[bool, str]":
    """(available, why not) — the judges' bubblewrap containment, when this host has it and is not inside a cage."""
    try:
        from provider import sandbox as _sb
    except Exception as e:                              # noqa: BLE001
        return False, f"the sandbox module cannot load ({e.__class__.__name__})"
    if _sb.is_sandboxed():
        return False, "already inside a sandbox (no nesting)"
    if not _abs_exe("bwrap"):
        return False, "bubblewrap is not installed (on an absolute PATH entry)"
    return True, ""


def _argv(bash: str, cmd: str, root: Path, home: Path, contained: bool, private_tmp: bool = False,
          ro_links: "list[tuple[str, str]] | None" = None, protect: "list[str] | None" = None) -> "list[str]":
    """The row's argv. Contained: the judges' bubblewrap launch, plus `--die-with-parent` (a killed run takes its row
    with it, impl round 1 opus), a private `/tmp` when the run's folder is not under it (a live project under `/tmp`
    must not be writable from a row — impl round 1, agy and codex, GRAVE), and each `red_links` folder bound read-only
    into the copy."""
    if not contained:
        return [bash, "-c", cmd]
    argv = _launcher_argv(bash, cmd, root, home)
    exe = _abs_exe("bwrap")
    if not exe or os.path.basename(argv[0]) != "bwrap":
        raise RuntimeError("the sandbox's argv does not start with bwrap, or no absolute bwrap was found")
    argv[0] = exe                                       # never a PATH lookup from the copy (impl round 2, GRAVE)
    argv.insert(1, "--die-with-parent")
    for i in range(len(argv) - 2):
        if argv[i:i + 3] == ["--bind", "/tmp", "/tmp"]:
            if private_tmp:
                argv[i + 3:i + 3] = ["--tmpfs", "/tmp"]
            else:                                       # /tmp stays shared: each live repository read-only over it
                ro = []
                for live in protect or []:
                    ro += ["--ro-bind", live, live]
                argv[i + 3:i + 3] = ro
            break
    else:
        raise RuntimeError("the sandbox's argv has no `--bind /tmp /tmp` to make private")
    tail = len(argv) - 3                                # the target: bash -c <cmd>
    extra = []
    for real, inside in ro_links or []:
        extra += ["--ro-bind", real, inside]
    argv[tail:tail] = extra
    return argv


def _launcher_argv(bash: str, cmd: str, root: Path, home: Path) -> "list[str]":
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


def _run_row(cmd: str, root: Path, home: Path, ceiling: Path, timeout: float, contained: bool,
             private_tmp: bool = False, ro_links: "list[tuple[str, str]] | None" = None,
             protect: "list[str] | None" = None) -> "dict":
    """One command in one copy: {status, exit, seconds, last, error}. Its own process group: a timeout or an
    interrupt kills the group; a child that left the group and holds the output open cannot hang the run."""
    from tasks.bash_resolver import resolve_bash
    bash, _how = resolve_bash()
    if bash and not os.path.isabs(bash):
        bash = _abs_exe(os.path.basename(bash))         # never a relative shell (impl round 2, GRAVE)
    if not bash:
        return {"status": "launch failed", "exit": None, "seconds": 0.0, "last": "no bash found to run the command",
                "error": ""}
    env = _row_env(dict(os.environ), home, ceiling, caged=contained or os.environ.get("PLAYBOOK_SANDBOXED") == "1")
    t0 = time.monotonic()
    p = subprocess.Popen(_argv(bash, cmd, root, home, contained, private_tmp, ro_links, protect), cwd=str(root), env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            out, _ = p.communicate(timeout=5)
        except subprocess.TimeoutExpired:            # a child outside the group keeps the pipe (impl round 1, sonnet)
            if p.stdout is not None:
                p.stdout.close()
            p.wait()
            out = b"(a child of the row kept its output open after the time-out; the output was cut)"
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
    try:
        os.killpg(p.pid, signal.SIGKILL)                # a child the row left behind goes with it (impl round 2)
    except OSError:
        pass
    status = "green" if code == 0 else ("launch failed" if code in LAUNCH_FAILED else "red")
    return {"status": status, "exit": code, "seconds": round(time.monotonic() - t0, 2),
            "last": _last_line(out or b""), "error": _first_error(out or b"")}


def verdict(base: str, now: str) -> str:
    """What a row's two sides prove (plan panel P5)."""
    for s in ("refused", "unrunnable", "launch failed", "timeout"):
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
        if r["status"] in ("unrunnable", "refused"):
            sides, last = (r["status"], "—"), r.get("why", "")
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


_GUARD = None


def _guard(cmd: str, cfg: dict) -> "tuple[bool, str]":
    """The destructive-command interlock the agent's own shell meets, for a row's command (impl round 1, sonnet):
    the config's `command_guard: false` and `dangerous_commands`. A guard that cannot load or judge refuses (fails
    closed)."""
    global _GUARD
    if cfg.get("command_guard") is False:
        return True, ""
    # NOT `PLAYBOOK_ALLOW_DANGEROUS`: in this process it is the agent's environment, and the guard's own hook refuses
    # a permission an agent can give itself (impl round 2, opus — a guard failing open)
    try:
        if _GUARD is None:
            import importlib.util
            path = Path(__file__).resolve().parent.parent / "scripts" / "command_guard.py"
            spec = importlib.util.spec_from_file_location("_pb_red_command_guard", path)
            if spec is None or spec.loader is None:
                raise ImportError(str(path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _GUARD = mod
        verdict, name, why = _GUARD.classify_command(cmd, cfg.get("dangerous_commands"))
    except Exception as e:                              # noqa: BLE001 — fail closed
        return False, f"the command guard could not judge it ({e.__class__.__name__}: {e}) — refused"
    if verdict == "block":
        return False, f"the command guard blocks it: {name} — {why}"
    return True, ""


def _run_parent() -> "str | None":
    """Where the run's folder goes: /var/tmp when it can, so a row's `/tmp` can be private (impl round 1, GRAVE)."""
    vt = "/var/tmp"
    return vt if os.path.isdir(vt) and os.access(vt, os.W_OK | os.X_OK) else None


def _links(project: Path, cfg: dict) -> "tuple[list[tuple[str, str]], list[str]]":
    """`red_links`: project-relative folders the copies get from the real tree (an ignored `node_modules`, `.venv`)
    — ([(real path, path in the copy)], notes)."""
    raw = cfg.get("red_links")
    out, notes = [], []
    if not isinstance(raw, list):
        return out, notes
    for rel in raw:
        if not isinstance(rel, str) or not rel.strip() or rel.startswith("/") or ".." in Path(rel).parts:
            notes.append(f"red_links entry {rel!r} skipped (a project-relative path with no `..`)")
            continue
        real = (project / rel).resolve()
        if not real.exists():
            notes.append(f"red_links {rel} does not exist in the project")
            continue
        out.append((str(real), rel.strip("/")))
    return out, notes


def _prepare_links(copy: Path, links: "list[tuple[str, str]]", contained: bool, notes: "list[str]",
                   side: str) -> "set[str]":
    """Contained: an empty folder where each link is bound read-only. Uncontained: a link to the real folder.
    Returns the links this copy got — each copy decides alone (impl round 2, codex)."""
    ready: "set[str]" = set()
    for real, rel in links:
        at = copy / rel
        if at.exists() or at.is_symlink():
            notes.append(f"red_links {rel}: the {side} copy already has it — its own is used")
            continue
        _real_dir(copy, str(Path(rel).parent) if str(Path(rel).parent) != "." else "")
        if contained:
            at.mkdir()
        else:
            os.symlink(real, at)
        ready.add(rel)
    return ready


def _side_run(command: str, pristine: Path, work: Path, home: Path, ceiling: Path, timeout: float, contained: bool,
              private_tmp: bool, links: "list[tuple[str, str]]", ready: "set[str]",
              protect: "list[str] | None" = None) -> "dict":
    """One side of one row in a fresh copy of `pristine` and a fresh HOME (impl round 1, codex: a side's writes must
    not reach the other side, nor the next row)."""
    shutil.copytree(pristine, work, symlinks=True)
    home.mkdir(parents=True)
    try:
        ro = [(real, str(work / rel)) for real, rel in links if rel in ready] if contained else None
        return _run_row(command, work, home, ceiling, timeout, contained, private_tmp, ro, protect)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


@contextlib.contextmanager
def _no_git_overrides():
    """The CLI's own git — here and in what it calls (`post_d6.worktree_tree_why`) — in the repository it names,
    whatever GIT_DIR / GIT_WORK_TREE the caller exported (impl round 2, agy and opus)."""
    saved = {k: os.environ.pop(k) for k in _GIT_OVERRIDES if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _norm_scope(name: str) -> str:
    """A scope's name as the copy's paths spell it: `./code/` is `code` (impl round 2, codex)."""
    n = name.replace("\\", "/").strip()
    while n.startswith("./"):
        n = n[2:]
    return n.strip("/")


def _scopes(project: Path, cfg: dict) -> "dict[str, Path]":
    from tasks.core import _tail_cert_scopes
    return {_norm_scope(n): p for n, p in _tail_cert_scopes(project, cfg).items()}


def _combine(verdicts: "list[str]") -> str:
    """A row's verdict from its tests' (impl round 2, opus): proved only when each is proved."""
    if verdicts and all(v == "proved" for v in verdicts):
        return "proved"
    if verdicts and all(v == "already true" for v in verdicts):
        return "already true"
    for s in ("refused", "unrunnable", "launch failed", "timeout", "broken", "regressed"):
        if s in verdicts:
            return s
    return "partly proved"


def cmd_red(cmd_args: "list[str]") -> None:
    """`tasks red [<N>] [--base [<scope>=]<commit>]…` — see the module docstring."""
    with _no_git_overrides():
        _cmd_red(cmd_args)


def _cmd_red(cmd_args: "list[str]") -> None:
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
    scopes = _scopes(project, cfg)
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
    if contained and _run_parent() is None:
        contained_line += " /tmp shared (no usable /var/tmp): each live repository was bound read-only."
    run = hashlib.sha256(f"{os.getpid()} {time.time_ns()}".encode()).hexdigest()[:12]
    at = _now()
    base_line = ", ".join(
        f"{n or '(project)'} {e['commit'][:7]}" + (f" (tree {e['tree'][:7]})" if e["used"] == "tree" else
                                                   f" ({e.get('why', 'commit')})")
        for n, e in plan.items())
    links, link_notes = _links(project, cfg)
    tmp = Path(tempfile.mkdtemp(prefix="pb-red-", dir=_run_parent()))
    private_tmp = contained and not str(tmp.resolve()).startswith("/tmp/")
    results: "list[dict]" = []
    old_handlers = {}

    def _stop(signum, _frame):                          # SIGTERM / SIGHUP take the interrupt's path (impl round 1)
        raise KeyboardInterrupt(f"signal {signum}")
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            old_handlers[sig] = signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass
    try:
        bprist, nprist = tmp / "pristine-base", tmp / "pristine-now"
        order = sorted(scopes, key=lambda n: (n.count("/") + (1 if n else 0), n))   # the project first, then inward
        for name in order:
            repo, e = scopes[name], plan[name]
            _extract(repo, e["tree"] if e["used"] == "tree" else e["commit"], bprist, name, exclude)
            _extract(repo, now_tree[name], nprist, name, exclude)
        laid = _overlay_from_copy(nprist, bprist, cfg, list(scopes), exclude)
        tests_sha = _digest_copy(nprist, cfg, list(scopes), exclude)
        ready = {"base": _prepare_links(bprist, links, contained, link_notes, "base"),
                 "now": _prepare_links(nprist, links, contained, link_notes, "now")}
        protect: "list[str]" = []
        if contained and not private_tmp:              # /tmp shared: each live repository read-only over it
            for repo in scopes.values():
                live = str(Path(repo).resolve())
                if not str(tmp.resolve()).startswith(live + "/"):
                    protect.append(live)
        print(f"tasks red — task {d.name}, run {run}; base copy: {bprist}", flush=True)
        print(f"  on the base: {base_line}; {laid} test file(s) from the working state laid over it; "
              + ("contained" + ("" if private_tmp else " (/tmp shared: each live repository bound read-only)")
                 if contained else f"UNCONTAINED ({why_not})"), flush=True)
        for note in link_notes:
            print("  note: " + note, flush=True)
        for k, row in enumerate(rows, 1):
            cmds, why = resolve(row["test"], cfg)
            rec = {"at": _now(), "run": run, "task": d.name, "row": row["row"], "kind": row["kind"],
                   "row_sha": hashlib.sha256(row["line"].encode("utf-8")).hexdigest()[:16], "test": row["test"],
                   "base": plan, "now_tree": now_tree, "tests_sha": tests_sha, "contained": contained,
                   "private_tmp": private_tmp}
            empty = {"exit": None, "seconds": 0.0, "last": "", "error": ""}
            if not cmds:
                rec.update(dict(empty, status="unrunnable", why=why, command="", tests=[],
                                now=dict(empty, status="unrunnable"), verdict="unrunnable"))
            else:
                tests = []
                for j_, c in enumerate(cmds, 1):           # each test on its own, on each side (impl round 2, opus)
                    ok, guard_why = _guard(c, cfg)
                    if not ok:
                        t = {"command": c, "base": dict(empty, status="refused"), "now": dict(empty, status="refused"),
                             "verdict": "refused", "why": guard_why}
                    else:
                        b = _side_run(c, bprist, tmp / "rows" / f"{k}-{j_}-base", tmp / "homes" / f"{k}-{j_}-base",
                                      tmp, timeout, contained, private_tmp, links, ready["base"], protect)
                        n = _side_run(c, nprist, tmp / "rows" / f"{k}-{j_}-now", tmp / "homes" / f"{k}-{j_}-now",
                                      tmp, timeout, contained, private_tmp, links, ready["now"], protect)
                        t = {"command": c, "base": b, "now": n, "verdict": verdict(b["status"], n["status"])}
                    tests.append(t)
                first = tests[0]
                rec.update(first["base"])
                rec.update({"command": " ; ".join(t["command"] for t in tests), "now": first["now"],
                            "tests": tests, "verdict": _combine([t["verdict"] for t in tests])})
                why_all = "; ".join(t["why"] for t in tests if t.get("why"))
                if why_all:
                    rec["why"] = why_all
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
    except (RuntimeError, ValueError, tarfile.TarError) as e:
        print(f"Refused: {e}. The rows that ended are in {RECEIPT_NAME}; task.md was not written.", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for sig, h in old_handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass
