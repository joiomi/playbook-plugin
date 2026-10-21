"""The main agent's token usage, read from the host's session transcript (task 244, PHASE-B item 6).

The owner, 2026-10-18 (dialog): "Da, după 243 (Recomandat)" — every closure's numbers carry the main agent's own tokens,
not only the judge seats'. Only the host transcript holds them, one `type: "assistant"` entry per content block of an API
call, each repeating that call's `message.usage` (measured on a 1 GB transcript: 926 of 1,227 messages had several
entries, their usage identical, their entries contiguous). `scripts/agent_usage.py` reads a transcript from an offset
to its last complete line and sums each call once.

Held:
  * one call written as several entries counts once (dedupe by `message.id`);
  * a call whose entries straddle two reads counts once (the id last counted is carried with the offset);
  * a partial last line is left for the next read; the offset stops at the last newline;
  * `<synthetic>` entries are no call; `isSidechain` entries count apart;
  * per model: calls, in, out, cache_read, cache_write, thinking;
  * a file it cannot read is `unknown`, never 0; a file shorter than the offset is read from 0 again, said;
  * an entry whose usage is not readable is counted as unreadable, never as 0 tokens; a broken line counts as bad
    only when it names an assistant entry (no other line is parsed);
  * nothing of the conversation leaves the reader but the model's name;
  * the invariant: a span read in pieces, cut at every line boundary, sums to one read of the whole span.

Run: python3 -m unittest tests.test_agent_usage
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_P = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts" / "agent_usage.py"


def _load():
    spec = importlib.util.spec_from_file_location("_agent_usage_under_test", _P)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def entry(mid, model="claude-opus-5-5", inp=2, cw=100, cr=1000, out=50, think=None, side=False, text="secret words"):
    usage: dict = {"input_tokens": inp, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
             "output_tokens": out}
    if think is not None:
        usage["output_tokens_details"] = {"thinking_tokens": think}
    return {"type": "assistant", "isSidechain": side, "sessionId": "s1", "timestamp": "2026-10-18T20:00:00Z",
            "message": {"id": mid, "model": model, "role": "assistant", "usage": usage,
                        "content": [{"type": "text", "text": text}]}}


def user(text="please do it"):
    return {"type": "user", "message": {"role": "user", "content": text}}


def lines(*objs) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


class _Case(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "t.jsonl"

    def write(self, data: bytes):
        self.path.write_bytes(data)

    def read(self, offset=0, last_id=""):
        return self.mod.read_span(str(self.path), offset, last_id)


class Counting(_Case):
    def test_one_call_written_as_several_entries_counts_once(self):
        self.write(lines(user(), entry("m1"), entry("m1"), entry("m1"), user("tool result"), entry("m2", out=7)))
        r = self.read()
        self.assertEqual(r["status"], "known")
        m = r["models"]["claude-opus-5-5"]
        self.assertEqual((m["calls"], m["in"], m["cache_write"], m["cache_read"], m["out"]), (2, 4, 200, 2000, 57))
        self.assertEqual(r["last_id"], "m2")
        self.assertEqual(r["offset"], self.path.stat().st_size)

    def test_thinking_is_summed_where_given(self):
        self.write(lines(entry("m1", think=12), entry("m2")))
        self.assertEqual(self.read()["models"]["claude-opus-5-5"]["thinking"], 12)

    def test_synthetic_is_no_call_and_a_sidechain_counts_apart(self):
        self.write(lines(entry("s0", model="<synthetic>"), entry("m1"), entry("k1", side=True, out=9)))
        r = self.read()
        self.assertNotIn("<synthetic>", r["models"])
        self.assertEqual(r["models"]["claude-opus-5-5"]["calls"], 1)
        self.assertEqual(r["sidechain"]["claude-opus-5-5"]["calls"], 1)
        self.assertEqual(r["sidechain"]["claude-opus-5-5"]["out"], 9)

    def test_models_are_kept_apart(self):
        self.write(lines(entry("m1"), entry("m2", model="claude-haiku-5-5", out=3)))
        r = self.read()
        self.assertEqual(sorted(r["models"]), ["claude-haiku-5-5", "claude-opus-5-5"])
        self.assertEqual(r["models"]["claude-haiku-5-5"]["out"], 3)


class Boundaries(_Case):
    def test_a_call_straddling_two_reads_counts_once(self):
        first = lines(entry("m1"), entry("m2"))
        self.write(first + lines(entry("m2"), entry("m3")))
        r1 = self.mod.read_span(str(self.path), 0, "", stop=len(first))
        self.assertEqual(r1["last_id"], "m2")
        r2 = self.read(r1["offset"], r1["last_id"])
        self.assertEqual(r2["models"]["claude-opus-5-5"]["calls"], 1, "m2's second entry is not counted again")

    def test_a_partial_last_line_waits_for_the_next_read(self):
        whole = lines(entry("m1"))
        half = json.dumps(entry("m2")).encode()[:40]
        self.write(whole + half)
        r = self.read()
        self.assertEqual(r["offset"], len(whole))
        self.assertEqual(r["models"]["claude-opus-5-5"]["calls"], 1)

    def test_a_file_shorter_than_the_offset_is_read_from_the_start_and_said(self):
        self.write(lines(entry("m9")))
        r = self.read(offset=10 ** 6, last_id="m8")
        self.assertTrue(r["restarted"])
        self.assertEqual(r["models"]["claude-opus-5-5"]["calls"], 1)

    def test_pieces_cut_at_every_line_boundary_sum_to_the_whole(self):
        data = lines(user(), entry("a"), entry("a"), entry("b", side=True), user(), entry("c", think=4), entry("c"),
                     entry("d", model="<synthetic>"), entry("e", model="claude-haiku-5-5"))
        self.write(data)
        whole = self.read()
        cuts = [i + 1 for i, ch in enumerate(data) if ch == 0x0A]
        for cut in cuts:
            a = self.mod.read_span(str(self.path), 0, "", stop=cut)
            b = self.read(a["offset"], a["last_id"])
            for bucket in ("models", "sidechain"):
                for model in set(whole[bucket]) | set(a[bucket]) | set(b[bucket]):
                    for k in ("calls", "in", "out", "cache_read", "cache_write", "thinking"):
                        got = a[bucket].get(model, {}).get(k, 0) + b[bucket].get(model, {}).get(k, 0)
                        self.assertEqual(got, whole[bucket].get(model, {}).get(k, 0), (cut, bucket, model, k))


class WhatItCannotRead(_Case):
    def test_a_missing_file_is_unknown_not_zero(self):
        r = self.read()
        self.assertEqual(r["status"], "unknown")
        self.assertEqual(r["models"], {})
        self.assertTrue(r["reason"])

    def test_a_link_or_a_fifo_is_not_read(self):
        target = Path(self._tmp.name) / "real.jsonl"
        target.write_bytes(lines(entry("m1")))
        self.path.symlink_to(target)
        self.assertEqual(self.read()["status"], "unknown")
        fifo = Path(self._tmp.name) / "f.jsonl"
        os.mkfifo(fifo)
        self.assertEqual(self.mod.read_span(str(fifo), 0, "")["status"], "unknown")

    def test_an_entry_whose_usage_cannot_be_read_is_counted_as_unreadable(self):
        bad = entry("m2")
        bad["message"]["usage"]["output_tokens"] = "lots"
        self.write(lines(entry("m1"), bad, user()) + b'{"type": "assistant", not json\n' + b"{not json either\n")
        r = self.read()
        self.assertEqual(r["models"]["claude-opus-5-5"]["calls"], 1)
        self.assertEqual(r["unreadable"], 1)
        self.assertEqual(r["bad_lines"], 1, "a broken line that names an assistant entry; the other is never parsed")

    def test_nothing_of_the_conversation_leaves_the_reader(self):
        self.write(lines(user("my password is hunter2"), entry("m1", text="hunter2 again")))
        blob = json.dumps(self.read())
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("password", blob)



_PBJ = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts" / "pb_journal.py"


def _load_pbj():
    spec = importlib.util.spec_from_file_location("_pb_journal_under_test", _PBJ)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Lane(unittest.TestCase):
    """A lane (`.agent`) with its journal dir, a main transcript and its subagents folder."""
    def setUp(self):
        self.mod = _load()
        self.pbj = _load_pbj()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.lane = root / "proj" / ".agent"
        self.lane.mkdir(parents=True)            # no journal dir yet: a fresh project (the recorder makes it)
        self.tdir = root / "host" / "-proj"
        self.tdir.mkdir(parents=True)
        self.main = self.tdir / "sess-1.jsonl"

    def records(self):
        p = self.lane / "journal" / "enforcement.jsonl"
        if not p.exists():
            return []
        out = []
        for l in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("hook") == "agent-usage" and r.get("file") != ["not", "hashable"]:
                out.append(r)
        return out

    def record(self, task="7", paths=None, **kw):
        return self.mod.record_spans(self.lane, task=task, paths=[str(p) for p in (paths or [self.main])],
                                     session_id="pid-1", pbj=self.pbj, **kw)

    def total(self, side=0):
        return sum(r.get("calls", 0) for r in self.records() if r.get("side") == side and r.get("status") != "unknown")


class RecordingSpans(_Lane):
    def test_a_record_names_its_file_and_span_and_the_next_read_continues_from_it(self):
        self.main.write_bytes(lines(entry("m1"), entry("m1"), entry("m2")))
        self.record()
        r = self.records()
        self.assertEqual(len(r), 1)
        rec = r[0]
        self.assertEqual((rec["calls"], rec["task"], rec["side"], rec["from"]), (2, "7", 0, 0))
        self.assertEqual(rec["to"], self.main.stat().st_size)
        self.assertEqual(rec["last_id"], "m2")
        self.assertEqual(rec["ino"], str(self.main.stat().st_ino))
        self.assertEqual(len(rec["file"]), 16)
        with open(self.main, "ab") as fh:
            fh.write(lines(entry("m2"), entry("m3")))
        self.record(task="8")
        r = self.records()
        self.assertEqual([x["calls"] for x in r], [2, 1], "m2's late entry is not counted again")
        self.assertEqual(r[1]["task"], "8")
        self.assertEqual(r[1]["from"], rec["to"])

    def test_one_record_per_model_and_a_subagent_file_counts_as_side(self):
        self.main.write_bytes(lines(entry("m1"), entry("h1", model="claude-haiku-5-5")))
        sub = self.tdir / "sess-1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-a1.jsonl").write_bytes(lines(entry("s1", side=True), entry("s2", side=True)))
        self.record()
        by = {(r["side"], r["model"]): r["calls"] for r in self.records()}
        self.assertEqual(by, {(0, "claude-opus-5-5"): 1, (0, "claude-haiku-5-5"): 1, (1, "claude-opus-5-5"): 2})

    def test_a_file_with_no_record_is_read_from_the_start_when_small(self):
        self.main.write_bytes(lines(entry("m1")))
        self.record(fresh_max=10 ** 6)
        self.assertEqual(self.total(), 1)

    def test_a_large_file_with_no_record_gets_a_seed_and_no_history(self):
        self.main.write_bytes(lines(*[entry(f"old{i}") for i in range(20)]))
        size = self.main.stat().st_size
        self.record(fresh_max=100)
        r = self.records()
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["status"], r[0]["from"], r[0]["to"]), ("unknown", size, size))
        self.assertIn("no earlier record", r[0]["why"])
        with open(self.main, "ab") as fh:
            fh.write(lines(entry("new1")))
        self.record(fresh_max=100)
        self.assertEqual(self.total(), 1, "only what came after the seed")

    def test_a_seed_whose_end_cannot_be_read_is_not_written(self):
        """PLAN.md "From task 253" (b) (task 265; opus): a failed read of the last newline seeded at the size again —
        inside a half-written line, which the next read would never count. No seed then; the next read seeds."""
        if os.geteuid() == 0:
            self.skipTest("root reads a mode-000 file")
        whole = lines(*[entry(f"old{i}") for i in range(20)])
        self.main.write_bytes(whole + lines(entry("new1"))[:50])
        os.chmod(self.main, 0)
        try:
            self.record(fresh_max=100)
            # asked while the file is still unreadable (impl round 1, opus Important: asked after the restore, it
            # proved nothing of the read-error arm)
            unread = self.mod._unread(str(self.lane), [str(self.main)])
        finally:
            os.chmod(self.main, 0o644)
        self.assertEqual(self.records(), [], "a seed written on a read error")
        self.assertTrue(unread, "an unreadable file is not nothing unread")
        self.record(fresh_max=100)
        r = self.records()
        self.assertEqual((r[0]["status"], r[0]["from"], r[0]["to"]), ("unknown", len(whole), len(whole)))

    def test_a_seed_taken_mid_write_sits_at_the_last_complete_line(self):
        # task 253 (PLAN.md "From task 252" (a), sonnet / codex): a seed at `size` fell inside a half-written line, which
        # the next read would then never count; at the last newline, the line is counted once it ends
        whole = lines(*[entry(f"old{i}") for i in range(20)])
        tail = lines(entry("new1"))
        self.main.write_bytes(whole + tail[:50])                         # the host stopped mid-line
        self.record(fresh_max=100)
        r = self.records()
        self.assertEqual((r[0]["status"], r[0]["from"], r[0]["to"]), ("unknown", len(whole), len(whole)))
        with open(self.main, "ab") as fh:
            fh.write(tail[50:])
        self.record(fresh_max=100)
        self.assertEqual(self.total(), 1, "the finished line, counted once")

    def test_a_replaced_file_is_a_new_file(self):
        self.main.write_bytes(lines(entry("m1"), entry("m2")))
        self.record()
        self.main.unlink()
        # LARGER than the old record's `to`: only its inode says it is another file (a shorter one restarts anyway)
        self.main.write_bytes(lines(*[entry(f"n{i}") for i in range(5)]))
        self.record(fresh_max=10 ** 6)
        self.assertEqual(self.total(), 7)

    def test_an_old_pointer_path_and_the_payload_path_are_both_read(self):
        old = self.tdir / "sess-old.jsonl"
        old.write_bytes(lines(entry("o1")))
        self.record(paths=[old])
        with open(old, "ab") as fh:
            fh.write(lines(entry("o2")))
        self.main.write_bytes(lines(entry("m1")))
        self.record(paths=[self.main, old], fresh_max=10 ** 6)
        self.assertEqual(self.total(), 3, "o1, then o2 from the old file's tail, and m1")

    def test_a_crash_before_the_append_reads_the_span_again_and_counts_it_once(self):
        self.main.write_bytes(lines(entry("m1")))

        class Boom:
            def __getattr__(self, name):
                raise RuntimeError("the append never happened")
        try:
            self.mod.record_spans(self.lane, task="7", paths=[str(self.main)], session_id="pid-1", pbj=Boom())
        except RuntimeError:
            pass
        self.assertEqual(self.records(), [])
        self.record()
        self.assertEqual(self.total(), 1)

    def test_two_recorders_at_once_count_each_call_once(self):
        import threading
        self.main.write_bytes(lines(*[entry(f"m{i}") for i in range(200)]))
        go = threading.Barrier(4)

        def run():
            go.wait()
            self.record(lock_wait=10)
        ts = [threading.Thread(target=run) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(self.total(), 200)

    def test_the_longest_record_stays_under_the_atomic_floor(self):
        self.main.write_bytes(lines(entry("msg_" + "x" * 300, model="m" * 300, think=10 ** 14, cr=10 ** 15)))
        self.record()
        raw = (self.lane / "journal" / "enforcement.jsonl").read_bytes().splitlines()[-1]
        self.assertLess(len(raw) + 1, 512, raw)



class RoundOneFixes(_Lane):
    """Task 244's impl round 1 (codex, opus, sonnet)."""
    def test_a_span_of_two_models_is_one_append(self):
        self.main.write_bytes(lines(entry("m1"), entry("h1", model="claude-haiku-5-5")))
        calls = []
        real = self.pbj._write_record

        def count(agent_dir, rec, name="enforcement.jsonl", data=None):
            calls.append(data)
            return real(agent_dir, rec, name, data)
        self.pbj._write_record = count
        self.record()
        self.assertEqual(len(calls), 1, "both models' records in one write")
        self.assertEqual(len(self.records()), 2)

    def test_a_file_replaced_in_place_is_counted_in_full_by_the_table(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "playbook"))
        from tasks import numbers
        self.main.write_bytes(lines(entry("m1"), entry("m2")))
        self.record()
        with open(self.main, "r+b") as fh:            # in place: the same inode
            fh.truncate(0)
            fh.write(lines(*[entry(f"n{i}") for i in range(5)]))
        self.record(fresh_max=10 ** 6)
        recs = [dict(r, _t=0.0, _n=n) for n, r in enumerate(self.records())]
        self.assertEqual(numbers.agent_table(recs)["models"]["claude-opus-5-5"]["calls"], 7)

    def test_an_inode_number_past_the_integer_cap_is_kept_exactly(self):
        big = 10 ** 17 + 5
        self.pbj.append_agent_usage(self.lane, task="7", file="f" * 16, ino=big, span_from=0, span_to=10, model="m",
                                    calls=1, counts={})
        self.assertEqual(self.records()[-1]["ino"], str(big))

    def test_a_key_the_cut_scan_did_not_reach_gets_a_seed_not_a_read_from_zero(self):
        self.main.write_bytes(lines(entry("m1")))
        self.record()
        filler = {"ts": "2026-10-19T00:00:00Z", "hook": "verify", "decision": "record", "reason": "verify run",
                  "task": "7", "command": "x" * 100, "rc": 0, "how": "ran", "seconds": 1}
        with open(self.lane / "journal" / "enforcement.jsonl", "a") as fh:
            for _ in range(200):
                fh.write(json.dumps(filler) + "\n")
        with open(self.main, "ab") as fh:
            fh.write(lines(entry("m2")))
        self.record(fresh_max=10 ** 6, scan_max=4096)
        r = self.records()
        self.assertEqual(r[-1].get("status"), "unknown", "a seed, since the scan could not reach the old record")
        self.assertEqual(self.total(), 1, "m1 is not counted a second time")

    def test_broken_assistant_lines_are_unreadable_even_in_a_span_with_no_call(self):
        self.main.write_bytes(lines(entry("m1")) + b'{"type": "assistant", broken\n')
        self.record()
        self.assertEqual(self.records()[-1].get("unreadable"), 1)
        with open(self.main, "ab") as fh:
            fh.write(b'{"type": "assistant", broken again\n')
        self.record()
        last = self.records()[-1]
        self.assertEqual((last.get("calls"), last.get("unreadable")), (0, 1))

    def test_an_entry_with_no_id_is_unreadable_never_counted(self):
        e = entry("x")
        del e["message"]["id"]
        self.write_main = None
        self.main.write_bytes(lines(e, e, e))
        self.record()
        self.assertEqual(self.total(), 0)
        self.assertEqual(sum(r.get("unreadable", 0) for r in self.records()), 3)

    def test_odd_lines_in_the_journal_do_not_stop_the_recorder(self):
        (self.lane / "journal").mkdir(exist_ok=True)
        with open(self.lane / "journal" / "enforcement.jsonl", "a") as fh:
            fh.write('["agent-usage"]\n')
            fh.write(json.dumps({"hook": "agent-usage", "file": ["not", "hashable"]}) + "\n")
        self.main.write_bytes(lines(entry("m1")))
        self.record()
        self.assertEqual(self.total(), 1)



