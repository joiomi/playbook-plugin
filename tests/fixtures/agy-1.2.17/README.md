# agy 1.2.17 output fixtures (task 111)

Every file here is the VERBATIM stdout or stderr of the Antigravity CLI `agy` 1.2.17
(linux_amd64, signed in with a Google AI Pro account), captured on 2026-10-05 — except
the two pairs whose name contains `constructed-not-captured`, which could not be
provoked and are assembled by hand (see the last section), and `quota-exhausted`, which
is the verbatim END of a real stream (see its row). Nothing was edited after
capture. To keep personal paths out of the files, each call ran with a scratch project
mounted at `/mnt`:

    NB="bwrap --ro-bind / / --proc /proc --dev /dev --bind /tmp /tmp \
        --bind ~/.gemini ~/.gemini --bind ~/.cache ~/.cache --bind ~/.local ~/.local \
        --bind <scratch project> /mnt --chdir /mnt"
    A="--input-format stream-json --output-format stream-json --mode plan"
    line() { python3 -c 'import json,sys; print(json.dumps({"event":"user","message":{"role":"user","content":sys.argv[1]}}))' "$1"; }

The scratch project held one file, `notes.txt` (`scratch project` / `the code word is HERON-5518`).

## Captured

| File (`.stdout` + `.stderr`) | rc | Command |
|---|---|---|
| `success-pong` | 0 | `line "Reply with the single word PONG. Do not use any tool." \| $NB agy --model gemini-3.8-flash-high $A --print-timeout 120s` |
| `success-tools` | 0 | `line "Use exactly two tool calls and no others: first view the file /mnt/notes.txt, then run the shell command python3 -c 'print(6*7)'. Then reply with exactly two lines: CODE=<the code word from the file> and CMD=<the command output>." \| $NB agy --dangerously-skip-permissions --model gemini-3.8-flash-high $A --print-timeout 180s` |
| `web-tool` | 0 | `line "Use the search_web tool exactly once to look up: latest stable CPython release. Then reply with one line: VERSION=<what you found>. Use no other tool." \| $NB agy --dangerously-skip-permissions --model gemini-3.8-flash-high $A --print-timeout 180s` |
| `denied-command` | 0 | `line "Create a file named proof.txt in the current directory containing the single letter x, using whatever tool you have. Then reply WRITE=ok or WRITE=failed with the reason." \| $NB agy --model gemini-3.8-flash-high $A --print-timeout 120s` (no bypass flag: the command request is auto-denied, the response is empty) |
| `print-timeout` | 0 | `line "Write a 3000-word essay on the history of bubblewrap sandboxing. Do not use tools." \| $NB agy --model gemini-3.8-flash-high $A --print-timeout 8s` (exit 0, `status: SUCCESS`, the stderr line is the only timeout signal) |
| `error-missing-event` | 1 | `printf '%s\n' '{"type":"user","message":{"role":"user","content":"PONG"}}' \| $NB agy $A` |
| `error-model-rejected` | 1 | `line PONG \| $NB agy --model gemini-9.9-nope $A` |
| `error-model-needs-effort` | 1 | `line PONG \| agy --model gemini-3.8-flash $A` |
| `error-model-effort-conflict` | 1 | `line PONG \| agy --model gemini-3.8-flash-low --effort high $A` |
| `not-signed-in` | 1 | `line "Reply with the single word PONG" \| env -u XDG_RUNTIME_DIR -u XDG_CONFIG_HOME -u XDG_DATA_HOME -u XDG_CACHE_HOME DBUS_SESSION_BUS_ADDRESS=unix:path=<missing socket> HOME=<empty dir> timeout 60 setsid -w agy $A` (the keyring is unreachable and the state dir is empty; the real sign-in was not touched) |
| `models` | 0 | `agy models` |
| `quota` | 0 | `agy -p /quota --output-format json </dev/null` |
| `credits` | 0 | `agy -p /credits --output-format json </dev/null` |
| `quota-exhausted` | 3 | NOT a hand-run command: a real judge call of task 111's judgebench exam (`hf-011-r6`, `agy:gemini-3.1-pro-high`, 2026-10-05 15:22 EEST) that ran five minutes and then hit the 5-hour limit. `.stderr` is complete (two lines). `.stdout` is the LAST TWO LINES of that call's stream, verbatim — the `error_message` step and the terminal `result` event; the harness keeps only the last 2,000 characters of a failed call's stdout, so the 57 earlier events are not in the file. |

## Constructed, NOT captured

A depleted AI-credit balance and a mid-turn model error could not be provoked, so these
two pairs are written by hand. (A quota stop could not be provoked either — it happened
by itself during the exam; until then this section also held a constructed quota pair,
whose message text and `AGY_ERROR` JSON both turned out wrong: the real wording is
"Individual quota reached", and the real line carries `short_error`, `status`,
`error_code`, `code_kind`, `retryable`, `error_id`.) A test that uses one says so. If a real capture
ever becomes available it replaces the constructed pair, and the rule it feeds is
re-checked against it.

| File | What is real in it | What is assumed |
|---|---|---|
| `credits-too-low.constructed-not-captured` | the message `Your AI credits balance is too low to continue.` (a string in the 1.2.17 binary, also quoted by the changelog); the envelope around it is the real `quota-exhausted` capture with only the message text swapped | that agy reports a depleted credit balance through the same envelope as an exhausted quota |
| `midturn-error.constructed-not-captured` | the changelog's statement that a multi-turn `stream-json` session "warns and continues" after a model or agent error. One real mid-turn failure was seen in the exam (`hf-007-r3`, `agy:gemini-3.1-pro-high`): agy exited 0 with a `result` whose status was not `SUCCESS`, the error `The stream was interrupted. Please continue the task you were working on.` and the partial response `FINDINGS: NONE END FINDINGS` — only the seat's rendered text was kept, not the stream, so it is not a fixture | everything in THIS file: the `ERROR` state on an `agent_response` step, exit 0 with `status: SUCCESS` and partial text, the `AGY_ERROR` line |
