"""`memory_api.MemoryAPI`: how the memory stacklet's own code outside the
curator reads and writes the memory, as that code sees it.

The host commands, hooks and migrations, and the memory's code in the bot
runner, call the curator with this client. Here it calls the real curator
API, served in this process over a card store whose clone pushes to a bare
repository standing in for Forgejo (`memory_git`). Where the client finds
the curator, on the host and in a container, is checked against a local
HTTP server at the address it should reach.
"""

from __future__ import annotations

import socket

import pytest

from memory.cards import CardStore
from memory.memory_api import CuratorDown, MemoryAPI, Refused, put, remove
from memory_git import Forgejo, clone_of, commit_as, curator_serving, version_of
from simpsons_cards import ONTOLOGY, VAULT

TOKEN = "cards-write-token"
LIST = "family/lists/camping.md"
BART = "family/people/bart.md"

CAMPING = """---
type: list
card_id: list-camping
title: Camping
---

# Camping

- [ ] Tent
"""


@pytest.fixture
def forgejo(tmp_path) -> Forgejo:
    forgejo = Forgejo(tmp_path)
    forgejo.seed("homer", "the Simpsons", {**VAULT, "ontology.toml": ONTOLOGY, LIST: CAMPING})
    return forgejo


@pytest.fixture
def store(forgejo, tmp_path) -> CardStore:
    (tmp_path / "curator").mkdir()
    return CardStore(clone_of(forgejo, tmp_path / "curator" / "vault"), remote="origin",
                     server_name="simpson")


@pytest.fixture
def memory(store) -> MemoryAPI:
    """The client as a process with the write token holds it, asking a
    curator whose mirror shows every write at once."""
    with curator_serving(store, TOKEN, on_write=lambda commit: True) as address:
        yield MemoryAPI(address, token=TOKEN, where="the memory's curator")


# ── Files and commits ────────────────────────────────────────────────────

def test_a_file_comes_with_its_version_and_a_missing_one_is_none(memory):
    camping = memory.file(LIST)

    assert (camping.path, camping.text, camping.version) == (LIST, CAMPING, version_of(CAMPING))
    assert memory.file("family/lists/groceries.md") is None


def test_a_directory_comes_as_its_files_with_their_versions(memory):
    """The files of a directory can be written on as they came, like one file."""
    found = memory.files("family/lists")

    assert LIST in [f.path for f in found]
    assert found == [memory.file(f.path) for f in found]
    assert all(f.path.startswith("family/lists/") for f in found)
    assert memory.files("bart/lists") == []


def test_a_change_made_from_a_read_file_is_committed_by_the_person(memory, forgejo):
    """Given the camping list read through the client, when Marge's change
    is written on its version, then Forgejo has one commit by her through
    the memory bot, and the answer names it."""
    camping = memory.file(LIST)

    written = memory.commit([put(LIST, CAMPING.replace("Tent", "Tent for four"), base=camping.version)],
                            by="marge", kind="chore(lists)", title="marge changed Camping")

    assert written.changed and not written.pending and written.mirrored
    assert forgejo.new_commits() == [
        "marge <marge@simpson> | memory-bot <memory-bot@local> | chore(lists): marge changed Camping"]


def test_a_removal_is_a_change_too(memory, forgejo):
    memory.commit([remove(LIST, base=version_of(CAMPING))], by="marge", kind="forget", title="Camping")

    assert forgejo.file(LIST) is None


def test_a_change_on_an_old_version_is_refused_naming_the_file(memory, forgejo, tmp_path):
    """Given Marge's edit in Forgejo after the list was read, when the old
    read's change is written, then it is refused as changed, naming the
    file, in the curator's words."""
    camping = memory.file(LIST)
    commit_as(clone_of(forgejo, tmp_path / "marge"), "marge", "Tent is torn",
              {LIST: CAMPING.replace("Tent", "Torn tent")})

    with pytest.raises(Refused) as refused:
        memory.commit([put(LIST, "x\n", base=camping.version)], by="homer", kind="chore(lists)",
                      title="homer changed Camping")

    assert (refused.value.status, refused.value.reason, refused.value.paths) == (409, "changed", (LIST,))
    assert "read it again" in str(refused.value)


