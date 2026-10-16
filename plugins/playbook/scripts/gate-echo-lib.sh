#!/bin/bash
# gate-echo-lib.sh
# Shared logic for hooks: project root detection + gate parsing.

# _canonical_path PATH
# The one seam where a shell-derived path meets a Python-derived one (the lane
# comparisons, write_log's arguments). On Linux both halves already speak the
# same form, so this is the identity. It stays as the named boundary: up to
# 1.5.47 (tag `last-multiplatform`) it converted Git Bash's mount form with
# `cygpath -m`, and bringing Windows back starts here.
_canonical_path() {
    printf '%s\n' "$1"
}

# find_project_root
# Walk up from $PWD looking for .agent/tasks/ (legacy) or .agent/<user>/tasks/
# (multi-user) — the definitive playbook marker.
# CLAUDE.md and MIND_MAP.md alone are NOT sufficient — they exist in non-playbook
# projects and would cause hooks to fire where they shouldn't.
# Outputs the project root path, or empty string if not found.
find_project_root() {
    local dir="$PWD"
    while true; do
        # Legacy layout
        if [ -d "$dir/.agent/tasks" ]; then
            echo "$dir"
            return 0
        fi
        # Multi-user layout: .agent/<user>/tasks/
        if [ -d "$dir/.agent" ]; then
            local sub
            for sub in "$dir/.agent"/*/; do
                if [ -d "${sub}tasks" ]; then
                    echo "$dir"
                    return 0
                fi
            done
        fi
        local parent
        parent=$(dirname "$dir")
        if [ "$parent" = "$dir" ]; then
            break
        fi
        dir="$parent"
    done
    echo ""
    return 0  # "not found" communicated via empty output, not exit code (set -e safe)
}

# SESSION_UNRESOLVED_MESSAGE — the one stderr line every writer prints when the
# session id is unresolved (task 105). Verbatim copy of
# tasks/core.py SESSION_UNRESOLVED_MESSAGE.
SESSION_UNRESOLVED_MESSAGE="playbook: no session identity — PLAYBOOK_SESSION_ID is not set and the process-tree walk either reached the Claude Code background daemon (bg-pty-host) or could not read an ancestor process with ps; no session state was written."

session_unresolved_notice() {
    echo "$SESSION_UNRESOLVED_MESSAGE" >&2
}

# _is_daemon_argv ARGV...
# True when the EXACT argv (one positional parameter per argument) is a Claude
# Code background-daemon process: basename(argv[0]) is claude/claude.exe and
# argv[1] is bg-pty-host/--bg-pty-host, or argv[1..2] is `daemon run` (owner
# decision 2026-09-28), applied to basename(argv[0])'s words + argv[1..] because
# the pty host rewrites its title into argv[0] ("claude bg-pty-host", measured
# live). The shared pty host / daemon is never a session root.
# Mirrors tasks/core.py _is_daemon_argv.
_is_daemon_argv() {
    [ "$#" -ge 1 ] || return 1
    # Live 2026-09-28: the pty host / spare rewrite their title, so argv[0] is
    # ONE argument "claude bg-pty-host". Split basename(argv[0]) on whitespace
    # and put its words in front of argv[1..] (mirrors core.py).
    local b0="${1##*/}"
    b0="${b0//$'\n'/ }"      # \n and \r split words like str.split() (post-D6)
    b0="${b0//$'\r'/ }"
    local -a head=()
    read -r -a head <<< "$b0" || true
    shift
    [ "${#head[@]}" -gt 0 ] || return 1
    set -- "${head[@]}" "$@"
    [ "$#" -ge 2 ] || return 1
    [ "$1" = "claude" ] || [ "$1" = "claude.exe" ] || return 1
    case "$2" in
        bg-pty-host|--bg-pty-host) return 0 ;;
    esac
    [ "$2" = "daemon" ] && [ "${3:-}" = "run" ] && return 0
    return 1
}

# _is_daemon_args LINE
# The `ps` fallback (no /proc mounted): the flattened `ps -o args=` line split
# on whitespace and fed to the SAME detector. \n and \r become spaces first so
# the split equals Python's str.split(). HEURISTIC by construction (an argv[0]
# with spaces cannot be told from argument boundaries) — a declared limitation
# of that fallback; no parsing rules are added here (owner decision 2026-09-28).
_is_daemon_args() {
    local a="$1"
    a="${a//$'\n'/ }"
    a="${a//$'\r'/ }"
    local -a toks=()
    read -r -a toks <<< "$a" || true
    [ "${#toks[@]}" -gt 0 ] || return 1
    _is_daemon_argv "${toks[@]}"
}

# _proc_status PID  — sets _PB_PPID / _PB_NAME from <proc root>/PID/status
# (Name:, PPid:); returns 1 when unreadable. Test seam: PLAYBOOK_PROC_ROOT.
_proc_status() {
    local f="${PLAYBOOK_PROC_ROOT:-/proc}/$1/status" line
    _PB_PPID=""
    _PB_NAME=""
    _PB_STATE=""
    [ -r "$f" ] || return 1
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            Name:*) _PB_NAME="${line#Name:}"
                    _PB_NAME="${_PB_NAME#"${_PB_NAME%%[![:space:]]*}"}"
                    _PB_NAME="${_PB_NAME%"${_PB_NAME##*[![:space:]]}"}" ;;
            PPid:*) _PB_PPID="${line#PPid:}"
                    _PB_PPID="${_PB_PPID//[[:space:]]/}" ;;
            State:*) _PB_STATE="${line#State:}"
                     _PB_STATE="${_PB_STATE#"${_PB_STATE%%[![:space:]]*}"}"
                     _PB_STATE="${_PB_STATE:0:1}" ;;
        esac
    done < "$f" 2>/dev/null
    case "$_PB_PPID" in
        ""|*[!0-9]*) return 1 ;;
    esac
    [ -n "$_PB_NAME" ]
}

# _proc_argv PID — fills the array _PB_ARGV with the exact argv from
# <proc root>/PID/cmdline (NUL-separated, read with `read -d ''`, bash 3.2);
# returns 1 when empty or unreadable.
_proc_argv() {
    local f="${PLAYBOOK_PROC_ROOT:-/proc}/$1/cmdline" a=""
    _PB_ARGV=()
    [ -r "$f" ] || return 1
    while IFS= read -r -d '' a; do
        _PB_ARGV[${#_PB_ARGV[@]}]="$a"
    done < "$f" 2>/dev/null
    [ -n "$a" ] && _PB_ARGV[${#_PB_ARGV[@]}]="$a"   # a last arg with no trailing NUL
    [ "${#_PB_ARGV[@]}" -gt 0 ]
}

# _ps_field PID FIELDS
# `ps -ww -p PID -o FIELDS`, or empty when it cannot be read — the fallback when
# /proc is missing. -ww: procps clips to $COLUMNS even on a pipe. One
# retry so a single hiccup does not fail a live session closed. Mirrors
# tasks/core.py _ps_field.
_ps_field() {
    local out _try
    for _try in 1 2; do
        out=$(ps -ww -p "$1" -o "$2" 2>/dev/null) || out=""
        if [ -n "${out//[[:space:]]/}" ]; then
            echo "$out"
            return 0
        fi
    done
    return 0
}

# _agent_walk
# Walk the parent process tree once. Output "<pid>" of the highest agent
# ancestor BELOW any Claude Code daemon process, "daemon" when the walk stopped
# (daemon, or an unreadable ancestor) with no agent below it, or "" when no
# agent was found within 20 hops. It reads /proc/<pid>/status + cmdline
# (exact); only without <proc root>/self (no /proc mounted) does it use `ps`. Mirrors
# `_walk_agent_ancestry()` in tasks/core.py.
_agent_walk() {
    # `_agent_walk all` prints EVERY agent pid below any daemon (bottom-up,
    # space-separated; empty when none) instead of the root — for the env-id
    # check (task 106), mirroring core.py _walk_agent_chain.
    # `_agent_walk trace` (task 192, #48) prints the default answer, a TAB, and
    # what THIS walk read: each hop `<pid> <name> <state>` (a claude* process's
    # argument COUNT, never its words — a `claude -p` argv holds the prompt), then
    # `| stop: …` where it stopped. Names are reduced to [A-Za-z0-9._+-?] so the
    # line stays one parseable journal field (impl panel r1).
    local mode="${1:-}" agents="" tr="" why=""
    local use_proc=""
    [ -d "${PLAYBOOK_PROC_ROOT:-/proc}/self" ] && use_proc=1
    local pid=$PPID
    local last_agent=""
    local count=0
    local info ppid comm args
    while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ] && [ "$count" -lt 20 ]; do
        if [ -n "$use_proc" ]; then
            if _proc_status "$pid"; then
                ppid=$_PB_PPID
                comm=$_PB_NAME
            else
                ppid=""
                comm=""
            fi
        else
            if [ "$count" -eq 0 ] && ! command -v ps >/dev/null 2>&1; then
                why="no ps"
                break   # no ps binary at all (minimal container): legacy fallback
            fi
            info=$(_ps_field "$pid" ppid=,comm=)
            ppid=$(echo "$info" | awk '{print $1}')
            comm=$(echo "$info" | awk '{$1=""; sub(/^ +/, ""); print}')
            case "$ppid" in
                ""|*[!0-9]*) ppid="" ;;
            esac
        fi
        if [ -z "$ppid" ] || [ -z "$comm" ]; then
            # An ancestor we cannot read, on any hop: it could be the shared
            # daemon — never fall back to a made-up pid. Mirrors core.py.
            if [ "$mode" = trace ]; then
                if [ -n "$use_proc" ] && [ ! -e "${PLAYBOOK_PROC_ROOT:-/proc}/$pid" ]; then why="$pid gone"; else why="$pid unreadable"; fi
            fi
            _pb_walk_out "$mode" "$agents" "${last_agent:-daemon}" "$tr | stop: $why"
            return 0
        fi
        comm="${comm##*/}"  # parameter expansion: strip path; safe for "-zsh" (basename would error)
        if [ "$mode" = trace ]; then
            tr="$tr > $pid ${comm//[^A-Za-z0-9._+-]/?}"
            if [ -n "$use_proc" ]; then tr="$tr ${_PB_STATE//[^A-Za-z]/?}"; fi
        fi
        case "$comm" in
            claude*)
                # Task 105: the daemon's processes are `claude.exe` too; they are
                # shared by every hosted session — stop, never climb past them.
                # An unreadable argv counts as the boundary (round-1 R1-1).
                why=""
                if [ -n "$use_proc" ]; then
                    if ! _proc_argv "$pid"; then
                        why="$pid unreadable argv"
                    elif _is_daemon_argv "${_PB_ARGV[@]}"; then
                        why="$pid daemon"
                    fi
                    if [ "$mode" = trace ]; then tr="$tr argc=${#_PB_ARGV[@]}"; fi
                else
                    args=$(_ps_field "$pid" args=)
                    if [ -z "$args" ]; then
                        why="$pid unreadable argv"
                    elif _is_daemon_args "$args"; then
                        why="$pid daemon"
                    fi
                fi
                if [ -n "$why" ]; then
                    _pb_walk_out "$mode" "$agents" "${last_agent:-daemon}" "$tr | stop: $why"
                    return 0
                fi
                last_agent=$pid; agents="$agents $pid" ;;
            codex|agy|grok|pi) last_agent=$pid; agents="$agents $pid" ;;
        esac
        if [ "$ppid" = "$pid" ]; then why="$pid own parent"; break; fi
        pid=$ppid
        count=$((count + 1))
    done
    if [ -z "$why" ]; then
        if [ "$count" -ge 20 ]; then why="20 hops"; else why="top $pid"; fi
    fi
    _pb_walk_out "$mode" "$agents" "$last_agent" "$tr | stop: $why"
}

# _pb_walk_out MODE AGENTS VALUE TRACE — how `_agent_walk` answers where it stops:
# the agent list (`all`), the value and a TAB and what the walk read (`trace`),
# or the value.
_pb_walk_out() {
    case "$1" in
        all) echo "${2# }" ;;
        trace) printf '%s\t%s\n' "$3" "${4# > }" ;;
        *) echo "$3" ;;
    esac
}

# find_agent_root_pid
# Output PID of the highest agent ancestor (claude, claude.exe, claude*, codex,
# agy, grok, pi) below any Claude Code daemon process, or empty if none found
# within 20 hops. Mirrors `find_agent_root_pid()` in tasks/core.py. Used as
# fallback when PLAYBOOK_SESSION_ID env var isn't propagated.
find_agent_root_pid() {
    local r
    r=$(_agent_walk)
    [ "$r" = "daemon" ] && r=""
    echo "$r"
}

