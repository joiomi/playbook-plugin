"""The judges' sandbox leaves writable only the running agent's own state, never what runs or configures the host
later, nor a person's data (task 308, audit 4's G3).

`build_bwrap_argv` bound `~/.claude` and `.codex .gemini .omlx .pi .grok .cache .local` writable over the read-only
root. Inside, a stand-in agent rewrote the installed plugin's task-gate-hook (the gate then let an Edit through with no
task), and wrote `~/.claude/settings.json` and `~/.local/bin` (his blind session, 2026-10-25; reproduced in task 306,
`evidence/m2-mine/g3_sandbox_home.sh`). `~/.local/share` holds a person's data — keyrings, the Trash, mail — and
`~/.local/bin` the commands on PATH. Impl round 1 (codex, grok, agy, opus, sonnet) found the holes a list of read-only
paths left: a folder above one renamed and replaced, a missing one created, a link or a hard link beside one, a host
shell snapshot made during the run. The tests build the argv with HOME set to a temporary home OUTSIDE /tmp for the
live rows (a home under /tmp is refused by the git-sources rule without its ~/.gitconfig) and, where bubblewrap can
start, try each write inside it, as the agent that owns the folder: the hardest case, its folder writable.
"""

from __future__ import annotations

import inspect
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

# the agent whose run has each state folder writable
OWNER = {".claude": "claude", ".codex": "codex", ".gemini": "agy", ".grok": "grok"}

# what runs or configures a later session on the host, inside the agents' state folders
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
    ".claude/local/claude",
    ".claude/statusline.sh",
    ".codex/config.toml",
    ".codex/AGENTS.md",
    ".codex/packages/standalone/current/bin/codex",
    ".codex/rules/default.rules",
    ".codex/skills/s/SKILL.md",
    ".codex/plugins/p.json",
    ".codex/hooks.json",
    ".gemini/settings.json",
    ".gemini/GEMINI.md",
    ".gemini/config/plugins/p/hooks.json",
    ".gemini/trustedFolders.json",
    ".gemini/antigravity-cli/bin/agentapi",
    ".gemini/antigravity-cli/builtin/skills/s.md",
    ".gemini/extensions/e/gemini-extension.json",
    ".grok/hooks/playbook-enforcement.json",
    ".grok/bin/agent",
    ".grok/downloads/grok-1.0.0",
    ".grok/installed-plugins/p.json",
    ".grok/bundled/skills/s.md",
    # impl round 2: names the round-1 list did not have (grok, agy, opus) — read-only because they exist
    ".grok/trusted_folders.toml",
    ".grok/sandbox.toml",
    ".grok/skills/commit/SKILL.md",
    ".grok/plugins/p.json",
    ".grok/marketplace-cache/x/hooks/hooks.json",
    ".gemini/antigravity-cli/settings.json",
    ".claude/remote-settings.json",
    ".claude/chrome/chrome-native-host",
    ".claude/jobs/j/job.json",
    # impl round 3: entries the measured list let through though they hold code or a hook source (sonnet, grok,
    # codex): grok's shell completions (an rc file sources them), its config (hooks), codex's daemon settings
    ".grok/completions/bash/grok.bash",
    ".grok/config.toml",
    ".codex/app-server-daemon/settings.json",
    ".codex/.tmp/plugins/p/hooks.json",
    # a name no list has: read-only because it exists and is not state its agent writes
    ".grok/a-future-config.toml",
    ".claude/a-future-tool/run",
    ".codex/a-future-hooks/h.json",
)
# what a host session sources before its shell calls: (the private folder, a file in it)
SNAPSHOTS = ((".claude/shell-snapshots", ".claude/shell-snapshots/snapshot-bash-1-x.sh"),
             (".codex/shell_snapshots", ".codex/shell_snapshots/s.sh"),
             (".claude/session-env", ".claude/session-env/sid/sessionstart-hook-0.sh"))
