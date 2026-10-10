#!/usr/bin/env python3
"""Does a shell command name a task directory that is, or may be, inside the project?

task-gate-hook's "don't create task directories manually" guard (Guard 2) calls
this only after its own trigger regex has matched, to decide whether the match
is a real task directory or a fixture elsewhere (task 080, S1d — the 073 flag
C15: `<tmp>/.agent/tasks/001-x` in a temp dir was refused).

Usage:   printf '%s' <command> | PB_PROJECT=<project root> python3 task-dir-target.py
         printf '%s' <file path> | PB_PROJECT=<project root> python3 task-dir-target.py --path
         printf '%s' <file path> | PB_PROJECT=<project root> python3 task-dir-target.py --creates
Exit 0   every `.agent[/<lane>]/tasks/` token is an absolute path outside the project
Exit 1   some token is, or may be, inside it — the hook blocks
Other    (a crash) — the hook blocks too; this script can only NARROW the guard

Only a SIMPLE command is judged at all: its first word is `mkdir`, it has no
shell operator (`; && || | & ( ) < >`), and it contains no `$`, backtick, glob,
brace or newline anywhere — the filesystem is read BEFORE the command runs, so an
earlier step (`ln -s …;`, `cd … &&`, `$(…)`) could repoint what was judged.
Within it, only a literal absolute path is ever judged "outside"; a relative
path, `~`, or a command shlex cannot split all count as "may be inside" — the
guard's old answer. Tokens are matched after collapsing `..` and `//`.

A token is inside when ANY of these says so (so no single view can let it out):
its `..`-collapsed spelling under the project's spelling or its realpath; the
realpath of the raw token (kernel `..` semantics) or of the collapsed one under
the project's realpath (symlinks into any subdirectory, `/var`→`/private/var`);
or `samefile` between the project and an existing ancestor of either realpath
(case variants on a case-insensitive disk).
"""
from __future__ import annotations

import os
import posixpath
import re
import shlex
import sys

_TASK_DIR = re.compile(r"\.agent(/[^/]+)?/tasks(/|$)")
# case-folded twin: `.AGENT/TASKS` is the live tree on a case-insensitive disk
_TASK_DIR_ANYCASE = re.compile(_TASK_DIR.pattern, re.I)
# a word the shell still rewrites at run time: a variable, a backtick, or a glob
# (kept strict as in round 1, although a glob cannot create a new path)
# Task 110 (085 round 3 V4, parked R8): the shell's SPECIAL parameters
# (`$@ $* $# $? $- $$ $!`) expand at run time too — `.$@/tasks/9-x` passed.
_UNRESOLVED = re.compile(r"\$[A-Za-z_{(0-9@*#?!$-]|`|[?*\[]")
# One unresolved expansion in a word, for the hint test below.
_EXPANSION_PART = re.compile(r"\$\{[^}]*\}|\$\([^)]*\)|`[^`]*`|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@*#?!$-]")
_NAME_HINT = re.compile(r"\.ag|tasks", re.I)
_SHELL_EXPANDS = set("$`*?[{")
_OPERATORS = set(";&|()<>")


def _canon(path: str) -> str:
    """Lexical form: forward slashes and `..` collapsed."""
    return posixpath.normpath(path.replace("\\", "/"))


def _is_absolute(token: str) -> bool:
    return token.replace("\\", "/").startswith("/")


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _lexically_inside(path: str, project: str) -> bool:
    return _under(_canon(path), _canon(project))


def _existing_ancestor(path: str) -> str | None:
    probe = path
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent
    return probe


def _physically_inside(path: str, project: str) -> bool:
    proj_real = _canon(os.path.realpath(project))
    candidates = {os.path.realpath(path),
                  os.path.realpath(posixpath.normpath(path.replace("\\", "/")))}
    for cand in candidates:
        if _under(_canon(cand), proj_real):
            return True
        probe = _existing_ancestor(cand)
        while probe is not None:
            try:
                if os.path.samefile(probe, project):
                    return True
            except OSError:
                pass
            parent = os.path.dirname(probe)
            probe = None if parent == probe else parent
    return False


