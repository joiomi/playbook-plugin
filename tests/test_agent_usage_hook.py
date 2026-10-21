"""`agent-usage-hook` — the recorder of the main agent's tokens, on Stop (task 244, PHASE-B item 6).

At each turn's end (Stop) it reads the session's host transcript (the payload's
`transcript_path`, then the session dir's pointer) and its subagents' files from where the journal last stopped, and
appends `hook: "agent-usage"` records under the session's task (`scripts/agent_usage.py` `record_spans`).

Held:
  * a Stop records the session's calls under its task, prints nothing (a Stop hook's stdout could carry a decision)
    and exits 0; its hook-journal line says so;
  * with no path in the payload the session's pointer is read; the hook is wired on Stop only (impl round 1, codex:
    on SessionEnd it raced session-end-hook's removal of the session dir) — session-end-hook runs the recorder
    itself, first (task 246, `AtTheSessionsEnd`);
  * at the session's end, a busy lane lock with something unread leaves an `unknown` marker, at every end reason
    (a `/clear` too: SessionStart then points the session at the new transcript); "unread" uses the recorder's own
    identity check and restart rule — another file in the transcript's place, the same inode with another start or
    another size, an identical copy on a new inode, a replaced subagent file — and an empty file with no record is
    nothing unread (task 247); "another size" is the end of the last COMPLETE line, and a half-written last line marks
    unless it names a user entry — it may be a call no reader will count (task 252 and its impl round 1);
  * a session with no task records under "-";
  * a judge, tail-cert or monitor session records nothing (task 232 counts the judges' own usage);
  * a crash of the recorder exits 0, prints nothing, records nothing, and its journal line says it was skipped;
  * outside a playbook project nothing is created.

Every run uses a disposable HOME and project.

Run: python3 -m unittest tests.test_agent_usage_hook
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts" / "agent-usage-hook"
RECORDER = HOOK.parent / "agent_usage.py"


def entry(mid, model="claude-opus-5-5", out=50):
    return {"type": "assistant", "isSidechain": False, "timestamp": "2026-10-19T00:00:00Z",
            "message": {"id": mid, "model": model, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 10,
                                                            "cache_read_input_tokens": 100, "output_tokens": out}}}


class _Project(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb244-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = self.d / "home"
        (self.home / ".config" / "git").mkdir(parents=True)
        (self.home / ".gitconfig").write_text("")
        (self.home / ".config" / "git" / "config").write_text("")
        self.project = self.d / "proj"
        self.agent = self.project / ".agent"
        (self.agent / "tasks").mkdir(parents=True)
        self.sdir = self.agent / "sessions" / "s244"
        self.sdir.mkdir(parents=True)
        (self.sdir / "current_state").write_text("5\n")
        tdir = self.d / "host" / "-proj"
        tdir.mkdir(parents=True)
        self.transcript = tdir / "h1.jsonl"
        self.transcript.write_text("".join(json.dumps(e) + "\n" for e in (entry("m1"), entry("m1"), entry("m2"))))

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update({"HOME": str(self.home), "PLAYBOOK_SESSION_ID": "s244"})
        env.update(extra)
        return env

    def payload(self, event="Stop", path=True):
        p: dict = {"hook_event_name": event, "session_id": "h1", "cwd": str(self.project)}
        if path:
            p["transcript_path"] = str(self.transcript)
        if event == "Stop":
            p["stop_hook_active"] = False
        return p

    def run_hook(self, p, cwd=None, **env):
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(p), cwd=cwd or self.project, env=self.env(**env),
                              capture_output=True, text=True, timeout=60)

    def records(self):
        p = self.agent / "journal" / "enforcement.jsonl"
        if not p.exists():
            return []
        return [r for r in (json.loads(l) for l in p.read_text().splitlines() if l.strip())
                if r.get("hook") == "agent-usage"]

    def hook_lines(self):
        p = self.agent / "journal" / "hooks.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


class Recording(_Project):
    def test_a_stop_records_the_sessions_calls_under_its_task_and_prints_nothing(self):
        r = self.run_hook(self.payload())
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        recs = self.records()
        self.assertEqual([(x["task"], x["calls"], x["side"]) for x in recs], [("5", 2, 0)])
        line = [x for x in self.hook_lines() if x.get("hook") == "agent-usage"]
        self.assertEqual(len(line), 1)
        self.assertEqual(line[0]["decision"], "ok")
        self.assertIn("recorded 2 call", line[0]["detail"])

    def test_the_pointer_serves_when_the_payload_has_no_path(self):
        (self.sdir / "transcript_path").write_text(str(self.transcript) + "\n")
        r = self.run_hook(self.payload("Stop", path=False))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual([x["calls"] for x in self.records()], [2])

    def test_a_second_stop_counts_only_what_is_new(self):
        self.run_hook(self.payload())
        with open(self.transcript, "a") as fh:
            fh.write(json.dumps(entry("m3")) + "\n")
        self.run_hook(self.payload())
        self.assertEqual([x["calls"] for x in self.records()], [2, 1])

    def test_a_session_with_no_task_records_under_a_dash(self):
        (self.sdir / "current_state").unlink()
        self.run_hook(self.payload())
        self.assertEqual([x["task"] for x in self.records()], ["-"])


class TheWiring(unittest.TestCase):
    def test_it_runs_on_stop_and_not_on_session_end(self):
        hooks = json.loads((HOOK.parent.parent / "hooks" / "hooks.json").read_text())["hooks"]
        cmds = {ev: [h["command"] for m in v for h in m["hooks"]] for ev, v in hooks.items()}
        self.assertTrue(any("agent-usage-hook" in c for c in cmds["Stop"]))
        self.assertFalse(any("agent-usage-hook" in c for c in cmds.get("SessionEnd", [])))


class WhatItLeavesAlone(_Project):
    def test_a_judge_a_tail_cert_or_a_monitor_session_records_nothing(self):
        for env in ({"PLAYBOOK_SESSION_ID": "judge"}, {"PLAYBOOK_SESSION_ID": "tail-cert"},
                    {"PLAYBOOK_ROLE": "monitor"}):
            r = self.run_hook(self.payload(), **env)
            self.assertEqual((r.returncode, r.stdout), (0, ""), (env, r.stderr))
        self.assertEqual(self.records(), [])

    def test_a_crash_exits_zero_prints_nothing_records_nothing_and_says_skipped(self):
        shim = self.d / "shim"
        shim.mkdir()
        real = shutil.which("python3")
        (shim / "python3").write_text("#!/bin/bash\nfor a in \"$@\"; do case \"$a\" in *agent_usage.py) exit 3 ;; "
                                      f"esac; done\nexec '{real}' \"$@\"\n")
        (shim / "python3").chmod(0o755)
        r = self.run_hook(self.payload(), PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.records(), [])
        line = [x for x in self.hook_lines() if x.get("hook") == "agent-usage"]
        self.assertEqual(line[0]["decision"], "skip")

    def test_below_the_python_floor_it_says_so_and_records_nothing(self):
        shim = self.d / "shim39"
        shim.mkdir()
        real = shutil.which("python3")
        (shim / "python3").write_text("#!/bin/bash\nfor a in \"$@\"; do case \"$a\" in *'(3,10)'*) exit 1 ;; "
                                      "--version) echo 'Python 3.9.18'; exit 0 ;; esac; done\n"
                                      f"exec '{real}' \"$@\"\n")
        (shim / "python3").chmod(0o755)
        r = self.run_hook(self.payload(), PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertIn("needs python3 >= 3.10 (Python 3.9.18)", r.stderr)
        self.assertEqual(self.records(), [])

    def test_outside_a_playbook_project_nothing_is_created(self):
        other = self.d / "plain"
        other.mkdir()
        r = self.run_hook(self.payload(), cwd=other)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertFalse((other / ".agent").exists())



SESSION_END = HOOK.parent / "session-end-hook"
SESSION_START = HOOK.parent / "session-start-hook"


class AtTheSessionsEnd(_Project):
    """Task 246 (PLAN.md "From task 244" (b)): session-end-hook records the turn after the last Stop — one interrupted
    (Esc, a kill) gets no Stop — before it removes the session dir that holds the transcript pointer."""
    def end(self, reason="exit", **env):
        p = {"hook_event_name": "SessionEnd", "session_id": "h1", "cwd": str(self.project),
             "transcript_path": str(self.transcript), "reason": reason}
        return subprocess.run(["bash", str(SESSION_END)], input=json.dumps(p), cwd=self.project, env=self.env(**env),
                              capture_output=True, text=True, timeout=60)

    def test_an_exit_records_under_the_sessions_task_then_removes_the_dir(self):
        r = self.end("exit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(x["task"], x["calls"]) for x in self.records()], [("5", 2)])
        self.assertFalse(self.sdir.exists(), "the session dir is removed after the record")

    def test_a_clear_records_too_and_keeps_the_dir(self):
        r = self.end("clear")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([x["calls"] for x in self.records()], [2])
        self.assertTrue(self.sdir.exists())

    def test_a_judge_or_tail_cert_session_end_records_nothing(self):
        self.end("exit", PLAYBOOK_SESSION_ID="judge")
        self.end("exit", PLAYBOOK_SESSION_ID="tail-cert")
        self.assertEqual(self.records(), [])

    def test_a_stop_then_the_sessions_end_count_each_call_once(self):
        self.run_hook(self.payload())
        self.end("exit")
        self.assertEqual(sum(x.get("calls", 0) for x in self.records()), 2)

    def test_its_journal_line_says_what_was_recorded(self):
        self.end("exit")
        line = [x for x in self.hook_lines() if x.get("hook") == "session-end"]
        self.assertTrue(line and "recorded 2 call" in line[-1].get("detail", ""), line)

    def test_a_free_lock_at_the_end_with_a_call_still_being_written_marks_it(self):
        """PLAN.md "From task 255" (a) (task 265; 1.6.6's release round 1, codex): with the lane lock FREE at the
        session's end, a call the host was still writing then was neither counted nor marked — the half-written tail was
        checked only on a busy lock."""
        with open(self.transcript, "ab") as fh:
            fh.write(json.dumps(entry("m3")).encode()[:60])          # the host was killed writing a call
        r = self.end("exit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sum(x.get("calls", 0) for x in self.records()), 2)
        self.assertEqual(self.unknown(), [("5", "unknown")])
        line = [x for x in self.hook_lines() if x.get("hook") == "session-end"]
        self.assertIn("half-written", line[-1].get("detail", ""))

    def test_a_free_lock_at_the_end_with_a_half_written_user_entry_marks_nothing(self):
        user = {"parentUuid": "u9", "isSidechain": False, "promptId": "p9", "type": "user",
                "message": {"role": "user", "content": "z" * 300}}
        with open(self.transcript, "ab") as fh:
            fh.write(json.dumps(user, separators=(",", ":")).encode()[:200])
        r = self.end("exit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.unknown(), [])

    def hold_lock(self):
        lock = self.agent / "journal" / "agent-usage.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(["flock", str(lock), "sleep", "60"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        # held once a take that does not wait fails (task 247's impl round 1, sonnet: a fixed 0.3 s let a slow runner
        # give the hook the lock first)
        import time as _time
        t0 = _time.time()
        while subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 0 and _time.time() - t0 < 30:
            _time.sleep(0.05)
        return holder

    def release(self, holder):
        os.killpg(holder.pid, 9)
        holder.wait()

    def start(self, transcript, source="clear"):
        p = {"hook_event_name": "SessionStart", "session_id": "h2", "cwd": str(self.project),
             "transcript_path": str(transcript), "source": source}
        return subprocess.run(["bash", str(SESSION_START)], input=json.dumps(p), cwd=self.project, env=self.env(),
                              capture_output=True, text=True, timeout=60)

    def unknown(self):
        return [(x.get("task"), x.get("status")) for x in self.records() if x.get("status") == "unknown"]

    def test_a_busy_lock_at_a_clear_marks_the_old_transcripts_tail(self):
        # task 247 (task 246's post-panel judge, codex): after a clear, SessionStart points the session at the new
        # transcript and the next Stop's payload names it too — nothing reads the old one's tail again
        h = self.hold_lock()
        try:
            self.end("clear")
        finally:
            self.release(h)
        new = self.transcript.with_name("h2.jsonl")
        new.write_text(json.dumps(entry("n1")) + "\n")
        r = self.start(new)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.sdir / "transcript_path").read_text().strip(), str(new))
        p = self.payload()
        p.update(transcript_path=str(new), session_id="h2")
        self.run_hook(p)
        self.assertEqual(self.unknown(), [("5", "unknown")], self.records())
        self.assertEqual(sum(x.get("calls", 0) for x in self.records()), 1, "the new transcript's call only")

    def test_a_busy_lock_at_a_clear_with_nothing_unread_leaves_no_marker(self):
        self.run_hook(self.payload())
        h = self.hold_lock()
        try:
            self.end("clear")
        finally:
            self.release(h)
        self.assertEqual(self.unknown(), [])

    def busy_exit_after(self, replace, grow=False):
        self.run_hook(self.payload())                     # read to its end: the record's `to` is the file's size
        size = self.transcript.stat().st_size
        replace()
        if not grow:
            self.assertLessEqual(self.transcript.stat().st_size, size, "no larger: the size alone says nothing unread")
        h = self.hold_lock()
        try:
            self.end("exit")
        finally:
            self.release(h)
        return self.unknown()

    def test_a_transcript_replaced_by_another_file_is_unread_at_a_busy_exit(self):
        # task 247 (task 246's post-panel judge, codex): `_unread` compared the size only
        def replace():
            other = self.transcript.with_name("h1.new")
            other.write_text(json.dumps(entry("r1")) + "\n")
            os.replace(other, self.transcript)            # another inode and another start
        self.assertEqual(self.busy_exit_after(replace), [("5", "unknown")])

    def test_a_transcript_rewritten_in_place_is_unread_at_a_busy_exit(self):
        # the same inode AND the same size, another start: only `head` tells it apart (task 247's impl round 2, opus —
        # a shorter rewrite was flagged by the size alone, so it proved nothing about `head`)
        def rewrite():
            st, old = self.transcript.stat(), self.transcript.read_bytes()
            new = old.replace(b'"m1"', b'"x1"')
            self.assertEqual(len(new), len(old))
            self.assertNotEqual(new, old)
            with open(self.transcript, "r+b") as f:
                f.write(new)
            st2 = self.transcript.stat()
            self.assertEqual((st2.st_ino, st2.st_size), (st.st_ino, st.st_size))
        self.assertEqual(self.busy_exit_after(rewrite), [("5", "unknown")])

    def test_a_replaced_subagent_file_is_unread_at_a_busy_exit(self):
        # task 247's impl round 2 (sonnet): a subagent's file goes through the same check as the main transcript
        sub = self.transcript.with_suffix("") / "subagents" / "agent-a1.jsonl"
        sub.parent.mkdir(parents=True)
        sub.write_text("".join(json.dumps(entry(f"s{i}")) + "\n" for i in range(3)))
        def replace():
            other = sub.with_name("agent-a1.new")
            other.write_text(json.dumps(entry("s9")) + "\n")
            os.replace(other, sub)
        self.assertEqual(self.busy_exit_after(replace), [("5", "unknown")])
        self.assertTrue(any(x.get("side") for x in self.records()), "the subagent file was recorded at the Stop")

    def test_a_transcript_cut_short_in_place_with_its_start_kept_is_unread_at_a_busy_exit(self):
        # task 247's impl round 1 (codex; opus): the same inode and first 4 KiB, a smaller size — `read_span` reads
        # it again from 0, so `_unread` must not take `size <= to` for read
        self.transcript.write_text("".join(json.dumps(entry(f"p{i}")) + "\n" for i in range(60)))
        old = self.transcript.read_bytes()
        self.assertGreater(len(old), 4096 + 2000)
        def cut():
            ino = self.transcript.stat().st_ino
            keep = old[:old.index(b"\n", 4096) + 1]          # whole lines past the first 4 KiB
            with open(self.transcript, "r+b") as f:
                f.truncate(0)
                f.write(keep + (json.dumps(entry("new1")) + "\n").encode())
            self.assertEqual(self.transcript.stat().st_ino, ino)
            self.assertEqual(self.transcript.read_bytes()[:4096], old[:4096])
        self.assertEqual(self.busy_exit_after(cut), [("5", "unknown")])

    def test_an_empty_transcript_with_no_record_is_nothing_unread(self):
        # task 247's impl round 1 (sonnet): nothing to lose, no marker
        self.transcript.write_text("")
        h = self.hold_lock()
        try:
            self.end("exit")
        finally:
            self.release(h)
        self.assertEqual(self.unknown(), [])

    def user_entry(self):
        return {"type": "user", "isSidechain": False, "timestamp": "2026-10-19T00:00:00Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "x" * 300}]}}

    def test_a_half_written_user_entry_is_nothing_unread(self):
        # task 252 (PLAN.md "From task 247" (a), opus): `read_span` stops at the last newline; a half-written line that
        # names a user entry and no assistant is no call, so it marks nothing (a long tool result is the usual one)
        def partial():
            with open(self.transcript, "ab") as f:
                f.write(json.dumps(self.user_entry()).encode()[:200])   # no newline: the host stopped mid-line
        self.assertEqual(self.busy_exit_after(partial, grow=True), [])

    def test_a_half_written_assistant_entry_still_marks_and_so_does_it_finished(self):
        # its impl round 1 (opus): a call the host is still writing at the session's end, or was killed writing, is
        # never counted — it marks, as a silent gap must not happen; finished, it is an unread call that marks too
        line = json.dumps(entry("p1")).encode()
        def partial():
            with open(self.transcript, "ab") as f:
                f.write(line[:120])                                      # past `"assistant"`, before the usage
        self.assertIn(b'"assistant"', line[:120])
        self.assertEqual(self.busy_exit_after(partial, grow=True), [("5", "unknown")])
        with open(self.transcript, "ab") as f:
            f.write(line[120:] + b"\n")
        self.sdir.mkdir(parents=True, exist_ok=True)       # the exit above removed it: a resumed session, task 5
        (self.sdir / "current_state").write_text("5\n")
        h = self.hold_lock()
        try:
            self.end("exit")
        finally:
            self.release(h)
        self.assertEqual(self.unknown(), [("5", "unknown"), ("5", "unknown")], "the partial's marker, then the call's")

    def test_a_half_written_assistant_call_whose_input_holds_a_user_type_still_marks(self):
        # task 252's impl round 2 (codex, agy; opus, sonnet): a tool call's input can be an object with a "type": "user"
        # key — only a TOP-LEVEL user type (before "message") makes the half-written line a user entry
        call = {"parentUuid": "u0", "isSidechain": False,
                "message": {"model": "claude-opus-5-5", "id": "m9", "type": "message", "role": "assistant",
                            "content": [{"type": "tool_use", "id": "t9", "name": "Write",
                                         "input": {"type": "user", "note": "x" * 50}}]},
                "type": "assistant"}
        line = json.dumps(call, separators=(",", ":")).encode()
        cut = line.index(b'"note"')
        self.assertIn(b'"type":"user"', line[:cut])
        def partial():
            with open(self.transcript, "ab") as f:
                f.write(line[:cut])
        self.assertEqual(self.busy_exit_after(partial, grow=True), [("5", "unknown")])

    def test_a_host_shaped_user_line_cut_before_its_type_still_marks(self):
        # its impl round 2 (sonnet): the host writes a user entry's type ~120 bytes in; cut before it, it may be anything
        user = {"parentUuid": "u1", "isSidechain": False, "promptId": "p1", "type": "user",
                "message": {"role": "user", "content": "hi"}}
        line = json.dumps(user, separators=(",", ":")).encode()
        def partial():
            with open(self.transcript, "ab") as f:
                f.write(line[:line.index(b'"type"')])
        self.assertEqual(self.busy_exit_after(partial, grow=True), [("5", "unknown")])

    def test_a_half_written_user_line_with_a_message_value_before_its_type_is_a_user_entry(self):
        # task 253 (PLAN.md "From task 252" (b), the post-panel judge of 252): the top-level part ends at the "message"
        # KEY — a top-level value "message" (a branch so named) did not hide the user type
        user = {"parentUuid": "u2", "isSidechain": False, "gitBranch": "message", "promptId": "p2", "type": "user",
                "message": {"role": "user", "content": "z" * 300}}
        line = json.dumps(user, separators=(",", ":")).encode()
        def partial():
            with open(self.transcript, "ab") as f:
                f.write(line[:line.index(b'"content"')])
        self.assertEqual(self.busy_exit_after(partial, grow=True), [])

    def test_a_seeded_transcript_ending_mid_line_is_nothing_unread_while_unchanged(self):
        # task 253 (PLAN.md "From task 252" (a)): the seed's `to` is the last complete line, so the unchanged file
        # matches it (a seed at `size` read as unread at every busy end)
        one = (json.dumps(self.user_entry()) + "\n").encode()            # past FRESH_MAX (8 MiB): the Stop seeds
        self.transcript.write_bytes(one * (8 * 1024 * 1024 // len(one) + 2) + one[:100])
        r = self.run_hook(self.payload())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([x.get("status") for x in self.records()], ["unknown"], "a seed")
        h = self.hold_lock()
        try:
            self.end("exit")
        finally:
            self.release(h)
        self.assertEqual(self.unknown(), [("5", "unknown")], "the seed only, no busy-end marker")

    def test_a_transcript_that_is_only_a_half_written_user_entry_is_nothing_unread(self):
        self.transcript.write_text(json.dumps(self.user_entry())[:200])
        h = self.hold_lock()
        try:
            self.end("exit")
        finally:
            self.release(h)
        self.assertEqual(self.unknown(), [])

    def test_a_half_written_line_past_one_block_reads_back_to_the_last_newline(self):
        # its impl round 1 (opus, agy): `_complete_end` reads back in 64 KiB blocks — a last line longer than one
        def partial():
            big = dict(self.user_entry())
            big["message"] = {"role": "user", "content": [{"type": "tool_result", "content": "y" * 70000}]}
            with open(self.transcript, "ab") as f:
                f.write(json.dumps(big).encode()[:69000])
        self.assertEqual(self.busy_exit_after(partial, grow=True), [])

    def test_an_identical_copy_in_its_place_is_unread_at_a_busy_exit(self):
        # the same start, another inode: the recorder reads it from 0 as another file, so it is a span to mark
        def copy():
            other = self.transcript.with_name("h1.new")
            shutil.copyfile(self.transcript, other)
            os.replace(other, self.transcript)
        self.assertEqual(self.busy_exit_after(copy), [("5", "unknown")])

    def test_a_busy_lock_at_an_exit_with_nothing_unread_leaves_no_marker(self):
        # impl round 2 (opus): the usual clean exit — the last Stop read everything — is no gap
        self.run_hook(self.payload())
        h = self.hold_lock()
        try:
            self.end("exit")
        finally:
            self.release(h)
        self.assertFalse([x for x in self.records() if x.get("status") == "unknown"], self.records())
        line = [x for x in self.hook_lines() if x.get("hook") == "session-end"]
        self.assertIn("nothing unread", line[-1].get("detail", ""))

    def test_a_journal_that_refuses_the_write_is_said_not_recorded(self):
        # impl round 2 (codex, sonnet): the calls were counted as recorded before the append was known to land
        j = self.agent / "journal" / "enforcement.jsonl"
        j.parent.mkdir(parents=True, exist_ok=True)
        j.write_text("")
        j.chmod(0o444)                               # readable (the scan is complete), not writable: codex's case
        try:
            with open(j, "a"):
                j.chmod(0o644)
                self.skipTest("file permissions not enforced for this user")
        except PermissionError:
            pass
        try:
            self.end("exit")
        finally:
            j.chmod(0o644)
        line = [x for x in self.hook_lines() if x.get("hook") == "session-end"]
        self.assertIn("NOT written", line[-1].get("detail", ""), line)
        self.assertNotIn("recorded 2", line[-1].get("detail", ""))

    def test_a_busy_lock_at_the_sessions_end_leaves_an_unknown_marker_and_says_so(self):
        # impl round 1 (codex): the turn would be lost with no trace once the dir is gone
        holder = self.hold_lock()
        try:
            r = self.end("exit")
        finally:
            self.release(holder)
        self.assertEqual(r.returncode, 0, r.stderr)
        recs = self.records()
        self.assertEqual([(x.get("task"), x.get("status")) for x in recs], [("5", "unknown")])
        self.assertIn("lock", recs[0].get("why", ""))
        line = [x for x in self.hook_lines() if x.get("hook") == "session-end"]
        self.assertIn("skipped: lock", line[-1].get("detail", ""))


class OnAFreshMultiUserClone(_Project):
    """Task 256 (task 255's gauntlet, PB-LANE-RESOLUTION): per-user lanes, no `.agent/current_user`, no root
    `.agent/tasks` — the lane is unresolvable, and the recorder wrote `journal/` at the ROOT; like the stop hook and
    `pb_journal`, it writes nothing there."""
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.agent / "tasks")
        shutil.rmtree(self.agent / "sessions")
        (self.agent / "alice" / "tasks").mkdir(parents=True)

    def assert_no_root_journal(self):
        # nothing at the root but the lane that was there: no journal/, lock, sessions/ or tasks/ (its impl round 1,
        # sonnet: the ledger row says no root state, not only no journal)
        self.assertEqual(sorted(p.name for p in self.agent.iterdir()), ["alice"])

    def test_the_stop_recorder_writes_nothing_at_the_root(self):
        r = self.run_hook(self.payload())
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assert_no_root_journal()

    def test_the_recorder_itself_refuses_the_root_lane(self):
        """PLAN.md "From task 256" (its impl round 1, opus): `agent_usage.py hook` wrote to whatever lane it was given,
        and its lock made `journal/` — a caller that skipped the hook's `lanes_without_marker` check would bring the
        root `journal/` back. The recorder now refuses that lane itself, at a Stop and at the session's end."""
        for busy in ("", "1"):
            r = subprocess.run(["python3", "-I", str(RECORDER), "hook"], input=json.dumps(self.payload()),
                               env=self.env(PB_AU_LANE=str(self.agent), PB_AU_SESSION="h1", PB_AU_MARK_BUSY=busy),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("skipped", r.stdout)
            self.assert_no_root_journal()

    def test_the_sessions_end_records_nothing_at_the_root(self):
        p = {"hook_event_name": "SessionEnd", "session_id": "h1", "cwd": str(self.project),
             "transcript_path": str(self.transcript), "reason": "exit"}
        r = subprocess.run(["bash", str(SESSION_END)], input=json.dumps(p), cwd=self.project, env=self.env(),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_no_root_journal()


if __name__ == "__main__":
    unittest.main()
