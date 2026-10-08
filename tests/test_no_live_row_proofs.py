#!/usr/bin/env python3
"""PLAN S12: one new proof per no-live ledger row (task 148).

Task 100 (PLAN S8) read every bound proof against its row's statement and
found, row by row, a clause no test asserted — several reproduced by mutation
(a dry run that writes, a heading a hostile reason forges, a writer that leaks
attribution into the body). Each class below proves the clause its row's
`missing_evidence_or_limitation` named, through the real CLI or hook where the
statement is about the CLI or hook. Each was watched red on a deliberate break
of the product line it depends on (task 148, `## Red-first`).

No-write clauses are checked with a full path -> bytes snapshot of the tree, not
a substring: an extra write anywhere fails them.

Run: python3 -m unittest tests.test_no_live_row_proofs
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "playbook"
SCRIPTS = PLUGIN / "scripts"
sys.path.insert(0, str(PLUGIN))
from tasks.readme_drift import BASELINE_REL, DEFAULT_COVERED_PATHS  # noqa: E402


def snapshot(root: Path, skip=(".git",)) -> dict:
    """path -> bytes for every file under root (directories as a marker), so
    a created, deleted or rewritten file anywhere changes the result."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel.split("/", 1)[0] in skip:
            continue
        out[rel] = p.read_bytes() if p.is_file() else b"<dir>"
    return out


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout


def new_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "t")
    return path


def write_task(project: Path, num: str, slug: str, status: str, gates: str,
               lane: str = "") -> Path:
    d = project / ".agent" / lane / "tasks" / f"{num}-{slug}" if lane else \
        project / ".agent" / "tasks" / f"{num}-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "task.md"
    p.write_text(f"# {num} - {slug}\n\n## Status\n{status}\n\n## Intent\n{slug} intent\n\n"
                 f"## Work Plan\n{gates}\n", encoding="utf-8")
    return p


class _Project(unittest.TestCase):
    """A throwaway single-user playbook project with a git repo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.project = new_repo(self.tmp / "proj")
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        self.env = dict(os.environ, PYTHONPATH=str(PLUGIN),
                        PLAYBOOK_SESSION_ID="pid-s12-proofs", HOME=str(self.tmp / "home"))
        self.env.pop("BASH_ENV", None)
        (self.tmp / "home").mkdir()
        # Something the session GC every command runs first WOULD reclaim (a legacy
        # flat pointer): a no-write claim must hold with it present (impl panel r1,
        # codex-high — the dry runs used to GC it before their own work).
        (self.project / ".agent" / "current_state").write_text("001\n", encoding="utf-8")

    def cli(self, *args, cwd=None, env=None):
        return subprocess.run(
            [sys.executable, "-m", "tasks.cli", *args], cwd=cwd or self.project,
            env=env or self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)


# ── PB-CLI-HELP-LAUNCHERS ─────────────────────────────────────────────────────
class LaunchersHelpRunsNoProvider(unittest.TestCase):
    """All four provider launchers, `--help` and `-h`: usage, exit 0, no session
    directory, and the provider binary itself is never run. The fake provider
    writes a marker when executed, so "not executed" is observed, not inferred
    from an exit code."""

    LAUNCHERS = ("codex", "grok", "agy", "pi")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.project = root / "project"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        self.bindir = root / "bin"
        self.bindir.mkdir()
        self.marker = root / "provider-ran"
        for name in self.LAUNCHERS:
            fake = self.bindir / name
            fake.write_text(f'#!/bin/sh\necho "{name}" >> "{self.marker.as_posix()}"\nexit 0\n',
                            encoding="utf-8")
            fake.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bindir}{os.pathsep}{os.environ.get('PATH', '')}")
        self.env.pop("BASH_ENV", None)
        self.env.pop("PLAYBOOK_SESSION_ID", None)

    def _launch(self, name, *args):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / f"playbook-{name}"), *args],
                              cwd=self.project, env=self.env, capture_output=True,
                              text=True, timeout=60)

    def test_every_launcher_help_flag_never_runs_the_provider(self):
        for name in self.LAUNCHERS:
            for flag in ("--help", "-h"):
                with self.subTest(launcher=name, flag=flag):
                    before = snapshot(self.project)
                    r = self._launch(name, flag)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(snapshot(self.project), before,
                                     f"playbook-{name} {flag} wrote to the project")
                    self.assertIn("Usage:", r.stdout)
                    self.assertFalse(self.marker.exists(),
                                     f"playbook-{name} {flag} ran the provider")
                    sessions = self.project / ".agent" / "sessions"
                    self.assertFalse(sessions.exists() and any(sessions.iterdir()),
                                     f"playbook-{name} {flag} provisioned a session")

    def test_launcher_without_help_does_run_the_provider(self):
        # Negative control: the same fixture without a help flag reaches the
        # provider (the marker appears) — so the help test's silence is the
        # help path's, not a fake that can never run.
        for name in self.LAUNCHERS:
            with self.subTest(launcher=name):
                if self.marker.exists():
                    self.marker.unlink()
                sessions = self.project / ".agent" / "sessions"
                if sessions.exists():
                    shutil.rmtree(sessions)
                self._launch(name)
                self.assertTrue(self.marker.exists(),
                                f"playbook-{name} never reached the fake provider")
                self.assertIn(name, self.marker.read_text(encoding="utf-8"))
                # and it provisions a session dir — the help test's "no session"
                # half can see one (impl panel r1, opus)
                self.assertTrue(sessions.is_dir() and any(sessions.iterdir()),
                                f"playbook-{name} provisioned no session dir")


# ── PB-CLI-DRY-COMPACT ────────────────────────────────────────────────────────
COMPACT_BODY = """# 012 - Example

## Status
done

## Implementation Review
- [x] Triage findings
<!-- archive:start -->
### Round 1 findings
Cold narrative that only bloats the review keyhole.
<!-- archive:end -->

