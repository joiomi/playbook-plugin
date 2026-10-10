#!/usr/bin/env python3
"""Task 108 — the post-D6 review protocol (owner decision Q-E (d), retro 107).

Retro 107 measured the single-judge series that close an assertive task after
its D6 round cap: 17 runs, 229.3 judge-minutes, 68 % of them in runs 3+. Three
causes, three parts, each pinned here:

  (a) SETTLED block — the owner's `## Owner rulings` and the REJECT lines of
      earlier triage reach the judge (judge.md never did), under a header that
      says a ruling closes a design choice, never a correctness defect. A
      finding tagged [SETTLED] does not count against PASS; one tagged
      [SETTLED-CONTRADICTED] does (task.md is agent-written, so the block must
      not be a bypass).
  (b) DELTA — every panel saves, per scope, a tree of the WORKING state
      (staged + unstaged + untracked) built in a temporary index; the post-D6
      judge reviews base → current working tree. A finding outside the delta is
      [PRE-EXISTING]: parked, not counted.
  (c) CAP — `impl-review` counts its runs after the newest impl panel; run 3
      needs a counted Critical in run 2, or `--owner-ok --reason`.

Pure stdlib unittest. Run: python3 tests/test_post_d6.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))

from tasks import post_d6  # noqa: E402


def _git(repo, *args, env=None):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True, env=env).stdout.strip()


class _TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pb108-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


# ── (a) the settled block ────────────────────────────────────────────────────

TASK_WITH_RULINGS = """# 1 - t

## Status
in-progress

## Owner rulings
<!-- pin -->
- 2026-09-29 keep the daemon-only case without an export.

- 2026-09-29 nested sessions keep the outer id (design).

## Why
```md
## Owner rulings
- a fenced example that is NOT a ruling
```
"""

JUDGE_MD = """# Panel Impl Review — t

