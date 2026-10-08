"""NEW-1 (1.5.11 audit): the code-edit gate's .agent/.claude exemption must not
be defeated by `..` traversal.

The gate exempted any path CONTAINING `.agent`/`.claude` as a component, without
resolving `..`. So `.agent/../src/main.py` was exempted (exit 0) while the write
landed on the real code file `src/main.py` — a one-string bypass of the core
"no code without an active task" boundary. Reachable by any agent/prompt-injection
that can name a path.
"""

from __future__ import annotations

import json
import os
import subprocess
from tests._bashcheck import bash_or_skip
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "plugins" / "playbook" / "scripts" / "task-gate-hook"

import sys  # noqa: E402
sys.path.insert(0, str(REPO_ROOT / "plugins" / "playbook"))
from provider.policy import _is_management_path  # noqa: E402


class CodexManagementPathTraversal(unittest.TestCase):
    """NEW-1 codex twin: _is_management_path must resolve `..` before exempting."""

    def test_traversal_out_of_agent_is_not_management(self):
        self.assertFalse(_is_management_path("/proj/.agent/../src/main.py"))
        self.assertFalse(_is_management_path("/proj/.claude/../src/main.py"))

    def test_genuine_management_paths_still_true(self):
        self.assertTrue(_is_management_path("/proj/.agent/tasks/001-x/task.md"))
        self.assertTrue(_is_management_path("/proj/.claude/settings.json"))

    def test_plain_code_path_is_not_management(self):
        self.assertFalse(_is_management_path("/proj/src/main.py"))

    def test_deep_traversal_out_of_management_is_not_management(self):
        """`..` that starts INSIDE the management tree must still resolve out."""
        self.assertFalse(
            _is_management_path("/proj/.agent/tasks/../../src/main.py"))
        self.assertFalse(
            _is_management_path("/proj/x/.claude/../../src/a.py"))

    def test_a_backslash_spelled_traversal_is_one_file_name(self):
        """`.agent\\..\\src\\main.py` has no `/`: on Linux it is a single file NAME,
        so no component of it is `.agent` (task 158 — up to 1.5.47 the `\\` were
        turned into `/` first and the traversal was then resolved; either way it
        is not a management path)."""
        self.assertFalse(_is_management_path(".agent\\..\\src\\main.py"))

    def test_traversal_that_lands_back_inside_agent_is_management(self):
        # Negative control for the traversal rule: resolution, not the mere
        # presence of `..`, decides — this path genuinely ends under .agent/.
        self.assertTrue(_is_management_path("/proj/.agent/../.agent/x"))

    def test_lookalike_components_are_not_management(self):
        self.assertFalse(_is_management_path("/proj/.agentx/file.py"))
        self.assertFalse(_is_management_path("/proj/my.claude/file.py"))

    def test_cr_suffix_does_not_forge_or_break_management(self):
        # The bare-CR trick (fix/cr-path-parity) rides the TRAILING path
        # component, so it can neither forge a `.agent`/`.claude` component nor
        # break a genuine one that sits earlier in the path — the management
        # exemption stays consistent under it (no divergence, no fix needed).
        # A trailing CR on a code file is still NOT management (would fall to the
        # code-file test and gate):
        self.assertFalse(_is_management_path("/proj/src/main.py\r"))
        self.assertFalse(_is_management_path("/proj/src/main.py\r\n"))
        # A CR-mangled `.agent`/`.claude` token is not the component (fail-safe):
        self.assertFalse(_is_management_path("/proj/.agent\r/main.py"))
        # A genuine management path with a trailing CR on the LAST component is
        # still management — the exempted component is unmangled:
        self.assertTrue(_is_management_path("/proj/.agent/tasks/001-x/task.md\r"))
        self.assertTrue(_is_management_path("/proj/.claude/settings.json\r\n"))

    def test_unicode_management_path_is_management(self):
        self.assertTrue(
            _is_management_path("/proj/.agent/tasks/001-задача/task.md"))

    def test_a_backslash_is_a_file_name_character_not_a_separator(self):
        # Task 158 (Linux only): `src/a\.agent\x.py` is ONE file name inside src/.
        # Up to 1.5.47 the classifier turned every `\` into `/` first (Windows
        # spellings), so that name read as a path THROUGH `.agent/` and a codex
        # patch to it was exempt from the edit gate with no active task.
        self.assertFalse(_is_management_path("src/a\\.agent\\x.py"))
        self.assertFalse(_is_management_path("/proj/src/x\\.claude\\settings.py"))
        self.assertFalse(_is_management_path(".agent\\tasks\\001-x\\main.py"))
        # Controls: the real directories stay management, a `\` inside one changes nothing.
        self.assertTrue(_is_management_path("/proj/.agent/a\\b.py"))
        self.assertTrue(_is_management_path(".claude/settings.json"))


