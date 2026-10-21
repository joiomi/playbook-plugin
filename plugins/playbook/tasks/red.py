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

Part 2 (task 281) reads the records: a side that passed no test is "skipped" (unittest's and pytest's summaries; any
other runner's skip reads green, and the side says no reader matched), a green side that skipped a test "partly
skipped" — neither proves. `--rows` runs only some rows; `## Red receipt` is the state across runs (`compute_state`:
each row's newest record by its key — kind, behavior, test — else by its number); a row not in the first run's
contract is "added later" and proves only on a `--base` its run named. `--check` and the close (`close_report`,
advisory) print each row's state, stale when its text, its command or the test files changed since its record.
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
        if name:
            # a code root must be its own repository: a folder of the project's own repository is read through it
            # (the base would be the project's tree, not the folder's)
            top = _git(repo, ["rev-parse", "--show-toplevel"])
            if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(repo):
                return None, f"{where} is not its own git repository", exclude
        commit = head.stdout.strip() if head.returncode == 0 else ""
        if not commit:
            # an unborn repository (no commit yet — the first task of a project started from zero, impl round 3,
            # opus): its base is its working tree alone; anything that is not a work tree is a failure
            wt = _git(repo, ["rev-parse", "--is-inside-work-tree"])
            if wt.returncode != 0 or wt.stdout.strip() != "true":
                return None, f"{where} is not a git work tree", exclude
        tree, why = worktree_tree_why(repo, exclude)
        entry: "dict" = {"commit": commit or None, "tree": tree}
        if tree and commit:
            ct = _git(repo, ["rev-parse", "--verify", "-q", f"{commit}^{{tree}}"])
            entry["clean"] = ct.returncode == 0 and ct.stdout.strip() == tree
        else:
            entry["clean"] = False
            if not tree:
                entry["tree_error"] = why or "unknown"
                if not commit:
                    return None, f"{where} has no commit and its working tree cannot be read ({why})", exclude
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


def _safe_parts(rel: str) -> "list[str]":
    """`rel`'s parts, each a plain name: a `..`, `.`, empty or absolute part is refused — no name, however it is
    spelled, reaches outside the copy (impl round 3, from codex's run: a `code_roots` name with backslashes, rewritten
    by my old normalization into `../../…`, made the unpack overwrite a folder outside the copy — GRAVE)."""
    if rel.startswith("/"):
        raise RuntimeError(f"refused path {rel!r}: absolute")
    parts = rel.split("/") if rel else []
    for p in parts:
        if p in ("", ".", ".."):
            raise RuntimeError(f"refused path {rel!r}: it holds {p!r} — every part must be a plain name")
    return parts


def _inside(root: Path, target: Path) -> bool:
    a, b = os.path.normpath(os.path.abspath(root)), os.path.normpath(os.path.abspath(target))
    return b == a or b.startswith(a + os.sep)


def _real_dir(root: Path, rel: str) -> Path:
    """`root/rel` as a real directory of the copy: the root first, then each ancestor and the folder itself — a link
    or a file standing there (the outer tree's own entry) is replaced, so a scope is never written THROUGH a link
    (impl round 2, codex Critical, GRAVE)."""
    parts = _safe_parts(rel)
    root.mkdir(parents=True, exist_ok=True)
    cur = root
    for comp in parts:
        cur = cur / comp
        if not _inside(root, cur):
            raise RuntimeError(f"refused path {rel!r}: outside the copy")
        if cur.is_symlink() or (cur.exists() and not cur.is_dir()):
            cur.unlink()
        if not cur.exists():
            cur.mkdir()
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
            steps.append(["rm", "--cached", "-r", "-q", "-f", "--ignore-unmatch", "--", *drop])
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
    parts = _safe_parts(rel)
    cur = _real_dir(dst_root, "/".join(parts[:-1])) if len(parts) > 1 else dst_root
    leaf = cur / parts[-1]
    if not _inside(dst_root, leaf):
        raise RuntimeError(f"refused path {rel!r}: outside the copy")
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
    present = set(_test_files(src_root, cfg, scope_names, ex))
    for rel in _test_files(dst_root, cfg, scope_names, ex):
        if rel not in present:                          # a test the task deleted leaves the base copy (impl round 3)
            _place(dst_root, rel)
    n = 0
    for rel in sorted(present):
        src = src_root / rel
        dst = _place(dst_root, rel)
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
                "error": "", "reader": "", "skips": 0}
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
                "last": _last_line(out or b""), "error": _first_error(out or b""), "reader": "", "skips": 0}
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
    status, reader, skips = _side_status(code, out or b"")
    return {"status": status, "exit": code, "seconds": round(time.monotonic() - t0, 2),
            "last": _last_line(out or b""), "error": _first_error(out or b""), "reader": reader, "skips": skips}


