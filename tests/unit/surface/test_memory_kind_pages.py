"""The memory's menu "Everything": every kind of card, the senders, and their pages.

Every memory page's menu (on a phone, behind the menu button) has the
group "Everything": one entry per kind of card the device may read, each
with its count, and "Senders" with how many there are:

    Documents 2   -> /memory/documents          (the documents keep their page)
    Notes 2       -> /memory/kind/note          that kind's cards, newest first
    Diary 1       -> /memory/kind/diary
    Emails 1      -> /memory/kind/email
    Bookmarks 1   -> /memory/kind/bookmark
    Senders 2     -> /memory/senders            every sender, with its documents

A kind the device has no card of is not in the menu, and its address is
the same "not found" page as one that does not exist. The counts and rows
are the memory's answer for the device's audience: nothing of another
person's own folder is in them.

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

TENT = "note-7e1a2b3c4d"              # names Marge
SAXOPHONE = "note-1b2c3d4e5f"         # names Lisa, newer
PIE = "bookmark-5c4d3e2f1a"
TRIP = "email-9e8d7c6b5a"
BIKE = "diary-4a1b2c3d4e"
RECEIPT = "document-4c5d6e7f80"       # from the Kwik-E-Mart
POLICY = "document-2a3b4c5d6e"        # from Springfield Insurance
# Homer's own folder: never on Marge's phone.
MOES = "diary-7b8c9d0e1f"
BET = "document-9f8e7d6c5b"           # from Fat Tony, whose card is Homer's too
SURPRISE = "list-surprise-party"      # the only list: Marge has no list at all

KWIK = "correspondent-kwik-e-mart"
INSURANCE = "correspondent-springfield-insurance"
BOOKIE = "correspondent-fat-tony"

CARDS = {
    **PEOPLE,
    "family/people/lisa.md": "---\ntype: person\ncard_id: person-lisa\ntitle: Lisa Simpson\n"
                             "account: lisa\n---\n\n# Lisa Simpson\n",
    "family/notes/2026/11/tent.md": (
        f"---\ntype: note\ncard_id: {TENT}\ntitle: Tent for four\ndate: \"2026-11-01\"\n"
        "persons:\n  - marge\n---\n\n# Tent for four\n"),
    "family/notes/2026/11/saxophone.md": (
        f"---\ntype: note\ncard_id: {SAXOPHONE}\ntitle: Saxophone lesson\ndate: \"2026-11-03\"\n"
        "persons:\n  - lisa\n---\n\n# Saxophone lesson\n"),
    "family/bookmarks/2026/11/pie.md": (
        f"---\ntype: bookmark\ncard_id: {PIE}\ntitle: Pie recipe\ndate: \"2026-11-02\"\n"
        "persons:\n  - marge\nresource: \"https://pie.example/recipe\"\n---\n\n# Pie recipe\n"),
    "family/emails/2026/11/school-trip.md": (
        f"---\ntype: email\ncard_id: {TRIP}\ntitle: School trip to the plant\ndate: \"2026-11-04\"\n"
        "persons:\n  - bart\nresource: \"mid:<trip-42@springfield-elementary.example>\"\n---\n\n"
        "# School trip to the plant\n"),
    "family/diary/entries/2026/11/bike.md": (
        f"---\ntype: diary\ncard_id: {BIKE}\ntitle: Bart's first bike ride\ndate: \"2026-11-05\"\n"
        "persons:\n  - bart\n---\n\nBart rode to the end of Evergreen Terrace.\n"),
    "family/correspondents/kwik-e-mart.md": (
        f"---\ntype: correspondent\ncard_id: {KWIK}\ntitle: Kwik-E-Mart\n---\n\n# Kwik-E-Mart\n"),
    "family/correspondents/springfield-insurance.md": (
        f"---\ntype: correspondent\ncard_id: {INSURANCE}\ntitle: Springfield Insurance\n---\n\n"
        "# Springfield Insurance\n"),
    "family/documents/2026/11/receipt.md": (
        f"---\ntype: document\ncard_id: {RECEIPT}\ntitle: Receipt from Kwik-E-Mart\n"
        "date: \"2026-11-02\"\ncorrespondent: Kwik-E-Mart\ndocument_type: Receipt\n"
        "persons:\n  - marge\n---\n\n# Receipt from Kwik-E-Mart\n"),
    "family/documents/2026/09/policy.md": (
        f"---\ntype: document\ncard_id: {POLICY}\ntitle: Car insurance\n"
        "date: \"2026-09-02\"\ncorrespondent: Springfield Insurance\ndocument_type: Policy\n"
        "persons:\n  - homer\n---\n\n# Car insurance\n"),
    "homer/diary/entries/2026/11/moes.md": (
        f"---\ntype: diary\ncard_id: {MOES}\ntitle: An evening at Moe's\ndate: \"2026-11-06\"\n"
        "persons:\n  - homer\n---\n\n# An evening at Moe's\n"),
    "homer/correspondents/fat-tony.md": (
        f"---\ntype: correspondent\ncard_id: {BOOKIE}\ntitle: Fat Tony\n---\n\n# Fat Tony\n"),
    "homer/documents/2026/11/bet.md": (
        f"---\ntype: document\ncard_id: {BET}\ntitle: A bet on the game\n"
        "date: \"2026-11-07\"\ncorrespondent: Fat Tony\ndocument_type: Receipt\n"
        "persons:\n  - homer\n---\n\n# A bet on the game\n"),
    "homer/lists/surprise-party.md": (
        f"---\ntype: list\ncard_id: {SURPRISE}\ntitle: Surprise party\n---\n\n- [ ] Cake\n"),
}

# What of Homer's own folder must never reach Marge's phone, by id and by title.
HOMERS_OWN = [MOES, "An evening at Moe", BET, "A bet on the game", BOOKIE, "Fat Tony", SURPRISE,
              "Surprise party", "/memory/kind/list"]


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("kind-pages"), CARDS) as running:
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


def opens(browser: Browser, path: str) -> Page:
    page = browser.opens(path)
    assert page.status == 200, page
    return page


def everything(page: Page) -> list[tuple[str, str]]:
    """(address, count) of each entry of the menu's "Everything", in order."""
    group = page.within("data-group", "everything")
    return list(zip([href for _, href in group.links()], group.texts("n")))


