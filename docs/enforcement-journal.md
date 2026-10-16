# Enforcement journal — record shapes

Playbook appends a best-effort, **log-only** journal of what its enforcement and
review machinery did, one JSON object per line, to the lane-resolved
`.agent/<lane>/journal/enforcement.jsonl` (root lane:
`.agent/journal/enforcement.jsonl`). It exists so an external reader — e.g.
**playbook-lens** (a separate repo) — can attribute enforcement decisions and
review spend after the fact.

**Hard contract (every writer):** a journal write failure NEVER changes or
breaks any decision or review. Each record is a single `O_APPEND` write of one
line, with no `fsync` and no locking; POSIX makes an append of at most
`PIPE_BUF` bytes atomic against concurrent appenders, so lines from separate
processes (parallel panel seats, multi-lane) do not interleave while a record
stays under that bound (≥512 bytes everywhere; 4096 on Linux). String fields are
byte-capped so real records stay well under 512 bytes. The journal is only ever
created inside an already-existing playbook-managed lane — `.agent` itself is
never minted as a side effect. Both writers skip a sink that is not the lane's own — a linked `journal/`
directory, a linked `enforcement.jsonl`, or one that is not a regular file (a FIFO would hang the writing hook):
`pb_journal.py` since task 042, the bash twin since task 192 (it hung the gate on a FIFO and appended through a
link before, measured). The bash twin checks and then writes, so a process that swaps the file for a FIFO
between the two can still hang it; the Python writer opens non-blocking and without following a link.

