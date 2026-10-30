"""The stack's own Immich account: `[photos] stack_account` in stack.toml.

With the switch on, `stack photos setup --stack-account` (which every
`stack up photos` runs) gives the stack an Immich account of its own: not
an admin, its password known only to the stack, the photos stacklet's icon
as its picture. Every family member with an Immich account shares their library
with it as a partner, in its searches, and the account's API key is kept
as `photos__API_KEY`, the key core's photo search reads. A partner share is
read-only, so the stack can find every member's photos and change none.
With the switch off, only the stack's key is deleted (decision 0036).
Workspace decision 0035 holds the why and the measured Immich behaviour.

A stand-in Immich answers over HTTP with the routes and shapes Immich
3.3.5 answered on the test rig: accounts and passwords through the admin
API, partner shares made by the member and put in the timeline by the
account, API keys and the profile picture owned by whoever sets them,
sessions that end on logout, and accounts deleted in Immich's UI that wait
to be purged (`deletedAt`). It keeps its state across runs, so a second run shows what setup asks for
again.
"""

from __future__ import annotations

import json
import re
import time
import uuid

import pytest
from werkzeug.wrappers import Response

from stack.stack import stage_warning
from stacklets.photos import stack_account
from stacklets.photos.cli import album, setup
from tests.springfield import REPO

HOMER = {"id": "homer", "name": "Homer Simpson", "email": "homer@simpson.test",
         "role": "admin", "stacklets": ["photos"]}
MARGE = {"id": "marge", "name": "Marge Simpson", "email": "marge@simpson.test",
         "role": "member", "stacklets": ["photos"]}
BART = {"id": "bart", "name": "Bart Simpson", "email": "bart@simpson.test",
        "role": "member", "stacklets": ["photos"]}
LISA = {"id": "lisa", "name": "Lisa Simpson", "email": "lisa@simpson.test",
        "role": "member"}
FAMILY_SECRETS = {"global__ADMIN_PASSWORD": "tech-admin-pw",
                  "global__USER_HOMER_PASSWORD": "homer-pw",
                  "global__USER_MARGE_PASSWORD": "marge-pw",
                  "global__USER_BART_PASSWORD": "bart-pw",
                  "global__USER_LISA_PASSWORD": "lisa-pw"}
TECH_ADMIN = "stackadmin@home.local"
ACCOUNT = "stack-photos@home.local"


