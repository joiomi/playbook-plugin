"""Task 106: a `pid-<digits>` PLAYBOOK_SESSION_ID is honored only if it names a
live agent process.

Measured 2026-09-28 (record: .agent/tasks/106-*/task.md "## Measurement"): a
resumed Claude Code conversation re-sources its OLD session-start env file, so
Bash commands carried `PLAYBOOK_SESSION_ID=pid-187021` (dead) while the hooks
walked to the live claude (5459); `tasks blocked` answered "No active task to
block" with a task active. The same stale id also spread by environment
inheritance into a child `claude --bg` session.

Rule (owner, 2026-09-28), both resolvers in parity: an env id of the form
`pid-<digits>` is honored only if process N is alive AND its comm is an agent
(claude*, codex, agy, grok, pi); otherwise it is ignored and the existing walk
decides; the CLI prints one stderr line naming the stale id and the id used.
N need NOT be an ancestor (under the daemon the hosted session is not one).
Other ids (`judge`, `pid-win-fallback`, `pid-blocked-test`) are untouched.

The harness (a `/proc` fixture via PLAYBOOK_PROC_ROOT + a fake `ps`) is task
105's; every vector runs on both paths.
"""

from __future__ import annotations

import os
import subprocess
import sys

import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip
from tests.test_session_daemon_identity import (
    DAEMON, GATE_LIB, PLUGIN, PTY_HOST, SHELL, SYSTEMD, TERMINAL,
    _FakePsMixin, _ProjectMixin,
)

STALE_MARK = "ignoring stale PLAYBOOK_SESSION_ID"

DEAD = 4711                        # in no table and no fixture → no such process
SLEEPER = (4712, 1, ("sleep", "sleep 300"))       # alive, not an agent
AGENT = (4713, 1, TERMINAL)                        # alive agent, NOT an ancestor
BASE = [(400, 300, TERMINAL), (300, 1, SHELL)]     # the walk → pid-400


