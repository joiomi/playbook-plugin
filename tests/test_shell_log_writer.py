"""The shell log's one writer, `shell_log_append` in gate-echo-lib.sh (PHASE-B item 2, task 221).

The owner, 2026-10-16: the shell log keeps only the agent's own commands ("Doar comenzile mele"), and the hook that
receives the exact command writes it ("Hook-ul scrie"). Before, a logger sourced into every non-interactive bash through
`BASH_ENV` wrote every simple command of every shell — on 2026-10-15, 45,083 lines for 1,666 commands of the agent's.

What is held here, with the REAL function run by bash:
  * one call = one physical line `YYYY-mm-dd HH:MM:SS | AGENT | <command>` in local time, read back exactly: `\\` is
    written `\\\\`, then a newline `\\n`, a CR `\\r` (plan panel 2026-10-16: without the first, a typed `\\n` and a real
    line break looked the same);
  * appenders side by side lose and tear no line, long lines too; a writer that gets no lock writes a spool file of
    its own, which the next writer holding the lock appends (no unlocked append: torn 64 KiB lines were measured);
  * past 50 MB the file is renamed to `bash_history.archived-<stamp>-<pid>` under the lock and nothing is carried; no
    lock, no rotation; a writer killed around the rename leaves the history under one of the two names;
  * nothing is written through a link, into a FIFO, or into a linked lane.

Run: python3 -m unittest tests.test_shell_log_writer
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts"
LIB = SCRIPTS / "gate-echo-lib.sh"
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| AGENT \| (.*)$", re.S)
LIMIT = 52428800


def decode(text: str) -> str:
    """The exact inverse of the writer's escape: `\\\\` → `\\`, `\\n` → LF, `\\r` → CR, read left to right."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text) and text[i + 1] in "\\nr":
            out.append({"\\": "\\", "n": "\n", "r": "\r"}[text[i + 1]])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _needs_flock(test):
    return unittest.skipUnless(shutil.which("flock"), "no flock(1) on this host")(test)


class _Lane(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.lane = self.root / "proj" / ".agent"
        (self.lane / "tasks").mkdir(parents=True)
        self.hist = self.lane / "bash_history"

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, **extra):
        env = dict(os.environ)
        for k in ("BASH_ENV", "ENV", "PLAYBOOK_SHELL_LOG_LOCK_WAIT", "PLAYBOOK_SANDBOXED", "PLAYBOOK_ROLE"):
            env.pop(k, None)
        env.update(extra)
        return env

    def argv(self, lane=None):
        return [bash_or_skip(), "-c", 'source "$1"; shell_log_append "$2" "$3"; echo "rc=$?"', "_", str(LIB),
                str(lane if lane is not None else self.lane)]

    def append(self, command, lane=None, timeout=20, **extra):
        r = subprocess.run(self.argv(lane) + [command], env=self.env(**extra), capture_output=True, text=True,
                           timeout=timeout)
        return r

    def start(self, command, **extra):
        return subprocess.Popen(self.argv() + [command], env=self.env(**extra), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)

    def lines(self, path=None):
        p = path or self.hist
        if not p.exists():
            return []
        data = p.read_text(encoding="utf-8")
        self.assertTrue(data == "" or data.endswith("\n"), "the file ends on a line")
        return data.split("\n")[:-1]

    def commands(self, path=None):
        out = []
        for ln in self.lines(path):
            m = LINE_RE.match(ln)
            assert m is not None, f"not a log line: {ln[:120]!r}"
            out.append(decode(m.group(2)))
        return out

    def spools(self):
        return sorted(p for p in self.lane.iterdir() if p.name.startswith("bash_history.spool"))


