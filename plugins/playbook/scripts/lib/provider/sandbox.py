"""Unified sandbox launcher for headless and interactive agent invocations.

Single source of truth for write-containment when running any of the supported
CLI agents (claude, codex, agy, pi). Backend: Linux bubblewrap (bwrap) — the only
one since 2026-10-08 (owner decision, Linux-only; the macOS backend
ended with 1.5.47, tag `last-multiplatform`). Stdlib only.

Callers (cli.py judge dispatch, adapter run_headless_judge, bin/sandbox shim)
import from here; do not re-implement profile generation elsewhere.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Priority order for default_agent() — first available wins. New providers are
# APPENDED (grok after pi) so adding one never changes the existing default for
# installs that already have an earlier provider (task 014 I7).
_AGENT_ORDER: tuple[str, ...] = ("claude", "codex", "agy", "pi", "grok")

# Binary names per agent. Pi may resolve via the `omlx` launcher when `pi` itself
# is absent (omlx launches pi via os.execvpe, inheriting our sandbox).
_AGENT_BINARIES: dict[str, tuple[str, ...]] = {
    "claude": ("claude",),
    "codex": ("codex",),
    "agy": ("agy",),
    "pi": ("pi", "omlx"),
    "grok": ("grok",),
}

# Per-agent bypass-flag injection. These are appended to argv at the top level
# (i.e. before any subcommand). Codex's bypass is technically an `exec`
# subcommand flag — callers building codex argv must insert it AFTER the
# `exec` token themselves; bypass_args() returns the flag string for the map,
# the launcher routes accordingly.
_BYPASS_FLAGS: dict[str, list[str]] = {
    "claude": ["--dangerously-skip-permissions"],
    "agy": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "pi": [],
    "grok": ["--always-approve"],
}

# Home-relative state folders, by the agent whose run has them writable: claude's,
# codex's, agy's (~/.gemini — bound in place, never copied; its sign-in itself is in
# the OS keyring, which a sandboxed agy reaches over the D-Bus socket under the
# read-only root: task 111), grok's (auth/sessions/leader socket — grok dies at
# startup with FS_PERMISSION_DENIED if ~/.grok is read-only), pi's config and omlx's
# server data. Task 308 (audit 4 G3): a run has only ITS agent's folders writable — a
# codex judge no longer reaches ~/.claude (impl round 1: agy, codex, grok, opus) — and
# the generic `.cache` and `.local` are gone: they hold a person's data (keyrings, the
# Trash, mail under ~/.local/share), the commands on PATH (~/.local/bin), the user's
# Python packages and other programs' code. The write log keeps its own bind.
_AGENT_HOME: dict[str, tuple[str, ...]] = {
    "claude": (".claude",),
    "codex": (".codex",),
    "agy": (".gemini",),
    "grok": (".grok",),
    "pi": (".pi", ".omlx"),
}

# Task 308 (impl round 2): inside the running agent's state folder, every entry that
# exists at launch is read-only but those the agent writes as it runs — a list of what
# NOT to write had a new hole each review round (grok's trusted folders and skills,
# agy's own settings). The entries below were measured 2026-10-25 05:27 EET, one judge
# run of each agent through this launcher, with the folders' full listings; the rest of
# each name is what its own name says it is (a session, a log, a cache, a sign-in). A
# mixed folder (a key with a `/`) is pinned and gets the same rule one level down. A
# folder with no list (pi's, omlx's: on no panel, not measured) has only new names writable.
# Read-only, for example: the plugins and their install records, settings, remote
# settings, instructions, the agent's own install, a status line, Chrome's native host,
# Claude Code's daemon and jobs, grok's trusted folders and sandbox profile.
_HOME_WRITES: dict[str, tuple[str, ...]] = {
    ".claude": ("projects", "sessions", "todos", "statsig", "telemetry", "debug", "cache", "backups", "file-history",
                "paste-cache", "plans", "uploads", "feedback", "ide", "state", ".credentials.json", "history.jsonl",
                "stats-cache.json", ".last-cleanup", ".last-update-result.json", "mcp-needs-auth-cache.json",
                "*.lock"),
    ".codex": ("sessions", "session_index.jsonl", "history.jsonl", "auth.json", "cache", "log",
               "*.sqlite", "*.sqlite-shm", "*.sqlite-wal", "*.lock", "models_cache.json", "installation_id",
               "version.json", "thread-writer-locks", "app-server-control",
               "tui-thread-reference-capabilities", ".personality_migration", ".sandbox_migration"),
    ".codex/.tmp": ("git-*", "*.lock", "plugins.sha"),
    ".codex/app-server-daemon": ("daemon*", "*.lock", "*.pid", "*.log", "*.log.*", "*.sock"),
    ".gemini": ("google_accounts.json", "history", "installation_id", "oauth_creds.json", "projects.json",
                "state.json", "tmp"),
    ".gemini/antigravity-cli": ("annotations", "brain", "cache", "cli.log", "conversations",
                                "conversation_summaries.db*", "crashes", "history.jsonl", "implicit", "installation_id",
                                "jetbox_summaries_proto.pb", "jetski_state.pbtxt", "last_check.timestamp", "log",
                                "presence"),
    ".pi": (),
    ".omlx": (),
    ".grok": ("active_sessions.json", "*.lock", "agent_id", "auth.json", "campaigns_state.json", "last-copy.txt",
              "logs", "long-running-background-tasks", "memory",
              "memory-v2", "memtrace", ".metadata_version", "models_cache.json", "relocations", "sandbox-blocked.*",
              "sandbox-bwrap-sentinel", "sessions", "settings_cache.json", "slash-mru.json", "tip_cursor.json",
              "version.json", "worktrees.db"),
}
# Config folders made, empty, when missing, so a run cannot make one with a skill, an
# agent, a rule, a hook or a plugin in it (impl rounds 1-2: agy, codex, grok). An
# agent's own install (`~/.claude/local`, `~/.codex/packages`, …) is not made: an empty
# one could make its updater take it for an install, and nothing runs a missing one.
_HOME_RO_DIRS_MADE: tuple[str, ...] = (
    ".claude/plugins", ".claude/hooks", ".claude/commands", ".claude/agents", ".claude/skills",
    ".claude/output-styles",
    ".codex/rules", ".codex/skills", ".codex/plugins",
    ".gemini/config", ".gemini/extensions",
    ".grok/hooks", ".grok/installed-plugins", ".grok/bundled", ".grok/skills", ".grok/plugins", ".grok/commands",
    ".grok/agents",
)
# What a host session sources before its shell calls: the shell snapshots, and Claude
# Code's per-session environment scripts (`session-env/<id>/sessionstart-hook-*.sh` —
# impl round 2, opus and grok). Each is a private, empty tmpfs in the sandbox: the run
# writes its own there, and the host's — a session's started during the run too — are
# neither seen nor written (impl round 1, opus).
_HOME_PRIVATE: tuple[str, ...] = (".claude/shell-snapshots", ".claude/session-env", ".codex/shell_snapshots",
                                  ".codex/tmp")
# A hook source a run could otherwise create where none is (impl round 3, grok): made with its empty content when
# missing, read-only from there on — `{}` for JSON, nothing for TOML. Grok also loads Claude Code's two settings files.
_HOME_FILES_MADE: dict[str, str] = {
    ".claude/settings.json": "{}\n",
    ".claude/settings.local.json": "{}\n",
    ".grok/managed_config.toml": "",
}


def _publish_new(path: Path, text: str) -> None:
    """Make `path` with `text`, whole or not at all, and never over a file that is there (post-panel run 1, codex: made
    empty and written after, a crash between left an empty settings file for good). Written to a temporary name of its
    own beside it, then linked to `path` — link(2) fails where anything is — and the temporary name removed."""
    # a name of this call's own: a panel launches its seats side by side in one process (verify, 2026-10-25 06:35 —
    # the pid alone collided)
    tmp = path.with_name(f".{path.name}.playbook-{os.getpid()}-{os.urandom(6).hex()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass                                        # made meanwhile: that one stays
    finally:
        os.unlink(tmp)


def _home_refusal(path: Path, why: str) -> RuntimeError:
    return RuntimeError(f"{path} {why}. Nothing was launched.")


_LINK_WHY = ("is a link inside a folder a sandboxed run may write: the run could replace the link, and a later "
             "session on this machine would follow it. Replace the link with the file or folder it names")


def _home_layer(home: Path, folders: "Iterable[str]",
                also_writable: "Iterable[str]" = ()) -> "tuple[list[str], list[str], list[str]]":
    """What a run with `folders` (its agent's state folders) writable must not change there: (pins, read-only paths,
    private folders), each an absolute path. Read-only: every entry of a state folder that exists at launch and is not
    one `_HOME_WRITES` lets its agent write; a mixed folder there is pinned (bound onto itself first, so it cannot be
    moved aside and replaced — impl round 1: codex, grok, sonnet, as task 181 pins git's places) and judged the same way
    one level down. Private: `_HOME_PRIVATE`. The launch is refused (RuntimeError) for a state folder, a read-only
    entry, a pin or a private folder that is a link (bubblewrap cannot bind onto it, and the link itself could be
    replaced), a private path that is not a folder, a read-only file with another hard link, a link inside a
    read-only folder whose chain passes through or ends in a writable place of the run (`/tmp`, a state folder,
    `also_writable`: the write log, the extra writable paths, a writable project — impl round 3: agy, codex, grok,
    opus), and a folder that cannot be read (impl round 2: codex). Places are compared resolved (impl round 3, agy:
    a `/home` that is itself a link)."""
    import fnmatch
    tops = [home / f for f in folders]
    for t in tops:
        if t.is_symlink():
            raise _home_refusal(t, _LINK_WHY)
    mine = [t for t in tops if t.is_dir()]
    names = {t.name for t in mine}
    private = [home / rel for rel in _HOME_PRIVATE if rel.split("/")[0] in names]
    pins: "list[str]" = []
    ro: "list[str]" = []

    def entries(d: Path) -> "list[Path]":
        try:
            return sorted(d.iterdir())
        except OSError as exc:
            raise _home_refusal(d, f"cannot be read ({exc.strerror or exc})") from None

    def judge(rel: str) -> None:
        d = home / rel
        allow = _HOME_WRITES.get(rel)
        if allow is None:
            return                                      # not measured: stays writable (pi, omlx)
        for p in entries(d):
            sub = f"{rel}/{p.name}"
            if p in private:
                continue
            if sub in _HOME_WRITES:                     # a mixed folder: pinned, then judged inside
                if p.is_symlink():
                    raise _home_refusal(p, _LINK_WHY)
                if p.is_dir():
                    pins.append(str(p))
                    judge(sub)
                    continue                            # not a folder (impl round 3, agy): held as it is
            if any(fnmatch.fnmatchcase(p.name, pat) for pat in allow):
                continue
            if p.is_symlink():
                raise _home_refusal(p, _LINK_WHY)
            ro.append(str(p))

    for t in mine:
        judge(t.name)
    for d in private:
        if d.is_symlink():
            raise _home_refusal(d, _LINK_WHY)
        if os.path.lexists(d) and not d.is_dir():
            raise _home_refusal(d, "is not a folder, where a sandboxed run keeps a private one")
    real = os.path.realpath
    writable = [real(w) for w in ("/tmp", *(str(t) for t in mine), *also_writable)]
    kept = [real(r) for r in ro]

    def inside(path: str, places: "list[str]") -> bool:
        return any(path == w or path.startswith(w.rstrip("/") + "/") for w in places)

    later = [real(w) for w in also_writable]           # bound after the kept paths: they win over them

    def in_writable(path: str) -> bool:
        """Is `path` (its folders resolved) writable in the run? An extra writable path, a writable project or the
        write log always is (post-panel run 1, codex: bound after a kept path, it wins); /tmp and the state folder
        are, outside the kept paths."""
        return inside(path, later) or (inside(path, writable) and not inside(path, kept))

    def through_a_replaceable_link(path: str) -> bool:
        """Does `path`, unresolved, pass through a folder that is a link sitting in a place the run may write?
        The run could re-point that link (post-panel run 1, codex: `~/.claude/cache` → elsewhere)."""
        parts = Path(path).parts
        for k in range(2, len(parts)):
            pre = os.path.join(*parts[:k])
            if os.path.islink(pre) and in_writable(os.path.join(real(os.path.dirname(pre)), os.path.basename(pre))):
                return True
        return False

    def leads_out(link: str) -> bool:
        """Does the chain of links from `link` pass through, or end in, a place the run may write? Each hop's own
        place is judged, not only the end (impl round 3, opus: a link in a writable place on the way can be
        replaced)."""
        cur = link
        for _ in range(40):
            try:
                t = os.readlink(cur)
            except OSError:
                return False                            # not a link (any more): the chain ends here
            nxt = t if os.path.isabs(t) else os.path.join(os.path.dirname(cur), t)
            if through_a_replaceable_link(nxt):
                return True
            nxt = os.path.join(real(os.path.dirname(nxt)), os.path.basename(nxt))
            if in_writable(nxt):
                return True
            if not os.path.islink(nxt):
                return False
            cur = nxt
        return True                                     # a chain this long is not followed: refused

    # A read-only file with another hard link is writable through that other name: refused for each read-only
    # entry itself. Inside a read-only folder it is not looked for — a run cannot make such a link (link(2) across
    # the bind fails with EXDEV, measured), and package stores fill folders with them (pnpm: 102,743 under
    # ~/.claude/jobs here). Links there are: one that leads into a writable place of the run would let it write
    # what the host then reads (impl round 2, codex).
    import stat as _stat
    for path in ro:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue                                    # gone between the listing and the look
        if _stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
            raise _home_refusal(Path(path), "has another hard link: what a sandboxed run writes through that other "
                                            "name changes this file. Make it a copy of its own")
        if not _stat.S_ISDIR(st.st_mode):
            continue
        stack = [path]                                  # the entries' own types (scandir): no look per entry
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        if e.is_symlink():
                            if leads_out(e.path):
                                raise _home_refusal(Path(e.path), "is a link from a read-only place into one a "
                                                    "sandboxed run may write: what the run writes there, a later "
                                                    "session on this machine would read through it. Replace the "
                                                    "link with a copy")
                        elif e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
            except FileNotFoundError:
                continue                                # gone between the listing and the look
            except OSError as exc:
                raise _home_refusal(Path(path), f"cannot be read ({exc.strerror or exc})") from None
    return pins, ro, [str(d) for d in private]


# Friendly model aliases — collapse "agent + canonical model" into one knob.
# Loaded from JSON so agents can edit the table without a code release. Model
# strings drift between releases (claude-opus-4-7 → 4-8, gpt-5.5 → 5.6, …);
# updating `models.json` is enough.
#
# Resolution order:
#   1. Plugin default: <this_file_dir>/models.json (ships with the plugin)
#   2. Project override: <cwd>/.agent/models.json (writable from inside the
#      sandbox, so agents can pin per-project versions)
# Per-alias keys merge with the project file winning. Missing/malformed JSON
# is non-fatal — falls back to empty {} and pattern inference still works.
#
# Schema (both files):
#   {"aliases": {"<label>": ["<agent>", <model_or_null>, [<extras>...]]}}

def _parse_models_json(path: Path) -> dict[str, tuple[str, str | None, tuple[str, ...]]]:
    """Read one models.json. Returns {} on any parse/shape error."""
    import json
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):  # valid JSON but not an object (e.g. a bare list)
        return {}
    aliases = raw.get("aliases", {})
    if not isinstance(aliases, dict):
        return {}
    out: dict[str, tuple[str, str | None, tuple[str, ...]]] = {}
    for label, entry in aliases.items():
        if not isinstance(label, str) or not isinstance(entry, list) or len(entry) != 3:
            continue
        agent, model, extras = entry
        if not isinstance(agent, str):
            continue
        if model is not None and not isinstance(model, str):
            continue
        if not isinstance(extras, list) or not all(isinstance(x, str) for x in extras):
            continue
        out[label] = (agent, model, tuple(extras))
    return out


def _find_project_models_override(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default cwd) for a `.agent/models.json`.

    Callers that know the project root (tasks CLI) should pass it — the cwd
    walk-up can resolve a DIFFERENT project's config when invoked from a
    nested/outside directory (task 012 fable F1).
    """
    base = start or Path.cwd()
    for d in (base, *base.parents):
        candidate = d / ".agent" / "models.json"
        if candidate.is_file():
            return candidate
    return None


def _load_model_aliases() -> dict[str, tuple[str, str | None, tuple[str, ...]]]:
    """Plugin default + project override merged (project keys win)."""
    aliases: dict[str, tuple[str, str | None, tuple[str, ...]]] = {}
    default_path = Path(__file__).parent / "models.json"
    if default_path.is_file():
        aliases.update(_parse_models_json(default_path))
    project_override = _find_project_models_override()
    if project_override:
        aliases.update(_parse_models_json(project_override))
    return aliases


MODEL_ALIASES: dict[str, tuple[str, str | None, tuple[str, ...]]] = _load_model_aliases()


def resolve_model(model: str) -> tuple[str, str | None, tuple[str, ...]]:
    """Map a `--model X` value to `(agent, canonical_model_or_None, extra_args)`.

    Resolution order:
      1. Direct alias hit in MODEL_ALIASES.
      2. Pattern inference on canonical IDs:
         - `claude-*` → claude
         - `gpt-*` / `o1-*` / `o3-*` / `o4-*` → codex
         - `gemini-*` → agy (drop model — agy has no `-m`)
         - `vendor/model` (contains `/`) → pi via openrouter
         - `qwen*` → pi via oss
      3. No match → raise ValueError (caller must supply `--agent`).
    """
    if model in MODEL_ALIASES:
        return MODEL_ALIASES[model]
    if model.startswith("claude-"):
        return ("claude", model, ())
    if model in {"o1", "o3", "o4"} or model.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return ("codex", model, ())
    if model.startswith("gemini-"):
        return ("agy", None, ())  # agy has no -m; drop the model arg
    if model.startswith("grok-"):
        return ("grok", model, ())
    if "/" in model:
        return ("pi", model, ("--provider", "openrouter"))
    if model.startswith("qwen"):
        return ("pi", model, ("--provider", "oss"))
    raise ValueError(
        f"Cannot infer agent from model {model!r}. "
        f"Pass --agent explicitly, or use one of: {', '.join(MODEL_ALIASES)}"
    )


# ── Judge selection (panel-review + single judge) ────────────────────────────
# Canonical provider keys ("claude" | "codex" | "agy" | "pi") plus the synonyms
# callers may type. A judge spec is one of: "provider:variant", a bare provider,
# or an alias from models.json. Resolution returns (provider, variant_or_None);
# the CLI maps the provider string to a concrete adapter class (kept here to
# avoid importing adapters into sandbox.py — adapters import sandbox, not vice
# versa).
_PROVIDER_SYNONYMS: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "agy": "agy", "antigravity": "agy", "gemini": "agy",
    "pi": "pi", "qwen": "pi",
    "grok": "grok",
}


