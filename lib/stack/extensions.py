"""Extensions from a git repo: what a repo offers, the copy, and where it came from.

`stack extensions list`, `add` and `update` (cli.py) are built on this
module; it fetches, reads and copies, and prints nothing. ADR-022 has
the decisions behind it.

An extension is copied out of its repo, not cloned. `fetch` puts the repo
into a temporary directory, `Fetched` says which stacklets it offers, and
`install` writes one of them as a plain folder into an extension dir,
where `Stack.discover` reads it like any other. The folder carries a
`.source.toml` (`Source`): the repo, the path in it, the commit and the
tree of that path, so the folder can later be compared with its repo
without being a git checkout, and every file the copy wrote, so an update
knows which files are the extension's. A folder without one was put there
by hand, and nothing here touches it. `replace` puts a newer version in
place of an installed one; it deletes only files that `install` or
`replace` wrote, and only while they are unchanged.

Where a repo's stacklets are:

    repo/stacklet.toml      the repo is one extension (path ".")
    repo/<x>/stacklet.toml  otherwise, each folder one level down is one

Never deeper, and a root manifest means the folders are not searched.

    with fetch(url) as repo:
        offer = repo.offer("hue")
        install(repo, offer, Path("~/stack-extensions").expanduser())
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

# The catalog: extensions the project reviewed, one per root folder.
# `[core] extension_catalog` points elsewhere, for a fork or a test.
DEFAULT_CATALOG = "https://github.com/famstack-dev/famstack-extensions"

SOURCE_FILE = ".source.toml"

# Where a copy is written before it is renamed into place: a folder in the
# extension dir, so the rename stays on one disk, and two levels down, where
# discovery (`*/stacklet.toml`) and the bot runner (`*/bot/bot.toml`) do not
# look. A copy cut off by a crash stays invisible there instead of claiming
# the extension's id.
INCOMING = ".incoming"

# What `stack extensions list` last read from the catalog, in the
# instance's `.stack/`. Never in the extension dir: listing must not
# create one.
CATALOG_CACHE = "catalog.json"

# The manifest fields a stacklet needs to be listed and installed, the
# same rule the catalog's CI checks (script/check-manifests.py there).
REQUIRED = ("id", "name", "description")

# The id names a folder, a container, an env prefix and a secret prefix,
# so it is a plain lowercase name. It also keeps `../x` out of the path.
PLAIN_ID = re.compile(r"[a-z][a-z0-9_]*")

# A first fetch of a big repo over a slow line takes a while; a fetch that
# hangs on a dead server should not hang the command for ever.
GIT_TIMEOUT = 180


# ── Where the catalog is, and what counts as it ──────────────────────────

def catalog_url(config: dict) -> str:
    """The catalog's URL from stack.toml: `[core] extension_catalog`."""
    return config.get("core", {}).get("extension_catalog") or DEFAULT_CATALOG


def _normal(url: str) -> str:
    return url.strip().rstrip("/").removesuffix(".git")


def same_repo(a: str, b: str) -> bool:
    """Two spellings of one repo: with or without `.git` and a final slash."""
    return _normal(a) == _normal(b)


def is_url(arg: str) -> bool:
    """A git URL or path, as opposed to a catalog id, which is a plain name."""
    return "/" in arg or ":" in arg


def short_url(url: str) -> str:
    """The URL without its scheme and `.git`, for a column in a list."""
    return re.sub(r"^[a-z+]+://", "", _normal(url))


# ── Where an installed extension came from ───────────────────────────────

@dataclass(frozen=True)
class Source:
    """An installed extension's `.source.toml`.

    `tree` is the git tree of `path` at `commit`. It changes exactly when
    something under that path does, so a commit elsewhere in a repo of
    several extensions is not an update to this one. `branch` is a record
    only: an update reads the repo's default branch, as `list` does.

    `files` holds every file and symlink the copy wrote, by path, as
    `file_record` describes it. It is taken from what was written, not
    from the repo, so `export-ignore`, line endings and git filters make
    no difference. A file not in it is not the extension's.
    """

    url: str
    path: str
    branch: str
    commit: str
    tree: str
    files: dict[str, str] = field(default_factory=dict)

    def text(self) -> str:
        """The file's text. A JSON string is a valid TOML basic string."""
        lines = ["# Where this extension came from, written by `stack extensions`."]
        lines += [f"{key:<6} = {json.dumps(getattr(self, key))}" for key in _FIELDS]
        lines += ["", "# Every file the copy wrote. An update replaces only these, while unchanged.",
                  "[files]"]
        lines += [f"{json.dumps(path)} = {json.dumps(record)}" for path, record in sorted(self.files.items())]
        return "\n".join(lines) + "\n"


