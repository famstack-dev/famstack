"""The card store over HTTP: how every process but the curator writes to the memory.

The curator serves the store on its HTTP server (`cards_api.serve`), and the
host, the bot runner and the curator's own child processes write and read
through it:

    POST /commits              a write, with the write token
    GET  /pending              what waits for Forgejo, and how the last cycle went
    GET  /cards/<id>/versions  a card's history

These tests start that server on a free port, in this process, over the
store of a real clone with a bare repository as Forgejo, and ask it with
the stack's JSON client. What they assert is what a caller is answered and
what Forgejo has afterwards.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from stacklets.memory.cards import CardStore, Put, StoreAPI
from stacklets.memory.cards_api import CardsAPI, serve
from tests.memory.unit.instance import Forgejo, clone_of, commit_as, curator_serving, git, version_of
from stack.cards.history import Version
from stack.http_json import JsonClient

TOKEN = "donuts"
LIST = "family/lists/camping.md"
NOTE = "family/notes/2026/10/tent.md"

CAMPING = """---
type: list
card_id: list-camping
title: Camping
---

# Camping

- [ ] Tent
"""

TENT_FOR_FOUR = CAMPING.replace("Tent", "Tent for four")

TENT_NOTE = """---
type: note
card_id: note-3f2a1b9c0d
title: Tent
---

# Tent

The tent is torn.
"""


def change_to_camping(text: str = TENT_FOR_FOUR, base: str = "") -> dict:
    """Marge's write of the camping list, as a caller sends it."""
    return {"kind": "chore(lists)", "title": "marge changed Camping", "by": "marge",
            "changes": [{"path": LIST, "text": text, "base": base or version_of(CAMPING)}]}


@pytest.fixture
def forgejo(tmp_path) -> Forgejo:
    forgejo = Forgejo(tmp_path)
    forgejo.seed("homer", "chore(lists): homer added Camping", {LIST: CAMPING})
    return forgejo


@pytest.fixture
def store(forgejo, tmp_path) -> CardStore:
    (tmp_path / "curator").mkdir()
    clone = clone_of(forgejo, tmp_path / "curator" / "vault")
    return CardStore(clone, remote="origin", server_name="simpson")


@pytest.fixture
def written() -> list[str]:
    """Each commit the API told the curator it wrote; the curator answers
    that the trees people read have it (`mirrored`)."""
    return []


@pytest.fixture
def curator(store, written):
    """The curator's HTTP server, serving the store on a free port."""
    def wrote(commit: str) -> bool:
        written.append(commit)
        return True
    with curator_serving(store, TOKEN, on_write=wrote) as address:
        yield address


@pytest.fixture
def writer(curator) -> JsonClient:
    """A process that holds the write token."""
    return JsonClient(curator, token=TOKEN)


# ── Writing ──────────────────────────────────────────────────────────────

def test_a_write_over_http_is_committed_and_pushed_before_the_answer(writer, forgejo, written):
    """Given Homer's camping list, when Marge's change arrives over HTTP,
    then the answer names the commit, Forgejo has it, by Marge through the
    memory bot, and the curator is told so it mirrors at once."""
    answer = writer.post("/commits", change_to_camping())

    assert answer.status == 201
    head = git(forgejo.repo, "rev-parse", "main")
    assert answer.body == {"commit": head, "pending": False, "mirrored": True}
    assert forgejo.new_commits() == [
        "marge <marge@simpson> | memory-bot <memory-bot@local> | chore(lists): marge changed Camping"]
    assert written == [head]


def test_the_answer_says_when_the_readers_do_not_have_the_change_yet(store, forgejo):
    """Given a curator whose mirror has not caught up within its wait, when
    a change is written, then it is written, and the answer says the wiki
    and the agent's mount do not show it yet.

    A caller told "not yet" waits; one told nothing would write again, and
    a write made twice is a duplicate.
    """
    with curator_serving(store, TOKEN, on_write=lambda commit: False) as address:
        answer = JsonClient(address, token=TOKEN).post("/commits", change_to_camping())

    assert answer.status == 201
    assert answer.body["mirrored"] is False
    assert len(forgejo.new_commits()) == 1


