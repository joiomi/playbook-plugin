#!/usr/bin/env python3
"""Remember the last green verify (task 228, PHASE-B.md item 3).

The owner, 2026-10-16 09:42 (task 218): "Ține minte ultima verde (Recomandat)" — when exactly the same code already
passed the verify contract through the CLI, the close does not run it again. The verify commands here append to a
marker file under `.agent/` each time they run (outside the code key), so a test sees whether one ran; they fail while
`.agent/red.flag` exists.

Pure stdlib unittest. Run: python3 tests/test_verify_memory.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import verify_memory  # noqa: E402


def marker_cmd(name):
    return ("python3 -c \"import pathlib,sys; p=pathlib.Path('.agent/ran-" + name + ".log'); "
            "p.write_text((p.read_text() if p.exists() else '')+'x'); "
            "sys.exit(1 if pathlib.Path('.agent/red.flag').exists() else 0)\"")


A = marker_cmd("a")
B = marker_cmd("b")


class Project(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb228-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.g("init", "-q")
        (self.d / "home").mkdir()
        (self.d / ".gitignore").write_text("home/\n.agent/journal/\n.agent/sessions/\n.agent/*.log\n.agent/red.flag\n",
                                           encoding="utf-8")
        (self.d / "src.py").write_text("x = 1\n", encoding="utf-8")
        (self.d / ".agent" / "tasks").mkdir(parents=True)
        self.contract(A)
        self.g("add", "-A")
        self.g("commit", "-qm", "start")

    def contract(self, *cmds, extra=None):
        cfg = {"verify": cmds[0] if len(cmds) == 1 else {"_always": list(cmds)}}
        cfg.update(extra or {})
        (self.d / ".agent" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    def g(self, *args, cwd=None):
        return subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=p", *args], cwd=cwd or self.d,
                              capture_output=True, text=True, check=True)

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update({"PYTHONPATH": str(_PLAYBOOK), "HOME": str(self.d / "home"),
                    "PLAYBOOK_SESSION_ID": "pid-verify-probe"})
        return env

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.d, env=self.env(),
                              capture_output=True, text=True, timeout=300)

    def runs(self, name="a"):
        p = self.d / ".agent" / f"ran-{name}.log"
        return len(p.read_text()) if p.exists() else 0

    def red(self, on=True):
        f = self.d / ".agent" / "red.flag"
        f.write_text("") if on else f.unlink(missing_ok=True)

    def key(self):
        return verify_memory.code_key(self.d)

    def green(self, key, cmd, started, seconds):
        """A green run as the close records it: its start marked first, then its result."""
        verify_memory.begin(self.d, key, cmd, started)
        return verify_memory.record(self.d, key, key, cmd, True, started=started, seconds=seconds)

    def recorded(self, cmd=A):
        return verify_memory.reusable(self.d, self.key(), cmd, timeout=1200)


class TheKey(Project):

    def test_stable_and_blind_to_agent_records(self):
        k = self.key()
        self.assertTrue(k)
        self.assertEqual(self.key(), k)
        (self.d / ".agent" / "tasks" / "note.md").write_text("a record\n", encoding="utf-8")
        self.assertEqual(self.key(), k)

    def test_every_kind_of_code_change_moves_it(self):
        changes = {
            "tracked edit": lambda: (self.d / "src.py").write_text("x = 2\n", encoding="utf-8"),
            "same-size edit": lambda: (self.d / "src.py").write_text("y = 1\n", encoding="utf-8"),
            "untracked file": lambda: (self.d / "new.py").write_text("y = 1\n", encoding="utf-8"),
            "deletion": lambda: (self.d / "src.py").unlink(),
            "mode": lambda: (self.d / "src.py").chmod(0o755),
            "LF to CRLF": lambda: (self.d / "src.py").write_bytes(b"x = 1\r\n"),
        }
        k = self.key()
        mode = (self.d / "src.py").stat().st_mode & 0o7777
        for name, change in changes.items():
            change()
            with self.subTest(change=name):
                self.assertNotEqual(self.key(), k)
            (self.d / "new.py").unlink(missing_ok=True)
            self.g("checkout", "-q", "--", "src.py")
            (self.d / "src.py").chmod(mode)
            self.assertEqual(self.key(), k, name)

    def test_staging_an_untracked_file_moves_it(self):
        (self.d / "new.py").write_text("y = 1\n", encoding="utf-8")
        k = self.key()
        self.g("add", "new.py")
        self.assertNotEqual(self.key(), k)

    def test_the_owner_exclude_does_not_hide_code(self):
        self.contract(A, extra={"fingerprint_exclude": ["src.py"]})
        k = self.key()
        (self.d / "src.py").write_text("x = 2\n", encoding="utf-8")
        self.assertNotEqual(self.key(), k)

    def test_a_nested_code_root(self):
        sub = self.d / "nested"
        sub.mkdir()
        self.g("init", "-q", cwd=sub)
        (sub / "lib.py").write_text("z = 1\n", encoding="utf-8")
        self.g("add", "-A", cwd=sub)
        self.g("commit", "-qm", "n", cwd=sub)
        (self.d / ".gitignore").write_text((self.d / ".gitignore").read_text() + "nested/\n", encoding="utf-8")
        self.contract(A, extra={"code_roots": ["nested"]})
        k = self.key()
        self.assertTrue(k)
        (sub / "lib.py").write_text("z = 2\n", encoding="utf-8")
        self.assertNotEqual(self.key(), k)

    def test_no_key_for_a_repository_outside_the_scopes(self):
        sub = self.d / "embedded"
        sub.mkdir()
        self.g("init", "-q", cwd=sub)
        (sub / "lib.py").write_text("z = 1\n", encoding="utf-8")
        self.g("add", "-A", cwd=sub)
        self.g("commit", "-qm", "e", cwd=sub)
        self.assertIsNone(self.key())                                    # an untracked embedded repository
        self.g("add", "embedded")                                        # now a gitlink
        self.assertIsNone(self.key())

    def test_no_key_during_an_unfinished_merge(self):
        main = self.g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.g("checkout", "-qb", "side")
        (self.d / "src.py").write_text("x = 'side'\n", encoding="utf-8")
        self.g("commit", "-qam", "side")
        self.g("checkout", "-q", main)
        (self.d / "src.py").write_text("x = 'main'\n", encoding="utf-8")
        self.g("commit", "-qam", "main")
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=p", "merge", "-q", "side"], cwd=self.d,
                       capture_output=True)
        self.assertIsNone(self.key())

    def test_no_key_with_hidden_index_bits(self):
        for flag in ("--skip-worktree", "--assume-unchanged"):
            self.g("update-index", flag, "src.py")
            with self.subTest(flag=flag):
                self.assertIsNone(self.key())
            self.g("update-index", flag.replace("--", "--no-"), "src.py")
        self.assertTrue(self.key())


class Round1(Project):
    """Impl round 1: each case is a reuse on code that would fail — a guard failing open."""

    def test_a_staged_change_moves_the_key(self):
        """grok: `git diff --check` reads the index — a staged change restored in the worktree must move the key."""
        k = self.key()
        (self.d / "src.py").write_text("x = 1 \n", encoding="utf-8")
        self.g("add", "src.py")
        (self.d / "src.py").write_text("x = 1\n", encoding="utf-8")
        self.assertNotEqual(self.key(), k)

    def test_tracked_agent_files_count_task_records_do_not(self):
        """grok: a tracked `.agent/config.json` is code a verify can read; the task records are not."""
        k = self.key()
        rec = self.d / ".agent" / "tasks" / "001-x"
        rec.mkdir(parents=True)
        (rec / "task.md").write_text("# x\n", encoding="utf-8")
        self.g("add", "-A")
        (rec / "task.md").write_text("# x, ticked\n", encoding="utf-8")
        self.assertEqual(self.key(), k)
        cfg = self.d / ".agent" / "config.json"
        cfg.write_text(cfg.read_text() + " ", encoding="utf-8")
        self.assertNotEqual(self.key(), k)

    def test_a_newer_red_run_is_not_undone_by_an_older_green(self):
        """codex, grok: a red run leaves its start time; an older green finishing after it is not recorded."""
        k = self.key()
        verify_memory.record(self.d, k, k, A, False, started=200.0, seconds=1.0)
        verify_memory.begin(self.d, k, A, 100.0)
        self.assertEqual(verify_memory.record(self.d, k, k, A, True, started=100.0, seconds=1.0), "kept")
        self.assertIsNone(verify_memory.reusable(self.d, k, A, timeout=1200))

    def test_a_code_root_without_its_own_repository(self):
        """codex: a `code_roots` dir with no `.git` of its own would be listed through the outer repo — no key."""
        (self.d / "nested").mkdir()
        (self.d / "nested" / "lib.py").write_text("z = 1\n", encoding="utf-8")
        (self.d / ".gitignore").write_text((self.d / ".gitignore").read_text() + "nested/\n", encoding="utf-8")
        self.contract(A, extra={"code_roots": ["nested"]})
        self.assertIsNone(self.key())

    def test_a_red_result_that_cannot_be_written_still_ends_the_green(self):
        """codex: the run's start is written before the command runs; if the journal cannot be written, the record is
        removed — never left green for code that just failed."""
        k = self.key()
        self.green(k, A, 1.0, 50.0)
        with mock.patch("tasks.atomic.atomic_write", side_effect=OSError("disk full")):
            out = verify_memory.run_contract(self.d, [("config", A)], 10, lambda c: (1, "red"))
        self.assertEqual(out[0][2], 1)
        self.assertIsNone(verify_memory.reusable(self.d, k, A, timeout=None))

    def test_a_run_that_dies_midway_leaves_no_green(self):
        """codex: the start is marked red before the command runs — a run killed midway (here: the runner raises)
        leaves no green record for that code."""
        k = self.key()
        self.green(k, A, 1.0, 50.0)

        def dies(_cmd):
            raise RuntimeError("killed")
        with self.assertRaises(RuntimeError):
            verify_memory.run_contract(self.d, [("config", A)], 10, dies)
        self.assertIsNone(verify_memory.reusable(self.d, k, A, timeout=None))

    def test_the_key_never_raises(self):
        """sonnet, opus: a key that cannot be built is no key — never a traceback in the close."""
        with mock.patch("os.readlink", side_effect=OSError("gone")):
            (self.d / "link.py").symlink_to("src.py")
            self.assertIsNone(self.key())
        with mock.patch.object(verify_memory, "_scope_digest", side_effect=MemoryError()):
            self.assertIsNone(self.key())

    def test_a_link_out_of_the_scope_is_no_key(self):
        """grok: a link is keyed by its target; one that leaves the scope points at bytes the key cannot see."""
        outside = Path(tempfile.mkdtemp(prefix="pb228x-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "real.py").write_text("q = 1\n", encoding="utf-8")
        (self.d / "far.py").symlink_to(outside / "real.py")
        self.assertIsNone(self.key())


class Round2(Project):
    """Impl round 2 (the last panel): each case but the first is a reuse on code that would fail."""

    def test_a_multi_user_lane_keeps_the_key_through_record_edits(self):
        """agy, opus, sonnet, codex: `.agent/<user>/tasks/` are task records too."""
        (self.d / ".agent" / "current_user").write_text("ana\n", encoding="utf-8")
        rec = self.d / ".agent" / "ana" / "tasks" / "001-x"
        rec.mkdir(parents=True)
        (rec / "task.md").write_text("# x\n", encoding="utf-8")
        self.g("add", "-A")
        k = self.key()
        (rec / "task.md").write_text("# x, ticked\n", encoding="utf-8")
        self.assertEqual(self.key(), k)

    def test_tasks_verify_always_runs(self):
        """opus: the agent's own verify never answers from memory — only the close reuses."""
        self.assertEqual(self.cli("verify").returncode, 0)
        r = self.cli("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), 2)
        self.assertNotIn("reused", r.stdout)

    def test_an_intent_to_add_entry_is_no_key(self):
        """codex: `git add -N` then a real add keeps the same blob — refuse while an intent-to-add entry exists."""
        (self.d / "new.py").write_text("", encoding="utf-8")
        self.g("add", "-N", "new.py")
        self.assertIsNone(self.key())

    def test_a_failed_write_does_not_let_an_older_green_back(self):
        """codex: begin(A,100), begin(B,200), B red with the write failing, then A green — no green record."""
        k = self.key()
        verify_memory.begin(self.d, k, A, 100.0)
        verify_memory.begin(self.d, k, A, 200.0)
        with mock.patch("tasks.atomic.atomic_write", side_effect=OSError("disk full")):
            verify_memory.record(self.d, k, k, A, False, started=200.0, seconds=1.0)
        self.assertEqual(verify_memory.record(self.d, k, k, A, True, started=100.0, seconds=1.0), "kept")
        self.assertIsNone(verify_memory.reusable(self.d, k, A, timeout=None))

    def test_a_commit_in_a_code_root_moves_the_key(self):
        """grok: the nested repo's HEAD (what the outer gitlink points at) is part of the key."""
        sub = self.d / "nested"
        sub.mkdir()
        self.g("init", "-q", cwd=sub)
        (sub / "lib.py").write_text("z = 1\n", encoding="utf-8")
        self.g("add", "-A", cwd=sub)
        self.g("commit", "-qm", "n", cwd=sub)
        self.contract(A, extra={"code_roots": ["nested"]})
        self.g("add", "nested")
        k = self.key()
        self.assertTrue(k)
        self.g("commit", "-q", "--allow-empty", "-m", "empty", cwd=sub)
        self.assertNotEqual(self.key(), k)
        first = self.g("rev-list", "--max-parents=0", "HEAD", cwd=sub).stdout.strip()
        self.g("add", "nested")
        k2 = self.key()
        self.g("update-index", "--cacheinfo", f"160000,{first},nested")   # the outer gitlink moved, the repo did not
        self.assertNotEqual(self.key(), k2)

    def test_a_link_is_keyed_by_what_it_points_at(self):
        """grok: a link into an ignored or skipped file — its target's bytes are in the key."""
        (self.d / ".gitignore").write_text((self.d / ".gitignore").read_text() + "secret.py\n", encoding="utf-8")
        (self.d / "secret.py").write_text("s = 1\n", encoding="utf-8")
        (self.d / "link.py").symlink_to("secret.py")
        k = self.key()
        self.assertTrue(k)
        (self.d / "secret.py").write_text("s = 2\n", encoding="utf-8")
        self.assertNotEqual(self.key(), k)
        (self.d / "dirlink").symlink_to(".agent")
        self.assertIsNone(self.key())                                     # a link to a directory: no key


class PostPanelRun1(Project):
    """The post-panel judge's run 1."""

    def test_an_intent_to_add_entry_removed_from_the_worktree(self):
        """codex: the flag itself, not the porcelain status (` D` once the file is removed)."""
        (self.d / "new.py").write_text("", encoding="utf-8")
        self.g("add", "-N", "new.py")
        (self.d / "new.py").unlink()
        self.assertIsNone(self.key())

    def test_a_link_targets_mode_counts(self):
        """codex: a link to a script — its target losing the execute bit moves the key."""
        runner = self.d / ".agent" / "runner"
        runner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        runner.chmod(0o755)
        (self.d / "runcheck").symlink_to(".agent/runner")
        k = self.key()
        self.assertTrue(k)
        runner.chmod(0o644)
        self.assertNotEqual(self.key(), k)

    def test_a_records_only_commit_keeps_the_key(self):
        """codex: the outer HEAD is not code — committing task records after a green run keeps the key."""
        rec = self.d / ".agent" / "tasks" / "001-x"
        rec.mkdir(parents=True)
        (rec / "task.md").write_text("# x\n", encoding="utf-8")
        k = self.key()
        self.g("add", "-A")
        self.g("commit", "-qm", "the task record")
        self.assertEqual(self.key(), k)


class TheRecord(Project):

    def test_record_reuse_and_the_conditions(self):
        k = self.key()
        self.assertEqual(self.green(k, A, 100.0, 5.0), "recorded")
        self.assertIsNotNone(verify_memory.reusable(self.d, k, A, timeout=1200))
        self.assertIsNone(verify_memory.reusable(self.d, k, A, timeout=3))            # the run would time out now
        self.assertIsNone(verify_memory.reusable(self.d, k, B, timeout=1200))         # another command
        self.assertIsNone(verify_memory.reusable(self.d, "0" * 64, A, timeout=1200))  # other code
        with mock.patch("tasks.bash_resolver.usable_bash", return_value=(None, "gone")):
            self.assertIsNone(verify_memory.reusable(self.d, k, A, timeout=1200))     # no bash: run it (126)

    def test_only_a_later_run_replaces_or_removes(self):
        k = self.key()
        self.green(k, A, 200.0, 1.0)
        self.assertEqual(verify_memory.record(self.d, k, k, A, False, started=100.0, seconds=1.0), "kept")
        self.assertIsNotNone(verify_memory.reusable(self.d, k, A, timeout=1200))
        self.assertEqual(verify_memory.record(self.d, k, k, A, False, started=300.0, seconds=1.0), "marked")
        self.assertIsNone(verify_memory.reusable(self.d, k, A, timeout=1200))
        self.green(k, A, 500.0, 1.0)
        other = "3" * 64
        self.assertEqual(verify_memory.record(self.d, other, other, A, True, started=400.0, seconds=1.0), "kept")
        self.assertIsNotNone(verify_memory.reusable(self.d, k, A, timeout=1200))      # the newer run stays

    def test_the_newest_run_of_a_command_has_the_last_word(self):
        """One record per command — its newest run: a later red run on other code ends the green too (the command
        then runs again: conservative); a later green run on other code replaces it."""
        k = self.key()
        self.green(k, A, 100.0, 1.0)
        verify_memory.record(self.d, "1" * 64, "1" * 64, A, False, started=200.0, seconds=1.0)
        self.assertIsNone(verify_memory.reusable(self.d, k, A, timeout=1200))
        self.green(k, B, 300.0, 1.0)
        self.assertIsNotNone(verify_memory.reusable(self.d, k, B, timeout=1200))      # another command untouched

    def test_moved_code_or_no_key_is_not_recorded(self):
        k = self.key()
        self.assertEqual(verify_memory.record(self.d, k, "2" * 64, A, True, started=1.0, seconds=1.0), "skipped")
        self.assertEqual(verify_memory.record(self.d, None, None, A, True, started=1.0, seconds=1.0), "skipped")
        self.assertEqual(verify_memory.record(self.d, k, k, "", True, started=1.0, seconds=1.0), "skipped")

    def test_a_corrupt_record_is_none(self):
        j = self.d / ".agent" / "journal"
        j.mkdir(parents=True, exist_ok=True)
        (j / "verify-green.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(verify_memory.reusable(self.d, self.key(), A, timeout=1200))
        k = self.key()
        self.assertEqual(self.green(k, A, 1.0, 1.0), "recorded")


class TheCommand(Project):

    def test_tasks_verify_records_green_not_red(self):
        self.red()
        r = self.cli("verify")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIsNone(self.recorded())
        self.red(False)
        r = self.cli("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), 2)
        self.assertIsNotNone(self.recorded())
        self.assertEqual(self.runs(), 2)

    def test_code_that_moves_while_it_runs_is_not_remembered(self):
        self.contract(A.replace("sys.exit(", "pathlib.Path('src.py').write_text('x = 9\\\\n'); sys.exit("))
        r = self.cli("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not remembered", r.stdout + r.stderr)
        self.assertIsNone(verify_memory.reusable(self.d, self.key(), self.contract_cmd(), timeout=1200))

    def contract_cmd(self):
        return json.loads((self.d / ".agent" / "config.json").read_text())["verify"]

    def test_a_task_number_and_wrong_calls(self):
        """agy, codex: `tasks verify 1` finds `001-*`; a wrong call or an unknown task is exit 2."""
        r = self.cli("new", "light", "probe", "probe")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.cli("verify", "1").returncode, 0)
        self.assertEqual(self.cli("verify", "001").returncode, 0)
        self.assertEqual(self.cli("verify", "999").returncode, 2)
        self.assertEqual(self.cli("verify", "bad").returncode, 2)
        self.assertEqual(self.cli("verify", "1", "2").returncode, 2)

    def test_no_key_says_so(self):
        """opus: a run with no code key is not remembered, and says why."""
        self.g("update-index", "--skip-worktree", "src.py")
        r = self.cli("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not remembered: no code key", r.stdout)

    def test_no_contract_records_nothing(self):
        (self.d / ".agent" / "config.json").write_text("{}", encoding="utf-8")
        r = self.cli("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.d / ".agent" / "journal" / "verify-green.json").exists())


class TheClose(Project):

    def task(self, risk="reversible"):
        r = self.cli("new", "light", "probe", "probe the reuse")
        self.assertEqual(r.returncode, 0, r.stderr)
        tf = next((self.d / ".agent" / "tasks").glob("001-*/task.md"))
        text = tf.read_text(encoding="utf-8").replace("## Risk\nunclassified\n", f"## Risk\n{risk}\n")
        text = text.replace("- [ ] ", "- [x] ") .replace("\n- [x] ", "\n- [x] ")
        text = "\n".join(ln + (" — done" if ln.startswith("- [x] ") else "") for ln in text.split("\n"))
        tf.write_text(text, encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "the task")
        r = self.cli("work", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return tf

    def test_the_close_reuses_a_green_run_of_the_same_code(self):
        """Q1."""
        tf = self.task()
        self.assertEqual(self.cli("verify").returncode, 0)
        self.assertEqual(self.runs(), 1)
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), 1)                                   # not run again
        self.assertIn("reused", r.stdout + r.stderr)
        text = tf.read_text(encoding="utf-8")
        self.assertRegex(text, r"\[PASS\] `[^`]+` \([^)]+\) — reused: the green run of .+ on the same code \(key [0-9a-f]{12}\)")
        from tasks import audit
        sweep = audit.check_verify_contract_change(self.d)
        self.assertNotEqual((sweep or {}).get("status"), "findings", sweep)

    def test_the_close_runs_after_a_code_edit(self):
        """Q2."""
        self.task()
        self.assertEqual(self.cli("verify").returncode, 0)
        (self.d / "src.py").write_text("x = 2\n", encoding="utf-8")
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), 2)

    def test_a_red_close_clears_the_record_before_it_exits(self):
        """A green record the close may not reuse (its 50 s are over a 10 s timeout) — the command runs, red, and its
        result is recorded before the close exits: the record is gone."""
        self.task()
        k = self.key()
        self.green(k, A, 1.0, 50.0)
        self.red()
        env = dict(self.env(), PLAYBOOK_VERIFY_TIMEOUT_SECS="10")
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "work", "done"], cwd=self.d, env=env,
                           capture_output=True, text=True, timeout=300)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), 1)
        self.assertIsNone(verify_memory.reusable(self.d, self.key(), A, timeout=None))

    def test_a_close_blocked_after_a_green_verify_is_reused_next_time(self):
        tf = self.task(risk="unclassified")                                 # blocked after the verify: no review
        r = self.cli("work", "done")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), 1)
        tf.write_text(tf.read_text(encoding="utf-8").replace("## Risk\nunclassified\n", "## Risk\nreversible\n"),
                      encoding="utf-8")
        r = self.cli("work", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), 1)

    def test_two_commands_one_reused_one_run(self):
        """The reversible bar runs A; the task turns assertive (a task record — outside the key): its close reuses A
        and runs B."""
        (self.d / ".agent" / "config.json").write_text(json.dumps({"verify": {"_always": [A], "assertive": [B]}}),
                                                       encoding="utf-8")
        self.g("add", "-A")
        self.g("commit", "-qm", "two risk classes")
        tf = self.task()
        self.assertEqual(self.cli("verify").returncode, 0)
        self.assertEqual((self.runs("a"), self.runs("b")), (1, 0))
        tf.write_text(tf.read_text(encoding="utf-8").replace("## Risk\nreversible\n", "## Risk\nassertive\n"),
                      encoding="utf-8")
        self.cli("work", "done")                                   # blocked later (no review) — the verify ran first
        self.assertEqual((self.runs("a"), self.runs("b")), (1, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
