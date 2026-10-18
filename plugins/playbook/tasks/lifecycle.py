"""The work lifecycle — activation, switching, and THE CLOSE PATH — plus
task creation and the honest pause states.

Boundary: every command that moves a task through its life. `work <N>`
(activation, blocked-resume, reopen/re-adopt, stub expansion, chat capture,
switch-bounce — `--force` leaves a task honestly open, never silently done)
and `work done`, the ONLY writer of `done` (the 1.4.7 principle): the verify
contract via shared's `_merge_verify_module`, the risk-keyed evidence bar,
the F18 assertive/irreversible freshness gate, the receipt, dirty-close honesty, parked
surfacing, the retro trigger. Plus `new` (creation + stubs), `blocked` (the
honest pause), `parked` (the standing query), and `freehand`. All policy
DECISIONS live in tasks.core (`close_decision`, `freshness_gate_decision`,
gate parsers); this module wires them to the console. Imports stdlib +
tasks.core + tasks.shared + tasks.template; never a command module
(design-1.5.9.md §4).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from tasks.atomic import atomic_write
from tasks.core import (
    PLAYBOOKS, _atomic_write, _find_playbook_skill, _rewrite, create_task,
    read_task_text, require_session_id, resolve_agent_dir,
)
from tasks.shared import _merge_verify_module, find_project_root


def _state_file(project_path: Path) -> Path:
    """Return per-session state file under .agent/sessions/<id>/current_state.

    Creates the session dir, so it is a WRITER: an unresolved id (task 105 —
    under the background daemon with no PLAYBOOK_SESSION_ID) exits via
    require_session_id instead of composing `sessions/""`."""
    session_id = require_session_id()
    state_dir = resolve_agent_dir(project_path) / "sessions" / session_id
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "current_state"


def _record_transition(agent_dir: Path, event: str, task, detail: str = "") -> None:
    """The CLI's own record of a transition that HAPPENED — `work` (task activated), `new` (task made), `done`
    (task closed) — in the plugin's journal (`pb_journal.append_lifecycle`, task 221). The task windows of retro,
    timeline, tagger, tag and intent are read from these records: the shell log, written by a hook, holds whole
    command lines, and a line is not an event. Best-effort and fully wrapped, like the close's own journal line: a
    journal failure never changes or breaks the command."""
    try:
        import importlib.util as _ilu
        from tasks.core import resolve_session_id
        path = Path(__file__).resolve().parent.parent / "scripts" / "pb_journal.py"
        spec = _ilu.spec_from_file_location("_pb_journal_transition", path)
        if spec is None or spec.loader is None:
            return
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.append_lifecycle(agent_dir, event=event, task=str(task), detail=detail,
                             session_id=resolve_session_id(quiet=True))
    except Exception:
        pass


def _record_agent_usage(agent_dir: Path, task, session_dirs) -> None:
    """Task 244 (PHASE-B item 6; design v2 of its plan round 1): the main agent's tokens up to now, recorded under
    `task` — the task a transition leaves (the close's own, or the one `tasks work` switches from; "-" for none) — before
    the transition is written, so a recorded span never crosses one. Reads each session dir's `transcript_path` pointer
    (session-start-hook writes it) and hands the files to `agent_usage.record_spans`, which holds the lane lock.
    Best-effort and fully wrapped, like `_record_transition`: a failure never changes or breaks the command."""
    try:
        import importlib.util as _ilu
        from tasks.core import resolve_session_id
        scripts = Path(__file__).resolve().parent.parent / "scripts"

        def _load(name, file):
            spec = _ilu.spec_from_file_location(name, scripts / file)
            if spec is None or spec.loader is None:
                raise ImportError(file)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        au = _load("_agent_usage_cli", "agent_usage.py")
        paths = [p for p in (au._pointer(str(agent_dir), Path(d).name) for d in session_dirs) if p]
        if not paths:
            return
        pbj = _load("_pb_journal_agent_cli", "pb_journal.py")
        sid = resolve_session_id(quiet=True) or ""
        res = au.record_spans(str(agent_dir), task=str(task or "-"), paths=paths, session_id=sid, pbj=pbj,
                              lock_wait=5.0)
        if res.get("skipped") == "lock":
            # impl round 2 (opus, sonnet): never a silent gap — the span up to here goes to the next reader's task,
            # so this one is marked as not counted, and said
            marked = pbj.append_agent_usage(agent_dir, task=str(task or "-"), known=False, session_id=sid,
                                            why="the lane lock was busy at this transition: the span up to it was "
                                                "not counted here")
            # task 246 (the post-panel judge of 244): say what was written, never a marker that did not land
            print("agent tokens: not recorded at this transition (the lane lock was busy) — "
                  + ("marked unknown in the numbers" if marked else
                     "and the unknown marker could not be written either: this closure's agent numbers miss that "
                     "span unmarked"), file=sys.stderr)
    except Exception:
        pass


def _sessions_on_task(agent_dir: Path, task) -> list:
    """The session dirs of the lane whose task pointer names `task` (not through links) — the sessions a close of
    `task` reads before it removes them."""
    out = []
    try:
        sdir = agent_dir / "sessions"
        if sdir.is_symlink() or not sdir.is_dir():
            return out
        for d in sorted(sdir.iterdir()):
            if d.is_symlink() or not d.is_dir():
                continue
            st = d / "current_state"
            if st.is_symlink() or not st.is_file():
                continue
            if st.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0].strip() == str(task):
                out.append(d)
    except OSError:
        pass
    return out


# How much of one chat message `### Recent Chat` keeps. The judge prompt that
# describes those excerpts (`template._intent_check`) reads this same number. The
# chat log itself keeps up to 50,000 characters of a message, so "whole" is no
# bound for a task record that judges read through a budget.
RECENT_CHAT_CUT = 1000


def _capture_recent_chat(project_path: Path, max_messages: int = 10,
                         max_gap_seconds: int = 10800) -> list[str]:
    """Capture recent chat_log messages for task attribution.

    Scans backwards from end of chat_log.md. Stops at:
    - Previous 'tasks done' or 'tasks work done' in message text
    - A time gap > max_gap_seconds (default 3h) between consecutive messages
    - max_messages reached (default 10)

    Returns list of message blocks (most recent last), each as a header line
    "**[MNNN]** [timestamp]" followed by the text INSIDE A CODE FENCE — whole up
    to `RECENT_CHAT_CUT` characters; a longer one is cut there and says how much
    was left out and under which message id the whole text is (task 166).

    The fence is what keeps a message from being read as part of the task: these
    blocks are written into task.md above its gates, and a checkbox, a heading or
    an unclosed code fence in the owner's words used to become a live gate, a
    live section, or a fence over everything below (impl panel r1). The fence is
    one backtick longer than the longest run of backticks in the text, so nothing
    in the text can close it.
    """
    import re
    from datetime import datetime

    chat_log = resolve_agent_dir(project_path) / "chat_log.md"
    if not chat_log.exists():
        return []

    content = chat_log.read_text(encoding="utf-8", errors="replace")
    # Split into message blocks on --- separator
    # `\w+`[^\n]* (not `\w+`\s*\n): the producer appends a ` (provider/pid)`
    # suffix to the header since 1.4.3, so anchoring the backtick-host tag right
    # before the newline made this parser DEAD on every modern entry (I12).
    # Consume the rest of the header line, matching both legacy and modern.
    # Task 233: the header starts a line (a header-shaped text inside an entry is the entry's text), and its tag is
    # kept — an options-dialog entry (`DIALOG`) holds the agent's question beside the user's answer.
    msg_pattern = re.compile(
        r'(?m)^\*\*\[(M\d+)\]\*\*\s+\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]\s+`(\w+)`[^\n]*\n\s*\n(.*?)(?=\n---|\Z)',
        re.DOTALL
    )

    messages = []
    for m in msg_pattern.finditer(content):
        msg_id = m.group(1)
        timestamp_str = m.group(2)
        tag = m.group(3)
        text = m.group(4).strip()
        try:
            ts = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        messages.append((msg_id, ts, timestamp_str, text, tag))

    if not messages:
        return []

    # Scan backwards
    captured = []
    prev_ts = None
    for msg_id, ts, ts_str, text, tag in reversed(messages):
        # Stop at time gap
        if prev_ts is not None:
            gap = (prev_ts - ts).total_seconds()
            if gap > max_gap_seconds:
                break
        prev_ts = ts

        # Stop at task-done marker — in the user's own words: of an options-dialog entry, only its answer lines (its
        # Q lines are the agent's, which may well name `tasks work done` — plan panel, codex's probe; task 233)
        said = text
        if tag == "DIALOG":
            from tasks.core import dialog_answer_text
            said = dialog_answer_text(text)
        text_lower = said.lower()
        if "tasks done" in text_lower or "tasks work done" in text_lower:
            break

        # A long message is cut — and says so. At 200 characters, mid-sentence and
        # with a bare "...", three of four of the owner's messages reached the task
        # record without their end (PLAN S11 item 9, task 166).
        if len(text) > RECENT_CHAT_CUT:
            display_text = (f"{text[:RECENT_CHAT_CUT]}… [cut here: {len(text) - RECENT_CHAT_CUT:,} "
                            f"more characters — the whole message is {msg_id} in chat_log.md]")
        else:
            display_text = text
        fence = "`" * (max([3] + [len(run) + 1 for run in re.findall(r"`+", display_text)]))
        captured.append(f"**[{msg_id}]** [{ts_str}]\n{fence}text\n{display_text}\n{fence}")

        if len(captured) >= max_messages:
            break

    # Reverse to chronological order
    captured.reverse()
    return captured


def _inject_chat_into_task(task_file: Path, messages: list[str]) -> None:
    """Inject captured chat messages into task.md References section."""
    if not messages:
        return

    import re

    def _utf8_safe(text: str) -> str:
        """Replace non-UTF-8-survivable code points like lone surrogates."""
        return text.encode("utf-8", errors="replace").decode("utf-8")

    chat_block = "\n### Recent Chat (auto-captured at activation — review and remove unrelated)\n"
    for msg in messages:
        chat_block += f"\n{_utf8_safe(msg)}\n"

    def _t(content: str) -> "str | None":
        # Insert before the first `---` line (end of the References section, before
        # the Design Phase) — the first one OUTSIDE a code fence. A captured message
        # is written inside a fence, and one whose text is `---` would otherwise be
        # taken for the anchor at the next activation: the old block was then cut
        # inside its own fence, leaving the closing fence open over the whole task
        # (task 166, impl panel r2). A record with an unclosed fence gives no
        # anchor — nothing is written into it.
        from tasks.core import _iter_nonfenced
        lines = content.split("\n")
        sep = next((i for i, _s in _iter_nonfenced(lines) if i > 0 and lines[i] == "---"
                    and i + 1 < len(lines)), None)
        if sep is None:
            return None                            # no anchor → nothing to write
        first_sep = len("\n".join(lines[:sep]))    # the offset of that line's "\n---\n"
        references = content[:first_sep]
        references = re.sub(
            r'\n### Recent Chat \(auto-captured at activation — review and remove unrelated\)\n.*\Z',
            "",
            references,
            flags=re.DOTALL,
        )
        return _utf8_safe(references.rstrip() + "\n" + chat_block + content[first_sep:])

    _rewrite(task_file, _t)   # task 058: one locked transaction


def _dirty_outside(project_path, task_dir) -> int:
    """Dirty or untracked files (`git status --porcelain -z`, every untracked file
    listed) that are NOT inside `task_dir` — the closing task's own records do not
    count (task 128). The project's repo, plus each `code_roots` checkout that is its
    own repo (task 138 G4-2: a gitignored nested checkout holds the verified code the
    warning is for). 0 for a repo git cannot answer for."""
    try:
        own = Path(task_dir).resolve()
    except OSError:
        own = Path(task_dir)
    try:
        top = _git_out(project_path, "rev-parse", "--show-toplevel").strip()
    except (OSError, subprocess.SubprocessError):
        return 0
    if not top:
        return 0
    count = _dirty_in(top, own)
    try:
        from tasks.core import _code_roots, load_config
        roots = _code_roots(load_config(Path(project_path)))
    except Exception:
        roots = []
    seen = {Path(top).resolve()}
    for rel in roots:
        root = Path(project_path) / rel
        try:
            ans = _git_out(root, "rev-parse", "--show-toplevel").strip()
            if not ans:
                continue            # git cannot answer here — Path("") would be the cwd
            rtop = Path(ans).resolve()
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        # each repo once — a plain directory of the outer repo resolves to the outer
        # repo, already counted
        if rtop in seen:
            continue
        seen.add(rtop)
        count += _dirty_in(str(rtop), own)
    return count


def _git_out(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout


def _dirty_in(top: str, own: Path) -> int:
    """Entries of `git status --porcelain -z` in the repo at `top` that touch a path
    outside `own`. A rename or copy counts when EITHER end is outside (task 138 G4-1:
    a file moved into the task's directory left the project)."""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"],
                             cwd=top, capture_output=True).stdout
    except (OSError, subprocess.SubprocessError):
        return 0

    def outside(path: str) -> bool:
        # porcelain paths are relative to the repository root, even from a
        # subdirectory (measured, task 138)
        try:
            return not (Path(top) / path).resolve().is_relative_to(own)
        except (OSError, ValueError):
            return True

    fields = out.decode("utf-8", errors="surrogateescape").split("\0")
    count, i = 0, 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        status, paths = entry[:2], [entry[3:]]
        if "R" in status or "C" in status:
            if i < len(fields):
                paths.append(fields[i])  # the rename/copy source follows as its own field
            i += 1
        if any(outside(p) for p in paths):
            count += 1
    return count


def _gate_bounce(task_id: str, task_file, action: str) -> bool:
    """If `task_file` has open (unchecked) gates, print a steering message and
    return True (the caller should abort). Returns False when all gates are
    checked. The `--force` decision is the caller's — this only reports.
    """
    from tasks.core import _extract_head_position, _live_gate_scan, _physical_lines
    # The decision and the count come from THE gate reader (`_live_gate_scan`, what the hooks
    # read): a line that is not a live gate — a `- **X**…:` lead-in, a fenced `- [ ]` example —
    # never refuses a close (task 190, owner ruling (a) of 2026-10-13: "0 open gate(s) … Next
    # open gate: - **Ceva**, în scratch:"). Unreadable or unscannable → an unknown count, and refuse.
    try:
        open_count = _live_gate_scan(_physical_lines(
            read_task_text(task_file)))[0]
    except Exception:
        open_count = None
    if open_count == 0:
        return False
    head = _extract_head_position(task_file)
    print(
        f"Blocked: task {task_id} has {'?' if open_count is None else open_count} open gate(s) — "
        f"{action} needs them finalized.",
        file=sys.stderr,
    )
    print(f"  Next open gate: {head}", file=sys.stderr)
    print(
        "  Finish them (check the boxes in task.md), then retry — or override with --force.",
        file=sys.stderr,
    )
    return True


def _unknown_dirs_bounce(project_path, agent_dir) -> bool:
    """If the lane holds a task folder the CLI did not make — not registered (a `reserved` line not counting, a
    creation in progress skipped), not committed: `registry.unknown_now`, the reader the watch uses — print what and
    the way out, and return True (the caller aborts). The owner's "la închidere" (2026-10-13 12:27, task 196 W4): the
    watch sees a folder made during a session's shell call or Write; the close sees every folder of the lane, however
    it was made. No registry → the git rule: what is not committed is unknown (task 197, its plan panel P4; until then
    a missing registry read as nothing). An unreadable registry refuses,
    naming its repair (P9: the watch never holds on it, so the close is where it stops)."""
    from tasks.registry import (LaneUnreadable, RegistryUnreadable, registry_path, standing_before, unknown_now,
                                unreadable_message)
    try:
        # a missing registry is no pass: what is not committed is unknown (task 197, its plan panel P4); a folder
        # enrolled during a watched call still running — any session's — was made in it (impl panel r2 J5; K3)
        left = unknown_now(project_path, agent_dir, no_registry="git", before=standing_before(agent_dir))
    except RegistryUnreadable as e:
        print(f"Blocked: {unreadable_message(e)}", file=sys.stderr)
        print("  The close checks this lane's task folders against the task registry. Nothing changed.",
              file=sys.stderr)
        return True
    except LaneUnreadable as e:
        print(f"Blocked: this lane's task folders cannot be read ({e}) — the close checks each one. Restore the "
              "folder's permissions and re-run `tasks work done`. Nothing changed.", file=sys.stderr)
        return True
    if not left:
        return False
    try:
        rel = Path(agent_dir).relative_to(project_path).as_posix()
    except ValueError:
        rel = str(agent_dir)
    print("Blocked: this lane holds task folder(s) the playbook CLI did not make (the task registry does not name "
          "them and they are not committed):", file=sys.stderr)
    for n in left:
        print(f"  {rel}/tasks/{n!r}" if any(ord(c) < 32 for c in n) else f"  {rel}/tasks/{n}", file=sys.stderr)
    if not os.path.lexists(registry_path(agent_dir)):
        print("  (The lane's task registry is gone: what is not committed is unknown — `tasks adopt <folder>` writes a "
              "new registry.)", file=sys.stderr)
    print("  Keep one:   .claude/bin/tasks adopt <folder>   (it must hold a task.md and a free number)\n"
          "  Remove one: rm -r <folder>   (removing deletes its content — it may be someone's work)\n"
          "  Then retry `tasks work done` — or override with --force --reason. Nothing changed.", file=sys.stderr)
    return True


def _prepare_pointer(session_state: Path, task_num: str) -> Path:
    """Create the session directory and a prepared copy of the pointer beside it
    (through the one atomic writer). Raises OSError — before anything else is
    written; the caller renames the copy into place or removes it."""
    session_state.parent.mkdir(parents=True, exist_ok=True)
    tmp = session_state.with_name(f".current_state.{os.getpid()}.tmp")
    atomic_write(session_state.with_name(tmp.name), f"{task_num}\n")
    return tmp


def _activate_status_then_publish(task_file, task_num, resume_target, reopen_target,
                                  pointer_tmp: Path, session_state: Path) -> None:
    """The activation is allowed: clear a block / reopen a done task / claim the task
    — each decided inside its locked transform, since the status may have moved since
    it was read — and only then publish the prepared pointer (one rename). A refusal
    exits before the rename: the session points at nothing new."""
    if resume_target is not None:
        from tasks.core import resume_blocked_task
        try:
            resume_blocked_task(task_file)
        except ValueError as exc:       # V7c: a refusal, not a traceback
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Resuming task {task_num} (was blocked — decision made).")
    if reopen_target is not None:
        from tasks.core import reopen_done_task
        # Through the ONE fence-aware status path (V7, task 043); False = the task
        # is no longer `done` (or its live `## Status` is gone) — refuse.
        if not reopen_done_task(task_file):
            print(f"Error: could not reopen task {task_num} — its status is no longer "
                  "`done` (another writer changed it first); nothing changed.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"Note: task {task_num} was marked done — reopening.")

    # Task 140 (owner Q10): an activated task says so — `pending` → `in_progress`.
    # Decided under the task lock, and BEFORE the pointer: a task another session
    # closed or paused after the lookup is not activated (impl panel r2).
    from tasks.core import claim_for_activation
    moved = claim_for_activation(task_file)
    if moved is not None:
        how = (f"`tasks work {task_num} --reopen` reopens it" if moved.startswith("done")
               else f"run `tasks work {task_num}` again to resume it")
        print(f"Error: task {task_num} became `{moved}` while this activation ran "
              f"(another session changed it) — {how}. Nothing changed.", file=sys.stderr)
        sys.exit(1)

    # Publish (atomic: a hook may read this concurrently; current_state carries no
    # lock protocol — only os.replace changes it). The file exists and sits in the
    # same directory, so this rename needs no new space.
    os.replace(pointer_tmp, session_state)


def cmd_work(cmd_args):
    """The `tasks work` arm — body moved verbatim from cli.py (1.5.9 split)."""
    if not cmd_args:
        print("Error: 'work' requires a task number or 'done'", file=sys.stderr)
        print("Usage: tasks work <number> | tasks work done", file=sys.stderr)
        sys.exit(1)

    task_num = cmd_args[0]
    if task_num != "done" and task_num.isdigit():
        task_num = task_num.zfill(3)
    force = any(a in ("--force", "-f") for a in cmd_args[1:])
    # --stale-panel-ok: the NARROW exit from the F18 freshness gate (assertive
    # OR irreversible, since T1) — suppresses only that gate, only with a --reason, and the
    # reason is recorded in the receipt's freshness clause. Deliberately
    # not folded into --force: batch-5 field data shows agents take the
    # cheapest sanctioned exit under friction, and the cheap exit must not
    # be whole-policy bypass.
    stale_panel_ok = "--stale-panel-ok" in cmd_args[1:]
    # --owner-ok --reason "…" (PLAN S12b, task 149): where the workspace binds a
    # stale close to a post-D6 PASS (core.bound_stale_close), the owner's
    # recorded decision is the other way through. An accountability marker, not
    # an authentication: it goes to the receipt and the enforcement journal.
    owner_ok = "--owner-ok" in cmd_args[1:]
    if owner_ok:
        # the owner's recorded decision is at least a stale-panel acceptance
        stale_panel_ok = True
    # --reason "why": required for any forced close so the escape hatch leaves
    # a trace (the 046 fix). Stored in the verification receipt. Shared by
    # --stale-panel-ok; when BOTH --force and --stale-panel-ok appear, the
    # reason is attributed to force (the stronger hatch — judge F5).
    reason = None
    for _i, _a in enumerate(cmd_args):
        if _a == "--reason" and _i + 1 < len(cmd_args):
            _val = cmd_args[_i + 1]
            # I11: don't swallow the NEXT FLAG as the reason —
            # `--reason --force` used to force-close with the reason literally
            # "--force", satisfying "a forced close must record why" with a
            # flag name. A real reason is prose; a `--`-prefixed token is
            # another flag, so leave reason unset (the force gate then blocks).
            # Task 138 G1-6: a short flag (`-f`) is not a reason either.
            if not _val.startswith("-"):
                reason = _val
            break
    project_path = find_project_root()

    # Task 116 (gauntlet 2 G2-12): a hatch without its reason is refused BEFORE the close
    # runs the verify contract — it used to run the whole verify and only then say so.
    # `cli._wrong_usage` refuses it even earlier, before the session GC; this is the
    # backstop for an in-process caller, and the close policy keeps the rule too.
    if task_num == "done" and (force or stale_panel_ok or owner_ok) and not (reason and reason.strip()):
        hatch = "--force" if force else ("--stale-panel-ok" if stale_panel_ok else "--owner-ok")
        print(f'Error: {hatch} requires --reason "why" — a forced or stale-panel close must '
              "record why. Nothing changed.", file=sys.stderr)
        sys.exit(1)

    # Handle 'tasks work done' - deactivate current task and set Status in task.md
    if task_num == "done":
        agent_dir = resolve_agent_dir(project_path)
        session_id = require_session_id()   # task 105: never `sessions/""`
        session_state = agent_dir / "sessions" / session_id / "current_state"

        # Find the active task from session state file
        prev_task = session_state.read_text(encoding="utf-8", errors="replace").strip() if session_state.exists() else None

        if prev_task:
            # Set ## Status to done in task.md
            tasks_dir = agent_dir / "tasks"
            # N2: task pointers are numeric — a non-digit value (incl. a glob
            # metacharacter like `*`, which would otherwise match a real task
            # dir and close the WRONG task) is not a valid pointer. Force the
            # non-resolving path below (fail loud, change nothing).
            matches = (list(tasks_dir.glob(f"{prev_task}-*/task.md"))
                       if prev_task.isdigit() else [])
            if not matches:
                # C1: the pointer names a task whose `NNN-*` folder does not
                # resolve — a renamed/deleted folder, a wrong-lane pointer, or
                # the C1b substring bug that wrote a raw non-padded pointer.
                # The close path used to keep going: it printed "Task X done.",
                # wiped every session dir pointing at the pointer, never wrote
                # `## Status`, then crashed reading the unbound `task_file`.
                # Resolve to a REAL task before ANY destructive step; if it
                # does not resolve, fail loud and change NOTHING — an
                # autonomous agent must never be told finished work is done.
                print(f"Error: active task pointer '{prev_task}' does not "
                      f"resolve to a task under {tasks_dir} — refusing to "
                      "close. Nothing changed (no status write, no session "
                      "wipe). Re-activate a real task with `tasks work <N>`.",
                      file=sys.stderr)
                sys.exit(1)
            # Guaranteed non-empty here (the guard above exits otherwise).
            # Bind before the (now always-true) `if matches:` wrapper — kept to
            # avoid reindenting the whole close body — so nothing downstream can
            # read an unbound `task_file` (the original C1 crash).
            task_file = matches[0]
            # Task 196 W4: a hand-made task folder in this lane refuses the close, before anything is read or written
            # for it (`--force --reason` overrides, as for the gates).
            if not force and _unknown_dirs_bounce(project_path, agent_dir):
                sys.exit(1)
            # V7b (task 043, round-1 panel): a close whose `done` cannot land
            # must not run verify, write a receipt, clear the session and print
            # "done" — preflight the live `## Status` value line FIRST and refuse
            # with nothing changed (the same land-together rule set_task_blocked
            # applies).
            from tasks.core import _live_status_index, _physical_lines
            try:
                # Physical `\n` lines — the SAME line model the writer (`_set_status`)
                # uses, so the preflight cannot pass a `prose\x85## Status\x85pending`
                # line the writer then refuses (task 055, round-3 panel).
                _st_lines = _physical_lines(read_task_text(task_file))
            except OSError:
                _st_lines = []
            # Task 058: remember the status this close was AUTHORISED on, so the
            # final transaction can refuse if it changed while verify/judges ran.
            from tasks.core import _extract_status as _es058
            from tasks.core import _gate_counts as _gc058
            from tasks.core import blocked_digest as _bd058
            from tasks.core import judge_digest as _jd058
            try:
                _entry_text = read_task_text(task_file)
                _status_at_entry = _es058(task_file)
                _blocked_at_entry = _bd058(_entry_text)
                _chk, _tot = _gc058(_entry_text)
                _open_gates_at_entry = _tot - _chk
                _judge_at_entry = _jd058(task_file)
            except Exception as _snap_err:      # noqa: BLE001
                # A close whose baseline cannot be captured must ABORT, not
                # proceed with its safety comparisons disabled (impl panel r3,
                # codex-high #2 — which corrected r2's "skip rather than assert a
                # false baseline": skipping ALSO lets a concurrent pause be
                # overwritten as `done`). Nothing has been written at this point.
                print(f"Blocked: cannot close task {prev_task} — task.md could not be "
                      f"read to capture the state this close is authorised on "
                      f"({_snap_err}). Nothing was written; fix the file and re-run "
                      "`tasks work done`.", file=sys.stderr, flush=True)
                sys.exit(1)
            if _live_status_index(_st_lines) is None:
                print(f"Error: {task_file} has no live `## Status` heading with a "
                      "value line (missing, hidden inside a code fence, or directly "
                      "followed by another section) — refusing to close: `done` "
                      "could not be recorded. Fix the heading/fence, then re-run "
                      "`tasks work done`. Nothing changed.", file=sys.stderr)
                sys.exit(1)
            if matches:
                if not force and _gate_bounce(prev_task, task_file, "closing this task"):
                    sys.exit(1)
                # Belt to gate-bounce's suspenders (#09): head-position can be
                # fooled by a tricky line while the honest, line-anchored COUNT
                # still shows open gates — the 71/74-yet-"all-checked" symptom.
                # Refuse on the count too, so no single parser's blind spot can
                # close a task with open gates.
                if not force:
                    from tasks.core import _gate_counts
                    try:
                        _chk, _tot = _gate_counts(read_task_text(task_file))
                    except OSError:
                        _chk, _tot = 0, 0
                    if _tot and _chk < _tot:
                        print(f"Blocked: task {prev_task} shows {_chk}/{_tot} gates "
                              f"checked — {_tot - _chk} still open by the line-anchored "
                              "count. Finish them, or override with --force --reason.",
                              file=sys.stderr)
                        sys.exit(1)
                # Evidence contract + consequence gate (P1 / P2). Close is no
                # longer "write the string done": run the project's declared
                # verify contract, record a receipt, and refuse to close on a
                # failing verify or an unreviewed high-consequence change —
                # unless forced with a recorded reason.
                from tasks.core import (
                    close_decision, extract_risk, format_verify_receipt,
                    has_panel_impl_evidence, has_review_evidence, has_risk_section,
                    resolve_panel_required, resolve_verify_commands,
                    resolve_verify_timeout,
                )
                risk = extract_risk(task_file)
                # Whether the gate was OFFERED, not just what it says: a missing
                # `## Risk` heading is a pre-1.5.0 task, a present-but-unset one
                # is a skipped gate. close_decision holds only the second to the
                # high-consequence bar.
                risk_offered = has_risk_section(task_file)
                commands = resolve_verify_commands(project_path, risk)
                entries = []
                verify_failed = False
                if commands:
                    mv = _merge_verify_module()
                    if mv is None:
                        print("Error: cannot load the verify runner "
                              "(skills/merge/merge-verify.py missing).", file=sys.stderr)
                        sys.exit(1)
                    _vt = resolve_verify_timeout(project_path)
                    print(f"Verifying close ({len(commands)} declared command(s), risk={risk})...",
                          flush=True)
                    # Task 228 (PHASE-B.md item 3; the owner, 2026-10-16 09:42 "Ține minte ultima verde"): a command
                    # whose green run is remembered for exactly this code is not run again; every run is recorded
                    # right after it ran — before the red exit below and before the later gates.
                    from tasks import verify_memory as _vm
                    for label, cmd, rc, output, how in _vm.run_contract(
                            project_path, commands, _vt,
                            lambda _c: mv.run_command_capture(_c, str(project_path), timeout_secs=_vt),
                            on_result=lambda _r: _vm.print_line(_r[0], _r[1], _r[2], _r[4]),
                            task=str(prev_task)):           # task 232: each run journaled for this task
                        entries.append((label, cmd, rc, output))
                        if rc != 0:
                            verify_failed = True
                else:
                    print(f"  (no verify contract declared — NOTHING verified at close; risk={risk})",
                          file=sys.stderr, flush=True)
                if verify_failed and not force:
                    # Task 073 (C13): a failed verify blocks the close regardless,
                    # so say so NOW — before the freshness gate could spend a paid
                    # tail-certification judge on a close that cannot succeed.
                    print(f"\nBlocked: cannot close task {prev_task} — declared verification "
                          "failed — fix it, or override with --force --reason.",
                          file=sys.stderr, flush=True)
                    sys.exit(1)

                # Owner policy: panel_required_for makes the evidence bar
                # PANEL-grade (all available judges, quorum PASS) — for the
                # configured risk classes, or "all". Otherwise single-judge
                # impl evidence suffices as before.
                # Task 209 (PHASE-B.md item 1, plan panel P7): the risk the newest impl panel ran
                # under, recorded with its reservation — lowering `## Risk` after a panel frees
                # nothing: the review gates below take the stricter of the two (the verify
                # contract above keeps the task's own risk).
                from tasks import stop_rule as _sr209
                try:
                    _rec209 = _sr209.panel_record(task_file.parent)
                except Exception:
                    _rec209 = None
                _rec_risk209 = (_rec209 or {}).get("risk")
                _rec_risk209 = _rec_risk209 if isinstance(_rec_risk209, str) and _rec_risk209 else None
                _gate_risk = _sr209.stricter(_rec_risk209, risk)
                _panel_req = any(resolve_panel_required(project_path, _r)
                                 for _r in {risk, _gate_risk, _rec_risk209} if _r)
                _evidence = (has_panel_impl_evidence(task_file) if _panel_req
                             else has_review_evidence(task_file, impl_only=True))

                # F18: panel freshness — computed BEFORE the close is
                # earned, recorded in the receipt for every close with an
                # impl round, and GATING for assertive/irreversible closes
                # that rest on panel evidence (design-1.5.6.md; blind judge
                # conditional-PASS, all conditions built; assertive added by
                # T1, owner decision 2026-08-23).
                from tasks.core import (
                    freshness_gate_decision, parse_judge_rounds,
                    tree_state_fingerprint,
                )
                _jm = task_file.parent / "judge.md"
                _rounds = []
                if _jm.exists():
                    try:
                        _rounds = parse_judge_rounds(
                            _jm.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        _rounds = []
                _impl = next((r for r in _rounds if r["mode"] == "impl"), None)
                # evidence_carries = the panel evidence that would satisfy
                # THIS close: rounds[0] impl + PASS (judge C3 — a FAIL
                # round or a replan on top falls through to the
                # panel-evidence block, never a double-block).
                _carries = bool(_panel_req and _rounds
                                and _rounds[0]["mode"] == "impl"
                                and _rounds[0]["verdict"] == "PASS")
                _now_fp = tree_state_fingerprint(project_path) if _impl else ""
                # Task 060: is this a git repo at all? A non-git project can
                # never have been stamped, so NO-STAMP stays advisory there;
                # in a git repo an absent stamp / an unreadable tree BLOCKS a
                # carrying high-consequence close (freshness_gate_decision).
                # Ancestor walk like review.py's `_in_git_repo` (a Playbook
                # root may sit inside a parent worktree); pure stats.
                # ONE shared probe (task 059, impl-panel r2 sonnet #2 — this used
                # to be an inline ancestor walk that ignored GIT_CEILING_DIRECTORIES
                # and mount boundaries, the same T009#2 false positive the review
                # path had). `in_git_repo` honours git's measured discovery rules.
                from tasks.core import in_git_repo
                _git_here = in_git_repo(Path(project_path))
                if not _git_here:
                    # impl-panel r1 (codex-medium): a worktree driven by
                    # GIT_DIR / GIT_WORK_TREE has no `.git` inside the project
                    # yet fingerprints fine — ask git itself too (either
                    # signal → git is available here).
                    try:
                        import subprocess as _sp060
                        _git_here = _sp060.run(
                            ["git", "rev-parse", "--is-inside-work-tree"],
                            cwd=project_path, capture_output=True,
                            timeout=30).returncode == 0
                    except (OSError, _sp060.SubprocessError):
                        _git_here = False
                _freshness = None
                if _impl is not None:
                    if not _impl["tree_state"]:
                        # Judge F4: a missing stamp must leave a RECORD
                        # when panel evidence carries the close — silence
                        # here would be the one zero-record bypass.
                        if _carries:
                            _freshness = {
                                "verdict": "NO-STAMP",
                                "accepted_reason": (
                                    reason if (stale_panel_ok and not force
                                               and _git_here) else None),
                            }
                    elif not _now_fp:
                        # Task 060 (T019 F4-outer): stamped round, but git
                        # cannot fingerprint the tree NOW. Not "STALE" (no code
                        # is known to have changed) and never FRESH: UNREADABLE.
                        # Tail certification is not attempted (it needs a STALE
                        # verdict and a working git); the gate blocks below.
                        _freshness = {
                            "verdict": "UNREADABLE",
                            "round_fp": _impl["tree_state"],
                            "now_fp": "",
                            "accepted_reason": (
                                reason if (stale_panel_ok and not force)
                                else None),
                        }
                    elif _now_fp:
                        _stale = _now_fp != _impl["tree_state"]
                        # Task 085 T: a STALE read explained entirely by commits
                        # that touch only fingerprint-excluded paths (the task's
                        # own record) is FRESH — checked exactly, else unchanged.
                        _records_only = False
                        if _stale:
                            from tasks.core import records_only_delta
                            _records_only = records_only_delta(
                                Path(project_path), _impl.get("snapshot"),
                                _impl["tree_state"])
                            if _records_only:
                                _stale = False
                        _freshness = {
                            "verdict": "STALE" if _stale else "FRESH",
                            "records_only": _records_only,
                            "round_fp": _impl["tree_state"],
                            "now_fp": _now_fp,
                            "accepted_reason": (
                                reason if (_stale and stale_panel_ok
                                           and not force) else None),
                        }
                # Task 060: an owner `fingerprint_exclude` that covers SOURCE
                # makes every fingerprint blind to that code — no verdict above
                # can be trusted, fresh or stale. Checked only when the panel
                # evidence would carry; git error → covers (fail closed).
                _excl_hits: "list[str]" = []
                if _impl is not None and _carries:
                    from tasks.core import load_config as _lc, owner_exclude_covers_behavioral
                    try:
                        _cfg060 = _lc(Path(project_path))
                    except Exception:
                        _cfg060 = {}
                    _cov, _excl_hits = owner_exclude_covers_behavioral(
                        Path(project_path), _cfg060)
                    if _cov:
                        _freshness = {
                            "verdict": "EXCLUDE-COVERS-CODE",
                            "paths": _excl_hits,
                            "round_fp": _impl["tree_state"],
                            "now_fp": _now_fp,
                            "accepted_reason": (
                                reason if (stale_panel_ok and not force)
                                else None),
                        }
                # Task 059 (plan panel P2): a carrying round whose tamper guard
                # was DEGRADED cannot certify a high-consequence close — the
                # verdict was kept on the review path, but its tamper-freedom is
                # unverified. Recorded as its own verdict (never FRESH), blocked
                # by the gate for assertive/irreversible/unclassified with the
                # STALE exits; advisory for reversible. A pre-059 round has no
                # receipt line and is treated as clean (additive).
                _tamper_degraded = bool(
                    _impl is not None and _impl.get("tamper_guard") == "degraded")
                if _tamper_degraded and _impl is not None and _freshness is not None:
                    # Carried alongside whatever verdict already applies, so a
                    # receipt for an EXCLUDE-COVERS-CODE / NO-STAMP / UNREADABLE
                    # close still names the degraded guard (059 r3, sonnet #2).
                    _freshness.setdefault("tamper_degraded_detail",
                                          _impl.get("tamper_detail") or "guard could not fully run")
                if (_tamper_degraded and _impl is not None
                        and (_freshness is None
                             or _freshness.get("verdict") in ("FRESH", "STALE"))):
                    _freshness = {
                        "verdict": "TAMPER-GUARD-DEGRADED",
                        "round_fp": _impl["tree_state"],
                        "now_fp": _now_fp,
                        "detail": _impl.get("tamper_detail") or "",
                        "accepted_reason": (
                            reason if (stale_panel_ok and not force and _carries)
                            else None),
                    }
                # Task 059 (impl-panel r1, sonnet #1): the panel gate above sees
                # only judge.md rounds. A SINGLE-judge review records its own
                # `[tamper guard]` receipt in its log; surface it when no panel
                # verdict is carrying this close, so a degraded single review is
                # not silent. Advisory (note + receipt clause), never a block.
                if _freshness is None:
                    from tasks.core import single_review_tamper_degraded
                    _sj_detail = single_review_tamper_degraded(task_file)
                    if _sj_detail:
                        _freshness = {"verdict": "SINGLE-TAMPER-DEGRADED",
                                      "detail": _sj_detail}
                _f_allowed, _f_reason = freshness_gate_decision(
                    risk=_gate_risk, panel_required=_panel_req,
                    evidence_carries=_carries,
                    round_fp=(_impl["tree_state"] if _impl else ""),
                    now_fp=_now_fp, force=force,
                    stale_ok=stale_panel_ok, stale_reason=reason,
                    git_available=_git_here,
                    exclude_covers=_excl_hits,
                    tamper_degraded=_tamper_degraded,
                    records_only=bool(_freshness and _freshness.get("records_only")),
                )
                # PB-STALE-CLOSE-BOUND (PLAN S12b, task 149): in an opted-in
                # workspace a stale close of an assertive/irreversible task may
                # not rest on a bare reason — --stale-panel-ok and --force alike.
                # Every verdict the two hatches override, not only STALE (impl
                # panel r1, opus: a degraded guard or an exclude that hides code
                # took a bare reason) — and an unset risk is held to the same bar
                # as everywhere else (agy).
                _bound_verdicts = ("STALE", "TAMPER-GUARD-DEGRADED", "EXCLUDE-COVERS-CODE",
                                   "NO-STAMP", "UNREADABLE")
                # Task 209 (PHASE-B.md item 1): the review stop rule, decided on what changed since
                # the newest impl round that met its quorum — whatever the position against the D6
                # cap (plan panel P1) and whatever round sits on it (impl round 1, R1: a plan round,
                # a below-quorum impl round). A verdict that is not a plain STALE trusts no delta
                # (R4). Every doubt makes the judge required (`close_state` fails closed; an
                # exception here too).
                # impl round 2, S4: the reviewed round is looked for in judge.md AND judge-archive.md
                try:
                    _pass209 = (_sr209.impl_rounds(task_file.parent) or [None])[-1]
                except _sr209.RoundsUnreadable:
                    _pass209 = next((r for r in _rounds if r["mode"] == "impl" and r["verdict"] == "PASS"), None)
                _newest209 = bool(_rounds and _rounds[0]["mode"] == "impl" and _rounds[0]["verdict"] == "PASS"
                                  and _sr209.same_round(_rounds[0], _pass209))
                _key209 = (_pass209 or {}).get("round_id") or (_pass209 or {}).get("tree_state") or ""
                _now209 = _now_fp if (_impl is not None or _pass209 is None) else tree_state_fingerprint(project_path)
                if _now209 and not _now_fp:
                    _now_fp = _now209   # impl round 3, T1: the commit's tree check needs it (an archived round)
                # impl round 3, T1: the post-D6 runs, the binding and `impl-review` key to judge.md's newest impl
                # round; when that is not the reviewed one, nothing they record can be bound to it
                from tasks.post_d6 import newest_impl_round as _nir209
                _mismatch209 = bool(_pass209 is not None
                                    and not _sr209.same_round(_nir209(task_file.parent), _pass209))
                _st209 = None
                _v209 = ""
                _bad209 = ""
                if _pass209 is not None:
                    if not _pass209.get("tree_state"):
                        # impl round 2, S3: an unstamped reviewed round in a git repository is an
                        # untrusted delta whether or not the freshness gate carries this close
                        _v209 = "NO-STAMP" if _git_here else ""
                    elif _newest209:
                        # outside a git repository no tree can be compared and no judge bound to one:
                        # an untrusted verdict there stays what it was (advisory where not held), as
                        # NO-STAMP is for a non-git project (task 060)
                        if (_freshness and _freshness.get("verdict") in _bound_verdicts
                                and (_freshness["verdict"] == "STALE" or _git_here)):
                            _v209 = _freshness["verdict"]
                    elif not _now209:
                        _v209 = "UNREADABLE" if _git_here else ""
                    elif _now209 != _pass209["tree_state"]:
                        from tasks.core import records_only_delta as _rod209
                        if not _rod209(Path(project_path), _pass209.get("snapshot"), _pass209["tree_state"]):
                            _v209 = "STALE"
                if _v209:
                    try:
                        _st209 = _sr209.close_state(Path(project_path), task_file.parent,
                                                    _gate_risk, _pass209, _rec209,
                                                    listable=(_v209 == "STALE"),
                                                    why_not=f"the freshness verdict is {_v209}")
                    except Exception as _e209:          # noqa: BLE001
                        _st209 = _sr209.failed_state(_gate_risk, f"the stop rule could not be evaluated ({_e209})")
                    if _freshness is None:
                        # the newest impl round (an unstamped below-quorum one) or none in judge.md (the
                        # reviewed round archived) gave no verdict: the receipt line is the reviewed
                        # round's — every branch below writes into it (impl round 2's own test found one
                        # skipped, and the owner-ok note would have had nowhere to go)
                        _freshness = {"verdict": _v209, "round_fp": _pass209.get("tree_state") or "?",
                                      "now_fp": _now209 or "?"}
                    _bad209 = _sr209.ledger_untrusted(task_file.parent)
                    if _bad209:
                        # impl round 3, T3: what the panels recorded cannot be read — every doubt fails closed
                        _st209 = _sr209.failed_state(_gate_risk, f"{_bad209} — what the panels recorded cannot be trusted",
                                                     guards=_st209.get("guards"))
                    if _mismatch209 and not owner_ok:
                        print(f"\nBlocked: cannot close task {prev_task} — the impl panel round that reviewed this "
                              "task's code is not judge.md's newest impl round (a below-quorum round sits on it, or it "
                              "was moved to judge-archive.md), so no post-panel judge or run can be bound to it. Run an "
                              'impl panel that meets its quorum, or let the owner decide:  tasks work done --owner-ok '
                              '--reason "…"' + (f"\n  Stop rule (D6): {_st209['why']}" if _st209.get("why") else ""),
                              file=sys.stderr, flush=True)
                        sys.exit(1)
                    for _m209 in (_st209.get("missing") or []):
                        print(f"  ⚠ `guard_paths` entry {_m209!r} names nothing in the project — it guards "
                              "nothing (.agent/config.json).", file=sys.stderr, flush=True)
                _held209 = bool(_carries and _gate_risk != "reversible")   # the freshness gate holds this close
                if (_carries and _impl is not None and _freshness
                        and _freshness.get("verdict") in _bound_verdicts
                        and (stale_panel_ok or force)
                        and _gate_risk != "reversible"):
                    if (_st209 is not None and not _st209["judge"] and _st209["reached"]
                            and _freshness.get("verdict") == "STALE" and _st209.get("can")):
                        # D2 (the owner, 2026-10-15): after the last round D6 allows, a change no
                        # judge needs closes on the reason, and the receipt names what no judge read —
                        # unless a post-panel run holds it (impl round 1, R6). Only on a plain STALE
                        # with a listed delta (impl round 2, S1): every other verdict keeps its binding.
                        _hold209 = "" if owner_ok else _sr209.post_panel_hold(task_file.parent, _key209)
                        if _hold209:
                            print(f"\nBlocked: cannot close task {prev_task} — {_hold209}",
                                  file=sys.stderr, flush=True)
                            sys.exit(1)
                        _freshness["stop_rule"] = _sr209.receipt_clause(_st209)
                        if owner_ok and reason and reason.strip():
                            _freshness["bound"] = f'owner-ok: "{" ".join(reason.split())}"'
                    else:
                        from tasks.core import bound_stale_close
                        _rule209 = bool(_st209 is not None and _st209["judge"])
                        _b_ok, _b_msg, _b_note = bound_stale_close(
                            Path(project_path), task_file.parent,
                            owner_ok_reason=(reason if owner_ok else None),
                            rule_on=_rule209, why=(_st209 or {}).get("why", ""))
                        if not _b_ok:
                            print(f"\nBlocked: cannot close task {prev_task} — {_b_msg}"
                                  + (("\n" + _sr209.block_hint(_st209, prev_task)) if _st209 else ""),
                                  file=sys.stderr, flush=True)
                            sys.exit(1)
                        if _b_note:
                            _freshness["bound"] = _b_note
                            if _rule209:
                                _freshness["bound_rule_on"] = _st209["why"]
                            if owner_ok and not force:
                                # the owner's decision carries the stale close
                                _f_allowed = True
                                _freshness["accepted_reason"] = reason
                # Task 209 (plan panel P10): a close the freshness gate does not hold — a reversible
                # task, or no panel requirement — whose change the stop rule sends to the judge: the
                # post-panel PASS on this tree, or the owner (`--force` bound too, as above).
                if _st209 is not None and _st209["judge"] and not _held209:
                    from tasks.core import bound_stale_close
                    _b_ok, _b_msg, _b_note = bound_stale_close(
                        Path(project_path), task_file.parent,
                        owner_ok_reason=(reason if owner_ok else None),
                        rule_on=True, why=_st209["why"])
                    if not _b_ok:
                        print(f"\nBlocked: cannot close task {prev_task} — {_b_msg}\n"
                              + _sr209.block_hint(_st209, prev_task), file=sys.stderr, flush=True)
                        sys.exit(1)
                    if _b_note:
                        _freshness["bound"] = _b_note
                        _freshness["bound_rule_on"] = _st209["why"]
                # Task 209 (impl round 1, R9): every close the rule leaves without a judge after the
                # last round names what no judge read — a reversible task's too.
                if (_st209 is not None and not _st209["judge"] and _st209["reached"] and not _held209
                        and "stop_rule" not in (_freshness or {})):
                    _hold209 = "" if owner_ok else _sr209.post_panel_hold(task_file.parent, _key209)
                    if _hold209:
                        print(f"\nBlocked: cannot close task {prev_task} — {_hold209}",
                              file=sys.stderr, flush=True)
                        sys.exit(1)
                    _freshness["stop_rule"] = _sr209.receipt_clause(_st209)
                # TAIL CERTIFICATION (task 036, owner decision A). When the panel
                # is STALE and would block, but the ONLY post-panel delta is in
                # non-behavioral file classes (docs/tests/ledger/claim docs), a
                # single judge may re-certify the exact delta instead of forcing a
                # fresh full panel. Any code-path delta (incl. a code comment) still
                # blocks. Reached ONLY on the plain stale block (not --force, not
                # --stale-panel-ok, an actual STALE verdict on a carrying panel);
                # everything fails CLOSED (missing descriptor, scope change, git
                # error, behavioral delta, non-PASS verdict, or a tree that mutates
                # during the judge call — the TOCTOU compare-and-swap below).
                from tasks.core import post_d6_pass_base as _pdb209
                _skip_tc209 = bool(_st209 is not None and _st209["judge"]
                                   and (_bad209 or not _pdb209(Path(project_path), task_file.parent)))
                # (the single judge, run 1: while the ledger is untrusted no post-D6 PASS read from it is a base)
                if (not _f_allowed and not force and not stale_panel_ok
                        and _carries and _impl is not None and _freshness
                        and _freshness.get("verdict") == "STALE"
                        # impl round 2, S5: a change the stop rule sends to the post-panel judge is not
                        # left to tail certification (another question, no run cap, no binding) until a
                        # post-D6 PASS is the base; the refusal below names the exit
                        and not _skip_tc209):
                    from tasks.core import (
                        _cause_paths, tail_cert_delta_why, tail_cert_gate_decision,
                    )
                    from tasks.review import run_tail_cert_judge, tail_cert_unavailable
                    _snap = _impl.get("snapshot") if _impl else None
                    _snap_fp = _impl["tree_state"]
                    # Task 149 (PLAN S12b): after a post-D6 PASS, certify the delta
                    # since the tree THAT judge reviewed, not since the impl panel.
                    from tasks.core import post_d6_pass_base
                    _pd6_base = post_d6_pass_base(Path(project_path), task_file.parent)
                    if _pd6_base:
                        _snap, _snap_fp, _ = _pd6_base
                    # task 202 (#17): the cause of a refusal, naming the reviewed tree's base
                    _tc_base = f"post-D6 PASS {_pd6_base[2]}" if _pd6_base else ""
                    _tc_can, _tc_beh, _tc_non, _tc_cause = tail_cert_delta_why(
                        project_path, _snap, _snap_fp, base=_tc_base)
                    _tc_verdict = None
                    # Task 165 (PLAN S11 item 11): can the delta be put before a
                    # judge at all? Asked first, so that "no judge was called" is
                    # said as that. Closing retro 161 the delta was over the
                    # payload cap, no judge ran, and the close answered "tail
                    # certification did not return PASS" — a judge's FAIL in all
                    # but name.
                    _tc_noverdict = ""
                    _tc_unavail = (tail_cert_unavailable(project_path, _snap)
                                   if (_tc_can and not _tc_beh) else "")
                    # impl round 3, T2: a certified delta that holds a guarded path — after a post-D6 PASS
                    # too — is the post-panel judge's to read, not tail certification's
                    _tcg209 = [x for x in (_tc_non if (_tc_can and not _tc_beh) else [])
                               if _st209 is not None and _sr209.guarded(x, _st209.get("guards") or [])]
                    if _tcg209:
                        _tc_unavail = ("the delta holds files under `guard_paths` (" + ", ".join(_tcg209[:8])
                                       + ") — the post-panel judge reads them: tasks impl-review " + prev_task)
                    if _tc_unavail:
                        print(f"  … tail certification cannot run: {_tc_unavail} — "
                              "no judge is called", file=sys.stderr, flush=True)
                    if _tc_can and not _tc_beh and not _tc_unavail:
                        # Give the certifying judge what the panel actually
                        # reviewed (impl-panel r6 opus F1): a bare "PANEL PASS"
                        # can't tell the judge whether a docs delta CONTRADICTS an
                        # approved claim. Pass a bounded slice of the impl round
                        # body (its verdict + findings).
                        # Strip the single-line `**Panel-snapshot:**` JSON (per-
                        # path sha256 for every dirty file — can exceed 8 KB on a
                        # dirty tree) and the `**Tree-state:**` stamp before slicing
                        # (impl-review r12b F2), so the judge's summary is the
                        # VERDICT + FINDINGS, not raw descriptor bytes.
                        _body = "\n".join(
                            _ln for _ln in (_impl.get("body") or "").splitlines()
                            if not _ln.startswith(("**Panel-snapshot:**",
                                                   "**Tree-state:**")))[:8000]
                        _panel_summary = (
                            f"PANEL {_impl.get('verdict')} impl review at tree "
                            f"{_impl['tree_state']}. The panel's round (verdict + "
                            f"findings) follows — use it to judge whether this "
                            f"non-behavioral delta contradicts what was approved:\n"
                            f"{_body}"
                            + (f"\nA post-D6 single judge then PASSED the tree {_snap_fp} "
                               f"(run {_pd6_base[2]}); the delta below is measured from that tree."
                               if _pd6_base else ""))
                        print("  … non-behavioral post-panel delta — single-judge "
                              "tail certification", file=sys.stderr, flush=True)
                        from tasks.core import resolve_review_timeout
                        _tc_why: list = []
                        _tc_verdict = run_tail_cert_judge(
                            project_path, _snap, _tc_non, _panel_summary,
                            timeout_secs=resolve_review_timeout(project_path),
                            task_file=task_file, why=_tc_why)
                        # no verdict: say whose doing it was (impl panel r1) —
                        # the runner knows whether a judge was called at all
                        if _tc_verdict is None and _tc_why:
                            _kind, _said = _tc_why[0]
                            if _kind == "no-judge":
                                _tc_unavail = _said
                            else:
                                _tc_noverdict = _said
                        if _tc_verdict == "PASS":
                            # Finding D (TOCTOU): the judge call is long; recompute
                            # the fingerprint and require it to equal the one the
                            # certification was computed against — a compare-and-
                            # swap that closes the mutate-during-cert window
                            # without a lock. impl-panel r5 codex:terra#1/sol#1: the
                            # scope-set + exclude-set + delta checks live INSIDE
                            # tail_cert_delta and ran BEFORE the judge, so re-run
                            # the WHOLE thing here and require the identical
                            # non-behavioral-only result (a fingerprint match alone
                            # doesn't re-validate a code_root removal or an exclude
                            # change made during the judge call).
                            _recheck_fp = tree_state_fingerprint(project_path)
                            _r_can, _r_beh, _r_non, _r_cause = tail_cert_delta_why(
                                project_path, _snap, _snap_fp, base=_tc_base)
                            if (_recheck_fp != _now_fp or not _r_can or _r_beh
                                    or _r_non != _tc_non):
                                _tc_verdict = None
                                # task 202 (plan triage 5): which of the four discarded it
                                _gone = []
                                if _recheck_fp != _now_fp:
                                    _gone.append("the tree moved while the judge ran")
                                if not _r_can:
                                    _gone.append(f"it can no longer certify — {_r_cause}")
                                elif _r_beh:
                                    _gone.append("a code path changed since the reviewed tree ("
                                                 + _cause_paths(_r_beh) + ")")
                                elif _r_non != _tc_non:
                                    _gone.append("the files it read are no longer the delta (now: "
                                                 + _cause_paths(_r_non) + ")")
                                _tc_noverdict = "its PASS was discarded — " + "; ".join(_gone)
                                print(f"  ⚠ changed during certification: {'; '.join(_gone)} — not certifying "
                                      "(fresh panel required)",
                                      file=sys.stderr, flush=True)
                    _tc_allowed, _tc_clause = tail_cert_gate_decision(
                        can_certify=_tc_can, behavioral_nonempty=bool(_tc_beh),
                        cert_verdict=_tc_verdict, non_behavioral=_tc_non,
                        unavailable=_tc_unavail, no_verdict=_tc_noverdict, cause=_tc_cause,
                        behavioral=_tc_beh, base=_tc_base)
                    if _tc_allowed:
                        _f_allowed = True
                        _freshness = {
                            "verdict": "TAIL-CERT-PASS",
                            "round_fp": _impl["tree_state"],
                            "now_fp": _now_fp,
                            "cert_clause": (_tc_clause + (f" (measured from post-D6 PASS {_pd6_base[2]})"
                                                          if _pd6_base else "")),
                            "pd6_base_run": _pd6_base[2] if _pd6_base else None,
                        }
                        print(f"  ✓ tail-certified: {_tc_clause}",
                              file=sys.stderr, flush=True)
                    else:
                        # "attempted" was said here even when no judge had been
                        # called; the clause now says which it was (task 165)
                        _f_reason = (_f_reason
                                     + f"\n  (tail certification: {_tc_clause})")
                if not _f_allowed:
                    print(f"\nBlocked: cannot close task {prev_task} — {_f_reason}"
                          + (("\n" + _sr209.block_hint(_st209, prev_task)) if _st209 else ""),
                          file=sys.stderr, flush=True)
                    sys.exit(1)

                allowed, block_reason = close_decision(
                    risk=_gate_risk, verify_declared=bool(commands),
                    verify_failed=verify_failed,
                    # impl-grade only: a plan-phase review cannot vouch for
                    # what was BUILT.
                    has_review_evidence=_evidence,
                    force=force, reason=reason,
                    panel_required=_panel_req,
                    risk_section_present=risk_offered,
                )
                if not allowed:
                    print(f"\nBlocked: cannot close task {prev_task} — {block_reason}",
                          file=sys.stderr, flush=True)
                    sys.exit(1)

                # F14 blind-judge Finding 3, narrowed by the 1.5.32 audit: an
                # unclassified risk means the risk-keyed review bar cannot be
                # evaluated. Where the gate WAS offered and skipped,
                # close_decision has already blocked above; reaching here with
                # `unclassified` therefore means a pre-1.5.0 task whose template
                # had no Risk section, or a close that cleared the bar another
                # way (review evidence, or --force). Still say what was skipped
                # — a silent fail-open is the thing this line exists to prevent.
                if risk == "unclassified":
                    print("⚠ closing with ## Risk unclassified — the "
                          "risk-keyed review requirement could not be evaluated "
                          "for this close. Set ## Risk to exactly one word "
                          "(reversible / irreversible / assertive) at the "
                          "Risk gate.", file=sys.stderr, flush=True)

                # Close is earned — record the receipt (ONE section, newest
                # entry first — a reopened task must not accrete duplicate
                # headings that section parsers read as current), then set done.
                import subprocess
                from tasks.core import _set_status, upsert_task_section
                try:
                    _head = subprocess.run(
                        ["git", "rev-parse", "HEAD"], cwd=project_path,
                        capture_output=True, text=True).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    _head = ""
                # Dirty-tree honesty (StrataDB F6): closing before committing
                # is the normal flow, so say so in the receipt and out loud —
                # a crash between close and commit silently loses "done" work.
                # Task 128 (gauntlet 2 R20): not the task's own records — its
                # task.md and judge files are always dirty at a close, so
                # counting them made the warning fire on every close.
                _dirty = _dirty_outside(project_path, task_file.parent)
                try:
                    from tasks.post_d6 import close_receipt_line as _pd6_line
                    _pd6_receipt = _pd6_line(task_file.parent)
                except Exception:
                    _pd6_receipt = ""
                receipt = format_verify_receipt(
                    entries, _head, risk, reason=(reason if force else None),
                    dirty_files=_dirty, freshness=_freshness, post_d6=_pd6_receipt)
                # Task 058 (plan panel P1/P10): receipt + status are ONE locked
                # transaction, composed on the bytes just read and re-checked
                # against what earned this close. Everything expensive (verify,
                # judges) already happened OUTSIDE the lock; what is serialized
                # here is only the commit. A task that moved underneath — a
                # `tasks blocked` from another session, a status someone else
                # changed — REFUSES rather than overwriting the other record.
                # W4b (plan panel P9/P10): the freshness decision was made from
                # `_now_fp` BEFORE verify and the judges ran. Re-compute it here,
                # at the commit, and refuse if the tree moved — narrow and honest:
                # it does not make the whole-tree TOCTOU preventable, it makes THIS
                # close's own decision non-stale.
                # `--force` is the blunt whole-policy override (verify, review and
                # freshness alike), so it bypasses this CAS too — otherwise the one
                # escape that exists for an actively-edited repo could not get
                # through it (impl panel r3, opus F1). The forced close's reason is
                # already recorded in the receipt.
                # Task 149 (impl panel r1, codex ×2): a close bound to a post-D6 PASS
                # is re-checked at the commit — --force included, and the config
                # too, which the fingerprint cannot see.
                if _freshness and _freshness.get("bound") and not owner_ok:
                    from tasks.core import bound_stale_close as _bsc
                    _ok2, _msg2, _ = _bsc(Path(project_path), task_file.parent, owner_ok_reason=None,
                                          rule_on=bool(_freshness.get("bound_rule_on")),
                                          why=str(_freshness.get("bound_rule_on") or ""))
                    if not _ok2:
                        print(f"Blocked: cannot close task {prev_task} — the tree or the config "
                              f"changed while this close was running: {_msg2}",
                              file=sys.stderr, flush=True)
                        sys.exit(1)
                if _now_fp and not force:
                    _commit_fp = tree_state_fingerprint(project_path)
                    # An UNREADABLE fingerprint at the commit must block too (058
                    # impl panel r1, codex ×2 + grok): `if _commit_fp and …` let an
                    # empty result — git broken, a corrupt index — pass as "no
                    # mismatch", which is the fail-open direction this whole task
                    # exists to remove.
                    if _commit_fp != _now_fp:
                        print(f"Blocked: cannot close task {prev_task} — the tree "
                              f"changed while this close was running (tree-state "
                              f"{_now_fp} → {_commit_fp or 'UNREADABLE'}); the freshness decision "
                              "above was made on the earlier state. Nothing was "
                              "written; re-run `tasks work done`.",
                              file=sys.stderr, flush=True)
                        sys.exit(1)
                from tasks.core import CloseRaceRefused, compose_close

                def _bound_still_holds():
                    # Task 149 (impl panel r2, codex-high): re-checked INSIDE the task
                    # lock the commit holds — a post-D6 run reserved (same lock) after
                    # the check above cannot slip in before the receipt.
                    if _freshness and _freshness.get("pd6_base_run"):
                        # post-D6 run 2 (codex): tail-cert started from a post-D6 PASS;
                        # a newer run reserved meanwhile makes that PASS not the newest
                        from tasks.core import post_d6_pass_base as _pb3
                        _base3 = _pb3(Path(project_path), task_file.parent)
                        if not _base3 or _base3[2] != _freshness["pd6_base_run"]:
                            raise CloseRaceRefused("the post-D6 PASS tail certification started from "
                                                   "is no longer the newest finished run")
                    # Task 196 (impl panel r1 I2, codex, reproduced): a task folder made while verify ran must
                    # still refuse the close — the check before verify is repeated here, under the task lock, at the
                    # commit (`--force --reason` overrides, as there).
                    if not force:
                        from tasks.registry import LaneUnreadable as _LU196
                        from tasks.registry import RegistryUnreadable as _RU196, unknown_now as _un196
                        from tasks.registry import standing_before as _sb197
                        try:
                            _late196 = _un196(project_path, agent_dir, no_registry="git",
                                              before=_sb197(agent_dir))
                        except _RU196 as _e196:
                            raise CloseRaceRefused(f"the task registry became unreadable while this close ran "
                                                   f"({_e196}).")
                        except _LU196 as _e196:
                            raise CloseRaceRefused(f"this lane's task folders cannot be read any more ({_e196}) — "
                                                   "the close checks each one.")
                        if _late196:
                            raise CloseRaceRefused(
                                "task folder(s) the playbook CLI did not make appeared while this close ran: "
                                + ", ".join(_late196) + " — adopt (`.claude/bin/tasks adopt <folder>`) or remove "
                                "each, then re-run `tasks work done`.")
                    if _freshness and _freshness.get("bound") and not owner_ok:
                        from tasks.core import bound_stale_close as _bsc3
                        _ok3, _msg3, _ = _bsc3(Path(project_path), task_file.parent,
                                               owner_ok_reason=None,
                                               rule_on=bool(_freshness.get("bound_rule_on")),
                                               why=str(_freshness.get("bound_rule_on") or ""))
                        if not _ok3:
                            raise CloseRaceRefused(f"the post-D6 binding no longer holds: {_msg3}")
                    # Task 209 (plan panel P11; impl round 1, R2 and R10): a close that took no
                    # judge re-makes the stop rule's decision here, under the task lock — the config
                    # (guard list, caps) and the tree are read again; under `--force` (which skips
                    # the tree check above) a tree that moved re-makes it even after a FRESH panel.
                    if (not owner_ok and _pass209 is not None
                            and not (_freshness or {}).get("bound_rule_on")):
                        _moved209 = False
                        if force:
                            try:
                                _moved209 = tree_state_fingerprint(project_path) != _now_fp
                            except Exception:           # noqa: BLE001
                                _moved209 = True
                        if (_st209 is not None and not _st209["judge"]) or _moved209:
                            # the single judge, run 1: the ledger's trust is part of the decision — re-read here
                            _bad_now209 = _sr209.ledger_untrusted(task_file.parent)
                            if _bad_now209:
                                raise CloseRaceRefused(
                                    "the stop rule's answer changed while this close ran — "
                                    f"{_bad_now209}: what the panels recorded cannot be trusted.")
                            try:
                                _fp209 = tree_state_fingerprint(project_path)
                                # the record read at the decision stays in force (the single judge, run 2)
                                _rec_now = _sr209.merge_records(_rec209, _sr209.panel_record(task_file.parent))
                                _risk_now = _sr209.stricter((_rec_now or {}).get("risk") or None, _gate_risk)
                                if _fp209 and _pass209.get("tree_state") and _fp209 == _pass209["tree_state"]:
                                    _st_now = None      # the reviewed tree itself: nothing changed since
                                else:
                                    _st_now = _sr209.close_state(
                                        Path(project_path), task_file.parent, _risk_now, _pass209, _rec_now,
                                        listable=(_v209 in ("", "STALE")),
                                        why_not=f"the freshness verdict is {_v209}")
                            except Exception as _e209b:     # noqa: BLE001
                                _st_now = _sr209.failed_state(
                                    _gate_risk, f"the stop rule could not be evaluated ({_e209b})")
                            _was = "the stop rule's answer changed while this close ran — "
                            if (_freshness or {}).get("stop_rule"):
                                # impl round 2, S6: a post-D6 run that counted a Critical, or a corrupt or
                                # unfinished ledger, appearing while the close ran
                                _h209 = _sr209.post_panel_hold(task_file.parent, _key209)
                                if _h209:
                                    raise CloseRaceRefused(f"{_was}{_h209}")
                            if _st_now is not None and _st_now["judge"]:
                                raise CloseRaceRefused(f"{_was}it now needs the post-panel judge: {_st_now['why']}.")
                            if (_st_now is not None and _st209 is not None
                                    and _st_now["reached"] != _st209["reached"]):
                                raise CloseRaceRefused(
                                    f"{_was}the D6 cap state moved ({_st209['rounds']} of {_st209['allowed']} "
                                    f"rounds → {_st_now['rounds']} of {_st_now['allowed']}).")
                            if (force and _st_now is not None and _st209 is not None
                                    and _st_now["changed"] != _st209["changed"]):
                                raise CloseRaceRefused(
                                    f"{_was}the files changed after the panel are not the ones this close was "
                                    "decided on (now: " + ", ".join(_st_now["changed"][:8]) + ").")
                try:
                    _rewrite(task_file, lambda _txt: _bound_still_holds() or compose_close(
                        _txt, receipt_heading="Verification Receipt",
                        receipt=receipt, expect_status=_status_at_entry,
                        expect_blocked=_blocked_at_entry,
                        expect_risk=risk if risk else None,
                        expect_open_gates=_open_gates_at_entry,
                        expect_judge=_judge_at_entry, task_file=task_file))
                except CloseRaceRefused as _race:
                    print(f"Blocked: cannot close task {prev_task} — {_race} "
                          "Nothing was written; the session pointer is kept.",
                          file=sys.stderr, flush=True)
                    sys.exit(1)
                # T5: record the verify contract this close ran in the
                # enforcement journal too. `.agent/config.json` (which declares
                # `verify`) is gate-exempt, so a silently weakened verify could
                # close against a hollow bar; the receipt already names the
                # commands, and this adds a durable forensic line. Best-effort
                # and fully wrapped: a journal failure must NEVER change or break
                # a close (same contract as the hook emitters). Loaded like
                # command_guard's helper so its absence can't break the CLI.
                try:
                    import importlib.util as _ilu
                    _pbj_path = (Path(__file__).resolve().parent.parent
                                 / "scripts" / "pb_journal.py")
                    _spec = _ilu.spec_from_file_location("_pb_journal_close", _pbj_path)
                    if _spec is not None and _spec.loader is not None:
                        _pbj = _ilu.module_from_spec(_spec)
                        _spec.loader.exec_module(_pbj)
                        # Single-line-safe encoding: escape newlines in each
                        # command BEFORE joining. pb_journal._head keeps only the
                        # FIRST line of the `command` field (to hold the record
                        # under PIPE_BUF for the atomic single-write contract), so
                        # a raw newline inside any one verify command would drop
                        # every command after it from the journal record (panel
                        # round-3 sonnet). Escaping to a literal `\n` keeps the
                        # whole set on one line; the _head length cap still bounds
                        # it, so the atomicity contract is preserved.
                        def _flat(c):
                            return (c.replace("\r\n", "\\n")
                                     .replace("\n", "\\n").replace("\r", "\\n"))
                        _vcmds = "; ".join(_flat(c) for _l, c in commands) or "(none declared)"
                        # Short FIXED reason (like every other emitter); the
                        # commands go in the `command` field, which pb_journal
                        # caps — a verbatim reason would be uncapped and could
                        # push the record past PIPE_BUF, breaking its atomic
                        # single-write contract under concurrent closes (panel).
                        # decision="record": the journal contract is
                        # {allow, block, record} (owner-ratified, M173/R5); a
                        # close is a log-only forensic RECORD of the verify bar,
                        # not a gate allow/block. The earlier "verify-contract"
                        # value was outside the contract and tripped consumers'
                        # unexpected-decision checks.
                        _pbj.append(agent_dir, "close", "record",
                                    "verify contract",
                                    session_id=session_id, command=_vcmds, task=prev_task)
                        if _freshness and _freshness.get("bound"):
                            _pbj.append(agent_dir, "close", "record",
                                        "stale close owner-ok" if owner_ok else "stale close bound",
                                        session_id=session_id,
                                        command=_flat(str(_freshness["bound"])), task=prev_task)
                        if _freshness and _freshness.get("stop_rule"):
                            # task 209: a close after the last round D6 allows that no judge read
                            _pbj.append(agent_dir, "close", "record", "stale close no judge (stop rule)",
                                        session_id=session_id,
                                        command=_flat(str(_freshness["stop_rule"])), task=prev_task)
                except Exception:
                    pass
                if _dirty:
                    print(f"⚠ {_dirty} modified/untracked file(s) — this close's "
                          "receipt describes UNCOMMITTED work. Commit before ending "
                          "the session, or a crash loses 'done' work silently.",
                          flush=True)
                # Panel freshness console note (1.5.3 advisory, F18-reworked):
                # the receipt clause above is now the durable record; the
                # note keeps the mismatch visible in the session where the
                # close happened. Uses the already-computed freshness —
                # re-parsing here could disagree with what the receipt says.
                if _freshness and _freshness.get("verdict") == "STALE":
                    print("note: the code state changed after the newest "
                          "impl panel (tree-state mismatch) — if code was "
                          "edited post-review, consider re-running "
                          "`tasks panel-review <N> --mode impl`.",
                          flush=True)
                if _freshness and _freshness.get("verdict") == "SINGLE-TAMPER-DEGRADED":
                    print("note: the newest single-judge review ran with a degraded tamper "
                          "guard (its findings are kept, their tamper-freedom unverified) — "
                          "recorded in the receipt.", flush=True)
                if _freshness and _freshness.get("verdict") == "TAMPER-GUARD-DEGRADED":
                    print("note: the newest impl panel's tamper guard was degraded "
                          "(its verdict is kept, its tamper-freedom unverified) — "
                          "recorded in the receipt; consider re-running "
                          "`tasks panel-review <N> --mode impl` on a healthy git.",
                          flush=True)
            # Remove session dirs that reference this task.
            # PLAYBOOK_SESSION_ID is not set when called from Bash tool, so scan all sessions.
            # Intentional partial delete: only sessions pointing at prev_task are removed;
            # sessions for other tasks are left intact.
            # Inside the project only, through descriptors (task 194, impl panel r2, opus and sonnet: the
            # `glob` + `rmtree` here followed a linked `.agent/sessions` into its target).
            from tasks.shared import _remove_sessions_of_task
            # task 244: the agent's tokens up to the close, under this task — before its `done` and before the
            # removal below takes the sessions' transcript pointers away
            _record_agent_usage(agent_dir, prev_task, _sessions_on_task(agent_dir, prev_task))
            _remove_sessions_of_task(Path(project_path), agent_dir, prev_task)  # agent_dir already resolved above
            _record_transition(agent_dir, "done", prev_task)
            print(f"Task {prev_task} done.")
            # Task 235 (PHASE-B item 6): the task's own numbers for this closure, from the plugin's records only. After
            # the transaction and its `done` record; imported here, in a `try` that also catches SystemExit, inside a
            # time budget: a fault says one line and changes nothing of the close (plan panel: sonnet, codex).
            try:
                from tasks.numbers import write_closure as _write_numbers
                _np = _write_numbers(agent_dir, task_file.parent, prev_task)
                if _np is not None:
                    try:
                        _shown = _np.relative_to(Path(project_path))
                    except ValueError:
                        _shown = _np
                    print(f"numbers: {_shown} (this closure's numbers — commit it with the task)")
                else:                       # impl r1, grok: a write that did not happen says so
                    print("numbers: not written (no receipt to key it, a newer close, or a link in its place)",
                          file=sys.stderr)
            except (Exception, SystemExit) as _nexc:  # noqa: BLE001 — never changes the close
                print(f"numbers: not written ({type(_nexc).__name__})", file=sys.stderr)

            # P9: surface this task's OPEN parked items at close so they are
            # not swallowed. Advisory (does not block) — resolve each by
            # promoting (`tasks new` then mark `[promoted → NNN]`), dismissing
            # (`[dismissed: reason]`), or leaving open deliberately.
            from tasks.core import existing_task_numbers, open_parked_items, retro_proposal
            try:
                # dangling promotions count here too (PLAN S4, impl panel r1)
                _still_open = open_parked_items(read_task_text(task_file),
                                                existing_task_numbers(Path(project_path)))
            except OSError:
                _still_open = []
            if _still_open:
                print(f"\n⚠ {len(_still_open)} open parked item(s) from this task — "
                      "resolve before they rot (`tasks parked` to review):")
                for _p in _still_open:
                    print(f"    - {_p}")
                print("  Promote (`tasks new …` + mark `[promoted → NNN]`), "
                      "dismiss (`[dismissed: reason]`), or re-park deliberately.")

            # P4: the learning loop finally gets a trigger — propose a retro
            # once enough tasks have closed since the last one.
            _retro = retro_proposal(project_path)
            if _retro:
                print(f"\n💡 {_retro}")
        else:
            print("No active task.")
        print("Code edits blocked until: tasks work <N>")
        return

    # Task 105 (round-1 R1-4): identity FIRST — the resume and reopen paths
    # below rewrite task.md, so a refused activation must be refused before
    # them, not after (exits with SESSION_UNRESOLVED_MESSAGE when unresolved).
    require_session_id()

    # Resume a BLOCKED task (#08): `tasks work <N>` is the "I am picking this
    # up" verb. A blocked task is skipped by _find_active_task, so it is found
    # here — but its status is written only AFTER the switch check below has
    # passed (task 140, impl panel r1: the resume and the reopen used to be
    # written first, so an activation refused for the previous task's open gates
    # left this one resumed/reopened with no pointer).
    from tasks.core import _folder_matches_filter, _is_blocked, _iter_task_dirs
    _named = [tf for _n, _s, tf in _iter_task_dirs(Path(project_path))
              if _folder_matches_filter(tf.parent.name, task_num)]
    # A word that is a whole folder name means that folder; otherwise several
    # matches are ambiguous for the done / blocked arms — never the first one
    # (impl panel r2: it reopened or resumed somebody else's task).
    _exact = [tf for tf in _named if tf.parent.name == task_num]
    if _exact:
        _named = _exact
        # …and before the open-task search below, which is by substring (post-D6 run 1:
        # `001-foo` activated the open `002-001-foo-more`): go on by its number.
        from tasks.core import number_twins
        _twins = number_twins(Path(project_path), task_num)
        if _twins:
            # the CLI refuses this before its session GC; the in-process backstop
            print(f"Error: {' and '.join(_twins)} share one task number — the session "
                  "pointer holds a number and cannot tell them apart. Renumber one "
                  "(rename its folder), then retry. Nothing changed.", file=sys.stderr)
            sys.exit(1)
        _n_exact = task_num.split("-", 1)[0]
        if _n_exact.isdigit():
            task_num = _n_exact.zfill(3)
    _ambiguous = len(_named) > 1
    _resume_target = (_named[0] if _named and not _ambiguous and _is_blocked(_named[0])
                      else None)
    _reopen_target = None

    # Verify task exists
    # _extract_head_position is imported here, not further down where the
    # auto-close branch uses it: both live in this same function, so a
    # later function-scope import leaves the name unbound for the
    # re-adoption arm below (UnboundLocalError, not NameError).
    from tasks.core import _find_active_task, _extract_head_position
    task_file = _find_active_task(project_path, task_num)
    if not task_file and _resume_target is not None:
        task_file = _resume_target
    elif task_file is not None:
        _resume_target = None           # another, active task answers to this name
    if not task_file:
        # the same name forms activation takes (`7`, `007`, `007-slug`) — a done task
        # named by its folder used to be "not found" here (task 140, impl panel r1)
        matches = _named
        if _ambiguous:
            print(f"Error: {len(matches)} tasks match {task_num!r}: "
                  + ", ".join(tf.parent.name for tf in matches)
                  + " — name one by its number. Nothing changed.", file=sys.stderr)
            sys.exit(1)
        if matches:
            from tasks.core import _is_done, _is_stub_file
            tf = matches[0]
            done = _is_done(tf)
            if done and "--reopen" not in cmd_args:
                # Task 140 (owner Q6): the CLI refuses this before its session GC
                # (`cli._wrong_usage`); this is the backstop for an in-process caller.
                print(f"Error: task {task_num} is done — `tasks work {task_num} --reopen` "
                      "reopens it. Nothing changed.", file=sys.stderr)
                sys.exit(1)
            if done:
                # Reopen — written below, after the switch check (`_reopen_target`).
                _reopen_target = tf
                task_file = tf
                # Fall through to activation below
            elif _is_stub_file(tf):
                # Stub — allow activation, expansion happens below
                task_file = tf
            elif _extract_head_position(tf) == "(all gates checked)":
                # Re-adopt: every gate is checked but the task was never
                # closed. Reachable whenever the session pointer is lost
                # while a finished-but-open task is active — a lost pointer
                # used to make this state UNRECOVERABLE through the CLI:
                # `work done` reads the pointer (absent → "No active task",
                # never touches ## Status), and this branch refused the task
                # because _find_active_task only returns tasks with open
                # gates. The only sanctioned writer of ## Status needed a
                # pointer, and the only way to get a pointer was refused, so
                # the field report's author had to hand-write the file
                # (task 027). Status is never set to `done` here — activation
                # restores the pointer (and, since task 140, writes
                # `in_progress` like every activation), and `work done`
                # remains the thing that closes the task.
                print(f"Note: task {task_num} has all gates checked but is "
                      f"not closed — re-adopting; run 'tasks work done' to close it.")
                task_file = tf
            else:
                print(f"Task {task_num} has no open gates.", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Task {task_num} not found", file=sys.stderr)
            sys.exit(1)

    # F11: `tasks work` accepts a bare number, a `NNN-slug`, or a bare slug
    # (`tasks list` shows the folder name, so an agent naturally copies it).
    # Whatever was given, the session pointer MUST be the canonical numeric —
    # the numeric-only code-edit gate (N2) rejects a slug pointer and blocks the
    # very next edit, silently stranding the agent after an apparently-successful
    # activation. Canonicalize to the resolved folder's number here so activation
    # and the gate agree (and so the `int(task_num)` in stub-expansion is safe).
    _resolved_num = task_file.parent.name.split("-", 1)[0]
    if _resolved_num.isdigit():
        task_num = _resolved_num.zfill(3)

    # Auto-close previous task if all gates are checked
    agent_dir = resolve_agent_dir(project_path)
    agent_dir.mkdir(parents=True, exist_ok=True)
    # Task 197 (its plan panel P1): a lane with no task registry gets one at the activation, from what stands — so the
    # watch and the close can tell a CLI-made folder from a hand-made one (the session start does the same). A failure
    # here is said, never a refusal of the activation.
    try:
        from tasks.registry import ensure_enrolled
        ensure_enrolled(project_path, agent_dir)
    except Exception as _enrol_err:          # noqa: BLE001
        print(f"note: the task registry could not be written ({_enrol_err}).", file=sys.stderr)
    session_id = require_session_id()   # task 105: never `sessions/""`
    session_dir = agent_dir / "sessions" / session_id
    session_state = session_dir / "current_state"
    prev_task = None
    if session_state.exists():
        prev_task = session_state.read_text(encoding="utf-8", errors="replace").strip()
    if prev_task and prev_task != task_num:
        from tasks.core import _extract_head_position, _extract_status
        prev_matches = list((agent_dir / "tasks").glob(f"{prev_task}-*/task.md"))
        if prev_matches:
            prev_file = prev_matches[0]
            prev_status = _extract_status(prev_file)
            prev_head = _extract_head_position(prev_file)
            if prev_status == "blocked":
                # A task that is honestly waiting for the owner is not being
                # abandoned: it is left as it is, without the override word, and
                # the command says so (PLAN S11 item 8, task 166 — task 160 → 161
                # needed `--force`, and `--force` then said "left in_progress"
                # of a task whose status stayed `blocked`). The EXACT token, as
                # `core._is_blocked` reads it — never a prefix — and asked BEFORE
                # the all-gates bounce below: a blocked task whose gates are all
                # checked waits for the owner about its close (impl panel r1).
                print(f"Task {prev_task} is blocked (waiting for a decision) — it stays blocked, "
                      f"with its reason; switching to task {task_num}. "
                      f"Resume it with: tasks work {prev_task}")
            elif prev_head == "(all gates checked)" and not prev_status.startswith("done"):
                # F14 blind-judge Finding 1 (the class, not a light-only
                # patch): this branch used to write `done` DIRECTLY — no
                # risk check, no review evidence, no verify contract, no
                # receipt. A policy-free second close path defeats the
                # whole 1.5.0 evidence contract, and `work done` is
                # supposed to be the ONLY thing that closes a task (the
                # 1.4.7 principle). The switch now bounces to the real
                # close; --force switches away leaving the task honestly
                # open, never silently done.
                if force:
                    print(f"--force: switching away from task {prev_task} "
                          "(all gates checked, NOT closed — left open; "
                          "close it with `tasks work done` later).")
                else:
                    print(f"Task {prev_task} has all gates checked but is not closed.\n"
                          f"Close it properly first: tasks work done   "
                          "(runs the verify contract + close policy)\n"
                          f"Or switch anyway: tasks work {task_num} --force   "
                          f"(leaves {prev_task} open)", file=sys.stderr)
                    sys.exit(1)
            elif not prev_status.startswith("done") and not force:
                # prev task still has open gates — don't silently abandon it.
                _gate_bounce(prev_task, prev_file, f"switching to task {task_num}")
                sys.exit(1)
            elif not prev_status.startswith("done"):
                print(f"--force: switching away from task {prev_task} with open gates "
                      f"(left as it is: {prev_status.split()[0] if prev_status.split() else 'open'}).")

    # The activation is allowed: NOW clear a block / reopen a done task — each one
    # decided inside its locked transform (the status may have moved since it was
    # read), and each BEFORE the pointer is written: a refusal changes nothing.
    # The pointer is PREPARED first — its directory and a temp file holding the
    # number — and only renamed into place after the status steps (impl panel r2,
    # post-D6 run 1): what can fail (no space, no permission) fails before task.md
    # is touched, so nothing ever has to be undone. An earlier fix undid a reopen
    # after a failed pointer write; that left other changes in place and could
    # close a task another session had activated meanwhile.
    try:
        _pointer_tmp = _prepare_pointer(session_state, task_num)
    except OSError as exc:
        print(f"Error: could not prepare the session pointer ({exc}) — task {task_num} "
              "was not activated. Nothing changed.", file=sys.stderr)
        sys.exit(1)
    try:
        # A stub is expanded BEFORE the status steps and the pointer (task 144, impl
        # panel r2): a refused expansion (the task changed meanwhile) then leaves no
        # pointer and no status change behind, and the claim below moves the expanded
        # template's `pending` to `in_progress`.
        # Expand stubs on activation
        task_content = read_task_text(task_file)
        import re as _stub_re
        # F7: custom playbook type names carry hyphens (`sp-eval`, the flagship
        # example in playbooks-README) — `\w+` can't match `stub:sp-eval` so the
        # marker survived `work` and the stub never expanded. Accept `-` too.
        # task 144: the marker LINE, outside a fence — never the text anywhere
        from tasks.core import stub_marker_type
        stub_type = stub_marker_type(task_content)
        stub_match = stub_type is not None
        if stub_match:
            # Extract user's Intent and Why sections before expanding
            def _extract_section(content, heading):
                pattern = rf'^## {heading}\n(.*?)(?=\n## |\Z)'
                m = _stub_re.search(pattern, content, _stub_re.MULTILINE | _stub_re.DOTALL)
                return m.group(1).strip() if m else ""

            user_intent = _extract_section(task_content, "Intent")
            user_why = _extract_section(task_content, "Why")
            user_refs = _extract_section(task_content, "References")

            # Render full template
            task_num_int = int(task_num)
            title = task_file.parent.name.split("-", 1)[1].replace("-", " ").title()

            # F18: a custom stub type (.agent/playbooks/<type>.md) must expand to
            # ITS playbook \u2014 the WHOLE file, as create_task does \u2014 not the base
            # Build template. Without this dispatch the custom playbook was never
            # loaded (`_load_playbook` only knows built-in PLAYBOOKS keys), so a
            # custom stub silently expanded to the base template and every custom
            # gate vanished on activation. Mirror create_task's dispatch exactly.
            from tasks.core import _find_custom_playbook, _load_playbook
            custom = _find_custom_playbook(project_path, stub_type)
            if custom:
                full_content = custom.read_text(encoding="utf-8", errors="replace")
                full_content = full_content.replace("{{NNN}}", f"{task_num_int:03d}")
                full_content = full_content.replace("{{TITLE}}", title)
            else:
                from tasks.template import render_template
                full_content = render_template(num=task_num_int, title=title, task_type=stub_type)
                # F3: Append playbook role template (same as create_task)
                role_template = _load_playbook(stub_type, project_path)
                if role_template:
                    full_content += "\n" + role_template + "\n"

            # Inject preserved user content
            if user_intent:
                # Try every base-template Intent placeholder variant.
                for placeholder in [
                    "(what we want to achieve \u2014 the outcome, not the activity)",
                    "(one line \u2014 what to do and how to verify)",
                    # F6: the `light` template's Intent placeholder (B1 twin) \u2014 was
                    # missing here, so `tasks new --stub light <name> <intent>`
                    # captured the intent in the stub but dropped it on activation.
                    "(one line \u2014 what to do and what proves it worked)",
                ]:
                    if placeholder in full_content:
                        full_content = full_content.replace(placeholder, user_intent)
                        break
            if user_why:
                full_content = full_content.replace(
                    "(why this matters now \u2014 urgency, context, what breaks if delayed)",
                    user_why,
                )
            # F1: Inject preserved references
            if user_refs and "(optional)" not in user_refs.lower():
                # Replace the default References content
                full_content = _stub_re.sub(
                    r'(## References\n).*?(?=\n---)',
                    f'## References\n{user_refs}',
                    full_content,
                    count=1,
                    flags=_stub_re.DOTALL,
                )

            # The stub's own status goes into the expanded template (post-D6 run 1): the
            # template says `pending`, and the resume / reopen that follow move only a
            # `blocked` / `done` task — a blocked or done stub then failed half-way.
            # Spliced into the LIVE status pair (post-D6 run 2) — the same fence-aware
            # reader/writer as every status write, never a literal text replace.
            from tasks.core import (_status_from_lines, _physical_lines as _pl144,
                                    _live_status_pair, _splice_status_value)
            _stub_status = str(_status_from_lines(_pl144(task_content)) or "").strip()
            if _stub_status and _stub_status != "pending":
                _lines144 = _pl144(full_content, keepends=True)
                _pair144 = _live_status_pair(_lines144)
                if _pair144 is not None:
                    _splice_status_value(_lines144, _pair144, _stub_status)
                    full_content = "".join(_lines144)

            # F8: standing gates land at expansion (a stub has no gates until
            # now) — same helper create_task uses, same LAST-gates guarantee.
            from tasks.core import append_standing_gates, load_config
            full_content, _sg_issues = append_standing_gates(
                full_content, load_config(project_path), task_num_int)
            for _msg in _sg_issues:
                print(f"[playbook] standing_gates: {_msg}", file=sys.stderr)

            # Task 058's locked transaction — on the bytes read UNDER the lock: if the task
            # changed since it was read above (another `tasks work`, an edit), the expansion
            # built from the old text is not written over it (task 144, impl panel r1).
            if _rewrite(task_file, lambda _t: full_content if _t == task_content else None) is None:
                print(f"Error: task {task_num} changed while it was being expanded (another "
                      "session or an edit); nothing written — run `tasks work` again.", file=sys.stderr)
                sys.exit(1)
            # Re-read for chat injection and display
            task_content = full_content
            print(f"Expanded stub to full {stub_type} template.")
            print()
            _print_guide(stub_type)
        # task 244: what this session's agent spent before this activation belongs to the task it leaves ("-": none)
        _record_agent_usage(agent_dir, prev_task or "-", [session_dir])     # the same task again: still its own
        _activate_status_then_publish(task_file, task_num, _resume_target, _reopen_target,
                                      _pointer_tmp, session_state)
        _record_transition(agent_dir, "work", task_num)
    finally:
        try:
            _pointer_tmp.unlink()
        except OSError:
            pass                        # published (renamed away) or already gone

    # Session GC runs in _gc_dead_sessions() at the CLI entry point — and
    # ALSO in scripts/session-start-hook, which sweeps the same directory at
    # every SessionStart (including `compact`). This comment used to name
    # only the Python one, which is part of how the hook's contradictory
    # mtime-only policy went unnoticed until task 027.
    #
    # NOTE: only ACTIVATION writes `current_state` — this line, the freehand
    # orchestrator arm, and prepare-merge's pointer rewrite. Nothing refreshes
    # it while the session works, so its mtime means "when the task was
    # activated" and is never a liveness signal. Deleting sessions by that
    # mtime is what task 027 fixed; don't reintroduce it.

    # Workflow rules — deferred from bootstrap to task activation
    from tasks.template import workflow_briefing
    print("=== WORKFLOW ===")
    print(workflow_briefing())
    print()

    # Capture recent chat messages into task.md
    recent_chat = _capture_recent_chat(project_path)
    if recent_chat:
        _inject_chat_into_task(task_file, recent_chat)
        print(f"Captured {len(recent_chat)} recent chat message(s) into References.")

    # Print the full task file
    print(read_task_text(task_file).rstrip())

    # F21 (batch-6 finding): the parked lifecycle taught its markers only
    # at CLOSE (own-task items). The consumption moment — a new task picks
    # up an EARLIER task's parked item — had no nudge, so task 012 consumed
    # 010's parked guard and 010's entry still reads open. One line, at
    # activation, only when parked debt exists.
    from tasks.core import open_parked_items as _opi
    from tasks.core import existing_task_numbers as _etn
    _parked_elsewhere = 0
    try:
        _existing = _etn(project_path)          # dangling promotions count (PLAN S4)
        for _tf in sorted((agent_dir / "tasks").glob("*/task.md")):
            if _tf == task_file:
                continue
            _parked_elsewhere += len(_opi(read_task_text(_tf), _existing))
    except OSError:
        pass
    if _parked_elsewhere:
        print(f"\nnote: {_parked_elsewhere} open parked item(s) in earlier "
              "tasks (`tasks parked` to list). If THIS task picks one up, "
              f"mark the source entry `[promoted → {task_num}]` so the "
              "lifecycle shows it consumed.", flush=True)


def cmd_new(cmd_args):
    """The `tasks new` arm — body moved verbatim from cli.py (1.5.9 split)."""
    # Parse --stub flag — position-independent, so a trailing
    # `tasks new feature name --stub` can't silently become Intent text
    # (gauntlet-155 wart: the flag was only honored in first position).
    # Task 116: everything after the first `--` is intent text, taken verbatim — a
    # `--stub` there is a word, and type and name must come before it.
    tail: list = []
    if "--" in cmd_args:
        at = cmd_args.index("--")
        cmd_args, tail = list(cmd_args[:at]), list(cmd_args[at + 1:])
    is_stub = "--stub" in cmd_args
    if is_stub:
        cmd_args = [a for a in cmd_args if a != "--stub"]
    if tail and len(cmd_args) != 2:
        print("Error: 'new' takes <type> <name> before `--`, the intent after it. Nothing changed.",
              file=sys.stderr)
        print("Usage: tasks new [--stub] <type> <name> [-- intent words …]", file=sys.stderr)
        sys.exit(2)
    cmd_args = cmd_args + tail

    if len(cmd_args) < 2:
        print("Error: 'new' requires a type and a name", file=sys.stderr)
        print("Usage: tasks new [--stub] <type> <name> [intent...]", file=sys.stderr)
        from tasks.core import list_all_types
        all_types = list_all_types(find_project_root())
        print(f"Types: {', '.join(all_types)}", file=sys.stderr)
        sys.exit(1)

    task_type = cmd_args[0]
    from tasks.core import list_all_types, _find_custom_playbook
    project_path_for_check = find_project_root()
    is_custom = _find_custom_playbook(project_path_for_check, task_type) is not None
    if task_type not in PLAYBOOKS and task_type not in ("quick", "light") and not is_custom:
        all_types = list_all_types(project_path_for_check)
        print(f"Error: unknown type '{task_type}'", file=sys.stderr)
        print(f"Types: {', '.join(all_types)}", file=sys.stderr)
        sys.exit(1)

    # args[1] = name, args[2:] = optional intent text
    task_name = cmd_args[1]
    intent_text = " ".join(cmd_args[2:]) if len(cmd_args) > 2 else None
    project_path = find_project_root()

    # Check if user included a task number prefix: it must be the next number, read under the
    # lane lock by the creator (task 195 — the check here used to read the directories alone,
    # outside any lock, and could pass a number a concurrent creator or a burned reservation took)
    import re as _re
    from tasks.registry import NumberTaken, RegistryUnreadable, unreadable_message
    num_match = _re.match(r'^(\d{3})-(.+)$', task_name)
    provided_num = None
    if num_match:
        provided_num = int(num_match.group(1))
        task_name = num_match.group(2)
    try:
        task_file = create_task(project_path, task_name, task_type=task_type,
                                intent_text=intent_text, stub=is_stub, number=provided_num)
    except NumberTaken as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"Usage: tasks new {task_type} {task_name}", file=sys.stderr)
        sys.exit(1)
    except RegistryUnreadable as e:
        print(unreadable_message(e), file=sys.stderr)
        sys.exit(1)
    pattern_name = PLAYBOOKS.get(task_type, f"custom ({task_type})")

    import re
    task_num_match = re.match(r'^(\d+)-', task_file.parent.name)
    task_num = task_num_match.group(1) if task_num_match else "?"

    _record_transition(resolve_agent_dir(project_path), "new", task_num, f"{task_type} {task_name}")
    print(f"Created: {task_file.relative_to(project_path)}")
    if is_stub:
        print(f"Stub ({pattern_name}) — expand with: tasks work {task_num}")
    elif task_type not in ("quick", "light"):
        print(f"Pattern: {pattern_name}")
        print(f"Next: fill in task.md gates, then ask user to run: tasks work {task_num}")
    else:
        print(f"Next: fill in task.md gates, then ask user to run: tasks work {task_num}")
    print()
    if not is_stub:
        _print_guide(task_type)

    # quick/light skip the full playbook-guide dump — shedding that
    # ceremony is their reason to exist (light still routes review by
    # risk; see the template's Risk Routing gate).
    if task_type not in ("quick", "light"):
        # Print full playbook so agent has workflow guidance inline
        playbook_path = _find_playbook_skill(project_path)
        if playbook_path:
            playbook_file = Path(playbook_path)
            if playbook_file.exists():
                print("=== PLAYBOOK (task.md design guide) ===")
                print("Use this to improve your task.md: select patterns and gates as appropriate,")
                print("or invent new ones. This is a starting point — expand as needed.")
                print()
                content = playbook_file.read_text(encoding="utf-8", errors="replace")
                # Strip sections not relevant to task design
                for marker in ["## Mind Map", "> Evidence base:"]:
                    idx = content.find(marker)
                    if idx > 0:
                        content = content[:idx]
                print(content.rstrip())
                print()
                print(f"Now fill in {task_file.relative_to(project_path)} — design a good task.md.")


def _print_guide(task_type) -> None:
    """The single copy of the template's instructions, for the gates about to be filled (task 213)."""
    from tasks.template import guide_text
    print(f"=== TASK GUIDE (again: tasks guide {task_type}) ===")
    print(guide_text(task_type))
    print()


def cmd_guide(cmd_args):
    """`tasks guide [<type>]` — prints the guide; writes nothing (task 213)."""
    from tasks.template import guide_text
    task_type = cmd_args[0] if cmd_args else None
    if task_type and task_type not in PLAYBOOKS and task_type not in ("quick", "light", "retro", "freehand"):
        print(f"(no built-in type {task_type!r} — the whole guide follows)", file=sys.stderr)
    print(guide_text(task_type))


def cmd_blocked(cmd_args):
    """The `tasks blocked` arm — body moved verbatim from cli.py (1.5.9 split)."""
    # #08: an honest state for "paused, waiting on the owner's decision" — so
    # the agent never fakes a checkbox or misuses freehand to end its turn.
    # Satisfies the Stop hook via the status marker (not gate text), records
    # the reason in task.md, shows as BLOCKED, and is cleared by `tasks work`.
    project_path = find_project_root()
    reason = " ".join(cmd_args).strip()
    if not reason:
        print('Error: a reason is required — tasks blocked "why you are paused '
              'and what decision you need"', file=sys.stderr)
        sys.exit(1)
    agent_dir = resolve_agent_dir(project_path)
    session_id = require_session_id()   # task 105: never `sessions/""`
    state_file = agent_dir / "sessions" / session_id / "current_state"
    active = state_file.read_text(encoding="utf-8", errors="replace").strip() if state_file.exists() else None
    if not active:
        print("No active task to block. Activate one first: tasks work <N>",
              file=sys.stderr)
        sys.exit(1)
    matches = list((agent_dir / "tasks").glob(f"{active}-*/task.md"))
    if not matches:
        print(f"Task {active} not found", file=sys.stderr)
        sys.exit(1)
    from tasks.core import set_task_blocked
    try:
        set_task_blocked(matches[0], reason)
    except ValueError as exc:           # V7b: a refusal, not a traceback
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Task {active} marked BLOCKED — {' '.join(reason.split())}")
    print("You can end your turn; the Stop hook won't block a blocked task. "
          f"Resume with: tasks work {active}")


def cmd_handoff(cmd_args):
    """The `tasks handoff` arm (C1): codify the proven manual session-handoff.

    Writes the mechanical ~80% (project + nested-code-root git state, gate
    progress, latest verification receipt, a timestamp) into the ACTIVE task's
    `## Handoff` section, prints instructions for the agent to append the
    judgment ~20%, then blocks the task with reason "handoff" (reusing the
    honest blocked state — never a faked checkbox). `tasks bootstrap` surfaces
    the newest unconsumed handoff; `tasks work <N>` consumes it."""
    # S1b (task 080): the verb takes no arguments. It used to ignore them, so a
    # typo or a guessed flag (`handoff --bogus`) wrote a `## Handoff` and BLOCKED
    # the active task. Refuse before any read or write. (-h/--help never gets
    # here: cli.main intercepts help globally, dry.)
    if cmd_args:
        print(f"Error: tasks handoff takes no arguments (got: {' '.join(cmd_args)}). "
              "Nothing changed.", file=sys.stderr)
        print("Usage: tasks handoff", file=sys.stderr)
        sys.exit(1)
    project_path = find_project_root()
    agent_dir = resolve_agent_dir(project_path)
    session_id = require_session_id()   # task 105: never `sessions/""`
    state_file = agent_dir / "sessions" / session_id / "current_state"
    active = state_file.read_text(encoding="utf-8", errors="replace").strip() \
        if state_file.exists() else None
    if not active:
        print("No active task to hand off. Activate one first: tasks work <N>",
              file=sys.stderr)
        sys.exit(1)
    matches = list((agent_dir / "tasks").glob(f"{active}-*/task.md"))
    if not matches:
        print(f"Task {active} not found", file=sys.stderr)
        sys.exit(1)
    task_file = matches[0]
    from tasks.core import (_live_status_index, build_handoff_section,
                            set_task_blocked, write_handoff)
    # V7b preflight (round-1 panel): the handoff is only honest if the task can
    # then be marked blocked. Check the live `## Status` value line BEFORE the
    # `## Handoff` write so a refusal leaves task.md byte-identical.
    try:
        from tasks.core import _physical_lines as _plines   # same line model as set_task_blocked
        _hl = _plines(read_task_text(task_file))
    except OSError:
        _hl = []
    if _live_status_index(_hl) is None:
        print(f"Error: {task_file} has no live `## Status` heading with a value "
              "line (missing, hidden inside a code fence, or directly followed by "
              "another section) — refusing to hand off: the task could not be "
              "marked blocked. Fix the heading/fence first. Nothing changed.",
              file=sys.stderr)
        sys.exit(1)
    section = build_handoff_section(project_path, task_file)
    # Task 058: the section AND the honest blocked state (reason "handoff" —
    # bootstrap keys on it to surface the handoff, and `tasks work` clears it)
    # land in ONE locked transaction; a refusal writes nothing at all.
    from tasks.core import write_handoff_and_block
    try:
        write_handoff_and_block(task_file, section, "handoff")
    except ValueError as exc:
        print(f"Error: the handoff was NOT written — {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Handoff written to task {active} — mechanical state captured in the "
          "## Handoff section, task marked BLOCKED (reason: handoff).")
    print("")
    print("NOW, before you stop: append the judgment ~20% the tooling can't know "
          "under the '### Agent notes' scaffold in that section —")
    print("  - your in-flight reasoning, decisions not yet written in the file, "
          "dead ends already ruled out.")
    print(f"A fresh session's `tasks bootstrap` will surface this handoff; "
          f"resume (and consume) it with: tasks work {active}")


def cmd_parked(cmd_args):
    """The `tasks parked` arm — body moved verbatim from cli.py (1.5.9 split)."""
    # P9: the standing query that makes parked items un-swallowable. Lists
    # OPEN parked items across all tasks, oldest first; --all includes
    # resolved ones. Nothing else in the tool ever surfaced them again.
    project_path = find_project_root()
    show_all = "--all" in cmd_args
    from tasks.core import scan_parked
    everything = scan_parked(project_path, open_only=False)     # one read of every record
    items = everything if show_all else [it for it in everything if it["status"] in ("open", "dangling")]
    if not items:
        print("No parked items." if show_all else "No open parked items.")
    else:
        scope = "" if show_all else " open"
        print(f"{len(items)}{scope} parked item(s):")
        cur = None
        for it in items:
            if it["task"] != cur:
                cur = it["task"]
                print(f"\n  T{it['task']:03d} — {it['slug'].replace('-', ' ')}")
            if it["status"] == "open":
                tag = ""
            elif it["status"] == "dangling":
                missing = ", ".join(f"{n:03d}" for n in it.get("missing", []))
                tag = f"  [dangling — task {missing} does not exist]"
            else:
                tag = f"  [{it['status']}]"
            print(f"    - {it['item']}{tag}")
    if not show_all:
        # a dated owner deferral is not open, but it must not vanish (PLAN S4)
        deferred = [it for it in everything if it["status"] == "deferred"]
        if deferred:
            print(f"\n({len(deferred)} deferred by a dated owner decision — `tasks parked --all` lists them.)")
        print("\nResolve each: promote (`tasks new …` then mark the bullet "
              "`[promoted → NNN]`), dismiss (`[dismissed: reason]`), or leave "
              "open deliberately.")


def cmd_adopt(cmd_args):
    """`tasks adopt <task folder>` (task 195): register a task folder the CLI did not make — one renamed by
    hand (the freehand gate asks for it), or one made by hand that should stay — with the lane's task
    registry, under its lock. Idempotent: a folder it already knows is left as it is. A refusal says why
    and changes nothing. One journal record per adoption."""
    from tasks.registry import AdoptRefused, RegistryUnreadable, adopt, unreadable_message
    project_path = find_project_root()
    agent_dir = resolve_agent_dir(project_path)
    # a trailing slash or a path to the folder (tab completion) names the folder; a path outside this lane's
    # `tasks/` is refused (task 195, impl panel r2, gemini)
    arg = cmd_args[0].rstrip("/")
    name = arg
    if "/" in arg:
        given = Path(arg).resolve()
        if given.parent != (agent_dir / "tasks").resolve():
            print(f"Error: {cmd_args[0]!r} is not a folder of this lane's tasks/ ({agent_dir / 'tasks'}). "
                  "Nothing changed.", file=sys.stderr)
            sys.exit(1)
        name = given.name
    try:
        result = adopt(project_path, agent_dir, name)
    except AdoptRefused as e:
        print(f"Error: {e}. Nothing changed.", file=sys.stderr)
        sys.exit(1)
    except RegistryUnreadable as e:
        print(unreadable_message(e), file=sys.stderr)
        sys.exit(1)
    if result == "known":
        print(f"{name} is already known to the task registry — nothing changed.")
        return
    print(f"Adopted {name}: the task registry now knows it.")
    try:                                   # a journal failure never changes the adoption
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "_pb_journal_adopt", Path(__file__).resolve().parent.parent / "scripts" / "pb_journal.py")
        if _spec is not None and _spec.loader is not None:
            _pbj = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_pbj)
            _sid = os.environ.get("PLAYBOOK_SESSION_ID", "")
            _pbj.append(agent_dir, "adopt", "record", "task folder adopted into the registry", _sid,
                        path=f"tasks/{name}")
    except Exception:
        pass


# The freehand rename gate (task 195, plan panel T8): a renamed task directory is one the task
# registry no longer names — `tasks adopt <new name>` registers it again.
_FREEHAND_RENAME_GATE = ("- [ ] Rename the task folder and header to what was done, run "
                         "`.claude/bin/tasks adopt <new folder name>`, check this last.\n")
# Task 223 (the owner's "Titlu de o propoziție"): one sentence a gate, its command kept; what each gate used to say
# beyond its title is `tasks guide freehand` (template.FREEHAND_GATE_NOTES). `cmd_freehand log` finds the log gate by
# its `Freehand log` start.
_FREEHAND_LOG_GATE = ("- [ ] Freehand log \u2014 run `.claude/bin/tasks freehand log`, then add checked gates for the "
                      "work done.\n")
# A freehand block carries no task pointer, so it names its own guide (task 225; grok, 223's impl round 2).
_FREEHAND_POINTER = "> What each freehand gate asks: `.claude/bin/tasks guide freehand`.\n"


def cmd_freehand(cmd_args):
    """The `tasks freehand` arm — body moved verbatim from cli.py (1.5.9 split)."""
    project_path = find_project_root()
    sub = cmd_args[0] if cmd_args else None

    if sub == "log":
        # Extract chat_log messages from freehand-start to now into task.md
        agent_dir = resolve_agent_dir(project_path)
        state_file = _state_file(project_path)
        if not state_file.exists():
            print("Error: no active task", file=sys.stderr)
            sys.exit(1)
        task_num = state_file.read_text(encoding="utf-8", errors="replace").strip()
        tasks_dir = agent_dir / "tasks"
        matches = list(tasks_dir.glob(f"{task_num}-*/task.md"))
        if not matches:
            print(f"Error: task {task_num} not found", file=sys.stderr)
            sys.exit(1)
        task_file = matches[0]
        task_text = read_task_text(task_file)

        # Find the freehand-start marker
        import re
        # Use findall + take last — supports multiple freehand blocks in one task
        all_markers = re.findall(r'<!-- freehand-start: (.+?) -->', task_text)
        marker_match = all_markers[-1] if all_markers else None
        if not marker_match:
            print("Error: no freehand-start marker found in task.md", file=sys.stderr)
            sys.exit(1)

        # Parse the start timestamp
        from datetime import datetime, timezone
        start_str = marker_match.strip()
        try:
            # B2: the freehand writer emits a `Z`-suffixed UTC stamp, but
            # datetime.fromisoformat() rejects a trailing `Z` before Python 3.11
            # (Ubuntu 22.04 ships 3.10) — normalize `Z` → `+00:00` so `freehand
            # log` works on the plugin's declared 3.10+ floor, not just 3.11+.
            _iso = start_str[:-1] + "+00:00" if start_str.endswith("Z") else start_str
            start_ts = datetime.fromisoformat(_iso)
            if start_ts.tzinfo is None:
                start_ts = start_ts.replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Error: cannot parse freehand-start timestamp: {start_str}", file=sys.stderr)
            sys.exit(1)

        # Read chat_log.md and extract messages in the span
        chat_log = agent_dir / "chat_log.md"
        if not chat_log.exists():
            print(f"Error: {chat_log.relative_to(project_path).as_posix()} not found", file=sys.stderr)
            sys.exit(1)

        log_text = chat_log.read_text(encoding="utf-8", errors="replace")
        # Parse message blocks: **[MNNN]** [YYYY-MM-DD HH:MM:SS UTC]
        msg_pattern = re.compile(
            r'^(\*\*\[M\d+\]\*\* \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\].*)',
            re.MULTILINE
        )
        # Split log into message blocks by the --- separator
        blocks = log_text.split("\n---\n")
        extracted = []
        for block in blocks:
            m = msg_pattern.search(block)
            if m:
                ts_str = m.group(2)
                try:
                    msg_ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if msg_ts >= start_ts:
                    extracted.append(block.strip())

        if not extracted:
            print("No chat_log messages found in freehand span.")
            return

        # Insert extracted messages into task.md below the Freehand log gate
        log_gate_pattern = re.compile(r'^(- \[ \] Freehand log\b.*)', re.MULTILINE)
        log_gate_match = log_gate_pattern.search(task_text)
        if not log_gate_match:
            print("Error: no '- [ ] Freehand log' gate found in task.md", file=sys.stderr)
            sys.exit(1)

        log_content = "\n\n" + "\n\n---\n\n".join(extracted) + "\n"

        def _t_log(text: str) -> "str | None":
            m = log_gate_pattern.search(text)
            if m is None:
                return None                        # the gate vanished under us
            return text[:m.end()] + log_content + text[m.end():]

        _rewrite(task_file, _t_log)   # task 058: locked transaction
        print(f"Inserted {len(extracted)} chat_log messages into task.md")
        return

    # Main freehand command: insert Freehand block into active task
    state_file = _state_file(project_path)
    agent_dir = resolve_agent_dir(project_path)

    if state_file.exists():
        task_num = state_file.read_text(encoding="utf-8", errors="replace").strip()
    else:
        task_num = None

    if not task_num:
        # Orchestrator mode: create a minimal freehand task (no Design Phase)
        print("No active task — creating freehand session...")
        from tasks.core import _slugify
        from tasks.registry import RegistryUnreadable, create_task_dir, unreadable_message
        # Write minimal template — Freehand gate is first unchecked gate
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _render(num: int, _name: str) -> str:
            return (
                f"# {num:03d} - Freehand\n\n"
                f"## Status\nin_progress\n\n"
                f"## Intent\n(freehand session — intent determined during work)\n\n"
                f"## Work Plan\n\n"
                f"### Freehand\n"
                f"<!-- freehand-start: {now_iso} -->\n"
                f"{_FREEHAND_POINTER}"
                f"- [ ] Freehand\n"
                f"{_FREEHAND_LOG_GATE}"
                f"- [ ] Rewrite this freehand work into normal task gates inside this task so the final trace reads like ordinary tracked work\n"
                f"{_FREEHAND_RENAME_GATE}")
        # made, numbered and registered under the lane lock (task 195 — `mkdir(exist_ok=True)` let two at
        # once SHARE one directory)
        try:
            task_dir = create_task_dir(project_path, agent_dir, _slugify("freehand"), "freehand", _render)
        except RegistryUnreadable as e:
            print(unreadable_message(e), file=sys.stderr)
            sys.exit(1)
        task_num = task_dir.name.split("-", 1)[0]
        task_file = task_dir / "task.md"
        # Activate it
        session_id = require_session_id()   # task 105: never `sessions/""`
        session_dir = agent_dir / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        # task 244 (impl round 1, opus): what this session's agent spent before belongs to the task it leaves
        _prev = (session_dir / "current_state")
        _prev_task = (_prev.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0].strip()
                      if _prev.is_file() and not _prev.is_symlink() else "")
        _record_agent_usage(agent_dir, _prev_task or "-", [session_dir])
        atomic_write(session_dir / "current_state", f"{task_num}\n")
        _record_transition(agent_dir, "new", task_num, "freehand freehand")
        _record_transition(agent_dir, "work", task_num)
        print(f"Created and activated task {task_num}")
    else:
        # Work mode: insert freehand block into current task
        tasks_dir = agent_dir / "tasks"
        matches = list(tasks_dir.glob(f"{task_num}-*/task.md"))
        if not matches:
            print(f"Error: task {task_num} not found", file=sys.stderr)
            sys.exit(1)
        task_file = matches[0]

        from datetime import datetime, timezone
        task_text = read_task_text(task_file)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        freehand_block = (
            f"\n### Freehand\n"
            f"<!-- freehand-start: {now_iso} -->\n"
            f"{_FREEHAND_POINTER}"
            f"- [ ] Freehand\n"
            f"{_FREEHAND_LOG_GATE}"
            f"- [ ] Rewrite this freehand work into normal task gates inside this task so the final trace reads like ordinary tracked work\n"
            f"{_FREEHAND_RENAME_GATE}"
        )

        # Find Work Plan section and insert before first unchecked gate there
        import re
        # `## Work Plan` (full templates) OR `## Work` (quick/light — task 073
        # finding B8: the light shape has no `## Work Plan`, so the block used to
        # land at EOF, INSIDE the trailing `## Parked` section, and `tasks parked`
        # listed the Freehand gates as parked debt). Never after `## Parked`.
        # Fence-aware (impl panel of task 073): a fenced example that quotes
        # `## Work` / a gate / `## Parked` must never receive the block.
        from tasks.core import _iter_fenced_flags, _physical_lines
        _pl = _physical_lines(task_text, keepends=True)
        _fenced = _iter_fenced_flags([l.rstrip("\r\n") for l in _pl],
                                     unclosed_is_live=True, track_indented_code=False)
        _offsets = []
        _acc = 0
        for _l in _pl:
            _offsets.append(_acc)
            _acc += len(_l)

        def _first_live(pattern, start=0):
            for _idx in range(start, len(_pl)):
                if not _fenced[_idx] and re.match(pattern, _pl[_idx].rstrip("\r\n")):
                    return _idx
            return None

        wp_idx = _first_live(r'## Work( Plan)?\b')
        parked_idx = _first_live(r'## Parked\b')
        if wp_idx is not None:
            gate_idx = _first_live(r'- \[ \]', wp_idx + 1)
            sep_idx = _first_live(r'---\s*$', wp_idx + 1)
            if gate_idx is not None and (parked_idx is None or gate_idx < parked_idx):
                insert_pos = _offsets[gate_idx]
            elif sep_idx is not None and (parked_idx is None or sep_idx < parked_idx):
                insert_pos = _offsets[sep_idx]
            elif parked_idx is not None and parked_idx > wp_idx:
                insert_pos = _offsets[parked_idx]
            else:
                insert_pos = len(task_text)
        elif parked_idx is not None:
            insert_pos = _offsets[parked_idx]
        else:
            insert_pos = len(task_text)

        # Task 058: the offsets above were computed from `task_text`, so the
        # write must apply to THOSE bytes — the transform refuses (returns None,
        # leaving the file untouched) if the file changed under us, and the
        # caller is told rather than silently clobbering the other writer.
        def _t_block(current: str) -> "str | None":
            if current != task_text:
                return None
            return task_text[:insert_pos] + freehand_block + "\n" + task_text[insert_pos:]

        if _rewrite(task_file, _t_block) is None:
            print("Error: task.md changed while the freehand block was being "
                  "composed — nothing was written; re-run `tasks freehand`.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"Freehand block inserted in task {task_num}")
    # task 223 (impl round 1): a freehand block carries no pointer, so the guide its gates' explanations live in is
    # printed here, on both paths
    _print_guide("freehand")
    print(f"Freehand mode active. Agent: wait for user instructions. Close only when user says done.")
