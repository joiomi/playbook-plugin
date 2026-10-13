"""The multi-user fixture never writes the developer's HOME (task 187, PLAN S14 T1; task 185's parked #49).

On 2026-10-12 a mutation run of task 183 broke `init`'s refusal on a fresh multi-user clone; the fixture's S15,
which expects that refusal, ran `init` with the developer's real HOME and replaced the owner's live shell logger.
This control reproduces that run on a COPY of the plugin tree — the same mutation in `require_lane_marker` — under a
sentinel HOME, and requires that (1) the fixture reports S15's refusal as broken (the mutation took, so the control
is not vacuous) and (2) every file of the sentinel HOME is byte-identical afterwards and none was added.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "wrapper-multiuser-fixture.sh"
PLUGIN = ROOT / "plugins" / "playbook"
REFUSAL = '    [ -n "$found" ] || return 0\n'


def snapshot(home: Path) -> dict:
    return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(home.rglob("*")) if p.is_file() or p.is_symlink()}


@unittest.skipUnless(shutil.which("bash"), "bash is required")
class FixtureNeverWritesTheCallersHome(unittest.TestCase):
    def test_a_broken_refusal_writes_only_the_fixtures_own_home(self):
        tmp = Path(tempfile.mkdtemp(prefix="pb187-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        copy = tmp / "repo"
        (copy / "tests").mkdir(parents=True)
        shutil.copy2(FIXTURE, copy / "tests" / FIXTURE.name)
        shutil.copytree(PLUGIN, copy / "plugins" / "playbook", ignore=shutil.ignore_patterns("__pycache__"))
        lib = copy / "plugins" / "playbook" / "scripts" / "gate-echo-lib.sh"
        text = lib.read_text(encoding="utf-8")
        self.assertEqual(text.count(REFUSAL), 1, "the refusal line moved: update this control")
        lib.write_text(text.replace(REFUSAL, "    return 0\n"), encoding="utf-8")   # task 183's mutation

        home = tmp / "caller-home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "bash-log.sh").write_text("# the caller's own logger\n", encoding="utf-8")
        (home / ".claude" / "settings.json").write_text('{"env": {"BASH_ENV": "~/.claude/bash-log.sh"}}\n',
                                                        encoding="utf-8")
        for rc in (".bashrc", ".profile", ".zshrc"):
            (home / rc).write_text(f"# the caller's {rc}\n", encoding="utf-8")
        before = snapshot(home)

        env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ZDOTDIR", "CLAUDE_CONFIG_DIR")}
        env["HOME"] = str(home)
        r = subprocess.run(["bash", str(copy / "tests" / FIXTURE.name)], cwd=tmp, env=env,
                           capture_output=True, text=True, timeout=600)
        out = r.stdout + r.stderr
        self.assertIn("FAIL  S15 bash init refuses", out,
                      "the mutation did not break init's refusal — this control would prove nothing:\n" + out[-1500:])
        self.assertEqual(snapshot(home), before,
                         "the fixture wrote the caller's HOME:\n" + out[-1500:])


if __name__ == "__main__":
    unittest.main()
