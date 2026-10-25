"""The dashboard's activity widget, as a browser on the network sees it.

The dashboard is a page of core's tools server at `/dashboard`, on its
public listener (published as port 42000). Its first widget tells what
changed in the memory, in a sentence each, newest first.

Driven through the real thing: the tools server's FastAPI app with its
real access rule (the listener a request arrives on decides what it may
see), reading a real cards API (`serve(CardsAPI(...))`) over HTTP, over a
real git repository. Nothing is patched; `MEMORY_CARDS_URL` points core
at the cards API as it does in a test of the container.

The page is read without running its scripts: what a browser without
JavaScript shows must already be complete, with links that open the
cards. The widget's script only enhances it (relative times, refresh,
"Show more"); how the widget looks at each size is CSS and is checked on
screenshots, not here.

The data is the Simpsons'.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

from tests.memory.unit.dsl import Memory

from .conftest import LIB, STACKLETS
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import PUBLIC_PORT  # noqa: E402
from stacklets.memory.cards_api import CardsAPI, serve  # noqa: E402

NOW = datetime.now().astimezone().replace(microsecond=0)
MIDNIGHT = NOW.replace(hour=0, minute=0, second=0)
FOUR_DAYS_AGO = NOW - timedelta(days=4)


def today(share: float) -> datetime:
    """A moment earlier today, `share` of the way from midnight to now, so
    it is today whenever the suite runs."""
    return MIDNIGHT + (NOW - MIDNIGHT) * share


def note(card_id: str, title: str) -> str:
    return f"---\ntype: note\ncard_id: {card_id}\ntitle: {title}\n---\n\n# {title}\n"


TENT = note("note-7f3a9c21e4", "Tent is torn")
SIDECAR = '{"card_id": "note-7f3a9c21e4", "sources": []}'
CAMPING = "---\ntype: list\ncard_id: list-camping\ntitle: Camping\n---\n\n- [ ] Tent\n"
WATERCOLOUR = note("note-0b1c2d3e4f", "Bart's watercolour")
PERSON = "---\ntype: person\ntitle: {}\n---\n"


@pytest.fixture(scope="module")
def springfield(tmp_path_factory) -> Memory:
    """Five days of the Simpsons' memory: Homer's list and filing, a
    migration this morning, Marge's reply and her own note since."""
    m = Memory.new(tmp_path_factory.mktemp("springfield") / "vault")
    m.changes("chore(lists): homer added Tent to Camping", by="homer", at=FOUR_DAYS_AGO,
              files={"family/lists/camping.md": CAMPING})
    m.changes("capture: Tent is torn", by="homer", at=NOW - timedelta(days=1),
              files={"family/camping/tent.md": TENT, "family/camping/tent.json": SIDECAR})
    m.changes("cards: give 3 card(s) their kind", by="memory-bot", at=today(0.2),
              files={"family/lists/camping.md": CAMPING + "\n",
                     "family/camping/tent.md": TENT + "\n",
                     "family/people/lisa.md": PERSON.format("Lisa Simpson")},
              trailers=("Migration: memory/0006",))
    m.changes("reply: Tent is torn", by="marge", at=today(0.5),
              files={"family/camping/tent.md": TENT + "\n> Use the old tarp.\n",
                     "family/camping/tent.json": SIDECAR + "\n"})
    m.changes("capture: Bart's watercolour", by="marge", at=today(0.8),
              files={"marge/notes/watercolour.md": WATERCOLOUR})
    return m


def cards_api(memory: Memory, tmp_path_factory) -> str:
    fonts = tmp_path_factory.mktemp("fonts")
    (fonts / "inter-latin-wght-normal.woff2").write_bytes(b"wOF2 not really")
    server = serve(CardsAPI(vault=memory.repo, fonts=fonts), host="127.0.0.1", port=0)
    return f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture(scope="module")
def tools_server():
    import server

    return server.app


@pytest.fixture
def reading(monkeypatch, tools_server):
    """Point core at a cards API, as `MEMORY_CARDS_URL` does in the container."""
    def at(url: str):
        monkeypatch.setenv("MEMORY_CARDS_URL", url)
        return tools_server
    return at


@pytest.fixture(scope="module")
def springfield_api(springfield, tmp_path_factory) -> str:
    return cards_api(springfield, tmp_path_factory)


@pytest.fixture
def phone(reading, springfield_api) -> Browser:
    """A phone on the home network, opening the Mac's published port."""
    return Browser(reading(springfield_api), listener=PUBLIC_PORT, host="192.168.1.20:42000")


@pytest.fixture
def this_mac(reading, springfield_api) -> Browser:
    """A browser on the Mac the stack runs on."""
    return Browser(reading(springfield_api), listener=PUBLIC_PORT, host="localhost:42000")


# ── The page, without JavaScript ─────────────────────────────────────────

