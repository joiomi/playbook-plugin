"""A review's fixed copy of the tree (task 231, PHASE-B item 7).

The owner, 2026-10-17 (~14:20, an AskUserQuestion answer): "Copie pe aceeași cale (Recomandat)" — before each review
a copy of the files; bubblewrap shows it to the judges exactly at the project's path; the agent works on the real tree
meanwhile; the tamper guard checks the copy; deleted after.

What a copy is: for the outer repository and each `code_roots` repository (each in a real directory inside the
project), `git clone --local --mirror` into `<copy>/<scope>/.git` (every ref; objects hardlinked where one file system
holds both, copied otherwise — never alternates, which would point into the path the mount covers), the live
`.git/config` and `info/` copied, `core.bare=false`, HEAD as live, the live index, then every file `git ls-files -co
--exclude-standard` lists copied with its mode (links as links). Entries git ignores are NOT copied: the sandbox binds
them read-only from the live tree at their own path (`live_binds`), so the judges see them as before — the seal never
covered ignored files and does not now. Their mountpoints are made in the copy.

The seal, by content: the copy is kept only when its fingerprint (`core.tree_state_fingerprint` with the LIVE config)
equals the stamp the review took from the live tree, each scope's working-tree object (`post_d6.worktree_tree`) equals
the tree the review recorded (a panel's snapshot `base`, the post-panel judge's delta `cur`), and each scope's HEAD is
the recorded commit. A tree that moved while the copy was made is tried again, `COPY_ATTEMPTS` times, then refused
(`Moved`) — the caller spends nothing and never falls back to the live tree after trying a copy (plan panel P8).

Lifetime: a copy lives in `<lane>/journal/review-views/<run>-<attempt>/` (git-ignored by init's block — refused when
it is not) with an `flock` held on `<run>-<attempt>.lock` beside it for the review's whole life; `sweep` removes only
copies whose lock it can take (an owner that died), never a held one.

`availability` says, BEFORE anything is spent, why no copy is possible here (the review then reads the live tree as
before, and says so first): already inside a sandbox (no new mount), no bubblewrap, a project that is a subdirectory of
its repository or a linked worktree, a linked `.agent` or lane, a code root that is a link or not its own repository, a
submodule or an untracked nested repository in a scope, the copy folder not ignored, or more than `MAX_FILES` listed
files / `MAX_BYTES` of listed content / `MAX_LIVE_BINDS` ignored entries.
"""
from __future__ import annotations

import errno
import fcntl
import os
import shutil
import stat
import subprocess
from pathlib import Path

VIEWS_DIRNAME = "review-views"
MAX_FILES = 50_000
MAX_BYTES = 1 << 30
MAX_LIVE_BINDS = 2_000
COPY_ATTEMPTS = 3
_GIT_TIMEOUT = 300
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES", "GIT_PREFIX")


class Unavailable(Exception):
    """No copy can be made here; the message is the reason the review prints."""


class Moved(Exception):
    """The tree kept changing while the copy was made; the review is refused before anything is spent."""


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}


def _git(args: "list[str]", cwd: "Path | str", *, check: bool = True, binary: bool = False):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, env=_env(), timeout=_GIT_TIMEOUT,
                       **({} if binary else {"text": True, "encoding": "utf-8", "errors": "surrogateescape"}))
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, ["git", *args], r.stdout, r.stderr)
    return r


def views_dir(project: "Path | str", task_file: "Path | str | None" = None) -> Path:
    """`<lane>/journal/review-views` — the reviewed task's lane, else the project's."""
    if task_file is not None:
        lane = Path(task_file).parent.parent.parent
    else:
        from tasks.core import resolve_agent_dir
        lane = resolve_agent_dir(Path(project))
    return lane / "journal" / VIEWS_DIRNAME


def _scopes(project: Path, cfg: dict) -> "dict[str, Path]":
    from tasks.core import _tail_cert_scopes
    return _tail_cert_scopes(project, cfg)


def _listed(scope: Path) -> "list[str]":
    out = _git(["ls-files", "-co", "--exclude-standard", "-z"], scope, binary=True).stdout
    return [os.fsdecode(x) for x in out.split(b"\0") if x]


def _norm(name: str) -> str:
    """A scope's name as a clean project-relative path ('' for the project): `./sub` and `sub/` are `sub` (impl round
    1, G1 — codex measured a live code root bound over its copy when the config said `./sub`)."""
    n = os.path.normpath(name) if name else ""
    return "" if n in ("", ".") else n.replace(os.sep, "/")


def _nested(name: str, roots: "list[str]") -> bool:
    return any(name == r or name.startswith(r + "/") for r in roots)


