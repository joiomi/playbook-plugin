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

RUNS_NAME = "post-panel-reviews.jsonl"
CONTEXT_NAME = "post-d6-context.md"   # the SETTLED + DELTA parts the last single judge received
PATCH_DIR = "post-d6"                  # `<lane>/journal/post-d6/<task folder>-<run id>.patch`: a CUT delta whole,
PATCH_CAP = 1_000_000                  # for a judge with no shell (task 201) — at most this much of it
DELTA_MARGIN = 2_000
SETTLED_CAP = 8000

SETTLED_HEADER = (
    "SETTLED — an owner ruling closes a design choice, never a correctness defect. "
    "Do not raise a settled item again. Report one only if the code contradicts a "
    "ruling, or a ruling hides a correctness defect — and then tag that finding "
    "[SETTLED-CONTRADICTED] (it counts against PASS). A finding that re-raises a "
    "settled item without that must be tagged [SETTLED]; it does not count against PASS."
)

_OWNER_HEAD_RE = re.compile(r"^## Owner rulings[ \t]*$")
_H2_RE = re.compile(r"^#{1,2} ")
# REJECT in the triage VERDICT position: a bullet that starts with it, or whose
# bold title is followed (after punctuation) by it — not a mention in prose.
_REJECT_LINE_RE = re.compile(
    r"^\s*[-*+]\s+(?:(?:\*\*.+?\*\*[\s.:;,—–-]*)?REJECT\b"      # after the bullet or the bold title
    r"|\*\*[^*]*?[—–-]\s*REJECT\b)")                             # at the end INSIDE the bold title
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_GIT_TIMEOUT = 120


# ── (a) settled block ────────────────────────────────────────────────────────

def extract_owner_rulings(task_text: str) -> "list[str]":
    """Lines of the LAST unfenced `## Owner rulings` section, verbatim (right-
    stripped); the `<!-- pin -->` marker, blank lines and blockquote guidance are
    dropped. A heading inside a code fence is an example, not a section."""
    from tasks.core import _iter_fenced_flags, _physical_lines
    lines = _physical_lines(task_text)       # task 214: a heading inside a line is not the rulings section
    fenced = _iter_fenced_flags(lines, unclosed_is_live=False)
    start = None
    for i, ln in enumerate(lines):
        if not fenced[i] and _OWNER_HEAD_RE.match(ln.rstrip("\r")):       # a CRLF heading too (task 214, T7)
            start = i
    if start is None:
        return []
    out: "list[str]" = []
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if not fenced[i] and _H2_RE.match(ln.rstrip("\r")):
            break
        s = ln.rstrip()
        if not s.strip() or s.strip() == "<!-- pin -->" or s.lstrip().startswith(">"):
            continue
        out.append(s)
    return out


def extract_rejected_findings(task_dir: Path) -> "list[str]":
    """Triage bullets whose VERDICT is `REJECT` (upper case, whole word, right
    after the bullet or after its bold title — `ACCEPT`, `REJECTED` and a mention
    in prose do not match) from judge.md and judge-archive.md, file order,
    de-duplicated. A judge's lines in a round 1.6.2 wrote are quoted (`> `, task 200, #3), so none of them is a
    bullet at a line start; text 1.6.1 wrote, where a judge's own output is not told apart from agent triage, keeps
    being read as before (task 186's rule 10) with its bound: SETTLED only lowers the count for a finding the NEXT
    judge itself tags [SETTLED], and a contradicted ruling still counts (the owner, 2026-09-29, task 108)."""
    out: "list[str]" = []
    seen = set()
    for name in ("judge.md", "judge-archive.md"):
        p = Path(task_dir) / name
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            if _REJECT_LINE_RE.match(ln):
                s = ln.strip()
                if s not in seen:
                    seen.add(s)
                    out.append(s)
    return out


PROJECT_CAP = 4000      # task 227: the project's rulings take at most this much of the SETTLED block, whole entries


