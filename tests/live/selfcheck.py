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
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import rows_install  # noqa: E402
import rows_judge  # noqa: E402
import rows_platform  # noqa: E402
import rows_session  # noqa: E402
import rows_session2  # noqa: E402
from common import delta, end_tree, snapshot, tempdir, tool_results  # noqa: E402

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
                       ("the same change plus a mode change 100644 -> 100755",
                        re.sub(r"index ([0-9a-f]+)\.\.([0-9a-f]+) 100644", r"old mode 100644\nnew mode 100755\nindex \1..\2",
                               idents[0][1])),
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
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        left = alive(d / "gc")
        check("T13 the OLD way (only the direct child killed on the timeout) leaves the grandchild running", left, True)
    finally:
        # the old way's leftovers, whatever happened — an interrupted selfcheck included (K2): its group (the
        # foreground sleep) and the grandchild in its own session
        end_tree(p.pid)
        try:
            end_tree(int((d / "gc").read_text()))
        except (OSError, ValueError):
            pass


def step_detached() -> None:
    """A process that detached (setsid -f) and was reparented BEFORE the timeout is not a descendant any more;
    the step's token in its environment still finds it (single judge after round 2, J1)."""
    d = tempdir("pb184-det-")
    from common import Run
    r = Run("PB-SELFCHECK", "a step that detaches a child at once", d / "out", [])
    cmd = f"setsid -f bash -c 'trap \"\" TERM; echo $$ > {d}/gc; exec sleep 300'; sleep 1; sleep 300"
    res = r.step("a step whose child detaches at once and ignores SIGTERM", cmd, timeout=3)
    time.sleep(0.5)
    try:
        os.kill(int((d / "gc").read_text()), 0)
        alive = True
    except (OSError, ValueError):
        alive = False
    check("J1 a child detached and reparented before the timeout is gone after it", res.returncode == 124 and not alive,
          True, (res.stderr.strip().splitlines() or [""])[-1])


def gauntlet_quarantine() -> None:
    """gauntlet.sh run on a stub row that writes a PASS artifact and then dies (exit 139): the artifact is set aside
    and the run fails; the same stub exiting 0 keeps it (single judge after round 2, J2)."""
    for code, kept in ((139, False), (0, True)):
        d = tempdir("pb184-gq-")
        (d / "lib").mkdir()
        (d / "gauntlet.sh").write_text((Path(__file__).resolve().parent / "gauntlet.sh").read_text())
        (d / "lib" / "run_row.py").write_text(
            "import os, sys, pathlib\n"
            "pathlib.Path(sys.argv[2], sys.argv[1] + '.txt').write_text('# stub\\nROW ' + sys.argv[1] + ': PASS (1 clauses)\\n')\n"
            f"os._exit({code})\n")
        (d / "selfcheck.py").write_text("import sys\nsys.exit(0)\n")
        out = d / "out"
        r = subprocess.run(["bash", str(d / "gauntlet.sh"), "--out", str(out), "PB-PYTHON-FLOOR"],
                           capture_output=True, text=True, timeout=60)
        art = (out / "PB-PYTHON-FLOOR.txt").exists()
        check(f"J2 a row that wrote PASS and exited {code}: artifact {'kept' if kept else 'set aside'}, run rc "
              f"{0 if kept else 1}", art == kept and r.returncode == (0 if kept else 1)
              and (out / "PB-PYTHON-FLOOR.txt.invalid").exists() != kept, True,
              f"rc {r.returncode}; " + " / ".join(x for x in r.stdout.splitlines() if x.startswith("ROW")))