def cards_in(page: Page, section: str) -> list[str]:
    """The card ids a section's lines open, in page order."""
    part = page.within("data-section", section)
    return [href.removeprefix("/card/") for _, href in part.links() if href.startswith("/card/")]


# ── The menu ──────────────────────────────────────────────────────────────

def test_the_menu_lists_every_kind_marge_may_read_and_the_senders_with_counts(tools):
    """Given the family's cards and Homer's own diary, bet and list, when
    Marge's phone opens any memory page, then the menu's "Everything" has
    one entry per kind she has cards of, in a fixed order, each with its
    count, and the senders with theirs. The documents open their own page.

    No list is hers to read, so Lists is not there; Homer's diary entry
    and his bet are not in the counts.
    """
    marge = phone(tools, person="marge")

    for path in ("/memory", "/memory/family", "/memory/kind/note", "/memory/senders"):
        assert everything(opens(marge, path)) == [
            ("/memory/documents", "2"), ("/memory/kind/note", "2"), ("/memory/kind/diary", "1"),
            ("/memory/kind/email", "1"), ("/memory/kind/bookmark", "1"), ("/memory/senders", "2")], path


def test_homers_menu_counts_his_own_cards_too(tools):
    """Given Homer's own diary, bet and list, then his phone's menu counts
    them, and lists the kind only he has."""
    homer = opens(phone(tools, person="homer"), "/memory/family")

    assert everything(homer) == [
        ("/memory/documents", "3"), ("/memory/kind/note", "2"), ("/memory/kind/diary", "2"),
        ("/memory/kind/email", "1"), ("/memory/kind/bookmark", "1"), ("/memory/kind/list", "1"),
        ("/memory/senders", "3")]


