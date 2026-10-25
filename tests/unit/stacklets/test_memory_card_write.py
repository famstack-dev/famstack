"""The card write endpoint: the one way a card changes, from the client's side.

Every surface that changes a card (the card browser, `stack memory card
edit`, the agent through that command) sends the change to the memory
stacklet, which checks it against the card type's schema and commits it
as the person it is by (ADR-015). These tests are that client: they read a
card and its version over HTTP, send changes, and look at what the memory
repo holds afterwards.

Forgejo is a git repository the endpoint's card store pushes to, which is
all Forgejo is to git; the memory stacklet's side is real: its HTTP
server, its schemas, the card store and the curator's clone of the memory
repo, where it finds cards and people. The command that is its client is described in
`tests/unit/surface/test_memory_card_edit.py`.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))
sys.path.insert(0, str(Path(__file__).parent))

from stacklets.memory.card_edit import CardEditor  # noqa: E402
from stacklets.memory.cards_api import CardsAPI, serve  # noqa: E402
from tests.memory.unit.instance import Forgejo, clone_of, commit_as, version_of  # noqa: E402
from simpsons_cards import ONTOLOGY, VAULT  # noqa: E402

from stacklets.memory.cards import CardStore  # noqa: E402
from stack.frontmatter import parse  # noqa: E402

TOKEN = "write-token-for-tests"
BART = "family/people/bart.md"
NOTE = "marge/notes/2026/10/art-supplies.md"
SIDECAR = "marge/notes/2026/10/art-supplies.json"
BOOKMARK = "family/bookmarks/2026/10/krusty-burger.md"


@pytest.fixture
def forgejo(tmp_path) -> Forgejo:
    """family/memory on Forgejo, holding the Simpsons' cards."""
    forgejo = Forgejo(tmp_path)
    forgejo.seed("marge", "feat: the Simpsons", {**VAULT, "ontology.toml": ONTOLOGY})
    return forgejo


@pytest.fixture
def endpoint(forgejo, tmp_path):
    """The card write endpoint as the curator serves it, over the card store
    of the curator's clone."""
    (tmp_path / "curator").mkdir()
    store = CardStore(clone_of(forgejo, tmp_path / "curator" / "vault"), remote="origin",
                      server_name="simpson")
    server = serve(CardsAPI(vault=store.repo), host="127.0.0.1", port=0,
                   editor=CardEditor(store=store, token=TOKEN))
    yield f"http://127.0.0.1:{server.server_address[1]}"
    # `shutdown` returns at the server's next poll, half a second away, and
    # nothing after a test waits for it.
    threading.Thread(target=server.shutdown, daemon=True).start()


def _send(base: str, method: str, card_id: str, *, body: bytes | None = None,
          headers: dict | None = None) -> tuple[int, dict, dict | str]:
    request = urllib.request.Request(f"{base}/cards/{card_id}", data=body, method=method,
                                     headers=headers or {})
    try:
        with urllib.request.urlopen(request) as r:
            status, sent, raw = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, sent, raw = e.code, dict(e.headers), e.read()
    kind = sent.get("Content-Type", "")
    return status, sent, json.loads(raw) if "json" in kind else raw.decode()


def _read(base: str, card_id: str) -> tuple[dict, str]:
    status, sent, card = _send(base, "GET", card_id)
    assert status == 200
    return card, sent["ETag"]


def _patch(base: str, card_id: str, fields: dict, *, etag: str, actor: str = "marge",
           token: str = TOKEN, kind: str = "application/merge-patch+json"):
    headers = {"Content-Type": kind, "Famstack-Actor": actor}
    if etag:
        headers["If-Match"] = etag
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _send(base, "PATCH", card_id, body=json.dumps({"fields": fields}).encode(), headers=headers)


def _put(base: str, card_id: str, text: str, *, etag: str, actor: str = "memory-bot"):
    return _send(base, "PUT", card_id, body=text.encode(), headers={
        "Content-Type": "text/markdown", "If-Match": etag, "Famstack-Actor": actor,
        "Authorization": f"Bearer {TOKEN}"})


class TestReadingACard:

    def test_a_card_comes_with_its_fields_its_text_and_its_version(self, endpoint, forgejo):
        card, etag = _read(endpoint, "person-bart")

        assert card["fields"]["title"] == "Bart Simpson"
        assert card["rendered"].startswith("# Bart Simpson")
        assert etag == f'"{version_of(forgejo.file(BART))}"'

    def test_asked_for_markdown_it_is_the_file(self, endpoint, forgejo):
        status, sent, text = _send(endpoint, "GET", "person-bart", headers={"Accept": "text/markdown"})

        assert (status, text) == (200, forgejo.file(BART))
        assert sent["Content-Type"].startswith("text/markdown")

    def test_a_card_no_file_carries_is_not_found(self, endpoint):
        assert _send(endpoint, "GET", "person-grampa")[0] == 404


