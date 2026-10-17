"""Judges read a fixed copy of the tree (task 231, PHASE-B item 7; the owner 2026-10-17: "Copie pe aceeași cale").

A review makes a copy of what git lists (every scope: the outer repo and each `code_roots` repo), seals it by
content — the copy's fingerprint equals the stamp, each scope's working tree object equals the snapshot's base (or the
post-panel delta's tree) and HEAD is the same — and bubblewrap mounts it read-only AT THE PROJECT PATH, so the agent
may keep writing the live tree while the judges read. Ignored entries are bound from the live tree (the judges see
them as before); `.agent`'s record masks are built from the live inventory and its other entries bound back from the
copy (tracked) or the live tree (ignored). No copy possible → the review reads the live tree, says so first, and the
old tamper guard holds.

Pure stdlib unittest; every case builds REAL git repos. The real-bwrap and end-to-end cases skip where no copy can be
mounted (no usable bwrap, or already inside a sandbox).
Run: python3 -m unittest tests.test_review_view
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))

from tasks import review as R  # noqa: E402
from tasks import core as C  # noqa: E402
from provider import sandbox as S  # noqa: E402


def _git(*args, cwd, check=True, **kw):
    return subprocess.run(["git", *args], cwd=str(cwd), check=check, capture_output=True, text=True, **kw)


def _init(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=d)
    _git("config", "user.email", "x@y.z", cwd=d)
    _git("config", "user.name", "x", cwd=d)
    return d


def _rv():
    """The module under test, imported per test so a missing module fails each case (red first), not the file."""
    from tasks import review_view
    return review_view


def _project(case: unittest.TestCase) -> Path:
    """A project shaped like a real one: dirty, staged, untracked, a link, a nested code root, ignored content,
    a tag and a second branch."""
    root = Path(tempfile.mkdtemp(prefix="rv-"))
    case.addCleanup(shutil.rmtree, root, True)
    d = _init(root / "proj")
    (d / ".gitignore").write_text(
        ".agent/journal/\n.agent/*/journal/\n.agent/models.json\n.agent/current_user\n.agent/sessions/\n"
        ".agent/chat_log.md\n.venv/\n__pycache__/\nsub/\n", encoding="utf-8")
    (d / "src").mkdir()
    (d / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    (d / "src" / "b.py").write_text("b = 1\n", encoding="utf-8")
    (d / "docs").mkdir()
    (d / "docs" / "x.md").write_text("doc\n", encoding="utf-8")
    os.symlink("src/a.py", d / "link.py")
    (d / ".agent" / "tasks" / "042-demo").mkdir(parents=True)
    (d / ".agent" / "tasks" / "042-demo" / "task.md").write_text(
        "# 042 - demo\n## Status\npending\n## Risk\nassertive\n## Intent\nx\n## Work Plan\n- [ ] a gate\n",
        encoding="utf-8")
    (d / ".agent" / "tasks" / "043-other").mkdir(parents=True)
    (d / ".agent" / "tasks" / "043-other" / "task.md").write_text("# 043 - other\n## Status\npending\n",
                                                                   encoding="utf-8")
    (d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["sub"]}), encoding="utf-8")
    _git("add", "-A", cwd=d)
    _git("commit", "-qm", "seed", cwd=d)
    _git("tag", "v1", cwd=d)
    _git("branch", "feature", cwd=d)
    # the states a review meets
    (d / "src" / "a.py").write_text("a = 2  # dirty\n", encoding="utf-8")
    (d / "src" / "b.py").write_text("b = 2  # staged\n", encoding="utf-8")
    _git("add", "src/b.py", cwd=d)
    (d / "new.txt").write_text("untracked\n", encoding="utf-8")
    (d / ".venv" / "bin").mkdir(parents=True)
    (d / ".venv" / "bin" / "python").write_text("LIVE-INTERPRETER\n", encoding="utf-8")
    (d / ".agent" / "models.json").write_text('{"panel": ["claude:opus"]}', encoding="utf-8")
    (d / ".agent" / "chat_log.md").write_text("SECRET CHAT\n", encoding="utf-8")
    (d / ".agent" / "journal").mkdir()
    (d / ".agent" / "journal" / "hooks.jsonl").write_text("{}\n", encoding="utf-8")
    s = _init(d / "sub")
    (s / "m.py").write_text("m = 1\n", encoding="utf-8")
    (s / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git("add", "-A", cwd=s)
    _git("commit", "-qm", "sub seed", cwd=s)
    (s / "m.py").write_text("m = 2  # dirty in the code root\n", encoding="utf-8")
    (s / "__pycache__").mkdir()
    (s / "__pycache__" / "m.cpython.pyc").write_bytes(b"\x00cache")
    return d


def _cfg(d: Path) -> dict:
    return C.load_config(d)


def _snapshot_trees(d: Path):
    fp = C.tree_state_fingerprint(d)
    snap = C.build_panel_snapshot(d, fp)
    scopes = snap["scopes"]
    return fp, {k: v["base"] for k, v in scopes.items()}, {k: v["commit"] for k, v in scopes.items()}


def _make(case, d, **kw):
    rv = _rv()
    fp, trees, heads = _snapshot_trees(d)
    view = rv.make(d, _cfg(d), run_id=kw.pop("run_id", "t1"), fingerprint=fp, trees=trees, heads=heads, **kw)
    case.addCleanup(view.remove)
    return view, fp


def _mountable() -> bool:
    try:
        ok, _why = S.view_mount_available()
        return bool(ok)
    except Exception:
        return False


class CopyAndSeal(unittest.TestCase):
    def test_the_copys_fingerprint_equals_the_live_one(self):
        d = _project(self)
        view, fp = _make(self, d)
        self.assertEqual(C.tree_state_fingerprint(view.root, cfg=_cfg(d)), fp)
        self.assertTrue(view.matches(fp, _cfg(d)))

    def test_the_copy_carries_refs_the_branch_the_index_and_the_status(self):
        d = _project(self)
        view, _fp = _make(self, d)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=view.root).stdout, _git("rev-parse", "HEAD", cwd=d).stdout)
        self.assertEqual(_git("symbolic-ref", "HEAD", cwd=view.root).stdout.strip(), "refs/heads/main")
        self.assertEqual(_git("tag", cwd=view.root).stdout.strip(), "v1")
        self.assertIn("feature", _git("branch", "--list", cwd=view.root).stdout)
        self.assertEqual(_git("status", "--porcelain", cwd=view.root).stdout,
                         _git("status", "--porcelain", cwd=d).stdout)
        self.assertEqual(_git("diff", "--cached", "--name-only", cwd=view.root).stdout.strip(), "src/b.py")
        self.assertEqual((view.root / "sub" / "m.py").read_text(encoding="utf-8"), "m = 2  # dirty in the code root\n")
        self.assertTrue(os.path.islink(view.root / "link.py"))

    def test_each_scopes_tree_in_the_copy_is_the_snapshots_base(self):
        d = _project(self)
        from tasks import post_d6
        fp, trees, _heads = _snapshot_trees(d)
        view = _rv().make(d, _cfg(d), run_id="t2", fingerprint=fp, trees=trees)
        self.addCleanup(view.remove)
        ex = [":(exclude).agent"]
        self.assertEqual(post_d6.worktree_tree(view.root, ex), trees[""])
        self.assertEqual(post_d6.worktree_tree(view.root / "sub", ex), trees["sub"])

    def test_a_base_from_another_state_is_refused_although_the_fingerprints_agree(self):
        """A→B→A (plan panel P4, codex): a base recorded at B while the stamp and the copy say A."""
        d = _project(self)
        rv = _rv()
        fp_a = C.tree_state_fingerprint(d)
        (d / "src" / "a.py").write_text("a = 3  # state B\n", encoding="utf-8")
        _fp_b, trees_b, _h = _snapshot_trees(d)
        (d / "src" / "a.py").write_text("a = 2  # dirty\n", encoding="utf-8")
        self.assertEqual(C.tree_state_fingerprint(d), fp_a)
        with self.assertRaises(rv.Moved):
            rv.make(d, _cfg(d), run_id="t3", fingerprint=fp_a, trees=trees_b)
        self.assertEqual(list(rv.views_dir(d).glob("t3*")), [], "a refused copy was left behind")

    def test_each_part_of_the_seal_refuses_on_its_own(self):
        """The fingerprint, the trees and HEAD each refuse a copy alone (mutants of each survived the end-to-end
        tests, where the parts agree)."""
        d = _project(self)
        rv = _rv()
        fp, trees, heads = _snapshot_trees(d)
        view = rv.make(d, _cfg(d), run_id="parts", fingerprint=fp, trees=trees, heads=heads)
        self.addCleanup(view.remove)
        ex = [":(exclude).agent"]
        self.assertTrue(view.sealed(fp, _cfg(d), trees, heads, ex))
        self.assertFalse(view.sealed("000000000000", _cfg(d), trees, heads, ex), "a wrong fingerprint passed")
        self.assertFalse(view.sealed(fp, _cfg(d), dict(trees, sub="0" * 40), heads, ex), "a wrong tree passed")
        self.assertFalse(view.sealed(fp, _cfg(d), trees, dict(heads, **{"": "0" * 40}), ex), "a wrong HEAD passed")

    def test_a_write_during_every_copy_attempt_is_refused_and_leaves_nothing(self):
        d = _project(self)
        rv = _rv()
        fp, trees, heads = _snapshot_trees(d)
        real = rv._copy_listed_files
        n = []

        def racing(*a, **kw):
            # the agent writes between the stamp and the copy, at every attempt
            n.append(1)
            (d / "src" / "a.py").write_text(f"a = {10 + len(n)}  # the agent wrote at once\n", encoding="utf-8")
            return real(*a, **kw)

        with mock.patch.object(rv, "_copy_listed_files", racing):
            with self.assertRaises(rv.Moved):
                rv.make(d, _cfg(d), run_id="t4", fingerprint=fp, trees=trees, heads=heads)
        self.assertEqual(len(n), rv.COPY_ATTEMPTS * 2, "each attempt copies both scopes")
        self.assertEqual(list(rv.views_dir(d).glob("t4*")), [])

    def test_ignored_entries_are_bound_live_and_their_mountpoints_exist(self):
        d = _project(self)
        view, _fp = _make(self, d)
        live = set(view.live_binds)
        self.assertIn(".venv", live)
        self.assertIn("sub/__pycache__", live)
        self.assertNotIn("sub", live, "a code root is copied, never bound live")
        self.assertFalse(any(x == ".agent" or x.startswith(".agent/") for x in live),
                         ".agent is the record masks' business")
        for rel in live:
            self.assertTrue(os.path.lexists(view.root / rel), f"no mountpoint for {rel}")
        self.assertFalse((view.root / ".venv" / "bin" / "python").exists(), "ignored content was copied")


class Refusals(unittest.TestCase):
    def _reason(self, d):
        return _rv().availability(d, _cfg(d))

    def test_a_copy_is_possible_for_the_fixture(self):
        d = _project(self)
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertEqual(self._reason(d), "")

    def test_the_views_folder_must_be_ignored(self):
        d = _project(self)
        text = (d / ".gitignore").read_text(encoding="utf-8").replace(".agent/journal/\n", "")
        (d / ".gitignore").write_text(text, encoding="utf-8")
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn("ignore", self._reason(d))

    def test_a_project_in_a_subdirectory_of_its_repo(self):
        d = _project(self)
        inner = d / "docs"
        (inner / ".agent").mkdir()
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn("subdirectory", _rv().availability(inner, {}))

    def test_a_linked_worktree(self):
        d = _project(self)
        wt = d.parent / "wt"
        _git("worktree", "add", "-q", str(wt), "feature", cwd=d)
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn("worktree", _rv().availability(wt, {}))

    def test_a_submodule_in_a_scope(self):
        d = _project(self)
        _git("update-index", "--add", "--cacheinfo", "160000,1111111111111111111111111111111111111111,mod", cwd=d)
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn("submodule", self._reason(d))

    def test_a_code_root_that_is_a_link(self):
        d = _project(self)
        other = _init(d.parent / "elsewhere")
        (other / "z.py").write_text("z\n", encoding="utf-8")
        _git("add", "-A", cwd=other)
        _git("commit", "-qm", "z", cwd=other)
        os.symlink(other, d / "linked")
        (d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["sub", "linked"]}), encoding="utf-8")
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn("linked", self._reason(d))

    def test_a_linked_agent_folder(self):
        d = _project(self)
        real = d.parent / "agent-real"
        shutil.move(str(d / ".agent"), str(real))
        os.symlink(real, d / ".agent")
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn(".agent", self._reason(d))

    def test_over_the_file_cap(self):
        d = _project(self)
        rv = _rv()
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")), \
             mock.patch.object(rv, "MAX_FILES", 3):
            self.assertIn("files", rv.availability(d, _cfg(d)))

    def test_an_exclude_that_hides_code(self):
        d = _project(self)
        (d / ".agent" / "config.json").write_text(
            json.dumps({"code_roots": ["sub"], "fingerprint_exclude": ["src/"]}), encoding="utf-8")
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn("fingerprint_exclude", self._reason(d))

    def test_no_mount_possible(self):
        d = _project(self)
        with mock.patch.object(S, "view_mount_available", return_value=(False, "already inside a sandbox")):
            self.assertIn("already inside a sandbox", self._reason(d))


class Lifetime(unittest.TestCase):
    def test_a_held_copy_survives_a_sweep_and_goes_once_released(self):
        d = _project(self)
        rv = _rv()
        fp, trees, heads = _snapshot_trees(d)
        view = rv.make(d, _cfg(d), run_id="held", fingerprint=fp, trees=trees, heads=heads)
        self.assertEqual(rv.sweep(d), 0)
        self.assertTrue(view.root.is_dir())
        view.remove()
        self.assertFalse(view.root.exists())

    def test_a_copy_whose_owner_died_is_swept(self):
        d = _project(self)
        rv = _rv()
        fp, trees, heads = _snapshot_trees(d)
        view = rv.make(d, _cfg(d), run_id="dead", fingerprint=fp, trees=trees, heads=heads)
        os.close(view.lock_fd)          # what a killed owner leaves: the folder, no held lock
        view.lock_fd = -1
        self.assertEqual(rv.sweep(d), 1)
        self.assertFalse(view.root.exists())

    def test_remove_never_leaves_the_views_folder(self):
        d = _project(self)
        rv = _rv()
        outside = d / "src"
        with self.assertRaises(ValueError):
            rv._remove_tree(d, outside)
        self.assertTrue((outside / "a.py").exists())


class FingerprintTakesTheLiveConfig(unittest.TestCase):
    def test_cfg_parameter(self):
        d = _project(self)
        view, fp = _make(self, d)
        os.chmod(view.root / ".agent", 0o700)                # the sealed copy is read-only on disk (round 2)
        (view.root / ".agent" / "config.json").unlink()      # as if the config were ignored, bound live
        self.assertEqual(C.tree_state_fingerprint(view.root, cfg=_cfg(d)), fp)
        self.assertNotEqual(C.tree_state_fingerprint(view.root), fp)


class SandboxArgv(unittest.TestCase):
    def _argv(self, d, view, **kw):
        return S.build_bwrap_argv(d, None, ["true"], None, project_writable=False,
                                  project_view=view.sandbox_view(), bwrap_exe="/usr/bin/bwrap", **kw)

    def _pairs(self, argv, flag):
        return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == flag and i + 2 < len(argv)]

    def test_the_copy_replaces_the_project_bind(self):
        d = _project(self)
        view, _fp = _make(self, d)
        argv = self._argv(d, view)
        binds = self._pairs(argv, "--ro-bind")
        self.assertIn((str(view.root), str(d.resolve())), binds)
        self.assertNotIn((str(d.resolve()), str(d.resolve())), binds)

    def test_agent_entries_come_from_the_copy_when_tracked_and_live_when_ignored(self):
        d = _project(self)
        view, _fp = _make(self, d)
        argv = self._argv(d, view)
        binds = self._pairs(argv, "--ro-bind")
        p = str(d.resolve())
        # `.agent`'s top-level folders are layers of their own (task 138): their entries are bound one by one
        self.assertIn((str(view.root / ".agent" / "tasks" / "042-demo"), p + "/.agent/tasks/042-demo"), binds)
        self.assertIn((str(view.root / ".agent" / "config.json"), p + "/.agent/config.json"), binds)
        self.assertIn((p + "/.agent/journal/hooks.jsonl", p + "/.agent/journal/hooks.jsonl"), binds)
        self.assertIn((p + "/.agent/models.json", p + "/.agent/models.json"), binds)
        self.assertIn(("/dev/null", p + "/.agent/chat_log.md"), binds, "a record is masked as before")
        self.assertNotIn((p + "/.agent/tasks/042-demo", p + "/.agent/tasks/042-demo"), binds,
                         "the live task records came back")

    def test_ignored_entries_are_bound_live_after_the_copy(self):
        d = _project(self)
        view, _fp = _make(self, d)
        argv = self._argv(d, view)
        p = str(d.resolve())
        i_copy = argv.index(str(view.root))
        i_venv = [i for i, a in enumerate(argv) if a == p + "/.venv"]
        self.assertTrue(i_venv and min(i_venv) > i_copy)

    def test_a_copy_with_a_writable_project_is_refused(self):
        d = _project(self)
        view, _fp = _make(self, d)
        with self.assertRaises(ValueError):
            S.build_bwrap_argv(d, None, ["true"], None, project_writable=True,
                               project_view=view.sandbox_view(), bwrap_exe="/usr/bin/bwrap")

    def test_a_copy_inside_a_sandbox_is_refused_not_read_live(self):
        d = _project(self)
        view, _fp = _make(self, d)
        with mock.patch.object(S, "is_sandboxed", return_value=True):
            with self.assertRaises(RuntimeError):
                S._launch_plan("claude", ["-p"], d.resolve(), None, False,
                               project_view=view.sandbox_view())

    def test_run_hands_the_copy_to_the_launch_plan(self):
        seen = {}

        def plan(*a, **kw):
            seen.update(kw)
            return ["true"], True

        with mock.patch.object(S, "_launch_plan", plan):
            S.run("bash", ["-c", "true"], project_root=tempfile.gettempdir(), project_writable=False,
                  project_view=S.ProjectView(root="/x", live=()), capture_output=True)
        self.assertEqual(seen.get("project_view"), S.ProjectView(root="/x", live=()))

    def test_every_adapter_forwards_the_copy_to_the_sandbox(self):
        from provider.adapters.claude import ClaudeAdapter
        from provider.adapters.codex import CodexAdapter
        from provider.adapters.grok import GrokAdapter
        from provider.adapters.antigravity import AntigravityAdapter
        from provider.adapters.pi import PiAdapter
        pv = S.ProjectView(root="/x", live=())
        for cls in (ClaudeAdapter, CodexAdapter, GrokAdapter, AntigravityAdapter, PiAdapter):
            seen = {}

            def fake_run(*a, **kw):
                seen.update(kw)
                return subprocess.CompletedProcess(a, 0, stdout="ok", stderr="")

            with mock.patch.object(S, "run", fake_run), \
                 mock.patch("shutil.which", return_value="/usr/bin/true"):
                try:
                    cls(session_id="judge", project_root=tempfile.gettempdir()).run_headless_judge(
                        "p", None, "", web_search=False, timeout_secs=5, budget_usd="1", project_view=pv)
                except TypeError as e:
                    self.fail(f"{cls.__name__}.run_headless_judge does not take project_view: {e}")
                except Exception:
                    pass        # the faked output may not parse — only the call matters
            self.assertEqual(seen.get("project_view"), pv, cls.__name__)


@unittest.skipUnless(_mountable(), "no copy can be mounted here (no usable bubblewrap, or already sandboxed)")
class RealBubblewrap(unittest.TestCase):
    def test_the_judge_reads_the_copy_live_ignored_files_and_writes_nothing(self):
        d = _project(self)
        view, _fp = _make(self, d)
        (d / "src" / "a.py").write_text("a = 99  # written live after the copy\n", encoding="utf-8")
        (d / ".agent" / "tasks" / "042-demo" / "task.md").write_text("LIVE TRIAGE EDIT\n", encoding="utf-8")
        cmd = ("cat src/a.py; cat .agent/tasks/042-demo/task.md | head -1; cat .venv/bin/python; "
               "cat .agent/models.json; echo; cat .agent/chat_log.md 2>&1 | head -1; "
               "echo x > src/new.py 2>&1 || echo WRITE-REFUSED; git -C . rev-parse --abbrev-ref HEAD")
        with mock.patch.object(S, "_compose_agent_argv", lambda agent, args: [agent, *args]):
            argv, contained = S._launch_plan("bash", ["-c", cmd], d.resolve(), None, False,
                                             project_view=view.sandbox_view())
        self.assertTrue(contained)
        out = subprocess.run(argv, cwd=str(d), capture_output=True, text=True, timeout=60)
        text = out.stdout + out.stderr
        self.assertIn("a = 2  # dirty", text)
        self.assertNotIn("a = 99", text)
        self.assertIn("# 042 - demo", text)
        self.assertNotIn("LIVE TRIAGE EDIT", text)
        self.assertIn("LIVE-INTERPRETER", text)
        self.assertIn('"panel"', text)
        self.assertNotIn("SECRET CHAT", text)
        self.assertIn("WRITE-REFUSED", text)
        self.assertIn("main", text)
        self.assertFalse((d / "src" / "new.py").exists())


class ImplRoundOne(unittest.TestCase):
    """Task 231's impl panel round 1: the holes its judges found (G1-G7 and the minors), red first."""

    def test_g1_a_dotted_code_root_is_copied_never_bound_live(self):
        d = _project(self)
        (d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["./sub"]}), encoding="utf-8")
        view, _fp = _make(self, d)
        self.assertFalse(any(x in ("sub", "./sub") or x.startswith(("sub/", "./sub/")) and "__pycache__" not in x
                             for x in view.live_binds), view.live_binds)

    def test_g2_records_are_not_copied_and_the_copies_folder_is_hidden(self):
        d = _project(self)
        text = (d / ".gitignore").read_text(encoding="utf-8").replace(".agent/chat_log.md\n", "")
        (d / ".gitignore").write_text(text, encoding="utf-8")
        view, _fp = _make(self, d)
        self.assertFalse((view.root / ".agent" / "chat_log.md").exists(), "a record was copied")
        self.assertIn(str(_rv().views_dir(d).resolve()), view.sandbox_view().hide)

    def test_g5_a_live_only_agent_entry_is_shown_only_when_ignored(self):
        d = _project(self)
        view, _fp = _make(self, d)
        (d / ".agent" / "tasks" / "999-new").mkdir()
        (d / ".agent" / "tasks" / "999-new" / "task.md").write_text("NEW TASK\n", encoding="utf-8")
        argv = S.build_bwrap_argv(d, None, ["true"], None, project_writable=False,
                                  project_view=view.sandbox_view(), bwrap_exe="/usr/bin/bwrap")
        p = str(d.resolve())
        self.assertNotIn(p + "/.agent/tasks/999-new", argv, "a live, unignored .agent entry was bound")
        self.assertIn(p + "/.agent/journal/hooks.jsonl", argv, "an ignored live entry was lost")

    def test_g6_a_failure_during_the_copy_is_retried_then_refused_never_live(self):
        d = _project(self)
        rv = _rv()
        fp, trees, heads = _snapshot_trees(d)
        real, n = rv._clone_scope, []

        def failing(*a, **kw):
            n.append(1)
            raise subprocess.CalledProcessError(1, ["git", "update-ref"], "", "fatal: bad object")

        with mock.patch.object(rv, "_clone_scope", failing):
            with self.assertRaises(rv.Moved):
                rv.make(d, _cfg(d), run_id="g6", fingerprint=fp, trees=trees, heads=heads)
        self.assertEqual(len(n), rv.COPY_ATTEMPTS)
        self.assertEqual(list(rv.views_dir(d).glob("g6*")), [])

    def test_g7_an_ignored_folder_holding_a_code_root_keeps_its_ignored_siblings(self):
        d = _project(self)
        with open(d / ".gitignore", "a", encoding="utf-8") as f:
            f.write("deps/\n")
        app = _init(d / "deps" / "app")
        (app / "x.py").write_text("x\n", encoding="utf-8")
        _git("add", "-A", cwd=app)
        _git("commit", "-qm", "app", cwd=app)
        (d / "deps" / "tool").mkdir()
        (d / "deps" / "tool" / "python").write_text("TOOL\n", encoding="utf-8")
        (d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["sub", "deps/app"]}), encoding="utf-8")
        view, _fp = _make(self, d)
        self.assertIn("deps/tool", view.live_binds)
        self.assertNotIn("deps", view.live_binds)
        self.assertFalse(any(x == "deps/app" or x.startswith("deps/app/") and "__pycache__" not in x
                             for x in view.live_binds))

    def test_a_scope_set_other_than_the_trees_refuses_the_seal(self):
        d = _project(self)
        rv = _rv()
        fp, trees, heads = _snapshot_trees(d)
        view = rv.make(d, _cfg(d), run_id="scopes", fingerprint=fp, trees=trees, heads=heads)
        self.addCleanup(view.remove)
        only_outer = {"": trees[""]}
        self.assertFalse(view.sealed(fp, _cfg(d), only_outer, None, [":(exclude).agent"]))

    def test_an_ignored_task_record_refuses_the_copy(self):
        d = _project(self)
        with open(d / ".gitignore", "a", encoding="utf-8") as f:
            f.write(".agent/tasks/\n")
        tf = d / ".agent" / "tasks" / "042-demo" / "task.md"
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn("task", _rv().availability(d, _cfg(d), tf))

    def test_a_copy_entry_that_is_a_file_is_bound_even_if_live_became_a_link(self):
        d = _project(self)
        view, _fp = _make(self, d)
        cfgp = d / ".agent" / "config.json"
        cfgp.unlink()
        os.symlink("/etc/hostname", cfgp)
        argv = S.build_bwrap_argv(d, None, ["true"], None, project_writable=False,
                                  project_view=view.sandbox_view(), bwrap_exe="/usr/bin/bwrap")
        p = str(d.resolve())
        self.assertIn(p + "/.agent/config.json", argv, "the entry was dropped")
        i = argv.index(p + "/.agent/config.json")
        self.assertEqual(argv[i - 1], str(view.root / ".agent" / "config.json"))

    def test_a_read_only_folder_in_the_copy_is_removed(self):
        d = _project(self)
        rv = _rv()
        fp, trees, heads = _snapshot_trees(d)
        view = rv.make(d, _cfg(d), run_id="ro", fingerprint=fp, trees=trees, heads=heads)
        os.chmod(view.root / "docs", 0o555)
        view.remove()
        self.assertFalse(view.root.exists())

    def test_the_lock_is_retaken_when_its_file_was_replaced_before_the_flock(self):
        d = _project(self)
        rv = _rv()
        vd = rv.views_dir(d)
        vd.mkdir(parents=True, exist_ok=True)
        path = vd / "race.lock"
        real_flock = rv.fcntl.flock
        swapped = []

        def flock(fd, op):
            if not swapped:
                swapped.append(1)
                os.unlink(path)
                path.write_text("", encoding="utf-8")       # another process's sweep replaced it
            return real_flock(fd, op)

        with mock.patch.object(rv.fcntl, "flock", flock):
            fd = rv._lock(path)
        self.addCleanup(os.close, fd)
        self.assertEqual(os.fstat(fd).st_ino, os.stat(path).st_ino, "the lock held is not the file at the path")


