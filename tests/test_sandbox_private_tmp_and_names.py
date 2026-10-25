"""Every sandboxed run gets a private /tmp, and the names the agents look up at the top of their own folder and do not
find are made and held read-only (task 315, 308's follow-up; the owner's design, 2026-10-25 22:28: "/tmp privat +
nume").

/tmp was shared with the host and writable. So 308's home layer refused every claude launch while a kept folder held a
link into /tmp: Chrome's singleton links under `~/.claude/jobs` refused his claude seat at 18:37. And the top of the
running agent's own folder took any new name. strace (308's `evidence/strace-missing-names.txt`) found the names the
agents look up and lack, and a run could make any of them for the host to load. Claude Code takes
`~/.claude/.config.json` as its global config when it has content, and that config's `mcpServers` are commands the
host starts.

The argv rows build the argv with HOME set to 308's temporary home. The live rows run under real bubblewrap, with that
home outside /tmp, never the real HOME.
"""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))
sys.path.insert(0, str(_HERE.parent))
from tests._bwrap_standin import bwrap_usable  # noqa: E402
from tests import test_sandbox_keeps_host_code_and_data as t308  # noqa: E402  (its TestCase classes stay there)
from provider.sandbox import build_bwrap_argv  # noqa: E402

# the names of the design's table, by the agent whose run makes them: {file: its content}, [folders]
FILES = {
    "claude": {".claude/.config.json": "", ".claude/CLAUDE.md": "", ".claude/AGENTS.md": ""},
    "codex": {".codex/AGENTS.md": "", ".codex/environments.toml": ""},
    "agy": {".gemini/AGENTS.md": "", ".gemini/hooks.json": "{}\n", ".gemini/antigravity-cli/hooks.json": "{}\n"},
    "grok": {},
}
FOLDERS = {
    "claude": [".claude/rules", ".claude/workflows", ".claude/dev-mods"],
    "codex": [".codex/AGENTS.override.md", ".codex/.codex-plugin", ".codex/.claude-plugin", ".codex/.cursor-plugin"],
    "agy": [],
    "grok": [".grok/personas", ".grok/roles"],
}


def _pairs(argv):
    return [(a, argv[i + 1]) for i, a in enumerate(argv[:-1])]


def _tmp_dir(test, prefix):
    """A folder of the host's /tmp itself (not the suite's own temporary folder), removed after the test."""
    d = Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp")).resolve()
    test.addCleanup(shutil.rmtree, d, True)
    return d


