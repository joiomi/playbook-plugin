"""The hook journal's python writer and its callers, and the parity of its three writers (PHASE-B item 2, task 222).

`pb_journal.append_hook_run` writes the line for the hooks that are python — codex's three and `command_guard.py`
(Claude's and grok's through command-guard-hook's `exec`, codex's directly) — with the bash writer's fields, caps,
line bound and lane and sink rules, and the same rotation: a project whose only hooks are python rotates too (plan
panel 2026-10-16: codex, grok, opus). The guard is the hottest hook and fails open, so its line can never change its
decision: without pb_journal it blocks and allows as before, a FIFO sink hangs nothing, a block is written once
(sonnet I5). A codex Stop block said in JSON with exit 0, and an internal error that exits 2, read as what they are
(opus I4, grok I3). `monitor-nudge.sh` keeps an inline copy (it cannot source the lib); one parity table holds the bash
lib, pb_journal and the nudge to the same sink answers (sonnet m2).

The REAL scripts run as subprocesses in temp projects. Run: python3 -m unittest tests.test_hook_journal_python
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"
NUDGE = PLUGIN / "hooks" / "monitor-nudge.sh"
LIMIT = 20971520


def _load_pb_journal():
    spec = importlib.util.spec_from_file_location("pb_journal_t222", SCRIPTS / "pb_journal.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = self.root / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        self.journal = self.project / ".agent" / "journal" / "hooks.jsonl"
        self.proc = self.root / "proc-no-agent"
        (self.proc / "self").mkdir(parents=True)
        (self.root / "home").mkdir()
        self.pbj = _load_pb_journal()

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
               if not k.startswith(("PLAYBOOK_", "_PB_", "PB_", "CLAUDE_", "CODEX_")) and k not in ("BASH_ENV", "ENV")}
        env.update(PLAYBOOK_SESSION_ID="pid-1", HOME=str(self.root / "home"), PLAYBOOK_PROC_ROOT=str(self.proc))
        env.update(extra)
        return env

    def lines(self, path=None):
        p = path or self.journal
        if not p.is_file():             # a FIFO planted by a test is never opened here (it would block)
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


class ThePythonWriter(_Tmp):
    def age_closed(self, path, seconds=1200):
        """A closed file as it is `seconds` after its rename: the rename time its name records, and its mtime."""
        m = re.fullmatch(r"(hooks\.jsonl\.archived-\d{8}-\d{6})-(?:(\d+)-)?(\d+)", path.name)
        assert m, path.name
        new = path.with_name(f"{m.group(1)}-{int(time.time()) - seconds}-{m.group(3)}")
        path.rename(new)
        old = time.time() - seconds
        os.utime(new, (old, old))
        return new

    def test_a_closed_file_waits_for_the_rename_time_its_name_records(self):
        """Post-panel judge run 1 (codex, grave): the python writer too — an old mtime alone never compresses."""
        self.journal.parent.mkdir()
        self.journal.write_text("{}\n")
        killed = self.journal.parent / f"hooks.jsonl.archived-20261016-120000-{int(time.time())}-99"
        killed.write_text("{}\n")
        nameless = self.journal.parent / "hooks.jsonl.archived-foreign"
        nameless.write_text("{}\n")
        old = time.time() - 1200
        for f in (killed, nameless):
            os.utime(f, (old, old))
        os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"] = "1"
        try:
            self.pbj.append_hook_run(self.project, hook="x")
            self.assertTrue(killed.exists() and nameless.exists())
            aged = self.age_closed(killed)
            self.pbj.append_hook_run(self.project, hook="x")
        finally:
            del os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"]
        self.assertTrue(aged.with_name(aged.name + ".gz").exists())
        self.assertTrue(nameless.exists())
        written = self.journal.parent / f"hooks.jsonl.archived-20261016-110000-{int(time.time()) - 1200}-98"
        written.write_text("{}\n")                             # renamed long ago, written a moment ago: waits
        os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"] = "1"
        try:
            self.pbj.append_hook_run(self.project, hook="x")
        finally:
            del os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"]
        self.assertTrue(written.exists())

    def test_one_line_with_the_bash_writers_fields(self):
        self.pbj.append_hook_run(self.project, hook="codex-stop", event="Stop", session_id="pid-1",
                                 host_session_id="h-1", decision="allow", detail='released "x" \\ y', ms=12,
                                 exit_code=0)
        rec = self.lines()[0]
        self.assertEqual(tuple(rec), self.pbj.HOOK_FIELDS)
        self.assertEqual((rec["hook"], rec["event"], rec["decision"], rec["detail"], rec["ms"], rec["exit"]),
                         ("codex-stop", "Stop", "allow", 'released "x" \\ y', 12, 0))

    def test_the_line_stays_within_one_kib(self):
        for q in ('"' * 3000, "\\" * 3000, "é" * 3000, "😀" * 2000):
            with self.subTest(q=q[:3]):
                self.journal.unlink(missing_ok=True)
                self.pbj.append_hook_run(self.project, hook="h" * 400, event=q, tool=q, tool_use_id=q, session_id=q,
                                         host_session_id=q, agent_id=q, decision=q, detail=q, ms=10 ** 20,
                                         exit_code=0, error=q)
                raw = self.journal.read_bytes()
                self.assertLessEqual(len(raw), 1024)
                self.assertEqual(tuple(json.loads(raw)), self.pbj.HOOK_FIELDS)

    def test_where_it_does_not_write(self):
        target = self.root / "target"
        target.mkdir()
        cases = {}
        p = self.root / "fresh"
        (p / ".agent" / "alice" / "tasks").mkdir(parents=True)
        cases["fresh clone"] = p
        p = self.root / "badmarker"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (p / ".agent" / "current_user").write_text("a\nb\n")
        cases["invalid marker"] = p
        p = self.root / "linkedlane"
        p.mkdir()
        (target / "lane" / "tasks").mkdir(parents=True)
        (p / ".agent").symlink_to(target / "lane")
        cases["linked lane"] = p
        p = self.root / "linkedjournal"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (target / "j").mkdir()
        (p / ".agent" / "journal").symlink_to(target / "j")
        cases["linked journal"] = p
        p = self.root / "notplaybook"
        (p / ".agent").mkdir(parents=True)                  # opus r1: an `.agent/` without tasks is not a project
        cases["not a playbook project"] = p
        for name, proj in cases.items():
            with self.subTest(name):
                self.pbj.append_hook_run(proj, hook="x", decision="ok")
                self.assertEqual(list(proj.rglob("hooks.jsonl")), [])
        for sid in ("judge", "tail-cert"):                  # opus r1: a review writes no line
            os.environ["PLAYBOOK_SESSION_ID"] = sid
            try:
                self.pbj.append_hook_run(self.project, hook="x")
            finally:
                del os.environ["PLAYBOOK_SESSION_ID"]
            self.assertFalse(self.journal.exists())
        self.assertEqual(list(target.rglob("hooks.jsonl")), [])
        self.pbj.append_hook_run(None, hook="x")

    def test_a_fifo_sink_does_not_hang(self):
        (self.journal.parent).mkdir()
        os.mkfifo(self.journal)
        t0 = time.monotonic()
        self.pbj.append_hook_run(self.project, hook="x")
        self.assertLess(time.monotonic() - t0, 2)

    def test_past_the_limit_it_rotates_and_compresses(self):
        self.journal.parent.mkdir()
        line = json.dumps({"hook": "old", "detail": "x" * 200}) + "\n"
        n = (LIMIT + 4096) // len(line) + 1
        self.journal.write_text(line * n)
        os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"] = "1"
        try:
            held = open(self.journal, "a")
            self.pbj.append_hook_run(self.project, hook="codex-stop")
            held.write('{"hook":"held-late"}\n')         # G1: a descriptor held across the rename
            held.close()
            arch = sorted(self.journal.parent.glob("hooks.jsonl.archived-*"))
            self.assertEqual(len(arch), 1, arch)
            self.assertFalse(arch[0].name.endswith(".gz"))      # just closed: compressed only once idle
            self.age_closed(arch[0])
            self.pbj.append_hook_run(self.project, hook="codex-stop")
        finally:
            del os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"]
        arch = sorted(self.journal.parent.glob("hooks.jsonl.archived-*"))
        self.assertEqual(len(arch), 1, arch)
        self.assertTrue(arch[0].name.endswith(".gz"))
        with gzip.open(arch[0], "rt") as f:
            text = f.read()
        self.assertEqual(len(text.splitlines()), n + 1)
        self.assertIn('"held-late"', text)
        self.assertEqual([r["hook"] for r in self.lines()], ["codex-stop", "codex-stop"])

    def test_the_duration_counts_the_journals_own_work(self):
        """Impl panel r1 (codex I2): the python writer takes the duration after its rotation and compression."""
        self.journal.parent.mkdir()
        self.journal.write_text("{}\n")
        arch = self.journal.parent / f"hooks.jsonl.archived-20261016-000000-{int(time.time()) - 1200}-1"
        arch.write_text("{}\n")
        old = time.time() - 1200
        os.utime(arch, (old, old))
        real = self.pbj._hj_compress
        setattr(self.pbj, "_hj_compress", lambda a: (time.sleep(1), real(a)))
        os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"] = "1"
        try:
            self.pbj.append_hook_run(self.project, hook="x", timing=("", time.time()))
        finally:
            del os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"]
            setattr(self.pbj, "_hj_compress", real)
        self.assertGreaterEqual(self.lines()[-1]["ms"], 900)
        self.assertTrue(Path(str(arch) + ".gz").exists())

    def test_an_idle_oversized_file_is_not_compressed_by_the_run_that_closes_it(self):
        """Impl panel r2 (codex, grok — reproduced, grave): the python writer compressed an idle file in the same run."""
        self.journal.parent.mkdir()
        line = json.dumps({"hook": "old", "detail": "x" * 200}) + "\n"
        self.journal.write_text(line * ((LIMIT + 4096) // len(line) + 1))
        old = time.time() - 1200
        os.utime(self.journal, (old, old))
        os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"] = "1"
        try:
            held = open(self.journal, "a")
            self.pbj.append_hook_run(self.project, hook="x")
            held.write('{"hook":"held-late"}\n')
            held.close()
            arch = sorted(self.journal.parent.glob("hooks.jsonl.archived-*"))
            self.assertEqual([a.name.endswith(".gz") for a in arch], [False], arch)
            self.age_closed(arch[0])
            self.pbj.append_hook_run(self.project, hook="x")
        finally:
            del os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"]
        gz = sorted(self.journal.parent.glob("hooks.jsonl.archived-*.gz"))
        with gzip.open(gz[0], "rt") as f:
            self.assertIn('"held-late"', f.read())

    def test_an_append_waits_while_the_journal_is_held_exclusively(self):
        """The owner 22:21: the python writer's append holds a shared lock too."""
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.journal.parent.mkdir()
        holder = subprocess.Popen(["flock", "-x", str(self.journal.parent), "sleep", "0.8"])
        try:
            time.sleep(0.2)
            t0 = time.monotonic()
            self.pbj.append_hook_run(self.project, hook="x")
            waited = time.monotonic() - t0
        finally:
            holder.wait()
        self.assertGreaterEqual(waited, 0.45)
        self.assertEqual(len(self.lines()), 1)
        self.pbj.append_hook_run(self.project, hook="y")       # (the ms of a timed run: below)

    def test_the_python_line_counts_the_wait_and_spools_past_it(self):
        """Post-panel run 3: the wait counts in `ms`; the owner 00:13: past the wait, a spool, never an unlocked append."""
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.journal.parent.mkdir()
        holder = subprocess.Popen(["flock", "-x", str(self.journal.parent), "sleep", "0.8"])
        try:
            time.sleep(0.2)
            self.pbj.append_hook_run(self.project, hook="x", timing=("", time.time()))
        finally:
            holder.wait()
        self.assertGreaterEqual(self.lines()[-1]["ms"], 450)
        holder = subprocess.Popen(["flock", "-x", str(self.journal.parent), "sleep", "1.8"])
        try:
            time.sleep(0.2)
            self.pbj.append_hook_run(self.project, hook="spooled")
            self.assertEqual([r["hook"] for r in self.lines()], ["x"])
            self.assertEqual(len(list(self.journal.parent.glob("hooks.jsonl.spool-*"))), 1)
        finally:
            holder.wait()
        os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"] = "1"
        try:
            self.pbj.append_hook_run(self.project, hook="next")
        finally:
            del os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"]
        self.assertEqual(sorted(r["hook"] for r in self.lines()), ["next", "spooled", "x"])
        self.assertEqual(list(self.journal.parent.glob("hooks.jsonl.spool*")), [])

    def test_the_first_append_of_a_project_holds_the_lock_too(self):
        """Post-panel judge run 4 (codex, grave): with no journal directory yet, the python writer appended unlocked.
        It now makes the directory first; while it writes, an exclusive lock on it cannot be had."""
        import fcntl
        seen = []
        real = self.pbj._write_record

        def probe(*a, **k):
            fd = os.open(str(self.journal.parent), os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.append("unlocked")
            except OSError:
                seen.append("locked")
            finally:
                os.close(fd)
            return real(*a, **k)
        setattr(self.pbj, "_write_record", probe)
        try:
            self.assertFalse(self.journal.parent.exists())
            self.pbj.append_hook_run(self.project, hook="first")
        finally:
            setattr(self.pbj, "_write_record", real)
        self.assertEqual(seen, ["locked"])
        self.assertEqual([r["hook"] for r in self.lines()], ["first"])

    def test_the_python_sweep_takes_a_dead_writers_temporary_and_leaves_a_live_ones(self):
        """Post-panel judge run 4 (codex, grave): the python sweep removed temporaries for their age."""
        self.journal.parent.mkdir()
        self.journal.write_text("")
        dead = subprocess.Popen(["true"])
        dead.wait()
        gone = self.journal.parent / f"hooks.jsonl.spooling-20261017-000000-{dead.pid}-1"
        gone.write_text('{"hook":"orphan"}\n')
        alive = self.journal.parent / f"hooks.jsonl.spooling-20261017-000000-{os.getpid()}-2"
        alive.write_text('{"hook":"in-flight"}\n')
        old = time.time() - 1200
        for f in (gone, alive):
            os.utime(f, (old, old))
        os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"] = "1"
        try:
            self.pbj.append_hook_run(self.project, hook="x")
        finally:
            del os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"]
        self.assertFalse(gone.exists())
        self.assertIn("orphan", [r["hook"] for r in self.lines()])
        self.assertTrue(alive.exists())

    def test_a_held_lock_means_no_rotation(self):
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.journal.parent.mkdir()
        self.journal.write_text("x" * (LIMIT + 10) + "\n")
        holder = subprocess.Popen(["flock", str(self.journal.parent), "sleep", "5"])
        try:
            time.sleep(0.3)
            os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"] = "1"
            try:
                self.pbj.append_hook_run(self.project, hook="x")
            finally:
                del os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"]
            self.assertEqual(list(self.journal.parent.glob("hooks.jsonl.archived-*")), [])
        finally:
            holder.kill()
            holder.wait()


class TheGuard(_Tmp):
    def guard(self, payload, *, scripts=SCRIPTS, wrapper=True, **env):
        argv = [bash_or_skip(), str(scripts / "command-guard-hook")] if wrapper else \
               [sys.executable, "-E", "-s", str(scripts / "command_guard.py")]
        before = len(self.lines())
        r = subprocess.run(argv, input=json.dumps(payload), cwd=str(self.project), env=self.env(**env),
                           capture_output=True, text=True, timeout=30)
        return r, self.lines()[before:]

    def test_through_the_wrapper_one_line_naming_the_rule(self):
        r, new = self.guard({"session_id": "h-1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                             "tool_input": {"command": "rm -rf /"}, "tool_use_id": "toolu_G"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(len(new), 1, new)
        rec = new[0]
        self.assertEqual((rec["hook"], rec["decision"], rec["exit"], rec["tool_use_id"], rec["host_session_id"]),
                         ("command-guard", "block", 2, "toolu_G", "h-1"))
        self.assertTrue(rec["detail"])
        self.assertIsInstance(rec["ms"], int)
        r, new = self.guard({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual((r.returncode, len(new), new[0]["decision"]), (0, 1, "allow"))

    def test_codex_runs_it_directly(self):
        r, new = self.guard({"hook_event_name": "PreToolUse", "tool_name": "exec_command",
                             "tool_input": {"cmd": ["bash", "-lc", "git push --force origin main"]}}, wrapper=False)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual([(x["decision"], x["exit"]) for x in new], [("block", 2)])

    def test_without_pb_journal_the_decisions_stand(self):
        copy = self.root / "scripts-nojournal"
        shutil.copytree(SCRIPTS, copy, ignore=shutil.ignore_patterns("pb_journal.py", "__pycache__"))
        r, new = self.guard({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}, scripts=copy)
        self.assertEqual((r.returncode, new), (2, []))
        r, new = self.guard({"tool_name": "Bash", "tool_input": {"command": "ls"}}, scripts=copy)
        self.assertEqual((r.returncode, new), (0, []))

    def test_a_fifo_sink_hangs_nothing(self):
        self.journal.parent.mkdir()
        os.mkfifo(self.journal)
        t0 = time.monotonic()
        r, _ = self.guard({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}})
        self.assertEqual(r.returncode, 2)
        self.assertLess(time.monotonic() - t0, 10)

    def test_a_classifier_error_is_an_allow_with_its_error(self):
        copy = self.root / "scripts-broken"
        shutil.copytree(SCRIPTS, copy, ignore=shutil.ignore_patterns("__pycache__"))
        g = copy / "command_guard.py"
        g.write_text(g.read_text().replace("def classify_command(", "def classify_command(*a, **k):\n"
                                                                    "    raise RuntimeError('boom')\n\n"
                                                                    "def _orig_classify_command(", 1))
        r, new = self.guard({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}, scripts=copy)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((new[0]["decision"], new[0]["detail"]), ("allow", "fail open: classifier error"))
        self.assertEqual(new[0]["error"], "RuntimeError")            # sonnet r1: the type only, no message text


class TheCodexHooks(_Tmp):
    def codex(self, hook, payload, *, scripts=SCRIPTS, raw=None, **env):
        before = len(self.lines())
        r = subprocess.run([sys.executable, "-E", "-s", str(scripts / hook)],
                           input=raw if raw is not None else json.dumps(payload), cwd=str(self.project),
                           env=self.env(PLAYBOOK_PROJECT_ROOT=str(self.project), **env), capture_output=True, text=True,
                           timeout=60)
        return r, self.lines()[before:]

    def test_a_prompt_is_logged(self):
        r, new = self.codex("codex-user-prompt-hook", {"hook_event_name": "UserPromptSubmit", "prompt": "hi",
                                                       "turn_id": "t1", "session_id": "c-1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(x["hook"], x["decision"], x["detail"], x["host_session_id"]) for x in new],
                         [("codex-user-prompt", "ok", "logged", "c-1")])

    def test_a_swallowed_exception_is_an_error(self):
        for hook in ("codex-stop-hook", "codex-user-prompt-hook"):
            with self.subTest(hook=hook):
                r, new = self.codex(hook, None, raw="not json")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual([x["decision"] for x in new], ["error"])
                self.assertRegex(new[0]["error"], r"^[A-Za-z_][A-Za-z0-9_.]*$")      # an exception type only

    def test_a_codex_only_project_rotates(self):
        """No bash hook runs under codex: its python writer closes the file past the bound itself (plan panel, codex)."""
        self.journal.parent.mkdir()
        line = json.dumps({"hook": "old", "detail": "x" * 200}) + "\n"
        self.journal.write_text(line * ((LIMIT + 4096) // len(line) + 1))
        before = len(self.lines())
        r = subprocess.run([sys.executable, "-E", "-s", str(SCRIPTS / "codex-user-prompt-hook")],
                           input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hi", "turn_id": "t"}),
                           cwd=str(self.project), capture_output=True, text=True, timeout=60,
                           env=self.env(PLAYBOOK_PROJECT_ROOT=str(self.project), PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        arch = sorted(self.journal.parent.glob("hooks.jsonl.archived-*"))
        self.assertEqual(len(arch), 1, arch)                 # closed; compressed on a later run once idle (G1)
        self.assertEqual(len(arch[0].read_text().splitlines()), before)
        self.assertEqual([x["hook"] for x in self.lines()], ["codex-user-prompt"])

    def test_a_stop_block_said_in_json_reads_block(self):
        """Codex's Stop hook blocks through its JSON answer with exit 0 (plan panel, grok I3): the line says `block`."""
        (self.project / "src").mkdir()
        app = self.project / "src" / "app.py"
        app.write_text("x = 1\n")
        r, _ = self.codex("codex-user-prompt-hook", {"hook_event_name": "UserPromptSubmit", "prompt": "go",
                                                     "turn_id": "t1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        app.write_text("x = 2\n")                         # code changed with no task this turn
        r, new = self.codex("codex-stop-hook", {"hook_event_name": "Stop", "turn_id": "t1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout).get("decision"), "block", r.stdout)
        self.assertEqual([(x["hook"], x["decision"], x["exit"]) for x in new], [("codex-stop", "block", 0)])
        self.assertIn("no task", new[0]["detail"])

    def test_a_stop_with_an_active_task_is_one_line(self):
        """Impl panel r1 (sonnet I2): with an active task codex's Stop runs `bash stop-hook`; one run, one line."""
        (self.project / ".agent" / "tasks" / "001-x").mkdir()
        (self.project / ".agent" / "tasks" / "001-x" / "task.md").write_text(
            "# 001 - x\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] open\n")
        (self.project / ".agent" / "sessions" / "sess-c1").mkdir(parents=True)
        (self.project / ".agent" / "sessions" / "sess-c1" / "current_state").write_text("001\n")
        r, new = self.codex("codex-stop-hook", {"hook_event_name": "Stop", "turn_id": "t1"}, PLAYBOOK_SESSION_ID="sess-c1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([x["hook"] for x in new], ["codex-stop"], new)
        # impl panel r2 (opus, codex, gemini): the line names the active task's open gates, not "no task"
        self.assertEqual((new[0]["decision"], new[0]["detail"]), ("block", "held: 1 unchecked gate(s) in task 001"))

    def test_a_stop_with_no_decision_is_released(self):
        r, new = self.codex("codex-stop-hook", {"hook_event_name": "Stop", "turn_id": "t1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(x["decision"], x["exit"]) for x in new], [("allow", 0)])

    def test_apply_patch_block_and_internal_error(self):
        patch = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_use_id": "call_1",
                 "tool_input": {"command": "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch\n"}}
        r, new = self.codex("codex-apply-patch-hook", patch)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual([(x["decision"], x["tool_use_id"], x["exit"]) for x in new], [("block", "call_1", 2)])
        self.assertEqual(new[0]["detail"], "no active task")         # sonnet, codex r1: never the patch's paths
        self.assertNotIn("app.py", json.dumps(new[0]))
        copy = self.root / "scripts-nolib"
        shutil.copytree(SCRIPTS, copy, ignore=shutil.ignore_patterns("lib", "__pycache__"))
        r, new = self.codex("codex-apply-patch-hook", patch, scripts=copy)
        self.assertEqual(r.returncode, 2, r.stderr)             # fail-closed, unchanged
        self.assertEqual([(x["decision"], x["exit"]) for x in new], [("error", 2)])
        self.assertIn("internal error", new[0]["detail"])


class ThreeWritersOneAnswer(_Tmp):
    """The bash lib, pb_journal and the nudge's inline copy: the same sink answers, and the same line."""

    def scenarios(self):
        target = self.root / "target"
        target.mkdir(exist_ok=True)
        out = {}

        def proj(name, *, tasks=True):
            p = self.root / name
            if tasks:
                (p / ".agent" / "tasks").mkdir(parents=True)
            else:
                p.mkdir()
            return p
        out["root lane"] = (proj("ok"), True)
        p = proj("user", tasks=False)
        (p / ".agent" / "alice" / "tasks").mkdir(parents=True)
        (p / ".agent" / "current_user").write_text("alice\n")
        out["a user's lane"] = (p, True)
        p = proj("fresh", tasks=False)
        (p / ".agent" / "alice" / "tasks").mkdir(parents=True)
        out["fresh clone"] = (p, False)
        p = proj("bad")
        (p / ".agent" / "current_user").write_text("a\nb\n")
        out["invalid marker"] = (p, False)
        p = proj("linkedj")
        (target / "j").mkdir()
        (p / ".agent" / "journal").symlink_to(target / "j")
        out["linked journal"] = (p, False)
        p = proj("linkedf")
        (p / ".agent" / "journal").mkdir()
        (target / "f").write_text("")
        (p / ".agent" / "journal" / "hooks.jsonl").symlink_to(target / "f")
        out["linked file"] = (p, False)
        p = proj("fifo")
        (p / ".agent" / "journal").mkdir()
        os.mkfifo(p / ".agent" / "journal" / "hooks.jsonl")
        out["fifo"] = (p, False)
        if os.geteuid() != 0:
            p = proj("ro")
            (p / ".agent").chmod(0o555)
            out["read-only lane"] = (p, False)
        return out

    @staticmethod
    def written(p):
        return [f for f in p.rglob("hooks.jsonl") if f.is_file() and not f.is_symlink() and f.stat().st_size > 0]

    def bash_lib(self, p):
        script = ('_PB_HJ_T0=$EPOCHREALTIME\nsource "$1"\npb_hook_begin parity\nINPUT=$(cat)\n'
                  'PROJECT_DIR="$2"\nexit 0\n')
        subprocess.run([bash_or_skip(), "-c", script, "_", str(SCRIPTS / "gate-echo-lib.sh"), str(p)],
                       input=self.PAYLOAD, cwd=str(p), env=self.env(), capture_output=True, text=True, timeout=20)

    def python(self, p):
        self.pbj.append_hook_run(p, hook="parity", event="PostToolUse", tool="Bash", tool_use_id="toolu_P",
                                 host_session_id="h-1", agent_id="", decision="ok", ms=1, exit_code=0)

    def nudge(self, p):
        subprocess.run([bash_or_skip(), str(NUDGE)], input=self.PAYLOAD, cwd=str(p), env=self.env(),
                       capture_output=True, text=True, timeout=20)

    PAYLOAD = ('{"session_id":"h-1","hook_event_name":"PostToolUse","tool_name":"Bash","tool_input":{"command":"x"},'
               '"tool_response":{},"tool_use_id":"toolu_P","duration_ms":2}')

    def test_the_same_sink_answers(self):
        for writer in (self.bash_lib, self.python, self.nudge):
            for name, (p, ok) in self.scenarios().items():
                with self.subTest(writer=writer.__name__, case=name):
                    t0 = time.monotonic()
                    writer(p)
                    self.assertLess(time.monotonic() - t0, 10)
                    self.assertEqual(bool(self.written(p)), ok, name)
            for p in self.root.iterdir():
                if p.name not in ("home", "proc-no-agent"):
                    for q in p.rglob("*"):
                        try:
                            if q.is_dir() and not q.is_symlink():
                                q.chmod(0o755)
                        except OSError:
                            pass
                    shutil.rmtree(p, ignore_errors=True)

    def test_the_nudge_skips_a_judge_and_borrows_no_nested_agent_id(self):
        p = self.root / "nud"
        (p / ".agent" / "tasks").mkdir(parents=True)
        agent_call = ('{"session_id":"h-1","hook_event_name":"PostToolUse","tool_name":"Agent","tool_input":{"p":"x"},'
                      '"tool_response":{"agentId":"a-sub"},"tool_use_id":"toolu_A"}')
        subprocess.run([bash_or_skip(), str(NUDGE)], input=agent_call, cwd=str(p), env=self.env(), capture_output=True,
                       text=True, timeout=20)
        recs = self.lines(p / ".agent" / "journal" / "hooks.jsonl")
        self.assertEqual([(r["agent_id"], r["tool_use_id"]) for r in recs], [("", "toolu_A")])   # codex r1 I3
        subprocess.run([bash_or_skip(), str(NUDGE)], input=agent_call, cwd=str(p),
                       env=self.env(PLAYBOOK_SESSION_ID="judge"), capture_output=True, text=True, timeout=20)
        self.assertEqual(len(self.lines(p / ".agent" / "journal" / "hooks.jsonl")), 1)          # opus r1 I1

    def test_the_nudge_waits_while_the_journal_is_held_exclusively(self):
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        p = self.root / "nudlock"
        (p / ".agent" / "journal").mkdir(parents=True)
        (p / ".agent" / "tasks").mkdir()
        holder = subprocess.Popen(["flock", "-x", str(p / ".agent" / "journal"), "sleep", "0.8"])
        try:
            time.sleep(0.2)
            t0 = time.monotonic()
            subprocess.run([bash_or_skip(), str(NUDGE)], input=self.PAYLOAD, cwd=str(p), env=self.env(),
                           capture_output=True, text=True, timeout=20)
            waited = time.monotonic() - t0
        finally:
            holder.wait()
        self.assertGreaterEqual(waited, 0.45)
        recs = self.lines(p / ".agent" / "journal" / "hooks.jsonl")
        self.assertEqual(len(recs), 1)
        self.assertGreaterEqual(recs[0]["ms"], 450)              # post-panel run 3: the wait counts
        holder = subprocess.Popen(["flock", "-x", str(p / ".agent" / "journal"), "sleep", "1.8"])
        try:
            time.sleep(0.2)
            subprocess.run([bash_or_skip(), str(NUDGE)], input=self.PAYLOAD, cwd=str(p), env=self.env(),
                           capture_output=True, text=True, timeout=20)
            self.assertEqual(len(self.lines(p / ".agent" / "journal" / "hooks.jsonl")), 1)   # not appended unlocked
            self.assertEqual(len(list((p / ".agent" / "journal").glob("hooks.jsonl.spool-*"))), 1)
        finally:
            holder.wait()

    def test_the_nudge_spools_into_its_own_journal_under_a_path_with_a_colon(self):
        """Post-panel judge run 4 (codex — reproduced): the nudge stripped `:` from the whole spool path."""
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        p = self.root / "proj:with:colons"
        (p / ".agent" / "journal").mkdir(parents=True)
        (p / ".agent" / "tasks").mkdir()
        holder = subprocess.Popen(["flock", "-x", str(p / ".agent" / "journal"), "sleep", "1.8"])
        try:
            time.sleep(0.2)
            subprocess.run([bash_or_skip(), str(NUDGE)], input=self.PAYLOAD, cwd=str(p), env=self.env(),
                           capture_output=True, text=True, timeout=20)
            self.assertEqual(len(list((p / ".agent" / "journal").glob("hooks.jsonl.spool-*"))), 1)
        finally:
            holder.wait()

    def test_the_same_cut_of_a_text_full_of_quotes(self):
        """`detail` is cut where its ESCAPED form reaches the cap — the same place in bash and python (P04 of the
        mutation run: counting an escape as one byte kept twice the quotes in python)."""
        p = self.root / "cut"
        (p / ".agent" / "tasks").mkdir(parents=True)
        q = 'a"' * 150 + "\\" * 50
        script = ('_PB_HJ_T0=$EPOCHREALTIME\nsource "$1"\npb_hook_begin cut\nINPUT=""\nPROJECT_DIR="$2"\n'
                  'PB_HJ_DETAIL="$Q"\nexit 0\n')
        subprocess.run([bash_or_skip(), "-c", script, "_", str(SCRIPTS / "gate-echo-lib.sh"), str(p)], cwd=str(p),
                       env=self.env(Q=q), capture_output=True, text=True, timeout=20)
        self.pbj.append_hook_run(p, hook="cut", decision="ok", detail=q)
        bash_rec, py_rec = self.lines(p / ".agent" / "journal" / "hooks.jsonl")
        self.assertEqual(bash_rec["detail"], py_rec["detail"])
        self.assertTrue(q.startswith(bash_rec["detail"]) and len(bash_rec["detail"]) < len(q))

    def test_the_same_line(self):
        p = self.root / "same"
        (p / ".agent" / "tasks").mkdir(parents=True)
        self.bash_lib(p)
        self.python(p)
        self.nudge(p)
        recs = self.lines(p / ".agent" / "journal" / "hooks.jsonl")
        self.assertEqual(len(recs), 3)
        for r in recs:
            self.assertEqual(tuple(r), self.pbj.HOOK_FIELDS)
        strip = [{k: v for k, v in r.items() if k not in ("ts", "ms", "hook", "session_id")} for r in recs]
        self.assertEqual(strip[0], strip[1])
        self.assertEqual(strip[2].pop("detail"), "no nudge")        # the nudge says what it did; the rest is the same
        strip[0].pop("detail")
        self.assertEqual(strip[0], strip[2])


if __name__ == "__main__":
    unittest.main()
