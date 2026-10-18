# The live gauntlet (PLAN S11, task 184)

`tests/live/gauntlet.sh` runs, for real, the scenario each ledger row's
`required_live_evidence` asks for — real agent sessions, real judges, real
interpreters in containers, real bubblewrap, a disposable install — on the plugin
tree of THIS checkout (the candidate). Each row has one section in the script,
`=== <ROW-ID>:`, which is what the ledger binds as `scenario:<ROW-ID>` of
`tests/live/gauntlet.sh`.

**Never run by CI or `scripts/verify`.** It spends the owner's model quota, starts
containers and needs the network. `unittest discover` does not collect it (no
`test_*` file, no `__init__.py`), and `tests/test_shell_fixtures.py` runs only its
own six fixtures; `scripts/verify` neither runs nor lints this directory (its
`bash -n` and `ast.parse` steps count the same files with and without it).

    tests/live/gauntlet.sh --out <dir> [--root candidate|installed] [ROW-ID ...]    # no ROW-ID: every row
    tests/live/gauntlet.sh --list

`--root installed` (PLAN §S13's installed-root gauntlet, task 185) runs every row on the copy the
plugin manager installed — the `installPath` of the `playbook@…` entry of
`~/.claude/plugins/installed_plugins.json` — instead of this checkout's tree; a registry without
exactly one such copy is refused, never fallen back from. Each artifact names its root
(`root: candidate|installed`) and the tree it ran. The install rows build their own disposable
install either way: they publish this checkout's HEAD over v1.6.0 (`rows_install.OLD_REF`), so on
the installed root HEAD must be the released commit.

**Where to run it from:** the nested repository inside the workspace. Two rows read beyond the plugin
tree: the install rows publish this checkout by a bare clone of it and clone that `-b main`, so it needs a
local `main` branch; PB-CLI-INTENT checks the owner's distillation record (task 183's intent run and INTENT.md
in the workspace around this repository). `lib/preflight.py` checks both for the rows chosen and refuses
before any row runs, saying why — a fresh clone elsewhere once failed three rows halfway (task 242; a
fourth failed that run for another reason). The install rows also need v1.6.0's commit (`rows_install.OLD_REF`)
in the checkout: a shallow clone may lack it, and the preflight says so.

One artifact per row, `<dir>/<ROW-ID>.txt`: the candidate commit, tool versions,
every command with its full output and the filesystem delta it was told to watch,
one `CLAUSE PASS|FAIL` line per clause with the observed evidence, and the last
line `ROW <ID>: PASS|FAIL|WAITING (…)`. Exit status 0 = every selected row passed,
1 = one failed, 3 = none failed but one is waiting for a target that cannot run now.

What it never does: copy a credential (claude and codex run under the owner's own
account; task 109's copied homes cost a codex sign-in), touch the owner's install
or projects (the install rows run in a disposable HOME), write inside this
repository. Scratch projects are made under the system temp directory with the
project half of init (`claude-md-merge.py`), never its HOME half.

**The session rows** (`lib/rows_session.py`, `lib/rows_session2.py`) start real headless
`claude -p` sessions in scratch projects. Each scratch project disables the owner's
installed copy in its own `.claude/settings.json`; the candidate is loaded with
`--plugin-dir`, and `.claude/bin/tasks` runs the candidate's CLI. Sessions run with
`--setting-sources project,local`, so the owner's user settings — his own hooks, such
as a sound at every stop — stay out of them, and DETACHED (`setsid -f`): the hooks give
a session the identity of the root agent of its process chain, so a session started
under the one running the gauntlet would carry that one's identity. What a clause reads
is what the hooks left — the enforcement journal, the chat log, the files, the hook
responses Claude Code reports with `--include-hook-events` — never the agent's own
account. Scratch directories are removed when the row ends; the sessions' transcripts
stay where Claude Code keeps them (`~/.claude/projects/-tmp-pb184-*`), which the
gauntlet does not touch.

**The judge rows** (`lib/rows_judge.py`) run REAL judges — a claude seat (`haiku`) and a
codex seat (`gpt-6.1-sol` at `low`) — through the candidate's review paths. Each judge
CLI is reached through a recording shim placed first on PATH: it stands where the
judge's CLI starts, inside the judge's sandbox, keeps the exact argv and prompt the
review path sent, tries one harmless write into the project, and then runs the real
CLI with the same arguments and prompt. Grok is a target of these rows; they end WAITING,
not PASS, and this harness has no grok seat yet: its half is new code (a shim like the other
two, the seat in each scenario), written and reviewed once its credit returns.

PB-CLI-INTENT's last step — reconciling the reports with the user and writing INTENT.md — is a
human act: it was done with the owner on task 183 (2026-10-12), and the scenario checks that
record (`rows_judge.OWNER_DISTILLATION`: the run's reports, the INTENT.md entry with its marker,
the graded seams) instead of redoing it.

`selfcheck.py <artifacts-dir>` is the harness's own negative control, offline and free: each
clause the impl panel found able to pass without its behaviour is a pure predicate, run on a
committed artifact (it must PASS) and on a copy with exactly that behaviour removed (it must FAIL).
`gauntlet.sh` runs it at the end of every run, on the artifacts that run wrote (controls whose
row was not run are skipped and said); a control that does not hold fails the run. A row that
exits with anything but 0 or 3 — killed, crashed — fails the run too, and leaves no artifact.

`check_flips.py <artifacts-dir>` holds the ledger to the every-target rule: a row whose
`required_live_evidence` names targets is verified only with a live proof bound to its
`scenario:<ROW-ID>` and an artifact that passed every target, waited for none, and was
made on a clean candidate.
