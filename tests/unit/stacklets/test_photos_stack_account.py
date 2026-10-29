"""The stack's own Immich account: `[photos] stack_account` in stack.toml.

With the switch on, photos setup (which `stack up photos` runs) gives the
stack an Immich account of its own: not an admin, its password known only
to the stack. Every family member shares their library with it as a
partner, in its searches, and the account's API key is kept as
`photos__API_KEY`, the key core's photo search reads. A partner share is
read-only, so the stack can find every member's photos and change none.
With the switch off, setup removes the account and its key again.
Workspace decision 0035 holds the why and the measured Immich behaviour.

A stand-in Immich answers over HTTP with the routes and shapes Immich
3.3.5 answered on the test rig (the immich spike): accounts through the
admin API, partner shares made by the member and switched into the
timeline by the account, API keys owned by whoever creates them. It keeps
its state across runs, so a second run shows what setup asks for again.
"""

from __future__ import annotations

import re
import uuid

import pytest
from werkzeug.wrappers import Response

from stack.stack import stage_warning
from stacklets.photos.cli import album, setup

HOMER = {"id": "homer", "name": "Homer Simpson", "email": "homer@simpson.test",
         "role": "admin", "stacklets": ["photos"]}
MARGE = {"id": "marge", "name": "Marge Simpson", "email": "marge@simpson.test",
         "role": "member", "stacklets": ["photos"]}
BART = {"id": "bart", "name": "Bart Simpson", "email": "bart@simpson.test",
        "role": "member", "stacklets": ["photos"]}
FAMILY_SECRETS = {"global__ADMIN_PASSWORD": "tech-admin-pw",
                  "global__USER_HOMER_PASSWORD": "homer-pw",
                  "global__USER_MARGE_PASSWORD": "marge-pw",
                  "global__USER_BART_PASSWORD": "bart-pw"}


class Immich:
    """Immich 3.3.5's API, as far as photos setup uses it, with state."""

    def __init__(self, httpserver):
        self.users = {}       # id -> {id, email, name, isAdmin, password}
        self.tokens = {}      # bearer token or API key secret -> user id
        self.partners = {}    # (sharer id, shared-with id) -> inTimeline
        self.keys = {}        # key id -> {owner, name, secret}
        self.requests = []    # (method, path) of every request, in order
        self.add("stackadmin@home.local", "tech-admin-pw", is_admin=True)
        httpserver.expect_request(re.compile(".*")).respond_with_handler(self._handle)
        self.port = httpserver.port

    def add(self, email, password, is_admin=False, name=""):
        uid = str(uuid.uuid4())
        self.users[uid] = {"id": uid, "email": email, "name": name or email,
                           "isAdmin": is_admin, "password": password}
        return uid

    def by_email(self, email):
        return next((u for u in self.users.values() if u["email"] == email), None)

    def stack_account(self):
        """The accounts that are neither the tech admin nor a family member."""
        family = {HOMER["email"], MARGE["email"], BART["email"], "stackadmin@home.local"}
        return [u for u in self.users.values() if u["email"] not in family]

    def made(self, method, path_pattern):
        return [p for m, p in self.requests if m == method and re.fullmatch(path_pattern, p)]

    def _caller(self, request):
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ") or request.headers.get("x-api-key", "")
        return self.users.get(self.tokens.get(token))

    def _handle(self, request):
        self.requests.append((request.method, request.path))
        body = request.get_json(silent=True) or {}
        route = (request.method, request.path)
        me = self._caller(request)

        def json(data, status=200):
            import json as _json
            return Response(_json.dumps(data), status=status, content_type="application/json")

        if route == ("GET", "/api/server/ping"):
            return json({"res": "pong"})
        if route == ("POST", "/api/auth/admin-sign-up"):
            return json({"message": "Admin setup is not available"}, 400)
        if route == ("POST", "/api/auth/login"):
            user = self.by_email(body.get("email"))
            if not user or user["password"] != body.get("password"):
                return json({"message": "Incorrect email or password"}, 401)
            token = str(uuid.uuid4())
            self.tokens[token] = user["id"]
            return json({"accessToken": token, "userId": user["id"]}, 201)
        if me is None:
            return json({"message": "Authentication required"}, 401)

        if request.path.startswith("/api/admin/"):
            if not me["isAdmin"]:
                return json({"message": "Forbidden"}, 403)
            if route == ("GET", "/api/admin/users"):
                return json([{k: v for k, v in u.items() if k != "password"}
                             for u in self.users.values()])
            if route == ("POST", "/api/admin/users"):
                uid = self.add(body["email"], body["password"],
                               body.get("isAdmin", False), body["name"])
                return json({k: v for k, v in self.users[uid].items() if k != "password"}, 201)
            if m := re.fullmatch(r"/api/admin/users/([^/]+)", request.path):
                uid = m.group(1)
                if request.method == "PUT":
                    self.users[uid].update(body)
                    return json(self.users[uid])
                if request.method == "DELETE":
                    gone = self.users.pop(uid)
                    self.partners = {k: v for k, v in self.partners.items() if uid not in k}
                    self.keys = {k: v for k, v in self.keys.items() if v["owner"] != uid}
                    return json({k: v for k, v in gone.items() if k != "password"})

        if route == ("POST", "/api/partners"):
            self.partners[(me["id"], body["sharedWithId"])] = False
            return json({"id": body["sharedWithId"]}, 201)
        if route == ("GET", "/api/partners"):
            assert request.args.get("direction") == "shared-with"
            return json([{"id": sharer, "email": self.users[sharer]["email"],
                          "name": self.users[sharer]["name"], "inTimeline": shown}
                         for (sharer, reader), shown in self.partners.items()
                         if reader == me["id"]])
        if m := re.fullmatch(r"/api/partners/([^/]+)", request.path):
            self.partners[(m.group(1), me["id"])] = body["inTimeline"]
            return json({"id": m.group(1), "inTimeline": body["inTimeline"]})

        if route == ("GET", "/api/api-keys"):
            return json([{"id": kid, "name": k["name"]}
                         for kid, k in self.keys.items() if k["owner"] == me["id"]])
        if route == ("POST", "/api/api-keys"):
            kid, secret = str(uuid.uuid4()), str(uuid.uuid4())
            self.keys[kid] = {"owner": me["id"], "name": body["name"], "secret": secret}
            self.tokens[secret] = me["id"]
            return json({"secret": secret, "apiKey": {"id": kid, "name": body["name"]}}, 201)
        if m := re.fullmatch(r"/api/api-keys/([^/]+)", request.path):
            gone = self.keys.pop(m.group(1))
            self.tokens.pop(gone["secret"], None)
            return Response(status=204)
        return json({"message": f"no stand-in for {route}"}, 404)