class OneCallOneLine(_Lane):
    def test_a_command_is_one_line_stamped_in_local_time(self):
        before = time.time()
        r = self.append("ls -la /tmp")
        after = time.time()
        self.assertEqual(r.stdout.strip(), "rc=0")
        self.assertEqual(r.stderr, "")
        lines = self.lines()
        self.assertEqual(len(lines), 1)
        m = LINE_RE.match(lines[0])
        assert m is not None
        self.assertEqual(m.group(2), "ls -la /tmp")
        stamp = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        self.assertLessEqual(int(before) - 1, stamp)
        self.assertLessEqual(stamp, after + 1)

    def test_the_escape_reads_back_exactly(self):
        cases = [
            "printf 'a\\nb'",                       # a typed backslash-n: NOT a line break
            "echo one\necho two",                   # a real line break
            "echo x\\\\ny",                          # backslashes before an n
            "cat <<'EOF'\n.claude/bin/tasks work 5\nEOF",
            "echo a\rb",                            # a CR
            "echo a b\x85c\x0bd\x0ce",          # what str.splitlines() would split on
            "trailing\\",                           # a backslash at the end
            "tab\there",
        ]
        for c in cases:
            self.append(c)
        self.assertEqual(self.commands(), cases)
        self.assertEqual(len(self.lines()), len(cases), "one physical line per command")

    def test_a_trailing_crlf_is_dropped_too(self):
        # impl panel r1 (gemini): a command ending in CRLF kept its CR as a visible `\r`
        self.append("ls -la\r\n")
        self.append("echo a\r\nb\r\n\r\n")
        self.assertEqual(self.commands(), ["ls -la", "echo a\r\nb"])

    def test_a_long_command_is_escaped_exactly_and_quickly(self):
        # past 64 KiB the escape is sed's (bash's pattern substitution is quadratic: a 5 MB command took seconds)
        unit = "cat <<'EOF'\nline with a \\ backslash, a typed \\n and a CR\r\nEOF\n"
        cmd = unit * 4000                       # ~200 KB, many lines
        src = self.root / "long.txt"
        src.write_text(cmd, encoding="utf-8")
        t0 = time.time()
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; c=$(cat "$3"; printf x); shell_log_append "$2" "${c%x}"',
                            "_", str(LIB), str(self.lane), str(src)], env=self.env(), capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.stderr, "")
        self.assertLess(time.time() - t0, 3.0)
        self.assertEqual(self.commands(), [cmd.rstrip("\n")])

    def _from_file(self, text, **extra):
        src = self.root / "cmd.txt"
        src.write_bytes(text.encode("utf-8"))
        t0 = time.time()
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; c=$(cat "$3"; printf x); shell_log_append "$2" "${c%x}"',
                            "_", str(LIB), str(self.lane), str(src)], env=self.env(**extra), capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.stderr, "")
        return time.time() - t0

    def test_two_hundred_thousand_trailing_newlines_are_dropped_quickly(self):
        # impl panel r2 (codex): dropping them one at a time was quadratic — 30,000 took 7.8 s through the hook; the
        # post-panel judge (run 1): a per-byte scan from the end was quadratic too (60,000: 6.1 s) — 200,000 here
        took = self._from_file("echo hi" + "\n" * 200000 + "\r\n" * 100)
        self.assertLess(took, 3.0)
        self.assertEqual(self.commands(), ["echo hi"])

    def test_the_cut_ends_on_a_whole_character_and_outside_an_escape(self):
        # impl panel r2 (gemini, sonnet): the 4 MiB cut could split a UTF-8 character or an escape pair
        for filler in ("é", "\\", "\nx"):
            with self.subTest(filler=repr(filler)):
                if self.hist.exists():
                    self.hist.unlink()
                self._from_file("echo " + filler * (5 * 1024 * 1024 // len(filler.encode("utf-8"))))
                raw = self.hist.read_bytes()
                raw.decode("utf-8")                         # whole characters: strict decode passes
                body = raw.decode("utf-8").split(" | AGENT | ", 1)[1].rsplit(" …[cut: ", 1)[0]
                trailing = len(body) - len(body.rstrip("\\"))
                self.assertEqual(trailing % 2, 0, "the cut left half an escape")

    def test_a_long_command_without_sed_is_still_a_line(self):
        # impl panel r2 (gemini): where `sed -z` fails the long command was dropped
        bindir = self.root / "nosed"
        bindir.mkdir()
        for tool in ("cat", "mv", "rm", "stat", "find", "flock"):
            real = shutil.which(tool)
            if real:
                (bindir / tool).symlink_to(real)
        (bindir / "sed").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (bindir / "sed").chmod(0o755)
        self._from_file("printf 'a'\n" * 20000, PATH=str(bindir))
        [line] = self.lines()
        self.assertIn("printf 'a'\\nprintf", line)
        self.assertRegex(line, r" …\[cut: \d+ more bytes\]$")

    def test_a_command_over_4_mib_is_cut_and_says_so(self):
        # impl panel r1 (grok, sonnet): a spool bigger than the takers' bound was never merged — no line is that big now
        big = self.root / "big.txt"
        big.write_text("echo " + "y" * (5 * 1024 * 1024), encoding="utf-8")
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; shell_log_append "$2" "$(cat "$3")"; echo "rc=$?"',
                            "_", str(LIB), str(self.lane), str(big)], env=self.env(), capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.stdout.strip(), "rc=0")
        [line] = self.lines()
        self.assertLess(len(line.encode("utf-8")), 4 * 1024 * 1024 + 200)
        self.assertRegex(line, r" …\[cut: \d+ more bytes\]$")

    def test_trailing_newlines_are_dropped_and_an_empty_or_blank_command_writes_nothing(self):
        self.append("echo hi\n\n")
        self.append("")
        self.append("\n\n")
        self.append("   \t ")
        self.assertEqual(self.commands(), ["echo hi"])

    def test_a_missing_lane_writes_nothing_and_says_nothing(self):
        r = self.append("ls", lane=self.root / "nope" / ".agent")
        self.assertEqual((r.stdout.strip(), r.stderr), ("rc=0", ""))
        self.assertFalse((self.root / "nope").exists())


