#!/usr/bin/env python3
"""`init` writes CLAUDE.md + .gitignore mechanically, merging never clobbering (F15).

Gauntlet F15: those two files were the AGENT half of /playbook:init — the
doctrine held only if the agent performed it (it did on the real project;
the fragility is the finding). The mechanical write guarantees it.

The hard requirement from batch-1: init on a project with a SEEDED CLAUDE.md
(an owner's pointer paragraph above everything) must MERGE — pointer preserved,
template sections added/updated — never clobber. That shape was verified live
in the field and is the negative control here, plus: template-owned sections
refresh on re-init while custom sections and preambles survive byte-for-byte,
runs are idempotent, and .gitignore appends its marker-guarded block exactly
once without touching existing content.

Covers the module (unit) and the real `scripts/init` run (fixture, isolated
HOME so ~/.claude is never mutated — the S14 lesson).

Run: python3 -m unittest tests.test_init_claude_md
"""
from __future__ import annotations

import importlib.util
import os
from collections import Counter
import subprocess
from tests._bashcheck import bash_or_skip
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PLUGIN = _HERE.parent / "plugins/playbook"
SCRIPTS = PLUGIN / "scripts"

_spec = importlib.util.spec_from_file_location(
    "claude_md_merge", SCRIPTS / "claude-md-merge.py")
cmm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmm)

TEMPLATE = (SCRIPTS / "CLAUDE.md.template").read_text(encoding="utf-8")

SEEDED = """# StrataDB

**Read `PURPOSE.md` first** — this project's role and your journal duty.

## Dev Tooling

pyright is installed; stdlib-only at runtime.
"""


class MergeClaudeMd(unittest.TestCase):
    def test_fresh_write_substitutes_name_and_strips_header(self):
        out = cmm.merge_claude_md(TEMPLATE, None, "My Proj")
        self.assertTrue(out.startswith("# My Proj\n"), out[:40])
        self.assertNotIn("PLAYBOOK TEMPLATE", out)
        self.assertIn("## Correctness Contract", out)

    def test_seeded_pointer_survives_and_sections_append(self):
        out = cmm.merge_claude_md(TEMPLATE, SEEDED, "StrataDB")
        # The owner's seed stays at the very top, byte-for-byte.
        self.assertTrue(out.startswith("# StrataDB\n"))
        self.assertIn("**Read `PURPOSE.md` first**", out)
        self.assertLess(out.index("PURPOSE.md"), out.index("## Start Here"))
        # Custom section preserved; template sections all present.
        self.assertIn("## Dev Tooling\n\npyright is installed", out)
        for h in ("## Start Here", "## Task Lifecycle", "## Correctness Contract",
                  "## CLI", "## Don't"):
            self.assertIn(h, out)

    def test_template_owned_section_updates_in_place(self):
        stale = ("# P\n\nkeep me\n\n## Correctness Contract\n\nOLD 1.5.0 WORDING\n\n"
                 "## My Rules\n\nnever bulk-surgery source\n")
        out = cmm.merge_claude_md(TEMPLATE, stale, "P")
        self.assertNotIn("OLD 1.5.0 WORDING", out)
        self.assertIn("the WORLD reverts", out)  # current template body landed
        self.assertIn("## My Rules\n\nnever bulk-surgery source", out)
        # Updated in place: contract stays BEFORE the custom section.
        self.assertLess(out.index("## Correctness Contract"), out.index("## My Rules"))

    def test_idempotent(self):
        once = cmm.merge_claude_md(TEMPLATE, SEEDED, "StrataDB")
        twice = cmm.merge_claude_md(TEMPLATE, once, "StrataDB")
        self.assertEqual(once, twice)

    def test_clobber_is_impossible_for_unrelated_content(self):
        # Negative control: a CLAUDE.md with NO template sections at all comes
        # through with every original byte still present.
        original = "# Legacy\n\nhand-written rules the owner cares about\n\n## Ops\n\nrestart with systemctl\n"
        out = cmm.merge_claude_md(TEMPLATE, original, "Legacy")
        self.assertIn("hand-written rules the owner cares about", out)
        self.assertIn("## Ops\n\nrestart with systemctl", out)


WORKSPACE_FIXTURE = _HERE / "fixtures" / "playbook-plugin-dev_be73740_CLAUDE.md"


def _project_part(text: str) -> str:
    """Everything from the thematic break above `# Project:` to the end."""
    return text[text.index("---\n\n# Project:"):]


