"""The owner's options-dialog answers into chat_log.md (task 233, PHASE-B item 6).

`scripts/dialog-log-hook` runs on AskUserQuestion: PreToolUse writes only its hook-journal line in bash (the dialog
opened), PostToolUse one `DIALOG` entry beside the prompts through `scripts/dialog_log.py` — his answers first and
exact (JSON string literals), then the agent's questions flattened. The payloads are the shapes claude 2.1.302 sent in
task 232's probe (`.agent/tasks/232-*/evidence/w1/`). Each test runs the hook as Claude Code does — a subprocess, the
payload on stdin, the project as its working directory — with a temp HOME. Stdlib unittest.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins" / "playbook"
HOOK = PLUGIN / "scripts" / "dialog-log-hook"
CHAT_HOOK = PLUGIN / "scripts" / "chat-log-hook"
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN / "scripts"))

from tasks import retro  # noqa: E402
from tasks.core import _gate_counts  # noqa: E402
from tasks.lifecycle import _capture_recent_chat  # noqa: E402

PROBE = _HERE.parent.parent / ".agent" / "tasks"
COLOR = {"questions": [{"question": "Pick a color", "header": "Color", "multiSelect": False,
                        "options": [{"label": "Red", "description": "Choose red"},
                                    {"label": "Blue", "description": "Choose blue"}]}],
         "answers": {"Pick a color": "Red"}, "annotations": {}}
FRUIT = {"questions": [{"question": "Name a fruit", "header": "Fruit", "multiSelect": False,
                        "options": [{"label": "Apple", "description": "Choose apple"},
                                    {"label": "Pear", "description": "Choose pear"}]}],
         "answers": {"Name a fruit": "mango, because it is sweet"}, "annotations": {}}
INTRO = "Options dialog — the user's answers (A, exact: JSON string literals), then the agent's questions (Q):"


def payload(event, response=None, tool_use_id="toolu_01M7cKrdinbVxKCCP7mF7qtR", tool="AskUserQuestion"):
    resp = COLOR if response is None else response
    p = {"session_id": "f6b4621f", "hook_event_name": event, "tool_name": tool, "tool_use_id": tool_use_id,
         "tool_input": {"questions": resp.get("questions", []) if isinstance(resp, dict) else []}}
    if event == "PostToolUse":
        p["tool_response"] = resp
        p["duration_ms"] = 1
    return p


def a_lines(text):
    return [ln for ln in text.splitlines() if re.match(r"A\d+ ", ln)]


class _Project(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb233-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = self.d / "home"
        (self.home / ".config" / "git").mkdir(parents=True)
        (self.home / ".gitconfig").write_text("")
        (self.home / ".config" / "git" / "config").write_text("")
        self.project = self.d / "proj"
        self.agent = self.project / ".agent"
        (self.agent / "tasks").mkdir(parents=True)
        self.log = self.agent / "chat_log.md"

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update({"HOME": str(self.home), "PLAYBOOK_SESSION_ID": "s233"})
        env.update(extra)
        return env

    def run_hook(self, p, hook=HOOK, **env):
        data = p if isinstance(p, str) else json.dumps(p)
        return subprocess.run(["bash", str(hook)], input=data, cwd=self.project, env=self.env(**env),
                              capture_output=True, text=True, timeout=60)

    def entries(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text(encoding="utf-8").splitlines() if ln.startswith("**[M")]

    def journal(self):
        p = self.agent / "journal" / "hooks.jsonl"
        return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


class TheEntry(_Project):
    def test_an_answer_is_one_dialog_entry_in_the_chat_log_hooks_frame(self):
        r = self.run_hook(payload("PostToolUse"))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        text = self.log.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Project Chat Log\n\nUser messages logged with timestamps.\n\n---\n\n"))
        (head,) = self.entries()
        self.assertRegex(head, r"^\*\*\[M001\]\*\* \[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC\] `DIALOG` \(claude/s233\)$")
        self.assertIn(head + "\n\n" + INTRO + "\nA1 (Color): \"Red\"\nQ1 (Color): Pick a color\n"
                      "<!-- dialog:toolu_01M7cKrdinbVxKCCP7mF7qtR -->\n\n", text)
        self.assertEqual((self.agent / "chat_log_counter").read_text().strip(), "1")
        (line,) = [x for x in self.journal() if x["hook"] == "dialog-log"]
        self.assertEqual((line["event"], line["tool"], line["tool_use_id"], line["decision"], line["detail"]),
                         ("PostToolUse", "AskUserQuestion", "toolu_01M7cKrdinbVxKCCP7mF7qtR", "ok", "logged M001"))

    def test_the_probed_payloads(self):
        """grok's K3: the answers are keyed by the question's text — task 232's two captured PostToolUse payloads."""
        files = sorted(PROBE.glob("232-*/evidence/w1/post-*.json"))
        if len(files) != 2:
            self.skipTest("task 232's probe payloads are not beside this checkout")
        for i, f in enumerate(files):
            p = json.loads(f.read_text())
            p["tool_use_id"] = f"toolu_probe_{i}"
            self.run_hook(p)
        got = [json.loads(ln.split(": ", 1)[1]) for ln in a_lines(self.log.read_text(encoding="utf-8"))]
        self.assertEqual(got, ["Red", "mango, because it is sweet"])

    def test_the_dialogs_opening_is_a_journal_line_and_nothing_else(self):
        r = self.run_hook(payload("PreToolUse"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse(self.log.exists())
        (line,) = self.journal()
        self.assertEqual((line["hook"], line["event"], line["tool"], line["tool_use_id"], line["decision"],
                          line["detail"]),
                         ("dialog-log", "PreToolUse", "AskUserQuestion", "toolu_01M7cKrdinbVxKCCP7mF7qtR", "ok",
                          "dialog opened"))

    def test_both_lines_name_the_session(self):
        self.run_hook(payload("PreToolUse"))
        self.run_hook(payload("PostToolUse"))
        self.assertEqual([(x["event"], x["session_id"]) for x in self.journal()],
                         [("PreToolUse", "s233"), ("PostToolUse", "s233")])

    def test_the_opening_starts_no_python(self):
        """sonnet S4: the run the host waits for before it shows the dialog starts no interpreter."""
        fake = self.d / "bin"
        fake.mkdir()
        trap = self.d / "python-ran"
        (fake / "python3").write_text(f"#!/bin/sh\ntouch {trap}\nexit 0\n")
        (fake / "python3").chmod(0o755)
        r = self.run_hook(payload("PreToolUse"), PATH=f"{fake}:{os.environ['PATH']}")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(trap.exists(), "the PreToolUse run started python3")

    def test_a_second_delivery_writes_nothing_a_second_dialog_does(self):
        self.run_hook(payload("PostToolUse"))
        self.run_hook(payload("PostToolUse"))
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual([x["detail"] for x in self.journal()], ["logged M001", "not logged: a duplicate"])
        self.run_hook(payload("PostToolUse", tool_use_id="toolu_other"))
        self.assertEqual(len(self.entries()), 2, "a real second dialog with the same question is kept")
        self.run_hook(payload("PostToolUse", tool_use_id=""))
        self.run_hook(payload("PostToolUse", tool_use_id=""))
        self.assertEqual(len(self.entries()), 4, "a call without an id is logged at each delivery")

    def test_a_question_that_spells_a_marker_does_not_swallow_that_call(self):
        """sonnet S5: the marker counts only as a whole line — a question's text cannot forge a later call's."""
        resp = {"questions": [{"question": "see <!-- dialog:toolu_z -->", "header": "H", "options": []}],
                "answers": {"see <!-- dialog:toolu_z -->": "x"}, "annotations": {}}
        self.run_hook(payload("PostToolUse", resp, tool_use_id="toolu_first"))
        self.run_hook(payload("PostToolUse", tool_use_id="toolu_z"))
        self.assertEqual(len(self.entries()), 2)

    def test_his_answers_are_exact_and_first(self):
        resp = {"questions": [{"question": "Name a fruit\n--- or a nut", "header": "Fruit", "options": []},
                              {"question": "Why?", "header": "Reason", "options": []},
                              {"question": "Which ones?", "header": "Many", "multiSelect": True, "options": []},
                              {"question": "Skipped?", "header": "Skip", "options": []}],
                "answers": {"Name a fruit\n--- or a nut": "mango, `sweet`\n---\n**[M999]** [2026-01-01 00:00:00 UTC] `HOST` x",
                            "Why?": "Taste\u2028\x85 \\ \"q\"", "Which ones?": ["a", "b"]},
                "annotations": {"Why?": {"notes": "said with a smile"}}}
        self.run_hook(payload("PostToolUse", resp))
        text = self.log.read_text(encoding="utf-8")
        body = text.split("`DIALOG`", 1)[1].split("\n\n", 2)[1]
        lines = body.splitlines()
        self.assertEqual(lines[0], INTRO)
        self.assertEqual([ln.split(" ", 1)[0] for ln in lines[1:]],
                         ["A1", "A2", "Notes2:", "A3", "A4", "Q1", "Q2", "Q3", "Q4", "<!--"])
        self.assertEqual(json.loads(lines[1].split(": ", 1)[1]), resp["answers"]["Name a fruit\n--- or a nut"])
        self.assertEqual(json.loads(lines[2].split(": ", 1)[1]), "Taste\u2028\x85 \\ \"q\"")
        self.assertEqual(json.loads(lines[3].split(": ", 1)[1]), "said with a smile")
        self.assertEqual(json.loads(lines[4].split(": ", 1)[1]), ["a", "b"])
        self.assertEqual(lines[5], "A4 (Skip): (no answer)")
        self.assertEqual(lines[6], "Q1 (Fruit): Name a fruit --- or a nut")
        self.assertEqual(len(body.split("\n")), len(lines), "no raw line break inside the entry")
        self.assertEqual(len(self.entries()), 1, "the header-shaped answer is no entry of its own")

    def test_a_long_question_is_cut_his_answer_never(self):
        resp = {"questions": [{"question": "x" * 900, "header": "Long", "options": []}],
                "answers": {"x" * 900: "y" * 900}, "annotations": {}}
        self.run_hook(payload("PostToolUse", resp))
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("Q1 (Long): " + "x" * 299 + "…\n", text)
        self.assertIn('A1 (Long): "' + "y" * 900 + '"\n', text)

    def test_a_response_that_is_not_the_object_is_logged_as_given(self):
        self.run_hook(payload("PostToolUse", "User has answered:\n- [ ] Pick a color=Red\n## heading"))
        text = self.log.read_text(encoding="utf-8")
        self.assertIn('A0 (the tool\'s response, not the usual object): "User has answered:\\n- [ ] Pick a color=Red'
                      '\\n## heading"\n', text)

    def test_other_tools_and_garbage_exit_zero_and_write_nothing(self):
        for p in (payload("PostToolUse", tool="Bash"), "not json", "", "[1]"):
            r = self.run_hook(p)
            self.assertEqual(r.returncode, 0, (p, r.stderr))
        self.assertFalse(self.log.exists())

    def test_python_that_crashes_still_exits_zero(self):
        fake = self.d / "bin"
        fake.mkdir()
        (fake / "python3").write_text("#!/bin/sh\ncase \"$*\" in *version_info*) exit 0 ;; esac\nexit 2\n")
        (fake / "python3").chmod(0o755)
        r = self.run_hook(payload("PostToolUse"), PATH=f"{fake}:{os.environ['PATH']}")
        self.assertEqual(r.returncode, 0, "a PreToolUse/PostToolUse status of 2 would be a block")


class TheSinks(_Project):
    def test_a_linked_log_counter_or_lock_gets_nothing(self):
        outside = self.d / "outside.md"
        for name in ("chat_log.md", "chat_log_counter", "chat_log_counter.lock", "chat_log.md.new"):
            outside.write_text("keep\n")
            p = self.agent / name
            p.symlink_to(outside)
            r = self.run_hook(payload("PostToolUse", tool_use_id=f"toolu_{name.replace('.', '_')}"))
            self.assertEqual(r.returncode, 0)
            self.assertEqual(outside.read_text(), "keep\n", name)
            self.assertIn("not logged", r.stderr)
            p.unlink()

    def test_a_fifo_log_does_not_hang(self):
        os.mkfifo(self.log)
        r = self.run_hook(payload("PostToolUse"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("not logged", r.stderr)

    def test_a_bad_lane_marker_or_lanes_without_one_write_nothing(self):
        (self.agent / "current_user").write_text("../escape\n")
        self.run_hook(payload("PostToolUse"))
        self.assertFalse(self.log.exists())
        (self.agent / "current_user").unlink()
        shutil.rmtree(self.agent / "tasks")
        (self.agent / "alice" / "tasks").mkdir(parents=True)
        self.run_hook(payload("PostToolUse"))
        self.assertFalse(self.log.exists())
        self.assertFalse((self.agent / "alice" / "chat_log.md").exists())

    def test_a_valid_lane_gets_the_entry(self):
        (self.agent / "alice" / "tasks").mkdir(parents=True)
        (self.agent / "current_user").write_text("alice\n")
        self.run_hook(payload("PostToolUse"))
        self.assertTrue((self.agent / "alice" / "chat_log.md").exists())
        self.assertFalse(self.log.exists())

    def test_the_sessions_counters_are_untouched(self):
        sdir = self.agent / "sessions" / "s233"
        sdir.mkdir(parents=True)
        (sdir / "counters").write_text("tools=7\nwrites=3\n")
        (sdir / "turn_end").write_text("7 1-abc\n")
        before = {p.name: p.read_bytes() for p in sdir.iterdir()}
        self.run_hook(payload("PreToolUse"))
        self.run_hook(payload("PostToolUse"))
        self.assertEqual({p.name: p.read_bytes() for p in sdir.iterdir()}, before)


class OneHold(_Project):
    def _spawn(self, hook, data):
        p = subprocess.Popen(["bash", str(hook)], cwd=self.project, env=self.env(), stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        p.stdin.write(json.dumps(data))
        p.stdin.close()
        return p

    def test_prompts_and_answers_side_by_side_ids_unique_and_in_file_order(self):
        procs = []
        for i in range(12):
            procs.append(self._spawn(CHAT_HOOK, {"hook_event_name": "UserPromptSubmit", "prompt": f"prompt number {i}",
                                                 "session_id": "x"}))
            procs.append(self._spawn(HOOK, payload("PostToolUse", tool_use_id=f"toolu_{i}")))
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        ids = [int(h.split("]")[0][4:]) for h in self.entries()]
        self.assertEqual(ids, list(range(1, 25)), "every id once, and the file in id order (one hold per writer)")
        text = self.log.read_text(encoding="utf-8")
        for i in range(12):
            self.assertIn(f"prompt number {i}\n", text)
            self.assertIn(f"<!-- dialog:toolu_{i} -->\n", text)

    def test_the_prompt_hook_takes_one_hold_for_its_id_and_its_entry(self):
        """sonnet S3: the chat-log hook took the id in one hold and appended in a second — the race above is rare, so
        the hold itself is counted: a `flock` on PATH that notes each call, then runs the real one."""
        real = shutil.which("flock")
        if not real:
            self.skipTest("no flock")
        fake = self.d / "bin"
        fake.mkdir()
        calls = self.d / "flock-calls"
        (fake / "flock").write_text(f"#!/bin/sh\necho \"$*\" >> {calls}\nexec {real} \"$@\"\n")
        (fake / "flock").chmod(0o755)
        r = self.run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "one prompt", "session_id": "x"},
                          hook=CHAT_HOOK, PATH=f"{fake}:{os.environ['PATH']}")
        self.assertEqual(r.returncode, 0, r.stderr)
        held = [ln for ln in calls.read_text().splitlines() if ln.startswith("-x")]
        self.assertEqual(held, ["-x 201"], "one exclusive hold of the chat log's lock per prompt")
        self.assertEqual(len(self.entries()), 1)

    def test_the_entry_waits_for_the_lock_the_other_writers_hold(self):
        """The hold is shown, not hoped for: while this test holds `chat_log_counter.lock` (as `tasks tag` or the
        chat-log hook would), the hook writes nothing; once it is released, the entry lands."""
        import fcntl
        import time
        lock = self.agent / "chat_log_counter.lock"
        fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            p = self._spawn(HOOK, payload("PostToolUse"))
            time.sleep(1.5)
            self.assertIsNone(p.poll(), "the hook did not wait for the lock")
            self.assertEqual(self.entries(), [])
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(p.wait(timeout=60), 0)
        self.assertEqual(len(self.entries()), 1)

    def test_two_deliveries_of_one_call_at_once_leave_one_entry_and_one_step(self):
        for _ in range(5):
            procs = [self._spawn(HOOK, payload("PostToolUse", tool_use_id="toolu_same")) for _ in range(6)]
            for p in procs:
                self.assertEqual(p.wait(timeout=120), 0)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual((self.agent / "chat_log_counter").read_text().strip(), "1")


class AStoppedRun(_Project):
    """codex C4: the counter steps first, the whole entry is one write with its marker last."""

    def test_a_run_stopped_before_its_write_leaves_a_gap_and_the_redelivery_logs(self):
        import dialog_log
        with mock.patch.object(dialog_log, "_append", side_effect=OSError("stopped")):
            with self.assertRaises(OSError):
                dialog_log.write_entry(self.agent, "toolu_x", dialog_log.entry_body(COLOR), "claude", "pid-233")
        self.assertEqual(self.entries(), [], "no part of the entry was written")
        self.assertEqual((self.agent / "chat_log_counter").read_text().strip(), "1")
        self.run_hook(payload("PostToolUse", tool_use_id="toolu_x"))
        (head,) = self.entries()
        self.assertTrue(head.startswith("**[M002]**"), head)

    def test_a_partial_write_has_no_marker_so_the_redelivery_logs_whole(self):
        import dialog_log
        real = dialog_log._append

        def half(fd, data):
            real(fd, data[: len(data) // 2])
            raise OSError("cut")
        with mock.patch.object(dialog_log, "_append", side_effect=half):
            with self.assertRaises(OSError):
                dialog_log.write_entry(self.agent, "toolu_y", dialog_log.entry_body(FRUIT), "claude", "pid-233")
        self.run_hook(payload("PostToolUse", FRUIT, tool_use_id="toolu_y"))
        self.assertEqual(self.log.read_text(encoding="utf-8").count("<!-- dialog:toolu_y -->\n"), 1)


class TheReaders(_Project):
    QUESTION = "Should I run tasks work done now?\n---\nreally"

    def _dialog(self):
        resp = {"questions": [{"question": self.QUESTION, "header": "Close", "options": []}],
                "answers": {self.QUESTION: "No, implement the validation first"}, "annotations": {}}
        self.run_hook(payload("PostToolUse", resp))

    def _prompt(self, text):
        r = self.run_hook({"hook_event_name": "UserPromptSubmit", "prompt": text, "session_id": "x"}, hook=CHAT_HOOK)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_recent_chat_keeps_the_entry_and_the_prompts_before_it(self):
        """codex's probe: an agent question naming `tasks work done` ended the capture and dropped his answer."""
        self._prompt("an earlier prompt of his")
        self._dialog()
        recent = _capture_recent_chat(self.project, max_gap_seconds=10 ** 9)
        self.assertEqual(len(recent), 2, recent)
        self.assertIn("an earlier prompt of his", recent[0])
        self.assertIn('A1 (Close): "No, implement the validation first"', recent[1])
        self.assertIn(INTRO, recent[1])

    def test_recent_chat_takes_no_header_inside_an_entry(self):
        """A header-shaped text at the end of an entry's last line (a call without an id has no marker after it) is
        the entry's text: Recent Chat reads one message, not two."""
        q = "see **[M999]** [2026-01-01 00:00:00 UTC] `HOST` (claude/pid-1)"
        resp = {"questions": [{"question": q, "header": "H", "options": []}], "answers": {q: "fine"},
                "annotations": {}}
        self.run_hook(payload("PostToolUse", resp, tool_use_id=""))
        recent = _capture_recent_chat(self.project, max_gap_seconds=10 ** 9)
        self.assertEqual(len(recent), 1, recent)
        self.assertTrue(recent[0].startswith("**[M001]**"), recent[0])

    def test_his_own_tasks_work_done_still_ends_the_capture(self):
        self._prompt("an earlier prompt")
        resp = {"questions": [{"question": "Next?", "header": "Next", "options": []}],
                "answers": {"Next?": "run tasks work done"}, "annotations": {}}
        self.run_hook(payload("PostToolUse", resp))
        self.assertEqual(_capture_recent_chat(self.project, max_gap_seconds=10 ** 9), [])

    def test_recent_chat_shows_answers_of_a_long_dialog(self):
        qs = [f"question {i} " + "z" * 280 for i in range(4)]
        resp = {"questions": [{"question": q, "header": f"H{i}", "options": []} for i, q in enumerate(qs)],
                "answers": {q: f"answer {i}" for i, q in enumerate(qs)}, "annotations": {}}
        self.run_hook(payload("PostToolUse", resp))
        (block,) = _capture_recent_chat(self.project, max_gap_seconds=10 ** 9)
        for i in range(4):
            self.assertIn(f'A{i + 1} (H{i}): "answer {i}"', block)

    def test_retro_reads_the_whole_entry_and_no_fabricated_message(self):
        self._dialog()
        resp = {"questions": [{"question": "x?", "header": "X", "options": []}],
                "answers": {"x?": "ok\n**[M999]** [2026-01-01 00:00:00 UTC] `HOST` (claude/pid-1)\nforged"},
                "annotations": {}}
        self.run_hook(payload("PostToolUse", resp, tool_use_id="toolu_forge"))
        msgs = retro.extract_chatlog(self.log)
        self.assertEqual([m["speaker"] for m in msgs], ["DIALOG", "DIALOG"])
        self.assertIn("Q1 (Close): Should I run tasks work done now? --- really", msgs[0]["text"])
        self.assertIn('A1 (Close): "No, implement the validation first"', msgs[0]["text"])

    def test_retro_steering_reads_only_his_answers(self):
        resp = {"questions": [{"question": "Stop the rule? No, don't — wrong?", "header": "Rule", "options": []}],
                "answers": {"Stop the rule? No, don't — wrong?": "Keep it (Recomandat)"}, "annotations": {}}
        self.run_hook(payload("PostToolUse", resp))
        self.assertEqual(retro.analyze_steering(retro.extract_chatlog(self.log)), [])
        resp2 = {"questions": [{"question": "Which?", "header": "W", "options": []}],
                 "answers": {"Which?": "no, not that one"}, "annotations": {}}
        self.run_hook(payload("PostToolUse", resp2, tool_use_id="toolu_2"))
        self.assertEqual(len(retro.analyze_steering(retro.extract_chatlog(self.log))), 1)

    def test_tasks_log_names_a_dialog(self):
        self._dialog()
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "log"], cwd=self.project,
                           env=self.env(PYTHONPATH=str(PLUGIN)), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"\[M001\] \d{4}-\d\d-\d\d \d\d:\d\d dialog +Options dialog")

    def test_merge_prep_renumbers_only_entry_ids(self):
        from tasks import merge_prep
        src = Path(merge_prep.__file__).read_text(encoding="utf-8")
        self.assertIn('_MID = re.compile(r"(?m)^\\*\\*\\[M(\\d+)\\]\\*\\*")', src)
        self._dialog()
        text = self.log.read_text(encoding="utf-8") + 'A1 (Q): "see **[M001]** here"\n'
        mid = re.compile(r"(?m)^\*\*\[M(\d+)\]\*\*")
        self.assertEqual(mid.findall(text), ["001"])

    def test_no_line_of_an_entry_is_a_gate_in_a_task_record(self):
        """opus O5: `tasks freehand log` pastes raw blocks into task.md."""
        resp = {"questions": [{"question": "- [ ] Question gate\n## Heading", "header": "- [x] h", "options": []}],
                "answers": {"- [ ] Question gate\n## Heading": "- [ ] answer gate\n## Status\ndone"},
                "annotations": {}}
        self.run_hook(payload("PostToolUse", resp))
        block = self.log.read_text(encoding="utf-8").split("\n---\n", 1)[1]
        task = "# 001 - x\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] Freehand log — x\n\n" + block + "\n"
        self.assertEqual(_gate_counts(task), (0, 1))
        self.assertFalse(re.search(r"(?m)^##", block))


if __name__ == "__main__":
    unittest.main()