def test_a_household_device_has_everything_in_its_menu(tools):
    """Given a device that is no person's, then its menu has no topics of
    its own but still lists every kind it may read."""
    entries = dict(everything(opens(phone(tools), "/memory/family")))

    assert entries["/memory/kind/note"] == "2"
    assert "/memory/kind/list" not in entries


def test_the_menu_marks_the_kind_shown(tools):
    page = opens(phone(tools, person="marge"), "/memory/kind/diary")

    assert page.links_marked("aria-current") == ["/memory/kind/diary"]


# ── A kind's page ─────────────────────────────────────────────────────────

def test_a_kinds_page_lists_its_cards_newest_first(tools):
    """Given two family notes, when Marge opens the notes, then the page
    names the kind, counts both, and lists them newest first, each a row
    to its card's page."""
    page = opens(phone(tools, person="marge"), "/memory/kind/note")

    assert page.attribute("h1", "tabindex") == "-1"
    assert page.texts("count-line") == ["2 cards"]
    assert cards_in(page, "cards") == [SAXOPHONE, TENT]
    assert "/memory/person/lisa" in {href for _, href in page.within("data-section", "links").links()}


def test_the_diary_shows_only_what_the_device_may_read(tools):
    """Given Bart's family diary entry and Homer's own, then Marge's diary
    page lists Bart's alone, and Homer's lists both."""
    assert cards_in(opens(phone(tools, person="marge"), "/memory/kind/diary"), "cards") == [BIKE]
    assert cards_in(opens(phone(tools, person="homer"), "/memory/kind/diary"), "cards") == [MOES, BIKE]


@pytest.mark.parametrize("path", ["/memory/kind/list", "/memory/kind/nonsense", "/memory/kind/person"])
def test_a_kind_the_device_has_no_card_of_is_not_found(tools, path):
    """Given lists only in Homer's own folder, when Marge's phone opens the
    lists, then it is the "not found" page, byte for byte the page of a
    kind that does not exist, so it tells nothing about Homer's folder.
    (A missing topic has a page of its own, "Not one of your topics", so
    the page to match is a kind's.)"""
    marge = phone(tools, person="marge")
    page, nothing = marge.opens(path), marge.opens("/memory/kind/nothing-of-that-kind")

    assert page.status == 404
    assert page.body == nothing.body


# ── The senders ───────────────────────────────────────────────────────────

def test_the_senders_page_lists_each_sender_with_its_documents(tools):
    """Given a receipt from the Kwik-E-Mart and a policy from Springfield
    Insurance, then the senders page lists both, each with the number of
    its documents and a link to its sender page; Fat Tony sent only
    Homer's own bet, so he is on Homer's page alone."""
    marge = opens(phone(tools, person="marge"), "/memory/senders")
    homer = opens(phone(tools, person="homer"), "/memory/senders")

    rows = marge.within("data-section", "senders")
    assert [href for _, href in rows.links()] == [f"/memory/sender/{KWIK}", f"/memory/sender/{INSURANCE}"]
    assert rows.texts("fl-date") == ["1 document", "1 document"]
    assert marge.texts("count-line") == ["2 senders"]
    assert f"/memory/sender/{BOOKIE}" in {href for _, href in homer.within("data-section", "senders").links()}


# ── Who may see what ──────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/memory", "/memory/family", "/memory/kind/diary", "/memory/kind/note",
                                  "/memory/senders", "/memory/documents"])
def test_nothing_of_homers_own_folder_reaches_marges_phone(tools, path):
    """Given Homer's own diary entry, bet, sender and list, then no entry,
    count or row of Marge's pages comes from them."""
    page = opens(phone(tools, person="marge"), path)

    assert [leak for leak in HOMERS_OWN if leak in page.body] == []


