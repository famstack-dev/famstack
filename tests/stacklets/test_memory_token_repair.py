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

A token that was never stored is the same hole seen from the other
side, and it is the one production hit: an instance installed before
the install hook learned to persist a token has none, `stack up memory`
had nothing to test and so repaired nothing, and every vault write
failed with "Forgejo credentials missing" until someone re-ran setup by
hand. The start hook is driven end to end below for that case, because
the bug lived in the branch that decides whether to repair at all.

The third test asserts where a write actually lands, because a write
addressed to the LAN IP is the failure that started all of this and no
amount of reading the code proves the rewrite happened.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "hooks"))

import on_start_ready  # noqa: E402
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


class _RecordingCtx:
    """The slice of hook context `on_start_ready` actually reads.

    A real `StackContext` wants an instance on disk, a parsed config and
    a Docker runtime. The hook wants four things: the data dir, the
    rendered env, the secret store, and somewhere to log progress.
    Standing up only those four is what lets the hook be driven end to
    end here, which matters because the bug this file guards lived in
    `run()` itself and not in any helper it calls.
    """

    def __init__(self, data_dir: Path, env: dict, secrets: dict):
        self.stack = SimpleNamespace(data=data_dir)
        self.env = env
        self.secrets = secrets
        self.steps: list[str] = []

    def secret(self, name, value=None):
        if value is not None:
            self.secrets[name] = value
            return value
        return self.secrets.get(name)      # None when unset, as the store does

    def step(self, message):
        self.steps.append(message)


@pytest.fixture
def forgejo_issuing_tokens(httpserver) -> str:
    """A Forgejo that is up and will mint `fresh-t0ken` on request.

    Only the three calls `reissue_write_token` makes are served. Every
    other path 500s, which is deliberate: the clone that follows the
    repair then fails and the hook returns early, so these tests stay
    about the credential decision and nothing downstream of it.
    """
    httpserver.expect_request("/api/v1/version").respond_with_json({})
    httpserver.expect_request(
        "/api/v1/users/stackadmin/tokens", method="GET",
    ).respond_with_json([])
    httpserver.expect_request(
        "/api/v1/users/stackadmin/tokens", method="POST",
    ).respond_with_json({"sha1": "fresh-t0ken"})
    return httpserver.url_for("").rstrip("/")


class TestAnInstanceThatNeverStoredAToken:
    """The production failure: no token at all, and no way back.

    Installs predating the persisting install hook hold no
    `MEMORY_BOT_TOKEN`. Every host-side write answered "Forgejo
    credentials missing" and every restart repaired nothing, because
    the repair only ran once there was a remote to test and a missing
    token builds no remote. The cure for "never had one" and for
    "Forgejo rejected it" is the same call; only the trigger was wrong.
    """

    def _ctx(self, tmp_path, code_url, secrets):
        return _RecordingCtx(
            tmp_path / "data",
            {
                "CODE_URL": code_url,
                "ADMIN_USER": "stackadmin",
                "ADMIN_PASSWORD": "hunter2",
            },
            secrets,
        )

    def test_a_missing_token_is_minted_on_the_next_start(
        self, tmp_path, forgejo_issuing_tokens,
    ):
        ctx = self._ctx(tmp_path, forgejo_issuing_tokens, {})

        on_start_ready.run(ctx)

        assert ctx.secrets.get("MEMORY_BOT_TOKEN") == "fresh-t0ken", (
            "an instance with no stored token stayed unable to write to its "
            "own vault, which is the state production was found in"
        )

    def test_the_operator_is_told_a_credential_was_created(
        self, tmp_path, forgejo_issuing_tokens,
    ):
        """A credential appearing out of nowhere is worth one line.

        Someone reading `stack up memory` should be able to connect a
        vault that started working to the run that fixed it.
        """
        ctx = self._ctx(tmp_path, forgejo_issuing_tokens, {})

        on_start_ready.run(ctx)

        assert any("token" in step for step in ctx.steps)

    def test_a_token_already_on_file_is_left_alone(
        self, tmp_path, forgejo_issuing_tokens,
    ):
        """Reissuing is a repair, not a routine.

        Forgejo deletes the old token when it issues a same-named
        replacement, so minting on every start would invalidate the
        credential any concurrent writer is holding. Here the remote
        never answers "authentication failed" -- it is merely
        unreachable for git -- so the stored token stands.
        """
        ctx = self._ctx(tmp_path, forgejo_issuing_tokens,
                        {"MEMORY_BOT_TOKEN": "already-good"})

        on_start_ready.run(ctx)

        assert ctx.secrets["MEMORY_BOT_TOKEN"] == "already-good"

    def test_no_token_is_invented_when_forgejo_is_down(self, tmp_path):
        """Port 9 (discard) refuses instantly.

        The hook must still fall through to its existing "credentials
        missing, skipping" path rather than failing the start. A stack
        coming up with the code stacklet not yet listening is normal.
        """
        ctx = self._ctx(tmp_path, "http://127.0.0.1:9", {})

        on_start_ready.run(ctx)

        assert not ctx.secrets.get("MEMORY_BOT_TOKEN")


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
