"""Task 105: session identity under the Claude Code background daemon.

Measured 2026-09-27 (record: .agent/tasks/105-*/task.md "## Measurement"):
Claude Code hosts background sessions under a daemon that is shared by every
session it hosts —

    claude.exe --session-id …          (the hosted session; comm `claude.exe`)
      └ claude bg-pty-host --bg-pty-host /tmp/cc-daemon-1000/…   (comm `claude.exe`)
          └ claude.exe daemon run …    (comm `claude.exe`)

The ancestor walk matched only `claude|codex|agy|grok|pi`, so a hosted session
was invisible and every hook fell back to its own per-call `pid-$PPID` (a new,
immediately dead session per hook call → "No active task"). Recognizing
`claude.exe` naively would instead walk up to the pty host / daemon, which all
hosted sessions share, so they would overwrite each other's pointers.

Contract pinned here:
  * both resolvers (bash gate-echo-lib.sh, python tasks/core.py) recognize
    `claude`, `claude.exe` and `claude*`, and give the SAME answer (parity);
  * a daemon process (argv[1] `bg-pty-host` / `daemon`, or `--bg-pty-host`) is
    never a session root and the walk never crosses it;
  * with no agent below the daemon and no PLAYBOOK_SESSION_ID the id is
    unresolved (""): no hook and no CLI command creates a `pid-*` dir or
    touches any pointer; each prints ONE stderr line;
  * `tasks doctor` reports the dead session dirs the GC reclaimed.

Owner decision 2026-09-28 (option b): on Linux both resolvers read
`/proc/<pid>/status` + `/proc/<pid>/cmdline` (exact argv); `ps` is only the
fallback where `<proc root>/self` is missing (macOS), and its argv is heuristic.
Every tree below is therefore written TWICE — as a `/proc` fixture under
`PLAYBOOK_PROC_ROOT` and as a fake-`ps` table — and the `*Proc` classes run the
same tests on the /proc path while the base classes force the `ps` path
(PLAYBOOK_PROC_ROOT pointed at a directory with no `self`).

The process tree is faked by a `ps` shim first on PATH that answers from a
table; any pid NOT in the table (the real test process, which is the parent of
every subprocess below) is reported as a `bash` whose parent is the table's
first row — so the walk starts from a real $PPID and continues into the table.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"
GATE_LIB = SCRIPTS / "gate-echo-lib.sh"

UNRESOLVED_MARK = "no session identity"
MSG_NAMES_PS_CAUSE = "could not read an ancestor"

# (comm, ps args line, EXACT argv as /proc/<pid>/cmdline shows it) — the argv
# column is the live measurement of 2026-09-28 (task 105 record): the pty host
# and the spare REWRITE their process title, so argv[0] is ONE argument
# "claude bg-pty-host" / "claude bg-spare" (a space inside it), not two.
PTY_HOST = ("claude.exe",
            "claude bg-pty-host --bg-pty-host /tmp/cc-daemon-1000/020c31ec/pty/2ad1fc4c.sock"
            " 95 62 -- /opt/claude-code/bin/claude.exe --session-id 2ad1fc4c",
            ["claude bg-pty-host", "--bg-pty-host", "/tmp/cc-daemon-1000/020c31ec/pty/2ad1fc4c.sock",
             "95", "62", "--", "/opt/claude-code/bin/claude.exe", "--session-id", "2ad1fc4c"])
DAEMON = ("claude.exe",
          "/opt/claude-code/bin/claude.exe daemon run --json-path /home/u/.claude/daemon.json")
HOSTED = ("claude.exe", "/opt/claude-code/bin/claude.exe --session-id 2ad1fc4c --fork-session")
SPARE = ("claude.exe", "claude bg-spare --bg-spare /tmp/cc-daemon-1000/020c31ec/spare/a6.claim.sock",
         ["claude bg-spare", "--bg-spare", "/tmp/cc-daemon-1000/020c31ec/spare/a6.claim.sock"])
TERMINAL = ("claude", "claude")
SYSTEMD = ("systemd", "/lib/systemd/systemd --user")
SHELL = ("bash", "bash")

FAKE_PS = textwrap.dedent('''\
    #!/usr/bin/env python3
    """ps shim: answers `ps -p PID -o f1=,f2=` from $FAKE_PS_TABLE (TSV pid ppid comm args)."""
    import os, sys
    rows, root = {}, None
    with open(os.environ["FAKE_PS_TABLE"], encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\\n")
            if not line:
                continue
            pid, ppid, comm, args = line.split("\\t")
            root = root or pid
            rows[pid] = {"pid": pid, "ppid": ppid, "comm": comm, "args": args}
    argv = sys.argv[1:]
    if "-p" not in argv or "-o" not in argv:
        os.execv("/bin/ps", ["ps"] + argv)
    pid = argv[argv.index("-p") + 1]
    fields = [f.rstrip("=") for f in argv[argv.index("-o") + 1].split(",")]
    # Round-1 R1-1: a pid whose args probe FAILS (transient ps error).
    if "args" in fields and pid in os.environ.get("FAKE_PS_FAIL_ARGS", "").split(","):
        sys.exit(1)
    # Round-2: fail the args probe for this pid ONCE (a hiccup, then fine).
    once = os.environ.get("FAKE_PS_FAIL_ARGS_ONCE", "")
    if "args" in fields and pid == once:
        flag = os.environ["FAKE_PS_TABLE"] + ".failed-once"
        if not os.path.exists(flag):
            open(flag, "w").close()
            sys.exit(1)
    # Task 106: pids listed here do not exist (ps answers nothing, exit 1).
    if pid in os.environ.get("FAKE_PS_DEAD", "").split(","):
        sys.exit(1)
    # Round-2: every query about this pid fails ("*" = ps unusable for all).
    fail_all = os.environ.get("FAKE_PS_FAIL_ALL", "")
    if fail_all == "*" or pid in fail_all.split(","):
        sys.exit(1)
    # Single-judge run 2: queries about this pid are slow (past a 1 s deadline).
    if pid == os.environ.get("FAKE_PS_SLOW", ""):
        import time
        time.sleep(float(os.environ.get("FAKE_PS_SLOW_S", "2.5")))
    # Round-2: this pid's argv carries a non-UTF-8 byte.
    if "args" in fields and pid == os.environ.get("FAKE_PS_RAW_ARGS", ""):
        sys.stdout.buffer.write(b"claude \\xff--x\\n")
        sys.exit(0)
    row = rows.get(pid) or {"pid": pid, "ppid": root or "1", "comm": "bash", "args": "bash"}
    # Task 106: `stat` — Z for the pids in FAKE_PS_ZOMBIE, S otherwise.
    zst = "Z" if pid in os.environ.get("FAKE_PS_ZOMBIE", "").split(",") else "S"
    row = dict(row, stat=zst, state=zst)
    out = " ".join(row[f] for f in fields)
    # Round-1 R1-3: procps clips the line to $COLUMNS even on a pipe unless -ww.
    cols = os.environ.get("COLUMNS", "")
    if cols.isdigit() and "-ww" not in argv:
        out = out[:int(cols)]
    print(out)
''')


def _table(rows):
    """rows: list of (pid, ppid, (comm, args[, argv])) — first row is the walk's entry."""
    return "".join(f"{pid}\t{ppid}\t{ca[0]}\t{ca[1]}\n" for pid, ppid, ca in rows)


