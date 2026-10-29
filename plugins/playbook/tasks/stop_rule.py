"""The review stop rule's round cap (PHASE-B.md item 1, task 209; task 343 left it the panel's side).

The owner's rule D6 (2026-09-25), made the plugin's by his answers of 2026-10-15: impl panel rounds are capped by the
task's `## Risk` — reversible 1, assertive 2, irreversible 2 (an unset risk takes the larger of the two high caps); one
round more only when the cap's last round counted a Critical; past that only `tasks panel-review <N> --mode impl
--owner-ok --reason "…"`. A project changes the caps with `.agent/config.json` `review_rounds`. (Task 343,
PLAN-PLAYBOOK-2.md 4.1 a: the close side — the post-panel judge after the last round, its guard list and its
opt-in key — is gone; the close reads only `panel_record`'s risk and the cap's state.)

A round is an impl panel round that met its quorum (`PANEL VERDICT: PASS` in its header) in judge.md or
judge-archive.md; a below-quorum round and a plan round are not rounds. A Critical is a `**Critical**` finding
marker (`_FINDING_RE`, matched after one leading `> ` is stripped) in a judge's text — after the round's
first `═` bar, and in a round 1.6.2 wrote (format 2) only on a quoted line, the judges' lines being the quoted
ones — that is not tagged `[SETTLED]`.
LIMIT (as task 108's run cap): D6 says a Critical that was ACCEPTED; the CLI sees one a judge wrote."""
import os
import re
import sys
from pathlib import Path

# A finding starts at the line start or right after a sentence end on the same
# line (grok glues its preamble to the first finding: "…constatările.**Important**").
_FINDING_RE = re.compile(
    r"(?:^|(?<=[.!?:)]))[ \t]*(?:[-*+]\s+|\d+[.)]\s+)?\*\*\s*\[?\s*(Critical|Important)\b",
    re.IGNORECASE)


# The tag counts only RIGHT AFTER the severity marker (inside or just after the
# bold), never a literal "[SETTLED]" later in the finding's prose — a `[file: …]`
# field may stand between the two (task 201: judges reorder fixed markers).
_TAG_AFTER_RE = re.compile(
    r"\]?\s*(?:\*\*)?\s*(?:\[file:[^\]\n]*\]\s*(?:\*\*)?\s*)?"
    r"\[(SETTLED-CONTRADICTED|SETTLED|PRE-EXISTING)\]", re.IGNORECASE)

ROUNDS_KEY = "review_rounds"
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


def round_findings(rnd: dict) -> "dict[str, int]":
    """The Critical and Important findings the judges of one panel round wrote (not `[SETTLED]`), each judge's line
    counted as written — the stop rule's count (`round_criticals`) and the run ledger's (task 232) from one reading."""
    counts = {"critical": 0, "important": 0}
    body = rnd.get("body") or ""
    bar = _BAR.search(body)
    if not bar:
        return counts
    for ln in body[bar.start():].splitlines():
        quoted = ln.startswith(">")
        if rnd.get("format") == 2 and not quoted:
            continue                      # 1.6.2 quotes every line a judge wrote: the rest is the writer's
        s = _QUOTE.sub("", ln)
        for m in _FINDING_RE.finditer(s):
            tag = _TAG_AFTER_RE.match(s, m.end())
            if tag and tag.group(1).upper() == "SETTLED":
                continue
            counts[m.group(1).lower()] += 1
    return counts


def round_criticals(rnd: dict) -> int:
    """The Critical findings the judges of one panel round wrote (not `[SETTLED]`)."""
    return round_findings(rnd)["critical"]


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
        "  Close on the last round (a change after it closes with `tasks work done --force --reason \"…\"`), or let "
        "the owner decide:\n"
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


# ── what the close reads (W3) ────────────────────────────────────────────────

def panel_record(task_dir: Path) -> "dict | None":
    """What the task's impl panels ran under, from EVERY impl panel reservation in the run ledger (task 209's impl
    round 2, S2: the newest one alone let a below-quorum round, or a second panel after `## Risk` was lowered, replace
    what the reviewed one recorded): the stricter risk of them all and the larger cap per risk. None when no
    reservation recorded either (panels from before task 209). A ledger that cannot be trusted is `ledger_untrusted`'s
    to say (the close then takes `unclassified` — task 343, plan panel P17)."""
    from tasks import review_ledger
    recs = [r for r in review_ledger.read_runs(Path(task_dir))
            if r.get("kind") == "panel" and r.get("mode") in (None, "impl")
            and any(k in r for k in ("risk", "rounds_cap"))]
    if not recs:
        return None
    risks = [r.get("risk") for r in recs if isinstance(r.get("risk"), str) and r.get("risk")]
    risk = None
    for x in risks:
        risk = stricter(risk, x) if risk else x
    caps = dict(DEFAULT_ROUNDS)
    seen = [_recorded_caps(r) for r in recs if isinstance(r.get("rounds_cap"), dict)]
    if seen:
        caps = {k: max(c[k] for c in seen) for k in DEFAULT_ROUNDS}
    return {"risk": risk, "rounds_cap": caps if seen else None}


def ledger_untrusted(task_dir: Path) -> str:
    """Why the run ledger cannot be trusted as the record of what the panels ran under (impl round 3, T3: a read
    error made `read_runs` return nothing, so the recorded guard list and risk vanished): it exists but cannot be
    read, or has corrupt lines. "" for an absent or clean ledger."""
    from tasks import review_ledger
    p = Path(task_dir) / review_ledger.RUNS_NAME
    if not os.path.lexists(p):
        return ""
    try:
        with open(p, encoding="utf-8") as fh:
            fh.read()
    except (OSError, UnicodeDecodeError) as e:
        return f"the run ledger {review_ledger.RUNS_NAME} cannot be read ({e.__class__.__name__})"
    bad = review_ledger.ledger_corrupt_lines(Path(task_dir))
    if bad:
        return f"the run ledger {review_ledger.RUNS_NAME} has {bad} corrupt line(s)"
    return ""


def _recorded_caps(rec: "dict | None") -> "dict[str, int]":
    raw = (rec or {}).get("rounds_cap")
    return resolve_rounds({ROUNDS_KEY: raw} if isinstance(raw, dict) else {}, warn=False)


