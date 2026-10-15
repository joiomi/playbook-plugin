"""The review stop rule as the plugin's default (PHASE-B.md item 1, task 209).

The owner's rule D6 (2026-09-25) and his ruling of 2026-10-13 18:37, made the plugin's by his answers of 2026-10-15:
  * impl panel rounds are capped by the task's `## Risk` — reversible 1, assertive 2, irreversible 2 (an unset risk
    takes the larger of the two high caps); one round more only when the cap's last round counted a Critical; past
    that only `tasks panel-review <N> --mode impl --owner-ok --reason "…"`. A project changes the caps with
    `.agent/config.json` `review_rounds`.
  * after an impl panel, a stale close needs the single judge's PASS on what changed since only on an irreversible
    (or unclassified) task whose code changed, or where a changed file is under the project's `guard_paths` (empty
    by default) — whatever the task's risk, and before the last round too; otherwise, after the last allowed round
    the close takes `--stale-panel-ok --reason` and its receipt names the files no judge read, and before it the
    project's own rule stands (`stale_panel_requires_post_d6`, or a reason).

A round is an impl panel round that met its quorum (`PANEL VERDICT: PASS` in its header) in judge.md or
judge-archive.md; a below-quorum round and a plan round are not rounds. A Critical is a `**Critical**` finding
marker (`post_d6._FINDING_RE`, matched after one leading `> ` is stripped) in a judge's text — after the round's
first `═` bar, and in a round 1.6.2 wrote (format 2) only on a quoted line, the judges' lines being the quoted
ones — that is not tagged `[SETTLED]`; a `[PRE-EXISTING]` one counts (a panel is given no delta to place it
outside of, as `parse_verdict` counts it without one).
LIMIT (as task 108's run cap): D6 says a Critical that was ACCEPTED; the CLI sees one a judge wrote."""
import fnmatch
import os
import posixpath
import re
import sys
from pathlib import Path

ROUNDS_KEY = "review_rounds"
GUARD_KEY = "guard_paths"
DEFAULT_ROUNDS = {"reversible": 1, "assertive": 2, "irreversible": 2}


class RoundsUnreadable(Exception):
    """judge.md or judge-archive.md exists but cannot be read: the rounds cannot be counted."""


def _warn(msg: str) -> None:
    print(f"  ⚠ .agent/config.json: {msg}", file=sys.stderr, flush=True)


def resolve_rounds(cfg: "dict | None", warn: bool = True) -> "dict[str, int]":
    """The caps per risk: the defaults, each replaced by a positive integer the config gives."""
    out = dict(DEFAULT_ROUNDS)
    raw = (cfg or {}).get(ROUNDS_KEY)
    if raw is None:
        return out
    if not isinstance(raw, dict):
        if warn:
            _warn(f"`{ROUNDS_KEY}` is not an object — the defaults apply {DEFAULT_ROUNDS}")
        return out
    for k in DEFAULT_ROUNDS:
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            if warn:
                _warn(f"`{ROUNDS_KEY}.{k}` = {v!r} is not a positive integer — the default {out[k]} applies")
            continue
        out[k] = v
    return out


def cap_for(risk: str, caps: "dict[str, int]") -> int:
    """A risk's cap; an unset or unknown risk is held to the higher of the two high-consequence caps."""
    if risk in ("reversible", "assertive", "irreversible"):
        return int(caps[risk])
    return max(int(caps["assertive"]), int(caps["irreversible"]))


