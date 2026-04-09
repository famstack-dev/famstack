"""Post-setup hook for the chatai stacklet.

Runs once after Open WebUI is healthy. Creates accounts for all users
in users.toml. Admin-role users get the admin role in Open WebUI.
"""

import json


def run(ctx):
    secret = ctx.secret
    step = ctx.step

    admin_password = secret("ADMIN_PASSWORD")
    if not admin_password:
        step("No admin password in secrets — skipping account creation")
        return

    from stack.users import TECH_ADMIN_EMAIL, user_id, get_user_password

    base = "http://localhost:42050"

    # Log in as tech admin
    try:
        resp = ctx.http_post(
            f"{base}/api/v1/auths/signin",
            json.dumps({
                "email": TECH_ADMIN_EMAIL,
                "password": admin_password,
            }),
            content_type="application/json",
        )
        token = resp.get("token")
        if not token:
            step("Login succeeded but no token returned")
            return
    except Exception as e:
        step(f"Could not log in as tech admin: {e}")
        return

    auth = {"Authorization": f"Bearer {token}"}

    # Fetch existing users
    try:
        existing = ctx.http_get(f"{base}/api/v1/users/", headers=auth)
        existing_emails = {u["email"].lower() for u in existing}
    except Exception:
        existing_emails = set()

    # Create accounts for all users from users.toml
    users = ctx.users
    if not users:
        return

    for u in users:
        email = u.get("email", "")
        if not email or email.lower() in existing_emails:
            continue

        password = get_user_password(u, ctx.stack.secrets)
        if not password:
            step(f"No password for {user_id(u)} — skipping")
            continue

        is_admin = u.get("role") == "admin"

        try:
            ctx.http_post(
                f"{base}/api/v1/auths/add",
                json.dumps({
                    "name": u.get("name", user_id(u)),
                    "email": email,
                    "password": password,
                    "role": "admin" if is_admin else "user",
                }),
                content_type="application/json",
                headers=auth,
            )
            label = "admin" if is_admin else "user"
            step(f"ChatAI account created: {email} ({label})")
        except Exception as e:
            step(f"Could not create ChatAI account for {user_id(u)}: {e}")
