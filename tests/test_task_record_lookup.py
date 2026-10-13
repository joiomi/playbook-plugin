"""The hooks find the active task's record by its DIRECTORY's own name (task 192, PLAN S14 T3, item #25).

`state-echo-hook`, `stop-hook` and `task-gate-hook` located the record with
`find "$AGENT_DIR/tasks" -maxdepth 2 -name task.md -path "*/${TASK}-*/*" | head -1`. `-path` matches the WHOLE
path, so in a project whose own directory is named `<N>-…` (`007-proj/`) with task 007 active, every record of the
project matched and the hook read whichever `find` listed first. With no 007 record at all another task's record
still resolved: the gate authorized a code edit for a task that does not exist, the stop gate counted another
task's gates, the echo showed another task's gate. Now one lib function (`gate-echo-lib.sh` `task_record_file`)
matches `<tasks>/<N>-*/task.md` on the directory's name, in the CLI's order (`sorted()`, i.e. `LC_ALL=C`).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "playbook" / "scripts"
SID = "t192-lookup"
OPEN = "# {n} - {name}\n\n## Status\nin_progress\n\n## Work Plan\n- [ ] {name} gate open\n"
COMPLETE = "# {n} - {name}\n\n## Status\nin_progress\n\n## Work Plan\n- [x] {name} gate done\n"
DONE = "# {n} - {name}\n\n## Status\ndone\n\n## Work Plan\n- [x] {name} gate done\n"


class _Project(unittest.TestCase):
    PROJECT_NAME = "007-proj"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / self.PROJECT_NAME
        self.tasks = self.project / ".agent" / "tasks"
        self.tasks.mkdir(parents=True)
        self.session = self.project / ".agent" / "sessions" / SID
        self.session.mkdir(parents=True)
        (self.session / "current_state").write_text("007\n", encoding="utf-8")

    def record(self, dirname, template):
        n, name = dirname.split("-", 1)
        d = self.tasks / dirname
        d.mkdir()
        (d / "task.md").write_text(template.format(n=n, name=name), encoding="utf-8")
        return d / "task.md"

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE")}
        env["PLAYBOOK_SESSION_ID"] = SID
        env["HOME"] = self._tmp.name
        env.update(extra)
        return env

    def hook(self, name, payload, **extra):
        return subprocess.run([bash_or_skip(), str(SCRIPTS / name)], cwd=self.project, env=self.env(**extra),
                              input=json.dumps(payload), capture_output=True, text=True, timeout=60)

    def gate(self, **extra):
        return self.hook("task-gate-hook", {"tool_name": "Edit",
                                            "tool_input": {"file_path": str(self.project / "src" / "app.py")}}, **extra)

    def stop(self, **extra):
        return self.hook("stop-hook", {"hook_event_name": "Stop", "stop_hook_active": False}, **extra)

    def echo(self, **extra):
        r = self.hook("state-echo-hook", {"tool_name": "Read", "tool_input": {"file_path": "x"}}, **extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")][-1]
        return json.loads(line)["hookSpecificOutput"]["additionalContext"]


class NoRecordOfTheActiveTask(_Project):
    """Pointer 007, no 007 record, the project directory named `007-proj`: nothing resolves."""

    def setUp(self):
        super().setUp()
        self.record("003-alpha", OPEN)
        self.record("012-gamma", OPEN)

    def test_the_gate_refuses_a_code_edit(self):
        r = self.gate()
        self.assertEqual(r.returncode, 2, f"a pointer to a task with no record authorized a code edit: {r.stderr}")
        self.assertIn("No active task", r.stderr)

    def test_the_stop_is_let_through(self):
        r = self.stop()
        self.assertEqual(r.returncode, 0, f"the stop gate counted another task's gates: {r.stderr}")

    def test_the_echo_says_not_found(self):
        ctx = self.echo()
        self.assertIn("task file not found", ctx)
        self.assertNotIn("alpha", ctx)
        self.assertNotIn("gamma", ctx)

    def test_a_stray_record_in_the_tasks_directory_is_not_the_task(self):
        (self.tasks / "task.md").write_text(OPEN.format(n="000", name="stray"), encoding="utf-8")
        self.assertEqual(self.gate().returncode, 2)
        self.assertIn("task file not found", self.echo())


class TheActiveTaskAmongOthers(_Project):
    """Pointer 007 with records 003, 007 and 012 in `007-proj`: each hook reads 007's record."""

    def test_the_stop_reads_the_active_record(self):
        self.record("003-alpha", OPEN)
        self.record("007-beta", COMPLETE)
        self.record("012-gamma", OPEN)
        r = self.stop()
        self.assertEqual(r.returncode, 0, f"007 has no open gate, yet the stop was refused: {r.stderr}")

    def test_the_echo_shows_the_active_record(self):
        self.record("003-alpha", OPEN)
        self.record("007-beta", OPEN)
        self.record("012-gamma", OPEN)
        ctx = self.echo()
        self.assertIn("beta gate open", ctx)
        self.assertNotIn("alpha", ctx)
        self.assertNotIn("gamma", ctx)

    def test_the_gate_reads_the_active_record_status(self):
        self.record("003-alpha", OPEN)
        self.record("007-beta", DONE)
        self.record("012-gamma", OPEN)
        r = self.gate()
        self.assertEqual(r.returncode, 2, f"a pointer to a DONE task authorized a code edit: {r.stderr}")
        rows = [json.loads(x) for x in (self.project / ".agent" / "journal" / "enforcement.jsonl")
                .read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual([x["reason"] for x in rows if x.get("hook") == "task-gate"], ["pointer to done task"])

    def test_a_real_active_record_still_authorizes(self):
        # Negative control: the lookup still finds the active task.
        self.record("003-alpha", DONE)
        self.record("007-beta", OPEN)
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_linked_task_directory_is_not_followed(self):
        # `find` (no -L) never descended a symbolic link to a directory; the lookup keeps that.
        real = Path(self._tmp.name) / "elsewhere" / "007-beta"
        real.mkdir(parents=True)
        (real / "task.md").write_text(OPEN.format(n="007", name="beta"), encoding="utf-8")
        (self.tasks / "007-beta").symlink_to(real, target_is_directory=True)
        self.assertEqual(self.gate().returncode, 2)
        self.assertIn("task file not found", self.echo())


class TheCliOrder(_Project):
    """Two records with the same number: the hooks take the one the CLI's `sorted()` takes, whatever the locale."""

    PROJECT_NAME = "proj"

    def test_the_first_in_byte_order(self):
        r = subprocess.run(["locale", "-a"], capture_output=True, text=True)
        names = {x.lower().replace("-", "") for x in r.stdout.split()}
        if "en_us.utf8" not in names:
            self.skipTest("en_US.UTF-8 is not installed: the byte order and the locale's order agree here")
        self.record("007-a", OPEN)
        self.record("007-B", OPEN)
        self.assertEqual(sorted(p.name for p in self.tasks.iterdir()), ["007-B", "007-a"])
        ctx = self.echo(LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8")
        self.assertIn("B gate open", ctx)


class TheLookupFunction(unittest.TestCase):
    def lookup(self, agent, task):
        r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; task_record_file "$2" "$3"', "_",
                            str(SCRIPTS / "gate-echo-lib.sh"), str(agent), task],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_a_pointer_that_is_not_a_number_finds_nothing(self):
        # Impl panel r1 (fable): the function itself refuses a glob or an empty number — the N2 self-authorization
        # must not rest on every caller's own digit check. (The quoted prefix already kept a glob literal; an EMPTY
        # number matched any directory named `-…`, the case the old function returned.)
        with tempfile.TemporaryDirectory() as t:
            agent = Path(t) / "proj" / ".agent"
            for name in ("001-a", "002-b", "-x"):
                (agent / "tasks" / name).mkdir(parents=True)
                (agent / "tasks" / name / "task.md").write_text("x", encoding="utf-8")
            for bad in ("*", "?", "[0-9]*", "", "00*", "1 2"):
                with self.subTest(task=bad):
                    self.assertEqual(self.lookup(agent, bad), "")
            self.assertEqual(self.lookup(agent, "001"), str(agent / "tasks" / "001-a" / "task.md"))

    def test_a_name_that_extends_another_is_ordered_as_the_cli_orders_it(self):
        # Impl panel r1 (opus), measured: bash sorted `007-fix-2/task.md` before `007-fix/task.md` (`-` < `/`) while
        # the CLI's sorted(Path) compares the directory names and takes `007-fix`.
        with tempfile.TemporaryDirectory() as t:
            agent = Path(t) / "proj" / ".agent"
            for name in ("007-fix-2", "007-fix"):
                (agent / "tasks" / name).mkdir(parents=True)
                (agent / "tasks" / name / "task.md").write_text("x", encoding="utf-8")
            cli = sorted((agent / "tasks").glob("*/task.md"))[0]
            self.assertEqual(cli.parent.name, "007-fix")
            self.assertEqual(self.lookup(agent, "007"), str(cli))

    def test_only_the_directory_name_is_matched(self):
        with tempfile.TemporaryDirectory() as t:
            agent = Path(t) / "007-proj" / ".agent"
            (agent / "tasks" / "003-alpha").mkdir(parents=True)
            (agent / "tasks" / "003-alpha" / "task.md").write_text("x", encoding="utf-8")
            r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; task_record_file "$2" "$3"', "_",
                                str(SCRIPTS / "gate-echo-lib.sh"), str(agent), "007"],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
            r = subprocess.run([bash_or_skip(), "-c", 'source "$1"; task_record_file "$2" "$3"', "_",
                                str(SCRIPTS / "gate-echo-lib.sh"), str(agent), "003"],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.stdout.strip(), str(agent / "tasks" / "003-alpha" / "task.md"))


if __name__ == "__main__":
    unittest.main()
