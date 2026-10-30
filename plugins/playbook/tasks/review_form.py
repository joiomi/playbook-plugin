"""The fixed review form (task 347; DESIGN-PLAYBOOK-2.md §3, approved by the owner as R17; PLAN-PLAYBOOK-2.md 4.2).

A judge's answer counts as a review by its form, never by its words. Task 320's word check (`not_a_review`) erred
both ways at audit 6: it counted a plan, a refusal and an error (G1) and failed clean reviews in ordinary words (G2).
Now every review prompt carries its run's code (`new_code`, `prompt_clause`) and the judge answers in one block:

    REVIEW <code>
    FINDING
    severity: Critical | Important | Minor
    settled: no | SETTLED | SETTLED-CONTRADICTED
    where: <path>:<line>[-<last>] | base:<path>:<line>[-<last>] | none
    scenario: <input> → <what goes wrong> → <where it shows>
    drum: <the file, line, command or flow normal work takes there> | none
    fix: <one sentence>
    NONE                      (instead: no Critical or Important finding)
    read: <place>, <place>, …
    checked: <one sentence>
    END <code>
    CAP <k>/5 exhausted | CAP 5/5 more-remain     (the old `CAP: <k>/5 reported, …` is read too)

`read_answer` checks the frame, then each record alone: a record it cannot read is BROKEN (kept, with its lines and
why, for the triage), and the answer is a review when the frame is good and one record is not broken. `Places` checks
that every place a record names exists — in the tree the judges read, or (`base:`) in the task's start commit — and
that a NONE read at least one file the task changed. `render` writes the records as the CLI's own lines of judge.md,
numbered across a round (`F1 | Critical | settled: no | <seat>`); `count_records` counts only those lines.

Stated limit (DESIGN §10): the form proves the answer was written for this run and says what the judge read; it does
not prove the judge read with care.
"""
from __future__ import annotations

import os
import posixpath
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# The head of a failed answer — `models_check.NOT_A_REVIEW_HEAD`, which the outage readers skip.
MAX_RECORDS = 30
MAX_VALUE = 4000
MAX_READ = 32 * 1024 * 1024
MAX_NAMED = 40            # changed files a prompt names
MAX_EARLIER = 20_000      # characters of a first answer a re-run is given

SEVERITIES = {"critical": "Critical", "important": "Important", "minor": "Minor"}
SETTLED_VALUES = {"no": "no", "settled": "SETTLED", "settled-contradicted": "SETTLED-CONTRADICTED"}
FINDING_KEYS = ("severity", "settled", "where", "scenario", "drum", "fix")
NONE_KEYS = ("read", "checked")

_CODE_RE = re.compile(r"[0-9a-f]{12}")
_CAP_RE = re.compile(r"CAP (\d+)/(\d+) (exhausted|more[- ]remain)\.?|CAP: (\d+)/(\d+) reported, (exhausted|more remain)\.?")
# A fence or a rule: decoration a judge puts around or inside the block — skipped, never a record's line.
_TRAILER_RE = re.compile(r"(?:`{3,}|~{3,})[\w+.-]*|-{3,}|\*{3,}|_{3,}")
_FIELD_RE = re.compile(r"([A-Za-z]+)[ \t]*:(.*)")
_ARROW_RE = re.compile(r"→|->")
_LINES_RE = re.compile(r"(.+?):(\d+)(?:-(\d+)|:\d+)?")                # `path:line:col` is its line (impl round 1)
# A scenario that names a file: a token with a `/`, or a name with an extension of 2-8 letters (`e.g.` is none).
_FILE_TOKEN_RE = re.compile(r"(?<![\w/.-])(?:[\w.-]+/)+[\w.-]+|(?<![\w/.-])[\w-]+\.[A-Za-z][A-Za-z0-9]{0,7}\b")
_NOT_FILES = {"e.g", "i.e"}
_WORD_RE = re.compile(r"[\w.-]+")
# The CLI's own record line in judge.md (`render`): never a quoted line, never an indented field.
_WRITER_RE = re.compile(r"^F\d+ \| (Critical|Important|Minor) \| settled: (no|SETTLED|SETTLED-CONTRADICTED) \| ",
                        re.MULTILINE)


def new_code() -> str:
    """One review run's code: 12 hex characters, fresh for every panel round and every single-judge run."""
    return secrets.token_hex(6)


