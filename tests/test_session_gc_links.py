"""The session sweepers delete nothing through a planted link (task 194, impl panel r1; grave, pre-existing).

Measured before the fix (task 194's `evidence/gc_link_probe.py`): in a project whose `.agent/sessions` is a link to a
directory holding `photos/ projects/ notes/ pid-999999/`, `session-start-hook` removed all four, and so did ANY tasks
CLI call (`_gc_dead_sessions` runs before every verb): each sweeper skipped a link AMONG the session directories but
never asked whether the sessions directory itself — or `.agent`, or a lane above it — is one, and a name that is not
`pid-*` with no fresh `current_state` reads as a dead session. A cloned repository can ship such a link (`.gitignore`'s
`.agent/sessions/` matches directories only). Now neither sweeper, nor session-end's removal of its own directory, acts
on a sessions directory that does not lie physically inside the project: nothing is removed, and the hook says why.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"
DIRS = ("photos", "projects", "notes", "pid-999999")


class TheSweepersFollowNoLink(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "home").mkdir()

    def victim(self, name="victim"):
        v = self.tmp / name
        for d in DIRS:
            (v / d).mkdir(parents=True)
            (v / d / "keep.txt").write_text("the owner's data\n", encoding="utf-8")
        return v

    def project(self, name):
        p = self.tmp / name
        (p / ".agent" / "tasks").mkdir(parents=True)
        return p

    def env(self):
        proc = self.tmp / "proc-no-agent"           # a /proc with no agent: the chain these tests mean
        (proc / "self").mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE", "PLAYBOOK_SANDBOXED", "PLAYBOOK_ROLE")}
        env.update(HOME=str(self.tmp / "home"), PLAYBOOK_SESSION_ID="sweep-sess",
                   CLAUDE_ENV_FILE=str(self.tmp / "envfile"), PLAYBOOK_PROC_ROOT=str(proc))
        return env

    def start(self, p):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / "session-start-hook")], cwd=p, env=self.env(), text=True,
                              input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                              capture_output=True, timeout=60)

    def cli(self, p, *verb):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / "tasks"), *verb], cwd=p, env=self.env(), text=True,
                              capture_output=True, timeout=120)

    def end(self, p):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / "session-end-hook")], cwd=p, env=self.env(), text=True,
                              input=json.dumps({"hook_event_name": "SessionEnd", "reason": "exit",
                                                "session_id": "sweep-sess"}),
                              capture_output=True, timeout=60)

    def assertUntouched(self, v):
        for d in DIRS:
            self.assertTrue((v / d / "keep.txt").is_file(), f"{d} was removed through the link")

    def test_a_linked_sessions_directory_is_not_swept(self):
        for who in ("session-start", "cli list", "cli status"):
            with self.subTest(sweeper=who):
                v = self.victim(f"v-{who.replace(' ', '-')}")
                p = self.project(f"p-{who.replace(' ', '-')}")
                (p / ".agent" / "sessions").symlink_to(v, target_is_directory=True)
                r = self.start(p) if who == "session-start" else self.cli(p, who.split()[1])
                self.assertUntouched(v)
                if who == "session-start":
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertIn("is a link", r.stderr)

    def test_a_linked_agent_or_lane_directory_is_not_swept(self):
        for shape in ("agent", "lane"):
            with self.subTest(shape=shape):
                v = self.tmp / f"state-{shape}"
                self.victim(f"state-{shape}/sessions")
                (v / "tasks").mkdir(parents=True, exist_ok=True)
                p = self.tmp / f"p-{shape}"
                if shape == "agent":
                    p.mkdir()
                    (p / ".agent").symlink_to(v, target_is_directory=True)
                else:
                    (p / ".agent").mkdir(parents=True)
                    (p / ".agent" / "current_user").write_text("alice\n", encoding="utf-8")
                    (p / ".agent" / "alice").symlink_to(v, target_is_directory=True)
                self.start(p)
                self.cli(p, "list")
                self.assertUntouched(v / "sessions")

    def test_session_end_removes_nothing_through_a_linked_sessions_directory(self):
        v = self.victim()
        (v / "sweep-sess").mkdir()
        (v / "sweep-sess" / "keep.txt").write_text("the owner's data\n", encoding="utf-8")
        p = self.project("p-end")
        (p / ".agent" / "sessions").symlink_to(v, target_is_directory=True)
        r = self.end(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((v / "sweep-sess" / "keep.txt").is_file(), "session-end removed a directory through the link")

    def test_control_a_plain_project_is_swept_as_before(self):
        for who in ("session-start", "cli"):
            with self.subTest(sweeper=who):
                p = self.project(f"plain-{who}")
                sessions = p / ".agent" / "sessions"
                (sessions / "old-legacy").mkdir(parents=True)
                (sessions / "old-legacy" / "current_state").write_text("001\n", encoding="utf-8")
                old = time.time() - 3 * 86400
                os.utime(sessions / "old-legacy" / "current_state", (old, old))
                (sessions / "fresh-legacy").mkdir()
                (sessions / "fresh-legacy" / "current_state").write_text("001\n", encoding="utf-8")
                self.start(p) if who == "session-start" else self.cli(p, "list")
                self.assertFalse((sessions / "old-legacy").exists(), "a dead session was not swept")
                self.assertTrue((sessions / "fresh-legacy").is_dir())

    def test_control_session_end_removes_its_own_plain_directory(self):
        p = self.project("plain-end")
        (p / ".agent" / "sessions" / "sweep-sess").mkdir(parents=True)
        r = self.end(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((p / ".agent" / "sessions" / "sweep-sess").exists())



SWAP = """#!/bin/bash
# swap the directory at $PB_SWAP_PATH for a link to $PB_SWAP_TO the first time this tool runs (when its arguments
# hold $PB_SWAP_WHEN), then run the real tool: the race a check-then-delete by path loses (impl panel r2, codex)
if [ ! -e "$PB_SWAP_DONE" ] && [[ "$*" == *"$PB_SWAP_WHEN"* ]]; then
    : > "$PB_SWAP_DONE"
    mv "$PB_SWAP_PATH" "$PB_SWAP_PATH.orig" && ln -s "$PB_SWAP_TO" "$PB_SWAP_PATH"
