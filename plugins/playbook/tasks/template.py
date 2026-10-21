"""Composable template components for task.md files.

Each methodology clause is a function returning a markdown string.
Templates are rendered by composing components in order.

Usage:
    from tasks.template import render_template
    content = render_template(num=1, title="My Task", task_type="feature")
"""
from __future__ import annotations

from tasks.core import PLAYBOOKS


# ---------------------------------------------------------------------------
# Components — each returns a markdown string
# ---------------------------------------------------------------------------

def header(num: int, title: str) -> str:
    return f"# {num:03d} - {title}"


def pointer(task_type: "str | None" = None) -> str:
    """The one line a task.md keeps of the instructions: where the single copy is (task 213, the owner's
    "Scris o dată, nu în fiecare fișă")."""
    # task 223 (impl round 1, agy): "what each gate asks" only where the guide has a Gates section — quick has none
    # (named, not computed: the notes are read off a render, which carries this pointer; a test ties the two)
    asks = "" if task_type == "quick" else ", what each gate asks"
    return (f"> How to fill this in — gate discipline, what each `## Risk` value means{asks}: "
            f"`.claude/bin/tasks guide {task_type or 'feature'}` (printed when the task was created).")


def sticker() -> str:
    """The full types' gate discipline — in the guide only (task 213)."""
    return """\
> **Gate discipline:**
> - One gate → do the work → check the box → the next gate.
> - Never backfill: the document IS the execution trace.
> - Never born checked: write each gate BEFORE you do its step, then check it.
> - Work you already finished: add its gate unchecked, then check it in a SEPARATE edit, with its outcome.
> - Closing a gate: check the box and, in the same edit, write its outcome on the lines under it — indented two spaces deeper than the gate, one fact per line (`- [x] <the gate>`, then `  - 283 tests green`).
> - Never replace a gate's original text.
> - Closing several ALREADY-DONE gates in one edit: only when each gate carries its own outcome note (hook-enforced, at most 5; a bare batch tick is blocked; where no hook runs, one gate at a time).
> - Design Phase = orientation (one gate, a brief answer); Work Plan = real work (one gate, full effort).
> - If the hook's reminder (one line every 10 calls on the same gate) names the same gate a third time, you're drifting — STOP and update.
> **Sanctioned compaction (the one exception):**
> - When this file grows past the review context budget, old Plan Review / Implementation Review triage narrative may move VERBATIM to `task-archive.md` (same dir).
> - Never gates, never Intent/Design/Parked/receipts — moving history is not deleting it.
> - Do it mechanically: wrap each cold block in `<!-- archive:start -->` … `<!-- archive:end -->`, then run `.claude/bin/tasks compact <N>`.
> - It refuses to move a gate, a pin or a protected heading, so a mismark fails loud."""


def status() -> str:
    return "## Status\npending\n\n" + risk()


def status_note() -> str:
    """What `## Status` used to explain in every task.md — in the guide only (task 213)."""
    return ("> **Before filling this in:**\n"
            "> - Run `.claude/bin/tasks work <N>` to activate this task.\n"
            "> - Hooks won't enforce until it is activated.")


def risk() -> str:
    """The `## Risk` field with its explanation. Its own function so a generator
    that writes its own `## Status` (the retro scaffold — task 080, S1c) still
    offers the field: without the heading, `has_risk_section` reads the record
    as pre-1.5.0 and the close takes the lenient legacy path. Task 213: the value and one pointer line; what
    each class means is in the guide (`risk_explanation`) and in CLAUDE.md's Correctness Contract."""
    return """\
## Risk
unclassified

> One word: `reversible` / `irreversible` / `assertive` — what each means: `.claude/bin/tasks guide` (Risk), or CLAUDE.md's "Correctness Contract"."""


def risk_explanation() -> str:
    """The `## Risk` explanation every task.md used to carry — in the guide only (task 213)."""
    return """\
> **Set this at the Structure gate** to one of: `reversible` / `irreversible` / `assertive`.
> - `reversible`: the question is whether the WORLD reverts, not the diff.
>   - It touches only code/config that `git revert` fully undoes; the normal bar.
>   - NOT reversible even when its diff reverts cleanly: deleting or migrating persisted data, changing an on-disk format, rotating a secret, rewriting history, publishing or asserting a claim about the world.
> - `irreversible`: deletes/migrates data, rotates a secret, rewrites history, or publishes.
>   - It needs a named rollback plan and explicit confirmation, and cannot light-close.
> - `assertive`: changes a **claim about the world** (docs, a calibration, a measurement, a "verified accurate").
>   - It is reviewed for the claim AND its instrument whatever the diff's size — a docs-only diff can be the most review-worthy thing a task produces.
>   - It cannot light-close.
> - Leaving it `unclassified` is **not** the cheap way out: an unset gate cannot be evaluated.
>   - The close is then held to the same bar as `assertive`/`irreversible` (review evidence, or `--force --reason`); one word here is the whole cost."""


def intent_why_refs(playbook: str) -> str:
    return f"""\
## Intent
(what we want to achieve \u2014 the outcome, not the activity)

## Why
(why this matters now \u2014 urgency, context, what breaks if delayed)

## References
- [ ] Context: run `.claude/bin/tasks recall <words>`, then paste the relevant excerpts of each node below.
- Playbook: {playbook}
- Note: Don't hardcode task numbers in plans \u2014 `.claude/bin/tasks new` auto-increments.

---"""


def design_phase_intro() -> str:
    return "## Design Phase"


def design_phase_note() -> str:
    """The Design Phase's instructions — in the guide only (task 213)."""
    return """\
> - **Answer each gate on the lines under it** (indented two spaces deeper, one fact per line).
> - A bare checkmark means you skipped it.
> - Complete these gates before writing the work plan.
> - The `/playbook` skill has workflow patterns if you need a reference."""


def chat_log_research() -> str:
    return """\
### Chat Log Research
- [ ] Keep the Recent Chat messages that matter and carry the user's own words into Intent and Why."""


def understand() -> str:
    return """\
### Understand
- [ ] Restate in your own words what the user actually wants.
- [ ] Critique: Am I solving the stated problem or a different one I find more interesting?
- [ ] What does "done" look like, and how will we know the task succeeded?
- [ ] What are you assuming about the existing code/architecture that you haven't verified?
- [ ] What is OUT of scope for this task?"""


def structure() -> str:
    return """\
### Structure
- [ ] Say what kind of work this is and, if it is long or uncertain, where you will stop to reassess.
- [ ] Set `## Risk` and name what would show the change wrong (assertive: its instrument; irreversible: its rollback)."""


def reflection_gates() -> str:
    return """\
### Reflection Gates
- [ ] Write task-specific check questions whose answers need evidence, not a yes or no.
- [ ] Test strategy: what you will test and how (point tests, property tests for invariants).
- [ ] Before the riskiest step: what would make you stop and reconsider?
- [ ] If judging quality before building: is the gap worth closing?"""



def verify() -> str:
    return """\
### Verify
- [ ] Review the work plan and add any likely growth point now.
- [ ] Does the work plan include moments where you stop and question your approach \u2014 not just execute?
- [ ] Checkpoint: Would a fresh agent understand this task and execute it well?
- [ ] The work plan below has the right granularity (not too coarse, not micro-steps)"""


def design_phase() -> str:
    """Compose all design phase subsections."""
    parts = [
        design_phase_intro(),
        chat_log_research(),
        understand(),
        structure(),
        reflection_gates(),
        verify(),
    ]
    return "\n\n".join(parts)


def judge_section() -> str:
    return """\
## Plan Review
- [ ] Run the plan panel in the background — `.claude/bin/tasks panel-review <N>` — then read `judge.md`.
- [ ] Triage the plan panel's findings here: accept, park or reject each one, with its reason.

(plan review triage appears here)

---"""


def work_plan() -> str:
    return """\
## Work Plan

(write work gates here)

---"""


def judge_impl_section() -> str:
    return """\
## Implementation Review
- [ ] Run the impl panel in the background — `.claude/bin/tasks panel-review <N> --mode impl` — then read `judge.md`.
- [ ] Triage the impl panel's findings here: accept, park or reject each one, with its reason.

(implementation review triage appears here)

---"""


def debrief() -> str:
    return """\
## Debrief
- [ ] Freehand — stay to discuss the work with the user; remove this gate at design time if headless or not needed."""


def pre_review() -> str:
    return """\
## Pre-review
- [ ] All tests pass
- [ ] No debug artifacts
- [ ] MIND_MAP.md: update the OWNING subsystem node in place, never one node per task."""


def parked() -> str:
    return """\
## Parked
(Findings or ideas that emerged during work but are out of scope. Describe each with enough context for a future task to pick it up.)

---"""


