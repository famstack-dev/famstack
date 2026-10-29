"""The card store's types and its one class, `CardStore` (see the package).

A write runs in five steps, each a method below, in this order:

    catch up with Forgejo  →  check every base  →  write the files
    →  commit them as one  →  push

and holds the store's lock throughout, so the curator's cycle and its HTTP
threads never run git in the clone at the same time. Nothing here starts a
process itself: git runs through `_git.Git`.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Sequence
from typing import Protocol
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from stack.cards.commits import (MEMORY_BOT, SUBJECTS, NoServerName,
                                 commit_author, commit_subject)
from stack.cards.history import LOG_FORMAT, Commit, Version, card_versions, parse_blobs, parse_log
from stack.cards.lookup import find_by_card_id
from stack.cards.tree import remove_file

from ._git import NETWORK_TIMEOUT_S, PUSH_REFUSED, Git, GitFailed, Ran

BRANCH = "main"
# The trailer a migration's commit carries (`stack.migrations`); its cards
# may be between formats, so the check leaves the schemas out for it.
MIGRATION = "Migration"
# What git says when the remote has no such branch: a memory with no commit yet.
_NO_BRANCH = "couldn't find remote ref"


# ── What goes in and what comes out ──────────────────────────────────────

@dataclass(frozen=True)
class File:
    """A file as the last commit left it. `version` is its git blob id;
    `card_id` the id it carries, when it was found as a card."""

    path: str
    text: str
    version: str
    card_id: str = ""


@dataclass(frozen=True)
class Put:
    """Write `text` at `path`, made from the file at `base` ("" for none)."""

    path: str
    text: str
    base: str


@dataclass(frozen=True)
class Remove:
    """Remove the file at `path`, read at `base`."""

    path: str
    base: str


Change = Put | Remove


@dataclass(frozen=True)
class Written:
    """What a write did. `sha` is its commit, "" when nothing changed;
    `pending` is True while Forgejo does not have it yet."""

    sha: str
    pending: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.sha)


@dataclass(frozen=True)
class Pending:
    """A commit in the clone that Forgejo does not have yet."""

    sha: str
    subject: str
    at: str


@dataclass(frozen=True)
class Sync:
    """How the clone stands against Forgejo after the curator's cycle.

    `state` is one of:

        "ok"           nothing waits, or what waited is pushed now
        "unreachable"  Forgejo did not answer; the next cycle tries again
        "refused"      Forgejo answered and did not take the push
        "diverged"     the clone holds commits that cannot be joined to
                       Forgejo's history without a person deciding, so
                       nothing is pushed until then
        "missing"      there is no clone yet
        "failed"       git failed in the clone; `detail` says how

    `pending` are the commits Forgejo does not have, oldest first,
    `detail` is what git or Forgejo said, for a person to read, and `head`
    the commit the clone stands at after the cycle ("" with no clone or no
    commit): the newest the memory is, since every write goes through it.
    """

    state: str
    pending: tuple[Pending, ...] = ()
    detail: str = ""
    head: str = ""


# ── Errors, each one a sentence for a person ─────────────────────────────

class StoreError(Exception):
    """A write that did not happen. The text says why, for a person."""


class CardChanged(StoreError):
    """A file is no longer the version the write was made from.

    Nothing was written. `paths` are the files that moved on, in the order
    the write named them.
    """

    def __init__(self, paths: tuple[str, ...]):
        self.paths = paths
        super().__init__(f"{', '.join(paths)} changed since it was read: read it again "
                         "and make the change on top of what is there now")


class Refused(StoreError):
    """The write cannot be made as asked; nothing was tried."""


class CardInvalid(Refused):
    """A file the write puts is no valid card, or would make one card another.

    Nothing was written. `errors` names each file and field, as
    `card_check.CardCheck` found them: `{"path", "field", "message"}`.
    """

    def __init__(self, errors: Sequence[dict]):
        self.errors = tuple(errors)
        named = "; ".join(f"{e['path']}: {e['field']}: {e['message']}" for e in self.errors[:3])
        more = f" (and {len(self.errors) - 3} more)" if len(self.errors) > 3 else ""
        super().__init__(f"nothing was written, the change would leave a card that is not "
                         f"valid: {named}{more}")


class Check(Protocol):
    """What the store asks before it writes: the files put, `(path, text)`,
    a reader of the memory as it stands, the clone, and the paths the write
    removes; the problems, or none. `formats` False is a migration's commit
    (`card_check.CardCheck`). It runs under the store's lock, after the
    clone caught up with Forgejo, so it judges the memory the write lands on."""

    def __call__(self, puts: Sequence[tuple[str, str]], read: Callable[[str], "str | None"],
                 repo: Path, *, formats: bool, removes: Sequence[str] = ()) -> list[dict]: ...


class CloneMissing(StoreError):
    """There is no clone of the memory to write to yet."""

    def __init__(self, repo: Path):
        super().__init__(f"the memory's clone is not there yet ({repo}): `stack up memory` "
                         "makes it, and the curator writes once it is there")


# ── The store ────────────────────────────────────────────────────────────

def _in_the_clone(doing: str) -> Callable:
    """Git failing while the store is `doing` something becomes a
    `StoreError` that says so in git's words, at the store's edge."""
    def wrap(method: Callable) -> Callable:
        @functools.wraps(method)
        def run(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except GitFailed as e:
                raise StoreError(f"could not {doing} in the memory's clone: {e}") from e
        return run
    return wrap


class CardStore:
    """The one writer of the memory's clone at `repo`, pushing to `remote`.

    `server_name` is the Matrix server's name, which a person's commit
    address is made from (`commit_author`). `git_env` is added to git's
    environment: the curator's names a global config that trusts its
    bind-mounted clone.
    """

    def __init__(self, repo: Path, *, remote: str, server_name: str,
                 git_env: dict[str, str] | None = None, check: Check | None = None):
        self.repo = repo
        self.remote = remote
        self.server_name = server_name
        # What a write's files are checked against before anything is
        # committed (`card_check.CardCheck`, the curator's); None checks none.
        self.check = check
        self._git = Git(repo, env=git_env)
        self._lock = threading.RLock()
        self._said = ""          # what git last said about Forgejo, for `sync`
        self.last_sync: Sync | None = None   # how the curator's last cycle went

    # ── Reading ──

    @_in_the_clone("read a file")
    def read(self, path: str) -> File | None:
        """`path` as the last commit left it, None when it is not there."""
        path = _checked(path)
        self._require_clone()
        text = self._git.text(path)
        return None if text is None else File(path, text, self._git.blob(text))

    @_in_the_clone("look at the history")
    def is_new(self) -> bool:
        """Whether the memory has no commit yet, as a new instance's has none."""
        self._require_clone()
        return not self._git.head()

    @_in_the_clone("read a directory")
    def files(self, under: str = "") -> list[File]:
        """Every file under the directory `under`, at any depth, or every file
        of the memory with no `under`, as the last commit left it, by path;
        none when there is no such directory."""
        under = _directory(under) if under else ""
        self._require_clone()
        listed = sorted(self._git.listed(under))
        if not listed:
            return []
        out = self._git.raw("cat-file", "--batch",
                            input="".join(f"{blob}\n" for _, blob in listed).encode())
        return [File(path, text, blob)
                for (path, blob), text in zip(listed, parse_blobs(out, len(listed)))]

    @_in_the_clone("list the files")
    def paths(self, under: str = "") -> list[str]:
        """The path of every file under the directory `under`, at any depth,
        or of every file of the memory with no `under`, as the last commit
        left them, sorted."""
        under = _directory(under) if under else ""
        self._require_clone()
        return sorted(path for path, _ in self._git.listed(under))

    @_in_the_clone("find a card")
    def card(self, card_id: str) -> File | None:
        """The card that carries `card_id`, now or as a former id, as the
        last commit left it; None when no card does."""
        self._require_clone()
        found = find_by_card_id(card_id, root=self.repo)
        current = self.read(found.path) if found else None
        return File(current.path, current.text, current.version, found.card_id) \
            if found and current else None

    @_in_the_clone("compare two commits")
    def includes(self, commit: str, *, later: str) -> bool:
        """Whether `later` is `commit` or builds on it; False when the clone
        has either of them not."""
        self._require_clone()
        if not (commit and later and self._git.head(commit) and self._git.head(later)):
            return False
        return self._git.is_ancestor(commit, later)

    @_in_the_clone("read a card's history")
    def history(self, path: str, card_id: str) -> list[Version]:
        """Every version of the card `card_id`, now at `path`, oldest first,
        followed back across its moves (`stack.cards.history.card_versions`)."""
        path = _checked(path)
        self._require_clone()
        return card_versions(path, card_id=card_id, commits_of=self._commits_of,
                             read=self._files_at, commit_at=self._commit_at)

    def _commits_of(self, path: str, since: str) -> list[Commit]:
        return self._log(since or "HEAD", "--", path)

    def _commit_at(self, sha: str) -> Commit | None:
        return next(iter(self._log("-1", "--no-renames", "--name-status", sha)), None)

    def _log(self, *args: str) -> list[Commit]:
        listed = self._git("-c", "core.quotePath=false", "log", f"--format={LOG_FORMAT}", *args)
        if not listed.ok and "does not have any commits" not in listed.err:
            raise GitFailed(listed.complaint)
        return parse_log(listed.out)

    def _files_at(self, specs: Sequence[tuple[str, str]]) -> list[str]:
        if not specs:
            return []
        out = self._git.raw("cat-file", "--batch",
                            input="".join(f"{sha}:{path}\n" for sha, path in specs).encode())
        return parse_blobs(out, len(specs))

    # ── Writing ──

    @_in_the_clone("write the change")
    def commit(self, changes: Sequence[Change], *, by: str, kind: str, title: str,
               body: str = "", trailers: dict[str, str] | None = None,
               via: str = MEMORY_BOT) -> Written:
        """Write `changes` as one commit by `by`, through the bot `via`, and push it."""
        message = _message(kind, title, body, trailers or {})
        author, committer = self._author(by), _committer(via)
        changes = _checked_changes(changes)
        with self._lock:
            self._require_clone()
            state = self._catch_up()
            if moved := tuple(c.path for c in changes if self._git.version(c.path) != c.base):
                raise CardChanged(moved)
            if edited := tuple(c.path for c in changes if self._uncommitted(c.path)):
                raise Refused(f"{', '.join(edited)} has changes in the curator's clone that were "
                              "never committed, and nothing was written: make the change in "
                              "Forgejo instead, or put the file back as it was")
            todo = [c for c in changes if self._changes_anything(c)]
            if not todo:
                return Written("")
            if self.check and (errors := self.check(
                    [(c.path, c.text) for c in todo if isinstance(c, Put)],
                    lambda path: self._git.text(path), self.repo,
                    formats=MIGRATION not in (trailers or {}),
                    removes=[c.path for c in todo if isinstance(c, Remove)])):
                raise CardInvalid(errors)
            sha = self._commit_files(todo, message, author, committer)
            if state == "diverged":
                return Written(sha, pending=True)      # the cycle reports it; nothing is forced
            delivered = self._deliver(todo)
            return Written(self._git.head(), pending=not delivered)   # joined: a new id

    def _author(self, by: str) -> tuple[str, str]:
        try:
            return commit_author(by, self.server_name)
        except NoServerName as e:
            raise Refused(str(e)) from e
        except ValueError as e:
            raise Refused(str(e)) from e

    def _require_clone(self) -> None:
        if not (self.repo / ".git").exists():
            raise CloneMissing(self.repo)

    def _uncommitted(self, path: str) -> bool:
        """Whether the file at `path` in the clone is not what the last
        commit has there: edited, removed or added by hand."""
        on_disk, committed = self.repo / path, self._git.version(path)
        if not on_disk.is_file():
            return bool(committed) or on_disk.exists()
        try:
            return self._git.blob(on_disk.read_bytes().decode("utf-8")) != committed
        except (OSError, UnicodeDecodeError):
            return True

    def _changes_anything(self, change: Change) -> bool:
        if isinstance(change, Remove):
            return bool(change.base)
        return self._git.blob(change.text) != change.base

    # ── Keeping up with Forgejo ──

    def sync(self) -> Sync:
        """The curator's cycle: pull what Forgejo has, push what waits.
        The answer is kept as `last_sync`."""
        with self._lock:
            self.last_sync = self._sync()
            return self.last_sync

    def _sync(self) -> Sync:
        try:
            return self._sync_once()
        except (GitFailed, StoreError) as e:
            return Sync("failed", detail=str(e))

    def _sync_once(self) -> Sync:
        with self._lock:
            if not (self.repo / ".git").exists():
                return Sync("missing", detail=str(CloneMissing(self.repo)))
            state = self._catch_up()
            if state != "ok":
                return Sync(state, self.pending(), self._said, head=self._git.head())
            if self.pending():
                pushed = self._push()
                if not pushed.ok:
                    return Sync("refused", self.pending(), pushed.complaint, head=self._git.head())
            return Sync("ok", head=self._git.head())

    @_in_the_clone("list what waits for Forgejo")
    def pending(self) -> tuple[Pending, ...]:
        """The commits in the clone that Forgejo does not have, oldest first,
        as far as the last pull or push knows."""
        if not self._git.head():
            return ()
        tracking = f"refs/remotes/{self.remote}/{BRANCH}"
        # Before the first push Forgejo has nothing of the clone's.
        since = f"{tracking}..HEAD" if self._git.head(tracking) else "HEAD"
        out = self._git("log", "--reverse", "--format=%H%x00%s%x00%cI", since).out
        return tuple(Pending(*line.split("\0")) for line in out.splitlines() if line)

    # ── The commit, and putting things back when it fails ──

    def _commit_files(self, todo: list[Change], message: str, author: tuple[str, str],
                      committer: tuple[str, str]) -> str:
        """Write the files, commit them as one, and return the commit.

        On any failure the paths are put back as the last commit left them
        and the error says what git said.
        """
        written = _Written(self.repo)
        try:
            for change in todo:
                written.apply(change)
            paths = [c.path for c in todo]
            self._must(self._git("add", "--all", "--", *paths), "stage the change")
            self._must(self._git("commit", "--quiet", "--no-verify", "-F", "-", "--", *paths,
                                 input=message, env=_identity(author, committer)),
                       "commit the change")
        except BaseException:
            self._git("reset", "--quiet", "HEAD", "--", *written.paths)
            written.undo(self._git)
            raise
        return self._git.head()

    def _must(self, ran: Ran, what: str) -> None:
        if ran.ok:
            return
        if "index.lock" in ran.err:
            raise StoreError(f"could not {what}: another git process is working in the memory's "
                             f"clone ({self.repo / '.git' / 'index.lock'}). Try again in a moment, "
                             "or remove that file if no git process is running")
        raise StoreError(f"could not {what} in the memory's clone: {ran.complaint}")

    # ── Forgejo ──

    def _catch_up(self) -> str:
        """Bring the clone up to Forgejo; the state it is in afterwards.

        Behind: fast-forward. Ahead: nothing, the next push carries it.
        Both moved: replay the clone's own commits on top of Forgejo's,
        each keeping its author and committer. A replay that conflicts is
        put back as it was and leaves the clone diverged.
        """
        fetched = self._git("fetch", "--quiet", self.remote, BRANCH, timeout=NETWORK_TIMEOUT_S)
        self._said = fetched.complaint
        if not fetched.ok and _NO_BRANCH in fetched.err:
            return "ok"        # Forgejo answered: a new memory, nothing there yet to catch up with
        if not fetched.ok:
            return "unreachable"
        theirs, ours = self._git.head("FETCH_HEAD"), self._git.head()
        if theirs == ours or self._git.is_ancestor(theirs, ours):
            return "ok"
        if self._git.is_ancestor(ours, theirs):
            self._must(self._git("merge", "--quiet", "--ff-only", theirs), "pull Forgejo's changes")
            return "ok"
        return "ok" if self._replay(onto=theirs) else "diverged"

    def _replay(self, *, onto: str) -> bool:
        """The clone's own commits, replayed on top of `onto`; False, with
        the clone as it was, when one of them conflicts or the histories
        share nothing."""
        before = self._git.head()
        if not self._git("merge-base", before, onto).ok:
            self._said = "the clone's history and Forgejo's share no commit"
            return False
        ours = self._git("rev-list", "--reverse", f"{onto}..{before}").out.split()
        self._must(self._git("reset", "--quiet", "--keep", onto), "set the clone onto Forgejo's history")
        for sha in ours:
            if not self._git("cherry-pick", "--allow-empty", sha, env=self._committer_of(sha)).ok:
                self._said = (f"{self._git('log', '-1', '--format=%s', sha).out.strip()!r} and a change "
                              "in Forgejo touch the same lines")
                self._git("cherry-pick", "--abort")
                # Unchecked, a clone left half replayed would be pushed as it is.
                self._must(self._git("reset", "--quiet", "--keep", before),
                           "put the clone back as it was before the replay")
                return False
        return True

    def _committer_of(self, sha: str) -> dict[str, str]:
        name, email = self._git("log", "-1", "--format=%cn%x00%ce", sha).out.strip().split("\0")
        return {"GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}

    def _deliver(self, todo: list[Change]) -> bool:
        """Push the clone to Forgejo; True when Forgejo has it.

        A refused push catches up once and tries again: Forgejo moved on
        since this write fetched. When the write itself cannot be joined to
        what Forgejo has now, it is taken back and `CardChanged` names its
        files; any other failure, Forgejo not answering included, leaves it
        pending.
        """
        pushed = self._push()
        if pushed.ok:
            return True
        if pushed.code != PUSH_REFUSED:
            return False
        sha = self._git.head()
        state = self._catch_up()
        if state == "diverged" and self._git.head() == sha:
            self._must(self._git("reset", "--quiet", "--keep", f"{sha}~1"), "take the write back")
            raise CardChanged(tuple(c.path for c in todo))
        return state == "ok" and self._push().ok

    def _push(self) -> Ran:
        return self._git("push", "--quiet", self.remote, f"HEAD:{BRANCH}", timeout=NETWORK_TIMEOUT_S)


# ── Checking a write before anything is tried ────────────────────────────

def _message(kind: str, title: str, body: str, trailers: dict[str, str]) -> str:
    if kind not in SUBJECTS:
        raise Refused(f"{kind!r} is no kind of change the memory's history knows "
                      f"(one of: {', '.join(sorted(SUBJECTS))})")
    if not title.strip():
        raise Refused("a change needs a title: what it is about, in a few words")
    parts = [commit_subject(kind, " ".join(title.split()))]
    if body.strip():
        parts.append(body.strip())
    if trailers:
        parts.append("\n".join(f"{key}: {value}" for key, value in trailers.items()))
    return "\n\n".join(parts) + "\n"


def _committer(via: str) -> tuple[str, str]:
    bot = (via or "").strip().lower()
    if not bot.endswith("-bot"):
        raise Refused(f"{via!r} cannot commit to the memory: changes are written by a bot "
                      "account (`<name>-bot`) on a person's behalf")
    return bot, f"{bot}@local"


def _identity(author: tuple[str, str], committer: tuple[str, str]) -> dict[str, str]:
    return {"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": committer[0], "GIT_COMMITTER_EMAIL": committer[1]}


def _checked(path: str) -> str:
    """`path` as a file of the memory, or `Refused`."""
    parts = PurePosixPath(path or "").parts
    if not parts or path.startswith("/") or path.endswith("/") or {"..", ".git"} & set(parts):
        raise Refused(f"{path!r} is not a file of the memory: a path inside it, such as "
                      "family/lists/camping.md")
    return PurePosixPath(*parts).as_posix()


def _directory(path: str) -> str:
    """`path` as a directory of the memory, or `Refused`."""
    parts = PurePosixPath(path or "").parts
    if not parts or path.startswith("/") or {"..", ".git"} & set(parts):
        raise Refused(f"{path!r} is not a directory of the memory: a path inside it, such as "
                      "family/lists")
    return PurePosixPath(*parts).as_posix()


def _checked_changes(changes: Sequence[Change]) -> list[Change]:
    if not changes:
        raise Refused("a write needs at least one file to change")
    checked: list[Change] = []
    for change in changes:
        if isinstance(change, Put):
            checked.append(Put(_checked(change.path), change.text, change.base))
        elif isinstance(change, Remove):
            checked.append(Remove(_checked(change.path), change.base))
        else:
            raise Refused(f"{change!r} is no change the store makes: a `Put` or a `Remove`")
    paths = [c.path for c in checked]
    if len(set(paths)) != len(paths):
        raise Refused(f"a write names {sorted({p for p in paths if paths.count(p) > 1})[0]} twice")
    return checked


# ── The files a write touched, and how to put them back ──────────────────

@dataclass
class _Written:
    repo: Path
    paths: list[str] = field(default_factory=list)
    made_dirs: list[Path] = field(default_factory=list)

    def apply(self, change: Change) -> None:
        target = self.repo / change.path
        self.paths.append(change.path)
        if isinstance(change, Remove):
            # With the folders it leaves empty: a folder the clone keeps
            # after git dropped it reads as there to every reader of it.
            remove_file(self.repo, change.path)
            return
        for parent in reversed(target.relative_to(self.repo).parents[:-1]):
            if not (self.repo / parent).exists():
                (self.repo / parent).mkdir()
                self.made_dirs.append(self.repo / parent)
        target.write_bytes(change.text.encode("utf-8"))

    def undo(self, git: Git) -> None:
        """Every touched path as the last commit has it."""
        for path in self.paths:
            text = git.text(path)
            if text is None:
                (self.repo / path).unlink(missing_ok=True)
            else:
                (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
                (self.repo / path).write_bytes(text.encode("utf-8"))
        for made in reversed(self.made_dirs):
            if made.exists() and not any(made.iterdir()):
                made.rmdir()