## Parked
Nothing.
"""


class CompactDryRunIsByteIdentical(_Project):
    def setUp(self):
        super().setUp()
        self.task_dir = self.project / ".agent" / "tasks" / "012-example"
        self.task_dir.mkdir()
        (self.task_dir / "task.md").write_text(COMPACT_BODY, encoding="utf-8")

    def test_compact_dry_run_leaves_the_task_dir_byte_identical(self):
        before = snapshot(self.project)
        r = self.cli("compact", "12", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[dry-run]", r.stdout)
        self.assertEqual(snapshot(self.project), before,
                         "compact --dry-run wrote to the project")

    def test_compact_without_dry_run_changes_the_task_dir(self):
        # Negative control: the same fixture without --dry-run does move the
        # block — the snapshot comparison can see a compaction.
        before = snapshot(self.project)
        self.assertEqual(self.cli("compact", "12").returncode, 0)
        self.assertNotEqual(snapshot(self.project), before)
        self.assertTrue((self.task_dir / "task-archive.md").exists())


class SessionGcSkipsOnlyTheWriteNothingInvocations(unittest.TestCase):
    """The GC exemption (task 148) is exactly the invocations documented to write
    nothing: a future write mode of merge-doctor, or a dry-run flag on another
    command, must change this table on purpose (impl panel r2, sonnet)."""

    def test_exempt_set(self):
        from tasks.cli import _writes_nothing
        yes = [("merge-doctor", []), ("merge-doctor", ["a", "b"]), ("compact", ["12", "--dry-run"]),
               ("prepare-merge", ["--target", "main", "--dry-run"]), ("tag", ["--dry-run"])]
        no = [("compact", ["12"]), ("prepare-merge", ["--target", "main"]), ("tag", []),
              ("work", ["done", "--dry-run"]), ("new", ["light", "x", "--dry-run"]), ("audit", [])]
        self.assertEqual([c for c, a in yes if not _writes_nothing(c, a)], [])
        self.assertEqual([(c, a) for c, a in no if _writes_nothing(c, a)], [])


# ── PB-CLI-DRY-MERGE-PREP ─────────────────────────────────────────────────────
class PrepareMergeDryRunWritesNothing(_Project):
    """Both early returns of the dry run: the colliding task directory (tasks
    arm) and the chat-log re-sequence (chat-log arm) are previewed, and the
    whole working tree stays byte-identical."""

    def _chat(self, *ids):
        log = self.project / ".agent" / "chat_log.md"
        log.write_text("# Chat Log\n\n---\n\n" + "\n---\n\n".join(
            f"**[M{i:03d}]** [2026-10-0{i} 10:00:00 UTC] `HOST` (claude/pid-1)\n\nmsg {i}\n"
            for i in ids), encoding="utf-8")

    def setUp(self):
        super().setUp()
        (self.project / "README").write_text("x\n", encoding="utf-8")
        self._chat(1)
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "base")
        write_task(self.project, "002", "on-main", "pending", "- [ ] g")
        self._chat(1, 2)
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "main work")
        git(self.project, "checkout", "-q", "-b", "feature", "HEAD~1")
        write_task(self.project, "002", "on-feature", "pending", "- [ ] g")
        self._chat(1, 2)
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "feature work")

    def test_prepare_merge_dry_run_previews_both_arms_and_writes_nothing(self):
        before = snapshot(self.project)
        r = self.cli("prepare-merge", "--target", "main", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[dry-run] rename", r.stdout)
        self.assertIn("Chat log: re-sequencing", r.stdout)
        self.assertIn("(dry-run — no files written)", r.stdout)
        self.assertEqual(snapshot(self.project), before,
                         "prepare-merge --dry-run wrote to the working tree")

    def test_prepare_merge_without_dry_run_renames_and_resequences(self):
        # Negative control: the applying run changes both things the dry run
        # previewed.
        r = self.cli("prepare-merge", "--target", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        names = sorted(p.name for p in (self.project / ".agent" / "tasks").iterdir())
        self.assertIn("003-on-feature", names)
        self.assertIn("**[M003]**", (self.project / ".agent" / "chat_log.md").read_text(encoding="utf-8"))


# ── PB-CLI-SANDBOX-INSPECTION ─────────────────────────────────────────────────
class SandboxInspectionPrintsItsContent(_Project):
    """The inspection flags return their CONTENT — the capability matrix, the
    alias table — alone and combined with --prompt."""

    def _sandbox(self, *args):
        # Task 151: the wrapper sets its own PYTHONPATH (joined to an inherited one with `:`);
        # drop the inherited one so the run sees only the wrapper's.
        env = {k: v for k, v in self.env.items() if k != "PYTHONPATH"}
        return subprocess.run([bash_or_skip(), str(SCRIPTS / "sandbox"), *args],
                              cwd=self.project, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60)

    def test_inspection_flags_print_profile_matrix_and_aliases(self):
        expect = {
            "--list-agents": ("Sandbox agent capability matrix:", "claude", "codex", "grok"),
            "--list-models": ("Model aliases", "opus", "-> --agent claude"),
        }
        for flag, needles in expect.items():
            for extra in ((), ("--prompt", "hello")):
                with self.subTest(flag=flag, extra=extra):
                    r = self._sandbox(flag, *extra)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    for needle in needles:
                        self.assertIn(needle, r.stdout)

    def test_each_inspection_flag_prints_only_its_own_content(self):
        # Negative control: the needles are specific — each flag's output
        # lacks the other flag's header, so a flag that printed the wrong
        # table (or both) would not pass the content test by accident.
        heads = {"--list-agents": "capability matrix", "--list-models": "Model aliases"}
        for flag in heads:
            with self.subTest(flag=flag):
                out = self._sandbox(flag).stdout
                for other, head in heads.items():
                    (self.assertIn if other == flag else self.assertNotIn)(head, out)


def chat_log_text(n: int, body: str = "message") -> str:
    return "# Chat Log\n\n---\n\n" + "\n---\n\n".join(
        f"**[M{i:03d}]** [2026-07-0{i} 10:0{i}:00 UTC] `HOST` (claude/pid-{i})\n\n{body} {i}\n"
        for i in range(1, n + 1))


# ── PB-CLI-LOG ────────────────────────────────────────────────────────────────
class TasksLogWindowWidthAndTimestamp(_Project):
    def setUp(self):
        super().setUp()
        (self.project / ".agent" / "chat_log.md").write_text(
            chat_log_text(4, "a fairly long message body that can be cropped"), encoding="utf-8")

    def lines(self, *args, cwd=None):
        r = self.cli("log", *args, cwd=cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_last_n_window_and_timestamp_on_every_line(self):
        out = self.lines("2")
        self.assertEqual([ln.split()[0] for ln in out], ["[M003]", "[M004]"])
        for i, ln in zip((3, 4), out):
            self.assertRegex(ln, rf"^\[M00{i}\] 2026-07-0{i} 10:0{i} claude\s")

    def test_width_crops_the_body_with_a_ten_character_floor(self):
        wide = self.lines("--width", "20")
        for ln in wide:
            body = ln.split(" claude ", 1)[1].strip()
            self.assertEqual(len(body), 20, ln)
            self.assertTrue(body.endswith("…"), ln)
        floor = self.lines("--width", "3")
        for ln in floor:
            body = ln.split(" claude ", 1)[1].strip()
            self.assertEqual(len(body), 10, f"--width 3 must floor at 10: {ln}")

    def test_multi_user_lane_log_renders_from_the_lane(self):
        (self.project / ".agent" / "current_user").write_text("alice\n", encoding="utf-8")
        lane = self.project / ".agent" / "alice"
        (lane / "tasks").mkdir(parents=True)
        (lane / "chat_log.md").write_text(chat_log_text(1, "alice lane"), encoding="utf-8")
        out = self.lines()
        self.assertEqual(len(out), 1)
        self.assertIn("alice lane 1", out[0])
        self.assertNotIn("cropped", out[0])   # the root log is not this lane's


# ── PB-CLI-DRY-TAG ────────────────────────────────────────────────────────────
class TagDryRunWritesNothing(_Project):
    def setUp(self):
        super().setUp()
        self.env["TZ"] = "UTC"
        self.log = self.project / ".agent" / "chat_log.md"
        self.log.write_text(chat_log_text(3), encoding="utf-8")
        (self.project / ".agent" / "bash_history").write_text(
            "2026-07-02 09:00:00 | AGENT | tasks work 7\n", encoding="utf-8")

    def test_tag_dry_run_previews_and_leaves_the_tree_byte_identical(self):
        before = snapshot(self.project)
        r = self.cli("tag", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Would insert", r.stdout)
        self.assertIn("<!-- T007 -->", r.stdout)
        self.assertEqual(snapshot(self.project), before, "tag --dry-run wrote")

    def test_bare_tag_announces_its_rewrite_and_rewrites(self):
        # PLAN S12: a bare `tasks tag` (it rewrote chat_log.md with no
        # arguments, task 079) must say that it rewrites the file. Also the
        # negative control of the dry run: the real run changes the file.
        before = self.log.read_bytes()
        r = self.cli("tag")
        self.assertEqual(r.returncode, 0, r.stderr)
        m = re.search(r"Inserted (\d+) tags into chat_log\.md", r.stdout)
        self.assertIsNotNone(m, r.stdout)
        added = len(re.findall(r"^<!-- /?T\d+ -->$", self.log.read_text(encoding="utf-8"), re.M))
        self.assertGreater(added, 0)
        self.assertEqual(int(m.group(1)), added, "the announced count is not the tags inserted")
        self.assertNotEqual(self.log.read_bytes(), before)
        self.assertIn("<!-- T007 -->", self.log.read_text(encoding="utf-8"))


# ── PB-CLI-DETECT-VERIFY ──────────────────────────────────────────────────────
class DetectVerifyNeverRunsTheComposedCommand(_Project):
    """Every tool the composed command would name is a fake on PATH that writes
    a marker when run; detect-verify (text and --json) must leave none."""

    TOOLS = ("npm", "npx", "make", "cargo", "go", "mypy", "ruff", "pyright",
             "flake8", "tsc", "eslint", "pytest")

    def setUp(self):
        super().setUp()
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        self.marker = self.tmp / "ran"
        for tool in self.TOOLS:
            f = self.bindir / tool
            f.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{self.marker.as_posix()}"\nexit 0\n',
                         encoding="utf-8")
            f.chmod(0o755)
        # python3 too (impl panel r2): a composed `python3 -m unittest …` run, or a
        # second probe, would go through it. The fake logs its argv and forwards
        # to the real interpreter, so the documented pytest probe still works.
        self.pylog = self.tmp / "python3-calls"
        py = self.bindir / "python3"
        py.write_text(f'#!/bin/sh\necho "$*" >> "{self.pylog.as_posix()}"\n'
                      f'exec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8")
        py.chmod(0o755)
        self.env["PATH"] = f"{self.bindir}{os.pathsep}{os.environ.get('PATH', '')}"
        p = self.project
        (p / "package.json").write_text(json.dumps(
            {"scripts": {"test": "jest", "lint": "eslint .", "typecheck": "tsc"}}), encoding="utf-8")
        (p / "tsconfig.json").write_text("{}", encoding="utf-8")
        (p / "Makefile").write_text("test:\n\techo hi\n", encoding="utf-8")
        (p / "pyproject.toml").write_text("[tool.ruff]\n[tool.mypy]\n", encoding="utf-8")
        (p / "tests").mkdir()
        (p / "tests" / "test_x.py").write_text("import unittest\n", encoding="utf-8")

    def test_detect_verify_composes_but_never_executes(self):
        for args in (("detect-verify",), ("detect-verify", "--json")):
            with self.subTest(args=args):
                r = self.cli(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("npm", r.stdout)       # it did compose a command
                self.assertFalse(self.marker.exists(),
                                 "detect-verify executed: " + (self.marker.read_text()
                                                               if self.marker.exists() else ""))
                calls = (self.pylog.read_text(encoding="utf-8").splitlines()
                         if self.pylog.exists() else [])
                if self.pylog.exists():
                    self.pylog.unlink()
                self.assertEqual([c for c in calls if c.strip() != "-m pytest --version"], [],
                                 "python3 ran something other than the pytest probe")


# ── PB-CLI-BOOTSTRAP ──────────────────────────────────────────────────────────
class BootstrapPrintsMapTasksAndReference(_Project):
    def test_bootstrap_prints_the_mind_map_the_pending_tasks_and_the_cli_reference(self):
        (self.project / "MIND_MAP.md").write_text(
            "# Mind map\n\n[1] **Zebra node** - a distinctive node.\n", encoding="utf-8")
        write_task(self.project, "001", "open-work", "pending", "- [ ] next gate")
        write_task(self.project, "002", "closed-work", "done", "- [x] g")
        r = self.cli("bootstrap")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        order = [out.find(h) for h in ("=== MIND MAP", "=== PENDING TASKS ===", "=== CLI REFERENCE ===")]
        self.assertNotIn(-1, order, out)
        self.assertEqual(order, sorted(order), "sections out of order")
        self.assertIn("**Zebra node**", out[order[0]:order[1]])
        pending = out[order[1]:order[2]]
        self.assertIn("001-open-work", pending)
        self.assertNotIn("002-closed-work", pending)
        self.assertIn("tasks work <N>", out[order[2]:])


# ── PB-CLI-LIST ───────────────────────────────────────────────────────────────
class ListOverviewAndPendingFilter(_Project):
    def setUp(self):
        super().setUp()
        three = "- [x] one — done\n- [ ] two\n- [ ] three"
        write_task(self.project, "001", "alpha", "pending", three)
        write_task(self.project, "002", "beta", "blocked", three)
        write_task(self.project, "003", "gamma", "done", three)

    def rows(self, *args):
        r = self.cli("list", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {ln.split("|")[0].strip(): [c.strip() for c in ln.split("|")[1:]]
                for ln in r.stdout.splitlines() if re.match(r"^\d{3}-", ln)}, r.stdout

    def test_overview_rows_carry_status_and_progress_and_pending_drops_done(self):
        rows, out = self.rows()
        self.assertEqual(rows["001-alpha"][:2], ["pending", "1/3"])
        self.assertEqual(rows["002-beta"][:2], ["blocked", "1/3"])
        self.assertEqual(rows["003-gamma"][:2], ["done", "1/3"])
        self.assertIn("Summary: 1 done, 1 pending, 1 blocked", out)
        open_rows, out = self.rows("--pending")
        self.assertEqual(sorted(open_rows), ["001-alpha", "002-beta"])
        self.assertIn("(showing 2 open)", out)


# ── PB-CLI-STATUS ─────────────────────────────────────────────────────────────
class StatusShowsEveryOpenTasksGatePosition(_Project):
    """`tasks status` lists every task that is not done with its progress and
    its first unchecked gate; a blocked task shows the resume command instead.
    It does not read the session pointer (task 148 measured it): an active
    task appears because its status is not done."""

    def setUp(self):
        super().setUp()
        three = "- [x] one — done\n- [ ] the second gate\n- [ ] three"
        write_task(self.project, "001", "alpha", "pending", three)
        write_task(self.project, "002", "beta", "blocked", three)
        write_task(self.project, "003", "gamma", "done", three)

    def test_status_renders_head_gate_blocked_line_and_skips_done(self):
        outputs = []
        for pointer in (None, "001", "999"):   # none, live, stale — same output
            with self.subTest(pointer=pointer):
                sess = self.project / ".agent" / "sessions" / "pid-s12-proofs"
                if pointer:
                    sess.mkdir(parents=True, exist_ok=True)
                    (sess / "current_state").write_text(pointer + "\n", encoding="utf-8")
                r = self.cli("status")
                self.assertEqual(r.returncode, 0, r.stderr)
                lines = {ln.split("|")[0].strip(): [c.strip() for c in ln.split("|")[1:]]
                         for ln in r.stdout.splitlines() if "|" in ln}
                self.assertEqual(lines["001-alpha"], ["1/3", "the second gate"])
                self.assertEqual(lines["002-beta"][0], "1/3")
                self.assertIn("BLOCKED", lines["002-beta"][1])
                self.assertIn("tasks work 002", lines["002-beta"][1])
                self.assertNotIn("003-gamma", lines)
                outputs.append(r.stdout)
        self.assertEqual(len(set(outputs)), 1, "status output depends on the pointer")


# ── PB-CLI-RETRO ──────────────────────────────────────────────────────────────
class RetroReportCountsAnnotatedGates(_Project):
    def test_retro_report_counts_gates_with_annotated_continuation_lines(self):
        gates = ("- [x] Fix — 4 changes:\n"
                 "  1. did a\n"
                 "  2. did b\n"
                 "- [x] Verify results\n"
                 "  → ran the suite, 12 pass\n"
                 "- [x] A genuinely bare gate\n"
                 "- [ ] not checked")
        write_task(self.project, "001", "work", "done", gates)
        r = self.cli("retro")
        self.assertEqual(r.returncode, 0, r.stderr)
        tasks = sorted(self.project.glob(".agent/tasks/*retro*/task.md"))
        self.assertEqual(len(tasks), 1, r.stdout)
        report = tasks[0].read_text(encoding="utf-8")
        row = next(ln for ln in report.splitlines() if ln.startswith("| 001 |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        # | # | Title | Status | Gates | Bare | Type |: three of four checked,
        # and only the gate with nothing under it is bare
        self.assertEqual(cells[3:5], ["3/4", "1"], row)


# ── PB-CLI-TIMELINE-TAGGER ────────────────────────────────────────────────────
class TimelineAndTaggerNameTheMissingFile(_Project):
    """Single-user repo: each command exits 1 naming the input it lacks —
    including tagger's SECOND check (chat log present, shell history absent)."""

    def test_missing_inputs_exit_one_with_the_path(self):
        r = self.cli("timeline")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No .agent/bash_history found.", r.stderr)
        r = self.cli("tagger")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No .agent/chat_log.md found.", r.stderr)
        (self.project / ".agent" / "chat_log.md").write_text(chat_log_text(1), encoding="utf-8")
        r = self.cli("tagger")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No .agent/bash_history found.", r.stderr)