CODEX_TMP = ".codex/tmp/arg0/codex-arg0x/apply_patch"
# a person's data and the commands on PATH: no agent's state
NOT_AN_AGENTS = (".local/bin/tool", ".local/share/keyrings/login.keyring", ".local/share/Trash/files/old.txt",
                 ".local/lib/python3.10/site-packages/m.py", ".cache/ms-playwright/chromium/chrome")
# what an agent writes as it runs: its transcript, its sign-in's refresh, the write log
STATE = (".claude/projects/-p/s.jsonl", ".claude/.credentials.json", ".codex/sessions/s.jsonl", ".codex/auth.json",
         ".gemini/oauth_creds.json", ".grok/sessions/s.json", ".codex/app-server-daemon/daemon.pid")
WRITE_LOG = ".local/share/playbook/log"


def owner(rel: str) -> str:
    return OWNER[rel.split("/")[0]]


def _build(proj, cmd, agent):
    # The `agent` the launcher passes since impl round 1. A `tasks red` row runs these tests on older code, which
    # takes none and binds every agent's folder: called without it there, so each row fails for its own reason.
    if "agent" in inspect.signature(build_bwrap_argv).parameters:
        return build_bwrap_argv(str(proj), None, cmd, None, project_writable=False, agent=agent)
    return build_bwrap_argv(str(proj), None, cmd, None, project_writable=False)


class _Home(unittest.TestCase):
    # where the temporary home is made: the argv rows read only the argv, so the default temporary folder serves (as
    # under `tasks red`, whose rows see a private /tmp and a read-only /var/tmp); the live rows need one OUTSIDE /tmp,
    # where the git-sources rule refuses a HOME with no ~/.gitconfig (task 315: /tmp is no longer shared). Never the
    # real HOME.
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
        for rel in CODE_AND_CONFIG + tuple(f for _, f in SNAPSHOTS) + (CODEX_TMP,) + NOT_AN_AGENTS + STATE:
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("original\n", encoding="utf-8")
        (home / WRITE_LOG).parent.mkdir(parents=True, exist_ok=True)
        self.proj = Path(tempfile.mkdtemp(prefix="pb308-proj-", dir=str(home))).resolve()
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def argv(self, cmd, agent="claude"):
        return _build(self.proj, cmd, agent)

    def run_in(self, script, agent="claude"):
        r = subprocess.run(self.argv(["bash", "-c", script], agent), cwd=str(self.proj), capture_output=True,
                           text=True, timeout=60, env=dict(os.environ, HOME=str(self.home)))
        return r.stdout.strip()

    def attempt(self, rel, agent):
        return self.run_in(f'printf "changed\\n" > "$HOME/{rel}" 2>/dev/null && echo WROTE || echo REFUSED', agent)

    def remove(self, rel):
        p = self.home / rel
        shutil.rmtree(p) if p.is_dir() else p.unlink()