# _env_pid_is_stale ID
# Task 106: true when an env id `pid-<digits>` does NOT name a live agent (dead,
# or alive with a non-agent comm) — a resumed conversation re-sources its OLD
# env file and a child claude inherits the parent's env, so `pid-N` can name a
# dead process. N need NOT be an ancestor. Other ids are never stale. /proc
# status on Linux, `ps -o comm=` without /proc; no ps binary → cannot judge →
# keep. Mirrors tasks/core.py _env_pid_is_stale (leading zeros and >10 digits
# treated the same way).
_env_pid_is_stale() {
    case "$1" in
        pid-*) ;;
        *) return 1 ;;
    esac
    local n="${1#pid-}" comm
    case "$n" in
        ""|*[!0-9]*) return 1 ;;
    esac
    [ "${#n}" -le 10 ] || return 0
    n=$((10#$n))
    [ "$n" -gt 0 ] || return 0
    local out stat args
    if [ -d "${PLAYBOOK_PROC_ROOT:-/proc}/self" ]; then
        _proc_status "$n" || return 0
        case "$_PB_STATE" in
            Z|X) return 0 ;;                 # zombie (round 1 R1-2)
        esac
        comm="${_PB_NAME##*/}"
        case "$comm" in
            claude*)
                # R1-1: the daemon's processes are claude.exe too, but never a
                # session; an unreadable argv counts as not-a-session
                _proc_argv "$n" || return 0
                _is_daemon_argv "${_PB_ARGV[@]}" && return 0
                return 1 ;;
            codex|agy|grok|pi) return 1 ;;
        esac
        return 0
    fi
    command -v ps >/dev/null 2>&1 || return 1
    out=$(_ps_field "$n" state=,comm=)   # `state`: BSD ps + procps (task 106)
    out="${out#"${out%%[![:space:]]*}"}"
    stat="${out%%[[:space:]]*}"
    comm="${out#"$stat"}"
    comm="${comm#"${comm%%[![:space:]]*}"}"
    comm="${comm%"${comm##*[![:space:]]}"}"
    [ -n "$stat" ] && [ -n "$comm" ] || return 0
    case "$stat" in
        Z*|X*) return 0 ;;
    esac
    comm="${comm##*/}"
    case "$comm" in
        claude*)
            args=$(_ps_field "$n" args=)
            [ -n "$args" ] || return 0
            _is_daemon_args "$args" && return 0
            return 1 ;;
        codex|agy|grok|pi) return 1 ;;
    esac
    return 0
}

# resolve_session_id
# Returns the session_id used to namespace .agent/sessions/<id>/.
# Order: PLAYBOOK_SESSION_ID env (a `pid-<digits>` only if it names a live
# agent, task 106) → ancestor scan (root agent PID, never a
# daemon process) → "" when the scan hit the Claude Code background daemon with
# no agent below it (task 105: unresolved — every writer must check for "" and
# write nothing, see session_unresolved_notice) → immediate-parent PID. Mirrors
# resolve_session_id() in tasks/core.py — Python and bash converge on the same
# value when env var is unset.
resolve_session_id() {
    # `resolve_session_id trace` (task 192, #48): the same id, a TAB, and what decided it — for the walk, the
    # hops THIS walk read (`_agent_walk trace`), so a transient failure is journaled as it happened.
    local _pb_t="${1:-}" _pb_e="${PLAYBOOK_SESSION_ID:-unset}"
    _pb_e="${_pb_e//[^A-Za-z0-9._-]/?}"
    _pb_e="ppid=$PPID env=${_pb_e:0:40}"
    if [ -n "${PLAYBOOK_SESSION_ID:-}" ]; then
        # Sanitize (C4): this value becomes a path component in `rm -rf
        # .agent/sessions/<id>` and in every hook. An unsanitized `../tasks`
        # deleted the task DB. Accept only a safe single component (the
        # canonical `pid-*` ids AND the sanctioned `judge` session id);
        # NEUTRALIZE anything else — a slash, whitespace, or the traversal
        # components `.`/`..` — by falling through to the derived pid. Mirrors
        # tasks.core._sanitize_session_id.
        case "$PLAYBOOK_SESSION_ID" in
            .|..) : ;;                       # traversal → neutralize
            *[!A-Za-z0-9._-]*) : ;;          # slash / space / control char → neutralize
            *)
                # Task 106: a `pid-<digits>` that is not a live agent is stale —
                # fall through to the walk (silently here; the Python CLI prints
                # the one line).
                if ! _env_pid_is_stale "$PLAYBOOK_SESSION_ID"; then
                    # Owner-accepted deviation 2026-09-28: a live agent's
                    # `pid-N` is refused when the walk finds agents and N is
                    # not one of them (an id inherited from a live sibling);
                    # honored when N is any agent in the chain (codex run from
                    # a claude's Bash) or there is none. Mirrors core.py
                    # _env_pid_rejection.
                    local _pb_w _pb_n="${PLAYBOOK_SESSION_ID#pid-}"
                    case "$PLAYBOOK_SESSION_ID" in
                        pid-*) ;;
                        *) _pb_sid_out "$_pb_t" "$PLAYBOOK_SESSION_ID" "$_pb_e: honored, not a pid id"; return 0 ;;
                    esac
                    case "$_pb_n" in
                        ""|*[!0-9]*) _pb_sid_out "$_pb_t" "$PLAYBOOK_SESSION_ID" "$_pb_e: honored, not a pid id"; return 0 ;;
                    esac
                    _pb_w=$(_agent_walk all)
                    if [ -z "$_pb_w" ]; then
                        _pb_sid_out "$_pb_t" "$PLAYBOOK_SESSION_ID" "$_pb_e: honored, no agent in the walk"; return 0
                    fi
                    if [ "${#_pb_n}" -le 10 ]; then
                        _pb_n=$((10#$_pb_n))
                        case " $_pb_w " in
                            *" $_pb_n "*) _pb_sid_out "$_pb_t" "$PLAYBOOK_SESSION_ID" "$_pb_e: honored, an agent of the walk"; return 0 ;;
                        esac
                    fi
                fi ;;
        esac
    fi
    local agent_pid _pb_read=""
    if [ "$_pb_t" = trace ]; then
        agent_pid=$(_agent_walk trace)
        _pb_read="${agent_pid#*$'\t'}"
        agent_pid="${agent_pid%%$'\t'*}"
    else
        agent_pid=$(_agent_walk)
    fi
    case "$agent_pid" in
        daemon) _pb_sid_out "$_pb_t" "" "$_pb_e walk: $_pb_read" ;;
        "") _pb_sid_out "$_pb_t" "pid-$PPID" "$_pb_e walk: $_pb_read" ;;
        *) _pb_sid_out "$_pb_t" "pid-$agent_pid" "$_pb_e walk: $_pb_read" ;;
    esac
}

# _pb_sid_out TRACE? ID WHAT — the resolver's answer: the id, or (`trace`) the id, a TAB and what decided it.
_pb_sid_out() {
    if [ "$1" = trace ]; then printf '%s\t%s\n' "$2" "$3"; else echo "$2"; fi
}

# journal_session_walk AGENT_DIR HOOK SESSION_ID TOOL READ
# Task 192 (#48): journal a `session-walk` record — `read: <READ>`, what the resolver's own walk read (READ is the
# part after the TAB of `resolve_session_id trace`) — on an unresolved id, then `again=` what a walk right after
# resolves; and, when PLAYBOOK_WALK_TRACE=1 (the live gauntlet sets it), on every call. Unresolved records are
# capped per day (impl panel r1: a hook that always runs unresolved must not grow the journal without bound): a
# walk that stopped at the daemon — a shape that repeats on every call — at PB_WALK_DAEMON_DAY_CAP, any other
# (the transient misses #48 is after) at PB_WALK_DAY_CAP, so a daemon burst cannot use up the day of the others
# (impl panel r2). Counted in `journal/session-walk.count` (`<day> <others> <daemon>`, replaced by rename; an
# unreadable or foreign-day line counts from 0); the record that reaches a cap says so. The counter takes no lock,
# so hooks running side by side can lose an increment: the caps bound growth, not an exact count. Log-only and
# best-effort like journal_enforcement: every step swallows its error (the gate runs under `set -e`), it never
# changes a decision, it returns 0.
PB_WALK_DAY_CAP=100
PB_WALK_DAEMON_DAY_CAP=20
journal_session_walk() {
    local all="" reason again cnt day line rest a b n cap tmp
    if [ "${PLAYBOOK_WALK_TRACE:-}" = 1 ]; then all=1; fi
    if [ -n "$3" ] && [ -z "$all" ]; then return 0; fi
    if [ -z "$1" ] || [ ! -d "$1" ]; then return 0; fi
    reason="read: ${5:-}"
    if [ -z "$3" ]; then
        again=$(resolve_session_id 2>/dev/null) || again=""
        reason="$reason | again=${again:-unresolved}"
        if [ -z "$all" ]; then
            cnt="$1/journal/session-walk.count"
            if [ ! -d "$1/journal" ]; then mkdir -p "$1/journal" 2>/dev/null || return 0; fi
            # a linked or non-regular counter is not ours (impl panel r2, measured: a link was truncated through,
            # a FIFO stalled the gate): no record at all; the counter is replaced by rename, never written in place
            _pb_sink_ok "$1/journal" "$cnt" || return 0
            day=$(date +%Y-%m-%d 2>/dev/null) || day=""
            line=""
            if [ -f "$cnt" ]; then IFS= read -r line < "$cnt" 2>/dev/null || :; fi
            a=0; b=0
            case "$line" in
                "$day "*)
                    rest="${line#"$day "}"
                    a="${rest%% *}"
                    b="${rest#* }"
                    if [ "$b" = "$rest" ]; then b=0; fi ;;
            esac
            case "$a" in ""|*[!0-9]*) a=0 ;; esac
            case "$b" in ""|*[!0-9]*) b=0 ;; esac
            if [ "${#a}" -gt 6 ]; then a=0; fi
            if [ "${#b}" -gt 6 ]; then b=0; fi
            a=$((10#$a)); b=$((10#$b))
            case "${5:-}" in
                *"| stop: "*" daemon") cap=$PB_WALK_DAEMON_DAY_CAP; n=$b ;;
                *) cap=$PB_WALK_DAY_CAP; n=$a ;;
            esac
            if [ "$n" -ge "$cap" ]; then return 0; fi
            n=$((n + 1))
            if [ "$cap" = "$PB_WALK_DAEMON_DAY_CAP" ]; then b=$n; else a=$n; fi
            tmp=$(mktemp "$1/journal/.session-walk.XXXXXX" 2>/dev/null) || return 0
            if ! { printf '%s %s %s\n' "$day" "$a" "$b" > "$tmp" && mv -f "$tmp" "$cnt"; } 2>/dev/null; then
                rm -f "$tmp" 2>/dev/null || :
                return 0
            fi
            if [ "$n" -eq "$cap" ]; then
                reason="$reason | the day's cap of $cap records of this kind is reached: no more today"
            fi
        fi
    fi
    journal_enforcement "$1" "$2" session-walk "$reason" "$3" "${4:-}"
    return 0
}

