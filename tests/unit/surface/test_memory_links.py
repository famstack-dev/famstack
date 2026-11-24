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
404: no released version wrote it. A shared topic's link keeps landing in the
wiki. A person's link and a person's own topic land on the memory pages,
because the wiki shows only what the family shares; a device that may not
read the topic gets a 404.

A card's page leads on into the memory's pages: a person's chip opens the
person's page when the device may read it, a topic's tag the topic's page,
any other tag the memory's search with the tag filled in, a document's
sender the sender's page, and the side bar's "Memory" opens `/memory`.

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
# Homer's own cards, which only he and the Mac read: a list, a capture, a
# document filed from Paperless number 99, and a garden list Marge has too.
PARTY = "list-surprise-party"
HOMERS_CAPTURE_ID = "$HomersOwnCaptureEventId0123456789abcdefghij"
HOMERS_CAPTURE = "note-1c2d3e4f5a"
HOMERS_DOCUMENT = "document-9a8b7c6d5e"
HOMERS_ENTRY_ID = "7b8c9d0e1f"
HOMERS_ENTRY = "diary-7b8c9d0e1f"
# A family note in the Itchy & Scratchy Land topic's folder that names Lisa
# and Grampa, tagged with the topic and with a word no topic has.
SAXOPHONE = "note-5a4b3c2d1e"
SAXOPHONE_PATH = "family/itchy-scratchy-land/notes/2026/10/saxophone.md"

POT_ROAST = "note-6b5a4c3d2e"
FLU_SHOT = "note-8c7b6a5d4e"
FLU_SHOT_PATH = "family/medical/notes/2026/11/flu-shot.md"

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
    "homer/lists/surprise-party.md": (
        f"---\ntype: list\ncard_id: {PARTY}\ntitle: Surprise party\n---\n\n- [ ] Cake\n"),
    "homer/notes/2026/11/moes.md": (
        f"---\ntype: note\ncard_id: {HOMERS_CAPTURE}\ntitle: Moe's tab\n"
        f"capture_id: \"{HOMERS_CAPTURE_ID}\"\n---\n\nPay it before Marge sees it.\n"),
    "homer/documents/2026/11/bowling.md": (
        f"---\ntype: document\ncard_id: {HOMERS_DOCUMENT}\ntitle: Bowling league\n"
        "paperless_id: 99\n---\n\nMembership.\n"),
    "homer/diary/entries/2026/11/2026-11-12-7b8c9d0e1f.md": (
        f"---\ntype: diary\ncard_id: {HOMERS_ENTRY}\ntitle: Bowling night\n"
        f"date: \"2026-11-12\"\nentry_id: {HOMERS_ENTRY_ID}\n---\n\nA 300 game, nearly.\n"),
    "homer/lists/garden.md": (
        "---\ntype: list\ncard_id: list-garden-homer\ntitle: Garden\n---\n\n- [ ] Mow\n"),
    "marge/lists/garden.md": (
        "---\ntype: list\ncard_id: list-garden-marge\ntitle: Garden\n---\n\n- [ ] Roses\n"),
    # Lisa's person card is the family's; Grampa's is in Homer's own folder,
    # so only Homer's phone and the Mac read his page.
    "family/people/lisa.md": "---\ntype: person\ncard_id: person-lisa\ntitle: Lisa Simpson\n"
                             "account: lisa\n---\n\n# Lisa Simpson\n",
    "homer/people/grampa.md": "---\ntype: person\ncard_id: person-grampa\ntitle: Abe Simpson\n"
                              "---\n\n# Abe Simpson\n",
    # Marge goes by "Mom" too, and a family note names her so.
    "family/people/marge.md": "---\ntype: person\ncard_id: person-marge\ntitle: Marge Simpson\n"
                              "aliases:\n  - Mom\naccount: marge\n---\n\n# Marge Simpson\n",
    "family/notes/2026/11/pot-roast.md": (
        f"---\ntype: note\ncard_id: {POT_ROAST}\ntitle: Pot roast on Sunday\n"
        "persons:\n  - Mom\n---\n\nMom makes the pot roast.\n"),
    # Marge's own topic: only her phone and the Mac read it.
    "marge/geschenke/topic.md": ("---\ntype: topic\ncard_id: topic-geschenke\ntitle: Geschenke\n"
                                 "---\n\n# Geschenke\n"),
    # A folder topic named like the ontology's topic `medical`, and a note
    # in it tagged `medical`: the ontology's tag, which names no folder.
    "family/medical/topic.md": "---\ntype: topic\ncard_id: topic-medical\ntitle: Doctors\n---\n\n# Doctors\n",
    FLU_SHOT_PATH: (
        f"---\ntype: note\ncard_id: {FLU_SHOT}\ntitle: Flu shots\ntags:\n  - medical\n---\n\n"
        "Everyone on Saturday.\n"),
    SAXOPHONE_PATH: (
        f"---\ntype: note\ncard_id: {SAXOPHONE}\ntitle: Saxophone at the park\n"
        "persons:\n  - lisa\n  - grampa\ntags:\n  - itchy-scratchy-land\n  - jazz\n---\n\n"
        "Lisa played for Grampa by the gate.\n"),
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


