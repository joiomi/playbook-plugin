"""The review round cap (task 209; task 343 left it the panel's side; task 344 made it one number).

DESIGN-PLAYBOOK-2.md §3.3 point 12, approved by the owner (R17): before the close a task gets at most 2 impl panel
rounds, whatever its risk and whatever the rounds counted; a third only with `tasks panel-review <N> --mode impl
--owner-ok --reason "…"`. (Task 344, PLAN-PLAYBOOK-2.md 4.1 a part 2: the caps per `## Risk`, the round more after a
Critical and the project's key for them are gone. Task 343: the close reads only `panel_record`'s risk and the cap's
state.)

A round is an impl panel round that met its quorum (`PANEL VERDICT: PASS` in its header) in judge.md or
judge-archive.md; a below-quorum round and a plan round are not rounds. A Critical is a `**Critical**` finding
marker (`_FINDING_RE`, matched after one leading `> ` is stripped) in a judge's text — after the round's
first `═` bar, and in a round 1.6.2 wrote (format 2) only on a quoted line, the judges' lines being the quoted
ones — that is not tagged `[SETTLED]`; `round_findings` counts them for the run ledger."""
import os
import re
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


# At most this many impl panel rounds before the close (DESIGN §3.3 point 12); past it, the owner's word.
ROUND_CAP = 2


class RoundsUnreadable(Exception):
    """judge.md or judge-archive.md exists but cannot be read: the rounds cannot be counted."""


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
    counted as written — the run ledger's measures (task 232)."""
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


def panel_decision(rounds: "list[dict]", task: str = "<N>") -> "tuple[bool, str]":
    """(allowed, refusal text) for one more impl panel round: allowed while fewer than `ROUND_CAP` rounds met their
    quorum — no risk is asked and no Critical extends it (task 344)."""
    n = len(rounds)
    if n < ROUND_CAP:
        return True, ""
    return False, (
        f"Round cap: task {task} has had {n} impl panel round(s); at most {ROUND_CAP} come before the close. "
        "Nothing was spent.\n"
        "  Close on the last round (a change after it closes with `tasks work done --force --reason \"…\"`), or let "
        "the owner decide:\n"
        f'  tasks panel-review {task} --mode impl --owner-ok --reason "…"')


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
    what the reviewed one recorded): the stricter risk of them all. None when no reservation recorded a risk (panels
    from before task 209). Any other field of a reservation is not read (task 344, plan panel P4). A ledger that
    cannot be trusted is `ledger_untrusted`'s to say (the close then takes `unclassified` — task 343, P17)."""
    from tasks import review_ledger
    recs = [r for r in review_ledger.read_runs(Path(task_dir))
            if r.get("kind") == "panel" and r.get("mode") in (None, "impl") and "risk" in r]
    if not recs:
        return None
    risks = [r.get("risk") for r in recs if isinstance(r.get("risk"), str) and r.get("risk")]
    risk = None
    for x in risks:
        risk = stricter(risk, x) if risk else x
    return {"risk": risk}


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


