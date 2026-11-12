"""The card pages: every card has an address a phone on the home network opens.

A card opens at `/card/<card id>`, on core's public listener (port 42000), so a chat link and a bookmark name a
card and a phone opens it. The pages read only: the cards API's read routes
answer through the public listener, and a write through it is refused from
every device, this Mac included. The host's own path for writes, `/cards/api`
on the listener published on this Mac only, stays as it was.

Driven through the tools server's FastAPI app (the Browser DSL), as a phone
on the home network and a browser on this Mac reach it. The memory is
memory's test instance: a real curator over a bare repository holding the
Simpsons' cards, which core reaches over HTTP on localhost.
"""

from __future__ import annotations


import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import VAULT

from .conftest import tools_server
from .dsl import Browser

CARDS_PORT = tools_server("access").CARDS_PORT
PUBLIC_PORT = tools_server("access").PUBLIC_PORT

GRILL = "note-0b6e2d91aa"
# The grill note was a bookmark once, and kept that id as a former one.
FORMER = "bookmark-1a2b3c4d5e"
CARDS = {**VAULT, "homer/notes/grill.md": (
    f"---\ntype: note\ncard_id: {GRILL}\nformer_card_ids:\n  - {FORMER}\n"
    "title: Grill\n---\n# Grill\nBuy charcoal.\n")}
NOBODYS = "note-ffffffffff"
# One of the fonts the pages' stylesheet loads (`memory_ui/static/memory.css`).
FONT = "fonts/inter-latin-wght-normal.woff2"


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("memory-pages"), CARDS) as running:
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    """Core's tools server, reaching the memory's curator."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    return server.app


def phone(app) -> Browser:
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


def this_mac(app) -> Browser:
    """A browser on the server Mac, at the card browser's old address."""
    return Browser(app, listener=CARDS_PORT, host="127.0.0.1:42002")


def location(app, path: str) -> str:
    browser = phone(app)
    r = browser.client.get(path, follow_redirects=False, headers={"host": browser.host})
    assert r.status_code in (301, 302, 303, 307, 308), r.status_code
    return r.headers["location"]


# ── A card's address ──────────────────────────────────────────────────────

def test_a_phone_opens_a_card_at_its_address(tools):
    """Given a card, when a phone opens `/card/<card id>`, then the
    card's page answers: the address a chat link names works."""
    page = phone(tools).opens(f"/card/{GRILL}")

    assert page.status == 200
    assert "html" in page.kind
    assert page.texts("title") == ["Grill"]


def test_a_card_page_loads_its_files_under_its_own_prefix(tools):
    page = phone(tools).opens(f"/card/{GRILL}")

    assert "/card/static/memory.js" in page.scripts()


@pytest.mark.parametrize("path", ["/card", "/card?q=grill", "/memory", f"/memory/{GRILL}",
                                  "/memory/static/memory.css"])
def test_no_page_for_all_cards_and_none_at_the_old_prefix(tools, path):
    """Given the card pages, when a phone opens `/card` itself or anything
    under `/memory`, then it gets a 404: there is no overview of the
    memory among the card pages, and `/memory` was never released."""
    assert phone(tools).opens(path).status == 404


def test_a_former_id_goes_on_to_the_cards_address(tools):
    """Given a card that changed its id, when its former id opens, then
    the browser goes on to the current one, so a card has one address."""
    assert location(tools, f"/card/{FORMER}") == f"/card/{GRILL}"


def test_a_cards_file_goes_on_to_the_cards_address(tools):
    """An old bookmark named a card by its file; it lands on the id."""
    assert location(tools, "/card/homer/notes/grill.md") == f"/card/{GRILL}"


def test_an_id_no_card_has_answers_404(tools):
    assert phone(tools).opens(f"/card/{NOBODYS}").status == 404


