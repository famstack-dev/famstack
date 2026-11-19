"""My memory: the memory's page for the person on the paired device.

`/memory` greets the person the device is paired to and shows what the
memory holds for them: how many cards are theirs or name them, the cards
by type, what comes up, the open lists, their topics, what changed, their
bookmarks and their documents by kind. `/memory/family` is the household's
page with the same sections. A household device opens the family's page at
`/memory`, and the Mac's address the admin's page.

Every line links on: a card to its page (`/card/<card id>`), a person, a
topic or a sender to its memory page (`/memory/person/<id>`, ...). The
memory decides what a device may see (the overview it answers for the
device's audience); the page renders that answer and filters nothing
itself, so a card in Homer's own folder that names Marge is not on Marge's
page.

Driven through the tools server's FastAPI app (the Browser DSL), read the
way a browser without JavaScript reads the page. The memory is memory's
test instance: a real curator over a bare repository, which core reaches
over HTTP on localhost. The host API, which names the family and its admin,
is the dashboard's stand-in (`tests/dashboard_host.py`) on a loopback port.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import PEOPLE

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT
CARDS_PORT = tools_server("access").CARDS_PORT

# A day the overview's "Up next" holds: it looks 60 days ahead of the
# curator's today, which is the real one.
AHEAD = (date.today() + timedelta(days=10)).isoformat()

TENT = "note-7e1a2b3c4d"
PIE = "bookmark-2b3c4d5e6f"
RECEIPT = "document-4c5d6e7f80"
DENTIST = "note-5d6e7f8091"
SKATEBOARD = "note-6e7f8091a2"
MOES = "diary-8091a2b3c4"              # Homer's own diary entry
SURPRISE = "note-7f8091a2b3"          # in Homer's own folder, names Marge
GROCERIES = "list-groceries"
LISAS = "note-8091a2b3c4d5"           # in Lisa's own folder, names Homer

CARDS = {
    **PEOPLE,
    "family/camping/topic.md": "---\ntype: topic\ncard_id: topic-camping\ntitle: Camping\n---\n\n# Camping\n",
    "family/camping/notes/2026/11/tent.md": (
        f"---\ntype: note\ncard_id: {TENT}\ntitle: Tent for four\ndate: \"2026-11-01\"\n"
        "persons:\n  - marge\n  - grampa\n---\n\n# Tent for four\n"),
    # Grampa's person card is in Homer's own folder: only Homer's phone and
    # the Mac read his page, though a family card names him.
    "homer/people/grampa.md": "---\ntype: person\ncard_id: person-grampa\ntitle: Abe Simpson\n"
                              "---\n\n# Abe Simpson\n",
    # In Lisa's own folder, and about Homer: not on his page, also not on the Mac.
    "lisa/notes/2026/11/saxophone.md": (
        f"---\ntype: note\ncard_id: {LISAS}\ntitle: Dad fell asleep at my concert\n"
        "date: \"2026-11-07\"\npersons:\n  - homer\n---\n\n# Dad fell asleep at my concert\n"),
    "family/bookmarks/2026/11/pie.md": (
        f"---\ntype: bookmark\ncard_id: {PIE}\ntitle: Pie recipe\ndate: \"2026-11-03\"\n"
        "persons:\n  - marge\nresource: \"https://pie.example/recipe\"\n---\n\n# Pie recipe\n"),
    "family/correspondents/kwik-e-mart.md": (
        "---\ntype: correspondent\ncard_id: correspondent-kwik-e-mart\ntitle: Kwik-E-Mart\n---\n\n"
        "# Kwik-E-Mart\n"),
    "family/documents/2026/11/receipt.md": (
        f"---\ntype: document\ncard_id: {RECEIPT}\ntitle: Receipt from Kwik-E-Mart\n"
        "date: \"2026-11-02\"\ncorrespondent: Kwik-E-Mart\ndocument_type: Receipt\n"
        "persons:\n  - marge\n---\n\n# Receipt from Kwik-E-Mart\n"),
    "family/notes/2026/11/dentist.md": (
        f"---\ntype: note\ncard_id: {DENTIST}\ntitle: Dentist with Lisa\ndate: \"{AHEAD}\"\n"
        "persons:\n  - marge\n---\n\n# Dentist with Lisa\n"),
    "family/notes/2026/11/skateboard.md": (
        f"---\ntype: note\ncard_id: {SKATEBOARD}\ntitle: New skateboard\ndate: \"2026-11-04\"\n"
        "persons:\n  - bart\n---\n\n# New skateboard\n"),
    "homer/notes/2026/11/surprise.md": (
        f"---\ntype: note\ncard_id: {SURPRISE}\ntitle: A present for Marge\ndate: \"2026-11-05\"\n"
        "persons:\n  - marge\n---\n\n# A present for Marge\n"),
    "homer/diary/2026/11/moes.md": (
        f"---\ntype: diary\ncard_id: {MOES}\ntitle: An evening at Moe's\ndate: \"2026-11-06\"\n"
        "persons:\n  - homer\n---\n\n# An evening at Moe's\n"),
    "family/lists/groceries.md": (
        f"---\ntype: list\ncard_id: {GROCERIES}\ntitle: Groceries\n---\n\n- [ ] Milk\n- [ ] Duff\n"),
}

# The sections each page shows, by the `data-section` of each one.
MY_SECTIONS = ["up_next", "lists", "topics", "changed", "bookmarks", "documents", "links"]
FAMILY_SECTIONS = ["up_next", "lists", "changed", "documents", "links"]


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("my-memory"), CARDS) as running:
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


@pytest.fixture
def without_memory(host, docker_with_no_containers):
    """Core's tools server where the memory is set up but does not answer:
    the cards API is conftest's closed port, and the host's `stack list`
    says the memory runs (Docker answers it with no container)."""
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
    """The sections of `page`, in page order, by name."""
    return page.attributes("section", "data-section")


def hrefs(page: Page, section: str = "") -> set[str]:
    """Where the links of `page` lead, or of its one section `section`."""
    part = page.within("data-section", section) if section else page
    return {href for _, href in part.links()}


def empty_sections(page: Page) -> list[str]:
    """The sections of `page` that show their empty line."""
    return [s for s in sections(page) if page.within("data-section", s).texts("empty-text")]


# ── My memory ─────────────────────────────────────────────────────────────

def test_my_memory_greets_the_person_and_shows_every_section(tools):
    """Given Marge's phone, when it opens /memory, then the page greets
    Marge, says how many cards are hers or name her, and shows her
    sections in the page's order."""
    page = opens(phone(tools, person="marge"), "/memory")

    assert page.texts("title") == ["Hello, Marge"]
    assert sections(page) == MY_SECTIONS
    # Tent, pie recipe, receipt and the dentist, which name her, and the
    # groceries, which name no one and are everyone's.
    assert page.texts("count-line")[0].startswith("5 ")


