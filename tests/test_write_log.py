"""Task 141 (owner decision Q8, 2026-10-07; gauntlet 2 G2-15): the write log is bounded.

After every Edit/Write the state-echo hook appended a full copy of the edited file to
`~/.local/share/playbook/<project-slug>/write_log` — for ever, whatever the file's size, with
nothing reading it and no line of documentation (135 MB for one project on the owner's machine).
Now: a file over 1 MB is named, not copied; the log rotates at 10 MB and keeps one old file; a
log that predates the cap is renamed aside once and never touched again (nothing that exists
today is deleted by this change); `"write_log": false` in `.agent/config.json` turns it off.

Every case runs the real script as a subprocess.

Run: python3 -m unittest tests.test_write_log
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "playbook" / "scripts"
SCRIPT = SCRIPTS / "write_log.py"
MB = 1024 * 1024


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.project = self.root / "proj"
        (self.project / ".agent").mkdir(parents=True)
        self.log_dir = self.root / "share" / "slug"
        self.log = self.log_dir / "write_log"

    def edited(self, name: str, data: bytes) -> Path:
        f = self.project / name
        f.write_bytes(data)
        return f

    def run_script(self, file_path, stdin: "bytes | None" = None) -> subprocess.CompletedProcess:
        payload = stdin if stdin is not None else json.dumps(
            {"tool_name": "Edit", "tool_input": {"file_path": str(file_path)}}).encode("utf-8")
        return subprocess.run([sys.executable, str(SCRIPT), str(self.log_dir), str(self.project)],
                              input=payload, capture_output=True, timeout=60)

    def capped(self):
        """Put the log directory in the state every later run sees (the cap's marker exists)."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / "write_log.capped").write_text("", encoding="utf-8")

    def sparse(self, path: Path, size: int):
        with open(path, "wb") as fh:
            fh.truncate(size)


class Appends(_Fixture):
    def test_an_entry_is_a_header_and_the_files_bytes(self):
        f = self.edited("a.py", b"x = 1\n")
        r = self.run_script(f)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.log.read_bytes().decode("utf-8")
        self.assertRegex(text, r"^=== \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ " + str(f).replace("\\", "\\\\")
                         + r" \(6 bytes\) ===\nx = 1\n\n$")

    def test_two_edits_are_two_entries_in_order(self):
        f = self.edited("a.py", b"one\n")
        self.run_script(f)
        f.write_bytes(b"two\n")
        self.run_script(f)
        text = self.log.read_bytes()
        self.assertEqual(text.count(b"=== "), 2)
        self.assertLess(text.index(b"one\n"), text.index(b"two\n"))

    def test_nothing_to_copy_is_a_quiet_no_op(self):
        for payload in (json.dumps({"tool_input": {"file_path": str(self.project / "gone.py")}}).encode(),
                        json.dumps({"tool_input": {}}).encode(), b"not json", b""):
            r = self.run_script(None, stdin=payload)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.log_dir.exists())


class Bounds(_Fixture):
    def test_a_file_over_one_megabyte_is_named_not_copied(self):
        big = self.edited("big.bin", b"B" * (MB + 1))
        ok = self.edited("ok.bin", b"K" * MB)                      # exactly 1 MB is still copied
        self.run_script(big)
        self.run_script(ok)
        data = self.log.read_bytes()
        self.assertNotIn(b"BBBB", data)
        self.assertRegex(data.decode("utf-8", "replace").splitlines()[0],
                         r"big\.bin \(1048577 bytes\) — not copied: over 1 MB ===$")
        self.assertIn(b"K" * MB, data)

    def test_at_ten_megabytes_the_log_moves_to_one_old_file(self):
        self.capped()
        self.sparse(self.log, 10 * MB)
        f = self.edited("a.py", b"after the rotation\n")
        self.run_script(f)
        old = self.log_dir / "write_log.1"
        self.assertEqual(old.stat().st_size, 10 * MB)
        self.assertLess(self.log.stat().st_size, 1024)
        self.assertIn(b"after the rotation", self.log.read_bytes())
        # the next rotation replaces that one old file — the bound is two files
        self.sparse(self.log, 10 * MB + 5)
        self.run_script(f)
        self.assertEqual(old.stat().st_size, 10 * MB + 5)
        self.assertEqual(sorted(p.name for p in self.log_dir.iterdir()),
                         ["write_log", "write_log.1", "write_log.capped", "write_log.lock"])

    def test_while_the_entry_fits_under_ten_megabytes_nothing_rotates(self):
        self.capped()
        self.sparse(self.log, 10 * MB - 4096)
        self.run_script(self.edited("a.py", b"x\n"))
        self.assertFalse((self.log_dir / "write_log.1").exists())
        self.assertGreater(self.log.stat().st_size, 10 * MB - 4096)

    def test_the_live_log_never_passes_ten_megabytes(self):
        # impl panel r2 (opus): the size was checked BEFORE the append, so a 1 MB entry on a
        # log just under 10 MB made it ~11 MB — the documented bound was not the real one
        self.capped()
        self.sparse(self.log, 10 * MB - 10)
        self.run_script(self.edited("big.bin", b"K" * MB))
        self.assertLessEqual(self.log.stat().st_size, 10 * MB)
        self.assertEqual((self.log_dir / "write_log.1").stat().st_size, 10 * MB - 10)