_FIELDS = ("url", "path", "branch", "commit", "tree")


def read_source(folder: Path) -> Source | None:
    """The source of the extension in `folder`, or None for one put there
    by hand, or a record that does not read or lists no files. Without the
    files, the extension's files cannot be told from the admin's."""
    try:
        raw = tomllib.loads((folder / SOURCE_FILE).read_text(encoding="utf-8"))
        files = {str(path): str(record) for path, record in raw["files"].items()}
        return Source(**{key: str(raw[key]) for key in _FIELDS}, files=files)
    except (OSError, tomllib.TOMLDecodeError, KeyError, AttributeError):
        return None


def file_record(path: Path) -> str | None:
    """What `path` is, as `Source.files` records it: `file`, `exec` or
    `link`, then the sha256 of its content (of its target, for a link).
    None when nothing is there; `other` for a directory or anything else
    where a file was."""
    try:
        info = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return f"link {hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()}"
    if not stat.S_ISREG(info.st_mode):
        return "other"
    kind = "exec" if info.st_mode & 0o111 else "file"
    with path.open("rb") as f:
        return f"{kind} {hashlib.file_digest(f, 'sha256').hexdigest()}"


def record_files(folder: Path) -> dict[str, str]:
    """Every file and symlink under `folder` with its record, by path.
    A symlink to a directory is one entry; it is not followed."""
    found = {}
    for root, dirs, files in os.walk(folder):
        here = Path(root)
        for name in [*files, *(d for d in dirs if (here / d).is_symlink())]:
            rel = (here / name).relative_to(folder).as_posix()
            if rel != SOURCE_FILE and (record := file_record(here / name)):
                found[rel] = record
    return found


# ── What a repo offers ───────────────────────────────────────────────────

class FetchFailed(Exception):
    """git could not fetch the repo. The message is git's own reason."""


class CopyFailed(Exception):
    """The copy into the extension dir did not complete; nothing is left of it."""


@dataclass
class Brings:
    """The code an extension would run, read from its files.

    Containers come from the compose file, the rest from the folders the
    framework picks up by convention: hooks and migrations run on the
    host with the stack's own Python, CLI plugins as `stack <id> <cmd>`.
    """

    containers: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    migrations: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    bots: list[str] = field(default_factory=list)


@dataclass
class Offer:
    """One stacklet a repo offers: its path there and its manifest.

    `manifest` is None when stacklet.toml does not parse. The id falls
    back to the folder's name, as discovery's does, so a broken manifest
    can still be named and refused.
    """

    path: str
    tree: str
    manifest: dict | None

    @property
    def id(self) -> str:
        fallback = "" if self.path == "." else self.path
        return str((self.manifest or {}).get("id") or fallback)

    @property
    def name(self) -> str:
        return str((self.manifest or {}).get("name") or self.id)

    @property
    def stage(self) -> str:
        return str((self.manifest or {}).get("stage") or "stable")

    def problems(self) -> list[str]:
        """Why this manifest cannot be installed, one line each; none if it can."""
        where = "stacklet.toml" if self.path == "." else f"{self.path}/stacklet.toml"
        if self.manifest is None:
            return [f"{where} does not parse"]
        found = [f"{where} has no {key}" for key in REQUIRED if not self.manifest.get(key)]
        sid = self.manifest.get("id")
        if sid and not (isinstance(sid, str) and PLAIN_ID.fullmatch(sid)):
            found.append(f"{where}: id {sid!r} is not a plain folder name "
                         f"(lowercase letters, digits and _)")
        return found


