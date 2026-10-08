"""
AntigravityAdapter — provider adapter for Google's Antigravity CLI (`agy`).

Two halves with different standing:

JUDGE PATH — rewritten for agy 1.2.17 and measured on 2026-10-05 (task 111; Linux,
a Google AI Pro sign-in; the complete outputs are in that task's record and the
captured fixtures in tests/fixtures/agy-1.2.17/). The seat is `judge: experimental`:
it works on the machine it was measured on and carries no support claim.

  * Why agy at all: the `gemini` CLI (0.62.0) refuses a personal Google login —
    the owner's attempt answered "This client is no longer supported for Gemini
    Code Assist for individuals … migrate to the Antigravity suite" — so a Gemini
    judge paid by a consumer subscription, not an API key, goes through `agy`.
  * Prompt on stdin: `agy --input-format stream-json --output-format stream-json`
    with NO `-p/--print` (a string flag — it would take the next token as the
    prompt) reads one NDJSON line per turn: `{"event":"user","message":{"role":
    "user","content":…}}`. A 150 KB prompt arrives whole. Nothing of the prompt is
    on argv, so the per-argument cap does not apply.
  * Pinned model: a seat pins a WHOLE model id (`gemini-3.8-flash-high` — the id
    carries the effort; `<base>:<effort>` is refused). agy rejects an unknown id
    before any turn, and the stream's `init` event echoes the model value agy
    accepted: a pinned seat whose stream carries another value, or none, fails.
  * Read-only is the OS sandbox's doing, NOT `--mode plan`'s. Plan mode is passed
    and agy applies it, but under `--dangerously-skip-permissions` it did not stop
    a file write (measured); and WITHOUT that flag headless agy auto-denies the
    first tool request and ends the turn with an empty response. So the seat runs
    like every other judge — `sandbox.run(..., project_writable=False)` prepends
    the bypass flag and the read-only project bind refuses the write. Where no OS
    sandbox is usable the seat is uncontained, exactly as the other judges are.
  * Credentials in place: agy keeps its sign-in in the OS keyring (Secret Service
    over the session D-Bus — an empty HOME alone stays signed in) and its state in
    ~/.gemini, which the sandbox binds read-write at its real path. Nothing is
    copied. The judge environment drops GEMINI_API_KEY, GOOGLE_API_KEY and
    GOOGLE_APPLICATION_CREDENTIALS: this seat is for a signed-in subscription, never
    per-call or per-project billing.
  * Output: NDJSON events ending in `{"event":"result","result":{status, response,
    usage,…}}`; `provider/usage.py` (the agy section) is the one parser and lists
    the ways an agy call can exit 0 and still not be a review — its own
    `--print-timeout` expiry (exit 0, `status: SUCCESS`, partial text), an
    auto-denied tool, a mid-turn error, a web or browser tool call when web search
    is off. agy has no flag that disables its web tools.
  * Live check, same day and machine: a one-seat agy panel and a single-judge
    review returned reviews and left spend records whose token counts equal agy's.
  * A real quota stop (captured in that task's exam): exit 3 mid-turn, "Individual
    quota reached. … Resets in 34m13s." — the seat fails with that sentence first.
  * Not measured: an expired sign-in and a depleted AI-credit
    balance (their handling follows agy's documented messages — the fixtures
    README says which files are constructed).

MAIN-AGENT PATH — written for agy 1.0.2, experimental, NOT touched by task 111
(the non-judge `headless_argv` shape, hooks, bootstrap, launch, the session log).
What follows describes that older CLI and may be out of date:

agy v1.0.2 (Go-based, brew cask) stores state under ~/.gemini/antigravity/ —
bootstrap file is GEMINI.md (auto-loaded by agy from project cwd, same convention
as ~/.gemini/GEMINI.md at user scope).

Hook surface: agy v1.0.2 has a Claude-compatible plugin loader that accepts
PreToolUse / PostToolUse / UserPromptSubmit / Stop hooks via project-local
plugin manifests. install_hooks writes the manifest (T134 W5a).

Session identity: no AGY_SESSION_ID env var. Resolution order:
  1. $PLAYBOOK_SESSION_ID (set by scripts/playbook-agy wrapper — preferred)
  2. PID-walk fallback: find 'agy' in parent process chain, use pid-<N>

Session transcript: JSONL at
    ~/.gemini/antigravity/brain/<uuid>/.system_generated/logs/transcript.jsonl
Records of interest: source=USER_EXPLICIT, type=USER_INPUT — content wrapped
in <USER_REQUEST>...</USER_REQUEST>, optionally followed by <ADDITIONAL_METADATA>
and <USER_SETTINGS_CHANGE> blocks.

Panel-review participation: a configured seat names its model (`agy:<id>`); the
legacy no-config fan-out contributes one unpinned seat (None) that runs whatever
model is selected in agy.
"""

