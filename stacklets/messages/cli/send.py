"""
stack messages send <room> "message" [--as <user>] [--mention <user>] [--thread <event-id>] [--reply-to <event-id>]
stack messages send --to <person> "message" [--as <user>] ...

Sends a text message to a room, or with `--to <person>` to that person's
private chat with the sender: the room whose only members are the two of
them. When there is none, nothing is sent and the command fails, so a
scheduled run shows up as failed. The stack does not create rooms here.

By default it posts as stacker-bot (the system account). `--as agent`
posts as the agent, `--as <id>-bot` as that bot, and `--as <user>` as a
family member, the text counterpart to `stack messages upload --as`. The
room can be a bare alias (e.g. 'chat') or a full Matrix room ID.

An unencrypted room gets the message over plain HTTP, from the host. An
encrypted room gets it encrypted, from a device of the sending account's
own, `<account> (command line)`, which runs in the bot runner (core must
be up) and keeps its keys there next to the bot's. It signs itself with
the account's cross-signing keys when the stack holds them (the bots), so
apps show it as verified; a person's command-line device stays unverified.
Two sends as one account take turns on its key store.

Pass `--mention <user>` (repeatable) to notify a user via the Matrix
`m.mentions` payload. A bot in a group room answers when it is mentioned this
way, so `--mention agent` is how you get the agent to reply — putting "@Stacky"
in the text alone does not, because the bot keys on the mention payload, not
the display name.

Pass `--thread <event-id>` to post inside a thread, the way a client's "Reply
in thread" does. The event id comes back from the send that started the thread,
so a whole threaded conversation can be driven from the terminal. The agent
treats a thread it is part of as addressed to it, so this is also how you check
that a follow-up needs no mention at all.

Pass `--reply-to <event-id>` to answer one message without starting a
thread, the way a client's "Reply" does. For the archivist, a reply to its
filing is a correction.

The id of the message sent is printed, and returned with `--json`: it is what
`--thread`, `--reply-to` and `stack messages read --after` take.

This is the building block other stacklets use for notifications:
  - photos could notify #notifications when a backup completes
  - docs could notify when a new document is archived
  - core could notify on update events

The plain HTTP session is persisted in .stack/messages/session-<user>.toml so
repeated sends don't create new devices or require re-authentication.
"""

HELP = "Send a message to a chat room or a person's private chat"

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

from stack import bot_runner

from ._matrix import Account, MatrixClient, resolve_login

_USAGE = ('Usage: stack messages send <room> "message" | --to <person> "message" '
          '[--as <user>] [--mention <user>] [--thread <event-id>] [--reply-to <event-id>]')

# The flags that take a value, and what the value is, for the error when it is missing.
_VALUE_FLAGS = {
    "--as": "a username",
    "--to": "a username",
    "--mention": "a username",
    "--thread": "an event id",
    "--reply-to": "an event id",
}

# Where the bot runner mounts the stack's data dir (stacklets/core/docker-compose.yml).
_BOT_RUNNER_DATA = "/data"


def _simple_markdown_to_html(text):
    """`text` as the HTML a bot would send for it, for the markdown it uses most.

    The bots convert with `stack.matrix.html.matrix_html`, which needs the
    `markdown` package; the host has the standard library only. This is the
    part of it a message from the command line uses: paragraphs whose
    newlines stay breaks, `- ` lists, **bold** and `code`. For those it
    gives what `matrix_html(text, line_breaks=True)` gives, and a test
    holds it to that. Returns None for text with no formatting, which is
    sent as plain text.
    """
    if not any(c in text for c in ("**", "- ", "`", "\n")):
        return None
    blocks = [b for b in _BLANK_LINES.split(text.strip()) if b.strip()]
    return "\n".join(_html_block(b.strip().split("\n")) for b in blocks)


_BLANK_LINES = re.compile(r"\n\s*\n")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`(.+?)`")


def _html_block(lines):
    """One block of lines: a list when every line is an item, else a paragraph."""
    if all(ln.strip().startswith("- ") for ln in lines):
        items = "".join(f"<li>{_inline(ln.strip()[2:])}</li>\n" for ln in lines)
        return f"<ul>\n{items}</ul>"
    return "<p>" + "<br />\n".join(_inline(ln.strip()) for ln in lines) + "</p>"


def _inline(text):
    return _CODE.sub(r"<code>\1</code>", _BOLD.sub(r"<strong>\1</strong>", text))


# ── The request ──────────────────────────────────────────────────────────


@dataclass
class Request:
    """One send, as the command line asked for it."""
    message: str
    room: str | None = None
    to: str | None = None
    sender: str | None = None
    mentions: list[str] = field(default_factory=list)
    thread_root: str | None = None
    reply_to: str | None = None


