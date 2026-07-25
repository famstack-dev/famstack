"""Install-side tests for the memory stacklet.

Covers the two install primitives in `stacklets/memory/lib.py`:

  - `ensure_memory_repo(client)` — creates the `family` org and
    `memory` repo if they don't exist. Idempotent.
  - `install_seeds(client, seed_dir)` — walks the seed directory and
    `put_file`s each entry into the repo, skipping anything already
    present.

Forgejo is stubbed with `pytest-httpserver` so the HTTP shape, JSON
parsing, and `ForgejoClient` are exercised for real.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "memory"))

from lib import (  # noqa: E402
    BRAIN_REPO_NAME,
    REPO_NAME,
    REPO_OWNER,
    brain_remote_url,
    ensure_brain_repo,
    ensure_brain_projection_admin,
    ensure_memory_repo,
    install_seeds,
    purge_generated_memory_pages,
    purge_local_generated_memory_pages,
    seed_brain,
)
import lib as memory_lib  # noqa: E402
from stack.forgejo import ForgejoClient  # noqa: E402


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

class TestInstallSeeds:

    @pytest.fixture
    def seed_dir(self, tmp_path):
        """A tiny, predictable seed layout.

        Mirrors the structure of `stacklets/memory/seeds/` (top-level
        files plus a `wiki/` subdirectory) without depending on the
        shipped content.
        """
        (tmp_path / "ontology.toml").write_text("[topic.x]\nnames = {en = 'X'}\n")
        (tmp_path / "facts.toml").write_text("# empty\n")
        (tmp_path / "wiki").mkdir()
        (tmp_path / "wiki" / "README.md").write_text("# wiki\n")
        return tmp_path

    def test_creates_all_files_when_repo_is_empty(self, httpserver, seed_dir):
        # Every GET returns 404 → every PUT happens.
        for repo_path in ("ontology.toml", "facts.toml", "wiki/README.md"):
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}",
                method="GET",
            ).respond_with_data("not found", status=404)
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}",
                method="POST",
            ).respond_with_json({"content": _file_response(repo_path)}, status=201)

        result = install_seeds(
            _admin_client(httpserver), seed_dir=seed_dir,
            commit_message="seed: test",
        )

        assert sorted(result["created"]) == ["facts.toml", "ontology.toml", "wiki/README.md"]
        assert result["skipped"] == []

    def test_skips_files_already_present(self, httpserver, seed_dir):
        # Every GET returns content → no PUT calls.
        for repo_path in ("ontology.toml", "facts.toml", "wiki/README.md"):
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}",
                method="GET",
            ).respond_with_json(_file_response(repo_path, "existing"))

        result = install_seeds(
            _admin_client(httpserver), seed_dir=seed_dir,
            commit_message="seed: test",
        )

        assert result["created"] == []
        assert sorted(result["skipped"]) == ["facts.toml", "ontology.toml", "wiki/README.md"]

    def test_creates_only_the_missing_files(self, httpserver, seed_dir):
        # ontology.toml exists; the other two don't.
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/ontology.toml",
            method="GET",
        ).respond_with_json(_file_response("ontology.toml", "existing"))

        for repo_path in ("facts.toml", "wiki/README.md"):
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}",
                method="GET",
            ).respond_with_data("not found", status=404)
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}",
                method="POST",
            ).respond_with_json({"content": _file_response(repo_path)}, status=201)

        result = install_seeds(
            _admin_client(httpserver), seed_dir=seed_dir,
            commit_message="seed: test",
        )

        assert sorted(result["created"]) == ["facts.toml", "wiki/README.md"]
        assert result["skipped"] == ["ontology.toml"]

    def test_sends_file_content_base64_encoded(self, httpserver, seed_dir):
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/ontology.toml",
            method="GET",
        ).respond_with_data("not found", status=404)
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/ontology.toml",
            method="POST",
        ).respond_with_json({}, status=201)

        # Skip the other two seed files for this assertion.
        for repo_path in ("facts.toml", "wiki/README.md"):
            httpserver.expect_request(
                f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}",
                method="GET",
            ).respond_with_json(_file_response(repo_path, "existing"))

        install_seeds(
            _admin_client(httpserver), seed_dir=seed_dir,
            commit_message="seed: test",
            author_name="memory-bot",
            author_email="memory-bot@local",
        )

        # pytest-httpserver records every (request, response) pair it
        # served. Find the POST and inspect its body.
        posts = [req for req, _ in httpserver.log if req.method == "POST"]
        assert len(posts) == 1
        body = json.loads(posts[0].get_data())
        decoded = base64.b64decode(body["content"]).decode("utf-8")
        assert decoded == "[topic.x]\nnames = {en = 'X'}\n"
        assert body["message"] == "seed: test"
        assert body["author"] == {"name": "memory-bot", "email": "memory-bot@local"}


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

        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/git/trees/main",
            method="GET",
        ).respond_with_json({"tree": []})

        cloned = {}

        def fake_clone(path, remote):
            cloned["path"] = path
            cloned["remote"] = remote
            return True

        monkeypatch.setattr(memory_lib, "ensure_vault_cloned", fake_clone)

        brain = tmp_path / "brain"
        result = ensure_brain_projection_admin(
            code_url=httpserver.url_for(""),
            admin_user="stackadmin",
            admin_password="secret",
            brain_path=brain,
        )

        assert result["created_brain_repo"] is True
        assert sorted(result["brain_seeds"]["created"]) == [".gitignore", "README.md"]
        assert result["memory_purge"] == {"deleted": []}
        assert result["cloned_brain"] is True
        assert cloned["path"] == brain
        assert "stackadmin:migration-token@" in cloned["remote"]


class TestPurgeGeneratedMemoryPages:
    """Source cleanup deletes only marker-bearing generated markdown."""

    def test_deletes_generated_markdown_from_memory(self, httpserver):
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/git/trees/main",
            method="GET",
        ).respond_with_json({
            "tree": [
                {"type": "blob", "path": "index.md"},
                {"type": "blob", "path": "homer/about.md"},
                {"type": "blob", "path": "homer/notes/source.md"},
                {"type": "blob", "path": "family/correspondents/README.md"},
                {"type": "blob", "path": "facts.toml"},
            ],
        })
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/index.md",
            method="GET",
        ).respond_with_json(_file_response(
            "index.md",
            "---\ntitle: Home\n---\n<!-- begin: generated -->\nbody\n<!-- end: generated -->\n",
        ))
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/index.md",
            method="DELETE",
        ).respond_with_json({}, status=200)
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/homer/about.md",
            method="GET",
        ).respond_with_json(_file_response(
            "homer/about.md",
            "---\ntitle: Homer\n---\n<!-- begin: generated -->\nbody\n<!-- end: generated -->\n",
        ))
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/homer/about.md",
            method="DELETE",
        ).respond_with_json({}, status=200)
        httpserver.expect_request(
            f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents/homer/notes/source.md",
            method="GET",
        ).respond_with_json(_file_response(
            "homer/notes/source.md",
            "---\ntype: note\n---\n\nsource body\n",
        ))

        result = purge_generated_memory_pages(_admin_client(httpserver))

        assert result == {"deleted": ["index.md", "homer/about.md"]}


class TestPurgeLocalGeneratedMemoryPages:
    """Local cleanup removes generated pages that would block pull."""

    def test_deletes_generated_markdown_but_keeps_readme_and_sources(self, tmp_path):
        generated = tmp_path / "homer" / "about.md"
        generated.parent.mkdir(parents=True)
        generated.write_text(
            "---\ntitle: Homer\n---\n<!-- begin: generated -->\nbody\n",
            encoding="utf-8",
        )
        readme = tmp_path / "family" / "correspondents" / "README.md"
        readme.parent.mkdir(parents=True)
        readme.write_text("Example <!-- begin: generated --> marker\n", encoding="utf-8")
        source = tmp_path / "homer" / "notes" / "source.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("---\ntype: note\n---\n\nsource\n", encoding="utf-8")

        result = purge_local_generated_memory_pages(tmp_path)

        assert result == {"deleted": ["homer/about.md"]}
        assert not generated.exists()
        assert readme.exists()
        assert source.exists()