class ALogThatPredatesTheCapIsNeverDeleted(_Fixture):
    def test_it_is_renamed_aside_once_byte_for_byte_and_survives_every_rotation(self):
        self.log_dir.mkdir(parents=True)
        legacy = b"=== 2026-07-01T00:00:00Z /p/old.py (4 bytes) ===\nold\n\n" * 3
        self.log.write_bytes(legacy)                                  # small: size is not the test
        f = self.edited("a.py", b"new\n")
        self.run_script(f)
        parked = [p for p in self.log_dir.iterdir() if p.name.startswith("write_log.pre-cap-")]
        self.assertEqual(len(parked), 1, [p.name for p in self.log_dir.iterdir()])
        self.assertEqual(parked[0].read_bytes(), legacy)
        self.assertTrue((self.log_dir / "write_log.capped").exists())
        self.assertNotIn(b"old\n", self.log.read_bytes())
        self.assertIn(b"new\n", self.log.read_bytes())
        for _ in range(3):                                            # rotations never touch it
            self.sparse(self.log, 10 * MB)
            self.run_script(f)
        self.assertEqual(parked[0].read_bytes(), legacy)
        self.assertEqual(len([p for p in self.log_dir.iterdir() if p.name.startswith("write_log.pre-cap-")]), 1)

    def test_a_first_run_with_no_old_log_parks_nothing(self):
        self.run_script(self.edited("a.py", b"x\n"))
        self.assertEqual(sorted(p.name for p in self.log_dir.iterdir()), ["write_log", "write_log.capped", "write_log.lock"])


class ImplPanelRound2b(_Fixture):
    """Impl panel round 2 re-run (opus, codex-high, codex-medium)."""

    def test_a_rotation_that_fails_skips_the_copy_instead_of_passing_the_cap(self):
        # codex-high, codex-medium (both reproduced it): a failed os.replace was swallowed and
        # the entry appended anyway — the live log grew past 10 MB
        self.capped()
        self.sparse(self.log, 10 * MB)
        old = self.log_dir / "write_log.1"
        old.mkdir()                                  # os.replace(file, dir) fails on every platform
        r = self.run_script(self.edited("a.py", b"not appended\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log.stat().st_size, 10 * MB)

    def test_a_filesystem_without_locks_still_logs(self):
        # opus: ENOLCK / EOPNOTSUPP were read as "someone holds the lock" — every edit waited
        # 1.5 s and nothing was logged. Only real contention waits; no lock support = no lock.
        import time
        code = (
            "import errno, sys, runpy\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "def no_locks(*a, **k):\n"
            "    raise OSError(errno.ENOLCK, 'No locks available')\n"
            "try:\n"
            "    import fcntl; fcntl.flock = no_locks\n"
            "except ImportError:\n"
            "    import msvcrt; msvcrt.locking = no_locks\n"
            "sys.argv = sys.argv[1:]\n"
            "sys.argv[0] = sys.argv[0] + '/write_log.py'\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n")
        f = self.edited("a.py", b"logged without a lock\n")
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), str(self.log_dir), str(self.project)],
                           input=json.dumps({"tool_input": {"file_path": str(f)}}).encode(),
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertIn(b"logged without a lock", self.log.read_bytes())


NO_LOCKS = (
    "import errno, os, sys, runpy\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "def no_locks(*a, **k):\n"
    "    raise OSError(errno.ENOLCK, 'No locks available')\n"
    "try:\n"
    "    import fcntl; fcntl.flock = no_locks\n"
    "except ImportError:\n"
    "    import msvcrt; msvcrt.locking = no_locks\n"
    "{extra}"
    "sys.argv = sys.argv[1:]\n"
    "sys.argv[0] = sys.argv[0] + '/write_log.py'\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n")


