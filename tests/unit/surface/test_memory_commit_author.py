"""Who a commit to the memory repo is by, whichever way the change got there.

A person commits as `<person id>@<Matrix server name>`: Marge, on an
instance whose Matrix server is `simpson`, is `marge <marge@simpson>`.
The address mirrors her Matrix id, so `git log --author` and Forgejo
group her commits, and no real address goes into a repository that is
meant to outlive the stack. A bot keeps its Forgejo account's address.

Three ways a person's change reaches the memory, each through its own
door, and each must name her the same way:

- the archivist files what she sent to a room (its mirror, in the bot
  runner, which knows the server name from its environment);
- `stack memory card edit`, which the memory stacklet's card endpoint in
  the curator commits (the curator's environment);
- `stack memory reprocess --msg ... --by`, which the host commits (stack.toml).

What is real: the archivist's mirror, the card endpoint as the curator
builds it, core's card door, the `./stack` commands through their loader.
What stands in: Forgejo (`test_memory_card_links`) over a real git
repository; the curator's pull of its clone after a commit; the bot
runner's `docker exec` for the model half of a refile, which works the
correction in without a model. The data is the Simpsons'.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.wrappers import Response

from stack.forgejo import ForgejoClient

from .conftest import REPO, STACKLETS, _ours, make_stack, write_instance
from .dsl import Memory, Person, commit
from .test_memory_card_edit import TOKEN, _stop, core_door  # noqa: F401

sys.path.insert(0, str(REPO / "tests" / "unit" / "stacklets"))
from simpsons_cards import ONTOLOGY, VAULT  # noqa: E402
from test_memory_card_links import _git, contents, forgejo  # noqa: E402, F401

TREE = "/api/v1/repos/family/memory/git/trees/main"
# A note of Marge's that was filed before the test began.
ART_SUPPLIES = "note-7f3a9c21e4"


# ── The memory repo, and the curator's clone of it ───────────────────────

@pytest.fixture
def memory(tmp_path) -> Memory:
    """family/memory on Forgejo, holding the Simpsons' cards as a migration left them."""
    repo = tmp_path / "forgejo" / "memory"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    for path, text in {**VAULT, "ontology.toml": ONTOLOGY}.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--author=memory-bot <memory-bot@local>", "-m", "cards: the Simpsons")
    _git(repo, "update-server-info")
    return Memory(repo)


@pytest.fixture
def curator(memory, tmp_path, monkeypatch) -> Path:
    """The memory's clone on this Mac, as the curator keeps it: it follows
    every commit Forgejo takes, and the commit counts as carried on to the
    trees that are read. Patched at the class, so every writer's client,
    the host's, the archivist's and the endpoint's, reaches it."""
    data = tmp_path / "data"
    vault, state = data / "memory" / "vault", data / "memory" / "curator"
    vault.parent.mkdir(parents=True)
    _git(vault.parent, "clone", "-q", str(memory.repo), str(vault))

    def following(write):
        def wrapped(self, *args, **kwargs):
            answer = write(self, *args, **kwargs)
            _git(vault, "pull", "-q", "--ff-only")
            state.mkdir(parents=True, exist_ok=True)
            (state / "last-mirrored-sha").write_text(_git(vault, "rev-parse", "HEAD"))
            return answer
        return wrapped

    for name in ("change_files", "put_file"):
        monkeypatch.setattr(ForgejoClient, name, following(getattr(ForgejoClient, name)))
    return vault


# ── The server name, where each door reads it ────────────────────────────

@pytest.fixture
def server_name() -> str | None:
    """The instance's Matrix server name; a test overrides it with
    `@pytest.mark.parametrize("server_name", [None])`."""
    return "simpson"


@pytest.fixture
def containers(server_name, monkeypatch) -> None:
    """What `stack up` renders into the bot runner's and the curator's
    environment from stack.toml's `[messages] server_name`."""
    if server_name:
        monkeypatch.setenv("MATRIX_SERVER_NAME", server_name)
    else:
        monkeypatch.delenv("MATRIX_SERVER_NAME", raising=False)


@pytest.fixture
def endpoint(containers, curator, httpserver, forgejo, contents, monkeypatch, tmp_path):  # noqa: F811
    """The card write endpoint, built as the curator builds it from its
    environment, and served as the curator serves it."""
    for key, value in {"CODE_URL": httpserver.url_for("").rstrip("/"),
                       "MEMORY_BOT_TOKEN": "bottoken", "CARDS_WRITE_TOKEN": TOKEN}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.syspath_prepend(str(STACKLETS / "memory" / "bot"))
    import curator as the_curator
    from memory.cards_api import CardsAPI, serve

    editor = the_curator._card_editor(curator, "family", "en", curator.parent / "curator")
    server = serve(CardsAPI(vault=curator), host="127.0.0.1", port=0, editor=editor)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    _stop(server)


# ── The archivist ────────────────────────────────────────────────────────

class Archivist:
    """The archivist filing a note someone sent to a room, through its mirror."""

    def __init__(self, mirror, source):
        self.mirror, self.source = mirror, source

    def files_a_note(self, sender: str, words: str):
        """What the mirror filed (`Filed`), or None when it filed nothing."""
        person = sender.split(":")[0].lstrip("@")
        source = self.source("original", person, "2026-10-20T08:00:00+00:00", "chat", "text",
                             "$tent:simpson", quote=words)
        with _in_the_bot_runner():
            return asyncio.run(self.mirror.publish_capture(
                entity=person, kind="note", source_uri=None, title_hint=None, body_text=words,
                classification={"title": "Tent for the camping trip", "summary": ""},
                captured_at="2026-10-20", model="stand-in", capture_id=source.id,
                submitter=sender, clean="Bring the big tent.", sources=[source],
                made={"at": "2026-10-20T08:00:05+00:00", "model": "stand-in"}))


