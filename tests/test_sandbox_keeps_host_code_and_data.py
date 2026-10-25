"""The judges' sandbox leaves writable each agent's own state, never what runs or configures the host later, nor a
person's data (task 308, audit 4's G3).

`build_bwrap_argv` bound `~/.claude` and `.codex .gemini .omlx .pi .grok .cache .local` writable over the read-only
root. Inside, a stand-in agent rewrote the installed plugin's task-gate-hook (the gate then let an Edit through with no
task), and wrote `~/.claude/settings.json` and `~/.local/bin` (his blind session, 2026-10-25; reproduced in task 306,
`evidence/m2-mine/g3_sandbox_home.sh`). `~/.local/share` holds a person's data — keyrings, the Trash, mail — and
`~/.local/bin` the commands on PATH. The tests build the argv with HOME set to a temporary home OUTSIDE /tmp (a home
under /tmp would be writable through the sandbox's own /tmp bind) and, where bubblewrap can start, try each write.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))
sys.path.insert(0, str(_HERE.parent))
from tests._bwrap_standin import bwrap_usable  # noqa: E402
from provider.sandbox import build_bwrap_argv  # noqa: E402

# what runs or configures a later session on the host, inside the agents' writable state folders
CODE_AND_CONFIG = (
    ".claude/plugins/cache/mk/playbook/1.0.0/scripts/task-gate-hook",
    ".claude/plugins/installed_plugins.json",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/CLAUDE.md",
    ".claude/hooks/h.sh",
    ".claude/commands/c.md",
    ".claude/agents/a.md",
    ".claude/skills/s/SKILL.md",
    ".claude/output-styles/o.md",
    ".claude/statusline.sh",
    ".claude/shell-snapshots/snapshot-bash-1-x.sh",
    ".codex/config.toml",
    ".codex/AGENTS.md",
    ".codex/packages/standalone/current/bin/codex",
    ".codex/rules/default.rules",
    ".codex/skills/s/SKILL.md",
    ".codex/plugins/p.json",
    ".codex/hooks.json",
    ".codex/shell_snapshots/s.sh",
    ".gemini/settings.json",
    ".gemini/GEMINI.md",
    ".gemini/config/plugins/p/hooks.json",
    ".gemini/trustedFolders.json",
    ".gemini/antigravity-cli/bin/agentapi",
    ".gemini/antigravity-cli/builtin/skills/s.md",
    ".grok/hooks/playbook-enforcement.json",
    ".grok/bin/agent",
    ".grok/downloads/grok-1.0.0",
    ".grok/installed-plugins/p.json",
    ".grok/bundled/skills/s.md",
)
# a person's data and the commands on PATH: not an agent's state
NOT_AN_AGENTS = (".local/bin/tool", ".local/share/keyrings/login.keyring", ".local/share/Trash/files/old.txt",
                 ".local/lib/python3.10/site-packages/m.py", ".cache/ms-playwright/chromium/chrome")
# what an agent writes as it runs: its transcript, its sign-in's refresh, a new shell snapshot, the write log
STATE = (".claude/projects/-p/s.jsonl", ".claude/.credentials.json", ".claude/shell-snapshots/snapshot-new.sh",
         ".codex/sessions/s.jsonl", ".codex/auth.json", ".gemini/oauth_creds.json", ".grok/config.toml",
         ".local/share/playbook/log")


class _Home(unittest.TestCase):
    # where the temporary home is made: the argv rows read only the argv, so the default temporary folder serves (as
    # under `tasks red`, whose rows see a private /tmp and a read-only /var/tmp); the live rows need one OUTSIDE /tmp,
    # which would be writable through the sandbox's own /tmp bind. Never the real HOME.
    HOME_BASE: "str | None" = None

    def setUp(self):
        bases = [self.HOME_BASE]
        if self.HOME_BASE and os.environ.get("PLAYBOOK_RED") == "1":
            bases.append(os.environ["HOME"])     # a `tasks red` row: /var/tmp is read-only, its HOME a throwaway one
        home = None
        for base in bases:
            try:
                home = Path(tempfile.mkdtemp(prefix="pb308-home-", dir=base)).resolve()
                break
            except OSError:
                continue
        if home is None:
            self.skipTest(f"no temporary folder in {bases}")
        self.addCleanup(shutil.rmtree, home, True)
        self.home = home
        for rel in CODE_AND_CONFIG + NOT_AN_AGENTS + STATE:
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if not rel.endswith("snapshot-new.sh") and rel != ".local/share/playbook/log":
                p.write_text("original\n", encoding="utf-8")
        self.proj = Path(tempfile.mkdtemp(prefix="pb308-proj-", dir=str(home))).resolve()
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def argv(self, cmd):
        return build_bwrap_argv(str(self.proj), None, cmd, None, project_writable=False)


class TheArgv(_Home):

    def test_a_protected_path_that_is_a_link_refuses_the_launch(self):
        # bubblewrap cannot bind onto a link (measured: the launch fails), and the link sits in a writable folder
        settings = self.home / ".claude" / "settings.json"
        real = self.home / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text("original\n", encoding="utf-8")
        settings.unlink()
        settings.symlink_to(real)
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(settings), str(cm.exception))
        self.assertIn("Nothing was launched", str(cm.exception))

    def binds(self, argv, kind):
        return [argv[i + 1] for i, a in enumerate(argv[:-2]) if a == kind]

    def test_local_and_cache_are_not_bound_writable(self):
        rw = self.binds(self.argv(["true"]), "--bind")
        for top in (".local", ".cache"):
            self.assertNotIn(str(self.home / top), rw)
        self.assertIn(str(self.home / ".local" / "share" / "playbook"), rw)       # the write log stays

    def test_code_and_config_are_bound_read_only_after_their_folder(self):
        # what a path IS in the sandbox is what the LAST bind covering it says: here a read-only one of its own, laid
        # over its folder's writable bind (never only the read-only root under it)
        argv = self.argv(["true"])
        binds = [(a, argv[i + 1]) for i, a in enumerate(argv[:-2]) if a in ("--bind", "--ro-bind")]
        for rel in CODE_AND_CONFIG:
            path = str(self.home / rel)
            governing = [(a, p) for a, p in binds if (path + "/").startswith(p.rstrip("/") + "/")][-1]
            self.assertEqual(governing[0], "--ro-bind", (rel, governing))
            self.assertNotEqual(governing[1], "/", rel)


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class InsideTheSandbox(_Home):
    HOME_BASE = "/var/tmp"

    def attempt(self, rel):
        script = f'printf "changed\\n" > "$HOME/{rel}" 2>/dev/null && echo WROTE || echo REFUSED'
        r = subprocess.run(self.argv(["bash", "-c", script]), cwd=str(self.proj), capture_output=True, text=True,
                           timeout=60, env=dict(os.environ, HOME=str(self.home)))
        return r.stdout.strip()

    def test_code_config_and_data_cannot_be_written(self):
        for rel in CODE_AND_CONFIG + NOT_AN_AGENTS:
            self.assertEqual(self.attempt(rel), "REFUSED", rel)
            self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "original\n", rel)

    def test_control_an_agents_state_is_written(self):
        for rel in STATE:
            self.assertEqual(self.attempt(rel), "WROTE", rel)

    def test_a_missing_claude_folder_is_made_empty_and_held(self):
        # with no skills or agents folder at launch, a run could make one with a skill or an agent the host then loads
        for name in ("skills", "agents"):
            shutil.rmtree(self.home / ".claude" / name)
        for rel in (".claude/skills/s/SKILL.md", ".claude/agents/a.md"):
            script = f'mkdir -p "$(dirname "$HOME/{rel}")" 2>/dev/null; printf x > "$HOME/{rel}" 2>/dev/null ' \
                     f'&& echo WROTE || echo REFUSED'
            r = subprocess.run(self.argv(["bash", "-c", script]), cwd=str(self.proj), capture_output=True, text=True,
                               timeout=60, env=dict(os.environ, HOME=str(self.home)))
            self.assertEqual(r.stdout.strip(), "REFUSED", rel)
        for name in ("skills", "agents"):
            self.assertEqual(list((self.home / ".claude" / name).iterdir()), [], name)

    def test_a_read_only_file_cannot_be_replaced_or_removed(self):
        for verb in ('mv "$HOME/.claude/x" "$HOME/.claude/settings.json"', 'rm -f "$HOME/.claude/settings.json"',
                     'rm -rf "$HOME/.claude/plugins"'):
            script = f'printf x > "$HOME/.claude/x"; {verb} 2>/dev/null; true'
            subprocess.run(self.argv(["bash", "-c", script]), cwd=str(self.proj), capture_output=True, timeout=60,
                           env=dict(os.environ, HOME=str(self.home)))
            self.assertEqual((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"), "original\n")
            self.assertTrue((self.home / CODE_AND_CONFIG[0]).exists(), verb)


if __name__ == "__main__":
    unittest.main()