def _argv(ca):
    """The exact argv of a process constant: its explicit list, else args split."""
    return list(ca[2]) if len(ca) > 2 else ca[1].split()


def write_proc_tree(root, rows, *, omit_status=(), omit_cmdline=(), raw_cmdline=None, zombie=()):
    """A /proc fixture: `<pid>/status` (Name:, PPid:) + `<pid>/cmdline` (NUL-
    separated argv) per row, `self/` so the resolvers take the /proc path, and
    the REAL parent of every subprocess (this test process) as a `bash` whose
    parent is the first row — the same entry trick the fake `ps` uses."""
    import shutil
    if root.exists():
        shutil.rmtree(root)
    (root / "self").mkdir(parents=True)
    first = rows[0][0] if rows else 1
    entries = [(os.getpid(), first, ("bash", "bash"))] + list(rows)
    raw_cmdline = raw_cmdline or {}
    for pid, ppid, ca in entries:
        d = root / str(pid)
        d.mkdir(exist_ok=True)
        if pid not in omit_status:
            state = "Z (zombie)" if pid in zombie else "S (sleeping)"
            (d / "status").write_text(f"Name:\t{ca[0]}\nUmask:\t0002\nState:\t{state}\n"
                                      f"PPid:\t{ppid}\n", encoding="utf-8")
        if pid in raw_cmdline:
            (d / "cmdline").write_bytes(raw_cmdline[pid])
        elif pid not in omit_cmdline:
            (d / "cmdline").write_bytes(b"".join(a.encode("utf-8") + b"\0" for a in _argv(ca)))


# name → (rows, expected id; "PPID" = pid-<this test process>, the no-agent fallback)
VECTORS = {
    "terminal claude": ([(400, 300, TERMINAL), (300, 1, SHELL)], "pid-400"),
    "hosted claude.exe below pty host": (
        [(400, 300, HOSTED), (300, 200, PTY_HOST), (200, 1, SYSTEMD)], "pid-400"),
    "macOS full-path comm": (
        [(400, 300, ("/Applications/Claude.app/Contents/MacOS/claude", "claude")),
         (300, 1, SHELL)], "pid-400"),
    "claude-* comm variant": ([(400, 300, ("claude-code", "claude-code")), (300, 1, SHELL)],
                              "pid-400"),
    "nested claude → highest": (
        [(450, 400, TERMINAL), (400, 300, TERMINAL), (300, 1, SHELL)], "pid-400"),
    "spare session below pty host": (
        [(460, 300, SPARE), (300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)],
        "pid-460"),
    "daemon only (pty host → daemon)": (
        [(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)], ""),
    "daemon between two sessions → the lower one": (
        [(450, 300, HOSTED), (300, 250, PTY_HOST), (250, 400, DAEMON),
         (400, 350, TERMINAL), (350, 1, SHELL)], "pid-450"),
    "daemon only, terminal claude above it → never crossed": (
        [(300, 250, PTY_HOST), (250, 400, DAEMON), (400, 350, TERMINAL), (350, 1, SHELL)], ""),
    "codex unchanged": ([(400, 300, ("codex", "codex")), (300, 1, SHELL)], "pid-400"),
    "no agent → parent pid": ([(300, 1, SHELL)], "PPID"),
}


