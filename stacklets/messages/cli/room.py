"""
stack messages room — manage Matrix rooms

Subcommands:
    stack messages room list                              List all rooms
    stack messages room create <alias> "Name" ["topic"]   Create room in family Space
    stack messages room delete <alias>                    Delete a room (purges messages)

Examples:
    stack messages room list
    stack messages room create groceries "Groceries" "Shopping lists"
    stack messages room delete groceries

How it works:
    All operations use the Synapse admin API, which requires a server admin
    account. The CLI tries the tech admin (stackadmin) first, then falls back
    to admin-role users from secrets.toml. Created rooms are automatically
    added to the family Space so they appear in everyone's sidebar.

    Delete purges all messages — there is no undo. The Synapse v2 delete
    API runs asynchronously; large rooms may take a moment to fully clear.
"""

HELP = "Manage chat rooms (list, create, delete)"

import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import MatrixClient, _get, _api


# ── Authentication ──────────────────────────────────────────────────────────
#
# Room management needs a Synapse server admin. We try the tech admin first
# (stackadmin with the global admin password), then iterate over user
# passwords in secrets.toml looking for any account that has admin rights.
# This fallback is necessary during migration when stackadmin may not exist
# yet but a human admin does.

def _login_admin(client, secrets, admin_pass):
    """Try tech admin, then fall back to user passwords from secrets."""
    from stack.users import TECH_ADMIN_USERNAME
    if client.login(TECH_ADMIN_USERNAME, admin_pass):
        return True
    for key, pw in secrets.items():
        if key.startswith("global__USER_") and key.endswith("_PASSWORD"):
            username = key.replace("global__USER_", "").replace("_PASSWORD", "").lower()
            if client.login(username, pw):
                return True
    return False


# ── Space discovery ─────────────────────────────────────────────────────────
#
# When creating a room, we add it to the family Space so it shows up in
# everyone's sidebar automatically. The Space is identified by having
# room_type=m.space and "family" in its name. If no Space exists (e.g.
# fresh install before setup), the room is created standalone.

def _find_family_space(client, base_url):
    """Find the family Space by looking for a room of type m.space."""
    status, resp = _get(
        f"{base_url}/_synapse/admin/v1/rooms?limit=100",
        token=client.token,
    )
    if status != 200:
        return None
    for r in resp.get("rooms", []):
        room_type = r.get("room_type") or ""
        name = (r.get("name") or "").lower()
        if room_type == "m.space" and "family" in name:
            return r["room_id"]
    return None


# ── Connection setup ────────────────────────────────────────────────────────

def _connect(config):
    """Set up and authenticate a Matrix client from CLI config."""
    instance_dir = config.get("instance_dir", config.get("repo_root", "."))
    secrets = config.get("secrets", {})
    stack_cfg = config.get("stack", {})
    server_name = stack_cfg.get("messages", {}).get("server_name", "home")
    admin_pass = secrets.get("global__ADMIN_PASSWORD", "")

    manifest = config.get("manifest", {})
    synapse_port = manifest.get("ports", {}).get("synapse", 42031)
    base_url = f"http://localhost:{synapse_port}"

    client = MatrixClient(base_url, server_name, instance_dir)
    if not _login_admin(client, secrets, admin_pass):
        return None, base_url, server_name
    return client, base_url, server_name


# ── Subcommands ─────────────────────────────────────────────────────────────

def _cmd_list(client, base_url, argv):
    """List all rooms with name, alias, and member count."""
    status, resp = _get(
        f"{base_url}/_synapse/admin/v1/rooms?limit=100",
        token=client.token,
    )
    if status != 200:
        return {"error": f"Failed to list rooms: {resp.get('error', 'unknown')}"}

    rooms = resp.get("rooms", [])
    if not rooms:
        print("\n  No rooms found.\n")
        return

    print()
    for r in rooms:
        name = r.get("name") or "(unnamed)"
        alias = r.get("canonical_alias") or ""
        members = r.get("joined_members", 0)
        print(f"  {name:30s} {alias:35s} {members} members")
    print()


def _cmd_create(client, base_url, server_name, argv):
    """Create a room and add it to the family Space.

    The room is created as a private room with shared history visibility,
    meaning new members can see messages sent before they joined. This
    matches how family rooms should work — no secrets, full context.
    """
    if len(argv) < 2:
        return {"error": 'Usage: stack messages room create <alias> "Name" ["topic"]'}

    alias = argv[0]
    name = argv[1]
    topic = argv[2] if len(argv) > 2 else None

    # Idempotency check — don't create duplicates
    existing = client.resolve_room(alias)
    if existing:
        return {"error": f"Room #{alias}:{server_name} already exists"}

    room_id = client.create_room(alias, name=name, topic=topic)
    if not room_id:
        return {"error": f"Failed to create room '{alias}'"}

    print(f"\n  Created #{alias}:{server_name}")

    # Auto-add to Space so the room appears in everyone's sidebar
    space_id = _find_family_space(client, base_url)
    if space_id:
        client.add_space_child(space_id, room_id)
        print(f"  Added to family Space")

    print()


def _cmd_delete(client, base_url, server_name, argv):
    """Delete a room and purge all messages.

    Uses the Synapse admin v2 delete API which runs asynchronously.
    There is no undo — all messages, media, and state are permanently removed.
    """
    if len(argv) < 1:
        return {"error": "Usage: stack messages room delete <alias>"}

    alias = argv[0]
    force = "--yes" in argv

    room_id = client.resolve_room(alias)
    if not room_id:
        return {"error": f"Room #{alias}:{server_name} not found"}

    # Gate: require explicit confirmation unless --yes is passed
    if not force:
        print(f"\n  This will permanently delete #{alias}:{server_name}")
        print(f"  All messages and media will be purged. There is no undo.\n")
        try:
            confirm = input(f"  Type '{alias}' to confirm: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Aborted.\n")
            return
        if confirm != alias:
            print(f"\n  Aborted.\n")
            return

    status, resp = _api(
        "DELETE",
        f"{base_url}/_synapse/admin/v2/rooms/{room_id}",
        {"purge": True},
        token=client.token,
    )
    if status == 200:
        print(f"\n  Deleted #{alias}:{server_name}\n")
    else:
        return {"error": f"Failed to delete room: {resp.get('error', 'unknown')}"}


# ── CLI entry point ─────────────────────────────────────────────────────────

USAGE = """
  Usage: stack messages room <command>

  Commands:
    list                              List all rooms
    create <alias> "Name" ["topic"]   Create room in family Space
    delete <alias> [--yes]             Delete a room (asks for confirmation)
"""


def run(args, stacklet, config):
    if not stacklet.get("enabled"):
        return {"error": "Messages is not running — start it with 'stack up messages'"}

    argv = sys.argv[3:]  # skip 'stack', 'messages', 'room'
    if not argv:
        print(USAGE)
        return

    subcmd = argv[0]
    rest = argv[1:]

    client, base_url, server_name = _connect(config)
    if not client:
        return {"error": "Could not log in as any admin user"}

    if subcmd == "list":
        return _cmd_list(client, base_url, rest)
    elif subcmd == "create":
        return _cmd_create(client, base_url, server_name, rest)
    elif subcmd == "delete":
        return _cmd_delete(client, base_url, server_name, rest)
    else:
        print(USAGE)
        return {"error": f"Unknown subcommand: {subcmd}"}