def _collapsed(token: str) -> str:
    """`..` and `//` collapsed, so `.agent/x/../tasks` and `.agent//tasks` are seen."""
    return posixpath.normpath(token.replace("\\", "/"))


_ANSI_C_QUOTE = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.S)
_BRACE_CAP = 512          # more spellings than this → judged strictly (blocks)


def _decode_ansi_c(command: str) -> "str | None":
    """Replace every `$'…'` with what bash makes of it (`$'\\x73'` → `s`).
    None when the decoder is unavailable — the caller then fails closed."""
    if "$'" not in command:
        return command
    try:
        from command_guard import _ansi_c_decode
    except Exception:
        return None
    return _ANSI_C_QUOTE.sub(lambda m: _ansi_c_decode(m.group(1)), command)


def _brace_expand(text: str) -> "list[str] | None":
    """Every spelling bash's brace expansion can produce: comma lists (nested)
    and `{a..z}` / `{1..9}` sequences. None past _BRACE_CAP (fail closed)."""
    out, work = [], [text]
    while work:
        t = work.pop()
        m = None
        depth, start = 0, -1
        for k, ch in enumerate(t):                # expand the FIRST complete,
            if ch == "{":                         # expandable top-level group
                if depth == 0:
                    start = k
                depth += 1
            elif ch == "}" and depth:
                depth -= 1
                if depth == 0 and _expandable(t[start + 1:k]):
                    m = (start, k)
                    break
        if m is None:
            out.append(t)
        else:
            s, e = m
            head, inner, tail = t[:s], t[s + 1:e], t[e + 1:]
            seq = _SEQ.fullmatch(inner)
            if seq and "," not in _top_level(inner):
                a, b = seq.group(1), seq.group(2)
                if a.lstrip("-").isdigit() and b.lstrip("-").isdigit():
                    lo, hi = sorted((int(a), int(b)))
                    if hi - lo > _BRACE_CAP:
                        return None
                    alts = [str(v) for v in range(lo, hi + 1)]
                elif len(a) == 1 and len(b) == 1:
                    lo, hi = sorted((ord(a), ord(b)))
                    alts = [chr(v) for v in range(lo, hi + 1)]
                else:
                    alts = [inner]
            else:
                alts = _split_top_level(inner)
            work.extend(head + alt + tail for alt in alts)
        if len(out) + len(work) > _BRACE_CAP:
            return None
    return out


_SEQ = re.compile(r"(-?\w+)\.\.(-?\w+)(?:\.\.-?\d+)?")


def _expandable(inner: str) -> bool:
    """A `{…}` bash expands: a top-level comma list or an `a..b` sequence
    (`{x}` and `{}` stay literal)."""
    return "," in _top_level(inner) or bool(_SEQ.fullmatch(inner))


def _top_level(inner: str) -> str:
    """`inner` with nested `{…}` groups removed (their commas are not ours)."""
    depth, keep = 0, []
    for ch in inner:
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        elif depth == 0:
            keep.append(ch)
    return "".join(keep)


def _split_top_level(inner: str) -> "list[str]":
    parts, depth, cur = [], 0, []
    for ch in inner:
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        cur.append(ch)
    parts.append("".join(cur))
    return parts


def _spellings(command: str) -> "list[str] | None":
    """What the shell can turn this command's words into, loosely: ANSI-C
    decoded, quotes/backslashes/`$` removed, braces expanded. None = cannot
    tell (decoder missing, expansion too large) → the caller fails closed."""
    decoded = _decode_ansi_c(command)
    if decoded is None:
        return None
    stripped = re.sub(r"[\"'\\\\$]", "", decoded)
    # Too many spellings to enumerate: judge the text as written rather than
    # refuse it (post-cap fix, impl panel round 3 — failing closed here refused
    # `touch f{1..1000}` once any `{` reached the helper). The bound: a task
    # dir hidden inside a brace list that large is not seen.
    expanded = _brace_expand(stripped)
    return expanded if expanded is not None else [stripped]