def test_the_page_opens_while_the_memory_does_not_answer(memory, monkeypatch):
    """Given the curator does not answer, then the page still opens, and
    its reads (a font here) answer 503, as the card browser's did."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", "http://127.0.0.1:9")

    assert phone(server.app).opens(f"/card/{GRILL}").status == 200
    assert phone(server.app).reads(f"/card/api/{FONT}")[0] == 503


# ── Reading, from every device ─────────────────────────────────────────────

# The memory's reads a page never makes from a browser: the pages render on
# the server, so a browser reads only their media and fonts.
STORE_READS = ["tree/family", "files/facts.toml", "paths", "pending", "report",
               f"card/{GRILL}", "find?q=charcoal", "people", "history",
               f"cards/{GRILL}/versions", "links/family"]


@pytest.mark.parametrize("route", STORE_READS)
def test_a_phone_reads_nothing_of_the_store_through_the_page_routes(tools, route):
    """Given any device on the home network, when it asks `/card/api`
    for anything but a page's media or fonts, then it gets 404: the store's
    files, paths and sync state stay with the Mac."""
    assert phone(tools).reads(f"/card/api/{route}")[0] == 404


def test_a_refused_read_asks_nothing_of_the_memory(memory, monkeypatch):
    """Given the curator does not answer, then a refused read still says
    404, not 503: it never went to the memory."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", "http://127.0.0.1:9")

    assert phone(server.app).reads("/card/api/tree/family")[0] == 404



def test_the_pages_files_are_served(tools):
    for name in ("memory.js", "memory.css"):
        browser = phone(tools)
        r = browser.client.get(f"/card/static/{name}", headers={"host": browser.host})
        assert r.status_code == 200, name


# ── Writing, from no device ────────────────────────────────────────────────

WRITES = [
    ("PATCH", f"/card/api/cards/{GRILL}"),
    ("POST", "/card/api/commits"),
    ("POST", "/card/api/sync"),
    ("PATCH", f"/cards/api/cards/{GRILL}"),
]


@pytest.mark.parametrize(("method", "path"), WRITES)
@pytest.mark.parametrize("device", ["a phone", "this Mac at port 42000"])
def test_a_write_through_the_public_listener_is_refused(tools, memory, device, method, path):
    """Given the pages are open to the home network, then no write passes
    the public listener, from a phone or from this Mac: nothing on it
    knows who is looking. The card is left as it was."""
    host = "192.168.1.20:42000" if device == "a phone" else "localhost:42000"
    before = memory.new_commits()
    browser = Browser(tools, listener=PUBLIC_PORT, host=host)

    r = browser.client.request(method, path, json={"tags": ["grill"]},
                               headers={"host": host, "if-match": "x", "authorization": "Bearer x"})

    assert 400 <= r.status_code < 500
    assert memory.new_commits() == before


# ── The host's own path, unchanged ─────────────────────────────────────────

def test_this_macs_write_path_still_reaches_the_memory(tools, memory):
    """`stack memory card edit` changes a card through `/cards/api` on the
    listener published on this Mac only. A card edit there is passed on,
    with its token, and lands as a commit."""
    mac = this_mac(tools)
    read = mac.client.get(f"/cards/api/cards/{GRILL}", headers={"host": mac.host})
    assert read.status_code == 200
    before = memory.new_commits()

    r = mac.client.patch(f"/cards/api/cards/{GRILL}", json={"fields": {"tags": ["grill"]}}, headers={
        "host": mac.host, "authorization": f"Bearer {memory.environ['CARDS_WRITE_TOKEN']}",
        "if-match": read.headers["etag"], "famstack-actor": "homer",
        "content-type": "application/merge-patch+json"})

    assert r.status_code == 200, r.text
    assert len(memory.new_commits()) == len(before) + 1


# ── The old address ───────────────────────────────────────────────────────

def test_the_old_address_sends_the_browser_on_to_the_card(tools):
    """Given a bookmark of `/cards#card=<id>` on this Mac, the part after
    `#` never reaches the server. So `/cards` answers a page whose script
    goes on to `/card/<id>`, on the same origin, which serves it."""
    mac = this_mac(tools)
    page = mac.opens("/cards")

    assert page.status == 200
    [script] = page.scripts()
    forward = mac.client.get(script, headers={"host": mac.host})
    assert forward.status_code == 200
    assert "location.replace" in forward.text
    assert '"/card/" + card' in forward.text
    assert mac.opens(f"/card/{GRILL}").status == 200


def test_the_network_still_gets_no_cards_address(tools):
    assert phone(tools).opens("/cards").status == 404
