# claude-playbook: project-scoped command logging (bash)
# Sourced via BASH_ENV — logs commands in playbook projects to the current
# user's lane: .agent/bash_history, or .agent/<user>/bash_history.
# Purpose: forensic post-mortem record ("what did the agent actually run?")

# PLAN S5b (task 088): a shell that asks not to be logged installs no trap at
# all. The Claude Code statusline is the reason (~710 renders a day, ~40 simple
# commands each). This file is sourced BEFORE a script's first line, so the knob
# must be in the environment the shell starts with (e.g. a statusLine command of
# `PLAYBOOK_NO_BASHLOG=1 bash ~/.claude/statusline.sh`); exporting it inside a
# script covers only the shells that script starts. A script NAMED statusline is
# skipped in the callback below either way. "" and "0" mean "log".
case "${PLAYBOOK_NO_BASHLOG:-}" in
    ""|0) ;;
    *) return 0 ;;
esac

# PLAN S11, task 174 (retro 107 R13: "lock + prepared-temp replace"). The rotation of
# a history past 50 MB used to be `mv` it away, then append its `tasks work|new`
# lines to a new file: two shells that found it big together both rotated (the
# second moved away the first's fresh file), a shell killed between the two steps
# left the activations only in the archive — and `tasks retro` / `tasks timeline`
# read only the live file — and every rotation carried every activation line ever
# written. Now:
#   * UNDER A LOCK, or not at all. `flock -n` on a descriptor opened on the history
#     file itself (no lock file to leave behind); a shell that does not get it
#     leaves the lane alone and just logs. Where no lock can be had — no `flock`
#     on PATH, or one that cannot lock on this mount — the history is NOT rotated:
#     it grows, and nothing in it is lost (owner, 2026-10-10; an order without a
#     lock let one shell undo what another had written down).
#   * The lock that counts is the one on the file that has the live name NOW: a
#     rotation gives that name to another file, so a lock taken on what turns out
#     to be the old one is no lock, and the rotator itself takes the new file's
#     lock before it gives anything back.
#   * WRITTEN DOWN first. Before anything is moved, a marker beside the history
#     (`bash_history.archived-owes`) names the archive about to be made. A shell
#     that finds it — one `[[ -f ]]` where the size is looked at — gives back what
#     that archive still owes and removes it. So a rotation that dies at ANY step
#     leaves either the history as it was or an archive the next shell collects
#     from (impl panel round 1).
#   * GIVEN BACK, repeatably: every carried line of the archive that the live file
#     does not hold yet is appended to it. "Carried" is the newest 5 MiB of whole
#     activation lines — the bound.
#   * TWICE: by the shell that rotated, at once, and once more by the NEXT shell,
#     which is the one that removes the marker. A shell that had the old file open
#     at the rename writes its line into the archive after the first giving-back;
#     the second is for that line (owner, 2026-10-10 — a lock taken by every
#     logged command would cost each of them a process).
#   * The fresh file is PREPARED and renamed into place, with the archive made as
#     a second name of the old one: the live name never ceases to exist and never
#     holds less than the carried lines. Only where a hard link cannot be made is
#     the history moved away and the lines given back — between the two the live
#     file lacks them, until this shell, or after a death the next one, gives
#     them back.
# Left: a process that keeps the old file open past the next shell's giving-back
# and writes an activation then; an activation older than the bound.
# The lane is a directory that comes with the project: a marker, an archive and
# symbolic links can be SHIPPED in it, and the marker makes the first shell that
# logs there act. So nothing is written through a name somebody else may have
# put there: a history that is a link is left alone altogether, the marker and
# the prepared file are made anew (removed first, then created only if absent),
# and what a giving-back works with is kept outside the lane (`mktemp -d`).
# Files it makes in the lane all begin `bash_history.archived-` (`….new` while in
# preparation), which the judges' read mask and the seeded ignore block already
# cover. Nothing here may fail the caller or print: it runs in the DEBUG trap of
# every shell (see the callback below).
_cpb_rotate() {
    local _lane="$1"
    {
        (
            # This subshell is the whole of it: the host's options and its DEBUG
            # trap do not apply in here, nothing in here reaches the host, and a
            # command of the rotation that turns out to be a bash script (a
            # wrapper on PATH) starts unlogged — it would otherwise be a logged
            # shell that finds the same big file. `command` below: a FUNCTION
            # of the host's script named like one of these is not ours. Task
            # 176: bash's `printf` and `read`, which a script can switch off
            # (`enable -n`), are not used in here — and what stood in for them
            # is not believed on its word either: the marker is read by bash
            # itself (`$(<file)`) and read back after it is written, and
            # nothing is put in front of what is carried for a later step to
            # drop (a line in front that was missing cost the first line owed).
            set +e +u +C
            trap - DEBUG
            export PLAYBOOK_NO_BASHLOG=1
            _hist="$_lane/bash_history"
            _owes="$_lane/bash_history.archived-owes"
            _re=' [|] [A-Za-z0-9_]+ [|] .*tasks[[:space:]]+(work|new)'

            # FROM's newest activation lines, whole, of at most 5 MiB, written
            # to the file TO. All of them where they fit — nothing is cut, so
            # nothing is dropped. Only where they are past the bound: the last
            # 5 MiB and one byte of them, without the first line of that — the
            # line that was cut, or the one line too many. `-a`: a history can
            # hold stray binary bytes. Fails unless FROM was read whole (`grep`:
            # 0 = lines found, 1 = none).
            _carried() {
                command grep -a -E "$_re" "$1" > "$2" 2>/dev/null
                [[ $? -le 1 && -f "$2" ]] || return 1
                # (a `find` that fails has not said "they fit": no carry)
                _past=$(command find "$2" -prune -size +5242880c 2>/dev/null) || return 1
                if [[ -n "$_past" ]]; then
                    command tail -c 5242881 "$2" 2>/dev/null | command tail -n +2 > "$2.cut.new" 2>/dev/null
                    _st=("${PIPESTATUS[@]}")
                    if [[ "${_st[0]}" -ne 0 || "${_st[1]}" -ne 0 ]] || ! command mv -f "$2.cut.new" "$2" 2>/dev/null; then
                        command rm -f "$2.cut.new" 2>/dev/null
                        return 1
                    fi
                fi
                return 0
            }

            # Does FILE end on a line? 0: yes, or it is empty. 1: no — it ends
            # inside one. 2: it could not be looked at. The last byte goes to a
            # file and `wc -l` counts it: a NUL is then a byte like any other
            # (a command substitution drops it and would read "nothing"), and
            # either tool failing is an answer of its own (impl panel r2).
            _ends_on_a_line() {
                [[ -s "$1" ]] || return 0
                command tail -c 1 "$1" > "$_tmp/last" 2>/dev/null || return 2
                [[ -s "$_tmp/last" ]] || return 2
                _nl=$(command wc -l < "$_tmp/last" 2>/dev/null) || return 2
                [[ "${_nl//[!0-9]/}" == 1 ]]
            }

            # Every carried line of ARCHIVE that the live file does not hold yet,
            # appended to it. May be run again after any death: it gives only
            # what is missing. Fails (and the marker stays) unless it got through.
            _deliver() {
                # its two working files are NOT in the lane: their names there
                # would follow from the marker, which the project can ship —
                # with a link under either name
                _tmp=$(command mktemp -d 2>/dev/null) || return 1
                [[ -n "$_tmp" && -d "$_tmp" ]] || return 1
                if ! _carried "$1" "$_tmp/owed"; then
                    command rm -rf "$_tmp" 2>/dev/null
                    return 1
                fi
                if [[ -f "$_hist" ]]; then
                    command grep -a -E "$_re" "$_hist" > "$_tmp/held" 2>/dev/null
                    [[ $? -le 1 ]] || { command rm -rf "$_tmp" 2>/dev/null; return 1; }
                else
                    : > "$_tmp/held"
                fi
                # a live file that ends inside a line (a writer was killed in it):
                # closed first, so that what is given back starts on a line of its own
                _ends_on_a_line "$_hist"
                _rc=$?
                if [[ "$_rc" -eq 1 ]]; then
                    command cat <<< '' >> "$_hist" 2>/dev/null
                    # looked at again, not taken on `cat`'s word
                    _ends_on_a_line "$_hist"
                    _rc=$?
                fi
                if [[ "$_rc" -ne 0 ]]; then
                    # it does not end on a line, or that could not be told:
                    # nothing is given, and the marker stays
                    command rm -rf "$_tmp" 2>/dev/null
                    return 1
                fi
                command grep -a -F -x -v -f "$_tmp/held" "$_tmp/owed" >> "$_hist" 2>/dev/null
                _rc=$?
                command rm -rf "$_tmp" 2>/dev/null
                [[ "$_rc" -le 1 ]]
            }

            # The lock, on whatever file has the live name now. Fails when another
            # shell holds it, when no lock can be had here at all, and when the
            # file it was taken on is no longer the live one (a rotation went by
            # between the open and the lock). For appending, never truncating:
            # over NFS an exclusive flock needs a descriptor open for WRITING
            # (flock(2), "NFS details"). And never on a history that is a
            # symbolic link: opening it would make, and everything after it would
            # write, a file somewhere else.
            _take() {
                [[ ! -L "$_hist" ]] || return 1
                # (a plain `exec`: through `builtin` the descriptor would be open
                # for that one command only — measured: flock then answers 65)
                exec 9>>"$_hist" || return 1
                command flock -n 9 || return 1
                [[ ! -L "$_hist" && /dev/fd/9 -ef "$_hist" ]]
            }

            _take || exit 0
            # what a rotator that was killed left in preparation — under the lock
            # nobody else is preparing anything
            command find "$_lane" -maxdepth 1 -type f -name 'bash_history.archived-*.new' -delete 2>/dev/null

            # A rotation that has not been closed: one that died, or one that
            # lived and left this second giving-back to us. The marker's one line
            # is read as the NAME of an archive in this lane, and as nothing else.
            # A marker of OURS is a regular file that holds one short name. One
            # that is a symbolic link, or longer than a name can be, was put
            # there by somebody else (a project can ship it — a link to a very
            # large file, a payload after a first line that reads well): it is
            # removed WITHOUT being read, and nothing is given on its word
            # (impl panel r2). Whether it is too long is asked of `find`; if
            # that cannot be asked, the marker is left for the next shell.
            if [[ -L "$_owes" ]]; then
                command rm -f "$_owes" 2>/dev/null
            elif [[ -f "$_owes" ]]; then
                _long=$(command find "$_owes" -prune -size +255c 2>/dev/null) || exit 0
                if [[ -n "$_long" ]]; then
                    command rm -f "$_owes" 2>/dev/null
                    _name=""
                else
                    # read by bash itself: no program whose failing, and no
                    # builtin whose absence, could empty the name and have the
                    # marker removed with nothing given
                    _name=$(<"$_owes")
                    _name="${_name%%$'\n'*}"
                fi
                case "$_name" in
                    */*) _name="" ;;
                    bash_history.archived-owes) _name="" ;;
                    bash_history.archived-?*) ;;
                    *) _name="" ;;
                esac
                if [[ -n "$_name" && -f "$_lane/$_name" && ! -L "$_lane/$_name" ]]; then
                    if [[ "$_hist" -ef "$_lane/$_name" ]]; then
                        # named, linked, never renamed: it is a second name of
                        # the file still in use, and nothing is owed
                        command rm -f "$_lane/$_name" 2>/dev/null
                    else
                        _deliver "$_lane/$_name" || exit 0
                    fi
                fi
                command rm -f "$_owes" 2>/dev/null
            fi

            # by NAME, and now: is it (still) past the limit?
            [[ -n "$(command find "$_hist" -prune -size +52428800c 2>/dev/null)" ]] || exit 0
            _arch="$_lane/bash_history.archived-$(command date '+%Y%m%d-%H%M%S')-$$" || _arch="$_lane/bash_history.archived-$$"
            _new="$_arch.new"
            # never over an archive that exists (same shell, same second): the
            # next shell rotates, under another name
            [[ ! -e "$_arch" ]] || exit 0
            # the marker and the prepared file are made ANEW: whatever has that
            # name is removed, and under noclobber `>` creates only what is absent
            # — it never opens what a link under that name points to
            command rm -f "$_owes" "$_new" 2>/dev/null
            set -C
            command cat <<< "${_arch##*/}" > "$_owes" 2>/dev/null
            # … and read back: a marker that does not say what was written —
            # nothing, or something else — is how a later shell would lose
            # track of what this archive owes. Nothing has been moved yet.
            if [[ "$(<"$_owes")" != "${_arch##*/}" ]]; then
                command rm -f "$_owes" 2>/dev/null
                exit 0
            fi
            _carried "$_hist" "$_new"
            _rc=$?
            set +C
            if [[ "$_rc" -ne 0 ]]; then
                # the lines could not be read out whole: the history stays as it is
                command rm -f "$_new" "$_owes" 2>/dev/null
                exit 0
            fi
            if command ln "$_hist" "$_arch" 2>/dev/null; then
                command mv -f "$_new" "$_hist" 2>/dev/null
                # read off the files, not off `mv`: a rename that happened and
                # then reported failure has left the archive as the ONLY name of
                # the old history
                if [[ "$_hist" -ef "$_arch" ]]; then
                    command rm -f "$_new" "$_arch" "$_owes" 2>/dev/null
                    exit 0
                fi
            else
                # no hard link on this filesystem: moved away, then given back —
                # the marker is what makes a death between the two something the
                # next shell repairs
                command rm -f "$_new" 2>/dev/null
                command mv -f "$_hist" "$_arch" 2>/dev/null
                if [[ ! -f "$_arch" ]]; then
                    command rm -f "$_owes" 2>/dev/null
                    exit 0
                fi
            fi
            # the live name is another file now: its lock, before anything is
            # given back. If another shell has it, it has seen the marker.
            _take || exit 0
            _deliver "$_arch"
            # … and the marker stays: the next shell gives back once more (a
            # line that was in flight at the rename) and is the one to remove it
            exit 0
        )
    } 2>/dev/null || true
    return 0
}