def _inner_roots(scope_name: str, names: "list[str]") -> "list[str]":
    """The code roots below `scope_name`, relative to it (normalized)."""
    base = _norm(scope_name)
    pre = base + "/" if base else ""
    out = []
    for n in names:
        nn = _norm(n)
        if nn and nn != base and nn.startswith(pre):
            out.append(nn[len(pre):])
    return out


def _expand_around_roots(project: Path, full: str, roots: "list[str]", out: "set[str]") -> None:
    """`full` is ignored and holds a code root: bind its other children live, descend toward the root (impl round 1,
    G7 — `deps/` ignored with `deps/app` a code root lost `deps/tool`)."""
    try:
        children = sorted(os.scandir(project / full), key=lambda e: e.name)
    except OSError:
        return
    for c in children:
        rel = f"{full}/{c.name}"
        if rel in roots:
            continue
        if any(r.startswith(rel + "/") for r in roots) and c.is_dir(follow_symlinks=False):
            _expand_around_roots(project, rel, roots, out)
        else:
            out.add(rel)


def ignored_entries(scopes: "dict[str, Path]") -> "list[str]":
    """Project-relative entries git ignores in each scope (a directory collapsed to one entry), minus `.agent` (the
    record masks' business) and the code roots (copied); an ignored folder that holds a code root is opened and its
    other children listed."""
    names = sorted(scopes)
    roots = sorted(_norm(n) for n in names if _norm(n))
    project = scopes[""] if "" in scopes else Path(".")
    out: "set[str]" = set()
    for name in names:
        base = _norm(name)
        raw = _git(["ls-files", "-o", "-i", "--exclude-standard", "--directory", "-z"], scopes[name],
                   binary=True).stdout
        for x in raw.split(b"\0"):
            if not x:
                continue
            rel = os.fsdecode(x).rstrip("/")
            full = _norm(f"{base}/{rel}" if base else rel)
            if not base and (full == ".agent" or full.startswith(".agent/")):
                continue
            if full in roots:
                continue
            if any(r.startswith(full + "/") for r in roots):
                _expand_around_roots(project, full, roots, out)
                continue
            out.add(full)
    return sorted(out)


def agent_ignored(project: Path) -> "list[str]":
    """Project-relative `.agent` entries git ignores (a directory collapsed to one) — the only `.agent` entries a judge
    may see from the live tree (impl round 1, G5: a task folder made after the seal was shown live)."""
    raw = _git(["ls-files", "-o", "-i", "--exclude-standard", "--directory", "-z", "--", ".agent"], project,
               binary=True).stdout
    return sorted(_norm(os.fsdecode(x).rstrip("/")) for x in raw.split(b"\0") if x)


