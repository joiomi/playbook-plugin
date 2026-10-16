#!/usr/bin/env python3
"""First-task chat attribution (F2): the mandate must not be invisible.

Field evidence (StrataDB batches 1 and 4):

  * The message that DEFINES a project — the owner's mandate — predates
    `tasks new 1` / `tasks work 1` by construction. Every attribution window
    opened at the first task's activation, so the one message the plan judge
    most needs ("check the plan against the user's actual words") was
    unattributable. `tasks context 001` → "no attributed messages".
  * On task 010 the judges' intent-check ran blind for a second reason:
    `tasks context` reads only `<!-- TNNN -->` spans, the spans are written
    only by `tasks tag`, and nothing ever runs `tasks tag` — so context was
    blind for EVERY task even though gate entries + the recorded activations
    contained everything needed to attribute messages (verified against the
    real project: the window fallback returns 3 messages for task 10). Since
    task 221 the activations are the CLI's own records in the journal (UTC),
    not lines of the shell log.

The class fix, at the chokepoints:

  * `build_task_windows` — the earliest-activated task's window opens at the
    epoch, not at its activation (pre-history IS the project seed);
  * `tasks tag` — same rule for the span writer;
  * `tasks context` — falls back to timestamp windows when no spans exist
    (the same fallback `tasks intent`'s chat layer already has), still
    failing loudly when nothing is attributable;
  * `extract_chatlog` — consumes the `(provider/pid)` header suffix instead
    of leaking it into message text (same disease `tasks log` had, fixed in
    1.4.3; the extractor was missed).

Run: python3 -m unittest tests.test_chat_attribution
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins/playbook"
sys.path.insert(0, str(PLUGIN))

from tasks.retro import build_task_windows, extract_chatlog, _attribute_to_task  # noqa: E402

MANDATE = "Build StrataDB, an embeddable zero-dependency document DB. THE-MANDATE-SENTINEL."

CHAT_LOG = f"""# Project Chat Log

User messages logged with timestamps.

---

**[M001]** [2026-08-12 06:44:12 UTC] `HOST` (claude/pid-111)

{MANDATE}

---

**[G001:29]** [2026-08-12 06:47:59 UTC] `HOST` (2 tool calls)

- [x] Understand: read the brief

---

**[M002]** [2026-08-12 06:50:00 UTC] `HOST` (claude/pid-111)

continue with task one

---

**[M003]** [2026-08-12 07:30:00 UTC] `HOST` (claude/pid-111)

message during task two

