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
(`counters.lock`, flock), with a time limit after which the step goes on without it, as
every step did before.

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
from tests.test_notification_turn import SCRIPTS, SID, _Session

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
        env["PLAYBOOK_COUNTERS_LOCK_WAIT"] = "60"
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


@_needs_flock
class TheLockHasATimeLimit(_Counters):
    """A lock that is never let go must not hold a tool call: after the limit the call is
    counted WITHOUT the lock, as every call was before, and the hook says so."""

    def _flock_shim(self):
        """A `flock` that refuses the counters lock (descriptor 204) and notes how it was
        asked; every other lock is the real program's."""
        d = Path(self._tmp.name) / "shim"
        d.mkdir()
        self.asked = Path(self._tmp.name) / "flock-asked"
        shim = d / "flock"
        shim.write_text(f'#!/bin/sh\ncase " $* " in *" 204 "*) echo "$*" >> "{self.asked}"; exit 1 ;; esac\n'
                        f'exec "{shutil.which("flock")}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        return str(d) + os.pathsep + os.environ["PATH"]

    def _asked(self):
        return self.asked.read_text(encoding="utf-8").splitlines() if self.asked.exists() else []

    def test_a_lock_that_is_never_let_go(self):
        with self.held():
            started = time.monotonic()
            rc, out, err = self.finish(self.start_tool(PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
            took = time.monotonic() - started
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.counts(), (1, 1))
        self.assertGreaterEqual(took, 0.9, "the hook did not wait for the lock at all")
        self.assertIn(NOTICE, out)
        self.assertIn("1s", out)
        json.loads(out)                                  # … and it is still the hook's answer

    def test_the_limit_is_waited_once_for_a_whole_call(self):
        # the hook takes the lock for the count, again for the gate, and each write in
        # between asks too: a holder that never lets go costs ONE limit, not seven
        rc, out, err = self.finish(self.start_tool(PATH=self._flock_shim(), PLAYBOOK_COUNTERS_LOCK_WAIT="1"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self._asked(), ["-w 1 204"])
        self.assertEqual(self.counts(), (1, 1))
        self.assertIn(NOTICE, out)

    def test_two_seconds_unless_told_otherwise(self):
        env = self._env(PATH=self._flock_shim())
        del env["PLAYBOOK_COUNTERS_LOCK_WAIT"]
        for value, want in ((None, "2"), ("7", "7"), ("99", "99"), ("0", "2"), ("100", "2"), ("-1", "2"),
                            ("1.5", "2"), ("abc", "2"), ("1; touch pwned", "2"), ("", "2")):
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
