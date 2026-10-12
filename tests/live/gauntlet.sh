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
        *) echo "no live section for $id" >&2; false ;;
    esac
    rc=$?
    if [ $rc -eq 1 ] || [ $rc -eq 2 ]; then worst=1
    elif [ $rc -eq 3 ] && [ $worst -eq 0 ]; then worst=3
    fi
done
exit $worst