class TestChangingACard:

    def test_a_field_of_a_person_is_committed_once_as_the_person(self, endpoint, forgejo):
        _, etag = _read(endpoint, "person-bart")

        status, sent, card = _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag)

        assert status == 200
        assert card["fields"]["description"] == "Rides a skateboard."
        assert parse(forgejo.file(BART))["description"] == "Rides a skateboard."
        assert forgejo.new_commits() == [
            "marge <marge@simpson> | memory-bot <memory-bot@local> | edit: Bart Simpson"]
        assert forgejo.message() == "edit: Bart Simpson\n\ndescription: Rides a skateboard."
        assert sent["ETag"] == f'"{version_of(forgejo.file(BART))}"'

    def test_only_the_field_changes_every_other_byte_stays(self, endpoint, forgejo):
        before = forgejo.file(BART)
        _, etag = _read(endpoint, "person-bart")

        _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag)

        assert forgejo.file(BART) == before.replace("account: bart\n", "account: bart\ndescription: Rides a skateboard.\n")

    def test_a_change_that_changes_nothing_makes_no_commit(self, endpoint, forgejo):
        _, etag = _read(endpoint, "person-bart")

        status, sent, _ = _patch(endpoint, "person-bart", {"title": "Bart Simpson"}, etag=etag)

        assert (status, forgejo.new_commits()) == (200, [])
        assert sent["ETag"] == etag

    def test_new_people_are_linked_in_the_meta_block(self, endpoint, forgejo):
        """The frontmatter holds the ids, the body the links (ADR-017 §3)."""
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        _patch(endpoint, "note-7f3a9c21e4", {"persons": ["marge", "bart"]}, etag=etag)

        assert ("> **About** [Marge](../../../../family/people/marge.md), "
                "[Bart](../../../../family/people/bart.md)\n") in forgejo.file(NOTE)

    def test_a_change_to_a_note_leaves_its_sidecar_as_it_is(self, endpoint, forgejo):
        """A note's sidecar keeps where it came from and how it was made
        (ADR-018): a person changes what the card says, so the commit holds
        the card alone, and its sources are as they were."""
        sidecar = forgejo.file(SIDECAR)
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        status, _, _ = _patch(endpoint, "note-7f3a9c21e4", {"description": "For Marge's class."}, etag=etag)

        assert status == 200
        assert forgejo.changed() == [NOTE]
        assert forgejo.file(SIDECAR) == sidecar
        assert forgejo.file(NOTE).endswith("## Sources\n\n> brushes + paper for next time\n")

    def test_a_removed_field_is_gone_from_the_file(self, endpoint, forgejo):
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        _patch(endpoint, "note-7f3a9c21e4", {"tags": None}, etag=etag)

        assert "tags" not in parse(forgejo.file(NOTE))
        assert "tags: (removed)" in forgejo.message()



class TestOnlyFieldsChange:

    def test_a_whole_card_is_not_put_and_nothing_is_written(self, endpoint, forgejo):
        """A person changes a card's fields with PATCH. A whole card is
        written only by the reprocess, through the memory's write path,
        never through this endpoint: it has no PUT."""
        _, etag = _read(endpoint, "note-7f3a9c21e4")
        text = forgejo.file(NOTE) + "\nAnd the easel.\n"

        status, _, _ = _put(endpoint, "note-7f3a9c21e4", text, etag=etag)

        assert status == 501
        assert forgejo.new_commits() == []