def _project_rulings_part(project: "Path | None", cap: int = PROJECT_CAP) -> "list[str]":
    """The SETTLED lines for the project's owner rulings in force (`.agent/rulings.md`, task 227), or [] when there
    are none. Whole entries only, in order, within `cap` characters; the rest are named, never cut. A list that cannot
    be read arrives as its raw text (within `cap`, whole lines) under "cannot be read" — its words still reach the
    judge (plan panel P9)."""
    if project is None:
        return []
    from tasks import rulings as _r
    path = _r.path_for(project)
    if not path.is_file():
        return []
    try:
        force = _r.in_force(_r.read(project))
    except _r.RulingsError as e:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            raw = []
        out = [f"Project rulings (`.agent/rulings.md`) — the list cannot be read ({e}); its text, unparsed:"]
        used = len(out[0]) + 1
        for i, ln in enumerate(raw):
            if used + len(ln) + 1 > cap:
                out.append(f"[... {len(raw) - i} more line(s) of the list — read `.agent/rulings.md` ...]")
                break
            out.append(ln)
            used += len(ln) + 1
        return out
    if not force:
        return []
    out = ["Project rulings in force (`.agent/rulings.md`):"]
    used = len(out[0]) + 1
    for i, r in enumerate(force):
        ln = "- " + r.line()
        if used + len(ln) + 1 > cap:
            out.append(f"[... {len(force) - i} more in force — read `.agent/rulings.md` ...]")
            break
        out.append(ln)
        used += len(ln) + 1
    return out


def settled_block(task_file: "Path | None", cap: int = SETTLED_CAP, *, project_path: "Path | None" = None) -> str:
    """The `=== SETTLED ===` context part, or "" when nothing is settled: the project's owner rulings in force first
    (task 227 — read from `project_path`, or from the project a task.md sits in; a taskless review gets them too), then
    the task's own `## Owner rulings`, then the REJECT lines of its triage. Past `cap` the block is cut on a line
    boundary and says how many lines it left out."""
    text = ""
    if task_file is not None:
        try:
            from tasks.core import read_task_text
            text = read_task_text(Path(task_file))       # task 214 (U5): a lone CR stays inside its line
        except OSError:
            text = ""
    if project_path is None and task_file is not None:
        from tasks.rulings import project_of_task
        project_path = project_of_task(task_file)
    project = _project_rulings_part(project_path)
    rulings = extract_owner_rulings(text)
    rejects = extract_rejected_findings(Path(task_file).parent) if task_file is not None else []
    if not project and not rulings and not rejects:
        return ""
    parts = ["=== SETTLED (owner rulings + findings already rejected in triage) ===",
             SETTLED_HEADER, ""]
    if project:
        parts += [*project, ""]
    if rulings:
        parts += ["Owner rulings (task.md `## Owner rulings`):", *rulings, ""]
    if rejects:
        parts += ["Findings already REJECTED in earlier triage (judge.md / judge-archive.md):",
                  *rejects, ""]
    block = "\n".join(parts).rstrip() + "\n"
    if len(block) > cap:
        lines = block.splitlines()
        kept, used = [], 0
        room = cap - 80                                   # the notice's own line
        for ln in lines:
            if used + len(ln) + 1 > room:
                break
            kept.append(ln)
            used += len(ln) + 1
        block = ("\n".join(kept).rstrip() + f"\n[... {len(lines) - len(kept)} more line(s) left out at the "
                 f"{cap:,}-character cap ...]\n")
    return block


# ── verdict ──────────────────────────────────────────────────────────────────

# A finding starts at the line start or right after a sentence end on the same
# line (grok glues its preamble to the first finding: "…constatările.**Important**").
_FINDING_RE = re.compile(
    r"(?:^|(?<=[.!?:)]))[ \t]*(?:[-*+]\s+|\d+[.)]\s+)?\*\*\s*\[?\s*(Critical|Important)\b",
    re.IGNORECASE)
# Task 209 (D3, the owner 2026-10-15): a Minor finding, written apart — counted on its own, never in the verdict
# and never in the CAP line's equality (plan panel P6: an unmarked finding must stay a mismatch).
_MINOR_RE = re.compile(
    r"(?:^|(?<=[.!?:)]))[ \t]*(?:[-*+]\s+|\d+[.)]\s+)?\*\*\s*\[?\s*Minor\b", re.IGNORECASE)
# The tag counts only RIGHT AFTER the severity marker (inside or just after the
# bold), never a literal "[SETTLED]" later in the finding's prose — a `[file: …]`
# field may stand between the two (task 201: judges reorder fixed markers).
_TAG_AFTER_RE = re.compile(
    r"\]?\s*(?:\*\*)?\s*(?:\[file:[^\]\n]*\]\s*(?:\*\*)?\s*)?"
    r"\[(SETTLED-CONTRADICTED|SETTLED|PRE-EXISTING)\]", re.IGNORECASE)
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


