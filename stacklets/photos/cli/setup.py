"""
stack photos setup — create Immich accounts from users.toml

Reads users.toml from the repo root, connects to the Immich API, and ensures
every listed user has an account. Idempotent — existing accounts are left
alone.

The first user with role=admin becomes the Immich admin (created via the
one-time admin-sign-up endpoint). All other users are created as regular
accounts through the admin API.

Passwords default to the user's id (e.g. "arthur") unless overridden with a
password field in users.toml. This is intentional — famstack runs on a local
network, not the internet.
"""

HELP = "Create accounts from users.toml"

import json
import ssl
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from stack.users import user_id

# ── HTTP helpers ─────────────────────────────────────────────────────────────
#
# Thin wrappers around urllib so the seed script stays zero-dependency.
# We disable certificate verification — Immich is on localhost or the LAN.

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

def _api(method, url, body=None, token=None):
    """Make an HTTP request to the Immich API and return (status, parsed json).

    Returns (status_code, response_dict) on success or HTTP error.
    Raises on network-level failures so the caller can surface them clearly.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
        except Exception:
            err_body = {"message": e.reason}
        return e.code, err_body

def _get(url, **kw):
    return _api("GET", url, **kw)

def _post(url, body, **kw):
    return _api("POST", url, body=body, **kw)

def _put(url, body, **kw):
    return _api("PUT", url, body=body, **kw)


# ── User loading ─────────────────────────────────────────────────────────────

def _load_users(repo_root):
    """Read users.toml from the repo root.

    Returns a list of user dicts. Validates that at least one admin exists and
    that required fields are present.
    """
    path = Path(repo_root) / "users.toml"
    if not path.exists():
        return None, "users.toml not found. Copy users.toml.example to users.toml and add your family members."
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    users = raw.get("users", [])
    if not users:
        return None, "users.toml has no users defined"
    for u in users:
        if not u.get("name") or not u.get("email"):
            return None, f"User entry missing required fields (name, email): {u}"
    admins = [u for u in users if u.get("role") == "admin"]
    if not admins:
        return None, "users.toml needs at least one user with role = \"admin\""
    return users, None


# ── Seed logic ───────────────────────────────────────────────────────────────

def _seed_immich(base_url, users, secrets):
    """Create accounts in Immich for every user. Returns a result dict.

    The flow:
      1. Check if the server has been initialized (admin exists).
      2. If not, create the admin via the one-time sign-up endpoint.
      3. Log in as admin to get a session token.
      4. Fetch existing users to avoid duplicates.
      5. Create missing user accounts.

    Each step is explicit and logged so the user sees exactly what happened.
    """
    from stack.users import get_admin_password, get_user_password
    results = []

    # ── Step 1: is the server alive? ─────────────────────────────────────

    status, _ = _get(f"{base_url}/api/server/ping")
    if status != 200:
        return {"error": "Can't reach Photos. Make sure it's running with 'stack up photos'."}

    # ── Step 2: ensure tech admin exists ────────────────────────────────
    #
    # The tech admin (stackadmin) is the internal service account used by
    # the CLI. Created via the one-time admin sign-up endpoint. If an admin
    # already exists, Immich returns 400 — that's fine, we skip creation.

    from stack.users import TECH_ADMIN_USERNAME, TECH_ADMIN_EMAIL
    admin_password = get_admin_password(secrets)
    if not admin_password:
        return {"error": "Admin password not found in secrets. Run './stack install' first."}

    status, resp = _post(f"{base_url}/api/auth/admin-sign-up", {
        "email": TECH_ADMIN_EMAIL,
        "password": admin_password,
        "name": TECH_ADMIN_USERNAME,
    })
    if status in (200, 201):
        results.append({"user": TECH_ADMIN_USERNAME, "action": "created (tech admin)", "ok": True})
    elif status == 400 and "already" in resp.get("message", "").lower():
        results.append({"user": TECH_ADMIN_USERNAME, "action": "tech admin already exists", "ok": True})
    else:
        msg = resp.get("message", "unknown error")
        return {"error": f"Could not create the tech admin account: {msg}"}

    # ── Step 3: log in as admin ──────────────────────────────────────────

    status, login = _post(f"{base_url}/api/auth/login", {
        "email": TECH_ADMIN_EMAIL,
        "password": admin_password,
    })
    if status != 201:
        return {
            "error": "Can't log in to Photos as tech admin. Check ADMIN_PASSWORD in .stack/secrets.toml.",
        }
    token = login["accessToken"]

    # ── Step 4: fetch existing users to skip duplicates ──────────────────

    status, existing = _get(f"{base_url}/api/admin/users", token=token)
    if status != 200:
        return {"error": "Failed to list existing Immich users"}
    existing_emails = {u["email"].lower() for u in existing}

    # ── Step 5: create user accounts ───────────────────────────────────
    # The tech admin was created via sign-up above. All users from
    # users.toml (including admin-role users) are created via the admin API.
    # Admin-role users get isAdmin: true so they can manage the library.

    remaining = users
    for u in remaining:
        allowed = u.get("stacklets", [])
        if "photos" not in allowed:
            results.append({"user": user_id(u), "action": "skipped (not in stacklets list)", "ok": True})
            continue

        is_admin_role = u.get("role") == "admin"

        if u["email"].lower() in existing_emails:
            # Promote existing user to admin if their role says so
            if is_admin_role:
                immich_user = next(
                    (eu for eu in existing if eu["email"].lower() == u["email"].lower()), None)
                if immich_user and not immich_user.get("isAdmin"):
                    _put(f"{base_url}/api/admin/users/{immich_user['id']}",
                         {"isAdmin": True}, token=token)
                    results.append({"user": user_id(u), "action": "promoted to admin", "ok": True})
                else:
                    results.append({"user": user_id(u), "action": "already admin", "ok": True})
            else:
                results.append({"user": user_id(u), "action": "already exists", "ok": True})
            continue

        password = get_user_password(u, secrets)
        if not password:
            results.append({"user": user_id(u), "action": "no password in secrets", "ok": False})
            continue

        payload = {
            "email": u["email"],
            "password": password,
            "name": u["name"],
        }
        if is_admin_role:
            payload["isAdmin"] = True

        status, resp = _post(f"{base_url}/api/admin/users", payload, token=token)

        if status in (200, 201):
            action = "created (admin)" if is_admin_role else "created"
            results.append({"user": user_id(u), "action": action, "ok": True})
        else:
            msg = resp.get("message", "unknown error")
            results.append({"user": user_id(u), "action": f"failed: {msg}", "ok": False})

    return {"ok": True, "users": results}


# ── Pretty output ────────────────────────────────────────────────────────────

def _pretty(result):
    """Format the seed result for terminal output."""
    if "error" in result:
        lines = [f"\n  x  {result['error']}"]
        if "hint" in result:
            lines.append(f"     {result['hint']}")
        lines.append("")
        return "\n".join(lines)

    lines = ["\n  Immich accounts:\n"]
    for u in result.get("users", []):
        icon = "+" if "created" in u["action"] else "=" if "exists" in u["action"] else "-"
        lines.append(f"    {icon} {u['user']:16s} {u['action']}")
    lines.append("")
    return "\n".join(lines)


# ── Entry point ──────────────────────────────────────────────────────────────

def run(args, stacklet, config):
    """Called by the main stack CLI when the user runs 'stack photos seed'.

    Loads users.toml, resolves the Immich URL, and creates accounts.
    """
    if not config["is_healthy"]():
        return {"error": "Photos is not running — start it with 'stack up photos'"}

    # Immich listens on its port inside the host network. We always use
    # localhost — seed runs from the host, not from inside a container.
    port = stacklet.get("port", 2283)
    base_url = f"http://localhost:{port}"

    repo_root = config.get("repo_root", ".")
    users, err = _load_users(repo_root)
    if err:
        return {"error": err}

    secrets = config.get("secrets", {})
    result = _seed_immich(base_url, users, secrets)

    # Pretty output when running on a terminal
    if sys.stderr.isatty():
        print(_pretty(result), file=sys.stderr)

    return result
