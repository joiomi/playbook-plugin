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


def _running(pid: int) -> bool:
    """Alive and not a zombie (a killed child nobody reaped still has a /proc entry)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat[stat.rindex(")") + 2:].split()[0] != "Z"


def spawn_interrupted() -> None:
    """A signal that lands as the spawn returns — before `Run.step` holds the process — must not leave the step's
    process running: the step's token finds it (task 184's L1). Simulated by a Popen that starts the real process
    and then raises SystemExit, as a SIGTERM turned into an exit would there."""
    import common
    d = tempdir("pb184-sp-")
    real = common.subprocess.Popen
    started = []

    class Interrupted(real):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            started.append(self.pid)
            raise SystemExit(143)
    run = common.Run("PB-SELFCHECK", "a step interrupted as it spawns", d / "out", [])
    common.subprocess.Popen = Interrupted
    try:
        try:
            run.step("a long step", "exec sleep 300", timeout=60)
        except SystemExit:
            pass
    finally:
        common.subprocess.Popen = real
    time.sleep(0.3)
    left = bool(started) and _running(started[0])
    if left:
        end_tree(started[0])
    check("L1 a step interrupted as its process spawned leaves no process behind", bool(started) and not left, True,
          f"spawned: {started}; still running: {left}")


def owner_distillation() -> None:
    """PB-CLI-INTENT's record check, on copies of the owner's record: the real one passes; a copy whose INTENT.md
    lacks the run's marker, or whose grading sheet leaves a seam without a verdict, fails (task 170 R8.3)."""
    from common import Run
    proj, num, rid = rows_judge.OWNER_DISTILLATION
    src = sorted(proj.glob(f".agent/tasks/{num}-*/intent/{rid}"))
    if not src or not (proj / "INTENT.md").is_file():
        print("skip owner_distillation: the owner's record is not in this checkout")
        return
    for label, doctor, want in (("the record as it is", None, True),
                                ("INTENT.md without the run's marker", "marker", False),
                                ("a seam left without a verdict", "seam", False),
                                # task 170, impl round 1, P1: a seam counts as graded with exactly one box ticked, or
                                # as NOT GRADED only when its verdict says so
                                ("a seam with neither box ticked and no NOT GRADED", "unticked", False),
                                ("a seam with both boxes ticked", "both", False),
                                # P6: the ratification is read in the recorded run's OWN entry, not the task's last one
                                ("the run's entry emptied, another ratified entry appended", "body", False),
                                # Q3: the reader must return the recorded run's OWN entry
                                ("a later entry of the same task after the recorded run's", "later", False)):
        d = tempdir("pb170-od-")
        run_dir = d / src[0].relative_to(proj)
        run_dir.mkdir(parents=True)
        for f in src[0].iterdir():
            (run_dir / f.name).write_bytes(f.read_bytes())
        text = (proj / "INTENT.md").read_text(encoding="utf-8")
        if doctor == "marker":
            text = text.replace(f"<!-- intent:T{num}:{rid} -->", "")
        if doctor == "body":
            mark = f"<!-- intent:T{num}:{rid} -->"
            head, rest = text.split(mark, 1)
            nxt = re.search(r"(?m)^## task ", rest)
            text = head + mark + "\n\n" + (rest[nxt.start():] if nxt else "")
            text = text.rstrip() + (f"\n\n## task {num} · another-run\n<!-- intent:T{num}:another-run -->\n\n"
                                    "- **Confirmed** — something else.\n\nRatified by the owner, another day.\n")
        if doctor == "later":
            text = text.rstrip() + (f"\n\n## task {num} · a-later-run\n<!-- intent:T{num}:a-later-run -->\n\n"
                                    "- **Confirmed** — a later intent.\n\nRatified by the owner, later.\n")
        (d / "INTENT.md").write_text(text, encoding="utf-8")
        sheet = run_dir / "review.md"
        if doctor == "seam":
            sheet.write_text(re.sub(r"(?m)^(- \[x\] aligned / \[ \] drift — verdict:).*$", r"\1", sheet.read_text(
                encoding="utf-8"), count=1), encoding="utf-8")
        if doctor == "unticked":
            sheet.write_text(re.sub(r"(?m)^- \[x\] aligned / \[ \] drift — verdict:", "- [ ] aligned / [ ] drift — verdict:",
                                    sheet.read_text(encoding="utf-8"), count=1), encoding="utf-8")
        if doctor == "both":
            sheet.write_text(re.sub(r"(?m)^- \[x\] aligned / \[ \] drift — verdict:", "- [x] aligned / [x] drift — verdict:",
                                    sheet.read_text(encoding="utf-8"), count=1), encoding="utf-8")
        run = Run("PB-SELFCHECK", "the owner's distillation record", d / "out", [])
        rows_judge._owner_distillation(run, (d, num, rid))
        ok = all(c[1] for c in run.clauses)
        check(f"R8.3 PB-CLI-INTENT's record check — {label}", ok, want,
              "; ".join(f"{'PASS' if c[1] else 'FAIL'} {c[0][:50]}" for c in run.clauses if not c[1]) or "all clauses pass")


