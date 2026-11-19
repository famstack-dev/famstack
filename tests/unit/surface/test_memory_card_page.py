"""A card's page, as a phone on the home network reads it.

`/card/<card id>` shows one card in the family wiki's look, one layout
for every type: the type's label, the title, the facts line, the outside
link, the text, people and tags, "History" closed and "Details" closed.
It shows no links between cards and nothing to edit.

A device reads only the cards its person may read: a card in a person's
own folder opens on that person's phone and on the Mac's address, and for
every other device it is a card that does not exist (ADR-023's trust
table: a paired device is trusted as much as its person, a shared device
as much as the household).

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
CARDS_PORT = tools_server("access").CARDS_PORT

POLICY = "document-3f9a0c1b2d"
MENU = "bookmark-5c4d3e2f1a"
GRILL = "note-0b6e2d91aa"
NOBODYS = "note-ffffffffff"
# A photo on Homer's own grill note, kept by the media store.
GRILL_PNG = "2026/11/grill.png"
# A person only Homer keeps a card for, in his own folder, and a shared
# note about him: the note's people chip names him by his card's title
# only for a reader who may read that card.
GRAMPA_VISIT = "note-a1b2c3d4e5"
CARDS = {**VAULT, "homer/notes/grill.md": (
    f"---\ntype: note\ncard_id: {GRILL}\ntitle: Grill\ntags:\n  - bbq\n---\n"
    f"# Grill\nBuy **charcoal** <script>alert(1)</script>\n\n![[/media/{GRILL_PNG}]]\n"),
    "homer/people/grampa.md": ("---\ntype: person\ncard_id: person-grampa\ntitle: Abe Simpson\n"
                               "---\n\n# Abe Simpson\n"),
    "family/notes/2026/11/grampa-visit.md": (
        f"---\ntype: note\ncard_id: {GRAMPA_VISIT}\ntitle: Grampa's visit\n"
        "persons:\n  - grampa\n---\n\n# Grampa's visit\nHe brought his old photos.\n")}


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("card-page"), CARDS) as running:
        running.keeps_file(GRILL_PNG, b"\x89PNG not really", filename="grill.png",
                           mime="image/png", size=16)
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    """Core's tools server, reaching the memory's curator, in UTC."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    monkeypatch.setattr(server, "DOCS_PUBLIC_URL", "http://localhost:42020")
    return server.app


def phone(app, language: str = "en", *, person: str = "") -> Browser:
    """A phone on the home network, paired for the household, or as `person`."""
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language,
                   person=person)


def this_mac(app) -> Browser:
    """A browser on the Mac's own address, with the admin key."""
    return Browser(app, listener=CARDS_PORT, host="127.0.0.1:42002")


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


def test_the_sender_opens_its_page_in_the_memory(tools):
    assert opens(tools, f"/card/{POLICY}").links("facts") == [
        ("Duff Insurance", "/memory/sender/correspondent-duff-insurance")]


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
    # Each chip leads on into the memory: a person's page, the search for a
    # tag that names no topic card (test_memory_links has the cases).
    assert page.links("chip") == [("Homer", "/memory/person/homer"),
                                  ("#Medical", "/memory/search?q=Medical"),
                                  ("#car-insurance", "/memory/search?q=car-insurance")]


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
    page = phone(tools, person="homer").opens(f"/card/{GRILL}")

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
    # The frame's header stays, so the person can search or go back.
    assert page.attribute("form", "role") == "search"


# ── The memory's frame ────────────────────────────────────────────────────
# A card is one step in the memory's graph: its page keeps the memory's
# header (the mark, the one search, who looks) and the menu of the
# device's `/memory`, so the walk goes on from the card.

def menu(page: Page) -> list[tuple[str, str]]:
    """The links of the memory's menu on `page`, in order."""
    return page.within("id", "memory-menu").links()


