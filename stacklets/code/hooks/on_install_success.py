"""Create admin users in Forgejo after first successful start.

Creates the tech admin account (stackadmin) and promotes all admin-role
users from users.toml to Forgejo admins.
"""


def run(ctx):
    from stack.users import (
        TECH_ADMIN_USERNAME, TECH_ADMIN_EMAIL,
        user_id, get_admin_password, get_user_password,
    )

    admin_password = get_admin_password(ctx.stack.secrets)
    if not admin_password:
        ctx.step("Missing admin password in secrets — skipping")
        return

    # ── Tech admin ───────────────────────────────────────────────────
    ctx.step(f"Creating tech admin: {TECH_ADMIN_USERNAME}")
    try:
        ctx.shell(
            f'docker exec --user git stack-code forgejo admin user create '
            f'--username "{TECH_ADMIN_USERNAME}" '
            f'--password "{admin_password}" '
            f'--email "{TECH_ADMIN_EMAIL}" '
            f'--admin'
        )
        ctx.step(f"Tech admin created: {TECH_ADMIN_USERNAME}")
    except RuntimeError as e:
        if "already exists" in str(e).lower():
            ctx.step(f"Tech admin already exists: {TECH_ADMIN_USERNAME}")
        else:
            ctx.warn(f"Could not create tech admin {TECH_ADMIN_USERNAME}: {e}")

    # ── Admin-role users from users.toml ─────────────────────────────
    users = ctx.users or []
    for u in users:
        if u.get("role") != "admin":
            continue
        username = user_id(u)
        email = u.get("email", "")
        password = get_user_password(u, ctx.stack.secrets)
        if not password:
            ctx.step(f"No password for {username} — skipping")
            continue

        ctx.step(f"Creating admin account: {username}")
        try:
            ctx.shell(
                f'docker exec --user git stack-code forgejo admin user create '
                f'--username "{username}" '
                f'--password "{password}" '
                f'--email "{email}" '
                f'--admin'
            )
            ctx.step(f"Admin account created: {username}")
        except RuntimeError as e:
            if "already exists" in str(e).lower():
                ctx.step(f"Admin account already exists: {username}")
            else:
                ctx.warn(f"Could not create admin {username}: {e}")