def _parse_judge_config(path: Path) -> dict:
    """Read `default_judge` / `panel` from one models.json. {} on any error."""
    import json
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):  # valid JSON but not an object → {} per contract
        return {}
    out: dict = {}
    dj = raw.get("default_judge")
    if isinstance(dj, str) and dj.strip():
        out["default_judge"] = dj.strip()
    panel = raw.get("panel")
    if isinstance(panel, list) and all(isinstance(x, str) for x in panel):
        out["panel"] = [x for x in panel if x.strip()]
    return out


def load_judge_config(project_root: Path | None = None) -> dict:
    """Plugin default ⊕ project `.agent/models.json` (project wins per key).

    Returns {"default_judge": str | None, "panel": list[str]}.
    A project `panel` REPLACES the shipped panel (not merged) — to add claude
    back, a project lists every judge it wants. Empty/missing → {} defaults.
    Pass `project_root` when known; otherwise the override is found by cwd
    walk-up, which can pick up a different project's config (task 012).
    """
    cfg: dict = {}
    default_path = Path(__file__).parent / "models.json"
    if default_path.is_file():
        cfg.update(_parse_judge_config(default_path))
    override = _find_project_models_override(project_root)
    if override:
        cfg.update(_parse_judge_config(override))
    return {"default_judge": cfg.get("default_judge"), "panel": cfg.get("panel", [])}


def resolve_judge_spec(name: str) -> tuple[str, str | None]:
    """Map a judge spec → (provider, variant_or_None).

    Accepts `provider:variant` (e.g. ``codex:gpt-5.3-codex``,
    ``claude:opus-4-8-1m``), a bare provider (``codex``, ``agy``…), or a
    models.json alias (``gpt``, ``opus``, ``gemini``…). Synonyms
    (antigravity/gemini→agy, qwen→pi) are normalized. Raises ValueError on an
    unknown spec.
    """
    name = name.strip()
    if not name:
        raise ValueError("empty judge spec")
    if ":" in name:
        prov, _, variant = name.partition(":")
        prov = prov.strip().lower()
        if prov not in _PROVIDER_SYNONYMS:
            raise ValueError(f"unknown provider {prov!r} in judge spec {name!r}")
        return (_PROVIDER_SYNONYMS[prov], variant.strip() or None)
    low = name.lower()
    if low in _PROVIDER_SYNONYMS:
        return (_PROVIDER_SYNONYMS[low], None)
    if name in MODEL_ALIASES:
        agent, model, _extras = MODEL_ALIASES[name]
        return (_PROVIDER_SYNONYMS.get(agent, agent), model)
    raise ValueError(
        f"unknown judge spec {name!r}. Use provider:variant, a provider "
        f"({', '.join(sorted(set(_PROVIDER_SYNONYMS.values())))}), or an alias "
        f"({', '.join(MODEL_ALIASES)})."
    )


def format_judge_output(result: subprocess.CompletedProcess) -> str:
    """Render a judge subprocess result so a failure can never masquerade as a
    clean empty review (the T139 `(no output)` bug) — or as a SUCCESSFUL one.

    Returncode wins over stdout (task 012): a CLI that exits nonzero after
    printing progress — or that prints its error to stdout, as claude does
    with budget-exceeded and bad-model messages — previously had its stdout
    returned verbatim and read as a clean review. Failure classification
    (`classify_failure`) runs only over `(FAILED …)`-marked strings, so both
    streams must survive here.

    - exit != 0 → `(FAILED — exit N)` + labeled stdout/stderr tails (2000
      chars each), so the cause AND any model-unavailable signature reach
      judge.md and the classifier.
    - exit 0 + non-empty stdout → returned verbatim (the review).
    - exit 0 + empty stdout → genuinely empty (`(no output)`).
    """
    rc = result.returncode
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if rc != 0:
        parts = [f"(FAILED — exit {rc})"]
        if stdout:
            parts.append(f"[stdout tail]\n{stdout[-2000:]}")
        if stderr:
            parts.append(f"[stderr tail]\n{stderr[-2000:]}")
        if not stdout and not stderr:
            parts.append("(no output captured)")
        # A model-availability signature buried before the truncation point
        # must still reach the classifier (tasks.models_check.classify_failure
        # matches these same anchors) — surface the matched line explicitly.
        block = "\n".join(parts)
        full = stdout + "\n" + stderr
        for sig in _AVAILABILITY_SIGNATURES:
            if sig in full and sig not in block:
                line = next(ln for ln in full.splitlines() if sig in ln)
                parts.append(f"[signature] {line.strip()[:400]}")
        return "\n".join(parts)
    if stdout:
        return result.stdout
    return "(no output)"


# Model-availability failure anchors, kept in sync with the classification
# patterns in tasks/models_check.py (live-captured, task 012).
_AVAILABILITY_SIGNATURES = (
    "model is not supported",
    "requires a newer version of Codex",
    "There's an issue with the selected model",
    "Couldn't set model",  # grok bad-model (task 014) — pairs with the line below
    'Invalid params: "unknown model id"',
)


@dataclass(frozen=True)
class AgentInfo:
    name: str
    binary_path: str | None  # absolute path if found, else None
    via: str | None          # "direct" | "omlx" | None


def detect_agents() -> dict[str, AgentInfo]:
    """Probe which agent CLIs are installed. Returns map agent → AgentInfo."""
    out: dict[str, AgentInfo] = {}
    for agent, binaries in _AGENT_BINARIES.items():
        found: str | None = None
        via: str | None = None
        for bin_name in binaries:
            path = shutil.which(bin_name)
            if path:
                found = path
                via = "direct" if bin_name == agent else bin_name
                break
        out[agent] = AgentInfo(name=agent, binary_path=found, via=via)
    return out


def default_agent() -> str:
    """First available agent by priority. Raises if none installed."""
    agents = detect_agents()
    for name in _AGENT_ORDER:
        if agents[name].binary_path:
            return name
    raise RuntimeError(
        "No supported agent found on PATH (looked for: "
        + ", ".join(sum(_AGENT_BINARIES.values(), ()))
        + ")"
    )


def is_sandboxed() -> bool:
    """True if already inside our sandbox — skip re-wrapping to avoid nesting.

    Sole signal: PLAYBOOK_SANDBOXED=1 in env. `run()` exports this to a child
    it wrapped (and passes it on when nested itself), so a nested invocation
    sees it; a child it could NOT wrap does not get it (task 164). The
    earlier cwd-prefix heuristic (`/tmp/eval-`) was dropped because it could
    skip wrapping when the launching process's cwd happened to be in the
    eval tree but the target project_root was elsewhere.
    """
    return os.environ.get("PLAYBOOK_SANDBOXED") == "1"


@dataclass(frozen=True)
class ProjectView:
    """A review's fixed copy of the tree (task 231, PHASE-B item 7; `tasks/review_view.py`): `root` is mounted
    read-only AT the project's path in place of the project, and each path in `live` (an entry git ignores, bound
    from the live tree as the judges always saw it) is bound read-only at its own path after it."""
    root: str
    live: "tuple[str, ...]" = ()
    # task 231 impl round 1: the `.agent` entries git ignores (the only ones a judge sees from the live tree — G5) and
    # the places hidden from the judge (the copies folder, reachable through the live journal — G2)
    agent_live: "tuple[str, ...]" = ()
    hide: "tuple[str, ...]" = ()


