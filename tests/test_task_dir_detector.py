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
import sys
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
        self.proj = self.base / getattr(self, "PROJ_NAME", "proj")
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

    def edit_call(self, rel, old, new, effect, tuid="toolu_01E"):
        """An Edit as the host runs it (an empty `old` creates the file and its folders — measured, task 196 I12)."""
        ti = {"file_path": str(self.proj / rel), "old_string": old, "new_string": new}
        b, e, _ = self.fire("PreToolUse", self._payload("PreToolUse", "Edit", ti, tuid, SID))
        self.assertFalse(b, f"the Edit itself was refused: {e}")
        effect()
        return self.fire("PostToolUse", dict(self._payload("PostToolUse", "Edit", ti, tuid, SID),
                                             tool_response={"filePath": str(self.proj / rel)}))

    def helper(self, payload, session_dir):
        """task-dir-watch.py run directly — past the bash hook's own checks."""
        return subprocess.run([sys.executable, "-E", "-s", str(PLUGIN / "scripts" / "task-dir-watch.py"),
                               str(self.proj), str(self.lane), str(session_dir), SID], input=json.dumps(payload),
                              text=True, capture_output=True, env=self._env(), timeout=60)

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

    def test_a_lane_whose_registry_is_gone_is_still_watched(self):
        # task 197 P1 (opus, fable, sonnet): with the Bash check gone, a lane with no registry must not be unguarded —
        # what the git rule does not know is unknown there
        (self.lane / "task-registry").unlink()
        self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"))
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])

    def test_re_enrolment_after_a_flag_does_not_release_it(self):
        # task 197 P3: with the registry gone any creator re-enrols every folder with a task.md — the flagged one too;
        # that is no release: only `tasks adopt`, its removal or a commit is
        (self.lane / "task-registry").unlink()
        self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"))
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])
        r = self.cli("new", "quick", "b")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.lane / "task-registry").read_text(encoding="utf-8").count("999-x\tenrolled"), 1)
        self.assertTrue(self.edit()[0])
        r = self.cli("adopt", ".agent/tasks/999-x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.edit()[0])

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

    def test_a_file_replaced_by_a_folder_of_its_name_is_caught(self):
        # the listing is of DIRECTORIES: a file `999-x` before the call, a folder `999-x` after it — new
        (self.lane / "tasks" / "999-x").write_text("x", encoding="utf-8")

        def swap():
            (self.lane / "tasks" / "999-x").unlink()
            self._task(self.lane, "999-x")
        self.shell_call("rm; cp -r", swap)
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])

    def test_a_file_is_not_a_task_folder(self):
        # impl panel r1 I11: `12-notes.md` (a file) is no task folder — not flagged, and never refuses a close
        self.shell_call("touch", lambda: (self.lane / "tasks" / "999-notes.md").write_text("x", encoding="utf-8"))
        self.assertIsNone(self.flagged())

    def test_an_edit_that_creates_a_file_makes_its_folder_and_is_caught(self):
        # impl panel r1 I12, measured: an Edit with an empty old_string creates the file AND its missing folders
        def made():
            (self.lane / "tasks" / "999-x").mkdir()
            (self.lane / "tasks" / "999-x" / "notes.md").write_text("n\n", encoding="utf-8")
        self.edit_call(".agent/tasks/999-x/notes.md", "", "n", made)
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])

    def test_an_edit_of_an_existing_file_is_not_watched(self):
        self.edit_call("README.md", "a", "b", lambda: None)
        self.assertEqual(list((self.lane / "sessions" / SID).glob("task_dirs_before*"))
                         if (self.lane / "sessions" / SID).exists() else [], [])

    def test_a_name_that_is_not_utf8_is_flagged_and_removable(self):
        # impl panel r1 I4 (codex, reproduced): a name with byte 0xff broke the flag's write — nothing persisted
        raw = os.fsencode(str(self.lane / "tasks")) + b"/999-\xff"
        self.shell_call("cp -r", lambda: os.mkdir(raw))
        self.assertEqual(self.flagged(), [".agent/tasks/999-\udcff"])
        blocked, err, _ = self.edit()
        self.assertTrue(blocked)
        self.assertIn("rm -r $'.agent/tasks/999-\\xff'", err)
        blocked, err, _ = self.pre("rm -r $'.agent/tasks/999-\\xff'")
        self.assertFalse(blocked, err)
        os.rmdir(raw)
        self.assertFalse(self.edit()[0])

    def test_a_registry_removed_during_a_call_flags_what_the_call_made(self):
        # impl panel r1 I6 (sonnet S3): no registry after the call — what it made is known to nothing
        def both():
            (self.lane / "task-registry").unlink()
            self._task(self.lane, "999-x")
        self.shell_call("rm .agent/task-registry; cp -r", both)
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])

    def test_a_linked_session_directory_is_never_swept_nor_written(self):
        # impl panel r1 I1 (codex, Critical, reproduced): the session directory swapped for a link after the hook's
        # check let the snapshot sweep delete files outside the project
        outside = self.base / "outside"
        outside.mkdir()
        old = outside / "task_dirs_before.toolu_OLD"
        old.write_text('{"v": 1, "folders": []}', encoding="utf-8")
        two_days = time.time() - 2 * 86400
        os.utime(old, (two_days, two_days))
        (self.lane / "sessions").mkdir()
        (self.lane / "sessions" / SID).symlink_to(outside)
        for event in ("PreToolUse", "PostToolUse"):
            with self.subTest(event=event):
                self.helper(self._payload(event, "Bash", {"command": "ls"}, "toolu_L", SID),
                            self.lane / "sessions" / SID)
                self.assertTrue(old.exists())
                self.assertEqual(sorted(p.name for p in outside.iterdir()), ["task_dirs_before.toolu_OLD"])

    def test_a_lane_reached_through_a_link_is_used_and_never_swept(self):
        # a shared folder as the lane (task 195's rule): watched, but nothing in it swept
        shared = self.base / "shared"
        shutil.move(str(self.lane), str(shared))
        self.lane.symlink_to(shared)
        sd = shared / "sessions" / SID
        sd.mkdir(parents=True)
        old = sd / "task_dirs_before.toolu_OLD"
        old.write_text('{"v": 1, "folders": []}', encoding="utf-8")
        two_days = time.time() - 2 * 86400
        os.utime(old, (two_days, two_days))
        self.shell_call("cp -r", lambda: self._task(shared, "999-x"), tuid="toolu_LL")
        self.assertTrue(old.exists())
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])

    def test_a_flag_lock_never_had_at_a_post_still_flags(self):
        # impl panel r2 J5/J8 (sonnet, codex): the lock not had within its wait — the flag is written without it
        sd = self.lane / "sessions" / SID
        sd.mkdir(parents=True)
        with open(sd / "task_dir_flag.lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"), tuid="toolu_LK")
            self.assertTrue(list(sd.glob("task_dir_flag.pending.*")))   # recorded as a pending entry (K1)
            self.assertTrue(self.edit()[0])                              # which holds, the lock still not had
        self.assertTrue(self.edit()[0])                                  # merged under the lock: still held
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])
        self.assertEqual(list(sd.glob("task_dir_flag.pending.*")), [])

    def test_a_flag_that_cannot_be_written_is_said_with_its_folder(self):
        # the session directory not writable: nothing persists — the agent is told WHICH folder, and the close refuses
        sd = self.lane / "sessions" / SID
        self.assertFalse(self.pre("cp -r", "toolu_RO")[0])
        self._task(self.lane, "999-x")
        sd.chmod(0o500)
        self.addCleanup(sd.chmod, 0o755)
        _, _, outs = self.post("cp -r", "toolu_RO")
        self.assertIn(".agent/tasks/999-x", "".join(outs))

    def test_a_tasks_folder_that_cannot_be_listed_is_never_read_as_empty(self):
        # impl panel r2 J9 (codex): an enumeration error became an empty listing — nothing new, nothing flagged
        tasks = self.lane / "tasks"
        self.addCleanup(tasks.chmod, 0o755)

        def lock_out():
            self._task(self.lane, "999-x")
            tasks.chmod(0o300)                              # no read: it cannot be listed
        _, _, outs = self.shell_call("cp -r; chmod", lock_out)
        self.assertIn("could not run", "".join(outs))

    def test_a_tasks_folder_whose_entries_cannot_be_examined_is_never_read_as_empty(self):
        # r2 J9: listed but not searchable (no x) — an entry's stat fails; never read as "no folder"
        tasks = self.lane / "tasks"
        self.addCleanup(tasks.chmod, 0o755)

        def lock_out():
            self._task(self.lane, "999-x")
            tasks.chmod(0o600)
        _, _, outs = self.shell_call("cp -r; chmod", lock_out)
        self.assertIn("could not run", "".join(outs))

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


