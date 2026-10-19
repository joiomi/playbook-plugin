"""The enforcement journal's rotation (task 257; the owner, 2026-10-19: "Da, în Faza B (Recomandat)").

`journal/enforcement.jsonl` is rotated like `hooks.jsonl`: past 20 MB it is renamed to an archive, compressed once idle,
every archive kept, and the new live file starts with a horizon line and each transcript's newest read position (the
agent recorder's), so the recorder's backward scan and the close's read stay bounded for a project used for years.
Every append holds a shared lock on `journal/enforcement.lock` (the owner's "Lacăt la fiecare rând"); past a 1 s wait a
record goes to a spool ("Bilet în cutie"), never appended unlocked.

The REAL scripts run in temp projects. Run: python3 -m unittest tests.test_enforcement_rotation
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project = self.root / "proj"
        self.lane = self.project / ".agent"
        (self.lane / "tasks").mkdir(parents=True)
        self.jdir = self.lane / "journal"
        self.live = self.jdir / "enforcement.jsonl"
        self.lock = self.jdir / "enforcement.lock"
        self.pbj = _load("pb_journal_t257", "pb_journal.py")

    def records(self) -> "list[dict]":
        if not self.live.exists():
            return []
        return [json.loads(x) for x in self.live.read_text(encoding="utf-8").splitlines() if x.strip()]

    def spools(self) -> "list[Path]":
        return sorted(self.jdir.glob("enforcement.jsonl.spool-*"))

    def hold(self, seconds: float, mode: str = "-x") -> subprocess.Popen:
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.jdir.mkdir(exist_ok=True)
        holder = subprocess.Popen(["flock", mode, str(self.lock), "sleep", str(seconds)])
        time.sleep(0.2)
        return holder


class TheWritersHoldTheLock(_Tmp):
    """W1: the python writer's every append to enforcement.jsonl holds `enforcement.lock` shared."""

    def test_an_append_waits_while_the_lock_is_held_exclusively(self):
        holder = self.hold(0.8)
        try:
            t0 = time.monotonic()
            self.pbj.append(self.lane, "task-gate", "allow", "x")
            waited = time.monotonic() - t0
        finally:
            holder.wait()
        self.assertGreaterEqual(waited, 0.45)
        self.assertEqual([r["reason"] for r in self.records()], ["x"])
        self.assertEqual(self.spools(), [])

    def test_past_one_second_the_record_goes_to_a_spool_never_unlocked(self):
        holder = self.hold(1.8)
        try:
            self.pbj.append(self.lane, "task-gate", "block", "spooled", session_id="pid-1", task="7")
            self.assertEqual(self.records(), [])
            self.assertEqual(len(self.spools()), 1)
        finally:
            holder.wait()
        rec = json.loads(self.spools()[0].read_text(encoding="utf-8"))
        self.assertEqual((rec["hook"], rec["reason"], rec["task"]), ("task-gate", "spooled", "7"))
        self.assertEqual(rec["spool"], self.spools()[0].name)          # its identity through the merge (P2)
        self.assertEqual(list(self.jdir.glob("enforcement.jsonl.spooling-*")), [])

    def test_the_clis_records_wait_five_seconds_a_hooks_one(self):
        """Plan r1 P3: a CLI record (a transition, a verify run, a review's spend, a close) is not spooled for a hold of
        a few seconds — a spool is placed only by its second, the file order within it lost."""
        holder = self.hold(2.5)
        try:
            self.pbj.append(self.lane, "task-gate", "allow", "hook-record")
            t0 = time.monotonic()
            self.pbj.append_lifecycle(self.lane, event="done", task="7")
            waited = time.monotonic() - t0
        finally:
            holder.wait()
        self.assertGreater(waited, 0.8)
        self.assertEqual([r["reason"] for r in self.records()], ["done"])
        self.assertEqual([json.loads(p.read_text())["reason"] for p in self.spools()], ["hook-record"])

    def test_a_shared_holder_does_not_stop_an_append(self):
        holder = self.hold(1.5, "-s")
        try:
            t0 = time.monotonic()
            self.pbj.append_verify(self.lane, task="7", command="true", rc=0, seconds=1.0)
            self.assertLess(time.monotonic() - t0, 0.5)
        finally:
            holder.wait()
        self.assertEqual([r["hook"] for r in self.records()], ["verify"])

    def test_the_lock_file_is_made_inside_the_journal(self):
        self.pbj.append(self.lane, "task-gate", "block", "x")
        self.assertTrue(self.lock.is_file() and not self.lock.is_symlink())
        self.assertEqual(len(self.records()), 1)

    def test_a_linked_or_odd_lock_writes_nothing(self):
        self.jdir.mkdir()
        target = self.root / "elsewhere"
        target.write_text("")
        self.lock.symlink_to(target)
        self.pbj.append(self.lane, "task-gate", "block", "linked")
        self.assertEqual((self.records(), self.spools()), ([], []))
        self.lock.unlink()
        os.mkfifo(self.lock)
        self.pbj.append(self.lane, "task-gate", "block", "fifo")
        self.assertEqual((self.records(), self.spools()), ([], []))

    def test_an_agent_usage_span_is_written_under_the_lock(self):
        import fcntl
        seen = []
        real = os.write

        def probe(fd, data):
            if b'"agent-usage"' in bytes(data):
                lk = os.open(str(self.lock), os.O_RDWR)
                try:
                    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    seen.append("unlocked")
                except OSError:
                    seen.append("locked")
                finally:
                    os.close(lk)
            return real(fd, data)
        self.pbj.os.write = probe
        try:
            self.pbj.append_agent_usage_span(self.lane, [dict(task="7", file="ab" * 8, ino=1, span_from=0, span_to=10,
                                                              head="0" * 8, model="m", calls=1, counts={"in": 1})])
        finally:
            self.pbj.os.write = real
        self.assertEqual(seen, ["locked"])
        self.assertEqual(len(self.records()), 1)

    def test_the_hook_journal_does_not_take_the_enforcement_lock(self):
        self.pbj._write_record(self.lane, {"hook": "x"}, name="hooks.jsonl")
        self.assertFalse(self.lock.exists())
        self.assertTrue((self.jdir / "hooks.jsonl").is_file())