class GatePathTraversal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".agent" / "tasks").mkdir(parents=True)

    def _run(self, file_path):
        env = dict(os.environ)
        env["PLAYBOOK_SESSION_ID"] = "pid-trav-test"
        env.pop("BASH_ENV", None)
        return subprocess.run(
            [bash_or_skip(), str(HOOK)], cwd=self.project, env=env, text=True,
            input=json.dumps({"tool_name": "Edit",
                              "tool_input": {"file_path": file_path}}),
            capture_output=True)

    def test_agent_traversal_to_code_is_blocked(self):
        p = str(self.project / ".agent" / ".." / "src" / "main.py")
        r = self._run(p)
        self.assertEqual(r.returncode, 2,
                         f".agent/../ traversal bypassed the gate (rc={r.returncode})")

    def test_claude_traversal_to_code_is_blocked(self):
        p = str(self.project / ".claude" / ".." / "src" / "main.py")
        r = self._run(p)
        self.assertEqual(r.returncode, 2,
                         f".claude/../ traversal bypassed the gate (rc={r.returncode})")

    def test_genuine_agent_path_still_allowed(self):
        # Negative control: a real .agent/ path is still exempt.
        p = str(self.project / ".agent" / "tasks" / "001-x" / "task.md")
        r = self._run(p)
        self.assertEqual(r.returncode, 0,
                         f"genuine .agent path was blocked: {r.stderr}")

    def test_plain_code_file_still_blocked(self):
        # Negative control: a normal code file with no task still blocks.
        r = self._run(str(self.project / "src" / "main.py"))
        self.assertEqual(r.returncode, 2)

    def test_deep_traversal_from_inside_agent_is_blocked(self):
        p = str(self.project / ".agent" / "tasks" / ".." / ".." / "src" / "main.py")
        r = self._run(p)
        self.assertEqual(r.returncode, 2,
                         f".agent/tasks/../../ traversal bypassed the gate (rc={r.returncode})")

    def test_a_backslash_named_code_file_is_gated(self):
        # Task 158: the bash gate never read `\` as a separator — this is the
        # behaviour the codex twin (provider/policy.py) now matches.
        r = self._run(str(self.project / "src") + "/a\\.agent\\module.py")
        self.assertEqual(r.returncode, 2,
                         f"a file NAMED `a\\.agent\\module.py` was exempted (rc={r.returncode})")

    def test_lookalike_agent_dir_is_not_exempt(self):
        r = self._run(str(self.project / ".agentx" / "file.py"))
        self.assertEqual(r.returncode, 2,
                         f".agentx/ lookalike was exempted (rc={r.returncode})")

    def test_unicode_management_path_still_allowed(self):
        # Negative control: a genuine .agent/ path stays exempt regardless of
        # the bytes inside the task-dir name.
        p = str(self.project / ".agent" / "tasks" / "001-задача" / "task.md")
        r = self._run(p)
        self.assertEqual(r.returncode, 0,
                         f"unicode .agent path was blocked: {r.stderr}")

    def test_bare_cr_code_path_still_blocked(self):
        # End-to-end (fix/cr-path-parity): a code file with a trailing CR and NO
        # code-dir component reaches is_code_file_path as the classifier's only
        # gate. Before the fix bash kept the \r inside the extension, missed the
        # code-ext match, found no code dir, and FAILED OPEN (rc 0) — while the
        # Codex/Python twin gated the same path. Must block with no active task.
        r = self._run(str(self.project / "main.py") + "\r")
        self.assertEqual(r.returncode, 2,
                         f"bare-CR code path bypassed the gate (rc={r.returncode})")

    def test_crlf_code_path_still_blocked(self):
        r = self._run(str(self.project / "main.py") + "\r\n")
        self.assertEqual(r.returncode, 2,
                         f"CRLF code path bypassed the gate (rc={r.returncode})")

    def test_cr_doc_path_still_allowed(self):
        # Negative control: a trailing CR on a NON-code file must not start
        # blocking edits it never blocked — README.md\r stays exempt.
        r = self._run(str(self.project / "README.md") + "\r")
        self.assertEqual(r.returncode, 0,
                         f"CR doc path was wrongly blocked: {r.stderr}")