def registry_probe() -> None:
    """The newest-image probe with a stand-in `docker` first on PATH: the registry's own "no such manifest" is
    absence; a timeout or another error is UNKNOWN and fails the clause (single judge after round 2, J3)."""
    tags = [rows_platform.NEW_IMAGE] + [f"python:{v}-slim" for v in rows_platform.NEXT_VERSIONS]
    for label, body, want in (
            ("not-found for the next two", 'case "$3" in *3.14*) exit 0;; *) echo "no such manifest: docker.io/library/$3" >&2; exit 1;; esac', True),
            ("an error for the next two", 'case "$3" in *3.14*) exit 0;; *) echo "net/http: TLS handshake timeout" >&2; exit 1;; esac', False),
            ("no answer at all", "exit 1", False)):
        d = tempdir("pb184-reg-")
        (d / "docker").write_text("#!/bin/bash\n" + body + "\n")
        (d / "docker").chmod(0o755)
        probe = rows_platform.newest_probe(tags).replace("sleep 5", "sleep 0")
        r = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, PATH=f"{d}:{os.environ['PATH']}"))
        ok, said = rows_platform.newest_verdict(r.stdout, tags)
        check(f"J3 the newest-image clause with the registry answering {label}", ok, want, said)


def docker_timeout() -> None:
    """A container whose step times out is removed, not left to the daemon (K1). Skipped without docker."""
    if not shutil.which("docker"):
        print("skip docker_timeout: no docker")
        return
    d = tempdir("pb184-dk-")
    from common import Run
    run = Run("PB-SELFCHECK", "a container step that times out", d / "out", [])
    seen = []
    real_step = run.step

    def step(title, argv, **kw):                       # record the container's name as the step starts it
        seen.extend(a for a in argv if str(a).startswith("pb184-"))
        return real_step(title, argv, **kw)
    run.step = step
    r = rows_platform._docker(run, "sleep past the limit", rows_platform.OLD_IMAGE, "sleep 300", timeout=8)
    left = subprocess.run(["docker", "ps", "-a", "-q", "--filter", f"name={seen[0]}"], capture_output=True,
                          text=True).stdout.strip() if seen else "?"
    check("K1 a container step that timed out leaves no container behind", r.returncode == 124 and left == "", True,
          f"rc {r.returncode}; container {seen[:1]} still there: {left!r}")


def gauntlet_missing_and_lock() -> None:
    """gauntlet.sh: a row that exits 0 and writes nothing fails the run; a second run on the same --out is
    refused while the first holds it (K3)."""
    d = tempdir("pb184-gm-")
    (d / "lib").mkdir()
    (d / "gauntlet.sh").write_text((Path(__file__).resolve().parent / "gauntlet.sh").read_text())
    (d / "lib" / "run_row.py").write_text("import sys\nsys.exit(0)\n")
    (d / "selfcheck.py").write_text("import sys\nsys.exit(0)\n")
    out = d / "out"
    r = subprocess.run(["bash", str(d / "gauntlet.sh"), "--out", str(out), "PB-PYTHON-FLOOR"], capture_output=True,
                       text=True, timeout=60)
    check("K3 a row that exits 0 with no artifact fails the run", r.returncode == 1 and "no artifact" in r.stdout, True,
          f"rc {r.returncode}; " + " / ".join(x for x in r.stdout.splitlines() if x.startswith("ROW")))
    holder = subprocess.Popen(["flock", str(out / ".gauntlet.lock"), "sleep", "30"], start_new_session=True)
    try:
        time.sleep(0.5)
        r = subprocess.run(["bash", str(d / "gauntlet.sh"), "--out", str(out), "PB-PYTHON-FLOOR"],
                           capture_output=True, text=True, timeout=60)
        check("K3 a second run on an --out another run holds is refused (exit 2)", r.returncode == 2
              and "another gauntlet run" in r.stderr, True, f"rc {r.returncode}; {r.stderr.strip()[:120]!r}")
    finally:
        end_tree(holder.pid)


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
    for s in (signal.SIGTERM, signal.SIGHUP):            # an interrupted selfcheck still runs its cleanups (K2)
        signal.signal(s, lambda signum, _f: sys.exit(128 + signum))
    for f in (freehand, names, install_env, snapshot_whole, step_timeout, step_detached, gauntlet_quarantine,
              registry_probe, docker_timeout, gauntlet_missing_and_lock):
        f()
    print(f"selfcheck: {'every control holds' if not FAILED else f'{len(FAILED)} control(s) do not hold'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
