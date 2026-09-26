"""
stack messages read <room> [--limit N] [--ids] [--after <event-id>] [--from <user>] [--wait <seconds>] — show a room's recent messages

Prints the last N messages (default 20) in a room, oldest-first, as
`HH:MM  sender: text`. Reads through the Synapse admin API, so it works for
any room without the admin having to be a member — handy for checking what a
bot replied after a capture, or reading back a conversation from the terminal.

`--ids` prints each message's event id underneath it. That is what
`stack messages send --thread <event-id>` takes, so the two together let you
reply in a thread on a message you did not send — the bot's own answer, say.

`--after <event-id>` shows only what came after that message, `--from <user>`
only what one sender posted (`archivist` finds `archivist-bot`), and
`--wait <seconds>` waits up to that long until such a message is there. Together
they answer "what did the archivist say to the message I just sent":

    id=$(stack messages send picnic "Archivist, where do we meet?" --as marge --json | jq -r .event_id)
    stack messages read picnic --after "$id" --from archivist --wait 120

With `--json` each message carries its event id, sender, time, and the thread
or message it answers.

Examples:
    stack messages read chat
    stack messages read topic-camping --limit 5
    stack messages read thread-rig --ids            # ids for --thread
    stack messages read '!abc123:home'          # a room ID works too

The room can be a bare alias ('chat'), a full alias ('#chat:home'), or a room
ID ('!...'). Uses the same server-admin login as `stack messages room`.
"""

HELP = "Show a room's recent messages"

import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import _get  # noqa: E402
from room import _connect  # noqa: E402

# Reading after a message needs that message inside the window read back.
AFTER_WINDOW = 50
# How often a waiting read looks again; a bot answers in seconds, not less.
POLL_SECONDS = 2

USAGE = ("Usage: stack messages read <room> [--limit N] [--ids] "
         "[--after <event-id>] [--from <user>] [--wait <seconds>]")


def _parse_args(argv):
    """(options, error) from the raw arg list."""
    opts = {"room": None, "limit": 20, "ids": False, "after": None, "sender": None, "wait": 0}
    valued = {"--limit": ("limit", int), "--after": ("after", str),
              "--from": ("sender", str), "--wait": ("wait", int)}
    rest = []
    i = 0
    while i < len(argv):
        flag = argv[i]
        if flag in valued:
            key, kind = valued[flag]
            if i + 1 >= len(argv):
                return None, f"{flag} needs a value"
            try:
                opts[key] = kind(argv[i + 1])
            except ValueError:
                return None, f"{flag} wants a number, got {argv[i + 1]!r}"
            i += 2
            continue
        if flag == "--ids":
            opts["ids"] = True
            i += 1
            continue
        rest.append(flag)
        i += 1
    if not rest:
        return None, USAGE
    opts["room"] = rest[0]
    return opts, None


# ── What a message is, and which ones were asked for ─────────────────────

def message_of(event: dict) -> dict:
    """The parts of a room message a reader needs, its relation included."""
    content = event.get("content", {}) or {}
    relates = content.get("m.relates_to") or {}
    thread = relates.get("event_id") if relates.get("rel_type") == "m.thread" else None
    reply_to = None if thread else (relates.get("m.in_reply_to") or {}).get("event_id")
    return {
        "event_id": event.get("event_id", ""),
        "sender": event.get("sender", "?").split(":")[0].lstrip("@"),
        "ts": event.get("origin_server_ts"),
        "msgtype": content.get("msgtype", ""),
        "body": content.get("body", ""),
        "thread": thread,
        "reply_to": reply_to,
    }


def select(messages: list[dict], *, after: str | None, sender: str | None) -> list[dict]:
    """The messages after event `after`, from `sender` (with or without -bot)."""
    if after:
        index = next((i for i, m in enumerate(messages) if m["event_id"] == after), None)
        messages = messages[index + 1:] if index is not None else []
    if sender:
        base = sender.removesuffix("-bot")
        messages = [m for m in messages if m["sender"] in {base, f"{base}-bot"}]
    return messages


def _fetch(client, base_url, room_id, limit):
    status, resp = _get(
        f"{base_url}/_synapse/admin/v1/rooms/{quote(room_id)}"
        f"/messages?dir=b&limit={limit}",
        token=client.token,
    )
    if status != 200:
        return None, resp.get("error", status)
    # Flip to reading order (oldest-first) and keep only chat messages.
    return [message_of(ev) for ev in reversed(resp.get("chunk", []))
            if ev.get("type") == "m.room.message"], None


def _print(messages, show_ids):
    for m in messages:
        when = datetime.fromtimestamp(m["ts"] / 1000).strftime("%H:%M") if m["ts"] else "--:--"
        first, *more = m["body"].split("\n")
        print(f"{when}  {m['sender']}: {first}")
        for line in more:  # indent continuation lines so replies stay readable
            print(f"         {line}")
        if show_ids:
            # The id is what `send --thread` takes, so print it where you can
            # copy it: under the message it belongs to, not in a separate list.
            print(f"         [{m['event_id']}]")


def run(args, stacklet, config):
    opts, err = _parse_args(args or [])
    if opts is None:
        return {"error": err}
    reader, err = _reader(config, opts["room"])
    if reader is None:
        return {"error": err}
    chosen, err = _await(reader, opts)
    if err:
        return {"error": err}
    _show(chosen, opts)
    return {"ok": True, "room": opts["room"], "count": len(chosen), "messages": chosen}


def _reader(config, room):
    """A function reading the room's latest messages, or the reason there is none."""
    client, base_url, _ = _connect(config)
    if client is None:
        return None, "Can't authenticate as a server admin — is core up?"
    # A room ID is usable as-is; an alias needs a directory lookup first.
    room_id = room if room.startswith("!") else client.resolve_room(room)
    if not room_id:
        return None, f"No such room: {room!r}"
    return (lambda limit: _fetch(client, base_url, room_id, limit)), None


def _await(reader, opts):
    """The messages asked for, waiting up to `--wait` seconds for them to appear."""
    limit = max(opts["limit"], AFTER_WINDOW) if opts["after"] else opts["limit"]
    deadline = time.time() + opts["wait"]
    while True:
        messages, err = reader(limit)
        if messages is None:
            return [], f"Couldn't read {opts['room']!r}: {err}"
        chosen = select(messages, after=opts["after"], sender=opts["sender"])
        if chosen or time.time() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    if not chosen and opts["wait"]:
        return [], f"Nothing from {opts['sender'] or 'anyone'} in {opts['room']} within {opts['wait']}s"
    return chosen, None


def _show(messages, opts):
    _print(messages, opts["ids"])
    if not messages:
        print(f"(no messages in {opts['room']})")
