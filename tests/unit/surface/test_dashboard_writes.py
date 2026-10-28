"""The dashboard's writes are taken only as JSON.

The dashboard is open on the home network until the backlog entry "Basic
security for the tools server" lands, and a web page from any site, opened
in a browser at home, could post to it without asking first: a string body
goes out as `text/plain`, a form as a form, a Blob without a type with no
Content-Type at all, and none of these needs a CORS preflight. Sent as
`application/json`, the browser asks first; the tools server answers no
preflight, so the post never leaves the browser. So each of the dashboard's
writes (starting a job, setting the family, answering a stacklet's setup
forms) takes a body only when it is sent as JSON, and refuses anything else
with 415 before the host API hears of it.

Driven through the tools server's FastAPI app (the Browser DSL), as a phone
on the home network reaches it. The host API is a stand-in at its edge,
`stack.live.client`, noting every request.
"""

from __future__ import annotations

import sys

import pytest

from .conftest import LIB, STACKLETS
from .dsl import Browser
from .test_dashboard_setup import FAMILY

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402
from stack.live.envelope import Event  # noqa: E402
from stack.live.hub import Hub  # noqa: E402
from stack.live.jobs import Jobs  # noqa: E402

# Each write, with a body the page would send it.
WRITES = {
    "a job": ("/dashboard/api/jobs", {"action": "up", "target": "photos"}),
    "the family": ("/dashboard/api/family", FAMILY),
    "setup answers": ("/dashboard/api/configure/shoebox", {"answers": {"drive": "shoebox-drive"}}),
}
# How a page from another site sends a body without a preflight.
NOT_JSON = ["text/plain", "text/plain;charset=UTF-8", "application/x-www-form-urlencoded",
            "multipart/form-data; boundary=famstack", None]
JSON = ["application/json", "application/json; charset=utf-8", "application/vnd.api+json"]


class HostAPI:
    """The host API, noting every request: a family set returns the family,
    setup answers are ready, and `stack up` ends at once."""

    def __init__(self):
        self.asked: list[dict] = []

    def call(self, request: dict, timeout: float) -> dict:
        self.asked.append(request)
        if request.get("cmd") == "family":
            return FAMILY
        if request.get("cmd") == "configure":
            return {"ready": True}
        return {"error": "not stood in"}

    def stream(self, request: dict, *, silence_secs: float):
        self.asked.append(request)
        yield Event.now(1, "result", {"ok": True, "stacklet": request["stacklet"]})


@pytest.fixture
def api(monkeypatch) -> HostAPI:
    import live_routes

    stand_in = HostAPI()
    hub = Hub()
    monkeypatch.setattr(live_routes, "hub", hub)
    monkeypatch.setattr(live_routes, "jobs", Jobs(hub))
    monkeypatch.setattr(client, "call", stand_in.call)
    monkeypatch.setattr(client, "stream", stand_in.stream)
    return stand_in


def phone() -> Browser:
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


@pytest.mark.parametrize("sent_as", NOT_JSON)
@pytest.mark.parametrize("write", list(WRITES))
def test_a_write_not_sent_as_json_is_refused_before_the_host_api_hears_of_it(api, write, sent_as):
    path, body = WRITES[write]

    status, answer = phone().posts(path, body, sent_as=sent_as)

    assert status == 415
    assert "application/json" in answer["error"]
    assert api.asked == []


@pytest.mark.parametrize("sent_as", JSON)
@pytest.mark.parametrize("write", list(WRITES))
def test_a_write_sent_as_json_is_taken(api, write, sent_as):
    path, body = WRITES[write]

    status, answer = phone().posts(path, body, sent_as=sent_as)

    assert status in (200, 202), answer
    assert len(api.asked) == 1
