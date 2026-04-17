"""Post-setup hook for the messages stacklet.

Runs once after Synapse is healthy. Creates the tech admin account using
the registration shared secret, then creates family member accounts and
default rooms via setup.py.
"""

import json
import subprocess

from stack.users import TECH_ADMIN_USERNAME


def run(ctx):
    secret = ctx.secret
    step = ctx.step
    http_post = ctx.http_post

    admin_password = secret("ADMIN_PASSWORD")
    reg_secret = secret("REGISTRATION_SECRET")
    server_name = ctx.cfg("server_name", default="home")

    if not admin_password or not reg_secret:
        step("Missing secrets — skipping account creation")
        return

    base = "http://localhost:42031"

    # ── Create tech admin account ────────────────────────────────────
    # Use register_new_matrix_user inside the container — it's the only
    # way to create the first account before any admin exists.
    step("Creating tech admin account...")
    result = subprocess.run(
        ["docker", "exec", "stack-messages-synapse",
         "register_new_matrix_user",
         f"-u={TECH_ADMIN_USERNAME}",
         f"-p={admin_password}",
         "-a",  # admin
         "-c", "/data/homeserver.yaml",
         "http://localhost:8008"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        step(f"Tech admin @{TECH_ADMIN_USERNAME}:{server_name} created")
    elif "User ID already taken" in result.stdout + result.stderr:
        step("Tech admin already exists")
    else:
        step(f"Could not create tech admin: {(result.stderr or result.stdout).strip()}")
        return

    # ── Log in as tech admin ─────────────────────────────────────────
    try:
        login = http_post(
            f"{base}/_matrix/client/v3/login",
            json.dumps({
                "type": "m.login.password",
                "user": TECH_ADMIN_USERNAME,
                "password": admin_password,
            }),
            content_type="application/json",
        )
        token = login.get("access_token")
        if not token:
            step("Login succeeded but no token returned")
            return
    except Exception as e:
        step(f"Could not log in as tech admin: {e}")
        return

    secret("ADMIN_TOKEN", token)

    # ── Rooms, Space, and family member accounts ────────────────────
    # Delegate to setup.py which handles all of this idempotently.
    result = ctx.stack.run_cli_command("messages", "setup")
    if result and result.get("error"):
        # Surface setup.py failures — without this, a broken setup
        # silently succeeds from the framework's POV (marker touched,
        # no accounts created) and bites us at first login.
        step(f"messages setup failed: {result['error']}")
        raise RuntimeError(f"messages setup failed: {result['error']}")