class PostD6Run1(_Fixture):
    """Task 141, post-D6 single judge run 1 (codex)."""

    def run_patched(self, f, extra=""):
        return subprocess.run([sys.executable, "-c", NO_LOCKS.replace("{extra}", extra), str(SCRIPTS),
                               str(self.log_dir), str(self.project)],
                              input=json.dumps({"tool_input": {"file_path": str(f)}}).encode(),
                              capture_output=True, timeout=60)

    def test_without_lock_support_a_held_fallback_lock_is_waited_for(self):
        # without flock/msvcrt the hooks ran unserialized: two could both decide an entry fits
        # and pass the cap together. The fallback is an atomic mkdir lock.
        import time
        self.capped()
        (self.log_dir / "write_log.lock.d").mkdir()
        f = self.edited("a.py", b"x\n")
        t0 = time.monotonic()
        r = self.run_patched(f)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreater(time.monotonic() - t0, 1.0)              # it waited
        self.assertFalse(self.log.exists(), "wrote without exclusivity")

    def test_a_stale_fallback_lock_is_cleared(self):
        self.capped()
        d = self.log_dir / "write_log.lock.d"
        d.mkdir()
        old = __import__("time").time() - 120
        os.utime(d, (old, old))
        r = self.run_patched(self.edited("a.py", b"after a stale lock\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"after a stale lock", self.log.read_bytes())
        self.assertFalse(d.exists())

    def test_a_size_that_cannot_be_read_skips_the_copy(self):
        # any getsize() error read as "no log" — a transient error on a FULL log appended past the cap
        self.capped()
        self.sparse(self.log, 10 * MB)
        extra = ("import os.path as _p\n"
                 "_real = _p.getsize\n"
                 "def _gs(x):\n"
                 "    if str(x).endswith('write_log'):\n"
                 "        raise PermissionError(13, 'denied')\n"
                 "    return _real(x)\n"
                 "_p.getsize = _gs\n")
        r = self.run_patched(self.edited("a.py", b"x\n"), extra=extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log.stat().st_size, 10 * MB)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_config_that_is_a_broken_link_turns_it_off(self):
        # post-D6 run 2: open() raises FileNotFoundError for a dangling symlink too — the
        # config entry EXISTS but cannot be read, so it must not read as "no config"
        (self.project / ".agent" / "config.json").symlink_to(self.root / "gone.json")
        r = self.run_script(self.edited("a.py", b"SECRET\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.log.exists())

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "POSIX permission bits, not as root")
    def test_a_config_that_cannot_be_reached_turns_it_off(self):
        # os.path.exists() is False when `.agent` cannot be traversed — the opt-out read as absent
        (self.project / ".agent" / "config.json").write_text('{"write_log": false}', encoding="utf-8")
        os.chmod(self.project / ".agent", 0o600)
        self.addCleanup(os.chmod, self.project / ".agent", 0o755)
        r = self.run_script(self.edited("a.py", b"SECRET\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.log.exists())


class ImplPanelRound2(_Fixture):
    def test_a_relative_path_is_the_projects(self):
        # agy [PRE-EXISTING]: a relative file_path was looked up from the hook's cwd
        self.edited("rel.py", b"relative\n")
        payload = json.dumps({"tool_input": {"file_path": "rel.py"}}).encode()
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.log_dir), str(self.project)],
                           input=payload, capture_output=True, timeout=60, cwd=str(self.root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"relative\n", self.log.read_bytes())

    def test_the_header_counts_the_bytes_copied(self):
        # sonnet: the header's size came from a stat taken before the read
        f = self.edited("a.py", b"12345\n")
        self.run_script(f)
        self.assertIn(b"(6 bytes) ===\n12345\n", self.log.read_bytes())


class Switch(_Fixture):
    def test_write_log_false_writes_nothing(self):
        (self.project / ".agent" / "config.json").write_text(json.dumps({"write_log": False}), encoding="utf-8")
        r = self.run_script(self.edited("a.py", b"x\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.log_dir.exists())

    def test_a_false_written_as_text_turns_it_off_too(self):
        # impl panel r2 (sonnet): `"write_log": "false"` left the copies of secrets running
        for off in ('"false"', '"off"', '"no"', '0', '"0"'):
            (self.project / ".agent" / "config.json").write_text('{"write_log": %s}' % off, encoding="utf-8")
            self.run_script(self.edited("a.py", b"x\n"))
            self.assertFalse(self.log.exists(), off)

    def test_a_config_that_cannot_be_read_turns_it_off(self):
        # impl panel r2 (codex-high): a half-written `{"write_log": false}` read as "on" — the
        # side that can copy a secret is the wrong side to fail on
        for cfg in ("not json", '{"write_log": fal'):
            (self.project / ".agent" / "config.json").write_text(cfg, encoding="utf-8")
            self.run_script(self.edited("a.py", b"x\n"))
            self.assertFalse(self.log.exists(), cfg)

    def test_any_other_config_keeps_it_on(self):
        for cfg in ('{"write_log": true}', '{"verify": "true"}', '["a list"]', "{}"):
            (self.project / ".agent" / "config.json").write_text(cfg, encoding="utf-8")
            self.run_script(self.edited("a.py", b"x\n"))
            self.assertTrue(self.log.exists(), cfg)
            self.log.unlink()


class ThroughTheHookLibrary(_Fixture):
    def test_write_log_append_goes_through_the_script(self):
        """The shell function the state-echo hook calls. The project path is the one bash
        sees (`pwd` — `/c/…` on Git-Bash, impl panel r1), and the log directory is found,
        not re-derived: exactly one appears under the temporary HOME."""
        home = self.root / "home"
        home.mkdir()
        big = self.edited("big.bin", b"B" * (MB + 1))
        small = self.edited("a.py", b"small\n")
        script = 'source "$1"; cd "$2" || exit 9; write_log_append "$3" "$(pwd)"; write_log_append "$4" "$(pwd)"'
        env = dict(os.environ, HOME=str(home))
        env.pop("BASH_ENV", None)
        mk = lambda p: json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(p)}})
        r = subprocess.run([bash_or_skip(), "-c", script, "x", str(SCRIPTS / "gate-echo-lib.sh"),
                            str(self.project), mk(big), mk(small)],
                           env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        logs = list((home / ".local" / "share" / "playbook").glob("*/write_log"))
        self.assertEqual(len(logs), 1, logs)
        data = logs[0].read_bytes()
        self.assertIn(b"not copied: over 1 MB", data)
        self.assertIn(b"small\n", data)
        self.assertNotIn(b"BBBB", data)


HOLD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
import write_log
lock = write_log._DirLock(sys.argv[2])
print("held" if lock.acquired else "not-held", flush=True)
sys.stdin.readline()                     # hold until the test says so
lock.release()
"""


class TheLockOnEveryPlatform(_Fixture):
    """Impl panel r2 (agy, sonnet): the lock tests ran on POSIX only, so the Windows
    `msvcrt` path was never exercised. A helper process holds the lock through
    write_log's own `_DirLock` — flock or msvcrt, whichever the platform uses."""

    def hold(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        h = subprocess.Popen([sys.executable, "-c", HOLD, str(SCRIPTS), str(self.log_dir)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (h.poll() is None) and h.kill())
        self.assertEqual(h.stdout.readline().strip(), "held")
        return h

    def test_a_held_lock_makes_the_script_wait_then_it_rotates_once(self):
        import time
        self.capped()
        self.sparse(self.log, 10 * MB)
        h = self.hold()
        f = self.edited("a.py", b"waited\n")
        proc = subprocess.Popen([sys.executable, str(SCRIPT), str(self.log_dir), str(self.project)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.stdin.write(json.dumps({"tool_input": {"file_path": str(f)}}).encode())
        proc.stdin.close()
        time.sleep(0.3)                  # well inside LOCK_WAIT (impl panel r2: 1.0 s was too tight)
        self.assertFalse((self.log_dir / "write_log.1").exists(), "rotated while the lock was held")
        h.stdin.write("\n")
        h.stdin.flush()
        h.wait(timeout=30)
        self.assertEqual(proc.wait(timeout=30), 0)
        self.assertEqual((self.log_dir / "write_log.1").stat().st_size, 10 * MB)
        self.assertIn(b"waited\n", self.log.read_bytes())

    def test_a_lock_that_is_never_released_costs_a_bounded_wait_and_writes_nothing(self):
        import time
        self.capped()
        self.hold()
        t0 = time.monotonic()
        r = self.run_script(self.edited("a.py", b"x\n"))
        self.assertEqual(r.returncode, 0)
        self.assertLess(time.monotonic() - t0, 4.5)          # inside the hook's 5 s timeout
        self.assertFalse(self.log.exists())


@unittest.skipIf(os.name == "nt", "POSIX permission bits")
class ImplPanelRound1(_Fixture):
    """Task 141, implementation panel round 1 (codex-high): private permissions."""

    def test_the_log_and_its_directory_are_private(self):
        # codex-high [PRE-EXISTING]: copies of edited secrets were 0644 under umask 022
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)
        self.run_script(self.edited("a.py", b"SECRET=1\n"))
        self.assertEqual(self.log_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)

    def test_existing_logs_are_made_private_too(self):
        self.log_dir.mkdir(parents=True, mode=0o755)
        os.chmod(self.log_dir, 0o755)
        self.log.write_bytes(b"legacy\n")
        os.chmod(self.log, 0o644)
        self.run_script(self.edited("a.py", b"x\n"))
        self.assertEqual(self.log_dir.stat().st_mode & 0o777, 0o700)
        for f in self.log_dir.iterdir():
            self.assertEqual(f.stat().st_mode & 0o077, 0, f.name)

if __name__ == "__main__":
    unittest.main()