class _FakePsMixin(unittest.TestCase):
    PROC = False        # True: resolvers read the /proc fixture; False: force the ps path

    def setUp(self):
        if os.getpid() < 1000:
            # Every fixture pid is < 1000 and this process is the walk's entry
            # (post-rewrite single judge run 2): a collision would start the
            # walk AT a row instead of below it.
            self.skipTest(f"test process pid {os.getpid()} collides with the fixture pid range (< 1000)")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        ps = self.fakebin / "ps"
        ps.write_text(FAKE_PS, encoding="utf-8")
        ps.chmod(0o755)
        self.table = self.tmp / "ps-table.tsv"
        self.proc = self.tmp / "proc"

    def set_tree(self, rows, **proc_opts):
        self.table.write_text(_table(rows), encoding="utf-8")
        write_proc_tree(self.proc, rows, **proc_opts)

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "CLAUDE_ENV_FILE", "BASH_ENV")}
        env["PATH"] = f"{self.fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        env["FAKE_PS_TABLE"] = str(self.table)
        env["PYTHONPATH"] = str(PLUGIN)
        env["PLAYBOOK_PROC_ROOT"] = str(self.proc if self.PROC else self.tmp / "no-proc")
        env.update(extra)
        return env

    def bash_resolve(self, **extra):
        r = subprocess.run([bash_or_skip(), "-c", f"source '{GATE_LIB.as_posix()}' && resolve_session_id"],
                           env=self.env(**extra), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def py_resolve(self, **extra):
        r = subprocess.run([sys.executable, "-c",
                            "import tasks.core as c; print(c.resolve_session_id())"],
                           env=self.env(**extra), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()


# Only consulted for a process whose comm is claude* (the walk never asks otherwise).
DAEMON_ARGS = {
    PTY_HOST[1]: True,
    DAEMON[1]: True,
    "claude.exe daemon run": True,
    "/usr/lib/claude daemon run --x": True,
    "claude --bg-pty-host /tmp/s.sock": True,
    HOSTED[1]: False,
    SPARE[1]: False,
    "claude": False,
    "claude fix the bg-pty-host bug": False,        # a prompt argument, not a daemon
    "claude -p daemon run": False,                  # subcommand must follow argv[0]
    "claude daemon": False,
    "bg-pty-host": False,
    # Round-1 R1-2: prompt text naming a marker is not a daemon.
    "claude -p please explain --bg-pty-host in this plugin": False,
    "claude fix the claude daemon run bug": False,
    "claude fix the --bg-pty-host bug": False,
    "/home/u/claude/bin/claude.exe daemon run --x": True,
    # Owner decision 2026-09-28: an argv[0] with spaces is AMBIGUOUS on the ps
    # line — a declared macOS residual (the /proc path reads it exactly, below).
    "/Applications/Claude Code.app/Contents/MacOS/claude.exe daemon run": False,
    "/Applications/Claude Code.app/Contents/MacOS/claude.exe --session-id x": False,
    # Round-2: an argv[0] path whose directory is itself named `claude`.
    "/tmp/claude build/bin/claude.exe daemon run": False,       # residual (ps line)
    "/tmp/claude build/bin/claude.exe --session-id x": False,
    # Single-judge run 2: a bare `claude` whose ARGUMENT is a path is not argv[0].
    "claude /tmp/claude daemon run": False,
    # Post-D6 run 5 (S5-1, S5-2) — red-first vectors for the owner's option (b).
    "/opt/my claude helper/bin/claude.exe daemon run": False,   # S5-1: residual on the ps line
    "claude\ndaemon run": True,
    "claude bg-pty-host\r": True,
}


# EXACT argv (the /proc path): the spaced argv[0] cases the ps line cannot read.
DAEMON_ARGV = [
    (PTY_HOST[2], True),                      # live 2026-09-28: argv[0] = "claude bg-pty-host"
    (SPARE[2], False),                        # live: argv[0] = "claude bg-spare" — a session
    (["claude bg-pty-host"], True),           # the rewritten title alone
    # Post-rewrite single judge run 1: \r / \n INSIDE argv[0] split like str.split().
    (["claude bg-pty-host\r", "--bg-pty-host", "/tmp/s.sock"], True),
    (["claude\ndaemon", "run"], True),
    (["claude\rbg-pty-host"], True),
    (["/opt/my claude helper/bin/claude.exe bg-pty-host", "--bg-pty-host"], True),
    (["claude bg-spare", "daemon", "run"], False),
    (["claude", "bg-pty-host", "--bg-pty-host", "/tmp/s.sock"], True),
    (["/opt/claude-code/bin/claude.exe", "daemon", "run", "--json-path", "x"], True),
    (["claude.exe", "--bg-pty-host", "/tmp/s.sock"], True),
    (["/opt/my claude helper/bin/claude.exe", "daemon", "run"], True),           # S5-1
    (["/Applications/Claude Code.app/Contents/MacOS/claude.exe", "daemon", "run"], True),
    (["/tmp/claude build/bin/claude.exe", "daemon", "run"], True),
    (["/tmp/claude build/bin/claude.exe", "--session-id", "x"], False),
    (["claude", "/tmp/claude", "daemon", "run"], False),
    (["claude", "-p", "please explain --bg-pty-host in this plugin"], False),
    (["claude", "fix the claude daemon run bug"], False),
    (["claude", "daemon run"], False),                    # one argument, not two
    (["claude", "bg-pty-host\r"], False),                # exact: not the marker
    (["claude", "daemon"], False),
    (["claude"], False),
    (["bg-pty-host"], False),
    (["node", "claude", "daemon", "run"], False),
    ([], False),
]


class DaemonArgvDetectorParity(unittest.TestCase):
    """bash `_is_daemon_argv "$@"` and python `_is_daemon_argv(list)` agree on
    exact argv lists (the /proc path's detector)."""

    def test_bash_equals_python_equals_expected(self):
        sys.path.insert(0, str(PLUGIN))
        try:
            import tasks.core as core
        finally:
            sys.path.remove(str(PLUGIN))
        for argv, want in DAEMON_ARGV:
            with self.subTest(argv=argv):
                # argv travels NUL-separated on stdin, not on the command line:
                # on Windows the MSYS runtime rebuilds argv from the Windows
                # command line and drops a trailing \r (CI run 36401496454).
                r = subprocess.run([bash_or_skip(), "-c",
                                    f"source '{GATE_LIB.as_posix()}' && A=(); "
                                    "while IFS= read -r -d '' a; do A[${#A[@]}]=\"$a\"; done; "
                                    "_is_daemon_argv \"${A[@]}\""],
                                   input=b"".join(a.encode("utf-8") + b"\0" for a in argv),
                                   capture_output=True, timeout=30)
                self.assertEqual(r.returncode == 0, want, f"bash on {argv!r}: rc={r.returncode}")
                self.assertEqual(core._is_daemon_argv(argv), want, f"python on {argv!r}")


class DaemonDetectorParity(unittest.TestCase):
    """bash `_is_daemon_args` and python `_is_daemon_args` agree on every `ps`
    line (the macOS fallback: the line split on whitespace — \n/\r included —
    then the same argv detector)."""

    def test_bash_equals_python_equals_expected(self):
        sys.path.insert(0, str(PLUGIN))
        try:
            import tasks.core as core
        finally:
            sys.path.remove(str(PLUGIN))
        for args, want in DAEMON_ARGS.items():
            with self.subTest(args=args):
                # stdin, not argv (MSYS drops a trailing \r from Windows argv)
                r = subprocess.run([bash_or_skip(), "-c",
                                    f"source '{GATE_LIB.as_posix()}' && IFS= read -r -d '' a; "
                                    "_is_daemon_args \"$a\""],
                                   input=args.encode("utf-8") + b"\0",
                                   capture_output=True, timeout=30)
                self.assertEqual(r.returncode == 0, want, f"bash on {args!r}: rc={r.returncode}")
                self.assertEqual(core._is_daemon_args(args), want, f"python on {args!r}")


class NoticeParity(unittest.TestCase):
    def test_bash_and_python_print_the_same_notice(self):
        import re
        sys.path.insert(0, str(PLUGIN))
        try:
            import tasks.core as core
        finally:
            sys.path.remove(str(PLUGIN))
        m = re.search(r'^SESSION_UNRESOLVED_MESSAGE="(.*)"$',
                      GATE_LIB.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), core.SESSION_UNRESOLVED_MESSAGE)


class ResolverParity(_FakePsMixin):
    """Bash and Python walk the same tree to the same id, on every vector."""

    def test_every_vector_bash_equals_python_equals_expected(self):
        for name, (rows, expected) in VECTORS.items():
            with self.subTest(vector=name):
                self.set_tree(rows)
                want = f"pid-{os.getpid()}" if expected == "PPID" else expected
                py, sh = self.py_resolve(), self.bash_resolve()
                self.assertEqual(py, sh, f"parity broken on {name!r}: python {py!r} vs bash {sh!r}")
                self.assertEqual(py, want, f"{name!r}: got {py!r}, want {want!r}")

    def test_claude_exe_ancestor_is_recognized(self):
        self.set_tree(VECTORS["hosted claude.exe below pty host"][0])
        self.assertEqual(self.py_resolve(), "pid-400")
        self.assertEqual(self.bash_resolve(), "pid-400")

    def test_pty_host_is_never_the_root(self):
        for name in ("hosted claude.exe below pty host", "daemon only (pty host → daemon)",
                     "daemon between two sessions → the lower one"):
            with self.subTest(vector=name):
                self.set_tree(VECTORS[name][0])
                for got in (self.py_resolve(), self.bash_resolve()):
                    self.assertNotIn(got, ("pid-300", "pid-250"),
                                     f"{name!r}: a daemon process became the session root")

    def test_env_is_the_only_identity_under_the_daemon(self):
        # Task 106: an env `pid-<digits>` must name a LIVE AGENT to be honored,
        # so the fixture now carries pid 777 as a (non-ancestor) claude.
        self.set_tree(VECTORS["daemon only (pty host → daemon)"][0] + [(777, 1, TERMINAL)])
        self.assertEqual(self.py_resolve(PLAYBOOK_SESSION_ID="pid-777"), "pid-777")
        self.assertEqual(self.bash_resolve(PLAYBOOK_SESSION_ID="pid-777"), "pid-777")

    def test_unsafe_env_under_the_daemon_is_still_unresolved(self):
        # Sanitization neutralizes `../tasks` to the DERIVED id — under the
        # daemon that is "" (never a made-up pid).
        self.set_tree(VECTORS["daemon only (pty host → daemon)"][0])
        self.assertEqual(self.py_resolve(PLAYBOOK_SESSION_ID="../tasks"), "")
        self.assertEqual(self.bash_resolve(PLAYBOOK_SESSION_ID="../tasks"), "")


# The measured daemon argv (pid 1576599, 2026-09-27): `daemon run` sits past column 80.
LONG_DAEMON = ("claude.exe",
               "/home/mihnea/.nvm/versions/node/v24.11.0/lib/node_modules/@anthropic-ai/claude-code/bin/"
               "claude.exe daemon run --json-path /home/mihnea/.claude/daemon.json --log-file /home/mihnea/.claude/daemon.log")


class ProbeFailuresFailClosed(_FakePsMixin):
    """Round 1: an ancestor the walk cannot fully read is never promoted to root."""

    def test_failed_args_probe_on_the_pty_host_is_not_a_root(self):
        self.set_tree([(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)])
        for got in (self.py_resolve(FAKE_PS_FAIL_ARGS="300"), self.bash_resolve(FAKE_PS_FAIL_ARGS="300")):
            self.assertEqual(got, "", "an uninspectable claude* ancestor became the session root")

    def test_failed_args_probe_keeps_the_session_found_below(self):
        self.set_tree([(400, 300, HOSTED), (300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)])
        for got in (self.py_resolve(FAKE_PS_FAIL_ARGS="300"), self.bash_resolve(FAKE_PS_FAIL_ARGS="300")):
            self.assertEqual(got, "pid-400")

    def test_one_args_hiccup_does_not_unresolve_a_terminal_session(self):
        for resolve in (self.py_resolve, self.bash_resolve):
            with self.subTest(resolver=resolve.__name__):
                flag = Path(str(self.table) + ".failed-once")
                if flag.exists():
                    flag.unlink()
                self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)])
                self.assertEqual(resolve(FAKE_PS_FAIL_ARGS_ONCE="400"), "pid-400")

    def test_unreadable_ancestor_mid_walk_is_unresolved(self):
        # hop 1 (the real parent) reads fine; the next pid cannot be read at all
        self.set_tree([(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)])
        for got in (self.py_resolve(FAKE_PS_FAIL_ALL="300"), self.bash_resolve(FAKE_PS_FAIL_ALL="300")):
            self.assertEqual(got, "", "an unreadable ancestry invented a pid-$PPID session")

    def test_no_ps_binary_keeps_the_legacy_fallback(self):
        # control: no `ps` at all (a minimal container) is not daemon evidence
        import shutil
        nops = self.tmp / "nops"
        nops.mkdir()
        for tool in ("awk", "uname"):
            (nops / tool).symlink_to(shutil.which(tool))
        want = f"pid-{os.getpid()}"
        self.assertEqual(self.py_resolve(PATH=str(nops)), want)
        self.assertEqual(self.bash_resolve(PATH=str(nops)), want)

    def test_ps_failing_on_the_first_hop_is_unresolved(self):
        # single-judge (post-D6): a ps that exists but cannot answer (timeout,
        # error) is NOT "no ps" — python fell back to pid-$PPID while bash,
        # with no deadline, walked on: split-brain. Both now say unresolved.
        self.set_tree([(300, 1, SHELL)])
        self.assertEqual(self.py_resolve(FAKE_PS_FAIL_ALL="*"), "")
        self.assertEqual(self.bash_resolve(FAKE_PS_FAIL_ALL="*"), "")

    def test_slow_ps_does_not_split_python_from_bash(self):
        # bash has no deadline; python must wait for the same slow `ps` (single
        # judge runs 2+3: any shorter python deadline split the two).
        cases = {
            "slow higher claude": ([(450, 400, TERMINAL), (400, 300, TERMINAL), (300, 1, SHELL)], "400", "pid-400"),
            "slow non-agent above claude": ([(400, 300, TERMINAL), (300, 200, SHELL), (200, 1, SHELL)], "300", "pid-400"),
        }
        for name, (rows, slow, want) in cases.items():
            with self.subTest(case=name):
                self.set_tree(rows)
                py, sh = self.py_resolve(FAKE_PS_SLOW=slow), self.bash_resolve(FAKE_PS_SLOW=slow)
                self.assertEqual(py, sh, f"{name}: python {py!r} vs bash {sh!r}")
                self.assertEqual(py, want)

    def test_non_utf8_argv_does_not_crash_python(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)])
        py, sh = self.py_resolve(FAKE_PS_RAW_ARGS="400"), self.bash_resolve(FAKE_PS_RAW_ARGS="400")
        self.assertEqual(py, sh)
        self.assertEqual(py, "pid-400")

    def test_columns_clipping_does_not_hide_the_daemon(self):
        self.assertGreater(LONG_DAEMON[1].index("daemon run"), 80)
        self.set_tree([(250, 200, LONG_DAEMON), (200, 1, SYSTEMD)])
        for got in (self.py_resolve(COLUMNS="80"), self.bash_resolve(COLUMNS="80")):
            self.assertEqual(got, "", "a $COLUMNS-clipped argv hid the daemon; it became the root")


