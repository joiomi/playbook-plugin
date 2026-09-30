#!/usr/bin/env python3
"""PB-CONFIG-ATOMIC-DURABILITY (PLAN S8c, task 100): routing completeness as a test.

The ledger row proves the atomic primitive (same-dir temp, flush+fsync,
os.replace, permission-preserving) but disclosed that "every consequence-relevant
writer calls the primitive" was enforced by code review only. This sweep makes it
mechanical: an AST walk over every shipped Python module under tasks/, provider/
and scripts/ (recursively — monitor-lib, adapters) finds each TRUNCATING write —
`.write_text(...)`, `.write_bytes(...)`, `open(..., "w"/"x"...)`,
`os.fdopen(fd, "w"...)` — and a regex pass over the extensionless bash scripts in
scripts/ finds the same shapes inside their embedded `python3 -c` snippets
(scripts/init writes settings.json that way). Every site must be either the
primitive itself or a documented exception carrying its reason. A new direct
writer fails the build; a stale exception fails it too, so an approval can never
silently cover a future writer with the same shape.

Out of scope, by the row's own statement: append-mode opens (`"a"`) and the
journal's `os.open(O_APPEND)`/`os.write` path never truncate; bash `cat >`/`echo >`
creations of files init only writes when they do NOT yet exist (`.agent/config.json`,
the MIND_MAP.md stub) are covered by init's transaction, which removes what a failed
run created — recorded on the ledger row, not matched here. The `scripts/lib/provider`
tree is a byte-identical mirror of `provider/` (tests/test_mirror_sync.py) and is skipped.

Run: python3 tests/test_atomic_routing_sweep.py
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins" / "playbook"
SCAN_DIRS = ("tasks", "provider", "scripts", "skills")
MIRROR_PREFIX = "scripts/lib/provider/"

# (relative file, a substring of the call's source line) -> why it is NOT routed
# through tasks.atomic. Every entry must still match a real site (stale entries fail).
APPROVED = {
    ("tasks/post_d6.py", "shutil.copy2(real, idx)"):
        "task 108: a copy of the git index into a private mkdtemp dir (the temporary GIT_INDEX_FILE "
        "for the post-D6 base tree), removed in `finally` — ephemeral, never state",
    ("tasks/atomic.py", 'os.fdopen(fd, "wb")'):
        "the primitive itself: the temp file it later os.replace()s into place",
    ("tasks/atomic.py", 'os.fdopen(fd, "w", encoding=encoding, newline=newline)'):
        "the primitive itself (text mode)",
    ("tasks/audit.py", 'os.fdopen(fd, "w", encoding="utf-8", newline="\\n")'):
        "an mkstemp'd sweep script handed to bash and deleted — ephemeral, never state",
    ("skills/merge/merge-verify.py", 'os.fdopen(fd, "w", encoding="utf-8", newline="\\n")'):
        "an mkstemp'd script for the declared merge_verify command, run by bash and deleted — ephemeral",
    ("tasks/intent.py", '(Path(td) / "evidence.md").write_text('):
        "evidence for a judge inside a TemporaryDirectory — ephemeral, never state",
    ("tasks/merge_prep.py", '(agent_dir / "chat_log_counter").write_text('):
        "the chat-log-hook flock-protocol DATA file: an os.replace would swap the inode "
        "out from under a concurrent locked incrementer (documented exception)",
    ("scripts/gate-batch-check.py", "marker.write_text(str(tools_now)"):
        "hook-time advisory counter (the consecutive-batch marker); a torn value only "
        "loses one advisory, never a decision (documented exception: hook-time writers)",
    ("scripts/monitor-lib/sensor.py", 'tmp.write_text(f"{jsonl_path}\\n{offset}")'):
        "monitor sensor poll offset: temp + os.replace (no fsync) — advisory state rewritten "
        "on every poll; a lost offset re-reads events, never loses a decision",
    ("scripts/monitor-lib/sensor.py", "tmp.write_text(str(offset))"):
        "monitor sensor poll offset (legacy shape), same bound as above",
    ("scripts/init_txn.py", 'shutil.copy2(e["path"], dest)'):
        "the transaction's own snapshot: copies the LIVE file into the backup dir at begin "
        "(verbatim bytes + mode) — the target is the record, never a live file",
    ("provider/adapter.py", "offset_path.write_text(str(offset))"):
        "hook-time chat_log_offset (documented exception: adapter offset); re-processing "
        "a few messages is the cheaper failure",
    ("provider/codex_hooks.py", "log_path.write_text(new_text"):
        "codex-*-hook runtime: tasks/ is off sys.path there (provider/paths.py); chat-log "
        "migration rewrite — experimental provider (owner 2026-08-21)",
    ("provider/codex_hooks.py", 'counter_path.write_text(f"{msg_num}\\n"'):
        "codex-*-hook runtime counter (same bootstrap bound as above)",
    ("provider/codex_hooks.py", 'counter_path.write_text("\\n".join(lines)'):
        "codex-*-hook runtime counters reset (same bootstrap bound)",
    ("provider/codex_hooks.py", "log_path.write_text(_CHAT_LOG_HEADER"):
        "codex-*-hook runtime: first chat-log header (same bootstrap bound)",
    ("provider/codex_hooks.py", 'counter_path.write_text(f"{next_id}\\n"'):
        "codex-*-hook runtime counter (same bootstrap bound)",
    ("provider/codex_hooks.py", "baseline_file.write_text("):
        "codex-*-hook runtime per-turn baseline (same bootstrap bound)",
    ("provider/codex_hooks.py", 'marker.write_text("1"'):
        "codex-*-hook runtime stop-block marker, best-effort (same bootstrap bound)",
}

# The same shapes, as text, for python embedded in the extensionless bash scripts.
_EMBEDDED_WRITE = re.compile(r"""open\([^)\n]*,\s*(?:mode\s*=\s*)?['"][^'"]*[wx]|\.write_text\(|\.write_bytes\(""")


