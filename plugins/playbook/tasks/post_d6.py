"""The post-D6 review protocol (task 108, owner decision Q-E (d), retro 107).

After an assertive/irreversible task spends its D6 panel rounds, the code delta
that follows is closed by a SINGLE judge (D6-amended). Retro 107 measured those
series (104/105/106: 17 runs, 229.3 judge-minutes, 68 % in runs 3+) and found
three causes; each has a part here:

  (a) SETTLED block (`settled_block`): the owner's `## Owner rulings` lines and
      the REJECT lines of earlier triage in judge.md / judge-archive.md, under
      `SETTLED_HEADER`, delivered by BOTH context builders (panel and single
      judge). A finding tagged [SETTLED] does not count against PASS; one tagged
      [SETTLED-CONTRADICTED] does — task.md is agent-written, so a ruling can
      only close a design choice, never hide a correctness defect.
  (b) DELTA (`worktree_tree`, `delta_parts` + `render_delta` = `delta_text`):
      every panel records, per scope, a
      tree of the WORKING state (staged + unstaged + untracked) built in a
      temporary index (GIT_INDEX_FILE) — the real index is never touched. The
      post-D6 judge reviews base → current working tree; a finding outside it
      is [PRE-EXISTING]: parked, not counted. No ref keeps a base alive (a ref
      to a tree breaks `git log --all`); the object lives until `git gc`
      prunes it, and a missing base is reported, never guessed.
  (c) CAP (`gate_run`, run ledger `RUNS_NAME` in the task dir): run 3+ after
      the newest impl panel needs a counted Critical in the previous run, or
      `--owner-ok --reason`, which is recorded in the ledger and the close
      receipt.

`parse_verdict` turns a judge log into the D6-amended verdict: PASS = no counted
Critical or Important. Stdlib only.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# Moved by task 343 (PLAN-PLAYBOOK-2.md 4.1 a); imported back until this module goes.
from tasks.review_ledger import (  # noqa: F401
    RUNS_NAME, PanelRefused, _in_progress_msg, _live_reservation, _owner_alive, _owner_fields,
    append_run, fail_run, finish_panel, finish_run, gate_run, ledger_corrupt_lines, panel_refusal,
    read_runs, reserve_panel, reserve_run)
from tasks.review_view import _GIT_TIMEOUT, _wt_git as _git, patch_dir_ready, worktree_tree, worktree_tree_why  # noqa: F401
from tasks.rulings import (  # noqa: F401
    PROJECT_CAP, SETTLED_CAP, SETTLED_HEADER, _H2_RE, _OWNER_HEAD_RE, _REJECT_LINE_RE, _project_rulings_part,
    extract_owner_rulings, extract_rejected_findings, settled_block)
from tasks.stop_rule import _FINDING_RE, _TAG_AFTER_RE  # noqa: F401

CONTEXT_NAME = "post-d6-context.md"   # the SETTLED + DELTA parts the last single judge received
PATCH_DIR = "post-d6"                  # `<lane>/journal/post-d6/<task folder>-<run id>.patch`: a CUT delta whole,
PATCH_CAP = 1_000_000                  # for a judge with no shell (task 201) — at most this much of it
DELTA_MARGIN = 2_000


_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


# ── (a) settled block ────────────────────────────────────────────────────────


# ── verdict ──────────────────────────────────────────────────────────────────

# Task 209 (D3, the owner 2026-10-15): a Minor finding, written apart — counted on its own, never in the verdict
# and never in the CAP line's equality (plan panel P6: an unmarked finding must stay a mismatch).
_MINOR_RE = re.compile(
    r"(?:^|(?<=[.!?:)]))[ \t]*(?:[-*+]\s+|\d+[.)]\s+)?\*\*\s*\[?\s*Minor\b", re.IGNORECASE)
# Task 201 (#6, the owner's Q-L (a) 2026-09-30): a finding's file, a FIXED field on
# its first line.
_FILE_FIELD_RE = re.compile(r"\[file:\s*([^\]\n]*?)\s*\]", re.IGNORECASE)
_CAP_RE = re.compile(r"CAP:\s*(\d+)\s*/\s*\d+\s+reported", re.IGNORECASE)
# The WHOLE last line must be the CAP line (task 108 round 2: a quoted mention on
# the last line is not a verdict).
# Bare, not in a code span: a backticked CAP is the prompt's EXAMPLE (task 108).
_CAP_LINE_RE = re.compile(
    r"^\s*CAP:\s*(\d+)\s*/\s*\d+\s+reported,\s*(exhausted|more remain)\s*\.?\s*$",
    re.IGNORECASE)
_CITED_PATH_RE = re.compile(r"([\w./@+-]+\.[\w]+):\d+")


def delta_files(delta: str) -> "set[str]":
    """The paths a POST-PANEL DELTA touches (both sides of `diff --git a/X b/Y`)."""
    out: "set[str]" = set()
    for m in re.finditer(r"^diff --git a/(\S+) b/(\S+)$", delta or "", re.MULTILINE):
        out.update((m.group(1), m.group(2)))
    return out


def _cites_delta(line: str, files: "set[str]") -> bool:
    """Does the finding cite `<delta file>:<line>`? Matched on the delta's ACTUAL
    file names (task 108 D3-1: `Makefile:8` has no dot extension), preceded by the
    start, whitespace, a quote/bracket or a `/` (an absolute or repo-prefixed path)."""
    for f in files:
        if f and re.search(r"(?:^|[\s`'\"(\[/])" + re.escape(f) + r":\d+", line):
            return True
    for m in _CITED_PATH_RE.finditer(line):   # a shorter cited path, e.g. `tasks/x.py:3`
        cited = m.group(1).lstrip("./")
        if any(f.endswith("/" + cited) for f in files):
            return True
    return False


def _field_path(raw: str) -> str:
    """The `[file: …]` field's path as a judge writes it, normalised: no quotes, no
    leading `./`, no trailing `:line` (`:12`, `:12-30`, `:12:4`)."""
    v = raw.strip().strip("`'\"").strip()
    v = re.sub(r":\d+(?:[-:,]\d+)*$", "", v)
    while v.startswith("./"):
        v = v[2:]
    return v


def real_path(project_path, rel: str, alias: "tuple[str, str] | None" = None) -> str:
    """`rel`'s path from the project's real root when it names an existing regular
    file, links resolved (task 201, impl round 1: a spelling or a link must not hide
    a file the delta changed) — "" for anything else, a path that resolves out of
    the project included.

    `alias` (task 231, impl round 2 — codex): `(live project, copy)` when `project_path` is a review's copy mounted at
    the live path — a link that names an absolute path in the live project is followed into the copy, as the judge
    read it, instead of out of the copy."""
    try:
        root = os.path.realpath(str(project_path))
        if alias is not None:
            full = _resolve_in_copy(root, os.path.realpath(alias[0]), rel)
            if not full:
                return ""
        else:
            full = os.path.realpath(os.path.join(root, rel))
        if not os.path.isfile(full):
            return ""
        out = os.path.relpath(full, root)
    except (OSError, ValueError):
        return ""
    return "" if out == ".." or out.startswith("../") or os.path.isabs(out) else out


def _resolve_in_copy(copy: str, live: str, rel: str) -> str:
    """`rel` resolved as the judge's mount shows it — the copy at the live path — link by link (impl round 3, codex:
    realpath followed the whole live chain first). The walk is in the judge's coordinates: each step is a live path,
    and only the file it shows is read from the copy; nothing is normalized lexically, so a `..` after a linked
    folder is that link TARGET's parent (the post-panel judge's run 1, codex), and a `..` from the project root
    leaves it. The copy's own path for the end, or '' past 40 links."""
    def shown(p: str) -> str:
        return copy + p[len(live):] if (p == live or p.startswith(live + os.sep)) else p

    parts = [x for x in rel.split("/") if x not in ("", ".")]
    cur = live
    hops = 0
    while parts:
        part = parts.pop(0)
        if part == "..":
            cur = os.path.dirname(cur) or os.sep
            continue
        nxt = os.path.join(cur, part)
        if os.path.islink(shown(nxt)):
            hops += 1
            if hops > 40:
                return ""
            target = os.readlink(shown(nxt))
            if target.startswith("/"):
                cur = os.sep
            parts = [x for x in target.split("/") if x not in ("", ".")] + parts
            continue
        cur = nxt
    return shown(cur)


