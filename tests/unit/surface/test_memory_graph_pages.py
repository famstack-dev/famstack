"""The memory's graph pages: a person, a topic, a sender, and the documents by kind.

From "My memory" every person, topic and sender links to a page of its
own, and the documents chips to the documents page:

    /memory/person/lisa                          Lisa's lists, topics, what changed, her cards
    /memory/topic/topic-camping                  the topic's cards, its people and senders
    /memory/sender/correspondent-kwik-e-mart     the documents from that sender, newest first
    /memory/documents, /memory/documents/Receipt one tile per kind, then the newest documents

Each page links on: a card to its page (`/card/<card id>`), a person, a
topic or a sender to its own memory page, so the memory reads as a graph.

The memory decides what a device may see (its overview for the device's
audience). A person, topic or sender the device may not read answers 404
there, exactly as one that does not exist, and the page for both is the
same "not found" page, so it tells nothing about what another person
keeps in their own folder.

Driven through the tools server's FastAPI app (the Browser DSL), read the
way a browser without JavaScript reads the page. The memory is memory's
test instance: a real curator over a bare repository, which core reaches
over HTTP on localhost. The host API is the dashboard's stand-in
(`tests/dashboard_host.py`) on a loopback port.
"""

from __future__ import annotations

import pytest

from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import PEOPLE

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT
CARDS_PORT = tools_server("access").CARDS_PORT

TENT = "note-7e1a2b3c4d"              # camping, names Lisa and Marge
SAXOPHONE = "note-1b2c3d4e5f"         # names Lisa
RECEIPT = "document-4c5d6e7f80"       # a receipt from Kwik-E-Mart, names Marge
OLDER_RECEIPT = "document-3b4c5d6e7f"  # an older one, camping gear
POLICY = "document-2a3b4c5d6e"        # a policy from Springfield Insurance
BOWLING_NOTE = "note-9a0b1c2d3e"      # in Homer's own bowling topic
SCHOOL = "list-school"                # Lisa's list

CAMPING = "topic-camping"
BOWLING = "topic-bowling"             # in Homer's own folder
KWIK = "correspondent-kwik-e-mart"
BOOKIE = "correspondent-fat-tony"     # in Homer's own folder

CARDS = {
    **PEOPLE,
    "family/people/lisa.md": "---\ntype: person\ncard_id: person-lisa\ntitle: Lisa Simpson\n"
                             "account: lisa\n---\n\n# Lisa Simpson\n",
    "family/camping/topic.md": f"---\ntype: topic\ncard_id: {CAMPING}\ntitle: Camping\n---\n\n# Camping\n",
    "family/camping/notes/2026/11/tent.md": (
        f"---\ntype: note\ncard_id: {TENT}\ntitle: Tent for four\ndate: \"2026-11-01\"\n"
        "persons:\n  - lisa\n  - marge\n---\n\n# Tent for four\n"),
    "family/camping/documents/2026/10/gear.md": (
        f"---\ntype: document\ncard_id: {OLDER_RECEIPT}\ntitle: Camping gear\n"
        "date: \"2026-10-12\"\ncorrespondent: Kwik-E-Mart\ndocument_type: Receipt\n"
        "persons:\n  - homer\n---\n\n# Camping gear\n"),
    "family/notes/2026/11/saxophone.md": (
        f"---\ntype: note\ncard_id: {SAXOPHONE}\ntitle: Saxophone lesson\ndate: \"2026-11-03\"\n"
        "persons:\n  - lisa\n---\n\n# Saxophone lesson\n"),
    "family/correspondents/kwik-e-mart.md": (
        f"---\ntype: correspondent\ncard_id: {KWIK}\ntitle: Kwik-E-Mart\n---\n\n# Kwik-E-Mart\n"),
    "family/correspondents/springfield-insurance.md": (
        "---\ntype: correspondent\ncard_id: correspondent-springfield-insurance\n"
        "title: Springfield Insurance\n---\n\n# Springfield Insurance\n"),
    "family/documents/2026/11/receipt.md": (
        f"---\ntype: document\ncard_id: {RECEIPT}\ntitle: Receipt from Kwik-E-Mart\n"
        "date: \"2026-11-02\"\ncorrespondent: Kwik-E-Mart\ndocument_type: Receipt\n"
        "persons:\n  - marge\n---\n\n# Receipt from Kwik-E-Mart\n"),
    "family/documents/2026/09/policy.md": (
        f"---\ntype: document\ncard_id: {POLICY}\ntitle: Car insurance\n"
        "date: \"2026-09-02\"\ncorrespondent: Springfield Insurance\ndocument_type: Policy\n"
        "persons:\n  - homer\n---\n\n# Car insurance\n"),
    "family/lists/school.md": (
        f"---\ntype: list\ncard_id: {SCHOOL}\ntitle: School things\npersons:\n  - lisa\n---\n\n"
        "- [ ] Reed for the sax\n"),
    "homer/bowling/topic.md": f"---\ntype: topic\ncard_id: {BOWLING}\ntitle: Bowling\n---\n\n# Bowling\n",
    "homer/bowling/notes/2026/11/league.md": (
        f"---\ntype: note\ncard_id: {BOWLING_NOTE}\ntitle: League night\ndate: \"2026-11-04\"\n"
        "persons:\n  - homer\n---\n\n# League night\n"),
    "homer/correspondents/fat-tony.md": (
        f"---\ntype: correspondent\ncard_id: {BOOKIE}\ntitle: Fat Tony\n---\n\n# Fat Tony\n"),
}


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("graph-pages"), CARDS) as running:
        yield running


