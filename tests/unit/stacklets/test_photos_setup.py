"""`stack photos setup`: give every family member an Immich account.

Setup signs up the tech admin through Immich's one-time admin sign-up,
logs in as it, and creates an account for each member of users.toml who
has photos. Once Immich has an admin, the sign-up is closed and answers
400. That is the normal case on every run after the first, and on the
first run too, because the install hook signs the tech admin up before
it calls setup.

Immich has worded that 400 differently across versions ("The server
already has an admin" in 2.x, "Admin setup is not available" in 3.x), so
a closed sign-up must not stop setup, whatever its message. Logging in
as the tech admin is what proves the account exists.

pytest-httpserver stands in for Immich, answering in the shapes its API
returns. The 3.x message is the one Immich v3.3.5 returned on the test
rig.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup_cli = _load("photos_cli_setup", _REPO_ROOT / "stacklets" / "photos" / "cli" / "setup.py")

HOMER = {"name": "Homer Simpson", "email": "homer@simpson.test",
         "role": "admin", "stacklets": ["photos"]}
MARGE = {"name": "Marge Simpson", "email": "marge@simpson.test",
         "role": "member", "stacklets": ["photos"]}
SECRETS = {"global__ADMIN_PASSWORD": "tech-admin-pw",
           "global__USER_HOMER_PASSWORD": "homer-pw",
           "global__USER_MARGE_PASSWORD": "marge-pw"}
TECH_ADMIN = {"id": "a0", "email": "stackadmin@home.local", "isAdmin": True}

SIGN_UP_CLOSED = [
    pytest.param("Admin setup is not available", id="immich-3"),
    pytest.param("The server already has an admin", id="immich-2"),
]


@pytest.fixture
def immich(httpserver):
    """Immich with an admin already in place: the sign-up is closed."""
    def _serve(closed_message, existing):
        httpserver.expect_request("/api/server/ping").respond_with_json({"res": "pong"})
        httpserver.expect_request(
            "/api/auth/admin-sign-up", method="POST").respond_with_json(
                {"message": closed_message}, status=400)
        httpserver.expect_request(
            "/api/auth/login", method="POST").respond_with_json(
                {"accessToken": "token"}, status=201)
        httpserver.expect_request(
            "/api/admin/users", method="GET").respond_with_json(existing)
        httpserver.expect_request(
            "/api/admin/users", method="POST").respond_with_json({}, status=201)
        return httpserver
    return _serve


def _setup(server, users):
    config = {"is_healthy": lambda: True, "users": users, "secrets": SECRETS}
    return setup_cli.run([], {"port": server.port}, config)


def _created(server):
    """The accounts setup asked Immich to create, by email."""
    return {request.get_json()["email"]: request.get_json().get("isAdmin", False)
            for request, _ in server.log
            if request.path == "/api/admin/users" and request.method == "POST"}


@pytest.mark.parametrize("closed_message", SIGN_UP_CLOSED)
def test_family_accounts_are_created_when_immich_already_has_an_admin(immich, closed_message):
    """The first run after install: the hook signed the tech admin up, so
    setup finds the sign-up closed and goes on to create the family."""
    server = immich(closed_message, existing=[TECH_ADMIN])

    result = _setup(server, [HOMER, MARGE])

    assert "error" not in result, result
    assert _created(server) == {"homer@simpson.test": True, "marge@simpson.test": False}


@pytest.mark.parametrize("closed_message", SIGN_UP_CLOSED)
def test_a_second_run_changes_nothing(immich, closed_message):
    """Everyone has an account already: setup reports them and creates none."""
    server = immich(closed_message, existing=[
        TECH_ADMIN,
        {"id": "h1", "email": "homer@simpson.test", "isAdmin": True},
        {"id": "m1", "email": "marge@simpson.test", "isAdmin": False},
    ])

    result = _setup(server, [HOMER, MARGE])

    assert "error" not in result, result
    assert _created(server) == {}