class _ContaminatedMerge(_Project):
    """A two-lane repo after a merge: alice's chat log carries a long line of
    bob's, a tracked prose file carries stranded conflict markers, a legacy
    shared file is tracked under `.agent/`, and the exempt `.agent/config.json`
    is tracked too (it must NOT be reported)."""

    BOB_LINE = "[bob-only line: a long sentence only bob ever wrote here]"
    contaminate = True

    def setUp(self):
        super().setUp()
        p = self.project
        shutil.rmtree(p / ".agent" / "tasks")
        (p / "README").write_text("x\n", encoding="utf-8")
        # the reclaimable legacy pointer stays out of git, as a real one would
        (p / ".gitignore").write_text(".agent/current_state\n", encoding="utf-8")
        git(p, "add", "-A")
        git(p, "commit", "-qm", "base")
        git(p, "checkout", "-q", "-b", "alice")
        (p / ".agent" / "alice").mkdir(parents=True)
        (p / ".agent" / "alice" / "chat_log.md").write_text(
            "[alice-only line: alice wrote this long first line]\n", encoding="utf-8")
        git(p, "add", "-A")
        git(p, "commit", "-qm", "alice lane")
        git(p, "checkout", "-q", "main")
        git(p, "checkout", "-q", "-b", "bob")
        (p / ".agent" / "bob").mkdir(parents=True)
        (p / ".agent" / "bob" / "chat_log.md").write_text(self.BOB_LINE + "\n", encoding="utf-8")
        git(p, "add", "-A")
        git(p, "commit", "-qm", "bob lane")
        git(p, "checkout", "-q", "alice")
        git(p, "merge", "-q", "--no-ff", "-m", "merge bob", "bob")
        (p / ".agent" / "config.json").write_text("{}\n", encoding="utf-8")
        git(p, "add", ".agent/config.json")
        git(p, "commit", "-qm", "repo policy")
        if not self.contaminate:
            return
        with open(p / ".agent" / "alice" / "chat_log.md", "a", encoding="utf-8") as fh:
            fh.write(self.BOB_LINE + "\n")
        (p / "NOTES.md").write_text("notes\n<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> bob\n",
                                    encoding="utf-8")
        (p / ".agent" / "legacy.txt").write_text("old shared state\n", encoding="utf-8")
        git(p, "add", "NOTES.md", ".agent/legacy.txt")
        git(p, "commit", "-qm", "post-merge edits")