@unittest.skipUnless(_mountable(), "no copy can be mounted here")
class ImplRoundOneRealBubblewrap(unittest.TestCase):
    def _run(self, d, view, cmd):
        with mock.patch.object(S, "_compose_agent_argv", lambda agent, args: [agent, *args]):
            argv, _c = S._launch_plan("bash", ["-c", cmd], d.resolve(), None, False,
                                      project_view=view.sandbox_view())
        out = subprocess.run(argv, cwd=str(d), capture_output=True, text=True, timeout=60)
        return out.stdout + out.stderr

    def test_g1_the_judge_reads_the_dotted_code_roots_copy(self):
        d = _project(self)
        (d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["./sub"]}), encoding="utf-8")
        view, _fp = _make(self, d)
        (d / "sub" / "m.py").write_text("m = 99  # live after the copy\n", encoding="utf-8")
        text = self._run(d, view, "cat sub/m.py")
        self.assertIn("m = 2  # dirty in the code root", text)
        self.assertNotIn("m = 99", text)

    def test_g2_no_alias_of_the_copies_inside_the_sandbox(self):
        d = _project(self)
        text = (d / ".gitignore").read_text(encoding="utf-8").replace(".agent/chat_log.md\n", "")
        (d / ".gitignore").write_text(text, encoding="utf-8")
        view, _fp = _make(self, d)
        out = self._run(d, view, "ls -A .agent/journal/review-views 2>&1; "
                                 "cat .agent/journal/review-views/*/.agent/chat_log.md 2>&1 | head -1")
        self.assertNotIn("SECRET CHAT", out)
        self.assertNotIn(view.root.name, out, "the copies folder is visible")

    def test_g3_host_processes_are_not_reachable(self):
        d = _project(self)
        view, _fp = _make(self, d)
        out = self._run(d, view, f"test -d /proc/{os.getpid()} && echo HOST-PID-VISIBLE || echo HIDDEN")
        self.assertIn("HIDDEN", out)

    def test_g5_a_task_made_after_the_seal_is_not_visible(self):
        d = _project(self)
        view, _fp = _make(self, d)
        (d / ".agent" / "tasks" / "999-new").mkdir()
        (d / ".agent" / "tasks" / "999-new" / "task.md").write_text("NEW TASK\n", encoding="utf-8")
        out = self._run(d, view, "cat .agent/tasks/999-new/task.md 2>&1 | head -1")
        self.assertNotIn("NEW TASK", out)