def _recent_chat_cut() -> int:
    """The capture's own number (`lifecycle.RECENT_CHAT_CUT`), so the prompt cannot
    describe a cut the capture does not make. Imported here, not at the top:
    `lifecycle` imports this module."""
    from tasks.lifecycle import RECENT_CHAT_CUT
    return RECENT_CHAT_CUT


def _intent_check(task_path: str) -> str:
    """Extract task number and return intent-check instruction for judge prompts."""
    import re as _re
    _tn = _re.search(r'/(\d{3})-', task_path)
    task_number = _tn.group(1) if _tn else None
    if task_number:
        return (
            # Task 125: judges cannot read the chat log any more (owner decision
            # 2026-10-02 — the judge sandbox masks it), so `tasks context` is
            # not offered; the user's own words reach a judge through the task
            # record: its Intent/Why and the Recent Chat captured at activation.
            "The task's `## Intent`, `## Why` and `### Recent Chat` (the user's messages as captured when "
            f"the task was activated — each whole up to {_recent_chat_cut():,} characters, a longer one cut "
            "there and marked with what was left out) carry the user's own words. "
            # task 233: an options dialog's entry holds the agent's question beside the user's answer
            "An entry that opens with `Options dialog` is an options dialog: its `A`/`Notes` lines are the user's "
            "answers (exact, as JSON string literals), its `Q` lines the agent's questions. "
            "Check whether the task addresses what the user actually asked for, not just the agent's interpretation. "
        )
    return ""


def judge_verify_clause(commands) -> str:
    """Optional judge-execution clause (L1): the PROJECT declares commands safe
    to run inside the judge's read-only sandbox (`judge_verify` in
    .agent/config.json — read-only-repo-safe AND parallel-safe, unique temp
    dirs). Empty when nothing is declared, so undeclared projects get exactly
    the pre-1.5.3 prompt. The rules exist to keep execution evidence honest:
    hypothesis-first kills vacuous 'it ran and passed'; reproduce-twice kills
    flake noise; no-timing kills parallel-contention garbage."""
    cmds = [c.strip() for c in (commands or [])
            if isinstance(c, str) and c.strip()]
    if not cmds:
        return ""
    listed = "; ".join(f"`{c}`" for c in cmds[:6])
    return (
        "EXECUTION (optional): the project declares these commands safe to run "
        f"in your read-only sandbox: {listed} (claude judges have no shell — Read, Glob and Grep only, task "
        "200 — so this does not apply to them). Use them only to check a SPECIFIC "
        "suspicion — never as routine (the close gate already runs the suite). "
        "Rules: state your predicted outcome BEFORE running — an unpredicted "
        "result is a lead to investigate, not evidence by itself; reproduce any "
        "failure a second time before reporting it; never use timing or "
        "benchmark output as evidence (judges run in parallel and contend for "
        "CPU); if a command fails for environmental reasons, say so plainly "
        "rather than reporting a code defect. "
    )


def _trim_clause(trim_notice: str) -> str:
    """One line telling a TRIMMED seat what it did not receive and where the
    full file lives — the judge has repo read access, so an elision is an
    instruction to go read, not a blind spot."""
    if not trim_notice:
        return ""
    return f"CONTEXT NOTE: {trim_notice} "


def _depth_budget_clause(soft_timeout_secs: "int | None") -> str:
    """The parenthetical inside the depth instruction.

    It has to agree with `time_budget_instruction`: with no soft deadline
    configured that function emits nothing, so telling the judge to reason
    "within the soft time budget" would point at a budget the prompt never
    states. Same instruction either way — reason until the claims are grounded,
    don't pad — but only the first form names a deadline.
    """
    if soft_timeout_secs is None:
        return (
            "reason until the claims are code-grounded; depth is how hard you "
            "think, not how much you write — do not pad"
        )
    return (
        "reason until the claims are code-grounded within the soft time budget; "
        "don't pad or exhaust the budget for its own sake — the soft deadline is "
        "the target, the hard kill is hang safety only"
    )


def soft_deadline_resume_instruction(soft_timeout_secs: int) -> str:
    """The message of a judge turn resumed after its soft deadline (task 261).

    agy has no clock, so `time_budget_instruction` alone did nothing for it: its turn is cut at the soft deadline
    (`--print-timeout`) and the same conversation resumed with this message. It still holds everything the turn
    read, so it can write its findings now. It must not start new work: the time left is what remains before the
    hard limit.
    """
    from tasks.core import human_duration

    return (
        f"TIME IS UP — the soft deadline (~{human_duration(soft_timeout_secs)}) has passed and your turn was "
        "stopped. Do NOT call any tool. Write your final review now, from everything you have already read and "
        "grounded, in exactly the output format the review instructions in my first message asked for. Say "
        "plainly which parts you did not get to check. A shorter fully-grounded report beats a longer "
        "incomplete one."
    )


def time_budget_instruction(
    soft_timeout_secs: "int | None" = None,
    hard_timeout_secs: "int | None" = None,
) -> str:
    """Prompt paragraph: soft wind-down + hard hang-safety.

    Soft = finish the current thought, then answer; do not open a new trail.
    Hard = process kill, only if truly stuck. Returns "" when no soft budget is
    configured — soft is the steering signal, so with no soft deadline the judge
    gets no time paragraph at all rather than a paragraph about a kill it should
    never be planning around.
    """
    if soft_timeout_secs is None:
        return ""
    from tasks.core import human_duration

    soft = human_duration(soft_timeout_secs)
    if hard_timeout_secs is None:
        hard_clause = (
            "There is no hard process kill configured — still honor the soft "
            "deadline so you do not burn unbounded tokens."
        )
    else:
        hard = human_duration(hard_timeout_secs)
        hard_clause = (
            f"A hard process kill exists only as hang safety at ~{hard} — "
            f"never plan to use it; it is not a target."
        )
    return (
        f"TIME BUDGET — soft deadline ~{soft}. {hard_clause} "
        "Self-regulate so you always produce a complete written findings response: "
        "(1) Prefer fewer deep, high-value probes over exhaustive exploration — "
        "do not pad reasoning for its own sake or open low-value side trails. "
        "(2) When you approach the soft deadline (or have used most of it): "
        "finish the SINGLE thought process you are currently in — the current "
        "hypothesis, file trail, or scenario only. Do not abandon mid-idea if "
        "finishing that one thought is what makes the finding sound. "
        "(3) After that current thought is finished, write your final findings "
        "from everything you have already grounded. "
        "(4) Do NOT start a new hypothesis, new file trail, or new investigation "
        "branch after the soft deadline. "
        "(5) A shorter fully-grounded report beats a longer incomplete one. "
    )


def post_d6_clauses(settled: bool = False, delta: bool = False) -> str:
    """Task 108 (post-D6 protocol): the severity marker every review prompt now
    requires (so `tasks.post_d6.parse_verdict` can count findings), plus the
    SETTLED and POST-PANEL DELTA rules when those context parts were delivered."""
    s = ("Start every finding with its severity in bold — `**Critical**` or `**Important**` (a Minor one: "
         "`**Minor**`) — optionally followed by ONE tag: `[SETTLED]`, `[SETTLED-CONTRADICTED]` or `[PRE-EXISTING]`. ")
    if settled:
        s += ("A SETTLED block (owner rulings + findings already rejected in triage) precedes "
              "the task context: read it first and follow its header — a ruling closes a design "
              "choice, never a correctness defect. ")
    if delta:
        s += ("This is a POST-PANEL review: its object is the POST-PANEL DELTA in your context "
              "(the newest impl panel's base → the current working tree). Every finding must cite "
              "a line the delta changed or a claim the delta changed; a problem in code the delta "
              "did not touch is tagged [PRE-EXISTING] — it is parked, not counted. "
              "Every finding's FIRST line also names its file in a fixed field, after the severity "
              "and the tag: `[file: <path>]` — the path copied exactly from the delta's DELTA FILES "
              "list, or, for a file the delta did not change, that file's path from the project root. "
              "A [PRE-EXISTING] finding is parked only when that field names a real file the delta "
              "did not change and the finding cites no file of the delta at a line (`<file>:<line>`); "
              "without the field it counts. ")
    return s


