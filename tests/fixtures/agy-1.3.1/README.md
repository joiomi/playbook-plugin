# agy 1.3.1 output fixtures (task 130)

Every file here is the VERBATIM stdout or stderr of the Antigravity CLI `agy` **1.3.1**
(linux_amd64, the same signed-in Google AI Pro account as `../agy-1.2.17/`), captured on
2026-10-07 with **the same commands** as the 1.2.17 README (same scratch project mounted
at `/mnt`, same `NB`/`A`/`line` definitions), so the two sets compare line for line.
Nothing was edited after capture.

| File (`.stdout` + `.stderr`) | rc | Same as 1.2.17? |
|---|---|---|
| `success-pong` | 0 | same event kinds and fields; `init.model` echoes the pinned id |
| `success-tools` | 0 | same; two more `step_update` events for the two tool steps |
| `denied-command` | 0 | same stderr line, empty response |
| `print-timeout` | 0 | same: `status: SUCCESS`, the stderr line is the only timeout signal |
| `error-missing-event` | 1 | same stderr |
| `error-model-rejected` | 1 | same stderr |
| `error-model-needs-effort` | 1 | same stderr |
| `models` | 0 | same ids (`gemini-3.8-flash-high` present) |
| `quota` | 0 | same JSON shape |
| `credits` | 0 | same JSON shape |

Not re-captured (no 1.3.1 file; `tests/test_agy_131.py` uses the 1.2.17 one): `web-tool`,
`error-model-effort-conflict`, `not-signed-in`, `quota-exhausted` (a real quota stop that
cannot be provoked on demand) and the two `constructed-not-captured` pairs.

`tests/test_agy_131.py` runs every test of `tests/test_agy_judge.py` over this set — none is
skipped — with the token counts swapped for 1.3.1's own (`USAGE_131`, written out from the
`result` event of `success-pong`, `success-tools` and `denied-command`).