def prompt_clause(code: str, settled: bool = False, changed: "list[str] | None" = None) -> str:
    """What every review prompt asks of the answer. The code stands only inside a sentence — never on a line of its
    own — so a judge that echoes its prompt writes no block line. `changed`, an impl review's files the task changed
    (`Places.changed`), is named — a NONE must read one of them."""
    s = (
        f"ANSWER FORM — the CLI reads only this block, by its form, so write it exactly. This run's code is `{code}`. "
        "After your reasoning (it is not read), write ONE block: a line holding only `REVIEW` and the code; then one "
        "record per finding; then a line holding only `END` and the code; then the CAP line. "
        "A finding is a line `FINDING` followed by these fields, each `key: value` on a line of its own, in this "
        "order: `severity:` Critical, Important or Minor; `settled:` no, SETTLED or SETTLED-CONTRADICTED; `where:` "
        "the place — `path:line` or `path:first-last`, the path relative to the project root and the line numbers "
        "the file itself has (the task.md you were given inline may be trimmed), in the tree you review, or "
        "prefixed `base:` (`base:path:line`) in the task's starting commit, for code the task deleted — "
        "or `none` only for something missing, when the scenario names the file it belongs in; `scenario:` input → "
        "what goes wrong → where it shows (three parts joined by →); `drum:` the file, line, command or flow by which "
        "normal work reaches it, or `none`; `fix:` one sentence. "
        "With no Critical or Important finding, write the record `NONE` instead, with `read:` the places you read, "
        "comma-separated (`path:line`, `path:first-last` or a whole `path`, `base:` allowed; at least one file this "
        "task changed, when it changed any) and `checked:` what you checked, in one sentence; Minor findings may "
        "stand beside it. "
        "The CAP line: `CAP <k>/5 exhausted` when you reported every Critical or Important finding you found (k of "
        "them), or `CAP 5/5 more-remain` when you had to drop some. "
        "Every value fits on its one line; nothing else goes inside the block. A record the CLI cannot read is set "
        "aside as broken, and an answer with no readable record is no review. "
        "For example, the code written `<code>`, each part between slashes a line of its own: `REVIEW <code>` / "
        "`FINDING` / `severity: Important` / `settled: no` / `where: src/app.py:42` / `scenario: an empty list → "
        "max() raises → the request returns 500` / `drum: src/routes.py:10 calls it on every request` / `fix: return "
        "0 for an empty list.` / `END <code>` / `CAP 1/5 exhausted`. "
    )
    if changed:
        named = ", ".join(f"`{n}`" for n in changed[:MAX_NAMED])
        more = f", and {len(changed) - MAX_NAMED} more" if len(changed) > MAX_NAMED else ""
        s += f"The files this task changed: {named}{more}. "
    if settled:
        s += ("A SETTLED block (owner rulings + findings already rejected in triage) precedes the task context: read "
              "it first and follow its header — a ruling closes a design choice, never a correctness defect. ")
    return s


def rerun_note(why: str, earlier: str) -> str:
    """What a seat's one re-run is told after its first answer failed the form: why, and that answer quoted (each line
    `> `, so none of it is a frame line; at most `MAX_EARLIER` characters, the cut said)."""
    text = str(earlier or "")
    cut = len(text) > MAX_EARLIER
    quoted = "\n".join("> " + ln for ln in text[:MAX_EARLIER].splitlines()) or "> (empty)"
    return ("Your previous answer was not in the review form: " + _one(why) + ". It follows, quoted"
            + (f" (its first {MAX_EARLIER:,} characters)" if cut else "") + ". If it holds your review, restate it "
            "in the form above; if it does not, do the review. Answer in the form, with this run's code.\n\n"
            + quoted)


@dataclass
class Record:
    """One record of the block: FINDING, NONE, or BROKEN (its lines as written, and why)."""
    kind: str
    lines: "list[str]"
    fields: "dict[str, str]" = field(default_factory=dict)
    why: str = ""

    @property
    def severity(self) -> "str | None":
        return self.fields.get("severity") if self.kind == "FINDING" else None

    @property
    def settled(self) -> "str | None":
        return self.fields.get("settled") if self.kind == "FINDING" else None

    @property
    def counted(self) -> bool:
        """A Critical or Important finding that does not re-raise a settled item."""
        return self.kind == "FINDING" and self.severity in ("Critical", "Important") and self.settled != "SETTLED"


@dataclass
class Answer:
    ok: bool
    why: str
    records: "list[Record]"
    cap: "tuple[int, int, str] | None"


