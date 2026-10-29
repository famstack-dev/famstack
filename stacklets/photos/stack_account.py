"""The stack's own Immich account, switched on by `[photos] stack_account`.

Immich keeps one library per family member, and an API key reads only its
owner's library. With the switch on, photos setup gives the stack an
account of its own and has every member share their library with it as a
partner. The account's key then finds every member's photos, and core's
photo search reads it as `photos__API_KEY`. A partner share is read-only:
Immich refuses to delete, archive or change a member's photo through it.

The account is not an admin and not the tech admin, because a person who
logs in with the tech admin sees every library, and a key of the tech
admin carries admin rights. Only the stack knows the account's password
(`photos__STACK_ACCOUNT_PASSWORD`) and key (`photos__API_KEY`).

The feature is incubating (workspace decision 0035, 2026-10-29: tried as
an opt-in before it becomes the default). Off is the default, and
switching it off again removes the account and its key. The members'
photos and albums belong to them and are not touched.

Every step is idempotent, because `stack up photos` runs setup on every
start while the switch is on: an account, a share or a key that exists is
left alone. Setup's own `_api` is passed in, so this module does no I/O
of its own choosing.
"""

from __future__ import annotations

import secrets as _random

from stack.stack import stage_warning
from stack.users import get_user_password, user_id

# Found by this address, so a renamed product (`[core] name`) still finds
# the account it made. The spike on the test rig used the same address.
ACCOUNT_EMAIL = "stack-photos@home.local"

# The name of the account's one key, so a key the stack lost track of can
# be found and removed.
KEY_NAME = "stack"

PASSWORD_SECRET = "STACK_ACCOUNT_PASSWORD"
KEY_SECRET = "API_KEY"

# Core renders IMMICH_API_KEY from photos__API_KEY when it starts, so a
# new key reaches its photo search only through a restart of core.
RESTART_CORE = "./stack restart core"


def is_switched_on(stack_config: dict) -> bool:
    """True when stack.toml says `[photos] stack_account = true`."""
    return bool(stack_config.get("photos", {}).get("stack_account", False))


def reconcile(api, base_url, admin_token, immich_users, members, config) -> dict | None:
    """Make Immich match the switch, and answer what setup reports.

    `immich_users` is the admin list setup already fetched, `members` the
    users.toml entries. Answers None when the switch is off and there is
    nothing to remove, so the resting state adds nothing to the answer
    and no request to Immich.
    """
    stack_config = config.get("stack", {})
    held = config.get("secrets", {})
    account = next((u for u in immich_users
                    if u["email"].lower() == ACCOUNT_EMAIL), None)
    if not is_switched_on(stack_config):
        return _remove(api, base_url, admin_token, account, held, config)
    name = stack_config.get("core", {}).get("name") or "stack"
    return _ensure(api, base_url, admin_token, account, members, name, config)


# ── Switched off ─────────────────────────────────────────────────────────

def _remove(api, base_url, admin_token, account, held, config) -> dict | None:
    """Delete the account and forget its password and key.

    `force` deletes the account's own data with it, which is its key and
    the partner shares made with it. The members' libraries are theirs
    and stay. An empty secret reads as no secret everywhere.
    """
    if account is None and not _held(held, PASSWORD_SECRET) and not _held(held, KEY_SECRET):
        return None
    if account is not None:
        status, resp = api("DELETE", f"{base_url}/api/admin/users/{account['id']}",
                           body={"force": True}, token=admin_token)
        if status not in (200, 204):
            return {"error": f"Could not remove the stack's Photos account: "
                             f"{resp.get('message', status)}"}
    config["set_secret"](PASSWORD_SECRET, "")
    config["set_secret"](KEY_SECRET, "")
    return {"account": "removed"}


# ── Switched on ──────────────────────────────────────────────────────────

