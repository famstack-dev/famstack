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
(`photos__STACK_ACCOUNT_PASSWORD`) and key (`photos__API_KEY`). The stack
never sets a password on an account: one at the stack's email whose
password the stack does not hold, or whose password Immich refuses, is
refused, because setting one would lock its owner out (decision 0036).

Whose key `photos__API_KEY` holds is asked of Immich (`whose_key`): the
account's, another user's (set by hand, which moves to the import key on
switch-on and stays on switch-off), or one Immich no longer accepts. On
switch-off the account's key deletes itself, so nothing else is needed.

Each member is shared once. The stack remembers whom it shared, by Immich
user id, in `.stack/photos-stack-account.json`, so a member who removes
the account from their partners stays removed.

The feature is incubating (workspace decision 0035, 2026-10-29: tried as
an opt-in before it becomes the default). Off is the default and asks
nothing of Immich. Switching it off deletes only the stack's key, in Immich
and in the secrets: no start or switch deletes the family's data
(workspace decision 0036, 2026-10-30). The account, its password, the
members' shares and the albums shared with it stay, so switching on again
uses the same account. Deleting the account is done by hand in Immich. A
stack.toml that cannot be read is neither on nor off, and changes nothing.

Every step is idempotent, because `stack up photos` runs it on every start
while the switch is on. A refused request stops the run with a `Refused`
that names it; the answer keeps what was done before it.
"""

from __future__ import annotations

import json
import os
import secrets as _random
import tempfile
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
IMPORT_KEY_SECRET = "IMPORT_API_KEY"

# The account's profile picture, so the family recognises it in their
# partner list. The photos stacklet owns its copy.
PROFILE_IMAGE = Path(__file__).parent / "stack-account.png"

# Whom the stack shared, under the instance's `.stack/`.
SHARED_STATE = "photos-stack-account.json"

# Core renders IMMICH_API_KEY from photos__API_KEY when it starts, so a
# changed key reaches its photo search only through a restart of core.
RESTART_CORE = "Run './stack restart core' so core's photo search uses the changed key."


class Refused(Exception):
    """A step that must not or could not happen; the message says which
    and what the admin can do."""


# ── The switch ───────────────────────────────────────────────────────────

def switch(stack_config: dict) -> str:
    """"on", "off", or "unknown" for a stack.toml that could not be read.

    The Stack answers `{}` for a stack.toml it cannot read, and the
    installer always writes `[core]`, so an empty config is unknown, not
    off. Only a TOML `true` or the string "true" is on.
    """
    if not stack_config:
        return "unknown"
    value = stack_config.get("photos", {}).get("stack_account")
    return "on" if value is True or value == "true" else "off"


def is_switched_on(stack_config: dict) -> bool:
    return switch(stack_config) == "on"


def is_wanted(stack_config: dict, held: dict) -> bool:
    """Whether a start has anything to do: the switch is on, or the stack
    holds its account's password and a key that may be the account's."""
    return is_switched_on(stack_config) or (
        bool(_held(held, PASSWORD_SECRET)) and bool(_held(held, KEY_SECRET)))


def key_for_imports(config: dict, base_url: str) -> str | None:
    """The key `stack photos import` and `album` use: photos__IMPORT_API_KEY,
    or photos__API_KEY only while the switch is off and Immich says that
    key is another user's. What they upload with the stack's key would
    belong to the stack's account (decision 0035)."""
    held = config.get("secrets", {})
    if _held(held, IMPORT_KEY_SECRET) or is_switched_on(config.get("stack", {})):
        return _held(held, IMPORT_KEY_SECRET) or None
    key = _held(held, KEY_SECRET)
    return key if key and whose_key(base_url, key)[0] == "other" else None


def for_the_admin(answer: dict) -> list[str]:
    """The lines of an answer a person must act on or should know, in the
    order `stack up photos` prints them."""
    return [answer[key] for key in ("warning", "error", "next") if answer.get(key)] \
        + answer.get("notices", [])


# ── The run ──────────────────────────────────────────────────────────────

