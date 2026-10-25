#!/usr/bin/env python3
"""Task 314 (stabilization audit 4, finding G5): the code-state fingerprint sees a file under an index bit.

`tree_state_fingerprint` names what a panel reviewed and what a close certified. It read `git status --porcelain`
and `git diff`, and both skip a file whose index entry carries assume-unchanged or skip-worktree: a code change to
such a file after a review left the fingerprint as it was, so a stale review read as fresh. Since impl round 1 every
freshness read of the working tree — the fingerprint, the dirty map tail certification is built from, the post-panel
tree — goes through a copy of the index with both bits cleared (in a sparse checkout, skip-worktree only where the
cone holds the file), so git itself reports the change; a repo with no bit keeps the fingerprint it had, byte for byte.

Run:
    python3 -m unittest tests.test_fingerprint_sees_index_bits
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "playbook"))

from tasks.core import tree_state_fingerprint  # noqa: E402


class _Repo(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.d = self.base / "repo"
        self.d.mkdir()
        self.git("init", "-q")
        self.app = self.d / "app.py"
        self.app.write_text("def f():\n    return 2\n", encoding="utf-8")
        (self.d / ".agent").mkdir()
        self.git("add", "app.py")
        self.git("commit", "-qm", "reviewed")

    def git(self, *args) -> str:
        return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=self.d, check=True,
                              capture_output=True, text=True).stdout

    def fp(self) -> str:
        got = tree_state_fingerprint(self.d)
        self.assertTrue(got, "no fingerprint")
        return got


class TheFingerprint(_Repo):
    def change_under(self, bit: str):
        self.git("update-index", bit, "app.py")
        reviewed = self.fp()
        self.app.write_text("def f():\n    return 3   # changed after the review\n", encoding="utf-8")
        self.assertEqual(self.git("status", "--porcelain"), "", "the control: git status does not see the change")
        self.assertNotEqual(self.fp(), reviewed, f"a code change under {bit} left the fingerprint as it was")

    def test_a_change_under_assume_unchanged_moves_it(self):
        self.change_under("--assume-unchanged")

    def test_a_change_under_skip_worktree_moves_it(self):
        self.change_under("--skip-worktree")

    def test_control_no_bit_is_as_before(self):
        # the formula of before this task, written out independently (as test_panel_freshness_gate's oracle is)
        def oracle():
            from tasks.core import _fingerprint_exclude_pathspecs
            exclude = _fingerprint_exclude_pathspecs({})          # impl round 1 (sonnet): not by hand
            head = self.git("rev-parse", "HEAD").strip()
            porcelain = self.git("status", "--porcelain", "-uall", "--ignore-submodules=none", "--", ".", *exclude)
            diff = self.git("diff", "--ignore-submodules=none", "HEAD", "--", ".", *exclude)
            empty_untracked = hashlib.sha256().hexdigest()
            return hashlib.sha256((head + porcelain + diff + empty_untracked).encode("utf-8")).hexdigest()[:12]
        clean = self.fp()
        self.assertEqual(clean, oracle())
        self.app.write_text("def f():\n    return 3\n", encoding="utf-8")
        self.assertEqual(self.fp(), oracle())
        self.assertNotEqual(self.fp(), clean)

    def test_a_marked_file_gone_or_linked_moves_it(self):
        self.git("update-index", "--assume-unchanged", "app.py")
        reviewed = self.fp()
        self.app.unlink()
        gone = self.fp()
        self.assertNotEqual(gone, reviewed, "a marked file deleted after the review left the fingerprint as it was")
        outside = self.base / "outside.py"
        outside.write_text("def f():\n    return 2\n", encoding="utf-8")       # the reviewed bytes, behind a link
        self.app.symlink_to(outside)
        linked = self.fp()
        self.assertNotEqual(linked, reviewed, "a marked file replaced by a link read as the reviewed file")
        self.assertNotEqual(linked, gone)
        outside.write_text("def f():\n    return 4\n", encoding="utf-8")
        self.assertEqual(self.fp(), linked, "the link was read through")
        os.unlink(self.app)
        self.app.symlink_to(self.base / "elsewhere.py")
        self.assertNotEqual(self.fp(), linked, "a link re-pointed left the fingerprint as it was")

    def test_the_same_change_to_another_file_is_another_state(self):
        (self.d / "b.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        self.git("add", "b.py")
        self.git("commit", "-qm", "two files")
        changed = "def f():\n    return 9\n"
        self.git("update-index", "--assume-unchanged", "app.py")
        self.app.write_text(changed, encoding="utf-8")
        app_changed = self.fp()
        self.git("update-index", "--no-assume-unchanged", "app.py")
        self.git("checkout", "--", "app.py")
        self.git("update-index", "--assume-unchanged", "b.py")
        (self.d / "b.py").write_text(changed, encoding="utf-8")
        self.assertNotEqual(self.fp(), app_changed, "a change to b.py read as the change to app.py")

    def test_a_project_in_a_subfolder_of_its_repository_sees_it(self):
        proj = self.d / "proj"
        (proj / ".agent").mkdir(parents=True)
        (proj / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "proj/app.py")
        self.git("commit", "-qm", "a project in a subfolder")
        self.git("update-index", "--assume-unchanged", "proj/app.py")
        reviewed = tree_state_fingerprint(proj)
        self.assertTrue(reviewed)
        (proj / "app.py").write_text("x = 2\n", encoding="utf-8")
        self.assertNotEqual(tree_state_fingerprint(proj), reviewed)

    def test_git_that_cannot_list_the_index_gives_no_fingerprint(self):
        import unittest.mock as mock
        import tasks.core as core
        real = subprocess.run

        def run(args, *a, **kw):
            if "ls-files" in args:
                return subprocess.CompletedProcess(args, 128, b"", b"fatal: index file corrupt")
            return real(args, *a, **kw)
        self.git("update-index", "--assume-unchanged", "app.py")
        with mock.patch.object(core.subprocess, "run", run):
            self.assertEqual(tree_state_fingerprint(self.d), "", "a failed index listing read as no index bit")


class TheStaleCloseReadersToo(_Repo):
    """Impl round 1 (opus Critical; codex, reproduced twice): the close's other readers of the working tree — the dirty
    map tail certification is built from, and the tree the post-panel judge and the review copy read — saw no
    change under a bit either; and the fingerprint missed a chmod and a same-size edit over 5 MiB."""

    def test_the_dirty_map_names_a_changed_file_under_a_bit(self):
        from tasks.core import _dirty_path_content_map, _fingerprint_exclude_pathspecs
        exclude = _fingerprint_exclude_pathspecs({})
        for bit in ("--assume-unchanged", "--skip-worktree"):
            self.git("update-index", bit, "app.py")
            self.app.write_text("def f():\n    return 5\n", encoding="utf-8")
            (self.d / "README.md").write_text("docs\n", encoding="utf-8")
            dirty = _dirty_path_content_map(self.d, exclude)
            self.assertIn("app.py", dirty or {}, f"{bit}: the delta would read docs-only")
            self.git("update-index", bit.replace("--", "--no-"), "app.py")
            self.git("checkout", "--", "app.py")
            (self.d / "README.md").unlink()

    def test_the_working_tree_object_holds_a_changed_file_under_a_bit(self):
        from tasks.core import _fingerprint_exclude_pathspecs
        from tasks.post_d6 import worktree_tree
        exclude = _fingerprint_exclude_pathspecs({})
        for bit in ("--assume-unchanged", "--skip-worktree"):
            self.git("update-index", bit, "app.py")
            before = worktree_tree(self.d, exclude)
            self.app.write_text("def f():\n    return 6\n", encoding="utf-8")
            after = worktree_tree(self.d, exclude)
            self.assertTrue(before and after)
            self.assertNotEqual(after, before, f"{bit}: the post-panel tree left the change out")
            blob = self.git("rev-parse", f"{after}:app.py").strip()
            self.assertEqual(self.git("cat-file", "-p", blob), "def f():\n    return 6\n")
            self.git("update-index", bit.replace("--", "--no-"), "app.py")
            self.git("checkout", "--", "app.py")

    def test_a_mode_change_under_a_bit_moves_the_fingerprint(self):
        self.git("update-index", "--assume-unchanged", "app.py")
        reviewed = self.fp()
        self.app.chmod(0o755)
        self.assertNotEqual(self.fp(), reviewed, "a chmod under the bit read FRESH")

    def test_a_same_size_edit_of_a_large_marked_file_moves_the_fingerprint(self):
        big = self.d / "big.py"
        big.write_bytes(b"x = 1\n" + b"#" * (5 * 1024 * 1024 + 100) + b"\n")
        self.git("add", "big.py")
        self.git("commit", "-qm", "big")
        self.git("update-index", "--assume-unchanged", "big.py")
        reviewed = self.fp()
        data = big.read_bytes()
        big.write_bytes(b"x = 2" + data[5:])                     # the same size
        self.assertNotEqual(self.fp(), reviewed, "a same-size edit over 5 MiB read FRESH")

    def test_marking_an_unchanged_file_leaves_the_fingerprint_as_it_was(self):
        clean = self.fp()
        self.git("update-index", "--assume-unchanged", "app.py")
        self.assertEqual(self.fp(), clean, "the bit alone made a spurious STALE")


class TheSkipWorktreeRule(_Repo):
    """My mutant run on impl round 1's fix: a skip-worktree file that is absent is a deletion, unless the repository is
    a sparse checkout, where files outside the cone are absent by design (and a `git diff` of each would print it)."""

    def test_a_skip_worktree_file_deleted_outside_a_sparse_checkout_moves_the_fingerprint(self):
        self.git("update-index", "--skip-worktree", "app.py")
        reviewed = self.fp()
        self.app.unlink()
        self.assertNotEqual(self.fp(), reviewed, "a deletion under skip-worktree read FRESH")

    def test_a_sparse_checkout_s_absent_files_are_not_read_as_deleted(self):
        from tasks.core import _fingerprint_exclude_pathspecs, _repo_fingerprint_material
        (self.d / "inside").mkdir()
        (self.d / "inside" / "a.py").write_text("a = 1\n", encoding="utf-8")
        (self.d / "outside").mkdir()
        (self.d / "outside" / "b.py").write_text("b = 1\n", encoding="utf-8")
        self.git("add", "inside", "outside")
        self.git("commit", "-qm", "two folders")
        self.git("sparse-checkout", "set", "inside")
        self.assertFalse((self.d / "outside" / "b.py").exists(), "the control: outside the cone, not checked out")
        material = _repo_fingerprint_material(self.d, _fingerprint_exclude_pathspecs({}))
        self.assertIsNotNone(material)
        self.assertNotIn("outside/b.py", material, "a file outside the cone was read as deleted")
        self.assertEqual(self.fp(), self.fp())


class InASparseCheckout(_Repo):
    """Impl round 2 (codex, each reproduced twice): in a sparse checkout, only the files the cone leaves out keep their
    skip-worktree bit — a file inside the cone deleted under the bit, and a file outside it put back and changed, are
    seen — and the post-panel tree stages with `--sparse`."""

    def setUp(self):
        super().setUp()
        for rel, text in (("inside/a.py", "a = 1\n"), ("inside/deep/d.py", "d = 1\n"), ("outside/b.py", "b = 1\n")):
            (self.d / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.d / rel).write_text(text, encoding="utf-8")
        self.git("add", "inside", "outside")
        self.git("commit", "-qm", "two folders")
        self.git("sparse-checkout", "set", "inside")
        self.assertFalse((self.d / "outside" / "b.py").exists(), "the control: outside the cone, not checked out")

    def test_a_file_inside_the_cone_deleted_under_skip_worktree_moves_the_fingerprint(self):
        from tasks.core import _dirty_path_content_map, _fingerprint_exclude_pathspecs
        self.git("update-index", "--skip-worktree", "inside/a.py")
        reviewed = self.fp()
        (self.d / "inside" / "a.py").unlink()
        self.assertNotEqual(self.fp(), reviewed, "a deletion inside the cone read FRESH")
        self.assertIn("inside/a.py", _dirty_path_content_map(self.d, _fingerprint_exclude_pathspecs({})) or {})

    def test_a_file_outside_the_cone_put_back_and_changed_is_seen(self):
        from tasks.core import _fingerprint_exclude_pathspecs
        from tasks.post_d6 import worktree_tree
        exclude = _fingerprint_exclude_pathspecs({})
        reviewed, before = self.fp(), worktree_tree(self.d, exclude)
        (self.d / "outside").mkdir()
        (self.d / "outside" / "b.py").write_text("b = 2  # put back and changed\n", encoding="utf-8")
        self.assertNotEqual(self.fp(), reviewed, "the changed file outside the cone read FRESH")
        after = worktree_tree(self.d, exclude)
        self.assertNotEqual(after, before)
        blob = self.git("rev-parse", f"{after}:outside/b.py").strip()
        self.assertEqual(self.git("cat-file", "-p", blob), "b = 2  # put back and changed\n")

    def test_root_files_and_a_listed_folder_s_parents_are_in_the_cone(self):
        # my mutant run on impl round 2's fix: cone mode holds the root's files and the files directly in a parent of a
        # listed folder; a deletion under skip-worktree there is a deletion
        self.git("sparse-checkout", "set", "inside/deep")
        for rel in ("app.py", "inside/a.py"):
            self.assertTrue((self.d / rel).exists(), f"the control: {rel} is checked out")
            self.git("update-index", "--skip-worktree", rel)
            reviewed = self.fp()
            (self.d / rel).unlink()
            self.assertNotEqual(self.fp(), reviewed, f"a deletion of {rel}, inside the cone, read FRESH")

    def test_a_cone_folder_git_prints_quoted_is_read_as_its_name(self):
        # post-panel run 1 (codex, reproduced twice): `sparse-checkout list` C-quotes a folder with a byte outside ASCII
        # (`"caf\303\251"`) or a quote, and the cone check took the quoted text for the name
        self.git("sparse-checkout", "disable")           # to add the folders; set again below
        for name in ("café", 'q"d'):
            (self.d / name).mkdir()
            (self.d / name / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "café", 'q"d')
        self.git("commit", "-qm", "odd folders")
        self.git("sparse-checkout", "set", "café", 'q"d')
        for name in ("café", 'q"d'):
            rel = f"{name}/app.py"
            self.assertTrue((self.d / rel).exists(), f"the control: {rel} is in the cone")
            self.git("update-index", "--skip-worktree", rel)
            reviewed = self.fp()
            (self.d / rel).unlink()
            self.assertNotEqual(self.fp(), reviewed, f"a deletion of {rel}, inside the cone, read FRESH")

    def test_a_project_folder_whose_name_starts_with_a_space_keeps_its_cone(self):
        # post-panel run 2 (codex, reproduced twice): `--show-prefix` read with `.strip()` lost the leading space of
        # ` project/`, so the cone check compared the wrong path
        self.git("sparse-checkout", "disable")
        proj = self.d / " project"
        (proj / "inside").mkdir(parents=True)
        (proj / ".agent").mkdir()
        (proj / "inside" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", " project/inside/app.py")
        self.git("commit", "-qm", "a project whose folder starts with a space")
        self.git("sparse-checkout", "set", "./ project/inside")   # `./`: git keeps the leading space so
        rel = " project/inside/app.py"
        self.assertTrue((self.d / rel).exists(), "the control: it is in the cone")
        self.git("update-index", "--skip-worktree", rel)
        (self.d / rel).unlink()
        # what this code controls, whatever git then reports for an in-cone skip-worktree entry (git 2.54 reports
        # some, codex's did not): the copy git reads clears the bit of a file the cone holds
        import subprocess as sp
        from tasks.core import _SeeingIndexBits
        with _SeeingIndexBits(proj) as env:
            self.assertIsNotNone(env)
            tags = sp.run(["git", "ls-files", "-v", "--", "inside/app.py"], cwd=proj, env=env,
                          capture_output=True, text=True).stdout
        self.assertEqual(tags, "H inside/app.py\n", "the copy kept the bit of a file the cone holds")

    def test_the_post_panel_tree_keeps_an_absent_file_outside_the_cone(self):
        from tasks.core import _fingerprint_exclude_pathspecs
        from tasks.post_d6 import worktree_tree
        tree = worktree_tree(self.d, _fingerprint_exclude_pathspecs({}))
        blob = self.git("rev-parse", f"{tree}:outside/b.py").strip()
        self.assertEqual(self.git("cat-file", "-p", blob), "b = 1\n", "an absent file outside the cone read as deleted")


class TheIndexCopy(_Repo):
    def test_a_failed_clearing_leaves_no_index_copy_behind(self):
        # impl round 2 (agy, opus, sonnet): `__exit__` does not run when `__enter__` raises
        import tempfile
        import unittest.mock as mock
        import tasks.core as core
        spare = self.base / "spare-tmp"
        spare.mkdir()
        self.git("update-index", "--assume-unchanged", "app.py")
        with mock.patch.object(tempfile, "tempdir", str(spare)), \
                mock.patch.object(core, "_index_bits_cleared", lambda repo, env: False):
            self.assertEqual(tree_state_fingerprint(self.d), "")
        self.assertEqual(list(spare.iterdir()), [], "an index copy was left behind")


if __name__ == "__main__":
    unittest.main()
