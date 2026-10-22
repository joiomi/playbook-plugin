#!/usr/bin/env python3
"""The command guard reads every spelling of the commands it promises to stop (task 293; audit 1, G3).

His blind check (2026-10-22) found `git clean --force -d` and `git push origin +main` let through, while `git clean -fd`
and `git push --force` were blocked: the rule read force only from the short options, and never from a `+` refspec
(git's own per-ref force). Each test names the row of task 293's contract it proves.

Run: python3 tests/test_command_guard_spellings.py
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "plugins/playbook/scripts"
sys.path.insert(0, str(_SCRIPTS))
import command_guard as cg  # noqa: E402


def verdict(cmd):
    v = cg.classify_command(cmd)
    return v[0], v[1]


class CleanForce(unittest.TestCase):
    def test_the_long_force_with_d_is_blocked(self):
        # row 1
        for cmd in ("git clean --force -d", "git clean -d --force", "git clean --force --quiet -d",
                    "git clean -d -x --force", "git clean -x --force -d"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-clean-force"))


class PushForce(unittest.TestCase):
    def test_a_plus_refspec_is_blocked(self):
        # row 2
        for cmd in ("git push origin +main", "git push origin +HEAD:main", "git push origin +refs/heads/a:refs/heads/a",
                    "git push origin main +dev", "git push -u origin +main"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-push-force"))


class Neighbours(unittest.TestCase):
    def test_the_safe_spellings_pass(self):
        # row 3 (must-not)
        for cmd in ("git clean -n -d", "git clean --dry-run -d", "git clean -n -f -d", "git clean -n -e build",
                    "git push origin main", "git push --force-with-lease", "git push origin main:main",
                    "git push origin --tags", "git push"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd)[0], "allow")

    def test_what_was_blocked_stays_blocked(self):
        # row 4 (must-not)
        for cmd, rule in (("git clean -fd", "git-clean-force"), ("git clean -xdf", "git-clean-force"),
                          ("git clean -f -d", "git-clean-force"), ("git push --force", "git-push-force"),
                          ("git push -f origin main", "git-push-force"), ("git reset --hard", "git-reset-hard")):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", rule))

    def test_data_and_other_verbs_pass(self):
        # row 6 (must-not)
        for cmd in ('git commit -m "+main"', "git log +main", "echo git push origin +main",
                    'grep -- "--force -d" notes.txt', "git show +main"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd)[0], "allow")


class Wrapped(unittest.TestCase):
    def test_wrapped_new_spellings_are_blocked(self):
        # row 5
        for cmd, rule in (("sudo git clean --force -d", "git-clean-force"),
                          ("env A=1 git push origin +main", "git-push-force"),
                          ('bash -c "git clean -d --force"', "git-clean-force"),
                          ("git -C repo push origin +main", "git-push-force"),
                          ("cd x && git clean --force -d", "git-clean-force")):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", rule))


class RoundOne(unittest.TestCase):
    """Task 293's impl round 1 (grok Critical, reproduced in a throwaway repo; opus, sonnet, codex): three more
    spellings of the same deletion or force push."""

    def test_an_abbreviated_long_force_is_force(self):
        # row 9: git's parse-options takes a unique prefix of a long option, and --force is clean's only --f…
        for cmd in ("git clean --f -d", "git clean --fo -d", "git clean --for -d", "git clean -d --forc"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-clean-force"))

    def test_a_forced_clean_with_a_pathspec_is_blocked(self):
        # row 10: with a pathspec, git clean removes untracked directories without -d
        for cmd in ("git clean -f .", "git clean --force src", "git clean -f -- docs", "git clean -fx build/"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-clean-force"))
        for cmd in ("git clean -n .", "git clean --dry-run -- docs"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd)[0], "allow")

    def test_a_config_override_is_read(self):
        # row 11: `-c` before the subcommand — git's own config, which the guard dropped with the globals
        for cmd, rule in (("git -c clean.requireForce=false clean -d", "git-clean-force"),
                          ("git -c clean.requireforce=0 clean .", "git-clean-force"),
                          ("git -c remote.origin.push=+refs/heads/main:refs/heads/main push", "git-push-force"),
                          ("git -c remote.origin.push=+main push origin", "git-push-force")):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", rule))
        for cmd in ("git -c user.name=x push origin main", "git -c clean.requireForce=true clean -n -d",
                    "git -c remote.origin.push=main push"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd)[0], "allow")


class RoundTwo(unittest.TestCase):
    """Task 293's impl round 2 (grok 3 Critical, each removing a directory in a throwaway repo; codex, opus): git's
    config has more spellings than a text guard can read, so a real `git clean` is refused unless it is a dry run, and
    a push or an alias whose config sits on the command line is read — or refused when it cannot be read."""

    def test_every_clean_that_is_no_dry_run_is_blocked(self):
        # row 13
        for cmd in ("git clean -f", "git clean --force", "git clean -d", "git clean -x", "git clean -i -d",
                    "git clean -f -e build", "git clean -f -epattern", "git clean -f --end-of-options --tmp",
                    "git clean -f -- -n", "git clean -f --end-of-options -n", "git clean -f -e -n",
                    "git clean -f --exclude -n",
                    "git -c clean.requireForce=00 clean -d", "git -c clean.requireForce=0x0 clean -d",
                    "V=false git --config-env=clean.requireForce=V clean -d",
                    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=clean.requireForce GIT_CONFIG_VALUE_0=false git clean -d"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-clean-force"))

    def test_push_config_the_guard_cannot_read_is_refused(self):
        # row 14 (edited after round 3: one rule name for config the guard cannot read)
        for cmd in ("P=+main git --config-env=remote.origin.push=P push origin",
                    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.push GIT_CONFIG_VALUE_0=+main git push origin",
                    "GIT_CONFIG_PARAMETERS=\"'remote.origin.push=+main'\" git push origin"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-config-unreadable"))
        self.assertEqual(verdict("GIT_TRACE=1 git push origin main")[0], "allow")

    def test_a_command_line_alias_is_refused(self):
        # row 15 (edited after round 3: an alias chain and the overrides it keeps cannot be followed — refused)
        for cmd in ("git -c alias.c='clean -fd' c", "git -c alias.p='push origin +main' p",
                    "git -c alias.x='!rm -rf ~' x", "V='clean -fd' git --config-env=alias.c=V c",
                    "git -c alias.a=b -c alias.b='clean -fd' a", "git -c remote.origin.push=+main -c alias.p=push p",
                    "git -c alias.st=status st"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-config-unreadable"))


class RoundThree(unittest.TestCase):
    """Task 293's impl round 3 (agy, grok, opus Critical; codex, sonnet): what a dry run is, and git config set on the
    command line, are refused unless the guard can prove them — the spellings kept coming."""

    def test_a_clean_passes_only_as_a_provable_dry_run(self):
        # row 17
        for cmd in ("git clean -n --no-dry-run -fd", "git clean --dry-run --no-dry-run -f", "git clean -fn --no-dry-run",
                    "git clean -fd --ex -n", "git clean -f --e -n", "git clean -f --ex --dry-run", "git clean -n --no-d -f",
                    "git clean -n -k -f", "git clean -n --interactive"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-clean-force"))
        for cmd in ("git clean -n", "git clean -n -d", "git clean --dry-run -x .", "git clean -n -f -d", "git clean -nq",
                    "git clean -n -e build", "git clean -n --exclude=build", "git clean -n --exclude build",
                    "git clean -n -- -x", "git clean --dry-run --end-of-options --no-dry-run"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd)[0], "allow")

    def test_git_config_in_the_environment_is_refused(self):
        # row 18
        for cmd in ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.c GIT_CONFIG_VALUE_0='clean -fd' git c",
                    "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.push GIT_CONFIG_VALUE_0=+main; git push origin",
                    "declare -x GIT_CONFIG_PARAMETERS=x; git c",
                    "bash -c 'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=x GIT_CONFIG_VALUE_0=y git push origin'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-config-unreadable"))
        for cmd in ("echo GIT_CONFIG_COUNT=1", "GIT_TRACE=1 git status", "GIT_CONFIG_COUNT=1 make build"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd)[0], "allow")

    def test_writing_a_dangerous_git_config_key_is_refused(self):
        # row 19
        for cmd in ("git config remote.origin.push +main", "git config --global alias.c 'clean -fd'",
                    "git config clean.requireForce false", "git config --local remote.origin.mirror true",
                    "git config set alias.x status", "git config --add remote.o.push +refs/heads/a:refs/heads/a",
                    "git config --file .git/config alias.c status", "git config -f cfg clean.requireForce false"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), ("block", "git-config-dangerous"))
        for cmd in ("git config user.name x", "git config --get remote.origin.push", "git config -l",
                    "git config remote.origin.push", "git config --unset alias.c", "git config get alias.c",
                    "git config --get-regexp remote.origin.push x", "git config --unset-all alias.c x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd)[0], "allow")


class Hook(unittest.TestCase):
    def test_the_hook_blocks_both(self):
        # row 7: the real hook, a temporary project and HOME
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="pb293-"))
        self.addCleanup(__import__("shutil").rmtree, d, True)
        (d / "home").mkdir()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(d / "home"), "LANG": "C.UTF-8", "PLAYBOOK_SESSION_ID": "x",
               "CLAUDE_PROJECT_DIR": str(d)}
        for cmd, rule in (("git clean --force -d", "git-clean-force"), ("git push origin +main", "git-push-force")):
            with self.subTest(cmd=cmd):
                payload = json.dumps({"session_id": "x", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                      "tool_input": {"command": cmd}})
                r = subprocess.run(["bash", str(_SCRIPTS / "command-guard-hook")], input=payload, cwd=d, env=env,
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn(rule, r.stdout + r.stderr)


class Docs(unittest.TestCase):
    def test_the_docs_name_the_spellings(self):
        # row 8
        doc = (_HERE.parent / "docs" / "configuration.md").read_text(encoding="utf-8")
        self.assertIn("`git clean --force -d`", doc)
        self.assertIn("`+` refspec", doc)
        for word in ("abbreviation", "pathspec", "dry run", "remote.<name>.push", "GIT_CONFIG_", "alias",
                     "not read", "provable", "git config"):                         # rows 12, 16, 20
            self.assertIn(word, doc, word)


if __name__ == "__main__":
    unittest.main()
