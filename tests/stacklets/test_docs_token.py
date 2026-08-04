"""The Paperless token, and the instances that lost it.

The archivist's ability to file a document rests entirely on this
token: `core` renders it into the bot runner as `PAPERLESS_TOKEN`, and
the start hook seeds tags and taxonomy with it. It was obtained during
install and never again, which is fine until it isn't -- a
`stack destroy docs` cycle gives Paperless a new database and
invalidates it, and an instance installed before the stacklet stored
one has none at all. Both leave the install hook already spent, so no
restart could recover, and the failure showed up as documents that
never got filed rather than as an error.

These tests are written against Paperless's actual token contract (a
`POST /api/token/` answering `{"token": ...}`, a bearer check that
401s) rather than against a recording of our own client, so they still
mean something if the implementation is rewritten.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs"))
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stack.hooks import StackContext  # noqa: E402

from auth import ensure_api_token  # noqa: E402  isort:skip


class _Secrets:
    """The slice of the secret store `ctx.secret` drives, with its
    stacklet-then-global fallback intact -- ADMIN_PASSWORD is a global
    secret and the token is a docs-scoped one, so a store that ignored
    the distinction would let a broken lookup pass."""

    def __init__(self, values: dict):
        self.values = dict(values)

    def get(self, stacklet_id, name):
        return (self.values.get(f"{stacklet_id}__{name}")
                or self.values.get(f"global__{name}"))

    def set(self, stacklet_id, name, value):
        self.values[f"{stacklet_id}__{name}"] = value


def _ctx(url: str, secrets: dict, admin_user: str = "stackadmin"):
    """A real StackContext over a fake secret store.

    The context's own HTTP helpers are the ones under test as much as
    anything else -- they are what turns a 401 into the exception the
    token check reads -- so they are left alone and pointed at a real
    server.
    """
    steps: list[str] = []
    ctx = StackContext(
        stack=SimpleNamespace(secrets=_Secrets(secrets)),
        stacklet_id="docs",
        env={"PAPERLESS_URL": url, "ADMIN_USER": admin_user},
        step_fn=steps.append,
    )
    ctx.steps = steps          # for assertions on what the operator saw
    return ctx


@pytest.fixture
def paperless(httpserver):
    """A Paperless that issues `fresh-t0ken` and honours only that token."""
    httpserver.expect_request(
        "/api/token/", method="POST",
    ).respond_with_json({"token": "fresh-t0ken"})
    httpserver.expect_request(
        "/api/documents/", method="GET",
        headers={"Authorization": "Token fresh-t0ken"},
    ).respond_with_json({"count": 0})
    return httpserver.url_for("").rstrip("/")


class TestAnInstanceWithNoToken:
    """The production shape: installed before the token was ever stored."""

    def test_a_token_is_obtained_and_saved(self, paperless):
        ctx = _ctx(paperless, {"global__ADMIN_PASSWORD": "hunter2"})

        assert ensure_api_token(ctx) == "fresh-t0ken"
        assert ctx.secret("API_TOKEN") == "fresh-t0ken", (
            "the token was used for this run but never persisted, so the "
            "archivist -- which reads it from the secret store -- stays blind"
        )

    def test_nothing_is_attempted_without_admin_credentials(self, paperless):
        """Paperless issues tokens against a real login, so with no admin
        password there is nothing to ask with. Saying so beats a stack
        trace from a request that could never have worked."""
        ctx = _ctx(paperless, {})

        assert ensure_api_token(ctx) == ""
        assert any("admin credentials" in s for s in ctx.steps)


class TestATokenThatStoppedWorking:
    """A destroy + up cycle hands Paperless a new database, and every
    token minted against the old one is now a 401."""

    def test_a_rejected_token_is_replaced(self, paperless):
        ctx = _ctx(paperless, {
            "docs__API_TOKEN": "from-the-old-database",
            "global__ADMIN_PASSWORD": "hunter2",
        })

        assert ensure_api_token(ctx) == "fresh-t0ken"
        assert ctx.secret("API_TOKEN") == "fresh-t0ken"

    def test_a_working_token_is_reused(self, paperless):
        """Not merely an optimisation. The token reaches the archivist
        through rendered container env, so churning it on every start
        would leave a running bot holding one Paperless has moved on
        from until something recreated the container."""
        ctx = _ctx(paperless, {
            "docs__API_TOKEN": "fresh-t0ken",
            "global__ADMIN_PASSWORD": "hunter2",
        })

        assert ensure_api_token(ctx) == "fresh-t0ken"
        assert not any("Obtaining" in s for s in ctx.steps)


class TestPaperlessNotAnswering:

    def test_no_token_is_invented_when_the_service_is_down(self):
        """Port 9 (discard) refuses instantly. A stack coming up with
        Paperless still migrating is ordinary, so this must return
        empty-handed rather than raise and take the hook down."""
        ctx = _ctx("http://127.0.0.1:9", {"global__ADMIN_PASSWORD": "hunter2"})

        assert ensure_api_token(ctx) == ""

    def test_a_stored_token_survives_an_outage(self):
        """The one case worth being careful about: an unreachable
        Paperless looks exactly like a rejected token from here. Failing
        to reach it must not clear what is on file, or a restart during
        an outage would cost the instance a credential it still had."""
        ctx = _ctx("http://127.0.0.1:9", {
            "docs__API_TOKEN": "still-good",
            "global__ADMIN_PASSWORD": "hunter2",
        })

        ensure_api_token(ctx)

        assert ctx.secret("API_TOKEN") == "still-good"
