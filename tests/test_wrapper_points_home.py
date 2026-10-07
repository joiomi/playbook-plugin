"""Task 115 (PLAN S11 group 1b): the plugin never sends a user to another repository.

Gauntlet 2 (task 109): `/playbook:upgrade` removed the user's marketplace and added the
UPSTREAM repository under the same name (G2-22); the wrapper's failure message named that
repository too (G2-27); and the wrapper's resolver ran as `python3 -` from the caller's cwd,
so a `glob.py` there shadowed the stdlib and the resolver died unseen (R10, parked since 086).

Run: python3 -m unittest tests.test_wrapper_points_home
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "playbook"
LIB = PLUGIN / "scripts" / "gate-echo-lib.sh"
UPGRADE = PLUGIN / "commands" / "upgrade.md"
UPSTREAM = "mariuscristescu"


def _fake_copy(base: Path, marker: str) -> Path:
    script = base / "scripts" / "tasks"
    script.parent.mkdir(parents=True)
    script.write_text(f"#!/bin/bash\necho {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    return base


@unittest.skipIf(os.name == "nt", "the fake plugin copies are POSIX scripts")
class TheWrapperResolvesTheInstalledCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        plugins = self.home / ".claude" / "plugins"
        # the copy the manifest names, and an older one the `find` fallback would pick first
        installed = _fake_copy(plugins / "cache" / "mk" / "playbook" / "2.0", "RESOLVED-INSTALLED-2.0")
        _fake_copy(plugins / "cache" / "mk" / "playbook" / "1.0", "FALLBACK-1.0")
        (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
            "playbook@mk": [{"installPath": str(installed), "version": "2.0", "scope": "user"}]}}),
            encoding="utf-8")
        self.project = self.tmp / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; create_wrapper "$2" tasks', "_",
                            str(LIB), str(self.project)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.wrapper = self.project / ".claude" / "bin" / "tasks"

    def _run(self, cwd: Path, home: Path | None = None):
        env = dict(os.environ, HOME=str(home or self.home))
        return subprocess.run([bash_or_skip(), str(self.wrapper)], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_a_glob_py_in_the_callers_directory_does_not_change_the_copy(self):
        here = self.tmp / "somewhere"
        here.mkdir()
        (here / "glob.py").write_text("raise SystemExit('a local glob.py shadowed the stdlib')\n", encoding="utf-8")
        (here / "json.py").write_text("raise SystemExit('a local json.py shadowed the stdlib')\n", encoding="utf-8")
        r = self._run(here)
        self.assertEqual(r.stdout.strip(), "RESOLVED-INSTALLED-2.0", r.stdout + r.stderr)
        r = self._run(self.project)                                    # the control: no shadowing module
        self.assertEqual(r.stdout.strip(), "RESOLVED-INSTALLED-2.0", r.stdout + r.stderr)

    def test_the_failure_message_names_no_repository(self):
        empty = self.tmp / "emptyhome"
        empty.mkdir()
        r = self._run(self.project, home=empty)
        self.assertEqual(r.returncode, 1)
        self.assertIn("playbook plugin not found", r.stderr)
        self.assertNotIn(UPSTREAM, r.stderr)
        self.assertNotIn("marketplace add", r.stderr)
        self.assertIn("claude plugin marketplace list", r.stderr)


class TheGeneratedWrapperIsolatesItsResolver(unittest.TestCase):
    def test_the_template_runs_the_resolver_isolated_and_names_no_repository(self):
        text = LIB.read_text(encoding="utf-8")
        self.assertIn('SCRIPT="$(python3 -I - "$PROJECT_ROOT" 2>/dev/null <<\'PYRESOLVE\'', text)
        start = text.index("content=$(cat <<'END_WRAPPER_TEMPLATE'")
        end = text.index("END_WRAPPER_TEMPLATE\n)", start)
        self.assertNotIn(UPSTREAM, text[start:end])


class UpgradeRefreshesWhatIsInstalled(unittest.TestCase):
    def setUp(self):
        self.text = UPGRADE.read_text(encoding="utf-8")

    def test_it_never_removes_or_adds_a_marketplace(self):
        self.assertNotIn("marketplace remove", self.text)
        self.assertNotIn("marketplace add", self.text)
        self.assertNotIn(UPSTREAM, self.text)

    def test_it_refreshes_the_marketplace_and_the_plugin_it_has(self):
        self.assertIn("claude plugin marketplace update playbook-x-marketplace", self.text)
        self.assertIn("claude plugin update playbook@playbook-x-marketplace", self.text)
        self.assertIn(".claude/bin/tasks --version", self.text)
        self.assertIn("/playbook:init", self.text)

    def test_the_first_step_never_stops_the_upgrade(self):
        # single-judge review: under "Stop on any failure" a bare `--version` (no wrapper yet, or a
        # wrapper that cannot resolve) ended the upgrade before anything was refreshed
        step1 = self.text[self.text.index("### 1."):self.text.index("### 2.")]
        self.assertIn('.claude/bin/tasks --version 2>/dev/null || echo "unknown', step1)
        self.assertIn("Tell the user this version now", step1)



class UpgradePullsAReleaseClone(unittest.TestCase):
    """Task 138 G1-2 (opus, codex-high, codex-medium): `claude plugin marketplace update` only
    re-reads a local directory, so on a marketplace that is a git clone (the owner's release
    clone since task 131) the recipe reinstalled the old code and reported "nothing newer".
    Step 2's first block pulls such a clone; it is run here as written."""

    def setUp(self):
        text = UPGRADE.read_text(encoding="utf-8")
        step2 = text[text.index("### 2."):text.index("### 3.")]
        blocks = step2.split("```bash\n")[1:]
        self.snippet = next(b.split("```")[0] for b in blocks if "pull --ff-only" in b)
        self.assertLess(step2.index("pull --ff-only"), step2.index("claude plugin marketplace update"))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        (self.home / ".claude" / "plugins").mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_NOSYSTEM="1")
        self.env.pop("BASH_ENV", None)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                              cwd=cwd, env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def known(self, source):
        (self.home / ".claude" / "plugins" / "known_marketplaces.json").write_text(
            json.dumps({"playbook-x-marketplace": {"source": source}}), encoding="utf-8")

    def run_snippet(self):
        return subprocess.run([bash_or_skip(), "-c", self.snippet], env=self.env, capture_output=True,
                              text=True, cwd=str(self.root), timeout=120)

    def test_a_directory_marketplace_that_is_a_clone_is_pulled(self):
        work = self.root / "upstream-work"
        work.mkdir()
        self.git("init", "-q", "-b", "main", cwd=work)
        (work / "v").write_text("1\n", encoding="utf-8")
        self.git("add", "v", cwd=work)
        self.git("commit", "-qm", "1.5.46", cwd=work)
        clone = self.root / "release"
        self.git("clone", "-q", str(work), str(clone))
        (work / "v").write_text("2\n", encoding="utf-8")
        self.git("commit", "-qam", "1.5.47", cwd=work)
        self.known({"source": "directory", "path": str(clone)})
        r = self.run_snippet()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((clone / "v").read_text(encoding="utf-8"), "2\n")

    def test_a_directory_nested_in_another_repository_is_not_pulled(self):
        # Task 139 C-1 (codex-high): `--is-inside-work-tree` is true for any directory inside a
        # repository — the recipe would have pulled the repository the marketplace sits in
        work = self.root / "upstream-work"
        work.mkdir()
        self.git("init", "-q", "-b", "main", cwd=work)
        (work / "v").write_text("1\n", encoding="utf-8")
        self.git("add", "v", cwd=work)
        self.git("commit", "-qm", "1", cwd=work)
        outer = self.root / "outer"
        self.git("clone", "-q", str(work), str(outer))
        (work / "v").write_text("2\n", encoding="utf-8")
        self.git("commit", "-qam", "2", cwd=work)
        (outer / "marketplace").mkdir()
        self.known({"source": "directory", "path": str(outer / "marketplace")})
        r = self.run_snippet()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((outer / "v").read_text(encoding="utf-8"), "1\n")   # not pulled

    def test_any_other_source_is_left_to_marketplace_update(self):
        plain = self.root / "plain-dir"
        plain.mkdir()
        for source in ({"source": "github", "repo": "someone/playbook"},
                       {"source": "directory", "path": str(plain)}):
            self.known(source)
            r = self.run_snippet()
            self.assertEqual(r.returncode, 0, (source, r.stdout + r.stderr))
        (self.home / ".claude" / "plugins" / "known_marketplaces.json").unlink()
        self.assertEqual(self.run_snippet().returncode, 0)


if __name__ == "__main__":
    unittest.main()
