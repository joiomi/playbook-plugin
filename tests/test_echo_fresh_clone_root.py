"""The state echo makes no session directory at the root of a fresh multi-user clone (task 192, PLAN S14 T3, #41).

On a clone with per-user lanes (`.agent/<user>/tasks/`) and no `.agent/current_user` (the marker is gitignored, so a
clone never receives it), `state-echo-hook` ran `mkdir -p "$AGENT_DIR/sessions/<id>"` before any writability check:
`AGENT_DIR` is the ROOT `.agent` there, so every tool call left an empty `.agent/sessions/<id>/` beside the lanes
(found live by task 184's PB-LANE-RESOLUTION session). The echo already computed that it may not write there
(`agent_dir_writable` refuses that shape); the directory is now made only when it may.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._bashcheck import bash_or_skip

SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts"
SID = "t192-fresh"


class EchoAtAFreshClone(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        for user in ("alice", "bob"):
            (self.project / ".agent" / user / "tasks").mkdir(parents=True)

    def echo(self):
        env = {k: v for k, v in os.environ.items() if k not in ("PLAYBOOK_SESSION_ID", "BASH_ENV", "CLAUDE_ENV_FILE")}
        env["PLAYBOOK_SESSION_ID"] = SID
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / "state-echo-hook")], cwd=self.project, env=env,
                           input=json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_no_session_directory_at_the_root(self):
        self.echo()
        self.echo()
        self.assertFalse((self.project / ".agent" / "sessions").exists(),
                         f"root entries: {sorted(p.name for p in (self.project / '.agent').iterdir())}")
        self.assertEqual(sorted(p.name for p in (self.project / ".agent").iterdir()), ["alice", "bob"])

    def test_a_picked_lane_gets_its_session_directory(self):
        # Negative control: once the user picks a lane the echo provisions the session there, as before.
        (self.project / ".agent" / "current_user").write_text("alice\n", encoding="utf-8")
        self.echo()
        self.assertTrue((self.project / ".agent" / "alice" / "sessions" / SID).is_dir())
        self.assertFalse((self.project / ".agent" / "sessions").exists())


if __name__ == "__main__":
    unittest.main()
