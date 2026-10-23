#!/usr/bin/env python3
"""Guard 0.5 v2 — annotated batch-close check (F1; blind-judge-reviewed).

Called by task-gate-hook on Edit/Write/MultiEdit to the ACTIVE task's task.md. Decides
whether the write's newly-checked gates are an allowed batch. stdin: the hook
payload JSON. Exit 0 = allow (stdout empty), exit 2 = block (stdout = the
message the hook relays to stderr). Any other failure = exit 1 and the hook
fails OPEN (a crashed guard must not brick the session).

The rules (design-1.5.5.md, F1 — including the judge's corrections):

  * three-way partition of checked lines in the NEW content (multiset):
    literally present checked in OLD = carried (ignored); else prefix-paired
    to an unchecked OLD original (longest original wins; empty originals
    excluded) = newly-checked, with the extension beyond the original as its
    annotation; else born-checked.
  * n = |newly-checked| + |born-checked|, on the whole file each edit leaves
    (task 291: an Edit too, matched as the Edit tool matches it; only an Edit
    the file cannot show is judged on its fragments). NEVER the raw x-count
    delta — unchecking in the same write must not launder n.
  * n ≤ 1 → allow silently (singles keep their old freedom).
  * 2 ≤ n ≤ 5 → allow ONLY if no born-checked line and every newly-checked
    gate's annotation has ≥ 8 unicode non-whitespace characters ("283 green"
    passes; "done" does not). The annotation is what the gate's line gained
    PLUS the lines of its note that the write adds (task 225, the owner's
    2026-10-16 "Liste scurte de la început": a gate's note goes on the lines
    under its title) — the note is the non-gate lines indented deeper than the
    gate, a blank line kept when the next line is deeper (`core.gate_note_ends`,
    the judges' trim's rule; a copy here, held equal by a test); a note line the
    old text already has adds nothing, one that only goes on from an old line
    adds what follows it — so a note written while the gate was open, or moved
    from another gate, never makes a bare tick an annotated one.
  * every checkbox line is a gate here, a fenced one too: the guard reads no
    code fences (task 225's impl round 1, grave: a fence rule looser than the
    counters', or read on an Edit FRAGMENT whose opener sits above it, hid bare
    ticks the counters still count). It can over-block — a fenced `- [x]`
    example inside a note reads as born-checked — never hide a tick.
  * n ≥ 6 → block even fully annotated (the whole-plan-in-one-write ceiling).
  * consecutive-batch guard: an allowed batch records the session's `tools`
    counter; the NEXT batch blocks unless at least one other tool call
    happened in between (kills the two-writes-of-5 end-of-task pattern).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

GATE_RE = re.compile(r"^[ \t]*- \[([ xX])\]\s*(.*)$")       # spaces or tabs only, as the gate counters (task 214, T6)
_SUBHEADING_RE = re.compile(r"^#{3,}(?:[ \t]|$)")             # a `###` heading ends a gate's note (the trim's rule)
ANNOTATION_FLOOR = 8   # unicode non-whitespace chars (never bash bytes)
# curly quotes read as straight ones — the Edit tool matches an old_string across them (task 291)
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
BATCH_CEILING = 5

BARE_MSG = """\
BLOCKED: {n} gates closed in one write, but {k} carry no outcome note.
Batch-closing ALREADY-DONE gates requires each gate to carry its own outcome
(>= {floor} non-space chars) written in the same write on the lines under the
gate, indented two spaces deeper, one fact per line — e.g. "  - 283 green" (a line
the file already has does not count).
Bare lines:
{lines}
Write each gate's outcome under it, or close them one at a time as you work."""

CEILING_MSG = """\
BLOCKED: {n} gates closed in one write — more than {ceiling} even with notes.
Close gates in smaller batches as the work actually completes; a whole plan
ticked at once is indistinguishable from backfill."""

BORN_MSG = """\
BLOCKED: this batch mints gate line(s) already checked (born-checked):
{lines}
Gates are added OPEN the moment you discover the work (`tasks guide`, Standing Orders),
then closed after the work is done — a line born [x] was never observable.
Most common cause: you REWROTE the gate's text while checking it. Keep the
original gate text byte-for-byte and write your outcome on the lines under it,
indented — e.g. "- [x] <original text>" then "  - 316 green"."""

CONSECUTIVE_MSG = """\
BLOCKED: second batch close with no tool call in between.
A batch attests ALREADY-DONE work; two back-to-back batch writes look like
end-of-task ticking. Do the next piece of work, then close its gates."""