class NeverThroughALink(_Lane):
    def test_a_linked_lane_is_not_written(self):
        outside = self.root / "outside"
        outside.mkdir()
        linked = self.root / "proj2" / ".agent"
        linked.parent.mkdir()
        linked.symlink_to(outside, target_is_directory=True)
        r = self.append("ls", lane=linked)
        self.assertEqual(r.stdout.strip(), "rc=0")
        self.assertEqual(list(outside.iterdir()), [])

    @_needs_flock
    def test_a_linked_lane_gets_no_spool_either(self):
        # mutation run (task 221 W9): with the lock held, the writer takes the spool path, which looks at no sink —
        # only the lane check at the top keeps a linked lane's target free of a spool file
        outside = self.root / "outside"
        outside.mkdir()
        linked = self.root / "proj3" / ".agent"
        linked.parent.mkdir()
        linked.symlink_to(outside, target_is_directory=True)
        fd = os.open(outside, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            r = self.append("ls", lane=linked, PLAYBOOK_SHELL_LOG_LOCK_WAIT="1")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(r.stdout.strip(), "rc=0")
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_history_that_is_a_link_is_left_alone(self):
        target = self.root / "target.txt"
        target.write_text("keep\n", encoding="utf-8")
        self.hist.symlink_to(target)
        self.append("ls")
        self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")
        self.assertTrue(self.hist.is_symlink())
        self.assertEqual(self.spools(), [])

    def test_a_dangling_history_link_makes_no_file(self):
        self.hist.symlink_to(self.root / "nowhere.txt")
        self.append("ls")
        self.assertFalse((self.root / "nowhere.txt").exists())

    def test_a_fifo_history_does_not_hang_the_writer(self):
        os.mkfifo(self.hist)
        t0 = time.time()
        r = self.append("ls", timeout=10)
        self.assertEqual(r.stdout.strip(), "rc=0")
        self.assertLess(time.time() - t0, 5)

    def test_a_history_that_is_a_directory_prints_nothing(self):
        self.hist.mkdir()
        r = self.append("ls")
        self.assertEqual((r.stdout.strip(), r.stderr), ("rc=0", ""))

    def test_a_planted_spool_link_is_not_followed(self):
        target = self.root / "secret.txt"
        target.write_text("not a log line\n", encoding="utf-8")
        (self.lane / "bash_history.spool-20260101-000000-1-1").symlink_to(target)
        self.append("ls")
        self.assertEqual(self.commands(), ["ls"])
        self.assertEqual(target.read_text(encoding="utf-8"), "not a log line\n")


@_needs_flock
class SideBySide(_Lane):
    def test_writers_side_by_side_lose_and_tear_no_line(self):
        n_proc, per = 8, 6
        script = ('source "$1"; for i in $(seq 1 %d); do shell_log_append "$2" "w$3-$i $(printf %%65536s | tr " " x)";'
                  ' done' % per)
        procs = [subprocess.Popen([bash_or_skip(), "-c", script, "_", str(LIB), str(self.lane), str(k)],
                                  env=self.env(PLAYBOOK_SHELL_LOG_LOCK_WAIT="2"), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) for k in range(n_proc)]
        for p in procs:
            p.wait(timeout=120)
        # a writer the lock was not given to in time left a spool: the next writer takes it in
        self.append("closing")
        got = self.commands()
        self.assertEqual(got[-1], "closing")
        body = "x" * 65536
        want = sorted(f"w{k}-{i} {body}" for k in range(n_proc) for i in range(1, per + 1))
        self.assertEqual(sorted(got[:-1]), want)
        self.assertEqual(self.spools(), [])

    def test_a_held_lock_gives_a_spool_that_the_next_writer_appends(self):
        fd = os.open(self.lane, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            t0 = time.time()
            r = self.append("while-held", PLAYBOOK_SHELL_LOG_LOCK_WAIT="1")
            took = time.time() - t0
            self.assertEqual(r.stdout.strip(), "rc=0")
            self.assertLess(took, 3.0)
            self.assertFalse(self.hist.exists(), "no append without the lock")
            spools = self.spools()
            self.assertEqual(len(spools), 1)
            self.assertEqual(self.commands(spools[0]), ["while-held"])
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.append("after")
        self.assertEqual(self.commands(), ["while-held", "after"])
        self.assertEqual(self.spools(), [])


@_needs_flock
class Rotation(_Lane):
    def _big(self):
        with open(self.hist, "wb") as f:
            f.write(b"2026-01-01 00:00:00 | AGENT | old line\n")
            f.truncate(LIMIT + 1)            # sparse: no 50 MB written

    def archives(self):
        return sorted(p for p in self.lane.iterdir() if p.name.startswith("bash_history.archived-"))

    def test_a_history_past_50_mb_is_renamed_and_nothing_is_carried(self):
        self._big()
        self.append("new line")
        arch = self.archives()
        self.assertEqual(len(arch), 1)
        self.assertRegex(arch[0].name, r"^bash_history\.archived-\d{8}-\d{6}-\d+$")
        self.assertEqual(arch[0].stat().st_size, LIMIT + 1)
        self.assertEqual(self.commands(), ["new line"])

    def test_a_small_history_is_not_renamed(self):
        self.append("one")
        self.append("two")
        self.assertEqual(self.archives(), [])
        self.assertEqual(self.commands(), ["one", "two"])

    def test_no_lock_no_rotation(self):
        self._big()
        fd = os.open(self.lane, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.append("spooled", PLAYBOOK_SHELL_LOG_LOCK_WAIT="1")
            self.assertEqual(self.archives(), [])
            self.assertEqual(self.hist.stat().st_size, LIMIT + 1)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _tools_without_flock(self, flock_body=None):
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        for tool in ("cat", "mv", "rm", "stat", "date", "find"):
            real = shutil.which(tool)
            if real and not (bindir / tool).exists():
                (bindir / tool).symlink_to(real)
        if flock_body is not None:
            (bindir / "flock").write_text("#!/bin/sh\n" + flock_body + "\n", encoding="utf-8")
            (bindir / "flock").chmod(0o755)
        return str(bindir)

    def test_a_host_without_flock_appends_directly_and_never_rotates(self):
        # impl panel r1 (sonnet, opus): with no lock to take, every line became a spool nothing ever merged
        self._big()
        self.append("no flock", PATH=self._tools_without_flock())
        self.assertEqual(self.archives(), [])
        self.assertEqual(self.spools(), [])
        self.assertTrue(self.hist.read_bytes().endswith(b" | AGENT | no flock\n"), "appended to the live file")

    def test_a_flock_that_cannot_wait_is_no_lock_to_take(self):
        # busybox's flock has no -w: it refuses at once — not a wait that ran out, so no spool
        self.append("refused at once", PATH=self._tools_without_flock("exit 1"))
        self.assertEqual(self.spools(), [])
        self.assertEqual(self.commands(), ["refused at once"])

    def test_a_stale_spooling_file_is_swept_and_a_fresh_one_kept(self):
        # impl panel r1 (opus): the temp of a writer killed mid-write was never cleaned up
        old = self.lane / "bash_history.spooling-20260101-000000-1-1"
        old.write_text("2026-01-01 00:00:00 | AGENT | half\n", encoding="utf-8")
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        fresh = self.lane / "bash_history.spooling-20260101-000000-2-2"
        fresh.write_text("2026-01-01 00:00:00 | AGENT | being written\n", encoding="utf-8")
        self.append("ls")
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual(self.commands(), ["ls"])

    def test_a_writer_killed_around_the_rename_leaves_the_history_under_one_name(self):
        self._big()
        bindir = self.root / "bin"
        bindir.mkdir()
        real_mv = shutil.which("mv")
        for when, body in (("before", f'sleep 30; exec {real_mv} "$@"'), ("after", f'{real_mv} "$@"; sleep 30')):
            with self.subTest(when=when):
                for p in self.archives():
                    p.unlink()
                if not self.hist.exists():
                    self._big()
                (bindir / "mv").write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
                (bindir / "mv").chmod(0o755)
                p = self.start("killed", PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
                time.sleep(1.5)
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=10)
                names = [x for x in (self.hist, *self.archives()) if x.exists()]
                sizes = sorted(x.stat().st_size for x in names)
                self.assertIn(LIMIT + 1, sizes, "the old history survives under one name")
                self.assertEqual(len([s for s in sizes if s == LIMIT + 1]), 1)
        (bindir / "mv").unlink()
        self.append("next")
        self.assertEqual(self.commands()[-1], "next")


class TheWritersFilesAreIgnored(unittest.TestCase):
    """The files the writer makes beside the history are runtime state: the seeded `.gitignore` block covers them,
    or an archive (~50 MB) and a spool show in `git status` and in the close's tree fingerprint (task 088's rule)."""

    def test_the_seeded_ignore_block_covers_archives_and_spools(self):
        import importlib.util
        git = shutil.which("git")
        if not git:
            self.skipTest("git not on PATH")
        merge = SCRIPTS / "claude-md-merge.py"
        spec = importlib.util.spec_from_file_location("claude_md_merge", merge)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run([git, "init", "-q", str(repo)], check=True)
            (repo / ".gitignore").write_bytes(("\n".join(mod.GITIGNORE_ENTRIES) + "\n").encode())
            for rel in (".agent/bash_history", ".agent/alice/bash_history",
                        ".agent/bash_history.archived-20261016-101010-42",
                        ".agent/alice/bash_history.archived-20261016-101010-42",
                        ".agent/bash_history.spool-20261016-101010-42-123",
                        ".agent/alice/bash_history.spool-20261016-101010-42-123",
                        ".agent/bash_history.spooling-20261016-101010-42-123",
                        ".agent/alice/bash_history.spooling-20261016-101010-42-123"):
                r = subprocess.run([git, "-C", str(repo), "check-ignore", "-q", rel])
                self.assertEqual(r.returncode, 0, f"{rel} is not ignored")


if __name__ == "__main__":
    unittest.main()
