"""The family's admins are admins in every family room.

Synapse's server-admin flag grants nothing inside a room, and a room's
creator, the tech admin, is its only admin unless someone is promoted.
Every room the stack creates therefore promotes the admin-role users
from users.toml: the installer's rooms, rooms made later with
`stack messages room create`, and the bot rooms the bot-runner creates.
`stack messages setup --room-admins` applies the rule to the whole family Space, on
every `stack up messages`, which is what corrects an existing install.

Synapse is replaced at its HTTP boundary by pytest-httpserver; the
client code under test is the real one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "cli"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from _admins import admin_user_ids, ensure_admins, family_room_ids  # noqa: E402
from _matrix import MatrixClient  # noqa: E402

SPACE, DOCS, PICNIC, GONE = "!space:simpson", "!docs:simpson", "!picnic:simpson", "!gone:simpson"
USERS = [
    {"id": "homer", "role": "admin"},
    {"id": "marge", "role": "member"},
    {"id": "lisa", "role": "member"},
]


def _levels(**users):
    return {"users": {f"@{k}:simpson": v for k, v in users.items()},
            "users_default": 0, "state_default": 50, "events": {"m.room.name": 50}}


class Synapse:
    """Serves room power levels and the Space's children, records writes."""

    def __init__(self, httpserver, rooms):
        self.writes: dict[str, dict] = {}
        space_state = [
            {"type": "m.space.child", "state_key": DOCS, "content": {"via": ["simpson"]}},
            {"type": "m.space.child", "state_key": PICNIC, "content": {"via": ["simpson"]}},
            # A child removed from the Space keeps its event, emptied.
            {"type": "m.space.child", "state_key": GONE, "content": {}},
            {"type": "m.room.name", "state_key": "", "content": {"name": "Family"}},
        ]
        httpserver.expect_request(f"/_matrix/client/v3/rooms/{SPACE}/state", method="GET") \
            .respond_with_json(space_state)
        # The tech admin left GONE when it was deleted; it is still listed.
        httpserver.expect_request("/_matrix/client/v3/joined_rooms") \
            .respond_with_json({"joined_rooms": [SPACE, DOCS, PICNIC]})
        httpserver.expect_request(f"/_matrix/client/v3/rooms/{DOCS}/state/m.room.name/") \
            .respond_with_json({"name": "Documents Room"})
        for rid, levels in rooms.items():
            path = f"/_matrix/client/v3/rooms/{rid}/state/m.room.power_levels/"
            httpserver.expect_request(path, method="GET").respond_with_json(levels)
            httpserver.expect_request(path.rstrip("/"), method="PUT") \
                .respond_with_handler(self._recorder(rid))
            httpserver.expect_request(path, method="PUT").respond_with_handler(self._recorder(rid))

    def _recorder(self, rid):
        from werkzeug import Response

        def handle(request):
            self.writes[rid] = json.loads(request.data)
            return Response(json.dumps({"event_id": "$x"}), content_type="application/json")
        return handle


def _client(httpserver):
    client = MatrixClient(httpserver.url_for("").rstrip("/"), "simpson", str(_REPO_ROOT))
    client.token = "t"
    return client


def test_the_admins_are_the_admin_role_users_from_users_toml():
    assert admin_user_ids(USERS) == ["homer"]


def test_family_rooms_are_the_space_and_the_rooms_the_stack_is_still_in(httpserver):
    Synapse(httpserver, {})
    assert family_room_ids(_client(httpserver), SPACE) == [SPACE, DOCS, PICNIC]


def test_an_admin_missing_from_a_room_is_promoted_and_nothing_else_changes(httpserver):
    synapse = Synapse(httpserver, {DOCS: _levels(stackadmin=100, marge=0)})

    results = ensure_admins(_client(httpserver), [DOCS], ["homer"])

    written = synapse.writes[DOCS]
    assert written["users"] == {"@stackadmin:simpson": 100, "@marge:simpson": 0, "@homer:simpson": 100}
    assert written["events"] == {"m.room.name": 50} and written["state_default"] == 50
    assert results == [{"ok": True, "user": "homer", "room": "Documents Room",
                        "item": "@homer:simpson in Documents Room", "action": "promoted to PL 100"}]