# ── PB-CLI-MERGE-DOCTOR ───────────────────────────────────────────────────────
class MergeDoctorFullRunIsReadOnly(_ContaminatedMerge):
    def test_merge_doctor_full_run_leaves_the_tree_byte_identical(self):
        before = snapshot(self.project)
        head = git(self.project, "rev-parse", "HEAD")
        r = self.cli("merge-doctor", "bob", "alice")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)   # it found things
        self.assertIn("[ACTIONABLE]", r.stdout)
        self.assertEqual(snapshot(self.project), before, "merge-doctor wrote to the tree")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), head)


# ── PB-MERGE-CONTAMINATION ────────────────────────────────────────────────────
class MergeDoctorReportsEachContaminationKind(_ContaminatedMerge):
    def _actionable(self, out):
        m = re.search(r"^\[ACTIONABLE\][^\n]*\n(.*?)(?=^\[|^merge-doctor:|\Z)", out, re.M | re.S)
        self.assertIsNotNone(m, out)
        return m.group(1)

    def test_report_names_cross_lane_marker_and_legacy_findings_not_config(self):
        r = self.cli("merge-doctor", "bob", "alice")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        actionable = self._actionable(r.stdout)
        self.assertIn("contamination: .agent/alice/chat_log.md", actionable)
        self.assertIn("bob:.agent/bob/chat_log.md", actionable)
        self.assertIn("stranded conflict markers in NOTES.md", actionable)
        self.assertIn("legacy shared path tracked in git: .agent/legacy.txt", actionable)
        self.assertNotIn(".agent/config.json", r.stdout)


