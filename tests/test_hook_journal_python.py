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
        # in-process writes: a judge's `PLAYBOOK_SESSION_ID=judge` would make the writer skip every line (agy, impl r1)
        self._sid = os.environ.pop("PLAYBOOK_SESSION_ID", None)

    def tearDown(self):
        if self._sid is not None:
            os.environ["PLAYBOOK_SESSION_ID"] = self._sid
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
        self.assertEqual(tuple(rec), ("ts", "hook", "event", "tool", "tool_use_id", "session_id", "host_session_id",
                                      "agent_id", "decision", "detail", "ms", "exit", "error", "task", "section",
                                      "susp", "sts", "boot"))
        self.assertEqual(tuple(rec), self.pbj.HOOK_FIELDS)
        self.assertEqual((rec["hook"], rec["event"], rec["decision"], rec["detail"], rec["ms"], rec["exit"]),
                         ("codex-stop", "Stop", "allow", 'released "x" \\ y', 12, 0))

    def test_the_line_stays_within_two_kib(self):
        for q in ('"' * 3000, "\\" * 3000, "é" * 3000, "😀" * 2000):
            with self.subTest(q=q[:3]):
                self.journal.unlink(missing_ok=True)
                self.pbj.append_hook_run(self.project, hook="h" * 400, event=q, tool=q, tool_use_id=q, session_id=q,
                                         host_session_id=q, agent_id=q, decision=q, detail=q, ms=10 ** 20,
                                         exit_code=0, error=q, section=q, susp=10 ** 20, sts=10 ** 20, boot=q)
                raw = self.journal.read_bytes()
                self.assertLessEqual(len(raw), 2048)
                self.assertEqual(tuple(json.loads(raw)), self.pbj.HOOK_FIELDS)

    def test_a_line_with_every_field_at_its_cap_is_written_whole(self):
        """Task 234: past the old 1,024 bytes with the five keys, under the 2,048 cap — no text dropped (P11 of the
        mutation pass: a cap left at 1,024 in python alone survived the other tests)."""
        i, q = "i" * 100, '"' * 400
        self.pbj.append_hook_run(self.project, hook=i, event=i, tool=i, tool_use_id=i, session_id=i, host_session_id=i,
                                 agent_id=i, decision=i, detail=q, ms=10 ** 20, exit_code=1, error=q, task="9" * 16,
                                 section=q, susp=999_999_999_999, sts=999_999_999_999, boot="ffffffff")
        raw = self.journal.read_bytes()
        self.assertGreater(len(raw), 1024)
        self.assertLessEqual(len(raw), 2048)
        rec = json.loads(raw)
        self.assertEqual((rec["detail"], rec["section"], rec["error"]), ('"' * 64, '"' * 64, '"' * 120))

    def test_python_samples_the_suspend_clock_itself(self):
        """Task 234: a python hook's line carries the sample its own process took — `susp` (CLOCK_BOOTTIME minus
        CLOCK_MONOTONIC, whole seconds), `sts` (the wall time of that sample) and `boot` (the boot id's first 8)."""
        before = time.time()
        self.pbj.append_hook_run(self.project, hook="codex-stop", session_id="pid-1")
        after = time.time()
        rec = self.lines()[0]
        slept = time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)
        self.assertIsInstance(rec["susp"], int)
        self.assertLessEqual(abs(rec["susp"] - slept), 2)
        self.assertTrue(int(before) <= rec["sts"] <= after + 1, (before, rec["sts"], after))
        boot = Path("/proc/sys/kernel/random/boot_id").read_text()[:8]
        self.assertEqual(rec["boot"], boot)
        self.assertIsNone(rec["section"])                              # no section read: null

    def test_values_given_are_written_as_given_once_valid(self):
        cases = [(dict(susp=0, sts=5, boot="0a1b2c3d"), (0, 5, "0a1b2c3d")),
                 (dict(susp=-1, sts=10 ** 12, boot="0A1B2C3D"), (None, None, None)),
                 (dict(susp="12", sts=1.5, boot="0a1b2c3"), (None, None, None)),
                 (dict(susp=True, sts=False, boot=7), (None, None, None)),
                 (dict(sts=5), (None, 5, None))]                       # one given: none sampled
        for given, want in cases:
            with self.subTest(given=given):
                self.journal.unlink(missing_ok=True)
                self.pbj.append_hook_run(self.project, hook="x", **given)
                rec = self.lines()[0]
                self.assertEqual((rec["susp"], rec["sts"], rec["boot"]), want)

    def test_a_line_past_the_cap_drops_the_three_texts_and_keeps_every_key(self):
        old = self.pbj._HJ_LINE_MAX
        self.pbj._HJ_LINE_MAX = 520
        try:
            self.pbj.append_hook_run(self.project, hook="x", detail="x" * 300, error="x" * 300, section="x" * 300,
                                     task="234", susp=5, sts=6, boot="0a1b2c3d", exit_code=1)
        finally:
            self.pbj._HJ_LINE_MAX = old
        rec = self.lines()[0]
        self.assertEqual(tuple(rec), self.pbj.HOOK_FIELDS)
        self.assertEqual((rec["detail"], rec["error"], rec["section"]), ("", "", None))
        self.assertEqual((rec["task"], rec["susp"], rec["sts"], rec["boot"]), ("234", 5, 6, "0a1b2c3d"))

    def test_the_task_from_the_sessions_pointer(self):
        d = self.project / ".agent" / "sessions" / "pid-1"
        d.mkdir(parents=True)
        (d / "current_state").write_text("  45 \t\n99\n")
        self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1")
        self.assertEqual(self.lines()[-1]["task"], "45")
        for text in ("", "abc\n", "1" * 17, "23x\n"):
            with self.subTest(text=text):
                (d / "current_state").write_text(text)
                self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1")
                self.assertEqual(self.lines()[-1]["task"], "")
        a = self.project / ".agent"
        (a / "tasks" / "current_state").write_text("5\n")                # a decoy where each id would lead
        (a / "current_state").write_text("6\n")
        (a / "sessions" / "current_state").write_text("4\n")
        (a / "sessions" / "a" / "b").mkdir(parents=True)
        (a / "sessions" / "a" / "b" / "current_state").write_text("3\n")
        for sid in ("", "..", ".", "../tasks", "a/b"):
            with self.subTest(sid=sid):
                self.pbj.append_hook_run(self.project, hook="x", session_id=sid)
                self.assertIsNone(self.lines()[-1]["task"])             # the session is not known: null
        self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1", task="77")
        self.assertEqual(self.lines()[-1]["task"], "77")               # given: written as given, once digits
        self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1", task="")
        self.assertEqual(self.lines()[-1]["task"], "")                 # given "": known, none
        self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1", task="7a")
        self.assertIsNone(self.lines()[-1]["task"])

    def test_a_linked_or_fifo_pointer_is_not_read(self):
        d = self.project / ".agent" / "sessions" / "pid-1"
        d.mkdir(parents=True)
        (self.root / "t").write_text("7\n")
        (d / "current_state").symlink_to(self.root / "t")
        self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1")
        self.assertIsNone(self.lines()[-1]["task"])
        (d / "current_state").unlink()
        os.mkfifo(d / "current_state")
        t0 = time.monotonic()
        self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1")
        self.assertLess(time.monotonic() - t0, 5)
        self.assertIsNone(self.lines()[-1]["task"])
        (d / "current_state").unlink()
        d.rmdir()
        (self.root / "sd").mkdir()
        (self.root / "sd" / "current_state").write_text("8\n")
        d.symlink_to(self.root / "sd")
        self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1")
        self.assertIsNone(self.lines()[-1]["task"])
        d.unlink()
        self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1")
        self.assertEqual(self.lines()[-1]["task"], "")                 # a known session with no directory: none
        d.mkdir()
        self.pbj.append_hook_run(self.project, hook="x", session_id="pid-1")
        self.assertEqual(self.lines()[-1]["task"], "")                 # its directory, no pointer: none (P24)

    def _fake_clock(self, readings):
        """A stand-in for pb_journal's `time`: each read pops the next (kind, ns) — `w` wall, `m` MONOTONIC, `b`
        BOOTTIME — and the test fails if the sampler reads a kind out of the scripted order."""
        it = iter(readings)

        class T:
            CLOCK_MONOTONIC, CLOCK_BOOTTIME = 1, 7

            @staticmethod
            def time_ns():
                kind, v = next(it)
                assert kind == "w", kind
                return v

            @staticmethod
            def clock_gettime_ns(c):
                kind, v = next(it)
                assert kind == ("m" if c == 1 else "b"), (kind, c)
                return v
        return T

    def test_the_sample_is_one_bracketed_reading(self):
        """codex, opus, sonnet (impl r1): wall, MONOTONIC, BOOTTIME, MONOTONIC, wall — kept only when the two brackets
        are short and agree; `susp` from BOOTTIME minus the bracket's middle, `sts` the second wall read."""
        from unittest import mock
        S = 10 ** 9
        clean = [("w", 1760700000 * S), ("m", 100 * S), ("b", 25673 * S + S // 2), ("m", 100 * S + 2000),
                 ("w", 1760700000 * S + 2000)]
        with mock.patch.object(self.pbj, "time", self._fake_clock(clean)):
            self.assertEqual(self.pbj.suspend_sample()[:2], (25573, 1760700000))
        delay = [("w", 0), ("m", 100 * S), ("b", 25675 * S), ("m", 102 * S), ("w", 2 * S)]       # 2 s awake inside
        slept = [("w", 0), ("m", 100 * S), ("b", 25675 * S), ("m", 100 * S + 1000), ("w", 9 * S)]   # wall jumped
        for name, bad in (("a delay", delay), ("a suspend", slept)):
            with self.subTest(name):
                with mock.patch.object(self.pbj, "time", self._fake_clock(bad + clean)):
                    self.assertEqual(self.pbj.suspend_sample()[:2], (25573, 1760700000))        # retried, then kept
        with mock.patch.object(self.pbj, "time", self._fake_clock(delay * 3)):
            self.assertEqual(self.pbj.suspend_sample()[:2], (None, None))                       # three bad tries

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

    def test_a_short_write_keeps_the_spool_and_rolls_the_journal_back(self):
        """Post-panel judge run 5 (codex — reproduced with RLIMIT_FSIZE): taking a spool ignored a short write, removed
        the spool and left a torn line. Now a short write is rolled back and the spool kept for a later run."""
        self.journal.parent.mkdir()
        self.journal.write_text('{"hook":"before"}\n')
        size = self.journal.stat().st_size
        spool = self.journal.parent / "hooks.jsonl.spool-20261017-000000-1-1"
        spool.write_text('{"hook":"spooled","detail":"' + "x" * 200 + '"}\n')
        script = ("import resource, signal, sys\n"
                  "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
                  f"resource.setrlimit(resource.RLIMIT_FSIZE, ({size + 5}, resource.RLIM_INFINITY))\n"
                  "import importlib.util\n"
                  f"s = importlib.util.spec_from_file_location('pbj', {str(SCRIPTS / 'pb_journal.py')!r})\n"
                  "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
                  "from pathlib import Path\n"
                  f"m._hj_take_spools(Path({str(self.journal.parent)!r}), Path({str(self.journal)!r}))\n")
        subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=30)
        self.assertTrue(spool.exists())                           # kept for a later run
        self.assertEqual(self.journal.stat().st_size, size)       # no torn line left
        self.assertEqual([r["hook"] for r in self.lines()], ["before"])

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


