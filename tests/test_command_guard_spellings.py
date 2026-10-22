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
        for cmd in ("git clean -n -d", "git clean -d", "git clean --force", "git clean --dry-run -d",
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
        for cmd in ("git clean -f", "git clean --force", "git clean -f -e build", "git clean -n .", "git clean -d -- docs"):
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
        for cmd in ("git -c user.name=x push origin main", "git -c clean.requireForce=true clean -d",
                    "git -c remote.origin.push=main push"):
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
        for word in ("abbreviation", "pathspec", "clean.requireForce=false", "remote.<name>.push"):   # row 12
            self.assertIn(word, doc, word)


if __name__ == "__main__":
    unittest.main()
