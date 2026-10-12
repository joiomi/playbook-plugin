"""Run one live row by id: `python3 -I run_row.py <ROW-ID> <out-dir>` (called by gauntlet.sh).
Exit status: 0 PASS, 1 FAIL, 3 WAITING, 2 unknown row."""
from __future__ import annotations

import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rows_install  # noqa: E402
import rows_judge  # noqa: E402
import rows_platform  # noqa: E402
import rows_session  # noqa: E402
import rows_session2  # noqa: E402

ROWS = {}
for module in (rows_platform, rows_install, rows_session, rows_session2, rows_judge):
    ROWS.update(module.ROWS)


def _exit_on(signum, _frame):
    # SIGTERM/SIGHUP become an ordinary exit, so every `finally` runs: a step's process tree and a detached
    # session are ended before the row's scratch directories go (impl panel round 2, T14)
    raise SystemExit(128 + signum)


def main(argv: "list[str]") -> int:
    if len(argv) != 2 or argv[0] not in ROWS:
        print(f"usage: run_row.py <ROW-ID> <out-dir>; rows: {', '.join(sorted(ROWS))}", file=sys.stderr)
        return 2
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, _exit_on)
    return ROWS[argv[0]](Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