def test_my_memory_shows_her_cards_by_type(tools):
    page = opens(phone(tools, person="marge"), "/memory")

    assert sorted(page.attributes("type-chip", "data-type")) == ["bookmark", "document", "list", "note"]


def test_each_section_holds_her_cards_and_links_each_to_its_page(tools):
    """Given Marge's cards, then each section lists the right card and
    each card line opens the card's page."""
    page = opens(phone(tools, person="marge"), "/memory")

    assert f"/card/{DENTIST}" in hrefs(page, "up_next")
    assert f"/card/{GROCERIES}" in hrefs(page, "lists")
    assert "/memory/topic/topic-camping" in hrefs(page, "topics")
    assert f"/card/{PIE}" in hrefs(page, "bookmarks")
    assert "/memory/documents/Receipt" in hrefs(page, "documents")
    assert f"/card/{RECEIPT}" in hrefs(page, "changed")


def test_every_type_chip_has_its_icon(tools):
    """Given the Mac's address, whose page holds Homer's diary entry, then
    every type chip draws its type's icon, the diary's too: a chip with an
    empty mark tells nobody what it counts."""
    page = opens(this_mac(tools), "/memory")

    types = page.attributes("type-chip", "data-type")
    assert "diary" in types
    assert [t for t in types if f'data-type="{t}"><svg' not in page.body] == []


def test_a_sender_and_a_person_link_to_their_memory_pages(tools):
    page = opens(phone(tools, person="marge"), "/memory")

    assert "/memory/sender/correspondent-kwik-e-mart" in hrefs(page)
    assert "/memory/person/marge" not in hrefs(page, "links"), "her own page is not among her links"


def test_a_person_whose_page_the_device_may_not_read_has_no_link(tools):
    """Given a family note that names Grampa, whose person card is in
    Homer's own folder, when Marge's phone opens /memory, then the note's
    row names him as the note writes him, without a link: his person card
    and his page are ones she may not read, so neither its title nor a
    link shows."""
    page = opens(phone(tools, person="marge"), "/memory")

    assert "Grampa" in page.text
    assert "Abe" not in page.text
    assert not [href for href in hrefs(page) if href.startswith("/memory/person/grampa")]


def test_another_persons_own_card_that_names_her_is_not_on_her_page(tools):
    """Given a note in Homer's own folder that names Marge, when Marge's
    phone opens /memory, then the note is nowhere on the page: the memory
    answers her overview without it, and the page adds nothing."""
    page = opens(phone(tools, person="marge"), "/memory")

    assert SURPRISE not in page.body
    assert "A present for Marge" not in page.text


# ── The family's page ─────────────────────────────────────────────────────

def test_the_family_page_shows_the_households_sections(tools):
    page = opens(phone(tools, person="marge"), "/memory/family")

    assert sections(page) == FAMILY_SECTIONS
    assert f"/card/{SKATEBOARD}" in hrefs(page, "changed")
    assert "/memory/person/bart" in hrefs(page, "links")


