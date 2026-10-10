"""Chat-log history and attribution: the `context`, `intent`, `timeline`,
`tagger`, `tag`, `retro`, and `log` arms.

Boundary: every command that READS the per-lane conversation record
(chat_log.md + bash_history) to attribute, tag, extract, or summarize it —
span-based context with the F2 timestamp-window fallback, the vertical-retro
intent extractions (delegating to tasks.intent), task-transition timelines,
span tagging, retro-task generation (delegating to tasks.retro), and the
compact log view. Nothing here writes task state except `tag` (attribution
spans into chat_log.md) and `retro` (a generated retro task). Imports stdlib
+ tasks.core + tasks.shared + leaf libs; never a command module
(design-1.5.9.md §4).
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path
from tasks.atomic import atomic_write
from tasks.core import resolve_agent_dir
from tasks.shared import find_project_root


def cmd_context(cmd_args):
    """The `tasks context` arm — body moved verbatim from cli.py (1.5.9 split)."""
    if not cmd_args:
        print("Error: 'context' requires a task number", file=sys.stderr)
        print("Usage: tasks context <number>", file=sys.stderr)
        sys.exit(1)

    task_num = cmd_args[0]
    if task_num.isdigit():
        task_num = task_num.zfill(3)
    project_path = find_project_root()

    chat_log = resolve_agent_dir(project_path) / "chat_log.md"
    import re
    open_tag = re.compile(r'^<!--\s*T' + re.escape(task_num) + r'\s*-->$')
    close_tag = re.compile(r'^<!--\s*/T' + re.escape(task_num) + r'\s*-->$')

    # Read, never exists() first (task 138 G3-5): under a read deny the stat
    # fails too, and Python 3.13's exists() then answers False.
    try:
        chat_text = chat_log.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print(f"No {chat_log.relative_to(project_path).as_posix()} found.", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        # Task 125: inside a judge's sandbox the chat log is masked on purpose.
        print(f"Cannot read {chat_log.relative_to(project_path).as_posix()} here "
              f"({type(e).__name__}) — a review judge's sandbox hides the chat log; "
              "the task's Intent and Recent Chat in task.md carry the user's words.",
              file=sys.stderr)
        sys.exit(1)

    spans = []
    current_span = []
    inside = False
    for line in chat_text.splitlines():
        stripped = line.strip()
        if not inside and open_tag.match(stripped):
            inside = True
            continue
        elif inside and close_tag.match(stripped):
            spans.append("\n".join(current_span))
            current_span = []
            inside = False
            continue
        if inside:
            current_span.append(line)

    # Handle unclosed span at end of file
    if inside and current_span:
        spans.append("\n".join(current_span))

    if not spans:
        # F2 (untagged projects): `<!-- TNNN -->` spans are written only by
        # `tasks tag`, which nothing runs automatically — so this path was
        # blind for EVERY task on most projects while gate entries +
        # bash_history held everything needed to attribute messages. Fall
        # back to timestamp-window attribution (the same fallback `tasks
        # intent`'s chat layer uses); still fail loudly when nothing is
        # attributable. stdout stays pure messages — the provenance note
        # goes to stderr.
        fallback_msgs = []
        try:
            _n = int(task_num)
        except ValueError:
            _n = None
        if _n is not None:
            from tasks.retro import build_task_windows, extract_chatlog
            _bash_history = resolve_agent_dir(project_path) / "bash_history"
            _windows = build_task_windows(
                chat_log, _bash_history if _bash_history.exists() else None)
            if _n in _windows:
                fallback_msgs = [m for m in extract_chatlog(chat_log, _windows)
                                 if m.get("task") == _n]
        if not fallback_msgs:
            print(f"No attributed messages for task {task_num}.", file=sys.stderr)
            sys.exit(1)
        print(f"note: no <!-- T{task_num} --> tags in chat_log.md; messages "
              "attributed via timestamp window (gate entries + bash_history). "
              "Run `tasks tag` to persist attribution.", file=sys.stderr)
        _max_line = 200
        for m in fallback_msgs:
            text = " ".join(m["text"].split())
            if len(text) > _max_line:
                text = text[:_max_line] + "..."
            print(f"[M{m['id']:03d}] {text}")
        # spans is empty: the tagged-span output loop below is a no-op.

    # Token-efficient output: strip markdown boilerplate, one line per message
    import re as _re
    max_line = 200
    msg_header = _re.compile(r'^\*\*\[(M\d+)\]\*\*.*')
    gate_header = _re.compile(r'^\*\*\[G\d+:\d+\]\*\*.*')
    for span in spans:
        msg_id = None
        msg_lines = []
        in_gate = False
        for line in span.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "---":
                in_gate = False
                continue
            if gate_header.match(stripped):
                in_gate = True
                continue
            if in_gate:
                continue
            m = msg_header.match(stripped)
            if m:
                # Flush previous message
                if msg_id and msg_lines:
                    text = " ".join(msg_lines)
                    if len(text) > max_line:
                        text = text[:max_line] + "..."
                    print(f"[{msg_id}] {text}")
                msg_id = m.group(1)
                msg_lines = []
            else:
                msg_lines.append(stripped)
        # Flush last message
        if msg_id and msg_lines:
            text = " ".join(msg_lines)
            if len(text) > max_line:
                text = text[:max_line] + "..."
            print(f"[{msg_id}] {text}")

def intent_approval(calls: int, seat: str, limit: str, cap: str) -> str:
    """What `tasks intent --yes` must carry to approve a run of `calls` judge calls on
    `seat`, each with the time limit `limit` and (for a claude seat) the budget cap
    `cap`: the four figures themselves, `<calls>@<seat>@<limit>@<cap>` (`-` for no
    cap). Not a digest of them (post-D6 run 1: an eight-digit one gave two different
    time limits the same id). One-to-one with the figures whatever the seat's name
    holds: `calls` is digits, and neither of the last two fields can hold an `@`
    (the limit is `<number>s` or `unlimited`; the cap is a string Python's float()
    accepted, or `-`), so the seat is exactly what lies between the first `@` and
    the last two."""
    return f"{calls}@{seat}@{limit}@{cap or '-'}"


def cmd_intent(cmd_args):
    """The `tasks intent` arm — body moved verbatim from cli.py (1.5.9 split)."""
    # Vertical retro: 4 blind intent extractions over one task's layers.
    if not cmd_args:
        print("Error: 'intent' requires a task number", file=sys.stderr)
        print("Usage: tasks intent <number> [--yes <calls>@<seat>@<limit>@<cap>] [--chat-file P] [--base REF --head REF] "
              "[--collect-only] [--timeout S]", file=sys.stderr)
        sys.exit(1)

    task_num = cmd_args[0]
    if task_num.isdigit():
        task_num = task_num.zfill(3)
    chat_file = base = head = None
    collect_only = False
    # Task 173 (owner's D3-C4, 2026-09-24): the extractions are judge calls, and the
    # command made them unasked. Bare, it now says what it would spend and stops;
    # --collect-only never spent and is not asked. The approval is the LINE the bare
    # command printed — its four figures, `--yes <calls>@<seat>@<limit>@<cap>` — not
    # a bare yes: the evidence is collected again on the re-run and can have grown in
    # between (the user's own "yes" in the chat can make a task's chat layer
    # available), so a bare yes given for one call could start two (impl panel r1,
    # codex-high). None = not given; "" = given with nothing after it.
    approved = None
    # None = not yet resolved; the real default comes from tasks.core so
    # `tasks intent` honours the same review knobs as plan/impl review
    # instead of pinning its own 300s. --timeout still overrides.
    timeout_secs = None
    i = 1
    while i < len(cmd_args):
        a = cmd_args[i]
        if a == "--chat-file" and i + 1 < len(cmd_args):
            chat_file = Path(cmd_args[i + 1]); i += 2
        elif a == "--base" and i + 1 < len(cmd_args):
            base = cmd_args[i + 1]; i += 2
        elif a == "--head" and i + 1 < len(cmd_args):
            head = cmd_args[i + 1]; i += 2
        elif a == "--collect-only":
            collect_only = True; i += 1
        elif a == "--yes":
            if i + 1 < len(cmd_args) and not cmd_args[i + 1].startswith("--"):
                approved = cmd_args[i + 1]; i += 2
            else:
                approved = ""; i += 1
        elif a == "--timeout" and i + 1 < len(cmd_args):
            timeout_secs = int(cmd_args[i + 1]); i += 2
        else:
            print(f"Error: unknown option for intent: {a}", file=sys.stderr)
            sys.exit(1)

    if bool(base) != bool(head):
        print("Error: --base and --head must be given together (an explicit range)",
              file=sys.stderr)
        sys.exit(1)

    from tasks.intent import (
        collect_all, run_extractions, make_default_runner,
        write_run, find_task_dir, new_run_id, last_intent_entry, LAYERS,
    )
    project_path = find_project_root()
    from tasks.core import format_timeout_label
    if timeout_secs is None:
        from tasks.core import resolve_review_timeout
        timeout_secs = resolve_review_timeout(project_path)
    agent_dir = resolve_agent_dir(project_path)
    task_dir = find_task_dir(agent_dir / "tasks", task_num)
    if task_dir is None:
        print(f"Error: no task {task_num} under {agent_dir / 'tasks'}", file=sys.stderr)
        sys.exit(1)

    slices = collect_all(project_path, agent_dir, task_dir, task_num,
                         chat_file=chat_file, base=base, head=head)
    print(f"Intent review — task {task_num} ({task_dir.name})")
    for layer in LAYERS:
        s = slices[layer]
        print(f"  {layer:7} {'✓' if s.available else '✗'}  {s.provenance}")
    avail = [l for l in LAYERS if slices[l].available]
    if not avail:
        print("Error: no available evidence on any layer — nothing to infer. "
              "Pass --chat-file and/or --base/--head.", file=sys.stderr)
        sys.exit(1)

    judge = budget = None
    if not collect_only:
        import shlex
        from tasks.core import resolve_judge_budget
        from tasks.intent import resolve_default_seat
        # Resolved ONCE, here: these are the judge and the cap the approval is
        # checked against, and the same two are handed to the runner below, which
        # used to resolve them a second time — a configuration that changed between
        # the two reads ran another judge or cap under the approval (post-D6 run 1).
        judge = resolve_default_seat(project_path)
        provider, _variant, seat = judge
        budget = str(resolve_judge_budget(project_path))
        limit = format_timeout_label(timeout_secs)
        cap = budget if provider == "claude" else ""   # the one judge CLI with a budget knob
        # Every figure of the line, as text (intent_approval): an approval of calls
        # and seat alone ran after `--timeout` or the budget was raised (impl panel
        # r2, codex-high and codex-medium).
        quote = intent_approval(len(avail), seat, limit, cap)
        if approved != quote:
            if approved == "":
                print("\ntasks intent: `--yes` needs what it approves after it "
                      "(`--yes <calls>@<seat>@<limit>@<cap>`).", file=sys.stderr)
            elif approved is not None:
                print(f"\ntasks intent: `--yes {shlex.quote(approved)}` does not approve what a run would "
                      "spend now — the evidence, the default judge, the time limit or the budget cap "
                      "changed since that line was printed, or this command never printed it.",
                      file=sys.stderr)
            capped = f", each capped at ${cap}" if cap else ""
            print(f"\ntasks intent: this would run {len(avail)} judge call(s) on {seat} — "
                  f"time limit {limit} each{capped}. "
                  "Nothing was spent and nothing was written.\n"
                  f"Re-run with `--yes {shlex.quote(quote)}` to spend exactly that, or with "
                  "--collect-only to write the prompts with no model call.", file=sys.stderr)
            sys.exit(2)

    run_id = new_run_id()
    if collect_only:
        from tasks.intent import build_prompt
        reports = {l: (build_prompt(slices[l]) if slices[l].available
                       else f"# Intent inferred from {l}\n\n_(no evidence — "
                            f"{slices[l].provenance})_\n") for l in LAYERS}
        print("\n(--collect-only: wrote prompts, skipped model calls)")
    else:
        print(f"\nRunning {len(avail)} blind extraction(s) "
              f"(default judge, {format_timeout_label(timeout_secs)} each)...", flush=True)
        reports = run_extractions(slices, make_default_runner(
            project_path, timeout_secs=timeout_secs, task=task_num, judge=judge, budget_usd=budget))

    run_dir = write_run(task_dir, slices, reports, run_id=run_id)
    rel = run_dir.relative_to(project_path)
    print(f"\nReports written: {rel}/")
    print(f"Grading sheet:   {rel}/review.md")
    prior = last_intent_entry(project_path / "INTENT.md", task_num)
    if prior:
        print("Prior validated intent exists — reconcile as a DELTA against INTENT.md.")
    print("\nNext: read review.md with the user, grade the seams, then append "
          "vetted intent to INTENT.md (the /intent command drives this).")

# timestamp | AGENT/SCRIPT | [path/]tasks work|new …
_ACTIVATION_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (\w+) \| '
    r'(?:.*/)?(tasks (?:work|new) .+)$'
)


def activations(history_text: str):
    """(timestamp, command) for each `tasks work` / `tasks new` line of a bash_history,
    in file order — the one reader `tasks timeline` and `tasks tagger` share.

    An older logger wrote a command twice: the agent's line (`AGENT`) and, right under
    it, the script's echo (`SCRIPT`), in the same second. The readers hid that by
    dropping every SECOND sight of a command — which, with a logger that writes a
    command once, hid every second genuine activation: `work 7`, `work 8`, `work 7`
    showed two entries (retro 107 R12; fixed in task 171). An echo is recognised as
    exactly what it was: a `SCRIPT` line whose command and timestamp are those of the
    `AGENT` activation on the line directly above it IN THE FILE (any line in between
    ends the pairing — impl panel round 2). Nothing else is dropped — the same
    command twice in one second, by the agent, is two activations (the first version
    of this rule looked only at the text and the second, and lost them: impl panel
    round 1). The shipped loggers write `AGENT` lines only; measured on this
    workspace's archived history (2026-08-22 … 09-25): 542 activation lines, all
    `AGENT`, no two alike in one second."""
    previous = None                      # (timestamp, kind, command) when the line above was an activation
    for line in history_text.splitlines():
        m = _ACTIVATION_RE.match(line)
        if not m:
            previous = None
            continue
        stamp, kind, cmd = m.group(1), m.group(2), m.group(3)
        echo = kind == "SCRIPT" and previous == (stamp, "AGENT", cmd)
        previous = (stamp, kind, cmd)
        if not echo:
            yield stamp, cmd


def cmd_timeline(cmd_args):
    """The `tasks timeline` arm — body moved verbatim from cli.py (1.5.9 split)."""
    project_path = find_project_root()
    bash_history = resolve_agent_dir(project_path) / "bash_history"
    if not bash_history.exists():
        print(f"No {bash_history.relative_to(project_path).as_posix()} found.", file=sys.stderr)
        sys.exit(1)

    printed = 0
    for stamp, cmd in activations(bash_history.read_text(encoding="utf-8", errors="replace")):
        print(f"{stamp}  {cmd}")
        printed += 1
    if not printed:   # task 073 (C2): silence looked like a crash
        print("(no `tasks work`/`tasks new` activations recorded in bash_history yet)", file=sys.stderr)

def cmd_tagger(cmd_args):
    """The `tasks tagger` arm — body moved verbatim from cli.py (1.5.9 split)."""
    project_path = find_project_root()
    chat_log = resolve_agent_dir(project_path) / "chat_log.md"
    bash_history = resolve_agent_dir(project_path) / "bash_history"
    if not chat_log.exists():
        print(f"No {chat_log.relative_to(project_path).as_posix()} found.", file=sys.stderr)
        sys.exit(1)
    if not bash_history.exists():
        print(f"No {bash_history.relative_to(project_path).as_posix()} found.", file=sys.stderr)
        sys.exit(1)

    import re

    # 1. Parse messages from chat_log.md: (timestamp, msg_id, text)
    msg_header = re.compile(
        r'^\*\*\[(M\d+)\]\*\* \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]'
    )
    gate_header = re.compile(r'^\*\*\[G\d+:\d+\]\*\*')
    entries = []  # (timestamp_str, sort_key, display_line)
    max_line = 200

    msg_id = None
    msg_ts = None
    msg_lines = []
    in_gate = False

    def flush_msg():
        if msg_id and msg_lines:
            text = " ".join(msg_lines)
            if len(text) > max_line:
                text = text[:max_line] + "..."
            entries.append((msg_ts, 0, f"[{msg_id}] {text}"))

    for line in chat_log.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "---":
            in_gate = False
            continue
        if gate_header.match(stripped):
            in_gate = True
            continue
        if in_gate:
            continue
        m = msg_header.match(stripped)
        if m:
            flush_msg()
            msg_id = m.group(1)
            msg_ts = m.group(2)
            msg_lines = []
        elif stripped.startswith("<!--"):
            continue  # skip attribution tags / comments
        else:
            msg_lines.append(stripped)

    flush_msg()

    # 2. Parse task transitions from bash_history
    from tasks.retro import bash_history_ts_to_utc
    for stamp, task_cmd in activations(bash_history.read_text(encoding="utf-8", errors="replace")):
        entries.append((bash_history_ts_to_utc(stamp), 1, f"--- {task_cmd} ---"))   # local → UTC (073)

    # 3. Sort by timestamp, then task transitions before messages (sort_key: 1 before 0)
    #    Actually: task transitions AFTER messages at same timestamp makes more sense
    #    But transitions should come BEFORE subsequent messages — sort_key 1 means
    #    transitions sort after messages at same second. That's fine: the transition
    #    happened between messages.
    entries.sort(key=lambda e: (e[0], e[1]))

    # 4. Output
    for _, _, display in entries:
        print(display)

def cmd_tag(cmd_args):
    """The `tasks tag` arm — body moved verbatim from cli.py (1.5.9 split)."""
    dry_run = "--dry-run" in cmd_args
    project_path = find_project_root()
    chat_log = resolve_agent_dir(project_path) / "chat_log.md"
    bash_history = resolve_agent_dir(project_path) / "bash_history"
    if not chat_log.exists():
        print(f"No {chat_log.relative_to(project_path).as_posix()} found.", file=sys.stderr)
        sys.exit(1)
    if not bash_history.exists():
        print(f"No {bash_history.relative_to(project_path).as_posix()} found.", file=sys.stderr)
        sys.exit(1)

    import re
    from bisect import bisect_right

    # 1. Build sorted task transition list from bash_history
    #    Each entry: (timestamp, active_task_or_None)
    work_re = re.compile(r'tasks work (\d+)')
    transitions = []  # [(timestamp, task_num_or_None)]
    from tasks.retro import bash_history_ts_to_utc
    # the shared reader (task 171): the toggle that stood here dropped the SECOND
    # `tasks work done` of a history, so its task never closed in the spans
    for stamp, task_cmd in activations(bash_history.read_text(encoding="utf-8", errors="replace")):
        ts = bash_history_ts_to_utc(stamp)   # local → UTC (task 073, C12)
        if "work done" in task_cmd:
            transitions.append((ts, None))
        else:
            wm = work_re.search(task_cmd)
            if wm:
                transitions.append((ts, wm.group(1).zfill(3)))
    transitions.sort(key=lambda t: t[0])
    trans_times = [t[0] for t in transitions]

    def active_task_at(ts):
        """Return task number active at timestamp ts, or None."""
        idx = bisect_right(trans_times, ts) - 1
        if idx < 0:
            # F2 (first-task attribution): messages before the first
            # activation are the project seed — the mandate that produced
            # the first task. Attribute them to the first task ever
            # activated instead of dropping them.
            return next((t for _, t in transitions if t is not None), None)
        return transitions[idx][1]

    # 2. Scan chat_log.md, find message headers with timestamps,
    #    insert tags at task transition points
    msg_header = re.compile(
        r'^(\*\*\[(M\d+)\]\*\* \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\])'
    )
    # Also detect existing tags to avoid double-tagging
    existing_tag = re.compile(r'^<!--\s*/?T\d+\s*-->$')

    _tag_source = chat_log.read_text(encoding="utf-8", errors="replace")
    lines = _tag_source.splitlines(keepends=True)
    output = []
    current_tag = None  # currently open tag (task number)
    tags_inserted = 0

    for line in lines:
        stripped = line.strip()
        # Skip existing attribution tags (we'll rewrite them)
        if existing_tag.match(stripped):
            continue

        m = msg_header.match(stripped)
        if m:
            msg_id = m.group(2)
            msg_ts = m.group(3)
            task = active_task_at(msg_ts)

            if task != current_tag:
                # Close previous tag if open
                if current_tag is not None:
                    output.append(f"<!-- /T{current_tag} -->\n")
                    output.append("\n")
                    tags_inserted += 1
                # Open new tag if task is active
                if task is not None:
                    output.append(f"<!-- T{task} -->\n")
                    output.append("\n")
                    tags_inserted += 1
                current_tag = task

        output.append(line)

    # Close final tag if still open
    if current_tag is not None:
        output.append(f"\n<!-- /T{current_tag} -->\n")
        tags_inserted += 1

    if dry_run:
        print(f"Would insert {tags_inserted} tags into chat_log.md")
        # Show first few transitions
        current_tag = None
        for line in output:
            stripped = line.strip()
            if existing_tag.match(stripped):
                print(f"  {stripped}")
    else:
        # Full rewrite of chat_log.md (tag insertion), NOT an append — atomic so
        # an interrupt can't truncate the whole conversation log to a fragment.
        #
        # Task 058 (plan panel P11): the chat-log HOOK appends to this file while
        # a session runs, so a rewrite composed from an earlier read would drop
        # every prompt that landed in between. Two defenses, because the hook's
        # own `flock` covers only its counter and not the append:
        #   1. rendezvous on the SAME lock file the hook uses
        #      (`<agent>/chat_log_counter.lock`), so the two protocols agree
        #      rather than ignore each other;
        #   2. a compare-and-swap on the CONTENT — if the log changed since the
        #      buffer these tags were computed from, write NOTHING and say so.
        from tasks.filelock import named_lock
        counter_lock = resolve_agent_dir(project_path) / "chat_log_counter.lock"
        with named_lock(counter_lock):
            if chat_log.read_text(encoding="utf-8", errors="replace") != _tag_source:
                print("chat_log.md changed while tags were being computed (the "
                      "chat-log hook appended a message) — nothing was written, "
                      "so no prompt was lost. Re-run `tasks tag`.",
                      file=sys.stderr)
                sys.exit(1)
            atomic_write(chat_log, "".join(output))
        print(f"Inserted {tags_inserted} tags into chat_log.md")

def _messages_since(chatlog: list, start: str) -> list:
    """The chat from `start` on (task 145, impl panel r2 re-run): a TIME window from
    the last retro's activation. Attribution was tried first — it dropped the retro
    session's own discussion, lost everything for tasks with no attribution, and pulled
    a carried task's older history in."""
    from tasks.retro import _normalize_ts
    start = _normalize_ts(start)
    return [m for m in chatlog if _normalize_ts(m.get("timestamp", "")) >= start]