def _as_real(path: str, exists) -> str:
    """What `exists` says of a path: its real path (a string), the path as written
    (True), or "" — no real file."""
    r = exists(path)
    return path if r is True else (r if isinstance(r, str) else "")


def _names_a_delta_file(ln: str, start: int, end: int, files: "set[str]") -> bool:
    """Does ANY reading of a field's value name a file the delta changed (task 201,
    impl round 2: R2-4, R2-5)? The value read up to each `]` before `end` (a name may
    hold one), each raw (quotes stripped, `:line` kept — a file may be named so) and
    normalised."""
    j = ln.find("]", start, end)
    while j != -1:
        raw = ln[start:j].strip().strip("`'\"").strip()
        if raw and (_in_delta(raw, files) or _in_delta(_field_path(raw), files)):
            return True
        j = ln.find("]", j + 1, end)
    return False


def _in_delta(path: str, files: "set[str]") -> bool:
    """A listed path, its diff side's spelling (`a/`, `b/`), or the end of one."""
    return (path in files or (path[:2] in ("a/", "b/") and path[2:] in files)
            or any(f.endswith("/" + path) for f in files))


def _real_files(files: "set[str]", exists) -> "set[str]":
    """The real paths of the delta's files that still exist (a link or a nested
    scope's link names the file the judge may write by its real path)."""
    out: "set[str]" = set()
    for f in files:
        try:
            r = _as_real(f, exists)
        except Exception:
            continue
        if r:
            out.add(r)
    return out


