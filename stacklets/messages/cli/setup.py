"""
stack messages setup — bootstrap Matrix with admin, rooms, and family members

Creates the admin account, a family Space, default chat rooms, and accounts
for all users in users.toml. Joins everyone to the Space and its rooms
automatically so they see everything the moment they log in.

Idempotent — safe to run again. Existing users, rooms, and spaces are
skipped. New users.toml entries get accounts and join existing rooms.
"""

HELP = "Bootstrap admin, rooms, and family accounts"

import sys
from pathlib import Path

# Import the shared Matrix client from the same cli/ directory
_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import MatrixClient
sys.path.insert(0, str(_here.parent.parent.parent / "lib"))
from stack.users import user_id, get_admin_password, get_user_password, TECH_ADMIN_USERNAME


def _setup(client, users, config, secrets=None):
    """Run the full bootstrap flow. Returns a result dict."""
    results = []
    secrets = secrets or {}
    server_name = client.server_name

    # ── Step 1: log in as tech admin ────────────────────────────────────
    # The tech admin (stackadmin) is created by on_install_success via
    # register_new_matrix_user. Here we just log in to get a session.

    admin_pass = get_admin_password(secrets)
    if not admin_pass:
        return {"error": "Admin password not found in secrets. Run './stack install' or set global__ADMIN_PASSWORD in .stack/secrets.toml."}

    if not client.login(TECH_ADMIN_USERNAME, admin_pass):
        return {
            "error": "Can't log in to Messages as tech admin.",
            "hint": f"docker exec -it stack-messages-synapse register_new_matrix_user -u {TECH_ADMIN_USERNAME} -p <password> -a -c /data/homeserver.yaml http://localhost:8008",
        }

    results.append({"item": f"@{TECH_ADMIN_USERNAME}:{server_name}", "action": "tech admin"})

    # ── Step 2: create family Space ──────────────────────────────────────

    space_name = config.get("space_name", "Family")
    space_id = client.create_room(
        "family", name=space_name,
        topic="Family chat — rooms, bots, and notifications",
        space=True,
    )
    if space_id:
        results.append({"item": space_name, "action": "Space created"})
    else:
        # Space might already exist — try to find it via admin API
        results.append({"item": space_name, "action": "Space already exists"})

    # ── Step 3: create default rooms ─────────────────────────────────────

    ROOMS = [
        {
            "alias": "famchat",
            "name": "Family Room",
            "topic": "Your family's private space. Share moments, coordinate plans, stay connected.",
            "everyone": True,
        },
        {
            "alias": "memories",
            "name": "Memories",
            "topic": "Your family diary. Voice messages, photos, moments. Stored on your Mac, yours forever.",
            "everyone": True,
        },
        {
            "alias": "famstack",
            "name": "Server Room",
            "topic": "famstack system notifications, service status, and admin alerts.",
            "everyone": False,  # admin only
        },
    ]

    room_ids = {}

    for room in ROOMS:
        alias = room["alias"]
        rid = client.create_room(alias, name=room["name"], topic=room["topic"])
        if rid:
            room_ids[alias] = rid
            if space_id:
                client.add_space_child(space_id, rid)
            results.append({"item": f"#{alias}", "action": "ready"})
        else:
            existing = client.resolve_room(alias)
            if existing:
                room_ids[alias] = existing
                results.append({"item": f"#{alias}", "action": "ready"})
            else:
                results.append({"item": f"#{alias}", "action": "failed to create"})

    # Build lookup: which rooms should everyone join vs admin-only
    everyone_rooms = [room_ids[r["alias"]] for r in ROOMS if r["everyone"] and r["alias"] in room_ids]
    all_rooms = list(room_ids.values())

    # ── Step 4: create user accounts and join them ───────────────────────
    # All users from users.toml get accounts. Admin-role users are
    # created as Synapse admins so they can manage the server.

    remaining = users
    for u in remaining:
        uid = user_id(u)
        is_admin_role = u.get("role") == "admin"
        allowed = u.get("stacklets", [])
        # Admins are created on every stacklet per stack-reference.md —
        # the `stacklets` opt-in list only gates non-admin members.
        if not is_admin_role and "messages" not in allowed:
            results.append({"item": uid, "action": "skipped (not in stacklets list)"})
            continue

        password = get_user_password(u, secrets)
        if not password:
            results.append({"item": uid, "action": "no password in secrets"})
            continue

        created = client.create_user(uid, password, displayname=u.get("name"), admin=is_admin_role)

        if created:
            label = "admin" if is_admin_role else "ready"
            results.append({"item": f"@{uid}:{server_name}", "action": f"{label} (password: {password})"})

            if space_id:
                client.join_user(space_id, uid)
            # Admins join all rooms (including Server Room), members only public ones
            join_rooms = all_rooms if is_admin_role else everyone_rooms
            for rid in join_rooms:
                client.join_user(rid, uid)
        else:
            results.append({"item": uid, "action": "failed to create"})

    # ── Step 5: create stacker-bot and post welcome messages ───────────

    BOT_NAME = "stacker-bot"
    BOT_DISPLAY = "Stacker"
    BOT_SECRET_KEY = "STACKER_BOT_PASSWORD"
    bot_pass = secrets.get(f"messages__{BOT_SECRET_KEY}")
    if not bot_pass:
        import secrets as sec_mod
        bot_pass = sec_mod.token_urlsafe(16)
        # Persist so the password survives re-runs
        from stack.secrets import TomlSecretStore
        store = TomlSecretStore(Path(config.get("instance_dir", config.get("repo_root", "."))) / ".stack" / "secrets.toml")
        store.set("messages", BOT_SECRET_KEY, bot_pass)

    bot_created = client.create_user(BOT_NAME, bot_pass, displayname=BOT_DISPLAY)
    if bot_created:
        results.append({"item": f"@{BOT_NAME}:{server_name}", "action": "ready"})

        # Join bot to Server Room only — Family Room is for humans
        if "famstack" in room_ids:
            client.join_user(room_ids["famstack"], BOT_NAME)

        # Log in as stacker-bot to post welcome messages
        bot_client = MatrixClient(client.base_url, server_name, client.repo_root)
        if bot_client.login(BOT_NAME, bot_pass):
            _post_welcome_messages(bot_client, room_ids, server_name, config)

    return {"ok": True, "results": results}


