#!/bin/bash
_PB_HJ_T0=$EPOCHREALTIME     # the hook journal's start (task 222): first, before anything else runs
# monitor-nudge.sh — hook for injecting monitor nudges into agent context
#
# Registered as a NON-PLUGIN hook in .claude/settings.json.
# Works for both PostToolUse and UserPromptSubmit (reads event name from stdin).
# Current CC: PostToolUse additionalContext works, UserPromptSubmit is broken (bug #12151).
#
# Reads <agent-dir>/monitor/nudge.md (T121 flat layout), where <agent-dir> is
# the per-user lane `.agent/<user>/` when the repo declares one, else `.agent/`.
# The monitor WRITES its nudge into that same lane (launch-monitor resolves
# MONITOR_DIR the same way), so a root-only reader here would leave every nudge
# undelivered on a multi-user repo.
# If non-empty: atomic claim, emit additionalContext, log to chat_log.
# If empty or missing: exit 0 silently (no-op).

set -e

# Skip when invoked from the monitor's own claude session — otherwise the
# monitor's own Bash/Write tool calls would trigger this hook, which would
# consume the monitor's just-written nudge before the front agent ever sees
# it. Launch-monitor exports PLAYBOOK_ROLE=monitor specifically for this.
if [ "${PLAYBOOK_ROLE:-}" = "monitor" ]; then
    exit 0
fi

# Root + lane resolution are inlined rather than sourced from
# scripts/gate-echo-lib.sh: this file is registered as a NON-plugin hook and is
# copied into the project's own .claude/hooks/, so CLAUDE_PLUGIN_ROOT is unset
# and the plugin's scripts/ dir is at no fixed relative path. The contract is
# identical to that library (and to tasks/core.py and provider/paths.py);
# tests/wrapper-multiuser-fixture.sh asserts this copy agrees with them.