def test_a_write_names_its_committing_bot_and_its_message_parts(writer, forgejo):
    """Given the archivist filing a note for Homer, when it writes with a
    body and trailers, then Forgejo's commit carries them as sent."""
    answer = writer.post("/commits", {
        "kind": "capture", "title": "Tent", "by": "homer", "via": "archivist-bot",
        "body": "The tent is torn.", "trailers": {"Source": "chat"},
        "changes": [{"path": NOTE, "text": TENT_NOTE, "base": ""}]})

    assert answer.status == 201
    assert forgejo.new_commits() == [
        "homer <homer@simpson> | archivist-bot <archivist-bot@local> | capture: Tent"]
    assert forgejo.message() == "capture: Tent\n\nThe tent is torn.\n\nSource: chat"


def test_a_removal_is_a_change_with_remove_set(writer, forgejo):
    """Given Homer's camping list, when its removal arrives, then Forgejo
    no longer has it."""
    answer = writer.post("/commits", {
        "kind": "forget", "title": "Camping", "by": "marge",
        "changes": [{"path": LIST, "remove": True, "base": version_of(CAMPING)}]})

    assert answer.status == 201
    assert forgejo.file(LIST) is None


def test_writing_what_is_there_answers_that_nothing_changed(writer, forgejo, written):
    """Given Homer's camping list, when the same text arrives, then the
    answer has no commit, Forgejo has none, and the curator is not woken."""
    answer = writer.post("/commits", change_to_camping(CAMPING))

    assert (answer.status, answer.body) == (200, {"commit": "", "pending": False, "mirrored": False})
    assert forgejo.new_commits() == []
    assert written == []


@pytest.mark.parametrize("token", ["", "wrong"])
def test_a_write_without_the_write_token_is_refused(curator, forgejo, token):
    """Given a caller without the write token, when it writes, then it is
    refused and told what it needs, and nothing is written."""
    answer = JsonClient(curator, token=token).post("/commits", change_to_camping())

    assert answer.status == 401
    assert "write token" in answer.detail
    assert forgejo.new_commits() == []


def test_a_write_on_an_old_version_is_refused_naming_the_file(writer, forgejo, tmp_path):
    """Given Marge's change in Forgejo since the caller read the list, when
    the caller's change arrives, then it is refused as a conflict naming
    the file, in words a person can act on."""
    commit_as(clone_of(forgejo, tmp_path / "marge"), "marge", "Tent is torn",
              {LIST: CAMPING.replace("Tent", "Torn tent")})

    answer = writer.post("/commits", change_to_camping())

    assert answer.status == 409
    assert answer.body["reason"] == "changed" and answer.body["paths"] == [LIST]
    assert "read it again" in answer.detail


def test_a_write_the_store_cannot_make_says_why(writer, forgejo):
    """Given a kind of change the memory's history does not know, when it
    arrives, then it is refused with the store's reason."""
    answer = writer.post("/commits", {**change_to_camping(), "kind": "tidy"})

    assert answer.status == 422
    assert answer.body["reason"] == "refused" and "tidy" in answer.detail
    assert forgejo.new_commits() == []


@pytest.mark.parametrize("body, names", [
    ({"title": "x", "by": "marge", "changes": [{"path": LIST, "text": "x", "base": ""}]}, "kind"),
    ({"kind": "edit", "title": "x", "by": "marge", "changes": []}, "changes"),
    ({"kind": "edit", "title": "x", "by": "marge"}, "changes"),
    ({"kind": "edit", "title": "x", "by": "marge", "changes": [{"path": LIST, "text": "x"}]}, "base"),
    ({"kind": "edit", "title": "x", "by": "marge", "changes": [{"path": LIST, "base": ""}]}, "text"),
    ({"kind": "edit", "title": "x", "by": "marge", "trailers": {"Source": 1},
      "changes": [{"path": LIST, "text": "x", "base": ""}]}, "trailers"),
    (["not", "an", "object"], "object"),
])
def test_a_malformed_write_names_what_is_wrong(writer, forgejo, body, names):
    """Given a write missing a field or with one of the wrong type, when it
    arrives, then it is refused naming that field, and nothing is written.

    The caller is code, and a field it got wrong is a bug in it: the
    answer says which.
    """
    answer = writer.post("/commits", body)

    assert answer.status == 400
    assert answer.body["reason"] == "malformed" and names in answer.detail
    assert forgejo.new_commits() == []


def test_a_write_that_is_no_json_is_refused(curator, forgejo):
    """Given a body that is not JSON, when it arrives, then it is refused."""
    request = urllib.request.Request(f"{curator}/commits", data=b"camping", method="POST",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=10)

    assert refused.value.code == 400
    assert forgejo.new_commits() == []


