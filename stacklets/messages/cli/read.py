"""
stack messages read <room> [--limit N] — show a room's recent messages

Prints the last N messages (default 20) in a room, oldest-first, as
`HH:MM  sender: text`. Reads through the Synapse admin API, so it works for
any room without the admin having to be a member — handy for checking what a
bot replied after a capture, or reading back a conversation from the terminal.

Examples:
    stack messages read chat
    stack messages read topic-camping --limit 5
    stack messages read '!abc123:home'          # a room ID works too

The room can be a bare alias ('chat'), a full alias ('#chat:home'), or a room
ID ('!...'). Uses the same server-admin login as `stack messages room`.
"""

HELP = "Show a room's recent messages"

import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import _get  # noqa: E402
from room import _connect  # noqa: E402


def _parse_args(argv):
    """(room, limit, error) from the raw arg list."""
    limit = 20
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--limit":
            if i + 1 >= len(argv):
                return None, None, "--limit needs a number"
            try:
                limit = int(argv[i + 1])
            except ValueError:
                return None, None, f"--limit wants a number, got {argv[i + 1]!r}"
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    if not rest:
        return None, None, "Usage: stack messages read <room> [--limit N]"
    return rest[0], limit, None


def run(args, stacklet, config):
    room, limit, err = _parse_args(args or [])
    if err:
        return {"error": err}

    client, base_url, _ = _connect(config)
    if client is None:
        return {"error": "Can't authenticate as a server admin — is core up?"}

    # A room ID is usable as-is; an alias needs a directory lookup first.
    room_id = room if room.startswith("!") else client.resolve_room(room)
    if not room_id:
        return {"error": f"No such room: {room!r}"}

    # The admin API reads any room without membership and returns newest-first.
    status, resp = _get(
        f"{base_url}/_synapse/admin/v1/rooms/{quote(room_id)}"
        f"/messages?dir=b&limit={limit}",
        token=client.token,
    )
    if status != 200:
        return {"error": f"Couldn't read {room!r}: {resp.get('error', status)}"}

    messages = []
    # Flip to reading order (oldest-first) and keep only chat messages.
    for ev in reversed(resp.get("chunk", [])):
        if ev.get("type") != "m.room.message":
            continue
        sender = ev.get("sender", "?").split(":")[0].lstrip("@")
        body = ev.get("content", {}).get("body", "")
        ts = ev.get("origin_server_ts")
        when = datetime.fromtimestamp(ts / 1000).strftime("%H:%M") if ts else "--:--"
        first, *more = body.split("\n")
        print(f"{when}  {sender}: {first}")
        for line in more:  # indent continuation lines so replies stay readable
            print(f"         {line}")
        messages.append({"sender": sender, "body": body, "ts": ts})

    if not messages:
        print(f"(no messages in {room})")
    return {"ok": True, "room": room, "count": len(messages), "messages": messages}
