# 001 - X

> **Gate discipline:** One gate → do work → check box → next gate.
> Never backfill. The document IS the execution trace. Closing several ALREADY-DONE gates in one edit is allowed ONLY when each line carries its own outcome note (hook-enforced, max 5; a bare batch tick is blocked; where no hook runs, keep to one gate at a time).
> **Never born checked:** write each gate BEFORE you do its step, then check it — a gate may never be born checked. To record work you already finished, add the gate unchecked, then check it in a SEPARATE edit with its outcome note.
> **Closing a gate:** check the box, append your outcome. Never replace the original text.
> **Sanctioned compaction (the one exception):** when this file grows past the review context budget, old *Plan Review / Implementation Review triage narrative* may move VERBATIM to `task-archive.md` (same dir). Never gates, never Intent/Design/Parked/receipts — moving history is not deleting it. Do it mechanically: wrap each cold block in `<!-- archive:start -->` … `<!-- archive:end -->` then run `.claude/bin/tasks compact <N>` (it refuses to move a gate, a pin, or a protected heading, so a mismark fails loud).
> Design Phase = orientation (one gate, brief answer). Work Plan = real work (one gate, full effort).
> If you see the same gate 5+ times in the hook echo, you're drifting — STOP and update.

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
(what we want to achieve — the outcome, not the activity)

## Why
(why this matters now — urgency, context, what breaks if delayed)

## References
- [ ] Context: `.claude/bin/tasks recall keyword1 keyword2` (locates matching nodes across MIND_MAP.md + overflow), then `tasks recall <N>` for each → paste relevant excerpts below
- Playbook: playbook/Investigate
- Note: Don't hardcode task numbers in plans — `.claude/bin/tasks new` auto-increments.

---

## Design Phase

### Chat Log Research
- [ ] Review the "Recent Chat" messages captured in References (auto-injected at `tasks work`). Remove unrelated ones. Pull key user quotes, constraints, and context into Intent/Why above. The user's actual words are the ground truth for Intent.

### Investigation Orientation
- [ ] What's the question or hypothesis? State it before looking.
- [ ] What evidence would change your mind?
- [ ] When do you stop? (convergence criteria: N rounds with no new position, or specific answer found)
- [ ] Test strategy: if findings lead to code changes, point tests or also property tests (`hypothesis`) for invariants?

## Work Plan

> Rounds: hypothesis → test → result → checkpoint. Stop when converging.

### Round 1: [focus]
- **Hypothesis:** (before testing)
- **Test:** (what to check)
- **Result:** (what happened)
- [ ] Checkpoint: converging or scattering? New hypothesis needed?

### Round 2: [focus]
- **Hypothesis:** (refined from Round 1)
- **Test:** (what to check)
- **Result:** (what happened)
- [ ] Checkpoint: converging or scattering?

### Synthesis
- [ ] What did you learn? Key findings with evidence.
- [ ] What remains unknown? What would a follow-up task investigate?

---

## Debrief
- [ ] Freehand — work is done, stay for discussion with user. Remove this gate during Design Phase if running headless or task doesn't need debrief.

## Pre-review
- [ ] All tests pass
- [ ] No debug artifacts
- [ ] MIND_MAP.md: update the OWNING subsystem node **in place** — a NEW node only for a genuinely new subsystem, never one node per task

## Parked
(Findings or ideas that emerged during work but are out of scope. Describe each with enough context for a future task to pick it up.)

---

## Standing Orders
- **Expand dynamically**: When you discover something you'll need to do, write new gates immediately — don't wait until you get there.
- **Steer openly**: If your direction changes, edit your open (unchecked) gates to reflect reality. The plan is alive, not a contract.
- **Never defer awareness**: The moment you realize work exists, capture it. Forgetting is the failure mode, not having too many gates.