def reconcile(base_url, admin_token, members, config) -> dict:
    """Make Immich match the switch, and answer what the run did.

    `members` are the users.toml entries; `config` is the command's config
    (`stack`, `secrets`, `set_secret`, `instance_dir`). `admin_token` is
    needed only while the switch is on. Any failure answers
    `{"error": ...}` with what was done before it, `next` included.
    """
    report: dict = {"notices": []}
    try:
        state = switch(config.get("stack", {}))
        if state == "on":
            _ensure(base_url, admin_token, members, config, report)
        elif state == "off":
            _switch_off(base_url, config, report)
        else:
            raise Refused("stack.toml could not be read, so the stack's Photos account "
                          "and its key were left as they are. Fix stack.toml, then start "
                          "photos again.")
    except Refused as e:
        report["error"] = str(e)
    except Exception as e:  # a timeout or a dropped connection
        report["error"] = f"The stack's Photos account: {type(e).__name__}: {e}"
    if not report["notices"]:
        del report["notices"]
    return report


# ── Switched off ─────────────────────────────────────────────────────────

def _switch_off(base_url, config, report):
    """Delete the stack's key, in Immich and in the secrets, and nothing else.

    The key deletes itself, with itself: no login and no password, so a
    password Immich refuses does not keep it alive. The account, its
    password and the shares stay. A dead key is cleared without a word; a
    key of another user stays where it is.
    """
    owner, me = whose_key(base_url, _held(config.get("secrets", {}), KEY_SECRET))
    if owner == "other":
        return
    if owner == "stack":
        _delete_keys(base_url, api_key=_held(config.get("secrets", {}), KEY_SECRET))
    config["set_secret"](KEY_SECRET, "")
    if owner == "stack":
        report["key"] = "deleted"
        report["next"] = RESTART_CORE
        report["notices"].append(
            f"The stack's Photos account is switched off. Its key was deleted, so the stack "
            f"no longer reads photos through it. The account {me.get('name')} "
            f"({ACCOUNT_EMAIL}) and the members' shares stay in Immich. To remove the "
            f"account, delete it in Immich's administration, under Users. If you keep it, "
            f"switching on again uses it.")


# ── Switched on ──────────────────────────────────────────────────────────

def _ensure(base_url, admin_token, members, config, report):
    """The account, its key, every member's share, then its picture."""
    users = _immich_users(base_url, admin_token)
    account = _find_account(users) or _deleted_account(base_url, admin_token)
    name = account["name"] if account else _product_name(config)
    feature = f"The {name} account in Photos"
    report.update(name=name, feature=feature, warning=stage_warning("incubating", feature))
    account, token = _account(base_url, admin_token, account, name, config, report)
    try:
        _key(base_url, token, config, report)
        _share_libraries(base_url, token, account, members, users, config, report)
        _profile_image(base_url, token, report)
    finally:
        immich.logout(base_url, token)


def _deleted_account(base_url, admin_token) -> None:
    """Refuse when the account was deleted in Immich but not purged yet:
    its email stays taken, so it cannot be made again until then."""
    users = _check(immich.request("GET", f"{base_url}/api/admin/users?withDeleted=true",
                                  token=admin_token), "list the deleted Immich accounts")
    if _find_account(users):
        raise Refused(f"The stack's Photos account ({ACCOUNT_EMAIL}) was deleted in Immich, "
                      f"which purges it after its delete delay (7 days by default). The stack "
                      f"waits until then and makes a new account at the next start after it. "
                      f"To use the old account now, restore it in Immich's administration.")


def _account(base_url, admin_token, account, name, config, report):
    """The account and a session of it: (account, token).

    Made when missing. An account at the stack's email whose password the
    stack does not hold is refused: the stack never sets a password on an
    account (decision 0036), because that would lock out whoever made it.
    """
    password = _held(config.get("secrets", {}), PASSWORD_SECRET)
    report["account"] = "exists" if account else "not created"
    if account is None:
        account, password = _create(base_url, admin_token, name, config)
        report["account"] = "created"
    elif not password:
        raise Refused(f"An Immich account with the stack's email ({ACCOUNT_EMAIL}) exists, "
                      f"and the stack holds no password for it, so it does not use it. "
                      f"Delete it or change its email in Immich, then start photos again.")
    return account, _login(base_url, account, password)


