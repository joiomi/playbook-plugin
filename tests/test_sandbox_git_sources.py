#!/usr/bin/env python3
"""What git runs by itself cannot be changed from inside the sandbox (task 181, PLAN §S11 item 31).

The sandbox binds the git directory read-only, so an agent cannot rewrite a hook or the
repository's settings there. Git can be TOLD to take them from somewhere else:

  * `core.hooksPath` names another directory for the hooks;
  * `.git/hooks` or `.git/config` may be a link to a place outside the git directory;
  * `include.path` and `includeIf.<condition>.path` pull more settings files in.

Where that place is the project's own content — which a worker may write — the agent
could change what git runs ON THE HOST at the next git command. Measured with real
bubblewrap on 2026-10-11, five ways: a command inside the sandbox wrote the file and
the host's next `git commit`, alias or diff ran what it had written. A hooks directory
that did not exist yet was simply created.

At launch the sandbox now asks git where its hooks and its settings come from and binds
those places read-only, after every writable bind. A place that does not exist where
the run could create it, or that covers something the run must write, cannot be kept
read-only: the launch is refused, with what to do. `--rw-git-sources` (the owner's
explicit permission) lifts all of it.

Every behaviour test runs a real command under real bubblewrap and then asks the HOST's
git whether what the agent wrote ran. Skipped where bubblewrap cannot start a sandbox.

Run: python3 -m unittest tests.test_sandbox_git_sources
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
PLUGIN = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(PLUGIN))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tests._bwrap_standin import bwrap_usable  # noqa: E402
from provider import sandbox  # noqa: E402

MARKER = "RAN-ON-HOST"
HOOK = f"#!/bin/sh\ntouch {MARKER}\n"
WRITE_HOOK = "printf '#!/bin/sh\\ntouch %s\\n' > {path} && chmod +x {path}" % MARKER
ADD_ALIAS = "printf '[alias]\\n\\tboom = !touch %s\\n' >> {path}" % MARKER


def _git(root, *args, check=True, env=None):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check, env=env)


class _Repo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.p = self.repo("proj")

    def repo(self, name: str) -> Path:
        p = self.base / name
        p.mkdir()
        _git(p, "init", "-q")
        _git(p, "config", "user.email", "t@t")
        _git(p, "config", "user.name", "t")
        (p / "a.txt").write_text("a\n", encoding="utf-8")
        _git(p, "add", "-A")
        _git(p, "commit", "-q", "-m", "c0")
        return p

    # what the host's git does afterwards
    def commit_ran_it(self, p=None) -> bool:
        p = p or self.p
        (p / "a.txt").write_text("changed\n", encoding="utf-8")
        r = _git(p, "commit", "-q", "-am", "c1", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (p / MARKER).exists()

    def alias_ran_it(self, p=None) -> bool:
        p = p or self.p
        _git(p, "boom", check=False)
        return (p / MARKER).exists()

    def hooks_path(self, name=".githooks", make=True):
        if make:
            (self.p / name).mkdir()
            (self.p / name / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            os.chmod(self.p / name / "pre-commit", 0o755)
        _git(self.p, "config", "core.hooksPath", name)


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class WhatGitRunsCannotBeChangedFromInside(_Repo):
    """The measured cases, each through the launch plan a real run uses."""

    def plan(self, script, p=None, **kw):
        # the REAL launch plan — its bubblewrap, its git directory, its read-only binds — with
        # a shell command in the agent's place (the plan knows only agents by name)
        from unittest import mock
        p = p or self.p
        with mock.patch.object(sandbox, "_compose_agent_argv", lambda agent, args: list(args)):
            argv = sandbox._wrapped_argv("claude", ["sh", "-c", script], p, kw.pop("extra_rw", None),
                                         kw.pop("project_writable", True), **kw)
        if Path(argv[0]).name != "bwrap":
            self.skipTest("this process is already inside a sandbox: the plan is the bare command")
        return argv

    def inside(self, script, p=None, **kw):
        return subprocess.run(self.plan(script, p, **kw), cwd=p or self.p, capture_output=True, text=True)

    def refused(self, r, what):
        self.assertNotEqual(r.returncode, 0, f"{what}: the write went through")
        self.assertIn("Read-only file system", r.stdout + r.stderr)

    def test_control_the_git_directory_itself(self):
        # green before task 181 too: this is what the sandbox always kept
        self.refused(self.inside(WRITE_HOOK.format(path=".git/hooks/pre-commit")), "the git directory")
        self.assertFalse(self.commit_ran_it())

    def test_control_an_ordinary_repository_is_launched_and_can_write_its_files(self):
        r = self.inside("echo new > b.txt && mkdir d && echo x > d/c.txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.p / "b.txt").read_text(encoding="utf-8"), "new\n")

    def test_a_hooks_path_that_names_a_project_directory(self):
        self.hooks_path()
        self.refused(self.inside(WRITE_HOOK.format(path=".githooks/pre-commit")), "core.hooksPath")
        self.assertFalse(self.commit_ran_it(), "the host's git ran what the sandboxed command wrote")
        # … and the rest of the project is still the agent's to write
        self.assertEqual(self.inside("echo new > b.txt").returncode, 0)

    def test_a_hooks_directory_that_is_a_link_into_the_project(self):
        (self.p / "hooks-in-project").mkdir()
        shutil.rmtree(self.p / ".git" / "hooks")
        os.symlink("../hooks-in-project", self.p / ".git" / "hooks")
        self.refused(self.inside(WRITE_HOOK.format(path="hooks-in-project/pre-commit")), ".git/hooks → the project")
        self.assertFalse(self.commit_ran_it())

    def test_a_settings_file_that_is_a_link_into_the_project(self):
        shutil.move(str(self.p / ".git" / "config"), str(self.p / "gitconfig-in-project"))
        os.symlink("../gitconfig-in-project", self.p / ".git" / "config")
        self.refused(self.inside(ADD_ALIAS.format(path="gitconfig-in-project")), ".git/config → the project")
        self.assertFalse(self.alias_ran_it())

    def test_an_included_settings_file_in_the_project(self):
        (self.p / "tools").mkdir()
        (self.p / "tools" / "extra.gitconfig").write_text("[core]\n\tabbrev = 12\n", encoding="utf-8")
        _git(self.p, "config", "include.path", "../tools/extra.gitconfig")
        self.refused(self.inside(ADD_ALIAS.format(path="tools/extra.gitconfig")), "include.path")
        self.assertFalse(self.alias_ran_it())

    def test_a_conditional_include_is_kept_whatever_its_condition_says_now(self):
        # the condition is evaluated again on the host, later: on another branch, here
        (self.p / "tools").mkdir()
        (self.p / "tools" / "later.gitconfig").write_text("", encoding="utf-8")
        _git(self.p, "config", "includeIf.onbranch:later.path", "../tools/later.gitconfig")
        payload = "printf '[diff]\\n\\texternal = sh -c \\\"touch %s\\\" --\\n' >> tools/later.gitconfig" % MARKER
        self.refused(self.inside(payload), "includeIf")
        _git(self.p, "checkout", "-q", "-b", "later")
        (self.p / "a.txt").write_text("changed\n", encoding="utf-8")
        _git(self.p, "diff", check=False)
        self.assertFalse((self.p / MARKER).exists(), "the host's git ran what the sandboxed command wrote")

    def test_a_hooks_path_that_does_not_exist_yet_refuses_the_launch(self):
        # it cannot be bound read-only, and left alone the run simply creates it (measured)
        self.hooks_path(".githooks-later", make=False)
        with self.assertRaises(RuntimeError) as cm:
            self.plan("true")
        said = str(cm.exception)
        self.assertIn(str(self.p / ".githooks-later"), said)
        self.assertIn("does not exist", said)
        self.assertIn("--rw-git-sources", said)
        self.assertTrue(said.endswith("Nothing was launched."), said)
        self.assertFalse((self.p / ".githooks-later").exists())

    def test_a_hooks_path_that_is_the_project_itself_refuses_the_launch(self):
        # read-only there would take the run's own writes with it
        _git(self.p, "config", "core.hooksPath", ".")
        with self.assertRaises(RuntimeError) as cm:
            self.plan("true")
        said = str(cm.exception)
        self.assertIn(str(self.p), said)
        self.assertIn("--rw-git-sources", said)
        self.assertTrue(said.endswith("Nothing was launched."), said)

    def _hooks_through_a_link_in_the_project(self):
        (self.p / "real-hooks").mkdir()
        (self.p / "real-hooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(self.p / "real-hooks" / "pre-commit", 0o755)
        os.symlink("real-hooks", self.p / "hooks-link")
        _git(self.p, "config", "core.hooksPath", "hooks-link")

    def test_a_link_in_the_project_on_the_way_to_the_hooks_refuses_the_launch(self):
        # what the link names can be bound read-only; the LINK is an entry of a directory the
        # run may write, and can be replaced by one that names something else
        self._hooks_through_a_link_in_the_project()
        with self.assertRaises(RuntimeError) as cm:
            self.plan("true")
        said = str(cm.exception)
        self.assertIn("link", said)
        self.assertIn(str(self.p), said)
        self.assertIn("--rw-git-sources", said)
        self.assertTrue(said.endswith("Nothing was launched."), said)
        # a judge's read-only run cannot replace it, and is not refused
        r = self.inside("rm hooks-link 2>&1; echo rc=$?", project_writable=False, mask_records=False)
        self.assertIn("Read-only file system", r.stdout + r.stderr)
        self.assertTrue((self.p / "hooks-link").is_symlink())

    def test_a_place_the_run_could_not_create_anyway_does_not_refuse(self):
        # an include that names a file nowhere writable: nothing to protect, nothing to refuse
        _git(self.p, "config", "include.path", "/nonexistent-181/not-here.gitconfig")
        self.assertEqual(self.inside("echo new > b.txt").returncode, 0)

    def test_the_owner_can_allow_it_explicitly(self):
        self.hooks_path()
        r = self.inside(WRITE_HOOK.format(path=".githooks/pre-commit"), git_sources_writable=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.commit_ran_it())          # his choice: the agent edits the hooks he tracks
        # … and a place that is not there yet no longer refuses
        _git(self.p, "config", "core.hooksPath", ".githooks-later")
        self.assertEqual(self.inside("true", git_sources_writable=True).returncode, 0)

    def test_a_judges_read_only_run_is_not_refused_and_keeps_its_outdir(self):
        # every review goes through this path: a new refusal here would stop them all
        self.hooks_path(".githooks-later", make=False)
        out = self.p / "out"
        r = self.inside("echo verdict > out/v.txt; mkdir .githooks-later 2>&1; echo rc=$?",
                        project_writable=False, extra_rw=[str(out)], mask_records=False)
        self.assertEqual((out / "v.txt").read_text(encoding="utf-8"), "verdict\n", r.stderr)
        self.assertIn("Read-only file system", r.stdout)
        self.assertFalse((self.p / ".githooks-later").exists())

    def test_a_repository_named_in_code_roots_is_asked_too(self):
        nested = self.repo("proj/plugin")
        (nested / ".githooks").mkdir()
        (nested / ".githooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(nested / ".githooks" / "pre-commit", 0o755)
        _git(nested, "config", "core.hooksPath", ".githooks")
        (self.p / ".agent").mkdir()
        (self.p / ".agent" / "config.json").write_text(json.dumps({"code_roots": ["plugin"]}), encoding="utf-8")
        self.refused(self.inside(WRITE_HOOK.format(path="plugin/.githooks/pre-commit")), "a code root's core.hooksPath")
        self.assertFalse(self.commit_ran_it(nested))


class WhatGitIsAsked(_Repo):
    """Without bubblewrap: where git says its hooks and its settings come from."""

    def sources(self, p=None):
        existing, missing, _link_dirs = sandbox._git_sources(p or self.p)
        return set(existing), set(missing)

    def test_the_directories_that_hold_a_link_on_the_way(self):
        # `.git/hooks` as a link: the link is an entry of the git directory
        (self.p / "hooks-in-project").mkdir()
        shutil.rmtree(self.p / ".git" / "hooks")
        os.symlink("../hooks-in-project", self.p / ".git" / "hooks")
        self.assertEqual(sandbox._git_sources(self.p)[2], [str(self.p / ".git")])
        # a hooks path through a link in the project, and that link through another one
        (self.p / "real").mkdir()
        (self.p / "sub").mkdir()
        os.symlink("../real", self.p / "sub" / "two")
        os.symlink("sub/two", self.p / "one")
        _git(self.p, "config", "core.hooksPath", "one")
        existing, _missing, link_dirs = sandbox._git_sources(self.p)
        self.assertIn(str(self.p / "real"), existing)
        self.assertEqual(sorted(link_dirs), sorted([str(self.p), str(self.p / "sub")]))

    def test_an_ordinary_repository(self):
        existing, missing = self.sources()
        self.assertIn(str(self.p / ".git" / "hooks"), existing)
        self.assertIn(str(self.p / ".git" / "config"), existing)
        self.assertEqual(missing, set())

    def test_the_hooks_path_relative_absolute_and_missing(self):
        self.hooks_path()
        self.assertIn(str(self.p / ".githooks"), self.sources()[0])
        elsewhere = self.base / "hooks-elsewhere"
        elsewhere.mkdir()
        _git(self.p, "config", "core.hooksPath", str(elsewhere))
        self.assertIn(str(elsewhere), self.sources()[0])
        _git(self.p, "config", "core.hooksPath", "not-there")
        existing, missing = self.sources()
        self.assertIn(str(self.p / "not-there"), missing)
        self.assertNotIn(str(self.p / "not-there"), existing)

    def test_links_are_followed_to_what_they_name(self):
        (self.p / "hooks-in-project").mkdir()
        shutil.rmtree(self.p / ".git" / "hooks")
        os.symlink("../hooks-in-project", self.p / ".git" / "hooks")
        shutil.move(str(self.p / ".git" / "config"), str(self.p / "gitconfig-in-project"))
        os.symlink("../gitconfig-in-project", self.p / ".git" / "config")
        existing, _ = self.sources()
        self.assertIn(str(self.p / "hooks-in-project"), existing)
        self.assertIn(str(self.p / "gitconfig-in-project"), existing)

    def test_include_paths_are_read_against_the_file_that_names_them(self):
        (self.p / "tools").mkdir()
        (self.p / "tools" / "one.gitconfig").write_text("[include]\n\tpath = two.gitconfig\n", encoding="utf-8")
        (self.p / "tools" / "two.gitconfig").write_text("[core]\n\tabbrev = 12\n", encoding="utf-8")
        _git(self.p, "config", "include.path", "../tools/one.gitconfig")     # relative to .git/
        _git(self.p, "config", "--add", "include.path", "../tools/empty.gitconfig")
        (self.p / "tools" / "empty.gitconfig").write_text("", encoding="utf-8")   # contributes no setting
        _git(self.p, "config", "--add", "include.path", "../tools/absent.gitconfig")
        _git(self.p, "config", "includeIf.onbranch:never.path", "../tools/cond.gitconfig")
        (self.p / "tools" / "cond.gitconfig").write_text("", encoding="utf-8")
        existing, missing = self.sources()
        for name in ("one", "two", "empty", "cond"):
            self.assertIn(str(self.p / "tools" / f"{name}.gitconfig"), existing, name)
        self.assertEqual(missing, {str(self.p / "tools" / "absent.gitconfig")})

    def test_a_home_relative_include(self):
        home = self.base / "home"
        home.mkdir()
        (home / "work.gitconfig").write_text("[core]\n\tabbrev = 12\n", encoding="utf-8")
        _git(self.p, "config", "include.path", "~/work.gitconfig")
        # one that gives no value and one that is not there: git lists neither among the files
        # it read, so only the include path itself — with its `~` read as git reads it — names them
        (home / "empty.gitconfig").write_text("", encoding="utf-8")
        _git(self.p, "config", "--add", "include.path", "~/empty.gitconfig")
        _git(self.p, "config", "--add", "include.path", "~/absent.gitconfig")
        env = dict(os.environ, HOME=str(home))
        from unittest import mock
        with mock.patch.dict(os.environ, env, clear=True):
            existing, missing = self.sources()
        self.assertIn(str(home / "work.gitconfig"), existing)
        self.assertIn(str(home / "empty.gitconfig"), existing)
        self.assertEqual(missing, {str(home / "absent.gitconfig")})

    def test_the_environment_cannot_make_git_answer_for_another_repository(self):
        other = self.repo("other")
        (other / ".githooks").mkdir()
        _git(other, "config", "core.hooksPath", ".githooks")
        from unittest import mock
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
            existing, _ = self.sources()
        self.assertIn(str(self.p / ".git" / "hooks"), existing)
        self.assertNotIn(str(other / ".githooks"), existing)

    def test_a_directory_that_is_no_repository_names_nothing(self):
        plain = self.base / "plain"
        plain.mkdir()
        from unittest import mock
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.base)}):
            existing, missing, link_dirs = sandbox._git_sources(plain)
        self.assertEqual([p for p in existing if p.startswith(str(plain))], [])
        self.assertEqual((missing, link_dirs), ([], []))


class WhereTheBindsGo(_Repo):
    """The argv itself: after everything writable, and nothing laid over a path the run needs."""

    def argv(self, **kw):
        return sandbox.build_bwrap_argv(self.p, self.p / ".git", ["true"], kw.pop("extra_rw", None), **kw)

    def test_a_source_is_bound_read_only_after_the_project_and_the_writable_paths(self):
        hooks = self.p / ".githooks"
        hooks.mkdir()
        argv = self.argv(extra_rw=[str(self.p / "out")], git_sources=[str(hooks)])
        ro = [i for i in range(len(argv) - 2) if argv[i] == "--ro-bind" and argv[i + 1] == str(hooks)]
        self.assertEqual(len(ro), 1, argv)
        last_rw = max(i for i in range(len(argv)) if argv[i] == "--bind")
        self.assertGreater(ro[0], last_rw)

    def test_with_the_owners_permission_nothing_is_added_and_nothing_refused(self):
        hooks = self.p / ".githooks"
        hooks.mkdir()
        plain = self.argv()
        allowed = self.argv(git_sources=[str(hooks), str(self.p)],
                            git_sources_missing=[str(self.p / "later")], git_sources_writable=True)
        self.assertEqual(allowed, plain)

    def test_a_missing_source_under_a_read_only_place_does_not_refuse(self):
        # `.git/hooks` deleted: it is inside the git directory, which is read-only anyway
        argv = self.argv(git_sources_missing=[str(self.p / ".git" / "hooks")])
        self.assertIn("true", argv)
        # … and so is anything in a project that is itself read-only (a judge's run)
        self.argv(project_writable=False, mask_records=False, git_sources_missing=[str(self.p / "later")])

    def test_a_missing_source_under_tmp_refuses_even_for_a_read_only_project(self):
        # /tmp is writable in every run
        missing = Path(tempfile.gettempdir()).resolve() / "pb181-no-such-hooks-dir"
        if not str(missing).startswith("/tmp/"):
            self.skipTest("the temp directory is not under /tmp here")
        with self.assertRaises(RuntimeError) as cm:
            self.argv(project_writable=False, mask_records=False, git_sources_missing=[str(missing)])
        self.assertIn(str(missing), str(cm.exception))

    def test_a_link_directory_refuses_only_where_the_run_may_write_it(self):
        with self.assertRaises(RuntimeError) as cm:
            self.argv(git_source_links=[str(self.p)])
        self.assertIn("link", str(cm.exception))
        self.argv(git_source_links=[str(self.p / ".git")])                      # the git directory: read-only
        self.argv(project_writable=False, mask_records=False, git_source_links=[str(self.p)])
        self.argv(git_source_links=[str(self.p)], git_sources_writable=True)    # the owner's permission


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class TheCommandLine(_Repo):
    """`scripts/sandbox` itself: the preview shows what a launch would bind, and the option."""

    def main(self, *args):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sandbox._main(["--agent", "claude", "--project-root", str(self.p), *args])
        return rc, out.getvalue().splitlines(), err.getvalue()

    def read_only(self, lines):
        return [lines[i + 1] for i in range(len(lines) - 1) if lines[i] == "--ro-bind"]

    def test_the_preview_shows_the_hooks_directory_read_only_and_the_option_takes_it_out(self):
        self.hooks_path()
        rc, lines, err = self.main("--print-argv", "--", "hello")
        if rc == 0 and Path(lines[0]).name != "bwrap":
            self.skipTest("this process is already inside a sandbox: the preview is the bare command")
        self.assertEqual(rc, 0, err)
        self.assertIn(str(self.p / ".githooks"), self.read_only(lines))
        rc, lines, err = self.main("--print-argv", "--rw-git-sources", "--", "hello")
        self.assertEqual(rc, 0, err)
        self.assertNotIn(str(self.p / ".githooks"), self.read_only(lines))
        self.assertIn(str(self.p / ".git"), self.read_only(lines))            # the git directory stays

    def test_a_refusal_is_said_by_the_command_and_the_option_lifts_it(self):
        self.hooks_path(".githooks-later", make=False)
        rc, lines, err = self.main("--print-argv", "--", "hello")
        if rc == 0 and lines and Path(lines[0]).name != "bwrap":
            self.skipTest("this process is already inside a sandbox")
        self.assertEqual(rc, 2)
        self.assertEqual(lines, [])
        self.assertIn(str(self.p / ".githooks-later"), err)
        self.assertTrue(err.strip().endswith("Nothing was launched."), err)
        self.assertFalse((self.p / ".githooks-later").exists())
        self.assertEqual(self.main("--print-argv", "--rw-git-sources", "--", "hello")[0], 0)

    def test_the_option_is_not_for_the_path_the_judges_share(self):
        rc, lines, err = self.main("--rw-git-sources", "--prompt", "x")
        self.assertEqual(rc, 2)
        self.assertIn("--rw-git-sources is not supported with --prompt", err)


if __name__ == "__main__":
    unittest.main()