def test_the_activity_is_on_the_page_in_a_sentence_each_newest_first(phone):
    """Given five days of the memory, when a phone opens /dashboard, then
    the page already holds each change as a sentence a person would say,
    newest first, without commit subjects, hashes or file paths.

    The page must read complete before any script runs: on an old tablet,
    and for the moment before the widget's script loads.
    """
    page = phone.opens("/dashboard")

    assert page.status == 200
    assert page.texts("sentence") == [
        "Marge filed a note: Bart's watercolour",
        "Marge replied to a note: Tent is torn",
        "The memory was updated (3 cards)",
        "Homer filed a note: Tent is torn",
        "Homer edited a list: Camping",
    ]
    for technical in ("capture:", "reply:", "Migration", "memory-bot", "tent.md", ".json"):
        assert technical not in page.text


def test_the_changes_are_grouped_by_day(phone):
    """Given changes today, yesterday and four days ago, then they sit under
    "Today", "Yesterday" and the date spelled out."""
    page = phone.opens("/dashboard")

    day = FOUR_DAYS_AGO
    assert page.texts("day-label") == ["Today", "Yesterday", f"{day:%A}, {day.day} {day:%B}"]


def test_on_the_network_a_card_opens_through_its_persistent_link(phone):
    """Given the dashboard opened from a phone, then each card links to its
    `/go/<card_id>`: it opens the card wherever it lives, from any device
    in the house. The card browser is published on the Mac alone."""
    links = dict(phone.opens("/dashboard").links("sentence"))

    assert links["Tent is torn"] == "/go/note-7f3a9c21e4"
    assert links["Camping"] == "/go/list-camping"


def test_on_the_mac_a_card_opens_in_the_card_browser(this_mac):
    """Given the dashboard opened on the Mac itself, then a card opens in
    the card browser, which reads the memory as it is this second; the
    wiki behind `/go` is compiled a few minutes later, so the newest
    change could not be opened there yet."""
    links = dict(this_mac.opens("/dashboard").links("sentence"))

    assert links["Tent is torn"] == "http://localhost:42002/cards/#card=note-7f3a9c21e4"


def test_show_more_without_javascript_is_a_link_to_more(phone):
    """Given the page asked for two changes, then it shows two and a "Show
    more" link; following it shows the rest and no further link."""
    first = phone.opens("/dashboard?limit=2")
    [(label, more)] = first.links("fs-more")

    assert (len(first.texts("sentence")), label) == (2, "Show more")
    following = phone.opens(more)
    assert len(following.texts("sentence")) == 5
    assert following.links("fs-more") == []


def test_viewed_as_someone_the_page_leaves_out_what_they_may_not_read(phone):
    """Given Marge's own note, when the page is opened as Bart, then her
    filing is not on it."""
    page = phone.opens("/dashboard?audience=bart")

    assert "Bart's watercolour" not in page.text
    assert len(page.texts("sentence")) == 4


# ── Machine work and runs of the same thing ──────────────────────────────

FAMILY = ("Homer", "Bart", "Abe", "Patty", "Lisa", "Maggie")


def person(name: str) -> str:
    return f"---\ntype: person\ncard_id: person-{name.lower()}\ntitle: {name} Simpson\n---\n"


@pytest.fixture(scope="module")
def busy_morning(tmp_path_factory) -> Memory:
    """This morning: the admin has Homer's tent note refreshed, Marge goes
    through six people's cards one after another, then has the note
    refreshed again herself."""
    m = Memory.new(tmp_path_factory.mktemp("busy") / "vault")
    m.changes("capture: Tent is torn", by="homer", at=today(0.1),
              files={"family/camping/tent.md": TENT})
    m.changes("reprocess: Tent is torn", by="memory-bot", at=today(0.2),
              files={"family/camping/tent.md": TENT + "\nA new tarp.\n"},
              trailers=("Replaces: 1a2b3c4", "Requested-By: admin"))
    for i, name in enumerate(FAMILY):
        m.changes(f"chore(memory): marge changed {name}", by="marge", at=today(0.3 + i * 0.05),
                  files={f"family/people/{name.lower()}.md": person(name)})
    m.changes("reprocess: Tent is torn", by="memory-bot", at=today(0.9),
              files={"family/camping/tent.md": TENT + "\nA new tarp, and pegs.\n"},
              trailers=("Replaces: 5d6e7f8", "Requested-By: marge"))
    return m


@pytest.fixture
def phone_this_morning(reading, busy_morning, tmp_path_factory) -> Browser:
    return Browser(reading(cards_api(busy_morning, tmp_path_factory)), listener=PUBLIC_PORT,
                   host="192.168.1.20:42000")