# resolve_agent_dir PROJECT_DIR
# Echoes the agent state directory:
#   absent .agent/current_user  → PROJECT_DIR/.agent        (legacy)
#   valid  .agent/current_user  → PROJECT_DIR/.agent/<user> (multi-user)
#   invalid content             → stderr + exit 1
# Marker contract (identical in tasks/core.py, provider/paths.py, and every
# inline shell copy — tests/test_provider_multiuser.py pins all of them to the
# same vectors):
#   * exactly ONE content line; a second non-empty line is INVALID
#     (`alice\n../evil` must not silently resolve to lane `alice`)
#   * a trailing CR is stripped, so a CRLF-saved marker works (read tolerance)
#   * a missing trailing newline is fine — `read` returns 1 but still assigns,
#     hence `|| true`; without it errexit fires inside a DEBUG trap
#   * surrounding whitespace is ignored, matching Python's .strip()
resolve_agent_dir() {
    local project_dir="$1"
    local marker="$project_dir/.agent/current_user"
    if [ ! -f "$marker" ]; then
        echo "$project_dir/.agent"
        return 0
    fi
    local name="" extra=""
    # Read builtins only — the old sed+grep form accepted a MULTI-LINE marker
    # (grep matches any line), so `alice\n../evil` resolved to lane `alice`
    # while Python rejected the same file.
    { read -r name; read -r extra; } < "$marker" 2>/dev/null || true
    name="${name%$'\r'}"
    if [ -n "$extra" ]; then
        echo "Error: .agent/current_user must contain exactly one username line." >&2
        exit 1
    fi
    # Validate: non-empty, not . or .., no slash, matches [a-zA-Z0-9][a-zA-Z0-9_.-]*
    if [ -z "$name" ] || [ "$name" = "." ] || [ "$name" = ".." ]; then
        echo "Error: .agent/current_user contains invalid username '${name}'. Must be non-empty and not . or .." >&2
        exit 1
    fi
    case "$name" in
        */*) echo "Error: .agent/current_user contains invalid username '${name}'. Slashes not allowed." >&2; exit 1 ;;
        [a-zA-Z0-9]*) ;;
        *) echo "Error: .agent/current_user contains invalid username '${name}'. Must start with a letter or digit." >&2; exit 1 ;;
    esac
    case "$name" in
        *[!a-zA-Z0-9_.-]*)
            echo "Error: .agent/current_user contains invalid username '${name}'. Use only letters, digits, hyphens, underscores, dots." >&2
            exit 1 ;;
    esac
    echo "$project_dir/.agent/$name"
}

# lanes_without_marker PROJECT_DIR
# Echoes the per-user lane names when the repo is in the "fresh clone" shape
# (lanes present, gitignored `.agent/current_user` absent, no root
# `.agent/tasks/` — see require_lane_marker for why that last case is exempt),
# empty otherwise. Never exits: hooks use this to SKIP quietly, since aborting
# would brick the user's session over a state file.
# Behaviorally identical to lanes_without_marker() in tasks/core.py and
# provider/paths.py; the shared vector table in tests/test_provider_multiuser.py
# holds all three to the same answers.
lanes_without_marker() {
    local project_dir="$1"
    [ -n "$project_dir" ] || return 0
    [ -f "$project_dir/.agent/current_user" ] && return 0
    [ -d "$project_dir/.agent/tasks" ] && return 0
    [ -d "$project_dir/.agent" ] || return 0
    local sub found=""
    for sub in "$project_dir/.agent"/*/; do
        if [ -d "${sub}tasks" ]; then
            found="${sub%/}"
            found="${found##*/}"
            echo "$found"
        fi
    done
    return 0
}

# require_lane_marker PROJECT_DIR [CONTEXT]
# Fail loud on the "fresh clone of a multi-user repo" shape: per-user lanes
# (.agent/<user>/tasks/) exist, but .agent/current_user does NOT — because that
# marker is gitignored install-local, so it never arrives with a clone.
# resolve_agent_dir would silently answer the ROOT .agent/ there, and whoever
# provisions state next creates a phantom root lane beside the real ones.
#
# Deliberately narrow: a repo that ALSO has root .agent/tasks/ is a legitimate
# mixed/legacy layout (root is a real lane — see the lane model in core.py), so
# it is left alone. Only the unambiguous lanes-but-no-root, no-marker case fails.
#
# Callers: the provider wrappers and `init` — anything that CREATES state.
# Read-only surfaces must NOT call this; they should degrade, not abort.
require_lane_marker() {
    local project_dir="$1"
    local context="${2:-playbook}"
    [ -n "$project_dir" ] || return 0
    [ -f "$project_dir/.agent/current_user" ] && return 0
    [ -d "$project_dir/.agent/tasks" ] && return 0
    [ -d "$project_dir/.agent" ] || return 0

    local found
    found=$(lanes_without_marker "$project_dir" | tr '\n' ' ')
    found="${found% }"
    [ -n "$found" ] || return 0

    {
        echo "Error: $context found per-user playbook lanes but no .agent/current_user marker."
        echo ""
        echo "  Project: $project_dir"
        echo "  Lane(s): $found"
        echo ""
        echo "The marker is gitignored install-local, so a fresh clone never receives it."
        echo "Without it every surface would fall back to the shared root .agent/, creating"
        echo "a phantom lane beside the real ones. Pick your lane first:"
        echo ""
        echo "    echo '<your-username>' > \"$project_dir/.agent/current_user\""
        echo ""
    } >&2
    exit 1
}

# agent_dir_writable PROJECT_DIR
# Returns 0 if the resolved agent dir exists and is writable, 1 otherwise.
# Use this before any hook that writes to .agent/ — in sandbox mode
# the directory may exist but be read-only.
agent_dir_writable() {
    local agent_dir
    # Fresh-clone shape: no knowable lane. Report unwritable so the state-writing
    # hooks skip rather than mint a phantom root lane (they must never exit —
    # that would take the session down over a log file).
    [ -n "$(lanes_without_marker "$1")" ] && return 1
    agent_dir=$(resolve_agent_dir "$1")
    [ -d "$agent_dir" ] && [ -w "$agent_dir" ]
}

# task_record_file AGENT_DIR TASK
# The record of task TASK: `AGENT_DIR/tasks/<TASK>-*/task.md`, matched on the task DIRECTORY's own name, the first
# directory in byte order of the NAMES (the CLI's `sorted(Path)`, which compares components: `007-fix` before
# `007-fix-2` — globbing `…/task.md` whole sorted `-` before `/`, impl panel r1), or nothing. TASK must be digits
# (the callers check it too). Task 192 (#25): the hooks used `find … -path "*/${TASK}-*/*"`, which matches the WHOLE
# path — in a project directory named `007-proj` every record matched pointer 007, and with no 007 record another
# task's record still resolved. As `find` did: a linked task directory is not followed, and any entry named task.md
# counts. Always returns 0 (callers run under `set -e`).
task_record_file() {
    local d="$1/tasks" e
    case "$2" in
        ''|*[!0-9]*) return 0 ;;
    esac
    [ -d "$d" ] || return 0
    (
        LC_ALL=C
        for e in "$d/$2"-*; do
            [ -L "$e" ] && continue
            [ -e "$e/task.md" ] || [ -L "$e/task.md" ] || continue
            printf '%s\n' "$e/task.md"
            break
        done
    )
    return 0
}

# get_gate_info TASK_FILE
# Outputs: done_count total_count gate_line gate_text
# If all done: gate_line and gate_text are empty
get_gate_info() {
    local task_file="$1"

    if [ ! -f "$task_file" ]; then
        echo "0 0 0 ''"
        return 1
    fi

    # Count total and done checkboxes (only at line start, not in backticks)
    # Pattern: only match [ ], [x], [X] — not [8] or [40] (reference links)
    local total
    total=$(grep -cE '^[[:space:]]*- \[( |x|X)\]' "$task_file" 2>/dev/null) || total=0
    local done
    done=$(grep -cE '^[[:space:]]*- \[[xX]\]' "$task_file" 2>/dev/null) || done=0

    # Find first unchecked gate
    local gate_line=""
    local gate_text=""

    while IFS= read -r line; do
        local lineno="${line%%:*}"
        local content="${line#*:}"
        if echo "$content" | grep -qE '^[[:space:]]*- \[ \]'; then
            gate_line="$lineno"
            gate_text=$(echo "$content" | sed 's/^[[:space:]]*- \[ \] *//')
            break
        fi
    done < <(grep -nE '^[[:space:]]*- \[ \]' "$task_file" 2>/dev/null)

    echo "$done $total $gate_line $gate_text"
}

# read_counter FILE KEY
# Read a key=value from the counter file. Outputs the value, or empty if missing.
read_counter() {
    local file="$1" key="$2"
    if [ -f "$file" ]; then
        sed -n "s/^${key}=//p" "$file" 2>/dev/null | head -1
    fi
}

# _safe_int VALUE
# Coerce arbitrary (untrusted) bytes to a non-negative decimal integer,
# defaulting to 0. Counter/offset files live under .agent/, which the task-gate
# EXEMPTS from the code-edit gate, so their content is untrusted — and bash
# arithmetic EXECUTES command substitution / array subscripts embedded in an
# operand: `tools=x[$(touch PWNED)]` ran `touch` inside `$(( TOOLS + 1 ))` (C5).
# NEVER feed raw file bytes to `$(( ))`; run them through this first. `10#`
# forces base-10 so a leading-zero counter ("008") doesn't trip octal parsing.
# An all-digit but absurdly long value (>18 digits) would overflow bash's signed
# 64-bit `$(( ))` and print a NEGATIVE number, breaking the "non-negative"
# contract (F10). Real counters never approach 10^18, so clamp overlong input
# to 0 rather than return a wrapped negative.
_safe_int() {
    case "$1" in
        ''|*[!0-9]*) printf '0' ;;
        *) if [ "${#1}" -gt 18 ]; then printf '0'; else printf '%d' "$((10#$1))"; fi ;;
    esac
}

# read_counter_int FILE KEY — read_counter coerced to a safe integer (C5).
read_counter_int() {
    _safe_int "$(read_counter "$1" "$2")"
}

