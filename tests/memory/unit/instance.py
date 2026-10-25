"""Memory's test instance: a running memory, the one way a test reaches it.

Forgejo is a bare repository in a temporary directory, which is all Forgejo
is to git. A clone of it is the curator's; another is a person editing in
Forgejo's web editor. `curator_serving` serves the curator's HTTP API over
the curator's card store, `door_serving` is core's card door on this Mac,
and `running_memory` runs all of it, where a host command and a command in
a container find it:

    with running_memory(tmp_path, {LIST: CAMPING}) as memory:
        ...
        assert memory.text(LIST) == ...

Memory's own tests get it as the fixture `memory` (`conftest.py`); another
module's tests import `running_memory` from here.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from stack.stacklet_code import load
from tests.springfield import make_stack, write_instance
from tests.unit.surface.dsl import Commit, Memory, Person

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



def stop(server) -> None:
    """Stop `server` now, and close its socket.

    `shutdown` returns once the server's loop sees it, at its next poll,
    half a second away, which every test that stops an instance would wait
    for twice. A connection wakes the loop, which checks for the shutdown
    before it takes a request; it is repeated until the shutdown is seen,
    since one made just before it would be taken as a request instead.
    """
    stopping = threading.Thread(target=server.shutdown)
    stopping.start()
    while stopping.is_alive():
        with contextlib.suppress(OSError):
            socket.create_connection(server.server_address[:2], timeout=1).close()
        stopping.join(0.02)
    server.server_close()

@contextlib.contextmanager
def curator_serving(store, token: str, *,
                    on_write: Callable[[str], bool] | None = None,
                    sync: Callable | None = None) -> Iterator[str]:
    """The curator's HTTP API over `store`, as the curator serves it (the
    cards, the card editor, the card store's routes), on a free port; its
    address while the block runs. `sync` is what `POST /sync` runs, the
    store's own unless the curator's is given."""
    from stacklets.memory.card_edit import CardEditor
    from stacklets.memory.cards import StoreAPI
    from stacklets.memory.cards_api import CardsAPI, serve

    server = serve(CardsAPI(vault=store.repo), host="127.0.0.1", port=0,
                   editor=CardEditor(store=store, token=token),
                   store_api=StoreAPI(store, token=token, on_write=on_write, sync=sync))
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        stop(server)


@contextlib.contextmanager
def door_serving(curator: str) -> Iterator[int]:
    """Core's card door on this Mac: `/cards/api/<path>` passed on to the
    curator's API at `curator` by core's own rules (`cards_page`: which
    paths go through and which headers go with them). Its port."""
    cards_page = load(_REPO / "stacklets" / "core",
                      _REPO / "stacklets" / "core" / "tools-server" / "cards_page.py")

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
        stop(server)


class RunningMemory:
    """The memory as its commands reach it, and as a test reads it back.

    People act through it: `memory.person("marge")` runs `./stack` commands
    as Marge, on an instance whose memory this is (`stack`). What they see
    is read back from Forgejo, where every write lands: the commits since
    the test began (`new_commits`, `authors`, `committers`, `trailers`), a
    page (`page`, or `text` for one that may not be there). The states a
    test starts from are set through it too: `edited_in_forgejo`, and
    `forgejo_down()` for the time a block runs.

    `config` is what `./stack` hands a host command (the data dir, stack.toml's
    `[core] cards_port` at core's door, the card write token); `environ` is
    what a command in the curator's or the bot runner's container has in its
    environment (the curator's address, the token, the clone, the server
    name); `vault` is the curator's clone, where the host reads people and
    lists; `curator` the address of the curator's API.
    """

    def __init__(self, root: Path, forgejo: Forgejo, vault: Path, store, *,
                 server_name: str | None, mirrored: bool):
        self.root, self.forgejo, self.vault, self.store = root, forgejo, vault, store
        self.server_name = server_name
        # What the curator's mirror answers for each write: whether the wiki
        # and the agent's mount show it yet.
        self.mirrored = mirrored
        self.history = Memory(forgejo.repo)
        self.curator = ""
        self.config: dict = {}
        self.environ: dict = {}

    # ── People ──

    @functools.cached_property
    def stack(self):
        """The Stack `./stack` builds for this instance: its stack.toml names
        core's card door and the Matrix server, its secrets the card write
        token, as `stack up memory` leaves them."""
        port = self.config["stack"]["core"]["cards_port"]
        instance = write_instance(self.root, server_name=self.server_name, data_dir=self.root,
                                  core=f"cards_port = {port}\n",
                                  secrets={"memory__CARDS_WRITE_TOKEN": TOKEN})
        return make_stack(instance)

    def person(self, name: str) -> Person:
        """`name`, running `./stack` commands on this instance as themselves."""
        return Person(name, self.stack, self.root)

    # ── What Forgejo holds ──

    def text(self, path: str) -> str:
        """The file as Forgejo has it now, "" when it is not there."""
        return self.forgejo.file(path) or ""

    def page(self, path: str) -> str:
        """The page as Forgejo has it now; reading one that is not there fails the test."""
        return self.history.page(path)

    def new_commits(self) -> list[Commit]:
        """Every commit since the test began, oldest first, by its author."""
        return self.history.new_commits()

    def authors(self) -> list[str]:
        """Who each commit since the test began is by: `marge <marge@simpson>`."""
        return self.history.authors()

    def committers(self) -> list[str]:
        """The account that wrote each commit since the test began."""
        return self.history.committers()

    def trailers(self, key: str) -> list[str]:
        """The `key:` trailer of each commit since the test began, "" for none."""
        return self.history.trailers(key)

    # ── States a test starts from ──

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

    @contextlib.contextmanager
    def forgejo_down(self) -> Iterator[None]:
        """Forgejo does not answer while the block runs; it is back after."""
        self.forgejo.goes_down()
        try:
            yield
        finally:
            self.forgejo.comes_back()

    # ── What the bots wrote ──

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


# ── The bot runner ───────────────────────────────────────────────────────

# Where nothing answers: the default for a service a test stands no one in for.
NOWHERE = "http://127.0.0.1:9"
_BOT_RUNNER_DOCKER = Path(__file__).with_name("bot_runner.py")


@contextlib.contextmanager
def bot_runner_on_path(root: Path, environ: dict[str, str]) -> Iterator[None]:
    """`docker` answering for the bot runner (`bot_runner.py`), first on
    `PATH` while the block runs: an entry point `docker exec` would start in
    the bot runner starts on this Mac, in a process of its own, with
    `environ` as its whole environment."""
    bin_dir, workdir = root / "bot-runner" / "bin", root / "bot-runner" / "app"
    bin_dir.mkdir(parents=True)
    workdir.mkdir()
    settings = root / "bot-runner" / "settings.json"
    settings.write_text(json.dumps({"python": sys.executable, "repo": str(_REPO),
                                    "workdir": str(workdir), "environ": environ}),
                        encoding="utf-8")
    docker = bin_dir / "docker"
    docker.write_text(f"#!/bin/sh\nexec '{sys.executable}' -I '{_BOT_RUNNER_DOCKER}' "
                      f"'{settings}' \"$@\"\n", encoding="utf-8")
    docker.chmod(0o755)
    path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{path}"
    try:
        yield
    finally:
        os.environ["PATH"] = path


def _bot_runner_environ(root: Path, host: RunningMemory, *, model: str,
                        paperless: str) -> dict[str, str]:
    """What core renders into the bot runner's environment for the
    archivist and memory, every service a test does not stand in for at
    an address where nothing answers; `PYTHONPATH` is the framework, which
    core mounts at `/app`."""
    home = root / "bot-runner" / "home"
    home.mkdir(parents=True)
    return {**{k: v for k, v in host.environ.items() if v},
            "PATH": os.environ.get("PATH", os.defpath), "HOME": str(home), "LANG": "C.UTF-8",
            "PYTHONPATH": str(_REPO / "lib"),
            "OPENAI_URL": model, "OPENAI_KEY": "k", "AI_DEFAULT_MODEL": "stand-in",
            "LANGUAGE": "en", "SHARED_BUCKET": "family",
            "PAPERLESS_URL": paperless, "PAPERLESS_TOKEN": "t",
            "MATRIX_HOMESERVER": NOWHERE, "STACK_COMMIT": "9a5bdc9"}


# ── The model ────────────────────────────────────────────────────────────

class Model:
    """The model at its boundary: the chat completions API on a
    pytest-httpserver, as the bot runner's commands reach it at `url`.

    It answers the JSON objects a test gives (`answers`), in order, and the
    last one again after that, and keeps every request's messages
    (`requests`), the newest last.
    """

    def __init__(self, httpserver):
        from werkzeug.wrappers import Response

        self.url = httpserver.url_for("/v1")
        self.requests: list[list[dict]] = []
        self._answers: list[dict] = [{}]
        self._from = 0

        def complete(request):
            self.requests.append(json.loads(request.data)["messages"])
            answer = self._answers[min(len(self.requests) - self._from, len(self._answers)) - 1]
            return Response(json.dumps({
                "id": "c1", "object": "chat.completion", "created": 0, "model": "stand-in",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": json.dumps(answer)}}],
            }), content_type="application/json")

        httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_handler(complete)

    def answers(self, *answers: dict) -> None:
        """What the next requests are answered, in order."""
        self._answers, self._from = list(answers), len(self.requests)


# ── The instance ─────────────────────────────────────────────────────────

@contextlib.contextmanager
def running_memory(root: Path, files: dict[str, str], *, server_name: str | None = "simpson",
                   mirrored: bool = True, seeded_by: str = "homer",
                   seeded_as: str = "the Simpsons", model: str = NOWHERE,
                   paperless: str = NOWHERE) -> Iterator[RunningMemory]:
    """Forgejo holding `files`, committed by `seeded_by` as `seeded_as`, the
    curator's clone under `root` as the data dir has it (`<root>/memory/vault`),
    the curator's API over its card store, core's door to it on this Mac,
    and the bot runner.

    `server_name` is the Matrix server's name, None for an instance whose
    stack.toml names none; `mirrored` is what the curator's mirror answers
    for each write. `model` is the chat completions API the bot runner's
    commands ask (its `/v1` address), `paperless` Paperless's address."""
    from stacklets.memory.card_check import CardCheck
    from stacklets.memory.cards import CardStore

    forgejo = Forgejo(root)
    forgejo.seed(seeded_by, seeded_as, files)
    (root / "memory").mkdir(parents=True, exist_ok=True)
    vault = clone_of(forgejo, root / "memory" / "vault")
    store = CardStore(vault, remote="origin", server_name=server_name or "", check=CardCheck())
    host = RunningMemory(root, forgejo, vault, store, server_name=server_name, mirrored=mirrored)
    with curator_serving(store, TOKEN, on_write=lambda commit: host.mirrored) as curator, \
            door_serving(curator) as port:
        host.curator = curator
        host.config = {
            "data_dir": str(root),
            "stack": {"core": {"cards_port": port},
                      **({"messages": {"server_name": server_name}} if server_name else {})},
            "secrets": {"memory__CARDS_WRITE_TOKEN": TOKEN}}
        host.environ = {"MEMORY_API_URL": curator, "CARDS_WRITE_TOKEN": TOKEN,
                        "MEMORY_VAULT_DIR": str(vault), "MATRIX_SERVER_NAME": server_name or ""}
        with bot_runner_on_path(root, _bot_runner_environ(root, host, model=model,
                                                          paperless=paperless)):
            yield host