def parse_request(argv):
    """`(request, error)` from the words after `stack messages send`, one of them None.

    With `--to` every other word is the message; without it the first is the room.
    """
    values = {flag: [] for flag in _VALUE_FLAGS}
    rest = []
    i = 0
    while i < len(argv):
        flag = argv[i]
        if flag not in _VALUE_FLAGS:
            rest.append(flag)
            i += 1
            continue
        if i + 1 >= len(argv):
            return None, f"{flag} needs {_VALUE_FLAGS[flag]}"
        values[flag].append(argv[i + 1])
        i += 2

    def last(flag):
        return values[flag][-1] if values[flag] else None

    to = last("--to")
    room = None if to else (rest.pop(0) if rest else None)
    if not rest:
        return None, _USAGE
    return Request(message=" ".join(rest), room=room, to=to, sender=last("--as"),
                   mentions=values["--mention"], thread_root=last("--thread"),
                   reply_to=last("--reply-to")), None


# ── The room ─────────────────────────────────────────────────────────────


def _target_room(client, request, account):
    """`(room id, error)` for the request's room or person, one of them None."""
    if request.to:
        room_id = client.private_chat(request.to)
        if not room_id:
            return None, f"{request.to.capitalize()} has no private chat with {account.label} yet."
        return room_id, None
    room = request.room or ""
    # Best-effort join so a family member not yet in the room can still post.
    client.join(room)
    room_id = room if room.startswith("!") else client.resolve_room(room)
    if not room_id:
        return None, f"Room '{room}' not found"
    return room_id, None


# ── The encrypted path ───────────────────────────────────────────────────


def _state_dir(account):
    """Where the account's command-line device keeps its state in the bot runner.

    A bot's next to the bot's own device, so it finds the account's
    cross-signing keys; a person's in the messages data dir.
    """
    kind = "cli" if account.is_person else "bot"
    return f"{_BOT_RUNNER_DATA}/{account.stacklet}/{kind}"


def _send_encrypted(account: Account, user_id, room_id, content):
    """Send through `stack.matrix.send` in the bot runner. Returns (ok, event id or error).

    The request, password included, goes over stdin, so it is in no
    process's arguments.
    """
    if not bot_runner.bot_runner_running():
        return False, "The room is encrypted, and sending into it needs core: start it with 'stack up core'."
    request = json.dumps({"user_id": user_id, "password": account.password,
                          "state_dir": _state_dir(account), "room_id": room_id,
                          "content": content})
    try:
        result = bot_runner.run_module("stack.matrix.send", request)
    except subprocess.TimeoutExpired:
        return False, "the encrypted send timed out"
    except FileNotFoundError:
        return False, "docker CLI not found on this host"
    lines = result.stdout.strip().splitlines()
    try:
        answer = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        answer = {}
    if answer.get("event_id"):
        return True, answer["event_id"]
    reason = answer.get("error") or (result.stderr.strip().splitlines() or ["no answer"])[-1]
    return False, reason


# ── The command ──────────────────────────────────────────────────────────


def run(args, stacklet, config):
    """Called by the main stack CLI: 'stack messages send <room> "message"'.

    The room and message come from positional args that argparse doesn't
    know about — we pull them from sys.argv directly since the stacklet
    CLI plugin system passes the full args namespace.
    """
    if not config["is_healthy"]():
        return {"error": "Messages is not running — start it with 'stack up messages'"}

    # sys.argv: ['stack', 'messages', 'send', ...]
    request, err = parse_request(sys.argv[3:])
    if request is None:
        return {"error": err}

    stack_cfg = config.get("stack", {})
    account, err = resolve_login(request.sender, config.get("secrets", {}), stack_cfg)
    if account is None:
        return {"error": err}

    client = _client(config)
    if not client.login(account.login, account.password):
        return {"error": f"{account.login} can't log in: check the password in secrets."}

    room_id, err = _target_room(client, request, account)
    if room_id is None:
        return {"error": err}

    content = client.text_content(
        request.message, html=_simple_markdown_to_html(request.message),
        mentions=request.mentions, thread_root=request.thread_root, reply_to=request.reply_to)
    if client.is_encrypted(room_id):
        ok, detail = _send_encrypted(account, client.user_id, room_id, content)
    else:
        ok, detail = client.send_content(room_id, content)
    if not ok:
        return {"error": f"Failed to send: {detail}"}

    # The id is what `--thread`, `--reply-to` and `read --after` take.
    target = request.room or request.to
    print(f"  Sent to {target}: {detail}")
    return {"ok": True, "room": target, "room_id": room_id, "event_id": detail}


def _client(config):
    """A client for the stack's Synapse, as the host reaches it."""
    server_name = config.get("stack", {}).get("messages", {}).get("server_name", "home")
    synapse_port = config.get("manifest", {}).get("ports", {}).get("synapse", 42031)
    instance_dir = config.get("instance_dir", config.get("repo_root", "."))
    return MatrixClient(f"http://localhost:{synapse_port}", server_name, instance_dir)