@pytest.fixture
def host(tmp_path, monkeypatch):
    """The host API, answering `stack family` for the Simpsons (Homer is
    the admin) and `stack list` with the memory set up."""
    stand_in = DashboardHost.in_state("services", root=tmp_path / "instance")
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(stand_in.serve()))
    yield stand_in
    stand_in.close()


@pytest.fixture
def tools(memory, host, monkeypatch):
    """Core's tools server, reaching the memory's curator, in UTC."""
    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return tools_server("server").app


def phone(app, *, person: str = "", language: str = "en") -> Browser:
    """A phone on the home network, paired as `person`, or for the household."""
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language,
                   person=person)


def this_mac(app) -> Browser:
    """A browser on the Mac's own address, with the admin key."""
    return Browser(app, listener=CARDS_PORT, host="127.0.0.1:42002")


def opens(browser: Browser, path: str) -> Page:
    page = browser.opens(path)
    assert page.status == 200, page
    return page


def sections(page: Page) -> list[str]:
    return page.attributes("section", "data-section")


def hrefs(page: Page, section: str = "") -> set[str]:
    part = page.within("data-section", section) if section else page
    return {href for _, href in part.links()}


def cards_in(page: Page, section: str) -> list[str]:
    """The card ids a section's lines open, in page order."""
    part = page.within("data-section", section)
    return [href.removeprefix("/card/") for _, href in part.links() if href.startswith("/card/")]


# ── A person ──────────────────────────────────────────────────────────────

def test_a_persons_page_shows_her_lists_topics_changes_and_cards(tools):
    """Given Lisa's cards, when Marge's phone opens /memory/person/lisa,
    then it names Lisa and shows her lists, her topics, what changed and
    the cards that name her, each line opening the card's page."""
    page = opens(phone(tools, person="marge"), "/memory/person/lisa")

    assert page.texts("title") == ["Lisa Simpson"]
    assert {"lists", "topics", "changed", "cards"} <= set(sections(page))
    assert f"/card/{SCHOOL}" in hrefs(page, "lists")
    assert f"/memory/topic/{CAMPING}" in hrefs(page, "topics")
    assert set(cards_in(page, "cards")) == {TENT, SAXOPHONE, SCHOOL}
    assert "/memory/person/marge" in hrefs(page, "links"), "Marge is often with her"


def test_a_persons_page_links_their_profile(tools):
    """Given Marge's phone, when it opens Lisa's memory page and then her
    own, then each links that person's profile: "Lisa's profile" and
    "Your profile", where the devices and the settings are."""
    lisas = opens(phone(tools, person="marge"), "/memory/person/lisa")
    own = opens(phone(tools, person="marge"), "/memory/person/marge")

    assert lisas.links("profile-link") == [("Lisa's profile", "/dashboard/person/lisa")]
    assert own.links("profile-link") == [("Your profile", "/dashboard/person/marge")]


def test_a_persons_page_leaves_out_another_persons_own_cards(tools):
    """Given Homer's own bowling topic, when Marge opens Homer's page,
    then neither the topic nor its note is on it."""
    page = opens(phone(tools, person="marge"), "/memory/person/homer")

    assert BOWLING not in page.body
    assert BOWLING_NOTE not in page.body


# ── A topic ───────────────────────────────────────────────────────────────

def test_a_topics_page_shows_its_cards_people_and_senders(tools):
    page = opens(phone(tools, person="marge"), f"/memory/topic/{CAMPING}")

    assert page.texts("title") == ["Camping"]
    assert set(cards_in(page, "cards")) == {TENT, OLDER_RECEIPT}
    assert {"/memory/person/lisa", "/memory/person/marge", "/memory/person/homer"} <= hrefs(page, "links")
    assert f"/memory/sender/{KWIK}" in hrefs(page, "links")
    assert f"/memory/topic/{CAMPING}" not in hrefs(page, "links"), "the topic is not its own link"


def test_a_topics_page_counts_its_cards_and_those_about_the_person_looking(tools):
    """Given Camping with two cards, one about Marge, then her phone reads
    the count of both, and the number about her."""
    page = opens(phone(tools, person="marge"), f"/memory/topic/{CAMPING}")

    assert page.texts("count-line") == ["2 cards"]
    assert page.texts("about-line") == ["1 about you"]


# ── A sender ──────────────────────────────────────────────────────────────

def test_a_senders_page_lists_its_documents_newest_first_with_their_kinds(tools):
    page = opens(phone(tools, person="marge"), f"/memory/sender/{KWIK}")

    assert page.texts("title") == ["Kwik-E-Mart"]
    assert cards_in(page, "cards") == [RECEIPT, OLDER_RECEIPT]
    assert all("Receipt" in line for line in page.within("data-section", "cards").texts("fl-meta"))


