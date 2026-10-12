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

    tests/live/gauntlet.sh --out <dir> [ROW-ID ...]    # no ROW-ID: every row
    tests/live/gauntlet.sh --list

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
