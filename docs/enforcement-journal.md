# Enforcement journal — record shapes

Playbook appends a best-effort, **log-only** journal of what its enforcement and
review machinery did, one JSON object per line, to the lane-resolved
`.agent/<lane>/journal/enforcement.jsonl` (root lane:
`.agent/journal/enforcement.jsonl`). It exists so an external reader — e.g.
**playbook-lens** (a separate repo) — can attribute enforcement decisions and
review spend after the fact.

**Hard contract (every writer):** a journal write failure NEVER changes or
breaks any decision or review. Each record is a single `O_APPEND` write of one
line, with no `fsync`, holding `journal/enforcement.lock` shared since task 257 (the rotation's lock —
[below](#rotation--archives-carried-positions-the-lock-task-257)); POSIX makes an append of at most
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

Writers live in `scripts/pb_journal.py` (`append`, `append_review`, `append_lifecycle`, `append_verify`) and its bash
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

A record that waited for the journal's lock in vain was written to a spool file and carries one more key, `spool` —
the spool file's name and the line's index in it (`<name>.<i>`), its identity when the spool is merged into the
journal (task 257).

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
`reason="verify contract"`) logging the verify bar a close ran, and since task 235 each of its records names its
`task`. Optional fields:
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
marker `{"status":"unknown"}`, or `{"status":"known","in":<int>,"out":<int>}` — the
second with `cache_read` and `cache_write` beside them for a claude seat (task 232),
each written only when it is a non-negative int.
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

The **claude** judge asks for `claude -p --output-format json` since task 232 (PHASE-B
item 6; until then it ran in plain text and all its seats were `unknown` — 747 of 747
records in this workspace). The judge call alone asks for it, on the panel seat and on
the single-judge path (tail certification and the post-panel judge included); a
subagent's call stays plain text. claude prints ONE object, `type: result`, at the end:
the review is `result`; the counts are copied from `modelUsage` when it names exactly one
model (a budget stop zeroes the top-level `usage` but keeps them there), else from the
top-level `usage` — both cumulative over the run's turns, measured; `usage.iterations`
is the last call only and is never read. `in` is claude's `input_tokens` **as reported**:
it excludes the cache, whose two counts are `cache_read` (claude's
`cacheReadInputTokens`) and `cache_write` (`cacheCreationInputTokens`) — not summed into
`in`, since a sum would be derived. All four zero (nothing ran, as for a model that does
not exist) is `unknown`. A failure — `is_error`, or a `subtype` other than `success` —
reads exactly as plain mode's did: `(FAILED — exit N)` with claude's own words (the
object's `errors`, else its `result`, else the raw object) as the `[stdout tail]`, so the
seat-outage rule (a claude account limit is read when its line is the whole stdout tail)
and the model-availability anchors see what they saw before; an error on exit 0 says
`exit 0, claude reported an error`. A claude judge killed at its timeout has printed
nothing yet (the object comes at the end), as in plain mode. Fixtures: verbatim
captures of claude 2.1.302, `tests/fixtures/claude-2.1.302/`.
**Usage comes ONLY from the adapter-carried value** (`JudgeOutput.usage`,
attached by an adapter that itself requested structured output). Judge TEXT is
never parsed for usage: a review is prose, and prose — even a seat whose entire
output happens to be a JSON usage envelope — can never poison the field. Failure shapes fail closed: JSON-looking stdout that is not the
recognized envelope, a codex event stream with a stray line or without a terminal
`turn.completed`, a codex `error`/`turn.failed` event, or a grok `stopReason`
other than `end_turn` all produce a `(FAILED — …)` result (partial text kept as a
diagnostic, usage still recorded when a frame exists) — never a clean seat.
A reader should still treat `unknown` as an ordinary value (failures, a seat whose
CLI printed no counts, a claude record from before task 232), never assume tokens are present.

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

## Family 5 — verify runs (`hook` = `verify`, `decision` = `record`, task 232)

One per command of the verify contract, at the close and in `tasks verify` alike (`tasks/verify_memory.py`
`run_contract`, via `pb_journal.append_verify`), right after the command ran or was reused; `reason` is
`"verify run"`. Fields: `task` (the task number, or `"-"` when `tasks verify` resolved none), `command` (its first
line, capped at 120 bytes), `rc` (its exit, signed — a command killed by a signal exits negative), `how` (`ran` or
`reused`) and `seconds` — the monotonic time the run took (it stops while the machine sleeps), `0` for a reused
command: that time was spent once, by the run it reuses, so a sum over the records counts it once. A run that ended
"not remembered" (the code moved while it ran, no code key) is `ran`. Like every family, a write failure never
changes the verify's result.

```json
{"ts":"2026-10-17T17:02:11Z","session_id":"pid-1952820","hook":"verify","decision":"record","reason":"verify run","task":"232","command":"cd playbook-plugin && python3 scripts/verify","rc":0,"how":"ran","seconds":81.337}
```

## Family 6 — the main agent's tokens (`hook` = `agent-usage`, `decision` = `record`, task 244)

The owner, 2026-10-18: "Da, după 243 (Recomandat)" — a closure's numbers carry the main agent's own tokens beside the
judge seats'. Only the host's session transcript holds them (one `type: "assistant"` entry per content block of an API
call, each repeating the call's `message.usage`), and the host deletes transcripts after `cleanupPeriodDays` (30 by
default), so they are recorded as the work goes: `scripts/agent-usage-hook` at each turn's end (Stop), `session-end-hook`
at the session's end — first, before it removes the session dir (task 246), for the turn after the last Stop — and the
CLI at its own transitions — `tasks work N` and `tasks freehand` under the task they leave (`-` for none), the close
under the closing task, before its `done` record — so a recorded span never crosses a transition. Each record covers
one span of one transcript file for one model: `task`, `file` (16 hex of the file's absolute path's sha1), `ino` and
`head` (8 hex of the sha1 of the file's first min(4096, `to`) bytes — a transcript only grows at its end, so this tells
the same file from one put in its place even under a reused inode number), `from` / `to` (byte offsets) and `last_id`
(the last call counted), then `model`, `side` (1 for a subagent's file under
`<session>/subagents/` or a sidechain entry), `calls` (one per `message.id`, however many entries carry it), `in`,
`out`, `cache_read`, `cache_write`, `thinking` (a part of `out`, as the host counts it) and `unreadable` (entries whose
usage could not be read — never counted as zero). The record IS the read position: the next read of that file starts at
its newest record's `to`, under the lane's lock (`journal/agent-usage.lock`; without it nothing is written), so two
readers never count one span and a read stopped before its append is read again; a span's records (one per model and
side) go in one append, so the span's `to` never lands without all of them. A file with no record is read from 0 up to
8 MiB; a larger one (a session begun before the recorder was installed) gets a seed — `status: "unknown"`, `why`,
`from` = `to` = the end of its last complete line (task 253: not inside a half-written one) — so no history is charged to one task; so does a file whose last record the backward scan of
the journal (32 MiB at most) did not reach (since task 257 the live file stays near 20 MB, and its rotation carries
every position newer than the horizon into the next one — [Rotation](#rotation--archives-carried-positions-the-lock-task-257)). A broken assistant line and an entry with no `message.id` count as
`unreadable`, never as a call (one unreadable per call, however many entries carry it). `ino` is text (an inode number can pass the integer cap). The task is read inside the lock; a session the hook cannot resolve records nothing (its span stays for a reader that knows the session). A file's first read that finds no call writes one zero-call record — a read position, so later scans find it. A close or `tasks work` that cannot take the lock writes an `unknown` marker for the task it leaves and says so: never a silent gap. The session's end records too (task 246: `session-end-hook` runs the recorder before it can remove the session dir, so a turn interrupted with no Stop — Esc, or a session ended mid-turn — is still read; a busy lock there leaves an `unknown` marker when something is unread — at every end reason, a `/clear` included, since the SessionStart that follows points the session at the new transcript and no later reader names the old one; a transcript replaced in place (another inode or first 4 KiB), or whose last complete line ends elsewhere than its newest record's `to`, counts as unread, by the recorder's own identity check and restart rule (tasks 247, 252: the recorder stops at the last newline); a half-written last line counts as unread unless it names a user entry at its top level (before its `"message":` key — a tool call's input can hold the same type, a top-level value can read "message") — it may be a call the host is still writing, or was killed writing, which no reader will count (task 252); a file with no record whose only line is such a user entry does not; the marker errs toward `unknown`, never toward a hidden gap: it is decided without the lock, so a holder recording the same span at that moment both counts and marks it; a journal scan that did not reach a transcript's record counts it as unread — and the hook's journal line says what it recorded, or that the journal refused a span). Still not counted (bounds): a brand-new file in a journal past the scan bound gets a seed; a recorder that its 3 s limit kills after taking the lock leaves only the hook's journal line; a `claude` started from another session's shell is counted under the outer session's task (as at Stop); a transcript resumed later (`claude --resume`) after a busy-lock end is read on from its last record, so that end's `unknown` marker overstates the loss (task 247). The host gives SessionEnd hooks the largest timeout they declare (5 s here; 1.5 s only when none declares one — measured in Claude Code's own code, task 246). No conversation text is written: only the model's name. Bounds: a span after the
close's own read (the rest of that turn) is not the closure's; a transcript never read again (a crash with no
SessionEnd, never continued) keeps its unread tail; a judge's, tail-cert's or the monitor's session records nothing, and nor does a fresh multi-user clone (per-user lanes, no `.agent/current_user`: the lane is unresolvable, so nothing is written at the root — task 256)
(task 232 counts the judges' usage). A real record — task 244's timing run, on a copy of a 22 MB transcript tail:

```json
{"ts":"2026-10-18T22:15:53Z","session_id":"s1","hook":"agent-usage","decision":"record","reason":"agent usage","task":"5","file":"34ae6986f0647c4f","ino":"26667247","from":19862178,"to":21876806,"head":"6f5953ed","last_id":"msg_011CgAPA3MaFGF5U4a31rEkR","model":"claude-opus-5-5","side":0,"calls":13,"in":26,"out":11836,"cache_read":7780805,"cache_write":15447,"thinking":2280}
```

## Rotation — archives, carried positions, the lock (task 257)

The owner, 2026-10-19: "Da, în Faza B (Recomandat)" — `enforcement.jsonl` rotates like `hooks.jsonl`, so a project
used for years keeps the close's read, the agent recorder's backward scan and the bootstrap's panel health bounded.

**Archives.** Past 20 MB (20,971,520 bytes) the live file is renamed to
`enforcement.jsonl.archived-<YYYYmmdd-HHMMSS>-<epoch>-<seq>` — the rename's UTC time and epoch second, then a
sequence one past the highest archive's, taken under the exclusive hold: archives are read in that order, never in the
clock's (a clock set back would name a later archive earlier). Beside it, `<archive>.tasks` —
`{"tasks": [...], "size": N}`, the task numbers its lines name (`"task":"<digits>"`, carried lines aside) and the
archive's size — lets a reader of one task's history skip the archives that hold none of it; an archive no longer that
size (a writer of an older version, holding its descriptor across the rename, appended to it) is read whole. An archive
idle for 10 minutes (by the rename time its name records and by its mtime) is compressed to `<archive>.gz`, one a pass,
the compressed copy made durable (fsync) and checked against the archive's size before the archive goes; every archive
is kept (the owner's "Comprimate, toate").

**Who rotates.** Only python: `pb_journal.rotate_enforcement`, called by the agent recorder inside its
`journal/agent-usage.lock` (each Stop, the session's end, the CLI's transitions) and by the CLI when there is no
transcript to read. A pass with nothing to do costs ~150 µs (measured); rotating a 20 MB file 0.36 s, compressing it
0.22 s (this repo's laptop, a Ryzen 7 5800H). The bash writer never rotates.

**The new live file** starts with one horizon line and one carry line per transcript the recorder read (Family 6's
read positions), so its next read of every transcript is the same as before the rotation:

```json
{"ts":"2026-10-19T12:21:08Z","session_id":"","hook":"agent-usage","decision":"record","reason":"read positions horizon","horizon":"2026-09-19T12:21:08Z"}
{"ts":"2026-10-19T12:21:08Z","session_id":"pid-1","hook":"agent-usage","decision":"record","reason":"read position carried","task":"7","file":"34ae6986f0647c4f","ino":"26667247","to":21876806,"head":"6f5953ed","last_id":"msg_011CgAPA3MaFGF5U4a31rEkR","from":21876806,"of":"2026-10-18T22:15:53Z"}
```

A carry keeps the position's `file`, `ino`, `to`, `head`, `last_id` and `task`, with `from` = `to` and `of` = the time
of the record it carries. Positions older than the horizon (30 days before the rotation; a horizon never moves back)
are not carried: a transcript the recorder then finds no position for, whose first top-level `timestamp` is earlier
than the horizon plus a day, gets a seed (`why` names the horizon) — never a read from its start, which would count it
twice. A transcript whose first stamp is unknown is read from its start as before (a new session's first turns count).
Both are `record`s told by their `reason` (`read positions horizon`, `read position carried`) — the journal's
decisions stay the four above — and neither is an event: every reader of events skips them.

**The lock.** Every append holds `journal/enforcement.lock` shared (python `_write_record`, bash
`journal_enforcement`); the rotation's swap, its spool merge and a compression's swap hold it exclusively, for
milliseconds, and only inside `rotate_enforcement`, under the recorder's lock — so a read position, which is written
under that lock too, never meets an exclusive hold. The CLI's records (transitions, verify runs, review spend, close)
wait up to 5 s for the shared lock, a hook's up to 1 s; past that the record goes to a spool,
`enforcement.jsonl.spool-<stamp>-<pid>-<rand>` (written as `…spooling-…`), each line carrying its `spool` id —
never an unlocked append. A host with no `flock` command spools every bash record. The next rotation pass merges the
spools into the live file, once each (a spool whose ids are all there already is only removed), and takes a dead
writer's complete `…spooling-…` file the same way. A lock file that is a link or not
a regular file writes nothing. Measured cost: a `task-gate-hook` run that writes a record, median +2.3 ms (300
interleaved runs; quartiles +0.7 / +1.4 ms) — about the hook journal's own lock.

**Reading the whole journal** (the plugin's readers: `numbers.enforcement_lines`; an outside reader should do the
same), holding `enforcement.lock` shared while listing and reading (with no lock file yet, the journal is read
unlocked, and read again, locked, if the lock appears meanwhile — the first rotation makes it before its rename; a
reader never makes it, so a dry run writes nothing):

1. the archives by their sequence — `X`, not `X.gz`, when both exist (a compression killed before its last step), and
   never an archive name on the live file's inode (a rotation killed between its link and its rename);
2. the live file;
3. the spools;
4. one line per `spool` id, a spooled line placed by its `ts` after the ordinary lines of its second; carry and
   horizon lines skipped.

A chosen archive that cannot be read whole (a `.gz` cut short, a file gone) makes the read incomplete, never empty:
`numbers.JournalIncomplete` — the close then writes no numbers and says `numbers: not written (JournalIncomplete)`, the
dashboard says `truncated`, the retro says so on stderr.

A task's numbers read only the archives whose `.tasks` names the task (or that have none) — 0.13 s for a task in the
live file, 0.76 s for one begun in the oldest of 10 compressed archives (200 MB of history; measured). The retro
reads every archive; the dashboard and the bootstrap's panel health those renamed since their window's baseline began.

**Bounds.** Appends with no pause between them (not seen in use: a hook writes a line or two a second at most) keep the
lock shared and postpone the rotation — never a loss. A reopen inside the same second as a close whose `done` waited
over 5 s for the lock may be read before it. A transcript resumed after 30 days idle gets a seed: its first turn after
the pause is not counted; so would a resumed or forked transcript that begins with copied old entries (not seen here:
no message id shared between the 300 newest transcripts, 2026-10-19).

**Going back to one file** (a downgrade to 1.6.6, whose readers read only the live file). Close every Claude Code
session of the project first, so nothing writes the journal, then, in the journal directory (`.agent/journal`, or
`.agent/<user>/journal` in a multi-user repo):

<!-- recipe:merge:start -->
```bash
set -eo pipefail
for f in $(ls | grep -E '^enforcement\.jsonl\.archived-[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+(\.gz)?$' | sort -t- -k5,5n); do
  [ "$f" -ef enforcement.jsonl ] && continue          # a rotation killed half-way: the live file itself
  case "$f" in
    *.gz) [ -e "${f%.gz}" ] && continue; zcat -- "$f" ;;   # its source still there: read that
    *) cat -- "$f" ;;
  esac
done > enforcement.jsonl.all
cat enforcement.jsonl >> enforcement.jsonl.all
# every file a rotation started holds a horizon line and carries: read positions, not events
{ grep -v -e '"reason":"read position carried"' -e '"reason":"read positions horizon"' enforcement.jsonl.all || [ $? -eq 1 ]; } > enforcement.jsonl.merged
rm enforcement.jsonl.all
for s in $(ls | grep -E '^enforcement\.jsonl\.spool(ing)?-' || true); do
  [ -s "$s" ] && [ -z "$(tail -c 1 -- "$s")" ] || continue   # whole: a spool, or a dead writer's temporary
  id=$(grep -o '"spool":"[^"]*\.' -- "$s" | head -n 1 || true)
  if [ -n "$id" ] && grep -qF -- "$id" enforcement.jsonl.merged; then continue; fi   # merged already
  cat -- "$s" >> enforcement.jsonl.merged
done
wc -l < enforcement.jsonl.merged
mv enforcement.jsonl.merged enforcement.jsonl
rm -f enforcement.jsonl.archived-* enforcement.jsonl.spool-* enforcement.jsonl.spooling-*
```
<!-- recipe:merge:end -->

Every line comes back (the rotation never deletes one), the carries and the horizon go, and the spooled lines come
last. Tested on rotated journals with each kill state (`tests/test_enforcement_rotation.py`, `TheRollbackRecipe`).

## The hook journal — `journal/hooks.jsonl` (task 222)

The owner, 2026-10-16: "e important ca playbook sa isi salveze infomratiile legat de ce face, ca sa il putem analiza
si imbunatati" — and "Istoric curat + jurnal de hook-uri". Every run of a playbook hook appends ONE line to
`.agent/<lane>/journal/hooks.jsonl` (root lane: `.agent/journal/hooks.jsonl`): Claude's and grok's eight bash hooks,
the project's `monitor-nudge.sh`, codex's three python hooks and `command_guard.py`. A file of its own, not a fifth
family of `enforcement.jsonl`: that file keeps the decisions and the CLI's task transitions the task windows read, and
this one grows tens of times faster (measured on this repository, 2026-10-07 to 2026-10-16: 8,000-13,000 hook runs a
day, implied by the transcripts' tool calls, against 80-500 enforcement records a day). The two files share no key
beyond the session ids and the time.

Every line has the same eighteen keys, in this order (the last five since task 234):

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
| `task` | the session's task (`"007"`): on the state echo's and the stop hook's lines the task their read was for; on every other line the session's pointer `sessions/<id>/current_state`, read by the writer itself; `""` where the session is known and has no task; `null` where the writer does not know the session — monitor-nudge knows it only from `PLAYBOOK_SESSION_ID`, and the command guard resolves no session on an allow (resolving it means importing `tasks.core`, ~24 ms of the guard's ~70 ms, measured), so the same call's task-gate line, with the same `tool_use_id`, names the task — or where the pointer the writer reads is a link or not a regular file (the state echo and the stop hook follow a linked pointer, as they always have, and name the task they acted on; routed to one rule, task 234's impl round 2) |
| `section` | on the state echo's line only: the `##` section the first open gate sits in (`"Work Plan"`), from that call's own accepted read of task.md — the heading's text without `## `, control characters and U+0085 / U+2028 / U+2029 as spaces, at most 64 bytes cut on a whole character; `""` when that read found no open gate or no heading above it; `null` on every other line, and where the read was not accepted (overtaken by another hook, unreadable) or its text was dropped past the line cap |
| `susp` | the whole seconds the machine had slept since boot (CLOCK_BOOTTIME − CLOCK_MONOTONIC), from one bracketed reading by the python process the hook ran; `null` where it ran none or no clean reading was had |
| `sts` | the wall time of that sample, epoch seconds; `null` with `susp` |
| `boot` | the boot id's first 8 characters (`/proc/sys/kernel/random/boot_id`); `null` where none was sampled |

Example (the state echo in a test project, task 234):

```json
{"ts":"2026-10-17T20:09:07Z","hook":"state-echo","event":"PostToolUse","tool":"Bash","tool_use_id":"toolu_01QQ4HUXVqTcKHE1zLD87eio","session_id":"s234","host_session_id":"f156c929-b7b5-4e71-bf48-878ac7c78aeb","agent_id":"","decision":"context","detail":"echo full","ms":223,"exit":0,"error":"","task":"007","section":"Work Plan","susp":25573,"sts":1792267747,"boot":"d6a8e5ef"}
```

**What it never holds:** no command, no path, no prompt — only the hooks' own words and ids, and one text taken from a
file: the open gate's `##` heading in `section`, which the task's own template or its author wrote, cut at 64 bytes.
It is not a conversation record, so a judge's read-only sandbox does not hide it.

**`null` means "not on this line", `""` means "read, and there is none"** (impl round 1, grok): a stop line's `section`
`null` says nothing about the gate; a state echo's `""` says its read found no open gate; a bash hook's `susp` `null`
says no python ran there, not that the machine never slept. Where the samples are taken: the state echo
(`task-status.py --echo`, or `--section` on a failed call), the stop hook (`--fields`), and every python hook (codex's,
the command guard, the dialog hook's answer line — `pb_journal.append_hook_run` samples itself); the task gate, the
chat log, the session hooks, the task-folder watch and monitor-nudge take none. **The sample is one bracketed reading**
— wall, MONOTONIC, BOOTTIME, MONOTONIC, wall, in nanoseconds — kept only when both brackets span at most 5 ms, so a delay, a suspend or a clock step inside it cannot be counted as sleep or paired with the wrong moment (up
to three tries, then `null`). **The sample's moment is `sts`, not `ts`:** the sample is taken while the hook runs and
`ts` when it exits, so a suspend between the two falls on the right side only when `susp` is paired with `sts`. Between
two samples of the same `boot`, the difference of `susp` is the time the machine slept between their `sts`, as
CLOCK_BOOTTIME counts it — measured on this laptop (task 234, W7): 18.7 s under the kernel's suspend entry-to-exit
spans over 3 suspends, about 6 s each, the device suspend and resume work CLOCK_MONOTONIC counts as awake — and a
difference of one second either way is rounding, not sleep; across a different `boot`, it says nothing (each boot
starts from zero).

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

Each line is one append of at most 2,048 bytes (Linux's `PIPE_BUF` is 4,096; a line with every field at its cap measured
1,122 bytes with task 234's five keys, 898 before them, under a cap of 1,024 then), so hooks side by side tear none. Each
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
keep only id characters; `detail`, `error` and `section` are escaped and cut on a whole character and a whole escape
(a line past the cap drops those three texts — `section` then `null` — and keeps every key); `task` is digits, `""`
or `null`, `susp` and `sts` whole numbers of at most 12 digits, `boot` 8 lowercase hex — anything else is `null`.
Nothing is
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
DEBUG trap, `error` names that trap instead of the failing line; on a host without `flock(1)` (util-linux) the bash
writers append without the lock while codex's python hooks still rotate there with `fcntl`, so a line in flight there
can still be lost to a compression after a forward clock step; a crash between publishing a spool and removing its
temporary can record that one line twice (the post-panel judge's run 5, stated); task-dir-watch's `tool_use_id` is read by pattern,
so a host whose payload put a nested object carrying the same key AFTER the id could mislabel it.

## A task's numbers — `<task>/numbers.json` (task 235)

The owner, 2026-10-13 18:44, on PLAN.md's Phase B item 6: "În Faza B (Recomandat)". At every successful close, after
its `done` record, the close composes the task's numbers for that closure from the plugin's own records only — this
journal, the hook journal (with its archives and spools), the run ledger, `chat_log.md` and the receipt — and keeps
them in `numbers.json` in the task folder, one entry per close (`closures[]`, keyed by the receipt's stamp; a re-close
adds one). It composes and writes under the task lock and only while that receipt is still the task's newest, so a
close that paused before writing never replaces a later close's numbers, and no reopen or second close can come between
the receipt it reads and the journal it reads; a reopen that came before the write does not stop it (the closure is
bounded by the reopen's `work`). It holds the task lock for that time — about the 5 s budget: the reading and the
composition keep the deadline, the run ledger's read and the write after them are not bounded — so another session's
write to the task waits that long. The close prints `numbers: <path>` (commit it with the task); a
fault, or 5 s passed — one deadline for the reading, the lock wait and the write — prints `numbers: not written (…)`
and changes nothing else. `tasks numbers [N]`
shows them and writes nothing; for a task not yet closed — a reopened one too — it adds a snapshot of the life in
progress up to now, marked as one, and a newest close missing from the file is composed and shown. The hook journal is
read under its directory's shared lock (rotation takes the exclusive one), and the time split is linear in the
journal's lines (60,000 lines in about half a second, measured).

| key | meaning |
|---|---|
| `closed_at` | the receipt's stamp (the close's moment) |
| `closure_no` | which close of the task this is (its `done` record's rank); with `closed_at` it keys the entry, so two closes in one second keep two; `tasks numbers` shows it as `closure #N` |
| `window` | UTC `start` (the first `tasks work N` after the `done` before this closure's own, else the first) and `end` (the receipt's stamp). The closure is found in the journal's file order, so a close, a reopen and a second close in one second stay apart: its `done` is the task's last one for the newest close (the first at or after the stamp for an older one), and its close, review and verify records lie between its first `work` and the next `work` after its `done` — a review or verify record written after its `done`, even in the same second, is late |
| `snapshot` | true for an open task's composition (never written) |
| `time` | the task's share of time, wall seconds summed over host sessions: `sessions`, `total`, `by_kind`, `by_section` (the state echo's `##` sections; `?` before the first; `""` — `tasks numbers` prints it `(no section)` — when no gate was open, the time after the last tick, or the open gate sat under no `##` heading), `slept`, `away`, `unknown`, `sleep_unmeasured`, `sleep_unplaced` (sleep the samples recorded that no gap could hold — outside `total`); null when the journal holds no activation record for the task |
| `judges_wall` | the union of the task's review spans, wall seconds |
| `seats` | per judge seat: runs, ok, failed, seconds — summed over that seat's runs, not wall time — tokens in / out / cache_read / cache_write from the runs whose usage is known, how many were unknown, the runs by kind |
| `agent` | the main agent's tokens (task 244), from this task's `agent-usage` records between its first `work` and its `done`: per model (a subagent's as `<model> (subagent)`) calls, in, out, cache_read, cache_write, thinking (a part of out), unreadable; `unknown` spans not counted, `dropped` exact duplicates of a span already counted (overlapping spans count: another file read in a file's place); null when the closure has no record (no recorder ran) — a closure written before task 244 has no such key |
| `verify` | the recorded verify runs (the close's, `tasks verify`): runs, ran, reused, failed, seconds |
| `rounds` | panels (mode, outcome, critical, important, owner_ok — the run ledger's), post-panel runs (verdict, critical, important, owner_ok), single-judge calls and tail certifications counted one per review record (a single-judge record within 120 s of a post-panel row is that run), the runs the ledger marks failed |
| `owner` | his prompts — the closure's first one too, logged before the `work` record that opens the window: the prompt that opened the turn in which the task was activated (a prompt that switches tasks counts for both) — the background notifications, his options dialogs answered and cancelled, their questions and answers (counted from the DIALOG entries, matched by their `<!-- dialog:<id> -->` marker), the time he took to answer, the entries' ids |
| `close` | `freshness` (FRESH / STALE / TAIL-CERTIFIED / NO-STAMP / UNREADABLE / …, read at its fixed place in the writer's text, so a reason that names a verdict is not that verdict), `accepted_reason`, `forced_reason`, `owner_ok`, `bound` (a post-panel PASS the close was bound to), `stop_rule`, `post_panel` (runs, last verdict, every owner-ok), the commands' pass / fail counts, `risk` — read whole from the receipt: the panel line split only where the writer puts a field (`; owner-ok: "`, `; bound to post-D6 PASS `, `; stop rule (D6): `), the receipt found outside closed fences (an unclosed fence above does not hide it); the task's `hook=close` records fill only a gap (their text is cut at 200 bytes) |
| `late_records` | the task's review / verify records written after the close's `done` and before the next `work` (`tasks numbers` recounts them after the last `done`) |

Example — the live check of task 235 (a scratch session: prompts, a Read, an options dialog answered after 8 s, a failed
call, a background `sleep 12`, a switch to task 002 and back, then a forced close); its transcript, counted B1's way,
gave agent 32.9 s, owner-question 8.5 s, background 10.7 s for the whole session, and task 001 plus task 002 here give
32, 8 and 11 (the closure below, composed again from the same records by the code of task 236 — which added the
prompt that started it, M001, and the closure's number — without its keys that were empty in that session — `seats`, `verify`, `rounds`,
`late_records` — and the schema / task fields of the file around it):

```json
{"closed_at":"2026-10-18T01:22:06+03:00","closure_no":1,"window":{"start":"2026-10-17T22:17:33Z","end":"2026-10-17T22:22:06Z"},"time":{"sessions":1,"total":222,"by_kind":{"agent":25,"owner-idle":178,"owner-question":8,"background":11},"by_section":{"Debrief":{"agent":25,"owner-idle":178,"owner-question":8,"background":11}},"slept":0,"away":0,"unknown":0,"sleep_unmeasured":1,"sleep_unplaced":0},"owner":{"prompts":7,"notifications":1,"dialogs":1,"cancelled":0,"questions":1,"answers":1,"wait":8,"ids":["M001","M002","M003","M004","M005","M006","M007","M010"]},"close":{"stamp":"2026-10-18T01:22:06+03:00","risk":"reversible","freshness":null,"accepted_reason":null,"forced_reason":"live","owner_ok":null,"bound":null,"stop_rule":null,"post_panel":null,"commands":{"pass":0,"fail":0}}}
```

**How the time is split**, per host session, over the gaps between its consecutive hook-journal lines (each gap once):

1. A gap is the task's only when both its lines are the task's (their `task`, or a command guard's line joined to its
   call by `tool_use_id`). The time spent on another task in between, and the call that switches tasks, is in no closure.
2. *Slept:* between two suspend samples of one `boot`, the `susp` difference goes to the gaps overlapping the two
   samples' own `[sts1, sts2]` (a sample can be written later than it was taken), largest overlap first, each capped at
   its overlap; what no gap can hold is `sleep_unplaced`. Across a `boot` change those gaps are `unknown`. A gap no two
   samples of one boot bracket counts in `sleep_unmeasured` as well (its sleep, if any, stays in its kind).
3. *Away:* a gap whose awake time is over 2 h (B1's rule, judged after the sleep is taken out).
4. *Outside a turn* — from a RELEASED stop or a session's start to the next prompt or the session's end:
   `owner-idle`, or `background` when the prompt ending it is a background task's notification. A held stop does not
   end a turn; a tool event proves one (the turn's prompt can be older than the lines read); a new prompt ends every
   call left open (an interrupted call, a cancelled dialog and a blocked command fire no PostToolUse).
5. *Inside a turn:* `owner-question` while an options dialog is open, else `agent` — the model's time and every
   tool's together.
6. The part of a gap with a Bash call open, of a background gap, or of the close's own session's last gap outside a
   turn (task 249: the close run as a background command after the turn ended; the rule holds for every close's last
   gap outside a turn where the `done` record names the close's session), that overlaps the task's review spans (each review record's end minus its duration) is
   `judges`; overlapping its recorded verify spans, `verify` (a stretch under both counts once, as judges).
7. The close's own session is the one its `done` record names (`session_id`); there the time runs to the receipt's
   stamp, and the close's own call (this task's last shell call with no PostToolUse) stays open through a prompt that
   comes meanwhile (a background notification). A line of that session in the stamp's own second that is no task's
   came after the close (the notification of a close run in the background) and is not the closure's, though its
   suspend sample still brackets the last gap (task 249). Without such a record, the latest call of THIS task still
   open, in any session, of any tool, ends at the stamp. A call another session or another task left open does not
   run on. The close
   takes its own suspend sample as it composes, and a sample written within 120 s after the close (its PostToolUse's,
   on a later `tasks numbers`) also brackets its last gap; a later one does not, so sleep long after the close does
   not land in it. A suspend within those 120 s still can: a pair that straddles the close gives its whole `susp`
   difference to the closing gap (task 236's impl round 2; a stated limit, see below). A sample pair wholly outside the window
   adds nothing to `sleep_unplaced`. A gap that crosses the window's
   start is clipped at it, and keeps only the sleep whose own samples allow it inside the window (sleep they prove fell
   before the window is not charged; sleep they allow on either side is, up to the window part's length). Codex's shell
   (`exec_command`) is carved like Bash.

Per session, `by_kind` + `slept` + `away` + `unknown` equals the task's time, and with the time that is not the task's
it equals the session's span (a test holds this over 400 built timelines).

**Limits:**
- Tool and model time are one kind. PreToolUse runs only for Bash, the edit tools and AskUserQuestion, so a Read, an
  MCP call or an Agent call has no start.
- Bash is not split by command; the hook journal keeps no command. The agent's own test runs count as `agent`, and
  `verify` holds only the recorded runs.
- The agent's own work during a panel stays `agent`; only Bash and background time inside a review span counts as
  `judges` (a polled panel).
- The stamps are whole seconds: each gap is exact to a second either way, and sums are unbiased. On three live
  sessions, against their transcripts counted B1's way, `agent` came within 2.5 s and `owner-idle` within 2 s (task
  235, W2).
- Sleep is measured only between two samples of one boot. `susp` itself undercounts the kernel's suspend spans by
  the device suspend / resume time (task 234, W7).
- Two sessions working on one task at once each count their own time.
- A close run in the background after the turn ended (task 249) leaves `owner-idle` only for its recorded verify and
  review spans:
  - what they do not cover — the close's audit, its numbers, a reused verify — stays `owner-idle`, as does an owner's
    real wait before a close the owner typed;
  - only the close's last gap is carved: a prompt that comes while the close still runs leaves the part before it
    `owner-idle`, and the stretch from the background call's own PostToolUse to the released stop stays `agent`;
  - a close whose own call has a line in the stamp's second ends its wait at that line, uncarved.
- Two races within seconds of a close are not told apart (stated limits, the owner, 2026-10-18, task 242): a close
  whose `done` is written only after a reopen and a second close in another session gives both closures the wrong
  life; and a recomposed closure (a write that failed) can take a suspend from the 120 s after the close into its
  last gap. A close that waits for the task lock while a reopen and a second close finish writes the second close's
  closure (the newest receipt), and its own is not written.
- The review rounds (`rounds`) are bounded by time: the run ledger holds only `ts`, so two lives in one second share
  their rounds.
- A reason that itself holds one of the receipt's separators (`; owner-ok: "`, `; bound to post-D6 PASS `,
  `; stop rule (D6): `) is split there.
- The prompt that switches from one task to another counts in both tasks' `owner`, so a sum of prompts over tasks
  counts each switch twice; the entries' `ids` tell them apart.
- A task closed before this existed has no `numbers.json`; `tasks numbers` composes what the records hold.
