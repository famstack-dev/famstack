"""
stack messages users — list all Matrix users

Shows all active (non-deactivated) users on the Synapse server with their
display name and admin status. Useful for verifying account setup after
migration or checking who has admin privileges.

How it works:
    Uses the Synapse admin API (GET /_synapse/admin/v2/users) which returns
    all registered users. Deactivated accounts are filtered out. The output
    is formatted for terminal display; piped output gets JSON from the
    framework's standard serialization.
"""

HELP = "List all chat users"

import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import MatrixClient


def run(args, stacklet, config):
    if not stacklet.get("enabled"):
        return {"error": "Messages is not running — start it with 'stack up messages'"}

    # ── Connect and authenticate ────────────────────────────────────────
    repo_root = config.get("repo_root", ".")
    stack_cfg = config.get("stack", {})
    secrets = config.get("secrets", {})
    server_name = stack_cfg.get("messages", {}).get("server_name", "home")
    admin_pass = secrets.get("global__ADMIN_PASSWORD", "")

    manifest = config.get("manifest", {})
    synapse_port = manifest.get("ports", {}).get("synapse", 42031)
    base_url = f"http://localhost:{synapse_port}"

    from stack.users import TECH_ADMIN_USERNAME

    client = MatrixClient(base_url, server_name, repo_root)
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

    # ── Fetch and display ───────────────────────────────────────────────
    users = client.list_users()
    if not users:
        print("\n  No users found.\n")
        return

    print()
    for u in users:
        name = u.get("name", "")
        display = u.get("displayname", "")
        admin = " (admin)" if u.get("admin") else ""
        print(f"  {name:35s} {display:20s}{admin}")
    print()