class EveryLaneIsWatched(_Project):
    """Task 197 (PLAN S14 T5b, the last part): the Bash pre-run check is gone (the owner's answer of 2026-10-13 12:27,
    "O scot pentru shell"); a lane is enrolled at the session start, and a folder made by hand is caught after the call
    whatever the command's spelling."""

    def test_the_session_start_enrols_a_lane_without_a_registry(self):
        (self.lane / "task-registry").unlink()
        self.fire("SessionStart", {"session_id": SID, "cwd": str(self.proj), "hook_event_name": "SessionStart",
                                   "source": "startup"})
        reg = self.lane / "task-registry"
        self.assertTrue(reg.is_file())
        self.assertIn("001-a\tenrolled\t", reg.read_text(encoding="utf-8"))

    def _caught(self, command, name):
        # each spelling in a session of its own (one fixture per subtest)
        sid = "s197-" + name
        blocked, err, _ = self.pre(command, "toolu_M" + name, sid=sid)
        self.assertFalse(blocked, f"{command!r} refused before it ran: {err}")
        self._task(self.lane, name)
        self.post(command, "toolu_M" + name, sid=sid)
        self.assertEqual(self.flagged(sid), [f".agent/tasks/{name}"])

    def test_a_real_mkdir_runs_and_is_caught_after(self):
        self._caught("mkdir -p .agent/tasks/901-x", "901-x")

    def test_a_brace_spelling_runs_and_is_caught_after(self):
        self._caught("mkdir -p .agent/tasks/{902-x,902-y}", "902-x")

    def test_an_ansi_c_spelling_runs_and_is_caught_after(self):
        self._caught("mkdir -p $'.agent/tasks/903-x'", "903-x")

    def test_the_helper_without_a_mode_is_no_judge(self):
        r = subprocess.run([sys.executable, "-E", "-s", str(PLUGIN / "scripts" / "task-dir-target.py")],
                           input="mkdir -p .agent/tasks/9-x", text=True, capture_output=True,
                           env=dict(self._env(), PB_PROJECT=str(self.proj)))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("usage", r.stderr.lower())


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
        # `jump/..` is, physically, another project holding its own `.claude/bin/tasks` (impl panel r1 I3, codex)
        other = self.base / "other"
        (other / "sub").mkdir(parents=True)
        (other / ".claude" / "bin").mkdir(parents=True)
        (other / ".claude" / "bin" / "tasks").write_text("#!/bin/bash\n", encoding="utf-8")
        os.symlink(other / "sub", self.proj / "jump")
        (self.proj / ".claude" / "bin").mkdir(parents=True, exist_ok=True)
        (self.proj / ".claude" / "bin" / "tasks").write_text("#!/bin/bash\n", encoding="utf-8")
        for cmd in ("ls", "rm -r .agent/tasks/999-x; ls", "rm -r .agent/tasks/999-x && echo ok",
                    "rm -r .agent/tasks/999-x | cat", "rm -r .agent/tasks/999-x .agent/tasks/001-a",
                    "rm -r .agent/tasks/*", "rm -r .agent/tasks", "rm -r $D", "rm -r .agent/tasks/999-x > /dev/null",
                    "rmdir -p .agent/tasks/999-x", "rm -r .agent/tasks/999-x\nls", "/tmp/evil/tasks adopt x",
                    "tasks status; ls", "rm -r link/../proj/.agent/tasks/999-x", "tasks status",
                    "jump/../.claude/bin/tasks adopt 999-x", ".claude/bin/tasks status; ls",
                    ".claude/bin/tasks status && ls", ".claude/bin/tasks status | cat",
                    ".claude/bin/tasks status > /tmp/x", "cd .agent; ls", "cd .agent", f"cd {self.proj}",
                    "cd /tmp", "rm -r $'.agent/tasks/999-\\cA'", "rm -r $'.agent/tasks/999-\\u0041'",
                    "rm -i .agent/tasks/999-x", "cd /tmp && ls", "cd", "mv .agent/tasks/999-x /tmp/x",
                    "rm -r \"$(echo .agent/tasks/999-x)\"", "rm -r '.agent/tasks/999-x"):
            with self.subTest(cmd=cmd):
                self.assertTrue(self.pre(cmd)[0], cmd)

    def test_the_way_out_passes(self):
        for cmd in ("rm -r .agent/tasks/999-x", "rm -rf .agent/tasks/999-x/", "rm -r -f .agent/tasks/999-x",
                    f"rm -r {self.proj}/.agent/tasks/999-x", "rm -r './.agent/tasks/999-x'",
                    'rm -r ".agent/tasks/999-x"', "rmdir .agent/tasks/999-x", "rm -r -- .agent/tasks/999-x",
                    "rm --recursive --force .agent/tasks/999-x", ".claude/bin/tasks adopt .agent/tasks/999-x",
                    f"{CLI} adopt 999-x", "rm -r $'.agent/tasks/999-x'", "rm -r $'.agent/tasks/999\\x2dx'",
                    "rm -r .agent/tasks/001-a/../999-x"):           # `..` through a real folder: the same folder
            with self.subTest(cmd=cmd):
                blocked, err, _ = self.pre(cmd)
                self.assertFalse(blocked, f"{cmd}: {err}")

    def test_the_way_out_reads_ansi_c_as_bash_does(self):
        # impl panel r2 J7 (codex): an escape decoded otherwise than bash decodes it names another folder — only the
        # escapes bash decodes the same everywhere are read; each compared with bash's own reading
        from importlib import util
        spec = util.spec_from_file_location("tdw", PLUGIN / "scripts" / "task-dir-watch.py")
        mod = util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for word in ("$'a\\x41b'", "$'\\101'", "$'x\\ty'", "$'it\\'s'", "$'\\\\'", "$'\\e'", "$'\\x7'",
                     "$'\\0101'", "$'\\xffz'", "$'q\\\"'", "$'\\?'", "$'\\a\\b\\f\\v\\r\\n'"):
            with self.subTest(word=word):
                ours = mod._words("rm " + word)
                self.assertIsNotNone(ours, word)
                theirs = subprocess.run([bash_or_skip(), "-c", f"printf '%s' {word}"], capture_output=True).stdout
                self.assertEqual(ours[1].encode("utf-8", "surrogateescape"), theirs, word)
        for word in ("$'\\cA'", "$'\\u0041'", "$'\\U00000041'", "$'\\q'"):
            with self.subTest(refused=word):
                self.assertIsNone(mod._words("rm " + word), word)

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

    def test_a_hold_outlives_the_registry_and_adopt_ends_it(self):
        # impl panel r1 I6 (fable): with the registry gone nothing knows the folder — still held, and said
        (self.lane / "task-registry").unlink()
        blocked, err, _ = self.edit()
        self.assertTrue(blocked)
        self.assertIn("task registry", err)
        self.assertTrue(any(j.get("hook") == "task-dir-watch" and j.get("decision") == "record"
                            and "registry" in j.get("reason", "") for j in self.journal()), self.journal())
        r = self.cli("adopt", ".agent/tasks/999-x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.edit()[0])
        self.assertIsNone(self.flagged())

    def test_an_unreadable_registry_at_a_hold_is_journaled(self):
        # impl panel r1 I7 (fable): not held (its repair must stay possible), but journaled
        real = self.base / "reg"
        (self.lane / "task-registry").rename(real)
        (self.lane / "task-registry").symlink_to(real)
        self.assertFalse(self.edit()[0])
        self.assertTrue(any(j.get("hook") == "task-dir-watch" and j.get("decision") == "record"
                            and j.get("reason") == "task registry unreadable" for j in self.journal()),
                        self.journal())

    def test_a_flag_lock_never_had_holds_without_hanging(self):
        # impl panel r1 I14: the flag's lock is waited for a bounded time; not had, a flagged session stays held
        lock = self.lane / "sessions" / SID / "task_dir_flag.lock"
        with open(lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            t0 = time.monotonic()
            blocked, err, _ = self.edit()
            self.assertLess(time.monotonic() - t0, 15)
        self.assertTrue(blocked, err)

    def _watch_module(self):
        from importlib import util
        spec = util.spec_from_file_location("tdw_k1", PLUGIN / "scripts" / "task-dir-watch.py")
        mod = util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_a_detection_made_while_a_recheck_runs_is_never_lost(self):
        # the single judge's run 1, K1 (codex): a post that cannot have the lock (a re-check holds it) records its
        # folder; the re-check, which read the flag BEFORE that, must not remove it. Reproduced in-process: the post
        # happens inside the re-check, while the lock is held.
        mod = self._watch_module()
        mod.LOCK_WAIT = 0.2
        shutil.rmtree(self.lane / "tasks" / "999-x")          # the flagged folder is gone: the re-check will clear
        self._task(self.lane, "998-y")                         # the folder the racing post finds
        sfd, _inside = mod._open_session(self.proj, self.lane, SID)
        self.addCleanup(os.close, sfd)
        real = mod._unknown

        def racing(project, lane, names):
            mod._record_flag(sfd, ".agent", [".agent/tasks/998-y"])
            return real(project, lane, names)
        mod._unknown = racing
        mod._recheck(self.proj, self.lane, sfd)
        mod._unknown = real
        left, _state, _note = mod._recheck(self.proj, self.lane, sfd)
        self.assertEqual(left, ["998-y"])
        self.assertTrue(self.edit()[0])

    def test_a_linked_folder_whose_target_cannot_be_examined_stays_held(self):
        # the single judge's run 1, K3 (codex): EACCES on a linked folder's target read as "gone" — the hold cleared
        target_parent = self.base / "elsewhere"
        (target_parent / "real").mkdir(parents=True)
        (target_parent / "real" / "task.md").write_text("# x\n", encoding="utf-8")
        shutil.rmtree(self.lane / "tasks" / "999-x")          # the fixture's hold ends at the next call
        self.shell_call("ln -s", lambda: os.symlink(target_parent / "real", self.lane / "tasks" / "998-l"),
                        tuid="toolu_K3")
        self.assertIn(".agent/tasks/998-l", self.flagged())
        target_parent.chmod(0o600)
        self.addCleanup(target_parent.chmod, 0o755)
        self.assertTrue(self.edit()[0])

    def test_a_flagged_session_whose_directory_became_a_link_is_held(self):
        # task 193's single judge, run 3 (M3): the hook let a flagged session through once its directory was a link
        sd = self.lane / "sessions" / SID
        moved = self.base / "moved-session"
        shutil.move(str(sd), str(moved))
        sd.symlink_to(moved)
        self.assertTrue(self.edit()[0])
        self.assertTrue(self.pre("ls")[0])

    def test_from_inside_the_flagged_folder_the_suggestion_works(self):
        # task 195's single judge, run 3 (N3): `rm -r .` from inside the folder works for nothing — an absolute path
        inside = self.lane / "tasks" / "999-x"
        _, err, _ = self.pre("ls", cwd=inside)
        self.assertIn(f"rm -r {inside}", err)
        blocked, err2, _ = self.pre(f"rm -r {inside}", cwd=inside)
        self.assertFalse(blocked, err2)

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


class APathWithGlobCharacters(_Project):
    """Task 195's single judge, run 3 (N2): `compgen -G` read the session path's `[...]` as a glob — a pending entry
    in `proj[one]` was missed and the next Edit passed."""
    PROJ_NAME = "proj[one]"

    def test_a_pending_entry_holds_in_such_a_project(self):
        sd = self.lane / "sessions" / SID
        sd.mkdir(parents=True)
        with open(sd / "task_dir_flag.lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"), tuid="toolu_GL")
            self.assertTrue(list(sd.glob("task_dir_flag.pending.*")))
            self.assertTrue(self.edit()[0])


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
        self.assertIn("may be someone's work", err)                # impl panel r2 J3: the warning on removal too


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

    def test_a_file_in_tasks_never_refuses_the_close(self):
        # impl panel r1 I11: a FILE with a task-like name is no task folder
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


class AReEnrolmentLaundersNothing(_Project):
    """Task 197's impl panel r1: I2 (fable, opus, sonnet) — the registry gone, a hand-made folder flagged, `tasks new`
    (a way out) re-enrols it `enrolled`, and `tasks work done` (a way out) closed over it: the rule was the watch's
    alone. I3 (fable, opus) — ONE call that makes the folder and re-enrols the lane left it never flagged."""

    def _activate(self, title, sid=SID):
        r = self.cli("new", "quick", title, sid=sid)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        num = re.search(r"tasks/(\d+)-", r.stdout).group(1)
        self.assertEqual(self.cli("work", num, sid=sid).returncode, 0)

    def test_the_close_refuses_after_a_re_enrolment_in_the_flagged_session_and_in_another(self):
        self._activate("c")
        self._activate("d", sid=SID_B)
        (self.lane / "task-registry").unlink()
        self.shell_call("cp -r", lambda: self._task(self.lane, "999-x"))
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])
        r = self.cli("new", "quick", "b")                     # a way out of the hold — it re-enrols the lane
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("999-x\tenrolled\t", (self.lane / "task-registry").read_text(encoding="utf-8"))
        for sid in (SID, SID_B):                              # the flagged session, and one the watch never held
            r = self.cli("work", "done", sid=sid)
            self.assertNotEqual(r.returncode, 0, sid)
            self.assertIn(".agent/tasks/999-x", r.stderr, sid)
        self.assertEqual(self.cli("adopt", ".agent/tasks/999-x").returncode, 0)
        self.assertNotIn("999-x", self.cli("work", "done", sid=SID_B).stderr)

    def test_one_call_that_makes_a_folder_and_re_enrols_the_lane_is_caught(self):
        (self.lane / "task-registry").unlink()

        def effect():
            self._task(self.lane, "999-x")
            r = self.cli("new", "quick", "b")                 # inside the same call: the lane re-enrolled
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.shell_call("cp -r … && .claude/bin/tasks new quick b", effect)
        self.assertIn("999-x\tenrolled\t", (self.lane / "task-registry").read_text(encoding="utf-8"))
        self.assertEqual(self.flagged(), [".agent/tasks/999-x"])       # the CLI's own `002-b` is not
        self.assertTrue(self.edit()[0])

    def test_the_bound_a_one_lane_project_with_no_tasks_folder(self):
        # impl panel r2 J1, kept as a BOUND by the single judge's run 1 (K1): with no `.agent/tasks` the hooks see no
        # project (`find_project_root`), so the call's pre takes no snapshot; the post cannot tell what stood, and an
        # `enrolled` row is trusted there (reading it as made by the call held every session after a close, K1).
        # A folder made by hand alone, with no creator in the call, is still flagged (no row knows it).
        shutil.rmtree(self.lane / "tasks")
        (self.lane / "task-registry").unlink()
        self.shell_call("mkdir -p …", lambda: self._task(self.lane, "009-x"))
        self.assertEqual(self.flagged(), [".agent/tasks/009-x"])

    def test_a_close_inside_the_call_that_made_the_folder_refuses(self):
        # impl panel r2 J5 (codex, reproduced): one call deletes the registry, makes a folder, runs `tasks new` and
        # `tasks work done` — the close ran before the post could flag anything, and passed
        (self.lane / "task-registry").unlink()
        said = {}

        def effect():
            self._task(self.lane, "999-x")
            r = self.cli("new", "quick", "b")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            num = re.search(r"tasks/(\d+)-", r.stdout).group(1)
            self.assertEqual(self.cli("work", num).returncode, 0)
            said["close"] = self.cli("work", "done")
        self.shell_call("rm …; cp -r …; tasks new quick b; tasks work N; tasks work done", effect)
        self.assertNotEqual(said["close"].returncode, 0)
        self.assertIn(".agent/tasks/999-x", said["close"].stderr)

    def test_a_close_that_removes_its_session_flags_no_legacy_folder(self):
        # the single judge's run 1, K1 (codex): a successful close removes its session directory, the call's snapshot
        # with it — the post then read every uncommitted `enrolled` folder as made by the call and held the session
        self._registry(self.lane, [("001-a", "new"), ("050-legacy", "enrolled")])
        self._task(self.lane, "050-legacy")
        sd = self.lane / "sessions" / SID
        self.shell_call("tasks work done", lambda: shutil.rmtree(sd))
        self.assertIsNone(self.flagged())
        self.assertFalse(self.edit()[0])

    def test_another_sessions_running_call_is_seen_by_a_close(self):
        # the single judge's run 1, K3 (codex): session A's call made a folder and re-enrolled the lane but has not
        # posted yet; session B's close read only B's snapshots, which already held the folder, and passed
        r = self.cli("new", "quick", "d", sid=SID_B)
        num = re.search(r"tasks/(\d+)-", r.stdout).group(1)
        self.assertEqual(self.cli("work", num, sid=SID_B).returncode, 0)
        (self.lane / "task-registry").unlink()
        b, e, _ = self.pre("cp -r …; tasks new quick b", "toolu_A1")         # A's call starts: its snapshot
        self.assertFalse(b, e)
        # a live session: every CLI call sweeps a session whose id is no live agent and whose pointer is not fresh
        (self.lane / "sessions" / SID / "current_state").write_text("001\n", encoding="utf-8")
        self._task(self.lane, "999-x")
        self.assertEqual(self.cli("new", "quick", "b").returncode, 0)       # …re-enrols, and A has not posted
        self.assertTrue((self.lane / "sessions" / SID / "task_dirs_before.toolu_A1").exists())
        b, e, _ = self.pre("tasks work done", "toolu_B1", sid=SID_B)        # B's close, inside a call of B's
        self.assertFalse(b, e)
        r = self.cli("work", "done", sid=SID_B)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".agent/tasks/999-x", r.stderr)

    def test_a_lane_with_no_tasks_folder_in_a_multi_user_project_gets_its_snapshot(self):
        # impl panel r2 J1: the hook left before python in a lane with no `tasks/` — here the project is found through
        # another lane's, so the snapshot of `[]` is what lets the close inside the call see the folder it made
        shutil.rmtree(self.lane / "tasks")
        (self.lane / "task-registry").unlink()
        alice, bob = self.lane / "alice", self.lane / "bob"
        self._task(bob, "001-bo")
        self._registry(bob, [("001-bo", "new")])
        alice.mkdir()
        (self.lane / "current_user").write_text("alice\n", encoding="utf-8")
        said = {}

        def effect():
            self._task(alice, "009-x")
            r = self.cli("new", "quick", "b")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            num = re.search(r"tasks/(\d+)-", r.stdout).group(1)
            self.assertEqual(self.cli("work", num).returncode, 0)
            said["close"] = self.cli("work", "done")
        self.shell_call("make a folder; tasks new quick b; tasks work N; tasks work done", effect)
        self.assertNotEqual(said["close"].returncode, 0)
        self.assertIn(".agent/alice/tasks/009-x", said["close"].stderr)


