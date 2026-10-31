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
                self.assertNotIn("verify alone", text)    # the close also runs the contract's rows (impl round 2)
                self.assertRegex(text, r"reversible work( \([^)]*\))? closes with no review")
                self.assertIn("`tasks red` rows", text)

    def test_s2_the_task_gate(self):
        readme = _flat(_read("README.md"))
        self.assertNotIn("The agent can't edit code without an active task", readme)
        self.assertNotIn("Warnings don't stick, so we block instead", readme)
        self.assertIn("through its edit tools", readme)
        self.assertIn("a write through the shell", readme)
        self.assertIn("memento", readme)
        self.assertNotIn("declare a task first. The hooks enforce this.", readme)
        self.assertIn("`tasks work done` refuses", readme)     # the close's refusal, not a hook's (impl round 1)
        # any rewording that promises the gate for code without naming the edit tools (impl round 2, sonnet)
        self.assertNotRegex(readme, r"can't edit (?!a code file through its edit tools)[^.]{0,80}without an active task")
        self.assertNotIn("Where a warning doesn't stick, they block", readme)
        self.assertIn("where they see the call", readme)
        # the freshness block holds an assertive, irreversible or unclassified close; a reversible one is told
        # (core.py `freshness_gate_decision`; impl round 2, codex)
        self.assertNotIn("so a change after it means another round or `--force --reason`", readme)
        self.assertIn("a reversible one is told", readme)
        seeded = _flat(_read("plugins/playbook/scripts/CLAUDE.md.template"))
        self.assertNotIn("blocks editing a code file with no active task", seeded)
        self.assertIn("through the edit tools", seeded)
        for name, text in (("CLAUDE.md.template", seeded), ("tasks init's CLAUDE.md", _flat(template.claude_md("X")))):
            with self.subTest(where=name):
                self.assertNotIn("without an active task — blocked by hook until", text)
                self.assertIn("a shell write passes", text)
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
                           r"has no access to the conversation|but no conversation|not\W+your chat|none of your chat|"
                           r"none of your conversation|"
                           r"The judge won't see this|Only what you put in the prompt and what it reads", re.I)
        for name, text in places.items():
            with self.subTest(where=name):
                self.assertIsNone(stale.search(_flat(text)), stale.search(_flat(text)))
        readme = _flat(places["README.md"])
        judge = readme[readme.index("**The judge**"):readme.index("**The chat log**")]
        for named in ("prompt", "~/.claude/projects/", "the task quotes", "`panel-review --prompt`"):
            with self.subTest(named=named):
                self.assertIn(named, judge)
        # the judge skill's single-judge route is one judge whose record is judge-single.md (impl round 2, grok)
        skill = _flat(places["skills/judge/SKILL.md"])
        self.assertNotIn("(a sandboxed, blind panel that writes `judge.md`)", skill)
        self.assertIn("`judge-single.md`", skill)

    def test_s4_the_sandbox(self):
        readme = _flat(_read("README.md"))
        self.assertNotIn("even if it tries", readme)
        self.assertNotIn("anything it shouldn't", readme)
        self.assertNotIn("its writes stay in the project", readme)
        summary = readme[readme.index("**The sandbox**"):readme.index("**Providers")]
        usage = readme[readme.index("The sandbox uses Linux bubblewrap"):readme.index("When you want to drive yourself")]
        for part, text in (("summary", summary), ("usage", usage)):
            for name in (".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/", ".claude/bin/",
                         ".mcp.json", "uncontained", "~/.local/share/playbook", "/tmp"):
                with self.subTest(part=part, name=name):
                    self.assertIn(name, text)
        # the git part holds only with a non-setuid bubblewrap >= 0.8.0, in the summary too (impl round 2, sonnet)
        self.assertIn("0.8.0", summary)
        arch = _flat(_read("docs/architecture.md"))
        box = arch[arch.index("## The sandbox"):arch.index("## Judges")]
        self.assertIn("`.claude/settings.local.json`", box)
        kernel = box[box.index("What's enforced at the kernel level"):]
        self.assertIn("~/.local/share/playbook", kernel[:kernel.index(". ")])   # impl round 2, opus, grok

    def test_s5_a_single_judge_as_evidence(self):
        for rel in ("plugins/playbook/scripts/CLAUDE.md.template", "plugins/playbook/skills/playbook/SKILL.md",
                    "docs/cli.md"):
            text = _flat(_read(rel))
            with self.subTest(where=rel):
                self.assertNotIn("a second opinion, never a close's evidence", text)
                self.assertNotIn("does not list an assertive, irreversible or unclassified task's class", text)
                self.assertIn("under the seeded `panel_required_for` never a close's evidence", text)
                self.assertIn("only where the policy requires no panel for that close", text)
                self.assertIn("an unclassified task needs the panel whenever the list names any class", text)
        # the evidence paragraph says the same (impl round 2, agy)
        conf = _flat(_read("docs/configuration.md"))
        para = conf[conf.index("Review evidence is a record the CLI wrote"):]
        self.assertIn("only where the policy requires no panel for that close", para[:para.index("Setting `## Risk`")])

    def test_s6_tasks_red(self):
        readme = _flat(_read("README.md"))
        self.assertNotIn("runs each row on the code before the fix", readme)
        self.assertNotIn("on newer code", readme)
        self.assertIn("in the project as it is", readme)
        self.assertIn("on another tree", readme)
        # red.py `row_state`: a later green with the same tests digest on another tree proves the row, a tree whose
        # code did not change included (impl round 1, grok) — the guide says what the receipt reads
        guide = _flat(template.guide_text())
        self.assertNotIn("on newer code", guide)
        self.assertIn("a green on another tree with the same test files", guide)


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
        targets = [claude / "settings.json", claude / "settings.local.json", claude / "hooks" / "h.sh",
                   claude / "bin" / "tasks", self.project / ".mcp.json"]
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

    @unittest.skipUnless(_bwrap_works(), "bwrap not available or not usable here")
    def test_s4_control_a_worker_sandbox_writes_nothing_else_under_home(self):
        # what reaches the host is the claim: a test's HOME lies under /tmp, the run's own private tmpfs, where a
        # write may "succeed" and is gone with the run (a real HOME refuses it: evidence/probe_sandbox.out)
        outside = self.home / "outside.txt"
        write = ("import sys\n"
                 "try:\n"
                 "    open(sys.argv[1], 'w', encoding='utf-8').write('x')\n"
                 "except OSError:\n"
                 "    pass\n")
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            argv = sandbox.build_bwrap_argv(self.project, None, [sys.executable, "-c", write, str(outside)],
                                            None, project_writable=True, agent="claude")
        self._run(argv)
        self.assertFalse(outside.exists())