UNFOUND_MSG = """\
BLOCKED: this edit ticks a gate, but the guard cannot find its old_string in task.md
(read as the Edit tool reads it: the text itself, curly and straight quotes alike,
\\uXXXX escapes as their characters). Re-read task.md and copy the gate's exact text."""


def _apply_as_the_tool(text: str, o: str, n: str, replace_all: bool) -> "str | None":
    """`text` after the Edit tool replaces `o` by `n`, or None when the guard cannot find `o` (task 291, its impl
    round 1, measured on the tool 2026-10-23): the text itself; else with curly and straight quotes read alike,
    replacing the file's own first spelling (one character for one, so the positions agree); else both with
    `\\uXXXX` escapes read as their characters. Replaced by nothing, a text takes the newline after it."""
    for oo, nn in ((o, n), (_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), o),
                            _ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), n))):
        if not oo:
            continue
        if oo not in text:
            i = text.translate(_QUOTES).find(oo.translate(_QUOTES))
            if i < 0:
                continue
            oo = text[i:i + len(oo)]
        if nn == "" and not oo.endswith("\n") and oo + "\n" in text:
            oo += "\n"
        return text.replace(oo, nn) if replace_all else text.replace(oo, nn, 1)
    return None


def _ticks(old: str, new: str) -> bool:
    """Whether going from `old` to `new` checks a box `old` did not have checked — a newly- or born-checked line."""
    newly, born = partition(old, new)
    return bool(newly or born)


def _read_untranslated(path) -> str:
    """The file as the gate counters read it (task 214, U4): no newline translation — a lone CR stays inside its line;
    CRLF read as LF. `Path.read_text`'s translation made a CR-delimited quote's checked boxes look like existing gates,
    so a Write of real ones passed as unchanged."""
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def open_gate_originals(old: str) -> "list[str]":
    """The open gates' texts in `old` — the born-checked hint's candidates — on physical lines (task 214)."""
    return [m.group(2).rstrip("\r") for line in old.split("\n")          # a CRLF line's `\r` is not text (T7)
            if (m := GATE_RE.match(line)) and m.group(1) == " " and m.group(2).strip()]


def gate_bodies(text: str) -> "tuple[list[str], list[str]]":
    """(checked_bodies, unchecked_bodies) — line-anchored, whitespace-normal."""
    checked: "list[str]" = []
    unchecked: "list[str]" = []
    for line in text.split("\n"):      # physical lines, as the gate counters (task 214): never splitlines()
        m = GATE_RE.match(line)
        if not m:
            continue
        body = m.group(2).strip()
        (checked if m.group(1) in "xX" else unchecked).append(body)
    return checked, unchecked


def _indent(s: str) -> int:
    return len(s.expandtabs(4)) - len(s.expandtabs(4).lstrip(" "))


def _note_lines(lines: "list[str]", k: int, gates: "set[int]") -> "list[str]":
    """The note under the gate at line k, stripped: the non-gate lines indented deeper than it, a blank line kept when
    the next non-blank line is deeper and not a gate, a `###` heading ending it — `core.gate_note_ends`' rule (the
    judges' trim), copied so this guard imports nothing it could fail on (`tests/test_note_under_title.py` holds them
    equal)."""
    ind = _indent(lines[k])
    out: "list[str]" = []
    j = k + 1
    while j < len(lines) and j not in gates:
        t = lines[j]
        if not t.strip():
            nxt = next((q for q in range(j + 1, len(lines)) if lines[q].strip()), None)
            if nxt is not None and nxt not in gates and _indent(lines[nxt]) > ind:
                j += 1
                continue
            break
        if _indent(t) > ind and not _SUBHEADING_RE.match(t):
            out.append(t.strip())
            j += 1
            continue
        break
    return out


def _gates(text: str) -> "tuple[list[str], set[int], list[tuple[int, bool, str]]]":
    """(lines, gate line indexes, [(index, checked, body)]) on physical lines (task 214) — never splitlines(); every
    checkbox line, a fenced one too (task 225, impl round 1: no fence rule, so none can hide a tick)."""
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    found = []
    for i, line in enumerate(lines):
        m = GATE_RE.match(line)
        if m:
            found.append((i, m.group(1) in "xX", m.group(2).strip()))
    return lines, {i for i, _c, _b in found}, found


_STEM_MIN = 3     # non-space characters an old line needs to be a stem a note line's gain is measured from (Q6)


