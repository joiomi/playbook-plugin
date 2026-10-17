#!/usr/bin/env python3
"""A mind-map node is its titled line plus one fact per line under it, and every reader takes it so (task 226).

The owner, 2026-10-16 09:36 (task 218): "Liste scurte de la început (Recomandat)" — the template, the guide and the map
write one fact per line. The map skill used to require one line per node ("grep `^[5]` returns the entire node"). The
same map is written here both ways — one line per node, and `[N] **Title**` + `- ` fact lines — and every reader must
give the same answer on both; the CLI must not send a reader to grep a node.

Pure stdlib unittest. Run: python3 tests/test_map_one_fact_per_line.py
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAYBOOK = _HERE.parent / "plugins" / "playbook"
sys.path.insert(0, str(_PLAYBOOK))
from tasks import audit, core, mindmap, retro, template  # noqa: E402

ARROW = chr(0x2197)
NODES = [  # (id, title, summary?, facts)
    (1, "Overview", False, ["The probe project; see [2], [3] and [4].", "Facts sit one per line."]),
    (2, "Storage", True, ["`store.py` keeps the data; back to [1].", "It writes through one atomic helper."]),
    (3, "Rendering", False, ["`render.py` draws it; it uses [2].", f"The {ARROW} marker in prose is not a summary.",
                             "unique-word-zebra lives here."]),
    (4, "Network", False, ["`net.py` talks to the server; see [3].", "Retries three times."]),
]


def one_line(nodes=NODES, pad=0):
    out = ["# Mind Map — probe", ""]
    for nid, title, summ, facts in nodes:
        mark = f" {ARROW}" if summ else ""
        out.append(f"[{nid}] **{title}**{mark} - " + " ".join(facts + ["x" * pad] if pad else facts))
        out.append("")
    return "\n".join(out)


def multi_line(nodes=NODES, pad=0):
    out = ["# Mind Map — probe", ""]
    for nid, title, summ, facts in nodes:
        mark = f" {ARROW}" if summ else ""
        out.append(f"[{nid}] **{title}**{mark}")
        out += [f"- {f}" for f in facts + (["x" * pad] if pad else [])]
        out.append("")
    return "\n".join(out)


def _ref_integrity():
    spec = importlib.util.spec_from_file_location("ref_integrity_226", _PLAYBOOK / "skills" / "merge" / "ref-integrity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Project(unittest.TestCase):
    def project(self, main, overflow=None):
        d = Path(tempfile.mkdtemp(prefix="pb226m-"))
        self.addCleanup(shutil.rmtree, d, True)
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        (d / ".agent" / "tasks").mkdir(parents=True)
        (d / "home").mkdir()
        for name in ("store.py", "render.py", "net.py"):
            (d / name).write_text("x\n", encoding="utf-8")
        (d / "MIND_MAP.md").write_text(main, encoding="utf-8")
        if overflow is not None:
            (d / "MIND_MAP_OVERFLOW.md").write_text(overflow, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=d, check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=p", "commit", "-qm", "init"], cwd=d, check=True)
        return d

    def cli(self, d, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYBOOK_", "CLAUDE"))}
        env.update({"PYTHONPATH": str(_PLAYBOOK), "HOME": str(d / "home"), "PLAYBOOK_SESSION_ID": "pid-map-probe"})
        return subprocess.run([sys.executable, "-m", "tasks.cli", *args], cwd=d, env=env, capture_output=True, text=True,
                              timeout=120)


class EveryReaderReadsBothForms(Project):

    def test_the_node_parsers(self):
        for form in (one_line(), multi_line()):
            with self.subTest(form=form[:40]):
                self.assertEqual([(n, t) for n, t, _x in mindmap._iter_map_nodes(form)],
                                 [(1, "Overview"), (2, "Storage"), (3, "Rendering"), (4, "Network")])
                self.assertEqual(sorted(mindmap._parse_nodes(form)), [1, 2, 3, 4])
                ri = _ref_integrity()
                bodies = ri._node_bodies(form)
                self.assertEqual(sorted(bodies), [1, 2, 3, 4])
                self.assertEqual({n: ri._is_summary(b) for n, b in bodies.items()},
                                 {1: False, 2: True, 3: False, 4: False})

    def test_recall_prints_the_whole_node_and_only_it(self):
        d = self.project(multi_line())
        out = self.cli(d, "recall", "3").stdout
        for fact in NODES[2][3]:
            self.assertIn(fact, out)
        self.assertNotIn("net.py", out)
        self.assertIn("[3] Rendering", self.cli(d, "recall", "unique-word-zebra").stdout)

    def test_the_map_audits_agree(self):
        for name, check in (("wellformed", audit.check_mindmap_wellformed),
                            ("dangling", audit.check_mindmap_dangling_links),
                            ("stale", audit.check_mindmap_staleness)):
            got = []
            defects = NODES + [(5, "Five", False, ["Back to [1]; it cites `src/gone.py`, not in the tree."]),
                               (6, "Orphan", False, ["Nothing links here; it links [9]."]),
                               (2, "Storage again", False, ["A duplicate id."])]
            for form in (one_line(defects), multi_line(defects)):
                r = check(self.project(form)) or {}
                got.append((r.get("status"), r.get("output")))
            with self.subTest(check=name):
                self.assertEqual(got[0][0], "findings", got[0])     # each check has something to agree on
                self.assertEqual(got[0], got[1])

    def test_node_freshness_fires_alike_and_one_fresh_fact_refreshes_a_node(self):
        """The fourth map audit blames a node line by line: drift fires on both forms, and on the multi-line form
        editing one fact line is editing the node."""
        def run(form, refresh=False):
            d = Path(tempfile.mkdtemp(prefix="pb226f-"))
            self.addCleanup(shutil.rmtree, d, True)
            (d / "src").mkdir()
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@t")

            def commit(msg, date):
                e = dict(env, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
                subprocess.run(["git", "add", "-A"], cwd=d, env=e, check=True)
                subprocess.run(["git", "commit", "-qm", msg], cwd=d, env=e, check=True)
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            nodes = [(1, "Overview", False, ["The entry point, see [2]."]),
                     (2, "Storage", False, ["Persistence lives in src/store.py [1].", "It owns the cache."])]
            (d / "MIND_MAP.md").write_text(form(nodes), encoding="utf-8")
            (d / "src" / "store.py").write_text("cache = {}\n", encoding="utf-8")
            commit("init", "2020-01-01T00:00:00")
            for i, day in ((2, "02"), (3, "03")):
                (d / "src" / "store.py").write_text(f"cache = {{}}\n# v{i}\n", encoding="utf-8")
                commit(f"v{i}", f"2020-01-{day}T00:00:00")
            if refresh:
                nodes[1] = (2, "Storage", False, ["Persistence lives in src/store.py [1].", "It owns an LRU cache."])
                (d / "MIND_MAP.md").write_text(form(nodes), encoding="utf-8")
                commit("refresh one fact", "2020-01-04T00:00:00")
            return audit.check_mindmap_node_freshness(d)
        one, multi = run(one_line), run(multi_line)
        self.assertEqual(one["status"], "findings", one)
        self.assertEqual((one["status"], one["output"]), (multi["status"], multi["output"]))
        self.assertIn("[2]", multi["output"])
        self.assertEqual(run(multi_line, refresh=True)["status"], "clean")

    def test_mindmap_sync_reads_the_summary_from_the_title_line(self):
        """A fact line that mentions the marker does not make a full node a summary (R3) — so its drift is seen. A
        node written as one line is all title line: it reads as before (the marker anywhere on it), which keeps every
        existing map's verdict — measured on every MIND_MAP.md on the build machine, 764 nodes, none changed."""
        nodes = [n if n[0] != 3 else (3, "Rendering", False, n[3] + ["A newer fact."]) for n in NODES]
        drift = {}
        for form in (one_line, multi_line):
            out = self.cli(self.project(form(nodes), overflow=form()), "mindmap-sync").stdout
            drift[form.__name__] = out.split("Content drift", 1)[-1] if "Content drift" in out else ""
            self.assertIn("(2 full, 2 summary/" if form is one_line else "(3 full, 1 summary/", out)
        self.assertIn("[3]", drift["multi_line"])
        self.assertNotIn("[3]", drift["one_line"])

    def test_mindmap_sync_reads_the_later_of_two_nodes_with_one_id(self):
        """`map_node_spans` keeps both, in file order; `mindmap-sync` reads the later one, as it did before 226."""
        dup = multi_line() + "\n" + multi_line([(4, "Network", False, NODES[3][3] + ["A newer fact."])])[len("# Mind Map — probe\n\n"):]
        self.assertEqual([n for n, _t in mindmap.map_node_spans(dup)], [1, 2, 3, 4, 4])
        out = self.cli(self.project(dup, overflow=multi_line()), "mindmap-sync").stdout
        self.assertIn("[4]", out.split("Content drift", 1)[-1] if "Content drift" in out else "")

    def test_mindmap_sync_fix_keeps_a_summary_nodes_fuller_overflow(self):
        """The owner's data: a summary node (the marker on its title line) is shorter in MIND_MAP.md by design;
        `--fix` must leave its fuller overflow text alone, while a full node's drift is synced."""
        short = [n if n[0] != 2 else (2, "Storage", True, n[3][:1]) for n in NODES]
        short = [n if n[0] != 4 else (4, "Network", False, n[3] + ["A newer fact."]) for n in short]
        d = self.project(multi_line(short), overflow=multi_line())
        r = self.cli(d, "mindmap-sync", "--fix")
        self.assertEqual(r.returncode, 0, r.stderr)
        over = (d / "MIND_MAP_OVERFLOW.md").read_text(encoding="utf-8")
        self.assertIn("- It writes through one atomic helper.", over)
        self.assertIn("- A newer fact.", over)

    def test_mindmap_sync_fix_rewrites_the_overflow_node_whole(self):
        nodes = [n if n[0] != 4 else (4, "Network", False, n[3] + ["A newer fact."]) for n in NODES]
        d = self.project(multi_line(nodes), overflow=multi_line())
        r = self.cli(d, "mindmap-sync", "--fix")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- A newer fact.", (d / "MIND_MAP_OVERFLOW.md").read_text(encoding="utf-8"))

    def test_the_retro_counts_every_line_of_a_node(self):
        """R4: the size is the node's whole text (its `[N] ` prefix and trailing blanks off), up to the next node or
        the first heading — a `## Legacy` tail is not the last node's."""
        d = self.project(multi_line() + "\n## Legacy\n\n" + "old note " * 40 + "\n")
        nodes = {n["id"]: n for n in retro.extract_mindmap(d / "MIND_MAP.md")}
        body3 = "**Rendering**\n" + "\n".join(f"- {f}" for f in NODES[2][3])
        self.assertEqual(nodes[3]["size"], len(body3.encode("utf-8")))
        self.assertNotIn("old note", nodes[4]["text"])

    def test_the_bootstrap_index_and_its_hint(self):
        """Over the bootstrap budget, with no overflow file: each node listed once, and the hint is `tasks recall`."""
        more = NODES + [(n, f"Extra{n}", False, [f"`net.py` again, see [{n - 1}].", "One more fact."]) for n in range(5, 9)]
        d = self.project(multi_line(more, pad=1500))
        out = mindmap._bootstrap_mind_map(d) or ""
        self.assertIn("MIND MAP INDEX", out)
        for nid, title, _s, _f in more:
            self.assertEqual(out.count(f"[{nid}] {title}") + out.count(f"[{nid}] **{title}**"), 1, (nid, out[:400]))
        self.assertIn("tasks recall", out)
        self.assertNotIn("grep '^", out)

    def test_the_judges_trim_names_recall(self):
        text = multi_line(pad=4000)
        out = mindmap._trim_mind_map_by_node(text, 9000) or ""
        self.assertIn("omitted", out)
        self.assertIn("tasks recall", out)
        self.assertNotIn("grep '^", out)
        kept = [n for n, _t, _x in mindmap._iter_map_nodes(out)]
        for n, _t, full in mindmap._iter_map_nodes(text):
            if n in kept:
                self.assertIn(full.strip(), out)        # a kept node is kept whole