# ── The documents ─────────────────────────────────────────────────────────

def test_the_documents_page_opens_with_one_tile_per_kind_count_first(tools):
    """Given two receipts and a policy, then the page opens with a tile
    for all documents and one per kind, most first, each with its count,
    and then the newest documents, each naming its kind."""
    page = opens(phone(tools, person="marge"), "/memory/documents")

    tiles = page.within("class", "kindcards")
    assert [href for _, href in tiles.links()] == [
        "/memory/documents", "/memory/documents/Receipt", "/memory/documents/Policy"]
    assert tiles.texts("kc-n") == ["3", "2", "1"]
    assert not tiles.texts("kc-line")
    assert tiles.texts("kc-day") == ["Newest 2 November", "Newest 2 November", "Newest 2 September"]
    assert "Kwik-E-Mart" not in tiles.text
    assert cards_in(page, "documents") == [RECEIPT, OLDER_RECEIPT, POLICY]
    assert page.within("data-section", "documents").texts("fl-meta")[0].startswith("Receipt")


def test_the_documents_of_one_kind_keep_every_tile_and_mark_their_own(tools):
    page = opens(phone(tools, person="marge"), "/memory/documents/Policy")

    assert cards_in(page, "documents") == [POLICY]
    assert len(page.within("class", "kindcards").links()) == 3
    assert "/memory/documents/Policy" in page.links_marked("aria-current")


# ── Not found ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("person, hidden, missing", [
    ("marge", f"/memory/topic/{BOWLING}", "/memory/topic/topic-nowhere"),
    ("marge", f"/memory/sender/{BOOKIE}", "/memory/sender/correspondent-nobody"),
    ("", f"/memory/topic/{BOWLING}", "/memory/topic/topic-nowhere"),
])
def test_a_page_the_device_may_not_read_is_the_same_as_one_that_does_not_exist(tools, person, hidden,
                                                                                 missing):
    """Given Homer's own topic and sender, when Marge's phone or the
    household's browser opens them, then each is the memory's "not found"
    page, byte for byte the page of one that does not exist, so it tells
    nothing about Homer's folder."""
    device = phone(tools, person=person)
    secret, nothing = device.opens(hidden), device.opens(missing)

    assert (secret.status, nothing.status) == (404, 404)
    assert secret.body == nothing.body
    assert "Bowling" not in secret.text and "Fat Tony" not in secret.text


@pytest.mark.parametrize("path", ["/memory/person/nobody", "/memory/documents/Nonsense"])
def test_an_unknown_person_or_kind_is_the_same_not_found_page(tools, path):
    """Given a person and a kind of document the memory does not have,
    then each is the "not found" page every other missing thing gets, and
    the page does not repeat what was typed in the address."""
    marge = phone(tools, person="marge")
    page, nothing = marge.opens(path), marge.opens("/memory/topic/topic-nowhere")

    assert page.status == 404
    assert page.body == nothing.body
    assert page.attribute("h1", "tabindex") == "-1"


def test_the_mac_reads_homers_own_topic(tools):
    page = opens(this_mac(tools), f"/memory/topic/{BOWLING}")

    assert cards_in(page, "cards") == [BOWLING_NOTE]


# ── The menu, German, navigation ──────────────────────────────────────────

def test_the_menu_lists_her_topics_with_their_counts(tools):
    """Given Marge's camping topic, then every page's menu lists it under
    "Your topics" with the topic's count, the count its page shows, and
    marks it on the topic's own page."""
    marge = phone(tools, person="marge")

    menu = opens(marge, "/memory/family").within("data-group", "topics")
    assert [href for _, href in menu.links()] == [f"/memory/topic/{CAMPING}"]
    assert menu.texts("n") == ["2"]
    assert opens(marge, f"/memory/topic/{CAMPING}").links_marked("aria-current") == [
        f"/memory/topic/{CAMPING}"]


def test_a_household_device_has_no_topics_of_its_own_in_the_menu(tools):
    assert opens(phone(tools), "/memory/family").within("data-group", "topics").links() == []


@pytest.mark.parametrize("path", ["/memory/person/lisa", f"/memory/topic/{CAMPING}",
                                  f"/memory/sender/{KWIK}", "/memory/documents"])
def test_in_german_every_page_is_german(tools, path):
    english = opens(phone(tools, person="marge"), path)
    german = opens(phone(tools, person="marge", language="de"), path)

    assert german.attribute("html", "lang") == "de"
    assert not set(english.texts("section-title")) & set(german.texts("section-title"))


@pytest.mark.parametrize("path, title", [
    ("/memory/person/lisa", "Lisa Simpson"), (f"/memory/topic/{CAMPING}", "Camping"),
    (f"/memory/sender/{KWIK}", "Kwik-E-Mart"), ("/memory/documents", "Documents")])
def test_each_page_names_itself_and_its_heading_takes_the_focus(tools, path, title):
    page = opens(phone(tools, person="marge"), path)

    assert f"<title>{title} · famstack</title>" in page.body
    assert page.attribute("h1", "tabindex") == "-1"