HAIKU_ANSWER = ("## Implementation Review Findings\n\nBased on my review of the completed implementation against the "
                "stated Intent (\"Add two numbers correctly in src/calc.py\"):\n\n**Critical: Function implements "
                "subtraction instead of addition**  \nsrc/calc.py:2 — The `add` function returns `a - b` instead of "
                "`a + b`.\n\n**Important: No test coverage for the core functionality**  \nNo test files exist.\n")
CODEX_ANSWER = ("**Critical** — src/calc.py:2 subtracts instead of adding: executing `add(2, 3)` returns `-1`, violating "
                "the stated Intent. Replace `return a - b` with `return a + b`.\n")


def triage_answer() -> None:
    """PB-REVIEW-TRIAGE's reading of a judge's answer (task 170): the planted defect named inside a Critical or
    Important finding, in either form a real judge wrote it (haiku's, verbatim from the final run of 21:32, and
    codex's); not without a severity marker, not when the finding does not name the file."""
    for label, answer, want in (("haiku's real answer (`**Critical: …**`, the file on the next line)", HAIKU_ANSWER, True),
                                ("codex's real answer (`**Critical** — src/calc.py:2 …`)", CODEX_ANSWER, True),
                                ("the same finding with no severity marker",
                                 HAIKU_ANSWER.replace("**Critical: ", "").replace("**Important: ", ""), False),
                                ("a Critical finding that names another file",
                                 "**Critical** — src/other.py:2 returns the wrong value.\n", False)):
        check(f"REVIEW-TRIAGE the judge's answer — {label}", rows_judge.names_the_defect(answer), want)
    check("REVIEW-TRIAGE the OLD reading on haiku's real answer (it failed the run: one line only)",
          re.search(r"\*\*(Critical|Important)\*\*[^\n]*calc\.py", HAIKU_ANSWER) is not None, False)
    # task 170, impl round 2, Q2 (agy, codex): a blank line inside the finding; a finding that only names the file
    for label, answer, want in (
            ("a blank line between the severity heading and the file",
             "**Critical: add() is wrong**\n\nsrc/calc.py:2 returns `a - b` instead of `a + b`.\n", True),
            ("a finding that could not read the file",
             "**Important: Unable to inspect src/calc.py**\nThe sandbox refused the read.\n", False),
            ("a finding about another file whose name ends in calc.py",
             "**Critical** — src/other_calc.py:2 subtracts instead of adding.\n", False),
            # task 170, post-D6 run 1: an unrelated finding on the right file whose words hold "sign"
            ("an unrelated finding on src/calc.py (its `signature`)",
             "**Important** — src/calc.py:1 lacks a docstring describing its signature.\n", False),
            # post-D6 run 2 (codex): a hedge is no detection, and `src/calc.py.bak` is another file
            ("a hedged finding that could not determine the defect",
             "**Important** — Unable to inspect src/calc.py; cannot determine whether add subtracts instead of adding.\n",
             False),
            ("the defect described on src/calc.py.bak", "**Critical** — src/calc.py.bak:2 subtracts instead of adding.\n",
             False),
            ("the defect at the end of a sentence, `src/calc.py.`",
             "**Critical** — add() subtracts instead of adding in src/calc.py.\n", True),
            # the final run of 22:48: haiku wrote the marker in capitals and the file in bold — the product's own
            # finding marker (`post_d6._FINDING_RE`) reads both; so does a list item
            ("haiku's real answer of 22:48 (`**CRITICAL** — **src/calc.py:2** —`)",
             "**CRITICAL** — **src/calc.py:2** — Function `add()` returns `a - b` instead of `a + b`, implementing "
             "subtraction rather than addition as required by the Intent.\n\nCAP: 1/5 reported, exhausted\n", True),
            ("a finding as a list item", "- **Critical** — src/calc.py:2 subtracts instead of adding.\n", True)):
        check(f"REVIEW-TRIAGE (Q2) — {label}", rows_judge.names_the_defect(answer), want)