def _added(notes: "list[list[str]]", base_lines: "list[str]") -> "list[list[str]]":
    """What each gate's note lines add over the old text's lines (task 225, P1; its impl round 1, Q2, Q6): a line the
    old text already has adds nothing; a line that only goes on from an old line (one of at least three non-space
    characters) adds what follows it; any other line adds itself. Nothing is consumed, so the answer is the same in
    any order and for every gate — a line the file already has never counts, however often it is written."""
    have = set(base_lines)
    stems = sorted({o for o in have if len(re.sub(r"\s", "", o)) >= _STEM_MIN}, key=len, reverse=True)
    out: "list[list[str]]" = []
    for note in notes:
        added = []
        for ln in note:
            if ln in have:
                continue
            stem = next((o for o in stems if ln.startswith(o)), None)
            added.append(ln[len(stem):] if stem else ln)
        out.append(added)
    return out


def partition(old: str, new: str):
    """Return (newly_checked [(body, annotation)], born_checked [body]). A newly checked gate's annotation is what its
    line gained plus what the note lines under it add over the old text's lines (task 225)."""
    new_lines, new_gates, new_found = _gates(new)
    old_lines, old_gates, old_found = _gates(old)

    carried = Counter(b for _i, c, b in old_found if c)
    originals = Counter(b for _i, c, b in old_found if not c and b)
    base = [ln.strip() for i, ln in enumerate(old_lines) if i not in old_gates and ln.strip()]
    ticked: "list[tuple[str, str, list[str]]]" = []           # (body, what its line gained, its note lines)
    born: "list[str]" = []
    for i, checked, body in new_found:
        if not checked:
            continue
        if carried[body] > 0:
            carried[body] -= 1
            continue
        candidates = [o for o, c in originals.items() if c > 0 and body.startswith(o)]
        if not candidates:
            born.append(body)
            continue
        orig = max(candidates, key=len)
        originals[orig] -= 1
        ticked.append((body, body[len(orig):], _note_lines(new_lines, i, new_gates)))
    gains = _added([note for _b, _g, note in ticked], base)
    newly = [(body, " ".join([gain] + added)) for (body, gain, _n), added in zip(ticked, gains)]
    return newly, born


def annotation_ok(extension: str) -> bool:
    return len(re.sub(r"\s", "", extension)) >= ANNOTATION_FLOOR