def _outside_delta(path: str, files: "set[str]", exists,
                   real_files: "set[str] | frozenset[str]" = frozenset()) -> bool:
    """Does a finding's declared file lie OUTSIDE the delta (task 201, #6)? Only a
    real file of the project (`exists`) that the delta did not change — compared as
    written AND by its real path against the listed files' real paths. A listed
    path, a path found nowhere, an absolute one, one with `..`, or no field at all:
    in it."""
    if not path or exists is None or path.startswith("/") or ".." in path.split("/"):
        return False
    if _in_delta(path, files):
        return False
    try:
        real = _as_real(path, exists)
    except Exception:
        return False
    return bool(real) and not _in_delta(real, set(files) | set(real_files))


def parse_verdict(text: str, delta_files: "set[str] | None" = None, exists=None) -> dict:
    """Classify a judge log. A finding = a line that STARTS with a bold
    `Critical`/`Important` marker (the prompt requires it); its tag, if any, is
    read from that line. PASS iff no counted Critical/Important and the log is
    parseable: a missing final `CAP: k/5` line, or one whose k differs from the
    recognised findings, fails CLOSED as `unparsed` (errs toward one more review)."""
    counts = {"critical": 0, "important": 0, "settled": 0, "contradicted": 0,
              "pre_existing": 0, "minor": 0}
    found = 0
    real_files = None       # the delta's files by their real paths, built when a field first needs them
    lines = (text or "").splitlines()
    marks = [(i, m) for i, ln in enumerate(lines) for m in _FINDING_RE.finditer(ln)]
    minors = [(i, m.start()) for i, ln in enumerate(lines) for m in _MINOR_RE.finditer(ln)]
    counts["minor"] = len(minors)
    # every severity marker bounds the finding before it — a Minor too, though it is never counted (task 209,
    # impl round 1, R8: a Minor citing a delta file used to make the PRE-EXISTING finding above it count)
    bounds = sorted([(i, m.start()) for i, m in marks] + minors)
    for k, (i, m) in enumerate(marks):
        ln = lines[i]
        found += 1
        # the finding's whole text: from its marker to the next marker (or the end)
        later = [b for b in bounds if b > (i, m.start())]
        nxt = later[0][0] if later else len(lines)
        # the first line up to the next marker on it (impl round 2, S9: a later finding's text on the same line)
        first = ln[m.start():later[0][1]] if later and later[0][0] == i else ln[m.start():]
        span = "\n".join([first] + lines[i + 1:max(i + 1, nxt)])
        tag = _TAG_AFTER_RE.match(ln, m.end())
        t = tag.group(1).upper() if tag else ""
        if t == "SETTLED":
            counts["settled"] += 1
            continue
        # [PRE-EXISTING] is only defined relative to a delivered delta, and never
        # for a file the delta changed (task 108 round 2): otherwise it counts.
        # Task 201 (#6): the file is the finding's FIXED field on its first line (up
        # to the next marker on it); not counted only when that names a real file
        # the delta did not change AND the text cites no delta file at a line —
        # 1.6.1's check, kept as a reason to count: never fewer counted than 1.6.1.
        if t == "PRE-EXISTING" and delta_files is not None:
            seg_end = later[0][1] if later and later[0][0] == i else len(ln)
            # two fields on the first line, or a value whose any reading names a delta
            # file, is ambiguous: counted (task 201, impl round 2: R2-1, R2-4, R2-5)
            fields = list(_FILE_FIELD_RE.finditer(ln, m.end(), seg_end))
            field = fields[0] if len(fields) == 1 else None
            if field and exists is not None and real_files is None:
                real_files = _real_files(delta_files, exists)
            if (field is not None
                    and not _names_a_delta_file(ln, field.start(1), seg_end, delta_files)
                    and _outside_delta(_field_path(field.group(1)), delta_files, exists,
                                       real_files or frozenset())
                    and not _cites_delta(span, delta_files)):
                counts["pre_existing"] += 1
                continue
        if t == "SETTLED-CONTRADICTED":
            counts["contradicted"] += 1
        counts[m.group(1).lower()] += 1
    # The CAP line must END the response: the last non-empty line (codex, task
    # 108 run 2 — a quoted CAP mid-response is not a verdict).
    tail = [ln for ln in (text or "").splitlines() if ln.strip()]
    cap = _CAP_LINE_RE.match(tail[-1]) if tail else None
    # The FINAL line must BE the CAP line and its count must equal the recognised
    # findings — an earlier or quoted "CAP:" cannot stand in for it, and an
    # unmarked finding shows as a mismatch: fail closed.
    unparsed = (cap is None) or (int(cap.group(1)) != found)
    # "more remain": the judge dropped findings to fit — incomplete, fail closed.
    incomplete = bool(cap) and cap.group(2).lower() == "more remain"
    ok = (not unparsed and not incomplete
          and counts["critical"] == 0 and counts["important"] == 0)
    return {"verdict": "PASS" if ok else "FAIL", "unparsed": unparsed,
            "incomplete": incomplete, "findings": found, **counts}


