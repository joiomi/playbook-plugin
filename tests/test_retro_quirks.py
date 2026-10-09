#!/usr/bin/env python3
"""Retro generator quirks (task 018 / bug report #5).

5a: bare-checkmark heuristic must not false-positive on gates whose annotation
    lives on indented continuation lines (numbered sub-bullets, `→` lines).
5b: `tasks log` must parse chat-log entries that carry the `(provider/pid)`
    suffix (added by multi-provider tagging) — and legacy entries without it.

Pure stdlib unittest. Run: python3 tests/test_retro_quirks.py
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_PLUGIN = _HERE.parent / "plugins/playbook"
sys.path.insert(0, str(_PLUGIN))

from tasks.retro import _parse_task, _gate_has_continuation  # noqa: E402
from tasks import cli as tcli  # noqa: E402


class BareCheckmarkTest(unittest.TestCase):
    def test_continuation_annotated_gates_not_bare(self):
        content = (
            "# 001\n## Design Phase\n"
            "- [x] Fix — 4 changes:\n"
            "  1. did a\n"
            "  2. did b\n"
            "- [x] Verify results\n"
            "  → ran the suite, 12 pass\n"
            "- [x] A genuinely bare gate\n"
            "- [ ] not checked\n"
        )
        r = _parse_task(1, "x", content)
        self.assertEqual(r["checked_count"], 3)
        self.assertEqual(r["bare_checkmark_count"], 1)  # only the genuinely bare one

    def test_all_bare_template_gates_still_counted(self):
        content = ("# 002\n## Design Phase\n"
                   "- [x] Understand\n- [x] Structure\n- [x] Verify\n")
        r = _parse_task(2, "x", content)
        self.assertEqual(r["bare_checkmark_count"], 3)

    def test_arrow_continuation_any_indent(self):
        lines = ["- [x] Do the thing", "→ outcome on next line", "- [ ] next"]
        self.assertTrue(_gate_has_continuation(lines, 0, 0))

    def test_deeper_indent_is_continuation(self):
        lines = ["- [x] header", "    some indented detail", "- [ ] next"]
        self.assertTrue(_gate_has_continuation(lines, 0, 0))

    def test_next_gate_same_indent_is_not_continuation(self):
        lines = ["- [x] bare gate", "- [x] another bare gate"]
        self.assertFalse(_gate_has_continuation(lines, 0, 0))

    def test_same_indent_prose_is_not_continuation(self):
        lines = ["- [x] bare gate", "prose at column zero"]
        self.assertFalse(_gate_has_continuation(lines, 0, 0))

    def test_blank_then_continuation(self):
        lines = ["- [x] header", "", "  1. sub-bullet after a blank"]
        self.assertTrue(_gate_has_continuation(lines, 0, 0))

    def test_blank_then_next_gate_not_continuation(self):
        lines = ["- [x] bare", "", "- [x] next bare"]
        self.assertFalse(_gate_has_continuation(lines, 0, 0))


class TasksLogTest(unittest.TestCase):
    """`tasks log` must parse both suffixed (`HOST` (provider/pid)) and legacy
    (bare backticked provider) chat-log entries (bug report #5b)."""

    def _run_log(self, chat_log_text, *args):
        proj = Path(tempfile.mkdtemp())
        (proj / ".agent" / "tasks").mkdir(parents=True)
        (proj / ".agent" / "chat_log.md").write_text(chat_log_text, encoding="utf-8")
        buf = io.StringIO()
        cwd = os.getcwd()
        os.chdir(proj)
        try:
            with mock.patch.object(sys, "argv", ["tasks", "log", *args]), \
                 redirect_stdout(buf):
                try:
                    tcli.main()
                except SystemExit:
                    pass
        finally:
            os.chdir(cwd)
        return buf.getvalue()

    def test_parses_suffixed_entries(self):
        log = (
            "# Chat Log\n\n---\n\n"
            "**[M001]** [2026-07-01 10:00:00 UTC] `HOST` (claude/pid-35089)\n\nhello world\n\n"
            "---\n\n"
            "**[M002]** [2026-07-01 10:05:00 UTC] `HOST` (codex/pid-win-fallback)\n\nsecond msg\n"
        )
        out = self._run_log(log)
        self.assertIn("[M001]", out)
        self.assertIn("hello world", out)
        self.assertIn("claude", out)     # provider from suffix, not "HOST"
        self.assertIn("codex", out)
        self.assertNotIn("HOST", out)    # backticked field is superseded by provider

    def test_parses_legacy_unsuffixed_entries(self):
        # Pre-suffix format: the backticked field WAS the provider, no ` (…)`.
        log = ("**[M001]** [2026-05-01 09:00:00 UTC] `claude`\n\nlegacy entry\n")
        out = self._run_log(log)
        self.assertIn("[M001]", out)
        self.assertIn("legacy entry", out)
        self.assertIn("claude", out)

    def test_mixed_log_both_parse(self):
        log = (
            "**[M001]** [2026-05-01 09:00:00 UTC] `claude`\n\nold\n\n---\n\n"
            "**[M002]** [2026-07-01 10:00:00 UTC] `HOST` (codex/pid-1)\n\nnew\n"
        )
        out = self._run_log(log)
        self.assertIn("old", out)
        self.assertIn("new", out)
        self.assertEqual(len([ln for ln in out.splitlines() if ln.startswith("[M")]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class GauntletRetroType(unittest.TestCase):
    def test_light_task_is_not_typed_quick(self):
        # Task 073 finding C16: the retro table typed a `light` task as `quick`
        # (both lack `## Design Phase`; light has `## Risk Routing`).
        from tasks.retro import _detect_type
        light = "# 002 - Second\n\n## Status\npending\n\n## Risk\nunclassified\n\n## Risk Routing\n- [ ] risk\n\n## Work\n- [ ] Do the work\n"
        quick = "# 001 - First\n\n## Status\npending\n\n## Work\n- [ ] Do the work\n"
        self.assertEqual(_detect_type(light), "light")
        self.assertEqual(_detect_type(quick), "quick")


class DefaultWindow(unittest.TestCase):
    """Task 145 (retro 134 (b)): a bare `tasks retro` read the WHOLE history while the
    close-time nudge counts the tasks closed since the last retro. The default window
    is now the tasks after the last retro, and the command says which window it used."""

    def _project(self, numbered):
        proj = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, proj, True)
        for num, slug in numbered:
            td = proj / ".agent" / "tasks" / f"{num:03d}-{slug}"
            td.mkdir(parents=True)
            (td / "task.md").write_text(
                f"# {num:03d} - {slug}\n\n## Status\ndone (2026-09-01)\n\n## Risk\nreversible\n\n"
                "## Work\n- [x] Do the work — did it\n", encoding="utf-8")
        return proj

    def _retro(self, proj, *args, env=None):
        import subprocess
        extra = env or {}
        env = dict(os.environ, PYTHONPATH=str(_PLUGIN), PLAYBOOK_SESSION_ID="pid-retro-window", **extra)
        env.pop("BASH_ENV", None)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "retro", *args], cwd=proj, env=env,
                           capture_output=True, text=True, timeout=60)
        made = sorted((proj / ".agent" / "tasks").glob("*-retro-*"))
        return r, made[-1].name if made else ""

    def test_a_bare_retro_starts_after_the_last_retro(self):
        proj = self._project([(1, "a"), (2, "b"), (3, "retro-001-002"), (4, "c"), (5, "d")])
        r, made = self._retro(proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(made, "006-retro-004-005")
        self.assertIn("after retro T003", r.stdout)
        self.assertIn("--since", r.stdout)

    def test_since_zero_still_reads_everything(self):
        proj = self._project([(1, "a"), (2, "retro-001-001"), (3, "c")])
        r, made = self._retro(proj, "--since", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(made, "004-retro-001-003")

    def test_with_no_retro_yet_a_bare_retro_reads_everything(self):
        proj = self._project([(1, "a"), (2, "b")])
        r, made = self._retro(proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(made, "003-retro-001-002")

    def test_a_task_whose_name_starts_with_retro_is_not_a_retro(self):
        # impl panel r1 (opus, codex-high, agy): any slug starting `retro` counted — THIS task,
        # `145-retro-window-after-last-retro`, would have been taken for the last retro
        proj = self._project([(1, "a"), (2, "retro-001-001"), (3, "retro-window-x"), (4, "retrofit-api")])
        r, made = self._retro(proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(made, "005-retro-003-004")
        self.assertIn("after retro T002", r.stdout)

    def test_with_no_retro_yet_the_window_is_named_too(self):
        # impl panel r1 (opus, codex-medium, agy): the window was only named after a retro
        proj = self._project([(1, "a"), (2, "b")])
        r, _made = self._retro(proj)
        self.assertIn("Window: all tasks", r.stdout)

    CHAT = ("# Project Chat Log\n\n"
            "**[M001]** [2026-10-01 10:30:00 UTC] `HOST`\n\nbefore the last retro\n\n---\n\n"
            "**[M002]** [2026-10-01 12:30:00 UTC] `HOST`\n\nthe retro's own discussion\n\n---\n\n"
            "**[G002:1]** [2026-10-01 13:00:00 UTC] gate\n\n---\n\n"
            "**[M003]** [2026-10-02 10:30:00 UTC] `HOST`\n\nin the window\n\n---\n")

    def _stamp(self, proj, retro_dir, ts):
        tf = proj / ".agent" / "tasks" / retro_dir / "task.md"
        lines = tf.read_text(encoding="utf-8").split("\n", 1)
        tf.write_text(lines[0] + f"\n<!-- retro-generated: {ts} -->\n" + lines[1], encoding="utf-8")

    def test_a_bare_retro_reads_the_chat_from_when_the_last_retro_was_made(self):
        # impl panel r2 re-run (opus, codex x2): windowing by task ATTRIBUTION dropped the retro
        # session's own discussion and lost chat with no attribution; post-D6 run 1 (codex): a
        # gate entry comes AFTER the retro's opening discussion, and the first task's window
        # starts in year 0000. The boundary is the time the last retro was GENERATED.
        proj = self._project([(1, "a"), (2, "retro-001-001"), (3, "c")])
        self._stamp(proj, "002-retro-001-001", "2026-10-01 12:00:00 UTC")
        (proj / ".agent" / "chat_log.md").write_text(self.CHAT, encoding="utf-8")
        r, made = self._retro(proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(made, "004-retro-003-003")
        self.assertIn("1 tasks in window, 2 chat messages", r.stdout)
        text = (proj / ".agent" / "tasks" / made / "task.md").read_text(encoding="utf-8")
        self.assertIn("M002–M003", text)
        self.assertRegex(text.split("\n", 2)[1], r"^<!-- retro-generated: \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC -->$")
        r, _ = self._retro(proj, "--since", "0")
        self.assertIn("3 chat messages", r.stdout)

    def test_an_older_retro_is_bounded_by_its_activation_in_the_shell_history(self):
        proj = self._project([(1, "a"), (2, "retro-001-001"), (3, "c")])
        (proj / ".agent" / "chat_log.md").write_text(self.CHAT, encoding="utf-8")
        (proj / ".agent" / "bash_history").write_text(
            "2026-10-01 12:00:00 | HOST | .claude/bin/tasks work 2\n", encoding="utf-8")
        r, _ = self._retro(proj, env={"TZ": "UTC"})
        self.assertIn("2 chat messages", r.stdout)

    def test_a_search_for_the_command_is_not_an_activation(self):
        # post-D6 run 2 (codex): `rg "tasks work 2"` matched the fallback and moved the boundary back
        proj = self._project([(1, "a"), (2, "retro-001-001"), (3, "c")])
        (proj / ".agent" / "chat_log.md").write_text(self.CHAT, encoding="utf-8")
        (proj / ".agent" / "bash_history").write_text(
            '2026-10-01 09:00:00 | HOST | rg "tasks work 2" .agent/bash_history\n'
            "2026-10-01 09:30:00 | HOST | echo remember: tasks work 2 later\n"
            "2026-10-01 12:00:00 | HOST | .claude/bin/tasks work 2\n", encoding="utf-8")
        r, _ = self._retro(proj, env={"TZ": "UTC"})
        self.assertIn("2 chat messages", r.stdout)

    def test_the_stamp_is_taken_before_the_chat_is_read(self):
        # post-D6 run 2 (codex): a message appended between the read and a LATER stamp fell into
        # neither retro. The stamp is taken first, so the next window starts no later than the read.
        from unittest import mock
        import contextlib
        import io
        from tasks import history, retro as _retro
        proj = self._project([(1, "a"), (2, "b")])
        seen = []
        real = _retro.extract_chatlog

        def read_then_note(*a, **k):
            seen.append(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
            __import__("time").sleep(1.2)          # a stamp taken AFTER the read is then visibly later
            return real(*a, **k)
        prev = os.getcwd()
        os.chdir(proj)
        self.addCleanup(os.chdir, prev)
        with mock.patch.object(_retro, "extract_chatlog", read_then_note), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            history.cmd_retro([])
        (made,) = sorted((proj / ".agent" / "tasks").glob("*-retro-*/task.md"))
        import re as _re
        stamp = _re.search(r"<!-- retro-generated: (.+?) UTC -->", made.read_text(encoding="utf-8")).group(1)
        self.assertLessEqual(stamp, seen[0].strftime("%Y-%m-%d %H:%M:%S"))

    def test_a_retro_whose_time_is_unknown_keeps_the_whole_chat_and_says_so(self):
        # a gate entry alone is no boundary: it can come after the retro's opening discussion
        proj = self._project([(1, "a"), (2, "retro-001-001"), (3, "c")])
        (proj / ".agent" / "chat_log.md").write_text(self.CHAT, encoding="utf-8")
        r, _ = self._retro(proj)
        self.assertIn("3 chat messages", r.stdout)
        self.assertIn("not windowed", r.stdout)

    def test_the_listed_ids_are_exactly_the_kept_messages(self):
        # post-D6 run 1 (codex): ids and timestamps need not agree in order — a range M001–M003
        # pulled an excluded M002 back in
        proj = self._project([(1, "a"), (2, "retro-001-001"), (3, "c")])
        self._stamp(proj, "002-retro-001-001", "2026-10-01 12:00:00 UTC")
        (proj / ".agent" / "chat_log.md").write_text(
            "# Project Chat Log\n\n"
            "**[M001]** [2026-10-01 12:10:00 UTC] `HOST`\n\nin\n\n---\n\n"
            "**[M002]** [2026-10-01 11:59:00 UTC] `HOST`\n\nout (older)\n\n---\n\n"
            "**[M003]** [2026-10-01 12:20:00 UTC] `HOST`\n\nin\n\n---\n", encoding="utf-8")
        r, made = self._retro(proj)
        text = (proj / ".agent" / "tasks" / made / "task.md").read_text(encoding="utf-8")
        self.assertIn("M001, M003", text)
        self.assertNotIn("M001–M003", text)

    def test_an_explicit_since_keeps_the_whole_chat_as_before(self):
        # sonnet: only the DEFAULT window narrows the chat; `--since N` behaves as it always did
        proj = self._project([(1, "a"), (2, "retro-001-001"), (3, "c")])
        self._stamp(proj, "002-retro-001-001", "2026-10-01 12:00:00 UTC")
        (proj / ".agent" / "chat_log.md").write_text(self.CHAT, encoding="utf-8")
        r, _ = self._retro(proj, "--since", "3")
        self.assertIn("3 chat messages", r.stdout)

    def _with_retro_table(self, proj, retro_dir, rows):
        tf = proj / ".agent" / "tasks" / retro_dir / "task.md"
        table = "| # | Title | Status | Gates | Bare | Type |\n|---|-------|--------|-------|------|------|\n"
        table += "".join(f"| {n:03d} | T{n} | {st} | 1/1 | 0 | light |\n" for n, st in rows)
        tf.write_text(tf.read_text(encoding="utf-8") + "\n## Tasks in window\n\n" + table, encoding="utf-8")

    def test_a_task_still_open_at_the_last_retro_is_carried_into_the_next(self):
        # impl panel r2 (opus): the window was by NUMBER — retro 134 here saw 14 tasks (114-133)
        # `blocked`, all closed later, and no later default window would ever have read them
        proj = self._project([(1, "a"), (2, "b"), (3, "retro-001-002"), (4, "c")])
        self._with_retro_table(proj, "003-retro-001-002", [(1, "done"), (2, "blocked")])
        from tasks.core import count_tasks_since_retro
        self.assertEqual(count_tasks_since_retro(proj), (2, 3))        # T004 and the carried T002
        r, made = self._retro(proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(made, "005-retro-002-004")
        self.assertIn("T002", r.stdout)

    def test_nothing_since_the_last_retro_says_so(self):
        proj = self._project([(1, "a"), (2, "retro-001-001")])
        r, made = self._retro(proj)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("after retro T002", r.stderr)
        self.assertEqual(made, "002-retro-001-001")                  # no new retro

    # PLAN S11 item 2 (task 165; task 145 panel r2, codex-high). A task the last retro
    # recorded as unfinished is carried into the next window — so with one such task
    # EVERY further bare `tasks retro` made one more retro of it (measured on a scratch
    # project: 005-retro-002-002, then 006-retro-002-002, with no end).

    def _set_status(self, proj, num, status):
        import re
        tf = next((proj / ".agent" / "tasks").glob(f"{num:03d}-*/task.md"))
        tf.write_text(re.sub(r"(## Status\n)[^\n]*", lambda m: m.group(1) + status,
                             tf.read_text(encoding="utf-8"), count=1), encoding="utf-8")

    def _retros(self, proj):
        return sorted(p.name for p in (proj / ".agent" / "tasks").glob("*-retro-*"))

    def _one_retro_with_a_blocked_task(self):
        proj = self._project([(1, "a"), (2, "b"), (3, "c")])
        self._set_status(proj, 2, "blocked")
        r, made = self._retro(proj)
        self.assertEqual((r.returncode, made), (0, "004-retro-001-003"), r.stderr)
        return proj

    def test_a_second_retro_with_nothing_changed_is_refused(self):
        proj = self._one_retro_with_a_blocked_task()
        r, _made = self._retro(proj)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self._retros(proj), ["004-retro-001-003"])      # nothing was created
        for word in ("T004", "T002", "--since"):                         # which retro, what it waits for, the way out
            self.assertIn(word, r.stderr)

    def test_a_carried_task_whose_status_moved_is_a_change(self):
        proj = self._one_retro_with_a_blocked_task()
        self._set_status(proj, 2, "done (2026-09-02)")
        r, made = self._retro(proj)
        self.assertEqual((r.returncode, made), (0, "005-retro-002-002"), r.stderr)

    def test_a_task_after_the_last_retro_is_a_change(self):
        proj = self._one_retro_with_a_blocked_task()
        td = proj / ".agent" / "tasks" / "005-d"
        td.mkdir()
        (td / "task.md").write_text("# 005 - d\n\n## Status\ndone (2026-09-02)\n\n## Risk\nreversible\n\n"
                                    "## Work\n- [x] Do the work — did it\n", encoding="utf-8")
        r, made = self._retro(proj)
        self.assertEqual((r.returncode, made), (0, "006-retro-002-005"), r.stderr)   # the carried T002 and T005

    def test_an_explicit_window_is_never_refused(self):
        proj = self._one_retro_with_a_blocked_task()
        r, _made = self._retro(proj, "--since", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self._retros(proj)), 2)

    # Impl panel round 1 (opus, codex-high, grok): the first guard compared the status
    # alone, so a carried task that had WORKED — more gates checked, still `blocked` —
    # was refused with "is as it recorded it"; and a carried task that had been removed
    # left the others "unchanged".

    def test_a_carried_task_whose_gates_moved_is_a_change(self):
        proj = self._one_retro_with_a_blocked_task()
        tf = proj / ".agent" / "tasks" / "002-b" / "task.md"
        tf.write_text(tf.read_text(encoding="utf-8") + "- [x] A second step — done since the retro\n",
                      encoding="utf-8")                                   # 1/1 → 2/2, still blocked
        r, made = self._retro(proj)
        self.assertEqual((r.returncode, made), (0, "005-retro-002-002"), r.stderr)

    def test_a_carried_task_that_is_gone_is_a_change(self):
        proj = self._project([(1, "a"), (2, "b"), (3, "c")])
        self._set_status(proj, 2, "blocked")
        self._set_status(proj, 3, "blocked")
        r, made = self._retro(proj)
        self.assertEqual((r.returncode, made), (0, "004-retro-001-003"), r.stderr)
        __import__("shutil").rmtree(proj / ".agent" / "tasks" / "003-c")
        r, made = self._retro(proj)
        self.assertEqual((r.returncode, made), (0, "005-retro-002-002"), r.stderr)

    def test_the_refusal_says_what_it_compared(self):
        proj = self._one_retro_with_a_blocked_task()
        r, _made = self._retro(proj)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("T002 (blocked, 1/1 gates)", r.stderr)
        self.assertIn("byte for byte", r.stderr)

    # Impl panel round 2 (codex ×2, grok): status and gate count are not "nothing moved" —
    # a carried task whose Intent, findings or WHICH gates are checked changed has new
    # material for a retro. A retro now keeps a digest of each task it carries, and the
    # guard refuses only when their records are byte for byte what they were.

    def test_an_edit_inside_a_carried_task_is_a_change(self):
        proj = self._one_retro_with_a_blocked_task()
        tf = proj / ".agent" / "tasks" / "002-b" / "task.md"
        tf.write_text(tf.read_text(encoding="utf-8").replace("did it", "did it, and found the cause"),
                      encoding="utf-8")                                   # same status, same 1/1 gates
        r, made = self._retro(proj)
        self.assertEqual((r.returncode, made), (0, "005-retro-002-002"), r.stderr)

    def test_a_retro_made_before_digests_existed_is_compared_by_status_and_gates(self):
        # a retro record of an older version carries the table and no digests
        proj = self._project([(1, "a"), (2, "b"), (3, "retro-001-002")])
        self._set_status(proj, 2, "blocked")
        self._with_retro_table(proj, "003-retro-001-002", [(1, "done"), (2, "blocked")])
        r, _made = self._retro(proj)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self._retros(proj), ["003-retro-001-002"])
        self.assertIn("status and the gate count", r.stderr)
        self.assertNotIn("byte for byte", r.stderr)

    # Impl panel round 2 (opus): the refusal sent the user to `--since N` without saying
    # which N — and a retro made from an N above a carried task drops that task from every
    # later window (the loss task 145 fixed). It names the N that keeps them.
    def test_the_way_out_it_names_keeps_the_carried_tasks(self):
        proj = self._one_retro_with_a_blocked_task()
        r, _made = self._retro(proj)
        self.assertIn("tasks retro --since 2", r.stderr)
        r, made = self._retro(proj, "--since", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        from tasks.core import retro_carry_over
        self.assertIn(2, retro_carry_over(proj, int(made[:3])))          # still carried by the new retro


class ScaffoldPinsTheTaskTable(unittest.TestCase):
    """PLAN S11 item 10 (task 165; retro 161 panel r1, codex-medium). A retro record is
    mostly gates, which `tasks compact` may not move, so it outgrows the context of a
    seat that takes its prompt on argv; such a seat is told which sections it lost and
    reads the file (`_trim_clause`, since 1.5.3). But in retro 161's first round that
    seat lost the per-task TABLE too, because nothing in the scaffold was pinned."""

    def _scaffold(self, statuses):
        import subprocess
        proj = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, proj, True)
        for num, status in enumerate(statuses, 1):
            td = proj / ".agent" / "tasks" / f"{num:03d}-t{num}"
            td.mkdir(parents=True)
            (td / "task.md").write_text(
                f"# {num:03d} - Task {num} of ordinary title length\n\n## Status\n{status}\n\n## Risk\nreversible\n\n"
                "## Work\n- [x] Do the work — did it\n", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(_PLUGIN), PLAYBOOK_SESSION_ID="pid-retro-pin")
        env.pop("BASH_ENV", None)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "retro"], cwd=proj, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        made = sorted((proj / ".agent" / "tasks").glob("*-retro-*/task.md"))
        return proj, made[-1]

    def test_a_trimmed_record_still_carries_every_task_row(self):
        from tasks.core import select_task_context, split_md_sections
        _proj, scaffold = self._scaffold(["done (2026-09-01)"] * 40)
        # the record as it looks when the retro is worked: an outcome on every gate
        grown = "\n".join((ln + " — " + "y" * 300) if ln.lstrip().startswith("- [ ] ") else ln
                          for ln in scaffold.read_text(encoding="utf-8").splitlines()) + "\n"
        table = dict(split_md_sections(grown))["Structural Summary"]
        self.assertEqual(table.count("\n| 0"), 40, "the fixture's table does not hold the 40 tasks")
        # a budget under which the table is the section that no longer fits (the selection
        # fills from the newest section backwards; the table is one of the oldest)
        selected, receipt = select_task_context(grown, len(grown) - len(table) + 50)
        self.assertTrue(receipt, "the fixture was not trimmed")
        self.assertEqual(selected.count("\n| 0"), 40, f"task rows lost in the trim — {receipt}")
        self.assertNotIn("Structural Summary", receipt.split("· dropped:")[1])

    def test_a_table_larger_than_the_whole_budget_is_cut_and_says_so(self):
        # impl panel rounds 1-2 (codex-medium, sonnet): a pin is not a guarantee — past the
        # budget the pinned section is cut too; the payload's own marker must name the pin
        from tasks.core import select_task_context
        _proj, scaffold = self._scaffold(["done (2026-09-01)"] * 40)
        text = scaffold.read_text(encoding="utf-8")
        selected, receipt = select_task_context(text, 2_000)
        self.assertLess(selected.count("\n| 0"), 40)
        self.assertIn("hard-truncated", receipt)
        self.assertIn("pinned", selected[-120:])

    def test_the_pin_does_not_hide_the_table_from_the_next_retro(self):
        # `retro_carry_over` reads the rows of that same section
        from tasks.core import retro_carry_over
        proj, scaffold = self._scaffold(["done (2026-09-01)", "blocked", "done (2026-09-01)"])
        self.assertIn("<!-- pin -->", scaffold.read_text(encoding="utf-8").split("## Structural Summary")[1][:40])
        self.assertEqual(retro_carry_over(proj, 4), {2})


class ScaffoldRisk(unittest.TestCase):
    """PLAN S1c (task 079 finding P1-05): the retro scaffold emitted `## Status`
    and no `## Risk`, so `has_risk_section` read the record as a pre-1.5.0 task
    and `close_decision` took the LENIENT legacy path — a fail-open, not the
    block stub 065 assumed. The scaffold is created through the real CLI."""

    def test_fresh_scaffold_has_risk_section(self):
        import subprocess
        from tasks.core import extract_risk, has_risk_section
        proj = Path(tempfile.mkdtemp())
        td = proj / ".agent" / "tasks" / "001-first"
        td.mkdir(parents=True)
        (td / "task.md").write_text(
            "# 001 - First\n\n## Status\ndone (2026-09-01)\n\n## Risk\nreversible\n\n"
            "## Work\n- [x] Do the work — did it\n", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(_PLUGIN), PLAYBOOK_SESSION_ID="pid-retro-risk")
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "retro"], cwd=proj, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        made = sorted((proj / ".agent" / "tasks").glob("002-retro-*/task.md"))
        self.assertEqual(len(made), 1, f"retro scaffold not created: {r.stdout}")
        scaffold = made[0]
        self.assertTrue(has_risk_section(scaffold), "retro scaffold has no ## Risk heading")
        self.assertEqual(extract_risk(scaffold), "unclassified")
        text = scaffold.read_text(encoding="utf-8")
        self.assertLess(text.index("\n## Status\n"), text.index("\n## Risk\n"),
                        "## Risk must follow ## Status, as in every other template")
        self.assertIn("Set this at the Structure gate", text,
                      "the scaffold must carry the template's explanation of the field")