TASK_MD = "# {n} - t\n\n## Status\npending\n\n## Risk\nreversible\n\n## Work Plan\n- [ ] gate\n"


class _ProjectMixin(_FakePsMixin):
    """A temp project where ANOTHER live session (A) holds an active pointer."""

    def setUp(self):
        super().setUp()
        self.project = self.tmp / "proj"
        for n in ("001", "002"):
            td = self.project / ".agent" / "tasks" / f"{n}-t"
            td.mkdir(parents=True)
            (td / "task.md").write_text(TASK_MD.format(n=n), encoding="utf-8")
        (self.project / ".agent" / "config.json").write_text("{}", encoding="utf-8")
        # Session A = a real live process, so the liveness GC keeps its dir.
        self.sleeper = subprocess.Popen(["sleep", "300"])
        self.addCleanup(self.sleeper.wait)
        self.addCleanup(self.sleeper.kill)
        self.a_pid = self.sleeper.pid
        self.a_dir = self.project / ".agent" / "sessions" / f"pid-{self.a_pid}"
        self.a_dir.mkdir(parents=True)
        (self.a_dir / "current_state").write_text("002\n", encoding="utf-8")
        self.env_file = self.tmp / "claude-env"
        self.env_file.write_text("", encoding="utf-8")

    def snapshot(self):
        sessions = self.project / ".agent" / "sessions"
        out = {}
        for p in sorted(sessions.rglob("*")):
            if p.is_file():
                st = p.stat()
                out[str(p.relative_to(sessions))] = (p.read_bytes(), st.st_mtime_ns)
            else:
                out[str(p.relative_to(sessions))] = None
        return out

    def run_hook(self, hook, payload):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / hook)], input=json.dumps(payload),
                              cwd=self.project, env=self.env(CLAUDE_ENV_FILE=str(self.env_file)),
                              capture_output=True, text=True, timeout=60)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.project,
                              env=self.env(), capture_output=True, text=True, timeout=60)

    HOOKS = (
        ("session-start-hook", {"hook_event_name": "SessionStart", "source": "startup",
                                "transcript_path": "/tmp/t.jsonl"}),
        ("state-echo-hook", {"tool_name": "Bash", "tool_input": {"command": "ls"},
                             "transcript_path": "/tmp/t.jsonl"}),
        ("task-gate-hook", {"tool_name": "Bash", "tool_input": {"command": "ls"}}),
        ("stop-hook", {"stop_hook_active": False}),
        ("chat-log-hook", {"prompt": "hello"}),
        ("session-end-hook", {"reason": "logout"}),
    )