# is_code_file_path FILE_PATH
# Returns 0 for source code paths that should require an active task.
#
# F2 (1.5.18): this MUST agree in behavior with the Python `_is_code_file_path`
# in provider/policy.py — the default Claude path enforces via this bash hook,
# the opt-in codex apply_patch gate enforces via that Python one, and "no code
# without a task" has to mean the same thing under every provider.
# tests/test_gate_classifier_parity.py pins the agreement over a shared vector
# table; edit BOTH together. The one deliberate asymmetry is the shebang branch
# below — bash can read the working tree, the codex pre-decision sees a patch and
# not always a file, so it is a bash-only superset, never a hole.
#
# Algorithm: extension decides first (code ext -> gate; doc/data ext -> exempt);
# an undecided extension gates iff a path component is a known code dir.
# Extension match is case-insensitive (parity with Python's .lower()); use `tr`
# not `${x,,}` (works in any bash, 3.2 included).
is_code_file_path() {
    local file_path="$1"
    local norm="$file_path"                  # a backslash is a file-name character (task 159)
    # Strip trailing CR/LF (Python parity: norm = ....rstrip("\r\n")). Without
    # this a crafted trailing \r stays inside the extension (".py\r"), defeats
    # the code-ext match, finds no code dir, and falls through to ALLOW — while
    # the Python twin gates the same path (a fail-open on the Claude enforcement
    # path). Strip ALL trailing \r/\n so \r, \n, and \r\n classify identically.
    while [ -n "$norm" ]; do
        case "$norm" in
            *[$'\r\n']) norm="${norm%?}" ;;
            *) break ;;
        esac
    done
    local base="${norm##*/}"                 # basename
    # Strip LEADING dots before finding the extension, so a dots-then-name
    # basename (".gitignore", "..py", "...toml") has NO extension — matching
    # Python's os.path.splitext, which ignores leading dots (1.5.20 parity fix).
    local stripped="$base"
    while [ "${stripped#.}" != "$stripped" ]; do stripped="${stripped#.}"; done
    local ext=""
    case "$stripped" in
        *.*) ext=".$(printf '%s' "${stripped##*.}" | tr '[:upper:]' '[:lower:]')" ;;
    esac

    case "$ext" in
        # Code — languages (both prior lists ∪ common extras) + strict config/markup.
        .py|.ts|.js|.mjs|.cjs|.tsx|.jsx|.sh|.bash|.go|.rs|.rb|.java|.c|.cpp|.h|.hpp|.swift|.kt|.kts|.dart|.cs|.php|.r|.m|.mm|.scala|.zig|.lua|.ex|.exs|.ml|.mli|.tf|.vue|.svelte|.ipynb|.css|.scss|.less|.html|.sql|.yaml|.yml|.toml|.proto|.graphql|.gradle)
            return 0 ;;
        # Docs / data / binaries — never code, even inside a code dir.
        .md|.txt|.json|.png|.svg|.jpg|.jpeg|.gif|.ico|.webp|.pdf|.lock|.csv)
            return 1 ;;
    esac

    # Undecided by extension -> code iff a path component is a known code dir.
    case "/$norm/" in
        */scripts/*|*/bin/*|*/src/*|*/hooks/*|*/lib/*|*/cmd/*)
            return 0 ;;
    esac

    # bash-only superset: an extensionless EXISTING file with a shebang.
    if [ -z "$ext" ] && [ -f "$file_path" ] && head -1 "$file_path" 2>/dev/null | grep -q '^#!'; then
        return 0
    fi

    return 1
}

# ── The counters lock (task 182, PLAN S11 item 17) ──────────────────────────
# A session's `counters` file is rewritten WHOLE by every writer — write_counter
# copies the other keys and renames the copy over it, reset_counters writes it
# anew — and the host runs the hooks of one message's tool calls side by side.
# Measured with the real state-echo-hook: 40 Bash calls two at a time were
# counted as tools=20 writes=20; each pair read the same file and the second
# rename dropped the first's increment. A `writes` that is too low is the
# releasing side of the stop hook. So every read-modify-write of the file
# happens under ONE lock per session: `<counters>.lock`, flock, descriptor 204,
# taken in the CALLING shell, so that what a step sets stays set.
#
# counters_lock FILE / counters_unlock — a pair. They nest: write_counter
# inside a caller's lock does not wait for itself. They never fail the caller.
# What a step does when it has no lock depends on WHY:
#   - there is no lock to take: no `flock` on the host; a `flock` that cannot
#     wait with a limit (it refuses AT ONCE, where a wait that ran out takes its
#     time — BusyBox; round 3); or a session directory that cannot be written
#     (the write itself then says so). The step goes on WITHOUT one, as every
#     step did before the task. Silent; a count can be lost there, as before.
#   - there IS one and it was not given: not within PLAYBOOK_COUNTERS_LOCK_WAIT
#     seconds (1 or 2; anything else means 2 — the host kills a hook after 5,
#     and the limit is for the PROCESS: a hook takes the lock twice, and the
#     second hold waits only for what the first left); what stands in the
#     lock's place is no plain file (a FIFO there made the OPEN wait for ever);
#     or it cannot be opened where it could have been made. Then NOTHING is
#     written to the counters by this process — no copy, no rename, nothing
#     that could land later over what was counted under the lock (three rounds
#     of the task's panel each found a way for a write made without the lock to
#     release a stop). Where what was not written is a COUNT (`tools`,
#     `writes`) or the reset, the session is MARKED instead (file
#     `counts_unsure` beside the counters): the counts are behind, and the stop
#     hook uses neither of its low-activity releases until reset_counters — the
#     user's next prompt — has run UNDER the lock. A gate field that was not
#     written leaves no mark: the stop hook does not read those, and the next
#     call writes them. PLAYBOOK_COUNTERS_UNLOCKED=true lets the hook say what
#     happened; after it the process does not wait again.
# A reader takes no lock. A single write replaces the file by a rename; the reset
# rewrites it in place, and the stop hook reads `writes` and `tools` in two
# reads — so a reader can meet a reset half-way (zero counts, or none yet).
PLAYBOOK_COUNTERS_UNLOCKED=false
PLAYBOOK_COUNTERS_WAITED=""
_PB_COUNTERS_DEPTH=0
_PB_COUNTERS_FD=false
_PB_COUNTERS_SPENT_MS=0     # what this process has already waited for the lock
_PB_COUNTERS_NOLOCK=false   # this host's `flock` cannot wait with a limit: no lock to take

counters_lock() {
    _PB_COUNTERS_DEPTH=$(( _PB_COUNTERS_DEPTH + 1 ))
    [ "$_PB_COUNTERS_DEPTH" -eq 1 ] || return 0
    [ "$PLAYBOOK_COUNTERS_UNLOCKED" != true ] || return 0
    [ "$_PB_COUNTERS_NOLOCK" != true ] || return 0
    command -v flock &>/dev/null || return 0
    local lock="$1.lock" wait="${PLAYBOOK_COUNTERS_LOCK_WAIT:-}" left began t0 t1 took given=true
    case "$wait" in [1-2]) ;; *) wait=2 ;; esac
    if [ -e "$lock" ] || [ -L "$lock" ]; then
        if [ ! -f "$lock" ] || [ -L "$lock" ]; then
            _counters_unsure "$1" "$wait"
            return 0
        fi
    fi
    if ! { exec 204>>"$lock"; } 2>/dev/null; then
        [ ! -w "$(_counters_dir "$1")" ] || _counters_unsure "$1" "$wait"
        return 0
    fi
    _PB_COUNTERS_FD=true
    # one wait for the whole process: the second hold gets what the first left, in
    # whole seconds; with nothing left it only tries
    left=$(( wait - _PB_COUNTERS_SPENT_MS / 1000 ))
    began=$SECONDS
    t0="${EPOCHREALTIME:-}"
    if [ "$left" -gt 0 ]; then
        flock -w "$left" 204 2>/dev/null || given=false
    else
        flock -n 204 2>/dev/null || given=false
    fi
    t1="${EPOCHREALTIME:-}"
    if [ -n "$t0" ] && [ -n "$t1" ]; then        # bash 5: to the microsecond
        took=$(( (10#${t1/[.,]/} - 10#${t0/[.,]/}) / 1000 ))
    else
        took=$(( (SECONDS - began) * 1000 ))
    fi
    _PB_COUNTERS_SPENT_MS=$(( _PB_COUNTERS_SPENT_MS + took ))
    if [ "$given" != true ]; then
        if [ "$left" -gt 0 ] && [ "$took" -lt $(( (left - 1) * 1000 + 500 )) ]; then
            # refused long before the limit: not a wait that ran out — this `flock`
            # cannot wait with a limit (or cannot lock here at all). No lock to take.
            _PB_COUNTERS_NOLOCK=true
            { exec 204>&-; } 2>/dev/null || true
            _PB_COUNTERS_FD=false
        else
            _counters_unsure "$1" "$wait"
        fi
    fi
    return 0
}

# _counters_dir FILE — the directory FILE lies in, without starting a program.
_counters_dir() {
    case "$1" in */*) printf '%s' "${1%/*}" ;; *) printf '.' ;; esac
}

# _counters_unsure FILE WAIT — there is a lock and it was not given: say so to
# the hook (the flag). What the stop hook must know — that a COUNT was not made
# — is said by the writer that did not make it (the mark). Never fails the
# caller.
_counters_unsure() {
    PLAYBOOK_COUNTERS_UNLOCKED=true
    PLAYBOOK_COUNTERS_WAITED="$2"
}

# _counters_mark FILE — leave the mark beside FILE. Never fails the caller.
_counters_mark() {
    # never through a link or into a FIFO (task 193, impl panel r1: `: >` truncated a link's target),
    # nor through a linked `sessions` (task 194)
    _pb_session_dir_ok "$(_counters_dir "$1")" || return 0
    _pb_sink_ok "" "$(_counters_dir "$1")/counts_unsure" || return 0
    : > "$(_counters_dir "$1")/counts_unsure" 2>/dev/null || true
}

counters_unlock() {
    [ "$_PB_COUNTERS_DEPTH" -gt 0 ] || return 0
    _PB_COUNTERS_DEPTH=$(( _PB_COUNTERS_DEPTH - 1 ))
    [ "$_PB_COUNTERS_DEPTH" -eq 0 ] || return 0
    if [ "$_PB_COUNTERS_FD" = true ]; then
        { exec 204>&-; } 2>/dev/null || true
        _PB_COUNTERS_FD=false
    fi
    return 0
}

# write_counter FILE KEY VALUE
# Set a key=value in the counter file. Creates file if missing, updates in-place if key exists.
# Uses grep-filter-append instead of sed to avoid delimiter collisions with gate text
# containing |, backticks, or other special characters.
# The copy is made and renamed under the counters lock (task 182): a caller that
# reads a value and writes one built on it takes the lock around BOTH.
write_counter() {
    local file="$1" key="$2" value="$3"
    local tmp="${file}.tmp.$$"
    # Not inside a linked session directory, not over a link or a non-regular counters
    # file (task 194): the temp file and its rename landed in the link's target. The count
    # is not made, and a count not made is marked (the stop hook also holds its releases
    # on a linked session directory, task 193).
    # Nor through a linked `sessions` (task 194, impl panel r1), nor over a temp path that is a
    # link or not a regular file (codex, reproduced: a link planted at `counters.tmp.<pid>` was
    # written through, then renamed onto the counters). A refused write is said (gemini).
    if ! _pb_session_dir_ok "$(_counters_dir "$file")" || ! _pb_sink_ok "" "$file" \
       || ! _pb_sink_ok "" "$tmp"; then
        case "$key" in tools|writes) _counters_mark "$file" ;; esac
        PLAYBOOK_WRITE_FAILED=true
        return 0
    fi
    counters_lock "$file"
    if [ "$PLAYBOOK_COUNTERS_UNLOCKED" = true ]; then
        # there is a lock and it was not given: nothing is written without it —
        # and a count that was not made is what the stop hook must know of
        case "$key" in tools|writes) _counters_mark "$file" ;; esac
        counters_unlock
        return 0
    fi
    if [ -f "$file" ]; then
        grep -v "^${key}=" "$file" > "$tmp" 2>/dev/null || true
    fi
    # Fail-OPEN and fail-LOUD: a write failure here (e.g. a lock or a full disk
    # on the atomic mv) must never kill the caller under `set -e` — that silently
    # freezes the gate_key and stops all gate logging (bug report #4). Record it
    # in PLAYBOOK_WRITE_FAILED so the hook can surface it, and always return 0.
    if ! { printf '%s=%s\n' "$key" "$value" >> "$tmp" 2>/dev/null && mv "$tmp" "$file" 2>/dev/null; }; then
        rm -f "$tmp" 2>/dev/null || true
        PLAYBOOK_WRITE_FAILED=true
    fi
    counters_unlock
    return 0
}