def verdict_line(v: dict) -> str:
    extra = (" · UNPARSED (fail closed)" if v.get("unparsed") else "") + (
        " · INCOMPLETE — more remain (fail closed)" if v.get("incomplete") else "")
    return (f"POST-D6 VERDICT: {v['verdict']} — counted {v['critical']} Critical / "
            f"{v['important']} Important (incl. {v['contradicted']} SETTLED-CONTRADICTED); "
            f"not counted: {v['settled']} SETTLED, {v['pre_existing']} PRE-EXISTING, "
            f"{v.get('minor', 0)} Minor{extra}")


# ── (b) the delta ────────────────────────────────────────────────────────────


def scope_delta(repo: Path, base: str, exclude: "list[str]", with_tree: bool = False):
    """`git diff base → current working tree` for one scope: (diff text, "") or
    (None, reason); with `with_tree`, a third element = the current tree id. A
    base no longer in the object store is reported, not guessed."""
    def out(text, why, cur=""):
        return (text, why, cur) if with_tree else (text, why)
    from tasks.core import _is_object_id
    if base and not _is_object_id(base):
        # never an option to git (task 202, impl round 2: R2-3)
        return out(None, "the panel's base tree is not an object id")
    try:
        if not base or _git(repo, ["cat-file", "-e", f"{base}^{{tree}}"]).returncode != 0:
            return out(None, f"base tree {base or '(none)'} is not in the object store (pruned by gc?)")
        cur = worktree_tree(repo, exclude)
        if not cur:
            return out(None, "could not build the current working-tree object")
        # the prefixes are asked for: a user's `diff.noprefix` changes every file's
        # header line, which is what the patch is cut into files by (task 178). So is
        # the short form of a gitlink's change: with `diff.submodule=log` or `diff` a
        # nested repository's move is a "Submodule …" line with no header line at all
        # (impl round 1: measured, the whole scope then went out unsorted)
        d = _git(repo, ["diff", "--text", "--no-ext-diff", "--no-color", "--no-textconv", "--ignore-submodules=none", "-M",
                        "--src-prefix=a/", "--dst-prefix=b/", "--submodule=short",
                        base, cur, "--", ".", *exclude])
        if d.returncode != 0:
            return out(None, "git diff failed")
        return out(d.stdout, "", cur)
    except (OSError, subprocess.SubprocessError, ValueError):
        return out(None, "git error")


# The order the delta is handed over in (task 178, PLAN S11 item 28). Git lists a
# scope's files by path, and the outer scope came first: a rewritten ledger row used
# the judge's budget up before the first line of code (task 174 — one line of the
# logger's diff was handed over, none of the tests').
_CLASS_TITLES = ("code", "tests", "docs, the ledger and records")


def _file_class(path: str, outer: bool) -> int:
    """0 code, 1 tests, 2 docs/ledger/records — the file classes of
    `core.classify_delta_paths`, its non-behavioral class split at `tests/`."""
    from tasks.core import classify_delta_paths
    if classify_delta_paths([path], is_outer_scope=outer)[0]:
        return 0
    return 1 if "tests" in path.split("/")[:-1] else 2


