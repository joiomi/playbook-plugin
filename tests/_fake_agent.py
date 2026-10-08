"""A live process whose comm is an agent name, for tests (task 106).

Since task 106 a `PLAYBOOK_SESSION_ID=pid-<digits>` is honored only when it
names a LIVE process whose comm is an agent (claude*, codex, agy, grok, pi), so a
test that pins a session with a numeric pid needs such a process. This copies
the `sleep` binary under an agent name (comm comes from the executed file's
name) and runs it; the caller kills it. The leading underscore keeps unittest
discovery from collecting this module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def spawn_fake_agent(directory: "str | os.PathLike[str]", name: str = "claude",
                     seconds: int = 300) -> subprocess.Popen:
    sleep = shutil.which("sleep")
    if sleep is None:
        raise RuntimeError("no `sleep` binary to impersonate an agent")
    exe = Path(directory) / name
    shutil.copy(sleep, exe)
    exe.chmod(0o755)
    return subprocess.Popen([str(exe), str(seconds)])


def agent_proc_root(directory: "str | os.PathLike[str]", pid: int, name: str = "claude") -> str:
    """A /proc fixture (for PLAYBOOK_PROC_ROOT) that lists ONLY `pid` as a
    live agent named `name`.

    Why (owner decision 2026-09-28): an env `pid-N` is refused when the walk
    finds a DIFFERENT agent root — and a test run inside a real Claude Code
    session always walks up to that real claude, while CI has none. Under this
    fixture the walk finds no agent (the test's own processes are absent), so
    the id is judged the same way locally and in CI; `pid` should also be a
    REAL live process (spawn_fake_agent) wherever a GC probes it with kill -0.
    """
    root = Path(directory) / f"proc-fixture-{pid}"
    (root / "self").mkdir(parents=True, exist_ok=True)
    d = root / str(pid)
    d.mkdir(exist_ok=True)
    (d / "status").write_text(f"Name:\t{name}\nState:\tS (sleeping)\nPPid:\t1\n", encoding="utf-8")
    (d / "cmdline").write_bytes(name.encode() + b"\0" + b"300\0")
    return str(root)


def stop(proc: subprocess.Popen) -> None:
    proc.kill()
    proc.wait()
