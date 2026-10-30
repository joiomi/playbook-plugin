# Configuration

Two JSON files, both under `.agent/` in your project and hand-editable: `config.json` is created by `/playbook:init` (which also seeds its `verify` command after confirming it with you); `models.json` is created by `/playbook:init` when you pick a panel other than the shipped all-Claude default — or any time via `tasks models select` / `tasks models set` (or by hand). `models.json` holds machine-specific judge pins and is gitignored by design.

`config.json` carries two kinds of setting, and the difference decides whether you commit it:

- **Review knobs** (`judge_budget_usd`, `review_timeout_secs`, `review_soft_timeout_secs`) are naturally per-install — a spend cap is a wallet decision and a timeout depends on the machine. Committing them just sets a default your teammates can override through the env tier — upward, in the timeout's case (see the floor rule below).
- **Project policy** (`merge_verify`) only works when it *is* committed: the merge skill reads it to decide whether a merge may auto-push, so every clone has to see the same declaration. A repo that leaves `config.json` untracked leaves that check permanently skipped.

## `.agent/config.json` — review knobs

```json
{
  "judge_budget_usd": 10,
  "review_timeout_secs": 1200,
  "review_soft_timeout_secs": 900
}
```

- `judge_budget_usd` — spend cap for the **claude** judge (`--max-budget-usd`). Default 10. Claude-only; codex/agy/grok/pi have no budget knob.
- `review_timeout_secs` — the **hard** timeout for every review agent (plan / impl / panel): hang safety only. On expiry the whole process tree is terminated, the judge's partial output is salvaged to `judge-*.partial.log` — every cut run is kept there, the newest first, as the main log keeps every run (task 312); a partial log whose earlier runs cannot be read is left as it is, and the new run goes to a sibling `judge-*.partial.<UTC>-<pid>.log` that the message names — and the prior review log is left untouched. Default 1200 — the 900s soft deadline plus five minutes of grace, so a judge winding down on schedule is never cut off mid-sentence. Set it to `0` or `"unlimited"` for **no wall-clock kill at all** — a judge that is still writing is then never cut off mid-response. (Other accepted unlimited spellings, as JSON *strings*: `"none"`, `"null"`, `"inf"`, `"infinite"`. A bare JSON `null` means "not set" and falls through to the default, as it does for every other key — use `0` if you mean unlimited.)
- `review_soft_timeout_secs` — the **soft** deadline, default 900. Keep it below the hard timeout; if it is higher it gets clamped down and warns on every review. This is not a kill; it is the number the judge is *told* about, instructing it to finish the thought it is in, then write its findings, and not open a new investigation branch. Set it to `0`/`"unlimited"` to drop the time paragraph from judge prompts entirely. If soft would exceed a finite hard, it is clamped down to hard so the prompt never promises more time than the process gets.

  **The one exception: the antigravity (`agy`) judge**, panel seat and single judge (task 261). agy has no clock, so the prompt's soft deadline did nothing for it. Between 2026-10-18 and the evening of 10-20, 6 of its 38 runs reached the hard limit, each holding its panel 20 minutes and losing its vote; none had before. For agy, when the soft deadline sits at least 90 s below a finite hard one, it is a cut followed by a resume:
  - its call gets `--print-timeout <soft>`;
  - a turn cut there is resumed in the same conversation (`agy --conversation <id>`), told that time is up, to call no tool and to write its findings now in the review's format, from everything it has read;
  - the resume runs under the time left before the hard limit, if at least 60 s are left;
  - the review it writes counts as the seat's answer, its usage counted once (agy's resumed result holds the conversation's running total). A `**[playbook] Soft deadline:**` line says so, written by playbook apart from the judge's text (since task 264): unquoted in the seat's block in judge.md, above the judge's quoted review; for the single judge, at the head of its log (`[soft deadline] …`) and as the CLI's own line before the findings. A judge that writes the same words stays inside its quoted text;
  - the seat's spend record says `soft_cut: resumed`.

  A cut turn in which agy stated an error (a non-zero exit, a result that is not SUCCESS, an `error:` or `AGY_ERROR:` line, a response step in ERROR) is not resumed: it times out, as the same output does without the cut. A resume that fails saying why fails the seat with agy's words. A resume that times out, or fails saying nothing, makes the seat time out as before. Its record then says `soft_cut: timed-out`, keeping the cut turn's usage, and its marker says where it stopped (`at the soft deadline … and could not be resumed` when it was never resumed, `after hard` when the resume ran out) and names the conversation, which a person can resume by hand. The single judge says the same: a seat stopped at its soft deadline and not resumed never says the hard timeout fired, in its message, its run's part of the partial log (above the earlier runs kept since task 312) or its record (task 264).

  The cost: an agy run that would have finished between soft and hard on its own is cut too. Counted on one window, up to the evening of 2026-10-20 (20:00Z), that was 1 of 126 answered runs since 10-14 (1,136 s), and 0 of the 32 since 10-18, the longest of which took 769 s.

The hard number is a ceiling on the **judge subprocess**, not a guarantee about the whole command: after a kill the runner spends up to 5s reaping, and the antigravity judge (panel seat and single judge) is deliberately given its own `--print-timeout` + 30 s, so agy can hand back its partial text at that limit instead of being SIGKILLed: it then exits 0, and the runner records the seat as timed out, or resumes it after a soft-deadline cut (above). With the resume, agy's two calls still end by the hard limit + 30 s, because the resume gets the time left as measured. Budget ~1230s worst case rather than exactly 1200.

Two numbers rather than one because they answer different questions: soft is *when should the judge wrap up*, hard is *when is it obviously stuck*. Leaving only a hard kill means a judge doing good work gets truncated mid-sentence; leaving only a soft target means a genuinely hung process runs forever.

**Precedence, highest first:** CLI flag (`--budget`, `--timeout`, `--soft-timeout` on `plan-review` / `impl-review` / `panel-review`) → env var (`PLAYBOOK_JUDGE_BUDGET_USD`, `PLAYBOOK_REVIEW_TIMEOUT_SECS`, `PLAYBOOK_REVIEW_SOFT_TIMEOUT_SECS`) → `.agent/config.json` → built-in default. A missing file or malformed value falls back to the default (surfaced by `tasks doctor`, never fatal).

**One exception — `review_timeout_secs` in `config.json` is a floor, not just a tier.** When the file sets it, the CLI and env tiers may only *raise* the hard timeout, never lower it; a lower value is clamped up and a warning is printed. When the file sets it to unlimited, a finite `--timeout` is ignored outright. The reason is that `--timeout` is often passed by an agent, and an install that deliberately removed its kill window should not have one reintroduced by a subprocess argument. Nothing is floored when the file does not set the key — then ordinary precedence applies and `--timeout 60` means 60. The soft deadline is never floored; that is what `--soft-timeout` is for.

## `.agent/config.json` — judge & close knobs

Three more `.agent/config.json` keys tune the judge panel and the close-time verify run:

```json
{
  "panel_quorum": "majority",
  "judge_verify": ["python3 -m pytest -q"],
  "verify_timeout_secs": 1200,
  "review_context_chars": 100000,
  "review_context_chars_stdin": 200000
}
```

