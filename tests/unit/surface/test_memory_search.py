"""The memory's search, and the preview of a card link.

Every memory page has one search in its header. Its words go to
`/memory/search?q=`, which asks the memory's `find` for the device's
audience and shows what it found grouped by type: the people, topics and
senders first, each linking its memory page, then the cards, each linking
its card page. The search finds a card by its title, its summary, its
sender, its kind of document, the site of a bookmark, and a topic or a
person by its name, because `find` reads the whole card. A card in another
person's own folder is never among the results: the memory leaves it out
for the device's audience, and the page adds nothing.

A card link on a memory page shows a short preview when the pointer rests
on it or it gets the focus. The page's script reads it from
`/memory/preview/<card id>`: the card viewer's description of the card,
read for the same audience, so a preview never shows a card the device
may not open.

Driven through the tools server's FastAPI app (the Browser DSL), read the
way a browser without JavaScript reads the page. The memory is memory's
test instance: a real curator over a bare repository, reached over HTTP on
localhost. The host API is the dashboard's stand-in on a loopback port.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import PEOPLE

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT
FOUND_LIMIT = tools_server("memory_ui.reads").FOUND_LIMIT

TENT = "note-7e1a2b3c4d"
PIE = "bookmark-2b3c4d5e6f"
RECEIPT = "document-4c5d6e7f80"
INVOICE = "document-5d6e7f8091"
DENTIST = "note-6e7f8091a2"
SURPRISE = "note-7f8091a2b3"          # in Homer's own folder, names Marge
CAMPING = "topic-camping"
TEETH = "topic-teeth"
KWIK_E_MART = "correspondent-kwik-e-mart"
WOLFE = "correspondent-wolfe"

CARDS = {
    **PEOPLE,
    "family/camping/topic.md": f"---\ntype: topic\ncard_id: {CAMPING}\ntitle: Camping\n---\n\n# Camping\n",
    "family/teeth/topic.md": (
        f"---\ntype: topic\ncard_id: {TEETH}\ntitle: Teeth and the dentist\n---\n\n# Teeth and the dentist\n"),
    "family/camping/notes/2026/11/tent.md": (
        f"---\ntype: note\ncard_id: {TENT}\ntitle: Tent for four\ndate: \"2026-11-01\"\n"
        "persons:\n  - marge\n---\n\n# Tent for four\n\nPitch 14, by the lake. Bring the big tent.\n"),
    "family/bookmarks/2026/11/pie.md": (
        f"---\ntype: bookmark\ncard_id: {PIE}\ntitle: Pie recipe\ndate: \"2026-11-03\"\n"
        "persons:\n  - marge\nresource: \"https://piecrust.example/recipe\"\n---\n\n# Pie recipe\n"),
    "family/correspondents/kwik-e-mart.md": (
        f"---\ntype: correspondent\ncard_id: {KWIK_E_MART}\ntitle: Kwik-E-Mart\n---\n\n# Kwik-E-Mart\n"),
    "family/correspondents/wolfe.md": (
        f"---\ntype: correspondent\ncard_id: {WOLFE}\ntitle: Dr. Wolfe Dentistry\n---\n\n"
        "# Dr. Wolfe Dentistry\n"),
    "family/documents/2026/11/receipt.md": (
        f"---\ntype: document\ncard_id: {RECEIPT}\ntitle: Groceries for the week\n"
        "date: \"2026-11-02\"\ncorrespondent: Kwik-E-Mart\ndocument_type: Receipt\n"
        "persons:\n  - marge\n---\n\n# Groceries for the week\n"),
    "family/documents/2026/11/invoice.md": (
        f"---\ntype: document\ncard_id: {INVOICE}\ntitle: Invoice for a cleaning\n"
        "date: \"2026-11-04\"\ncorrespondent: Dr. Wolfe Dentistry\ndocument_type: Invoice\n"
        "persons:\n  - lisa\n---\n\n# Invoice for a cleaning\n\nLisa's cleaning at the dentist, 80 dollars.\n"),
    "family/notes/2026/11/dentist.md": (
        f"---\ntype: note\ncard_id: {DENTIST}\ntitle: Dentist with Lisa\ndate: \"2026-11-05\"\n"
        "persons:\n  - marge\n---\n\n# Dentist with Lisa\n"),
    "homer/notes/2026/11/surprise.md": (
        f"---\ntype: note\ncard_id: {SURPRISE}\ntitle: A present for Marge from the dentist\n"
        "date: \"2026-11-05\"\npersons:\n  - marge\n---\n\n# A present for Marge from the dentist\n"),
}


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("memory-search"), CARDS) as running:
        yield running


@pytest.fixture
def tools(memory, tmp_path, monkeypatch):
    """Core's tools server, reaching the memory's curator, with the host
    API's stand-in answering for the Simpsons."""
    host = DashboardHost.in_state("services", root=tmp_path / "instance")
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(host.serve()))
    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    yield tools_server("server").app
    host.close()


