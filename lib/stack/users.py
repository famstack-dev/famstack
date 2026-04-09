from __future__ import annotations

"""User identity and credentials.

Canonical way to resolve usernames and passwords from users.toml
and secrets.toml. All code that needs user info goes through here.
"""

from ._compat import tomllib
from pathlib import Path

# Internal service account — used by the CLI to manage all services.
# Not a real person, never shown in user-facing UIs.
TECH_ADMIN_USERNAME = "stackadmin"
TECH_ADMIN_EMAIL = "stackadmin@home.local"


def load_users(root: Path) -> list[dict]:
    """Load all users from users.toml."""
    path = root / "users.toml"
    if not path.exists():
        return []
    try:
        with open(path, "rb") as f:
            return tomllib.load(f).get("users", [])
    except (tomllib.TOMLDecodeError, OSError):
        return []


def get_admin_user(root: Path) -> dict | None:
    """Load the first admin from users.toml."""
    for u in load_users(root):
        if u.get("role") == "admin":
            return u
    return None


def user_id(user: dict) -> str:
    """Derive a username from a users.toml entry.

    Uses 'id' if explicitly set, otherwise takes the first name lowercased.
    """
    if user.get("id"):
        return user["id"]
    return user["name"].split()[0].lower()


def password_key(user: dict) -> str:
    """Secret key for a user's password. e.g. 'USER_ARTHUR_PASSWORD'."""
    return f"USER_{user_id(user).upper()}_PASSWORD"


def get_admin_password(secrets) -> str | None:
    """Read the admin password from secrets."""
    if isinstance(secrets, dict):
        return secrets.get("global__ADMIN_PASSWORD")
    return secrets.get("global", "ADMIN_PASSWORD")


def get_user_password(user: dict, secrets) -> str | None:
    """Read a user's password from secrets."""
    key = password_key(user)
    if isinstance(secrets, dict):
        return secrets.get(f"global__{key}")
    return secrets.get("global", key)
