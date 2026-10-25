"""Install-side tests for the memory stacklet.

Covers the two install primitives in `stacklets/memory/lib.py`:

  - `ensure_memory_repo(client)` — creates the `family` org and
    `memory` repo if they don't exist. Idempotent.
  - `seed_files(seed_dir)` — what a new memory starts with, which the
    curator gives it as its first commit.

Forgejo is stubbed with `pytest-httpserver` so the HTTP shape, JSON
parsing, and `ForgejoClient` are exercised for real.
"""

from __future__ import annotations

import base64

import pytest


from werkzeug.wrappers import Response  # noqa: E402

from stacklets.memory.lib import (  # noqa: E402
    add_admins_to_the_family,
    BRAIN_REPO_NAME,
    REPO_NAME,
    REPO_OWNER,
    brain_remote_url,
    ensure_brain_repo,
    ensure_brain_projection_admin,
    ensure_memory_repo,
    seed_brain,
    seed_files,
)
from stacklets.memory import lib as memory_lib  # noqa: E402
from stack.forgejo import ForgejoClient  # noqa: E402

# Every write names its committer, which the client asks Forgejo for.
pytestmark = pytest.mark.usefixtures("forgejo_accounts")


# ─── Helpers ─────────────────────────────────────────────────────────────

def _admin_client(httpserver) -> ForgejoClient:
    return ForgejoClient(
        url=httpserver.url_for(""),
        admin_user="stackadmin",
        admin_password="secret",
        token="installtoken",
    )


def _file_response(path: str, content: str = "") -> dict:
    return {
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "encoding": "base64",
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "sha": f"sha-{path}",
    }


# ─── ensure_memory_repo ──────────────────────────────────────────────────

class TestEnsureMemoryRepo:

    def test_creates_both_when_neither_exists(self, httpserver):
        # GET org → 404 (not found). create_org → 201.
        httpserver.expect_request(
            f"/api/v1/orgs/{REPO_OWNER}", method="GET",
        ).respond_with_data("not found", status=404)
        httpserver.expect_request(
            "/api/v1/orgs", method="POST",
        ).respond_with_json({"id": 1, "name": REPO_OWNER}, status=201)

        # GET repo → 404, create_repo (on org path) → 201.
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}", method="GET",
        ).respond_with_data("not found", status=404)
        httpserver.expect_request(
            f"/api/v1/orgs/{REPO_OWNER}/repos", method="POST",
        ).respond_with_json({"id": 2, "name": REPO_NAME}, status=201)

        result = ensure_memory_repo(_admin_client(httpserver))

        assert result == {"created_org": True, "created_repo": True}

    def test_creates_nothing_when_both_exist(self, httpserver):
        httpserver.expect_request(
            f"/api/v1/orgs/{REPO_OWNER}", method="GET",
        ).respond_with_json({"id": 1, "name": REPO_OWNER})
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}", method="GET",
        ).respond_with_json({"id": 2, "name": REPO_NAME})

        result = ensure_memory_repo(_admin_client(httpserver))

        assert result == {"created_org": False, "created_repo": False}

    def test_creates_only_repo_when_org_already_present(self, httpserver):
        httpserver.expect_request(
            f"/api/v1/orgs/{REPO_OWNER}", method="GET",
        ).respond_with_json({"id": 1, "name": REPO_OWNER})

        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}", method="GET",
        ).respond_with_data("not found", status=404)
        httpserver.expect_request(
            f"/api/v1/orgs/{REPO_OWNER}/repos", method="POST",
        ).respond_with_json({"id": 2, "name": REPO_NAME}, status=201)

        result = ensure_memory_repo(_admin_client(httpserver))

        assert result == {"created_org": False, "created_repo": True}


# ─── install_seeds ───────────────────────────────────────────────────────

class TestSeedFiles:
    """What a new memory starts with: the shipped seed files, which the
    curator gives it as its first commit (`test_memory_curator_sync.py`)."""

    @pytest.fixture
    def seed_dir(self, tmp_path):
        """A tiny, predictable seed layout, shaped like `stacklets/memory/seeds/`:
        top-level files, a subdirectory, and the household's shared bucket."""
        (tmp_path / "ontology.toml").write_text("[topic.x]\nnames = {en = 'X'}\n")
        (tmp_path / "wiki").mkdir()
        (tmp_path / "wiki" / "README.md").write_text("# wiki\n")
        (tmp_path / "_shared" / "correspondents").mkdir(parents=True)
        (tmp_path / "_shared" / "correspondents" / "README.md").write_text("# Correspondents\n")
        return tmp_path

    def test_every_shipped_file_at_its_path_and_the_shared_ones_in_the_households_bucket(
            self, seed_dir):
        assert seed_files(seed_dir, shared_bucket="simpsons") == {
            "ontology.toml": "[topic.x]\nnames = {en = 'X'}\n",
            "simpsons/correspondents/README.md": "# Correspondents\n",
            "wiki/README.md": "# wiki\n",
        }

    def test_the_shipped_seed_is_what_a_new_memory_gets(self):
        """The release's own seed: its ontology, and no `_shared/` path left
        unplaced in a bucket."""
        files = seed_files(shared_bucket="family")

        assert "ontology.toml" in files
        assert not [path for path in files if path.startswith("_shared/")]


