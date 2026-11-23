"""Core's tools server searches documents and photos with the current token.

Documents' and photos' first runs write the token their service needs
(`docs__API_TOKEN`, `photos__API_KEY`), often while the tools server runs
the setup page. The tools server reads each token when a search needs it,
from the host API (`stack env core`), so a token written after it started
reaches its next search without a recreate of the server.

Driven through the tools server's FastAPI app on its internal listener, as
Open WebUI calls a tool. The host API is `tests/dashboard_host.py` on a
loopback port, over a throwaway instance whose secrets the test writes as a
first run writes them. Paperless and Immich are `pytest-httpserver`
stand-ins that answer only a request carrying the expected token.
"""

from __future__ import annotations

import pytest

from stack.secrets import TomlSecretStore
from tests.dashboard_host import DashboardHost
from tests.unit.surface.conftest import tools_server

TOKEN = "written-after-the-server-started"


@pytest.fixture
def host_api(tmp_path, monkeypatch):
    """The host API over a fresh instance, on a loopback port the tools
    server asks. Returns the instance's secret store."""
    host = DashboardHost.in_state("fresh", root=tmp_path / "instance")
    monkeypatch.setenv("STACK_API_PORT", str(host.serve()))
    yield TomlSecretStore(tmp_path / "instance" / ".stack" / "secrets.toml")
    host.close()


def ask(path: str, query: str):
    """`GET <path>?query=<query>` on the tools server's internal listener;
    the response."""
    from fastapi.testclient import TestClient
    server = tools_server("server")
    access = tools_server("access")
    client = TestClient(server.app, base_url=f"http://stack-core-tools:{access.INTERNAL_PORT}")
    return client.get(path, params={"query": query})


def search(path: str, query: str) -> dict:
    """`ask`, for a search that must succeed; its answer."""
    response = ask(path, query)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_document_search_sends_the_token_written_after_the_server_started(
        host_api, httpserver, monkeypatch):
    """Given the tools server runs, and documents' first run then writes
    its token,
    when a document search comes in,
    then the server asks Paperless with that token."""
    tools_server("server")
    host_api.set("docs", "API_TOKEN", TOKEN)
    httpserver.expect_request("/api/documents/", headers={"Authorization": f"Token {TOKEN}"}) \
        .respond_with_json({"count": 1, "results": [{"id": 7, "title": "Tent receipt"}]})
    monkeypatch.setenv("PAPERLESS_URL", httpserver.url_for("").rstrip("/"))

    found = search("/tools/documents/search", "tent")

    assert [doc["id"] for doc in found["results"]] == [7]


def test_a_photo_search_sends_the_key_written_after_the_server_started(
        host_api, httpserver, monkeypatch):
    """Given the tools server runs, and photos' first run then writes its
    key,
    when a photo search comes in,
    then the server asks Immich with that key."""
    tools_server("server")
    host_api.set("photos", "API_KEY", TOKEN)
    httpserver.expect_request("/api/search/smart", method="POST", headers={"x-api-key": TOKEN}) \
        .respond_with_json({"assets": {"items": [{"id": "beach-1"}]}})
    monkeypatch.setenv("IMMICH_URL", httpserver.url_for("").rstrip("/"))

    found = search("/tools/photos/search", "beach")

    assert [photo["id"] for photo in found["results"]] == ["beach-1"]


@pytest.mark.parametrize("path, address", [("/tools/documents/search", "PAPERLESS_URL"),
                                           ("/tools/photos/search", "IMMICH_URL")])
def test_a_search_while_the_host_api_does_not_answer_does_not_ask_for_an_install(
        monkeypatch, path, address):
    """Given the service is installed, and the host API does not answer
    (the conftest points it at a closed port),
    when a search comes in,
    then the server answers that it cannot search right now, and names no
    install to run: the service is there, only its token could not be read."""
    monkeypatch.setenv(address, "http://127.0.0.1:1")

    response = ask(path, "tent")

    assert response.status_code == 503
    assert "install" not in response.json()["error"]
