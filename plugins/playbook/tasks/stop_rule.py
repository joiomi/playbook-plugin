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


def guarded(path: str, patterns: "list[str]") -> bool:
    """Whether a project-relative path (a `code_roots` scope's prefixed with its name, as the close's delta names
    it) is under one of the patterns: the path itself, anything below it, or an `fnmatch` glob (`*` crosses `/`)."""
    for pat in patterns:
        stem = pat.rstrip("/")
        if not stem:
            continue
        if path == stem or path.startswith(stem + "/") or fnmatch.fnmatchcase(path, pat):
            return True
    return False


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
    extended = len(rounds) >= cap and round_criticals(rounds[cap - 1]) > 0
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
                   cause: str = "", panel_regime: bool = True) -> "tuple[bool, str]":
    """After an impl panel: does what changed since it need the single judge before a stale close? (required, why).
    `behavioral` = the code paths of the delta, `changed` = every path of it (code, tests, docs, records);
    `panel_regime` = the project requires a panel for this risk (`panel_required_for`) — the automatic judge on an
    irreversible or unclassified task's code is that regime's; a listed guard file needs it on any task. A delta
    that cannot be listed requires the judge wherever the answer would depend on it."""
    high = panel_regime and risk not in ("reversible", "assertive")
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
    """The reservation of the task's newest impl round — its `**Round-id:**` is the reservation's id (task 149) —
    as the run ledger merges it, or None (a round from before, a ledger without it)."""
    from tasks import post_d6
    rid = (post_d6.newest_impl_round(Path(task_dir)) or {}).get("round_id") or ""
    if not rid:
        return None
    for r in post_d6.read_runs(Path(task_dir)):
        if r.get("id") == rid and r.get("kind") == "panel":
            return r
    return None


def _recorded_caps(rec: "dict | None") -> "dict[str, int]":
    raw = (rec or {}).get("rounds_cap")
    return resolve_rounds({ROUNDS_KEY: raw} if isinstance(raw, dict) else {}, warn=False)


def _recorded_guards(rec: "dict | None") -> "list[str]":
    raw = (rec or {}).get("guard_paths")
    return [x.strip() for x in raw if isinstance(x, str) and x.strip()] if isinstance(raw, list) else []


def close_state(project_path: Path, task_dir: Path, risk: str, rnd: dict, rec: "dict | None",
                cfg: "dict | None" = None, panel_regime: bool = True) -> dict:
    """What a STALE close of the carrying impl round `rnd` needs (the caller decides the verdict). `risk` is the
    close's gate risk (`stricter` of the recorded and the current one). The guard list is the union of the one the
    panel's reservation recorded and today's; the caps the larger of the two per risk — below the cap a project's
    own binding still applies, so a larger cap is never the easier close. Keys: judge, why, reached, rounds,
    allowed, cap, extended, changed, behavioral, can, cause, guards, counted (False when the rounds could not be
    read: then never `reached`)."""
    from tasks.core import load_config, tail_cert_delta_why
    if cfg is None:
        try:
            cfg = load_config(Path(project_path))
        except Exception:
            cfg = {}
    guards = sorted(set(resolve_guard_paths(cfg, warn=False)) | set(_recorded_guards(rec)))
    now, then = resolve_rounds(cfg, warn=False), _recorded_caps(rec)
    caps = {k: max(now[k], then[k]) for k in now}
    can, beh, non, cause = tail_cert_delta_why(Path(project_path), rnd.get("snapshot"), rnd.get("tree_state") or "")
    changed = sorted(set(beh) | set(non))
    judge, why = judge_required(risk, can, beh, changed, guards, cause, panel_regime)
    try:
        rounds = impl_rounds(Path(task_dir))
        counted = True
    except RoundsUnreadable:
        rounds, counted = [], False
    cap, allowed, extended = allowed_rounds(risk, rounds, caps)
    return {"judge": judge, "why": why, "reached": counted and len(rounds) >= allowed, "rounds": len(rounds),
            "allowed": allowed, "cap": cap, "extended": extended, "changed": changed, "behavioral": beh,
            "can": can, "cause": cause, "guards": guards, "counted": counted, "risk": risk}


def failed_state(risk: str, why: str) -> dict:
    """The state when the rule cannot be evaluated: the judge is required (every doubt fails closed)."""
    return {"judge": True, "why": why, "reached": False, "rounds": 0, "allowed": 0, "cap": 0, "extended": False,
            "changed": [], "behavioral": [], "can": False, "cause": why, "guards": [], "counted": False,
            "risk": risk}


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