def test_another_persons_own_card_is_not_on_the_family_page_either(tools):
    """Given a note in Homer's own folder that names Marge, when Marge's
    phone opens the family's page, then the note is nowhere on it: the
    family's page shows what her device may read, not what Homer may."""
    page = opens(phone(tools, person="marge"), "/memory/family")

    assert SURPRISE not in page.body
    assert "A present for Marge" not in page.text


def test_a_household_device_opens_the_family_page_at_memory(tools):
    page = opens(phone(tools), "/memory")

    assert sections(page) == FAMILY_SECTIONS
    assert page.texts("title") == ["The Simpsons"]
    assert SURPRISE not in page.body


def test_the_macs_address_opens_the_admins_page(tools):
    """Given the Mac's address, which reads every card, then /memory is the
    admin's page: Homer's, with his own note in it."""
    page = opens(this_mac(tools), "/memory")

    assert page.texts("title") == ["Hello, Homer"]
    assert SURPRISE in page.body


def test_the_macs_address_shows_what_the_admins_phone_shows(tools):
    """Given a note in Lisa's own folder that names Homer, when the Mac's
    address opens /memory, then the note is not on Homer's page, as it is
    not on his phone: the page is his, so it holds what he may read."""
    mac = opens(this_mac(tools), "/memory")
    homers_phone = opens(phone(tools, person="homer"), "/memory")

    assert LISAS not in mac.body
    assert hrefs(mac, "topics") == hrefs(homers_phone, "topics")
    assert {h for h in hrefs(mac) if h.startswith("/card/")} == \
        {h for h in hrefs(homers_phone) if h.startswith("/card/")}


# ── Empty, unanswered, German ─────────────────────────────────────────────

def test_a_section_with_nothing_in_it_says_so(tools):
    """Given Homer's phone (his one card is a note in his own folder), then
    every section with nothing for him shows its empty line instead of
    vanishing, so the page keeps its shape."""
    page = opens(phone(tools, person="homer"), "/memory")

    assert page.texts("title") == ["Hello, Homer"]
    assert sections(page)[:6] == MY_SECTIONS[:6]
    assert set(empty_sections(page)) == {"up_next", "topics", "bookmarks", "documents"}


def test_a_memory_that_does_not_answer_is_said_on_the_page(without_memory):
    page = phone(without_memory, person="marge").opens("/memory")

    assert page.status == 200
    assert len(page.texts("notice")) == 1
    assert sections(page) == []


def test_in_german_the_page_is_german(tools):
    english = opens(phone(tools, person="marge"), "/memory")
    german = opens(phone(tools, person="marge", language="de"), "/memory")

    assert german.attribute("html", "lang") == "de"
    assert german.texts("title") == ["Hallo, Marge"]
    assert not set(english.texts("section-title")) & set(german.texts("section-title"))
    assert english.texts("menu-label") != german.texts("menu-label")


# ── Navigation and accessibility ─────────────────────────────────────────

def test_the_heading_takes_the_focus_and_the_title_names_the_page(tools):
    page = opens(phone(tools, person="marge"), "/memory")

    assert page.attribute("h1", "tabindex") == "-1"
    assert "<title>My memory · famstack</title>" in page.body
    family = opens(phone(tools, person="marge"), "/memory/family")
    assert "<title>The family · famstack</title>" in family.body


def test_the_link_to_the_current_page_says_so(tools):
    page = opens(phone(tools, person="marge"), "/memory")

    assert page.links_marked("aria-current") == ["/memory"]
    family = opens(phone(tools, person="marge"), "/memory/family")
    assert family.links_marked("aria-current") == ["/memory/family"]


def test_a_household_device_marks_only_the_page_it_shows(tools):
    """Given a household device, whose `/memory` shows the family's page,
    then the menu marks the family's link only on `/memory/family`, the
    address the link leads to, and no link on `/memory`."""
    assert opens(phone(tools), "/memory/family").links_marked("aria-current") == ["/memory/family"]
    assert opens(phone(tools), "/memory").links_marked("aria-current") == []


def test_the_macs_address_falls_back_to_the_family_page_at_memory(tmp_path, host, monkeypatch):
    """Given a memory with no person card for the admin, when the Mac's
    address opens /memory, then it gets the family's page there, and the
    menu marks the link to /memory, the page shown, not the family's link."""
    cards = {k: v for k, v in CARDS.items() if k != "family/people/homer.md"}
    with running_memory(tmp_path / "no-admin-card", cards) as memory:
        monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
        monkeypatch.setenv("TIMEZONE", "UTC")
        page = opens(this_mac(tools_server("server").app), "/memory")

    assert sections(page) == FAMILY_SECTIONS
    assert page.links_marked("aria-current") == ["/memory"]


def test_the_header_holds_the_one_search(tools):
    page = opens(phone(tools, person="marge"), "/memory")

    assert page.attribute("input", "type") == "search"
    assert page.attribute("form", "role") == "search"
