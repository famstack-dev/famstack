"""The stack's own Immich account, switched on by `[photos] stack_account`.

Immich keeps one library per family member, and an API key reads only its
owner's library. With the switch on, `stack photos setup --stack-account`
(which `stack up photos` runs) gives the stack an account of its own, and
every family member with an Immich account shares their library with it as
a partner. The account's key then finds every member's photos, and core's
photo search reads it as `photos__API_KEY`. A partner share is read-only:
Immich refuses to delete, archive or change a member's photo through it.

The account is not an admin and not the tech admin, because a person who
logs in with the tech admin sees every library, and a key of the tech
admin carries admin rights. Only the stack knows the account's password
(`photos__STACK_ACCOUNT_PASSWORD`) and key (`photos__API_KEY`). The
password is also what says the stack made the account: without it,
switching off removes nothing and a key in the secrets is not the stack's.

Each member is shared once. The stack remembers whom it shared in
`.stack/photos-stack-account.json`, so a member who removes the account
from their partners stays removed.

The feature is incubating (workspace decision 0035, 2026-10-29: tried as
an opt-in before it becomes the default). Off is the default and asks
nothing of Immich. Switching it off again removes the account and its key;
the members' photos and albums belong to them and are not touched.

Every step is idempotent, because `stack up photos` runs it on every start
while the switch is on. Every refused request stops the run with a
`Refused` that names it.
"""

from __future__ import annotations

import json
import secrets as _random
from pathlib import Path
from typing import Any

from stack.stack import stage_warning
from stack.users import get_user_password, user_id

from . import immich

# Found by this address, so a renamed product (`[core] name`) still finds
# the account it made. The spike on the test rig used the same address.
ACCOUNT_EMAIL = "stack-photos@home.local"

# The name of the account's key in Immich, so a key the stack lost track
# of can be found and removed.
KEY_NAME = "stack"

PASSWORD_SECRET = "STACK_ACCOUNT_PASSWORD"
KEY_SECRET = "API_KEY"

# The famstack "a" the wiki shows as its icon, so the family recognises the
# account in their partner list. One copy in the repo, read as a file.
PROFILE_IMAGE = Path("stacklets/memory/quartz/static/icon.png")

# Whom the stack shared, under the instance's `.stack/`.
SHARED_STATE = "photos-stack-account.json"

# Core renders IMMICH_API_KEY from photos__API_KEY when it starts, so a
# new key reaches its photo search only through a restart of core.
RESTART_CORE = "Run './stack restart core' so core's photo search uses the new key."


class Refused(Exception):
    """Immich refused a step; the message says which and why."""


def is_switched_on(stack_config: dict) -> bool:
    """True for `[photos] stack_account = true`, or the string "true".
    Anything else, "false" and "False" included, is off."""
    value = stack_config.get("photos", {}).get("stack_account")
    return value is True or value == "true"


def is_wanted(stack_config: dict, held: dict) -> bool:
    """Whether a start has anything to do: the switch is on, or it is off
    and the account the stack made is still there to remove."""
    return is_switched_on(stack_config) or bool(_held(held, PASSWORD_SECRET))


def reconcile(base_url, admin_token, members, config) -> dict:
    """Make Immich match the switch, and answer what the run did.

    `members` are the users.toml entries; `config` is the command's config
    (`stack`, `secrets`, `set_secret`, `instance_dir`, `repo_root`). A
    refused step answers `{"error": ...}` with what was done before it.
    """
    report: dict = {}
    try:
        if is_switched_on(config.get("stack", {})):
            _ensure(base_url, admin_token, members, config, report)
        else:
            _remove(base_url, admin_token, config, report)
    except Refused as e:
        report["error"] = str(e)
    return report


# ── Switched off ─────────────────────────────────────────────────────────

def _remove(base_url, admin_token, config, report):
    """Delete the account the stack made and forget its password and key.

    `force` deletes the account's own data with it: its key and the
    partner shares made with it. An empty secret reads as no secret.
    """
    if not _held(config.get("secrets", {}), PASSWORD_SECRET):
        return
    account = _find_account(_immich_users(base_url, admin_token))
    if account:
        _check(immich.request("DELETE", f"{base_url}/api/admin/users/{account['id']}",
                              {"force": True}, token=admin_token), "remove the account")
    config["set_secret"](PASSWORD_SECRET, "")
    config["set_secret"](KEY_SECRET, "")
    _state_path(config).unlink(missing_ok=True)
    report["account"] = "removed" if account else "already gone"


