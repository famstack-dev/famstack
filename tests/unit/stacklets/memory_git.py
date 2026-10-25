"""The family memory in git, for tests: Forgejo, the curator's clone, a person's clone.

Forgejo is a bare repository in a temporary directory, which is all Forgejo
is to git. A clone of it is the curator's; another is a person editing in
Forgejo's web editor. `curator_serving` serves the curator's HTTP API over
the curator's card store, `door_serving` is core's card door on this Mac,
and `running_memory` runs all of it, where a host command and a command in
a container find it.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

_REPO = Path(__file__).resolve().parents[3]
# The card write token the curator and the host share in these tests.
TOKEN = "cards-write-token"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def version_of(text: str) -> str:
    """What Forgejo calls a file's `sha`: the git blob id of its bytes."""
    return subprocess.run(["git", "hash-object", "--stdin"], input=text, check=True,
                          capture_output=True, text=True).stdout.strip()


class Forgejo:
    """The memory repository as Forgejo holds it: a bare repository."""

    def __init__(self, root: Path):
        self.repo = root / "forgejo" / "memory.git"
        self.repo.parent.mkdir(parents=True)
        git(self.repo.parent, "init", "-q", "--bare", "-b", "main", self.repo.name)
        self._start = ""

    def seed(self, by: str, subject: str, files: dict[str, str]) -> None:
        """The repository's first commit, by `by`; what a test then counts
        as new starts after it."""
        first = clone_of(self, self.repo.parent / "first")
        for path, text in files.items():
            (first / path).parent.mkdir(parents=True, exist_ok=True)
            (first / path).write_text(text, encoding="utf-8")
        git(first, "add", "-A")
        git(first, "-c", f"user.name={by}", "-c", f"user.email={by}@simpson", "commit", "-q",
            "-m", subject)
        git(first, "push", "-q", "origin", "HEAD:main")
        self.began()

    def began(self) -> None:
        self._start = git(self.repo, "rev-parse", "main")

    def new_commits(self) -> list[str]:
        """`author | committer | subject` of every commit since the test
        began, oldest first."""
        out = git(self.repo, "log", "--reverse", "--format=%an <%ae> | %cn <%ce> | %s",
                  f"{self._start}..main")
        return out.splitlines() if out else []

    def changed(self) -> list[str]:
        """The paths every commit since the test began touched, sorted."""
        out = git(self.repo, "diff", "--name-only", f"{self._start}..main")
        return sorted(out.splitlines()) if out else []

    def message(self, ref: str = "main") -> str:
        return git(self.repo, "log", "-1", "--format=%B", ref)

    def file(self, path: str) -> str | None:
        """The file as Forgejo holds it, its line endings as they are."""
        found = subprocess.run(["git", "-C", str(self.repo), "show", f"main:{path}"],
                               capture_output=True)
        return found.stdout.decode("utf-8") if found.returncode == 0 else None

    def goes_down(self) -> None:
        self.repo.rename(self.repo.with_name("memory.git.down"))

    def comes_back(self) -> None:
        self.repo.with_name("memory.git.down").rename(self.repo)


def clone_of(forgejo: Forgejo, at: Path) -> Path:
    git(at.parent, "clone", "-q", str(forgejo.repo), at.name)
    git(at, "config", "user.name", "nobody")
    git(at, "config", "user.email", "nobody@example")
    return at


def commit_as(clone: Path, who: str, subject: str, files: dict[str, str]) -> None:
    """One commit by `who` in a clone of their own, pushed to Forgejo."""
    git(clone, "pull", "-q", "--ff-only")
    for path, text in files.items():
        (clone / path).parent.mkdir(parents=True, exist_ok=True)
        (clone / path).write_text(text, encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "-c", f"user.name={who}", "-c", f"user.email={who}@simpson", "commit", "-q", "-m", subject)
    git(clone, "push", "-q", "origin", "HEAD:main")