def runs_mkdir(command: str) -> bool:
    """Does any spelling contain the word `mkdir`? (task 085 round 2, T2: the
    hook's own trigger also fires on `$'…'`, which it cannot decode.)"""
    sp = _spellings(command)
    if sp is None:
        return True
    return any(re.search(r"(^|[^\w])mkdir($|[^\w])", s) for s in sp)


def names_a_task_dir(command: str) -> bool:
    """Could this command, once the shell dequotes/unescapes/expands it, name a
    task directory at all? (task 085 G2 + round 2 T2). `.ag'ent/tasks'`,
    `.ag\\ent`, `ta''sks`, `ta$'\\x73'ks`, `ta{sk,zz}s` and `ta{r..t}ks` all read
    as what the shell creates; a glob needs no help (it only matches paths that
    already exist, so it cannot create a new task dir). Loose on purpose: a
    match only sends the command to the strict path below."""
    sp = _spellings(command)
    if sp is None:
        return True
    # Round 3 (U1, U7): a WORD of some spelling must be a task-dir path — the
    # real pattern after `..`/`//` collapse, case-folded (a case-insensitive disk
    # maps `.AGENT/TASKS` onto the live tree). Two loose substrings blocked
    # `.agentic/tasks` and `.agent-stuff/my-tasks`.
    if any(_TASK_DIR_ANYCASE.search(_collapsed(w)) for s in sp for w in s.split()):
        return True
    # Round 3 (U2): `$` was stripped above, so a variable can bridge the name
    # (`.$D/tasks`, `.agent/$T`). A word holding an unresolved variable or a
    # backtick that also SHOWS part of the name is judged strictly (blocks);
    # one with no visible hint (`"$D/build"`, `$A/$B`) stays allowed — the
    # owner's call on unknown variables (2026-09-23).
    decoded = _decode_ansi_c(command) or command
    return any(_UNRESOLVED.search(w) and _hint_can_complete(w) for w in decoded.split())


def _hint_can_complete(word: str) -> bool:
    """Task 110 (109 G2-02, parked R15 — a regression against 1.5.45): the old
    rule took any `.ag` next to a variable as a task-dir hint, so `"$N/.agent"`
    was refused although a word that ENDS in a complete `.agent` cannot add a
    `tasks` component — it is exactly as unknowable as the allowed `"$D/build"`
    (owner, 2026-09-23). A hint counts only when the visible text can still be
    part of a task-dir path: `tasks` is visible, or an expansion follows the
    `.ag` hint (`.agent/$T`, `.ag$X`), or the `.ag` is not a complete `.agent`
    component (`.ag$X/…` handled by the previous case, `.agen…` a prefix)."""
    if not _NAME_HINT.search(word):
        return False
    visible = _EXPANSION_PART.sub("\x00", word)
    visible = re.sub(r"[\"'\\\\]", "", visible)
    if re.search(r"tasks", visible, re.I):
        return True
    for m in re.finditer(r"\.ag", visible, re.I):
        rest = visible[m.start():]
        if "\x00" in rest:
            return True                           # an expansion after the hint
        if not re.match(r"\.agent(/|$)", rest, re.I):
            return True                           # `.ag…` that is not a whole `.agent`
    return False


def _masked(command: str) -> str:
    """The command with its DATA regions removed. A quoted heredoc a non-shell
    reads is data wherever it sits on the line (`cd x; python3 - <<'PY'`);
    `_strip_data_regions` only knows a cat/tee sink at the start of a line."""
    import command_guard as cg
    return cg._strip_comments(cg._strip_data_regions(cg._strip_quoted_heredoc_bodies(command)))


def _shell_text(command: str) -> str:
    """The command as the shell reads it, minus the bodies that are DATA (a
    quoted heredoc a non-shell reads, a file-sink heredoc). Unlike `_masked` it
    keeps every word of an echo line: masking a quoted echo argument also
    swallows a redirection glued to it (`echo 'x'>file` is one word to the
    lexer), and the call-level scans below read quotes themselves."""
    import command_guard as cg
    return cg._strip_comments(cg._drop_sink_heredoc_bodies(cg._strip_quoted_heredoc_bodies(command)))