def cmd_retro(cmd_args):
    """The `tasks retro` arm — body moved verbatim from cli.py (1.5.9 split)."""
    project_path = find_project_root()
    # Parse --since N flag. No --since: the tasks AFTER the last retro (task 145,
    # retro 134 (b): a bare retro read the whole history, while the close-time nudge
    # counts the tasks closed since the last retro); all of them when none ran yet.
    since = None
    i = 0
    while i < len(cmd_args):
        if cmd_args[i] == "--since" and i + 1 < len(cmd_args):
            try:
                since = int(cmd_args[i + 1])
            except ValueError:
                print(f"Error: --since requires a number", file=sys.stderr)
                sys.exit(1)
            i += 2
        else:
            i += 1

    from tasks.retro import (
        extract_tasks, extract_chatlog, extract_mindmap,
        build_task_windows,
    )

    window = ""
    carry: "dict[int, tuple[str, str]]" = {}   # task → (status, gates) the last retro recorded
    last_retro = None                   # set only on the default window
    if since is None:
        from tasks.core import count_tasks_since_retro, retro_carried_state
        _closed, last_retro = count_tasks_since_retro(project_path)
        since = (last_retro + 1) if last_retro is not None else 0
        carry = retro_carried_state(project_path, last_retro)
        window = (f"Window: tasks after retro T{last_retro:03d} (the last retro) — "
                  "`tasks retro --since N` reads from task N, `--since 0` everything."
                  if last_retro is not None else
                  "Window: all tasks (no retro has run yet) — `tasks retro --since N` reads from task N.")

    tasks_dir = resolve_agent_dir(project_path) / "tasks"
    chatlog_path = resolve_agent_dir(project_path) / "chat_log.md"
    bash_history_path = resolve_agent_dir(project_path) / "bash_history"
    mindmap_path = project_path / "MIND_MAP.md"

    # Extract data
    tasks = extract_tasks(tasks_dir, since=since)
    if carry:
        # still open at the last retro: theirs is this window (impl panel r2)
        carried = [t for t in extract_tasks(tasks_dir, since=0) if t["number"] in carry]
        # …but a window that holds ONLY such tasks, each as it was when the last
        # retro was made, would repeat that retro: with one unfinished task every
        # further bare `tasks retro` made one more (PLAN S11 item 2, task 165). "As
        # it was" is the record BYTE FOR BYTE — the digest that retro kept
        # (`retro-carried`; impl panel r2: a task whose text or WHICH gates are
        # checked changed has new material, whatever its counts say). A retro made
        # before the digests existed is compared by what its table holds: status
        # (cut at seven characters, as the table cuts it) and gate count (r1). Every
        # carried task must still be there (one that is gone is a change too). An
        # explicit `--since` never comes here.
        from tasks.core import retro_carried_digests
        _kept = retro_carried_digests(project_path, last_retro)

        def _unmoved(t):
            n = t["number"]
            if n in _kept:
                return t["digest"] == _kept[n]
            return (t["status"][:7].strip().lower(),
                    f"{t['checked_count']}/{t['gate_count']}") == carry[n]
        if (carried and not tasks and len(carried) == len(carry)
                and all(_unmoved(t) for t in carried)):
            waits = ", ".join("T{:03d} ({}, {} gates)".format(t["number"], *carry[t["number"]])
                              for t in carried)
            how = ("is byte for byte what it was when that retro was made"
                   if all(t["number"] in _kept for t in carried) else
                   "has the status and the gate count it recorded (that retro kept no digest "
                   "of the records, so their text is not compared)")
            print(f"Nothing to review since retro T{last_retro:03d} (the last retro): no task after "
                  f"it, and what it carried as unfinished {how} — {waits}. No retro made. "
                  f"`tasks retro --since {min(carry)}` makes one all the same, from the oldest of "
                  "them on (a higher N would leave them out of every later window).",
                  file=sys.stderr)
            sys.exit(1)
        tasks = sorted(carried + tasks, key=lambda t: t["number"])
        window += (" Also " + ", ".join(f"T{n:03d}" for n in sorted(carry))
                   + " — open at the last retro.")
    # this retro's own "made at" — taken BEFORE the chat is read, so a message that
    # lands while it is generated is in the next retro's window (post-D6 run 2)
    _stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    task_windows = build_task_windows(chatlog_path, bash_history_path)
    chatlog = extract_chatlog(chatlog_path, task_windows)
    mindmap = extract_mindmap(mindmap_path)

    if not tasks:
        print("No tasks found in window." + (f" {window}" if window else ""), file=sys.stderr)
        sys.exit(1)
    # The DEFAULT window narrows the chat too, by time: from the last retro's own
    # activation on (its discussion included). An explicit `--since N` keeps the whole
    # chat, as it always did. Unknown activation time: the whole chat, said so.
    if last_retro is not None and chatlog:
        from tasks.retro import retro_start_time
        _rf = sorted(tasks_dir.glob(f"{last_retro:03d}-*/task.md"))
        start = retro_start_time(_rf[0], last_retro, bash_history_path) if _rf else None
        if start:
            chatlog = _messages_since(chatlog, start)
            window += f" Chat: from {start} on (when retro T{last_retro:03d} was made)."
        else:
            window += (" The chat is not windowed: when retro "
                       f"T{last_retro:03d} was made is not recorded.")

    # Run structural analysis passes
    from tasks.retro import (
        analyze_intent_health, analyze_garbage,
        generate_retro_task,
    )
    health = analyze_intent_health(tasks)
    gc = analyze_garbage(tasks)

    # Generate the retro task.md — a cognitive program
    retro_content = generate_retro_task(
        tasks=tasks, chatlog=chatlog, mindmap=mindmap,
        health=health, gc=gc,
    )

    # Create as a new task
    from tasks.core import _next_task_number, _slugify
    tasks_dir_path = resolve_agent_dir(project_path) / "tasks"
    task_num = _next_task_number(tasks_dir_path)
    first = tasks[0]["number"]
    last = tasks[-1]["number"]
    slug = f"retro-{first:03d}-{last:03d}"
    folder_name = f"{task_num:03d}-{slug}"
    task_dir = tasks_dir_path / folder_name
    task_dir.mkdir(parents=True)
    task_file = task_dir / "task.md"
    # when this retro was made — the next retro's chat boundary (task 145)
    _head, _sep, _rest = retro_content.partition("\n")
    # …and a digest of every unfinished task of the window: the next bare retro refuses
    # only if those records have not changed at all (task 165). The digest is the one
    # taken with the read that built the table above — never a later read of the file.
    from tasks.core import retro_carried_line
    _carried_line = retro_carried_line(
        {_t["number"]: _t["digest"] for _t in tasks
         if not _t["status"].strip().lower().startswith("done")})
    retro_content = (f"{_head}\n<!-- retro-generated: {_stamp} -->"
                     + (f"\n{_carried_line}" if _carried_line else "") + f"{_sep}{_rest}")
    atomic_write(task_file, retro_content)

    print(f"Created: {task_file.relative_to(project_path)}")
    if window:
        print(window)
    print(f"Retro task T{task_num:03d} — {len(tasks)} tasks in window, "
          f"{len(chatlog)} chat messages, {len(mindmap)} mind map nodes")
    print(f"Next: tasks work {task_num}")