def plan_review_prompt(
    task_path: str,
    inline_context: bool = False,
    *,
    soft_timeout_secs: "int | None" = None,
    hard_timeout_secs: "int | None" = None,
    trim_notice: str = "",
    judge_verify=None,
    settled: bool = False,
    delta: bool = False,
) -> str:
    """Return the blind judge prompt for plan review (before implementation)."""
    context_location = "provided below" if inline_context else "provided in your system prompt"
    intent_check = _intent_check(task_path)
    time_budget = time_budget_instruction(soft_timeout_secs, hard_timeout_secs)
    depth_budget = _depth_budget_clause(soft_timeout_secs)

    return (
        "You are a senior engineer reviewing a PLAN — no code has been written yet. "
        f"The MIND_MAP.md and task.md are {context_location}. "
        "Read the source files referenced in the plan to understand existing patterns. "
        f"{intent_check}"
        f"{time_budget}"
        f"{_trim_clause(trim_notice)}"
        f"{judge_verify_clause(judge_verify)}"
        "Work the problem deeply before you write anything — spend substantial reasoning effort on the analysis, not on a long report "
        f"({depth_budget}). "
        "Where you have file access, read the relevant source and its callers/callees (don't judge from names alone) and trace, end-to-end, the data and control flow the plan would touch. "
        "Form several independent hypotheses about where this plan will fail or fall short, and for each, try to construct a concrete scenario that breaks it — keep the ones that hold up, discard the rest. "
        "Where you have file access, verify each claim against the code before committing to it; otherwise ground it in the inline task.md / mind-map context — do not treat lack of file access as a reason to drop everything. Drop only claims you cannot ground either way. "
        "All of that reasoning stays internal — your written output stays terse per the finding-format rule below. Depth of thinking, brevity of report. "
        "Then critique the plan through six lenses: "
        "(1) Intent alignment — will this approach actually fulfill the stated Intent? What's missing or underspecified? "
        "(2) Failure modes — what will go wrong that isn't addressed? Construct a concrete failing scenario. "
        "(3) Hostile sequences — for every state-changing flow the plan touches, walk: two concurrent requests (identical or conflicting); "
        "the same logical event delivered twice under DISTINCT ids; events arriving reordered; the external call succeeding while the local "
        "transaction rolls back; a crash after commit but before any post-commit step; a lost response causing the caller to retry. "
        "For each applicable sequence, name the invariant or mechanism the plan relies on AND the planned test that will prove it — "
        "an unaddressed applicable sequence is a finding. If the change touches no shared or persisted state, say so in one line and move on. "
        "(4) Test coverage — does the test plan cover the failure modes above? For pure-function code, does it identify invariants (idempotency, bounds, round-trip) worth property-testing? "
        "(5) Simplify — is anything over-engineered? What can be dropped? "
        "(6) Prove it — cite file:line evidence for claims about existing code. No hand-waving. "
        "Be specific and adversarial — your job is to find problems, not approve. "
        f"{post_d6_clauses(settled, delta)}"
        "Max 5 findings that count — Critical or Important. Below them you may add at most 3 Minor ones "
        "(a small issue that should not hold the close: wording, style, a cheap hardening), each starting "
        "with `**Minor**`; they are never counted and block nothing — do not raise a Minor to Important to "
        "be heard, nor lower a real one to Minor; the CAP line counts the Critical and Important ones, and "
        "`more remain` only when you dropped a Critical or Important one. "
        "Then, as your LAST line, report whether the cap bound you: "
        "`CAP: 5/5 reported, more remain` if you had to drop findings to fit, or "
        "`CAP: <k>/5 reported, exhausted` if you reported everything you found — "
        "so the reader can tell convergence (nothing left) from saturation (more remain). "
        "Each finding: cite file:line, 1-2 sentences stating the problem, 1 sentence stating the fix. No elaboration. "
        "DO NOT edit any files — you are sandboxed read-only and the attempt will "
        "fail. Output your findings to stdout only; the parent process writes them "
        "into the task's '## Plan Review' section, and the agent that owns the task "
        "triages them and revises its own Work Plan gates."
    )


def impl_review_prompt(
    task_path: str,
    inline_context: bool = False,
    *,
    soft_timeout_secs: "int | None" = None,
    hard_timeout_secs: "int | None" = None,
    trim_notice: str = "",
    judge_verify=None,
    settled: bool = False,
    delta: bool = False,
) -> str:
    """Return the blind judge prompt for implementation review (after code is written)."""
    context_location = "provided below" if inline_context else "provided in your system prompt"
    intent_check = _intent_check(task_path)
    time_budget = time_budget_instruction(soft_timeout_secs, hard_timeout_secs)
    depth_budget = _depth_budget_clause(soft_timeout_secs)

    return (
        "You are a senior engineer reviewing a COMPLETED implementation. "
        f"The MIND_MAP.md and task.md are {context_location}. "
        "Read the source files changed by this task (look at the Work Plan gates for paths). "
        f"{intent_check}"
        f"{time_budget}"
        f"{_trim_clause(trim_notice)}"
        f"{judge_verify_clause(judge_verify)}"
        "Work the problem deeply before you write anything — spend substantial reasoning effort on the analysis, not on a long report "
        f"({depth_budget}). "
        "Where you have file access, read the changed source and its callers/callees (don't judge from names alone) and trace the data and control flow end-to-end. "
        "Form several independent hypotheses about how this code could be wrong — bugs, edge cases, races, security — and for each, try to construct a concrete input or sequence that triggers it; keep the ones that hold up, discard the rest. "
        "For any test claim, check the test would actually fail if the behavior regressed. Where you have file access, verify each claim against the code before committing to it; otherwise ground it in the inline task.md / mind-map context — do not treat lack of file access as a reason to drop everything. Drop only claims you cannot ground either way. "
        "All of that reasoning stays internal — your written output stays terse per the finding-format rule below. Depth of thinking, brevity of report. "
        "Review through six lenses: "
        "(1) Simplify — what's unnecessary or over-engineered? What can be removed? "
        "(2) Self-critique — does the code actually fulfill the stated Intent? What would a skeptic say? "
        "(3) Bug scan — find actual bugs, edge cases, race conditions, or security issues. "
        "(4) Hostile sequences — for every state-changing flow this change touches, walk: two concurrent requests (identical or conflicting); "
        "the same logical event delivered twice under DISTINCT ids; events arriving reordered; the external call succeeding while the local "
        "transaction rolls back; a crash after commit but before any post-commit step; a lost response causing the caller to retry. "
        "For each applicable sequence, trace the persisted state and external effects, then cite the test that proves it safe or raise a "
        "finding demanding one. If the change touches no shared or persisted state, say so in one line and move on. "
        "(5) Test quality — do the tests verify Intent claims or just confirm the implementation? For pure-function code (parsers, formatters, transformations), are there untested invariants that property tests would catch? "
        "(6) Prove it works — cite file:line evidence showing correctness, or construct a concrete scenario showing failure. "
        "Be specific and adversarial — your job is to find problems, not approve. "
        f"{post_d6_clauses(settled, delta)}"
        "Max 5 findings that count — Critical or Important. Below them you may add at most 3 Minor ones "
        "(a small issue that should not hold the close: wording, style, a cheap hardening), each starting "
        "with `**Minor**`; they are never counted and block nothing — do not raise a Minor to Important to "
        "be heard, nor lower a real one to Minor; the CAP line counts the Critical and Important ones, and "
        "`more remain` only when you dropped a Critical or Important one. "
        "Then, as your LAST line, report whether the cap bound you: "
        "`CAP: 5/5 reported, more remain` if you had to drop findings to fit, or "
        "`CAP: <k>/5 reported, exhausted` if you reported everything you found — "
        "so the reader can tell convergence (nothing left) from saturation (more remain). "
        "Each finding: cite file:line, 1-2 sentences stating the problem, 1 sentence stating the fix. No elaboration. "
        "DO NOT edit any files — you are sandboxed read-only and the attempt will "
        "fail. Output your findings to stdout only; the parent process writes them "
        "into the task's '## Implementation Review' section."
    )