class TheArgv(_Home):

    def binds(self, argv, kind):
        return [argv[i + 1] for i, a in enumerate(argv[:-2]) if a == kind]

    def test_local_and_cache_are_not_bound_writable(self):
        for agent in ("claude", "codex", "agy", "grok", "pi"):
            rw = self.binds(self.argv(["true"], agent), "--bind")
            for top in (".local", ".cache"):
                self.assertNotIn(str(self.home / top), rw, agent)
            self.assertIn(str(self.home / ".local" / "share" / "playbook"), rw, agent)       # the write log stays

    def test_a_run_has_only_its_own_agents_folder_writable(self):
        mine = {"claude": {".claude"}, "codex": {".codex"}, "agy": {".gemini"}, "grok": {".grok"},
                "pi": {".pi", ".omlx"}}
        for agent, folders in mine.items():
            tops = {b[len(str(self.home)) + 1:].split("/")[0] for b in self.binds(self.argv(["true"], agent), "--bind")
                    if b.startswith(str(self.home) + "/")}
            self.assertEqual(tops - {".local"}, folders, agent)

    def test_code_and_config_are_bound_read_only_after_their_folder(self):
        # what a path IS in the sandbox is what the LAST bind covering it says: here a read-only one of its own, laid
        # over its folder's writable bind (never only the read-only root under it)
        for rel in CODE_AND_CONFIG:
            argv = self.argv(["true"], owner(rel))
            binds = [(a, argv[i + 1]) for i, a in enumerate(argv[:-2]) if a in ("--bind", "--ro-bind", "--ro-bind-try")]
            path = str(self.home / rel)
            governing = [(a, p) for a, p in binds if (path + "/").startswith(p.rstrip("/") + "/")][-1]
            self.assertIn(governing[0], ("--ro-bind", "--ro-bind-try"), (rel, governing))
            self.assertNotEqual(governing[1], "/", rel)

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

    def test_a_script_or_a_snapshot_folder_that_is_a_link_refuses_the_launch(self):
        real = self.home / "dotfiles"
        real.mkdir()
        (real / "status.sh").write_text("echo hi\n", encoding="utf-8")
        self.remove(".claude/statusline.sh")
        (self.home / ".claude" / "statusline.sh").symlink_to(real / "status.sh")
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn("statusline.sh", str(cm.exception))
        (self.home / ".claude" / "statusline.sh").unlink()
        self.remove(".claude/shell-snapshots")
        (real / "snaps").mkdir()
        (self.home / ".claude" / "shell-snapshots").symlink_to(real / "snaps")
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn("shell-snapshots", str(cm.exception))

    def test_a_link_chain_through_a_writable_place_refuses_the_launch(self):
        # impl round 3 (opus): a link whose END is read-only but whose chain passes through a place the run may write
        # (the run can replace the middle link); and one into the write log (agy, codex, grok)
        hook = self.home / CODE_AND_CONFIG[0]
        middle = self.home / ".claude" / "projects" / "hop"
        middle.symlink_to(self.home / ".claude" / "settings.json")
        hook.unlink()
        hook.symlink_to(middle)
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(hook), str(cm.exception))

    def test_a_link_into_an_extra_writable_path_inside_a_kept_folder_refuses_the_launch(self):
        # post-panel run 1 (codex): an `--rw` path laid over a kept folder is writable, whatever the kept bind says
        ws = self.home / ".claude" / "plugins" / "ws"
        ws.mkdir()
        hook = self.home / CODE_AND_CONFIG[0]
        hook.unlink()
        hook.symlink_to(ws / "hook.sh")
        args = {"agent": "claude"} if "agent" in inspect.signature(build_bwrap_argv).parameters else {}
        with self.assertRaises(RuntimeError) as cm:
            build_bwrap_argv(str(self.proj), None, ["true"], [str(ws)], project_writable=False, **args)
        self.assertIn(str(hook), str(cm.exception))

    def test_a_hook_source_is_published_whole_or_not_at_all(self):
        # post-panel run 1 (codex): made empty and written after, a crash between left an empty settings.json for
        # good (later launches see a file and make none)
        p = self.home / ".claude" / "settings.local.json"
        p.unlink()
        real_write = os.write

        def dies(fd, data):
            raise OSError(5, "simulated crash")
        with mock.patch.object(os, "write", dies):
            with self.assertRaises(OSError):
                self.argv(["true"])
        self.assertFalse(os.path.lexists(p))
        self.assertEqual([q.name for q in p.parent.iterdir() if q.name.startswith(".settings.local.json")], [])
        del real_write
        self.argv(["true"])
        self.assertEqual(p.read_text(encoding="utf-8"), "{}\n")

    def test_launches_at_once_each_make_or_find_the_hook_source(self):
        # verify, 2026-10-25 06:35: a panel launches its seats side by side in one process, and a temporary name
        # made of the pid alone collided ("File exists") — two of three seats failed
        import threading
        for rel in (".claude/settings.json", ".claude/settings.local.json"):
            (self.home / rel).unlink()
        errors: "list[BaseException]" = []

        def launch():
            try:
                self.argv(["true"])
            except BaseException as exc:                # noqa: BLE001 — every failure is the test's finding
                errors.append(exc)
        threads = [threading.Thread(target=launch) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        for rel in (".claude/settings.json", ".claude/settings.local.json"):
            self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "{}\n", rel)
        self.assertEqual([q.name for q in (self.home / ".claude").iterdir() if ".playbook-" in q.name], [])

    def test_a_hook_source_being_published_by_another_launch_is_not_a_hard_link(self):
        # post-panel run 2 (codex, a barrier reproduced it twice): between linking the new file into place and removing
        # its temporary name, another launch saw two links and refused — a seat lost for nothing
        p = self.home / ".claude" / "settings.local.json"
        os.link(p, p.with_name(".settings.local.json.playbook-123-abcdef012345"))
        self.argv(["true"])                             # no RuntimeError: that other name is a launch's own
        other = self.home / ".claude" / "projects" / "settings.local.json.copy"
        os.link(p, other)
        with self.assertRaises(RuntimeError):           # a third name, of anyone's, still refuses
            self.argv(["true"])

    def test_a_temporary_name_that_came_after_the_look_is_not_taken_off_the_count(self):
        # my mutant run on post-panel run 3's fix: only a temporary name held read-only is taken off the link count; one
        # the look did not see is writable in this run, so the launch refuses (it fails closed)
        p = self.home / ".claude" / "settings.local.json"
        tmp = p.with_name(".settings.local.json.playbook-123-abcdef012345")
        os.link(p, tmp)
        real_iterdir = Path.iterdir
        with mock.patch.object(Path, "iterdir", lambda d: (q for q in real_iterdir(d) if q != tmp)):
            with self.assertRaises(RuntimeError) as cm:
                self.argv(["true"])
        self.assertIn("another hard link", str(cm.exception))

    def test_a_missing_hook_source_is_made_empty_and_held(self):
        # impl round 3 (grok): grok loads Claude Code's settings files and its own managed config as hooks
        for rel, agent, empty in ((".claude/settings.local.json", "claude", "{}\n"),
                                  (".grok/managed_config.toml", "grok", "")):
            p = self.home / rel
            if p.exists():
                p.unlink()
            argv = self.argv(["true"], agent)
            self.assertEqual(p.read_text(encoding="utf-8"), empty, rel)
            self.assertIn(("--ro-bind-try", str(p)), [(a, argv[i + 1]) for i, a in enumerate(argv[:-2])], rel)

    def test_a_missing_codex_or_agy_hook_source_is_made_empty_and_held(self):
        # post-panel run 4 (opus): only Claude Code's and grok's were made, and his codex has `hooks = true` with no
        # `~/.codex/hooks.json` — a codex run could make the hooks his later codex sessions run
        for rel, agent, empty in ((".codex/config.toml", "codex", ""), (".codex/hooks.json", "codex", '{"hooks": {}}\n'),
                                  (".codex/app-server-daemon/settings.json", "codex", "{}\n"),
                                  (".gemini/settings.json", "agy", "{}\n"),
                                  (".gemini/antigravity-cli/settings.json", "agy", "{}\n")):
            p = self.home / rel
            if p.exists():
                p.unlink()
            argv = self.argv(["true"], agent)
            self.assertEqual(p.read_text(encoding="utf-8") if p.exists() else None, empty, rel)
            self.assertIn(("--ro-bind-try", str(p)), [(a, argv[i + 1]) for i, a in enumerate(argv[:-2])], rel)

    def test_no_agy_folder_makes_no_agy_settings_and_launches(self):
        # my mutant run on post-panel run 4's fix: with no `antigravity-cli` (agy not installed) none is made, and the
        # launch does not fail on the missing folder
        self.remove(".gemini/antigravity-cli")
        self.argv(["true"], "agy")
        self.assertFalse(os.path.lexists(self.home / ".gemini" / "antigravity-cli"))

    def test_codex_s_missing_env_file_is_made_empty_and_held(self):
        # post-panel run 5 (opus): codex loads `~/.codex/.env` into its environment at every start; missing here, a
        # codex run could write a `BASH_ENV` there that his later codex sessions run
        p = self.home / ".codex" / ".env"
        self.assertFalse(p.exists())
        argv = self.argv(["true"], "codex")
        self.assertEqual(p.read_text(encoding="utf-8") if p.exists() else None, "")
        self.assertIn(("--ro-bind-try", str(p)), [(a, argv[i + 1]) for i, a in enumerate(argv[:-2])])

    def test_no_folder_is_made_through_a_linked_folder_above_it(self):
        # post-panel run 5 (opus): with `~/.codex/.tmp` a link, `.tmp/plugins` was made where it pointed, and only then
        # was the launch refused
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        self.remove(".codex/.tmp")
        (self.home / ".codex" / ".tmp").symlink_to(elsewhere)
        with self.assertRaises(RuntimeError):
            self.argv(["true"], "codex")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_nothing_is_made_through_a_state_folder_that_is_a_link(self):
        # my mutant run on post-panel run 5's fix: a folder or a hook source deeper down was made where a linked state
        # folder points, before the launch was refused
        elsewhere = self.home / "elsewhere"
        shutil.copytree(self.home / ".codex", elsewhere, symlinks=True)
        shutil.rmtree(elsewhere / ".tmp")
        shutil.rmtree(elsewhere / "app-server-daemon")
        self.remove(".codex")
        (self.home / ".codex").symlink_to(elsewhere)
        before = sorted(str(q.relative_to(elsewhere)) for q in elsewhere.rglob("*"))
        with self.assertRaises(RuntimeError):
            self.argv(["true"], "codex")
        self.assertEqual(sorted(str(q.relative_to(elsewhere)) for q in elsewhere.rglob("*")), before)

    def test_a_state_folder_that_is_a_link_refuses_the_launch(self):
        # bubblewrap cannot bind onto it either (measured); refused first, with what to do
        real = self.home / "dotfiles" / "claude"
        real.parent.mkdir()
        (self.home / ".claude").rename(real)
        (self.home / ".claude").symlink_to(real)
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(self.home / ".claude"), str(cm.exception))
        self.assertIn("Nothing was launched", str(cm.exception))

    def test_a_link_from_a_read_only_folder_into_a_writable_one_refuses_the_launch(self):
        # a plugin's hook that is a link into the run's own state: the run writes there, the host runs it
        hook = self.home / CODE_AND_CONFIG[0]
        hook.unlink()
        hook.symlink_to(self.home / ".claude" / "projects" / "-p" / "s.jsonl")
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(hook), str(cm.exception))
        self.assertIn("Nothing was launched", str(cm.exception))

    def test_a_protected_file_with_another_hard_link_refuses_the_launch(self):
        settings = self.home / ".claude" / "settings.json"
        os.link(settings, self.home / ".claude" / "projects" / "settings.backup")
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(settings), str(cm.exception))
        self.assertIn("Nothing was launched", str(cm.exception))