# write_counters FILE KEY VALUE [KEY VALUE ...]
# write_counter for several keys in ONE rewrite of the file (task 219: the state echo keeps its gate and its memory
# in eight fields — one grep + mv each would be eight per tool call). The same guards and the same lock as
# write_counter; a value is one line (it is cut at a newline). Keys are the hook's own names, never input.
write_counters() {
    local file="$1"; shift
    local tmp="${file}.tmp.$$" pat="" i
    local args=("$@")
    # no pair, nothing to write (impl round 1, agy: an empty pattern `^()=` stripped every line that starts with `=`);
    # an odd number of words is a caller's slip — nothing is written rather than a key with no value (task 220, agy)
    [ "${#args[@]}" -ge 2 ] || return 0
    if [ $(( ${#args[@]} % 2 )) -ne 0 ]; then
        PLAYBOOK_WRITE_FAILED=true      # said, not swallowed (task 220, agy)
        return 0
    fi
    if ! _pb_session_dir_ok "$(_counters_dir "$file")" || ! _pb_sink_ok "" "$file" \
       || ! _pb_sink_ok "" "$tmp"; then
        PLAYBOOK_WRITE_FAILED=true
        return 0
    fi
    counters_lock "$file"
    if [ "$PLAYBOOK_COUNTERS_UNLOCKED" = true ]; then
        counters_unlock
        return 0
    fi
    for ((i = 0; i < ${#args[@]}; i += 2)); do
        pat="${pat:+$pat|}${args[i]}"
    done
    : > "$tmp" 2>/dev/null || true
    if [ -f "$file" ]; then
        grep -Ev "^(${pat})=" "$file" > "$tmp" 2>/dev/null || true
    fi
    local ok=true
    for ((i = 0; i < ${#args[@]}; i += 2)); do
        printf '%s=%s\n' "${args[i]}" "${args[i+1]%%$'\n'*}" >> "$tmp" 2>/dev/null || ok=false
    done
    # Fail-open and fail-loud, as write_counter: a half-written temp file is never moved into place.
    if [ "$ok" != true ] || ! mv "$tmp" "$file" 2>/dev/null; then
        rm -f "$tmp" 2>/dev/null || true
        PLAYBOOK_WRITE_FAILED=true
    fi
    counters_unlock
    return 0
}

# reset_counters FILE
# Reset tools=0 and writes=0, preserving gate_* fields. Creates file if missing.
# Under the counters lock (task 182): the gate's lines it keeps are the ones the
# file holds when it is let in, and no tool call's count is renamed over the zeros.
reset_counters() {
    local file="$1"
    # A counters file that is a link or not a regular one, or a linked session directory,
    # is not ours (task 193, impl panel r1, fable and sonnet; measured: the reset wrote
    # `tools=0` through a link, a FIFO held the prompt): no reset — the counts of the turn
    # before stay, the side that holds a stop.
    _pb_session_dir_ok "$(_counters_dir "$file")" || return 0     # nor a linked `sessions` (task 194)
    _pb_sink_ok "" "$file" || return 0
    counters_lock "$file"
    if [ "$PLAYBOOK_COUNTERS_UNLOCKED" = true ]; then
        # not reset without the lock either: the counts stay those of the turn
        # before — the side that holds a stop — and the mark stays with them
        _counters_mark "$file"
        counters_unlock
        return 0
    fi
    if [ -f "$file" ]; then
        # Preserve gate_* lines, reset tools/writes
        local gate_lines
        gate_lines=$(grep '^gate_' "$file" 2>/dev/null || true)
        printf 'tools=0\nwrites=0\n' > "$file"
        if [ -n "$gate_lines" ]; then
            echo "$gate_lines" >> "$file"
        fi
    else
        printf 'tools=0\nwrites=0\n' > "$file"
    fi
    # counts made under the lock are sure again (on a host with no lock to take
    # there is no mark to remove)
    rm -f "$(_counters_dir "$file")/counts_unsure" 2>/dev/null || true
    counters_unlock
}

# Owner's Q1 (task 173): two one-line files beside a session's `counters` —
# `turn_end`, the tool count at the last stop the stop hook let through, and
# `notif_start`, the write count when a notification started a turn. Files of
# their own, NOT keys of `counters`: write_counter sets a key by copying the
# whole file and renaming the copy over it, so a hook that overlaps a tool
# call's hook can rename a stale copy over that call's increment (impl panel
# r1). Nothing that serves the rule rewrites `counters`. (Since task 182 the
# writers of `counters` take a lock; these two stay files of their own — that
# lock has a time limit, and the rule must not hang on it.)
# Each holds `<number> <nonce>`: the stop hook makes a new nonce for every stop
# it lets through, the notification's baseline carries the nonce of the mark it
# was taken from, and the baseline counts only beside the mark with that nonce
# (post-D6 run 1). So a baseline that reaches the disk LATE — after a user's
# prompt removed the mark, or after a later stop replaced it — is a dead letter,
# in whatever order the two hooks ran; nothing here depends on a lock.
#
# write_session_mark FILE VALUE — temp file + rename in the same directory.
# Never fails the caller; a mark that cannot be written is simply not there.
write_session_mark() {
    local file="$1" value="$2"
    # never renamed onto a link or a directory — `mv` moved the temp file INTO one (task 193, the
    # single judge after round 2, codex); such a target keeps what it is, and no mark is written.
    # Nor inside a session directory that is a link (task 194, measured: the stop hook's
    # `turn_end` landed in the link's target) — no mark, the stricter case for each reader.
    _pb_session_dir_ok "$(_counters_dir "$file")" || return 0
    _pb_sink_ok "" "$file" || return 0
    local tmp="${file}.tmp.$$"
    # nor over a temp path that is a link or not a regular file (impl panel r1, codex, reproduced)
    _pb_sink_ok "" "$tmp" || return 0
    if ! { printf '%s\n' "$value" > "$tmp" 2>/dev/null && mv "$tmp" "$file" 2>/dev/null; }; then
        rm -f "$tmp" 2>/dev/null || true
    fi
    return 0
}

# read_session_mark FILE — prints `<number> <nonce>` when the file's first line
# is exactly that: a plain number of at most 18 digits, one space-separated
# word of letters, digits and dashes (at most 64), nothing more. Nothing
# otherwise — missing, empty, signed, a third word, anything else: the bytes
# are `.agent/`-resident and untrusted, like the counters.
read_session_mark() {
    local number="" nonce="" rest=""
    [ -f "$1" ] || return 0
    read -r number nonce rest < "$1" 2>/dev/null || true
    case "$number" in ''|*[!0-9]*) return 0 ;; esac
    [ "${#number}" -le 18 ] || return 0
    case "$nonce" in ''|*[!A-Za-z0-9-]*) return 0 ;; esac
    [ "${#nonce}" -le 64 ] || return 0
    [ -z "$rest" ] || return 0
    printf '%s %s' "$number" "$nonce"
}

# The PROMPT GENERATION of a session (task 173, post-D6 run 2): one word in the
# file `prompt_gen`, renewed by every prompt of the user's. The stop hook puts
# the generation it saw when it STARTED at the head of the nonce it makes, and a
# mark counts — for the notification's hook and for the stop hook's bypass —
# only while that is still the session's generation. So a stop hook that a
# user's prompt overtook publishes a mark that starts nothing. `none` = the
# session has seen no prompt of the user's yet.
#
# read_prompt_generation SESSION_DIR — the word, or `none`.
read_prompt_generation() {
    local mark
    mark=$(read_session_mark "$1/prompt_gen")
    if [ -n "$mark" ]; then
        printf '%s' "${mark#* }"
    else
        printf 'none'
    fi
}

# prompt_generation_unreadable SESSION_DIR — true when `prompt_gen` is there but holds no
# generation (a directory, a link, a FIFO, garbage): `none` then is NOT the generation of a
# session that has seen no prompt of the user's, and it starts or releases no turn (task 193,
# the single judge after round 2, codex — a planted `prompt_gen` directory kept it at `none`).
prompt_generation_unreadable() {
    if [ -e "$1/prompt_gen" ] || [ -L "$1/prompt_gen" ]; then
        [ -z "$(read_session_mark "$1/prompt_gen")" ] && return 0
        [ -L "$1/prompt_gen" ] && return 0
    fi
    return 1
}

# new_prompt_generation SESSION_DIR — begin a new one: twelve characters of a
# fresh nonce, letters and digits only (its tail — the random end of either
# form new_session_nonce gives). The file's shape is a mark's, `0 <word>`.
new_prompt_generation() {
    local word
    word=$(new_session_nonce)
    word="${word//-/}"
    [ "${#word}" -le 12 ] || word="${word: -12}"
    write_session_mark "$1/prompt_gen" "0 $word"
    # A generation that could not be published is withdrawn (task 193, the single judge
    # after round 2, run 2, codex: with the rename failed the OLD one stayed readable, and
    # a stop hook's late mark of it was taken for current). With no `prompt_gen` the
    # generation reads `none`, which no mark of an older generation matches; an entry
    # that is not a plain file stays and reads as unreadable (prompt_generation_unreadable).
    if [ "$(read_session_mark "$1/prompt_gen")" != "0 $word" ]; then
        if [ -f "$1/prompt_gen" ] || [ -L "$1/prompt_gen" ]; then
            rm -f "$1/prompt_gen" 2>/dev/null || true
        fi
    fi
    return 0
}

# new_session_nonce — a value that does not repeat from one mark to the next:
# the kernel's random UUID (one builtin read, no process); where that file
# cannot be read, the pid, the clock in nanoseconds and three draws of $RANDOM.
new_session_nonce() {
    local nonce=""
    IFS= read -r nonce < /proc/sys/kernel/random/uuid 2>/dev/null || true
    case "$nonce" in
        ''|*[!A-Za-z0-9-]*) nonce="$$-$(date '+%s%N' 2>/dev/null || echo 0)-$RANDOM$RANDOM$RANDOM" ;;
    esac
    printf '%s' "$nonce"
}

# format_context TASK_NUM DONE TOTAL GATE_TEXT GATE_LINE REL_PATH
# Outputs the formatted context string for the hook
format_context() {
    local task_num="$1"
    local done="$2"
    local total="$3"
    local gate_text="$4"
    local gate_line="$5"
    local rel_path="$6"

    if [ -z "$gate_line" ]; then
        echo "# [${task_num}] — all gates done. Stay for follow-up. Auto-closes on task switch."
    else
        echo "# Working on task [${task_num}] gate (${done}/${total}) -> [ ] ${gate_text}
# Done? Check the box: ${rel_path}:${gate_line}"
    fi
}

# write_log_append INPUT_JSON PROJECT_DIR
# Appends the written file's content to the persistent write log.
# Called from PostToolUse for Write/Edit tools. Extracts file_path from
# the tool input JSON, reads the file, appends with timestamp.
# Log lives at ~/.local/share/playbook/<project-slug>/write_log
# — outside the project tree so agent can't accidentally delete it.
write_log_append() {
    local input="$1" project_dir="$2"
    # Project slug: absolute path with / replaced by -
    local slug
    slug=$(echo "$project_dir" | sed 's|^/||; s|/|-|g')
    local log_dir="$HOME/.local/share/playbook/$slug"
    # Bounded since task 141 (owner Q8): the size cap, the rotation, the one-time
    # parking of a pre-cap log and the `"write_log": false` switch live in
    # write_log.py. Best-effort: never fail the tool call.
    # The paths cross into Python: canonical form at that boundary (task 151;
    # _canonical_path is the identity on Linux)
    printf '%s' "$input" | python3 -E -s "$(_canonical_path "$(dirname "${BASH_SOURCE[0]}")")/write_log.py" \
        "$(_canonical_path "$log_dir")" "$(_canonical_path "$project_dir")" 2>/dev/null || true
}

# create_wrapper PROJECT_DIR WRAPPER_NAME
# Creates .claude/bin/<WRAPPER_NAME> as a wrapper that resolves the plugin's
# scripts/<WRAPPER_NAME> (manifest-first, scope-aware, version-deterministic —
# see the template below) and execs into it.
# - Skips if file exists without "# playbook-managed" marker (custom wrapper)
# - Skips if content is already current (idempotent — session-start-hook calls
#   this on EVERY SessionStart, including each headless judge session)
# - Overwrites if file has the marker but is stale (or is empty — self-healing)
# - Creates .claude/bin/ directory if needed
# The write is temp-file + atomic mv: concurrent sessions (e.g. a 6-judge
# panel, each firing SessionStart) previously raced the in-place `cat >` +
# `sed -i` and truncated wrappers to 0 bytes.
create_wrapper() {
    local project_dir="$1"
    local wrapper_name="$2"
    local wrapper_path="$project_dir/.claude/bin/$wrapper_name"

    # Skip custom wrappers (no playbook-managed marker)
    # Empty files are NOT custom — overwrite them (self-healing)
    if [ -f "$wrapper_path" ] && [ -s "$wrapper_path" ]; then
        if ! grep -q '# playbook-managed' "$wrapper_path" 2>/dev/null; then
            return 0
        fi
    fi

    local content
    # Delimiter MUST NOT be a prefix of any line in the body below. Inside
    # `$( )`, bash 5.2 and later end a heredoc at a body line that merely STARTS
    # with the delimiter and holds a `)`, so the old `WRAPPER` delimiter was cut
    # short by the body's own `WRAPPER_DIR="$(…)"` line — every regenerated
    # wrapper was silently truncated to six lines, and session-start-hook
    # regenerates all of them on every session. Field report: cristi
    # (ai-ring-vet, Git Bash MSYS 5.2.26), 2026-07-21; measured on Linux bash
    # 5.2.15-5.2.37 and 5.3.20 on 2026-10-09 (5.1.16 is not affected).
    # `tests/test_wrapper_template.py` holds this invariant for both this
    # delimiter and the nested PYRESOLVE one.
    # The resolver runs as `python3 -I -` (task 115, parked since 086): isolated mode keeps
    # the caller's cwd off sys.path, so a `glob.py` / `json.py` there cannot shadow the
    # stdlib and kill the resolver unseen (its stderr is discarded on purpose).
    content=$(cat <<'END_WRAPPER_TEMPLATE'
#!/bin/bash
# playbook-managed — do not edit; regenerated by playbook plugin
# Resolves the SAME plugin copy the harness hooks run: installed_plugins.json's
# installPath (what Claude Code points ${CLAUDE_PLUGIN_ROOT} at), scope-aware,
# newest version first. Falls back to a filesystem scan that prefers versioned
# cache dirs over marketplace clone tips, then to a plain deterministic find.
WRAPPER_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$WRAPPER_DIR/../.." 2>/dev/null && pwd -P)"
SCRIPT="$(python3 -I - "$PROJECT_ROOT" 2>/dev/null <<'PYRESOLVE'
@@PLAYBOOK_WRAPPER_RESOLVER@@
PYRESOLVE
)"
if [ -z "$SCRIPT" ]; then
    # Last resort (python3 unavailable/broken — the tasks CLI couldn't run
    # anyway): deterministic, readdir-order-independent search.
    SCRIPT="$(find ~/.claude/plugins -path "*/playbook/scripts/WRAPPER_NAME" -type f 2>/dev/null | sort | head -1)"
fi
if [ -z "$SCRIPT" ]; then
    echo "Error: playbook plugin not found (no usable copy in ~/.claude/plugins)." >&2
    echo "Install it from the marketplace you added it from:" >&2
    echo "  claude plugin marketplace list      # shows that marketplace's name" >&2
    echo "  claude plugin install playbook@<marketplace>" >&2
    exit 1
fi
exec "$SCRIPT" "$@"
END_WRAPPER_TEMPLATE
)
    # The resolver lives in ONE file, `wrapper_resolver.py` beside this lib, so
    # `tasks doctor` can run the very code every wrapper embeds (PLAN S3, task
    # 086). It is spliced in by prefix/suffix removal — `${var/pat/repl}` would
    # rewrite `&` in the replacement under bash 5.2's patsub_replacement. Read
    # only when the template carries the placeholder. Missing file: warn, write
    # NOTHING, return 0 — never a broken wrapper, never an aborted `set -e` hook.
    if [[ "$content" == *"@@PLAYBOOK_WRAPPER_RESOLVER@@"* ]]; then
        local resolver_file resolver
        resolver_file="$(dirname "${BASH_SOURCE[0]}")/wrapper_resolver.py"
        if [ ! -f "$resolver_file" ] || ! resolver="$(cat "$resolver_file")"; then
            echo "playbook: $resolver_file is missing — .claude/bin/$wrapper_name left unchanged" >&2
            return 0
        fi
        content="${content%%@@PLAYBOOK_WRAPPER_RESOLVER@@*}${resolver}${content#*@@PLAYBOOK_WRAPPER_RESOLVER@@}"
    fi
    content="${content//WRAPPER_NAME/$wrapper_name}"

    # Already current → nothing to write (also avoids needless mtime churn)
    if [ -f "$wrapper_path" ] && [ "$(cat "$wrapper_path" 2>/dev/null)" = "$content" ] \
        && [ -x "$wrapper_path" ]; then
        return 0
    fi

    mkdir -p "$project_dir/.claude/bin"

    # Write to a per-process temp file, then atomically rename into place. The
    # real wrapper path is only ever touched by the final `mv` — it never passes
    # through a 0-byte O_TRUNC window, so a kill (e.g. a panel-review judge
    # subprocess timing out mid-SessionStart) can't leave it empty, and two
    # concurrent writers can't interleave a truncate with a write.
    local tmp_path="$wrapper_path.tmp.$$"
    printf '%s\n' "$content" > "$tmp_path" || { rm -f "$tmp_path"; return 1; }
    chmod +x "$tmp_path"
    mv -f "$tmp_path" "$wrapper_path"
}

# Known playbook-managed wrapper names. Kept in sync with the create_wrapper
# calls in `init` and `session-start-hook`.
PLAYBOOK_WRAPPER_NAMES="tasks sandbox monitor playbook-codex playbook-agy playbook-grok playbook-pi"

# heal_empty_wrappers PROJECT_DIR
# Repair any playbook wrapper that a pre-fix kill left 0 bytes. Cheap enough to
# call on every hook: for each KNOWN name that exists AND is empty, regenerate.
# - Allowlist only, so a legitimately-empty *custom* file in .claude/bin is never
#   clobbered (create_wrapper treats empty as non-custom and would overwrite it).
# - Only heals files that already exist — never provisions wrappers a project lacks.
# - Fully failure-tolerant: intended to be called as `heal_empty_wrappers … || true`
#   from hooks that run under `set -e` and may be in a read-only sandbox.
heal_empty_wrappers() {
    local project_dir="$1"
    [ -n "$project_dir" ] || return 0
    local bin_dir="$project_dir/.claude/bin"
    [ -d "$bin_dir" ] || return 0
    local name
    for name in $PLAYBOOK_WRAPPER_NAMES; do
        if [ -e "$bin_dir/$name" ] && [ ! -s "$bin_dir/$name" ]; then
            create_wrapper "$project_dir" "$name" 2>/dev/null || true
        fi
    done
    return 0
}

# _json_str VALUE
# Minimal JSON string-body escaper in pure bash (no interpreter startup): escape
# backslash and double-quote, and neutralize the control bytes that would break a
# single-line record — tab -> \t, CR dropped, newline -> space. `hook`/`decision`
# values are our own literals and need none of this; paths/commands/reasons do.
# Bash 3.2 safe (parameter expansion + ANSI-C quoting only). Exotic control bytes
# (other than \t\r\n) are left as-is — vanishingly rare in a tool path/command,
# and the journal is best-effort; this never affects any decision.
_json_str() {
    local s="$1"
    s="${s//\\/\\\\}"          # backslash FIRST
    s="${s//\"/\\\"}"          # double quote
    s="${s//$'\t'/\\t}"        # tab -> \t
    s="${s//$'\r'/}"           # CR dropped
    s="${s//$'\n'/ }"          # newline -> space (keep the record one line)
    printf '%s' "$s"
}

# _pb_session_dir_ok DIR — true when the session directory DIR and its parent, `sessions`, are not symbolic
# links (task 194, impl panel r1 — fable, opus, codex reproduced it: with `.agent/sessions` a link, the leaf
# check saw a real `sessions/<id>` and every hook wrote into the link's target; `.gitignore`'s
# `.agent/sessions/` matches directories only, so the link is committable). For what WRITES session state;
# links above `sessions` (`.agent`, a lane) hold the whole state, the CLI's too — not asked here.
_pb_session_dir_ok() {
    [ -L "$1" ] && return 1
    [ -L "${1%/*}" ] && return 1
    return 0
}

# _pb_inside_project ROOT PATH — true when PATH is ROOT/… and no component of it below ROOT is a symbolic
# link, so PATH lies physically inside the project. For what DELETES (task 194, impl panel r1 — grave, measured:
# with `.agent/sessions` a link, the session sweep removed every directory of the link's target; a cloned
# repository can ship that link). Above ROOT nothing is asked: a project reached through a link is the user's.
_pb_inside_project() {
    local root="$1" rest cur part
    case "$2" in "$root"/?*) rest="${2#"$root"/}" ;; *) return 1 ;; esac
    cur="$root"
    while [ -n "$rest" ]; do
        part="${rest%%/*}"
        cur="$cur/$part"
        [ -L "$cur" ] && return 1
        [ "$rest" = "$part" ] && break
        rest="${rest#*/}"
    done
    return 0
}

# _pb_cwd_is ROOT PATH — true when the directory this shell stands in is PATH as it lies physically
# inside ROOT (ROOT's physical path + PATH's part below ROOT). Asked AFTER a `cd` into PATH: from
# then on the shell holds that directory, and what it deletes by RELATIVE names stays there — a
# swap of PATH, or of a directory above it, after the check changes nothing (task 194, impl panel
# r2, codex — reproduced: a sweep that checked by path and deleted by path followed a swap).
_pb_cwd_is() {
    local root_p
    case "$2" in "$1"/?*) ;; *) return 1 ;; esac
    root_p=$(cd "$1" 2>/dev/null && pwd -P) || return 1
    [ "$(pwd -P)" = "$root_p/${2#"$1"/}" ]
}

# _pb_sink_ok DIR FILE — true when DIR is not a symbolic link and FILE is absent or a regular file that is not a
# link: a log written there stays inside the lane and never blocks on a FIFO. (Residual: a swap between this check
# and the write — the window pb_journal.py states too.)
_pb_sink_ok() {
    [ -L "$1" ] && return 1
    [ -L "$2" ] && return 1
    if [ -e "$2" ] && [ ! -f "$2" ]; then return 1; fi
    return 0
}

# journal_enforcement AGENT_DIR HOOK DECISION REASON [SESSION_ID] [TOOL] [PATH] [COMMAND]
# Best-effort append-only enforcement-journal record. HARD CONTRACT (mirrors
# pb_journal.py): a journal failure must NEVER change a decision, so every step
# swallows its error and the function always returns 0. Writes ONLY when the
# resolved lane dir already exists (playbook-managed) — the `journal/` subdir is
# created inside that existing lane, `.agent` itself is never minted here. One
# `printf >>` append per record; single-line records under PIPE_BUF are atomic
# against concurrent appenders (see pb_journal.py docstring for the honest bound).
# No fsync, no locking, no interpreter startup.
journal_enforcement() {
    local agent_dir="$1" hook="$2" decision="$3" reason="$4"
    local session_id="${5:-}" tool="${6:-}" path="${7:-}" command="${8:-}"
    [ -n "$agent_dir" ] && [ -d "$agent_dir" ] || return 0
    local jdir="$agent_dir/journal"
    [ -d "$jdir" ] || mkdir -p "$jdir" 2>/dev/null || return 0
    # Hostile-tree safe like pb_journal.py (task 192, impl panel r2, measured): a FIFO at the journal path stalled
    # the ENFORCING gate past its timeout (the host then lets the call through), and a link wrote the record into
    # whatever it points at — a cloned repository can ship either under `.agent/`. Such a sink is skipped.
    _pb_sink_ok "$jdir" "$jdir/enforcement.jsonl" || return 0
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null) || return 0
    local line
    line="{\"ts\":\"$ts\",\"session_id\":\"$(_json_str "$session_id")\""
    line="$line,\"hook\":\"$hook\",\"decision\":\"$decision\""
    line="$line,\"reason\":\"$(_json_str "$reason")\""
    [ -n "$tool" ] && line="$line,\"tool\":\"$(_json_str "$tool")\""
    [ -n "$path" ] && line="$line,\"path\":\"$(_json_str "$path")\""
    if [ -n "$command" ]; then
        # command head only: first line, capped at 200 chars — never full content.
        local head="${command%%$'\n'*}"
        line="$line,\"command\":\"$(_json_str "${head:0:200}")\""
    fi
    line="$line}"
    printf '%s\n' "$line" >> "$jdir/enforcement.jsonl" 2>/dev/null || return 0
    return 0
}

# --- The shell log (PHASE-B item 2, task 221) ------------------------------------------------------------------------
# shell_log_append AGENT_DIR COMMAND — the agent's shell command as ONE line of AGENT_DIR/bash_history. The owner,
# 2026-10-16: the log keeps only the agent's own commands ("Doar comenzile mele") and the hook that receives the exact
# command writes it ("Hook-ul scrie"). Before, a logger sourced through BASH_ENV into every non-interactive bash wrote
# every simple command of every shell — the hooks', the `tasks` launcher's, Claude Code's snapshot: on 2026-10-15,
# 45,083 lines for 1,666 commands of the agent's.
#
# The line: `YYYY-mm-dd HH:MM:SS | AGENT | <command>`, local time from bash itself (no program per line); `\` is written
# `\\`, then a newline `\n` and a CR `\r`, so the line is one physical line and reads back exactly (trailing newlines
# are dropped; a command of white space alone is no line). No reader of the plugin parses it: the activations come
# from the CLI's own journal records.
#
# Written under `flock` on the lane directory (descriptor 206, opened read-only), waiting at most
# PLAYBOOK_SHELL_LOG_LOCK_WAIT seconds (1 or 2; default 1) — the lock is held for one append or one rename. Holding
# it, the writer first appends the spool files waiting in the lane (name order) and removes each, then renames a
# history past 50 MB to `bash_history.archived-<YYYYmmdd-HHMMSS>-<pid>` (never over an existing name; nothing is
# carried), then appends its line. Not holding it (no `flock`, or the wait ran out) it writes its line to a spool file
# of its own — made whole under another name, then renamed in — and never rotates: no line is lost or torn by writers
# side by side or a stuck holder (plan panel 2026-10-16: an unlocked append tore 64 KiB lines, measured).
#
# Refused: a lane that is a symbolic link; a history or spool that is a link or not a regular file (a FIFO would
# hang the hook). Always returns 0 and prints nothing: callers run under `set -e`, and a log never changes a decision.
_PB_SHELL_LOG_LIMIT=52428800
_PB_SHELL_LOG_LINE_MAX=4194304      # a command longer than this many bytes is cut there, and the line says so
_PB_SHELL_LOG_SPOOL_MAX=4198400     # a spool holds one line (the bound + its stamp and cut note): bigger is not ours

# shell_log_append's own steps, on its variables `cmd` and `cut` (bash's dynamic scope; LC_ALL=C from it).
_shell_log_cut() {      # keep the first $1 bytes of cmd, on a whole character; add what goes to `cut`
    local keep="$1" k=0 total="${#cmd}"
    while [ "$k" -lt 3 ] && [ "$keep" -gt 0 ] && [[ "${cmd:keep:1}" == [$'\x80'-$'\xbf'] ]]; do
        keep=$(( keep - 1 )); k=$(( k + 1 ))
    done
    cut=$(( ${cut:-0} + total - keep ))
    cmd="${cmd:0:keep}"
}
_shell_log_escape_short() {     # `\` first, then a newline and a CR: an exact round trip (bash, ≤ 64 KiB here)
    cmd="${cmd//\\/\\\\}"
    cmd="${cmd//$'\n'/\\n}"
    cmd="${cmd//$'\r'/\\r}"
}

shell_log_append() {
    local LC_ALL=C      # lengths and cuts in bytes (impl panel r1: a spool past the takers' bound was never merged)
    local lane="$1" cmd="$2" hist now line wait opened=false got=false nolock=false t0="" t1="" rest i cut=""
    [ -n "$lane" ] && [ -d "$lane" ] && [ ! -L "$lane" ] || return 0
    # trailing newlines and CRs (a CRLF ending, impl panel r1) are dropped in ONE match and one cut: one `${cmd%…}`
    # per newline was quadratic (impl panel r2: 30,000 took 7.8 s), and so was a per-byte look from the end — bash's
    # `${cmd:i:1}` walks to i (the post-panel judge: 60,000 took 6.1 s); the regex is linear (200,000: 3 ms)
    if [[ "$cmd" =~ [$'\n\r']+$ ]]; then
        cmd="${cmd:0:${#cmd}-${#BASH_REMATCH[0]}}"
    fi
    [[ "$cmd" =~ [^[:space:]] ]] || return 0
    # A command past half the line bound is cut BEFORE it is escaped (escaped it is at most twice as long, so the
    # cut never splits an escape), on a whole character: the cut backs over at most 3 UTF-8 continuation bytes
    # (impl panel r2: the 4 MiB cut of the escaped text could split either). The line says how much was cut.
    if [ "${#cmd}" -gt $(( _PB_SHELL_LOG_LINE_MAX / 2 )) ]; then
        _shell_log_cut $(( _PB_SHELL_LOG_LINE_MAX / 2 ))
    fi
    if [ "${#cmd}" -gt 65536 ]; then
        # bash's `${x//a/b}` is quadratic in the length of x (measured, task 221: 1 MB 0.18 s, 5 MB 4.6 s, three of
        # them per line — past the hook's 5 s), so a long command is escaped by sed, in one linear pass (`-z`: the
        # whole command is one record — no NUL can be in it — so each substitution sees all of it). Where sed cannot
        # (impl panel r2: a sed without -z), the first 64 KiB are escaped here and the line says the rest was cut.
        if rest=$(printf '%s' "$cmd" | command sed -z -e 's/\\/\\\\/g' -e 's/\r/\\r/g' -e 's/\n/\\n/g' \
                  2>/dev/null) && [ -n "$rest" ]; then
            cmd="$rest"
        else
            _shell_log_cut 65536
            _shell_log_escape_short
        fi
    else
        _shell_log_escape_short
    fi
    [ -z "$cut" ] || cmd="$cmd …[cut: $cut more bytes]"
    printf -v now '%(%Y-%m-%d %H:%M:%S)T' -1 2>/dev/null || return 0
    line="$now | AGENT | $cmd"
    hist="$lane/bash_history"
    wait="${PLAYBOOK_SHELL_LOG_LOCK_WAIT:-}"
    case "$wait" in 1|2) ;; *) wait=1 ;; esac
    if command -v flock >/dev/null 2>&1 && { exec 206<"$lane"; } 2>/dev/null; then
        opened=true
        t0="${EPOCHREALTIME:-}"
        if flock -w "$wait" 206 2>/dev/null; then
            got=true
        else
            t1="${EPOCHREALTIME:-}"
            # refused long before the limit: this `flock` cannot wait with a limit (busybox has no -w) — no lock to
            # take, as `counters_lock` reads it; a wait that ran out is a held lock, and the line is spooled
            if [ -n "$t0" ] && [ -n "$t1" ] \
               && [ $(( (10#${t1/[.,]/} - 10#${t0/[.,]/}) / 1000 )) -lt $(( wait * 1000 - 500 )) ]; then
                nolock=true
            fi
        fi
    else
        nolock=true
    fi
    if [ "$got" = true ]; then
        _shell_log_take_spools "$lane" "$hist"
        _shell_log_rotate "$lane" "$hist"
        if _pb_sink_ok "$lane" "$hist"; then
            { printf '%s\n' "$line" >> "$hist"; } 2>/dev/null || true
        fi
    elif [ "$nolock" = true ]; then
        # no lock on this host at all (impl panel r1: every line became a spool nothing merged): appended directly,
        # never rotated — two long lines written side by side can tear here, the bound of a host without a working
        # `flock`
        if _pb_sink_ok "$lane" "$hist"; then
            { printf '%s\n' "$line" >> "$hist"; } 2>/dev/null || true
        fi
    else
        _shell_log_spool "$lane" "$line"
    fi
    if [ "$opened" = true ]; then
        { exec 206<&-; } 2>/dev/null || true
    fi
    return 0
}

# Under the lock: every spool file of the lane, in name order, appended whole and removed. A link of that name is
# removed unread (none is ours); anything else that is not a small regular file is left. A writer's temporary
# (`bash_history.spooling-…`) older than ten minutes was left by a writer killed while writing it, and is removed
# (impl panel r1). A crash between appending a spool and removing it appends that line twice on the next take.
_shell_log_take_spools() {
    local lane="$1" hist="$2" f size
    _pb_sink_ok "$lane" "$hist" || return 0
    for f in "$lane"/bash_history.spooling-*; do
        [ -e "$f" ] || [ -L "$f" ] || continue
        command find "$lane" -maxdepth 1 -name 'bash_history.spooling-*' -mmin +10 -delete 2>/dev/null || true
        break
    done
    for f in "$lane"/bash_history.spool-*; do
        if [ -L "$f" ]; then
            rm -f -- "$f" 2>/dev/null || true
            continue
        fi
        [ -f "$f" ] || continue
        size=$(stat -c %s -- "$f" 2>/dev/null) || continue
        case "$size" in ''|*[!0-9]*) continue ;; esac
        [ "$size" -le "$_PB_SHELL_LOG_SPOOL_MAX" ] || continue
        { cat -- "$f" >> "$hist"; } 2>/dev/null && rm -f -- "$f" 2>/dev/null
    done
    return 0
}

# Under the lock: a history past the limit is renamed to an archive name that does not exist yet (`mv -n`); a kill
# leaves it under the old name or the new one. Nothing is carried into the new file.
_shell_log_rotate() {
    local lane="$1" hist="$2" size stamp arch
    [ -f "$hist" ] && [ ! -L "$hist" ] || return 0
    size=$(stat -c %s -- "$hist" 2>/dev/null) || return 0
    case "$size" in ''|*[!0-9]*) return 0 ;; esac
    [ "$size" -gt "$_PB_SHELL_LOG_LIMIT" ] || return 0
    printf -v stamp '%(%Y%m%d-%H%M%S)T' -1 2>/dev/null || return 0
    arch="$lane/bash_history.archived-$stamp-$$"
    if [ -e "$arch" ] || [ -L "$arch" ]; then return 0; fi
    mv -n -- "$hist" "$arch" 2>/dev/null || true
    return 0
}

# Without the lock: the line goes to a spool file of this writer's own, written whole under a name the takers do not
# match (`bash_history.spooling-…`, noclobber: never through anything already there), then renamed in.
_shell_log_spool() {
    local lane="$1" line="$2" stamp tmp name
    printf -v stamp '%(%Y%m%d-%H%M%S)T' -1 2>/dev/null || return 0
    name="bash_history.spool-$stamp-$$-$RANDOM$RANDOM"
    tmp="$lane/bash_history.spooling-${name#bash_history.spool-}"
    if ! ( set -o noclobber; printf '%s\n' "$line" > "$tmp" ) 2>/dev/null; then
        [ -L "$tmp" ] || [ ! -f "$tmp" ] || rm -f -- "$tmp" 2>/dev/null
        return 0
    fi
    mv -n -- "$tmp" "$lane/$name" 2>/dev/null || rm -f -- "$tmp" 2>/dev/null
    return 0
}

# ---------------------------------------------------------------------------------------------------------------------
# The hook journal (task 222, PHASE-B item 2; the owner, 2026-10-16 10:26: "Istoric curat + jurnal de hook-uri"). Every
# run of a playbook hook appends ONE JSON line to `<lane>/journal/hooks.jsonl`: which hook, when (UTC), the event, the
# tool call (its name and the host's tool-call id), playbook's session id and the host's, a subagent's id, what the hook
# decided and a short why, how long it ran, its exit status and, on an error, the hook's own failing line. A file of its
# own: `enforcement.jsonl` keeps the decisions and the CLI's task transitions the task windows read (task 221).
#
# A hook's first line is `_PB_HJ_T0=$EPOCHREALTIME`; once the lib is sourced it calls `pb_hook_begin <name>`, and its
# own exit actions go through `pb_on_exit` (a second `trap … EXIT` would replace the runner). The runner keeps the hook's
# exit status exactly: it turns `set +e`, every step is `|| true`, each action sees the hook's status as `$?` (the stop
# hook writes its turn-end mark only after an allowed stop — plan panel 2026-10-16, all five judges) and it ends with
# `exit` of that status. A journal failure never changes a decision. A hook killed by the host writes nothing.
_PB_HJ_LIMIT=20971520           # past 20 MB the file is closed: renamed, compressed, kept (the owner, 2026-10-16 18:04)
_PB_HJ_LINE_MAX=1024            # one append of at most this many bytes, the newline included (PIPE_BUF is 4096)
_PB_HJ_HOOK=""
_PB_HJ_ACTIONS=()
_PB_HJ_ERR_CMD=""
_PB_HJ_ERR_LINE=""
PB_HJ_DECISION=""          # these, and the frame's four below, start empty in every hook: never inherited
PB_HJ_DETAIL=""
PB_HJ_ERROR=""
PB_HJ_FRAME=""
PB_HJ_EVENT=""
PB_HJ_TOOL_USE_ID=""
PB_HJ_AGENT_ID=""
PB_HJ_HOST_SESSION=""

# pb_hook_begin NAME — from here on, this run writes its line when the hook exits. The runner is installed in every
# case (it runs the hook's own exit actions); no LINE is written for a review's session (`judge`, `tail-cert`: a review
# must not write the project — impl panel r1, opus) nor for a nested run (`PLAYBOOK_HOOK_NESTED=1`: codex's Stop runs
# the stop hook as a subprocess, and its own line already says the run — impl panel r1, sonnet).
pb_hook_begin() {
    _PB_HJ_HOOK="$1"
    [ "${PLAYBOOK_HOOK_NESTED:-}" != 1 ] || _PB_HJ_HOOK=""
    case "${PLAYBOOK_SESSION_ID:-}" in judge|tail-cert) _PB_HJ_HOOK="" ;; esac
    trap '_PB_HJ_ERR_CMD=$BASH_COMMAND; _PB_HJ_ERR_LINE=$LINENO' ERR
    trap '_pb_hj_exit' EXIT
}

