#!/usr/bin/env python3
"""Negative controls for the live gauntlet's own clauses (task 184, impl panel round 1).

    python3 -I tests/live/selfcheck.py <artifacts-dir>

A clause that can pass without the behaviour it names proves nothing. Each predicate tightened after the
impl panel is run here, offline and without spending quota, on two inputs:
  * the real thing — the events or output a committed artifact recorded (or, where the artifact does not
    keep the input, a sample in the product's own format) — where it must PASS;
  * a doctored copy with exactly the named behaviour removed — where it must FAIL.
Where the panel showed the OLD clause passing that doctored copy, that is shown too, so the control is
seen to separate the two. Exit 0 = every control holds; 1 = one does not; 2 = usage."""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import rows_install  # noqa: E402
import rows_judge  # noqa: E402
import rows_session  # noqa: E402
import rows_session2  # noqa: E402
from common import delta, snapshot, tempdir, tool_results  # noqa: E402

FAILED: "list[str]" = []


def check(name: str, got: bool, want: bool, evidence: str = "") -> None:
    ok = bool(got) == want
    if not ok:
        FAILED.append(name)
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {'PASS' if got else 'FAIL'} (want {'PASS' if want else 'FAIL'})"
          + (f" — {evidence}" if evidence else ""))


def steps(artifact: Path) -> "list[tuple[str, str, str]]":
    """(title, stdout, stderr) of every step an artifact recorded."""
    out, title, buf, where = [], None, {"stdout": [], "stderr": []}, None
    for line in artifact.read_text(encoding="utf-8").splitlines():
        m = re.match(r"## step \d+: (.*)$", line)
        if m:
            if title is not None:
                out.append((title, "\n".join(buf["stdout"]), "\n".join(buf["stderr"])))
            title, buf, where = m.group(1), {"stdout": [], "stderr": []}, None
        elif line in ("--- stdout ---", "--- stderr ---"):
            where = line[4:10]
        elif line.startswith(("--- filesystem delta", "CLAUSE ", "WAITING: ", "## clauses")):
            where = None
        elif where:
            buf[where].append(line)
    if title is not None:
        out.append((title, "\n".join(buf["stdout"]), "\n".join(buf["stderr"])))
    return out


def events(stdout: str) -> "list[dict]":
    ev = []
    for line in stdout.splitlines():
        try:
            ev.append(json.loads(line))
        except ValueError:
            continue
    return ev


def session_events(artifact: Path) -> "list[list[dict]]":
    return [ev for _t, so, _se in steps(artifact) if (ev := events(so))]


def activate(d: Path) -> None:
    ev = session_events(d / "PB-TASK-ACTIVATE.txt")[0]
    got, said = rows_session.resume_named(ev)
    check("I3 resume named — the real session", got, True, said)
    bad = copy.deepcopy(ev)
    for e in bad:
        if e.get("type") == "system" and "is blocked" in str(e.get("output") or ""):
            e["output"] = re.sub(r"tasks work 0*1\b", "tasks status", str(e["output"]))
    got, said = rows_session.resume_named(bad)
    check("I3 resume named — the refusal with the resume taken out", got, False, said)
    check("I3 the OLD clause on that copy (it passed: the prompt echoed back says `tasks work 1`)",
          "tasks work 1" in " ".join(tool_results(bad)), True)


def echo(d: Path) -> None:
    ev = session_events(d / "PB-HOOK-STATE-ECHO.txt")[0]
    got, said = rows_session.parallel_echoes(ev)
    check("I4 parallel echoes — the real session", got, True, said)
    reads = [i for i, e in enumerate(ev) if e.get("subtype") == "hook_response" and e.get("hook_name") == "PostToolUse:Read"]
    bad = [e for i, e in enumerate(ev) if i != reads[-1]]
    got, said = rows_session.parallel_echoes(bad)
    check("I4 parallel echoes — the second Read's response removed", got, False, said)
    post = [json.dumps(x) for x in rows_session.hook_responses(bad, "PostToolUse")]
    check("I4 the OLD clause on that copy (it passed: 3 gate lines among all PostToolUse responses)",
          sum("Working on task [001]" in c for c in post) >= 3, True)


GATE = "- [ ] Freehand log — run `.claude/bin/tasks freehand log` to capture chat_log messages, then check this box"
MSG = "**[M001]** [2026-10-12 12:20:42 UTC] Run: .claude/bin/tasks freehand"
CHAT = "\n### Recent Chat (auto-captured at activation — review and remove unrelated)\n\n" + MSG + "\n"