def availability(project: "Path | str", cfg: dict, task_file: "Path | str | None" = None) -> str:
    """'' when a copy can be made and mounted for a review here; else the reason, which the review prints first."""
    from provider import sandbox as _sb
    try:
        ok, why = _sb.view_mount_available()
    except Exception as e:      # noqa: BLE001 — no copy beats a wrong one
        ok, why = False, f"the sandbox could not be asked ({e})"
    if not ok:
        return why or "no copy can be mounted here"
    project = Path(project)
    try:
        real = project.resolve()
        agent = project / ".agent"
        if agent.is_symlink():
            return ".agent is a link — a copy cannot freeze what it points to"
        if agent.is_dir():
            for e in os.scandir(agent):
                if e.is_symlink() and os.path.isdir(e.path):
                    return f".agent/{e.name} is a linked folder — a copy cannot freeze what it points to"
        top = _git(["rev-parse", "--show-toplevel"], project, check=False)
        if top.returncode != 0:
            return "the project is not a git repository"
        if Path(top.stdout.strip()).resolve() != real:
            return "the project is a subdirectory of its git repository"
        if not (project / ".git").is_dir():
            return "the project is a linked worktree (its .git is a file)"
        scopes = _scopes(project, cfg)
        from tasks.core import _code_roots, owner_exclude_covers_behavioral
        covers, hits = owner_exclude_covers_behavioral(project, cfg)
        if covers:
            return ("fingerprint_exclude hides code (" + ", ".join(hits[:3]) + ") — a copy could not be sealed "
                    "over what the fingerprint does not see")
        for rel in _code_roots(cfg):
            if rel not in scopes:
                return f"code root {rel} resolves outside the project"
        files = 0
        size = 0
        for name, path in scopes.items():
            label = name or "the project"
            if name:
                if (project / name).is_symlink() or path.resolve() != real / name:
                    return f"code root {name} is a link"
                if not (path / ".git").is_dir():
                    return f"code root {name} is not its own repository (or is a linked worktree)"
                stop = _git(["rev-parse", "--show-toplevel"], path, check=False)
                if stop.returncode != 0 or Path(stop.stdout.strip()).resolve() != path.resolve():
                    return f"code root {name} is not the top of its repository"
            staged = _git(["ls-files", "-s", "-z"], path, binary=True).stdout
            if any(x.startswith(b"160000 ") for x in staged.split(b"\0")):
                return f"{label} holds a submodule"
            inner = _inner_roots(name, list(scopes))
            for n in _listed(path):
                if _nested(n, inner):
                    continue
                if n.endswith("/"):
                    return f"{label} holds an untracked nested repository ({n}) — add it to code_roots or ignore it"
                try:
                    st = os.lstat(path / n)
                except FileNotFoundError:
                    continue
                if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                    return f"{label} lists a special file ({n})"
                files += 1
                size += st.st_size if stat.S_ISREG(st.st_mode) else 0
        if files > MAX_FILES:
            return f"{files} listed files is over the copy's cap of {MAX_FILES}"
        if size > MAX_BYTES:
            return f"{size} bytes of listed files is over the copy's cap of {MAX_BYTES}"
        if len(ignored_entries(scopes)) > MAX_LIVE_BINDS:
            return f"more than {MAX_LIVE_BINDS} ignored entries to show live"
        if task_file is not None:
            try:
                rel_tf = Path(task_file).resolve().relative_to(real)
            except ValueError:
                return "the task file is outside the project"
            if _git(["check-ignore", "-q", "--no-index", str(rel_tf)], project, check=False).returncode == 0:
                return f"git ignores the task's records ({rel_tf}) — a copy of what git lists would not hold them"
        vd = views_dir(project, task_file)
        try:
            rel_vd = (vd / "probe").resolve().relative_to(real)
        except ValueError:
            return "the copy folder is outside the project"
        if _git(["check-ignore", "-q", "--no-index", str(rel_vd)], project, check=False).returncode != 0:
            return (f"the copy folder ({rel_vd.parent}) is not ignored by git — init's block ignores "
                    ".agent/journal/; run /playbook:init")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return f"the tree could not be read for a copy ({e})"
    return ""


