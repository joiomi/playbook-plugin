"""A stand-in judge CLI for integration tests of the review paths (task 184, PLAN S11).

`stub_bin(dir)` writes an executable `claude` into `dir` that does what a judge's process
does at the boundary the review path drives — and no more:

  * reads its prompt on stdin (the claude adapter sends it there) and saves it, with its
    argv, under $PB_STUB_DIR, so a test reads what the review path ACTUALLY sent;
  * tries ONE harmless write into its working directory (the project the judge reviews)
    and says in its answer whether the write went through;
  * fails (exit 1, an error on stdout as claude prints one) when its `--model` is listed
    in $PB_STUB_FAIL_MODELS, so a panel can be given a controlled mix of verdicts;
  * answers, exit 0, with the text of `$PB_STUB_SAY/<model>.<n>.txt` on its n-th call for that model, else of
    `$PB_STUB_SAY/<model>.txt`, when one exists (task 320: a refusal, a wait, a plain answer — texts that are or
    are not a review), each `{CODE}` in it replaced by the run's code the prompt carries (task 347);
  * otherwise answers with one finding in the review form (task 347), exit 0.

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
d="${PB_STUB_DIR:?PB_STUB_DIR unset}"
model=""
prev=""
probe=""
for a in "$@"; do
    [ "$prev" = "--model" ] && model="$a"; prev="$a"
    [ "$a" = "reply with exactly: ok" ] && probe=1     # `tasks models check` / the review's dead-pin probe
done
gone="There's an issue with the selected model ($model). It may not exist or you may not have access to it."
case " ${PB_STUB_GONE_MODELS:-} " in *" $model "*) echo "$gone"; exit 1 ;; esac
if [ -n "$probe" ]; then echo "ok"; exit 0; fi
case " ${PB_STUB_GONE_IN_REVIEW_ONLY:-} " in *" $model "*) echo "$gone"; exit 1 ;; esac
case " ${PB_STUB_BUDGET_MODELS:-} " in *" $model "*) echo "Error: Exceeded USD budget (0.05)"; exit 1 ;; esac
case " ${PB_STUB_TIMEOUT_MODELS:-} " in *" $model "*) echo "API Error: Request timed out."; exit 1 ;; esac
prompt="$(cat)"
n="$(date +%s%N)-$$"
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
# task 347: the review form's code, as the prompt names it ("This run's code is `<12 hex>`"), and this model's call count
code="$(printf '%s' "$prompt" | grep -o 'code is `[0-9a-f]\{12\}`' | head -n1 | cut -c10-21)"
calls="$(ls "$d" | grep -cF -- "prompt-$model-")"      # a fixed string: opus's model is `claude-opus-5-5[1m]`
# task 320: answer with a given text, exit 0 — a refusal, a wait, a plain "OK" — when one is laid out for the model
if [ -n "${PB_STUB_SAY:-}" ]; then
    for f in "$PB_STUB_SAY/$model.$calls.txt" "$PB_STUB_SAY/$model.txt"; do
        if [ -f "$f" ]; then sed "s/{CODE}/$code/g" "$f"; exit 0; fi
    done
fi
# task 347: one finding in the review form, with the run's code
printf 'WRITE-ATTEMPT: %s\n\nREVIEW %s\nFINDING\nseverity: Important\nsettled: no\nwhere: none\nscenario: a finding from the stand-in judge (%s) on README.md → it is reported → judge.md shows it\ndrum: none\nfix: none needed, it is the stand-in.\nEND %s\nCAP 1/5 exhausted\n' "$w" "$code" "$model" "$code"
'''


_AGY = r'''#!/bin/bash
# a stand-in agy: `agy models` prints the 1.2.17 listing shape (`id<TAB>label`), nothing else answers
if [ "${1:-}" = "models" ]; then
    printf 'gemini-3.8-flash-high\tGemini 3.8 Flash (High)\ngemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)\n'
    exit 0
fi
echo "stand-in agy: not a judge" >&2
exit 1
'''


_CODE_IN_PROMPT = r"code is `([0-9a-f]{12})`"
# A record every test project can hold: a Minor finding names no place that must exist, and is never counted.
MINOR_RECORD = ("FINDING\nseverity: Minor\nsettled: no\nwhere: none\n"
                "scenario: a reader opens README.md → it reads well → nothing to change\ndrum: none\nfix: none.")


# An answer in the form whose code a fake judge process fills from its prompt (`fill_code`).
FORM_ANSWER = "REVIEW {CODE}\n" + MINOR_RECORD + "\nEND {CODE}\nCAP 0/5 exhausted\n"


def code_of(prompt: str) -> str:
    """The review form's code a prompt carries (task 347: "This run's code is `<12 hex>`"), or "" when it has none."""
    import re
    m = re.search(_CODE_IN_PROMPT, prompt or "")
    return m.group(1) if m else ""


