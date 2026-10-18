"""`agent-usage-hook` — the recorder of the main agent's tokens, on Stop and SessionEnd (task 244, PHASE-B item 6).

At each turn's end (Stop) and at the session's end (SessionEnd) it reads the session's host transcript (the payload's
`transcript_path`, then the session dir's pointer) and its subagents' files from where the journal last stopped, and
appends `hook: "agent-usage"` records under the session's task (`scripts/agent_usage.py` `record_spans`).

Held:
  * a Stop records the session's calls under its task, prints nothing (a Stop hook's stdout could carry a decision)
    and exits 0; its hook-journal line says so;
  * SessionEnd records too; with no path in the payload the session's pointer is read;
  * a session with no task records under "-";
  * a judge, tail-cert or monitor session records nothing (task 232 counts the judges' own usage);
  * a crash of the recorder exits 0, prints nothing, records nothing, and its journal line says it was skipped;
  * outside a playbook project nothing is created.

Every run uses a disposable HOME and project.

Run: python3 -m unittest tests.test_agent_usage_hook
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "plugins" / "playbook" / "scripts" / "agent-usage-hook"


def entry(mid, model="claude-opus-5-5", out=50):
    return {"type": "assistant", "isSidechain": False, "timestamp": "2026-10-19T00:00:00Z",
            "message": {"id": mid, "model": model, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 10,
                                                            "cache_read_input_tokens": 100, "output_tokens": out}}}


class _Project(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pb244-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = self.d / "home"
        (self.home / ".config" / "git").mkdir(parents=True)
        (self.home / ".gitconfig").write_text("")
        (self.home / ".config" / "git" / "config").write_text("")
        self.project = self.d / "proj"
        self.agent = self.project / ".agent"
        (self.agent / "tasks").mkdir(parents=True)
        self.sdir = self.agent / "sessions" / "s244"
        self.sdir.mkdir(parents=True)
        (self.sdir / "current_state").write_text("5\n")
        tdir = self.d / "host" / "-proj"
        tdir.mkdir(parents=True)
        self.transcript = tdir / "h1.jsonl"
        self.transcript.write_text("".join(json.dumps(e) + "\n" for e in (entry("m1"), entry("m1"), entry("m2"))))

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update({"HOME": str(self.home), "PLAYBOOK_SESSION_ID": "s244"})
        env.update(extra)
        return env

    def payload(self, event="Stop", path=True):
        p: dict = {"hook_event_name": event, "session_id": "h1", "cwd": str(self.project)}
        if path:
            p["transcript_path"] = str(self.transcript)
        if event == "Stop":
            p["stop_hook_active"] = False
        return p

    def run_hook(self, p, cwd=None, **env):
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(p), cwd=cwd or self.project, env=self.env(**env),
                              capture_output=True, text=True, timeout=60)

    def records(self):
        p = self.agent / "journal" / "enforcement.jsonl"
        if not p.exists():
            return []
        return [r for r in (json.loads(l) for l in p.read_text().splitlines() if l.strip())
                if r.get("hook") == "agent-usage"]

    def hook_lines(self):
        p = self.agent / "journal" / "hooks.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


class Recording(_Project):
    def test_a_stop_records_the_sessions_calls_under_its_task_and_prints_nothing(self):
        r = self.run_hook(self.payload())
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        recs = self.records()
        self.assertEqual([(x["task"], x["calls"], x["side"]) for x in recs], [("5", 2, 0)])
        line = [x for x in self.hook_lines() if x.get("hook") == "agent-usage"]
        self.assertEqual(len(line), 1)
        self.assertEqual(line[0]["decision"], "ok")
        self.assertIn("recorded 2 call", line[0]["detail"])

    def test_session_end_records_too_and_the_pointer_serves_when_the_payload_has_no_path(self):
        (self.sdir / "transcript_path").write_text(str(self.transcript) + "\n")
        r = self.run_hook(self.payload("SessionEnd", path=False))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual([x["calls"] for x in self.records()], [2])

    def test_a_second_stop_counts_only_what_is_new(self):
        self.run_hook(self.payload())
        with open(self.transcript, "a") as fh:
            fh.write(json.dumps(entry("m3")) + "\n")
        self.run_hook(self.payload())
        self.assertEqual([x["calls"] for x in self.records()], [2, 1])

    def test_a_session_with_no_task_records_under_a_dash(self):
        (self.sdir / "current_state").unlink()
        self.run_hook(self.payload())
        self.assertEqual([x["task"] for x in self.records()], ["-"])


class WhatItLeavesAlone(_Project):
    def test_a_judge_a_tail_cert_or_a_monitor_session_records_nothing(self):
        for env in ({"PLAYBOOK_SESSION_ID": "judge"}, {"PLAYBOOK_SESSION_ID": "tail-cert"},
                    {"PLAYBOOK_ROLE": "monitor"}):
            r = self.run_hook(self.payload(), **env)
            self.assertEqual((r.returncode, r.stdout), (0, ""), (env, r.stderr))
        self.assertEqual(self.records(), [])

    def test_a_crash_exits_zero_prints_nothing_records_nothing_and_says_skipped(self):
        shim = self.d / "shim"
        shim.mkdir()
        real = shutil.which("python3")
        (shim / "python3").write_text("#!/bin/bash\nfor a in \"$@\"; do case \"$a\" in *agent_usage.py) exit 3 ;; "
                                      f"esac; done\nexec '{real}' \"$@\"\n")
        (shim / "python3").chmod(0o755)
        r = self.run_hook(self.payload(), PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.records(), [])
        line = [x for x in self.hook_lines() if x.get("hook") == "agent-usage"]
        self.assertEqual(line[0]["decision"], "skip")

    def test_below_the_python_floor_it_says_so_and_records_nothing(self):
        shim = self.d / "shim39"
        shim.mkdir()
        real = shutil.which("python3")
        (shim / "python3").write_text("#!/bin/bash\nfor a in \"$@\"; do case \"$a\" in *'(3,10)'*) exit 1 ;; "
                                      "--version) echo 'Python 3.9.18'; exit 0 ;; esac; done\n"
                                      f"exec '{real}' \"$@\"\n")
        (shim / "python3").chmod(0o755)
        r = self.run_hook(self.payload(), PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertIn("needs python3 >= 3.10 (Python 3.9.18)", r.stderr)
        self.assertEqual(self.records(), [])

    def test_outside_a_playbook_project_nothing_is_created(self):
        other = self.d / "plain"
        other.mkdir()
        r = self.run_hook(self.payload(), cwd=other)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertFalse((other / ".agent").exists())


if __name__ == "__main__":
    unittest.main()
