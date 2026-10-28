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


from ._matrix import MatrixClient


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Messages is not running — start it with 'stack up messages'"}

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

    # ── Fetch and display ───────────────────────────────────────────────
    # `last_seen` is when the account was last used from a device, in ms,
    # or None for one nobody has signed in to yet: the dashboard's "Bring
    # everyone in" reads it (`--json`).
    users = [{"id": u.get("name", ""), "name": u.get("displayname") or "",
              "admin": bool(u.get("admin")), "last_seen": u.get("last_seen_ts")}
             for u in client.list_users()]
    if not users:
        print("\n  No users found.\n")
        return {"users": []}

    print()
    for u in users:
        admin = " (admin)" if u["admin"] else ""
        print(f"  {u['id']:35s} {u['name']:20s}{admin}")
    print()
    return {"users": users}