def view_mount_available() -> "tuple[bool, str]":
    """Can a review's copy be mounted here? (True, '') or (False, why). Inside a sandbox no new mount can be made;
    without bubblewrap there is no mount at all — the review then reads the live tree (task 231)."""
    if is_sandboxed():
        return False, "already inside a sandbox — a copy cannot be mounted from here"
    if not _bwrap_exe():
        return False, "bubblewrap (bwrap) is not available"
    return True, ""


def bypass_args(agent: str) -> list[str]:
    """Per-agent bypass-flag injection (copy — callers may mutate)."""
    if agent not in _BYPASS_FLAGS:
        raise ValueError(f"Unknown agent: {agent!r}")
    return list(_BYPASS_FLAGS[agent])


def _normalize_rw(extra_rw: Iterable[str] | None) -> list[str]:
    if not extra_rw:
        return []
    return [str(Path(p).resolve()) for p in extra_rw]


# The conversation records a read-only judge must not read (owner decision
# 2026-10-02, gauntlet 2 G2-05: a judge quoted a canary that existed only in the
# chat log). Names under `.agent` and under each lane dir in it; a file's
# rotated/archived siblings (`chat_log.md.1`, `bash_history.archived-…`) too.
_JUDGE_MASKED_FILES = ("chat_log.md", "bash_history")
_JUDGE_MASKED_DIRS = ("sessions",)


def _is_record_file(name: str) -> bool:
    return any(name == f or name.startswith(f + ".") for f in _JUDGE_MASKED_FILES)


def _agent_dir(project_dir: Path | str) -> "Path | None":
    try:
        agent = (Path(project_dir).resolve() / ".agent").resolve()
        return agent if agent.is_dir() else None
    except OSError:
        return None


def _is_record_dir(name: str) -> bool:
    # `sessions` and its archives (`sessions.archived-1`, task 139 C-3)
    return any(name == d or name.startswith(d + ".") for d in _JUDGE_MASKED_DIRS)


