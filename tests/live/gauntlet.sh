#!/usr/bin/env bash
# The live gauntlet of PLAN S11 (task 184): each ledger row that asks for a LIVE run
# has one section below, `=== <ROW-ID>:`, which is what the ledger binds as
# `scenario:<ROW-ID>` of this file. A section runs the row's real scenario on the
# CANDIDATE plugin tree (this checkout) and writes `<out>/<ROW-ID>.txt`.
#
# NEVER run by CI or `scripts/verify`: it starts real agent sessions and judges
# (they spend the owner's quota), containers and a disposable install. Run by hand:
#
#   tests/live/gauntlet.sh --out <dir> [ROW-ID ...]     # no ROW-ID: every row
#   tests/live/gauntlet.sh --list
#
# Exit status: 0 every selected row PASS; 1 a row FAILED; 3 none failed, a row is
# WAITING for a target that cannot run now (grok's credit, the owner's own step).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
OUT=""
ROWS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        --list) grep -oE '^ +echo "=== PB-[A-Z0-9-]+:' "${BASH_SOURCE[0]}" | sed 's/.*=== //; s/:$//'; exit 0 ;;
        -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
        PB-*) ROWS+=("$1"); shift ;;
        *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
    esac
done
[ -n "$OUT" ] || { echo "--out <dir> is required" >&2; exit 2; }
mkdir -p "$OUT"
if [ ${#ROWS[@]} -eq 0 ]; then
    mapfile -t ROWS < <("${BASH_SOURCE[0]}" --list)
fi

row() { python3 -I "$HERE/lib/run_row.py" "$1" "$OUT"; }

worst=0
for id in "${ROWS[@]}"; do
    case "$id" in
        PB-PYTHON-FLOOR)
            echo "=== PB-PYTHON-FLOOR: Python 3.10 and the newest image compile and run the CLI; 3.9 is refused before import"
            row "$id" ;;
        PB-RUNTIME-STDLIB)
            echo "=== PB-RUNTIME-STDLIB: on interpreters with site-packages emptied the CLI round trip runs; every shipped file is text"
            row "$id" ;;
        PB-SANDBOX-DETECTION-FALLBACK)
            echo "=== PB-SANDBOX-DETECTION-FALLBACK: bubblewrap present, absent, relative, unable to start, nested; the judge path's fallback"
            row "$id" ;;
        PB-SANDBOX-READ-NETWORK)
            echo "=== PB-SANDBOX-READ-NETWORK: a real judge run reads outside the project and the network, cannot write it, cannot read the records"
            row "$id" ;;
        PB-UPGRADE-INSTALLED-COPY)
            echo "=== PB-UPGRADE-INSTALLED-COPY: a disposable v1.6.0 install upgraded to the candidate by the real plugin manager; init re-run from the manifest's copy"
            row "$id" ;;
        PB-INSTALL-ROLLBACK)
            echo "=== PB-INSTALL-ROLLBACK: the upgraded copy's init interrupted three controlled ways; everything restored; a re-run recovers"
            row "$id" ;;
        PB-TASK-ACTIVATE)
            echo "=== PB-TASK-ACTIVATE: a real session: no task refused, a pending task allowed, a blocked task refused with its resume"
            row "$id" ;;
        PB-PROVIDER-HOOK-INVOCATION)
            echo "=== PB-PROVIDER-HOOK-INVOCATION: a real session: an allowed edit, a destructive command blocked, the prompt attributed, the echo, the stop"
            row "$id" ;;
        PB-HOOK-STATE-ECHO)
            echo "=== PB-HOOK-STATE-ECHO: a real session: the echo after each tool event, two parallel calls, a ticked gate"
            row "$id" ;;
        PB-HOOK-STOP-CLOSE)
            echo "=== PB-HOOK-STOP-CLOSE: real sessions: the first stop blocked with gates open, the valve, a complete task let through"
            row "$id" ;;
        PB-TASK-FREEHAND)
            echo "=== PB-TASK-FREEHAND: real sessions: freehand released, its log captured, a tracked task re-arms the gates"
            row "$id" ;;
        PB-LANE-RESOLUTION)
            echo "=== PB-LANE-RESOLUTION: real sessions: a two-lane repository stays in its lane; a fresh clone with no marker invents no root lane"
            row "$id" ;;
        PB-PAYLOAD-NORMALIZE)
            echo "=== PB-PAYLOAD-NORMALIZE: real hook payloads captured in a session and fed to the normalizer: native bytes unchanged, fields its own; a foreign dialect rewritten"
            row "$id" ;;
        PB-PROVIDER-CAPABILITY-DEGRADE)
            echo "=== PB-PROVIDER-CAPABILITY-DEGRADE: claude's advertised capability cells observed in a real session; the model report and live probes against the real CLIs"
            row "$id" ;;
        PB-SLASH-COMMANDS)
            echo "=== PB-SLASH-COMMANDS: every shipped command discovered and dispatched in a real session (tools off)"
            row "$id" ;;
        PB-MERGE-PUSH-GATE)
            echo "=== PB-MERGE-PUSH-GATE: /playbook:merge --push on a disposable two-branch repository: pushed only after the four gates and GREEN; a failing verify pushes nothing"
            row "$id" ;;
        PB-JUDGE-BLIND-CONTEXT)
            echo "=== PB-JUDGE-BLIND-CONTEXT: real claude and codex judges: a canary only in the conversation never reaches a task review; the task-less question carries it; --bare carries nothing"
            row "$id" ;;
        PB-JUDGE-READONLY)
            echo "=== PB-JUDGE-READONLY: real claude and codex judges, single and panel: launched read-only, a write from where each starts is denied"
            row "$id" ;;
        PB-PANEL-QUORUM)
            echo "=== PB-PANEL-QUORUM: a controlled mixed-verdict panel (two real seats answer, one fails on purpose) under five quorum settings"
            row "$id" ;;
        PB-MODEL-AVAILABILITY)
            echo "=== PB-MODEL-AVAILABILITY: pins against the real CLIs: supported, missing, entitlement; a removed pin stops a review; a timeout and a budget stop never read as removal"
            row "$id" ;;
        PB-REVIEW-TRIAGE)
            echo "=== PB-REVIEW-TRIAGE: real single judges asked for findings only: no edit instruction sent, task.md untouched"
            row "$id" ;;
        PB-CLI-INTENT)
            echo "=== PB-CLI-INTENT: tasks intent on a finished task: no spend without the exact approval; approved, blind extractions on a real claude judge"
            row "$id" ;;
        *) echo "no live section for $id" >&2; false ;;
    esac
    rc=$?
    if [ $rc -eq 1 ] || [ $rc -eq 2 ]; then worst=1
    elif [ $rc -eq 3 ] && [ $worst -eq 0 ]; then worst=3
    fi
done
exit $worst