def taps(app, path: str, language: str = "en", *, person: str = "") -> Page:
    """What a phone on the home network gets when it opens `path`: the
    household's, or `person`'s own."""
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000",
                   language=language, person=person).opens(path)


def lands(app, path: str) -> str:
    return lands_as(app, path, "")


def lands_as(app, path: str, person: str) -> str:
    """Where `path` sends `person`'s phone ("" for the household's)."""
    page = taps(app, path, person=person)
    assert page.status == 302, page
    return page.location


def opens(app, path: str, language: str = "en", *, person: str = "") -> Page:
    """What a phone shows in the end: `path`, or the page its one
    redirect lands on."""
    page = taps(app, path, language, person=person)
    return taps(app, page.location, language, person=person) if page.status == 302 else page


# ── A card link opens the card's page ─────────────────────────────────────

def test_a_card_link_opens_the_cards_page(tools):
    """Given a filing's link `/go/card/<card id>`, when it is tapped, then the
    card's page opens."""
    assert lands(tools, f"/go/card/{GRILL}") == f"/card/{GRILL}"
    assert taps(tools, f"/card/{GRILL}", person="homer").status == 200


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
    dashboard. The card was in Marge's own folder, so it is her phone.
    It answers 410, so a link checker knows it is gone for good."""
    page = opens(tools, f"/go/card/{GONE}", person="marge")

    assert page.status == 410
    assert page.texts("title") == ["This card is gone"]
    assert page.texts("intro")[0].startswith("Marge removed it on 3 November")
    assert page.links("actions") == [("‹ Dashboard", "/dashboard")]


def test_a_removed_cards_own_address_says_the_same(tools):
    """Given the address of a removed card, bookmarked or opened from the
    a link earlier, then the same page as its link: 410, who and when."""
    page = taps(tools, f"/card/{GONE}", person="marge")

    assert page.status == 410
    assert page.texts("title") == ["This card is gone"]
    assert page.texts("intro")[0].startswith("Marge removed it on 3 November")


def test_a_removed_card_in_german(tools):
    page = opens(tools, f"/go/card/{GONE}", language="de", person="marge")

    assert page.status == 410
    assert page.texts("intro")[0].startswith("Marge hat sie am 3. November")


@pytest.mark.parametrize("person", ["homer", ""])
def test_a_removed_card_of_another_person_is_a_missing_card(tools, person):
    """Given a card Marge removed from her own folder, when Homer's phone
    or the household's opens its address, then it gets the page of a card
    that never was: who removed what from Marge's folder is hers."""
    page = taps(tools, f"/card/{GONE}", person=person)

    assert page.status == 404
    assert page.body == taps(tools, "/card/note-0000000000", person=person).body


# ── A link to another person's own card ──────────────────────────────────