class ProjectPartAfterTemplateSections(unittest.TestCase):
    """Task 093 (PLAN S7b): a level-1 `#` heading below a template section opens the
    project's own part — in the plugin's own dev workspace: `---`, `# Project:
    playbook-plugin-dev`, its paragraph, then custom `##` sections. `split_sections`
    split only on `## `, so that part rode inside the `## Don't` body and a template
    refresh of `## Don't` deleted it, while init printed "project content preserved".
    The fixture is that workspace's CLAUDE.md as committed (outer be73740)."""

    def setUp(self):
        self.fixture = WORKSPACE_FIXTURE.read_text(encoding="utf-8")

    def test_workspace_project_part_survives_merge(self):
        out = cmm.merge_claude_md(TEMPLATE, self.fixture, "playbook-plugin-dev")
        # the whole part, byte for byte: the break, the `#` heading, its paragraph and
        # every section below it
        self.assertIn(_project_part(self.fixture), out)
        self.assertLess(out.index("## Don't"), out.index("# Project: playbook-plugin-dev"))

    def test_workspace_merge_is_idempotent(self):
        once = cmm.merge_claude_md(TEMPLATE, self.fixture, "playbook-plugin-dev")
        self.assertEqual(cmm.merge_claude_md(TEMPLATE, once, "playbook-plugin-dev"), once)

    def test_level1_part_without_a_break_survives_and_the_section_above_still_refreshes(self):
        stale = "# P\n\n## CLI\n\nOLD CLI WORDING\n# Second part\nkeep this line\n"
        out = cmm.merge_claude_md(TEMPLATE, stale, "P")
        self.assertIn("# Second part\nkeep this line\n", out)
        self.assertNotIn("OLD CLI WORDING", out)

    def test_crlf_project_part_survives(self):
        crlf = ("# P\r\n\r\n## Don't\r\n\r\n- old\r\n\r\n---\r\n\r\n# Mine\r\n\r\n"
                "keep crlf\r\n\r\n## Mine too\r\n\r\nstill here\r\n")
        out = cmm.merge_claude_md(TEMPLATE, crlf, "P")
        self.assertIn("---\r\n\r\n# Mine\r\n\r\nkeep crlf\r\n", out)
        self.assertIn("## Mine too\r\n\r\nstill here\r\n", out)

    def test_fenced_level2_line_in_a_custom_section_is_text(self):
        # a `## CLI` line inside a fence used to start a "section" named like the
        # template's, whose fenced text the template then replaced
        custom = "# P\n\n## My Rules\n\n```md\n## CLI\nfake cli text\n```\n"
        out = cmm.merge_claude_md(TEMPLATE, custom, "P")
        self.assertIn("## My Rules\n\n```md\n## CLI\nfake cli text\n```\n", out)

    def test_fenced_hash_comment_in_a_template_section_does_not_split_it(self):
        # control for the fix: a `# comment` in a bash fence is not a level-1 heading,
        # so the stale template section is still refreshed WHOLE (no fence half left)
        stale = "# P\n\n## Don't\n\n```bash\n# stale comment\necho x\n```\n"
        out = cmm.merge_claude_md(TEMPLATE, stale, "P")
        self.assertNotIn("stale comment", out)
        self.assertEqual(out.count("```") % 2, 0, "unbalanced fence left behind")
        self.assertEqual(cmm.merge_claude_md(TEMPLATE, out, "P"), out)

    def test_unclosed_fence_fences_nothing(self):
        # a fence that never closes must not turn the headings below it into text:
        # the refreshed template section would then swallow the project's sections
        x = "# P\n\n## CLI\n\n```bash\nnever closed\n\n## My Rules\n\nkeep my rules\n"
        out = cmm.merge_claude_md(TEMPLATE, x, "P")
        self.assertIn("## My Rules\n\nkeep my rules\n", out)

    # --- panel round 1 (task 093) -------------------------------------------------

    def test_template_named_section_inside_the_project_part_is_kept(self):
        # V1: a project's own `## CLI` under its `#` part is not the template's
        x = "# P\n\n## CLI\n\nOLD\n\n---\n\n# Mine\n\n## CLI\n\ncustom cli notes\n"
        out = cmm.merge_claude_md(TEMPLATE, x, "P")
        self.assertIn("# Mine\n\n## CLI\n\ncustom cli notes\n", out)
        self.assertNotIn("OLD", out)
        self.assertEqual(out.count("## CLI"), 2)          # the template's once + the project's
        self.assertEqual(cmm.merge_claude_md(TEMPLATE, out, "P"), out)

    def test_template_section_appended_below_a_project_part_is_idempotent(self):
        # V1's other half: sections the file lacks are appended at the END, i.e. below a
        # trailing project part; the next merge must own them there, not append again
        x = "# P\n\n## Don't\n\n- x\n\n---\n\n# Mine\n\nmine\n"
        once = cmm.merge_claude_md(TEMPLATE, x, "P")
        self.assertEqual(once.count("## Correctness Contract"), 1)
        self.assertEqual(cmm.merge_claude_md(TEMPLATE, once, "P"), once)

    def test_crlf_file_stays_crlf_when_a_template_section_refreshes(self):
        # V2: the joiner and the refreshed template text used to be LF
        crlf = "# P\r\n\r\n## Don't\r\n\r\n- stale\r\n\r\n---\r\n\r\n# Mine\r\n\r\nkeep crlf\r\n"
        out = cmm.merge_claude_md(TEMPLATE, crlf, "P")
        self.assertNotIn("- stale", out)
        self.assertEqual(out.count("\n"), out.count("\r\n"), "a lone LF in a CRLF file")
        self.assertEqual(cmm.merge_claude_md(TEMPLATE, out, "P"), out)

    def test_setext_underline_above_a_project_part_is_not_moved(self):
        # V3: `text\n---` is a Setext heading, not a thematic break to carry
        # (the template sections this file lacks are inserted above `# Part`, so the
        # check is that the underline stays glued to its text, not what follows it)
        x = "# P\n\n## My Notes\n\nProject notes\n---\n\n# Part\n\nx\n"
        out = cmm.merge_claude_md(TEMPLATE, x, "P")
        self.assertIn("## My Notes\n\nProject notes\n---\n\n", out)
        self.assertNotIn("Project notes\n\n---", out)
        self.assertIn("# Part\n\nx\n", out)

    def test_hash_line_inside_an_html_comment_does_not_split_a_template_section(self):
        # V4: a closed `<!-- -->` comment is text, like a closed fence
        x = "# P\n\n## CLI\n\nSTALE ONE\n<!--\n# Example\n-->\nSTALE TWO\n"
        out = cmm.merge_claude_md(TEMPLATE, x, "P")
        self.assertNotIn("STALE", out)
        self.assertNotIn("# Example", out)

    def test_backtick_in_the_info_string_is_not_a_fence(self):
        # V5: CommonMark — a backtick fence's info string cannot contain a backtick
        x = "# P\n\n## CLI\n\n```foo`bar\n\n## My Rules\n\nkeep my rules\n\n```\n"
        out = cmm.merge_claude_md(TEMPLATE, x, "P")
        self.assertIn("## My Rules\n\nkeep my rules\n", out)

    def test_a_template_with_a_level1_line_does_not_crash(self):
        # V6: defensive — the shipped template has no `#` below its first `##`
        tmpl = TEMPLATE.rstrip("\n") + "\n\n# Appendix\n\ntext\n"
        out = cmm.merge_claude_md(tmpl, SEEDED, "StrataDB")
        self.assertIn("## Dev Tooling", out)

    # --- panel round 2 (task 093) -------------------------------------------------

    def test_template_named_section_only_inside_the_project_part_is_kept(self):
        # X1: no earlier template `## CLI` — the project's own is still its text, and the
        # missing template section is inserted ABOVE the part, not inside it
        x = "# P\n\n## Don't\n\n- x\n\n---\n\n# Mine\n\n## CLI\n\nPRIVATE CLI\n"
        out = cmm.merge_claude_md(TEMPLATE, x, "P")
        self.assertIn("# Mine\n\n## CLI\n\nPRIVATE CLI\n", out)
        self.assertEqual(out.count("## CLI"), 2)
        self.assertLess(out.index("## Correctness Contract"), out.index("# Mine"))
        self.assertEqual(cmm.merge_claude_md(TEMPLATE, out, "P"), out)

    def test_spaced_dash_break_is_carried_not_taken_for_a_setext_underline(self):
        # X3: `- - -` under a text line is a thematic break; inside a refreshed template
        # section it would be deleted with the section if it stayed there
        x = "# P\n\n## Don't\n\n- stale\n- - -\n\n# Mine\n\nkeep\n"
        out = cmm.merge_claude_md(TEMPLATE, x, "P")
        self.assertIn("- - -\n\n# Mine\n\nkeep\n", out)
        self.assertNotIn("- stale", out)

    def test_hash_without_space_is_not_a_heading(self):
        stale = "# P\n\n## CLI\n\nold\n#nospace belongs to CLI\n"
        out = cmm.merge_claude_md(TEMPLATE, stale, "P")
        self.assertNotIn("#nospace", out)