class MergeDoctorCleanTwoLaneMergeIsQuiet(_ContaminatedMerge):
    """Negative control for the report: the same two-lane merge (with the
    exempt tracked config) and no contamination reports nothing actionable."""
    contaminate = False

    def test_clean_two_lane_merge_reports_nothing_actionable(self):
        r = self.cli("merge-doctor", "bob", "alice")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("[ACTIONABLE]", r.stdout)
        self.assertNotIn(".agent/config.json", r.stdout)


# ── PB-TASK-BLOCKED ───────────────────────────────────────────────────────────
class BlockedReasonMintsNoHeading(_Project):
    def test_hostile_multi_line_reason_adds_no_line_start_heading(self):
        p = write_task(self.project, "001", "work", "pending", "- [ ] g")
        self.assertEqual(self.cli("work", "1").returncode, 0)
        headings_before = [ln for ln in p.read_text(encoding="utf-8").splitlines()
                           if ln.startswith("#")]
        r = self.cli("blocked", "waiting\n## Fake Heading\n# Another\n- [x] forged gate")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text(encoding="utf-8")
        headings = [ln for ln in text.splitlines() if ln.startswith("#")]
        self.assertEqual(sorted(set(headings) - set(headings_before)), ["## Blocked"],
                         "a hostile reason minted a heading:\n" + text)
        self.assertNotIn("\n- [x] forged gate", text)
        self.assertIn("Fake Heading", text)   # the words are kept, inert


# ── PB-CHAT-LOG ───────────────────────────────────────────────────────────────
class ChatLogStoredBodyIsExactlyTheMessage(_Project):
    def test_the_stored_body_equals_the_submitted_message(self):
        messages = ["exact body: (claude/pid-x) look-alike text stays as typed",
                    "a second, different message"]
        env = dict(self.env, PLAYBOOK_PROVIDER="claude", PLAYBOOK_SESSION_ID="pid-clw-exact")
        for message in messages:
            r = subprocess.run([bash_or_skip(), str(SCRIPTS / "chat-log-hook")],
                               cwd=self.project, env=env, text=True, encoding="utf-8",
                               input=json.dumps({"prompt": message}), capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.project / ".agent" / "chat_log.md").read_text(encoding="utf-8")
        entries = re.findall(
            r"^\*\*\[(M\d+)\]\*\* \[[^\]]+ UTC\] `HOST` \(claude/pid-clw-exact\)\n\n(.*?)\n(?:\n---\n|\n*\Z)",
            text, re.M | re.S)
        self.assertEqual([mid for mid, _ in entries], ["M001", "M002"], text)
        self.assertEqual([body for _, body in entries], messages,
                         "a stored body differs from the message submitted")


