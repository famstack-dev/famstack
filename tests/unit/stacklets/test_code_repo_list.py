"""`stack code repo list`: the repositories on the family's Forgejo.

Without an owner the command asks Forgejo's search endpoint, which wraps
its results in an envelope (`{"ok": true, "data": [...]}`, the
`SearchResults` schema in Forgejo's swagger). With an owner it asks the
user's repository list, which is a bare array. Both have to come out as
the same listing, for people and with `--json`.

pytest-httpserver stands in for Forgejo, answering in the shapes its
API documents.
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


repo_cli = _load("code_cli_repo", _REPO_ROOT / "stacklets" / "code" / "cli" / "repo.py")

MEMORY = {"id": 1, "name": "memory", "full_name": "family/memory",
          "owner": {"id": 2, "login": "family"}, "private": True,
          "description": "The family's memory"}
NOTES = {"id": 2, "name": "notes", "full_name": "homer/notes",
         "owner": {"id": 3, "login": "homer"}, "private": False,
         "description": ""}


@pytest.fixture
def repo(httpserver, monkeypatch):
    """Run `stack code repo <argv>` against the stand-in Forgejo."""
    config = {"is_healthy": lambda: True,
              "manifest": {"port": httpserver.port},
              "secrets": {"global__ADMIN_PASSWORD": "pw"}}

    def _go(*argv):
        monkeypatch.setattr(sys, "argv", ["stack", "code", "repo", *argv])
        return repo_cli.run(list(argv), None, config)
    return _go


def test_every_repository_is_listed(repo, httpserver, capsys):
    httpserver.expect_request("/api/v1/repos/search").respond_with_json(
        {"ok": True, "data": [MEMORY, NOTES]})

    result = repo("list")

    assert result == {"repos": [
        {"full_name": "family/memory", "private": True, "description": "The family's memory"},
        {"full_name": "homer/notes", "private": False, "description": ""},
    ]}
    out = capsys.readouterr().out
    assert "family/memory" in out and "private" in out
    assert "homer/notes" in out and "public" in out


def test_one_owners_repositories_are_listed(repo, httpserver):
    httpserver.expect_request("/api/v1/users/family/repos").respond_with_json([MEMORY])

    result = repo("list", "family")

    assert [r["full_name"] for r in result["repos"]] == ["family/memory"]


def test_no_repositories_is_an_empty_list(repo, httpserver, capsys):
    httpserver.expect_request("/api/v1/repos/search").respond_with_json(
        {"ok": True, "data": []})

    assert repo("list") == {"repos": []}
    assert "No repositories." in capsys.readouterr().out


def test_an_error_from_forgejo_is_reported_not_raised(repo, httpserver):
    httpserver.expect_request("/api/v1/repos/search").respond_with_json(
        {"message": "user does not exist"}, status=401)

    result = repo("list")

    assert "401" in result["error"]
