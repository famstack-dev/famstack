"""Create admin users in Forgejo after first successful start.

Creates the tech admin account (stackadmin) and promotes all admin-role
users from users.toml to Forgejo admins.
"""


def _create_admin(ctx, username: str, email: str, password: str) -> None:
    """Create one Forgejo admin account, or report that it exists.

    Runs Forgejo's admin CLI in the container through ctx.run_in_container: the
    password is an argument, and a failed call must not print it.
    Forgejo's CLI refuses to run as root, so it runs as `git`.
    """
    try:
        ctx.run_in_container(["forgejo", "admin", "user", "create",
                  "--username", username, "--password", password,
                  "--email", email, "--admin"], user="git")
        ctx.step(f"Admin account created: {username}")
    except RuntimeError as e:
        # The CLI's last stderr line names the reason, and
        # ctx.run_in_container puts it in the message.
        if "already exists" in str(e).lower():
            ctx.step(f"Admin account already exists: {username}")
        else:
            ctx.warn(f"Could not create admin {username}: {e}")


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
    _create_admin(ctx, TECH_ADMIN_USERNAME, TECH_ADMIN_EMAIL, admin_password)

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
        _create_admin(ctx, username, email, password)