class _Rotating(_Tmp):
    """Helpers for task 259's rotation tests (impl r1 I5: shared, so no class runs another's tests)."""

    def rotate(self):
        os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"] = "1"
        try:
            self.pbj._hj_rotate(self.journal.parent)
        finally:
            del os.environ["PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK"]

    def idle(self, data=b'{"hook":"old"}\n', pid=7):
        t = int(time.time()) - 1200
        p = self.journal.parent / f"hooks.jsonl.archived-20261016-000000-{t}-{pid}"
        p.write_bytes(data)
        os.utime(p, (t, t))
        return p


class TheRotationLeftovers(_Rotating):
    """Task 259 (the owner, 2026-10-19 17:10: "Repar ce a rămas (Recomandat)"): the enforcement journal's protections in
    the hook journal's python rotator — `X` beside `X.gz` repaired, one compressor per archive, and the gzip outside
    the exclusive hold, fsynced and checked whole before the `.gz` and the directory are fsynced and the archive goes."""

    def test_x_beside_its_gz_is_repaired_once_the_gz_holds_it(self):
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 20)
        with gzip.open(str(x) + ".gz", "wb") as f:
            f.write(x.read_bytes())
        self.rotate()
        self.assertFalse(x.exists())
        y = self.idle(b'{"hook":"old"}\n' * 20, pid=8)
        with gzip.open(str(y) + ".gz", "wb") as f:
            f.write(b'{"hook":"old"}\n')                     # a short .gz: the archive stays
        self.rotate()
        self.assertTrue(y.exists())

    def test_a_short_gz_never_replaces_the_archive_and_the_next_try_waits(self):
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 50)
        calls = []
        real = self.pbj._gzip_to

        def short(src, tmp):
            calls.append(src)
            ok = real(src, tmp)
            with gzip.open(tmp, "wb") as f:
                f.write(b'{"hook":"old"}\n')
            return ok
        setattr(self.pbj, "_gzip_to", short)
        try:
            self.rotate()
            self.rotate()
        finally:
            setattr(self.pbj, "_gzip_to", real)
        self.assertTrue(x.exists())
        self.assertFalse(Path(str(x) + ".gz").exists())
        self.assertEqual(len(calls), 1)                         # the failure pushed the next try past the idle time

    def test_the_gzip_runs_outside_the_exclusive_hold_and_is_fsynced(self):
        import fcntl
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 50)
        held, synced = [], []
        real, real_sync = self.pbj._gzip_to, os.fsync

        def probe(src, tmp):
            fd = os.open(str(self.journal.parent), os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)  # an append's lock: free while the gzip runs
                held.append("free")
            except OSError:
                held.append("held")
            finally:
                os.close(fd)
            return real(src, tmp)
        setattr(self.pbj, "_gzip_to", probe)
        os.fsync = lambda fd: (synced.append(os.readlink(f"/proc/self/fd/{fd}")), real_sync(fd))[1]
        try:
            self.rotate()
        finally:
            setattr(self.pbj, "_gzip_to", real)
            os.fsync = real_sync
        self.assertEqual(held, ["free"])
        self.assertTrue(any(".gz.tmp-" in x for x in synced), synced)
        self.assertFalse(x.exists())
        self.assertTrue(Path(str(x) + ".gz").exists())


