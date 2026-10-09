"""A stand-in `bwrap` for tests that only PREVIEW a sandbox launch (task 164).

The launcher refuses to start — or to preview — an agent on a host with no usable
bubblewrap. Tests whose subject is the preview still have to run on such a host:
`--print-argv` builds the wrapped argv and never executes bwrap, so a `bwrap` that
does nothing but answer the launcher's start probe is enough for them.

`path_with_bwrap(bindir)` returns a PATH that has a bwrap on it: the host's own when
there is one, otherwise this stand-in, written into `bindir` and put first.

It contains nothing. Never use it for a test that launches an agent.

The leading underscore keeps unittest's ``test*.py`` discovery from collecting this
module as a test.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def path_with_bwrap(bindir: Path, path: "str | None" = None) -> str:
    path = os.environ.get("PATH", "") if path is None else path
    if shutil.which("bwrap", path=path):
        return path
    stand_in = Path(bindir) / "bwrap"
    stand_in.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stand_in.chmod(0o755)
    return f"{bindir}{os.pathsep}{path}"
