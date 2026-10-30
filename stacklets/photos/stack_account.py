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

Whose key `photos__API_KEY` holds is asked of Immich (`_key_owner`): the
account's, another user's (set by hand, which moves to the import key on
switch-on and stays on switch-off), or one Immich no longer accepts.

Each member is shared once. The stack remembers whom it shared, by Immich
user id, in `.stack/photos-stack-account.json`, so a member who removes
the account from their partners stays removed. The same file holds the
fingerprint of the stack's key, so a start while switched off asks Immich
nothing unless that key may still be live.

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

import hashlib
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


def is_wanted(stack_config: dict, held: dict, instance_dir) -> bool:
    """Whether a start has anything to do: the switch is on, or it is off
    while photos__API_KEY may still hold the stack's own key.

    Switched off, the start asks Immich nothing unless the key held is the
    one the stack last made (its fingerprint in the state file), so a key
    an admin set by hand causes no request. A state file that cannot be
    read says yes, so the run names it.
    """
    if is_switched_on(stack_config):
        return True
    key = _held(held, KEY_SECRET)
    if not key:
        return False
    try:
        return _load_state(_state_file(instance_dir)).get("key") == _fingerprint(key)
    except Refused:
        return True


def for_the_admin(answer: dict) -> list[str]:
    """The lines of an answer a person must act on or should know, in the
    order `stack up photos` prints them."""
    return [answer[key] for key in ("warning", "error", "next") if answer.get(key)] \
        + answer.get("notices", [])


# ── The run ──────────────────────────────────────────────────────────────

def reconcile(base_url, admin_token, members, config) -> dict:
    """Make Immich match the switch, and answer what the run did.

    `members` are the users.toml entries; `config` is the command's config
    (`stack`, `secrets`, `set_secret`, `instance_dir`). Any failure answers
    `{"error": ...}` with what was done before it, `next` included.
    """
    report: dict = {"notices": []}
    try:
        state = switch(config.get("stack", {}))
        if state == "on":
            _ensure(base_url, admin_token, members, config, report)
        elif state == "off":
            _switch_off(base_url, admin_token, config, report)
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

def _switch_off(base_url, admin_token, config, report):
    """Delete the stack's key, in Immich and in the secrets, and nothing else.

    The account, its password, the shares and the remembered members stay.
    photos__API_KEY is emptied only when it holds the account's key or a
    key Immich no longer accepts: a key of another user stays where it is.
    """
    held = _held(config.get("secrets", {}), KEY_SECRET)
    owner = _key_owner(base_url, held)
    if owner != "other":
        account = _find_account(_immich_users(base_url, admin_token))
        if account is not None and owner == "stack":
            _delete_stack_keys(base_url, admin_token, account, config)
        config["set_secret"](KEY_SECRET, "")
        report["key"] = "deleted" if owner == "stack" and account else "cleared"
        report["next"] = RESTART_CORE
        report["notices"].append(_switched_off(account, owner))
    _remember_key(config, None)


def _delete_stack_keys(base_url, admin_token, account, config):
    token = _login(base_url, account, _held(config.get("secrets", {}), PASSWORD_SECRET))
    try:
        _delete_keys(base_url, token)
    finally:
        immich.logout(base_url, token)


def _switched_off(account, owner) -> str:
    if account is None:
        return (f"The stack's Photos account ({ACCOUNT_EMAIL}) is no longer in Immich, "
                f"so the stack cleared its key.")
    if owner == "dead":
        return "The stack's Photos key no longer worked in Immich, so the stack cleared it."
    return (f"The stack's Photos account is switched off. Its key was deleted, so the stack "
            f"no longer reads photos through it. The account {account['name']} "
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
        raise Refused(f"The stack's Photos account ({ACCOUNT_EMAIL}) was deleted in Immich "
                      f"and is waiting to be purged. Restore it in Immich's administration, "
                      f"or switch [photos] stack_account off.")


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
                      f"administration, under Users, then start photos again.")
    if not token:
        raise Refused(f"Could not log in as the stack's account (HTTP {status}).")
    return token


