"""Task 125 (PLAN S11; gauntlet 2 G2-05, owner decision 2026-10-02): a judge cannot read the chat.

Owner decision: ".agent/chat_log.md, .agent/sessions/ and .agent/bash_history are read-masked in
the judges' sandbox". In gauntlet 2 (J02) a judge quoted a canary that existed only in
`.agent/chat_log.md` — the blind-context claim covers the PROMPT, and the sandbox left the whole
filesystem readable. Now, in the read-only (judge) mode only, the sandbox hides those records —
and their archives (`chat_log.md.*`, `bash_history.*`), in `.agent` and in every lane under it:
bwrap binds /dev/null over each file (a read is refused — the read-only bind is nodev) and an
empty tmpfs over `sessions/`; seatbelt denies reading them. A writable (worker) sandbox keeps them. Windows has no backend: unchanged, unmasked.

Run: python3 -m unittest tests.test_judge_read_mask
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))
from provider import sandbox  # noqa: E402

CANARY = "CANARY-125-only-in-the-chat"
# Task 151: a seatbelt profile only ever runs on macOS. Built on Windows it carries `C:\…` paths
# (escaped, and joined to `/`-separated record names) that no backend reads, and NTFS refuses a
# `"` in a name — so the seatbelt assertions skip there; Linux and macOS run every one of them.
SEATBELT_PATHS = os.name != "nt"
NO_SEATBELT_PATHS = "a seatbelt profile is macOS-only; Windows paths never reach one"


def _project() -> Path:
    p = Path(tempfile.mkdtemp(prefix="pb-125-")).resolve()
    a = p / ".agent"
    (a / "sessions" / "pid-1").mkdir(parents=True)
    (a / "sessions" / "pid-1" / "current_state").write_text("003\n", encoding="utf-8")
    (a / "chat_log.md").write_text(f"## M1\n{CANARY}\n", encoding="utf-8")
    (a / "chat_log.md.1").write_text(f"{CANARY} (rotated)\n", encoding="utf-8")
    (a / "bash_history").write_text(f"echo {CANARY}\n", encoding="utf-8")
    (a / "bash_history.archived-20260925").write_text(f"echo {CANARY}\n", encoding="utf-8")
    (a / "tasks" / "003-x").mkdir(parents=True)
    (a / "tasks" / "003-x" / "task.md").write_text("# 003 - x\n", encoding="utf-8")
    lane = a / "alice"
    (lane / "sessions").mkdir(parents=True)
    (lane / "tasks").mkdir()
    (lane / "chat_log.md").write_text(f"{CANARY} (lane)\n", encoding="utf-8")
    (p / "src.py").write_text("x = 1\n", encoding="utf-8")
    return p


class Masks(unittest.TestCase):
    """Task 138 G3-2: the masks are LAYERS, not a launch-time list of files — `.agent` and each
    directory under it become an empty tmpfs onto which only the non-record entries are bound
    back, so a record created or renamed into place after launch never appears."""

    def setUp(self):
        self.p = _project()
        self.addCleanup(shutil.rmtree, self.p, True)
        self.a = self.p / ".agent"
        self.files = {str(self.a / n) for n in ("chat_log.md", "chat_log.md.1", "bash_history",
                                                "bash_history.archived-20260925")} | {str(self.a / "alice" / "chat_log.md")}

    @staticmethod
    def _one(argv, flag):
        return [argv[i + 1] for i, a in enumerate(argv) if a == flag]

    @staticmethod
    def _two(argv, flag):
        return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == flag]

    def test_bwrap_judge_mode_layers_the_records_away_after_the_project_bind(self):
        argv = sandbox.build_bwrap_argv(self.p, None, ["true"], None, project_writable=False)
        proj_at = argv.index(str(self.p))
        tmpfs = self._one(argv, "--tmpfs")
        self.assertEqual(tmpfs[:1], [str(self.a)])                      # the .agent layer first
        self.assertIn(str(self.a / "alice"), tmpfs)                     # every lane is its own layer
        self.assertGreater(argv.index(str(self.a)), proj_at)
        nulls = {dst for (src, dst) in self._two(argv, "--ro-bind") if src == "/dev/null"}
        self.assertEqual(nulls, self.files)                             # placeholders: a read is refused
        binds = {dst for (src, dst) in self._two(argv, "--ro-bind") if src == dst}
        self.assertIn(str(self.a / "tasks" / "003-x"), binds)           # the record of the task comes back
        self.assertNotIn(str(self.a / "sessions"), binds)
        self.assertIn(str(self.a / "sessions"), self._one(argv, "--dir"))

    def test_a_writable_worker_sandbox_masks_nothing(self):
        argv = sandbox.build_bwrap_argv(self.p, None, ["true"], None, project_writable=True)
        self.assertNotIn("/dev/null", argv)
        self.assertNotIn("--tmpfs", argv)
        prof = sandbox.build_seatbelt_profile(self.p, None, None, project_writable=True)
        self.assertNotIn("file-read", prof)

    def test_a_read_only_sandbox_that_keeps_the_records_masks_nothing(self):
        # Task 138 G3-1: the monitor runs read-only too and reads .agent/sessions/<id>/transcript_path
        argv = sandbox.build_bwrap_argv(self.p, None, ["true"], None, project_writable=False,
                                        mask_records=False)
        self.assertNotIn("/dev/null", argv)
        self.assertNotIn("--tmpfs", argv)
        self.assertIn("--ro-bind", argv)
        prof = sandbox.build_seatbelt_profile(self.p, None, None, project_writable=False, mask_records=False)
        self.assertNotIn("file-read", prof)

    @unittest.skipUnless(SEATBELT_PATHS, NO_SEATBELT_PATHS)
    def test_seatbelt_judge_mode_denies_records_by_name_last(self):
        import re
        prof = sandbox.build_seatbelt_profile(self.p, None, None, project_writable=False)
        lines = prof.splitlines()
        rules = [re.match(r'\(deny file-read\* \(regex #"(.*)"\)\)$', l) for l in lines]
        pats = [re.compile(m.group(1)) for m in rules if m]
        self.assertTrue(pats, prof)
        denied = lambda path: any(p.search(path) for p in pats)
        for f in self.files | {str(self.a / "chat_log.md.7"), str(self.a / "carol" / "bash_history"),
                               str(self.a / "sessions"), str(self.a / "sessions" / "pid-9" / "x"),
                               str(self.a / "alice" / "sessions" / "y"),
                               str(self.a / "sessions.archived-1" / "transcript_path")}:
            self.assertTrue(denied(f), f)                               # incl. ones that do not exist yet
        for ok in (self.a / "tasks" / "003-x" / "task.md", self.p / "src.py", self.a / "config.json",
                   self.a / "tasks" / "chat_log.md.notes" / "x", self.a / "sessionsX"):
            self.assertFalse(denied(str(ok)), ok)
        last_write = max(i for i, l in enumerate(lines) if "file-write" in l)
        first_read = min(i for i, l in enumerate(lines) if "file-read" in l)
        self.assertGreater(first_read, last_write)

    @unittest.skipUnless(SEATBELT_PATHS, NO_SEATBELT_PATHS)
    def test_seatbelt_also_denies_a_symlinked_lanes_real_records(self):
        ext = Path(tempfile.mkdtemp(prefix="pb-138x-")).resolve()
        self.addCleanup(shutil.rmtree, ext, True)
        (ext / "bob").mkdir()
        (self.a / "bob").symlink_to(ext / "bob", target_is_directory=True)
        import re
        prof = sandbox.build_seatbelt_profile(self.p, None, None, project_writable=False)
        pats = [re.compile(m.group(1)) for m in
                (re.match(r'\(deny file-read\* \(regex #"(.*)"\)\)$', l) for l in prof.splitlines()) if m]
        self.assertTrue(any(p.search(str(ext / "bob" / "chat_log.md")) for p in pats), prof)

    def test_a_lane_link_is_resolved_once(self):
        # Task 139 C-4 (codex-medium): the recreated link and the masked layer came from two
        # separate reads of the symlink — re-pointed in between, the sandbox linked one directory
        # and masked another. Simulated: the one read (`_symlinked_lanes`) answers a target the
        # link on disk does not point at; the sandbox must link what it masks, not re-read.
        from unittest import mock
        ext = Path(tempfile.mkdtemp(prefix="pb-139x-")).resolve()
        self.addCleanup(shutil.rmtree, ext, True)
        (ext / "bob").mkdir()
        (ext / "other").mkdir()
        (self.a / "bob").symlink_to(ext / "bob", target_is_directory=True)
        with mock.patch.object(sandbox, "_symlinked_lanes", lambda agent: {"bob": ext / "other"}):
            argv = sandbox.build_bwrap_argv(self.p, None, ["true"], None, project_writable=False)
        layers = set(self._one(argv, "--tmpfs"))
        links = {dst: src for (src, dst) in self._two(argv, "--symlink")}
        self.assertIn(str(self.a / "bob"), links)
        self.assertIn(links[str(self.a / "bob")], layers)             # the link points at a masked dir

    @unittest.skipUnless(SEATBELT_PATHS, NO_SEATBELT_PATHS)
    def test_seatbelt_denies_a_symlinked_records_real_target(self):
        import re
        ext = Path(tempfile.mkdtemp(prefix="pb-139s-")).resolve()
        self.addCleanup(shutil.rmtree, ext, True)
        (ext / "sess").mkdir()
        (ext / "log.md").write_text("x\n", encoding="utf-8")
        (self.a / "sessions.old").symlink_to(ext / "sess", target_is_directory=True)
        (self.a / "alice" / "bash_history.1").symlink_to(ext / "log.md")
        prof = sandbox.build_seatbelt_profile(self.p, None, None, project_writable=False)
        self.assertIn(f'(deny file-read* (subpath "{ext / "sess"}"))', prof.splitlines())
        self.assertIn(f'(deny file-read* (literal "{ext / "log.md"}"))', prof.splitlines())

    def test_a_dangling_record_link_fails_closed_on_bwrap_and_is_denied_on_seatbelt(self):
        # Task 139 post-D6 run 2 (codex): a dangling `chat_log.md` link was skipped — the
        # writer could create its target after the judge started, readable at its real path.
        # bwrap cannot mount over a path that does not exist in the read-only root: refuse.
        ext = Path(tempfile.mkdtemp(prefix="pb-139d-")).resolve()
        self.addCleanup(shutil.rmtree, ext, True)
        (self.a / "alice" / "chat_log.md.9").symlink_to(ext / "not-yet.md")
        with self.assertRaises(RuntimeError) as cm:
            sandbox.build_bwrap_argv(self.p, None, ["true"], None, project_writable=False)
        self.assertIn(str(ext / "not-yet.md"), str(cm.exception))
        if SEATBELT_PATHS:
            prof = sandbox.build_seatbelt_profile(self.p, None, None, project_writable=False)
            self.assertIn(f'(deny file-read* (literal "{ext / "not-yet.md"}"))', prof.splitlines())
        # a worker / a --keep-records sandbox is not affected
        sandbox.build_bwrap_argv(self.p, None, ["true"], None, project_writable=True)
        sandbox.build_bwrap_argv(self.p, None, ["true"], None, project_writable=False, mask_records=False)

    def test_a_dangling_sessions_link_is_classified_by_its_name(self):
        # Task 139 post-D6 run 3 (codex, live probe): `e.is_dir()` is False for a dangling
        # link, so `sessions.old -> (missing)` was neither masked nor refused
        ext = Path(tempfile.mkdtemp(prefix="pb-139e-")).resolve()
        self.addCleanup(shutil.rmtree, ext, True)
        (self.a / "sessions.old").symlink_to(ext / "later", target_is_directory=True)
        with self.assertRaises(RuntimeError) as cm:
            sandbox.build_bwrap_argv(self.p, None, ["true"], None, project_writable=False)
        self.assertIn(str(ext / "later"), str(cm.exception))
        if SEATBELT_PATHS:
            prof = sandbox.build_seatbelt_profile(self.p, None, None, project_writable=False)
            self.assertIn(f'(deny file-read* (subpath "{ext / "later"}"))', prof.splitlines())

    @unittest.skipUnless(SEATBELT_PATHS, NO_SEATBELT_PATHS)
    def test_a_quote_in_a_masked_path_is_escaped_for_seatbelt(self):
        # Task 139 post-D6 run 2 (codex): a `"` in a target path ended the profile's string
        ext = Path(tempfile.mkdtemp(prefix='pb-139"q-')).resolve()
        self.addCleanup(shutil.rmtree, ext, True)
        (ext / "sess").mkdir()
        (self.a / "sessions.old").symlink_to(ext / "sess", target_is_directory=True)
        prof = sandbox.build_seatbelt_profile(self.p, None, None, project_writable=False)
        want = str(ext / "sess").replace("\\", "\\\\").replace('"', '\\"')
        self.assertIn(f'(deny file-read* (subpath "{want}"))', prof.splitlines())
        for line in prof.splitlines():
            if "file-read" in line:                       # every read rule is one balanced string
                body = line.replace('\\"', "")
                self.assertEqual(body.count('"') % 2, 0, line)

    def test_the_monitor_launch_keeps_the_records(self):
        text = (_HERE.parent / "plugins/playbook/scripts/monitor-lib/launch-monitor").read_text(encoding="utf-8")
        self.assertIn("--keep-records", text)
        r = subprocess.run([sys.executable, "-m", "provider.sandbox", "--agent", "claude", "--ro-project",
                            "--keep-records", "--project-root", str(self.p), "--print-argv", "--", "x"],
                           cwd=str(_HERE.parent / "plugins/playbook"), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(str(self.a / "sessions"), r.stdout)
        self.assertNotIn("/dev/null", r.stdout.splitlines())


class JudgesAreNotSentToTheChat(unittest.TestCase):
    """opus r1 I1: the judge prompt told every judge to run `tasks context <N>`, which reads
    the now-masked chat log (it crashed with a traceback inside the sandbox)."""

    def test_the_intent_check_points_at_the_task_record(self):
        from tasks.template import _intent_check
        clause = _intent_check("/p/.agent/tasks/125-x/task.md")
        self.assertNotIn("tasks context", clause)
        for part in ("## Intent", "## Why", "### Recent Chat"):
            self.assertIn(part, clause)

    def test_tasks_context_says_why_when_the_log_cannot_be_read(self):
        import contextlib
        import io
        import os
        from unittest import mock
        from tasks import history
        p = _project()
        self.addCleanup(shutil.rmtree, p, True)
        real = Path.read_text

        def deny(self_, *a, **kw):
            if self_.name == "chat_log.md":
                raise PermissionError(13, "Permission denied")
            return real(self_, *a, **kw)
        err = io.StringIO()
        prev = os.getcwd()
        os.chdir(p)
        self.addCleanup(os.chdir, prev)
        with mock.patch.object(Path, "read_text", deny), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            history.cmd_context(["3"])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("hides the chat log", err.getvalue())

    def test_a_denied_stat_is_not_reported_as_a_missing_log(self):
        # Task 138 G3-5 (agy): seatbelt's deny file-read* covers stat too, and Python 3.13's
        # Path.exists() returns False on any OSError — the old exists() pre-check then said
        # "No .agent/chat_log.md found." instead of naming the mask
        import contextlib
        import io
        import os
        from unittest import mock
        from tasks import history
        p = _project()
        self.addCleanup(shutil.rmtree, p, True)
        real_read, real_exists = Path.read_text, Path.exists

        def deny(self_, *a, **kw):
            if self_.name == "chat_log.md":
                raise PermissionError(13, "Operation not permitted")
            return real_read(self_, *a, **kw)
        prev = os.getcwd()
        os.chdir(p)
        self.addCleanup(os.chdir, prev)
        for exists_says in (False, True):
            err = io.StringIO()
            hidden = (lambda self_, *a, **kw: False if self_.name == "chat_log.md"
                      else real_exists(self_, *a, **kw)) if not exists_says else real_exists
            with mock.patch.object(Path, "read_text", deny), mock.patch.object(Path, "exists", hidden), \
                    contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit):
                history.cmd_context(["3"])
            self.assertIn("hides the chat log", err.getvalue(), exists_says)
        (p / ".agent" / "chat_log.md").unlink()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit):
            history.cmd_context(["3"])
        self.assertIn("No .agent/chat_log.md found.", err.getvalue())


def _bwrap_works() -> bool:
    if not shutil.which("bwrap"):
        return False
    try:
        return subprocess.run(["bwrap", "--ro-bind", "/", "/", "true"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@unittest.skipUnless(_bwrap_works(), "bwrap not available or not usable here")
class LiveBwrapMask(unittest.TestCase):
    """Real-os: inside the judge-mode sandbox the records cannot be read, the rest of the
    project as before."""

    PROBE = r"""