def _symlinked_lanes(agent: Path) -> "dict[str, Path]":
    """{link name: the real directory behind it} for the symlinks directly in `.agent`
    — `--ro-bind / /` shows a symlinked lane at its real
    path too. Resolved ONCE: the sandbox links each name to exactly the directory it
    masks (task 139 C-4 — two reads let a re-point in between link one directory and
    mask another). Not an ancestor of `.agent` (that would hide the project itself; its
    `.agent` part is masked anyway) and not inside it (a lane there is a layer itself)."""
    out: "dict[str, Path]" = {}
    try:
        entries = sorted(os.scandir(agent), key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        try:
            if not (e.is_symlink() and e.is_dir()) or _is_record_dir(e.name):
                continue
            real = Path(e.path).resolve()
        except OSError:
            continue
        if real == agent or agent.is_relative_to(real) or real.is_relative_to(agent) \
                or real in out.values():
            continue
        out[e.name] = real
    return out


def _record_link_targets(agent: Path, linked: "dict[str, Path]") -> "tuple[list[Path], list[Path]]":
    """(files, dirs): the real targets of RECORDS that are themselves symlinks — in
    `.agent`, in each directory directly under it and in each symlinked lane. Masking
    the name alone left the target readable at its real path (task 139 post-D6). Not
    an ancestor of `.agent` (that would hide the project itself)."""
    files: list[Path] = []
    dirs: list[Path] = []
    roots = [agent] + list(linked.values())
    try:
        roots += sorted(Path(e.path) for e in os.scandir(agent)
                        if e.is_dir(follow_symlinks=False))
    except OSError:
        pass
    for root in roots:
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_symlink():
                    continue
                # by NAME when dangling: is_dir() is False for a missing target (run 3)
                if _is_record_dir(e.name) and (e.is_dir() or not os.path.exists(e.path)):
                    bucket = dirs
                elif _is_record_file(e.name) and not e.is_dir():
                    bucket = files
                else:
                    continue
                real = Path(e.path).resolve()
            except OSError:
                continue
            # a DANGLING target is kept too (task 139 post-D6 run 2): the writer may
            # create it while the judge runs
            if agent.is_relative_to(real) or real in files or real in dirs:
                continue
            bucket.append(real)
    return files, dirs


def _bwrap_record_masks(project_dir: Path | str, hidden: "list[str] | None" = None,
                        source_root: "Path | str | None" = None,
                        live_allowed: "Iterable[str] | None" = None) -> list[str]:
    """bwrap args that hide the conversation records from a read-only judge (task
    125; task 138 G3-2..G3-4). `.agent`, each directory in it, and the real
    directory behind each symlinked lane become an empty tmpfs LAYER onto which
    only the non-record entries present at launch are bound back. A record created
    or renamed into place later never appears (a rename over a single-file mount
    detached it on Linux >= 3.18); each record present at launch gets /dev/null as
    a placeholder (opening it is refused: the read-only bind is nodev), `sessions/`
    an empty dir. A layer that cannot be listed stays empty, an entry that cannot
    be read is left out — fail closed, never open. Never raises.

    `hidden`, when given, receives the places these masks hide records in, so that a
    caller can refuse a bind laid over, at or inside one (task 169): `.agent` itself
    and the real directory behind each symlinked lane — the two kinds of tree the
    layers are built in, where a record may also appear later — each record file and
    record directory present now, the real target of a record that is a symlink, and
    a layer that could not be listed. `<project>/.agent` is reported even when it does
    not exist (or is not a directory) and nothing can be masked: a bind laid there
    would CREATE the place the records are then written to.

    `source_root` (task 231): a review's copy mounted at the project path. The records and their real targets are
    still taken from the LIVE `.agent` (an ignored record link is not in the copy, and its target must stay hidden
    — plan panel P2); every other entry is bound back FROM THE COPY when the copy has it, and from the live tree
    only when it does not (an entry git ignores: the journal, models.json, current_user). An entry only the copy has
    is bound from the copy too. Bound onto itself, the live entry would lay the live `.agent` back over the copy
    (plan panel P1). With a copy, an entry neither the copy has nor git ignores (`live_allowed`: the ignored
    `.agent` entries, absolute) is not shown at all — made after the seal, it is no part of what was reviewed (impl
    round 1, G5)."""
    def hide(path) -> None:
        if hidden is not None and str(path) not in hidden:
            hidden.append(str(path))

    try:
        named = Path(project_dir).resolve() / ".agent"
        hide(named)
        hide(named.resolve())           # where a link of that name leads, if it is one
    except OSError:
        pass
    agent = _agent_dir(project_dir)
    if agent is None:
        return []
    args: list[str] = []

    hide(agent)
    done: set[Path] = set()
    linked = _symlinked_lanes(agent)
    try:
        proj_real = Path(project_dir).resolve()
    except OSError:
        proj_real = Path(project_dir)

    allowed = [Path(a) for a in (live_allowed or ())]

    def live_ok(path: str) -> bool:
        return any(_covers(a, Path(path)) for a in allowed)

    def from_copy(path: str) -> "str | None":
        """The copy's counterpart of a live path inside the project, if the copy has it."""
        if source_root is None or not _covers(proj_real, Path(path)):
            return None
        alt = os.path.join(str(source_root), os.path.relpath(path, proj_real))
        return alt if os.path.lexists(alt) else None

    def layer(real: Path, lanes: bool) -> list[Path]:
        """Mount the layer for `real`; return its sub-directories that need their own."""
        if real in done:
            return []
        done.add(real)
        args.extend(["--tmpfs", str(real)])
        try:
            entries = sorted(os.scandir(real), key=lambda e: e.name)
        except OSError:
            hide(real)
            if source_root is None:
                return []
            entries = []        # impl round 2 (agy): a folder removed live still shows the copy's entries
        copy_dir = from_copy(str(real))
        if copy_dir is not None and os.path.isdir(copy_dir) and not os.path.islink(copy_dir):
            # an entry only the copy holds (removed live after the copy was sealed) is the copy's
            names = {e.name for e in entries}
            try:
                entries = sorted(entries + [e for e in os.scandir(copy_dir) if e.name not in names],
                                 key=lambda e: e.name)
            except OSError:
                pass
        subs: list[Path] = []
        for e in entries:
            path = str(real / e.name)
            try:
                link = e.is_symlink()
                is_dir = e.is_dir()
                if _is_record_file(e.name) and not is_dir:
                    args.extend(["--ro-bind", "/dev/null", path])
                    hide(path)
                elif _is_record_dir(e.name) and (is_dir or (link and not os.path.exists(e.path))):
                    args.extend(["--dir", path])
                    hide(path)
                elif link and lanes and e.name in linked:
                    args.extend(["--symlink", str(linked[e.name]), path])   # the masked dir
                elif source_root is not None and from_copy(path) is not None:
                    src = from_copy(path)
                    # the copy's own kind decides (agy, impl round 1: a live link over a copied file was dropped)
                    if os.path.islink(src):
                        args.extend(["--symlink", os.readlink(src), path])
                    elif os.path.isdir(src) and lanes:
                        subs.append(real / e.name)
                    else:
                        args.extend(["--ro-bind", src, path])
                elif source_root is not None and not live_ok(path):
                    continue        # neither copied nor ignored: made after the seal — not shown (G5)
                elif link:
                    args.extend(["--symlink", os.readlink(e.path), path])
                elif is_dir and lanes:
                    subs.append(real / e.name)
                else:
                    args.extend(["--ro-bind", path, path])
            except OSError:
                continue
        return subs

    for sub in layer(agent, lanes=True):
        layer(sub, lanes=False)
    # after every regular layer, so a bind from one of them cannot cover it
    for real in linked.values():
        hide(real)
        layer(real, lanes=False)
    # last: a record that is itself a symlink is hidden at its real path too
    rec_files, rec_dirs = _record_link_targets(agent, linked)
    missing = [str(x) for x in rec_dirs + rec_files if not os.path.lexists(x)]
    if missing:
        # bwrap cannot mount over a path absent from the read-only root, and left
        # unmasked the target would be readable once written: refuse (fail closed)
        raise RuntimeError(
            "a conversation record in .agent is a symlink to a path that does not exist yet ("
            + ", ".join(missing) + ") — the judge sandbox cannot hide it there; remove the link "
            "or create its target, then re-run")
    for d in rec_dirs:
        args.extend(["--tmpfs", str(d)])
        hide(d)
    for f in rec_files:
        args.extend(["--ro-bind", "/dev/null", str(f)])
        hide(f)
    return args


def build_bwrap_argv(
    project_dir: Path | str,
    git_dir: Path | str | None,
    target_argv: list[str],
    extra_rw: Iterable[str] | None = None,
    *,
    project_writable: bool = True,
    no_network: bool = False,
    mask_records: bool | None = None,
    git_readonly: Iterable[str] | None = None,
    git_sources: Iterable[str] | None = None,
    git_sources_missing: Iterable[str] | None = None,
    git_sources_unanswered: Iterable[str] | None = None,
    git_source_links: Iterable[str] | None = None,
    git_sources_writable: bool = False,
    bwrap_exe: str | None = None,
    project_view: "ProjectView | None" = None,
    agent: "str | None" = None,
) -> list[str]:
    """Generate the bwrap argv: read-only root, bind project + tmp + the running
    `agent`'s own state folders under home read-write (`_AGENT_HOME`; none for no
    agent — what the host runs or obeys inside them read-only again, `_home_layer`,
    task 308), bind git_dir — and every other path of git metadata the caller names
    in `git_readonly` (`_git_paths_to_protect`) — read-only.

    `git_sources_unanswered` (roots where git could not be asked, or sources that
    could not be resolved: the launch is
    refused) and
    `git_sources` / `git_sources_missing` / `git_source_links` (`_git_sources`, task
    181): the places git takes its hooks and its settings from. One the run could
    write is bound read-only, last. Four cannot be kept: one that does not exist
    where the run could CREATE it; one that is, or contains, a path the run must
    write; one that is in or over a place where the run hides the conversation
    records (its bind would be laid over the masks); and one reached through a link
    that sits in a directory the run may write (what the link names is bound — the
    link itself could be replaced) — the launch is refused. A source file with multiple hard links also refuses the
    launch, including a file inside an already read-only hooks directory: another
    name could still be writable — where this user could write the file or make it
    writable (its owner); a file of another owner that this user cannot write is
    not changed through any name. `git_sources_writable=True` is the owner's explicit
    permission to let the agent edit them: nothing is added and nothing refused. A
    read-only run's refusals do not name that option: every review is such a run,
    and the path the judges share cannot pass it.

    This is WRITE containment: `--ro-bind / /` leaves the whole filesystem
    READABLE (nothing outside the project is hidden), and by default there is NO
    network isolation — the judge path relies on the network because judges
    invoke provider CLIs that call model APIs.

    project_writable=False is the contained "outdir" mode: the project/corpus is
    bound read-only; the only writable project-side location is extra_rw (the
    workspace). The agents' state folders under home stay writable, but not their
    code and config (task 308).

    no_network=True is the OPT-IN network jail: it appends `--unshare-net`, which
    drops the sandbox into a fresh network namespace (loopback only). It is never
    a default and is never wired into the judge path — the judge needs the
    network. Only bwrap supports it; a nested sandbox has no
    equivalent, and the launcher fails loudly there rather than pretend (see
    `network_isolation_available` / `_wrapped_argv`).
    """
    project = str(Path(project_dir).resolve())
    home = Path.home()
    rw_paths = _normalize_rw(extra_rw)

    # Task 231 (PHASE-B item 7): a review's fixed copy, mounted read-only AT the project path. Everything git keeps
    # under the project or the copy is then read-only by that mount (the copy's own git directory is inside it), so
    # only the git places outside both are kept below; a writable path inside the project would lay the live tree
    # over the copy.
    view_root = None
    if project_view is not None:
        if project_writable:
            raise ValueError("a review's copy is mounted read-only — project_writable must be False")
        view_root = str(Path(project_view.root).resolve())

        def _under(p) -> bool:
            try:
                rp = Path(p).resolve()
            except (OSError, RuntimeError):
                return False
            return _covers(project, rp) or _covers(view_root, rp)

        for rw in rw_paths:
            if _covers(project, Path(rw)) or _covers(view_root, Path(rw)):
                raise ValueError(f"`--rw {rw}` lies in the project, which a review's copy covers read-only")
        if git_dir is not None and _under(git_dir):
            git_dir = None
        git_readonly = [g for g in (git_readonly or []) if not _under(g)]
        git_sources = [g for g in (git_sources or []) if not _under(g)]
        git_sources_missing = [g for g in (git_sources_missing or []) if not _under(g)]
        git_source_links = [g for g in (git_source_links or []) if not _under(g)]

    argv = ["bwrap", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev"]
    if project_view is not None:
        # task 231 impl round 1 (G3, opus): the guard watches the copy, so the judge must not reach the live tree
        # through a host process (`/proc/<pid>/root`) — its own PID namespace shows it none
        argv.append("--unshare-pid")
    if no_network:
        argv.append("--unshare-net")
    # Every bind below, in order, with whether it leaves its path writable: what a
    # path IS in the sandbox is what the LAST bind over it says (task 181).
    layers: "list[tuple[str, bool]]" = []

    def writable_at(path: str) -> "str | None":
        """The writable bind that governs `path` in the sandbox, or None."""
        last = None
        for where, writable in layers:
            if _covers(where, Path(path)):
                last = where if writable else None
        return last

    # ORDER IS LOAD-BEARING: in bwrap, later binds stack over earlier ones.
    # The broad read-write mounts (/tmp, the write log, per-agent home subpaths)
    # go FIRST, and the project bind goes AFTER them — so a project that happens
    # to live UNDER one of those paths (a /tmp checkout, a scratch clone) is
    # still governed by its own bind. The original order bound the project
    # first and /tmp second, which silently re-exposed a /tmp-resident project
    # read-write in judge mode — found by an empirical spike, 2026-08-13.
    argv += ["--bind", "/tmp", "/tmp"]
    layers.append(("/tmp", True))

    write_log_dir = home / ".local" / "share" / "playbook"
    write_log_dir.mkdir(parents=True, exist_ok=True)
    argv += ["--bind", str(write_log_dir), str(write_log_dir)]
    layers.append((str(write_log_dir), True))

    # Pre-create + bind the running agent's home state folders (task 308: its own only).
    folders = _AGENT_HOME.get(agent or "", ())
    for sub in folders:
        target = home / sub
        if target.is_symlink():
            raise _home_refusal(target, _LINK_WHY)
        if os.path.lexists(target) and not target.is_dir():
            raise _home_refusal(target, "is not a folder, where the agent keeps its state")
        target.mkdir(parents=True, exist_ok=True)
        argv += ["--bind", str(target), str(target)]
        layers.append((str(target), True))
    # ...and, laid over them, what the host runs or obeys later (task 308) — before the project bind, so a project
    # inside one of them still has its own: the folders above each kept path pinned onto themselves, the kept paths
    # read-only, each snapshot folder a private tmpfs. A listed folder that is missing is made first, empty; a missing
    # FILE is not (an empty settings file is not valid; a missing instructions file is a stated bound, as memory is).
    for sub in _HOME_RO_DIRS_MADE:
        if sub.split("/")[0] in folders and not os.path.lexists(home / sub):
            (home / sub).mkdir(parents=True, exist_ok=True)       # a link or a file there: `_home_layer` judges it
    for sub, empty in _HOME_FILES_MADE.items():
        if sub.split("/")[0] in folders and not os.path.lexists(home / sub):
            _publish_new(home / sub, empty)
    pins, kept, snaps = _home_layer(home, folders, [str(write_log_dir), *rw_paths,
                                                     *([project] if project_writable else [])])
    for path in pins:
        argv += ["--bind", path, path]
        layers.append((path, True))
    for path in kept:
        # `-try`: a file the host rewrites by renaming a new one over it (Claude Code's daemon.status.json, many
        # times a minute) can be gone at the instant bubblewrap mounts it, and a plain bind then failed the whole
        # launch (verify, 2026-10-25 06:30). The path then holds what replaced it, read-only too
        argv += ["--ro-bind-try", path, path]
        layers.append((path, False))
    for path in snaps:
        argv += ["--tmpfs", path]
        layers.append((path, True))

    # Ancestor pins go here: later project binds and record masks must win over
    # them. Pinning a /tmp ancestor after a judge's project would expose it again.
    pin_index = len(argv)

    # Git metadata, part one. Every path that holds it, resolved and in order.
    git_all: list[str] = []
    for p in ([git_dir] if git_dir else []) + list(git_readonly or []):
        resolved = str(Path(p).resolve())
        if resolved not in git_all:
            git_all.append(resolved)
    # A git directory that CONTAINS the project (worktrees inside a bare repository;
    # a bare repository used as the project) cannot be bound read-only after the
    # project: that bind would take the project's own writes with it (task 169, impl
    # panel r2 — round 1 did exactly that). It is bound read-only BEFORE the project
    # bind, which then wins for the project's subtree; its metadata entries are bound
    # again, last, below.
    containing = [g for g in git_all if _covers(g, Path(project))]
    for g in containing:
        argv += ["--ro-bind", g, g]
        layers.append((g, False))

    project_bind = "--bind" if project_writable else "--ro-bind"
    if view_root is not None:
        argv += ["--ro-bind", view_root, project]
        layers.append((project, False))
        # what git ignores is shown live, as the judges always saw it — bound from the host path after the copy
        for live in project_view.live:
            live_p = str(Path(live))
            if not _covers(project, Path(live_p)) or not os.path.lexists(live_p) or os.path.islink(live_p):
                continue
            argv += ["--ro-bind", live_p, live_p]
            layers.append((live_p, False))
    else:
        argv += [project_bind, project, project]
        layers.append((project, project_writable))

    # Task 125: a read-only judge cannot read the conversation records — layers
    # after the project bind so they win the overlap (`_bwrap_record_masks`). A
    # worker keeps them, and so does a read-only observer that passes
    # mask_records=False (the monitor, task 138 G3-1).
    hidden: list[str] = []
    if (not project_writable) if mask_records is None else mask_records:
        argv += _bwrap_record_masks(project, hidden, source_root=view_root,
                                    live_allowed=(project_view.agent_live if project_view is not None else None))
        layers += [(h, False) for h in hidden]
    if project_view is not None:
        # the copies folder is reachable through the live journal: hidden (G2 — every copy, this one and others')
        for place in project_view.hide:
            if os.path.isdir(place) and not os.path.islink(place):
                argv += ["--tmpfs", place]
                hidden.append(place)
                layers.append((place, False))

    # What the masks hide must stay hidden, and a bind laid after them replaces
    # them wherever it lands. One that COVERS a hidden place binds the real records
    # back (task 169, impl round 2). One INSIDE such a place does the same from the
    # other side: `--rw .agent/sessions/<id>` shows that session's records over the
    # emptied `sessions`, and `--rw .agent/<a lane>` replaces the lane's layer, so a
    # record written there during the run is readable (the single judge after round
    # 2). So a run that hides the records takes no writable path that overlaps a
    # hidden place at all — and is refused before that path is created on the host.
    # `--keep-records` is the way to have one (the monitor's `.agent/monitor`).
    for rw in rw_paths:
        touched = next((t for t in hidden if _overlaps(rw, t)), None)
        if touched is not None:
            raise RuntimeError(
                f"`--rw {rw}` is in or over a place where a read-only run hides the conversation "
                f"records ({touched}) — a writable bind there would show them, or the ones written "
                "during the run. Pass --keep-records if this run may read the records, or give an "
                "--rw path outside `.agent`. Nothing was launched.")

    # extra_rw (the judge workspace / outdir) after the project bind: it must stay
    # writable even when it lives inside a read-only project.
    for rw in rw_paths:
        Path(rw).mkdir(parents=True, exist_ok=True)
        argv += ["--bind", rw, rw]
        layers.append((rw, True))

    # Git metadata, part two: read-only even when the project is writable, and
    # laid after the project bind AND after every extra writable bind, so it wins
    # whatever those cover. The one bind there was used to come before them, and a
    # writable path that covered `.git` — `--rw <project>`, a parent of it, `.git`
    # itself — made it writable again (task 169; measured with real bubblewrap).
    # `git_readonly` adds what `git_dir` alone leaves out: a gitfile, a shared
    # directory, a nested repository (`_git_paths_to_protect`). Of a git directory
    # that contains the project, the entries git keeps there are bound, never the
    # directory itself.
    git_binds: list[str] = []
    for g in git_all:
        if g in containing:
            for name in _git_dir_entries(g):
                entry = os.path.join(g, name)
                if not _covers(entry, Path(project)) and entry not in git_binds:
                    git_binds.append(entry)
        elif g not in git_binds:
            git_binds.append(g)
    for g in git_binds:
        # the same rule as for a writable path: nothing is laid over, at or inside a
        # hidden place. No layout known to do this (a repository kept inside `.agent`
        # and named in `code_roots` would): fail closed.
        touched = next((t for t in hidden if _overlaps(g, t)), None)
        if touched is not None:
            raise RuntimeError(
                f"git metadata at {g} is in or over a place where a read-only run hides the "
                f"conversation records ({touched}); they cannot be hidden in this layout. Pass "
                "--keep-records if this run may read the records. Nothing was launched.")

    # Git's hooks and settings, wherever git is told to take them from (task 181):
    # read-only too. The owner's explicit permission skips all of it. DECIDED here —
    # against what every path will be once git's own directories are laid — and
    # laid further down, with the rest of what is to be kept.
    kept: list[str] = []
    if not git_sources_writable:
        # not offered to a read-only run: every review is one, and the path the judges
        # share refuses the option (the single judge after round 2)
        lift = (", or pass --rw-git-sources to let this agent edit the hooks and settings git takes "
                "from there" if project_writable else "")
        allow = ("Point git at a place of its own for them (`core.hooksPath`, `include.path`)" + lift
                 + ". Nothing was launched.")
        unanswered = list(dict.fromkeys(str(s) for s in (git_sources_unanswered or [])))
        if unanswered:
            # git failed where there IS a repository — a settings file it cannot read,
            # as a rule. Read as "no repository here", nothing was kept, and the agent
            # could repair the file with settings of its own (impl round 1; measured).
            raise RuntimeError(
                "git could not say where it takes its hooks and its settings from in "
                f"{', '.join(unanswered)} — most likely a settings file it cannot read (`git status` there "
                "names it). Nothing git would run there can be kept read-only while it cannot be asked, "
                "and a file that cannot be read now could be repaired from inside with settings of the "
                "agent's own. Repair it" + lift + ". Nothing was launched.")
        decided = layers + [(g, False) for g in git_binds]

        def writable_then(path: str) -> "str | None":
            last = None
            for where, writable in decided:
                if _covers(where, Path(path)):
                    last = where if writable else None
            return last

        for source in dict.fromkeys(str(Path(s).resolve()) for s in (git_sources or [])):
            # Even a file already under read-only .git may have a writable name
            # for the same inode. A path bind cannot protect that other name.
            # Only a file this user could change through it, though: one writable for
            # this user, or theirs to make writable. `perl` under its two names as a
            # hook, a deduplicated /etc/gitconfig — another owner's, not writable here —
            # refused every launch, a judge's too (the single judge after round 2).
            try:
                info = os.stat(source)
                if (os.path.isfile(source) and info.st_nlink > 1
                        and (info.st_uid == os.geteuid() or os.access(source, os.W_OK))):
                    raise RuntimeError(
                        f"git takes hooks or settings from {source}, which has {info.st_nlink} hard links; "
                        "a read-only bind on one name cannot keep the other names read-only. "
                        "Remove the extra hard links or give git a separate copy. " + allow)
            except OSError as exc:
                raise RuntimeError(
                    f"Cannot check hard links for git source {source}: {exc}. Repair the file or the "
                    "project's directory permissions. " + allow) from exc
            if writable_then(source) is None:
                continue                   # read-only already: inside git metadata, or nowhere writable
            needed = next((w for w, writable in decided if writable and _covers(source, Path(w))), None)
            if needed is not None:
                raise RuntimeError(
                    f"git takes its hooks or settings from {source}, and this run must be able to write "
                    f"{needed}, which is that place or lies inside it — it cannot be kept read-only, and "
                    "left writable the agent could change what git runs on this machine. " + allow)
            # What the masks hide stays hidden. A source that COVERS a hidden place — a
            # hooks directory that is a directory above a judge's read-only project — is
            # governed by a writable bind further up and holds no path the run must
            # write, so nothing above refuses it; bound read-only last, it was laid OVER
            # the masks and the run read the records (the single judge after round 2,
            # run 3; measured — the comment that stood here said it could not happen).
            touched = next((t for t in hidden if _overlaps(source, t)), None)
            if touched is not None:
                raise RuntimeError(
                    f"git takes its hooks or settings from {source}, which is in or over a place where a "
                    f"read-only run hides the conversation records ({touched}) — kept read-only there, it "
                    "would show them. Pass --keep-records if this run may read the records. " + allow)
            kept.append(source)
            decided.append((source, False))
        creatable: list[str] = []
        for absent in dict.fromkeys(str(Path(s).resolve()) for s in (git_sources_missing or [])):
            within = writable_then(absent)
            if within is not None:
                creatable.append(f"{absent} (in {within}, which the run may write)")
        if creatable:
            # All of them in one refusal: named one at a time, a home under /tmp was
            # refused for `~/.gitconfig` and, once that was made, for `~/.config/git/config`
            # (the single judge after round 2, run 3). And "looks for", not "is told to":
            # the system's and the user's files are read without anyone naming them.
            raise RuntimeError(
                "git looks for its hooks or settings in a place that does not exist and that this run "
                f"could create — {'; '.join(creatable)}: git would then run on this machine what the "
                "agent put there. Create each one yourself (empty will do) so that it can be kept "
                "read-only. " + allow)
        for holder in dict.fromkeys(str(Path(s).resolve()) for s in (git_source_links or [])):
            within = writable_then(holder)
            if within is not None:
                raise RuntimeError(
                    f"git reaches its hooks or settings through a link in {holder}, a directory this run "
                    "may write: what the link names can be kept read-only, the link itself could be "
                    "replaced by one that names something else. Make it a real directory or file there. "
                    + allow)

    # THE DIRECTORIES ABOVE what is kept (task 181, impl round 1; task 169's nested
    # repositories had the same shape). A read-only bind makes ITS path refuse a
    # rename, and nothing above it: a plain directory between the writable bind's
    # root and the place could be moved aside from inside and a new one made under
    # its name — the host's git then read the agent's file (measured). So every
    # directory on that way is bound onto itself, which makes it a mount point: it
    # cannot be renamed, removed or replaced (measured: "Device or resource busy"),
    # and stays writable inside. Outermost first, and BEFORE every read-only bind
    # below: a bind laid over a directory hides what was laid inside it earlier.
    protected = git_binds + kept
    pins: "dict[str, str]" = {}
    # task 231 impl round 2 (codex, measured): with a copy the project's own git places are dropped, and with them the
    # pins they gave — a judge renamed the folder holding the live project under /tmp. The project pins its own way up.
    for place in protected + ([project] if view_root is not None else []):
        # The governing bind is insufficient: /tmp (or a home bind) still lets
        # an ancestor ABOVE the project be moved aside. Use the outermost layer.
        covering = [w for w, writable in layers if writable and _covers(w, Path(place))]
        if not covering:
            continue
        within = min(covering, key=lambda p: len(Path(p).parts))
        above, d = [], os.path.dirname(place)
        while d != within and d != os.path.dirname(d) and _covers(within, Path(d)):
            above.append(d)
            d = os.path.dirname(d)
        for d in above:
            # (a directory that lies inside ANOTHER kept place is fixed like any other:
            # that place's read-only bind is laid after it, further down, and covers it)
            # Only where the bind that governs it leaves it writable. Under a read-only
            # one nothing can be renamed — and a bind laid there, after that one, made
            # the directory WRITABLE: a code root inside a judge's read-only project
            # (the single judge after round 2; measured).
            if writable_at(d) is not None and not any(w == d for w, _writable in layers):
                pins.setdefault(d, place)
    early_pins: list[str] = []
    late_pins: list[str] = []
    for d in sorted(pins, key=lambda p: p.count(os.sep)):
        # One that holds another bind goes BEFORE the project's bind and all that
        # follows it: laid later it would hide those binds — hooks beside a READ-ONLY
        # project in one directory of /tmp, and that directory bound onto itself last,
        # would lay the writable /tmp over the project. A bind that a later one covers
        # still fixes its directory: the kernel refuses to rename what any mount of the
        # namespace sits on (measured: "Device or resource busy").
        early = any(w != d and _covers(d, Path(w)) for w, _writable in layers)
        (early_pins if early else late_pins).extend(["--bind", d, d])
    argv[pin_index:pin_index] = early_pins
    argv += late_pins
    for place in protected:
        argv += ["--ro-bind", place, place]
        layers.append((place, False))

    if bwrap_supports_disable_userns(bwrap_exe or _bwrap_exe() or "bwrap"):
        argv += ["--unshare-user", "--disable-userns"]
    else:
        print("Warning: bubblewrap lacks usable --disable-userns; nested user/mount namespaces can "
              "bypass read-only mounts. Upgrade to non-setuid bubblewrap >= 0.8.0.", file=sys.stderr)
    argv += list(target_argv)
    return argv


def _covers(outer: "Path | str", inner: Path) -> bool:
    """Is `inner` the path `outer`, or inside it? Both already resolved."""
    outer = Path(outer)
    return outer == inner or outer in inner.parents


def _overlaps(a: "Path | str", b: "Path | str") -> bool:
    """Is one of the two paths the other, or inside it? Both already resolved."""
    return _covers(a, Path(b)) or _covers(b, Path(a))


# What git keeps at the top of a git directory. Used only for a git directory that
# CONTAINS the project, where the directory itself cannot be bound read-only last —
# there the line between git's entries and the project's own can only be drawn by
# NAME. The names: gitrepository-layout(5) and the list of shared paths in git's own
# `path.c`, the state files git writes beside HEAD during a merge, rebase, bisect,
# cherry-pick or notes merge, and the directories of git-lfs, git-svn, git-annex and
# git-filter-repo. (The first list here held 27 names and missed `rr-cache`, the
# recorded conflict resolutions git replays — the single judge's run 2, task 169.)
_GIT_DIR_NAMES = frozenset((
    "HEAD", "config", "config.worktree", "description", "hooks", "info", "objects", "refs",
    "packed-refs", "logs", "index", "shallow", "worktrees", "modules", "commondir", "gitdir",
    "branches", "remotes", "common", "rr-cache", "lost-found", "reftable", "locked",
    "rebase-merge", "rebase-apply", "sequencer", "AUTO_MERGE", "SQUASH_MSG",
    "gc.log", "gc.pid", "git-daemon-export-ok", "lfs", "svn", "annex", "filter-repo",
))
_GIT_DIR_PREFIXES = ("fsmonitor--daemon",)
# The state files git writes beside HEAD, in the syntax git itself holds them to
# (refs.c: a pseudoref's name is capital letters, `_` and `-`): `<NAME>_HEAD` —
# ORIG_HEAD, FETCH_HEAD, MERGE_HEAD, CHERRY_PICK_HEAD, REVERT_HEAD, REBASE_HEAD,
# BISECT_HEAD, and any pseudoref a tool makes — `<NAME>_EDITMSG`, the `BISECT_…`,
# `MERGE_…` and `NOTES_MERGE_…` families, and `sharedindex.<hex>`. The first form of
# this rule was "ends in `_HEAD`" and "starts with `MERGE_`": a project file named
# `draft_HEAD` or `MERGE_notes.txt`, in a bare repository used as the project, was
# made read-only with git's own (the single judge's run 3, task 169).
_GIT_STATE_NAME = re.compile(
    r"(?:[A-Z_-]+_HEAD|[A-Z_-]+_EDITMSG|(?:BISECT|MERGE|NOTES_MERGE)_[A-Z_-]+|sharedindex\.[0-9a-fA-F]+)\Z")


def _is_git_dir_entry(name: str) -> bool:
    return (name in _GIT_DIR_NAMES or name.startswith(_GIT_DIR_PREFIXES)
            or _GIT_STATE_NAME.match(name) is not None)


def _git_dir_entries(git_dir: "Path | str") -> "list[str]":
    """The entries present at the top of `git_dir` that are git's, by name, sorted.

    A directory that cannot be LISTED is refused, not guessed at (the single judge's
    run 3, task 169 — Critical): the first form fell back to the fixed names it knew,
    and in a bare repository whose directory could be traversed and written but not
    listed, a state file that exists only by pattern — ORIG_HEAD — was left writable
    (measured with real bubblewrap). What cannot be listed cannot be told apart."""
    try:
        names = os.listdir(git_dir)
    except OSError as exc:
        raise RuntimeError(
            f"the git directory {git_dir} contains the project and cannot be listed "
            f"({exc.strerror or exc}) — what git keeps in it cannot be told from the project's own "
            "files, so it could not be kept read-only. Make that directory readable for this user, "
            "or launch from a project that is not inside a git directory. Nothing was launched.")
    return sorted(n for n in names if _is_git_dir_entry(n))


# git reads these from the environment and would then answer for ANOTHER repository
# (task 169, impl panel r2: with GIT_DIR set, a worktree's shared directory was left
# out of the read-only binds). Discovery asks about what is on disk.
_GIT_ENV_OVERRIDES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                      "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
                      "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM")


def _git_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_OVERRIDES}