def _fail(why: str) -> Answer:
    return Answer(False, why, [], None)


def _short(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def read_answer(text: str, code: str, places: "Places | None" = None) -> Answer:
    """The answer as the form reads it. The frame: exactly one whole line `REVIEW <code>` (else, exactly one line that
    ends with it, a preamble glued before it, not a quoted `>` line), one `END <code>` after it, and the CAP line as
    the first line after END that is not blank, a fence or a rule. The records between them,
    each checked alone (`places` checks the places they name; without it only their shape). `ok` when the frame is
    good and one record is not broken; else `why` says what failed. The text is split at every line break any reader
    splits at (`str.splitlines`); a BOM is dropped."""
    if not _CODE_RE.fullmatch(code or ""):
        return _fail("the run has no review code")
    lines = [ln.strip().lstrip("\ufeff").strip() for ln in str(text or "").splitlines()]
    opens = [i for i, s in enumerate(lines) if s == f"REVIEW {code}"]
    if not opens:
        # a preamble glued to the frame line ("…how they're tested.REVIEW <code>": grok, both its answers in task
        # 347's live probe) — never a quoted line, which the re-run note writes
        opens = [i for i, s in enumerate(lines)
                 if re.search(rf"(?<!\w)REVIEW {code}$", s) and not s.startswith(">")]
    ends = [i for i, s in enumerate(lines) if s == f"END {code}"]
    if not opens:
        return _fail("there is no `REVIEW <code>` line with this run's code — the answer is not in the review form")
    if len(opens) > 1 or len(ends) > 1:
        return _fail("there is more than one block with this run's code")
    if not ends:
        return _fail("the block has no `END <code>` line")
    if ends[0] < opens[0]:
        return _fail("the `END <code>` line comes before the `REVIEW <code>` line")
    cap = None
    for s in lines[ends[0] + 1:]:
        if not s or _TRAILER_RE.fullmatch(s):
            continue
        m = _CAP_RE.fullmatch(s.strip("*`").strip())
        if m:
            k, n, state = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
            cap = (int(k), int(n), "more-remain" if state.startswith("more") else "exhausted")
        break
    if cap is None:
        return _fail("the line after `END <code>` is not the CAP line (`CAP <k>/5 exhausted` or `CAP 5/5 more-remain`)")
    if cap[0] > cap[1]:
        return _fail(f"the CAP line counts {cap[0]} of {cap[1]}")
    records = _records(lines[opens[0] + 1:ends[0]], places)
    if not records:
        return _fail("the block holds no record")
    if all(r.kind == "BROKEN" for r in records):
        return Answer(False, f"every record is broken ({len(records)} record(s))", records, cap)
    return Answer(True, "", records, cap)


def _records(block: "list[str]", places: "Places | None") -> "list[Record]":
    raw: "list[dict]" = []
    cur = None
    for s in block:
        if not s or _TRAILER_RE.fullmatch(s):
            continue
        if s.upper() in ("FINDING", "NONE"):
            cur = {"kind": s.upper(), "lines": [s], "fields": {}, "errors": []}
            raw.append(cur)
            continue
        if cur is None:
            cur = {"kind": "", "lines": [], "fields": {}, "errors": ["text before the first record"]}
            raw.append(cur)
        cur["lines"].append(s)
        if not cur["kind"]:
            continue
        m = _FIELD_RE.fullmatch(s)
        if not m:
            cur["errors"].append(f"a line that is no `key: value` field: {_short(s)}")
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key not in (FINDING_KEYS if cur["kind"] == "FINDING" else NONE_KEYS):
            cur["errors"].append(f"`{key}:` is no field of a {cur['kind']}")
        elif key in cur["fields"]:
            cur["errors"].append(f"`{key}:` is repeated")
        else:
            cur["fields"][key] = value
    if len(raw) > MAX_RECORDS:
        rest = [ln for r in raw[MAX_RECORDS:] for ln in r["lines"]]
        raw = raw[:MAX_RECORDS] + [{"kind": "", "lines": rest, "fields": {},
                                    "errors": [f"past {MAX_RECORDS} records: the rest is not read"]}]
    records = [_checked(r, places) for r in raw]
    # NONE says there is no counted finding: beside one it is broken, and only one NONE stands
    counted = [r for r in records if r.counted]
    seen_none = False
    for r in records:
        if r.kind != "NONE":
            continue
        if counted:
            r.kind, r.why = "BROKEN", "a NONE says there is no Critical or Important finding, but the block holds one"
        elif seen_none:
            r.kind, r.why = "BROKEN", "a second NONE"
        seen_none = True
    return records


def _checked(r: dict, places: "Places | None") -> Record:
    kind, f, errors = r["kind"], dict(r["fields"]), list(r["errors"])
    if kind:
        for key in (FINDING_KEYS if kind == "FINDING" else NONE_KEYS):
            if key not in f:
                errors.append(f"`{key}:` is missing")
            elif not f[key]:
                errors.append(f"`{key}:` is empty")
            elif len(f[key]) > MAX_VALUE:
                errors.append(f"`{key}:` is over {MAX_VALUE:,} characters")
    if kind == "FINDING" and not errors:
        sev = SEVERITIES.get(f["severity"].lower())
        if sev is None:
            errors.append(f"`severity:` is {_short(f['severity'])!r}, not Critical, Important or Minor")
        else:
            f["severity"] = sev
        st = SETTLED_VALUES.get(f["settled"].lower())
        if st is None:
            errors.append(f"`settled:` is {_short(f['settled'])!r}, not no, SETTLED or SETTLED-CONTRADICTED")
        else:
            f["settled"] = st
        parts = [p.strip() for p in _ARROW_RE.split(f["scenario"])]
        if len(parts) < 3 or not all(parts):
            errors.append("`scenario:` needs three parts: input → what goes wrong → where it shows")
        if f["where"].strip("`").lower() == "none":
            if not _names_a_file(f["scenario"], places):
                errors.append("`where: none` needs a scenario that names the file it belongs in")
        else:
            for item in [x for x in f["where"].split(",") if x.strip()]:      # several places (impl round 1)
                _, why = _locate(places, item, whole_file=False)
                if why:
                    errors.append(f"`where:` {why}")
    elif kind == "NONE" and not errors:
        items = [x.strip() for x in f["read"].split(",") if x.strip()]
        found: "list[str]" = []
        for item in items:
            path, why = _locate(places, item, whole_file=True)
            if why:
                errors.append(f"`read:` {why}")
            elif path:
                found.append(path)
        if not items:
            errors.append("`read:` names no place")
        changed = places.changed if places is not None else None
        if not errors and changed and not ({places.identity(n) for n in found} & {places.identity(n) for n in changed}):
            some = ", ".join(sorted(changed)[:3]) + (", …" if len(changed) > 3 else "")
            errors.append(f"`read:` names none of the files this task changed ({some})")
    if errors or not kind:
        return Record("BROKEN", r["lines"], f, "; ".join(errors))
    return Record(kind, r["lines"], f)


def _names_a_file(scenario: str, places: "Places | None") -> bool:
    """A scenario names a file: a token with a `/`, a name with an extension (`calc.h`; not `e.g.`), or — when the
    places are known — a bare word an existing project file is called (`Makefile`; impl round 1, grok)."""
    if any(m.group(0).lower() not in _NOT_FILES for m in _FILE_TOKEN_RE.finditer(scenario or "")):
        return True
    if places is None:
        return False
    names = {f.rsplit("/", 1)[-1] for f in places._project_files()}
    return any(w.strip(".") in names for w in _WORD_RE.findall(scenario or ""))


def _parse_location(item: str, whole_file: bool):
    """(base side?, path, first line, last line) — the lines None for a whole file — or a string: why not."""
    s = item.strip()
    if len(s) >= 2 and s[0] == s[-1] == "`":
        s = s[1:-1].strip()
    base = s.startswith("base:")
    if base:
        s = s[len("base:"):].strip()
    m = _LINES_RE.fullmatch(s)
    if m:
        first = int(m.group(2))
        last = int(m.group(3)) if m.group(3) else first
        if first < 1 or last < first:
            return f"`{_short(item)}`: lines {first}-{last} are not a range of lines"
        return base, m.group(1), first, last
    if not whole_file:
        return f"`{_short(item)}` names no line (`path:line` or `path:first-last`)"
    if not s:
        return f"`{_short(item)}` names no file"
    return base, s, None, None


def _locate(places: "Places | None", item: str, *, whole_file: bool) -> "tuple[str | None, str]":
    if places is not None:
        return places.locate(item, whole_file=whole_file)
    got = _parse_location(item, whole_file)
    return (None, got) if isinstance(got, str) else (got[1], "")


def _count_lines(data: bytes) -> int:
    """Lines as an editor numbers them: a break is `\\n`, `\\r\\n` or a lone `\\r`; a last line needs none."""
    return len(data.splitlines())


def _inside(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip("/") + "/")


class Places:
    """Where the places a record names are read: `root`, the tree the judges read (the review's fixed copy, or the
    project) — but an entry the copy binds live (`live`: `View.live_binds` + `View.agent_live`, an empty mountpoint in
    the copy) and a file the copy does not hold are read from the project; `scopes`, each repository's folder and the
    task's start commit (base.json) — `""` the project's own, else a code root; `changed`, the files the task changed
    since its start, project-relative, each on one side or the other (None: the rule is not applied — a plan review,
    or they cannot be read); `task_md`, the reviewed task's file, which a bare `task.md` names."""

    def __init__(self, project: Path, *, root: "Path | None" = None,
                 scopes: "dict[str, tuple[Path, str | None]] | None" = None, changed: "set[str] | None" = None,
                 base_why: str = "", live: "list[str] | tuple[str, ...]" = (), task_md: str = ""):
        self.project = Path(project)
        self.root = Path(root) if root is not None else self.project
        self.scopes = dict(scopes or {})
        self.changed = changed
        self.base_why = base_why or ("the task's start is not recorded (base.json)" if not self.scopes else "")
        self.live = [posixpath.normpath(x).strip("/") for x in live if x]
        self.task_md = task_md
        self._files: "list[str] | None" = None
        self._base_files: "list[str] | None" = None

    @classmethod
    def for_task(cls, project: Path, root: "Path | None", task_dir: Path, *, live=(), impl: bool = True) -> "Places":
        """The places of one task's review: its repositories, its start (base.json) and — an impl review's — the
        files it changed, read now (before the judges spawn: a file changed after the seal is never asked for)."""
        from tasks import red
        from tasks.core import load_config
        project = Path(project)
        try:
            cfg = load_config(project)
        except Exception:                               # noqa: BLE001 — a bad config is said by its own readers
            cfg = {}
        repos = red._scopes(project, cfg)
        base = red._load_base(Path(task_dir))
        scopes: "dict[str, tuple[Path, str | None]]" = {name: (repo, None) for name, repo in repos.items()}
        base_why = ""
        if not base or "failed" in base or not isinstance(base.get("scopes"), dict):
            base_why = "the task's start is not recorded (base.json)"
        else:
            for name, rec in base["scopes"].items():
                name = red._norm_scope(name)
                if name in scopes and isinstance(rec, dict):
                    head = rec.get("head") if "head" in rec else rec.get("commit")
                    scopes[name] = (scopes[name][0], head if isinstance(head, str) and head else None)
        try:
            task_md = (Path(task_dir) / "task.md").relative_to(project).as_posix()
        except ValueError:
            task_md = ""
        places = cls(project, root=root, scopes=scopes, base_why=base_why, live=live, task_md=task_md)
        if impl and not base_why:
            try:
                with red._no_git_overrides():
                    got = red._changed_by_scope(project, Path(task_dir), cfg)[0]
            except Exception:                           # noqa: BLE001 — unread is not "none changed"
                got = None
            if got is not None:
                named = {red._shown(f"{sc + '/' if sc else ''}{n}") for sc, names in got.items() for n in names}
                # only a file on one side or the other can be read (codex, plan panel: one untracked at the start
                # and deleted since is on neither)
                places.changed = {n for n in named
                                  if places._current_lines(n)[0] is not None or places._base_lines(n)[0] is not None}
        places._project_files()     # the index of the tree as it is now, before the spawn (impl round 1, codex)
        return places

    def identity(self, rel: str) -> str:
        """The file a project-relative path is, once links are followed — so an alias of a changed file is that file
        (impl round 1, codex); a path that leaves the project, or is gone, is its own spelling."""
        top = os.path.realpath(self.project)
        real = os.path.realpath(self.project / rel)
        return os.path.relpath(real, top).replace(os.sep, "/") if _inside(real, top) else rel

    def _start_files(self) -> "list[str]":
        """Every file of each repository's start commit, project-relative — for a `base:` path cited by its suffix
        (impl round 1, codex). Read once."""
        if self._base_files is None:
            from tasks import red
            out: "list[str]" = []
            for name, (repo, head) in sorted(self.scopes.items()):
                if not head:
                    continue
                try:
                    with red._no_git_overrides():
                        r = red._git(repo, ["ls-tree", "-r", "--name-only", "-z", head, "--", "."])
                except (OSError, subprocess.SubprocessError):
                    continue
                if r.returncode == 0:
                    out += [f"{name + '/' if name else ''}{n}" for n in red._z(r.stdout)]
            self._base_files = out
        return self._base_files

    def _project_files(self) -> "list[str]":
        """Every file of each repository (`git ls-files -co --exclude-standard`), project-relative — for a path
        cited by its suffix. Read once."""
        if self._files is None:
            from tasks import red
            out: "list[str]" = []
            repos = {name: repo for name, (repo, _h) in self.scopes.items()} or {"": self.project}
            for name, repo in sorted(repos.items()):
                try:
                    with red._no_git_overrides():
                        r = red._git(repo, ["ls-files", "-co", "--exclude-standard", "-z"])
                except (OSError, subprocess.SubprocessError):
                    continue
                if r.returncode == 0:
                    out += [f"{name + '/' if name else ''}{n}" for n in red._z(r.stdout)]
            self._files = out
        return self._files

    def _candidates(self, path: str) -> "list[str] | str":
        """The project-relative paths a cited path may mean: as written from the project root, then from each code
        root, then the one project file it is the unique suffix of. A bare `task.md` is the reviewed task's. An
        absolute path inside the project (or the copy) is made relative; one that leaves it is refused."""
        p = path.strip()
        if os.path.isabs(p):
            for top in (str(self.project), str(self.root)):
                if _inside(p, top) and p != top:
                    p = p[len(top.rstrip("/")) + 1:]
                    break
            else:
                return f"`{_short(path)}` is outside the project"
        rel = posixpath.normpath(p)
        if rel in (".", "") or rel == ".." or rel.startswith("../") or rel.startswith("/"):
            return f"`{_short(path)}` is not a file inside the project"
        if rel == "task.md" and self.task_md:
            return [self.task_md]
        out = [rel]
        for scope in sorted((sc for sc in self.scopes if sc), key=len, reverse=True):
            if not _inside(rel, scope):
                out.append(f"{scope}/{rel}")
        return out

    def _by_suffix(self, rel: str, base: bool = False) -> "tuple[str | None, str]":
        files = self._start_files() if base else self._project_files()
        hits = sorted({f for f in files if f.endswith("/" + rel)})
        if len(hits) == 1:
            return hits[0], ""
        if hits:
            shown = ", ".join(hits[:4]) + (", …" if len(hits) > 4 else "")
            return None, f"`{_short(rel)}` names {len(hits)} files ({shown}): say which"
        return None, ""

    def _current_lines(self, rel: str) -> "tuple[int | None, str]":
        """(the file's line count, "") in the tree the judges read; (None, why) when it is no regular file there."""
        # the copy alone: what it lacks was made after the seal — but an entry it binds live is read from the project
        # (impl round 1, codex and opus)
        live = any(_inside(rel, x) for x in self.live)
        for top in ((self.project,) if live else (self.root,)):
            p = top / rel
            if not os.path.lexists(p):
                continue
            try:
                # a link into the project resolves: the judges' mount maps the project's path to the copy (codex)
                real = os.path.realpath(p)
                if not (_inside(real, os.path.realpath(top)) or _inside(real, os.path.realpath(self.project))):
                    return None, f"`{rel}` leads outside the project"
                if not os.path.isfile(real):
                    return None, f"`{rel}` is not a file"
                if os.path.getsize(real) > MAX_READ:
                    return None, f"`{rel}` is over {MAX_READ // (1024 * 1024)} MiB: not read"
                with open(real, "rb") as fh:
                    return _count_lines(fh.read(MAX_READ + 1)), ""
            except OSError as e:
                return None, f"`{rel}` cannot be read ({e.__class__.__name__})"
        return None, f"`{rel}` does not exist"

    def _base_lines(self, rel: str) -> "tuple[int | None, str]":
        """(the file's line count, "") in the task's start commit of the repository that holds it (the longest code
        root it is under, its prefix stripped; else the project's own)."""
        if self.base_why:
            return None, self.base_why
        scope = next((sc for sc in sorted(self.scopes, key=len, reverse=True) if sc and _inside(rel, sc)), "")
        repo, head = self.scopes.get(scope, (None, None))
        if repo is None or not head:
            return None, f"`{rel}` is not in the task's start commit (none is recorded for {scope or 'the project'})"
        # `./`: the path from the repository's folder — a project in a subfolder of its repository (impl round 1)
        inner = "./" + (rel[len(scope) + 1:] if scope else rel)
        from tasks import red
        try:
            with red._no_git_overrides():
                size = red._git(repo, ["cat-file", "-s", f"{head}:{inner}"])
                if size.returncode != 0:
                    return None, f"`{rel}` is not in the task's start commit"
                if int(size.stdout.strip() or 0) > MAX_READ:
                    return None, f"`{rel}` is over {MAX_READ // (1024 * 1024)} MiB in the start commit: not read"
                blob = red._git(repo, ["cat-file", "blob", f"{head}:{inner}"], text=False)
        except (OSError, ValueError, subprocess.SubprocessError) as e:   # git missing or failing: this place unread
            return None, f"git could not read `{rel}` in the start commit ({e.__class__.__name__})"
        if blob.returncode != 0:
            return None, f"`{rel}` is not a file in the task's start commit"
        return _count_lines(blob.stdout), ""

    def locate(self, item: str, *, whole_file: bool) -> "tuple[str | None, str]":
        """(the project-relative path a place names, "") when it exists on its side with its lines inside it, else
        (None, why). The first candidate path that exists decides; a path that names no file as written resolves by
        its unique suffix."""
        got = _parse_location(item, whole_file)
        if isinstance(got, str):
            return None, got
        base, path, _first, last = got
        cands = self._candidates(path)
        if isinstance(cands, str):
            return None, cands
        lines = self._base_lines if base else self._current_lines
        why = ""
        for rel in cands:
            n, why_here = lines(rel)
            if n is None:
                why = why or why_here
                if base and self.base_why:
                    return None, why
                if "does not exist" in why_here or "not in the task's start commit" in why_here:
                    continue
                return None, why_here
            if last is not None and last > n:
                return None, f"`{rel}` has {n} line(s), not {last}" + (" in the start commit" if base else "")
            return rel, ""
        hit, why_suffix = self._by_suffix(cands[0], base)
        if why_suffix:
            return None, why_suffix
        if hit:
            n, why_here = lines(hit)
            if n is None:
                return None, why_here
            if last is not None and last > n:
                return None, f"`{hit}` has {n} line(s), not {last}" + (" in the start commit" if base else "")
            return hit, ""
        return None, why


def _one(s: str) -> str:
    """A value as one printable line of judge.md."""
    return "".join(ch if ch.isprintable() else " " for ch in str(s))


def render(answer: Answer, label: str, start: int = 1) -> "tuple[list[str], int]":
    """The CLI's lines for one seat's records in judge.md, numbered from `start` across the round, and the next
    number. A finding: `F<n> | <severity> | settled: <…> | <seat>`, then its fields indented; a broken record:
    `F<n> | BROKEN | <why> | <seat>`, then its lines quoted; a NONE: `NONE | read: … | <seat>` and `checked:`. The
    judge's values never open a line."""
    out: "list[str]" = []
    n = start
    for r in answer.records:
        if r.kind == "FINDING":
            out.append(f"F{n} | {r.severity} | settled: {r.settled} | {_one(label)}")
            out += [f"    {k}: {_one(r.fields[k])}" for k in ("where", "scenario", "drum", "fix")]
            n += 1
        elif r.kind == "BROKEN":
            out.append(f"F{n} | BROKEN | {_one(r.why)} | {_one(label)}")
            out += [f"    > {_one(ln)}" for ln in r.lines]
            n += 1
        else:
            out.append(f"NONE | read: {_one(r.fields['read'])} | {_one(label)}")
            out.append(f"    checked: {_one(r.fields['checked'])}")
    if answer.cap:
        out.append(f"CAP {answer.cap[0]}/{answer.cap[1]} {answer.cap[2]}")
    return out, n


def count_records(text: str) -> "dict[str, int]":
    """The counted findings among the CLI's record lines (`render`) in `text`: Critical and Important, not
    `settled: SETTLED`. A judge's own lines are quoted in judge.md, so none of them is one."""
    counts = {"critical": 0, "important": 0}
    for m in _WRITER_RE.finditer(text or ""):
        if m.group(1) != "Minor" and m.group(2) != "SETTLED":
            counts[m.group(1).lower()] += 1
    return counts
