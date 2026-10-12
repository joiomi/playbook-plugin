#!/usr/bin/env python3
"""Every-target flip rule for the live rows (task 184, plan panel P3).

    python3 tests/live/check_flips.py <artifacts-dir> [--ledger docs/guarantee-ledger.json]

A ledger row whose `required_live_evidence` names targets may be `verified_by_current_executable_evidence`
only when ALL of these hold:
  * it carries a live proof bound to `tests/live/gauntlet.sh` as `scenario:<ROW-ID>`;
  * `<artifacts-dir>/<ROW-ID>.txt` exists and its last verdict line is `ROW <ROW-ID>: PASS`;
  * the artifact's `targets:` line names every target the row's `required_live_evidence` names;
  * the artifact holds no `WAITING:` line (a target that could not run is not a target that passed);
  * the artifact was made on a clean candidate (its `candidate (nested HEAD):` line says `clean`).

It also reports, for information, the rows that are NOT verified and what their artifact says, so a
row left partial while its artifact passed every target is visible. Exit 0 = the rule holds for every
verified row; 1 = a row is verified without every target passing; 2 = usage."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

GAUNTLET = "tests/live/gauntlet.sh"
VERIFIED = "verified_by_current_executable_evidence"


def artifact_facts(path: Path, row: str) -> dict:
    if not path.is_file():
        return {"exists": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    verdicts = re.findall(rf"^ROW {re.escape(row)}: (PASS|FAIL|WAITING)\b", text, re.M)
    targets = re.search(r"^targets: (.*)$", text, re.M)
    cand = re.search(r"^candidate \(nested HEAD\): (\S+) \((clean|DIRTY working tree)\)", text, re.M)
    return {"exists": True, "verdict": verdicts[-1] if verdicts else None,
            "targets": {t.strip() for t in targets.group(1).split(",")} if targets else set(),
            "waiting": re.findall(r"^WAITING: (.*)$", text, re.M), "candidate": cand.group(1) if cand else None,
            "clean": bool(cand) and cand.group(2) == "clean"}


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="the every-target flip rule for the live rows")
    ap.add_argument("artifacts")
    ap.add_argument("--ledger", default=str(Path(__file__).resolve().parents[2] / "docs" / "guarantee-ledger.json"))
    a = ap.parse_args(argv)
    art = Path(a.artifacts)
    if not art.is_dir():
        print(f"not a directory: {art}", file=sys.stderr)
        return 2
    rows = json.loads(Path(a.ledger).read_text(encoding="utf-8"))["guarantees"]
    bad = 0
    for r in rows:
        live = r.get("required_live_evidence") or []
        if not live:
            continue
        rid = r["id"]
        need = {t for e in live for t in e["targets"]}
        bound = any(p.get("path") == GAUNTLET and p.get("reference") == f"scenario:{rid}"
                    and p.get("type") in ("live-provider test", "live-platform test") for p in r["proofs"])
        f = artifact_facts(art / f"{rid}.txt", rid)
        passed_all = (f.get("exists") and f.get("verdict") == "PASS" and need <= f.get("targets", set())
                      and not f.get("waiting") and f.get("clean"))
        why = ("no artifact" if not f.get("exists") else
               f"verdict {f.get('verdict')}, targets {sorted(f.get('targets', set()))} of {sorted(need)}"
               + (f", waiting: {f['waiting']}" if f.get("waiting") else "")
               + f", candidate {f.get('candidate')} ({'clean' if f.get('clean') else 'NOT a clean tree'})")
        if r["status"] == VERIFIED:
            ok = bound and passed_all
            bad += not ok
            print(f"{'OK  ' if ok else 'FAIL'} {rid} verified — live proof bound: {bound}; {why}")
        else:
            print(f"     {rid} {r['status']} — live proof bound: {bound}; {why}")
    print(f"{'the every-target rule holds' if not bad else f'{bad} verified row(s) without every target passing'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
