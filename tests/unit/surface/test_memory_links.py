"""Every card link in the chat opens the card's page in the memory.

A link posted into the family's chat never changes, so it lands where the
family reads the card: its page, `/card/<card id>`. `/go/card/<card id>`
goes straight there, and the page decides which card the id names: a
former id goes on to the current card, a removed card answers 410 with a
page that says who removed it and when, at either address, and an id no
card ever had is a 404. `/go/capture/<id>`, `/go/diary/<entry id>` and
`/go/docs/<n>` (a document's card by its Paperless number; Paperless when
no card has it) land on the card's address in one redirect. A list link
names one list. Without the memory, a card link and a card's page say
that the memory is not set up yet. `/go/<card id>` without `card/` is a
404: no released version wrote it. Topic and person links keep landing in the
wiki.

Driven through the tools server's FastAPI app (the Browser DSL), as a
phone on the home network opens a link. The memory is memory's test
instance: a real curator over a bare repository holding the Simpsons'
cards. The resolver reads the curator's clone where it reads the wiki's
copy on an instance (`/brain`) and the memory's repository (`/vault`):
the same tree, the same history. The host API that says which stacklets
are set up is the dashboard's stand-in (`tests/dashboard_host.py`),
answering as `tests/unit/surface/setup_stand_in.py` wires it.
"""

from __future__ import annotations


import pytest

from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import VAULT

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT

POLICY = "document-3f9a0c1b2d"          # Paperless number 72
GRILL = "note-0b6e2d91aa"
HOMER = "person-homer"
CAPTURE_ID = "$Efjml6ySCYyWM6xsNwRCSSEund-u2CIywGpgC8u6tvY"
CAMPSITE = "note-5d0c3e8a71"            # was a bookmark first
ENTRY_ID = "4a1b2c3d4e"
LUNCH = "diary-4a1b2c3d4e"
GONE = "note-6e5d4c3b2a"
GONE_PATH = "marge/notes/2026/10/old-campsite.md"

