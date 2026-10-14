"""Task 196 (PLAN S14 T5b, B31 = #12): a task folder made by hand is DETECTED after the call that made it.

The owner's answers (2026-10-13 12:27): detect after every shell command and at the close; provenance from the task
registry (task 195); the session's next action held until the folder is removed or made by the CLI, with a journal
line. The plan panel's requirements (task 186 R3-2, task 196 P1-P20): a way out — the exact removal of the flagged
folder, the `tasks` CLI, the read tools —, only a session whose own call saw the folder appear held, the session's
own lane watched, Write watched like a shell call, a slow git or an unreadable registry never silently waved through.

Every test fires the hooks hooks.json wires for the event and the tool — the task gate, the command guard, the state
echo and the watch together, as Claude Code runs them — with payloads shaped like the ones Claude Code 2.1.296 sends
(measured in task 196: `tool_use_id` in both, PostToolUseFailure after a command that exits non-zero), and the tree
CLI for `tasks new` / `tasks adopt` / `tasks work done`.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip
from tests._oldpython import make_oldpython_path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
CLI = PLUGIN / "scripts" / "tasks"
COMMAND_GUARD = PLUGIN / "scripts" / "command-guard-hook"
HEADER = "# playbook task-registry v1"
SID = "s196-detector-a"
SID_B = "s196-detector-b"


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

    # -- fixtures
    def _task(self, lane: Path, name: str, md: bool = True) -> Path:
        d = lane / "tasks" / name
        d.mkdir(parents=True)
        if md:
            (d / "task.md").write_text(f"# {name}\n\n## Status\npending\n", encoding="utf-8")
        return d

    def _registry(self, lane: Path, rows) -> None:
        text = HEADER + "\n" + "".join(f"{n}\t{o}\t2026-10-14T00:00:00Z\n" for n, o in rows)
        (lane / "task-registry").write_text(text, encoding="utf-8")

    def _env(self, sid=SID, path=None):
        # a /proc with no agent: the session is the env id alone, as on CI — a judge's nested run must not decide
        proc = self.base / "proc-no-agent"
        (proc / "self").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PLAYBOOK_SESSION_ID=sid, HOME=str(self.home), CLAUDE_PLUGIN_ROOT=str(PLUGIN),
                   PLAYBOOK_PROC_ROOT=str(proc))
        for k in ("BASH_ENV", "PLAYBOOK_SANDBOXED", "CLAUDE_PROJECT_DIR", "PLAYBOOK_EVAL_CONFIG", "PLAYBOOK_ROLE"):
            env.pop(k, None)
        if path is not None:
            env["PATH"] = path
        return env

    # -- the hooks, as hooks.json wires them
    def fire(self, event, payload, sid=SID, path=None, cwd=None, only=None):
        """Every hook hooks.json runs for `event` and this tool (`only`: those whose command names it). (blocked,
        stderr, stdouts)."""
        hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"].get(event, [])
        tool = payload.get("tool_name", "")
        blocked, errs, outs = False, [], []
        for entry in hooks:
            m = entry.get("matcher")
            if m not in (None, "", "*") and not re.fullmatch(m, tool):
                continue
            for h in entry["hooks"]:
                if only and only not in h["command"]:
                    continue
                r = subprocess.run([bash_or_skip(), "-c", h["command"]], cwd=cwd or self.proj,
                                   env=self._env(sid, path), input=json.dumps(payload), text=True,
                                   capture_output=True, timeout=60)
                blocked = blocked or r.returncode == 2
                errs.append(r.stderr)
                outs.append(r.stdout)
        return blocked, "\n".join(errs), outs

    def _payload(self, event, tool, tool_input, tuid, sid, cwd=None):
        p = {"session_id": sid, "cwd": str(cwd or self.proj), "hook_event_name": event, "tool_name": tool,
             "tool_input": tool_input}
        if tuid is not None:
            p["tool_use_id"] = tuid
        return p

    def pre(self, command, tuid: "str | None" = "toolu_01PRE", sid=SID, path=None, cwd=None):
        return self.fire("PreToolUse", self._payload("PreToolUse", "Bash", {"command": command, "description": "t"},
                                                     tuid, sid, cwd), sid, path, cwd)

    def post(self, command, tuid: "str | None" = "toolu_01PRE", sid=SID, failed=False, path=None):
        event = "PostToolUseFailure" if failed else "PostToolUse"
        p = self._payload(event, "Bash", {"command": command, "description": "t"}, tuid, sid)
        if failed:
            p.update(error="Exit code 1", is_interrupt=False, duration_ms=5)
        else:
            p.update(tool_response={"stdout": "", "stderr": "", "interrupted": False, "isImage": False},
                     duration_ms=5)
        return self.fire(event, p, sid, path)

    def edit(self, rel="README.md", sid=SID):
        return self.fire("PreToolUse", self._payload("PreToolUse", "Edit",
                                                     {"file_path": str(self.proj / rel), "old_string": "a",
                                                      "new_string": "b"}, "toolu_01EDIT", sid), sid)

    def write_call(self, rel, tuid="toolu_01W", sid=SID):
        """A Write as the host runs it: the PreToolUse hooks, the file written, the PostToolUse hooks."""
        ti = {"file_path": str(self.proj / rel), "content": "x\n"}
        b, e, _ = self.fire("PreToolUse", self._payload("PreToolUse", "Write", ti, tuid, sid), sid)
        self.assertFalse(b, f"the Write itself was refused: {e}")
        (self.proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.proj / rel).write_text("x\n", encoding="utf-8")
        return self.fire("PostToolUse", dict(self._payload("PostToolUse", "Write", ti, tuid, sid),
                                             tool_response={"type": "create", "filePath": str(self.proj / rel)}), sid)

    def shell_call(self, command, effect, tuid: "str | None" = "toolu_01PRE", sid=SID, failed=False, path=None):
        """One shell call as the host runs it: the PreToolUse hooks, the command's effect, the PostToolUse hooks."""
        b, e, _ = self.pre(command, tuid, sid, path)
        self.assertFalse(b, f"the call itself was refused: {e}")
        effect()
        return self.post(command, tuid, sid, failed, path)

    def cli(self, *args, sid=SID):
        return subprocess.run([bash_or_skip(), str(CLI), *args], cwd=self.proj, env=self._env(sid),
                              text=True, capture_output=True, timeout=120)

    # -- what the watch leaves
    def flagged(self, sid=SID, lane=None):
        p = (lane or self.lane) / "sessions" / sid / "task_dir_flag"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8")).get("folders")

    def journal(self, lane=None):
        p = (lane or self.lane) / "journal" / "enforcement.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class FlaggedAfterTheCall(_Project):

    def test_a_folder_made_by_a_shell_call_flags_the_session(self):
        _, _, outs = self.shell_call("cp -r .agent/tasks/001-a .agent/tasks/999-x",
                                     lambda: shutil.copytree(self.lane / "tasks" / "001-a",
                                                             self.lane / "tasks" / "999-x"))
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])
        said = "".join(outs)
        self.assertIn(".agent/tasks/999-x", said)               # said to the agent (additionalContext)
        self.assertIn('"hookEventName": "PostToolUse"', said)
        self.assertTrue(any(j.get("hook") == "task-dir-watch" and j.get("decision") == "record"
                            and j.get("reason") == "unknown task folder" and j.get("path") == ".agent/tasks/999-x"
                            for j in self.journal()), self.journal())
        self.assertTrue({j.get("decision") for j in self.journal()} <= {"allow", "block", "record", "session-walk"})

    def test_a_failing_call_is_caught_too(self):
        # measured: a command that exits non-zero fires PostToolUseFailure, never PostToolUse
        _, _, outs = self.shell_call("cp -r .agent/tasks/001-a .agent/tasks/999-x; false",
                                     lambda: self._task(self.lane, "999-x"), failed=True)
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])
        self.assertIn('"hookEventName": "PostToolUseFailure"', "".join(outs))

    def test_a_folder_made_by_a_write_is_caught(self):
        # Guard 0 judges only `.../task.md`; any other file written into a new task folder makes the folder
        self.write_call(".agent/tasks/999-x/notes.md")
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])

    def test_tasks_new_inside_a_shell_call_is_not_flagged(self):
        self.shell_call("tasks new quick b", lambda: self.assertEqual(self.cli("new", "quick", "b").returncode, 0))
        self.assertIsNone(self.flagged())

    def test_a_folder_there_before_the_call_is_not_flagged(self):
        self._task(self.lane, "998-y")                         # hand-made earlier: the close's to find
        self.shell_call("ls", lambda: None)
        self.assertIsNone(self.flagged())

    def test_no_registry_no_watch(self):
        (self.lane / "task-registry").unlink()
        self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"))
        self.assertIsNone(self.flagged())
        self.assertEqual(list((self.lane / "sessions" / SID).glob("task_dirs_before*")), [])

    def test_a_committed_folder_is_not_flagged(self):
        git = shutil.which("git") or self.skipTest("no git")
        env = dict(self._env(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        subprocess.run([git, "init", "-q"], cwd=self.proj, check=True, env=env)

        def pull():                                             # a pull or a checkout brings a committed task
            self._task(self.lane, "997-z")
            subprocess.run([git, "add", ".agent/tasks/997-z/task.md"], cwd=self.proj, check=True, env=env)
            subprocess.run([git, "commit", "-qm", "z"], cwd=self.proj, check=True, env=env)
        self.shell_call("git pull", pull)
        self.assertIsNone(self.flagged())

    def test_a_git_that_does_not_answer_leaves_the_folder_unproven(self):
        # the committed rule asks git; a git slower than the watch's budget proves nothing — flagged, in time, and
        # git is asked ONCE: the first timeout ends the asking (not five refs × the budget)
        fake = self.base / "slowgit"
        fake.mkdir()
        calls = self.base / "git-calls"
        (fake / "git").write_text(f"#!/bin/bash\necho call >> '{calls}'\nexec sleep 30\n", encoding="utf-8")
        (fake / "git").chmod(0o755)
        path = f"{fake}:{os.environ.get('PATH', '')}"
        t0 = time.monotonic()
        self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"), path=path)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])
        self.assertEqual(calls.read_text(encoding="utf-8").count("call"), 1)

    def test_a_second_flag_keeps_the_first(self):
        # two calls in flight; the second's snapshot already holds the first's folder, so its post sees only its own
        # — the flag must keep both (a rewrite with the new folders alone would drop the first)
        self.assertFalse(self.pre("cp a", "toolu_U1")[0])
        self._task(self.lane, "998-y")
        self.assertFalse(self.pre("cp b", "toolu_U2")[0])
        self.post("cp a", "toolu_U1")
        self.assertEqual(self.flagged(), [".agent/tasks/998-y"])
        self._task(self.lane, "999-x")
        self.post("cp b", "toolu_U2")
        self.assertEqual(self.flagged(), [".agent/tasks/998-y", ".agent/tasks/999-x"])

    def test_a_reserved_folder_while_a_creator_holds_the_lock_is_not_flagged(self):
        self._registry(self.lane, [("001-a", "new"), ("995-r", "reserved")])
        with open(self.lane / "task-registry.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)                      # a creator between its rename and its registration
            self.shell_call("ls", lambda: self._task(self.lane, "995-r"))
        self.assertIsNone(self.flagged())

    def test_a_reserved_folder_with_the_lock_free_is_flagged(self):
        self._registry(self.lane, [("001-a", "new"), ("995-r", "reserved")])
        self.shell_call("ls", lambda: self._task(self.lane, "995-r"))
        self.assertEqual(self.flagged(), [".agent/tasks/995-r"])

    def test_a_folder_name_outside_the_task_pattern_is_ignored(self):
        self.shell_call("mk", lambda: (self.lane / "tasks" / "notes").mkdir())
        self.assertIsNone(self.flagged())

    def test_parallel_calls_of_one_session_keep_their_own_snapshots(self):
        # A makes the folder after its snapshot; B's snapshot is taken once it exists. One snapshot per session
        # would be overwritten by B's, and A's post would see nothing new.
        self.assertFalse(self.pre("cp -r", "toolu_A")[0])
        self._task(self.lane, "999-x")
        self.assertFalse(self.pre("sleep 1", "toolu_B")[0])
        self.post("sleep 1", "toolu_B")
        self.assertIsNone(self.flagged())
        self.post("cp -r", "toolu_A")
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])

    def test_a_payload_without_tool_use_id_is_still_watched(self):
        self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"), tuid=None)
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])

    def test_a_call_with_no_snapshot_flags_every_unknown_folder_present(self):
        self._task(self.lane, "998-y")
        self.post("ls", "toolu_NOPRE")                          # its PreToolUse never wrote one
        self.assertEqual(self.flagged(), [".agent/tasks/998-y"])

    def test_old_snapshots_are_swept(self):
        sd = self.lane / "sessions" / SID
        sd.mkdir(parents=True)
        old, fresh = sd / "task_dirs_before.toolu_OLD", sd / "task_dirs_before.toolu_FRESH"
        for f in (old, fresh):
            f.write_text('{"v": 1, "folders": []}', encoding="utf-8")
        two_days = time.time() - 2 * 86400
        os.utime(old, (two_days, two_days))
        self.pre("ls", "toolu_NEW")
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_an_unreadable_registry_never_holds_and_is_said(self):
        real = self.base / "reg"
        (self.lane / "task-registry").rename(real)
        (self.lane / "task-registry").symlink_to(real)          # a link: the reader refuses it
        _, _, outs = self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"))
        self.assertIsNone(self.flagged())
        self.assertIn("task-registry", "".join(outs))
        self.assertFalse(self.edit()[0])

    def test_a_registry_deleted_during_a_call_is_said(self):
        _, _, outs = self.shell_call("rm .agent/task-registry", lambda: (self.lane / "task-registry").unlink())
        self.assertIn("task-registry", "".join(outs))
        self.assertTrue(any(j.get("hook") == "task-dir-watch" and "registry" in j.get("reason", "")
                            for j in self.journal()), self.journal())

    def test_two_posts_at_once_keep_both_folders(self):
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                flag = self.lane / "sessions" / SID / "task_dir_flag"
                if flag.exists():
                    flag.unlink()
                a, b = f"99{attempt}-a", f"98{attempt}-b"
                self.assertFalse(self.pre("x", f"toolu_A{attempt}")[0])
                self._task(self.lane, b)
                self.assertFalse(self.pre("y", f"toolu_B{attempt}")[0])
                self._task(self.lane, a)
                ts = [threading.Thread(target=self.post, args=("x", f"toolu_A{attempt}")),
                      threading.Thread(target=self.post, args=("y", f"toolu_B{attempt}"))]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
                self.assertEqual(set(self.flagged() or []), {f".agent/tasks/{a}", f".agent/tasks/{b}"})
                shutil.rmtree(self.lane / "tasks" / a)
                shutil.rmtree(self.lane / "tasks" / b)