def _mode_of(call: ast.Call, positional_index: int) -> str | None:
    """The mode string of an open()/fdopen() call, if it is a literal."""
    args = call.args
    if len(args) > positional_index:
        arg = args[positional_index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _is_truncating_write(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Attribute):
        if f.attr in ("write_text", "write_bytes"):
            return True
        if (f.attr in ("copyfile", "copy", "copy2", "copyfileobj")        # shutil.* truncates the target
                and isinstance(f.value, ast.Name) and f.value.id == "shutil"):
            return True
        if f.attr == "open" and isinstance(f.value, ast.Name) and f.value.id == "os":
            # os.open(..., O_TRUNC | ...) truncates; the journal's O_APPEND path does not
            return any(isinstance(n, ast.Attribute) and n.attr == "O_TRUNC" for n in ast.walk(call))
        if f.attr == "fdopen":
            m = _mode_of(call, 1)
            return bool(m) and ("w" in m or "x" in m)
        if f.attr == "open":                      # Path(...).open("w")
            m = _mode_of(call, 0)
            return bool(m) and ("w" in m or "x" in m)
    if isinstance(f, ast.Name) and f.id == "open":
        m = _mode_of(call, 1)
        return bool(m) and ("w" in m or "x" in m)
    return False


def sites_in_python_source(rel: str, text: str):
    """[(rel, line, source line)] for every truncating write in one Python module."""
    tree = ast.parse(text)
    lines = text.splitlines()
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_truncating_write(node):
            out.append((rel, node.lineno, lines[node.lineno - 1].strip()))
    return out


def sites_in_shell_source(rel: str, text: str):
    """[(rel, line, source line)] for embedded-python write shapes in a bash script."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):          # a comment is not a write
            continue
        if _EMBEDDED_WRITE.search(line):
            out.append((rel, i, line.strip()))
    return out


def truncating_write_sites(plugin: Path = PLUGIN):
    """Every truncating write under the scanned tree (mirror excluded)."""
    sites = []
    for rel_dir in SCAN_DIRS:
        for f in sorted((plugin / rel_dir).rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(plugin).as_posix()
            if rel.startswith(MIRROR_PREFIX) or "__pycache__" in rel:
                continue
            if f.suffix == ".py":
                sites += sites_in_python_source(rel, f.read_text(encoding="utf-8"))
            elif f.suffix == "" and rel_dir == "scripts":
                head = f.read_bytes()[:64]
                if head.startswith(b"#!") and b"bash" in head:
                    sites += sites_in_shell_source(rel, f.read_text(encoding="utf-8", errors="replace"))
    return sites


class EveryTruncatingWriterIsRoutedOrDocumented(unittest.TestCase):
    def test_no_undocumented_direct_writer(self):
        unapproved = []
        for rel, lineno, src in truncating_write_sites():
            if any(rel == f and sub in src for (f, sub) in APPROVED):
                continue
            unapproved.append(f"{rel}:{lineno}: {src[:110]}")
        self.assertEqual(unapproved, [], "\n".join(
            ["truncating writers outside tasks.atomic with no documented reason — route them "
             "through `atomic_write`/`atomic.rewrite`, or add them to APPROVED with the reason:",
             *unapproved]))

    def test_every_approval_matches_a_real_site(self):
        # A stale approval is an exemption waiting to cover a future writer.
        sites = truncating_write_sites()
        stale = [f"{f}: {sub}" for (f, sub) in APPROVED
                 if not any(rel == f and sub in src for rel, _ln, src in sites)]
        self.assertEqual(stale, [], f"stale approvals: {stale}")

    def test_the_sweep_sees_the_primitive_and_is_not_vacuous(self):
        sites = truncating_write_sites()
        self.assertTrue(any(rel == "tasks/atomic.py" for rel, _l, _s in sites),
                        "the sweep did not even find the primitive's own temp write")
        self.assertTrue(any(rel.startswith("scripts/monitor-lib/") for rel, _l, _s in sites),
                        "the sweep is not recursive — monitor-lib was not visited")
        self.assertGreaterEqual(len(sites), 5, f"suspiciously few write sites: {sites}")

    def test_control_a_planted_writer_is_detected(self):
        # Impl panel r1 (opus): the detector must be shown catching a writer, in
        # every shape it claims, and NOT flagging the shapes it exempts.
        planted = (
            "from pathlib import Path\nimport os\n"
            "def a(p):\n    Path(p).write_text('x')\n"
            "def b(p):\n    with open(p, 'w') as f:\n        f.write('x')\n"
            "def c(p):\n    open(p, mode='wb').write(b'x')\n"
            "def d(fd):\n    os.fdopen(fd, 'w').write('x')\n"
            "def e(p):\n    Path(p).open('x').write('x')\n"
            "def f(p):\n    Path(p).write_bytes(b'x')\n"
            "def g(p):\n    open(p).read()\n"
            "def h(p):\n    open(p, 'a').write('x')\n"
            "def i(p):\n    open(p, 'rb').read()\n"
            "import shutil\n"
            "def j(a, b):\n    shutil.copyfile(a, b)\n"
            "def k(p):\n    os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)\n"
            "def l(p):\n    os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND)\n"
        )
        hits = sites_in_python_source("planted.py", planted)
        # a..f (4, 6, 9, 11, 13, 15), j (23) and k (25) are writes; g, h, i (read,
        # append, rb) and l (O_APPEND) are not — impl panel r2 added the shutil/os.open shapes
        self.assertEqual(sorted(ln for _r, ln, _s in hits), [4, 6, 9, 11, 13, 15, 24, 26],
                         f"detector missed or over-flagged a shape: {hits}")
        shell = ("#!/bin/bash\nx=$(python3 -c \"\nimport json\n"
                 "open(path, 'w').write('x')\n"
                 "data = json.loads(open(path).read())\n"
                 "with open(path, 'a') as f: pass\n\")\n")
        self.assertEqual([ln for _r, ln, _s in sites_in_shell_source("init", shell)], [4])

    def test_the_consequence_relevant_writers_import_the_primitive(self):
        # The modules the ledger row names as routed: each must import the primitive
        # and, by the sweep above, own no undocumented direct write.
        for rel in ("tasks/core.py", "tasks/lifecycle.py", "tasks/review.py", "tasks/models_check.py",
                    "tasks/project_setup.py", "tasks/compact.py", "tasks/history.py", "tasks/merge_prep.py",
                    "tasks/mindmap.py", "provider/adapters/grok.py", "provider/adapters/codex.py",
                    "provider/adapters/antigravity.py", "provider/adapters/pi.py", "scripts/init",
                    "scripts/init_txn.py", "scripts/claude-md-merge.py"):
            with self.subTest(module=rel):
                text = (PLUGIN / rel).read_text(encoding="utf-8", errors="replace")
                self.assertTrue("from tasks.atomic import" in text or "atomic.rewrite" in text
                                or "_pb_atomic_write" in text,
                                f"{rel} writes state but never loads tasks.atomic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
