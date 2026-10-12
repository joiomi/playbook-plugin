"""Live rows of the install group (task 184, W2): a disposable install through Claude Code's real
plugin manager — never the owner's.

The disposable HOME is given to EVERY subprocess of the group (HOME and the XDG dirs point into
it, BASH_ENV is dropped), it is printed before the first command, and the owner's own plugin and
settings files are hashed before and after as a second line. No login is copied: the plugin
manager runs without one (measured before this was written).

The release flow is the owner's own, on scratch repositories: a bare repository stands for the
GitHub repository, a clone of it is the "release clone" the marketplace is a directory of. The
older install is v1.6.0 (the nested `main`); the newer is the CANDIDATE commit with its version
set to 1.6.1 in the scratch clone only. The upgrade's and init's own shell blocks are taken from
the installed copy's `commands/upgrade.md` and `commands/init.md` and run as they are written."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from common import NESTED, Run, clean_env, owner_home_guard, snapshot

OLD_REF = "main"                      # v1.6.0 on the nested repository
NEW_VERSION = "1.6.1"
MARKET = "playbook-x-marketplace"
NAME = "gauntlet-project"
USER_CLAUDE = "# My project\n\nMy own rules, written by hand.\n\n## Notes\n- keep this\n"


def _blocks(md: Path) -> "list[str]":
    """The ```bash blocks of a command file, in order, as written."""
    return re.findall(r"```bash\n(.*?)```", md.read_text(encoding="utf-8"), re.S)


CREDENTIAL_LIKE = re.compile(r"(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$|^(ANTHROPIC|OPENAI|XAI|GEMINI|GOOGLE)_")
ENV_PROBE = ("for v in HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME; do printf '%s=%s\\n' \"$v\" \"${!v}\"; done; "
             "for v in BASH_ENV CLAUDE_CONFIG_DIR; do if [ -n \"${!v+x}\" ]; then echo \"$v is SET\"; "
             "else echo \"$v is unset\"; fi; done; echo \"credential-like variables: $(env | cut -d= -f1 | "
             "grep -cE '(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$|^(ANTHROPIC|OPENAI|XAI|GEMINI|GOOGLE)_')\"")


def install_env(home: Path) -> "dict[str, str]":
    """The environment of every command of the install group. CLAUDE_CONFIG_DIR would send the plugin manager's
    writes to the owner's real configuration whatever HOME says, and the group needs no login: no
    credential-like variable reaches it (impl panel I11, I12). One function, so the selfcheck tests THIS
    construction, not a copy of it (round 2, T7)."""
    env = clean_env({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                     "XDG_CACHE_HOME": str(home / ".cache"), "XDG_DATA_HOME": str(home / ".local/share"),
                     "SHELL": "/bin/bash"}, drop=("CLAUDE_CONFIG_DIR",))
    return {k: v for k, v in env.items() if not CREDENTIAL_LIKE.search(k)}


class Install:
    """One disposable install: its HOME, its repositories, its project."""

    def __init__(self, run: Run, root: Path):
        self.run, self.root = run, root
        self.home = root / "home"
        self.home.mkdir()
        (self.home / ".bashrc").write_text("# the user's own shell setup\nexport MY_VAR=1\n", encoding="utf-8")
        self.env = install_env(self.home)
        self.shown = {k: self.env[k] for k in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME")}
        self.bare, self.release, self.work, self.proj = root / "github.git", root / "release", root / "work", root / "proj"

    def step(self, title, argv, cwd=None, **kw):
        return self.run.step(title, argv, cwd=cwd or self.root, env=kw.pop("env", self.env), env_shown=self.shown, **kw)

    def manifest(self) -> dict:
        p = self.home / ".claude" / "plugins" / "installed_plugins.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def entry(self) -> dict:
        return ((self.manifest().get("plugins") or {}).get(f"playbook@{MARKET}") or [{}])[0]

    def setup_old_install(self) -> None:
        # only the isolation variables are printed, never the whole environment (it lands in the artifact)
        r = self.step("the disposable HOME every command of this group runs with", ENV_PROBE)
        self.run.clause("the group's HOME is the disposable one; BASH_ENV, CLAUDE_CONFIG_DIR and every "
                        "credential-like variable are absent",
                        f"HOME={self.home}\n" in r.stdout and "BASH_ENV is unset" in r.stdout
                        and "CLAUDE_CONFIG_DIR is unset" in r.stdout and "credential-like variables: 0" in r.stdout,
                        " / ".join(r.stdout.strip().splitlines()))
        self.step("the 'GitHub' repository: a bare clone of the nested repository", ["git", "clone", "-q", "--bare", str(NESTED), str(self.bare)])
        self.step("the release clone the marketplace is a directory of, on v1.6.0", ["git", "clone", "-q", "-b", OLD_REF, str(self.bare), str(self.release)])
        self.step("add the marketplace (a local directory)", ["claude", "plugin", "marketplace", "add", str(self.release)])
        self.step("install the plugin from it", ["claude", "plugin", "install", f"playbook@{MARKET}"], timeout=300)
        self.proj.mkdir()
        for c in (["git", "init", "-q"], ["git", "config", "user.email", "u@u"], ["git", "config", "user.name", "u"]):
            subprocess.run(c, cwd=self.proj, check=True, env=self.env)
        (self.proj / "CLAUDE.md").write_text(USER_CLAUDE, encoding="utf-8")
        (self.proj / "notes.md").write_text("the user's notes\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.proj, check=True, env=self.env)
        subprocess.run(["git", "commit", "-qm", "user content"], cwd=self.proj, check=True, env=self.env)
        self.init("init the project with the installed (older) copy, as /playbook:init does")

    def init(self, title, *, env=None):
        copy = Path(self.entry().get("installPath", ""))
        block = _blocks(copy / "commands" / "init.md")[0].replace("<project name>", NAME)
        # one observation added to the block as written: which init script it resolved
        block = block.replace('bash "$INIT_SCRIPT"', 'echo "RESOLVED INIT_SCRIPT=$INIT_SCRIPT" >&2\nbash "$INIT_SCRIPT"')
        r = self.step(title + f" — the block of {copy.name}'s commands/init.md", block, cwd=self.proj, env=env or self.env,
                      timeout=300)
        return r

    def version(self, title) -> str:
        r = self.step(title, [str(self.proj / ".claude" / "bin" / "tasks"), "--version"], cwd=self.proj)
        return r.stdout.strip()

    def publish_candidate(self) -> None:
        head = subprocess.run(["git", "-C", str(NESTED), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.step("a scratch working clone of 'GitHub'", ["git", "clone", "-q", str(self.bare), str(self.work)])
        self.step("the candidate commit becomes its main", f"git fetch -q {NESTED} {head} && git checkout -q -B main FETCH_HEAD",
                  cwd=self.work)
        pj = self.work / "plugins" / "playbook" / ".claude-plugin" / "plugin.json"
        data = json.loads(pj.read_text(encoding="utf-8"))
        old = data["version"]
        data["version"] = NEW_VERSION
        pj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.run.note(f"(scratch clone only) plugin.json version {old} -> {NEW_VERSION} on top of candidate {head[:12]}")
        self.step("the release commit, pushed to 'GitHub'", "git -c user.email=r@r -c user.name=release commit -qam "
                  f"'release {NEW_VERSION} (scratch)' && git push -q origin main", cwd=self.work)

    def upgrade(self) -> None:
        """/playbook:upgrade steps 2 and 3, from the installed copy's own commands/upgrade.md."""
        copy = Path(self.entry().get("installPath", ""))
        blocks = _blocks(copy / "commands" / "upgrade.md")
        self.run.note(f"commands/upgrade.md of the installed copy ({copy}) has {len(blocks)} bash blocks; steps 2-3 = blocks 2-4")
        for i, b in enumerate(blocks[1:4], 2):
            self.step(f"/playbook:upgrade block {i}, as written", b, cwd=self.proj, timeout=300)


def upgrade_installed_copy(out: Path) -> int:
    run = Run("PB-UPGRADE-INSTALLED-COPY", "an older install upgraded through the real plugin manager, and init re-run "
              "against the copy the manifest names", out, ["linux"])
    guard = owner_home_guard()
    tmp = Path(tempfile.mkdtemp(prefix="pb184-up-"))
    try:
        ins = Install(run, tmp)
        ins.setup_old_install()
        old = ins.entry()
        run.clause("the older install is v1.6.0 in the manifest", old.get("version") == "1.6.0", json.dumps(old)[:200])
        v0 = ins.version("the project's tasks wrapper reports the installed version")
        run.clause("the project runs the older copy", v0.endswith("1.6.0"), repr(v0))
        old_dir = Path(old.get("installPath", ""))
        side = tmp / "kept-1.6.0"
        shutil.copytree(old_dir, side, symlinks=True)
        ins.publish_candidate()
        ins.upgrade()
        new = ins.entry()
        run.clause("the plugin manager updated the manifest to the new version and a new copy",
                   new.get("version") == NEW_VERSION and new.get("installPath") != old.get("installPath")
                   and Path(new.get("installPath", "")).is_dir(), json.dumps(new)[:220])
        r = ins.init("step 4: init re-run")
        resolved = next((x.split("=", 1)[1] for x in r.stderr.splitlines() if x.startswith("RESOLVED INIT_SCRIPT=")), "")
        run.clause("init re-run resolves the copy the manifest names and succeeds", r.returncode == 0
                   and resolved == str(Path(new.get("installPath", "")) / "scripts" / "init") and "FAILED" not in r.stdout,
                   f"rc={r.returncode}; resolved {resolved!r}")
        v1 = ins.version("step 5: verify")
        run.clause("the project now runs the new copy", v1.endswith(NEW_VERSION), repr(v1))
        claude_md = (ins.proj / "CLAUDE.md").read_text(encoding="utf-8")
        run.clause("the user's own CLAUDE.md content survived both inits", "My own rules, written by hand." in claude_md
                   and "- keep this" in claude_md and (ins.proj / "notes.md").read_text() == "the user's notes\n",
                   f"own rules kept: {'My own rules' in claude_md}; notes intact: {(ins.proj / 'notes.md').exists()}")
        # the manifest, not a glob: an older copy beside a newer one, the manifest naming the older
        if not old_dir.exists():
            shutil.copytree(side, old_dir, symlinks=True)
            run.note(f"(the plugin manager removed {old_dir}; restored beside the new copy for the control)")
        mp = ins.home / ".claude" / "plugins" / "installed_plugins.json"
        saved = mp.read_text(encoding="utf-8")
        data = json.loads(saved)
        data["plugins"][f"playbook@{MARKET}"][0].update(installPath=str(old_dir), version="1.6.0")
        mp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        v2 = ins.version("CONTROL: the manifest names the OLDER copy while the newer one is still in the cache")
        run.clause("the wrapper follows the manifest's installPath, not the newest copy a glob would find",
                   v2.endswith("1.6.0") and Path(new.get("installPath", "")).is_dir(), repr(v2))
        mp.write_text(saved, encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    after = owner_home_guard()
    changed = [k for k in guard if guard[k] != after[k] and not k.endswith(".claude.json")]
    run.clause("the owner's own plugin and settings files are untouched", not changed, f"changed: {changed or 'none'}")
    return run.finish()


def _state(ins: Install) -> dict:
    """What a rollback must restore: the project's files, and the user files init touches."""
    files = {}
    for name in (".claude/settings.json", ".claude/bash-log.sh", ".claude/bash-log.zsh", ".bashrc", ".profile"):
        p = ins.home / name
        files["HOME/" + name] = p.read_bytes() if p.exists() else None
    return {"project": snapshot(ins.proj), "home": files}


def _diff(a, b) -> "list[str]":
    d = [f"project {k}" for k in sorted(set(a["project"]) | set(b["project"])) if a["project"].get(k) != b["project"].get(k)
         and not k.startswith(".agent/backups/") and not k.startswith(".agent/.init-txn")]
    d += [k for k in a["home"] if a["home"][k] != b["home"][k]]
    return d


def install_rollback(out: Path) -> int:
    run = Run("PB-INSTALL-ROLLBACK", "init of the upgraded copy interrupted at controlled points: nothing lost, no mixed "
              "version, and a re-run recovers", out, ["linux"])
    run.note("Interruptions only at seams this run controls (task 184 plan panel P8): a project write the upgrade MUST make "
             "fails (`.claude/hooks/monitor-nudge.sh` differs between the two versions — file and directory read-only), a "
             "write in the user's HOME fails (`~/.claude` read-only), and SIGTERM is sent to init when it starts its "
             "CLAUDE.md merge. Each is compared with a snapshot taken right before it. The plugin manager's own copy step is "
             "NOT interrupted and not covered here.")
    guard = owner_home_guard()
    tmp = Path(tempfile.mkdtemp(prefix="pb184-rb-"))
    try:
        ins = Install(run, tmp)
        ins.setup_old_install()
        ins.publish_candidate()
        ins.upgrade()
        hooks_dir = ins.proj / ".claude" / "hooks"
        nudge = hooks_dir / "monitor-nudge.sh"
        new_copy = Path(ins.entry().get("installPath", ""))
        run.clause("the seam is real: the new version's monitor-nudge.sh differs from the project's",
                   nudge.exists() and (new_copy / "hooks" / "monitor-nudge.sh").read_bytes() != nudge.read_bytes(),
                   f"project {nudge.stat().st_size if nudge.exists() else '-'} B vs new copy "
                   f"{(new_copy / 'hooks' / 'monitor-nudge.sh').stat().st_size} B")

        def vector(label, prepare, restore, env=None):
            before = _state(ins)
            prepare()
            try:
                r = ins.init(f"{label}: init of the new copy", env=env)
            finally:
                restore()
            diff = _diff(before, _state(ins))
            run.clause(f"{label}: init fails, says it restores, and the project and the user's files are byte-identical to "
                       f"right before", r.returncode != 0 and "restoring the pre-init state" in r.stderr and not diff,
                       f"rc={r.returncode}; restore said: {'restoring the pre-init state' in r.stderr}; differs: {diff or 'nothing'}")

        def lock_hooks():
            nudge.chmod(stat.S_IRUSR)
            hooks_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)

        def unlock_hooks():
            hooks_dir.chmod(0o755)
            nudge.chmod(0o755)

        vector("a project write the upgrade must make fails", lock_hooks, unlock_hooks)
        hc = ins.home / ".claude"
        vector("a write in the user's HOME fails (~/.claude read-only)", lambda: hc.chmod(stat.S_IRUSR | stat.S_IXUSR),
               lambda: hc.chmod(0o755))
        shim = tmp / "shim"
        shim.mkdir()
        pidfile = tmp / "init.pid"
        real_py = shutil.which("python3")
        (shim / "python3").write_text("#!/bin/bash\nfor a in \"$@\"; do case \"$a\" in *claude-md-merge.py)\n"
                                      f"  kill -TERM \"$(cat '{pidfile}')\" 2>/dev/null; sleep 0.3; exit 0 ;;\nesac; done\n"
                                      f"exec '{real_py}' \"$@\"\n", encoding="utf-8")
        (shim / "python3").chmod(0o755)
        term_env = dict(ins.env, PATH=f"{shim}{os.pathsep}{ins.env['PATH']}")
        copy = Path(ins.entry().get("installPath", ""))
        block = _blocks(copy / "commands" / "init.md")[0].replace("<project name>", NAME)
        block = block.replace('bash "$INIT_SCRIPT"', f'echo $$ > "{pidfile}"; exec bash "$INIT_SCRIPT"')
        before = _state(ins)
        r = ins.step("SIGTERM to init as it starts its CLAUDE.md merge — init.md's block, exec'd so init holds the pid",
                     block, cwd=ins.proj, env=term_env, timeout=300)
        diff = _diff(before, _state(ins))
        run.clause("init interrupted by a signal restores everything", r.returncode != 0
                   and "restoring the pre-init state" in r.stderr and not diff,
                   f"rc={r.returncode}; differs: {diff or 'nothing'}")
        # CONTROL: the transaction off, the same failing project write — the restore is what kept things intact
        before = _state(ins)
        lock_hooks()
        try:
            r = ins.init("CONTROL: the same project write failing with the transaction off (PB_INIT_NO_TXN=1)",
                         env=dict(ins.env, PB_INIT_NO_TXN="1"))
        finally:
            unlock_hooks()
        diff = _diff(before, _state(ins))
        run.clause("CONTROL: without the transaction the same failure leaves the project or the user's files changed",
                   r.returncode != 0 and bool(diff), f"rc={r.returncode}; differs: {diff[:6]}")
        r = ins.init("recovery: the same init, nothing in the way")
        v = ins.version("the version the project runs after the recovery")
        claude_md = (ins.proj / "CLAUDE.md").read_text(encoding="utf-8")
        run.clause("a plain re-run recovers even after the control's half-done run: init succeeds, one version (the new) "
                   "runs, the user's content is there", r.returncode == 0 and v.endswith(NEW_VERSION)
                   and "My own rules, written by hand." in claude_md and nudge.read_bytes()
                   == (new_copy / "hooks" / "monitor-nudge.sh").read_bytes(), f"rc={r.returncode}; version {v!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    after = owner_home_guard()
    changed = [k for k in guard if guard[k] != after[k] and not k.endswith(".claude.json")]
    run.clause("the owner's own plugin and settings files are untouched", not changed, f"changed: {changed or 'none'}")
    return run.finish()


ROWS = {
    "PB-UPGRADE-INSTALLED-COPY": upgrade_installed_copy,
    "PB-INSTALL-ROLLBACK": install_rollback,
}
