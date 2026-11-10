"""An email card's page names its sender when the card has one.

An email card may hold the optional fields `from_name` and `from_address`,
who sent its thread's first message (`from_name: the school office`,
`from_address: office@school.example`). Its page then starts the facts
line with "From the school office (office@school.example)" before the
date. Without either field the facts line is the date alone, as for every
email filed before the archivist wrote them: the page never looks for a
sender in the card's text or its source.

Driven through the tools server's FastAPI app (the Browser DSL), as a
phone on the home network reads the page. The cards API is a stand-in on
localhost that answers `card/<id>` with the email cards below and nothing
else.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from .conftest import tools_server
from .dsl import Browser

PUBLIC_PORT = tools_server("access").PUBLIC_PORT

OFFICE = {"from_name": "the school office", "from_address": "office@school.example"}


def email(card_id: str, **sender) -> dict:
    """The cards API's answer for a ski-week email card of 5 November,
    with `sender` as its `from_name` and `from_address` when given."""
    meta = {"type": "email", "card_id": card_id, "title": "Class 4b ski week in Lindenberg",
            "date": "2026-11-05", "persons": ["bart"],
            "resource": "mid:<ski-4b@springfield-elementary.example>",
            "timestamp": "2026-11-05T07:30:00Z", **sender}
    return {"id": card_id, "name": card_id, "type": "email", "title": meta["title"],
            "day": "2026-11-05", "version": "1", "path": f"family/emails/2026/11/{card_id}.md",
            "persons": ["bart"], "links": 0, "media": [], "meta": meta,
            "text": "# Class 4b ski week in Lindenberg\n\nWritten by Principal Skinner.",
            "tag_labels": {}, "changes": [], "views": []}


CARDS = {c["id"]: c for c in (
    email("email-full", **OFFICE),
    email("email-name", from_name="the school office"),
    email("email-address", from_address="office@school.example"),
    email("email-none"),
)}


@pytest.fixture(scope="module")
def cards_api():
    """A stand-in for the memory's cards API: `card/<id>` from CARDS, 404 else."""
    class CardsAPI(BaseHTTPRequestHandler):
        def do_GET(self):
            ref = self.path.split("?", 1)[0].removeprefix("/card/")
            card = CARDS.get(ref) if self.path.startswith("/card/") else None
            body = json.dumps(card or {"error": "not found"}).encode()
            self.send_response(200 if card else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), CardsAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def tools(cards_api, monkeypatch):
    """Core's tools server, reaching the stand-in cards API, in UTC."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", cards_api)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return server.app


def facts(app, card_id: str, language: str = "en") -> list[str]:
    page = Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000",
                   language=language).opens(f"/card/{card_id}")
    assert page.status == 200, page
    return page.texts("facts")


def test_an_email_with_a_sender_names_it_and_its_address_before_the_date(tools):
    """Given an email card with a sender's name and address, then
    the facts line reads "From <name> (<address>) · <date>"."""
    assert facts(tools, "email-full") == [
        "From the school office (office@school.example) · 5 November"]


def test_a_sender_with_only_a_name_or_only_an_address_shows_what_it_has(tools):
    """Given a sender's name only, then the name; with an address only,
    then the address in place of the name."""
    assert facts(tools, "email-name") == ["From the school office · 5 November"]
    assert facts(tools, "email-address") == ["From office@school.example · 5 November"]


def test_an_email_without_a_sender_shows_the_date_alone(tools):
    """Given an email card without a sender, then the facts line is the
    date alone, as for every email filed before the fields."""
    assert facts(tools, "email-none") == ["5 November"]


def test_in_german_the_sender_line_starts_with_von(tools):
    """Given the page in German, then "Von" in place of "From"."""
    assert facts(tools, "email-full", "de") == [
        "Von the school office (office@school.example) · 5. November"]
