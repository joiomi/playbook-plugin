"""A task folder made by hand is refused at the close; no hook watches for it (task 339, PLAN-PLAYBOOK-2.md 4.1 b).

Task 196's after-the-call watch (`task-dir-watch-hook`, `task-dir-watch.py`) held the session that made a task folder
by hand until it was removed or adopted. The plan removes it (R14: "supravegherea de după apel a dosarului de task-uri
(auditul 1): scoasă" — the audits' journals show no real hold of it). What stays is the close's own check
(`lifecycle._unknown_dirs_bounce` before verify, and its re-check at the commit under the task lock): a folder the task
registry does not name and whose task.md is not committed refuses `tasks work done`. The session start still writes a
lane's missing registry. The close's tests came here from `tests/test_task_dir_detector.py` (deleted with the watch).

The hooks are fired as hooks.json wires them, with payloads shaped like Claude Code's; the tree CLI for `tasks new`,
`tasks work` and `tasks work done`.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
CLI = PLUGIN / "scripts" / "tasks"
HEADER = "# playbook task-registry v1"
SID = "s339-close-a"


class _Project(unittest.TestCase):
    """A project with one CLI-made task (`001-a`, registered) and its registry; no active task."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.proj = self.base / "proj"
        self.lane = self.proj / ".agent"
        self._task(self.lane, "001-a")
        self._registry(self.lane, [("001-a", "new")])
        (self.proj / "README.md").write_text("a\n", encoding="utf-8")

    def _task(self, lane: Path, name: str) -> Path:
        d = lane / "tasks" / name
        d.mkdir(parents=True)
        (d / "task.md").write_text(f"# {name}\n\n## Status\npending\n", encoding="utf-8")
        return d

    def _registry(self, lane: Path, rows) -> None:
        text = HEADER + "\n" + "".join(f"{n}\t{o}\t2026-10-14T00:00:00Z\n" for n, o in rows)
        (lane / "task-registry").write_text(text, encoding="utf-8")

    def _env(self, sid=SID):
        # a /proc with no agent: the session is the env id alone, as on CI — a judge's nested run must not decide
        proc = self.base / "proc-no-agent"
        (proc / "self").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PLAYBOOK_SESSION_ID=sid, HOME=str(self.home), CLAUDE_PLUGIN_ROOT=str(PLUGIN),
                   PLAYBOOK_PROC_ROOT=str(proc))
        for k in ("BASH_ENV", "PLAYBOOK_SANDBOXED", "CLAUDE_PROJECT_DIR", "PLAYBOOK_EVAL_CONFIG", "PLAYBOOK_ROLE"):
            env.pop(k, None)
        return env

    def fire(self, event, payload):
        """Every hook hooks.json runs for `event` and this tool: (blocked, stderr, how many ran)."""
        hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"].get(event, [])
        tool = payload.get("tool_name", "")
        blocked, errs = False, []
        for entry in hooks:
            m = entry.get("matcher")
            if m not in (None, "", "*") and not re.fullmatch(m, tool):
                continue
            for h in entry["hooks"]:
                r = subprocess.run([bash_or_skip(), "-c", h["command"]], cwd=self.proj, env=self._env(),
                                   input=json.dumps(payload), text=True, capture_output=True, timeout=60)
                blocked = blocked or r.returncode == 2
                errs.append(r.stderr)
        return blocked, "\n".join(errs), len(errs)

    def _payload(self, event, tool, tool_input, tuid):
        return {"session_id": SID, "cwd": str(self.proj), "hook_event_name": event, "tool_name": tool,
                "tool_input": tool_input, "tool_use_id": tuid}

    def cli(self, *args):
        return subprocess.run([bash_or_skip(), str(CLI), *args], cwd=self.proj, env=self._env(),
                              text=True, capture_output=True, timeout=120)