# A test run's summary (task 281, PHASE-B 9b part 2): a run that passed no test is no proof. Only these two runners'
# summaries are read — any other runner's skip reads green, and the side records that no reader matched.
_UT_RAN = re.compile(r"(?m)^Ran (\d+) tests? in [\d.]+s\s*$")
_UT_END = re.compile(r"(?m)^(OK|FAILED)(?: \(([^)\n]*)\))?\s*$")
_PY_SUMMARY = re.compile(r"(?m)^=*\s*((?:\d+ [a-z]+)(?:, \d+ [a-z]+)*) in [\d.]+s(?: \([^)\n]*\))?\s*=*\s*$")


def _summary(out: bytes) -> "tuple[str, int, int] | None":
    """(reader, tests that passed, tests skipped) from the LAST unittest or pytest summary in `out` (a test that
    printed one of its own earlier does not count), or None when neither is there."""
    text = out.decode("utf-8", "replace")
    ut = list(_UT_RAN.finditer(text))
    py = list(_PY_SUMMARY.finditer(text))
    if ut and (not py or ut[-1].start() > py[-1].start()):
        m = ut[-1]
        ran = int(m.group(1))
        end = _UT_END.search(text, m.end())
        counts: "dict[str, int]" = {}
        for part in (end.group(2) or "").split(",") if end else ():
            k, _, v = part.strip().partition("=")
            if v.strip().isdigit():
                counts[k.strip()] = int(v)
        skipped = counts.get("skipped", 0)
        not_passed = (skipped + counts.get("expected failures", 0) + counts.get("failures", 0)
                      + counts.get("errors", 0))
        return "unittest", max(ran - not_passed, 0), skipped     # a class-level skip: Ran 0 with skipped=1
    if py:
        counts = {}
        for part in py[-1].group(1).split(","):
            n, _, word = part.strip().partition(" ")
            counts[word] = counts.get(word, 0) + int(n)
        return "pytest", counts.get("passed", 0), counts.get("skipped", 0) + counts.get("xfailed", 0)
    return None


def _no_test_passed(out: bytes) -> bool:
    """The output's last summary shows that no test passed (all skipped, expected failures, or none ran)."""
    s = _summary(out)
    return s is not None and s[1] <= 0


def _side_status(code: int, out: bytes) -> "tuple[str, str, int]":
    """(status, reader, skips) of one side: a zero exit that passed no test is `skipped`, never green."""
    s = _summary(out)
    reader, skips = (s[0], s[2]) if s else ("", 0)
    if code != 0:
        return ("launch failed" if code in LAUNCH_FAILED else "red"), reader, skips
    if s is not None and s[1] <= 0:
        return "skipped", reader, skips
    return "green", reader, skips


def verdict(base: str, now: str, now_skips: int = 0) -> str:
    """What a row's two sides prove (plan panel P5). A side that passed no test proves nothing, and a green now side
    that skipped a test cannot prove either — the skipped one may be the test the row names (task 281, codex)."""
    for s in ("refused", "unrunnable", "launch failed", "timeout"):
        if s in (base, now):
            return s
    if now == "red":
        return "regressed" if base == "green" else "broken"
    if "skipped" in (base, now):
        return "skipped"
    if now_skips:
        return "partly skipped"
    return "proved" if base == "red" else "already true"


# ---------------------------------------------------------------------------- the records