def _load_journal():
    """Load the shared enforcement-journal helper (sibling file). Fail-open."""
    try:
        import importlib.util
        path = Path(__file__).resolve().parent / "pb_journal.py"
        spec = importlib.util.spec_from_file_location("_pb_journal", str(path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def read_tools_counter(session_dir: Path) -> int:
    try:
        for line in (session_dir / "counters").read_text(encoding="utf-8").splitlines():
            if line.startswith("tools="):
                return int(line.split("=", 1)[1].strip() or 0)
    except (OSError, ValueError):
        pass
    return 0


def main() -> int:
    # This is an ENFORCING guard. Robustness: stdin/stdout can run a codec that is
    # not UTF-8 (PYTHONIOENCODING, a Latin-1 locale; first met as a cp1252
    # console), which cannot encode the "→" in BARE_MSG (U+2192) — so
    # `print(BARE_MSG…)` raised UnicodeEncodeError, the top-level except swallowed
    # it as exit 1, and the hook treats any non-2 exit as ALLOW: every bare batch
    # close fail-opened there. (Messages carrying only "—" happened to
    # encode as cp1252 0x97 and fired, which is why annotated/born-checked cases
    # blocked while bare ones slipped through — the tell in the CI artifact.)
    # Force UTF-8 so the block message always encodes and the guard always fires.
    # No-op on POSIX, where the streams are already UTF-8. Matches
    # hook-payload-normalize.py and tasks/cli.py.
    for _stream in (sys.stdin, sys.stdout):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    args = sys.argv[1:]

    def opt(name: str) -> "str | None":
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None

    tool = opt("--tool") or ""
    file_path = opt("--file") or ""
    sd_raw = opt("--session-dir")
    session_dir = Path(sd_raw or ".")

    def _journal_block(reason: str) -> None:
        # Best-effort enforcement-journal (log-only). Only when a real
        # --session-dir was given (its parent's parent IS the resolved lane
        # dir: .agent[/<user>]); never guess a lane from cwd. Wrapped AND the
        # helper swallows errors — journalling can never change the block.
        if not sd_raw:
            return
        try:
            j = _load_journal()
            if j is not None:
                j.append(session_dir.parent.parent, "batch-close", "block", reason,
                         session_id=session_dir.name, tool=tool, path=file_path)
        except Exception:
            pass

    d = json.load(sys.stdin)
    ti = d.get("tool_input", {}) or {}

    if tool == "Edit":
        # Judged on the whole file it leaves, as a Write and a MultiEdit are (task 291, audit 1 G1: `replace_all` of
        # `- [ ]` → `- [x]` is one empty gate as a fragment, and a fragment from mid-line hides a tick — item 9). Only
        # an unreadable file's edit is judged on its fragments, as before; one the guard cannot find is refused below.
        o = str(ti.get("old_string", ""))
        n_ = str(ti.get("new_string", ""))
        try:
            whole = _read_untranslated(file_path)
        except OSError:
            whole = None
        after = None if whole is None else _apply_as_the_tool(whole, o, n_, bool(ti.get("replace_all", False)))
        if whole is not None and after is not None:
            old, new = whole, after
        elif whole is not None and o and _ticks(o, n_):
            # readable, and the guard cannot find the text: the tool fails the edit, or matches it by a rule this does
            # not model (impl round 2, opus) — a tick there is refused, never judged on its fragment
            print(UNFOUND_MSG)
            _journal_block("unfound-old-string")
            return 2
        else:
            old, new = o, n_
    elif tool == "Write":
        new = str(ti.get("content", ""))
        try:
            old = _read_untranslated(file_path)
        except OSError:
            old = ""
    elif tool == "MultiEdit":
        # Judged like a Write (task 166): the file as it is on disk against the
        # file as the edits, applied in order, leave it — so a gate ticked across
        # two edits of one call, or rewritten by one edit and ticked by the next,
        # is seen as what it is. An edit whose `old_string` is absent changes
        # nothing here (the tool itself fails it); a payload that is not a list
        # of edits leaves the text as it was, and the guard passes it — its own
        # errors never block an edit. Each edit is matched as the Edit tool matches it (task 291, impl round 1, opus).
        try:
            old = _read_untranslated(file_path)
        except OSError:
            old = ""
        new = old
        edits = ti.get("edits")
        for e in edits if isinstance(edits, list) else ():
            if not isinstance(e, dict):
                continue
            o, n = str(e.get("old_string", "")), str(e.get("new_string", ""))
            if not o:
                if not new:                 # the first edit of a new file is its content
                    new = n
                continue
            after = _apply_as_the_tool(new, o, n, bool(e.get("replace_all")))
            if after is not None:
                new = after
            elif _ticks(o, n):                          # as for an Edit: a tick the guard cannot place
                print(UNFOUND_MSG)
                _journal_block("unfound-old-string")
                return 2
    else:
        return 0

    newly, born = partition(old, new)

    n = len(newly) + len(born)
    if n <= 1:
        return 0

    if born:
        # F20 (batch-6 journal one-change): a born-checked line is usually a
        # near-miss — the agent rewrote or truncated the gate's text while
        # checking it, then "had to eyeball what I'd dropped". Show the closest
        # OPEN original under each offending line so restoration is copy-paste,
        # not memory. No near-miss → no hint (a fabricated gate must not be
        # blamed on some unrelated open gate).
        import difflib
        open_originals = open_gate_originals(old)
        rendered = []
        for b in born:
            rendered.append(f"  - [x] {b}")
            close = difflib.get_close_matches(b, open_originals, n=1, cutoff=0.4)
            if close:
                rendered.append("      closest open gate (restore byte-for-byte, "
                                f"then write your outcome under it):\n      - [ ] {close[0]}")
        print(BORN_MSG.format(lines="\n".join(rendered)))
        _journal_block("born-checked")
        return 2

    if n > BATCH_CEILING:
        print(CEILING_MSG.format(n=n, ceiling=BATCH_CEILING))
        _journal_block("batch-ceiling")
        return 2

    bare = [(body, ext) for body, ext in newly if not annotation_ok(ext)]
    if bare:
        print(BARE_MSG.format(
            n=n, k=len(bare), floor=ANNOTATION_FLOOR,
            lines="\n".join(f"  - [x] {b}" for b, _ in bare)))
        _journal_block("bare-batch")
        return 2

    # Allowed batch — consecutive-batch guard (judge Finding 2).
    marker = session_dir / "last_batch_close"
    tools_now = read_tools_counter(session_dir)
    try:
        recorded = int(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        recorded = None
    if recorded is not None and 0 <= tools_now - recorded <= 1:
        # Only the previous batch edit itself (or nothing) ran since the last
        # allowed batch: end-of-task ticking pattern. Marker NOT updated — a
        # real tool call is the only way forward.
        print(CONSECUTIVE_MSG)
        _journal_block("consecutive-batch")
        return 2
    try:
        marker.write_text(str(tools_now), encoding="utf-8")
    except OSError:
        pass  # advisory persistence; never block on it
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Fail OPEN: a crashed guard must not brick the session. The hook
        # treats any exit other than 2 as allow.
        sys.exit(1)