def _name_status_entries(repo: Path, base: str, cur: str, exclude: "list[str]"):
    """The delta's files in git's own order — the order of its patch too — one
    entry per changed file: `(path,)`, or `(old, new)` for a rename or a copy.
    None when git could not say."""
    try:
        nm = subprocess.run(["git", "diff", "--no-color", "--ignore-submodules=none", "--name-status", "-z", "-M",
                             base, cur,
                             "--", ".", *exclude], cwd=str(repo), capture_output=True,
                            timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if nm.returncode != 0:
        return None
    toks = nm.stdout.split(b"\0")
    out, i = [], 0
    while i < len(toks):
        status = toks[i]
        i += 1
        if not status:
            continue
        take = 2 if status[:1] in (b"R", b"C") else 1
        paths = tuple(os.fsdecode(t) for t in toks[i:i + take] if t)
        i += take
        if paths:
            out.append(paths)
    return out


def _split_patch(text: str) -> "list[str]":
    """A patch cut into its per-file parts. A part begins at a line that starts with
    `diff --git ` — a line of a file's CONTENT never does, it carries `+`, `-` or a
    space in front. The parts always join back to the text."""
    starts = [m.start() for m in re.finditer(r"^diff --git ", text, re.MULTILINE)]
    if not starts:
        return [text] if text else []
    starts[0] = 0
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]


def _written_as_it_is(path: str) -> bool:
    """Whether git writes `path` into a patch's header line as it is: printable ASCII
    with no double quote and no backslash. Those two, a control character and — with
    git's default `core.quotePath` — any byte outside ASCII make git write the name in
    quotes, escaped; a space does not."""
    return all(" " <= ch <= "~" and ch not in '"\\' for ch in path)


def _pair_parts(entries, parts: "list[str]") -> "list[tuple[tuple, str]] | None":
    """Each file of the list with its part of the patch, or None when the two do not
    pair one to one. Two parts in a row under the SAME header line are one file — a
    type change, which git writes as a deletion and a creation and lists once. Where
    git writes a name as it is the header is compared with it, so a pairing that
    drifted is refused, never believed; a name git quotes is paired by position, the
    count of parts still checked (impl round 1: the comparison used to be skipped for
    any name with a space)."""
    out, j = [], 0
    for paths in entries:
        if j >= len(parts):
            return None
        part = parts[j]
        j += 1
        head = part.split("\n", 1)[0]
        if j < len(parts) and parts[j].split("\n", 1)[0] == head:
            part += parts[j]
            j += 1
        if all(_written_as_it_is(p) for p in paths):
            if head != f"diff --git a/{paths[0]} b/{paths[-1]}":
                return None
        out.append((paths, part))
    return out if j == len(parts) else None


def _printable(name: str) -> str:
    """A path as the text shows it (task 201, impl round 1): the bytes of a name that
    is not UTF-8 (`os.fsdecode`'s surrogates) and every character that is not
    printable as `\\xNN` (`\\uNNNN` above 0xff) — a surrogate cannot be encoded for the
    judge, a line break would break the list."""
    s = name.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    return "".join(ch if ch.isprintable() else (f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}")
                   for ch in s)


def _listing(label: str, names: "list[str]", budget: int) -> str:
    """`label (N): a, b … and K more` — every name counted, as many written as fit."""
    if not names:
        return f"{label} (0)\n"
    line = f"{label} ({len(names)}): "
    shown = 0
    for nm in names:
        rest = len(names) - shown - 1
        more = f" … and {rest} more" if rest else ""
        piece = (", " if shown else "") + nm
        if len(line) + len(piece) + len(more) > budget and shown:
            break
        if len(line) + len(piece) + len(more) > budget:
            piece = piece[:max(8, budget - len(line) - len(more) - 1)] + "…"
        line += piece
        shown += 1
    if shown < len(names):
        line += f" … and {len(names) - shown} more"
    return line + "\n"


_DELTA_HEAD = ("=== POST-PANEL DELTA (the newest impl panel's base → the current working "
               "tree; staged, unstaged and untracked) ===\n")


