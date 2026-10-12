"""Live rows of the platform group (task 184, W1): real interpreters, a bare runtime,
real bubblewrap, a real judge run. Each function takes the output directory and
returns the row's exit status (common.PASS / FAIL / WAITING)."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
import uuid
from pathlib import Path

from common import PLUGIN, Run, clean_env, judge_runs, scratch_project

MIN_IMAGE, NEW_IMAGE, OLD_IMAGE = "python:3.10-slim", "python:3.14-slim", "python:3.9-slim"
# Every shipped script that starts by checking the interpreter (grep `version_info >= (3,10)` in scripts/).
FLOOR_ENTRY_POINTS = ("tasks", "sandbox", "init", "playbook-codex", "playbook-grok", "playbook-agy", "playbook-pi",
                      "monitor-lib/launch-monitor", "command-guard-hook", "codex-user-prompt-hook",
                      "codex-stop-hook", "codex-apply-patch-hook")
LAUNCHERS = FLOOR_ENTRY_POINTS[:8]           # refuse: exit 1 with the message
HOOKS = FLOOR_ENTRY_POINTS[8:]               # a hook that cannot run lets the call through, loud (node [5])
# The Claude hooks.json commands that do NOT check the interpreter (command-guard-hook, the seventh, does): under
# 3.9 they are run on a project with no active task and must answer as on a supported interpreter (impl panel
# round 2, T6) — that is all this run measures; their other paths under 3.9 are not claimed (parked on task 184).
# hook -> (event, payload fields, exit status, a word its answer must hold), as measured under 3.10.
UNCHECKED_CLAUDE_HOOKS = {
    "session-start-hook": ("SessionStart", {"source": "startup"}, "0", ""),
    "chat-log-hook": ("UserPromptSubmit", {"prompt": "hello"}, "0", ""),
    "task-gate-hook": ("PreToolUse", {"tool_name": "Edit", "tool_input": {
        "file_path": "/tmp/hp/src/a.py", "old_string": "x = 1", "new_string": "x = 2"}}, "2", "BLOCKED: No active task"),
    "state-echo-hook": ("PostToolUse", {"tool_name": "Read", "tool_input": {"file_path": "/tmp/hp/src/a.py"},
                                        "tool_response": {}}, "0", "No active task"),
    "stop-hook": ("Stop", {"stop_hook_active": False}, "0", ""),
    "session-end-hook": ("SessionEnd", {"reason": "exit"}, "0", ""),
}
NEXT_VERSIONS = ("3.15", "3.16")             # NEW_IMAGE is the newest only while neither of these images exists


def _claude_hook_probe() -> str:
    """Shell lines that run each unchecked Claude hook once with its real payload, in a project with no task."""
    lines = ["mkdir -p /tmp/hp/src /tmp/hp/.agent/tasks && echo 'x = 1' > /tmp/hp/src/a.py && cd /tmp/hp",
             "export CLAUDE_PLUGIN_ROOT=/pb CLAUDE_PROJECT_DIR=/tmp/hp"]
    for hook, (event, fields, _rc, _word) in UNCHECKED_CLAUDE_HOOKS.items():
        payload = json.dumps({"session_id": "0b0e5a1e-0000-4000-8000-000000000001", "cwd": "/tmp/hp",
                              "hook_event_name": event, **fields})
        lines.append(f"out=$(printf '%s' {shlex.quote(payload)} | timeout 30 bash /pb/scripts/{hook} 2>&1); rc=$?")
        lines.append(f'echo "### claude:{hook} rc=$rc"; ' + "printf '%s\\n' \"$out\"")
    lines.append("cd /tmp")
    return "\n".join(lines)

# Compile every shipped module without writing a byte (the plugin is mounted read-only).
_COMPILE_ALL = r'''
import os, sys
bad, n = [], 0
for d, dirs, files in os.walk("/pb"):
    dirs[:] = [x for x in dirs if x != "__pycache__"]
    for f in files:
        p = os.path.join(d, f)
        try:
            with open(p, "rb") as fh:
                head = fh.readline()
        except OSError:
            continue
        # a module, or a script whose first line runs python (the hooks and helpers)
        if f.endswith(".py") or (head.startswith(b"#!") and b"python" in head):
            n += 1
            try:
                compile(open(p, encoding="utf-8").read(), p, "exec")
            except SyntaxError as exc:
                bad.append(f"{p}: {exc}")
print(f"{sys.version.split()[0]}: compiled {n} python files, {len(bad)} failed")
print("\n".join(bad))
sys.exit(1 if bad or n != int(sys.argv[1]) else 0)
'''


def _python_files() -> int:
    """Counted on the host: what the container must find and compile."""
    n = 0
    for p in PLUGIN.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            with p.open("rb") as fh:
                head = fh.readline()
            n += p.suffix == ".py" or (head.startswith(b"#!") and b"python" in head)
    return n

# A task's whole round trip through the CLI in a scratch project, as a user starts.
_ROUND_TRIP = r'''set -u
mkdir -p /tmp/p && cd /tmp/p
T=/pb/scripts/tasks
for c in "new quick smoke-one first" "list" "work 1" "status" "audit" "parked"; do
  echo ">>> tasks $c"; $T $c; echo "<<< rc=$?"
done
# the tree it ran from is the read-only mount itself: a write there must be refused
if touch /pb/.pb184-write-probe 2>/dev/null; then echo "RO-PROBE: written"; else echo "RO-PROBE: refused"; fi
'''


def _docker(run: Run, title: str, image: str, script: str, *, network: bool = False, extra_env=()):
    argv = ["docker", "run", "--rm", "-v", f"{PLUGIN}:/pb:ro", "-e", "PYTHONDONTWRITEBYTECODE=1"]
    for e in extra_env:
        argv += ["-e", e]
    if not network:
        argv += ["--network", "none"]
    return run.step(title, argv + [image, "bash", "-c", script], timeout=600)


def python_floor(out: Path) -> int:
    run = Run("PB-PYTHON-FLOOR", "the minimum and the newest Python, and one below the floor, on real interpreters",
              out, ["linux"])
    run.note("Images: " + ", ".join((MIN_IMAGE, NEW_IMAGE, OLD_IMAGE)) + ". The plugin tree is mounted read-only, "
             "no bytecode is written.")
    # "the newest" is measured, not written down (impl panel round 2, T10): the registry is asked for the next two
    # versions, and for NEW_IMAGE itself as the control that the registry answered at all
    tags = [NEW_IMAGE] + [f"python:{v}-slim" for v in NEXT_VERSIONS]
    r = run.step("which python images exist on Docker Hub today", " ".join(
        f"docker manifest inspect {t} >/dev/null 2>&1 && echo '{t} exists' || echo '{t} absent';" for t in tags))
    found = r.stdout.split()
    run.clause(f"{NEW_IMAGE} is the newest python image today: it exists and {', '.join(tags[1:])} do not",
               f"{NEW_IMAGE} exists" in r.stdout and all(f"{t} absent" in r.stdout for t in tags[1:]),
               " / ".join(r.stdout.strip().splitlines()) or f"(no answer; {found})")
    expected = _python_files()
    run.note(f"python files in the plugin tree (modules + scripts whose first line runs python): {expected}")
    for image, label in ((MIN_IMAGE, "the minimum, 3.10"), (NEW_IMAGE, "the newest")):
        r = _docker(run, f"every shipped python file compiles under {label}", image,
                    f"python - {expected} <<'PY'\n{_COMPILE_ALL}PY")
        run.clause(f"every shipped python file compiles under {label} ({image})", r.returncode == 0 and " 0 failed" in r.stdout,
                   (r.stdout.strip().splitlines() or ["(no output)"])[0])
        r = _docker(run, f"a task's round trip through the CLI under {label}", image, _ROUND_TRIP)
        rcs = [line for line in r.stdout.splitlines() if line.startswith("<<< rc=")]
        run.clause(f"the CLI round trip (new, list, work, status, audit, parked) runs under {label}",
                   len(rcs) == 6 and all(x == "<<< rc=0" for x in rcs) and "Traceback" not in r.stdout + r.stderr,
                   f"exit statuses {[x[7:] for x in rcs]}, Traceback in output: {'Traceback' in r.stdout + r.stderr}")
        run.clause(f"under {label} the tree the round trip ran from refused a write (the mount is read-only)",
                   "RO-PROBE: refused" in r.stdout, f"{[x for x in r.stdout.splitlines() if x.startswith('RO-PROBE')]}")
    # one below the floor: every entry point refuses, or (a hook) lets the call through loud — before any import
    # launch-monitor first looks for a live main session — a process named `claude` with a session
    # directory — and only then checks the interpreter: give it one (a copy of `sleep` named claude)
    # (it asks `ps` for the process's name: the slim image has none, so procps is installed first —
    # the only step of the group with the network on)
    probe = ("apt-get -qq update >/dev/null && apt-get -qq install -y procps >/dev/null && "
             "cd /tmp && cp /bin/sleep /tmp/claude && { /tmp/claude 120 & } && "
             "mkdir -p .agent/sessions/pid-$! && for s in ") + " ".join(FLOOR_ENTRY_POINTS) + r'''; do
  out=$(timeout 20 /pb/scripts/$s </dev/null 2>&1); rc=$?
  echo "### $s rc=$rc"; printf '%s\n' "$out"
done
# the apply-patch hook's floor keeps its per-event polarity: the enforcing PreToolUse read fails CLOSED
for ev in PreToolUse PostToolUse; do
  out=$(printf '{"hook_event_name": "%s"}' "$ev" | timeout 20 /pb/scripts/codex-apply-patch-hook 2>&1); rc=$?
  echo "### codex-apply-patch-hook@$ev rc=$rc"; printf '%s\n' "$out"
done
''' + _claude_hook_probe()
    r = _docker(run, "each entry point under Python 3.9, one below the floor", OLD_IMAGE, probe, network=True)
    blocks = {}
    for chunk in r.stdout.split("### ")[1:]:
        head, _, body = chunk.partition("\n")
        name, _, rc = head.partition(" rc=")
        blocks[name] = (rc.strip(), body)
    for s in LAUNCHERS:
        rc, body = blocks.get(s, ("?", ""))
        run.clause(f"under 3.9 `{s}` refuses before importing anything", rc == "1" and "needs python3 >= 3.10" in body
                   and "Traceback" not in body, f"rc={rc}: {body.strip()[:160]!r}")
    for s in HOOKS:
        rc, body = blocks.get(s, ("?", ""))
        run.clause(f"under 3.9 the hook `{s}` says so and does not fail with a traceback", rc == "0"
                   and "3.10" in body and "Traceback" not in body, f"rc={rc}: {body.strip()[:160]!r}")
    for ev, want_rc, word in (("PreToolUse", "2", "fail-closed"), ("PostToolUse", "0", "fail-open")):
        rc, body = blocks.get(f"codex-apply-patch-hook@{ev}", ("?", ""))
        run.clause(f"under 3.9 `codex-apply-patch-hook` given a {ev} event exits {want_rc} ({word})",
                   rc == want_rc and word in body and "Traceback" not in body, f"rc={rc}: {body.strip()[:160]!r}")
    for hook, (event, _fields, want_rc, word) in UNCHECKED_CLAUDE_HOOKS.items():
        rc, body = blocks.get(f"claude:{hook}", ("?", ""))
        run.clause(f"under 3.9 the Claude hook `{hook}` (no floor check of its own) answers a {event} with no active "
                   f"task as on a supported interpreter: exit {want_rc}" + (f", `{word}`" if word else ""),
                   rc == want_rc and word in body and "Traceback" not in body and "Error" not in body,
                   f"rc={rc}: {body.strip()[:160]!r}")
    return run.finish()


def runtime_stdlib(out: Path) -> int:
    run = Run("PB-RUNTIME-STDLIB", "the runtime on a bare interpreter: no third-party package, no build step",
              out, ["linux"])
    # every shipped file is text: Bash, Python, Markdown, JSON — no compiled or binary artifact
    binary = []
    kinds: "dict[str, int]" = {}
    for p in PLUGIN.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            data = p.read_bytes()
            if b"\0" in data[:8192]:
                binary.append(str(p.relative_to(PLUGIN)))
            kinds[p.suffix or "(no suffix)"] = kinds.get(p.suffix or "(no suffix)", 0) + 1
    run.note("shipped files by suffix: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])))
    run.clause("every shipped file is text (no compiled or binary artifact)", not binary,
               f"{sum(kinds.values())} files, binary: {binary or 'none'}")
    empty = ("python - <<'PY'\nimport shutil, site, os\nfor d in site.getsitepackages():\n"
             "    for e in os.listdir(d):\n        p = os.path.join(d, e)\n"
             "        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)\nPY\n")
    for image, label in ((MIN_IMAGE, "3.10"), (NEW_IMAGE, "the newest")):
        r = _docker(run, f"site-packages emptied, then a task's round trip under {label} ({image})", image,
                    empty + "python -c 'import pip' 2>&1 | tail -1; python -c 'import setuptools' 2>&1 | tail -1; "
                    "command -v git || echo 'no git on this image'\n" + _ROUND_TRIP)
        rcs = [line for line in r.stdout.splitlines() if line.startswith("<<< rc=")]
        gone = r.stdout.count("No module named") >= 2
        ok = gone and len(rcs) == 6 and all(x == "<<< rc=0" for x in rcs) and "Traceback" not in r.stdout + r.stderr
        run.clause(f"with no third-party package at all ({label}: pip and setuptools gone) the CLI round trip runs",
                   ok, f"third-party gone: {gone}; exit statuses {[x[7:] for x in rcs]}")
        # no build step: the round trip above ran `/pb/scripts/tasks` from the mount, and the mount refused a write
        # (a constant here passed whatever happened — impl panel I2)
        probe = [x for x in r.stdout.splitlines() if x.startswith("RO-PROBE")]
        run.clause(f"no build step ({label}): the checked-out tree runs as it is, from a mount that refused a write",
                   ok and probe == ["RO-PROBE: refused"], f"round trip passed: {ok}; {probe}")
    return run.finish()


def _bindir(names: "tuple[str, ...]", root: Path) -> Path:
    """A PATH directory holding links to exactly these host tools."""
    root.mkdir(parents=True, exist_ok=True)
    for n in names:
        src = shutil.which(n)
        if src:
            (root / n).symlink_to(src)
    return root


_TOOLS = ("bash", "sh", "env", "python3", "git", "claude", "cat", "ls", "grep", "sed", "dirname", "basename",
          "readlink", "realpath", "uname", "id", "mkdir", "rm", "cp", "mv", "date", "tr", "head", "tail", "find",
          "awk", "sort", "wc", "cut", "timeout", "sleep", "ps", "flock", "touch", "stat", "mktemp", "tee")


def sandbox_detection_fallback(out: Path) -> int:
    run = Run("PB-SANDBOX-DETECTION-FALLBACK", "containment present, absent, behind a relative PATH entry, unable to "
              "start, nested — and the judge path's visible fallback", out, ["linux"])
    tmp = Path(tempfile.mkdtemp(prefix="pb184-sdf-"))
    try:
        proj = scratch_project(tmp / "proj")
        sandbox = str(PLUGIN / "scripts" / "sandbox")
        real_bwrap = shutil.which("bwrap")
        nobw = _bindir(_TOOLS, tmp / "nobw")
        stub = tmp / "stubbin"
        stub.mkdir()
        (stub / "bwrap").write_text("#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n")
        (stub / "bwrap").chmod(0o755)
        rel = proj / "relbin"
        rel.mkdir()
        (rel / "bwrap").write_text("#!/bin/sh\nexit 0\n")
        (rel / "bwrap").chmod(0o755)
        base = clean_env()

        r = run.step("present: the launcher previews a contained launch", [sandbox, "--agent", "claude", "--print-argv",
                     "--", "--version"], cwd=proj, env=base)
        first = (r.stdout.splitlines() or [""])[0]
        run.clause("with bubblewrap present the wrapped launch names it by the absolute path that was checked",
                   r.returncode == 0 and first == real_bwrap and os.path.isabs(first), f"first argv word {first!r}, which bwrap = {real_bwrap!r}")
        r = run.step("present: a real agent binary started inside it", [sandbox, "--agent", "claude", "--", "--version"],
                     cwd=proj, env=base, timeout=120)
        run.clause("…and an agent actually starts contained", r.returncode == 0 and "Claude Code" in r.stdout,
                   f"rc={r.returncode}, stdout {r.stdout.strip()[:80]!r}")

        env = dict(base, PATH=str(nobw))
        r = run.step("absent: no bubblewrap on PATH", [sandbox, "--agent", "claude", "--", "--version"], cwd=proj,
                     env=env, env_shown={"PATH": str(nobw)})
        run.clause("with no bubblewrap the launcher refuses to start the agent (exit 2) and says why",
                   r.returncode == 2 and "bubblewrap" in (r.stderr + r.stdout).lower() and "Claude Code" not in r.stdout,
                   (r.stderr.strip().splitlines() or [""])[0][:160])
        r = run.step("absent: the preview too", [sandbox, "--agent", "claude", "--print-argv", "--", "--version"],
                     cwd=proj, env=env, env_shown={"PATH": str(nobw)})
        run.clause("…and refuses the preview the same way", r.returncode == 2 and not r.stdout.strip(),
                   f"rc={r.returncode}, stdout {r.stdout.strip()[:60]!r}")

        env = dict(base, PATH=f"relbin:{nobw}")
        r = run.step("a bubblewrap reached only through a relative PATH entry (`relbin`, inside the project)",
                     [sandbox, "--agent", "claude", "--", "--version"], cwd=proj, env=env, env_shown={"PATH": f"relbin:{nobw}"})
        run.clause("a bubblewrap behind a relative PATH entry counts as missing: refused", r.returncode == 2
                   and "Claude Code" not in r.stdout, (r.stderr.strip().splitlines() or [""])[0][:160])

        env = dict(base, PATH=f"{stub}:{nobw}")
        r = run.step("a bubblewrap that cannot start a sandbox", [sandbox, "--agent", "claude", "--", "--version"],
                     cwd=proj, env=env, env_shown={"PATH": f"{stub}:{nobw}"})
        run.clause("a bubblewrap that is installed but cannot start a sandbox: refused", r.returncode == 2
                   and "Claude Code" not in r.stdout, (r.stderr.strip().splitlines() or [""])[0][:160])

        # nested: a process INSIDE the plugin's own sandbox (a real judge, haiku) runs the launcher's preview there.
        # "Nested" is the plugin's own cage: `run()` marks its child PLAYBOOK_SANDBOXED=1 (`is_sandboxed`); a
        # foreign bubblewrap is not one of ours and is not claimed to be detected.
        drop = Path(tempfile.gettempdir()) / f"pb184-nested-{uuid.uuid4().hex[:10]}"
        drop.mkdir()
        try:
            judge_runs(run, "nested: a real judge inside the plugin's sandbox runs the launcher's preview", proj,
                       [f"{sandbox} --agent claude --print-argv -- --version > {drop}/nested.txt 2>&1; "
                        f"echo rc=$? >> {drop}/nested.txt; echo marker=$PLAYBOOK_SANDBOXED >> {drop}/nested.txt"],
                       env=base)
            got = (drop / "nested.txt").read_text(errors="replace") if (drop / "nested.txt").exists() else "(absent)"
            run.note(f"--- the inner launcher's output (nested.txt) ---\n{got.rstrip()}")
            first = (got.splitlines() or [""])[0]
            run.clause("nested containment is detected: inside the plugin's sandbox the launch is the bare agent, "
                       "not a second cage", "marker=1" in got and "rc=0" in got and first != ""
                       and Path(first).name != "bwrap" and "claude" in first,
                       f"first argv word {first!r}; {got.strip().splitlines()[-2:] if got.strip() else got!r}")
        finally:
            shutil.rmtree(drop, ignore_errors=True)

        # the judge path itself: a real single-judge review (haiku, by the scratch project's own judge setting)
        tasks = str(PLUGIN / "scripts" / "tasks")
        (proj / ".agent" / "tasks").mkdir(parents=True, exist_ok=True)
        (proj / ".agent" / "models.json").write_text('{"default_judge": "haiku", "panel": ["haiku"]}\n')
        run.step("a scratch task for the judge to read", [tasks, "new", "quick", "judge-probe",
                 "A one-line probe: the judge only has to answer."], cwd=proj, env=base)
        for label, path_env, contained in (("no bubblewrap on PATH", str(nobw), False),
                                           ("a bubblewrap that cannot start a sandbox", f"{stub}:{nobw}", None)):
            env = dict(base, PATH=path_env)
            r = run.step(f"the judge path (`tasks plan-review 1`, judge haiku) with {label}", [tasks, "plan-review", "1"],
                         cwd=proj, env=env, env_shown={"PATH": path_env}, timeout=600)
            said = "UNCONTAINED" in r.stderr
            if contained is False:
                run.clause("with no bubblewrap the judge path runs the judge uncontained AND says so before it starts",
                           r.returncode == 0 and said, f"rc={r.returncode}; warning printed: {said}; "
                           f"{[x for x in r.stderr.splitlines() if 'UNCONTAINED' in x][:1]}")
            else:
                run.clause("with a bubblewrap that cannot start, the judge's launch fails and nothing falls back "
                           "uncontained", r.returncode != 0 and not said,
                           f"rc={r.returncode}; 'UNCONTAINED' printed: {said}; stderr tail {r.stderr.strip()[-200:]!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return run.finish()


def sandbox_read_network(out: Path) -> int:
    run = Run("PB-SANDBOX-READ-NETWORK", "a real judge run: reads and the network allowed, project writes denied, the "
              "records hidden", out, ["linux"])
    tmp = Path(tempfile.mkdtemp(prefix="pb184-srn-"))
    nonce = uuid.uuid4().hex[:12]
    drop = Path(tempfile.gettempdir()) / f"pb184-judge-{nonce}"
    try:
        proj = scratch_project(tmp / "proj")
        canary = f"CANARY-{nonce}"
        (proj / ".agent" / "chat_log.md").write_text(f"[M1] the owner said {canary}\n")
        drop.mkdir()
        host = Path("/etc/hostname").read_text().strip()
        r = judge_runs(run, "a real judge, read-only project, through the path the judges share "
                       "(`sandbox --ro-project --prompt`)", proj, [
                           f"cat /etc/hostname > {drop}/read.txt 2>&1",
                           f"python3 -c \"import urllib.request; print(urllib.request.urlopen('https://www.example.com', timeout=15).status)\" > {drop}/net.txt 2>&1",
                           f"{{ echo written > {proj}/judge-wrote-this.txt ; }} 2> {drop}/write.txt; echo rc=$? >> {drop}/write.txt",
                           f"cat {proj}/.agent/chat_log.md > {drop}/records.txt 2>&1; echo rc=$? >> {drop}/records.txt"],
                       watch=[proj])
        got = {n: (drop / n).read_text(errors="replace") if (drop / n).exists() else "(absent)"
               for n in ("read.txt", "net.txt", "write.txt", "records.txt")}
        for n, v in got.items():
            run.note(f"--- the judge's {n} ---\n{v.rstrip()}")
        run.clause("the judge answered at all (its provider CLI reached the model API from inside)",
                   r.returncode == 0 and "DONE" in r.stdout, f"rc={r.returncode}, reply {r.stdout.strip()[-40:]!r}")
        run.clause("a file outside the project is readable from inside", got["read.txt"].strip() == host,
                   f"read.txt {got['read.txt'].strip()[:40]!r}, host's /etc/hostname {host!r}")
        run.clause("the network is reachable from inside", got["net.txt"].strip() == "200", f"net.txt {got['net.txt'].strip()[:80]!r}")
        run.clause("a project write is denied", not (proj / "judge-wrote-this.txt").exists()
                   and "Read-only file system" in got["write.txt"], f"write.txt {got['write.txt'].strip()[:120]!r}")
        run.clause("the conversation records are hidden from the judge", canary not in got["records.txt"]
                   and got["records.txt"] != "(absent)", f"records.txt {got['records.txt'].strip()[:120]!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(drop, ignore_errors=True)
    return run.finish()


ROWS = {
    "PB-PYTHON-FLOOR": python_floor,
    "PB-RUNTIME-STDLIB": runtime_stdlib,
    "PB-SANDBOX-DETECTION-FALLBACK": sandbox_detection_fallback,
    "PB-SANDBOX-READ-NETWORK": sandbox_read_network,
}
