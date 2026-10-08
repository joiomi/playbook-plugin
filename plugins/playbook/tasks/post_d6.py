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
  (b) DELTA (`worktree_tree`, `delta_text`): every panel records, per scope, a
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
    from tasks.core import _iter_fenced_flags
    lines = task_text.splitlines()
    fenced = _iter_fenced_flags(lines, unclosed_is_live=False)
    start = None
    for i, ln in enumerate(lines):
        if not fenced[i] and _OWNER_HEAD_RE.match(ln):
            start = i
    if start is None:
        return []
    out: "list[str]" = []
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if not fenced[i] and _H2_RE.match(ln):
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
    de-duplicated. LIMIT (until writer-controlled
    round delimiters, parked S11 item R9): judge.md does not separate a judge's
    own output from agent triage, so a judge could plant such a line — which is
    why SETTLED only ever lowers the count for a finding the NEXT judge itself
    tags [SETTLED], and a contradicted ruling still counts."""
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


def settled_block(task_file: Path, cap: int = SETTLED_CAP) -> str:
    """The `=== SETTLED ===` context part, or "" when nothing is settled."""
    try:
        text = Path(task_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    rulings = extract_owner_rulings(text)
    rejects = extract_rejected_findings(Path(task_file).parent)
    if not rulings and not rejects:
        return ""
    parts = ["=== SETTLED (owner rulings + findings already rejected in triage) ===",
             SETTLED_HEADER, ""]
    if rulings:
        parts += ["Owner rulings (task.md `## Owner rulings`):", *rulings, ""]
    if rejects:
        parts += ["Findings already REJECTED in earlier triage (judge.md / judge-archive.md):",
                  *rejects, ""]
    block = "\n".join(parts).rstrip() + "\n"
    if len(block) > cap:
        block = block[:cap].rstrip() + f"\n[... settled block truncated at {cap:,} chars ...]\n"
    return block


# ── verdict ──────────────────────────────────────────────────────────────────

# A finding starts at the line start or right after a sentence end on the same
# line (grok glues its preamble to the first finding: "…constatările.**Important**").
_FINDING_RE = re.compile(
    r"(?:^|(?<=[.!?:)]))[ \t]*(?:[-*+]\s+|\d+[.)]\s+)?\*\*\s*\[?\s*(Critical|Important)\b",
    re.IGNORECASE)
# The tag counts only RIGHT AFTER the severity marker (inside or just after the
# bold), never a literal "[SETTLED]" later in the finding's prose.
_TAG_AFTER_RE = re.compile(
    r"\]?\s*(?:\*\*)?\s*\[(SETTLED-CONTRADICTED|SETTLED|PRE-EXISTING)\]", re.IGNORECASE)
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


def parse_verdict(text: str, delta_files: "set[str] | None" = None) -> dict:
    """Classify a judge log. A finding = a line that STARTS with a bold
    `Critical`/`Important` marker (the prompt requires it); its tag, if any, is
    read from that line. PASS iff no counted Critical/Important and the log is
    parseable: a missing final `CAP: k/5` line, or one whose k differs from the
    recognised findings, fails CLOSED as `unparsed` (errs toward one more review)."""
    counts = {"critical": 0, "important": 0, "settled": 0, "contradicted": 0,
              "pre_existing": 0}
    found = 0
    lines = (text or "").splitlines()
    marks = [(i, m) for i, ln in enumerate(lines) for m in _FINDING_RE.finditer(ln)]
    for k, (i, m) in enumerate(marks):
        ln = lines[i]
        found += 1
        # the finding's whole text: from its marker to the next marker (or the end)
        nxt = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        span = "\n".join([ln[m.start():]] + lines[i + 1:max(i + 1, nxt)])
        tag = _TAG_AFTER_RE.match(ln, m.end())
        t = tag.group(1).upper() if tag else ""
        if t == "SETTLED":
            counts["settled"] += 1
            continue
        # [PRE-EXISTING] is only defined relative to a delivered delta, and never
        # for a file the delta changed (task 108 round 2): otherwise it counts.
        if t == "PRE-EXISTING" and delta_files is not None and not _cites_delta(span, delta_files):
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
            f"not counted: {v['settled']} SETTLED, {v['pre_existing']} PRE-EXISTING{extra}")


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
            # than it; a fresh mtime on the copy hid a same-size edit (CI macOS, 108).
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
    try:
        if not base or _git(repo, ["cat-file", "-e", f"{base}^{{tree}}"]).returncode != 0:
            return out(None, f"base tree {base or '(none)'} is not in the object store (pruned by gc?)")
        cur = worktree_tree(repo, exclude)
        if not cur:
            return out(None, "could not build the current working-tree object")
        d = _git(repo, ["diff", "--text", "--no-ext-diff", "--no-color", "-M",
                        base, cur, "--", ".", *exclude])
        if d.returncode != 0:
            return out(None, "git diff failed")
        return out(d.stdout, "", cur)
    except (OSError, subprocess.SubprocessError, ValueError):
        return out(None, "git error")


def delta_text(project_path: Path, snapshot: "dict | None", cap: int,
               trees_out: "dict | None" = None,
               files_out: "set | None" = None) -> "tuple[str | None, str]":
    """The `=== POST-PANEL DELTA ===` part for the newest impl panel's snapshot:
    (text, note). None when any scope lacks a usable base — the caller then falls
    back to the whole-task review and says so. Over `cap`, the part carries each
    scope's `--stat`, the head of the diff and the commands to see the rest."""
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
    chunks, stats = [], []
    for name, repo in live.items():
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
        if trees_out is not None:
            trees_out[name] = cur
        if files_out is not None:
            # from the FULL diff, before any truncation (the verdict's PRE-EXISTING check)
            # --name-status -z: BOTH paths of a rename/copy (task 108 D2-4)
            nm = subprocess.run(["git", "diff", "--name-status", "-z", "-M", base, cur,
                                 "--", ".", *exclude], cwd=str(repo), capture_output=True,
                                timeout=_GIT_TIMEOUT)
            if nm.returncode != 0:
                # D3-3: a partial delta-file set must never feed the verdict
                return None, f"scope {label}: could not build the delta's file list (git diff --name-status failed)"
            from tasks.core import _parse_name_status_z
            files_out.update(_parse_name_status_z(nm.stdout))
        chunks.append(f"### scope {label} — git -C {label} diff {base} {cur}\n"
                      + (text if text.strip() else "(no change in this scope)\n"))
        # stat of the SAME two trees (untracked files included — codex r1)
        st = _git(repo, ["diff", "--stat=200", "-M", base, cur, "--", ".", *exclude])
        stats.append(f"scope {label} — git -C {label} diff --stat {base} {cur}:\n"
                     + (st.stdout if st.returncode == 0 else "(stat unavailable)\n"))
    head = ("=== POST-PANEL DELTA (the newest impl panel's base → the current working "
            "tree; staged, unstaged and untracked) ===\n")
    body = "\n".join(chunks)
    if len(head) + len(body) <= cap:
        return head + body, ""
    notice = (f"[... delta is {len(body):,} chars, over the {cap:,} budget: stat of every "
              "scope (base → captured working tree, untracked files included), then the head "
              "of the diff. For the rest run the `git -C <scope> diff <base> <tree>` command each "
              "scope names — both trees are in the object store ...]\n")
    stat_txt = "\n".join(stats)
    stat_cap = cap // 3                     # the stat is bounded too (codex, task 108 run 2)
    if len(stat_txt) > stat_cap:
        stat_txt = stat_txt[:stat_cap] + "\n[... stat truncated — run the stat command above ...]"
    room = max(0, cap - len(head) - len(notice) - len(stat_txt) - 2)
    return head + notice + stat_txt + "\n" + body[:room], "delta truncated to the budget"


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
    free; run k >= 3 needs a counted Critical in run k-1, or an owner-ok reason.
    LIMIT: "accepted" is the triage's word — the ledger knows the judge COUNTED a
    Critical, not that the agent accepted it."""
    prior = [r for r in runs if r.get("panel") == panel_fp and r.get("kind") != "panel"]
    n = len(prior) + 1
    if n <= 2:
        return True, n, ""
    if owner_ok and owner_ok.strip():
        return True, n, ""
    if n == 3 and int(prior[-1].get("critical") or 0) > 0:
        return True, n, ""
    why = ("run 2 counted no Critical (D6: a third run only after a Critical)" if n == 3
           else "D6 allows no fourth run without the owner")
    return False, n, (
        f"post-D6 run cap: this would be single-judge run {n} after impl panel "
        f"{panel_fp}, and {why}. Nothing was spent. Close on the last verdict, or let "
        'the owner decide:  tasks impl-review <N> --owner-ok --reason "…"')


def _owner_alive(rec: dict) -> "bool | None":
    """True/False when the reservation's owning process can be judged (same host,
    POSIX), None otherwise. Never os.kill on Windows: signal 0 there TERMINATES."""
    import socket
    pid = rec.get("pid")
    if os.name == "nt" or not isinstance(pid, int) or rec.get("host") != socket.gethostname():
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


def reserve_panel(task_file, stale_after: float = 7200, *, mode: "str | None" = None,
                  bound_since: "str | None" = None) -> "tuple[bool, str]":
    """A panel's reservation (round 2): taken BEFORE the panel's tamper snapshot so
    a concurrent single judge cannot append under it; never counted by the cap."""
    import secrets
    from tasks.filelock import task_lock
    task_file = Path(task_file)
    with task_lock(task_file):
        if _live_reservation(read_runs(task_file.parent), "", stale_after):
            return False, ""
        rid = secrets.token_hex(6)
        # task 149: the panel's mode and the stale-close opt-in in force when it ran
        # — a later removal of the opt-in must not free this task from it
        extra = {k: v for k, v in (("mode", mode), ("bound_since", bound_since)) if v}
        append_run(task_file.parent, {"id": rid, "kind": "panel", "status": "reserved",
                                      "expires_after": float(stale_after), **_owner_fields(), **extra})
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