class ImplRoundTwo(unittest.TestCase):
    """Task 231's impl panel round 2, red first."""

    def test_r2_1_the_sealed_copy_is_read_only_on_disk(self):
        d = _project(self)
        view, _fp = _make(self, d)
        with self.assertRaises(PermissionError):
            (view.root / "src" / "a.py").write_text("x\n", encoding="utf-8")
        with self.assertRaises(PermissionError):
            (view.root / "src" / "planted.py").write_text("x\n", encoding="utf-8")

    def test_r2_3_an_unborn_head_or_an_unmerged_index_reads_live(self):
        root = Path(tempfile.mkdtemp(prefix="rv-unborn-"))
        self.addCleanup(shutil.rmtree, root, True)
        d = _init(root / "p")
        (d / ".gitignore").write_text(".agent/journal/\n", encoding="utf-8")
        (d / ".agent").mkdir()
        (d / "a.py").write_text("a\n", encoding="utf-8")
        with mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn("commit", _rv().availability(d, {}))

    def test_r2_4_an_ignored_entry_over_a_code_root_reads_live(self):
        d = _project(self)
        with open(d / ".gitignore", "a", encoding="utf-8") as f:
            f.write("deps/app/\n")
        app = _init(d / "deps" / "app")
        (app / "x.py").write_text("x\n", encoding="utf-8")
        _git("add", "-A", cwd=app)
        _git("commit", "-qm", "app", cwd=app)
        (d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["sub", "deps/app"]}), encoding="utf-8")
        rv = _rv()
        with mock.patch.object(rv, "ignored_entries", return_value=["deps"]), \
             mock.patch.object(S, "view_mount_available", return_value=(True, "")):
            self.assertIn("overlap", rv.availability(d, _cfg(d)))

    def test_r2_5_a_linked_project_path_keeps_its_live_binds(self):
        d = _project(self)
        link = d.parent / "linked-proj"
        os.symlink(d, link)
        view, _fp = _make(self, link)
        pv = view.sandbox_view()
        self.assertTrue(all(x.startswith(str(d.resolve())) for x in pv.live), pv.live)
        argv = S.build_bwrap_argv(link, None, ["true"], None, project_writable=False, project_view=pv,
                                  bwrap_exe="/usr/bin/bwrap")
        self.assertIn(str(d.resolve()) + "/.venv", argv)

    def test_r2_6_a_folder_removed_live_still_shows_the_copys_content(self):
        d = _project(self)
        view, _fp = _make(self, d)
        shutil.rmtree(d / ".agent" / "tasks")
        argv = S.build_bwrap_argv(d, None, ["true"], None, project_writable=False,
                                  project_view=view.sandbox_view(), bwrap_exe="/usr/bin/bwrap")
        self.assertIn(str(view.root / ".agent" / "tasks" / "042-demo"), argv)

    def test_r2_7_a_snapshot_whose_dirty_map_is_not_the_copys_is_refused(self):
        d = _project(self)
        rv = _rv()
        fp = C.tree_state_fingerprint(d)
        snap = C.build_panel_snapshot(d, fp)
        trees = {k: v["base"] for k, v in snap["scopes"].items()}
        heads = {k: v["commit"] for k, v in snap["scopes"].items()}
        forged = json.loads(json.dumps(snap))
        dirty = forged["scopes"][""]["dirty"]
        k = sorted(dirty)[0]
        dirty[k] = "0" * len(str(dirty[k]))          # a token captured at another state (A→B→A)
        with self.assertRaises(rv.Moved):
            rv.make(d, _cfg(d), run_id="r27", fingerprint=fp, trees=trees, heads=heads, snapshot=forged)

    def test_r2_9_the_projects_parents_under_a_writable_place_are_pinned(self):
        d = _project(self)
        view, _fp = _make(self, d)
        argv = S.build_bwrap_argv(d, None, ["true"], None, project_writable=False,
                                  project_view=view.sandbox_view(), bwrap_exe="/usr/bin/bwrap")
        parent = str(d.resolve().parent)
        pairs = [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == "--bind" and i + 2 < len(argv)]
        self.assertIn((parent, parent), pairs, "the folder holding the live project can be renamed from inside")

    def test_r2_10_each_attempt_has_its_own_name_and_the_lock_goes_before_its_fd(self):
        d = _project(self)
        rv = _rv()
        fp, trees, heads = _snapshot_trees(d)
        view = rv.make(d, _cfg(d), run_id="r210-a1", fingerprint=fp, trees=trees, heads=heads, attempts=1)
        seen = []
        real_close = os.close

        def close(fd):
            if fd == view.lock_fd:
                seen.append(view.lock_path.exists())
            return real_close(fd)

        with mock.patch.object(rv.os, "close", close):
            view.remove()
        self.assertEqual(seen, [False], "the lock file outlived its fd")

    def test_r2_minor_every_lanes_copies_folder_is_hidden(self):
        d = _project(self)
        other = d / ".agent" / "bob" / "journal" / "review-views"
        other.mkdir(parents=True)
        view, _fp = _make(self, d)
        self.assertIn(str(other.resolve()), view.sandbox_view().hide)