def phone(app, *, person: str = "marge", language: str = "en") -> Browser:
    """A phone on the home network, paired as `person`."""
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language,
                   person=person)


def searches(browser: Browser, words: str) -> Page:
    page = browser.opens(f"/memory/search?q={quote(words)}")
    assert page.status == 200, page
    return page


def groups(page: Page) -> list[str]:
    """The result groups of `page`, in page order, by card type."""
    return page.attributes("results", "data-group")


def hrefs(page: Page) -> set[str]:
    return {href for _, href in page.links("results")}


# ── What the search finds ─────────────────────────────────────────────────

def test_a_word_finds_cards_senders_and_topics_grouped_by_type(tools):
    """Given the word "dentist", when Marge searches, then the results show
    the topic, the sender and the cards it matches, each type in its own
    group, the topic and the sender first, and each result is a link to its
    page."""
    page = searches(phone(tools), "dentist")

    assert groups(page)[:2] == ["topic", "correspondent"]
    assert set(groups(page)) == {"topic", "correspondent", "document", "note"}
    assert hrefs(page) >= {f"/memory/topic/{TEETH}", f"/memory/sender/{WOLFE}",
                           f"/card/{INVOICE}", f"/card/{DENTIST}"}


@pytest.mark.parametrize("words, found", [
    ("tent", f"/card/{TENT}"),                         # a title
    ("lake", f"/card/{TENT}"),                         # a summary
    ("Kwik-E-Mart", f"/card/{RECEIPT}"),               # a sender
    ("receipt", f"/card/{RECEIPT}"),                   # a kind of document
    ("piecrust", f"/card/{PIE}"),                      # a bookmarked site
    ("camping", f"/memory/topic/{CAMPING}"),           # a topic
    ("bart", "/memory/person/bart"),                   # a person's name
], ids=["title", "summary", "sender", "kind", "site", "topic", "person"])
def test_the_search_finds_each_field(tools, words, found):
    page = searches(phone(tools), words)

    assert found in hrefs(page)


@pytest.mark.parametrize("words, page", [
    ("bart", "/memory/person/bart"),
    ("camping", f"/memory/topic/{CAMPING}"),
    ("dentistry", f"/memory/sender/{WOLFE}"),
], ids=["person", "topic", "sender"])
def test_a_person_topic_or_sender_result_opens_its_page(tools, words, page):
    """Given a result that is a person, a topic or a sender, when Marge
    follows its link, then the memory's page for it opens."""
    browser = phone(tools)
    assert page in hrefs(searches(browser, words))

    assert browser.opens(page).status == 200


def test_a_card_in_another_persons_own_folder_is_never_a_result(tools):
    """Given a note in Homer's own folder that names Marge and the dentist,
    when Marge's phone searches "dentist", then the note is nowhere on the
    page: the memory answers the search for her device's audience."""
    page = searches(phone(tools), "dentist")

    assert SURPRISE not in page.body
    assert "A present for Marge" not in page.text


def test_the_owner_finds_their_own_card(tools):
    """Given the same note, when Homer's phone searches, then he finds it:
    the search leaves out what the device may not read, not more."""
    page = searches(phone(tools, person="homer"), "present")

    assert f"/card/{SURPRISE}" in hrefs(page)


def test_no_match_says_so_and_keeps_the_words(tools):
    """Given words nothing matches, then the page says so and the search
    field still holds the words, so a person can change them."""
    page = searches(phone(tools), "zeppelin")

    assert groups(page) == []
    assert len(page.texts("empty-text")) == 1
    assert page.attribute("input", "value") == "zeppelin"


def test_words_too_short_to_search_match_nothing(tools):
    """Given only words of one or two letters, which the memory's `find`
    does not search for, then the page says nothing matches: it does not
    show the newest cards as if they had."""
    page = searches(phone(tools), "tv")

    assert groups(page) == []
    assert len(page.texts("empty-text")) == 1