@contextlib.contextmanager
def _in_the_bot_runner():
    """`sys.path` as the bot runner has it for the archivist, for one call.

    The test body runs in `./stack`'s interpreter (`as_stack_runs_it`);
    what the archivist imports on the way is forgotten after, so the
    commands that follow still run as `./stack` runs them.
    """
    before, path = set(sys.modules), list(sys.path)
    sys.path[:0] = [str(STACKLETS / "docs" / "bot"), str(STACKLETS)]
    try:
        yield
    finally:
        sys.path[:] = path
        for name in set(sys.modules) - before:
            if _ours(sys.modules[name]):
                del sys.modules[name]


@pytest.fixture
def archivist(containers, curator, memory, httpserver, forgejo, contents, monkeypatch, tmp_path):  # noqa: F811
    """The archivist's mirror, set up with its Forgejo token as the bot
    runner has it after its first start."""
    monkeypatch.syspath_prepend(str(STACKLETS))
    monkeypatch.syspath_prepend(str(STACKLETS / "docs" / "bot"))
    from git_mirror import GitMirror
    from memory.note_card import Source

    def tree(request):
        files = _git(memory.repo, "ls-files").splitlines()
        return Response(json.dumps({"tree": [{"path": f, "type": "blob"} for f in files]}),
                        content_type="application/json")

    httpserver.expect_request(TREE, method="GET").respond_with_handler(tree)
    mirror = GitMirror(code_url=httpserver.url_for("").rstrip("/"), admin_user="stackadmin",
                       admin_password="pw", admin_usernames=[], data_dir=tmp_path / "archivist")
    mirror._setup_done = True
    mirror._creds = SimpleNamespace(token="bottoken")
    return Archivist(mirror, Source)


# ── The bot runner, for the model half of a refile ───────────────────────

@pytest.fixture
def bot_half(monkeypatch) -> None:
    """The bot runner's `docker exec` of a refile's model half: it works the
    correction into the card and keeps its sources as they are."""

    def call(entrypoint, command, *argv, input_text=None):
        asked = json.loads(input_text or "{}")
        after = asked["text"].rstrip("\n") + f"\n\n{asked['msg'] or 'Refiled.'}\n"
        return json.dumps({"proposed": after, "proposed_sidecar": asked["sidecar"],
                           "title": "Tent for the camping trip", "clean_rejected": "", "sources": []})

    monkeypatch.setattr("stack.bot_runner.call", call)


# ── The instance ─────────────────────────────────────────────────────────

@pytest.fixture
def simpson(tmp_path, httpserver, server_name, core_door, archivist, bot_half,  # noqa: F811
            as_stack_runs_it):
    """The instance, its Matrix server named `server_name`, with Forgejo,
    core's card door and the tokens as `stack up memory` leaves them."""
    instance = write_instance(tmp_path, server_name=server_name,
                              extra=f"\n[code]\nport = {httpserver.port}\n")
    toml = instance / "stack.toml"
    toml.write_text(toml.read_text().replace("[core]\n", f"[core]\ncards_port = {core_door}\n", 1))
    (instance / ".stack" / "secrets.toml").write_text(
        f'global__ADMIN_PASSWORD = "donuts"\nmemory__CARDS_WRITE_TOKEN = "{TOKEN}"\n'
        'memory__MEMORY_BOT_TOKEN = "bottoken"\n')
    return make_stack(instance)


@pytest.fixture
def marge(simpson, tmp_path) -> Person:
    return Person("marge", simpson, tmp_path)


# ── One person, one address ──────────────────────────────────────────────

def test_a_person_commits_under_one_address_on_every_path(memory, archivist, marge):
    """Given Marge on an instance whose Matrix server is `simpson`, when the
    archivist files a note she sent, she changes Bart's card with `stack
    memory card edit`, and she refiles her note with a correction, then
    each of the three commits is `marge <marge@simpson>`.

    Each door reads the server name from where it runs: the bot runner's
    and the curator's environment, the host's stack.toml. One rule over
    three readings is what keeps her commits under one address; two
    copies of it once gave her two.
    """
    note = archivist.files_a_note("@marge:simpson", "bring the big tent")
    marge.edits("person-bart", "--set", "description=Rides a skateboard.")
    marge.refiles(note.card_id, correction="the big tent is at Flanders'")

    assert memory.new_commits() == [commit(by="marge", saying="capture: Tent for the camping trip"),
                                    commit(by="marge", saying="edit: Bart Simpson"),
                                    commit(by="marge", saying="reprocess: Tent for the camping trip")]
    assert memory.authors() == ["marge <marge@simpson>"] * 3


@pytest.mark.parametrize("server_name", [None])
def test_without_a_server_name_a_persons_change_is_refused_and_says_where_it_comes_from(
        memory, archivist, marge):
    """Given an instance whose stack.toml names no Matrix server, when the
    archivist files Marge's note, she changes Bart's card and she refiles
    a note with a correction, then nothing is committed, and each command
    tells her the server name is missing and where it is set.

    An address made up in its place would put her commits under a second
    address for good: the memory's history is never rewritten.
    """
    filed = archivist.files_a_note("@marge:simpson", "bring the big tent")
    marge.edits("person-bart", "--set", "description=Rides a skateboard.")
    told_by_the_edit = marge.was_told("server_name", "[messages]", "stack.toml")
    marge.refiles(ART_SUPPLIES, correction="the paper is A3")

    assert filed is None
    assert memory.new_commits() == []
    assert told_by_the_edit
    assert marge.was_told("server_name", "[messages]", "stack.toml")