class TheCompressionProtocol(_Rotating):
    """Task 259's plan r1 (P2-P5): every branch of `_compress_archive` that keeps the archive, and the order of the
    durable steps before it is removed."""

    def test_the_swaps_own_lock_not_had_keeps_the_archive(self):
        """Impl r1 O1 (opus): a reader takes the lock WHILE the gzip runs, so the pass's own lock was had and only the
        swap's can fail — the archive stays, no `.gz`, no temporary."""
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 50)
        real = self.pbj._gzip_to
        holders = []

        def gzip_then_a_reader(src, tmp):
            ok = real(src, tmp)
            holders.append(subprocess.Popen(["flock", "-s", str(self.journal.parent), "sleep", "2.5"]))
            time.sleep(0.3)
            return ok
        setattr(self.pbj, "_gzip_to", gzip_then_a_reader)
        try:
            self.rotate()
        finally:
            setattr(self.pbj, "_gzip_to", real)
            for h in holders:
                h.wait()
        self.assertTrue(holders)
        self.assertTrue(x.exists())
        self.assertEqual(list(self.journal.parent.glob("*.gz*")), [])
        self.assertGreater(x.stat().st_mtime, time.time() - 60)     # I4: the next try waits the idle time again

    def test_the_claim_is_made_inside_the_rotations_hold(self):
        """Impl r1 I3 (sol): the claim scanned for other temporaries, then `_gzip_to` made this one — two rotators
        could both pass in between. The temporary now exists before the gzip starts, made under the hold."""
        self.journal.parent.mkdir()
        self.idle(b'{"hook":"old"}\n' * 50)
        seen = []
        real = self.pbj._gzip_to
        setattr(self.pbj, "_gzip_to", lambda src, tmp, **k: (seen.append(tmp.exists()), real(src, tmp, **k))[1])
        try:
            self.rotate()
        finally:
            setattr(self.pbj, "_gzip_to", real)
        self.assertEqual(seen, [True])

    def test_a_failed_repair_check_waits_the_idle_time_again(self):
        """Impl r1 I4 (sol, sonnet, opus): a `.gz` that does not hold `X` was re-checked on every pass."""
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 50)
        with gzip.open(str(x) + ".gz", "wb") as f:
            f.write(b'{"hook":"other"}\n')
        os.utime(str(x) + ".gz", None)
        calls = []
        real = self.pbj._gz_holds
        setattr(self.pbj, "_gz_holds", lambda gz, src: (calls.append(gz), real(gz, src))[1])
        try:
            self.rotate()
            self.rotate()
        finally:
            setattr(self.pbj, "_gz_holds", real)
        self.assertEqual(len(calls), 1)
        self.assertTrue(x.exists())

    def test_a_pair_that_cannot_be_repaired_does_not_starve_the_next_archive(self):
        """The post-panel judge's run 1 (sol): a failed repair touches `X`, so `X` is newer than its `.gz` and failed the
        repair's check on every pass — and, chosen first every pass, kept every later archive uncompressed."""
        self.journal.parent.mkdir()
        stuck = self.idle(b'{"hook":"old"}\n' * 50, pid=1)
        with gzip.open(str(stuck) + ".gz", "wb") as f:
            f.write(b'{"hook":"other"}\n')
        t = int(time.time()) - 1200
        os.utime(str(stuck) + ".gz", (t - 60, t - 60))          # older than `X`: the pair can never be repaired
        later = self.idle(b'{"hook":"later"}\n' * 50, pid=2)
        self.rotate()
        self.assertTrue(Path(str(later) + ".gz").exists())
        self.assertTrue(stuck.exists())

    def test_a_repair_without_its_lock_is_taken_again_after_the_idle_time(self):
        """PLAN.md "From task 259" (e) (the post-panel judge's run 3, codex Minor; measured on a copy): the backoff of a
        repair whose lock was not had touched `X` alone, so `X` was then newer than its `.gz`, and neither the claim nor
        `_compress_archive` took the pair again — a plain archive beside the `.gz` that holds it, for good."""
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 50)
        gz = Path(str(x) + ".gz")
        with gzip.open(str(gz), "wb") as f:
            f.write(x.read_bytes())
        os.utime(gz, ns=(x.stat().st_atime_ns, x.stat().st_mtime_ns))
        real = self.pbj._gz_holds
        holders = []

        def check_then_a_reader(g, src):
            ok = real(g, src)
            holders.append(subprocess.Popen(["flock", "-s", str(self.journal.parent), "sleep", "2.5"]))
            time.sleep(0.3)
            return ok
        setattr(self.pbj, "_gz_holds", check_then_a_reader)
        try:
            self.rotate()
        finally:
            setattr(self.pbj, "_gz_holds", real)
            for h in holders:
                h.wait()
        self.assertTrue(holders)
        self.assertTrue(x.exists())                                 # the lock not had: kept
        self.assertGreater(x.stat().st_mtime, time.time() - 60)     # and tried again only after the idle time
        self.assertLessEqual(x.stat().st_mtime_ns, gz.stat().st_mtime_ns)
        for p in (x, gz):                                           # the idle time passes: both age alike
            st = os.stat(p)
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns - 1200 * 10**9))
        self.rotate()
        self.assertFalse(x.exists())
        self.assertTrue(gz.exists())

    def test_the_claim_is_made_while_the_rotation_holds_the_lock(self):
        """The post-panel judge's run 1 (Minor): the claim's temporary exists before the gzip, AND it is made while the
        rotation holds the journal directory exclusively."""
        import fcntl
        self.journal.parent.mkdir()
        self.idle(b'{"hook":"old"}\n' * 50)
        seen = []
        real = self.pbj._hj_claim

        def probe(arch):
            fd = os.open(str(self.journal.parent), os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                seen.append("free")
            except OSError:
                seen.append("held")
            finally:
                os.close(fd)
            return real(arch)
        setattr(self.pbj, "_hj_claim", probe)
        try:
            self.rotate()
        finally:
            setattr(self.pbj, "_hj_claim", real)
        self.assertEqual(seen, ["held"])

    def test_a_gz_with_a_bad_crc_beside_x_keeps_x(self):
        """Impl r1 I1's class in python: a whole payload with a bad CRC — gzip reads every byte, then fails."""
        self.journal.parent.mkdir()
        data = b'{"hook":"old"}\n' * 200
        x = self.idle(data)
        good = gzip.compress(data)
        Path(str(x) + ".gz").write_bytes(good[:-8] + b"\0\0\0\0" + good[-4:])    # CRC zeroed, ISIZE kept
        self.rotate()
        self.assertTrue(x.exists())

    def test_the_gz_and_the_directory_are_fsynced_before_the_archive_goes(self):
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 50)
        events = []
        real_sync, real_unlink = os.fsync, Path.unlink
        os.fsync = lambda fd: (events.append(("fsync", os.readlink(f"/proc/self/fd/{fd}"))), real_sync(fd))[1]

        def unlink(path, *a, **k):
            events.append(("unlink", str(path)))
            return real_unlink(path, *a, **k)
        Path.unlink = unlink
        try:
            self.rotate()
        finally:
            os.fsync, Path.unlink = real_sync, real_unlink
        gone = events.index(("unlink", str(x)))
        before = [e[1] for e in events[:gone] if e[0] == "fsync"]
        self.assertIn(str(x) + ".gz", before)
        self.assertIn(str(self.journal.parent), before)

    def test_a_failed_directory_fsync_keeps_the_archive(self):
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 50)
        real = os.fsync

        def fail_dir(fd):
            if os.path.isdir(f"/proc/self/fd/{fd}"):
                raise OSError(5, "I/O error")
            return real(fd)
        os.fsync = fail_dir
        try:
            self.rotate()
        finally:
            os.fsync = real
        self.assertTrue(x.exists())

    def test_a_gz_with_a_garbled_body_and_a_whole_footer_keeps_the_archive(self):
        """sol (plan r1 P5): the ISIZE check alone would have let it remove a good archive."""
        self.journal.parent.mkdir()
        data = b'{"hook":"old"}\n' * 200
        x = self.idle(data)
        good = gzip.compress(data)
        bad = good[:10] + bytes(len(good) - 18) + good[-8:]   # header and footer whole, body zeroed
        Path(str(x) + ".gz").write_bytes(bad)
        self.rotate()
        self.assertTrue(x.exists())

    def test_one_compressor_per_archive(self):
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 50)
        other = Path(f"{x}.gz.tmp-{os.getppid()}")             # a living process's
        other.write_bytes(b"partial")
        self.rotate()
        self.assertTrue(x.exists() and other.exists())
        other.unlink()
        dead = Path(f"{x}.gz.tmp-999999")                     # a dead one's: removed, and the archive compressed
        dead.write_bytes(b"partial")
        self.rotate()
        self.assertFalse(dead.exists())
        self.assertFalse(x.exists())
        self.assertTrue(Path(str(x) + ".gz").exists())

    def test_a_lock_not_had_keeps_the_archive_and_leaves_no_temporary(self):
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.journal.parent.mkdir()
        x = self.idle(b'{"hook":"old"}\n' * 50)
        holder = subprocess.Popen(["flock", "-s", str(self.journal.parent), "sleep", "2.5"])   # a reader
        try:
            time.sleep(0.2)
            self.rotate()
            self.assertTrue(x.exists())
            self.assertEqual(list(self.journal.parent.glob("*.gz*")), [])
        finally:
            holder.wait()

    def test_a_repair_without_its_lock_waits_the_idle_time_again(self):
        """The post-panel judge's run 2 (Minor): the backoff covers a repair whose lock is not had too."""
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.journal.parent.mkdir()
        data = b'{"hook":"old"}\n' * 50
        x = self.idle(data)
        with gzip.open(str(x) + ".gz", "wb") as f:
            f.write(data)
        holders, real = [], self.pbj._gz_holds

        def a_reader_comes(gz, arch):       # after the claim's hold, before the repair's: a reader takes the lock
            holders.append(subprocess.Popen(["flock", "-s", str(self.journal.parent), "sleep", "2.5"]))
            time.sleep(0.2)
            return real(gz, arch)
        self.pbj._gz_holds = a_reader_comes
        try:
            self.rotate()
            self.assertEqual(len(holders), 1)
            self.assertTrue(x.exists())
            self.assertGreater(x.stat().st_mtime, time.time() - 60)
        finally:
            self.pbj._gz_holds = real
            for h in holders:
                h.wait()

    def test_one_compression_a_pass(self):
        self.journal.parent.mkdir()
        self.idle(pid=1)
        self.idle(pid=2)
        self.rotate()
        self.assertEqual(len(list(self.journal.parent.glob("*.gz"))), 1)
        self.rotate()
        self.assertEqual(len(list(self.journal.parent.glob("*.gz"))), 2)


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
        # the sample is the python process's own (task 234): its shape here, its value in ThePythonWriter
        self.assertIsInstance(recs[1].pop("susp"), int)
        self.assertIsInstance(recs[1].pop("sts"), int)
        self.assertRegex(recs[1].pop("boot"), r"^[0-9a-f]{8}$")
        for r in (recs[0], recs[2]):
            self.assertEqual((r.pop("susp"), r.pop("sts"), r.pop("boot")), (None, None, None))   # bash: none was taken
        # `task`: the lib and python know no session here (null), the nudge knows pid-1 with no pointer ("") — held to
        # one answer by test_the_same_task_section_and_sample
        strip = [{k: v for k, v in r.items() if k not in ("ts", "ms", "hook", "session_id", "task")} for r in recs]
        self.assertEqual(strip[0], strip[1])
        self.assertEqual(strip[2].pop("detail"), "no nudge")        # the nudge says what it did; the rest is the same
        strip[0].pop("detail")
        self.assertEqual(strip[0], strip[2])

    def test_the_nudge_reads_only_a_one_component_id_and_no_link(self):
        p = self.root / "nudtask"
        a = p / ".agent"
        (a / "tasks").mkdir(parents=True)
        (a / "tasks" / "current_state").write_text("5\n")                  # where `../tasks` would lead
        (a / "sessions" / "pid-1").mkdir(parents=True)
        (self.root / "t").write_text("7\n")
        (a / "sessions" / "pid-1" / "current_state").symlink_to(self.root / "t")
        for sid in ("../tasks", "pid-1"):
            with self.subTest(sid=sid):
                subprocess.run([bash_or_skip(), str(NUDGE)], input=self.PAYLOAD, cwd=str(p),
                               env=self.env(PLAYBOOK_SESSION_ID=sid), capture_output=True, text=True, timeout=20)
                self.assertIsNone(self.lines(a / "journal" / "hooks.jsonl")[-1]["task"])

    def test_the_same_task_section_and_sample(self):
        """Task 234: the three writers read the session's task from the same pointer, and write a section and a
        sample given to them the same way."""
        p = self.root / "same234"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (p / ".agent" / "sessions" / "pid-1").mkdir(parents=True)
        (p / ".agent" / "sessions" / "pid-1" / "current_state").write_text("234\n")
        script = ('_PB_HJ_T0=$EPOCHREALTIME\nsource "$1"\npb_hook_begin parity\nINPUT=$(cat)\n'
                  'PROJECT_DIR="$2"; SESSION_ID=pid-1\n'
                  'PB_HJ_SECTION=\'Work "Plan"\'; PB_HJ_SUSP=25573; PB_HJ_STS=1760700000; PB_HJ_BOOT=0a1b2c3d\nexit 0\n')
        subprocess.run([bash_or_skip(), "-c", script, "_", str(SCRIPTS / "gate-echo-lib.sh"), str(p)],
                       input=self.PAYLOAD, cwd=str(p), env=self.env(), capture_output=True, text=True, timeout=20)
        self.pbj.append_hook_run(p, hook="parity", event="PostToolUse", tool="Bash", tool_use_id="toolu_P",
                                 session_id="pid-1", host_session_id="h-1", decision="ok", ms=1, exit_code=0,
                                 section='Work "Plan"', susp=25573, sts=1760700000, boot="0a1b2c3d")
        self.nudge(p)
        recs = self.lines(p / ".agent" / "journal" / "hooks.jsonl")
        self.assertEqual(len(recs), 3)
        self.assertEqual([r["task"] for r in recs], ["234"] * 3)
        want = ('Work "Plan"', 25573, 1760700000, "0a1b2c3d")
        self.assertEqual([(r["section"], r["susp"], r["sts"], r["boot"]) for r in recs[:2]], [want, want])
        self.assertEqual((recs[2]["section"], recs[2]["susp"], recs[2]["sts"], recs[2]["boot"]), (None, None, None, None))
        strip = [{k: v for k, v in r.items() if k not in ("ts", "ms", "hook")} for r in recs[:2]]
        self.assertEqual(strip[0], strip[1])


if __name__ == "__main__":
    unittest.main()