def seat_answer_bound() -> None:
    """Q6 (task 170, impl round 2, codex): each seat's answer is its OWN log — `judge.log` for claude,
    `judge-codex.log` for codex — written by that step (newer than its start) that exited 0. Replay: claude's
    review left `judge.log`; codex's step then fails and writes nothing — codex must not be certified with
    claude's answer, as the old reading (the newest `judge*.log`) did."""
    d = tempdir("pb170-sa-")
    (d / "judge.log").write_text(CODEX_ANSWER, encoding="utf-8")
    t0 = time.time_ns()
    time.sleep(0.01)
    old = next(iter(sorted(d.glob("judge*.log"), key=lambda p: p.stat().st_mtime_ns, reverse=True)), None)
    check("Q6 the OLD reading certified codex with claude's log (no log of its own, the step failed)",
          bool(old) and rows_judge.names_the_defect(old.read_text(encoding="utf-8")), True)
    seat = getattr(rows_judge, "seat_answer", None)
    got = seat(d, "codex", t0, 1) if seat else "<no seat_answer>"
    check("Q6 a failed codex step with no log of its own gives no answer", got == "", True, repr(got[:40]))
    (d / "judge-codex.log").write_text(CODEX_ANSWER, encoding="utf-8")
    got = seat(d, "codex", t0, 0) if seat else ""
    check("Q6 codex's own fresh log, its step exited 0, is its answer", rows_judge.names_the_defect(got), True)
    got = seat(d, "codex", time.time_ns() + 10**9, 0) if seat else ""
    check("Q6 a log older than the step's start is not its answer", got == "", True)


