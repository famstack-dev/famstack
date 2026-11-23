"""A stand-in Synapse on localhost, for the unit lane.

`stack messages setup` and the other host commands of the messages
stacklet talk to Synapse over its client and admin API. The unit lane
runs no Synapse, so this answers those calls on a pytest-httpserver:

    synapse = Synapse(httpserver, server="simpson")
    synapse.has_account("homer", "donut-duff-couch-nap")
    ...  # run the command against httpserver.port
    synapse.posted_to("famstack")   # the texts posted into the Server room

It keeps accounts with their passwords, how many times each account's
devices were signed out, the rooms by alias with their members, every
join and power-level write, and every message posted.
Synapse's admin API is an upsert: a `PUT /_synapse/admin/v2/users/<id>`
that carries `password` sets that password and signs out every device of
the account, even when it is the same password. The stand-in does the
same, and answers 201 for a created account and 200 for a changed one.

Every request goes through one handler, so a call nobody expected still
gets a plausible answer instead of a 500.
"""

from __future__ import annotations

import json
import re
import urllib.parse

from werkzeug import Response

# The tech admin's password the stand-in starts with; a test's secrets
# hold the same, as `stack install` leaves them.
ADMIN_PASSWORD = "admin-pass"


class Synapse:
    """Answers the client and admin API calls the messages commands make.

    `passwords` maps a full user id to its password, `logouts` a full user
    id to how many times its devices were signed out, `rooms` an alias to
    its room id, `members` a room id to the full user ids in it, `departed`
    a room id to who left (`"leave"`) or was banned (`"ban"`), `writes`
    holds `(room id, what)` for every admin join and power-level write,
    and `posted` holds `(room id, sender, body)` for every message sent.
    """

    def __init__(self, httpserver, *, server: str = "simpson",
                 admin_password: str = ADMIN_PASSWORD):
        self.server = server
        self.passwords = {f"@stackadmin:{server}": admin_password}
        self.logouts: dict[str, int] = {}
        self.rooms: dict[str, str] = {}
        self.members: dict[str, set[str]] = {}
        self.departed: dict[str, dict[str, str]] = {}
        self.power_levels: dict[str, dict] = {}
        self.writes: list[tuple[str, str]] = []
        self.posted: list[tuple[str, str, str]] = []
        self.account_reads_fail = False
        httpserver.expect_request(re.compile(".*")).respond_with_handler(self._handle)

    def has_account(self, name, password):
        """An existing account, as an earlier setup or the person left it."""
        self.passwords[f"@{name}:{self.server}"] = password

    def has_room(self, alias, *names):
        """An existing room `#<alias>` with these accounts in it, as an
        earlier start left it. The tech admin made it, so it is in it too."""
        rid = f"!{alias}:{self.server}"
        self.rooms[f"#{alias}:{self.server}"] = rid
        self.members[rid] = {f"@stackadmin:{self.server}",
                             *(f"@{n}:{self.server}" for n in names)}

    def left(self, alias, name, membership="leave"):
        """`name` left the room `#<alias>` (`"leave"`) or was banned (`"ban"`)."""
        rid = self.rooms[f"#{alias}:{self.server}"]
        full = f"@{name}:{self.server}"
        self.members[rid].discard(full)
        self.departed.setdefault(rid, {})[full] = membership

    def members_of(self, alias: str) -> set[str]:
        """The names of the accounts in the room `#<alias>`, without the server."""
        room = self.rooms.get(f"#{alias}:{self.server}")
        return {m.split(":")[0].lstrip("@") for m in self.members.get(room, set())}

    def fails_account_reads(self):
        """Every admin read of an account answers 500, as a busy or failing server does.

        A client that times out sees the same: no answer to whether the
        account exists.
        """
        self.account_reads_fail = True

    def posted_to(self, alias: str) -> list[str]:
        """The bodies of the messages posted into the room `#<alias>`."""
        room = self.rooms.get(f"#{alias}:{self.server}")
        return [body for rid, _, body in self.posted if rid == room]

    def _handle(self, request):
        path = urllib.parse.unquote(request.path)
        body = json.loads(request.data) if request.data else {}
        token = request.headers.get("Authorization", "").removeprefix("Bearer tok-")
        if m := re.fullmatch(r"/_synapse/admin/v2/users/(@[^/]+)", path):
            return self._user(request.method, m.group(1), body)
        if path == "/_matrix/client/v3/login":
            full = f"@{body['user']}:{self.server}"
            if self.passwords.get(full) != body["password"]:
                return _json({"errcode": "M_FORBIDDEN"}, 403)
            return _json({"access_token": f"tok-{body['user']}", "device_id": "DEV",
                          "user_id": full})
        if m := re.fullmatch(r"/_matrix/client/v3/directory/room/(#[^/]+)", path):
            rid = self.rooms.get(m.group(1))
            return _json({"room_id": rid}) if rid else _json({"errcode": "M_NOT_FOUND"}, 404)
        if path == "/_matrix/client/v3/createRoom":
            alias = f"#{body['room_alias_name']}:{self.server}"
            if alias in self.rooms:
                return _json({"errcode": "M_UNKNOWN", "error": "Room alias already in use"}, 400)
            self.rooms[alias] = f"!{body['room_alias_name']}:{self.server}"
            self.members[self.rooms[alias]] = {f"@{token}:{self.server}"}
            return _json({"room_id": self.rooms[alias]})
        if m := re.fullmatch(r"/_synapse/admin/v1/join/([^/]+)", path):
            self.members.setdefault(m.group(1), set()).add(body["user_id"])
            self.departed.get(m.group(1), {}).pop(body["user_id"], None)
            self.writes.append((m.group(1), f"join {body['user_id']}"))
            return _json({"room_id": m.group(1)})
        if m := re.fullmatch(r"/_synapse/admin/v1/rooms/([^/]+)/state", path):
            rid = m.group(1)
            held = {**self.departed.get(rid, {}),
                    **dict.fromkeys(self.members.get(rid, set()), "join")}
            return _json({"state": [
                {"type": "m.room.member", "state_key": user, "content": {"membership": how}}
                for user, how in sorted(held.items())]})
        if path == "/_matrix/client/v3/joined_rooms":
            return _json({"joined_rooms": list(self.rooms.values())})
        if re.fullmatch(r"/_matrix/client/v3/rooms/[^/]+/state", path):
            return _json([])
        if m := re.fullmatch(r"/_matrix/client/v3/rooms/([^/]+)/state/m\.room\.power_levels/?", path):
            if request.method == "GET":
                return _json(self.power_levels.get(
                    m.group(1), {"users": {}, "events": {"m.space.child": 0}}))
            self.power_levels[m.group(1)] = body
            self.writes.append((m.group(1), "power levels"))
        if m := re.fullmatch(r"/_matrix/client/v3/rooms/([^/]+)/send/m\.room\.message/[^/]+", path):
            self.posted.append((m.group(1), token, body.get("body", "")))
        return _json({"event_id": "$x"})

    def _user(self, method, full, body):
        exists = full in self.passwords
        if method == "GET":
            if self.account_reads_fail:
                return _json({"errcode": "M_UNKNOWN", "error": "Internal server error"}, 500)
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
