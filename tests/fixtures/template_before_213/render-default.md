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
- Playbook: (none)
- Note: Don't hardcode task numbers in plans — `.claude/bin/tasks new` auto-increments.

---

## Design Phase

> **Write a 1-sentence answer for each gate.** A bare checkmark means you skipped it.
> Complete these gates before writing the work plan.
> (The `/playbook` skill has workflow patterns if you need a reference.)

### Chat Log Research
- [ ] Review the "Recent Chat" messages captured in References (auto-injected at `tasks work`). Remove unrelated ones. Pull key user quotes, constraints, and context into Intent/Why above. The user's actual words are the ground truth for Intent.

### Understand
- [ ] Restate the request in my own words. What does the user actually want?
- [ ] Critique: Am I solving the stated problem or a different one I find more interesting?
- [ ] What would "done" look like? How will we know the task succeeded?
- [ ] What are you assuming about the existing code/architecture that you haven't verified?
- [ ] What is OUT of scope for this task?

### Structure
- [ ] What kind of work is this? (build / investigate / evaluate / decide / combination?) If combination, what's the sequence? If >15 gates or uncertain approach, pick a checkpoint where you pause and reassess direction before continuing.
- [ ] **Set `## Risk`** (reversible / irreversible / assertive). Ask: if this claim/change were WRONG, what would show it? An `assertive` task (changes a claim about the world — docs, a calibration, a measurement) must name the instrument that would reveal the claim false. An `irreversible` task must name its rollback plan. These cannot be light-closed for being small.

### Reflection Gates
- [ ] Wrote task-specific check questions (Bad: "is this working?" Good: "Does the output include the progress counter?" — the answer should require evidence, not just yes/no)
- [ ] Test strategy: what are you testing and how? (point tests for specific behavior, property tests via `hypothesis` for invariants on transformations/parsers/arithmetic)
- [ ] Before the riskiest step: what would make you stop and reconsider?
- [ ] If judging quality before building: is the gap worth closing?

### Verify
- [ ] Review the work plan. If a likely growth point exists, add it to the plan now.
- [ ] Does the work plan include moments where you stop and question your approach — not just execute?
- [ ] Checkpoint: Would a fresh agent understand this task and execute it well?
- [ ] The work plan below has the right granularity (not too coarse, not micro-steps)

## Plan Review
- [ ] Run `.claude/bin/tasks panel-review <N>` — ALL available judges review the plan in parallel (blind: they see the repo, not your conversation). Wait for it to finish; findings land in `judge.md` (this dir), led by a PANEL VERDICT. Read judge.md, then address valid concerns by revising Work Plan gates yourself. **Justify lens:** does every work gate trace up to something in Intent/Design? Are there gates that justify nothing above them (scope creep)? Intent claims with no gate to satisfy them (gaps)? *(Fallback if no panel can run: `.claude/bin/tasks plan-review <N>` — single judge, writes into this file.)*
- [ ] **Triage panel findings into this file: judges = opinion, not gospel.** For each finding, document accept (with rationale) / park (with rationale) / reject (with rationale) here under this gate. Push back where you have concrete evidence — you live with the outcomes, the reviewers don't. Verify file:line claims before applying. Where judges DISAGREE with each other, say which you believed and why — disagreement between models is signal, not noise.

(plan review triage appears here)

---

## Work Plan

> For each work section: what could go wrong? How will you know it worked? (specific check, not "looks good")
> Standard feature: 6-8 work gates + tests. Large tasks work fine — if >15 gates, add a mid-point checkpoint to reassess direction.

(write work gates here)

---

## Implementation Review
- [ ] Run `.claude/bin/tasks panel-review <N> --mode impl` — ALL available judges review the implementation in parallel. Wait for it to finish; findings land in `judge.md`, led by a PANEL VERDICT (a quorum-PASS impl panel is what the close gate accepts as evidence when `panel_required_for` is set). Read judge.md. **Satisfy lens:** does every Intent claim trace down through code to tests? Where does the chain break? *(Fallback if no panel can run: `.claude/bin/tasks impl-review <N>` — single judge, writes into this file.)*
- [ ] **Triage panel findings into this file: judges = opinion, not gospel.** For each finding, document accept (with rationale) / park (with rationale) / reject (with rationale) here under this gate. Push back where you have concrete evidence — you live with the outcomes, the reviewers don't. Verify file:line claims before applying. Where judges DISAGREE, say which you believed and why.

(implementation review triage appears here)

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