def panel_plan_review_prompt(
    task_path: str,
    inline_context: bool = False,
    *,
    soft_timeout_secs: "int | None" = None,
    hard_timeout_secs: "int | None" = None,
    trim_notice: str = "",
    judge_verify=None,
    settled: bool = False,
    delta: bool = False,
) -> str:
    """Panel judge prompt for plan review — writes to stdout, never edits task.md."""
    context_location = "provided below" if inline_context else "provided in your system prompt"
    intent_check = _intent_check(task_path)
    time_budget = time_budget_instruction(soft_timeout_secs, hard_timeout_secs)
    depth_budget = _depth_budget_clause(soft_timeout_secs)

    return (
        "You are a senior engineer reviewing a PLAN — no code has been written yet. "
        f"The MIND_MAP.md and task.md are {context_location}. "
        "Read the source files referenced in the plan to understand existing patterns. "
        f"{intent_check}"
        f"{time_budget}"
        f"{_trim_clause(trim_notice)}"
        f"{judge_verify_clause(judge_verify)}"
        "Work the problem deeply before you write anything — spend substantial reasoning effort on the analysis, not on a long report "
        f"({depth_budget}). "
        "Where you have file access, read the relevant source and its callers/callees (don't judge from names alone) and trace, end-to-end, the data and control flow the plan would touch. "
        "Form several independent hypotheses about where this plan will fail or fall short, and for each, try to construct a concrete scenario that breaks it — keep the ones that hold up, discard the rest. "
        "Where you have file access, verify each claim against the code before committing to it; otherwise ground it in the inline task.md / mind-map context — do not treat lack of file access as a reason to drop everything. Drop only claims you cannot ground either way. "
        "All of that reasoning stays internal — your written output stays terse per the finding-format rule below. Depth of thinking, brevity of report. "
        "Then critique the plan through six lenses: "
        "(1) Intent alignment — will this approach actually fulfill the stated Intent? What's missing or underspecified? "
        "(2) Failure modes — what will go wrong that isn't addressed? Construct a concrete failing scenario. "
        "(3) Hostile sequences — for every state-changing flow the plan touches, walk: two concurrent requests (identical or conflicting); "
        "the same logical event delivered twice under DISTINCT ids; events arriving reordered; the external call succeeding while the local "
        "transaction rolls back; a crash after commit but before any post-commit step; a lost response causing the caller to retry. "
        "For each applicable sequence, name the invariant or mechanism the plan relies on AND the planned test that will prove it — "
        "an unaddressed applicable sequence is a finding. If the change touches no shared or persisted state, say so in one line and move on. "
        "(4) Test coverage — does the test plan cover the failure modes above? For pure-function code, does it identify invariants (idempotency, bounds, round-trip) worth property-testing? "
        "(5) Simplify — is anything over-engineered? What can be dropped? "
        "(6) Prove it — cite file:line evidence for claims about existing code. No hand-waving. "
        "Be specific and adversarial — your job is to find problems, not approve. "
        f"{post_d6_clauses(settled, delta)}"
        "Max 5 findings that count — Critical or Important. Below them you may add at most 3 Minor ones "
        "(a small issue that should not hold the close: wording, style, a cheap hardening), each starting "
        "with `**Minor**`; they are never counted and block nothing — do not raise a Minor to Important to "
        "be heard, nor lower a real one to Minor; the CAP line counts the Critical and Important ones, and "
        "`more remain` only when you dropped a Critical or Important one. "
        "Then, as your LAST line, report whether the cap bound you: "
        "`CAP: 5/5 reported, more remain` if you had to drop findings to fit, or "
        "`CAP: <k>/5 reported, exhausted` if you reported everything you found — "
        "so the reader can tell convergence (nothing left) from saturation (more remain). "
        "Each finding: cite file:line, 1-2 sentences stating the problem, 1 sentence stating the fix. No elaboration. "
        "Note: your findings will be triaged by the reading agent — they will verify file:line claims before applying, push back on speculative concerns, and require concrete evidence. Self-flag any claim you cannot defend with code citation. The reading agent lives with the outcomes; you do not. "
        "DO NOT edit any files. Output your findings to stdout only."
    )


def panel_impl_review_prompt(
    task_path: str,
    inline_context: bool = False,
    *,
    soft_timeout_secs: "int | None" = None,
    hard_timeout_secs: "int | None" = None,
    trim_notice: str = "",
    judge_verify=None,
    settled: bool = False,
    delta: bool = False,
) -> str:
    """Panel judge prompt for impl review — writes to stdout, never edits task.md."""
    context_location = "provided below" if inline_context else "provided in your system prompt"
    intent_check = _intent_check(task_path)
    time_budget = time_budget_instruction(soft_timeout_secs, hard_timeout_secs)
    depth_budget = _depth_budget_clause(soft_timeout_secs)

    return (
        "You are a senior engineer reviewing a COMPLETED implementation. "
        f"The MIND_MAP.md and task.md are {context_location}. "
        "Read the source files changed by this task (look at the Work Plan gates for paths). "
        f"{intent_check}"
        f"{time_budget}"
        f"{_trim_clause(trim_notice)}"
        f"{judge_verify_clause(judge_verify)}"
        "Work the problem deeply before you write anything — spend substantial reasoning effort on the analysis, not on a long report "
        f"({depth_budget}). "
        "Where you have file access, read the changed source and its callers/callees (don't judge from names alone) and trace the data and control flow end-to-end. "
        "Form several independent hypotheses about how this code could be wrong — bugs, edge cases, races, security — and for each, try to construct a concrete input or sequence that triggers it; keep the ones that hold up, discard the rest. "
        "For any test claim, check the test would actually fail if the behavior regressed. Where you have file access, verify each claim against the code before committing to it; otherwise ground it in the inline task.md / mind-map context — do not treat lack of file access as a reason to drop everything. Drop only claims you cannot ground either way. "
        "All of that reasoning stays internal — your written output stays terse per the finding-format rule below. Depth of thinking, brevity of report. "
        "Review through six lenses: "
        "(1) Simplify — what's unnecessary or over-engineered? What can be removed? "
        "(2) Self-critique — does the code actually fulfill the stated Intent? What would a skeptic say? "
        "(3) Bug scan — find actual bugs, edge cases, race conditions, or security issues. "
        "(4) Hostile sequences — for every state-changing flow this change touches, walk: two concurrent requests (identical or conflicting); "
        "the same logical event delivered twice under DISTINCT ids; events arriving reordered; the external call succeeding while the local "
        "transaction rolls back; a crash after commit but before any post-commit step; a lost response causing the caller to retry. "
        "For each applicable sequence, trace the persisted state and external effects, then cite the test that proves it safe or raise a "
        "finding demanding one. If the change touches no shared or persisted state, say so in one line and move on. "
        "(5) Test quality — do the tests verify Intent claims or just confirm the implementation? For pure-function code (parsers, formatters, transformations), are there untested invariants that property tests would catch? "
        "(6) Prove it works — cite file:line evidence showing correctness, or construct a concrete scenario showing failure. "
        "Be specific and adversarial — your job is to find problems, not approve. "
        f"{post_d6_clauses(settled, delta)}"
        "Max 5 findings that count — Critical or Important. Below them you may add at most 3 Minor ones "
        "(a small issue that should not hold the close: wording, style, a cheap hardening), each starting "
        "with `**Minor**`; they are never counted and block nothing — do not raise a Minor to Important to "
        "be heard, nor lower a real one to Minor; the CAP line counts the Critical and Important ones, and "
        "`more remain` only when you dropped a Critical or Important one. "
        "Then, as your LAST line, report whether the cap bound you: "
        "`CAP: 5/5 reported, more remain` if you had to drop findings to fit, or "
        "`CAP: <k>/5 reported, exhausted` if you reported everything you found — "
        "so the reader can tell convergence (nothing left) from saturation (more remain). "
        "Each finding: cite file:line, 1-2 sentences stating the problem, 1 sentence stating the fix. No elaboration. "
        "Note: your findings will be triaged by the reading agent — they will verify file:line claims before applying, push back on speculative concerns, and require concrete evidence. Self-flag any claim you cannot defend with code citation. The reading agent lives with the outcomes; you do not. "
        "DO NOT edit any files. Output your findings to stdout only."
    )


# Legacy alias for backward compatibility
def judge_prompt(
    task_path: str,
    inline_context: bool = False,
    mode: str = "plan",
    *,
    soft_timeout_secs: "int | None" = None,
    hard_timeout_secs: "int | None" = None,
    trim_notice: str = "",
    judge_verify=None,
) -> str:
    """Deprecated: use plan_review_prompt() or impl_review_prompt() instead."""
    kwargs = dict(
        soft_timeout_secs=soft_timeout_secs,
        hard_timeout_secs=hard_timeout_secs,
    )
    if mode == "impl":
        return impl_review_prompt(task_path, inline_context, **kwargs)
    return plan_review_prompt(task_path, inline_context, **kwargs)


def design_phase_light() -> str:
    """Lightweight design phase for Fix tasks — just restate and define done."""
    return "## Design Phase\n\n" + chat_log_research() + "\n\n" + """\
### Fix Orientation
- [ ] What exactly is broken or needs cleaning up?
- [ ] What does "fixed" look like (a specific grep, test or behavior)?
- [ ] What adjacent code could this break?
- [ ] Test strategy: point tests, or also property tests (`hypothesis`) if fixing a parser/formatter/transformation?"""


def work_plan_fix() -> str:
    """Fix-specific work plan — locate, fix, verify pairs."""
    return """\
## Work Plan

- [ ] Fix: (what to change)
- [ ] Verify: (grep/test that confirms the fix)
- [ ] Side effects: did anything else change, and does adjacent code still work?

---"""


def design_phase_investigate() -> str:
    """Investigate-oriented design phase — hypothesis-first."""
    return "## Design Phase\n\n" + chat_log_research() + "\n\n" + """\
### Investigation Orientation
- [ ] State the question or hypothesis before looking.
- [ ] What evidence would change your mind?
- [ ] When do you stop (N rounds with no new position, or the specific answer found)?
- [ ] Test strategy: if findings lead to code changes, point tests or also property tests (`hypothesis`) for invariants?"""


