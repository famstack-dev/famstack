"""User identity and credentials.

Canonical way to resolve usernames and passwords from users.toml
and secrets.toml. All code that needs user info goes through here.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

# Internal service account — used by the CLI to manage all services.
# Not a real person, never shown in user-facing UIs.
TECH_ADMIN_USERNAME = "stackadmin"
TECH_ADMIN_EMAIL = "stackadmin@home.local"


# ── Naming the household ──────────────────────────────────────────────
#
# stack.toml's [core] stack_owner is the surname the installer asked
# for. It reaches the family on the installer's closing line and in the
# title of their wiki, so both go through here and spell it the same.
#
# "Family name" gets answered two ways: one person types "Simpson", the
# next types "Simpsons". Both mean the same household, and only one of
# them needs an s adding.


def family_plural(owner: str | None) -> str:
    """The surname as you would address the whole household: "Simpsons".

    Returns "" when no owner is configured, so callers can fall back to
    something generic rather than render a name with a hole in it.
    Instances predating stack_owner still run, so that is a live path
    rather than a hypothetical.
    """
    name = (owner or "").strip()
    if not name:
        return ""
    return name if name.lower().endswith("s") else name + "s"


def family_display_name(owner: str | None) -> str:
    """The household as it appears on screen: "The Simpsons".

    Empty when no owner is configured. See `family_plural`.
    """
    plural = family_plural(owner)
    return f"The {plural}" if plural else ""


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


def has_account_on(user: dict, stacklet_id: str) -> bool:
    """Whether this users.toml entry gets an account on a stacklet.

    An admin gets one on every stacklet. A member gets one only on the
    stacklets their `stacklets` list names, so a member without
    `"messages"` has no chat account and is joined to no room.
    """
    return user.get("role") == "admin" or stacklet_id in user.get("stacklets", [])


def user_ids_with_role(users: list[dict], role: str) -> str:
    """The comma-separated user IDs of the entries with this role,
    as core renders them into env: `"homer"` for role `"admin"`."""
    return ",".join(user_id(u) for u in users if u.get("role") == role)


def parent_user_ids(users: list[dict]) -> str:
    """The comma-separated user IDs of the parents with a chat account:
    the entries whose `relation` is `"parent"` and that have an account on
    messages (`has_account_on`), such as `"homer,marge"`. A parent without
    a chat account cannot be joined to a room, so they are left out."""
    return ",".join(user_id(u) for u in users
                    if u.get("relation") == "parent" and has_account_on(u, "messages"))


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