def freehand() -> None:
    logged = f"# 001 - freehand\n\n## Freehand\n{GATE}\n\n{MSG}\n\n- [ ] Debrief\n{CHAT}"
    got, said = rows_session.freehand_logged(logged, ["Inserted 1 chat_log messages into task.md"])
    check("I9 freehand log — the block under the gate (the CLI's own format)", got, True, said)
    empty = f"# 001 - freehand\n\n## Freehand\n{GATE}\n- [ ] Debrief\n{CHAT}"
    got, said = rows_session.freehand_logged(empty, ["Inserted 1 chat_log messages into task.md"])
    check("I9 freehand log — nothing under the gate, a message only in Recent Chat", got, False, said)
    check("I9 the OLD clause on that copy (it passed: `[M0` anywhere after the words)",
          "[M0" in empty.split("Freehand log", 1)[-1], True)
    got, said = rows_session.freehand_logged(logged, ["No chat_log messages found in freehand span."])
    check("I9 freehand log — the CLI inserted nothing", got, False, said)


def availability(d: Path) -> None:
    by = {t: so + "\n" + se for t, so, se in steps(d / "PB-MODEL-AVAILABILITY.txt")}
    t = next(v for k, v in by.items() if "3 s review limit" in k)
    b = next(v for k, v in by.items() if "budget cap" in k)
    check("I6 timeout — the real run", rows_judge.timeout_reported(t), True)
    clamp_only = "\n".join(x for x in t.splitlines() if "hit hard timeout" not in x) + "\nError: something else\n"
    check("I6 timeout — only the clamp line printed before the judge started", rows_judge.timeout_reported(clamp_only), False)
    check("I6 the OLD clause on that copy (it passed: `timeout` in the clamp line)",
          re.search(r"timed out|timeout", clamp_only, re.I) is not None, True)
    check("I6 budget — the real run", rows_judge.budget_reported(b), True)
    other = "\n".join(x for x in b.splitlines() if "budget" not in x.lower()) + "\nusage: --max-budget-usd\n"
    check("I6 budget — a failure that is not the cap, the word only in a usage line", rows_judge.budget_reported(other), False)


def merge(d: Path) -> None:
    green = session_events(d / "PB-MERGE-PUSH-GATE.txt")[0]
    calls = rows_session2._calls_with_results(green)
    rx = rows_session2.GATES["code identity"][0]
    idents = [(c, r) for n, c, r in calls if n == "Bash" and re.search(rx, c)]
    check("I7 code identity — the real session ran it", bool(idents), True, f"{len(idents)} call(s)")
    if not idents:
        return
    check("I7 code identity — its real result", rows_session2.identity_green(idents[0][1]), True)
    at = rows_session2._gate_timeline(calls)
    check("I7 the real session: all four gates green before the push",
          "push" in at and all(g in at and at[g] < at["push"] for g in rows_session2.GATES), True, json.dumps(at))
    for label, res in (("`fatal: bad revision`", "fatal: bad revision 'deadbeef'\n"),
                       ("a diff that also changes another file", idents[0][1] + "\ndiff --git a/src/a.py b/src/a.py\n-A = 2\n+A = 9\n"),
                       ("an empty result", ""),
                       ("an extra line inside src/b.py, its hunk header true",
                        idents[0][1].replace("@@ -1 +1 @@\n-B = 1\n+B = 2", "@@ -1 +1,2 @@\n-B = 1\n+B = 2\n+raise RuntimeError('x')"))):
        check(f"I7 code identity — {label}", rows_session2.identity_green(res), False)
    bad = [(n, c, "fatal: bad revision 'deadbeef'\n" if re.search(rx, c) and n == "Bash" else r) for n, c, r in calls]
    at = rows_session2._gate_timeline(bad)
    check("I7 the timeline with the identity call failing: the push is not after four green gates",
          "push" in at and all(g in at and at[g] < at["push"] for g in rows_session2.GATES), False, json.dumps(at))


def names() -> None:
    made = [tempdir("pb184-self-") for _ in range(500)]
    odd = [p.name for p in made if re.search(r"[^A-Za-z0-9-]", p.name)]
    check("I5 500 scratch directory names: letters, digits and the prefix's `-` only", not odd, True, f"odd: {odd[:3]}")