_cpb_log_cmd() {
    # Hook shells are implementation machinery, not user/agent Bash tool calls.
    # BASH_ENV is sourced before bash assigns the script name to $0, so this
    # check must live in the DEBUG callback (where $0 is final), not at source
    # time.  It avoids both history noise and the expensive walk/date fork for
    # every hook-internal command. Real Bash tool shells keep $0 as bash/sh.
    # The script's own NAME only (`${0##*/}`), never a directory of that name.
    # A backslash is part of a name on Linux (up to 1.5.47 the name was also cut
    # at its last backslash, for Git Bash's `bash C:\…\statusline.sh`). ONE
    # line, ending in the S17 marker: the wrapper fixture's negative control
    # deletes the marked line, and any statement left before the `$BASH_COMMAND`
    # case would reset the stale status that control needs.
    local _me="${0##*/}"; case "$_me" in *-hook|statusline|statusline.sh|statusline-*) return 0 ;; esac  # PB-S17-FAST-PATH

    # Filter shell internals and CC infrastructure noise.
    #
    # Every exit from this function MUST be `return 0`, never bare `return`:
    # inside a DEBUG trap a bare `return` propagates the *stale* `$?` of the
    # previously executed command, and a DEBUG trap returning non-zero kills
    # a `set -e` shell. Since these arms match exactly the commands hooks run
    # constantly (`[ -d …`, `[[ …`), a bare return here silently killed every
    # `set -e` PostToolUse hook (state-echo-hook → gate logging dead; field
    # report 2026-07-21, reproduced on bash 3.2 and 5.2).
    case "$BASH_COMMAND" in
        *shell-snapshots*|"pwd -P"*|"case \$- in"*|return|"[["*) return 0 ;;
        "[ -d "*|"[ -f "*|"[ -n "*|"[ -z "*|"[ ! "*) return 0 ;;
        HIST*=*|PATH=*|"set -o"*|"shopt "*|"trap "*|"export PATH"*) return 0 ;;
        source*|.) return 0 ;;
    esac

    # PLAN S5b: each distinct command text is logged ONCE per shell process.
    # A DEBUG trap fires for every simple command, so a 5-iteration loop wrote
    # its header and body five times each (11 lines for `for …; do …; done; …`;
    # 195,168 × 3 rows on 2026-09-21). The text is the unexpanded source, so a
    # loop body is the same string every iteration. Bash 3.2 has no associative
    # arrays: a \x1f-delimited string, reset past 64 KiB.
    # The key holds the working directory too (panel r2): the destination lane
    # follows $PWD, so one shell running the same text in two projects logs it
    # in both. Activation-shaped text — `tasks` followed later by ` work` or
    # ` new` — is never deduped (panels r1-r3): retro and timeline build task
    # windows from those lines, and a quoted or backslash path (`"$B/tasks"
    # work 7`) must count too, while a loop that merely names `.agent/tasks`
    # is still deduped.
    local _key=$'\x1f'"$PWD"$'\x1e'"$BASH_COMMAND"$'\x1f'
    case "$BASH_COMMAND" in
        *tasks*" work"*|*tasks*" new"*) _key="" ;;
    esac
    if [[ -n "$_key" ]]; then
        case "${_CPB_SEEN:-}" in
            *"$_key"*) return 0 ;;
        esac
    fi

    # Walk up from $PWD looking for .agent/ directory
    local _dir="$PWD"
    while [[ "$_dir" != "/" ]]; do
        if [[ -d "$_dir/.agent" ]]; then
            # Log into THIS user's lane, which is the same file `tasks retro`
            # and `tasks context` read (cli.py resolve_agent_dir()/bash_history).
            # Writing the root while the CLI reads a lane makes the history
            # invisible and mixes users' commands together.
            #
            # Pure builtins only: this runs in a DEBUG trap on every single
            # command, so a `cat`/`tr` subshell here is a per-command fork.
            # `read` also trims surrounding whitespace, matching the .strip()
            # in tasks/core.py.
            #
            # The lane starts UNKNOWN. Defaulting it to "$_dir/.agent" and
            # only reassigning inside the marker branch is what made a fresh
            # clone of a multi-user repo — lanes present, the gitignored
            # marker absent — append every command to the SHARED root
            # .agent/bash_history (PB-LANE-RESOLUTION, Critical).
            #
            # The decision rests on exactly two filesystem facts: the marker,
            # and whether root .agent/tasks/ is a directory. Nothing about
            # .agent/'s children is consulted.
            #   valid marker                       -> the validated user lane
            #   no marker, root .agent/tasks/      -> the root IS a lane
            #   anything else                      -> owner unknown; skip
            local _lane=""
            if [[ -f "$_dir/.agent/current_user" ]]; then
                local _u="" _extra=""
            # One-line contract, same as tasks/core.py / provider/paths.py /
            # gate-echo-lib.sh: strip a trailing CR (CRLF markers), reject a
            # second content line (`alice\n../evil` must not become lane
            # `alice`), and `|| true` because `read` returns 1 on a marker with
            # no trailing newline — unguarded that trips errexit inside this
            # per-command DEBUG trap.
                { read -r _u; read -r _extra; } < "$_dir/.agent/current_user" 2>/dev/null || true
                _u="${_u%$'\r'}"
                [[ -n "$_extra" ]] && return 0
                # An unusable marker means we cannot know which lane owns this
                # command. Skip logging entirely — falling back to the shared
                # root is the cross-user contamination this lane model exists
                # to prevent. Never `exit`: this is the user's live shell.
                case "$_u" in
                    ""|"."|"..") return 0 ;;
                    [a-zA-Z0-9]*) ;;
                    *) return 0 ;;
                esac
                case "$_u" in
                    *[!a-zA-Z0-9_.-]*) return 0 ;;
                esac
                _lane="$_dir/.agent/$_u"
                # Task 176 (impl panel r1): a user lane that is a symbolic link
                # is not logged to — a project can ship `current_user` and the
                # lane as a link to a directory elsewhere, and the user's
                # commands were written there. (The root `.agent` as a link
                # is the user's own business: a shipped one is followed only
                # to a directory that already holds `tasks/` or the marker.)
                [[ ! -L "$_lane" ]] || return 0
            elif [[ -d "$_dir/.agent/tasks" ]]; then
                # No marker, but root .agent/tasks/ means the root IS itself a
                # legitimate lane (the legacy and mixed layouts). Refusing it
                # here would kill logging for every single-user project.
                _lane="$_dir/.agent"
            else
                # No marker, no root tasks/: owner unknown, so the shared
                # root is not elected. Stricter than lanes_without_marker,
                # which still answers the root — a missing forensic log, not
                # contamination, healed by anything creating .agent/tasks/.
                # No glob remains here, so no host option can move this.
                return 0
            fi
            [[ -d "$_lane" ]] || return 0
            local _cmd="${BASH_COMMAND//$'\n'/\\n}"
            # `|| return 0`: an append failure (perms, disk, history path
            # replaced by a directory) is a failing simple command inside the
            # trap — under the host shell's `set -e` that would kill it the
            # same way a bare return does.
            #
            # The brace group matters: `echo … >> file 2>/dev/null` does NOT
            # silence a failure to OPEN the file, because bash reports that
            # before it applies `2>/dev/null`. Since this runs per command, the
            # unguarded form prints "Is a directory" once for EVERY command in
            # every hook shell — and hook stderr/stdout is fed back to the
            # agent. Redirecting the group suppresses it properly.
            # PLAN S5b: a history past 50 MB is rotated before the first write a
            # shell makes to it (the 2026-09-21 file had reached 136 MB). Checked
            # once per process PER LANE (panel r2: a process-wide flag skipped a
            # second project), so one shell can overshoot by what it writes.
            local _rk=$'\x1f'"$_lane"$'\x1f'
            case "${_CPB_ROTATE_CHECKED:-}" in
                *"$_rk"*) ;;
                *)
                _CPB_ROTATE_CHECKED="${_CPB_ROTATE_CHECKED:-}$_rk"
                # `find -size +Nc` is one stat (a `wc -c` may read the whole
                # file). It is only the cheap first look: the rotation looks
                # again under its lock (`_cpb_rotate`, task 174), so two shells
                # that both see a big file here still make one archive.
                # The `tasks work|new` lines are carried into the fresh file
                # (panel r2): retro opens each task's window at its EARLIEST
                # activation and reads only the live file, so archiving the
                # active task's activation made its window vanish.
                # The marker of a rotation that has not been closed — one that
                # died, or the one before this shell, which leaves it a second
                # giving-back — sends this shell there (a builtin test, no fork).
                local _big=""
                # `find` is started with logging off (task 176): on PATH it
                # can be a bash script, and that script would be a logged
                # shell taking this same first look — a chain without an end.
                if [[ -f "$_lane/bash_history.archived-owes" ]]; then
                    _big=owed
                elif [[ -f "$_lane/bash_history" ]]; then
                    _big=$(PLAYBOOK_NO_BASHLOG=1 command find "$_lane/bash_history" -prune -size +52428800c 2>/dev/null) || _big=""
                fi
                if [[ -n "$_big" ]]; then
                    _cpb_rotate "$_lane" || true
                fi
                ;;
            esac
            # The stamp comes from bash itself (task 176): no program is started
            # for a line. `date` on PATH can be a bash script — a logged shell
            # whose first line asks for a stamp (measured: without end) — and a
            # line cost one process. The same text in the same local time, a
            # `TZ` set in the shell followed. Where bash cannot (older than 4.2,
            # or a script that switched the builtin off) the `date` program is
            # asked after all, started with logging off.
            local _now=""
            if ! builtin printf -v _now '%(%Y-%m-%d %H:%M:%S)T' -1 2>/dev/null \
               || [[ "$_now" != [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]\ [0-9][0-9]:[0-9][0-9]:[0-9][0-9] ]]; then
                _now=$(PLAYBOOK_NO_BASHLOG=1 command date '+%Y-%m-%d %H:%M:%S' 2>/dev/null) || _now=""
            fi
            # PLAN S11 item 26 (task 176). `.agent/` comes with the project: a
            # `bash_history` that is a symbolic link was written THROUGH — the
            # user's commands went wherever a shipped link pointed, and a
            # dangling one made the file. Such a lane is not logged to. The
            # look is made HERE, right before the name is opened, and nowhere
            # earlier: a look made before `find` ran left that whole run
            # between it and the append (impl panel r1). A shell redirection
            # cannot open without following; one builtin test and the open
            # after it are as close as it gets. (The rotation refuses a linked
            # history by itself, in `_take`.)
            [[ ! -L "$_lane/bash_history" ]] || return 0
            { echo "$_now | AGENT | $_cmd" >> "$_lane/bash_history"; } 2>/dev/null || return 0
            if [[ -n "$_key" ]]; then
                _CPB_SEEN="${_CPB_SEEN:-}$_key"
                if [[ ${#_CPB_SEEN} -gt 65536 ]]; then
                    _CPB_SEEN="$_key"
                fi
            fi
            break
        fi
        _dir="${_dir%/*}"
        [[ -n "$_dir" ]] || _dir="/"
    done
    return 0
}
set -o history
trap '_cpb_log_cmd' DEBUG