- `panel_quorum` — the minimum number of succeeding judges for a `panel-review` to **PASS**. Accepted values: `"majority"` (the default — `requested // 2 + 1`, a strict majority of the seats the panel was *asked* for: the configured panel, or the `--models` selection, counted before any seat is skipped for a missing CLI or an outage — so a panel with seats missing cannot quietly lower its own bar; when fewer seats can run than the quorum, the panel fails before any judge is called), `"all"` (every requested seat must pass), a positive integer (an absolute count), or a float in `(0, 1]` (that fraction of the requested seats, rounded up). Until 1.5.47 the count was the judges that *launched*, which let three skipped seats of six drop the default quorum from four to two. A bool, a value `≤ 0`, or an out-of-range fraction is **invalid**; an invalid value at any tier warns once and **falls through to the next tier** — env → config → the built-in `"majority"` — so a bad env var does not mask a valid `config.json` value, and `"majority"` applies only when no tier is valid. **Precedence, highest first:** `PLAYBOOK_PANEL_QUORUM` env → `.agent/config.json` `panel_quorum` → the built-in `"majority"`.
- `judge_verify` — a list of shell command strings the project declares safe for a judge to run inside its **read-only sandbox** while checking a specific suspicion. **This is prompt guidance, not an enforced execution engine, and whether a judge can actually run the commands depends on its seat's tools:** Claude judges — the panel's seats and, since 1.6.2 (task 200), the single judge — are invoked with `--tools Read,Glob,Grep` (plus `WebSearch` where a review enables it) and **cannot execute shell commands at all** (the clause tells them so) — for them the clause is advisory context; codex/grok seats decide for themselves. Only the **first six** declared commands are surfaced to the judge prompt (declare the ones that matter first). Declare only commands that never write inside the repo — redirect caches elsewhere, use a unique temp dir, keep them parallel-safe — because judges run concurrently under the sandbox. Absent or empty (the default) means no execution clause is added at all. A non-list value, or non-string / blank entries, are ignored.
- `verify_timeout_secs` — the **hard** wall-clock ceiling, in seconds, for **one** declared `verify` command at close (`tasks work done`): on expiry that command is killed and the close is blocked. Default 1200. Set it to `0` or `"unlimited"` (also the JSON strings `"none"` / `"null"` / `"inf"` / `"infinite"`) for **no ceiling** — this knob exists because a verify command with no ceiling can hang `tasks work done` forever, which in headless use is a silent deadlock. **Precedence, highest first:** `PLAYBOOK_VERIFY_TIMEOUT_SECS` env → `.agent/config.json` `verify_timeout_secs` → the 1200 default. This is distinct from `review_timeout_secs`, which bounds the *judge* subprocess, not the verify command.
- `review_context_chars` / `review_context_chars_stdin` — the per-transport character budget for the task context handed to a review judge. Two keys because the two transports differ: `review_context_chars_stdin` (default **200000**) applies to stdin-fed seats (claude, codex, and the experimental agy judge), which have no OS argv limit so their ceiling is model attention; `review_context_chars` (default **100000**) applies to argv-fed seats (grok and the experimental pi), which stay under the byte-guarded argv bound. Raising a budget past what the transport can carry is reported in the review receipts. A task.md over its share of the budget is cut in this order (task 212): the template's own commentary (its `> ` lines — a task.md made before task 213 carries them, a newer one only its two pointer lines; never inside a code fence, never a field value), the standing orders and every audit receipt but the newest; then the sections outside the kept set, newest first, the template's tail sections (Standing Orders, Pre-Panel Audit, Verification Receipt) last — the kept set being the title, Status, Risk, Intent, Why, Design, Handoff, Owner rulings, the Work Plan (a light task's Work; a retro record's phases and findings), the review sections and pinned ones; a dropped section is named in the gap's marker with its gate count (five names at most) — the other sections only when the kept set fits whole; if it alone does not fit, each step takes the least cut that fits: its gate notes (each keeps its title — at most 120 characters, a template gate's too — the start of its note, its indented lines joined in, and a pointer to its line in task.md), then the notes down to the gate titles, then the prose of its unpinned sections, then of pinned ones, then of Owner rulings — a review section and the rulings keep their newest end; Status and Risk keep their value line, a cut that would save less than its own marker is not made, and no gate line is dropped; only headings and gate titles that alone exceed the room are cut hard, inside the budget, with a WARNING. The receipt names each step that fired, and the judge's prompt says what was not sent, cut and dropped, and where the full file is. **Precedence, highest first, per key:** `PLAYBOOK_REVIEW_CONTEXT_CHARS` / `PLAYBOOK_REVIEW_CONTEXT_CHARS_STDIN` env → `.agent/config.json` → the default.

## `.agent/config.json` — `tasks red` (the red receipt)

Two keys tune `tasks red` (task 280, PHASE-B.md item 9b; `docs/cli.md` has the command). `red_tests` and `red_links` went with task 342: no copy of the base is made, the rows run in the project as it is.

```json
{
  "red_run": "python3 -m pytest {test}",
  "red_timeout": 300
}
```

- `red_run` — the command a `## Test contract` row's **test id** runs through: a string holding `{test}`, replaced by the id (a backticked span with no white space; a span with white space is run as a command itself). Absent, or without `{test}`, a row that names an id is recorded `unrunnable` ("no red_run"); command rows still run. It runs with the project root as its working directory — a `code_roots` project's command starts there (`cd playbook-plugin && python3 -m unittest {test}` in this repository).
- `red_timeout` — seconds one row's command may run (default 300); past it the row is `timeout`, never red. A non-number or a value ≤ 0 means the default.

## `.agent/config.json` — `merge_verify` (project policy)

The `/playbook:merge` skill always verifies *the merge itself*: mind-map integrity, per-user contamination, and that the merge introduced no code of its own. Whether your *branches* are healthy is a different question, and only your project knows what answering it looks like — so you declare the command:

```json
{
  "merge_verify": {
    "command": "pnpm -r typecheck && pnpm -r test && pytest"
  }
}
```

- `command` — whatever "green" means for **this** repo. Point it at your full gate, not one layer's: a merge that runs only the backend suite can certify itself while the frontend is red.

Where and how it runs: from the repo root, on the **merged working tree after conflicts are resolved but before the merge commit is created** (so a command that inspects `git log`/`HEAD` sees the pre-merge target, not the merge). It runs via `bash` under `set -e -o pipefail`, so quoting is preserved, `&&` and multi-line commands work, and a failing *early* step fails the gate — without `set -e` bash reports only the last command's status, so `typecheck` failing followed by a successful `echo done` would report success. If your command writes to the tree (formatters, codegen), say so to yourself: the skill re-checks code identity afterwards, but a command that rewrites source during verification makes "the merge introduced no code" harder to assert.

Four outcomes, and the exit code is the verdict — only the first allows `--push`:

| Outcome | When | Effect on `--push` |
|---|---|---|
| **GREEN** (0) | declared command exited 0 | may auto-push |
| **FAILED** (1) | declared command exited non-zero | blocked |
| **BLOCKED** (2) | declared but unusable — malformed JSON, wrong shape, misspelled key | blocked |
| **SKIPPED** (3) | nothing declared: no file, no key, empty command | blocked; merge is presented for you to push by hand |
| **CONFIGURED** (4) | `--plan` only — a command exists but was deliberately not run | blocked (a classification is not a result) |

Two deliberate asymmetries:

- **Absent is not an error, but it is not a pass either.** With no `merge_verify`, the merge completes and reports honestly that no soundness command ran — then hands the push back to you rather than auto-pushing something nobody checked.
- **Broken is not the same as absent.** A misspelled `commnd` key blocks instead of silently skipping, because a typo must not quietly disable a gate you believe you declared. `tasks doctor` warns about these long before merge time.

Nothing is inferred on your behalf: if you declare no command, the skill will not guess one.

**Commit the file.** Nothing forces you to — `merge-verify.py` runs whatever `.agent/config.json` it finds on disk — but an untracked config means the gate exists only in your clone: your merges verify, everyone else's report SKIPPED. `tasks doctor` warns when a `merge_verify` is declared in a file git isn't tracking. And because the command is read from the *merged* tree, an incoming branch can change it — the same trust you already extend to running that branch's tests, but worth a `git diff "$target_before" -- .agent/config.json` when the branch isn't yours.

## `.agent/config.json` — `standing_gates` (project policy)

Gates your project wants on **every** task — a journal entry, a changelog line — declared once instead of hand-added (and hand-relocated) per task:

```json
{
  "standing_gates": [
    {"title": "Journal", "text": "Write journal/{{NNN}}.md — Shipped / Friction / Value / Honesty-check / One-change"},
    {"title": "Changelog", "text": "Add the user-visible change to CHANGELOG.md"}
  ]
}
```

Each entry becomes a `## <title>` section with a single `- [ ] <text>` gate, appended in declared order as the **final gates** of every generated task — base templates, `quick`, custom `.agent/playbooks/` templates, and stub expansion alike (a stub gains them when it expands at activation). `{{NNN}}` in title or text substitutes the zero-padded task number. The Stop hook enforces them like any other gate.

Rules, all loud: the key is **opt-in** (absent means task generation is byte-identical to before — `init` seeds none); title and text are collapsed to a single line so a config value can never mint a phantom section or gate; an entry that is malformed, or whose title collides with a section the task already has, is skipped with a printed warning — never silently written, never silently dropped. Like `merge_verify`, this is project policy: commit the file so every clone generates the same tasks.

## `.agent/config.json` — `code_roots` (project policy)

The tree-state fingerprint is computed from the OUTER git repository. Some
projects keep their real code in a **gitignored nested checkout** — this
workspace's `playbook-plugin/`, HowFar-v2's app repo — invisible to the outer
`git status`. A code-only edit inside such a nested repo therefore does not move
the outer fingerprint at all, so a post-panel change there reads silently
**FRESH** and the freshness gate never fires — the unsafe direction of the
guarantee. `code_roots` closes that blind spot:

```json
{
  "code_roots": ["playbook-plugin"]
}
```