# pb_on_exit 'COMMAND' — run COMMAND when the hook exits, before its line is written, with `$?` the hook's status.
pb_on_exit() {
    _PB_HJ_ACTIONS+=("$1")
}

_pb_hj_status() { return "$1"; }

_pb_hj_exit() {
    _PB_HJ_RC=$?
    local _pb_hj_failing="$BASH_COMMAND" _pb_hj_action
    set +e
    trap - ERR
    for _pb_hj_action in "${_PB_HJ_ACTIONS[@]}"; do
        _pb_hj_status "$_PB_HJ_RC"
        eval "$_pb_hj_action" || true
    done
    hook_journal_append "$_PB_HJ_RC" "$_pb_hj_failing" 2>/dev/null || true
    exit "$_PB_HJ_RC"
}

# hook_journal_append RC FAILING-COMMAND — the run's line, from the hook's variables: where the hook read the
# normalizer's frame (PB_HJ_FRAME=1), its PB_HJ_EVENT, PB_HJ_TOOL_USE_ID, PB_HJ_HOST_SESSION, PB_HJ_AGENT_ID and
# TOOL_NAME as they are, empty ones too (a key nested in the tool's input must not stand in for an absent one); else
# by pattern from $INPUT. Always returns 0 and prints nothing.
hook_journal_append() {
    local LC_ALL=C      # lengths and cuts in bytes
    local rc="$1" failing="${2:-}" lane="" jdir hfile line ts t0="${_PB_HJ_T0:-}" t1 ms=null v
    local hook event tool tuid sid hsid agent decision detail error
    [ -n "$_PB_HJ_HOOK" ] || return 0
    _pb_hj_lane
    [ -n "$lane" ] || return 0
    jdir="$lane/journal"
    hfile="$jdir/hooks.jsonl"
    [ -L "$jdir" ] && return 0
    if [ ! -d "$jdir" ]; then
        [ -e "$jdir" ] && return 0
        mkdir "$jdir" 2>/dev/null || [ -d "$jdir" ] || return 0
    fi
    _pb_sink_ok "$jdir" "$hfile" || return 0
    _pb_hj_rotate "$jdir" "$hfile"      # before the duration is taken: the journal's own work counts (impl panel r1, codex)
    t1="${EPOCHREALTIME:-}"
    case "$rc" in ''|*[!0-9]*) rc=255 ;; esac
    TZ=UTC0 printf -v ts '%(%Y-%m-%dT%H:%M:%SZ)T' -1 2>/dev/null || return 0
    if [[ "$t0" =~ ^[0-9]{1,12}[.,][0-9]{6}$ ]] && [[ "$t1" =~ ^[0-9]{1,12}[.,][0-9]{6}$ ]]; then
        ms=$(( (10#${t1/[.,]/} - 10#${t0/[.,]/}) / 1000 ))
        [ "$ms" -ge 0 ] || ms=null
    fi
    _pb_hj_id "$_PB_HJ_HOOK" 32; hook="$v"
    local frame="${PB_HJ_FRAME:-}"
    if [ "$frame" = 1 ] || [ -n "${PB_HJ_EVENT:-}" ]; then v="${PB_HJ_EVENT:-}"; else _pb_hj_first 'hook_event_name|hookEventName'; fi
    _pb_hj_id "$v" 32; event="$v"
    if [ "$frame" = 1 ] || [ -n "${TOOL_NAME:-}" ]; then v="${TOOL_NAME:-}"; else _pb_hj_first 'tool_name|toolName'; fi
    _pb_hj_id "$v" 64; tool="$v"
    if [ "$frame" = 1 ] || [ -n "${PB_HJ_TOOL_USE_ID:-}" ]; then
        v="${PB_HJ_TOOL_USE_ID:-}"
    elif [[ "$event" == *Tool* ]]; then
        _pb_hj_tool_use_id
    else
        v=""        # a prompt, a stop, a session's start or end carries no tool call (its search costs ~0.1 ms)
    fi
    _pb_hj_id "$v" 64; tuid="$v"
    _pb_hj_id "${SESSION_ID:-}" 64; sid="$v"
    if [ "$frame" = 1 ] || [ -n "${PB_HJ_HOST_SESSION:-}" ]; then v="${PB_HJ_HOST_SESSION:-}"; else _pb_hj_first 'session_id|sessionId'; fi
    _pb_hj_id "$v" 64; hsid="$v"
    if [ "$frame" = 1 ] || [ -n "${PB_HJ_AGENT_ID:-}" ]; then v="${PB_HJ_AGENT_ID:-}"; else _pb_hj_first 'agent_id|agentId'; fi
    _pb_hj_id "$v" 64; agent="$v"
    decision="${PB_HJ_DECISION:-}"
    if [ -z "$decision" ]; then
        case "$rc" in
            2) decision=block ;;
            0) if [ "$event" = PreToolUse ]; then decision=allow; else decision=ok; fi ;;
            *) decision=error ;;
        esac
    fi
    _pb_hj_id "$decision" 16; decision="$v"
    _pb_hj_text "${PB_HJ_DETAIL:-}" 128; detail="$v"
    v="${PB_HJ_ERROR:-}"
    if [ -z "$v" ] && [ "$rc" != 0 ] && [ "$rc" != 2 ]; then
        # the ERR trap's line, where it saw this very command (a failure in a function reaches the trap as the call)
        if [ -n "$_PB_HJ_ERR_CMD" ] && [ "$_PB_HJ_ERR_CMD" = "$failing" ]; then
            v="line $_PB_HJ_ERR_LINE: $failing"
        else
            v="$failing"
        fi
    fi
    _pb_hj_text "$v" 240; error="$v"
    line="{\"ts\":\"$ts\",\"hook\":\"$hook\",\"event\":\"$event\",\"tool\":\"$tool\",\"tool_use_id\":\"$tuid\""
    line="$line,\"session_id\":\"$sid\",\"host_session_id\":\"$hsid\",\"agent_id\":\"$agent\""
    line="$line,\"decision\":\"$decision\",\"detail\":\"$detail\",\"ms\":$ms,\"exit\":$rc,\"error\":\"$error\"}"
    if [ "${#line}" -ge "$_PB_HJ_LINE_MAX" ]; then
        line="${line%%,\"detail\":*},\"detail\":\"\",\"ms\":$ms,\"exit\":$rc,\"error\":\"\"}"
        [ "${#line}" -lt "$_PB_HJ_LINE_MAX" ] || return 0
    fi
    _pb_sink_ok "$jdir" "$hfile" || return 0
    { printf '%s\n' "$line" >> "$hfile"; } 2>/dev/null || true
    return 0
}

