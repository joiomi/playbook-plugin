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
  task.md's `## Red receipt` (pinned; the newest run replaces the last run's own lines, and every line the CLI did
  not write stays — task 303, `_kept`) once all rows are done.

A side is red when the command exited non-zero within its time — but 126/127 (no runner, not executable) is "launch
failed" and a timeout is "timeout", never red. The verdict reads both sides (plan panel P5): "proved" (red on the base,
green now), "already true" (green, green), "broken" (red now after red), "regressed" (green on the base, red now).

Part 2 (task 281) reads the records: a side that passed no test is "skipped" (unittest's and pytest's summaries; any
other runner's skip reads green, and the side says no reader matched), a green side that skipped a test "partly
skipped" — neither proves. `--rows` runs only some rows; `## Red receipt` is the state across runs (`compute_state`:
each row's newest record by its key — kind, behavior, test — else by its number); a row not in the first run's
contract is "added later". `--check` and the close (`close_check`) print each row's state, stale when its text, its
command or the test files changed since its record; the close stops on a row that is certainly wrong (task 286).

Part 3 (task 282) makes the claims hold: a late row proves only when, in each scope whose code the task changed since
the reference (base.json, else the first run's base), its base is other code and a commit strictly after the
reference's — and in a scope the task did not change, the start's code (`_own_base`); every summary in a side's
output is read (`_summaries`); the first run's code is compared with the reference (a first run after the code is
said); the section is written from the contract, the receipt and the config as they are under the task lock, every
git answer worked out before it (`_Trees`, frozen under it); a bare `tasks red` / `tasks numbers` takes this
session's task (`session_task_dir`) or refuses.

Part 4 (task 283) judges every proof's base (`_base_check`): the start, or newer code that keeps a dirty
activation's work — else `proved on older code`, `on unrelated code` or `on an unchecked base`.

Part 8 (task 287) — a row leaves the contract only by `tasks red [<N>] --drop <row> --reason "<why>"` (the owner,
2026-10-22, M1444 and M1448): a recorded row (its key) not in today's contract and not dropped since its last run
blocks the close (`_gone`); an edit after a run is a new row, its old version dropped.
"""
from __future__ import annotations

import bisect
import contextlib
import fnmatch
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE_NAME = "base.json"
RECEIPT_NAME = "red-receipt.jsonl"
DROPPED = "dropped"                                     # a removal's verdict (`tasks red --drop`, task 287)
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
    try:                                                # deleting it later does not turn the close's check off
        from tasks.core import read_task_text           # (task 286, sonnet)
        _text = read_task_text(Path(task_dir) / "task.md")
        doc["contract"] = parse_contract(_text)[0] is not None or _has_contract_gate(_text)
    except Exception:                                   # noqa: BLE001 — not known: the task.md says at the close
        pass
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

def contract_problems(rows: "list[dict]") -> "list[str]":
    """Why `tasks red` refuses a contract's rows — and the close with it (task 286, agy, opus: one validation for both):
    no row, a row with no number, a number twice, a number that is not one (ASCII digits — `--rows` names them)."""
    if not rows:
        return [f"`{CONTRACT_HEADING}` has no row (a table line after its `---` separator)"]
    labels = [r["row"] for r in rows]
    out = []
    if any(not lab for lab in labels):
        out.append("a row with no number in `## Test contract` — each row needs its own `#`, the state is kept by it")
    twice = sorted({lab for lab in labels if lab and labels.count(lab) > 1})
    if twice:
        out.append(f"row {twice[0]} appears twice in `## Test contract` — each row needs its own `#` (plan panel: "
                   "sonnet)")
    words = [lab for lab in labels if lab and not _NUMBER.fullmatch(lab)]
    if words:                                           # `--rows` names rows by number (task 282, agy)
        out.append(f"row {words[0]} is not a number — each row's `#` in `## Test contract` is one")
    return out


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


def _tree_state(repo: Path, treeish: str, timeout: float = 120) -> "bool | None":
    """Whether a tree (or a commit's) is in `repo`: False only when git says it is not there (`rev-parse --verify
    --quiet` returns 1 — pruned by `git gc`, a commit gone after a rebase), None when git cannot say (task 285, opus,
    agy). One predicate for the receipt and for `_plan_base`. A value that starts with `-` is never put to git as an
    option (task 285, sonnet): not known."""
    if not isinstance(treeish, str) or not treeish or treeish.startswith("-"):
        return None
    try:
        r = _git(repo, ["rev-parse", "--verify", "--quiet", f"{treeish}^{{tree}}"], timeout=timeout)
    except (OSError, subprocess.SubprocessError, ValueError):   # ValueError: a NUL in it (task 286, agy)
        return None
    return True if r.returncode == 0 else (False if r.returncode == 1 else None)


def _tree_exists(repo: Path, tree: str) -> bool:
    return _tree_state(repo, tree) is True              # "not known" is not there: `_plan_base` fails closed


def _commit_of(repo: Path, rev: str) -> "str | None":
    if not isinstance(rev, str) or not rev or rev.startswith("-") or "\0" in rev:
        return None                                     # never an option to git (task 286, opus, sonnet)
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
    env.update({"HOME": str(home), "PLAYBOOK_RED": "1", "GIT_CEILING_DIRECTORIES": str(ceiling),
                "NO_COLOR": "1", "PY_COLORS": "0", "PYTHON_COLORS": "0"})     # no colour (task 284)
    for k in ("FORCE_COLOR", "CLICOLOR_FORCE"):
        env.pop(k, None)
    if caged:
        env["PLAYBOOK_SANDBOXED"] = "1"
    return env


def _last_line(data: bytes) -> str:
    text = _plain(data)                                 # no escapes in the table (task 284, opus, sonnet)
    for ln in reversed(text.split("\n")):
        if ln.strip():
            return ln.strip()[:LAST_MAX]
    return ""


_ERROR_LINE = re.compile(r"^(?:E\s+)?((?:[A-Za-z_][\w.]*\.)?[A-Z]\w*(?:Error|Exception|Failure|Exit|Interrupt)\b.*)")


def _first_error(data: bytes) -> str:
    """The first line of the output that names an exception (`AssertionError: 1 != 2`, `ModuleNotFoundError: …`) —
    what a runner's summary line (`FAILED (errors=1)`) does not say (plan panel P5, opus)."""
    for ln in _plain(data).split("\n"):                 # a coloured `E   AssertionError` too (task 284, opus)
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
_NUMBER = re.compile(r"[0-9]+")                         # ASCII digits only: `²` passes str.isdigit (task 282, sonnet)
# A terminal escape, read as plain (task 284): a CSI with any parameter bytes (`\x1b[38:5:3m` — impl round 1, codex),
# an OSC title or link (ended by BEL or ST, never past its line), or a two-byte one such as `tput sgr0`'s `\x1b(B`.
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b\n]*(?:\x07|\x1b\\)?|[ -/]*[0-~])")
_UT_RAN = re.compile(r"(?m)^Ran ([0-9]+) tests? in [0-9.]+s\s*$")     # ASCII digits: a test controls them (282)
_UT_END = re.compile(r"(?m)^(OK|FAILED|PASSED)(?: \(([^)\n]*)\))?\s*$")     # PASSED: Twisted trial's (task 284)
_PY_WORD = r"[0-9]+ (?:passed|failed|skipped|xfailed|xpassed|errors?|warnings?|deselected|rerun)"
_PY_SUMMARY = re.compile(r"(?m)^=*\s*(" + _PY_WORD + r"(?:, " + _PY_WORD + r")*) in [0-9.]+s(?: \([^)\n]*\))?\s*=*\s*$")


_NO_TESTS = re.compile(r"(?m)^(?:=+ )?no tests ran in [0-9.]+s|^NO TESTS RAN\s*$")
_PY_EMPTY = re.compile(r"(?m)^=*\s*no tests ran in [0-9.]+s(?: \([^)\n]*\))?\s*=*\s*$")
_UT_NONE = re.compile(r"(?m)^NO TESTS RAN\s*$")       # unittest's word for a run that passed no test (283, agy)


def _plain(out: bytes) -> str:
    """A side's output as text with colour escapes taken out — a forced colour (pytest's `--color=yes`, `PY_COLORS`)
    would hide a summary from the readers (task 284, opus, sonnet, codex)."""
    return _ANSI.sub("", out.decode("utf-8", "replace"))


def _summaries(out: bytes) -> "list[tuple[str, int, int, bool]]":
    """EVERY unittest and pytest summary in `out`, in order: (reader, tests that passed, tests skipped, a failure
    or error). Each unittest summary's end is looked for only up to the next summary (task 282, sonnet: a 3.12
    `NO TESTS RAN` has no OK line and took the next one's). A chained command shows several (task 282, codex and
    sonnet: the last one hid an all-skipped or a FAILED run)."""
    text = _plain(out)
    found: "list[tuple[int, str, int, int, bool]]" = []
    uts = list(_UT_RAN.finditer(text))
    pys = list(_PY_SUMMARY.finditer(text))
    empties = list(_PY_EMPTY.finditer(text))         # pytest's empty run is a summary too (task 282, codex)
    nones = list(_UT_NONE.finditer(text))
    starts = sorted(m.start() for m in uts + pys + empties + nones)
    for m in uts:
        nxt = bisect.bisect_right(starts, m.start())   # the next summary of EITHER runner (agy), in log time (283)
        stop = starts[nxt] if nxt < len(starts) else len(text)
        end = _UT_END.search(text, m.end(), stop)
        counts: "dict[str, int]" = {}
        for part in (end.group(2) or "").split(",") if end else ():
            k, _, v = part.strip().partition("=")
            if _NUMBER.fullmatch(v.strip()):
                counts[k.strip()] = int(v)
        skipped = (counts.get("skipped", 0) + counts.get("expected failures", 0)  # an expected failure FAILED;
                   + counts.get("skips", 0) + counts.get("expectedFailures", 0))  # trial's words (task 284)
        failed = counts.get("failures", 0) + counts.get("errors", 0)              # (impl round 1, codex)
        ran = int(m.group(1))
        if end is None and ran > 0:                     # killed after `Ran N`: no pass, a failure (283, agy)
            found.append((m.start(), "unittest", 0, skipped, True))
            continue
        found.append((m.start(), "unittest", max(ran - skipped - failed, 0), skipped,
                      bool(failed) or (end is not None and end.group(1) == "FAILED")))
    for m in nones:
        found.append((m.start(), "unittest", 0, 0, False))
    for m in empties:
        found.append((m.start(), "pytest", 0, 0, False))
    for m in pys:
        counts = {}
        for part in m.group(1).split(","):
            n, _, word = part.strip().partition(" ")
            counts[word] = counts.get(word, 0) + int(n)
        found.append((m.start(), "pytest", counts.get("passed", 0) + counts.get("xpassed", 0),   # xpassed a pass
                      counts.get("skipped", 0) + counts.get("xfailed", 0),
                      bool(counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0))))
    return [f[1:] for f in sorted(found)]


def _no_test_passed(out: bytes) -> bool:
    """A summary in the output shows that no test passed (all skipped, expected failures, or none ran)."""
    return _side_status(0, out)[0] == "skipped"


def _side_status(code: int, out: bytes) -> "tuple[str, str, int]":
    """(status, reader, skips) of one side. A non-zero exit is red — but exit 5 with pytest's `no tests ran` or
    unittest's `NO TESTS RAN` ran no test: `skipped` (task 282, opus). At exit 0 a summary with a failure makes the
    side red (`a ; b` — task 282, opus and sonnet), and one that passed no test makes it `skipped`, never green; the
    skips of every summary add up."""
    sums = _summaries(out)
    reader = "+".join(sorted({x[0] for x in sums}))
    skips = sum(x[2] for x in sums)
    if code in LAUNCH_FAILED:
        return "launch failed", reader, skips
    if any(x[3] for x in sums):                         # a failure, whatever the return (task 282, agy and opus)
        return "red", reader, skips
    if code == 5 and _NO_TESTS.search(_plain(out)):
        return "skipped", reader, skips
    if code != 0:
        return "red", reader, skips
    if any(x[1] <= 0 for x in sums):
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

RECEIPT_MAX = 64 * 1024 * 1024


def _open_receipt(p: Path, write: bool) -> int:
    """The receipt's descriptor — opened without waiting and without following a link, and only a regular file: a
    FIFO in its place made the close wait forever (impl round 1, codex). OSError otherwise."""
    flags = (os.O_RDWR | os.O_APPEND | os.O_CREAT) if write else os.O_RDONLY
    fd = os.open(p, flags | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o644)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{p.name} is not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _append(task_file: Path, *records: dict) -> None:
    """Each record a line of the receipt, all in one write under the task lock (a drop's records: task 287, P6)."""
    from tasks.filelock import task_lock
    p = Path(task_file).parent / RECEIPT_NAME
    data = b"".join(json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n" for r in records)
    with task_lock(task_file):
        try:
            fd = _open_receipt(p, write=True)
        except OSError as exc:
            raise RuntimeError(f"{RECEIPT_NAME} cannot be written ({exc})") from None
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                data = b"\n" + data                     # a run killed mid-line: this record starts its own line
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)


def _side(r) -> str:
    """One side as the table shows it — each field read defensively (impl round 1, codex: a record without `status`
    broke the section)."""
    if not isinstance(r, dict):
        return "—"
    st, ex, sec = r.get("status"), r.get("exit"), r.get("seconds")
    if st in ("red", "green", "launch failed", "skipped") and ex is not None:
        return f"{st} (exit {ex}, {sec} s)"
    if st == "timeout":
        return f"timeout ({sec} s)"
    return str(st or "—")


# A line break or control character in a value would add a line to task.md — a `## Status` of its own (task 287, impl
# round 1: codex, opus, sonnet, grok). `_physical_lines` splits on `\n` only; every other one is refused or flattened too.
_LINE_BREAKERS = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _one_line(text) -> str:
    return _LINE_BREAKERS.sub(" ", str(text))


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


# The section's own lines (task 303, audit 3's G3): a run rewrote the whole section, and every line the agent wrote
# there — a note, a `###` part, an open gate — went with it. Now its last line is its end line, which lists a digest of
# each line it wrote above (`_end_line`), and a rewrite removes only those (`_kept`): no line is told for its own by
# its shape (impl round 1, opus and codex: an agent's `Note: … .` was), and every other line stays.
END_HEAD = "<!-- the end of tasks red's lines: a line below this one stays when it runs again"
_END_LIST = re.compile(r" — its lines: ((?:[0-9a-f]{8} )*[0-9a-f]{8}) -->$")
_KEPT_WHOLE = ("Kept by `tasks red`: this section had no end line it could read, so each line it held stays below, "
               "its own old lines among them.")
_FIRST_LINE = "Written by `tasks red` — the state across its runs: "
_TABLE_HEAD = "| # | Kind | Test | On the base | Now | State | On the base, its first error (or last line) | Run |"
_TABLE_RULE = "|---|------|------|-------------|-----|-------|---------------------------------------------|-----|"


def _line_digest(line: str) -> str:
    """A line's digest, as the end line lists it — trailing white space left out (an editor may add it: impl round 1,
    opus and sonnet)."""
    return hashlib.sha256(line.rstrip().encode("utf-8")).hexdigest()[:8]


def _end_line(lines: "list[str]") -> str:
    """The end line: the digest of each line above it that is not blank, the heading left out."""
    return f"{END_HEAD} — its lines: {' '.join(_line_digest(ln) for ln in lines if ln.strip())} -->"


def _end_digests(line: str) -> "Counter[str] | None":
    """The digests an end line lists, each as many times as it is listed — or None: not an end line, or one whose list
    cannot be read."""
    ln = line.rstrip()
    m = _END_LIST.search(ln)
    if not ln.startswith(END_HEAD) or m is None or ln[:m.start()] != END_HEAD:
        return None
    return Counter(m.group(1).split())


def _kept(lines: "list[str]", a: int, b: int) -> "list[str]":
    """What a rewrite keeps of the section at [a, b) — its heading at `a` (task 303). Its end line is the first live
    line with a list `_end_digests` reads. Below it every line stays as it is. Above it, a live line whose digest the
    list names is the CLI's and goes (each listed digest once); every other line stays, in order, under one blank line,
    a run of blank lines between them kept as one — a line in a code fence (the writers' fail-closed view: after an
    unclosed opener, every line), a line the agent wrote, a line of the CLI's the agent edited. With no such end line
    (a section written before task 303, or one whose end line was taken out, edited or fenced) nothing is the CLI's:
    every line stays, under `_KEPT_WHOLE`. The list ends with a blank line, as the section always did."""
    from tasks.core import _iter_nonfenced
    live = {i for i, _s in _iter_nonfenced(lines)}
    end = next((i for i in range(a + 1, b) if i in live and lines[i].rstrip().startswith(END_HEAD)), None)
    listed = _end_digests(lines[end]) if end is not None else None
    if listed is None:
        end = b
    above: "list[str]" = []
    gap = False                                         # a live blank line since the last line kept
    for i in range(a + 1, end):
        if i in live and not lines[i].strip():
            gap = True
            continue
        if i in live and listed is not None and listed[_line_digest(lines[i])] > 0:
            listed[_line_digest(lines[i])] -= 1
            continue
        if above and gap and above[-1].strip():
            above.append("")
        above.append(lines[i])
        gap = False
    if above and listed is None:
        above = [_KEPT_WHOLE, ""] + above
    out = [""] + above if above else []
    after = lines[end + 1:b]
    if out and after and after[0].strip():
        out.append("")
    out += after
    if not out or out[-1].strip():
        out.append("")
    return out


def _section(st: dict, contained: str) -> str:
    """The state across runs, for the judges (task 281; agy): each row's newest record — the head names each run that
    is some row's newest, with its base; a count of each state with its rows; the rows added later, those no skip
    reader matched; a table of the rows NOT proved, each with its run; its end line last (task 303). Every record is
    in the jsonl."""
    rows = st["rows"]
    newest = st.get("newest") or {}
    head = [RECEIPT_HEADING, "<!-- pin -->",
            f"{_FIRST_LINE}each row's newest record in `{RECEIPT_NAME}` beside "
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
    if st.get("unchecked"):                             # what was not checked is said here too (task 282, agy)
        head.append(f"Note: not checked — {_cell(st['unchecked'])}.")
    late = [e for e in rows if e["late"]]
    if late:
        said = ("Added later (per the first run, which came after the code): " if st.get("anchor_after")   # (283)
                else "Added later (not in the first run's contract): ")
        head.append(said + "; ".join(
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
    if st.get("missing"):                               # (task 287)
        head.append("Recorded, no longer in the contract, not dropped (each blocks the close until `tasks red --drop`): "
                    + "; ".join(f"row {_cell(m['row'])} ({_cell(m['kind'])}, {_cell(m['test'])})"
                                for m in st["missing"]) + ".")
    if st.get("dropped"):
        head.append("Dropped: " + "; ".join(f"row {_cell(x['row'])} ({_cell(str(x.get('kind', '')))}, "
                                           f"{_cell(str(x.get('test', '')))}) — {_cell(str(x.get('reason', '')))}"
                                           for x in st["dropped"]) + ".")
    rest =[e for e in rows if e["state"] != "proved"]
    if rest:
        head += ["", _TABLE_HEAD, _TABLE_RULE]
        head += [_row_table_line(e) for e in rest]
    out = [_one_line(h) for h in head]                    # each value on its own line, never one more (task 287)
    return "\n".join(out + [_end_line(out[1:])])


def _rows_word(labels: "list[str]") -> str:
    return ("rows " if len(labels) > 1 else "row ") + ", ".join(labels)


def _write_section(task_file: Path, project: Path, contained: str) -> "tuple[str, dict | None]":
    """Rewrite `## Red receipt`'s own lines, keeping every other (`_kept`, task 303) — or put the section right after
    `## Test contract` — under the task's lock, from the contract, the
    receipt and the config as they are then (impl round 1 and task 282, codex: a run that ends last wrote the rows
    and the runner it began with), with today's trees built and every git answer worked out BEFORE the lock (task
    282, opus and sonnet: under it only the cache answers). Returns ("", the state) or (why nothing was written,
    None) — a receipt that cannot be read is that, never a traceback (task 282, opus)."""
    from tasks.atomic import atomic_write
    from tasks.core import _physical_lines, _writer_section_span, load_config, read_task_text
    from tasks.filelock import task_lock
    task_dir = Path(task_file).parent

    def config():
        try:
            return load_config(Path(project))
        except Exception:                               # noqa: BLE001
            return {}
    st = None
    try:
        for attempt in (1, 2):
            cfg = config()
            with _no_git_overrides():
                view, tests_now, why = _prepare(Path(project), cfg, None, True)
                rows0, _w = parse_contract(read_task_text(task_file))
                drops0: "list[tuple[int, dict]]" = []
                records0, old0 = _read_records(task_dir / RECEIPT_NAME, None, drops0)
                if rows0:                               # every git answer, now — before the lock (the hint's
                    ref0 = _reference(task_dir, records0)   # too: task 285, sonnet, agy)
                    compute_state(rows0, records0, cfg, view, tests_now, old0, ref0, *_base_hint(task_dir, ref0, view),
                                  dropped=drops0)
            view.frozen = True
            with task_lock(task_file):
                now_cfg = config()                      # the config may have changed while the trees were built
                if now_cfg != cfg:                      # (task 282, codex): prepare once more, outside the lock —
                    if attempt == 1:                    # a second change is a refusal, never a mix (283, agy, codex)
                        continue
                    return "the config kept changing while the section was prepared", None
                text = read_task_text(task_file)
                rows, why_c = parse_contract(text)
                if rows is None:
                    return why_c, None
                drops: "list[tuple[int, dict]]" = []
                records, old = _read_records(task_dir / RECEIPT_NAME, None, drops)
                ref = _reference(task_dir, records)
                st = compute_state(rows, records, now_cfg, view, tests_now, old, ref, *_base_hint(task_dir, ref, view),
                                   dropped=drops)
                st["unchecked"] = _unchecked(view, why)
                lines = _physical_lines(text)
                new = _physical_lines(_section(st, contained))
                sp = _writer_section_span(lines, RECEIPT_HEADING)
                if sp is not None:                      # only its own lines: the agent's stay (task 303)
                    lines[sp[0]:sp[1]] = new + _kept(lines, *sp)
                else:
                    new += [""]
                    cp = _writer_section_span(lines, CONTRACT_HEADING)
                    at = cp[1] if cp is not None else len(lines)
                    if at and lines[at - 1].strip():
                        new = [""] + new
                    lines[at:at] = new
                atomic_write(task_file, "\n".join(lines))
                return "", st
    except OSError as exc:
        return f"{RECEIPT_NAME} or task.md cannot be read ({exc})", None
    return "the config kept changing while the section was prepared", None     # not reached: attempt 2 returns


# ---------------------------------------------------------------------------- the state across runs (task 281)

def row_key(row: dict) -> str:
    """A row's identity across runs: its kind, behavior and test — not its number, so renumbering keeps it."""
    return hashlib.sha256("\0".join((row["kind"], row["behavior"], row["test"])).encode("utf-8")).hexdigest()[:16]


def _read_records(path: Path, skipped: "list[int] | None" = None,
                  dropped: "list[tuple[int, dict]] | None" = None) -> "tuple[list[dict], int]":
    """(the receipt's run records in file order, how many were written before the row key). A line that is not UTF-8,
    not JSON, or not a record of the shape the state reads is skipped (plan panel: sonnet); a record with no `row_key` /
    `contract` (task 280's) cannot say which row it was, so it is counted and not read — never "its text changed" or
    "added later" (found by the dogfood on task 280). A receipt that cannot be read raises OSError. `skipped` collects
    the line number of each line skipped (the close's check blocks on one — task 286, opus). A `dropped` record
    (`tasks red --drop`, task 287) is never a run: it goes to `dropped` with its place — how many run records came
    before it — and nowhere else (plan panel P2: the newest run, the first, the count)."""
    try:
        fd = _open_receipt(Path(path), write=False)
    except FileNotFoundError:
        return [], 0
    try:
        chunks, n = [], 0
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            n += len(b)
            if n > RECEIPT_MAX:
                raise OSError(f"{RECEIPT_NAME} is over {RECEIPT_MAX} bytes")
            chunks.append(b)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    out, old = [], 0
    for n, raw in enumerate(data.split(b"\n"), 1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            if skipped is not None:
                skipped.append(n)
            continue
        if not (isinstance(rec, dict) and all(isinstance(rec.get(k), str) for k in ("row", "run", "verdict", "row_sha"))):
            if skipped is not None:
                skipped.append(n)
            continue
        if isinstance(rec.get("row_key"), str) and isinstance(rec.get("contract"), list):
            if rec["verdict"] == DROPPED:
                if dropped is not None:
                    dropped.append((len(out), rec))
                continue
            out.append(rec)
        else:
            old += 1
    return out, old


def _gone(rows: "list[dict]", records: "list[dict]",
          dropped: "list[tuple[int, dict]]") -> "tuple[list[dict], dict[str, dict]]":
    """(each recorded row not in today's contract and not dropped since its last run — its key, its number at that run,
    kind, test and `row_sha`, by number; each drop in effect — after its key's last run — by key). A row is its key
    (kind, behavior, test): a renumber keeps it, an edit makes a new one, and the old one leaves only by `--drop` (the
    owner, 2026-10-22, M1444 and M1448). A row never run has no record, so nothing of it is checked (plan panel P9)."""
    last: "dict[str, int]" = {}
    newest: "dict[str, dict]" = {}
    for i, rec in enumerate(records):
        last[rec["row_key"]] = i
        newest[rec["row_key"]] = rec
    in_effect: "dict[str, dict]" = {}
    for pos, rec in dropped:
        if last.get(rec["row_key"], -1) < pos:          # after its last run; a later run supersedes it
            in_effect[rec["row_key"]] = rec
    keys_now = {row_key(r) for r in rows}
    out = [{"key": k, "row": r["row"], "kind": str(r.get("kind", "")), "test": str(r.get("test", "")),
            "row_sha": r["row_sha"], "run": r["run"]}
           for k, r in newest.items() if k not in keys_now and k not in in_effect]
    out.sort(key=lambda m: (int(m["row"]) if _NUMBER.fullmatch(m["row"]) else 1 << 62, m["row"]))
    return out, in_effect


def _treeish(e) -> "str | None":
    """What a base entry names: its recorded tree, else its commit."""
    if not isinstance(e, dict):
        return None
    if e.get("used") == "tree" and isinstance(e.get("tree"), str):
        return e["tree"]
    return e["commit"] if isinstance(e.get("commit"), str) else None


def _ref_treeish(e) -> "str | None":
    """A reference entry's code: its tree when it has one — base.json's entries carry `commit`, `tree`, `clean` and
    no `used` (task 282, sonnet: a dirty activation's tree is not its HEAD) — else its commit."""
    if not isinstance(e, dict):
        return None
    if e.get("used") != "commit" and isinstance(e.get("tree"), str):
        return e["tree"]
    return e["commit"] if isinstance(e.get("commit"), str) else None


BASE_STATES = {"ok": "proved", "start": "proved on the task's start only", "older": "proved on older code",
               "unrelated": "proved on unrelated code", "unchecked": "proved on an unchecked base"}


def _base_check(rec: dict, ref, view, late: bool) -> "tuple[str, str, str]":
    """What a proved record's base is against the REFERENCE (base.json, else the first run's base), per scope — one
    rule for every row (task 283): ("ok" | "start" | "older" | "unrelated" | "unchecked", why).

    - The reference itself (the same tree or commit): the start, with no git asked (task 283, opus) — for a late row
      "start" when the task changed code in that scope (its proof is on the start only).
    - Else, by code (`classify_delta_paths`' behavioral files, not test files): the start's code is as above; other
      code must be a commit strictly AFTER the reference commit (task 282) — strictly before it is "older", neither
      is "unrelated" (task 283, opus) — in a scope the task changed (in one it did not, other code is "unrelated"),
      and after a dirty activation it must keep the activation's uncommitted code: on each path that work touched
      (mode and blob; added or deleted) the base must differ from the activation commit (task 283, opus, sonnet,
      codex). A stated limit: a base that restored a dirty file to its committed form reads "older".
    - What git cannot answer, or a scope the reference does not have, is "unchecked" (task 283, codex).

    The third value says what helps an "unchecked" — `--check` again, a re-run of the row, or nothing (`_HELPS`;
    task 284: a cause code, not the note's words; task 285: which remedy).
    The dirty-work test runs for any reference that names its tree and is not marked clean — a run on base.json
    carries its `clean`, and an older record without it is judged by its listings (a clean one has no dirty path)."""
    b = rec.get("base")
    if not isinstance(b, dict) or not b:
        return "unchecked", "the record names no base", ""
    refs = ref if isinstance(ref, dict) else {}
    now = rec.get("now_tree") if isinstance(rec.get("now_tree"), dict) else {}
    on_start = []
    for scope, e in b.items():
        where = scope or "the project"
        r = refs.get(scope)
        if not isinstance(r, dict) or not isinstance(e, dict):
            return "unchecked", (f"{where} has no reference (a scope added after the activation?) — a re-run cannot "
                                 "judge it"), ""
        if r.get("clean") is False and not isinstance(r.get("tree"), str):      # (task 283, codex)
            return "unchecked", (f"{where}'s activation tree was not captured — its uncommitted code is not known; a "
                                 "re-run cannot judge it"), ""
        rt, et = _ref_treeish(r), _treeish(e)
        if rt is None:                                  # a cause of its own, not "names no tree" (task 284, opus)
            return "unchecked", f"{where}'s reference names no commit or tree — a re-run cannot judge it", ""
        same = (rt is not None and rt == et) or (r.get("clean") is True and isinstance(e.get("commit"), str)
                                                  and e.get("commit") == r.get("commit"))
        if same and not late:
            continue                                    # the start: no git (task 283, opus)
        if view is None:
            return "unchecked", "the trees were not read", "check"
        start = view.digest(scope, rt, code=True)       # each question answered in turn, its own cause (284)
        if start is None:
            return _unknown(view, where, scope, rt, True)
        mine = start if same else view.digest(scope, et, code=True)
        if mine is None:
            return _unknown(view, where, scope, et)
        if mine == start:
            if late:                                    # the now-tree only where it is used (task 285, opus)
                nowd = view.digest(scope, now.get(scope), code=True)
                if nowd is None:
                    return _unknown(view, where, scope, now.get(scope))
                if nowd != start:
                    on_start.append(scope)
            continue
        rc, ec = r.get("commit"), e.get("commit")
        if isinstance(rc, str) and ec == rc:            # the activation's commit without its uncommitted work
            return "older", f"{where}'s base is the activation's commit without its uncommitted code", ""
        if rc is None:
            after = True                                # an unborn reference: every commit is after it
        else:
            after = view.newer(scope, rc, ec)
            if after is None:
                return _not_related(view, where, scope, rc, ec)
        if not after:
            before = view.older(scope, ec, rc)
            if before is None:
                return _not_related(view, where, scope, rc, ec)
            return (("older", f"{where}'s base is older than the start", "") if before else
                    ("unrelated", f"{where}'s base is neither before nor after the start", ""))
        nowd = view.digest(scope, now.get(scope), code=True)
        if nowd is None:
            return _unknown(view, where, scope, now.get(scope))
        if nowd == start:
            return "unrelated", f"the task changed no code in {where}, and its base is other code", ""
        names_tree = isinstance(r.get("tree"), str) and rt == r.get("tree")   # not a tree gone (284, opus)
        if names_tree and r.get("clean") is not True:
            c0 = {} if rc is None else view.entries(scope, rc)   # unborn: every code path is the work (283, opus, codex)
            if c0 is None:
                return _unknown(view, where, scope, rc, True)
            t0 = view.entries(scope, r["tree"])
            if t0 is None:
                return _unknown(view, where, scope, r["tree"], True)
            mine_e = view.entries(scope, et)
            if mine_e is None:
                return _unknown(view, where, scope, et)
            none = (None, None)
            for path in sorted(set(c0) | set(t0)):      # the first one named, whatever the hash seed (284, agy)
                (cm, cb), (tm, tb), (bm, bb) = c0.get(path, none), t0.get(path, none), mine_e.get(path, none)
                if (cb != tb and bb == cb) or (cm != tm and bm == cm):    # content and mode apart (283, codex)
                    return "older", f"{where}'s base lacks the activation's uncommitted code ({path})", ""
    if on_start:
        return "start", "its base is the start in " + ", ".join(n or "the project" for n in on_start), ""
    return "ok", "", ""


# What helps an answer that was not known (task 285, sonnet, agy): `tasks red --check` asking again ("check"), a re-run
# of the row ("rerun": it records its trees anew, and only today's scopes), or nothing ("").
_HELPS = {"frozen": "check", "budget": "check", "git": "check", "tree": "rerun", "scope": "rerun"}
_GONE_START = "{}'s start is gone from the repository (git gc? a rebase?) — a re-run cannot judge it"
_REFRESHED = ("stale", "no record", "proved on the task's start only", "proved on older code", "proved on unrelated code",
              "proved on an unchecked base")         # the states a refresh line names


def _why_none(view, where: str, cause: "str | None" = None) -> str:
    """Why a digest was not known — a record the frozen view never saw is not a git failure (task 283, opus); a scope
    gone, or a tree the record does not name, is said as that (task 284). `cause` is the one read before any later
    question could overwrite `last_none` (task 285, opus)."""
    return {"frozen": "written while the section was being written — `tasks red --check` compares it",
            "budget": "the time budget ran out",
            "scope": f"{where} is no longer a scope (a code root removed?)",
            "tree": f"the record names no tree for {where}"}.get(getattr(view, "last_none", "") if cause is None
                                                                 else cause, f"git could not list {where}'s trees")


def _unknown(view, where: str, scope=None, treeish=None, of_start: bool = False) -> "tuple[str, str, str]":
    """An answer that was not known, and what helps. A listing git could not make is asked whether its tree is still
    there (task 285, opus, sonnet): the start gone can never be judged; a tree the record names, a re-run records
    anew."""
    cause = getattr(view, "last_none", "")
    if cause == "git" and scope is not None and view.has(scope, treeish) is False:
        if of_start:
            return "unchecked", _GONE_START.format(where), ""
        return "unchecked", f"a tree the record names in {where} is gone from the repository (git gc?)", "rerun"
    return "unchecked", _why_none(view, where, cause), _HELPS.get(cause, "check")


def _not_related(view, where: str, scope: str, rc, ec) -> "tuple[str, str, str]":
    """How a base and the start are related was not known: a record that names no commit (a re-run records one), a
    frozen view or a spent budget (its own words — impl round 1, agy, sonnet), or git — either commit may be gone
    (task 285, opus)."""
    if not isinstance(ec, str) or not ec:
        return "unchecked", f"the record names no commit for {where}", "rerun"
    cause = getattr(view, "last_none", "")
    if cause in ("frozen", "budget"):
        return "unchecked", _why_none(view, where, cause), "check"
    if view.has(scope, rc) is False:
        return "unchecked", _GONE_START.format(where), ""
    if view.has(scope, ec) is False:
        return "unchecked", f"the commit the record names in {where} is gone from the repository (git gc?)", "rerun"
    return "unchecked", f"git could not tell how {where}'s base and start are related", "check"


def _own_base(rec: dict, ref, view) -> bool:
    """A late row's run ran on the code before its fix (task 282's question, answered by `_base_check`)."""
    return _base_check(rec, ref, view, True)[0] == "ok"


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


def compute_state(rows: "list[dict]", records: "list[dict]", cfg: dict, view: "_Trees | None",
                  tests_now: "str | None", old: int = 0, ref=None, hint_base: "str | None" = "",
                  hint_why: str = "", dropped: "list[tuple[int, dict]] | None" = None) -> dict:
    """Each contract row's state from the receipt's run records (file order: the newest per row wins). A row is matched
    by its key (kind, behavior, test), else by its number — then its text changed. A key dropped since its last run
    has no record (task 287). `view` compares trees (its `today`: each scope's working state) and `tests_now` is
    today's tests digest — None: not read, so no test or tree change is claimed either way."""
    by_key: "dict[str, dict]" = {}
    by_label: "dict[str, dict]" = {}
    for rec in records:
        by_key[rec["row_key"]] = rec
        by_label[rec["row"]] = rec
    missing, gone_now = _gone(rows, records, dropped or [])
    first: "set | None" = ({k for k in records[0]["contract"] if isinstance(k, str)} or None) if records else None
    if ref is None and records:                         # an empty contract list is unknown (agy)
        ref = records[0].get("base")
    out = []
    unchecked_rows: "list[str]" = []
    keys_now = {row_key(r) for r in rows}
    for row in rows:
        key = row_key(row)
        cmds, _why = resolve(row["test"], cfg)
        e: dict = {"row": row["row"], "kind": row["kind"], "test": row["test"], "key": key,
                   "late": first is not None and key not in first, "rec": None, "verdict": ""}
        rec = None if key in gone_now else by_key.get(key)     # dropped, then put back: it must run (task 287)
        why: "list[str]" = []
        prev = by_label.get(row["row"])
        if rec is None and key not in gone_now and prev is not None and prev["row_key"] not in keys_now \
                and prev["row_key"] not in gone_now:    # a dropped old version is no record of it (P3)
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
        elif rec["verdict"] == "proved":                # every proof's base is judged (task 283, sonnet)
            res, e["base_why"], e["base_helps"] = _base_check(rec, ref, view, e["late"])
            e["state"] = BASE_STATES[res]
            if res == "unchecked":
                unchecked_rows.append(row["row"])
        elif rec["verdict"] == "partly proved" and e["late"]:   # its proved tests' base too (task 286, opus)
            res, e["base_why"], e["base_helps"] = _base_check(rec, ref, view, True)
            e["state"] = "partly proved"
            if res != "ok":
                e["base_off"] = BASE_STATES[res]
            if res == "unchecked":
                unchecked_rows.append(row["row"])
        else:
            e["state"] = rec["verdict"]
        nt = rec.get("now_tree") if isinstance(rec.get("now_tree"), dict) else {}
        e["moved"] = False
        if e["state"] != "stale" and view is not None:
            for scope, today in view.today.items():
                then = view.digest(scope, nt.get(scope))
                if today is not None and then is not None and then != today:
                    e["moved"] = True
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
    notes += [f"row {lab} is not a number — `tasks red` refuses the contract until each row's `#` is one"
              for lab in labels if lab and not _NUMBER.fullmatch(lab)]
    anchor_after = False
    if records and view is not None:                    # the first run before the code? (task 282, opus) — against
        fn = records[0].get("now_tree")                 # the reference: base.json when there is one (codex)
        for scope, e in (ref.items() if isinstance(ref, dict) else ()):
            then = view.digest(scope, _ref_treeish(e), code=True)
            now = view.digest(scope, fn.get(scope) if isinstance(fn, dict) else None, code=True)
            if then is not None and now is not None and then != now:
                anchor_after = True
    if anchor_after:
        notes.append("the first run came after the code — its code already differed from its base, so a row added "
                     "before it cannot be told from one written at design (run `tasks red` once the red tests are "
                     "written, before the code)")
    rewritten = []                                      # the scopes the task changed whose start is off HEAD
    newest = records[-1].get("now_tree") if records and isinstance(records[-1].get("now_tree"), dict) else {}
    for scope, r in (ref.items() if isinstance(ref, dict) and view is not None else ()):
        if isinstance(r, dict) and view.on_head(scope, r.get("commit")) is False:
            then = view.digest(scope, _ref_treeish(r), code=True)
            last = view.digest(scope, newest.get(scope), code=True)
            changed = None if then is None or last is None else then != last     # known, or not claimed (283, agy)
            notes.append(f"the activation's commit in {scope or 'the project'} is not an ancestor of HEAD (a rebase, "
                         "an amend, or another branch)" + {True: " — a late row whose code is there cannot prove",
                                                           False: "; the task changed no code there",
                                                           None: "; whether the task changed code there is not known"
                                                           }[changed])
            if changed:
                rewritten.append(scope)
    if old:
        notes.append(f"{old} record(s) without a row key (written before task 281) are not read — `tasks red` "
                     "records the rows again")
    refresh = any(e["state"] in _REFRESHED for e in out)   # git is asked only for a refresh line (285, opus)
    lost = []                                           # a start no run can be judged against (284, codex; 285)
    for s, r in sorted(ref.items()) if isinstance(ref, dict) else ():
        if not isinstance(r, dict) or (view is not None and s not in view.scopes):    # a re-run never reads a
            continue                                                                 # removed root (286, sonnet)
        if r.get("clean") is False and not isinstance(r.get("tree"), str):
            lost.append(f"{s or 'the project'}'s activation tree was not captured")
        elif refresh and view is not None and view.has(s, _ref_treeish(r)) is False:
            lost.append(_GONE_START.format(s or "the project").split(" — ")[0])
    if lost:                                            # `_plan_base` refuses every run there, or none is judged
        hint_base, hint_why = None, f"cannot be refreshed — {lost[0]}, so no run can be judged against it"
    if unchecked_rows:                                  # what could not be judged is said (task 283, opus, sonnet)
        mine = [e for e in out if e["row"] in unchecked_rows]
        helps = set() if lost else {e.get("base_helps") for e in mine}   # each remedy said (285); none can help a
        notes.append(f"{_rows_word(unchecked_rows)}: a base that could not be judged ("     # lost start (286, opus)
                     + "; ".join(sorted({e["base_why"] for e in mine if e.get("base_why")})) + ")"
                     + (" — `tasks red --check` asks again" if "check" in helps else "")
                     + (" — a re-run of the row records it anew" if "rerun" in helps else ""))
    return {"rows": out, "runs": runs, "notes": notes, "anchor_after": anchor_after, "rewritten": rewritten,
            "hint_base": hint_base, "hint_why": hint_why, "half": bool(lost), "missing": missing,
            "dropped": sorted(gone_now.values(), key=lambda d: (str(d.get("at", "")), d["row"])),
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
    if state in ("proved on older code", "proved on unrelated code", "proved on an unchecked base"):
        return state[len("proved "):]                   # each counted apart from proved (task 283)
    return "not proved"


def _counts(rows: "list[dict]", st: "dict | None" = None) -> str:
    """The counts line — the same groups for `--check`, the close and `tasks numbers` (impl round 1, opus). When the
    first run came after the code, "added later" is not known (task 282, opus)."""
    apart = ("start only", "on older code", "on unrelated code", "on an unchecked base")
    count = {g: [e["row"] for e in rows if _group(e["state"]) == g] for g in _GROUPS + apart}
    late = [e["row"] for e in rows if e["late"]]
    moved = [e["row"] for e in rows if e.get("moved")]
    noreader = [e["row"] for e in rows if e.get("noreader")]

    def n(label: str, xs: "list[str]") -> str:
        return f"{label} {len(xs)}" + (f" ({_rows_word(xs)})" if xs and label not in _GROUPS else "")
    return (", ".join(n(g, count[g]) for g in _GROUPS) + "; "
            + "; ".join((n("on the task's start only", count["start only"]), n("on older code", count["on older code"]),
                         n("on unrelated code", count["on unrelated code"]),
                         n("on an unchecked base", count["on an unchecked base"]),
                         (f"added later not known ({len(late)} after the first run)" if (st or {}).get("anchor_after")
                          else n("added later", late)),
                         n("working state changed since the run", moved), n("no skip reader", noreader))))


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
        if e["late"]:                                   # only what the first run shows when it came after the code
            s += (" — added later" + (" (per the first run)" if st.get("anchor_after") else "")       # (283, sonnet)
                  + (f", on {_base_short(e['rec'].get('base'))}" if e.get("rec") else ""))
        if e.get("moved"):
            s += " — the working state changed since its run"
        if e.get("noreader"):
            s += " — no skip reader matched its output"
        lines.append(s)
    for m in st.get("missing") or ():                  # by its key, its number at its last run (task 287)
        lines.append(_one_line(f"  row {m['row']} ({m['kind']}, {m['test']}): recorded, no longer in the contract — "
                               f"{_drop_command(task_ref, m['row'])} records its removal"))
    for x in st.get("dropped") or ():
        lines.append(_one_line(f"  dropped: row {x['row']} ({x.get('kind', '')}, {x.get('test', '')}) — "
                               f"{x.get('reason', '')} ({x.get('at', '')})"))
    lines.append("  " + _counts(rows, st))
    if st.get("unchecked"):
        lines.append(f"  (staleness not checked: {st['unchecked']} — the rows' text and commands were)")
    elsewhere = ("proved on older code", "proved on unrelated code", "proved on an unchecked base")
    hopeless = {e["row"] for e in rows if e["state"] == "proved on an unchecked base"     # by its cause code, not
                and not e.get("base_helps")}                                            # its words (284, agy)
    plain = [e["row"] for e in rows if not e["late"] and e["state"] in ("stale", "no record") + elsewhere
             and e["row"] not in hopeless]
    needs_base = [e["row"] for e in rows if e["late"] and (e["state"] in ("stale", "no record",
                                                                          "proved on the task's start only") + elsewhere
                                                           or (e["state"] == "partly proved" and e.get("base_off")))
                  and e["row"] not in hopeless]                       # (task 286, agy, codex, grok)
    if plain and st.get("hint_base") is not None:      # without base.json the hint names the base (284, opus)
        lines.append(f"  refresh: tasks red {task_ref} --rows {','.join(plain)}{st.get('hint_base') or ''}")
    elif plain:                                         # why none can be given (task 284)
        lines.append(f"  refresh: rows {','.join(plain)} {st.get('hint_why') or _NO_COMMIT}")
    if needs_base and st.get("half"):                   # said, not dropped (task 286, sonnet)
        lines.append(f"  added later: rows {','.join(needs_base)} {st.get('hint_why') or _NO_COMMIT}")
    elif needs_base and not st.get("rewritten"):        # no hint for a base that cannot exist (task 282, sonnet)
        lines.append(f"  added later, each needs its own base: tasks red {task_ref} --rows {','.join(needs_base)} "
                     "--base <scope>=<the commit before its fix>")
    return lines


class _Trees:
    """Trees compared by their listing with the excludes left out (impl round 1, opus: a working-state tree keeps the
    index's `.agent` entries, so committing the task's records moved it), each listed once by `git ls-tree` (which
    writes nothing); `today` is each scope's working state, when it was read. `code=True` compares only the code —
    `core.classify_delta_paths`' behavioral paths that are not test paths (task 282, opus and sonnet: a MIND_MAP or
    root `*.md` edit is no code). `newer` and `on_head` ask `git merge-base --is-ancestor`. Every answer is cached;
    `frozen` (under the task lock) answers only from the cache — no git runs there (task 282, opus and sonnet). Past
    the deadline, a frozen miss, or a git that cannot answer: None — not known, so nothing is claimed from it."""

    def __init__(self, scopes: "dict[str, Path]", exclude: "list[str]", cfg: "dict | None" = None,
                 deadline: "float | None" = None):
        self.scopes = scopes
        self.exclude = list(exclude) if exclude else [":(exclude).agent"]
        self.cfg = cfg or {}
        self.deadline = deadline
        self.today: "dict[str, str | None]" = {}
        self.today_trees: "dict[str, str]" = {}        # the working-state trees themselves (task 286)
        self.frozen = False
        self.ran_out = False
        self.missed = False
        self.last_none = ""                             # why the last answer was None: frozen/budget/git/scope/tree
        self._seen: dict = {}
        self._digests: dict = {}

    def left(self) -> float:
        return 120.0 if self.deadline is None else self.deadline - time.monotonic()

    def _ask(self, key, work):
        if key in self._seen:
            if self._seen[key] is None:
                self.last_none = "git"
            return self._seen[key]
        if self.frozen:
            self.missed, self.last_none = True, "frozen"
            return None
        if self.left() <= 0:
            self.ran_out, self.last_none = True, "budget"
            return None
        try:
            self._seen[key] = work(max(self.left(), 1.0))
        except (RuntimeError, OSError, subprocess.SubprocessError, ValueError):
            self._seen[key] = None
        if self._seen[key] is None:
            self.last_none = "git"
        return self._seen[key]

    def entries(self, scope: str, treeish, code: bool = True) -> "dict[str, tuple[bytes, bytes]] | None":
        """A tree's listing as path → (mode, blob) — its code only by default (task 283, codex: modes too). Each None
        leaves its own cause in `last_none` (task 284, sonnet, opus)."""
        self.last_none = ""
        if scope not in self.scopes:
            self.last_none = "scope"
            return None
        if not isinstance(treeish, str) or not treeish:
            self.last_none = "tree"
            return None

        def work(timeout):
            entries = _listing(self.scopes[scope], treeish, self.exclude, timeout)
            if code:
                from tasks.core import classify_delta_paths
                behavioral = set(classify_delta_paths([e[0] for e in entries], is_outer_scope=(scope == ""))[0])
                entries = [e for e in entries if e[0] in behavioral and not _is_test_path(e[0], self.cfg)]
            return {rel: (mode, blob) for rel, mode, blob in entries}
        return self._ask(("entries", scope, treeish, code), work)

    def digest(self, scope: str, treeish, code: bool = False) -> "str | None":
        if not isinstance(treeish, str) or not treeish:  # a record's value is any JSON: never a key (284, sonnet, codex)
            self.entries(scope, treeish, code)          # None, leaving its cause
            return None
        key = (scope, treeish, code)
        if key in self._digests:                        # worked out once; a cached one is no frozen miss (284)
            self.last_none = ""
            return self._digests[key]
        listed = self.entries(scope, treeish, code)
        if listed is None:
            return None
        self._digests[key] = _digest_entries([(p, m, b) for p, (m, b) in listed.items()])
        return self._digests[key]

    def _ancestor(self, scope: str, old: str, new: str) -> "bool | None":
        if old.startswith("-") or new.startswith("-") or "\0" in old + new:
            return None                                 # never an option to git (task 286, opus, sonnet)

        def work(timeout):
            r = _git(self.scopes[scope], ["merge-base", "--is-ancestor", old, new], timeout=timeout)
            return True if r.returncode == 0 else (False if r.returncode == 1 else None)
        return self._ask(("ancestor", scope, old, new), work)

    def has(self, scope: str, treeish) -> "bool | None":
        """Whether a tree (or a commit's) is still in the scope's repository: False only when git says it is gone,
        None when not known (task 285) — asked only once a listing has failed, or for a refresh line."""
        if scope not in self.scopes or not isinstance(treeish, str) or not treeish:
            return None
        return self._ask(("has", scope, treeish), lambda timeout: _tree_state(self.scopes[scope], treeish, timeout))

    def newer(self, scope: str, old, new) -> "bool | None":
        """`new` is a commit strictly after `old` (its descendant, not itself — task 282, agy and opus); an `old` of
        None (an unborn repository) has every commit after it."""
        if scope not in self.scopes or not isinstance(new, str) or not new:
            return None
        if old is None:
            return True
        if not isinstance(old, str) or old == new:
            return False
        return self._ancestor(scope, old, new)

    def older(self, scope: str, old, new) -> "bool | None":
        """`old` is a commit strictly before `new` (its ancestor, not itself) — the base older than the start."""
        if scope not in self.scopes or not isinstance(old, str) or not old or not isinstance(new, str) or not new:
            return None
        if old == new:
            return False
        return self._ancestor(scope, old, new)

    def on_head(self, scope: str, commit) -> "bool | None":
        """`commit` is HEAD or an ancestor of it — False: history was rewritten since (task 282, sonnet)."""
        if scope not in self.scopes or not isinstance(commit, str) or not commit:
            return None
        return self._ancestor(scope, commit, "HEAD")


def _today(scopes: "dict[str, Path]", cfg: dict, exclude: "list[str]", view: _Trees) -> "tuple[str | None, str]":
    """Each scope's working-state tree into `view.today`, and the tests digest read from them — (None, why) when one
    cannot be read, or the deadline passed (the close's budget; plan panel: sonnet)."""
    from tasks.post_d6 import worktree_tree_why
    trees: "dict[str, str]" = {}
    for name, repo in scopes.items():
        if view.left() <= 0:
            return None, "the time budget ran out"
        t, why = worktree_tree_why(repo, exclude)
        if not t:
            return None, f"the working state of {name or 'the project'} cannot be read ({why})"
        trees[name] = view.today_trees[name] = t        # kept as built, whatever follows (task 286, agy)
    try:
        tests = _digest_trees(scopes, trees, cfg, exclude, max(view.left(), 1.0))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return None, f"the test files cannot be listed ({exc})"
    for name, t in trees.items():
        view.today[name] = view.digest(name, t)
    return tests, ""


def _reference(task_dir: Path, records: "list[dict]"):
    """Each scope's reference base: base.json's (the activation) when the task has one, else the first record's base
    (task 282, codex: a first run on `--base B` is not the task's start)."""
    base = _load_base(Path(task_dir))
    if base and isinstance(base.get("scopes"), dict) and "failed" not in base:
        return base["scopes"]
    return records[0].get("base") if records else None


_NO_COMMIT = "need a run on the start, and with no base.json the start names no commit to pass as --base"


def _base_hint(task_dir: Path, ref, view=None) -> "tuple[str | None, str]":
    """(what a refresh hint adds, why there is none). Nothing with base.json; else `--base` for each scope of the
    reference — `tasks red` refuses a run without one there (task 284, opus), each `scope=commit` shell-quoted (284,
    codex) — or None when a scope names no commit, or its start was uncommitted work: no commit holds that code, and
    a re-run on its commit reads "older" again (task 284, opus, sonnet, codex). With the view (task 285): a scope no
    longer configured is left out (`_plan_base` refuses its `--base` — opus, codex), and a start whose uncommitted
    part is not code (its code digest the commit's) is named by its commit (codex)."""
    base = _load_base(Path(task_dir))
    if base and isinstance(base.get("scopes"), dict) and "failed" not in base:
        return "", ""
    if not isinstance(ref, dict) or not ref:
        return None, _NO_COMMIT
    parts = []
    for scope, e in sorted(ref.items()):
        if view is not None and scope not in view.scopes:
            continue
        commit = e.get("commit") if isinstance(e, dict) else None
        if not isinstance(commit, str) or not commit:
            return None, _NO_COMMIT
        if e.get("used") != "commit" and isinstance(e.get("tree"), str) and e.get("clean") is not True:
            code = None if view is None else view.digest(scope, e["tree"], code=True)
            held = None if view is None else view.digest(scope, commit, code=True)
            if view is not None and (code is None or held is None):     # not known is no "uncommitted" (285, opus)
                return None, (f"need a run on the start, and with no base.json {scope or 'the project'}'s start's code "
                              "could not be read (git, or the time budget) — no --base is named")
            if code is None or code != held:
                return None, (f"need a run on the start, and with no base.json {scope or 'the project'}'s start was "
                              "uncommitted work — no commit holds it to pass as --base")
        parts.append("--base " + shlex.quote(f"{scope}={commit}" if scope else commit))
    if not parts:                                       # (task 285, sonnet, agy)
        return None, "need a run on the start, and its scopes are no longer configured (a code root removed?)"
    return " " + " ".join(parts), ""


def _prepare(project: Path, cfg: dict, deadline: "float | None", read_trees: bool) -> "tuple[_Trees, str | None, str]":
    """The view and today's tests digest — every tree built OUTSIDE any lock (task 282, sonnet)."""
    from tasks.core import _fingerprint_exclude_pathspecs
    scopes = _scopes(Path(project), cfg)
    exclude = _fingerprint_exclude_pathspecs(cfg)
    view = _Trees(scopes, exclude, cfg, deadline)
    tests_now, why = _today(scopes, cfg, exclude, view) if read_trees else (None, "")
    return view, tests_now, why


def _unchecked(view: _Trees, why: str) -> str:
    if why:
        return why
    if view.ran_out:
        return ("the time budget ran out — a base not judged in time reads `proved on an unchecked base`, and a change of "
                "the working state is not known")
    if view.missed:
        return "a record written while the section was being written was not compared (`tasks red --check` does)"
    return ""


def state_of(project: Path, task_dir: Path, rows: "list[dict]", cfg: dict,
             deadline: "float | None" = None, read_trees: bool = True) -> dict:
    """The state of `task_dir`'s contract rows today — reads task.md's rows (given), the receipt and git; writes
    nothing (building today's working-state trees may store their objects in git, as the close's freshness check
    does — `read_trees=False` builds none and only lists recorded trees)."""
    drops: "list[tuple[int, dict]]" = []
    records, old = _read_records(Path(task_dir) / RECEIPT_NAME, None, drops)
    with _no_git_overrides():
        view, tests_now, why = _prepare(Path(project), cfg, deadline, read_trees)
        ref = _reference(Path(task_dir), records)
        st = compute_state(rows, records, cfg, view, tests_now, old, ref, *_base_hint(Path(task_dir), ref, view),
                           dropped=drops)
    st["unchecked"] = _unchecked(view, why)
    st["undecided"] = ((["tests"] if tests_now is None else []) + (["budget"] if view.ran_out else [])
                       + (["missed"] if view.missed else []))      # what the close's table reads (task 286)
    return st


_GATE_LINE = re.compile(r"- \[[ xX]\] Test contract:")


def _has_contract_gate(text: str) -> bool:
    """The task has the `Test contract:` design gate — a line outside any code fence (a quoted example is none)."""
    from tasks.core import _iter_nonfenced, _physical_lines
    return any(_GATE_LINE.match(s) for _i, s in _iter_nonfenced(_physical_lines(text)))


# The close's check (task 286, PHASE-B 9b part 7; the owner, 2026-10-22 08:26: "Doar ce e sigur greșit"). It replaces
# task 281's report after the close: the close now stops on what is certainly wrong, and passes the rest, said.
_PASSING = ("proved", "already true", "partly proved")
_PLACED = ("proved on older code", "proved on unrelated code", "proved on an unchecked base")   # a design row passes
_WHY_BLOCK = {"no record": "not run", "stale": "stale", "skipped": "skipped", "partly skipped": "skipped",
              "refused": "run badly", "unrunnable": "run badly", "launch failed": "run badly", "timeout": "run badly",
              "broken": "red now", "regressed": "red now"}
_NO_CHANGE = re.compile(r"^\s*(?:[-*]\s+)?No behavior changes:\s*(\S.*?)\s*$")     # a bullet too (286, agy)


def close_blocks(st: dict, task_ref: str = "") -> "list[str]":
    """The close's table (task 286): each row that is certainly wrong — not run, stale, skipped, run badly, red now, or a
    late row not proved on its own base (opus: on the start only, older or unrelated code, an unchecked base) — and a
    staleness the state could not decide (the tests' digest not read). Already true, partly proved and a design row on
    a base the receipt places elsewhere or cannot judge pass; a spent budget alone blocks nothing — it leaves only
    such states, or a late row this table blocks (sonnet, agy, opus). Any other state blocks (fail closed). Each
    recorded row gone from the contract and not dropped blocks, with its drop command (task 287, M1444)."""
    out = []
    for e in st.get("rows") or []:
        why = _row_block(e)
        if why:
            out.append(f"row {e.get('row')}: {why}")
    out += [_missing_block(m, task_ref) for m in st.get("missing") or ()]
    if "tests" in (st.get("undecided") or ()):
        out.append("staleness not checked: the test files' digest was not read")
    return out


def _drop_command(task_ref: str, row: str) -> str:
    return "`" + " ".join(x for x in ("tasks red", task_ref, "--drop", row) if x) + ' --reason "<why>"`'


def _missing_block(m: dict, task_ref: str) -> str:
    """A recorded row gone from the contract — named by its number at its last run, its kind and its test (a record
    keeps no behavior text) — and the command that records its removal (task 287; plan panel P10, opus)."""
    return _one_line(f"row {m['row']} ({m['kind']}, {m['test']}) {_GONE_WORDS} — {m['row']} is its number at its last "
                     f"run; {_drop_command(task_ref, m['row'])} records its removal")


_GONE_WORDS = "is recorded but no longer in the contract"


def _row_block(e: dict) -> "str | None":
    """Why one row blocks — None when it passes."""
    state, late = e.get("state"), bool(e.get("late"))
    if state == "partly proved" and late and e.get("base_off"):         # (task 286, opus)
        return f"partly proved, {e['base_off']} (added later, not proved on its own base)"
    if state in _PASSING or (state in _PLACED and not late):
        return None
    if state in _WHY_BLOCK:
        return f"{state} ({_WHY_BLOCK[state]})"
    if late and (state in _PLACED or state == "proved on the task's start only"):
        return f"{state} (added later, not proved on its own base)"
    return f"{state} (a state the close does not know)"


def close_way(blocks: "list[str]") -> str:
    """The way through a block, by its causes (task 286, opus, sonnet) — never a `tasks red` the check did not name."""
    text = " ".join(blocks)
    ways = []
    if any(w in text for w in ("(not run)", "(stale)", "(added later")):
        ways.append("run the `tasks red` the check names above")
    if any(w in text for w in ("(skipped)", "(run badly)", "(red now)")):
        ways.append("make its test run and pass on the code, then run its row again with `tasks red`")
    if "refuses the contract" in text or "`## Test contract` has no row" in text or "is not a number" in text \
            or "appears twice" in text or "a row with no number" in text:
        ways.append("fix `## Test contract`")
    if "staleness not checked" in text:
        ways.append("`tasks work done` again (git, or the time budget, left a row's staleness undecided)")
    if _GONE_WORDS in text:                             # (task 287, grok: never "put the row back" alone)
        ways.append("record each gone row's removal with the `tasks red --drop` the check names (or put it back in the "
                    "contract)")
    if "recorded a test contract" in text:
        ways.append("put the contract back")
    if "no live `## Test contract` section" in text:    # (task 286, agy, opus)
        ways.append("put the `## Test contract` section back")
    if "while code changed since the start" in text:
        ways.append("write the contract's rows for the code that changed (or undo the change)")
    if "the check cannot read" in text:
        ways.append("repair or remove the receipt's broken line (`tasks red` writes the row again)")
    if "cannot be read" in text or "could not be judged" in text or "could not be read" in text:
        ways.append("fix what the check says, then `tasks work done` again")
    ways.append("or override with --force --reason (written in the receipt)")
    return "; ".join(ways[:-1]) + (" — " if len(ways) > 1 else "") + ways[-1] + "."


def _contract_lines(text: str) -> "list[str]":
    from tasks.core import _live_section_span, _physical_lines
    lines = _physical_lines(text)
    sp = _live_section_span(lines, CONTRACT_HEADING)
    return [] if sp is None else lines[sp[0]:sp[1]]


def _no_change_reason(text: str) -> "str | None":
    """A contract with no row that says why: `No behavior changes: <why>` (the owner, M1439, 2026-10-22 08:47)."""
    from tasks.core import _iter_nonfenced
    for _i, ln in _iter_nonfenced(_contract_lines(text)):  # an example in a fence is none (task 286, sonnet)
        m = _NO_CHANGE.match(ln)
        if m:
            return m.group(1)
    return None


def _recorded_contract(task_dir: Path) -> bool:
    """The activation recorded that the task carried a test contract (`base.json`'s `contract`; task 286, sonnet)."""
    try:
        doc = json.loads((Path(task_dir) / BASE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("contract") is True


def _not_a_repository(project: Path) -> bool:
    """The project is not a repository at all: no `.git` entry on its path up to `/` (the owner, M1439: it passes,
    said). Git's own "not a git repository" is also its word for a broken one — a moved worktree's `gitdir:`, a `.git`
    it cannot read — and depends on the locale: that is git failing, which blocks (task 286, opus)."""
    p = Path(project).resolve()
    for d in (p, *p.parents):
        g = d / ".git"
        if g.exists() or g.is_symlink():
            return False
    return True


def close_digest(project: Path, task_file: Path, text: "str | None" = None) -> str:
    """What the check read, by files only — the receipt's bytes, the contract section, the config — compared again at
    the commit under the task lock, where no git runs (task 286, agy). The test files are not in it: their digest is
    git's (a stated limit; the close's tree check covers a task with an impl round). `text` is the task.md the check
    parsed — one snapshot; the contract gate and base.json are in it too (task 286, codex, grok, opus)."""
    from tasks.core import load_config, read_task_text
    h = hashlib.sha256()
    try:
        fd = _open_receipt(Path(task_file).parent / RECEIPT_NAME, write=False)
    except FileNotFoundError:
        h.update(b"no receipt")
    except OSError as e:
        h.update(f"unreadable: {e}".encode())
    else:
        try:
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                h.update(b)
        finally:
            os.close(fd)
    h.update(b"\0")
    try:
        text = read_task_text(Path(task_file)) if text is None else text
        h.update("\n".join(_contract_lines(text)).encode("utf-8", "surrogateescape"))
        h.update(b"\0gate" if _has_contract_gate(text) else b"\0no gate")
    except OSError as e:
        h.update(f"task.md unreadable: {e}".encode())
    h.update(b"\0")
    try:
        h.update((Path(task_file).parent / BASE_NAME).read_bytes())
    except FileNotFoundError:
        h.update(b"no base")
    except OSError as e:
        h.update(f"base unreadable: {e}".encode())
    h.update(b"\0" + _config_digest(project).encode())
    return h.hexdigest()


def _config_digest(project: Path) -> str:
    """The config as the check read it — kept from the first check, so a config changed after verify is refused (the
    post-panel judge, task 286)."""
    from tasks.core import load_config
    try:
        return hashlib.sha256(json.dumps(load_config(Path(project)), sort_keys=True, default=str).encode()).hexdigest()
    except Exception as e:                              # noqa: BLE001 — a config that cannot be read is a value too
        return f"config unreadable: {e}"


def _code_changed(project: Path, task_dir: Path, cfg: dict, deadline: float) -> "tuple[list[str] | None, str]":
    """The code files changed since the task's start (base.json) — None with why when that cannot be read."""
    base = _load_base(Path(task_dir))
    if not base or not isinstance(base.get("scopes"), dict) or "failed" in base:
        return None, "`No behavior changes:` needs the task's recorded start (base.json) to show no code changed"
    with _no_git_overrides():
        view, _tests, why = _prepare(Path(project), cfg, deadline, True)
        changed = []
        for scope, r in sorted(base["scopes"].items()):
            if scope not in view.scopes:                # unread is no "unchanged" (task 286, agy, opus, sonnet)
                return None, (f"{scope or 'the project'} is no longer configured — whether its code changed since "
                              "the start cannot be read")
            start = view.entries(scope, _ref_treeish(r), code=True)
            now = view.entries(scope, view.today_trees.get(scope), code=True)
            if start is None or now is None:
                return None, f"whether code changed in {scope or 'the project'} could not be read{f' ({why})' if why else ''}"
            changed += [f"{scope + '/' if scope else ''}{path}" for path in sorted(set(start) | set(now))
                        if start.get(path) != now.get(path)]
        for scope in sorted(set(view.scopes) - set(base["scopes"])):   # a code root added since the start
            now = view.entries(scope, view.today_trees.get(scope), code=True)
            if now is None:
                return None, f"whether {scope}'s code changed could not be read"
            changed += [f"{scope}/{path}" for path in sorted(now)]
    return changed, ""


def close_check(project: Path, task_file: Path, budget: float = 20.0) -> dict:
    """The close's check of the test contract (task 286 — task 281 reported it after the close): None for a task with
    no contract, no contract gate and none recorded at its activation; else {"lines", "blocks", "digest"} — the state
    as `--check` prints it, each certainly-wrong row (`close_blocks`) and any reason the receipt cannot be judged (fail
    closed: a guard that cannot judge refuses), and `close_digest` of what was read."""
    from tasks.core import load_config, read_task_text
    project, task_file = Path(project), Path(task_file)
    d = task_file.parent
    text = read_task_text(task_file)
    rows, why = parse_contract(text)
    gate = _has_contract_gate(text)
    digest = close_digest(project, task_file, text)     # one snapshot, for every close (task 286, codex, grok)
    config = _config_digest(project)
    if rows is None and not gate and not _recorded_contract(d):
        return {"applies": False, "lines": [], "blocks": [], "digest": digest, "config": config}
    head = f"test contract (the close's check): task {d.name}"

    def result(lines: "list[str]", blocks: "list[str]") -> dict:
        return {"applies": True, "lines": lines, "blocks": blocks, "digest": digest, "config": config}
    if rows is None:
        if gate:
            return result([f"{head}: {why} — the task has the `Test contract:` gate"], [why])
        return result([f"{head}: {why}"], ["its activation recorded a test contract, and task.md has neither its gate "
                                            "nor its section"])
    if _not_a_repository(project):
        return result([f"{head}: not judged: not a git repository — `tasks red` needs git (the close passes)"], [])
    try:
        cfg = load_config(project)
    except Exception:                                   # noqa: BLE001
        cfg = {}
    deadline = time.monotonic() + budget
    ref_n = d.name.split("-", 1)[0]
    reason = _no_change_reason(text) if not rows else None
    if reason is not None:
        bad: "list[int]" = []                           # a recorded row does not leave by the declaration (287, P5)
        drops: "list[tuple[int, dict]]" = []
        try:
            records, _old = _read_records(d / RECEIPT_NAME, bad, drops)
        except OSError as e:
            return result([f"{head}: the receipt cannot be read ({e})"], [f"the receipt cannot be read ({e})"])
        gone = [_missing_block(m, ref_n) for m in _gone([], records, drops)[0]]
        if bad:
            gone.append(f"the receipt has {len(bad)} line(s) the check cannot read (line {bad[0]}: a truncated record?)")
        changed, why_c = _code_changed(project, d, cfg, deadline)
        if changed is None:
            return result([f"{head}: No behavior changes: {reason} — {why_c}"], [why_c] + gone)
        if changed:
            return result([f"{head}: No behavior changes: {reason} — but code changed since the start: "
                           + ", ".join(changed[:8]) + (" …" if len(changed) > 8 else "")],
                          ["`No behavior changes:` while code changed since the start (" + ", ".join(changed[:8]) + ")"]
                          + gone)
        if gone:
            return result([f"{head}: No behavior changes: {reason} — no code file changed since the start, but "
                           f"{len(gone)} recorded row(s) or line(s) stand"], gone)
        return dict(result([f"{head}: No behavior changes: {reason} — no code file changed since the start"], []),
                    nochange=True)                      # its working state is bound at the commit (post-panel judge)
    problems = contract_problems(rows)
    if problems:
        return result([f"{head}: {p} — `tasks red` refuses the contract" for p in problems], problems)
    try:
        st = state_of(project, d, rows, cfg, deadline)
    except OSError as e:
        return result([f"{head}: the receipt cannot be read ({e})"], [f"the receipt cannot be read ({e})"])
    except Exception as e:                              # noqa: BLE001 — a check that cannot judge refuses
        return result([f"{head}: the receipt could not be judged ({type(e).__name__}: {e})"],
                      [f"the receipt could not be judged ({type(e).__name__}: {e})"])
    lines = render_state(st, ref_n, f"{head}, {len(rows)} row(s), {st['records']} record(s)")
    blocks = close_blocks(st, ref_n)
    bad: "list[int]" = []
    try:
        _read_records(d / RECEIPT_NAME, bad)
    except OSError:
        pass                                            # read just above by `state_of`
    if bad:                                             # an older record never stands in for a broken line (opus)
        blocks.append(f"the receipt has {len(bad)} line(s) the check cannot read (line {bad[0]}: a truncated record?)")
    if st.get("half") and len(blocks) > len(st.get("missing") or ()):  # a drop clears a gone row (287, grok)
        blocks.append("no run can be judged against the start — `--force --reason` is the only way through")
    return result(lines, blocks)


def numbers_line(project: Path, task_dir: Path, budget: float = 10.0) -> "str | None":
    """`tasks numbers`'s contract line, read live (never written into numbers.json): the counts `--check` prints — it
    builds no working-state tree, so `tasks numbers` stays read-only and the test files are not checked (impl round
    1, opus). None for a task without a live `## Test contract`."""
    from tasks.core import load_config, read_task_text
    rows, _why = parse_contract(read_task_text(Path(task_dir) / "task.md"))
    if rows is None:
        return None
    try:
        cfg = load_config(Path(project))
    except Exception:                                   # noqa: BLE001
        cfg = {}
    st = state_of(Path(project), Path(task_dir), rows, cfg, time.monotonic() + budget, read_trees=False)
    return (f"contract: {len(rows)} row(s) — {_counts(st['rows'], st)} (test files not checked — `tasks red --check` "
            "reads them" + (f"; not checked either: {st['unchecked']}" if st.get("unchecked") else "") + ")")


def _listing(repo: Path, treeish: str, exclude: "list[str]", timeout: float) -> "list[tuple[str, bytes, bytes]]":
    """A tree's blobs — (path, mode, blob), the excludes left out — by `git ls-tree`. Gitlinks are left to their own
    scope. RuntimeError when git cannot list it — or a value that would be an option (task 286, opus, sonnet)."""
    if treeish.startswith("-") or "\0" in treeish:
        raise RuntimeError(f"not a tree: {treeish[:12]!r}")
    r = _git(repo, ["ls-tree", "-r", "-z", "--full-tree", treeish], text=False, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"git ls-tree {treeish[:12]} failed")
    out = []
    for ent in r.stdout.split(b"\0"):
        meta, tab, path = ent.partition(b"\t")
        parts = meta.split(b" ")
        if not tab or len(parts) != 3 or parts[1] != b"blob":
            continue
        rel = path.decode("utf-8", "surrogateescape")
        if not _excluded(rel, exclude):
            out.append((rel, parts[0], parts[2]))
    return out


def _digest_entries(entries) -> str:
    h = hashlib.sha256()
    for key, mode, blob in sorted(entries):
        h.update(key.encode("utf-8", "surrogateescape") + b"\0" + mode + b"\0" + blob + b"\n")
    return h.hexdigest()[:16]


def _digest_trees(scopes: "dict[str, Path]", trees: "dict[str, str]", cfg: dict, exclude: "list[str]",
                  timeout: float = 120) -> str:
    """A digest of the test files in each scope's working-state tree — a path, its mode and its blob — the same from
    a run (its frozen "now" tree) and from a check, with no copy (task 281)."""
    ex = list(exclude) if exclude else [":(exclude).agent"]
    entries = []
    for name in sorted(scopes):
        for rel, mode, blob in _listing(scopes[name], trees[name], ex, timeout):
            if _is_test_path(rel, cfg):
                entries.append((f"{name}/{rel}" if name else rel, mode, blob))
    return _digest_entries(entries)


# ---------------------------------------------------------------------------- the command

ROWS_MAX = 10000


def _parse_rows(spec: str) -> "tuple[set[int] | None, str]":
    """`--rows 3,5-7` → {3, 5, 6, 7}; an empty item, a word, an inverted range is refused (plan panel: agy), and so
    is anything but ASCII digits or more than ROWS_MAX rows in all — checked BEFORE a range is expanded (task 282:
    `1-99999999` expanded first; `²` passed `\\d`)."""
    shown = spec if len(spec) <= 40 else spec[:40] + "…"
    bad = f"--rows {shown!r}: a row number, a list or a range like 5-7 (ASCII digits; at most {ROWS_MAX} rows)"
    out: "set[int]" = set()
    for item in spec.split(","):
        m = re.fullmatch(r"[ \t]*([0-9]{1,9})[ \t]*(?:-[ \t]*([0-9]{1,9})[ \t]*)?", item)
        if not m:
            return None, bad
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if hi < lo or len(out) + (hi - lo + 1) > ROWS_MAX:
            return None, bad
        out.update(range(lo, hi + 1))
    return out, ""


def session_task_dir(project: Path) -> "tuple[Path | None, str]":
    """This session's task — the number in `.agent/sessions/<id>/current_state`, read without creating anything (as
    `tasks verify` reads it) — or (None, why): no id, no pointer, or a task that is missing, done or blocked. Never
    the first open task (task 281's parked item; task 282, opus and sonnet: a wrong pick writes into another task)."""
    from tasks.core import _extract_status, resolve_agent_dir, resolve_session_id
    from tasks.intent import find_task_dir
    sid = resolve_session_id(quiet=True)
    if not sid:
        return None, "this session has no id"
    lane = resolve_agent_dir(Path(project))
    p = lane / "sessions" / sid / "current_state"
    try:
        num = p.read_text(encoding="utf-8", errors="replace").strip() if p.is_file() and not p.is_symlink() else ""
    except OSError:
        num = ""
    if not _NUMBER.fullmatch(num):
        return None, "this session points at no task"
    d = find_task_dir(lane / "tasks", num.zfill(3))
    if d is None or not (d / "task.md").is_file():
        return None, f"this session's task {num} does not exist"
    status = _extract_status(d / "task.md")
    if status in ("done", "blocked"):
        return None, f"this session's task {d.name} is {status}"
    return d, ""


def _parse_drop(args: "list[str]") -> "tuple[list[str], int | None, str | None, str]":
    """(the rest, `--drop`'s row number or None, `--reason`'s text or None, an error) — task 287. A reason is any one
    argument that does not start with `--`; an empty one says nothing, so it is refused."""
    rest: "list[str]" = []
    drop: "int | None" = None
    reason: "str | None" = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--drop", "--reason"):
            v = args[i + 1] if i + 1 < len(args) else None
            if a == "--drop":
                if drop is not None or v is None or not _NUMBER.fullmatch(v):
                    return [], None, None, "--drop takes one row number (ASCII digits): `--drop <row> --reason \"<why>\"`"
                drop = int(v)
            else:
                if reason is not None or v is None or v.startswith("--"):
                    return [], None, None, "--drop needs one --reason \"<why>\" (the removal is recorded with it)"
                reason = v
            i += 2
            continue
        rest.append(a)
        i += 1
    if drop is None and reason is not None:
        return [], None, None, "--reason goes with --drop: `tasks red [<N>] --drop <row> --reason \"<why>\"`"
    if drop is not None and not (reason or "").strip():
        return [], None, None, "--drop needs a --reason that says why (an empty one says nothing)"
    if reason is not None and _LINE_BREAKERS.search(reason):    # (task 287, impl round 1 — grave)
        return [], None, None, "--drop's --reason is one line of text: no line break or other control character"
    return rest, drop, reason, ""


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
            if tree and _tree_exists(repo, tree):        # its `clean` carried along (task 284, sonnet)
                plan[name] = {"commit": commit, "tree": tree, "used": "tree", "clean": b.get("clean")}
            elif not commit:
                _refuse(f"the base of {where} had no commit and its working-state tree is gone (git gc?) — name a "
                        "base with --base")
            elif commit and _commit_of(repo, commit) and b.get("clean"):
                plan[name] = {"commit": commit, "tree": tree, "used": "commit", "clean": True,
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


USAGE = ("usage: tasks red [<N>] [--rows <3,5-7>] [--base [<scope>=]<commit>]…  |  tasks red [<N>] --check  |  "
         "tasks red [<N>] --drop <row> --reason \"<why>\"")


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


def _contained_line(contained: bool, shared_tmp: bool, why_not: str) -> str:
    """How the rows ran, as the section says it."""
    line = ("Each row ran under the judges' write containment (root read-only)." if contained else
            f"Uncontained ({why_not}): each row ran as `tasks verify` runs the project's command.")
    if contained and shared_tmp:
        line += " /tmp shared (no usable /var/tmp): each live repository was bound read-only."
    return line


def _cmd_drop(project: Path, d: Path, number: int, reason: str) -> None:
    """`tasks red [<N>] --drop <row> --reason "<why>"` (task 287; the owner, M1444, M1448): a `dropped` record for each
    recorded row gone from the contract whose last run had that number — chosen from task.md and the receipt read
    under the task lock and written there in one append (plan panel P6), whatever the contract's own problems (P4) —
    then `## Red receipt` rewritten as a run rewrites it. It runs no row. Refused, nothing written: no such row."""
    from tasks.core import read_task_text
    from tasks.filelock import task_lock
    task_file = d / "task.md"
    ref_n = d.name.split("-", 1)[0]
    run = hashlib.sha256(f"drop {os.getpid()} {time.time_ns()}".encode()).hexdigest()[:12]
    try:
        with task_lock(task_file):
            rows, why = parse_contract(read_task_text(task_file))
            if rows is None:
                _refuse(f"{why} — `tasks red --drop` reads the contract")
            drops: "list[tuple[int, dict]]" = []
            try:
                records, _old = _read_records(d / RECEIPT_NAME, None, drops)
            except OSError as exc:
                _refuse(f"{RECEIPT_NAME} cannot be read ({exc}) — nothing to drop from")
            missing = _gone(rows, records, drops)[0]
            targets = [m for m in missing if _NUMBER.fullmatch(m["row"]) and int(m["row"]) == number]
            if not targets:
                _refuse(f"nothing to drop: no recorded row whose last run had number {number} is gone from "
                        f"`{CONTRACT_HEADING}`" + (" (gone, not dropped: " + ", ".join(
                            f"row {m['row']} ({m['kind']}, {m['test']})" for m in missing) + ")" if missing else ""))
            contract = [row_key(r) for r in rows]
            at = _now()
            _append(task_file, *[{"at": at, "run": run, "task": d.name, "verdict": DROPPED, "reason": reason,
                                  "row": m["row"], "kind": m["kind"], "test": m["test"], "row_key": m["key"],
                                  "row_sha": m["row_sha"], "contract": contract} for m in targets])
            newest = records[-1]
    except RuntimeError as exc:                         # the receipt could not be written, or the lock timed out
        _refuse(str(exc))
    print(f"tasks red — task {d.name}, drop {run}:", flush=True)
    for m in targets:
        print(f"  dropped row {m['row']} ({m['kind']}, {m['test']}) — {reason}", flush=True)
    contained_line = _contained_line(newest.get("contained") is True, newest.get("private_tmp") is False,
                                     _containment()[1] or "as its run recorded")
    left, st = _write_section(task_file, project, contained_line)
    if st is None:                                      # the record stands; the close reads it (plan panel P13)
        print(f"  `{RECEIPT_HEADING}` not written — {left}. The drop is in {RECEIPT_NAME}, which the close reads; the "
              f"next `tasks red {ref_n}` run rewrites the section.", file=sys.stderr, flush=True)
        raise SystemExit(1)
    print(f"  the state across runs is in task.md (`{RECEIPT_HEADING}`); `tasks red {ref_n} --check` prints every row.",
          flush=True)


def cmd_red(cmd_args: "list[str]") -> None:
    """`tasks red [<N>] [--base [<scope>=]<commit>]…` — see the module docstring."""
    with _no_git_overrides():
        _cmd_red(cmd_args)


def _cmd_red(cmd_args: "list[str]") -> None:
    """`tasks red [<N>] [--base [<scope>=]<commit>]…` — see the module docstring."""
    from tasks.core import _fingerprint_exclude_pathspecs, _tail_cert_scopes, load_config
    from tasks.core import read_task_text, resolve_agent_dir
    from tasks.intent import find_task_dir
    from tasks.post_d6 import worktree_tree_why
    from tasks.shared import find_project_root
    rest, drop, reason, err = _parse_drop(list(cmd_args))
    args, flags, only, check, err = _parse_flags(rest) if not err else ([], {}, None, False, err)
    if not err and drop is not None and (flags or only is not None or check):
        err = "--drop runs no row, so it takes no --rows, --base or --check"
    if err or len(args) > 1 or (args and not _NUMBER.fullmatch(args[0])):     # ASCII digits (task 283)
        print(f"Error: {err or USAGE}", file=sys.stderr)
        raise SystemExit(2)
    project = Path(find_project_root())
    if args:
        d = find_task_dir(resolve_agent_dir(project) / "tasks", args[0].zfill(3))
    else:
        d, why_none = session_task_dir(project)
        if d is None:
            print(f"Error: tasks red: {why_none} — name the task: `tasks red <N>`", file=sys.stderr)
            raise SystemExit(2)
    if d is None or not (d / "task.md").is_file():
        print(f"Error: tasks red: no task {args[0] if args else ''}", file=sys.stderr)
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
    if drop is not None:                                # before the contract's own problems (task 287, P4)
        _cmd_drop(project, d, drop, str(reason))
        return
    problems = contract_problems(rows)                  # one validation with the close's check (task 286, agy, opus)
    if problems:
        _refuse(problems[0])
    contract = [row_key(r) for r in rows]
    if only is not None:
        numbered = {int(r["row"]) for r in rows}
        missing = sorted(only - numbered)
        if missing:
            _refuse(f"no row {', '.join(map(str, missing[:20]))}{' …' if len(missing) > 20 else ''} in "
                    "`## Test contract`")
        run_rows = [r for r in rows if int(r["row"]) in only]
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
    contained_line = _contained_line(contained, _run_parent() is None, why_not)
    run = hashlib.sha256(f"{os.getpid()} {time.time_ns()}".encode()).hexdigest()[:12]
    base_line = _base_line(plan)
    links, link_notes = _links(project, cfg)
    tmp = Path(tempfile.mkdtemp(prefix="pb-red-", dir=_run_parent()))
    private_tmp = contained and not str(tmp.resolve()).startswith("/tmp/")
    results: "list[dict]" = []
    section_failed = False
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
            tests_ = [t for t in rec.get("tests") or [] if isinstance(t, dict)]
            sides = ("; ".join(f"{k}: {t['base'].get('status')} → {t['now'].get('status')}"    # every test's sides,
                               for k, t in enumerate(tests_, 1)) if len(tests_) > 1             # never a verdict
                     else f"{rec['status']} → {rec['now']['status']}")                          # word (task 282)
            print(f"  row {rec['row']} ({rec['kind']}): {sides}" + (f" — {rec['last']}" if rec.get("last") else "")
                  + (f" — {rec['why']}" if rec.get("why") else ""), flush=True)
        left, st = _write_section(task_file, project, contained_line)
        ref_n = d.name.split("-", 1)[0]
        if st is None:
            print(f"  `{RECEIPT_HEADING}` not written — {left}. The {len(results)} row(s) run are in {RECEIPT_NAME}; "
                  f"`tasks red {ref_n} --check` prints their state.", file=sys.stderr, flush=True)
            section_failed = True
        else:
            ran = {r["row"] for r in results}
            shown = dict(st, rows=[e for e in st["rows"] if e["row"] in ran])
            for ln in render_state(shown, ref_n, f"  the state of the {len(results)} row(s) run, as `## Red receipt` "
                                                 "now says:"):
                print(ln, flush=True)
            print(f"  the state across runs is in task.md (`{RECEIPT_HEADING}`), every record in {RECEIPT_NAME}; "
                  f"`tasks red {ref_n} --check` prints every row.", flush=True)
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
    if section_failed:
        raise SystemExit(1)
