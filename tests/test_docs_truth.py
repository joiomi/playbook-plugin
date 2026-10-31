"""Task 348 (D, DESIGN-PLAYBOOK-2.md §7): the shipped text says only what the code does.

Five audits each found one of these sentences false (PHASE-B.md's single list, E023.1, E059.5, E086.6, E107, E125),
and each was left "for the release text". Each `Text` test fails on a sentence that came back; each `Behavior` test
pins what the corrected sentence now says, so the text and the code move together:
  S1  a reversible close runs the verify alone — the help and init's `_doc` said "verify+single-judge";
  S2  the task gate stops a code edit made through the edit tools — the README said it held for code generally;
  S3  a judge's prompt carries no conversation — the README said it had "no conversation access", while it can read the
      session transcripts on disk;
  S4  a sandboxed agent's writes stay in the project — the README said it "can't escape the write containment even if
      it tries", while the project's own `.claude` settings, hooks and `bin` are writable and run outside the sandbox;
  S5  one judge's impl review is no close's evidence under the seeded `panel_required_for` only;
  S6  `tasks red` runs each row in the project as it is (task 342 removed the copy of the base).

Run: python3 -m unittest tests.test_docs_truth
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
from unittest import mock

from tests._bashcheck import bash_or_skip

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK = ROOT / "plugins" / "playbook"
sys.path.insert(0, str(PLAYBOOK))
from provider import sandbox  # noqa: E402
from tasks import template  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """One line, single spaces: a sentence the help wraps over lines reads as written."""
    return " ".join(text.split())


def _init_doc() -> str:
    """The `_doc` string `scripts/init` writes into a new project's `.agent/config.json`."""
    line = next(ln for ln in _read("plugins/playbook/scripts/init").splitlines() if ln.lstrip().startswith('"_doc":'))
    return json.loads("{" + line.strip().rstrip(",") + "}")["_doc"]


class Text(unittest.TestCase):
    def test_s1_a_reversible_close(self):
        places = {
            "tasks help": _flat(template.usage_text()),
            "init _doc": _init_doc(),
            "commands/init.md": _flat(_read("plugins/playbook/commands/init.md")),
            "docs/cli.md": _flat(_read("docs/cli.md")),
        }
        for name, text in places.items():
            with self.subTest(where=name):
                self.assertNotIn("verify+single-judge", text)
                self.assertRegex(text, r"reversible work( \([^)]*\))? closes on the verify alone")

    def test_s2_the_task_gate(self):
        readme = _flat(_read("README.md"))
        self.assertNotIn("The agent can't edit code without an active task", readme)
        self.assertNotIn("Warnings don't stick, so we block instead", readme)
        self.assertIn("through its edit tools", readme)
        self.assertIn("a write through the shell", readme)
        self.assertIn("memento", readme)
        self.assertNotIn("declare a task first. The hooks enforce this.", readme)
        seeded = _flat(_read("plugins/playbook/scripts/CLAUDE.md.template"))
        self.assertNotIn("blocks editing a code file with no active task", seeded)
        self.assertIn("through the edit tools", seeded)
        arch = _flat(_read("docs/architecture.md"))
        self.assertNotIn("warnings don't stick — blocking does", arch)
        self.assertIn("a write through the shell passes", arch)

    def test_s3_the_judge_and_the_conversation(self):
        places = {
            "README.md": _read("README.md"),
            "docs/architecture.md": _read("docs/architecture.md"),
            "docs/cli.md": _read("docs/cli.md"),
            "skills/judge/SKILL.md": _read("plugins/playbook/skills/judge/SKILL.md"),
            "tasks guide": template.guide_text(),
        }
        stale = re.compile(r"no conversation access|not your conversation|but not the conversation|"
                           r"has no access to the conversation|but no conversation", re.I)
        for name, text in places.items():
            with self.subTest(where=name):
                self.assertIsNone(stale.search(_flat(text)), stale.search(_flat(text)))
        readme = _flat(places["README.md"])
        self.assertIn("~/.claude/projects/", readme)
        self.assertIn("prompt", readme[readme.index("**The judge**"):readme.index("**The chat log**")])

    def test_s4_the_sandbox(self):
        readme = _flat(_read("README.md"))
        self.assertNotIn("even if it tries", readme)
        self.assertNotIn("anything it shouldn't", readme)
        for name in (".claude/settings.json", ".claude/hooks/", ".claude/bin/"):
            with self.subTest(name=name):
                self.assertIn(name, readme)
        self.assertIn("uncontained", readme)

    def test_s5_a_single_judge_as_evidence(self):
        for rel in ("plugins/playbook/scripts/CLAUDE.md.template", "plugins/playbook/skills/playbook/SKILL.md",
                    "docs/cli.md"):
            text = _flat(_read(rel))
            with self.subTest(where=rel):
                self.assertNotIn("a second opinion, never a close's evidence", text)
                self.assertIn("under the seeded `panel_required_for` never a close's evidence", text)

    def test_s6_tasks_red(self):
        readme = _flat(_read("README.md"))
        self.assertNotIn("runs each row on the code before the fix", readme)
        self.assertIn("in the project as it is", readme)