def delta_parts(project_path: Path, snapshot: "dict | None",
                need_files: bool = True) -> "tuple[dict | None, str]":
    """The git work of the `=== POST-PANEL DELTA ===` part for the newest impl
    panel's snapshot, no cap yet (task 201: the CLI builds it BEFORE it reserves a
    run, so an empty delta is refused with nothing written) — (parts, "") or
    (None, why). None when any scope lacks a usable base — the caller then falls
    back to the whole-task review and says so — or, with `need_files`, when a
    scope's file list cannot be built (D3-3: a partial set must never feed the
    verdict).

    `parts`: `body` (each scope's two trees, then its files FILE BY FILE, task 178:
    code first, then tests, then docs, the ledger and records, over all scopes;
    inside a class the smaller diff first, each run of files under a line that
    names its class and scope), `spans` (where each file's part begins and ends in
    the body), `stats`, `files` (every path the delta changed, scope-relative AND
    scope-prefixed — the verdict's set), `listed` (those paths as the delta names
    them, sorted — task 201, #6), `complete` (whether every scope's list was
    built) and `trees` (each scope's current tree). A scope whose file list
    cannot be paired with its patch is handed in git's own order and the text says
    so — after the code and tests of the scopes that pair, before the docs."""
    from tasks.core import review_scopes, load_config
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("scopes"), dict):
        return None, "the newest impl panel recorded no snapshot"
    try:
        cfg = load_config(Path(project_path))
    except Exception:
        cfg = {}
    from tasks.core import _scope_identity
    exclude = [x for x in (snapshot.get("exclude") or []) if isinstance(x, str)]
    live = review_scopes(Path(project_path), cfg)
    snap_names, live_names = set(snapshot["scopes"]), set(live)
    if snap_names != live_names:
        # A code_root added or removed after the panel: a silent partial delta
        # would hide that scope's code (codex r1) — refuse and say why.
        return None, ("scope set changed since the panel (panel: "
                      + ", ".join(sorted(n or "." for n in snap_names)) + "; now: "
                      + ", ".join(sorted(n or "." for n in live_names)) + ")")
    scope_lines, stats, units = [], [], []      # units: (class, scope no., size, file no., name, text)
    unsorted = []                               # (scope no., label, patch) — handed in git's order
    files: "set[str]" = set()
    listed: "set[str]" = set()
    trees: "dict[str, str]" = {}
    complete = True
    for scope_no, (name, repo) in enumerate(live.items()):
        rec = snapshot["scopes"].get(name)
        base = rec.get("base") if isinstance(rec, dict) else None
        label = name or "."
        if not base:
            why = (rec.get("base_error") if isinstance(rec, dict) else "") or \
                "panel predates task 108, or the base could not be built"
            return None, f"scope {label}: the panel recorded no base ({why})"
        if isinstance(rec, dict) and rec.get("identity") != _scope_identity(Path(project_path), repo):
            return None, f"scope {label}: it points at a different directory than at the panel"
        text, why, cur = scope_delta(repo, base, exclude, with_tree=True)
        if text is None:
            return None, f"scope {label}: {why}"
        trees[name] = cur
        # The file list: from the FULL diff, before any truncation (the verdict's
        # PRE-EXISTING check); --name-status -z gives BOTH paths of a rename/copy
        # (task 108 D2-4). It is also what the patch is sorted by (task 178).
        entries = _name_status_entries(repo, base, cur, exclude)
        if entries is None:
            if need_files:
                # D3-3: a partial delta-file set must never feed the verdict
                return None, f"scope {label}: could not build the delta's file list (git diff --name-status failed)"
            complete = False
        for paths in entries or []:
            for p in paths:
                shown = p if name == "" else f"{name}/{p}"
                # the judge copies the prefixed name as the text prints it (task 201, #6; impl r1)
                files.update((p, shown, _printable(p), _printable(shown)))
                listed.add(_printable(shown))
        scope_lines.append(f"### scope {label} — git -C {label} diff {base} {cur}"
                           + ("" if text.strip() else " — no change in this scope") + "\n")
        paired = _pair_parts(entries, _split_patch(text)) if entries is not None else None
        if paired is None:
            if text.strip():
                unsorted.append((scope_no, label, text))
        else:
            for file_no, (paths, part) in enumerate(paired):
                units.append((min(_file_class(p, name == "") for p in paths), scope_no,
                              len(part), file_no,
                              _printable(paths[-1] if name == "" else f"{name}/{paths[-1]}"), part))
        # stat of the SAME two trees (untracked files included — codex r1)
        st = _git(repo, ["diff", "--no-color", "--ignore-submodules=none", "--stat=200", "-M", base, cur, "--", ".",
                         *exclude])
        stats.append(f"scope {label} — git -C {label} diff --stat {base} {cur}:\n"
                     + (st.stdout if st.returncode == 0 else "(stat unavailable)\n"))
    labels = [name or "." for name in live]
    # The body: each scope's two trees, then the files — and where every file's part
    # begins and ends in it, which is what the cut is named from.
    body = "".join(scope_lines)
    spans = []                                  # (name, start, end) in handing order
    last = None
    # … a class at a time over ALL scopes; inside a class the smaller diff first, so that
    # as many files as possible go whole — whichever scope they are in (so one scope's
    # files of a class can come in several runs, each under its own line)
    ordered = sorted(units, key=lambda u: (u[0], u[2], u[1], u[3]))
    for cls, scope_no, _size, _file_no, shown, part in ordered:
        if cls >= 2:
            break
        if (cls, scope_no) != last:
            body += f"#### {_CLASS_TITLES[cls]} — scope {labels[scope_no]}\n"
            last = (cls, scope_no)
        spans.append((shown, len(body), len(body) + len(part)))
        body += part
    # A scope whose list does not fit its patch goes AFTER what is known to be code
    # and tests and before the docs: in git's order it may begin with a ledger, and
    # handed first it put that in front of the others' code again (impl round 1).
    for scope_no, label, patch in unsorted:
        body += (f"#### in git's own order (its file list could not be paired with the patch) — "
                 f"scope {label}\n")
        spans.append((f"scope {label}, unsorted", len(body), len(body) + len(patch)))
        body += patch
    for cls, scope_no, _size, _file_no, shown, part in ordered:
        if cls < 2:
            continue
        if (cls, scope_no) != last:
            body += f"#### {_CLASS_TITLES[cls]} — scope {labels[scope_no]}\n"
            last = (cls, scope_no)
        spans.append((shown, len(body), len(body) + len(part)))
        body += part
    return {"body": body, "spans": spans, "stats": stats, "files": files,
            "listed": sorted(listed), "complete": complete, "trees": trees}, ""