# ── PB-CONFIG-PRECEDENCE ──────────────────────────────────────────────────────
class ReviewBudgetAndTimeoutAtCliDispatch(unittest.TestCase):
    """The precedence matrix through the real `tasks plan-review` dispatch:
    the faked sandbox receives the budget the claude judge's argv carries and
    the hard timeout it is armed with."""

    def setUp(self):
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        from tasks import core
        self.core = core
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = new_repo(Path(self._tmp.name) / "proj")
        tdir = self.project / ".agent" / "tasks" / "001-test"
        tdir.mkdir(parents=True)
        (tdir / "task.md").write_text(
            "# 001 - Test\n\n## Status\npending\n\n## Intent\nx\n\n"
            "## Plan Review\n(plan review triage appears here)\n\n"
            "## Design Phase\n- [ ] a gate\n", encoding="utf-8")
        (self.project / "MIND_MAP.md").write_text("# Mind Map\n[1] node\n", encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "init")

    def _resolved(self, config=None, env=None, argv=()):
        import contextlib
        import io
        from unittest import mock
        from tasks import cli as tcli
        cfg = self.project / ".agent" / "config.json"
        if config is None:
            cfg.unlink(missing_ok=True)
        else:
            cfg.write_text(json.dumps(config), encoding="utf-8")
        self.core._warn_bad_config_value_once.cache_clear()
        seen = {}

        def fake_run(agent, agent_args, **kw):
            seen["budget"] = agent_args[agent_args.index("--max-budget-usd") + 1]
            seen["timeout"] = kw.get("timeout")
            return subprocess.CompletedProcess(agent_args, 0, "review text", "")

        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(("PLAYBOOK_JUDGE_BUDGET_USD", "PLAYBOOK_REVIEW_"))}
        clean.update(env or {})
        cwd = os.getcwd()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, clean, clear=True))
            stack.enter_context(mock.patch.object(
                sys, "argv", ["tasks", "plan-review", "001", "--backend", "claude", *argv]))
            stack.enter_context(mock.patch("shutil.which", return_value="/usr/bin/claude"))
            stack.enter_context(mock.patch("provider.sandbox.run", side_effect=fake_run))
            stack.enter_context(mock.patch("provider.sandbox.format_judge_output",
                                           side_effect=lambda r: r.stdout or ""))
            os.chdir(self.project)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    try:
                        tcli.main()
                    except SystemExit:
                        pass
            finally:
                os.chdir(cwd)
        return seen.get("budget"), seen.get("timeout")

    def test_flag_over_env_over_config_over_default_with_the_config_floor(self):
        cfg = {"judge_budget_usd": 3, "review_timeout_secs": 500}
        env = {"PLAYBOOK_JUDGE_BUDGET_USD": "4", "PLAYBOOK_REVIEW_TIMEOUT_SECS": "600"}
        cases = [
            ("default", None, None, (), ("10", 1200)),
            ("config", cfg, None, (), ("3", 500)),
            ("env over config", cfg, env, (), ("4", 600)),
            ("flag over env", cfg, env, ("--budget", "5", "--timeout", "700"), ("5", 700)),
            ("config hard timeout floors a lower flag", cfg, None, ("--timeout", "100"), ("3", 500)),
        ]
        for name, config, environ, argv, want in cases:
            with self.subTest(name):
                self.assertEqual(self._resolved(config, environ, argv), want)

    def test_dashboard_subprocess_shows_env_over_config_over_default(self):
        # The same resolvers through a separate process: `tasks dashboard`
        # prints the review knobs it would hand a judge.
        cfg = self.project / ".agent" / "config.json"
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("PLAYBOOK_JUDGE_BUDGET_USD", "PLAYBOOK_REVIEW_"))}
        base.update(PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="pid-s12-knobs")
        cfg_all = {"judge_budget_usd": 3, "review_timeout_secs": 500, "review_soft_timeout_secs": 400}
        env_all = {"PLAYBOOK_JUDGE_BUDGET_USD": "4", "PLAYBOOK_REVIEW_TIMEOUT_SECS": "600",
                   "PLAYBOOK_REVIEW_SOFT_TIMEOUT_SECS": "450"}
        cases = [
            ("default", None, {}, ("900", "1200", "10")),
            ("config", cfg_all, {}, ("400", "500", "3")),
            ("env over config", cfg_all, env_all, ("450", "600", "4")),
        ]
        for name, config, env, want in cases:
            with self.subTest(name):
                if config is None:
                    cfg.unlink(missing_ok=True)
                else:
                    cfg.write_text(json.dumps(config), encoding="utf-8")
                r = subprocess.run([sys.executable, "-m", "tasks.cli", "dashboard"],
                                   cwd=self.project, env={**base, **env}, capture_output=True,
                                   text=True, encoding="utf-8", timeout=120)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self._knobs(r), want)

    def _knobs(self, r):
        m = re.search(r"review knobs: soft timeout (\d+)s · hard timeout (\d+)s · judge budget \$(\S+)",
                      r.stdout)
        self.assertIsNotNone(m, r.stdout)
        return m.groups()

    def test_dashboard_subprocess_ignores_invalid_values(self):
        # Negative control for the dashboard instrument: values that do not
        # parse never show — the next valid tier (here the default) does.
        (self.project / ".agent" / "config.json").write_text(json.dumps(
            {"judge_budget_usd": "lots", "review_timeout_secs": "soon",
             "review_soft_timeout_secs": -5}), encoding="utf-8")
        base = {k: v for k, v in os.environ.items() if not k.startswith(
            ("PLAYBOOK_JUDGE_BUDGET_USD", "PLAYBOOK_REVIEW_"))}
        base.update(PYTHONPATH=str(PLUGIN), PLAYBOOK_SESSION_ID="pid-s12-knobs",
                    PLAYBOOK_JUDGE_BUDGET_USD="nan")
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "dashboard"], cwd=self.project,
                           env=base, capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._knobs(r), ("900", "1200", "10"))

    def test_invalid_values_fall_through_to_the_next_tier(self):
        # Negative control: a value that does not parse never wins — the next
        # valid tier does.
        cfg = {"judge_budget_usd": "lots", "review_timeout_secs": "soon"}
        env = {"PLAYBOOK_JUDGE_BUDGET_USD": "nan"}
        self.assertEqual(self._resolved(cfg, env, ("--budget", "-1")), ("10", 1200))
        self.assertEqual(self._resolved(None, {"PLAYBOOK_JUDGE_BUDGET_USD": "4"},
                                        ("--budget", "inf")), ("4", 1200))


# ── PB-CONFIG-PERSISTENCE ─────────────────────────────────────────────────────
class RejectedModelsSetLeavesAnExistingFileByteIdentical(unittest.TestCase):
    """A rejected `tasks models set` — a bad spec, or a dead pin without
    --force — leaves an EXISTING hand-authored models.json byte-identical (the
    bound proofs covered only the fresh state, where nothing exists to clobber).
    The provider probe is faked, as in test_model_availability.SetTest."""

    def setUp(self):
        from unittest import mock
        sys.path.insert(0, str(PLUGIN))
        self.addCleanup(sys.path.remove, str(PLUGIN))
        from tasks import models_check as mc
        self.mc = mc
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        (self.project / ".agent").mkdir()
        self.models = self.project / ".agent" / "models.json"
        self.models.write_text(json.dumps({
            "_doc": "hand-written note the user cares about",
            "aliases": {"mine": ["claude", "claude-opus-5-5", []]},
            "panel": ["opus", "sonnet"],
            "default_judge": "opus",
        }, indent=2) + "\n", encoding="utf-8")
        gone = {"spec": "codex:gpt-5.3-codex", "provider": "codex", "variant": "gpt-5.3-codex",
                "verdict": mc.GONE, "detail": "dead"}
        for patch in (
                mock.patch.object(mc, "check_pins", return_value={
                    "entries": [gone], "codex": None, "codex_cli_version": None,
                    "agy_models": None, "claude_candidates": [], "warnings": []}),
                mock.patch("provider.sandbox.load_judge_config",
                           return_value={"default_judge": "opus", "panel": ["opus", "sonnet"]})):
            patch.start()
            self.addCleanup(patch.stop)

    def _set(self, *argv):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.mc.cli_models(["set", *argv], self.project)

    def test_rejected_set_keeps_a_hand_authored_models_json_byte_identical(self):
        before = self.models.read_bytes()
        for argv in (("--panel", "not-a-provider:x"),          # bad spec
                     ("--panel", "codex:gpt-5.3-codex")):     # dead pin, no --force
            with self.subTest(argv=argv):
                self.assertEqual(self._set(*argv), 1)
                self.assertEqual(self.models.read_bytes(), before,
                                 f"a rejected `set {' '.join(argv)}` rewrote models.json")

    def test_forced_dead_pin_does_write(self):
        # Negative control: with --force the same dead pin is written, so the
        # byte comparison above can see a write.
        before = self.models.read_bytes()
        self.assertEqual(self._set("--panel", "codex:gpt-5.3-codex", "--force"), 0)
        self.assertNotEqual(self.models.read_bytes(), before)
        self.assertIn("_doc", json.loads(self.models.read_text(encoding="utf-8")))


