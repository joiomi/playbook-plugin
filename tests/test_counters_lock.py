"""The session's counters under one lock (PLAN S11 item 17; owner 2026-10-10; task 182).

The stop hook lets a turn with open gates end when the session's counters say it was a
chat reply: no write and fewer than five tool calls. `state-echo-hook` writes those
counters after EVERY tool call, and the host runs the hooks of one message's tool calls
side by side. Every writer rewrote the file WHOLE from what it had read a moment before
(`write_counter` copies the other keys and renames the copy over the file), with nothing
to keep two of them apart — measured on 2026-10-11 with the real hook: 40 Bash calls two
at a time were counted as `tools=20 writes=20`, 60 calls six at a time as 10 and 10. A
`writes` that is too low is the releasing side of the stop hook.

The fix: every read-modify-write of the file — a single write, an increment, the reset
at a prompt, the gate's repeat count — happens under ONE lock per session
(`counters.lock`, flock), with a time limit. A step that was not given the lock writes
NOTHING to the counters (the first build let it go on without the lock; three review
rounds each found a way for such a write to release a stop).

These tests run the REAL hooks as overlapping processes and compare what the counters
say with the number of calls; then they hold the lock themselves and look at what each
writer does while it is held. A test that needs a writer to END while the lock is held
(the unfixed behaviour) gives it a window; on the fixed code that window is only time.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip
from tests.test_notification_turn import NOTIFICATIONS, SCRIPTS, SID, _Session

LIB = SCRIPTS / "gate-echo-lib.sh"
WINDOW = 1.0      # seconds a writer that does NOT wait for the lock has to finish in
NOTICE = "counters lock"


def _needs_flock(test):
    return unittest.skipUnless(shutil.which("flock"), "no flock(1) on this host")(test)


class _Counters(_Session):
    def setUp(self):
        super().setUp()
        self.lock_file = self.session_dir / "counters.lock"
        self.counters_file.write_text("tools=0\nwrites=0\n", encoding="utf-8")
        self._payloads = 0

    def _env(self, **extra):
        env = dict(os.environ)
        env["PLAYBOOK_SESSION_ID"] = SID
        env["HOME"] = self._tmp.name
        env["PLAYBOOK_PROC_ROOT"] = str(self.proc_root)
        # counting is what these tests look at, not the time limit: a loaded machine
        # must not turn a long queue into a lost count
        env["PLAYBOOK_COUNTERS_LOCK_WAIT"] = "2"
        for k in ("BASH_ENV", "PLAYBOOK_ROLE", "PLAYBOOK_EVAL_CONFIG", "PLAYBOOK_SANDBOXED"):
            env.pop(k, None)
        env.update(extra)
        return env

    def _start(self, argv, payload=None, **extra):
        """One process, started and NOT waited for. Its input comes from a file, so
        nothing of it depends on this test still writing to it."""
        self._payloads += 1
        src = Path(self._tmp.name) / f"payload-{self._payloads}.json"
        src.write_text(json.dumps(payload) if payload is not None else "", encoding="utf-8")
        with src.open(encoding="utf-8") as stdin:
            return subprocess.Popen(argv, cwd=self.project, env=self._env(**extra), stdin=stdin,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def start_tool(self, name="Bash", **extra):
        return self._start([bash_or_skip(), str(SCRIPTS / "state-echo-hook")],
                           {"tool_name": name, "tool_input": {"command": "true"}}, **extra)

    def start_lib(self, script, **extra):
        """A bash script with the hooks' library sourced, under `set -e` as the hooks are."""
        return self._start([bash_or_skip(), "-c", f'set -e\nsource "{LIB}"\nF="{self.counters_file}"\n{script}'],
                           **extra)

    def finish(self, proc):
        out, err = proc.communicate(timeout=120)
        return proc.returncode, out, err

    def overlapping(self, *names, **extra):
        """The hooks of tool calls the host ran side by side: all started, then all waited for."""
        procs = [self.start_tool(n, **extra) for n in names]
        done = [self.finish(p) for p in procs]
        for rc, out, err in done:
            self.assertEqual(rc, 0, err)
            self.assertNotIn(NOTICE, out)
        return done

    def _flock_shim(self, refuse=True, at_once=False):
        """A `flock` that notes how the counters lock (descriptor 204) was asked for — and
        refuses it as a wait that ran out does (after the seconds it was asked to wait), or
        AT ONCE (a `flock` that cannot wait with a limit), or hands the request to the real
        program; every other lock is the real program's."""
        d = Path(self._tmp.name) / "shim"
        d.mkdir()
        self.asked = Path(self._tmp.name) / "flock-asked"
        shim = d / "flock"
        does = "exit 1" if at_once else ('[ "$1" != "-w" ] || sleep "$2"; exit 1' if refuse else ":")
        shim.write_text(f'#!/bin/sh\ncase " $* " in *" 204 "*) echo "$*" >> "{self.asked}"; {does} ;; esac\n'
                        f'exec "{shutil.which("flock")}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        return str(d) + os.pathsep + os.environ["PATH"]

    def _asked(self):
        return self.asked.read_text(encoding="utf-8").splitlines() if self.asked.exists() else []

    @contextlib.contextmanager
    def held(self):
        """The session's lock, held by this test as another hook would hold it."""
        fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def while_held(self, start, meanwhile):
        """Hold the lock, start a writer, give it WINDOW to end if it does not wait,
        then do `meanwhile` and let go. Returns (did it wait, rc, stdout, stderr)."""
        with self.held():
            proc = start()
            try:
                proc.wait(timeout=WINDOW)
                waited = False
            except subprocess.TimeoutExpired:
                waited = True
            meanwhile()
        return (waited, *self.finish(proc))


@_needs_flock
class OverlappingToolCallsAreAllCounted(_Counters):
    """The measured loss, as tests: what the counters say against how many calls there were."""

    def test_two_at_a_time(self):
        for _ in range(10):
            self.overlapping("Bash", "Bash")
        self.assertEqual(self.counts(), (20, 20))

    def test_six_at_a_time(self):
        for _ in range(4):
            self.overlapping(*["Bash"] * 6)
        self.assertEqual(self.counts(), (24, 24))

    def test_a_write_beside_calls_that_do_not_write(self):
        # the case that matters to the stop hook: a Read's hook renaming its copy of
        # the file over the one write of the turn
        for _ in range(6):
            self.overlapping("Read", "Bash", "Grep", "Read")
        self.assertEqual(self.counts(), (24, 6))

    def test_every_kind_of_writing_tool(self):
        self.overlapping("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "Read")
        self.assertEqual(self.counts(), (6, 5))

    def test_the_gates_repeat_count(self):
        # the same file, another key: the number the hook shows as "(N tool calls)"
        for _ in range(3):
            self.overlapping(*["Read"] * 5)
        self.assertEqual(self.counters()["gate_count"], "15")
        self.assertEqual(self.counts(), (15, 0))

    def test_control_one_at_a_time(self):
        self.tools(*["Bash"] * 8)
        self.assertEqual(self.counts(), (8, 8))
        self.assertEqual(self.counters()["gate_count"], "8")


@_needs_flock
class AGatesCompletionIsLoggedOnce(_Counters):
    """The step that notices a ticked gate reads the previous gate from the counters and
    writes the new one back. Side by side, every call saw the OLD gate and each logged
    its completion."""

    def setUp(self):
        super().setUp()
        self.task_file.write_text(
            "# 001 - x\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] the first gate\n- [ ] the second gate\n",
            encoding="utf-8")
        self.chat_log = self.project / ".agent" / "chat_log.md"
        self.tools("Read")                                   # the hook now knows gate 001:7
        self.assertEqual(self.counters()["gate_key"], "001:7")
        self.task_file.write_text(
            self.task_file.read_text(encoding="utf-8").replace("- [ ] the first gate", "- [x] the first gate — done"),
            encoding="utf-8")

    def _logged(self):
        if not self.chat_log.exists():
            return 0
        return self.chat_log.read_text(encoding="utf-8").count("**[G001:7]**")

    def test_overlapping_calls_after_a_gate_is_ticked(self):
        done = self.overlapping(*["Read"] * 6)
        self.assertEqual(self._logged(), 1)
        said = [d[1] for d in done if "previous gate done" in d[1]]
        self.assertEqual(len(said), 1, "the completion is told to one call, the one that saw it")
        self.assertEqual(self.counters()["gate_key"], "001:8")
        self.assertEqual(self.counters()["gate_count"], "6")

    def test_control_one_call_logs_it(self):
        self.tools("Read")
        self.assertEqual(self._logged(), 1)
        self.assertEqual(self.counters()["gate_count"], "1")

    def test_the_last_gate_is_logged_once_too_and_the_gate_is_forgotten(self):
        # no gate left open: the entry of the last one, once, and the counters hold no gate
        self.tools("Read")                                               # 001:7 logged, now on 001:8
        self.task_file.write_text(
            self.task_file.read_text(encoding="utf-8").replace("- [ ] the second gate", "- [x] the second gate — done"),
            encoding="utf-8")
        self.overlapping(*["Read"] * 4)
        log = self.chat_log.read_text(encoding="utf-8")
        self.assertEqual(log.count("**[G001:8]**"), 1)
        self.assertIn("- [x] the second gate", log)
        self.assertEqual({k: v for k, v in self.counters().items() if k.startswith("gate_")},
                         {"gate_key": "", "gate_count": "", "gate_text": ""})


@_needs_flock
class EveryWriterWaitsForTheLock(_Counters):
    """Each writer of the file, started while the lock is held: it must still be waiting
    after WINDOW, and what it writes must be built on what the file holds when it is let in."""

    def _put(self, text):
        self.counters_file.write_text(text, encoding="utf-8")

    def test_a_tool_calls_count(self):
        waited, rc, out, err = self.while_held(
            self.start_tool, lambda: self._put("tools=100\nwrites=100\n"))
        self.assertEqual(rc, 0, err)
        self.assertTrue(waited, "the hook counted the call while another writer held the lock")
        self.assertEqual(self.counts(), (101, 101))
        self.assertNotIn(NOTICE, out)

    def test_a_single_write(self):
        waited, rc, _, err = self.while_held(
            lambda: self.start_lib('write_counter "$F" gate_key "001:9"'),
            lambda: self._put("tools=5\nwrites=4\n"))
        self.assertEqual((rc, err), (0, ""))
        self.assertTrue(waited, "write_counter rewrote the file while another writer held the lock")
        self.assertEqual(self.counters(), {"tools": "5", "writes": "4", "gate_key": "001:9"})

    def test_the_reset_at_a_prompt(self):
        # the real chat hook, a user's prompt: the counts go to zero, the gate's lines stay
        waited, rc, _, err = self.while_held(
            lambda: self._start([bash_or_skip(), str(SCRIPTS / "chat-log-hook")], {"prompt": "a plain user prompt"}),
            lambda: self._put("tools=7\nwrites=7\ngate_key=001:7\ngate_count=3\n"))
        self.assertEqual(rc, 0, err)
        self.assertTrue(waited, "the reset rewrote the file while another writer held the lock")
        self.assertEqual(self.counters(), {"tools": "0", "writes": "0", "gate_key": "001:7", "gate_count": "3"})

    def test_a_step_under_the_lock_does_not_wait_for_itself(self):
        # write_counter inside a caller's lock: one lock, taken once. Seen from outside:
        # held between the pair, free after it.
        probe = f'flock -n "{self.lock_file}" true && echo free || echo held'
        proc = self.start_lib(
            'counters_lock "$F"\n'
            'write_counter "$F" tools 3\nwrite_counter "$F" gate_key "001:7"\n'
            f'echo "inside: $({probe})"\n'
            'counters_unlock\n'
            f'echo "after: $({probe})"\n'
            'echo "unlocked=$PLAYBOOK_COUNTERS_UNLOCKED"\n', PLAYBOOK_COUNTERS_LOCK_WAIT="3")
        started = time.monotonic()
        rc, out, err = self.finish(proc)
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(out.splitlines(), ["inside: held", "after: free", "unlocked=false"])
        self.assertEqual(self.counters(), {"tools": "3", "writes": "0", "gate_key": "001:7"})
        self.assertLess(time.monotonic() - started, 3, "a step waited the whole limit for its own lock")

    def test_a_tool_call_asks_for_the_lock_twice(self):
        # once for the count, once for the gate — however many keys each of the two
        # steps writes: a write inside a held lock does not let go of it and take it again
        rc, out, err = self.finish(self.start_tool(PATH=self._flock_shim(refuse=False)))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self._asked(), ["-w 2 204", "-w 2 204"])
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(self.counters()["gate_count"], "1")
        self.assertNotIn(NOTICE, out)


@_needs_flock
class TheLockIsHeldOnlyForItsTwoSteps(_Counters):
    """The hook does more than count: it reads the payload, finds the task, builds its
    answer. None of that is done with the lock held — the other calls' hooks wait for
    the two short steps only. Seen through the helper the hook starts before, between
    and after them: a `python3` in front of the real one that first looks at the lock."""

    def _python_shim(self):
        d = Path(self._tmp.name) / "py-shim"
        d.mkdir()
        self.seen = Path(self._tmp.name) / "lock-seen"
        shim = d / "python3"
        shim.write_text(
            f'#!/bin/sh\nif [ ! -e "{self.lock_file}" ] || flock -n "{self.lock_file}" true 2>/dev/null; then\n'
            f'    echo free >> "{self.seen}"\nelse\n    echo held >> "{self.seen}"\nfi\n'
            f'exec "{shutil.which("python3")}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        return str(d) + os.pathsep + os.environ["PATH"]

    def _seen(self):
        return self.seen.read_text(encoding="utf-8").split() if self.seen.exists() else []

    def test_every_helper_the_hook_starts_finds_the_lock_free(self):
        path = self._python_shim()
        for _ in range(2):          # the second call has a lock file from the start
            rc, _, err = self.finish(self.start_tool(PATH=path))
            self.assertEqual(rc, 0, err)
        seen = self._seen()
        self.assertGreaterEqual(len(seen), 4, "the hook starts python3 for its payload and for its answer")
        self.assertEqual(set(seen), {"free"})
        self.assertEqual(self.counts(), (2, 2))

    def test_control_the_same_look_sees_a_lock_that_is_held(self):
        path = self._python_shim()
        with self.held():
            rc, _, err = self.finish(self.start_tool(PATH=path, PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(set(self._seen()), {"held"})


@_needs_flock
class TheLockHasATimeLimit(_Counters):
    """A lock that is never let go must not hold a tool call: after the limit the hook goes
    on — the call is NOT counted, nothing is written to the counters, and the hook says so."""

    def test_a_lock_that_is_never_let_go(self):
        with self.held():
            started = time.monotonic()
            rc, out, err = self.finish(self.start_tool(PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
            took = time.monotonic() - started
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.counts(), (0, 0))          # nothing is written without the lock (round 3)
        self.assertGreaterEqual(took, 0.9, "the hook did not wait for the lock at all")
        self.assertIn(NOTICE, out)
        self.assertIn("was NOT counted", out)
        self.assertIn("the stop hook holds this turn", out)             # what it costs, said where it is seen
        self.assertIn("1s", out)
        json.loads(out)                                  # … and it is still the hook's answer

    def test_the_limit_is_waited_once_for_a_whole_call(self):
        # the hook takes the lock for the count, again for the gate, and each write in
        # between asks too: a holder that never lets go costs ONE limit, not seven
        rc, out, err = self.finish(self.start_tool(PATH=self._flock_shim(), PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self._asked(), ["-w 1 204"])
        self.assertEqual(self.counts(), (0, 0))
        self.assertIn(NOTICE, out)

    def test_two_seconds_unless_told_otherwise(self):
        env = self._env(PATH=self._flock_shim(refuse=False))
        del env["PLAYBOOK_COUNTERS_LOCK_WAIT"]
        # 1 or 2: the host kills a hook after 5 seconds, and a hook that is killed while it
        # waits has neither counted the call nor said anything (the setting reached 99 at first, then 3)
        for value, want in ((None, "2"), ("1", "1"), ("2", "2"), ("3", "2"), ("4", "2"), ("7", "2"), ("99", "2"), ("0", "2"),
                            ("-1", "2"), ("1.5", "2"), ("abc", "2"), ("1; touch pwned", "2"), ("", "2")):
            with self.subTest(value=value):
                self.asked.unlink(missing_ok=True)
                e = dict(env)
                if value is not None:
                    e["PLAYBOOK_COUNTERS_LOCK_WAIT"] = value
                r = subprocess.run([bash_or_skip(), "-c", f'set -e\nsource "{LIB}"\nwrite_counter "{self.counters_file}" k v'],
                                   cwd=self.project, env=e, capture_output=True, text=True, timeout=60)
                self.assertEqual((r.returncode, r.stderr), (0, ""))
                self.assertEqual(self._asked(), [f"-w {want} 204"])
                self.assertFalse((self.project / "pwned").exists())

    def test_a_judges_run_is_not_told(self):
        # a sandboxed judge's hooks cannot write the session at all; the hook's notices
        # about its own writes are kept out of every verdict
        rc, out, err = self.finish(self.start_tool(PATH=self._flock_shim(), PLAYBOOK_SANDBOXED="1"))
        self.assertEqual(rc, 0, err)
        self.assertNotIn(NOTICE, out)


@_needs_flock
class ACountMadeWithoutTheLockHoldsTheStop(_Counters):
    """Where a lock exists and was not given, the call is NOT counted and nothing is written
    to the counters — so the counts are behind, and a count that is too low is what lets the
    stop hook take a turn for a chat reply. The session is MARKED (file `counts_unsure`), and
    the stop hook uses neither of its low-activity releases until a prompt of the user's has
    reset the counts under the lock (three review rounds, a counted Critical in each)."""

    def setUp(self):
        super().setUp()
        self.unsure = self.session_dir / "counts_unsure"

    def _a_read_while_the_lock_is_held(self):
        with self.held():
            rc, out, err = self.finish(self.start_tool("Read", PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
        self.assertEqual(rc, 0, err)
        self.assertIn(NOTICE, out)

    def test_control_a_reply_with_one_read_may_end(self):
        self.prompt()
        self.tools("Read")
        self.assertFalse(self.unsure.exists())
        self.assertEqual(self.stop().returncode, 0)

    def test_a_turn_counted_without_the_lock_is_held_like_one_that_wrote(self):
        self.prompt()
        self._a_read_while_the_lock_is_held()
        self.assertTrue(self.unsure.exists(), "nothing marks that the count was made without the lock")
        r = self.stop()
        self.assertEqual(r.returncode, 2, "released on counts that were made without the lock")
        # … and the user's next prompt, whose reset IS made under the lock, clears it
        self.prompt()
        self.assertFalse(self.unsure.exists())
        self.tools("Read")
        self.assertEqual(self.stop().returncode, 0)

    def test_a_prompt_whose_own_reset_could_not_take_the_lock_leaves_it_unsure(self):
        self.prompt()
        self.tools("Bash")                                   # counted under the lock: (1, 1)
        self._a_read_while_the_lock_is_held()
        with self.held():
            self._n = getattr(self, "_n", 0) + 1
            r = self._hook("chat-log-hook", {"prompt": f"a prompt while the lock is held, {self._n}"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.unsure.exists())
        self.assertEqual(self.counts(), (1, 1))          # the Read was not counted, and NOTHING was reset
        self.assertEqual(self.stop().returncode, 2)

    def _a_turn_a_notification_started(self, one_read_without_the_lock):
        """Five reads and no write in a turn that a notification began — the owner's
        exception lets such a turn end, however many calls it made."""
        self.a_turn_that_ended()
        self.prompt(NOTIFICATIONS[0])
        self.assertTrue(self.notif_start.exists(), "the notification did not begin a turn")
        if one_read_without_the_lock:
            self._a_read_while_the_lock_is_held()
        self.tools(*["Read"] * 5)
        return self.stop()

    def test_control_a_turn_a_notification_started_may_end_without_a_write(self):
        self.assertEqual(self._a_turn_a_notification_started(False).returncode, 0)

    def test_the_notification_exception_is_off_too_while_the_mark_stands(self):
        # the exception reads `writes` as the chat-reply release does (impl round 2, opus: untested)
        r = self._a_turn_a_notification_started(True)
        self.assertTrue(self.unsure.exists())
        self.assertEqual(r.returncode, 2, "released by the notification exception on counts made without the lock")

    def test_a_call_that_was_not_given_the_lock_writes_nothing_to_the_counters(self):
        # Round 2 left the mark again AFTER a write made without the lock had landed; between
        # that rename and the mark the stop hook could read the old `writes` (impl round 3,
        # codex — counted Critical for the third time on this path). Where there is a lock and
        # it is not given, the counters are not written at all: no copy, no rename, nothing
        # that could land later. The mark says the counts are behind.
        d = Path(self._tmp.name) / "mv-log"
        d.mkdir()
        log = Path(self._tmp.name) / "mv-calls"
        (d / "mv").write_text(f'#!/bin/sh\necho "$*" >> "{log}"\nexec "{shutil.which("mv")}" "$@"\n', encoding="utf-8")
        (d / "mv").chmod(0o755)
        self.prompt()
        self.tools("Bash")
        before = self.counters_file.read_bytes()
        with self.held():
            rc, out, err = self.finish(self.start_tool("Bash", PATH=str(d) + os.pathsep + os.environ["PATH"],
                                                       PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.counters_file.read_bytes(), before)
        renamed = [ln for ln in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []) if "counters" in ln]
        self.assertEqual(renamed, [], "a copy of the counters was renamed without the lock")
        self.assertTrue(self.unsure.exists())
        self.assertEqual(self.stop().returncode, 2)
        # the user's next prompt resets under the lock; what is counted after it stays counted
        self.prompt()
        self.assertFalse(self.unsure.exists())
        self.tools("Bash")
        self.assertEqual(self.counts(), (1, 1))

    def test_a_flock_that_cannot_wait_is_no_lock_at_all(self):
        # A `flock` without the timed wait (BusyBox) refuses every request AT ONCE. That is
        # not a lock that was not given in time: read as one, the mark never cleared and
        # every call was warned for the whole session (impl round 3, opus). It is a host
        # with no lock to take — counted as before the task, nothing said, nothing marked.
        path = self._flock_shim(at_once=True)
        for _ in range(3):
            rc, out, err = self.finish(self.start_tool(PATH=path))
            self.assertEqual(rc, 0, err)
            self.assertNotIn(NOTICE, out)
        self.assertEqual(self.counts(), (3, 3))
        self.assertFalse(self.unsure.exists())
        self.assertEqual(len(self._asked()), 3, "asked again after it was known that this flock cannot wait")

    def test_which_key_was_not_written_decides_the_mark(self):
        # the library alone, the lock held: a count that is not made marks the session —
        # either count — and a gate field that is not written does not
        for key, marked in (("tools", True), ("writes", True), ("gate_key", False), ("gate_count", False)):
            with self.subTest(key=key):
                self.unsure.unlink(missing_ok=True)
                before = self.counters_file.read_bytes()
                with self.held():
                    rc, out, err = self.finish(self.start_lib(
                        f'write_counter "$F" {key} 7\necho "unlocked=$PLAYBOOK_COUNTERS_UNLOCKED"',
                        PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
                self.assertEqual((rc, err, out.strip()), (0, "", "unlocked=true"))
                self.assertEqual(self.counters_file.read_bytes(), before)
                self.assertEqual(self.unsure.exists(), marked)

    def test_a_flock_that_refuses_marks_it_too(self):
        rc, out, err = self.finish(self.start_tool(PATH=self._flock_shim(), PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.unsure.exists())

    def test_something_that_is_no_file_in_the_locks_place(self):
        # a FIFO there made the OPEN wait for ever, before any time limit (codex); a
        # directory or a link is no lock either
        (self.session_dir / "a-real-file").write_text("", encoding="utf-8")
        for what in ("fifo", "directory", "link", "link to a file"):
            with self.subTest(what=what):
                if self.lock_file.is_dir() and not self.lock_file.is_symlink():
                    os.rmdir(self.lock_file)
                elif os.path.lexists(self.lock_file):
                    os.unlink(self.lock_file)
                self.unsure.unlink(missing_ok=True)
                self.counters_file.write_text("tools=0\nwrites=0\n", encoding="utf-8")
                if what == "fifo":
                    os.mkfifo(self.lock_file)
                elif what == "directory":
                    os.mkdir(self.lock_file)
                elif what == "link":
                    os.symlink(self.session_dir / "elsewhere", self.lock_file)
                else:
                    os.symlink(self.session_dir / "a-real-file", self.lock_file)
                proc = self.start_tool(PLAYBOOK_COUNTERS_LOCK_WAIT="1")
                try:
                    out, err = proc.communicate(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    self.fail("the hook did not come back: it waits on what stands in the lock's place")
                self.assertEqual(proc.returncode, 0, err)
                self.assertEqual(self.counts(), (0, 0))
                self.assertIn(NOTICE, out)
                self.assertTrue(self.unsure.exists())
                self.assertFalse((self.session_dir / "elsewhere").exists(), "the link was written through")

    @unittest.skipIf(os.geteuid() == 0, "root opens a file of mode 000")
    def test_a_lock_file_that_cannot_be_opened_where_it_could_be_made(self):
        self.lock_file.write_text("", encoding="utf-8")
        os.chmod(self.lock_file, 0)
        self.addCleanup(os.chmod, self.lock_file, 0o644)
        rc, out, err = self.finish(self.start_tool(PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.counts(), (0, 0))
        self.assertIn(NOTICE, out)
        self.assertTrue(self.unsure.exists())


@_needs_flock
class TheGateIsReadUnderTheLock(_Counters):
    """The step that notices a ticked gate compares the gate it READ from task.md with the
    one the counters hold. Read before the lock, a hook's gate could be older than what a
    hook that overtook it had written: it then logged the OPEN gate as done and put the old
    one back (impl round 1, opus). And what it appends to the chat log is appended after the
    counters lock is let go: it waited for the chat log's lock, without a limit, while every
    other call's hook waited for ours (codex)."""

    def setUp(self):
        super().setUp()
        self.task_file.write_text(
            "# 001 - x\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] the first gate\n- [ ] the second gate\n",
            encoding="utf-8")
        self.chat_log = self.project / ".agent" / "chat_log.md"
        self.tools("Read")
        self.assertEqual(self.counters()["gate_key"], "001:7")

    def _tick_the_first_gate(self):
        self.task_file.write_text(
            self.task_file.read_text(encoding="utf-8").replace("- [ ] the first gate", "- [x] the first gate — done"),
            encoding="utf-8")

    def _log(self):
        return self.chat_log.read_text(encoding="utf-8") if self.chat_log.exists() else ""

    def _held_at_its_second_lock(self):
        """A `flock` in front of the real one that makes ONE hook wait at its second request
        for the counters lock — the gate step's — until the test lets it go."""
        d = Path(self._tmp.name) / "hold-shim"
        d.mkdir()
        mark = Path(self._tmp.name) / "hold"
        (d / "flock").write_text(
            '#!/bin/sh\n'
            'case " $* " in *" 204 "*)\n'
            '  if [ -n "$PB_TEST_HOLD" ]; then\n'
            '    echo x >> "$PB_TEST_HOLD.calls"\n'
            '    if [ "$(wc -l < "$PB_TEST_HOLD.calls")" -eq 2 ]; then\n'
            '      : > "$PB_TEST_HOLD.waiting"\n'
            '      while [ ! -e "$PB_TEST_HOLD.go" ]; do sleep 0.05; done\n'
            '    fi\n'
            '  fi ;;\n'
            'esac\n'
            f'exec "{shutil.which("flock")}" "$@"\n', encoding="utf-8")
        (d / "flock").chmod(0o755)
        return str(d) + os.pathsep + os.environ["PATH"], mark

    def test_a_tick_that_lands_between_two_overlapping_calls(self):
        path, mark = self._held_at_its_second_lock()
        slow = self.start_tool("Read", PATH=path, PB_TEST_HOLD=str(mark))
        deadline = time.monotonic() + 20
        while not Path(str(mark) + ".waiting").exists():
            self.assertLess(time.monotonic(), deadline, "the first hook never reached its second lock")
            time.sleep(0.02)
        # the gate is ticked, and another call's hook — started after the tick — goes through
        self._tick_the_first_gate()
        self.tools("Read")
        self.assertEqual(self.counters()["gate_key"], "001:8")
        self.assertEqual(self._log().count("**[G001:7]**"), 1)
        Path(str(mark) + ".go").write_text("", encoding="utf-8")
        rc, out, err = self.finish(slow)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("**[G001:8]**", self._log(), "the open gate was logged as done")
        self.assertEqual(self._log().count("**[G001:7]**"), 1)
        self.assertEqual(self.counters()["gate_key"], "001:8")
        self.assertEqual(self.counters()["gate_count"], "2")
        self.assertNotIn("previous gate done", out)

    def test_a_gate_whose_entry_cannot_be_written_yet_is_not_advanced(self):
        # Three rounds on one line. Appended under our lock with an unlimited wait for the
        # chat log's, the entry made every other hook wait (round 1); appended after ours
        # was let go, it could be lost with the hook (round 2); appended without the chat
        # log's lock after a second, it could be lost under a `tasks tag` rewrite (round 3);
        # and a second of waiting under ours starved the hooks behind (post-panel run 1).
        # So: the chat log's lock is TRIED under ours, not waited for; if it is not given,
        # nothing is appended and the gate is NOT advanced — the next call finds the old
        # gate and tries again.
        self._tick_the_first_gate()
        chat_lock = self.project / ".agent" / "chat_log_counter.lock"
        fd = os.open(chat_lock, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)                       # … and not let go while the first hook runs
        proc = self.start_tool("Read")
        try:
            try:
                out, err = proc.communicate(timeout=20)
                ended = True
            except subprocess.TimeoutExpired:
                ended = False
            seen = (self.counters().get("gate_key"), self._log().count("**[G001:7]**"))
        finally:
            os.close(fd)
            if proc.poll() is None:
                out, err = proc.communicate(timeout=60)
        self.assertTrue(ended, "the hook waited for the chat log's lock for as long as it was held")
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(seen, ("001:7", 0), "(gate key, entries) after a hook that was not given the chat log's lock")
        self.assertNotIn("previous gate done", out)
        self.assertEqual(self.counts(), (2, 0))              # the call itself is counted
        # the lock is free again: the next call logs the gate, once, and moves on
        done = self.overlapping(*["Read"] * 3)
        self.assertEqual(self._log().count("**[G001:7]**"), 1)
        self.assertEqual(len([d for d in done if "previous gate done" in d[1]]), 1)
        self.assertEqual(self.counters()["gate_key"], "001:8")

    def test_six_calls_side_by_side_while_the_chat_logs_lock_is_held(self):
        # The single judge ran this (post-panel run 1): gate ticked, the chat log's lock held,
        # six overlapping Bash calls. All six were counted — and three answers said "this
        # call was NOT counted", the session was marked and the stop held: each hook waited
        # a second for the chat log's lock under ours, and the ones behind ran out of their
        # own wait at the GATE step, after their count was made. The chat log's lock is
        # tried once, not waited for; and a hook says "not counted" only of a count.
        self._tick_the_first_gate()
        before = self.counts()
        chat_lock = self.project / ".agent" / "chat_log_counter.lock"
        fd = os.open(chat_lock, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            procs = [self.start_tool("Bash") for _ in range(6)]
            done = [self.finish(p) for p in procs]
        finally:
            os.close(fd)
        for rc, out, err in done:
            self.assertEqual(rc, 0, err)
            self.assertNotIn(NOTICE, out)
        self.assertEqual(self.counts(), (before[0] + 6, before[1] + 6))
        self.assertFalse((self.session_dir / "counts_unsure").exists())
        self.assertEqual((self.counters()["gate_key"], self._log().count("**[G001:7]**")), ("001:7", 0))
        self.tools("Read")
        self.assertEqual((self.counters()["gate_key"], self._log().count("**[G001:7]**")), ("001:8", 1))

    def test_a_call_that_was_counted_and_only_lost_the_gate_step_says_that(self):
        # our lock given for the count and not given again for the gate step: the count is
        # sure — no mark, the stop not held — and the answer does not say "not counted"
        d = Path(self._tmp.name) / "second-shim"
        d.mkdir()
        calls = Path(self._tmp.name) / "second-calls"
        (d / "flock").write_text(
            '#!/bin/sh\n'
            'case " $* " in *" 204 "*)\n'
            f'  echo x >> "{calls}"\n'
            f'  if [ "$(wc -l < "{calls}")" -eq 2 ]; then [ "$1" != "-w" ] || sleep "$2"; exit 1; fi ;;\n'
            'esac\n'
            f'exec "{shutil.which("flock")}" "$@"\n', encoding="utf-8")
        (d / "flock").chmod(0o755)
        self.prompt()
        before = self.counts()
        rc, out, err = self.finish(self.start_tool("Read", PATH=str(d) + os.pathsep + os.environ["PATH"],
                                                   PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.counts(), (before[0] + 1, before[1]))
        self.assertIn(NOTICE, out)
        self.assertNotIn("NOT counted", out)
        self.assertIn("the gate was not looked at", out)
        self.assertFalse((self.session_dir / "counts_unsure").exists())
        self.assertEqual(self.stop().returncode, 0)          # a reply with one counted Read may end

    def _a_flock_that_refuses_the_chat_logs_lock(self, ours_too):
        d = Path(self._tmp.name) / ("refuse-both" if ours_too else "refuse-202")
        d.mkdir()
        ours = '*" 204 "*) exit 1 ;; ' if ours_too else ""
        (d / "flock").write_text(f'#!/bin/sh\ncase " $* " in *" 202 "*) exit 1 ;; {ours}esac\n'
                                 f'exec "{shutil.which("flock")}" "$@"\n', encoding="utf-8")
        (d / "flock").chmod(0o755)
        return str(d) + os.pathsep + os.environ["PATH"]

    def test_a_gate_whose_entry_stays_unwritten_is_said_at_every_call(self):
        # "not yet" has no end of its own: a holder of the chat log's lock that never lets
        # go froze the gate with nothing said (post-panel run 2, run by the judge)
        self._tick_the_first_gate()
        path = self._a_flock_that_refuses_the_chat_logs_lock(ours_too=False)
        for _ in range(2):
            rc, out, err = self.finish(self.start_tool("Read", PATH=path))
            self.assertEqual(rc, 0, err)
            self.assertIn("is not logged yet", out)
            self.assertNotIn("previous gate done", out)
        self.assertEqual((self.counters()["gate_key"], self._log().count("**[G001:7]**")), ("001:7", 0))
        self.tools("Read")                                               # the real flock again
        self.assertEqual((self.counters()["gate_key"], self._log().count("**[G001:7]**")), ("001:8", 1))

    def test_where_there_is_no_lock_to_take_the_entry_is_appended_without_one(self):
        # a `flock` that refuses every request at once: a host with no lock to take — the
        # gate is logged as on a host without `flock`, not held for ever
        self._tick_the_first_gate()
        path = self._a_flock_that_refuses_the_chat_logs_lock(ours_too=True)
        rc, out, err = self.finish(self.start_tool("Read", PATH=path))
        self.assertEqual(rc, 0, err)
        self.assertNotIn(NOTICE, out)
        self.assertNotIn("is not logged yet", out)
        self.assertIn("previous gate done", out)
        self.assertEqual((self.counters()["gate_key"], self._log().count("**[G001:7]**")), ("001:8", 1))

    def test_the_last_gate_waits_for_its_entry_too(self):
        # no gate left open: the counters forget the gate only once its entry is written
        self._tick_the_first_gate()
        self.tools("Read")                                               # 001:7 logged, now on 001:8
        self.task_file.write_text(
            self.task_file.read_text(encoding="utf-8").replace("- [ ] the second gate", "- [x] the second gate — done"),
            encoding="utf-8")
        chat_lock = self.project / ".agent" / "chat_log_counter.lock"
        fd = os.open(chat_lock, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            rc, out, err = self.finish(self.start_tool("Read"))
        finally:
            os.close(fd)
        self.assertEqual(rc, 0, err)
        self.assertIn("is not logged yet", out)
        self.assertEqual((self.counters()["gate_key"], self._log().count("**[G001:8]**")), ("001:8", 0))
        self.tools("Read")
        self.assertEqual((self.counters()["gate_key"], self._log().count("**[G001:8]**")), ("", 1))

    def test_a_call_that_was_not_given_the_counters_lock_logs_no_gate(self):
        # it cannot write the new gate, so it must not write the entry either
        self._tick_the_first_gate()
        with self.held():
            rc, out, err = self.finish(self.start_tool("Read", PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
        self.assertEqual(rc, 0, err)
        self.assertIn(NOTICE, out)
        self.assertNotIn("previous gate done", out)
        self.assertNotIn("is not logged yet", out)           # one reason is said, the true one
        self.assertEqual((self.counters()["gate_key"], self._log().count("**[G001:7]**")), ("001:7", 0))
        self.overlapping("Read", "Read")
        self.assertEqual((self.counters()["gate_key"], self._log().count("**[G001:7]**")), ("001:8", 1))

    def test_a_chat_log_that_cannot_be_appended_to_is_said(self):
        # Parked by this task's first build and closed here, on the same lines: with `flock`
        # on the host the append ran in a subshell and its failure never came back — the
        # hook's "write FAILED" line was never printed (measured before: "with flock: … the
        # hook says the write FAILED: False"). The gate still moves on: a log that cannot
        # be written must not hold it for ever.
        self._tick_the_first_gate()
        self.chat_log.unlink(missing_ok=True)
        self.chat_log.mkdir()
        rc, out, err = self.finish(self.start_tool("Read"))
        self.assertEqual(rc, 0, err)
        self.assertIn("write FAILED", out)
        self.assertEqual(self.counters()["gate_key"], "001:8")


@_needs_flock
class OneWaitForAWholeCall(_Counters):
    """A hook takes the lock twice. Given late both times, it waited the limit twice — six
    seconds at the setting 3, with the host killing a hook after five (impl round 2, opus).
    The two holds share ONE wait."""

    def test_what_the_first_hold_waited_is_taken_off_the_second(self):
        d = Path(self._tmp.name) / "slow-shim"
        d.mkdir()
        self.asked = Path(self._tmp.name) / "flock-asked"
        (d / "flock").write_text(
            '#!/bin/sh\n'
            'case " $* " in *" 204 "*)\n'
            f'  echo "$*" >> "{self.asked}"\n'
            f'  [ "$(wc -l < "{self.asked}")" -ne 1 ] || sleep 1.4 ;;\n'
            'esac\n'
            f'exec "{shutil.which("flock")}" "$@"\n', encoding="utf-8")
        (d / "flock").chmod(0o755)
        rc, out, err = self.finish(self.start_tool(PATH=str(d) + os.pathsep + os.environ["PATH"]))
        self.assertEqual(rc, 0, err)
        asked = self._asked()
        self.assertEqual(len(asked), 2, asked)
        self.assertEqual(asked[0], "-w 2 204")
        self.assertIn(asked[1], ("-w 1 204", "-n 204"), "the second hold was given the whole limit again")
        self.assertEqual(self.counts(), (1, 1))
        self.assertNotIn(NOTICE, out)


class WhereNoLockCanBeHad(_Counters):
    """Controls: the two places where there is no lock to take. The step goes on as it
    always did — nothing waits, nothing dies under `set -e`, nothing new is printed."""

    def _path_without(self, tool):
        d = Path(self._tmp.name) / f"path-without-{tool}"
        d.mkdir()
        for src in os.environ["PATH"].split(os.pathsep):
            try:
                names = os.listdir(src)
            except OSError:
                continue
            for name in names:
                if name != tool and not os.path.lexists(d / name):
                    os.symlink(os.path.join(src, name), d / name)
        return str(d)

    def test_without_flock_on_the_host(self):
        path = self._path_without("flock")
        for _ in range(3):
            rc, out, err = self.finish(self.start_tool(PATH=path))
            self.assertEqual(rc, 0, err)
            self.assertNotIn(NOTICE, out)
        self.assertEqual(self.counts(), (3, 3))
        self.assertFalse(self.lock_file.exists(), "a lock file was made on a host that cannot lock")

    @unittest.skipIf(os.geteuid() == 0, "root writes a read-only directory")
    def test_a_session_the_hook_must_not_write_gets_no_lock_file(self):
        # the hook writes nothing where the agent directory is read-only (a judge's
        # project) — whatever the session directory under it would allow
        agent = self.project / ".agent"
        os.chmod(agent, 0o555)
        self.addCleanup(os.chmod, agent, 0o755)
        rc, out, err = self.finish(self.start_tool())
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(sorted(p.name for p in self.session_dir.iterdir()), ["counters", "current_state"])
        self.assertNotIn(NOTICE, out)

    @unittest.skipIf(os.geteuid() == 0, "root writes a read-only directory")
    def test_a_session_directory_that_cannot_be_written(self):
        os.chmod(self.session_dir, 0o555)
        self.addCleanup(os.chmod, self.session_dir, 0o755)
        proc = self.start_lib('write_counter "$F" tools 9\nreset_counters "$F" 2>/dev/null || true\n'
                              'echo "failed=$PLAYBOOK_WRITE_FAILED unlocked=$PLAYBOOK_COUNTERS_UNLOCKED"\n',
                              PLAYBOOK_COUNTERS_LOCK_WAIT="1")
        rc, out, err = self.finish(proc)
        self.assertEqual(rc, 0, err)
        # the write fails and says so, as it did before; the lock adds no word of its
        # own, and "not free in time" is not what happened
        self.assertEqual(out.strip(), "failed=true unlocked=false")
        self.assertNotIn("counters.lock", err)
        self.assertEqual(self.counts(), (0, 0))


class OnCodexNothingCountsToolCalls(unittest.TestCase):
    """The bound this fix states: the Codex lane resets a session's counters at a prompt
    (Python, `provider/codex_hooks.py`) and nothing there counts a tool call, so that
    reset is the only writer of a Codex session's file and is NOT under the lock. If a
    second Python writer of the file appears, this fails and names the lock."""

    def test_the_reset_is_the_only_python_writer_of_the_file(self):
        src = (SCRIPTS.parent / "provider" / "codex_hooks.py").read_text(encoding="utf-8")
        users = [ln.strip() for ln in src.splitlines() if "_session_counter_path(" in ln]
        self.assertEqual(users, [
            "def _session_counter_path(project_root: Path, session_id: str) -> Path:",
            "counter_path = _session_counter_path(project_root, session_id)",
        ], "another Python writer of a session's counters: it must take `counters.lock` (task 182)")
        self.assertEqual(src.count('/ "counters"'), 1)


if __name__ == "__main__":
    unittest.main()