fi
exec "$PB_REAL" "$@"
"""


class TheDeletionsHoldTheirDirectory(unittest.TestCase):
    """impl panel r2 (codex, CRITICAL, reproduced): the sweepers checked containment by path and deleted by path, so a
    swap of `.agent/sessions` for a link in between redirected the delete into the link's target. Deletion now holds
    the directory it checked (bash: a `cd` verified with `pwd -P`, relative names; Python: descriptors opened with
    O_NOFOLLOW from the project root). And opus, sonnet: `tasks work done` and merge-prep deleted through a linked
    `sessions` as well; opus: session-start's `.claude/bin` temp sweep and the wrapper provisioning follow a linked
    `.claude`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "home").mkdir()

    def env(self, **extra):
        proc = self.tmp / "proc-no-agent"
        (proc / "self").mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE", "PLAYBOOK_SANDBOXED", "PLAYBOOK_ROLE")}
        env.update(HOME=str(self.tmp / "home"), PLAYBOOK_SESSION_ID="sweep-sess",
                   CLAUDE_ENV_FILE=str(self.tmp / "envfile"), PLAYBOOK_PROC_ROOT=str(proc))
        env.update(extra)
        return env

    def swapping(self, tool, path, to, when):
        """An env whose `tool` swaps `path` for a link to `to` right before its first run with `when` in its args."""
        import shutil as _sh
        bindir = self.tmp / f"swap-{tool}"
        bindir.mkdir()
        (bindir / tool).write_text(SWAP, encoding="utf-8")
        (bindir / tool).chmod(0o755)
        return self.env(PATH=f"{bindir}:{os.environ['PATH']}", PB_SWAP_PATH=str(path), PB_SWAP_TO=str(to),
                        PB_SWAP_WHEN=when, PB_SWAP_DONE=str(self.tmp / f"swapped-{tool}"), PB_REAL=_sh.which(tool))

    def victim_with(self, *names):
        v = self.tmp / "victim"
        for n in names:
            (v / n).mkdir(parents=True, exist_ok=True)
            (v / n / "keep.txt").write_text("the owner's data\n", encoding="utf-8")
        return v

    def hook(self, name, p, env, payload):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / name)], cwd=p, env=env, text=True,
                              input=json.dumps(payload), capture_output=True, timeout=60)

    def test_the_session_start_sweep_holds_its_directory_when_sessions_is_swapped(self):
        v = self.victim_with("photos")
        p = self.tmp / "p"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (p / ".agent" / "sessions" / "photos").mkdir(parents=True)     # a dead legacy session, named as a victim dir
        env = self.swapping("rm", p / ".agent" / "sessions", v, "photos")
        self.hook("session-start-hook", p, env, {"hook_event_name": "SessionStart", "source": "startup"})
        self.assertTrue((self.tmp / "swapped-rm").exists(), "the swap did not run: the test proves nothing")
        self.assertTrue((v / "photos" / "keep.txt").is_file(), "the sweep deleted through the swapped link")

    def test_session_end_holds_its_directory_when_sessions_is_swapped(self):
        v = self.victim_with("sweep-sess")
        p = self.tmp / "p"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (p / ".agent" / "sessions" / "sweep-sess").mkdir(parents=True)
        env = self.swapping("rm", p / ".agent" / "sessions", v, "sweep-sess")
        self.hook("session-end-hook", p, env, {"hook_event_name": "SessionEnd", "reason": "exit",
                                                "session_id": "sweep-sess"})
        self.assertTrue((self.tmp / "swapped-rm").exists(), "the swap did not run: the test proves nothing")
        self.assertTrue((v / "sweep-sess" / "keep.txt").is_file(), "session-end deleted through the swapped link")

    def test_the_wrapper_temp_sweep_holds_its_directory_when_claude_is_swapped(self):
        v = self.tmp / "victim-claude"
        (v / "bin").mkdir(parents=True)
        old = v / "bin" / "notes.tmp.1"
        old.write_text("the owner's data\n", encoding="utf-8")
        os.utime(old, (1_000_000_000, 1_000_000_000))
        p = self.tmp / "p"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (p / ".claude" / "bin").mkdir(parents=True)
        env = self.swapping("find", p / ".claude", v, ".tmp.")
        self.hook("session-start-hook", p, env, {"hook_event_name": "SessionStart", "source": "startup"})
        self.assertTrue((self.tmp / "swapped-find").exists(), "the swap did not run: the test proves nothing")
        self.assertTrue(old.is_file(), "the temp sweep deleted through the swapped link")

    def test_a_linked_claude_gets_no_temp_sweep(self):
        # (the wrapper provisioning still writes there — new or empty playbook files only; a linked `.claude` can be a
        # shared config folder: written as a BOUND, not refused)
        v = self.tmp / "victim-claude"
        (v / "bin").mkdir(parents=True)
        old = v / "bin" / "notes.tmp.1"
        old.write_text("the owner's data\n", encoding="utf-8")
        os.utime(old, (1_000_000_000, 1_000_000_000))
        (v / "bin" / "tasks").write_text("", encoding="utf-8")      # an empty file the heal would overwrite
        p = self.tmp / "p"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (p / ".claude").symlink_to(v, target_is_directory=True)
        self.hook("session-start-hook", p, self.env(), {"hook_event_name": "SessionStart", "source": "startup"})
        self.hook("task-gate-hook", p, self.env(), {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                                    "tool_input": {"command": "ls"}})
        self.assertTrue(old.is_file(), "the temp sweep ran through the linked .claude")

    def test_the_cli_sweep_holds_its_directory_when_sessions_is_swapped(self):
        v = self.victim_with("photos")
        p = self.tmp / "p"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (p / ".agent" / "sessions" / "photos").mkdir(parents=True)
        code = (
            "import sys, os; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
            "import tasks.shared as s\n"
            "real = s._session_is_dead\n"
            "def swap(d, own, cutoff):\n"
            "    sd = Path(sys.argv[2]) / '.agent' / 'sessions'\n"
            "    if not Path(sys.argv[4]).exists():\n"
            "        Path(sys.argv[4]).write_text('')\n"
            "        os.rename(sd, str(sd) + '.orig'); os.symlink(sys.argv[3], sd)\n"
            "    return True\n"
            "s._session_is_dead = swap\n"
            "s._gc_dead_sessions(Path(sys.argv[2]))\n")
        r = subprocess.run([sys.executable, "-c", code, str(PLUGIN), str(p), str(v), str(self.tmp / "swapped-py")],
                           env=self.env(), text=True, capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / "swapped-py").exists(), "the swap did not run: the test proves nothing")
        self.assertTrue((v / "photos" / "keep.txt").is_file(), "the CLI sweep deleted through the swapped link")

    def test_the_close_removes_nothing_through_a_linked_sessions_directory(self):
        v = self.victim_with("other")
        (v / "other" / "current_state").write_text("001\n", encoding="utf-8")   # another checkout's session on 001
        p = self.tmp / "p"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (p / ".agent" / "sessions").symlink_to(v, target_is_directory=True)
        cli = [bash_or_skip(), str(SCRIPTS / "tasks")]
        env = self.env()
        for args in (["new", "quick", "x"], ["work", "1"], ["work", "done", "--force", "--reason", "test of a close"]):
            subprocess.run(cli + args, cwd=p, env=env, text=True, capture_output=True, timeout=120)
        self.assertTrue((v / "other" / "keep.txt").is_file(), "the close removed a directory through the link")

    def test_merge_prep_unlinks_nothing_through_a_linked_sessions_directory(self):
        v = self.victim_with("other")
        (v / "other" / "chat_log_offset").write_text("12\n", encoding="utf-8")
        p = self.tmp / "p"
        (p / ".agent" / "tasks").mkdir(parents=True)
        (p / ".agent" / "sessions").symlink_to(v, target_is_directory=True)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
                "import tasks.merge_prep as m\n"
                "m._rename_colliding_tasks(Path(sys.argv[2]), Path(sys.argv[2]) / '.agent', {}, {})\n")
        r = subprocess.run([sys.executable, "-c", code, str(PLUGIN), str(p)], env=self.env(), text=True,
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((v / "other" / "chat_log_offset").is_file(), "merge-prep unlinked through the link")

    def test_a_link_inside_a_dead_session_is_removed_as_a_link(self):
        # the descriptor-based removal never follows a link below the directory it removes: a link inside a dead
        # session goes as a link, what it points at stays
        v = self.victim_with("photos")
        p = self.tmp / "p"
        (p / ".agent" / "tasks").mkdir(parents=True)
        dead = p / ".agent" / "sessions" / "old-legacy"
        dead.mkdir(parents=True)
        (dead / "inner").symlink_to(v, target_is_directory=True)
        for who in ("cli", "session-start"):
            with self.subTest(sweeper=who):
                if who == "cli":
                    subprocess.run([bash_or_skip(), str(SCRIPTS / "tasks"), "list"], cwd=p, env=self.env(),
                                   capture_output=True, timeout=120)
                else:
                    dead.mkdir(parents=True, exist_ok=True)
                    if not (dead / "inner").is_symlink():
                        (dead / "inner").symlink_to(v, target_is_directory=True)
                    self.hook("session-start-hook", p, self.env(), {"hook_event_name": "SessionStart",
                                                                    "source": "startup"})
                self.assertFalse(dead.exists(), "the dead session was not swept")
                self.assertTrue((v / "photos" / "keep.txt").is_file(), "the removal followed a link inside it")

    def test_pb_cwd_is_names_the_directory_the_shell_holds(self):
        p = self.tmp / "p"
        (p / ".agent" / "sessions").mkdir(parents=True)
        (self.tmp / "elsewhere").mkdir()
        (p / ".agent" / "linked").symlink_to(self.tmp / "elsewhere", target_is_directory=True)
        script = ('source "$1"; cd "$2/.agent/sessions" && _pb_cwd_is "$2" "$2/.agent/sessions" && echo REAL; '
                  'cd "$2/.agent/linked" && _pb_cwd_is "$2" "$2/.agent/linked" || echo LINKED-REFUSED; '
                  'cd "$2/.agent/sessions" && _pb_cwd_is "$2" "$2/.agent/linked" || echo OTHER-REFUSED')
        r = subprocess.run([bash_or_skip(), "-c", script, "x", str(SCRIPTS / "gate-echo-lib.sh"), str(p)],
                           text=True, capture_output=True, timeout=30)
        self.assertEqual(r.stdout.split(), ["REAL", "LINKED-REFUSED", "OTHER-REFUSED"], r.stderr)

    def test_control_the_close_and_merge_prep_in_a_plain_project(self):
        p = self.tmp / "plain"
        (p / ".agent" / "tasks").mkdir(parents=True)
        cli = [bash_or_skip(), str(SCRIPTS / "tasks")]
        env = self.env()
        for args in (["new", "quick", "x"], ["work", "1"]):
            subprocess.run(cli + args, cwd=p, env=env, text=True, capture_output=True, timeout=120)
        sess = p / ".agent" / "sessions" / "sweep-sess"
        self.assertEqual((sess / "current_state").read_text(encoding="utf-8").strip(), "001")
        (sess / "chat_log_offset").write_text("3\n", encoding="utf-8")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
                "import tasks.merge_prep as m\n"
                "m._rename_colliding_tasks(Path(sys.argv[2]), Path(sys.argv[2]) / '.agent', {}, {})\n")
        subprocess.run([sys.executable, "-c", code, str(PLUGIN), str(p)], env=env, timeout=60)
        self.assertFalse((sess / "chat_log_offset").exists(), "merge-prep left the offset in a plain project")
        subprocess.run(cli + ["work", "done", "--force", "--reason", "test of a close"], cwd=p, env=env,
                       text=True, capture_output=True, timeout=120)
        self.assertFalse(sess.exists(), "the close left its session in a plain project")


if __name__ == "__main__":
    unittest.main()