class TheFormatsRulesHold(Project):
    """Each line the format forbids among a node's facts does what the format says it does (W4)."""

    def test_a_fact_that_begins_with_a_link_starts_a_node(self):
        text = multi_line().replace("- Facts sit one per line.", "[5] is where facts go.")
        self.assertIn(5, [n for n, _t, _x in mindmap._iter_map_nodes(text)])
        self.assertIn(5, dict(mindmap.map_node_spans(text)))
        r = audit.check_mindmap_wellformed(self.project(text))
        self.assertEqual(r["status"], "findings", r)
        self.assertIn("node [5] has no **bold title**", r["output"])

    def test_readers_disagree_on_where_a_node_with_a_heading_ends(self):
        text = multi_line().replace("- Facts sit one per line.", "## Notes\n- after-the-heading")
        spans = dict(mindmap.map_node_spans(text))              # mindmap-sync and the retro: the heading ends it
        self.assertNotIn("after-the-heading", spans[1])
        self.assertNotIn("after-the-heading", _ref_integrity()._node_bodies(text)[1])
        recall = {n: full for n, _t, full in mindmap._iter_map_nodes(text)}   # recall: it does not
        self.assertIn("after-the-heading", recall[1])

    def test_a_fence_keeps_a_link_line_inside_its_node_and_an_open_one_stops_fix(self):
        fenced = multi_line().replace("- Facts sit one per line.", "```\n[9] **Not a node**\n```")
        self.assertEqual([n for n, _t, _x in mindmap._iter_map_nodes(fenced)], [1, 2, 3, 4])
        self.assertIn("[9] **Not a node**", dict(mindmap.map_node_spans(fenced))[1])
        comment = multi_line().replace("- Facts sit one per line.", "```\n# a comment, not a heading\n```\n- after-the-fence")
        self.assertIn("after-the-fence", dict(mindmap.map_node_spans(comment))[1])   # a fenced `#` does not end it
        open_fence = multi_line().replace("- Facts sit one per line.", "```")
        d = self.project(open_fence, overflow=multi_line())
        before = (d / "MIND_MAP_OVERFLOW.md").read_bytes()
        r = self.cli(d, "mindmap-sync", "--fix")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("unmatched code fence", r.stderr)
        self.assertEqual((d / "MIND_MAP_OVERFLOW.md").read_bytes(), before)

    def test_a_blank_line_inside_a_node_does_not_split_it(self):
        """The format asks for none (one blank line parts two nodes); a reader still reads to the next title line."""
        text = multi_line().replace("- Facts sit one per line.", "\n- after-a-blank")
        self.assertEqual([n for n, _t, _x in mindmap._iter_map_nodes(text)], [1, 2, 3, 4])
        self.assertIn("after-a-blank", dict(mindmap.map_node_spans(text))[1])
        out = self.cli(self.project(text), "recall", "1").stdout
        self.assertIn("after-a-blank", out)


