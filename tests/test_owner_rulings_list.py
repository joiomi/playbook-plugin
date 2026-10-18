#!/usr/bin/env python3
"""The project's owner rulings list: written once by the CLI, never edited, given to every judge (task 227).

The owner, 2026-10-16 09:48 (task 218): "Listă de proiect cu durată (Recomandat)" — a ruling for a batch or the whole
project is written once into a list the plugin keeps, with its number, the date, his exact words and how long it holds
(`permanent`, or until a batch ends); an ended ruling gets a closing note; every judge receives the rulings in force
with the task's own. The list is `<project>/.agent/rulings.md`, one per project whatever the lane.

Pure stdlib unittest. Run: python3 tests/test_owner_rulings_list.py
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import post_d6  # noqa: E402

WORDS1 = '"Listă de proiect cu durată (Recomandat)"'
WORDS2 = '"Da, și în Faza B (Recomandat)"'
TASK = ("# 001 - Probe\n\n## Status\nin_progress\n\n## Owner rulings\n<!-- pin -->\n"
        "- 2026-10-17 07:00: \"only this task\"\n")


def _gitignore_entries():
    spec = importlib.util.spec_from_file_location("cmm_227", _PLAYBOOK / "scripts" / "claude-md-merge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GITIGNORE_ENTRIES


class Project(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb227-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        subprocess.run(["git", "init", "-q"], cwd=self.d, check=True)
        (self.d / ".agent" / "tasks" / "001-probe").mkdir(parents=True)
        (self.d / "home").mkdir()
        (self.d / ".gitignore").write_text("home/\n" + "\n".join(_gitignore_entries()) + "\n", encoding="utf-8")
        self.task = self.d / ".agent" / "tasks" / "001-probe" / "task.md"
        self.task.write_text(TASK, encoding="utf-8")
        self.list = self.d / ".agent" / "rulings.md"

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update({"PYTHONPATH": str(_PLAYBOOK), "HOME": str(self.d / "home"),
                    "PLAYBOOK_SESSION_ID": "pid-rulings-probe"})
        return env

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=self.d, env=self.env(),
                              capture_output=True, text=True, timeout=120)

    def add(self, words, *opts):
        r = self.cli("ruling", "add", *(opts or ("--permanent",)), words)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def git(self, *args, check=True):
        return subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=p", *args], cwd=self.d,
                              capture_output=True, text=True, check=check)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-qm", msg)


class AddEndList(Project):

    def test_add_twice_end_one_list_shows_the_other(self):
        """Q1."""
        self.assertIn("R1 added", self.add(WORDS1, "--permanent", "--date", "2026-10-16 09:48").stdout)
        self.assertIn("R2 added", self.add(WORDS2, "--until", "Phase B ends").stdout)
        r = self.cli("ruling", "end", "R1", "replaced by R2")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.cli("ruling", "list").stdout
        self.assertIn(f"R2 (", out)
        self.assertIn("until Phase B ends", out)
        self.assertIn(WORDS2, out)
        self.assertNotIn(WORDS1, out)
        every = self.cli("ruling", "list", "--all").stdout
        self.assertIn(WORDS1, every)
        self.assertIn("replaced by R2", every)

    def test_the_file_is_the_documented_format(self):
        self.add(WORDS1, "--permanent", "--date", "2026-10-16 09:48", "--about", "one list")
        text = self.list.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Owner rulings\n> "), text[:80])
        self.assertTrue(text.endswith(
            f"\n\n## R1\n- Date: 2026-10-16 09:48\n- Words: {WORDS1}\n- Holds: permanent\n- About: one list\n"), text)

    def test_the_words_are_kept_verbatim(self):
        odd = 'use "strict" mode — „da" , not \'no\''
        self.add(odd)
        self.assertIn(f"- Words: {odd}\n", self.list.read_text(encoding="utf-8"))
        self.assertIn(odd, self.cli("ruling", "list").stdout)

    def test_every_write_appends_the_old_text_stays_its_start(self):
        self.add(WORDS1)
        before = self.list.read_bytes()
        self.add(WORDS2, "--until", "batch 1.6.4")
        mid = self.list.read_bytes()
        self.assertEqual(self.cli("ruling", "end", "2", "batch 1.6.4 released").returncode, 0)
        after = self.list.read_bytes()
        self.assertTrue(mid.startswith(before) and after.startswith(mid))
        self.assertIn(b"\n\n## R2 ended\n- Date: ", after)

    def test_refusals_leave_the_file_unchanged(self):
        """Q5."""
        self.add(WORDS1)
        before = self.list.read_bytes()
        for args, code in ((("add", WORDS2), 2),                                     # no duration
                           (("add", "--permanent", "--until", "x", WORDS2), 2),      # two
                           (("add", "--permanent", "--date", "yesterday", WORDS2), 1),
                           (("add", "--permanent", "a\nb"), 1),
                           (("add", "--permanent", "a b"), 1),
                           (("add", "--permanent", "a\rb"), 1),
                           (("add", "--permanent", "   "), 1),
                           (("add", "--permanent", "x" * 1501), 1),
                           (("add", "--permanent", WORDS1), 1),                      # already in force
                           (("add", "--permanent", "two", "words"), 2),
                           (("add", "--until", "  ", WORDS2), 1),
                           (("end", "7", "no such"), 1),
                           (("end", "1"), 2),
                           (("frobnicate",), 2),
                           ((), 2)):
            r = self.cli("ruling", *args)
            with self.subTest(args=args):
                self.assertEqual(r.returncode, code, r.stdout + r.stderr)
                self.assertEqual(self.list.read_bytes(), before)
        self.assertEqual(self.cli("ruling", "end", "1", "done").returncode, 0)
        after = self.list.read_bytes()
        r = self.cli("ruling", "end", "1", "again")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.list.read_bytes(), after)
        self.add(WORDS1)                         # ended, so the same words may be ruled again
        self.assertIn("## R2\n", self.list.read_text(encoding="utf-8"))

    def test_round1_refusals(self):
        """Impl round 1: an entry line over the cap (opus, grok — `About` was uncapped), separators the block's cut
        would split (codex), a lone `--` ends the options (sonnet)."""
        self.add(WORDS1)
        before = self.list.read_bytes()
        for args in (("add", "--permanent", "--about", "a" * 2500, WORDS2),
                     ("add", "--until", "u" * 2500, WORDS2),
                     ("add", "--permanent", "a\x1cb"), ("add", "--permanent", "a\x1db"), ("add", "--permanent", "a\x1eb")):
            r = self.cli("ruling", *args)
            with self.subTest(args=[a[:20] for a in args]):
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertEqual(self.list.read_bytes(), before)
        r = self.cli("ruling", "add", "--permanent", "--", "--strange words")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- Words: --strange words\n", self.list.read_text(encoding="utf-8"))

    def test_a_list_with_empty_words_or_a_link_is_refused(self):
        """Impl round 1 (agy, opus): `parse` refuses blank words or notes; a linked list is never read (its target
        would reach outside judges)."""
        self.add(WORDS1)
        text = self.list.read_text(encoding="utf-8")
        self.list.write_text(text.replace(f"- Words: {WORDS1}", "- Words:  "), encoding="utf-8")
        self.assertEqual(self.cli("ruling", "list").returncode, 1)
        secret = self.d / "elsewhere.md"                  # a valid list: only the link refusal keeps it out
        secret.write_text(text.replace(WORDS1, '"SECRET-TOKEN"'), encoding="utf-8")
        self.list.unlink()
        self.list.symlink_to(secret)
        r = self.cli("ruling", "list")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("SECRET-TOKEN", post_d6.settled_block(self.task))

    @unittest.skipIf(os.geteuid() == 0, "root writes anywhere")
    def test_a_write_that_fails_is_a_refusal_not_a_traceback(self):
        """Impl round 1 (sonnet): an OSError from the write is the usual `Error: … unchanged`, exit 1."""
        self.add(WORDS1)
        agent = self.d / ".agent"
        agent.chmod(0o555)
        self.addCleanup(agent.chmod, 0o755)
        r = self.cli("ruling", "add", "--permanent", WORDS2)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("unchanged", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_r10_is_not_r1(self):
        for i in range(1, 11):
            self.add(f'"rule {i}"')
        self.assertEqual(self.cli("ruling", "end", "10", "over").returncode, 0)
        out = self.cli("ruling", "list").stdout
        self.assertIn('"rule 1"', out)
        self.assertNotIn('"rule 10"', out)

    def test_a_hand_edited_list_is_refused_not_guessed(self):
        self.add(WORDS1)
        self.list.write_text(self.list.read_text(encoding="utf-8").replace("- Holds: permanent\n", ""), encoding="utf-8")
        before = self.list.read_bytes()
        r = self.cli("ruling", "add", "--permanent", WORDS2)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unchanged", r.stderr)
        self.assertEqual(self.list.read_bytes(), before)

    def test_two_adds_at_once_keep_both(self):
        """Q4: the read-number-append is one transaction under the list's lock."""
        procs = [subprocess.Popen([sys.executable, "-m", "tasks.cli", "ruling", "add", "--permanent", f'"race {i}"'],
                                  cwd=self.d, env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for i in range(6)]
        for p in procs:
            _out, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
        text = self.list.read_text(encoding="utf-8")
        for i in range(6):
            self.assertIn(f'"race {i}"', text)
        for n in range(1, 7):
            self.assertEqual(text.count(f"\n## R{n}\n"), 1)

    def test_git_status_is_clean_after_an_add(self):
        """P1: the lock is a named, ignored file."""
        self.commit("start")
        self.add(WORDS1)
        st = self.git("status", "--porcelain", "--untracked-files=all").stdout.splitlines()
        self.assertEqual(st, ["?? .agent/rulings.md"])

    def test_one_list_per_project_whatever_the_lane(self):
        """Q6 / P2: a multi-user repo keeps one list, and both users' judges receive it."""
        (self.d / ".agent" / "current_user").write_text("ana\n", encoding="utf-8")
        ana = self.d / ".agent" / "ana" / "tasks" / "001-a"
        bob = self.d / ".agent" / "bob" / "tasks" / "001-b"
        for t in (ana, bob):
            t.mkdir(parents=True)
            (t / "task.md").write_text(TASK, encoding="utf-8")
        self.add(WORDS1)
        self.assertTrue(self.list.exists())
        self.assertFalse((self.d / ".agent" / "ana" / "rulings.md").exists())
        for t in (ana, bob):
            self.assertIn(WORDS1, post_d6.settled_block(t / "task.md"))


class EveryJudgeGetsTheRulingsInForce(Project):

    def test_the_settled_block_lists_them_first_an_ended_one_never(self):
        """Q2."""
        self.add(WORDS1)
        self.add(WORDS2, "--until", "Phase B ends")
        self.cli("ruling", "end", "1", "replaced")
        block = post_d6.settled_block(self.task)
        self.assertIn(WORDS2, block)
        self.assertNotIn(WORDS1, block)
        self.assertLess(block.index(WORDS2), block.index("only this task"))

    def test_emitted_when_only_the_list_has_something(self):
        """P4: a ruling written once and not copied into the task still reaches the judge."""
        self.task.write_text("# 001 - Probe\n\n## Status\nin_progress\n", encoding="utf-8")
        self.assertEqual(post_d6.settled_block(self.task), "")
        self.add(WORDS1)
        self.assertIn(WORDS1, post_d6.settled_block(self.task))

    def test_a_taskless_review_gets_them(self):
        """P3."""
        self.add(WORDS1)
        self.assertIn(WORDS1, post_d6.settled_block(None, project_path=self.d))

    def test_the_budgets_keep_whole_entries_and_the_task_part(self):
        """P5: the project part within 4,000 characters, whole entries, the rest named; the task's rulings kept."""
        for i in range(40):
            self.add(f'"ruling number {i:02d} ' + "w" * 180 + '"')
        block = post_d6.settled_block(self.task)
        self.assertLessEqual(len(block), post_d6.SETTLED_CAP + 200)
        self.assertIn("only this task", block)
        shown = [i for i in range(40) if f"ruling number {i:02d} " in block]
        self.assertEqual(shown, list(range(len(shown))))                  # in order, a prefix of the list
        self.assertLess(len(shown), 40)
        self.assertIn(f"{40 - len(shown)} more in force", block)
        for ln in block.splitlines():
            if "ruling number" in ln:
                self.assertTrue(ln.rstrip().endswith('w"'), ln[-40:])     # never a cut entry

    def test_the_cut_splits_on_lf_only(self):
        """Impl round 1 (codex): a task ruling holding U+001C stays one line through the 8,000 cut."""
        odd = "- 2026-10-17: \"keep\x1cthis together\"\n"
        self.task.write_text(TASK + odd + "".join(f"- 2026-10-17: \"filler {i} " + "f" * 150 + "\"\n"
                                                    for i in range(80)), encoding="utf-8")
        block = post_d6.settled_block(self.task)
        self.assertIn("keep\x1cthis together", block)

    def test_the_final_cut_is_on_a_line_and_named(self):
        self.add(WORDS1)
        rejects = "".join(f"- **X{i}** — REJECT: reason {i} " + "r" * 150 + f" end{i}\n" for i in range(80))
        (self.task.parent / "judge.md").write_text("## Triage\n" + rejects, encoding="utf-8")
        block = post_d6.settled_block(self.task)
        self.assertLessEqual(len(block), post_d6.SETTLED_CAP + 200)
        self.assertIn(WORDS1, block)
        self.assertIn("only this task", block)
        self.assertRegex(block, r"\[\.\.\. \d+ more line\(s\) left out at the 8,000-character cap")
        for ln in block.splitlines():
            if ln.startswith("- **X"):
                self.assertRegex(ln, r" end\d+$")                           # no line cut mid-way

    def test_an_unreadable_list_arrives_as_its_raw_text(self):
        """P9: its words still reach the judges, named as unreadable."""
        self.list.write_text("# not the header\n- Words: \"keep me\"\n", encoding="utf-8")
        block = post_d6.settled_block(self.task)
        self.assertIn("cannot be read", block)
        self.assertIn('"keep me"', block)
        self.assertIn("only this task", block)

    def test_no_list_changes_nothing(self):
        self.assertNotIn("Project rulings", post_d6.settled_block(self.task))

    def test_bootstrap_prints_them_and_survives_a_bad_list(self):
        self.add(WORDS1, "--until", "batch 1.6.4")
        out = self.cli("bootstrap").stdout
        self.assertIn("OWNER RULINGS IN FORCE", out)
        self.assertIn(WORDS1, out)
        self.assertIn("once its Holds is true", out)
        self.list.write_text("garbage\n", encoding="utf-8")
        r = self.cli("bootstrap")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rulings.md cannot be read", r.stdout + r.stderr)


class TheAuditWithoutGit(Project):
    """Task 239: on a host with no git (a bare interpreter — the live rows PB-RUNTIME-STDLIB and PB-PYTHON-FLOOR run on
    slim images that have none), the check does not raise: like the other checks it stays silent on history it cannot
    read, as in a project with no commits, and still reads the list itself."""

    def check_without_git(self):
        from tasks import audit
        empty = self.d / "no-git-bin"
        empty.mkdir()
        old = os.environ.get("PATH")
        os.environ["PATH"] = str(empty)
        try:
            return audit.check_rulings_append_only(self.d)
        finally:
            os.environ["PATH"] = old

    def test_a_list_is_read_without_git(self):
        self.add(WORDS1)
        self.commit("a ruling")
        self.assertEqual(self.check_without_git()["status"], "clean")

    def test_a_broken_list_is_still_a_finding_without_git(self):
        self.add(WORDS1)
        self.list.write_text(self.list.read_text(encoding="utf-8") + "not a ruling line\n", encoding="utf-8")
        self.assertEqual(self.check_without_git()["status"], "findings")

    def test_no_list_and_no_git_is_not_applicable(self):
        self.assertIsNone(self.check_without_git())


class TheAuditCatchesAHandEdit(Project):

    def audit(self):
        from tasks import audit
        return audit.check_rulings_append_only(self.d)

    def test_clean_after_adds(self):
        self.add(WORDS1)
        self.commit("R1")
        self.add(WORDS2)
        self.assertEqual(self.audit()["status"], "clean")

    def test_an_edit_in_the_working_tree(self):
        """Q3."""
        self.add(WORDS1)
        self.commit("R1")
        self.list.write_text(self.list.read_text(encoding="utf-8").replace("Recomandat", "Reworded"), encoding="utf-8")
        r = self.audit()
        self.assertEqual(r["status"], "findings", r)
        self.assertIn("changed or removed by hand", r["output"])

    def test_an_edit_committed_stays_found(self):
        """P7: once committed, the edit is in history — still found."""
        self.add(WORDS1)
        self.commit("R1")
        self.list.write_text(self.list.read_text(encoding="utf-8").replace("Recomandat", "Reworded"), encoding="utf-8")
        self.commit("reworded")
        self.add(WORDS2)
        self.commit("R2")
        r = self.audit()
        self.assertEqual(r["status"], "findings", r)
        self.assertIn("reworded", r["output"])            # names the commit

    def test_a_tracked_list_deleted(self):
        self.add(WORDS1)
        self.commit("R1")
        self.list.unlink()
        r = self.audit()
        self.assertEqual(r["status"], "findings", r)

    def test_staged_or_absent_or_untracked_is_clean(self):
        self.assertIsNone(self.audit())                    # no list, no history: not applicable
        self.add(WORDS1)
        self.assertEqual(self.audit()["status"], "clean")
        self.git("add", ".agent/rulings.md")
        self.assertEqual(self.audit()["status"], "clean")

    def test_a_merge_keeps_this_branchs_list_first(self):
        self.add(WORDS1)
        self.commit("R1")
        base = self.list.read_text(encoding="utf-8")
        main = self.git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.git("checkout", "-qb", "side")
        self.add('"side words"')
        self.commit("side R2")
        side_entry = self.list.read_text(encoding="utf-8")[len(base):]            # "\n## R2\n…side words…"
        self.git("checkout", "-q", main)
        self.add('"main words"')
        self.commit("main R2")
        mine = self.list.read_text(encoding="utf-8")
        merged = self.git("merge", "-q", "side", check=False)
        self.assertNotEqual(merged.returncode, 0)                                   # both appended at the end
        self.list.write_text(mine + side_entry.replace("\n## R2\n", "\n## R3\n", 1), encoding="utf-8")
        self.commit("merge side")
        self.assertEqual(len(self.git("rev-list", "--parents", "-n1", "HEAD").stdout.split()), 3)   # a merge
        self.assertEqual(self.cli("ruling", "list").returncode, 0)
        self.assertEqual(self.audit()["status"], "clean", self.audit())

    def test_a_merge_that_reorders_this_branchs_list_is_found(self):
        self.add(WORDS1)
        self.commit("R1")
        base = self.list.read_text(encoding="utf-8")
        main = self.git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.git("checkout", "-qb", "side")
        self.add('"side words"')
        self.commit("side R2")
        side = self.list.read_text(encoding="utf-8")
        self.git("checkout", "-q", main)
        self.add('"main words"')
        self.commit("main R2")
        mine_entry = self.list.read_text(encoding="utf-8")[len(base):]
        self.git("merge", "-q", "side", check=False)
        self.list.write_text(side + mine_entry.replace("\n## R2\n", "\n## R3\n", 1), encoding="utf-8")   # side first
        self.commit("merge side, renumbering ours")
        r = self.audit()
        self.assertEqual(r["status"], "findings", r)
        self.assertIn("merge side, renumbering ours", r["output"])

    def _two_sided(self):
        self.add(WORDS1)
        self.commit("R1")
        main = self.git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.git("checkout", "-qb", "side")
        self.add('"side words"')
        self.commit("side R2")
        self.git("checkout", "-q", main)
        self.add('"main words"')
        self.commit("main R2")
        self.git("merge", "-q", "side", check=False)

    def test_a_merge_that_takes_their_list_is_found(self):
        """Impl round 1 (opus, codex, grok — GRAVE): `--theirs` drops this branch's committed R2; git's history
        simplification hid that merge and every commit of this branch."""
        self._two_sided()
        self.git("checkout", "--theirs", ".agent/rulings.md")
        self.commit("merge side, taking theirs")
        r = self.audit()
        self.assertEqual(r["status"], "findings", r)
        self.assertIn("main words", r["output"])

    def test_a_merge_that_keeps_our_list_is_found(self):
        """Impl round 1 (grok): `--ours` keeps this branch's list a prefix but drops the side's committed R2."""
        self._two_sided()
        self.git("checkout", "--ours", ".agent/rulings.md")
        self.commit("merge side, keeping ours")
        r = self.audit()
        self.assertEqual(r["status"], "findings", r)
        self.assertIn("side words", r["output"])

    def test_a_project_inside_a_larger_repo(self):
        """Impl round 1 (agy, opus, codex): the list's path is the project's, not the repository top's."""
        outer = Path(tempfile.mkdtemp(prefix="pb227o-"))
        self.addCleanup(shutil.rmtree, outer, True)
        subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
        shutil.copytree(self.d / ".agent", outer / "sub" / ".agent")
        (outer / "sub" / "home").mkdir()
        from tasks import audit, rulings
        rulings.add(outer / "sub", WORDS1, holds="permanent")
        g = lambda *a: subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=p", *a], cwd=outer, check=True,
                                      capture_output=True)
        g("add", "-A")
        g("commit", "-qm", "R1")
        self.assertEqual(audit.check_rulings_append_only(outer / "sub")["status"], "clean")
        lst = outer / "sub" / ".agent" / "rulings.md"
        lst.write_text(lst.read_text(encoding="utf-8").replace("Recomandat", "Reworded"), encoding="utf-8")
        r = audit.check_rulings_append_only(outer / "sub")
        self.assertEqual(r["status"], "findings", r)
        self.assertIn("changed or removed by hand", r["output"])
        self.assertNotIn("deleted", r["output"])

    def test_git_that_cannot_read_the_history_is_a_finding(self):
        """Impl round 1 (codex): a failing `git log` is not a clean history."""
        from unittest import mock
        from tasks import audit
        self.add(WORDS1)
        self.commit("R1")
        real = subprocess.run

        def fake(cmd, *a, **k):
            if isinstance(cmd, list) and "log" in cmd and ".agent/rulings.md" in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: bad object")
            return real(cmd, *a, **k)
        with mock.patch.object(audit.subprocess, "run", side_effect=fake):
            r = audit.check_rulings_append_only(self.d)
        self.assertEqual(r["status"], "findings", r)
        self.assertIn("cannot read", r["output"])

    def test_an_unreadable_list_is_a_finding(self):
        self.list.write_text("garbage\n", encoding="utf-8")
        self.assertEqual(self.audit()["status"], "findings")


class ARulingCommitIsARecord(Project):

    def test_a_panel_stays_fresh_after_a_committed_add(self):
        """P8: a ruling commit changes no reviewed code."""
        from tasks.core import build_panel_snapshot, records_only_delta, tree_state_fingerprint
        (self.d / "code.py").write_text("x = 1\n", encoding="utf-8")
        self.commit("code")
        fp = tree_state_fingerprint(self.d)
        snap = build_panel_snapshot(self.d, fp)
        self.add(WORDS1)
        self.commit("R1")
        self.assertTrue(records_only_delta(self.d, snap, fp))
        (self.d / ".agent" / "config.json").write_text("{}\n", encoding="utf-8")   # control: policy stays STALE
        self.commit("config")
        self.assertFalse(records_only_delta(self.d, snap, fp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