class ManualTaskDirGuard(unittest.TestCase):
    """PLAN S1d (task 073 flag C15, measured live by task 079 and again by task
    080): "don't create task directories manually" matched the WHOLE command
    text, so a temp-dir fixture outside the project (`<tmp>/.agent/tasks/001-x`)
    was refused. Only a target that is — or may be — under the project root
    is a task dir. The literal `mkdir` is assembled at runtime so this file's
    own text never trips the guard it tests."""

    MK = "mk" + "dir"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Forward-slash spellings: POSIX shell quoting eats backslashes, so a
        # Windows `C:\\…` literal is not what anyone types into Git Bash.
        self.base = Path(self._tmp.name)
        (self.base / "proj" / ".agent" / "tasks").mkdir(parents=True)
        self.project = (self.base / "proj").as_posix()
        self.outside = (self.base / "fixture").as_posix()   # a sibling temp dir, NOT under proj

    def _run(self, command):
        env = dict(os.environ, PLAYBOOK_SESSION_ID="pid-mkdir-test")
        env.pop("BASH_ENV", None)
        return subprocess.run(
            [bash_or_skip(), str(HOOK)], cwd=self.base / "proj", env=env, text=True,
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
            capture_output=True)

    def test_temp_dir_agent_tasks_is_not_a_task_dir(self):
        for cmd in (f"{self.MK} -p {self.outside}/.agent/tasks/001-x",
                    f"{self.MK} -p {self.outside}/.agent/tasks",
                    f"{self.MK} -p {self.outside}/.agent/alice/tasks/001-x",
                    f"{self.MK} -p '{self.outside}/.agent/tasks/001-x'",
                    f"{self.MK} -m 755 -p {self.outside}/.agent/tasks/001-x/sub",
                    f"/bin/{self.MK} -p {self.outside}/.agent/tasks/001-x"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 0, f"temp fixture refused: {cmd!r}: {r.stderr}")

    def test_only_a_simple_mkdir_is_ever_allowed(self):
        # Round 2 (2 seats): the helper judges the filesystem BEFORE the command
        # runs, so an earlier step of a compound command can repoint the path
        # (`ln -s <proj> <tmp>/late; mkdir -p <tmp>/late/...`). Only a single,
        # simple mkdir with no expansion anywhere is judged; the rest is refused.
        late = f"{self.base.as_posix()}/late"
        tail = f"{self.MK} -p {self.outside}/.agent/tasks/001-x"
        for cmd in (f"ln -s {self.project} {late}; {self.MK} -p {late}/.agent/tasks/999-x",
                    f"cd /tmp && {tail}",
                    f"{tail} | cat",
                    f"{tail} &",
                    f"({tail})",
                    # Task 110: was `echo $(ln -s …) {tail}` — there mkdir is only an
                    # ARGUMENT of echo and creates nothing (item 31's narrowing
                    # allows it); the substitution-then-mkdir shape it meant is this:
                    f"echo $(ln -s {self.project} {late}) && {tail}",
                    f"true\n{tail}",
                    f"X=1 {tail}"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 2, f"a non-simple command was judged: {cmd!r}")

    def test_quoted_escaped_and_globbed_spellings_are_still_blocked(self):
        # Task 085 G2 (083 W10, sol-medium #2): the trigger was a raw-text regex,
        # so a shell spelling of `.agent/tasks` never reached the helper — the
        # shell dequotes / unescapes / globs it back into the real path.
        mk = self.MK
        for cmd in (f"{mk} -p .ag'ent/tasks'/999-x", f"{mk} -p .ag\\ent/tasks/999-x",
                    f'{mk} -p ".agent"/tasks/999-x', f'{mk} -p {self.project}/.ag"ent"/tasks/999-x',
                    f"{mk} -p .ag$'e'nt/tasks/999-x", f"{mk} -p .ag?nt/tasks/999-x",
                    f"{mk} -p .agent/ta''sks/999-x"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 2, f"shell spelling of a task dir allowed: {cmd!r}")

    def test_expanded_and_respelled_forms_are_blocked(self):
        # Task 085 round 2, T2 (impl panel opus #1, sol-high #1, sol-medium #4):
        # each of these makes bash create a real task dir; all were allowed by
        # 1.5.45 AND by 085 round 1 (measured in scratchpad triage_085.sh).
        mk = self.MK
        for cmd in (f"{mk} -p .agent/ta$'\\x73'ks/999-x", f"{mk} -p .agent/ta$'\\163'ks/999-x",
                    f"{mk} -p .agent/ta{{sk,zz}}s/999-x", f"{mk} -p .agent/ta{{r..t}}ks/999-x",
                    f"{mk} -p .{{a,b}}gent/{{x,tasks}}/999-x",
                    "m\\kdir -p .agent/tasks/999-x", "m'kdir' -p .agent/tasks/999-x",
                    "$'\\x6d'kdir -p .agent/tasks/999-x"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 2, f"task dir spelling allowed: {cmd!r}")

    def test_expansions_that_name_no_task_dir_stay_allowed(self):
        mk = self.MK
        for cmd in (f"{mk} -p src/{{a,b}}/lib", f"{mk} -p build/{{1..3}}",
                    "printf $'a\\tb\\n' > notes.txt", f"{mk} -p $'logs'/today",
                    f"{mk} -p .agent/{{notes,cache}}"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 0, f"ordinary command refused: {cmd!r}: {r.stderr}")

    def test_round3_spellings_are_blocked(self):
        # Task 085 round 3 (impl panel round 2): each makes bash create a real
        # task dir (measured in scratchpad triage_r2.sh) or, for `.AGENT/TASKS`,
        # lands in the real tree on a case-insensitive disk (U7).
        mk = self.MK
        for cmd in ("D=agent; " + mk + " -p .$D/tasks/999-x",            # U2
                    "T=tasks; " + mk + " -p .agent/$T/999-x",           # U2
                    "mk{d,z}ir -p .agent/tasks/999-x",                  # U3
                    "m{k,foo}dir -p .agent/tasks/999-x",                # U3
                    f"{mk} -p .agent/ta$'\\U00000073'ks/999-x",         # U5
                    f"{mk} -p .AGENT/TASKS/999-x"):                     # U7
            r = self._run(cmd)
            self.assertEqual(r.returncode, 2, f"task dir spelling allowed: {cmd!r}")

    def test_round3_lookalikes_are_allowed(self):
        # U1: round 1's two-substring test blocked names that merely contain
        # `.ag…` and `tasks` (1.5.45 allowed the first and third).
        mk = self.MK
        for cmd in (f"{mk} -p .agentic/tasks", f"{mk} -p .agent-stuff/my-tasks",
                    f"{mk} -p .agent/tasks_archive", f'{mk} -p "$D/build"',
                    "echo {a,b}", "jq '{x: 1}' f.json"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 0, f"ordinary command refused: {cmd!r}: {r.stderr}")

    def test_a_large_unrelated_brace_expansion_is_allowed(self):
        # Round 3 regression (impl panel round 3, opus): the `{` trigger plus a
        # fail-closed expansion cap refused any command with a big brace list.
        for cmd in ("touch f{1..1000}", f"{self.MK} -p logs/{{1..1000}}"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 0, f"ordinary command refused: {cmd!r}: {r.stderr}")

    def test_the_bound_a_variable_assembled_command_name(self):
        # Task 100 impl panel r1 (codex-high, measured): a command NAME assembled
        # in a variable exists only after the shell evaluates it — the same
        # architectural bound as the glob below and as command_guard's
        # TheArchitecturalBound. Documented on PB-TASK-DIR-GUARD; if this starts
        # blocking, the bound moved — update the ledger row, do not delete this.
        r = self._run('D=mk; D+=dir; "$D" -p .agent/tasks/999-x')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_bound_a_globbed_command_name(self):
        # U8, a documented bound (same class as command_guard's
        # TheArchitecturalBound): a glob in the command NAME only resolves
        # against the filesystem at run time. If this starts blocking, the
        # bound moved — update docs/architecture.md, do not delete this test.
        r = self._run("/bin/mk?ir -p .agent/tasks/999-x")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_task_dir_named_on_an_earlier_line_is_blocked(self):
        # Task 085 G2, measured after the fix: the old per-line trigger never saw a
        # path assigned on one line and created on the next (allowed by 1.5.45).
        # The accepted cost of judging the whole command: prose that merely
        # mentions both words on different lines is refused too.
        r = self._run(f'D=.agent/tasks/999-x\n{self.MK} -p "$D"')
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_ordinary_mkdirs_are_not_blocked(self):
        # the widened trigger (any mkdir) must not turn ordinary directory
        # creation into a refusal: only a spelling that names `.agent…tasks` is judged
        mk = self.MK
        for cmd in (f"{mk} -p build", f'{mk} -p "$D/build"', f"cd /tmp && {mk} y",
                    f"{mk} -p {self.outside}/logs", f"{mk} -p $(mktemp -d)/y",
                    f"{mk} -p docs/tasks", f"{mk} -p .agentx/notes"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 0, f"ordinary mkdir refused: {cmd!r}: {r.stderr}")

    def test_project_agent_tasks_is_still_blocked(self):
        mk = self.MK
        for cmd in (f"{mk} -p {self.project}/.agent/tasks/001-x",
                    f"{mk} -p .agent/tasks/001-x",
                    f"{mk} -p ./.agent/tasks/001-x",
                    f"{mk} -p {self.project}/.agent/alice/tasks/001-x",
                    f"{mk} -p ~/.agent/tasks/001-x",
                    f"{mk} -p $HOME/.agent/tasks/001-x",
                    f"{mk} -p \"$PWD\"/.agent/tasks/001-x",
                    f"{mk} -p {self.outside}/../proj/.agent/tasks/001-x",
                    f"{mk} -p {self.outside}/.agent/tasks/001-x && {mk} -p .agent/tasks/002-y",
                    f"{mk} -p {self.outside}/.agent/tasks/001-x {self.project}/.agent/tasks/002-y",
                    # Round 2 (grok): spellings the old trigger never matched.
                    f"{mk} -p {self.project}/.agent/foo/../tasks/001-x",
                    f"{mk} -p {self.project}//.agent//tasks//001-x"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 2, f"in-project task dir allowed: {cmd!r}")
            self.assertIn("task directories manually", r.stderr)

    def test_symlink_into_project_is_still_blocked(self):
        # A literal absolute path OUTSIDE the project that resolves INTO it is
        # still a task dir — inside-ness is judged through the filesystem too.
        link = self.base / "link-to-proj"
        try:
            os.symlink(self.base / "proj", link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this filesystem")
        r = self._run(f"{self.MK} -p {link.as_posix()}/.agent/tasks/001-x")
        self.assertEqual(r.returncode, 2, f"symlink into the project allowed: {r.stderr}")
        # Control: the helper still allows a real outside path in the same run shape.
        r = self._run(f"{self.MK} -p {self.outside}/.agent/tasks/001-x")
        self.assertEqual(r.returncode, 0, r.stderr)

    def _link_or_skip(self, target, link):
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this filesystem")

    def test_symlink_into_project_subdirectory_is_still_blocked(self):
        # Round 1 (4 seats): samefile against the ROOT missed a link into a subdir.
        (self.base / "proj" / "src").mkdir()
        link = self.base / "sublink"
        self._link_or_skip(self.base / "proj" / "src", link)
        r = self._run(f"{self.MK} -p {link.as_posix()}/.agent/tasks/001-x")
        self.assertEqual(r.returncode, 2, f"subdir symlink into the project allowed: {r.stderr}")

    def test_symlinked_alias_with_dotdot_through_missing_dir_is_blocked(self):
        # CI macOS (/var -> /private/var): the project is reached through its
        # physical path, the token through an alias plus `missing/..`.
        alias = self.base / "alias"
        self._link_or_skip(self.base, alias)
        r = self._run(f"{self.MK} -p {alias.as_posix()}/missing/../proj/.agent/tasks/001-x")
        self.assertEqual(r.returncode, 2, f"alias + missing/.. allowed: {r.stderr}")

    def test_shell_expansion_is_not_judged_literally(self):
        # Round 1: the shell expands these; a literal reading judged them outside.
        (self.base / "fixture").mkdir()
        b = self.base.as_posix()
        for cmd in (f"{self.MK} -p {b}/{{fixture,proj}}/.agent/tasks/001-x",
                    f"{self.MK} -p {b}/*/.agent/tasks/001-x",
                    f"{self.MK} -p {b}/pro?/.agent/tasks/001-x",
                    f"{self.MK} -p {b}/[p]roj/.agent/tasks/001-x"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 2, f"shell expansion judged literally: {cmd!r}")

    def test_drive_letter_is_relative_on_posix(self):
        # Round 1: `C:/x` is a RELATIVE path on POSIX — it lands under the cwd.
        r = self._run(f"{self.MK} -p C:/tmp/.agent/tasks/001-x")
        self.assertEqual(r.returncode, 2, f"POSIX-relative C:/ path allowed: {r.stderr}")

    def test_guard_runs_before_session_injection(self):
        # Round 1: `tasks status; …` hit the session-injection early allow first.
        for cmd in (f"tasks status; {self.MK} -p {self.project}/.agent/tasks/999-x",
                    f".claude/bin/tasks work 3 && {self.MK} -p .agent/tasks/999-x"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 2, f"guard bypassed via injection: {cmd!r}")
        # Control: the injection itself still works for a plain tasks call.
        r = self._run("tasks status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PLAYBOOK_SESSION_ID", r.stdout)


class ManualTaskDirHelperPortability(unittest.TestCase):
    """task-dir-target.py judges the command it reads on stdin."""


    def test_command_is_read_from_stdin_not_the_environment(self):
        # Task 080: the command is read from stdin, never from the environment
        # (Git Bash, up to 1.5.47, rewrote an env value starting with `/`). A
        # mangled PB_CMD in the environment, the real command on stdin.
        import sys as _sys
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            (proj / ".agent" / "tasks").mkdir(parents=True)
            real = f"/bin/{'mk' + 'dir'} -p {(Path(tmp) / 'fixture').as_posix()}/.agent/tasks/001-x"
            env = dict(os.environ, PB_PROJECT=proj.as_posix(),
                       PB_CMD="C:/Program Files/Git/usr/bin/" + real[len("/bin/"):])
            r = subprocess.run([_sys.executable, str(HOOK.parent / "task-dir-target.py")],
                               input=real, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"stdin command not judged: {r.stderr}")


# ── task 110 (PLAN S11 fix batch, group GUARD): the task-dir guard ────────────
# Source: task 109's gauntlet (REPORT.md G2-02, G2-13; parked R8, R15, item 31)
# and 085 round 3 V4. Written before the fix and seen failing (record: task 110).

class TaskDirGuardFixBatch110(unittest.TestCase):
    MK = "mk" + "dir"
    TD = ".agent/" + "tasks"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        (self.base / "proj" / ".agent" / "tasks").mkdir(parents=True)

    def _run(self, command):
        env = dict(os.environ, PLAYBOOK_SESSION_ID="pid-mkdir-test")
        env.pop("BASH_ENV", None)
        return subprocess.run(
            [bash_or_skip(), str(HOOK)], cwd=self.base / "proj", env=env, text=True,
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
            capture_output=True)

    def _journal(self):
        p = self.base / "proj" / ".agent" / "journal" / "enforcement.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_a_variable_before_a_final_dotagent_is_not_a_task_dir(self):
        # G2-02 / R15 (a REGRESSION against 1.5.45, 109 H09): the variable-hint
        # rule took `.ag` anywhere next to a variable as a task-dir hint, but a
        # word that ENDS in `.agent` cannot add a `tasks` component — exactly as
        # unknowable as the allowed `"$D/build"` (owner, 2026-09-23).
        mk = self.MK
        for cmd in ('N=$PWD/p2; ' + mk + ' -p "$N/.agent"',
                    'N=$PWD/p2; ' + mk + ' -p "$N/.agent" && cp a.json "$N/.agent/"',
                    mk + ' -p "$HOME/.agent"', mk + ' -p "${WORK}/.agent/"'):
            with self.subTest(cmd=cmd):
                r = self._run(cmd)
                self.assertEqual(r.returncode, 0, f"bare .agent dir refused: {cmd!r}: {r.stderr}")

    def test_a_variable_that_can_still_complete_a_task_dir_blocks(self):
        # controls for the narrowing: a visible `tasks`, or a variable AFTER the
        # `.ag` hint, can still spell a task dir
        mk = self.MK
        for cmd in ('D=agent; ' + mk + ' -p .$D/tasks/999-x', 'T=tasks; ' + mk + ' -p .agent/$T/999-x',
                    mk + ' -p "$N/.agent/tasks/999-x"', mk + ' -p .ag$X/tasks',
                    mk + ' -p ".agent/${T}"'):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)

    def test_shell_special_parameters_are_unresolved(self):
        # 085 round 3 V4 / R8: `$@`, `$*`, `$1` … are expanded at run time;
        # `_UNRESOLVED` missed the special ones, so `.$@/tasks/9-x` passed.
        mk = self.MK
        for cmd in ("set -- agent; " + mk + " -p .$@/tasks/999-x",
                    "set -- agent; " + mk + " -p .$*/tasks/999-x",
                    "set -- agent; " + mk + " -p .${@}/tasks/999-x",
                    "set -- agent; " + mk + " -p .$1/tasks/999-x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)

    def test_text_that_only_mentions_mkdir_is_not_a_task_dir(self):
        # Item 31 / R8 (live: the dev repo's own hook refused `tasks new` for this
        # very task, a heredoc note, and a python script writing a report): the
        # trigger fires on the WORD mkdir anywhere, so data was judged as a run.
        # Only a command-position mkdir creates a directory.
        mk, td = self.MK, self.TD
        for cmd in (f"cat > note.md <<'EOF'\nreminder: never {mk} -p {td}/1-x by hand\nEOF",
                    f"cat > note.md <<'EOF'\n{mk} -p {td}/1-x\nEOF",
                    f'echo "do not run {mk} -p {td}/1-x" > note.md',
                    f'.claude/bin/tasks new bugfix x "intent: a manual {mk} -p {td}/9-x is refused"',
                    f'git commit -m "guard: {mk} -p {td}/9-x stays refused"',
                    f'grep -n "{mk} -p {td}" CHANGELOG.md',
                    f"printf '%s\\n' '{mk} -p {td}/9-x' >> notes.txt"):
            with self.subTest(cmd=cmd):
                r = self._run(cmd)
                self.assertEqual(r.returncode, 0, f"a mention was judged as a run: {cmd!r}: {r.stderr}")

    def test_a_mkdir_that_really_runs_still_blocks(self):
        # the narrowing must keep every command position: wrappers, a shell's -c,
        # eval, a substitution, xargs, find -exec, a heredoc a SHELL reads, a later
        # line, a group, a herestring
        mk, td = self.MK, self.TD
        for cmd in (f"{mk} -p {td}/999-x", f"sudo {mk} -p {td}/999-x",
                    f"bash -c '{mk} -p {td}/999-x'", f'eval "{mk} -p {td}/999-x"',
                    f"echo $({mk} -p {td}/999-x)", f"echo ok; {mk} -p {td}/999-x",
                    f"xargs {mk} -p <<< {td}/999-x",
                    f"find . -maxdepth 0 -exec {mk} -p {td}/999-x \\;",
                    f"bash <<'EOF'\n{mk} -p {td}/999-x\nEOF",
                    f"true\n{mk} -p {td}/999-x", f"( {mk} -p {td}/999-x )",
                    f"{{ {mk} -p {td}/999-x; }}", f"bash <<< '{mk} -p {td}/999-x'",
                    f"nohup {mk} -p {td}/999-x", f"timeout 5 {mk} -p {td}/999-x",
                    "mk{d,z}ir -p " + td + "/999-x", "$'\\x6d'kdir -p " + td + "/999-x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)

    def test_the_block_is_journalled(self):
        # G2-13 (109 H08): Guard 2's refusal wrote no journal line, while Guard 0's
        # (a task.md Write) does.
        r = self._run(f"{self.MK} -p {self.TD}/999-x")
        self.assertEqual(r.returncode, 2)
        rows = [x for x in self._journal() if x.get("hook") == "task-gate"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["decision"], rows[0]["reason"]), ("block", "manual task dir creation"))
        self.assertEqual(rows[0].get("tool"), "Bash")
        self.assertIn(self.TD, rows[0].get("command", ""))


class TaskDirGuardImplPanelRound1(unittest.TestCase):
    """Task 110 impl panel round 1 (opus, sol-high): the first narrowing allowed
    whenever no command head READ `mkdir`, so a head it could not resolve and a
    wrapper command_guard does not model passed — all refused by 1.5.45. A
    mkdir word is inert now only in masked data or among the arguments of a
    known read/print command."""
    MK, TD = TaskDirGuardFixBatch110.MK, TaskDirGuardFixBatch110.TD
    setUp = TaskDirGuardFixBatch110.setUp
    _run = TaskDirGuardFixBatch110._run

    def test_unresolved_heads_and_unmodelled_wrappers_block(self):
        mk, td = self.MK, self.TD
        for cmd in (f"M={mk}; $M -p {td}/999-x",
                    f'"$(echo {mk})" -p {td}/999-x',
                    f"`echo {mk}` -p {td}/999-x",
                    f"busybox {mk} -p {td}/999-x",
                    f"flock /tmp/l {mk} -p {td}/999-x",
                    f"rg --pre {mk} x {td}/999-x",
                    f"git -c alias.x='!{mk} {td}/999-x' x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)

    def test_a_parallel_too_large_to_enumerate_blocks(self):
        mk, td = self.MK, self.TD
        many = " ".join(f"{td}/{i}-x" for i in range(600))
        self.assertEqual(self._run(f"parallel {{1}}{{2}} ::: {mk} ::: {many} ::: a b").returncode, 2)

    def test_a_shell_heredoc_body_is_a_script_wherever_it_sits(self):
        # the quoted-heredoc sink was read from the FIRST command of the line
        mk, td = self.MK, self.TD
        for cmd in (f"cd /tmp; bash <<'EOF'\n{mk} -p {td}/999-x\nEOF",
                    f"cat <<'EOF' | bash\n{mk} -p {td}/999-x\nEOF",
                    f"true && sudo sh <<'EOF'\n{mk} -p {td}/999-x\nEOF"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)

    def test_mentions_in_read_print_commands_still_pass(self):
        mk, td = self.MK, self.TD
        for cmd in (f'git commit -m "note: {mk} {td}/999-x is refused"',
                    f"grep -n '{mk} -p {td}' notes.md",
                    f".claude/bin/tasks new quick a 'refuse {mk} {td}/999-x'",
                    f"echo {mk} {td}/999-x",
                    f"cat <<'EOF' > n.md\n{mk} -p {td}/999-x\nEOF",
                    f"cd /tmp; cat >> n.md <<'EOF'\n- a `{mk} -p {td}/999-x` note\nEOF",
                    f"cd /tmp; python3 - <<'PY'\nprint('{mk} -p {td}/999-x')\nPY"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 0, cmd)


class TaskDirGuardImplPanelRound2(unittest.TestCase):
    """Task 110 impl panel round 2, class 6 (opus) — a REGRESSION against 1.5.45.
    The narrowing took a mkdir word for inert whenever it sat in masked data or
    among a read/print command's arguments, without asking whether another
    command of the SAME call could run that text: a script written through a
    file-sink heredoc or by echo/printf and then run, a read command's output
    piped into a shell, a quoted heredoc whose reader the helper cannot name.
    1.5.45 refused every one (measured on three roots, record `measure/`). A
    mention is inert now only while EVERY command of the call is one the helper
    knows to read, print or write text."""
    MK, TD = TaskDirGuardFixBatch110.MK, TaskDirGuardFixBatch110.TD
    setUp = TaskDirGuardFixBatch110.setUp
    _run = TaskDirGuardFixBatch110._run

    def test_text_one_command_writes_is_not_inert_when_the_call_can_run_it(self):
        body = f"{self.MK} -p {self.TD}/999-x"
        sink = f"cat > /tmp/s.sh <<'EOF'\n{body}\nEOF\n"
        for cmd in (sink + "bash /tmp/s.sh",
                    f"cat > /tmp/s.sh <<EOF\n{body}\nEOF\nsh /tmp/s.sh",
                    f"tee /tmp/s.sh <<'EOF'\n{body}\nEOF\nbash /tmp/s.sh",
                    sink + ". /tmp/s.sh",
                    sink + "chmod +x /tmp/s.sh && /tmp/s.sh",
                    sink + "bash < /tmp/s.sh",
                    sink + "make -f /tmp/s.sh",
                    f"echo '{body}' > /tmp/s.sh; bash /tmp/s.sh",
                    f"printf '%s\\n' '{body}' > /tmp/s.sh && sh /tmp/s.sh",
                    f"echo '{body}' | bash",
                    f"grep -h '{body}' notes.md | sh",
                    f"python3 - <<'PY'\nopen('/tmp/s.sh','w').write('{body}')\nPY\nbash /tmp/s.sh"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)

    def test_a_quoted_heredoc_with_a_reader_the_helper_cannot_name_is_not_data(self):
        body = f"{self.MK} -p {self.TD}/999-x"
        for reader in ("su -", "sudo -s", "frobnicate --stdin"):
            cmd = f"{reader} <<'EOF'\n{body}\nEOF"
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)

    def test_a_read_command_that_can_be_made_to_run_a_program_is_not_read_only(self):
        # The closed list holds only commands that cannot run what the call
        # wrote. A pager or sorter that can start a helper program is not on it,
        # and an environment prefix can turn any tool into one that does.
        body = f"{self.MK} -p {self.TD}/999-x"
        sink = f"cat > /tmp/s.sh <<'EOF'\n{body}\nEOF\n"
        for cmd in (sink + "less /tmp/s.sh", sink + "sort /tmp/s.sh",
                    sink + "PAGER=/tmp/s.sh git log -1",
                    sink + "X=/tmp/s.sh cat /tmp/s.sh"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)

    def test_the_read_only_list_holds_only_in_an_unchanged_environment_and_for_bare_names(self):
        # My own review after post-D6 run 1, same class: the list said "this
        # command cannot run what the call wrote", and three things made that
        # false. (1) A path-qualified head may BE the file the call wrote, named
        # like a listed tool. (2) An assignment, an `export`, an environment
        # prefix change what a listed tool starts. (3) git and the `tasks` CLI
        # start programs they are configured with (a hook, the verify command).
        # So: a path-qualified head, git and `tasks` count only when no command
        # of the call writes a file, and any environment change takes the call
        # off the list.
        body = f"{self.MK} -p {self.TD}/999-x"
        sink = f"cat > /tmp/s.sh <<'EOF'\n{body}\nEOF\n"
        for cmd in (f"cat > /tmp/grep <<'EOF'\n{body}\nEOF\n/tmp/grep x",
                    sink + ".claude/bin/tasks status",
                    sink + "git commit -m x",
                    sink + "git add s.sh && git commit -m x",
                    sink + "export EDITOR=/tmp/s.sh\ngit commit",
                    sink + "PATH=/tmp:/usr/bin\ngrep x y",
                    f"X=1 python3 - <<'PY'\nprint('never run {body} by hand')\nPY",
                    sink + ".venv/bin/python - <<'PY'\nprint(1)\nPY"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)

    def test_a_write_counts_however_it_is_spelled(self):
        # Post-D6 run 2 (codex): "the call writes no file" was decided from the
        # output redirections written as their own word. A read-write
        # redirection, an operator glued to the word before it, and a listed
        # command's own output-file operand all write a file too.
        body = f"{self.MK} -p {self.TD}/999-x"
        tool = "/tmp/grep"                             # path-qualified, named like a listed tool
        for write in (f"echo '{body}' 1<> {tool}", f"echo '{body}' <> {tool}",
                      f"echo '{body}'>{tool}", f"echo '{body}'>>{tool}",
                      f"echo '{body}' >& {tool}", f"echo '{body}' &>> {tool}",
                      f"echo '{body}' >| {tool}", f"echo '{body}' | uniq - {tool}",
                      f"echo '{body}'; git log -1 --output={tool}",
                      # inside a substitution that itself sits in double quotes
                      f"echo \"$(echo '{body}' > {tool})\"",
                      f"echo \"$(echo \"{body}\" > {tool})\"",
                      f"echo \"`echo '{body}' > {tool}`\""):
            cmd = f"{write}; {tool} x"
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)
        # controls: a redirection that writes no file
        for cmd in (f"echo 'never run {body}' 2>&1; .claude/bin/tasks status",
                    f"echo 'never run {body}' > /dev/null; .claude/bin/tasks status",
                    f"grep -c '{body}' notes.md 2>/dev/null; git status"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 0, cmd)

    def test_an_environment_change_counts_however_it_is_made(self):
        # Post-D6 run 2 (codex): the list describes its tools in the environment
        # the call found. A listed builtin that sets a variable, an expansion
        # that assigns, an arithmetic assignment change it without any
        # assignment word.
        body = f"{self.MK} -p {self.TD}/999-x"
        sink = f"cat > /tmp/s.sh <<'EOF'\n{body}\nEOF\n"
        for change in ("printf -v PATH '%s' /tmp", "printf -vPATH %s /tmp",
                       ": ${PATH:=/tmp}", 'echo "${EDITOR=/tmp/s.sh}"',
                       "echo $((PATH=1))", "(( PATH = 1 ))",
                       "echo $[PATH=1]", "echo ${a[PATH=1]}"):
            cmd = f"{sink}{change}\ngrep x y"
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 2, cmd)
        # controls: the same tools without a change
        for cmd in (f"printf '%s\\n' 'never run {body}' > /tmp/n.md; cat /tmp/n.md",
                    f"echo 'never run {body}' | cut -c1-20",
                    f"echo \"note ${{HOME}}: never run {body}\" > /tmp/n.md"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 0, cmd)

    def test_an_interpreter_is_exempt_only_when_its_program_is_the_heredoc(self):
        # Post-D6 run 1 (codex): the exemption for a non-shell interpreter looked
        # only for a quoted heredoc on its line. With `-c`, `-e`, `-m`, a script
        # operand or another stdin redirection, the program is NOT the heredoc —
        # the heredoc is only its input — and that program can run what the call
        # wrote.
        body = f"{self.MK} -p {self.TD}/999-x"
        sink = f"cat > /tmp/s.sh <<'EOF'\n{body}\nEOF\n"
        prog = 'import subprocess; subprocess.run(["sh", "/tmp/s.sh"])'
        for tail in (f"python3 -c '{prog}' <<'X'\ndata\nX",
                     "python3 -m runpy <<'X'\ndata\nX",
                     "python3 /tmp/run.py <<'X'\ndata\nX",
                     "perl -e 'system(\"sh /tmp/s.sh\")' <<'X'\ndata\nX",
                     "node -e 'x' <<'X'\ndata\nX",
                     "python3 <<'X' < /tmp/run.py\ndata\nX"):
            with self.subTest(tail=tail):
                self.assertEqual(self._run(sink + tail).returncode, 2, tail)
        # controls: the program IS the heredoc
        for tail in ("python3 - <<'PY'\nprint(1)\nPY", "python3 <<'PY'\nprint(1)\nPY",
                     "python3 -B - <<'PY'\nprint(1)\nPY", "python3 - a b <<'PY'\nprint(1)\nPY",
                     "python3 - <<'PY' > /tmp/out.txt 2>&1\nprint(1)\nPY",
                     "node - <<'JS'\nconsole.log(1)\nJS"):
            with self.subTest(tail=tail):
                self.assertEqual(self._run(sink + tail).returncode, 0, tail)

    def test_a_mention_stays_inert_while_every_command_only_reads_or_prints(self):
        # controls (item 31 keeps its promise): notes, prints and record writers
        body = f"{self.MK} -p {self.TD}/999-x"
        for cmd in (f"cat > /tmp/notes.md <<'EOF'\nnever run {body} by hand\nEOF",
                    f"echo 'never run {body} by hand' > /tmp/notes.md; cat /tmp/notes.md",
                    f"cd /tmp && python3 - <<'PY'\nprint('never run {body} by hand')\nPY",
                    f"git add notes.md && git commit -m 'note: {body} is refused'",
                    f"grep -n '{body}' notes.md | head -n 3",
                    f"if [ -f notes.md ]; then grep -c '{body}' notes.md; fi"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._run(cmd).returncode, 0, cmd)


if __name__ == "__main__":
    unittest.main()