# ── German, the phone ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/memory/kind/note", "/memory/senders"])
def test_in_german_the_menu_and_the_pages_are_german(tools, path):
    english = opens(phone(tools, person="marge"), path)
    german = opens(phone(tools, person="marge", language="de"), path)

    assert german.attribute("html", "lang") == "de"
    menu = "data-group", "everything"
    assert not set(english.within(*menu).texts("fn-sub")) & set(german.within(*menu).texts("fn-sub"))
    assert english.texts("title") != german.texts("title")


def test_the_phones_menu_button_opens_the_menu_with_everything(tools):
    """Given a phone, then the menu the menu button controls holds the
    group "Everything", so it folds away with the rest of the menu."""
    page = opens(phone(tools, person="marge"), "/memory/kind/note")

    [menu] = page.attributes("menu-button", "aria-controls")
    assert page.within("id", menu).within("data-group", "everything").links()


# ── A long list, part by part ─────────────────────────────────────────────

# 45 notes in the garden topic and 45 receipts from the Kwik-E-Mart, one
# a day, all naming Marge: more than a page lists at once (40), so each of
# these pages has two parts, and Marge's page three.
MANY = 45
LONG_PAGES = ["/memory/kind/note", "/memory/topic/topic-garden", "/memory/documents",
              "/memory/documents/Receipt", f"/memory/sender/{KWIK}"]


def _day(n: int) -> str:
    return f"2026-0{7 + n // 30}-{1 + n % 30:02d}"


LONG_LISTS = {
    **PEOPLE,
    "family/correspondents/kwik-e-mart.md": CARDS["family/correspondents/kwik-e-mart.md"],
    "family/garden/topic.md": "---\ntype: topic\ncard_id: topic-garden\ntitle: Garden\n---\n\n# Garden\n",
    **{f"family/garden/notes/{n:02d}.md": (f"---\ntype: note\ncard_id: note-{n:02d}\ntitle: Note {n}\n"
                                    f"date: \"{_day(n)}\"\npersons:\n  - marge\n---\n\n# Note {n}\n")
       for n in range(MANY)},
    **{f"family/documents/{n:02d}.md": (f"---\ntype: document\ncard_id: document-{n:02d}\ntitle: Receipt {n}\n"
                                        f"date: \"{_day(n)}\"\ncorrespondent: Kwik-E-Mart\n"
                                        "document_type: Receipt\npersons:\n  - marge\n---\n\n"
                                        f"# Receipt {n}\n")
       for n in range(MANY)},
}


@pytest.fixture(scope="module")
def long_memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("long-lists"), LONG_LISTS) as running:
        yield running