def resolve_guard_paths(cfg: "dict | None", warn: bool = True) -> "list[str]":
    """The project's code that guards, as path patterns. Absent → []. A value that is not a list of non-empty
    strings reads as EVERY path (`*`): a list that cannot be read must not read as "nothing guards"."""
    raw = (cfg or {}).get(GUARD_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        if warn:
            _warn(f"`{GUARD_KEY}` is not a list of path patterns — every changed path is treated as guard code")
        return ["*"]
    return [x.strip() for x in raw]


def _norm(path: str) -> str:
    """A path or pattern as the comparison reads it: `./`, `//` and a trailing `/` folded (`posixpath.normpath`), a
    leading `/` dropped (impl round 1, R5: `./hooks/`, or a `code_roots` entry `./src`, matched nothing)."""
    s = posixpath.normpath(path.strip()) if path.strip() else ""
    return s.lstrip("/")


def guarded(path: str, patterns: "list[str]") -> bool:
    """Whether a project-relative path (a `code_roots` scope's prefixed with its name, as the close's delta names
    it) is under one of the patterns: the path itself, anything below it, or an `fnmatch` glob (`*` crosses `/`);
    both sides normalised (`_norm`); `.` is the whole project."""
    p = _norm(path)
    for pat in patterns:
        stem = _norm(pat)
        if not stem:
            continue
        if stem == "." or p == stem or p.startswith(stem + "/") or fnmatch.fnmatchcase(p, stem):
            return True
    return False


def names_nothing(project_path: Path, patterns: "list[str]") -> "list[str]":
    """The patterns that name nothing in the project — a typo guards nothing, so the close says so (impl round 1,
    R5). A plain pattern must exist as a file or folder; a glob's folder part (before its first `*?[`) must."""
    out = []
    for pat in patterns:
        stem = _norm(pat)
        if not stem or stem in (".", "*"):
            continue
        glob_at = min([stem.index(c) for c in "*?[" if c in stem] or [len(stem)])
        if glob_at < len(stem):
            head = stem[:glob_at]
            # a glob's folder part — none for a root-level glob such as `CLAUDE*.md` (impl round 2, S8)
            base = head.rsplit("/", 1)[0] if "/" in head else ""
        else:
            base = stem
        if base and not (Path(project_path) / base).exists():
            out.append(pat)
    return out


def _read_rounds(task_dir: Path) -> "list[dict]":
    """Every round of judge-archive.md then judge.md, OLDEST first (both files stack newest first)."""
    from tasks.core import parse_judge_rounds
    out: "list[dict]" = []
    seen: "set[str]" = set()
    for name in ("judge-archive.md", "judge.md"):
        p = Path(task_dir) / name
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except OSError as e:
            raise RoundsUnreadable(f"{p.name} cannot be read ({e})")
        for r in reversed(parse_judge_rounds(text)):
            # a round archived and not yet trimmed from judge.md (a crash between the two writes) is one round
            key = r.get("round_id") or "\n".join(ln.rstrip() for ln in (r.get("body") or "").splitlines()).strip()
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out


def impl_rounds(task_dir: Path) -> "list[dict]":
    """The task's impl panel rounds that met their quorum, oldest first."""
    return [r for r in _read_rounds(task_dir) if r.get("mode") == "impl" and r.get("verdict") == "PASS"]


_BAR = re.compile(r"(?m)^═{6,}")
_QUOTE = re.compile(r"^>[ \t]?")


def round_criticals(rnd: dict) -> int:
    """The Critical findings the judges of one panel round wrote (not `[SETTLED]`)."""
    from tasks.post_d6 import _FINDING_RE, _TAG_AFTER_RE
    body = rnd.get("body") or ""
    bar = _BAR.search(body)
    if not bar:
        return 0
    n = 0
    for ln in body[bar.start():].splitlines():
        quoted = ln.startswith(">")
        if rnd.get("format") == 2 and not quoted:
            continue                      # 1.6.2 quotes every line a judge wrote: the rest is the writer's
        s = _QUOTE.sub("", ln)
        for m in _FINDING_RE.finditer(s):
            if m.group(1).lower() != "critical":
                continue
            tag = _TAG_AFTER_RE.match(s, m.end())
            if tag and tag.group(1).upper() == "SETTLED":
                continue
            n += 1
    return n


def allowed_rounds(risk: str, rounds: "list[dict]", caps: "dict[str, int]") -> "tuple[int, int, bool]":
    """(cap, rounds allowed, extended): the cap, plus one when the cap's last round counted a Critical."""
    cap = cap_for(risk, caps)
    # the owner's words give the extension to the two-round risks only — "a 3-a doar dacă runda 2 a găsit o
    # problemă Critical" (impl round 1, R7): a cap of 1 has none
    extended = cap >= 2 and len(rounds) >= cap and round_criticals(rounds[cap - 1]) > 0
    return cap, cap + (1 if extended else 0), extended


def panel_decision(risk: str, rounds: "list[dict]", caps: "dict[str, int]", task: str = "<N>"
                   ) -> "tuple[bool, str]":
    """(allowed, refusal text) for one more impl panel round."""
    cap, allowed, extended = allowed_rounds(risk, rounds, caps)
    n = len(rounds)
    if n < allowed:
        return True, ""
    how = (f"cap {cap} for a {risk} task, plus one because round {cap} counted a Critical" if extended
           else f"cap {cap} for a {risk} task" + (
               "" if n < cap else f"; round {cap} counted no Critical, so no round {cap + 1}"))
    return False, (
        f"D6 round cap: task {task} has had {n} impl panel round(s) ({how}). Nothing was spent.\n"
        f"  Close on the last round (after it, a single judge reads only the code `{GUARD_KEY}` lists, or an "
        "irreversible task's), or let the owner decide:\n"
        f'  tasks panel-review {task} --mode impl --owner-ok --reason "…"\n'
        f"  (the caps are `{ROUNDS_KEY}` in .agent/config.json)")


_RANK = {"reversible": 0, "assertive": 1, "irreversible": 2}


def stricter(a: "str | None", b: "str | None") -> str:
    """The stricter of two risks (reversible < assertive < irreversible < unset/unknown); None or "" is absent —
    a risk recorded with the panel and the one the task says now: lowering `## Risk` after a panel frees nothing."""
    have = [r for r in (a, b) if r]
    if not have:
        return "unclassified"
    return max(have, key=lambda r: _RANK.get(r, 3))


def judge_required(risk: str, can: bool, behavioral: "list[str]", changed: "list[str]", patterns: "list[str]",
                   cause: str = "") -> "tuple[bool, str]":
    """After an impl panel: does what changed since it need the single judge before a stale close? (required, why).
    `behavioral` = the code paths of the delta, `changed` = every path of it (code, tests, docs, records). An
    irreversible or unclassified task's code needs it in every project — the owner: "Fără nicio configurare"
    (impl round 1, R3: it used to depend on `panel_required_for`); a listed guard file needs it on any task. A delta
    that cannot be listed requires the judge wherever the answer would depend on it."""
    high = risk not in ("reversible", "assertive")
    if not can:
        if patterns or high:
            return True, f"the delta since the panel cannot be listed ({cause or 'unknown cause'})"
        return False, ""
    hits = [p for p in changed if guarded(p, patterns)]
    if hits:
        return True, f"`{GUARD_KEY}` lists " + ", ".join(hits[:8]) + (" …" if len(hits) > 8 else "")
    if behavioral and high:
        return True, f"the task is {risk or 'unclassified'} and its code changed after the panel"
    return False, ""


# ── the close (W3) ───────────────────────────────────────────────────────────

def panel_record(task_dir: Path) -> "dict | None":
    """What the task's impl panels ran under, from EVERY impl panel reservation in the run ledger (impl round 2,
    S2: the newest one alone let a below-quorum round, or a second panel after `## Risk` was lowered, replace what
    the reviewed one recorded): the stricter risk of them all, the union of their guard lists, the larger cap per
    risk. None when no reservation recorded any of it (panels from before task 209)."""
    from tasks import post_d6
    recs = [r for r in post_d6.read_runs(Path(task_dir))
            if r.get("kind") == "panel" and r.get("mode") in (None, "impl")
            and any(k in r for k in ("risk", "guard_paths", "rounds_cap"))]
    if not recs:
        return None
    risks = [r.get("risk") for r in recs if isinstance(r.get("risk"), str) and r.get("risk")]
    risk = None
    for x in risks:
        risk = stricter(risk, x) if risk else x
    guards = sorted({g for r in recs for g in _recorded_guards(r)})
    caps = dict(DEFAULT_ROUNDS)
    seen = [_recorded_caps(r) for r in recs if isinstance(r.get("rounds_cap"), dict)]
    if seen:
        caps = {k: max(c[k] for c in seen) for k in DEFAULT_ROUNDS}
    return {"risk": risk, "guard_paths": guards, "rounds_cap": caps if seen else None}


def merge_records(a: "dict | None", b: "dict | None") -> "dict | None":
    """Two `panel_record` answers as one — the stricter risk, the union of the guard lists, the larger cap per risk:
    the record read when the close decided stays in force at its commit, whatever happened to the ledger meanwhile
    (the single judge, run 2: a ledger deleted or emptied during a `--force` close shed the recorded guard list)."""
    have = [r for r in (a, b) if r]
    if not have:
        return None
    risk = None
    for r in have:
        x = r.get("risk")
        if isinstance(x, str) and x:
            risk = stricter(risk, x) if risk else x
    seen = [_recorded_caps(r) for r in have if isinstance(r.get("rounds_cap"), dict)]
    return {"risk": risk, "guard_paths": sorted({g for r in have for g in _recorded_guards(r)}),
            "rounds_cap": ({k: max(c[k] for c in seen) for k in DEFAULT_ROUNDS} if seen else None)}


def ledger_untrusted(task_dir: Path) -> str:
    """Why the run ledger cannot be trusted as the record of what the panels ran under (impl round 3, T3: a read
    error made `read_runs` return nothing, so the recorded guard list and risk vanished): it exists but cannot be
    read, or has corrupt lines. "" for an absent or clean ledger."""
    from tasks import post_d6
    p = Path(task_dir) / post_d6.RUNS_NAME
    if not os.path.lexists(p):
        return ""
    try:
        with open(p, encoding="utf-8") as fh:
            fh.read()
    except (OSError, UnicodeDecodeError) as e:
        return f"the run ledger {post_d6.RUNS_NAME} cannot be read ({e.__class__.__name__})"
    bad = post_d6.ledger_corrupt_lines(Path(task_dir))
    if bad:
        return f"the run ledger {post_d6.RUNS_NAME} has {bad} corrupt line(s)"
    return ""


def same_round(a: "dict | None", b: "dict | None") -> bool:
    """Two parsed rounds are the same round: by Round-id when both have one, else by their text."""
    if not a or not b:
        return False
    if a.get("round_id") and b.get("round_id"):
        return a["round_id"] == b["round_id"]
    return (a.get("body") or "").rstrip() == (b.get("body") or "").rstrip()


def _recorded_caps(rec: "dict | None") -> "dict[str, int]":
    raw = (rec or {}).get("rounds_cap")
    return resolve_rounds({ROUNDS_KEY: raw} if isinstance(raw, dict) else {}, warn=False)


def _recorded_guards(rec: "dict | None") -> "list[str]":
    raw = (rec or {}).get("guard_paths")
    return [x.strip() for x in raw if isinstance(x, str) and x.strip()] if isinstance(raw, list) else []


def close_state(project_path: Path, task_dir: Path, risk: str, rnd: dict, rec: "dict | None",
                cfg: "dict | None" = None, listable: bool = True, why_not: str = "") -> dict:
    """What a STALE close of the carrying impl round `rnd` needs (the caller decides the verdict). `risk` is the
    close's gate risk (`stricter` of the recorded and the current one). The guard list is the union of the one the
    panel's reservation recorded and today's; the caps the larger of the two per risk — below the cap a project's
    own binding still applies, so a larger cap is never the easier close. Keys: judge, why, reached, rounds,
    allowed, cap, extended, changed, behavioral, can, cause, guards, counted (False when the rounds could not be
    read: then never `reached`), missing (guard patterns that name nothing). `listable` False: the freshness verdict
    is not a plain STALE (a degraded tamper guard, an exclude over code, a missing or unreadable stamp), so no delta
    is trusted — the judge is required wherever a guard list or the automatic judge applies (impl round 1, R4)."""
    from tasks.core import load_config, tail_cert_delta_why
    if cfg is None:
        try:
            cfg = load_config(Path(project_path))
        except Exception:
            cfg = {}
    guards = sorted(set(resolve_guard_paths(cfg, warn=False)) | set(_recorded_guards(rec)))
    now, then = resolve_rounds(cfg, warn=False), _recorded_caps(rec)
    caps = {k: max(now[k], then[k]) for k in now}
    if listable:
        can, beh, non, cause = tail_cert_delta_why(Path(project_path), rnd.get("snapshot"),
                                                   rnd.get("tree_state") or "")
    else:
        can, beh, non, cause = False, [], [], why_not or "the freshness verdict is not a plain STALE"
    changed = sorted(set(beh) | set(non))
    judge, why = judge_required(risk, can, beh, changed, guards, cause)
    try:
        rounds = impl_rounds(Path(task_dir))
        counted = True
    except RoundsUnreadable:
        rounds, counted = [], False
    cap, allowed, extended = allowed_rounds(risk, rounds, caps)
    return {"judge": judge, "why": why, "reached": counted and len(rounds) >= allowed, "rounds": len(rounds),
            "allowed": allowed, "cap": cap, "extended": extended, "changed": changed, "behavioral": beh,
            "can": can, "cause": cause, "guards": guards, "counted": counted, "risk": risk,
            "missing": names_nothing(Path(project_path), guards)}


def failed_state(risk: str, why: str, guards: "list[str] | None" = None) -> dict:
    """The state when the rule cannot be evaluated: the judge is required (every doubt fails closed). `guards`:
    the guard list already known — kept, so a later check that reads it still sees every listed file (the single
    judge, run 1: an untrusted ledger used to erase it)."""
    return {"judge": True, "why": why, "reached": False, "rounds": 0, "allowed": 0, "cap": 0, "extended": False,
            "changed": [], "behavioral": [], "can": False, "cause": why, "guards": list(guards or []),
            "counted": False, "risk": risk, "missing": []}


def post_panel_hold(task_dir: Path, key: str = "") -> str:
    """Why a close the stop rule sends to no judge must still wait, from the post-D6 runs after the newest impl
    panel (impl round 1, R6): the ledger is corrupt, the newest run has not finished, or the newest finished run
    COUNTED a Critical — grave, by the owner's definition; a FAIL with only Important findings does not hold it
    (his ruling (b) of 2026-10-13 16:30). "" when nothing holds it."""
    from tasks import post_d6
    td = Path(task_dir)
    if post_d6.ledger_corrupt_lines(td):
        return (f"the post-D6 run ledger ({post_d6.RUNS_NAME}) has corrupt lines — a run that counted a Critical "
                "could be hidden in them; repair it, or let the owner decide (--owner-ok).")
    key = key or post_d6.panel_key(post_d6.newest_impl_round(td))   # the reviewed round's, when the caller has it
    every = [r for r in post_d6.read_runs(td) if key and r.get("panel") == key and r.get("kind") != "panel"]
    if every and every[-1].get("status") != "done":
        return (f"the newest post-D6 run ({every[-1].get('id')}) has not finished — wait for it, or record it "
                "failed if its process is gone.")
    done = [r for r in every if r.get("status") == "done"]
    if done and int(done[-1].get("critical") or 0) > 0:
        return (f"the newest post-D6 run ({done[-1].get('id')}) counted a Critical — fix it and run the judge "
                "again (two runs at most), or let the owner decide (--owner-ok).")
    return ""


def _names(files: "list[str]", limit: int = 40) -> str:
    shown = ", ".join(files[:limit])
    return shown + (f" … (+{len(files) - limit} more)" if len(files) > limit else "")


def receipt_clause(st: dict) -> str:
    """The receipt's words for a close that took no judge after the last round D6 allows."""
    what = (f"({len(st['changed'])} file(s)): {_names(st['changed'])}" if st.get("can")
            else f"(the delta could not be listed: {st.get('cause') or 'unknown cause'})")
    return (f"stop rule (D6): {st['rounds']} of {st['allowed']} impl rounds used ({st['risk']}); no judge read after "
            f"the last panel {what}")


def block_hint(st: dict, task: str = "<N>") -> str:
    """The line a refused stale close adds: which exit the stop rule leaves (plan panel P5)."""
    if st["judge"]:
        return (f"  Stop rule (D6): {st['why']} — the single judge reads what changed since the panel:\n"
                f"  tasks impl-review {task}; after its PASS: tasks work done --stale-panel-ok --reason \"…\"\n"
                '  (or the owner: tasks work done --owner-ok --reason "…")')
    if st["reached"]:
        return (f"  Stop rule (D6): the impl rounds are used up ({st['rounds']} of {st['allowed']}, {st['risk']}) — "
                "a fresh impl panel needs --owner-ok; nothing changed needs a judge (no file under "
                f"`{GUARD_KEY}`), so: tasks work done --stale-panel-ok --reason \"…\" — the receipt names the files "
                "no judge read.")
    if not st["counted"]:
        return "  Stop rule (D6): the impl rounds could not be counted (judge.md / judge-archive.md unreadable)."
    return (f"  Stop rule (D6): impl round {st['rounds'] + 1} of {st['allowed']} is still allowed: "
            f"tasks panel-review {task} --mode impl")