@pytest.fixture
def immich(httpserver):
    """Immich after install: the tech admin and the family's accounts."""
    server = Immich(httpserver)
    for person in (HOMER, MARGE, BART):
        server.add(person["email"], f"{person['id']}-pw", person["role"] == "admin",
                   person["name"])
    return server


class Instance:
    """What `stack photos setup` gets from the framework: stack.toml,
    users.toml, secrets, and a writer for the photos secrets."""

    def __init__(self, immich, switch=None):
        self.immich = immich
        self.stack = {"photos": {"stack_account": switch}} if switch is not None else {}
        self.secrets = dict(FAMILY_SECRETS)
        self.users = [HOMER, MARGE, BART]

    def set_secret(self, name, value):
        self.secrets[f"photos__{name}"] = value

    def setup(self):
        config = {"is_healthy": lambda: True, "users": self.users,
                  "stack": self.stack, "secrets": dict(self.secrets),
                  "set_secret": self.set_secret}
        return setup.run([], {"port": self.immich.port}, config)


def _shares(immich, account):
    """Who shares their library with the account, and whether it is in
    the account's searches."""
    return {immich.users[sharer]["email"]: shown
            for (sharer, reader), shown in immich.partners.items() if reader == account["id"]}


# ── Switched on ─────────────────────────────────────────────────────────


def test_the_stack_gets_its_own_account_that_reads_every_library(immich):
    """Given the switch is on, when photos setup runs, then Immich has one
    non-admin account for the stack, every member shares their library
    with it in its searches, and its key is kept where core reads it.
    This is what lets one key find every member's photos."""
    instance = Instance(immich, switch=True)

    result = instance.setup()

    assert "error" not in result, result
    [account] = immich.stack_account()
    assert not account["isAdmin"]
    assert account["password"] == instance.secrets["photos__STACK_ACCOUNT_PASSWORD"]
    assert _shares(immich, account) == {
        HOMER["email"]: True, MARGE["email"]: True, BART["email"]: True}
    [key] = immich.keys.values()
    assert key["owner"] == account["id"]
    assert instance.secrets["photos__API_KEY"] == key["secret"]
    assert result["stack_account"]["warning"] == stage_warning(
        "incubating", result["stack_account"]["feature"])
    assert "./stack restart core" in result["stack_account"]["next"]