class TestTheAdminsSeeTheMemory:
    """The household's admins are members of the family org's Owners team,
    so its repositories show among their own in Forgejo."""

    def test_each_admin_joins_the_owners_team_once_asked(self, httpserver):
        joined: list[str] = []
        httpserver.expect_request(f"/api/v1/orgs/{REPO_OWNER}/teams", method="GET") \
            .respond_with_json([{"id": 7, "name": "Owners"}])
        for name in ("homer", "marge"):
            httpserver.expect_request(f"/api/v1/teams/7/members/{name}", method="PUT") \
                .respond_with_handler(lambda request, name=name: joined.append(name)
                                      or Response("", status=204))

        add_admins_to_the_family(_admin_client(httpserver), ["homer", "marge"])

        assert joined == ["homer", "marge"]

    def test_without_admins_forgejo_is_not_asked(self, httpserver):
        add_admins_to_the_family(_admin_client(httpserver), [])

        assert httpserver.log == []


# ─── brain projection repo ───────────────────────────────────────────────

class TestBrainRemoteUrl:
    """The projection repo's git remote sits beside memory's, same org."""

    def test_points_at_family_brain(self):
        assert brain_remote_url("http://stack-code:3000") == (
            "http://stack-code:3000/family/brain.git"
        )

    def test_trailing_slash_stripped(self):
        assert brain_remote_url("http://stack-code:3000/") == (
            "http://stack-code:3000/family/brain.git"
        )


class TestEnsureBrainRepo:
    """`ensure_brain_repo` only makes the repo — the `family` org is
    already there because memory installs first."""

    def test_creates_when_missing(self, httpserver):
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{BRAIN_REPO_NAME}", method="GET",
        ).respond_with_data("not found", status=404)
        httpserver.expect_request(
            f"/api/v1/orgs/{REPO_OWNER}/repos", method="POST",
        ).respond_with_json({"id": 3, "name": BRAIN_REPO_NAME}, status=201)

        assert ensure_brain_repo(_admin_client(httpserver)) == {"created_repo": True}

    def test_no_op_when_present(self, httpserver):
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{BRAIN_REPO_NAME}", method="GET",
        ).respond_with_json({"id": 3, "name": BRAIN_REPO_NAME})

        assert ensure_brain_repo(_admin_client(httpserver)) == {"created_repo": False}


class TestSeedBrain:
    """Brain carries only a .gitignore + README scaffold; the rest fills
    in from the mirror. Idempotent: present files are left alone."""

    def test_pushes_both_scaffold_files_when_empty(self, httpserver):
        for repo_path in (".gitignore", "README.md"):
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{BRAIN_REPO_NAME}/contents/{repo_path}",
                method="GET",
            ).respond_with_data("not found", status=404)
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{BRAIN_REPO_NAME}/contents/{repo_path}",
                method="POST",
            ).respond_with_json({}, status=201)

        result = seed_brain(_admin_client(httpserver))

        assert sorted(result["created"]) == [".gitignore", "README.md"]
        assert result["skipped"] == []

    def test_skips_present_files(self, httpserver):
        for repo_path in (".gitignore", "README.md"):
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{BRAIN_REPO_NAME}/contents/{repo_path}",
                method="GET",
            ).respond_with_json(_file_response(repo_path, "existing"))

        result = seed_brain(_admin_client(httpserver))

        assert result["created"] == []
        assert sorted(result["skipped"]) == [".gitignore", "README.md"]


class TestEnsureBrainProjectionAdmin:
    """Upgrade path for instances whose install hook already ran before
    the brain projection existed."""

    def test_creates_seeds_and_clones_missing_brain(self, httpserver, tmp_path, monkeypatch):
        httpserver.expect_request("/api/v1/version").respond_with_json({"version": "11"})
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{BRAIN_REPO_NAME}", method="GET",
        ).respond_with_data("not found", status=404)
        httpserver.expect_request(
            f"/api/v1/orgs/{REPO_OWNER}/repos", method="POST",
        ).respond_with_json({"id": 3, "name": BRAIN_REPO_NAME}, status=201)

        httpserver.expect_request(
            "/api/v1/users/stackadmin/tokens", method="GET",
        ).respond_with_json([])
        httpserver.expect_request(
            "/api/v1/users/stackadmin/tokens", method="POST",
        ).respond_with_json({"sha1": "migration-token"}, status=201)

        for repo_path in (".gitignore", "README.md"):
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{BRAIN_REPO_NAME}/contents/{repo_path}",
                method="GET",
            ).respond_with_data("not found", status=404)
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{BRAIN_REPO_NAME}/contents/{repo_path}",
                method="POST",
            ).respond_with_json({}, status=201)

        cloned = {}

        def fake_clone(path, remote, **_):
            cloned["path"] = path
            cloned["remote"] = remote
            return True

        monkeypatch.setattr(memory_lib, "ensure_projection_cloned", fake_clone)

        brain = tmp_path / "brain"
        result = ensure_brain_projection_admin(
            code_url=httpserver.url_for(""),
            admin_user="stackadmin",
            admin_password="secret",
            brain_path=brain,
        )

        assert result["created_brain_repo"] is True
        assert sorted(result["brain_seeds"]["created"]) == [".gitignore", "README.md"]

        assert result["cloned_brain"] is True
        assert cloned["path"] == brain
        assert "stackadmin:migration-token@" in cloned["remote"]
