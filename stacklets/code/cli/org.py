"""
stack code org — manage Forgejo organisations.

Subcommands:
    stack code org list                        List every organisation
    stack code org create <name> [description] Create an organisation
    stack code org members <name>              List members of an org
    stack code org add-member <org> <user>     Add a user to the Owners team

Examples:
    stack code org create family "Family-shared repos"
    stack code org add-member family archivist-bot
    stack code org members family

How it works:
    Forgejo calls go through the site-admin basic-auth path (stackadmin +
    global admin password). The archivist bot provisions the `family` org
    automatically on first publish; these commands are for humans running
    cleanup, inspection, or manual seeding before the bot boots.
"""

HELP = "Manage Forgejo organisations (list, create, members, add-member)"

import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _forgejo import ForgejoClient, ForgejoError


def _connect(config):
    """Build an admin-auth Forgejo client from the CLI config dict."""
    manifest = config.get("manifest", {})
    port = manifest.get("port", 42040)
    url = f"http://localhost:{port}"

    secrets = config.get("secrets", {})
    admin_pass = secrets.get("global__ADMIN_PASSWORD", "")
    if not admin_pass:
        return None, "Missing global__ADMIN_PASSWORD in secrets"
    return ForgejoClient(url=url, admin_user="stackadmin",
                         admin_password=admin_pass), None


# ── Subcommands ─────────────────────────────────────────────────────────────

def _cmd_list(client, argv):
    orgs = client.list_orgs()
    if not orgs:
        print("\n  No organisations.\n")
        return
    print()
    for o in orgs:
        name = o.get("username") or o.get("name") or "?"
        visibility = o.get("visibility", "?")
        print(f"  {name:30s} {visibility}")
    print()


def _cmd_create(client, argv):
    if not argv:
        return {"error": "Usage: stack code org create <name> [description]"}
    name = argv[0]
    description = argv[1] if len(argv) > 1 else ""

    if client.get_org(name):
        return {"error": f"Organisation '{name}' already exists"}
    client.create_org(name, description=description)
    print(f"\n  Created org '{name}'\n")


def _cmd_members(client, argv):
    if not argv:
        return {"error": "Usage: stack code org members <name>"}
    org = argv[0]
    if not client.get_org(org):
        return {"error": f"Organisation '{org}' does not exist"}
    members = client.list_org_members(org)
    if not members:
        print(f"\n  No members in '{org}'.\n")
        return
    print()
    for m in sorted(members):
        print(f"  {m}")
    print()


def _cmd_add_member(client, argv):
    if len(argv) < 2:
        return {"error": "Usage: stack code org add-member <org> <user>"}
    org, user = argv[0], argv[1]
    if not client.get_org(org):
        return {"error": f"Organisation '{org}' does not exist"}
    team_id = client.get_owners_team_id(org)
    client.add_team_member(team_id, user)
    print(f"\n  Added {user} to {org}/Owners\n")


# ── Entry point ─────────────────────────────────────────────────────────────

USAGE = """
  Usage: stack code org <command>

  Commands:
    list                        List organisations
    create <name> [description] Create an organisation
    members <name>              List members of an org
    add-member <org> <user>     Add a user to the Owners team
"""


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Code is not running — start it with 'stack up code'"}

    argv = sys.argv[3:]  # skip 'stack', 'code', 'org'
    if not argv:
        print(USAGE)
        return

    client, err = _connect(config)
    if err:
        return {"error": err}

    subcmd, rest = argv[0], argv[1:]

    try:
        if subcmd == "list":
            return _cmd_list(client, rest)
        elif subcmd == "create":
            return _cmd_create(client, rest)
        elif subcmd == "members":
            return _cmd_members(client, rest)
        elif subcmd == "add-member":
            return _cmd_add_member(client, rest)
        else:
            print(USAGE)
            return {"error": f"Unknown subcommand: {subcmd}"}
    except ForgejoError as e:
        return {"error": f"Forgejo: {e}"}