def test_a_write_before_the_clone_exists_says_how_to_make_it(tmp_path):
    """Given a curator whose clone is not there yet, when a write arrives,
    then it is answered as unavailable, naming the command that makes it."""
    store = CardStore(tmp_path / "nowhere", remote="origin", server_name="simpson")
    server = serve(CardsAPI(vault=tmp_path / "nowhere"), host="127.0.0.1", port=0,
                   store_api=StoreAPI(store, token=TOKEN))
    try:
        answer = JsonClient(f"http://127.0.0.1:{server.server_address[1]}", token=TOKEN).post(
            "/commits", change_to_camping())
    finally:
        server.shutdown()
        server.server_close()

    assert answer.status == 503
    assert answer.body["reason"] == "missing" and "stack up memory" in answer.detail


def test_before_the_clone_exists_the_curator_answers_and_says_it_is_missing(tmp_path):
    """Given a curator that has no clone yet, as on a new instance, when it
    is asked what waits, then it answers: the curator is up, its clone is
    missing. `stack up memory` waits for this answer before it goes on."""
    store = CardStore(tmp_path / "nowhere", remote="origin", server_name="simpson")
    server = serve(CardsAPI(vault=tmp_path / "nowhere"), host="127.0.0.1", port=0,
                   store_api=StoreAPI(store, token=TOKEN))
    try:
        answer = JsonClient(f"http://127.0.0.1:{server.server_address[1]}").get("/pending")
    finally:
        server.shutdown()
        server.server_close()

    assert answer.status == 200
    assert answer.body["state"] == "missing" and answer.body["commits"] == []


def test_a_sync_runs_what_the_curator_gives_the_api_for_it(store, written):
    """The curator's sync does more than the store's: it makes the clone and
    seeds a new memory. `POST /sync` runs the one the curator gives."""
    from stacklets.memory.cards import Sync

    asked = []
    api = StoreAPI(store, token=TOKEN, sync=lambda: asked.append("sync") or Sync("ok"))

    status, _, _ = api.answer("POST", "/sync", {"authorization": f"Bearer {TOKEN}"}, b"")

    assert (status, asked) == (200, ["sync"])


# ── Reading a file to change it ──────────────────────────────────────────

def test_a_file_comes_with_the_version_a_write_names(writer):
    """Given Homer's camping list, when it is read by its path, then the
    answer has its text and the version a write made from it names."""
    answer = JsonClient(writer.base_url).get(f"/files/{LIST}")

    assert answer.status == 200
    assert answer.body == {"path": LIST, "text": CAMPING, "version": version_of(CAMPING)}


def test_a_file_that_is_not_there_is_not_found(writer):
    answer = JsonClient(writer.base_url).get("/files/family/lists/groceries.md")

    assert answer.status == 404
    assert "family/lists/groceries.md" in answer.detail


def test_a_path_outside_the_memory_is_refused_on_reading_too(writer):
    """`..` never reaches past the memory, whichever route names it."""
    answer = JsonClient(writer.base_url).get("/files/family/../../etc/passwd")

    assert answer.status in (404, 422)
    assert "root:" not in str(answer.body)


def test_a_directory_comes_as_every_file_under_it_with_its_version(writer):
    """Given Homer's camping list, when the bucket's lists are read, then
    the answer has each file under the directory with its text and version."""
    answer = JsonClient(writer.base_url).get("/tree/family/lists")

    assert answer.status == 200
    assert answer.body == {"files": [{"path": LIST, "text": CAMPING, "version": version_of(CAMPING)}]}


def test_a_directory_that_is_not_there_has_no_files(writer):
    assert JsonClient(writer.base_url).get("/tree/bart/lists").body == {"files": []}


def test_the_paths_of_a_directory_or_of_the_whole_memory_come_without_their_text(writer):
    reader = JsonClient(writer.base_url)

    assert reader.get("/paths/family").body == {"paths": [LIST]}
    assert reader.get("/paths").body == {"paths": [LIST]}
    assert reader.get("/paths/bart").body == {"paths": []}


def test_a_directory_outside_the_memory_is_refused(writer):
    answer = JsonClient(writer.base_url).get("/tree/family/../..")

    assert answer.status == 422
    assert "not a directory of the memory" in answer.detail


# ── Bringing the clone up to Forgejo now ─────────────────────────────────