def _append(task_file: Path, record: dict) -> None:
    from tasks.filelock import task_lock
    p = Path(task_file).parent / RECEIPT_NAME
    with task_lock(task_file):
        torn = False                                    # a run killed mid-line: the next record starts its own line
        try:
            with open(p, "rb") as fh:
                if fh.seek(0, 2):
                    fh.seek(-1, 2)
                    torn = fh.read(1) != b"\n"
        except FileNotFoundError:
            pass
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(("\n" if torn else "") + json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
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


def _row_table_line(e: dict) -> str:
    """One row of the section's table — a row not proved, from its newest record (or none)."""
    r = e.get("rec")
    test = e["test"].replace("|", chr(92) + "|")
    state = e["state"] + (f" — {e['why']} (was: {e['verdict']})" if e["state"] == "stale" else "")
    if r is None:
        return f"| {_cell(e['row'])} | {_cell(e['kind'])} | {test} | — | — | {_cell(state)} |  | — |"
    tests = r.get("tests") if isinstance(r.get("tests"), list) else []
    tests = [t for t in tests if isinstance(t, dict) and isinstance(t.get("base"), dict)
             and isinstance(t.get("now"), dict)]
    now = r.get("now") if isinstance(r.get("now"), dict) else {}
    if r.get("status") == "unrunnable" and not tests:
        sides, last = ("unrunnable", "—"), str(r.get("why", ""))
    elif len(tests) > 1:
        each = "; ".join(f"{k}: {t['base'].get('status')} → {t['now'].get('status')} ({t.get('verdict')})"
                         for k, t in enumerate(tests, 1))
        sides = (each, "(each test on the left)")
        last = str(r.get("why") or next((t["base"].get("error") or t["base"].get("last") for t in tests
                                         if t.get("verdict") != "proved"), "") or "")
    elif tests and tests[0].get("verdict") == "refused":
        sides, last = ("refused", "—"), str(r.get("why", ""))
    else:
        sides, last = (_side(r), _side(now)), str(r.get("error") or r.get("last", ""))
    return (f"| {_cell(e['row'])} | {_cell(e['kind'])} | {test} | {_cell(sides[0])} | {_cell(sides[1])} | "
            f"{_cell(state)} | {_cell(last)} | `{_cell(str(r.get('run', '')))}` |")


def _section(st: dict, contained: str) -> str:
    """The state across runs, for the judges (task 281; agy): each row's newest record — the head names each run that
    is some row's newest, with its base; a count of each state with its rows; the rows added later, those no skip
    reader matched; a table of the rows NOT proved, each with its run. Every record is in the jsonl."""
    rows = st["rows"]
    newest = st.get("newest") or {}
    head = [RECEIPT_HEADING, "<!-- pin -->",
            f"Written by `tasks red` — the state across its runs: each row's newest record in `{RECEIPT_NAME}` beside "
            f"this file. The newest run `{newest.get('run', '')}`, {newest.get('at', '')}. {contained}"]
    if st["runs"]:
        head.append("Runs: " + "; ".join(f"`{r['run']}` {r['at']} on the base {r['base']}" for r in st["runs"]) + ".")
    head.append("")
    order: "list[str]" = []
    by: "dict[str, list[str]]" = {}
    for e in rows:
        if e["state"] not in by:
            order.append(e["state"])
            by[e["state"]] = []
        by[e["state"]].append(_cell(e["row"]))
    head.append("State: " + "; ".join(f"{v}: {len(by[v])} ({_rows_word(by[v])})" for v in order) + ".")
    head += [f"Note: {_cell(n)}." for n in st["notes"]]
    late = [e for e in rows if e["late"]]
    if late:
        head.append("Added later (not in the first run's contract): " + "; ".join(
            f"row {_cell(e['row'])}" + (f" — run `{e['rec'].get('run', '')}`, on {_base_short(e['rec'].get('base'))}"
                                        if e.get("rec") else " — no record") for e in late) + ".")
    noreader = [_cell(e["row"]) for e in rows if e.get("noreader")]
    if noreader:
        head.append(f"No skip reader matched: {_rows_word(noreader)} (only unittest's and pytest's summaries are read — "
                    "a skip under another runner reads green).")
    moved = [_cell(e["row"]) for e in rows if e.get("moved")]
    if moved:
        head.append(f"The working state changed since the run of: {_rows_word(moved)} (their green side was an older "
                    "tree).")
    rest = [e for e in rows if e["state"] != "proved"]
    if rest:
        head += ["", "| # | Kind | Test | On the base | Now | State | On the base, its first error (or last line) | Run |",
                 "|---|------|------|-------------|-----|-------|---------------------------------------------|-----|"]
        head += [_row_table_line(e) for e in rest]
    return "\n".join(head)


def _rows_word(labels: "list[str]") -> str:
    return ("rows " if len(labels) > 1 else "row ") + ", ".join(labels)


def _write_section(task_file: Path, make) -> None:
    """Replace `## Red receipt` (or put it right after `## Test contract`) under the task's lock — `make(records)`
    renders it from the receipt as read under the same lock, so a run that ends meanwhile is in it."""
    from tasks.atomic import atomic_write
    from tasks.core import _physical_lines, _writer_section_span, read_task_text
    from tasks.filelock import task_lock
    with task_lock(task_file):
        entry = make(*_read_records(Path(task_file).parent / RECEIPT_NAME))
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


# ---------------------------------------------------------------------------- the state across runs (task 281)

def row_key(row: dict) -> str:
    """A row's identity across runs: its kind, behavior and test — not its number, so renumbering keeps it."""
    return hashlib.sha256("\0".join((row["kind"], row["behavior"], row["test"])).encode("utf-8")).hexdigest()[:16]


def _read_records(path: Path) -> "tuple[list[dict], int]":
    """(the receipt's records in file order, how many were written before the row key). A line that is not UTF-8, not
    JSON, or not a record of the shape the state reads is skipped (plan panel: sonnet); a record with no `row_key` /
    `contract` (task 280's) cannot say which row it was, so it is counted and not read — never "its text changed" or
    "added later" (found by the dogfood on task 280). A receipt that cannot be read raises OSError."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return [], 0
    out, old = [], 0
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not (isinstance(rec, dict) and all(isinstance(rec.get(k), str) for k in ("row", "run", "verdict", "row_sha"))):
            continue
        if isinstance(rec.get("row_key"), str) and isinstance(rec.get("contract"), list):
            out.append(rec)
        else:
            old += 1
    return out, old


def _named_base(rec: dict) -> bool:
    b = rec.get("base")
    return isinstance(b, dict) and any(isinstance(e, dict) and e.get("why") == "--base" for e in b.values())


def _base_line(plan) -> str:
    """A run's base as the section names it: `code 1234567 (tree 89abcde)`."""
    if not isinstance(plan, dict):
        return "(unknown)"
    out = []
    for n, e in plan.items():
        if not isinstance(e, dict):
            continue
        c = str(e.get("commit") or "unborn")[:7]
        tail = f" (tree {str(e.get('tree') or '')[:7]})" if e.get("used") == "tree" else f" ({e.get('why', 'commit')})"
        out.append(f"{n or '(project)'} {c}{tail}")
    return ", ".join(out) or "(unknown)"


def _base_short(plan) -> str:
    """A late row's base: `(project)=1234567, code=89abcde (--base)`."""
    if not isinstance(plan, dict):
        return "(unknown)"
    return ", ".join(f"{n or '(project)'}={str(e.get('commit') or 'unborn')[:7]}"
                     + (" (--base)" if e.get("why") == "--base" else "")
                     for n, e in plan.items() if isinstance(e, dict)) or "(unknown)"


def _sides(rec: dict) -> "list[dict]":
    """The sides of a record that ran (red, green or skipped), across its tests."""
    tests = rec.get("tests") if isinstance(rec.get("tests"), list) else []
    out = []
    for t in tests:
        if isinstance(t, dict):
            for side in (t.get("base"), t.get("now")):
                if isinstance(side, dict) and side.get("status") in ("red", "green", "skipped"):
                    out.append(side)
    return out


def compute_state(rows: "list[dict]", records: "list[dict]", cfg: dict, trees: "dict[str, str] | None",
                  tests_now: "str | None", old: int = 0) -> dict:
    """Each contract row's state from the receipt's records (file order: the newest per row wins). A row is matched by
    its key (kind, behavior, test), else by its number — then its text changed. `trees` / `tests_now` are today's
    working state (None: not read, so no test or tree staleness is claimed either way)."""
    by_key: "dict[str, dict]" = {}
    by_label: "dict[str, dict]" = {}
    for rec in records:
        by_key[rec["row_key"]] = rec
        by_label[rec["row"]] = rec
    first: "set | None" = ({k for k in records[0]["contract"] if isinstance(k, str)} if records else None)
    out = []
    keys_now = {row_key(r) for r in rows}
    for row in rows:
        key = row_key(row)
        cmds, _why = resolve(row["test"], cfg)
        e: dict = {"row": row["row"], "kind": row["kind"], "test": row["test"], "key": key,
                   "late": first is not None and key not in first, "rec": None, "verdict": ""}
        rec = by_key.get(key)
        why: "list[str]" = []
        prev = by_label.get(row["row"])
        if rec is None and prev is not None and prev.get("row_key") not in keys_now:
            rec, why = prev, ["the row's text"]         # its number's record, of a text no row has now
        if rec is None:
            e["state"] = "no record"
            out.append(e)
            continue
        if not why:
            if str(rec.get("command", "")) != " ; ".join(cmds):
                why.append("the command")
            if tests_now is not None and rec.get("tests_sha") != tests_now:
                why.append("the test files")
        e["rec"], e["verdict"] = rec, rec["verdict"]
        if why:
            e["state"], e["why"] = "stale", " and ".join(why) + " changed"
        elif rec["verdict"] == "proved" and e["late"] and not _named_base(rec):
            e["state"] = "proved on the task's start only"
        else:
            e["state"] = rec["verdict"]
        nt = rec.get("now_tree")
        e["moved"] = (e["state"] != "stale" and trees is not None and isinstance(nt, dict)
                      and any(nt.get(n) != t for n, t in trees.items()))
        sides = _sides(rec)
        e["noreader"] = bool(sides) and all(s.get("reader") == "" for s in sides)
        out.append(e)
    latest_runs: "list[str]" = []
    for e in out:
        if e["rec"] is not None and e["rec"]["run"] not in latest_runs:
            latest_runs.append(e["rec"]["run"])
    runs = []
    for run in [r["run"] for r in records if r["run"] in latest_runs]:
        if run not in [x["run"] for x in runs]:
            rec = next(r for r in records if r["run"] == run)
            runs.append({"run": run, "at": str(rec.get("at", "")), "base": _base_line(rec.get("base"))})
    labels = [r["row"] for r in rows]
    notes = [f"row {lab} appears twice — `tasks red` refuses the contract until each row has its own number"
             for lab in sorted({x for x in labels if x and labels.count(x) > 1})]
    if any(not lab for lab in labels):
        notes.append("a row with no number — `tasks red` refuses the contract until each row has its own number")
    if old:
        notes.append(f"{old} record(s) without a row key (written before task 281) are not read — `tasks red` "
                     "records the rows again")
    return {"rows": out, "runs": runs, "notes": notes,
            "newest": {"run": records[-1]["run"], "at": str(records[-1].get("at", ""))} if records else {},
            "records": len(records)}


_GROUPS = ("proved", "already true", "skipped", "not proved", "no record", "stale")


def _group(state: str) -> str:
    if state in ("proved", "already true", "no record", "stale"):
        return state
    if state in ("skipped", "partly skipped"):
        return "skipped"
    if state == "proved on the task's start only":
        return "start only"
    return "not proved"


def render_state(st: dict, task_ref: str, header: str) -> "list[str]":
    """The state as `--check` and the close print it: a line per row, the counts, and the commands that refresh."""
    rows = st["rows"]
    lines = [header]
    for note in st["notes"]:
        lines.append("  note: " + note)
    for e in rows:
        s = f"  row {e['row']}: "
        if e["state"] == "stale":
            s += f"stale — {e['why']} since its run (was: {e['verdict']})"
        else:
            s += e["state"] + (f" (run {e['rec']['run']})" if e.get("rec") else "")
        if e["late"]:
            s += " — added later" + (f", on {_base_short(e['rec'].get('base'))}" if e.get("rec") else "")
        if e.get("moved"):
            s += " — the working state changed since its run"
        if e.get("noreader"):
            s += " — no skip reader matched its output"
        lines.append(s)
    count = {g: [e["row"] for e in rows if _group(e["state"]) == g] for g in _GROUPS + ("start only",)}
    late = [e["row"] for e in rows if e["late"]]
    moved = [e["row"] for e in rows if e.get("moved")]
    noreader = [e["row"] for e in rows if e.get("noreader")]

    def n(label: str, xs: "list[str]") -> str:
        return f"{label} {len(xs)}" + (f" ({_rows_word(xs)})" if xs and label not in _GROUPS else "")
    lines.append("  " + ", ".join(n(g, count[g]) for g in _GROUPS) + "; "
                 + "; ".join((n("on the task's start only", count["start only"]), n("added later", late),
                              n("working state changed since the run", moved), n("no skip reader", noreader))))
    if st.get("unchecked"):
        lines.append(f"  (staleness not checked: {st['unchecked']} — the rows' text and commands were)")
    plain = [e["row"] for e in rows if not e["late"] and e["state"] in ("stale", "no record")]
    needs_base = [e["row"] for e in rows if e["late"] and e["state"] in ("stale", "no record",
                                                                         "proved on the task's start only")]
    if plain:
        lines.append(f"  refresh: tasks red {task_ref} --rows {','.join(plain)}")
    if needs_base:
        lines.append(f"  added later, each needs its own base: tasks red {task_ref} --rows {','.join(needs_base)} "
                     "--base <scope>=<the commit before its fix>")
    return lines


def _today(project: Path, cfg: dict, deadline: "float | None") -> "tuple[dict[str, str] | None, str | None, str]":
    """Each scope's working-state tree and the tests digest read from them — (None, None, why) when one cannot be
    read, or the deadline passed before it (the close's budget; plan panel: sonnet)."""
    from tasks.core import _fingerprint_exclude_pathspecs
    from tasks.post_d6 import worktree_tree_why
    scopes = _scopes(project, cfg)
    exclude = _fingerprint_exclude_pathspecs(cfg)
    trees: "dict[str, str]" = {}
    for name, repo in scopes.items():
        if deadline is not None and time.monotonic() >= deadline:
            return None, None, "the time budget ran out"
        t, why = worktree_tree_why(repo, exclude)
        if not t:
            return None, None, f"the working state of {name or 'the project'} cannot be read ({why})"
        trees[name] = t
    try:
        return trees, _digest_trees(scopes, trees, cfg, exclude), ""
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return None, None, f"the test files cannot be listed ({exc})"


def state_of(project: Path, task_dir: Path, rows: "list[dict]", cfg: dict,
             deadline: "float | None" = None) -> dict:
    """The state of `task_dir`'s contract rows today — reads task.md's rows (given), the receipt and git; writes
    nothing (git may store the working state's objects, as the close's freshness check does)."""
    records, old = _read_records(Path(task_dir) / RECEIPT_NAME)
    with _no_git_overrides():
        trees, tests_now, why = _today(Path(project), cfg, deadline)
    st = compute_state(rows, records, cfg, trees, tests_now, old)
    st["unchecked"] = why
    return st


_GATE_LINE = re.compile(r"(?m)^[ \t]*- \[[ xX]\] Test contract:")


def close_report(project: Path, task_file: Path, budget: float = 20.0) -> "list[str]":
    """The close's lines (task 281, advisory: it never stops a close): each row's state, for a task with a live
    `## Test contract` or the contract gate — none for any other task (an older one, light, research)."""
    from tasks.core import load_config, read_task_text
    head = "red receipt (advisory — this close does not wait for it)"
    text = read_task_text(Path(task_file))
    rows, why = parse_contract(text)
    if rows is None:
        return [f"{head}: {why} — the task has the `Test contract:` gate"] if _GATE_LINE.search(text) else []
    try:
        cfg = load_config(Path(project))
    except Exception:                                   # noqa: BLE001
        cfg = {}
    st = state_of(Path(project), Path(task_file).parent, rows, cfg, time.monotonic() + budget)
    d = Path(task_file).parent
    return render_state(st, d.name.split("-", 1)[0],
                        f"{head}: task {d.name}, {len(rows)} row(s), {st['records']} record(s)")


def numbers_line(project: Path, task_dir: Path, budget: float = 10.0) -> "str | None":
    """`tasks numbers`'s contract line, read live (never written into numbers.json): rows, proved, not proved, added
    later — None for a task without a live `## Test contract`."""
    from tasks.core import load_config, read_task_text
    rows, _why = parse_contract(read_task_text(Path(task_dir) / "task.md"))
    if rows is None:
        return None
    try:
        cfg = load_config(Path(project))
    except Exception:                                   # noqa: BLE001
        cfg = {}
    st = state_of(Path(project), Path(task_dir), rows, cfg, time.monotonic() + budget)
    proved = sum(1 for e in st["rows"] if e["state"] == "proved")
    late = sum(1 for e in st["rows"] if e["late"])
    return (f"contract: {len(rows)} row(s) — proved {proved}, not proved {len(rows) - proved}, added later {late}"
            + (f" (staleness not checked: {st['unchecked']})" if st.get("unchecked") else ""))


def _digest_trees(scopes: "dict[str, Path]", trees: "dict[str, str]", cfg: dict, exclude: "list[str]") -> str:
    """A digest of the test files in each scope's working-state tree — a path, its mode and its blob — the same from
    a run (its frozen "now" tree) and from a check, with no copy (task 281). Gitlinks are left to their own scope."""
    ex = list(exclude) if exclude else [":(exclude).agent"]
    entries = []
    for name in sorted(scopes):
        r = _git(scopes[name], ["ls-tree", "-r", "-z", "--full-tree", trees[name]], text=False)
        if r.returncode != 0:
            raise RuntimeError(f"git ls-tree failed in {name or 'the project'}")
        for ent in r.stdout.split(b"\0"):
            meta, tab, path = ent.partition(b"\t")
            parts = meta.split(b" ")
            if not tab or len(parts) != 3 or parts[1] != b"blob":
                continue
            rel = path.decode("utf-8", "surrogateescape")
            if _excluded(rel, ex) or not _is_test_path(rel, cfg):
                continue
            entries.append(((f"{name}/{rel}" if name else rel).encode("utf-8", "surrogateescape"), parts[0], parts[2]))
    h = hashlib.sha256()
    for key, mode, blob in sorted(entries):
        h.update(key + b"\0" + mode + b"\0" + blob + b"\n")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------- the command

def _parse_rows(spec: str) -> "tuple[set[int] | None, str]":
    """`--rows 3,5-7` → {3, 5, 6, 7}; an empty item, a word, an inverted range is refused (plan panel: agy)."""
    out: "set[int]" = set()
    for item in spec.split(","):
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", item)
        if not m or (m.group(2) and int(m.group(2)) < int(m.group(1))):
            return None, f"--rows {spec!r}: each item is a row number or a range like 5-7"
        out.update(range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1))
    return out, ""