def _create(base_url, admin_token, name, config):
    password = _random.token_urlsafe(24)
    account = _check(immich.request("POST", f"{base_url}/api/admin/users", {
        "email": ACCOUNT_EMAIL, "name": name, "password": password,
        "shouldChangePassword": False}, token=admin_token), "create the account")
    config["set_secret"](PASSWORD_SECRET, password)
    _save_state(config, {"shared": []})
    return account, password


# ── The key ──────────────────────────────────────────────────────────────

def _key(base_url, token, config, report):
    """Keep the account's working key; otherwise make a new one. A key of
    another user moves to the import key, as on a first switch-on."""
    held = _held(config.get("secrets", {}), KEY_SECRET)
    owner = _key_owner(base_url, held)
    if owner == "stack":
        _remember_key(config, held)
        report["key"] = "kept"
        return
    key = _new_key(base_url, token)
    config["set_secret"](KEY_SECRET, key)
    report["next"] = RESTART_CORE
    report["key"] = {"none": "created", "dead": "replaced"}.get(owner, "replaced a key of another user")
    if owner == "other":
        _keep_for_imports(held, config, report)
    elif owner == "dead":
        report["notices"].append("The key that was in photos__API_KEY no longer worked in "
                                 "Immich, so the stack replaced it.")
    _remember_key(config, key)


def _key_owner(base_url, key) -> str:
    """Whose key photos__API_KEY holds, as Immich answers for it: "stack"
    (the account's), "other" (another user's), "dead" (refused with 401),
    or "none" when there is no key."""
    if not key:
        return "none"
    status, me = immich.request("GET", f"{base_url}/api/users/me", api_key=key)
    if status == 401:
        return "dead"
    if status != 200:
        raise Refused(f"Could not check whose key photos__API_KEY holds (HTTP {status}).")
    return "stack" if me.get("email", "").lower() == ACCOUNT_EMAIL else "other"


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


def _delete_keys(base_url, token) -> None:
    """Delete the account's keys named KEY_NAME, the only keys the stack makes."""
    keys = _check(immich.request("GET", f"{base_url}/api/api-keys", token=token),
                  "list the account's keys")
    for key in keys:
        if key.get("name") == KEY_NAME:
            _check(immich.request("DELETE", f"{base_url}/api/api-keys/{key['id']}", token=token),
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

def _state_file(instance_dir) -> Path:
    return Path(instance_dir) / ".stack" / SHARED_STATE


def _load_state(path) -> dict:
    """The state file: `shared`, the Immich user ids of the members the
    stack has shared, and `key`, the fingerprint of the stack's own key."""
    if not path.exists():
        return {"shared": []}
    try:
        state = json.loads(path.read_text())
        if not isinstance(state.get("shared"), list):
            raise ValueError("no list of shared members")
        return state
    except (OSError, ValueError, AttributeError) as e:
        raise Refused(f"Cannot read {path} ({e}). Delete it to reset: the next start then "
                      f"counts the members who share now as shared, and shares the rest.") from e


def _load_shared(config) -> set:
    return set(_load_state(_state_file(config["instance_dir"]))["shared"])


def _save_shared(config, shared: set) -> None:
    state = _load_state(_state_file(config["instance_dir"]))
    _save_state(config, {**state, "shared": sorted(shared)})


def _remember_key(config, key) -> None:
    """Record the fingerprint of the stack's key, or forget it (None)."""
    state = _load_state(_state_file(config["instance_dir"]))
    state.pop("key", None)
    if key:
        state["key"] = _fingerprint(key)
    _save_state(config, state)


def _fingerprint(key: str) -> str:
    """A hash, so the state file holds no key."""
    return hashlib.sha256(key.encode()).hexdigest()


def _save_state(config, state: dict) -> None:
    """Write the file whole or not at all: a temp file, then a rename."""
    path = _state_file(config["instance_dir"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, path)