def test_a_capped_answer_says_that_more_exist(tmp_path, monkeypatch, tools):
    """Given more cards that match than the memory's answer holds, when
    Marge searches, then the count line names both numbers, the shown and
    the found, so a long list does not read as complete."""
    many = {f"family/notes/2026/10/duff-{n}.md": (
        f"---\ntype: note\ncard_id: note-d{n:09d}\ntitle: Duff number {n}\n---\n\n# Duff number {n}\n")
        for n in range(FOUND_LIMIT + 2)}
    with running_memory(tmp_path / "many", {**PEOPLE, **many}) as memory:
        monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
        page = searches(phone(tools_server("server").app), "duff")

    assert len(hrefs(page)) == FOUND_LIMIT
    count = page.texts("count-line")[0]
    assert str(FOUND_LIMIT) in count and str(FOUND_LIMIT + 2) in count


def test_the_found_words_stay_in_the_field(tools):
    page = searches(phone(tools), "dentist")

    assert page.attribute("input", "value") == "dentist"


def test_the_search_without_words_asks_for_them(tools):
    page = searches(phone(tools), "")

    assert groups(page) == []
    assert len(page.texts("empty-text")) == 1


def test_in_german_the_groups_are_named_in_german(tools):
    english = searches(phone(tools), "dentist")
    german = searches(phone(tools, language="de"), "dentist")

    assert german.attribute("html", "lang") == "de"
    assert not set(english.texts("section-title")) & set(german.texts("section-title"))


# ── The preview of a card link ────────────────────────────────────────────

def test_a_card_links_preview_is_the_card_viewers_description(tools):
    """Given the invoice, whose text starts with its summary, when a memory
    page's script asks its preview, then it gets the card's title, type and
    the description the card viewer gives the card."""
    status, preview = phone(tools).reads(f"/memory/preview/{INVOICE}")

    assert status == 200
    assert preview["id"] == INVOICE
    assert preview["type"] == "document"
    assert preview["title"] == "Invoice for a cleaning"
    assert preview["description"].startswith("Lisa's cleaning at the dentist")


def test_the_preview_of_another_persons_own_card_is_not_found(tools):
    """Given a note in Homer's own folder, when Marge's phone asks its
    preview, then the answer is 404, the same as for a card that does not
    exist: the preview reads with her device's audience."""
    status, _ = phone(tools).reads(f"/memory/preview/{SURPRISE}")
    missing, _ = phone(tools).reads("/memory/preview/note-0000000000")

    assert status == missing == 404


def test_every_memory_page_loads_the_preview(tools):
    """Given any memory page, then it loads the preview's script, which
    shows a card link's preview on hover and on focus."""
    for path in ("/memory", "/memory/family", "/memory/search?q=tent"):
        page = phone(tools).opens(path)
        assert "/memory/static/preview.js" in page.scripts(), path
    assert phone(tools).opens("/memory/static/preview.js").status == 200


# ── The search as a layer over the page ──────────────────────────────────

def test_the_layer_reads_the_results_without_the_pages_frame(tools):
    """Given a memory page with its script, when Marge types "dentist" in
    the header's search, then the script reads the results from
    `/memory/search/results?q=`: the same groups and links as the search
    page, without the header, the menu or a second page heading, so they
    fit over the page she is on."""
    browser = phone(tools)
    page = browser.opens(f"/memory/search/results?q={quote('dentist')}")
    full = searches(browser, "dentist")

    assert page.status == 200
    assert groups(page) == groups(full)
    assert hrefs(page) == hrefs(full)
    assert "<html" not in page.body and "mhead" not in page.body
    assert 'id="page-title"' not in page.body


def test_the_layer_leaves_out_another_persons_own_card(tools):
    """The layer reads with the device's audience, as the search page."""
    page = phone(tools).opens(f"/memory/search/results?q={quote('dentist')}")

    assert SURPRISE not in page.body


def test_the_layer_without_words_asks_for_them(tools):
    page = phone(tools).opens("/memory/search/results?q=")

    assert groups(page) == []
    assert len(page.texts("empty-text")) == 1


def test_every_memory_page_loads_the_layer_and_a_named_clear_button(tools):
    """Given any memory page, then it loads the layer's script, and the
    search holds a clear button with an accessible name, hidden while the
    field is empty."""
    for path in ("/memory", "/memory/family", "/memory/search?q=tent"):
        page = phone(tools).opens(path)
        assert "/memory/static/search_layer.js" in page.scripts(), path
        assert page.attributes("search-clear", "aria-label") == ["Clear the search"], path
    assert "hidden" in phone(tools).opens("/memory").body.split('class="search-clear"')[1].split(">")[0]
    assert phone(tools).opens("/memory/static/search_layer.js").status == 200