@pytest.fixture
def long_tools(long_memory, host, monkeypatch):
    """Core's tools server, reaching the memory with the long lists."""
    monkeypatch.setenv("MEMORY_CARDS_URL", long_memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return tools_server("server").app


def listed_cards(page: Page) -> list[int]:
    """The numbers of the notes and receipts a page lists, in page order,
    each once: a topic's entry links its card twice (its title and its foot)."""
    numbers = [int(href[-2:]) for _, href in page.links() if href.startswith(("/card/note-", "/card/document-"))
               if href[-2:].isdigit()]
    return list(dict.fromkeys(numbers))


@pytest.mark.parametrize("path", LONG_PAGES)
def test_a_long_list_offers_the_older_cards_on_a_page_of_their_own(long_tools, path):
    """Given 45 notes in a topic and 45 receipts from one sender, when
    Marge opens the notes, the topic, the documents, the receipts or the
    sender, then the page lists
    the newest 40 and offers "Older" at its own address; that page lists
    the 5 oldest, offers "Newer" back to the first and no "Older", so
    Back and a bookmark each open one part."""
    marge = phone(long_tools, person="marge")
    first = opens(marge, path)
    [(_, older)] = first.links("pager-older")
    second = opens(marge, older)

    newest = sorted(range(MANY), reverse=True)
    assert listed_cards(first)[-40:] == newest[:40] and len(set(listed_cards(first))) == 40
    assert older == f"{path}?part=2"
    assert first.links("pager-newer") == []
    assert listed_cards(second)[-5:] == newest[40:]
    assert [href for _, href in second.links("pager-newer")] == [path]
    assert second.links("pager-older") == []
    assert first.links_marked("aria-current") == second.links_marked("aria-current")


def test_a_persons_page_offers_her_older_cards_part_by_part(long_tools):
    """Given 90 cards naming Marge, when Homer opens her page, then its
    cards come in three parts of 40, 40 and 10, linked by "Older" and
    "Newer", and every part keeps her documents above the cards."""
    homer = phone(long_tools, person="homer")
    first = opens(homer, "/memory/person/marge")
    [(_, second_at)] = first.links("pager-older")
    second = opens(homer, second_at)
    [(_, third_at)] = second.links("pager-older")
    third = opens(homer, third_at)

    assert [len(cards_in(p, "cards")) for p in (first, second, third)] == [40, 40, 10]
    assert len({c for p in (first, second, third) for c in cards_in(p, "cards")}) == 2 * MANY
    assert (second_at, third_at) == ("/memory/person/marge?part=2", "/memory/person/marge?part=3")
    assert [href for _, href in third.links("pager-newer")] == [second_at]
    assert third.links("pager-older") == []
    documents = [p.within("data-section", "documents").links() for p in (first, third)]
    assert documents[0] == documents[1] != []


def test_what_changed_shows_its_first_lines_and_no_line_that_leads_nowhere(long_tools):
    """Given 90 cards about Marge changed today, when Homer opens her page,
    then "What changed" counts all 90 in its heading and lists the first
    lines, each with its one meta line, and nothing below them: a line
    saying more are left would lead nowhere."""
    page = opens(phone(long_tools, person="homer"), "/memory/person/marge")
    changed = page.within("data-section", "changed")

    assert changed.texts("fl-count") == [str(2 * MANY)]
    assert 0 < len(cards_in(page, "changed")) < 2 * MANY
    assert len(changed.texts("fl-meta")) == len(cards_in(page, "changed"))


def test_a_short_list_offers_no_other_part(tools):
    """Given two notes, then their page offers neither "Older" nor "Newer"."""
    page = opens(phone(tools, person="marge"), "/memory/kind/note")

    assert page.links("pager") == []


@pytest.mark.parametrize("query", ["part=3", "part=0", "part=two", "part=-1", "part=²", "part=٣",
                                   pytest.param("part=" + "9" * 5000, id="part=5000-digits")])
def test_a_part_past_the_list_is_not_found(long_tools, query):
    """Given two parts of notes, then a part that is not one of them is
    the "not found" page: also a superscript or Arabic-Indic digit, which
    is no part (and no second address of part 3), and a number too long
    to be one, never a server error."""
    assert phone(long_tools, person="marge").opens(f"/memory/kind/note?{query}").status == 404


@pytest.mark.parametrize("path", ["/memory/kind/note?part=2", "/memory/documents?part=2"])
def test_in_german_the_paging_is_german(long_tools, path):
    """Given a German browser, then "Newer" and "Older" are German words."""
    english = opens(phone(long_tools, person="marge"), path)
    german = opens(phone(long_tools, person="marge", language="de"), path)

    assert english.links("pager") and german.links("pager")
    assert not {t for t, _ in english.links("pager")} & {t for t, _ in german.links("pager")}


@pytest.mark.parametrize("path", ["/memory/kind/document", "/memory/kind/document?part=2"])
def test_the_kind_document_is_the_documents_page(long_tools, path):
    """Given the address of the kind "document", then it sends the browser
    to the documents page, which shows the documents by kind; a part goes
    to the same part there."""
    page = phone(long_tools, person="marge").opens(path)

    assert page.status in (301, 302, 307, 308)
    assert page.location == path.replace("/memory/kind/document", "/memory/documents")
