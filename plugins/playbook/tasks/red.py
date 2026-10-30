"""The red receipt — tests first, as a record (task 280, PHASE-B.md item 9b; simplified by task 342, PLAN-PLAYBOOK-2.md
4.1 d, DESIGN-PLAYBOOK-2.md §2, approved R17).

The owner, 2026-10-21 15:15 (M1415): "daca am gandit o functionalitate, trebuie sa ii creez toate testele posibile si
imposibile, ca sa ma asigur ca codul pe care il voi scrie urmeaza path-ul lor". A full task carries a `## Test contract`
(task 278); this module makes "each test was watched failing first" something the CLI saw:

- `tasks red [<N>] [--rows <3,5-7>] [--mutant "<why>"]` runs each contract row's test (a backticked command, or a test
  id through `red_run` in `.agent/config.json`) in the PROJECT as it is now — as `tasks verify` runs the verify command:
  its own process group, `red_timeout` seconds, a scrubbed environment and a throwaway HOME, the command guard on each
  command — and appends one record per row to `<task>/red-receipt.jsonl` (format v2): its status, the tree's
  fingerprint (`tree_fingerprint`) and the test files' digest (`tests_digest`), both read for that row, and the time. A
  row during which the tree changed is marked `moved` and counts for nothing. `--mutant "<why>"` says the code is
  broken on purpose: its red counts, with its reason shown.
- A row is PROVED when its records (those of its test cell) end green and its newest red is followed by a green on the
  same test files and another tree (impl round 1: an older pair proves nothing about tests changed since). The close
  (`close_check`) blocks each row not proved — not run, never red, red now, red and green on the same tree, red on other
  test files, skipped, run badly — and on a receipt line it cannot read; `--force --reason` overrides. Limits: a test
  edited after its row was proved keeps the proof, and code changed after the newest green is caught only if `verify`
  runs that test (the receipt cannot tell which files a row's test reads).
- `record_base` writes `<task>/base.json` at the first activation: each repository's HEAD commit and the files that
  already differed from it, by content — no temporary index, nothing written into `.git/objects` (E023.11). Only `No
  behavior changes: <why>` reads it: no code file changed since then, `__pycache__` / `*.pyc` not counted (E059.7).
- `## Red receipt` in task.md shows each row's state; its own lines are replaced by their digest and every line the
  agent wrote there stays (task 303, audit 3 G3).

Gone with task 342: the run on a copy of the old code (`--base`), the late rows' own bases, `--drop` and the inference
of removed rows, the state carried between runs, the containment of a row, `red_tests`.
"""
from __future__ import annotations

import bisect
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

BASE_NAME = "base.json"
RECEIPT_NAME = "red-receipt.jsonl"
CONTRACT_HEADING = "## Test contract"
RECEIPT_HEADING = "## Red receipt"
DEFAULT_TIMEOUT = 300
LAST_MAX = 200
RECORD_V = 2


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


_GIT_OVERRIDES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                  "GIT_NAMESPACE", "GIT_ALTERNATE_OBJECT_DIRECTORIES")