def _commands(command: str, depth: int = 0) -> "list[tuple[str, str, bool]]":
    """`(head, segment text, env)` for every command the shell would RUN in
    `command` — `env` is True when an assignment prefix (`X=… cmd`) stood before
    it — found with command_guard's walker (segments, wrappers and their
    options, `sh -c`, `eval`, `watch`/`parallel` payloads, `$(…)`/backticks/
    process substitutions, herestrings to a shell, `find -exec`), after removing
    the regions that are DATA (a quoted heredoc a non-shell reads, echo/printf
    literals). Task 110 (parked item 31, R8): the hook's trigger is the WORD
    mkdir anywhere, so a heredoc note, a `git commit -m`, a `grep` pattern or a
    quoted `tasks new` intent that merely MENTIONS it was judged as a run.
    Raises on any failure — the caller then keeps the old answer."""
    import command_guard as cg
    if depth > 32:
        raise RuntimeError("nesting too deep")
    text = _masked(command)
    out: "list[tuple[str, str, bool]]" = []
    for seg in cg._split_segments(text):
        rest, executes, payloads = cg._walk_prefix(seg)
        for p in payloads:
            if p.startswith("\x00"):                 # a `parallel` the guard cannot judge
                raise RuntimeError("parallel jobs not enumerable")
            if p:
                out += _commands(p, depth + 1)
        if not executes:
            continue
        lexed = cg._lex(rest)
        stripped = seg.strip()
        prefix = stripped[:len(stripped) - len(rest)] if rest and stripped.endswith(rest) else ""
        env = any(cg._ASSIGN.match(t[2]) for t in cg._lex(prefix))
        if lexed:
            out.append((lexed[0][2], rest, env))
        elif not payloads and not _runs_nothing(seg):
            out.append((_UNREADABLE_HEAD, seg, env))  # a wrapper took every word
        elif not payloads and any(cg._ASSIGN.match(t[2]) for t in cg._lex(seg)):
            out.append((_ASSIGNMENT_HEAD, seg, True))  # `X=…` alone: the environment changes
        values = [t[2] for t in lexed]
        for k, v in enumerate(values[:-1]):          # `find … -exec <cmd> …`
            if v in ("-exec", "-execdir", "-ok", "-okdir"):
                out.append((values[k + 1], " ".join(values[k + 1:]), env))
        inner = cg._unwrap_shell_c(rest) or cg._unwrap_shell_c(cg._normalize_command_head(rest))
        if inner:
            out += _commands(inner, depth + 1)
    subs = (cg._command_substitutions(cg._strip_quoted_heredoc_bodies(text))
            + cg._herestring_payloads(text))
    for sub in subs:
        if sub.strip():
            out += _commands(sub, depth + 1)
    return out


def _names_mkdir(head: str) -> bool:
    """Is this command NAME `mkdir` in some spelling? Brace expansion and a glob
    in the NAME (`mk{d,z}ir`, `mkd?r`) count — conservatively."""
    import fnmatch
    name = posixpath.basename(head.replace("\\", "/")).lstrip("({!")
    alts = _brace_expand(name) or [name]
    return any(a == "mkdir" or (set(a) & set("?*[") and fnmatch.fnmatchcase("mkdir", a))
               for a in alts)


# Commands that only READ or PRINT their arguments: a mkdir word among them is
# text. Impl panel round 1 (opus, sol-high): the first form allowed whenever no
# head read `mkdir`, so a head it could not resolve (`$M -p …`, `"$(echo mkdir)"
# …`) or a wrapper command_guard does not model (`busybox mkdir`, `flock /l
# mkdir`) passed, where 1.5.45 refused. Now a mkdir word is inert only inside
# masked data or in the arguments of one of these; any other head with a mkdir
# word in its command — and every unresolved head — counts as running it.
# Not inert: `rg --pre CMD` and `git grep -O CMD` run a program they are given.
_INERT_HEADS = {"echo", "printf", "grep", "egrep", "fgrep", "cat", "head",
                "tail", "wc", "less", "more", "true", ":", "tasks", "test", "["}