### Run 2 triage
- **S2-1 Important — session-start drops an inherited id.** REJECT (owner rule). The owner's point 2 required it.
- **S2-2 Important — the ledger sentence is false.** ACCEPT (claim defect). Narrowed.
- the word rejected in lowercase prose is not a triage line
"""

ARCHIVE_MD = """### Round 1 triage
- **R1-3 Important — pre-existing sharing of the pid dir.** REJECT for this task, PARKED (P2).
"""


class OwnerRulings(_TmpDir):
    def test_rulings_extracted_verbatim_pin_and_blanks_dropped(self):
        got = post_d6.extract_owner_rulings(TASK_WITH_RULINGS)
        self.assertEqual(got, [
            "- 2026-09-29 keep the daemon-only case without an export.",
            "- 2026-09-29 nested sessions keep the outer id (design).",
        ])

    def test_fenced_example_is_not_a_ruling(self):
        got = post_d6.extract_owner_rulings(TASK_WITH_RULINGS)
        self.assertNotIn("a fenced example", " ".join(got))

    def test_of_two_sections_the_last_one_is_read(self):
        # the row says the LAST unfenced section (a task that restated its rulings); the
        # fixture above has one (task 172)
        two = ("# 1\n\n## Owner rulings\n- 2026-09-01 an earlier ruling, since replaced.\n\n## Why\nx\n\n"
               "## Owner rulings\n<!-- pin -->\n- 2026-09-29 the ruling that stands.\n")
        self.assertEqual(post_d6.extract_owner_rulings(two), ["- 2026-09-29 the ruling that stands."])

    def test_no_section_no_rulings(self):
        self.assertEqual(post_d6.extract_owner_rulings("# 1\n\n## Why\nx\n"), [])


class RejectedFindings(_TmpDir):
    def test_reject_lines_from_judge_md_and_archive(self):
        (self.tmp / "judge.md").write_text(JUDGE_MD, encoding="utf-8")
        (self.tmp / "judge-archive.md").write_text(ARCHIVE_MD, encoding="utf-8")
        got = post_d6.extract_rejected_findings(self.tmp)
        self.assertEqual(len(got), 2, got)
        self.assertIn("S2-1", got[0])
        self.assertIn("REJECT (owner rule)", got[0])
        self.assertIn("R1-3", got[1])

    def test_accept_lines_and_prose_are_not_extracted(self):
        (self.tmp / "judge.md").write_text(JUDGE_MD, encoding="utf-8")
        got = " ".join(post_d6.extract_rejected_findings(self.tmp))
        self.assertNotIn("S2-2", got)
        self.assertNotIn("lowercase prose", got)

    def test_no_judge_files(self):
        self.assertEqual(post_d6.extract_rejected_findings(self.tmp), [])


class SettledBlock(_TmpDir):
    def _task(self, text):
        tf = self.tmp / "task.md"
        tf.write_text(text, encoding="utf-8")
        return tf

    def test_block_carries_header_rulings_and_rejects(self):
        (self.tmp / "judge.md").write_text(JUDGE_MD, encoding="utf-8")
        block = post_d6.settled_block(self._task(TASK_WITH_RULINGS))
        self.assertTrue(block.startswith("=== SETTLED"), block[:80])
        self.assertIn(post_d6.SETTLED_HEADER, block)
        self.assertIn("never a correctness defect", block)
        self.assertIn("SETTLED-CONTRADICTED", block)
        self.assertIn("keep the daemon-only case", block)
        self.assertIn("S2-1", block)

    def test_empty_when_nothing_is_settled(self):
        self.assertEqual(post_d6.settled_block(self._task("# 1\n\n## Why\nx\n")), "")

    def test_block_is_bounded(self):
        many = "# 1\n\n## Owner rulings\n" + "".join(
            f"- 2026-09-29 ruling {i} " + "x" * 200 + "\n" for i in range(200))
        block = post_d6.settled_block(self._task(many), cap=4000)
        self.assertLessEqual(len(block), 4200)
        self.assertIn("truncated", block)


# ── verdict: what counts against PASS ────────────────────────────────────────

class Verdict(unittest.TestCase):
    def v(self, text):
        return post_d6.parse_verdict(text)

    def test_settled_important_does_not_break_pass(self):
        r = self.v("**Important** [SETTLED] — `a.py:3` re-raises the no-export rule.\n\nCAP: 1/5 reported, exhausted")
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(r["settled"], 1)

    def test_settled_contradicted_breaks_pass(self):
        r = self.v("**Important** [SETTLED-CONTRADICTED] — `a.py:3` the code exports although the ruling says no export.\n\nCAP: 1/5 reported, exhausted")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertEqual(r["important"], 1)

    def test_pre_existing_is_parked_not_counted(self):
        # parked only relative to a delivered delta that did not touch b.py (task 108 round 2)
        r = post_d6.parse_verdict("- **Important [PRE-EXISTING]** — `b.py:9` unchanged since the panel.\n"
                                  "CAP: 1/5 reported, exhausted", delta_files={"a.py"})
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(r["pre_existing"], 1)

    def test_untagged_critical_fails(self):
        r = self.v("1. **Critical — the guard fails open.** `c.py:1`\n2. **Important** — `d.py:2` x\nCAP: 2/5 reported, exhausted")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertEqual((r["critical"], r["important"]), (1, 1))

    def test_no_findings_passes(self):
        r = self.v("No Critical or Important findings. Task 107 changes records only.\n\nCAP: 0/5 reported, exhausted")
        self.assertEqual(r["verdict"], "PASS")

    def test_reported_findings_without_a_severity_marker_fail_closed(self):
        r = self.v("The code has a bug in e.py:4 that loses data.\n\nCAP: 1/5 reported, exhausted")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(r["unparsed"])

    def test_live_log_shapes_from_retro_107(self):
        # shapes copied from the task 104-107 logs
        for line in ("**Important —** `x.py:1` y", "**[Important]** `x.py:1` y",
                     "**Important — The claim is false.** y", "**Critical.** `x:1` y",
                     "- **Important — baza propusă nu este validă.** y"):
            r = self.v(line + "\n\nCAP: 1/5 reported, exhausted")
            self.assertEqual(r["verdict"], "FAIL", line)


# ── (b) the delta: base tree of the working state ────────────────────────────

class WorktreeTree(_TmpDir):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "r"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / "keep.py").write_text("keep = 1\n", encoding="utf-8")
        (self.repo / "edit.py").write_text("v = 1\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "c0")
        self.exclude = [":(exclude).agent"]

    def _index_bytes(self):
        return (self.repo / ".git" / "index").read_bytes()

    def test_base_holds_unstaged_staged_and_untracked_and_leaves_real_index(self):
        (self.repo / "edit.py").write_text("v = 2\n", encoding="utf-8")        # unstaged
        (self.repo / "staged.py").write_text("s = 1\n", encoding="utf-8")
        _git(self.repo, "add", "staged.py")                                   # staged
        (self.repo / "new.py").write_text("n = 1\n", encoding="utf-8")         # untracked
        before = self._index_bytes()
        status_before = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo,
                                       capture_output=True, text=True).stdout
        tree = post_d6.worktree_tree(self.repo, self.exclude)
        self.assertRegex(tree or "", r"^[0-9a-f]{40,64}$")
        self.assertEqual(self._index_bytes(), before, "the real index was touched")
        names = _git(self.repo, "ls-tree", "-r", "--name-only", tree).splitlines()
        self.assertEqual(sorted(names), ["edit.py", "keep.py", "new.py", "staged.py"])
        self.assertEqual(_git(self.repo, "show", f"{tree}:edit.py"), "v = 2")
        status_after = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo,
                                      capture_output=True, text=True).stdout
        self.assertEqual(status_after, status_before, "the worktree/index state must be unchanged")
        self.assertIn("A  staged.py", status_after)
        self.assertIn("?? new.py", status_after)

    def test_excluded_dir_is_not_in_the_base(self):
        (self.repo / ".agent").mkdir()
        (self.repo / ".agent" / "note.md").write_text("x\n", encoding="utf-8")
        tree = post_d6.worktree_tree(self.repo, self.exclude)
        names = _git(self.repo, "ls-tree", "-r", "--name-only", tree).splitlines()
        self.assertNotIn(".agent/note.md", names)

    def test_delta_ignores_unchanged_file_and_includes_untracked(self):
        base = post_d6.worktree_tree(self.repo, self.exclude)
        (self.repo / "edit.py").write_text("v = 3\n", encoding="utf-8")
        (self.repo / "fresh.py").write_text("fresh = True\n", encoding="utf-8")
        text, note = post_d6.scope_delta(self.repo, base, self.exclude)
        self.assertIsNotNone(text, note)
        self.assertIn("edit.py", text)
        self.assertIn("+v = 3", text)
        self.assertIn("fresh.py", text)
        self.assertIn("+fresh = True", text)
        self.assertNotIn("keep.py", text)

    def test_a_racy_same_size_edit_is_in_the_delta(self):
        # CI macOS (run 36689720491): `v = 1` -> `v = 3` (same size, same timestamp at
        # the filesystem's granularity). git re-hashes such a "racily clean" entry only
        # because the INDEX file is not newer than it; a copy with a fresh mtime lost that
        # and the edit vanished from the delta. Built deterministically here:
        idx = self.repo / ".git" / "index"
        past = 1_600_000_000_000_000_000                        # ns, well in the past
        os.utime(self.repo / "edit.py", ns=(past, past))
        subprocess.run(["git", "status", "--porcelain"], cwd=self.repo, capture_output=True)  # entry now has mtime T, unsmudged
        base = post_d6.worktree_tree(self.repo, self.exclude)
        (self.repo / "edit.py").write_text("v = 3\n", encoding="utf-8")   # same size
        os.utime(self.repo / "edit.py", ns=(past, past))                   # same stat as the entry
        os.utime(idx, ns=(past, past))                                     # the real index is racy
        ro = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        self.assertIn("edit.py", subprocess.run(["git", "status", "--porcelain"], cwd=self.repo,
                                                capture_output=True, text=True, env=ro).stdout,
                      "precondition: git itself sees the racy edit")
        text, note = post_d6.scope_delta(self.repo, base, self.exclude)
        self.assertIn("+v = 3", text or "", note)

    def test_missing_base_object_is_reported_not_guessed(self):
        text, note = post_d6.scope_delta(self.repo, "0" * 40, self.exclude)
        self.assertIsNone(text)
        self.assertIn("base", note)

    def test_non_repo_gives_none(self):
        self.assertIsNone(post_d6.worktree_tree(self.tmp / "nope", self.exclude))


# ── (c) the run cap ──────────────────────────────────────────────────────────

class RunCap(_TmpDir):
    def rec(self, panel, critical=0, owner_ok=None):
        return {"panel": panel, "verdict": "FAIL" if critical else "PASS",
                "critical": critical, "important": 0, "owner_ok": owner_ok}

    def test_first_two_runs_are_allowed(self):
        self.assertTrue(post_d6.gate_run([], "fp1")[0])
        self.assertTrue(post_d6.gate_run([self.rec("fp1")], "fp1")[0])

    def test_third_run_refused_without_a_critical_in_run_two(self):
        ok, n, msg = post_d6.gate_run([self.rec("fp1"), self.rec("fp1")], "fp1")
        self.assertFalse(ok)
        self.assertEqual(n, 3)
        self.assertIn("--owner-ok", msg)

    def test_third_run_allowed_after_a_critical_in_run_two(self):
        ok, n, _ = post_d6.gate_run([self.rec("fp1"), self.rec("fp1", critical=1)], "fp1")
        self.assertTrue(ok)
        self.assertEqual(n, 3)

    def test_third_run_allowed_with_owner_ok_reason(self):
        ok, n, _ = post_d6.gate_run([self.rec("fp1"), self.rec("fp1")], "fp1",
                                    owner_ok="owner 2026-09-29: one more pass")
        self.assertTrue(ok)

    def test_runs_after_an_older_panel_do_not_count(self):
        runs = [self.rec("old"), self.rec("old"), self.rec("old")]
        self.assertEqual(post_d6.gate_run(runs, "fp2")[:2], (True, 1))

    def test_ledger_round_trip(self):
        post_d6.append_run(self.tmp, self.rec("fp1"))
        post_d6.append_run(self.tmp, self.rec("fp1", owner_ok="because"))
        runs = post_d6.read_runs(self.tmp)
        self.assertEqual([r.get("owner_ok") for r in runs], [None, "because"])
        (self.tmp / post_d6.RUNS_NAME).write_text("not json\n" + json.dumps(self.rec("fp1")) + "\n",
                                                  encoding="utf-8")
        self.assertEqual(len(post_d6.read_runs(self.tmp)), 1)

    def test_receipt_line_names_runs_and_owner_ok(self):
        runs = [self.rec("fp1"), self.rec("fp1"), dict(self.rec("fp1"), owner_ok="one more")]
        line = post_d6.receipt_line(runs, "fp1")
        self.assertIn("3 run(s)", line)
        self.assertIn('owner-ok: "one more"', line)
        self.assertEqual(post_d6.receipt_line([], "fp1"), "")

    def test_close_receipt_carries_the_post_d6_line(self):
        from tasks.core import format_verify_receipt
        line = '- **Post-D6 single judge:** 3 run(s) after impl panel fp1 — last verdict PASS; owner-ok: "x"'
        out = format_verify_receipt([], "abc", "assertive", post_d6=line)
        self.assertIn(line, out)
        self.assertNotIn("Post-D6", format_verify_receipt([], "abc", "assertive"))

    def test_close_receipt_line_reads_the_newest_impl_round(self):
        (self.tmp / "judge.md").write_text(
            "# Panel Impl Review — t\n\n**PANEL VERDICT: PASS** — 2/2\n\n**Tree-state:** aaaaaa111111\n\n"
            "# Panel Impl Review — t\n\n**PANEL VERDICT: PASS** — 2/2\n\n**Tree-state:** bbbbbb222222\n",
            encoding="utf-8")
        post_d6.append_run(self.tmp, self.rec("bbbbbb222222"))
        self.assertEqual(post_d6.close_receipt_line(self.tmp), "")
        post_d6.append_run(self.tmp, self.rec("aaaaaa111111", owner_ok="the owner said so"))
        self.assertIn('1 run(s) after impl panel aaaaaa111111', post_d6.close_receipt_line(self.tmp))
        self.assertIn('owner-ok: "the owner said so"', post_d6.close_receipt_line(self.tmp))


# ── panel round 1 (task 108) findings, red first ─────────────────────────────

_FIX = _HERE / "fixtures" / "post_d6"


class Round1Verdict(unittest.TestCase):
    def test_tag_counts_only_right_after_the_severity(self):
        # codex-high Critical: a literal "[SETTLED]" later on the line is not a tag
        r = post_d6.parse_verdict("**Critical** — `a.py:1` the parser trusts the literal [SETTLED] anywhere.\n"
                                  "CAP: 1/5 reported, exhausted")
        self.assertEqual((r["verdict"], r["critical"], r["settled"]), ("FAIL", 1, 0))

    def test_findings_without_a_cap_line_fail_closed(self):
        r = post_d6.parse_verdict("**Important** [SETTLED] — `a.py:1` x")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(r["unparsed"])

    def test_real_codex_clean_log_passes(self):
        r = post_d6.parse_verdict((_FIX / "codex-clean-107.log").read_text(encoding="utf-8"))
        self.assertEqual((r["verdict"], r["findings"]), ("PASS", 0))

    def test_real_codex_findings_log_counts_three(self):
        r = post_d6.parse_verdict((_FIX / "codex-3-important-107.log").read_text(encoding="utf-8"))
        self.assertEqual((r["verdict"], r["important"], r["unparsed"]), ("FAIL", 3, False))

    def test_real_grok_log_with_preamble_glued_to_the_first_finding(self):
        r = post_d6.parse_verdict((_FIX / "grok-3-important-106.log").read_text(encoding="utf-8"))
        self.assertEqual((r["verdict"], r["important"], r["unparsed"]), ("FAIL", 3, False))


class Round1Rejects(_TmpDir):
    def test_reject_must_be_in_the_verdict_position(self):
        (self.tmp / "judge.md").write_text(
            "- **S1 Important — x.** REJECT (owner rule).\n"
            "- **S2 Important — y.** ACCEPT; the REJECT alternative was weighed.\n"
            "- guidance: never REJECT a finding without a measured reason\n"
            "- REJECT — S3: pre-existing design.\n"
            "- **S4 Important — z.** — REJECT for 106, PARKED (P2).\n", encoding="utf-8")
        got = " | ".join(post_d6.extract_rejected_findings(self.tmp))
        for want in ("S1 ", "S3:", "S4 "):
            self.assertIn(want, got)
        for bad in ("S2 ", "guidance"):
            self.assertNotIn(bad, got)


class Round1Cap(_TmpDir):
    def rec(self, panel, critical=0, owner_ok=None):
        return {"panel": panel, "verdict": "FAIL" if critical else "PASS",
                "critical": critical, "important": 0, "owner_ok": owner_ok}

    def test_run_four_needs_the_owner_even_after_a_critical(self):
        runs = [self.rec("p"), self.rec("p", critical=1), self.rec("p", critical=1)]
        ok, n, msg = post_d6.gate_run(runs, "p")
        self.assertEqual((ok, n), (False, 4))
        self.assertTrue(post_d6.gate_run(runs, "p", owner_ok="owner: once more")[0])

    def test_round_id_separates_two_panels_on_the_same_tree(self):
        (self.tmp / "judge.md").write_text(
            "# Panel Impl Review — t\n\n**PANEL VERDICT: PASS** — 2/2\n\n**Round-id:** abcd0002\n\n"
            "**Tree-state:** aaaaaa111111\n\n"
            "# Panel Impl Review — t\n\n**PANEL VERDICT: PASS** — 2/2\n\n**Round-id:** abcd0001\n\n"
            "**Tree-state:** aaaaaa111111\n", encoding="utf-8")
        self.assertEqual(post_d6.panel_key(post_d6.newest_impl_round(self.tmp)), "abcd0002")
        legacy = {"tree_state": "aaaaaa111111", "round_id": ""}
        self.assertEqual(post_d6.panel_key(legacy), "aaaaaa111111")

    def test_concurrent_reservations_never_exceed_the_cap(self):
        # separate PROCESSES: the task lock is re-entrant within one process, so
        # threads would prove nothing (sonnet Critical / codex: the gate→append race)
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        # Each child reserves, prints, then STAYS ALIVE until the release file exists:
        # since D2-3 a reservation is live exactly while its process runs, so the
        # children must overlap. (The first version let each child exit at once; on
        # the CI py3.12 lane a child exited before the next checked, and the dead
        # owner's reservation correctly counted as spent: 2 granted — reproduced
        # locally by running them one after another.)
        release = self.tmp / "release"
        code = ("import sys, time, pathlib; sys.path.insert(0, sys.argv[1]); from tasks import post_d6; "
                "print(int(post_d6.reserve_run(sys.argv[2], 'p', None)[0]), flush=True); "
                "rel = pathlib.Path(sys.argv[3]); t0 = time.time()\n"
                "while not rel.exists() and time.time() - t0 < 120: time.sleep(0.05)")
        procs, results = [], []
        for _ in range(6):          # one after another, each still alive when the next tries
            pr = subprocess.Popen([sys.executable, "-c", code, str(_HERE.parent / "plugins/playbook"),
                                   str(tf), str(release)], stdout=subprocess.PIPE, text=True)
            procs.append(pr)
            results.append(int((pr.stdout.readline() or "0").strip() or 0))
        release.write_text("go", encoding="utf-8")
        for pr in procs:
            pr.communicate(timeout=120)
        # one review at a time per task: exactly ONE of six live processes is granted
        self.assertEqual(sum(results), 1, results)
        self.assertEqual(len(post_d6.read_runs(self.tmp)), 1)

    def test_a_failed_run_frees_its_slot_a_finished_one_keeps_it(self):
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        ok, n, _, rid = post_d6.reserve_run(tf, "p", None)
        post_d6.fail_run(self.tmp, rid)
        self.assertEqual(post_d6.read_runs(self.tmp), [])
        ok, n, _, rid = post_d6.reserve_run(tf, "p", None)
        self.assertEqual(n, 1)
        post_d6.finish_run(self.tmp, rid, {"verdict": "FAIL", "critical": 1})
        runs = post_d6.read_runs(self.tmp)
        self.assertEqual((len(runs), runs[0]["critical"], runs[0]["panel"]), (1, 1, "p"))


class Round1Delta(_TmpDir):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / "a.py").write_text("a = 1\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "c0")

    def _snap(self, base, extra_scope=None):
        from tasks.core import _scope_identity
        scopes = {"": {"commit": "x", "dirty": {}, "base": base,
                       "identity": _scope_identity(self.repo, self.repo)}}
        if extra_scope:
            scopes[extra_scope] = {"commit": "x", "dirty": {}, "base": base, "identity": "gone"}
        return {"v": 1, "tree_fp": "f", "scopes": scopes, "exclude": [":(exclude).agent"]}

    def test_a_scope_removed_after_the_panel_refuses_the_delta(self):
        base = post_d6.worktree_tree(self.repo, [":(exclude).agent"])
        text, note = post_d6.delta_text(self.repo, self._snap(base, extra_scope="lib"), 50_000)
        self.assertIsNone(text)
        self.assertIn("scope", note)

    def test_a_scope_whose_directory_is_another_one_refuses_the_delta(self):
        # the row names the scope SET and a scope's DIRECTORY; the test above removes a
        # scope — here the scope is still named and its directory is not the one the
        # panel saw (task 172)
        base = post_d6.worktree_tree(self.repo, [":(exclude).agent"])
        snap = self._snap(base)
        snap["scopes"][""]["identity"] = "the-directory-the-panel-saw"
        text, note = post_d6.delta_text(self.repo, snap, 50_000)
        self.assertIsNone(text)
        self.assertIn("scope", note)

    def test_truncated_delta_names_untracked_files_in_its_stat(self):
        base = post_d6.worktree_tree(self.repo, [":(exclude).agent"])
        (self.repo / "big.py").write_text("x = 1\n" * 4000, encoding="utf-8")
        (self.repo / "zz_small_untracked.py").write_text("y = 2\n", encoding="utf-8")
        text, note = post_d6.delta_text(self.repo, self._snap(base), 3000)
        self.assertIsNotNone(text, note)
        self.assertIn("truncated", note)
        self.assertIn("zz_small_untracked.py", text)
        self.assertIn(base, text)


# ── post-D6 single judge run 1 on task 108 (codex) findings, red first ───────

class SingleRun1(_TmpDir):
    def test_the_final_cap_line_decides_and_must_match(self):
        # an earlier quoted "CAP: 1/5" must not hide the final one (codex S1-2)
        text = ("**Important** [SETTLED] — `a.py:1` quotes a log that said CAP: 1/5 reported, exhausted\n"
                "An unmarked defect in b.py:2 loses data.\n\nCAP: 2/5 reported, exhausted\n")
        r = post_d6.parse_verdict(text)
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(r["unparsed"])

    def test_cap_count_equal_to_the_findings_parses(self):
        r = post_d6.parse_verdict("**Important** [SETTLED] — x\n\nCAP: 1/5 reported, exhausted\n")
        self.assertEqual((r["verdict"], r["unparsed"]), ("PASS", False))

    def test_a_corrupt_ledger_refuses_new_reservations(self):
        # a truncated completion line must not make run 3 look like run 2 (codex S1-3)
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        post_d6.append_run(self.tmp, {"id": "a1", "panel": "p", "status": "done", "critical": 0})
        with open(self.tmp / post_d6.RUNS_NAME, "a", encoding="utf-8") as f:
            f.write('{"id": "b2", "panel": "p", "sta\n')
        ok, n, msg, rid = post_d6.reserve_run(tf, "p", None)
        self.assertFalse(ok)
        self.assertIn("corrupt", msg)
        self.assertTrue(post_d6.reserve_run(tf, "p", "owner: repaired by hand")[0])


# ── post-D6 single judge run 2 on task 108 (codex) findings, red first ───────

class SingleRun2(_TmpDir):
    def test_the_cap_line_must_end_the_response(self):
        # a quoted "CAP: 0/5" mid-response is not the final line (codex S2-1)
        r = post_d6.parse_verdict("I was reviewing and quoted `CAP: 0/5 reported, exhausted` from the prompt,\n"
                                  "then the output stopped here without a verdict")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(r["unparsed"])

    def test_reject_inside_the_bold_title_is_a_verdict(self):
        # the real shape of task 051's judge.md (codex S2-4)
        (self.tmp / "judge.md").write_text(
            "- **sonnet#1 [Important] \"round 2 has not happened yet\" — REJECT as moot:** this IS round 2.\n"
            "- **grok#1 [Important] test proves presence — ACCEPT.** Added a test; the REJECT path was not taken.\n",
            encoding="utf-8")
        got = post_d6.extract_rejected_findings(self.tmp)
        self.assertEqual(len(got), 1, got)
        self.assertIn("sonnet#1", got[0])

    def test_a_live_reservation_blocks_a_second_run_without_writing(self):
        # a second reservation's append would void the first run by tamper (codex S2-3)
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        self.assertTrue(post_d6.reserve_run(tf, "p", None, stale_after=3600)[0])
        before = (self.tmp / post_d6.RUNS_NAME).read_bytes()
        ok, n, msg, rid = post_d6.reserve_run(tf, "p", None, stale_after=3600)
        self.assertFalse(ok)
        self.assertIn("in progress", msg)
        self.assertEqual((self.tmp / post_d6.RUNS_NAME).read_bytes(), before, "the refusal wrote")

    def test_a_stale_reservation_counts_as_spent_and_does_not_block(self):
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        post_d6.append_run(self.tmp, {"id": "old1", "panel": "p", "status": "reserved",
                                      "ts": "2020-01-01T00:00:00+00:00"})
        ok, n, _, _ = post_d6.reserve_run(tf, "p", None, stale_after=3600)
        self.assertEqual((ok, n), (True, 2))

    def test_the_stat_is_bounded_by_the_cap_too(self):
        repo = self.tmp / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "c0")
        from tasks.core import _scope_identity
        base = post_d6.worktree_tree(repo, [":(exclude).agent"])
        for i in range(600):
            (repo / f"untracked_file_with_a_long_name_{i:04d}.py").write_text(f"x = {i}\n", encoding="utf-8")
        snap = {"scopes": {"": {"base": base, "identity": _scope_identity(repo, repo)}},
                "exclude": [":(exclude).agent"]}
        cap = 8000
        text, note = post_d6.delta_text(repo, snap, cap)
        self.assertIsNotNone(text, note)
        self.assertLessEqual(len(text), cap + 200, len(text))
        # … and the stat's LISTING is cut at a third of the cap (task 172: the total above
        # holds for other shares too); one line saying so follows the cut, so the stat
        # part as a whole is a third plus that line (impl panel r1, codex-medium)
        marker = "[... stat truncated"
        self.assertIn(marker, text)
        after_notice = text.split("...]\n", 1)[1]
        listing = after_notice.split(marker, 1)[0]
        self.assertLessEqual(len(listing), cap // 3 + 1)
        self.assertGreater(len(listing), cap // 4)
        said = marker + after_notice.split(marker, 1)[1].split("\n", 1)[0]
        self.assertLess(len(said), 80)


# ── impl panel round 2 on task 108 findings, red first ───────────────────────

class Round2(_TmpDir):
    def test_pre_existing_counts_when_no_delta_was_delivered(self):
        text = "**Critical** [PRE-EXISTING] — `a.py:1` loses data.\n\nCAP: 1/5 reported, exhausted"
        self.assertEqual(post_d6.parse_verdict(text, delta_files=None)["verdict"], "FAIL")
        self.assertEqual(post_d6.parse_verdict(text, delta_files={"b.py"})["verdict"], "PASS")

    def test_pre_existing_counts_when_it_cites_a_file_the_delta_changed(self):
        text = "**Important** [PRE-EXISTING] — `pkg/a.py:12` x\n\nCAP: 1/5 reported, exhausted"
        self.assertEqual(post_d6.parse_verdict(text, delta_files={"pkg/a.py"})["verdict"], "FAIL")

    def test_the_last_line_must_be_a_whole_cap_line(self):
        for tail in ("The example said `CAP: 0/5 reported, exhausted`.",
                     "No findings. The prompt said CAP: 0/5 reported, exhausted as its example"):
            r = post_d6.parse_verdict("No Critical or Important findings.\n" + tail)
            self.assertEqual(r["verdict"], "FAIL", tail)

    def test_more_remain_is_incomplete(self):
        text = "".join(f"**Important** [SETTLED] — `a.py:{i}` x\n" for i in range(5))
        r = post_d6.parse_verdict(text + "\nCAP: 5/5 reported, more remain")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(r["incomplete"])

    def test_delta_files_are_listed_from_the_diff(self):
        diff = ("### scope . — git -C . diff a b\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
                "diff --git a/dir/new.py b/dir/new.py\nnew file mode 100644\n")
        self.assertEqual(post_d6.delta_files(diff), {"x.py", "dir/new.py"})

    def test_a_live_panel_reservation_blocks_a_single_judge_and_vice_versa(self):
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        ok, rid = post_d6.reserve_panel(tf, stale_after=3600)
        self.assertTrue(ok)
        ok2, n, msg, _ = post_d6.reserve_run(tf, "p", None, stale_after=3600)
        self.assertFalse(ok2)
        self.assertIn("in progress", msg)
        post_d6.finish_panel(self.tmp, rid)
        self.assertTrue(post_d6.reserve_run(tf, "p", None, stale_after=3600)[0])
        self.assertFalse(post_d6.reserve_panel(tf, stale_after=3600)[0])
        self.assertEqual([r for r in post_d6.read_runs(self.tmp) if r.get("panel") == "p"].__len__(), 1,
                         "a panel record was counted as a single-judge run")

    def test_the_refusal_says_how_to_clear_a_dead_reservation(self):
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        post_d6.reserve_run(tf, "p", None, stale_after=3600)
        msg = post_d6.reserve_run(tf, "p", None, stale_after=3600)[2]
        self.assertIn('"status": "failed"', msg)

    def test_a_conflicted_index_names_the_cause(self):
        repo = self.tmp / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "f.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "c0")
        first = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        _git(repo, "checkout", "-q", "-b", "other")
        (repo / "f.txt").write_text("other\n", encoding="utf-8")
        _git(repo, "commit", "-q", "-am", "o")
        _git(repo, "checkout", "-q", first)
        (repo / "f.txt").write_text("main\n", encoding="utf-8")
        _git(repo, "commit", "-q", "-am", "m")
        subprocess.run(["git", "merge", "-q", "other"], cwd=repo, capture_output=True)
        tree, why = post_d6.worktree_tree_why(repo, [":(exclude).agent"])
        self.assertIsNone(tree)
        self.assertIn("unmerged", why)


# ── post-D6 judge run 1 after round 2 (codex, real delta) findings, red first ─

class DeltaRun1(_TmpDir):
    def test_a_citation_on_the_findings_next_line_counts(self):
        text = ("**Important** [PRE-EXISTING] — the parser drops a case.\n"
                "See `pkg/a.py:12` for the loop.\n\nCAP: 1/5 reported, exhausted")
        self.assertEqual(post_d6.parse_verdict(text, delta_files={"pkg/a.py"})["verdict"], "FAIL")

    def test_a_backticked_cap_line_is_an_example_not_a_verdict(self):
        r = post_d6.parse_verdict("No Critical or Important findings.\n`CAP: 0/5 reported, exhausted`")
        self.assertEqual(r["verdict"], "FAIL")

    def test_a_reservation_keeps_its_own_expiry(self):
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        self.assertTrue(post_d6.reserve_panel(tf, stale_after=3600)[0])
        # a later caller with a 10-second horizon must not treat the 1-hour panel as stale
        ok, n, msg, _ = post_d6.reserve_run(tf, "p", None, stale_after=0)
        self.assertFalse(ok)
        self.assertIn("in progress", msg)

    def test_delta_files_come_from_the_full_diff_even_when_truncated(self):
        repo = self.tmp / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "c0")
        from tasks.core import _scope_identity
        base = post_d6.worktree_tree(repo, [":(exclude).agent"])
        (repo / "aa_big.py").write_text("x = 1\n" * 3000, encoding="utf-8")
        (repo / "zz_late.py").write_text("late = 1\n", encoding="utf-8")
        snap = {"scopes": {"": {"base": base, "identity": _scope_identity(repo, repo)}},
                "exclude": [":(exclude).agent"]}
        files = set()
        text, note = post_d6.delta_text(repo, snap, 3000, files_out=files)
        self.assertNotIn("+late = 1", text)                      # cut from the delivered text
        self.assertIn("zz_late.py", files)                       # still known to the verdict


# ── delta run 2 findings (D2-2..D2-4), red first ─────────────────────────────

class DeltaRun2(_TmpDir):
    def test_both_rename_paths_are_delta_files(self):
        repo = self.tmp / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "old_name.py").write_text("".join(f"line_{i} = {i}\n" for i in range(40)), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "c0")
        from tasks.core import _scope_identity
        base = post_d6.worktree_tree(repo, [":(exclude).agent"])
        (repo / "old_name.py").rename(repo / "new_name.py")
        snap = {"scopes": {"": {"base": base, "identity": _scope_identity(repo, repo)}},
                "exclude": [":(exclude).agent"]}
        files = set()
        text, note = post_d6.delta_text(repo, snap, 50_000, files_out=files)
        self.assertIsNotNone(text, note)
        self.assertEqual({"old_name.py", "new_name.py"} - files, set(), files)

    def test_a_reservation_of_a_live_process_blocks_past_its_expiry(self):
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        import socket
        post_d6.append_run(self.tmp, {"id": "live1", "kind": "panel", "status": "reserved",
                                      "ts": "2020-01-01T00:00:00+00:00", "expires_after": 10.0,
                                      "pid": os.getpid(), "host": socket.gethostname()})
        ok, n, msg, _ = post_d6.reserve_run(tf, "p", None, stale_after=10)
        self.assertFalse(ok, "an old reservation whose process is alive was treated as stale")
        self.assertIn("in progress", msg)

    def test_a_reservation_of_a_dead_process_does_not_block(self):
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        import socket
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        post_d6.append_run(self.tmp, {"id": "dead1", "kind": "panel", "status": "reserved",
                                      "ts": datetime_now_iso(), "expires_after": 3600.0,
                                      "pid": dead.pid, "host": socket.gethostname()})
        self.assertTrue(post_d6.reserve_run(tf, "p", None, stale_after=3600)[0])

    def test_reservations_record_their_process(self):
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        post_d6.reserve_panel(tf, stale_after=60)
        rec = post_d6.read_runs(self.tmp)[0]
        self.assertEqual(rec.get("pid"), os.getpid())
        self.assertTrue(rec.get("host"))


class DeltaRun3(_TmpDir):
    def test_an_extensionless_delta_file_is_matched(self):
        # D3-1: `Makefile:8` has no dot extension
        text = "**Important** [PRE-EXISTING] — `Makefile:8` drops a flag.\n\nCAP: 1/5 reported, exhausted"
        self.assertEqual(post_d6.parse_verdict(text, delta_files={"Makefile"})["verdict"], "FAIL")
        self.assertEqual(post_d6.parse_verdict(text, delta_files={"other.py"})["verdict"], "PASS")

    def test_the_recorded_horizon_wins_over_a_longer_caller_timeout(self):
        # D3-2: where liveness cannot be judged, an expired 60 s reservation must not
        # block because the NEW caller has a 3,600 s timeout
        import datetime
        tf = self.tmp / "task.md"
        tf.write_text("# t\n", encoding="utf-8")
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(seconds=120)).isoformat(timespec="seconds")
        post_d6.append_run(self.tmp, {"id": "x1", "kind": "panel", "status": "reserved",
                                      "ts": old, "expires_after": 60.0, "host": "another-host", "pid": 1})
        self.assertTrue(post_d6.reserve_run(tf, "p", None, stale_after=3600)[0])

    def test_a_failed_file_enumeration_refuses_the_delta(self):
        # D3-3: a partial delta-file set must never feed the verdict
        from unittest import mock
        repo = self.tmp / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "c0")
        from tasks.core import _scope_identity
        base = post_d6.worktree_tree(repo, [":(exclude).agent"])
        (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
        snap = {"scopes": {"": {"base": base, "identity": _scope_identity(repo, repo)}},
                "exclude": [":(exclude).agent"]}
        real_run = subprocess.run

        def failing_name_status(cmd, *a, **k):
            if isinstance(cmd, list) and "--name-status" in cmd:
                return subprocess.CompletedProcess(cmd, 128, b"", b"boom")
            return real_run(cmd, *a, **k)
        with mock.patch("tasks.post_d6.subprocess.run", side_effect=failing_name_status):
            text, note = post_d6.delta_text(repo, snap, 50_000, files_out=set())
        self.assertIsNone(text)
        self.assertIn("file list", note)


def datetime_now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ── end to end: the real panel and impl-review entry points, judges faked ────

class EndToEnd(unittest.TestCase):
    """Drives `cmd_panel_review` and `tasks impl-review` in a git project with
    faked judges: the panel's payload carries the SETTLED block, the panel round
    records a per-scope `base`, the post-D6 single judge receives SETTLED + the
    POST-PANEL DELTA (the edited and the untracked file, not the unchanged one),
    its verdict is mechanical, and the run cap refuses run 3 until --owner-ok."""

    def setUp(self):
        from tasks import review as R
        self.R = R
        self.d = Path(tempfile.mkdtemp(prefix="pb108e2e-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        _git(self.d, "init", "-q")
        _git(self.d, "config", "user.email", "t@t")
        _git(self.d, "config", "user.name", "t")
        td = self.d / ".agent" / "tasks" / "001-demo"
        td.mkdir(parents=True)
        self.tf = td / "task.md"
        self.tf.write_text(
            "# 001 - demo\n\n## Status\nin-progress\n\n## Risk\nassertive\n\n## Intent\nx\n\n"
            "## Owner rulings\n<!-- pin -->\n- 2026-09-29 keep the daemon-only case without an export.\n\n"
            "## Implementation Review\n(implementation review triage appears here)\n\n"
            "## Work Plan\n- [ ] a gate\n", encoding="utf-8")
        (td / "judge.md").write_text(
            "### Run 1 triage\n- **S1-1 Important — nested dir sharing.** REJECT (pre-existing design).\n",
            encoding="utf-8")
        (self.d / ".agent" / "models.json").write_text(json.dumps(
            {"panel": ["claude:opus", "claude:sonnet"], "default_judge": "claude:opus"}), encoding="utf-8")
        (self.d / "MIND_MAP.md").write_text("# Mind Map\n[1] node\n", encoding="utf-8")
        (self.d / "keep.py").write_text("keep = 1\n", encoding="utf-8")
        (self.d / "edit.py").write_text("v = 1\n", encoding="utf-8")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-q", "-m", "c0")
        R._PB_JOURNAL_MOD = None
        R._PB_JOURNAL_LOADED = False
        self.panel_contexts = []
        self.single_calls = []

    def _single_runs(self):
        # the ledger also holds the panel's own reservation (kind "panel", never counted)
        return [r for r in post_d6.read_runs(self.tf.parent) if r.get("kind") != "panel"]

    def _in_project(self, fn):
        import contextlib
        import io
        err, out = io.StringIO(), io.StringIO()
        code = None
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                fn()
        except SystemExit as e:
            code = e.code
        finally:
            os.chdir(cwd)
        return code, err.getvalue(), out.getvalue()

    def _panel(self):
        from unittest import mock
        from provider.adapters.claude import ClaudeAdapter
        cap = self.panel_contexts

        def judge(self_, *a, **kw):
            cap.append(str(kw.get("system_context", "")) + " ".join(str(x) for x in a))
            return "1. **Important** — `edit.py:1` fine.\n\nCAP: 1/5 reported, exhausted\n"
        with mock.patch.object(ClaudeAdapter, "is_available", classmethod(lambda cls: True)), \
             mock.patch.object(ClaudeAdapter, "run_headless_judge", judge):
            return self._in_project(lambda: self.R.cmd_panel_review(
                ["001", "--mode", "impl", "--models", "claude:opus,claude:sonnet"]))

    def _single(self, output, *extra, rc=0):
        from unittest import mock
        from tasks import cli as tcli
        calls = self.single_calls

        def fake_run(agent, agent_args, **kw):
            calls.append(repr(agent_args) + repr(kw))
            if output is None:                     # the judge hits its hard timeout
                raise subprocess.TimeoutExpired(cmd="judge", timeout=1)
            return subprocess.CompletedProcess(args=[], returncode=rc, stdout=output, stderr="")
        with mock.patch.object(sys, "argv", ["tasks", "impl-review", "001", "--backend", "claude", *extra]), \
             mock.patch("shutil.which", return_value="/usr/bin/claude"), \
             mock.patch("provider.sandbox.run", side_effect=fake_run), \
             mock.patch("provider.sandbox.format_judge_output", side_effect=lambda r: (r.stdout or "")):
            return self._in_project(tcli.main)

    def test_a_tree_edit_between_delta_and_tamper_baseline_sends_nothing(self):
        from unittest import mock
        self._panel()
        (self.d / "edit.py").write_text("v = 2\n", encoding="utf-8")
        real = self.R._snapshot_repo_state

        def edit_then_snapshot(*a, **k):
            (self.d / "edit.py").write_text("v = 99\n", encoding="utf-8")   # lands after the delta
            return real(*a, **k)
        n = len(self.single_calls)
        with mock.patch.object(self.R, "_snapshot_repo_state", edit_then_snapshot):
            code, err, out = self._single("CAP: 0/5 reported, exhausted\n")
        self.assertEqual(code, 1, err)
        self.assertIn("changed while the delta was being built", err)
        self.assertEqual(len(self.single_calls), n, "a judge was spawned on a stale delta")
        self.assertEqual(self._single_runs(), [], "the aborted run kept its reservation")

    def test_a_scope_added_between_delta_and_tamper_baseline_sends_nothing(self):
        # D2-2: the re-check must compare the scope SET, not only the old scopes' trees
        from unittest import mock
        self._panel()
        (self.d / "edit.py").write_text("v = 2\n", encoding="utf-8")
        real = self.R._snapshot_repo_state
        lib = self.d / "lib"

        def add_scope_then_snapshot(*a, **k):
            lib.mkdir(exist_ok=True)
            _git(lib, "init", "-q")
            # keep the outer tree byte-identical (info/exclude is not in the tree):
            # ONLY the scope set changes, so only a scope-set re-check can catch it
            with open(self.d / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
                f.write("lib/\n")
            (self.d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["lib"]}), encoding="utf-8")
            return real(*a, **k)
        n = len(self.single_calls)
        with mock.patch.object(self.R, "_snapshot_repo_state", add_scope_then_snapshot):
            code, err, out = self._single("CAP: 0/5 reported, exhausted\n")
        self.assertEqual(code, 1, err)
        self.assertIn("changed while the delta was being built", err)
        self.assertEqual(len(self.single_calls), n)

    def test_a_scope_repointed_between_delta_and_tamper_baseline_sends_nothing(self):
        # The third thing the re-check compares, beside the trees and the scope SET: each
        # scope's DIRECTORY. Task 172 first cut this clause from the row for want of a test;
        # its impl panel (opus, sonnet) pointed at the test above as the way to write one.
        # The code root `lib` is a link; in between it is repointed to a clone with the SAME
        # tree — the set and every tree are unchanged, only where `lib` leads is not.
        from unittest import mock
        for name in ("lib_a", "lib_b"):
            repo = self.d / name
            repo.mkdir()
            _git(repo, "init", "-q")
            _git(repo, "config", "user.email", "t@t")
            _git(repo, "config", "user.name", "t")
            (repo / "m.py").write_text("m = 1\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "c0")
        os.symlink("lib_a", self.d / "lib")
        with open(self.d / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
            f.write("lib\nlib_a/\nlib_b/\n")
        (self.d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["lib"]}), encoding="utf-8")
        self._panel()
        (self.d / "edit.py").write_text("v = 2\n", encoding="utf-8")
        real = self.R._snapshot_repo_state

        def repoint_then_snapshot(*a, **k):
            os.remove(self.d / "lib")
            os.symlink("lib_b", self.d / "lib")
            return real(*a, **k)
        n = len(self.single_calls)
        with mock.patch.object(self.R, "_snapshot_repo_state", repoint_then_snapshot):
            code, err, out = self._single("CAP: 0/5 reported, exhausted\n")
        self.assertEqual(code, 1, err)
        self.assertIn("changed while the delta was being built", err)
        self.assertEqual(len(self.single_calls), n, "a judge was spawned on a scope that points elsewhere")

    def test_each_reservation_records_its_own_horizon(self):
        # "a reservation past its horizon counts as spent" — and the horizon is what the
        # entry point wrote: the hard review timeout plus 15 minutes for a panel, plus 10
        # for a single judge (impl panel r1, opus: the row said "+ 10 min" for both and no
        # test pinned either number)
        from tasks.core import resolve_review_timeout
        hard = resolve_review_timeout(self.d)
        self._panel()
        (self.d / "edit.py").write_text("v = 2\n", encoding="utf-8")
        self._single("CAP: 0/5 reported, exhausted\n")
        lines = [json.loads(ln) for ln in (self.tf.parent / post_d6.RUNS_NAME).read_text(encoding="utf-8").splitlines()]
        reserved = [r for r in lines if r.get("status") == "reserved"]
        panel = next(r for r in reserved if r.get("kind") == "panel")
        single = next(r for r in reserved if r.get("kind") != "panel")
        self.assertEqual((panel["expires_after"], single["expires_after"]), (hard + 900, hard + 600))

    def test_a_head_clamp_tells_the_judge(self):
        self._panel()
        (self.d / "edit.py").write_text("v = 2\n", encoding="utf-8")
        from unittest import mock
        with mock.patch("tasks.core.resolve_review_context_chars", return_value=800):
            self._single("CAP: 0/5 reported, exhausted\n")
        self.assertIn("HEAD-CLAMPED", self.single_calls[-1])

    def test_the_settled_block_comes_first_in_both_contexts(self):
        # PB-POST-D6-PROTOCOL says both builders deliver the SETTLED block FIRST; the tests
        # asserted that it is there, not where (task 170's binding audit; task 172). What
        # it must precede: the delta, the mind map and the task record.
        def first_of(context, *marks):
            at = {m: context.find(m) for m in marks}
            self.assertNotIn(-1, at.values(), at)
            return min(at, key=at.get)

        self._panel()
        self.assertEqual(first_of(self.panel_contexts[0], "=== SETTLED", "MIND_MAP", "## Intent"), "=== SETTLED")
        (self.d / "edit.py").write_text("v = 2\n", encoding="utf-8")
        self._single("CAP: 0/5 reported, exhausted\n")
        self.assertEqual(first_of(self.single_calls[-1], "=== SETTLED", "POST-PANEL DELTA", "MIND_MAP", "## Intent"),
                         "=== SETTLED")

    def test_protocol_end_to_end(self):
        code, err, out = self._panel()
        self.assertIn(code, (None, 0), err)
        self.assertTrue(self.panel_contexts, "no panel judge ran")
        self.assertIn("=== SETTLED", self.panel_contexts[0], "the panel payload lacks the SETTLED block")
        self.assertIn("keep the daemon-only case", self.panel_contexts[0])
        self.assertIn("REJECT (pre-existing design)", self.panel_contexts[0])
        rnd = post_d6.newest_impl_round(self.tf.parent)
        self.assertIsNotNone(rnd)
        base = rnd["snapshot"]["scopes"][""]["base"]
        self.assertRegex(base or "", r"^[0-9a-f]{40,64}$", "the panel recorded no base")

        (self.d / "edit.py").write_text("v = 2\n", encoding="utf-8")
        (self.d / "fresh.py").write_text("fresh = True\n", encoding="utf-8")
        code, err, out = self._single(
            "**Important** [SETTLED] — `edit.py:1` re-raises the no-export ruling.\n\nCAP: 1/5 reported, exhausted\n")
        # CI Windows (run 36689720491) spawned no judge here: say why if it recurs
        self.assertTrue(self.single_calls, f"no judge spawned (exit {code}): {err[-1500:]}")
        seen = self.single_calls[-1]
        self.assertIn("=== SETTLED", seen)
        self.assertIn("POST-PANEL DELTA", seen)
        self.assertIn("fresh.py", seen)
        self.assertIn("+v = 2", seen)
        delta_part = seen.split("POST-PANEL DELTA", 1)[1].split("=== MIND_MAP", 1)[0]
        self.assertNotIn("keep.py", delta_part)
        self.assertIn("POST-D6 VERDICT: PASS", out)
        self.assertEqual(len(self._single_runs()), 1)

        # a judge that fails with no review (a 402, a crash) releases its reserved slot
        code, err, out = self._single("", rc=1)
        self.assertNotIn("POST-D6 VERDICT", out)
        self.assertEqual(len(self._single_runs()), 1, "a failed run was counted")

        # a judge killed at its hard timeout DID spend: the run counts (codex S1-1)
        code, err, out = self._single(None)
        self.assertEqual(code, 1)
        runs = self._single_runs()
        self.assertEqual(len(runs), 2, "a timed-out run was released")
        self.assertEqual(runs[-1]["verdict"], "TIMEOUT")
        # clean the slate for the cap checks below: the timeout was run 2
        with open(self.tf.parent / post_d6.RUNS_NAME, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": runs[-1]["id"], "status": "failed"}) + "\n")
        ctx = (self.tf.parent / post_d6.CONTEXT_NAME).read_text(encoding="utf-8")
        self.assertIn("fresh.py", ctx)

        code, err, out = self._single(
            "**Important** [SETTLED-CONTRADICTED] — `edit.py:1` exports although the ruling says "
            "no export.\n\nCAP: 1/5 reported, exhausted\n")
        self.assertIn("POST-D6 VERDICT: FAIL", out)

        n_calls = len(self.single_calls)
        code, err, out = self._single("CAP: 0/5 reported, exhausted\n")
        self.assertEqual(code, 2, err)
        self.assertIn("post-D6 run cap", err)
        self.assertEqual(len(self.single_calls), n_calls, "the refused run spawned a judge")

        code, err, out = self._single("CAP: 0/5 reported, exhausted\n", "--owner-ok")
        self.assertEqual(code, 2)
        self.assertIn("--owner-ok requires --reason", err)

        code, err, out = self._single("No Critical or Important findings.\n\nCAP: 0/5 reported, exhausted\n",
                                      "--owner-ok", "--reason", "owner 2026-09-29: one more pass")
        self.assertIn("POST-D6 VERDICT: PASS", out)
        runs = self._single_runs()
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[-1]["owner_ok"], "owner 2026-09-29: one more pass")
        self.assertIn('owner-ok: "owner 2026-09-29: one more pass"',
                      post_d6.receipt_line(runs, post_d6.panel_key(rnd)))
        self.assertRegex(rnd["round_id"], r"^[0-9a-f]{12}$", "the panel wrote no Round-id")


if __name__ == "__main__":
    unittest.main()