def _code_root_dirs(project_dir: "Path | str") -> "list[Path]":
    """The nested repositories `.agent/config.json` names under `code_roots`, as
    existing directories inside the project (the same shape `tasks/core.py` accepts:
    a list of project-relative paths, no absolute path, no `..`). The provider
    package cannot import `tasks`, so it reads the key itself — quietly: whatever
    cannot be read, or points outside, is left out. Never raises."""
    try:
        project = Path(project_dir).resolve()
        cfg = json.loads((project / ".agent" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = cfg.get("code_roots") if isinstance(cfg, dict) else None
    out: list[Path] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, str) or not entry.strip() or "\x00" in entry:
            continue
        rel = entry.strip()
        if os.path.isabs(rel) or ".." in Path(rel).parts:
            continue
        try:
            root = (project / rel).resolve()
            if root.is_dir() and root != project and _covers(project, root) and root not in out:
                out.append(root)
        except OSError:
            continue
    return out


def _git_paths_to_protect(project_dir: "Path | str") -> "list[str]":
    """Every existing path that holds git metadata of the project, or of a
    repository `code_roots` names: the `.git` entry itself — a directory, or the
    FILE a linked worktree or a submodule has in its place — and the git directory
    and common directory `git rev-parse` names from there.

    `git rev-parse --git-dir` alone (`_git_dir_of`) left three layouts open, each
    measured with real bubblewrap (task 169, impl panel r1): in a linked worktree
    the `.git` file sits in the writable project and could be rewritten to point
    anywhere, and the shared repository's config and hooks were writable wherever a
    writable bind covered them (`/tmp`, an `--rw` parent); a nested repository's
    `.git` is plain project content.

    Not covered, and said in the ledger row: a `.git` that is a SYMLINK (what it
    points at is bound, the link itself can be replaced — it is an entry of a
    writable directory); a nested repository `code_roots` does not name; what a
    link INSIDE a git directory points to when that is project content. git is
    asked with its environment overrides removed (`_git_env`). Never raises; a root
    where git cannot answer contributes what exists on disk."""
    out: list[str] = []

    def add(path: Path) -> None:
        try:
            resolved = str(path.resolve())
        except OSError:
            return
        if os.path.exists(resolved) and resolved not in out:
            out.append(resolved)

    try:
        roots = [Path(project_dir).resolve(), *_code_root_dirs(project_dir)]
    except OSError:
        return out
    for root in roots:
        entry = root / ".git"
        if os.path.lexists(entry):
            add(entry)
        for flag in ("--git-dir", "--git-common-dir"):
            try:
                answer = subprocess.run(["git", "rev-parse", flag], cwd=str(root),
                                        capture_output=True, text=True, check=False,
                                        env=_git_env())
            except OSError:               # no git binary: what is on disk was added above
                break
            if answer.returncode == 0 and answer.stdout.strip():
                add(root / answer.stdout.strip())
    return out


_INCLUDE_PATH_KEY = re.compile(r"include(?:if\..*)?\.path\Z", re.IGNORECASE | re.DOTALL)


def _link_dirs_on(path: str) -> "list[str]":
    """The real directories that hold a symbolic link followed on the way to the
    absolute `path` (a link's own target included, so a chain gives every one).
    The link is an ENTRY of that directory: where the directory can be written,
    the link can be replaced by one that names something else."""
    out: list[str] = []
    todo = [part for part in path.split("/") if part]
    here, followed = "/", 0
    while todo:
        part = todo.pop(0)
        if part == ".":
            continue
        if part == "..":
            here = os.path.dirname(here)
            continue
        entry = os.path.join(here, part)
        if os.path.islink(entry):
            followed += 1
            if followed > 40:              # a loop: git could not follow it either
                break
            if here not in out:
                out.append(here)
            try:
                target = os.readlink(entry)
            except OSError:
                break
            if target.startswith("/"):
                here = "/"
            todo = [p for p in target.split("/") if p] + todo
            continue
        here = entry
    return out


def _repository_on_disk(root: Path) -> bool:
    """Whether `root` lies in a git repository git could USE, by what is on disk — asked
    only where git itself failed. The first `.git` entry on the way up decides, as it
    does for git: a directory with a `HEAD`, or a file whose `gitdir:` names one; a
    stray `.git` that leads nowhere is no repository (git runs nothing there). A bare
    repository is its own directory."""
    try:
        here = root.resolve()
    except (OSError, RuntimeError):
        return False
    if (here / "HEAD").is_file() and (here / "objects").is_dir():
        return True
    while True:
        entry = here / ".git"
        if os.path.lexists(entry):
            try:
                if entry.is_dir():
                    return (entry / "HEAD").exists()
                first = entry.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
            except OSError:
                return False
            if first and first[0].startswith("gitdir:"):
                target = Path(first[0][len("gitdir:"):].strip())
                return ((here / target) / "HEAD").exists()
            return False
        if here.parent == here:
            return False
        here = here.parent


def _default_settings_files(root: Path) -> "list[Path]":
    """The system's and the user's settings files, where git looks for them in THIS
    environment (git-config(1), FILES and ENVIRONMENT) — whether or not they exist:
    `GIT_CONFIG_SYSTEM` in place of `/etc/gitconfig`, `GIT_CONFIG_GLOBAL` in place of
    both of the user's files (set and empty: no file of that level), else
    `~/.gitconfig` and `$XDG_CONFIG_HOME/git/config` (`~/.config` where that is unset
    or empty). A relative name is read against `root`, the directory git is asked in.

    Round two named the three default files only, and only where they existed: a
    file the environment names, or one that is not there yet, in a place the run may
    write was written or created from inside and read by the host's next git command
    (the single judge after the panel, run 2; measured). Never raises."""
    env = os.environ
    files: "list[Path]" = []
    system = env.get("GIT_CONFIG_SYSTEM")
    if system is None:
        files.append(Path("/etc/gitconfig"))
    elif system:
        files.append(root / system)
    user = env.get("GIT_CONFIG_GLOBAL")
    if user is None:
        try:
            home = Path.home()
        except (RuntimeError, KeyError):
            home = None                    # no home to be found: git has none to read from either
        if home is not None:
            files.append(home / ".gitconfig")
            files.append(root / (env.get("XDG_CONFIG_HOME") or str(home / ".config")) / "git" / "config")
    elif user:
        files.append(root / user)
    return files


def _git_sources(project_dir: "Path | str") -> "tuple[list[str], list[str], list[str], list[str]]":
    """Where git takes its HOOKS and its SETTINGS from, for the project and for each
    repository `code_roots` names: (the places that exist, the places that do not,
    the directories that hold a link on the way to one of them, the roots where git
    could not be asked), each resolved.

    The git directory is bound read-only, but git can be told to use something
    else, and where that is project content a worker could change what git runs on
    the HOST at its next command (task 181, PLAN S11 item 31 — measured with real
    bubblewrap, five ways). Git itself is asked, with its environment overrides
    removed (`_git_env`):
      * `git rev-parse --git-path hooks` — the hooks directory in effect
        (`core.hooksPath`, or `hooks` in the git directory, which may be a link),
        plus its direct files and the targets of links to them;
      * the repository's config and config.worktree, and the system's and the
        user's files where the environment puts them (`_default_settings_files`),
        even when they are empty and emit no values — or do not exist yet;
      * `git config --list --show-origin` — every settings FILE that gives a value
        (the repository's own, which may be a link; the user's; the system's; every
        include whose condition holds), plus ALL core.hooksPath values, including
        ones shadowed by a later value. Git names the repository's own file from the
        TOP of the work tree, wherever it is asked: read against a project that is a
        directory below the top it named a file that does not exist, and every launch
        there was refused (impl round 1);
      * every `include.path` and `includeIf.<condition>.path` — read against the file
        that names it, `~` expanded; one the environment names (`GIT_CONFIG_COUNT`)
        has no file beside it — whether or not the file gives a value, exists,
        or its condition holds now: the condition is evaluated again on the host;
      * and, for each such file that is NOT in effect now, what the file itself names
        (`git config -f <file> --no-includes`): its own includes, in turn, and its
        `core.hooksPath` — git lists nothing of a file whose condition does not hold,
        so a hooks directory named there stayed writable (impl round 1; measured).
    A source that cannot be resolved is returned as unanswered, never dropped.
    A directory that is no repository names nothing. A root where there IS a repository
    on disk and git fails — a settings file it cannot read — is returned as unanswered:
    it used to read as "no repository", and nothing was kept (impl round 1; measured).
    Never raises."""
    existing: list[str] = []
    missing: list[str] = []
    link_dirs: list[str] = []
    unanswered: list[str] = []

    def add(path: Path) -> "str | None":
        try:
            resolved = str(path.resolve())
        except (OSError, RuntimeError):
            if str(path) not in unanswered:
                unanswered.append(str(path))
            return None
        bucket = existing if os.path.lexists(resolved) else missing
        if resolved not in bucket:
            bucket.append(resolved)
        for holder in _link_dirs_on(str(path)):
            if holder not in link_dirs:
                link_dirs.append(holder)
        return resolved

    def ask(root: Path, *args: str) -> "tuple[int | None, str]":
        """(git's exit status, what it printed); (None, "") where there is no git."""
        try:
            answer = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                                    check=False, env=_git_env())
        except OSError:
            return None, ""
        out = answer.stdout              # bytes from git; whatever else is read as nothing said
        if isinstance(out, bytes):
            out = out.decode("utf-8", "surrogateescape")
        return answer.returncode, out if isinstance(out, str) else ""

    def add_hooks(path: Path) -> None:
        """Keep hook files too: a directory bind does not follow symlink targets
        or protect another hard-link name for a file inside it."""
        resolved = add(path)
        if resolved is None or not os.path.isdir(resolved):
            return
        try:
            with os.scandir(resolved) as entries:
                for entry in entries:
                    if entry.is_symlink() or entry.is_file(follow_symlinks=False):
                        add(Path(entry.path))
        except OSError:
            if str(path) not in unanswered:
                unanswered.append(str(path))

    def records(out: str):
        """`--show-origin -z`: origin NUL key LF value NUL, again and again."""
        toks = out.split("\0")
        for i in range(0, len(toks) - 1, 2):
            origin, rest = toks[i], toks[i + 1]
            key, _sep, value = rest.partition("\n")
            yield origin, key, value

    def named(value: str, beside: Path) -> Path:
        """A path a setting names: `~` expanded, a relative one read against `beside`."""
        target = Path(os.path.expanduser(value)) if value.startswith("~") else Path(value)
        return target if target.is_absolute() else beside / target

    try:
        roots = [Path(project_dir).resolve(), *_code_root_dirs(project_dir)]
    except OSError:
        return existing, missing, link_dirs, unanswered
    for root in roots:
        rc, hooks = ask(root, "rev-parse", "--git-path", "hooks")
        if rc is None:
            break                          # no git on this machine: nothing would run there
        if rc != 0 or not hooks.strip("\n"):
            if _repository_on_disk(root) and str(root) not in unanswered:
                unanswered.append(str(root))
            continue                       # else: no repository here, nothing git would run
        rc_top, top = ask(root, "rev-parse", "--show-toplevel")
        base = Path(top.rstrip("\n")) if rc_top == 0 and top.strip("\n") else root   # a bare repository: itself
        add_hooks(root / hooks.rstrip("\n"))
        # Origins name only files that EMIT values. Git also reads empty default
        # files, including .git/config linked into writable project content.
        for name in ("config", "config.worktree"):
            rc_config, config = ask(root, "rev-parse", "--git-path", name)
            if rc_config != 0 or not config.strip("\n"):
                if str(root) not in unanswered:
                    unanswered.append(str(root))
            else:
                path = root / config.rstrip("\n")
                if name == "config" or os.path.lexists(path):
                    add(path)
        # … and the system's and the user's, wherever this environment puts them and
        # whether or not they are there: one that is missing where the run could
        # create it refuses the launch, like any other place git is told to read
        for path in _default_settings_files(root):
            add(path)
        rc_list, listed = ask(root, "config", "--list", "--show-origin", "-z")
        # (`--get-regexp` answers 1 when nothing matches: that IS an answer)
        rc_inc, includes = ask(root, "config", "--show-origin", "-z", "--get-regexp",
                               r"^include(if\..*)?\.path$")
        if rc_list != 0 or rc_inc not in (0, 1):
            if str(root) not in unanswered:
                unanswered.append(str(root))
            continue
        in_effect: "set[str]" = set()
        for origin, key, value in records(listed):
            if key.lower() == "core.hookspath":
                add_hooks(named(value, base))     # ALL values, even ones shadowed now
            if origin.startswith("file:"):
                resolved = add(base / origin[5:])
                if resolved:
                    in_effect.add(resolved)
        todo: "list[str]" = []
        for origin, key, value in records(includes):
            if not _INCLUDE_PATH_KEY.match(key) or not value:
                continue
            # (one the environment names — origin "command line:" — has no file beside
            # it; git takes only an absolute path there, and fails on any other)
            beside = (base / origin[5:]).parent if origin.startswith("file:") else base
            resolved = add(named(value, beside))
            if resolved:
                todo.append(resolved)
        # what a file that is not in effect now names, in the file's own words
        seen: "set[str]" = set()
        while todo:
            path = todo.pop()
            if path in seen or path in in_effect or not os.path.isfile(path):
                continue
            seen.add(path)
            rc_f, out = ask(root, "config", "-f", path, "--no-includes", "-z", "--get-regexp",
                            r"^(include(if\..*)?\.path|core\.hookspath)$")
            if rc_f not in (0, 1):
                if str(root) not in unanswered:
                    unanswered.append(str(root))       # it cannot be read: nor can what it names be kept
                continue
            for rest in out.split("\0"):
                key, sep, value = rest.partition("\n")
                if not sep or not value:
                    continue
                if key.lower() == "core.hookspath":
                    add_hooks(named(value, base))      # hooks run from the top of the work tree
                elif _INCLUDE_PATH_KEY.match(key):
                    resolved = add(named(value, Path(path).parent))
                    if resolved:
                        todo.append(resolved)
    return existing, missing, link_dirs, unanswered