class Immich:
    """Immich 3.3.5's API, as far as the stack's account uses it, with state."""

    def __init__(self, httpserver):
        self.users = {}       # id -> {id, email, name, isAdmin, password, profileImagePath}
        self.sessions = {}    # session token -> user id
        self.api_keys = {}    # key secret -> user id
        self.partners = {}    # (sharer id, shared-with id) -> inTimeline
        self.keys = {}        # key id -> {owner, name, secret}
        self.requests = []    # (method, path) of every request, in order
        self.refuse = {}      # (method, path pattern) -> status to answer instead
        self.stall = {}       # path -> seconds to wait before answering
        self.add(TECH_ADMIN, "tech-admin-pw", is_admin=True)
        httpserver.expect_request(re.compile(".*")).respond_with_handler(self._handle)
        self.port = httpserver.port

    def add(self, email, password, is_admin=False, name=""):
        uid = str(uuid.uuid4())
        self.users[uid] = {"id": uid, "email": email, "name": name or email,
                           "isAdmin": is_admin, "password": password, "profileImagePath": "",
                           "deletedAt": None}
        return uid

    def by_email(self, email):
        return next((u for u in self.users.values() if u["email"] == email), None)

    def account(self):
        return self.by_email(ACCOUNT)

    def shares(self):
        """Who shares with the stack's account, and whether it is in its searches."""
        account = self.account()
        return {self.users[sharer]["email"]: shown
                for (sharer, reader), shown in self.partners.items() if reader == account["id"]}

    def made(self, method, path_pattern):
        return [p for m, p in self.requests if m == method and re.fullmatch(path_pattern, p)]

    def _caller(self, request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        uid = self.sessions.get(token) or self.api_keys.get(request.headers.get("x-api-key", ""))
        return self.users.get(uid)

    def _handle(self, request):
        self.requests.append((request.method, request.path))
        time.sleep(self.stall.get(request.path, 0))
        for (method, pattern), status in self.refuse.items():
            if request.method == method and re.fullmatch(pattern, request.path):
                return _json({"message": "refused by the test"}, status)
        body = request.get_json(silent=True) or {}
        me = self._caller(request)
        if request.path == "/api/server/ping":
            return _json({"res": "pong"})
        if request.path == "/api/auth/admin-sign-up":
            return _json({"message": "Admin setup is not available"}, 400)
        if request.path == "/api/auth/login":
            user = self.by_email(body.get("email"))
            if not user or user["password"] != body.get("password"):
                return _json({"message": "Incorrect email or password"}, 401)
            token = str(uuid.uuid4())
            self.sessions[token] = user["id"]
            return _json({"accessToken": token, "userId": user["id"]}, 201)
        if me is None:
            return _json({"message": "Invalid user token"}, 401)
        if request.path == "/api/auth/logout":
            self.sessions.pop(request.headers["Authorization"].removeprefix("Bearer "))
            return _json({"successful": True})
        if request.path.startswith("/api/admin/"):
            return self._admin(request, body) if me["isAdmin"] else _json({}, 403)
        return self._user(request, body, me)

    def _admin(self, request, body):
        if request.path == "/api/admin/users" and request.method == "GET":
            deleted_too = request.args.get("withDeleted") == "true"
            return _json([_public(u) for u in self.users.values()
                          if deleted_too or not u["deletedAt"]])
        if request.path == "/api/admin/users":
            if self.by_email(body["email"]):
                return _json({"message": "Failed to create user"}, 500)
            uid = self.add(body["email"], body["password"], body.get("isAdmin", False), body["name"])
            return _json(_public(self.users[uid]), 201)
        uid = request.path.rsplit("/", 1)[1]
        if request.method == "PUT":
            self.users[uid].update(body)
            return _json(_public(self.users[uid]))
        # The stack deletes no user (decision 0036), so a delete is not stood in.
        return _json({"message": f"no stand-in for {request.method} {request.path}"}, 404)

    def _user(self, request, body, me):
        route = (request.method, request.path)
        if route == ("GET", "/api/users/me"):
            return _json(_public(me))
        if route == ("POST", "/api/users/profile-image"):
            assert request.files["file"].read().startswith(b"\x89PNG")
            me["profileImagePath"] = f"upload/profile/{me['id']}.webp"
            return _json({"userId": me["id"], "profileImagePath": me["profileImagePath"]}, 201)
        if route == ("POST", "/api/partners"):
            self.partners[(me["id"], body["sharedWithId"])] = False
            return _json({"id": body["sharedWithId"]}, 201)
        if route == ("GET", "/api/partners"):
            return _json([{**_public(self.users[sharer]), "inTimeline": shown}
                          for (sharer, reader), shown in self.partners.items()
                          if reader == me["id"]])
        if m := re.fullmatch(r"/api/partners/([^/]+)", request.path):
            self.partners[(m.group(1), me["id"])] = body["inTimeline"]
            return _json({"id": m.group(1), "inTimeline": body["inTimeline"]})
        if route == ("GET", "/api/api-keys"):
            return _json([{"id": kid, "name": k["name"]}
                          for kid, k in self.keys.items() if k["owner"] == me["id"]])
        if route == ("POST", "/api/api-keys"):
            kid, secret = str(uuid.uuid4()), str(uuid.uuid4())
            self.keys[kid] = {"owner": me["id"], "name": body["name"], "secret": secret}
            self.api_keys[secret] = me["id"]
            return _json({"secret": secret, "apiKey": {"id": kid, "name": body["name"]}}, 201)
        if m := re.fullmatch(r"/api/api-keys/([^/]+)", request.path):
            self.api_keys.pop(self.keys.pop(m.group(1))["secret"])
            return Response(status=204)
        return _json({"message": f"no stand-in for {route}"}, 404)


def _json(data, status=200):
    return Response(json.dumps(data), status=status, content_type="application/json")


def _public(user):
    return {k: v for k, v in user.items() if k != "password"}


@pytest.fixture
def immich(httpserver):
    """Immich after install: the tech admin and Homer's, Marge's and
    Bart's accounts. Lisa has none."""
    server = Immich(httpserver)
    for person in (HOMER, MARGE, BART):
        server.add(person["email"], f"{person['id']}-pw", person["role"] == "admin",
                   person["name"])
    return server


class Instance:
    """What `stack photos setup` gets from the framework: stack.toml,
    users.toml, the secrets as they are when it starts, a writer for the
    photos secrets, and the instance directory."""

    def __init__(self, immich, tmp_path, switch=None, core_name="homestack"):
        self.immich = immich
        self.tmp_path = tmp_path
        self.stack = {"core": {"name": core_name}}
        if switch is not None:
            self.stack["photos"] = {"stack_account": switch}
        self.secrets = dict(FAMILY_SECRETS)
        self.users = [HOMER, MARGE, BART]

    def switch(self, value):
        self.stack["photos"] = {"stack_account": value}

    def set_secret(self, name, value):
        self.secrets[f"photos__{name}"] = value

    def config(self):
        return {"is_healthy": lambda: True, "users": self.users, "stack": self.stack,
                "secrets": dict(self.secrets), "set_secret": self.set_secret,
                "instance_dir": str(self.tmp_path), "repo_root": str(REPO)}

    def start(self):
        """What `stack up photos` runs."""
        return setup.run(["--stack-account"], {"port": self.immich.port}, self.config())

    def started(self):
        """A start that succeeded."""
        result = self.start()
        assert "error" not in result, result
        return result


@pytest.fixture
def instance(immich, tmp_path):
    return Instance(immich, tmp_path, switch=True)


# ── Switched on ─────────────────────────────────────────────────────────


def test_the_stack_gets_its_own_account_that_reads_every_library(immich, instance):
    """Given the switch is on, when photos starts, then Immich has one
    non-admin account for the stack with the stacklet's icon, every member
    shares their library with it in its searches, and its key is kept
    where core reads it. This is what lets one key find every member's
    photos."""
    result = instance.started()

    account = immich.account()
    assert not account["isAdmin"] and account["name"] == "homestack"
    assert account["password"] == instance.secrets["photos__STACK_ACCOUNT_PASSWORD"]
    assert account["profileImagePath"]
    assert immich.shares() == {HOMER["email"]: True, MARGE["email"]: True, BART["email"]: True}
    [key] = immich.keys.values()
    assert key["owner"] == account["id"]
    assert instance.secrets["photos__API_KEY"] == key["secret"]
    assert result["warning"] == stage_warning("incubating", result["feature"])
    assert "./stack restart core" in result["next"]


def test_a_second_start_changes_nothing(immich, instance):
    """Given the account, its picture, its shares and its key, when photos
    starts again, then nothing is made again and core needs no restart."""
    instance.started()
    immich.requests.clear()

    result = instance.started()

    assert immich.made("POST", "/api/admin/users") == []
    assert immich.made("POST", "/api/partners") == []
    assert immich.made("PUT", "/api/.*") == []
    assert immich.made("POST", "/api/api-keys") == []
    assert immich.made("POST", "/api/users/profile-image") == []
    assert len(immich.keys) == 1 and result["key"] == "kept"
    assert "next" not in result


def test_every_session_the_start_opens_is_closed(immich, instance):
    """Members' Immich lists each open session as a device: after a start
    none is left open, the tech admin's included."""
    instance.users = [HOMER, MARGE, BART]
    instance.started()

    assert immich.sessions == {}


def test_a_lost_key_is_replaced_and_the_old_one_removed(immich, instance):
    """Given the stack lost its key (secrets from an old backup), when
    photos starts, then the account gets one new key and the old one is
    deleted from Immich."""
    instance.started()
    [old] = immich.keys
    del instance.secrets["photos__API_KEY"]

    result = instance.started()

    [new] = immich.keys
    assert new != old and instance.secrets["photos__API_KEY"] == immich.keys[new]["secret"]
    assert "./stack restart core" in result["next"]


def test_a_key_immich_no_longer_accepts_is_replaced(immich, instance):
    """Given the key was deleted in Immich's settings, when photos starts,
    then Immich answers 401 to it and the stack makes a new one."""
    instance.started()
    immich.keys.clear()
    immich.api_keys.clear()

    result = instance.started()

    assert result["key"] == "replaced"
    [key] = immich.keys.values()
    assert instance.secrets["photos__API_KEY"] == key["secret"]


def test_a_member_without_a_stacklets_line_is_shared(immich, instance):
    """Given Homer and Marge have Immich accounts but no `stacklets` line
    in users.toml (the test rig), when photos starts, then both share.
    Lisa, who has no Immich account, is named as such and not asked to
    share by hand."""
    homer = {k: v for k, v in HOMER.items() if k != "stacklets"}
    marge = {k: v for k, v in MARGE.items() if k != "stacklets"}
    instance.users = [homer, marge, LISA]

    result = instance.started()

    assert immich.shares() == {HOMER["email"]: True, MARGE["email"]: True}
    assert result["no_immich_account"] == ["lisa"]
    assert "by_hand" not in result


def test_a_member_the_stack_cannot_log_in_as_is_named(immich, instance):
    """Given Marge changed her password in the app, when photos starts,
    then Homer and Bart share, and the answer names Marge and the account
    as Immich shows it, so she can share by hand."""
    immich.by_email(MARGE["email"])["password"] = "changed-in-the-app"
    instance.started()
    instance.stack["core"]["name"] = "renamed"

    result = instance.started()

    assert immich.shares() == {HOMER["email"]: True, BART["email"]: True}
    assert result["by_hand"] == ["marge"]
    [hint] = [n for n in result["notices"] if "marge" in n]
    assert "'homestack'" in hint


def test_a_member_added_later_is_shared_on_the_next_start(immich, instance):
    """Given Homer's and Marge's shares, when Bart is added to users.toml
    and photos starts, then Bart's library is shared."""
    instance.users = [HOMER, MARGE]
    instance.started()
    assert BART["email"] not in immich.shares()

    instance.users = [HOMER, MARGE, BART]
    instance.started()

    assert immich.shares()[BART["email"]] is True


def test_a_member_who_removed_the_account_stays_removed(immich, instance):
    """Given Marge removed the stack from her partners, when photos starts,
    then her library is not shared again and the answer says she removed
    it. A partner who is not a member is not put in the searches."""
    instance.started()
    account = immich.account()
    del immich.partners[(immich.by_email(MARGE["email"])["id"], account["id"])]
    immich.partners[(immich.by_email(TECH_ADMIN)["id"], account["id"])] = False
    immich.requests.clear()

    result = instance.started()

    assert MARGE["email"] not in immich.shares()
    assert result["removed_by_member"] == ["marge"]
    assert immich.shares()[TECH_ADMIN] is False


def test_a_lost_password_is_set_again_and_the_shares_stay(immich, instance):
    """Given the stack lost the account's password, when photos starts,
    then the admin sets a new one: no account is deleted, and the
    members' shares and the key stay."""
    instance.started()
    instance.secrets["photos__STACK_ACCOUNT_PASSWORD"] = "an-old-backup"
    shares = immich.shares()

    result = instance.started()

    assert result["account"] == "password set"
    assert immich.made("DELETE", "/api/admin/users/.*") == []
    assert immich.account()["password"] == instance.secrets["photos__STACK_ACCOUNT_PASSWORD"]
    assert immich.shares() == shares


def test_any_other_login_failure_stops_the_start(immich, instance):
    """Given Immich answers 500 to the account's login, when photos starts,
    then the answer is an error and nothing after the login is asked."""
    instance.started()
    immich.refuse[("POST", "/api/auth/login")] = 500
    immich.requests.clear()

    result = instance.start()

    assert "error" in result
    assert immich.made("PUT", "/api/admin/users/.*") == []
    assert immich.made("GET", "/api/partners") == []


def test_a_refused_key_is_a_clear_error(immich, instance):
    """Given Immich refuses to make a key, when photos starts, then the
    answer names that step, and what was done before it is in the answer."""
    immich.refuse[("POST", "/api/api-keys")] = 400

    result = instance.start()

    assert "key" in result["error"] and "HTTP 400" in result["error"]
    assert result["account"] == "created"
    assert not instance.secrets.get("photos__API_KEY")


def test_a_refused_account_is_not_reported_as_existing(immich, instance):
    """Given Immich refuses to create the account, when photos starts, then
    the answer is an error and says the account was not created."""
    immich.refuse[("POST", "/api/admin/users")] = 400

    result = instance.start()

    assert "error" in result and result["account"] == "not created"


def test_an_error_before_the_account_is_answered_not_raised(immich, instance):
    """Given the tech admin cannot log in, when photos starts, then the
    answer is an error, with nothing about an account in it."""
    instance.secrets["global__ADMIN_PASSWORD"] = "wrong"

    result = instance.start()

    assert set(result) == {"error"}


def test_member_accounts_are_seeded_without_the_stack_account(immich, instance):
    """`stack photos setup` without a flag (the install hook) makes member
    accounts only, so a first install does the stack's account once, in
    `stack up photos`."""
    result = setup.run([], {"port": immich.port}, instance.config())

    assert "error" not in result, result
    assert immich.account() is None


# ── Switched off ────────────────────────────────────────────────────────


def test_switching_off_deletes_only_the_stacks_key(immich, instance, tmp_path):
    """Given the account, its key and the shares, when the switch is turned
    off and photos starts, then the key is deleted in Immich and in the
    secrets, and nothing else: no request deletes or changes a user, and
    the password, the remembered shares and the members' partner shares
    stay. Nothing of the family's is deleted implicitly (decision 0036)."""
    instance.started()
    [key_id] = immich.keys
    shares = immich.shares()
    state = (tmp_path / ".stack" / "photos-stack-account.json").read_text()
    immich.requests.clear()

    instance.switch(False)
    result = instance.started()

    assert immich.made("DELETE", "/api/api-keys/.*") == [f"/api/api-keys/{key_id}"]
    assert immich.made("DELETE", "/api/admin/users.*") == []
    assert immich.made("PUT", "/api/admin/users.*") == []
    assert immich.account() is not None and immich.keys == {}
    assert immich.shares() == shares
    assert not instance.secrets.get("photos__API_KEY")
    assert instance.secrets["photos__STACK_ACCOUNT_PASSWORD"]
    assert (tmp_path / ".stack" / "photos-stack-account.json").read_text() == state
    assert result["key"] == "deleted"


def test_a_second_start_switched_off_asks_immich_nothing(immich, instance):
    """Given the key was deleted at switch-off, when photos starts again
    switched off, then Immich gets no request."""
    instance.started()
    instance.switch(False)
    instance.started()
    immich.requests.clear()

    result = instance.start()

    assert result == {"ok": True}
    assert immich.requests == []


def test_switching_on_again_uses_the_same_account(immich, instance):
    """Given the switch went off and on again, when photos starts, then the
    same account gets a new key and no member is shared a second time."""
    instance.started()
    account = immich.account()
    instance.switch(False)
    instance.started()
    immich.requests.clear()

    instance.switch(True)
    result = instance.started()

    assert immich.account()["id"] == account["id"]
    assert immich.made("POST", "/api/admin/users") == []
    assert immich.made("POST", "/api/partners") == []
    assert len(immich.keys) == 1 and result["key"] == "created"
    assert instance.secrets["photos__API_KEY"] == next(iter(immich.keys.values()))["secret"]


def test_an_account_gone_from_immich_at_switch_off_clears_the_key(immich, instance):
    """Given the account was deleted by hand in Immich, when the switch is
    turned off and photos starts, then the stack clears its key and says
    so, without an error."""
    instance.started()
    immich.account()["deletedAt"] = "2026-10-30T08:00:00Z"

    instance.switch(False)
    result = instance.started()

    assert not instance.secrets.get("photos__API_KEY")
    assert result["key"] == "cleared"
    assert any("no longer in Immich" in n for n in result["notices"])


@pytest.mark.parametrize("switch", [None, False, "false"], ids=["unset", "off", "string-false"])
def test_without_the_switch_immich_is_asked_nothing(immich, tmp_path, switch):
    """Given the stack made no account and the switch is not on, when
    photos starts, then Immich gets no request and a key an admin set by
    hand stays as it is."""
    instance = Instance(immich, tmp_path, switch=switch)
    instance.secrets["photos__API_KEY"] = "set-by-hand"

    result = instance.start()

    assert result == {"ok": True}
    assert immich.requests == []
    assert instance.secrets["photos__API_KEY"] == "set-by-hand"


def test_switching_on_over_a_key_set_by_hand_says_it_was_replaced(immich, instance):
    """Given an admin put a key in photos__API_KEY by hand, when the switch
    is turned on, then the stack's key takes its place and the answer says
    the old one was not the stack's."""
    instance.secrets["photos__API_KEY"] = "set-by-hand"

    result = instance.started()

    assert result["key"] == "replaced a key the stack did not make"
    assert instance.secrets["photos__API_KEY"] != "set-by-hand"


@pytest.mark.parametrize("value, on", [
    (True, True), ("true", True),
    (False, False), ("false", False), ("False", False), ("yes", False), (1, False), (None, False),
])
def test_only_true_switches_it_on(value, on):
    """A TOML `true` or the string "true" is on; anything else is off."""
    assert stack_account.is_switched_on({"photos": {"stack_account": value}}) is on


# ── What the admin reads ─────────────────────────────────────────────────


def test_stack_up_passes_on_what_a_person_must_act_on(immich, instance):
    """Given Marge removed the account, Lisa has no Immich account and the
    key was lost, when photos starts, then the lines `stack up photos`
    prints name Marge, name Lisa with the command that makes her account,
    and say to restart core."""
    instance.started()
    del immich.partners[(immich.by_email(MARGE["email"])["id"], immich.account()["id"])]
    instance.users = [HOMER, MARGE, BART, LISA]
    del instance.secrets["photos__API_KEY"]

    lines = stack_account.for_the_admin(instance.started())

    assert any("marge" in line and "removed" in line for line in lines)
    assert any("lisa" in line and "./stack photos setup" in line for line in lines)
    assert any("./stack restart core" in line for line in lines)


def test_switching_off_says_what_stays_and_to_restart_core(immich, instance):
    """The admin reads that the account and the shares stay, how to delete
    the account by hand, and that core still holds the deleted key until
    it restarts."""
    instance.started()
    instance.switch(False)

    lines = stack_account.for_the_admin(instance.started())

    [off] = [line for line in lines if "switched off" in line]
    assert "homestack" in off and "stack-photos@home.local" in off and "Users" in off
    assert any("./stack restart core" in line for line in lines)


# ── Failures keep what was done ─────────────────────────────────────────


def test_a_timeout_after_a_new_key_still_says_to_restart_core(immich, instance, monkeypatch):
    """Given Immich stops answering after the key was made and saved, when
    photos starts, then the answer is an error and still says to restart
    core, because the saved key is new."""
    monkeypatch.setattr(stack_account.immich, "TIMEOUT_SECONDS", 0.3)
    immich.stall["/api/partners"] = 0.6

    result = instance.start()

    assert "error" in result and instance.secrets["photos__API_KEY"]
    assert "./stack restart core" in result["next"]


def test_a_refused_picture_does_not_block_the_shares(immich, instance):
    """Given Immich refuses the profile picture, when photos starts, then
    every member is shared, and the answer has a notice, not an error."""
    immich.refuse[("POST", "/api/users/profile-image")] = 400

    result = instance.started()

    assert immich.shares() == {HOMER["email"]: True, MARGE["email"]: True, BART["email"]: True}
    assert any("picture" in n for n in result["notices"])


def test_an_unreadable_state_file_is_named_and_the_key_is_still_made(immich, instance, tmp_path):
    """Given the file that remembers the shares is damaged, when photos
    starts, then the error names the file and how to reset it, and the
    key is made all the same."""
    state = tmp_path / ".stack" / "photos-stack-account.json"
    instance.started()
    state.write_text("{not json")
    del instance.secrets["photos__API_KEY"]

    result = instance.start()

    assert str(state) in result["error"] and "Delete it" in result["error"]
    assert instance.secrets["photos__API_KEY"]


def test_a_member_whose_account_was_made_again_is_shared_again(immich, instance):
    """Given Marge's Immich account was deleted and made again, when photos
    starts, then her new account shares: the stack remembers accounts,
    not email addresses."""
    instance.started()
    old = immich.by_email(MARGE["email"])
    del immich.users[old["id"]]
    immich.partners = {k: v for k, v in immich.partners.items() if old["id"] not in k}
    immich.add(MARGE["email"], "marge-pw", name=MARGE["name"])

    instance.started()

    assert immich.shares()[MARGE["email"]] is True


# ── Accounts the stack did not make ─────────────────────────────────────


def test_an_account_the_stack_did_not_make_is_never_taken_over(immich, instance):
    """Given an account at the stack's address whose password the stack
    does not hold, when photos starts, then the stack refuses, does not
    set its password, and says to delete it or change its email. Setting
    a password would lock its owner out (decision 0036)."""
    uid = immich.add(ACCOUNT, "someone-elses", name="Stack")

    result = instance.start()

    assert "Delete it or change its email" in result["error"]
    assert immich.made("PUT", "/api/admin/users/.*") == []
    assert immich.users[uid]["password"] == "someone-elses"


def test_an_account_deleted_in_immich_is_named_not_made_again(immich, instance):
    """Given the stack's account was deleted in Immich's UI and waits to be
    purged, when photos starts, then the stack neither restores nor makes
    it again, and says to restore it or switch off."""
    instance.started()
    immich.account()["deletedAt"] = "2026-10-29T12:00:00Z"
    immich.requests.clear()

    result = instance.start()

    assert "restore" in result["error"].lower() and "stack_account" in result["error"]
    assert immich.made("POST", "/api/admin/users.*") == []


def test_an_unreadable_stack_toml_removes_nothing(immich, instance):
    """Given the account and its key, when stack.toml cannot be read (the
    framework then answers an empty config), then not even the key goes."""
    instance.started()
    instance.stack = {}

    result = instance.start()

    assert "stack.toml" in result["error"]
    assert immich.made("DELETE", "/api/.*") == []
    assert instance.secrets["photos__API_KEY"]


# ── Switching on over a key set by hand ─────────────────────────────────


def test_a_key_set_by_hand_moves_to_the_import_key(immich, instance):
    """Given an admin set photos__API_KEY by hand for imports, when the
    switch is turned on, then that key moves to photos__IMPORT_API_KEY."""
    instance.secrets["photos__API_KEY"] = "set-by-hand"

    result = instance.started()

    assert instance.secrets["photos__IMPORT_API_KEY"] == "set-by-hand"
    assert any("moved" in n for n in result["notices"])


def test_a_key_set_by_hand_is_dropped_when_there_is_an_import_key(immich, instance):
    """Given an import key already, when the switch is turned on over a
    hand-set photos__API_KEY, then the import key stays and the answer
    says the other one was dropped."""
    instance.secrets["photos__API_KEY"] = "set-by-hand"
    instance.secrets["photos__IMPORT_API_KEY"] = "for-imports"

    result = instance.started()

    assert instance.secrets["photos__IMPORT_API_KEY"] == "for-imports"
    assert any("dropped" in n for n in result["notices"])


# ── Imports stay out of the stack's account ─────────────────────────────


def _album(immich, switch, secrets):
    config = {"is_healthy": lambda: True, "stack": {"photos": {"stack_account": switch}},
              "secrets": {**FAMILY_SECRETS, **secrets}}
    return album.run(["archive", "--pattern", "*.jpg"], {"port": immich.port}, config)


@pytest.mark.parametrize("switch", [True, False], ids=["on", "just-switched-off"])
def test_while_the_stack_has_an_account_album_does_not_use_its_key(immich, switch):
    """Given the stack holds its account's password (switched on, or off
    and not removed yet), when `stack photos album` runs with only
    photos__API_KEY, then it sends Immich nothing and asks for an import
    key: what it made would belong to the account, which switching off
    deletes."""
    result = _album(immich, switch, {"photos__API_KEY": "the-stacks-key",
                                     "photos__STACK_ACCOUNT_PASSWORD": "pw"})

    assert "photos__IMPORT_API_KEY" in result["error"]
    assert immich.requests == []


def test_without_a_stack_account_album_uses_a_key_set_by_hand(immich):
    """Given the stack made no account and an admin set photos__API_KEY
    by hand, when `stack photos album` runs, then it uses that key."""
    immich.api_keys["set-by-hand"] = immich.by_email(HOMER["email"])["id"]

    _album(immich, False, {"photos__API_KEY": "set-by-hand"})

    assert immich.made("POST", "/api/search/metadata")