def _bwrap_works() -> bool:
    if not shutil.which("bwrap"):
        return False
    try:
        return subprocess.run(["bwrap", "--ro-bind", "/", "/", "true"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class Behavior(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pb-348-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.project = self.tmp / "proj"
        (self.project / ".agent" / "tasks" / "001-x").mkdir(parents=True)
        (self.project / ".agent" / "tasks" / "001-x" / "task.md").write_text(
            "# 001 - x\n## Status\nin_progress\n## Work Plan\n- [ ] g\n", encoding="utf-8")
        (self.project / "src").mkdir()
        (self.project / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.home = self.tmp / "home"
        self.home.mkdir()

    def gate(self, tool: str, tool_input: dict) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "LANG": "C.UTF-8",
               "PLAYBOOK_SESSION_ID": "pid-348-gate"}
        return subprocess.run([bash_or_skip(), str(PLAYBOOK / "scripts" / "task-gate-hook")], cwd=self.project,
                              env=env, text=True, capture_output=True,
                              input=json.dumps({"tool_name": tool, "tool_input": tool_input}))

    def test_s2_the_gate_blocks_the_edit_not_the_shell(self):
        src = self.project / "src" / "a.py"
        edit = self.gate("Edit", {"file_path": str(src), "old_string": "x = 1", "new_string": "x = 2"})
        self.assertEqual(edit.returncode, 2, edit.stdout + edit.stderr)
        shell = self.gate("Bash", {"command": f"printf 'x = 2\\n' > {src}"})
        self.assertEqual(shell.returncode, 0, shell.stdout + shell.stderr)
        self.assertNotIn('"deny"', shell.stdout)

    def _run(self, argv: "list[str]") -> str:
        r = subprocess.run(argv, cwd=str(self.project), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    @unittest.skipUnless(_bwrap_works(), "bwrap not available or not usable here")
    def test_s3_a_judge_sandbox_reads_a_transcript(self):
        transcript = self.home / ".claude" / "projects" / "-proj" / "s.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text('{"message": "CANARY-348-in-the-transcript"}\n', encoding="utf-8")
        probe = [sys.executable, "-c", "import sys; print(open(sys.argv[1], encoding='utf-8').read())",
                 str(transcript)]
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            argv = sandbox.build_bwrap_argv(self.project, None, probe, None, project_writable=False,
                                            agent="claude")
        self.assertIn("CANARY-348-in-the-transcript", self._run(argv))

    @unittest.skipUnless(_bwrap_works(), "bwrap not available or not usable here")
    def test_s4_a_worker_sandbox_writes_the_projects_claude_folder(self):
        claude = self.project / ".claude"
        (claude / "hooks").mkdir(parents=True)
        (claude / "bin").mkdir()
        targets = [claude / "settings.json", claude / "hooks" / "h.sh", claude / "bin" / "tasks"]
        for t in targets:
            t.write_text("before\n", encoding="utf-8")
        write = ("import sys\n"
                 "for p in sys.argv[1:]:\n"
                 "    open(p, 'w', encoding='utf-8').write('written in the sandbox\\n')\n")
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            argv = sandbox.build_bwrap_argv(self.project, None, [sys.executable, "-c", write, *map(str, targets)],
                                            None, project_writable=True, agent="claude")
        self._run(argv)
        for t in targets:
            with self.subTest(path=str(t.relative_to(self.project))):
                self.assertEqual(t.read_text(encoding="utf-8"), "written in the sandbox\n")


if __name__ == "__main__":
    unittest.main()