Writers live in `scripts/pb_journal.py` (`append`, `append_review`, `append_lifecycle`) and its bash
twin `scripts/gate-echo-lib.sh::journal_enforcement`. The hook journal — one line per run of a hook — is a file of
its own beside this one, `journal/hooks.jsonl` ([below](#the-hook-journal--journalhooksjsonl-task-222)). Guarantees:
`PB-ENFORCEMENT-JOURNAL` (enforcement decisions) and `PB-REVIEW-SPEND-JOURNAL`
(review spend).

## Common envelope

Every record carries these keys:

| key          | type   | meaning                                                        |
|--------------|--------|---------------------------------------------------------------|
| `ts`         | string | UTC timestamp, `YYYY-MM-DDTHH:MM:SSZ`                          |
| `session_id` | string | emitting session id (may be empty)                            |
| `hook`       | string | which surface emitted it (see below)                          |
| `decision`   | string | one of `allow` \| `block` \| `record` \| `session-walk`        |
| `reason`     | string | short fixed reason string                                     |

A reader MUST tolerate unknown future keys and unknown `hook`/`decision` values
rather than erroring — this is a log, and the repo's format is the source of
truth.

## Family 1 — enforcement decisions (`decision` = `allow` / `block` / `record`)

Emitted by the gate/guard/stop/batch hooks, the task-folder watch and the close path. `hook` is one
of `task-gate`, `command-guard`, `stop`, `gate-batch-check`, `task-dir-watch` (task 196) or `close`.
`task-dir-watch` writes a `record` with `reason="unknown task folder"` and the folder as `path` for each task
folder it flags after a call (also `"task registry unreadable"` and `"task registry removed during this call"`),
`block` with `reason="unknown task folder"` when it holds a flagged session's call, and `allow` with
`reason="unknown task folder: the way out"` for the removal or `tasks` call it lets through;
`task-gate` writes `allow` or `block` (its Bash task-dir refusal wrote
`reason="manual task dir creation"` from task 110 until task 197 removed it); `command-guard` writes
`block`, and `allow` for an acknowledged dangerous command with
`reason="ack-irreversible-task:<rule>"` or `"ack-operator-env:<rule>"` (task 110);
`stop`/`gate-batch-check` write `block`; the **close** path writes a `record` (`hook="close"`,
`reason="verify contract"`) logging the verify bar a close ran. Optional fields:
`tool`, `path`, `command` (the command head, capped). See
`PB-ENFORCEMENT-JOURNAL`.

## Family 2 — review spend (`hook` = `review`, `decision` = `record`)

Emitted for **every judge invocation** — by the review runner (`tasks/review.py`)
for each panel seat, the single judge and the tail-cert judge, and by `tasks intent`
(`tasks/intent.py`) for each blind extraction (task 120) — via
`pb_journal.append_review`, on its completion or timeout. A tail-certification judge
that could not be started — its adapter could not be built, or its program is not
installed, so nothing was launched — is no invocation and leaves no record (task 178). The one exception is a
**tamper hard-stop**: if the working tree changed while the judges ran (a judge or
any other writer — the guard cannot tell which), the review emits its tamper banner and stops, recording **no** spend — the journal write
must never precede that banner (a hostile-tree hang inside it could suppress the
banner, the "operation-before-banner" class the tamper guard is built around),
so every path emits only past the tamper check. `reason` is `"review spend"`.
Additional fields:

| key           | type          | meaning                                                            |
|---------------|---------------|--------------------------------------------------------------------|
| `kind`        | string        | `panel` \| `single` \| `tail-cert` \| `intent` (one per `tasks intent` extraction; `round` 0; left out of `tasks dashboard`'s per-seat stats and drift advice, which measure reviews) |
| `seat`        | string        | the judge spec as `model:effort` (e.g. `claude:opus:high`, `codex:gpt-5.6-terra:medium`; an agy id carries its effort: `agy:gemini-3.8-flash-high`) |
| `task`        | string        | task number (`"042"`) or `"-"` for a taskless / `--prompt` review  |
| `round`       | int           | review iteration (see the round note below); `0` = unknown         |
| `duration_ms` | int, optional | wall time of the judge subprocess in milliseconds (absent if unknown) |
| `status`      | string        | `ok` \| `fail` \| `timeout` \| `dnf` (did-not-finish / spawn error)|
| `usage`       | object        | token usage — see the usage note below                            |
| `error`       | string, optional | why a non-`ok` invocation failed (task 096) — see below       |

`seat` carries reasoning effort so spend can be attributed by `model:effort`
(the owner's ask). codex and grok already encode effort in the model variant
(`gpt-5.6-terra:medium`, `grok-4.6:high`); the claude judge runs at a fixed
`--effort high` not present in its variant, so `:high` is appended for claude
seats. A codex/grok seat configured WITHOUT an explicit effort in its variant is
recorded without one (the ambient CLI default is not resolved) — the record
reflects what was specified, never a guessed effort.

`status` is `dnf` (did-not-finish) for any judge output that begins with
`(error: …)` — this covers BOTH a pre-spawn resolution error (a missing CLI, an
adapter raise) and a judge that spawned and then crashed. A `dnf` record
therefore does NOT imply tokens were spent; it means "no usable review," with
`usage` unknown.

All numeric fields (`round`, `duration_ms`, and the usage token counts) are
magnitude-capped (to 15 digits) and all string fields are byte-capped with
control characters stripped, so **real** records stay well under the PIPE_BUF
atomic-write bound. This is a best-effort bound, not an absolute one: a
pathological caller could still exceed it (e.g. a field stuffed with `"`/`\`,
which JSON escapes to two bytes each) — an accepted bound, never a correctness
risk, because the write never affects any decision. The journal file is opened
non-blocking and without following symlinks, its `journal/` parent is lstat-
checked, and the fd is confirmed to be a regular file before any write, so a
hostile-tree swap of the journal path (or its parent dir) to a FIFO/symlink is
skipped rather than hanging or escaping the lane.

On a **panel**, one record lands per seat, all sharing the same `round`.

Example:

```json
{"ts":"2026-09-01T16:41:19Z","session_id":"pid-123","hook":"review","decision":"record","reason":"review spend","kind":"panel","seat":"claude:opus:high","task":"042","round":3,"duration_ms":48210,"status":"ok","usage":{"status":"unknown"}}
```

### The `error` field

Present only on a `fail`, `dnf` or `timeout` record, never on `ok`. It is the
reason the invocation failed, in one line: `timed out after <limit>` for a timeout;
the model-availability verdict (`MODEL_UNAVAILABLE` / `CLI_UPGRADE_REQUIRED`) when
the failure matches one; otherwise `exit N: <first line of the failure output>`, or
the output's first line (a spawn error such as `(error: claude CLI not found)`).
For an `exit N`, the first line of the stderr tail wins over the stdout tail (a
progress line is not the reason); a timeout without a limit keeps the judge's
own text (`(error: tail-cert judge timed out)`).
It comes from a CLI's stderr, so it is sanitized: control characters are dropped,
`"` becomes `'` and `\` becomes `/`, and token-shaped runs (an `sk-`/`xai-`/`ghp_`
style key, or a 32+ run of letters and digits with no `_` or `/` — hex or
base64-shaped; paths and snake_case test ids survive) become `<redacted>`. It is then cut to
the bytes the rest of the record leaves under the 512-byte atomic-append floor
(its one-byte newline included), and
left out when fewer than 8 bytes remain — the field can shorten, never lengthen a
line past the floor.

### The `usage` field — honest bounds

`usage` is always present and always exactly one of two shapes: the explicit
marker `{"status":"unknown"}`, or `{"status":"known","in":<int>,"out":<int>}`.
`append_review` **normalizes** it to that fixed schema — an arbitrary caller dict
is never copied verbatim (that would blow the PIPE_BUF bound), and a non-int
token count degrades to `unknown`. **Numbers are never fabricated.**

Where the numbers come from (task 056): the codex and grok judge paths ask the
CLI for **structured output** — `codex exec --json` (JSONL; the LAST
`turn.completed` event carries `usage.input_tokens` / `usage.output_tokens`) and
`grok --output-format json` (one object with the review under `text` and
`usage.input_tokens` / `usage.output_tokens`). The adapter parses the usage from
that raw stdout and returns the review PROSE as a `str` subclass that *carries* the
usage (`provider.usage.JudgeOutput`), so judge.md / judge-*.log hold prose while the
spend record holds the CLI's own numbers — copied, never derived. `in` is the
vendor's reported `input_tokens` **as reported** (codex's includes cached input;
grok's is its billed input) — recorded per vendor, not normalized across them;
cached/reasoning/cost fields are not recorded (fixed schema). A codex/grok seat is
`known` on success and also when the CLI emitted a usage frame before exiting
nonzero (tokens were spent either way), and on a timeout when the CLI had already
emitted a complete usage frame before the kill (the partial stdout is parsed strictly);
it is `unknown` on a timeout with no complete frame, on spawn errors,
and whenever the stdout is not the expected envelope — a non-int, bool or negative
count is `unknown`, never clamped into a number.

The **claude** judge still runs in plain-text mode, so its seats are `unknown` by
design. **Usage comes ONLY from the adapter-carried value** (`JudgeOutput.usage`,
attached by an adapter that itself requested structured output). Judge TEXT is
never parsed for usage: a review is prose, and prose — even a seat whose entire
output happens to be a JSON usage envelope — can never poison the field. Enabling
structured output for claude would be a deliberate adapter change that attaches
the carrier (the shared parser already knows claude's `usage` shape), not an
accident. Failure shapes fail closed: JSON-looking stdout that is not the
recognized envelope, a codex event stream with a stray line or without a terminal
`turn.completed`, a codex `error`/`turn.failed` event, or a grok `stopReason`
other than `end_turn` all produce a `(FAILED — …)` result (partial text kept as a
diagnostic, usage still recorded when a frame exists) — never a clean seat.
A reader should still treat `unknown` as an ordinary value (claude seats,
failures), never assume tokens are present.

The **agy** judge (an experimental seat, task 111; agy 1.2.17) also reports usage:
its judge path asks for `--output-format stream-json`, and the terminal `result`
event carries `usage.input_tokens` / `usage.output_tokens` for the WHOLE turn
(every model call of a tool-using review, summed by agy). `in` is recorded as
reported — it excludes agy's `cache_read_tokens`; `out` includes its
`thinking_tokens`; neither extra field is recorded (fixed schema). Two agy
specifics a reader should know: an **all-zero** usage object is recorded as
`unknown`, never as a measured zero (agy prints zeros when its own
`--print-timeout` expires mid-turn, although the turn ran, and for a call that
failed before reaching the model); and an agy seat's record names the seat
`agy:<model id>` on both the panel and the single-judge path (the id carries
the effort, e.g. `agy:gemini-3.8-flash-high`).

One disclosed trade-off: grok's json mode emits its single object at the END, so
a grok seat killed at the hard timeout salvages no partial prose (its status is
`timeout` either way); codex's JSONL partial stdout is salvaged as the last
completed message text, not protocol frames.

### The `round` field — best-effort

`round` counts existing panel rounds + 1, summed across BOTH `judge.md` and its
overflow sibling `judge-archive.md` (judge.md retains only the newest 5 rounds
and archives the rest, so counting judge.md alone would cap the round at 6). For
a **panel** it is the iteration this panel becomes; for **single** and
**tail-cert** it is an approximate spend-correlation hint (those do not add a
`judge.md` round), not an exact iteration index. `0` means it could not be
determined.

## Family 3 — session walks (`decision` = `session-walk`, task 192)

Emitted by `state-echo` and `task-gate` (bash, `gate-echo-lib.sh::journal_session_walk`) when the session id is
UNRESOLVED ("playbook: no session identity"), and on every call when the environment has `PLAYBOOK_WALK_TRACE=1`
(the live gauntlet sets it). `session_id` is the id the hook resolved (empty when unresolved); `tool` the call's
tool. `reason` is `read: ppid=<P> env=<id or unset> walk: <pid> <name> <state> > … | stop: <where>` — what the
resolver's OWN walk read, hop by hop (a claude process shows its argument count `argc=<n>`, never its words; names
are reduced to `[A-Za-z0-9._+-]`, `?` for the rest), `<where>` one of `<pid> gone`, `<pid> unreadable`,
`<pid> unreadable argv`, `<pid> daemon`, `<pid> own parent`, `top <pid>`, `20 hops`, `no ps`; for an
honoured `PLAYBOOK_SESSION_ID` the read says so instead of a walk. An unresolved record then adds
`| again=<id or unresolved>` (a walk right after) and, on the record that reaches a day's cap, `| the day's cap of
<N> records of this kind is reached: no more today`. Unresolved records are capped per day: 20 for a walk that
stopped at the daemon, 100 for the rest, counted in `journal/session-walk.count` (`<day> <others> <daemon>`; no
lock — the caps bound growth, not an exact count); trace-mode records are not capped.

## Family 4 — task transitions (`hook` = `tasks`, `decision` = `record`, task 221)

Emitted by the `tasks` CLI (`pb_journal.append_lifecycle`) when a transition HAPPENED: `tasks work N` activated a
task (`reason` = `work`, written after the session pointer is published), `tasks new` made one (`new`), `tasks work
done` closed one (`done`, after the close). A refused command writes none. Additional fields: `task` (the task
number, as the CLI names it — `"007"` or `"7"`) and, on `new`, `detail` (`"<type> <name>"`).

These records are what `tasks timeline`, `tasks tagger`, `tasks tag`, retro and `tasks intent` build their task
windows from (`tasks/retro.py::lifecycle_records`). Until task 221 those readers parsed `tasks work`/`tasks new`
out of `.agent/bash_history` — which, written by the PostToolUse hook since then, holds each command whole: a
skipped `&&` branch, a heredoc or a grep that names `tasks work 5` ran nothing, so a command line is not an event.
A project's activations from before this version are only in its shell log: those tasks' windows start at their
first gate entry.

Example:

```json
{"ts":"2026-10-16T12:18:43Z","session_id":"pid-123","hook":"tasks","decision":"record","reason":"new","task":"001","detail":"quick first"}
```

## The hook journal — `journal/hooks.jsonl` (task 222)

The owner, 2026-10-16: "e important ca playbook sa isi salveze infomratiile legat de ce face, ca sa il putem analiza
si imbunatati" — and "Istoric curat + jurnal de hook-uri". Every run of a playbook hook appends ONE line to
`.agent/<lane>/journal/hooks.jsonl` (root lane: `.agent/journal/hooks.jsonl`): Claude's and grok's eight bash hooks,
the project's `monitor-nudge.sh`, codex's three python hooks and `command_guard.py`. A file of its own, not a fifth
family of `enforcement.jsonl`: that file keeps the decisions and the CLI's task transitions the task windows read, and
this one grows tens of times faster (measured on this repository, 2026-10-07 to 2026-10-16: 8,000-13,000 hook runs a
day, implied by the transcripts' tool calls, against 80-500 enforcement records a day). The two files share no key
beyond the session ids and the time.

Every line has the same thirteen keys, in this order:

| key | meaning |
|---|---|
| `ts` | UTC, `YYYY-MM-DDTHH:MM:SSZ`, when the hook EXITED |
| `hook` | `session-start`, `chat-log`, `task-gate`, `command-guard`, `task-dir-watch`, `state-echo`, `stop`, `session-end`, `monitor-nudge`, `codex-apply-patch`, `codex-stop`, `codex-user-prompt` |
| `event` | the host's event (`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SessionStart`, `SessionEnd`) |
| `tool` | the tool's name (`Bash`, `Edit`, an MCP tool's), "" for a prompt, a stop or a session |
| `tool_use_id` | the host's id of the tool call — the transcript's (`toolu_…`), "" where there is none |
| `session_id` | playbook's session id (`pid-N`, as `sessions/<id>` and `enforcement.jsonl` name it), "" where the hook resolved none |
| `host_session_id` | the host's own session id, from the payload |
| `agent_id` | a subagent's id when a subagent made the call, else "" |
| `decision` | `allow` (a PreToolUse hook let the call through, or the stop hook released the turn), `block` (exit 2: the call or the stop held), `context` (the hook showed the agent something), `ok` (it ran and showed nothing), `skip` (it did nothing by design: no session, python below 3.10), `error` (it failed — whatever its exit status says) |
| `detail` | the hook's short why, from a fixed vocabulary: `echo full`, `echo reminder`, `quiet`, `failed call counted`, `released: no active task`, `held: 2 unchecked gate(s) in task 007`, `active task 007`, `no active task`, `gate batch refused`, `git-push-force`, `session id injected`, `logged M042`, `nudge delivered`, … |
| `ms` | how long the hook ran, whole milliseconds from its FIRST line to its exit; `null` when bash has no `EPOCHREALTIME` |
| `exit` | the hook's exit status |
| `error` | on a failure, what failed: the hook's own name for it, its own failing source line (`line 214: cat "$f"`, as written in the hook — never a command of the agent's), or a python hook's exception TYPE (`RuntimeError` — its message, which can carry a path, stays on stderr) |

Example (a live session, task 222):

```json
{"ts":"2026-10-16T16:00:17Z","hook":"command-guard","event":"PreToolUse","tool":"Bash","tool_use_id":"toolu_01QQ4HUXVqTcKHE1zLD87eio","session_id":"pid-2025865","host_session_id":"f156c929-b7b5-4e71-bf48-878ac7c78aeb","agent_id":"","decision":"block","detail":"git-push-force","ms":64,"exit":2,"error":""}
```

**What it never holds:** no command, no path, no prompt, no file content — only the hooks' own words and ids. It is
not a conversation record, so a judge's read-only sandbox does not hide it.

**How it is written.** The bash hooks set `_PB_HJ_T0=$EPOCHREALTIME` on their first line and call `pb_hook_begin`
once the library is sourced; their own exit actions (the shell log's line, the stop hook's turn-end mark) go through
`pb_on_exit`. One EXIT trap runs those actions, each seeing the hook's own exit status as `$?`, then writes the line,
and ends with that same status: a journal failure never changes a decision. The python hooks use
`pb_journal.HookRun`/`run_hook`; `command-guard-hook` hands its start time to `command_guard.py` before its `exec`, and
the python side writes the guard's line, with the rule that decided. The payload's ids come from the hook-payload
normalizer's frame (v4) where the hook reads it (the task gate, the state echo), else by pattern: `tool_use_id` is
the last match in the payload's last 4 KiB (Claude's comes after the tool's input and response), the event, the
host session and the agent the first match in the payload's HEAD — before its first tool input, within 4 KiB (they
come before any tool input in every payload captured; the Agent tool's response carries a nested `agentId`, which must
not stand in for the caller's).

Each line is one append of at most 1,024 bytes (Linux's `PIPE_BUF` is 4,096), so hooks side by side tear none. Each
append holds a SHARED lock on the journal directory (`flock`; renaming and compressing hold it exclusively), so no
line is in flight while a closed file is compressed, whatever the clock — the owner, 2026-10-16 22:21: "Lacăt la
fiecare rând" (a forward clock jump made the time rule below insufficient — the post-panel judge). The lock is taken
before the end time, so a wait counts in `ms`. No line is ever appended without it: past a 1 s wait the line goes to a
spool file of its own (`hooks.jsonl.spool-…`, written whole under another name, then renamed in), which the next run
that holds the lock exclusively moves into the journal — the owner, 2026-10-17 00:13: "Bilet în cutie". A writer's
temporary is never removed for its age: one whose writer (the pid in its name) is gone is taken the same way — its
one write is the whole line or nothing — and a living writer's is left (the post-panel judge's run 4: a clock step
made a live one look old). The journal directory is made before the lock is taken, so a project's first line holds it
too. A host without `flock` appends directly (and never rotates). Ids
keep only id characters; `detail` and `error` are escaped and cut on a whole character and a whole escape. Nothing is
written outside a playbook project (no `.agent/tasks`, no lane with `tasks/`), in the fresh-clone shape (lanes, no
marker), with an invalid `.agent/current_user`,
into a lane that is a link or not writable, through a linked `journal/`, or into a `hooks.jsonl` that is a link or not
a regular file. The bash writers check and then append, so a process that swaps the file for a FIFO between the two
can still hang one hook run; the python writer opens non-blocking and without following a link.

**Rotation (the owner, 2026-10-16 18:04: "Comprimate, toate").** About one run in 32 (`PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK`)
looks at the journal directory, under a lock on it taken without waiting (a busy lock: nothing on that run). Past 20
MB, after a second look, the file is renamed to `hooks.jsonl.archived-<YYYYmmdd-HHMMSS>-<epoch>-<pid>`: its name
records the rename time, written by the rename itself (a rename keeps the old mtime, so an idle 20 MB file was
compressed by the run that closed it — impl panel round 2 — and a rotator killed before resetting the mtime left the
same trap — the post-panel judge). A closed file is compressed to `….gz` beside it only once it is idle — on a later
such run, 10 minutes after the rename its name records AND after its last write; a closed file whose name records no
rename time is never compressed
(measured: 20 MB → 1.9 MB in 0.18 s) — because a hook that opened the file just before the rename can still append to
it, and a line written after `gzip` had read it would have gone with the uncompressed copy (impl panel round 1; the
hosts stop a hook after at most a minute). The uncompressed copy is removed only once the compressed one is in place,
and every archive is kept — no hook deletes a journal file. Without `gzip`, on a failure, or if the hook is killed
meanwhile, the closed file stays uncompressed. A line written while another hook renames the file lands in the
closed file or the new one. That work runs before the duration is taken, so a run that rotated says so in its `ms`.

**Not recorded:** a hook the host kills at its time limit (it runs no exit trap); the monitor's own session
(`PLAYBOOK_ROLE=monitor`: its hooks exit before anything); a review's session (`PLAYBOOK_SESSION_ID` `judge` or
`tail-cert`: no writer writes a line — a review must not write the project); a nested run (`PLAYBOOK_HOOK_NESTED=1`:
codex's Stop runs the stop hook as a subprocess, and codex-stop's own line says the run); a hook that finds no
playbook project. **Bounds:** codex runs `command_guard.py` directly, so its duration starts when python has started
and loaded the guard; every other hook's duration starts at its first line — bash's own start and a
`BASH_ENV` file are not in it — and includes the shell log's lock wait (up to 2 s); where a `BASH_ENV` file sets a
DEBUG trap, `error` names that trap instead of the failing line; task-dir-watch's `tool_use_id` is read by pattern,
so a host whose payload put a nested object carrying the same key AFTER the id could mislabel it.