def work_plan_investigate() -> str:
    """Investigate-specific work plan — round structure."""
    return """\
## Work Plan

### Round 1: [focus]
- **Hypothesis:** (before testing)
- **Test:** (what to check)
- **Result:** (what happened)
- [ ] Checkpoint: converging or scattering, and is a new hypothesis needed?

### Round 2: [focus]
- **Hypothesis:** (refined from Round 1)
- **Test:** (what to check)
- **Result:** (what happened)
- [ ] Checkpoint: converging or scattering?

### Synthesis
- [ ] What did you learn: the key findings, each with its evidence?
- [ ] What remains unknown, and what would a follow-up task investigate?

---"""


def design_phase_evaluate() -> str:
    """Evaluate-oriented design phase — define lenses and scope."""
    return "## Design Phase\n\n" + chat_log_research() + "\n\n" + """\
### Evaluation Orientation
- [ ] What are you evaluating, and against what criteria?
- [ ] Define lenses (2-4 dimensions to assess consistently across all items)
- [ ] How many items (more than 5: plan a midpoint checkpoint)?
- [ ] Assess first and fix after: keep the two separate.
- [ ] Test strategy: if evaluation leads to fixes, point tests or also property tests (`hypothesis`) for invariants?"""


def work_plan_evaluate() -> str:
    """Evaluate-specific work plan — lenses, per-item, verdict."""
    return """\
## Work Plan

### Lenses
| Lens | What it measures |
|------|-----------------|
| (lens 1) | (description) |
| (lens 2) | (description) |

### Assessment
- [ ] Item 1: (apply all lenses)
- [ ] Item 2: (apply all lenses)
- [ ] Midpoint checkpoint: are patterns emerging, and do you stop early or continue?

### Verdict
- [ ] Overall assessment: PASS / PARTIAL / FAIL
- [ ] Gaps found: cosmetic or material?
- [ ] Sufficiency: is the current state good enough, or do gaps justify action?

---"""


def standing_orders() -> str:
    return """\
## Standing Orders
- **Expand dynamically**: When you discover something you'll need to do, write new gates immediately \u2014 don't wait until you get there.
- **Steer openly**: If your direction changes, edit your open (unchecked) gates to reflect reality. The plan is alive, not a contract.
- **Never defer awareness**: The moment you realize work exists, capture it. Forgetting is the failure mode, not having too many gates."""


# Task 213 (PHASE-B.md item 2; the owner, 2026-10-15: "Scris o dată, nu în fiecare fișă"): the instructions every
# task.md used to carry — the gate discipline, the Status and Risk explanations, the Design Phase and Work Plan
# notes, the Standing Orders — are written here once, verbatim, and printed by `tasks new` / `tasks guide`. A
# task.md keeps `pointer()` at the top and `risk()`'s one pointer line under its value.
WORK_PLAN_NOTES = {
    "Build": """\
> - For each work section: what could go wrong, and how will you know it worked (a specific check, not "looks good")?
> - A standard feature: 6-8 work gates + tests.
> - Large tasks work fine — past 15 gates, add a mid-point checkpoint to reassess direction.""",
    "Fix": "> - Fix / Verify pairs.\n> - What could this break?",
    "Investigate": "> - Rounds: hypothesis → test → result → checkpoint.\n> - Stop when converging.",
    "Evaluate": "> - Apply the lenses consistently.\n> - Assess first, decide the action after.",
}


# Task 223 (PHASE-B.md item 2; the owner, 2026-10-16 09:08: "Titlu de o propoziție"): a gate's title is one
# sentence; what a long title used to say is here, verbatim, under its new title — printed by the guide. A title that
# was only two short sentences made one has no entry.
GATE_NOTES = (
    ("Context: run `.claude/bin/tasks recall <words>`, then paste the relevant excerpts of each node below.",
     "Context: `.claude/bin/tasks recall keyword1 keyword2` (locates matching nodes across MIND_MAP.md + overflow), then `tasks recall <N>` for each → paste relevant excerpts below"),
    ("Keep the Recent Chat messages that matter and carry the user's own words into Intent and Why.",
     "Review the \"Recent Chat\" messages captured in References (auto-injected at `tasks work`). Remove unrelated ones. Pull key user quotes, constraints, and context into Intent/Why above. The user's actual words are the ground truth for Intent."),
    ("Say what kind of work this is and, if it is long or uncertain, where you will stop to reassess.",
     "What kind of work is this? (build / investigate / evaluate / decide / combination?) If combination, what's the sequence? If >15 gates or uncertain approach, pick a checkpoint where you pause and reassess direction before continuing."),
    ("Set `## Risk` and name what would show the change wrong (assertive: its instrument; irreversible: its rollback).",
     "**Set `## Risk`** (reversible / irreversible / assertive). Ask: if this claim/change were WRONG, what would show it? An `assertive` task (changes a claim about the world — docs, a calibration, a measurement) must name the instrument that would reveal the claim false. An `irreversible` task must name its rollback plan. These cannot be light-closed for being small."),
    ("Write task-specific check questions whose answers need evidence, not a yes or no.",
     "Wrote task-specific check questions (Bad: \"is this working?\" Good: \"Does the output include the progress counter?\" — the answer should require evidence, not just yes/no)"),
    ("Test strategy: what you will test and how (point tests, property tests for invariants).",
     "Test strategy: what are you testing and how? (point tests for specific behavior, property tests via `hypothesis` for invariants on transformations/parsers/arithmetic)"),
    ("Run the plan panel in the background — `.claude/bin/tasks panel-review <N>` — then read `judge.md`.",
     "Run `.claude/bin/tasks panel-review <N>` — ALL available judges review the plan in parallel (blind: they see the repo, not your conversation). Wait for it to finish; findings land in `judge.md` (this dir), led by a PANEL VERDICT. Read judge.md, then address valid concerns by revising Work Plan gates yourself. **Justify lens:** does every work gate trace up to something in Intent/Design? Are there gates that justify nothing above them (scope creep)? Intent claims with no gate to satisfy them (gaps)? *(Fallback if no panel can run: `.claude/bin/tasks plan-review <N>` — single judge, writes into this file.)*"),
    ("Triage the plan panel's findings here: accept, park or reject each one, with its reason.",
     "**Triage panel findings into this file: judges = opinion, not gospel.** For each finding, document accept (with rationale) / park (with rationale) / reject (with rationale) here under this gate. Push back where you have concrete evidence — you live with the outcomes, the reviewers don't. Verify file:line claims before applying. Where judges DISAGREE with each other, say which you believed and why — disagreement between models is signal, not noise."),
    ("Run the impl panel in the background — `.claude/bin/tasks panel-review <N> --mode impl` — then read `judge.md`.",
     "Run `.claude/bin/tasks panel-review <N> --mode impl` — ALL available judges review the implementation in parallel. Wait for it to finish; findings land in `judge.md`, led by a PANEL VERDICT (a quorum-PASS impl panel is what the close gate accepts as evidence when `panel_required_for` is set). Read judge.md. **Satisfy lens:** does every Intent claim trace down through code to tests? Where does the chain break? *(Fallback if no panel can run: `.claude/bin/tasks impl-review <N>` — single judge, writes into this file.)*"),
    ("Triage the impl panel's findings here: accept, park or reject each one, with its reason.",
     "**Triage panel findings into this file: judges = opinion, not gospel.** For each finding, document accept (with rationale) / park (with rationale) / reject (with rationale) here under this gate. Push back where you have concrete evidence — you live with the outcomes, the reviewers don't. Verify file:line claims before applying. Where judges DISAGREE, say which you believed and why."),
    ("Freehand — stay to discuss the work with the user; remove this gate at design time if headless or not needed.",
     "Freehand — work is done, stay for discussion with user. Remove this gate during Design Phase if running headless or task doesn't need debrief."),
    ("MIND_MAP.md: update the OWNING subsystem node in place, never one node per task.",
     "MIND_MAP.md: update the OWNING subsystem node **in place** — a NEW node only for a genuinely new subsystem, never one node per task"),
    ("Set `## Risk` to one word before any work, and say why in the note.",
     "`## Risk` above set to exactly one word — reversible / irreversible / assertive — BEFORE any work, and this gate's outcome note states WHY in one line (what would `git revert` NOT undo? does this assert a claim about the world?). If assertive or irreversible: ceremony stays light but the FULL review bar applies — run the implementation panel once the work is built; the close gate enforces the evidence in judge.md. Light never waives review."),
    ("Review routing honored: assertive or irreversible work had the implementation panel, triaged in judge.md.",
     "Review routing honored — reversible: may proceed without review; assertive/irreversible: the implementation panel ran and its findings are triaged in judge.md (the close gate checks the evidence itself, not this box)"),
)


