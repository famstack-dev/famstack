"""Running `stack messages setup` again keeps each person's chat password.

The setup runs again whenever the admin adds a person to the family, so a
rerun is a normal step. It creates the accounts that are missing, with the
password the stack stored for them, and leaves every existing account
alone: its password and its signed-in devices stay as they are. The output
lists a password only for an account this run created.

Synapse's admin API is an upsert: a `PUT /_synapse/admin/v2/users/<id>`
that carries `password` sets that password and signs out every device of
the account, even when it is the same password. So "leave it alone" means
the PUT for an existing person carries no password.

Synapse is stood in on localhost by pytest-httpserver (`Synapse` below).
It keeps accounts, passwords and devices, answers the admin API with 201
for a created account and 200 for a changed one, as Synapse does, and signs
out an account's devices on every password it is sent. The command and its
Matrix client are the real ones.
"""

from __future__ import annotations

import json
import re
import urllib.parse

import pytest
from werkzeug import Response

from stacklets.messages.cli import setup

SERVER = "simpson"
ADMIN_PASSWORD = "admin-pass"
BOT_PASSWORD = "stacker-pass"

FAMILY = [
    {"id": "homer", "name": "Homer", "role": "admin"},
    {"id": "marge", "name": "Marge", "role": "member", "stacklets": ["messages"]},
]
STORED = {
    "global__ADMIN_PASSWORD": ADMIN_PASSWORD,
    "core__STACKER_BOT_PASSWORD": BOT_PASSWORD,
    "global__USER_HOMER_PASSWORD": "donut-duff-couch-nap",
    "global__USER_MARGE_PASSWORD": "blue-hair-pork-chop",
}


# ── The stand-in Synapse ─────────────────────────────────────────────────

class Synapse:
    """Answers the client and admin API calls the setup makes.

    Holds `passwords` (full user id to password), `logouts` (full user id
    to how many times its devices were signed out) and the rooms by alias.
    Every request goes through one handler, so a call nobody expected still
    gets a plausible answer instead of a 500.
    """

    def __init__(self, httpserver):
        self.passwords = {f"@stackadmin:{SERVER}": ADMIN_PASSWORD}
        self.logouts: dict[str, int] = {}
        self.rooms: dict[str, str] = {}
        httpserver.expect_request(re.compile(".*")).respond_with_handler(self._handle)

    def has_account(self, name, password):
        """An existing account, as an earlier setup or the person left it."""
        self.passwords[f"@{name}:{SERVER}"] = password

    def _handle(self, request):
        path = urllib.parse.unquote(request.path)
        body = json.loads(request.data) if request.data else {}
        if m := re.fullmatch(r"/_synapse/admin/v2/users/(@[^/]+)", path):
            return self._user(request.method, m.group(1), body)
        if path == "/_matrix/client/v3/login":
            full = f"@{body['user']}:{SERVER}"
            if self.passwords.get(full) != body["password"]:
                return _json({"errcode": "M_FORBIDDEN"}, 403)
            return _json({"access_token": f"tok-{body['user']}", "device_id": "DEV",
                          "user_id": full})
        if m := re.fullmatch(r"/_matrix/client/v3/directory/room/(#[^/]+)", path):
            rid = self.rooms.get(m.group(1))
            return _json({"room_id": rid}) if rid else _json({"errcode": "M_NOT_FOUND"}, 404)
        if path == "/_matrix/client/v3/createRoom":
            alias = f"#{body['room_alias_name']}:{SERVER}"
            if alias in self.rooms:
                return _json({"errcode": "M_UNKNOWN", "error": "Room alias already in use"}, 400)
            self.rooms[alias] = f"!{body['room_alias_name']}:{SERVER}"
            return _json({"room_id": self.rooms[alias]})
        if path == "/_matrix/client/v3/joined_rooms":
            return _json({"joined_rooms": list(self.rooms.values())})
        if re.fullmatch(r"/_matrix/client/v3/rooms/[^/]+/state", path):
            return _json([])
        if "/state/m.room.power_levels" in path and request.method == "GET":
            return _json({"users": {}, "events": {"m.space.child": 0}})
        return _json({"event_id": "$x"})

    def _user(self, method, full, body):
        exists = full in self.passwords
        if method == "GET":
            return _json({"name": full}) if exists else _json({"errcode": "M_NOT_FOUND"}, 404)
        if "password" in body:
            if exists:
                self.logouts[full] = self.logouts.get(full, 0) + 1
            self.passwords[full] = body["password"]
        elif not exists:
            self.passwords[full] = None
        return _json({"name": full}, 200 if exists else 201)


