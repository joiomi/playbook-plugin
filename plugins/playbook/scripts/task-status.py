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
  task-status.py --fields <task.md>  four lines: status, live unchecked gate
                                     count, first live unchecked gate text (empty
                                     when none), and `release`/`hold` — whether the
                                     stop-hook's Freehand arm may waive the block
                                     (`tasks.core._freehand_release_allowed`: the
                                     ONE rule, decided here, never re-derived from
                                     the gate text in bash) — one spawn for
                                     everything the stop-hook needs
  task-status.py --echo <task.md> [<P> <B> <T>]
                                     eight lines for the state echo (task 219):
                                     total, checked, the first live open gate's
                                     line, its position among the live gates (-1
                                     none), the checked live gates below it, the
                                     transition from the gate the echo last saw
                                     (position P, B checked below it, text T):
                                     `none` (no previous), `same`, `skip` (the same
                                     open gate, a gate below it checked), `done` /
                                     `done-bare` (the previous gate's own line is
                                     checked — with a note / exactly its text),
                                     `moved` (it is still open elsewhere: a gate was
                                     added, moved or unticked above), `reworded`
                                     (it is gone); the checked line (done only);
                                     and the first open gate's full text

Exits 0 on success. Any failure (missing file, import error) exits non-zero with a
one-line diagnostic on stderr; the hooks treat that as UNREADABLE state and fail
CLOSED (open gates enforced, no Freehand release) — loud, never a guess.

Stdlib-only; the plugin root (this file's parent's parent) is put on sys.path.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _echo_fields(lines: "list[str]", prev: "tuple[int, int, str] | None") -> "list[str]":
    """`--echo`'s eight fields (see the module docstring). A gate is a live (unfenced) `- [ ]` / `- [x]` line, read
    the way `tasks.core._live_gate_scan` reads it; the previous gate is known by its place among the live gates and
    its text, never by its line, so lines added above it or a fenced checklist change nothing."""
    from tasks.core import _GATE_LINE_RE, _iter_fenced_flags
    fenced = _iter_fenced_flags(lines, unclosed_is_live=True, track_indented_code=False)
    gates: "list[tuple[int, bool, str]]" = []          # (line, checked, text)
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        raw = line.lstrip("﻿").rstrip("\r\n")
        m = _GATE_LINE_RE.match(raw)
        if m:
            gates.append((i + 1, m.group(1) != " ", raw[m.end():].strip()))
    first = next((k for k, g in enumerate(gates) if not g[1]), -1)
    below = sum(1 for g in gates[first + 1:] if g[1]) if first >= 0 else 0
    text = gates[first][2] if first >= 0 else ""
    transition, ticked = "none", ""
    if prev is not None:
        pos, prev_below, prev_text = prev

        def ticks(g: "tuple[int, bool, str]") -> bool:      # the previous gate, checked, with or without a note
            return g[1] and (g[2] == prev_text or g[2].startswith(prev_text + " "))

        hit = None
        if first >= 0 and first == pos and text == prev_text:
            transition = "skip" if below > prev_below else "same"
        elif 0 <= pos < len(gates) and ticks(gates[pos]):
            hit = gates[pos]
        elif any(not g[1] and g[2] == prev_text for g in gates):
            transition = "moved"
        else:
            near = sorted((abs(k - pos), g[0]) for k, g in enumerate(gates) if ticks(g))
            if near:
                hit = next(g for g in gates if g[0] == near[0][1])
            else:
                transition = "reworded"
        if hit is not None:
            transition, ticked = ("done-bare" if hit[2] == prev_text else "done"), str(hit[0])
    return [str(len(gates)), str(sum(1 for g in gates if g[1])), str(gates[first][0]) if first >= 0 else "",
            str(first), str(below), transition, ticked, text.replace("\r", "")]


def main(argv: "list[str]") -> int:
    fields = echo = False
    prev: "tuple[int, int, str] | None" = None
    args = argv[1:]
    if args and args[0] == "--fields":
        fields = True
        args = args[1:]
    elif args and args[0] == "--echo":
        echo = True
        args = args[1:]
        if len(args) == 4:
            try:
                prev = (int(args[1]), int(args[2]), args[3])
            except ValueError:
                print("task-status: --echo's <P> and <B> must be integers", file=sys.stderr)
                return 2
            args = args[:1]
    if len(args) != 1:
        print("usage: task-status.py [--fields | --echo] <task.md> [<P> <B> <T>]", file=sys.stderr)
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
        sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8", "replace"))
        sys.stdout.buffer.flush()
        return 0
    out = [_status_from_lines(lines)]
    if fields:
        unchecked, _total, first = _live_gate_state(lines)
        # The gate text is ONE physical line, so it cannot carry `\n`; any other
        # separator char stays inside the field, and a stray CR is dropped so the
        # third field is exactly one line for the hook's `read`.
        out += [str(unchecked), first.replace("\r", ""),
                "release" if _freehand_release_allowed(lines) else "hold"]
    # Bytes, `\n` only: a text-mode print() may translate the newline to CRLF, and
    # bash command substitution keeps the CR, so `blocked\r` would miss the hook's
    # exact match (round-5 panel). The hook also strips one trailing CR — belt + braces.
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