import os, sys, time
go = sys.argv[1]
if go != "-":
    print("ready", flush=True)
    for _ in range(600):
        if os.path.exists(go):
            break
        time.sleep(0.05)
for path in sys.argv[2:]:
    try:
        if os.path.isdir(path):
            print(path + "=" + repr(sorted(os.listdir(path))))
        else:
            with open(path, encoding="utf-8") as fh:
                print(path + "=" + repr(fh.read()))
    except OSError as e:
        print(path + "=ERR " + type(e).__name__)
"""

    def setUp(self):
        self.p = _project()
        self.addCleanup(shutil.rmtree, self.p, True)
        self.a = self.p / ".agent"

    def _argv(self, paths, writable=False, go="-", **kw):
        return sandbox.build_bwrap_argv(self.p, None, [sys.executable, "-c", self.PROBE, go, *map(str, paths)],
                                        None, project_writable=writable, **kw)

    def _run(self, argv):
        r = subprocess.run(argv, cwd=str(self.p), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def _hidden(self, out, path):
        line = next(l for l in out.splitlines() if l.startswith(str(path) + "="))
        self.assertRegex(line, r"=(ERR (PermissionError|FileNotFoundError)|'')$", line)

    def test_a_judge_reads_no_chat_but_the_rest_of_the_project(self):
        recs = [self.a / "chat_log.md", self.a / "chat_log.md.1", self.a / "bash_history",
                self.a / "alice" / "chat_log.md"]
        out = self._run(self._argv([*recs, self.a / "sessions", self.a / "tasks/003-x/task.md", self.p / "src.py"]))
        self.assertNotIn(CANARY, out)
        for rec in recs:
            self._hidden(out, rec)
        self.assertIn(f"{self.a / 'sessions'}=[]", out)
        self.assertIn("='# 003 - x\\n'", out)
        self.assertIn("='x = 1\\n'", out)

    def test_negative_control_a_worker_still_reads_them(self):
        self.assertIn(CANARY, self._run(self._argv([self.a / "chat_log.md"], writable=True)))

    def test_a_keep_records_sandbox_reads_the_sessions(self):
        # G3-1: the monitor's sandbox
        out = self._run(self._argv([self.a / "sessions", self.a / "chat_log.md"], mask_records=False))
        self.assertIn("['pid-1']", out)
        self.assertIn(CANARY, out)

    def test_an_archived_sessions_dir_is_hidden(self):
        # Task 139 C-3 (codex-medium, live probe): `sessions.archived-1/` was readable
        for d in (self.a / "sessions.archived-1", self.a / "alice" / "sessions.old"):
            d.mkdir()
            (d / "transcript_path").write_text(CANARY + " (archived sessions)\n", encoding="utf-8")
        out = self._run(self._argv([self.a / "sessions.archived-1" / "transcript_path",
                                    self.a / "alice" / "sessions.old" / "transcript_path"]))
        self.assertNotIn(CANARY, out)

    def test_a_symlinked_record_is_hidden_at_its_real_path_too(self):
        # Task 139 post-D6 (codex): a record that is itself a symlink (`sessions.archived-1 ->
        # elsewhere`, a lane's `chat_log.md -> elsewhere`) was hidden at its `.agent` path only;
        # `--ro-bind / /` shows the real target
        ext = Path(tempfile.mkdtemp(prefix="pb-139r-")).resolve()
        self.addCleanup(shutil.rmtree, ext, True)
        (ext / "sess").mkdir()
        (ext / "sess" / "transcript_path").write_text(CANARY + " (linked sessions)\n", encoding="utf-8")
        (ext / "log.md").write_text(CANARY + " (linked log)\n", encoding="utf-8")
        (self.a / "sessions.archived-1").symlink_to(ext / "sess", target_is_directory=True)
        (self.a / "alice" / "chat_log.md.2").symlink_to(ext / "log.md")
        out = self._run(self._argv([self.a / "sessions.archived-1" / "transcript_path",
                                    ext / "sess" / "transcript_path",
                                    self.a / "alice" / "chat_log.md.2", ext / "log.md"]))
        self.assertNotIn(CANARY, out)

    def test_a_record_created_after_launch_stays_hidden(self):
        # G3-2: the masks used to be a list of the files that existed when the argv was built
        argv = self._argv([self.a / "chat_log.md.2", self.a / "carol" / "chat_log.md"])
        (self.a / "chat_log.md.2").write_text(CANARY + " (rotated later)\n", encoding="utf-8")
        (self.a / "carol").mkdir()
        (self.a / "carol" / "chat_log.md").write_text(CANARY + " (new lane)\n", encoding="utf-8")
        out = self._run(argv)
        self.assertNotIn(CANARY, out)

    def test_a_rename_over_the_log_during_the_run_stays_hidden(self):
        # G3-2: on Linux >= 3.18 a rename over a mount point detaches the mount in the judge's namespace
        go = Path(tempfile.mkdtemp(prefix="pb-138go-")).resolve()
        self.addCleanup(shutil.rmtree, go, True)
        proc = subprocess.Popen(self._argv([self.a / "chat_log.md"], go=str(go / "go")), cwd=str(self.p),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert proc.stdout is not None
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            new = self.a / "chat_log.md.tmp"
            new.write_text(CANARY + " (renamed in)\n", encoding="utf-8")
            os.replace(new, self.a / "chat_log.md")
            (go / "go").write_text("", encoding="utf-8")
            out, err = proc.communicate(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(proc.returncode, 0, err)
        self.assertNotIn(CANARY, out)

    def test_a_symlinked_lane_is_hidden_at_both_paths(self):
        # G3-3: a lane that is a symlink was skipped, and `--ro-bind / /` shows its real path too
        ext = Path(tempfile.mkdtemp(prefix="pb-138x-")).resolve()
        self.addCleanup(shutil.rmtree, ext, True)
        (ext / "bob" / "tasks" / "007-y").mkdir(parents=True)
        (ext / "bob" / "chat_log.md").write_text(CANARY + " (symlinked lane)\n", encoding="utf-8")
        (ext / "bob" / "tasks" / "007-y" / "task.md").write_text("# 007 - y\n", encoding="utf-8")
        (self.a / "bob").symlink_to(ext / "bob", target_is_directory=True)
        out = self._run(self._argv([self.a / "bob" / "chat_log.md", ext / "bob" / "chat_log.md",
                                    self.a / "bob" / "tasks" / "007-y" / "task.md"]))
        self.assertNotIn(CANARY, out)
        self.assertIn("='# 007 - y\\n'", out)                          # the lane's tasks still read

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads a mode-000 directory")
    def test_an_unreadable_lane_does_not_unmask_the_next_one(self):
        # G3-4: one OSError aborted the whole scan and every later lane stayed readable
        (self.a / "aaa").mkdir()
        (self.a / "zed").mkdir()
        (self.a / "zed" / "chat_log.md").write_text(CANARY + " (after the unreadable lane)\n", encoding="utf-8")
        os.chmod(self.a / "aaa", 0)
        self.addCleanup(os.chmod, self.a / "aaa", 0o755)
        out = self._run(self._argv([self.a / "zed" / "chat_log.md", self.a / "chat_log.md"]))
        self.assertNotIn(CANARY, out)


if __name__ == "__main__":
    unittest.main()
