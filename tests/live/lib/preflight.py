"""Before any row runs: what this checkout must have for the rows chosen (PLAN.md "From task 242" (a)).

Two rows read beyond the plugin tree. The install rows publish this checkout to a disposable "GitHub" by a bare clone
of it and clone that `-b main` (`rows_install.py`), so the checkout needs a local `main` branch. PB-CLI-INTENT checks the
owner's distillation record (`rows_judge.OWNER_DISTILLATION`: task 183's intent run under the workspace that holds this
repository, and its INTENT.md). Task 242 ran the gauntlet from a fresh clone and three rows failed halfway for these
reasons (a fourth that run, PB-PAYLOAD-NORMALIZE, failed for another: its session wrote with `printf`); now the
gauntlet refuses such a checkout before anything runs, and says why. The install rows also reset the release clone to
`rows_install.OLD_REF` (v1.6.0), so that commit must be in the checkout (a shallow clone may lack it).

Usage (gauntlet.sh): python3 -I preflight.py <ROW-ID> ...   — exit 0: fine; exit 2: refused, each reason on stderr.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import NESTED  # noqa: E402
import rows_install  # noqa: E402
import rows_judge  # noqa: E402

INSTALL_ROWS = ("PB-UPGRADE-INSTALLED-COPY", "PB-INSTALL-ROLLBACK")


def problems(rows: "list[str]", nested: "Path | None" = None, record=None, old_ref: "str | None" = None) -> "list[str]":
    """The reasons this checkout cannot run `rows` (none: it can). `nested`, `record` and `old_ref` default to the
    harness's own (`common.NESTED`, `rows_judge.OWNER_DISTILLATION`, `rows_install.OLD_REF`); selfcheck passes
    scratch ones."""
    nested = Path(nested) if nested is not None else NESTED
    old_ref = old_ref or rows_install.OLD_REF
    out = []
    install = [r for r in INSTALL_ROWS if r in rows]
    if install:
        def git_ok(*args):
            return subprocess.run(["git", "-C", str(nested), *args], capture_output=True).returncode == 0
        if not git_ok("rev-parse", "--verify", "--quiet", "refs/heads/main"):
            out.append(f"{', '.join(install)}: this checkout ({nested}) has no local `main` branch — the install rows "
                       "clone `-b main` from a bare clone of it (rows_install.py); run the gauntlet from the nested "
                       "repository inside the workspace, or create `main` here")
        # in the checkout, on a ref or not: both clones of the install rows are from a local path, which copies the
        # object store, so a commit present here reaches the release clone (measured on git 2.54, task 248 — task
        # 245's check also asked for a local branch or tag and refused a checkout that would have run)
        if not git_ok("cat-file", "-e", f"{old_ref}^{{commit}}"):
            out.append(f"{', '.join(install)}: this checkout ({nested}) does not hold commit {old_ref} (v1.6.0) — "
                       "the install rows reset the release clone to it; a shallow clone may lack it: fetch the history")
    if "PB-CLI-INTENT" in rows:
        proj, num, rid = record if record is not None else rows_judge.OWNER_DISTILLATION
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