class NoWatch(_Project):

    def test_a_hand_made_folder_holds_no_session(self):
        cmd = {"command": "mkdir -p .agent/tasks/901-x", "description": "t"}
        b, e, _ = self.fire("PreToolUse", self._payload("PreToolUse", "Bash", cmd, "toolu_01MK"))
        self.assertFalse(b, e)
        self._task(self.lane, "901-x")                          # the call's effect: a task folder made by hand
        self.fire("PostToolUse", dict(self._payload("PostToolUse", "Bash", cmd, "toolu_01MK"),
                                      tool_response={"stdout": "", "stderr": "", "interrupted": False,
                                                     "isImage": False}, duration_ms=5))
        nxt = [("Edit", {"file_path": str(self.proj / "README.md"), "old_string": "a", "new_string": "b"}),
               ("Write", {"file_path": str(self.proj / "NOTES.md"), "content": "x\n"}),
               ("Bash", {"command": "ls", "description": "t"})]
        for i, (tool, ti) in enumerate(nxt):
            b, e, ran = self.fire("PreToolUse", self._payload("PreToolUse", tool, ti, f"toolu_01N{i}"))
            self.assertGreater(ran, 0, f"no hook is wired for {tool}: the check would prove nothing")   # impl r1, sonnet
            self.assertFalse(b, f"{tool} held after a hand-made folder: {e}")
        sd = self.lane / "sessions" / SID
        named = [p.name for p in sd.rglob("*") if p.is_file() and "901-x" in p.read_text(errors="replace")] \
            if sd.exists() else []
        self.assertEqual(named, [])

    def test_the_watch_is_gone(self):
        self.assertNotIn("task-dir-watch", HOOKS_JSON.read_text(encoding="utf-8"))
        for name in ("task-dir-watch-hook", "task-dir-watch.py"):
            self.assertFalse((PLUGIN / "scripts" / name).exists(), name)
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        from tasks import hooks_check
        self.assertEqual([e for e, names in hooks_check.EXPECTED_HOOKS.items() if "task-dir-watch-hook" in names],
                         [])


class TheSessionStart(_Project):

    def _start(self):
        return subprocess.run([bash_or_skip(), str(PLUGIN / "scripts" / "session-start-hook")], cwd=self.proj,
                              env=self._env(), text=True, capture_output=True, timeout=60,
                              input=json.dumps({"session_id": SID, "cwd": str(self.proj),
                                                "hook_event_name": "SessionStart", "source": "startup"}))

    def test_the_session_start_enrols_a_lane_without_a_registry(self):
        (self.lane / "task-registry").unlink()
        r = self._start()
        self.assertEqual(r.returncode, 0, r.stderr)
        reg = self.lane / "task-registry"
        self.assertTrue(reg.is_file(), r.stderr)
        self.assertIn("001-a\tenrolled\t", reg.read_text(encoding="utf-8"))

    def test_an_enrolment_that_cannot_write_is_no_failure(self):
        # the enrolment's own failure: its lock and temporary file go in the lane, which takes no new name here
        (self.lane / "task-registry").unlink()
        (self.lane / "sessions" / SID).mkdir(parents=True)     # the session directory the start provisions
        self.lane.chmod(0o555)
        self.addCleanup(self.lane.chmod, 0o755)
        if os.access(self.lane, os.W_OK):
            self.skipTest("a lane without write permission is still writable here (root)")
        r = self._start()
        # the exit status and the missing registry are the proof (the hook sends the enrolment's own output to
        # /dev/null, so no traceback could show here — impl round 1, opus)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.lane / "task-registry").exists())