class TheBashWriterHoldsTheLock(_Tmp):
    """W2: `gate-echo-lib.sh::journal_enforcement` holds the same lock shared, spools past 1 s, and appends directly
    only where the host has no `flock` (nothing can take a spool there) — the hook journal's rules."""

    def bash(self, reason: str, path_env: "str | None" = None) -> subprocess.CompletedProcess:
        from tests._bashcheck import bash_or_skip
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "PB_", "_PB_"))}
        if path_env is not None:
            env["PATH"] = path_env
        return subprocess.run([bash_or_skip(), "-c", f'source "{SCRIPTS / "gate-echo-lib.sh"}"; '
                               'journal_enforcement "$1" task-gate block "$2" pid-1', "x", str(self.lane), reason],
                              env=env, capture_output=True, text=True, timeout=30)

    def test_an_append_waits_for_the_exclusive_holder(self):
        holder = self.hold(0.8)
        try:
            t0 = time.monotonic()
            r = self.bash("waited")
            waited = time.monotonic() - t0
        finally:
            holder.wait()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreaterEqual(waited, 0.45)
        self.assertEqual([x["reason"] for x in self.records()], ["waited"])

    def test_past_one_second_a_spool_never_unlocked(self):
        holder = self.hold(1.8)
        try:
            r = self.bash("spooled")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.records(), [])
            self.assertEqual(len(self.spools()), 1)
        finally:
            holder.wait()
        rec = json.loads(self.spools()[0].read_text(encoding="utf-8"))
        self.assertEqual((rec["hook"], rec["decision"], rec["reason"]), ("task-gate", "block", "spooled"))
        self.assertEqual(list(self.jdir.glob("enforcement.jsonl.spooling-*")), [])

    def test_with_no_flock_on_the_host_every_record_is_spooled(self):
        """Plan r1 P5 (astra, sol): an unlocked append there could land after python compressed the file it held open;
        a spool is a file of its own, which python takes."""
        tools = self.root / "bin"
        tools.mkdir()
        for t in ("date", "mkdir", "mv", "rm", "cat"):
            w = shutil.which(t)
            if w:
                (tools / t).symlink_to(w)
        r = self.bash("no-flock", path_env=str(tools))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.records(), [])
        [spool] = self.spools()
        rec = json.loads(spool.read_text(encoding="utf-8"))
        self.assertEqual((rec["reason"], rec["spool"]), ("no-flock", spool.name))
        self.pbj.rotate_enforcement(self.lane)                    # python takes it
        self.assertEqual([x["reason"] for x in self.records()], ["no-flock"])
        self.assertEqual(self.spools(), [])

    def test_a_bash_spool_carries_its_id(self):
        holder = self.hold(1.8)
        try:
            self.bash("tagged")
        finally:
            holder.wait()
        [spool] = self.spools()
        self.assertEqual(json.loads(spool.read_text(encoding="utf-8"))["spool"], spool.name)

    def test_a_background_child_of_the_hook_does_not_keep_the_lock(self):
        """Sonnet (plan r1): a descriptor left open would let a long-lived child hold the lock shared, and no rotation
        could take it exclusively again."""
        import fcntl
        from tests._bashcheck import bash_or_skip
        r = subprocess.Popen([bash_or_skip(), "-c", f'source "{SCRIPTS / "gate-echo-lib.sh"}"; '
                              'journal_enforcement "$1" task-gate block x pid-1; sleep 3 & wait', "x", str(self.lane)])
        try:
            time.sleep(0.8)
            fd = os.open(str(self.lock), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)        # raises if a child still holds it shared
            finally:
                os.close(fd)
        finally:
            r.wait()

    def test_a_linked_or_odd_lock_writes_nothing(self):
        self.jdir.mkdir()
        target = self.root / "elsewhere"
        target.write_text("")
        self.lock.symlink_to(target)
        self.assertEqual(self.bash("linked").returncode, 0)
        self.lock.unlink()
        os.mkfifo(self.lock)                    # a FIFO must not hang the writer (the sink rule)
        t0 = time.monotonic()
        self.assertEqual(self.bash("fifo").returncode, 0)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual((self.records(), self.spools()), ([], []))
        self.assertEqual(target.read_text(), "")

    def test_python_and_bash_spool_alike(self):
        holder = self.hold(3.5)                 # both writers wait their second inside it
        try:
            self.bash("from-bash")
            self.pbj.append(self.lane, "task-gate", "block", "from-python", session_id="pid-1")
        finally:
            holder.wait()
        got = sorted(json.loads(p.read_text(encoding="utf-8"))["reason"] for p in self.spools())
        self.assertEqual(got, ["from-bash", "from-python"])
        for p in self.spools():
            self.assertRegex(p.name, r"^enforcement\.jsonl\.spool-\d{8}-\d{6}-\d+-\d+$")


CARRY, HZ = "read position carried", "read positions horizon"     # the rotation's two reasons


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _position(key: str, to: int, ts: float, task: str = "7", **extra) -> dict:
    rec = {"ts": _iso(ts), "session_id": "pid-1", "hook": "agent-usage", "decision": "record", "reason": "agent usage",
           "task": task, "file": key, "ino": "11", "from": 0, "to": to, "head": "abcd0123", "last_id": f"msg_{to}",
           "model": "m", "side": 0, "calls": 1, "in": 1, "out": 1, "cache_read": 0, "cache_write": 0, "thinking": 0}
    rec.update(extra)
    return rec


def _line(rec: dict) -> bytes:
    return (json.dumps(rec, separators=(",", ":")) + "\n").encode()


class _Journal(_Tmp):
    LIMIT = 4096

    def setUp(self):
        super().setUp()
        self.jdir.mkdir()
        self.now = time.time()

    def write(self, *recs: dict) -> None:
        with open(self.live, "ab") as f:
            for r in recs:
                f.write(_line(r))

    def pad(self, n: int = LIMIT + 1) -> None:
        """Decision lines until the live file passes `n` bytes."""
        i = 0
        while not self.live.exists() or self.live.stat().st_size <= n:
            self.write({"ts": _iso(self.now), "session_id": "pid-1", "hook": "task-gate", "decision": "allow",
                        "reason": f"pad {i}"})
            i += 1

    def rotate(self, held: bool = False) -> str:
        return self.pbj.rotate_enforcement(self.lane, held=held, limit=self.LIMIT)

    def archives(self) -> "list[Path]":
        """The closed files, plain or compressed (not their `.tasks` index)."""
        return sorted(p for p in self.jdir.glob("enforcement.jsonl.archived-*") if not p.name.endswith(".tasks"))