# Find project root: legacy `.agent/tasks/` OR multi-user `.agent/<user>/tasks/`.
find_root() {
    local dir="$PWD" sub
    while [ "$dir" != "/" ]; do
        [ -d "$dir/.agent/tasks" ] && echo "$dir" && return
        if [ -d "$dir/.agent" ]; then
            for sub in "$dir/.agent"/*/; do
                [ -d "${sub}tasks" ] && echo "$dir" && return
            done
        fi
        dir="$(dirname "$dir")"
    done
}

PROJECT_DIR=$(find_root)
[ -z "$PROJECT_DIR" ] && exit 0

# Resolve this user's lane. An unusable marker means we cannot tell whose nudge
# outbox to read: exit silently rather than read the shared root. Never fail the
# hook — a nudge is advisory, and taking down every tool call over a bad marker
# would be far worse than a missed nudge.
AGENT_DIR="$PROJECT_DIR/.agent"
if [ -f "$PROJECT_DIR/.agent/current_user" ]; then
    CU=""; CU_EXTRA=""
    # One-line contract — see gate-echo-lib.sh resolve_agent_dir.
    { read -r CU; read -r CU_EXTRA; } < "$PROJECT_DIR/.agent/current_user" 2>/dev/null || true
    CU="${CU%$'\r'}"
    [ -n "$CU_EXTRA" ] && exit 0
    case "$CU" in
        ""|"."|"..") exit 0 ;;
        [a-zA-Z0-9]*) ;;
        *) exit 0 ;;
    esac
    case "$CU" in
        *[!a-zA-Z0-9_.-]*) exit 0 ;;
    esac
    AGENT_DIR="$PROJECT_DIR/.agent/$CU"
fi

# The hook journal (task 222): one line per run in <lane>/journal/hooks.jsonl — an inline copy of gate-echo-lib.sh's
# writer, which this file cannot source: the same fields, caps and sink rules (no line in the fresh-clone shape, for a
# lane that is a link or not writable, through a linked journal/, into a hooks.jsonl that is a link or not a regular
# file — a FIFO would hang the hook); no rotation of its own (the plugin's hooks rotate the file).
# tests/test_hook_journal_python.py holds the three writers to the same answers. Always keeps the exit status. No
# line for a review's session (judge, tail-cert).
PB_HJ_DECISION=""; PB_HJ_DETAIL=""
_pb_nudge_journal() {
    local rc=$? LC_ALL=C lane="$AGENT_DIR" p="$PROJECT_DIR" jdir hfile ts ms=null t0="${_PB_HJ_T0:-}" t1 locked=none
    local re v h k sub event tool tuid sid hsid agent decision detail line error="" fresh=false
    set +e
    case "${PLAYBOOK_SESSION_ID:-}" in judge|tail-cert) exit "$rc" ;; esac
    _pbn_id() { v="${1:0:$(( $2 * 2 ))}"; v="${v//[^A-Za-z0-9._:@\/+=-]/}"; v="${v:0:$2}"; }
    # the head only: before the first tool input, within 4 KiB (a nested `agentId` is not the caller's — impl panel r1)
    _pbn_first() {
        re="\"($1)\"[[:space:]]*:[[:space:]]*\"([^\"]*)\""; v=""; h="${INPUT:0:4096}"
        h="${h%%\"tool_input\"*}"; h="${h%%\"toolInput\"*}"
        [[ "$h" =~ $re ]] && v="${BASH_REMATCH[2]}"
    }
    # escaped, control characters as spaces, at most $2 bytes cut on a whole character and a whole escape
    _pbn_text() {
        v="${1:0:$(( $2 * 2 ))}"; v="${v//[$'\001'-$'\037'$'\177']/ }"; v="${v//\\/\\\\}"; v="${v//\"/\\\"}"
        if [ "${#v}" -gt "$2" ]; then
            k=0; h="$2"
            while [ "$k" -lt 3 ] && [ "$h" -gt 0 ] && [[ "${v:h:1}" == [$'\x80'-$'\xbf'] ]]; do h=$(( h - 1 )); k=$(( k + 1 )); done
            v="${v:0:h}"
            if [[ "$v" =~ \\+$ ]] && (( ${#BASH_REMATCH[0]} % 2 )); then v="${v%\\}"; fi
        fi
    }
    if [ ! -f "$p/.agent/current_user" ] && [ ! -d "$p/.agent/tasks" ]; then
        for sub in "$p/.agent"/*/; do [ -d "${sub}tasks" ] && fresh=true && break; done
    fi
    if [ "$fresh" != true ] && [ -d "$lane" ] && [ ! -L "$lane" ] && [ -w "$lane" ]; then
        jdir="$lane/journal"; hfile="$jdir/hooks.jsonl"
        if [ ! -L "$jdir" ] && { [ -d "$jdir" ] || { [ ! -e "$jdir" ] && { mkdir "$jdir" 2>/dev/null || [ -d "$jdir" ]; }; }; } \
           && [ ! -L "$jdir" ] && [ ! -L "$hfile" ] && { [ ! -e "$hfile" ] || [ -f "$hfile" ]; }; then
            # the shared lock first (the plugin's writers' rule): a wait counts in `ms`; past 1 s, a spool, never an
            # unlocked append (the owner, 2026-10-17 00:13: "Bilet în cutie"); no `flock`: appended directly
            if command -v flock >/dev/null 2>&1 && { exec 208<"$jdir"; } 2>/dev/null; then
                if flock -s -w 1 208 2>/dev/null; then locked=yes; else locked=no; fi
            fi
            t1="${EPOCHREALTIME:-}"
            TZ=UTC0 printf -v ts '%(%Y-%m-%dT%H:%M:%SZ)T' -1 2>/dev/null
            if [[ "$t0" =~ ^[0-9]{1,12}[.,][0-9]{6}$ ]] && [[ "$t1" =~ ^[0-9]{1,12}[.,][0-9]{6}$ ]]; then
                ms=$(( (10#${t1/[.,]/} - 10#${t0/[.,]/}) / 1000 )); [ "$ms" -ge 0 ] || ms=null
            fi
            _pbn_first 'hook_event_name|hookEventName'; _pbn_id "$v" 32; event="$v"
            _pbn_first 'tool_name|toolName'; _pbn_id "$v" 64; tool="$v"
            tuid=""
            if [[ "$event" == *Tool* ]]; then
                v="$INPUT"; [ "${#v}" -le 4096 ] || v="${v:${#v}-4096}"
                re='.*"(tool_use_id|toolUseId)"[[:space:]]*:[[:space:]]*"([^"]*)"'
                if [[ "$v" =~ $re ]]; then v="${BASH_REMATCH[2]}"
                else re='"(tool_use_id|toolUseId)"[[:space:]]*:[[:space:]]*"([^"]*)"'; v=""; [[ "$INPUT" =~ $re ]] && v="${BASH_REMATCH[2]}"; fi
                _pbn_id "$v" 64; tuid="$v"
            fi
            _pbn_first 'session_id|sessionId'; _pbn_id "$v" 64; hsid="$v"
            _pbn_first 'agent_id|agentId'; _pbn_id "$v" 64; agent="$v"
            _pbn_id "${PLAYBOOK_SESSION_ID:-}" 64; sid="$v"
            decision="$PB_HJ_DECISION"
            if [ -z "$decision" ]; then
                case "$rc" in 2) decision=block ;; 0) decision=ok ;; *) decision=error ;; esac
            fi
            _pbn_id "$decision" 16; decision="$v"
            _pbn_text "$PB_HJ_DETAIL" 128; detail="$v"
            case "$rc" in
                0|2) ;;
                *) _pbn_text "$BASH_COMMAND" 240; error="$v" ;;
            esac
            case "$rc" in ''|*[!0-9]*) rc=255 ;; esac
            printf -v line '{"ts":"%s","hook":"monitor-nudge","event":"%s","tool":"%s","tool_use_id":"%s","session_id":"%s","host_session_id":"%s","agent_id":"%s","decision":"%s","detail":"%s","ms":%s,"exit":%s,"error":"%s"}' \
                "$ts" "$event" "$tool" "$tuid" "$sid" "$hsid" "$agent" "$decision" "$detail" "$ms" "$rc" "$error"
            if [ "${#line}" -lt 1024 ]; then
                if [ "$locked" = no ]; then
                    sub="$jdir/hooks.jsonl.spool-$ts-$$-$RANDOM$RANDOM"; sub="${sub//:/}"
                    h="$jdir/hooks.jsonl.spooling-${sub##*/hooks.jsonl.spool-}"
                    if ( set -o noclobber; printf '%s\n' "$line" > "$h" ) 2>/dev/null; then
                        mv -n -- "$h" "$sub" 2>/dev/null || rm -f -- "$h" 2>/dev/null
                    fi
                else
                    { printf '%s\n' "$line" >> "$hfile"; } 2>/dev/null
                fi
            fi
            [ "$locked" = none ] || { exec 208<&-; } 2>/dev/null
        fi
    fi
    exit "$rc"
}
trap '_pb_nudge_journal' EXIT

