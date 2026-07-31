"""What `MatrixClient.create_user` promises about existing accounts.

Synapse's admin API creates users with `PUT /_synapse/admin/v2/users/{id}`,
an upsert. The sharp edge is that including `password` in that PUT does not
merely record a credential: Synapse also invalidates the account's devices.
Re-asserting the same password therefore still logs the account out.

That is fine for a human, who logs back in. It is fatal for a bot, which
has nothing watching it, and it is how the whole stack once ended up with a
stacker-bot that could not authenticate (FAM-2).

These tests drive the client against `pytest-httpserver` and assert on the
request body that reaches the wire, because the contract we care about is
what Synapse is told, not how the method arranges its locals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "cli"))

from _matrix import MatrixClient  # noqa: E402


ADMIN_PATH = "/_synapse/admin/v2/users/@stacker-bot:simpson"


@pytest.fixture
def client(httpserver: HTTPServer):
    c = MatrixClient(httpserver.url_for(""), "simpson", str(_REPO_ROOT))
    c.token = "admin-token"
    return c


def _put_bodies(httpserver):
    """The JSON body of every PUT that reached the server, in order."""
    return [
        json.loads(req.get_data())
        for req, _ in httpserver.log
        if req.method == "PUT"
    ]


def test_existing_account_keeps_its_password(httpserver, client):
    """`reset_password=False` must not re-send the password of a live account.

    This is the FAM-2 invariant. Setup re-runs against an instance whose bot
    is already provisioned and running, so the PUT has to be a profile
    update only.
    """
    httpserver.expect_request(ADMIN_PATH, method="GET").respond_with_json(
        {"name": "@stacker-bot:simpson"}
    )
    httpserver.expect_request(ADMIN_PATH, method="PUT").respond_with_json({}, status=200)

    assert client.create_user("stacker-bot", "s3cret", displayname="Stacker",
                              reset_password=False)

    body = _put_bodies(httpserver)[0]
    assert "password" not in body, "an existing bot must not be re-credentialed"
    assert body["displayname"] == "Stacker"


def test_missing_account_is_still_created_with_a_password(httpserver, client):
    """`reset_password=False` still provisions an account that does not exist.

    Otherwise a fresh install would produce a passwordless bot, which is a
    worse failure than the one this flag exists to prevent.
    """
    httpserver.expect_request(ADMIN_PATH, method="GET").respond_with_json(
        {"errcode": "M_NOT_FOUND"}, status=404
    )
    httpserver.expect_request(ADMIN_PATH, method="PUT").respond_with_json({}, status=201)

    assert client.create_user("stacker-bot", "s3cret", reset_password=False)

    assert _put_bodies(httpserver)[0]["password"] == "s3cret"


def test_password_is_reset_by_default(httpserver, client):
    """The default stays a full upsert, for callers that do own the credential.

    Family accounts are provisioned from `users.toml` on every setup run and
    are meant to converge on the stored secret.
    """
    httpserver.expect_request(ADMIN_PATH, method="PUT").respond_with_json({}, status=200)

    assert client.create_user("stacker-bot", "s3cret")

    assert _put_bodies(httpserver)[0]["password"] == "s3cret"
    assert not [req for req, _ in httpserver.log if req.method == "GET"], \
        "the default path should not need an existence check"