def _login(base_url, account, password) -> str:
    """A session of the stack's account, with the password the stack holds.
    A refused password is refused in turn: the stack sets none."""
    status, token = immich.login(base_url, ACCOUNT_EMAIL, password)
    if status == 401:
        raise Refused(f"Immich refuses the password the stack holds for its account "
                      f"{account['name']} ({ACCOUNT_EMAIL}). Delete the account in Immich's "
                      f"administration, under Users, with 'Queue user and assets for "
                      f"immediate deletion' ticked, then start photos again.")
    if not token:
        raise Refused(f"Could not log in as the stack's account (HTTP {status}).")
    return token


def _create(base_url, admin_token, name, config):
    password = _random.token_urlsafe(24)
    account = _check(immich.request("POST", f"{base_url}/api/admin/users", {
        "email": ACCOUNT_EMAIL, "name": name, "password": password,
        "shouldChangePassword": False}, token=admin_token), "create the account")
    config["set_secret"](PASSWORD_SECRET, password)
    _save_shared(config, set())
    return account, password


# ── The key ──────────────────────────────────────────────────────────────

def _key(base_url, token, config, report):
    """Keep the account's working key; otherwise make a new one. A key of
    another user moves to the import key, as on a first switch-on."""
    held = _held(config.get("secrets", {}), KEY_SECRET)
    owner, _ = whose_key(base_url, held)
    if owner == "stack":
        report["key"] = "kept"
        return
    config["set_secret"](KEY_SECRET, _new_key(base_url, token))
    report["next"] = RESTART_CORE
    report["key"] = {"none": "created", "dead": "replaced"}.get(owner, "replaced a key of another user")
    if owner == "other":
        _keep_for_imports(held, config, report)


def whose_key(base_url, key) -> tuple[str, dict]:
    """Whose key `key` is, as Immich answers `GET /api/users/me` with it:
    ("stack", me) for the account's, ("other", me) for another user's or
    one without user.read (403), ("dead", {}) for a refused one (401), and
    ("none", {}) without a key. Any other answer is an error."""
    if not key:
        return "none", {}
    status, me = immich.request("GET", f"{base_url}/api/users/me", api_key=key)
    if status == 401:
        return "dead", {}
    if status == 403:
        return "other", {}
    if status != 200:
        raise Refused(f"Could not check whose key photos__API_KEY holds (HTTP {status}).")
    return ("stack" if me.get("email", "").lower() == ACCOUNT_EMAIL else "other"), me


def _keep_for_imports(hand_set_key, config, report):
    """A key an admin put in photos__API_KEY was there for imports: it
    moves to photos__IMPORT_API_KEY unless that holds one already."""
    if _held(config.get("secrets", {}), IMPORT_KEY_SECRET):
        report["notices"].append("The key that was in photos__API_KEY was dropped: "
                                 "photos__IMPORT_API_KEY already holds one for imports.")
        return
    config["set_secret"](IMPORT_KEY_SECRET, hand_set_key)
    report["notices"].append("The key that was in photos__API_KEY moved to "
                             "photos__IMPORT_API_KEY, so imports keep using it.")


def _new_key(base_url, token) -> str:
    """The account's one key, made again: a key of that name goes first,
    so a key the stack lost does not stay valid in Immich."""
    _delete_keys(base_url, token)
    made = _check(immich.request("POST", f"{base_url}/api/api-keys",
                                 {"name": KEY_NAME, "permissions": ["all"]}, token=token),
                  "create the account's key")
    return made["secret"]


def _delete_keys(base_url, token=None, api_key=None) -> None:
    """Delete the account's keys named KEY_NAME, the only keys the stack
    makes, as a session or with a key of the account itself. Oldest first,
    so a key that deletes itself goes last: its newest key, as a rule."""
    auth = {"token": token, "api_key": api_key}
    keys = _check(immich.request("GET", f"{base_url}/api/api-keys", **auth),
                  "list the account's keys")
    for key in sorted(keys, key=lambda k: k.get("createdAt", "")):
        if key.get("name") == KEY_NAME:
            _check(immich.request("DELETE", f"{base_url}/api/api-keys/{key['id']}", **auth),
                   "delete the account's key")