# Read stdin to extract hook_event_name (PostToolUse or UserPromptSubmit)
INPUT=$(cat)
export EVENT_NAME=$(echo "$INPUT" | python3 -I -c "import sys,json; print(json.loads(sys.stdin.read() or '{}').get('hook_event_name','UserPromptSubmit'))" 2>/dev/null || echo "UserPromptSubmit")

SESSION_ID="${PLAYBOOK_SESSION_ID:-pid-$PPID}"
NUDGE_FILE="$AGENT_DIR/monitor/nudge.md"

# Orphan recovery: a crash between this hook's rename-claim (below) and either
# its success `rm` or its failure `ln`-restore STRANDS a
# `nudge.md.delivering.<pid>.<rand>` file. Later invocations only look at
# nudge.md, so that already-claimed nudge is silently lost — breaking the
# at-least-once contract the unique-claim design establishes. Before making this
# invocation's own claim, re-land any stranded claim whose OWNER PROCESS IS DEAD.
# A claim whose pid is still alive is a concurrent in-flight delivery: leave it.
# All arms are `set -e`-safe (guards / `|| true` / `if`), and $$/$RANDOM/kill are
# bash builtins (bash 3.2+). Re-landing uses the SAME atomic `ln` no-clobber
# as the failure path so a stale orphan can never destroy a newer live nudge.
for orphan in "$AGENT_DIR"/monitor/nudge.md.delivering.*; do
    [ -e "$orphan" ] || continue                    # glob had no match
    if [ ! -s "$orphan" ]; then                     # empty stray claim — just drop it
        rm -f "$orphan" 2>/dev/null || true
        continue
    fi
    obase=${orphan##*/nudge.md.delivering.}         # <pid>.<rand>
    opid=${obase%%.*}                               # <pid>
    case "$opid" in
        ''|*[!0-9]*) continue ;;                    # unparseable pid — don't guess, leave it
    esac
    if kill -0 "$opid" 2>/dev/null; then
        continue                                    # owner alive → in-flight, not orphaned
    fi
    # Owner dead → re-land the orphan as nudge.md, atomically and no-clobber.
    if ln "$orphan" "$NUDGE_FILE" 2>/dev/null; then
        rm -f "$orphan" 2>/dev/null || true         # linked → drop the extra name
    elif [ -e "$NUDGE_FILE" ]; then
        rm -f "$orphan" 2>/dev/null || true         # a newer nudge won; drop stale orphan
    else
        # ln unavailable/failing and no nudge.md — non-atomic fallback (as legacy).
        mv "$orphan" "$NUDGE_FILE" 2>/dev/null || rm -f "$orphan" 2>/dev/null || true
    fi