# The retro scaffold's (`retro.py`) and `tasks freehand`'s (`lifecycle.py`) gates that carried more than their new
# title says; their sections are `tasks guide retro` / `tasks guide freehand` (task 223, plan panel P3/P4).
RETRO_GATE_NOTES = (
    ("Read this window's chat messages and summarize the user's arc, its themes and its frustrations.",
     "Read chat_log.md messages for this window (use `.claude/bin/tasks log` for a compact one-line-per-message view, "
     "or filter by message IDs listed above). Summarize: what was the user's arc? What themes dominated? What "
     "frustrations surfaced?"),
    ("Identify the steering moments: where the user corrected the agent, and what kind of correction each was.",
     "Identify steering moments \u2014 where did the user correct the agent? What was the nature of each correction "
     "(misframed intent / over-engineering / wrong abstraction / missing context / process friction)?"),
)
FREEHAND_GATE_NOTES = (
    ("Freehand log \u2014 run `.claude/bin/tasks freehand log`, then add checked gates for the work done.",
     "Freehand log \u2014 run `.claude/bin/tasks freehand log` to capture chat_log messages, then retro-add checked "
     "gates for work done"),
    ("Rename the task folder and header to what was done, run `.claude/bin/tasks adopt <new folder name>`, check "
     "this last.",
     "Rename this task folder and header to match what was actually done, then run `.claude/bin/tasks adopt <new "
     "folder name>` (the task registry knows it by its name) and check this gate last"),
)
GATES_HEADING = "Gates \u2014 what each short title asks"


def gate_notes_text(task_type: "str | None" = None) -> str:
    """The gate notes for `task_type`: a template type's own gates (those its render writes), the retro's, the
    freehand gates', or every entry for no type or one the CLI does not know — each short title, then what it used
    to say."""
    if task_type == "retro":
        entries = RETRO_GATE_NOTES
    elif task_type == "freehand":
        entries = FREEHAND_GATE_NOTES
    elif task_type in ("quick", "light") or PLAYBOOKS.get(task_type or ""):
        rendered = set(render_template(1, "X", task_type).split("\n"))
        entries = tuple(e for e in GATE_NOTES if f"- [ ] {e[0]}" in rendered)
    else:
        entries = GATE_NOTES + RETRO_GATE_NOTES + FREEHAND_GATE_NOTES
    return "\n".join(f"> **{title}**\n> {old}" for title, old in entries)


def _guide_sections(task_type: "str | None") -> "list[tuple[str, str]]":
    """(title, text) of the guide for one task type; every section for no type or one the CLI does not know."""
    full = [("Gate discipline (feature, build, refactor, ops, bugfix, cleanup, research, audit, eval)", sticker()),
            ("Gate discipline (light)", sticker_light()), ("Gate discipline (quick)", sticker_quick())]
    common = [("Status", status_note()), ("Risk", risk_explanation())]
    notes = [(f"Work Plan ({pattern}: {', '.join(t for t, p in PLAYBOOKS.items() if p == pattern)})", text)
             for pattern, text in WORK_PLAN_NOTES.items()]
    notes_text = gate_notes_text(task_type)                 # once: it renders the type (223's impl round 2, sonnet)
    gates = [(GATES_HEADING, notes_text)] if notes_text else []
    if task_type == "quick":
        return [full[2]] + common + gates
    if task_type == "light":
        return [full[1]] + common + gates
    if task_type == "retro":
        return [("Gate discipline", sticker())] + common + gates
    if task_type == "freehand":
        return gates
    pattern = PLAYBOOKS.get(task_type) if task_type else None
    if pattern is None:
        return full + common + [("Design Phase", design_phase_note())] + notes + gates + [
            ("Standing Orders", standing_orders().split("\n", 1)[1])]
    return ([("Gate discipline", sticker())] + common + [("Design Phase", design_phase_note())]
            + [n for n in notes if n[0].startswith(f"Work Plan ({pattern}:")] + gates
            + [("Standing Orders", standing_orders().split("\n", 1)[1])])


def guide_text(task_type: "str | None" = None) -> str:
    """The single copy of the template's instructions (task 213) — for `task_type`, or all of it."""
    return "\n\n".join(f"### {title}\n{text}" for title, text in _guide_sections(task_type))


# ---------------------------------------------------------------------------
# CLAUDE.md init template
# ---------------------------------------------------------------------------

def claude_md(title: str) -> str:
    """Generate CLAUDE.md content for `tasks init`."""
    return f"""\
# {title}

## Start Here

```bash
.claude/bin/tasks bootstrap          # loads mind map, skills, pending tasks
```

Then **ask the user** what they want to work on. Don't autonomously pick a task.

## CLI

```bash
.claude/bin/tasks work <number>              # activate task, hook starts tracking
.claude/bin/tasks work done [--force]        # finish; bounces if gates open. --force overrides all; --stale-panel-ok --reason clears only a stale panel
.claude/bin/tasks new <type> <name> [intent] # create task — intent fills ## Intent
.claude/bin/tasks new --stub <type> <name> [intent] # stub — expands on tasks work
.claude/bin/tasks plan-review <number>       # blind plan review by independent agent
.claude/bin/tasks impl-review <number>       # blind implementation review by independent agent
.claude/bin/tasks list [--pending]           # task overview
.claude/bin/tasks status                     # current gate position
.claude/bin/tasks bootstrap                  # orientation: mind map + skills + pending
.claude/bin/sandbox --prompt "..." [--agent claude|codex|agy|pi] [--bare]  # run a contained headless subagent; `--help` for flags
```

## Don't

- Create task directories manually — always `.claude/bin/tasks new`
- Edit session state files directly (under `.agent/`, or `.agent/<user>/` in a multi-user repo) — use `.claude/bin/tasks work <N>` / `.claude/bin/tasks work done`
- Edit `## Status` in task.md directly — use `.claude/bin/tasks work done`
- Skip task.md checkboxes — they're your observable progress
- Start coding without an active task — blocked by hook until `.claude/bin/tasks work <N>`
- Use EnterPlanMode or plan files — use `.claude/bin/tasks new <type> <name>` instead, the task.md IS the plan
"""


# ---------------------------------------------------------------------------
# Bootstrap briefing
# ---------------------------------------------------------------------------

def identity_preamble() -> str:
    """One-line framing shown at the top of bootstrap."""
    return "You are a coding assistant working with a task management harness."


def mind_map_header() -> str:
    """Navigation header shown before full mind map at bootstrap."""
    return (
        "Project knowledge graph. Nodes cross-reference with [N] IDs.\n"
        "Below is either the full map or an index (routing nodes + titles) —\n"
        "drill into any node: `tasks recall <N>` (a node is its [N] line and the fact lines under it)\n"
        "Format spec: /mindmap skill"
    )



def workflow_briefing() -> str:
    """Workflow rules shown at task activation (tasks work <N>)."""
    return """\
- One gate at a time: read gate → do work → check box → next gate
- Close a gate with its outcome on the lines under it, indented two spaces deeper, one fact per line, in the same edit as the tick.
- Closing several ALREADY-DONE gates in one edit: allowed only when each gate carries its own outcome note (hook-enforced, max 5; where no hook runs, keep to one at a time). Bare batch ticks are blocked.
- Pattern templates in task.md ARE the work plan — fill them in, don't skip"""


def cli_reference() -> str:
    """CLI quick reference shown at bootstrap."""
    return """\
Tasks CLI:
  Workflow:
    tasks work <N>             activate task
    tasks work done            deactivate
    tasks freehand             user-driven mode (no gate pressure)
    tasks adopt <folder>       register a task folder the CLI did not make (e.g. after renaming one)
  Create:
    tasks new <type> <name> [intent]   create task (intent fills ## Intent)
    tasks new --stub <type> <name> [intent]   stub (expands on work)
    tasks guide [<type>]       how to fill a task.md in (printed by `tasks new`)
  Review:
    tasks plan-review <N>      blind plan review
    tasks impl-review <N>      blind impl review
    tasks panel-review [<N>]   multi-model judge panel; task optional — use --prompt alone for any question, --bare to strip all context
    tasks models check         audit models.json judge pins against live availability (--no-probe skips claude probes)
    tasks models detect        list installed agent CLIs + their selectable models/efforts (fast, no probe)
    tasks models select        interactively refresh the panel in .agent/models.json
    tasks models set           write the panel non-interactively (--panel a,b --default-judge c [--force])
  Analysis:
    tasks retro [--since N]    project retrospective
    tasks context <N>          extract chat messages for a task
    tasks doctor               harness health check
    tasks environment          advisory: optional tools that improve the setup + how to install them
    tasks detect-verify        suggest a full verify command (typecheck+tests+lint) from the project's toolchains
  Info:
    tasks list [--pending]     show tasks
    tasks status               current gate position
    tasks dashboard            read-only: what playbook runs on now — panel seats + 14d journal stats, knobs, hooks, judgebench exam, 3 triggers with exact commands
    tasks recall <id|words>    fetch a mind-map node (main+overflow) by id, or locate by keyword
    tasks ruling add|end|list  the project's owner rulings, written once: add (--permanent | --until "<what ends it>") "<his exact words>"
    tasks verify [<N>]         run the verify contract as the close does; a green run of the same code is not run again at the close
                               (stage the change first — each code_roots repo committed, its gitlink staged if tracked; a later edit outside the task's records means the close runs it again)
    tasks numbers [N]          read-only: a task's own numbers per close (time by kind and section, rounds, seats, verify, the owner) — an open task's as a snapshot
    tasks compact <N>          move <!-- archive:start/end --> narrative to task-archive.md"""


