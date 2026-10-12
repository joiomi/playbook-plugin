"""A stand-in judge CLI for integration tests of the review paths (task 184, PLAN S11).

`stub_bin(dir)` writes an executable `claude` into `dir` that does what a judge's process
does at the boundary the review path drives — and no more:

  * reads its prompt on stdin (the claude adapter sends it there) and saves it, with its
    argv, under $PB_STUB_DIR, so a test reads what the review path ACTUALLY sent;
  * tries ONE harmless write into its working directory (the project the judge reviews)
    and says in its answer whether the write went through;
  * fails (exit 1, an error on stdout as claude prints one) when its `--model` is listed
    in $PB_STUB_FAIL_MODELS, so a panel can be given a controlled mix of verdicts;
  * otherwise answers with one finding in the review format, exit 0.

No network, no model: the review path, the sandbox and bubblewrap are the real ones.
The leading underscore keeps `unittest discover` from collecting this module.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "playbook"
TASKS = PLUGIN / "scripts" / "tasks"

_STUB = r'''#!/bin/bash
prompt="$(cat)"
d="${PB_STUB_DIR:?PB_STUB_DIR unset}"
n="$(date +%s%N)-$$"
model=""
prev=""
for a in "$@"; do [ "$prev" = "--model" ] && model="$a"; prev="$a"; done
printf '%s' "$prompt" > "$d/prompt-$model-$n.txt"
printf '%s\n' "$@" > "$d/argv-$model-$n.txt"
if { echo "a judge was here" > "$PWD/judge-was-here.txt"; } 2> "$d/write-$model-$n.txt"; then
    w="WROTE"
else
    w="DENIED: $(tr '\n' ' ' < "$d/write-$model-$n.txt")"
fi
printf 'cwd=%s\nwrite=%s\nsandboxed=%s\n' "$PWD" "$w" "${PLAYBOOK_SANDBOXED:-}" > "$d/result-$model-$n.txt"
case " ${PB_STUB_FAIL_MODELS:-} " in
    *" $model "*) echo "API Error: the stub was told to fail for model $model"; exit 1 ;;
esac
printf '**Important** — `src/x.py:1` a finding from the stand-in judge (%s).\n\nWRITE-ATTEMPT: %s\n\nCAP: 1/5 reported, exhausted\n' "$model" "$w"
'''


def stub_bin(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    claude = directory / "claude"
    claude.write_text(_STUB, encoding="utf-8")
    claude.chmod(0o755)
    return directory


def clean_env(**extra: str) -> "dict[str, str]":
    """The test process's environment without this session's identity, logger or interpreter
    settings — a review started by a test must not act as the session that runs the suite."""
    drop = ("PLAYBOOK_", "CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_PLUGIN_")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(drop) and k not in ("BASH_ENV", "PYTHONPATH", "PYTHONHOME")}
    env.update(extra)
    return env


def results(stub_dir: Path, kind: str) -> "list[str]":
    """The stub's saved files of one kind (prompt, argv, write, result), oldest first."""
    return [p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(stub_dir.glob(f"{kind}-*.txt"), key=lambda p: p.stat().st_mtime_ns)]