@pytest.mark.parametrize("person", ["marge", ""])
def test_a_card_page_has_the_menu_its_devices_memory_shows(tools, person):
    """Given a phone paired as Marge, or the household's browser, when it
    opens a card, then the side holds the same menu its `/memory` shows:
    the way back to the dashboard, the person's and the family's pages,
    the topics and every kind of card."""
    browser = phone(tools, person=person)

    page = opens_with(browser, f"/card/{POLICY}")

    assert menu(page) == menu(opens_with(browser, "/memory"))
    hrefs = [href for _, href in menu(page)]
    assert "/dashboard" in hrefs and "/memory/family" in hrefs
    assert page.within("data-group", "everything").links()
    assert page.links_marked("aria-current") == []


def test_the_macs_card_page_has_the_menu_of_the_macs_memory(tools):
    mac = this_mac(tools)

    assert menu(opens_with(mac, f"/card/{GRILL}")) == menu(opens_with(mac, "/memory"))


def test_a_card_page_has_the_memorys_header_with_the_one_search(tools):
    """Given Marge's phone on a card, then the header names her, holds the
    search that opens the search layer, and the card is still the card."""
    page = opens_with(phone(tools, person="marge"), f"/card/{POLICY}")

    assert page.attribute("form", "role") == "search"
    assert page.attribute("form", "action") == "/memory/search"
    assert "/memory/static/search_layer.js" in page.scripts()
    assert page.texts("me")[0].startswith("M")
    assert page.texts("title") == ["Duff Insurance policy"]
    assert "<title>Duff Insurance policy · famstack</title>" in page.body


def test_the_missing_page_has_the_frame_and_names_nothing(tools):
    """Given Homer's own note, when Marge's phone opens it, then she gets
    the page of a card that does not exist, byte for byte, in the memory's
    frame with her menu: a frame read for the card asked would tell her
    the card exists."""
    marge = phone(tools, person="marge")

    page = marge.opens(f"/card/{GRILL}")

    assert page.status == 404
    assert page.body == marge.opens(f"/card/{NOBODYS}").body
    assert page.attribute("form", "role") == "search"
    assert menu(page) == menu(opens_with(marge, "/memory"))
    assert "Grill" not in page.text


def test_a_link_no_card_answers_has_the_frame(tools):
    """Given `/card` alone, then its page of a missing card has the frame."""
    page = phone(tools, person="marge").opens("/card")

    assert page.status == 404
    assert page.attribute("form", "role") == "search"


def opens_with(browser: Browser, path: str) -> Page:
    page = browser.opens(path)
    assert page.status == 200, page
    return page


# ── A card's history ───────────────────────────────────────────────────────

def test_history_when_opened_says_who_did_what_and_when(tools):
    """The fragment the page asks for when History opens: the seed commit
    by Homer, as a person's change."""
    page = opens(tools, f"/card/{POLICY}/history")

    events = page.texts("person")
    assert len(events) == 1
    assert events[0].startswith("Homer changed it")
    assert events[0].endswith("today")


# ── Who may read a card ───────────────────────────────────────────────────
# Homer's grill note lies in his own folder (`homer/notes/grill.md`). The
# memory reads a person's folder only for that person; a device reads as
# its person, the household's device as the household, the Mac as the admin.

def missing(browser: Browser, path: str) -> Page:
    """What `browser` gets for `path` with GRILL replaced by an id no card has."""
    return browser.opens(path.replace(GRILL, NOBODYS))


def test_another_persons_phone_gets_the_page_of_a_missing_card(tools):
    """Given Homer's own note, when Marge's phone opens its address, then
    it gets the page of a card that does not exist, byte for byte: a
    refusal that said so would tell her the card exists."""
    marge = phone(tools, person="marge")

    page = marge.opens(f"/card/{GRILL}")

    assert page.status == 404
    assert page.body == missing(marge, f"/card/{GRILL}").body
    assert "Grill" not in page.text