from __future__ import annotations
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Mapping, Optional

from ..adapter import ProviderAdapter, Invocation
from ..capabilities import ProviderCapabilities


_USER_REQUEST_RE = re.compile(
    r"<USER_REQUEST>(.*?)</USER_REQUEST>",
    re.DOTALL,
)

# ── judge path (agy 1.2.17, task 111) ────────────────────────────────────────

# agy has no flag that switches its web and browser tools off, so with web search
# off the seat is told in one line ahead of the prompt (and the stream is checked
# afterwards — provider.usage.agy_web_tool_calls).
_NO_WEB_TOOLS = ("Web search is off for this review: do not call search_web, "
                 "read_url_content or any browser tool.")

# Never handed to a judge or probe: credentials through which agy could bill per
# call instead of using the signed-in subscription — the two API-key variables, and
# GOOGLE_APPLICATION_CREDENTIALS (a service-account file: a project-billed path; no
# measurement shows agy prefers its keyring sign-in over it — impl panel r2). The
# cost: an agy signed in through ADC cannot serve as this experimental judge seat.
_API_KEY_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")

# The killer sits this far above agy's own `--print-timeout`, so agy can return its
# partial text first (it then exits 0 — provider.usage.agy_timed_out catches that).
_KILL_GRACE_SECS = 30


def validate_model_id(model: str) -> str:
    """An agy judge pin → the model id handed to agy's model flag, or ValueError.

    A pin is a WHOLE id as `agy models` lists it: the id already carries the effort
    (`gemini-3.8-flash-high`). The `<base>:<effort>` form other providers use is
    refused: agy would accept `--model <base>` plus its effort flag, but its stream
    then names only `<base>` (measured), so the seat could not check which variant it
    was given."""
    mid = (model or "").strip()
    if not mid or ":" in mid or any(ch.isspace() for ch in mid):
        base, _, effort = mid.partition(":")
        hint = (f" — did you mean {base.strip()}-{effort.strip()}?"
                if base.strip() and effort.strip() and not any(c.isspace() for c in mid) else "")
        raise ValueError(
            f"bad agy pin {model!r}: pin a whole model id from `agy models` (the id carries "
            f"the effort, e.g. gemini-3.8-flash-high), not <base>:<effort>{hint}")
    return mid


def pinned_model_id(model: Optional[str]) -> Optional[str]:
    """The value agy's `init` event must carry for this seat: the pinned id, or None
    for an unpinned seat. `init.model` echoes the model value agy ACCEPTED (measured);
    that agy really serves it rests on agy's own behaviour — it refuses an unknown or
    contradictory selection before any turn (also measured)."""
    return validate_model_id(model) if model else None


def judge_env(env: "Mapping[str, str]", session_id: str = "judge") -> "dict[str, str]":
    """A copy of `env` for a judge/probe call: the billed-credential variables dropped, the
    Playbook session id set. HOME, DBUS_SESSION_BUS_ADDRESS and XDG_RUNTIME_DIR pass
    through untouched — they are how agy reaches its real sign-in."""
    out = dict(env)
    for var in _API_KEY_ENV:
        out.pop(var, None)
    out["PLAYBOOK_SESSION_ID"] = session_id or "judge"
    return out