def agents_md_template() -> str:
    """AGENTS.md content for Codex projects.

    Codex auto-loads AGENTS.md from the repo root (baked into its base
    instructions).  This file teaches the agent the Playbook workflow.
    Embed cli_reference() literally — current at install time.  To refresh
    after a Playbook upgrade: delete AGENTS.md, then re-run
    `tasks init --provider codex`.
    """
    return """\
# Playbook Workflow

This project uses the **Playbook task harness**.  Follow these rules on every
session — they govern how you work, not what you build.

## Start of Session

Run this first, before anything else:

    .claude/bin/tasks bootstrap

It prints the project mind map, pending tasks, and the full CLI reference.
Read it.  Then ask the user what to work on, or pick the highest-priority
pending task.

## Before Editing Code

You **must** activate a task before touching any code file:

    .claude/bin/tasks work <N>      # e.g. tasks work 042

This sets the active task.  Without it, edits are blocked.

## Working Through a Task

- Read the task.md that `tasks work` prints.
- Work **one gate at a time**: read the gate → do the work → check the box
  (write its outcome on the lines under it, indented two spaces deeper, one fact
  per line) → move to the next gate.
- Never skip gates.  Batch-closing ALREADY-DONE gates in one edit is allowed
  only where a hook enforces an outcome note per gate — no such hook runs on
  this provider, so keep to one gate at a time here.
- If you discover new work, add new gates to task.md immediately.

## End of Task

    .claude/bin/tasks work done

This deactivates the task and marks it done.  Run it when all gates are
checked — not before.

## CLI Reference

{cli_ref}

## Do Not

- Edit session state files directly (under `.agent/`, or `.agent/<user>/` in a multi-user repo) — use `tasks work` / `tasks work done`.
- Create task directories manually — use `tasks new`.
- Close multiple gates in a single edit.
- Start coding without an active task.
""".format(cli_ref=cli_reference())


def antigravity_md_template() -> str:
    """GEMINI.md content for Antigravity CLI (`agy`) projects.

    agy reads GEMINI.md from project cwd (mirrors the user-level `~/.gemini/GEMINI.md`
    convention). Hook enforcement works when `tasks init --provider antigravity --hooks`
    is run: this installs a global agy plugin via `agy plugin install` that wires
    PreToolUse / PostToolUse / UserPromptSubmit / Stop hooks to the Playbook scripts.

    Model selection: agy v1.0.2 has no -m CLI flag — set the model from the agy UI
    (~/.gemini/antigravity/user_settings.pb). When upstream ships -m, panel-review
    will switch from a single judge to per-model judges automatically.
    """
    return """\
# Playbook Workflow

This project uses the **Playbook task harness**.  Hooks are installed globally
via `agy plugin install` when you run `tasks init --provider antigravity --hooks`.
Without that step the file is advisory.

## Start of Session

Run this first:

    .claude/bin/tasks bootstrap

It prints the project mind map, pending tasks, and the full CLI reference.

## Before Editing Code

Activate a task:

    .claude/bin/tasks work <N>

## Working Through a Task

Work one gate at a time.  Check each gate box before moving to the next.
Never skip.  Write each gate's outcome on the lines under it, indented two
spaces deeper, one fact per line.  Batch-closing needs a hook that enforces
an outcome note per gate — none runs on this provider, so keep to one gate at
a time here.

## End of Task

    .claude/bin/tasks work done

## CLI Reference

{cli_ref}

## Do Not

- Edit session state files directly (under `.agent/`, or `.agent/<user>/` in a multi-user repo) — use `tasks work` / `tasks work done`.
- Create task directories manually — use `tasks new`.
- Close multiple gates in a single edit.
- Start coding without an active task.
""".format(cli_ref=cli_reference())


# ---------------------------------------------------------------------------
# CLI usage
# ---------------------------------------------------------------------------

def usage_text() -> str:
    """Usage text for `tasks --help`."""
    # D2: `light` is a first-class type (own template, accepted by cmd_new, listed
    # by list_all_types on the error path) — include it here too, not just `quick`.
    types = ", ".join(sorted(set(PLAYBOOKS.keys()) | {"quick", "light"}))
    return f"""\
Usage: tasks <command> [args]

Commands:
  work <number>       Set active task (e.g. tasks work 058)
  work done [--force --reason "why"]  Finish task; runs the verify contract and
                      records a receipt; a failing verify blocks, as does an
                      unreviewed task under the panel_required_for policy
                      (/playbook:init seeds ["assertive","irreversible"] ⇒ only
                      those close-gate on a PASS panel, reversible work closes on
                      verify+single-judge; set "all" for a panel on every close).
                      Stale panel evidence on a high-consequence close blocks;
                      --stale-panel-ok --reason accepts only that, while --force
                      --reason is the blunt whole-policy override. --force and
                      --stale-panel-ok both need --reason.
  audit [<N>]         Run mechanical pre-panel sweeps (conflict markers, merge
                      artifacts, stale markers, + project sweeps); the newest receipt in
                      task.md, the older ones in audit-log.md beside it
  parked [--all]      List open parked items across tasks (--all: incl. resolved)
  blocked "<reason>"  Pause the active task awaiting the owner's decision — an
                      honest state (not a faked checkbox); resume with work <N>
  handoff             Hand off the active task to a fresh session: writes the
                      mechanical state (repo + code_roots git state, gates, latest
                      receipt) into a ## Handoff section for you to append your
                      in-flight reasoning to, then blocks (reason: handoff).
                      bootstrap surfaces it; work <N> resumes and consumes it.
  freehand            User-driven mode (no gate pressure)
  adopt <folder>      Register a task folder the CLI did not make (a renamed one, or a
                      hand-made one that should stay) with the lane's task registry
  new <type> <name> [intent]   Create task (intent pre-fills ## Intent)
  new --stub <type> <name> [intent]   Create stub (expands on work)
  guide [<type>]      How to fill a task.md in — gate discipline, what each ## Risk value
                      means, the standing orders; printed by `new` (a stub: when it expands)
  ruling add|end|list The project's owner rulings, written once into .agent/rulings.md:
                      add (--permanent | --until "<what ends it>") "<his exact words>"; end <N> "<why>"
  verify [<N>]        Run the verify contract as the close does (task N's risk, or the active task's);
                      a green run is remembered, and a close of the same code does not run it again
                      (stage the change first — each code_roots repo committed, its gitlink staged if tracked; a later edit outside the task's records means the close runs it again)
  numbers [N]         Read-only: the task's own numbers per close, kept in its numbers.json (time by kind
                      and section without sleep, rounds, seats, verify, the owner); an open task's as a snapshot
  list [--pending]    List all tasks with status
  status              Show head position for active tasks
  compact <N> [--dry-run]  Move <!-- archive:start/end --> narrative from a bloated
                      task.md into task-archive.md (verbatim); refuses gates/pins
  plan-review <N>     Run blind plan review
  impl-review <N>     Run blind implementation review
  panel-review [<N>]  Multi-model judge panel
                      --prompt "..."     add steering (appended to review prompt, or full mission if no task)
                      --no-mind-map      strip mind map from context
                      --bare             no context at all; --prompt is the entire prompt
  models check        Audit models.json judge pins against live availability (--no-probe: skip claude probes)
  models detect       List installed agent CLIs + selectable models/efforts (fast, no probe) [--json]
  models select       Interactively refresh the panel in .agent/models.json
  models set          Write the panel non-interactively: --panel a,b --default-judge c [--force]
  retro [--since N]   Project retrospective
                      Collect Playbook artifacts for a global retro archive
  context <N>         Extract chat messages for a task
  intent <N>          Vertical retro of one finished task → INTENT.md (says what it would spend; `--yes <what it quoted>` runs exactly that)
  timeline            Internal retro-support: chronological tasks + messages
  tagger              Internal retro-support: tag tasks for retro analysis
  tag                 Internal retro-support: apply a retro tag
  log [N] [--width W]  Compact one-line-per-message chat log (last N, body cropped to W; default all/500)
  prepare-merge [--target <branch>] [--dry-run]
                      Renumber tasks, re-sequence chat_log, report MIND_MAP collisions
                      so the branch merges cleanly into target (default: main)
  merge-doctor        Audit a multi-user repo before/after a merge (contamination,
                      stranded conflict markers, legacy .agent paths)
  mindmap-sync        Mind-map merge support (conflict-marker-safe sync)
  doctor [--verbose]  Harness health check (--verbose: enumerate findings in
                      stale/foreign install copies too, not just the live one)
  dashboard [--no-detect]  Read-only: plugin/verify/panel seats + effort/default judge, review
                      knobs, hooks health, open+parked tasks, judgebench last exam, per-seat
                      14d journal stats (runs, ok%, timeout%, median) and 3 triggers, each with
                      its exact command (--no-detect: skip the provider model listing)
  environment         Advisory: optional tools that improve the setup + install hints [--json] [--suggest-only]
  detect-verify       Suggest a full verify command (typecheck+tests+lint) from the toolchains present [--json]
  bootstrap           Load mind map (indexed if large) + skills + pending tasks
  recall <id|words>   Fetch a mind-map node by id (main + overflow), or locate
                      nodes by keyword — the fetch half of the bootstrap index
  init                Create CLAUDE.md for this project

Sandboxed subagents (separate CLI):
  .claude/bin/sandbox --prompt "..." [--agent claude|codex|agy|pi] [--bare] [--stream]
                      Run a contained headless agent (write-containment); `--help` for flags

Task types: {types}

Examples:
  tasks work 058
  tasks new feature add-auth
  tasks new build my-task Build extraction layer for retro command
  tasks new --stub research token-bug Investigate auth token refresh
  tasks plan-review 001
  tasks panel-review 001 --prompt "focus on the title-detection approach"
  tasks panel-review --prompt "which of these two designs is simpler?" --no-mind-map
  tasks panel-review --bare --prompt "read ideas.txt and pick the best story idea"
  tasks list --pending"""