def _profile_image(base_url, token, report):
    """Upload the picture once, best effort: an account with a picture
    keeps it, and a failure is a notice, never an error."""
    try:
        me = _check(immich.request("GET", f"{base_url}/api/users/me", token=token),
                    "read the account")
        if me.get("profileImagePath"):
            return
        _check(immich.upload(f"{base_url}/api/users/profile-image", "file", "stack.png",
                             PROFILE_IMAGE.read_bytes(), "image/png", token=token),
               "set the account's picture")
        report["profile_image"] = "set"
    except (Refused, OSError) as e:
        report["notices"].append(f"The account has no profile picture yet: {e}")


# ── Members' libraries ───────────────────────────────────────────────────

def _share_libraries(base_url, token, account, members, users, config, report):
    """Every member with an Immich account shares once, in the searches.

    A member shares as themselves, with the password the stack gave them.
    One who changed it is named for sharing by hand; one who removed the
    account from their partners is left alone; one without an Immich
    account is named as such. inTimeline is set only for members.
    """
    ids = {u["email"].lower(): u["id"] for u in users}
    member_ids = {ids[m["email"].lower()] for m in members if m["email"].lower() in ids}
    partners = _partners(base_url, token)
    shared = _load_shared(config) | (partners.keys() & member_ids)
    outcome: dict[str, list] = {"shared": [], "by_hand": [], "removed_by_member": [],
                                "no_immich_account": []}
    for member in members:
        uid = ids.get(member["email"].lower())
        if uid is None:
            outcome["no_immich_account"].append(user_id(member))
        elif uid in shared:
            if uid not in partners:
                outcome["removed_by_member"].append(user_id(member))
        elif _share(base_url, member, account["id"], config):
            outcome["shared"].append(user_id(member))
            shared.add(uid)
        else:
            outcome["by_hand"].append(user_id(member))
    _save_shared(config, shared)
    if outcome["shared"]:
        partners = _partners(base_url, token)
    _show_in_searches(base_url, token, partners, member_ids)
    report.update({k: v for k, v in outcome.items() if v})
    report["notices"] += _share_notices(outcome, account["name"])


def _share_notices(outcome, name) -> list[str]:
    """What the admin reads about the members who are not shared."""
    notices = []
    if outcome["by_hand"]:
        notices.append(f"{', '.join(outcome['by_hand'])}: the stack cannot log in as them. "
                       f"In Immich, open Account Settings > Partner Sharing, "
                       f"and add '{name}' as a partner.")
    if outcome["removed_by_member"]:
        notices.append(f"{', '.join(outcome['removed_by_member'])} removed '{name}' from their "
                       f"partners in Immich. The stack does not share their library again.")
    if outcome["no_immich_account"]:
        notices.append(f"{', '.join(outcome['no_immich_account'])}: no Immich account. Run "
                       f"'./stack photos setup' to create it; the next 'stack up photos' "
                       f"then shares the library.")
    return notices


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


def _show_in_searches(base_url, token, partners, member_ids):
    """Put every member's share into the account's searches. A partner
    who is not in users.toml is left as they are."""
    for uid, partner in partners.items():
        if uid in member_ids and not partner.get("inTimeline"):
            _check(immich.request("PUT", f"{base_url}/api/partners/{uid}",
                                  {"inTimeline": True}, token=token),
                   "show a member's photos in the account's searches")


def _partners(base_url, token) -> dict:
    """The people who share with the account, by Immich user id."""
    partners = _check(immich.request("GET", f"{base_url}/api/partners?direction=shared-with",
                                     token=token), "list the account's partners")
    return {p["id"]: p for p in partners}


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


# ── Whom the stack shared ────────────────────────────────────────────────

def _state_path(config) -> Path:
    return Path(config["instance_dir"]) / ".stack" / SHARED_STATE


def _load_shared(config) -> set:
    """The Immich user ids of the members the stack has shared."""
    path = _state_path(config)
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text())["shared"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused(f"Cannot read {path} ({e}). Delete it to reset: the next start then "
                      f"counts the members who share now as shared, and shares the rest.") from e


def _save_shared(config, shared: set) -> None:
    """Write the file whole or not at all: a temp file, then a rename."""
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"shared": sorted(shared)}, indent=2) + "\n")
    os.replace(tmp, path)