# ── PB-PACKAGE-MANIFESTS ──────────────────────────────────────────────────────
def manifest_problems(repo: Path) -> list:
    """What would make the manifests NOT identify the Playbook package: a
    missing/extra plugin entry, a source outside the repo or without its
    plugin.json, a name/description that disagrees, a version that is not the
    newest versioned CHANGELOG heading, or a hook command whose script is not
    in the package."""
    market = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    entries = market.get("plugins") or []
    if [e.get("name") for e in entries] != ["playbook"]:
        return [f"marketplace plugins are {[e.get('name') for e in entries]}, not ['playbook']"]
    entry = entries[0]
    problems = []
    source = (repo / entry.get("source", "")).resolve()
    if not source.is_relative_to(repo.resolve()):
        problems.append(f"source {entry.get('source')!r} leaves the repo")
    manifest = source / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return problems + [f"no plugin.json under source {entry.get('source')!r}"]
    plugin = json.loads(manifest.read_text(encoding="utf-8"))
    for key in ("name", "description"):
        if plugin.get(key) != entry.get(key):
            problems.append(f"{key}: plugin.json {plugin.get(key)!r} != marketplace {entry.get(key)!r}")
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\]", (repo / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    if not m or plugin.get("version") != m.group(1):
        problems.append(f"plugin.json version {plugin.get('version')!r} != newest CHANGELOG "
                        f"release {m.group(1) if m else None!r}")
    hooks = json.loads((source / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for matchers in hooks.get("hooks", {}).values():
        for matcher in matchers:
            for hook in matcher.get("hooks", []):
                command = hook.get("command", "")
                targets = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"\s]+)", command)
                if not targets:
                    problems.append(f"hook command {command!r} names no script under ${{CLAUDE_PLUGIN_ROOT}}")
                for target in targets:
                    path = (source / target).resolve()
                    if not path.is_relative_to(source.resolve()):
                        problems.append(f"hook command names {target}, outside the package")
                    elif not path.is_file():
                        problems.append(f"hook command names {target}, not in the package")
    return problems


class ManifestsIdentifyThePlaybookPackage(unittest.TestCase):
    def test_marketplace_entry_resolves_to_the_playbook_plugin_manifest(self):
        self.assertEqual(manifest_problems(REPO_ROOT), [])
        self.assertEqual(
            json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(
                encoding="utf-8"))["plugins"][0]["source"], "./plugins/playbook")

    def test_shipped_scripts_are_tracked_executable(self):
        # Every extensionless shebang script the package ships (hooks,
        # launchers, `tasks`, `sandbox`, `init`) is tracked 100755, so a
        # checkout or install from git runs it directly.
        try:
            listing = git(REPO_ROOT, "ls-files", "-s", "--", "plugins/playbook/scripts")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        modes = {}
        for line in listing.splitlines():
            meta, path = line.split("\t", 1)
            modes[path] = meta.split()[0]
        # walk the FILESYSTEM, not the index: an untracked script would ship
        # from a directory marketplace but be invisible to `git ls-files`
        # (impl panel r1, codex ×2)
        scripts = REPO_ROOT / "plugins" / "playbook" / "scripts"
        shebang = sorted(f.relative_to(REPO_ROOT).as_posix() for f in scripts.rglob("*")
                         if f.is_file() and "." not in f.name and "__pycache__" not in f.parts
                         and f.read_bytes()[:2] == b"#!")
        self.assertGreaterEqual(len(shebang), 15, shebang)
        self.assertEqual({p: modes.get(p, "untracked") for p in shebang
                          if modes.get(p) != "100755"}, {})
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        targets = {f"plugins/playbook/{m}" for m in re.findall(
            r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"\s]+)", json.dumps(hooks).replace('\\"', '"'))}
        self.assertTrue(targets)
        self.assertEqual({p for p in targets if p not in modes}, set(), "a hook script is untracked")

    def test_a_wrong_source_name_version_or_hook_is_reported(self):
        # Negative control: the same check on a copy with one thing wrong at a
        # time reports it.
        market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        market["plugins"][0]["source"] = "./plugins/playbook"   # the copy's layout, whatever the real file says
        cases = (
            (lambda m, c: m["plugins"][0].update(source="./plugins/other"), "no plugin.json"),
            (lambda m, c: m["plugins"][0].update(source="../outside"), "leaves the repo"),
            (lambda m, c: m["plugins"][0].update(name="other"), "not ['playbook']"),
            (lambda m, c: (c / "CHANGELOG.md").write_text("## [0.0.1] — x\n", encoding="utf-8"),
             "newest CHANGELOG release"),
            (lambda m, c: (c / "plugins/playbook/hooks/hooks.json").write_text(json.dumps(
                {"hooks": {"Stop": [{"hooks": [{"command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/gone-hook"'}]}]}}),
                encoding="utf-8"), "gone-hook, not in the package"),
            (lambda m, c: ((c / "outside-hook").write_text("#!/bin/sh\n", encoding="utf-8"),
                           (c / "plugins/playbook/hooks/hooks.json").write_text(json.dumps(
                {"hooks": {"Stop": [{"hooks": [{"command": 'bash "${CLAUDE_PLUGIN_ROOT}/../../outside-hook"'}]}]}}),
                encoding="utf-8")), "outside the package"),
        )
        for mutate, needle in cases:
            with self.subTest(needle=needle), tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "repo"
                (copy / ".claude-plugin").mkdir(parents=True)
                shutil.copytree(PLUGIN / ".claude-plugin", copy / "plugins/playbook/.claude-plugin")
                shutil.copytree(PLUGIN / "hooks", copy / "plugins/playbook/hooks")
                shutil.copy(REPO_ROOT / "CHANGELOG.md", copy / "CHANGELOG.md")
                bad = json.loads(json.dumps(market))
                mutate(bad, copy)
                (copy / ".claude-plugin" / "marketplace.json").write_text(json.dumps(bad), encoding="utf-8")
                self.assertTrue(any(needle in p for p in manifest_problems(copy)), manifest_problems(copy))


# ── PB-DOCUMENTATION-DRIFT ────────────────────────────────────────────────────
class ReadmeDriftSurfacesThroughBootstrap(unittest.TestCase):
    """End to end through `tasks bootstrap`: the CLI run from a source
    checkout (a git repo with README, the plugin manifest and the audit skill)
    whose covered surface changed after the audit baseline prints the drift
    note; the SAME files without `.git` — what an installed copy is — print
    nothing."""

    SKILL = Path(".claude/skills/readme-audit/SKILL.md")

    def _checkout(self, root: Path, with_git: bool) -> Path:
        plug = root / "plugins" / "playbook"
        shutil.copytree(PLUGIN / "tasks", plug / "tasks",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(PLUGIN / "provider", plug / "provider",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (plug / ".claude-plugin").mkdir()
        (plug / ".claude-plugin" / "plugin.json").write_text('{"name": "playbook", "version": "9.9.9"}',
                                                             encoding="utf-8")
        (plug / "commands").mkdir()
        (plug / "commands" / "init.md").write_text("x\n", encoding="utf-8")
        (root / "README.md").write_text("# readme\n", encoding="utf-8")
        (root / self.SKILL).parent.mkdir(parents=True)
        (root / self.SKILL).write_text("# skill\n", encoding="utf-8")
        if with_git:
            new_repo_in_place(root)
            git(root, "add", "-A")
            git(root, "commit", "-qm", "initial")
            sha = git(root, "rev-parse", "HEAD").strip()
        else:
            sha = "0" * 40
        baseline = root / BASELINE_REL
        covered = list(DEFAULT_COVERED_PATHS)
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(json.dumps({"audited_commit": sha, "version": "9.9.9",
                                        "date": "2026-01-01", "covered_paths": covered}),
                            encoding="utf-8")
        (plug / "commands" / "new-feature.md").write_text("y\n", encoding="utf-8")
        if with_git:
            git(root, "add", "-A")
            git(root, "commit", "-qm", "feat: new command")
        return plug

    def _bootstrap(self, plug: Path, tmp: Path) -> str:
        host = tmp / "host"
        (host / ".agent" / "tasks").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=str(plug), HOME=str(tmp / "home"),
                   PLAYBOOK_SESSION_ID="pid-s12-drift")
        r = subprocess.run([sys.executable, "-m", "tasks.cli", "bootstrap"], cwd=plug.parent.parent,
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_checkout_surfaces_drift_and_the_installed_copy_stays_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "home").mkdir()
            out = self._bootstrap(self._checkout(tmp / "checkout", with_git=True), tmp)
            self.assertRegex(out, r"NOTE: .*README", "the checkout did not surface drift")
            out = self._bootstrap(self._checkout(tmp / "copy", with_git=False), tmp)
            self.assertNotRegex(out, r"NOTE: .*README", "a copy without .git surfaced drift")


def new_repo_in_place(path: Path) -> None:
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "t")


# ── PB-SESSION-END-PRESERVE ───────────────────────────────────────────────────
class SessionEndKeepsTheDirForEveryNonTerminalReason(unittest.TestCase):
    """The preserve arm beyond `clear`: `resume`, an unknown reason and an
    unparseable payload keep the session directory; a terminal reason in any
    letter case removes it. The session id names the hook's exiting agent
    (this process, via the PLAYBOOK_PROC_ROOT fixture), so on a terminal reason
    the directory WOULD be removed — keeping it is the reason arm's doing."""

    def setUp(self):
        from tests._fake_agent import agent_proc_root
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        self.sid = f"pid-{os.getpid()}"
        self.sess = self.project / ".agent" / "sessions" / self.sid
        self.env = dict(os.environ, PLAYBOOK_SESSION_ID=self.sid,
                        PLAYBOOK_PROC_ROOT=agent_proc_root(self._tmp.name, os.getpid()))
        self.env.pop("BASH_ENV", None)

    def _end(self, payload: str):
        self.sess.mkdir(parents=True, exist_ok=True)
        (self.sess / "current_state").write_text("001\n", encoding="utf-8")
        subprocess.run([bash_or_skip(), str(SCRIPTS / "session-end-hook")], cwd=self.project,
                       env=self.env, text=True, input=payload, capture_output=True, timeout=60)
        return self.sess.exists()

    def test_resume_unknown_and_unparseable_keep_the_session_dir(self):
        for payload in (json.dumps({"reason": "resume"}), json.dumps({"reason": "Resume"}),
                        json.dumps({"reason": "something-new"}), json.dumps({}),
                        "not json at all", ""):
            with self.subTest(payload=payload):
                self.assertTrue(self._end(payload), f"{payload!r} removed the session dir")

    def test_terminal_reasons_remove_it_in_any_case(self):
        # Negative control: the same fixture DOES remove the directory on a
        # terminal reason, case-folded.
        for reason in ("logout", "LOGOUT", "Exit", "prompt_input_exit", "OTHER"):
            with self.subTest(reason=reason):
                self.assertFalse(self._end(json.dumps({"reason": reason})),
                                 f"{reason!r} kept the session dir")


# ── PB-TASK-CREATE-CHAT-CAPTURE ───────────────────────────────────────────────
class ActivationInjectsRecentChatIntoReferences(_Project):
    def test_tasks_work_puts_recent_messages_into_the_references_section(self):
        log = self.project / ".agent" / "chat_log.md"
        log.write_text(
            "# Chat Log\n\n---\n\n"
            "**[M001]** [2026-07-01 10:00:00 UTC] `HOST` (claude/pid-1)\n\nmodern suffixed message\n\n---\n\n"
            "**[M002]** [2026-07-01 10:05:00 UTC] `claude`\n\nlegacy unsuffixed message\n",
            encoding="utf-8")
        r = self.cli("new", "light", "capture-me")
        self.assertEqual(r.returncode, 0, r.stderr)
        task = next(self.project.glob(".agent/tasks/*capture-me/task.md"))
        self.assertNotIn("### Recent Chat", task.read_text(encoding="utf-8"),
                         "creation captured — the statement says activation")
        r = self.cli("work", task.parent.name.split("-")[0])
        self.assertEqual(r.returncode, 0, r.stderr)
        text = task.read_text(encoding="utf-8")
        refs = text[text.index("## References"):text.index("\n---\n", text.index("## References"))]
        self.assertIn("### Recent Chat", refs)
        self.assertIn("modern suffixed message", refs)
        self.assertIn("legacy unsuffixed message", refs)


# ── PB-DOCTOR-ADVISORY-NONBLOCKING ────────────────────────────────────────────
class DoctorAdvisoryFindingKeepsExitZero(_Project):
    def test_an_advisory_only_doctor_run_warns_and_exits_zero(self):
        (self.project / "CLAUDE.md").write_text("# proj\n", encoding="utf-8")
        (self.project / "MIND_MAP.md").write_text("# MIND_MAP\n", encoding="utf-8")
        healthy = self.cli("doctor")
        self.assertEqual(healthy.returncode, 0, healthy.stdout + healthy.stderr)
        (self.project / ".agent" / "config.json").write_text(
            json.dumps({"merge_verify": {}}), encoding="utf-8")
        r = self.cli("doctor")
        self.assertIn("SKIPPED", r.stdout, "the advisory finding was not printed")
        self.assertNotIn("FAIL", r.stdout)
        self.assertEqual(r.returncode, 0, "an advisory finding changed doctor's exit code")