class StaleEnvIdPs(_FakePsMixin):
    """The rule on the `ps` path (macOS fallback)."""

    def resolve_both(self, rows, sid):
        self.set_tree(rows)
        extra = {"FAKE_PS_DEAD": str(DEAD)}
        if sid is not None:
            extra["PLAYBOOK_SESSION_ID"] = sid
        py, sh = self.py_resolve(**extra), self.bash_resolve(**extra)
        self.assertEqual(py, sh, f"parity broken for {sid!r}: python {py!r} vs bash {sh!r}")
        return py

    def test_dead_env_pid_falls_back_to_the_walk(self):
        self.assertEqual(self.resolve_both(BASE, f"pid-{DEAD}"), "pid-400")

    def test_live_non_agent_env_pid_falls_back_to_the_walk(self):
        self.assertEqual(self.resolve_both(BASE + [SLEEPER], "pid-4712"), "pid-400")

    def test_live_agent_env_pid_wins_even_if_not_an_ancestor(self):
        # the walk finds NO agent of its own (e.g. a plain terminal) → the env
        # id of a live agent is honored although it is not an ancestor
        self.assertEqual(self.resolve_both([(300, 1, SHELL), AGENT], "pid-4713"), "pid-4713")

    def test_inherited_live_agent_id_loses_to_the_walked_session(self):
        # Owner decision 2026-09-28: a child claude that inherited ANOTHER live
        # claude's pid-N resolves to ITS OWN session (the walk found a different
        # agent root), so hooks and CLI agree and the parent is not shared.
        self.assertEqual(self.resolve_both(BASE + [AGENT], "pid-4713"), "pid-400")

    def test_env_naming_the_walked_session_itself_wins(self):
        self.assertEqual(self.resolve_both(BASE, "pid-400"), "pid-400")

    def test_codex_launched_from_a_claude_keeps_its_own_id(self):
        # `playbook-codex` run from a claude's Bash exports pid-<codex> (pid-$$,
        # then exec). The CLI codex runs walks codex → … → claude: the id names
        # an agent IN the chain, so it wins — the codex hooks
        # (provider/codex_hooks.py) use it too; picking the higher claude split
        # CLI from hooks.
        rows = [(4800, 4700, ("codex", "codex")), (4700, 400, SHELL), (400, 300, TERMINAL), (300, 1, SHELL)]
        self.assertEqual(self.resolve_both(rows, "pid-4800"), "pid-4800")

    def test_nested_claude_inheriting_the_outer_id_keeps_it(self):
        # claude inside claude: no env → the highest (outer) as before; the
        # inherited outer id is an agent in the chain → honored (same answer)
        rows = [(450, 400, TERMINAL), (400, 300, TERMINAL), (300, 1, SHELL)]
        self.assertEqual(self.resolve_both(rows, "pid-400"), "pid-400")
        self.assertEqual(self.resolve_both(rows, None), "pid-400")

    def test_non_pid_ids_are_untouched(self):
        for sid in ("judge", "pid-blocked-test", "pid-4711x", "pid-win-fallback"):
            with self.subTest(sid=sid):
                self.assertEqual(self.resolve_both(BASE, sid), sid)

    def test_edge_numeric_ids_agree(self):
        rows = BASE + [AGENT]
        for sid, want in (("pid-0", "pid-400"), ("pid-0004713", "pid-400"),   # another root → walk
                          ("pid-99999999999", "pid-400"), ("pid-1", "pid-400")):
            with self.subTest(sid=sid):
                self.assertEqual(self.resolve_both(rows, sid), want)
        # leading zeros parse the same in both: honored when the walk has no agent
        self.assertEqual(self.resolve_both([(300, 1, SHELL), AGENT], "pid-0004713"), "pid-0004713")

    # ── round 1 (impl panel) ────────────────────────────────────────────────
    def test_live_daemon_pid_in_env_is_not_a_session(self):
        # R1-1: the shared pty host / daemon have comm claude.exe but are never
        # a session — an env naming one must not collapse hosted sessions.
        rows = BASE + [(4714, 1, PTY_HOST), (4715, 1, DAEMON)]
        for sid in ("pid-4714", "pid-4715"):
            with self.subTest(sid=sid):
                self.assertEqual(self.resolve_both(rows, sid), "pid-400")

    def test_zombie_agent_in_env_is_dead(self):
        # R1-2: exited but not yet reaped — still listed, but not alive.
        self.set_tree(BASE + [AGENT], zombie=(4713,))
        extra = {"PLAYBOOK_SESSION_ID": "pid-4713", "FAKE_PS_ZOMBIE": "4713"}
        py, sh = self.py_resolve(**extra), self.bash_resolve(**extra)
        self.assertEqual(py, sh)
        self.assertEqual(py, "pid-400")

    def test_env_under_the_daemon_still_wins(self):
        daemon_only = [(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD), AGENT]
        self.assertEqual(self.resolve_both(daemon_only, "pid-4713"), "pid-4713")

    def test_dead_env_under_the_daemon_is_unresolved(self):
        daemon_only = [(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)]
        self.assertEqual(self.resolve_both(daemon_only, f"pid-{DEAD}"), "")

    def test_cli_names_the_stale_id_and_the_id_used_once(self):
        self.set_tree(BASE)
        r = subprocess.run([sys.executable, "-c",
                            "import tasks.core as c\n"
                            "for _ in range(3): c.resolve_session_id()\n"
                            "print(c.resolve_session_id())"],
                           env=self.env(PLAYBOOK_SESSION_ID=f"pid-{DEAD}", FAKE_PS_DEAD=str(DEAD)),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "pid-400")
        lines = [ln for ln in r.stderr.splitlines() if STALE_MARK in ln]
        self.assertEqual(len(lines), 1, f"want one stale-id line, stderr:\n{r.stderr}")
        self.assertIn(f"pid-{DEAD}", lines[0])
        self.assertIn("pid-400", lines[0])


class StaleEnvIdProc(StaleEnvIdPs):
    """The same vectors on the Linux /proc path."""
    PROC = True

    def test_dead_env_pid_falls_back_to_the_walk(self):   # cited by the ledger
        super().test_dead_env_pid_falls_back_to_the_walk()

    def test_live_non_agent_env_pid_falls_back_to_the_walk(self):   # cited by the ledger
        super().test_live_non_agent_env_pid_falls_back_to_the_walk()