---
"""

ACTIVATIONS = (("2026-08-12 06:46:00", 1), ("2026-08-12 07:25:25", 2))


def journal_text(activations) -> str:
    """The records `tasks work N` writes (task 221; `pb_journal.append_lifecycle`), stamps in UTC."""
    import json as _json
    return "".join(_json.dumps({"ts": s.replace(" ", "T") + "Z", "session_id": "", "hook": "tasks",
                                "decision": "record", "reason": "work", "task": str(n)},
                               separators=(",", ":")) + "\n" for s, n in activations)


def make_project(chat_log: str = CHAT_LOG, activations=ACTIVATIONS) -> Path:
    proj = Path(tempfile.mkdtemp())
    agent = proj / ".agent"
    (agent / "tasks" / "001-first").mkdir(parents=True)
    (agent / "tasks" / "001-first" / "task.md").write_text(
        "# 001 - First\n\n## Status\npending\n", encoding="utf-8")
    (agent / "chat_log.md").write_text(chat_log, encoding="utf-8")
    if activations is not None:
        (agent / "journal").mkdir()
        (agent / "journal" / "enforcement.jsonl").write_text(journal_text(activations), encoding="utf-8")
    return proj


def run_cli(proj: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PLUGIN)
    return subprocess.run(
        [sys.executable, "-m", "tasks.cli", *args],
        cwd=proj, env=env, capture_output=True, text=True, timeout=60)


class WindowsIncludeTheSeed(unittest.TestCase):
    """`build_task_windows`: pre-history belongs to the earliest-activated task."""

    def _windows(self, proj: Path):
        agent = proj / ".agent"
        return build_task_windows(agent / "chat_log.md", agent / "journal" / "enforcement.jsonl")

    def test_mandate_before_first_activation_attributes_to_first_task(self):
        proj = make_project()
        windows = self._windows(proj)
        # M001 (06:44:12) predates task 1's activation (06:46:00).
        self.assertEqual(_attribute_to_task("2026-08-12 06:44:12 UTC", windows), 1)

    def test_widening_does_not_leak_into_later_tasks(self):
        proj = make_project()
        windows = self._windows(proj)
        # In-window messages keep their attribution (negative control for the
        # widening: only the FIRST window opens early).
        self.assertEqual(_attribute_to_task("2026-08-12 06:50:00 UTC", windows), 1)
        self.assertEqual(_attribute_to_task("2026-08-12 07:30:00 UTC", windows), 2)

    def test_no_activations_yields_no_windows(self):
        # Negative control: with no activation evidence at all, nothing is
        # invented — an empty dict, not a fabricated window.
        proj = make_project(
            chat_log="# Project Chat Log\n\n---\n\n**[M001]** [2026-08-12 06:44:12 UTC] `HOST`\n\nhello\n\n---\n",
            activations=None)
        self.assertEqual(self._windows(proj), {})

    def test_earliest_activated_wins_even_when_numbering_disagrees(self):
        # Task 5 activated before task 2: pre-history belongs to 5 (activation
        # order), not to the lowest number.
        proj = make_project(activations=(("2026-08-12 06:46:00", 5), ("2026-08-12 07:25:25", 2)))
        windows = self._windows(proj)
        self.assertEqual(_attribute_to_task("2026-08-12 06:00:00 UTC", windows), 5)


class ExtractChatlogHeaderSuffix(unittest.TestCase):
    def test_provider_pid_suffix_not_leaked_into_text(self):
        proj = make_project()
        msgs = extract_chatlog(proj / ".agent" / "chat_log.md")
        m1 = next(m for m in msgs if m["id"] == 1)
        self.assertNotIn("claude/pid-111", m1["text"])
        self.assertTrue(m1["text"].startswith("Build StrataDB"), m1["text"][:80])

    def test_legacy_unsuffixed_entries_still_parse(self):
        legacy = ("# Project Chat Log\n\n---\n\n"
                  "**[M001]** [2026-08-12 06:44:12 UTC] `HOST`\n\nplain entry\n\n---\n")
        proj = make_project(chat_log=legacy, activations=None)
        msgs = extract_chatlog(proj / ".agent" / "chat_log.md")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "plain entry")


class TagCommandCoversTheSeed(unittest.TestCase):
    def test_pre_first_activation_messages_are_tagged_to_first_task(self):
        proj = make_project()
        r = run_cli(proj, "tag")
        self.assertEqual(r.returncode, 0, r.stderr)
        tagged = (proj / ".agent" / "chat_log.md").read_text(encoding="utf-8")
        # The mandate (M001) must sit inside the <!-- T001 --> span.
        t1_open = tagged.index("<!-- T001 -->")
        self.assertGreater(tagged.index("THE-MANDATE-SENTINEL"), t1_open)
        # And the widening must not swallow task 2's messages (control).
        t2_open = tagged.index("<!-- T002 -->")
        self.assertGreater(tagged.index("message during task two"), t2_open)
        self.assertLess(tagged.index("THE-MANDATE-SENTINEL"), t2_open)

    def test_context_returns_the_mandate_after_tag(self):
        proj = make_project()
        run_cli(proj, "tag")
        r = run_cli(proj, "context", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("THE-MANDATE-SENTINEL", r.stdout)


class ContextFallsBackToWindows(unittest.TestCase):
    """`tasks context` must not be blind on an untagged project (task 010)."""

    def test_untagged_project_still_returns_messages(self):
        proj = make_project()  # no `tasks tag` run — the field state
        r = run_cli(proj, "context", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("THE-MANDATE-SENTINEL", r.stdout)
        self.assertIn("continue with task one", r.stdout)
        self.assertNotIn("message during task two", r.stdout)

    def test_untagged_later_task_returns_its_window(self):
        proj = make_project()
        r = run_cli(proj, "context", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("message during task two", r.stdout)
        self.assertNotIn("THE-MANDATE-SENTINEL", r.stdout)

    def test_fallback_names_its_source(self):
        # The provenance note goes to stderr; stdout stays pure messages.
        proj = make_project()
        r = run_cli(proj, "context", "1")
        self.assertIn("timestamp window", r.stderr.lower())
        self.assertNotIn("timestamp window", r.stdout.lower())

    def test_truly_unattributable_still_fails_loud(self):
        # Negative control: no gate entries, no recorded activation → the old loud
        # failure survives (the fallback must not fabricate attribution).
        proj = make_project(
            chat_log="# Project Chat Log\n\n---\n\n**[M001]** [2026-08-12 06:44:12 UTC] `HOST`\n\nhello\n\n---\n",
            activations=None)
        r = run_cli(proj, "context", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No attributed messages", r.stderr)


if __name__ == "__main__":
    unittest.main()


class JournalStampsAreUtc(unittest.TestCase):
    """Task 073 (live gauntlet 1.5.44, C12) found the shell log stamped in LOCAL time while chat_log.md is UTC: on a
    UTC+3 machine a message typed 1 s after `tasks work 7` attributed to the previous task. Since task 221 the
    activations are the CLI's journal records, stamped UTC like the chat log — no conversion, in any zone."""

    def _with_tz(self, tz, fn):
        import time
        old = os.environ.get("TZ")
        os.environ["TZ"] = tz
        time.tzset()
        try:
            return fn()
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_a_message_one_second_after_the_activation_belongs_to_it_in_any_zone(self):
        from tasks.retro import build_task_windows, extract_chatlog

        def run():
            d = Path(tempfile.mkdtemp())
            (d / "enforcement.jsonl").write_text(
                journal_text((("2026-09-20 10:00:00", 4), ("2026-09-20 20:43:27", 7))), encoding="utf-8")
            (d / "chat_log.md").write_text(
                "# Project Chat Log\n\n---\n\n"
                "**[M001]** [2026-09-20 20:43:28 UTC] `HOST` (claude/pid-x)\n\n"
                "message during task seven\n\n---\n", encoding="utf-8")
            w = build_task_windows(d / "chat_log.md", d / "enforcement.jsonl")
            msgs = extract_chatlog(d / "chat_log.md", w)
            self.assertEqual([m.get("task") for m in msgs], [7], w)
        for tz in ("Europe/Bucharest", "America/Los_Angeles", "UTC"):
            with self.subTest(tz=tz):
                self._with_tz(tz, run)


