"""
stack messages join <room> <user> [user2 ...] — join users to a room

Force-joins one or more users to a room using the Synapse admin API.
Users don't need to accept an invite — they're added immediately.
This is useful for onboarding family members or adding bots to rooms.

Examples:
    stack messages join famstack homer marge
    stack messages join documents archivist-bot

How it works:
    Uses the Synapse admin API endpoint POST /_synapse/admin/v1/join/{room_id}
    which bypasses the normal invite flow. Requires a server admin account.
    Bare usernames are expanded to full Matrix IDs automatically
    (e.g. "homer" becomes "@homer:yourserver.name").
"""

HELP = "Join users to a chat room"

import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import MatrixClient


def run(args, stacklet, config):
    if not stacklet.get("enabled"):
        return {"error": "Messages is not running — start it with 'stack up messages'"}

    argv = sys.argv[3:]  # skip 'stack', 'messages', 'join'
    if len(argv) < 2:
        return {"error": "Usage: stack messages join <room> <user> [user2 ...]"}

    room_alias = argv[0]
    usernames = argv[1:]

    # ── Connect and authenticate ────────────────────────────────────────
    instance_dir = config.get("instance_dir", config.get("repo_root", "."))
    stack_cfg = config.get("stack", {})
    secrets = config.get("secrets", {})
    server_name = stack_cfg.get("messages", {}).get("server_name", "home")
    admin_pass = secrets.get("global__ADMIN_PASSWORD", "")

    manifest = config.get("manifest", {})
    synapse_port = manifest.get("ports", {}).get("synapse", 42031)
    base_url = f"http://localhost:{synapse_port}"

    from stack.users import TECH_ADMIN_USERNAME

    client = MatrixClient(base_url, server_name, instance_dir)
    logged_in = client.login(TECH_ADMIN_USERNAME, admin_pass)
    if not logged_in:
        for key, pw in secrets.items():
            if key.startswith("global__USER_") and key.endswith("_PASSWORD"):
                username = key.replace("global__USER_", "").replace("_PASSWORD", "").lower()
                if client.login(username, pw):
                    logged_in = True
                    break
    if not logged_in:
        return {"error": "Could not log in as any admin user"}

    # ── Resolve room and join users ─────────────────────────────────────
    room_id = client.resolve_room(room_alias)
    if not room_id:
        return {"error": f"Room '{room_alias}' not found"}

    print()
    for username in usernames:
        client.join_user(room_id, username)
        print(f"  Joined @{username}:{server_name} to #{room_alias}")
    print()
