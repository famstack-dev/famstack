"""
stack messages react <room> <event-id> <emoji> [--as <user>] — react to a message

Adds a reaction to a message, as stacker-bot or, with `--as <user>`, as a
family member. Reactions are how the family tells the archivist what to do
with one message: 📌 keeps it, 📎 archives its source, 🔁 retries a filing.
The event id comes from `stack messages read --ids` or from the send that
posted the message.

Examples:
    stack messages react picnic '$abc...' 📌 --as marge
"""

HELP = "React to a message in a chat room"

import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import MatrixClient, resolve_login


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Messages is not running — start it with 'stack up messages'"}

    argv = list(args or [])
    sender = None
    if "--as" in argv:
        i = argv.index("--as")
        if i + 1 >= len(argv):
            return {"error": "--as needs a username"}
        sender = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 3:
        return {"error": "Usage: stack messages react <room> <event-id> <emoji> [--as <user>]"}
    room, event_id, emoji = argv

    username, password, err = resolve_login(sender, config.get("secrets", {}))
    if err:
        return {"error": err}
    server_name = config.get("stack", {}).get("messages", {}).get("server_name", "home")
    synapse_port = config.get("manifest", {}).get("ports", {}).get("synapse", 42031)
    client = MatrixClient(f"http://localhost:{synapse_port}", server_name,
                          config.get("instance_dir", config.get("repo_root", ".")))
    if not client.login(username, password):
        return {"error": f"{username} can't log in — check the password in secrets."}

    ok, detail = client.react(room, event_id, emoji)
    if not ok:
        return {"error": f"Failed to react: {detail}"}
    print(f"  Reacted {emoji} in {room}: {detail}")
    return {"ok": True, "room": room, "event_id": detail, "reacted_to": event_id}
