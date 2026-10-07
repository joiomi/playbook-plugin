#!/usr/bin/env python3
"""command_guard — the destructive/outward-command interlock (the missing
deterministic safety layer).

The sandbox contains filesystem blast radius; the code-edit gate stops untracked
code; the close contract catches under-leveling at close. The remaining hole is a
DANGEROUS or IRREVERSIBLE shell command running before any of those can help —
`rm -rf /`, `git push --force`, `curl | sh`, a DB `DROP`. This classifies the
Bash command a PreToolUse hook is about to run and BLOCKS the unambiguous
high-blast ones until they are explicitly acknowledged.

Design for "safe without new problems":
  * CONSERVATIVE — only high-confidence, unambiguous patterns, matched at a
    command position (so `echo "rm -rf /"` / `grep "DROP TABLE"` do NOT trip it),
    with narrow scope (a relative `rm -rf ./build` is fine; only dangerous
    targets flag; `--force-with-lease` is allowed, only `--force`/`-f` flags).
  * A COMMAND POSITION IS FOUND THROUGH WRAPPERS AND THEIR OPTIONS (task 077).
    `sudo -u root rm -rf /`, `timeout 5 rm -rf /`, `env -S 'rm -rf /'` and
    `curl … | nice -n 19 bash` all reach the same rules as their bare forms,
    because `_WRAPPERS` + `_walk_prefix` know each wrapper's option arity. Two
    things that walk deliberately does NOT do: it never skips a token that is not
    a known option or a known option's value (scanning past the command is how a
    guard starts blocking `sudo grep -rn "rm -rf /" /etc`), and it treats a
    wrapper's terminal/query mode as non-executing (`sudo --version rm -rf /`
    prints a version and deletes nothing). An unknown option that takes a value
    leaves that value at the head and the segment reads as safe — the guard
    under-blocks there, which is the correct direction to fail for a layer whose
    false positives a user cannot route around.

RECORDED DECISION (task 077, owner-reversible) — a GENERIC pipe into a shell,
`cat evil.sh | sh`, is NOT blocked; the pipe rule stays downloader-specific.
`curl … | sh` is unambiguous because the code is remote and unreviewed; a LOCAL
file may be anything, the guard cannot see inside it, and blocking that shape
trades a real bypass for a routine false positive. The heredoc half is already
covered — segment rules are line-split and never masked, so a heredoc body line
`rm -rf /` blocks on its own. A project that wants the stricter rule turns it on
without a release via `dangerous_commands` (the regex is in docs/configuration.md).

  * FAIL-OPEN on any internal error (a broken guard must never wedge a session);
    FAIL-CLOSED on a match (block until acknowledged).
  * ACKNOWLEDGE path: `PLAYBOOK_ALLOW_DANGEROUS=1` IN THE HOOK'S OWN ENVIRONMENT
    (the operator's shell / harness env, not a command-line prefix) lets a human-confirmed command
    through; config `command_guard: false` disables the guard; config
    `dangerous_commands: [regex,...]` adds project-specific patterns.

The threat model is the AGENT'S MISTAKE (running something dangerous it didn't
weigh), not an adversary — so a deliberate ack is enough; the point is that no
dangerous command runs by accident.

`classify_command` is a pure function, spec'd by the fixtures in
tests/test_command_guard.py — a decision fixture set (dangerous MUST block, safe
lookalikes MUST allow). Stdlib only. Dev/enforcement path.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import sys

# Statement separators: each becomes its own command position. Single `|` too,
# so `foo | rm -rf /` still sees `rm` at a command position.
# Separators are recognised OUTSIDE quotes only. Splitting the raw string first
# cut an option value in half (so a command hidden behind it never reached a
# command position) and blocked a separator that was merely DATA inside an echo
# string (impl panel round 2, both directions from the same defect).
_SEP_OPS = ("&&", "||", ";", "\n", "|", "&")


def _strip_comments(text):
    """Drop an unquoted `#` word and the rest of its line. Without this, an
    inert `echo ok # <anything>` was classified as if the comment ran (impl
    panel round 4, sol:medium)."""
    out, i, n, quote, word_start = [], 0, len(text), "", True
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == quote:
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(c)
            out.append(text[i + 1])
            i += 2
            word_start = False
            continue
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            word_start = False
            continue
        if c == "#" and word_start:
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(c)
        word_start = c.isspace() or c in ";|&("
        i += 1
    return "".join(out)


def _is_redirection_char(text, i, hit):
    """Is the `&`/`|` at `i` part of a redirection operator rather than a command
    separator? `2>&1`, `>&2`, `<&3`, `&>f`, `&>>f`, `>|f`. Impl panel round 1
    (sonnet): splitting `eval 2>&1 'rm -rf /'` at that `&` left eval without its
    argument, so the command was allowed (1.5.45 too)."""
    prev = text[i - 1] if i > 0 else ""
    if hit == "&":
        return prev in "<>" or text.startswith(">", i + 1)
    if hit == "|":
        return prev == ">"
    return False


def _split_segments_keep(text, ops=_SEP_OPS):
    """Like `_split_segments`, but keeps each segment's trailing separator so the
    text can be rebuilt byte-for-byte."""
    out, buf, sep, i, n, quote = [], [], "", 0, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(text[i + 1])
            i += 2
            continue
        if c in "\"'":
            quote = c
            buf.append(c)
            i += 1
            continue
        hit = next((op for op in ops if text.startswith(op, i)), None)
        if hit and _is_redirection_char(text, i, hit):
            hit = None
        if hit:
            out.append(("".join(buf), hit))
            buf = []
            i += len(hit)
            continue
        buf.append(c)
        i += 1
    out.append(("".join(buf), ""))
    return out


def _split_segments(text, ops=_SEP_OPS):
    out, buf, i, n, quote = [], [], 0, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(text[i + 1])
            i += 2
            continue
        if c in "\"'":
            quote = c
            buf.append(c)
            i += 1
            continue
        hit = next((op for op in ops if text.startswith(op, i)), None)
        if hit and _is_redirection_char(text, i, hit):
            hit = None
        if hit:
            out.append("".join(buf))
            buf = []
            i += len(hit)
            continue
        buf.append(c)
        i += 1
    out.append("".join(buf))
    return out
# ── the wrapper grammar (task 077) ────────────────────────────────────────────
# A WRAPPER delegates to the command that follows it. Stripping only the bare
# wrapper token left every optioned form unguarded — `sudo rm -rf /` blocked while
# `sudo -u root rm -rf /` ran — because the option token then sat where the
# command should be and every rule here anchors at position 0. So each wrapper
# declares the little grammar it actually has:
#
#   val_short — short options whose value is the NEXT token, but only when the
#               option ends its cluster with nothing attached: `-n 19` takes
#               `19`; `-n19`, `-o0` and `--long=v` carry their own value and take
#               nothing (eating the next token there would swallow the command).
#   val_long  — long options that take a value (`--signal KILL` / `--signal=KILL`).
#   terminal  — query/help modes: the wrapper PRINTS and runs nothing, so the rest
#               of the segment is not a command. `sudo --version rm -rf /` deletes
#               nothing, and a guard that blocked it would be wrong.
#   operands  — leading non-option operands consumed before the command
#               (`timeout 5 …`, `chrt 10 …`). `--` ends OPTIONS, not operands.
#   split     — options whose value IS a command line and must be classified
#               rather than consumed (`env -S 'rm -rf /'`).
#
# Everything unknown is left alone: an unrecognised option that takes a value
# leaves that value at the head and the segment reads as safe. That is the same
# under-blocking the guard already had, and it is the right direction to fail for
# a layer whose false positives a user cannot route around.


def _wspec(val_short="", val_long=(), terminal=(), operands=0,
           split_short="", split_long=(), operand_satisfied_by=(),
           split_positional=False, bare_dash=False, positional_stop=(),
           shell_args=False):
    return {
        # `su`/`runuser` (su mode): after the positional USER, every remaining
        # argument goes to the user's SHELL, so a `-c` there is a command string
        # (task 085 round 2, T3).
        "shell_args": shell_args,
        # words that END a split-positional template (`parallel … ::: args`).
        "positional_stop": set(positional_stop),
        # `su -` / `runuser -`: a lone `-` is the LOGIN flag, not an operand
        # (task 085 G1 — reading it as the user moved the command position).
        "bare_dash": bare_dash,
        "sat": set(operand_satisfied_by),
        "split_positional": split_positional,
        "val_short": set(val_short),
        "val_long": set(val_long),
        "terminal": set(terminal),
        "operands": operands,
        "split_short": set(split_short),
        "split_long": set(split_long),
    }


_WRAPPERS = {
    # privilege
    "sudo": _wspec(
        val_short="ugUCprtTRDh",
        val_long=("--user", "--group", "--other-user", "--close-from", "--prompt",
                  "--role", "--type", "--chroot", "--chdir", "--host",
                  "--command-timeout"),
        # `-h` is NOT terminal: sudo takes `-h host`, and treating it as a
        # query mode made `sudo -h localhost rm -rf /` read as non-executing
        # (impl panel round 1, grok #1). `--help` alone is the query form.
        terminal=("-V", "--version", "--help", "-l", "--list",
                  "-v", "--validate", "-K", "--remove-timestamp")),
    "doas": _wspec(val_short="uC", val_long=(),
                   terminal=("-L", "-V", "-h", "--help", "--version")),
    # environment / scheduling / buffering
    # `--block-signal`/`--default-signal`/`--ignore-signal` take an OPTIONAL
    # value that is only ever attached with `=`, so modelling them as
    # value-taking swallowed the command (impl panel round 1, sol:high #3).
    "env": _wspec(val_short="uC",
                  val_long=("--unset", "--chdir"),
                  terminal=("--help", "--version"),
                  split_short="S", split_long=("--split-string",)),
    "nice": _wspec(val_short="n", val_long=("--adjustment",),
                   terminal=("--help", "--version")),
    "ionice": _wspec(val_short="cnp", val_long=("--class", "--classdata", "--pid"),
                     terminal=("-h", "--help", "-V", "--version")),
    "chrt": _wspec(val_short="pTDP", operands=1,
                   terminal=("-h", "--help", "-V", "--version")),
    "stdbuf": _wspec(val_short="ioe",
                     val_long=("--input", "--output", "--error"),
                     terminal=("--help", "--version")),
    "timeout": _wspec(val_short="ks", val_long=("--kill-after", "--signal"),
                      operands=1, terminal=("--help", "--version")),
    "setsid": _wspec(terminal=("-h", "--help", "-V", "--version")),
    "nohup": _wspec(terminal=("--help", "--version")),
    "time": _wspec(val_short="fo", val_long=("--format", "--output"),
                   terminal=("-V", "--version", "--help")),
    # `-e`/`-i` take an OPTIONAL value that is only ever attached, so listing
    # them as value-taking swallowed the command (impl panel round 2, sol:medium).
    "xargs": _wspec(val_short="nPIadLsDE",
                    val_long=("--max-args", "--max-procs", "--replace",
                              "--delimiter", "--arg-file", "--eof",
                              "--max-chars", "--max-lines", "--process-slot-var"),
                    terminal=("--help", "--version")),
    # shell builtins that delegate
    "command": _wspec(terminal=("-v", "-V")),
    "builtin": _wspec(),
    # `watch '<cmd>'` hands its argument to `sh -c`, so a quoted operand is a
    # command STRING, not a token (impl panel round 5, sol:high).
    "watch": _wspec(val_short="n", val_long=("--interval",),
                    terminal=("--help", "--version"),
                    split_positional=True),
    # GNU parallel's first positional is a command TEMPLATE, like `watch '<cmd>'`
    # (task 085 G1: `parallel 'rm -rf /' ::: 1` was allowed).
    "parallel": _wspec(val_short="jPN", val_long=("--jobs", "--delay", "--timeout",
                                                 "--retries", "--sshlogin",
                                                 "--max-args", "--max-procs"),
                       terminal=("--help", "--version"),
                       split_positional=True,
                       positional_stop=(":::", "::::", ":::+", "::::+")),
    # `trap '<command>' SIGNAL` stores a command string that runs on the signal.
    "trap": _wspec(split_short="", split_long=(), operands=0),
    # `eval` delegates to a STRING, so its remainder is classified, not walked.
    "eval": _wspec(),
    "exec": _wspec(val_short="a", val_long=()),
    # control-flow keywords that can precede a command in a split segment
    # Reserved words that INTRODUCE a command: the next word is the command.
    "then": _wspec(),
    "do": _wspec(),
    "else": _wspec(),
    "elif": _wspec(),
    "if": _wspec(),
    # `case x in  x) <cmd>;; esac` — the word and `in` are operands, the arm
    # pattern ends with `)` and is skipped like a function header.
    "case": _wspec(operands=2),
    "esac": _wspec(),
    "done": _wspec(),
    "fi": _wspec(),
    "while": _wspec(),
    "until": _wspec(),
    # Privilege wrappers of the same class as sudo/doas (impl panel round 3).
    "pkexec": _wspec(val_long=("--user",), terminal=("--version", "--help")),
    # `-c`/`--command` is a COMMAND STRING, not a consumed value, and the user
    # may be a positional operand: `su root -c '…'` (impl panel round 4).
    # Task 085 G1: `-s/--shell` and `-w/--whitelist-environment` take a value,
    # and a lone `-` is the login flag — each used to be read as the user
    # operand, moving the command position onto the user name.
    # Task 110 (085 round 3, V3): `--supp-group` is `-G`'s long form and takes a
    # value; without it `su --supp-group wheel root -c '…'` read `wheel` as the user.
    "runuser": _wspec(val_short="ugGsw", val_long=("--user", "--group", "--shell",
                                                   "--whitelist-environment",
                                                   "--supp-group"),
                      terminal=("--version", "--help"), operands=1,
                      operand_satisfied_by=("u", "--user"),
                      split_short="c", split_long=("--command", "--session-command"),
                      bare_dash=True, shell_args=True),
    "su": _wspec(val_short="gGsw", val_long=("--group", "--shell",
                                             "--whitelist-environment", "--supp-group"),
                 terminal=("--version", "--help"), operands=1,
                 split_short="c", split_long=("--command", "--session-command"),
                 bare_dash=True, shell_args=True),
}

# ── lexing and naming (impl panel round 1) ────────────────────────────────────
# Round 1 shipped a whitespace tokenizer and ONE normalisation site. The panel
# produced 31 vectors that walked through, and every one of them was really two
# defects: quoting is invisible to a whitespace split (`sudo -p 'Password: ' rm
# -rf /` puts `rm` where an option value was expected), and a command can be
# NAMED many ways (`'rm'`, `"/bin/rm"`, `r\m`, `~/rm`, `$HOME/bin/rm`) while the
# rules all anchor on the bare word. So: one lexer, one naming function, used
# everywhere a command position is decided.

# `\UXXXXXXXX` (up to 8 hex) is bash too — task 085 round 3 (U5): without it
# `$'\U00000072'm -rf /` read as `U00000072m` and was allowed.
_ANSI_C = re.compile(r"\\(x[0-9A-Fa-f]{1,2}|[0-7]{1,3}|u[0-9A-Fa-f]{1,4}|U[0-9A-Fa-f]{1,8}|.)")
_ANSI_SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
                "f": "\f", "v": "\v", "e": "\x1b", "\\": "\\", "'": "'", '"': '"'}


def _ansi_c_decode(body):
    def one(m):
        tok = m.group(1)
        try:
            if tok[0] == "x":
                return chr(int(tok[1:], 16))
            if tok[0] in "uU":
                return chr(int(tok[1:], 16))
            if tok[0] in "01234567":
                return chr(int(tok, 8))
        except (ValueError, OverflowError):
            return tok
        return _ANSI_SIMPLE.get(tok, tok)
    return _ANSI_C.sub(one, body)


def _lex(s):
    """(raw, start, value, quoted) per token. Quote- and escape-aware, handles
    ANSI-C `$'…'` and shell line continuations, and is FORGIVING — an unbalanced
    quote simply runs to the end rather than raising, because a classifier that
    throws fails OPEN. `quoted` records whether any part of the token was inside
    quotes, which is what lets an operator be told from a string containing one.
    """
    toks = []
    i, n = 0, len(s)
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        buf = []
        kinds = set()
        while i < n:
            c = s[i]
            if c == "\\" and i + 1 < n and s[i + 1] == "\n":
                i += 2                                 # line continuation
                continue
            if c.isspace():
                break
            if c == "\\" and i + 1 < n:
                buf.append(s[i + 1])
                i += 2
                continue
            if c == "$" and i + 1 < n and s[i + 1] == "'":
                # ANSI-C quoting DECODES its escapes: `$'\x72\x6d'` is `rm`.
                # Recognising the syntax without decoding it (round 3) just moved
                # the bypass one layer in (impl panel round 4, sol:high).
                j = i + 2
                body = []
                while j < n and s[j] != "'":
                    if s[j] == "\\" and j + 1 < n:
                        body.append(s[j])
                        body.append(s[j + 1])
                        j += 2
                        continue
                    body.append(s[j])
                    j += 1
                buf.append(_ansi_c_decode("".join(body)))
                kinds.add("'")
                i = j + 1
                continue
            if c in "\"'":
                q = c
                kinds.add(q)
                i += 1
                while i < n and s[i] != q:
                    if q == '"' and s[i] == "\\" and i + 1 < n:
                        buf.append(s[i + 1])
                        i += 2
                        continue
                    buf.append(s[i])
                    i += 1
                i += 1                                 # closing quote, or end
                continue
            buf.append(c)
            i += 1
        toks.append((s[start:i], start, "".join(buf), "".join(sorted(kinds))))
    return toks


# A command NAME never contains whitespace. That single rule is what keeps this
# from turning data into commands: `'rm'` is an obfuscated name, `"rm -rf /"` is
# a string, and only the first one is renamed.
_PATH_PREFIX = re.compile(r"^(?:[A-Za-z0-9_.+~$@{}-]*/)+")
_LEAD_GROUP = "({!"
_TRAIL_GROUP = ")};"


def _command_name(value):
    """The bare command a token names, or None when the token is not a name."""
    if not value:
        return None
    v = value.lstrip(_LEAD_GROUP).rstrip(_TRAIL_GROUP)
    if not v or any(ch.isspace() for ch in v):
        return None
    v = _PATH_PREFIX.sub("", v)
    return v or None


# A leading token that groups or negates, rather than naming a command.
_GROUPERS = ("(", "{", "!", "&&", "||")
_ASSIGN = re.compile(r"^\w+\+?=")


def _payload_from(value, rest_tokens):
    """A split-string option's value IS a command line, and the operands that
    follow it are that command's arguments — `env -S 'bash -c' '<script>'` runs
    the script. Rebuild one invocation with the boundaries preserved (impl panel
    round 2, sol:high: merging them as raw text corrupted both)."""
    parts = [value]
    for tok in rest_tokens:
        try:
            parts.append(shlex.quote(tok))
        except Exception:
            parts.append(tok)
    return " ".join(p for p in parts if p)


def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    return s


# ── GNU parallel (task 110, PLAN S11 G2-01 + 085 round 3 V2/V6) ──────────────
# `parallel` runs its TEMPLATE once per input argument: the argument is APPENDED,
# or substituted for a replacement string (`{}`, `{.}`, `{/}`, `{1}`, an `-I`
# string); with several `:::` sources the jobs are their product, in order; with
# an EMPTY template every argument IS the command. The generic split-positional
# walk stopped at the first `:::` and dropped the arguments, so `parallel rm -rf
# ::: /etc /usr` was classified as `rm -rf` — a regression against 1.5.45, which
# read the whole line. Arguments read at run time (`::::`, `-a FILE`) are not
# known: the same bound as a plain variable target.
# Option table: GNU parallel's own `GetOptions` spec (`Getopt::Long::Configure(
# "bundling", "require_order")`; `=s`/`=i`/`=f` take a value, `:s`/`:f` take an
# OPTIONAL one), plus the value options later releases added. Impl panel round 1
# (task 110): `--plus` is a FLAG and was listed as taking a value, so
# `parallel --plus rm -rf / ::: x` read `/` as the template; `--filter`,
# `--compress-program` and `--sql` take values and were missing. Two readings
# are judged wherever the spec leaves the grammar open — an OPTIONAL value (`-i`,
# `--replace`, `-e`, `--eof`, `-l`, `--max-lines`: the next word may or may not
# be the value) and a long option the table does not know (a newer release's
# value option reads like a flag to an older table) — and the command blocks if
# EITHER reading runs a dangerous job.
_PARALLEL_VAL_SHORT = set("DIUjSBWHJPdsaEnNCL")
_PARALLEL_OPT_SHORT = set("iel")                       # `:s` / `:f`
_PARALLEL_FLAG_SHORT = set("mXvkgu0qMTrptxY")
_PARALLEL_TERMINAL_SHORT = set("hV")
_PARALLEL_VAL_LONG = {
    "--debug", "--joblog", "--results", "--result", "--res", "--parens", "--rpl",
    "--extensionreplace", "--er", "--basenamereplace", "--bnr", "--dirnamereplace",
    "--dnr", "--basenameextensionreplace", "--bner", "--seqreplace", "--slotreplace",
    "--jobs", "--delay", "--sshdelay", "--ssh-delay", "--load", "--nice", "--timeout",
    "--tagstring", "--tag-string", "--ctagstring", "--sshlogin", "--sshloginfile",
    "--slf", "--return", "--trc", "--basefile", "--bf", "--workdir", "--work-dir",
    "--wd", "--tmpdir", "--tempdir", "--use-compress-program", "--compress-program",
    "--use-decompress-program", "--decompress-program", "--halt-on-error", "--halt",
    "--retries", "--arg-sep", "--argsep", "--arg-file-sep", "--argfilesep", "--trim",
    "--env", "--profile", "--recstart", "--recend", "--block", "--block-size",
    "--blocksize", "--memfree", "--memsuspend", "--max-procs", "--delimiter",
    "--max-chars", "--arg-file", "--max-args", "--max-replace-args", "--colsep",
    "--col-sep", "--semaphoretimeout", "--st", "--semaphorename", "--id", "--header",
    "--filter", "--sql", "--sqlmaster", "--sqlworker", "--sqlandworker", "--template",
    "--tmpl", "--transferfile", "--transfer-file", "--tf", "--ssh", "--group-by",
    "--shard", "--bin", "--limit", "--termseq", "--block-timeout", "--bt",
    "--process-slot-var", "--total-jobs", "--total", "--shebang-wrap-arg",
    "--match",                                        # from the model's memory, UNVERIFIED
}
_PARALLEL_OPT_LONG = {"--replace", "--eof", "--max-lines"}
_PARALLEL_FLAG_LONG = {
    "--xargs", "--resume", "--resume-failed", "--resumefailed", "--retry-failed",
    "--silent", "--keep-order", "--keeporder", "--no-keep-order", "--nokeeporder",
    "--group", "--ungroup", "--linebuffer", "--linebuffered", "--line-buffer",
    "--line-buffered", "--latest-line", "--tmux", "--tmuxpane", "--null", "--quote",
    "--plus", "--noswap", "--use-cpus-instead-of-cores", "--use-cores-instead-of-threads",
    "--use-sockets-instead-of-threads", "--shellquote", "--shell_quote", "--shell-quote",
    "--tag", "--ctag", "--color", "--color-failed", "--onall", "--nonall",
    "--filter-hosts", "--filterhosts", "--controlmaster", "--transfer", "--cleanup",
    "--ctrlc", "--noctrlc", "--compress", "--tty", "--dry-run", "--dryrun",
    "--progress", "--eta", "--bar", "--recordenv", "--record-env", "--session",
    "--plain", "--pipe", "--spreadstdin", "--robin", "--round-robin", "--roundrobin",
    "--regexp", "--regex", "--remove-rec-sep", "--removerecsep", "--rrs", "--files",
    "--files0", "--output-as-files", "--outputasfiles", "--tollef", "--gnu",
    "--xapply", "--will-cite", "--willcite", "--nn", "--nonotice", "--no-notice",
    "--no-run-if-empty", "--interactive", "--verbose", "--exit", "--semaphore",
    "--fg", "--bg", "--wait", "--shebang", "--hashbang", "--shebang-wrap",
    "--internal-pipe-means-argfiles", "--skip-first-line", "--cat", "--fifo",
    "--pipepart", "--pipe-part", "--hgrp", "--hostgroup", "--hostgroups", "--csv",
    "--tsv", "--embed", "--tee", "--fast", "--show-limits", "--showlimits",
    "--sqlandworker-only", "--lb",
}
_PARALLEL_TERMINAL = {"--help", "--version", "--number-of-cpus", "--number-of-cores",
                      "--number-of-threads", "--number-of-sockets", "--citation",
                      "--bibtex", "--minversion", "--min-version",
                      "--max-line-length-allowed"}
_PARALLEL_LITERAL_SOURCE = {":::", ":::+"}
_PARALLEL_FILE_SOURCE = {"::::", "::::+"}
_PARALLEL_SOURCES = _PARALLEL_LITERAL_SOURCE | _PARALLEL_FILE_SOURCE
_PARALLEL_REPL = re.compile(r"\{=.*?=\}|\{(\d+)?(?:\.|/|//|/\.|#|%)?\}")
# Jobs are the product of the DISTINCT values of each known source. Impl panel
# round 1: the earlier fallback past 64 kept 256 single arguments, so an argument
# past the slice — or a pair only the full product forms — was never seen; a cap
# that ALLOWS past its limit is the bypass (the 077 lesson). Past this cap the
# invocation is REFUSED (`parallel-too-many-jobs`), never classified partially.
_PARALLEL_MAX_JOBS = 512
_PARALLEL_MAX_READINGS = 16
# A sentinel payload: `classify_command` turns it into a block.
_PARALLEL_UNJUDGEABLE = "\x00parallel-unjudgeable"

# ── what the guard does NOT model in GNU parallel (task 110, owner 2026-10-05) ──
# The job lines composed below are exact in four cases only: the argument is
# APPENDED to the template, or substituted WHOLE for `{}`, for `{N}` (one
# source's value) or for an `-I`/`--replace` string. GNU parallel can also
# REWRITE an argument before it reaches the job line. That grammar is not
# modelled here, on purpose: impl panel round 2 found the composition wrong for
# every such form, and the owner chose one conservative rule over a model of it.
# What is not modelled is listed here, as DATA:
#   * `_PARALLEL_UNMODELLED_*` — options that cut an argument into columns or
#     records, pack several arguments into one job line, or switch on further
#     replacement strings;
#   * `_PARALLEL_REWRITING_REPL` — replacement strings that rewrite their
#     argument (its extension, its directory or base name, a perl expression);
#   * `_PARALLEL_HIDES_TEMPLATE_LONG` — options that rename the source
#     separators or declare a replacement string of their own, after which the
#     guard cannot tell which words of the line are the template at all.
# PROVENANCE (owner 2026-10-05): every GNU parallel table in this file — the
# option arities above and the lists below — was written from DOCUMENTATION, on
# a machine with no GNU parallel binary installed; none was run against the
# tool. One entry, `--match`, is from the MODEL'S MEMORY of recent releases and
# is UNVERIFIED (it is marked where it is listed).
# A long option spelled as a unique PREFIX of a listed one is that option (GNU
# parallel accepts abbreviations). An option no table knows at all is NOT in
# these lists: it keeps the two readings it always had. With a listed form
# present the arguments are UNKNOWN, and the command names in the template are
# all the guard can judge: see `_parallel_template_unreadable`.
_PARALLEL_UNMODELLED_LONG = frozenset({
    "--colsep", "--col-sep", "--csv", "--tsv", "--header", "--delimiter",
    "--max-args", "--max-replace-args", "--max-lines", "--xargs", "--plus",
    "--match",                                        # from the model's memory, UNVERIFIED
})
_PARALLEL_UNMODELLED_SHORT = frozenset("CdnNLlXm")
_PARALLEL_HIDES_TEMPLATE_LONG = frozenset({
    "--arg-sep", "--argsep", "--arg-file-sep", "--argfilesep", "--rpl", "--parens",
    "--extensionreplace", "--er", "--basenamereplace", "--bnr", "--dirnamereplace",
    "--dnr", "--basenameextensionreplace", "--bner",
})
_PARALLEL_REWRITING_REPL = re.compile(r"\{=.*?=\}|\{\d*(?:\.|/|//|/\.)\}")
# Anything of a replacement string's SHAPE (`{}`, `{3}`, `{.}`, a `--header`
# column name, a `--plus` form; not the shell's own `${name}`): the test for
# "this word is filled in from an argument".
_PARALLEL_ANY_REPL = re.compile(r"\{=.*?=\}|(?<!\$)\{[^{}\s]*\}")
# The second sentinel payload: the template cannot be judged without reading an
# argument the guard does not know.
_PARALLEL_UNMODELLED = "\x00parallel-unmodelled"
# The third: under an unmodelled rewriting the template runs a command that ONE
# rule judges by its operand — the download-then-run rule and its runners
# (`_DL_RUNNERS`: an interpreter, a sourcer). On its own that is no refusal; it
# is one for `_downloads_then_runs` once a download precedes it (post-D6 run 1).
_PARALLEL_RUNS_UNKNOWN = "\x00parallel-runs-unknown"
# A redirection OPERATOR (with an optional leading fd). Whatever follows it in the
# same token is the target (`2>&1`, `>/tmp/o`, `<&3`); an operator that ends the
# token takes the NEXT token as its target (`< /dev/null`). Impl panel round 1
# (sonnet): the earlier pattern also swallowed the `1` of `2>&1`, so the token
# looked target-less and the next word — `eval 2>&1 'rm -rf /'` — was skipped.
_EVAL_REDIR = re.compile(r"^(?:\d*|&)(?:<<<|<<-?|>>|>\||<>|>&|<&|&>>|&>|<|>)")


def _parallel_lists(base, table, names):
    """Is the long option `base` one of `table`? `names` is its exact name when
    an option table knows the spelling; otherwise the spelling is tried as an
    abbreviation — a prefix of a listed option."""
    if names is not None:
        return bool(names & table)
    return any(o.startswith(base) for o in table)


def _from_argument(text, repl):
    """Does `text` hold a replacement string — something GNU parallel fills in
    from an argument? (`repl` is this reading's `-I`/`--replace` string.)"""
    return bool(_PARALLEL_ANY_REPL.search(text)) or bool(repl and repl in text)


def _names_need_an_argument(text, repl, names, _depth=0):
    """Would judging the command line `text` mean reading an ARGUMENT? True when
    a command name in it is in `names` — the names some rule judges by their
    arguments — is itself filled in from an argument, or stands in a line some
    wrapper RE-PARSES with an argument in it (`eval`, `watch`, `su -c`: there the
    argument becomes code, so that line has no readable name either). Names are
    found the way every rule here finds them: per segment, through the wrapper
    walk, its payloads and substitutions."""
    if _depth > 16:
        return True                                    # cannot follow further: refuse
    text = _strip_comments(text)
    for seg in _split_segments(text):
        if not seg.strip():
            continue
        rest, executes, payloads = _walk_prefix(seg)
        for p in payloads:
            if not p:
                continue
            if p.startswith("\x00"):                   # a nested `parallel`'s own verdict
                if p != _PARALLEL_RUNS_UNKNOWN or names is _DL_RUNNERS:
                    return True
                continue
            if _from_argument(p, repl) or _names_need_an_argument(p, repl, names, _depth + 1):
                return True
        if not executes:
            continue
        lexed = _lex(rest)
        if not lexed:
            continue
        word = lexed[0][2]
        if _from_argument(word, repl) or _command_name(word) in names:
            return True
    for sub_ in _command_substitutions(text) + _herestring_payloads(text):
        if sub_.strip() and (_from_argument(sub_, repl)
                             or _names_need_an_argument(sub_, repl, names, _depth + 1)):
            return True
    return False


def _parallel_template_unreadable(lexed, start, repl, how, names):
    """The ONE conservative rule for what the guard does not model (owner ruling
    2026-10-05, task 110; impl panel round 2, classes 1 and 2). When this reading
    rewrites its arguments in an unmodelled way — an option from the tables
    above, or a rewriting replacement string in the template — the arguments are
    unknown, so only the command NAMES in the template are judged, and the
    invocation is refused (`parallel-unmodelled-arguments`) when one of them
    cannot be judged without its arguments: it is argument-judged, or it is not
    a readable name at all (no template: the arguments ARE the commands; a
    replacement string where the name should be; a hidden template). `names` is
    the set of argument-judged names asked about: `_ARGUMENT_JUDGED_HEADS` for
    the rules that refuse on their own, `_DL_RUNNERS` for the download-then-run
    rule, which reads a runner's operand only after a download."""
    if how == 2:
        return True                                    # the template cannot be located
    words = []
    for raw, _st, val, _q in lexed[start:]:
        if val in _PARALLEL_SOURCES:
            break
        words.append((raw, val))
    forms = {" ".join(v for _r, v in words), " ".join(r for r, _v in words)}
    if not how and not any(_PARALLEL_REWRITING_REPL.search(f) for f in forms):
        return False                                   # used whole: the composition is exact
    for form in sorted(forms):
        # no replacement string in the template: the argument is APPENDED
        line = form if _from_argument(form, repl) else (form + " {}").strip()
        if _names_need_an_argument(line, repl, names):
            return True
    return False


def _parallel_readings(toks, i):
    """Every way GNU parallel's option prefix can parse: a list of `(index of
    the first template/source token, replace string or None, how)`. `how` says
    how much of the reading the guard models: 0 all of it, 1 an option listed in
    `_PARALLEL_UNMODELLED_*` was seen (the arguments are rewritten), 2 one from
    `_PARALLEL_HIDES_TEMPLATE_LONG` (the template cannot be located). Readings
    that reach a terminal option (`--help`, `-V`) run nothing and are dropped.
    None when more readings exist than can be judged."""
    states: list = [(i, None, 0)]
    done: list = []
    while states:
        if len(states) + len(done) > _PARALLEL_MAX_READINGS:
            return None
        i, repl, how = states.pop()
        if i >= len(toks):
            done.append((i, repl, how))
            continue
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        nxt_is_value = nxt is not None and not nxt.startswith("-") and nxt not in _PARALLEL_SOURCES
        if t == "--":
            done.append((i + 1, repl, how))
            continue
        if t in _PARALLEL_SOURCES or not t.startswith("-") or len(t) < 2:
            done.append((i, repl, how))                # require_order: options end here
            continue
        if t.startswith("--"):
            base, eq, val = t.partition("=")
            if base in _PARALLEL_TERMINAL:
                continue
            # A spelling no table knows may be a unique PREFIX of a listed option
            # (Getopt::Long abbreviations): it is matched against the listed ones.
            known = (base in _PARALLEL_OPT_LONG or base in _PARALLEL_VAL_LONG
                     or base in _PARALLEL_FLAG_LONG)
            names = {base} if known else None
            if _parallel_lists(base, _PARALLEL_HIDES_TEMPLATE_LONG, names):
                how = 2
            elif _parallel_lists(base, _PARALLEL_UNMODELLED_LONG, names):
                how = max(how, 1)
            if base == "--replace":
                states.append((i + 1, (val or "{}") if eq else "{}", how))
                if not eq and nxt_is_value:
                    states.append((i + 2, nxt, how))
            elif base in _PARALLEL_OPT_LONG:
                states.append((i + 1, repl, how))
                if not eq and nxt_is_value:
                    states.append((i + 2, repl, how))
            elif base in _PARALLEL_VAL_LONG:
                states.append((i + (1 if eq else 2), repl, how))
            elif base in _PARALLEL_FLAG_LONG or eq:
                states.append((i + 1, repl, how))
            else:                                      # unknown: flag OR value option
                states.append((i + 1, repl, how))
                if nxt is not None:
                    states.append((i + 2, repl, how))
            continue
        letters, k = t[1:], 0                          # a bundle: `-j4`, `-kq`, `-I@@`
        branched = False
        while k < len(letters):
            ch = letters[k]
            attached = letters[k + 1:]
            if ch in _PARALLEL_TERMINAL_SHORT:
                branched = True                        # this reading runs nothing
                break
            if ch in _PARALLEL_UNMODELLED_SHORT:
                how = max(how, 1)
            if ch in _PARALLEL_VAL_SHORT:
                new = (attached or nxt) if ch == "I" else repl
                states.append((i + (1 if attached else 2), new, how))
                branched = True
                break
            if ch in _PARALLEL_OPT_SHORT:
                new = (attached or "{}") if ch == "i" else repl
                states.append((i + 1, new, how))
                if not attached and nxt_is_value:
                    states.append((i + 2, nxt if ch == "i" else repl, how))
                branched = True
                break
            if ch in _PARALLEL_FLAG_SHORT:
                k += 1
                continue
            states.append((i + 1, repl, how))          # unknown letter: flag or value
            if not attached and nxt is not None:
                states.append((i + 2, repl, how))
            branched = True
            break
        if not branched:
            states.append((i + 1, repl, how))
    return done


def _parallel_jobs(lexed, start, repl):
    """Command lines for one reading: the template on its own, then one line per
    job of the source product. The template is read twice — its words joined
    DEQUOTED (what `parallel` hands its shell) and joined RAW, quoting kept (what
    `parallel -q` and a shell-word template such as `bash -c '<cmd>'` mean; impl
    panel round 1, sol-high: dequoting lost the argument boundary, so `parallel
    bash -c 'rm -rf /' ::: a` was allowed). None when too many jobs to judge."""
    template, sources, cur = [], [], None
    for raw, _st, val, _q in lexed[start:]:
        if val in _PARALLEL_SOURCES:
            cur = [] if val in _PARALLEL_LITERAL_SOURCE else None
            sources.append(cur)
        elif sources:
            if cur is not None and val not in cur:     # distinct values: same jobs
                cur.append(val)
        else:
            template.append((raw, val))
    forms = []
    for f in (" ".join(v for _r, v in template), " ".join(r for r, _v in template)):
        if f not in forms:
            forms.append(f)
    known = [s for s in sources if s]                  # file sources are unknown
    total = 1
    for s in known:
        total *= len(s)
    if known and total * len(forms) > _PARALLEL_MAX_JOBS:
        return None
    import itertools
    jobs = [list(c) for c in itertools.product(*known)] if known else []
    payloads = [f for f in forms if f]
    for tmpl in forms:
        for job in jobs:
            if not tmpl:
                payloads.append(" ".join(job))
                continue
            line, hit = tmpl, False
            if repl and repl in line:
                line, hit = line.replace(repl, " ".join(job)), True

            def _sub(m, job=job):
                if m.group(0).startswith("{="):
                    return " ".join(job)
                n = m.group(1)
                if n and n.isdigit() and 0 < int(n) <= len(job):
                    return job[int(n) - 1]
                return " ".join(job)
            line2 = _PARALLEL_REPL.sub(_sub, line)
            hit = hit or line2 != line
            payloads.append(line2 if hit else f"{line} {' '.join(job)}")
    return payloads


def _parallel_payloads(lexed, i):
    """`(executes, payloads)` for a `parallel` invocation whose options start at
    token `i`: every job line of every reading of its options. A reading count or
    a job count past the cap yields the `_PARALLEL_UNJUDGEABLE` sentinel, and a
    reading whose template cannot be judged without its arguments the
    `_PARALLEL_UNMODELLED` one; the classifier refuses both. A third sentinel,
    `_PARALLEL_RUNS_UNKNOWN`, is information for the download-then-run rule."""
    toks = [t[2] for t in lexed]
    readings = _parallel_readings(toks, i)
    if readings is None:
        return True, [_PARALLEL_UNJUDGEABLE]
    if not readings:
        return False, []                               # every reading is `--help`/`-V`
    payloads = []
    unreadable = runs_unknown = False
    for start, repl, how in readings:
        more = _parallel_jobs(lexed, start, repl)
        if more is None:
            return True, [_PARALLEL_UNJUDGEABLE]
        for p in more:
            if p not in payloads:
                payloads.append(p)
        unreadable = unreadable or _parallel_template_unreadable(
            lexed, start, repl, how, _ARGUMENT_JUDGED_HEADS)
        runs_unknown = runs_unknown or _parallel_template_unreadable(
            lexed, start, repl, how, _DL_RUNNERS)
    if unreadable:
        # LAST: a job that blocks on its own still names its own rule
        payloads.append(_PARALLEL_UNMODELLED)
    elif runs_unknown:
        payloads.append(_PARALLEL_RUNS_UNKNOWN)
    return True, payloads


def _walk_prefix(seg):
    """Walk the wrapper/option prefix of one segment.

    Returns `(rest, executes, payloads)`:
      * `rest` — the segment text from the first token that is not part of a
        wrapper prefix, sliced out of the ORIGINAL string so quoting and spacing
        survive for the regex rules;
      * `executes` — False when a terminal/query option means the segment runs
        no command at all;
      * `payloads` — command-line strings found inside option values (`env -S`),
        for the caller to classify recursively.
    """
    s = seg.strip()
    lexed = _lex(s)
    toks = [(value, start) for _raw, start, value, _q in lexed]
    i = 0
    payloads = []
    # Termination needs no ceiling: every path below consumes at least one token,
    # and the token list is finite. A ceiling WOULD be the bypass — nest one more
    # wrapper than the cap and the walk stops short (impl panel round 1, three
    # seats; the round-1 code had one and its test used 500 against a cap of
    # 10 000, so it proved nothing).
    while i < len(toks):
        tok = toks[i][0]
        if tok in _GROUPERS or _ASSIGN.match(tok):
            i += 1                                     # consumes a token → terminates
            if _ASSIGN.match(tok):                     # `x=$(echo hi) rm -rf /`
                depth = tok.count("(") - tok.count(")")
                while depth > 0 and i < len(toks):
                    depth += toks[i][0].count("(") - toks[i][0].count(")")
                    i += 1
            continue
        name = _command_name(tok)
        if tok.endswith("()") or tok == "function":    # `f() {…}` / `function f {…}`
            i += 1
            if tok == "function" and i < len(toks):
                i += 1                                 # the name
            continue
        if (tok.endswith(")") and not tok.startswith(("(", "{")) and not lexed[i][3]
                and "$(" not in tok and "`" not in tok):
            i += 1                                     # a `case` arm: `x) <cmd>`
            continue                                   # (not a quoted/computed name: task 110 r1)
        spec = _WRAPPERS.get(name) if name else None
        if spec is None:
            break                                      # this token is the command
        if name == "trap":                             # `trap '<cmd>' SIGNAL`
            i += 1
            while i < len(toks) and toks[i][0] in ("--",):
                i += 1
            if i < len(toks) and toks[i][0] in ("-l", "-p", "--list", "--print"):
                return (s, False, payloads)            # lists traps, runs nothing
            if i < len(toks):
                payloads.append(toks[i][0])
            i = len(toks)
            break
        if name == "parallel":                         # task 110: one payload per job
            executes, more = _parallel_payloads(lexed, i + 1)
            if not executes:
                return (s, False, payloads)
            payloads.extend(more)
            i = len(toks)
            break
        if name == "eval":                             # delegates to a STRING
            i += 1
            if i < len(toks):
                rest_toks = lexed[i:]
                if rest_toks and rest_toks[0][2] == "--":
                    rest_toks = rest_toks[1:]          # option terminator
                # Task 110: bash's eval joins its DEQUOTED arguments with spaces
                # and re-parses the result; a redirection of eval itself is not
                # an argument. Taking the raw remainder kept the quotes, so the
                # head became the whole quoted string: `eval 'rm -rf /' <
                # /dev/null` was allowed (1.5.45 too).
                args, k = [], 0
                while k < len(rest_toks):
                    raw, _st, val, quoted = rest_toks[k]
                    op = None if quoted else _EVAL_REDIR.match(raw)
                    if op:
                        if op.end() == len(raw) and k + 1 < len(rest_toks):
                            k += 1                     # `< /dev/null`: skip the target too
                        k += 1
                        continue
                    args.append(val)
                    k += 1
                if args:
                    payloads.append(" ".join(args))
            i = len(toks)
            break
        i += 1                                         # the wrapper itself
        end_of_opts = False
        operands = spec["operands"]
        user_positional = False                        # T3: `su -- root -c …`
        while i < len(toks):
            t = toks[i][0]
            if not end_of_opts and t == "--":
                end_of_opts = True                     # ends OPTIONS, not operands
                i += 1
                continue
            if not end_of_opts and t == "-" and spec["bare_dash"]:
                i += 1                                 # `su -` login flag (task 085)
                continue
            if not end_of_opts and t.startswith("--") and len(t) > 2:
                base = t.split("=", 1)[0]
                if base in spec["terminal"]:
                    return (s, False, payloads)
                if base in spec["split_long"]:
                    if "=" in t:
                        payloads.append(_payload_from(
                            t.split("=", 1)[1], [v for v, _st in toks[i + 1:]]))
                    elif i + 1 < len(toks):
                        payloads.append(_payload_from(
                            toks[i + 1][0], [v for v, _st in toks[i + 2:]]))
                    i = len(toks)
                    continue
                if base in spec["val_long"] and "=" not in t:
                    if base in spec["sat"]:
                        operands = 0
                    i += 2
                    continue
                if base in spec["sat"]:                # `--user=root`
                    operands = 0
                i += 1
                continue
            if not end_of_opts and t.startswith("-") and len(t) > 1:
                if t in spec["terminal"]:
                    return (s, False, payloads)
                letters = t[1:]
                takes_next = False
                for k, ch in enumerate(letters):
                    attached = letters[k + 1:]
                    if ch in spec["split_short"]:
                        if attached:                   # value attached to the flag
                            payloads.append(_payload_from(
                                attached, [v for v, _st in toks[i + 1:]]))
                        elif i + 1 < len(toks):        # value in the next token
                            payloads.append(_payload_from(
                                toks[i + 1][0], [v for v, _st in toks[i + 2:]]))
                        i = len(toks)
                        takes_next = None              # already advanced
                        break
                    if ch in spec["val_short"]:
                        if ch in spec["sat"]:          # the operand came as an option
                            operands = 0
                        takes_next = not attached      # `-n 19` yes, `-n19`/`-o0` no
                        break
                if takes_next is None:
                    continue                           # a split option consumed the rest
                i += 1
                if takes_next:
                    i += 1
                continue
            if spec["split_positional"]:               # `watch <cmd words>`
                # joined whether or not the first word is quoted (task 085
                # round 3, U6: `watch echo ';' …` was walked as a plain echo).
                # The tool JOINS every command word with spaces and hands the
                # line to a shell, so the payload is all of them, not the first
                # (task 085 round 2, T1: `parallel 'rm' '-rf' '/' ::: 1`).
                words = []
                while i < len(toks) and toks[i][0] not in spec["positional_stop"]:
                    words.append(toks[i][0])
                    i += 1
                payloads.append(" ".join(words))
                i = len(toks)
                break
            if (spec["shell_args"] and user_positional and end_of_opts
                    and re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", t)):
                # after the user, `su` passes the rest to the SHELL: `-c X`
                # is the shell's command string (task 085 round 2, T3)
                if i + 1 < len(toks):
                    payloads.append(toks[i + 1][0])
                i = len(toks)
                break
            if operands > 0:                           # `timeout 5 …`, `chrt 10 …`
                operands -= 1
                user_positional = spec["shell_args"]
                i += 1
                continue
            break                                      # the command starts here
    rest = s[toks[i][1]:] if i < len(toks) else ""
    return (rest, True, payloads)


def _normalize_command_head(seg):
    """`seg` with its command written plainly: quotes, escapes, a leading `(`,
    and a directory prefix removed from the HEAD token only. Everything after it
    is left byte-for-byte, so the rules still see the real arguments."""
    toks = _lex(seg)
    if not toks:
        return seg
    raw, start, value, _quoted = toks[0]
    name = _command_name(value)
    if name is None or name == raw:
        return seg
    return seg[:start] + name + seg[start + len(raw):]


# `git` is not a wrapper — the VERB carries the meaning — but it has the same
# "options before the verb" shape, and `git -C <dir> push --force` is ordinary
# agent usage (impl panel round 1, opus #2). These are git's global options.
_GIT_VALUE_SHORT = set("Cc")
_GIT_VALUE_LONG = {"--git-dir", "--work-tree", "--namespace", "--exec-path",
                   "--super-prefix", "--config-env"}
_GIT_FLAG_LONG = {"--paginate", "--no-pager", "--bare", "--literal-pathspecs",
                  "--no-replace-objects", "--no-optional-locks", "--no-ext-diff",
                  "--no-lazy-fetch", "--no-advice", "--glob-pathspecs",
                  "--noglob-pathspecs", "--icase-pathspecs", "--html-path",
                  "--exec-path", "--no-replace-objects"}


def _git_after_globals(args):
    """`args` with git's leading GLOBAL options removed, so the subcommand sits
    first. `--` ends the options (impl panel round 2, grok #2: `git -- push
    --force` matched nothing)."""
    i = 0
    while i < len(args):
        t = args[i]
        if t == "--":
            i += 1
            break
        if t.startswith("--"):
            base = t.split("=", 1)[0]
            if base in _GIT_VALUE_LONG and "=" not in t:
                i += 2
                continue
            if base in _GIT_VALUE_LONG or base in _GIT_FLAG_LONG:
                i += 1
                continue
            break
        if t.startswith("-") and len(t) > 1:
            letters = t[1:]
            if letters[0] in _GIT_VALUE_SHORT:
                i += 1 if letters[1:] else 2
                continue
            i += 1
            continue
        break
    return list(args[i:])


def _brace_alternatives(t):
    """`{/,./build}` expands to `/` and `./build`, so a brace list is dangerous
    when any ALTERNATIVE is (impl panel round 4, sol:medium)."""
    out, i, n = [], 0, len(t)
    while i < n:
        if t[i] == "{":
            j = t.find("}", i)
            if j == -1:
                break
            inner = t[i + 1:j]
            if "," in inner:
                head, tail = t[:i], t[j + 1:]
                return [head + alt + tail for alt in inner.split(",")]
            i = j + 1
            continue
        i += 1
    return out


def _rm_is_dangerous(values, kinds=None):
    """`rm` recursive+force against a DANGEROUS target, decided on DEQUOTED
    tokens. A relative subdir (`./build`, `node_modules`) is NOT dangerous; `/`,
    `~`, `$HOME`, `*`, `..` or any absolute path is. Round 2 of the impl panel
    found this reading raw text, so a quoted root target — the single most
    catastrophic command, trivially quoted — walked through every check."""
    if not values or _command_name(values[0]) != "rm":
        return False
    kinds = kinds or [""] * len(values)
    flags = "".join(t[1:] for t in values if t.startswith("-") and not t.startswith("--"))
    longs = [t for t in values if t.startswith("--")]
    recursive = "r" in flags or "R" in flags or "--recursive" in longs
    force = "f" in flags or "--force" in longs
    if not (recursive and force):
        return False
    for idx, t in enumerate(values[1:], start=1):
        if t.startswith("-"):
            continue
        kind = kinds[idx] if idx < len(kinds) else ""
        for cand in [t] + _brace_alternatives(t):
            if cand in ("/", "/*", "..") or cand.startswith("/") \
                    or cand.startswith(".."):
                return True                            # a path is a path, quoted or not
            # A target that RUNS something (`$(pwd)`, a backtick) is dangerous:
            # it commonly yields a rooted path. A plain variable is NOT — round 4
            # treated every `$` as computed, which blocked `rm -rf "$WORK"`, the
            # standard temp-dir cleanup idiom that appears ~28 times in this
            # repository alone and was ALLOWED before this task. Measured, then
            # narrowed (impl panel round 5, opus #2). Known-dangerous NAMES are
            # still dangerous however they are written.
            if kind != "'" and ("$(" in cand or "`" in cand):
                return True
            if kind != "'" and _DANGEROUS_VAR.search(cand):
                return True
            if ("*" in cand or cand.startswith("~")) and not kind:
                return True
    return False


_DEVICE = re.compile(r"^/dev/(sd|nvme|disk|hd)")
# Variable names whose value is a root the user cannot afford to lose.
_DANGEROUS_VAR = re.compile(r"\$\{?(HOME|HOMEDRIVE|HOMEPATH|USERPROFILE|ROOT|PREFIX)\b")
_REDIR = re.compile(r"^\d*>>?")


def _segment_checks(seg):
    """Command-position checks on one prefix-stripped segment → (name, why) or
    None. Everything is decided on the LEXED, dequoted tokens: the command is
    named through quotes/escapes/paths, and so are its flags and targets."""
    toks = _lex(seg)
    values = [t[2] for t in toks]
    if not values:
        return None
    head = _command_name(values[0])
    if head == "git":
        values = ["git"] + _git_after_globals(values[1:])
    kinds = [t[3] for t in toks]
    if _rm_is_dangerous([head or values[0]] + values[1:], kinds):
        return ("rm-rf-dangerous-target", "recursive force-delete of a dangerous path")
    if head == "git" and len(values) > 1:
        verb, args = values[1], values[2:]
        short = "".join(a[1:] for a in args
                        if a.startswith("-") and not a.startswith("--"))
        # `git push --force --force-with-lease` still force-pushes: the presence
        # of the safe flag does not cancel the unsafe one (impl panel round 4).
        if verb == "push" and ("--force" in args or "f" in short):
            return ("git-push-force", "force-push overwrites remote history irreversibly")
        if verb == "reset" and "--hard" in args:
            return ("git-reset-hard", "discards uncommitted work irrecoverably")
        if verb == "clean":
            if "f" in short and "d" in short:
                return ("git-clean-force", "deletes untracked files/dirs irrecoverably")
    if head == "dd" and any(a.startswith("of=/dev/") for a in values[1:]):
        return ("dd-to-device", "writes raw to a device — destroys it")
    if head and head.startswith("mkfs"):
        return ("mkfs", "formats a filesystem — destroys its contents")
    for i, (raw, _st, v, _quoted) in enumerate(toks):
        if not _REDIR.match(raw):                      # `>`, `>>`, `1>`, `2>>`
            continue                                   # a QUOTED `>` is data
        op = _REDIR.match(v)
        if not op:
            continue
        target = v[op.end():]                          # the TARGET may be quoted
        if not target and i + 1 < len(toks):
            target = toks[i + 1][2]
        if target and _DEVICE.match(target):
            return ("redirect-to-device", "overwrites a raw device")
    return None


# The database clients the SQL rule knows, as a table (task 110: the names are
# also argument-judged command names, see `_ARGUMENT_JUDGED_HEADS`).
_DB_CLIENTS = ("psql", "mysql", "mariadb", "sqlite", "sqlite3", "mongo", "mongosh",
               "clickhouse", "cockroach")
_DB_CLIENT_RX = r"\b(?:" + "|".join(sorted(_DB_CLIENTS, key=len, reverse=True)) + r")\b"
_SQL_DESTRUCTIVE_RX = r"\b(?:drop\s+(?:database|table|schema)|truncate\b|delete\s+from)\b"

# Whole-command patterns (context spans segments): pipe-to-shell, and SQL that is
# clearly issued to a DB client (so `grep "DROP TABLE"` is NOT flagged).
_WHOLE = [
    ("pipe-to-shell",
     re.compile(r"\b(curl|wget|fetch)\b[^|]*\|\s*(?:(?:sudo|env|command|nice|nohup)(?:\s+-\S+)*\s+)*(sh|bash|zsh|ksh|python3?|perl|ruby)\b", re.I),
     "piping a downloaded script straight into a shell runs unreviewed remote code"),
    ("sql-destructive",
     # Either order (task 073 round 2: a statement echoed INTO the client from the
     # left never matched before), spanning lines.
     re.compile(r"(?:" + _DB_CLIENT_RX + r".*" + _SQL_DESTRUCTIVE_RX + r")"
                r"|(?:" + _SQL_DESTRUCTIVE_RX + r".*" + _DB_CLIENT_RX + r")", re.I | re.S),
     "a DB drop/truncate/delete issued to a client is irreversible"),
]


_SHELL_C = re.compile(r"^(?:sh|bash|zsh|ksh|dash)\b[^;]*?\s-[a-z]*c\s*(.+)$")


def _unwrap_shell_c(seg):
    """`bash -lc "<script>"` → `<script>`. POSIX is `sh -c string [name [args]]`,
    so ONLY the operand right after `-c` is the script — round 2 took everything
    to end-of-line as one string, which meant a trailing `argv0` made the whole
    thing stop looking like a script (impl panel round 3, sol:high + grok)."""
    toks = _lex(seg.strip())
    if not toks or _command_name(toks[0][2]) not in _SHELLS:
        return None
    k = 1
    while k < len(toks):
        v = toks[k][2]
        if v == "--":
            k += 1
            continue
        if v.startswith("--"):
            # `--rcfile FILE` / `--init-file FILE` take a value; the rest are
            # flags (impl panel round 5, sol:high).
            k += 2 if v in _SHELL_VALUE_LONG else 1
            continue
        if v.startswith("-") and len(v) > 1:
            if "c" in v[1:]:
                attached = v[1:].split("c", 1)[1]
                if attached:
                    return attached
                return toks[k + 1][2] if k + 1 < len(toks) else None
            # `-O extglob`, `-o noglob` take a value; other short flags do not
            # (impl panel round 4: they stopped the scan before `-c` was seen).
            k += 2 if v[-1] in "Oo" else 1
            continue
        break
    return None


# Task 073 data-region masking, tightened by the impl panel (round 1):
#  * only a DATA SINK's heredoc is inert — `cat`/`tee` (optionally redirected)
#    with `<<TAG` as the LAST thing on the line; `bash <<EOF`, `sh`, `psql`,
#    `python3 -`, `ssh host` … RUN their body and keep it;
#  * an UNQUOTED heredoc expands `$(…)`, backticks and `${…}` — kept when the
#    body has any;
#  * an unterminated heredoc masks nothing;
#  * echo/printf arguments are masked only when they carry no expansion.
# A redirect target must be a PLAIN PATH — `>(sh)` is a process substitution
# that executes the "data" (impl-panel round 2).
_PATH = r"[^\s()<>|&;`$]+"
_HEREDOC_SINK = re.compile(
    r"^\s*(?:cat|tee)\b[^|;&<>()`$]*(?:>{1,2}\s*" + _PATH + r"\s*)?<<-?\s*(?P<q>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=q)\s*(?:>{1,2}\s*" + _PATH + r"\s*)?$")
_EXPANSION = re.compile(r"\$\(|`|\$\{|<\(|>\(")
# An echo/printf line is inert only when it is a single simple command redirected
# to a plain file with NO pipe, no expansion and no process substitution anywhere
# on the line — a quote-blind matcher cannot tell `"a | sh"` from ` | sh`, so any
# `|` on the line keeps the text (impl-panel round 2: `echo '… | sh' | bash`).
_DATA_LINE = re.compile(r"^\s*(?:echo|printf)\b[^|;&<>()`$]*>{1,2}\s*" + _PATH + r"\s*$")   # single simple command only (round 3)


def _mask_echo_literals(text):
    """Mask each echo/printf SEGMENT separately. A separator glued to a quoted
    token (`"x";psql`) stays inside one lexer token, so deciding per token let
    the mask run on into the next command (impl panel round 5, opus #1 — the
    round-3 regression class in a form the round-3 test could not see).
    `_split_segments` already tells an operator from a quoted one; use it."""
    pieces = _split_segments_keep(text)
    return "".join((seg if sep == "|" else _mask_one_echo(seg)) + sep
                   for seg, sep in pieces)


def _mask_one_echo(line):
    """An `echo`/`printf` argument that is FULLY QUOTED and carries no expansion
    is a string, not a command — whatever else is on the line. Task 073 could
    only mask a redirected echo, because a quote-blind matcher cannot tell
    `"a | sh"` from ` | sh`; the lexer records quoting now, so the docstring's
    promise ("echoing dangerous text is fine") can finally be true in general
    (impl panel round 3, opus #3). Unquoted text and anything with `$`, a
    backtick or a process substitution is left exactly as it was."""
    toks = _lex(line)
    if not toks or _command_name(toks[0][2]) not in ("echo", "printf"):
        return line

    out, last = [], 0
    for raw, start, value, quoted in toks[1:]:
        if not quoted or _EXPANSION.search(raw):
            continue
        out.append(line[last:start])
        out.append("DATA")
        last = start + len(raw)
    if not out:
        return line
    out.append(line[last:])
    return "".join(out)


def _drop_sink_heredoc_bodies(command):
    """The heredoc half of `_strip_data_regions`, on its own (task 110: the
    task-dir helper scans the call's text for redirections and must not read a
    note's body, but must still see every word of an echo line): a `cat`/`tee`
    heredoc body written to a plain file (quoted tag, or no expansion inside)
    is dropped; everything else stays as written."""
    lines = str(command).split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        m = _HEREDOC_SINK.match(line)
        if not m:
            continue
        tag, quoted = m.group("tag"), bool(m.group("q"))
        j = i
        while j < len(lines) and lines[j].strip() != tag:
            j += 1
        if j >= len(lines):
            continue                                   # unterminated → keep all
        if not quoted and _EXPANSION.search("\n".join(lines[i:j])):
            continue                                   # expansions would run
        i = j + 1                                      # drop body + closing tag
    return "\n".join(out)


def _strip_data_regions(command):
    """Return the command text with inert DATA removed, for the WHOLE-command
    rules only (the segment rules are line-split and never masked — a heredoc
    body line `rm -rf /` still blocks, conservatively): a `cat`/`tee` heredoc
    body written to a plain file (quoted tag, or no expansion inside) and an
    echo/printf line redirected to a plain file with no pipe/expansion on it.
    Everything that could run stays."""
    return "\n".join("echo > file" if _DATA_LINE.match(l) else _mask_echo_literals(l)
                     for l in _drop_sink_heredoc_bodies(command).split("\n"))


# The pipe rule had its OWN wrapper list (`sudo|env|command|nice|nohup` with
# flag-only options), so every value-taking form walked past it — `curl … | sudo
# -u root bash` ran while `curl … | sudo bash` blocked. It now walks the same
# `_WRAPPERS` table as the segment rules. The original regex is KEPT: it still
# catches shapes this split does not see (a pipe inside `bash -c '…'`), and two
# independent rules that both block is the right redundancy for this layer.
_DOWNLOADER = re.compile(r"^(?:curl|wget|fetch|aria2c)$")
_INTERPRETERS = {"sh", "bash", "zsh", "ksh", "dash", "ash",
                 "python", "python3", "perl", "ruby", "node"}
_SINGLE_PIPE = re.compile(r"(?<!\|)\|(?!\|)")
_PIPE_WHY = "piping a downloaded script straight into a shell runs unreviewed remote code"


def _pipes_downloader_into_shell(text):
    """True when a downloader's output reaches an interpreter through a pipe,
    however many optioned wrappers sit in between."""
    # `||` is a control operator, not a pipe: `curl … || bash` is "drop to a
    # shell if the download fails" (impl panel round 3, opus #1).
    parts = _SINGLE_PIPE.split(text)
    if len(parts) < 2:
        return False
    seen_downloader = False
    for part in parts:
        # `|&` pipes stdout AND stderr, and a grouped `(bash)` / `{ bash; }` runs
        # the same interpreter — both walked past the first version of this rule.
        part = part.lstrip("&")
        rest, executes, payloads = _walk_prefix(part)
        if not executes:
            continue
        rest = _normalize_command_head(rest)
        lexed = _lex(rest)
        head = _command_name(lexed[0][2]) if lexed else None
        if seen_downloader:
            # `curl … | env -S 'bash'` puts the interpreter inside an option
            # VALUE (impl panel round 1, grok #3).
            for payload in payloads:
                p = _lex(payload)
                if p and _command_name(p[0][2]) in _INTERPRETERS:
                    return True
            if head in _INTERPRETERS:
                return True
        if head and _DOWNLOADER.match(head):
            seen_downloader = True
        else:
            for payload in payloads:                   # `env -S 'curl …' | …`
                p = _lex(payload)
                name = _command_name(p[0][2]) if p else None
                if name and _DOWNLOADER.match(name):
                    seen_downloader = True
                    break
    return False


def _command_substitutions(text):
    """The contents of every `$( … )` and backtick substitution. Their bodies RUN,
    whatever surrounds them — `echo "$(rm -rf /)"` deletes the disk — so each is
    classified as a command in its own right."""
    out = []
    i, n = 0, len(text)
    while i < n:
        # `<( … )` and `>( … )` run their body too, and `. <(…)` sources the
        # result — the same command position as a `$( … )` substitution.
        if text.startswith("<(", i) or text.startswith(">(", i):
            depth, j, quote = 1, i + 2, ""
            while j < n and depth:
                c = text[j]
                if quote:
                    if c == quote:
                        quote = ""
                elif c in "\"'":
                    quote = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                j += 1
            if depth == 0:
                out.append(text[i + 2:j - 1])
            i = j
            continue
        if text.startswith("$(", i):
            # Balance parentheses OUTSIDE quotes: `$(rm -rf /var/lib/app '(')`
            # closed early for a quote-blind counter (impl panel round 1,
            # sol:high #5), which left the real body unclassified.
            depth, j, quote = 1, i + 2, ""
            while j < n and depth:
                c = text[j]
                if quote:
                    if c == "\\" and quote == '"' and j + 1 < n:
                        j += 1
                    elif c == quote:
                        quote = ""
                elif c in "\"'":
                    quote = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                j += 1
            if depth == 0:
                out.append(text[i + 2:j - 1])
            i = j
        elif text[i] == "`":
            j = text.find("`", i + 1)
            if j == -1:
                break
            out.append(text[i + 1:j])
            i = j + 1
        else:
            i += 1
    return out


# A QUOTED heredoc tag means the shell performs NO expansion in the body — no
# `$( )`, no backticks — whatever the sink is. The segment rules still read those
# lines (so an interpreter heredoc carrying `rm -rf /` as a COMMAND still blocks,
# because the interpreter runs it), but the SUBSTITUTION scan must not, or every
# `python3 - <<'PY'` whose script mentions a dangerous command inside a Markdown
# code span gets refused. Measured on this repo's own 73 859 source/doc lines:
# that single distinction accounts for 19 of 27 newly-blocked lines, and it is
# not a heuristic — a quoted tag genuinely suppresses expansion in every shell.
_QUOTED_HEREDOC = re.compile(r"""<<-?\s*(['"])(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\1""")
# ... unless the SINK is itself a shell: `bash <<'EOF'` does not expand the body
# when the outer shell reads it, but bash then runs it and expands it there.
_SHELLS = {"sh", "bash", "zsh", "ksh", "dash", "ash"}
# The command names whose verdict a rule here reads from their ARGUMENTS: `rm`
# from its flags and target, `git` from its verb and flags, `dd` from an operand,
# a shell from the script it is handed, a database client from the statement.
# When the arguments are unknown, the name is all there is to judge (task 110,
# owner 2026-10-05). A name a rule refuses on sight (`mkfs…`) needs no entry:
# the bare template already blocks.
_ARGUMENT_JUDGED_HEADS = frozenset({"rm", "git", "dd"}) | frozenset(_SHELLS) | frozenset(_DB_CLIENTS)


def _line_runs_a_shell(line, _depth=0):
    for seg in _split_segments(line):
        rest, _executes, payloads = _walk_prefix(seg)
        lexed = _lex(rest)
        if lexed and _command_name(lexed[0][2]) in (_SHELLS | _SOURCERS):
            return True
        if _depth < 8 and any(p and _line_runs_a_shell(p, _depth + 1) for p in payloads):
            return True
    return False


def _strip_quoted_heredoc_bodies(text):
    lines = str(text).split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        m = _QUOTED_HEREDOC.search(line)
        if not m:
            continue
        # `sudo bash <<'EOF'` / `env bash <<…` / `timeout 5 sh <<…`: resolve the
        # sink through the wrapper walk, not from the first token (impl panel
        # round 2, sol:high + sol:medium).
        # Task 110 (impl panel round 1 follow-up): the sink was read from the
        # FIRST command of the line, so `cd x; bash <<'EOF'` and `cat <<'EOF' |
        # bash` hid a shell script. The body is kept when ANY command on the
        # line — or a payload it delegates to — is a shell or a sourcer.
        if _line_runs_a_shell(line):
            continue                                   # the body IS a shell script
        tag = m.group("tag")
        j = i
        while j < len(lines) and lines[j].strip() != tag:
            j += 1
        if j >= len(lines):
            continue                                   # unterminated → keep it all
        i = j + 1                                      # drop the inert body
    return "\n".join(out)


# `bash <<< '<script>'` hands the shell its script on stdin, so the string is a
# command line, not an argument (found by my own sweep during the round-3 panel).
_HERESTRING = re.compile(r"<<<\s*(.+)$", re.S)


def _herestring_payloads(text):
    out = []
    for line in str(text).split("\n"):
        m = _HERESTRING.search(line)
        if not m:
            continue
        rest, _executes, _payloads = _walk_prefix(line.split("<<<")[0])
        lexed = _lex(rest)
        if lexed and _command_name(lexed[0][2]) in _SHELLS:
            out.append(_unquote(m.group(1).strip()))
    return out


# Recursion bound. It is NOT a scanning budget on nesting: every level strips at
# least two characters of syntax, so the real bound is the input length; this is
# only a stack guard for pathological input (impl panel round 3, sol:high #4 —
# the previous cap of 3 let `echo $(echo $(echo $(echo $(<destructive>))))` pass).
_MAX_DEPTH = 64
_TOO_DEEP = ("block", "unparseable-nesting",
             "nesting too deep to classify — refusing rather than guessing")


_SHELL_VALUE_LONG = {"--rcfile", "--init-file"}
_SOURCERS = {".", "source"}


def _shell_consumes_a_downloader(text):
    """`bash <(curl …)`, `. <(curl …)`, `bash <<< "$(curl …)"`: the syntax was
    handled by the substitution scan, but a bare downloader body classifies as
    ALLOW on its own, so the download-execute SEMANTICS slipped through while
    `curl … | bash` blocked (impl panel round 5, opus #3)."""
    for line in str(text).split("\n"):
        rest, _executes, _payloads = _walk_prefix(line)
        lexed = _lex(rest)
        head = _command_name(lexed[0][2]) if lexed else None
        if head not in _SHELLS and head not in _SOURCERS:
            continue
        for body in _command_substitutions(line) + _herestring_payloads(line):
            b = _lex(body)
            name = _command_name(b[0][2]) if b else None
            if name and _DOWNLOADER.match(name):
                return True
    return False


# ── download-then-run (owner Q-B (b), task 110, parked R3 from 077) ───────────
# The pipe rule catches `curl … | sh`; the same threat with the pipe replaced by
# a FILE — a downloader writes a file in one segment, a later segment of the SAME
# command runs it — was a disclosed bound. Shipped only after measuring it on the
# owner's bash history: 0 of 282 commands naming a downloader (0 of 32,997) would
# have been refused (task 110 record, measure/results-download-then-run.json).
# Scope: one command line; a file downloaded in an EARLIER tool call is not seen.
_DL_RUNNERS = _INTERPRETERS | _SOURCERS


def _dl_basename(url):
    return posixpath.basename(url.split("?", 1)[0].split("#", 1)[0])


def _downloaded_files(values):
    """Paths a downloader invocation writes (dequoted tokens; [0] = the name)."""
    head = _command_name(values[0])
    urls = [v for v in values[1:] if "://" in v]
    out, remote_name, named = [], False, False
    i = 1
    while i < len(values):
        v = values[i]
        nxt = values[i + 1] if i + 1 < len(values) else ""
        if v.startswith("--"):
            base, eq, val = v.partition("=")
            if base in ("--output", "--output-document", "--out"):
                out.append(val if eq else nxt)
                named = True
                i += 1 if eq else 2
                continue
            if base == "--remote-name" or base == "--remote-name-all":
                remote_name = True
            i += 1
            continue
        if v.startswith(">"):                       # `> file` / `>file` / `>>file`
            out.append(v.lstrip(">") or nxt)
            i += 1 if v.lstrip(">") else 2
            continue
        if v.startswith("-") and len(v) > 1:
            letters = v[1:]
            for k, ch in enumerate(letters):
                if (head in ("curl", "fetch", "aria2c") and ch == "o") or (head == "wget" and ch == "O"):
                    attached = letters[k + 1:]
                    out.append(attached or nxt)
                    named = True
                    i += 0 if attached else 1
                    break
                if head == "curl" and ch == "O":
                    remote_name = True
            i += 1
            continue
        i += 1
    if remote_name or (head == "wget" and not named):
        out += [_dl_basename(u) for u in urls]
    return {posixpath.normpath(o) for o in out
            if o and o != "-" and not o.startswith("/dev/")}


def _stdin_files(lexed):
    """Files a command reads as STDIN: `< x.sh`, `<x.sh`, `0< x.sh` (unquoted)."""
    out = []
    for k, (raw, _st, _val, quoted) in enumerate(lexed):
        if quoted:
            continue
        m = re.match(r"^0?<(?![<&>(])(.*)$", raw)
        if not m:
            continue
        target = m.group(1) or (lexed[k + 1][2] if k + 1 < len(lexed) else "")
        if target:
            out.append(_unquote(target))
    return out


def _downloads_then_runs(text, written=None, _depth=0):
    """True when a later part of THIS command runs a file a downloader wrote
    earlier in it. Impl panel round 1: the file is followed into the commands a
    segment delegates to — wrapper and `eval`/`parallel` payloads, a `sh -c`
    body — through stdin (`bash < x.sh`), and by the path AS WRITTEN (the head
    normalisation strips a directory, so `/tmp/x.sh` after `curl -o /tmp/x.sh`
    was compared as `x.sh`)."""
    if written is None:
        written = set()
    if _depth > 16:
        return bool(written)                           # cannot follow further: refuse
    for seg in _split_segments(text):
        rest, executes, payloads = _walk_prefix(seg)
        if written:
            for p in payloads:
                if p == _PARALLEL_RUNS_UNKNOWN:
                    return True                        # a runner whose operand is rewritten
                if p and not p.startswith("\x00") and _downloads_then_runs(p, written, _depth + 1):
                    return True
        if not executes:
            continue
        raw_lexed = _lex(rest)
        values = [t[2] for t in _lex(_normalize_command_head(rest))]
        if not values or not raw_lexed:
            continue
        head = _command_name(values[0])
        if head and _DOWNLOADER.match(head):
            written |= _downloaded_files(values)
            continue
        if not written:
            continue
        for name in (raw_lexed[0][2], values[0]):      # `/tmp/x.sh`, `./install`, `x.sh`
            if posixpath.normpath(name) in written:
                return True
        if head in _DL_RUNNERS:
            operands = [v for v in values[1:] if not v.startswith("-")]
            if operands and posixpath.normpath(operands[0]) in written:
                return True
            if any(posixpath.normpath(f) in written for f in _stdin_files(raw_lexed)):
                return True                            # `bash < x.sh`
        inner = _unwrap_shell_c(rest) or _unwrap_shell_c(_normalize_command_head(rest))
        if inner and _downloads_then_runs(inner, written, _depth + 1):
            return True                                # `bash -c 'sh x.sh'`
    return False


def classify_command(command, extra_patterns=None, _depth=0, _argv=False, _in_sub=False):
    """Return ("block", name, why) or ("allow", None, None). Pure + deterministic.

    `command` may be a str, or a list of argv tokens (Codex `exec_command`), in
    which case the joined form AND each element are checked."""
    if isinstance(command, (list, tuple)):
        # ONE invocation, with element boundaries preserved. Classifying each
        # element on its own blocked benign argv such as
        # ["git","commit","-m","<a message mentioning a dangerous command>"]
        # (impl panel round 2, sol:high + grok).
        try:
            quoted = " ".join(shlex.quote(str(p)) for p in command)
        except Exception:
            quoted = " ".join(str(p) for p in command)
        return classify_command(quoted, extra_patterns, _depth, _argv=True, _in_sub=_in_sub)
    if not command or not str(command).strip():
        return ("allow", None, None)
    command = str(command)
    for seg in _split_segments(_strip_comments(command)):
        stripped, executes, payloads = _walk_prefix(seg)
        for payload in payloads:                       # `env -S "<command line>"`
            if payload == _PARALLEL_UNJUDGEABLE:
                return ("block", "parallel-too-many-jobs",
                        "GNU parallel would run more jobs (or option readings) than the "
                        "guard can judge one by one — refusing rather than judging a sample")
            if payload == _PARALLEL_UNMODELLED:
                return ("block", "parallel-unmodelled-arguments",
                        "GNU parallel rewrites its arguments here in a way the guard does not "
                        "model, and a command in the template is judged by its arguments or "
                        "cannot be read — refusing rather than guessing what the jobs run")
            if payload.startswith("\x00"):
                continue                               # information for another rule
            if payload:
                if _depth + 1 >= _MAX_DEPTH:
                    return _TOO_DEEP                   # never silently open
                v = classify_command(payload, extra_patterns, _depth + 1, _in_sub=_in_sub)
                if v[0] == "block":
                    return v
        if not executes:                               # `sudo --version …` prints, runs nothing
            continue
        normalized = _normalize_command_head(stripped)
        hit = (_segment_checks(stripped)
               or _segment_checks(normalized)
)
        if hit:
            return ("block", hit[0], hit[1])
        inner = _unwrap_shell_c(stripped) or _unwrap_shell_c(normalized)
        if inner and _depth < _MAX_DEPTH:                       # unwrap `bash -lc "<script>"`
            v = classify_command(inner, extra_patterns, _depth + 1, _in_sub=_in_sub)
            if v[0] == "block":
                return v
    # Task 073 (B0): the whole-command patterns must not fire on DATA — a heredoc
    # body or an echo/printf string being written to a file is text about a
    # command, not the command (the documented bound: echoing dangerous text is
    # fine). Segment checks above already ran on the full text.
    whole_text = _strip_comments(_strip_data_regions(command))
    # The input arrived as argv: word splitting, substitution and pipes have
    # already happened, so `$( … )` inside an element is DATA (impl panel round 3,
    # opus #2 — it blocked a benign commit message). Round 4, four seats: skipping
    # the SUBSTITUTION scan was right, skipping everything after it was my
    # regression — it took the DB-client rule and every project `dangerous_commands`
    # pattern with it, on the one delivery shape this task added fixtures for.
    if not _argv and _depth < _MAX_DEPTH:
        # On the MASKED text: a quoted heredoc written to a plain file does not
        # expand, so a fixture ABOUT `$(rm -rf /)` stays data (task 073's promise).
        for sub in (_command_substitutions(_strip_quoted_heredoc_bodies(whole_text))
                    + _herestring_payloads(whole_text)):
            if sub.strip():
                if _depth + 1 >= _MAX_DEPTH:
                    return _TOO_DEEP                   # fail CLOSED, never silently open
                v = classify_command(sub, extra_patterns, _depth + 1, _in_sub=True)
                if v[0] == "block":
                    return v
    if _pipes_downloader_into_shell(whole_text):
        return ("block", "pipe-to-shell", _PIPE_WHY)
    if _shell_consumes_a_downloader(whole_text):
        return ("block", "pipe-to-shell", _PIPE_WHY)
    # The body of a quoted heredoc that a NON-shell reads (a `python3 - <<'PY'`
    # program) is not shell code: measured, the unrestricted form refused one
    # legitimate command of the owner's history — a python script writing prose
    # that quotes the vector. A shell's heredoc body is kept (the sink runs it).
    # Not inside a body the SUBSTITUTION scan extracted: that scan also lifts a
    # backtick span out of single quotes (where bash keeps it literal), and that
    # pre-existing quirk was the whole of the one false positive measured with
    # the rule applied there (task 110 — parked as its own finding). Bound: a
    # download-then-run INSIDE `$( … )` is not seen.
    if not _in_sub and _downloads_then_runs(_strip_quoted_heredoc_bodies(whole_text)):
        return ("block", "download-then-run",
                "running a script this command just downloaded executes unreviewed remote code")
    for name, rx, why in _WHOLE:
        if rx.search(whole_text):
            return ("block", name, why)
    for pat in (extra_patterns or []):
        # On the MASKED text, like every built-in whole-command rule: a project
        # pattern must not fire on a heredoc fixture either (impl panel round 1,
        # sonnet #2 — the shipped example dodged it only by its `$` anchor).
        try:
            if re.search(pat, whole_text, re.I):
                return ("block", "project-dangerous", f"matches project pattern {pat!r}")
        except re.error:
            continue
    return ("allow", None, None)


# ── hook body ─────────────────────────────────────────────────────────────────

def _find_root():
    d = os.getcwd()
    while True:
        if os.path.isdir(os.path.join(d, ".agent", "tasks")):
            return d
        if os.path.isdir(os.path.join(d, ".agent")):
            for sub in os.listdir(os.path.join(d, ".agent")):
                if os.path.isdir(os.path.join(d, ".agent", sub, "tasks")):
                    return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _load_cfg(root):
    if not root:
        return {}
    try:
        with open(os.path.join(root, ".agent", "config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def _load_journal():
    """Load the shared enforcement-journal helper (sibling file). Fail-open:
    None on any error so journalling can never wedge the guard."""
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pb_journal.py")
        spec = importlib.util.spec_from_file_location("_pb_journal", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _normalize_payload(payload):
    """Apply the provider-dialect normalizer in-process.

    The wrapper used to pipe stdin through hook-payload-normalize.py and then
    into this script — two interpreter starts on every shell tool call. Doing it
    here makes the guard one process. FAIL-OPEN on any failure: the caller falls
    back to the raw payload, which is what the pre-normalizer guard read anyway
    (Shell / run_terminal_command are already recognised by name below; the
    normalizer's job here is grok's camelCase toolName/toolInput).
    """
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "hook-payload-normalize.py")
        spec = importlib.util.spec_from_file_location("_pb_hook_norm", path)
        if spec is None or spec.loader is None:
            return payload
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.normalize(payload)
    except Exception:
        return payload


def _journal_session_id():
    """The session id the enforcement journal attributes a decision to: the id
    the guard used (task 106 single judge) — on POSIX the resolved one, so a
    stale or sibling env value is not what the log names; "" when unset."""
    sid = os.environ.get("PLAYBOOK_SESSION_ID", "").strip()
    # Task 110 (Q-A b): with no env id — every real hook process (109 K10) — the
    # walk still names the session; the journal said `"session_id":""` before.
    try:
        return _resolved_session_id()
    except Exception:
        return sid


def _resolved_session_id():
    """The session id the CLI and the hooks resolve (`tasks.core.
    resolve_session_id`) on EVERY platform. Impl panel round 1 (sol-high): on
    Windows the guard kept the raw env id, while the CLI resolves an absent one
    to the constant `pid-win-fallback` it shares with the bash hooks — so a task
    activated there could never acknowledge. The resolver's one-time Windows
    warning is swallowed: on a block, stderr IS the agent's message."""
    import contextlib
    import io
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    from tasks.core import resolve_session_id
    with contextlib.redirect_stderr(io.StringIO()):
        return resolve_session_id(quiet=True)


def _active_task_is_irreversible(root):
    """True iff this session's ACTIVE task (lane/sessions/<sid>/current_state →
    lane/tasks/<N>-*/task.md) carries a live `## Risk` of `irreversible`, read
    with the CLI's fence-aware reader. Any failure → False (the block stands)."""
    try:
        if not root:
            return False
        sid = os.environ.get("PLAYBOOK_SESSION_ID", "").strip()
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if plugin_dir not in sys.path:
            sys.path.insert(0, plugin_dir)
        # Task 106 (round 1 R1-3): the SAME stale-id rule as the resolvers — a
        # `pid-N` that is not a live agent (e.g. inherited from a parent that
        # has exited) must not let that session's irreversible task acknowledge.
        # Round 2 (task 106): follow the id the CLI and the hooks resolve — a
        # rejected env id (dead, or a live sibling's) must neither acknowledge
        # through ITS session nor block the session the shell really is in.
        # Task 110 (owner Q-A (b), parked R1 + P1): with NO env id the walk still
        # resolves the session — a real hook process carries none (109 K10:
        # 99/99 events), so requiring one meant the documented acknowledgement
        # never fired in a normal session. Windows included (impl panel round
        # 1): there the resolver answers the env id or `pid-win-fallback`.
        sid = _resolved_session_id()
        if not sid or "/" in sid or "\\" in sid or ".." in sid:
            return False
        # The lane through the ENFORCING resolver (task 110, R1): the journal's
        # best-effort resolver answers the ROOT lane for a malformed marker, so a
        # stale root irreversible task could acknowledge for a session whose lane
        # is unknowable. `resolve_agent_dir` refuses a malformed marker (it exits,
        # hence BaseException below), and the fresh-clone shape — lanes present,
        # no marker, no root tasks dir — is refused here as every enforcing
        # surface refuses it.
        from tasks.core import resolve_agent_dir
        from pathlib import Path as _P
        agent = _P(root) / ".agent"
        if not (agent / "current_user").exists() and not (agent / "tasks").is_dir():
            if any((c / "tasks").is_dir() for c in agent.iterdir() if c.is_dir()):
                return False
        lane = str(resolve_agent_dir(_P(root)))
        pointer = os.path.join(lane, "sessions", sid, "current_state")
        with open(pointer, encoding="utf-8") as fh:
            num = fh.read().strip()
        if not num.isdigit():
            return False
        tasks_dir = os.path.join(lane, "tasks")
        cands = sorted(d for d in os.listdir(tasks_dir) if d.startswith(num + "-"))
        if not cands:
            return False
        task_md = os.path.join(tasks_dir, cands[0], "task.md")
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if plugin_dir not in sys.path:
            sys.path.insert(0, plugin_dir)
        from tasks.core import (extract_risk_from_text, _status_from_lines,   # fence-aware readers
                                _physical_lines, stub_marker_type)
        from pathlib import Path as _P
        # ONE read, both answers (task 058, plan panel P2): this used to read
        # task.md for the status and AGAIN inside `extract_risk`, so a write
        # landing between them could pair one task's status with another's risk.
        _text = _P(task_md).read_text(encoding="utf-8", errors="replace")
        # A done/blocked task in a stale pointer never acknowledges (073 round 3:
        # a crash between the close and the pointer clear). Task 110 W9: `pending`
        # was the status of every task `tasks work N` activated — requiring
        # `in_progress` meant the acknowledgement never fired for one. Since task
        # 140 activation writes `in_progress`; `pending` stays accepted for a task
        # activated by an older CLI, or whose status write could not land. A stub
        # is never activated (`tasks work` expands it); one in a pointer still does
        # not acknowledge.
        if str(_status_from_lines(_physical_lines(_text))).strip().lower() not in ("pending", "in_progress"):
            return False
        if stub_marker_type(_text) is not None:    # task 144: the marker line, not a quote
            return False
        if str(extract_risk_from_text(_text)).strip().lower() != "irreversible":
            return False
        # RE-READ the pointer: a task switch between the first read and here would
        # otherwise let a stale irreversible task acknowledge for a new one. No
        # lock is taken — a PreToolUse hook must never wait on a writer — and any
        # mismatch or error keeps the dangerous command BLOCKED, which is this
        # function's standing contract ("any failure → False").
        with open(pointer, encoding="utf-8") as fh:
            if fh.read().strip() != num:
                return False
        return True
    except BaseException:       # incl. SystemExit from resolve_agent_dir's marker check
        return False


def main() -> int:
    # FAIL-OPEN: any failure to read/parse must allow (never wedge a session).
    def _open_loudly(what: str) -> int:
        # Task 100 (PLAN S8c, impl panel r1+r2): every cannot-run arm is LOUD —
        # a malformed, empty or mis-shaped payload allows the call, and says so.
        print("[command-guard] WARNING: %s — failing OPEN (guard disabled for "
              "this call)" % what, file=sys.stderr)
        return 0

    # Read BYTES: a payload that is not valid UTF-8 (a truncated multibyte
    # sequence, a stray 0xFF) must be a loud fail-open, not a UnicodeDecodeError
    # traceback (D6-amended single judge, pass 1).
    try:
        raw_bytes = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8", "replace")
    except (OSError, ValueError) as exc:
        return _open_loudly("could not read the hook payload from stdin: %s" % exc)
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _open_loudly("hook payload is not valid UTF-8: %s" % exc)
    if not raw.strip():
        return _open_loudly("empty hook payload (no JSON on stdin)")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return _open_loudly("could not parse the hook payload as JSON: %s" % exc)
    if not isinstance(payload, dict):
        return _open_loudly("hook payload is not a JSON object (%s)" % type(payload).__name__)
    payload = _normalize_payload(payload)
    # Bash/Shell/run_terminal_command = Claude + grok (post-normalize);
    # exec_command = Codex's shell tool.
    if payload.get("tool_name") not in ("Bash", "Shell", "run_terminal_command", "exec_command"):
        return 0
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        return _open_loudly("tool_input is not an object (%s)" % type(ti).__name__)
    command = ti.get("command", ti.get("cmd", ""))     # codex exec may use either
    if not isinstance(command, (str, list)):
        return _open_loudly("tool_input.command is neither text nor argv (%s)" % type(command).__name__)

    root = _find_root()
    cfg = _load_cfg(root)
    if cfg.get("command_guard") is False:
        return 0
    # Operator acknowledgement (round 2: `=0` is not an ack). Task 110 (109 G2-14):
    # it is decided AFTER classification, so the allow it grants to a command that
    # WOULD have blocked is journalled like the irreversible-task ack below — an
    # override nobody can see afterwards was the one decision with no trace.
    env_ack = os.environ.get("PLAYBOOK_ALLOW_DANGEROUS", "").strip().lower() in ("1", "true", "yes", "on")

    extra = cfg.get("dangerous_commands")
    extra = extra if isinstance(extra, list) else []
    try:
        verdict, name, why = classify_command(command, extra)
    except Exception as exc:                            # fail-open on any bug —
        # but LOUDLY: PB-COMMAND-FAILURE-POLICY promises the guard says so on
        # stderr whenever it cannot run, and a silent `return 0` turned a BLOCK
        # into an ALLOW with no trace (plan panel 077, sol:medium #4).
        print("playbook command-guard: classifier error, failing OPEN "
              "(%s: %s)" % (exc.__class__.__name__, exc), file=sys.stderr)
        return 0
    if verdict != "block":
        return 0

    shown = command if isinstance(command, str) else " ".join(str(p) for p in command)

    if env_ack:
        try:
            j = _load_journal()
            if j is not None:
                j.append(j.resolve_lane_dir(root), "command-guard", "allow",
                         f"ack-operator-env:{name or 'dangerous-command'}",
                         session_id=_journal_session_id(),
                         tool=payload.get("tool_name", ""), command=shown)
        except Exception:
            pass
        return 0

    if _active_task_is_irreversible(root):
        # The documented in-session acknowledgement (task 073, impl panel: it was
        # a claim without code until now): the ACTIVE task is classified
        # `## Risk: irreversible`, read fence-aware through tasks.core. Logged.
        try:
            j = _load_journal()
            if j is not None:
                j.append(j.resolve_lane_dir(root), "command-guard", "allow",
                         f"ack-irreversible-task:{name or 'dangerous-command'}",
                         session_id=_journal_session_id(),
                         tool=payload.get("tool_name", ""), command=shown)
        except Exception:
            pass
        return 0

    # Enforcement-journal (log-only, best-effort): record the block. Wrapped AND
    # the helper itself swallows errors — journalling can never change the block.
    try:
        j = _load_journal()
        if j is not None:
            j.append(j.resolve_lane_dir(root), "command-guard", "block",
                     name or "dangerous-command",
                     session_id=_journal_session_id(),
                     tool=payload.get("tool_name", ""), command=shown)
    except Exception:
        pass

    sys.stderr.write(block_message(shown, name, why))
    return 2


def block_message(shown, name, why):
    """The block text. Task 073 (B1): the old text said "re-run with
    PLAYBOOK_ALLOW_DANGEROUS=1" — impossible from inside the agent session, the
    hook reads ITS OWN environment (a prefix on the command line cannot set it).
    Say where the acknowledgement actually has to happen."""
    return (
        f"BLOCKED — destructive/irreversible command ({name}): {why}.\n"
        f"  command: {str(shown).strip()[:200]}\n"
        "  If this is intended: confirm with the user and run it inside a task\n"
        "  classified `## Risk: irreversible` with a rollback plan (the interlock\n"
        "  stands down for that task). A one-off acknowledgement is the OPERATOR's:\n"
        "  PLAYBOOK_ALLOW_DANGEROUS=1 must be present in the environment the hook\n"
        "  runs in (the shell that started the agent, or the harness env settings) —\n"
        "  the hook reads its own environment, so a prefix on this command cannot\n"
        "  set it.\n")


if __name__ == "__main__":
    sys.exit(main())