def _ensure(api, base_url, admin_token, account, members, name, config) -> dict:
    """The account, its key and every member's share, each made once."""
    set_secret = config["set_secret"]
    held = config.get("secrets", {})
    feature = f"The {name} account in Photos"
    report: dict[str, object] = {"account": "exists", "feature": feature,
              "warning": stage_warning("incubating", feature)}

    password = _held(held, PASSWORD_SECRET)
    token = _login(api, base_url, ACCOUNT_EMAIL, password) if account and password else None
    if account and not token:
        # An account the stack cannot log in to: its password was lost
        # with the secrets. Only the stack uses it, so it is made again.
        api("DELETE", f"{base_url}/api/admin/users/{account['id']}",
            body={"force": True}, token=admin_token)
        account = None
        report["account"] = "replaced"
    if account is None:
        account, password = _create(api, base_url, admin_token, name)
        if account is None:
            return {**report, "error": f"Could not create the stack's Photos account: {password}"}
        set_secret(PASSWORD_SECRET, password)
        set_secret(KEY_SECRET, "")
        held = {**held, f"photos__{KEY_SECRET}": ""}
        token = _login(api, base_url, ACCOUNT_EMAIL, password)
        report["account"] = "created" if report["account"] == "exists" else "replaced"

    if not _held(held, KEY_SECRET):
        key = _new_key(api, base_url, token)
        set_secret(KEY_SECRET, key)
        report["next"] = f"Run '{RESTART_CORE}' so core's photo search uses the new key."

    shared, by_hand = _share_libraries(api, base_url, token, account["id"], members, held)
    report["shared"] = shared
    if by_hand:
        report["share_by_hand"] = by_hand
        report["hint"] = (f"{', '.join(by_hand)}: the stack cannot log in with this password. "
                          f"In Immich, open Account Settings > Partner Sharing, "
                          f"and add '{name}' as a partner.")
    return report


def _create(api, base_url, admin_token, name):
    """A new non-admin account with a random password: (account, password),
    or (None, the reason) when Immich refuses."""
    password = _random.token_urlsafe(24)
    status, resp = api("POST", f"{base_url}/api/admin/users", body={
        "email": ACCOUNT_EMAIL, "name": name, "password": password,
        "shouldChangePassword": False,
    }, token=admin_token)
    if status not in (200, 201):
        return None, resp.get("message", status)
    return resp, password


def _new_key(api, base_url, token) -> str:
    """The account's one key, made again: any key of that name goes first,
    so a key the stack lost does not stay valid in Immich."""
    _, keys = api("GET", f"{base_url}/api/api-keys", token=token)
    for key in keys if isinstance(keys, list) else []:
        if key.get("name") == KEY_NAME:
            api("DELETE", f"{base_url}/api/api-keys/{key['id']}", token=token)
    _, resp = api("POST", f"{base_url}/api/api-keys",
                  body={"name": KEY_NAME, "permissions": ["all"]}, token=token)
    return resp["secret"]


def _share_libraries(api, base_url, token, account_id, members, held):
    """Every photos member shares with the account, in its searches.

    A member shares as themselves, so the stack logs in with the password
    it gave them. One who changed it in the app is answered by name, and
    the others are shared regardless. Answers (shared now, by hand).
    """
    shared_with = _partners(api, base_url, token)
    shared, by_hand = [], []
    for member in members:
        if "photos" not in member.get("stacklets", []):
            continue
        if member["email"].lower() in shared_with:
            continue
        member_token = _login(api, base_url, member["email"], get_user_password(member, held))
        if not member_token:
            by_hand.append(user_id(member))
            continue
        api("POST", f"{base_url}/api/partners",
            body={"sharedWithId": account_id}, token=member_token)
        shared.append(user_id(member))
    if shared:
        shared_with = _partners(api, base_url, token)
    for partner in shared_with.values():
        if not partner.get("inTimeline"):
            api("PUT", f"{base_url}/api/partners/{partner['id']}",
                body={"inTimeline": True}, token=token)
    return shared, by_hand


def _partners(api, base_url, token) -> dict:
    """The members who share with the account, by lowercase email."""
    _, partners = api("GET", f"{base_url}/api/partners?direction=shared-with", token=token)
    return {p["email"].lower(): p for p in partners} if isinstance(partners, list) else {}


def _login(api, base_url, email, password) -> str | None:
    if not password:
        return None
    status, resp = api("POST", f"{base_url}/api/auth/login",
                       body={"email": email, "password": password})
    return resp.get("accessToken") if status in (200, 201) else None


def _held(held: dict, name: str) -> str:
    return held.get(f"photos__{name}") or ""