# Sets the caller's `lane`: the hook's own (AGENT_DIR); where the hook found a project but set no lane, resolved in a
# subshell (resolve_agent_dir exits on a bad marker — from the trap that would replace the hook's status: plan panel,
# sonnet); where the hook looked and found none (PROJECT_DIR set, empty), nothing — no second walk; where it never
# looked, the project above the working directory. Nothing in the fresh-clone shape, with an invalid marker, or for a
# lane that is missing, a link or not writable.
_pb_hj_lane() {
    local p=""
    lane=""
    if [ -n "${AGENT_DIR:-}" ]; then
        lane="$AGENT_DIR"
        p="${PROJECT_DIR:-}"
        if [ -z "$p" ]; then
            case "$lane" in */.agent) p="${lane%/.agent}" ;; */.agent/*) p="${lane%/.agent/*}" ;; esac
        fi
    elif [ "${PROJECT_DIR+set}" = set ]; then
        [ -n "$PROJECT_DIR" ] || return 0
        p="$PROJECT_DIR"
        lane=$(resolve_agent_dir "$p" 2>/dev/null) || lane=""
    else
        p=$(find_project_root 2>/dev/null) || p=""
        [ -n "$p" ] || return 0
        lane=$(resolve_agent_dir "$p" 2>/dev/null) || lane=""
    fi
    if [ "${MARKER_INVALID:-false}" = true ] || { [ -n "$p" ] && _pb_hj_fresh_clone "$p"; }; then
        lane=""
        return 0
    fi
    [ -n "$lane" ] && [ -d "$lane" ] && [ ! -L "$lane" ] && [ -w "$lane" ] || lane=""
    return 0
}

# True in the fresh-clone shape (lanes_without_marker's, without its fork): lanes, no marker, no root `tasks/`.
_pb_hj_fresh_clone() {
    local sub
    [ -f "$1/.agent/current_user" ] && return 1
    [ -d "$1/.agent/tasks" ] && return 1
    [ -d "$1/.agent" ] || return 1
    for sub in "$1/.agent"/*/; do
        [ -d "${sub}tasks" ] && return 0
    done
    return 1
}

