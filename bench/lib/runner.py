"""Candidate invocation (plan §7 execution primitive, §15, §18; step 5).

`LiveRunner` is a bench-local, thin copy of the SHAPE of production's tail-cert
raw runner (`tasks.review._run_tail_cert_judge_raw`): resolve the seat spec →
adapter class → `run_headless_judge(prompt, model=variant, system_context="",
web_search=False, timeout_secs, budget_usd)` inside the read-only provider
sandbox, capture stdout. It is NOT imported from production because that
function reads `default_judge` from config and returns tail-cert-worded errors;
copying ~15 lines keeps the bench honest about what it runs and keeps the
production symbol private. Status/usage extraction IS imported
(`_judge_status`, `_parse_judge_usage`) so the bench and the spend journal
speak the same enum.

Classification over the REAL adapter envelope (`provider.sandbox.format_judge_output`):

    "(error: … not found on PATH)"          → dnf, no retry (deterministic)
    "(error: … timed out)" / TimeoutExpired → timeout, no retry
    "(error: …)" other                      → dnf, ONE retry (transport class)
    "(FAILED — exit N)" + "(no output captured)" → dnf, ONE retry (transport class)
    "(FAILED — exit N)" with output         → fail (data: the judge ran and broke)
    "(FAILED — exit N)" + a provider TRANSIENT  → dnf, ONE retry (task 050 W0b: "at capacity",
                                              "Reconnecting...", 5xx — the judge never reviewed)
    either envelope + a QUOTA/CREDIT refusal → dnf, NO retry (task 050: the judge never
                                              reviewed; deterministic until the provider's
                                              reset — the run HALTS, `--resume` re-runs it)
    parseable output                        → ok (findings) | ok-empty | malformed

`FakeRunner` scripts any of these per (case, candidate) and never touches an
adapter — it is what every test and `run --fake` use.

Transport preflight (plan §27.1, panel F3): before ANY candidate runs, the
rendered prompt is checked against each candidate's transport — the adapter's
own `headless_argv` says whether the prompt rides argv or stdin; argv gets
`argv_guard.argv_byte_error` (the physical POSIX cap) and both get production's
`resolve_review_context_chars` char budget as the fairness cap. If ANY candidate
is oversize the case is `excluded` for ALL candidates in the run — paired inputs
are never trimmed per candidate.
"""
from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from bench.lib import REPO_ROOT, PLUGIN_ROOT  # noqa: F401  (sys.path bootstrap)
from bench.lib import scoring
from bench.lib.snapshot import snapshot_tree

STATUSES = ("ok", "fail", "timeout", "dnf", "malformed", "excluded")
SCORABLE = ("ok", "malformed", "fail")          # the judge ran; DNF/timeout/excluded never score


class CandidateError(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    label: str
    spec: str
    backend: str
    variant: "str | None"

    def to_dict(self) -> dict:
        return {"label": self.label, "spec": self.spec, "backend": self.backend,
                "variant": self.variant}


# Bench-local presets so the plan's literal commands (§14/§24: `sol-med,sol-high`)
# resolve to the Test A/B seats (impl-panel sol #5). `label=spec` always wins.
_WINDOWS_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)}
                                  | {f"LPT{i}" for i in range(1, 10)})

RESERVED_LABELS = frozenset({"journal", "manifest.json", "adjudication.json", "report.md", ".lock", "raw"})

PRESETS = {
    "sol-med": "codex:gpt-5.6-sol:medium",
    "sol-high": "codex:gpt-5.6-sol:high",
    "grok-med": "grok:grok-4.6:medium",
    "grok-high": "grok:grok-4.6:high",
    # task 111: the two Gemini candidates for the experimental agy judge seat (the agy
    # model id carries the effort)
    "gem-flash-high": "agy:gemini-3.8-flash-high",
    "gem-pro-high": "agy:gemini-3.1-pro-high",
}


def parse_candidates(csv: str) -> list:
    """`label=provider:model:effort,…`, a bench preset (`sol-med`), or a bare spec
    (label = spec with ':'→'-'). Validated through production's
    `resolve_judge_spec` grammar; labels unique."""
    from provider.sandbox import resolve_judge_spec
    out, seen = [], set()
    for item in (s.strip() for s in (csv or "").split(",")):
        if not item:
            continue
        label, _, spec = item.rpartition("=") if "=" in item else ("", "", item)
        spec = spec.strip()
        if not label and spec in PRESETS:
            label, spec = spec, PRESETS[spec]
        try:
            backend, variant = resolve_judge_spec(spec)
        except ValueError as exc:
            raise CandidateError(f"candidate {spec!r}: {exc}") from exc
        label = (label.strip() or spec.replace(":", "-").replace("/", "_"))
        if not label.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise CandidateError(f"candidate label {label!r} must be [A-Za-z0-9._-]")
        # Labels become directories under the run dir (r3 sol #3 / grok #3): never a
        # control path, never dot-prefixed, never longer than a portable filename.
        if label.startswith(".") or len(label) > 64 or label.lower() in RESERVED_LABELS:
            raise CandidateError(f"candidate label {label!r} is reserved/unsafe as a directory name")
        # Portable path segment (r4 sol #5): Windows folds case, forbids device names and
        # trailing dots — enforce those rules on every platform so a run dir is portable.
        if label.endswith(".") or label.split(".")[0].upper() in _WINDOWS_DEVICE_NAMES:
            raise CandidateError(f"candidate label {label!r} is not a portable directory name")
        if label.casefold() in seen:
            raise CandidateError(f"duplicate candidate label {label!r} (labels are case-insensitive)")
        seen.add(label.casefold())
        out.append(Candidate(label=label, spec=spec, backend=backend, variant=variant))
    if not out:
        raise CandidateError("no candidates given")
    return out


@dataclass
class Invocation:
    status: str
    raw: str = ""
    usage: dict = field(default_factory=lambda: {"status": "unknown"})
    duration_ms: int = 0
    retries: int = 0
    findings: "scoring.ParsedFindings | None" = None
    note: str = ""
    attempts: list = field(default_factory=list)      # earlier (retried) attempts, never dropped

    def to_dict(self) -> dict:
        return {"status": self.status, "raw": self.raw, "usage": self.usage,
                "duration_ms": self.duration_ms, "retries": self.retries,
                "findings": self.findings.to_dict() if self.findings else None,
                "note": self.note, "attempts": list(self.attempts)}


# ── classification ───────────────────────────────────────────────────────────

# Provider quota / credit refusals, matched case-insensitively INSIDE a failure envelope only
# (`(FAILED …)` / `(error: …)`) — a review that merely discusses a "usage limit" is a review.
# codex-cli 0.151.0 prints "You've hit your usage limit." (+ " for <model>", "Upgrade to
# Plus/Pro", "purchase more credits"); grok Build answered HTTP 402 "Payment Required" in
# 2026-08. Extend here when a provider changes its wording — a miss degrades to `fail`.
# agy is NOT in this list (task 111): for an agy seat the harness does not read quota from the
# wording at all — `is_quota_exhausted` asks whether the agy ADAPTER named a quota stop (or the
# harness's own /quota pre-flight did). Both fired in that task's exam: the first halt came
# from a real refusal ("Individual quota reached … Resets in 34m13s"), the second from the
# pre-flight (no call spent).
QUOTA_SIGNATURES = (
    "you've hit your usage limit",
    "you have hit your usage limit",
    "usage limit reached",
    "usage_limit_reached",
    "payment required",
    "insufficient credits",
    "insufficient balance",
    "insufficient_quota",
    "out of credits",
    "credits depleted",
    "spend control reached",
)
QUOTA_NOTE = "quota exhausted — halt; --resume after the reset"

# Provider-side TRANSIENTS (task 050 W0b, owner decision after smoke1): the judge never
# reviewed, but the next call may well succeed — one retry now, `--resume` later, and the
# run does NOT halt. Matched inside a failure envelope only. codex-cli 0.151.0 prints
# "ERROR: Reconnecting... N/5" then "ERROR: Selected model is at capacity. Please try a
# different model." (smoke1, 2026-09-07). Quota (above) takes precedence.
TRANSIENT_SIGNATURES = (
    "at capacity",
    "reconnecting...",
    "service unavailable",
    "overloaded",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "bad gateway",
    "gateway timeout",
    "502 ", "503 ", "504 ", "529 ",
)
TRANSIENT_NOTE = "transient provider error — retried once; --resume re-runs it"

# agy ONLY (task 111), on top of the shared list, and matched only against the first line the agy
# adapter built (`_signature_text`). The shared list is searched in the WHOLE envelope of every other
# backend, so wording added for agy must not sit there: a failed codex review that merely quoted
# one of these would earn a retry (post-D6 run 3).
#   * exam, 2026-10-05: one call ended with "The stream was interrupted. Please continue the task
#     you were working on." and a partial `FINDINGS: NONE` — it had been scored `fail` (a
#     zero-finding review); a provider-side interruption, retried once from now on;
#   * the same exam's last flash call ended on `API error (attempt 1): UNAVAILABLE (code 503): The
#     service is currently unavailable.` — the shared `503 ` entry does not match (code in parentheses);
#   * a bare RESOURCE_EXHAUSTED / 429 with no quota wording is a rate limit (impl panel r2); a
#     quota stop is named by the adapter and wins.
_AGY_TRANSIENT_SIGNATURES = (
    "the stream was interrupted",
    "currently unavailable",
    "resource_exhausted",
)


# What the agy adapter itself writes as the first line of a failed seat, from agy's real
# error channels (stderr `error:` / `AGY_ERROR:` lines, the result event's error field):
_AGY_QUOTA_PREFIXES = ("(FAILED — agy quota exhausted", "(error: agy quota exhausted")   # adapter / pre-flight
_AGY_REPORTED_PREFIXES = ("(FAILED — agy reported: ",                       # exit != 0, agy's statement
                          "(FAILED — the judge CLI reported an error: ",    # exit 0, result.status != SUCCESS
                          "(FAILED — the agy turn reported an error: ")     # exit 0, SUCCESS, AGY_ERROR / step ERROR


def _agy_first_line(raw: str) -> str:
    return (raw or "").lstrip().split("\n", 1)[0]


def _signature_text(raw: str, backend=None) -> str:
    """The text a TRANSIENT signature may be matched against.

    Any backend but agy: the whole envelope, as before (codex and grok carry their wording in
    the stdout/stderr tails).

    agy (task 111): the seat's first line, and only when it is a line the adapter built from
    agy's own error channels (`agy reported: <agy's statement>` for a non-zero exit, `the judge
    CLI reported an error: <result.error>` for an exit-0 result that is not SUCCESS, `the agy turn
    reported an error: <AGY_ERROR line / the response step's error>` for an exit-0 SUCCESS whose
    turn agy still reported as failed — left out at first, so an outage reported that way was
    scored as a zero-finding review: post-D6 run 2). Every other first line can
    carry untrusted text — a malformed stream is QUOTED into its own failure line — and the rest
    of the envelope is untrusted altogether (partial review text; stdout tails in which text can
    imitate a `[stderr tail]` section). Three review rounds each reproduced an exam halted or
    retried by text the judge under test had written; the rule is a whitelist for that reason."""
    t = (raw or "").lstrip()
    if backend != "agy":
        return t
    first = _agy_first_line(t)
    return first if first.startswith(_AGY_REPORTED_PREFIXES) else ""


def _no_output_captured(t: str, backend=None) -> bool:
    """`format_judge_output`'s both-streams-empty marker. For agy it must be the line right
    under the `(FAILED — exit N)` header — anywhere else it is stdout text."""
    if backend != "agy":
        return "(no output captured)" in t
    return t.split("\n")[1:2] == ["(no output captured)"]


def is_quota_exhausted(raw: str, backend=None) -> bool:
    """True when a FAILURE envelope carries a provider quota/credit refusal.

    For agy the question is not put to the wording at all: the adapter decides what a quota stop
    is (`provider.usage.agy_failure_cause`, from agy's error channels) and says so in the seat's
    first line; the harness's own /quota pre-flight uses the same words. Nothing else is one."""
    t = (raw or "").lstrip()
    if not (t.startswith("(error:") or t.startswith("(FAILED")):
        return False
    if backend == "agy":
        return _agy_first_line(t).startswith(_AGY_QUOTA_PREFIXES)
    low = t.lower()
    return any(sig in low for sig in QUOTA_SIGNATURES)


def is_transient_provider_error(raw: str, backend=None) -> bool:
    """True when a FAILURE envelope carries a provider-side transient (capacity,
    reconnect exhaustion, 5xx, connection reset) and NOT a quota refusal."""
    t = (raw or "").lstrip()
    if not (t.startswith("(error:") or t.startswith("(FAILED")):
        return False
    if is_quota_exhausted(t, backend):
        return False
    low = _signature_text(t, backend).lower()
    sigs = TRANSIENT_SIGNATURES + _AGY_TRANSIENT_SIGNATURES if backend == "agy" else TRANSIENT_SIGNATURES
    return any(sig in low for sig in sigs)


def classify(raw: str, timed_out: bool = False, backend=None) -> tuple:
    """(status, retry_ok) over the real adapter envelope. Status is the spend
    enum from `tasks.review._judge_status` refined for the bench: a `(FAILED`
    with no output is a transport failure (dnf), and a clean review is `ok` or
    `malformed` depending on whether the FINDINGS block parses."""
    from tasks.review import _judge_status
    t = (raw or "").lstrip()
    if timed_out:
        return "timeout", False
    if t.startswith("(error:"):
        low = t.lower()
        if "timed out" in low:
            return "timeout", False
        if "not found on path" in low:
            return "dnf", False
        # The adapters' own size-cap envelopes are deterministic (r4 grok #2): retrying
        # the same oversized prompt can only fail again.
        if "argv element" in low:
            return "dnf", False
        if is_quota_exhausted(t, backend):
            return "dnf", False
        return "dnf", True
    if t.startswith("(FAILED"):
        if is_quota_exhausted(t, backend):
            return "dnf", False
        if _no_output_captured(t, backend):
            return "dnf", True
        if is_transient_provider_error(t, backend):
            return "dnf", True
        return "fail", False
    base = _judge_status(raw)
    if base != "ok":
        return base, False
    return "ok", False


def finish(raw: str, *, timed_out: bool, duration_ms: int, retries: int, backend=None) -> Invocation:
    from tasks.review import _parse_judge_usage
    status, _ = classify(raw, timed_out, backend)
    usage = _parse_judge_usage(raw) or {"status": "unknown"}
    parsed = None
    if status == "ok":
        parsed = scoring.parse_findings(raw)
        if parsed.status == "malformed":
            status = "malformed"
    note = ""
    if status == "dnf":
        if is_quota_exhausted(raw, backend):
            note = QUOTA_NOTE
        elif is_transient_provider_error(raw, backend):
            note = TRANSIENT_NOTE
    return Invocation(status=status, raw=raw or "", usage=usage, duration_ms=duration_ms,
                      retries=retries, findings=parsed, note=note)


# ── runners ──────────────────────────────────────────────────────────────────

class FakeRunner:
    """Scripted outputs; never touches an adapter. `script` maps
    `"<case-id>|<label>"` (or `"default"`) → {"status": ok|empty|malformed|timeout|dnf|fail|quota|transient,
    "findings": [{file, symbol, severity, why}], "raw": "...", "duration_ms": N}."""
    needs_tree = False

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.calls = []
        self._lock = threading.Lock()

    @staticmethod
    def render(entry: dict, backend=None) -> tuple:
        status = entry.get("status", "ok")
        if "raw" in entry:
            return entry["raw"], False
        if backend == "agy" and status in ("quota", "transient"):
            # an agy seat's failure is classified from the line the agy adapter writes (task 111),
            # so the scripted failure of an agy candidate has that shape — taken from the real exam
            if status == "quota":
                return ("(FAILED — agy quota exhausted: error: Individual quota reached. Please upgrade your "
                        "subscription to increase your limits. Resets in 34m13s. — `agy -p /quota` shows "
                        "when it resets)\n\n(FAILED — exit 3)"), False
            return ("(FAILED — agy reported: Eligibility check failed: UNAVAILABLE (code 503): The service "
                    "is currently unavailable.)\n\n(FAILED — exit 1)"), False
        if status == "ok":
            fs = entry.get("findings") or [{"file": "src/demo.py", "symbol": "demo",
                                             "severity": "Important", "why": "scripted finding"}]
            lines = ["Free-text review (fake).", "", "FINDINGS:"]
            for i, f in enumerate(fs, 1):
                lines += [f"{i}. FILE: {f.get('file', 'src/demo.py')}",
                          f"   SYMBOL: {f.get('symbol') or '-'}",
                          f"   SEVERITY: {f.get('severity', 'Important')}",
                          f"   WHY: {f.get('why', 'scripted')}"]
            lines.append("END FINDINGS")
            return "\n".join(lines) + "\n", False
        if status == "empty":
            return "Looks fine.\n\nFINDINGS:\nNONE\nEND FINDINGS\n", False
        if status == "malformed":
            return "I think the code is fine. No structured block here.\n", False
        if status == "timeout":
            return "(error: judge timed out)", True
        if status == "dnf":
            return "(error: fakecli not found on PATH)", False
        if status == "fail":
            return "(FAILED — exit 1)\n[stderr tail]\nboom", False
        if status == "transient":
            return ("(FAILED — exit 1)\n[stderr tail]\nERROR: Reconnecting... 3/5\nERROR: Selected model "
                    "is at capacity. Please try a different model."), False
        if status == "quota":
            return ("(FAILED — exit 1)\n[stderr tail]\nYou've hit your usage limit. Visit "
                    "https://chatgpt.com/codex/settings/usage to purchase more credits"), False
        raise ValueError(f"unknown fake status {status!r}")

    def invoke(self, case, candidate, package, tree, *, soft_timeout, hard_timeout) -> Invocation:
        with self._lock:
            self.calls.append((case.id, candidate.label))
        entry = self.script.get(f"{case.id}|{candidate.label}") or self.script.get("default") or {}
        raw, timed_out = self.render(entry, candidate.backend)
        # classified exactly as the live runner classifies it: by the candidate's backend
        return finish(raw, timed_out=timed_out, duration_ms=int(entry.get("duration_ms", 1234)),
                      retries=0, backend=candidate.backend)

    def preflight(self, candidates, package, repo_root):
        return {}


def _adapter_invoke(backend, variant, prompt, project_root, timeout_secs, budget_usd) -> str:
    """The production seam, verbatim in shape (tail-cert raw runner)."""
    from provider.subagent import _adapter_class
    try:
        adapter = _adapter_class(backend)(session_id="bench", project_root=Path(project_root))
        return adapter.run_headless_judge(prompt=prompt, model=variant, system_context="",
                                          web_search=False, timeout_secs=timeout_secs,
                                          budget_usd=budget_usd)
    except subprocess.TimeoutExpired:
        return "(error: bench judge timed out)"
    except Exception as exc:                       # spawn/resolution error → dnf envelope
        return f"(error: bench judge spawn failed: {exc})"


AGY_QUOTA_FLOOR = 0.03      # under 3% left in a bucket: do not start another agy call


def _read_agy_quota():
    from tasks.models_check import agy_quota
    return agy_quota()


class LiveRunner:
    """Real providers. `invoke`, `adapter_factory` and `quota_reader` are injectable for tests."""
    needs_tree = True

    def __init__(self, repo_root: Path, *, invoke=None, adapter_factory=None, budget_usd=None,
                 quota_reader=None):
        self.repo_root = Path(repo_root)
        # The agy /quota pre-flight (task 111). Injected in tests; the real reader only
        # when the real invoker is in use, so a test with a fake `invoke` never starts agy.
        self._quota_reader = quota_reader if quota_reader is not None else (
            _read_agy_quota if invoke is None else None)
        self._invoke = invoke or _adapter_invoke
        self._adapter_factory = adapter_factory
        self.budget_usd = budget_usd
        self.calls = []
        self._lock = threading.Lock()

    def _budget(self) -> str:
        if self.budget_usd is not None:
            return str(self.budget_usd)
        try:
            from tasks.core import resolve_judge_budget
            return resolve_judge_budget(self.repo_root)
        except Exception:
            return "10"

    def _adapter(self, candidate, project_root):
        if self._adapter_factory:
            return self._adapter_factory(candidate.backend, project_root)
        from provider.subagent import _adapter_class
        return _adapter_class(candidate.backend)(session_id="bench", project_root=Path(project_root))

    def preflight(self, candidates, package, repo_root) -> dict:
        """label → error string for candidates whose transport cannot carry the
        prompt; {} when all fit. One decision, shared with `corpus validate --transport`
        (bench/lib/transport.py) so the report and the run can never disagree."""
        from bench.lib import transport as _transport
        return _transport.preflight_errors(candidates, package.prompt, repo_root,
                                           adapter_factory=self._adapter_factory,
                                           budget_root=self.repo_root)      # the BENCH repo's policy, not the case's

    def _agy_quota_guard(self, candidate) -> str:
        """'' to go ahead, or a quota envelope when the agy bucket this candidate draws
        on is (nearly) empty. Read from `agy -p /quota` — no model turn — so the run
        HALTS on what agy reports rather than on an error wording nobody has captured
        (task 111, plan panel P1/P10). An unreadable quota never blocks a call."""
        if candidate.backend != "agy" or self._quota_reader is None:
            return ""
        try:
            buckets = self._quota_reader()
        except Exception:
            return ""
        prefix = "gemini-" if str(candidate.variant or "gemini").startswith("gemini") else "3p-"
        for b in buckets or []:
            try:
                mine = str(b.get("id", "")).startswith(prefix)
                frac = float(b.get("remaining_fraction"))
            except (AttributeError, TypeError, ValueError):
                continue
            if mine and frac < AGY_QUOTA_FLOOR:
                return (f"(error: agy quota exhausted: {frac * 100:.1f}% left in the {b.get('group', '?')} "
                        f"{b.get('window', '?')} bucket (floor {AGY_QUOTA_FLOOR * 100:.0f}%), resets "
                        f"{b.get('reset_time', '?')} — judgebench pre-flight from `agy -p /quota`; "
                        f"no model turn was spent)")
        return ""

    def invoke(self, case, candidate, package, tree, *, soft_timeout, hard_timeout) -> Invocation:
        with self._lock:
            self.calls.append((case.id, candidate.label))
        guard = self._agy_quota_guard(candidate)
        if guard:
            return finish(guard, timed_out=False, duration_ms=0, retries=0, backend=candidate.backend)
        retries = 0
        t0 = time.monotonic()
        raw = self._invoke(candidate.backend, candidate.variant, package.prompt, tree,
                           hard_timeout, self._budget())
        status, retry_ok = classify(raw, backend=candidate.backend)
        attempts = []
        if retry_ok:
            # Keep the first attempt on record (r2 sonnet #2): it may have been billed
            # even though its output looked like a transport failure.
            from tasks.review import _parse_judge_usage
            attempts.append({"status": status, "usage": _parse_judge_usage(raw) or {"status": "unknown"},
                             "raw_head": (raw or "")[:300], "raw": raw or "",
                             "duration_ms": int((time.monotonic() - t0) * 1000)})
            retries = 1
            t0 = time.monotonic()                      # latency = the FINAL attempt only (r3 opus F4)
            raw = self._invoke(candidate.backend, candidate.variant, package.prompt, tree,
                               hard_timeout, self._budget())
        duration_ms = int((time.monotonic() - t0) * 1000)
        inv = finish(raw, timed_out=False, duration_ms=duration_ms, retries=retries,
                     backend=candidate.backend)
        inv.attempts = attempts
        return inv