class TheRotation(_Journal):
    """W3: past the limit the live file is renamed to an archive, and the new one starts with a horizon line and each
    transcript's newest read position — the recorder's next read of every transcript is the same as before."""

    def test_below_the_limit_nothing_moves(self):
        self.write(_position("a" * 16, 10, self.now))
        before = self.live.read_bytes()
        self.rotate()
        self.assertEqual(self.archives(), [])
        self.assertEqual(self.live.read_bytes(), before)

    def test_past_the_limit_the_file_is_archived_whole_and_the_positions_carried(self):
        a, b = "a" * 16, "b" * 16
        self.write(_position(a, 10, self.now - 50), _position(b, 20, self.now - 40, task="8"))
        self.pad()
        self.write(_position(a, 30, self.now - 10, last_id="msg_last"))
        old = self.live.read_bytes()
        self.rotate()
        arch = self.archives()
        self.assertEqual(len(arch), 1)
        self.assertRegex(arch[0].name, r"^enforcement\.jsonl\.archived-\d{8}-\d{6}-\d{1,12}-\d+$")
        self.assertEqual(arch[0].read_bytes(), old)                  # every line, byte for byte
        recs = self.records()
        self.assertEqual((recs[0]["decision"], recs[0]["reason"]), ("record", HZ))
        self.assertEqual(recs[0]["hook"], "agent-usage")
        carries = {r["file"]: r for r in recs[1:]}
        self.assertEqual(set(carries), {a, b})
        self.assertTrue(all(r["decision"] == "record" and r["reason"] == CARRY and r["hook"] == "agent-usage" for r in recs[1:]))
        self.assertEqual((carries[a]["to"], carries[a]["last_id"], carries[a]["ino"], carries[a]["head"]),
                         (30, "msg_last", "11", "abcd0123"))
        self.assertEqual((carries[b]["to"], carries[b]["task"], carries[b]["of"]), (20, "8", _iso(self.now - 40)))
        self.assertLess(self.live.stat().st_size, self.LIMIT)

    def test_a_seed_is_a_position_and_a_marker_is_not(self):
        s = "c" * 16
        self.write(_position(s, 99, self.now - 5, status="unknown", why="seed"),
                   {"ts": _iso(self.now), "session_id": "pid-1", "hook": "agent-usage", "decision": "record",
                    "reason": "agent usage", "task": "7", "status": "unknown", "why": "busy lock"})
        self.pad()
        self.rotate()
        carries = [r for r in self.records() if r.get("reason") == CARRY]
        self.assertEqual([(r["file"], r["to"]) for r in carries], [(s, 99)])

    def test_positions_older_than_thirty_days_are_dropped_and_the_horizon_says_so(self):
        old, new = "d" * 16, "e" * 16
        self.write(_position(old, 5, self.now - 31 * 86400), _position(new, 6, self.now - 29 * 86400))
        self.pad()
        self.rotate()
        recs = self.records()
        self.assertEqual([r["file"] for r in recs if r.get("reason") == CARRY], [new])
        hz = recs[0]["horizon"]
        self.assertAlmostEqual(_epoch(hz), self.now - 30 * 86400, delta=5)

    def test_a_carry_keeps_the_time_of_its_record_and_is_pruned_by_it(self):
        k, j = "f" * 16, "9" * 16
        self.write({"ts": _iso(self.now), "session_id": "", "hook": "agent-usage", "decision": "record",
                    "reason": HZ, "horizon": _iso(self.now - 40 * 86400)},
                   dict(_position(k, 7, self.now), reason=CARRY, of=_iso(self.now - 31 * 86400)),
                   dict(_position(j, 8, self.now), reason=CARRY, of=_iso(self.now - 2 * 86400)))
        self.pad()
        self.rotate()
        carries = [r for r in self.records() if r.get("reason") == CARRY]
        self.assertEqual([(r["file"], r["of"]) for r in carries], [(j, _iso(self.now - 2 * 86400))])

    def test_the_horizon_never_moves_back(self):
        later = _iso(self.now + 86400)              # a horizon written before the clock was set back
        self.write({"ts": _iso(self.now), "session_id": "", "hook": "agent-usage", "decision": "record",
                    "reason": HZ, "horizon": later})
        self.pad()
        self.rotate()
        self.assertEqual(self.records()[0]["horizon"], later)

    def test_a_spool_is_merged_first_and_its_position_carried(self):
        k = "1" * 16
        self.write(_position(k, 10, self.now - 30))
        self.pad()
        spool = self.jdir / "enforcement.jsonl.spool-20261019-000000-1-1"
        spool.write_bytes(_line(_position(k, 50, self.now - 1)))
        self.rotate()
        self.assertFalse(spool.exists())
        self.assertIn(b'"to":50', self.archives()[0].read_bytes())
        self.assertEqual([r["to"] for r in self.records() if r.get("reason") == CARRY], [50])

    def test_lines_written_after_the_pre_scan_are_scanned_under_the_lock(self):
        k = "2" * 16
        self.write(_position(k, 10, self.now - 30))
        self.pad()
        pre = self.pbj._ej_prescan(self.live)
        self.write(_position(k, 77, self.now))                         # lands between the pre-scan and the swap
        self.pbj._ej_rotate_locked(self.jdir, self.live, self.LIMIT, pre)
        self.assertEqual([r["to"] for r in self.records() if r.get("reason") == CARRY], [77])

    def test_a_file_replaced_since_the_pre_scan_is_not_rotated(self):
        self.pad()
        pre = self.pbj._ej_prescan(self.live)
        data = self.live.read_bytes()
        self.live.rename(self.jdir / "kept-aside")                     # kept: Linux would hand its inode out again
        self.live.write_bytes(data)                                    # another inode, as after another rotation
        self.pbj._ej_rotate_locked(self.jdir, self.live, self.LIMIT, pre)
        self.assertEqual(self.archives(), [])

    def test_unheld_it_takes_the_recorders_lock_and_skips_when_busy(self):
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        self.pad()
        holder = subprocess.Popen(["flock", "-x", str(self.jdir / "agent-usage.lock"), "sleep", "1.5"])
        try:
            time.sleep(0.2)
            said = self.rotate()
            self.assertEqual(self.archives(), [])
            self.assertIn("busy", said)
        finally:
            holder.wait()
        self.rotate()
        self.assertEqual(len(self.archives()), 1)

    def test_held_it_runs_under_the_callers_lock(self):
        import fcntl
        self.pad()
        fd = os.open(str(self.jdir / "agent-usage.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.rotate(held=True)
        finally:
            os.close(fd)
        self.assertEqual(len(self.archives()), 1)

    def test_a_spool_is_merged_on_any_pass_below_the_limit(self):
        """Plan r1 P1 (agy Critical; opus, sonnet): not only inside a rotation past 20 MB."""
        self.write({"ts": _iso(self.now - 5), "hook": "task-gate", "reason": "first"})
        spool = self.jdir / "enforcement.jsonl.spool-20261019-000000-1-1"
        spool.write_bytes(_line({"ts": _iso(self.now), "hook": "tasks", "reason": "done", "task": "7",
                                 "spool": spool.name}))
        self.rotate()
        self.assertEqual(self.archives(), [])
        self.assertFalse(spool.exists())
        self.assertEqual([r["reason"] for r in self.records()], ["first", "done"])

    def test_a_merge_killed_before_its_unlink_is_not_appended_twice(self):
        """Plan r1 P2 (astra, sol, opus): the spool's id is already in the live file, so the next pass only unlinks it —
        and two distinct events with the same bytes but different ids both stay."""
        name = "enforcement.jsonl.spool-20261019-000000-1-1"
        rec = {"ts": _iso(self.now), "hook": "task-gate", "decision": "allow", "reason": "same"}
        self.write(dict(rec, spool=name))                       # appended by a merge that was then killed
        (self.jdir / name).write_bytes(_line(dict(rec, spool=name)))
        other = "enforcement.jsonl.spool-20261019-000000-2-2"
        (self.jdir / other).write_bytes(_line(dict(rec, spool=other)))
        self.rotate()
        self.assertEqual(self.spools(), [])
        self.assertEqual(sorted(r["spool"] for r in self.records()), [name, other])

    def test_an_exclusive_hold_is_only_taken_under_the_recorders_lock(self):
        """Plan r1 P1: a position is written under `agent-usage.lock`; every exclusive hold of the journal lock is taken
        under it too, so a position can never meet one and be spooled (`_newest` reads only the live file)."""
        import fcntl
        self.pad()
        self.lock.touch()
        (self.jdir / "enforcement.jsonl.spool-20261019-000000-1-1").write_bytes(b'{"hook":"x"}\n')
        seen = []
        real = self.pbj._flock_wait

        def probe(fd, mode, wait):
            if mode == fcntl.LOCK_EX and os.fstat(fd).st_ino == os.stat(self.lock).st_ino:
                au = os.open(str(self.jdir / "agent-usage.lock"), os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(au, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    seen.append("unheld")
                except OSError:
                    seen.append("held")
                finally:
                    os.close(au)
            return real(fd, mode, wait)
        setattr(self.pbj, "_flock_wait", probe)
        try:
            self.rotate()
        finally:
            setattr(self.pbj, "_flock_wait", real)
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"held"})

    def test_no_pre_scan_while_the_journal_cannot_be_had_exclusively(self):
        """Plan r1 P9 (opus, sonnet): a reader holding the lock shared costs the pass nothing but a probe."""
        self.pad()
        calls = []
        real = self.pbj._ej_prescan
        setattr(self.pbj, "_ej_prescan", lambda live: calls.append(live) or real(live))
        holder = self.hold(1.5, "-s")
        try:
            t0 = time.monotonic()
            self.rotate()
            self.assertLess(time.monotonic() - t0, 0.9)
        finally:
            holder.wait()
            setattr(self.pbj, "_ej_prescan", real)
        self.assertEqual((calls, self.archives()), ([], []))

    def test_each_archive_gets_the_list_of_the_tasks_it_names(self):
        """Plan r1 P7 (agy, opus, sol): a task's numbers read only the archives that name it."""
        self.write({"ts": _iso(self.now), "hook": "tasks", "decision": "record", "reason": "work", "task": "7"},
                   _position("a" * 16, 1, self.now, task="12"))
        self.pad()
        self.write({"ts": _iso(self.now), "hook": "verify", "decision": "record", "reason": "verify run",
                    "task": "31"})
        self.rotate()
        [arch] = self.archives()
        side = Path(str(arch) + ".tasks")
        self.assertEqual(json.loads(side.read_text())["tasks"], ["12", "31", "7"])

    def test_a_killed_swap_leaves_no_sidecar_behind(self):
        self.write(_position("a" * 16, 10, self.now))
        twin = self.jdir / f"enforcement.jsonl.archived-20261019-000000-{int(self.now)}-1"
        os.link(self.live, twin)
        Path(str(twin) + ".tasks").write_text('{"tasks":["7"]}')
        orphan = self.jdir / "enforcement.jsonl.archived-20261019-000001-1-1.tasks"
        orphan.write_text('{"tasks":[]}')
        self.rotate()
        self.assertEqual(sorted(p.name for p in self.jdir.glob("*.tasks")), [])

    def test_it_never_raises(self):
        shutil.rmtree(self.jdir)
        self.jdir.write_text("not a directory")
        self.assertEqual(self.rotate(), "")
        self.assertEqual(self.pbj.rotate_enforcement(self.root / "nowhere", limit=1), "")


class RepairAndCompression(_Journal):
    """W4: a rotation killed half-way is repaired by the next pass; an archive is compressed only once idle — by the
    rename time its name records and by its mtime — outside the lock, and swapped in under it only if unchanged."""

    def archive(self, age: float, data: bytes = b'{"hook":"x"}\n', mtime_age: "float | None" = None,
                pid: int = 1) -> Path:
        t = int(self.now - age)
        p = self.jdir / f"enforcement.jsonl.archived-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(t))}-{t}-{pid}"
        p.write_bytes(data)
        m = self.now - (age if mtime_age is None else mtime_age)
        os.utime(p, (m, m))
        return p

    def test_a_swap_killed_after_its_link_is_undone(self):
        self.write(_position("a" * 16, 10, self.now))
        twin = self.jdir / f"enforcement.jsonl.archived-20261019-000000-{int(self.now)}-1"
        os.link(self.live, twin)                        # the link made, the rename not
        before = self.live.read_bytes()
        self.rotate()
        self.assertFalse(twin.exists())
        self.assertEqual(self.live.read_bytes(), before)

    def test_a_dead_rotators_carry_file_is_removed(self):
        self.write(_position("a" * 16, 10, self.now))
        stale = self.jdir / "enforcement.jsonl.carry-999999-1"
        stale.write_bytes(b"x\n")
        self.rotate()
        self.assertFalse(stale.exists())

    def test_only_an_idle_archive_is_compressed_and_its_bytes_kept(self):
        import gzip
        fresh = self.archive(60)
        named_old_written_now = self.archive(3600, mtime_age=60, pid=2)
        idle = self.archive(3600, data=b'{"hook":"idle"}\n' * 100, pid=3)
        self.rotate()
        self.assertTrue(fresh.exists() and named_old_written_now.exists())
        self.assertFalse(idle.exists())
        with gzip.open(str(idle) + ".gz", "rb") as f:
            self.assertEqual(f.read(), b'{"hook":"idle"}\n' * 100)
        self.assertEqual(list(self.jdir.glob("*.gz.tmp-*")), [])

    def test_an_archive_written_while_it_was_compressed_is_kept_for_later(self):
        p = self.archive(3600)
        real = self.pbj._gzip_to

        def late(src, tmp):
            ok = real(src, tmp)
            with open(src, "ab") as f:
                f.write(b'{"hook":"late"}\n')     # a host with no flock appended meanwhile
            return ok
        setattr(self.pbj, "_gzip_to", late)
        try:
            self.rotate()
        finally:
            setattr(self.pbj, "_gzip_to", real)
        self.assertTrue(p.exists())
        self.assertFalse(Path(str(p) + ".gz").exists())
        self.assertIn(b'"late"', p.read_bytes())

    def test_a_reader_holding_the_lock_keeps_the_archive_in_place(self):
        p = self.archive(3600)
        holder = self.hold(3.5, "-s")           # past both 1 s waits of the pass
        try:
            self.rotate()
            self.assertTrue(p.exists())
            self.assertFalse(Path(str(p) + ".gz").exists())
            self.assertEqual(list(self.jdir.glob("*.gz.tmp-*")), [])
        finally:
            holder.wait()
        self.rotate()
        self.assertFalse(p.exists())
        self.assertTrue(Path(str(p) + ".gz").exists())

    def test_the_compression_itself_waits_for_a_reader_between_the_probe_and_the_swap(self):
        """W9's mutant M6: the pass's probe let go of the lock at once — a reader can take it before the compression's
        swap, which must then hold back on its own exclusive try."""
        p = self.archive(3600)
        holder = self.hold(1.5, "-s")
        try:
            self.pbj._ej_compress_idle(self.jdir)
            self.assertTrue(p.exists())
            self.assertFalse(Path(str(p) + ".gz").exists())
            self.assertEqual(list(self.jdir.glob("*.gz.tmp-*")), [])
        finally:
            holder.wait()

    def test_a_swap_killed_after_the_gz_link_drops_the_archive_once_older(self):
        import gzip
        p = self.archive(3600)
        gz = Path(str(p) + ".gz")
        with gzip.open(gz, "wb") as f:
            f.write(p.read_bytes())
        self.rotate()
        self.assertFalse(p.exists())
        self.assertTrue(gz.exists())

    def test_a_gz_of_other_bytes_does_not_drop_the_archive(self):
        """Plan r1 P4: the plain file goes only when the `.gz` holds as many bytes (its ISIZE) and is not older."""
        import gzip
        p = self.archive(3600, data=b'{"hook":"whole"}\n' * 10)
        gz = Path(str(p) + ".gz")
        with gzip.open(gz, "wb") as f:
            f.write(b'{"hook":"cut"}\n')
        self.rotate()
        self.assertTrue(p.exists())

    def test_a_dead_compressors_temporary_goes_a_live_ones_stays(self):
        p = self.archive(60)
        dead = self.jdir / (p.name + ".gz.tmp-999999")
        alive = self.jdir / (p.name + f".gz.tmp-{os.getpid()}")
        dead.write_bytes(b"x")
        alive.write_bytes(b"x")
        self.rotate()
        self.assertFalse(dead.exists())
        self.assertTrue(alive.exists())

    def test_a_writer_that_opened_before_the_rename_loses_nothing(self):
        """The hook journal's three grave findings: a line written on a descriptor held across the rename was lost to
        an immediate compression. Here it lands in the archive, which stays uncompressed while not idle."""
        import gzip
        self.pad()
        fd = os.open(str(self.live), os.O_WRONLY | os.O_APPEND)
        try:
            self.rotate()
            os.write(fd, b'{"hook":"held-late"}\n')
        finally:
            os.close(fd)
        arch = self.archives()[0]
        self.rotate()                                   # a pass right away: the archive is not idle
        self.assertTrue(arch.exists())
        self.assertIn(b"held-late", arch.read_bytes())
        old = int(self.now - 3600)                      # its name aged; its mtime still the late write's
        aged = self.jdir / f"enforcement.jsonl.archived-20261019-000000-{old}-1"
        arch.rename(aged)
        self.rotate()
        self.assertTrue(aged.exists())
        os.utime(aged, (self.now - 3600, self.now - 3600))
        self.rotate()
        with gzip.open(str(aged) + ".gz", "rb") as f:
            self.assertIn(b"held-late", f.read())


class StressRealProcesses(_Journal):
    """The stop gate after the core: real bash and python writers and a recorder writing positions, against a rotator
    that rotates at 2 KiB and compresses at once (the 10-minute idle rule switched OFF: the lock alone must keep every
    line). At the end every record is there exactly once — archives, live file and spools, carries aside, one line per
    spool id — and each transcript's newest position is the recorder's last."""

    BASH_WRITERS, BASH_EACH = 4, 50
    PY_WRITERS, PY_EACH = 3, 80
    POSITIONS, KEYS = 120, 4

    def test_every_record_lands_exactly_once(self):
        import gzip
        import sys
        from tests._bashcheck import bash_or_skip
        if not shutil.which("flock"):
            self.skipTest("no flock(1)")
        stop = self.root / "stop"
        boot = f"import importlib.util as u, sys\ns = u.spec_from_file_location('pbj', {str(SCRIPTS / 'pb_journal.py')!r})\n" \
               "m = u.module_from_spec(s); s.loader.exec_module(m)\n"
        rotator = subprocess.Popen([sys.executable, "-c", boot + (
            f"import os, time\nm._EJ_IDLE_S = -1\n"
            f"while not os.path.exists({str(stop)!r}):\n"
            f"    m.rotate_enforcement({str(self.lane)!r}, limit=2048)\n")])
        procs = []
        for i in range(self.BASH_WRITERS):
            procs.append(subprocess.Popen([bash_or_skip(), "-c", f'source "{SCRIPTS / "gate-echo-lib.sh"}"; '
                                           f'for j in $(seq {self.BASH_EACH}); do '
                                           f'journal_enforcement "$1" task-gate allow "b{i}-$j" pid-1; sleep 0.01; done',
                                           "x", str(self.lane)]))
        for i in range(self.PY_WRITERS):
            procs.append(subprocess.Popen([sys.executable, "-c", boot + (
                f"import time\nfor j in range(1, {self.PY_EACH} + 1):\n"
                f"    m.append({str(self.lane)!r}, 'task-gate', 'allow', f'p{i}-{{j}}')\n"
                f"    time.sleep(0.005)\n")]))
        au = _load("agent_usage_t257", "agent_usage.py")
        recorder = subprocess.Popen([sys.executable, "-c", boot + (
            f"import importlib.util as u2\na = u2.spec_from_file_location('au', {str(SCRIPTS / 'agent_usage.py')!r})\n"
            f"au = u2.module_from_spec(a); a.loader.exec_module(au)\nimport os\n"
            f"for j in range(1, {self.POSITIONS} + 1):\n"
            f"    fd = au._lock({str(self.lane)!r}, 5)\n"
            f"    key = 'k' * 15 + str(j % {self.KEYS})\n"
            f"    m.append_agent_usage_span({str(self.lane)!r}, [dict(task='7', file=key, ino=1, span_from=j - 1,"
            f" span_to=j, head='00000000', model='m', calls=1, counts={{'in': 1}})])\n"
            f"    m.rotate_enforcement({str(self.lane)!r}, held=True, limit=2048)\n"
            f"    os.close(fd)\n"
            f"    __import__('time').sleep(0.004)\n")])
        for p in procs + [recorder]:
            self.assertEqual(p.wait(timeout=300), 0)
        stop.write_text("")
        self.assertEqual(rotator.wait(timeout=60), 0)
        self.pbj.rotate_enforcement(self.lane)                  # a last pass takes what was spooled
        lines = []
        for p in sorted(self.jdir.iterdir()):
            if p.name.endswith(".gz") and "archived-" in p.name:
                with gzip.open(p, "rb") as f:
                    lines += f.read().splitlines()
            elif "archived-" in p.name and not p.name.endswith((".tasks", ".gz")) or p.name == "enforcement.jsonl" \
                    or p.name.startswith("enforcement.jsonl.spool-"):
                lines += p.read_bytes().splitlines()
        recs, ids = [], set()
        for raw in lines:
            r = json.loads(raw)
            if r.get("reason") in (CARRY, HZ):
                continue
            if r.get("spool"):
                if r["spool"] in ids:
                    continue
                ids.add(r["spool"])
            recs.append(r)
        want = sorted([f"b{i}-{j}" for i in range(self.BASH_WRITERS) for j in range(1, self.BASH_EACH + 1)]
                      + [f"p{i}-{j}" for i in range(self.PY_WRITERS) for j in range(1, self.PY_EACH + 1)])
        got = sorted(r["reason"] for r in recs if r.get("hook") == "task-gate")
        self.assertEqual(got, want)
        self.assertEqual(sorted(r["to"] for r in recs if r.get("hook") == "agent-usage"),
                         list(range(1, self.POSITIONS + 1)))
        self.assertGreater(len(self.archives()), 3)               # it did rotate, many times
        keys = {"k" * 15 + str(j % self.KEYS) for j in range(1, self.POSITIONS + 1)}
        newest, complete = au._newest(str(self.live), keys)
        self.assertTrue(complete)
        last = {"k" * 15 + str(j % self.KEYS): j for j in range(1, self.POSITIONS + 1)}
        self.assertEqual({k: r["to"] for k, r in newest.items()}, last)


from tests.test_agent_usage import _Lane as _AULane, entry as _entry, lines as _lines  # noqa: E402


class TheRecorderAcrossARotation(_AULane):
    """W5: the recorder rotates inside its own lock, continues every transcript from its carried position, seeds a
    transcript whose position the horizon pruned — only on a positive old first stamp (plan r1, P6) — and the CLI's
    transitions rotate even with no transcript to read."""

    def setUp(self):
        super().setUp()
        setattr(self.pbj, "_EJ_LIMIT", 4096)
        self.jdir = self.lane / "journal"
        self.now = time.time()

    def pad(self):
        live = self.jdir / "enforcement.jsonl"
        self.jdir.mkdir(exist_ok=True)
        with open(live, "ab") as f:
            while f.tell() <= 4200:
                f.write(_line({"ts": _iso(self.now), "hook": "task-gate", "decision": "allow", "reason": "pad"}))

    def archives(self):
        return sorted(p for p in self.jdir.glob("enforcement.jsonl.archived-*") if not p.name.endswith(".tasks"))

    def stamped(self, mid: str, t: float) -> dict:
        e = _entry(mid)
        e["timestamp"] = _iso(t)
        return e

    def test_a_recorded_transcript_continues_from_its_carried_position(self):
        self.main.write_bytes(_lines(_entry("m1"), _entry("m2")))
        self.record()
        first = self.records()[-1]
        self.pad()
        with open(self.main, "ab") as f:
            f.write(_lines(_entry("m3")))
        self.record(task="8")
        self.assertEqual(len(self.archives()), 1)                 # rotated inside the recorder's lock
        live = self.records()
        self.assertEqual([r["reason"] for r in live], [HZ, CARRY, "agent usage"])
        self.assertEqual((live[-1]["calls"], live[-1]["from"], live[-1]["task"]), (1, first["to"], "8"))

    def test_a_pruned_transcript_begun_before_the_horizon_is_seeded_never_read_again(self):
        old = self.now - 40 * 86400
        self.main.write_bytes(_lines(self.stamped("m1", old), self.stamped("m2", old)))
        self.record()
        live = self.jdir / "enforcement.jsonl"
        rec = json.loads(live.read_text().splitlines()[-1])
        rec["ts"] = _iso(self.now - 31 * 86400)                    # its position is now older than the horizon
        live.write_text(json.dumps(rec) + "\n")
        self.pad()
        with open(self.main, "ab") as f:
            f.write(_lines(self.stamped("m3", self.now)))
        self.record(task="8")
        live_recs = [r for r in self.records() if r.get("reason") != HZ]
        self.assertEqual(len(live_recs), 1)
        self.assertEqual(live_recs[0].get("status"), "unknown")
        self.assertIn("horizon", live_recs[0]["why"])
        self.assertEqual(live_recs[0]["to"], self.main.stat().st_size)

    def test_a_transcript_begun_after_the_horizon_is_read_from_its_start(self):
        other = self.tdir / "sess-0.jsonl"
        other.write_bytes(_lines(_entry("o1")))
        self.record(paths=[other])
        self.pad()
        self.record(paths=[other])                                 # rotates: the live file now has a horizon
        self.assertEqual(len(self.archives()), 1)
        self.main.write_bytes(_lines(self.stamped("m1", self.now), self.stamped("m2", self.now)))
        self.record()
        mine = [r for r in self.records() if r.get("reason") == "agent usage"]
        self.assertEqual(sum(r["calls"] for r in mine), 2)

    def test_a_transcript_with_no_first_stamp_is_read_from_its_start(self):
        """Plan r1 P6 (opus, sonnet, agy): unknown is not old — a new session's first turns are counted."""
        other = self.tdir / "sess-0.jsonl"
        other.write_bytes(_lines(_entry("o1")))
        self.record(paths=[other])
        self.pad()
        self.record(paths=[other])
        bare = [_entry("m1"), _entry("m2")]
        for e in bare:
            del e["timestamp"]
        self.main.write_bytes(_lines({"type": "mode", "mode": "x"}, *bare))
        self.record()
        self.assertEqual(sum(r["calls"] for r in self.records() if r.get("reason") == "agent usage"
                             and r["file"] == self.mod.file_key(str(self.main))), 2)

    def test_with_no_transcript_the_cli_still_rotates(self):
        self.pad()
        res = self.mod.record_spans(self.lane, task="7", paths=[], session_id="pid-1", pbj=self.pbj)
        self.assertEqual(res["skipped"], "no transcript")
        self.assertEqual(len(self.archives()), 1)


class _Archives(_Journal):
    """A journal with hand-made archives, and the tasks package importable."""

    def setUp(self):
        super().setUp()
        import sys
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        from tasks import numbers
        self.numbers = numbers

    def rec(self, reason: str, t: float, task: str = "7", hook: str = "tasks", **extra) -> dict:
        return dict({"ts": _iso(t), "session_id": "pid-1", "hook": hook, "decision": "record", "reason": reason,
                     "task": task}, **extra)

    def arch(self, name_epoch: int, recs, tasks=None, gz=False, pid=1) -> Path:
        import gzip
        p = self.jdir / f"enforcement.jsonl.archived-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(name_epoch))}" \
                        f"-{name_epoch}-{pid}"
        data = b"".join(_line(r) for r in recs)
        if gz:
            with gzip.open(str(p) + ".gz", "wb") as f:
                f.write(data)
        else:
            p.write_bytes(data)
        if tasks is not None:
            Path(str(p) + ".tasks").write_text(json.dumps({"tasks": tasks}))
        return p

    def read(self, task="7", deadline=5.0):
        return self.numbers.read_enforcement(self.lane, time.monotonic() + deadline, task=task)


class TheNumbersReader(_Archives):
    """W6: `numbers.read_enforcement` reads the archives a task needs — the ones whose `.tasks` index names it, or with
    none — then the live file and the spools, each line once, in order (plan r1, P2-P4, P7)."""

    def test_archives_then_live_in_order_carries_and_horizon_left_out(self):
        t = self.now - 3000
        self.arch(int(t + 100), [self.rec("new", t), self.rec("work", t + 10)], tasks=["7"], gz=True)
        self.arch(int(t + 200), [self.rec("verify run", t + 150, hook="verify")], tasks=["7"])
        self.write({"ts": _iso(t + 201), "hook": "agent-usage", "decision": "record", "reason": HZ, "horizon": _iso(t)},
                   dict(_position("a" * 16, 5, t + 201), reason=CARRY, of=_iso(t + 5)),
                   self.rec("done", t + 300))
        got = self.read()
        self.assertEqual([r["reason"] for r in got], ["new", "work", "verify run", "done"])
        self.assertEqual([r["_n"] for r in got], sorted(r["_n"] for r in got))

    def test_an_archive_whose_index_leaves_the_task_out_is_not_read_one_without_an_index_is(self):
        t = self.now - 3000
        self.arch(int(t + 100), [self.rec("work", t, task="7")], tasks=["9"])        # says: not task 7's
        self.arch(int(t + 200), [self.rec("work", t + 150, task="7")], tasks=None, pid=2)
        self.write(self.rec("done", t + 300))
        self.assertEqual([r["_t"] for r in self.read()], [int(t + 150), int(t + 300)])

    def test_kill_states_are_read_once(self):
        """Plan r1 P4: an archive name on the live file's inode, and `X` beside `X.gz`."""
        import gzip
        t = self.now - 3000
        x = self.arch(int(t + 100), [self.rec("work", t)], tasks=["7"])
        with gzip.open(str(x) + ".gz", "wb") as f:
            f.write(x.read_bytes())
        self.write(self.rec("done", t + 300))
        os.link(self.live, self.jdir / f"enforcement.jsonl.archived-20261019-000000-{int(t + 400)}-9")
        self.assertEqual([r["reason"] for r in self.read()], ["work", "done"])

    def test_one_line_per_spool_id_placed_by_its_second(self):
        t = self.now - 3000
        sid = "enforcement.jsonl.spool-20261019-000000-1-1"
        self.write(self.rec("work", t), self.rec("verify run", t + 20, hook="verify"),
                   self.rec("done", t + 10, spool=sid),                # merged late, out of file order
                   self.rec("work", t + 30))
        (self.jdir / sid).write_bytes(_line(self.rec("done", t + 10, spool=sid)))     # its merge killed before the unlink
        other = "enforcement.jsonl.spool-20261019-000001-2-2"
        (self.jdir / other).write_bytes(_line(self.rec("verify run", t + 20, hook="verify", spool=other)))
        got = [(r["reason"], r["_t"] - int(t)) for r in self.read()]
        self.assertEqual(got, [("work", 0), ("done", 10), ("verify run", 20), ("verify run", 20), ("work", 30)])

    def test_a_rotation_in_progress_makes_the_reader_wait_then_give_up(self):
        self.write(self.rec("work", self.now))
        holder = self.hold(2.5)
        try:
            with self.assertRaises(self.numbers.OutOfTime):
                self.read(deadline=0.5)
        finally:
            holder.wait()
        self.assertEqual(len(self.read()), 1)


class RetroAndDashboard(_Archives):
    """W7: the retro reads every transition, archived or live; the dashboard and the bootstrap's panel health read the
    archives renamed since their window's baseline began (plan r1, P11)."""

    def review(self, t: float, seat: str = "claude:opus") -> dict:
        return {"ts": _iso(t), "session_id": "pid-1", "hook": "review", "decision": "record", "reason": "review spend",
                "kind": "panel", "seat": seat, "task": "7", "round": 1, "duration_ms": 60000, "status": "ok",
                "usage": {"status": "unknown"}}

    def test_the_retro_reads_the_transitions_of_every_archive(self):
        from tasks.retro import lifecycle_records, transition_journal
        t = self.now - 9000
        self.arch(int(t + 100), [self.rec("new", t, task="3", detail="build x"), self.rec("work", t + 5, task="3")],
                  tasks=["3"], gz=True)
        self.arch(int(t + 200), [self.rec("done", t + 150, task="3")], tasks=["3"], pid=2)
        self.write({"ts": _iso(t + 201), "hook": "agent-usage", "decision": "record", "reason": HZ, "horizon": _iso(t)},
                   self.rec("work", t + 300, task="4"))
        got = [(e, task) for _s, e, task, _d in lifecycle_records(transition_journal(self.lane))]
        self.assertEqual(got, [("new", "3"), ("work", "3"), ("done", "3"), ("work", "4")])

    def test_the_dashboard_reads_the_archives_its_window_needs(self):
        from datetime import datetime, timezone
        from tasks import dashboard
        old = self.now - 60 * 86400
        self.arch(int(old + 100), [self.review(old)], pid=1)                      # before window + baseline
        self.arch(int(self.now - 86400), [self.review(self.now - 2 * 86400), self.review(self.now - 2 * 86400 + 5)],
                  pid=2)
        self.write(self.review(self.now - 60))
        recs, skipped, status = dashboard.load_review_journal(self.live, since=self.now - 44 * 86400)
        self.assertEqual((len(recs), skipped, status), (3, 0, "ok"))
        lines = dashboard.panel_health_lines(self.project, now=datetime.fromtimestamp(self.now, timezone.utc))
        self.assertIn("reviews: 3 judge runs", lines[2])


class TheOracle(unittest.TestCase):
    """W8: one stream of records written twice — to a lane that never rotates and to one that rotates at random points
    with a tiny limit, some records spooled. Every reader must answer the same: numbers' records per task (compose's
    only input from this journal), the retro's transitions, the dashboard's review records, and the recorder's newest
    position per transcript — the transcripts whose position the horizon pruned aside (plan r1, P12), checked apart."""

    SEEDS = 12

    def setUp(self):
        import sys
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.pbj = _load("pb_journal_oracle", "pb_journal.py")
        self.au = _load("agent_usage_oracle", "agent_usage.py")
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        from tasks import dashboard, numbers, retro
        self.numbers, self.retro, self.dashboard = numbers, retro, dashboard

    def stream(self, rnd) -> "list[dict]":
        now = time.time()
        t = now - 3 * 86400
        recs = []
        for k in range(2):                                     # positions the horizon will prune
            recs.append(_position(f"{k:x}" * 16, 50 + k, now - (40 + k) * 86400, task="1"))
        tos = {f"k{k:015d}": 0 for k in range(6)}
        open_tasks: "list[str]" = []
        made = 0
        while len(recs) < 900:
            t += rnd.choice((0, 0, 1, 2, 5, 30))
            roll = rnd.random()
            if roll < 0.06 and made < 9:
                made += 1
                task = str(made)
                recs += [{"ts": _iso(t), "session_id": "pid-1", "hook": "tasks", "decision": "record", "reason": "new",
                          "task": task, "detail": "build x"},
                         {"ts": _iso(t), "session_id": "pid-1", "hook": "tasks", "decision": "record",
                          "reason": "work", "task": task}]
                open_tasks.append(task)
            elif roll < 0.10 and open_tasks:
                task = open_tasks.pop(rnd.randrange(len(open_tasks)))
                recs += [{"ts": _iso(t), "session_id": "pid-1", "hook": "close", "decision": "record",
                          "reason": "verify contract", "task": task},
                         {"ts": _iso(t), "session_id": "pid-1", "hook": "tasks", "decision": "record",
                          "reason": "done", "task": task}]
                if rnd.random() < 0.4:                         # reopened later
                    open_tasks.append(task)
                    t += 1
                    recs.append({"ts": _iso(t), "session_id": "pid-1", "hook": "tasks", "decision": "record",
                                 "reason": "work", "task": task})
            elif roll < 0.35 and open_tasks:
                task = rnd.choice(open_tasks)
                kind = rnd.random()
                if kind < 0.3:
                    recs.append({"ts": _iso(t), "session_id": "pid-1", "hook": "verify", "decision": "record",
                                 "reason": "verify run", "task": task, "rc": 0, "how": "ran", "seconds": 3.0})
                elif kind < 0.6:
                    recs.append({"ts": _iso(t), "session_id": "pid-1", "hook": "review", "decision": "record",
                                 "reason": "review spend", "kind": "panel", "seat": rnd.choice(("a", "b")),
                                 "task": task, "round": 1, "duration_ms": 1000, "status": "ok",
                                 "usage": {"status": "unknown"}})
                else:
                    key = rnd.choice(sorted(tos))
                    tos[key] += rnd.randint(1, 900)
                    recs.append(_position(key, tos[key], t, task=task))
            else:
                recs.append({"ts": _iso(t), "session_id": "pid-1", "hook": "task-gate", "decision": "allow",
                             "reason": "x" * rnd.randint(10, 120)})
        return recs

    def lane(self, name: str) -> Path:
        p = self.root / name / ".agent"
        (p / "journal").mkdir(parents=True)
        return p

    def test_rotated_answers_as_unrotated(self):
        import random
        for seed in range(self.SEEDS):
            with self.subTest(seed=seed):
                rnd = random.Random(seed)
                recs = self.stream(rnd)
                a, b = self.lane(f"a{seed}"), self.lane(f"b{seed}")
                limit = rnd.choice((2048, 4096, 9000))
                seconds = {}
                for r in recs:
                    seconds[r["ts"]] = seconds.get(r["ts"], 0) + 1
                for i, r in enumerate(recs):
                    self.pbj._write_record(a, r)
                    if seconds[r["ts"]] == 1 and r["hook"] != "agent-usage" and rnd.random() < 0.1:
                        self.pbj._spool_line(b / "journal", _line(r), "enforcement.jsonl", tag=True)
                    else:
                        self.pbj._write_record(b, r)
                    if rnd.random() < 0.08:
                        self.pbj.rotate_enforcement(b, limit=limit)
                self.pbj.rotate_enforcement(b, limit=limit)
                self.assertTrue(list((b / "journal").glob("enforcement.jsonl.archived-*")), "it rotated")
                self.compare(a, b, recs)

    def compare(self, a: Path, b: Path, recs: "list[dict]") -> None:
        def strip(rs):
            return [{k: v for k, v in r.items() if k not in ("_n", "spool")} for r in rs]
        far = time.monotonic() + 60
        tasks = sorted({r["task"] for r in recs if r.get("task")})
        for task in tasks:
            ra = [r for r in self.numbers.read_enforcement(a, far) if str(r.get("task")) == task]
            rb = [r for r in self.numbers.read_enforcement(b, far, task=task) if str(r.get("task")) == task]
            self.assertEqual(strip(rb), strip(ra), f"task {task}")
        self.assertEqual(self.retro.lifecycle_records(b / "journal" / "enforcement.jsonl"),
                         self.retro.lifecycle_records(a / "journal" / "enforcement.jsonl"))
        since = time.time() - 2 * 86400
        da = [r for r in self.dashboard.load_review_journal(a / "journal" / "enforcement.jsonl")[0]
              if r["ts"].timestamp() >= since]
        db = [r for r in self.dashboard.load_review_journal(b / "journal" / "enforcement.jsonl", since=since)[0]
              if r["ts"].timestamp() >= since]
        self.assertEqual(db, da)
        keys = {r["file"] for r in recs if r.get("hook") == "agent-usage"}
        na, _ = self.au._newest(str(a / "journal" / "enforcement.jsonl"), keys)
        nb, complete = self.au._newest(str(b / "journal" / "enforcement.jsonl"), keys)
        self.assertTrue(complete)
        pruned = {k for k, r in na.items() if r["ts"] < _iso(time.time() - 30 * 86400)}
        self.assertEqual(len(pruned), 2)
        self.assertEqual(set(nb), set(na) - pruned)
        for k in nb:
            self.assertEqual({f: nb[k][f] for f in ("to", "ino", "head", "last_id")},
                             {f: na[k][f] for f in ("to", "ino", "head", "last_id")}, k)


class TheRollbackRecipe(TheOracle):
    """W11: the rollback's merge recipe — read from docs/enforcement-journal.md between its markers, so the text a user
    runs is the text tested — gives one file back with every line of the rotated journal, the carries and the horizon
    out, even with each kill state present (an `X.gz` beside `X`, an archive name on the live file's inode, a spool
    already merged and one not yet)."""

    def recipe(self) -> str:
        doc = (PLUGIN.parent.parent / "docs" / "enforcement-journal.md").read_text(encoding="utf-8")
        block = doc.split("<!-- recipe:merge:start -->", 1)[1].split("<!-- recipe:merge:end -->", 1)[0]
        return block.split("```bash", 1)[1].split("```", 1)[0]

    def test_rotated_answers_as_unrotated(self):           # (the oracle runs once, in its own class)
        pass

    def test_the_recipe_gives_every_line_back(self):
        import gzip
        import random
        from tests._bashcheck import bash_or_skip
        rnd = random.Random(7)
        recs = self.stream(rnd)
        a, b = self.lane("a"), self.lane("b")
        seconds = {}
        for r in recs:
            seconds[r["ts"]] = seconds.get(r["ts"], 0) + 1
        for r in recs:
            self.pbj._write_record(a, r)
            self.pbj._write_record(b, r)
            if rnd.random() < 0.05:
                self.pbj.rotate_enforcement(b, limit=4096)
        jb = b / "journal"
        arch = sorted(p for p in jb.glob("enforcement.jsonl.archived-*") if not p.name.endswith((".tasks", ".gz")))
        self.assertGreater(len(arch), 3)
        with gzip.open(str(arch[0]) + ".gz", "wb") as f:   # a compression killed before it removed its source
            f.write(arch[0].read_bytes())
        with gzip.open(str(arch[1]) + ".gz", "wb") as f:   # a finished one
            f.write(arch[1].read_bytes())
        arch[1].unlink()
        os.link(jb / "enforcement.jsonl", jb / f"enforcement.jsonl.archived-20261019-235959-{int(time.time()) + 9}-1")
        late = [{"ts": _iso(time.time()), "session_id": "pid-1", "hook": "task-gate", "decision": "block",
                 "reason": f"late {i}"} for i in range(2)]
        merged_name = "enforcement.jsonl.spool-20261019-000000-1-1"
        line = _line(dict(late[0], spool=merged_name))
        with open(jb / "enforcement.jsonl", "ab") as f:
            f.write(line)                                   # merged, then its merge killed before the unlink
        (jb / merged_name).write_bytes(line)
        self.pbj._spool_line(jb, _line(late[1]), "enforcement.jsonl", tag=True)
        r = subprocess.run([bash_or_skip(), "-c", self.recipe()], cwd=jb, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(p.name for p in jb.iterdir() if p.name.startswith("enforcement.jsonl")),
                         ["enforcement.jsonl"])

        def canon(raw):
            d = json.loads(raw)
            d.pop("spool", None)
            return json.dumps(d, sort_keys=True)
        got = [canon(x) for x in (jb / "enforcement.jsonl").read_text().splitlines()]
        want = [canon(x) for x in (a / "journal" / "enforcement.jsonl").read_text().splitlines()] + \
               [canon(json.dumps(x)) for x in late]
        self.assertEqual(got, want)                         # every line, in order, the spooled ones last


def _epoch(ts: str) -> float:
    import calendar
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


if __name__ == "__main__":
    unittest.main()