def in_form(prompt: str, before: str = "", records: str = MINOR_RECORD, cap: str = "CAP 0/5 exhausted") -> str:
    """`before` (text the review form does not read), then an answer in the form for the run whose prompt this is
    (task 347) — by default one Minor finding, which counts nowhere and names no file."""
    code = code_of(prompt)
    return (before.rstrip("\n") + "\n\n" if before else "") + f"REVIEW {code}\n{records}\nEND {code}\n{cap}\n"


def fill_code(text: str, prompt: str) -> str:
    """`text` with each `{CODE}` replaced by the code `prompt` carries — what a fake judge process does with an answer
    laid out before the run's code was known (task 347)."""
    return (text or "").replace("{CODE}", code_of(prompt))


def answer_for(kw: dict, before: str = "") -> str:
    """`in_form` for the call whose keywords these are: a judge adapter's `prompt`, or the prompt a judge process reads
    on stdin (`input`)."""
    return in_form(str(kw.get("prompt") or kw.get("input") or ""), before)


def agy_as_review(stream: str) -> str:
    """A captured agy stream-json run as a review the protocol accepts: its `result` event's answer ends with an answer
    in the review form (task 347; task 320 wanted the CAP line), whose `{CODE}` the fake run fills from the prompt it
    was given (`fill_code`). The captures under tests/fixtures/ stay verbatim; a test that needs the run to read as a
    review passes it through this."""
    import json
    out = []
    for ln in stream.splitlines(keepends=True):
        try:
            ev = json.loads(ln)
        except ValueError:
            out.append(ln)
            continue
        res = ev.get("result") if isinstance(ev, dict) and ev.get("event") == "result" else None
        if isinstance(res, dict) and isinstance(res.get("response"), str) and res["response"].strip():
            res["response"] = (res["response"].rstrip("\n") + "\n\nREVIEW {CODE}\n" + MINOR_RECORD
                               + "\nEND {CODE}\nCAP 0/5 exhausted\n")
            ln = json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + ("\n" if ln.endswith("\n") else "")
        out.append(ln)
    return "".join(out)


def stub_bin(directory: Path, *, agy: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    claude = directory / "claude"
    claude.write_text(_STUB, encoding="utf-8")
    claude.chmod(0o755)
    if agy:
        (directory / "agy").write_text(_AGY, encoding="utf-8")
        (directory / "agy").chmod(0o755)
    return directory


def stub_home(base: Path) -> "tuple[Path, Path]":
    """A scratch HOME under `base`, and the folder in it where the stand-in saves its records. Every sandboxed run has
    /tmp as its own since task 315, so a record left there never reaches the test: it goes where every run may write
    and the host reads, playbook's write log folder of this HOME. git's two settings files are made, empty: the sandbox
    refuses a HOME under /tmp without them (they could be made from inside)."""
    home = base / "home"
    (home / ".config" / "git").mkdir(parents=True, exist_ok=True)
    for f in (home / ".gitconfig", home / ".config" / "git" / "config"):
        f.touch()
    out = home / ".local" / "share" / "playbook" / "stub-out"
    out.mkdir(parents=True, exist_ok=True)
    return home, out


def clean_env(**extra: str) -> "dict[str, str]":
    """The test process's environment without this session's identity, logger or interpreter
    settings — a review started by a test must not act as the session that runs the suite."""
    drop = ("PLAYBOOK_", "CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_PLUGIN_")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(drop) and k not in ("BASH_ENV", "PYTHONPATH", "PYTHONHOME", "CLAUDE_PID",
                                                    "CLAUDE_ENV_FILE", "CLAUDE_PROJECT_DIR")}
    env.update(extra)
    return env


def results(stub_dir: Path, kind: str) -> "list[str]":
    """The stub's saved files of one kind (prompt, argv, write, result), oldest first."""
    return [p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(stub_dir.glob(f"{kind}-*.txt"), key=lambda p: p.stat().st_mtime_ns)]
