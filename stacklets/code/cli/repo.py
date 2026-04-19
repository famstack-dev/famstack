"""
stack code repo — manage Forgejo repositories.

Subcommands:
    stack code repo list [owner]             List repos (optionally one owner)
    stack code repo create <owner> <name>    Create a repo (user or org)
    stack code repo show <owner> <name>      Print repo metadata

Examples:
    stack code repo list family
    stack code repo create family brain "Family wiki"
    stack code repo show family documents

How it works:
    Uses the site-admin API with basic auth. `create` figures out whether
    the owner is a user or an org and hits the right endpoint. The
    `family/documents` repo is normally provisioned by the archivist bot;
    these commands cover the cases where a human wants to inspect, seed,
    or recover manually.
"""

HELP = "Manage Forgejo repositories (list, create, show)"

import sys

from stack.forgejo import ForgejoClient, ForgejoError


def _connect(config):
    manifest = config.get("manifest", {})
    port = manifest.get("port", 42040)
    url = f"http://localhost:{port}"

    secrets = config.get("secrets", {})
    admin_pass = secrets.get("global__ADMIN_PASSWORD", "")
    if not admin_pass:
        return None, "Missing global__ADMIN_PASSWORD in secrets"
    return ForgejoClient(url=url, admin_user="stackadmin",
                         admin_password=admin_pass), None


def _cmd_list(client, argv):
    owner = argv[0] if argv else None
    repos = client.list_repos(owner=owner)
    if not repos:
        print("\n  No repositories.\n")
        return
    print()
    for r in repos:
        full = r.get("full_name") or f"{r.get('owner', {}).get('login', '?')}/{r.get('name', '?')}"
        visibility = "private" if r.get("private") else "public"
        desc = r.get("description") or ""
        print(f"  {full:40s} {visibility:8s} {desc}")
    print()


def _cmd_create(client, argv):
    if len(argv) < 2:
        return {"error": 'Usage: stack code repo create <owner> <name> [description]'}
    owner, name = argv[0], argv[1]
    description = argv[2] if len(argv) > 2 else ""

    if client.get_repo(owner, name):
        return {"error": f"Repository {owner}/{name} already exists"}

    org = client.get_org(owner)
    owner_is_org = org is not None
    client.create_repo(owner, name, description=description,
                       private=True, owner_is_org=owner_is_org)
    kind = "org" if owner_is_org else "user"
    print(f"\n  Created {owner}/{name} (owner is a {kind})\n")


def _cmd_show(client, argv):
    if len(argv) < 2:
        return {"error": "Usage: stack code repo show <owner> <name>"}
    owner, name = argv[0], argv[1]
    repo = client.get_repo(owner, name)
    if not repo:
        return {"error": f"Repository {owner}/{name} not found"}

    # Compact, human-scannable — not a JSON dump
    fields = [
        ("full_name", repo.get("full_name")),
        ("description", repo.get("description") or "(none)"),
        ("private", repo.get("private")),
        ("default_branch", repo.get("default_branch")),
        ("size (kB)", repo.get("size")),
        ("html_url", repo.get("html_url")),
        ("clone_url", repo.get("clone_url")),
    ]
    print()
    for k, v in fields:
        print(f"  {k:<16s} {v}")
    print()


# ── Entry point ─────────────────────────────────────────────────────────────

USAGE = """
  Usage: stack code repo <command>

  Commands:
    list [owner]                          List repos (optionally one owner)
    create <owner> <name> [description]   Create a repo (auto-detects user vs org)
    show <owner> <name>                   Print repo metadata
"""


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Code is not running — start it with 'stack up code'"}

    argv = sys.argv[3:]
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
        elif subcmd == "show":
            return _cmd_show(client, rest)
        else:
            print(USAGE)
            return {"error": f"Unknown subcommand: {subcmd}"}
    except ForgejoError as e:
        return {"error": f"Forgejo: {e}"}