# Round 2 asks a harder question of a command than "is a mkdir word among its
# arguments text?": can it RUN what another command of the call wrote? This list
# is the commands that cannot — they read, print or write text and start no
# program of the caller's choosing. So no pager and no sorter (`less` runs a
# preprocessor, `sort` a compressor), no command with an output-file operand
# (`uniq IN OUT`), and nothing that changes the environment (`export`, `set`,
# an assignment, a prefix): the list describes these tools in the environment
# the call found. Apart from `tee`, a command on it writes a file only through
# a redirection, which is what lets `_call_writes_a_file` be decided from text.
_READS_OR_PRINTS = {"echo", "printf", "grep", "egrep", "fgrep", "cat", "head",
                    "tail", "wc", "true", ":", "tasks", "test", "[", "tee", "cd",
                    "pwd", "ls", "sleep", "date", "cut", "tr", "nl"}
# Commands that start a program they are CONFIGURED with (a git hook or pager,
# the verify command the `tasks` CLI runs): read/print only while the call
# itself writes no file that could be that program.
_RUNS_CONFIGURED_PROGRAMS = {"git", "tasks"}
_NOT_A_FILE = {"/dev/null", "/dev/stdout", "/dev/stderr"}
_WORD_END = set(" \t\n;&|()<>")


def _call_writes_a_file(text: str, cmds) -> bool:
    """Can this call write a file? Post-D6 run 2 (codex): the first form looked
    for the output redirections written as their own word, and missed the
    read-write operator, an operator glued to the word before it and every
    other spelling. This asks the opposite question: `tee`, or ANY unquoted `>`
    in the call's text, is a write, unless what follows it is the null device
    or another descriptor (`2>&1`, `>/dev/null`, `>&-`). A `>` that is no
    redirection at all (a comparison inside `[[ … ]]`) counts as a write too:
    the answer may only err toward "writes". A substitution opens a quoting
    context of its own — `"$(echo x > f)"` writes although the `>` stands
    between double quotes — so every substitution body is scanned as its own
    text, however deep."""
    import command_guard as cg
    if any(posixpath.basename(h.replace("\\", "/")) == "tee" for h, _seg, _env in cmds):
        return True
    pending, scanned = [text], 0
    while pending:
        part = pending.pop()
        scanned += 1
        if scanned > 512 or _has_unquoted_output_operator(part):
            return True                               # too deep to follow counts as a write
        pending.extend(b for b in cg._command_substitutions(part) if b.strip())
    return False


def _has_unquoted_output_operator(text: str) -> bool:
    i, n, quote = 0, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote in ('"', "$'") and i + 1 < n:
                i += 2
                continue
            if c == quote[-1]:
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c in "\"'":
            quote = "$'" if c == "'" and i and text[i - 1] == "$" else c
            i += 1
            continue
        if c != ">":
            i += 1
            continue
        j = i + 1
        if j < n and text[j] in ">|":
            j += 1
        to_descriptor = j < n and text[j] == "&"
        if to_descriptor:
            j += 1
        while j < n and text[j] in " \t":
            j += 1
        k = j
        while k < n and text[k] not in _WORD_END:
            k += 1
        target = re.sub(r"[\"'\\\\]", "", text[j:k])
        if not (target in _NOT_A_FILE or (to_descriptor and re.fullmatch(r"\d+|-", target))):
            return True
        i = max(k, i + 1)
    return False


# `${NAME=word}` / `${NAME:=word}` assign as they expand; an arithmetic context
# can assign too: `(( … ))`, `$(( … ))`, the old `$[ … ]`, an array subscript.
_ASSIGNING_EXPANSION = re.compile(r"\$\{!?[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?:?="
                                  r"|\$\{[^}\n]*\[[^\]\n]*=")
_ARITHMETIC = re.compile(r"\(\(|\$\[")


