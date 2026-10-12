#!/usr/bin/env python3
"""The shipped runtime needs nothing but Python's standard library (task 184, PLAN S11).

PB-RUNTIME-STDLIB: "Shipped Playbook runtime requires no third-party Python packages, no build
step, and uses plain Bash/Python/Markdown files." Three things are observed here, each with the
control that shows the observation could fail:

  * every import in every shipped Python file — modules and the scripts whose first line runs
    python — names a standard-library module or one of the plugin's own; the CONTROL feeds the
    same scan a file that imports a third-party package and sees it named;
  * a task's round trip through the real CLI (new, list, work, status, audit) runs with every
    `python3` the CLI starts unable to see any site-packages (`-S`, through a `python3` on PATH
    that adds it); the CONTROL shows that interpreter really cannot import a package this host
    has in site-packages;
  * every shipped file is text of a plain kind — Python, Bash, Markdown, JSON, text, a template —
    with nothing compiled or binary; the CONTROL sees a binary file named.

The checked-out tree runs as it is: there is no build step to run before any of this.

Run: python3 -m unittest tests.test_runtime_stdlib_integration
"""
from __future__ import annotations

import ast
import os
import shutil
import site
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "playbook"
# plain text kinds: Python, Bash and Zsh (the zsh logger), Markdown, JSON, text, templates — and `.ts`: the hook
# bridge the experimental pi agent loads itself (`pi -e …`), TypeScript source, never compiled by the plugin
PLAIN = {".py", ".sh", ".zsh", ".md", ".json", ".jsonl", ".txt", ".template", ".toml", ".yml", ".yaml", ".cfg",
         ".ini", ".ts"}
# standard-library modules newer than this interpreter may be: `tomllib` (3.11) — imported by the codex adapter
# inside a guard (task 184 parks that it reads as "off" on 3.10); stdlib, not a third-party package
NEWER_STDLIB = {"tomllib"}


def shipped_files(root: Path = PLUGIN) -> "list[Path]":
    return [p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts]


def is_python(p: Path) -> bool:
    if p.suffix == ".py":
        return True
    with p.open("rb") as fh:
        head = fh.readline()
    return head.startswith(b"#!") and b"python" in head


def own_names(root: Path = PLUGIN) -> "set[str]":
    """Every module and package name the plugin itself provides, wherever it sits on its paths."""
    names = set()
    for p in shipped_files(root):
        if p.suffix == ".py":
            names.add(p.stem)
            names.update(part for part in p.relative_to(root).parts[:-1])
    return names


def foreign_imports(files: "list[Path]", own: "set[str]") -> "list[str]":
    allowed = set(sys.stdlib_module_names) | NEWER_STDLIB | own | {"__future__", "__main__"}
    bad = []
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for n in names:
                if n.split(".")[0] not in allowed:
                    bad.append(f"{p}:{node.lineno}: {n}")
    return bad


def not_plain(files: "list[Path]") -> "list[str]":
    bad = []
    for p in files:
        data = p.read_bytes()
        if b"\0" in data[:65536]:
            bad.append(f"{p}: binary")
        elif p.suffix and p.suffix not in PLAIN:
            bad.append(f"{p}: {p.suffix}")
        elif not p.suffix and not data.startswith(b"#!") and p.name not in ("LICENSE", "VERSION"):
            bad.append(f"{p}: no suffix and no interpreter line")
    return bad


class EveryImportIsTheStandardLibraryOrThePlugins(unittest.TestCase):
    def test_no_shipped_python_file_imports_a_third_party_package(self):
        files = [p for p in shipped_files() if is_python(p)]
        self.assertGreater(len(files), 50)
        self.assertEqual(foreign_imports(files, own_names()), [])

    def test_control_the_scan_names_a_third_party_import(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "uses_requests.py"
            f.write_text("import os\nimport requests\nfrom yaml import safe_load\n", encoding="utf-8")
            self.assertEqual([x.rsplit(": ", 1)[1] for x in foreign_imports([f], own_names())], ["requests", "yaml"])


class TheCliRunsWithNoSitePackages(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        real = shutil.which("python3")
        assert real is not None, "no python3 on PATH"
        self.real = os.path.realpath(real)
        wrap = base / "bin"
        wrap.mkdir()
        (wrap / "python3").write_text(f'#!/bin/sh\nexec "{self.real}" -S "$@"\n', encoding="utf-8")
        (wrap / "python3").chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("PLAYBOOK_", "CLAUDE_CODE_", "CLAUDECODE")) and k not in ("BASH_ENV", "PYTHONPATH")}
        self.env["PATH"] = f"{wrap}{os.pathsep}{self.env.get('PATH', '')}"
        self.env["PLAYBOOK_SESSION_ID"] = "pid-stdlib-roundtrip"
        self.p = base / "proj"
        self.p.mkdir()

    def py(self, code: str) -> subprocess.CompletedProcess:
        return subprocess.run(["python3", "-c", code], env=self.env, capture_output=True, text=True, timeout=60)

    def test_the_interpreter_on_path_sees_no_site_packages(self):
        r = self.py("import sys; print([p for p in sys.path if 'site-packages' in p or 'dist-packages' in p])")
        self.assertEqual(r.stdout.strip(), "[]", r.stderr)

    def test_a_tasks_round_trip_runs(self):
        tasks = str(PLUGIN / "scripts" / "tasks")
        for args in (["new", "quick", "smoke", "a smoke task"], ["list"], ["work", "1"], ["status"], ["audit"]):
            with self.subTest(args=args):
                r = subprocess.run([tasks, *args], cwd=self.p, env=self.env, capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stdout + r.stderr)
                self.assertNotIn("ModuleNotFoundError", r.stdout + r.stderr)

    def test_control_a_package_this_host_has_in_site_packages_cannot_be_imported(self):
        candidate = None
        for d in site.getsitepackages():
            for entry in sorted(Path(d).iterdir()) if Path(d).is_dir() else []:
                if entry.is_dir() and (entry / "__init__.py").exists() and entry.name.isidentifier() \
                        and entry.name not in sys.stdlib_module_names:
                    candidate = entry.name
                    break
            if candidate:
                break
        if candidate is None:
            self.skipTest("this host has no package in site-packages to hide")
        plain = subprocess.run([self.real, "-c", f"import {candidate}"], capture_output=True, text=True, timeout=60,
                               env={k: v for k, v in self.env.items() if k != "PATH"} | {"PATH": os.environ.get("PATH", "")})
        self.assertEqual(plain.returncode, 0, f"{candidate}: {plain.stderr}")
        hidden = self.py(f"import {candidate}")
        self.assertNotEqual(hidden.returncode, 0)
        self.assertIn("ModuleNotFoundError", hidden.stderr)


class EveryShippedFileIsPlainText(unittest.TestCase):
    def test_nothing_compiled_or_binary_is_shipped(self):
        self.assertEqual(not_plain(shipped_files()), [])

    def test_control_a_binary_file_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "helper.so").write_bytes(b"\x7fELF\x02\x01\x01\0\0\0")
            (Path(td) / "notes.md").write_text("# plain\n", encoding="utf-8")
            self.assertEqual(len(not_plain(shipped_files(Path(td)))), 1)


if __name__ == "__main__":
    unittest.main()