def _git(repo: Path, args: "list[str]", *, text: bool = True, timeout: float = 120):
    """git in `repo` — whatever repository a caller's GIT_DIR / GIT_WORK_TREE names (impl round 1, agy). Its text keeps
    a name's bytes (`surrogateescape`: a Linux name that is not UTF-8 opens as itself — task 342's impl round 1, I6)."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_OVERRIDES}
    if text:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="surrogateescape", timeout=timeout, env=env)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=timeout, env=env)


def contract_problems(rows: "list[dict]") -> "list[str]":
    """Why `tasks red` refuses a contract's rows — and the close with it (task 286, agy, opus: one validation for both):
    no row, a row with no number, a number twice, a number that is not one (ASCII digits — `--rows` names them)."""
    if not rows:
        return [f"`{CONTRACT_HEADING}` has no row (a table line after its `---` separator)"]
    labels = [r["row"] for r in rows]
    out = []
    if any(not lab for lab in labels):
        out.append("a row with no number in `## Test contract` — each row needs its own `#`, the state is kept by it")
    twice = sorted({lab for lab in labels if lab and labels.count(lab) > 1})
    if twice:
        out.append(f"row {twice[0]} appears twice in `## Test contract` — each row needs its own `#` (plan panel: "
                   "sonnet)")
    words = [lab for lab in labels if lab and not _NUMBER.fullmatch(lab)]
    if words:                                           # `--rows` names rows by number (task 282, agy)
        out.append(f"row {words[0]} is not a number — each row's `#` in `## Test contract` is one")
    return out


def _cells(line: str) -> "list[str]":
    """A table line's cells; `\\|` is a `|` inside a cell (markdown's escape — a shell `||` is written `\\|\\|`)."""
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def parse_contract(text: str) -> "tuple[list[dict] | None, str]":
    """The `## Test contract` rows: [{"row", "kind", "behavior", "test", "line"}] — None with the reason when there
    is no live section. A table line after the header and its `---` separator is a row; a cell holding a `|` is
    not supported (the table's own grammar)."""
    from tasks.core import _live_section_span, _physical_lines
    lines = _physical_lines(text)
    sp = _live_section_span(lines, CONTRACT_HEADING)
    if sp is None:
        return None, f"no live `{CONTRACT_HEADING}` section in task.md"
    rows: "list[dict]" = []
    seen_sep = False
    for ln in lines[sp[0] + 1:sp[1]]:
        s = ln.strip()
        if not s.startswith("|"):
            if seen_sep and rows:
                break                                   # the table ended
            continue
        cells = _cells(s)
        if not seen_sep:
            if all(set(c) <= set("-: ") and c for c in cells):
                seen_sep = True
            continue
        if len(cells) < 4:
            continue
        rows.append({"row": cells[0], "kind": cells[1], "behavior": cells[2], "test": " | ".join(cells[3:]),
                     "line": s})
    return rows, ""


def _spans(cell: str) -> "list[str]":
    """The backticked spans of a cell, in order (a lone backtick ends nothing)."""
    out, i = [], 0
    while True:
        a = cell.find("`", i)
        if a < 0:
            return out
        b = cell.find("`", a + 1)
        if b < 0:
            return out
        if b > a + 1:
            out.append(cell[a + 1:b])
        i = b + 1


def resolve(cell: str, cfg: dict) -> "tuple[list[str], str]":
    """The commands a row's test cell names — ([commands], "") or ([], why). A span with white space is a command;
    one without is a test id run through `red_run` (its `{test}` replaced)."""
    spans = _spans(cell)
    if not spans:
        return [], "no test named (the cell holds no backticked test id or command)"
    run = cfg.get("red_run")
    cmds = []
    for sp in spans:
        if any(ch.isspace() for ch in sp):
            cmds.append(sp)
        elif isinstance(run, str) and "{test}" in run:
            cmds.append(run.replace("{test}", sp))
        else:
            return [], f"`{sp}` is a test id and there is no red_run (with `{{test}}`) in .agent/config.json"
    return cmds, ""


# The live project's environment a row must not see (plan panel P6): a repository override would point git at the
# real repository, a PYTHONPATH could load the current code into the base copy, the host's and the CLI's own
# variables name the real project and the session.
_ENV_DROP_PREFIXES = ("GIT_", "CLAUDE", "PLAYBOOK_")


_ENV_DROP = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT")


LAUNCH_FAILED = (126, 127)


def _row_env(environ: "dict[str, str]", home: Path, ceiling: Path, caged: bool = False) -> "dict[str, str]":
    """A row's environment. `caged`: the row runs in a sandbox (this run's, or the one the CLI is in) — then it gets
    the sandbox's own signal, PLAYBOOK_SANDBOXED=1, so a launcher inside it does not try to nest (provider/sandbox's
    `_child_env` rule)."""
    env = {k: v for k, v in environ.items() if not k.startswith(_ENV_DROP_PREFIXES) and k not in _ENV_DROP}
    env.update({"HOME": str(home), "PLAYBOOK_RED": "1", "GIT_CEILING_DIRECTORIES": str(ceiling),
                "NO_COLOR": "1", "PY_COLORS": "0", "PYTHON_COLORS": "0"})     # no colour (task 284)
    for k in ("FORCE_COLOR", "CLICOLOR_FORCE"):
        env.pop(k, None)
    if caged:
        env["PLAYBOOK_SANDBOXED"] = "1"
    return env


def _last_line(data: bytes) -> str:
    text = _plain(data)                                 # no escapes in the table (task 284, opus, sonnet)
    for ln in reversed(text.split("\n")):
        if ln.strip():
            return ln.strip()[:LAST_MAX]
    return ""


_ERROR_LINE = re.compile(r"^(?:E\s+)?((?:[A-Za-z_][\w.]*\.)?[A-Z]\w*(?:Error|Exception|Failure|Exit|Interrupt)\b.*)")


def _first_error(data: bytes) -> str:
    """The first line of the output that names an exception (`AssertionError: 1 != 2`, `ModuleNotFoundError: …`) —
    what a runner's summary line (`FAILED (errors=1)`) does not say (plan panel P5, opus)."""
    for ln in _plain(data).split("\n"):                 # a coloured `E   AssertionError` too (task 284, opus)
        m = _ERROR_LINE.match(ln.strip())
        if m:
            return m.group(1)[:LAST_MAX]
    return ""


def _abs_exe(name: str) -> "str | None":
    """`name` found only on the ABSOLUTE entries of PATH, as a real path: a relative entry (`bin:`) would let a file
    of the copy, or of the project, stand in for the sandbox or the shell (impl round 2, codex Critical, GRAVE)."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or not os.path.isabs(d):
            continue
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.realpath(cand)
    return None


# A test run's summary (task 281, PHASE-B 9b part 2): a run that passed no test is no proof. Only these two runners'
# summaries are read — any other runner's skip reads green, and the side records that no reader matched.
_NUMBER = re.compile(r"[0-9]+")                         # ASCII digits only: `²` passes str.isdigit (task 282, sonnet)


# A terminal escape, read as plain (task 284): a CSI with any parameter bytes (`\x1b[38:5:3m` — impl round 1, codex),
# an OSC title or link (ended by BEL or ST, never past its line), or a two-byte one such as `tput sgr0`'s `\x1b(B`.
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b\n]*(?:\x07|\x1b\\)?|[ -/]*[0-~])")


_UT_RAN = re.compile(r"(?m)^Ran ([0-9]+) tests? in [0-9.]+s\s*$")     # ASCII digits: a test controls them (282)


_UT_END = re.compile(r"(?m)^(OK|FAILED|PASSED)(?: \(([^)\n]*)\))?\s*$")     # PASSED: Twisted trial's (task 284)


_PY_WORD = r"[0-9]+ (?:passed|failed|skipped|xfailed|xpassed|errors?|warnings?|deselected|rerun)"


_PY_SUMMARY = re.compile(r"(?m)^=*\s*(" + _PY_WORD + r"(?:, " + _PY_WORD + r")*) in [0-9.]+s(?: \([^)\n]*\))?\s*=*\s*$")


_NO_TESTS = re.compile(r"(?m)^(?:=+ )?no tests ran in [0-9.]+s|^NO TESTS RAN\s*$")


_PY_EMPTY = re.compile(r"(?m)^=*\s*no tests ran in [0-9.]+s(?: \([^)\n]*\))?\s*=*\s*$")


_UT_NONE = re.compile(r"(?m)^NO TESTS RAN\s*$")       # unittest's word for a run that passed no test (283, agy)


def _plain(out: bytes) -> str:
    """A side's output as text with colour escapes taken out — a forced colour (pytest's `--color=yes`, `PY_COLORS`)
    would hide a summary from the readers (task 284, opus, sonnet, codex)."""
    return _ANSI.sub("", out.decode("utf-8", "replace"))


def _summaries(out: bytes) -> "list[tuple[str, int, int, bool]]":
    """EVERY unittest and pytest summary in `out`, in order: (reader, tests that passed, tests skipped, a failure
    or error). Each unittest summary's end is looked for only up to the next summary (task 282, sonnet: a 3.12
    `NO TESTS RAN` has no OK line and took the next one's). A chained command shows several (task 282, codex and
    sonnet: the last one hid an all-skipped or a FAILED run)."""
    text = _plain(out)
    found: "list[tuple[int, str, int, int, bool]]" = []
    uts = list(_UT_RAN.finditer(text))
    pys = list(_PY_SUMMARY.finditer(text))
    empties = list(_PY_EMPTY.finditer(text))         # pytest's empty run is a summary too (task 282, codex)
    nones = list(_UT_NONE.finditer(text))
    starts = sorted(m.start() for m in uts + pys + empties + nones)
    for m in uts:
        nxt = bisect.bisect_right(starts, m.start())   # the next summary of EITHER runner (agy), in log time (283)
        stop = starts[nxt] if nxt < len(starts) else len(text)
        end = _UT_END.search(text, m.end(), stop)
        counts: "dict[str, int]" = {}
        for part in (end.group(2) or "").split(",") if end else ():
            k, _, v = part.strip().partition("=")
            if _NUMBER.fullmatch(v.strip()):
                counts[k.strip()] = int(v)
        skipped = (counts.get("skipped", 0) + counts.get("expected failures", 0)  # an expected failure FAILED;
                   + counts.get("skips", 0) + counts.get("expectedFailures", 0))  # trial's words (task 284)
        failed = counts.get("failures", 0) + counts.get("errors", 0)              # (impl round 1, codex)
        ran = int(m.group(1))
        if end is None and ran > 0:                     # killed after `Ran N`: no pass, a failure (283, agy)
            found.append((m.start(), "unittest", 0, skipped, True))
            continue
        found.append((m.start(), "unittest", max(ran - skipped - failed, 0), skipped,
                      bool(failed) or (end is not None and end.group(1) == "FAILED")))
    for m in nones:
        found.append((m.start(), "unittest", 0, 0, False))
    for m in empties:
        found.append((m.start(), "pytest", 0, 0, False))
    for m in pys:
        counts = {}
        for part in m.group(1).split(","):
            n, _, word = part.strip().partition(" ")
            counts[word] = counts.get(word, 0) + int(n)
        found.append((m.start(), "pytest", counts.get("passed", 0) + counts.get("xpassed", 0),   # xpassed a pass
                      counts.get("skipped", 0) + counts.get("xfailed", 0),
                      bool(counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0))))
    return [f[1:] for f in sorted(found)]


def _no_test_passed(out: bytes) -> bool:
    """A summary in the output shows that no test passed (all skipped, expected failures, or none ran)."""
    return _side_status(0, out)[0] == "skipped"


def _side_status(code: int, out: bytes) -> "tuple[str, str, int]":
    """(status, reader, skips) of one side. A non-zero exit is red — but exit 5 with pytest's `no tests ran` or
    unittest's `NO TESTS RAN` ran no test: `skipped` (task 282, opus). At exit 0 a summary with a failure makes the
    side red (`a ; b` — task 282, opus and sonnet), and one that passed no test makes it `skipped`, never green; the
    skips of every summary add up."""
    sums = _summaries(out)
    reader = "+".join(sorted({x[0] for x in sums}))
    skips = sum(x[2] for x in sums)
    if code in LAUNCH_FAILED:
        return "launch failed", reader, skips
    if any(x[3] for x in sums):                         # a failure, whatever the return (task 282, agy and opus)
        return "red", reader, skips
    if code == 5 and _NO_TESTS.search(_plain(out)):
        return "skipped", reader, skips
    if code != 0:
        return "red", reader, skips
    if any(x[1] <= 0 for x in sums):
        return "skipped", reader, skips
    return "green", reader, skips


def _open_receipt(p: Path, write: bool) -> int:
    """The receipt's descriptor — opened without waiting and without following a link, and only a regular file: a
    FIFO in its place made the close wait forever (impl round 1, codex). OSError otherwise."""
    flags = (os.O_RDWR | os.O_APPEND | os.O_CREAT) if write else os.O_RDONLY
    fd = os.open(p, flags | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o644)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{p.name} is not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _append(task_file: Path, *records: dict) -> None:
    """Each record a line of the receipt, all in one write under the task lock (a drop's records: task 287, P6)."""
    from tasks.filelock import task_lock
    p = Path(task_file).parent / RECEIPT_NAME
    data = b"".join(json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n" for r in records)
    with task_lock(task_file):
        try:
            fd = _open_receipt(p, write=True)
        except OSError as exc:
            raise RuntimeError(f"{RECEIPT_NAME} cannot be written ({exc})") from None
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                data = b"\n" + data                     # a run killed mid-line: this record starts its own line
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)


# A line break or control character in a value would add a line to task.md — a `## Status` of its own (task 287, impl
# round 1: codex, opus, sonnet, grok). `_physical_lines` splits on `\n` only; every other one is refused or flattened too.
_LINE_BREAKERS = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _one_line(text) -> str:
    return _LINE_BREAKERS.sub(" ", str(text))


def _cell(text: str) -> str:
    """A value inside a table row: no `|` to end the cell, no backtick to open a span (plan panel P8)."""
    return (text or "").replace("\\", "\\\\").replace("|", "\\|").replace("`", "'")


# The section's own lines (task 303, audit 3's G3): a run rewrote the whole section, and every line the agent wrote
# there — a note, a `###` part, an open gate — went with it. Now its last line is its end line, which carries the
# SHA-256 of the lines it wrote above it (`_end_line`), and a rewrite replaces those lines only when they are still,
# byte for byte, the ones it wrote (`_kept`). No line is told for its own by its shape (impl round 1, opus and codex:
# an agent's `Note: … .` was) or by a digest of its own (impl round 2, codex: an 8-hex digest collided with an agent's
# gate, and an agent's copy of an edited line took that line's digest) — when in doubt, every line stays.
END_HEAD = "<!-- the end of tasks red's lines: a line below this one stays when it runs again"


_END_DIGEST = re.compile(r" — the digest of its lines: ([0-9a-f]{64}) -->$")


_KEPT_WHOLE = ("Kept by `tasks red`: the lines above its end line were not, byte for byte, the ones it wrote (a line "
               "was added, changed or taken out, or it found no end line it could read), so each line the section held "
               "there stays below, its own old lines among them.")


def _block_digest(lines: "list[str]") -> str:
    """The digest an end line carries: of the lines above it, the heading left out — blank lines left out, and each
    line without its trailing white space (an editor may add it: impl round 1, opus and sonnet)."""
    return hashlib.sha256("\n".join(ln.rstrip() for ln in lines if ln.strip()).encode("utf-8")).hexdigest()


def _end_line(lines: "list[str]") -> str:
    """The end line under `lines`, the CLI's lines of the section but its heading."""
    return f"{END_HEAD} — the digest of its lines: {_block_digest(lines)} -->"


def _end_digest(line: str) -> "str | None":
    """The digest an end line carries — or None: not an end line, or one that is not as the CLI wrote it."""
    ln = line.rstrip()
    m = _END_DIGEST.search(ln)
    if m is None or ln[:m.start()] != END_HEAD:
        return None
    return m.group(1)


def _kept(lines: "list[str]", a: int, b: int) -> "list[str]":
    """What a rewrite keeps of the section at [a, b) — its heading at `a` (task 303). Its end line is the first line
    outside a code fence (the writers' fail-closed view: after an unclosed opener, every line is fenced) that begins as
    one. Below it every line stays as it is. The lines above it go only when their digest is the one it carries: they
    are the CLI's, as it wrote them. Otherwise — a line added, changed or taken out among them, or no end line it can
    read (none, changed, or fenced) — nothing is the CLI's: every line above it (with an end line it cannot read, every
    line of the section, that one among them) stays, in order, under `_KEPT_WHOLE`, a run of blank lines outside a
    fence kept as one. The list ends with a blank line, as the section always did."""
    from tasks.core import _iter_nonfenced
    live = {i for i, _s in _iter_nonfenced(lines)}
    end = next((i for i in range(a + 1, b) if i in live and lines[i].rstrip().startswith(END_HEAD)), None)
    digest = _end_digest(lines[end]) if end is not None else None
    if end is None or digest is None:
        held, after = range(a + 1, b), []
    elif digest == _block_digest(lines[a + 1:end]):
        held, after = range(0), lines[end + 1:b]
    else:
        held, after = range(a + 1, end), lines[end + 1:b]
    above: "list[str]" = []
    gap = False                                         # a blank line outside a fence since the last line kept
    for i in held:
        if i in live and not lines[i].strip():
            gap = True
            continue
        if above and gap and above[-1].strip():
            above.append("")
        above.append(lines[i])
        gap = False
    out = ["", _KEPT_WHOLE, ""] + above if above else []
    if out and after and after[0].strip():
        out.append("")
    out += after
    if not out or out[-1].strip():
        out.append("")
    return out


_GATE_LINE = re.compile(r"- \[[ xX]\] Test contract:")


def _has_contract_gate(text: str) -> bool:
    """The task has the `Test contract:` design gate — a line outside any code fence (a quoted example is none)."""
    from tasks.core import _iter_nonfenced, _physical_lines
    return any(_GATE_LINE.match(s) for _i, s in _iter_nonfenced(_physical_lines(text)))


_NO_CHANGE = re.compile(r"^\s*(?:[-*]\s+)?No behavior changes:\s*(\S.*?)\s*$")     # a bullet too (286, agy)


def _contract_lines(text: str) -> "list[str]":
    from tasks.core import _live_section_span, _physical_lines
    lines = _physical_lines(text)
    sp = _live_section_span(lines, CONTRACT_HEADING)
    return [] if sp is None else lines[sp[0]:sp[1]]


def _no_change_reason(text: str) -> "str | None":
    """A contract with no row that says why: `No behavior changes: <why>` (the owner, M1439, 2026-10-22 08:47)."""
    from tasks.core import _iter_nonfenced
    for _i, ln in _iter_nonfenced(_contract_lines(text)):  # an example in a fence is none (task 286, sonnet)
        m = _NO_CHANGE.match(ln)
        if m:
            return m.group(1)
    return None


def _recorded_contract(task_dir: Path) -> bool:
    """The activation recorded that the task carried a test contract (`base.json`'s `contract`; task 286, sonnet)."""
    try:
        doc = json.loads((Path(task_dir) / BASE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("contract") is True


def _not_a_repository(project: Path) -> bool:
    """The project is not a repository at all: no `.git` entry on its path up to `/` (the owner, M1439: it passes,
    said). Git's own "not a git repository" is also its word for a broken one — a moved worktree's `gitdir:`, a `.git`
    it cannot read — and depends on the locale: that is git failing, which blocks (task 286, opus)."""
    p = Path(project).resolve()
    for d in (p, *p.parents):
        g = d / ".git"
        if g.exists() or g.is_symlink():
            return False
    return True


def close_digest(project: Path, task_file: Path, text: "str | None" = None) -> str:
    """What the check read, by files only — the receipt's bytes, the contract section, the config — compared again at
    the commit under the task lock, where no git runs (task 286, agy). The test files are not in it: their digest is
    git's (a stated limit; the close's tree check covers a task with an impl round). `text` is the task.md the check
    parsed — one snapshot; the contract gate and base.json are in it too (task 286, codex, grok, opus)."""
    from tasks.core import load_config, read_task_text
    h = hashlib.sha256()
    try:
        fd = _open_receipt(Path(task_file).parent / RECEIPT_NAME, write=False)
    except FileNotFoundError:
        h.update(b"no receipt")
    except OSError as e:
        h.update(f"unreadable: {e}".encode())
    else:
        try:
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                h.update(b)
        finally:
            os.close(fd)
    h.update(b"\0")
    try:
        text = read_task_text(Path(task_file)) if text is None else text
        h.update("\n".join(_contract_lines(text)).encode("utf-8", "surrogateescape"))
        h.update(b"\0gate" if _has_contract_gate(text) else b"\0no gate")
    except OSError as e:
        h.update(f"task.md unreadable: {e}".encode())
    h.update(b"\0")
    try:
        h.update((Path(task_file).parent / BASE_NAME).read_bytes())
    except FileNotFoundError:
        h.update(b"no base")
    except OSError as e:
        h.update(f"base unreadable: {e}".encode())
    h.update(b"\0" + _config_digest(project).encode())
    return h.hexdigest()


def _config_digest(project: Path) -> str:
    """The config as the check read it — kept from the first check, so a config changed after verify is refused (the
    post-panel judge, task 286)."""
    from tasks.core import load_config
    try:
        return hashlib.sha256(json.dumps(load_config(Path(project)), sort_keys=True, default=str).encode()).hexdigest()
    except Exception as e:                              # noqa: BLE001 — a config that cannot be read is a value too
        return f"config unreadable: {e}"


ROWS_MAX = 10000


def _parse_rows(spec: str) -> "tuple[set[int] | None, str]":
    """`--rows 3,5-7` → {3, 5, 6, 7}; an empty item, a word, an inverted range is refused (plan panel: agy), and so
    is anything but ASCII digits or more than ROWS_MAX rows in all — checked BEFORE a range is expanded (task 282:
    `1-99999999` expanded first; `²` passed `\\d`)."""
    shown = spec if len(spec) <= 40 else spec[:40] + "…"
    bad = f"--rows {shown!r}: a row number, a list or a range like 5-7 (ASCII digits; at most {ROWS_MAX} rows)"
    out: "set[int]" = set()
    for item in spec.split(","):
        m = re.fullmatch(r"[ \t]*([0-9]{1,9})[ \t]*(?:-[ \t]*([0-9]{1,9})[ \t]*)?", item)
        if not m:
            return None, bad
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if hi < lo or len(out) + (hi - lo + 1) > ROWS_MAX:
            return None, bad
        out.update(range(lo, hi + 1))
    return out, ""


def session_task_dir(project: Path) -> "tuple[Path | None, str]":
    """This session's task — the number in `.agent/sessions/<id>/current_state`, read without creating anything (as
    `tasks verify` reads it) — or (None, why): no id, no pointer, or a task that is missing, done or blocked. Never
    the first open task (task 281's parked item; task 282, opus and sonnet: a wrong pick writes into another task)."""
    from tasks.core import _extract_status, resolve_agent_dir, resolve_session_id
    from tasks.intent import find_task_dir
    sid = resolve_session_id(quiet=True)
    if not sid:
        return None, "this session has no id"
    lane = resolve_agent_dir(Path(project))
    p = lane / "sessions" / sid / "current_state"
    try:
        num = p.read_text(encoding="utf-8", errors="replace").strip() if p.is_file() and not p.is_symlink() else ""
    except OSError:
        num = ""
    if not _NUMBER.fullmatch(num):
        return None, "this session points at no task"
    d = find_task_dir(lane / "tasks", num.zfill(3))
    if d is None or not (d / "task.md").is_file():
        return None, f"this session's task {num} does not exist"
    status = _extract_status(d / "task.md")
    if status in ("done", "blocked"):
        return None, f"this session's task {d.name} is {status}"
    return d, ""


def _refuse(msg: str) -> None:
    print(f"Refused: {msg}. Nothing written.", file=sys.stderr)
    raise SystemExit(1)


_GUARD = None


def _guard(cmd: str, cfg: dict) -> "tuple[bool, str]":
    """The destructive-command interlock the agent's own shell meets, for a row's command (impl round 1, sonnet):
    the config's `command_guard: false` and `dangerous_commands`. A guard that cannot load or judge refuses (fails
    closed)."""
    global _GUARD
    if cfg.get("command_guard") is False:
        return True, ""
    # NOT `PLAYBOOK_ALLOW_DANGEROUS`: in this process it is the agent's environment, and the guard's own hook refuses
    # a permission an agent can give itself (impl round 2, opus — a guard failing open)
    try:
        if _GUARD is None:
            import importlib.util
            path = Path(__file__).resolve().parent.parent / "scripts" / "command_guard.py"
            spec = importlib.util.spec_from_file_location("_pb_red_command_guard", path)
            if spec is None or spec.loader is None:
                raise ImportError(str(path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _GUARD = mod
        verdict, name, why = _GUARD.classify_command(cmd, cfg.get("dangerous_commands"))
    except Exception as e:                              # noqa: BLE001 — fail closed
        return False, f"the command guard could not judge it ({e.__class__.__name__}: {e}) — refused"
    if verdict == "block":
        return False, f"the command guard blocks it: {name} — {why}"
    return True, ""


@contextlib.contextmanager
def _no_git_overrides():
    """The CLI's own git — here and in what it calls (`review_view.worktree_tree_why`) — in the repository it names,
    whatever GIT_DIR / GIT_WORK_TREE the caller exported (impl round 2, agy and opus)."""
    saved = {k: os.environ.pop(k) for k in _GIT_OVERRIDES if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _norm_scope(name: str) -> str:
    """A scope's name as the copy's paths spell it: `./code/` is `code` (impl round 2, codex)."""
    n = name.strip()                                    # never `\\` → `/` (impl round 3, GRAVE): a name, not a path
    while n.startswith("./"):
        n = n[2:]
    return n.rstrip("/")


def _scopes(project: Path, cfg: dict) -> "dict[str, Path]":
    from tasks.core import review_scopes
    return {_norm_scope(n): p for n, p in review_scopes(project, cfg).items()}


# ---------------------------------------------------------------------------- the start (base.json, v2)

def _heads(project_path: Path) -> "tuple[dict | None, str]":
    """Each scope's HEAD commit and the paths that already differed from it, with their content: ({scope: {"head":
    commit or None, "dirty": {path: sha256}}}, "") or (None, why). Reads only — no temporary index, nothing written
    into `.git/objects` (task 342; E023.11: the old capture wrote every untracked file there)."""
    from tasks.core import _fingerprint_exclude_pathspecs, load_config
    try:
        cfg = load_config(Path(project_path))
    except Exception:                                   # noqa: BLE001 — a bad config is said by its own readers
        cfg = {}
    exclude = _fingerprint_exclude_pathspecs(cfg)
    scopes: "dict[str, dict]" = {}
    for name, repo in _scopes(Path(project_path), cfg).items():
        where = name or "the project"
        try:
            head = _git(repo, ["rev-parse", "--verify", "-q", "HEAD^{commit}"])
            if name:
                # a code root must be its own repository: a folder of the project's own repository is read through it
                top = _git(repo, ["rev-parse", "--show-toplevel"])
                if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(repo):
                    return None, f"{where} is not its own git repository"
            commit = head.stdout.strip() if head.returncode == 0 else ""
            if not commit:                              # an unborn repository: no commit yet, a work tree all the same
                wt = _git(repo, ["rev-parse", "--is-inside-work-tree"])
                if wt.returncode != 0 or wt.stdout.strip() != "true":
                    return None, f"{where} is not a git work tree"
            st = _scope_state(repo, exclude)
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"git could not run in {where} ({e.__class__.__name__})"
        if st is None:
            return None, f"git could not list what differs from HEAD in {where}"
        # the files that already differed at the start, by their content: `No behavior changes:` subtracts them
        # (plan panel P8 — work uncommitted before the activation is not the task's)
        scopes[name] = {"head": commit or None, "dirty": {rel: _content(repo, rel) for rel in st[1]}}
    return scopes, ""


def record_base(project_path: Path, task_dir: Path, task_num: str, first: bool) -> "tuple[bool, str]":
    """At a task's first activation (`first`: its status was `pending`), create `<task_dir>/base.json` exclusively —
    each scope's HEAD commit (or the failure), and whether the task carried a test contract. (written, a line to print
    — empty when nothing needs saying.) Only `No behavior changes:` reads it now (task 342)."""
    p = Path(task_dir) / BASE_NAME
    if p.exists() or p.is_symlink():
        return False, ""
    if not first:
        # the start is where the task began: a task that is not `pending` began before this activation
        return False, ("note: no start recorded — this task already began (its status was not `pending` at this "
                       "activation); `No behavior changes:` cannot be checked against it")
    with _no_git_overrides():
        scopes, why = _heads(Path(project_path))
    doc: "dict" = {"v": 2, "task": task_num, "recorded_at": _now()}
    try:                                                # deleting it later does not turn the close's check off
        from tasks.core import read_task_text           # (task 286, sonnet)
        _text = read_task_text(Path(task_dir) / "task.md")
        doc["contract"] = parse_contract(_text)[0] is not None or _has_contract_gate(_text)
    except Exception:                                   # noqa: BLE001 — not known: the task.md says at the close
        pass
    if scopes is None:
        doc["failed"] = why
    else:
        doc["scopes"] = scopes
    tmp = Path(task_dir) / f".{BASE_NAME}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.link(tmp, p)                             # exclusive: two first activations write one start
        except FileExistsError:
            return False, ""
    except OSError as e:
        return False, f"note: no start recorded for this task — {p.name} could not be written ({e})"
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    if scopes is None:
        return True, f"note: no start recorded for this task — {why}"
    return True, ""


def _load_base(task_dir: Path) -> "dict | None":
    try:
        doc = json.loads((Path(task_dir) / BASE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and (isinstance(doc.get("scopes"), dict) or "failed" in doc) else None


# ---------------------------------------------------------------------------- a run (task 342)

# SIGTERM / SIGHUP / SIGINT while `tasks red` runs take the interrupt's path (impl round 1, I5: codex), and the handler
# itself first kills the process group of the row that runs (`_ROW`) — wherever the interrupt lands, after the wait
# too (post-panel run 2, codex: a signal between the wait and the kill left a child of the row running). While a
# row's process is being started they are held — a list of those that came — and raised once `_ROW` is known (post-
# panel run 1, codex: a signal in that window left the row running).
_HELD: "list[int] | None" = None
_ROW: "int | None" = None
_STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _stop(signum, _frame):
    if _HELD is not None:
        _HELD.append(signum)
        return
    if _ROW is not None:
        _kill_group(_ROW)
    raise KeyboardInterrupt(f"signal {signum}")


def _run_row(cmd: str, project: Path, home: Path, timeout: float) -> "dict":
    """One command in the project as it is now: {status, exit, seconds, last, error, reader, skips}. Its own process
    group: a timeout or an interrupt kills the group; a child that left the group and holds the output open cannot
    hang the run."""
    from tasks.bash_resolver import resolve_bash
    bash, _how = resolve_bash()
    if bash and not os.path.isabs(bash):
        bash = _abs_exe(os.path.basename(bash))         # never a relative shell (task 280's impl round 2, GRAVE)
    if not bash:
        return {"status": "launch failed", "exit": None, "seconds": 0.0, "last": "no bash found to run the command",
                "error": "", "reader": "", "skips": 0}
    env = _row_env(dict(os.environ), home, Path(project).parent,
                   caged=os.environ.get("PLAYBOOK_SANDBOXED") == "1")
    env["PYTHONDONTWRITEBYTECODE"] = "1"                # a row writes no cache into the tree (plan panel P1)
    global _HELD, _ROW
    t0 = time.monotonic()
    _HELD = []
    try:
        p = subprocess.Popen([bash, "-c", cmd], cwd=str(project), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        _ROW = p.pid                                    # still held: from here the handler can kill the group
    except BaseException:
        _HELD = None
        raise
    try:
        held, _HELD = _HELD, None
        if held:
            raise KeyboardInterrupt(f"signal {held[0]}")
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(p.pid)
            try:
                out, _ = p.communicate(timeout=5)
            except subprocess.TimeoutExpired:            # a child outside the group keeps the pipe
                if p.stdout is not None:
                    p.stdout.close()
                p.wait()
                out = b"(a child of the row kept its output open after the time-out; the output was cut)"
            return {"status": "timeout", "exit": None, "seconds": round(time.monotonic() - t0, 2),
                    "last": _last_line(out or b""), "error": _first_error(out or b""), "reader": "", "skips": 0}
        code = p.returncode
        _kill_group(p.pid)                              # a child the row left behind goes with it
    except BaseException:
        _kill_group(p.pid)                              # an interrupt stops the row's process group too
        p.wait()
        raise
    finally:
        _ROW = None
    status, reader, skips = _side_status(code, out or b"")
    if status == "green" and skips:
        status = "partly skipped"                       # a green that skipped a test proves nothing (task 281)
    return {"status": status, "exit": code, "seconds": round(time.monotonic() - t0, 2),
            "last": _last_line(out or b""), "error": _first_error(out or b""), "reader": reader, "skips": skips}


# A row's status from its commands' (a cell may name several): a command that ran badly decides, then one that passed
# no test, then a red; green only when every one is green.
_BAD = ("refused", "unrunnable", "launch failed", "timeout")
_NO_PASS = ("skipped", "partly skipped")


def _row_status(sides: "list[dict]") -> dict:
    for group in (_BAD, _NO_PASS, ("red",)):
        for s in sides:
            if s.get("status") in group:
                return s
    return sides[-1]


# What running the code writes into the tree — Python's caches (E059.7) — is not the code: never part of a tree.
_CACHE_PARTS = ("__pycache__",)
_CACHE_SUFFIXES = (".pyc", ".pyo")
# The project's test files (the old `red_tests` default): a path with one of these parts, or a name like these.
TEST_DIRS = ("tests", "test", "spec", "__tests__", "fixtures", "testdata", "__snapshots__")
TEST_NAMES = ("test_*", "*_test.*", "*.test.*", "*.spec.*", "conftest.py")


def _cache_file(path: str) -> bool:
    """A file Python writes when the code runs (E059.7: verify's own `__pycache__` read as a code change)."""
    parts = path.split("/")
    return any(p in _CACHE_PARTS for p in parts[:-1]) or parts[-1].endswith(_CACHE_SUFFIXES)


def _is_test_file(path: str) -> bool:
    import fnmatch
    parts = path.split("/")
    return any(p in TEST_DIRS for p in parts[:-1]) or any(fnmatch.fnmatchcase(parts[-1], g) for g in TEST_NAMES)


def _content(repo: Path, rel: str) -> str:
    """A file's bytes as a sha256, read by Python (never `git hash-object -w`: nothing is written to `.git`), with
    `+x` when it is executable (git keeps that bit: a script that loses it changed — post-panel run 2, codex); a link
    by its target; a path that is gone, `deleted`."""
    p = Path(repo) / rel
    try:
        if p.is_symlink():
            return "link:" + os.readlink(p)
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
            x = "+x" if os.fstat(fh.fileno()).st_mode & stat.S_IXUSR else ""
        return h.hexdigest() + x
    except FileNotFoundError:
        return "deleted"
    except IsADirectoryError:
        return "directory"
    except OSError as e:
        return f"unreadable:{e.errno}"


def _z(out: str) -> "list[str]":
    return [n for n in out.split("\0") if n]


def _scope_state(repo: Path, exclude: "list[str]") -> "tuple[str | None, list[str]] | None":
    """(HEAD commit or None when unborn, the paths that differ from it) — tracked files changed against HEAD (no
    rename detection: a moved file counts by its source too), untracked files git does not ignore, every tracked file
    when there is no commit, and the files flagged assume-unchanged or skip-worktree (git would hide their edits).
    Python's caches left out. Reads only. None when git cannot answer."""
    head = _git(repo, ["rev-parse", "--verify", "-q", "HEAD^{commit}"])
    commit = head.stdout.strip() if head.returncode == 0 else ""
    tracked = (_git(repo, ["diff", "--name-only", "--no-renames", "-z", commit, "--", ".", *exclude]) if commit else
               _git(repo, ["ls-files", "-z", "--", ".", *exclude]))
    others = _git(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--", ".", *exclude])
    tagged = _git(repo, ["ls-files", "-v", "-z", "--", ".", *exclude])
    if tracked.returncode != 0 or others.returncode != 0 or tagged.returncode != 0:
        return None
    flagged = [e[2:] for e in _z(tagged.stdout) if len(e) > 2 and (e[0].islower() or e[0] == "S")]
    paths = sorted({n for n in (*_z(tracked.stdout), *_z(others.stdout), *flagged) if not _cache_file(n)})
    return (commit or None), paths


def tree_fingerprint(project: Path, cfg: dict) -> str:
    """The tree's fingerprint for the receipt (task 342; plan panel P5): per scope (the project and each code root),
    its HEAD — or `unborn` — and each path that differs from it with its content (`_scope_state`, `_content`); `.agent`
    left out (the fingerprint's own exclusion, task 343), Python's caches too. Works before the first commit; writes nothing.
    "" when git cannot answer."""
    from tasks.core import _fingerprint_exclude_pathspecs
    exclude = _fingerprint_exclude_pathspecs(cfg)
    h = hashlib.sha256()
    try:
        for name, repo in sorted(_scopes(Path(project), cfg).items()):
            st = _scope_state(repo, exclude)
            if st is None:
                return ""
            head, paths = st
            h.update(f"\0scope {name}\0{head or 'unborn'}\0".encode())
            for rel in paths:
                h.update(rel.encode("utf-8", "surrogateescape") + b"\0" + _content(repo, rel).encode() + b"\0")
    except (OSError, subprocess.SubprocessError):
        return ""
    return h.hexdigest()[:12]


def tests_digest(project: Path, cfg: dict) -> str:
    """The sha256 of the project's test files (tracked and untracked, `_is_test_file`): a green proves a row only
    against a red on the same test files (plan panel P6 — a red seen before the test existed, or a test edited until
    it passes, proves nothing). "" when git cannot answer."""
    from tasks.core import _fingerprint_exclude_pathspecs
    exclude = _fingerprint_exclude_pathspecs(cfg)
    h = hashlib.sha256()
    try:
        for name, repo in sorted(_scopes(Path(project), cfg).items()):
            ls = _git(repo, ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".", *exclude])
            if ls.returncode != 0:
                return ""
            for rel in sorted({n for n in _z(ls.stdout) if _is_test_file(n) and not _cache_file(n)}):
                h.update(f"{name}/{rel}".encode("utf-8", "surrogateescape") + b"\0"
                         + _content(repo, rel).encode() + b"\0")
    except (OSError, subprocess.SubprocessError):
        return ""
    return h.hexdigest()[:12]


# ---------------------------------------------------------------------------- the receipt (format v2)

def _read_records(path: Path, bad: "list[int] | None" = None) -> "list[dict]":
    """The receipt's v2 records in file order. A line that is not JSON is counted in `bad` (its 1-based number): the
    close blocks on it, never reads past it as nothing. A record of the old format (no `"v": 2`) is skipped: a task
    begun before task 342 starts its receipt over (the new-projects rule). OSError when the file cannot be read."""
    try:
        fd = _open_receipt(Path(path), write=False)
    except FileNotFoundError:
        return []
    try:
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
    finally:
        os.close(fd)
    out = []
    for i, raw in enumerate(b"".join(chunks).split(b"\n"), 1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            if bad is not None:
                bad.append(i)
            continue
        if isinstance(rec, dict) and rec.get("v") == RECORD_V:
            out.append(rec)
    return out


def row_state(row: dict, records: "list[dict]") -> dict:
    """One row's state from its records — those of its test cell (its key: rewording the behavior keeps them, changing
    the test starts it over), a run whose tree moved left out. `proved`: the newest is green, and the newest red is
    followed, on a later line, by a green on the same test files (`tests`) and another tree — an older pair proves
    nothing about tests changed since (impl round 1, I3: codex); a test file changed after that green keeps it (I1).
    Otherwise `not run`, `run badly`, `skipped`, `red now`, `never red`, `same tree` or `other test files`."""
    recs = [r for r in records if r.get("test") == row["test"] and not r.get("moved")]
    out = {"row": row["row"], "kind": row["kind"], "test": row["test"], "state": "not run", "red": None, "green": None,
           "newest": recs[-1] if recs else None}
    for r in recs:
        if r.get("status") == "red":
            out["red"] = r
        elif r.get("status") == "green":
            out["green"] = r
    if not recs:
        return out
    st = recs[-1].get("status")
    if st in _BAD:
        out["state"] = "run badly"
    elif st in _NO_PASS:
        out["state"] = "skipped"
    elif st == "red":
        out["state"] = "red now"
    elif st != "green":
        out["state"] = "run badly"
    elif out["red"] is None:
        out["state"] = "never red"
    else:
        last = max(i for i, r in enumerate(recs) if r.get("status") == "red")
        red = recs[last]
        after = [g for g in recs[last + 1:] if g.get("status") == "green" and g.get("tests") == red.get("tests")]
        if any(g.get("tree") != red.get("tree") for g in after):
            out["state"] = "proved"
        elif after:
            out["state"] = "same tree"
        else:
            out["state"] = "other test files"
    return out


_WHY = {"not run": "not run — run `tasks red`",
        "never red": "never seen red — watch it fail before the code, or break the code on purpose and run `tasks red "
                     "--mutant \"<why>\"`, then green",
        "red now": "its newest run is red",
        "same tree": "its red and its green ran on the same tree (a flaky test?) — green after the code changed proves it",
        "other test files": "its newest red ran on other test files than the greens after it (seen red before the test "
                            "was written, or a test file changed between them) — watch it red on the test files as "
                            "they are (on a mutant: `tasks red --mutant \"<why>\"`), then green",
        "skipped": "its newest run passed no test (skipped)",
        "run badly": "its newest run did not run its test (refused, unrunnable, launch failed or timeout)"}


def _red_word(r: "dict | None") -> str:
    if not r:
        return "—"
    return str(r.get("at", "")) + (f", mutant: {r['mutant']}" if r.get("mutant") else "")


def _state_lines(states: "list[dict]", head: str) -> "list[str]":
    lines = [head]
    for e in states:
        tail = f" — red {_red_word(e['red'])}" if e["state"] == "proved" else (
            f" — {_WHY[e['state']]}" if e["state"] in _WHY else "")
        lines.append(_one_line(f"  row {e['row']} ({e['kind']}, {e['test']}): {e['state']}{tail}"))
    return lines


def _counts(states: "list[dict]") -> str:
    order: "list[str]" = []
    by: "dict[str, list[str]]" = {}
    for e in states:
        if e["state"] not in by:
            order.append(e["state"])
            by[e["state"]] = []
        by[e["state"]].append(str(e["row"]))
    return "; ".join(f"{s}: {len(by[s])} ({_rows_word(by[s])})" for s in order) or "no row"


def _rows_word(labels: "list[str]") -> str:
    return ("rows " if len(labels) > 1 else "row ") + ", ".join(labels)


# ---------------------------------------------------------------------------- `## Red receipt`

_FIRST_LINE = ("Written by `tasks red`: each row's state from its records in `red-receipt.jsonl` beside this file — a "
               "row counts when it was seen red and later green on another tree (a red from a mutant names why). ")
_TABLE_HEAD = "| # | Kind | Test | State | Red (the one that counts, or the newest) | Green (the newest) |"
_TABLE_RULE = "|---|------|------|-------|------------------------------------------|--------------------|"


def _section(states: "list[dict]", run: str, at: str) -> str:
    head = [RECEIPT_HEADING, "<!-- pin -->", _FIRST_LINE + f"The newest run `{run}`, {at}.", "",
            "State: " + _counts(states) + ".", "", _TABLE_HEAD, _TABLE_RULE]
    for e in states:
        head.append(f"| {_cell(str(e['row']))} | {_cell(e['kind'])} | {_cell(e['test']).replace('`', chr(39))} | "
                    f"{_cell(e['state'])} | {_cell(_red_word(e['red']))} | "
                    f"{_cell(str(e['green'].get('at', '')) if e['green'] else '—')} |")
    out = [_one_line(h) for h in head]                  # each value on its own line, never one more (task 287)
    return "\n".join(out + [_end_line(out[1:])])


def _write_section(task_file: Path, run: str, at: str) -> "tuple[str, list[dict] | None]":
    """Rewrite `## Red receipt`'s own lines, keeping every other (`_kept`, task 303) — or put the section right after
    `## Test contract` — under the task's lock, from the contract and the receipt as they are then. ("", the states)
    or (why nothing was written, None)."""
    from tasks.atomic import atomic_write
    from tasks.core import _physical_lines, _writer_section_span, read_task_text
    from tasks.filelock import task_lock
    try:
        with task_lock(task_file):
            text = read_task_text(task_file)
            rows, why = parse_contract(text)
            if rows is None:
                return why, None
            records = _read_records(Path(task_file).parent / RECEIPT_NAME)
            states = [row_state(r, records) for r in rows]
            lines = _physical_lines(text)
            new = _physical_lines(_section(states, run, at))
            sp = _writer_section_span(lines, RECEIPT_HEADING)
            if sp is not None:                          # only its own lines: the agent's stay (task 303)
                lines[sp[0]:sp[1]] = new + _kept(lines, *sp)
            else:
                new += [""]
                cp = _writer_section_span(lines, CONTRACT_HEADING)
                place = cp[1] if cp is not None else len(lines)
                if place and lines[place - 1].strip():
                    new = [""] + new
                lines[place:place] = new
            atomic_write(task_file, "\n".join(lines))
            return "", states
    except OSError as exc:
        return f"{RECEIPT_NAME} or task.md cannot be read ({exc})", None


# ---------------------------------------------------------------------------- the close (task 286, simplified by 342)

def _problems(rows: "list[dict]") -> "list[str]":
    """The contract's problems (`contract_problems`) and two rows with one test cell: they would share their records
    (plan panel P11)."""
    out = contract_problems(rows)
    seen: "dict[str, str]" = {}
    for r in rows:
        if r["test"] in seen:
            out.append(f"rows {seen[r['test']]} and {r['row']} have the same test — each row needs its own test (a "
                       "row's records are its test's)")
            break
        seen[r["test"]] = r["row"]
    return out


def _rev_content(repo: Path, rev: str, rel: str) -> str:
    """`_content` of a file as commit `rev` holds it (`git ls-tree`, `git cat-file`: reads only): its bytes' sha256
    with `+x` for mode 100755, a link by its target, `deleted` when `rev` has none, the kind and object otherwise (a
    submodule: never a file's)."""
    ls = _git(repo, ["--literal-pathspecs", "ls-tree", "-z", rev, "--", rel])
    if ls.returncode != 0:
        return "unreadable"
    entry = ls.stdout.split("\0", 1)[0]
    if not entry:
        return "deleted"
    mode, kind, obj = entry.split("\t", 1)[0].split(" ")
    if kind != "blob":
        return f"{kind}:{obj}"
    blob = _git(repo, ["cat-file", "blob", obj], text=False)
    if blob.returncode != 0:
        return "unreadable"
    if mode == "120000":
        return "link:" + blob.stdout.decode("utf-8", "surrogateescape")
    return hashlib.sha256(blob.stdout).hexdigest() + ("+x" if mode == "100755" else "")


def _shown(name: str) -> str:
    """A path as it can be printed: a byte that is not UTF-8 (kept by `_git`) replaced."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _changed_by_scope(project: Path, task_dir: Path, cfg: dict) -> "tuple[dict[str, list[str]] | None, str]":
    """Every file changed since the task's start (base.json), per scope, by the scope's own paths: those that differ
    from the start commit in the work tree or in HEAD (a commit undone in the work tree still changed the code — impl
    round 1, I7) — no rename detection, untracked files, the flagged files by content — and every file that differed
    at the start (`dirty`: one restored or deleted since changed too — I4), minus each of those that still holds its
    start bytes; `.agent` and Python's caches left out. `_code_changed` keeps the code among them; the review form's
    changed-files rule reads them all (task 347). Reads only. (None, why) when it cannot be read."""
    from tasks.core import _fingerprint_exclude_pathspecs
    base = _load_base(Path(task_dir))
    if not base or not isinstance(base.get("scopes"), dict) or "failed" in base:
        return None, "the task's recorded start (base.json) cannot be read"
    scopes = _scopes(Path(project), cfg)
    exclude = _fingerprint_exclude_pathspecs(cfg)
    found: "dict[str, list[str]]" = {}

    def differ(repo: Path, start: "str | None", dirty: dict) -> "list[str] | None":
        # `--relative`: names from the repository's folder, as `ls-files` gives them — a project in a subfolder of its
        # repository (task 347's impl round 1, codex); the same names where the folder is the repository's top
        tracked = (_git(repo, ["diff", "--relative", "--name-only", "--no-renames", "-z", start, "--", ".", *exclude])
                   if start else
                   _git(repo, ["ls-files", "-z", "--", ".", *exclude]))
        others = _git(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--", ".", *exclude])
        tagged = _git(repo, ["ls-files", "-v", "-z", "--", ".", *exclude])
        if tracked.returncode != 0 or others.returncode != 0 or tagged.returncode != 0:
            return None
        paths = set(_z(tracked.stdout)) | set(_z(others.stdout)) | set(dirty)
        committed: "set[str]" = set()
        if _git(repo, ["rev-parse", "--verify", "-q", "HEAD^{commit}"]).returncode == 0:
            # unborn at the start: from the empty tree (`hash-object` without -w writes nothing)
            since = start or _git(repo, ["hash-object", "-t", "tree", "/dev/null"]).stdout.strip()
            moved = (_git(repo, ["diff", "--relative", "--name-only", "--no-renames", "-z", since, "HEAD", "--", ".",
                                 *exclude])
                     if since else None)
            if moved is None or moved.returncode != 0:
                return None
            committed = set(_z(moved.stdout))
        for e in _z(tagged.stdout):                     # git hides a flagged file's edit: read its bytes and mode
            if len(e) > 2 and (e[0].islower() or e[0] == "S"):
                rel = e[2:]
                if not start or _rev_content(repo, start, rel) != _content(repo, rel):
                    paths.add(rel)
        out = {n for n in paths if not _cache_file(n) and not (n in dirty and dirty[n] == _content(repo, n))}
        # a commit since the start counts on its own — the work tree's bytes do not hide it — unless it holds exactly
        # the bytes the file had at the start (post-panel run 1, codex)
        out |= {n for n in committed if not _cache_file(n) and not (n in dirty and dirty[n] == _rev_content(repo, "HEAD", n))}
        return sorted(out)
    with _no_git_overrides():
        for scope, r in sorted(base["scopes"].items()):
            where = scope or "the project"
            if scope not in scopes:                     # unread is no "unchanged" (task 286, agy, opus, sonnet)
                return None, f"{where} is no longer configured — whether its code changed since the start cannot be read"
            r = r if isinstance(r, dict) else {}
            start = r.get("head") if "head" in r else r.get("commit")
            dirty = r.get("dirty") if isinstance(r.get("dirty"), dict) else {}
            try:
                got = differ(scopes[scope], start, dirty)
            except (OSError, subprocess.SubprocessError) as e:
                return None, f"whether code changed in {where} could not be read ({e.__class__.__name__})"
            if got is None:
                return None, f"whether code changed in {where} could not be read"
            found[scope] = got
        for scope in sorted(set(scopes) - set(base["scopes"])):   # a code root added since the start
            got = differ(scopes[scope], None, {})
            if got is None:
                return None, f"whether {scope}'s code changed could not be read"
            found[scope] = got
    return found, ""


def _code_changed(project: Path, task_dir: Path, cfg: dict) -> "tuple[list[str] | None, str]":
    """The CODE files changed since the task's start (base.json): `_changed_by_scope`'s files, kept only when
    `core.classify_delta_paths` calls them behavioral (docs, tests, `.agent` and the root's `.md` are not). Reads
    only. None with why when it cannot be read. A name that is not UTF-8 is shown with its bad bytes replaced."""
    from tasks.core import classify_delta_paths
    base = _load_base(Path(task_dir))
    if not base or not isinstance(base.get("scopes"), dict) or "failed" in base:
        return None, "`No behavior changes:` needs the task's recorded start (base.json) to show no code changed"
    got, why = _changed_by_scope(project, task_dir, cfg)
    if got is None:
        return None, why
    changed: "list[str]" = []
    for scope, names in got.items():
        code, _docs = classify_delta_paths(names, is_outer_scope=not scope)
        changed += [_shown(f"{scope + '/' if scope else ''}{n}") for n in code]
    return changed, ""


def close_check(project: Path, task_file: Path, budget: float = 20.0) -> dict:
    """The close's check of the test contract: {"applies": False, …} for a task with no contract, no contract gate and
    none recorded at its activation; else {"applies", "lines", "blocks", "digest", "config"} — each row's state and a
    block for each row not proved (task 342: its newest red, then a green on another tree), any reason the receipt cannot be
    judged (fail closed), and `close_digest` of what was read. `budget` is kept for the callers (no git runs long)."""
    from tasks.core import load_config, read_task_text
    project, task_file = Path(project), Path(task_file)
    d = task_file.parent
    text = read_task_text(task_file)
    rows, why = parse_contract(text)
    gate = _has_contract_gate(text)
    digest = close_digest(project, task_file, text)     # one snapshot, for every close (task 286, codex, grok)
    config = _config_digest(project)
    if rows is None and not gate and not _recorded_contract(d):
        return {"applies": False, "lines": [], "blocks": [], "digest": digest, "config": config}
    head = f"test contract (the close's check): task {d.name}"

    def result(lines: "list[str]", blocks: "list[str]") -> dict:
        return {"applies": True, "lines": lines, "blocks": blocks, "digest": digest, "config": config}
    if rows is None:
        if gate:
            return result([f"{head}: {why} — the task has the `Test contract:` gate"], [why])
        return result([f"{head}: {why}"], ["its activation recorded a test contract, and task.md has neither its gate "
                                            "nor its section"])
    if _not_a_repository(project):
        return result([f"{head}: not judged: not a git repository — `tasks red` needs git (the close passes)"], [])
    try:
        cfg = load_config(project)
    except Exception:                                   # noqa: BLE001
        cfg = {}
    bad: "list[int]" = []
    try:
        records = _read_records(d / RECEIPT_NAME, bad)
    except OSError as e:
        return result([f"{head}: the receipt cannot be read ({e})"], [f"the receipt cannot be read ({e})"])
    broken = ([f"the receipt has {len(bad)} line(s) the check cannot read (line {bad[0]}: a truncated record?)"]
              if bad else [])
    reason = _no_change_reason(text) if not rows else None
    if reason is not None:
        changed, why_c = _code_changed(project, d, cfg)
        if changed is None:
            return result([f"{head}: No behavior changes: {reason} — {why_c}"], [why_c] + broken)
        if changed:
            return result([f"{head}: No behavior changes: {reason} — but code changed since the start: "
                           + ", ".join(changed[:8]) + (" …" if len(changed) > 8 else "")],
                          ["`No behavior changes:` while code changed since the start (" + ", ".join(changed[:8]) + ")"]
                          + broken)
        return dict(result([f"{head}: No behavior changes: {reason} — no code file changed since the start"], broken),
                    nochange=True)                      # its working state is bound at the commit (post-panel judge)
    problems = _problems(rows)
    if problems:
        return result([f"{head}: {p} — `tasks red` refuses the contract" for p in problems], problems)
    states = [row_state(r, records) for r in rows]
    lines = _state_lines(states, f"{head}, {len(rows)} row(s), {len(records)} record(s) — {_counts(states)}")
    blocks = [_one_line(f"row {e['row']}: {e['state']} ({_WHY[e['state']]})") for e in states if e["state"] != "proved"]
    return result(lines, blocks + broken)


def close_way(blocks: "list[str]") -> str:
    """The way through a block, by its causes."""
    text = " ".join(blocks)
    ways = []
    if any(w in text for w in (": not run", ": never red", ": same tree", ": red now", ": skipped", ": run badly",
                               ": other test files")):
        ways.append("run each row with `tasks red` — seen red before its code (or red on a mutant: `tasks red "
                    "--mutant \"<why>\"` with the code broken on purpose), then green on the code")
    if "refuses the contract" in text or "`## Test contract` has no row" in text or "is not a number" in text \
            or "appears twice" in text or "a row with no number" in text:
        ways.append("fix `## Test contract`")
    if "recorded a test contract" in text:
        ways.append("put the contract back")
    if "no live `## Test contract` section" in text:
        ways.append("put the `## Test contract` section back")
    if "while code changed since the start" in text:
        ways.append("write the contract's rows for the code that changed (or undo the change)")
    if "the check cannot read" in text:
        ways.append("repair or remove the receipt's broken line (`tasks red` writes the row again)")
    if "cannot be read" in text or "could not be read" in text:
        ways.append("fix what the check says, then `tasks work done` again")
    ways.append("or override with --force --reason (written in the receipt)")
    return "; ".join(ways[:-1]) + (" — " if len(ways) > 1 else "") + ways[-1] + "."


def numbers_line(project: Path, task_dir: Path, budget: float = 10.0) -> "str | None":
    """`tasks numbers`'s contract line, read live (never written into numbers.json): each row's state. None for a task
    without a live `## Test contract`."""
    from tasks.core import read_task_text
    text = read_task_text(Path(task_dir) / "task.md")
    rows, _why = parse_contract(text)
    if rows is None:
        return None
    reason = _no_change_reason(text) if not rows else None
    if reason is not None:
        return f"contract: No behavior changes: {reason}"
    try:
        records = _read_records(Path(task_dir) / RECEIPT_NAME)
    except OSError as e:
        return f"contract: {len(rows)} row(s) — the receipt cannot be read ({e})"
    return f"contract: {len(rows)} row(s) — {_counts([row_state(r, records) for r in rows])}"


# ---------------------------------------------------------------------------- `tasks red`

USAGE = ("usage: tasks red [<N>] [--rows <3,5-7>] [--mutant \"<why>\"]  |  tasks red [<N>] --check")


def _parse(args: "list[str]") -> "tuple[list[str], set[int] | None, str | None, bool, str]":
    """(positional, rows, mutant reason, check, error)."""
    pos: "list[str]" = []
    only: "set[int] | None" = None
    mutant: "str | None" = None
    check = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--check":
            check = True
        elif a == "--rows":
            if i + 1 >= len(args):
                return pos, None, None, False, "--rows takes a row number, a list or a range"
            only, err = _parse_rows(args[i + 1])
            if err:
                return pos, None, None, False, err
            i += 1
        elif a == "--mutant":
            if i + 1 >= len(args) or not args[i + 1].strip() or args[i + 1].startswith("--"):
                return pos, None, None, False, ("--mutant takes the reason in words: what was broken on purpose "
                                                "(`--mutant \"f raises\"`)")
            mutant = _one_line(args[i + 1].strip())[:LAST_MAX]
            i += 1
        elif a.startswith("-"):
            return pos, None, None, False, f"unknown argument {a!r}"
        else:
            pos.append(a)
        i += 1
    if check and (mutant is not None or only is not None):
        return pos, None, None, False, "--check runs no row, so it takes no --rows or --mutant"
    return pos, only, mutant, check, ""


def cmd_red(cmd_args: "list[str]") -> None:
    """`tasks red [<N>] [--rows …] [--mutant "<why>"] | --check` — see the module docstring."""
    with _no_git_overrides():
        _cmd_red(cmd_args)


def _cmd_red(cmd_args: "list[str]") -> None:
    from tasks.core import load_config, read_task_text, refuse_unreadable_records, resolve_agent_dir
    from tasks.intent import find_task_dir
    from tasks.shared import find_project_root
    args, only, mutant, check, err = _parse(list(cmd_args))
    if err or len(args) > 1 or (args and not _NUMBER.fullmatch(args[0])):     # ASCII digits (task 283)
        print(f"Error: {err or USAGE}", file=sys.stderr)
        raise SystemExit(2)
    project = Path(find_project_root())
    if args:
        d = find_task_dir(resolve_agent_dir(project) / "tasks", args[0].zfill(3))
    else:
        d, why_none = session_task_dir(project)
        if d is None:
            print(f"Error: tasks red: {why_none} — name the task: `tasks red <N>`", file=sys.stderr)
            raise SystemExit(2)
    if d is None or not (d / "task.md").is_file():
        print(f"Error: tasks red: no task {args[0] if args else ''}", file=sys.stderr)
        raise SystemExit(2)
    task_file = d / "task.md"
    refuse_unreadable_records(d)        # task 335: before any row runs or red-receipt.jsonl is written
    rows, why = parse_contract(read_task_text(task_file))
    if rows is None:
        _refuse(f"{why} — `tasks red` runs the contract's rows")
    try:
        cfg = load_config(project)
    except Exception:                                   # noqa: BLE001
        cfg = {}
    if check:
        try:
            records = _read_records(d / RECEIPT_NAME)
        except OSError as exc:
            _refuse(f"{RECEIPT_NAME} cannot be read ({exc.__class__.__name__}: {exc})")
        states = [row_state(r, records) for r in rows]
        for ln in _state_lines(states, f"red receipt — task {d.name}: {len(rows)} row(s), {len(records)} record(s) — "
                                       f"{_counts(states)}"):
            print(ln)
        return
    problems = _problems(rows)                          # one validation with the close's check (task 286)
    if problems:
        _refuse(problems[0])
    if only is not None:
        missing = sorted(only - {int(r["row"]) for r in rows})
        if missing:
            _refuse(f"no row {', '.join(map(str, missing[:20]))}{' …' if len(missing) > 20 else ''} in "
                    "`## Test contract`")
        rows = [r for r in rows if int(r["row"]) in only]
    timeout = cfg.get("red_timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        timeout = DEFAULT_TIMEOUT
    if not tree_fingerprint(project, cfg):
        _refuse("the tree's fingerprint cannot be read (git status / diff failed) — a run must say which code it ran on")
    if not tests_digest(project, cfg):
        _refuse("the test files' digest cannot be read (git ls-files failed) — a run must say which test files it ran "
                "on")
    run = hashlib.sha256(f"{os.getpid()} {time.time_ns()}".encode()).hexdigest()[:12]
    at = _now()
    print(f"tasks red — task {d.name}, run {run}" + (f" — on a mutant: {mutant}" if mutant else ""))
    home = Path(tempfile.mkdtemp(prefix="pb-red-home-"))
    old_handlers = {}
    for sig in _STOP_SIGNALS:                           # `_stop`: an interrupt, held while a row's process starts
        try:
            old_handlers[sig] = signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass
    try:
        for row in rows:
            cmds, why_r = resolve(row["test"], cfg)
            before = tree_fingerprint(project, cfg)
            tests = tests_digest(project, cfg)          # for each row: an earlier row may change them (I2)
            if not cmds:
                sides = [{"status": "unrunnable", "exit": None, "seconds": 0.0, "last": why_r, "error": "",
                          "reader": "", "skips": 0}]
            else:
                sides = []
                for cmd in cmds:
                    ok, why_g = _guard(cmd, cfg)
                    if not ok:
                        sides.append({"status": "refused", "exit": None, "seconds": 0.0, "last": why_g, "error": "",
                                      "reader": "", "skips": 0, "cmd": cmd})
                        continue
                    sides.append(dict(_run_row(cmd, project, home, float(timeout)), cmd=cmd))
            after = tree_fingerprint(project, cfg)
            tests_after = tests_digest(project, cfg)
            top = _row_status(sides)
            rec = {"v": RECORD_V, "run": run, "at": _now(), "row": row["row"], "kind": row["kind"],
                   "test": row["test"], "status": top.get("status"), "exit": top.get("exit"),
                   "seconds": round(sum(float(s.get("seconds") or 0) for s in sides), 2), "last": top.get("last", ""),
                   "error": top.get("error", ""), "reader": top.get("reader", ""), "skips": top.get("skips", 0),
                   "tree": before, "tests": tests, "sides": sides}
            if mutant:
                rec["mutant"] = mutant
            if not before or after != before or not tests or tests_after != tests:
                rec["moved"] = True                     # the code it ran on is not one tree: it counts for nothing
            _append(task_file, rec)
            said = rec["status"] + (f" (mutant: {mutant})" if mutant and rec["status"] == "red" else "")
            if rec.get("moved"):
                said += (" — the tree changed while it ran: it counts for nothing (a file the test itself writes can be "
                         "left out with `.gitignore`)")
            detail = rec["error"] or rec["last"]
            print(_one_line(f"  row {row['row']} ({row['kind']}): {said}" + (f" — {detail}" if detail else "")))
            if mutant and rec["status"] == "green":
                print(f"  note: row {row['row']} came out green on the mutant — the break did not reach its test, so "
                      "it is no red")
    except KeyboardInterrupt:
        print(f"  interrupted: the rows that ended are in {RECEIPT_NAME}; task.md was not written.", file=sys.stderr,
              flush=True)
        raise SystemExit(130)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        for sig, h in old_handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass
    why_w, states = _write_section(task_file, run, at)
    if states is None:
        print(f"  ## Red receipt not written: {why_w}", file=sys.stderr)
        return
    print(f"  the rows' states: {_counts(states)} — `## Red receipt` in task.md, every record in {RECEIPT_NAME}")
