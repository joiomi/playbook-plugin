"""Before any row runs: what this checkout must have for the rows chosen (PLAN.md "From task 242" (a)).

Two rows read beyond the plugin tree. The install rows publish this checkout to a disposable "GitHub" by a bare clone
of it and clone that `-b main` (`rows_install.py`), so the checkout needs a local `main` branch. PB-CLI-INTENT checks the
owner's distillation record (`rows_judge.OWNER_DISTILLATION`: task 183's intent run under the workspace that holds this
repository, and its INTENT.md). Task 242 ran the gauntlet from a fresh clone and four rows failed halfway for these
reasons; now the gauntlet refuses such a checkout before anything runs, and says why.

Usage (gauntlet.sh): python3 -I preflight.py <ROW-ID> ...   — exit 0: fine; exit 2: refused, each reason on stderr.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import NESTED  # noqa: E402
import rows_judge  # noqa: E402

INSTALL_ROWS = ("PB-UPGRADE-INSTALLED-COPY", "PB-INSTALL-ROLLBACK")


def problems(rows: "list[str]") -> "list[str]":
    out = []
    install = [r for r in INSTALL_ROWS if r in rows]
    if install:
        has_main = subprocess.run(["git", "-C", str(NESTED), "rev-parse", "--verify", "--quiet", "refs/heads/main"],
                                  capture_output=True).returncode == 0
        if not has_main:
            out.append(f"{', '.join(install)}: this checkout ({NESTED}) has no local `main` branch — the install rows "
                       "clone `-b main` from a bare clone of it (rows_install.py); run the gauntlet from the nested "
                       "repository inside the workspace, or create `main` here")
    if "PB-CLI-INTENT" in rows:
        proj, num, rid = rows_judge.OWNER_DISTILLATION
        if not sorted(proj.glob(f".agent/tasks/{num}-*/intent/{rid}")) or not (proj / "INTENT.md").is_file():
            out.append(f"PB-CLI-INTENT: the owner's distillation record — task {num}'s intent run {rid} and INTENT.md "
                       f"under {proj} — is not there; the row checks that record, so run the gauntlet from the nested "
                       "repository inside the workspace")
    return out


if __name__ == "__main__":
    found = problems(sys.argv[1:])
    for p in found:
        print(f"REFUSED: {p}", file=sys.stderr)
    sys.exit(2 if found else 0)