def test_a_refresh_the_memory_made_reads_as_what_happened_to_the_card(phone_this_morning):
    """Given the memory bot refiling Homer's note, once for the admin and
    once at Marge's request, then each line says the note was refreshed,
    names Marge when she asked, and names no bot and no admin.

    A bot is not someone the family knows; "Memory refiled a note for
    Admin" says how it happened, not what happened to their note.
    """
    sentences = phone_this_morning.opens("/dashboard").texts("sentence")

    assert sentences[0] == "Note refreshed at Marge's request: Tent is torn"
    assert sentences[2] == "Note refreshed: Tent is torn"
    for word in ("Memory", "Admin", "admin", "refiled"):
        assert all(word not in s for s in sentences)


def test_a_run_of_the_same_change_is_one_line(phone_this_morning):
    """Given Marge editing six people's cards one after another, then the
    page has one line for them, naming the first two and how many more,
    each name a link to its card; the day still counts six changes.

    Six lines saying the same thing push everything else off a glance.
    """
    page = phone_this_morning.opens("/dashboard")

    assert page.texts("sentence")[1] == "Marge edited 6 people: Maggie, Lisa and 4 more"
    assert len(page.texts("sentence")) == 4
    assert page.links("sentence")[1:3] == [("Maggie", "/go/person-maggie"),
                                           ("Lisa", "/go/person-lisa")]
    assert page.texts("glance") == ["9 changes today"]


# ── A rebuild the stack did, and a person's change a bot wrote ───────────

def diary_entries(sender: str, first: int, n: int) -> dict[str, str]:
    """`n` of `sender`'s diary entries as the compile writes them, in the
    shared bucket."""
    return {f"family/diary/entries/2026/03/{sender}-{i}.md":
            f"---\ntype: diary\ncard_id: diary-{i:010x}\ntitle: {sender.capitalize()}'s memo {i}\n---\n"
            for i in range(first, first + n)}


MARGES_MEMOS = diary_entries("marge", 100, 30)
HOMERS_MEMOS = diary_entries("homer", 200, 12)


@pytest.fixture(scope="module")
def diary_night(tmp_path_factory) -> Memory:
    """Five days of the Simpsons' memory since bots commit as themselves:
    Homer's memos updated before that, as his own commit; Marge's note
    filed by the archivist; two memos of Marge's filed by the diary
    compile; then the compile writing every entry again, one commit per
    sender, as the memory bot."""
    m = Memory.new(tmp_path_factory.mktemp("diary") / "vault")
    m.changes("update: 3 diary entries", by="homer", at=FOUR_DAYS_AGO,
              files=diary_entries("homer", 200, 3))
    m.changes("capture: Tent is torn", by="marge", committed_by="archivist-bot",
              at=NOW - timedelta(days=1), files={"family/camping/tent.md": TENT})
    m.changes("learn: 2 diary entries", by="marge", committed_by="memory-bot", at=today(0.3),
              files=diary_entries("marge", 100, 2))
    m.changes("update: 30 diary entries", by="marge", committed_by="memory-bot", at=today(0.6),
              files={path: text + "\nRead again.\n" for path, text in MARGES_MEMOS.items()})
    m.changes("update: 12 diary entries", by="homer", committed_by="memory-bot", at=today(0.6),
              files={path: text + "\nRead again.\n" for path, text in HOMERS_MEMOS.items()})
    return m


@pytest.fixture(scope="module")
def diary_night_api(diary_night, tmp_path_factory) -> str:
    return cards_api(diary_night, tmp_path_factory)


def test_a_rebuild_the_stack_did_reads_as_what_happened_to_the_diary(reading, diary_night_api):
    """Given the diary compile writing Marge's and Homer's entries again,
    then the page says the diary was updated and how many entries, once
    for the whole compile, with the memory's icon and no one as the one
    who did it; what a bot wrote for a person still names them: Marge
    filed her note and her two memos. Homer's update from before bots
    committed as themselves reads as it always did.

    The compile writes each entry with its sender as author, because the
    entries are theirs. "Marge updated 30 diary entries" would say she did
    something she did not; the committer, the memory bot, says the stack
    did it (ADR-015).
    """
    page = Browser(reading(diary_night_api), listener=PUBLIC_PORT,
                   host="192.168.1.20:42000").opens("/dashboard")

    assert page.texts("sentence") == [
        "The diary was updated (42 entries)",
        "Marge filed 2 diary entries: Marge's memo 100 and Marge's memo 101",
        "Marge filed a note: Tent is torn",
        "Homer updated 3 diary entries: Homer's memo 200, Homer's memo 201 and Homer's memo 202",
    ]
    assert page.texts("name") == ["Marge", "Marge", "Homer"]
    assert page.texts("glance") == ["3 changes today"]


