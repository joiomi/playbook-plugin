#!/usr/bin/env python3
"""Every gate the CLI writes is one short sentence (PHASE-B.md item 2, task 223).

The owner, 2026-10-16 09:08 (task 218): "Titlu de o propoziție (Recomandat)" — some gates were as long as a page (the
feature template's longest 612 characters; 7 of its 25 over the 200 the gate echo shows), and the echo cut them in
half (B1: in 52 % of gate echoes here, 77 % in a second project). A title is now one sentence of at most the 120
characters a trimmed gate keeps (`core.TRIM_TITLE_MAX`); what a long title used to say is in the guide, verbatim,
under its new title (`template.GATE_NOTES`, `RETRO_GATE_NOTES`, `FREEHAND_GATE_NOTES`).

`tests/fixtures/template_before_213/` holds the renders as they were before task 213 — task 213 changed no gate, so
they are also the gates as they were before this task.

Pure stdlib unittest. Run: python3 tests/test_short_gate_titles.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import core, lifecycle, template  # noqa: E402
from tasks.core import PLAYBOOKS, TRIM_TITLE_MAX, _freehand_release_allowed, has_review_evidence  # noqa: E402
from tasks.retro import generate_retro_task  # noqa: E402

BEFORE = _HERE / "fixtures" / "template_before_213"
OLD_RENDERS = {"default": None, "quick": "quick", "light": "light", "bugfix": "bugfix",
               "research": "research", "audit": "audit"}
TYPES = [None, "quick", "light", *PLAYBOOKS]
GATE_RE = re.compile(r"^\s*- \[ \] (.*)$")
CODE_SPAN_RE = re.compile(r"`[^`]*`")
# The sentence rule (plan panel P6): with code spans removed, a `.`, `?` or `!` followed by white space and more text
# starts a second sentence. A title therefore never writes "e.g." — it would count.
BREAK_RE = re.compile(r"[.?!]\s+(?=\S)")
TASKS_CMD_RE = re.compile(r"\.claude/bin/tasks ([a-z][a-z-]*)")
# The titles whose old text was only short sentences made one — nothing else was in it, so the guide has no entry for
# them; the map shows each, old -> new.
REWORDED = {
    "Restate the request in my own words. What does the user actually want?":
        "Restate in your own words what the user actually wants.",
    "What would \"done\" look like? How will we know the task succeeded?":
        "What does \"done\" look like, and how will we know the task succeeded?",
    "Review the work plan. If a likely growth point exists, add it to the plan now.":
        "Review the work plan and add any likely growth point now.",
    "What does \"fixed\" look like? (specific grep, test, or behavior)":
        "What does \"fixed\" look like (a specific grep, test or behavior)?",
    "Side effects: anything else that changed? Adjacent code still works?":
        "Side effects: did anything else change, and does adjacent code still work?",
    "What's the question or hypothesis? State it before looking.":
        "State the question or hypothesis before looking.",
    "When do you stop? (convergence criteria: N rounds with no new position, or specific answer found)":
        "When do you stop (N rounds with no new position, or the specific answer found)?",
    "Checkpoint: converging or scattering? New hypothesis needed?":
        "Checkpoint: converging or scattering, and is a new hypothesis needed?",
    "What did you learn? Key findings with evidence.":
        "What did you learn: the key findings, each with its evidence?",
    "What remains unknown? What would a follow-up task investigate?":
        "What remains unknown, and what would a follow-up task investigate?",
    "How many items? If >5, plan a midpoint checkpoint.":
        "How many items (more than 5: plan a midpoint checkpoint)?",
    "Are you assessing or fixing? Keep them separate — assess first.":
        "Assess first and fix after: keep the two separate.",
    "Midpoint checkpoint: patterns emerging? Abort early or continue?":
        "Midpoint checkpoint: are patterns emerging, and do you stop early or continue?",
}
# Task 225: a one-sentence title reworded because its note now goes under it (the owner's 2026-10-16 "Liste scurte de
# la început") — nothing else was in the old text.
NOTE_REWORDED = {
    "Verify: run the check that proves it — paste the result on this line":
        "Verify: run the check that proves it and paste its result under this gate.",
}
# Task 278 (PHASE-B.md item 9a): the test-strategy gates of the full types REPLACED by the test-contract gate — not a
# reword: what the gate asks is the guide's "Test contract" section and the `## Test contract` it fills.
REPLACED = {
    "Test strategy: what are you testing and how? (point tests for specific behavior, property tests via "
    "`hypothesis` for invariants on transformations/parsers/arithmetic)": template.CONTRACT_GATE,
    "Test strategy: point tests, or also property tests (`hypothesis`) if fixing a parser/formatter/transformation?":
        template.CONTRACT_GATE,
}
# The retro scaffold's (`retro.py`) gates before this task, old -> new; two carried more and are in
# `RETRO_GATE_NOTES`, the rest are rewords.
RETRO_BEFORE = {
    "Read chat_log.md messages for this window (use `.claude/bin/tasks log` for a compact one-line-per-message view, "
    "or filter by message IDs listed above). Summarize: what was the user's arc? What themes dominated? What "
    "frustrations surfaced?": None,
    "Identify steering moments — where did the user correct the agent? What was the nature of each correction "
    "(misframed intent / over-engineering / wrong abstraction / missing context / process friction)?": None,
    "What inefficiencies show up? How many messages did simple things take? Where did the conversation loop?":
        "What inefficiencies show up: simple things that took many messages, loops in the conversation?",
    "For each facet: trace the chain. Did each task build on the previous? Did the work converge or scatter?":
        "For each facet, trace the chain: did each task build on the previous, did the work converge or scatter?",
    "Which investments paid off? Which were abandoned? Any patterns in what succeeds vs what stalls?":
        "Which investments paid off, which were abandoned, and what separates what succeeds from what stalls?",
    "For each mind map node: does it carry information that would cost an agent significant time to rediscover? If "
    "not, compress or remove.":
        "For each mind map node: would it cost an agent real time to rediscover, and if not, compress or remove it?",
    "Apply edits. Keep MIND_MAP.md ≤25KB (the load budget); push full node text to MIND_MAP_OVERFLOW.md.":
        "Apply the edits, keeping MIND_MAP.md under its 25 KB load budget (full node text in MIND_MAP_OVERFLOW.md).",
    "Synthesize: what worked well in this window? What needs to change?":
        "Synthesize what worked well in this window and what needs to change.",
}


def gates(text: str) -> "list[str]":
    return [m.group(1).rstrip() for m in map(GATE_RE.match, text.split("\n")) if m]


def sentences(title: str) -> int:
    return len(BREAK_RE.findall(CODE_SPAN_RE.sub("``", title))) + 1


def old_to_new() -> "dict[str, str]":
    """Every template title that changed, old -> new, by position in each before-213 render."""
    out = {}
    for name, ty in OLD_RENDERS.items():
        old = gates((BEFORE / f"render-{name}.md").read_text(encoding="utf-8"))
        new = gates(template.render_template(1, "X", ty))
        assert len(old) == len(new), name
        out.update({o: n for o, n in zip(old, new) if o != n})
    return out


def retro_text(parked: bool = False, intent: str = "") -> str:
    task = {"number": 1, "title": "X", "status": "done", "gate_count": 0, "checked_count": 0,
            "bare_checkmark_count": 0, "parked_items": [], "playbook_type": "build", "intent": intent}
    gc = {"pending": [], "loose_ends": [],
          "parked": [{"task": 1, "status": "waiting", "text": "p" * 300}] if parked else []}
    return generate_retro_task([task], [], [], [], gc)


def retro_fixed_gates(text: str) -> "list[str]":
    """The scaffold's own gates — not the data gates: one per task (`**T001** …`) and one per parked item
    (`T001 [waiting]: …`), which carry a record's title, intent or text."""
    return [t for t in gates(text) if not re.match(r"(\*\*)?T\d{3}\b", t)]