class TheArgv(t308._Home):

    def test_a_kept_link_into_tmp_does_not_refuse_the_launch(self):
        # the bug (18:37): Chrome's singleton links in a Claude Code job's profile lead into /tmp, writable for the
        # run, so every claude launch was refused while such a job ran
        chrome = _tmp_dir(self, "pb315-chrome-")
        (chrome / "SingletonSocket").write_text("", encoding="utf-8")
        prof = self.home / ".claude" / "jobs" / "j" / "tmp" / "chrome-x"
        prof.mkdir(parents=True)
        (prof / "SingletonSocket").symlink_to(chrome / "SingletonSocket")
        argv = self.argv(["true"], "claude")
        self.assertIn(("--tmpfs", "/tmp"), _pairs(argv))
        self.assertNotIn(("--bind", "/tmp"), _pairs(argv))

    def test_claude_s_config_json_is_made_empty_with_no_write_bit(self):
        # Claude Code takes `.config.json` as its global config when it has content, or has a write bit while
        # `~/.claude.json` is missing (its `y_()`): an empty one with no write bit is never taken
        p = self.home / ".claude" / ".config.json"
        self.assertFalse(os.path.lexists(p))
        argv = self.argv(["true"], "claude")
        self.assertEqual(p.read_bytes() if p.exists() else None, b"")
        self.assertEqual(p.stat().st_mode & 0o222, 0, oct(p.stat().st_mode))
        self.assertIn(("--ro-bind-try", str(p)), _pairs(argv))

    def test_an_existing_name_is_left_and_another_agents_folder_gets_none(self):
        # a claude run makes no name in another agent's folder
        self.argv(["true"], "claude")
        for agent in ("codex", "agy", "grok"):
            for rel in [*FILES[agent], *FOLDERS[agent]]:
                if rel not in t308.CODE_AND_CONFIG:
                    self.assertFalse(os.path.lexists(self.home / rel), rel)
        # the user's own, of each kind: left as they are
        cfg = self.home / ".claude" / ".config.json"
        cfg.unlink(missing_ok=True)
        cfg.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        os.chmod(cfg, 0o600)
        override = self.home / ".codex" / "AGENTS.override.md"     # a file where a folder is made
        override.write_text("mine\n", encoding="utf-8")
        rules = self.home / ".claude" / "rules"
        shutil.rmtree(rules, ignore_errors=True)
        rules.mkdir()
        (rules / "r.md").write_text("rule\n", encoding="utf-8")
        hooks = self.home / ".gemini" / "hooks.json"               # a folder where a file is made
        hooks.mkdir()
        for agent in ("claude", "codex", "agy"):
            self.argv(["true"], agent)
        self.assertEqual(cfg.read_text(encoding="utf-8"), '{"mcpServers": {}}\n')
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
        self.assertEqual(override.read_text(encoding="utf-8"), "mine\n")
        self.assertEqual(sorted(p.name for p in rules.iterdir()), ["r.md"])
        self.assertTrue(hooks.is_dir() and not any(hooks.iterdir()))


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class InsideTheSandbox(t308._Home):
    HOME_BASE = "/var/tmp"

    def _run(self, argv, env=None):
        return subprocess.run(argv, cwd=str(self.proj), capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, HOME=str(self.home), **(env or {})))

    def test_tmp_is_private(self):
        host = _tmp_dir(self, "pb315-host-")
        (host / "seen").write_text("host\n", encoding="utf-8")
        name = f"/tmp/pb315-run-{os.getpid()}-{os.urandom(4).hex()}"
        self.addCleanup(lambda: os.path.lexists(name) and os.unlink(name))
        out = self.run_in(f'cat "{host}/seen" 2>/dev/null || echo UNSEEN; printf x > "{name}" && echo WROTE')
        self.assertEqual(out.split(), ["UNSEEN", "WROTE"])
        self.assertFalse(os.path.lexists(name))

    def test_a_project_and_an_extra_path_under_tmp_are_still_there(self):
        proj, extra = _tmp_dir(self, "pb315-proj-"), _tmp_dir(self, "pb315-extra-")
        cmd = ["bash", "-c", f'printf p > "{proj}/p" && printf e > "{extra}/e" && echo OK']
        argv = build_bwrap_argv(str(proj), None, cmd, [str(extra)], project_writable=True, agent="claude")
        r = subprocess.run(argv, cwd=str(proj), capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, HOME=str(self.home)))
        self.assertEqual(r.stdout.strip(), "OK", r.stderr)
        self.assertEqual(((proj / "p").read_text(encoding="utf-8"), (extra / "e").read_text(encoding="utf-8")),
                         ("p", "e"))

    def test_a_tmpdir_under_tmp_is_there_inside(self):
        # pam_tmpdir's shape: a TMPDIR of the user's own under /tmp, there on the host
        tmpdir = _tmp_dir(self, "pb315-tmpdir-") / "user" / "1000"
        tmpdir.mkdir(parents=True)
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmpdir)}):
            argv = self.argv(["bash", "-c", 'f=$(mktemp) && printf x > "$f" && echo "$f"'])
        r = self._run(argv, {"TMPDIR": str(tmpdir)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip().startswith(f"{tmpdir}/"), r.stdout)
        self.assertEqual(list(tmpdir.iterdir()), [])                # made in the run's own /tmp, not on the host

    def test_a_program_on_path_under_tmp_is_found_and_read_only(self):
        bindir = _tmp_dir(self, "pb315-bin-")
        prog = bindir / "pb315prog"
        prog.write_text("#!/bin/sh\necho from-tmp\n", encoding="utf-8")
        prog.chmod(0o755)
        # /tmp itself (bound, it would make the run's /tmp read-only), a relative entry and a look-alike are not bound;
        # the program's folder is, once (my mutant run, 23:10: `/tmp` itself counted survived)
        path = os.pathsep.join(["/tmp", "/tmp/", "pb315rel", "/tmpx", str(bindir), f"{bindir}/",
                                os.environ.get("PATH", "")])
        with mock.patch.dict(os.environ, {"PATH": path}):
            argv = self.argv(["bash", "-c", f'pb315prog; printf x > "{bindir}/w" 2>/dev/null && echo WROTE || '
                                            f'echo REFUSED; printf x > /tmp/pb315-w && echo TMP-WRITABLE'])
        self.assertEqual([d for a, d in _pairs(argv) if a == "--ro-bind-try" and d.startswith("/tmp")], [str(bindir)])
        r = self._run(argv, {"PATH": path})
        self.assertEqual(r.stdout.split(), ["from-tmp", "REFUSED", "TMP-WRITABLE"], r.stderr)
        self.assertFalse((bindir / "w").exists())

    def _made_and_held(self, agent):
        for rel in [*FILES[agent], *FOLDERS[agent]]:
            if os.path.lexists(self.home / rel):
                self.remove(rel)
        # each attempt is its own launch: the names are made at it, and the run then tries to make or write one
        for rel in FILES[agent]:
            self.assertEqual(self.attempt(rel, agent), "REFUSED", rel)
        for rel in FOLDERS[agent]:
            out = self.run_in(f'printf x > "$HOME/{rel}" 2>/dev/null && echo WROTE || echo REFUSED; '
                              f'mkdir -p "$HOME/{rel}" 2>/dev/null; printf x > "$HOME/{rel}/x" 2>/dev/null && '
                              f'echo WROTE || echo REFUSED', agent)
            self.assertEqual(out.split(), ["REFUSED", "REFUSED"], rel)
        for rel, text in FILES[agent].items():
            p = self.home / rel
            self.assertEqual(p.read_text(encoding="utf-8") if p.is_file() else None, text, rel)
        for rel in FOLDERS[agent]:
            p = self.home / rel
            self.assertEqual(sorted(os.listdir(p)) if p.is_dir() else None, [], rel)

    def test_claude_s_names_are_made_and_held(self):
        self._made_and_held("claude")

    def test_codex_s_names_are_made_and_held(self):
        # an empty override FILE hides codex's AGENTS.md (measured 2026-10-25, logged 22:36:39): a folder is passed over
        self._made_and_held("codex")

    def test_agy_s_names_are_made_and_held(self):
        self._made_and_held("agy")

    def test_grok_s_names_are_made_and_held(self):
        self._made_and_held("grok")


def _codex_ok():
    spec = importlib.util.spec_from_file_location("_t315_judge_usage", _HERE / "test_judge_usage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CODEX_OK


class TheCodexSingleJudge(unittest.TestCase):
    """The sandbox below stands in for the real one with a private /tmp: what the run writes reaches the host only
    through a path the launch makes writable for it."""

    def setUp(self):
        from tasks import review
        self.review = review
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name).resolve()
        tdir = self.project / ".agent" / "tasks" / "042-demo"
        tdir.mkdir(parents=True)
        (tdir / "task.md").write_text("# 042 - demo\n## Status\npending\n## Intent\nx\n## Work Plan\n- [ ] a gate\n",
                                      encoding="utf-8")
        (self.project / ".agent" / "models.json").write_text(
            json.dumps({"panel": ["claude:opus", "claude:sonnet"], "default_judge": "claude:opus"}), encoding="utf-8")
        review._PB_JOURNAL_MOD = None
        review._PB_JOURNAL_LOADED = False
        self.log = tdir / "judge-codex.log"
        home = self.project / "home"                                 # never the real HOME
        home.mkdir()
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_message_folder_is_writable_for_the_run(self):
        from provider import sandbox
        stdout = _codex_ok()

        given = []

        def _run(agent, args, **kw):
            out = args[args.index("-o") + 1]
            rw = [os.path.realpath(p) for p in (kw.get("extra_rw") or [])]
            given.extend(rw)
            if any(os.path.realpath(out).startswith(p.rstrip("/") + "/") for p in rw):
                Path(out).write_text("FINAL MESSAGE\n", encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="", args=list(args))
        prev = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, prev)
        with mock.patch.object(sandbox, "run", _run), \
                mock.patch.object(shutil, "which", lambda name: "/usr/bin/" + name), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.suppress(SystemExit):
            self.review.cmd_single_review("plan-review", ["042", "--backend", "codex", "--model", "gpt-5.6-sol:medium"])
        self.assertIn("FINAL MESSAGE", self.log.read_text(encoding="utf-8") if self.log.exists() else "")
        self.assertTrue(given)
        self.assertEqual([p for p in given if os.path.lexists(p)], [])   # the review removed the folder