def real_path(project_path, rel: str) -> str:
    """`rel`'s path from the project's real root when it names an existing regular
    file, links resolved (task 201, impl round 1: a spelling or a link must not hide
    a file the delta changed) — "" for anything else, a path that resolves out of
    the project included."""
    try:
        root = os.path.realpath(str(project_path))
        full = os.path.realpath(os.path.join(root, rel))
        if not os.path.isfile(full):
            return ""
        out = os.path.relpath(full, root)
    except (OSError, ValueError):
        return ""
    return "" if out == ".." or out.startswith("../") or os.path.isabs(out) else out


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

def _git(repo, args, env=None):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=_GIT_TIMEOUT)


def worktree_tree(repo: Path, exclude: "list[str]") -> "str | None":
    """A tree object of `repo`'s WORKING state — tracked edits (staged or not),
    deletions and untracked non-ignored files — built in a temporary copy of the
    index, so `.git/index`, the worktree and `git status` are untouched. `exclude`
    are pathspecs (the fingerprint's). None on any git error."""
    return worktree_tree_why(repo, exclude)[0]


def worktree_tree_why(repo: Path, exclude: "list[str]") -> "tuple[str | None, str]":
    """`worktree_tree` plus the reason when it cannot be built (round 2: a base
    that silently failed looked like "panel predates task 108")."""
    repo = Path(repo)
    if not repo.is_dir():
        return None, "not a directory"
    try:
        u = _git(repo, ["ls-files", "-u"])
        if u.returncode == 0 and u.stdout.strip():
            return None, "the index has unmerged (conflict) entries — finish the merge/rebase"
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, "git error"
    tmpd = tempfile.mkdtemp(prefix="pb-base-")
    try:
        r = _git(repo, ["rev-parse", "--git-path", "index"])
        if r.returncode != 0:
            return None, "not a git work tree"
        real = Path(r.stdout.strip())
        if not real.is_absolute():
            real = repo / real
        idx = Path(tmpd) / "index"
        env = dict(os.environ, GIT_INDEX_FILE=str(idx))
        env.pop("GIT_WORK_TREE", None)
        if real.is_file():
            # copy2: keep the stat cache (no full re-hash) AND the index's own mtime —
            # git re-hashes a "racily clean" entry only while the index is not newer
            # than it; a fresh mtime on the copy hid a same-size edit (seen in CI, task 108).
            shutil.copy2(real, idx)
        else:
            head = _git(repo, ["rev-parse", "--verify", "-q", "HEAD"])
            seed = ["read-tree", "HEAD"] if head.returncode == 0 else ["read-tree", "--empty"]
            if _git(repo, seed, env=env).returncode != 0:
                return None, "git read-tree failed"
        a = _git(repo, ["add", "-A", "--", ".", *exclude], env=env)
        if a.returncode != 0:
            return None, "git add failed: " + ((a.stderr or "").strip().splitlines() or ["?"])[0][:200]
        w = _git(repo, ["write-tree"], env=env)
        tree = w.stdout.strip() if w.returncode == 0 else ""
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", tree):
            return tree, ""
        return None, "git write-tree failed"
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, "git error"
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


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
    from tasks.core import _tail_cert_scopes, load_config
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("scopes"), dict):
        return None, "the newest impl panel recorded no snapshot"
    try:
        cfg = load_config(Path(project_path))
    except Exception:
        cfg = {}
    from tasks.core import _scope_identity
    exclude = [x for x in (snapshot.get("exclude") or []) if isinstance(x, str)]
    live = _tail_cert_scopes(Path(project_path), cfg)
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


def patch_dir_ready(d: Path) -> None:
    """Make the patch folder; OSError when it, or the journal it is in, is a link — a
    write through a planted link would go elsewhere (task 192's class)."""
    d = Path(d)
    for x in (d.parent, d):
        if x.is_symlink():
            raise OSError(f"{x} is a link — not written through")
    d.mkdir(parents=True, exist_ok=True)
    if d.is_symlink() or d.parent.is_symlink() or not d.is_dir():
        raise OSError(f"{d} is not a plain folder")


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


