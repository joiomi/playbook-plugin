"""Live rows of the session group, part two (task 184, W3): hook payloads, the capability report,
slash commands and the merge skill's push gate — each in real headless claude sessions on the
CANDIDATE (`common.session_project`, `common.claude_session`), read from what the sessions left:
captured payloads, transcripts, hook responses, the remote's refs."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from common import PLUGIN, Run, claude_session, clean_env, journal, session_project, tempdir, tool_results
from rows_session import RULES, blocked, hook_responses, stop_rounds

NORMALIZER = PLUGIN / "scripts" / "hook-payload-normalize.py"
EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")


def _capture_hooks(project: Path) -> Path:
    """A project hook beside the plugin's that saves every payload Claude Code sends, byte for byte."""
    cap = tempdir("pb184-cap-")
    cmd = f"cat > {shlex.quote(str(cap))}/$(date +%s%N)-$$.json"
    settings = project / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"] = {ev: [{**({"matcher": "*"} if ev in ("PreToolUse", "PostToolUse") else {}),
                           "hooks": [{"type": "command", "command": cmd}]}] for ev in EVENTS}
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return cap


def _payloads(cap: Path) -> "list[tuple[Path, bytes, dict]]":
    out = []
    for f in sorted(cap.glob("*.json")):
        raw = f.read_bytes()
        try:
            out.append((f, raw, json.loads(raw)))
        except ValueError:
            out.append((f, raw, {}))
    return out


