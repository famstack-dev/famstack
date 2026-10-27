"""The family on the dashboard: the pills, and what their script reads.

Every dashboard page opens with the family as pills, each person with the
plain word for their relation, which is what a browser without JavaScript
shows; the page's script labels them for whoever the device remembers,
from `GET /dashboard/api/family`.

Driven through the tools server's FastAPI app and its access rule, as a
phone on the home network uses it (the Browser DSL). The host API is the
stand-in, at its edge: `stack.live.client.call` answers `family` as
`stack family --json` would, and notes what it was asked.
"""

from __future__ import annotations

import sys

import pytest

from .conftest import LIB, STACKLETS
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402

SIMPSONS = {"name": "Simpson", "domain": "", "language": "en", "people": [
    {"id": "homer", "name": "Homer Simpson", "relation": "parent", "words": "he",
     "role": "admin", "email": "homer@home.local"},
    {"id": "lisa", "name": "Lisa Simpson", "relation": "child", "words": "she",
     "role": "member", "email": "lisa@home.local"},
]}


class HostAPI:
    """The host API as the tools server reaches it: answers `family` with
    `family`, and notes every request."""

    def __init__(self, family: dict | None):
        self.family = family
        self.asked: list[dict] = []

    def call(self, request: dict, timeout: float) -> dict:
        self.asked.append(request)
        if request.get("cmd") != "family":
            return {"error": "not stood in"}
        return self.family if self.family is not None else {"error": "the host API is not running"}


@pytest.fixture
def api(monkeypatch):
    stand_in = HostAPI(SIMPSONS)
    monkeypatch.setattr(client, "call", stand_in.call)
    return stand_in


def phone(language: str = "en") -> Browser:
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language)


# ── Every page: the pills ────────────────────────────────────────────────

def test_the_page_opens_with_the_family_as_pills(api):
    page = phone().opens("/dashboard")

    assert page.texts("fs-pill-name") == ["Homer", "Lisa"]
    assert page.texts("fs-pill-label") == ["parent", "child"]
    assert page.texts("fs-pills-hint") == ["Tap your name, and this device greets you"]
    assert page.texts("greeting") == ["Welcome home, Simpsons."]
    assert "/dashboard/static/family-bar.js" in page.scripts()


def test_in_german_the_pills_say_it_in_german(api):
    page = phone("de").opens("/dashboard")

    assert page.texts("fs-pill-label") == ["Elternteil", "Kind"]


def test_people_written_before_relations_existed_get_a_pill_without_one(api):
    api.family = {**SIMPSONS, "people": [{**SIMPSONS["people"][0], "relation": None, "words": None}]}

    page = phone().opens("/dashboard")

    assert page.texts("fs-pill-name") == ["Homer"]
    assert page.texts("fs-pill-label") == []


# ── GET /dashboard/api/family ────────────────────────────────────────────

def test_the_family_reads_with_how_the_page_calls_it(api):
    assert phone().reads("/dashboard/api/family") == (200, {**SIMPSONS, "called": "Simpsons"})
    assert phone("de").reads("/dashboard/api/family")[1]["called"] == "Familie Simpson"


def test_the_family_is_unknown_while_the_stack_does_not_answer(api):
    api.family = None

    status, answer = phone().reads("/dashboard/api/family")

    assert status == 503
    assert "error" in answer


# ── What the page loads ──────────────────────────────────────────────────

@pytest.mark.parametrize("path, kind", [
    ("/dashboard/static/family-bar.js", "text/javascript"),
    ("/dashboard/static/components/family.js", "text/javascript"),
    ("/dashboard/static/components/pills.css", "text/css"),
    ("/dashboard/static/components/kin-words.json", "application/json"),
])
def test_the_family_bar_and_its_components_are_served(path, kind):
    r = phone().client.get(path, headers={"host": "192.168.1.20:42000"})

    assert r.status_code == 200
    assert r.headers["content-type"].startswith(kind)


@pytest.mark.parametrize("path", ["/dashboard/static/gallery.html",
                                  "/dashboard/static/components/../index.html",
                                  "/dashboard/static/components/nothing.js"])
def test_nothing_else_beside_them_is(path):
    assert phone().client.get(path, headers={"host": "192.168.1.20:42000"}).status_code == 404