class EveryGateIsOneShortSentence(unittest.TestCase):

    def assertShort(self, title: str, where: str):
        with self.subTest(where=where, title=title[:60]):
            self.assertLessEqual(len(title), TRIM_TITLE_MAX, title)
            self.assertEqual(sentences(title), 1, title)

    def test_every_render(self):
        for ty in TYPES:
            for title in gates(template.render_template(1, "X", ty)):
                self.assertShort(title, f"render {ty}")

    def test_the_measured_before_and_after(self):
        """The feature template: 25 gates, 7 over 200 characters, the longest 612 (task 218's measure)."""
        old = gates((BEFORE / "render-default.md").read_text(encoding="utf-8"))
        self.assertEqual((len(old), sum(len(t) > 200 for t in old), max(map(len, old))), (25, 7, 612))
        new = gates(template.render_template(1, "X", "feature"))
        self.assertEqual(len(new), 25)
        self.assertLessEqual(max(map(len, new)), TRIM_TITLE_MAX)

    def test_the_retro_scaffold_fixed_gates(self):
        text = retro_text(parked=True, intent="i" * 100)
        fixed = retro_fixed_gates(text)
        self.assertEqual(len(fixed), 14)
        for title in fixed:
            self.assertShort(title, "retro")
        # the two data gates are there and outside the rule: a stored title, intent or parked text is never cut
        data = [t for t in gates(text) if t not in fixed]
        self.assertEqual(len(data), 2, data)
        self.assertTrue(any(len(t) > TRIM_TITLE_MAX for t in data), data)

    def test_the_sentence_rule_bites(self):
        """Must-fail controls: today's two-question titles as they were (plan panel P6)."""
        for old in REWORDED:
            with self.subTest(old=old[:50]):
                self.assertEqual(sentences(old), 2, old)
        self.assertEqual(sentences("Run `a. B` then read `judge.md`."), 1)
        self.assertEqual(sentences("Read MIND_MAP.md and judge.md in place."), 1)