class DaemonOnlyAncestryWritesNothing(_ProjectMixin):
    """Under the daemon with no agent below it and no env: nothing is invented."""

    def setUp(self):
        super().setUp()
        # pty host → daemon → session A's terminal claude (the unsafe shape:
        # the old walk climbed straight into A).
        self.set_tree([(300, 250, PTY_HOST), (250, self.a_pid, DAEMON),
                       (self.a_pid, 350, TERMINAL), (350, 1, SHELL)])

    def test_hooks_invent_no_pid_session_and_leave_a_untouched(self):
        for hook, payload in self.HOOKS:
            with self.subTest(hook=hook):
                before = self.snapshot()
                r = self.run_hook(hook, payload)
                self.assertEqual(self.snapshot(), before,
                                 f"{hook} changed .agent/sessions under the daemon:\n{r.stderr}")
                marks = [ln for ln in r.stderr.splitlines() if UNRESOLVED_MARK in ln]
                self.assertTrue(all(MSG_NAMES_PS_CAUSE in ln for ln in marks),
                                "the notice must name both causes (daemon or unreadable ancestor)")
                self.assertEqual(len(marks), 1,
                                 f"{hook}: want exactly one '{UNRESOLVED_MARK}' line, stderr:\n{r.stderr}")
        self.assertNotIn("PLAYBOOK_SESSION_ID", self.env_file.read_text(encoding="utf-8"),
                         "session-start exported an invented id")

    def test_cli_work_refuses_and_leaves_a_untouched(self):
        before = self.snapshot()
        r = self.run_cli("work", "001")
        self.assertNotEqual(r.returncode, 0, f"tasks work activated with no identity:\n{r.stdout}")
        self.assertEqual(self.snapshot(), before, "tasks work touched .agent/sessions")
        self.assertEqual((self.a_dir / "current_state").read_text(encoding="utf-8"), "002\n")
        self.assertEqual(sum(UNRESOLVED_MARK in ln for ln in r.stderr.splitlines()), 1, r.stderr)

    def _refused_work_leaves_task_md(self, status):
        tf = self.project / ".agent" / "tasks" / "001-t" / "task.md"
        tf.write_text(TASK_MD.format(n="001").replace("pending", status), encoding="utf-8")
        before = tf.read_bytes()
        r = self.run_cli("work", "001")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(tf.read_bytes(), before,
                         f"a refused `tasks work` still rewrote a {status} task.md:\n{r.stdout}")

    def test_refused_work_does_not_resume_a_blocked_task(self):
        self._refused_work_leaves_task_md("blocked")

    def test_refused_work_does_not_reopen_a_done_task(self):
        self._refused_work_leaves_task_md("done")

    def test_stop_hook_fails_closed_when_unresolved(self):
        # single-judge (post-D6): "" used to exit 0, releasing a session whose
        # own gates are open when its argv could not be read. Block once
        # (loud); the stop_hook_active valve still ends a re-issued stop.
        r = self.run_hook("stop-hook", {"stop_hook_active": False})
        self.assertEqual(r.returncode, 2, f"unresolved stop was released:\n{r.stderr}")
        r = self.run_hook("stop-hook", {"stop_hook_active": True})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_session_end_deletes_nothing(self):
        r = self.run_hook("session-end-hook", {"reason": "logout"})
        self.assertTrue(self.a_dir.is_dir(), f"session-end deleted another session:\n{r.stderr}")
        self.assertTrue((self.project / ".agent" / "sessions").is_dir())


