#!/usr/bin/env python3
"""Model detection and model-pin checks, driven end to end at the process boundary (task 184).

`tasks models detect`, `tasks models check` and `tasks panel-review` run as a user runs them,
with stand-in `claude` and `agy` CLIs on PATH (`tests/_judge_stub.py`) and a scratch HOME —
which has the user's two git settings files (empty), or every launch under `/tmp` is refused
(task 181's bound 16). Two ledger rows rest on what is observed here:

  * PB-PROVIDER-CAPABILITY-DEGRADE — the report says how each provider's model is pinned:
    agy's entry lists the ids `agy models` prints, says to pin a seat as `agy:<id>`, names it an
    experimental judge seat that `models check` live-probes; the CONTROL — no agy on PATH — says
    it is not installed and lists nothing;
  * PB-MODEL-AVAILABILITY — a pin is not called available from a list: `--no-probe` says
    UNPROBED, a probe that answers says OK; a probe that the CLI rejects says GONE and the command
    fails; in a panel a seat that fails with the removed-model text AND whose probe confirms it
    stops the run (HARD STOP, exit 1, the failure kept in judge.md); budget and timeout texts are
    never read as a removed model; the CONTROL — the same removed-model text in the review but a
    probe that answers — does not stop the run.

The panel cases need bubblewrap that can start a sandbox. Run:
    python3 -m unittest tests.test_model_pins_integration
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tests._bwrap_standin import bwrap_usable  # noqa: E402
from tests._judge_stub import TASKS, clean_env, stub_bin  # noqa: E402

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"   # the shipped aliases' ids (task 203, #44)


class _Scratch(unittest.TestCase):
    agy = True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        home = base / "home"
        (home / ".config" / "git").mkdir(parents=True)
        (home / ".gitconfig").write_text("", encoding="utf-8")
        (home / ".config" / "git" / "config").write_text("", encoding="utf-8")
        self.p = base / "proj"
        self.p.mkdir()
        env = clean_env(HOME=str(home))
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.p, check=True, env=env)
        (self.p / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.p, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "c0"], cwd=self.p, check=True, env=env)
        (self.p / ".agent" / "tasks").mkdir(parents=True)
        (self.p / ".agent" / "models.json").write_text(
            json.dumps({"panel": ["opus", "sonnet", "haiku"], "default_judge": "sonnet"}), encoding="utf-8")
        out = base / "stub-out"
        out.mkdir()
        # only the stand-ins and the system's own tools: no real provider CLI is reachable
        self.env = clean_env(HOME=str(home), PB_STUB_DIR=str(out),
                             PATH=f"{stub_bin(base / 'bin', agy=self.agy)}{os.pathsep}/usr/bin{os.pathsep}/bin")
        r = self.tasks("new", "light", "probe", "a probe task")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tasks(self, *args, **extra):
        return subprocess.run([str(TASKS), *args], cwd=self.p, env={**self.env, **extra}, capture_output=True,
                              text=True, timeout=600)

    def verdict_of(self, out: str, pin: str) -> str:
        line = next((x for x in out.splitlines() if x.strip().startswith(pin + " ")), "")
        self.assertTrue(line, out)
        return line.split()[1]


class TheReportSaysHowEachModelIsPinned(_Scratch):
    def test_agy_lists_its_ids_and_how_to_pin_them(self):
        r = self.tasks("models", "detect")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        agy = r.stdout.split("[agy]", 1)[1].split("\n[", 1)[0]
        self.assertIn("installed", agy.splitlines()[0])
        self.assertIn("pin a seat as agy:<id>", agy)
        self.assertIn("experimental judge seat", agy)
        self.assertIn("live-probes", agy)
        self.assertIn("gemini-3.8-flash-high", agy)
        self.assertIn("gemini-3.8-flash-medium", agy)
        claude = r.stdout.split("[claude]", 1)[1].split("\n[", 1)[0]
        self.assertIn("no effort knob", claude)          # claude's pin carries no effort: said, not implied


class TheReportWithoutAgy(_Scratch):
    agy = False

    def test_control_without_agy_it_is_not_installed_and_lists_nothing(self):
        r = self.tasks("models", "detect")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        agy = r.stdout.split("[agy]", 1)[1].split("\n[", 1)[0]
        self.assertIn("not installed", agy.splitlines()[0])
        self.assertNotIn("gemini-", agy)


class APinIsCheckedNotAssumed(_Scratch):
    def test_without_a_probe_a_pin_is_unprobed_not_ok(self):
        r = self.tasks("models", "check", "--no-probe")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for pin in ("opus", "sonnet", "haiku"):
            self.assertEqual(self.verdict_of(r.stdout, pin), "UNPROBED")

    def test_a_probe_that_answers_is_ok(self):
        r = self.tasks("models", "check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for pin in ("opus", "sonnet", "haiku"):
            self.assertEqual(self.verdict_of(r.stdout, pin), "OK")

    def test_a_probe_the_cli_rejects_is_gone_and_the_command_fails(self):
        r = self.tasks("models", "check", PB_STUB_GONE_MODELS=HAIKU)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.verdict_of(r.stdout, "haiku"), "GONE")
        self.assertEqual(self.verdict_of(r.stdout, "sonnet"), "OK")
        self.assertIn("tasks models select", r.stderr)


@unittest.skipUnless(bwrap_usable(), "no bubblewrap that can start a sandbox here")
class APanelStopsOnlyOnAConfirmedRemovedModel(_Scratch):
    def judge_md(self) -> str:
        return (self.p / ".agent" / "tasks" / "001-probe" / "judge.md").read_text(encoding="utf-8")

    def test_a_removed_model_confirmed_by_its_probe_stops_the_run(self):
        r = self.tasks("panel-review", "1", "--mode", "impl", PB_STUB_GONE_MODELS=HAIKU)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("PANEL PASS: 2/3", r.stdout)               # the quorum held — and still it stops
        self.assertIn("HARD STOP: judge pin(s) unavailable (probe-confirmed)", r.stderr)
        self.assertIn(f"claude:{HAIKU}: GONE", r.stderr)
        self.assertIn("There's an issue with the selected model", self.judge_md())   # the failure is kept

    def test_control_the_same_text_with_a_probe_that_answers_does_not_stop_it(self):
        r = self.tasks("panel-review", "1", "--mode", "impl", PB_STUB_GONE_IN_REVIEW_ONLY=HAIKU)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PANEL PASS: 2/3", r.stdout)
        self.assertNotIn("HARD STOP", r.stderr)
        self.assertIn("There's an issue with the selected model", self.judge_md())

    def test_budget_and_timeout_texts_are_never_a_removed_model(self):
        r = self.tasks("panel-review", "1", "--mode", "impl", PB_STUB_BUDGET_MODELS=HAIKU,
                       PB_STUB_TIMEOUT_MODELS=SONNET)
        self.assertNotIn("HARD STOP", r.stderr)
        self.assertNotIn("GONE", r.stderr)
        self.assertIn("PANEL FAIL: 1/3", r.stdout)               # two seats failed: the quorum says so, not a removal
        md = self.judge_md()
        self.assertIn("Exceeded USD budget", md)
        self.assertIn("Request timed out", md)


if __name__ == "__main__":
    unittest.main()