# Sets `v` to the value of the payload's first key named by the alternation $1, searched only in the payload's HEAD:
# before its first `"tool_input"` (grok: `"toolInput"`) and within 4 KiB. The event, the session and the agent come
# before any tool input in every payload captured (task 222); a key of the same name nested in a tool's input or
# response is not the call's (impl panel r1, codex — the Agent tool's response carries `agentId`), and an absent key
# costs a 4 KiB scan, not the whole payload (sonnet).
_pb_hj_first() {
    local re="\"($1)\"[[:space:]]*:[[:space:]]*\"([^\"]*)\"" h="${INPUT:0:4096}"
    h="${h%%\"tool_input\"*}"
    h="${h%%\"toolInput\"*}"
    v=""
    if [[ "$h" =~ $re ]]; then v="${BASH_REMATCH[2]}"; fi
    return 0
}

# Sets `v` to the payload's tool-call id where no frame gave it. Claude's `tool_use_id` comes after `tool_input` and
# `tool_response`, followed only by `error` (a string), `is_interrupt` and `duration_ms` (measured, task 222): the LAST
# match in the payload's last 4 KiB is its own key, never one nested before it; past a long error, the first match.
_pb_hj_tool_use_id() {
    local re='.*"(tool_use_id|toolUseId)"[[:space:]]*:[[:space:]]*"([^"]*)"' t="${INPUT:-}"
    v=""
    [ "${#t}" -le 4096 ] || t="${t:${#t}-4096}"
    if [[ "$t" =~ $re ]]; then
        v="${BASH_REMATCH[2]}"
    else
        re='"(tool_use_id|toolUseId)"[[:space:]]*:[[:space:]]*"([^"]*)"'
        if [[ "${INPUT:-}" =~ $re ]]; then v="${BASH_REMATCH[2]}"; fi
    fi
    return 0
}

# Sets `v` to $1 with only the characters an id or a name is made of, at most $2 bytes (nothing to escape).
_pb_hj_id() {
    v="${1:0:$(( $2 * 2 ))}"
    v="${v//[^A-Za-z0-9._:@\/+=-]/}"
    v="${v:0:$2}"
    return 0
}

# Sets `v` to $1 JSON-escaped, control characters turned to spaces, at most $2 bytes — cut on a whole UTF-8 character
# and a whole escape (the line's bound holds for the encoded text: plan panel, grok).
_pb_hj_text() {
    local keep k=0
    v="${1:0:$(( $2 * 2 ))}"
    v="${v//[$'\001'-$'\037'$'\177']/ }"
    v="${v//\\/\\\\}"
    v="${v//\"/\\\"}"
    if [ "${#v}" -gt "$2" ]; then
        keep="$2"
        while [ "$k" -lt 3 ] && [ "$keep" -gt 0 ] && [[ "${v:keep:1}" == [$'\x80'-$'\xbf'] ]]; do
            keep=$(( keep - 1 )); k=$(( k + 1 ))
        done
        v="${v:0:keep}"
        if [[ "$v" =~ \\+$ ]] && (( ${#BASH_REMATCH[0]} % 2 )); then v="${v%\\}"; fi
    fi
    return 0
}

# About one run in 32 (PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK, 1-1000) looks at the journal directory. Under a lock on it taken
# without waiting (busy: nothing this run): past the limit, after a second look, the file is renamed to an archive name
# that does not exist yet; and a closed file is compressed (`gzip`) only once it is idle — its mtime over 10 minutes
# old — because a hook that opened the file before the rename can still append to it, and a line written after gzip
# read it would go with the uncompressed copy (impl panel r1, codex, grok, opus — reproduced). Every archive is kept.
# No `flock`, no `gzip`, a failure, a kill: the closed file stays as it is.
_pb_hj_rotate() {
    local jdir="$1" hfile="$2" every="${PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK:-32}" size stamp arch a now mt big=false idle=false
    [[ "$every" =~ ^[0-9]{1,4}$ ]] && [ "$((10#$every))" -ge 1 ] && [ "$((10#$every))" -le 1000 ] || every=32
    [ $(( RANDOM % 10#$every )) -eq 0 ] || return 0
    if [ -f "$hfile" ] && [ ! -L "$hfile" ]; then
        size=$(stat -c %s -- "$hfile" 2>/dev/null) || size=0
        case "$size" in ''|*[!0-9]*) size=0 ;; esac
        [ "$size" -le "$_PB_HJ_LIMIT" ] || big=true
    fi
    for a in "$jdir"/hooks.jsonl.archived-*; do
        case "$a" in *.gz|*.gz.tmp-*) continue ;; esac
        [ -f "$a" ] && [ ! -L "$a" ] && idle=true && break
    done
    [ "$big" = true ] || [ "$idle" = true ] || return 0
    command -v flock >/dev/null 2>&1 || return 0
    { exec 207<"$jdir"; } 2>/dev/null || return 0
    if flock -n 207 2>/dev/null; then
        if [ "$big" = true ]; then
            size=$(stat -c %s -- "$hfile" 2>/dev/null) || size=0
            case "$size" in ''|*[!0-9]*) size=0 ;; esac
            if [ "$size" -gt "$_PB_HJ_LIMIT" ] && [ -f "$hfile" ] && [ ! -L "$hfile" ] \
               && printf -v stamp '%(%Y%m%d-%H%M%S)T' -1 2>/dev/null; then
                arch="$jdir/hooks.jsonl.archived-$stamp-$$"
                if [ ! -e "$arch" ] && [ ! -L "$arch" ]; then
                    mv -n -- "$hfile" "$arch" 2>/dev/null || true
                fi
            fi
        fi
        if [ "$idle" = true ] && printf -v now '%(%s)T' -1 2>/dev/null; then
            for a in "$jdir"/hooks.jsonl.archived-*; do
                case "$a" in *.gz|*.gz.tmp-*) continue ;; esac
                [ -f "$a" ] && [ ! -L "$a" ] || continue
                mt=$(stat -c %Y -- "$a" 2>/dev/null) || continue
                case "$mt" in ''|*[!0-9]*) continue ;; esac
                [ $(( now - mt )) -gt 600 ] || continue
                _pb_hj_compress "$a"
            done
        fi
    fi
    { exec 207<&-; } 2>/dev/null || true
    return 0
}

_pb_hj_compress() {
    local arch="$1" tmp="$1.gz.tmp-$$"
    command -v gzip >/dev/null 2>&1 || return 0
    if [ -e "$arch.gz" ] || [ -L "$arch.gz" ] || [ -e "$tmp" ] || [ -L "$tmp" ]; then return 0; fi
    if ( set -o noclobber; gzip -c -- "$arch" > "$tmp" ) 2>/dev/null \
       && mv -n -- "$tmp" "$arch.gz" 2>/dev/null && [ -f "$arch.gz" ] && [ ! -e "$tmp" ]; then
        rm -f -- "$arch" 2>/dev/null
    elif [ -f "$tmp" ] && [ ! -L "$tmp" ]; then
        rm -f -- "$tmp" 2>/dev/null
    fi
    return 0
}