class DaemonBetweenSessionsKeepsThemApart(_ProjectMixin):
    """Hosted session B sits below the daemon, which (unsafe shape) hangs
    below terminal session A: B must resolve to itself, never to A."""

    B = 450

    def setUp(self):
        super().setUp()
        self.set_tree([(self.B, 300, HOSTED), (300, 250, PTY_HOST), (250, self.a_pid, DAEMON),
                       (self.a_pid, 350, TERMINAL), (350, 1, SHELL)])

    def test_b_work_does_not_overwrite_a_pointer(self):
        r = self.run_cli("work", "001")
        self.assertEqual((self.a_dir / "current_state").read_text(encoding="utf-8"), "002\n",
                         f"session B overwrote session A's pointer:\n{r.stdout}\n{r.stderr}")
        b_state = self.project / ".agent" / "sessions" / f"pid-{self.B}" / "current_state"
        self.assertTrue(b_state.exists(), f"B's own pointer was not written:\n{r.stderr}")
        self.assertEqual(b_state.read_text(encoding="utf-8").strip(), "001")

    def test_b_hooks_leave_a_untouched(self):
        a_before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.a_dir.iterdir()}
        for hook, payload in self.HOOKS[:-1]:           # session-end last, separately
            with self.subTest(hook=hook):
                self.run_hook(hook, payload)
                a_now = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.a_dir.iterdir()}
                self.assertEqual(a_now, a_before, f"{hook} (session B) wrote into session A's dir")
        self.run_hook("session-end-hook", {"reason": "logout"})
        self.assertTrue(self.a_dir.is_dir(), "session B's logout deleted session A's dir")