def _call_changes_the_environment(text: str, cmds) -> bool:
    """Does this call change the environment its own commands run in? The
    read/print list describes its tools in the environment the call FOUND, so
    any change takes the whole call off it. Post-D6 run 2 (codex): an
    assignment word and a prefix were seen; a listed builtin that sets a
    variable (`printf -v`) was not. Counted now, as a class: an assignment on
    its own, an environment prefix, `printf -v`, an expansion that assigns
    (`${X:=…}`), an arithmetic expression. Every other builtin that sets a
    variable (`export`, `read`, `declare`, `set`, `hash`, …) is simply not on
    the list."""
    import command_guard as cg
    if _ASSIGNING_EXPANSION.search(text) or _ARITHMETIC.search(text):
        return True
    for head, segment, env in cmds:
        if env:
            return True                               # a prefix, or an assignment on its own
        if posixpath.basename(head.replace("\\", "/")) == "printf":
            words = [t[2] for t in cg._lex(segment)][1:]
            if words and words[0].startswith("-v"):
                return True
    return False


_GIT_INERT = {"commit", "log", "show", "diff", "status", "add", "tag", "notes",
              "blame", "ls-files", "rev-parse", "cat-file", "branch"}
_MKDIR_WORD = re.compile(r"(^|[^\w])mkdir($|[^\w])")


# A head `_commands` reports when a wrapper swallowed the whole segment (`su`
# with only its operand, a quoted heredoc as that operand): something runs, and
# the helper cannot name it.
_UNREADABLE_HEAD = "\x00unreadable"
# ... and for a segment that only ASSIGNS: nothing runs, but what the later
# commands of the call start is no longer what the helper knows.
_ASSIGNMENT_HEAD = "\x00assignment"
# Words that open, close or join a compound command; a segment made only of
# these and of assignments runs nothing of its own.
_SHELL_KEYWORDS = {"then", "do", "else", "elif", "if", "fi", "done", "esac",
                   "while", "until", "{", "}", "(", ")", "!", ";;"}


def _runs_nothing(segment: str) -> bool:
    """Only keywords and assignments. An assignment's value may span words
    (`X=$(printf '%s' a b)`); what the substitution runs is found by the
    substitution scan, as its own command."""
    import command_guard as cg
    toks = [t[2] for t in cg._lex(segment)]
    i = 0
    while i < len(toks):
        tok = toks[i]
        i += 1
        if tok in _SHELL_KEYWORDS:
            continue
        if not cg._ASSIGN.match(tok):
            return False
        depth = tok.count("(") - tok.count(")")
        while depth > 0 and i < len(toks):
            depth += toks[i].count("(") - toks[i].count(")")
            i += 1
    return True


def _git_only_reads(segment: str) -> bool:
    """`git commit/log/show/diff/…`: the subcommand is found past git's global
    options (`git -C dir status`); a `-c`/`--config-env`/`--exec-path` can make
    git run a program (`git -c alias.x='!mkdir …' x`), so it never counts."""
    import command_guard as cg
    words = [t[2] for t in cg._lex(segment)][1:]
    if any(w == "-c" or w.startswith(("-c", "--config-env", "--exec-path")) for w in words):
        return False
    if any(w.startswith("--output") for w in words):
        return False                              # `git log --output=FILE` writes a file
    verb = cg._git_after_globals(words)
    return bool(verb) and verb[0] in _GIT_INERT


def _may_run_mkdir(head: str, segment: str) -> bool:
    if _names_mkdir(head):
        return True
    name = posixpath.basename(head.replace("\\", "/"))
    if name in _INERT_HEADS:
        return False                              # a literal read/print command
    if _UNRESOLVED.search(head) or "`" in head:
        return True                               # the name is only known at run time
    if not _MKDIR_WORD.search(re.sub(r"[\"'\\\\]", "", segment)):
        return False
    if name == "git":
        return not _git_only_reads(segment)
    return True


# Python options that take no value and name no program (`-c` and `-m` name
# one; `-X`/`-W` take a value).
_PY_PLAIN_FLAGS = re.compile(r"-[BEIOSbdqsuv]+")
_HEREDOC_QUOTED_TAG = re.compile(r"^<<-?['\"]")
_STDIN_FROM_ELSEWHERE = re.compile(r"^0?<")


