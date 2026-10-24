"""`stack.http_json`: one part of the stack asking another, as a caller sees it.

A real HTTP server on this machine answers (`pytest-httpserver`); nothing in
the client is patched. What a test asserts is what the caller gets back, and
what the server received.
"""

from __future__ import annotations

import json
import socket
import time

import pytest
from werkzeug import Response

from stack.http_json import JsonClient, Unreachable


def test_a_json_answer_comes_back_with_its_status(httpserver):
    """Given a service that files a change, when it is asked with a body
    and a token, then the caller gets the status and the JSON it sent, and
    the service got the body as JSON with the token as a bearer."""
    httpserver.expect_request(
        "/commits", method="POST", json={"kind": "capture"},
        headers={"Authorization": "Bearer donuts", "Content-Type": "application/json"},
    ).respond_with_json({"commit": "abc123"}, status=201)
    memory = JsonClient(httpserver.url_for("/"), token="donuts")

    answer = memory.post("/commits", {"kind": "capture"})

    assert (answer.status, answer.body, answer.ok) == (201, {"commit": "abc123"}, True)


def test_the_answers_headers_come_back_by_lower_case_name(httpserver):
    """Given a service that names a version in a header, when it is asked,
    then the caller reads it by its lower-case name, as HTTP means it."""
    httpserver.expect_request("/cards/person-bart").respond_with_json(
        {"title": "Bart Simpson"}, headers={"ETag": '"abc123"'})

    answer = JsonClient(httpserver.url_for("/")).get("/cards/person-bart")

    assert answer.headers["etag"] == '"abc123"'


def test_a_get_sends_no_body_and_no_token_unless_given(httpserver):
    """Given a service that lists what waits, when it is read without a
    token, then the request carries neither a body nor an Authorization."""
    httpserver.expect_request("/pending", method="GET").respond_with_json({"commits": []})

    answer = JsonClient(httpserver.url_for("")).get("pending")

    assert answer.body == {"commits": []}
    sent, _ = httpserver.log[0]
    assert "Authorization" not in sent.headers and sent.get_data() == b""


def test_an_error_status_is_an_answer_with_the_services_words(httpserver):
    """Given a service refusing with a problem document, when it is asked,
    then the caller gets the status and the service's own explanation.

    What a refusal means is the caller's to decide; an exception would
    lose the words the service chose for the person.
    """
    httpserver.expect_request("/commits", method="POST").respond_with_json(
        {"type": "about:blank", "status": 409, "detail": "camping.md changed since it was read"},
        status=409, content_type="application/problem+json")

    answer = JsonClient(httpserver.url_for("/")).post("/commits", {})

    assert not answer.ok
    assert answer.status == 409
    assert answer.detail == "camping.md changed since it was read"


def test_an_answer_that_is_not_json_is_kept_as_its_text(httpserver):
    """Given a service that fails with a plain-text page, when it is asked,
    then the caller still gets something to show: the start of the text."""
    httpserver.expect_request("/pending").respond_with_response(
        Response("Internal Server Error: the clone is gone", status=500, content_type="text/plain"))

    answer = JsonClient(httpserver.url_for("/")).get("/pending")

    assert answer.status == 500
    assert answer.detail == "Internal Server Error: the clone is gone"


def test_nothing_listening_is_unreachable_and_names_the_address():
    """Given nothing listening at an address, when it is asked, then the
    caller is told no answer came and from where."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with pytest.raises(Unreachable) as unreachable:
        JsonClient(f"http://127.0.0.1:{port}").post("/commits", {})

    assert unreachable.value.url == f"http://127.0.0.1:{port}/commits"
    assert unreachable.value.url in str(unreachable.value)


def test_a_service_slower_than_the_timeout_is_unreachable(httpserver):
    """Given a service that takes longer than the caller waits, when it is
    asked, then the caller stops waiting and is told it did not answer."""
    def slow(_request):
        time.sleep(1)
        return Response(json.dumps({}), status=200)
    httpserver.expect_request("/pending").respond_with_handler(slow)

    with pytest.raises(Unreachable):
        JsonClient(httpserver.url_for("/"), timeout=0.2).get("/pending")