def _transcript(events: "list[dict]") -> "Path | None":
    sid = next((e.get("session_id") for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    if not sid:
        return None
    return next((Path.home() / ".claude" / "projects").glob(f"*/{sid}.jsonl"), None)


def _normalize(raw: bytes, *flags: str) -> bytes:
    return subprocess.run(["python3", "-E", "-s", str(NORMALIZER), *flags], input=raw, capture_output=True,
                          timeout=60, env=clean_env()).stdout


def _proj(prefix: str, name: str = "proj") -> Path:
    p = session_project(tempdir(prefix) / name)
    (p / "src").mkdir(exist_ok=True)
    (p / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    return p


def payload_normalize(out: Path) -> int:
    run = Run("PB-PAYLOAD-NORMALIZE", "real Claude hook payloads fed to the candidate's normalizer", out, ["claude"])
    p = _proj("pb184-pay-")
    cap = _capture_hooks(p)
    _r, ev = claude_session(run, "an active task and every tool kind the hooks parse", p, RULES + (
        "1. Run: .claude/bin/tasks new quick pay-probe \"a probe task\"\n"
        "2. Run: .claude/bin/tasks work 1\n"
        "3. Read src/app.py.\n"
        "4. Edit src/app.py: change `x = 1` to `x = 2`.\n"
        "5. Write a new file src/new.py containing the single line `y = 3`.\n"
        "6. Run: echo \"payload probe\"\n"
        "Then reply DONE.\n"))
    caught = _payloads(cap)
    run.note("captured payloads: " + json.dumps([(f.name, d.get("hook_event_name"), d.get("tool_name")) for f, _b, d in caught]))
    kinds = {(d.get("hook_event_name"), d.get("tool_name")) for _f, _b, d in caught}
    need = {("PreToolUse", t) for t in ("Read", "Edit", "Write", "Bash")} | {("UserPromptSubmit", None), ("Stop", None)}
    run.clause("real payloads were captured for the prompt, the stop and every tool kind the hooks parse",
               need <= kinds, f"missing: {sorted(map(str, need - kinds))}")
    same = [f.name for f, b, _d in caught if _normalize(b) == b]
    run.clause("every native Claude payload passes through the candidate's normalizer byte for byte",
               bool(caught) and len(same) == len(caught), f"{len(same)} of {len(caught)} identical")
    bad = []
    for f, b, d in caught:
        parts = _normalize(b, "--emit-fields").split(b"\0")
        ti = d.get("tool_input") or {}
        want = [b"pb-fields-v4", str(d.get("tool_name") or "").encode(), str(ti.get("file_path") or "").encode()]
        fp = str(ti.get("file_path") or "")
        if len(parts) < 8 or parts[:3] != want or parts[4] != str(ti.get("command") or "").encode() \
                or parts[5] != str(d.get("transcript_path") or "").encode() or parts[6] != b \
                or parts[7] != (os.path.realpath(fp) if fp else "").encode():
            bad.append(f.name)
    run.clause("the fields the gate reads (`--emit-fields`) are the payload's own: tool, path, command, transcript, "
               "the payload itself unchanged, and the path with its links followed (v3, task 198; the frame is v4 since task 222)",
               bool(caught) and not bad, f"mismatched: {bad}")
    # the control: the comparison sees a change when there is one — a foreign (camelCase) dialect of a captured Edit
    edit = next((d for _f, _b, d in caught if d.get("hook_event_name") == "PreToolUse" and d.get("tool_name") == "Edit"), None)
    if edit:
        ti = edit["tool_input"]
        foreign = json.dumps({"hookEventName": "PreToolUse", "sessionId": edit.get("session_id"), "toolName": "StrReplace",
                              "toolInput": {"path": ti["file_path"], "old_string": ti.get("old_string"),
                                            "new_string": ti.get("new_string")}}).encode()
        got = _normalize(foreign)
        try:
            norm = json.loads(got)
        except ValueError:
            norm = {}
        run.clause("CONTROL: a foreign dialect of the same Edit is rewritten to the Claude schema (the byte check can fail)",
                   got != foreign and norm.get("tool_name") == "Edit"
                   and (norm.get("tool_input") or {}).get("file_path") == ti["file_path"],
                   f"normalized: {got[:200]!r}")
    else:
        run.clause("CONTROL: a foreign dialect of the same Edit is rewritten to the Claude schema", False, "no Edit payload")
    return run.finish()


def capability_degrade(out: Path) -> int:
    run = Run("PB-PROVIDER-CAPABILITY-DEGRADE", "claude's advertised capability cells, observed in a real session; "
              "the model-detection report against the real CLIs", out, ["claude", "agy"])
    p = _proj("pb184-capa-")
    cap = _capture_hooks(p)
    _r, ev = claude_session(run, "a refused edit, a task, an allowed edit, the turn ends", p, RULES + (
        "1. Edit src/app.py: change `x = 1` to `x = 2` (read it first).\n"
        "2. Run: .claude/bin/tasks new quick capa-probe \"a capability probe\"\n"
        "3. Run: .claude/bin/tasks work 1\n"
        "4. Edit src/app.py: change `x = 1` to `x = 2` (read it first).\n"
        "Then reply DONE.\n"))
    sid = next((e.get("session_id") for e in ev if e.get("type") == "system" and e.get("subtype") == "init"), "")
    code = ("import json, sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
            "from provider.adapters.claude import ClaudeAdapter; "
            "c = ClaudeAdapter(sys.argv[2], Path(sys.argv[3])).detect_capabilities(); "
            "print(json.dumps({k: (str(v) if isinstance(v, Path) else v) for k, v in c.__dict__.items()}))")
    adv = run.step("the candidate adapter's own report for this project", ["python3", "-I", "-c", code, str(PLUGIN), sid, str(p)],
                   env=clean_env())
    try:
        caps = json.loads(adv.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        caps = {}
    log = (p / ".agent" / "chat_log.md").read_text(encoding="utf-8") if (p / ".agent" / "chat_log.md").exists() else ""
    j = journal(p)
    run.clause("has_user_prompt_hook: the prompt hook fired and the plugin logged the prompt",
               caps.get("has_user_prompt_hook") is True and bool(hook_responses(ev, "UserPromptSubmit"))
               and "a capability probe" in log, f"advertised {caps.get('has_user_prompt_hook')}; "
               f"{len(hook_responses(ev, 'UserPromptSubmit'))} responses; prompt logged {'a capability probe' in log}")
    refused = [x for x in j if x.get("hook") == "task-gate" and x.get("decision") == "block"]
    run.clause("has_pre_tool_hook: the pre-tool hook BLOCKED a real edit (journaled, then allowed under a task)",
               caps.get("has_pre_tool_hook") is True and bool(refused)
               and (p / "src" / "app.py").read_text(encoding="utf-8").startswith("x = 2"),
               f"advertised {caps.get('has_pre_tool_hook')}; refusals {[x.get('reason') for x in refused]}")
    run.clause("has_post_tool_hook: the post-tool hook ran after the tools and spoke (the state echo)",
               caps.get("has_post_tool_hook") is True
               and any("Working on task" in json.dumps(x) for x in hook_responses(ev, "PostToolUse")),
               f"advertised {caps.get('has_post_tool_hook')}; {len(hook_responses(ev, 'PostToolUse'))} responses")
    stops = stop_rounds(ev)
    run.clause("has_stop_hook: the stop hook held the end of a turn that wrote with gates open",
               caps.get("has_stop_hook") is True and bool(stops) and blocked(stops[0]),
               f"advertised {caps.get('has_stop_hook')}; stops blocked {[blocked(s) for s in stops]}")
    caught = _payloads(cap)
    ids = {d.get("session_id") for _f, _b, d in caught}
    run.clause("session_id_in_payload: every payload carries the session's id", caps.get("session_id_in_payload") is True
               and bool(caught) and ids == {sid}, f"advertised {caps.get('session_id_in_payload')}; ids {sorted(map(str, ids))} vs {sid}")
    tr = _transcript(ev)
    lines_ok = bool(tr) and all(json.loads(x) is not None for x in tr.read_text(encoding="utf-8").splitlines() if x.strip())
    run.clause("session_log_format jsonl, session_log_base where Claude Code writes (a plain path)",
               caps.get("session_log_format") == "jsonl" and bool(tr) and lines_ok
               and caps.get("session_log_base") == str(tr.parent) if tr else False,
               f"advertised base {caps.get('session_log_base')}; real transcript {tr}")
    # the bound (parked on task 184): a path with characters other than letters, digits, `/` and `-`
    q = _proj("pb184-capb-", name="my_proj.v2")
    _r2, ev2 = claude_session(run, "a session in a path holding `_` and `.`", q,
                              "Reply with the single word OK.\n", extra=("--tools", ""))
    sid2 = next((e.get("session_id") for e in ev2 if e.get("type") == "system" and e.get("subtype") == "init"), "")
    tr2 = _transcript(ev2)
    adv2 = run.step("the adapter's report there", ["python3", "-I", "-c", code, str(PLUGIN), sid2, str(q)], env=clean_env())
    try:
        caps2 = json.loads(adv2.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        caps2 = {}
    expect = re.sub(r"[^A-Za-z0-9]", "-", str(q))
    run.clause("BOUND as stated: for a path holding `_` or `.` Claude Code writes the transcript under every "
               "non-alphanumeric mapped to `-`, and the adapter (mapping only `/`) reports no session log base",
               bool(tr2) and tr2.parent.name == expect and caps2.get("session_log_base") is None,
               f"real folder {tr2.parent.name if tr2 else None}; expected {expect}; adapter {caps2.get('session_log_base')}")
    # the model-detection report against the real CLIs
    agy = run.step("the ids the real `agy models` prints", ["agy", "models"], env=clean_env(), timeout=120)
    real_ids = [ln.split("\t")[0].strip() for ln in agy.stdout.splitlines() if ln.strip() and "\t" in ln]
    det = run.step("tasks models detect", [str(p / ".claude" / "bin" / "tasks"), "models", "detect"], cwd=p, env=clean_env(),
                   timeout=300)
    block = det.stdout.split("[agy]", 1)[1].split("\n[", 1)[0] if "[agy]" in det.stdout else ""
    run.clause("the agy entry lists every id `agy models` prints, says to pin a seat as `agy:<id>` and names it an "
               "experimental judge seat that `models check` live-probes", bool(real_ids) and all(i in block for i in real_ids)
               and "pin a seat as agy:<id>" in block and "experimental judge seat" in block and "live-probes" in block,
               f"real ids {real_ids}; entry: {block[:300]!r}")
    pin = next((i for i in real_ids if i.endswith("-medium")), real_ids[0] if real_ids else "")
    (p / ".agent" / "models.json").write_text(json.dumps({"panel": ["haiku", f"agy:{pin}"], "default_judge": "haiku"}),
                                              encoding="utf-8")
    chk = run.step("tasks models check (live probes)", [str(p / ".claude" / "bin" / "tasks"), "models", "check"], cwd=p,
                   env=clean_env(), timeout=600)
    verdict = {ln.split()[0]: ln.split()[1] for ln in chk.stdout.splitlines() if len(ln.split()) > 1
               and ln.split()[0] in ("haiku", f"agy:{pin}")}
    run.clause("`models check` live-probes both pins and finds them answering", chk.returncode == 0
               and verdict.get("haiku") == "OK" and verdict.get(f"agy:{pin}") == "OK", f"rc {chk.returncode}; {verdict}")
    (p / ".agent" / "models.json").write_text(json.dumps({"panel": ["claude-nonexistent-9-9"], "default_judge": "haiku"}),
                                              encoding="utf-8")
    bad = run.step("CONTROL: a pin no CLI knows", [str(p / ".claude" / "bin" / "tasks"), "models", "check"], cwd=p,
                   env=clean_env(), timeout=600)
    run.clause("CONTROL: a pin the real CLI rejects is reported GONE and the check fails", bad.returncode != 0
               and re.search(r"claude-nonexistent-9-9\s+GONE", bad.stdout) is not None, f"rc {bad.returncode}")
    return run.finish()


SAFE_ARGS = {"init": "probe-proj", "intent": "1", "playbook": "build"}
DISPATCH = ("This session tests slash-command dispatch only. Do NOT carry out the instructions you receive and do not "
            "attempt any tool call. Reply with exactly one line: RECEIVED: <the first Markdown heading of the "
            "instructions you received, without the # signs>")


def slash_commands(out: Path) -> int:
    run = Run("PB-SLASH-COMMANDS", "every shipped command discovered and dispatched in a real claude session", out, ["claude"])
    names = sorted(f.stem for f in (PLUGIN / "commands").glob("*.md"))
    run.note("shipped command files: " + ", ".join(names))
    seen_lists, missing = [], []
    for name in names:
        p = _proj("pb184-slash-")
        arg = SAFE_ARGS.get(name, "")
        _r, ev = claude_session(run, f"/playbook:{name}", p, f"/playbook:{name} {arg}".rstrip() + "\n",
                                extra=("--tools", "", "--append-system-prompt", DISPATCH), timeout=300)
        init = next((e for e in ev if e.get("type") == "system" and e.get("subtype") == "init"), {})
        seen_lists.append(set(init.get("slash_commands") or []))
        body = (PLUGIN / "commands" / f"{name}.md").read_text(encoding="utf-8").split("---", 2)[-1]
        heading = next((ln.lstrip("# ").strip() for ln in body.splitlines() if ln.startswith("#")), "")
        tr = _transcript(ev)
        text = tr.read_text(encoding="utf-8") if tr else ""
        tagged = f"<command-name>/playbook:{name}</command-name>" in text
        expanded = bool(heading) and json.dumps(heading)[1:-1] in text
        reply = " ".join(c.get("text", "") for e in ev if e.get("type") == "assistant"
                         for c in (e.get("message") or {}).get("content") or [] if isinstance(c, dict) and c.get("type") == "text")
        arg_ok = not arg or f"<command-args>{arg}</command-args>" in text
        run.note(f"/playbook:{name}: transcript {tr}; tagged {tagged}; body heading {heading!r} in the transcript {expanded}; "
                 f"argument {arg!r} passed {arg_ok}; reply {reply[:160]!r}")
        if not (tagged and expanded and arg_ok and reply.strip().startswith("RECEIVED:")):
            missing.append(name)
    run.clause("discovery: every shipped command is listed by the session as `playbook:<name>`",
               bool(seen_lists) and all({f"playbook:{n}" for n in names} <= s for s in seen_lists),
               f"missing from a listing: {sorted({f'playbook:{n}' for n in names} - set.intersection(*seen_lists)) if seen_lists else 'no session'}")
    run.clause("dispatch: each command's own text reached the model's conversation (its name tag, its body, its "
               "argument — the session's transcript) and the model answered it", not missing,
               f"not dispatched as expected: {missing}")
    return run.finish()


MAP = "# Mind Map\n\n[1] **root** — the scratch project; see [2]\n[2] **app** — `src/`, links [1]\n"


def _merge_repo(command: str) -> "tuple[Path, Path]":
    """A disposable two-branch playbook repository with a local bare remote and a declared soundness command."""
    p = session_project(tempdir("pb184-merge-") / "proj")
    env = clean_env()
    g = lambda *a: subprocess.run(["git", *a], cwd=p, check=True, capture_output=True, text=True, env=env)  # noqa: E731
    remote = p.parent / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, env=env)
    (p / "src").mkdir(exist_ok=True)
    (p / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (p / "src" / "b.py").write_text("B = 1\n", encoding="utf-8")
    (p / "MIND_MAP.md").write_text(MAP, encoding="utf-8")
    (p / ".agent" / "config.json").write_text(json.dumps({"merge_verify": {"command": command}}) + "\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    g("branch", "-M", "main")
    g("remote", "add", "origin", str(remote))
    g("push", "-q", "-u", "origin", "main")
    g("checkout", "-q", "-b", "feature")
    (p / "src" / "b.py").write_text("B = 2\n", encoding="utf-8")
    g("commit", "-q", "-am", "feature: B = 2")
    g("push", "-q", "-u", "origin", "feature")
    g("checkout", "-q", "main")
    (p / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    g("commit", "-q", "-am", "main: A = 2")
    g("push", "-q", "origin", "main")
    return p, remote


def _remote_main(remote: Path) -> str:
    return subprocess.run(["git", "--git-dir", str(remote), "rev-parse", "main"], capture_output=True, text=True,
                          env=clean_env()).stdout.strip()


def _calls_with_results(events: "list[dict]") -> "list[tuple[str, str, str]]":
    """(tool, shell command or input, result text) of every tool call, in order, each paired with ITS result."""
    results: "dict[str, str]" = {}
    for e in events:
        if e.get("type") == "user":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    body = c.get("content")
                    if isinstance(body, list):
                        body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                    results[str(c.get("tool_use_id"))] = str(body)
    out = []
    for e in events:
        if e.get("type") == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    out.append((c.get("name", ""), str(inp.get("command", json.dumps(inp))), results.get(str(c.get("id")), "")))
    return out


def _hunk_changes(section: str) -> "list[str] | None":
    """The changed lines of one `diff --git` section, read the way git wrote them: each hunk's `@@ -a,b +c,d @@`
    header says how many old and new lines follow, so output a combined command printed after the diff (an
    `echo --verify`) is never taken for a removed line. None when a hunk is cut short."""
    lines, out, i = section.splitlines(), [], 0
    while i < len(lines):
        m = re.match(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", lines[i])
        i += 1
        if not m:
            continue
        old = int(m.group(1)) if m.group(1) is not None else 1
        new = int(m.group(2)) if m.group(2) is not None else 1
        while (old > 0 or new > 0) and i < len(lines):
            ln = lines[i]
            if ln.startswith("-"):
                old -= 1
                out.append(ln)
            elif ln.startswith("+"):
                new -= 1
                out.append(ln)
            elif ln.startswith(" ") or ln == "":
                old, new = old - 1, new - 1
            elif not ln.startswith("\\"):                  # `\ No newline at end of file` counts for neither
                break
            i += 1
        if old or new:
            return None
    return out


def identity_green(result: str) -> bool:
    """The code-identity diff ran and shows exactly the merge's incoming change — the feature's one change in
    `src/b.py` — and nothing else: no error, no other file, no other changed line in that file (impl panel I7:
    a command that merely matched, `fatal: bad revision` included, used to count as green; round 2, T15: an
    extra line inside `src/b.py` did; J4: a mode change did). A command that printed the same diff twice is still
    that diff."""
    if re.search(r"(?m)^(fatal|error):", result):
        return False
    sections = re.split(r"(?m)^(?=diff --git )", result)[1:]
    if not sections:
        return False
    for s in sections:
        head = re.match(r"diff --git a/(\S+) b/(\S+)", s)
        # git's header for a content-only change of a regular file, and nothing else before the first hunk: a mode
        # change, a rename or a binary note shows here (single judge after round 2, J4)
        meta = []
        for ln in s.splitlines()[1:]:
            if ln.startswith("@@"):
                break
            meta.append(ln)
        plain = (len(meta) == 3 and re.fullmatch(r"index [0-9a-f]+\.\.[0-9a-f]+ 100644", meta[0]) is not None
                 and meta[1:] == ["--- a/src/b.py", "+++ b/src/b.py"])
        if not head or head.groups() != ("src/b.py", "src/b.py") or not plain \
                or _hunk_changes(s) != ["-B = 1", "+B = 2"]:
            return False
    return True


# each gate: what its command looks like, and what its GREEN outcome prints (the skill's own tools' words) —
# a pattern its result must match, or a function of its result
GATES = {"ref-integrity": (r"ref-integrity\.py", r"ref-integrity: clean"),
         "contamination (merge-doctor)": (r"tasks merge-doctor", r"SAFE TO CONTINUE"),
         # the skill's Step 7(d) form: the whole tree against the pre-merge tip, minus the paths the semantic steps own
         "code identity": (r"git diff\s+(\"?\$\w+\"?|[0-9a-f]{7,40})\b[^|;&\n]*:\(exclude,top\)MIND_MAP\.md", identity_green),
         "merge-verify": (r"merge-verify\.py(?![^;&|\n]*--plan)", r"merge-verify: GREEN")}


def _gate_timeline(calls: "list[tuple[str, str, str]]") -> "dict[str, tuple[int, int]]":
    """Where each gate first ran green, and where the first push ran, as (call index, offset in its command)."""
    at: "dict[str, tuple[int, int]]" = {}
    for i, (name, cmd, res) in enumerate(calls):
        if name != "Bash":
            continue
        for g, (rx, ok) in GATES.items():
            m = re.search(rx, cmd)
            if g not in at and m and (ok(res) if callable(ok) else re.search(ok, res)):
                at[g] = (i, m.start())
        m = re.search(r"git push", cmd)
        if "push" not in at and m:
            at["push"] = (i, m.start())
    return at


def merge_push_gate(out: Path) -> int:
    run = Run("PB-MERGE-PUSH-GATE", "/playbook:merge --push on a disposable two-branch repository", out, ["claude"])
    prompt = ("/playbook:merge feature main --push\n")
    p, remote = _merge_repo("test -f src/a.py && test -f src/b.py && grep -q 'B = 2' src/b.py")
    before = _remote_main(remote)
    _r, ev = claude_session(run, "GREEN: the declared soundness command passes", p, prompt, model="sonnet", timeout=1500)
    calls = _calls_with_results(ev)
    at = _gate_timeline(calls)
    after = _remote_main(remote)
    merged = subprocess.run(["git", "--git-dir", str(remote), "rev-list", "--parents", "-n", "1", "main"],
                            capture_output=True, text=True, env=clean_env()).stdout.split()
    run.note("gate timeline — (call, offset in its command) where each gate first ran green, and the push: " + json.dumps(at))
    run.clause("the merge was pushed: the remote's main is now a merge commit of the old main and feature",
               after != before and len(merged) == 3, f"remote main {before[:10]} -> {after[:10]}; parents {len(merged) - 1}")
    run.clause("the push came only AFTER ref-integrity, the contamination check, code identity and merge-verify had each "
               "shown its green outcome", "push" in at and all(g in at and at[g] < at["push"] for g in GATES),
               f"timeline {at}")
    # the control: the same merge, the declared command fails
    q, remote2 = _merge_repo("grep -q 'B = 3' src/b.py")
    before2 = _remote_main(remote2)
    _r2, ev2 = claude_session(run, "CONTROL — FAILED: the declared soundness command fails", q, prompt, model="sonnet",
                              timeout=1500)
    calls2 = _calls_with_results(ev2)
    pushed2 = any(n == "Bash" and re.search(r"git push", c) for n, c, _r in calls2)
    ran_verify = any(re.search(r"merge-verify\.py", c) and "FAILED" in r for _n, c, r in calls2)
    run.note("control commands: " + json.dumps([c[:200] for _n, c, _r in calls2]))
    final = " ".join(c.get("text", "") for e in ev2 if e.get("type") == "assistant"
                     for c in (e.get("message") or {}).get("content") or [] if isinstance(c, dict) and c.get("type") == "text")
    run.clause("CONTROL: with the soundness command failing, nothing is pushed and the run says the verify gate failed",
               _remote_main(remote2) == before2 and not pushed2 and ran_verify
               and re.search(r"FAILED|fail", final) is not None,
               f"remote main unchanged: {_remote_main(remote2) == before2}; a push call: {pushed2}; "
               f"merge-verify ran and printed FAILED: {ran_verify}")
    return run.finish()


def task_dir_watch(out: Path) -> int:
    """Task 196 (PLAN S14 T5b, B31): a task folder made by hand in a real session is flagged after the call, the next
    action is held, the way out releases it — also after a command that FAILS (measured: Claude Code fires only
    PostToolUseFailure then). Task 197: the Bash pre-run check is gone, so a real `mkdir -p` of a task folder (the B31
    case) runs, and is caught after the call like the copy."""
    run = Run("PB-TASK-DIR-WATCH", "a task folder made by hand is caught after the call and holds the session", out,
              ["claude"])
    p = _proj("pb196-watch-")
    (p / "notes.md").write_text("a\n", encoding="utf-8")
    _r, ev = claude_session(run, "a folder copied by hand, an edit, the removal, a failing copy, adopt", p, RULES + (
        "1. Run: .claude/bin/tasks new quick watch-probe \"a probe task\"\n"
        "2. Run exactly: cp -r .agent/tasks/001-watch-probe .agent/tasks/002-by-hand\n"
        "3. Edit notes.md: change `a` to `b` (read it first).\n"
        "4. Run exactly: rm -r .agent/tasks/002-by-hand\n"
        "5. Edit notes.md: change its text to `c` (read it first).\n"
        "6. Run exactly: cp -r .agent/tasks/001-watch-probe .agent/tasks/003-by-hand; false\n"
        "7. Run exactly: ls\n"
        "8. Run exactly: .claude/bin/tasks adopt .agent/tasks/003-by-hand\n"
        "9. Run exactly: ls\n"
        "10. Run exactly: mkdir -p .agent/tasks/004-by-hand\n"
        "11. Edit notes.md: change its text to `d` (read it first).\n"
        "12. Run exactly: rm -r .agent/tasks/004-by-hand\n"
        "13. Edit notes.md: change its text to `e` (read it first).\n"
        "Then reply DONE.\n"))
    post = [str(x.get("output") or "") for x in hook_responses(ev, "PostToolUse")]
    fail = [str(x.get("output") or "") for x in hook_responses(ev, "PostToolUseFailure")]
    pre = hook_responses(ev, "PreToolUse")
    held = [x for x in pre if x.get("exit_code") == 2 and "task folder appeared" in str(x.get("output") or "")
            + str(x.get("stderr") or "")]
    held_names = [str(x.get("hook_name", "")) for x in held]
    run.note("PreToolUse refusals by the watch: " + json.dumps(
        [(str(x.get("hook_name")), (str(x.get("stderr") or x.get("output") or ""))[:160]) for x in held]))
    run.clause("the copy made by hand is flagged by the watch after the shell call, and the agent is told",
               any("002-by-hand" in o and "did not make" in o for o in post),
               f"{len(post)} PostToolUse responses; naming 002-by-hand: {sum('002-by-hand' in o for o in post)}")
    run.clause("the next Edit is held, naming the folder and the way out",
               any(n.startswith("PreToolUse:Edit") for n in held_names)
               and any("rm -r .agent/tasks/002-by-hand" in str(x.get("stderr") or x.get("output") or "")
                       for x in held), f"held: {held_names}")
    notes = (p / "notes.md").read_text(encoding="utf-8")
    run.clause("the exact removal is let through, and the Edit after it goes through",
               not (p / ".agent" / "tasks" / "002-by-hand").exists() and notes.strip() == "e",
               f"002-by-hand present: {(p / '.agent' / 'tasks' / '002-by-hand').exists()}; notes.md: {notes!r}")
    run.clause("a copy made by a command that FAILS is caught through PostToolUseFailure",
               any("003-by-hand" in o for o in fail), f"{len(fail)} PostToolUseFailure responses: "
               + json.dumps([o[:120] for o in fail]))
    run.clause("the call after it is held (the `ls`)", any(n.startswith("PreToolUse:Bash") for n in held_names),
               f"held: {held_names}")
    # task 197 (B31): the real `mkdir -p` is not refused before it runs — the gate's old words never appear — it is
    # flagged after the call, the Edit after it is held naming it, and its removal releases the session
    every = [str(x.get("stderr") or "") + str(x.get("output") or "") for x in pre]
    run.clause("a real `mkdir -p` of a task folder runs (no PreToolUse refusal of it) and is flagged after the call",
               any("004-by-hand" in o and "did not make" in o for o in post + fail)
               and not any("create task directories manually" in o for o in every),
               f"post/failure responses naming 004-by-hand: {sum('004-by-hand' in o for o in post + fail)}; the old "
               f"refusal's words: {sum('create task directories manually' in o for o in every)}")
    run.clause("the Edit after it is held, naming that folder",
               any(n.startswith("PreToolUse:Edit") and "004-by-hand" in str(x.get("stderr") or x.get("output") or "")
                   for n, x in zip(held_names, held)), f"held: {held_names}")
    run.clause("its removal is let through and the last Edit goes through",
               not (p / ".agent" / "tasks" / "004-by-hand").exists() and notes.strip() == "e",
               f"004-by-hand present: {(p / '.agent' / 'tasks' / '004-by-hand').exists()}; notes.md: {notes!r}")
    reg = (p / ".agent" / "task-registry").read_text(encoding="utf-8") if (p / ".agent" / "task-registry").exists() \
        else ""
    flag = p / ".agent" / "sessions"
    flags = [f for f in flag.glob("*/task_dir_flag")] if flag.exists() else []
    run.clause("`tasks adopt` is let through, registers the folder and releases the session",
               "003-by-hand\tadopt\t" in reg and not flags, f"registry rows for 003: "
               f"{[l for l in reg.splitlines() if l.startswith('003-')]}; flags left: {[str(f) for f in flags]}")
    rows = journal(p)
    run.clause("the watch journals the flag and the hold", any(j.get("hook") == "task-dir-watch"
               and j.get("decision") == "record" and j.get("reason") == "unknown task folder" for j in rows) and any(j.get("hook") == "task-dir-watch"
               and j.get("decision") == "block" for j in rows),
               f"watch records: {[(j.get('decision'), j.get('path')) for j in rows if j.get('hook') == 'task-dir-watch']}")
    return run.finish()


def _injection(ev: "list[dict]", marker: str) -> "dict | None":
    """The task gate's PreToolUse answer for the Bash call whose command holds `marker` (its updatedInput)."""
    for x in hook_responses(ev, "PreToolUse"):
        out = str(x.get("stdout") or x.get("output") or "")
        if str(x.get("hook_name", "")).startswith("PreToolUse:Bash") and "updatedInput" in out and marker in out:
            try:
                return json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]
            except (ValueError, KeyError, IndexError, TypeError):
                return None
    return None


def session_id_no_allow(out: Path) -> int:
    """Task 301 (audit 3's G5): the session-id injection approves only a plain call of the project's CLI. In default
    permission mode with no allow rule — `-p` can ask no one, so a call that needs a prompt is refused — a plain
    `.claude/bin/tasks status` runs on the hook's `allow`, and a `touch` chained to a `tasks` call is refused: the hook
    gave it no decision, so Claude Code's own check ran. With permissions skipped, the chained call runs with the
    prefix the hook returned (Claude Code applied an updatedInput that carried no decision)."""
    run = Run("PB-SESSION-ID-NO-ALLOW", "the session-id injection approves only the project's own CLI", out,
              ["claude"])
    p = session_project(tempdir("pb301-ask-") / "proj")
    _r, ev = claude_session(run, "default permission mode, no allow rule: a plain call, then a chained one", p,
                            RULES + ("1. Run exactly this shell command: .claude/bin/tasks status\n"
                                     "2. Run exactly this shell command: touch ran-chained.txt && .claude/bin/tasks status\n"
                                     "Then reply DONE.\n"), skip_permissions=False, extra=("--permission-mode", "default"))
    plain, chained = _injection(ev, "; .claude/bin/tasks status\""), _injection(ev, "touch ran-chained.txt")
    results = tool_results(ev)
    run.clause("the plain call is approved by the hook and runs (its result is the CLI's, not a permission refusal)",
               bool(plain) and plain.get("permissionDecision") == "allow" and bool(results)
               and "permission" not in results[0].lower(),
               f"hook answer: {json.dumps(plain)[:300]}; first result: {[r[:200] for r in results[:1]]}")
    run.clause("the chained call gets the prefix and no decision from the hook",
               bool(chained) and "permissionDecision" not in chained
               and str((chained.get("updatedInput") or {}).get("command", "")).startswith("export PLAYBOOK_SESSION_ID="),
               f"hook answer: {json.dumps(chained)[:300]}")
    run.clause("Claude Code's own check refuses it (nothing could approve it): the chained `touch` never ran",
               not (p / "ran-chained.txt").exists(), f"ran-chained.txt exists: {(p / 'ran-chained.txt').exists()}; "
               f"results: {[r[:160] for r in results]}")
    q = session_project(tempdir("pb301-skip-") / "proj")
    cap = _capture_hooks(q)
    _r2, ev2 = claude_session(run, "permissions skipped: the chained call", q, RULES + (
        "1. Run exactly this shell command: touch ran-skip.txt && .claude/bin/tasks status\nThen reply DONE.\n"))
    chained2 = _injection(ev2, "touch ran-skip.txt")
    # what ran: Claude Code reports the input it ran in the PostToolUse payload (task 221 W5, measured live; the shell
    # log leaves the prefix out by design, PB-SHELL-ATTRIBUTION)
    ran = [str((x.get("tool_input") or {}).get("command", "")) for _f, _b, x in _payloads(cap)
           if x.get("hook_event_name") == "PostToolUse" and "ran-skip" in json.dumps(x)]
    run.clause("with permissions skipped the chained call runs with the hook's prefix (the updatedInput with no "
               "decision was applied: the PostToolUse payload reports the prefixed command)",
               (q / "ran-skip.txt").exists() and bool(chained2) and "permissionDecision" not in chained2
               and any(c.startswith("export PLAYBOOK_SESSION_ID=") and "touch ran-skip.txt" in c for c in ran),
               f"ran-skip.txt exists: {(q / 'ran-skip.txt').exists()}; hook answer: {json.dumps(chained2)[:200]}; "
               f"PostToolUse commands: {[c[:200] for c in ran]}")
    return run.finish()


ROWS = {
    "PB-SESSION-ID-NO-ALLOW": session_id_no_allow,
    "PB-TASK-DIR-WATCH": task_dir_watch,
    "PB-PAYLOAD-NORMALIZE": payload_normalize,
    "PB-PROVIDER-CAPABILITY-DEGRADE": capability_degrade,
    "PB-SLASH-COMMANDS": slash_commands,
    "PB-MERGE-PUSH-GATE": merge_push_gate,
}
