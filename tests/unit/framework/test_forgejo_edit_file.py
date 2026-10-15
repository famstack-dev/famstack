"""A read-modify-write of one file, whose transform can also remove it.

`edit_file` reads the current text, hands it to the caller's transform
and commits the answer against the hash it read. A transform that
answers `None` means the file should be gone: the same seam then
deletes it, in a commit authored by the person, so a deletion is in the
history under their name like any other change.

Forgejo is a real HTTP server here (`pytest-httpserver`) answering the
documented contents API.
"""

from __future__ import annotations

import base64
import json

from stack.forgejo import ForgejoClient

CONTENTS = "/api/v1/repos/family/memory/contents"


def _client(httpserver) -> ForgejoClient:
    return ForgejoClient(url=httpserver.url_for(""), token="bottoken")


def _file(text: str, sha: str) -> dict:
    return {"type": "file", "content": base64.b64encode(text.encode()).decode(), "sha": sha}


def test_a_transform_answering_none_deletes_the_file_as_the_person(httpserver):
    httpserver.expect_request(f"{CONTENTS}/family/lists/baumarkt.md",
                              method="GET").respond_with_json(_file("- [x] Dübel\n", "s1"))
    httpserver.expect_request(CONTENTS, method="POST").respond_with_json({}, status=201)

    _client(httpserver).edit_file(
        "family", "memory", "family/lists/baumarkt.md", lambda prior: None,
        message=lambda before, after: f"deleted ({len(before)} chars, after {after!r})",
        author_name="marge", author_email="marge@simpson")

    post = next(r for r, _ in httpserver.log if r.method == "POST")
    body = json.loads(post.data)
    assert body["files"] == [{"operation": "delete", "path": "family/lists/baumarkt.md",
                              "sha": "s1"}], "deleted against the hash it was read at"
    assert body["author"] == {"name": "marge", "email": "marge@simpson"}
    assert body["message"] == "deleted (12 chars, after '')"


def test_deleting_a_file_that_is_not_there_commits_nothing(httpserver):
    httpserver.expect_request(f"{CONTENTS}/family/lists/baumarkt.md",
                              method="GET").respond_with_data("", status=404)

    result = _client(httpserver).edit_file(
        "family", "memory", "family/lists/baumarkt.md", lambda prior: None, message="m")

    assert result is None
    assert not [r for r, _ in httpserver.log if r.method != "GET"]
