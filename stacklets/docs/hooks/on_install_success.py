"""Post-setup hook for the docs stacklet.

Runs once after Paperless-ngx is healthy. Obtains an API token using the
admin credentials and stores it in secrets.toml. Then creates accounts for
admin-role users as superusers via the Paperless API.
"""

import json


def run(ctx):
    env = ctx.env
    secret = ctx.secret
    step = ctx.step
    http_post = ctx.http_post
    http_get = ctx.http_get

    # Verify existing token still works (a previous destroy + up cycle
    # creates a fresh database, invalidating the old token in secrets.toml)
    existing_token = secret("API_TOKEN")
    token_valid = False
    if existing_token:
        try:
            http_get(
                "http://localhost:42020/api/documents/",
                headers={"Authorization": f"Token {existing_token}"},
            )
            token_valid = True
        except Exception:
            step("Stored API token is invalid — obtaining a new one")

    if not token_valid:
        username = env.get("ADMIN_USER", "")
        password = secret("ADMIN_PASSWORD")
        if not username or not password:
            step("No admin credentials — skipping API token")
            return

        step("Obtaining API token...")
        try:
            data = http_post(
                "http://localhost:42020/api/token/",
                f"username={username}&password={password}",
            )
            existing_token = data.get("token")
            if existing_token:
                secret("API_TOKEN", existing_token)
                step("API token saved")
            else:
                step("Unexpected response from Paperless token endpoint")
                return
        except Exception as e:
            step(f"Could not obtain API token: {e}")
            return

    # ── Create admin-role users as superusers ────────────────────────
    _create_admin_users(ctx, existing_token)


def _create_admin_users(ctx, token):
    """Create accounts for admin-role users (beyond the bootstrap admin).

    The bootstrap admin is created by Paperless via PAPERLESS_ADMIN_USER
    env var. Additional users with role=admin get superuser accounts
    via Django's manage.py shell (bypasses password validators so short
    initial passwords like first-name-lowercased work).
    """
    import subprocess
    from stack.users import user_id, get_user_password

    users = ctx.users
    if not users:
        return

    # The tech admin (stackadmin) is created via env vars — only
    # create accounts for real admin-role users from users.toml
    admin_users = [u for u in users if u.get("role") == "admin"]
    if not admin_users:
        return

    for u in admin_users:
        uid = user_id(u)
        email = u.get("email", "")
        password = get_user_password(u, ctx.stack.secrets)
        if not password:
            ctx.step(f"No password for {uid} — skipping Docs account")
            continue

        # create_superuser inside the container bypasses Django password
        # validators. The script is idempotent: existing users are skipped.
        script = (
            "from django.contrib.auth.models import User; "
            f"User.objects.create_superuser('{uid}', '{email}', '{password}') "
            f"if not User.objects.filter(username='{uid}').exists() else None"
        )
        result = subprocess.run(
            ["docker", "exec", "stack-docs-paperless",
             "python3", "/usr/src/paperless/src/manage.py", "shell", "-c", script],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            ctx.step(f"Admin account created in Docs: {uid}")
        else:
            err = (result.stderr or result.stdout).strip().split("\n")[-1]
            ctx.step(f"Could not create Docs admin {uid}: {err}")
