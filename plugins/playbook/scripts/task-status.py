#!/usr/bin/env python3
"""task-status.py [--fields] <task.md> — read a task's state the way the CLI reads it.

The enforcing hooks' ONE source of truth for task.md state (V7 / P-F, task 043;
gates + F3, task 055). The stop-hook used to mirror `tasks.core._extract_status`
in awk and count gates with grep — both fence-blind, so a `## Status`/`blocked`
pair or a `- [ ] Freehand…` line quoted inside a code fence (a documentation
example, or a decoy) released unchecked gates; task-gate-hook's F3 done-check
had the same awk. Re-implementing the shared CommonMark fence engine in awk/grep
(bash 3.2 / gawk / mawk / BSD grep) would leave two implementations that drift;
instead the hooks call this script, which imports the SAME readers the lifecycle
uses, so Python and the enforcing hooks cannot disagree about the same file.

  task-status.py <task.md>           one line: the status (`pending`, `blocked`,
                                     `done (…)`, `unknown` when no live `## Status`)
  task-status.py --fields <task.md>  five lines: status, live unchecked gate
                                     count, first live unchecked gate text (empty
                                     when none), `release`/`hold` — whether the
                                     stop-hook's Freehand arm may waive the block
                                     (`tasks.core._freehand_release_allowed`: the
                                     ONE rule, decided here, never re-derived from
                                     the gate text in bash) — and the suspend
                                     sample (below) — one spawn for everything the
                                     stop-hook needs
  task-status.py --section <task.md> two lines: the first live open gate's `##`
                                     section and the suspend sample — the state
                                     echo's read on a failed call (task 234)
  task-status.py --echo <task.md> [<P> <B> <OPENS> <CHECKS> <ABOVE> <T>]
                                     fifteen lines for the state echo (tasks 219,
                                     220): total, checked, the first live open
                                     gate's line, its position among the live gates
                                     (-1 none), the checked live gates below it, the
                                     transition from the gate the echo last saw
                                     (position P, B checked below it, OPENS open
                                     copies of its text T, CHECKS checked gates that
                                     are T with one span inserted, ABOVE of them
                                     above T's first open copy): `none` (no
                                     previous), `same`, `skip` (the same open gate,
                                     a gate below it checked), `done` / `done-bare`
                                     (T's open copies fewer AND its checked matches
                                     more, above a copy still open — with a note /
                                     exactly T), `moved` (T is
                                     still open: a gate added, moved or unticked
                                     above), `reworded` (T is gone); the checked
                                     line (done only); the first open gate's full
                                     text, its first 200 and 60 characters, and that
                                     text's OPENS, CHECKS and ABOVE for the next call
                                     (thirteen, `_echo_fields`); then (task 234) the
                                     first open gate's `##` section and the suspend
                                     sample

The section (task 234) is the nearest live `## ` heading above the first live open
gate — read with the gates' own fence flags and `core._atx_h2_text` — without the
`## `, control characters and U+0085 / U+2028 / U+2029 as spaces, spaces folded,
cut at 64 bytes on a whole character; empty when no gate is open or no heading is
above it. The suspend sample is one line `<susp> <sts> <boot>` (pb_journal's
`suspend_sample`): the whole seconds the machine had slept since boot, the wall
time of that reading and the boot id's first 8 characters, `-` for one that could
not be read. Each extra is computed apart: a fault there prints `` / `- - -` and
never fails the lines before it.

Exits 0 on success. Any failure (missing file, import error) exits non-zero with a
one-line diagnostic on stderr; the hooks treat that as UNREADABLE state and fail
CLOSED (open gates enforced, no Freehand release) — loud, never a guess.

Stdlib-only; the plugin root (this file's parent's parent) is put on sys.path.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _span_match(t: str, s: str) -> bool:
    """`s` is `t` with ONE span inserted anywhere (none at all included): a note after it, before it or inside it.
    True when the common prefix and the common suffix of the two cover the whole of `t`."""
    if len(s) < len(t):
        return False
    p = 0
    while p < len(t) and s[p] == t[p]:
        p += 1
    q = 0
    while q < len(t) and s[len(s) - 1 - q] == t[len(t) - 1 - q]:
        q += 1
    return p + q >= len(t)


def _echo_fields(lines: "list[str]", prev: "tuple[int, int, int, int, int, str] | None") -> "list[str]":
    """`--echo`'s thirteen fields (see the module docstring). A gate is a live (unfenced) `- [ ]` / `- [x]` line, read
    the way `tasks.core._live_gate_scan` reads it; the previous gate is known by its text and how many copies of it
    were open and checked — never by its line — so lines added above it or a fenced checklist change nothing.

    The tick (task 220, after task 219's two reviews found position rules both missing and inventing ticks): the
    previous gate T was ticked when its open copies are FEWER than before AND the checked gates that are T with one
    span inserted (a note after, before or inside it) are MORE than before — and, while a copy of T is still open,
    more of them ABOVE that first open copy. The tick named is that match at the previous place P, else the one
    nearest to it; bare when it is exactly T and no note sits under it (task 225). A note written under T while it was
    still open counts too: the echo keeps no copy of the file before the edit — a stated limit, the batch-tick guard
    (which has both texts) is the strict check (its plan panel, P5)."""
    from tasks.core import _GATE_LINE_RE, _iter_fenced_flags
    fenced = _iter_fenced_flags(lines, unclosed_is_live=True, track_indented_code=False)
    gates: "list[tuple[int, bool, str]]" = []          # (line, checked, text)
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        raw = line.lstrip("\ufeff").rstrip("\r\n")
        m = _GATE_LINE_RE.match(raw)
        if m:
            # a lone CR inside the text (not a line break, task 214) never reaches the stored copy, where the gates are
            # read (task 220, sonnet: the stored copy never matched) — task 224: trimmed of spaces and tabs only, as core
            # reads it, and an inner CR kept VISIBLE as the two characters `\r` (deleting it made `\rFreehand …` read
            # `Freehand …`, so the state-echo hook's `case` went into Freehand mode while the stop hook held — impl r1).
            # A literal backslash-r in a gate then reads the same as a CR: neither can start with `Freehand`, and the
            # tick compares copies made the same way (impl round 2, opus, sonnet)
            gates.append((i + 1, m.group(1) != " ", raw[m.end():].strip(" \t").replace("\r", "\\r")))
    first = next((k for k, g in enumerate(gates) if not g[1]), -1)
    below = sum(1 for g in gates[first + 1:] if g[1]) if first >= 0 else 0
    text = gates[first][2] if first >= 0 else ""

    def _has_note_under(idx: int) -> bool:
        from tasks.core import gate_note_ends
        texts = [ln.rstrip("\r\n") for ln in lines]
        end = gate_note_ends(texts, {g[0] - 1 for g in gates})[idx]
        return any(t.strip() for t in texts[idx + 1:end])

    def opens_of(t: str) -> int:
        return sum(1 for g in gates if not g[1] and g[2] == t)

    def matches_of(t: str) -> "list[int]":
        return [k for k, g in enumerate(gates) if g[1] and _span_match(t, g[2])]

    def above_of(t: str) -> int:
        """the checked matches of t above its first open copy (all of them when it has none)"""
        fo = next((k for k, g in enumerate(gates) if not g[1] and g[2] == t), None)
        return sum(1 for k in matches_of(t) if fo is None or k < fo)

    transition, ticked = "none", ""
    if prev is not None:
        pos, prev_below, prev_opens, prev_checks, prev_above, prev_text = prev
        matches = matches_of(prev_text)
        # when a copy of T is still open, the new match must sit ABOVE it — a later copy ticked out of order is a skip
        # (task 220, impl round 1: opus, codex)
        still_open = opens_of(prev_text) > 0
        if (opens_of(prev_text) < prev_opens and len(matches) > prev_checks
                and (not still_open or above_of(prev_text) > prev_above)):
            k = pos if pos in matches else min(matches, key=lambda m: (abs(m - pos), m))
            # bare only when nothing was written: not after the title, and no note under it (task 225 — a gate's note
            # goes on the lines under its title; `core.gate_note_ends`, the judges' trim's rule)
            transition = "done-bare" if gates[k][2] == prev_text and not _has_note_under(gates[k][0] - 1) else "done"
            ticked = str(gates[k][0])
        elif first >= 0 and text == prev_text:
            # a skip: a gate BELOW the open one was checked — counted below it, so it holds at any position
            transition = "skip" if below > prev_below else "same"
        elif opens_of(prev_text) > 0:
            transition = "moved"
        else:
            transition = "reworded"
    # The display cuts count characters, not bytes (bash `cut -c` split a multibyte character)
    return [str(len(gates)), str(sum(1 for g in gates if g[1])), str(gates[first][0]) if first >= 0 else "",
            str(first), str(below), transition, ticked, text.replace("\r", ""), text[:200].replace("\r", ""),
            text[:60].replace("\r", ""), str(opens_of(text) if first >= 0 else 0),
            str(len(matches_of(text)) if first >= 0 else 0), str(above_of(text) if first >= 0 else 0)]


_SECTION_MAX = 64          # bytes; JSON-escaped, at most twice that — the hook journal's 128 for `section`


def _section_text(title: str) -> str:
    """A heading's title as the hook journal keeps it: control characters (C0, DEL, C1 — U+0085 among them) and
    U+2028 / U+2029 as spaces, spaces folded, at most 64 bytes cut on a whole character."""
    t = "".join(" " if (c < " " or "\x7f" <= c <= "\x9f" or c in "\u2028\u2029") else c for c in title)
    b = " ".join(t.split()).encode("utf-8", "replace")
    return b[:_SECTION_MAX].decode("utf-8", "ignore").rstrip(" ")


def _head_section(lines: "list[str]") -> str:
    """The `##` section the first live open gate sits in (task 234): the nearest live H2 above it, read with the same
    fence flags `_echo_fields` reads the gates with (plan panel, sonnet) — "" when no gate is open or none is above."""
    from tasks.core import _GATE_LINE_RE, _atx_h2_text, _iter_fenced_flags
    fenced = _iter_fenced_flags(lines, unclosed_is_live=True, track_indented_code=False)
    first = next((i for i, line in enumerate(lines) if not fenced[i]
                  and (m := _GATE_LINE_RE.match(line.lstrip("\ufeff").rstrip("\r\n"))) and m.group(1) == " "), None)
    if first is None:
        return ""
    for i in range(first - 1, -1, -1):
        if not fenced[i]:
            h = _atx_h2_text(lines[i])
            if h:
                return _section_text(h[3:])
    return ""


def _sample_line() -> str:
    """`<susp> <sts> <boot>`, `-` for a value that could not be read (an empty first word would shift the hook's
    `read`). pb_journal sits beside this file; under `-I` its directory is not on the path, so it is added LAST."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.append(here)
    from pb_journal import suspend_sample
    susp, sts, boot = suspend_sample()
    return f"{'-' if susp is None else susp} {'-' if sts is None else sts} {boot or '-'}"


def _extras(lines: "list[str]", section: bool = True) -> "list[str]":
    """Task 234's lines after a mode's own, each in its own `try` (plan panel, grok: never fail what comes before)."""
    out = []
    if section:
        try:
            out.append(_head_section(lines))
        except Exception:  # noqa: BLE001
            out.append("")
    try:
        out.append(_sample_line())
    except Exception:  # noqa: BLE001
        out.append("- - -")
    return out


def main(argv: "list[str]") -> int:
    fields = echo = section_only = False
    prev: "tuple[int, int, int, int, int, str] | None" = None
    args = argv[1:]
    if args and args[0] == "--fields":
        fields = True
        args = args[1:]
    elif args and args[0] == "--section":
        section_only = True
        args = args[1:]
    elif args and args[0] == "--echo":
        echo = True
        args = args[1:]
        if len(args) == 7:
            try:
                prev = (int(args[1]), int(args[2]), int(args[3]), int(args[4]), int(args[5]), args[6])
            except ValueError:
                print("task-status: --echo's <P>, <B>, <OPENS>, <CHECKS> and <ABOVE> must be integers", file=sys.stderr)
                return 2
            args = args[:1]
    if len(args) != 1:
        print("usage: task-status.py [--fields | --section | --echo] <task.md> [<P> <B> <OPENS> <CHECKS> <ABOVE> <T>]",
              file=sys.stderr)
        return 2
    task_file = Path(args[0])
    if not task_file.is_file():
        print(f"task-status: not a file: {task_file}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from tasks.core import (_freehand_release_allowed, _live_gate_state,
                                _physical_lines, _status_from_lines, read_task_text)
    except Exception as exc:  # noqa: BLE001 — any import failure is "unreadable"
        print(f"task-status: cannot import tasks.core: {exc}", file=sys.stderr)
        return 1
    # ONE read: every field below derives from this single snapshot, so a
    # concurrent atomic rewrite can never pair an old status with the new file's
    # gates (round-2 panel). Physical `\n` lines only (never splitlines():
    # U+0085/U+2028 must not mint a line the hook's grep fallback cannot see, nor
    # a fake `## Status` pair).
    try:
        lines = _physical_lines(read_task_text(task_file))      # a lone CR is not a line break (task 214, T4)
    except Exception as exc:  # noqa: BLE001 — unreadable file → non-zero, hook fails closed
        print(f"task-status: could not read the task file: {exc}", file=sys.stderr)
        return 1
    if echo:
        try:
            out = _echo_fields(lines, prev)
        except Exception as exc:  # noqa: BLE001 — any failure is "unreadable": the hook echoes as it can
            print(f"task-status: --echo failed: {exc}", file=sys.stderr)
            return 1
        out += _extras(lines)
        sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8", "replace"))
        sys.stdout.buffer.flush()
        return 0
    if section_only:
        sys.stdout.buffer.write(("\n".join(_extras(lines)) + "\n").encode("utf-8", "replace"))
        sys.stdout.buffer.flush()
        return 0
    out = [_status_from_lines(lines)]
    if fields:
        unchecked, _total, first = _live_gate_state(lines)
        # The gate text is ONE physical line, so it cannot carry `\n`; any other
        # separator char stays inside the field, and a stray CR is shown as the two
        # characters `\r` (task 224, impl round 2: deleted, `\rFreehand now` showed as
        # `Freehand now` in the stop hook's message while the release line said `hold`)
        # so the third field is exactly one line for the hook's `read`.
        out += [str(unchecked), first.replace("\r", "\\r"),
                "release" if _freehand_release_allowed(lines) else "hold"]
        out += _extras(lines, section=False)
    # Bytes, `\n` only: a text-mode print() may translate the newline to CRLF, and
    # bash command substitution keeps the CR, so `blocked\r` would miss the hook's
    # exact match (round-5 panel). The hook also strips one trailing CR — belt + braces.
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