def _post_welcome_messages(bot, room_ids, server_name, config=None):
    """Post welcome messages from stacker-bot to Server Room."""

    if "famstack" not in room_ids:
        return

    # Read family name for a personal touch
    family = ""
    if config:
        stack_cfg = config.get("stack", {})
        family = stack_cfg.get("core", {}).get("stack_owner", "")

    if family:
        possessive = f"{family}'" if family.endswith("s") else f"{family}s'"
        headline = f"The {possessive} server is live."
    else:
        headline = "Your family server is live."

    plain = (
        f"{headline}\n\n"
        "This is your own famstack. Everything here is yours. Nothing leaves your network.\n\n"
        "This room is your server's control center. "
        "Status updates and alerts will appear here.\n\n"
        "Recommended next step:\n\n"
        "  stack up docs\n\n"
        "Sets up your document archive. "
        "Scan a receipt or letter and it's filed and searchable forever.\n\n"
        "Available commands:\n\n"
        "  stack status         See what's running\n"
        "  stack up photos      Private photo library\n"
        "  stack up docs        Document archive with OCR\n"
        "  stack up ai          Local AI engine\n"
        "  stack up chatai      ChatGPT-like interface\n"
    )

    html = (
        f"<h3>{headline}</h3>"
        "<p>This is your own famstack. Everything here is yours. Nothing leaves your network.</p>"
        "<p>This room is your server's <b>control center</b>. "
        "Status updates and alerts will appear here.</p>"
        "<h4>Recommended next step</h4>"
        "<pre><code>stack up docs</code></pre>"
        "<p>Sets up your document archive. "
        "Scan a receipt or letter and it's filed and searchable forever.</p>"
        "<h4>Available commands</h4>"
        "<table>"
        "<tr><td><code>stack status</code></td><td>See what's running</td></tr>"
        "<tr><td><code>stack up photos</code></td><td>Private photo library</td></tr>"
        "<tr><td><code>stack up docs</code></td><td>Document archive with OCR</td></tr>"
        "<tr><td><code>stack up ai</code></td><td>Local AI engine</td></tr>"
        "<tr><td><code>stack up chatai</code></td><td>ChatGPT-like interface</td></tr>"
        "</table>"
    )

    bot.send("famstack", plain, html=html)

    # ── Memories room welcome ────────────────────────────────────────
    if "memories" in room_ids:
        memories_plain = (
            "This is your family's memory collection.\n\n"
            "Record voice messages with your kids, capture what made today special, "
            "snap a photo at the zoo, keep a holiday diary. "
            "Let the kids talk to their future selves.\n\n"
            "Everything stays on your Mac. Voice messages are transcribed and searchable.\n\n"
            "We made it a habit to press record once or twice a week at the dinner table "
            "and capture a couple of moments. What was funny, what was special, "
            "what the kids want to tell their future selves.\n\n"
            "There's no wrong way to use this. Just start recording."
        )
        memories_html = (
            "<p>This is your family's <b>memory collection</b>.</p>"
            "<p>Record voice messages with your kids, capture what made today special, "
            "snap a photo at the zoo, keep a holiday diary. "
            "Let the kids talk to their future selves.</p>"
            "<p>Everything stays on your Mac. Voice messages are transcribed and searchable.</p>"
            "<p>We made it a habit to press record once or twice a week at the dinner table "
            "and capture a couple of moments. What was funny, what was special, "
            "what the kids want to tell their future selves.</p>"
            "<p><em>There's no wrong way to use this. Just start recording.</em></p>"
        )
        bot.send("memories", memories_plain, html=memories_html)


