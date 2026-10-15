# {num:03d} - {title}

> How to fill this in — gate discipline, what each `## Risk` value means: `.claude/bin/tasks guide <type>` (printed when the task was created).

## Status
pending

## Intent
(what we want to achieve — the outcome, not the activity)

## Why
(why this matters now — urgency, context, what breaks if delayed)

## References
- [ ] Context: `grep -Ein "keyword1|keyword2" MIND_MAP.md` → paste relevant excerpts below
- Origin: Mxxx
- Playbook: {playbook}
- Note: Don't hardcode task numbers in plans — `tasks new` auto-increments.

---

## Design Phase

### Understand
- [ ] Restate the request in my own words. What does the user actually want?
- [ ] Critique: Am I solving the stated problem or a different one I find more interesting?
- [ ] What would "done" look like? How will we know the task succeeded?
- [ ] What is OUT of scope for this task?

### Structure
- [ ] What kind of work is this? (build / investigate / evaluate / decide / combination?) If combination, what's the sequence? If >15 gates or uncertain approach, where do you stop and validate direction?

### Reflection Gates
- [ ] Wrote task-specific check questions (Bad: "is this working?" Good: "Does the output include the progress counter?" — the answer should require evidence, not just yes/no)
- [ ] Before the riskiest step: what would make you stop and reconsider?
- [ ] If judging quality before building: is the gap worth closing?

### Verify
- [ ] Review the work plan. If a likely growth point exists, add it to the plan now.
- [ ] Does the work plan include moments where you stop and question your approach — not just execute?
- [ ] Checkpoint: Would a fresh agent understand this task and execute it well?
- [ ] The work plan below has the right granularity (not too coarse, not micro-steps)

## Plan Review
- [ ] Run `tasks plan-review <N>` — **launch it in the background** (a panel or large review can exceed the 600 s foreground tool-call cap and be killed mid-run; the CLI prints an advisory when it might) and poll; wait for it to finish (it writes the judge's findings into this file; the judge itself is sandboxed read-only and will NOT touch your gates). Re-read this file to see its findings below, then address valid concerns by revising Work Plan gates yourself. **Justify lens:** does every work gate trace up to something in Intent/Design? Are there gates that justify nothing above them (scope creep)? Intent claims with no gate to satisfy them (gaps)?
- [ ] **Triage plan-review findings: judge = opinion, not gospel.** For each finding, document accept (with rationale) / park (with rationale) / reject (with rationale). Push back where you have concrete evidence — you live with the outcomes, the reviewer doesn't. Verify file:line claims before applying — single-judge reviews can cite wrong locations.

(plan review findings appear here)

---

## Work Plan

(write work gates here)

---

## Implementation Review
- [ ] Run `tasks impl-review <N>` — **launch it in the background** (a panel or large review can exceed the 600 s foreground tool-call cap and be killed mid-run; the CLI prints an advisory when it might) and poll; wait for it to finish (it writes the judge's findings into this file; the judge itself is sandboxed read-only). Re-read findings. **Satisfy lens:** does every Intent claim trace down through code to tests? Where does the chain break?
- [ ] **Triage impl-review findings: judge = opinion, not gospel.** For each finding, document accept (with rationale) / park (with rationale) / reject (with rationale). Push back where you have concrete evidence — you live with the outcomes, the reviewer doesn't. Verify file:line claims before applying — single-judge reviews can cite wrong locations.

(implementation review findings appear here)

---

## Pre-review
- [ ] All tests pass
- [ ] No debug artifacts
- [ ] MIND_MAP.md: update the OWNING subsystem node **in place** — a NEW node only for a genuinely new subsystem, never one node per task

## Parked
(Findings or ideas that emerged during work but are out of scope. Describe each with enough context for a future task to pick it up.)

---