def test_a_sync_pulls_a_change_made_in_forgejo_at_once(writer, forgejo, store, tmp_path):
    """Given Marge's edit in Forgejo that the curator has not pulled yet,
    when a sync is asked for, then the curator's clone has it, and the
    answer says all is well.

    A command that reads right after a person edited in Forgejo asks for
    this instead of pulling the curator's clone itself.
    """
    commit_as(clone_of(forgejo, tmp_path / "marge"), "marge", "Tent is torn",
              {LIST: CAMPING.replace("Tent", "Torn tent")})

    answer = writer.post("/sync", {})

    assert answer.status == 200
    assert answer.body["state"] == "ok" and answer.body["commits"] == []
    assert store.read(LIST).text == CAMPING.replace("Tent", "Torn tent")


def test_a_sync_answers_the_commit_the_clone_stands_at(writer, forgejo, tmp_path):
    """Given Marge's edit in Forgejo, when a sync is asked for, then the
    answer names Forgejo's head, now the clone's: what the wiki has to
    reach before `stack memory sync` and `stack memory wiki update` report
    it current."""
    commit_as(clone_of(forgejo, tmp_path / "marge"), "marge", "Tent is torn",
              {LIST: CAMPING.replace("Tent", "Torn tent")})

    answer = writer.post("/sync", {})

    assert answer.body["head"] == git(forgejo.repo, "rev-parse", "main")


def test_with_forgejo_down_a_sync_answers_the_clones_own_head(writer, store, forgejo):
    """Given Forgejo down and Marge's change waiting in the clone, when a
    sync is asked for, then the answer names the clone's head, her change:
    everything written went through the curator, so the clone is the
    newest the memory is."""
    forgejo.goes_down()
    written = writer.post("/commits", change_to_camping())

    answer = writer.post("/sync", {})

    assert answer.body["state"] == "unreachable"
    assert answer.body["head"] == written.body["commit"] == git(store.repo, "rev-parse", "HEAD")


def test_a_sync_needs_the_write_token(curator):
    """A sync may push what waits, so it is a write."""
    assert JsonClient(curator).post("/sync", {}).status == 401


# ── What waits for Forgejo ───────────────────────────────────────────────

def test_pending_lists_what_waits_and_how_the_last_cycle_went(writer, store, forgejo):
    """Given Forgejo down, when Marge's change is written and the curator's
    cycle has run, then the change is answered as pending, and `/pending`
    lists it with the cycle's state.

    `stack status` and `stack doctor` read this to tell the admin that
    changes wait, and why.
    """
    forgejo.goes_down()

    written = writer.post("/commits", change_to_camping())
    store.sync()
    pending = JsonClient(writer.base_url).get("/pending")

    assert written.status == 201 and written.body["pending"] is True
    assert pending.status == 200
    assert pending.body["state"] == "unreachable"
    assert [c["subject"] for c in pending.body["commits"]] == ["chore(lists): marge changed Camping"]


def test_nothing_pending_before_the_first_cycle(writer):
    """Given a curator that has not synced yet and nothing waiting, then
    `/pending` lists nothing and says no cycle has run yet."""
    pending = JsonClient(writer.base_url).get("/pending")

    assert pending.body == {"state": "unknown", "detail": "", "commits": []}


# ── A card's history ─────────────────────────────────────────────────────

def test_a_cards_versions_come_back_oldest_first(writer, store):
    """Given Homer's note filed and then corrected by Marge, when its
    versions are asked for by its card id, then both come back oldest
    first, each as `Version.to_json` wrote it."""
    store.commit([Put(NOTE, TENT_NOTE, "")], by="homer", via="archivist-bot", kind="capture",
                 title="Tent")
    corrected = TENT_NOTE.replace("is torn", "is torn at the seam")
    store.commit([Put(NOTE, corrected, version_of(TENT_NOTE))], by="marge", kind="edit",
                 title="Tent")

    answer = JsonClient(writer.base_url).get("/cards/note-3f2a1b9c0d/versions")

    assert answer.status == 200
    versions = [Version.from_json(v) for v in answer.body["versions"]]
    assert [(v.subject, v.author) for v in versions] == [("capture: Tent", "homer"),
                                                          ("edit: Tent", "marge")]
    assert versions[-1].card == corrected
    assert answer.body["path"] == NOTE


def test_the_versions_of_a_card_nobody_has_are_not_found(writer):
    """Given no card with the id, when its versions are asked for, then the
    answer is that there is no such card."""
    answer = JsonClient(writer.base_url).get("/cards/note-0000000000/versions")

    assert answer.status == 404
    assert "note-0000000000" in answer.detail