def _parse_flags(args: "list[str]") -> "tuple[list[str], dict[str, str], set[int] | None, bool, str]":
    """(the rest, the --base flags, the --rows set or None, --check, an error)."""
    rest: "list[str]" = []
    rows: "set[int] | None" = None
    check = False
    i = 0
    while i < len(args):
        if args[i] == "--check":
            check = True
            i += 1
            continue
        if args[i] == "--rows":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return [], {}, None, False, "--rows needs a value: a row number, a list or a range (3,5-7)"
            rows, err = _parse_rows(args[i + 1])
            if err:
                return [], {}, None, False, err
            i += 2
            continue
        rest.append(args[i])
        i += 1
    rest, flags, err = _parse_base_flags(rest)
    if not err and check and (flags or rows is not None):
        err = "--check runs nothing, so it takes no --base and no --rows"
    return rest, flags, rows, check, err


def _parse_base_flags(args: "list[str]") -> "tuple[list[str], dict[str, str], str]":
    rest, flags = [], {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--base":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return [], {}, "--base needs a value: [<scope>=]<commit>"
            scope, _, rev = args[i + 1].rpartition("=")
            flags[_norm_scope(scope)] = rev
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
            elif not commit:
                _refuse(f"the base of {where} had no commit and its working-state tree is gone (git gc?) — name a "
                        "base with --base")
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
    n = name.strip()                                    # never `\\` → `/` (impl round 3, GRAVE): a name, not a path
    while n.startswith("./"):
        n = n[2:]
    return n.rstrip("/")


def _scopes(project: Path, cfg: dict) -> "dict[str, Path]":
    from tasks.core import _tail_cert_scopes
    return {_norm_scope(n): p for n, p in _tail_cert_scopes(project, cfg).items()}


def _combine(verdicts: "list[str]") -> str:
    """A row's verdict from its tests' (impl round 2, opus): proved only when each is proved."""
    if verdicts and all(v == "proved" for v in verdicts):
        return "proved"
    if verdicts and all(v == "already true" for v in verdicts):
        return "already true"
    for s in ("refused", "unrunnable", "launch failed", "timeout", "broken", "regressed", "skipped",
              "partly skipped"):                        # a skip is never "partly proved" (task 281, agy and opus)
        if s in verdicts:
            return s
    return "partly proved"


USAGE = ("usage: tasks red [<N>] [--rows <3,5-7>] [--base [<scope>=]<commit>]…  |  tasks red [<N>] --check")


def _cmd_check(project: Path, d: Path, rows: "list[dict]", cfg: dict) -> None:
    """`tasks red [<N>] --check`: each row's state today, printed — runs nothing, writes nothing, takes no lock (a torn
    last line is skipped), and the CLI skips its session GC for it (plan panel: codex)."""
    try:
        st = state_of(project, d, rows, cfg)
    except OSError as exc:
        print(f"Refused: {RECEIPT_NAME} cannot be read ({exc.__class__.__name__}: {exc}). Nothing written.",
              file=sys.stderr)
        raise SystemExit(1)
    for ln in render_state(st, d.name.split("-", 1)[0], f"red receipt — task {d.name}: {len(rows)} row(s), "
                           f"{st['records']} record(s)"):
        print(ln)


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
    args, flags, only, check, err = _parse_flags(list(cmd_args))
    if err or len(args) > 1 or (args and not args[0].isdigit()):
        print(f"Error: {err or USAGE}", file=sys.stderr)
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
    try:
        cfg = load_config(project)
    except Exception:                                   # noqa: BLE001
        cfg = {}
    if check:
        _cmd_check(project, d, rows, cfg)
        return
    if not rows:
        _refuse(f"`{CONTRACT_HEADING}` has no row (a table line after its `---` separator)")
    labels = [r["row"] for r in rows]
    if any(not lab for lab in labels):
        _refuse("a row with no number in `## Test contract` — each row needs its own `#`, the state is kept by it")
    twice = sorted({lab for lab in labels if labels.count(lab) > 1})
    if twice:
        _refuse(f"row {twice[0]} appears twice in `## Test contract` — each row needs its own `#` (plan panel: sonnet)")
    contract = [row_key(r) for r in rows]
    if only is not None:
        numbered = {int(r["row"]) for r in rows if r["row"].isdigit()}
        missing = sorted(only - numbered)
        if missing:
            _refuse(f"no row {', '.join(map(str, missing))} in `## Test contract`")
        run_rows = [r for r in rows if r["row"].isdigit() and int(r["row"]) in only]
    else:
        run_rows = rows
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
    base_line = _base_line(plan)
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
        tests_sha = _digest_trees(scopes, now_tree, cfg, exclude)
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
        for k, row in enumerate(run_rows, 1):
            cmds, why = resolve(row["test"], cfg)
            rec = {"at": _now(), "run": run, "task": d.name, "row": row["row"], "kind": row["kind"],
                   "row_sha": hashlib.sha256(row["line"].encode("utf-8")).hexdigest()[:16], "row_key": row_key(row),
                   "contract": contract, "test": row["test"],
                   "base": plan, "now_tree": now_tree, "tests_sha": tests_sha, "contained": contained,
                   "private_tmp": private_tmp}
            empty = {"exit": None, "seconds": 0.0, "last": "", "error": "", "reader": "", "skips": 0}
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
                        t = {"command": c, "base": b, "now": n,
                             "verdict": verdict(b["status"], n["status"], n.get("skips", 0))}
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
        _write_section(task_file, lambda records, old: _section(
            compute_state(rows, records, cfg, now_tree, tests_sha, old), contained_line))
        proved = sum(1 for r in results if r["verdict"] == "proved")
        print(f"  {len(results)} row(s) run: {proved} proved (red on the base, green now); the state across runs is in "
              f"task.md (`{RECEIPT_HEADING}`), every record in {RECEIPT_NAME}; `tasks red {d.name.split('-', 1)[0]} "
              "--check` prints it.", flush=True)
    except KeyboardInterrupt:
        print("  interrupted: the rows that ended are in " + RECEIPT_NAME + "; task.md was not written.",
              file=sys.stderr, flush=True)
        raise SystemExit(130)
    except (RuntimeError, ValueError) as e:
        print(f"Refused: {e}. The rows that ended are in {RECEIPT_NAME}; task.md was not written.", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for sig, h in old_handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass
