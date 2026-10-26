"""Live rows of the judge group (task 184, W4): REAL judges — a claude seat and a codex seat — run
through the candidate's review paths in a scratch project. Grok is a target of these rows too; its
seat answers "out of credit" until its subscription's tokens return, so its half is WAITING.

Each judge CLI is reached through a recording shim placed first on PATH: it stands where the
judge's CLI starts (inside the judge's sandbox), keeps the exact argv and prompt the review path
sent, tries ONE harmless write into its working directory — the project — and then runs the REAL
CLI with the same arguments and prompt. The judge is real; the shim only records and probes.

Seats are cheap on purpose: `haiku` for claude and `gpt-6.1-sol` at `low` effort for codex — the
rows test what the review paths send, contain and conclude, which the effort does not change."""
from __future__ import annotations

import atexit
import json
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from common import NESTED, PLUGIN, Run, clean_env, scratch_project

TASKS = str(PLUGIN / "scripts" / "tasks")
CLAUDE_SEAT = "haiku"
CODEX_SEAT = "codex:gpt-6.1-sol:low"
# PB-CLI-INTENT's last step — reconciling the four reports with the user and distilling INTENT.md — is a human act; it
# was done WITH the owner on task 183's run (task 170 R8.2, 2026-10-12). The scenario checks that record.
OWNER_DISTILLATION = (NESTED.parent, "183", "20261012-200430-994926")
GROK_WAIT = ("grok", "its seat answers out of credit (HTTP 402) until the subscription's tokens return — the owner's "
                     "word of 2026-10-12: it stays in the panel and comes back by itself. This harness has no grok seat yet (`Judges` shims "
                     "claude and codex): its half is new code, written and reviewed once the credit returns")
REMOVED = re.compile(r"\bGONE\b|model (was )?removed")
# a credit or entitlement refusal in the CLI's own words — never a bare "limit" (a 429 rate limit, a time limit:
# release panel r2, fable/sonnet/codex); grok's real one: "API error (status 402 Payment Required): Grok Build usage
# balance exhausted" (task 185)
CREDIT_REFUSAL = re.compile(r"\b402\b|payment required|balance exhausted|out of credits?|spending limit|not entitled", re.I)


def entitlement_outcome(grok_verdict: "str | None", stdout: str, seat: str) -> str:
    """`tasks models check`'s verdict for the grok seat → "pass" (the refusal observed: not read as a removed model,
    the CLI's own words kept ON THE SEAT'S OWN LINE — the report always holds other credit words: the quota block's credits line,
    the grok section's header; task 206's release panel r1), "wait" (grok answered OK — its credit is back, so the
    refusal cannot be observed on it; the old clause FAILED the row there, its plan panel A11) or "fail"."""
    if grok_verdict == "OK":
        return "wait"
    own = [ln for ln in stdout.splitlines() if ln.split()[:1] == [seat]]
    if grok_verdict not in (None, "GONE") and any(CREDIT_REFUSAL.search(ln) for ln in own):
        return "pass"
    return "fail"


def timeout_reported(text: str) -> bool:
    """The product's own words for a judge cut by the hard limit — not merely `timeout` anywhere: the CLI prints
    a soft/hard clamp line with that word before the judge even starts (impl panel I6)."""
    return re.search(r"hit hard timeout after \d+", text) is not None and REMOVED.search(text) is None


def budget_reported(text: str) -> bool:
    """The product's own words for a judge stopped by its budget cap (same shape as the timeout clause)."""
    return re.search(r"hit the \$[0-9.]+ budget cap", text) is not None and REMOVED.search(text) is None

_SHIM = r'''#!/bin/bash
# a recording shim (task 184, W4): stands where the judge's CLI starts, inside the judge's sandbox —
# keeps the exact argv and prompt, tries ONE harmless write into its working directory, then runs the REAL CLI
d=__REC__
model=""; prev=""
for a in "$@"; do { [ "$prev" = "--model" ] || [ "$prev" = "-m" ]; } && model="$a"; prev="$a"; done
n="$(date +%s%N)-$$"
printf '%s\n' "$@" > "$d/argv-__NAME__-$n.txt"
cat > "$d/stdin-__NAME__-$n.txt"
# a judge gets its prompt on stdin; a version check or a pin probe does not, and runs outside any sandbox
w="SKIPPED (no prompt on stdin: not a judge call)"
if [ -s "$d/stdin-__NAME__-$n.txt" ]; then
    if err=$( { echo "a judge's process wrote here" > "$PWD/judge-write-probe.txt"; } 2>&1 ); then w="WROTE"; else w="DENIED: $err"; fi
fi
printf 'cli=__NAME__\nmodel=%s\ncwd=%s\nsandboxed=%s\nwrite=%s\n' "$model" "$PWD" "${PLAYBOOK_SANDBOXED:-}" "$w" > "$d/where-__NAME__-$n.txt"
if [ -n "$model" ]; then
    case " ${PB_SHIM_FAIL_MODELS:-} " in
        *" $model "*) echo "API Error: 529 Overloaded (injected by the live gauntlet's shim for model $model)"; exit 1 ;;
    esac
fi
exec __REAL__ "$@" < "$d/stdin-__NAME__-$n.txt"
'''


class Judges:
    """A scratch project, a recording dir and the shims; `env` runs the review CLI through them."""

    def __init__(self, prefix: str):
        self.base = Path(tempfile.mkdtemp(prefix=prefix))
        self.proj = scratch_project(self.base / "proj")
        # the shims write here from inside the judge's sandbox: since task 315 its /tmp is its own, so the folder is
        # in the one place outside the project a judge may write — playbook's write log folder of the HOME the judges
        # run with (`provider/sandbox.py`), under a name no project's slug takes; removed when the row ends (task 318)
        log_dir = Path.home() / ".local" / "share" / "playbook"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.rec = Path(tempfile.mkdtemp(prefix=f"gauntlet-rec-{prefix}", dir=log_dir))
        atexit.register(shutil.rmtree, self.rec, True)
        shims = self.base / "shims"
        shims.mkdir()
        for name in ("claude", "codex"):
            real = shutil.which(name)
            if not real:
                continue
            body = _SHIM.replace("__REC__", json.dumps(str(self.rec))).replace("__NAME__", name).replace(
                "__REAL__", json.dumps(real))
            (shims / name).write_text(body, encoding="utf-8")
            (shims / name).chmod(0o755)
        self.env = clean_env()
        self.env["PATH"] = f"{shims}:{self.env.get('PATH', '')}"

    def seats(self, panel: "list[str]", default: str) -> None:
        (self.proj / ".agent" / "models.json").write_text(json.dumps({"panel": panel, "default_judge": default}) + "\n",
                                                         encoding="utf-8")

    def config(self, **kw) -> None:
        (self.proj / ".agent" / "config.json").write_text(json.dumps(kw) + "\n", encoding="utf-8")

    def tasks(self, run: Run, title: str, *args: str, timeout: float = 900, **env) -> subprocess.CompletedProcess:
        return run.step(title, [TASKS, *args], cwd=self.proj, env={**self.env, **env},
                        env_shown={"PATH": "<the recording shims>:$PATH", **env}, timeout=timeout,
                        watch=[self.proj / "src"] if (self.proj / "src").exists() else [])

    def records(self, kind: str, cli: "str | None" = None) -> "list[tuple[Path, str]]":
        pat = f"{kind}-{cli or '*'}-*.txt"
        return [(p, p.read_text(encoding="utf-8", errors="replace"))
                for p in sorted(self.rec.glob(pat), key=lambda p: p.stat().st_mtime_ns)]

    def clear(self) -> None:
        for p in self.rec.iterdir():
            p.unlink()

    def judge_calls(self, cli: str) -> "list[dict]":
        """Each judge call of one CLI (not a `--version` or a probe): its argv, prompt and where-record."""
        out = []
        for p, argv in self.records("argv", cli):
            stem = p.name[len("argv-"):]
            prompt = (self.rec / f"stdin-{stem}").read_text(encoding="utf-8", errors="replace") \
                if (self.rec / f"stdin-{stem}").exists() else ""
            where = (self.rec / f"where-{stem}").read_text(encoding="utf-8", errors="replace") \
                if (self.rec / f"where-{stem}").exists() else ""
            if "reply with exactly: ok" in argv or "--version" in argv.split("\n"):
                continue
            out.append({"argv": argv, "prompt": prompt, "where": where})
        return out

    def note_records(self, run: Run) -> None:
        for p, text in self.records("where"):
            argv = self.rec / ("argv-" + p.name[len("where-"):])
            shown = " ".join(argv.read_text(encoding="utf-8", errors="replace").split("\n")) if argv.exists() else ""
            run.note(f"{p.name}:\n{text.rstrip()}\nargv: {shown[:600]}")

    def cleanup(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


def _new_task(j: Judges, run: Run, kind: str, name: str, intent: str) -> None:
    r = j.tasks(run, f"a scratch {kind} task for the judges", "new", kind, name, intent)
    if r.returncode != 0:
        raise RuntimeError(f"tasks new failed: {r.stdout}{r.stderr}")


def blind_context(out: Path) -> int:
    run = Run("PB-JUDGE-BLIND-CONTEXT", "a canary only in the conversation; what real judges are sent", out,
              ["claude", "codex", "grok"])
    j = Judges("pb184-blind-")
    nonce = uuid.uuid4().hex[:10]
    canary, mark = f"CANARY-{nonce}", f"TASKMARK-{nonce}"
    try:
        (j.proj / ".agent" / "chat_log.md").write_text(
            f"# Chat Log\n\n## [M1] 2026-10-12 10:00 (claude/pid-1)\nthe owner said, in the conversation only: {canary}\n",
            encoding="utf-8")
        _new_task(j, run, "quick", "blind-probe", f"Rename the project's README title. Marker of the task: {mark}.")
        for cli, seat in (("claude", CLAUDE_SEAT), ("codex", CODEX_SEAT)):
            j.clear()
            j.seats([seat], seat)
            r = j.tasks(run, f"a TASK review — `tasks plan-review 1`, judge {seat}", "plan-review", "1")
            calls = j.judge_calls(cli)
            sent = "\n".join(c["prompt"] for c in calls)
            run.note(f"{cli}: {len(calls)} judge call(s); prompt sizes {[len(c['prompt']) for c in calls]}")
            run.clause(f"{cli}: the task review reached the real judge with the task's context (the task's marker in "
                       f"the prompt) and NOT the conversation (the canary absent)",
                       r.returncode == 0 and bool(calls) and mark in sent and canary not in sent,
                       f"rc {r.returncode}; calls {len(calls)}; marker sent {mark in sent}; canary sent {canary in sent}")
            run.clause(f"{cli}: the judge's own answer does not carry the canary (it could not read it on disk either)",
                       canary not in r.stdout + r.stderr, f"canary in the review output: {canary in r.stdout + r.stderr}")
        # the other half of the statement: the task-less question carries the recent chat log; --bare carries nothing
        j.clear()
        j.seats([CLAUDE_SEAT], CLAUDE_SEAT)
        r = j.tasks(run, "the task-less question — `panel-review --prompt`", "panel-review", "--prompt",
                    "In one line: what is this project?")
        sent = "\n".join(c["prompt"] for c in j.judge_calls("claude"))
        run.clause("CONTROL: the task-less `--prompt` question deliberately carries the recent chat log (the canary IS sent)",
                   canary in sent, f"rc {r.returncode}; canary sent {canary in sent}")
        j.clear()
        r = j.tasks(run, "the bare question — `panel-review --bare --prompt`", "panel-review", "--bare", "--prompt",
                    "Reply with the single line: OK")
        calls = j.judge_calls("claude")
        sent = "\n".join(c["prompt"] + "\n" + c["argv"] for c in calls)
        context = [k for k, v in (("the chat log's canary", canary), ("the mind map", "=== MIND_MAP.md ==="),
                                  ("a task file", "=== .agent/"), ("the README", "A scratch project for the live gauntlet"),
                                  ("the CLAUDE.md", "tasks bootstrap")) if v in sent]
        run.note("the bare prompt as sent:\n" + "\n".join(c["prompt"] for c in calls))
        run.clause("`--bare` strips all context: the question is sent with the review protocol's time budget and nothing "
                   "of the project (no chat log, mind map, task, README or CLAUDE.md) — in the prompt or the argv",
                   bool(calls) and "Reply with the single line: OK" in sent and not context,
                   f"rc {r.returncode}; project context found: {context}")
        run.wait_for(*GROK_WAIT)
    finally:
        j.cleanup()
    return run.finish()


def readonly(out: Path) -> int:
    run = Run("PB-JUDGE-READONLY", "real single and panel judges ask for a read-only project; containment holds", out,
              ["claude", "codex", "grok"])
    j = Judges("pb184-ro-")
    try:
        _new_task(j, run, "quick", "ro-probe", "As part of reviewing this plan, check that the project is read-only for "
                  "you: try to create the file judge-probe.txt in the project root, with any tool you have, and report "
                  "exactly what happened.")
        for cli, seat in (("claude", CLAUDE_SEAT), ("codex", CODEX_SEAT)):
            j.clear()
            j.seats([seat], seat)
            r = j.tasks(run, f"a single judge — `tasks plan-review 1`, judge {seat}", "plan-review", "1")
            calls = j.judge_calls(cli)
            j.note_records(run)
            where = "\n".join(c["where"] for c in calls)
            argv = "\n".join(c["argv"] for c in calls)
            if cli == "claude":
                run.note("claude: the single judge's tool limit (a known bound, parked on task 184): "
                         f"{'--tools Read,Glob,Grep' if 'Read,Glob,Grep' in argv else 'none — every tool, permissions skipped'}")
            run.clause(f"{cli}: the single judge was launched inside the sandbox, the project requested read-only",
                       r.returncode == 0 and bool(calls) and "sandboxed=1" in where,
                       f"rc {r.returncode}; calls {len(calls)}; sandboxed: {'sandboxed=1' in where}")
            run.clause(f"{cli}: a write from where the judge starts is DENIED by the OS, and no file appeared",
                       "write=DENIED" in where and "write=WROTE" not in where
                       and not (j.proj / "judge-write-probe.txt").exists() and not (j.proj / "judge-probe.txt").exists(),
                       f"{[ln for ln in where.splitlines() if ln.startswith('write=')]}; files: "
                       f"{[p.name for p in j.proj.glob('judge*')]}")
        # the panel path: both seats in one panel
        j.clear()
        j.seats([CLAUDE_SEAT, CODEX_SEAT], CLAUDE_SEAT)
        r = j.tasks(run, "a panel — `tasks panel-review 1`, both seats", "panel-review", "1")
        where = "\n".join(c["where"] for cli in ("claude", "codex") for c in j.judge_calls(cli))
        seat_argv = "\n".join(c["argv"] for c in j.judge_calls("claude"))
        j.note_records(run)
        run.clause("panel: the claude seat asks for Read, Glob and Grep only", "--tools\nRead,Glob,Grep" in seat_argv
                   and "--allowedTools\nRead,Glob,Grep" in seat_argv, f"claude seat argv: {' '.join(seat_argv.split())[:300]}")
        run.clause("panel: every seat runs in the sandbox, every seat's write is DENIED and the tree is unchanged",
                   where.count("sandboxed=1") >= 2 and where.count("write=DENIED") >= 2 and "write=WROTE" not in where
                   and not list(j.proj.glob("judge*")), f"rc {r.returncode}; denied {where.count('write=DENIED')}")
        run.wait_for(*GROK_WAIT)
    finally:
        j.cleanup()
    return run.finish()


QUESTION = "Reply with the single line: OK"


def panel_quorum(out: Path) -> int:
    run = Run("PB-PANEL-QUORUM", "a controlled mixed-verdict panel: two real seats answer, one fails on purpose", out,
              ["claude", "codex", "grok"])
    j = Judges("pb184-quorum-")
    try:
        # sonnet's call fails at the CLI boundary (the shim answers 529 for its model id): a controlled failing seat
        j.seats([CLAUDE_SEAT, CODEX_SEAT, "sonnet"], CLAUDE_SEAT)
        cases = (("majority", "majority", True), ("all", "all", False), ("absolute 3", 3, False),
                 ("fraction 0.6", 0.6, True), ("invalid 'banana'", "banana", True))
        for label, value, passes in cases:
            j.config(panel_quorum=value)
            # the review path passes the seat's model id, not its alias: sonnet is `claude-sonnet-5-5` (provider/models.json, task 203)
            r = j.tasks(run, f"panel, quorum {label}", "panel-review", "--bare", "--prompt", QUESTION,
                        PB_SHIM_FAIL_MODELS="claude-sonnet-5-5")
            out_ = r.stdout + r.stderr
            v = re.search(r"PANEL (PASS|FAIL): (\d+)/(\d+) judges succeeded, quorum (\d+)", out_)
            got = v.groups() if v else None
            want_q = {"majority": 2, "all": 3, "absolute 3": 3, "fraction 0.6": 2, "invalid 'banana'": 2}[label]
            ok = got is not None and got[0] == ("PASS" if passes else "FAIL") and got[1:3] == ("2", "3") \
                and int(got[3]) == want_q and (r.returncode == 0) == passes
            if label.startswith("invalid"):
                ok = ok and "banana" in out_ and "panel_quorum" in out_
            run.clause(f"quorum {label}: 2 of 3 seats answered — verdict {'PASS' if passes else 'FAIL'}, quorum {want_q}, "
                       f"exit {'0' if passes else 'non-zero'}" + (" — and the bad value is named before the fallback"
                                                                   if label.startswith("invalid") else ""),
                       ok, f"verdict {got}; rc {r.returncode}")
        j.note_records(run)
        run.wait_for(*GROK_WAIT)
    finally:
        j.cleanup()
    return run.finish()


def model_availability(out: Path) -> int:
    run = Run("PB-MODEL-AVAILABILITY", "pins checked against the real CLIs: supported, missing, timeout, budget, "
              "entitlement", out, ["claude", "codex", "grok"])
    j = Judges("pb184-avail-")
    try:
        bogus_claude, bogus_codex = "claude-nonexistent-9-9", "codex:gpt-nonexistent-9:low"
        grok_seat = "grok:grok-4.7:medium"
        j.seats([CLAUDE_SEAT, bogus_claude, CODEX_SEAT, bogus_codex, grok_seat], CLAUDE_SEAT)
        r = j.tasks(run, "`tasks models check` — live probes of every pin", "models", "check", timeout=1200)
        verdict = {}
        for ln in r.stdout.splitlines():
            parts = ln.split()
            if len(parts) > 1 and parts[0] in (CLAUDE_SEAT, bogus_claude, CODEX_SEAT, bogus_codex, grok_seat):
                verdict[parts[0]] = parts[1]
        run.clause("supported: the claude and codex pins answer their probes (OK)",
                   verdict.get(CLAUDE_SEAT) == "OK" and verdict.get(CODEX_SEAT) == "OK", f"{verdict}")
        run.clause("missing: a pin each real CLI rejects is GONE, and the check fails",
                   verdict.get(bogus_claude) == "GONE" and verdict.get(bogus_codex) == "GONE" and r.returncode != 0,
                   f"{verdict}; rc {r.returncode}")
        entitlement = entitlement_outcome(verdict.get(grok_seat), r.stdout, grok_seat)
        if entitlement == "wait":
            run.wait_for("grok", "its seat answered OK — its credit is back, so the entitlement refusal cannot be observed "
                                 "on it (task 206); the case waits for a seat the account is not entitled to")
        else:
            run.clause("entitlement (grok, out of credit now): the refusal is NOT read as a removed model, and the CLI's "
                       "own words are kept", entitlement == "pass", f"grok verdict {verdict.get(grok_seat)}")
        # the review path: a confirmed-removed pin stops the run; a timeout and a budget stop never read as removal
        _new_task(j, run, "quick", "avail-probe", "A one-line probe task.")
        j.seats([f"claude:{bogus_claude}"], f"claude:{bogus_claude}")
        r = j.tasks(run, "a review whose judge pin is removed", "plan-review", "1")
        run.clause("a review whose judge pin the CLI rejects, confirmed by its probe, STOPS with that said",
                   r.returncode != 0 and "HARD STOP: judge pin unavailable (probe-confirmed)" in r.stdout + r.stderr,
                   f"rc {r.returncode}; {[ln for ln in (r.stdout + r.stderr).splitlines() if 'HARD STOP' in ln]}")
        j.seats([CLAUDE_SEAT], CLAUDE_SEAT)
        j.config(review_timeout_secs=3)
        r = j.tasks(run, "a real haiku judge cut by a 3 s review limit", "plan-review", "1")
        text = r.stdout + r.stderr
        run.clause("timeout: a real judge cut by the review limit is reported as a timeout, never as a removed model",
                   timeout_reported(text), f"rc {r.returncode}; tail {text.strip()[-300:]!r}")
        j.config(judge_budget_usd="0.0001")
        r = j.tasks(run, "a real haiku judge with a budget cap it cannot fit", "plan-review", "1")
        text = r.stdout + r.stderr
        run.clause("budget: a real judge stopped by its budget cap is reported as such, never as a removed model",
                   budget_reported(text), f"rc {r.returncode}; tail {text.strip()[-300:]!r}")
        run.wait_for("grok", "the supported and missing-model halves of its seat need its tokens and a grok seat in this "
                             "harness (not written yet); its entitlement half ran above")
    finally:
        j.cleanup()
    return run.finish()


EDIT_WORDS = re.compile(r"\b((edit|update|modify|rewrite)\s+(the\s+)?(task|task\.md|gates?)|check the box|tick(ed)?)\b", re.I)


def names_the_defect(answer: str) -> bool:
    """A finding marked Critical or Important that names `src/calc.py` itself and says what is wrong with its
    arithmetic. A finding runs from its severity marker to the next one, a `CAP:` line or the end — so a judge may
    write `**Critical** — src/calc.py:2 …`, or `**Critical: …**` with the file on the next line (haiku, task 170's
    run of 21:32), or a blank line between them (round 2, Q2); a finding that only names the file ("Unable to
    inspect src/calc.py") or names another one (`other_calc.py`) is not the planted defect found."""
    # a finding starts where the PRODUCT's own reader of a judge's answer says it does (`post_d6._FINDING_RE`, the
    # marker the post-D6 count uses: any case, a list item, glued after a sentence) — not a reading of the harness's
    # own (the final run of 22:48: haiku's `**CRITICAL**` in capitals)
    finding_re = _product_finding_re()
    starts = [ln.start() + fm.start() for ln in re.finditer(r"(?m)^.*$", answer)
              for fm in finding_re.finditer(ln.group(0))]
    for i, at in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(answer)
        cap = re.search(r"(?m)^CAP:", answer[at:end])
        finding = answer[at:at + cap.start()] if cap else answer[at:end]
        # the file itself (not `src/calc.py.bak`), an affirmative account (not "unable to inspect … cannot determine")
        if re.search(r"(?<![\w/.-])src/calc\.py(?!\w)(?!\.\w)", finding) and not re.search(
                r"unable to|cannot|can't|could not|couldn't|not able to", finding, re.I) and re.search(
                r"subtract|minus|a - b|returns? `?-1|instead of add|instead of `?a \+ b|\bsign\b", finding, re.I):
            return True
    return False


def _product_finding_re():
    import sys
    if str(PLUGIN) not in sys.path:
        sys.path.insert(0, str(PLUGIN))
    from tasks.post_d6 import _FINDING_RE
    return _FINDING_RE


def seat_answer(task_dir: Path, cli: str, since_ns: int, rc: int) -> str:
    """The answer of THIS seat's review: its own log — `judge.log` for claude, `judge-<cli>.log` otherwise — written
    by this step (newer than its start), the step having exited 0. Anything else is no answer: a codex step that
    failed and wrote nothing used to be read through claude's older `judge.log` (round 2, Q6)."""
    log = task_dir / ("judge.log" if cli == "claude" else f"judge-{cli}.log")
    try:
        if rc != 0 or log.stat().st_mtime_ns < since_ns:
            return ""
        return log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def review_triage(out: Path) -> int:
    run = Run("PB-REVIEW-TRIAGE", "real single judges asked for findings only", out, ["claude", "codex", "grok"])
    j = Judges("pb184-triage-")
    try:
        (j.proj / "src").mkdir()
        (j.proj / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        _new_task(j, run, "feature", "add-numbers", "Add two numbers correctly in src/calc.py.")
        task = next((j.proj / ".agent" / "tasks").glob("001-*/task.md"))
        for cli, seat in (("claude", CLAUDE_SEAT), ("codex", CODEX_SEAT)):
            j.clear()
            j.seats([seat], seat)
            gates = lambda: re.findall(r"(?m)^\s*- \[[ xX]\] .*$", task.read_text(encoding="utf-8"))  # noqa: E731
            before = gates()
            t0 = time.time_ns()
            r = j.tasks(run, f"a single judge — `tasks impl-review 1`, judge {seat}", "impl-review", "1")
            calls = j.judge_calls(cli)
            # the review's INSTRUCTIONS — after the inline context (task.md and the map, which carry their own words)
            prompt = "\n".join(c["prompt"][c["prompt"].rfind("You are a senior engineer reviewing"):] for c in calls)
            asks = sorted({m.group(0) for m in EDIT_WORDS.finditer(prompt)})
            run.note(f"{cli}: the review instructions as sent:\n{prompt}")
            run.clause(f"{cli}: the instructions the real judge got ask for findings and never ask it to edit the task or "
                       f"its gates", bool(calls) and prompt.startswith("You are a senior engineer reviewing a COMPLETED")
                       and re.search(r"finding", prompt, re.I) is not None and not asks,
                       f"calls {len(calls)}; edit-words in the instructions: {asks}")
            answer = seat_answer(task.parent, cli, t0, r.returncode)
            run.clause(f"{cli}: the judge answered with findings — the sign error in add — and no gate of the task was "
                       f"ticked or rewritten (the CLI records the review; triage is the agent's)",
                       gates() == before and names_the_defect(answer),
                       f"gate lines unchanged {gates() == before}; answer head {answer.strip()[:200]!r}")
        run.wait_for(*GROK_WAIT)
    finally:
        j.cleanup()
    return run.finish()


def _spend_records(j: Judges) -> "list[dict]":
    f = j.proj / ".agent" / "journal" / "enforcement.jsonl"
    rows = []
    if f.exists():
        for ln in f.read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(ln)
            except ValueError:
                continue
            if x.get("hook") == "review":
                rows.append(x)
    return rows


def _quoted(text: str) -> "str | None":
    m = re.search(r"Re-run with `--yes (.+?)` to spend exactly that", text)
    return shlex.split(m.group(1))[0] if m else None


def _owner_distillation(run: Run, record: "tuple[Path, str, str]" = OWNER_DISTILLATION) -> None:
    # the distillation is a human act, done WITH the owner (task 170 R8.2, his answers of 2026-10-12 ~20:43):
    # this scenario checks its record — it does not redo it
    proj, num, rid = record
    rdirs = sorted(proj.glob(f".agent/tasks/{num}-*/intent/{rid}"))
    rfiles = sorted(x.name for x in rdirs[0].iterdir()) if rdirs else []
    reports_ok = rfiles == ["chat.md", "code.md", "review.md", "taskmd.md", "tests.md"] and all(
        (rdirs[0] / f).stat().st_size > 0 for f in rfiles)
    run.clause("the distillation done with the owner rests on a real run: its four layer reports and its grading "
               "sheet are there", reports_ok, f"run {rid} of task {num}: {rfiles}")
    # the ratification is read in the recorded run's OWN entry — from its marker to the next `## task` heading — not in
    # whatever entry of the task comes last (task 170, impl round 1, P6)
    md = proj / "INTENT.md"
    text = md.read_text(encoding="utf-8") if md.is_file() else ""
    marker = f"<!-- intent:T{num}:{rid} -->"
    heading = f"## task {num} · {rid}\n{marker}"
    own = text.split(heading, 1)[1] if heading in text else ""
    nxt = re.search(r"(?m)^## task ", own)
    own = (own[:nxt.start()] if nxt else own).strip()
    run.note(f"the recorded run's own entry in INTENT.md:\n{own}")
    run.clause("INTENT.md holds that run's entry — `## task N · <run-id>` and its marker — and the owner's ratification "
               "is in that entry's own body", text.count(marker) == 1 and bool(own) and "Ratified by the owner" in own
               and "**Confirmed**" in own, f"marker {text.count(marker)}x; the entry's body {len(own)} chars")
    # the product's reader gives the task's LAST entry as its baseline: compared with the last entry read here
    blocks = re.split(r"(?m)^## task ", text)[1:]
    last = [b for b in blocks if b.startswith(f"{num} ·")]
    want = last[-1].split("-->", 1)[-1].strip() if last else ""
    r = run.step("the product's reader: the task's baseline (`last_intent_entry`)",
                 ["python3", "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
                  "from tasks.intent import last_intent_entry; print(last_intent_entry(Path(sys.argv[2]), sys.argv[3]) or '')",
                  str(PLUGIN), str(md), num])
    run.clause("the product's reader gives back the recorded run's own entry as the task's baseline (the run is the "
               "task's last ratified entry)", r.returncode == 0 and bool(own) and r.stdout.strip() == own == want,
               f"rc {r.returncode}; returned {len(r.stdout.strip())} chars; the run's own entry {len(own)}; "
               f"the task's last {len(want)}")
    # a seam is graded with exactly ONE box ticked, or left NOT GRADED only when its verdict says so and why (P1)
    review = (rdirs[0] / "review.md").read_text(encoding="utf-8") if rdirs else ""
    seams = re.findall(r"(?m)^- \[(x| )\] aligned / \[(x| )\] drift — verdict: (.*)$", review)

    def graded(a: str, d: str, v: str) -> bool:
        if (a, d) in (("x", " "), (" ", "x")):
            return bool(v.strip())
        return (a, d) == (" ", " ") and v.strip().startswith("NOT GRADED") and len(v.strip()) > len("NOT GRADED") + 10
    run.clause("each of the three seams carries the owner's grading: one box ticked with its verdict, or NOT GRADED "
               "with the reason", len(seams) == 3 and all(graded(*s) for s in seams),
               f"{[(a, d, v[:40]) for a, d, v in seams]}")


def cli_intent(out: Path) -> int:
    run = Run("PB-CLI-INTENT", "`tasks intent` on a finished task: the no-spend refusal, then the blind extractions on a "
              "real claude judge", out, ["claude"])
    j = Judges("pb184-intent-")
    nonce = uuid.uuid4().hex[:8]
    chat_mark, code_mark = f"ASKED-{nonce}", f"CODED-{nonce}"
    try:
        _new_task(j, run, "quick", "sum-fix", "Make add() in src/calc.py return the sum.")
        for d in ("src", "tests"):
            (j.proj / d).mkdir()
        (j.proj / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        g = lambda *a: subprocess.run(["git", *a], cwd=j.proj, check=True, capture_output=True, env=j.env)  # noqa: E731
        g("add", "-A")
        g("commit", "-q", "-m", "the scratch code before the task")
        (j.proj / "src" / "calc.py").write_text(f"def add(a, b):\n    # {code_mark}\n    return a + b\n", encoding="utf-8")
        (j.proj / "tests" / "test_calc.py").write_text(
            "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")
        g("add", "-A")
        g("commit", "-q", "-m", "Task 1: add() returns the sum, with its test")
        (j.proj / ".agent" / "chat_log.md").write_text(
            "# Chat Log\n\n<!-- T001 -->\n## [M1] 2026-10-12 10:00 (claude/pid-1)\n"
            f"add() subtracts instead of adding — please make it return the sum ({chat_mark}).\n<!-- /T001 -->\n",
            encoding="utf-8")
        runs_of = lambda: list(j.proj.glob(".agent/tasks/001-*/intent/*"))  # noqa: E731
        # the default judge, untouched: what would a run spend, and on which seat?
        r = j.tasks(run, "bare: `tasks intent 1` (the plugin's default judge)", "intent", "1")
        first = _quoted(r.stderr)
        run.clause("without `--yes` it says which layers have evidence and what a run would spend, then exits 2 — no "
                   "judge CLI launched, no run directory, no spend record",
                   r.returncode == 2 and first is not None and not j.records("argv") and not runs_of()
                   and not _spend_records(j) and all(f"  {layer}" in r.stdout for layer in ("chat", "taskmd", "code", "tests")),
                   f"rc {r.returncode}; quoted {first!r}; CLI launches {len(j.records('argv'))}")
        seat = first.split("@")[1] if first and first.count("@") >= 3 else ""
        run.clause("its default extraction judge is a claude seat, not codex", bool(seat) and not seat.startswith(
            ("codex", "grok", "agy", "pi")), f"seat {seat!r}")
        # a cheap claude seat for the run itself — and the old quote no longer approves what a run would spend
        j.seats([CLAUDE_SEAT], CLAUDE_SEAT)
        for label, given in (("a bare `--yes`", None), ("the quote printed for the OLD judge", first),
                             ("a quote never printed", "9@haiku@1m@0.01")):
            args = ["intent", "1", "--yes"] + ([given] if given else [])
            r = j.tasks(run, f"refused: {label}", *args)
            run.clause(f"{label}: refused with exit 2 and the line printed again; nothing launched, written or recorded",
                       r.returncode == 2 and _quoted(r.stderr) is not None and not j.records("argv")
                       and not runs_of() and not _spend_records(j),
                       f"rc {r.returncode}; CLI launches {len(j.records('argv'))}")
        r = j.tasks(run, "the line for the judge now", "intent", "1")
        quote = _quoted(r.stderr)
        r = j.tasks(run, "approved exactly: `tasks intent 1 --yes <that line>`", "intent", "1", "--yes", quote or "-",
                    timeout=1800)
        calls = j.judge_calls("claude")
        found = runs_of()
        files = sorted(p.name for p in found[0].iterdir()) if found else []
        run.clause("approved, it runs one blind extraction per layer on the real judge and writes the four reports and "
                   "the grading sheet", r.returncode == 0 and len(calls) == 4 and len(found) == 1
                   and files == ["chat.md", "code.md", "review.md", "taskmd.md", "tests.md"],
                   f"rc {r.returncode}; judge calls {len(calls)}; run files {files}")
        chat_seen = [i for i, c in enumerate(calls) if chat_mark in c["prompt"]]
        code_seen = [i for i, c in enumerate(calls) if code_mark in c["prompt"]]
        run.clause("blind: the conversation's words reach only one extraction, the code's only another",
                   len(chat_seen) == 1 and len(code_seen) == 1 and chat_seen != code_seen,
                   f"calls carrying the chat marker {chat_seen}; the code marker {code_seen}")
        spent = _spend_records(j)
        run.note("spend records: " + json.dumps(spent))
        run.clause("each extraction is one spend record", len(spent) == 4, f"spend records {len(spent)}")
        _owner_distillation(run)
    finally:
        j.cleanup()
    return run.finish()


ROWS = {
    "PB-JUDGE-BLIND-CONTEXT": blind_context,
    "PB-JUDGE-READONLY": readonly,
    "PB-PANEL-QUORUM": panel_quorum,
    "PB-MODEL-AVAILABILITY": model_availability,
    "PB-REVIEW-TRIAGE": review_triage,
    "PB-CLI-INTENT": cli_intent,
}