class MergeGitignore(unittest.TestCase):
    def test_created_when_absent(self):
        out = cmm.merge_gitignore(None)
        self.assertIn(cmm.GITIGNORE_MARKER, out)
        self.assertIn(".agent/sessions/", out)
        self.assertIn(".agent/*/sessions/", out)  # multi-user lanes covered
        self.assertIn(".agent/models.json", out)
        self.assertIn(".agent/model-catalog.json", out)   # task 054: the dashboard's catalog baseline, machine-local like models.json
        # T2: the enforcement journal is machine-local runtime state (the block
        # predated it), both root and per-user lanes.
        self.assertIn(".agent/journal/", out)
        self.assertIn(".agent/*/journal/", out)

    def test_appends_once_preserving_content(self):
        existing = "# mine\n__pycache__/\n"
        out = cmm.merge_gitignore(existing)
        self.assertTrue(out.startswith("# mine\n__pycache__/\n"))
        self.assertIn(cmm.GITIGNORE_MARKER, out)
        self.assertIsNone(cmm.merge_gitignore(out), "second run must be a no-op")

    def test_existing_block_gains_missing_entries_once(self):
        # task 054 r2 grok#3: an already-inited clone has the marker but not the newer
        # entries — the merge must ADD the missing lines inside the block, once
        pre_054 = "# mine\n" + cmm.GITIGNORE_MARKER + "\n" + "\n".join(
            e for e in cmm.GITIGNORE_ENTRIES if e != ".agent/model-catalog.json") + "\n"
        out = cmm.merge_gitignore(pre_054)
        self.assertIsNotNone(out)
        self.assertEqual(out.count(".agent/model-catalog.json"), 1)
        self.assertEqual(out.count(cmm.GITIGNORE_MARKER), 1)
        self.assertTrue(out.startswith("# mine\n" + cmm.GITIGNORE_MARKER + "\n"))
        self.assertEqual(out.count(".agent/models.json"), 1)
        self.assertIsNone(cmm.merge_gitignore(out), "complete block → no-op")

    def test_inline_marker_mention_is_not_the_block_and_crlf_is_preserved(self):
        # task 054 r2 sol-med#5: the marker must be matched as a LINE — an inline mention
        # (a comment quoting it) is not a block, and must never crash init; a CRLF file
        # keeps its line endings when entries are inserted
        inline = "# see also: " + cmm.GITIGNORE_MARKER + " (docs)\n__pycache__/\n"
        out = cmm.merge_gitignore(inline)
        self.assertIsNotNone(out)
        self.assertEqual(out.count(cmm.GITIGNORE_MARKER), 2)                 # the mention + the real block
        self.assertIn("\n" + cmm.GITIGNORE_MARKER + "\n", out)
        crlf = "# mine\r\n" + cmm.GITIGNORE_MARKER + "\r\n" + "\r\n".join(
            e for e in cmm.GITIGNORE_ENTRIES if e != ".agent/model-catalog.json") + "\r\n"
        out = cmm.merge_gitignore(crlf)
        self.assertIsNotNone(out)
        self.assertIn(cmm.GITIGNORE_MARKER + "\r\n.agent/model-catalog.json\r\n", out)
        self.assertNotIn("\n\n", out.replace("\r\n", "\n").replace("\n\n", "\n\n"))   # no stray blank lines
        self.assertEqual(out.count("\r\n"), out.count("\n"), "mixed line endings")
        self.assertIsNone(cmm.merge_gitignore(out))