class TheClose(_Project):

    def _active(self):
        r = self.cli("new", "quick", "c")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = re.search(r"tasks/(\d+)-", r.stdout)
        assert m, r.stdout
        num = m.group(1)
        self.assertEqual(self.cli("work", num).returncode, 0)

    def test_the_close_refuses_while_an_unknown_folder_stands(self):
        self._active()
        self._task(self.lane, "999-x")
        r = self.cli("work", "done")
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".agent/tasks/999-x", out)
        self.assertIn("tasks adopt", out)

    def test_the_close_refuses_on_an_unreadable_registry(self):
        self._active()
        real = self.base / "reg"
        (self.lane / "task-registry").rename(real)
        (self.lane / "task-registry").symlink_to(real)
        r = self.cli("work", "done")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("task-registry", r.stdout + r.stderr)

    def test_the_close_skips_a_creation_in_progress(self):
        self._active()
        rows = (self.lane / "task-registry").read_text(encoding="utf-8")
        (self.lane / "task-registry").write_text(rows + "995-r\treserved\t2026-10-14T00:00:00Z\n", encoding="utf-8")
        self._task(self.lane, "995-r")
        with open(self.lane / "task-registry.lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            r = self.cli("work", "done")
        self.assertNotIn("995-r", r.stdout + r.stderr)

    def test_a_file_in_tasks_never_refuses_the_close(self):
        # task 196's impl panel r1 I11: a FILE with a task-like name is no task folder
        self._active()
        (self.lane / "tasks" / "999-notes.md").write_text("x\n", encoding="utf-8")
        r = self.cli("work", "done")
        self.assertNotIn("999-notes.md", r.stdout + r.stderr)

    def test_the_close_without_a_registry_reads_the_git_rule(self):
        # task 197 P4: a missing registry is no pass — what is not committed is unknown
        self._active()
        (self.lane / "task-registry").unlink()
        self._task(self.lane, "999-x")
        r = self.cli("work", "done")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("999-x", r.stdout + r.stderr)

    def test_tasks_work_enrols_a_lane_without_a_registry(self):
        # task 197 P1(a): the activation writes the registry a lane lacks, from what stands (the first enrolment's trust)
        (self.lane / "task-registry").unlink()
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("001-a\tenrolled\t", (self.lane / "task-registry").read_text(encoding="utf-8"))


class TheCloseRechecks(unittest.TestCase):
    """Task 196's impl panel r1 I2 (codex, reproduced): a folder made while the close's verify ran must still refuse it."""

    def _repo(self, verify):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / "repo"
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        (d / ".agent" / "tasks" / "001-t").mkdir(parents=True)
        (d / ".agent" / "config.json").write_text(json.dumps({"verify": verify, "panel_required_for": []}),
                                                  encoding="utf-8")
        (d / ".agent" / "tasks" / "001-t" / "task.md").write_text(
            "# 001 - T\n\n## Status\npending\n\n## Risk\nreversible\n\n## Work Plan\n- [x] G1: do it\n",
            encoding="utf-8")
        (d / ".agent" / "task-registry").write_text(HEADER + "\n001-t\tnew\t2026-10-14T00:00:00Z\n",
                                                   encoding="utf-8")
        return d, tmp.name

    def _close(self, d, home, sid):
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID=sid, HOME=home)
        env.pop("BASH_ENV", None)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "1"], cwd=d, env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done"], cwd=d, env=env,
                              capture_output=True, text=True, timeout=120)

    def test_a_folder_made_during_verify_refuses_the_close(self):
        d, home = self._repo("python3 -c \"import os; os.mkdir('.agent/tasks/999-late')\"")
        r = self._close(d, home, "s339-close-late")
        self.assertNotIn("Task 001 done.", r.stdout)
        self.assertIn("[PASS] python3 -c", r.stdout)               # verify ran — and made the folder
        self.assertIn("appeared while this close ran: 999-late", r.stderr)
        self.assertIn("## Status\nin_progress", (d / ".agent" / "tasks" / "001-t" / "task.md").read_text(
            encoding="utf-8"))

    def test_a_registry_deleted_during_verify_refuses_at_the_commit(self):
        # task 197 P4 (agy, codex): the commit-time check read "no registry" as a pass
        d, home = self._repo("python3 -c \"import os; os.unlink('.agent/task-registry'); "
                             "os.mkdir('.agent/tasks/999-late')\"")
        r = self._close(d, home, "s339-close-noreg")
        self.assertNotIn("Task 001 done.", r.stdout)
        self.assertIn("999-late", r.stderr)

    def test_a_folder_verify_makes_and_re_enrols_refuses_at_the_commit(self):
        # task 197's impl panel r2 J5 at the commit: verify deletes the registry, makes a folder and runs a creator,
        # which re-enrols it — the re-check under the task lock reads the folders that stood at the close's first
        # check (task 339: its own listing; it was the watch's snapshot of the call, written by hand here before)
        d, home = self._repo("python3 -c \"import os, subprocess, sys; os.unlink('.agent/task-registry'); "
                             "os.mkdir('.agent/tasks/999-late'); open('.agent/tasks/999-late/task.md', 'w')"
                             ".write('# 999'); subprocess.run([sys.executable, '-m', 'tasks.cli', 'new', 'quick', "
                             "'z'], check=True)\"")
        r = self._close(d, home, "s339-commit-before")
        self.assertIn("999-late\tenrolled", (d / ".agent" / "task-registry").read_text(encoding="utf-8"))
        self.assertNotIn("Task 001 done.", r.stdout)
        self.assertIn("999-late", r.stderr)

    def test_a_tasks_folder_made_unlistable_during_verify_refuses_the_close(self):
        # task 196's impl panel r2 J9 (codex): verify makes a folder and takes the read bit off tasks/ — listing it
        # fails, the commit can still write task.md (the search bit stays); an empty listing would close over it
        d, home = self._repo("python3 -c \"import os; os.mkdir('.agent/tasks/999-late'); "
                             "os.chmod('.agent/tasks', 0o300)\"")
        tasks = d / ".agent" / "tasks"
        self.addCleanup(lambda: tasks.chmod(0o755) if tasks.exists() else None)
        r = self._close(d, home, "s339-close-unlistable")
        tasks.chmod(0o755)
        self.assertNotIn("Task 001 done.", r.stdout)
        self.assertIn("cannot be read", r.stderr)


if __name__ == "__main__":
    unittest.main()
