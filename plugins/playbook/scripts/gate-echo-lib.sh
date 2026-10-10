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
    local mode="${1:-}" agents=""
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
            if [ "$mode" = all ]; then echo "${agents# }"; elif [ -n "$last_agent" ]; then echo "$last_agent"; else echo "daemon"; fi
            return 0
        fi
        comm="${comm##*/}"  # parameter expansion: strip path; safe for "-zsh" (basename would error)
        case "$comm" in
            claude*)
                # Task 105: the daemon's processes are `claude.exe` too; they are
                # shared by every hosted session — stop, never climb past them.
                # An unreadable argv counts as the boundary (round-1 R1-1).
                if [ -n "$use_proc" ]; then
                    if ! _proc_argv "$pid" || _is_daemon_argv "${_PB_ARGV[@]}"; then
                        if [ "$mode" = all ]; then echo "${agents# }"; elif [ -n "$last_agent" ]; then echo "$last_agent"; else echo "daemon"; fi
                        return 0
                    fi
                else
                    args=$(_ps_field "$pid" args=)
                    if [ -z "$args" ] || _is_daemon_args "$args"; then
                        if [ "$mode" = all ]; then echo "${agents# }"; elif [ -n "$last_agent" ]; then echo "$last_agent"; else echo "daemon"; fi
                        return 0
                    fi
                fi
                last_agent=$pid; agents="$agents $pid" ;;
            codex|agy|grok|pi) last_agent=$pid; agents="$agents $pid" ;;
        esac
        [ "$ppid" = "$pid" ] && break
        pid=$ppid
        count=$((count + 1))
    done
    if [ "$mode" = all ]; then echo "${agents# }"; else echo "$last_agent"; fi
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
                        *) echo "$PLAYBOOK_SESSION_ID"; return 0 ;;      # not a pid id
                    esac
                    case "$_pb_n" in
                        ""|*[!0-9]*) echo "$PLAYBOOK_SESSION_ID"; return 0 ;;
                    esac
                    _pb_w=$(_agent_walk all)
                    if [ -z "$_pb_w" ]; then
                        echo "$PLAYBOOK_SESSION_ID"; return 0
                    fi
                    if [ "${#_pb_n}" -le 10 ]; then
                        _pb_n=$((10#$_pb_n))
                        case " $_pb_w " in
                            *" $_pb_n "*) echo "$PLAYBOOK_SESSION_ID"; return 0 ;;
                        esac
                    fi
                fi ;;
        esac
    fi
    local agent_pid
    agent_pid=$(_agent_walk)
    case "$agent_pid" in
        daemon) echo "" ;;
        "") echo "pid-$PPID" ;;
        *) echo "pid-$agent_pid" ;;
    esac
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

# write_counter FILE KEY VALUE
# Set a key=value in the counter file. Creates file if missing, updates in-place if key exists.
# Uses grep-filter-append instead of sed to avoid delimiter collisions with gate text
# containing |, backticks, or other special characters.
write_counter() {
    local file="$1" key="$2" value="$3"
    local tmp="${file}.tmp.$$"
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
    return 0
}

# reset_counters FILE
# Reset tools=0 and writes=0, preserving gate_* fields. Creates file if missing.
reset_counters() {
    local file="$1"
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
}

# Owner's Q1 (task 173): two one-line files beside a session's `counters` —
# `turn_end`, the tool count at the last stop the stop hook let through, and
# `notif_start`, the write count when a notification started a turn. Files of
# their own, NOT keys of `counters`: write_counter sets a key by copying the
# whole file and renaming the copy over it, so a hook that overlaps a tool
# call's hook can rename a stale copy over that call's increment (impl panel
# r1). Nothing that serves the rule rewrites `counters`.
#
# write_session_mark FILE VALUE — temp file + rename in the same directory.
# Never fails the caller; a mark that cannot be written is simply not there.
write_session_mark() {
    local file="$1" value="$2"
    local tmp="${file}.tmp.$$"
    if ! { printf '%s\n' "$value" > "$tmp" 2>/dev/null && mv "$tmp" "$file" 2>/dev/null; }; then
        rm -f "$tmp" 2>/dev/null || true
    fi
    return 0
}

# read_session_mark FILE — its first line when that is a plain number of at
# most 18 digits; nothing otherwise (missing, empty, signed, spaced, anything
# else: the bytes are `.agent/`-resident and untrusted, like the counters).
read_session_mark() {
    local value=""
    [ -f "$1" ] || return 0
    IFS= read -r value < "$1" 2>/dev/null || true
    case "$value" in ''|*[!0-9]*) return 0 ;; esac
    [ "${#value}" -le 18 ] || return 0
    printf '%s' "$value"
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
    printf '%s' "$input" | python3 "$(_canonical_path "$(dirname "${BASH_SOURCE[0]}")")/write_log.py" \
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