def _pretty(result):
    """Format the setup result for terminal output."""
    if "error" in result:
        lines = [f"\n  x  {result['error']}"]
        if "hint" in result:
            lines.append(f"     {result['hint']}")
        lines.append("")
        return "\n".join(lines)

    lines = ["\n  Chat setup:\n"]
    for r in result.get("results", []):
        lines.append(f"    {r['item']:30s}  {r['action']}")
    lines.append("")
    return "\n".join(lines)


# ── Entry point ──────────────────────────────────────────────────────────────

def run(args, stacklet, config):
    """Called by the main stack CLI: 'stack messages setup'."""
    instance_dir = config.get("instance_dir", config.get("repo_root", "."))
    stack_cfg = config.get("stack", {})
    server_name = stack_cfg.get("messages", {}).get("server_name", "home")

    # Synapse API port — read from the manifest's [ports] table so it
    # isn't hardcoded here. Falls back to 42031 for backward compatibility.
    manifest = config.get("manifest", {})
    synapse_port = manifest.get("ports", {}).get("synapse", 42031)
    base_url = f"http://localhost:{synapse_port}"

    # Reachability probe — produce a clear error when the user runs
    # setup before messages is actually up. The previous "enabled" check
    # broke when setup.py IS the install — the setup-done marker isn't
    # touched until on_install_success succeeds (i.e. until this runs).
    import socket
    try:
        with socket.create_connection(("localhost", synapse_port), timeout=2):
            pass
    except OSError:
        return {"error": "Messages is not running — start it with 'stack up messages'"}

    client = MatrixClient(base_url, server_name, instance_dir)

    users = config.get("users", [])
    if not users:
        return {"error": "No users found. Add family members to users.toml."}

    secrets = config.get("secrets", {})
    result = _setup(client, users, {"space_name": "Family"}, secrets)

    if sys.stderr.isatty():
        print(_pretty(result), file=sys.stderr)

    return result