@unittest.skipUnless(_bwrap_works(), "bwrap not available or not usable here")
class ThePromptAndTheChat(unittest.TestCase):
    """S3 (impl round 1, codex): what a judge's prompt carries, at the process boundary — the real CLI and review path,
    the real sandbox, the stand-in judge (`tests/_judge_stub.py`) saving the prompt it received."""

    CANARY = "CANARY-348-only-in-the-chat-log"

    def setUp(self):
        from tests._judge_stub import clean_env, stub_bin, stub_home
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.p = base / "proj"
        self.p.mkdir()
        home, self.out = stub_home(base)
        env = clean_env(HOME=str(home))
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.p, check=True, env=env)
        (self.p / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.p, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "c0"], cwd=self.p, check=True, env=env)
        (self.p / ".agent" / "tasks").mkdir(parents=True)
        (self.p / ".agent" / "models.json").write_text(
            json.dumps({"panel": ["opus", "sonnet", "haiku"], "default_judge": "sonnet"}), encoding="utf-8")
        self.env = clean_env(HOME=str(home), PATH=f"{stub_bin(base / 'bin')}{os.pathsep}{env['PATH']}",
                             PB_STUB_DIR=str(self.out))
        r = self.tasks("new", "light", "probe", "a probe task for the judges")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (self.p / ".agent" / "chat_log.md").write_text(f"**[M1]** [2026-10-31 12:00:00]\n```text\n{self.CANARY}\n```\n",
                                                       encoding="utf-8")

    def tasks(self, *args):
        from tests._judge_stub import TASKS
        return subprocess.run([str(TASKS), *args], cwd=self.p, env=self.env, capture_output=True, text=True,
                              timeout=600)

    def prompts(self) -> "list[str]":
        from tests._judge_stub import results
        return results(self.out, "prompt")

    def test_s3_a_task_review_prompt_holds_no_chat_log_a_consultation_does(self):
        for review in (("impl-review", "1"), ("panel-review", "1", "--mode", "impl", "--models", "sonnet")):
            r = self.tasks(*review)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        task_prompts = self.prompts()
        self.assertEqual(len(task_prompts), 2, task_prompts)
        for prompt in task_prompts:
            self.assertNotIn(self.CANARY, prompt)
        r = self.tasks("panel-review", "--prompt", "Is the README accurate?", "--models", "sonnet")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        asked = self.prompts()[len(task_prompts):]
        self.assertTrue(asked, r.stdout + r.stderr)
        for prompt in asked:
            self.assertIn(self.CANARY, prompt)


if __name__ == "__main__":
    unittest.main()