def ledger_corrupt_lines(task_dir: Path) -> int:
    """Lines of the run ledger that are not a JSON object (a truncated write)."""
    try:
        lines = (Path(task_dir) / RUNS_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    bad = 0
    for ln in lines:
        if not ln.strip():
            continue
        try:
            ok = isinstance(json.loads(ln), dict)
        except ValueError:
            ok = False
        bad += 0 if ok else 1
    return bad


def read_runs(task_dir: Path) -> "list[dict]":
    """The run ledger, merged by run id (a reservation completed by its `done`
    record; a `failed` run dropped — it spent nothing countable); legacy lines
    without an id are kept as they are. Corrupt lines are skipped."""
    merged: "dict[str, dict]" = {}
    order: "list[str]" = []
    try:
        lines = (Path(task_dir) / RUNS_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for k, ln in enumerate(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or f"#legacy{k}")
        if rid not in merged:
            merged[rid] = {}
            order.append(rid)
        merged[rid].update(r)
    return [merged[i] for i in order if merged[i].get("status") != "failed"]


def append_run(task_dir: Path, rec: dict) -> None:
    rec = dict(rec)
    rec.setdefault("ts", datetime.datetime.now(datetime.timezone.utc)
                   .isoformat(timespec="seconds"))
    with open(Path(task_dir) / RUNS_NAME, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def gate_run(runs: "list[dict]", panel_fp: str, owner_ok: "str | None" = None
             ) -> "tuple[bool, int, str]":
    """(allowed, this run's number after the panel, refusal text). Runs 1-2 are
    free; run k >= 3 needs an owner-ok reason — the owner's rule of 2026-10-13
    18:37 made the plugin's (task 209, PHASE-B.md item 1): "two runs at most,
    then the owner decides". Until task 209 a counted Critical in run 2 opened
    run 3 by itself (task 108)."""
    prior = [r for r in runs if r.get("panel") == panel_fp and r.get("kind") != "panel"]
    n = len(prior) + 1
    if n <= 2:
        return True, n, ""
    if owner_ok and owner_ok.strip():
        return True, n, ""
    why = ("the stop rule allows the post-panel judge two runs at most, then the owner decides"
           + (" (run 2 counted a Critical: fix it, and let the owner decide on a third run)"
              if n == 3 and int(prior[-1].get("critical") or 0) > 0 else ""))
    return False, n, (
        f"post-D6 run cap: this would be single-judge run {n} after impl panel "
        f"{panel_fp}, and {why}. Nothing was spent. Close on the last verdict, or let "
        'the owner decide:  tasks impl-review <N> --owner-ok --reason "…"')


def _owner_alive(rec: dict) -> "bool | None":
    """True/False when the reservation's owning process can be judged (same host,
    live pid), None otherwise."""
    import socket
    pid = rec.get("pid")
    if not isinstance(pid, int) or rec.get("host") != socket.gethostname():
        return None
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True                      # exists, another user
    except (OSError, OverflowError, ValueError):
        return False


def _live_reservation(runs: "list[dict]", panel: str, stale_after: float) -> str:
    """ts of a reservation for `panel` still in progress (not completed, younger
    than `stale_after` seconds), or ""."""
    now = datetime.datetime.now(datetime.timezone.utc)
    for r in runs:
        # ANY live reservation on the task — another single judge or a panel
        # (round 2: a single judge's append voided a running panel, and vice versa)
        if r.get("status") != "reserved":
            continue
        try:
            ts = datetime.datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        # D2-3: a reservation whose process can be judged is live exactly while that
        # process runs (an unlimited review has no expiry to go by); otherwise its
        # own recorded horizon (or the caller's, whichever is longer) decides.
        alive = _owner_alive(r)
        if alive is not None:
            if alive:
                return f'{r.get("kind") or "single-judge"} run {r.get("id")} reserved {r.get("ts")}'
            continue
        # D3-2: the reservation's OWN recorded horizon decides; the caller's timeout
        # only for a legacy record without one (a longer caller must not extend it).
        horizon = r.get("expires_after")
        horizon = float(horizon) if isinstance(horizon, (int, float)) else stale_after
        if (now - ts).total_seconds() < horizon:
            return f'{r.get("kind") or "single-judge"} run {r.get("id")} reserved {r.get("ts")}'
    return ""


def _in_progress_msg(task_file: Path, live: str) -> str:
    rid = live.split(" run ", 1)[1].split(" ", 1)[0] if " run " in live else "<id>"
    return (f"another review on this task is in progress ({live}); wait for it — "
            "concurrent reviews on one task void each other's tamper guard. If that "
            f"process is dead, append the line {{\"id\": \"{rid}\", \"status\": \"failed\"}} "
            f"to {Path(task_file).parent / RUNS_NAME}; a reservation older than the hard "
            "timeout + 10 min counts as spent and no longer blocks.")


def _owner_fields() -> dict:
    import socket
    return {"pid": os.getpid(), "host": socket.gethostname()}


class PanelRefused(Exception):
    """The `gate` passed to `reserve_panel` refused the panel (task 209: the D6 round cap); the text says why."""


def reserve_panel(task_file, stale_after: float = 7200, *, mode: "str | None" = None,
                  bound_since: "str | None" = None, gate=None, record: "dict | None" = None
                  ) -> "tuple[bool, str]":
    """A panel's reservation (round 2): taken BEFORE the panel's tamper snapshot so
    a concurrent single judge cannot append under it; never counted by the cap.
    Task 209: `gate()` runs under the same lock, after the in-progress check — a
    non-empty answer raises `PanelRefused` with it, nothing written; `record` adds
    fields to the reservation (the risk, caps and guard list the close reads)."""
    import secrets
    from tasks.filelock import task_lock
    task_file = Path(task_file)
    with task_lock(task_file):
        if _live_reservation(read_runs(task_file.parent), "", stale_after):
            return False, ""
        refusal = gate() if gate is not None else ""
        if refusal:
            raise PanelRefused(refusal)
        rid = secrets.token_hex(6)
        # task 149: the panel's mode and the stale-close opt-in in force when it ran
        # — a later removal of the opt-in must not free this task from it
        extra = {k: v for k, v in (("mode", mode), ("bound_since", bound_since)) if v}
        append_run(task_file.parent, {"id": rid, "kind": "panel", "status": "reserved",
                                      "expires_after": float(stale_after), **_owner_fields(),
                                      **(record or {}), **extra})
    return True, rid


def finish_panel(task_dir: Path, rid: str) -> None:
    from tasks.filelock import task_lock
    with task_lock(Path(task_dir) / "task.md"):
        append_run(task_dir, {"id": rid, "kind": "panel", "status": "done"})


def panel_refusal(task_file) -> str:
    live = _live_reservation(read_runs(Path(task_file).parent), "", 10 ** 9)
    return _in_progress_msg(Path(task_file), live or "a reservation")


def reserve_run(task_file, panel: str, owner_ok: "str | None", stale_after: float = 7200):
    """Check the cap and RESERVE the run under the task lock, BEFORE the judge is
    spawned (task 108 r1: a check-then-append-after-the-judge window let two
    concurrent runs both pass). Returns (allowed, run number, refusal, run id). A
    reservation never completed (a killed process) keeps counting — the spend
    happened; `fail_run` releases a run that produced no review."""
    import secrets
    from tasks.filelock import task_lock
    task_file = Path(task_file)
    with task_lock(task_file):
        bad = ledger_corrupt_lines(task_file.parent)
        if bad and not (owner_ok and owner_ok.strip()):
            # a truncated record could hide a counted run (codex, task 108): refuse
            return False, 0, (
                f"post-D6 run ledger {task_file.parent / RUNS_NAME} has {bad} corrupt "
                "line(s) — the run count cannot be trusted. Repair or remove them, or let "
                'the owner decide:  tasks impl-review <N> --owner-ok --reason "…"'), ""
        runs = read_runs(task_file.parent)
        live = _live_reservation(runs, panel, stale_after)
        if live:
            # a second concurrent run: its reservation append would change the
            # task dir under the first run's tamper guard and void that paid run
            # (codex, task 108 run 2) — refuse WITHOUT writing.
            return False, 0, _in_progress_msg(task_file, live), ""
        ok, n, msg = gate_run(runs, panel, owner_ok)
        if not ok:
            return False, n, msg, ""
        rid = secrets.token_hex(6)
        append_run(task_file.parent, {"id": rid, "panel": panel, "status": "reserved",
                                      "expires_after": float(stale_after), **_owner_fields(),
                                      "owner_ok": (" ".join(owner_ok.split()) if owner_ok else None)})
    return True, n, "", rid


def finish_run(task_dir: Path, rid: str, rec: dict) -> None:
    from tasks.filelock import task_lock
    with task_lock(Path(task_dir) / "task.md"):
        append_run(task_dir, {**rec, "id": rid, "status": "done"})


def fail_run(task_dir: Path, rid: str) -> None:
    from tasks.filelock import task_lock
    with task_lock(Path(task_dir) / "task.md"):
        append_run(task_dir, {"id": rid, "status": "failed"})


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
