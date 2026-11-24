"""Create Matrix accounts and rooms for discovered bots.

Runs on bot-runner startup before any bots are launched. Uses the
Synapse admin API to create accounts, create each bot's declared room
and join the bot and its people to it. A bot's `room_members` says who
those are: `"parents"` joins the admins and the parents (the Documents
Room), anything else the admins only (the Server Room). Anyone else, a
child for example, joins when an admin invites them.

Idempotent, so it is safe on every restart: people are only added, never
removed, so an invited member stays, and a room whose members are
already right gets no new event. A parent who left a room for the
parents, or was banned from it, stays out: only the bot and the admins
are joined again.
"""

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

from loguru import logger

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


def _api(method, url, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
        except Exception:
            err_body = {"error": e.reason}
        return e.code, err_body
    except Exception as e:
        return 0, {"error": str(e)}


def _admin_login(base, admin_user, admin_password):
    """Log in as admin, return access token or None."""
    status, resp = _api("POST", f"{base}/_matrix/client/v3/login", {
        "type": "m.login.password",
        "user": admin_user,
        "password": admin_password,
    })
    if status != 200:
        logger.warning("Admin login failed ({}): {}", status, resp.get("error", "?"))
        return None
    return resp["access_token"]


def _resolve_space(base, token, server_name):
    """Resolve the space's room ID, or None."""
    space_alias = f"#family:{server_name}"
    encoded_alias = urllib.parse.quote(space_alias)
    status, resp = _api("GET", f"{base}/_matrix/client/v3/directory/room/{encoded_alias}", token=token)
    return resp.get("room_id") if status == 200 else None


def setup_bot_accounts(bots, homeserver, server_name, admin_user, admin_password):
    """Create Matrix accounts for bots. Room setup is handled by ensure_rooms.

    Returns True if accounts were set up, False if Matrix is unavailable.
    """
    base = homeserver.rstrip("/")
    token = _admin_login(base, admin_user, admin_password)
    if not token:
        return False

    for bot in bots:
        bot_id = bot["id"]
        bot_name = bot.get("name", bot_id)
        bot_pass = bot.get("password", "")
        if not bot_pass:
            logger.warning("Bot {} has no password, skipping", bot_id)
            continue

        full_user = f"@{bot_id}:{server_name}"
        # Only set the password when *creating* the account. Re-sending it to an
        # existing account resets the password, which soft-logs-out that bot's
        # live session (e.g. the agent's nanobot, which holds its own token) and
        # makes it go deaf on M_UNKNOWN_TOKEN. Provision-only bots have no session
        # file to skip them here, so this ran every startup. Create with a
        # password; leave an existing account's password (and tokens) untouched.
        # Only a 404 says the account is missing. Any other answer (a 5xx, a
        # timeout) leaves it unknown, so nothing is sent for this bot.
        existing, resp = _api("GET", f"{base}/_synapse/admin/v2/users/{full_user}", token=token)
        body = {"displayname": bot_name, "admin": False}
        if existing == 404:
            body["password"] = bot_pass
        elif existing != 200:
            logger.warning("Account check failed for {}, nothing changed ({}): {}",
                           bot_id, existing, resp.get("error", "?"))
            continue
        status, resp = _api("PUT", f"{base}/_synapse/admin/v2/users/{full_user}", body, token=token)

        if status in (200, 201):
            logger.info("Account ready: {}", full_user)
        else:
            logger.warning("Account creation failed for {}: {}", bot_id, resp.get("error", "?"))

    return True


def ensure_rooms(bots, homeserver, server_name, admin_user, admin_password,
                 admin_user_ids=None, parent_user_ids=None):
    """Ensure bot rooms exist and their people are in them.

    Always runs on startup, even when bot sessions already exist. Creates
    each bot's room, joins the bot, and joins the people its declaration
    names: the admins and every user in `parent_user_ids` for
    `room_members = "parents"`, otherwise the admins only. Admins are made
    admins of the room.

    It only adds: nobody is removed, and a person already in the room is
    not joined again, so a restart with the right members writes nothing.
    A member who left the room or was banned from it is not joined again,
    because that was their choice or an admin's. The bot and the admins
    are, as the room does not work without them.
    """
    # Only process bots that declare a room
    bots_with_rooms = [b for b in bots if b.get("room") or b.get("join_rooms")]
    if not bots_with_rooms:
        return

    base = homeserver.rstrip("/")
    token = _admin_login(base, admin_user, admin_password)
    if not token:
        return

    space_id = _resolve_space(base, token, server_name)
    admins = [f"@{uid}:{server_name}" for uid in admin_user_ids or []]
    parents = [f"@{uid}:{server_name}" for uid in parent_user_ids or []]

    for bot in bots_with_rooms:
        bot_user = f"@{bot['id']}:{server_name}"
        room_alias = bot.get("room")

        # Rooms another stacklet owns, which the bot takes part in:
        # joined when they exist, never created here, and nobody else is
        # joined to them.
        for alias in bot.get("join_rooms") or []:
            existing = _resolve_room(base, token, server_name, alias)
            if existing:
                _join_missing(base, token, existing, [bot_user])

        if not room_alias:
            continue
        room_id = _ensure_room(base, token, server_name, room_alias,
                               bot.get("room_topic"), space_id)
        if not room_id:
            continue

        people = parents if bot.get("room_members") == "parents" else []
        _join_missing(base, token, room_id, [bot_user, *admins],
                      [p for p in people if p not in admins])
        # The tech admin created the room and is otherwise the only
        # account with power there.
        for admin in admins:
            _ensure_admin_power(base, token, room_id, admin)


def _join_missing(base, token, room_id, required, optional=()):
    """Force-join each user who is not in the room yet.

    `required` users are joined whatever their membership. `optional`
    users are joined only when they never were in the room: one who left
    or was banned stays out.

    The memberships are read first, because a join of a member who is
    already in the room can still write a membership event. When the
    read fails, every required user is joined (a join that may repeat is
    better than a bot or an admin left out) and no optional one, as
    nothing says whether they left.
    """
    memberships = _memberships(base, token, room_id)
    for user in dict.fromkeys(required):
        if memberships is None or memberships.get(user) != "join":
            _join_user(base, token, room_id, user)
    if memberships is None:
        return
    for user in dict.fromkeys(optional):
        if user not in memberships:
            _join_user(base, token, room_id, user)


def _memberships(base, token, room_id):
    """Each user's membership in the room (`"join"`, `"leave"`, `"ban"`,
    `"invite"`), from its state; None when the state cannot be read."""
    status, resp = _api("GET", f"{base}/_synapse/admin/v1/rooms/{room_id}/state", token=token)
    if status != 200:
        return None
    return {e["state_key"]: (e.get("content") or {}).get("membership")
            for e in resp.get("state") or [] if e.get("type") == "m.room.member"}


def _ensure_admin_power(base, token, room_id, full_user):
    """Give a user power level 100 in a room, writing only when it differs.
    The same rule `stack messages setup --room-admins` applies to every room the stack creates."""
    path = f"{base}/_matrix/client/v3/rooms/{room_id}/state/m.room.power_levels/"
    status, body = _api("GET", path, token=token)
    levels: dict = dict(body) if status == 200 else {}
    users: dict = dict(levels.get("users") or {})
    if status != 200 or users.get(full_user) == 100:
        return
    status, resp = _api("PUT", path, {**levels, "users": {**users, full_user: 100}}, token=token)
    if status != 200:
        logger.warning("Could not make {} an admin of {}: {}", full_user, room_id, resp.get("error", "?"))


def _resolve_room(base, token, server_name, alias):
    """The id of the room with this alias, or None if there is none."""
    encoded = urllib.parse.quote(f"#{alias}:{server_name}")
    status, resp = _api("GET", f"{base}/_matrix/client/v3/directory/room/{encoded}", token=token)
    return resp.get("room_id") if status == 200 else None


def _ensure_room(base, token, server_name, alias, topic, space_id):
    """Create a room or resolve an existing one."""
    full_alias = f"#{alias}:{server_name}"
    encoded = urllib.parse.quote(full_alias)

    # Check if room exists
    status, resp = _api("GET", f"{base}/_matrix/client/v3/directory/room/{encoded}", token=token)
    if status == 200:
        return resp["room_id"]

    # Create it
    body = {
        "name": alias.replace("-", " ").title() + " Room",
        "room_alias_name": alias,
        "preset": "private_chat",
        "initial_state": [{
            "type": "m.room.history_visibility",
            "content": {"history_visibility": "shared"},
        }],
    }
    if topic:
        body["topic"] = topic

    status, resp = _api("POST", f"{base}/_matrix/client/v3/createRoom", body, token=token)
    if status == 200:
        room_id = resp["room_id"]
        logger.info("Room created: #{}", alias)
        if space_id:
            _api("PUT",
                 f"{base}/_matrix/client/v3/rooms/{space_id}/state/m.space.child/{room_id}",
                 {"via": [server_name], "suggested": True}, token=token)
        return room_id

    logger.warning("Room creation failed for #{}: {}", alias, resp.get("error", "?"))
    return None


def _join_user(base, token, room_id, full_user):
    """Force-join a user to a room via the Synapse admin API."""
    _api("POST", f"{base}/_synapse/admin/v1/join/{room_id}",
         {"user_id": full_user}, token=token)