def _compose_agent_argv(agent: str, agent_args: list[str]) -> list[str]:
    """Build the final binary argv with per-agent bypass-flag injection at the
    correct position. Codex needs its bypass AFTER the `exec` subcommand.
    """
    bypass = bypass_args(agent)
    if not bypass:
        return [agent, *agent_args]

    if agent == "codex":
        # Find `exec` token; insert bypass after it. If no `exec`, prepend.
        if "exec" in agent_args:
            idx = agent_args.index("exec") + 1
            return ["codex", *agent_args[:idx], *bypass, *agent_args[idx:]]
        return ["codex", *bypass, *agent_args]
    # claude / agy: top-level flag, prepend before user args.
    return [agent, *bypass, *agent_args]


def _git_dir_of(project_dir: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
        )
        if result.returncode == 0 and result.stdout.strip():
            return (project_dir / result.stdout.strip()).resolve()
    except FileNotFoundError:
        pass
    return None


def _bwrap_exe() -> "str | None":
    """The bubblewrap this process uses, as an absolute path; None when there is
    none it can use. Only a `bwrap` that `which` finds through an ABSOLUTE PATH
    entry counts. A relative entry (or an empty one: the working directory) names
    a different directory in the launcher, in the monitor's launcher after its
    `cd`, and in the launch itself, which runs in the PROJECT — so what it finds
    can be a project-controlled file, and a different one each time (task 164,
    panel rounds 2 and 3). `which` stops at the first match, so when that match
    is behind a relative entry the absolute entries are searched for the
    bubblewrap behind it: a stray relative entry must not leave a host that has
    a real bubblewrap without one (post-D6 run 3)."""
    exe = shutil.which("bwrap")
    if not exe or os.path.isabs(exe):
        return exe
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(entry, "bwrap")
        if os.path.isabs(entry) and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _wrapped_argv(
    agent: str,
    agent_args: list[str],
    project: Path,
    extra_rw: Iterable[str] | None,
    project_writable: bool,
    no_network: bool = False,
    mask_records: bool | None = None,
    require_containment: bool = False,
    git_sources_writable: bool = False,
) -> list[str]:
    """The argv of `_launch_plan` (which see) — what a launch would exec."""
    return _launch_plan(agent, agent_args, project, extra_rw, project_writable,
                        no_network, mask_records, require_containment, git_sources_writable)[0]