def _program_is_a_quoted_heredoc(name: str, segment: str) -> bool:
    """Does this interpreter read its PROGRAM from a quoted heredoc? Post-D6
    run 1 (codex): looking only for a quoted heredoc on the line also exempted
    `python3 -c '<program>' <<'X'`, where the heredoc is the program's INPUT and
    the program — free to run what the call wrote — comes from the argument.
    The program is the heredoc only when the interpreter is given no program any
    other way: its words are none at all or begin with `-` (the stdin marker;
    what follows is that program's own argv), python's plain flags aside, and
    stdin is not redirected from anywhere else."""
    import command_guard as cg
    toks = cg._lex(segment)[1:]
    words, heredoc, k = [], False, 0
    while k < len(toks):
        raw, _start, value, quoted = toks[k]
        k += 1
        if _HEREDOC_QUOTED_TAG.match(raw):            # `<<'PY'`
            heredoc = True
        elif raw in ("<<", "<<-") and k < len(toks) and toks[k][3]:
            heredoc = True                            # `<< 'PY'`
            k += 1
        elif _STDIN_FROM_ELSEWHERE.match(raw) and not quoted:
            return False                              # `< file`, `<<< s`, an unquoted heredoc
        elif cg._REDIR.match(raw) and not quoted:     # `> out`, `2>&1`
            if cg._REDIR.match(raw).end() == len(raw):
                k += 1                                # the target is the next word
        elif re.match(r"^&>", raw) and not quoted:
            if len(raw.rstrip(">")) == 1:
                k += 1
        else:
            words.append(value)
    if not heredoc:
        return False
    for w in words:
        if w == "-":
            return True                               # the rest is the program's argv
        if name in ("python", "python3") and _PY_PLAIN_FLAGS.fullmatch(w):
            continue
        return False                                  # `-c`, `-e`, `-m`, a script file
    return True


def _only_reads_or_prints(head: str, segment: str, env: bool = False,
                          call_writes: bool = False) -> bool:
    """Is this a command the helper KNOWS only reads, prints or writes text, so
    that it cannot run what another command of the same call wrote? The list
    (`_READS_OR_PRINTS`, and git's read subcommands) is closed on purpose: a
    shell, a sourcer, `make`, a program the helper has never heard of — anything
    else could. It holds for a tool named BARE, in the environment the call
    found, so three things take a command off it:
      * an environment prefix or an assignment earlier in the call (`PAGER=…
        git log`: the environment decides what the tool starts);
      * a path-qualified name when the call writes a file — the name may be the
        very file the call wrote, called like a listed tool;
      * git and the `tasks` CLI when the call writes a file — they start
        programs they are configured with (a hook, the verify command).
    One addition: a non-shell interpreter whose PROGRAM is a quoted heredoc
    (`python3 - <<'PY'`, see `_program_is_a_quoted_heredoc`), the bound this
    guard declares — what that program does is not shell text. What remains
    out of sight is state from an EARLIER call (a hook, a file on PATH planted
    before): the guard keeps no state between calls."""
    import command_guard as cg
    if not head.strip("(){};!"):
        return True                               # a bare grouping token
    if env:
        return False
    spelled = head.replace("\\", "/")
    name = posixpath.basename(spelled)
    if call_writes and ("/" in spelled or name in _RUNS_CONFIGURED_PROGRAMS):
        return False
    if name in cg._INTERPRETERS - cg._SHELLS:
        return _program_is_a_quoted_heredoc(name, segment)
    if name in _READS_OR_PRINTS:
        return True
    return name == "git" and _git_only_reads(segment)


def runs_mkdir_at_command_position(command: str) -> bool:
    """True when some command the shell runs may BE mkdir; True as well when the
    walk cannot tell (any failure) — it can only narrow the old text trigger.

    Impl panel round 2 (opus), a regression against 1.5.45: a mkdir word that is
    only MENTIONED — in masked data, or among a read/print command's arguments —
    was taken for inert without asking what ELSE the call runs. A script written
    through a file-sink heredoc or by echo/printf and then run by a later
    command, a read command's output piped into a shell, a quoted heredoc whose
    reader the helper cannot name: all allowed, all refused by 1.5.45. So a
    mention is inert only while EVERY command of the call is one the helper
    knows to read, print or write text (`_only_reads_or_prints`); with anything
    else in the call the released answer stands."""
    try:
        cmds = _commands(command)
        if any(_may_run_mkdir(h, seg) for h, seg, _env in cmds):
            return True
        text = _shell_text(command)
        if _call_changes_the_environment(text, cmds):
            return True
        writes = _call_writes_a_file(text, cmds)
        return not all(_only_reads_or_prints(h, seg, env, writes) for h, seg, env in cmds)
    except Exception:
        return True