# ── Switched on ──────────────────────────────────────────────────────────

def _ensure(base_url, admin_token, members, config, report):
    """The account, its key, its picture and every member's share."""
    users = _immich_users(base_url, admin_token)
    account = _find_account(users)
    name = account["name"] if account else _product_name(config)
    feature = f"The {name} account in Photos"
    report.update(name=name, feature=feature, warning=stage_warning("incubating", feature))
    stack_made_key = bool(_held(config.get("secrets", {}), PASSWORD_SECRET))
    account, token = _account(base_url, admin_token, account, name, config, report)
    try:
        _key(base_url, token, config, stack_made_key, report)
        _profile_image(base_url, token, config, report)
        _share_libraries(base_url, token, account, members, users, config, report)
    finally:
        immich.logout(base_url, token)


def _account(base_url, admin_token, account, name, config, report):
    """The account and a session of it: (account, token).

    Made when missing. When the stack holds no password for it, or Immich
    answers 401 to the one it holds, the admin sets a new one: that keeps
    the members' shares and the albums shared with the account.
    """
    password = _held(config.get("secrets", {}), PASSWORD_SECRET)
    report["account"] = "exists" if account else "not created"
    if account is None:
        password = _random.token_urlsafe(24)
        account = _check(immich.request("POST", f"{base_url}/api/admin/users", {
            "email": ACCOUNT_EMAIL, "name": name, "password": password,
            "shouldChangePassword": False}, token=admin_token), "create the account")
        config["set_secret"](PASSWORD_SECRET, password)
        _save_shared(config, set())
        report["account"] = "created"
    status, token = immich.login(base_url, ACCOUNT_EMAIL, password) if password else (401, None)
    if status == 401:
        password = _random.token_urlsafe(24)
        _check(immich.request("PUT", f"{base_url}/api/admin/users/{account['id']}",
                              {"password": password}, token=admin_token),
               "set the account's password")
        config["set_secret"](PASSWORD_SECRET, password)
        report["account"] = "password reset"
        status, token = immich.login(base_url, ACCOUNT_EMAIL, password)
    if not token:
        raise Refused(f"Could not log in as the stack's account (HTTP {status}).")
    return account, token


def _key(base_url, token, config, stack_made_key, report):
    """Keep a working key the stack made; otherwise make a new one."""
    held = _held(config.get("secrets", {}), KEY_SECRET)
    if held and stack_made_key:
        status, me = immich.request("GET", f"{base_url}/api/users/me", api_key=held)
        if status == 200 and me.get("email", "").lower() == ACCOUNT_EMAIL:
            report["key"] = "kept"
            return
        if status not in (200, 401):
            raise Refused(f"Could not check the stack's key (HTTP {status}).")
    config["set_secret"](KEY_SECRET, _new_key(base_url, token))
    report["key"] = ("replaced a key the stack did not make" if held and not stack_made_key
                     else "replaced" if held else "created")
    report["next"] = RESTART_CORE


def _new_key(base_url, token) -> str:
    """The account's one key, made again: a key of that name goes first,
    so a key the stack lost does not stay valid in Immich."""
    keys = _check(immich.request("GET", f"{base_url}/api/api-keys", token=token),
                  "list the account's keys")
    for key in keys:
        if key.get("name") == KEY_NAME:
            _check(immich.request("DELETE", f"{base_url}/api/api-keys/{key['id']}", token=token),
                   "remove the account's old key")
    made = _check(immich.request("POST", f"{base_url}/api/api-keys",
                                 {"name": KEY_NAME, "permissions": ["all"]}, token=token),
                  "create the account's key")
    return made["secret"]


def _profile_image(base_url, token, config, report):
    """Upload the icon once: an account with a picture keeps it."""
    me = _check(immich.request("GET", f"{base_url}/api/users/me", token=token),
                "read the account")
    if me.get("profileImagePath"):
        return
    icon = (Path(config["repo_root"]) / PROFILE_IMAGE).read_bytes()
    _check(immich.upload(f"{base_url}/api/users/profile-image", "file", "famstack.png",
                         icon, "image/png", token=token), "set the account's picture")
    report["profile_image"] = "set"


# ── Members' libraries ───────────────────────────────────────────────────