class GauntletTaggerAndTies(unittest.TestCase):
    """Impl-panel round 1 (task 073): `tasks tagger` must order an activation among the chat's messages, and an
    exact-second tie in `tasks tag` (`…SS UTC` against `…SS`, sonnet #3) belongs to the new task. The activation
    is the CLI's journal record since task 221."""

    def _proj(self):
        from datetime import datetime, timezone
        d = Path(tempfile.mkdtemp())
        agent = d / ".agent"; (agent / "tasks").mkdir(parents=True)
        (agent / "journal").mkdir()
        (agent / "journal" / "enforcement.jsonl").write_text(journal_text((("2026-09-20 20:43:27", 7),)),
                                                             encoding="utf-8")
        (agent / "chat_log.md").write_text(
            "# Project Chat Log\n\n---\n\n"
            "**[M001]** [2026-09-20 20:43:00 UTC] `HOST` (claude/pid-x)\n\nbefore activation\n\n---\n\n"
            "**[M002]** [2026-09-20 20:43:27 UTC] `HOST` (claude/pid-x)\n\nsame second as activation\n\n---\n\n"
            "**[M003]** [2026-09-20 20:43:28 UTC] `HOST` (claude/pid-x)\n\nafter activation\n\n---\n",
            encoding="utf-8")
        return d

    def _run(self, d, *args):
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="pid-x")
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=d, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_tagger_orders_an_activation_among_the_messages(self):
        out = self._run(self._proj(), "tagger").stdout.splitlines()
        i_act = next(i for i, l in enumerate(out) if "tasks work 7" in l)
        i_before = next(i for i, l in enumerate(out) if "before activation" in l)
        i_after = next(i for i, l in enumerate(out) if "after activation" in l)
        self.assertLess(i_before, i_act, out)
        self.assertLess(i_act, i_after, out)

    def test_tag_attributes_the_same_second_message_to_the_new_task(self):
        d = self._proj()
        r = self._run(d, "tag")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = (d / ".agent" / "chat_log.md").read_text(encoding="utf-8")
        self.assertLess(log.index("<!-- T007 -->"), log.index("**[M002]**"), log)