class TheFreehandGates(unittest.TestCase):
    """Both `tasks freehand` paths — a new freehand task, and the block inserted into the active task — and
    `tasks freehand log` finding its gate (plan panel P4)."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb223fh-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        subprocess.run(["git", "init", "-q"], cwd=self.d, check=True)
        (self.d / ".agent" / "tasks").mkdir(parents=True)
        (self.d / "home").mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        self.env.update({"PYTHONPATH": str(_PLAYBOOK), "HOME": str(self.d / "home"),
                         "PLAYBOOK_SESSION_ID": "pid-223-freehand", "PYTHONDONTWRITEBYTECODE": "1"})
        self.env.pop("BASH_ENV", None)

    def cli(self, *args):
        r = subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.d, env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def freehand_gates(self) -> "list[str]":
        out = []
        for f in sorted((self.d / ".agent" / "tasks").glob("*/task.md")):
            block = f.read_text(encoding="utf-8").split("### Freehand\n", 1)[1]
            out += gates(block)[:4]                 # the block's four gates (a section's own gates may follow)
        return out

    def test_both_paths_write_short_gates_and_the_log_finds_its_gate(self):
        made = self.cli("freehand")                            # no active task: a freehand task of its own
        self.cli("new", "light", "probe")
        self.cli("work", "2", "--force")                       # task 1 keeps its open freehand gates
        inserted = self.cli("freehand")                        # the block inserted into task 2
        # a freehand block has no pointer: both paths print the guide its gates' explanations are in (impl round 1)
        for r in (made, inserted):
            self.assertIn("=== TASK GUIDE (again: tasks guide freehand) ===\n" + template.guide_text("freehand"),
                          r.stdout)
        written = self.freehand_gates()
        self.assertEqual(len(written), 8, written)
        for title in written:
            with self.subTest(title=title[:60]):
                self.assertLessEqual(len(title), TRIM_TITLE_MAX, title)
                self.assertEqual(sentences(title), 1, title)
        notes = {t for t, _old in template.FREEHAND_GATE_NOTES}
        self.assertEqual(sum(t in notes for t in written), 4)
        # both blocks name their guide in the file (task 225; grok, 223's impl round 2)
        for f in sorted((self.d / ".agent" / "tasks").glob("*/task.md")):
            with self.subTest(file=f.parent.name):
                self.assertIn(lifecycle._FREEHAND_POINTER, f.read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        (self.d / ".agent" / "chat_log.md").write_text(
            f"# Project Chat Log\n\n---\n\n**[M001]** [{now}] `HOST` (claude/pid-223-freehand)\n\nfreehand work\n",
            encoding="utf-8")
        self.cli("freehand", "log")
        text = next((self.d / ".agent" / "tasks").glob("002-*/task.md")).read_text(encoding="utf-8")
        log_at = text.index("- [ ] Freehand log — run `.claude/bin/tasks freehand log`")
        self.assertIn("M001", text[log_at:])


class TheRetroCommand(TheFreehandGates):
    """`tasks retro` prints the guide its record's pointer names — what a retro gate used to say is there."""

    def test_the_retro_prints_its_guide_and_points_at_it(self):
        self.cli("new", "quick", "probe")
        r = self.cli("retro")
        self.assertIn("=== TASK GUIDE (again: tasks guide retro) ===\n" + template.guide_text("retro") + "\n\n", r.stdout)
        text = next((self.d / ".agent" / "tasks").glob("002-retro-*/task.md")).read_text(encoding="utf-8")
        self.assertIn("`.claude/bin/tasks guide retro`", text)
        for title, _old in template.RETRO_GATE_NOTES:
            self.assertIn(f"- [ ] {title}\n", text)

    test_both_paths_write_short_gates_and_the_log_finds_its_gate = None


