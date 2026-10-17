# claude 2.1.302 judge output fixtures (task 232)

Every file here is the VERBATIM stdout or stderr of `claude -p` (Claude Code 2.1.302, signed in with a Claude
subscription), captured on 2026-10-17 in a scratch folder outside any repository. Nothing was edited after capture.
Each call piped its prompt on stdin, as the judge does, with the judge's environment (`CLAUDECODE=` and
`PLAYBOOK_SESSION_ID=judge`, `CLAUDE_CODE_SSE_PORT` and `CLAUDE_CODE_ENTRYPOINT` unset) and the judge's flags where
they matter (`--effort high --tools Read --allowedTools Read`).

| file | call | exit |
|---|---|---|
| `success.stdout` | `--model haiku --output-format json --max-budget-usd 0.05`, prompt "Reply with the single word OK." | 0 |
| `multiturn.stdout` | `--model haiku --output-format json --max-budget-usd 0.5`, prompt: read `a.txt` then `b.txt` (one Read each) and reply with both — 3 turns | 0 |
| `budget-stop.stdout` | `--model haiku --output-format json --max-budget-usd 0.00001` | 1 |
| `bad-model.stdout`, `bad-model.stderr` | `--model claude-nonexistent-9-9 --output-format json` | 1 |
| `plain-budget-stop.stdout` | the budget stop again, plain text (no `--output-format`) | 1 |
| `plain-bad-model.stdout`, `plain-bad-model.stderr` | the bad model again, plain text | 1 |

What they show (task 232's plan-panel triage):
- the top-level `usage` is cumulative over the turns (`multiturn`: 6 / 4,271 / 19,884 / 263) and equals the one entry of
  `modelUsage`; `usage.iterations` holds the LAST call only (2 / 192 / 8,053 / 13);
- a budget stop zeroes the top-level `usage` but `modelUsage` keeps the tokens spent; its words are in `errors`
  ("Reached maximum budget ($0.00001)"), `result` is absent and `subtype` is `error_max_budget_usd`;
- a bad model is `subtype` `success` with `is_error` true, and `result` holds the same sentence plain mode prints.

An account limit could not be provoked; the tests build its JSON-mode envelope from the plain line task 156 captured.