def _launch_plan(
    agent: str,
    agent_args: list[str],
    project: Path,
    extra_rw: Iterable[str] | None,
    project_writable: bool,
    no_network: bool = False,
    mask_records: bool | None = None,
    require_containment: bool = False,
    git_sources_writable: bool = False,
    project_view: "ProjectView | None" = None,
) -> "tuple[list[str], bool]":
    """Compose bypass-flag injection + bwrap wrapping into the final argv, and say
    whether the child will be in a cage (wrapped here, or we are nested). Shared
    by run() (blocking) and popen() (streaming) so containment is generated in
    exactly one place. If already inside a sandbox, returns the inner argv with
    bypass flags only. A wrapped argv names bubblewrap by absolute path
    (`_bwrap_exe`).

    require_containment=True is the LAUNCHER's contract (`_main`, and the
    subagent runner it alone calls): with no bwrap to wrap in, raise instead of
    returning the bare agent argv. `launch_refusal()` is asked first and explains;
    this is what holds if bwrap is gone between that check and this one. The
    judge paths do not pass it and keep the fall-through below.

    no_network=True (opt-in only, never the judge path) is honored solely on the
    bwrap backend; on any other backend it raises rather than silently emit a
    no-op — see `_NO_NETWORK_UNSUPPORTED`.
    """
    inner_argv = _compose_agent_argv(agent, agent_args)
    if no_network and not network_isolation_available():
        raise RuntimeError(_NO_NETWORK_UNSUPPORTED)
    if project_view is not None:
        # task 231: a review that asked for its copy never runs on the live tree in its place — the caller decides
        # BEFORE making a copy whether one can be mounted (`view_mount_available`) and reads the live tree openly
        if project_writable:
            raise ValueError("a review's copy is mounted read-only — project_writable must be False")
        ok, why = view_mount_available()
        if not ok:
            raise RuntimeError(f"the review's copy cannot be mounted: {why}. Nothing was launched.")
    if is_sandboxed():
        return inner_argv, True
    exe = _bwrap_exe()
    if exe:
        # the git places are asked of the tree the judge will see: the copy, when there is one
        seen = Path(project_view.root) if project_view is not None else project
        git_dir = _git_dir_of(seen)
        # what git runs by itself — its hooks, its settings — is asked of git, and kept
        # read-only too (task 181); not asked at all with the owner's permission
        sources, absent, links, unanswered = (([], [], [], []) if git_sources_writable
                                              else _git_sources(seen))
        argv = build_bwrap_argv(project, git_dir, inner_argv, extra_rw,
                                project_writable=project_writable, no_network=no_network,
                                mask_records=mask_records,
                                git_readonly=_git_paths_to_protect(seen),
                                git_sources=sources, git_sources_missing=absent,
                                git_source_links=links, git_sources_unanswered=unanswered,
                                git_sources_writable=git_sources_writable, bwrap_exe=exe,
                                project_view=project_view, agent=agent)
        return [exe, *argv[1:]], True     # by absolute path: the launch runs in the project
    if require_containment:
        raise RuntimeError(launch_refusal() or (
            "no containment — bubblewrap (`bwrap`) is not available, so the agent "
            "would run with nothing fencing its writes. Refusing to start it."))
    # No sandbox primitive available — exec directly with bypass.
    return inner_argv, False


def containment_available() -> bool:
    """True if `run(project_writable=False)` would actually deny project writes.

    Mirrors `_wrapped_argv`'s decision so callers (the judge tamper guard) can
    tell when a judge will run UNCONTAINED — already sandboxed (nested
    short-circuit) or no bwrap on PATH. On those paths OS write-denial is a
    no-op and the before/after tamper snapshot is the only defense."""
    if is_sandboxed():
        return False
    return bool(_bwrap_exe())


# One text for the launcher's refusal below and for `tasks environment`'s advice.
BWRAP_INSTALL_HINT = ("install bubblewrap — e.g. `apt install bubblewrap` (Debian/Ubuntu), "
                      "`dnf install bubblewrap` (Fedora), `pacman -S bubblewrap` (Arch)")


def _probe_command() -> str:
    """Something to run inside the probe sandbox, by ABSOLUTE path: a `true` looked
    up on PATH made a working bubblewrap look broken wherever PATH had none."""
    for candidate in ("/usr/bin/true", "/bin/true"):
        if os.access(candidate, os.X_OK):
            return candidate
    return sys.executable or "/bin/sh"


