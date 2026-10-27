"""The family on the dashboard: the pills, the first run, and its two routes.

Every dashboard page opens with the family as pills, each person with the
plain word for their relation, which is what a browser without JavaScript
shows; the page's script labels them for whoever the device remembers.
While the family is empty, the page is the first run instead: the script
sets the family up and posts it to `/dashboard/api/family`, which writes it
through the host API's `family` set, as `stack family set` does, and
answers with the family or with every problem by its field.

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
NOBODY = {"name": "", "domain": "", "language": "en", "people": []}


class HostAPI:
    """The host API as the tools server reaches it: answers `family` with
    `family`, a set with `answer`, and notes every request."""

    def __init__(self, family: dict | None):
        self.family = family
        self.answer: dict = {"ok": True, "changed": ["users.toml"], **SIMPSONS}
        self.asked: list[dict] = []

    def call(self, request: dict, timeout: float) -> dict:
        self.asked.append(request)
        if request.get("cmd") != "family":
            return {"error": "not stood in"}
        if request.get("action") == "set":
            return self.answer
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


# ── While nobody is in the family: the first run ─────────────────────────

def test_while_the_family_is_empty_the_page_is_the_first_run(api):
    api.family = NOBODY

    page = phone().opens("/dashboard")

    assert "<body data-first-run>" in page.body
    assert "/dashboard/static/first-run.js" in page.scripts()
    assert page.texts("fs-pill") == []
    # Without JavaScript the page says how to set the family up instead.
    assert "./stack family set --from family.toml" in page.texts("first-run-noscript")[0]


def test_the_first_run_speaks_the_browsers_language(api):
    api.family = NOBODY

    page = phone("de").opens("/dashboard")

    assert '"heading": "Wer wohnt noch bei euch?"' in page.body
    assert '"words": "Für Wörter wie Schwester oder Bruder"' in page.body


def test_when_the_stack_does_not_answer_there_is_no_first_run_on_a_guess(api):
    api.family = None

    page = phone().opens("/dashboard")

    assert "data-first-run" not in page.body
    assert page.texts("greeting") == ["Welcome home."]


# ── GET /dashboard/api/family ────────────────────────────────────────────

def test_the_family_reads_with_how_the_page_calls_it(api):
    assert phone().reads("/dashboard/api/family") == (200, {**SIMPSONS, "called": "Simpsons"})
    assert phone("de").reads("/dashboard/api/family")[1]["called"] == "Familie Simpson"


def test_the_family_is_unknown_while_the_stack_does_not_answer(api):
    api.family = None

    status, answer = phone().reads("/dashboard/api/family")

    assert status == 503
    assert "error" in answer


# ── POST /dashboard/api/family ───────────────────────────────────────────

FAMILY_FILE = {"name": "Simpson", "language": "en", "people": [
    {"name": "Homer", "relation": "parent", "words": "he", "looks_after": True},
    {"name": "Lisa", "relation": "child", "words": "she", "looks_after": False}]}


def test_the_first_run_writes_the_family_through_the_host_api(api):
    status, answer = phone().posts("/dashboard/api/family", FAMILY_FILE)

    assert status == 200
    assert api.asked[-1] == {"cmd": "family", "action": "set", "family": FAMILY_FILE}
    assert answer["called"] == "Simpsons"
    assert [p["id"] for p in answer["people"]] == ["homer", "lisa"]


def test_a_refused_family_comes_back_with_each_problem_by_its_field(api):
    api.answer = {"error": "Nothing was written: 1 problem",
                  "problems": [{"field": "domain", "message": "The web address is a domain name."}]}

    status, answer = phone().posts("/dashboard/api/family", {**FAMILY_FILE, "domain": "simpsons"})

    assert status == 422
    assert answer["problems"] == [{"field": "domain", "message": "The web address is a domain name."}]


def test_a_family_the_stack_could_not_take_says_why(api):
    api.answer = {"error": "the host API is not running"}

    status, answer = phone().posts("/dashboard/api/family", FAMILY_FILE)

    assert status == 503
    assert answer["error"] == "the host API is not running"


def test_anything_but_one_family_is_turned_away_before_the_stack_is_asked(api):
    import server
    from fastapi.testclient import TestClient

    r = TestClient(server.app, base_url=f"http://testserver:{PUBLIC_PORT}").post(
        "/dashboard/api/family", json=[FAMILY_FILE], headers={"host": "192.168.1.20:42000"})

    assert r.status_code == 400
    assert api.asked == []


# ── What the page loads ──────────────────────────────────────────────────

@pytest.mark.parametrize("path, kind", [
    ("/dashboard/static/first-run.js", "text/javascript"),
    ("/dashboard/static/family-bar.js", "text/javascript"),
    ("/dashboard/static/components/family-editor.js", "text/javascript"),
    ("/dashboard/static/components/moment.css", "text/css"),
    ("/dashboard/static/components/kin-words.json", "application/json"),
])
def test_the_first_run_and_its_components_are_served(path, kind):
    r = phone().client.get(path, headers={"host": "192.168.1.20:42000"})

    assert r.status_code == 200
    assert r.headers["content-type"].startswith(kind)


@pytest.mark.parametrize("path", ["/dashboard/static/gallery.html",
                                  "/dashboard/static/components/../index.html",
                                  "/dashboard/static/components/nothing.js"])
def test_nothing_else_beside_them_is(path):
    assert phone().client.get(path, headers={"host": "192.168.1.20:42000"}).status_code == 404
