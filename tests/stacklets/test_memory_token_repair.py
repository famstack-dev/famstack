"""Keeping the vault's Forgejo credentials working, not merely current.

A remote URL holds three things that rot, and only two of them can be
re-derived. The host part is knowable: the current config says what it
should be. The path is fixed. The token is neither -- it is minted once
during install and read forever after, so when Forgejo expires it (or
the code stacklet is rebuilt and the account goes with it) nothing
anywhere holds a newer one.

That distinction is the whole subject of this file. Re-pointing a
remote whose token is dead writes the dead token back, faithfully,
every restart, while every host-side write fails 401 and the operator
sees a sync that "skipped". The cure is a new token, and the tests
below pin both halves of getting there: telling a rejected credential
apart from an unreachable host, and refusing to burn a good token when
Forgejo is merely still starting up.

The third test asserts where a write actually lands, because a write
addressed to the LAN IP is the failure that started all of this and no
amount of reading the code proves the rewrite happened.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))

from lib import (  # noqa: E402
    reissue_write_token,
    remote_rejects_credentials,
    update_memory,
)


@pytest.fixture
def auth_failing_remote(tmp_path, monkeypatch) -> str:
    """A git transport that always answers "your credentials are wrong".

    A real `git-remote-<scheme>` helper on PATH, so git produces the
    failure itself and the code under test reads the same stderr
    Forgejo's expired-token response produces, through the same path.
    """
    bindir = tmp_path / "fake-git-transports"
    bindir.mkdir()
    helper = bindir / "git-remote-authfail"
    helper.write_text(
        "#!/bin/sh\n"
        "echo \"fatal: Authentication failed for 'authfail://memory.git'\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return "authfail://memory.git"


class TestTellingTheTwoFailuresApart:
    """Only one kind of failure is cured by issuing a new token."""

    def test_a_rejected_credential_is_recognised_as_one(self, auth_failing_remote):
        assert remote_rejects_credentials(auth_failing_remote)

    def test_an_unreachable_remote_is_not_a_credential_problem(self, tmp_path):
        """The distinction that keeps a restart from destroying a good token.

        Forgejo is routinely unreachable for a few seconds while the
        code stacklet starts. Treating that as "the token is bad" would
        reissue on every cold boot and, worse, would do it on the one
        occasion the reissue itself is most likely to fail.
        """
        assert not remote_rejects_credentials(str(tmp_path / "not-a-repo.git"))


class TestReissuingIsBestEffort:
    """A repair that cannot happen must not fail the whole start hook."""

    def test_without_an_admin_password_no_token_is_issued(self):
        """Forgejo issues tokens only to the owning account, so admin
        rights alone are not enough -- the password is required."""
        assert reissue_write_token("http://127.0.0.1:9", "stackadmin", "") == ""

    def test_an_unreachable_forgejo_yields_no_token_rather_than_raising(self):
        """Port 9 (discard) refuses instantly. The caller keeps serving
        what is on disk; it does not take startup down with it."""
        assert reissue_write_token("http://127.0.0.1:9", "stackadmin", "pw") == ""


class TestWritesGoToTheHostsOwnAddress:

    def test_a_write_reaches_loopback_when_config_names_the_lan_address(
        self, httpserver,
    ):
        """`{code_url}` renders the address a phone on the couch clicks.

        Baked into a host-side write that is a time bomb: the next DHCP
        lease moves it and every todo tick starts failing. Here the
        configured URL points at TEST-NET-1 (RFC 5737, guaranteed
        unroutable) on the port the test server is really listening on,
        so the request can only arrive if it was rewritten to loopback.
        """
        httpserver.expect_request("/api/v1/version").respond_with_json({})

        result = update_memory(
            {
                "secrets": {
                    "memory__MEMORY_BOT_TOKEN": "t0ken",
                    "__code_url": f"http://192.0.2.1:{httpserver.port}",
                },
            },
            "family/education/todos.md",
            lambda text: text + "\n- [ ] something\n",
            actor="homer",
            message="chore(todos): homer added something",
        )

        assert httpserver.log, (
            "the write never reached loopback, so it was addressed to the "
            "LAN literal in the config"
        )
        # The transform's own outcome is beside the point here; what is
        # pinned is the address it was sent to.
        assert isinstance(result, dict)