class TheGuideHoldsWhatTheTitlesSaid(unittest.TestCase):

    def test_a_type_guide_renders_its_notes_once(self):
        """Task 225 (223's impl round 2, sonnet): `gate_notes_text` renders the type — once per guide."""
        with mock.patch.object(template, "gate_notes_text", wraps=template.gate_notes_text) as spy:
            template.guide_text("feature")
        self.assertEqual(spy.call_count, 1)

    def test_the_arena_config_is_read_and_closed(self):
        """Task 225 (agy, 223's impl round 2, [PRE-EXISTING]): no file is left open by the eval switch."""
        import json as _json
        import warnings
        d = Path(tempfile.mkdtemp(prefix="pb225eval-"))
        self.addCleanup(shutil.rmtree, d, True)
        cfg = d / "eval.json"
        cfg.write_text(_json.dumps({"sticker": "off"}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"PLAYBOOK_EVAL_CONFIG": str(cfg)}), warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", ResourceWarning)
            text = template.render_template(1, "X", "feature")
        self.assertNotIn(template.pointer("feature"), text)            # the switch was read
        self.assertEqual([x for x in w if issubclass(x.category, ResourceWarning)], [])

    def test_every_changed_title_is_in_the_guide_or_a_plain_reword(self):
        guide = template.guide_text()
        changed = old_to_new()
        self.assertEqual(len(changed), 29)
        plain = {**REWORDED, **NOTE_REWORDED}
        for old, new in changed.items():
            with self.subTest(old=old[:60]):
                if old in REPLACED:
                    self.assertEqual(REPLACED[old], new)
                    self.assertNotIn(old, guide)
                    self.assertIn("### Test contract\n", guide)
                elif old in plain:
                    self.assertEqual(plain[old], new)
                    self.assertNotIn(old, guide)
                else:
                    self.assertIn(f"> **{new}**\n> {old}", guide)
        self.assertEqual(len(changed) - len(plain) - len(REPLACED), len(template.GATE_NOTES))

    def test_the_retro_gates(self):
        new = retro_fixed_gates(retro_text())
        guide = template.guide_text("retro")
        notes = dict(template.RETRO_GATE_NOTES)
        for old, reworded in RETRO_BEFORE.items():
            with self.subTest(old=old[:60]):
                self.assertNotIn(old, new)
                if reworded is None:
                    title = next(t for t, o in template.RETRO_GATE_NOTES if o == old)
                    self.assertIn(title, new)
                    self.assertIn(f"> **{title}**\n> {old}", guide)
                else:
                    self.assertIn(reworded, new)
                    self.assertGreaterEqual(sentences(old), 2, old)
        self.assertEqual(len(notes), 2)

    def test_the_freehand_gates_in_the_guide(self):
        guide = template.guide_text("freehand")
        for title, old in template.FREEHAND_GATE_NOTES:
            with self.subTest(title=title[:60]):
                self.assertIn(f"> **{title}**\n> {old}", guide)
                self.assertIn(f"- [ ] {title}\n", lifecycle._FREEHAND_LOG_GATE + lifecycle._FREEHAND_RENAME_GATE)

    def test_no_scaffold_line_becomes_guide_text(self):
        """A line the retro scaffold writes that is also a guide line would be template text the trim drops from
        every retro record (plan panel P3, grok)."""
        guide = set(template.guide_text().split("\n"))
        for ln in retro_text(parked=True).split("\n"):
            with self.subTest(line=ln[:60]):
                self.assertFalse(ln.strip() and ln in guide and ln.startswith(">") and "tasks guide" not in ln, ln)

    def test_the_pointer_promises_gate_notes_only_where_the_guide_has_them(self):
        """Impl round 1 (agy): quick's guide has no Gates section, so its pointer does not name one."""
        for ty in [*TYPES, "retro"]:
            with self.subTest(type=ty):
                self.assertEqual("what each gate asks" in template.pointer(ty),
                                 bool(template.gate_notes_text(ty or "feature")))

    def test_each_note_belongs_to_a_rendered_gate(self):
        rendered = {t for ty in TYPES for t in gates(template.render_template(1, "X", ty))}
        for title, old in template.GATE_NOTES:
            with self.subTest(title=title[:60]):
                self.assertIn(title, rendered)
                self.assertNotEqual(title, old)

    def test_a_type_guide_shows_its_own_gates_only(self):
        for ty in TYPES:
            own = set(gates(template.render_template(1, "X", ty)))
            text = template.guide_text(ty)
            for title, _old in template.GATE_NOTES:
                with self.subTest(type=ty, title=title[:50]):
                    self.assertEqual(f"> **{title}**" in text, ty is None or title in own)

    def test_a_title_keeps_every_command_the_guide_does_not_hold(self):
        """Plan panel P5 (sonnet): a command the old title named is in the new title, or the old text is in the
        guide the task file's pointer names."""
        pairs = [(o, n, template.guide_text()) for o, n in old_to_new().items()]
        pairs += [(o, t, template.guide_text("retro")) for t, o in template.RETRO_GATE_NOTES]
        pairs += [(o, t, "") for t, o in template.FREEHAND_GATE_NOTES]   # a freehand block has no pointer
        for old, new, guide in pairs:
            for cmd in TASKS_CMD_RE.findall(old):
                with self.subTest(old=old[:50], cmd=cmd):
                    self.assertTrue(f".claude/bin/tasks {cmd}" in new or f"> {old}" in guide, (cmd, new))