class TheSessionsAndTheLanes(_Project):

    def test_another_session_is_not_held(self):
        self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"))
        self.assertFalse(self.edit(sid=SID_B)[0])
        self.assertFalse(self.pre("ls", sid=SID_B)[0])

    def test_a_session_whose_call_did_not_overlap_is_not_flagged(self):
        self.shell_call("ls", lambda: None, sid=SID_B)
        self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"))
        self.assertIsNone(self.flagged(SID_B))

    def test_two_sessions_with_overlapping_calls_are_both_flagged(self):
        # the stated bound (plan panel P1): a listing cannot tell which of two overlapping calls made the folder
        self.assertFalse(self.pre("cp -r", "toolu_A")[0])
        self.assertFalse(self.pre("ls", "toolu_B", sid=SID_B)[0])
        self._task(self.lane, "999-x")
        self.post("cp -r", "toolu_A")
        self.post("ls", "toolu_B", sid=SID_B)
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])
        self.assertEqual(self.flagged(SID_B), [".agent/tasks/999-x"])
        b, err, _ = self.edit(sid=SID_B)
        self.assertTrue(b)
        self.assertIn("another session", err)

    def test_only_the_sessions_own_lane_is_watched(self):
        shutil.rmtree(self.lane / "tasks")
        (self.lane / "task-registry").unlink()
        alice, bob = self.lane / "alice", self.lane / "bob"
        for lane, n in ((alice, "001-al"), (bob, "001-bo")):
            self._task(lane, n)
            self._registry(lane, [(n, "new")])
        (self.lane / "current_user").write_text("alice\n", encoding="utf-8")
        self.shell_call("cp -r", lambda: self._task(bob, "999-x"))
        self.assertIsNone(self.flagged(lane=alice))
        self.shell_call("cp -r", lambda: self._task(alice, "999-y"), tuid="toolu_02")
        self.assertEqual(self.flagged(lane=alice), [".agent/alice/tasks/999-y"])