class StaleEnvIdRealProcesses(unittest.TestCase):
    """No seam: a real reaped child (dead) and a real `sleep` (live, not an
    agent) against the host's own process table. Whatever the real walk says
    with no env is what a stale env must resolve to."""

    def setUp(self):
        if os.name == "nt":
            self.skipTest("the ancestor walk is skipped on Windows")

    def _env(self, sid=None):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "PLAYBOOK_PROC_ROOT", "BASH_ENV", "CLAUDE_ENV_FILE")}
        env["PYTHONPATH"] = str(PLUGIN)
        if sid:
            env["PLAYBOOK_SESSION_ID"] = sid
        return env

    def _both(self, sid=None):
        py = subprocess.run([sys.executable, "-c", "import tasks.core as c; print(c.resolve_session_id())"],
                            env=self._env(sid), capture_output=True, text=True, timeout=60).stdout.strip()
        sh = subprocess.run([bash_or_skip(), "-c", f"source '{GATE_LIB.as_posix()}' && resolve_session_id"],
                            env=self._env(sid), capture_output=True, text=True, timeout=60).stdout.strip()
        return py, sh

    def test_real_dead_and_real_non_agent(self):
        p = subprocess.Popen(["true"])
        p.wait()
        sleeper = subprocess.Popen(["sleep", "300"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        base_py, base_sh = self._both()
        for sid in (f"pid-{p.pid}", f"pid-{sleeper.pid}"):
            with self.subTest(sid=sid):
                py, sh = self._both(sid)
                self.assertNotEqual(py, sid, f"python honored a stale id {sid!r}")
                self.assertNotEqual(sh, sid, f"bash honored a stale id {sid!r}")
                # R1-5: the walk alone decides — exactly each resolver's no-env
                # answer (both subprocesses share this test process as parent,
                # so even the pid-$PPID fallback is the same for both).
                self.assertEqual(py, base_py)
                self.assertEqual(sh, base_sh)
                self.assertEqual(py, sh)


class StaleEnvIdRealZombie(unittest.TestCase):
    """R1-2 on the real process table: an exited, unreaped `claude`."""

    @unittest.skipUnless(os.path.isdir("/proc/self"), "Linux /proc only")
    def test_real_zombie_claude_is_dead(self):
        import shutil, tempfile, time
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        exe = os.path.join(d, "claude")
        shutil.copy(shutil.which("true"), exe)
        os.chmod(exe, 0o755)
        z = subprocess.Popen([exe])          # exits at once; NOT waited → zombie
        self.addCleanup(z.wait)
        for _ in range(50):
            with open(f"/proc/{z.pid}/status", encoding="utf-8") as fh:
                if "\nState:\tZ" in fh.read():
                    break
            time.sleep(0.05)
        env = {k: v for k, v in os.environ.items() if k not in ("PLAYBOOK_PROC_ROOT", "BASH_ENV")}
        env["PYTHONPATH"] = str(PLUGIN)
        env["PLAYBOOK_SESSION_ID"] = f"pid-{z.pid}"
        py = subprocess.run([sys.executable, "-c", "import tasks.core as c; print(c.resolve_session_id())"],
                            env=env, capture_output=True, text=True, timeout=60).stdout.strip()
        sh = subprocess.run([bash_or_skip(), "-c", f"source '{GATE_LIB.as_posix()}' && resolve_session_id"],
                            env=env, capture_output=True, text=True, timeout=60).stdout.strip()
        self.assertNotEqual(py, f"pid-{z.pid}", "python honored a zombie claude")
        self.assertNotEqual(sh, f"pid-{z.pid}", "bash honored a zombie claude")


class PsTimeoutKeepsTheEnvId(unittest.TestCase):
    """R1-4: on the ps path a probe that times out cannot judge → keep the id
    (bash `ps` has no deadline and would honor a slow live agent)."""

    def test_timeout_is_not_stale(self):
        sys.path.insert(0, str(PLUGIN))
        try:
            import tasks.core as core
        finally:
            sys.path.remove(str(PLUGIN))
        from unittest import mock
        def slow(*a, **k):
            raise subprocess.TimeoutExpired(cmd=a[0], timeout=k.get("timeout"))
        with mock.patch.dict(os.environ, {"PLAYBOOK_PROC_ROOT": "/nonexistent-proc"}), \
                mock.patch.object(core.subprocess, "run", side_effect=slow):
            self.assertFalse(core._env_pid_is_stale("pid-4713"))


class PsTimeoutThenCompletedMissIsStale(unittest.TestCase):
    """Single judge run 3: one timed-out attempt followed by a COMPLETED empty
    read (ps exited non-zero: no such process) is a dead pid — bash reads the
    same empty answer as stale; only all-timeouts or no ps binary keep."""

    def test_timeout_then_miss(self):
        sys.path.insert(0, str(PLUGIN))
        try:
            import tasks.core as core
        finally:
            sys.path.remove(str(PLUGIN))
        from unittest import mock
        calls = {"n": 0}
        def run(cmd, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=k.get("timeout"))
            return subprocess.CompletedProcess(cmd, 1, "", "")
        with mock.patch.dict(os.environ, {"PLAYBOOK_PROC_ROOT": "/nonexistent-proc"}), \
                mock.patch.object(core.subprocess, "run", side_effect=run):
            self.assertTrue(core._env_pid_is_stale("pid-4711"))


class RealPsLivenessProbe(unittest.TestCase):
    """Single judge run 4: the ps-path liveness probe must use keywords the
    SYSTEM `ps` accepts (BSD ps on macOS, procps on Linux) — the fake `ps`
    accepts anything. Runs the real `ps` on a live process of ours; on the
    macOS CI lane this is the BSD `ps` the fallback really uses."""

    def setUp(self):
        import shutil
        if os.name == "nt" or shutil.which("ps") is None:
            self.skipTest("no POSIX ps")

    def test_python_probe_reads_a_live_process(self):
        sys.path.insert(0, str(PLUGIN))
        try:
            import tasks.core as core
        finally:
            sys.path.remove(str(PLUGIN))
        out = core._ps_probe(os.getpid(), core._PS_LIVENESS_FIELDS)
        self.assertTrue(out, f"the system ps gave no answer for {core._PS_LIVENESS_FIELDS!r}")
        self.assertRegex(out.split()[0], r"^[A-Za-z]", out)

    def test_bash_probe_reads_a_live_process(self):
        r = subprocess.run([bash_or_skip(), "-c",
                            f"source '{GATE_LIB.as_posix()}' && _ps_field $$ \"$PB_LIVENESS_FIELDS\""],
                           capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, PB_LIVENESS_FIELDS="state=,comm="))
        self.assertTrue(r.stdout.strip(), f"the system ps gave no answer: {r.stderr}")
        g = GATE_LIB.read_text(encoding="utf-8")
        self.assertIn('_ps_field "$n" state=,comm=', g, "the bash probe must use the same keywords")


class CommandGuardIgnoresAStaleId(unittest.TestCase):
    """R1-3: the destructive-command guard must not let a stale (dead) session
    id's in_progress irreversible task acknowledge a dangerous command.
    POSIX only: on Windows the guard keeps the raw env id by design (the
    resolvers judge no id there — owner rule "Windows untouched")."""

    def setUp(self):
        if os.name == "nt":
            self.skipTest("Windows: the guard keeps the raw env id (no id is judged there)")

    def test_dead_id_does_not_acknowledge(self):
        import json, shutil, tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / ".agent" / "tasks" / "001-x").mkdir(parents=True)
        (d / ".agent" / "tasks" / "001-x" / "task.md").write_text(
            "# 001 - x\n\n## Status\nin_progress\n\n## Risk\nirreversible\n\n## Work\n- [ ] g\n",
            encoding="utf-8")
        p = subprocess.Popen(["true"])
        p.wait()                            # a real, reaped (dead) pid
        sd = d / ".agent" / "sessions" / f"pid-{p.pid}"
        sd.mkdir(parents=True)
        (sd / "current_state").write_text("001\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("PLAYBOOK_ALLOW_DANGEROUS", "PLAYBOOK_PROC_ROOT")}
        env["PLAYBOOK_SESSION_ID"] = f"pid-{p.pid}"
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}})
        r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "command_guard.py")],
                           input=payload, cwd=d, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, f"a stale id acknowledged a dangerous command:\n{r.stderr}")