def cmd_log(cmd_args):
    """The `tasks log` arm — body moved verbatim from cli.py (1.5.9 split)."""
    # tasks log [N] [--width W]
    # Compact one-line-per-message view of chat_log.md (no gate cruft).
    # N: show only the last N messages (default: all).
    # --width: crop each message body to W chars (default 500).
    import re
    cmd_args = sys.argv[2:]
    last_n = None
    width = 500
    i = 0
    while i < len(cmd_args):
        a = cmd_args[i]
        if a == "--width" and i + 1 < len(cmd_args):
            width = max(10, int(cmd_args[i + 1])); i += 2
        elif a.isdigit():
            last_n = int(a); i += 1
        else:
            i += 1
    project_path = find_project_root()
    chat_log = resolve_agent_dir(project_path) / "chat_log.md"
    if not chat_log.exists():
        print(f"Error: {chat_log.relative_to(project_path).as_posix()} not found", file=sys.stderr)
        sys.exit(1)
    text = chat_log.read_text(encoding="utf-8", errors="replace")
    blocks = text.split("\n---\n")
    lines = []
    for block in blocks:
        # Entry header format grew a ` (provider/pid)` suffix with multi-provider
        # tagging (commit 0fca4b0), e.g. `**[M12]** [… UTC] `HOST` (claude/pid-9)`.
        # The suffix must be OPTIONAL (legacy entries lack it, and requiring it
        # made `tasks log` silently print nothing — bug report #5b) and CAPTURED
        # (its provider token is the real agent; the backticked field is now just
        # `HOST`). Prefer the suffix provider; fall back to the backticked name.
        m = re.match(
            r'\*\*(\[M\d+\])\*\* \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2} UTC\] '
            r'`(\w+)`(?:\s*\(([^)/]+)/[^)]*\))?\s*\n+(.*)',
            block.strip(), re.DOTALL
        )
        if m:
            mid, ts, role, provider, body = m.groups()
            agent = provider or role
            body = " ".join(body.split())
            if len(body) > width:
                body = body[:width - 1] + "…"
            lines.append(f"{mid} {ts} {agent:<6} {body}")
    if last_n is not None:
        lines = lines[-last_n:]
    for line in lines:
        print(line)
