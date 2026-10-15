# 001 - X

> **Light shape** — small, sub-day work; ceremony compressed, review NEVER waived: risk decides review, smallness never does. If this grows beyond small, convert: `tasks new <type> <name>`.
> Gate discipline unchanged: one gate → do work → check box with its outcome. Never backfill. Batch-closing ALREADY-DONE gates needs an outcome note per line (hook-enforced; where no hook runs, one at a time).
> **Never born checked:** write the gate BEFORE you do its step, then check it — a gate may never be born checked. To record work you already finished, add it unchecked, then check it in a SEPARATE edit with its outcome.
> **Selection rule:** `quick` is for declared-reversible trivia only; docs, claims, data, or publishing belong here or heavier.

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
(one line — what to do and what proves it worked)

## References
(optional context; recent chat lands here at activation)

---

## Risk Routing
- [ ] `## Risk` above set to exactly one word — reversible / irreversible / assertive — BEFORE any work, and this gate's outcome note states WHY in one line (what would `git revert` NOT undo? does this assert a claim about the world?). If assertive or irreversible: ceremony stays light but the FULL review bar applies — run the implementation panel once the work is built; the close gate enforces the evidence in judge.md. Light never waives review.

## Work
- [ ] Orient: name the files/claims this touches and what "verified" means (one paragraph)
- [ ] Do the work
- [ ] Verify: run the check that proves it — paste the result on this line

## Review
- [ ] Review routing honored — reversible: may proceed without review; assertive/irreversible: the implementation panel ran and its findings are triaged in judge.md (the close gate checks the evidence itself, not this box)

## Pre-review
- [ ] MIND_MAP.md: owning node updated in place (only if architecture or claims moved)

## Parked
(Findings or ideas that emerged during work but are out of scope. Describe each with enough context for a future task to pick it up.)

---