def test_without_the_write_token_the_refusal_says_how_to_get_one(store):
    """Given a host where `stack up memory` has not made the write token yet,
    when a write is sent, then the person is told which command makes it."""
    with curator_serving(store, TOKEN) as address:
        tokenless = MemoryAPI(address, token="", where="the memory's curator")

        with pytest.raises(Refused, match="stack up memory"):
            tokenless.commit([put(LIST, "x\n", base=version_of(CAMPING))], by="marge",
                             kind="chore(lists)", title="Camping")


def test_a_sync_pulls_an_edit_made_in_forgejo(memory, forgejo, tmp_path):
    commit_as(clone_of(forgejo, tmp_path / "marge"), "marge", "Tent is torn",
              {LIST: CAMPING.replace("Tent", "Torn tent")})

    synced = memory.sync()

    assert synced.state == "ok" and synced.waiting == ()
    assert memory.file(LIST).text == CAMPING.replace("Tent", "Torn tent")


def test_what_waits_is_listed_with_how_the_last_cycle_went(memory, forgejo, store):
    forgejo.goes_down()
    memory.commit([put(LIST, "x\n", base=version_of(CAMPING))], by="marge", kind="chore(lists)",
                  title="marge changed Camping")
    store.sync()

    pending = memory.pending()

    assert pending.state == "unreachable"
    assert [c["subject"] for c in pending.waiting] == ["chore(lists): marge changed Camping"]


def test_a_cards_versions_come_with_its_id_and_path(memory):
    history = memory.versions("person-bart")

    assert (history.card_id, history.path) == ("person-bart", BART)
    assert [v["subject"] for v in history.versions] == ["the Simpsons"]


def test_the_versions_of_a_card_nobody_has_are_refused_as_not_found(memory):
    with pytest.raises(Refused) as refused:
        memory.versions("note-0000000000")

    assert refused.value.status == 404


# ── One card's fields, through the card editor ───────────────────────────

def test_a_card_is_changed_on_the_version_it_was_read_at(memory, forgejo):
    card, version = memory.card("person-bart")

    changed = memory.edit("person-bart", {"description": "Rides a skateboard."}, version=version,
                          by="marge")

    assert card["fields"]["title"] == "Bart Simpson"
    assert changed["fields"]["description"] == "Rides a skateboard."
    assert forgejo.new_commits() == [
        "marge <marge@simpson> | memory-bot <memory-bot@local> | edit: Bart Simpson"]


def test_a_card_change_the_schema_refuses_names_the_field(memory, forgejo):
    """The card editor's refusal comes back with each field it refused and
    what that field may hold, for the command to print."""
    _, version = memory.card("person-bart")

    with pytest.raises(Refused) as refused:
        memory.edit("person-bart", {"nickname": "El Barto"}, version=version, by="marge")

    assert refused.value.status == 422
    assert [e["field"] for e in refused.value.errors] == ["nickname"]
    assert forgejo.new_commits() == []


# ── Where the client finds the curator ───────────────────────────────────

def test_on_the_host_it_goes_through_cores_card_door_with_the_instances_token(httpserver):
    """Core publishes its card door on this Mac only; a host command reaches
    the curator there, on the port stack.toml names, with the token the
    instance's secrets hold."""
    httpserver.expect_request("/cards/api/pending", headers={"Authorization": f"Bearer {TOKEN}"}) \
        .respond_with_json({"state": "ok", "detail": "", "commits": []})
    config = {"stack": {"core": {"cards_port": httpserver.port}},
              "secrets": {"memory__CARDS_WRITE_TOKEN": TOKEN}}

    assert MemoryAPI.from_host(config).pending().state == "ok"


def test_in_a_container_it_goes_to_the_curator_on_the_stack_network(httpserver):
    """In the bot runner the curator is a name on the stack network, and the
    token comes with the container's environment."""
    httpserver.expect_request("/pending", headers={"Authorization": f"Bearer {TOKEN}"}) \
        .respond_with_json({"state": "ok", "detail": "", "commits": []})
    environ = {"MEMORY_API_URL": httpserver.url_for("").rstrip("/"), "CARDS_WRITE_TOKEN": TOKEN}

    assert MemoryAPI.from_container(environ).pending().state == "ok"


def test_no_curator_says_what_to_start():
    """Given nothing listening where the curator should be, when the client
    asks, then the person is told which stacklet to start."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environ = {"MEMORY_API_URL": f"http://127.0.0.1:{port}", "CARDS_WRITE_TOKEN": TOKEN}

    with pytest.raises(CuratorDown, match="stack up memory"):
        MemoryAPI.from_container(environ).sync()