def _share_libraries(base_url, token, account, members, users, config, report):
    """Every member with an Immich account shares once, in the searches.

    A member shares as themselves, with the password the stack gave them.
    One who changed it is named for sharing by hand; one who removed the
    account from their partners is left alone; one without an Immich
    account is named as such. inTimeline is set only for members.
    """
    has_account = {u["email"].lower() for u in users}
    partners = _partners(base_url, token)
    shared = _load_shared(config) | (partners.keys() & _emails(members))
    outcome: dict[str, list] = {"shared": [], "by_hand": [], "removed_by_member": [],
                                "no_immich_account": []}
    for member in members:
        email = member["email"].lower()
        if email not in has_account:
            outcome["no_immich_account"].append(user_id(member))
        elif email in shared:
            if email not in partners:
                outcome["removed_by_member"].append(user_id(member))
        elif _share(base_url, member, account["id"], config):
            outcome["shared"].append(user_id(member))
            shared.add(email)
        else:
            outcome["by_hand"].append(user_id(member))
    _save_shared(config, shared)
    if outcome["shared"]:
        partners = _partners(base_url, token)
    _show_in_searches(base_url, token, partners, _emails(members))
    report.update({k: v for k, v in outcome.items() if v})
    if outcome["by_hand"]:
        report["hint"] = (f"{', '.join(outcome['by_hand'])}: the stack cannot log in as "
                          f"them. In Immich, open Account Settings > Partner Sharing, "
                          f"and add '{account['name']}' as a partner.")


def _share(base_url, member, account_id, config) -> bool:
    """Share `member`'s library with the account, logged in as them.
    False when Immich refuses their password."""
    password = get_user_password(member, config.get("secrets", {}))
    status, token = immich.login(base_url, member["email"], password) if password else (401, None)
    if status == 401:
        return False
    if not token:
        raise Refused(f"Could not log in as {user_id(member)} (HTTP {status}).")
    try:
        _check(immich.request("POST", f"{base_url}/api/partners",
                              {"sharedWithId": account_id}, token=token),
               f"share {user_id(member)}'s library")
    finally:
        immich.logout(base_url, token)
    return True


def _show_in_searches(base_url, token, partners, member_emails):
    """Put every member's share into the account's searches. A partner
    who is not in users.toml is left as they are."""
    for email, partner in partners.items():
        if email in member_emails and not partner.get("inTimeline"):
            _check(immich.request("PUT", f"{base_url}/api/partners/{partner['id']}",
                                  {"inTimeline": True}, token=token),
                   "show a member's photos in the account's searches")


def _partners(base_url, token) -> dict:
    """The members who share with the account, by lowercase email."""
    partners = _check(immich.request("GET", f"{base_url}/api/partners?direction=shared-with",
                                     token=token), "list the account's partners")
    return {p["email"].lower(): p for p in partners}


# ── Small pieces ─────────────────────────────────────────────────────────

def _immich_users(base_url, admin_token) -> list:
    return _check(immich.request("GET", f"{base_url}/api/admin/users", token=admin_token),
                  "list the Immich accounts")


def _find_account(users) -> dict | None:
    return next((u for u in users if u["email"].lower() == ACCOUNT_EMAIL), None)


def _product_name(config) -> str:
    """`[core] name`, as `Stack.product_name` reads it. A command gets
    stack.toml, not the Stack."""
    return config.get("stack", {}).get("core", {}).get("name") or "stack"


def _emails(members) -> set:
    return {m["email"].lower() for m in members}


def _check(answer, step) -> Any:
    """The answer's body when Immich accepted `step`; Refused otherwise."""
    status, body = answer
    if status not in (200, 201, 204):
        raise Refused(f"Could not {step}: {body.get('message', status)} (HTTP {status}).")
    return body


def _held(held: dict, name: str) -> str:
    """The photos secret `name` as the stack holds it, "" when it holds
    none. An emptied secret counts as none."""
    return held.get(f"photos__{name}") or ""


def _state_path(config) -> Path:
    return Path(config["instance_dir"]) / ".stack" / SHARED_STATE


def _load_shared(config) -> set:
    """The emails of the members the stack has shared, lowercase."""
    path = _state_path(config)
    return set(json.loads(path.read_text())["shared"]) if path.exists() else set()


def _save_shared(config, shared: set) -> None:
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"shared": sorted(shared)}, indent=2) + "\n")