def _files_line(parts: dict, budget: int) -> str:
    label = "DELTA FILES" if parts.get("complete", True) else \
        "DELTA FILES (incomplete: git could not list every scope's files)"
    return _listing(label, parts["listed"], budget)


def render_delta(parts: dict, cap: "int | None", patch_name: str = "",
                 patch_lines: int = 0) -> "tuple[str, str]":
    """The part from `delta_parts` within `cap` characters (None: whole) — (text,
    note). It OPENS with the list of every path the delta changed (task 201, #6;
    over the cap at most a sixth of it, every name counted). Over `cap` it carries,
    in this order: what was handed whole, which file is cut part-way and which are
    left out — by name — each scope's `--stat`, and as much of the ordered diff as
    fits; the note says the same in short, for the line the CLI prints. When the CLI
    wrote the whole delta to `patch_name` (task 201, II-5), the outro names it
    first, for a judge with no shell."""
    body, spans, stats = parts["body"], parts["spans"], parts["stats"]
    head = _DELTA_HEAD + _files_line(parts, 10 ** 9)
    if cap is None or len(head) + len(body) <= cap:
        return head + body, ""
    head = _DELTA_HEAD + _files_line(parts, cap // 6)
    intro = (f"[... delta is {len(body):,} chars, over the {cap:,} budget. Handed in this order: "
             "code, then tests, then docs, the ledger and records; inside each, the smaller diff "
             "first.\n")
    outro = ("Below: the stat of every scope (base → captured working tree, untracked files "
             "included), then the diff as far as it fits. For what is cut or left out "
             + (f"READ `{patch_name}` ({patch_lines:,} lines) — the whole delta and its whole file "
                "list, written by the CLI for this run; read it in parts (an offset and a limit). "
                "With a shell you may also " if patch_name else "")
             + "run `git -C <scope> diff <base> <tree> -- <path>` with the two trees each scope names — "
             "both are in the object store ...]\n")
    stat_txt = "\n".join(stats)
    stat_cap = cap // 3                     # the stat is bounded too (codex, task 108 run 2)
    if len(stat_txt) > stat_cap:
        stat_txt = stat_txt[:stat_cap] + "\n[... stat truncated — run the stat command above ...]"
    # … and so are the names of what was cut (task 178): at most a sixth of the cap.
    # What the diff may take depends on how long those lines are, and the lines on
    # where the diff is cut — so: once with the whole sixth held back, then with
    # what the lines needed plus a margin, which is all the room that is given up.
    fixed = len(head) + len(intro) + len(outro) + len(stat_txt) + 2
    said, *_ = _cut_lines(spans, max(0, cap - fixed - cap // 6), cap // 6)
    held = min(cap // 6, len(said) + 300)
    room = max(0, cap - fixed - held)
    said, whole, part, left = _cut_lines(spans, room, held)
    note = f"delta truncated to the budget: {len(whole)} of {len(spans)} files handed whole"
    if part:
        note += f"; cut part-way: {part[0][:120]}"
    if left:
        note += f"; left out: {len(left)} ({', '.join(n[:60] for n in left[:3])}" \
                + (", …" if len(left) > 3 else "") + ")"
    return head + intro + said + outro + stat_txt + "\n" + body[:room], note


def delta_text(project_path: Path, snapshot: "dict | None", cap: int,
               trees_out: "dict | None" = None,
               files_out: "set | None" = None) -> "tuple[str | None, str]":
    """`delta_parts` then `render_delta` in one call: (text, note), or (None, why).
    With `files_out` the file list must be complete (the verdict's set)."""
    parts, why = delta_parts(project_path, snapshot, need_files=files_out is not None)
    if parts is None:
        return None, why
    if trees_out is not None:
        trees_out.update(parts["trees"])
    if files_out is not None:
        files_out.update(parts["files"])
    return render_delta(parts, cap)


def patch_path(task_dir: Path, run_id: str) -> Path:
    """The file a cut delta is written to whole, one per run (task 201, II-5: a later
    run must not rewrite the file an earlier review cites): in the lane's journal,
    which init's block gitignores in both lane forms — the delta holds untracked files
    whole (impl round 2, R2-3) — and which the judge sandbox shows (it masks only
    `sessions`, `chat_log.md`, `bash_history`)."""
    task_dir = Path(task_dir)
    return task_dir.parent.parent / "journal" / PATCH_DIR / f"{task_dir.name}-{run_id or 'run'}.patch"


def patch_text(whole: str) -> str:
    """The whole delta as the patch file holds it: at most `PATCH_CAP` characters,
    the rest named by its git commands."""
    if len(whole) <= PATCH_CAP:
        return whole
    return whole[:PATCH_CAP] + (
        f"\n[... the file stops at {PATCH_CAP:,} of {len(whole):,} chars — for the rest run "
        "`git -C <scope> diff <base> <tree> -- <path>` with the two trees each scope names above ...]\n")


def task_room(budget: int, used: int, delta_len: int) -> int:
    """The task's share of a single judge's context — 1.6.1's rule, kept (task 201,
    plan triage 12): what SETTLED and the map (`used`) leave beside the delta, the
    delta held at min(its whole length, a quarter of the budget) — the most 1.6.1's
    cut delta took; at most half the budget, 5,000 at least."""
    return max(5_000, min(budget // 2, budget - used - min(delta_len, budget // 4) - DELTA_MARGIN))


def delta_cap(budget: int, used: int, task_len: int) -> int:
    """The delta's share (task 201, #7 — the owner's Q-K (a), 2026-09-30): what
    SETTLED, the map (`used`) and the task leave, less a margin; a quarter of the
    budget at least (task 108's fixed share, now its floor)."""
    return max(budget // 4, budget - used - task_len - DELTA_MARGIN)

def _cut_lines(spans, room: int, budget: int):
    """What a cut at `room` characters of the body does to its files, in words —
    (the lines, whole, part, left). The file cut part-way and the names of the ones
    left out come first in the budget: they are what a reader has to go and fetch."""
    whole = [nm for nm, _a, b in spans if b <= room]
    part = [(nm, room - a, b - a) for nm, a, b in spans if a < room < b]
    left = [nm for nm, a, _b in spans if a >= room]
    part_line = (f"cut part-way (1): {part[0][0][:200]} — {part[0][1]:,} of {part[0][2]:,} "
                 "chars handed\n") if part else ""
    left_line = _listing("left out", left, max(40, budget - len(part_line) - 60)) if left else ""
    whole_line = _listing("handed whole", whole, max(40, budget - len(part_line) - len(left_line)))
    said = whole_line + part_line + left_line
    if len(said) > budget:                  # a cap too small for even the shortest lines
        said = said[:max(0, budget - 1)] + "\n"
    return said, whole, part[0] if part else None, left


def newest_impl_round(task_dir: Path) -> "dict | None":
    from tasks.core import parse_judge_rounds
    try:
        text = (Path(task_dir) / "judge.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for r in parse_judge_rounds(text):       # newest first
        if r.get("mode") == "impl":
            return r
    return None


# ── (c) the run cap ──────────────────────────────────────────────────────────

def panel_key(rnd: "dict | None") -> str:
    """The key the cap counts runs under: the round's unique `**Round-id:**`
    (task 108 r1 — two panels on the same tree are two panels), or, for a round
    written before it existed, its tree-state."""
    if not rnd:
        return ""
    return rnd.get("round_id") or rnd.get("tree_state") or ""


def receipt_line(runs: "list[dict]", panel_fp: str) -> str:
    prior = [r for r in runs if r.get("panel") == panel_fp]
    if not prior:
        return ""
    line = (f"- **Post-D6 single judge:** {len(prior)} run(s) after impl panel {panel_fp}"
            f" — last verdict {prior[-1].get('verdict', '?')}")
    oks = [" ".join(str(r["owner_ok"]).split()) for r in prior if r.get("owner_ok")]
    if oks:
        line += "; " + "; ".join(f'owner-ok: "{o}"' for o in oks)
    return line


def close_receipt_line(task_dir: Path) -> str:
    """The receipt line for `tasks work done`: the runs after the NEWEST impl panel."""
    rnd = newest_impl_round(task_dir)
    fp = panel_key(rnd)
    return receipt_line(read_runs(task_dir), fp) if fp else ""
