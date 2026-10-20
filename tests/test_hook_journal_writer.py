"""The hook journal's bash writer and exit runner, in gate-echo-lib.sh (PHASE-B item 2, task 222).

The owner, 2026-10-16 10:25-10:26: "e important ca playbook sa isi salveze infomratiile legat de ce face, ca sa il putem
analiza si imbunatati" — "Istoric curat + jurnal de hook-uri". Every run of a playbook hook appends one JSON line to
`<lane>/journal/hooks.jsonl`; at 18:04, on the closed files: "Comprimate, toate" — compressed, every one kept.

What is held here, with the REAL lib run by bash on a crafted hook body:
  * the runner keeps the hook's exit status (0, 2, 1) whatever fails after it, and gives each exit action the hook's
    status as `$?` — the stop hook's turn-end mark reads it (plan panel 2026-10-16: all five judges, grok Critical);
  * one line per run with its fields; the decision by exit status unless the hook named it; the error from the
    hook's own failing line; the duration; `null` without a start time;
  * the lane: the hook's, resolved in a subshell where the hook found a project but set no lane, nothing where it
    found none, in the fresh-clone shape, with an invalid marker, a read-only lane, a linked lane or `journal/`, or
    a `hooks.jsonl` that is a link or not a regular file (a FIFO would hang the hook);
  * strings: ids keep only id characters; text is escaped and cut on a whole character and a whole escape; the encoded
    line stays within 1 KiB;
  * the tool-call id read by pattern takes the payload's own key, not one nested before it or quoted in a string;
  * writers side by side tear no line; past 20 MB the file is renamed and compressed exactly once, and kept.

Run: python3 -m unittest tests.test_hook_journal_writer
"""
from __future__ import annotations

import calendar
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts"
LIB = SCRIPTS / "gate-echo-lib.sh"
LIMIT = 20971520
FIELDS = ["ts", "hook", "event", "tool", "tool_use_id", "session_id", "host_session_id", "agent_id", "decision",
          "detail", "ms", "exit", "error", "task", "section", "susp", "sts", "boot"]

PRE = ('{"session_id":"host-sess-1","transcript_path":"/t","cwd":"/c","permission_mode":"default",'
       '"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"echo hi"},'
       '"tool_use_id":"toolu_01ABC"}')
SUB = ('{"session_id":"host-sess-1","agent_id":"a601d5c3","agent_type":"general-purpose",'
       '"hook_event_name":"PostToolUse","tool_name":"Bash","tool_input":{"command":"x"},'
       '"tool_response":{"stdout":""},"tool_use_id":"toolu_01SUB","duration_ms":3}')


def _needs(tool):
    return unittest.skipUnless(shutil.which(tool), f"no {tool} on this host")


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.proj = self.root / "proj"
        self.lane = self.proj / ".agent"
        (self.lane / "tasks").mkdir(parents=True)
        self.journal = self.lane / "journal" / "hooks.jsonl"

    def tearDown(self):
        for p in self.root.rglob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o755)
            except OSError:
                pass
        self._tmp.cleanup()

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("PLAYBOOK_", "_PB_", "PB_")) and k not in ("BASH_ENV", "ENV")}
        env.update(extra)
        return env

    def run_hook(self, body, *, hook="t-hook", cwd=None, payload="", timeout=20, **extra):
        """bash runs: the start time, the lib, `pb_hook_begin`, the payload read from stdin into $INPUT, then BODY."""
        script = ('_PB_HJ_T0=$EPOCHREALTIME\nsource "$1"\npb_hook_begin "$2"\nINPUT=$(cat)\n' + body + "\n")
        return subprocess.run([bash_or_skip(), "-c", script, "_", str(LIB), hook], input=payload,
                              cwd=str(cwd or self.proj), env=self.env(**extra), capture_output=True, text=True,
                              timeout=timeout)

    def lines(self, path=None):
        p = path or self.journal
        if not p.exists():
            return []
        raw = p.read_bytes()
        self.assertTrue(raw.endswith(b"\n") or not raw, raw[-80:])
        return [json.loads(x) for x in raw.decode("utf-8").splitlines()]

    def in_lane(self, rest="", project=None, lane=None):
        p = project or self.proj
        return (f'PROJECT_DIR="{p}"; AGENT_DIR="{lane or (p / ".agent")}"; SESSION_ID="pid-4242"\n' + rest)