def test_list_items_drawn_from_a_note_read_as_what_happened_to_the_list(reading, tmp_path_factory):
    """Given Marge's note about the tent, when the archivist puts its
    action items on the camping list in her name, then the page says the
    memory was updated, and names Marge only for her note.

    The model read her note; "Marge changed a list" would say she did
    something she did not (ADR-018 §6).
    """
    memory = Memory.new(tmp_path_factory.mktemp("lists") / "vault")
    memory.changes("capture: Tent is torn", by="marge", committed_by="archivist-bot", at=today(0.2),
                   files={"family/camping/tent.md": TENT})
    memory.changes("lists: add action items to camping", by="marge", committed_by="archivist-bot",
                   at=today(0.3), files={"family/lists/camping.md": CAMPING})
    page = Browser(reading(cards_api(memory, tmp_path_factory)), listener=PUBLIC_PORT,
                   host="192.168.1.20:42000").opens("/dashboard")

    assert page.texts("sentence") == ["The memory was updated (1 list)",
                                      "Marge filed a note: Tent is torn"]
    assert page.texts("name") == ["Marge"]


def test_in_german_the_rebuild_reads_as_what_happened_to_the_diary(reading, diary_night_api):
    """The same compile on a German page: "Das Tagebuch wurde aktualisiert"."""
    page = Browser(reading(diary_night_api), listener=PUBLIC_PORT, host="192.168.1.20:42000",
                   language="de").opens("/dashboard")

    assert page.texts("sentence")[0] == "Das Tagebuch wurde aktualisiert (42 Einträge)"


# ── The widget's data and its states ─────────────────────────────────────

def test_the_widget_reads_the_same_activity_as_json(phone):
    """Given the widget's script refreshing, then its endpoint answers the
    same days and sentences the page shows, with the card links to use.

    One endpoint per widget, answering what to show rather than raw data,
    so the page and the script never word a change differently.
    """
    status, activity = phone.reads("/dashboard/api/activity")

    assert status == 200
    assert [d["label"] for d in activity["days"]] == ["Today", "Yesterday",
                                                      f"{FOUR_DAYS_AGO:%A}, {FOUR_DAYS_AGO.day} {FOUR_DAYS_AGO:%B}"]
    reply = activity["days"][0]["entries"][1]
    assert (reply["who"]["name"], reply["text"]) == ("Marge", "replied to a note")
    assert reply["cards"] == [{"title": "Tent is torn", "type": "note", "href": "/go/note-7f3a9c21e4",
                               "sep": ""}]


def test_an_empty_memory_says_nothing_has_happened_yet(reading, tmp_path_factory):
    """Given a memory nothing was filed in yet, then the widget says so in
    a sentence rather than showing an empty box."""
    api = cards_api(Memory.new(tmp_path_factory.mktemp("empty") / "vault"), tmp_path_factory)
    page = Browser(reading(api), listener=PUBLIC_PORT, host="localhost:42000").opens("/dashboard")

    assert page.widget("activity").attribute("fs-widget", "state") == "empty"
    assert "Nothing has happened yet." in page.text
    assert page.texts("sentence") == []


def test_when_the_memory_does_not_answer_the_widget_says_so_and_offers_a_retry(reading):
    """Given the memory stacklet down, then the page still loads, and the
    widget says in one sentence that the memory does not answer, with a
    way to try again; its endpoint answers 503."""
    unreachable = "http://127.0.0.1:9"
    browser = Browser(reading(unreachable), listener=PUBLIC_PORT, host="localhost:42000")

    page = browser.opens("/dashboard")

    activity = page.widget("activity")
    assert page.status == 200 and activity.attribute("fs-widget", "state") == "error"
    assert "The memory does not answer right now." in activity.text
    assert [label for label, _ in activity.links("fs-error")] == ["Try again"]
    assert browser.reads("/dashboard/api/activity")[0] == 503


# ── What the network reaches ─────────────────────────────────────────────

def test_the_network_reaches_the_dashboard_and_what_it_loads(phone):
    """The page, its styles and scripts, its fonts and its widget's data
    are served on the published port."""
    for path in ("/dashboard", "/dashboard/api/activity", "/dashboard/static/tokens.css",
                 "/dashboard/static/widgets/fs-widget.js", "/dashboard/static/widgets/fs-activity.js",
                 "/dashboard/fonts/inter-latin-wght-normal.woff2"):
        assert phone.opens(path).status == 200, path


def test_the_network_reaches_nothing_else_new(phone):
    """Everything else on the published port stays as before: the
    persistent links and nothing more. The card browser, its API and
    the ops endpoints stay off the network."""
    for path in ("/cards", "/cards/api/history", "/cards/api/find", "/logs",
                 "/tools/stack/status", "/dashboardx", "/dashboard/api/find",
                 "/dashboard/static/../server.py", "/dashboard/static/index.html",
                 "/dashboard/static/gallery.html",
                 "/dashboard/fonts/secrets.toml"):
        assert phone.opens(path).status == 404, path