@contextlib.contextmanager
def curator_serving(store, token: str, *,
                    on_write: Callable[[str], bool] | None = None,
                    sync: Callable | None = None) -> Iterator[str]:
    """The curator's HTTP API over `store`, as the curator serves it (the
    cards, the card editor, the card store's routes), on a free port; its
    address while the block runs. `sync` is what `POST /sync` runs, the
    store's own unless the curator's is given."""
    from memory.card_edit import CardEditor
    from memory.cards import StoreAPI
    from memory.cards_api import CardsAPI, serve

    server = serve(CardsAPI(vault=store.repo), host="127.0.0.1", port=0,
                   editor=CardEditor(store=store, token=token),
                   store_api=StoreAPI(store, token=token, on_write=on_write, sync=sync))
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@contextlib.contextmanager
def door_serving(curator: str) -> Iterator[int]:
    """Core's card door on this Mac: `/cards/api/<path>` passed on to the
    curator's API at `curator` by core's own rules (`cards_page`: which
    paths go through and which headers go with them). Its port."""
    sys.path.insert(0, str(_REPO / "stacklets" / "core" / "tools-server"))
    import cards_page

    class Door(BaseHTTPRequestHandler):
        def _pass_on(self) -> None:
            parts = urlsplit(self.path)
            path = parts.path.removeprefix("/cards/api/")
            url = (cards_page.upstream(path, dict(parse_qsl(parts.query)), curator)
                   if self.command == "GET" else
                   cards_page.write_upstream(self.command, path, curator))
            if url is None:
                return self._answer(404, {}, b'{"detail": "Not Found"}')
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
            passed = urllib.request.Request(url, data=body, method=self.command,
                                            headers=cards_page.forwarded(
                                                {k.lower(): v for k, v in self.headers.items()}))
            try:
                with urllib.request.urlopen(passed) as r:
                    self._answer(r.status, r.headers, r.read())
            except urllib.error.HTTPError as e:
                self._answer(e.code, e.headers, e.read())

        def _answer(self, status: int, headers, body: bytes) -> None:
            self.send_response(status)
            for name, value in headers.items():
                if name.lower() in ("content-type", "etag"):
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_PATCH = do_POST = _pass_on

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Door)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@dataclass
class RunningMemory:
    """The memory as its commands reach it, and as a test reads it back.

    `config` is what `./stack` hands a host command (the data dir, stack.toml's
    `[core] cards_port` at core's door, the card write token); `environ` is
    what a command in the curator's or the bot runner's container has in its
    environment (the curator's address, the token, the clone, the server
    name); `vault` is the curator's clone, where the host reads people and
    lists; `forgejo` is where every write lands.
    """

    forgejo: Forgejo
    vault: Path
    store: object
    config: dict = field(default_factory=dict)
    environ: dict = field(default_factory=dict)
    # What the curator's mirror answers for each write: whether the wiki
    # and the agent's mount show it yet.
    mirrored: bool = True

    def text(self, path: str) -> str:
        """The file as Forgejo has it now, "" when it is not there."""
        return self.forgejo.file(path) or ""

    def edited_in_forgejo(self, files: dict[str, str], *, removed: tuple[str, ...] = (),
                          by: str = "marge", subject: str = "an edit in Forgejo") -> None:
        """`files` written and `removed` taken out by `by` in Forgejo's web
        editor, then pulled by the curator, as Forgejo's push notice has it do."""
        person = self.forgejo.repo.parent / f"{by}-in-forgejo"
        if not person.exists():
            clone_of(self.forgejo, person)
        for path in removed:
            git(person, "pull", "-q", "--ff-only")
            git(person, "rm", "-q", "--", path)
        commit_as(person, by, subject, files)
        self.store.sync()

    @property
    def written(self) -> list[list[str]]:
        """The paths of each commit a bot wrote since the test began, oldest
        first, each commit's sorted as git lists them: what reached the
        memory through the curator, without what people edited in Forgejo."""
        out = git(self.forgejo.repo, "log", "--reverse", "--no-renames", "--format=%x01%cn",
                  "--name-only", f"{self.forgejo._start}..main")
        found = []
        for record in out.split("\x01")[1:]:
            committer, *paths = record.split()
            if committer.endswith("-bot"):
                found.append(paths)
        return found

    @property
    def commits(self) -> list[tuple[str, str, str]]:
        """(path, author, message) of each commit since the test began,
        oldest first; the path is the first file it changed."""
        # \x01 and \x02 mark records and fields: `git` strips its output, and
        # Python counts \x1c to \x1f as whitespace.
        out = git(self.forgejo.repo, "log", "--reverse", "--format=%x01%an%x02%B%x02",
                  "--name-only", f"{self.forgejo._start}..main")
        found = []
        for record in out.split("\x01")[1:]:
            author, message, files = record.split("\x02")
            found.append((files.split()[0] if files.split() else "", author, message.strip()))
        return found


@contextlib.contextmanager
def running_memory(root: Path, files: dict[str, str], *, server_name: str = "simpson",
                       mirrored: bool = True, seeded_by: str = "homer",
                       seeded_as: str = "the Simpsons") -> Iterator[RunningMemory]:
    """Forgejo holding `files`, committed by `seeded_by` as `seeded_as`, the
    curator's clone under `root` as the data dir has it (`<root>/memory/vault`),
    the curator's API over its card store, and core's door to it on this Mac.
    `mirrored` is what the curator's mirror answers for each write.

    While it runs, the path is the curator's (`stacklets/memory/bot/curator.py`):
    `memory.*` from `stacklets/`, and the modules the card check imports by
    their bare names (`card_schema`, `card_fields`) from `stacklets/memory`.
    Without them the check loads only after another test left that
    directory on the path."""
    path = list(sys.path)
    sys.path[:0] = [str(_REPO / "stacklets" / "memory"), str(_REPO / "stacklets")]
    try:
        from memory.card_check import CardCheck
        from memory.cards import CardStore
    finally:
        sys.path[:] = path

    forgejo = Forgejo(root)
    forgejo.seed(seeded_by, seeded_as, files)
    (root / "memory").mkdir(parents=True, exist_ok=True)
    vault = clone_of(forgejo, root / "memory" / "vault")
    store = CardStore(vault, remote="origin", server_name=server_name, check=CardCheck())
    host = RunningMemory(forgejo, vault, store, mirrored=mirrored)
    with curator_serving(store, TOKEN, on_write=lambda commit: host.mirrored) as curator, \
            door_serving(curator) as port:
        host.config = {
            "data_dir": str(root),
            "stack": {"core": {"cards_port": port}, "messages": {"server_name": server_name}},
            "secrets": {"memory__CARDS_WRITE_TOKEN": TOKEN}}
        host.environ = {"MEMORY_API_URL": curator, "CARDS_WRITE_TOKEN": TOKEN,
                        "MEMORY_VAULT_DIR": str(vault), "MATRIX_SERVER_NAME": server_name}
        yield host

