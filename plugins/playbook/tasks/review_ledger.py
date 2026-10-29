"""The review ledger: each task's `post-panel-reviews.jsonl` — a review's reservation, taken under the task lock
before it spends anything, and how it ended (moved from post_d6.py by task 343; PLAN-PLAYBOOK-2.md 4.1 a keeps it,
DESIGN-PLAYBOOK-2.md §3.3 point 6: the background review reads it).

A panel and a single judge never run at once on one task: an append under a running review changes the task folder
under its tamper guard. Stdlib only."""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path


RUNS_NAME = "post-panel-reviews.jsonl"


def ledger_corrupt_lines(task_dir: Path) -> int:
    """Lines of the run ledger that are not a JSON object (a truncated write)."""
    try:
        lines = (Path(task_dir) / RUNS_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    bad = 0
    for ln in lines:
        if not ln.strip():
            continue
        try:
            ok = isinstance(json.loads(ln), dict)
        except ValueError:
            ok = False
        bad += 0 if ok else 1
    return bad


def read_runs(task_dir: Path) -> "list[dict]":
    """The run ledger, merged by run id (a reservation completed by its `done`
    record; a `failed` run dropped — it spent nothing countable); legacy lines
    without an id are kept as they are. Corrupt lines are skipped."""
    merged: "dict[str, dict]" = {}
    order: "list[str]" = []
    try:
        lines = (Path(task_dir) / RUNS_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for k, ln in enumerate(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or f"#legacy{k}")
        if rid not in merged:
            merged[rid] = {}
            order.append(rid)
        merged[rid].update(r)
    return [merged[i] for i in order if merged[i].get("status") != "failed"]


def append_run(task_dir: Path, rec: dict) -> None:
    rec = dict(rec)
    rec.setdefault("ts", datetime.datetime.now(datetime.timezone.utc)
                   .isoformat(timespec="seconds"))
    with open(Path(task_dir) / RUNS_NAME, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def _owner_alive(rec: dict) -> "bool | None":
    """True/False when the reservation's owning process can be judged (same host,
    live pid), None otherwise."""
    import socket
    pid = rec.get("pid")
    if not isinstance(pid, int) or rec.get("host") != socket.gethostname():
        return None
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True                      # exists, another user
    except (OSError, OverflowError, ValueError):
        return False


def _live_reservation(runs: "list[dict]", panel: str, stale_after: float) -> str:
    """ts of a reservation for `panel` still in progress (not completed, younger
    than `stale_after` seconds), or ""."""
    now = datetime.datetime.now(datetime.timezone.utc)
    for r in runs:
        # ANY live reservation on the task — another single judge or a panel
        # (round 2: a single judge's append voided a running panel, and vice versa)
        if r.get("status") != "reserved":
            continue
        try:
            ts = datetime.datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        # D2-3: a reservation whose process can be judged is live exactly while that
        # process runs (an unlimited review has no expiry to go by); otherwise its
        # own recorded horizon (or the caller's, whichever is longer) decides.
        alive = _owner_alive(r)
        if alive is not None:
            if alive:
                return f'{r.get("kind") or "single-judge"} run {r.get("id")} reserved {r.get("ts")}'
            continue
        # D3-2: the reservation's OWN recorded horizon decides; the caller's timeout
        # only for a legacy record without one (a longer caller must not extend it).
        horizon = r.get("expires_after")
        horizon = float(horizon) if isinstance(horizon, (int, float)) else stale_after
        if (now - ts).total_seconds() < horizon:
            return f'{r.get("kind") or "single-judge"} run {r.get("id")} reserved {r.get("ts")}'
    return ""


def _in_progress_msg(task_file: Path, live: str) -> str:
    rid = live.split(" run ", 1)[1].split(" ", 1)[0] if " run " in live else "<id>"
    return (f"another review on this task is in progress ({live}); wait for it — "
            "concurrent reviews on one task void each other's tamper guard. If that "
            f"process is dead, append the line {{\"id\": \"{rid}\", \"status\": \"failed\"}} "
            f"to {Path(task_file).parent / RUNS_NAME}; a reservation older than the hard "
            "timeout + 10 min counts as spent and no longer blocks.")


def _owner_fields() -> dict:
    import socket
    return {"pid": os.getpid(), "host": socket.gethostname()}


class PanelRefused(Exception):
    """The `gate` passed to `reserve_panel` refused the panel (task 209: the D6 round cap); the text says why."""


def reserve_panel(task_file, stale_after: float = 7200, *, mode: "str | None" = None,
                  gate=None, record: "dict | None" = None) -> "tuple[bool, str]":
    """A panel's reservation (round 2): taken BEFORE the panel's tamper snapshot so
    a concurrent single judge cannot append under it; never counted by the cap.
    Task 209: `gate()` runs under the same lock, after the in-progress check — a
    non-empty answer raises `PanelRefused` with it, nothing written; `record` adds
    fields to the reservation (the risk and the caps the close reads)."""
    import secrets
    from tasks.filelock import task_lock
    task_file = Path(task_file)
    with task_lock(task_file):
        if _live_reservation(read_runs(task_file.parent), "", stale_after):
            return False, ""
        refusal = gate() if gate is not None else ""
        if refusal:
            raise PanelRefused(refusal)
        rid = secrets.token_hex(6)
        extra = {"mode": mode} if mode else {}
        append_run(task_file.parent, {"id": rid, "kind": "panel", "status": "reserved",
                                      "expires_after": float(stale_after), **_owner_fields(),
                                      **(record or {}), **extra})
    return True, rid


def finish_panel(task_dir: Path, rid: str, ended: "dict | None" = None) -> None:
    """The panel's `done` record. Task 232 (PHASE-B item 6): `ended` says how the panel ended — `outcome` and, for a
    completed round, its `measures` (written by `review.cmd_panel_review`); no other key is taken from it, and the
    keys the ledger's readers use (`status`, `kind`, `panel`, `mode`, `risk`, …) are never among them."""
    from tasks.filelock import task_lock
    rec = {"id": rid, "kind": "panel", "status": "done"}
    rec.update({k: v for k, v in (ended or {}).items() if k in ("outcome", "measures")})
    with task_lock(Path(task_dir) / "task.md"):
        append_run(task_dir, rec)


def panel_refusal(task_file) -> str:
    live = _live_reservation(read_runs(Path(task_file).parent), "", 10 ** 9)
    return _in_progress_msg(Path(task_file), live or "a reservation")


def reserve_run(task_file, stale_after: float = 7200) -> "tuple[bool, str, str]":
    """RESERVE a single judge's run under the task lock, before the judge is spawned (task 108 r1: a check made after
    the judge let two runs both pass): (allowed, refusal, run id). Refused, with nothing written, only while another
    review — a single judge or a panel — holds a live reservation on the task (task 108 run 2: its append would change
    the task folder under the first run's tamper guard and void it). No cap (task 343, PLAN-PLAYBOOK-2.md 4.1 a: the
    post-panel judge's two runs went with it). `fail_run` releases a run that produced no review."""
    import secrets
    from tasks.filelock import task_lock
    task_file = Path(task_file)
    with task_lock(task_file):
        live = _live_reservation(read_runs(task_file.parent), "", stale_after)
        if live:
            return False, _in_progress_msg(task_file, live), ""
        rid = secrets.token_hex(6)
        append_run(task_file.parent, {"id": rid, "kind": "single", "status": "reserved",
                                      "expires_after": float(stale_after), **_owner_fields()})
    return True, "", rid


def finish_run(task_dir: Path, rid: str, rec: dict) -> None:
    from tasks.filelock import task_lock
    with task_lock(Path(task_dir) / "task.md"):
        append_run(task_dir, {**rec, "id": rid, "status": "done"})


def fail_run(task_dir: Path, rid: str) -> None:
    from tasks.filelock import task_lock
    with task_lock(Path(task_dir) / "task.md"):
        append_run(task_dir, {"id": rid, "status": "failed"})