class TheRunner(_Project):
    def test_the_exit_status_is_kept(self):
        for code in (0, 2, 1, 7):
            with self.subTest(code=code):
                r = self.run_hook(self.in_lane(f"exit {code}"))
                self.assertEqual(r.returncode, code, r.stderr)

    def test_the_status_is_kept_when_the_write_fails_and_an_action_fails(self):
        (self.lane / "journal").write_text("not a directory")          # mkdir fails, so does the append
        for code in (0, 2, 1):
            with self.subTest(code=code):
                r = self.run_hook(self.in_lane(f"set -e\npb_on_exit 'false'\npb_on_exit 'return 9'\nexit {code}"))
                self.assertEqual(r.returncode, code, r.stderr)

    def test_set_e_in_the_hook_does_not_turn_a_block_into_an_error(self):
        r = self.run_hook(self.in_lane("set -e\npb_on_exit 'false; echo after-false >&2'\nexit 2"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.lines()[-1]["decision"], "block")

    def test_each_action_sees_the_hooks_status_and_all_run_in_order(self):
        out = self.root / "seen"
        for code in (0, 2, 1):
            with self.subTest(code=code):
                out.write_text("")
                body = self.in_lane(
                    f"a() {{ local rc=$?; echo \"a $rc\" >> '{out}'; false; }}\n"
                    f"b() {{ local rc=$?; echo \"b $rc\" >> '{out}'; }}\n"
                    "pb_on_exit 'a'\npb_on_exit 'b'\n"
                    f"exit {code}")
                r = self.run_hook(body)
                self.assertEqual(r.returncode, code, r.stderr)
                self.assertEqual(out.read_text().split("\n")[:-1], [f"a {code}", f"b {code}"])

    def test_a_failing_command_under_set_e_is_the_error_with_its_line(self):
        r = self.run_hook(self.in_lane("set -e\ntrue\ncat /nonexistent-pb-222 2>/dev/null\necho never"))
        self.assertEqual(r.returncode, 1)
        rec = self.lines()[-1]
        self.assertEqual(rec["decision"], "error")
        self.assertEqual(rec["exit"], 1)
        self.assertRegex(rec["error"], r"^line \d+: cat /nonexistent-pb-222")

    def test_a_failure_inside_a_function_names_its_command(self):
        r = self.run_hook(self.in_lane("set -e\nf() { cat /nonexistent-pb-222b 2>/dev/null; echo never; }\nf"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("cat /nonexistent-pb-222b", self.lines()[-1]["error"])

    def test_a_stale_failure_is_not_named_for_a_later_exit(self):
        r = self.run_hook(self.in_lane("cat /nonexistent-pb-222c 2>/dev/null\nexit 3"))
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.lines()[-1]["error"], "exit 3")      # the exit as written, no line of the earlier failure

    def test_a_judges_session_and_a_nested_run_write_no_line_and_keep_their_actions(self):
        """Impl panel r1: a review must not write the project (opus I1), and codex's Stop runs the stop hook as a
        subprocess whose run its own line already says (sonnet I2). The exit actions still run, the status is kept."""
        out = self.root / "acted"
        for env in ({"PLAYBOOK_SESSION_ID": "judge"}, {"PLAYBOOK_SESSION_ID": "tail-cert"},
                    {"PLAYBOOK_HOOK_NESTED": "1"}):
            with self.subTest(env=env):
                out.unlink(missing_ok=True)
                r = self.run_hook(self.in_lane(f"pb_on_exit 'touch {out}'\nexit 2"), **env)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertTrue(out.exists())
                self.assertFalse(self.journal.exists())

    def test_without_pb_hook_begin_nothing_is_written(self):
        script = 'source "$1"\nPROJECT_DIR="$2"; AGENT_DIR="$2/.agent"\nexit 0\n'
        r = subprocess.run([bash_or_skip(), "-c", script, "_", str(LIB), str(self.proj)], cwd=str(self.proj),
                           env=self.env(), capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.journal.exists())


class TheLine(_Project):
    def test_one_line_with_every_field(self):
        r = self.run_hook(self.in_lane("exit 0"), hook="task-gate", payload=PRE)
        self.assertEqual(r.returncode, 0, r.stderr)
        recs = self.lines()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(list(rec), FIELDS)
        self.assertRegex(rec["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        stamp = calendar.timegm(time.strptime(rec["ts"], "%Y-%m-%dT%H:%M:%SZ"))
        self.assertLess(abs(stamp - time.time()), 120, rec["ts"])          # UTC, not local time
        self.assertEqual(rec["hook"], "task-gate")
        self.assertEqual((rec["event"], rec["tool"], rec["tool_use_id"]), ("PreToolUse", "Bash", "toolu_01ABC"))
        self.assertEqual((rec["session_id"], rec["host_session_id"], rec["agent_id"]),
                         ("pid-4242", "host-sess-1", ""))
        self.assertEqual((rec["decision"], rec["detail"], rec["exit"], rec["error"]), ("allow", "", 0, ""))
        self.assertIsInstance(rec["ms"], int)
        # task 234: a known session with no task is ""; no section read and no sample taken here are null
        self.assertEqual((rec["task"], rec["section"], rec["susp"], rec["sts"], rec["boot"]), ("", None, None, None, None))

    def test_the_decision_by_exit_status(self):
        cases = [(PRE, 0, "allow"), (PRE, 2, "block"), (SUB, 0, "ok"), (SUB, 2, "block"), ("", 0, "ok"),
                 (PRE, 1, "error")]
        for payload, code, decision in cases:
            with self.subTest(payload=payload[:40], code=code):
                self.journal.unlink(missing_ok=True)
                self.run_hook(self.in_lane(f"exit {code}"), payload=payload)
                self.assertEqual(self.lines()[-1]["decision"], decision)

    def test_the_hooks_own_decision_detail_and_error_win(self):
        self.run_hook(self.in_lane('PB_HJ_DECISION=context; PB_HJ_DETAIL="echo full"; PB_HJ_ERROR="the helper"\n'
                                   "exit 0"), payload=SUB)
        rec = self.lines()[-1]
        self.assertEqual((rec["decision"], rec["detail"], rec["error"]), ("context", "echo full", "the helper"))

    def test_a_subagents_call_carries_its_id(self):
        self.run_hook(self.in_lane("exit 0"), payload=SUB)
        rec = self.lines()[-1]
        self.assertEqual((rec["agent_id"], rec["tool_use_id"], rec["event"]), ("a601d5c3", "toolu_01SUB",
                                                                               "PostToolUse"))

    def test_the_frame_fields_win_over_the_pattern(self):
        self.run_hook(self.in_lane('TOOL_NAME=Write; PB_HJ_EVENT=PostToolUseFailure; PB_HJ_TOOL_USE_ID=toolu_F\n'
                                   'PB_HJ_HOST_SESSION=frame-sess; PB_HJ_AGENT_ID=frame-agent\nexit 0'),
                      payload=SUB)
        rec = self.lines()[-1]
        self.assertEqual((rec["tool"], rec["event"], rec["tool_use_id"], rec["host_session_id"], rec["agent_id"]),
                         ("Write", "PostToolUseFailure", "toolu_F", "frame-sess", "frame-agent"))

    def test_the_duration_and_its_absence(self):
        for body, check in (("sleep 0.2", lambda ms: self.assertGreaterEqual(ms, 150)),
                            ("_PB_HJ_T0=", self.assertIsNone), ("_PB_HJ_T0=garbage", self.assertIsNone),
                            ("_PB_HJ_T0=99999999999999999999.000000", self.assertIsNone)):
            with self.subTest(body=body):
                before = len(self.lines())
                self.run_hook(self.in_lane(body + "\nexit 0"))
                recs = self.lines()
                self.assertEqual(len(recs), before + 1)              # a new line each run, never a stale one read
                check(recs[-1]["ms"])

    def test_a_comma_as_the_decimal_mark_is_read(self):
        self.run_hook(self.in_lane('_PB_HJ_T0="${EPOCHREALTIME/./,}"\nexit 0'))
        self.assertIsInstance(self.lines()[-1]["ms"], int)


class TheStrings(_Project):
    def test_ids_keep_only_id_characters(self):
        payload = PRE.replace("toolu_01ABC", 'toolu_\\u0007x y\\"z\\\\w$(id)')
        self.run_hook(self.in_lane("exit 0"), payload=payload)
        got = self.lines()[-1]["tool_use_id"]
        self.assertTrue(got.startswith("toolu_"), got)
        self.assertRegex(got, r"^[A-Za-z0-9._:@/+=-]*$")        # JSON escapes are not decoded; the rest is dropped

    def test_text_is_escaped_and_control_characters_go(self):
        detail = 'say "hi" \\ there\ttab\x01bell'
        self.run_hook(self.in_lane('PB_HJ_DETAIL="$DETAIL"\nexit 0'), DETAIL=detail)
        self.assertEqual(self.lines()[-1]["detail"], 'say "hi" \\ there tab bell')

    def test_the_encoded_line_stays_within_two_kib(self):
        big_id = "a" * 5000
        payload = (f'{{"session_id":"{big_id}","agent_id":"{big_id}","hook_event_name":"{big_id}",'
                   f'"tool_name":"{big_id}","tool_input":{{}},"tool_use_id":"{big_id}"}}')
        body = self.in_lane('PB_HJ_DETAIL="$Q"; PB_HJ_ERROR="$Q"; PB_HJ_DECISION="$Q"; SESSION_ID="$Q"\n'
                            'PB_HJ_SECTION="$Q"; PB_HJ_SUSP=999999999999; PB_HJ_STS=999999999999; PB_HJ_BOOT=ffffffff\n'
                            '_PB_HJ_T0=000000000001.000000\nexit 0')
        for q in ('"' * 3000, "\\" * 3000, '\\"' * 1500, "é" * 2000, "😀" * 1000):
            with self.subTest(q=q[:4]):
                self.journal.unlink(missing_ok=True)
                self.run_hook(body, hook="h" * 300, payload=payload, Q=q)
                raw = self.journal.read_bytes()
                self.assertLessEqual(len(raw), 2048, raw[:200])
                rec = json.loads(raw.decode("utf-8"))           # whole characters, whole escapes
                self.assertEqual(list(rec), FIELDS)

    def test_a_line_with_every_field_at_its_cap_is_written_whole(self):
        """Task 234 (grok K1, opus, sonnet, agy): with the five new keys a maxed line passes the old 1,024 bytes; the
        cap is 2,048 in all three writers, so no field is dropped by the over-cap fallback."""
        i = "i" * 100
        payload = (f'{{"session_id":"{i}","agent_id":"{i}","hook_event_name":"{i}",'
                   f'"tool_name":"{i}","tool_input":{{}},"tool_use_id":"{i}"}}')
        d = self.lane / "sessions" / ("s" * 64)
        d.mkdir(parents=True)
        (d / "current_state").write_text("9" * 16 + "\n")
        body = self.in_lane(f'SESSION_ID={"s" * 64}; PB_HJ_DETAIL="$Q"; PB_HJ_ERROR="$Q"; PB_HJ_DECISION={i}\n'
                            'PB_HJ_SECTION="$Q"; PB_HJ_SUSP=999999999999; PB_HJ_STS=999999999999; PB_HJ_BOOT=ffffffff\n'
                            '_PB_HJ_T0=000000000001.000000\nexit 1')
        self.run_hook(body, hook="h" * 300, payload=payload, Q='"' * 400)
        raw = self.journal.read_bytes()
        self.assertGreater(len(raw), 1024, raw[:200])
        self.assertLessEqual(len(raw), 2048)
        rec = json.loads(raw)
        self.assertEqual(list(rec), FIELDS)
        self.assertEqual((rec["task"], rec["section"], rec["detail"], rec["error"]), ("9" * 16, '"' * 64, '"' * 64,
                                                                                     '"' * 120))

    def test_a_cut_ends_on_a_whole_character_and_a_whole_escape(self):
        for q in ("é" * 400, "x" + "é" * 400, '\\' * 401, 'x"' * 300):
            with self.subTest(q=q[:3]):
                self.journal.unlink(missing_ok=True)
                self.run_hook(self.in_lane('PB_HJ_DETAIL="$Q"\nexit 0'), Q=q)
                detail = json.loads(self.journal.read_bytes().decode("utf-8"))["detail"]
                self.assertTrue(q.startswith(detail), detail[-10:])
                self.assertGreater(len(detail), 20)


class TheTaskSectionAndSample(_Project):
    """Task 234 (PHASE-B item 6): every line names the session's task, read by the writer itself from
    `<lane>/sessions/<id>/current_state` (plan panel: opus, sonnet, codex — a line of any hook, not only the state
    echo's); `section` and the suspend sample (`susp`, `sts`, `boot`) are what the hook set, validated."""

    def pointer(self, text, sid="pid-4242"):
        d = self.lane / "sessions" / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "current_state").write_text(text)
        return d / "current_state"

    def rec(self, body="exit 0", **extra):
        self.journal.unlink(missing_ok=True)
        r = self.run_hook(self.in_lane(body), payload=PRE, **extra)
        self.assertEqual(r.returncode, int(body.rsplit("exit ", 1)[1]) if "exit " in body else 0, r.stderr)
        recs = self.lines()
        self.assertEqual(len(recs), 1)
        return recs[0]

    def test_the_task_from_the_sessions_pointer(self):
        self.pointer("234\n")
        self.assertEqual(self.rec()["task"], "234")
        self.assertEqual(self.rec("exit 2")["task"], "234")             # a block names it too

    def test_a_pointer_that_is_not_a_task_number_is_empty(self):
        for text in ("", "\n", "abc\n", "23x\n", "1" * 17 + "\n", "../9\n", "*\n"):
            with self.subTest(text=text):
                self.pointer(text)
                self.assertEqual(self.rec()["task"], "")

    def test_the_pointers_spaces_are_dropped_as_the_hooks_read_it(self):
        self.pointer("  45 \t\r\n99\n")                               # the first line, spaces dropped: the hooks' reading
        self.assertEqual(self.rec()["task"], "45")

    def test_a_linked_pointer_or_session_directory_is_not_read(self):
        target = self.root / "elsewhere"
        target.mkdir()
        (target / "current_state").write_text("7\n")
        d = self.lane / "sessions" / "pid-4242"
        d.mkdir(parents=True)
        (d / "current_state").symlink_to(target / "current_state")
        self.assertIsNone(self.rec()["task"])                           # not trusted: unknown, not "none"
        (d / "current_state").unlink()
        d.rmdir()
        d.symlink_to(target)
        self.assertIsNone(self.rec()["task"])
        d.unlink()
        (self.lane / "sessions").rmdir()
        (self.lane / "sessions").symlink_to(self.root)
        (self.root / "pid-4242").mkdir()
        (self.root / "pid-4242" / "current_state").write_text("8\n")
        self.assertIsNone(self.rec()["task"])

    @_needs("mkfifo")
    def test_a_fifo_pointer_hangs_nothing(self):
        d = self.lane / "sessions" / "pid-4242"
        d.mkdir(parents=True)
        os.mkfifo(d / "current_state")
        t0 = time.monotonic()
        self.assertIsNone(self.rec()["task"])
        self.assertLess(time.monotonic() - t0, 5)

    def test_a_session_id_that_is_not_one_component_reads_nothing(self):
        # a decoy where each id would lead: `../tasks`, `..` (the lane), `.` (sessions/), `a/b`, `a b`
        (self.lane / "tasks" / "current_state").write_text("5\n")
        (self.lane / "current_state").write_text("6\n")
        (self.lane / "sessions" / "a" / "b").mkdir(parents=True)
        (self.lane / "sessions" / "current_state").write_text("4\n")
        (self.lane / "sessions" / "a" / "b" / "current_state").write_text("3\n")
        (self.lane / "sessions" / "a b").mkdir()
        (self.lane / "sessions" / "a b" / "current_state").write_text("2\n")
        for sid in ("../tasks", "..", ".", "a/b", "a b", ""):
            with self.subTest(sid=sid):
                rec = self.rec(f'SESSION_ID="{sid}"\nexit 0')
                self.assertIsNone(rec["task"])                          # the session is not known

    def test_the_section_and_the_sample_as_the_hook_set_them(self):
        rec = self.rec('PB_HJ_SECTION="Work Plan"; PB_HJ_SUSP=25573; PB_HJ_STS=1760700000; PB_HJ_BOOT=0a1b2c3d\nexit 0')
        self.assertEqual((rec["section"], rec["susp"], rec["sts"], rec["boot"]),
                         ("Work Plan", 25573, 1760700000, "0a1b2c3d"))

    def test_a_sample_that_is_not_digits_or_hex_is_null_or_empty(self):
        for susp in ("", "-", "12x", "1e3", "-5", " 7", "0x10", "1" * 13):
            with self.subTest(susp=susp):
                rec = self.rec('PB_HJ_SUSP="$V"; PB_HJ_STS="$V"\nexit 0', V=susp)
                self.assertEqual((rec["susp"], rec["sts"]), (None, None))
        for boot in ("", "-", "0A1B2C3D", "0a1b2c3", "0a1b2c3d9", "0a1b2c3g", '0a1b"c3d'):
            with self.subTest(boot=boot):
                self.assertIsNone(self.rec('PB_HJ_BOOT="$V"\nexit 0', V=boot)["boot"])
        self.assertEqual(self.rec("PB_HJ_SUSP=0; PB_HJ_STS=0\nexit 0")["susp"], 0)
        rec = self.rec("PB_HJ_SUSP=0042; PB_HJ_STS=007\nexit 0")            # a leading zero is not JSON: read as 42
        self.assertEqual((rec["susp"], rec["sts"]), (42, 7))

    def test_a_line_past_the_cap_drops_the_three_texts_and_keeps_every_key(self):
        self.pointer("234\n")
        rec = self.rec('_PB_HJ_LINE_MAX=520; PB_HJ_DETAIL="$Q"; PB_HJ_ERROR="$Q"; PB_HJ_SECTION="$Q"\n'
                       'PB_HJ_SUSP=5; PB_HJ_STS=6; PB_HJ_BOOT=0a1b2c3d\nexit 1', Q="x" * 300)
        self.assertEqual(list(rec), FIELDS)
        self.assertEqual((rec["detail"], rec["error"], rec["section"]), ("", "", None))   # the dropped section: unknown
        self.assertEqual((rec["task"], rec["susp"], rec["sts"], rec["boot"], rec["exit"]), ("234", 5, 6, "0a1b2c3d", 1))

    def test_the_section_is_escaped_and_cut(self):
        q = 'Plan "review" \\ ' + "é" * 200
        rec = self.rec('PB_HJ_SECTION="$Q"\nexit 0', Q=q)
        self.assertTrue(q.startswith(rec["section"]), rec["section"])
        self.assertLessEqual(len(json.dumps(rec["section"], ensure_ascii=False).encode()) - 2, 128)
        self.assertEqual(self.rec('PB_HJ_SECTION=$\'a\\tb\\x01c\'\nexit 0')["section"], "a b c")

    def test_the_new_variables_start_empty_in_every_hook(self):
        rec = self.rec(PB_HJ_SECTION="inherited", PB_HJ_SUSP="5", PB_HJ_STS="6", PB_HJ_BOOT="0a1b2c3d", PB_HJ_TASK="999")
        self.assertEqual((rec["task"], rec["section"], rec["susp"], rec["sts"], rec["boot"]), ("", None, None, None, None))

    def test_a_section_read_and_empty_is_empty_and_an_unread_one_null(self):
        """grok (impl r1): "" is a read that found no section; null is no read."""
        self.assertEqual(self.rec('PB_HJ_SECTION=""\nexit 0')["section"], "")
        self.assertIsNone(self.rec('PB_HJ_SECTION=x; unset PB_HJ_SECTION\nexit 0')["section"])

    def test_the_task_the_hook_read_wins_over_a_later_pointer(self):
        """codex, grok, opus, sonnet (impl r1): the state echo names the task its read was for — a pointer moved
        after the read must not be paired with that read's section."""
        self.pointer("234\n")
        rec = self.rec('PB_HJ_TASK=77; PB_HJ_SECTION="Work Plan"\nexit 0')
        self.assertEqual((rec["task"], rec["section"]), ("77", "Work Plan"))
        self.assertEqual(self.rec("PB_HJ_TASK=7a\nexit 0")["task"], "234")     # not a task number: the pointer


class TheToolCallId(_Project):
    def test_a_nested_decoy_before_the_payloads_own_key_is_not_taken(self):
        payload = ('{"session_id":"s","hook_event_name":"PostToolUse","tool_name":"mcp__x__y",'
                   '"tool_input":{"tool_use_id":"decoy-in","a":{"tool_use_id":"decoy-deep"}},'
                   '"tool_response":{"tool_use_id":"decoy-out"},"tool_use_id":"toolu_REAL","duration_ms":4}')
        self.run_hook(self.in_lane("exit 0"), payload=payload)
        self.assertEqual(self.lines()[-1]["tool_use_id"], "toolu_REAL")

    def test_a_nested_agent_id_after_the_tool_input_is_not_the_callers(self):
        """Impl panel r1 (codex I3, reproduced on the nudge): the Agent tool's response carries `agentId` (live capture);
        a main agent's call has no top-level agent_id, and the line must not borrow the subagent's."""
        payload = ('{"session_id":"s","hook_event_name":"PostToolUse","tool_name":"Agent","tool_input":{"prompt":"x"},'
                   '"tool_response":{"agentId":"a-sub","agent_id":"a-sub2"},"tool_use_id":"toolu_A"}')
        self.run_hook(self.in_lane("exit 0"), payload=payload)
        rec = self.lines()[-1]
        self.assertEqual((rec["agent_id"], rec["tool_use_id"]), ("", "toolu_A"))

    def test_a_key_quoted_inside_a_string_is_not_taken(self):
        payload = ('{"session_id":"s","hook_event_name":"PreToolUse","tool_name":"Bash",'
                   '"tool_input":{"command":"echo \\"tool_use_id\\":\\"decoy\\""},"tool_use_id":"toolu_REAL"}')
        self.run_hook(self.in_lane("exit 0"), payload=payload)
        self.assertEqual(self.lines()[-1]["tool_use_id"], "toolu_REAL")

    def test_a_long_error_after_the_id_still_finds_it(self):
        payload = ('{"session_id":"s","hook_event_name":"PostToolUseFailure","tool_name":"Bash",'
                   '"tool_input":{"command":"false"},"tool_use_id":"toolu_FAIL","error":"' + "e" * 20000 +
                   '","is_interrupt":false,"duration_ms":9}')
        self.run_hook(self.in_lane("exit 0"), payload=payload)
        self.assertEqual(self.lines()[-1]["tool_use_id"], "toolu_FAIL")

    def test_a_five_mb_payload_is_read_quickly(self):
        payload = PRE.replace('"echo hi"', '"' + "a" * 5_000_000 + '"')
        t0 = time.monotonic()
        r = self.run_hook(self.in_lane("exit 0"), payload=payload, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertEqual(self.lines()[-1]["tool_use_id"], "toolu_01ABC")


class WhereItWrites(_Project):
    def test_a_hook_that_found_no_project_writes_nothing_and_does_not_look_again(self):
        r = self.run_hook('PROJECT_DIR=""\nexit 0')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.journal.exists())

    def test_a_hook_that_did_not_look_finds_the_project_from_its_directory(self):
        sub = self.proj / "src" / "deep"
        sub.mkdir(parents=True)
        r = self.run_hook("exit 0", cwd=sub)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.lines()), 1)

    def test_outside_any_project_nothing_is_written(self):
        out = self.root / "elsewhere"
        out.mkdir()
        r = self.run_hook("exit 2", cwd=out)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(list(self.root.rglob("hooks.jsonl")), [])

    def test_a_project_without_a_lane_resolves_it(self):
        self.run_hook(f'PROJECT_DIR="{self.proj}"\nexit 0')
        self.assertEqual(len(self.lines()), 1)

    def test_a_malformed_marker_keeps_a_block_and_writes_nothing(self):
        (self.lane / "current_user").write_text("alice\n../evil\n")
        r = self.run_hook(f'PROJECT_DIR="{self.proj}"\nexit 2')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(list(self.root.rglob("hooks.jsonl")), [])

    def test_an_invalid_marker_flag_writes_nothing(self):
        r = self.run_hook(self.in_lane("MARKER_INVALID=true\nexit 2"))
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.journal.exists())

    def test_the_fresh_clone_shape_writes_nothing(self):
        proj = self.root / "clone"
        (proj / ".agent" / "alice" / "tasks").mkdir(parents=True)
        for body in (self.in_lane("exit 0", project=proj), f'PROJECT_DIR="{proj}"\nexit 0', "exit 0"):
            with self.subTest(body=body[:30]):
                r = self.run_hook(body, cwd=proj)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(list(proj.rglob("hooks.jsonl")), [])
                self.assertFalse((proj / ".agent" / "journal").exists())

    def test_a_users_lane(self):
        proj = self.root / "multi"
        (proj / ".agent" / "alice" / "tasks").mkdir(parents=True)
        (proj / ".agent" / "current_user").write_text("alice\n")
        self.run_hook(f'PROJECT_DIR="{proj}"\nexit 0', cwd=proj)
        self.assertEqual(len(self.lines(proj / ".agent" / "alice" / "journal" / "hooks.jsonl")), 1)
        self.assertFalse((proj / ".agent" / "journal").exists())

    def test_a_read_only_lane_writes_nothing_and_keeps_the_status(self):
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere")
        self.lane.chmod(0o555)
        r = self.run_hook(self.in_lane("exit 2"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse((self.lane / "journal").exists())

    def test_a_linked_lane_or_journal_or_file_is_not_written_through(self):
        target = self.root / "target"
        target.mkdir()
        cases = []
        # a linked lane
        p1 = self.root / "p1"
        (p1).mkdir()
        (target / "lane").mkdir()
        (target / "lane" / "tasks").mkdir()
        (p1 / ".agent").symlink_to(target / "lane")
        cases.append((p1, target / "lane" / "journal"))
        # a linked journal/
        p2 = self.root / "p2"
        (p2 / ".agent" / "tasks").mkdir(parents=True)
        (target / "j2").mkdir()
        (p2 / ".agent" / "journal").symlink_to(target / "j2")
        cases.append((p2, target / "j2" / "hooks.jsonl"))
        # a linked hooks.jsonl
        p3 = self.root / "p3"
        (p3 / ".agent" / "journal").mkdir(parents=True)
        (p3 / ".agent" / "tasks").mkdir()
        (target / "f3").write_text("keep\n")
        (p3 / ".agent" / "journal" / "hooks.jsonl").symlink_to(target / "f3")
        cases.append((p3, target / "f3"))
        for proj, victim in cases:
            with self.subTest(proj=proj.name):
                before = victim.read_text() if victim.is_file() else None
                r = self.run_hook(self.in_lane("exit 2", project=proj), cwd=proj)
                self.assertEqual(r.returncode, 2, r.stderr)
                after = victim.read_text() if victim.is_file() else None
                self.assertEqual(before, after)
                self.assertFalse(victim.is_dir() and any(victim.iterdir()))

    def test_a_fifo_is_not_opened(self):
        (self.lane / "journal").mkdir()
        os.mkfifo(self.journal)
        t0 = time.monotonic()
        r = self.run_hook(self.in_lane("exit 2"), timeout=10)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertLess(time.monotonic() - t0, 5)


class SideBySide(_Project):
    def test_writers_side_by_side_tear_no_line(self):
        # a subshell is not a run (it has no trap): the writer is called directly, many times per process
        body = self.in_lane('for i in $(seq 1 150); do PB_HJ_DETAIL="w$W-$i"; hook_journal_append 0 ""; done\nexit 0')
        procs = [subprocess.Popen([bash_or_skip(), "-c",
                                   '_PB_HJ_T0=$EPOCHREALTIME\nsource "$1"\npb_hook_begin w\nINPUT=""\n' + body,
                                   "_", str(LIB)], cwd=str(self.proj), env=self.env(W=str(w)),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for w in range(8)]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        recs = self.lines()
        self.assertEqual(len(recs), 8 * 151)
        self.assertEqual(len({r["detail"] for r in recs if r["detail"]}), 8 * 150)


@_needs("flock")
class Rotation(_Project):
    def fill(self, size=LIMIT + 4096):
        (self.lane / "journal").mkdir(exist_ok=True)
        line = json.dumps({"ts": "2026-10-16T00:00:00Z", "hook": "old", "detail": "x" * 200}) + "\n"
        n = size // len(line) + 1
        with open(self.journal, "w") as f:
            f.write(line * n)
        return n

    def archives(self):
        return sorted((self.lane / "journal").glob("hooks.jsonl.archived-*"))

    def age(self, path, seconds=1200):
        old = time.time() - seconds
        os.utime(path, (old, old))

    def age_closed(self, path, seconds=1200):
        """A closed file as it is `seconds` after its rename: the rename time its name records, and its mtime."""
        m = re.fullmatch(r"(hooks\.jsonl\.archived-\d{8}-\d{6})-(?:(\d+)-)?(\d+)", path.name)
        assert m, path.name
        new = path.with_name(f"{m.group(1)}-{int(time.time()) - seconds}-{m.group(3)}")
        path.rename(new)
        self.age(new, seconds)
        return new

    def test_past_the_limit_the_file_is_closed_then_compressed_once_idle_and_kept(self):
        """Impl panel r1 (codex, grok, opus — G1, grave): a closed file is compressed only once no hook can still be
        writing to it — its mtime over 10 minutes old, on a later sampled run."""
        n = self.fill()
        r = self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        arch = self.archives()
        self.assertEqual(len(arch), 1, arch)
        self.assertFalse(arch[0].name.endswith(".gz"), arch[0].name)        # just closed: not compressed yet
        self.assertEqual([r["hook"] for r in self.lines()], ["t-hook"])
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        self.assertFalse(self.archives()[0].name.endswith(".gz"))            # still fresh
        self.age_closed(arch[0])
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        arch = self.archives()
        self.assertEqual(len(arch), 1, arch)
        self.assertTrue(arch[0].name.endswith(".gz"), arch[0].name)
        with gzip.open(arch[0], "rt") as f:
            self.assertEqual(len(f.read().splitlines()), n)

    def test_a_line_on_a_descriptor_held_across_the_rename_survives(self):
        """G1: a writer that opened the file before the rename and writes later puts its line in the closed file, and
        the compression that comes later keeps it (the old immediate gzip + rm dropped it — codex reproduced it)."""
        self.fill()
        held = open(self.journal, "a")
        try:
            self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
            held.write(json.dumps({"hook": "held-late"}) + "\n")
            held.flush()
        finally:
            held.close()
        arch = self.archives()
        self.assertEqual(len(arch), 1, arch)
        self.age_closed(arch[0])
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        gz = self.archives()[0]
        self.assertTrue(gz.name.endswith(".gz"), gz.name)
        with gzip.open(gz, "rt") as f:
            self.assertIn('"held-late"', f.read())

    def test_an_idle_oversized_file_is_not_compressed_by_the_run_that_closes_it(self):
        """Impl panel r2 (codex, grok — reproduced, grave): a rename keeps the file's mtime, so a 20 MB journal idle
        for over 10 minutes was compressed by the very run that closed it (here: an older closed file is waiting, so
        the compression pass runs), and a descriptor held across that rename lost its line."""
        older = self.lane / "journal" / f"hooks.jsonl.archived-20261001-000000-{int(time.time()) - 1200}-1"
        self.fill()
        older.write_text('{"hook":"older"}\n')
        self.age(older)
        self.age(self.journal)
        held = open(self.journal, "a")
        try:
            self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
            held.write(json.dumps({"hook": "held-late"}) + "\n")
            held.flush()
        finally:
            held.close()
        names = sorted(a.name for a in self.archives())
        self.assertIn(older.name + ".gz", names)                     # the old one, idle: compressed
        fresh = [n for n in names if not n.endswith(".gz")]
        self.assertEqual(len(fresh), 1, names)                       # the one just closed: not yet
        aged = self.age_closed(self.lane / "journal" / fresh[0])
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        with gzip.open(aged.with_name(aged.name + ".gz"), "rt") as f:
            self.assertIn('"held-late"', f.read())

    def test_a_closed_file_waits_for_the_rename_time_its_name_records(self):
        """Post-panel judge run 1 (codex — reproduced, grave): a rotator killed between the rename and the mtime reset
        left an old mtime; the next sampled run compressed the file at once. The rename time is in the NAME, written by
        the rename itself: such a file waits, and a closed file whose name records no rename time is never compressed."""
        (self.lane / "journal").mkdir(exist_ok=True)
        self.journal.write_text("{}\n")
        killed = self.lane / "journal" / f"hooks.jsonl.archived-20261016-120000-{int(time.time())}-99"
        killed.write_text('{"hook":"before"}\n')
        self.age(killed)                                       # the mtime reset never happened
        nameless = self.lane / "journal" / "hooks.jsonl.archived-foreign"
        nameless.write_text("{}\n")
        self.age(nameless)
        held = open(killed, "a")
        try:
            self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
            held.write(json.dumps({"hook": "held-late"}) + "\n")
        finally:
            held.close()
        self.assertTrue(killed.exists(), self.archives())       # not compressed: its rename is a moment ago
        aged = self.age_closed(killed)
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        with gzip.open(aged.with_name(aged.name + ".gz"), "rt") as f:
            self.assertIn('"held-late"', f.read())
        self.assertTrue(nameless.exists())                      # no rename time in its name: never compressed
        written = self.lane / "journal" / f"hooks.jsonl.archived-20261016-110000-{int(time.time()) - 1200}-98"
        written.write_text("{}\n")                             # renamed long ago, written a moment ago: waits
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        self.assertTrue(written.exists(), self.archives())

    def test_the_duration_counts_the_journals_own_work(self):
        """Impl panel r1 (codex I2): rotation and compression run before the duration is taken."""
        self.fill()
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        self.age_closed(self.archives()[0])
        slow = self.root / "slow-gzip"
        slow.mkdir()
        (slow / "gzip").write_text(f'#!/bin/sh\nsleep 1\nexec {shutil.which("gzip")} "$@"\n')
        (slow / "gzip").chmod(0o755)
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1", PATH=f"{slow}:{os.environ['PATH']}")
        self.assertGreaterEqual(self.lines()[-1]["ms"], 900)

    def test_under_the_limit_or_unsampled_nothing_rotates(self):
        self.fill(size=1000)
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        self.assertEqual(self.archives(), [])
        self.fill()
        self.run_hook(self.in_lane("RANDOM=1\nexit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1000")
        self.assertEqual(self.archives(), [])

    def test_two_rotators_rotate_once(self):
        self.fill()
        procs = [subprocess.Popen([bash_or_skip(), "-c",
                                   '_PB_HJ_T0=$EPOCHREALTIME\nsource "$1"\npb_hook_begin r\nINPUT=""\n'
                                   + self.in_lane("exit 0"), "_", str(LIB)], cwd=str(self.proj),
                                  env=self.env(PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1"),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for _ in range(6)]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        arch = self.archives()
        self.assertEqual(len(arch), 1, arch)
        closed = [json.loads(x)["hook"] for x in arch[0].read_text().splitlines()]
        new = [r["hook"] for r in self.lines()]
        self.assertEqual(closed.count("r") + new.count("r"), 6)          # no line lost: before or after the rename

    def test_a_writer_that_looked_before_another_rotated_does_not_rotate_again(self):
        """The second look under the lock: B saw the file past the limit, then waited (a slow `flock` stand-in) while A
        rotated it; once B holds the lock the file is the new small one, and B must leave it (else it archives it)."""
        self.fill()
        slow = self.root / "slow-flock"
        slow.mkdir()
        (slow / "flock").write_text(f'#!/bin/sh\nsleep 2\nexec {shutil.which("flock")} "$@"\n')
        (slow / "flock").chmod(0o755)
        body = '_PB_HJ_T0=$EPOCHREALTIME\nsource "$1"\npb_hook_begin r\nINPUT=""\n' + self.in_lane("exit 0")
        b = subprocess.Popen([bash_or_skip(), "-c", body, "_", str(LIB)], cwd=str(self.proj),
                             env=self.env(PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1", PATH=f"{slow}:{os.environ['PATH']}"),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(0.5)                                     # B has looked and sits in its slow flock
        a = subprocess.run([bash_or_skip(), "-c", body, "_", str(LIB)], cwd=str(self.proj),
                           env=self.env(PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1"), capture_output=True, timeout=60)
        self.assertEqual(a.returncode, 0)
        self.assertEqual(b.wait(timeout=60), 0)
        self.assertEqual(len(self.archives()), 1, self.archives())

    def test_an_append_waits_while_the_journal_is_held_exclusively(self):
        """The owner, 2026-10-16 22:21 ("Lacăt la fiecare rând"): each append holds a SHARED lock on the journal
        directory, so it waits while a rename or a compression holds it exclusively — no line can be in flight then,
        whatever the clock (the post-panel judge's run 2: a forward clock step made a held file look idle)."""
        (self.lane / "journal").mkdir(exist_ok=True)
        holder = subprocess.Popen(["flock", "-x", str(self.lane / "journal"), "sleep", "0.8"])
        try:
            time.sleep(0.2)
            t0 = time.monotonic()
            r = self.run_hook(self.in_lane("exit 0"))
            waited = time.monotonic() - t0
        finally:
            holder.wait()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreaterEqual(waited, 0.45)                    # it waited for the exclusive holder
        recs = self.lines()
        self.assertEqual(len(recs), 1)
        self.assertGreaterEqual(recs[0]["ms"], 450)              # and the line counts the wait (post-panel run 3)

    def test_past_the_wait_the_line_goes_to_a_spool_and_is_never_appended_unlocked(self):
        """The owner, 2026-10-17 00:13 ("Bilet în cutie"): post-panel run 3 found the unlocked append after the 1 s
        wait; now the line goes to a spool file of its own, written whole, and the next writer holding the lock
        exclusively moves it into the journal."""
        (self.lane / "journal").mkdir(exist_ok=True)
        holder = subprocess.Popen(["flock", "-x", str(self.lane / "journal"), "sleep", "1.8"])
        try:
            time.sleep(0.2)
            r = self.run_hook(self.in_lane("exit 0"), hook="spooled")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.lines(), [])                   # nothing appended without the lock
            spools = sorted((self.lane / "journal").glob("hooks.jsonl.spool-*"))
            self.assertEqual(len(spools), 1, spools)
        finally:
            holder.wait()
        self.run_hook(self.in_lane("exit 0"), hook="next", PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        self.assertEqual(sorted(r["hook"] for r in self.lines()), ["next", "spooled"])
        self.assertEqual(list((self.lane / "journal").glob("hooks.jsonl.spool*")), [])

    def test_a_writers_temporary_is_taken_only_when_its_writer_is_gone(self):
        """Post-panel judge run 4 (codex — reproduced, grave): the sweep removed a temporary for its AGE, so a live
        writer paused past a clock step lost its line. Now: a temporary whose writer (the pid in its name) is gone is
        taken as a spool (its one write is the whole line or nothing); a living writer's is left, however old."""
        (self.lane / "journal").mkdir(exist_ok=True)
        self.journal.write_text("")
        dead = subprocess.Popen(["true"])
        dead.wait()
        gone = self.lane / "journal" / f"hooks.jsonl.spooling-20261017-000000-{dead.pid}-1"
        gone.write_text('{"hook":"orphan"}\n')
        alive = self.lane / "journal" / f"hooks.jsonl.spooling-20261017-000000-{os.getpid()}-2"
        alive.write_text('{"hook":"in-flight"}\n')
        for f in (gone, alive):
            self.age(f)
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        self.assertFalse(gone.exists())
        self.assertIn("orphan", [r["hook"] for r in self.lines()])
        self.assertTrue(alive.exists())                          # a living writer's temporary is never removed

    def test_a_held_lock_means_no_rotation_and_the_line_is_written(self):
        self.fill()
        holder = subprocess.Popen(["flock", str(self.lane / "journal"), "sleep", "5"])
        try:
            time.sleep(0.3)
            t0 = time.monotonic()
            r = self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
            self.assertLess(time.monotonic() - t0, 2.5)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.archives(), [])
            self.assertGreater(self.journal.stat().st_size, LIMIT)
        finally:
            holder.kill()
            holder.wait()

    def test_a_failing_gzip_leaves_the_closed_file_uncompressed(self):
        n = self.fill()
        stub = self.root / "bin"
        stub.mkdir()
        (stub / "gzip").write_text("#!/bin/sh\nexit 1\n")
        (stub / "gzip").chmod(0o755)
        self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1")
        self.age_closed(self.archives()[0])
        r = self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1",
                          PATH=f"{stub}:{os.environ['PATH']}")
        self.assertEqual(r.returncode, 0, r.stderr)
        arch = self.archives()
        self.assertEqual(len(arch), 1, arch)
        self.assertFalse(arch[0].name.endswith(".gz"))
        self.assertEqual(len(arch[0].read_text().splitlines()), n)
        self.assertEqual(list((self.lane / "journal").glob("*.tmp*")), [])



@_needs("flock")
@_needs("gzip")
class TheBashCompressionProtocol(_Project):
    """Task 259's plan r1 (P1-P5) in bash: `_pb_hj_compress` gzips OUTSIDE the journal directory's exclusive hold, into
    a temporary made durable (`sync`), checks it whole (`gzip -dc | cmp -s`), and under `flock -w 1` (whole seconds:
    `-w 0.2` fails under a decimal-comma locale) links it to `.gz`, syncs the `.gz` and the directory, then removes
    the archive; any failure keeps the archive; one compressor per archive; `X` beside a whole `.gz` is repaired."""

    def idle(self, data=b'{"hook":"old"}\n' * 50, pid=7):
        (self.lane / "journal").mkdir(exist_ok=True)
        t = int(time.time()) - 1200
        p = self.lane / "journal" / f"hooks.jsonl.archived-20261016-000000-{t}-{pid}"
        p.write_bytes(data)
        os.utime(p, (t, t))
        return p

    def tools(self, **scripts):
        """A directory of stand-ins put first on PATH: name -> sh body."""
        d = self.root / "tools"
        d.mkdir(exist_ok=True)
        for name, body in scripts.items():
            (d / name).write_text("#!/bin/sh\n" + body + "\n")
            (d / name).chmod(0o755)
        return f"{d}:{os.environ['PATH']}"

    def rotate(self, background=False, **extra):
        if background:
            script = ('_PB_HJ_T0=$EPOCHREALTIME\nsource "$1"\npb_hook_begin t\nINPUT=""\n' + self.in_lane("exit 0"))
            return subprocess.Popen([bash_or_skip(), "-c", script, "_", str(LIB)], cwd=str(self.proj),
                                    env=self.env(PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1", **extra),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return self.run_hook(self.in_lane("exit 0"), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1", **extra)

    def test_the_gzip_runs_outside_the_exclusive_hold(self):
        x = self.idle()
        slow = self.tools(gzip=f'sleep 2\nexec {shutil.which("gzip")} "$@"')
        p = self.rotate(background=True, PATH=slow)
        try:
            time.sleep(1.0)                                    # the gzip is running
            r = subprocess.run(["flock", "-n", "-s", str(self.lane / "journal"), "true"])
            self.assertEqual(r.returncode, 0, "an append's shared lock was refused during the gzip")
        finally:
            p.wait(timeout=60)
        self.assertFalse(x.exists())
        self.assertTrue(Path(str(x) + ".gz").exists())

    def test_a_short_gzip_keeps_the_archive(self):
        x = self.idle()
        short = self.tools(gzip=f'{shutil.which("gzip")} "$@" | head -c 40')
        self.rotate(PATH=short)
        self.assertTrue(x.exists())
        self.assertFalse(Path(str(x) + ".gz").exists())
        self.assertEqual(list((self.lane / "journal").glob("*.gz.tmp-*")), [])

    def test_a_failing_sync_keeps_the_archive(self):
        x = self.idle()
        self.rotate(PATH=self.tools(sync="exit 1"))
        self.assertTrue(x.exists())

    def test_the_gz_and_the_directory_are_synced_before_the_archive_goes(self):
        x = self.idle()
        log = self.root / "sync.log"
        logged = self.tools(sync=f'printf "%s\\n" "$*" >> "{log}"\nexec {shutil.which("sync")} "$@"')
        self.rotate(PATH=logged)
        self.assertFalse(x.exists())
        calls = log.read_text().splitlines()
        # the directory as an argument of its own (W5's mutant M8: it is also a substring of the `.gz`'s path)
        self.assertTrue(any({f"{x}.gz", str(self.lane / "journal")} <= set(c.split(" ")) for c in calls), calls)

    def test_x_beside_a_whole_gz_is_repaired_and_a_garbled_one_keeps_it(self):
        data = b'{"hook":"old"}\n' * 200
        x = self.idle(data)
        with gzip.open(str(x) + ".gz", "wb") as f:
            f.write(data)
        os.utime(str(x) + ".gz", None)
        self.rotate()
        self.assertFalse(x.exists())
        y = self.idle(data, pid=8)
        good = gzip.compress(data)
        Path(str(y) + ".gz").write_bytes(good[:10] + bytes(len(good) - 18) + good[-8:])
        self.rotate()
        self.assertTrue(y.exists())

    def test_one_compressor_per_archive(self):
        x = self.idle()
        living = Path(f"{x}.gz.tmp-{os.getppid()}")
        living.write_bytes(b"partial")
        self.rotate()
        self.assertTrue(x.exists() and living.exists())
        living.unlink()
        dead = Path(f"{x}.gz.tmp-999999")
        dead.write_bytes(b"partial")
        self.rotate()
        self.assertFalse(dead.exists())
        self.assertFalse(x.exists())

    def test_a_lock_not_had_keeps_the_archive_and_leaves_no_temporary(self):
        x = self.idle()
        holder = subprocess.Popen(["flock", "-s", str(self.lane / "journal"), "sleep", "3"])   # a reader
        try:
            time.sleep(0.2)
            self.rotate()
            self.assertTrue(x.exists())
            self.assertEqual(list((self.lane / "journal").glob("*.gz*")), [])
        finally:
            holder.wait()

    def test_one_compression_a_pass(self):
        self.idle(pid=1)
        self.idle(pid=2)
        self.rotate()
        self.assertEqual(len(list((self.lane / "journal").glob("*.gz"))), 1)

    def test_a_bad_crc_with_a_whole_payload_keeps_the_archive(self):
        """Impl r1 I1 (astra, sol; agy's unfinished run reproduced it): `gzip -dc | cmp -s` took only cmp's status —
        gzip writes the whole payload, then fails on the CRC. Both pipelines now want both statuses 0."""
        real = shutil.which("gzip")
        bad = self.tools(gzip=(f'if [ "$1" = "-c" ]; then {real} "$@" | python3 -c "import sys; d=sys.stdin.buffer.read();'
                               f' sys.stdout.buffer.write(d[:-8] + bytes(4) + d[-4:])"; else exec {real} "$@"; fi'))
        x = self.idle()
        self.rotate(PATH=bad)                                   # the swap: its temporary has a bad CRC
        self.assertTrue(x.exists())
        self.assertFalse(Path(str(x) + ".gz").exists())
        data = b'{"hook":"old"}\n' * 200
        y = self.idle(data, pid=8)
        good = gzip.compress(data)
        Path(str(y) + ".gz").write_bytes(good[:-8] + bytes(4) + good[-4:])
        self.rotate()                                           # the repair: the .gz beside it has a bad CRC
        self.assertTrue(y.exists())

    def test_the_swaps_own_lock_not_had_keeps_the_archive_and_waits(self):
        """Impl r1 O1 (opus): a reader takes the lock while the gzip runs, so only the swap's `flock -w 1` can fail.
        I4: the archive's mtime is refreshed, so the next try waits the idle time."""
        jdir = self.lane / "journal"
        real = shutil.which("gzip")
        reader = self.tools(gzip=(f'if [ "$1" = "-c" ]; then (flock -s "{jdir}" sleep 3 &) ; sleep 0.3; fi\n'
                                  f'exec {real} "$@"'))
        x = self.idle()
        self.rotate(PATH=reader)
        self.assertTrue(x.exists())
        self.assertEqual(list(jdir.glob("*.gz*")), [])
        self.assertGreater(x.stat().st_mtime, time.time() - 60)
        time.sleep(3)

    def test_a_failed_repair_check_waits_the_idle_time_again(self):
        x = self.idle()
        with gzip.open(str(x) + ".gz", "wb") as f:
            f.write(b'{"hook":"other"}\n')
        os.utime(str(x) + ".gz", None)
        self.rotate()
        self.assertTrue(x.exists())
        self.assertGreater(x.stat().st_mtime, time.time() - 60)

    def test_a_pair_that_cannot_be_repaired_does_not_starve_the_next_archive(self):
        stuck = self.idle(pid=1)
        with gzip.open(str(stuck) + ".gz", "wb") as f:
            f.write(b'{"hook":"other"}\n')
        t = int(time.time()) - 1200
        os.utime(str(stuck) + ".gz", (t - 60, t - 60))
        later = self.idle(b'{"hook":"later"}\n' * 50, pid=2)
        self.rotate()
        self.assertTrue(Path(str(later) + ".gz").exists())
        self.assertTrue(stuck.exists())

    def test_the_claim_is_made_inside_the_rotations_hold(self):
        """Impl r1 I3 (sol): the temporary exists before gzip starts — made under the rotation's hold."""
        log = self.root / "first-stat.log"
        x = self.idle()
        # the compressor's first `stat -c '%s %y'` (its "before") runs after the hold is let go and before any gzip:
        # the temporary must already be there (a redirect would make one only at the gzip itself)
        spy = self.tools(stat=(f'if [ "$1" = "-c" ] && [ "$2" = "%s %y" ] && [ ! -e "{log}" ]; then '
                               f'ls "{x}".gz.tmp-* > "{log}" 2>/dev/null || : > "{log}"; fi\n'
                               f'exec {shutil.which("stat")} "$@"'))
        self.rotate(PATH=spy)
        self.assertTrue(log.exists() and log.read_text().strip(), "no temporary existed before the gzip")


if __name__ == "__main__":
    unittest.main()