class TheArgvOutsideTmp(_Home):
    # a home outside /tmp, so a place is writable only by its own bind, not by /tmp's
    HOME_BASE = "/var/tmp"

    def test_a_link_through_a_linked_folder_in_writable_state_refuses_the_launch(self):
        # post-panel run 1 (codex): `cache` is state the run may write; when it is itself a link, the run can point it
        # elsewhere, and a kept link through it then leads where the run chose
        real = self.home / "dotfiles"
        real.mkdir()
        (real / "hook.sh").write_text("echo hi\n", encoding="utf-8")
        cache = self.home / ".claude" / "cache"
        if cache.exists():
            shutil.rmtree(cache)
        cache.symlink_to(real)
        hook = self.home / CODE_AND_CONFIG[0]
        hook.unlink()
        hook.symlink_to(cache / "hook.sh")
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(hook), str(cm.exception))

    def test_a_link_to_a_linked_folder_written_with_a_slash_refuses_the_launch(self):
        # post-panel run 2 (codex, reproduced twice): a kept link to `~/.claude/cache/` — the trailing slash made the
        # check resolve `cache` before judging where it sits
        real = self.home / "dotfiles"
        real.mkdir()
        cache = self.home / ".claude" / "cache"
        if cache.exists():
            shutil.rmtree(cache)
        cache.symlink_to(real)
        hook_dir = self.home / ".claude" / "plugins" / "linked"
        os.symlink(str(cache) + "/", hook_dir)
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(hook_dir), str(cm.exception))

    def test_a_link_through_a_linked_folder_leading_into_writable_state_refuses_the_launch(self):
        # post-panel run 3 (opus): `a` sits in a read-only place and leads to `cache`, a link in the writable state;
        # resolved, the way ends in `dotfiles`, but the run can re-point `cache` and the host then follows the kept link
        real = self.home / "dotfiles"
        real.mkdir()
        (real / "hook.sh").write_text("echo hi\n", encoding="utf-8")
        cache = self.home / ".claude" / "cache"
        if cache.exists():
            shutil.rmtree(cache)
        cache.symlink_to(real)
        (self.home / "somewhere").mkdir()
        (self.home / "somewhere" / "a").symlink_to(cache)
        hook = self.home / CODE_AND_CONFIG[0]
        hook.unlink()
        hook.symlink_to(self.home / "somewhere" / "a" / "hook.sh")
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(hook), str(cm.exception))

    def test_a_kept_link_through_linked_folders_that_loop_refuses_the_launch(self):
        # my mutant run on post-panel run 3's fix: each linked folder's chain is followed, so folders that point into
        # each other share one budget of hops — the launch refuses with its message, never a crash
        (self.home / "a").symlink_to(self.home / "b" / "x")
        (self.home / "b").symlink_to(self.home / "a" / "y")
        hook = self.home / CODE_AND_CONFIG[0]
        hook.unlink()
        hook.symlink_to(self.home / "a" / "hook.sh")
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(hook), str(cm.exception))

    def test_a_kept_link_leaving_a_writable_folder_by_dot_dot_refuses_the_launch(self):
        # post-panel run 4 (opus): `cache` is a real folder in the writable state; the kept link goes in and leaves it
        # by `..`, so it ends in a kept place, but the run can turn `cache` into a link and redirect the chain
        hook = self.home / CODE_AND_CONFIG[0]
        hook.unlink()
        hook.symlink_to(str(self.home / ".claude" / "cache") + "/../plugins/p.json")
        (self.home / ".claude" / "cache").mkdir(exist_ok=True)
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(hook), str(cm.exception))

    def test_a_kept_link_leaving_a_writable_root_by_dot_dot_launches(self):
        # my mutant run on post-panel run 4's fix: a writable place's own root is a mount point, which a run cannot turn
        # into a link — `~/.claude/../.claude/settings.json` ends in a kept file and launches
        hook = self.home / CODE_AND_CONFIG[0]
        hook.unlink()
        hook.symlink_to(str(self.home / ".claude") + "/../.claude/settings.json")
        self.argv(["true"])

    def test_a_link_into_the_write_log_refuses_the_launch(self):
        # impl round 3 (agy, codex, grok): the write log is bound writable too
        hook = self.home / CODE_AND_CONFIG[0]
        hook.unlink()
        hook.symlink_to(self.home / ".local" / "share" / "playbook" / "x")
        with self.assertRaises(RuntimeError) as cm:
            self.argv(["true"])
        self.assertIn(str(hook), str(cm.exception))


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class TheLauncher(_Home):

    def test_the_launcher_gives_a_run_its_own_agents_folder(self):
        # the argv a judge or `.claude/bin/sandbox` is launched with: the agent it runs names the folder
        from provider import sandbox
        for agent, mine, other in (("codex", ".codex", ".claude"), ("claude", ".claude", ".codex")):
            with mock.patch.object(sandbox, "is_sandboxed", return_value=False):
                argv = sandbox._wrapped_argv(agent, [], self.proj, None, project_writable=False)
            self.assertTrue(argv[0].endswith("bwrap"), agent)
            rw = [argv[i + 1] for i, a in enumerate(argv[:-2]) if a == "--bind"]
            self.assertIn(str(self.home / mine), rw, agent)
            self.assertNotIn(str(self.home / other), rw, agent)


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class InsideTheSandbox(_Home):
    HOME_BASE = "/var/tmp"

    def test_code_config_and_data_cannot_be_written(self):
        for rel in CODE_AND_CONFIG:
            self.assertEqual(self.attempt(rel, owner(rel)), "REFUSED", rel)
            self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "original\n", rel)
        for rel in NOT_AN_AGENTS:
            for agent in ("claude", "codex"):
                self.assertEqual(self.attempt(rel, agent), "REFUSED", (rel, agent))
                self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "original\n", rel)

    def test_an_existing_entry_no_list_names_is_read_only(self):
        # impl round 2: each round found a name the list of what not to write did not have; now what exists is held
        for rel in (".grok/trusted_folders.toml", ".grok/sandbox.toml", ".grok/skills/commit/SKILL.md",
                    ".gemini/antigravity-cli/settings.json", ".claude/remote-settings.json",
                    ".claude/chrome/chrome-native-host", ".grok/a-future-config.toml", ".claude/a-future-tool/run",
                    ".codex/a-future-hooks/h.json"):
            self.assertEqual(self.attempt(rel, owner(rel)), "REFUSED", rel)
            self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "original\n", rel)

    def test_an_entry_holding_code_the_measured_list_let_through_is_read_only(self):
        # impl round 3 (sonnet, grok, codex): a shell rc file sources grok's completions, grok's config holds hooks,
        # codex's daemon settings switch features, codex's plugin copy holds hooks
        for rel in (".grok/completions/bash/grok.bash", ".grok/config.toml", ".codex/app-server-daemon/settings.json",
                    ".codex/.tmp/plugins/p/hooks.json"):
            self.assertEqual(self.attempt(rel, owner(rel)), "REFUSED", rel)
            self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "original\n", rel)

    def test_codex_tmp_is_private(self):
        # impl round 3 (grok): codex execs helpers it keeps in ~/.codex/tmp; the run gets a private, empty one
        rel = CODEX_TMP
        seen = self.run_in('ls -A "$HOME/.codex/tmp"; printf pwned > "$HOME/' + rel + '" 2>/dev/null; true', "codex")
        self.assertEqual(seen, "")
        self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "original\n")

    def test_another_agents_state_cannot_be_written(self):
        for rel, agent in ((".claude/projects/-p/s.jsonl", "codex"), (".codex/auth.json", "claude"),
                           (".gemini/oauth_creds.json", "grok"), (".grok/config.toml", "agy")):
            self.assertEqual(self.attempt(rel, agent), "REFUSED", (rel, agent))
            self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "original\n", rel)

    def test_control_an_agents_state_is_written(self):
        for rel in STATE:
            self.assertEqual(self.attempt(rel, owner(rel)), "WROTE", rel)
        self.assertEqual(self.attempt(WRITE_LOG, "codex"), "WROTE")

    def test_a_folder_above_a_read_only_path_cannot_be_moved_aside(self):
        for rel, agent in ((".gemini/antigravity-cli/bin/agentapi", "agy"), (SNAPSHOTS[0][1], "claude")):
            folder = str(Path(rel).parent.parent) if rel.startswith(".gemini") else str(Path(rel).parent)
            self.run_in(f'mv "$HOME/{folder}" "$HOME/{folder}.aside" 2>/dev/null; mkdir -p "$(dirname "$HOME/{rel}")"'
                        f' 2>/dev/null; printf pwned > "$HOME/{rel}" 2>/dev/null; true', agent)
            self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "original\n", rel)
            self.assertFalse((self.home / (folder + ".aside")).exists(), folder)

    def test_a_host_shell_snapshot_is_neither_seen_nor_written(self):
        for (folder, rel), agent in zip(SNAPSHOTS, ("claude", "codex", "claude")):
            before = sorted(p.name for p in (self.home / folder).iterdir())
            seen = self.run_in(f'ls -A "$HOME/{folder}"; printf mine > "$HOME/{folder}/new.sh" && echo MADE; '
                               f'mkdir -p "$(dirname "$HOME/{rel}")"; printf pwned > "$HOME/{rel}" 2>/dev/null; true',
                               agent)
            self.assertEqual(seen, "MADE", rel)                        # an empty, private folder of its own
            self.assertEqual((self.home / rel).read_text(encoding="utf-8"), "original\n", rel)
            self.assertEqual(sorted(p.name for p in (self.home / folder).iterdir()), before, rel)

    def test_a_missing_folder_of_the_list_is_made_empty_and_held(self):
        # with no such folder at launch, a run could make one with a skill, an agent, a rule or a hook in it
        for rel, agent in ((".claude/skills/s/SKILL.md", "claude"), (".claude/agents/a.md", "claude"),
                           (".codex/rules/default.rules", "codex"), (".gemini/config/plugins/p/hooks.json", "agy"),
                           (".grok/hooks/playbook-enforcement.json", "grok")):
            top = Path(rel).parts[0] + "/" + Path(rel).parts[1]
            self.remove(top)
            out = self.run_in(f'mkdir -p "$(dirname "$HOME/{rel}")" 2>/dev/null; printf x > "$HOME/{rel}" 2>/dev/null'
                              f' && echo WROTE || echo REFUSED', agent)
            self.assertEqual(out, "REFUSED", rel)
            self.assertEqual(list((self.home / top).iterdir()), [], top)

    def test_codex_s_missing_mixed_folders_are_made_and_held(self):
        # post-panel run 4 (opus): `.codex/.tmp` and `.codex/app-server-daemon` were pinned only when they existed
        for top, rel in ((".codex/.tmp", ".codex/.tmp/plugins/p/hooks.json"),
                         (".codex/app-server-daemon", ".codex/app-server-daemon/settings.json")):
            self.remove(top)
            out = self.run_in(f'mkdir -p "$(dirname "$HOME/{rel}")" 2>/dev/null; printf x > "$HOME/{rel}" 2>/dev/null'
                              f' && echo WROTE || echo REFUSED', "codex")
            self.assertEqual(out, "REFUSED", rel)
            self.assertNotEqual((self.home / rel).read_text(encoding="utf-8") if (self.home / rel).exists() else "",
                                "x", rel)

    def test_a_leftover_temporary_name_cannot_be_written_through(self):
        # post-panel run 3 (opus): a launch killed between its link and its unlink leaves its temporary name, a second
        # name of the hook source; skipped as transient, it stayed writable, and the link count took it off
        hook = self.home / ".claude" / "settings.local.json"
        leftover = ".claude/.settings.local.json.playbook-123-abcdef012345"
        os.link(hook, self.home / leftover)
        self.assertEqual(self.attempt(leftover, "claude"), "REFUSED")
        self.assertEqual(hook.read_text(encoding="utf-8"), "original\n")

    def test_a_read_only_file_cannot_be_replaced_or_removed(self):
        for verb in ('mv "$HOME/.claude/x" "$HOME/.claude/settings.json"', 'rm -f "$HOME/.claude/settings.json"',
                     'rm -rf "$HOME/.claude/plugins"'):
            self.run_in(f'printf x > "$HOME/.claude/x"; {verb} 2>/dev/null; true')
            self.assertEqual((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"), "original\n")
            self.assertTrue((self.home / CODE_AND_CONFIG[0]).exists(), verb)


if __name__ == "__main__":
    unittest.main()
