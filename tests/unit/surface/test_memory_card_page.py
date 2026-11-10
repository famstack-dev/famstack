"""A card's page, as a phone on the home network reads it.

`/card/<card id>` shows one card in the family wiki's look, one layout
for every type: the type's label, the title, the facts line, the outside
link, the text, people and tags, "History" closed and "Details" closed.
It shows no links between cards and nothing to edit.

Driven through the tools server's FastAPI app (the Browser DSL), read the
way a browser without JavaScript reads the page. The memory is memory's
test instance: a real curator over a bare repository holding the
Simpsons' cards, which core reaches over HTTP on localhost.
"""

from __future__ import annotations


import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import VAULT

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT

POLICY = "document-3f9a0c1b2d"
MENU = "bookmark-5c4d3e2f1a"
GRILL = "note-0b6e2d91aa"
CARDS = {**VAULT, "homer/notes/grill.md": (
    f"---\ntype: note\ncard_id: {GRILL}\ntitle: Grill\ntags:\n  - bbq\n---\n"
    "# Grill\nBuy **charcoal** <script>alert(1)</script>\n")}


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("card-page"), CARDS) as running:
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    """Core's tools server, reaching the memory's curator, in UTC."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    monkeypatch.setattr(server, "DOCS_PUBLIC_URL", "http://localhost:42020")
    return server.app


def phone(app, language: str = "en") -> Browser:
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language)


def opens(app, path: str, language: str = "en") -> Page:
    page = phone(app, language).opens(path)
    assert page.status == 200, page
    return page


# ── A card's page ─────────────────────────────────────────────────────────

def test_a_document_shows_what_it_is_its_facts_and_where_it_lives_outside(tools):
    """Given a document card, when a phone opens its page, then the page
    says it is a document, gives its facts line, and links Paperless."""
    page = opens(tools, f"/card/{POLICY}")

    assert page.texts("kicker") == ["Document"]
    assert page.texts("title") == ["Duff Insurance policy"]
    assert page.texts("facts") == ["Duff Insurance · 2 October · Policy"]
    assert page.texts("extra") == ["Insurance"]
    assert ("Open in PaperlessPaperless…/documents/72/details ↗",
            "http://localhost:42020/documents/72/details") in page.links("target")


def test_the_correspondent_links_its_own_card(tools):
    assert opens(tools, f"/card/{POLICY}").links("facts") == [
        ("Duff Insurance", "/card/family/correspondents/duff-insurance.md")]


def test_history_is_closed_and_carries_no_versions(tools):
    """History loads only when it is opened: the page itself names where
    to ask, and holds nothing but the loading line."""
    page = opens(tools, f"/card/{POLICY}")

    assert page.texts("history") == ["HistoryLoading the history…"]
    assert f'data-src="/card/{POLICY}/history"' in page.body
    assert '<details class="more history" data-src' in page.body
    assert "timeline" not in page.body


def test_a_card_page_shows_no_links_between_cards_and_nothing_to_edit(tools):
    page = opens(tools, f"/card/{POLICY}")

    for gone in ("Linked from", "Links to", "Around this card", "graph"):
        assert gone not in page.text
    for control in ("contenteditable", "<textarea", 'type="text"', "Edit"):
        assert control not in page.body


def test_people_and_tags_show_as_chips(tools):
    page = opens(tools, f"/card/{POLICY}")

    assert page.texts("chip") == ["Homer", "#Medical", "#car-insurance"]
    # A tag is a plain chip, no link: the card pages have no search to lead to.
    assert page.links("chip") == []


def test_the_creation_date_says_the_day_and_the_time_on_hover(tools):
    page = opens(tools, f"/card/{POLICY}")

    assert page.attribute("time", "title") == "Created 2 October 2026, 09:14"
    assert page.attribute("time", "aria-label") == "Created 2 October 2026, 09:14"


def test_a_bookmark_opens_its_page(tools):
    page = opens(tools, f"/card/{MENU}")

    assert page.texts("kicker") == ["Bookmark"]
    assert page.texts("facts") == ["krusty.example · saved by Bart · 11 October"]
    assert page.links("target")[0][1] == "https://krusty.example/menu"
    assert page.texts("bt-label") == ["Open the page"]


def test_a_cards_text_is_shown_and_nothing_in_it_runs(tools):
    page = opens(tools, f"/card/{GRILL}")

    assert "Buy charcoal" in " ".join(page.texts("body"))
    assert "<strong>charcoal</strong>" in page.body
    assert "<script>alert" not in page.body


def test_the_page_in_german(tools):
    page = opens(tools, f"/card/{POLICY}", language="de")

    assert page.texts("kicker") == ["Dokument"]
    assert page.texts("bt-label") == ["In Paperless öffnen"]
    assert page.texts("history") == ["VerlaufVerlauf wird geladen…"]
    assert page.attribute("time", "title") == "Erstellt am 2. Oktober 2026, 09:14"


def test_an_address_no_card_has_says_so(tools):
    page = phone(tools).opens("/card/note-ffffffffff")

    assert page.status == 404
    assert page.texts("notice") == ["No card has this address."]


def test_the_page_says_when_the_memory_does_not_answer(monkeypatch):
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", "http://127.0.0.1:9")
    page = phone(server.app).opens(f"/card/{POLICY}")

    assert page.status == 200
    assert "does not answer" in page.texts("notice")[0]


# ── A card's history ───────────────────────────────────────────────────────

def test_history_when_opened_says_who_did_what_and_when(tools):
    """The fragment the page asks for when History opens: the seed commit
    by Homer, as a person's change."""
    page = opens(tools, f"/card/{POLICY}/history")

    events = page.texts("person")
    assert len(events) == 1
    assert events[0].startswith("Homer changed it")
    assert events[0].endswith("today")