Each entry also widens the **judge tamper guard** (v1.5.45): the review paths
snapshot each root's state and its resolved identity before spawning judges, so
an edit inside a nested root — or a symlink repoint to a same-`HEAD` clone —
during the review window is caught rather than silently baked into the stamp.

Each entry is a **project-relative path** to a nested git repository. When set,
the fingerprint additionally folds in each root's `HEAD`, porcelain status, and
working diff (including untracked-file content; since task 314 these git reads go
through a copy of the index with the assume-unchanged and skip-worktree bits cleared
— skip-worktree too, except on a file a sparse checkout leaves out (in cone mode,
one the cone does not hold; in the deprecated non-cone mode, any absent one) — so a
change (or a deletion) under either bit is seen, as is the panel's snapshot) — the same material the outer
tree already hashes — plus, since task 060, the root's **resolved identity**
(its real path relative to the project), so repointing a symlinked root at a
different checkout with the same `HEAD` also moves the fingerprint. A code-only edit inside a listed root moves the
fingerprint, so the freshness gate sees it.

Rules, all loud: the key is **opt-in** — with `code_roots` absent (or `[]`) the
fingerprint is **byte-identical** to before the key existed, so nothing changes
for projects that don't use it. Entries are sorted and de-duplicated so config
order can never perturb the hash. An entry that is not a string, is empty, is an
absolute path, or contains `..` traversal is skipped with a printed warning; a
symlinked entry that *resolves* outside the project is likewise skipped loudly
(the fingerprint is never steered to hash something outside the tree). Each root
must be its **own** git repository — a plain subdirectory of the outer repo, a
path that does not exist yet, or a repo git cannot read contributes a stable
`<absent>` marker rather than adopting the ancestor repo's identity or crashing
the fingerprint. Commit the file — the panel stamp and the close comparison must
agree across clones.

The same exclusion applies inside every nested root as in the outer tree: `.agent/` only (workflow bookkeeping is
not code). Task 343 removed the owner's `fingerprint_exclude` list: a file your tests or standing gates write into the
tree belongs in `.gitignore`, which every fingerprint already leaves out.

## `.agent/config.json` — `audit` (pre-panel sweeps)

`tasks audit` runs mechanical sweeps before a review so judges spend tokens on hard problems, not greppable ones. The `audit` key tunes them:

```json
{
  "audit": {
    "sweeps": [
      {"name": "no-print", "command": "! grep -rn 'console.log' src", "why": "stray debug logs", "severity": "advisory"}
    ],
    "disable_defaults": false,
    "mindmap_severity": "advisory",
    "node_freshness": true,
    "node_freshness_severity": "advisory",
    "dangling_links_severity": "advisory",
    "wellformed_severity": "advisory",
    "task_bloat_chars": 24000
  }
}
```

- `sweeps` — project-specific sweeps appended to the built-in safety set. Each is `{name, command, why, severity}`; a sweep's shell `command` exits 0 = findings, 1 = clean, ≥2 = error/did-not-run (never a pass). Malformed entries (missing `name`/`command`) are skipped rather than crashing the audit. `severity` is one of `error` / `advisory` / `info` (default `advisory`); only `error` findings fail the audit.
- `disable_defaults` — set `true` to drop the built-in default sweeps (conflict-markers, merge-artifacts, stale-markers) and run only your declared `sweeps`. Default `false`.
- `node_freshness` — set `false` to turn off the mind-map node-freshness sweep (which flags a node whose cited code changed after the node). Default on.
- `mindmap_severity`, `node_freshness_severity`, `dangling_links_severity`, `wellformed_severity` — per-sweep severity overrides for the advisory mind-map checks; each takes `error` / `advisory` / `info`. Raise one to `error` to make that drift fail the audit instead of merely reporting.
- `task_bloat_chars` — the byte threshold above which an open `task.md` is flagged as too large to review through one window (the sweep nudges the sanctioned `tasks compact`). Unset or `≤ 0` defaults to **half the argv `review_context_chars` budget** (≈50000 at the default), so it tracks your context budget automatically.

## `.agent/config.json` — `command_guard` (destructive-command interlock)

A PreToolUse hook (`command-guard-hook`) blocks unambiguous high-blast /
irreversible shell commands — `rm -rf` on a dangerous path, `git push --force`
(also `-f`, a `+` refspec such as `git push origin +main`, and one set by `-c remote.<name>.push=+…`),
`git reset --hard` (since task 346 a git long option is read shortened too, as git runs it — `reset --h` is `--hard`, `push --m` is `--mirror` —, and so is rm's: `rm --rec --forc /`), since task 346 the git and `find` forms that delete what this machine cannot bring back — a whole-tree discard (`git checkout -- .`, or with a pathspec that is `.`, a glob such as `'*.py'` or `'**'`, a magic `:/`, a path the rm rule calls dangerous, or `--pathspec-from-file`; before checkout's `--` only a `$(git rev-parse <rev>)` substitution reads as a commit, so `git checkout "$PWD"` and `git checkout $(git diff --name-only)` block; `git checkout -f`; `git restore .` unless it is `--staged` alone; `git switch --discard-changes` / `-f`), `git stash drop` / `clear`, `git branch -D` (or a delete with a force), a push that deletes the remote's refs (`git push --mirror` in any position or `-c remote.<name>.mirror=true`, `--delete` / `-d`, `git push origin :main` or `-c remote.<name>.push=:main`, `--prune`; a dry run blocks too, fail safe), `git remote add --mirror`, a lost recovery point (`git reflog expire` / `delete` / `drop`, `git gc --prune=now` / `all` or `-c gc.pruneExpire=now`, a `git prune` that is not `-n`; `git gc --prune` alone keeps two weeks and passes), and a `find` that deletes (`-delete`, or `-exec rm …` behind a wrapper too) under a root the rm rule calls dangerous (`find / -delete`, `find ~ …`, `find .git …`, an absolute root even when narrowed, as `rm -rf /tmp/x` is) or under the working directory however its expression narrows — no name test keeps `.git` and `.agent` safe (`-name config` breaks the repository, `-name '*.md'` deletes the task records, `-empty` the empty refs), so `find . -delete` and `find . -name '*.pyc' -delete` block and a cleanup names its subdirectory (`find src -name '*.pyc' -delete` passes) —, the command an `-exec` runs judged as a command (an `rm` or `unlink` behind a wrapper too, `env -S 'rm -rf'`), every `git clean` that is not a provable dry run — only `-n` / `--dry-run` with the flags
`f d x X q`, `-e` / `--exclude`, `--force`, `--quiet` and pathspecs pass, so `git clean --force -d`, an abbreviation
such as `--fo`, a pathspec without `-n`, `--no-dry-run` and config that drops git's force requirement are all
refused —, git config the guard cannot read on the command line (an alias defined with `-c alias.<name>=…`, an
include set with `-c include.path=…`, any `--config-env`, a `GIT_CONFIG_*` assignment anywhere on the line,
whatever the line runs — any program on it may run git with it; `GIT_CONFIG` anywhere in a word of the line, read
after its quotes and backslashes are removed and `$'…'` decoded, is refused, so `'GIT_CONFIG_COUNT'=1`,
`GIT_CONFIG_COUNT+=1`, `env -SGIT_CONFIG_COUNT=1 …` and even `echo GIT_CONFIG_COUNT` or `MY_GIT_CONFIG_COUNT=1` are;
a name built at runtime from pieces (`GIT_CONF${E}IG_COUNT`) is not seen, a stated limit), a `git config`
write of a key that makes a later command destructive (`remote.<name>.push` / `.mirror`, `alias.*`,
`clean.requireForce`, `include.path` / `includeIf.<cond>.path`) or a rename of a section into or out of one (`alias`,
`remote…`, `clean`, `include…`) — `git config` is read only through the options git lists for it, so an abbreviation
or an option the guard does not know refuses the command, and, as git does, its options end at the
first argument that is not one (`git config alias.c x --get` writes) —, a git option before the subcommand that the
guard does not know (`git-option-unreadable`: its arity is unknown, so it hides the subcommand; `--attr-source` is
read, and the options that print and exit, `--version` / `--help` / `--man-path` / `--info-path` / `--list-cmds`,
pass), `curl|sh` (and since task 345 a download a shell reads by substitution in any segment of the line, `true; bash <(curl …)`, or that becomes an interpreter's PROGRAM — `python3 <(curl …)`, `python3 -c "$(curl …)"`, `python3 - <<< "$(curl …)"` — while a download it reads as data passes: `python3 -m json.tool <(curl …)`; each interpreter's options are read by its own grammar — python's `-I` / `-E` are flags, perl's `-E` and node's `-p` take code, a cluster (`-Ic`) and an attached operand (`-c"…"`, `--eval=…`) count, every perl / ruby `-e` is read, and a LOAD option's operand is the program too (node / ruby `-r`, node `--import`, `--loader`, `--experimental-loader`), and a cluster of code letters (`node -pe`) takes the next word; perl's `-I` takes a separate value and `-d` one only after `:` — and a redirection however it is spelled (`<<<"…"`, `0< <(…)`, `2>/dev/null`, even between a code option and its operand), with `/dev/stdin` and `/dev/fd/0` read as stdin, a redirection glued to the placeholder (`<(curl …)>/dev/null`) and a downloader found behind a wrapper (`<(env curl …)`); a continued line and a substitution that spans lines are read as one command, and the text as it stands too (an escaped backslash, `\\`, ends no line), and each line on its own; a substitution inside double quotes has quotes of its own (`"$(tr -d '"' < v)"`, and the backtick form); a bare `( … )` subshell is read segment by segment; a versioned name (`python3.12`, `perl5.36`) is its interpreter, in the pipe rule too),
`dd`/`mkfs` to a device, a DB `DROP`/`TRUNCATE` — before they run, because the sandbox contains filesystem
blast radius but not outward/logical irreversibility, and judgment alone is not a
guarantee. Git config held elsewhere — `~/.gitconfig`, the repository's config, an alias defined there — is not
read by the guard (a stated limit). It is **conservative** (matches only at a command position, so an
`echo`/`grep` of dangerous text is fine for the command-position rules; for the
two WHOLE-command rules (pipe-to-shell, DB drop) only these inert data regions
are skipped (task 073): a `cat`/`tee` heredoc body written to a plain file
(quoted tag, or no `$(…)`/backtick/process-substitution expansion in the body),
and an `echo`/`printf` line redirected to a plain file with no `|` or expansion
anywhere on the line — a heredoc fed to `bash`/`sh`/`psql` (or an unquoted one to
`python3`), `tee >(sh)`, an `echo … | sh`, an expansion, an unterminated heredoc or a
`<<` inside a string are all still seen. Since task 077 a pipe inside a quoted string
is data, so `echo "curl … | sh"` passes. Since 1.6.2 (task 199), in a plain command,
the body of a QUOTED heredoc that a data reader takes — `cat`, `tee`, `git commit`, a
python reading its program from stdin (`python3 - <<'PY'`) — is data too; one that
another program takes (`node - <<'JS'`) keeps meeting the rules. The command-position
rules are line-split and never masked: a heredoc body LINE that is itself
`rm -rf /` still blocks. A relative `rm -rf ./build` is fine;
`--force-with-lease` is allowed), and it **fails OPEN** on any internal error so
it can never wedge a session.

- **Acknowledge** a command you've confirmed: run it inside a task classified
  `## Risk: irreversible` with a rollback plan (the interlock reads the ACTIVE
  task's live risk and stands down for it, logging an `allow` — this is the
  in-session path; implemented in task 073, it had been documented but absent),
  or have the OPERATOR set
  `PLAYBOOK_ALLOW_DANGEROUS=1` in the environment the hook runs in (the shell that
  started the agent, or the harness's env settings). The hook reads its own
  environment: a `PLAYBOOK_ALLOW_DANGEROUS=1 <cmd>` prefix typed by the agent
  cannot set it (task 073). The task path reads the session the CLI and hooks
  resolve, so no `PLAYBOOK_SESSION_ID` is needed. It
  accepts the status every `tasks work N` activation leaves (`pending`) or
  `in_progress`. A `done` or `blocked` task left in the session pointer never
  acknowledges (task 110). Both paths journal
  `allow ack-irreversible-task:<rule>` or `allow ack-operator-env:<rule>`.
- **Wrappers are followed, with their options** (task 077). `sudo`, `doas`, `env`,
  `nice`, `ionice`, `chrt`, `stdbuf`, `timeout`, `setsid`, `nohup`, `time`,
  `xargs`, `command`, `builtin`, `exec` each declare the option arity they really
  have, so `sudo -u root rm -rf /`, `timeout 5 rm -rf /`, `chrt -f 99 rm -rf /`,
  `stdbuf -o0 git push --force`, `env -S 'rm -rf /'` and `curl … | nice -n 19 bash`
  are classified like their bare forms. A wrapper's **query mode runs nothing** and
  stays allowed: `sudo --version rm -rf /`, `sudo -l …`, `command -v …`,
  `timeout --help …`. An option the table does not know that takes a value hides
  what follows it — the guard under-blocks there rather than risk blocking a safe
  command, and that bound is deliberate.
- **What the interlock does NOT cover** (task 077, measured and pinned by tests rather than left implied). A command name that only exists after the shell evaluates something — an `alias`, `${X:-rm}`, `$(echo rm)`, or a glob such as `/bin/r?` that matches a binary on disk — cannot be resolved by a static classifier; the OS sandbox is the layer that covers those. And the guard answers *where is the command and is it one of the enumerated dangerous forms*, not *is this act destructive*: an interpreter running a deletion inside its own language (`perl -e 'system(…)'`), a remote `ssh host …`, a container bind mount, `rsync --delete` and `shred` are all allowed (`find … -delete` is in scope since task 346, above). So is a target that only arrives at run time — on stdin to `xargs` (`echo / | xargs rm -rf`) or from a file into `parallel` (`::::`, `-a FILE`): the classifier sees the command, not the data it will be fed (measured in task 085). Literal `:::` arguments are known, so `parallel 'rm -rf {}' ::: /` blocks (task 110). Task 345's stated limits: a command a wrapper re-quotes (`env -S 'rm -rf' "$DEST"/*` — its glob reads as quoted), `"$X"/.git` for a variable that is not one of the project's names, the interpreters `bun`, `deno` and `php` (not listed), and a download in an UNQUOTED heredoc's body that an interpreter runs as its program (`python3 - <<EOF` with `$(curl …)` inside: the body is not read as the program); a heredoc with an UNQUOTED tag whose text holds a dangerous form inside escaped backticks is read as a command (fail safe, as before — quote the tag, `<<'EOF'`), and so is a quoted heredoc's text in a compound line when it holds an unbalanced substitution opener (seen once in 81,231 real commands: write such text with a file tool). Task 346's stated limits: `git rm -f`, `git update-ref -d`, `git branch -M` / `-C`, `git checkout -B` / `git switch -C`, `git worktree remove --force`, history rewriting (`git filter-branch`, `git rebase`), `git gc --prune=<a near date>`, a `find` whose targets come from `xargs`, `fd`, and a `find` whose `-exec` runs a shell that deletes what find passes it (`sh -c 'rm -rf "$1"'`). Adding one of those is a new rule family with its own false-positive profile — a scope decision, not a bug fix. Use `dangerous_commands` if your project wants one of them today.
- **What quoting means for a target** (task 077): the shell expands `$` inside double quotes but not single ones, and expands a glob or a `~` inside neither — so `rm -rf "$HOME"` blocks while `rm -rf 'build*'` is a literal file name and passes. A glob OUTSIDE the quotes is read wherever it stands (task 345, G4): `rm -rf "$DEST"/*` with `DEST` empty is `rm -rf /*`, so it blocks, as do `"foo"/*`, `'$D'/*` and `~"/build"`; an escaped glob (`"foo"/\*`) is a file name, and the quoted `"${DEST:?}"/*` — which aborts on an empty `DEST` — passes when `${DEST:?}` is the token's only expansion, names no project variable and climbs nowhere (the unquoted `${DEST:?}/*` blocks, as before; `"${PWD:?}"/*` is the project's tree and blocks; a `*` inside a `${…}` removal pattern, `${f%.*}`, is no glob, while a default, assign or alternate word, and a replacement, is read as the target it expands to, a subscript's too and up to its own closing brace (`${f:-"${X}"*}`): `"$D"/${f:-*}`, `"$D"/${f:-~}`, `"$D"/${f/a/*}` and `"$D"/${f[0]:-*}` block — fail safe, so a star such a word quotes (`${f:-"*"}`) and a nested default with a path (`"${X:-${PWD}}/build"`) block too). The project's own `.git` and `.agent` are dangerous targets, read normalised (`./.git`, `.git/.`, `x/../.git`); a path under them is not. Brace alternatives are expanded before the test, ANSI-C `$'\x72\x6d'` escapes are decoded, an unquoted `#` starts a comment, and nesting past the classifier's limit REFUSES rather than allowing. An argv-delivered command still gets the DB-client rule and your `dangerous_commands` patterns; only the substitution scan is skipped for it, because word splitting has already happened.
- **Shell syntax the classifier now reads** (task 077): line continuations, ANSI-C `$'…'` quoting, `+=` assignment prefixes, the reserved words `if`/`while`/`until`, process substitution `<( … )`, herestrings `<<<`, file-descriptor redirects such as `1>`, git short option clusters (`push -qf`), and the privilege wrappers `su -c`, `pkexec` and `runuser`. A recursive-force `rm` whose target RUNS something (`$(pwd)`, a backtick) or names a root the user cannot afford to lose counts as dangerous: `$HOME` and its kin, and since task 345 the names that hold the project or the working directory — `$PWD`, `$OLDPWD`, `$CLAUDE_PROJECT_DIR` — as the target itself, its `.git` / `.agent`, or a climb out with `..`, in any spelling of the parameter — `${PWD:?}`, `${PWD:-.}`, a substring `${PWD:0}`, a subscript `${PWD[0]}`, a nested default `${X:-${PWD}}` — but `${PWD:+word}`, which expands to the word, judged as a target of its own (`"${PWD:+/}"` blocks, `"${PWD:+build}"` passes). Any parameter's default, assign or alternate word is a value the target may take, judged as a target of its own wherever it stands: `"${X:-${PWD}}"`, `"${X:-/}"` and `"./${X:-.git}"` block, and an absolute default, `"${TMPDIR:-/tmp}/job"`, blocks as `rm -rf /tmp/job` does (none in 81,231 real commands). `"$PWD/build"` is `./build` and passes; a name is literal only where its `$` stands in single quotes (`'$PWD'` passes; `$PWD'/'` and `''"$PWD"` block), and a longer name, `${PWD_TMP}`, is another variable. A plain variable is allowed — `rm -rf "$WORK"`, the temp-dir cleanup idiom (task 077, measured): its value is out of a text guard's reach, a stated limit. `sh -c` takes exactly one script operand, so a trailing argv0 no longer hides it. **Echoing dangerous text really is fine now**: a pipe inside a quoted string is data, while an unquoted pipe that feeds a shell still blocks.
- **Privilege-wrapper options that take a value** (task 085). `su` and `runuser` read `-s`/`--shell` and `-w`/`--whitelist-environment` as options with a value, and a bare `-` as the login flag, so `su - root -c '…'`, `su -s /bin/sh root -c '…'` and `runuser -w PATH root -c '…'` are classified like `su root -c '…'`. After `su -- root` (or `su root --`) the remaining arguments go to the user's shell, so a `-c '…'` there is classified too; `runuser -u USER -- CMD` runs CMD directly and is walked as a command. `parallel` and `watch` join every word of their command template, quoted or not, as the tools do, so `parallel 'rm' '-rf' '/' ::: 1` and `watch echo ';' rm -rf /` are classified like the line the tool hands to its shell. Since task 110 the values after `:::` are composed into each job, as `parallel` does: appended to the template, or substituted whole for `{}`, `{1}` or an `-I` string. Several sources make their product of distinct values and every job is judged; past 512 job lines the invocation is refused (`parallel-too-many-jobs`) rather than sampled. An empty template runs each argument as a command. The template is judged dequoted and with its quoting kept, so `parallel bash -c 'rm -rf /' ::: a` blocks. Options are read from GNU parallel's own spec; where it leaves the grammar open (an optional value such as `--replace`, an option the table does not know) every reading is judged, so a newer option can only make the guard stricter. GNU parallel can also REWRITE an argument before it reaches the job line, and the guard does not model that grammar (owner decision, task 110): an option that splits an argument into columns or records (`--colsep`, `--csv`, `--header`, `--delimiter`), packs several arguments into one job line (`-n`, `-N`, `-L`, `-X`, `-m`) or switches on further replacement strings (`--plus`); a replacement string that rewrites its argument (`{.}`, `{/}`, `{//}`, a `{= … =}` expression); and an option that renames the source separator or declares a replacement string of its own (`--arg-sep`, `--rpl`, `--er` and their family), after which the template cannot be located. The full lists are tables in `command_guard.py`. With any of them the arguments are unknown, so the guard judges the template by its command NAMES alone and refuses (`parallel-unmodelled-arguments`) when a name is one it judges by its arguments (`rm`, `git`, `dd`, a shell, a database client) or cannot be read at all (no template, a replacement string where the name should be, a hidden template). `parallel --colsep , echo {1} {2} ::: a,b` passes; the same line with `rm` for `echo` is refused whatever its arguments are, and needs the acknowledgement. An interpreter or `source` in such a template is refused only after a download earlier in the same command (`download-then-run`): its operand may then be the downloaded file. Two declared over-blocks: linked sources (`:::+`) are counted as a full product, so a linked invocation with few jobs can be refused at the job cap; and an optional-value option written bare (`-l`, `--max-lines`) is read both ways, so it is refused when the next word is the command's name (write the value). So `parallel rm -rf ::: /etc /usr` and `parallel ::: 'rm -rf /'` block, while `parallel gzip ::: *.log` passes. ANSI-C quoting decodes bash's eight-digit `\U` escape too.
- **Quoting is seen in the ARGUMENTS too** (task 077). Flags and targets are read from the same dequoted tokens as the command name, so a quoted root target or a quoted `--force` is classified like its bare form, while the benign twins (`"./build"`, `"--force-with-lease"`) still pass. Command separators are recognised outside quotes only: an operator inside an option value no longer splits the command, and an operator inside an echoed string is data. An argv list from a provider is classified as ONE invocation, so a commit message that merely mentions a dangerous command is not blocked.
- **Quoting and naming are seen** (task 077). The classifier lexes the command quote- and escape-aware, and resolves the command NAME through quotes, a leading backslash, a directory, `~/` and `$VAR/` — so `sudo -p 'Password: ' rm -rf /`, `'rm' -rf /`, `~/rm -rf /` and `/bin/bash -c '...'` are classified like their plain forms. A quoted string containing spaces is never treated as a command name, which is what keeps a source line full of such strings from blocking. `git`'s global options (`-C`, `-c`, `--git-dir=` ...) no longer hide its subcommand.
- **A command substitution is a command** (task 077). The body of `$( ... )` and of a backtick form is classified in its own right, because it runs whatever surrounds it. A single `&` separates commands like `;` does, and `|&` or a grouped `(bash)` reach the same interpreter as `| bash`. Inert DATA is still inert: a quoted `cat`/`tee` heredoc written to a plain file is not scanned, which is how you write a fixture ABOUT a dangerous command. Since 1.6.2 (task 199) a substitution inside single quotes or `$'…'` — which bash does not expand — is taken for the text it is, but only in a plain command of data commands (`echo`, `printf` without `-v`, `git commit` with a message, `eval` at a pipeline's head, filters such as `cat`, `tee`, `grep`): `echo 'never $(rm -rf /)'` passes. Anywhere else — an assignment (`x='$(…)'`), `declare`, `read`, a pipe into a shell — it is still read quote-blind and blocks, the older accepted cost. The double-quoted form, which bash runs, always blocks.
- **Download-then-run blocks within one command line** (task 110). A downloader that writes a file
  (`curl -o`/`-O`/`>`, `wget`'s default name or `-O`) followed by a later segment that runs it
  (`sh x.sh`, `./install`, `/tmp/x.sh`, `python3 x.py`, `source x`, `bash < x.sh`, or the same inside
  `bash -c '…'`, `eval` or a wrapper) is refused, like a downloader piped into a shell. It shipped after it refused none of 282 downloader commands in the owner's shell history.
  A download in an EARLIER tool call is not seen, since the guard keeps no state between calls,
  and neither is one inside `$( … )`. Two more declared bounds (task 110, impl panel round 2, the owner's
  decision): a download made INSIDE what a segment delegates to (a `sh -c` body, an `eval` or wrapper
  payload) is not carried out to the later segments, and the saved file's name is read only from an output
  option or from an operand that carries a URL scheme. This rule is a heuristic over one command's text: it
  narrows a common mistake and is not a containment layer. For unreviewed remote code the real boundary is
  the OS sandbox. Reading the download (`jq . x.json`, `tar xf x.tgz`) is fine.
- **A generic pipe into a shell is NOT blocked** — `cat evil.sh | sh` is allowed;
  only a *downloader* piped into an interpreter (`curl`/`wget`/`fetch`/`aria2c`)
  blocks. Remote code is unreviewed and unambiguous; a local file is not, and the
  guard cannot read it. Turn the stricter rule on per project, no release needed:
  ```json
  {"dangerous_commands": ["\\|\\s*(?:sudo\\s+|doas\\s+)?(?:sh|bash|zsh|ksh|dash)\\s*$"]}
  ```
  That blocks `cat x | sh` and `cat x | sudo bash` while leaving `sh script.sh`
  and `cat x | bash script.sh` alone (a shell with a script operand is not reading
  the pipe). Verified against those four forms.
- **Extend** with project-specific patterns (e.g. a deploy/publish command):
  ```json
  {"dangerous_commands": ["^fly deploy\\b", "npm publish"]}
  ```
  Entries are case-insensitive regexes matched against the whole command.
- **Disable** entirely: `{"command_guard": false}`.

It's a safety interlock against the agent's *mistake*, not an adversary — for
adversarial containment run the agent in the sandbox (OS-level). Like the rest
of the hook enforcement, it is a **supported** interlock only on Claude (owner
decision 2026-08-21); the grok and codex command-guard paths ship but are
experimental, and antigravity/pi are experimental everywhere. See the
[provider support matrix](providers.md#support-matrix).

### An unset `## Risk` is held to the high-consequence bar

`panel_required_for` keys the review requirement to the risk class, so an
`unclassified` task used to fall through it entirely and close on a warning —
making a blank field the cheapest path through the strictest gate in the system,
chosen by the same agent the gate constrains. Since 1.5.32 an unset field
(or a malformed one, e.g. the one-line `## Risk: assertive`) blocks `tasks work
done` unless there is impl-review evidence or `--force --reason`, exactly as for
`assertive` / `irreversible`. Since task 294 a task with no `## Risk` heading at all is held to the same bar: it
used to close on a warning, a path kept for pre-1.5.0 tasks, so deleting the
heading dropped the review — and a fresh project has no such task. The bar is the
strictest class's: when `panel_required_for` lists `assertive` or `irreversible`, an
unclassified task needs the panel too, so not classifying never lowers an assertive
task's bar to a single judge. A close that clears the bar another way still prints
the warning naming what was skipped.

Review evidence is a record the CLI wrote: judge.md whose newest round is an impl
round with verdict PASS, or judge-single.md's `## Single-judge impl review`
heading. The CLI appends that heading to judge-single.md after every single-judge
impl review, also when the findings themselves go into task.md's
`## Implementation Review` — and, since task 304, the review's text under it, every
line quoted, so a later review that replaces task.md's block leaves each run's text on
record; only the heading counts (a plan review writes `## Single-judge plan review`,
which is no implementation evidence; if the append fails, the CLI warns and the
review does not count); a ticked gate is never review evidence (task 290): the
templates carry the gate that tells you to run the impl panel, and ticking it
used to count.

Setting `## Risk` to one word clears it. `## Risk Routing` in the light template
is a gate checklist, not the classification field, and does not trigger this.

### The freshness gate (FRESH / STALE)

The gate fires only when policy requires a panel for the close (`panel_required_for`). The close asks one question
(task 343, PLAN-PLAYBOOK-2.md 4.1 a): does judge.md's newest impl round still describe the code being closed?

- **FRESH** — the round's `**Tree-state:**` stamp equals the tree's fingerprint now, or only workflow records (`.agent` tasks, sessions, journal, chat log, rulings)
  were committed since (below), and the round's tamper guard was not degraded.
- **STALE** — otherwise, with its cause in words: the code (a doc, a test or the mind map too) changed since the round;
  the round has no tree stamp; git cannot fingerprint the tree now (a corrupt index, a missing `HEAD`, an
  unresolvable toplevel, a failed `status`/`diff` — never read as FRESH); or the round's tamper guard was degraded
  (it could not verify that no judge wrote the tree).

An unstamped round in a project that is not a git repository could never have been stamped: the close says "not
judged" and is not held. A stamped round whose repository git no longer finds reads STALE.

When the close is held to the high-consequence bar — `## Risk` is `assertive` or `irreversible`, **or it is left
unset/`unclassified`** (held to that same bar everywhere else; blocking only the two would make blanking `## Risk`
more lenient than classifying it) — and judge.md's newest round is an impl PASS that would carry it, a STALE round
blocks `tasks work done`, which names the cause and the ways out:

- another impl round: `tasks panel-review <N> --mode impl` — or, once the round cap below is reached, a round with
  the owner's word (`--owner-ok --reason "…"`);
- `tasks work done --force --reason "…"` — the blunt whole-policy override, its reason in the receipt.

A `reversible` risk stays advisory (a console note and the receipt clause), as does any close for which policy does
not require a panel. The receipt of every close with an impl round says FRESH or STALE and the cause. The gate's risk
is the stricter of `## Risk` and the risk recorded with the task's impl panels (their reservations in
`post-panel-reviews.jsonl`): lowering `## Risk` after a panel frees nothing, and a run ledger that exists and cannot
be read, or has a corrupt line, makes that recorded risk unknown — the close then takes `unclassified`, whatever
judge.md holds (a panel that reserved and saved no round has recorded its risk there), and every block it causes
names the ledger. Every single-judge run writes that ledger too, so a line torn by a crash holds even a
`reversible` task until the line is repaired or the close is forced.

Nothing reviews after the round on the close's behalf: the post-panel judge as close evidence, tail certification,
`--stale-panel-ok`, `stale_panel_requires_post_d6` and `guard_paths` went with task 343. So write the docs and the
mind map before the round — the templates put `## Pre-review` before the review section — and treat a change after
it as what it is: another round, or the owner's override. `tasks work done` refuses an option it does not read.

**What the stamp means** (task 085). The panel fingerprints the tree *before* it spawns the judges, and stamps that
value. If the tree moves while the judges run (you commit or edit during the review), the round keeps the pre-review
stamp, prints a warning and records `**Tree moved during review:** yes` — so the close reads STALE instead of trusting
code no judge was shown. The round's `**Commit:**` is read at the same moment. Since task 231 a review whose judges
read a fixed copy of the tree (docs/architecture.md) stamps the copy's sealed fingerprint and records the snapshot
taken before the spawn: the live tree may change while the judges run — what changed is simply after the panel, and
the close reads it as STALE — and "moved" means the copy itself changed. If git cannot fingerprint the tree before the
review, the round carries no stamp (`**Tree-state unavailable:**`) and a high-consequence close reads it STALE; it
never falls back to the post-review tree.

**Committing the task's own record does not stale the panel** (task 085). The fingerprint already ignores `.agent/`,
but a commit moves the outer `HEAD`, which the fingerprint does include — so committing the task record after its
panel used to read STALE with nothing to review. The close accepts that case as FRESH, and the receipt says "only workflow
records changed since the round", when all of these hold: every path the commits touch is a workflow record
(`.agent[/<lane>]/tasks/` — any task's —, session pointers, the journal, the chat log, `.agent/rulings.md` — NOT `.agent/config.json` or `models.json`),
the round's snapshot matches its stamp, `git diff <panel commit> HEAD` outside `.agent` is empty, and the fingerprint
recomputed with `HEAD` set back to the panel's commit equals the stamp (so the working tree and every `code_roots`
repo are exactly what the panel saw). Any other difference — the mind map and every other doc included — or any git
error, is still STALE. Only the outer `HEAD` gets this treatment; a commit inside a `code_roots` repo still reads STALE.

The stamp compares a tree-state fingerprint of the **outer** git repository. If your project keeps code in a
gitignored **nested checkout** (this workspace's `playbook-plugin/`), a code-only edit there is invisible to the outer
fingerprint and would read silently FRESH — declare those repos in
[`code_roots`](#agentconfigjson--code_roots-project-policy) so the freshness gate sees them.

A submodule (gitlink) move is listed whatever a local `diff.ignoreSubmodules` or `.gitmodules` `ignore=` says
(`--ignore-submodules=none`), by the freshness fingerprint and the records-only check alike (task 202).

### The review round cap

Before the close a task gets at most **2 impl panel rounds**, whatever its `## Risk` (DESIGN-PLAYBOOK-2.md §3.3
point 12, the owner's approval of 2026-10-28; task 344). No key changes it.

- `tasks panel-review <N> --mode impl` counts the task's impl panel rounds that met their quorum (`PANEL VERDICT:
  PASS` in the round's header, in `judge.md` and `judge-archive.md`; a round in both files once; a below-quorum round
  and a plan round are not rounds) and refuses a third — exit 2, before any judge is called, with nothing written to
  the task's records or the run ledger. A Critical in the second round adds no round. A record that cannot be read
  refuses the round too. Past the cap only the owner's `--owner-ok --reason "…"`, recorded in the panel's reservation.
  Plan panels are not capped.
- Each impl panel's reservation records the task's risk; the close takes the stricter of them all and today's, so
  lowering `## Risk` after the panel frees nothing (the freshness gate above).
- Judges have a third severity: at most three `**Minor**` findings below the counted ones, never counted and stopping
  nothing; the `CAP:` line counts the Critical and Important ones only.
- Gone (task 344): the caps per risk (reversible 1, assertive 2, irreversible 2), the round more after a Critical, and
  the `review_rounds` key — a config that still holds it is not read.
- Limits: `--owner-ok` is a marker an agent can type, not an authentication; a task with no impl panel is out of the
  rule's reach.

### The verify-contract guard (a change to `verify` is made visible)

`verify` lives in `.agent/config.json`, which is on the management path and so
is **exempt from the code-edit gate** — an agent can edit it without an active
task. That is deliberate (task bookkeeping must stay editable), but it means the
verify command itself could be silently weakened or deleted and tasks then
closed against a hollow bar. Two guards make such a change visible, without
hard-blocking a legitimate improvement:

- **Journalled at close.** Every close records the verify commands it ran in the
  enforcement journal (`.agent/**/journal/enforcement.jsonl`, `hook: "close"`,
  `decision: "record"` — the journal `decision` contract is `{allow, block,
  record}`, and a close is a log-only `record`, not a gate allow/block) in
  addition to naming them in the task's
  `## Verification Receipt`. The commands are newline-escaped onto a single line,
  so a multi-line verify command is kept intact (not chopped at its first
  newline, which would silently drop every command after it). The `command`
  field is still capped at pb_journal's 200-**byte** head limit (byte, not
  character — 200 multi-byte chars would blow the `PIPE_BUF` atomic-single-write
  bound) — so a very long command set is truncated there; the task's `## Verification Receipt`
  remains the authoritative, uncapped per-command list. Best-effort and
  log-only: a journal failure never changes or blocks a close.
- **Flagged by `tasks audit`.** The `verify-contract-change` sweep flags any
  command that was run at **some past close** (the union of every close receipt
  across **all lanes** — the contract is repo-global — so a weaken-then-close, a
  fresh lane, or a different user's history cannot hide a weakening) but is no
  longer in the current contract **for the risk that close ran under**. The
  comparison is *per-risk*: a command recorded at a `reversible` close is checked
  against today's `reversible` bar, so moving a command out of `_always` and into
  a single risk key — which quietly drops it from every *other* risk's bar — is
  caught, where an all-risk-union check would have called it "still present". It
  reports an **advisory** finding naming the dropped command. Advisory, not
  error: adding a command (a stronger bar) is never flagged, only a removal, and
  the sweep surfaces it for a human rather than hard-blocking.
- **Acknowledging an intentional removal.** A legitimate rename or drop
  (`npm test` → `pnpm test`) would otherwise fire on every audit forever. List
  the removed command(s) under `verify_contract_ack`:

  ```json
  {"verify": "pnpm test", "verify_contract_ack": ["npm test"]}
  ```

  An acknowledged removal is **not fully silenced** — it is downgraded from an
  advisory finding to an **informational (`info`) line** (below advisory, never
  failing the audit) that still names the removed command and its ack. This is
  deliberate: `verify_contract_ack` lives on the *same gate-exempt
  `.agent/config.json` path the guard watches*, so the actor who deletes `verify`
  can add the dropped command to the ack list in the very same write. That means
  self-acknowledgment cannot be *prevented* — so the guarantee is **visibility,
  not prevention**: an acknowledged removal always leaves a visible line for a
  human to confirm. Only the named commands are downgraded; any *other* drop
  still fires at advisory severity.

Resilience and bounds: the very **first close** in a repo has no prior receipt,
so the sweep is clean by design — there is no baseline until one close has
recorded one, and because the comparison is per-risk, a risk class's own bar is
baselined only once that class has closed at least once (weakening a risk-keyed
command before its first close is not yet visible). The comparison is per-command
first line (cmd1), what the receipt records, so a change confined to lines 2+ of
a multi-line command is not distinguished; a clean sweep therefore means "no
first-line command was dropped", not "verify was never touched". A verify command
containing a literal backtick is not reliably tracked: the backtick-delimited
receipt encoding cannot represent it, so the recorded form is truncated at the
first backtick. Usually this over-flags (a persistent false advisory, clearable
with `verify_contract_ack`), but if a weakening removes exactly the text after
that first backtick the change can also be **missed**. Backticks in a `verify`
command are therefore unsupported for drift detection — use `$(…)` command
substitution instead; a lossless receipt encoding is future work. The comparison
is **set-based per risk**, so it flags a *dropped* command, not a **reordering**
(every command still runs, just in a new order — that is not a weakening the
sweep claims to catch). `verify_contract_ack` matches by command **string across
all risk classes** (it is not risk-qualified), so acknowledging a command's
removal accepts it wherever it was dropped. The sweep baselines off the **committed**
`## Verification Receipt` sections (which survive clones), not the gitignored
enforcement journal; neither the receipts, the journal, nor the ack list is
tamper-proof against an agent with raw filesystem access — nor against a
concurrent close of the *same task* overwriting a strong receipt with a weak one
— so this is best-effort *visibility*, and the OS sandbox plus human review
remain the real containment. The journal keeps the full per-close trail for
forensics.

## `.agent/config.json` — `write_log` (a copy of each edited file, outside the project)

After every `Edit` or `Write`, the state-echo hook appends a copy of the file the agent just wrote to a log **outside the project**: `~/.local/share/playbook/<project-slug>/write_log` (the slug is the project's absolute path without its leading `/`, the other `/` turned into `-`: `/home/me/proj` → `home-me-proj` — unlike Claude Code's own `~/.claude/projects/-home-me-proj`). It is a safety net — the last content an agent wrote, kept where the agent cannot delete it by accident — and nothing in Playbook reads it back. Each entry is a header line (`=== <UTC time> <path> (<N> bytes) ===`) followed by the file's bytes.

- **It can hold secrets.** A `.env` you let the agent edit is copied like any other file — and so is a file OUTSIDE the project that the agent edits (a dotfile in your home directory, say): the log records every Edit/Write the hook sees, wherever the file lives. The log lives in your home directory, is never committed, and is yours to delete at any time (`rm ~/.local/share/playbook/<project-slug>/write_log*`).
- **It is bounded** (since task 141). A file over **1 MB** is named in the log but not copied. When the next copy would take the log past **10 MB** it is renamed to `write_log.1`, replacing the previous one — so at most two files of 10 MB each per project, and older copies are dropped at each rotation. A relative path is read from the project directory.
- **A log from before the cap is kept, once.** The first time the bounded hook runs for a project, an existing `write_log` is renamed to `write_log.pre-cap-<UTC time>-<pid>` and never rotated or deleted by Playbook; the empty file `write_log.capped` records that this ran. Those pre-cap files can be large — delete them when you no longer want them.
- **Private and serialized.** The directory is created `0700` and the log files `0600` (existing ones are tightened). Two edits at the same moment take turns on a lock file in that directory (`write_log.lock`): a hook waits at most 1.5 s for it and then skips that one copy. On a filesystem without OS lock support (NFS without lockd, some FUSE mounts) the turns are taken on a lock directory instead (`write_log.lock.d`, cleared when older than 30 s). Known limit there: if a hook died holding it and two edits then arrive within the same instant, both may clear it and write, so the log can pass 10 MB by one copy. If the log is full and cannot be rotated, that copy is skipped rather than let the log pass 10 MB.
- **Turn it off** with `{"write_log": false}` (`"false"`, `"off"`, `"no"` and `0` work too). A `.agent/config.json` that exists but cannot be read or parsed also turns it off — it could be a half-written opt-out. Nothing is written and no directory is created; logs that already exist are left alone.

## `.agent/models.json` — judge panel pins

Judge selection lives in `models.json`: the plugin ships defaults in `provider/models.json`, and each install can shadow them per key with a gitignored `.agent/models.json`. **Supported judge seats are Claude, Grok, and Codex** — live-verified 2026-08-22 (the pins `opus`, `sonnet`, `codex:gpt-5.6-terra:high`, `codex:gpt-5.6-sol:high`, and `grok:grok-4.6:high` all responded on the owner's account); Antigravity (`agy`) and Pi seats are experimental (see the [provider support matrix](providers.md#support-matrix)):

```json
{
  "default_judge": "claude",
  "panel": ["opus", "claude:claude-sonnet-5", "codex:gpt-5.6-terra:high", "grok:grok-4.6"],
  "aliases": {"opus": ["claude", "claude-opus-4-8", []]}
}
```

- `default_judge` — backend for bare `plan-review` / `impl-review`.
- `panel` — the judge seats for `panel-review`; each spec is `backend[:model[:effort]]`.
- `aliases` — shorthand names expanded before dispatch. Each value is a
  **three-element list** `[agent, canonical_model_or_null, [extra_args]]` (the
  same schema as `provider/models.json`); a bare string is off-schema and is
  silently dropped by the parser.

### Keeping pins alive

Pinned model ids rot as providers ship and retire models, so the pins have a maintenance loop:

- `tasks models check` audits every pin against **live availability**: codex pins are probed with a tiny prompt (the `~/.codex/models_cache.json` catalog alone doesn't prove your account can use a model), claude pins are probed budget-capped (claude has no list command — new ids enter via `--claude-candidates`), grok pins are checked against `grok models` (a login-aware entitlement list: a pin that is not in it is GONE) and a listed pin is then probed with one tiny real turn, run with the pin's own effort and read for its answer — the list cannot see that the account's credit ran out, and a pin that only was listed used to read OK while every call failed —, agy seats (experimental) — a pinned one after a check against `agy models`, a bare `agy` one too — are probed with one tiny real turn that must come back as a clean answer from the pinned model (a listed model can be out of quota, or the sign-in gone); an id agy does not know is GONE, a missing or contradicted effort is BAD_EFFORT, anything else is UNKNOWN with its cause — and the report prints agy's remaining quota and AI credits, read without a model turn. `--no-probe` is the free/fast degraded audit (an agy seat and a listed grok pin then read LISTED). Exits 1 when any pin can't run as configured.
- `tasks models detect [--json]` is the fast inventory: which agent CLIs are installed and each one's selectable models + reasoning-effort levels. It reads local caches and the cheap listing commands only (no model is live-probed — though `grok models` is login-aware, so not strictly offline), so it never proves availability the way `check` does — it lists *choices*. `/playbook:init` reads it to build the panel menu.
- `tasks models select` refreshes interactively: shows the report, takes the new panel + default judge, writes `.agent/models.json` — creating it on fresh installs and preserving keys it doesn't manage.
- `tasks models set --panel a,b --default-judge c [--force]` is the non-interactive twin — same validation and no-probe audit, flag-driven. A dead pin aborts unless `--force`; this is how `/playbook:init` persists the panel it asked you about.
- `tasks doctor` warns (never fails) on a missing models.json or dead pins, using the cheap checks only.

### Failure semantics

- When a panel seat fails because its provider says the account is **out of credit** — grok's `402 Payment Required … usage balance exhausted`, codex's `You've hit your usage limit … try again at <time>` or (since 1.6.2) `Your workspace is out of credits` (no time given), agy's quota stop, claude's `You've hit your weekly limit · resets <time>` (read only when that line is all the seat printed) — the panel records it in `<lane>/journal/seat-outages.json` (machine-local, gitignored with the journal). When the message gives a reset time (codex's "try again at", agy, claude), later panels skip that seat with one line naming it until then — or sooner, for instance after you switch to another account, until you clear it with `tasks models enable <seat>`. When it gives none (grok's 402, a codex workspace out of credits), every later panel **tries the seat again**, beside the other seats so nothing waits for it:
  - a refusal for credit that comes before 60 s and reports no tokens is treated exactly like a skip — no review record in the journal, no section or place in `judge.md`, not counted in "N/M succeeded" or the run ledger — and the panel prints `Still out of credit: <seat> (…) — the next panel tries it again`; the record keeps when the seat ran out and counts the tries (`tried`, `last_tried`);
  - a review (or a call that worked at least 60 s and reported tokens before it reached its timeout) shows the account works: the panel prints `Back: <seat> answered …` and removes the record — unless a newer outage of that seat was recorded in the meantime;
  - any other failure, and a refusal for credit that took 60 s or longer or reported tokens, is a failed seat as before, recorded in the journal; the outage stays.
  - When the other seats alone cannot reach the quorum, the seats tried again go first: the panel waits for them at most 60 s and starts the rest only if the quorum can still be reached — counting the seats still working and those whose review came, never a refusal or a failure. A seat still working is never stopped. Otherwise the other seats are never called and the panel is refused (exit 1) with no round written; its run-ledger record says `refused`, which `tasks numbers` does not count as a round.

  A call to the seat that was under way when you cleared it does not put the seat back for the failure it saw. Skipping never lowers the quorum (above).

- When a review judge fails **specifically because its model no longer exists** — probe-confirmed, not just pattern-matched — the review still saves its output, then prints the availability report and exits nonzero: a deliberate hard stop so you re-pin before trusting a degraded panel. Timeouts, budget caps, and other errors keep their soft behavior.
- A judge that exhausts its budget cap is reported as **failed** with an explicit notice (raise `judge_budget_usd` or pass `--budget`) instead of masquerading as a successful empty review.

## Environment variables

| Variable | Purpose |
|---|---|
| `PLAYBOOK_JUDGE_BUDGET_USD` | Overrides `judge_budget_usd` (below CLI flags). |
| `PLAYBOOK_REVIEW_TIMEOUT_SECS` | Overrides `review_timeout_secs` (below CLI flags). May only raise a hard timeout that `config.json` has set — see the floor rule above. |
| `PLAYBOOK_REVIEW_SOFT_TIMEOUT_SECS` | Overrides `review_soft_timeout_secs` (below CLI flags). Not floored. |
| `PLAYBOOK_PANEL_QUORUM` | Overrides `panel_quorum` — same value grammar (`majority` / `all` / integer / fraction). |
| `PLAYBOOK_VERIFY_TIMEOUT_SECS` | Overrides `verify_timeout_secs` — the close-time verify ceiling (`0`/`unlimited` disables). |
| `PLAYBOOK_REVIEW_CONTEXT_CHARS` | Overrides `review_context_chars` — the argv-transport judge context budget. |
| `PLAYBOOK_REVIEW_CONTEXT_CHARS_STDIN` | Overrides `review_context_chars_stdin` — the stdin-transport judge context budget. |
| `GIT_CEILING_DIRECTORIES`, `GIT_DISCOVERY_ACROSS_FILESYSTEM`, `GIT_DIR` | Not Playbook's own, but honoured: the tamper guard's "is this a git repo?" probe walks ancestors the way git's discovery does, so a project below a ceiling or across a mount is not mistaken for part of an unreachable parent repo (v1.5.45). |
| `PLAYBOOK_ALLOW_DANGEROUS` | Set truthy IN THE HOOK'S ENVIRONMENT (operator shell / harness env, not a command prefix) to acknowledge a destructive command past the `command_guard` interlock (a human-confirmed one-off). |
| `PLAYBOOK_BASH` | Absolute path to the `bash` the shell-dependent surfaces (audit sweeps, `merge-verify`, `scripts/verify`) should use. Needed only where a bare `bash` on `PATH` is not the right one. The chosen bash is probed with a sentinel; an unusable one fails closed. `$PLAYBOOK_VERIFY_BASH` (named for the dev verifier) is honoured as a fallback when `PLAYBOOK_BASH` is unset. |
| `PLAYBOOK_SHELL_LOG_LOCK_WAIT` | `1` or `2` seconds (anything else means the default, 1). Read in the environment the HOOKS run in: how long the shell log's writer waits for the lock on the lane directory before it writes its line to a spool file of its own instead (the next writer that holds the lock appends it) — no line is lost or torn either way (task 221). |
| `PLAYBOOK_HOOK_JOURNAL_SIZE_CHECK` | `1`-`1000` (anything else means the default, 32). Read in the environment the HOOKS run in: about one hook run in this many looks at the hook journal (`journal/hooks.jsonl`): past 20 MB it is renamed, and a closed file is compressed once its last write is over 10 minutes old; every one is kept (task 222; `docs/enforcement-journal.md`, "The hook journal"). `1` checks on every run (a `stat` each: ~1.5 ms). |
| `PLAYBOOK_COUNTERS_LOCK_WAIT` | `1` or `2` seconds (anything else means the default, 2). It can only SHORTEN the wait: the host stops a hook after 5 seconds, and a hook takes the lock twice — the limit is for both holds, which can wait just under 3 seconds between them. Read in the environment the HOOKS run in: how long a hook waits for the session's counters lock (`counters.lock` in the session directory) before it gives up — the tool call is then NOT counted, the hook says so, and the session is marked (`counts_unsure`) so that the stop hook reads that turn as one that wrote (its memento shows) until your next prompt. |
| `PLAYBOOK_PROJECT_ROOT`, `PLAYBOOK_SESSION_ID`, `PLAYBOOK_SANDBOXED`, `PLAYBOOK_MINDMAP_MAX`, `PLAYBOOK_EVAL_CONFIG`, `PLAYBOOK_PROC_ROOT` | Internal — set by the wrappers, hooks, sandbox and tests; not meant to be set by hand. |