@pytest.mark.parametrize("link, unknown", [
    ("/go/list/surprise-party", "/go/list/nothing-here"),
    (f"/go/capture/{HOMERS_CAPTURE_ID}", "/go/capture/$NoSuchCaptureEventId0123456789abcdefghijk"),
    (f"/go/diary/{HOMERS_ENTRY_ID}", "/go/diary/0000000000"),
])
def test_a_link_to_another_persons_card_answers_as_an_unknown_name(tools, link, unknown):
    """Given Homer's own list, capture and diary entry, when Marge's phone opens their
    links, then each answers with the status and page of a name no card
    has: a redirect would tell her the card exists, and its id."""
    page, nothing = taps(tools, link, person="marge"), taps(tools, unknown, person="marge")

    assert (page.status, page.location, page.body) == (nothing.status, nothing.location, nothing.body)
    assert page.status == 404


@pytest.mark.parametrize("link, card", [("/go/list/surprise-party", PARTY),
                                        (f"/go/capture/{HOMERS_CAPTURE_ID}", HOMERS_CAPTURE),
                                        (f"/go/diary/{HOMERS_ENTRY_ID}", HOMERS_ENTRY),
                                        ("/go/docs/99", HOMERS_DOCUMENT)])
def test_the_owners_phone_lands_on_their_own_card(tools, link, card):
    assert lands_as(tools, link, "homer") == f"/card/{card}"


def test_a_document_in_another_persons_folder_answers_as_a_document_without_a_card(tools):
    """Given Paperless document 99, filed into Homer's own folder, when
    Marge's phone opens its link, then it answers as the link of a document
    no card was filed from (98): Paperless, or a 404 with no Paperless."""
    page = taps(tools, "/go/docs/99", person="marge")
    without = taps(tools, "/go/docs/98", person="marge")

    assert (page.status, page.location.replace("99", "98"), page.body) == \
        (without.status, without.location, without.body)
    assert HOMERS_DOCUMENT not in page.location


def test_a_list_name_two_people_have_opens_the_readers_own(tools):
    """Given a garden list in Homer's folder and one in Marge's, when
    Marge's phone opens `/go/list/garden`, then it lands on hers."""
    assert lands_as(tools, "/go/list/garden", "marge") == "/card/list-garden-marge"
    assert lands_as(tools, "/go/list/garden", "homer") == "/card/list-garden-homer"


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

def test_a_shared_topic_link_keeps_landing_in_the_wiki(tools, monkeypatch):
    """Resting state: the pages compiled from the family's cards stay in the wiki."""
    server = tools_server("server")

    monkeypatch.setattr(server, "WIKI_PUBLIC_URL", "http://192.168.1.20:42070")
    assert lands(tools, "/go/topic/camping") == "http://192.168.1.20:42070/family/camping/about"
    assert lands(tools, "/go/topic/family/camping") == "http://192.168.1.20:42070/family/camping/about"


# ── A person's own part opens in the memory ───────────────────────────────
#
# The wiki is one site for every paired device, so it shows only what the
# family shares. A person's page and their own topics are on the memory
# pages, which check who asks.

def test_a_person_link_lands_on_their_memory_page(tools):
    assert lands(tools, "/go/person/homer") == "/memory/person/homer"
    assert lands(tools, "/go/person/homer/todo") == "/memory/person/homer"


def test_a_persons_own_topic_link_lands_on_its_memory_page(tools):
    """Given Marge's own topic, when her phone taps its link, then the topic's
    memory page opens; it is not sent to the wiki."""
    assert lands_as(tools, "/go/topic/marge/geschenke", "marge") == "/memory/topic/topic-geschenke"


def test_a_persons_own_topic_link_is_a_404_for_someone_else(tools):
    """Given Marge's own topic, when Homer's phone taps its link, then it is a
    404, as for a topic that does not exist."""
    assert taps(tools, "/go/topic/marge/geschenke", person="homer").status == 404


# ── A card's page leads on into the memory ────────────────────────────────
#
# The memory's pages are a graph: a card leads to its people, its topic and
# its sender, and their pages lead back to the cards. A chip links only to a
# page the device may read, so a chip says nothing about what another
# person keeps.

def test_a_persons_chip_opens_their_memory_page(tools):
    """Given a card that names Lisa, when Marge's phone follows Lisa's
    chip, then Lisa's page in the memory opens."""
    chips = dict(opens(tools, f"/card/{SAXOPHONE}", person="marge").links("chip"))
    assert chips["Lisa"] == "/memory/person/lisa"

    person = opens(tools, chips["Lisa"], person="marge")

    assert person.texts("title") == ["Lisa Simpson"]