@unittest.skipUnless(_mountable(), "no copy can be mounted here")
class ImplRoundTwoRealBubblewrap(unittest.TestCase):
    def test_r2_9_the_folder_holding_the_project_cannot_be_moved(self):
        d = _project(self)
        view, _fp = _make(self, d)
        parent = d.resolve().parent
        cmd = f"mv {parent} {parent}.moved 2>&1 && echo MOVED || echo REFUSED"
        with mock.patch.object(S, "_compose_agent_argv", lambda agent, args: [agent, *args]):
            argv, _c = S._launch_plan("bash", ["-c", cmd], d.resolve(), None, False,
                                      project_view=view.sandbox_view())
        out = subprocess.run(argv, cwd=str(d), capture_output=True, text=True, timeout=60)
        if Path(str(parent) + ".moved").exists():
            os.rename(str(parent) + ".moved", parent)
        self.assertIn("REFUSED", out.stdout + out.stderr)


class _PanelHarness:
    def _models(self, d):
        (d / ".agent" / "models.json").write_text(
            '{"panel": ["claude:opus", "claude:sonnet"], "default_judge": "claude:opus"}', encoding="utf-8")

    def _run(self, d, judge, argv, *, view_ok=True):
        from provider.adapters.claude import ClaudeAdapter
        rv = _rv()
        old = os.getcwd()
        out = io.StringIO()
        patches = [mock.patch.object(ClaudeAdapter, "is_available", classmethod(lambda cls: True)),
                   mock.patch.object(ClaudeAdapter, "run_headless_judge", judge)]
        if not view_ok:
            patches.append(mock.patch.object(rv, "availability", lambda *a, **kw: "a test reason"))
        for p in patches:
            p.start()
        R._PB_JOURNAL_MOD = None
        R._PB_JOURNAL_LOADED = False
        os.chdir(d)
        try:
            with contextlib.suppress(SystemExit), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                R.cmd_panel_review(argv)
        finally:
            os.chdir(old)
            for p in reversed(patches):
                p.stop()
        return out.getvalue()