def _json(body, status=200):
    return Response(json.dumps(body), status=status, content_type="application/json")


# ── Running the command ──────────────────────────────────────────────────

@pytest.fixture
def synapse(httpserver):
    """A stand-in Synapse with only the tech admin's account."""
    return Synapse(httpserver)


def _setup_again(httpserver, tmp_path, users=FAMILY):
    """Run `stack messages setup` against the stand-in; its result rows by item."""
    config = {
        "instance_dir": str(tmp_path),
        "stack": {"messages": {"server_name": SERVER}},
        "manifest": {"ports": {"synapse": httpserver.port}},
        "users": users,
        "secrets": dict(STORED),
    }
    result = setup.run([], None, config)
    assert result.get("ok"), result
    return {row["item"]: row["action"] for row in result["results"]}


# ── Behaviour ────────────────────────────────────────────────────────────

def test_an_existing_account_keeps_its_password_and_devices(httpserver, synapse, tmp_path):
    """Given Homer's account exists, a rerun sends him no password and says it exists."""
    synapse.has_account("homer", STORED["global__USER_HOMER_PASSWORD"])

    rows = _setup_again(httpserver, tmp_path)

    assert synapse.passwords["@homer:simpson"] == STORED["global__USER_HOMER_PASSWORD"]
    assert "@homer:simpson" not in synapse.logouts, "Homer's devices were signed out"
    assert "exists" in rows["@homer:simpson"]
    assert STORED["global__USER_HOMER_PASSWORD"] not in rows["@homer:simpson"]


def test_a_password_changed_in_element_is_kept(httpserver, synapse, tmp_path):
    """Given Homer chose his own password in Element, a rerun keeps it."""
    synapse.has_account("homer", "mmm-forbidden-donut")

    _setup_again(httpserver, tmp_path)

    assert synapse.passwords["@homer:simpson"] == "mmm-forbidden-donut"
    assert not synapse.logouts


def test_a_new_person_gets_the_stored_password_listed_once(httpserver, synapse, tmp_path):
    """Given Marge was added since the last run, she gets her stored password, the only one shown."""
    synapse.has_account("homer", STORED["global__USER_HOMER_PASSWORD"])

    rows = _setup_again(httpserver, tmp_path)

    marge = STORED["global__USER_MARGE_PASSWORD"]
    assert synapse.passwords["@marge:simpson"] == marge
    assert marge in rows["@marge:simpson"]
    listed = [action for action in rows.values() if "password" in action]
    assert listed == [rows["@marge:simpson"]], "only the account made in this run shows a password"


def test_the_bot_and_the_rooms_are_as_before(httpserver, synapse, tmp_path):
    """Resting state: the bot keeps its session and the four rooms are there, none made twice."""
    synapse.has_account("homer", STORED["global__USER_HOMER_PASSWORD"])
    synapse.has_account("stacker-bot", BOT_PASSWORD)

    rows = _setup_again(httpserver, tmp_path)

    assert rows["@stacker-bot:simpson"] == "ready"
    assert "@stacker-bot:simpson" not in synapse.logouts
    assert set(synapse.rooms) == {f"#{a}:simpson" for a in ("family", "famchat", "memories", "famstack")}