done

# No nudge file or empty — silent exit
PB_HJ_DETAIL="no nudge"
[ -f "$NUDGE_FILE" ] || exit 0
[ -s "$NUDGE_FILE" ] || exit 0

# Atomic claim: rename the single nudge.md to a PER-INVOCATION UNIQUE path so
# concurrent hook firings can't collide. With a fixed shared `.delivering` name,
# a second firing's `mv` overwrote a first firing's in-flight claim (or both read
# the same claimed file) — dropping or double-delivering a nudge. A unique
# destination makes `mv` the mutual exclusion: exactly ONE concurrent firing can
# rename the single nudge.md (rename is atomic on the source inode); the losers'
# `mv` finds the source already gone and exits silently. `$$` (this hook
# process's pid) + `$RANDOM` are bash builtins (bash 3.2+); together
# they're unique across concurrent firings and robust to pid reuse.
DELIVERING="$NUDGE_FILE.delivering.$$.$RANDOM"
PB_HJ_DETAIL="nudge taken by another run"
mv "$NUDGE_FILE" "$DELIVERING" 2>/dev/null || exit 0
PB_HJ_DETAIL="empty nudge"

# Read content
NUDGE_CONTENT=$(cat "$DELIVERING")

# Skip if content is empty after read (drop the claimed empty file)
[ -z "$NUDGE_CONTENT" ] && { rm -f "$DELIVERING"; exit 0; }

# Emit additionalContext — this is what gets injected into the agent's context.
# The nudge was already CLAIMED (moved) above, so a failed emit (e.g. python3
# missing) used to consume-and-drop it under `set -e`. Build the JSON first, and
# only clear the claim on a SUCCESSFUL emit; on failure RESTORE the file so a
# later session can still deliver it — a nudge is delivered at-least-once, never
# silently lost. (stderr, not stdout, would carry any diagnostic — but here we
# simply leave the nudge for next time.)
if NUDGE_JSON=$(python3 -I -c "
import json, sys, os
nudge = sys.stdin.read().strip()
event = os.environ.get('EVENT_NAME', 'UserPromptSubmit')
out = {
    'hookSpecificOutput': {
        'hookEventName': event,
        'additionalContext': '[MONITOR] ' + nudge
    }
}
print(json.dumps(out))
" <<< "$NUDGE_CONTENT" 2>/dev/null) && [ -n "$NUDGE_JSON" ]; then
    printf '%s\n' "$NUDGE_JSON"
    rm -f "$DELIVERING"
    PB_HJ_DECISION=context; PB_HJ_DETAIL="nudge delivered"
else
    PB_HJ_DETAIL="nudge kept: it could not be built"
    # Emit failed (e.g. python3 missing): preserve at-least-once — re-land our
    # claim as nudge.md. But NEVER clobber a NEWER nudge the monitor may have
    # written after our claim. A `[ -e ] then mv` check is NOT atomic — the
    # monitor can write newer B between the test and the mv, and mv would
    # overwrite+lose B. `ln` (hard link, no `-f`) is atomic no-replace: it FAILS
    # (EEXIST) if nudge.md already exists, so our OLDER claim can never destroy
    # the monitor's NEWER nudge. On success nudge.md is re-landed; either way the
    # claim file is then dropped. `ln` unavailable/failing for another reason
    # falls back to a best-effort non-atomic restore (this whole branch exists
    # precisely because tools can be missing). All arms are `set -e`-safe.
    if ln "$DELIVERING" "$NUDGE_FILE" 2>/dev/null; then
        rm -f "$DELIVERING" 2>/dev/null || true          # linked → drop the extra name
    elif [ -e "$NUDGE_FILE" ]; then
        rm -f "$DELIVERING" 2>/dev/null || true          # ln EEXIST: a newer nudge won; drop stale claim
    else
        # ln unavailable and no nudge.md — non-atomic fallback, same as legacy.
        mv "$DELIVERING" "$NUDGE_FILE" 2>/dev/null || rm -f "$DELIVERING" 2>/dev/null || true
    fi
    exit 0
fi

# Log to chat_log
LOCAL_LOG="$AGENT_DIR/chat_log.md"
if [ -f "$LOCAL_LOG" ]; then
    TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
    {
        echo "---"
        echo ""
        echo "**[MONITOR→$SESSION_ID]** [$TIMESTAMP] ($EVENT_NAME)"
        echo ""
        echo "$NUDGE_CONTENT"
        echo ""
    } >> "$LOCAL_LOG"
fi