class HeldUntilResolved(_Project):

    def setUp(self):
        super().setUp()
        self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"))
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])

    def test_the_next_edit_write_and_shell_call_are_refused_naming_the_way_out(self):
        checks = [("Edit", lambda: self.edit()), ("Edit .agent", lambda: self.edit(".agent/notes.md")),
                  ("Write", lambda: self.fire("PreToolUse", self._payload(
                      "PreToolUse", "Write", {"file_path": str(self.proj / "b.md"), "content": "x"}, "toolu_W2", SID))),
                  ("Bash", lambda: self.pre("ls"))]
        for what, call in checks:
            with self.subTest(what=what):
                blocked, err, _ = call()
                self.assertTrue(blocked, what)
                self.assertIn(".agent/tasks/999-x", err)
                self.assertIn("rm -r .agent/tasks/999-x", err)
                self.assertIn("tasks adopt .agent/tasks/999-x", err)
        self.assertTrue(any(j.get("hook") == "task-dir-watch" and j.get("decision") == "block"
                            for j in self.journal()), self.journal())

    def test_anything_but_the_way_out_is_refused(self):
        os.symlink(self.base, self.proj / "link")
        for cmd in ("ls", "rm -r .agent/tasks/999-x; ls", "rm -r .agent/tasks/999-x && echo ok",
                    "rm -r .agent/tasks/999-x | cat", "rm -r .agent/tasks/999-x .agent/tasks/001-a",
                    "rm -r .agent/tasks/*", "rm -r .agent/tasks", "rm -r $D", "rm -r .agent/tasks/999-x > /dev/null",
                    "rmdir -p .agent/tasks/999-x", "rm -r .agent/tasks/999-x\nls", "/tmp/evil/tasks adopt x",
                    "tasks status; ls", "rm -r link/../proj/.agent/tasks/999-x",
                    "rm -i .agent/tasks/999-x", "cd /tmp && ls", "cd", "mv .agent/tasks/999-x /tmp/x",
                    "rm -r \"$(echo .agent/tasks/999-x)\"", "rm -r '.agent/tasks/999-x"):
            with self.subTest(cmd=cmd):
                self.assertTrue(self.pre(cmd)[0], cmd)

    def test_the_way_out_passes(self):
        for cmd in ("rm -r .agent/tasks/999-x", "rm -rf .agent/tasks/999-x/", "rm -r -f .agent/tasks/999-x",
                    f"rm -r {self.proj}/.agent/tasks/999-x", "rm -r './.agent/tasks/999-x'",
                    'rm -r ".agent/tasks/999-x"', "rmdir .agent/tasks/999-x", "rm -r -- .agent/tasks/999-x",
                    "rm --recursive --force .agent/tasks/999-x", ".claude/bin/tasks adopt .agent/tasks/999-x",
                    "tasks status", f"{CLI} adopt 999-x", f"cd {self.proj}", "cd .agent",
                    "rm -r .agent/tasks/001-a/../999-x"):           # `..` through a real folder: the same folder
            with self.subTest(cmd=cmd):
                blocked, err, _ = self.pre(cmd)
                self.assertFalse(blocked, f"{cmd}: {err}")

    def test_the_way_out_from_a_subdirectory(self):
        sub = self.proj / "sub"
        sub.mkdir()
        for cmd in ("rm -r ../.agent/tasks/999-x", f"rm -r {self.proj}/.agent/tasks/999-x"):
            with self.subTest(cmd=cmd):
                blocked, err, _ = self.pre(cmd, cwd=sub)
                self.assertFalse(blocked, f"{cmd}: {err}")
        blocked, err, _ = self.pre("ls", cwd=sub)
        self.assertIn("rm -r ../.agent/tasks/999-x", err)       # the suggestion is from the call's cwd

    def test_the_suggested_removal_passes_the_command_guard_too(self):
        sub = self.proj / "sub"
        sub.mkdir()
        _, err, _ = self.pre("ls", cwd=sub)
        m = re.search(r"rm -r \S+", err)
        assert m, err
        suggested = m.group(0)
        r = subprocess.run([bash_or_skip(), str(COMMAND_GUARD)], cwd=sub, env=self._env(), text=True,
                           capture_output=True, input=json.dumps({"tool_name": "Bash",
                                                                  "tool_input": {"command": suggested}}))
        self.assertEqual(r.returncode, 0, f"{suggested!r}: {r.stderr}")

    def test_removed_clears_the_flag(self):
        shutil.rmtree(self.lane / "tasks" / "999-x")
        self.assertFalse(self.edit()[0])
        self.assertIsNone(self.flagged())

    def test_adopted_clears_the_flag(self):
        r = self.cli("adopt", ".agent/tasks/999-x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.edit()[0])
        self.assertIsNone(self.flagged())

    def test_a_watch_that_cannot_run_holds_a_flagged_session(self):
        # fail closed while a flag stands, as the gate holds on a status it cannot read; a post is never held
        fake = self.base / "brokenpy"
        fake.mkdir()
        (fake / "python3").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
        (fake / "python3").chmod(0o755)
        path = f"{fake}:{os.environ.get('PATH', '')}"
        cmd = {"command": "ls", "description": "t"}
        blocked, err, _ = self.fire("PreToolUse", self._payload("PreToolUse", "Bash", cmd, "toolu_X", SID),
                                    path=path, only="task-dir-watch")
        self.assertTrue(blocked)
        self.assertIn("could not run", err)
        blocked, _, outs = self.fire("PostToolUse", self._payload("PostToolUse", "Bash", cmd, "toolu_X", SID),
                                     path=path, only="task-dir-watch")
        self.assertFalse(blocked)
        said = json.loads("".join(outs))["hookSpecificOutput"]
        self.assertEqual(said["hookEventName"], "PostToolUse")
        self.assertIn("could not run", said["additionalContext"])

    def test_a_flag_file_that_is_a_link_is_never_followed(self):
        p = self.lane / "sessions" / SID / "task_dir_flag"
        target = self.base / "elsewhere.json"
        target.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        p.unlink()
        p.symlink_to(target)
        shutil.rmtree(self.lane / "tasks" / "999-x")
        self.edit()
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["folders"], [".agent/tasks/999-x"])