class TheCloseRechecks(unittest.TestCase):
    """Impl panel r1 I2 (codex, reproduced): a folder made while the close's verify ran must still refuse it."""

    def test_a_folder_made_during_verify_refuses_the_close(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / "repo"
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        late = "python3 -c \"import os; os.mkdir('.agent/tasks/999-late')\""
        (d / ".agent" / "tasks" / "001-t").mkdir(parents=True)
        (d / ".agent" / "config.json").write_text(json.dumps({"verify": late, "panel_required_for": []}),
                                                  encoding="utf-8")
        (d / ".agent" / "tasks" / "001-t" / "task.md").write_text(
            "# 001 - T\n\n## Status\npending\n\n## Risk\nreversible\n\n## Work Plan\n- [x] G1: do it\n",
            encoding="utf-8")
        (d / ".agent" / "task-registry").write_text(HEADER + "\n001-t\tnew\t2026-10-14T00:00:00Z\n",
                                                   encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="s196-close-late", HOME=tmp.name)
        env.pop("BASH_ENV", None)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "1"], cwd=d, env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done"], cwd=d, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertNotIn("Task 001 done.", r.stdout)
        self.assertIn("[PASS] python3 -c", r.stdout)               # verify ran — and made the folder
        self.assertIn("appeared while this close ran: 999-late", r.stderr)
        self.assertIn("## Status\nin_progress", (d / ".agent" / "tasks" / "001-t" / "task.md").read_text(
            encoding="utf-8"))

    def test_a_registry_deleted_during_verify_refuses_at_the_commit(self):
        # task 197 P4 (agy, codex): the commit-time check read "no registry" as a pass
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / "repo"
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        late = ("python3 -c \"import os; os.unlink('.agent/task-registry'); os.mkdir('.agent/tasks/999-late')\"")
        (d / ".agent" / "tasks" / "001-t").mkdir(parents=True)
        (d / ".agent" / "config.json").write_text(json.dumps({"verify": late, "panel_required_for": []}),
                                                  encoding="utf-8")
        (d / ".agent" / "tasks" / "001-t" / "task.md").write_text(
            "# 001 - T\n\n## Status\npending\n\n## Risk\nreversible\n\n## Work Plan\n- [x] G1: do it\n",
            encoding="utf-8")
        (d / ".agent" / "task-registry").write_text(HEADER + "\n001-t\tnew\t2026-10-14T00:00:00Z\n",
                                                   encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="s197-close-noreg", HOME=tmp.name)
        env.pop("BASH_ENV", None)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "1"], cwd=d, env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done"], cwd=d, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertNotIn("Task 001 done.", r.stdout)
        self.assertIn("999-late", r.stderr)

    def test_a_folder_verify_makes_and_re_enrols_refuses_at_the_commit(self):
        # impl panel r2 J5 at the commit: verify deletes the registry, makes a folder and runs a creator (which
        # re-enrols it) — the re-check under the task lock reads the session's standing snapshot too
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / "repo"
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        late = ("python3 -c \"import os, subprocess, sys; os.unlink('.agent/task-registry'); "
                "os.mkdir('.agent/tasks/999-late'); open('.agent/tasks/999-late/task.md', 'w').write('# 999'); "
                "subprocess.run([sys.executable, '-m', 'tasks.cli', 'new', 'quick', 'z'], check=True)\"")
        (d / ".agent" / "tasks" / "001-t").mkdir(parents=True)
        (d / ".agent" / "config.json").write_text(json.dumps({"verify": late, "panel_required_for": []}),
                                                  encoding="utf-8")
        (d / ".agent" / "tasks" / "001-t" / "task.md").write_text(
            "# 001 - T\n\n## Status\npending\n\n## Risk\nreversible\n\n## Work Plan\n- [x] G1: do it\n",
            encoding="utf-8")
        (d / ".agent" / "task-registry").write_text(HEADER + "\n001-t\tnew\t2026-10-14T00:00:00Z\n",
                                                   encoding="utf-8")
        sid = "s197-commit-before"
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID=sid, HOME=tmp.name)
        env.pop("BASH_ENV", None)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "1"], cwd=d, env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        # the watch's snapshot of the call that runs the close: what stood before it
        (d / ".agent" / "sessions" / sid / "task_dirs_before.toolu_C").write_text(
            json.dumps({"v": 1, "folders": ["001-t"], "registry": True}), encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done"], cwd=d, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertIn("999-late\tenrolled", (d / ".agent" / "task-registry").read_text(encoding="utf-8"))
        self.assertNotIn("Task 001 done.", r.stdout)
        self.assertIn("999-late", r.stderr)

    def test_a_tasks_folder_made_unlistable_during_verify_refuses_the_close(self):
        # impl panel r2 J9 (codex): verify makes a folder and takes the read bit off tasks/ — listing it fails, the
        # commit can still write task.md (the search bit stays); an empty listing would have closed over the folder
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / "repo"
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        tasks = d / ".agent" / "tasks"
        self.addCleanup(lambda: tasks.chmod(0o755) if tasks.exists() else None)
        late = ("python3 -c \"import os; os.mkdir('.agent/tasks/999-late'); os.chmod('.agent/tasks', 0o300)\"")
        (tasks / "001-t").mkdir(parents=True)
        (d / ".agent" / "config.json").write_text(json.dumps({"verify": late, "panel_required_for": []}),
                                                  encoding="utf-8")
        (tasks / "001-t" / "task.md").write_text(
            "# 001 - T\n\n## Status\npending\n\n## Risk\nreversible\n\n## Work Plan\n- [x] G1: do it\n",
            encoding="utf-8")
        (d / ".agent" / "task-registry").write_text(HEADER + "\n001-t\tnew\t2026-10-14T00:00:00Z\n",
                                                   encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="s196-close-unlistable", HOME=tmp.name)
        env.pop("BASH_ENV", None)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "1"], cwd=d, env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done"], cwd=d, env=env,
                           capture_output=True, text=True, timeout=120)
        tasks.chmod(0o755)
        self.assertNotIn("Task 001 done.", r.stdout)
        self.assertIn("cannot be read", r.stderr)


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
