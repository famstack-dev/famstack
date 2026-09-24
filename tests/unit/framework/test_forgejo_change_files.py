"""Several files in one commit, and a directory listing with blob hashes.

A batch writer (the diary compiler) changes many vault files at once.
One commit per batch keeps the vault's history readable, and the blob
hash sent with each update makes Forgejo refuse the write when the file
changed since it was read, rather than overwrite a person's edit.

The request shape is Forgejo's documented `ChangeFilesOptions`
(`POST /repos/{owner}/{repo}/contents`, Forgejo 1.20 and later); the rig
runs the same call against a real Forgejo.
"""

from __future__ import annotations

import base64
import json

import pytest

from stack.forgejo import FileChange, ForgejoClient, ForgejoError


def _client(httpserver) -> ForgejoClient:
    return ForgejoClient(url=httpserver.url_for(""), admin_user="admin",
                         admin_password="pw")


def test_one_commit_creates_updates_and_deletes_with_the_persons_name(httpserver):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.get_data())
        seen["auth"] = request.headers.get("Authorization", "")
        from werkzeug import Response
        return Response(json.dumps({"commit": {"sha": "abc"}}), status=201,
                        content_type="application/json")

    httpserver.expect_request("/api/v1/repos/family/memory/contents",
                              method="POST").respond_with_handler(handler)

    _client(httpserver).change_files(
        "family", "memory",
        [FileChange("create", "family/diary/a.md", content="Ä new"),
         FileChange("update", "family/diary/b.md", content="changed", sha="b1"),
         FileChange("delete", "family/diary/c.md", sha="c1")],
        message="Diary: 3 entries", author_name="marge",
        author_email="marge@simpson")

    body = seen["body"]
    assert seen["auth"].startswith("Basic ")
    assert body["message"] == "Diary: 3 entries"
    assert body["branch"] == "main"
    assert body["author"] == {"name": "marge", "email": "marge@simpson"}
    ops = {f["path"]: f for f in body["files"]}
    assert ops["family/diary/a.md"]["operation"] == "create"
    assert base64.b64decode(ops["family/diary/a.md"]["content"]).decode() == "Ä new"
    assert ops["family/diary/b.md"]["sha"] == "b1"
    assert ops["family/diary/c.md"] == {"operation": "delete",
                                        "path": "family/diary/c.md", "sha": "c1"}


def test_a_file_changed_since_it_was_read_fails_the_whole_commit(httpserver):
    httpserver.expect_request("/api/v1/repos/family/memory/contents",
                              method="POST").respond_with_json(
        {"message": "sha does not match"}, status=409)
    with pytest.raises(ForgejoError):
        _client(httpserver).change_files(
            "family", "memory",
            [FileChange("update", "x.md", content="y", sha="stale")],
            message="m")


def test_a_directory_lists_its_files_and_folders_with_their_hashes(httpserver):
    httpserver.expect_request("/api/v1/repos/family/memory/contents/family/diary").respond_with_json([
        {"type": "dir", "path": "family/diary/2026", "sha": "d1"},
        {"type": "file", "path": "family/diary/about.md", "sha": "f1"},
    ])
    listing = _client(httpserver).list_dir("family", "memory", "family/diary")
    assert [(e["type"], e["path"], e["sha"]) for e in listing] == [
        ("dir", "family/diary/2026", "d1"), ("file", "family/diary/about.md", "f1")]


def test_a_missing_directory_is_empty(httpserver):
    httpserver.expect_request("/api/v1/repos/family/memory/contents/family/diary").respond_with_json(
        {"message": "not found"}, status=404)
    assert _client(httpserver).list_dir("family", "memory", "family/diary") == []