class NamesAdoptCannotTake(_Project):

    def test_a_name_with_a_space_and_a_dollar_can_be_removed(self):
        for name in ("999-my task", "998-a$b"):
            with self.subTest(name=name):
                self.shell_call("cp -r", lambda: self._task(self.lane, name), tuid=f"toolu_{name[:3]}")
                blocked, err, _ = self.edit()
                self.assertTrue(blocked)
                self.assertIn("adopt cannot", err)
                blocked, err, _ = self.pre(f"rm -r '.agent/tasks/{name}'")
                self.assertFalse(blocked, err)
                shutil.rmtree(self.lane / "tasks" / name)
                self.assertFalse(self.edit()[0])

    def test_a_folder_without_a_task_md_is_offered_removal_only(self):
        self.shell_call("mkdir", lambda: self._task(self.lane, "999-x", md=False))
        blocked, err, _ = self.edit()
        self.assertTrue(blocked)
        self.assertIn("rm -r .agent/tasks/999-x", err)
        self.assertNotIn("tasks adopt .agent/tasks/999-x", err)


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
        self._task(self.lane, "999-x")                         # made outside any watched call
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

    def test_the_close_without_a_registry_says_nothing_of_folders(self):
        self._active()
        (self.lane / "task-registry").unlink()
        self._task(self.lane, "999-x")
        r = self.cli("work", "done")
        self.assertNotIn("999-x", r.stdout + r.stderr)


class BelowTheFloor(_Project):

    def test_nothing_is_watched_and_nothing_crashes(self):
        # the watch alone: below the floor the gate refuses what it cannot read (task 194), whatever the watch does
        old = make_oldpython_path(self.base / "oldpy")
        cmd = {"command": "cp -r", "description": "t"}
        b, err, outs = self.fire("PreToolUse", self._payload("PreToolUse", "Bash", cmd, "toolu_F", SID), path=old,
                                 only="task-dir-watch")
        self.assertEqual(len(outs), 1, "the watch is not wired for a shell call")
        self.assertFalse(b, err)
        self._task(self.lane, "999-x")
        b, err, _ = self.fire("PostToolUse", self._payload("PostToolUse", "Bash", cmd, "toolu_F", SID), path=old,
                              only="task-dir-watch")
        self.assertFalse(b, err)
        self.assertIsNone(self.flagged())
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
