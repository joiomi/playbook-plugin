"""What the session walk read is journaled when it resolves nothing (task 192, PLAN S14 T3, item #48).

"playbook: no session identity" came 134 times in 54,095 runs of the two per-call hooks in the owner's two projects
(the B1 transcripts, 2026-10-13: state-echo 105, task-gate 29), and in 132 of them the other hook of the SAME tool
call resolved the session — a transient — but nothing said what the process walk had read. Now the echo and the
gate journal one `session-walk` record on an unresolved id: each hop (pid, name, state; a claude ancestor's argument
COUNT and daemon verdict, never its words), where the walk stopped (`gone`, `unreadable`, `daemon`), and what a walk
right then resolves. `PLAYBOOK_WALK_TRACE=1` journals it on every call (the live gauntlet sets it, so a capture
window shows the walk ran). The trace is a second walk: the resolver's ran in a command substitution. It never
decides anything, and its `result=` is held to `resolve_session_id` over every vector of the identity tests.
"""
from __future__ import annotations

import json
import subprocess
import unittest

from tests._bashcheck import bash_or_skip
from tests.test_session_daemon_identity import (GATE_LIB, SCRIPTS, SHELL, SYSTEMD, TERMINAL, PTY_HOST, DAEMON,
                                                VECTORS, _FakePsMixin)


def trace_result(line: str) -> str:
    return line.rsplit("result=", 1)[1].strip()


class _Trace(_FakePsMixin):
    def trace(self, **extra):
        r = subprocess.run([bash_or_skip(), "-c", f"source '{GATE_LIB.as_posix()}' && session_walk_trace"],
                           env=self.env(**extra), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()


class TraceResultParity(_Trace):
    """The trace walks as `_agent_walk` does: its result is the resolver's, on every vector (ps path here)."""

    def test_every_vector(self):
        for name, (rows, _) in VECTORS.items():
            with self.subTest(vector=name):
                self.set_tree(rows)
                want = self.bash_resolve() or "unresolved"
                line = self.trace()
                self.assertEqual(trace_result(line), want, line)
                self.assertEqual(len(line.splitlines()), 1, line)


class TraceResultParityProc(TraceResultParity):
    PROC = True


class TraceNamesWhereItStopped(_Trace):
    PROC = True

    def test_a_parent_that_is_gone(self):
        self.set_tree([(300, 777, SHELL)])      # 777 has no /proc entry: the process is gone
        line = self.trace()
        self.assertIn("777 gone", line)
        self.assertEqual(trace_result(line), "unresolved")

    def test_an_unreadable_ancestor(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)], omit_status=(400,))
        line = self.trace()
        self.assertIn("400 unreadable", line)
        self.assertNotIn("gone", line)
        self.assertEqual(trace_result(line), "unresolved")

    def test_the_daemon(self):
        self.set_tree([(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)])
        line = self.trace()
        self.assertIn("300 daemon", line)
        self.assertEqual(trace_result(line), "unresolved")

    def test_a_resolved_walk_names_its_hops(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)])
        line = self.trace()
        self.assertIn("400 claude", line)
        self.assertIn("300 bash", line)
        self.assertEqual(trace_result(line), "pid-400")

    def test_no_argument_of_a_claude_ancestor_is_written(self):
        secret = ("claude", "claude -p SECRET-PROMPT-WORDS", ["claude", "-p", "SECRET-PROMPT-WORDS"])
        self.set_tree([(400, 300, secret), (300, 1, SHELL)])
        line = self.trace()
        self.assertNotIn("SECRET", line)
        self.assertIn("argc=3", line)


class HooksJournalTheWalk(_FakePsMixin):
    """Through the real hooks: an unresolved id journals one `session-walk` record; a resolved one does not,
    unless PLAYBOOK_WALK_TRACE=1."""

    PROC = True
    UNRESOLVED = [(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)]
    RESOLVED = [(400, 300, TERMINAL), (300, 1, SHELL)]

    def setUp(self):
        super().setUp()
        self.project = self.tmp / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)

    def run_hook(self, name, payload, **extra):
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / name)], cwd=self.project,
                           env=self.env(HOME=str(self.tmp), **extra),
                           input=json.dumps(payload), capture_output=True, text=True, timeout=60)
        return r

    def echo(self, **extra):
        return self.run_hook("state-echo-hook", {"tool_name": "Read", "tool_input": {"file_path": "x"}}, **extra)

    def gate(self, **extra):
        return self.run_hook("task-gate-hook", {"tool_name": "Edit",
                                                "tool_input": {"file_path": str(self.project / "src" / "a.py")}},
                             **extra)

    def walks(self, root=None):
        f = (root or self.project / ".agent") / "journal" / "enforcement.jsonl"
        if not f.exists():
            return []
        rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [x for x in rows if x.get("decision") == "session-walk"]

    def test_the_echo_journals_an_unresolved_walk(self):
        self.set_tree(self.UNRESOLVED)
        r = self.echo()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no session identity", r.stderr)
        w = self.walks()
        self.assertEqual(len(w), 1, w)
        self.assertEqual((w[0]["hook"], w[0]["session_id"], w[0].get("tool")), ("state-echo", "", "Read"))
        self.assertIn("300 daemon", w[0]["reason"])
        self.assertEqual(trace_result(w[0]["reason"]), "unresolved")

    def test_the_gate_journals_an_unresolved_walk_and_still_refuses(self):
        self.set_tree(self.UNRESOLVED)
        r = self.gate()
        self.assertEqual(r.returncode, 2, r.stderr)
        w = self.walks()
        self.assertEqual(len(w), 1, w)
        self.assertEqual((w[0]["hook"], w[0]["session_id"], w[0].get("tool")), ("task-gate", "", "Edit"))

    def test_a_resolved_walk_is_not_journaled_by_default(self):
        self.set_tree(self.RESOLVED)
        self.echo()
        self.gate()
        self.assertEqual(self.walks(), [])

    def test_trace_mode_journals_every_walk(self):
        self.set_tree(self.RESOLVED)
        self.echo(PLAYBOOK_WALK_TRACE="1")
        self.gate(PLAYBOOK_WALK_TRACE="1")
        w = self.walks()
        self.assertEqual([x["hook"] for x in w], ["state-echo", "task-gate"], w)
        self.assertEqual({x["session_id"] for x in w}, {"pid-400"})
        self.assertEqual({trace_result(x["reason"]) for x in w}, {"pid-400"})

    def test_nothing_is_journaled_at_a_fresh_clone_root(self):
        import shutil
        shutil.rmtree(self.project / ".agent" / "tasks")
        for user in ("alice", "bob"):
            (self.project / ".agent" / user / "tasks").mkdir(parents=True)
        self.set_tree(self.UNRESOLVED)
        self.echo()
        self.gate()
        self.assertFalse((self.project / ".agent" / "journal").exists(),
                         sorted(p.name for p in (self.project / ".agent").iterdir()))


if __name__ == "__main__":
    unittest.main()
