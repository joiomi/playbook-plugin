"""What the session walk read is journaled when it resolves nothing (task 192, PLAN S14 T3, item #48).

"playbook: no session identity" came 134 times in 54,095 runs of the two per-call hooks in the owner's two projects
(the B1 transcripts, 2026-10-13: state-echo 105, task-gate 29), and in 132 of them the other hook of the SAME tool
call resolved the session — a transient — but nothing said what the process walk had read. Now the echo and the
gate journal one `session-walk` record on an unresolved id: `read:` is what the resolver's OWN walk read (impl panel
r1, codex: a second walk would show a healthy chain beside a transient failure) — each hop's pid, name and state, a
claude ancestor's argument COUNT never its words, where it stopped (`gone`, `unreadable`, `unreadable argv`,
`daemon`) — and `again=` what a walk right after resolves. `resolve_session_id trace` gives the id and that trace in
one call, and the id is the plain resolver's on every vector and env case. `PLAYBOOK_WALK_TRACE=1` journals every
call (the live gauntlet sets it). Unresolved records are capped at 100 a day (impl panel r1, sonnet). Log-only.
"""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import unittest

from tests._bashcheck import bash_or_skip
from tests.test_session_daemon_identity import (GATE_LIB, SCRIPTS, SHELL, SYSTEMD, TERMINAL, PTY_HOST, DAEMON,
                                                VECTORS, _FakePsMixin)

# a `ps` in front of the fixture's: the args probe of FAIL_PID fails for its first FAIL_TIMES calls, then the
# fixture answers — the resolver's walk (one probe and its retry) fails, a walk after it does not
FAIL_TWICE_PS = """#!/bin/bash
for a in "$@"; do [ "$a" = "$FAIL_PID" ] && pid_hit=1; done
case " $* " in *" args="*) ;; *) pid_hit="" ;; esac
if [ -n "$pid_hit" ]; then
    n=$(cat "$FAIL_COUNT" 2>/dev/null || echo 0)
    echo $((n + 1)) > "$FAIL_COUNT"
    [ "$n" -lt "$FAIL_TIMES" ] && exit 1
fi
exec "$NEXT_PS" "$@"
"""


def split(out: str) -> "tuple[str, str]":
    sid, tab, read = out.partition("\t")
    assert tab, f"no TAB in {out!r}"
    return sid, read


class _Trace(_FakePsMixin):
    def resolve(self, *args, **extra):
        r = subprocess.run([bash_or_skip(), "-c", f"source '{GATE_LIB.as_posix()}' && resolve_session_id {' '.join(args)}"],
                           env=self.env(**extra), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")


class TracedIdParity(_Trace):
    """`resolve_session_id trace` answers the same id as `resolve_session_id` — every vector, env cases (ps path)."""

    def test_every_vector(self):
        for name, (rows, _) in VECTORS.items():
            with self.subTest(vector=name):
                self.set_tree(rows)
                sid, read = split(self.resolve("trace"))
                self.assertEqual(sid, self.resolve())
                self.assertEqual(len(read.splitlines()), 1, read)

    def test_env_ids(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL), (450, 1, TERMINAL)])
        for env_id in ("judge", "pid-400", "pid-450", "pid-999999", "../x", "pid-"):
            with self.subTest(env=env_id):
                sid, read = split(self.resolve("trace", PLAYBOOK_SESSION_ID=env_id))
                self.assertEqual(sid, self.resolve(PLAYBOOK_SESSION_ID=env_id))
                self.assertNotIn("/", read.split("env=", 1)[1].split(" ", 1)[0])


class TracedIdParityProc(TracedIdParity):
    PROC = True