# ── one case × N candidates ──────────────────────────────────────────────────

def run_case(case, candidates, runner, package, *, source_repo=None, soft_timeout=900,
             hard_timeout=1200, concurrency=2, skip=frozenset(), snapshot_parent=None,
             on_result=None):
    """Invoke every candidate not in `skip` against one case. Builds ONE snapshot
    (live runners only), runs candidates with at most `concurrency` in flight,
    and tears the snapshot down after the last finishes. `on_result(cand, inv, tree)`
    fires for each candidate AS IT COMPLETES, inside the snapshot (r4 grok #3: the
    caller persists immediately, so a crash loses at most the candidates still in
    flight, never a whole case). Returns [(candidate, Invocation)] in candidate
    order. Never raises for a provider failure — that is a `dnf` result."""
    todo = [c for c in candidates if c.label not in skip]
    if not todo:
        return []
    try:
        pre = runner.preflight(todo, package, source_repo or REPO_ROOT) or {}
    except Exception as exc:                       # r3 sonnet #2: contain a raising preflight
        out = [(c, Invocation(status="dnf", raw=f"(error: preflight raised {type(exc).__name__}: {exc})",
                              note="preflight raised")) for c in todo]
        if on_result is not None:
            for c, inv in out:
                on_result(c, inv, None)
        return out
    if pre:
        why = "; ".join(f"{k}: {v}" for k, v in sorted(pre.items()))
        out = [(c, Invocation(status="excluded", note=f"transport preflight — {why}")) for c in todo]
        if on_result is not None:
            for c, inv in out:
                on_result(c, inv, None)
        return out

    def _one(cand, tree):
        try:
            return runner.invoke(case, cand, package, tree, soft_timeout=soft_timeout,
                                 hard_timeout=hard_timeout)
        except Exception as exc:                   # a runner bug must not abort the run
            return Invocation(status="dnf", raw=f"(error: runner raised {type(exc).__name__}: {exc})",
                              note="runner exception")

    def _all(tree):
        from concurrent.futures import as_completed
        results = {}
        with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(todo)))) as ex:
            futs = {ex.submit(_one, c, tree): c for c in todo}
            for f in as_completed(futs):
                c = futs[f]
                inv = f.result()
                results[c.label] = inv
                if on_result is not None:
                    try:
                        on_result(c, inv, tree)
                    except Exception as exc:               # persistence bug must not kill the run
                        inv.note = (inv.note + "; " if inv.note else "") + f"on_result raised {exc}"
        return [(c, results[c.label]) for c in todo]

    if getattr(runner, "needs_tree", False):
        repo = Path(source_repo) if source_repo else REPO_ROOT
        try:
            with snapshot_tree(repo, case.repo_base_sha, parent_dir=snapshot_parent) as tree:
                return _all(tree)
        except Exception as exc:                   # snapshot failure → every candidate dnf
            out = [(c, Invocation(status="dnf", raw=f"(error: snapshot failed: {exc})",
                                  note="snapshot")) for c in todo]
            if on_result is not None:
                for c, inv in out:
                    on_result(c, inv, None)
            return out
    return _all(None)
