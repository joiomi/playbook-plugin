"""Model-availability discovery + selection for judge pins (task 012).

Backs `tasks models check` and `tasks models select`. Model ids pinned in
`.agent/models.json` rot as providers ship/retire models; this module answers
"which judges CAN run on this machine right now" and guides the user through
refreshing the pins. `check_pins` is also reused by the panel/single-judge
hard-stop path (probe-confirming failed specs) and by doctor (probe=False).

Per-provider discovery surfaces (probed live, 2026-07-13):
- codex: `~/.codex/models_cache.json` lists slugs + per-model
  supported_reasoning_levels + the writing CLI's client_version. The cache is
  a catalog, NOT this account's entitlements — a listed model can still 400
  ("not supported when using Codex with a ChatGPT account") and an installed
  CLI older than the cache writer can 400 with "requires a newer version of
  Codex". So pins are live-probed by default; cache-only evidence gets the
  weaker LISTED verdict.
- claude: no list command exists; availability is probe-only (`claude
  --model X -p` → exit 0, or exit 1 + "There's an issue with the selected
  model"). Probes MUST scrub the Claude-session env vars and run from a cwd
  outside any playbook project — a nested claude session inside the project
  clobbers the active task's session state (live incident). Probe timeouts
  are UNKNOWN, never GONE. New Claude models can't be discovered, only
  candidate ids supplied via pins/aliases/--claude-candidates.
- agy (1.2.17, measured 2026-10-05 — task 111): `agy models` prints
  `id<TAB>label`; the id carries the effort and is what `--model` takes. A
  listed id can still be unusable (quota used up, not signed in), so a pin is
  live-probed with ONE tiny real turn through the judge's own stdin path;
  listing alone earns the weaker LISTED. A rejected `--model` fails before any
  turn with `invalid model selection (…)`, which agy uses for three different
  mistakes: an id it does not know (GONE), a base id without an effort and an
  id contradicted by an effort flag (both BAD_EFFORT — a spec error, never a
  dead pin). A pin is a whole id: `agy:<base>:<effort>` is refused, because
  agy's stream would name only `<base>`. `agy -p /quota` and `/credits` answer without a model turn.
- grok: `grok models` lists the ACCOUNT'S entitled model ids (login-aware —
  unlike the codex cache this list IS the entitlements). Entitled is not
  usable: the list cannot see that the credit ran out (2026-09-29: every call
  402 while the pin read OK), so a listed pin is live-probed with one tiny
  turn and listing alone earns LISTED (task 171). A pin that is NOT listed is
  GONE without a call. A bad `-m` fails fast pre-turn: exit 1 + stderr `Couldn't set
  model '<x>': Invalid params: "unknown model id"` (verified live, 0.2.99),
  which makes grok pins probe-confirmable for the hard-stop path.
- pi: no discovery surface known; adapter availability check only.

Verdicts:
  OK                verified available (live probe, or provider-default pin)
  LISTED            in the codex cache / `agy models` / `grok models` but not live-verified (--no-probe)
  GONE              verified NOT available (probe/cache says so)
  BAD_EFFORT        codex model exists but the :effort suffix isn't supported
  NEEDS_CLI_UPGRADE model needs a newer provider CLI (codex 400 signature)
  UNVERIFIABLE      provider offers no way to check (pi)
  PROVIDER_MISSING  the pin's provider CLI is not available on this machine
  UNPROBED          claude pin under --no-probe
  UNKNOWN           probe indeterminate (timeout, launch failure, odd error)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tasks.atomic import atomic_write

CODEX_CACHE_PATH = Path.home() / ".codex" / "models_cache.json"
CACHE_STALE_DAYS = 7
PROBE_TIMEOUT_SECS = 120
CLAUDE_PROBE_BUDGET_USD = "0.5"

OK = "OK"
LISTED = "LISTED"
GONE = "GONE"
BAD_EFFORT = "BAD_EFFORT"
NEEDS_CLI_UPGRADE = "NEEDS_CLI_UPGRADE"
UNVERIFIABLE = "UNVERIFIABLE"
PROVIDER_MISSING = "PROVIDER_MISSING"
UNPROBED = "UNPROBED"
UNKNOWN = "UNKNOWN"

# Live-captured codex 400 signatures (see task 012 References corpus).
_CODEX_MODEL_GONE = "model is not supported"
_CODEX_CLI_TOO_OLD = "requires a newer version of Codex"

# Failure classification of a judge's post-format_judge_output string.
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
CLI_UPGRADE_REQUIRED = "CLI_UPGRADE_REQUIRED"
OTHER = "OTHER"

_CLAUDE_MODEL_GONE = "There's an issue with the selected model"
_BUDGET_EXCEEDED = "Error: Exceeded USD budget"

# Live-captured grok bad-model signature (task 014): exit 1 + stderr
# `Couldn't set model 'x': Invalid params: "unknown model id". Run 'grok
# models' to see available models.` Match both fragments — the quoted
# "unknown model id" alone is a phrase a review could plausibly quote.
_GROK_MODEL_GONE = "Couldn't set model"
_GROK_MODEL_GONE_2 = 'Invalid params: "unknown model id"'

# Live-captured agy 1.2.17 signatures (task 111, tests/fixtures/agy-1.2.17/). agy
# answers THREE different mistakes with `invalid model selection (…)`; only the
# first means the model is gone:
#   …: model <id> is not recognized as a known model or custom model in settings
#   …: --model <base> requires --effort (available: low, medium, high)
#   …: --model <id> conflicts with --effort=<e>
_AGY_MODEL_SELECTION = "invalid model selection"
_AGY_MODEL_GONE = "is not recognized as a known model"
_AGY_EFFORT_MISSING = "requires --effort"
_AGY_EFFORT_CONFLICT = "conflicts with --effort"


def judge_failed(text: str) -> bool:
    """True when a judge's output string is a failure, not a review.

    Failure markers come from format_judge_output (`(FAILED — exit N)`,
    `(no output)`), the panel's run_judge guards (`(timed out…)`,
    `(error:…)`), and claude's budget-exhaustion message — which claude
    prints to stdout with exit 0, so it can't be caught by returncode and
    is anchored to block START (a review merely QUOTING it must not flag).
    """
    t = (text or "").lstrip()
    return (t.startswith("(FAILED") or t.startswith("(timed out")
            or t.startswith("(error") or t == "(no output)"
            or t.startswith(_BUDGET_EXCEEDED))


def budget_exceeded(text: str) -> bool:
    """True when a judge's output is claude's budget-exhaustion message."""
    return (text or "").lstrip().startswith(_BUDGET_EXCEEDED)


def classify_failure(output_text: str) -> str:
    """Classify a FAILED judge string → MODEL_UNAVAILABLE | CLI_UPGRADE_REQUIRED | OTHER.

    Only failure-marked strings are classified — a successful (rc==0) review
    that quotes these patterns never reaches the pattern checks, which kills
    the self-referential false positive (this repo's task.md contains every
    pattern verbatim and rides in judge context). Branches are mutually
    exclusive, most-specific first. Callers must still probe-confirm a
    MODEL_UNAVAILABLE/CLI_UPGRADE_REQUIRED verdict (probe_claude_model /
    probe_codex_model) before hard-stopping.
    """
    t = output_text or ""
    if not judge_failed(t):
        return OTHER
    if "invalid_request_error" in t and _CODEX_CLI_TOO_OLD in t:
        return CLI_UPGRADE_REQUIRED
    if "invalid_request_error" in t and _CODEX_MODEL_GONE in t:
        return MODEL_UNAVAILABLE
    if _CLAUDE_MODEL_GONE in t:
        return MODEL_UNAVAILABLE
    if _GROK_MODEL_GONE in t and _GROK_MODEL_GONE_2 in t:
        return MODEL_UNAVAILABLE
    if _AGY_MODEL_SELECTION in t and _AGY_MODEL_GONE in t:
        return MODEL_UNAVAILABLE
    return OTHER


def _adapter_classes() -> dict:
    from provider.adapters.antigravity import AntigravityAdapter
    from provider.adapters.claude import ClaudeAdapter
    from provider.adapters.codex import CodexAdapter
    from provider.adapters.grok import GrokAdapter
    from provider.adapters.pi import PiAdapter
    return {"claude": ClaudeAdapter, "codex": CodexAdapter,
            "agy": AntigravityAdapter, "pi": PiAdapter,
            "grok": GrokAdapter}


# ── codex ────────────────────────────────────────────────────────────────────

def parse_codex_cache(text: str) -> dict:
    """`models_cache.json` content → {fetched_at, client_version, models}.

    `models` maps slug → list of supported effort levels. Hidden entries
    (visibility != "list") are kept — a pin to one still runs.
    """
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("models cache is not a JSON object")
    models: dict[str, list[str]] = {}
    entries = raw.get("models", [])
    for m in entries if isinstance(entries, list) else []:
        if not isinstance(m, dict):
            continue
        slug = m.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        levels = m.get("supported_reasoning_levels", [])
        efforts = [
            lvl.get("effort")
            for lvl in (levels if isinstance(levels, list) else [])
            if isinstance(lvl, dict) and isinstance(lvl.get("effort"), str)
        ]
        models[slug] = efforts
    return {
        "fetched_at": raw.get("fetched_at"),
        "client_version": raw.get("client_version"),
        "models": models,
    }


def load_codex_cache(path: Path = CODEX_CACHE_PATH) -> Optional[dict]:
    """Parse the codex models cache; None when absent/unreadable."""
    try:
        return parse_codex_cache(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cache_age_days(fetched_at: Optional[str]) -> Optional[float]:
    """Age of the cache's ISO-8601 fetched_at stamp, in days; None if unparsable,
    not a string, or in the future (a future stamp is unknown, never fresh)."""
    if not fetched_at or not isinstance(fetched_at, str):
        return None
    # the codex cache stamps nanoseconds (`...51.980219765Z`) and Python 3.10's
    # fromisoformat accepts ONLY 3 or 6 fractional digits — normalise the fraction
    # to exactly 6 (trim or zero-pad), never reject (task 054)
    normalized = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"),
                        fetched_at.replace("Z", "+00:00"))
    try:
        stamp = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp).total_seconds() / 86400
    return age if age >= 0 else None


def installed_cli_version(binary: str = "codex") -> Optional[str]:
    """`<binary> --version` → "X.Y.Z", or None when missing/unparsable."""
    if not shutil.which(binary):
        return None
    try:
        result = subprocess.run(
            [binary, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", result.stdout or "")
    return match.group(1) if match else None


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def probe_codex_model(model: str, effort: Optional[str] = None,
                      timeout: int = PROBE_TIMEOUT_SECS) -> tuple[str, str]:
    """Live-probe one codex model id → (verdict, detail).

    The cache is a catalog, not an entitlement list (a listed model can 400
    per-account), so GONE/NEEDS_CLI_UPGRADE come only from the live 400
    signatures; timeouts and unrecognized failures are UNKNOWN. When the pin
    carries an :effort suffix, the probe sends the same
    `-c model_reasoning_effort=` the judge path sends (codex.py), so an
    effort the model doesn't accept fails here instead of at review time.
    """
    argv = ["codex", "exec", "-m", model, "--skip-git-repo-check"]
    if effort:
        argv += ["-c", f"model_reasoning_effort={effort}"]
    argv.append("reply with exactly: ok")
    with tempfile.TemporaryDirectory(prefix="playbook-models-probe-") as td:
        try:
            result = subprocess.run(
                argv,
                cwd=td, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return UNKNOWN, f"probe timed out after {timeout}s"
        except OSError as e:
            return UNKNOWN, f"probe failed to launch: {e}"
    if result.returncode == 0:
        return OK, "responds"
    combined = (result.stdout or "") + (result.stderr or "")
    if _CODEX_CLI_TOO_OLD in combined:
        return NEEDS_CLI_UPGRADE, "model requires a newer codex CLI — run `codex update`"
    if _CODEX_MODEL_GONE in combined:
        return GONE, "codex rejects this model for this account"
    first = combined.strip().splitlines()[0][:160] if combined.strip() else f"exit {result.returncode}"
    return UNKNOWN, f"probe failed for another reason: {first}"


# ── agy ──────────────────────────────────────────────────────────────────────

def parse_agy_models(text: str) -> list[str]:
    """`agy models` stdout → model-id list.

    agy 1.2.17 prints one `id<TAB>label` line per model (captured:
    tests/fixtures/agy-1.2.17/models.stdout); the id is the first field and is
    what `--model` takes. A line with no tab is kept only when it is a single
    token — so a progress line ("Fetching available models...", stderr in
    1.2.17) or a display-name-only line from an older agy never becomes an id.
    """
    ids: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        head = s.split("\t", 1)[0].strip()
        if head and not any(ch.isspace() for ch in head):
            ids.append(head)
    return ids


def _agy_env() -> dict:
    """The environment EVERY agy call of this module runs in — the judge's own
    (`antigravity.judge_env`: the billed-credential variables dropped), so the listing,
    the quota and the credits are read with the credential the judge will spend."""
    from provider import sandbox as _sandbox
    from provider.adapters.antigravity import judge_env
    env = judge_env(os.environ, "models-check")
    _sandbox.scrub_parent_session_env(env)
    return env


def list_agy_models() -> Optional[list[str]]:
    """Run `agy models`; None when the CLI is missing or errors (not signed in:
    exit 1 in under a second — measured)."""
    if not shutil.which("agy"):
        return None
    try:
        result = subprocess.run(
            ["agy", "models"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=60, encoding="utf-8", errors="replace", env=_agy_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_agy_models(result.stdout or "")


def _agy_slash_command(command: str) -> Optional[dict]:
    """`agy -p /<command> --output-format json` → its `command.data` object, or
    None. These read-only slash commands answer without a model turn (no quota
    spent). Only call when agy is signed in: unsigned, this `-p` path waits a
    minute for a login before failing (measured) — hence the short timeout."""
    if not shutil.which("agy"):
        return None
    try:
        result = subprocess.run(
            ["agy", "-p", command, "--output-format", "json"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace", env=_agy_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        obj = json.loads((result.stdout or "").strip())
    except ValueError:
        return None
    data = obj.get("command", {}).get("data") if isinstance(obj, dict) and isinstance(obj.get("command"), dict) else None
    return data if isinstance(data, dict) else None


def agy_quota() -> Optional[list[dict]]:
    """Remaining agy quota per bucket from `agy -p /quota` — a list of
    `{id, group, window, remaining_fraction, reset_time}` (captured shape:
    tests/fixtures/agy-1.2.17/quota.stdout), or None when it cannot be read."""
    data = _agy_slash_command("/quota")
    groups = data.get("groups") if data else None
    if not isinstance(groups, list):
        return None
    out: list[dict] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        for b in g.get("buckets") or []:
            if not isinstance(b, dict):
                continue
            frac = b.get("remaining_fraction")
            if isinstance(frac, bool) or not isinstance(frac, (int, float)):
                continue
            out.append({"id": str(b.get("id") or ""), "group": str(g.get("name") or ""),
                        "window": str(b.get("window") or ""),
                        "remaining_fraction": float(frac),
                        "reset_time": str(b.get("reset_time") or "")})
    return out or None


def agy_credits() -> Optional[int]:
    """Remaining AI credits from `agy -p /credits` (what agy spends once the
    plan quota is gone), or None when it cannot be read."""
    data = _agy_slash_command("/credits")
    n = data.get("remaining_credits") if data else None
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def probe_agy_model(variant: Optional[str], timeout: int = PROBE_TIMEOUT_SECS) -> tuple[str, str]:
    """Live-probe one agy judge seat — a pin (a whole model id) or None for the bare
    seat (agy's selected model) → (verdict, detail).

    ONE tiny real turn through the judge's own invocation (stdin stream-json,
    the pin on `--model`), because a listed model can still be unusable — the
    grok lesson, where a listed pin with no credit read OK. Run directly, from a
    throwaway cwd, WITHOUT the bypass flag (an unsandboxed probe must not
    auto-approve tools) and without the API-key variables. A rejected model
    selection fails before any turn (nothing spent).

      OK          the judge's own acceptance rule holds for the turn
                  (`usage.agy_judge_output`): it finished, it ANSWERED, agy flagged
                  no error, and the stream names the pinned model (impl panel r1 —
                  a turn that ended on an auto-denied tool with an empty answer is
                  UNKNOWN, not OK)
      GONE        agy does not know the id
      BAD_EFFORT  the pin is not a whole agy id (`<base>:<effort>`, or a base id
                  for which agy asks for an effort)
      UNKNOWN     not signed in / quota exhausted / timeout / anything else —
                  with the cause in the detail; never GONE on a guess
    """
    from provider import sandbox as _sandbox
    from provider import usage as _usage
    from provider.adapters.antigravity import AntigravityAdapter, judge_env, pinned_model_id
    with tempfile.TemporaryDirectory(prefix="playbook-models-probe-") as td:
        try:
            inv = AntigravityAdapter("models-check", Path(td)).judge_invocation(
                "reply with exactly: ok", variant, web_search=False, timeout_secs=timeout)
            expected = pinned_model_id(variant)
        except ValueError as e:
            return BAD_EFFORT, str(e)
        env = _agy_env()
        import time as _time
        _t0 = _time.monotonic()
        try:
            result = subprocess.run(
                ["agy", *inv.argv], cwd=td, env=env, input=inv.stdin,
                capture_output=True, text=True, timeout=timeout + 30,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return UNKNOWN, f"probe timed out after {timeout}s"
        except OSError as e:
            return UNKNOWN, f"probe failed to launch: {e}"
    # the judge path's own timeout rule (stderr line, a SUCCESS without usage, a cut
    # stream at the limit) — not only the stderr line (impl panel r2)
    if _usage.agy_timed_out(result.stderr, timeout, _time.monotonic() - _t0, result.stdout):
        return UNKNOWN, f"probe timed out after {timeout}s (agy's own limit)"
    seat = str(_usage.agy_judge_output(result, _sandbox.format_judge_output,
                                       expected_model=expected, web_search=False))
    if not judge_failed(seat):
        return OK, "responds"
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if _AGY_MODEL_SELECTION in combined:
        if _AGY_MODEL_GONE in combined:
            return GONE, "agy does not know this model id"
        if _AGY_EFFORT_MISSING in combined:
            return BAD_EFFORT, "agy needs an effort for this id — pin the full id from `agy models` (e.g. <base>-high)"
        if _AGY_EFFORT_CONFLICT in combined:
            return BAD_EFFORT, "agy reports an effort conflict for this model selection"
    # the seat's first line names what was wrong (a named cause, an auto-denied tool,
    # the wrong model, no envelope, a flagged turn) — it is the detail
    first = seat.strip().splitlines()[0] if seat.strip() else f"exit {result.returncode}"
    first = first.removeprefix("(FAILED — ").removesuffix(")")
    return UNKNOWN, "probe turn was not a clean answer: " + first[:220]


# ── grok ─────────────────────────────────────────────────────────────────────

# `--effort/--reasoning-effort: unknown effort level 'x'; use one of: …` (exit 1;
# captured from grok 1.0.50, 2026-10-10)
_GROK_BAD_EFFORT = "unknown effort level"

def parse_grok_models(text: str) -> list[str]:
    """`grok models` stdout → model-id list.

    Live format (grok 0.2.99):
        You are logged in with grok.com.

        Default model: grok-composer-2.5-fast

        Available models:
          * grok-composer-2.5-fast (default)
          - grok-build

    Model ids are the first token after a `*`/`-` bullet; the `(default)`
    decoration and prose lines (login banner, headers) are dropped.
    """
    models: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(("* ", "- ")):
            model = s[2:].split()[0].strip()
            if model:
                models.append(model)
    return models


def list_grok_models() -> Optional[list[str]]:
    """Run `grok models`; None when the CLI is missing or errors.

    Unlike the codex models cache (a catalog), this list is login-aware: what
    it lists is what the account is ENTITLED to. That is not "can run now" — it
    cannot see spent credit — so `check_pins` gives a listed pin a live turn, or
    LISTED where nothing is probed (task 171); a pin that is not listed is GONE.
    """
    if not shutil.which("grok"):
        return None
    try:
        result = subprocess.run(
            ["grok", "models"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=60, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_grok_models(result.stdout or "")


def probe_grok_model(model: str, effort: Optional[str] = None,
                     timeout: int = PROBE_TIMEOUT_SECS) -> tuple[str, str]:
    """Live-probe one grok seat — a model id and, when the pin carries one, its
    effort → (verdict, detail).

    A bad `-m` fails BEFORE any turn runs (exit 1 + the stderr signature —
    verified live on 0.2.99), so a GONE probe costs nothing; a good model
    answers one tiny turn. Runs from a throwaway temp cwd so the probe
    session can't attach to a playbook project.

    The probe is of the seat AS PINNED (task 171, impl panel r1): it sends the
    `--reasoning-effort` the judge path sends (grok.py), so an effort the CLI
    rejects is BAD_EFFORT here (exit 1 + `unknown effort level`, captured from
    grok 1.0.50) instead of a failure at review time. And it READS its answer:
    the prompt asks for one word, and OK is exit 0 AND that word (`ok`, in the
    spellings a model gives it) AND nothing on stderr — a healthy call printed
    none (grok 1.0.50, three calls), and the word beside a provider error there
    is not a clean answer (the single judge after round 2). The list of entitled models cannot see spent
    credit, so a 402 must not read OK however the CLI words it; the rule names
    the one good answer instead of a list of bad ones (impl panel r2: the first
    version matched the judge path's event shape and let other wordings through).
    How this plain `-p` mode words a 402 was captured on 2026-10-10 04:59:33, when
    the account ran dry mid-task: exit 1, stdout empty, the JSON object on stderr.
    """
    # --disable-web-search: grok's web tools are default-ON; without this a
    # probe turn can wander into a web search, blow PROBE_TIMEOUT_SECS, and
    # misclassify a live pin as UNKNOWN (probes gate the hard-stop path).
    argv = ["grok", "-p", "reply with exactly: ok", "-m", model,
            "--max-turns", "1", "--disable-web-search"]
    if effort:
        argv += ["--reasoning-effort", effort]
    with tempfile.TemporaryDirectory(prefix="playbook-models-probe-") as td:
        try:
            result = subprocess.run(
                argv,
                cwd=td, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return UNKNOWN, f"probe timed out after {timeout}s"
        except OSError as e:
            return UNKNOWN, f"probe failed to launch: {e}"
    combined = (result.stdout or "") + (result.stderr or "")
    # what it printed, on one line: the CLI words a provider error as a JSON object over
    # several lines, whose first line alone is `Internal error: {` (captured 2026-10-10)
    first = " ".join(combined.split())[:200] or f"exit {result.returncode}"
    if result.returncode == 0:
        said = (result.stdout or "").strip()
        if not said:                     # what it printed elsewhere, if anything, says why
            return UNKNOWN, "probe exited 0 with no answer" + (f": {first}" if combined.strip() else "")
        if "".join(ch for ch in said.lower() if ch.isalnum()) != "ok":
            return UNKNOWN, f"probe exited 0 without the answer it asked for: {first}"
        noise = (result.stderr or "").strip()
        if noise:                        # the word AND a report on stderr is not a clean answer
            return UNKNOWN, f"probe answered, and also reported on stderr: {' '.join(noise.split())[:200]}"
        return OK, "responds"
    if _GROK_MODEL_GONE in combined and _GROK_MODEL_GONE_2 in combined:
        return GONE, "grok rejects this model id for this account"
    if _GROK_BAD_EFFORT in combined:
        return BAD_EFFORT, f"grok rejects this effort: {first}"
    return UNKNOWN, f"probe failed for another reason: {first}"


# ── claude ───────────────────────────────────────────────────────────────────

def probe_claude_model(model: str, timeout: int = PROBE_TIMEOUT_SECS) -> tuple[str, str]:
    """Tiny live probe of one claude model id → (verdict, detail).

    Scrubs the parent-session env vars via the single shared
    `sandbox.scrub_parent_session_env` (the same set `_child_env` strips,
    including CLAUDE_ENV_FILE) and runs from a throwaway temp cwd: the env
    vars — not the cwd — are the vector by which a nested claude session
    attaches to (and clobbers) the calling playbook session. In particular a
    leaked CLAUDE_ENV_FILE would let this probe's SessionStart hook append
    `models-check` to the foreground's shared env file and shadow its
    active-task pointer (task 037, PB-SESSION-POINTER-ISOLATION). Budget-capped
    so a probe can never spend more than pennies; timeouts are UNKNOWN, never GONE.
    """
    from provider import sandbox as _sandbox
    env = os.environ.copy()
    env["CLAUDECODE"] = ""
    _sandbox.scrub_parent_session_env(env)
    env["PLAYBOOK_SESSION_ID"] = "models-check"
    with tempfile.TemporaryDirectory(prefix="playbook-models-probe-") as td:
        try:
            result = subprocess.run(
                ["claude", "--model", model, "-p", "reply with exactly: ok",
                 "--max-budget-usd", CLAUDE_PROBE_BUDGET_USD],
                cwd=td, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return UNKNOWN, f"probe timed out after {timeout}s"
        except OSError as e:
            return UNKNOWN, f"probe failed to launch: {e}"
    if result.returncode == 0:
        return OK, "responds"
    combined = ((result.stdout or "") + (result.stderr or "")).strip()
    if "There's an issue with the selected model" in combined:
        return GONE, "claude rejects this model id"
    first = combined.splitlines()[0][:160] if combined else f"exit {result.returncode}"
    return UNKNOWN, f"probe failed for another reason: {first}"


def _claude_configured_models(project_root: Optional[Path] = None) -> list[str]:
    """Claude model id(s) the user's Claude Code is actually configured to run.

    Claude has no list command (anthropics/claude-code#12612) and the Models
    API needs a key — but Claude Code records the selected model in its own
    settings, a key-free live signal we can read the way we read
    ~/.codex/models_cache.json for codex. Reads the `model` field from
    ~/.claude/settings.json (user scope) and <project_root>/.claude/settings.json
    (project scope). Returns each `claude-*` id plus its bracket-stripped bare
    form (e.g. 'claude-fable-5[1m]' -> ['claude-fable-5[1m]', 'claude-fable-5'])
    so both the exact configured id and the plain id get probed. Short alias
    words ('sonnet', 'default') are skipped — the shipped alias baseline already
    covers those. Defensive: a missing/unreadable/malformed file yields nothing.
    """
    import json
    import re
    out: list[str] = []
    paths = [Path.home() / ".claude" / "settings.json"]
    if project_root is not None:
        paths.append(Path(project_root) / ".claude" / "settings.json")
    for p in paths:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m = raw.get("model") if isinstance(raw, dict) else None
        if not isinstance(m, str) or not m.strip().startswith("claude-"):
            continue
        m = m.strip()
        out.append(m)
        bare = re.sub(r"\[.*?\]\s*$", "", m).strip()
        if bare and bare != m:
            out.append(bare)
    return out


def claude_candidate_models(panel_specs: list[str], extra: Optional[list[str]] = None,
                            project_root: Optional[Path] = None) -> list[str]:
    """Claude ids worth probing, most-live first:
    Claude Code's configured model ∪ pins ∪ shipped alias targets ∪ user-supplied.

    Claude has no list API/CLI command, so the set is assembled from live
    signals rather than one hardcoded table: `_claude_configured_models` reads
    the model the user's Claude Code is actually set to (key-free), pins and
    shipped aliases seed the current family, and `--claude-candidates` covers
    anything newer. Each is probed to confirm availability.
    """
    from provider.sandbox import MODEL_ALIASES, resolve_judge_spec
    candidates: list[str] = []
    # Live signal first: what Claude Code is actually configured to run.
    candidates.extend(_claude_configured_models(project_root))
    for nm in panel_specs:
        try:
            provider, variant = resolve_judge_spec(nm)
        except ValueError:
            continue
        if provider == "claude" and variant:
            candidates.append(variant)
    for agent, model, _extras in MODEL_ALIASES.values():
        if agent == "claude" and model:
            candidates.append(model)
    candidates.extend(extra or [])
    seen: set[str] = set()
    return [m for m in candidates if not (m in seen or seen.add(m))]


# ── check ────────────────────────────────────────────────────────────────────

def check_pins(project_root: Path, probe: bool = True,
               extra_specs: Optional[list[str]] = None,
               claude_candidates: Optional[list[str]] = None) -> dict:
    """Verdict for every models.json pin (+ extra_specs) + provider inventories.

    extra_specs lets the hard-stop path include the ACTUAL failed runtime
    specs (`--models`/`--model` overrides), not just configured pins.
    Returns {"entries": [{spec, provider, variant, verdict, detail}],
             "codex": cache|None, "codex_cli_version": str|None,
             "agy_models": [names]|None, "claude_candidates": [ids],
             "warnings": [str]}.
    """
    from provider.adapters.codex import _split_reasoning_effort
    from provider.sandbox import load_judge_config, resolve_judge_spec

    cfg = load_judge_config(project_root)
    panel = list(cfg.get("panel") or [])
    default_judge = cfg.get("default_judge")
    specs = list(panel)
    # User-supplied claude candidates get probed verdict rows like any pin —
    # this is the only way a NEW claude model id enters the report (I7).
    candidate_specs = [f"claude:{cid}" for cid in (claude_candidates or [])]
    for s in ([default_judge] if default_judge else []) + list(extra_specs or []) + candidate_specs:
        if s and s not in specs:
            specs.append(s)

    adapters = _adapter_classes()
    codex_cache = load_codex_cache()
    codex_version = installed_cli_version("codex")
    agy_models = list_agy_models() if adapters["agy"].is_available() else None
    _grok_adapter = adapters.get("grok")
    grok_models = (list_grok_models()
                   if _grok_adapter and _grok_adapter.is_available() else None)
    warnings: list[str] = []

    if codex_cache:
        age = cache_age_days(codex_cache.get("fetched_at"))
        if age is not None and age > CACHE_STALE_DAYS:
            warnings.append(
                f"codex models cache is {age:.0f} days old — run any codex "
                f"command to refresh it before trusting these verdicts"
            )
        writer = codex_cache.get("client_version")
        if writer and codex_version:
            try:
                if _version_tuple(codex_version) < _version_tuple(writer):
                    warnings.append(
                        f"installed codex CLI {codex_version} is older than the "
                        f"cache writer {writer} — newer models may fail with "
                        f"'requires a newer version of Codex'; run `codex update`"
                    )
            except ValueError:
                pass

    probed: dict[tuple[str, str], tuple[str, str]] = {}

    def _probe(provider: str, model: str, effort: Optional[str] = None) -> tuple[str, str]:
        key = (provider, model, effort)
        if key not in probed:
            if provider == "claude":
                probed[key] = probe_claude_model(model)
            elif provider == "grok":
                probed[key] = probe_grok_model(model, effort=effort)
            elif provider == "agy":
                probed[key] = probe_agy_model(model)      # `model` is the whole pin here
            else:
                probed[key] = probe_codex_model(model, effort=effort)
        return probed[key]

    entries = []
    for spec in specs:
        if spec.endswith(":"):
            # resolve_judge_spec accepts "codex:" as variant=None, silently
            # running the provider default — surface it instead (R13).
            warnings.append(f"pin '{spec}' has an empty variant — it would "
                            f"silently run the provider's default model")
        try:
            provider, variant = resolve_judge_spec(spec)
        except ValueError as e:
            entries.append({"spec": spec, "provider": "?", "variant": None,
                            "verdict": GONE, "detail": str(e)})
            continue
        adapter = adapters.get(provider)
        if adapter is None:
            entries.append({"spec": spec, "provider": provider, "variant": variant,
                            "verdict": GONE,
                            "detail": f"unknown provider '{provider}' (bad alias?)"})
            continue
        if not adapter.is_available():
            entries.append({"spec": spec, "provider": provider, "variant": variant,
                            "verdict": PROVIDER_MISSING,
                            "detail": f"provider '{provider}' not available on this machine"})
            continue

        if provider == "codex":
            if not variant:
                verdict, detail = OK, "uses the codex default model"
            else:
                try:
                    model_id, effort = _split_reasoning_effort(variant)
                except ValueError as e:
                    entries.append({"spec": spec, "provider": provider, "variant": variant,
                                    "verdict": BAD_EFFORT, "detail": str(e)})
                    continue
                efforts = (codex_cache or {"models": {}})["models"].get(model_id)
                if effort and efforts and effort not in efforts:
                    verdict = BAD_EFFORT
                    detail = f"'{model_id}' supports efforts {', '.join(efforts)} — not '{effort}'"
                elif probe:
                    verdict, detail = _probe("codex", model_id, effort)
                elif codex_cache is None:
                    verdict, detail = UNVERIFIABLE, "no ~/.codex/models_cache.json to check against"
                elif efforts is None:
                    verdict = GONE
                    detail = f"'{model_id}' not in models cache (have: {', '.join(sorted(codex_cache['models']))})"
                else:
                    verdict, detail = LISTED, "in models cache (not live-verified — cache is a catalog, not entitlements)"
        elif provider == "claude":
            if not variant:
                verdict, detail = OK, "uses the claude default model"
            elif not probe:
                verdict, detail = UNPROBED, "claude has no list command; re-run without --no-probe"
            else:
                verdict, detail = _probe("claude", variant)
        elif provider == "agy":
            if not variant:
                # The bare seat runs whatever model is selected in agy. It is probed like
                # a pin (impl panel r1: it used to read OK with no call, even signed out).
                if probe:
                    verdict, detail = _probe("agy", None)
                    if verdict == OK:
                        detail = "responds (the model selected in agy)"
                elif agy_models is not None:
                    verdict, detail = LISTED, "agy is signed in (`agy models` answered); the selected model was not live-verified"
                else:
                    verdict, detail = UNKNOWN, "`agy models` unavailable (signed in?); re-run without --no-probe"
            else:
                from provider.adapters.antigravity import validate_model_id as _agy_id
                try:
                    model_id = _agy_id(variant)
                except ValueError as e:        # `<base>:<effort>` — an agy pin is a whole id
                    entries.append({"spec": spec, "provider": provider, "variant": variant,
                                    "verdict": BAD_EFFORT, "detail": str(e)})
                    continue
                if agy_models is None:
                    # CLI present but `agy models` failed (not signed in?).
                    if probe:
                        verdict, detail = _probe("agy", variant)
                    else:
                        verdict, detail = UNKNOWN, "`agy models` unavailable (signed in?); re-run without --no-probe"
                elif model_id not in agy_models:
                    verdict = GONE
                    detail = f"'{model_id}' not in `agy models` (have: {', '.join(agy_models)})"
                elif probe:
                    # Listed is not enough: a listed model with no quota left, or an
                    # expired sign-in, must not read OK — one real turn decides.
                    verdict, detail = _probe("agy", variant)
                else:
                    verdict, detail = LISTED, "in `agy models` (not live-verified — a listed model can still be out of quota)"
        elif provider == "grok":
            if not variant:
                verdict, detail = OK, "uses the grok default model"
            else:
                from provider.adapters.grok import _split_reasoning_effort as _grok_split
                try:
                    model_id, effort = _grok_split(variant)
                except ValueError as e:
                    entries.append({"spec": spec, "provider": provider, "variant": variant,
                                    "verdict": BAD_EFFORT, "detail": str(e)})
                    continue
                if grok_models is None:
                    # CLI present but `grok models` failed (logged out?) —
                    # fall back to a live probe when allowed.
                    if probe:
                        verdict, detail = _probe("grok", model_id, effort)
                    else:
                        verdict, detail = UNKNOWN, "`grok models` unavailable (logged out?); re-run without --no-probe"
                elif model_id not in grok_models:
                    verdict = GONE
                    detail = f"'{model_id}' not in `grok models` (have: {', '.join(grok_models)})"
                elif probe:
                    # Listed is not enough (retro 107 R17, task 171): the list is the
                    # account's ENTITLEMENTS and cannot see that its credit ran out —
                    # on 2026-09-29 every grok call answered 402 while this line said
                    # OK. One real turn decides, as for a codex or an agy pin — of the
                    # seat as pinned: the effort goes with it (impl panel r1).
                    verdict, detail = _probe("grok", model_id, effort)
                else:
                    verdict, detail = LISTED, ("in `grok models` (not live-verified — an entitled "
                                               "model can still be out of credit)")
        else:  # pi
            verdict, detail = UNVERIFIABLE, "pi has no model-discovery surface"
        entries.append({"spec": spec, "provider": provider, "variant": variant,
                        "verdict": verdict, "detail": detail})

    return {"entries": entries, "codex": codex_cache, "codex_cli_version": codex_version,
            "agy_models": agy_models, "grok_models": grok_models,
            "claude_candidates": claude_candidate_models(specs, claude_candidates, project_root),
            "warnings": warnings}


def render_report(report: dict) -> str:
    """Human-readable availability report for stdout / hard-stop output."""
    lines = ["=== Judge pin verdicts (.agent/models.json ⊕ shipped) ==="]
    width = max((len(e["spec"]) for e in report["entries"]), default=10)
    for e in report["entries"]:
        lines.append(f"  {e['spec']:<{width}}  {e['verdict']:<18} {e['detail']}")
    codex = report.get("codex")
    if codex:
        age = cache_age_days(codex.get("fetched_at"))
        age_s = f", fetched {age:.1f}d ago" if age is not None else ""
        lines.append(f"\n=== codex models (cache writer {codex.get('client_version')}, "
                     f"installed {report.get('codex_cli_version')}{age_s}) ===")
        for slug, efforts in codex["models"].items():
            lines.append(f"  {slug:<22} efforts: {', '.join(efforts) if efforts else '-'}")
    if report.get("agy_models") is not None:
        lines.append("\n=== agy models (pin as agy:<id> — the id carries the effort) ===")
        for name in report["agy_models"]:
            lines.append(f"  {name}")
    if report.get("agy_quota"):
        lines.append("\n=== agy quota left (`agy -p /quota` — no model turn) ===")
        for b in report["agy_quota"]:
            lines.append(f"  {b['group']:<24} {b['window']:<7} {b['remaining_fraction'] * 100:5.1f}%  "
                         f"resets {b['reset_time']}")
    if report.get("agy_credits") is not None:
        lines.append(f"  AI credits (spent after the plan quota runs out): {report['agy_credits']}")
    if report.get("grok_models") is not None:
        lines.append("\n=== grok models (account-entitled list from `grok models`) ===")
        for name in report["grok_models"]:
            lines.append(f"  {name}")
    if report.get("claude_candidates"):
        lines.append("\n=== claude candidates (probed — claude has no list command, so this "
                     "is your Claude Code configured model ∪ pins ∪ known ids; add more "
                     "with --claude-candidates) ===")
        for model in report["claude_candidates"]:
            lines.append(f"  {model}")
    for w in report.get("warnings", []):
        lines.append(f"\nWARNING: {w}")
    return "\n".join(lines)


def bad_pins(report: dict) -> list[dict]:
    """Entries whose verdict means the judge cannot run as pinned."""
    return [e for e in report["entries"]
            if e["verdict"] in (GONE, BAD_EFFORT, NEEDS_CLI_UPGRADE, PROVIDER_MISSING)]


def confirm_dead_specs(failed_outputs: dict, spec_providers: dict, *,
                       probe_claude=None, probe_codex=None, probe_grok=None,
                       probe_agy=None) -> dict:
    """Probe-confirm which FAILED judge specs are actually dead.

    The shared hard-stop gate for panel and single-judge reviews:
    classification of the failure string is only a hint (failure tails can
    echo prompt fragments containing the very signatures we match); a live
    probe of the exact spec is the evidence that justifies exit 1.

    failed_outputs: {spec_label: output_text} for failed judges only.
    spec_providers: {spec_label: (provider, variant_or_None)}.
    Probes are injectable for tests. Returns {spec_label: (verdict, detail)}
    holding only probe-confirmed GONE / NEEDS_CLI_UPGRADE specs — pi,
    variantless pins, and local effort-spec errors are unconfirmable and
    skipped (they keep today's soft-fail). grok pins ARE confirmable (a bad
    -m fails pre-turn with a stable signature, task 014), and so are agy pins
    (an unknown id fails pre-turn with "is not recognized as a known model",
    task 111 — a missing or contradicted effort is a spec error, never GONE).
    """
    from provider.adapters.codex import _split_reasoning_effort
    from provider.adapters.grok import _split_reasoning_effort as _grok_split
    probe_claude = probe_claude or probe_claude_model
    probe_codex = probe_codex or probe_codex_model
    probe_grok = probe_grok or probe_grok_model
    probe_agy = probe_agy or probe_agy_model
    confirmed: dict = {}
    for spec in sorted(failed_outputs):
        if classify_failure(failed_outputs[spec]) not in (
                MODEL_UNAVAILABLE, CLI_UPGRADE_REQUIRED):
            continue
        provider, variant = spec_providers.get(spec, (None, None))
        if provider == "claude" and variant:
            pv, detail = probe_claude(variant)
        elif provider == "codex" and variant:
            try:
                model_id, effort = _split_reasoning_effort(variant)
            except ValueError:
                continue  # local spec error, not availability
            pv, detail = probe_codex(model_id, effort=effort)
        elif provider == "grok" and variant:
            try:
                model_id, _effort = _grok_split(variant)
            except ValueError:
                continue  # local spec error, not availability
            pv, detail = probe_grok(model_id)
        elif provider == "agy" and variant:
            pv, detail = probe_agy(variant)
        else:
            continue
        if pv in (GONE, NEEDS_CLI_UPGRADE):
            confirmed[spec] = (pv, detail)
    return confirmed


def apply_confirmed(report: dict, confirmed: dict) -> dict:
    """Override report entries with probe-confirmed verdicts.

    The hard-stop report is built with probe=False for speed; without this,
    a pin just probe-confirmed GONE (per-account 400) could still render
    LISTED from the cache in the very same output — a self-contradiction.
    """
    for e in report["entries"]:
        if e["spec"] in confirmed:
            e["verdict"], e["detail"] = confirmed[e["spec"]]
    return report


# ── select ───────────────────────────────────────────────────────────────────

def _project_models_path(project_root: Path) -> Path:
    return project_root / ".agent" / "models.json"


def spec_error(spec: str) -> Optional[str]:
    """Syntactic validation shared by panel entries and default_judge.

    Empty variants and codex/grok/agy effort suffixes are checked here because
    resolve_judge_spec accepts both (``codex:`` → default model,
    ``codex:gpt-5.5:bogus`` → effort unvalidated until review time). Returns an
    error string, or None when the spec is syntactically usable. Availability
    (does the model actually run) is a separate, probe-backed question — see
    _audit_proposed.
    """
    from provider.adapters.codex import _split_reasoning_effort
    from provider.sandbox import resolve_judge_spec
    if spec.endswith(":"):
        return f"pin '{spec}' has an empty variant"
    try:
        provider, variant = resolve_judge_spec(spec)
    except ValueError as e:
        return str(e)
    if provider == "codex" and variant:
        try:
            _split_reasoning_effort(variant)
        except ValueError as e:
            return str(e)
    if provider == "grok" and variant:
        from provider.adapters.grok import _split_reasoning_effort as _grok_split
        try:
            _grok_split(variant)
        except ValueError as e:
            return str(e)
    if provider == "agy" and variant:
        from provider.adapters.antigravity import validate_model_id as _agy_id
        try:
            _agy_id(variant)
        except ValueError as e:
            return str(e)
    return None


def _read_existing_models(path: Path) -> dict:
    """Round-trip the RAW models.json so hand-authored keys survive a rewrite.

    load_judge_config would drop everything but two keys; select/set mutate
    panel/default_judge/_updated in place and leave _doc, aliases, … intact.
    An unreadable file starts fresh (a warning goes to stderr).
    """
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"WARNING: existing {path} unreadable ({e}) — starting fresh", file=sys.stderr)
            return {}
        # Valid JSON but not an object (e.g. a bare list) is unusable — the
        # writers do `existing[...]=...` / `.get(...)`. Start fresh, don't crash.
        if not isinstance(data, dict):
            print(f"WARNING: existing {path} is not a JSON object — starting fresh",
                  file=sys.stderr)
            return {}
        return data
    return {}


def _audit_proposed(project_root: Path, new_panel: list[str],
                    default_judge: Optional[str]) -> list[dict]:
    """Cheap (no-probe) availability audit of the PROPOSED pins.

    Without this, select/set can immediately re-create a rotten panel
    (impl-panel I8) — a syntactically-valid pin to a retired model. Returns the
    bad_pins entries that belong to the proposed specs; empty when all look
    usable.
    """
    proposed = list(new_panel) + ([default_judge] if default_judge else [])
    if not proposed:
        return []
    audit = check_pins(project_root, probe=False, extra_specs=proposed)
    return [e for e in bad_pins(audit) if e["spec"] in proposed]


def _write_panel(path: Path, existing: dict, new_panel: Optional[list[str]],
                 default_judge: Optional[str]) -> None:
    """Atomically write panel/default_judge into `existing`, preserving the rest.

    `new_panel=None` leaves the existing `panel` key untouched — so a
    `set --default-judge X` with no `--panel` does NOT silently freeze the
    shipped default panel into the file (and keep it from tracking future
    plugin-panel upgrades). `new_panel=[]` explicitly clears it. An interrupt
    can't truncate models.json — the write goes through a temp + os.replace.
    Seeds a project-scoped `_doc` when the file had none.
    """
    now = datetime.now(timezone.utc)
    if "_panel_changed" not in existing and path.is_file():
        # a LEGACY file (written before the stamp existed): seed the stamp from its
        # pre-write mtime — the best approximation of its last change — so this
        # write does not read as a panel change when the seats stay the same
        try:
            from tasks.dashboard import panel_digest
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            existing["_panel_changed"] = mtime.strftime("%Y-%m-%dT%H:%M:%SZ")
            existing["_panel_changed_for"] = panel_digest(existing.get("panel"))
        except OSError:
            pass
    if new_panel is not None:
        # `_panel_changed` marks the most recent SEAT-LIST change (task 054): the
        # dashboard's health window starts there, so a default-judge-only or
        # same-panel rewrite must not move it (it still bumps `_updated`/mtime).
        # `_panel_changed_for` binds the stamp to the seat list it describes, so a
        # later HAND edit of `panel` (stamp untouched) is detectable by the reader.
        if existing.get("panel") != new_panel:
            from tasks.dashboard import panel_digest
            existing["_panel_changed"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            existing["_panel_changed_for"] = panel_digest(new_panel)
        existing["panel"] = new_panel
    if default_judge:
        existing["default_judge"] = default_judge
    existing["_updated"] = now.date().isoformat()
    existing.setdefault(
        "_doc",
        "Project override for playbook judge selection (shadows the plugin's "
        "provider/models.json per key). Refresh with `tasks models select` or "
        "`tasks models set`; audit with `tasks models check`.",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic — an interrupt can't truncate models.json (package primitive:
    # same-dir temp + fsync + os.replace, preserving the file's mode).
    atomic_write(path, json.dumps(existing, indent=2) + "\n")
    print(f"Wrote {path}")


def run_select(project_root: Path, probe: bool = True,
               claude_candidates: Optional[list[str]] = None) -> int:
    """Interactive panel refresh: show availability, take picks, write models.json.

    Creates `.agent/models.json` when absent (the fresh-install path).
    Round-trips the RAW file json — mutating only panel/default_judge/_updated
    — so hand-authored keys (`_doc`, `aliases`, …) are preserved; going
    through load_judge_config would drop them (it extracts two keys only).
    """
    report = check_pins(project_root, probe=probe, claude_candidates=claude_candidates)
    print(render_report(report))

    path = _project_models_path(project_root)
    existing = _read_existing_models(path)
    # Fallback = the effective (shipped) panel, NOT report entries — the
    # report also lists default_judge and extra specs, which aren't pins.
    from provider.sandbox import load_judge_config
    current_panel = existing.get("panel") or list(load_judge_config(project_root).get("panel") or [])

    print("\nCurrent panel:")
    for i, spec in enumerate(current_panel, 1):
        print(f"  {i}. {spec}")
    print("\nEnter the new panel as comma-separated judge specs")
    print("(provider:variant[:effort] / bare provider / alias — e.g. "
          "claude:claude-fable-5, codex:gpt-5.5:xhigh, grok:grok-build, agy).")
    print("Empty input keeps the current panel unchanged.")
    try:
        raw = input("panel> ").strip()
    except EOFError:
        raw = ""
    new_panel = [s.strip() for s in raw.split(",") if s.strip()] if raw else current_panel

    for spec in new_panel:
        err = spec_error(spec)
        if err:
            print(f"Error: {err}", file=sys.stderr)
            return 1

    default_judge = existing.get("default_judge")
    try:
        dj_raw = input(f"default_judge [{default_judge or 'unset'}]> ").strip()
    except EOFError:
        dj_raw = ""
    if dj_raw:
        err = spec_error(dj_raw)
        if err:
            print(f"Error: {err}", file=sys.stderr)
            return 1
        default_judge = dj_raw

    # Audit the PROPOSED pins (cheap checks) before writing — otherwise select
    # can immediately re-create a rotten panel (impl-panel I8). Bad verdicts
    # need an explicit confirmation.
    bad = _audit_proposed(project_root, new_panel, default_judge)
    if bad:
        print("\nProposed pin(s) look unusable:", file=sys.stderr)
        for e in bad:
            print(f"  {e['spec']}: {e['verdict']} — {e['detail']}", file=sys.stderr)
        try:
            answer = input("Write anyway? [y/N]> ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("Aborted — nothing written.", file=sys.stderr)
            return 1

    _write_panel(path, existing, new_panel, default_judge)
    return 0


# ── set (non-interactive panel writer) ────────────────────────────────────────

def run_set(project_root: Path, panel: Optional[list[str]] = None,
            default_judge: Optional[str] = None, force: bool = False) -> int:
    """Non-interactive `.agent/models.json` writer — the flag-driven twin of
    run_select, used by `/playbook:init` (which asks the user in the
    conversation, then writes the answer here) and by scripts.

    `panel=None` leaves the existing `panel` key untouched (so setting only the
    default judge doesn't freeze the shipped panel into the file); `panel=[]`
    explicitly clears it (with a warning — an empty panel means panel-review has
    no seats). `default_judge=None` leaves the existing default. Only the specs
    actually being changed are validated + availability-audited (no probe): a
    bad pin ABORTS with exit 1 unless `force=True` — the non-interactive
    analogue of select's "Write anyway?" (there's no one to ask, so refusing
    loudly beats writing a dead panel). Returns 0 on write, 1 on validation/
    audit failure, 2 when neither panel nor default_judge is given.
    """
    if panel is None and default_judge is None:
        print("Error: nothing to set — pass --panel and/or --default-judge",
              file=sys.stderr)
        return 2

    path = _project_models_path(project_root)
    existing = _read_existing_models(path)

    # Validate + audit ONLY what's being changed. When panel is None we are not
    # touching the panel, so its (shipped/existing) pins are not our concern here.
    changed_panel = panel if panel is not None else []
    for spec in changed_panel:
        err = spec_error(spec)
        if err:
            print(f"Error: {err}", file=sys.stderr)
            return 1
    if default_judge:
        err = spec_error(default_judge)
        if err:
            print(f"Error: {err}", file=sys.stderr)
            return 1

    bad = _audit_proposed(project_root, changed_panel, default_judge)
    if bad and not force:
        print("Proposed pin(s) look unusable:", file=sys.stderr)
        for e in bad:
            print(f"  {e['spec']}: {e['verdict']} — {e['detail']}", file=sys.stderr)
        print("Refusing to write a dead panel — re-run with --force to override.",
              file=sys.stderr)
        return 1

    if panel == []:
        print("WARNING: writing an EMPTY panel — panel-review will have no judge "
              "seats. Pass judges to --panel if that wasn't intended.", file=sys.stderr)

    _write_panel(path, existing, panel, default_judge)
    return 0


# ── detect (fast provider/model inventory — no live model probe) ─────────────

def detect_providers(project_root: Optional[Path] = None) -> dict:
    """Which agent CLIs are installed on this machine + each one's candidate
    models and supported reasoning efforts — the menu `/playbook:init` offers
    before it writes a panel.

    Fast because it runs NO live model turn: it reads `shutil.which` + local
    surfaces (codex's models_cache.json, Claude Code's settings.json) and the
    two cheap listing commands (`agy models`, `grok models`). Note both are
    login-aware (they list the account's server-side entitlements), so this is
    not strictly offline — but each listing is bounded by a 60s timeout and no
    model is prompt-probed. Availability of a chosen pin is confirmed separately
    by `tasks models check` (init's optional probe).

    Returns {"providers": [ {name, installed, models, efforts, note} ]} where
    `models` is a list of {"id", "efforts"} (efforts is the per-model effort
    vocabulary, [] when the provider has none) and `efforts` is the provider's
    whole effort vocabulary for quick reference.
    """
    from provider.adapters.grok import _REASONING_EFFORTS as GROK_EFFORTS
    from provider.sandbox import MODEL_ALIASES

    providers: list[dict] = []

    # claude — no list command; assemble from Claude Code's configured model ∪
    # the shipped claude alias targets. No reasoning-effort knob.
    claude_installed = shutil.which("claude") is not None
    claude_models: list[str] = []
    if claude_installed:
        claude_models.extend(_claude_configured_models(project_root))
        for agent, model, _extras in MODEL_ALIASES.values():
            if agent == "claude" and model:
                claude_models.append(model)
    seen: set[str] = set()
    claude_models = [m for m in claude_models if not (m in seen or seen.add(m))]
    providers.append({
        "name": "claude", "installed": claude_installed,
        "models": [{"id": m, "efforts": []} for m in claude_models], "efforts": [],
        "note": ("Claude Code's configured model + shipped ids; no effort knob."
                 if claude_installed else "claude CLI not on PATH."),
    })

    # codex — models + per-model effort levels from the local cache (a catalog,
    # not this account's entitlements, so a listed model can still 400 at run).
    codex_installed = shutil.which("codex") is not None
    codex_models: list[dict] = []
    codex_note = "codex CLI not on PATH."
    codex_age: Optional[float] = None       # days since the cache was fetched (task 054: the dashboard's freshness bar)
    if codex_installed:
        cache = load_codex_cache()
        if cache:
            codex_models = [{"id": slug, "efforts": efforts}
                            for slug, efforts in cache["models"].items()]
            codex_age = cache_age_days(cache.get("fetched_at"))
            codex_note = ("from ~/.codex/models_cache.json (a catalog — a listed "
                          "model can still 400 per-account; init's probe confirms).")
        else:
            codex_note = "codex installed but models cache unreadable."
    providers.append({
        "name": "codex", "installed": codex_installed,
        "models": codex_models, "efforts": [], "note": codex_note,
        "cache_age_days": codex_age,
    })

    # agy — `agy models` lists `id<TAB>label`; the id carries the effort and is
    # what `--model` takes (agy 1.2.17, task 111). `tasks models check` live-probes it.
    agy_installed = shutil.which("agy") is not None
    agy_names = list_agy_models() if agy_installed else None
    if not agy_installed:
        agy_note = "agy CLI not on PATH."
    elif agy_names is None:
        agy_note = "agy installed but `agy models` returned nothing."
    else:
        agy_note = ("ids from `agy models` — the id carries the effort; pin a seat as agy:<id> "
                    "(experimental judge seat; `tasks models check` live-probes the pin).")
    providers.append({
        "name": "agy", "installed": agy_installed,
        "models": [{"id": n, "efforts": []} for n in (agy_names or [])], "efforts": [],
        "note": agy_note,
    })

    # grok — `grok models` is login-aware (the list IS the entitlements);
    # --reasoning-effort accepts low/medium/high.
    grok_installed = shutil.which("grok") is not None
    grok_names = list_grok_models() if grok_installed else None
    grok_efforts = sorted(GROK_EFFORTS)
    if not grok_installed:
        grok_note = "grok CLI not on PATH."
    elif grok_names is None:
        grok_note = "grok installed but `grok models` failed (logged in?)."
    else:
        grok_note = "account-entitled ids from `grok models`; effort suffix low|medium|high."
    providers.append({
        "name": "grok", "installed": grok_installed,
        "models": [{"id": n, "efforts": grok_efforts} for n in (grok_names or [])],
        "efforts": grok_efforts,
        "note": grok_note,
    })

    # pi — no model-discovery surface; the adapter takes an explicit model id.
    pi_installed = shutil.which("pi") is not None
    providers.append({
        "name": "pi", "installed": pi_installed, "models": [], "efforts": [],
        "note": ("installed — no discovery surface; specify a model id explicitly "
                 "(e.g. pi:deepseek/deepseek-v4-flash)." if pi_installed
                 else "pi CLI not on PATH."),
    })

    return {"providers": providers}


def render_detect(report: dict) -> str:
    """Human-readable inventory for `tasks models detect` stdout."""
    lines = ["=== Installed agents & selectable models (fast detect — no live probe) ==="]
    for p in report["providers"]:
        mark = "installed" if p["installed"] else "not installed"
        lines.append(f"\n[{p['name']}] {mark} — {p['note']}")
        if not p["installed"]:
            continue
        if not p["models"]:
            continue
        for m in p["models"]:
            eff = f"  (efforts: {', '.join(m['efforts'])})" if m["efforts"] else ""
            lines.append(f"    {m['id']}{eff}")
    lines.append("\nPanel spec syntax: provider:variant[:effort] / bare provider / alias")
    lines.append("  e.g.  opus, sonnet, codex:gpt-5.5:high, grok:grok-build:medium, agy:gemini-3.8-flash-high")
    return "\n".join(lines)


# ── CLI entry ────────────────────────────────────────────────────────────────

MODELS_USAGE = """Usage: tasks models <subcommand>
  tasks models check  [--no-probe] [--claude-candidates a,b]   audit the configured pins (launches each CLI once)
  tasks models detect [--json]                                 installed agents + their models (no launch)
  tasks models select [--no-probe] [--claude-candidates a,b]   interactive panel rewrite
  tasks models set    --panel a,b --default-judge c [--force]  non-interactive write
  tasks models enable <seat>                                   call a seat recorded out of credit again"""


def cli_models(cmd_args: list[str], project_root: Path) -> int:
    """`tasks models check|select|detect|set [flags]`.

      check  [--no-probe] [--claude-candidates a,b]   audit configured pins
      select [--no-probe] [--claude-candidates a,b]   interactive panel rewrite
      detect [--json]                                 installed agents + models
      set    --panel a,b --default-judge c [--force]  non-interactive write
    """
    args = list(cmd_args)
    # Task 140 (owner Q3b): a bare `tasks models` used to run `check` — a live CLI
    # launch nobody asked for. It prints the usage; a probe is asked for by name.
    if not args:
        print(MODELS_USAGE)
        return 0
    if args[0].startswith("-"):
        print(f"Error: tasks models needs a subcommand before {args[0]!r}. Nothing changed.",
              file=sys.stderr)
        print(MODELS_USAGE, file=sys.stderr)
        return 2
    sub = args.pop(0)

    if sub == "enable":
        # PLAN S12b (task 149): a seat whose provider said the account is out of
        # credit is skipped by later panels until its reset time — or, when the
        # provider gave none (grok), until the owner says it has credit again.
        if len(args) != 1 or args[0].startswith("-"):
            print("Error: tasks models enable takes one seat, as the panel names it "
                  "(e.g. grok:grok-4.7:medium). Nothing changed.", file=sys.stderr)
            return 2
        from tasks.core import resolve_agent_dir
        from tasks.seat_outage import clear_outage
        if clear_outage(resolve_agent_dir(project_root), args[0]):
            print(f"{args[0]}: enabled — the next panel calls it again.")
        else:
            print(f"{args[0]}: no outage recorded for this seat — a panel that is running "
                  "now cannot record one for a failure from before this moment.")
        return 0

    if sub == "detect":
        as_json = False
        for a in args:
            if a == "--json":
                as_json = True
            else:
                print(f"Error: unknown detect flag '{a}'", file=sys.stderr)
                return 2
        report = detect_providers(project_root)
        print(json.dumps(report, indent=2) if as_json else render_detect(report))
        return 0

    def _value(flag: str, idx: int) -> Optional[str]:
        """The value token after a value-taking flag, or None (with a printed
        error) when it's absent or is itself another flag — so `--panel --force`
        reports "missing value" instead of silently consuming `--force`."""
        if idx + 1 >= len(args) or args[idx + 1].startswith("--"):
            print(f"Error: missing value for {flag}", file=sys.stderr)
            return None
        return args[idx + 1]

    if sub == "set":
        panel: Optional[list[str]] = None
        default_judge: Optional[str] = None
        force = False
        i = 0
        while i < len(args):
            if args[i] == "--panel":
                v = _value("--panel", i)
                if v is None:
                    return 2
                panel = [s.strip() for s in v.split(",") if s.strip()]
                i += 2
            elif args[i] == "--default-judge":
                v = _value("--default-judge", i)
                if v is None:
                    return 2
                default_judge = v.strip()
                i += 2
            elif args[i] == "--force":
                force = True
                i += 1
            else:
                print(f"Error: unknown set flag '{args[i]}'", file=sys.stderr)
                return 2
        return run_set(project_root, panel=panel, default_judge=default_judge, force=force)

    # check / select share the probe flags.
    probe = True
    candidates: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--no-probe":
            probe = False
            i += 1
        elif args[i] == "--claude-candidates":
            v = _value("--claude-candidates", i)
            if v is None:
                return 2
            candidates = [s.strip() for s in v.split(",") if s.strip()]
            i += 2
        else:
            print(f"Error: unknown models flag '{args[i]}'", file=sys.stderr)
            return 2
    if sub == "check":
        report = check_pins(project_root, probe=probe, claude_candidates=candidates)
        if probe and report.get("agy_models") is not None:
            # Signed in (the listing answered): add what is left of the subscription
            # quota and the AI-credit balance — both read without a model turn.
            report["agy_quota"] = agy_quota()
            report["agy_credits"] = agy_credits()
        print(render_report(report))
        dead = bad_pins(report)
        if dead:
            print(f"\n{len(dead)} pin(s) cannot run as configured — "
                  f"refresh with: tasks models select", file=sys.stderr)
            return 1
        return 0
    if sub == "select":
        return run_select(project_root, probe=probe, claude_candidates=candidates)
    print(f"Error: unknown models subcommand '{sub}' "
          f"(use: check, select, detect, set)", file=sys.stderr)
    return 2