def bwrap_supports_disable_userns(exe: str) -> bool:
    """Probe working namespace protection once per argv build, using the binary
    that will launch it. Older or unusable builds retain the owner's compatibility
    mode; do not infer capability from a version string or cache it across launches."""
    try:
        result = subprocess.run(
            [exe, "--unshare-user", "--disable-userns", "--ro-bind", "/", "/",
             "--proc", "/proc", "--dev", "/dev", _probe_command()],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def bwrap_start_error(exe: str) -> "str | None":
    """None when `exe` starts a trivial sandbox here; otherwise ONE line saying what
    happened — bwrap's own last stderr line where it printed one.

    The probe asks for the first mounts every launch asks for (`build_bwrap_argv`:
    read-only root, /proc, /dev), so a host that refuses them — user namespaces
    switched off, some containers — fails here the way the real launch would."""
    try:
        r = subprocess.run([exe, "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev",
                            _probe_command()],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           errors="replace", timeout=15)
    except subprocess.TimeoutExpired:
        return "it did not finish a trivial sandbox within 15 s"
    except OSError as e:
        return f"it could not be run ({e.strerror or e})"
    if r.returncode == 0:
        return None
    lines = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
    return lines[-1] if lines else f"it exited {r.returncode} and printed nothing"


def bwrap_state() -> "tuple[str, str, str]":
    """The ONE answer to "is there a bubblewrap this host can use?" — for the
    launcher's refusal below and for `tasks environment`, which must not give an
    all-clear where a launch is then refused. Returns (state, path, detail):
      "ok"            path = the bubblewrap that is used
      "missing"       no `bwrap` on PATH
      "relative"      path = what `which` found, through a relative PATH entry —
                      not used, and no absolute entry holds another (`_bwrap_exe`)
      "cannot-start"  path = the bubblewrap, detail = why it could not start a
                      sandbox (`bwrap_start_error`)"""
    exe = _bwrap_exe()
    if not exe:
        found = shutil.which("bwrap")
        return ("relative", found, "") if found else ("missing", "", "")
    why = bwrap_start_error(exe)
    return ("cannot-start", exe, why) if why else ("ok", exe, "")


def launch_refusal() -> "str | None":
    """Why the LAUNCHER (`_main`) must not start an agent here; None when it may.

    Owner ruling 2026-10-09 (task 164). `_wrapped_argv` falls through to the bare
    agent argv when there is no bwrap, and that argv carries the permission-bypass
    flag: the launcher used to exec it with nothing around it and said nothing —
    the opposite of what it is for. It refuses instead, the way `--no-network`
    already does without its backend. Inside a sandbox the outer one contains us
    (the nesting guard), so there is nothing to refuse. That signal is
    PLAYBOOK_SANDBOXED=1: `run()`/`popen()` give it only to a child they wrapped
    (or when they are nested themselves), so an unwrapped judge's own launches
    are refused here; set by hand, it is the operator's statement that an outer
    cage exists, and nothing checks it.

    NOT used by `run()`/`popen()`: the judge paths (tasks/review.py) call those
    directly and keep the uncontained path with their "UNCONTAINED" warning and the
    tamper guard — the same ruling left them as they are."""
    if is_sandboxed():
        return None
    state, path, why = bwrap_state()
    if state == "missing":
        return ("no containment — bubblewrap (`bwrap`) is not installed, so the agent would run "
                "with its permission prompts off and nothing fencing its writes. Refusing to "
                f"start it. To fix: {BWRAP_INSTALL_HINT}.")
    if state == "relative":
        return (f"no containment — the only `bwrap` on PATH is reached through a relative "
                f"PATH entry ({path}), which the sandbox does not use: a launch runs in "
                "the project directory, where that entry can name another file. Refusing "
                "to start the agent. Put bubblewrap's directory on PATH by its absolute path.")
    if state == "cannot-start":
        return (f"no containment — bubblewrap is installed ({path}) but could not start a "
                f"sandbox here: {why}. Refusing to start the agent.")
    return None


# The opt-in network jail (`--no-network` / no_network=True) exists ONLY on the
# bwrap backend. Without it — no bwrap, or nested inside a sandbox we cannot
# re-wrap — there is no `--unshare-net` equivalent, so the launcher REFUSES
# rather than emit a no-op that pretends the network is contained.
_NO_NETWORK_UNSUPPORTED = (
    "--no-network requires the Linux bubblewrap (bwrap) backend. Network "
    "isolation is not supported without bwrap or when nested inside another "
    "sandbox — refusing to run rather than pretend the network is contained."
)


def network_isolation_available() -> bool:
    """True iff a fresh (non-nested) run would go through the bwrap backend, the
    only one that can honor `--unshare-net`. Mirrors `_wrapped_argv`'s backend
    choice so callers can gate the opt-in before invoking it."""
    if is_sandboxed():
        return False
    return bool(_bwrap_exe())


# Parent-session identity that a sandboxed child must never inherit. The lead
# case is CLAUDE_ENV_FILE: the foreground writes its `export PLAYBOOK_SESSION_ID`
# there, and a child that keeps the variable has its OWN Claude-Code SessionStart
# hook APPEND to that same shared file — shadowing the foreground's active-task
# pointer so the code-edit gate resolves an empty `sessions/<wrong-id>/` and
# reports "No active task" mid-task (found live in task 036;
# PB-SESSION-POINTER-ISOLATION). The CLAUDE_CODE_* / CLAUDE_PROJECT_DIR siblings
# name the parent session/socket/entrypoint; the claude adapter already popped
# those three on its own judge path — stripping here makes it uniform across
# EVERY sandboxed subprocess (panel, single judge, sandbox CLI, stream sidebars,
# all providers), which is the single boundary they all funnel through. The
# child agent (Claude Code) mints its own fresh values at launch, and judges
# carry an explicit isolated PLAYBOOK_SESSION_ID, so nothing loses its identity.
_PARENT_SESSION_ENV = (
    "CLAUDE_ENV_FILE",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_PROJECT_DIR",
    # Parent-session IDENTITY + IPC credentials (not the pointer vector, but a
    # sandboxed read-only judge/subagent must not inherit them: a child
    # `claude -p` that keeps CLAUDE_CODE_SESSION_ID could resume/append to the
    # parent's session transcript, and the messaging socket/token attach it to
    # the parent's IPC channel). The child mints its own at launch.
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_PID",
)


def scrub_parent_session_env(env: dict[str, str]) -> dict[str, str]:
    """Remove the parent-session identity vars (`_PARENT_SESSION_ENV`) from `env`
    IN PLACE and return it. The single source of the strip: `_child_env` uses it
    for everything routed through the sandbox, and a direct-spawn site that does
    NOT go through the sandbox (e.g. `models_check.probe_claude_model` running
    `claude -p` itself) calls it too, so no spawn point re-opens the leak
    piecemeal (task 037 round-4, panel codex-terra F1)."""
    for var in _PARENT_SESSION_ENV:
        env.pop(var, None)
    return env


def _child_env(env: dict[str, str] | None, contained: bool) -> dict[str, str]:
    """The child's environment. PLAYBOOK_SANDBOXED=1 — the one signal `is_sandboxed()`
    and the launcher's refusal read — is given only to a child that IS in a cage:
    wrapped in bwrap by this launch, or started from inside a sandbox. It used to
    go to every child, so on a host without bwrap an unwrapped judge's own
    `sandbox` launches believed they were contained (task 164, panel round 1)."""
    child_env = dict(os.environ) if env is None else dict(env)
    scrub_parent_session_env(child_env)
    if contained:
        child_env["PLAYBOOK_SANDBOXED"] = "1"
    else:
        child_env.pop("PLAYBOOK_SANDBOXED", None)
    return child_env


def run(
    agent: str,
    agent_args: list[str],
    project_root: Path | str,
    extra_rw: Iterable[str] | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    check: bool = False,
    project_writable: bool = True,
    no_network: bool = False,
    mask_records: bool | None = None,
    require_containment: bool = False,
    git_sources_writable: bool = False,
    project_view: "ProjectView | None" = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run an agent under sandbox containment. Composes bypass-flag injection
    into argv, generates bwrap wrapping, exports PLAYBOOK_SANDBOXED=1 to a
    child that is in a cage (`_child_env`). If already inside a sandbox (ours OR
    a foreign one we can't nest in), skips wrapping but still injects bypass flags.

    no_network is the opt-in bwrap network jail; it defaults off and is never set
    by the judge path (judges need the network). require_containment: see
    `_wrapped_argv` — the launcher's launches raise rather than run unwrapped.
    """
    project = Path(project_root).resolve()
    wrapped, contained = _launch_plan(agent, agent_args, project, extra_rw, project_writable,
                                      no_network, mask_records, require_containment,
                                      git_sources_writable, project_view=project_view)
    child_env = _child_env(env, contained)

    if kwargs.get("text") or isinstance(kwargs.get("input"), str):
        # Robustness: text-mode pipes use the locale's codec, which need not be
        # UTF-8 (C/POSIX, Latin-1); any char it cannot encode in stdin/stdout
        # (e.g. U+2197 in MIND_MAP.md) kills the stdin writer thread. Pin UTF-8;
        # tolerate stray bytes out.
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")

    # A plain subprocess.run(timeout=) only SIGKILLs the direct child on expiry;
    # a sandboxed agent's grandchildren keep the captured pipe open, so the
    # post-kill communicate() blocks forever (observed: a 1s judge timeout hung
    # for minutes). When a timeout is requested, run under our own process group
    # and terminate the whole tree on expiry instead.
    if kwargs.get("timeout") is not None:
        return _run_with_timeout(wrapped, project, child_env, capture_output, check, kwargs)

    return subprocess.run(
        wrapped,
        cwd=str(project),
        env=child_env,
        capture_output=capture_output,
        check=check,
        **kwargs,
    )


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Best-effort terminate the agent AND its descendants. Grandchildren
    otherwise survive a lone proc.kill() and hold captured pipes open: signal
    the whole process group."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _run_with_timeout(wrapped, project, child_env, capture_output, check, kwargs):
    """run() helper: timeout-bounded execution with whole-tree termination.
    Re-raises subprocess.TimeoutExpired on expiry (after killing the tree) so
    callers can catch it exactly as they would from subprocess.run().

    The re-raised exception carries whatever the process had already written, on
    `.stdout` / `.stderr`, so callers can salvage a partial answer instead of
    reporting a timeout with nothing to show for the time spent.
    """
    timeout = kwargs.pop("timeout")
    input_data = kwargs.pop("input", None)
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    if input_data is not None:
        kwargs.setdefault("stdin", subprocess.PIPE)
    # Own process group / job so the whole tree is signalable, not just the leader.
    kwargs.setdefault("start_new_session", True)

    proc = subprocess.Popen(wrapped, cwd=str(project), env=child_env, **kwargs)
    try:
        out, err = proc.communicate(input=input_data, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        _kill_process_tree(proc)
        partial_out = partial_err = None
        try:
            # Reap; the tree is dead so this returns promptly. Keep what it hands
            # back: a judge killed at its ceiling has usually already written most
            # of its findings, and dropping them turns the entire wall-clock spend
            # into nothing at all. Popen.communicate() (unlike subprocess.run)
            # does not attach output to the exception, so attach it here.
            partial_out, partial_err = proc.communicate(timeout=5)
        except Exception:
            pass
        if partial_out:
            expired.stdout = partial_out
        if partial_err:
            expired.stderr = partial_err
        raise
    if check and proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, wrapped, out, err)
    return subprocess.CompletedProcess(wrapped, proc.returncode, out, err)


def popen(
    agent: str,
    agent_args: list[str],
    project_root: Path | str,
    extra_rw: Iterable[str] | None = None,
    env: dict[str, str] | None = None,
    project_writable: bool = True,
    no_network: bool = False,
    mask_records: bool | None = None,
    require_containment: bool = False,
    git_sources_writable: bool = False,
    project_view: "ProjectView | None" = None,
    **kwargs,
) -> subprocess.Popen:
    """Non-blocking variant of run() — returns a live Popen for streaming.

    Same containment/argv composition as run() (via _wrapped_argv), but the
    caller drives stdout incrementally (e.g. line-by-line stream-json → events
    for a chat sidebar). Defaults stdout to PIPE and text mode so callers can
    iterate `proc.stdout`; override via kwargs.

    no_network is the opt-in bwrap network jail; it defaults off and is never set
    by the judge path.
    """
    project = Path(project_root).resolve()
    wrapped, contained = _launch_plan(agent, agent_args, project, extra_rw, project_writable,
                                      no_network, mask_records, require_containment,
                                      git_sources_writable, project_view=project_view)
    child_env = _child_env(env, contained)

    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("text", True)
    # utf-8 (not the locale's codec, which need not be UTF-8) so piped stdin (e.g.
    # the claude prompt now on stdin) encodes and stream-json stdout decodes cleanly.
    # errors="replace": one stray non-utf-8 byte on agent stdout must not raise
    # UnicodeDecodeError and kill the whole stream.
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.Popen(
        wrapped,
        cwd=str(project),
        env=child_env,
        **kwargs,
    )


def _inject_model_args(
    agent: str,
    model: str | None,
    extras: tuple[str, ...],
    forwarded: list[str],
) -> list[str]:
    """Prepend `--model <id>` (per-agent shape) and extras to forwarded argv.

    Per-agent rules:
      - claude / pi: prepend `--model <id>` and extras
      - codex: insert `-m <id>` and extras AFTER the `exec` subcommand token
      - agy: drop model entirely (no -m flag); prepend extras
    """
    extras_list = list(extras)
    if model is None:
        # No model flag for this agent — extras still go in.
        if agent == "codex" and "exec" in forwarded:
            idx = forwarded.index("exec") + 1
            return forwarded[:idx] + extras_list + forwarded[idx:]
        return extras_list + forwarded

    if agent == "codex":
        model_flag = ["-m", model] + extras_list
        if "exec" in forwarded:
            idx = forwarded.index("exec") + 1
            return forwarded[:idx] + model_flag + forwarded[idx:]
        return model_flag + forwarded

    # claude / pi / agy-with-model: top-level --model
    return ["--model", model] + extras_list + forwarded


def _format_agent_matrix(agents: dict[str, AgentInfo]) -> str:
    rows = []
    for name in _AGENT_ORDER:
        info = agents[name]
        if info.binary_path:
            tag = f"✓ {info.binary_path}"
            if info.via and info.via != "direct":
                tag += f" (via {info.via})"
        else:
            tag = "—"
        rows.append(f"  {name:8s} {tag}")
    return "\n".join(rows)


def _main(argv: list[str]) -> int:
    """CLI entry: python3 -m provider.sandbox [--list-agents | --list-models |
    --print-argv | --agent X --] <agent-args>."""
    import argparse

    parser = argparse.ArgumentParser(prog="provider.sandbox", add_help=True)
    parser.add_argument("--agent", default=None,
                        help="Agent to launch (default: auto-detect or inferred from --model)")
    parser.add_argument("--model", default=None,
                        help="Model alias (opus/sonnet/haiku/gpt/gemini/qwen/deepseek) "
                             "OR canonical id (claude-*, gpt-*, gemini-*, vendor/model, qwen*). "
                             "Auto-picks the agent unless --agent is given.")
    parser.add_argument("--list-agents", action="store_true",
                        help="Print capability matrix and exit")
    parser.add_argument("--list-models", action="store_true",
                        help="Print model alias table and exit")
    parser.add_argument("--rw", action="append", default=[],
                        help="Extra read-write path (repeatable). With --ro-project and "
                             "without --keep-records, a path in or over .agent is refused: "
                             "it would show the hidden conversation records.")
    parser.add_argument("--ro-project", action="store_true",
                        help="Bind the project read-only; only --rw paths stay "
                             "writable project-side (contained-observer mode, "
                             "e.g. the conversation monitor)")
    parser.add_argument("--keep-records", action="store_true",
                        help="With --ro-project: do NOT hide the conversation records "
                             "(.agent chat log, sessions, bash history) — for a read-only "
                             "observer that needs them, e.g. the monitor. Judges never pass it.")
    parser.add_argument("--rw-git-sources", action="store_true",
                        help="Let the agent EDIT the hooks and settings git takes from outside "
                             "the git directory (a tracked hooks directory named by "
                             "core.hooksPath, an included settings file). By default those "
                             "places are read-only in the sandbox: what is written there runs "
                             "on this machine at the next git command. Not with --prompt.")
    parser.add_argument("--print-argv", action="store_true",
                        help="Print the fully wrapped argv (one arg per line) "
                             "instead of executing — inspectable containment")
    parser.add_argument("--no-network", action="store_true",
                        help="OPT-IN network jail: add bwrap --unshare-net. "
                             "Fails loudly without bwrap or inside a nested "
                             "sandbox (no --unshare-net equivalent). Never a "
                             "default; never the judge path.")
    parser.add_argument("--project-root", default=None,
                        help="Project root (default: cwd)")
    parser.add_argument("--prompt", default=None,
                        help="Run a headless prompt via the unified subagent runner "
                             "(builds the agent's native invocation for you, instead of raw -- args)")
    parser.add_argument("--bare", action="store_true",
                        help="With --prompt: no context, the prompt is the whole mission")
    parser.add_argument("--stream", action="store_true",
                        help="With --prompt: stream output events live to stdout")
    parser.add_argument("agent_args", nargs=argparse.REMAINDER,
                        help="Args passed verbatim to the agent binary")

    args = parser.parse_args(argv)

    # --no-network is opt-in on the raw/--print-argv paths only. It is NOT
    # threaded through the --prompt (subagent) runner, which is also the judge
    # path — so reject the combination loudly instead of silently ignoring it.
    if args.no_network and args.prompt is not None:
        print("Error: --no-network is not supported with --prompt (the subagent "
              "path, which the judge path shares). Use raw `-- <agent args>`.",
              file=sys.stderr)
        return 2

    # Same for --keep-records: the subagent runner (the judge path) never keeps them.
    if args.keep_records and args.prompt is not None:
        print("Error: --keep-records is not supported with --prompt (the subagent "
              "path, which the judge path shares). Use raw `-- <agent args>`.",
              file=sys.stderr)
        return 2
    if args.rw_git_sources and args.prompt is not None:
        print("Error: --rw-git-sources is not supported with --prompt (the subagent "
              "path, which the judge path shares — a judge never edits git's hooks or "
              "settings). Use raw `-- <agent args>`.", file=sys.stderr)
        return 2

    if args.list_agents:
        print("Sandbox agent capability matrix:")
        print(_format_agent_matrix(detect_agents()))
        return 0

    if args.list_models:
        print("Model aliases (use with `bin/sandbox --model X`):")
        for alias, (ag, model, extras) in MODEL_ALIASES.items():
            tail = f" + {' '.join(extras)}" if extras else ""
            print(f"  {alias:9s} -> --agent {ag}" + (f" --model {model}{tail}" if model else f"{tail} (no --model)"))
        print("Plus canonical patterns: claude-*, gpt-/o1-/o3-/o4-*, gemini-*, vendor/model, qwen*")
        return 0

    project = Path(args.project_root or Path.cwd()).resolve()

    forwarded = list(args.agent_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    # Resolve --model into (agent, canonical, extras). Validates against --agent.
    if args.model is not None:
        try:
            inferred_agent, canonical_model, extras = resolve_model(args.model)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        if args.agent and args.agent != inferred_agent:
            print(
                f"Error: --agent {args.agent!r} conflicts with --model {args.model!r} "
                f"(implies --agent {inferred_agent!r})",
                file=sys.stderr,
            )
            return 2
        agent = inferred_agent
        model_for_spec = canonical_model
        forwarded = _inject_model_args(agent, canonical_model, extras, forwarded)
    else:
        agent = args.agent or default_agent()
        model_for_spec = None

    # No containment → no launch. Everything below either starts an agent or
    # previews the argv that would, so the refusal covers a run, a --prompt run and
    # --print-argv alike: a preview must not show a launch that would be refused.
    refusal = launch_refusal()
    if refusal:
        print(f"Error: {refusal}", file=sys.stderr)
        return 2

    # --prompt: route through the unified subagent runner (builds the agent's
    # native invocation via headless_argv). Raw `--` passthrough still works
    # when --prompt is absent.
    if args.prompt is not None:
        from . import subagent as _subagent
        # Prompt execution must inherit the same containment contract as raw
        # passthrough and --print-argv. Dropping these flags here made inspection
        # show a read-only project while the live runner received writable repo
        # defaults; repeatable --rw exceptions disappeared as well.
        spec = _subagent.SubagentSpec(
            agent=agent, model=model_for_spec, prompt=args.prompt, bare=args.bare,
            contain="outdir" if args.ro_project else "repo",
            extra_rw=tuple(args.rw),
        )
        # --print-argv is a DRY RUN and must short-circuit BEFORE the run — and
        # it has to print THIS path's argv (the adapter's headless invocation),
        # not the empty raw-passthrough one, or the inspection would be a lie.
        try:
            if args.print_argv:
                _inv = _subagent.build_invocation(spec, project_root=project)
                for a in _wrapped_argv(agent, _inv.argv, project, args.rw,
                                       project_writable=not args.ro_project,
                                       require_containment=True):
                    print(a)
                return 0
            if args.stream:
                for ev in _subagent.stream_subagent(spec, project_root=project):
                    if ev.text:
                        sys.stdout.write(ev.text)
                        sys.stdout.flush()
                sys.stdout.write("\n")
                return 0
            res = _subagent.run_subagent(spec, project_root=project)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        print(res.text)
        return res.returncode

    if args.print_argv:
        try:
            wrapped = _wrapped_argv(agent, forwarded, project, args.rw,
                                    project_writable=not args.ro_project,
                                    mask_records=(False if args.keep_records else None),
                                    no_network=args.no_network, require_containment=True,
                                    git_sources_writable=args.rw_git_sources)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        for a in wrapped:
            print(a)
        return 0

    try:
        result = run(agent, forwarded, project, extra_rw=args.rw,
                     project_writable=not args.ro_project,
                     mask_records=(False if args.keep_records else None),
                     no_network=args.no_network, require_containment=True,
                     git_sources_writable=args.rw_git_sources)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return result.returncode


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