class NestedExitKeepsTheOuterSession(_ProjectMixin):
    """Task 126 (owner Q-D (b), 2026-09-29; 106 P2, gauntlet 2 G2-21): a claude started from
    another claude's Bash resolves to the OUTER session's `pid-N` (the highest agent), and its
    SessionEnd deleted that live directory — the outer session lost its task pointer.
    Session-end now deletes `pid-N` only when the exiting process IS N: the lowest agent in
    the hook's own chain."""

    def _dir(self, pid):
        d = self.project / ".agent" / "sessions" / f"pid-{pid}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "current_state").write_text("001\n", encoding="utf-8")
        return d

    def test_a_nested_claude_exiting_keeps_the_outer_dir(self):
        self.set_tree([(450, 400, TERMINAL), (400, 300, TERMINAL), (300, 1, SHELL)])
        outer = self._dir(400)
        r = self.run_hook("session-end-hook", {"reason": "exit"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((outer / "current_state").is_file(),
                        f"the nested claude's exit deleted the outer session's dir:\n{r.stderr}")

    def test_the_session_itself_exiting_still_deletes_its_dir(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)])
        own = self._dir(400)
        r = self.run_hook("session-end-hook", {"reason": "exit"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(own.exists(), "a top-level exit no longer cleans up its own dir")

    def test_no_agent_in_the_chain_keeps_it(self):
        # the id falls back to pid-<parent> (no agent seen): nothing proves the exiting
        # process is that session — keep; the liveness GC reclaims it once it is dead
        self.set_tree([(300, 1, SHELL)])
        d = self._dir(os.getpid())
        r = self.run_hook("session-end-hook", {"reason": "exit"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(d.is_dir(), r.stderr)


class NestedExitKeepsTheOuterSessionProc(NestedExitKeepsTheOuterSession):
    PROC = True


class DoctorSurvivesASlowPs(_ProjectMixin):
    """Single judge run 4: doctor gave the bash resolver 5 s while each probe
    may take up to 10 s — a 6 s `ps` crashed doctor with a traceback."""

    def test_parity_line_is_printed_not_a_traceback(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)])
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "doctor"], cwd=self.project,
                           env=self.env(FAKE_PS_SLOW="400", FAKE_PS_SLOW_S="6"),
                           capture_output=True, text=True, timeout=300)
        self.assertNotIn("Traceback", r.stderr, r.stderr[-2000:])
        line = [ln for ln in r.stdout.splitlines() if "Python ≡ bash resolver" in ln]
        self.assertTrue(line, f"no parity verdict line:\n{r.stdout[-2000:]}")
        self.assertIn("pid-400", line[0])


class DoctorReportsReclaimedDeadSessions(_ProjectMixin):
    """The CLI entry GC reclaims dead `pid-*` dirs; doctor names them."""

    def test_doctor_names_the_dirs_the_gc_reclaimed(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)])
        p = subprocess.Popen(["true"])
        p.wait()
        dead = self.project / ".agent" / "sessions" / f"pid-{p.pid}"
        dead.mkdir(parents=True)
        (dead / "current_state").write_text("001\n", encoding="utf-8")
        r = self.run_cli("doctor")
        self.assertFalse(dead.exists(), "GC did not reclaim the dead session dir")
        self.assertTrue(self.a_dir.exists(), "GC reclaimed a LIVE session dir")
        line = [ln for ln in r.stdout.splitlines() if "reclaimed" in ln]
        self.assertTrue(line and f"pid-{p.pid}" in line[0],
                        f"doctor did not report the reclaimed dir:\n{r.stdout}")


