"""`stack messages room create`: a family room the bots can always read.

Element X creates every room encrypted and offers no way to switch it
off, and an encrypted room ties the family to their device keys. A room
the stack creates is never encrypted: the server and the bots are the
family's own Mac. It sits in the Family space, the family and the
archivist are invited, and asking twice for the same name gives back
the room that already exists instead of a second one. Stacker's
`stack room create` in the Server Room runs this same command.

Synapse is replaced at its HTTP boundary by pytest-httpserver; the
client code under test is the real one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "cli"))
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from _matrix import MatrixClient  # noqa: E402
from room import create_family_room  # noqa: E402

SPACE, NEW, OLD = "!space:simpson", "!new:simpson", "!old:simpson"
USERS = [
    {"id": "homer", "role": "admin"},
    {"id": "marge", "role": "member", "stacklets": ["messages", "docs"]},
    {"id": "lisa", "role": "member", "stacklets": ["messages"]},
    # No messages account: inviting her would invite nobody.
    {"id": "maggie", "role": "member", "stacklets": ["photos"]},
]


class Synapse:
    """The rooms the server already has, and a createRoom that answers NEW."""

    def __init__(self, httpserver, rooms=()):
        self.httpserver = httpserver
        listed = [{"room_id": SPACE, "name": "Simpson Family", "room_type": "m.space"},
                  *rooms]
        httpserver.expect_request("/_synapse/admin/v1/rooms", method="GET") \
            .respond_with_json({"rooms": listed})
        httpserver.expect_request("/_matrix/client/v3/createRoom", method="POST") \
            .respond_with_json({"room_id": NEW})
        httpserver.expect_request(
            f"/_matrix/client/v3/rooms/{SPACE}/state/m.space.child/{NEW}", method="PUT",
        ).respond_with_json({"event_id": "$child"})
        httpserver.expect_request(f"/_matrix/client/v3/rooms/{NEW}/invite", method="POST") \
            .respond_with_json({})
        levels = f"/_matrix/client/v3/rooms/{NEW}/state/m.room.power_levels/"
        httpserver.expect_request(levels, method="GET") \
            .respond_with_json({"users": {"@stackadmin:simpson": 100}, "users_default": 0})
        httpserver.expect_request(levels.rstrip("/"), method="PUT").respond_with_json({})
        httpserver.expect_request(levels, method="PUT").respond_with_json({})
        httpserver.expect_request(
            "/_matrix/client/v3/directory/room/#topic-garden:simpson",
        ).respond_with_json({"errcode": "M_NOT_FOUND"}, status=404)

    def bodies(self, path, method="POST"):
        return [json.loads(req.get_data() or b"{}") for req, _ in self.httpserver.log
                if req.path == path and req.method == method]

    def create_room_body(self):
        (body,) = self.bodies("/_matrix/client/v3/createRoom")
        return body


def _client(httpserver):
    client = MatrixClient(httpserver.url_for("").rstrip("/"), "simpson", str(_REPO_ROOT))
    client.token = "t"
    return client


def test_the_room_is_created_without_encryption(httpserver):
    synapse = Synapse(httpserver)

    create_family_room(_client(httpserver), "Topic: Garden", USERS)

    body = synapse.create_room_body()
    assert body["name"] == "Topic: Garden"
    assert body["preset"] == "private_chat"
    types = [event["type"] for event in body["initial_state"]]
    assert "m.room.encryption" not in types
    # New members read what was said before they joined, as in every family room.
    assert {"type": "m.room.history_visibility",
            "content": {"history_visibility": "shared"}} in body["initial_state"]


def test_the_room_is_part_of_the_family_space_from_both_sides(httpserver):
    """The room names the space as its parent and the space lists the room
    as a child: Element shows it under the space either way round."""
    synapse = Synapse(httpserver)

    create_family_room(_client(httpserver), "Topic: Garden", USERS)

    parents = [event for event in synapse.create_room_body()["initial_state"]
               if event["type"] == "m.space.parent"]
    assert parents == [{"type": "m.space.parent", "state_key": SPACE,
                        "content": {"via": ["simpson"], "canonical": True}}]
    assert synapse.bodies(f"/_matrix/client/v3/rooms/{SPACE}/state/m.space.child/{NEW}",
                          method="PUT") == [{"via": ["simpson"], "suggested": True}]


def test_the_room_gets_an_alias_made_from_its_name(httpserver):
    synapse = Synapse(httpserver)

    create_family_room(_client(httpserver), "Topic: Garden", USERS)

    assert synapse.create_room_body()["room_alias_name"] == "topic-garden"


def test_the_family_with_a_chat_account_and_the_archivist_are_invited(httpserver):
    synapse = Synapse(httpserver)

    create_family_room(_client(httpserver), "Topic: Garden", USERS)

    invited = [b["user_id"] for b in synapse.bodies(f"/_matrix/client/v3/rooms/{NEW}/invite")]
    assert invited == ["@homer:simpson", "@marge:simpson", "@lisa:simpson",
                       "@archivist-bot:simpson"]


def test_the_familys_admins_are_admins_in_the_new_room(httpserver):
    synapse = Synapse(httpserver)

    create_family_room(_client(httpserver), "Topic: Garden", USERS)

    path = f"/_matrix/client/v3/rooms/{NEW}/state/m.room.power_levels"
    (written,) = synapse.bodies(path, method="PUT") + synapse.bodies(path + "/", method="PUT")
    assert written["users"]["@homer:simpson"] == 100


def test_the_result_links_the_new_room(httpserver):
    Synapse(httpserver)

    result = create_family_room(_client(httpserver), "Topic: Garden", USERS)

    assert result == {"ok": True, "created": True, "room_id": NEW, "name": "Topic: Garden",
                      "alias": "#topic-garden:simpson",
                      "link": f"https://matrix.to/#/{NEW}"}


def test_a_name_that_is_taken_gives_back_that_room_and_creates_none(httpserver):
    """Matched without regard to case: two rooms called "Topic: Garden" and
    "topic: garden" would be the same topic to the archivist."""
    synapse = Synapse(httpserver, rooms=[{"room_id": OLD, "name": "topic: garden"}])

    result = create_family_room(_client(httpserver), "Topic: Garden", USERS)

    assert synapse.bodies("/_matrix/client/v3/createRoom") == []
    assert result == {"ok": True, "created": False, "room_id": OLD, "name": "topic: garden",
                      "link": f"https://matrix.to/#/{OLD}"}


def test_a_name_with_no_letter_or_digit_is_refused(httpserver):
    synapse = Synapse(httpserver)

    result = create_family_room(_client(httpserver), "🌻 !", USERS)

    assert "error" in result
    assert synapse.bodies("/_matrix/client/v3/createRoom") == []


def test_an_alias_that_is_taken_gives_back_that_room_and_creates_none(httpserver):
    """"Topic Garden" and "Topic: Garden" both make the alias topic-garden."""
    httpserver.expect_request("/_matrix/client/v3/directory/room/#topic-garden:simpson") \
        .respond_with_json({"room_id": OLD})
    httpserver.expect_request(f"/_matrix/client/v3/rooms/{OLD}/state/m.room.name/") \
        .respond_with_json({"name": "Topic Garden"})
    synapse = Synapse(httpserver)

    result = create_family_room(_client(httpserver), "Topic: Garden", USERS)

    assert synapse.bodies("/_matrix/client/v3/createRoom") == []
    assert result["created"] is False
    assert (result["room_id"], result["name"]) == (OLD, "Topic Garden")