class RoundTwoFixes(_Lane):
    """Task 244's impl round 2 (agy, opus, sonnet)."""
    def table(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "playbook"))
        from tasks import numbers
        return numbers.agent_table([dict(r, _t=0.0, _n=n) for n, r in enumerate(self.records())])

    def test_a_subagent_file_with_entries_in_both_buckets_keeps_both(self):
        sub = self.tdir / "sess-1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-a1.jsonl").write_bytes(lines(entry("s1", side=False), entry("s2", side=True)))
        self.main.write_bytes(b"")
        self.record()
        self.assertEqual(self.table()["models"]["claude-opus-5-5 (subagent)"]["calls"], 2)

    def test_an_unreadable_call_written_as_several_entries_is_one_unreadable(self):
        bad = entry("b1")
        bad["message"]["usage"]["output_tokens"] = "lots"
        self.main.write_bytes(lines(bad, bad, bad, entry("m1")))
        self.record()
        self.assertEqual(sum(r.get("unreadable", 0) for r in self.records()), 1)

    def test_the_task_is_read_inside_the_lock(self):
        import fcntl
        self.main.write_bytes(lines(entry("m1")))
        held = []

        def task():
            fd = os.open(str(self.lane / "journal" / "agent-usage.lock"), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except OSError:
                held.append(True)
            finally:
                os.close(fd)
            return "9"
        self.mod.record_spans(self.lane, task=task, paths=[str(self.main)], session_id="pid-1", pbj=self.pbj)
        self.assertEqual(held, [True], "the task was asked while the lane lock was held")
        self.assertEqual(self.records()[-1]["task"], "9")

    def test_a_first_empty_read_writes_one_zero_call_record_and_no_more(self):
        self.main.write_bytes(lines(user()))
        self.record()
        self.record()
        r = self.records()
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["calls"], 0)
        self.assertEqual(r[0]["to"], self.main.stat().st_size)

    def test_a_record_whose_to_is_a_boolean_is_not_a_read_position(self):
        self.main.write_bytes(lines(entry("m1"), entry("m2")))
        key = self.mod.file_key(str(self.main))
        rec = {"ts": "2026-10-19T00:00:00Z", "hook": "agent-usage", "decision": "record", "reason": "agent usage",
               "task": "7", "file": key, "ino": str(self.main.stat().st_ino), "from": 0, "to": True,
               "head": self.mod.head_of(str(self.main), 1), "last_id": "", "model": "m", "side": 0, "calls": 0}
        (self.lane / "journal").mkdir(exist_ok=True)
        with open(self.lane / "journal" / "enforcement.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        self.record(fresh_max=10 ** 6)
        self.assertEqual(self.total(), 2)

    def _hook(self, payload, **env):
        keys = ("PB_AU_LANE", "PB_AU_SESSION", "PB_AU_MARK_BUSY")
        old = {k: os.environ.get(k) for k in keys}
        os.environ.update({"PB_AU_LANE": str(self.lane), "PB_AU_SESSION": "s1", "PB_AU_MARK_BUSY": "1", **env})
        try:
            return self.mod.hook_main(json.dumps(payload))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_a_free_lock_end_marks_a_call_the_host_finished_after_the_read(self):
        """Task 265's impl round 1 (opus Important, sonnet): the end check looked at the file again after the read had let
        the lock go, so a call the host finished in between — the very case — was neither counted nor marked. The end
        now asks whether anything is unread since the newest record (`_unread`)."""
        from unittest import mock
        tail = lines(entry("m2"))
        self.main.write_bytes(lines(entry("m1")) + tail[:40])
        real = self.mod.record_spans

        def read_then_the_host_finishes(*a, **k):
            res = real(*a, **k)
            with open(self.main, "ab") as fh:
                fh.write(tail[40:])
            return res
        with mock.patch.object(self.mod, "record_spans", read_then_the_host_finishes):
            said = self._hook({"transcript_path": str(self.main)})
        self.assertEqual(self.total(), 1, said)
        self.assertEqual([r.get("status") for r in self.records() if r.get("status") == "unknown"], ["unknown"], said)

    def test_a_free_lock_end_after_complete_lines_with_no_call_marks_nothing(self):
        """Task 265's impl round 2 (opus, sonnet Important): a span with no call writes no record, so the newest `to`
        stays behind what the read reached — and the end check against it marked `unknown` at almost every normal end
        (a prompt, a `/exit` entry after the last call). The end is now decided against what this read reached."""
        self.main.write_bytes(lines(entry("m1")))
        self._hook({"transcript_path": str(self.main)}, PB_AU_MARK_BUSY="")       # a Stop
        with open(self.main, "ab") as fh:
            fh.write(lines(user("/exit"), user("bye")))
        said = self._hook({"transcript_path": str(self.main)})                    # the session's end
        self.assertEqual(self.total(), 1, said)
        self.assertEqual([r for r in self.records() if r.get("status") == "unknown"], [], said)

    def test_a_free_lock_end_after_a_span_the_journal_refused_marks_it(self):
        """Task 265's impl round 1 (opus Minor): a span the journal refused at the session's end is a gap no later
        reader fills — marked, as what the read did not record."""
        from unittest import mock
        self.main.write_bytes(lines(entry("m1")))
        refuse = mock.patch.object(self.pbj, "append_agent_usage_span", lambda lane, items: False)
        with mock.patch.object(self.mod, "load_journal", lambda: self.pbj), refuse:
            said = self._hook({"transcript_path": str(self.main)})
        self.assertIn("NOT written", said)
        self.assertEqual([r.get("status") for r in self.records() if r.get("status") == "unknown"], ["unknown"], said)

    def test_a_marker_the_journal_refuses_at_the_end_is_said_as_such(self):
        """Task 266 (PLAN.md "From task 265" (e)): the message for a marker that could not be written named a
        half-written call only, though the marker covers anything unread at the end."""
        from unittest import mock
        self.main.write_bytes(lines(entry("m1")) + lines(entry("m2"))[:40])
        with mock.patch.object(self.mod, "load_journal", lambda: self.pbj), \
                mock.patch.object(self.pbj, "append_agent_usage", lambda *a, **k: False):
            said = self._hook({"transcript_path": str(self.main)})
        self.assertIn("the unknown marker (unread at the end) could not be written", said)

    def test_a_free_lock_end_after_a_clean_read_marks_nothing(self):
        self.main.write_bytes(lines(entry("m1"), entry("m2")))
        said = self._hook({"transcript_path": str(self.main)})
        self.assertEqual(self.total(), 2, said)
        self.assertEqual([r for r in self.records() if r.get("status") == "unknown"], [])

    def test_an_unresolved_session_records_nothing(self):
        self.main.write_bytes(lines(entry("m1")))
        old = {k: os.environ.get(k) for k in ("PB_AU_LANE", "PB_AU_SESSION")}
        os.environ["PB_AU_LANE"], os.environ["PB_AU_SESSION"] = str(self.lane), ""
        try:
            got = self.mod.hook_main(json.dumps({"transcript_path": str(self.main)}))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertIn("skipped", got)
        self.assertEqual(self.records(), [])



class TheWriteIsReported(_Lane):
    """Task 246 (PLAN.md "From task 244" (f)): an append says whether it landed."""
    def test_an_append_that_lands_says_true(self):
        ok = self.pbj.append_agent_usage(self.lane, task="7", known=False, why="x")
        self.assertIs(ok, True)

    def test_an_append_into_a_journal_that_cannot_be_written_says_false(self):
        (self.lane / "journal").mkdir(exist_ok=True)
        j = self.lane / "journal" / "enforcement.jsonl"
        j.write_text("")
        j.chmod(0o444)
        try:
            (self.lane / "journal" / "probe").write_text("x")
            with open(j, "a"):
                self.skipTest("file permissions not enforced for this user")
        except PermissionError:
            pass
        ok = self.pbj.append_agent_usage(self.lane, task="7", known=False, why="x")
        j.chmod(0o644)
        self.assertIs(ok, False)

    def test_an_append_the_writer_skips_says_false(self):
        # a linked journal is skipped without an error (the writer's own rule): the append must say so too
        (self.lane / "journal").mkdir(exist_ok=True)
        elsewhere = self.lane.parent / "elsewhere.jsonl"
        elsewhere.write_text("")
        (self.lane / "journal" / "enforcement.jsonl").symlink_to(elsewhere)
        ok = self.pbj.append_agent_usage(self.lane, task="7", known=False, why="x")
        self.assertIs(ok, False)
        self.assertEqual(elsewhere.read_text(), "", "nothing written through the link")


if __name__ == "__main__":
    unittest.main()
