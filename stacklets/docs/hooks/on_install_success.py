"""Post-setup hook for the docs stacklet.

Runs once after Paperless-ngx is healthy:
1. Obtains an API token and stores it in secrets.toml
2. Creates admin-role user accounts as superusers
3. Seeds person tags and document taxonomy

Also seeded on every `stack up docs` via on_start_ready.py
so they stay in sync with users.toml and taxonomy.yaml changes.
The token comes from `auth.ensure_api_token`, which both hooks share,
so an instance that loses one does not have to wait for a reinstall.
"""

import sys
from pathlib import Path

# seed.py and auth.py live one level up from hooks/
sys.path.insert(0, str(Path(__file__).parent.parent))
from auth import ensure_api_token
from seed import seed_person_tags, seed_taxonomy

def run(ctx):
    token = ensure_api_token(ctx)
    if not token:
        return

    # ── Create admin-role users as superusers ────────────────────────
    _create_admin_users(ctx, token)

    # ── Seed person tags + category taxonomy ───────────────────────────
    _seed_taxonomy(ctx, token)


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


def _seed_taxonomy(ctx, token):
    """Seed person tags and document taxonomy. See seed.py for details."""
    url = ctx.env.get("PAPERLESS_URL", "http://localhost:42020")
    seed_person_tags(url, token, ctx.users, step=ctx.step)
    language = ctx.env.get("LANGUAGE", "en")
    seed_taxonomy(url, token, language, step=ctx.step)