def _clone_scope(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    gd = dst / ".git"
    _git(["clone", "-q", "--local", "--mirror", str(src), str(gd)], dst)
    src_git = Path(_git(["rev-parse", "--absolute-git-dir"], src).stdout.strip())
    shutil.copyfile(src_git / "config", gd / "config")
    _git(["--git-dir", str(gd), "config", "core.bare", "false"], dst)
    _git(["--git-dir", str(gd), "config", "--unset-all", "core.worktree"], dst, check=False)
    if (src_git / "info").is_dir():
        shutil.copytree(src_git / "info", gd / "info", dirs_exist_ok=True, symlinks=True)
    head_ref = _git(["symbolic-ref", "-q", "HEAD"], src, check=False).stdout.strip()
    sha = _git(["rev-parse", "-q", "--verify", "HEAD"], src, check=False).stdout.strip()
    if head_ref:
        _git(["--git-dir", str(gd), "symbolic-ref", "HEAD", head_ref], dst)
        if sha:
            _git(["--git-dir", str(gd), "update-ref", head_ref, sha], dst)
    elif sha:
        _git(["--git-dir", str(gd), "update-ref", "--no-deref", "HEAD", sha], dst)
    idx = Path(_git(["rev-parse", "--git-path", "index"], src).stdout.strip())
    if not idx.is_absolute():
        idx = src / idx
    if idx.is_file():
        shutil.copy2(idx, gd / "index")     # copy2: its own mtime kept (git's racy-clean rule, post_d6.worktree_tree)
    for shared in src_git.glob("sharedindex.*"):
        shutil.copy2(shared, gd / shared.name)


def _is_record(name: str) -> bool:
    """A path under `.agent` that is (or is inside) a conversation record — never copied: the judges must not read
    one, and the copies folder could expose it (impl round 1, G2 — codex read an untracked chat log through it)."""
    parts = name.split("/")
    if parts[0] != ".agent":
        return False
    from provider.sandbox import _is_record_dir, _is_record_file
    return any(_is_record_dir(x) for x in parts[1:-1]) or _is_record_file(parts[-1]) or _is_record_dir(parts[-1])


def _copy_listed_files(src: Path, dst: Path, skip: "list[str]", outer: bool = False) -> "tuple[int, int]":
    files = size = 0
    for name in _listed(src):
        if _nested(name, skip) or name.endswith("/") or (outer and _is_record(name)):
            continue
        s = src / name
        try:
            st = os.lstat(s)
        except FileNotFoundError:
            continue                    # a tracked file deleted in the work tree is absent from the copy too
        d = dst / name
        d.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(st.st_mode):
            os.symlink(os.readlink(s), d)
        elif stat.S_ISREG(st.st_mode):
            shutil.copyfile(s, d, follow_symlinks=False)
            os.chmod(d, stat.S_IMODE(st.st_mode))
            size += st.st_size
        else:
            raise Unavailable(f"a special file is listed ({name})")
        files += 1
    return files, size


def _make_mountpoints(project: Path, root: Path, live: "list[str]") -> "list[str]":
    """An empty dir or file in the copy at each ignored entry's path, for its live bind; a link is copied as a link
    and needs no bind. Returns the entries to bind."""
    binds = []
    for rel in live:
        src = project / rel
        dst = root / rel
        if os.path.lexists(dst):
            binds.append(rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if os.path.islink(src):
            os.symlink(os.readlink(src), dst)
            continue
        if src.is_dir():
            dst.mkdir()
        else:
            dst.touch()
        binds.append(rel)
    return binds


class View:
    """A sealed copy and the lock that keeps it alive."""

    def __init__(self, root: Path, project: Path, lock_fd: int, lock_path: Path, live_binds: "list[str]",
                 vd: Path, scope_names: "list[str] | None" = None, agent_live: "list[str] | None" = None):
        self.root = root
        self.project = project
        self.lock_fd = lock_fd
        self.lock_path = lock_path
        self.live_binds = live_binds
        self._vd = vd
        self.scope_names = list(scope_names or [""])
        self.agent_live = list(agent_live or [])

    def scope_path(self, name: str) -> Path:
        return self.root / name if name else self.root

    def matches(self, fingerprint: str, cfg: dict) -> bool:
        from tasks.core import tree_state_fingerprint
        return bool(fingerprint) and tree_state_fingerprint(self.root, cfg=cfg) == fingerprint

    def sealed(self, fingerprint: str, cfg: dict, trees: "dict[str, str]", heads: "dict[str, str] | None",
               exclude: "list[str]") -> bool:
        if not self.matches(fingerprint, cfg):
            return False
        from tasks.post_d6 import worktree_tree
        if trees and {_norm(n) for n in trees} != {_norm(n) for n in self.scope_names}:
            return False        # impl round 1 (G4): every copied scope is a recorded one, and only those
        for name, want in (trees or {}).items():
            if not want or worktree_tree(self.scope_path(name), exclude) != want:
                return False
        for name, want in (heads or {}).items():
            if want and _git(["rev-parse", "-q", "--verify", "HEAD"], self.scope_path(name),
                             check=False).stdout.strip() != want:
                return False
        return True

    def sandbox_view(self):
        from provider import sandbox as _sb
        return _sb.ProjectView(root=str(self.root),
                               live=tuple(str(self.project / rel) for rel in self.live_binds),
                               agent_live=tuple(str(self.project.resolve() / rel) for rel in self.agent_live),
                               hide=(str(self._vd.resolve()),))

    def remove(self) -> None:
        try:
            if self.root.exists():
                _remove_tree(self._vd, self.root)
        finally:
            if self.lock_fd >= 0:
                try:
                    os.close(self.lock_fd)
                except OSError:
                    pass
                self.lock_fd = -1
            try:
                self.lock_path.unlink()
            except OSError:
                pass


def _remove_tree(base: "Path | str", target: "Path | str", task_file: "Path | str | None" = None) -> None:
    """Delete `target`, a copy directly inside the views folder; anything else raises ValueError. `base` is the views
    folder or the project (its views folder is then used)."""
    base = Path(base)
    vd = base if base.name == VIEWS_DIRNAME else views_dir(base, task_file)
    t = Path(target)
    if t.is_symlink() or not t.is_dir():
        raise ValueError(f"{t} is not a copy folder")
    if t.resolve().parent != vd.resolve() or vd.is_symlink():
        raise ValueError(f"{t} is not inside {vd}")

    def _writable_then_retry(func, path, _exc):
        # a read-only folder in the copy (a copied 0555 `info/`, a listed file's mode) — ours to delete
        parent = os.path.dirname(path)
        try:
            os.chmod(parent, 0o700)
            if os.path.isdir(path) and not os.path.islink(path):
                os.chmod(path, 0o700)
            func(path)
        except OSError:
            pass

    shutil.rmtree(t, onerror=_writable_then_retry)


def _lock(path: Path) -> int:
    """An exclusive `flock` on the file at `path` — re-taken when another process's sweep replaced the file between
    the open and the lock (impl round 1, opus: the lock held must be the file a later sweep finds)."""
    for _ in range(5):
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise
        try:
            if os.fstat(fd).st_ino == os.stat(str(path), follow_symlinks=False).st_ino:
                return fd
        except OSError:
            pass
        os.close(fd)
    raise OSError(errno.EAGAIN, f"{path} kept being replaced")


def sweep(project: "Path | str", task_file: "Path | str | None" = None) -> int:
    """Remove the copies (and stray locks) whose lock nobody holds; never a held one. Returns copies removed."""
    vd = views_dir(project, task_file)
    if not vd.is_dir() or vd.is_symlink():
        return 0
    removed = 0
    for entry in sorted(os.scandir(vd), key=lambda e: e.name):
        if entry.name.endswith(".lock") or entry.is_symlink() or not entry.is_dir():
            continue
        lock = vd / (entry.name + ".lock")
        try:
            fd = _lock(lock)
        except OSError as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                continue                # held: a review is reading it
            continue
        try:
            _remove_tree(vd, vd / entry.name)
            removed += 1
        except (OSError, ValueError):
            pass
        finally:
            os.close(fd)
            try:
                lock.unlink()
            except OSError:
                pass
    for entry in os.scandir(vd):
        if entry.name.endswith(".lock") and not (vd / entry.name[:-5]).exists():
            try:
                fd = _lock(vd / entry.name)
            except OSError:
                continue
            os.close(fd)
            try:
                (vd / entry.name).unlink()
            except OSError:
                pass
    return removed


def make(project: "Path | str", cfg: dict, *, run_id: str, fingerprint: str, trees: "dict[str, str]",
         heads: "dict[str, str] | None" = None, task_file: "Path | str | None" = None,
         exclude: "list[str] | None" = None, attempts: int = 0) -> View:
    """Make, seal and lock a copy (see the module docstring). `Unavailable` when it cannot be made, `Moved` when the
    tree kept changing. Nothing is left behind on either."""
    from tasks.core import _fingerprint_exclude_pathspecs
    from tasks.post_d6 import patch_dir_ready
    project = Path(project)
    if exclude is None:
        exclude = _fingerprint_exclude_pathspecs(cfg)
    vd = views_dir(project, task_file)
    try:
        patch_dir_ready(vd)
    except OSError as e:
        raise Unavailable(f"the copy folder could not be made ({e})") from None
    sweep(project, task_file)
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in (run_id or "run"))[:64]
    scopes = _scopes(project, cfg)
    names = sorted(scopes)
    for attempt in range(attempts or COPY_ATTEMPTS):
        base = f"{safe_id}-{attempt}"
        root = vd / base
        lock_path = vd / (base + ".lock")
        if os.path.lexists(root):
            raise Unavailable(f"{root} already exists")
        try:
            fd = _lock(lock_path)
        except OSError as e:
            raise Unavailable(f"the copy's lock could not be taken ({e})") from None
        view = None
        try:
            root.mkdir()
            for name in names:
                _clone_scope(scopes[name], root / name if name else root)
            for name in names:
                _copy_listed_files(scopes[name], root / name if name else root, _inner_roots(name, names),
                                   outer=not name)
            for name in names:
                _git(["update-index", "-q", "--refresh"], root / name if name else root, check=False)
            live = ignored_entries(scopes)
            roots = [_norm(n) for n in names if _norm(n)]
            if any(x in roots or any(r.startswith(x + "/") for r in roots) for x in live):
                raise Unavailable("an ignored entry overlaps a copied code root")   # never bind live over a copy
            binds = _make_mountpoints(project, root, live)
            view = View(root, project, fd, lock_path, binds, vd, scope_names=names,
                        agent_live=agent_ignored(project))
            if view.sealed(fingerprint, cfg, trees, heads, exclude):
                return view
            cause = "the copy is not the recorded tree"
        except (OSError, subprocess.SubprocessError, Unavailable) as e:
            # impl round 1 (G6): once a copy is tried, a failure is retried and then refused — never a live review
            cause = str(e) or type(e).__name__
        _discard(view, root, fd, lock_path, vd)
    raise Moved(f"the tree changed while its copy was made, {attempts or COPY_ATTEMPTS} times (last: {cause}) — let "
                "nothing write it until the review prints that its copy is ready, then run the review again")


def _discard(view: "View | None", root: Path, fd: int, lock_path: Path, vd: Path) -> None:
    if view is not None:
        view.remove()
        return
    try:
        if root.is_dir() and not root.is_symlink():
            _remove_tree(vd, root)
    except (OSError, ValueError):
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        lock_path.unlink()
    except OSError:
        pass