def test_the_owners_phone_opens_their_own_card(tools):
    """Given Homer's own note, when Homer's phone opens it, then he reads it."""
    page = phone(tools, person="homer").opens(f"/card/{GRILL}")

    assert page.status == 200
    assert page.texts("title") == ["Grill"]


def test_the_households_device_reads_only_the_shared_folder(tools):
    """Given a browser paired for the household, then the family's cards
    open and a person's own card is a missing one."""
    browser = phone(tools)

    assert browser.opens(f"/card/{POLICY}").status == 200
    page = browser.opens(f"/card/{GRILL}")
    assert (page.status, page.body) == (404, missing(browser, f"/card/{GRILL}").body)


def test_a_person_card_in_anothers_folder_lends_its_name_to_nobody_else(tools):
    """Given a person card in Homer's own folder (Abe Simpson) and a shared
    note about that person, when Marge's phone opens the note, then the
    people chip does not carry the card's name: the name is the card's
    text, which only Homer's phone and the Mac's address read."""
    path = f"/card/{GRAMPA_VISIT}"

    assert phone(tools, person="marge").opens(path).texts("chip") == ["Grampa"]
    assert phone(tools, person="homer").opens(path).texts("chip") == ["Abe"]


def test_the_macs_address_opens_every_card(tools):
    """Given the Mac's own address with the admin key, then a person's own
    card opens, as the admin's view always did."""
    page = this_mac(tools).opens(f"/card/{GRILL}")

    assert page.status == 200
    assert page.texts("title") == ["Grill"]


@pytest.mark.parametrize("path", [f"/card/{GRILL}/history", f"/card/{GRILL}/text",
                                  "/card/homer/notes/grill.md"])
def test_another_persons_phone_gets_no_history_text_or_path_of_the_card(tools, path):
    """Given Homer's own note, when Marge's phone asks its history, its full
    text or its file's path, then each answers as for a card that does not
    exist."""
    marge = phone(tools, person="marge")

    page = marge.opens(path)

    assert page.status == 404
    expected = marge.opens(f"/card/{NOBODYS}") if path.endswith(".md") else missing(marge, path)
    assert page.body == expected.body


def test_the_owner_still_reads_their_cards_history(tools):
    page = phone(tools, person="homer").opens(f"/card/{GRILL}/history")

    assert page.status == 200
    assert page.texts("person")


def test_a_link_to_another_persons_card_lands_on_the_missing_page(tools):
    """Given Homer's own note, when Marge's phone follows `/go/card/<id>`,
    then it lands where a link to a missing card lands."""
    marge = phone(tools, person="marge")

    link = marge.opens(f"/go/card/{GRILL}")
    landed = marge.opens(link.location)

    assert landed.status == 404
    assert landed.body == missing(marge, f"/card/{GRILL}").body


@pytest.mark.parametrize("query", ["", "?audience=homer"])
def test_another_persons_phone_gets_no_photo_of_the_card(tools, query):
    """Given a photo only Homer's own note shows, when Marge's phone asks
    for it, also naming Homer as the audience, then it answers as a file
    the media store does not have: the device decides who reads, never the
    address."""
    assert status_of(phone(tools, person="marge"), f"/card/api/media/{GRILL_PNG}{query}") == 404
    assert status_of(phone(tools, person="homer"), f"/card/api/media/{GRILL_PNG}") == 200


def status_of(browser: Browser, path: str) -> int:
    """The status a page's own read of `path` answers with, as the browser gets it."""
    return browser.client.get(path, headers={"host": browser.host}).status_code


def test_a_request_from_nobody_reads_the_shared_folder_at_most(tools):
    """Given the internal listener, which knows no device and no admin key,
    when a container asks for Homer's own card, then it gets the missing
    page: only the Mac's address reads as the admin."""
    INTERNAL_PORT = tools_server("access").INTERNAL_PORT
    container = Browser(tools, listener=INTERNAL_PORT, host="stack-core-tools:8000")

    assert container.opens(f"/card/{POLICY}").status == 200
    assert container.opens(f"/card/{GRILL}").status == 404