def _git(*args: str, cwd: Path | None = None, stdin: str | None = None) -> str:
    """One git command; its output, or FetchFailed with git's reason.

    No terminal prompt: a repo that wants a password (GitHub answers a
    missing repo that way) fails instead of waiting for someone to type.
    """
    try:
        result = subprocess.run(
            ["git", *(["-C", str(cwd)] if cwd else []), *args],
            input=stdin, capture_output=True, text=True, timeout=GIT_TIMEOUT,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        raise FetchFailed(f"git {args[0]} took longer than {GIT_TIMEOUT}s") from None
    if result.returncode != 0:
        raise FetchFailed(_reason(result.stderr) or f"git {args[0]} failed")
    return result.stdout.strip()


def _reason(stderr: str) -> str:
    """git's own reason: its first `fatal:` or `error:` line. What follows
    is advice ("Please make sure you have the correct access rights")."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    return next((ln for ln in lines if ln.startswith(("fatal:", "error:"))), lines[-1] if lines else "")


def _stacklet_paths(files: list[str]) -> list[str]:
    """Where a repo's stacklets are: "." for a root manifest, else every
    folder one level down that holds one."""
    if "stacklet.toml" in files:
        return ["."]
    return sorted(path.split("/")[0] for path in files
                  if path.count("/") == 1 and path.endswith("/stacklet.toml"))


def _in(path: str, name: str) -> str:
    """`name` inside the stacklet at `path` of a repo."""
    return name if path == "." else f"{path}/{name}"


# Files read before anything is copied: the manifests to list and check,
# and what the warning names. Fetched in one round trip.
_READ_FIRST = ("stacklet.toml", "docker-compose.yml", "bot/bot.toml")


class Fetched:
    """A repo fetched into a temporary directory, at its default branch.

    Only trees and the few files `_READ_FIRST` names are fetched at
    first. A blob-less clone plus a sparse checkout gets those files in
    one round trip; reading them one by one costs one each. The rest of
    the chosen path arrives when it is copied.
    """

    def __init__(self, url: str, workdir: Path):
        self.url = url
        self.repo = workdir / "repo"
        # `--`: a URL that starts with a dash is still a URL, never an option.
        _git("clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout",
             "--", url, str(self.repo))
        self.commit = _git("rev-parse", "HEAD", cwd=self.repo)
        self.branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.repo)
        short, date, subject = _git(
            "log", "-1", "--format=%h%x00%cs%x00%s", cwd=self.repo).split("\0", 2)
        self.short, self.date, self.subject = short, date, subject
        listed = _git("ls-tree", "-r", "--name-only", "-z", "HEAD", cwd=self.repo)
        self.files = [f for f in listed.split("\0") if f]
        paths = _stacklet_paths(self.files)
        if paths:
            patterns = [f"/{_in(path, name)}" for path in paths for name in _READ_FIRST]
            _git("sparse-checkout", "set", "--no-cone", "--stdin", cwd=self.repo,
                 stdin="\n".join(patterns) + "\n")
            _git("checkout", "--quiet", cwd=self.repo)
        self.offers = [Offer(path, self._tree(path), self._manifest(path)) for path in paths]

    def _tree(self, path: str) -> str:
        return _git("rev-parse", f"HEAD:{'' if path == '.' else path}", cwd=self.repo)

    def _manifest(self, path: str) -> dict | None:
        try:
            return tomllib.loads((self.repo / _in(path, "stacklet.toml")).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return None

    def offer(self, sid: str) -> Offer | None:
        """The stacklet with this id, or None."""
        return next((o for o in self.offers if o.id == sid), None)

    def offer_at(self, path: str) -> Offer | None:
        """The stacklet at this path of the repo, or None. An update finds
        an extension by where it was, so a changed id can be refused."""
        return next((o for o in self.offers if o.path == path), None)

    def source(self, offer: Offer, files: dict[str, str]) -> Source:
        return Source(url=self.url, path=offer.path, branch=self.branch,
                      commit=self.commit, tree=offer.tree, files=files)

    def brings(self, offer: Offer) -> Brings:
        """The code `offer` would run, from its file names and compose file."""
        prefix = "" if offer.path == "." else f"{offer.path}/"
        names = [f.removeprefix(prefix) for f in self.files if f.startswith(prefix)]
        found = Brings()
        compose = self.repo / _in(offer.path, "docker-compose.yml")
        if compose.is_file():
            found.containers = compose_services(compose.read_text(encoding="utf-8", errors="replace"))
        for name in sorted(names):
            folder, _, rest = name.partition("/")
            stem, dot, ext = rest.rpartition(".")
            if "/" in rest or not dot:
                continue
            if folder == "hooks" and stem.startswith("on_") and ext in ("py", "sh"):
                found.hooks.append(stem)
            elif folder == "migrations" and ext == "py" and not stem.startswith("_"):
                found.migrations.append(stem)
            elif folder == "cli" and ext == "py" and not stem.startswith("_") and stem != "post_setup":
                found.commands.append(stem)
        bot = self.repo / _in(offer.path, "bot/bot.toml")
        if bot.is_file():
            try:
                found.bots.append(str(tomllib.loads(bot.read_text(encoding="utf-8")).get("id", "bot")))
            except (UnicodeDecodeError, tomllib.TOMLDecodeError):
                found.bots.append("bot")
        found.hooks = sorted(set(found.hooks))
        return found

    def archive(self, path: str, into: Path) -> None:
        """Write the tree at `path` flat into `into`.

        `git archive <commit>:<path>` writes the path's contents at the top
        of the archive. A sparse checkout would keep them under `<path>/`,
        next to the repo's root files, where discovery cannot see them.
        """
        tar = self.repo.parent / "extension.tar"
        try:
            _git("archive", "--format=tar", f"--output={tar}",
                 f"HEAD:{'' if path == '.' else path}", cwd=self.repo)
        except FetchFailed as e:
            raise CopyFailed(str(e)) from None
        result = subprocess.run(["tar", "-x", "-f", str(tar), "-C", str(into)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            # tar names the file it could not write first, then sums up.
            raise CopyFailed(next(iter(result.stderr.strip().splitlines()), "tar failed"))


@contextmanager
def fetch(url: str) -> Generator[Fetched]:
    """The repo at `url` in a temporary directory, removed afterwards.

    Raises FetchFailed with git's reason when the repo cannot be fetched.
    """
    workdir = Path(tempfile.mkdtemp(prefix="stack-extension-"))
    try:
        yield Fetched(url, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def compose_services(text: str) -> list[str]:
    """The service names under a compose file's top-level `services:`.

    The host CLI has no YAML parser (stdlib only), and these names are
    for a person to read in a warning, so the keys one indent below
    `services:` are enough.
    """
    names, inside, indent = [], False, None
    key = re.compile(r"""\s*(?:"([^"]+)"|'([^']+)'|([^\s:#'"]+))\s*:\s*(?:#.*)?$""")
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        depth = len(line) - len(line.lstrip())
        if depth == 0:
            inside, indent = line.startswith("services:"), None
            continue
        if inside:
            indent = depth if indent is None else indent
            if depth == indent and (m := key.match(line)):
                names.append(next(g for g in m.groups() if g))
    return names


# ── Installing ───────────────────────────────────────────────────────────

def install(repo: Fetched, offer: Offer, into: Path) -> Path:
    """Copy `offer` flat into `into/<id>/` with its `.source.toml`; the folder.

    The copy is written to `INCOMING` first and renamed into place, so the
    folder either holds the whole extension or does not exist, and nothing
    finds it before then. Raises CopyFailed, and leaves nothing behind: no
    folder, no partial copy, and no extension dir when this call made it.
    """
    made = not into.exists()
    try:
        staging = _stage(repo, offer, into)
        _rename_or_drop(staging, into / offer.id)
    except BaseException:
        if made:
            shutil.rmtree(into, ignore_errors=True)
        raise
    finally:
        _drop_incoming(into)
    return into / offer.id


def _stage(repo: Fetched, offer: Offer, into: Path) -> Path:
    """Write `offer` into a new folder in `into/INCOMING`, with a
    `.source.toml` that records every file written; that folder.

    Raises CopyFailed for any failure, and then nothing of it is left.
    """
    staging = None
    try:
        (into / INCOMING).mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{offer.id}-", dir=into / INCOMING))
        # mkdtemp makes it 0700. Containers that mount the extension dir
        # may read it as another user.
        staging.chmod(0o755)
        repo.archive(offer.path, staging)
        source = repo.source(offer, record_files(staging))
        (staging / SOURCE_FILE).write_text(source.text(), encoding="utf-8")
        return staging
    except BaseException as e:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        _drop_incoming(into)
        raise _as_copy_failed(e) from None


def _as_copy_failed(e: BaseException) -> BaseException:
    """An OSError as CopyFailed with its message; anything else as it is."""
    return CopyFailed(str(e)) if isinstance(e, OSError) else e


def _rename_or_drop(staging: Path, target: Path) -> None:
    """Rename `staging` to `target`, or remove it and raise CopyFailed."""
    try:
        staging.rename(target)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise CopyFailed(str(e)) from None


def _drop_incoming(into: Path) -> None:
    """Remove `INCOMING` when it is empty. It is not empty while another
    copy is under way, and that one removes it."""
    with contextlib.suppress(OSError):
        (into / INCOMING).rmdir()


# ── Updating ─────────────────────────────────────────────────────────────

def edited_files(folder: Path, source: Source) -> list[str]:
    """The recorded files that are changed or missing in `folder`. A file
    that is not in the record is never an edit."""
    return [path for path, record in sorted(source.files.items())
            if file_record(folder / path) != record]


def kept_files(folder: Path, source: Source) -> list[str]:
    """What in `folder` the copy did not write: each file and symlink not
    in the record, and each directory with no recorded file below it, as
    a whole. `.source.toml` is rewritten, so it is not kept."""
    holds_record = {str(parent) for path in source.files
                    for parent in PurePosixPath(path).parents if str(parent) != "."}
    kept = []
    for root, dirs, files in os.walk(folder):
        here = Path(root)
        rel = {name: (here / name).relative_to(folder).as_posix() for name in [*dirs, *files]}
        whole = [d for d in dirs if (here / d).is_symlink() or rel[d] not in holds_record]
        dirs[:] = [d for d in dirs if d not in whole]
        kept += [rel[n] for n in [*whole, *files] if rel[n] not in source.files and rel[n] != SOURCE_FILE]
    return sorted(kept)


def replace(repo: Fetched, offer: Offer, folder: Path, installed: Source) -> Path:
    """Put `offer` in place of the installed extension in `folder`; the folder.

    The new version is staged in `INCOMING` of the extension dir that holds
    `folder`. Every kept file (`kept_files`) moves into it, never copied,
    so a `.env`, a local config or a virtualenv stays as it is. Then the
    folders are swapped with two renames, because macOS cannot rename a
    directory over one that is not empty, and the old folder is deleted
    only once the new one is in place.

    Raises CopyFailed when the new version has a file where a kept one
    is, or a step fails. Then the old folder is in place with its kept
    files, or, when even that fails, the message names where they are.
    """
    staging = _stage(repo, offer, folder.parent)
    kept = kept_files(folder, installed)
    try:
        if blocked := _collision(staging, kept):
            raise CopyFailed(f"the new version has a file where {blocked} is")
        _move_all(kept, folder, staging)
    except BaseException as e:
        shutil.rmtree(staging, ignore_errors=True)
        _drop_incoming(folder.parent)
        raise _as_copy_failed(e) from None
    _swap(folder, staging, kept)
    return folder


def _collision(staging: Path, kept: list[str]) -> str | None:
    """The first kept path the new version already uses, as a file or a
    directory, or under a file of its own; None when all fit."""
    for path in kept:
        target = staging / path
        parents = [staging / p for p in PurePosixPath(path).parents if str(p) != "."]
        if os.path.lexists(target) or any(p.is_symlink() or p.is_file() for p in parents):
            return path
    return None


def _move_all(paths: list[str], source: Path, target: Path) -> None:
    """Move each path from `source` to the same place under `target`. On a
    failure, move back what was moved, then raise the OSError."""
    moved = []
    try:
        for path in paths:
            (target / path).parent.mkdir(parents=True, exist_ok=True)
            (source / path).rename(target / path)
            moved.append(path)
    except OSError:
        for path in reversed(moved):
            with contextlib.suppress(OSError):
                (target / path).rename(source / path)
        raise


def _swap(folder: Path, staging: Path, kept: list[str]) -> None:
    """Rename `folder` into `INCOMING`, `staging` into its place, then
    delete the old folder. On a failure, put the old folder and the kept
    files back, and raise CopyFailed."""
    old = Path(tempfile.mkdtemp(prefix=f"{folder.name}-old-", dir=staging.parent))
    old.rmdir()
    try:
        folder.rename(old)
    except OSError as e:
        raise CopyFailed(_put_back(folder, None, staging, kept, e)) from None
    try:
        staging.rename(folder)
    except OSError as e:
        raise CopyFailed(_put_back(folder, old, staging, kept, e)) from None
    shutil.rmtree(old, ignore_errors=True)
    _drop_incoming(folder.parent)


def _put_back(folder: Path, old: Path | None, staging: Path, kept: list[str], error: OSError) -> str:
    """Undo a swap that failed: the old folder back into place (`old` is
    None when it never moved), the kept files back into it, the new copy
    gone. The message for the admin, with where things are if undoing
    failed too."""
    try:
        if old is not None:
            old.rename(folder)
        _move_all(kept, staging, folder)
    except OSError:
        where = f"the old folder is in {old}, " if old is not None and old.exists() else ""
        return f"{error}. Could not undo it: {where}the files you added are in {staging}"
    shutil.rmtree(staging, ignore_errors=True)
    _drop_incoming(folder.parent)
    return str(error)


# ── The catalog, as `list` last read it ──────────────────────────────────

def save_catalog(path: Path, repo: Fetched) -> None:
    """Keep what the catalog offered, for a `list` that cannot reach it."""
    entries = [{"id": o.id, "name": o.name, "stage": o.stage, "path": o.path, "tree": o.tree}
               for o in repo.offers if not o.problems()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": repo.url, "commit": repo.commit,
                                "fetched": date.today().isoformat(),
                                "extensions": entries}, indent=2) + "\n", encoding="utf-8")


def load_catalog(path: Path, url: str) -> dict | None:
    """The catalog as last saved, if it was this one."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if same_repo(saved.get("url", ""), url) else None
