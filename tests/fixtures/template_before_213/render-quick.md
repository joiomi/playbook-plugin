# 001 - X

> **Gate discipline:** One gate → do work → check box → next gate.
> Never backfill. The document IS the execution trace. Batch-closing ALREADY-DONE gates needs an outcome note per line (hook-enforced; where no hook runs, one gate at a time).
> **Never born checked:** write the gate BEFORE you do its step, then check it — a gate may never be born checked. To record work you already finished, add it unchecked, then check it in a SEPARATE edit with its outcome.
> **Selection rule:** quick is for declared-reversible trivia only — anything touching docs, claims, data, or publishing belongs in `light` or heavier (risk decides review; smallness never does).

## Status
pending

> **Before filling this in:** run `.claude/bin/tasks work <N>` to activate this task. Hooks won't enforce until activated.

## Risk
unclassified

> **Set this at the Structure gate** to one of: `reversible` / `irreversible` / `assertive`.
> - `reversible` — the question is whether the WORLD reverts, not the diff: touches only code/config that `git revert` fully undoes. A change that deletes/migrates persisted data, alters an on-disk format, rotates a secret, rewrites history, or publishes/asserts a claim about the world is NOT reversible even when its diff reverts cleanly. Normal bar.
> - `irreversible` — deletes/migrates data, rotates a secret, rewrites history, or publishes. Needs a named rollback plan + explicit confirmation, and cannot light-close.
> - `assertive` — changes a **claim about the world** (docs, a calibration, a measurement, a "verified accurate"). Reviewed for the claim AND its instrument regardless of diff size — a docs-only diff can be the most review-worthy thing a task produces. Cannot light-close.
> - Leaving it `unclassified` is **not** the cheap way out: an unset gate cannot be evaluated, so the close is held to the same bar as `assertive`/`irreversible` (review evidence, or `--force --reason`). One word here is the whole cost.

## Intent
(one line — what to do and how to verify)

---

## Work
- [ ] Do the work
- [ ] Test: verify it worked
- [ ] Cleanup: mind map, commit

## Parked
(out of scope discoveries)