def test_a_chip_of_a_person_whose_page_the_device_may_not_read_has_no_link(tools):
    """Given a card Marge may read that names Grampa, whose person card is
    in Homer's own folder, when Marge's phone opens it, then his chip shows
    as the card writes him and without a link: his person card and his
    page are ones she may not read."""
    page = opens(tools, f"/card/{SAXOPHONE}", person="marge")

    assert "Grampa" in page.texts("chip")
    assert "Abe" not in page.text
    assert [href for _, href in page.links("chip") if "/person/" in href] == ["/memory/person/lisa"]


def test_the_person_whose_page_the_device_may_read_gets_the_link(tools):
    """Given the same card, when Homer's phone opens it, then Grampa's chip
    opens his page too: Homer reads his own folder."""
    chips = dict(opens(tools, f"/card/{SAXOPHONE}", person="homer").links("chip"))
    assert chips["Abe"] == "/memory/person/grampa"

    person = opens(tools, chips["Abe"], person="homer")

    assert person.texts("title") == ["Abe Simpson"]


def test_a_topic_tag_opens_the_topics_page_and_another_tag_the_search(tools):
    """Given a card tagged with its topic and with a word no topic has, then
    the topic's tag opens the topic's page and the other tag opens the
    memory's search with the tag filled in."""
    links = dict(opens(tools, f"/card/{SAXOPHONE}", person="marge").links("chip"))

    assert links["#Itchy Scratchy Land"] == "/memory/topic/topic-itchy-scratchy-land"
    assert links["#jazz"] == "/memory/search?q=jazz"


def test_a_person_named_by_an_alias_opens_their_page(tools):
    """Given Marge's person card, which names her "Mom" too, and a note
    that names "Mom", when Marge's phone opens the note, then the chip
    "Mom" opens Marge's page: the memory resolves the name as it links
    the note to her."""
    chips = dict(opens(tools, f"/card/{POT_ROAST}", person="marge").links("chip"))

    assert chips["Mom"] == "/memory/person/marge"


def test_an_ontology_tag_does_not_open_a_folder_topic_of_its_name(tools):
    """Given a note in the folder topic "medical" tagged with the ontology's
    topic `medical`, then the tag's chip opens the search: the note belongs
    to the folder's topic by its folder, and the ontology's tag names no
    folder, as the memory links it."""
    chips = dict(opens(tools, f"/card/{FLU_SHOT}", person="marge").links("chip"))

    assert chips["#Medical"] == "/memory/search?q=Medical"


def test_a_tags_chip_opens_the_search_with_the_tag_filled_in(tools):
    """Given the tag "jazz", when Marge follows its chip, then the memory's
    search opens with "jazz" in its field and finds the card."""
    browser = Browser(tools, listener=PUBLIC_PORT, host="192.168.1.20:42000", person="marge")
    chips = dict(browser.opens(f"/card/{SAXOPHONE}").links("chip"))

    search = browser.opens(chips["#jazz"])

    assert search.status == 200, search
    assert search.attribute("input", "value") == "jazz"
    assert f"/card/{SAXOPHONE}" in {href for _, href in search.links("results")}


def test_a_documents_sender_opens_the_senders_page(tools):
    """Given a document from Duff Insurance, when a phone follows the sender
    in its facts, then the sender's page in the memory opens."""
    page = opens(tools, f"/card/{POLICY}")
    assert page.links("facts") == [("Duff Insurance", "/memory/sender/correspondent-duff-insurance")]

    sender = opens(tools, page.links("facts")[0][1])

    assert sender.texts("title") == ["Duff Insurance"]


@pytest.mark.parametrize("card", [POLICY, SAXOPHONE, LUNCH])
def test_every_card_page_leads_back_to_the_memory(tools, card):
    """Given any card page, then its side has the person's own page, `/memory`."""
    page = opens(tools, f"/card/{card}", person="marge")

    assert "/memory" in [href for _, href in page.links("side")]