@unittest.skipUnless(_mountable(), "no copy can be mounted here")
class PanelReadsACopy(unittest.TestCase, _PanelHarness):
    def setUp(self):
        self.d = _project(self)
        self._models(self.d)

    def test_live_writes_during_the_panel_keep_the_seal(self):
        d = self.d
        fp0 = C.tree_state_fingerprint(d)
        head0 = _git("rev-parse", "HEAD", cwd=d).stdout.strip()
        seen, once = [], threading.Lock()

        def judge(adapter, *a, **kw):
            pv = kw.get("project_view")
            seen.append(pv)
            with once:
                if len(seen) == 1:
                    (d / "src" / "a.py").write_text("a = 7  # the agent kept working\n", encoding="utf-8")
                    _git("commit", "-qam", "next task", cwd=d)
                    (d / ".agent" / "tasks" / "043-other" / "task.md").write_text("edited meanwhile\n",
                                                                                   encoding="utf-8")
            root = Path(pv.root)
            assert (root / "src" / "a.py").read_text(encoding="utf-8") == "a = 2  # dirty\n"
            return "1. **Note** — fine.\n"

        log = self._run(d, judge, ["042", "--mode", "impl", "--models", "claude:opus,claude:sonnet"])
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(pv is not None for pv in seen), "a seat ran on the live tree")
        self.assertIn("ready", log.lower())
        text = (d / ".agent" / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8")
        self.assertIn(f"**Tree-state:** {fp0}", text)
        self.assertIn("**Panel-snapshot:**", text)
        self.assertIn(f"**Commit:** {head0}", text)
        self.assertNotIn("Tree moved during review", text)
        self.assertIn("**Tamper guard:** clean", text)
        self.assertEqual([p for p in _rv().views_dir(d).iterdir() if p.is_dir()], [], "the copy was left behind")

    def test_a_copy_that_changed_under_the_judges_voids_the_round(self):
        d = self.d
        rv = _rv()

        def judge(adapter, *a, **kw):
            return "1. **Note** — fine.\n"

        real = rv.View.matches
        calls = []

        def matches(self_, fp, cfg):
            calls.append(1)
            return real(self_, fp, cfg) if len(calls) == 1 else False

        with mock.patch.object(rv.View, "matches", matches):
            log = self._run(d, judge, ["042", "--mode", "impl", "--models", "claude:opus"])
        self.assertFalse((d / ".agent" / "tasks" / "042-demo" / "judge.md").exists(), "a round of a changed copy was saved")
        self.assertIn("copy", log.lower())

    def test_critical_the_quorum_and_config_are_read_before_the_ready_line(self):
        d = self.d

        def judge(adapter, *a, **kw):
            (d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["sub"], "panel_quorum": 1}),
                                                      encoding="utf-8")
            return "(error: seat down)" if kw.get("model") == "sonnet" else "1. **Note** — fine.\n"

        log = self._run(d, judge, ["042", "--mode", "impl", "--models", "claude:opus,claude:sonnet"])
        text = (d / ".agent" / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8")
        self.assertIn("**PANEL VERDICT: FAIL**", text, "a config written after the ready line decided the verdict")

    def test_r2_2_a_seal_that_fails_without_movement_reads_live_not_refused(self):
        d = self.d
        rv = _rv()
        seen = []

        def judge(adapter, *a, **kw):
            seen.append(kw.get("project_view"))
            return "1. **Note** — fine.\n"

        with mock.patch.object(rv.View, "sealed", lambda *a, **kw: False):
            log = self._run(d, judge, ["042", "--mode", "impl", "--models", "claude:opus"])
        self.assertEqual(seen, [None], "a structural seal failure refused the review (or still used a copy)")
        self.assertIn("LIVE tree", log)
        self.assertNotIn("Refused", log)

    def test_the_ready_line_comes_after_every_refusal(self):
        d = self.d
        log = self._run(d, lambda adapter, *a, **kw: "1. **Note** — fine.\n",
                        ["042", "--mode", "impl", "--models", "claude:opus,claude:sonnet,claude:haiku-x"])
        lines = log.splitlines()
        ready = [i for i, l in enumerate(lines) if "ready: you may write" in l]
        spawn = [i for i, l in enumerate(lines) if "] done" in l]
        if ready and spawn:
            self.assertLess(max(ready), min(spawn))
        self.assertTrue(ready, log[-1500:])
        self.assertIn("ignored files stay live", lines[ready[0]])

    def test_a_records_edit_during_the_panel_leaves_the_close_fresh(self):
        d = self.d

        def judge(adapter, *a, **kw):
            (d / ".agent" / "tasks" / "043-other" / "task.md").write_text("notes\n", encoding="utf-8")
            return "1. **Note** — fine.\n"

        self._run(d, judge, ["042", "--mode", "impl", "--models", "claude:opus"])
        text = (d / ".agent" / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8")
        stamp = text.split("**Tree-state:** ", 1)[1].split()[0]
        self.assertEqual(C.tree_state_fingerprint(d), stamp)

    def test_a_docs_edit_is_certifiable_and_a_code_edit_is_not(self):
        d = self.d

        def judge(adapter, *a, **kw):
            (d / "docs" / "x.md").write_text("doc changed meanwhile\n", encoding="utf-8")
            return "1. **Note** — fine.\n"

        self._run(d, judge, ["042", "--mode", "impl", "--models", "claude:opus"])
        rounds = C.parse_judge_rounds((d / ".agent" / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8"))
        snap = rounds[0]["snapshot"]
        self.assertIsNotNone(snap, "no Panel-snapshot — tail certification is gone")
        can, beh, non, _why = C.tail_cert_delta_why(d, snap, rounds[0]["tree_state"])
        self.assertTrue(can)
        self.assertEqual(beh, [])
        self.assertIn("docs/x.md", " ".join(non), "the snapshot was taken after the edit, not before the spawn")
        (d / "src" / "a.py").write_text("a = 8  # code\n", encoding="utf-8")
        can, beh, non, _why = C.tail_cert_delta_why(d, snap, rounds[0]["tree_state"])
        self.assertTrue(beh)


@unittest.skipUnless(_mountable(), "no copy can be mounted here")
class SingleJudgeReadsACopy(unittest.TestCase, _PanelHarness):
    """The post-panel judge: its copy is the delta's tree (plan panel P3), a live write while it runs voids nothing,
    and its `[file:]` checks resolve in the copy (P16)."""

    def setUp(self):
        self.d = _project(self)
        self._models(self.d)
        with open(self.d / ".gitignore", "a", encoding="utf-8") as f:
            f.write("alias.py\n")
        os.symlink("docs/x.md", self.d / "alias.py")         # ignored: copied as a link, frozen
        self._run(self.d, lambda adapter, *a, **kw: "1. **Note** — fine.\n",
                  ["042", "--mode", "impl", "--models", "claude:opus"])
        text = (self.d / ".agent" / "tasks" / "042-demo" / "judge.md").read_text(encoding="utf-8")
        self.assertIn("**Panel-snapshot:**", text, "the panel left no snapshot")
        (self.d / "src" / "a.py").write_text("a = 5  # after the panel\n", encoding="utf-8")    # the delta

    def _single(self, judge_text, during):
        from tasks import cli as tcli
        seen = []

        def fake_run(agent, agent_args, **kw):
            pv = kw.get("project_view")
            seen.append(pv)
            during()
            if pv is not None:
                seen.append((Path(pv.root) / "src" / "a.py").read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=judge_text, stderr="")

        out = io.StringIO()
        old = os.getcwd()
        os.chdir(self.d)
        try:
            with mock.patch.object(sys, "argv", ["tasks", "impl-review", "042", "--backend", "claude"]), \
                 mock.patch("shutil.which", return_value="/usr/bin/claude"), \
                 mock.patch("provider.sandbox.run", side_effect=fake_run), \
                 mock.patch("provider.sandbox.format_judge_output", side_effect=lambda r: (r.stdout or "")), \
                 contextlib.suppress(SystemExit), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                tcli.main()
        finally:
            os.chdir(old)
        return seen, out.getvalue()

    def test_the_post_panel_judge_reads_the_deltas_tree_while_the_agent_writes(self):
        d = self.d

        def during():
            (d / "src" / "a.py").write_text("a = 6  # written while the judge ran\n", encoding="utf-8")

        seen, log = self._single("1. **Note** — fine.\n\nCAP: 0/5 reported, exhausted\n", during)
        self.assertTrue(seen and seen[0] is not None, "the post-panel judge read the live tree\n" + log[-3000:])
        self.assertEqual(seen[1], "a = 5  # after the panel\n")
        self.assertIn("POST-D6 VERDICT: PASS", log)
        self.assertIn("Its PASS binds the tree it reads", log)
        self.assertNotIn("TAMPER", log.upper().replace("TAMPER GUARD: CLEAN", ""))
        self.assertEqual([p for p in _rv().views_dir(d).iterdir() if p.is_dir()], [], "the copy was left behind")

    def test_a_write_between_the_delta_and_the_copy_refuses_the_run(self):
        from tasks import post_d6
        d = self.d
        real = post_d6.delta_parts

        def then_write(*a, **kw):
            out = real(*a, **kw)
            (d / "src" / "a.py").write_text("a = 9  # after the delta was built\n", encoding="utf-8")
            return out

        with mock.patch.object(post_d6, "delta_parts", then_write):
            seen, log = self._single("1. **Note** — fine.\n\nCAP: 0/5 reported, exhausted\n", lambda: None)
        self.assertEqual(seen, [], "a judge ran on a copy that is not the delta's tree")
        self.assertIn("Refused", log)

    def test_g4_a_code_root_added_after_the_delta_refuses_the_run(self):
        from tasks import post_d6
        d = self.d
        other = _init(d / "extra")
        (other / "e.py").write_text("e\n", encoding="utf-8")
        _git("add", "-A", cwd=other)
        _git("commit", "-qm", "e", cwd=other)
        with open(d / ".gitignore", "a", encoding="utf-8") as f:
            f.write("extra/\n")
        real = post_d6.delta_parts

        def then_add_a_root(*a, **kw):
            out = real(*a, **kw)
            (d / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["sub", "extra"]}), encoding="utf-8")
            return out

        with mock.patch.object(post_d6, "delta_parts", then_add_a_root):
            seen, log = self._single("1. **Note** — fine.\n\nCAP: 0/5 reported, exhausted\n", lambda: None)
        self.assertEqual(seen, [], "a judge ran though a scope was added after its delta")
        self.assertIn("Refused", log)

    def test_a_snapshot_of_another_state_is_never_recorded_as_reviewed(self):
        """A→B→A around the post-panel judge's snapshot: the recorded tree must BE the delta's (impl round 1)."""
        from tasks import core as tcore
        d = self.d
        real = tcore.build_panel_snapshot

        def at_b(project, fp):
            (d / "src" / "a.py").write_text("a = 7  # state B\n", encoding="utf-8")
            try:
                return real(project, fp)
            finally:
                (d / "src" / "a.py").write_text("a = 5  # after the panel\n", encoding="utf-8")

        with mock.patch.object(tcore, "build_panel_snapshot", at_b):
            seen, log = self._single("1. **Note** — fine.\n\nCAP: 0/5 reported, exhausted\n", lambda: None)
        self.assertEqual(seen, [], "a judge ran with a reviewed snapshot of another state")
        self.assertIn("Refused", log)

    def test_r2_8_an_absolute_link_into_the_project_resolves_in_the_copy(self):
        d = self.d
        os.unlink(d / "link.py")
        os.symlink(str((d / "docs" / "x.md").resolve()), d / "link.py")       # the delta retargets link.py
        seen, log = self._single(
            "**Important** [PRE-EXISTING] [file: docs/x.md] — older than the panel.\n\nCAP: 1/5 reported, exhausted\n",
            lambda: None)
        self.assertTrue(seen and seen[0] is not None, log[-2000:])
        self.assertIn("POST-D6 VERDICT: FAIL", log, "the delta's link target was lost by a host-path resolution")

    def test_a_file_field_resolves_in_the_copy_not_the_live_tree(self):
        d = self.d

        def during():
            os.unlink(d / "alias.py")
            os.symlink("src/a.py", d / "alias.py")          # live: now a delta file

        seen, log = self._single(
            "**Important** [PRE-EXISTING] [file: alias.py] — older than the panel.\n\nCAP: 1/5 reported, exhausted\n",
            during)
        self.assertTrue(seen and seen[0] is not None, log[-3000:])
        self.assertIn("POST-D6 VERDICT: PASS", log, "the field was resolved in the live tree")


@unittest.skipUnless(_mountable(), "no copy can be mounted here")
class ATreeThatKeepsMoving(unittest.TestCase, _PanelHarness):
    def test_the_panel_is_refused_before_anything_is_spent(self):
        d = _project(self)
        self._models(d)
        rv = _rv()
        real = rv._copy_listed_files
        n, called = [], []

        def racing(*a, **kw):
            n.append(1)
            (d / "src" / "a.py").write_text(f"a = {100 + len(n)}\n", encoding="utf-8")
            return real(*a, **kw)

        def judge(adapter, *a, **kw):
            called.append(1)
            return "1. **Note** — fine.\n"

        with mock.patch.object(rv, "_copy_listed_files", racing):
            log = self._run(d, judge, ["042", "--mode", "impl", "--models", "claude:opus"])
        self.assertEqual(called, [], "a judge ran on a tree that kept moving")
        self.assertIn("Refused", log)
        self.assertIn("Nothing was spent", log)
        self.assertFalse((d / ".agent" / "tasks" / "042-demo" / "judge.md").exists())
        self.assertEqual([p for p in rv.views_dir(d).iterdir() if p.is_dir()], [])


@unittest.skipUnless(_mountable(), "no copy can be mounted here")
class ACopyThatCannotBeMade(unittest.TestCase, _PanelHarness):
    def test_without_movement_it_reads_live_and_says_so_first(self):
        """Round 1 (G6) refused every failure; round 2 (opus) showed that refuses some projects for good. A failure
        while the live tree did not move is structural: no ready line was printed, so the live review is safe."""
        d = _project(self)
        self._models(d)
        rv = _rv()
        called = []

        def judge(adapter, *a, **kw):
            called.append(kw.get("project_view"))
            return "1. **Note** — fine.\n"

        with mock.patch.object(rv, "make", side_effect=rv.Unavailable("disk full")):
            log = self._run(d, judge, ["042", "--mode", "impl", "--models", "claude:opus"])
        self.assertEqual(called, [None])
        self.assertIn("LIVE tree", log)
        self.assertIn("disk full", log)
        self.assertNotIn("ready: you may write", log)

    def test_with_movement_it_is_refused(self):
        d = _project(self)
        self._models(d)
        rv = _rv()
        called, n = [], []

        def failing(*a, **kw):
            n.append(1)
            (d / "src" / "a.py").write_text(f"a = {50 + len(n)}\n", encoding="utf-8")
            raise rv.Unavailable("disk full")

        def judge(adapter, *a, **kw):
            called.append(1)
            return "1. **Note** — fine.\n"

        with mock.patch.object(rv, "make", failing):
            log = self._run(d, judge, ["042", "--mode", "impl", "--models", "claude:opus"])
        self.assertEqual(called, [])
        self.assertIn("Refused", log)


class PanelWithoutACopy(unittest.TestCase, _PanelHarness):
    def setUp(self):
        self.d = _project(self)
        self._models(self.d)

    def test_it_says_first_that_the_live_tree_is_read(self):
        log = self._run(self.d, lambda adapter, *a, **kw: "1. **Note** — fine.\n",
                        ["042", "--mode", "impl", "--models", "claude:opus"], view_ok=False)
        first = [ln for ln in log.splitlines() if "LIVE tree" in ln or "fixed copy" in ln]
        self.assertTrue(first and "LIVE tree" in first[0], log[:2000])
        self.assertIn("a test reason", log)

    def test_its_seats_get_no_copy(self):
        seen = []

        def judge(adapter, *a, **kw):
            seen.append(kw.get("project_view"))
            return "1. **Note** — fine.\n"

        self._run(self.d, judge, ["042", "--mode", "impl", "--models", "claude:opus"], view_ok=False)
        self.assertEqual(seen, [None])


if __name__ == "__main__":
    unittest.main()
