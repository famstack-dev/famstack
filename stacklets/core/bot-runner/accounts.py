"""Create Matrix accounts and rooms for discovered bots.

Runs on bot-runner startup before any bots are launched. Uses the
Synapse admin API to create/upsert accounts and join bots to their
declared rooms. Idempotent — safe to run on every restart.
"""

import json
import ssl
import urllib.error
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
    """Resolve the Family Space room ID, or None."""
    space_alias = f"#family:{server_name}"
    encoded_alias = urllib.request.quote(space_alias)
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
        status, resp = _api("PUT", f"{base}/_synapse/admin/v2/users/{full_user}", {
            "password": bot_pass,
            "displayname": bot_name,
            "admin": False,
        }, token=token)

        if status in (200, 201):
            logger.info("Account ready: {}", full_user)
        else:
            logger.warning("Account creation failed for {}: {}", bot_id, resp.get("error", "?"))

    return True


def ensure_rooms(bots, homeserver, server_name, admin_user, admin_password,
                 admin_user_ids=None):
    """Ensure bot rooms exist and admin users are joined.

    Always runs on startup, even when bot sessions already exist.
    Creates rooms, joins bots, joins admin-role users.
    """
    # Only process bots that declare a room
    bots_with_rooms = [b for b in bots if b.get("room")]
    if not bots_with_rooms:
        return

    base = homeserver.rstrip("/")
    token = _admin_login(base, admin_user, admin_password)
    if not token:
        return

    space_id = _resolve_space(base, token, server_name)

    for bot in bots_with_rooms:
        bot_id = bot["id"]
        room_alias = bot["room"]

        room_id = _ensure_room(base, token, server_name, room_alias,
                               bot.get("room_topic"), space_id)
        if not room_id:
            continue

        # Join the bot and all admin-role family members
        _join_user(base, token, room_id, f"@{bot_id}:{server_name}")
        for uid in (admin_user_ids or []):
            _join_user(base, token, room_id, f"@{uid}:{server_name}")


def _ensure_room(base, token, server_name, alias, topic, space_id):
    """Create a room or resolve an existing one."""
    full_alias = f"#{alias}:{server_name}"
    encoded = urllib.request.quote(full_alias)

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