class MainKeepsLineEndings(unittest.TestCase):
    """X2 (task 093 r2): the CLI read with `read_text` (universal newlines) and wrote
    with newline translation, so the merge never saw a CRLF file as CRLF."""

    def _merge(self, raw: bytes) -> bytes:
        root = Path(tempfile.mkdtemp())
        (root / "CLAUDE.md").write_bytes(raw)
        r = subprocess.run([sys.executable, str(SCRIPTS / "claude-md-merge.py"),
                            str(SCRIPTS / "CLAUDE.md.template"), str(root), "P"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return (root / "CLAUDE.md").read_bytes()

    def test_crlf_file_stays_crlf_through_main(self):
        out = self._merge(b"# P\r\n\r\n## Don't\r\n\r\n- stale\r\n\r\n---\r\n\r\n# Mine\r\n\r\nkeep\r\n")
        self.assertNotIn(b"- stale", out)
        self.assertIn(b"# Mine\r\n\r\nkeep\r\n", out)
        self.assertEqual(out.count(b"\n"), out.count(b"\r\n"), "a lone LF in a CRLF file")

    def test_lf_file_stays_lf_through_main(self):
        out = self._merge(b"# P\n\n## Don't\n\n- stale\n")
        self.assertNotIn(b"\r", out)


class InitWritesBothFiles(unittest.TestCase):
    """The real scripts/init run — the seam the gauntlet found fragile."""

    def _run_init(self, proj: Path) -> str:
        home = Path(tempfile.mkdtemp()) / "home"
        home.mkdir(parents=True)
        env = dict(os.environ, HOME=str(home))
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        r = subprocess.run([bash_or_skip(), str(SCRIPTS / "init"), "Fixture Proj"],
                           cwd=proj, env=env, capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def _project(self) -> Path:
        proj = Path(tempfile.mkdtemp()) / "proj"
        proj.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        return proj

    def test_bare_repo_gets_both_files(self):
        proj = self._project()
        self._run_init(proj)
        claude = proj / "CLAUDE.md"
        self.assertTrue(claude.exists(), "init did not write CLAUDE.md")
        text = claude.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Fixture Proj"))
        self.assertIn("## Correctness Contract", text)
        gi = proj / ".gitignore"
        self.assertTrue(gi.exists(), "init did not write .gitignore")
        self.assertIn(".agent/sessions/", gi.read_text(encoding="utf-8"))

    def test_seeded_pointer_survives_reinit(self):
        # THE negative control: merge, never clobber.
        proj = self._project()
        (proj / "CLAUDE.md").write_text(SEEDED, encoding="utf-8")
        (proj / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        self._run_init(proj)
        text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("**Read `PURPOSE.md` first**", text)
        self.assertTrue(text.startswith("# StrataDB"))
        self.assertIn("## Dev Tooling", text)
        self.assertIn("## Correctness Contract", text)
        gi = (proj / ".gitignore").read_text(encoding="utf-8")
        self.assertTrue(gi.startswith("__pycache__/"))
        self.assertIn(".agent/sessions/", gi)
        # Re-init: idempotent, still no clobber.
        before = text
        self._run_init(proj)
        self.assertEqual((proj / "CLAUDE.md").read_text(encoding="utf-8"), before)
        self.assertEqual((proj / ".gitignore").read_text(encoding="utf-8"), gi)

    def test_project_part_survives_reinit(self):
        # task 093: the real init on the dev workspace's CLAUDE.md keeps its `#` part,
        # which is what init's "project content preserved" line promises
        proj = self._project()
        fixture = WORKSPACE_FIXTURE.read_text(encoding="utf-8")
        (proj / "CLAUDE.md").write_text(fixture, encoding="utf-8")
        self._run_init(proj)
        text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(_project_part(fixture), text)
        self.assertIn("## Correctness Contract", text)
        before = text
        self._run_init(proj)
        self.assertEqual((proj / "CLAUDE.md").read_text(encoding="utf-8"), before)



# ── task 114: no silent loss ───────────────────────────────────────────────────────────────
# The gauntlet's R06 plant (task 109, PLAN S11): three shapes written INSIDE the template's
# `## Don't` section. A refresh deleted all three while init printed "project content preserved".
R06_PLANT = """
```md
# Mine
keep me (Z1: fence opened inside a template section, closed after a project heading)
```

- stale item
---
kept after a list item (Z2)

Project Title
=============
setext H1 body (V7)
"""


def _with_plant(base: str, plant: str, before_heading: str = "## Don't") -> str:
    """`base` with `plant` appended to the END of the section `before_heading` opens."""
    i = base.index(before_heading)
    j = base.find("\n## ", i + 1)
    j = len(base) if j < 0 else j + 1
    return base[:j].rstrip("\n") + "\n" + plant + "\n" + base[j:]


class NoSilentLoss(unittest.TestCase):
    """Every non-blank line of the existing file ends up in the merged file or in a backup, and
    the status says which (task 114)."""

    def setUp(self):
        self.fresh = cmm.merge_claude_md(TEMPLATE, None, "P")

    def _main(self, text: str, raw: bool = False):
        root = Path(tempfile.mkdtemp())
        (root / "CLAUDE.md").write_bytes(text if raw else text.encode("utf-8"))
        r = subprocess.run([sys.executable, str(SCRIPTS / "claude-md-merge.py"),
                            str(SCRIPTS / "CLAUDE.md.template"), str(root), "P"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return root, r.stdout

    @staticmethod
    def _backups(root: Path):
        d = root / ".agent" / "backups"
        return sorted(d.glob("CLAUDE.md.*.bak")) if d.is_dir() else []

    def test_the_gauntlet_plant_is_backed_up_and_named(self):
        planted = _with_plant(self.fresh, R06_PLANT)
        root, out = self._main(planted)
        merged = (root / "CLAUDE.md").read_text(encoding="utf-8")
        status = [ln for ln in out.splitlines() if ln.startswith("CLAUDE.md:")]
        self.assertEqual(len(status), 1, out)
        self.assertTrue(status[0].startswith("CLAUDE.md:REPLACED:"), status[0])
        self.assertIn("## Don't", status[0])
        self.assertNotIn("preserved", status[0])
        backups = self._backups(root)
        self.assertEqual(len(backups), 1, backups)
        self.assertEqual(backups[0].read_bytes(), planted.encode("utf-8"))   # the old file, whole
        self.assertIn(str(backups[0].relative_to(root)).replace(os.sep, "/"), status[0])
        # the Setext part is the project's and stays where it was written
        self.assertIn("Project Title\n=============\nsetext H1 body (V7)\n", merged)
        # text inside the template section is replaced, by contract — and was in the backup
        self.assertNotIn("kept after a list item (Z2)", merged)
        self.assertNotIn("keep me (Z1", merged)

    def test_every_existing_line_is_kept_or_backed_up_and_counted(self):
        # the invariant over a set of shapes, each planted inside every template section in turn
        shapes = {
            "a line": "my own rule\n",
            "a list": "- one\n- two\n",
            "a list then a break": "- stale\n---\nafter\n",
            "a fenced heading": "```\n# not a heading\n```\n",
            "a comment": "<!-- my note -->\n",
            "a Setext part": "My Part\n=======\nits body\n",
            # after a thematic break: right under a list item it would be that item's content
            "an indented part": "---\n\n  # Indented Part\nits body\n",
            "a closing-hash part": "# Closed #\nits body\n",
            "a level-3 note": "### Mine\ntext\n",
        }
        _, sections = cmm.split_sections(self.fresh)
        headings = [h for h, _b, _p in sections if h]
        self.assertGreater(len(headings), 5)
        wrong = []
        for heading in headings:
            for name, shape in shapes.items():
                planted = _with_plant(self.fresh, "\n" + shape, heading)
                merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
                # counted: a line planted twice and kept once is one lost line
                lost = Counter(ln for ln in planted.splitlines() if ln.strip()) - Counter(merged.splitlines())
                said = Counter(ln for _sec, ln in dropped)
                if lost != said or any(sec != heading.strip() for sec, _ln in dropped):
                    wrong.append((heading, name, "lost", dict(lost), "reported", dropped))
        self.assertEqual(wrong, [], "\n".join(map(str, wrong[:5])))
        # D (gemini): the parts are not just "accounted for" — they are KEPT, wherever planted
        for heading in headings:
            for name in ("a Setext part", "an indented part", "a closing-hash part"):
                planted = _with_plant(self.fresh, "\n" + shapes[name], heading)
                merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
                self.assertIn(shapes[name], merged, (heading, name))
                self.assertEqual(dropped, [], (heading, name))

    def test_project_parts_in_every_level1_form_survive(self):
        for part in ("Project Title\n=============\nbody V7\n",
                     "---\n\n  # Indented Title\nbody of the indented part\n",
                     "---\n\n   # Three spaces\nbody three\n"):
            planted = _with_plant(self.fresh, "\n" + part)
            merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
            self.assertIn(part, merged, part)
            self.assertEqual(dropped, [], part)

    def test_a_setext_heading_of_several_lines_is_a_part_and_one_mid_paragraph_is_not(self):
        two_line = "Project\nTitle on two lines\n=====\nits body\n"
        planted = _with_plant(self.fresh, "\n" + two_line)
        merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
        self.assertIn(two_line, merged)
        self.assertEqual(dropped, [])
        # a paragraph that contains a list item is no heading text: replaced and reported
        mixed = "intro\n- item\nTitle\n=====\n"
        merged, dropped = cmm.merge_report(TEMPLATE, _with_plant(self.fresh, "\n" + mixed), "P")
        self.assertNotIn("intro\n- item", merged)
        self.assertTrue(dropped)

    def test_two_backups_in_the_same_second_both_survive(self):
        root = Path(tempfile.mkdtemp())
        a = cmm.write_backup(root, b"first")
        b = cmm.write_backup(root, b"second")
        self.assertNotEqual(a, b)
        self.assertEqual((a.read_bytes(), b.read_bytes()), (b"first", b"second"))

    def test_a_setext_underline_after_a_list_item_or_quote_is_not_a_part(self):
        for shape in ("- item\n====\n", "> quoted\n====\n", "    code\n====\n"):
            planted = _with_plant(self.fresh, "\n" + shape)
            merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
            self.assertNotIn(shape, merged, shape)           # inside the template section: replaced …
            self.assertTrue(dropped, shape)                  # … and reported

    def test_nothing_dropped_means_no_backup_and_the_old_status(self):
        root, out = self._main(self.fresh + "\n## Mine\n\nmy own section\n")
        self.assertEqual(self._backups(root), [])
        self.assertIn("CLAUDE.md:UNCHANGED", out)
        stale = self.fresh.replace("## Don't", "## Don't\n", 1)            # a blank line only
        root, out = self._main(stale)
        self.assertEqual(self._backups(root), [])
        self.assertNotIn("REPLACED", out)

    def test_a_second_run_is_unchanged_and_makes_no_second_backup(self):
        root, _ = self._main(_with_plant(self.fresh, R06_PLANT))
        first = (root / "CLAUDE.md").read_bytes()
        r = subprocess.run([sys.executable, str(SCRIPTS / "claude-md-merge.py"),
                            str(SCRIPTS / "CLAUDE.md.template"), str(root), "P"],
                           capture_output=True, text=True, timeout=60)
        self.assertIn("CLAUDE.md:UNCHANGED", r.stdout)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), first)
        self.assertEqual(len(self._backups(root)), 1)

    def test_a_crlf_file_is_backed_up_byte_for_byte(self):
        planted = _with_plant(self.fresh, R06_PLANT).replace("\n", "\r\n").encode("utf-8")
        root, out = self._main(planted, raw=True)
        self.assertIn("CLAUDE.md:REPLACED:", out)
        (backup,) = self._backups(root)
        self.assertEqual(backup.read_bytes(), planted)
        merged = (root / "CLAUDE.md").read_bytes()
        self.assertEqual(merged.count(b"\n"), merged.count(b"\r\n"))


class ImplPanelRound1(unittest.TestCase):
    """Task 114, implementation panel round 1."""

    def setUp(self):
        self.fresh = cmm.merge_claude_md(TEMPLATE, None, "P")

    def _main(self, raw: bytes):
        root = Path(tempfile.mkdtemp())
        (root / "CLAUDE.md").write_bytes(raw)
        r = subprocess.run([sys.executable, str(SCRIPTS / "claude-md-merge.py"),
                            str(SCRIPTS / "CLAUDE.md.template"), str(root), "P"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return root, r.stdout

    def test_bytes_that_are_not_utf8_survive_where_the_project_wrote_them(self):
        # A (gemini, codex x2, opus): `errors="replace"` rewrote them as U+FFFD with no backup
        stale = self.fresh.replace("## Don't", "## Don't\n\nold template line\n", 1)     # forces a refresh
        raw = (stale + "\n## Notes\n\n").encode("utf-8") + b"My caf\xe9 notes\n"
        root, out = self._main(raw)
        self.assertIn(b"My caf\xe9 notes\n", (root / "CLAUDE.md").read_bytes())
        self.assertNotIn("�".encode("utf-8"), (root / "CLAUDE.md").read_bytes())
        # and a non-UTF-8 line INSIDE a template section is reported and backed up like any other
        raw2 = self.fresh.replace("## Don't", "## Don't\n", 1).encode("utf-8")
        i = raw2.index(b"## Don't\n") + len(b"## Don't\n")
        raw2 = raw2[:i] + b"\nna\xefve rule\n" + raw2[i:]
        root, out = self._main(raw2)
        self.assertIn("CLAUDE.md:REPLACED:", out)
        (backup,) = sorted((root / ".agent" / "backups").glob("CLAUDE.md.*.bak"))
        self.assertEqual(backup.read_bytes(), raw2)

    def test_a_backup_name_is_claimed_exclusively(self):
        # B (gemini, codex x2): two writers choosing the same absent name overwrote one backup
        root = Path(tempfile.mkdtemp())
        real_exists = Path.exists
        # every writer sees the name as free — only an exclusive claim keeps both
        with unittest.mock.patch.object(Path, "exists", lambda self: False if self.name.startswith("CLAUDE.md.") else real_exists(self)):
            a = cmm.write_backup(root, b"first")
            b = cmm.write_backup(root, b"second")
        self.assertNotEqual(a, b)
        self.assertEqual((a.read_bytes(), b.read_bytes()), (b"first", b"second"))

    def test_an_indented_hash_inside_a_list_is_list_content_not_a_part(self):
        # C (opus): `   # from repo root` under `1. Build:` opened a part and froze every template
        # section below it (never refreshed again, a fresh copy inserted into the list)
        custom = "## Setup\n\n1. Build:\n   # from repo root\n   make\n\n"
        i = self.fresh.index("\n## ") + 1
        stale = self.fresh.replace("## Don't", "## Don't\n\nold template line\n", 1)
        planted = stale[:i] + custom + stale[i:]
        merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
        self.assertIn(custom, merged)                                  # the list stays whole
        self.assertEqual(merged.count("## Don't"), 1)                  # refreshed in place, not duplicated
        self.assertNotIn("old template line", merged)
        self.assertEqual([ln for _s, ln in dropped], ["old template line"])
        for shape in ("- item\n\n   # still the item's\n", "> quote\n>\n  # in the quote? no: a new line\n"):
            planted = _with_plant(self.fresh, "\n" + shape)
            merged, _ = cmm.merge_report(TEMPLATE, planted, "P")
            self.assertEqual(merged.count("## Don't"), 1, shape)

    def test_an_indented_hash_right_under_a_list_is_that_lists_content(self):
        # Markdown: `  # x` after `- item` (blank line or not) continues the item — it is text of
        # the template section it sits in, so it is replaced with it, and reported
        planted = _with_plant(self.fresh, "\n- my item\n\n  # My Title\n")
        merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
        self.assertNotIn("  # My Title", merged)
        self.assertIn("  # My Title", [ln for _s, ln in dropped])
        # the same for indented Setext text under a list item
        planted = _with_plant(self.fresh, "\n- my item\n\n  Item Title\n  ==========\n")
        merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
        self.assertNotIn("  Item Title", merged)
        self.assertIn("  Item Title", [ln for _s, ln in dropped])

    def test_a_setext_heading_right_under_a_break_or_heading_is_a_part(self):
        # gemini: a paragraph may start right under a thematic break or an ATX heading
        for above in ("---\n", "### Notes\n"):
            part = "Project Title\n=============\nbody\n"
            planted = _with_plant(self.fresh, "\n" + above + part)
            merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
            self.assertIn(part, merged, above)


class ImplPanelRound2(unittest.TestCase):
    """Task 114, implementation panel round 2 (opus, sonnet)."""

    def setUp(self):
        self.fresh = cmm.merge_claude_md(TEMPLATE, None, "P")

    def test_every_part_shape_is_stable_over_two_refreshes(self):
        # opus: an indented part kept on run 1 was read as list content on run 2 (it now sits right
        # under the template's last list item) — REPLACED, a backup, and the part gone
        stale = self.fresh.replace("## Don't", "## Don't\n\nold template line\n", 1)
        for shape in ("\nmy note\n\n  # Mine\nbody\n", "\n---\n\n  # Mine\nbody\n",
                      "\nProject Title\n=============\nbody\n", "\n---\n\n  Indented Title\n  ==============\nbody\n",
                      "\n# Plain\nbody\n"):
            planted = _with_plant(stale, shape)
            once, _d1 = cmm.merge_report(TEMPLATE, planted, "P")
            twice, d2 = cmm.merge_report(TEMPLATE, once, "P")
            self.assertEqual(twice, once, shape)                     # idempotent after the first refresh
            self.assertEqual(d2, [], shape)

    def test_a_template_heading_in_other_case_is_not_reported_as_dropped(self):
        # sonnet: `## correctness contract` is the template's section (matched case-insensitively);
        # re-casing its heading is not text the project loses
        recased = self.fresh.replace("## Correctness Contract", "## correctness contract", 1)
        merged, dropped = cmm.merge_report(TEMPLATE, recased, "P")
        self.assertIn("## Correctness Contract", merged)
        self.assertEqual(dropped, [])

    def test_an_indented_heading_under_an_indented_paragraph_line_is_text_and_reported(self):
        # sonnet: "any indented line above" was read as a container; the rule now asks for an
        # explicit break above an indented heading, which also keeps it stable (opus)
        planted = _with_plant(self.fresh, "\n    some code\n\n  # After Code\nbody\n")
        merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
        self.assertIn("  # After Code", [ln for _s, ln in dropped])
        self.assertNotIn("  # After Code", merged)

    def test_an_indented_heading_after_a_paragraph_is_a_part_and_comes_back_unindented(self):
        # Task 138 G1-4 (codex-high, codex-medium): in Markdown `  # Mine` after a blank line and
        # a paragraph IS a level-1 heading — it was read as template text and replaced. Kept, its
        # heading is written without the indent (it renders the same): after a refresh the part
        # may sit under the template's last list item, where an indented `#` is the item's text.
        # (a blank line CLOSES a block quote — task 139, opus — so after one it is a heading too)
        for above in ("my note\n", "### Notes\n", "my note\nmore of it\n", "> quoted\n",
                      "  > quoted, indented\n"):           # task 139 post-D6 (codex): ≤3 spaces is a quote
            planted = _with_plant(self.fresh, "\n" + above + "\n  # Mine\nbody\n")
            merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
            self.assertIn("# Mine\nbody\n", merged, above)
            self.assertNotIn("  # Mine", merged, above)
            self.assertNotIn("# Mine", [ln.strip() for _s, ln in dropped], above)
            twice, d2 = cmm.merge_report(TEMPLATE, merged, "P")
            self.assertEqual((twice, d2), (merged, []), above)

    def test_after_a_list_or_quote_block_it_is_still_that_blocks_text(self):
        for above in ("- item\n", "1. step\n", "text\n- item\n", "  indented para\n"):
            planted = _with_plant(self.fresh, "\n" + above + "\n  # Theirs\n")
            merged, dropped = cmm.merge_report(TEMPLATE, planted, "P")
            self.assertIn("  # Theirs", [ln for _s, ln in dropped], above)

    def test_init_md_no_longer_promises_every_byte(self):
        text = (PLUGIN / "commands" / "init.md").read_text(encoding="utf-8")
        self.assertNotIn("keeps every byte", text)
        self.assertIn(".agent/backups/CLAUDE.md", text)


class InitSaysWhatItReplaced(unittest.TestCase):
    _run_init = InitWritesBothFiles._run_init
    _project = InitWritesBothFiles._project

    def test_init_prints_the_replacement_and_the_backup_not_preserved(self):
        proj = self._project()
        fresh = cmm.merge_claude_md(TEMPLATE, None, "Fixture Proj")
        (proj / "CLAUDE.md").write_text(_with_plant(fresh, R06_PLANT), encoding="utf-8")
        out = self._run_init(proj)
        line = [ln for ln in out.splitlines() if "CLAUDE.md" in ln and "template" in ln]
        self.assertEqual(len(line), 1, out)
        self.assertNotIn("preserved", line[0])
        self.assertIn("## Don't", line[0])
        self.assertIn(".agent/backups/CLAUDE.md.", line[0])
        self.assertEqual(len(list((proj / ".agent" / "backups").glob("CLAUDE.md.*.bak"))), 1)
        self.assertIn("Project Title\n=============", (proj / "CLAUDE.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