def test_a_second_run_changes_nothing(immich):
    """Given the account, its shares and its key, when setup runs again,
    then it creates no second account, share or key, and asks for no
    core restart. `stack up photos` runs setup on every start."""
    instance = Instance(immich, switch=True)
    instance.setup()
    immich.requests.clear()

    result = instance.setup()

    assert "error" not in result, result
    assert immich.made("POST", "/api/admin/users") == []
    assert immich.made("POST", "/api/partners") == []
    assert immich.made("PUT", "/api/partners/.*") == []
    assert immich.made("POST", "/api/api-keys") == []
    assert len(immich.stack_account()) == 1 and len(immich.keys) == 1
    assert "next" not in result["stack_account"]


def test_a_lost_key_is_replaced_and_the_old_one_removed(immich):
    """Given the stack lost its key (secrets restored from an old backup),
    when setup runs, then the account gets one new key and the old one is
    deleted from Immich, so no key is left that nothing uses."""
    instance = Instance(immich, switch=True)
    instance.setup()
    [old] = immich.keys
    del instance.secrets["photos__API_KEY"]

    result = instance.setup()

    assert "error" not in result, result
    [new] = immich.keys
    assert new != old
    assert instance.secrets["photos__API_KEY"] == immich.keys[new]["secret"]
    assert "./stack restart core" in result["stack_account"]["next"]


def test_a_member_the_stack_cannot_log_in_as_is_named(immich):
    """Given Marge changed her password in the app, when setup runs, then
    Homer and Bart share their libraries, and the answer names Marge so
    the admin can ask her to share hers by hand."""
    immich.by_email(MARGE["email"])["password"] = "changed-in-the-app"
    instance = Instance(immich, switch=True)

    result = instance.setup()

    assert "error" not in result, result
    [account] = immich.stack_account()
    assert _shares(immich, account) == {HOMER["email"]: True, BART["email"]: True}
    assert result["stack_account"]["share_by_hand"] == ["marge"]


def test_a_member_added_later_is_shared_on_the_next_run(immich):
    """Given the account and Homer's and Marge's shares, when Bart is
    added to users.toml and setup runs, then Bart's library is shared."""
    instance = Instance(immich, switch=True)
    instance.users = [HOMER, MARGE]
    instance.setup()
    [account] = immich.stack_account()
    assert BART["email"] not in _shares(immich, account)

    instance.users = [HOMER, MARGE, BART]
    instance.setup()

    assert _shares(immich, account)[BART["email"]] is True


# ── Switched off ────────────────────────────────────────────────────────


def test_switching_off_removes_the_account_and_its_key(immich):
    """Given the account exists, when the switch is turned off and setup
    runs, then the account and its key are gone from Immich and from the
    stack's secrets. The members' accounts stay."""
    instance = Instance(immich, switch=True)
    instance.setup()
    [account] = immich.stack_account()

    instance.stack = {"photos": {"stack_account": False}}
    result = instance.setup()

    assert "error" not in result, result
    assert immich.made("DELETE", f"/api/admin/users/{account['id']}")
    assert immich.stack_account() == [] and immich.keys == {}
    assert not instance.secrets.get("photos__API_KEY")
    assert not instance.secrets.get("photos__STACK_ACCOUNT_PASSWORD")
    assert {u["email"] for u in immich.users.values()} >= {
        HOMER["email"], MARGE["email"], BART["email"]}


@pytest.mark.parametrize("switch", [None, False], ids=["unset", "off"])
def test_without_the_switch_setup_asks_immich_nothing_new(immich, switch):
    """Given no stack account and the switch unset or off, when setup
    runs, then Immich gets the requests setup sent before the switch
    existed, and the answer has no stack account part."""
    instance = Instance(immich, switch=switch)

    result = instance.setup()

    assert "error" not in result, result
    assert immich.requests == [("GET", "/api/server/ping"),
                               ("POST", "/api/auth/admin-sign-up"),
                               ("POST", "/api/auth/login"),
                               ("GET", "/api/admin/users")]
    assert "stack_account" not in result


# ── Imports stay out of the stack's account ─────────────────────────────


def test_album_does_not_use_the_stacks_key(immich):
    """Given only the stack's key, when `stack photos album` runs, then it
    sends Immich nothing and asks for an import key, because albums made
    with the stack's key would belong to the stack's account."""
    config = {"is_healthy": lambda: True,
              "secrets": {**FAMILY_SECRETS, "photos__API_KEY": "the-stacks-key"}}

    result = album.run(["archive", "--pattern", "*.jpg"], {"port": immich.port}, config)

    assert "photos__IMPORT_API_KEY" in result["error"]
    assert immich.requests == []
