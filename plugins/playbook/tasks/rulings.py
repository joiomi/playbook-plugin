"""The project's owner rulings list — written once, never edited (task 227, PHASE-B.md item 2).

The owner, 2026-10-16 09:48: "Listă de proiect cu durată (Recomandat)". A ruling for one task stays in that task's
`## Owner rulings`; one for a batch or the whole project is written ONCE into `<project>/.agent/rulings.md` — one list
per project whatever the lane, shared like `.agent/config.json` (plan panel P2) — by `tasks ruling add`,
with its number, its date, his exact words and how long it holds (`permanent`, or `until <what ends it>`). Nothing ever
rewrites an entry: `tasks ruling end <N> "<why>"` appends a closing entry. Every write appends at the end, so the
committed text is always a byte prefix of the file — `tasks audit` checks that (`rulings-append-only`). Every judge's
SETTLED block lists the rulings in force (`post_d6.settled_block`); `tasks bootstrap` prints them. A write holds
`<project>/.agent/rulings.lock` (ignored by init). `Words:` is the rest of its line — the CLI adds no quotes and
strips none but the argument's surrounding whitespace. A list that is a link or not a regular file is never read.

The file, one fact per line (task 226):

    # Owner rulings
    > Written by `tasks ruling`; append-only — end a ruling with `tasks ruling end <N> "<why>"`, never edit one.

    ## R1
    - Date: 2026-10-16 09:48
    - Words: "Listă de proiect cu durată (Recomandat)"
    - Holds: permanent
    - About: a ruling for more than one task is written once, here

    ## R1 ended
    - Date: 2026-11-02 10:00
    - Note: batch 1.6.4 released

Pure stdlib.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

FILE_NAME = "rulings.md"
LOCK_NAME = "rulings.lock"
WORDS_MAX = 1500
LINE_MAX = 2000         # a whole entry's line in the SETTLED block — always under post_d6.PROJECT_CAP (impl round 1)
# every character `str.splitlines` breaks on (U+001C-1E too: a reader's cut must not split the owner's words — round 1)
_NOT_ONE_LINE = "\n\r\x0b\x0c\x1c\x1d\x1e\x85" + chr(0x2028) + chr(0x2029)
HEADER = ("# Owner rulings\n"
          "> Written by `tasks ruling`; append-only — end a ruling with `tasks ruling end <N> \"<why>\"`, "
          "never edit one.\n")
_ENTRY_RE = re.compile(r"^## R([1-9][0-9]{0,5})( ended)?$")
_FIELD_RE = re.compile(r"^- (Date|Words|Holds|About|Note): (.*)$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}$")
_FIELDS = {False: ("Date", "Words", "Holds"), True: ("Date", "Note")}
_OPTIONAL = {False: ("About",), True: ()}


class RulingsError(ValueError):
    """The list cannot be read or the request cannot be written; the message says why."""


@dataclass(frozen=True)
class Ruling:
    number: int
    date: str
    words: str
    holds: str
    about: str = ""
    ended: "tuple[str, str] | None" = None      # (date, note) of its closing entry

    def line(self) -> str:
        """One line for a judge or the bootstrap: `R<N> (<date>, <holds>): <words> — <about>`."""
        s = f"R{self.number} ({self.date}, {self.holds}): {self.words}"
        return s + (f" — {self.about}" if self.about else "")


def path_for(project: "Path | str") -> Path:
    return Path(project) / ".agent" / FILE_NAME


def project_of_task(task_file: "Path | str") -> "Path | None":
    """The project of a task.md at `<project>/.agent/[<user>/]tasks/<dir>/task.md`, or None for one elsewhere."""
    p = Path(task_file)
    tasks_dir = p.parent.parent
    if tasks_dir.name != "tasks":
        return None
    for anc in (tasks_dir.parent, tasks_dir.parent.parent):
        if anc.name == ".agent":
            return anc.parent
    return None


def parse(text: str) -> "list[Ruling]":
    """Every ruling of the list, with its closing entry if it has one. Refuses (RulingsError) anything the CLI does not
    write — a text that is not the header and then entries of the fixed form — never guesses."""
    if "\r" in text:
        raise RulingsError("the list holds a carriage return — the CLI writes LF only")
    if not text.startswith(HEADER):
        raise RulingsError("the list does not start with its header")
    body = text[len(HEADER):]
    rulings: "dict[int, Ruling]" = {}
    order: "list[int]" = []
    blocks = [b for b in body.split("\n\n") if b.strip()]
    if body and not body.endswith("\n"):
        raise RulingsError("the list does not end with a newline")
    for block in blocks:
        lines = block.strip("\n").split("\n")
        m = _ENTRY_RE.match(lines[0])
        if not m:
            raise RulingsError(f"not an entry heading: {lines[0][:80]!r}")
        n, is_end = int(m.group(1)), bool(m.group(2))
        fields: "dict[str, str]" = {}
        for ln in lines[1:]:
            f = _FIELD_RE.match(ln)
            if not f or f.group(1) in fields:
                raise RulingsError(f"R{n}: not a field line, or a field twice: {ln[:80]!r}")
            if not f.group(2).strip() or any(c in f.group(2) for c in _NOT_ONE_LINE):
                raise RulingsError(f"R{n}: {f.group(1)} is blank or holds a line separator")
            fields[f.group(1)] = f.group(2)
        want, opt = _FIELDS[is_end], _OPTIONAL[is_end]
        if any(k not in fields for k in want) or any(k not in want + opt for k in fields):
            raise RulingsError(f"R{n}{' ended' if is_end else ''}: its fields must be {', '.join(want)}"
                               + (f" (and {', '.join(opt)})" if opt else ""))
        if not _DATE_RE.match(fields["Date"]):
            raise RulingsError(f"R{n}: the date is not YYYY-MM-DD HH:MM")
        if is_end:
            r = rulings.get(n)
            if r is None or r.ended:
                raise RulingsError(f"R{n} ended: no such ruling open above it")
            rulings[n] = Ruling(r.number, r.date, r.words, r.holds, r.about, (fields["Date"], fields["Note"]))
        else:
            if n in rulings or (order and n != max(order) + 1) or (not order and n != 1):
                raise RulingsError(f"R{n}: rulings are numbered 1, 2, 3 … in order")
            holds = fields["Holds"]
            if holds != "permanent" and not (holds.startswith("until ") and holds[6:].strip()):
                raise RulingsError(f"R{n}: Holds is `permanent` or `until <what ends it>`")
            rulings[n] = Ruling(n, fields["Date"], fields["Words"], holds, fields.get("About", ""))
            order.append(n)
    return [rulings[n] for n in order]


def read(project: "Path | str") -> "list[Ruling]":
    """The rulings of the project's list; [] when there is none. RulingsError when it cannot be read."""
    p = path_for(project)
    _regular_or_absent(p)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise RulingsError(f"cannot read {p}: {e}") from None
    try:
        return parse(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise RulingsError("the list is not UTF-8") from None


def _regular_or_absent(p: Path) -> None:
    """Refuse a list that is a link or not a regular file — its target would reach every judge (impl round 1)."""
    import stat
    try:
        st = p.lstat()
    except FileNotFoundError:
        return
    except OSError as e:
        raise RulingsError(f"cannot read the list: {e}") from None
    if not stat.S_ISREG(st.st_mode):
        raise RulingsError("the list is a link or not a regular file")


def in_force(rulings: "list[Ruling]") -> "list[Ruling]":
    return [r for r in rulings if r.ended is None]


def _one_line(name: str, value: str) -> str:
    v = value.strip()
    if not v:
        raise RulingsError(f"{name}: empty")
    if any(c in v for c in _NOT_ONE_LINE):
        raise RulingsError(f"{name} must be one line")
    return v


def _now() -> str:
    """Local time, as `date` prints it — the owner's times are local."""
    return time.strftime("%Y-%m-%d %H:%M")


def _append(project: "Path | str", make_entry) -> str:
    """Under the list's own lock: read its raw bytes, check them, append `make_entry(rulings)` at the end, write it
    atomically. A file the CLI did not write (a CR, not UTF-8, not its form) is refused unchanged."""
    from tasks.atomic import atomic_write
    from tasks.filelock import named_lock
    p = path_for(project)
    if not p.parent.is_dir():
        raise RulingsError(f"no {p.parent} — not a playbook project")
    try:
        with named_lock(p.parent / LOCK_NAME):
            _regular_or_absent(p)
            try:
                raw = p.read_bytes()
            except FileNotFoundError:
                raw = b""
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise RulingsError("the list is not UTF-8") from None
            rulings = parse(text) if text else []
            entry = make_entry(rulings)
            atomic_write(p, (text or HEADER) + "\n" + entry, newline="")
            return entry
    except OSError as e:                         # the lock or the write (impl round 1: no traceback)
        raise RulingsError(f"cannot write the list: {e}") from None


def add(project, words: str, *, holds: str, about: str = "", date: str = "") -> int:
    """Append a ruling; its number. Refuses (RulingsError) a malformed request or the same words already in force."""
    words, holds = _one_line("the words", words), _one_line("Holds", holds)
    if len(words) > WORDS_MAX:
        raise RulingsError(f"the words are over {WORDS_MAX:,} characters — a judge's SETTLED block holds 8,000")
    about = _one_line("About", about) if about.strip() else ""
    date = date.strip() or _now()
    if not _DATE_RE.match(date):
        raise RulingsError("the date is not YYYY-MM-DD HH:MM")
    if holds != "permanent" and not (holds.startswith("until ") and holds[6:].strip()):
        raise RulingsError("Holds is `permanent` or `until <what ends it>`")
    if len(Ruling(0, date, words, holds, about).line()) > LINE_MAX:
        raise RulingsError(f"the entry is over {LINE_MAX:,} characters (its words, Holds and About together)")
    out: "list[int]" = []

    def make(rulings):
        same = next((r for r in in_force(rulings) if r.words == words), None)
        if same is not None:
            raise RulingsError(f"these words are already in force as R{same.number}")
        n = len(rulings) + 1
        out.append(n)
        return (f"## R{n}\n- Date: {date}\n- Words: {words}\n- Holds: {holds}\n"
                + (f"- About: {about}\n" if about else ""))
    _append(project, make)
    return out[0]


def end(project, number: int, note: str) -> None:
    """Append the closing entry of ruling `number`. Refuses an unknown or already ended one."""
    note = _one_line("the note", note)

    def make(rulings):
        r = next((x for x in rulings if x.number == number), None)
        if r is None:
            raise RulingsError(f"no ruling R{number}")
        if r.ended:
            raise RulingsError(f"R{number} already ended {r.ended[0]}")
        return f"## R{number} ended\n- Date: {_now()}\n- Note: {note}\n"
    _append(project, make)


USAGE = ('usage: tasks ruling add (--permanent | --until "<what ends it>") [--about "<what it decides>"] '
         '[--date "YYYY-MM-DD HH:MM"] "<the owner\'s exact words>"\n'
         '       tasks ruling end <N> "<why it ended>"\n'
         '       tasks ruling list [--all]')


def _usage(msg: str) -> "None":
    print(f"Error: {msg}\n{USAGE}", file=sys.stderr)
    raise SystemExit(2)


def cmd_ruling(cmd_args: "list[str]") -> None:
    """`tasks ruling add|end|list` — the project's owner rulings list (task 227)."""
    from tasks.shared import find_project_root
    if not cmd_args or cmd_args[0] not in ("add", "end", "list"):
        _usage("say add, end or list")
    sub, rest = cmd_args[0], list(cmd_args[1:])
    project = find_project_root()
    try:
        if sub == "list":
            if rest not in ([], ["--all"]):
                _usage("list takes only --all")
            rulings = read(project)
            shown = rulings if rest else in_force(rulings)
            if not shown:
                print("(no rulings in force)" if not rest else "(no rulings)")
            for r in shown:
                print(r.line() + (f"  [ended {r.ended[0]}: {r.ended[1]}]" if r.ended else ""))
            return
        if sub == "end":
            if len(rest) != 2 or not re.fullmatch(r"R?[1-9][0-9]{0,5}", rest[0]):
                _usage('end takes a ruling number and the reason it ended')
            n = int(rest[0].lstrip("R"))
            end(project, n, rest[1])
            print(f"R{n} ended — {path_for(project)}")
            return
        opts: "dict[str, str]" = {}
        words: "list[str]" = []
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--":                        # the rest are words, even one that starts with -- (impl round 1)
                words.extend(rest[i + 1:])
                break
            if a == "--permanent":
                if "holds" in opts:
                    _usage("give --permanent or --until once")
                opts["holds"] = "permanent"
            elif a in ("--until", "--about", "--date"):
                if i + 1 >= len(rest):
                    _usage(f"{a} needs a value")
                key = {"--until": "holds", "--about": "about", "--date": "date"}[a]
                if key in opts:
                    _usage(f"{'--permanent or --until' if key == 'holds' else a} given twice")
                v = rest[i + 1]
                opts[key] = f"until {v.strip()}" if a == "--until" else v
                i += 1
            elif a.startswith("--"):
                _usage(f"unknown option {a}")
            else:
                words.append(a)
            i += 1
        if "holds" not in opts:
            _usage("say how long it holds: --permanent or --until \"<what ends it>\"")
        if len(words) != 1:
            _usage("give the owner's exact words as ONE quoted argument")
        n = add(project, words[0], holds=opts["holds"], about=opts.get("about", ""), date=opts.get("date", ""))
        print(f"R{n} added — {path_for(project)}")
    except RulingsError as e:
        print(f"Error: {e} — {path_for(project)} unchanged", file=sys.stderr)
        raise SystemExit(1)