def double_signal() -> None:
    """Q4 (task 170, impl round 2, codex): two SIGTERMs in a row to a row that is running a step — the gauntlet
    used to send them (`$child` is also in `jobs -p`) — must not abort its cleanup: no process of the step is left.
    Ten trials, a row process each (the real `run_row._exit_on` handler, a real `Run.step`)."""
    lib = Path(__file__).resolve().parent / "lib"
    driver = ("import sys, signal, pathlib\nsys.path.insert(0, sys.argv[1])\nimport common, run_row\n"
              "for s in (signal.SIGTERM, signal.SIGHUP):\n    signal.signal(s, run_row._exit_on)\n"
              "d = pathlib.Path(sys.argv[2])\nr = common.Run('PB-X', 'x', d / 'out', [])\n"
              "r.step('a step', f\"echo $PB_LIVE_STEP > {d}/tok; setsid bash -c 'trap \\\"\\\" TERM; echo $$ > {d}/gc; exec sleep 300' & sleep 300\", timeout=120)\n")
    left_total = 0
    # two signals sent back to back are one (the kernel does not queue a pending SIGTERM): the second is sent a
    # little after the first, so it lands while the first one's cleanup runs
    for gap in (0.002, 0.005, 0.01, 0.02, 0.05) * 2:
        d = tempdir("pb170-ds-")
        (d / "drv.py").write_text(driver, encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-I", str(d / "drv.py"), str(lib), str(d)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            if not _wait_for(d / "gc"):
                left_total += 1
                continue
            p.send_signal(signal.SIGTERM)
            time.sleep(gap)
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            time.sleep(0.3)
            # every process of the step — the grandchild, the step's own `sleep 300`, its shell — by its token
            from common import _carriers
            tok = (d / "tok").read_text().strip() if (d / "tok").exists() else ""
            left_total += any(_running(x) for x in (_carriers(tok) if tok else [])) or not tok
        finally:
            end_tree(p.pid)
            try:
                end_tree(None, (d / "tok").read_text().strip())
            except (OSError, ValueError):
                pass
    check("Q4 two SIGTERMs in a row leave no process of the step behind (10 trials)", left_total == 0, True,
          f"trials with a survivor: {left_total}")


def pre_exec_signal() -> None:
    """Q5 (task 170, impl round 2, codex): a signal that lands while a step is spawning — after the fork, before the
    child carries its token, before `Run.step` holds it — is held until the process is held, then ends it.
    Simulated: a Popen stand-in forks a child WITHOUT the token, the signal lands, and only then does it return."""
    import common
    import run_row
    d = tempdir("pb170-pe-")
    real = common.subprocess.Popen
    started = []

    class Bare:
        def __init__(self, *a, **k):
            pid = os.fork()
            if pid == 0:
                os.execvp("sleep", ["sleep", "300"])      # no token in its environment
            self.pid = pid
            started.append(pid)
            os.kill(os.getpid(), signal.SIGTERM)          # the signal lands before the spawn has returned
            time.sleep(0.2)

        def communicate(self, *a, **k):
            os.waitpid(self.pid, 0)
            return "", ""

        def kill(self):
            os.kill(self.pid, signal.SIGKILL)
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    for s in saved:
        signal.signal(s, run_row._exit_on)
    run = common.Run("PB-SELFCHECK", "a signal during the spawn", d / "out", [])
    common.subprocess.Popen = Bare
    try:
        try:
            run.step("a step", "true", timeout=60)
        except SystemExit:
            pass
    finally:
        common.subprocess.Popen = real
        for s, h in saved.items():
            signal.signal(s, h)
    time.sleep(0.3)
    left = bool(started) and _running(started[0])
    if left:
        end_tree(started[0])
    check("Q5 a signal during the spawn (child without its token) still ends the child", bool(started) and not left,
          True, f"spawned {started}; still running {left}")
    # post-D6 run 1 (codex): the held signal, once raised, must keep the repeated-signal protection — a second
    # SIGTERM that lands while its cleanup runs must not abort it
    started.clear()
    for s in saved:
        signal.signal(s, run_row._exit_on)
    real_end = common.end_tree

    def end_with_second_signal(pid, token=None):
        os.kill(os.getpid(), signal.SIGTERM)           # the second signal lands as the cleanup starts
        time.sleep(0.05)
        return real_end(pid, token)
    run = common.Run("PB-SELFCHECK", "a held signal, then a second one", d / "out2", [])
    common.subprocess.Popen, common.end_tree = Bare, end_with_second_signal
    try:
        try:
            run.step("a step", "true", timeout=60)
        except SystemExit:
            pass
    finally:
        common.subprocess.Popen, common.end_tree = real, real_end
        for s, h in saved.items():
            signal.signal(s, h)
    time.sleep(0.3)
    left = bool(started) and _running(started[0])
    if left:
        end_tree(started[0])
    check("Q5 a held signal, then a second one during its cleanup: the child is still ended", bool(started) and not left,
          True, f"spawned {started}; still running {left}")


def gauntlet_windows() -> None:
    """Two windows the gauntlet's signal handler must cover (task 170, impl round 1), each widened in a copy of
    gauntlet.sh by a `sleep 2` so the signal lands in it every time: (P4) a selfcheck started but not yet held as
    `child`; (P5) a row that wrote PASS and exited 139, before the loop compared its verdict with its exit."""
    # P4
    d = _gauntlet_copy("import sys, pathlib\npathlib.Path(sys.argv[2], sys.argv[1] + '.txt').write_text("
                       "'# stub\\nROW ' + sys.argv[1] + ': PASS (1 clauses)\\n')\n",
                       "import os, sys, time, pathlib\npathlib.Path(sys.argv[1], 'sc.pid').write_text(str(os.getpid()))\n"
                       "time.sleep(60)\n")
    g_sh = d / "gauntlet.sh"
    src = g_sh.read_text()
    spawn = 'python3 -I "$HERE/selfcheck.py" "$OUT" &\n'
    widened = src.replace(spawn, spawn + "sleep 2\n", 1)
    g_sh.write_text(widened)
    out = d / "out"
    g = subprocess.Popen(["bash", str(g_sh), "--out", str(out), "PB-PYTHON-FLOOR"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        ok = widened != src and _wait_for(out / "sc.pid")
        g.send_signal(signal.SIGTERM)
        try:
            rc = g.wait(timeout=30)
        except subprocess.TimeoutExpired:
            rc = None
        time.sleep(0.3)
        try:
            left = _running(int((out / "sc.pid").read_text()))
        except (OSError, ValueError):
            left = False
        check("P4 a SIGTERM before the selfcheck is held as `child` still reaches it", ok and rc == 143 and not left,
              True, f"window widened and selfcheck started: {ok}; rc {rc}; selfcheck still running: {left}")
    finally:
        end_tree(g.pid)
        try:
            end_tree(int((out / "sc.pid").read_text()))
        except (OSError, ValueError):
            pass
    # P5
    d = _gauntlet_copy("import os, sys, pathlib\npathlib.Path(sys.argv[2], sys.argv[1] + '.txt').write_text("
                       "'# stub\\nROW ' + sys.argv[1] + ': PASS (1 clauses)\\n')\nos._exit(139)\n",
                       "import sys\nsys.exit(0)\n")
    g_sh = d / "gauntlet.sh"
    src = g_sh.read_text()
    widened = src.replace("    rc=$?\n", "    rc=$?\n    sleep 2\n", 1)
    g_sh.write_text(widened)
    out = d / "out"
    g = subprocess.Popen(["bash", str(g_sh), "--out", str(out), "PB-PYTHON-FLOOR"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        ok = widened != src and _wait_for(out / "PB-PYTHON-FLOOR.txt")
        time.sleep(0.5)                                # the row has exited; the loop sleeps before its check
        g.send_signal(signal.SIGTERM)
        try:
            rc = g.wait(timeout=30)
        except subprocess.TimeoutExpired:
            rc = None
        check("P5 a SIGTERM after a dead row wrote PASS, before its check, still sets the artifact aside",
              ok and rc == 143 and not (out / "PB-PYTHON-FLOOR.txt").exists(), True,
              f"window widened and artifact written: {ok}; rc {rc}; still there: {(out / 'PB-PYTHON-FLOOR.txt').exists()}")
    finally:
        end_tree(g.pid)


def installed_root() -> None:
    """The installed-root mode (task 185, plan panel A5): with PB_LIVE_ROOT=installed every row runs the copy the
    registry names — never the checkout — and a registry that names no usable copy is refused, not fallen back from."""
    lib = Path(__file__).resolve().parent / "lib"
    probe = "import sys; sys.path.insert(0, sys.argv[1]); import common; print('ROOT', common.ROOT); print('PLUGIN', common.PLUGIN)"
    home = tempdir("pb185-ir-")
    copy = home / "cache" / "playbook" / "9.9.9"
    (copy / "scripts").mkdir(parents=True)
    (copy / "scripts" / "tasks").write_text("#!/bin/bash\n", encoding="utf-8")
    reg = home / ".claude" / "plugins"
    reg.mkdir(parents=True)

    def run(entries, root):
        (reg / "installed_plugins.json").write_text(json.dumps({"plugins": entries}), encoding="utf-8")
        env = dict(os.environ, HOME=str(home), **({"PB_LIVE_ROOT": root} if root else {}))
        env.pop("PB_LIVE_ROOT", None) if not root else None
        return subprocess.run([sys.executable, "-I", "-c", probe, str(lib)], capture_output=True, text=True, env=env)
    r = run({"playbook@m": [{"installPath": str(copy), "version": "9.9.9"}]}, "installed")
    check("installed root: the rows run the copy the registry names", r.returncode == 0
          and f"PLUGIN {copy}" in r.stdout and "ROOT installed" in r.stdout, True, r.stdout.strip().replace("\n", " / "))
    r = run({"playbook@m": [{"installPath": str(home / "missing"), "version": "9.9.9"}]}, "installed")
    check("installed root: a registry whose copy is missing is refused, not fallen back from", r.returncode != 0
          and "installed root" in r.stderr, True, (r.stderr.strip().splitlines() or [""])[-1][:120])
    r = run({"playbook@m": [{"installPath": str(copy)}]}, None)
    check("without --root the rows run this checkout (the candidate)", r.returncode == 0 and "ROOT candidate" in r.stdout
          and str(copy) not in r.stdout, True, r.stdout.strip().replace("\n", " / "))
    # the install rows publish this checkout's HEAD: on the installed root it must be the installed copy's commit
    head = subprocess.run(["git", "-C", str(lib.parent.parent.parent), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    probe_head = "import sys; sys.path.insert(0, sys.argv[1]); import rows_install; rows_install.check_released_head(); print('OK')"
    for sha, want, label in ((head, True, "the installed copy's commit is HEAD: the install rows run"),
                             ("0" * 40, False, "the installed copy's commit is not HEAD: the install rows refuse"),
                             (None, False, "the registry names no commit: the install rows refuse")):
        (reg / "installed_plugins.json").write_text(json.dumps({"plugins": {"playbook@m": [
            {"installPath": str(copy), **({"gitCommitSha": sha} if sha else {})}]}}), encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", "-c", probe_head, str(lib)], capture_output=True, text=True,
                           env=dict(os.environ, HOME=str(home), PB_LIVE_ROOT="installed"))
        check(f"installed root: {label}", r.returncode == 0 and r.stdout.strip() == "OK", want,
              (r.stdout.strip() or (r.stderr.strip().splitlines() or [""])[-1])[:120])


def _gauntlet_copy(run_row: str, selfcheck: str) -> Path:
    d = tempdir("pb184-gs-")
    (d / "lib").mkdir()
    (d / "gauntlet.sh").write_text((Path(__file__).resolve().parent / "gauntlet.sh").read_text())
    (d / "lib" / "run_row.py").write_text(run_row)
    (d / "selfcheck.py").write_text(selfcheck)
    return d


def _wait_for(path: Path, seconds: float = 20) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return True
        time.sleep(0.1)
    return False


def gauntlet_interrupted() -> None:
    """A SIGTERM to the gauntlet (a) reaches a selfcheck that is running (task 184's L2) and (b) sets aside the
    artifact of the row it interrupts, even one that already says PASS (L3)."""
    # (a) the run's selfcheck is a long one that records its pid
    d = _gauntlet_copy("import sys, pathlib\npathlib.Path(sys.argv[2], sys.argv[1] + '.txt').write_text("
                       "'# stub\\nROW ' + sys.argv[1] + ': PASS (1 clauses)\\n')\n",
                       "import os, sys, time, pathlib\npathlib.Path(sys.argv[1], 'sc.pid').write_text(str(os.getpid()))\n"
                       "time.sleep(60)\n")
    out = d / "out"
    g = subprocess.Popen(["bash", str(d / "gauntlet.sh"), "--out", str(out), "PB-PYTHON-FLOOR"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        ok = _wait_for(out / "sc.pid")
        g.send_signal(signal.SIGTERM)
        try:
            rc = g.wait(timeout=30)
        except subprocess.TimeoutExpired:      # the gauntlet did not end on the signal
            rc = None
        time.sleep(0.3)
        try:
            os.kill(int((out / "sc.pid").read_text()), 0)
            left = True
        except (OSError, ValueError):
            left = False
        check("L2 a SIGTERM to the gauntlet reaches the selfcheck it is running", ok and rc == 143 and not left, True,
              f"selfcheck started: {ok}; gauntlet rc {rc}; selfcheck still running: {left}")
    finally:
        end_tree(g.pid)
        try:
            end_tree(int((out / "sc.pid").read_text()))
        except (OSError, ValueError):
            pass
    # (b) the row writes PASS, then is still running when the signal comes
    d = _gauntlet_copy("import sys, time, pathlib\npathlib.Path(sys.argv[2], sys.argv[1] + '.txt').write_text("
                       "'# stub\\nROW ' + sys.argv[1] + ': PASS (1 clauses)\\n')\ntime.sleep(60)\n",
                       "import sys\nsys.exit(0)\n")
    out = d / "out"
    g = subprocess.Popen(["bash", str(d / "gauntlet.sh"), "--out", str(out), "PB-PYTHON-FLOOR"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        ok = _wait_for(out / "PB-PYTHON-FLOOR.txt")
        g.send_signal(signal.SIGTERM)
        try:
            rc = g.wait(timeout=30)
        except subprocess.TimeoutExpired:      # the gauntlet did not end on the signal
            rc = None
        check("L3 the artifact of a row the gauntlet's SIGTERM interrupted is set aside, PASS or not",
              ok and rc == 143 and not (out / "PB-PYTHON-FLOOR.txt").exists()
              and (out / "PB-PYTHON-FLOOR.txt.invalid").exists(), True,
              f"artifact written: {ok}; rc {rc}; still there: {(out / 'PB-PYTHON-FLOOR.txt').exists()}")
    finally:
        end_tree(g.pid)


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
              registry_probe, docker_timeout, gauntlet_missing_and_lock, spawn_interrupted, gauntlet_interrupted,
              owner_distillation, gauntlet_windows, triage_answer, seat_answer_bound, double_signal, pre_exec_signal,
              installed_root):
        f()
    print(f"selfcheck: {'every control holds' if not FAILED else f'{len(FAILED)} control(s) do not hold'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