class AntigravityAdapter(ProviderAdapter):
    """Provider adapter for Antigravity CLI (`agy`)."""

    _BRAIN_DIR = Path.home() / ".gemini" / "antigravity" / "brain"

    def __init__(self, session_id: str, project_root: Path) -> None:
        self._session_id = session_id
        self._project_root = project_root
        self._transcript_path: Optional[Path] = None  # cached after first lookup

    # ── CLI identity ─────────────────────────────────────────────────────────

    @classmethod
    def binary_name(cls) -> str:
        return "agy"

    @classmethod
    def context_transport(cls) -> str:
        # The judge prompt rides stdin (stream-json, task 111) — no argv size cap.
        return "stdin"

    @classmethod
    def panel_variants(cls) -> list[Optional[str]]:
        # Legacy no-config fan-out only: one unpinned seat (agy's selected model).
        # A configured panel names its model: `agy:<id>`.
        return [None]

    def headless_argv(
        self,
        prompt: str,
        model: Optional[str],
        *,
        context: str = "",
        bare: bool = False,
        stream: bool = False,
        structured: bool = False,
    ) -> Invocation:
        if structured:
            return self._judge_argv(prompt, model, context=context, bare=bare)
        # agy 1.1.x `--print`/`--prompt` is a STRING flag: the prompt is its
        # VALUE, not stdin. (Bare `agy --print` errors "flag needs an argument:
        # -print"; agy has no stdin prompt path in 1.1.1 — `--print -` just
        # treats "-" as the literal prompt.) The prompt therefore rides right
        # after `--print` on argv; keeping it adjacent is load-bearing, because
        # run_headless_judge appends `--print-timeout <secs>s` afterwards — if
        # `--print` had no value, it would swallow `--print-timeout` as the
        # prompt (the bug that made every agy judge investigate the string
        # "--print-timeout" instead of reviewing; task 013). --print ignores cwd
        # so --add-dir exposes the project tree; no model flag (rejected).
        # Bypass flag (--dangerously-skip-permissions) prepended by sandbox.
        full_prompt = prompt if (bare or not context) else f"{context}\n\n---\n\n{prompt}"
        argv = ["--add-dir", str(self._project_root), "--print", full_prompt]
        return Invocation(argv, stdin=None)

    def _judge_argv(self, prompt: str, model: Optional[str], *, context: str = "",
                    bare: bool = False) -> Invocation:
        """The JUDGE invocation (agy 1.2.17): everything the model reads is ONE NDJSON
        line on stdin; argv carries only flags. `-p/--print` is deliberately absent —
        it is a string flag, and `--input-format stream-json` already means print
        mode. No `--add-dir`: agy uses the cwd, which sandbox.run sets to the project.
        ASCII-escaped JSON (the json.dumps default) keeps the line independent of the
        pipe's encoding and survives a lone surrogate."""
        full_prompt = prompt if (bare or not context) else f"{context}\n\n---\n\n{prompt}"
        argv = ["--input-format", "stream-json", "--output-format", "stream-json"]
        if model:
            argv += ["--model", validate_model_id(model)]
        argv += ["--mode", "plan"]
        line = json.dumps({"event": "user", "message": {"role": "user", "content": full_prompt}})
        return Invocation(argv, stdin=line + "\n")

    def judge_invocation(self, prompt: str, model: Optional[str], *, context: str = "",
                         web_search: bool = False,
                         timeout_secs: "int | None" = None) -> Invocation:
        """`_judge_argv` plus the two judge-only extras, shared by the panel seat
        (`run_headless_judge`) and the single-judge arm in tasks/review.py so the two
        cannot drift: the no-web line ahead of the prompt when web search is off, and
        `--print-timeout` when the review has a finite hard timeout (omitted when
        unlimited — agy's default waits for the turn). Raises ValueError on a bad pin."""
        if not web_search:
            prompt = f"{_NO_WEB_TOOLS}\n\n{prompt}"
        inv = self._judge_argv(prompt, model, context=context)
        argv = list(inv.argv)
        if timeout_secs is not None:
            argv += ["--print-timeout", f"{timeout_secs}s"]
        return Invocation(argv, stdin=inv.stdin)

    def run_headless_judge(
        self,
        prompt: str,
        model: Optional[str],
        system_context: str,
        *,
        web_search: bool,
        timeout_secs: "int | None",
        budget_usd: str,
    ) -> str:
        import shutil
        if not shutil.which(self.binary_name()):
            return f"(error: {self.binary_name()} not found on PATH)"
        inv = self.judge_invocation(prompt, model, context=system_context,
                                    web_search=web_search, timeout_secs=timeout_secs)
        env = judge_env(os.environ, self._session_id)
        from provider import sandbox as _sandbox
        from provider import usage as _usage
        # encoding="utf-8" guards the stdout decode against a non-UTF-8 locale
        # codec. The subprocess killer sits above agy's own
        # --print-timeout; unlimited stays unlimited (no arithmetic on None), and
        # sandbox.run then skips its process-group killer.
        run_timeout = None if timeout_secs is None else timeout_secs + _KILL_GRACE_SECS
        t0 = time.monotonic()
        result = _sandbox.run(
            "agy", inv.argv,
            project_root=self._project_root,
            project_writable=False,   # judge is read-only — cannot mutate repo/task.md
            env=env,
            input=inv.stdin,
            capture_output=True, text=True, timeout=run_timeout, encoding="utf-8",
        )
        elapsed = time.monotonic() - t0
        # agy's own timeout exits 0 with `status: SUCCESS` and PARTIAL text: hand it
        # to the callers' timeout paths (marker first, partial text salvaged, journal
        # `timeout`) exactly like a judge the killer stopped.
        if _usage.agy_timed_out(result.stderr, timeout_secs, elapsed, result.stdout):
            raise subprocess.TimeoutExpired(
                ["agy", *inv.argv], timeout_secs or 0, output=result.stdout, stderr=result.stderr)
        return _usage.agy_judge_output(
            result, _sandbox.format_judge_output,
            expected_model=pinned_model_id(model), web_search=web_search)

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def project_root(self) -> Path:
        return self._project_root

    # ── Bootstrap ────────────────────────────────────────────────────────────

    def bootstrap_file_name(self) -> str:
        return "GEMINI.md"

    def install_bootstrap(self, project_root: Path) -> None:
        """Write GEMINI.md teaching agy the Playbook workflow.

        agy auto-loads ~/.gemini/GEMINI.md at user scope; project-local
        GEMINI.md is read by agy when run from project cwd. Does not
        overwrite an existing GEMINI.md.
        """
        from tasks.atomic import atomic_write
        from tasks.template import antigravity_md_template
        target = project_root / "GEMINI.md"
        if not target.exists():
            atomic_write(target, antigravity_md_template())

    # ── Hooks ─────────────────────────────────────────────────────────────────

    _PLUGIN_NAME = "claude-playbook"

    def install_hooks(self, project_root: Path) -> None:
        """Install Playbook hooks globally with agy via plugin manifest.

        agy plugins live in ~/.gemini/config/plugins/<name>/ and must be registered
        via `agy plugin install <src>` — direct file writes are not picked up.
        This method builds a cached manifest in ~/.cache/claude-playbook/agy-plugin/
        then invokes `agy plugin install` to register it globally. Idempotent —
        re-install if the plugin already exists (refreshes hook script paths).
        """
        import shutil
        agy_bin = shutil.which("agy")
        if not agy_bin:
            print("  agy plugin   skipped: 'agy' not on PATH")
            return

        scripts_dir = self._resolve_playbook_scripts_dir()
        if scripts_dir is None:
            print("  agy plugin   skipped: could not resolve Playbook scripts dir")
            return

        cache_dir = self._build_plugin_manifest(scripts_dir)
        self._register_with_agy(agy_bin, cache_dir)

    def uninstall_hooks(self, project_root: Path) -> None:
        """Remove Playbook agy plugin registration."""
        import shutil
        agy_bin = shutil.which("agy")
        if not agy_bin:
            return
        subprocess.run(
            [agy_bin, "plugin", "uninstall", self._PLUGIN_NAME],
            capture_output=True, text=True,
        )

    def _resolve_playbook_scripts_dir(self) -> Optional[Path]:
        """Locate the directory containing Playbook hook scripts.

        Resolution order:
          1. $CLAUDE_PLUGIN_ROOT/scripts (set by Claude plugin loader)
          2. Walk up from this file: src/provider/adapters/antigravity.py
             → <repo>/scripts when running from the dev checkout.
        """
        env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
        if env_root:
            candidate = Path(env_root) / "scripts"
            if candidate.exists():
                return candidate
        # Walk up from src/provider/adapters/antigravity.py
        here = Path(__file__).resolve()
        # adapters → provider → src → repo root
        for parent in here.parents:
            candidate = parent / "scripts"
            if (candidate / "task-gate-hook").exists():
                return candidate
        return None

    def _build_plugin_manifest(self, scripts_dir: Path) -> Path:
        """Write the agy plugin manifest under ~/.cache/claude-playbook/agy-plugin/.

        Returns the manifest root path suitable for `agy plugin install <path>`.
        """
        from tasks.core import VERSION
        # Deferred import — setup-path only (adapter manifest build), where the
        # tasks package is importable (see provider/paths.py; same context that
        # already imports tasks.core.VERSION just above). Atomic so a crash can't
        # leave truncated installed-metadata / hook JSON that agy would reject.
        from tasks.atomic import atomic_write
        cache_dir = Path.home() / ".cache" / "claude-playbook" / "agy-plugin" / self._PLUGIN_NAME
        cache_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            cache_dir / "plugin.json",
            json.dumps({"name": self._PLUGIN_NAME, "version": VERSION}, indent=2),
        )
        hooks_dir = cache_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)

        def _entry(script: str, matcher: Optional[str] = None) -> dict:
            entry: dict = {
                "hooks": [{
                    "type": "command",
                    "command": str(scripts_dir / script),
                    "timeout": 5000,
                }],
            }
            if matcher is not None:
                entry["matcher"] = matcher
            return entry

        hooks_doc = {
            "hooks": {
                "PreToolUse":        [_entry("task-gate-hook",  matcher=".*")],
                "PostToolUse":       [_entry("state-echo-hook", matcher=".*")],
                "UserPromptSubmit":  [_entry("chat-log-hook")],
                "Stop":              [_entry("stop-hook")],
            }
        }
        atomic_write(hooks_dir / "hooks.json", json.dumps(hooks_doc, indent=2))
        return cache_dir

    def _register_with_agy(self, agy_bin: str, cache_dir: Path) -> None:
        """Invoke `agy plugin install <cache_dir>`. Idempotent w.r.t. agy's state."""
        # Uninstall first to guarantee a refresh (script paths may have changed
        # since the previous install). Ignore errors — plugin may not exist yet.
        subprocess.run(
            [agy_bin, "plugin", "uninstall", self._PLUGIN_NAME],
            capture_output=True, text=True,
        )
        result = subprocess.run(
            [agy_bin, "plugin", "install", str(cache_dir)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  agy plugin   installed ({self._PLUGIN_NAME})")
        else:
            print(f"  agy plugin   install failed: {result.stderr.strip()}")

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def launch_interactive(self, project_root: Path, **kwargs) -> int:
        """Launch `agy` TUI with PLAYBOOK_SESSION_ID pre-set."""
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        result = subprocess.run(["agy"], cwd=project_root, env=env, **kwargs)
        return result.returncode

    def launch_headless(self, project_root: Path, prompt: str, **kwargs) -> str:
        """Run `agy --print <prompt>` for a single non-interactive prompt.

        Uses --add-dir to expose the project tree — agy --print mode runs in
        its own scratch dir and ignores cwd otherwise. agy 1.1.x `--print` is a
        string flag taking the prompt as its value (no stdin path); the prompt
        must sit immediately after `--print`, before `--print-timeout` (else
        `--print` swallows `--print-timeout` as its value — see headless_argv).
        """
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        result = subprocess.run(
            ["agy", "--add-dir", str(project_root),
             "--print", prompt, "--print-timeout", "300s"],
            cwd=project_root, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **kwargs,
        )
        return result.stdout

    # ── Capabilities ─────────────────────────────────────────────────────────

    def detect_capabilities(self) -> ProviderCapabilities:
        """agy v1.0.2: plugin-system hooks (Claude-compatible schema, probed).

        Hook surface confirmed via `agy plugin validate` accepting Claude-shape
        hooks/hooks.json + binary strings (`PreToolUse`, `PostToolUse`, `Stop`,
        `HooksPanel`). Capability flags reflect plugin-installable hooks; actual
        hook firing under agy requires install_hooks (W5a) + smoke test (W6b).
        """
        log_base = self._BRAIN_DIR
        return ProviderCapabilities(
            provider="antigravity",
            has_user_prompt_hook=True,
            has_pre_tool_hook=True,
            has_post_tool_hook=True,
            has_stop_hook=True,
            session_id_in_payload=False,
            session_log_format="jsonl",
            session_log_base=log_base if log_base.exists() else None,
        )

    # ── Chat log ─────────────────────────────────────────────────────────────

    def session_log_path(self) -> Optional[Path]:
        """Find most recent transcript JSONL referencing the project cwd.

        Walks ~/.gemini/antigravity/brain/<uuid>/.system_generated/logs/transcript.jsonl
        and returns the file with newest mtime whose content mentions project_root.
        Verification is content-based (cwd appears in early USER_INPUT or tool_calls)
        because agy doesn't tag transcripts with cwd metadata directly.
        """
        if self._transcript_path is not None:
            return self._transcript_path
        if not self._BRAIN_DIR.exists():
            return None
        cwd_str = str(self._project_root)
        candidates: list[tuple[float, Path]] = []
        for brain_dir in self._BRAIN_DIR.iterdir():
            transcript = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if transcript.exists():
                candidates.append((transcript.stat().st_mtime, transcript))
        candidates.sort(reverse=True)  # newest first
        for _, path in candidates:
            try:
                # Read first ~8KB to check for cwd reference
                with open(path, "rb") as f:
                    head = f.read(8192).decode("utf-8", errors="replace")
                if cwd_str in head:
                    self._transcript_path = path
                    return path
            except OSError:
                continue
        return None

    def read_new_messages(self, since_offset: int) -> tuple[list[str], int]:
        """Read user messages from agy transcript since byte offset.

        Filters: source=USER_EXPLICIT, type=USER_INPUT.
        Cleans: unwraps <USER_REQUEST>...</USER_REQUEST>; strips trailing
        <ADDITIONAL_METADATA> / <USER_SETTINGS_CHANGE> blocks.
        Returns ([], since_offset) if no transcript found.
        """
        log_path = self.session_log_path()
        if log_path is None:
            return [], since_offset

        messages: list[str] = []
        new_offset = since_offset

        try:
            with open(log_path, "rb") as f:
                f.seek(since_offset)
                for raw_line in f:
                    new_offset += len(raw_line)
                    try:
                        obj = json.loads(raw_line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    # I18: skip non-dict records rather than AttributeError.
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("source") != "USER_EXPLICIT":
                        continue
                    if obj.get("type") != "USER_INPUT":
                        continue
                    content = obj.get("content", "")
                    if not isinstance(content, str):
                        continue
                    # Prefer the explicit <USER_REQUEST> wrapper when present;
                    # fall back to raw content stripped of trailing metadata blocks.
                    m = _USER_REQUEST_RE.search(content)
                    if m:
                        text = m.group(1).strip()
                    else:
                        # Strip trailing <ADDITIONAL_METADATA>...</ADDITIONAL_METADATA>
                        # and <USER_SETTINGS_CHANGE>...</USER_SETTINGS_CHANGE> blocks.
                        text = re.sub(
                            r"<(ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>.*?</\1>",
                            "",
                            content,
                            flags=re.DOTALL,
                        ).strip()
                    if text:
                        messages.append(text)
        except OSError:
            pass

        return messages, new_offset

    # ── Class method ─────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, project_root: Path) -> "AntigravityAdapter":
        """Construct adapter using best available session ID source.

        Priority:
        1. PLAYBOOK_SESSION_ID (set by scripts/playbook-agy wrapper)
        2. PID-walk to find 'agy' parent process
        """
        from .codex import _pid_walk_session_id
        session_id = os.environ.get("PLAYBOOK_SESSION_ID", "")
        if not session_id:
            session_id = _pid_walk_session_id(provider_names=["agy"])
        return cls(session_id=session_id, project_root=project_root)