class EveryRenderReadsAlike(unittest.TestCase):
    """R5: the guide's text moved out of the renders (task 213); each render's state reads the same to the CLI and to
    the hooks' reader."""

    def test_status_risk_and_gates(self):
        script = _PLAYBOOK / "scripts" / "task-status.py"
        for ty in [None, "quick", "light", *core.PLAYBOOKS]:
            text = template.render_template(1, "X", ty)
            d = Path(tempfile.mkdtemp(prefix="pb226r-"))
            self.addCleanup(shutil.rmtree, d, True)
            f = d / "task.md"
            f.write_text(text, encoding="utf-8")
            unchecked, total, first = core._live_gate_state(core._physical_lines(text))
            r = subprocess.run([sys.executable, str(script), "--fields", str(f)], capture_output=True, text=True)
            with self.subTest(type=ty):
                self.assertEqual(r.returncode, 0, r.stderr)
                status, count, gate, _release = r.stdout.split("\n")[:4]
                self.assertEqual((status, int(count), gate), (core._extract_status(f), unchecked, first))
                self.assertEqual(status, "pending")
                self.assertGreater(total, 0)
                self.assertEqual(core.extract_risk_from_text(text), "unclassified")


class NoInstructionSaysOneLinePerNode(unittest.TestCase):
    """Q6: no instruction file, and no string the CLI prints, says a node is one line or greps for one."""

    FILES = ["skills/playbook/mindmap.md", "skills/playbook/mindmap-gen.md", "skills/playbook/SKILL.md",
             "commands/mindmap.md", "commands/mindmap-optimize.md", "skills/merge/SKILL.md"]
    BANNED = (re.compile(r"single[- ]line", re.I), re.compile(r"grep-friendly", re.I),
              re.compile(r"one line per node", re.I), re.compile(r"one-line `?" + ARROW),
              re.compile(r"grep [\"']\^\\?\["), re.compile(r"grep `\^\["))

    def _assert_clean(self, name, text):
        for pat in self.BANNED:
            with self.subTest(source=name, pattern=pat.pattern):
                self.assertIsNone(pat.search(text), name)

    def test_the_instruction_files(self):
        for rel in self.FILES:
            self._assert_clean(rel, (_PLAYBOOK / rel).read_text(encoding="utf-8"))
        self._assert_clean("docs/cli.md", (_HERE.parent / "docs" / "cli.md").read_text(encoding="utf-8"))

    def test_the_strings_the_cli_prints(self):
        self._assert_clean("mind_map_header", template.mind_map_header())
        self._assert_clean("trim notice", mindmap._omitted_nodes_notice([3], 9))
        d = Path(tempfile.mkdtemp(prefix="pb226w-"))
        self.addCleanup(shutil.rmtree, d, True)
        more = NODES + [(n, f"Extra{n}", False, [f"see [{n - 1}].", "One more fact."]) for n in range(5, 9)]
        (d / "MIND_MAP.md").write_text(multi_line(more, pad=1500), encoding="utf-8")
        self._assert_clean("bootstrap index", mindmap._bootstrap_mind_map(d) or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