@unittest.skipUnless(shutil.which("bwrap"), "no bubblewrap here")
class TheRedRows(t308._Home):

    def test_a_contained_row_gets_the_private_tmp(self):
        from tasks import red
        live = _tmp_dir(self, "pb315-live-")                         # a live repository under /tmp
        root = Path(tempfile.mkdtemp(prefix="pb315-red-", dir=str(self.home))).resolve()
        params = inspect.signature(red._argv).parameters
        kw = {}
        if "private_tmp" in params:
            kw["private_tmp"] = False                                # the old code: the copy in /tmp, /tmp shared
        if "protect" in params:
            kw["protect"] = [str(live)]
        argv = red._argv("/bin/bash", "true", root, self.home, True, **kw)
        self.assertEqual([b for a, b in _pairs(argv) if a == "--tmpfs" and b == "/tmp"], ["/tmp"], argv)
        self.assertNotIn(("--bind", "/tmp"), _pairs(argv))
        self.assertNotIn(("--ro-bind", str(live)), _pairs(argv))
        # …and a launcher argv with /tmp shared is refused, never run (my mutant run, 23:25: the check removed survived)
        from provider import sandbox
        shared = ["bwrap", "--ro-bind", "/", "/", "--bind", "/tmp", "/tmp", "--bind", str(root), str(root),
                  "/bin/bash", "-c", "true"]
        with mock.patch.object(sandbox, "build_bwrap_argv", lambda *a, **k: list(shared)):
            with self.assertRaises(RuntimeError):
                red._argv("/bin/bash", "true", root, self.home, True)


if __name__ == "__main__":
    unittest.main()