class TheTitlesReadAsBefore(unittest.TestCase):
    """What a checked or open gate MEANS to the CLI and the hooks stays the same."""

    def _evidence(self, line: str) -> "tuple[bool, bool]":
        d = Path(tempfile.mkdtemp(prefix="pb223ev-"))
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "task.md"
        f.write_text(f"# 001 - X\n\n## Status\nin_progress\n\n## Work Plan\n- [x] {line}\n", encoding="utf-8")
        return has_review_evidence(f), has_review_evidence(f, impl_only=True)

    def test_review_evidence_parity_old_vs_new(self):
        for old, new in old_to_new().items():
            with self.subTest(old=old[:60]):
                self.assertEqual(self._evidence(old), self._evidence(new))

    def test_the_panel_gates_are_no_evidence(self):
        # task 290 (audit 1 G2): ticked, the impl panel gate closed a task with no judge; no tick is evidence now
        plan, impl = (t for t in gates(template.render_template(1, "X", "feature")) if t.startswith("Run the "))
        self.assertEqual(self._evidence(plan), (False, False))
        self.assertEqual(self._evidence(impl), (False, False))

    def test_the_freehand_readers(self):
        """The CLI's release rule, the codex hooks' and the bash hook's patterns, on the new titles (plan panel P4)."""
        from provider.codex_hooks import _FREEHAND_RE
        text = template.render_template(1, "X", "feature")
        lines = [ln if "Freehand" in ln else ln.replace("- [ ]", "- [x]") for ln in text.split("\n")]
        debrief = next(t for t in gates(text) if t.startswith("Freehand"))
        log = lifecycle._FREEHAND_LOG_GATE[len("- [ ] "):].rstrip("\n")
        self.assertTrue(_freehand_release_allowed(lines))
        self.assertFalse(_freehand_release_allowed([ln.replace(f"- [ ] {debrief}", f"- [ ] {log}") for ln in lines]))
        self.assertTrue(_FREEHAND_RE.match(debrief))
        self.assertFalse(_FREEHAND_RE.match(log))
        hook = (_PLAYBOOK / "scripts" / "state-echo-hook").read_text(encoding="utf-8")
        case = re.search(r'\n(\s*case "\$GATE_TEXT_FULL" in\n.*?\n\s*esac)', hook, re.S)
        self.assertIsNotNone(case, "the state-echo hook's Freehand case moved")
        for title, want in ((debrief, "freehand"), (log, "gate")):
            with self.subTest(title=title[:40]):
                r = subprocess.run(["bash", "-c", 'GATE_TEXT_FULL="$1"; IS_FREEHAND=false; ' + case.group(1)
                                    + '\n[ "$IS_FREEHAND" = true ] && echo freehand || echo gate', "_", title],
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(r.stdout.strip(), want, r.stderr)


class TheTrimReadsTheNewTitles(unittest.TestCase):
    """Plan panel P7 (grok): a template title is the start of a gate only before its note — an agent's own gate
    that merely begins with one keeps its own first 120 characters."""

    def _trim(self, body: str) -> str:
        return core._trim_notes([(1, "## Work\n"), (2, f"- [x] {body}\n")], 0, set())[1][1]

    def test_an_agent_gate_starting_with_a_template_title_keeps_its_own_words(self):
        own = ("Do the work in lifecycle.py and prove the freehand log still inserts under the new gate, then "
               "measure it")
        self.assertIn(own[:TRIM_TITLE_MAX], self._trim(f"{own} — note " + "n" * 300))

    def test_where_a_note_starts(self):
        """Impl round 1 (agy, opus, sonnet): the rest is a note only at a note start — or after the title's own end
        mark; a word glued to the title, a comma or a hyphenated continuation is the agent's own title."""
        q = "What is OUT of scope for this task?"
        cases = [
            (f"{q} None, the map stays as it is.", q),               # a question answered in plain words
            ("Do the work — 02:06: built it", "Do the work"),
            ("Do the work: built it", "Do the work"),
            ("Do the work - built it", "Do the work"),
            ("Do the work (part 2)", "Do the work"),
            ("Do the work [see W4]", "Do the work"),
            ("Do the work", "Do the work"),
            ("Do the work-around for issue 42 in lifecycle.py", None),
            ("Do the work, then measure the echo in the second project", None),
            ("Do the work in lifecycle.py and prove it", None),
            ("Do the workflow review", None),
        ]
        for body, want in cases:
            with self.subTest(body=body):
                self.assertEqual(core._template_title_of(body), want)

    def test_a_filled_in_placeholder_keeps_its_own_words(self):
        """Task 225 (223's impl round 2, grok): `)` is no end mark — a fill-in gate completed on its line is the
        agent's own title, not the placeholder's."""
        body = "Fix: (what to change) make _template_title_of keep the filled-in text"
        self.assertIsNone(core._template_title_of(body))
        self.assertIn(body[:TRIM_TITLE_MAX], self._trim(body + " — note " + "n" * 300))
        self.assertEqual(core._template_title_of("Fix: (what to change) — done"), "Fix: (what to change)")

    def test_a_dash_glued_to_the_title_starts_the_note(self):
        """Task 225 (223's impl round 2, opus): `Do the work—done` / `Do the work–done` — the note starts at
        the dash, as it does after a space."""
        for dash in ("—", "–"):
            with self.subTest(dash=hex(ord(dash))):
                self.assertEqual(core._template_title_of(f"Do the work{dash}done"), "Do the work")
        self.assertIsNone(core._template_title_of("Do the work-around"))     # a hyphen is a word's, not a note's

    def test_a_template_title_past_the_limit_is_cut_with_an_ellipsis(self):
        """The guard arm for a built-in title past TRIM_TITLE_MAX (223's impl round 2, opus, sonnet): none is today
        (the sweep forbids it), so a long one is planted for the test."""
        long_title = "Run the long thing " + "x" * 200
        with mock.patch.object(core, "_TEMPLATE_GATE_TITLES", (long_title,)):
            out = self._trim(long_title + " — note-start " + "n" * 300)
        self.assertIn(long_title[:TRIM_TITLE_MAX].rstrip() + " …", out)
        self.assertIn("[… note cut", out)

    def test_an_older_records_long_title_reads_like_an_agents(self):
        """The CHANGELOG's stated limit (223's impl round 2, opus): a title from before task 223 is no template title
        now — its first 120 characters, then more of its own text where the note's start used to show."""
        old = max(gates((BEFORE / "render-default.md").read_text(encoding="utf-8")), key=len)
        self.assertGreater(len(old), 300)
        lines = [(1, "## Work\n"), (2, f"- [x] {old} — note-start {'n' * 300}\n")]
        out = core._trim_notes(lines, 40, set())[1][1]
        self.assertTrue(out.startswith("- [x] " + old[:TRIM_TITLE_MAX]), out[:200])
        self.assertNotIn("note-start", out)

    def test_a_template_title_with_its_note_keeps_the_title_whole(self):
        out = self._trim("Do the work — 02:06: " + "n" * 300)
        self.assertTrue(out.startswith("- [x] Do the work [… note cut"), out)
        out = self._trim("Run the plan panel in the background — `.claude/bin/tasks panel-review <N>` — then read "
                         "`judge.md`. — 02:16: " + "n" * 300)
        self.assertIn("then read `judge.md`. [… note cut", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