# ---------------------------------------------------------------------------
# Composition
def sticker_quick() -> str:
    return """\
> **Gate discipline:**
> - One gate → do the work → check the box → the next gate.
> - Never backfill: the document IS the execution trace.
> - A gate's outcome goes on the lines under it, indented two spaces deeper, one fact per line.
> - Batch-closing ALREADY-DONE gates needs an outcome note per gate (hook-enforced; where no hook runs, one gate at a time).
> - Never born checked: write the gate BEFORE you do its step, then check it.
> - Work you already finished: add its gate unchecked, then check it in a SEPARATE edit, with its outcome.
> **Selection rule:** quick is for declared-reversible trivia only — anything touching docs, claims, data, or publishing belongs in `light` or heavier (risk decides review; smallness never does)."""


def sticker_light() -> str:
    return """\
> **Light shape** — small, sub-day work:
> - Ceremony compressed, review NEVER waived: risk decides review, smallness never does.
> - If this grows beyond small, convert: `tasks new <type> <name>`.
> - Gate discipline unchanged: one gate → do the work → check the box.
> - A gate's outcome goes on the lines under it, indented two spaces deeper, one fact per line.
> - Never backfill; batch-closing ALREADY-DONE gates needs an outcome note per gate (hook-enforced; where no hook runs, one at a time).
> - Never born checked: write the gate BEFORE you do its step, then check it.
> - Work you already finished: add its gate unchecked, then check it in a SEPARATE edit, with its outcome.
> **Selection rule:** `quick` is for declared-reversible trivia only; docs, claims, data, or publishing belong here or heavier."""


def render_stub_template(num: int, title: str, intent_text: str = "",
                         task_type: str | None = None) -> str:
    """Minimal stub for GTD capture. No gates, expands on `tasks work <N>`."""
    type_tag = task_type or "feature"
    parts = [
        header(num, title),
        f"<!-- stub:{type_tag} -->",
        status(),
        f"## Intent\n{intent_text}" if intent_text else "## Intent\n(fill in before expanding)",
        "## Why\n(fill in before expanding)",
        "## References\n(optional)",
    ]
    return "\n\n".join(parts) + "\n"


def render_quick_template(num: int, title: str) -> str:
    """Minimal task.md for sub-hour fixes and small work. ~3 gates, no ceremony."""
    parts = [
        header(num, title),
        pointer("quick"),
        status(),
        "## Intent\n(one line — what to do and how to verify)",
        "---",
        "## Work\n- [ ] Do the work\n- [ ] Test: verify it worked\n- [ ] Cleanup: mind map, commit",
        "## Parked\n(out of scope discoveries)",
    ]
    return "\n\n".join(parts) + "\n"


def render_light_template(num: int, title: str) -> str:
    """Small-work shape (F14): ~6 gates, risk classified FIRST, review routed
    by risk — never by size. The 056 lesson is enforced close-side
    (`close_decision`): an assertive/irreversible task cannot close without
    impl-grade review evidence whatever template it used; this shape's job is
    to say so early and shed the Build ceremony around it.

    Wording constraint (blind-judge Finding 2): no checkable line may carry
    the literal `impl-review` / `panel-review` / `plan-review` tokens —
    `has_review_evidence` matches those substrings on checked lines, so a
    routing gate that names the command would MINT evidence when checked.
    Say "the implementation panel"; the close gate reads judge.md, not boxes.
    """
    parts = [
        header(num, title),
        pointer("light"),
        status(),
        "## Intent\n(one line — what to do and what proves it worked)",
        "## References\n(optional context; recent chat lands here at activation)",
        "---",
        """## Risk Routing
- [ ] Set `## Risk` to one word before any work, and say why in the note.""",
        """## Work
- [ ] Orient: name the files/claims this touches and what "verified" means (one paragraph)
- [ ] Do the work
- [ ] Verify: run the check that proves it and paste its result under this gate.""",
        """## Review
- [ ] Review routing honored: assertive or irreversible work had the implementation panel, triaged in judge.md.""",
        """## Pre-review
- [ ] MIND_MAP.md: owning node updated in place (only if architecture or claims moved)""",
        parked(),
    ]
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------

def render_template(num: int, title: str, task_type: str | None = None) -> str:
    """Compose all components into a complete task.md template.

    Args:
        num: Task number (will be zero-padded to 3 digits)
        title: Task title (will be title-cased in header)
        task_type: Optional task type for playbook reference

    Returns:
        Complete task.md content as a string
    """
    # Quick/light templates — standalone, no PLAYBOOKS lookup
    if task_type == "quick":
        return render_quick_template(num, title)
    if task_type == "light":
        return render_light_template(num, title)

    pattern_name = PLAYBOOKS.get(task_type) if task_type else None
    playbook_ref = f"playbook/{pattern_name}" if pattern_name else "(none)"

    # --- Eval mode: read template flags from PLAYBOOK_EVAL_CONFIG ---
    import os as _os
    _eval_cfg = {}
    _eval_config_path = _os.environ.get("PLAYBOOK_EVAL_CONFIG", "")
    if _eval_config_path:
        try:
            import json as _json
            with open(_eval_config_path, encoding="utf-8") as _fh:      # closed (task 225; agy, 223's impl round 2)
                _eval_cfg = _json.loads(_fh.read())
        except Exception:
            pass

    # Common parts shared by all variants
    common_start = [
        header(num, title),
    ]
    if _eval_cfg.get("sticker", "on") != "off":    # the arena's switch: since task 213 it turns the pointer off
        common_start.append(pointer(task_type))
    common_start += [
        status(),
        intent_why_refs(playbook_ref),
    ]
    common_end = []
    if _eval_cfg.get("debrief", "on") != "off":
        common_end.append(debrief())
    common_end += [
        pre_review(),
        parked(),
    ]

    if pattern_name == "Fix":
        # judge_impl_section() carries the '## Implementation Review' anchor that
        # _write_review_findings needs to deliver a single-judge impl review into
        # task.md. Without it a Fix-pattern task (bugfix/cleanup) silently
        # stranded findings in judge-*.log (found live in task 010). Build already
        # appends it; Investigate/Evaluate deliberately do not (report-shaped).
        middle = [
            design_phase_light(),
            work_plan_fix(),
            judge_impl_section(),
        ]
    elif pattern_name == "Investigate":
        middle = [
            design_phase_investigate(),
            work_plan_investigate(),
        ]
    elif pattern_name == "Evaluate":
        middle = [
            design_phase_evaluate(),
            work_plan_evaluate(),
        ]
    else:
        # Build (default) — full ceremony
        middle = []
        if _eval_cfg.get("design_phase", "on") != "off":
            middle.append(design_phase())
        if _eval_cfg.get("judge", "on") != "off":
            middle.append(judge_section())
        middle.append(work_plan())
        if _eval_cfg.get("judge", "on") != "off":
            middle.append(judge_impl_section())

    parts = common_start + middle + common_end
    return "\n\n".join(parts) + "\n"