def test_a_room_that_is_already_right_is_not_written(httpserver):
    synapse = Synapse(httpserver, {PICNIC: _levels(stackadmin=100, homer=100)})

    assert ensure_admins(_client(httpserver), [PICNIC], ["homer"]) == []
    assert synapse.writes == {}


def test_members_are_left_at_their_level(httpserver):
    synapse = Synapse(httpserver, {DOCS: _levels(stackadmin=100)})

    ensure_admins(_client(httpserver), [DOCS], admin_user_ids(USERS))

    assert "@marge:simpson" not in synapse.writes[DOCS]["users"]
    assert "@lisa:simpson" not in synapse.writes[DOCS]["users"]


def test_the_bot_runner_makes_the_admins_admins_of_a_bot_room(httpserver):
    """The Documents room is created by the bot-runner on `stack up docs`,
    before messages runs again, so the bot-runner applies the same rule
    every time it starts."""
    import accounts
    synapse = Synapse(httpserver, {DOCS: _levels(stackadmin=100)})
    httpserver.expect_request("/_matrix/client/v3/login", method="POST") \
        .respond_with_json({"access_token": "t"})
    httpserver.expect_request("/_matrix/client/v3/directory/room/#family:simpson") \
        .respond_with_json({"room_id": SPACE})
    httpserver.expect_request("/_matrix/client/v3/directory/room/#documents:simpson") \
        .respond_with_json({"room_id": DOCS})
    httpserver.expect_request(f"/_synapse/admin/v1/join/{DOCS}", method="POST") \
        .respond_with_json({"room_id": DOCS})

    accounts.ensure_rooms(
        [{"id": "archivist-bot", "room": "documents"}],
        httpserver.url_for("").rstrip("/"), "simpson", "stackadmin", "pw", ["homer"],
    )

    assert synapse.writes[DOCS]["users"] == {"@stackadmin:simpson": 100, "@homer:simpson": 100}


def test_setup_room_admins_brings_every_family_room_up_to_date(httpserver, tmp_path):
    """What `stack up messages` runs: only the room-admin rule, across the
    Space and every room in it, without the rest of setup."""
    import setup
    synapse = Synapse(httpserver, {
        SPACE: _levels(stackadmin=100, homer=100),
        DOCS: _levels(stackadmin=100),
        PICNIC: _levels(stackadmin=100),
    })
    httpserver.expect_request("/_matrix/client/v3/login", method="POST") \
        .respond_with_json({"access_token": "t", "device_id": "D", "user_id": "@stackadmin:simpson"})
    httpserver.expect_request("/_matrix/client/v3/directory/room/#family:simpson") \
        .respond_with_json({"room_id": SPACE})
    httpserver.expect_request(f"/_matrix/client/v3/rooms/{PICNIC}/state/m.room.name/") \
        .respond_with_json({"name": "Topic: Powerplant Picnic"})
    config = {
        "instance_dir": str(tmp_path),   # the login saves its session here
        "manifest": {"ports": {"synapse": httpserver.port}},
        "stack": {"messages": {"server_name": "simpson"}},
        "secrets": {"global__ADMIN_PASSWORD": "pw"},
        "users": USERS,
    }

    result = setup.run(["--room-admins"], {}, config)

    assert result["ok"]
    assert result["summary"] == [{"ok": True, "line": "homer is now admin in all 3 family rooms "
                                                      "(made admin in: Documents Room, Topic: Powerplant Picnic)"}]
    assert set(synapse.writes) == {DOCS, PICNIC}
    assert all(w["users"]["@homer:simpson"] == 100 for w in synapse.writes.values())


def test_every_run_says_where_each_admin_stands():
    """Also when nothing changed: the admin reads a line, not silence."""
    from _admins import summary
    assert summary(["homer"], 6, []) == [
        {"ok": True, "line": "homer is already admin in all 6 family rooms"}]
    refused = [{"ok": False, "user": "homer", "room": "Old Room", "item": "x", "action": "y"}]
    assert summary(["homer"], 6, refused) == [
        {"ok": False, "line": "homer could not be made admin in: Old Room"}]
