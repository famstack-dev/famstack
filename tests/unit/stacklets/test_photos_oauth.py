"""Immich login through the stack's OIDC provider.

The photos stacklet is an OIDC client. Immich does not read its OAuth
settings from the environment, and its config file (IMMICH_CONFIG_FILE)
replaces the whole system config and locks every setting in the admin
UI. So the stacklet writes the OAuth section through Immich's admin
API, and only the keys it owns: whether OAuth is on, the issuer, the
client credentials and the button text. Everything else in the system
config, OAuth claims included, stays as the admin set it.

Immich's `PUT /api/system-config` takes the whole config, so the fake
here stores and returns the whole config as Immich does. It also counts
logouts, because Immich lists every session left open as a device of
the tech admin.
"""

from __future__ import annotations

import copy
import json

import pytest

from stack import Stack
from stacklets.photos import oauth
from tests.springfield import REPO


# ── The manifest side: what the provider registers ──────────────────────

@pytest.fixture
def instance(tmp_path):
    """The repo's stacklets plus a provider extension, in domain mode."""
    (tmp_path / "stack.toml").write_text(
        '[core]\ndomain = "home.example.family"\n'
        f'extension_dirs = ["{tmp_path / "ext"}"]\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / "ext" / "idp").mkdir(parents=True)
    (tmp_path / "ext" / "idp" / "stacklet.toml").write_text(
        'id = "idp"\nport = 42100\n[oidc_provider]\n')
    return Stack(root=REPO, data=tmp_path / "data", instance_dir=tmp_path)


def test_the_provider_registers_the_web_and_the_mobile_callbacks(instance):
    """Immich redirects to /auth/login and /user-settings in the browser,
    and the mobile app to app.immich:///oauth-callback. A callback the
    provider does not know fails at login time with a provider error."""
    [photos] = [c for c in instance.oidc_clients() if c["stacklet"] == "photos"]
    assert sorted(photos["callbacks"]) == sorted([
        "http://photos.home.example.family/auth/login",
        "http://photos.home.example.family/user-settings",
        "app.immich:///oauth-callback",
    ])


def test_registered_credentials_reach_the_hook(instance):
    instance.store_oidc_client("photos", client_id="c-1", client_secret="s-1")
    env = instance.env("photos")
    assert (env["OIDC_ISSUER"], env["OIDC_CLIENT_ID"], env["OIDC_CLIENT_SECRET"]) == (
        "http://idp.home.example.family", "c-1", "s-1")


# ── The Immich side: what the hook writes ────────────────────────────────

ADMIN = ("stackadmin@home.local", "admin-pass")
ENV = {"OIDC_ISSUER": "https://id.home.example.family",
       "OIDC_CLIENT_ID": "c-1", "OIDC_CLIENT_SECRET": "s-1"}

# A slice of Immich's system config with settings an admin changed.
ADMIN_CONFIG = {
    "oauth": {"enabled": False, "issuerUrl": "", "clientId": "",
              "clientSecret": "", "buttonText": "Login with OAuth",
              "storageLabelClaim": "email", "autoRegister": False},
    "passwordLogin": {"enabled": True},
    "server": {"externalDomain": "https://photos.home.example.family"},
}


class FakeImmich:
    """Immich's login, logout and system-config endpoints, holding one
    config. `refuse_put` answers every update with a 400."""

    def __init__(self, httpserver, config, refuse_put=False):
        self.config = copy.deepcopy(config)
        self.puts = 0
        self.refuse_put = refuse_put
        self.logouts = []
        httpserver.expect_request("/api/auth/login", method="POST").respond_with_handler(self._login)
        httpserver.expect_request("/api/auth/logout", method="POST").respond_with_handler(self._logout)
        httpserver.expect_request("/api/system-config", method="GET").respond_with_handler(self._get)
        httpserver.expect_request("/api/system-config", method="PUT").respond_with_handler(self._put)
        self.url = httpserver.url_for("").rstrip("/")

    def _login(self, request):
        from werkzeug import Response
        body = json.loads(request.data)
        if (body["email"], body["password"]) != ADMIN:
            return Response(json.dumps({"message": "Incorrect email or password"}), 401)
        return Response(json.dumps({"accessToken": "t-1"}), 201,
                        content_type="application/json")

    def _logout(self, request):
        from werkzeug import Response
        self.logouts.append(request.headers.get("Authorization"))
        return Response(json.dumps({"successful": True}), 200, content_type="application/json")

    def _authorized(self, request):
        return request.headers.get("Authorization") == "Bearer t-1"

    def _get(self, request):
        from werkzeug import Response
        if not self._authorized(request):
            return Response("{}", 401)
        return Response(json.dumps(self.config), 200, content_type="application/json")

    def _put(self, request):
        from werkzeug import Response
        if not self._authorized(request):
            return Response("{}", 401)
        if self.refuse_put:
            return Response(json.dumps({"message": "oauth.issuerUrl must be a URL"}), 400,
                            content_type="application/json")
        self.puts += 1
        self.config = json.loads(request.data)
        return Response(json.dumps(self.config), 200, content_type="application/json")


def _sync(immich, env=ENV, password=ADMIN[1]):
    steps = []
    oauth.sync(immich.url, ADMIN[0], password, env, steps.append)
    return steps


def test_turns_the_login_on_with_the_provider(httpserver):
    immich = FakeImmich(httpserver, ADMIN_CONFIG)
    _sync(immich)
    got = immich.config["oauth"]
    assert (got["enabled"], got["issuerUrl"], got["clientId"], got["clientSecret"]) == (
        True, "https://id.home.example.family", "c-1", "s-1")
    assert got["buttonText"] == "Sign in with family account"


def test_leaves_every_setting_it_does_not_own(httpserver):
    """The admin chose the email as storage label and no auto signup;
    the password login and the server settings are theirs too."""
    immich = FakeImmich(httpserver, ADMIN_CONFIG)
    _sync(immich)
    assert immich.config["oauth"]["storageLabelClaim"] == "email"
    assert immich.config["oauth"]["autoRegister"] is False
    assert immich.config["passwordLogin"] == ADMIN_CONFIG["passwordLogin"]
    assert immich.config["server"] == ADMIN_CONFIG["server"]


def test_says_what_changed_and_never_the_secret(httpserver):
    immich = FakeImmich(httpserver, ADMIN_CONFIG)
    steps = _sync(immich)
    assert len(steps) == 1
    assert "clientSecret" in steps[0] and "s-1" not in steps[0]


def test_a_second_run_writes_nothing_and_says_nothing(httpserver):
    immich = FakeImmich(httpserver, ADMIN_CONFIG)
    _sync(immich)
    steps = _sync(immich)
    assert immich.puts == 1
    assert steps == []


def test_without_credentials_immich_is_not_touched(httpserver):
    """No provider, or not registered yet: a login an admin set up by
    hand in the Immich UI must survive `stack up photos`."""
    immich = FakeImmich(httpserver, ADMIN_CONFIG)
    steps = _sync(immich, env={"OIDC_ISSUER": "", "OIDC_CLIENT_ID": "", "OIDC_CLIENT_SECRET": ""})
    assert immich.puts == 0
    assert steps == []
    assert httpserver.log == []


def test_a_refused_login_is_reported_not_raised(httpserver):
    """A failed sync must not stop `stack up photos`: Immich and its
    password login still work, and the next run tries again."""
    immich = FakeImmich(httpserver, ADMIN_CONFIG)
    steps = _sync(immich, password="wrong")
    assert immich.puts == 0
    assert len(steps) == 1 and "401" in steps[0]


# ── The session it opens ────────────────────────────────────────────────

SYNCED_CONFIG = {**ADMIN_CONFIG, "oauth": {**ADMIN_CONFIG["oauth"], **oauth.desired(ENV)}}


@pytest.mark.parametrize("config, refuse_put", [
    pytest.param(SYNCED_CONFIG, False, id="nothing-changed"),
    pytest.param(ADMIN_CONFIG, False, id="updated"),
    pytest.param(ADMIN_CONFIG, True, id="update-refused"),
])
def test_closes_the_session_it_opened(httpserver, config, refuse_put):
    """Given an OIDC provider with credentials, when `stack up photos`
    syncs the login settings, then the tech admin's login is closed
    again, whatever the sync found: an open session stays listed as a
    device of the tech admin, one more on every `stack up photos`."""
    immich = FakeImmich(httpserver, config, refuse_put=refuse_put)
    _sync(immich)
    assert immich.logouts == ["Bearer t-1"]


def test_a_refused_update_is_reported_not_raised(httpserver):
    immich = FakeImmich(httpserver, ADMIN_CONFIG, refuse_put=True)
    steps = _sync(immich)
    assert immich.puts == 0
    assert len(steps) == 1 and "400" in steps[0]