class ResolverParityProc(ResolverParity):
    """The same vectors, walked through the /proc fixture instead of `ps`."""
    PROC = True

    def test_every_vector_bash_equals_python_equals_expected(self):   # cited by the ledger
        super().test_every_vector_bash_equals_python_equals_expected()


class DaemonOnlyAncestryWritesNothingProc(DaemonOnlyAncestryWritesNothing):
    PROC = True

    def test_hooks_invent_no_pid_session_and_leave_a_untouched(self):   # cited by the ledger
        super().test_hooks_invent_no_pid_session_and_leave_a_untouched()


class DaemonBetweenSessionsKeepsThemApartProc(DaemonBetweenSessionsKeepsThemApart):
    PROC = True

    def test_b_work_does_not_overwrite_a_pointer(self):   # cited by the ledger
        super().test_b_work_does_not_overwrite_a_pointer()


class ProcProbeFailuresFailClosed(_FakePsMixin):
    """The /proc path: unreadable entries stop the walk (keep an agent found
    below, else unresolved) — never a made-up pid, never a daemon root."""
    PROC = True

    def both(self, **extra):
        py, sh = self.py_resolve(**extra), self.bash_resolve(**extra)
        self.assertEqual(py, sh, f"python {py!r} vs bash {sh!r}")
        return py

    def test_missing_cmdline_on_the_pty_host_is_not_a_root(self):
        self.set_tree([(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)],
                      omit_cmdline=(300,))
        self.assertEqual(self.both(), "")

    def test_empty_cmdline_keeps_the_session_found_below(self):
        self.set_tree([(400, 300, HOSTED), (300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)],
                      raw_cmdline={300: b""})
        self.assertEqual(self.both(), "pid-400")

    def test_missing_status_mid_walk_is_unresolved(self):
        self.set_tree([(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)],
                      omit_status=(300,))
        self.assertEqual(self.both(), "")

    def test_non_utf8_cmdline(self):
        self.set_tree([(400, 300, TERMINAL), (300, 1, SHELL)],
                      raw_cmdline={400: b"claude\0\xff--x\0"})
        self.assertEqual(self.both(), "pid-400")

    def test_last_argument_without_trailing_nul(self):
        self.set_tree([(300, 250, PTY_HOST), (250, 200, DAEMON), (200, 1, SYSTEMD)],
                      raw_cmdline={300: b"claude\0bg-pty-host"})
        self.assertEqual(self.both(), "")

    def test_s5_1_spaced_argv0_daemon_is_exact_on_proc(self):
        # S5-1 (post-D6 run 5): an argv[0] with spaces — ambiguous on the ps
        # line, exact here. The hosted session resolves to itself, never to
        # the daemon.
        spaced = ("claude.exe", "unused", ["/opt/my claude helper/bin/claude.exe", "daemon", "run"])
        self.set_tree([(400, 300, HOSTED), (300, 250, PTY_HOST), (250, 200, spaced), (200, 1, SYSTEMD)])
        self.assertEqual(self.both(), "pid-400")
        self.set_tree([(250, 200, spaced), (200, 1, SYSTEMD)])
        self.assertEqual(self.both(), "", "the spaced-argv0 daemon became a session root")

    def test_s5_2_newline_inside_an_argument_is_exact_on_proc(self):
        # S5-2: `daemon run` inside ONE argument is not the subcommand.
        nl = ("claude", "unused", ["claude", "daemon\nrun"])
        self.set_tree([(400, 300, nl), (300, 1, SHELL)])
        self.assertEqual(self.both(), "pid-400")


class ProcPathIsTheDefaultOnLinux(unittest.TestCase):
    """Without the seam, a Linux host takes the /proc path (no `ps` spawned)."""

    @unittest.skipUnless(os.path.isdir("/proc/self"), "no /proc on this host")
    def test_no_ps_is_spawned_when_proc_exists(self):
        # A `ps` on PATH that records every execution (post-rewrite single
        # judge run 2: "stdout starts with (" also passed with /proc removed).
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "ps-was-run"
            ps = Path(d) / "ps"
            ps.write_text(f"#!/bin/sh\necho run >> '{marker}'\nexit 1\n", encoding="utf-8")
            ps.chmod(0o755)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("PLAYBOOK_SESSION_ID", "PLAYBOOK_PROC_ROOT", "BASH_ENV")}
            env["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
            env["PYTHONPATH"] = str(PLUGIN)
            code = ("import os, tasks.core as c\n"
                    "print(c._walk_agent_ancestry())\n"
                    "c._walk_agent_ancestry.cache_clear()\n"
                    "os.environ['PLAYBOOK_PROC_ROOT'] = '/proc'\n"
                    "print(c._walk_from(os.getppid()))\n")
            r = subprocess.run([sys.executable, "-c", code], env=env,
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(marker.exists(), "the walk spawned ps although /proc exists")
            default, explicit = r.stdout.splitlines()
            self.assertEqual(default, explicit, "the default walk differs from an explicit /proc read")
            b = subprocess.run([bash_or_skip(), "-c", f"source '{GATE_LIB.as_posix()}' && _agent_walk"],
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(b.returncode, 0, b.stderr)
            self.assertFalse(marker.exists(), "the bash walk spawned ps although /proc exists")


if __name__ == "__main__":
    unittest.main()
