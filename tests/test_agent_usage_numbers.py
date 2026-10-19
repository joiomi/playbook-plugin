"""The main agent's tokens in each closure's numbers (task 244, PHASE-B item 6; design v2 of its plan round 1).

The recorder (`agent-usage-hook`) writes `agent-usage` records at each turn's end; the CLI writes them at its own
transitions too — `tasks work N` (under the task it leaves, "-" for none) and the close (under the closing task,
before its `done` record and before the close removes the session dir that holds the transcript pointer) — so a span
never crosses a transition. `numbers.py` then sums this task's records of the closure.

Held:
  * `agent_table`: per model and side; `thinking` is a part of `out`, never added; a record of the same span for
    another model is kept, an exact duplicate is dropped (and counted), an overlapping span counts; `unknown`
    spans are counted, never as zero tokens;
  * a closure counts the agent records of its task between its first `work` and its `done`, by file order; another
    task's, and one written after the `done`, are not its;
  * no record at all says so (`agent` is None), never zero calls;
  * the CLI: `tasks work N` records what came before under the task left; the close records the task's own calls and
    its numbers show them; a switch splits the calls between the two tasks exactly.

Run: python3 -m unittest tests.test_agent_usage_numbers
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_PLAYBOOK = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import numbers  # noqa: E402


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def au(n, task="007", model="claude-opus-5-5", side=0, calls=1, out=10, think=3, frm=0, to=100, file="f" * 16,
       ino=1, t=1000.0, **kw):
    r = {"ts": iso(t), "hook": "agent-usage", "decision": "record", "reason": "agent usage", "task": task,
         "file": file, "ino": ino, "from": frm, "to": to, "last_id": "m", "model": model, "side": side, "calls": calls,
         "in": 2, "out": out, "cache_read": 100, "cache_write": 5, "thinking": think, "_t": t, "_n": n}
    r.update(kw)
    return r


class TheTable(unittest.TestCase):
    def test_models_and_sides_apart_and_thinking_is_a_part_of_out(self):
        got = numbers.agent_table([au(1, out=10, think=3), au(2, frm=100, to=200, out=5, think=1),
                                   au(3, frm=0, to=50, model="claude-haiku-5-5", file="g" * 16),
                                   au(4, side=1, frm=0, to=30, file="h" * 16)])
        m = got["models"]
        self.assertEqual(m["claude-opus-5-5"]["calls"], 2)
        self.assertEqual((m["claude-opus-5-5"]["out"], m["claude-opus-5-5"]["thinking"]), (15, 4))
        self.assertEqual(m["claude-haiku-5-5"]["calls"], 1)
        self.assertEqual(m["claude-opus-5-5 (subagent)"]["calls"], 1)
        self.assertEqual((got["unknown"], got["dropped"]), (0, 0))

    def test_another_model_of_one_span_is_kept_an_exact_duplicate_dropped_an_overlap_counted(self):
        # impl round 1 (codex): an overlapping span is a file read again from 0 (another one in its place) — it counts
        got = numbers.agent_table([au(1), au(2, model="claude-haiku-5-5"), au(3), au(4, frm=50, to=150)])
        self.assertEqual(got["models"]["claude-opus-5-5"]["calls"], 2)
        self.assertEqual(got["models"]["claude-haiku-5-5"]["calls"], 1)
        self.assertEqual(got["dropped"], 1)

    def test_unknown_spans_are_counted_never_as_zero_tokens(self):
        seed = {"ts": iso(1000), "hook": "agent-usage", "task": "007", "status": "unknown", "why": "no earlier record",
                "file": "f" * 16, "ino": 1, "from": 500, "to": 500, "_t": 1000.0, "_n": 1}
        got = numbers.agent_table([seed, au(2, frm=500, to=600)])
        self.assertEqual(got["unknown"], 1)
        self.assertEqual(got["models"]["claude-opus-5-5"]["calls"], 1)

    def test_no_record_is_none(self):
        self.assertIsNone(numbers.agent_table([]))


class _CliProject(unittest.TestCase):
    """A real project and the real CLI; the session's transcript pointer set as session-start-hook sets it."""
    SID = "pid-num244"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.project = root / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.transcript = root / "host" / "-proj" / "h1.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text("")
        self.n = 0

    def pointer(self):
        d = self.project / ".agent" / "sessions" / self.SID
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript_path").write_text(str(self.transcript) + "\n")

    def say(self, k=1):
        """k API calls of the agent, each written as two entries (as the host does)."""
        with open(self.transcript, "a") as fh:
            for _ in range(k):
                self.n += 1
                e = {"type": "assistant", "timestamp": iso(time.time()), "isSidechain": False,
                     "message": {"id": f"m{self.n}", "model": "claude-opus-5-5",
                                 "usage": {"input_tokens": 2, "cache_creation_input_tokens": 10,
                                           "cache_read_input_tokens": 100, "output_tokens": 7}}}
                fh.write(json.dumps(e) + "\n" + json.dumps(e) + "\n")

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "PLAYBOOK_PROJECT_DIR", "CLAUDE_PROJECT_DIR", "PLAYBOOK_SANDBOXED",
                            "BASH_ENV", "PLAYBOOK_ROLE")}
        env.update(PYTHONPATH=str(_PLAYBOOK), PLAYBOOK_SESSION_ID=self.SID, HOME=self._tmp.name,
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        return env

    def cli(self, *args):
        r = subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project, env=self.env(),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def records(self):
        p = self.project / ".agent" / "journal" / "enforcement.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines() if '"agent-usage"' in x] if p.exists() else []

    def calls_by_task(self):
        out: dict = {}
        for r in self.records():
            if r.get("calls"):                     # a zero-call record is a read position (impl round 2), no calls
                out[r["task"]] = out.get(r["task"], 0) + r["calls"]
        return out

    def closure(self, prefix):
        td = next((self.project / ".agent" / "tasks").glob(f"{prefix}-*"))
        return json.loads((td / "numbers.json").read_text())["closures"][-1]


class TheCli(_CliProject):
    def test_work_records_what_came_before_and_the_close_records_the_tasks_own_calls(self):
        self.cli("new", "quick", "first")
        self.pointer()
        self.say(2)                                    # before any task
        self.cli("work", "1")
        self.say(3)                                    # the task's own work
        self.cli("work", "done", "--force", "--reason", "test close")
        self.assertEqual(self.calls_by_task(), {"-": 2, "001": 3})
        a = self.closure("001")["agent"]
        self.assertEqual(a["models"]["claude-opus-5-5"]["calls"], 3)
        self.assertEqual(a["models"]["claude-opus-5-5"]["out"], 21)

    def test_a_switch_splits_the_calls_between_the_two_tasks(self):
        self.cli("new", "quick", "first")
        self.cli("new", "quick", "second")
        self.pointer()
        self.cli("work", "1")
        self.say(4)
        self.cli("work", "2", "--force")               # leaves 1 (open) for 2
        self.say(1)
        self.cli("work", "done", "--force", "--reason", "close 2")
        self.assertEqual(self.calls_by_task(), {"001": 4, "002": 1})
        self.assertEqual(self.closure("002")["agent"]["models"]["claude-opus-5-5"]["calls"], 1)

    def test_working_on_the_active_task_again_keeps_its_calls(self):
        self.cli("new", "quick", "first")
        self.pointer()
        self.cli("work", "1")
        self.say(2)
        self.cli("work", "1")                          # again, the same task: what came before is still its own
        self.say(1)
        self.cli("work", "done", "--force", "--reason", "again")
        self.assertEqual(self.calls_by_task(), {"001": 3})

    def test_freehand_records_what_came_before_it(self):
        self.pointer()
        self.say(2)
        self.cli("freehand")
        self.say(1)
        self.cli("work", "done", "--force", "--reason", "freehand close")
        self.assertEqual(self.calls_by_task(), {"-": 2, "001": 1})

    def test_a_close_that_cannot_take_the_lock_says_so_and_marks_the_gap(self):
        self.cli("new", "quick", "first")
        self.pointer()
        self.cli("work", "1")
        self.say(2)
        lock = self.project / ".agent" / "journal" / "agent-usage.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(["flock", str(lock), "sleep", "60"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.time() + 10
            while not lock.exists() and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            r = self.cli("work", "done", "--force", "--reason", "lock busy")
        finally:
            os.killpg(holder.pid, 9)
            holder.wait()
        self.assertIn("agent tokens: not recorded", r.stdout + r.stderr)
        a = self.closure("001")["agent"]
        self.assertEqual(a["unknown"], 1)

    def test_a_busy_lock_close_whose_marker_cannot_be_written_says_so(self):
        self.cli("new", "quick", "first")
        self.pointer()
        self.cli("work", "1")
        self.say(1)
        lock = self.project / ".agent" / "journal" / "agent-usage.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(["flock", str(lock), "sleep", "60"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        journal = self.project / ".agent" / "journal" / "enforcement.jsonl"
        probe = journal.with_name("probe.ro")
        probe.write_text("")
        probe.chmod(0o444)
        try:
            with open(probe, "a"):
                os.killpg(holder.pid, 9)
                holder.wait()
                self.skipTest("file permissions not enforced for this user")
        except PermissionError:
            pass
        try:
            time.sleep(0.3)
            journal.chmod(0o444)
            r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done", "--force", "--reason", "ro"],
                               cwd=self.project, env=self.env(), capture_output=True, text=True, timeout=120)
        finally:
            journal.chmod(0o644)
            os.killpg(holder.pid, 9)
            holder.wait()
        self.assertIn("could not be written", r.stdout + r.stderr)

    def test_a_close_with_no_record_says_so(self):
        self.cli("new", "quick", "first")
        self.cli("work", "1")                          # no transcript pointer: nothing can be read
        self.cli("work", "done", "--force", "--reason", "no pointer")
        self.assertIsNone(self.closure("001")["agent"])


if __name__ == "__main__":
    unittest.main()