def install_env() -> None:
    """The install group's env as `rows_install.install_env` builds it — the function the installer calls, not a
    copy of it (round 2, T7) — with a credential-like name and CLAUDE_CONFIG_DIR set around it."""
    extra = {"PB184_SELFCHECK_API_KEY": "x", "CLAUDE_CONFIG_DIR": "/nonexistent"}
    os.environ.update(extra)
    try:
        dirty = dict(os.environ)
        built = rows_install.install_env(Path("/tmp/pb184-selfcheck-home"))
    finally:
        for k in extra:
            os.environ.pop(k, None)
    run = lambda e: subprocess.run(["bash", "-c", rows_install.ENV_PROBE], env=e, capture_output=True, text=True).stdout  # noqa: E731
    good = run(built)
    check("I11/I12/T7 rows_install.install_env with a key and CLAUDE_CONFIG_DIR set around it",
          "HOME=/tmp/pb184-selfcheck-home" in good and "CLAUDE_CONFIG_DIR is unset" in good
          and "credential-like variables: 0" in good and "BASH_ENV is unset" in good, True, " / ".join(good.splitlines()))
    raw = run(dirty)
    check("I11/I12 the same probe on the unfiltered env sees them",
          "CLAUDE_CONFIG_DIR is unset" in raw and "credential-like variables: 0" in raw, False, " / ".join(raw.splitlines()[-3:]))
    check("I11 the probe prints no variable's value but the four isolation paths",
          all(re.match(r"(HOME|XDG_\w+)=|\w+ is (unset|SET)$|credential-like variables: \d+$", x) for x in raw.splitlines()), True)


def snapshot_whole() -> None:
    """Two files of 2 MiB that differ only in their last byte must not snapshot alike (round 2, T16)."""
    d = tempdir("pb184-snap-")
    (d / "f").write_bytes(b"a" * (2 << 20))
    before = snapshot(d)
    (d / "f").write_bytes(b"a" * ((2 << 20) - 1) + b"b")
    check("T16 a change in the last byte of a 2 MiB file is a change", bool(delta(before, snapshot(d))), True,
          str(delta(before, snapshot(d))))


def step_timeout() -> None:
    """A step that times out ends everything it started — here a grandchild in its own session that ignores
    SIGTERM (round 2, T13). The control: the same command through a bare `subprocess.run(timeout=…)`, which
    leaves that grandchild running (it is then ended by hand)."""
    d = tempdir("pb184-tmo-")
    cmd = (f"setsid bash -c 'trap \"\" TERM; echo $$ > {d}/gc; exec sleep 300' & "
           f"sleep 300")

    def alive(pidfile: Path) -> bool:
        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.1)
        try:
            os.kill(int(pidfile.read_text()), 0)
            return True
        except (OSError, ValueError):
            return False

    from common import Run, end_tree
    r = Run("PB-SELFCHECK", "a step that times out", d / "out", [])
    res = r.step("a step whose grandchild ignores SIGTERM in its own session", cmd, timeout=2)
    time.sleep(0.5)
    check("T13 Run.step timed out (124) and its grandchild is gone", res.returncode == 124 and not alive(d / "gc"),
          True, (res.stderr.strip().splitlines() or [""])[-1])
    (d / "gc").unlink(missing_ok=True)
    # the old way: kill only the direct child on the timeout, as `subprocess.run(timeout=…)` does. Its own group
    # and no pipe, so what it leaves behind can be ended here and holds no reader of this script's output open
    p = subprocess.Popen(["bash", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        p.wait(timeout=2)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
    left = alive(d / "gc")
    check("T13 the OLD way (only the direct child killed on the timeout) leaves the grandchild running", left, True)
    end_tree(p.pid)                                    # the old way's leftovers: its group (the foreground sleep) …
    if left:
        end_tree(int((d / "gc").read_text()))          # … and the grandchild in its own session


def main(argv: "list[str]") -> int:
    if len(argv) != 1 or not Path(argv[0]).is_dir():
        print("usage: python3 -I tests/live/selfcheck.py <artifacts-dir>", file=sys.stderr)
        return 2
    d = Path(argv[0])
    needs = {activate: "PB-TASK-ACTIVATE", echo: "PB-HOOK-STATE-ECHO", availability: "PB-MODEL-AVAILABILITY",
             merge: "PB-MERGE-PUSH-GATE"}
    for f, row in needs.items():
        if (d / f"{row}.txt").is_file():
            f(d)
        else:
            print(f"skip {f.__name__}: no {row}.txt in {d} (that row was not run)")
    for f in (freehand, names, install_env, snapshot_whole, step_timeout):
        f()
    print(f"selfcheck: {'every control holds' if not FAILED else f'{len(FAILED)} control(s) do not hold'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
