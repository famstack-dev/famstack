"""Pipeline test for `install_memory_to_forgejo`.

End-to-end behaviour against a stubbed Forgejo: ensures the install
hook composes the underlying `ForgejoClient` calls in the right
order, with the right authorship, and persists what it should.

Three branches covered:

  - **Happy path with reusable token.** The caller already has a
    `memory-bot` token, so `issue_token` is not called. Org and repo
    are created. All seed files land via `put_file`.
  - **Token issuance.** No token cached, so the pipeline goes through
    the basic-auth-as-bot dance to issue a fresh one.
  - **Forgejo unreachable.** No further calls past `ping`; the result
    carries a `skipped_reason` so the hook can log and move on.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "memory"))

from lib import (  # noqa: E402
    BOT_USERNAME,
    REPO_NAME,
    REPO_OWNER,
    install_memory_to_forgejo,
)
from stack.forgejo import ForgejoClient  # noqa: E402


# ─── Helpers ─────────────────────────────────────────────────────────────

@pytest.fixture
def seed_dir(tmp_path):
    """A tiny seed layout: one top-level file + one nested file."""
    (tmp_path / "ontology.toml").write_text("[topic.x]\nnames = {en = 'X'}\n")
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "README.md").write_text("# wiki\n")
    return tmp_path


def _arm_happy_path_endpoints(httpserver, *, with_token_issuance: bool = False):
    """Pre-arm every Forgejo endpoint the install hits."""

    # 1. ping
    httpserver.expect_request("/api/v1/version").respond_with_json({"version": "11"})

    # 2. create_user (idempotent — 201 here, 422-already-exists also OK)
    httpserver.expect_request(
        "/api/v1/admin/users", method="POST",
    ).respond_with_json({"login": BOT_USERNAME}, status=201)

    # 3. issue_token (only when we don't pass one in)
    if with_token_issuance:
        # list existing → empty
        httpserver.expect_request(
            f"/api/v1/users/{BOT_USERNAME}/tokens", method="GET",
        ).respond_with_json([])
        # issue new
        httpserver.expect_request(
            f"/api/v1/users/{BOT_USERNAME}/tokens", method="POST",
        ).respond_with_json({"sha1": "fresh-bot-token"}, status=201)

    # 4. ensure_memory_repo: org missing → created; repo missing → created
    httpserver.expect_request(
        f"/api/v1/orgs/{REPO_OWNER}", method="GET",
    ).respond_with_data("not found", status=404)
    httpserver.expect_request(
        "/api/v1/orgs", method="POST",
    ).respond_with_json({"id": 1, "name": REPO_OWNER}, status=201)
    httpserver.expect_request(
        f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}", method="GET",
    ).respond_with_data("not found", status=404)
    httpserver.expect_request(
        f"/api/v1/orgs/{REPO_OWNER}/repos", method="POST",
    ).respond_with_json({"id": 2, "name": REPO_NAME}, status=201)

    # 5. team membership
    httpserver.expect_request(
        f"/api/v1/orgs/{REPO_OWNER}/teams", method="GET",
    ).respond_with_json([{"id": 99, "name": "Owners"}])
    httpserver.expect_request(
        f"/api/v1/teams/99/members/{BOT_USERNAME}", method="PUT",
    ).respond_with_data("", status=204)

    # 6. install_seeds — each file: GET → 404, POST → 201
    for repo_path in ("ontology.toml", "wiki/README.md"):
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}",
            method="GET",
        ).respond_with_data("not found", status=404)
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}",
            method="POST",
        ).respond_with_json({}, status=201)


# ─── Happy path ──────────────────────────────────────────────────────────

class TestHappyPath:

    def test_creates_repo_and_pushes_seeds_with_existing_token(
        self, httpserver, seed_dir,
    ):
        _arm_happy_path_endpoints(httpserver)

        result = install_memory_to_forgejo(
            code_url=httpserver.url_for(""),
            admin_user="stackadmin",
            admin_password="secret",
            bot_password="botpw",
            bot_token="existing-token",
            seed_dir=seed_dir,
        )

        assert result["created_org"] is True
        assert result["created_repo"] is True
        assert result["bot_token"] == "existing-token"
        assert sorted(result["seeds"]["created"]) == [
            "ontology.toml", "wiki/README.md",
        ]
        assert result["seeds"]["skipped"] == []

    def test_seed_commits_attributed_to_memory_bot(self, httpserver, seed_dir):
        _arm_happy_path_endpoints(httpserver)

        install_memory_to_forgejo(
            code_url=httpserver.url_for(""),
            admin_user="stackadmin",
            admin_password="secret",
            bot_password="botpw",
            bot_token="existing-token",
            seed_dir=seed_dir,
        )

        # Find the seed-file POSTs (path under /contents/) and verify
        # they carry memory-bot as the commit author.
        seed_posts = [
            req for req, _ in httpserver.log
            if req.method == "POST" and "/contents/" in req.path
        ]
        assert seed_posts, "expected at least one seed file POST"
        import json
        for req in seed_posts:
            body = json.loads(req.get_data())
            assert body["author"] == {
                "name": BOT_USERNAME, "email": "memory-bot@local",
            }


# ─── Token issuance ──────────────────────────────────────────────────────

class TestTokenIssuance:

    def test_issues_fresh_token_when_none_passed(self, httpserver, seed_dir):
        _arm_happy_path_endpoints(httpserver, with_token_issuance=True)

        result = install_memory_to_forgejo(
            code_url=httpserver.url_for(""),
            admin_user="stackadmin",
            admin_password="secret",
            bot_password="botpw",
            bot_token=None,
            seed_dir=seed_dir,
        )

        assert result["bot_token"] == "fresh-bot-token"


# ─── Forgejo unreachable ─────────────────────────────────────────────────

class TestForgejoUnreachable:

    def test_returns_skipped_reason_when_ping_fails(self, seed_dir):
        # Closed port — no httpserver in this test.
        result = install_memory_to_forgejo(
            code_url="http://127.0.0.1:1",
            admin_user="stackadmin",
            admin_password="secret",
            bot_password="botpw",
            bot_token="x",
            seed_dir=seed_dir,
        )
        assert result == {"skipped_reason": "forgejo unreachable"}