class TheTraceSaysWhatTheWalkRead(_Trace):
    PROC = True

    def read(self, **extra):
        sid, read = split(self.resolve("trace", **extra))
        return sid, read

    def test_a_parent_that_is_gone(self):
        self.set_tree([(300, 777, SHELL)])      # 777 has no /proc entry: the process is gone
        sid, read = self.read()
        self.assertEqual(sid, "")
        self.assertIn("stop: 777 gone", read)

    def test_an_unreadable_ancestor(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)], omit_status=(400,))
        sid, read = self.read()
        self.assertEqual(sid, "")
        self.assertIn("stop: 400 unreadable", read)
        self.assertNotIn("gone", read)

    def test_the_daemon(self):
        self.set_tree([(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)])
        sid, read = self.read()
        self.assertEqual(sid, "")
        self.assertIn("stop: 300 daemon", read)

    def test_a_resolved_walk_names_its_hops(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)])
        sid, read = self.read()
        self.assertEqual(sid, "pid-400")
        self.assertIn("400 claude S argc=1", read)
        self.assertIn("300 bash S", read)
        self.assertIn("stop: top", read)

    def test_no_argument_of_a_claude_ancestor_is_written(self):
        secret = ("claude", "claude -p SECRET-PROMPT-WORDS", ["claude", "-p", "SECRET-PROMPT-WORDS"])
        self.set_tree([(400, 300, secret), (300, 1, SHELL)])
        _sid, read = self.read()
        self.assertNotIn("SECRET", read)
        self.assertIn("argc=3", read)

    def test_a_control_byte_in_a_name_or_the_env_id_is_not_written(self):
        # Impl panel r1 (fable): `_json_str` escapes only \\ " \\t \\r \\n — any other control byte made the journal
        # line unparseable, and the live summary dropped the very record.
        self.set_tree([(400, 300, ("cl\x01aude", "claude")), (300, 1, ("ba\x1bsh", "bash"))])
        _sid, read = self.read(PLAYBOOK_SESSION_ID="pid-\x02x")
        self.assertFalse(any(ord(c) < 32 for c in read), repr(read))


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
        return subprocess.run([bash_or_skip(), str(SCRIPTS / name)], cwd=self.project,
                              env=self.env(HOME=str(self.tmp), **extra),
                              input=json.dumps(payload), capture_output=True, text=True, timeout=60)

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

    def counter(self):
        return self.project / ".agent" / "journal" / "session-walk.count"

    def test_the_echo_journals_an_unresolved_walk(self):
        self.set_tree(self.UNRESOLVED)
        r = self.echo()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no session identity", r.stderr)
        w = self.walks()
        self.assertEqual(len(w), 1, w)
        self.assertEqual((w[0]["hook"], w[0]["session_id"], w[0].get("tool")), ("state-echo", "", "Read"))
        self.assertIn("read: ppid=", w[0]["reason"])
        self.assertIn("stop: 300 daemon", w[0]["reason"])
        self.assertIn("| again=unresolved", w[0]["reason"])

    def test_the_gate_journals_an_unresolved_walk_and_still_refuses(self):
        self.set_tree(self.UNRESOLVED)
        r = self.gate()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("No active task", r.stderr)
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
        self.assertTrue(all("400 claude S argc=1" in x["reason"] for x in w), w)
        self.assertFalse(any("again=" in x["reason"] for x in w), w)

    def test_nothing_is_journaled_at_a_fresh_clone_root(self):
        shutil.rmtree(self.project / ".agent" / "tasks")
        for user in ("alice", "bob"):
            (self.project / ".agent" / user / "tasks").mkdir(parents=True)
        self.set_tree(self.UNRESOLVED)
        self.echo()
        self.gate()
        self.assertFalse((self.project / ".agent" / "journal").exists(),
                         sorted(p.name for p in (self.project / ".agent").iterdir()))

    # the per-day cap (impl panel r1, sonnet): a deterministic unresolved shape must not grow the journal unbounded
    def today(self):
        return datetime.date.today().isoformat()

    def test_the_record_that_reaches_the_cap_says_so_and_the_next_is_not_written(self):
        self.set_tree(self.UNRESOLVED)
        self.counter().parent.mkdir(parents=True, exist_ok=True)
        self.counter().write_text(f"{self.today()} 99\n", encoding="utf-8")
        self.echo()
        self.echo()
        w = self.walks()
        self.assertEqual(len(w), 1, w)
        self.assertIn("the day's cap of 100 records is reached", w[0]["reason"])
        self.assertEqual(self.counter().read_text(encoding="utf-8"), f"{self.today()} 100\n")

    def test_another_day_counts_from_zero(self):
        self.set_tree(self.UNRESOLVED)
        self.counter().parent.mkdir(parents=True, exist_ok=True)
        self.counter().write_text("2000-01-01 100\n", encoding="utf-8")
        self.echo()
        self.assertEqual(len(self.walks()), 1)
        self.assertEqual(self.counter().read_text(encoding="utf-8"), f"{self.today()} 1\n")

    def test_trace_mode_is_not_capped(self):
        # an UNRESOLVED walk (the only kind the cap counts) past the cap, in trace mode, is still journaled
        self.set_tree(self.UNRESOLVED)
        self.counter().parent.mkdir(parents=True, exist_ok=True)
        self.counter().write_text(f"{self.today()} 100\n", encoding="utf-8")
        self.echo(PLAYBOOK_WALK_TRACE="1")
        w = self.walks()
        self.assertEqual(len(w), 1, w)
        self.assertEqual(w[0]["session_id"], "")
        self.assertEqual(self.counter().read_text(encoding="utf-8"), f"{self.today()} 100\n")

    def test_an_unreadable_counter_never_stops_the_gate(self):
        # the gate runs under `set -e`: a failing read of the counter must not end it (exit 1 lets the edit through)
        self.set_tree(self.UNRESOLVED)
        self.counter().mkdir(parents=True)
        r = self.gate()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("No active task", r.stderr)


class TheJournaledWalkIsTheResolversOwn(_FakePsMixin):
    """Impl panel r1 (codex): a transient read failure in the resolver's walk is what the record shows — the ps
    path, where the claude ancestor's args probe fails for the walk (probe and retry) and answers afterwards."""

    def test_the_failed_read_is_journaled_and_the_walk_after_it_resolves(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)])
        front = self.tmp / "frontbin"
        front.mkdir()
        (front / "ps").write_text(FAIL_TWICE_PS, encoding="utf-8")
        (front / "ps").chmod(0o755)
        project = self.tmp / "proj"
        (project / ".agent" / "tasks").mkdir(parents=True)
        env = self.env(HOME=str(self.tmp), FAIL_PID="400", FAIL_TIMES="2", FAIL_COUNT=str(self.tmp / "count"),
                       NEXT_PS=str(self.fakebin / "ps"))
        env["PATH"] = f"{front}:{env['PATH']}"
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / "state-echo-hook")], cwd=project, env=env,
                           input=json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no session identity", r.stderr)
        rows = [json.loads(x) for x in (project / ".agent" / "journal" / "enforcement.jsonl")
                .read_text(encoding="utf-8").splitlines()]
        w = [x for x in rows if x.get("decision") == "session-walk"]
        self.assertEqual(len(w), 1, rows)
        self.assertIn("stop: 400 unreadable argv", w[0]["reason"])
        self.assertIn("| again=pid-400", w[0]["reason"])


if __name__ == "__main__":
    unittest.main()
