"""Bubblewrap for the tests: is the host's usable, and a stand-in where it is not (task 164).

The launcher refuses to start — or to preview — an agent on a host with no USABLE
bubblewrap: none on PATH, or one that cannot start a sandbox (user namespaces denied).
`shutil.which("bwrap")` alone cannot tell the second case, so tests ask `bwrap_usable()`.

Tests whose subject is the preview still have to run on such hosts: `--print-argv`
builds the wrapped argv and never executes bwrap, so a `bwrap` that does nothing but
answer the launcher's start probe is enough for them. `path_with_bwrap(bindir)`
returns a PATH with a usable bwrap on it: the host's own when it works, otherwise
this stand-in, written into `bindir` and put FIRST (it has to shadow a broken one).

The stand-in contains nothing. Never use it for a test that launches an agent —
gate those on `bwrap_usable()`.

The leading underscore keeps unittest's ``test*.py`` discovery from collecting this
module as a test.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "playbook"
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))


def bwrap_usable(path: "str | None" = None) -> bool:
    """True when the bubblewrap found on `path` (default: this process's PATH) starts a
    sandbox here — the launcher's own probe, so the tests and the product agree."""
    from provider.sandbox import bwrap_start_error
    exe = shutil.which("bwrap", path=os.environ.get("PATH", "") if path is None else path)
    # the launcher's rule (provider.sandbox.bwrap_state): only a bwrap behind an ABSOLUTE
    # PATH entry is used, and only if it starts
    return bool(exe) and os.path.isabs(exe) and bwrap_start_error(exe) is None


def path_with_bwrap(bindir: Path, path: "str | None" = None) -> str:
    path = os.environ.get("PATH", "") if path is None else path
    if bwrap_usable(path):
        return path
    stand_in = Path(bindir) / "bwrap"
    stand_in.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stand_in.chmod(0o755)
    return f"{bindir}{os.pathsep}{path}"