class SessionStartIgnoresAnInheritedPidId(_ProjectMixin):
    """Owner decision 2026-09-28 (the second propagation path): session-start
    resolves a `pid-<digits>` session id from the walk ONLY — an inherited one
    is never re-exported into the new session's env file. Non-pid ids
    (`judge`, a subagent's uuid) are exported as before."""
    PROC = True

    def _export_of(self, rows, sid=None):
        self.set_tree(rows)
        self.env_file.write_text("", encoding="utf-8")
        payload = {"hook_event_name": "SessionStart", "source": "startup"}
        extra = {"PLAYBOOK_SESSION_ID": sid} if sid else {}
        r = subprocess.run([bash_or_skip(), str(PLUGIN / "scripts" / "session-start-hook")],
                           input=__import__("json").dumps(payload), cwd=self.project,
                           env=self.env(CLAUDE_ENV_FILE=str(self.env_file), **extra),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [ln for ln in self.env_file.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def test_inherited_live_agent_id_is_not_exported(self):
        # the inherited id names a REAL live agent (a copy of `sleep` named
        # `claude`) that is not this session
        from tests._fake_agent import spawn_fake_agent, stop
        agent = spawn_fake_agent(self.tmp)
        self.addCleanup(stop, agent)
        rows = BASE + [(agent.pid, 1, TERMINAL)]
        self.assertEqual(self._export_of(rows, f"pid-{agent.pid}"),
                         ["export PLAYBOOK_SESSION_ID=pid-400"])

    def test_without_env_the_export_is_unchanged(self):
        self.assertEqual(self._export_of(BASE), ["export PLAYBOOK_SESSION_ID=pid-400"])

    def test_daemon_only_with_an_inherited_id_exports_nothing(self):
        daemon_only = [(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD), AGENT]
        self.assertEqual(self._export_of(daemon_only, "pid-4713"), [])

    def test_non_pid_ids_are_still_exported(self):
        self.assertEqual(self._export_of(BASE, "9d1c2e4a-uuid-sub"),
                         ["export PLAYBOOK_SESSION_ID=9d1c2e4a-uuid-sub"])
        # Task 149 (PLAN S12b): a JUDGE session's hook does nothing at all — the
        # adapter already put PLAYBOOK_SESSION_ID=judge in the judge's environment,
        # and every write the hook tried failed on the judge's read-only project
        # (tests/test_judge_session_quiet.py).
        self.assertEqual(self._export_of(BASE, "judge"), [])


class SessionStartIgnoresAnInheritedPidIdPs(SessionStartIgnoresAnInheritedPidId):
    PROC = False


class Round2Findings(_ProjectMixin):
    """Impl panel round 2 (task 106)."""
    PROC = True

    def _irreversible_in(self, sid):
        tf = self.project / ".agent" / "tasks" / "001-t" / "task.md"
        tf.write_text("# 001 - t\n\n## Status\nin_progress\n\n## Risk\nirreversible\n\n## Work\n- [ ] g\n",
                      encoding="utf-8")
        sd = self.project / ".agent" / "sessions" / sid
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "current_state").write_text("001\n", encoding="utf-8")

    def _guard(self, env_sid):
        import json
        env = self.env(PLAYBOOK_SESSION_ID=env_sid)
        env.pop("PLAYBOOK_ALLOW_DANGEROUS", None)
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}})
        return subprocess.run([sys.executable, str(PLUGIN / "scripts" / "command_guard.py")], input=payload,
                              cwd=self.project, env=env, capture_output=True, text=True, timeout=60)

    def test_guard_follows_a_dead_env_id_to_the_walked_session(self):
        # A: the CLI and the hooks resolve pid-400; its irreversible task must
        # acknowledge exactly as it would with the right id in the env
        self.set_tree(BASE)
        self._irreversible_in("pid-400")
        self.assertEqual(self._guard(f"pid-{DEAD}").returncode, 0)

    def _task(self, n, risk):
        tf = self.project / ".agent" / "tasks" / f"{n}-t" / "task.md"
        tf.write_text(f"# {n} - t\n\n## Status\nin_progress\n\n## Risk\n{risk}\n\n## Work\n- [ ] g\n",
                      encoding="utf-8")

    def _point(self, sid, n):
        sd = self.project / ".agent" / "sessions" / sid
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "current_state").write_text(f"{n}\n", encoding="utf-8")

    def test_guard_follows_a_sibling_env_id_to_the_walked_session(self):
        # single judge run 1: each session points at its OWN task, with
        # opposite risks, so the test tells which pointer decided
        self.set_tree(BASE + [AGENT])
        self._point("pid-400", "001")
        self._point("pid-4713", "002")
        self._task("001", "irreversible")
        self._task("002", "reversible")
        self.assertEqual(self._guard("pid-4713").returncode, 0, "the walked session's irreversible task must decide")
        self._task("001", "reversible")
        self._task("002", "irreversible")
        self.assertEqual(self._guard("pid-4713").returncode, 2, "the sibling's irreversible task must not acknowledge")

    def test_guard_journals_the_resolved_session_id(self):
        # single judge run 1: the enforcement journal attributes the decision
        # to the id the guard used, not to the raw (stale) env value
        import json
        self.set_tree(BASE)
        self._irreversible_in("pid-400")
        self.assertEqual(self._guard(f"pid-{DEAD}").returncode, 0)
        jf = self.project / ".agent" / "journal" / "enforcement.jsonl"
        last = json.loads(jf.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last.get("session_id"), "pid-400", last)

    def test_session_start_exports_the_id_its_hooks_will_use(self):
        # G: claude started from codex started from claude — the inherited
        # pid-<codex> is IN the chain, so the hooks keep it; the export must match
        rows = [(4800, 4700, ("codex", "codex")), (4700, 400, SHELL), (400, 300, TERMINAL), (300, 1, SHELL)]
        self.set_tree(rows)
        self.env_file.write_text("", encoding="utf-8")
        r = subprocess.run([bash_or_skip(), str(PLUGIN / "scripts" / "session-start-hook")],
                           input='{"hook_event_name":"SessionStart","source":"startup"}', cwd=self.project,
                           env=self.env(CLAUDE_ENV_FILE=str(self.env_file), PLAYBOOK_SESSION_ID="pid-4800"),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        exported = [ln for ln in self.env_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        hook_id = self.bash_resolve(PLAYBOOK_SESSION_ID="pid-4800")
        self.assertEqual(hook_id, "pid-4800")
        self.assertEqual(exported, [f"export PLAYBOOK_SESSION_ID={hook_id}"])


class PsArgsTimeoutOnAClaudeIsNotASession(unittest.TestCase):
    """F (round 2): on the ps path an unreadable argv of a `claude*` N (the
    daemon's processes are claude.exe too) must count as not-a-session — as in
    the walk — not "cannot judge, keep"."""

    def test_args_timeout_is_stale(self):
        sys.path.insert(0, str(PLUGIN))
        try:
            import tasks.core as core
        finally:
            sys.path.remove(str(PLUGIN))
        from unittest import mock
        def run(cmd, *a, **k):
            if cmd[-1] == "args=":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=k.get("timeout"))
            return subprocess.CompletedProcess(cmd, 0, "S claude.exe\n", "")
        with mock.patch.dict(os.environ, {"PLAYBOOK_PROC_ROOT": "/nonexistent-proc"}), \
                mock.patch.object(core.subprocess, "run", side_effect=run):
            self.assertTrue(core._env_pid_is_stale("pid-4715"))


class StaleEnvBlockedFindsTheActiveTask(_ProjectMixin):
    """`tasks blocked` with a dead env id used to answer "No active task"."""
    PROC = True

    def test_blocked_with_a_dead_env_blocks_the_active_task(self):
        self.set_tree(BASE)
        sd = self.project / ".agent" / "sessions" / "pid-400"
        sd.mkdir(parents=True)
        (sd / "current_state").write_text("001\n", encoding="utf-8")
        tf = self.project / ".agent" / "tasks" / "001-t" / "task.md"
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "blocked", "decision needed"],
                           cwd=self.project, env=self.env(PLAYBOOK_SESSION_ID=f"pid-{DEAD}"),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}")
        self.assertIn("blocked", tf.read_text(encoding="utf-8").split("## Status", 1)[1].split("##", 1)[0])
        self.assertIn(STALE_MARK, r.stderr)


if __name__ == "__main__":
    unittest.main()
