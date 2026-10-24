"""
stack messages room — manage Matrix rooms

Subcommands:
    stack messages room list                              List all rooms
    stack messages room create "Name"                     Create room in family Space
    stack messages room create <alias> "Name" ["topic"]   ... with its own alias
    stack messages room delete <alias>                    Delete a room (purges messages)

Examples:
    stack messages room list
    stack messages room create "Topic: Garden"
    stack messages room create groceries "Groceries" "Shopping lists"
    stack messages room delete groceries

How it works:
    All operations use the Synapse admin API, which requires a server admin
    account. The CLI tries the tech admin (stackadmin) first, then falls back
    to admin-role users from secrets.toml. Created rooms are automatically
    added to the family Space so they appear in everyone's sidebar, are
    not encrypted, and the family and the archivist are invited. A name
    or alias that is already taken gives back that room; none is created.
    Stacker's `stack room create <name>` in the Server Room runs this.

    Delete purges all messages — there is no undo. The Synapse v2 delete
    API runs asynchronously; large rooms may take a moment to fully clear.
"""

HELP = "Manage chat rooms (list, create, delete)"

import sys
import unicodedata
import urllib.parse
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import MatrixClient, _get, _api
from _admins import admin_user_ids, ensure_admins
from stack.users import user_id


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


# ── Creating a family room ──────────────────────────────────────────────────
#
# Element X encrypts every room it creates and cannot switch that off, which
# ties the family to their device keys. A room created here is never
# encrypted: the server and the bots are the family's own Mac. It names the
# family Space as its parent and the Space lists it, the family and the
# archivist are invited, and the family's admins are its admins. Asking again
# for a name or alias that is taken gives back that room instead of a second.

# The bot that files what a family room receives. It joins on the invite.
ARCHIVIST = "archivist-bot"


def _alias_for(name):
    """`Topic: Gärten 2027` → `topic-garten-2027`. Empty when the name has
    no letter or digit to make one from."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = "".join(c if c.isalnum() else " " for c in ascii_name.lower()).split()
    return "-".join(words)


def _link(room_id):
    return f"https://matrix.to/#/{room_id}"


def _family_members(users):
    """Who has a chat account: the admins, and members with `messages`.
    The same rule `stack messages setup` creates accounts by."""
    return [user_id(u) for u in users
            if u.get("role") == "admin" or "messages" in u.get("stacklets", [])]


def _room_named(client, name):
    """The room on this server called `name`, ignoring case, or None.
    Asks the admin API, which also sees rooms the tech admin is not in."""
    query = urllib.parse.urlencode({"search_term": name, "limit": 100})
    status, resp = _get(f"{client.base_url}/_synapse/admin/v1/rooms?{query}",
                        token=client.token)
    rooms = resp.get("rooms", []) if status == 200 else []
    return next((r for r in rooms
                 if (r.get("name") or "").casefold() == name.casefold()
                 and r.get("room_type") != "m.space"), None)


def _existing(room_id, name):
    return {"ok": True, "created": False, "room_id": room_id, "name": name,
            "link": _link(room_id)}


def create_family_room(client, name, users, alias=None, topic=None):
    """Create the unencrypted family room `name`, or give back the one that
    has its name or alias. Returns the room and a link to it."""
    alias = alias or _alias_for(name)
    if not alias:
        return {"error": f"'{name}' needs a letter or digit to make the room's address from"}

    if taken := _room_named(client, name):
        return _existing(taken["room_id"], taken["name"])
    if room_id := client.resolve_room(alias):
        return _existing(room_id, client.room_name(room_id))

    space_id = _find_family_space(client, client.base_url)
    room_id = client.create_room(alias, name=name, topic=topic, parent=space_id)
    if not room_id:
        return {"error": f"Failed to create room '{name}'"}
    print(f"\n  Created {name} (#{alias}:{client.server_name})")

    # The Space lists the room, so it appears in everyone's sidebar.
    if space_id:
        client.add_space_child(space_id, room_id)
        print("  Added to family Space")

    for invitee in [*_family_members(users), ARCHIVIST]:
        if not client.invite(room_id, invitee):
            print(f"  Could not invite {invitee}")

    # The creating account is the tech admin; the family's admins would
    # otherwise have no rights in the new room.
    for r in ensure_admins(client, [room_id], admin_user_ids(users), {room_id: name}):
        print(f"  {r['item']}: {r['action']}")
    print()

    return {"ok": True, "created": True, "room_id": room_id, "name": name,
            "alias": client._full_room(alias), "link": _link(room_id)}


def _cmd_create(client, argv, users=()):
    """`create "Name"`, or `create <alias> "Name" ["topic"]`."""
    if not argv:
        return {"error": 'Usage: stack messages room create "Name"  '
                         '(or <alias> "Name" ["topic"])'}
    if len(argv) == 1:
        result = create_family_room(client, argv[0], users)
    else:
        result = create_family_room(client, argv[1], users, alias=argv[0],
                                    topic=argv[2] if len(argv) > 2 else None)
    if not result.get("created") and "error" not in result:
        print(f"\n  {result['name']} already exists: {result['link']}\n")
    return result


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
        print("  All messages and media will be purged. There is no undo.\n")
        try:
            confirm = input(f"  Type '{alias}' to confirm: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Aborted.\n")
            return
        if confirm != alias:
            print("\n  Aborted.\n")
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
    create "Name"                     Create room in family Space
    create <alias> "Name" ["topic"]   ... with its own alias
    delete <alias> [--yes]             Delete a room (asks for confirmation)
"""


def run(args, stacklet, config):
    if not config["is_healthy"]():
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
        return _cmd_create(client, rest, config.get("users", []))
    elif subcmd == "delete":
        return _cmd_delete(client, base_url, server_name, rest)
    else:
        print(USAGE)
        return {"error": f"Unknown subcommand: {subcmd}"}