def may_be_inside(command: str, project: str) -> bool:
    if not runs_mkdir(command):
        return False                              # the `$'…'` trigger: no mkdir at all
    if not runs_mkdir_at_command_position(command):
        return False                              # mkdir only MENTIONED (task 110, item 31)
    if not names_a_task_dir(command):
        return False                              # an ordinary mkdir (task 085 G2)
    # Only a SIMPLE command is ever judged (task 080 round 2). The judgment reads
    # the filesystem BEFORE the command runs, so any earlier step — `ln -s …;`,
    # `cd … &&`, a `$(…)` — could repoint the path it judged. Expansion anywhere,
    # or a newline, likewise means shlex does not see what the shell will run.
    if "\n" in command or "\r" in command or _SHELL_EXPANDS & set(command):
        return True
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return True
    if not tokens or posixpath.basename(tokens[0].replace("\\", "/")) != "mkdir":
        return True
    if any(set(t) <= _OPERATORS for t in tokens):
        return True
    targets = [t for t in tokens if _TASK_DIR_ANYCASE.search(_collapsed(t))]
    if not targets:
        # The hook's trigger matched text shlex does not return as one token
        # (e.g. spread across quoting) — cannot judge, keep the guard.
        return True
    for t in targets:
        if t.startswith("~") or not _is_absolute(t):
            return True
        if _lexically_inside(t, project) or _physically_inside(t, project):
            return True
    return False


def path_may_be_inside(path: str, project: str) -> bool:
    """`--path` (task 171, Guard 0): is this FILE path inside the project, or may it be?
    The same inside test a task-directory target gets — lexical or physical — and the
    same answer for a path that cannot be placed against the project (relative, `~`)."""
    if not path or path.startswith("~") or not _is_absolute(path):
        return True
    return _lexically_inside(path, project) or _physically_inside(path, project)


def write_may_create_inside(path: str, project: str) -> bool:
    """`--creates` (task 171, Guard 0): would a `Write` of this path CREATE a file inside
    the project — or may it? The tool that opens the path is not ours to know, so the
    path is read both ways: as the kernel reads it (links followed, then `..`; a missing
    directory is taken as it will be made) and as text (collapsed first). A creation
    inside the project under EITHER reading counts; a file that exists is a rewrite. So
    `<link into the project>/../.agent/tasks/N/task.md` with a decoy at the place its
    text collapses to is still a creation (impl panel r2), and a rewrite of an existing
    record under any lexical spelling is not. A path that cannot be placed (relative,
    `~`) is judged on existence alone, as the guard always did."""
    if not path:
        return True
    if path.startswith("~") or not _is_absolute(path):
        return not os.path.isfile(path)
    for reading in (os.path.realpath(path), _canon(path)):
        if not os.path.isfile(reading) and (
                _lexically_inside(reading, project) or _physically_inside(reading, project)):
            return True
    return False


def main() -> int:
    # The command arrives on STDIN, never in the environment (task 080: Git Bash,
    # up to 1.5.47, rewrote an env value starting with `/`; stdin carries the
    # command byte for byte everywhere).
    command = sys.stdin.read()
    project = os.environ.get("PB_PROJECT", "")
    if not command or not project:
        return 1
    if sys.argv[1:] == ["--path"]:                # stdin is one file path, not a command
        return 1 if path_may_be_inside(command, project) else 0
    if sys.argv[1:] == ["--creates"]:
        return 1 if write_may_create_inside(command, project) else 0
    return 1 if may_be_inside(command, project) else 0


if __name__ == "__main__":
    sys.exit(main())