class TestWhatIsRefused:

    def test_a_tag_that_is_no_topic_id_and_no_keyword(self, endpoint, forgejo):
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        status, sent, problem = _patch(endpoint, "note-7f3a9c21e4",
                                       {"tags": ["watercolour", "Gesundheit"]}, etag=etag)

        assert status == 422
        assert sent["Content-Type"] == "application/problem+json"
        [entry] = problem["errors"]
        assert entry["field"] == "tags" and "medical" in entry["allowed"]
        assert forgejo.new_commits() == []

    def test_a_field_no_schema_names(self, endpoint, forgejo):
        _, etag = _read(endpoint, "person-bart")

        status, _, problem = _patch(endpoint, "person-bart", {"nickname": "Bartman"}, etag=etag)

        assert status == 422
        [entry] = problem["errors"]
        assert entry["field"] == "nickname" and "aliases" in entry["allowed"]
        assert forgejo.new_commits() == []

    def test_where_a_note_came_from_which_its_sidecar_keeps(self, endpoint, forgejo):
        """Who filed a note, and from which message, is in its sidecar
        (ADR-018), not among the card's fields, so a change cannot put it
        back on the card."""
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        status, _, problem = _patch(endpoint, "note-7f3a9c21e4", {"filed_by": "bart"}, etag=etag)

        assert status == 422
        [entry] = problem["errors"]
        assert entry["field"] == "filed_by" and "persons" in entry["allowed"]
        assert forgejo.new_commits() == []

    def test_a_read_only_field_with_what_it_holds(self, endpoint, forgejo):
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        status, _, problem = _patch(endpoint, "note-7f3a9c21e4", {"card_id": "note-mine"}, etag=etag)

        assert status == 422
        assert problem["errors"] == [{"pointer": "/fields/card_id", "field": "card_id",
                                      "message": "`card_id` is read-only", "allowed": ["note-7f3a9c21e4"]}]

    def test_a_persons_account_which_decides_who_is_a_member(self, endpoint, forgejo):
        _, etag = _read(endpoint, "person-maggie")

        status, _, problem = _patch(endpoint, "person-maggie", {"account": "maggie"}, etag=etag)

        assert status == 422
        assert problem["errors"][0]["message"] == "`account` is read-only"
        assert forgejo.new_commits() == []

    def test_a_stale_version_and_nothing_is_written(self, endpoint, forgejo, tmp_path):
        """Marge changes Bart's card in Forgejo after the client read it: the
        change is refused, and the answer carries the card's version now, to
        read it again."""
        _, etag = _read(endpoint, "person-bart")
        commit_as(clone_of(forgejo, tmp_path / "marge"), "marge", "Marge's own change",
                  {BART: forgejo.file(BART) + "Marge changed this meanwhile.\n"})

        status, sent, _ = _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag)

        assert status == 412
        assert sent["ETag"] == f'"{version_of(forgejo.file(BART))}"'
        assert [c.split(" | ")[2] for c in forgejo.new_commits()] == ["Marge's own change"]
        assert "description" not in parse(forgejo.file(BART))

    def test_a_change_without_a_version(self, endpoint, forgejo):
        assert _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag="")[0] == 428

    def test_a_change_in_another_media_type(self, endpoint):
        _, etag = _read(endpoint, "person-bart")

        assert _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag,
                      kind="application/json")[0] == 415

    def test_a_change_to_a_card_that_is_not_there(self, endpoint):
        assert _patch(endpoint, "person-grampa", {"description": "Rides a skateboard."}, etag='"abc"')[0] == 404

    def test_a_change_without_the_write_token(self, endpoint, forgejo):
        """The token is generated for the memory stacklet and read by the
        host CLI; no other container carries it. A container that reaches the
        endpoint on the stack network, the agent's among them, cannot write."""
        _, etag = _read(endpoint, "person-bart")

        assert _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag, token="")[0] == 401
        assert _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag, token="guess")[0] == 401
        assert forgejo.new_commits() == []

    def test_a_change_by_someone_who_is_no_member(self, endpoint):
        _, etag = _read(endpoint, "person-bart")

        status, _, problem = _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag,
                                    actor="maggie")

        assert status == 422
        assert problem["errors"][0]["allowed"] == ["bart", "homer", "marge"]


def test_before_the_clone_is_there_the_endpoint_says_so(tmp_path):
    """The curator's clone is made by `stack up memory`; until then a card
    is not missing, the curator cannot see it yet, and says how to fix it."""
    store = CardStore(tmp_path / "nowhere", remote="origin", server_name="simpson")
    editor = CardEditor(store=store, token=TOKEN)

    status, _, body = editor.answer("GET", "person-bart", {}, b"")

    assert status == 503 and b"stack up memory" in body


def test_the_write_token_reaches_the_curator_and_the_bot_runner_and_no_other_stacklet():
    """The write token is in memory's `.env`, which the curator, which checks
    it, reads; and in core's `.env`, for the memory's code in the bot runner
    (the diary, the archivist's filings), which writes through the curator.
    No other stacklet's containers, the agent's among them, carry it."""
    holders = [p.relative_to(_REPO).as_posix() for p in (_REPO / "stacklets").rglob("*")
               if p.suffix in (".yml", ".toml") and "CARDS_WRITE_TOKEN" in p.read_text()]

    assert sorted(holders) == ["stacklets/core/stacklet.toml", "stacklets/memory/docker-compose.yml",
                               "stacklets/memory/stacklet.toml"]
