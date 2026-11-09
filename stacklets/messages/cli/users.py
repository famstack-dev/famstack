"""
stack messages users — list all Matrix users

Shows all active (non-deactivated) users on the Synapse server with their
display name and admin status, and the app of each of their sign-ins.
Useful for verifying account setup after migration or checking who has
admin privileges.

With `--json` each user carries `last_seen` (when the account was last
used from any device, in ms, or None) and `devices`, one per sign-in,
read-only:

    {"id": "@homer:simpson", "name": "Homer Simpson", "admin": true,
     "last_seen": 1762700000000,
     "devices": [{"id": "QWERTY", "app": "Element X Android",
                  "kind": "phone", "last_seen": 1762700000000}]}

A device's `kind` is "phone", "computer", "browser" or "" when the chat
server does not say which app it is (`_devices.py`). `devices` is None
when the server did not list them. The setup page's phone step reads
the admin's devices: it is done once one is the phone app.

How it works:
    Uses the Synapse admin API (GET /_synapse/admin/v2/users) which returns
    all registered users, and GET /_synapse/admin/v2/users/<user>/devices
    for each one's sign-ins. Deactivated accounts are filtered out. The output
    is formatted for terminal display; piped output gets JSON from the
    framework's standard serialization.
"""

HELP = "List all chat users"


from ._devices import device
from ._matrix import MatrixClient


def run(args, stacklet, config):
    """`{"users": [...]}`, each user with its sign-ins, or `{"error": ...}`
    while the chat does not run or no admin can sign in. Reads only."""
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
    # everyone in" reads it (`--json`). `devices` names each sign-in's app,
    # which the setup's phone step reads.
    users = [{"id": u.get("name", ""), "name": u.get("displayname") or "",
              "admin": bool(u.get("admin")), "last_seen": u.get("last_seen_ts"),
              "devices": _devices(client, u.get("name", ""))}
             for u in client.list_users()]
    if not users:
        print("\n  No users found.\n")
        return {"users": []}

    print()
    for u in users:
        admin = " (admin)" if u["admin"] else ""
        apps = ", ".join(sorted({d["app"] for d in u["devices"] or [] if d["app"]}))
        print(f"  {u['id']:35s} {u['name']:20s}{admin}  {apps}".rstrip())
    print()
    return {"users": users}


def _devices(client, user_id: str) -> list[dict] | None:
    """One account's sign-ins (`_devices.device`), or None when the
    server did not list them."""
    raw = client.list_devices(user_id)
    return None if raw is None else [device(d) for d in raw]