CARDS = {
    **VAULT,
    "family/camping/notes/2026/08/campsite-3a338e.md": (
        f"---\ntype: note\ncard_id: {CAMPSITE}\nformer_card_ids:\n  - bookmark-a1b2c3d4e5\n"
        f"title: Campsite booked at Lake Springfield\ncapture_id: \"{CAPTURE_ID}\"\n---\n\n"
        "# Campsite booked at Lake Springfield\n\nPitch 14, by the lake. Bring the big tent.\n"),
    "family/diary/entries/2026/08/2026-08-14-4a1b2c3d4e.md": (
        f"---\ntype: diary\ncard_id: {LUNCH}\ntitle: Bart's first bike ride\n"
        f"date: \"2026-08-14\"\nentry_id: {ENTRY_ID}\n---\n\nBart rode to the end of Evergreen Terrace.\n"),
    GONE_PATH: (
        f"---\ntype: note\ncard_id: {GONE}\ntitle: The old campsite\n---\n\nToo close to the plant.\n"),
}


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    """The Simpsons' memory, after Marge removed the old campsite note on
    3 November in Forgejo."""
    with running_memory(tmp_path_factory.mktemp("links"), CARDS) as running:
        running.edited_in_forgejo({}, removed=(GONE_PATH,), by="marge",
                                  subject="remove: the old campsite", at="2026-11-03T10:00:00")
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    """Core's tools server, reading the memory as it does on an instance."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    monkeypatch.setattr(server, "BRAIN_DIR", memory.vault)
    monkeypatch.setattr(server, "VAULT_DIR", memory.vault)
    return server.app


@pytest.fixture
def without_memory(docker_with_no_containers, tmp_path, monkeypatch):
    """Core's tools server on an instance where the chat runs and the memory
    was never set up: the mounts are empty folders, the cards API does not
    answer (conftest's closed port), and the host API's `stack list` says
    the memory is available, not set up."""
    server = tools_server("server")
    from stack.live import client

    host = DashboardHost.in_state("chat", root=tmp_path / "instance")
    monkeypatch.setattr(client, "call", lambda request, timeout: host.answer(request)[0])
    monkeypatch.setattr(server, "BRAIN_DIR", tmp_path / "brain")
    monkeypatch.setattr(server, "VAULT_DIR", tmp_path / "vault")
    return server.app


def taps(app, path: str, language: str = "en") -> Page:
    """What a phone on the home network gets when it opens `path`."""
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000",
                   language=language).opens(path)


def lands(app, path: str) -> str:
    page = taps(app, path)
    assert page.status == 302, page
    return page.location


def opens(app, path: str, language: str = "en") -> Page:
    """What a phone shows in the end: `path`, or the page its one
    redirect lands on."""
    page = taps(app, path, language)
    return taps(app, page.location, language) if page.status == 302 else page


# ── A card link opens the card's page ─────────────────────────────────────

def test_a_card_link_opens_the_cards_page(tools):
    """Given a filing's link `/go/card/<card id>`, when it is tapped, then the
    card's page opens."""
    assert lands(tools, f"/go/card/{GRILL}") == f"/card/{GRILL}"
    assert taps(tools, f"/card/{GRILL}").status == 200


def test_a_person_lands_on_their_card_page(tools):
    """A person's link lands on their card page, which links their page in
    the wiki."""
    assert lands(tools, f"/go/card/{HOMER}") == f"/card/{HOMER}"


def test_a_former_id_opens_the_current_cards_page(tools):
    """The link goes to the address it names; that page goes on to the
    card that carries the id now."""
    assert lands(tools, "/go/card/bookmark-a1b2c3d4e5") == "/card/bookmark-a1b2c3d4e5"
    assert lands(tools, "/card/bookmark-a1b2c3d4e5") == f"/card/{CAMPSITE}"


def test_a_capture_link_and_a_diary_link_open_the_card_they_name(tools):
    """The links posted before card ids name the card by its capture's or
    entry's id; each lands on that card's address in one redirect."""
    assert lands(tools, f"/go/capture/{CAPTURE_ID}") == f"/card/{CAMPSITE}"
    assert lands(tools, f"/go/diary/{ENTRY_ID}") == f"/card/{LUNCH}"


def test_a_document_link_opens_the_documents_card(tools):
    """Given `/go/docs/72` and a document card with Paperless number 72,
    then the card's page in one redirect, not Paperless."""
    assert lands(tools, "/go/docs/72") == f"/card/{POLICY}"


def test_a_list_link_opens_the_list_by_its_id(tools):
    assert lands(tools, "/go/list/einkaufsliste") == "/card/list-einkaufsliste"


def test_a_list_link_names_one_list(tools):
    """A name with a wildcard in it names no list, rather than the first
    list it matches."""
    for name in ("*", "%5Ba-z%5D%2A", "einkaufs%3Fiste"):
        assert taps(tools, f"/go/list/{name}").status == 404, name


def test_a_document_without_a_card_opens_in_paperless(tools, monkeypatch):
    server = tools_server("server")

    monkeypatch.setattr(server, "DOCS_PUBLIC_URL", "http://192.168.1.20:42020")
    assert lands(tools, "/go/docs/999") == "http://192.168.1.20:42020/documents/999/details"


def test_the_cards_page_previews_its_summary_in_the_chat(tools):
    """A chat app previews the page a `/go` link lands on by its title and
    description: the card's title and the first words of its text."""
    page = taps(tools, f"/card/{CAMPSITE}")

    assert '<meta property="og:title" content="Campsite booked at Lake Springfield">' in page.body
    assert ('<meta property="og:description" '
            'content="Pitch 14, by the lake. Bring the big tent.">') in page.body


# ── A removed card, an unknown id ─────────────────────────────────────────

def test_a_removed_card_says_who_removed_it_and_when(tools):
    """Given a card Marge removed on 3 November, when its link is tapped,
    then a page says it is gone, by whom and when, and leads back to the
    dashboard.
    It answers 410, so a link checker knows it is gone for good."""
    page = opens(tools, f"/go/card/{GONE}")

    assert page.status == 410
    assert page.texts("title") == ["This card is gone"]
    assert page.texts("intro")[0].startswith("Marge removed it on 3 November")
    assert page.links("actions") == [("‹ Dashboard", "/dashboard")]


def test_a_removed_cards_own_address_says_the_same(tools):
    """Given the address of a removed card, bookmarked or opened from the
    a link earlier, then the same page as its link: 410, who and when."""
    page = taps(tools, f"/card/{GONE}")

    assert page.status == 410
    assert page.texts("title") == ["This card is gone"]
    assert page.texts("intro")[0].startswith("Marge removed it on 3 November")


def test_a_removed_card_in_german(tools):
    page = opens(tools, f"/go/card/{GONE}", language="de")

    assert page.status == 410
    assert page.texts("intro")[0].startswith("Marge hat sie am 3. November")


def test_an_id_no_card_ever_had_is_a_404(tools):
    assert opens(tools, "/go/card/note-0000000000").status == 404
    assert taps(tools, "/card/note-0000000000").status == 404
    assert taps(tools, "/go/card/campsite").status == 404


def test_a_card_link_without_its_kind_is_a_404(tools):
    """Given a card, when `/go/<card id>` is opened without `card/`, then
    it is a 404: every link names its kind, and no released version
    wrote that form."""
    assert taps(tools, f"/go/{GRILL}").status == 404


# ── Without the memory ────────────────────────────────────────────────────

def test_without_the_memory_a_card_link_says_it_is_not_set_up(without_memory):
    """Given an instance whose memory was never set up, when a card link is
    tapped, then a page says so and offers the setup."""
    page = opens(without_memory, f"/go/card/{GRILL}")

    assert page.status == 404
    assert page.texts("title") == ["The family memory is not set up yet"]
    assert page.texts("intro") == ["Once it runs, every link in the chat opens its card here."]
    assert page.links("actions") == [("Set it up", "/setup")]


def test_without_the_memory_a_cards_page_says_it_is_not_set_up(without_memory):
    page = taps(without_memory, f"/card/{GRILL}")

    assert page.texts("title") == ["The family memory is not set up yet"]


def test_without_the_memory_a_document_link_opens_in_paperless(without_memory, monkeypatch):
    server = tools_server("server")

    monkeypatch.setattr(server, "DOCS_PUBLIC_URL", "http://192.168.1.20:42020")
    assert lands(without_memory, "/go/docs/72") == "http://192.168.1.20:42020/documents/72/details"


# ── What stays ────────────────────────────────────────────────────────────

def test_topic_and_person_links_keep_landing_in_the_wiki(tools, monkeypatch):
    """Resting state: the pages compiled from many cards stay in the wiki."""
    server = tools_server("server")

    monkeypatch.setattr(server, "WIKI_PUBLIC_URL", "http://192.168.1.20:42070")
    assert lands(tools, "/go/topic/camping") == "http://192.168.1.20:42070/family/camping/about"
    assert lands(tools, "/go/person/homer/todo") == "http://192.168.1.20:42070/homer/todos"
