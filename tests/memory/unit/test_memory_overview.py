"""The overview of the family memory for one person, as a page reads it.

Core's memory pages ask the curator's cards API for `GET /overview/<view>`
with the audience of the paired device, and render what comes back. Driven
here over real HTTP against memory's test instance (`running_memory`):
Forgejo holding the Simpsons' cards, the curator's clone and its API.

What to assert: which cards reach which section for whom, the counts, and
the shape a page reads. Above all, that nothing from another person's own
folder reaches Marge: not as an item, not as a link, not in a count.

"Today" is 2026-10-01 (`STACK_TODAY`, the household clock's setting).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from tests.memory.unit.instance import RunningMemory, running_memory

TODAY = "2026-10-01"
SEEDED = "2026-09-01T10:00:00"


def person(slug: str, title: str) -> str:
    return f"---\ntype: person\ncard_id: person-{slug}\ntitle: {title}\naccount: {slug}\n---\n\n# {title}\n"


def topic(slug: str, title: str) -> str:
    return f"---\ntype: topic\ncard_id: topic-{slug}\ntitle: {title}\n---\n\n# {title}\n"


def card(kind: str, card_id: str, title: str, day: str, persons: list[str], **fields: str) -> str:
    people = "".join(f"\n  - {p}" for p in persons)
    extra = "".join(f"\n{k}: \"{v}\"" for k, v in fields.items())
    return (f"---\ntype: {kind}\ncard_id: {card_id}\ntitle: {title}\ndate: \"{day}\"\n"
            f"persons:{people or ' []'}{extra}\n---\n\n# {title}\n")


def correspondent(slug: str, title: str) -> str:
    return f"---\ntype: correspondent\ncard_id: correspondent-{slug}\ntitle: {title}\n---\n\n# {title}\n"


GROCERIES = ("---\ntype: list\ncard_id: list-groceries\ntitle: Groceries\n---\n\n"
             "- [ ] Milk\n- [ ] Pumpkin for the party 📅 2026-10-05\n- [ ] Eggs\n- [x] Duff\n")
# One list, items for two people: each one's items are theirs alone.
PARTY = ("---\ntype: list\ncard_id: list-party\ntitle: Party\n---\n\n"
         "- [ ] Cake [Marge](../people/marge.md) 📅 2026-10-08\n"
         "- [ ] Balloons [Bart](../people/bart.md) 📅 2026-10-09\n")
CHORES = ("---\ntype: list\ncard_id: list-chores\ntitle: Bart's chores\n---\n\n"
          "- [ ] Clean the room [Bart](../people/bart.md)\n")

SIMPSONS = {
    "family/people/homer.md": person("homer", "Homer Simpson"),
    "family/people/marge.md": person("marge", "Marge Simpson"),
    "family/people/bart.md": person("bart", "Bart Simpson"),
    "family/people/lisa.md": person("lisa", "Lisa Simpson"),
    "family/camping/topic.md": topic("camping", "Camping"),
    "family/camping/notes/tent.md": card("note", "note-tent", "The tent is torn", "2026-09-03", ["marge"]),
    "family/camping/notes/stove.md": card("note", "note-stove", "Gas for the stove", "2026-09-04", ["homer"]),
    "family/lists/groceries.md": GROCERIES,
    "family/lists/chores.md": CHORES,
    "family/lists/party.md": PARTY,
    "family/notes/school-play.md": card("note", "note-play", "Lisa's school play", "2026-10-20",
                                        ["lisa", "marge"]),
    "family/documents/insurance.md": card("document", "document-insurance", "Car insurance",
                                          "2026-09-02", ["marge"], document_type="Policy",
                                          correspondent="Springfield Insurance"),
    "family/documents/receipt.md": card("document", "document-receipt", "Groceries receipt",
                                        "2026-09-05", ["marge"], document_type="Receipt",
                                        correspondent="Kwik-E-Mart"),
    "family/correspondents/springfield-insurance.md": correspondent("springfield-insurance",
                                                                    "Springfield Insurance"),
    "family/correspondents/kwik-e-mart.md": correspondent("kwik-e-mart", "Kwik-E-Mart"),
    "family/correspondents/power-plant.md": correspondent("power-plant", "Power Plant"),
    "family/bookmarks/pie.md": card("bookmark", "bookmark-pie", "Pumpkin pie", "2026-09-06", ["marge"],
                                    resource="https://example.org/pie"),
    "marge/garden/topic.md": topic("garden", "Garden"),
    "marge/garden/notes/roses.md": card("note", "note-roses", "Roses need cutting", "2026-09-07", []),
    # Homer's own folder: never Marge's, though a card names her.
    "homer/bowling/topic.md": topic("bowling", "Bowling"),
    "homer/bowling/notes/league.md": card("note", "note-league", "Bowling league", "2026-09-08",
                                          ["homer", "marge"]),
    "homer/documents/payslip.md": card("document", "document-payslip", "Payslip September",
                                       "2026-09-09", ["homer", "marge"], document_type="Payslip",
                                       correspondent="Power Plant"),
}
# A note without a date of its own: the graph gives it the day it last changed.
UNDATED = ("---\ntype: note\ncard_id: note-undated\ntitle: Call the plumber\n"
           "persons:\n  - marge\n---\n\n# Call the plumber\n")
SURPRISE = {"homer/notes/surprise.md": card("note", "note-surprise", "Surprise party for Marge",
                                            "2026-10-10", ["marge"])}
# What of Homer's own folder must never reach anyone else, by id and by title.
HOMERS_OWN = ["note-surprise", "Surprise party", "note-league", "Bowling league",
              "document-payslip", "Payslip September", "topic-bowling", "Power Plant"]

VIEWS = ["me", "family", "person/marge", "person/bart", "person/homer", "topic/topic-camping",
         "sender/correspondent-kwik-e-mart", "documents",
         "documents/Payslip", "everything", "kind/note", "kind/document", "senders"]


@pytest.fixture(scope="module")
def simpsons(tmp_path_factory) -> Iterator[RunningMemory]:
    """The Simpsons' memory, seeded a month ago. Marge edited the tent note
    three days ago, Homer filed the surprise party in his own folder two
    days ago. Yesterday the memory bot tidied the chores, and rebuilt
    Marge's roses note in her name. Today Marge ticked the milk and wrote
    down an undated note."""
    with pytest.MonkeyPatch.context() as clock, \
            running_memory(tmp_path_factory.mktemp("overview"), SIMPSONS, seeded_at=SEEDED) as memory:
        clock.setenv("STACK_TODAY", TODAY)
        memory.edited_in_forgejo({"family/camping/notes/tent.md":
                                  SIMPSONS["family/camping/notes/tent.md"] + "\nIt rained in.\n"},
                                 by="marge", at="2026-09-28T10:00:00")
        memory.edited_in_forgejo(SURPRISE, by="homer", at="2026-09-29T10:00:00")
        memory.edited_in_forgejo({"family/lists/chores.md": CHORES + "\n"},
                                 by="memory-bot", subject="cards: tidy", at="2026-09-30T10:00:00")
        memory.edited_in_forgejo({"marge/garden/notes/roses.md":
                                  SIMPSONS["marge/garden/notes/roses.md"] + "\nRebuilt.\n"},
                                 by="marge", committer="memory-bot",
                                 subject="update: diary entry rebuilt", at="2026-09-30T11:00:00")
        memory.edited_in_forgejo({"family/lists/groceries.md": GROCERIES.replace("- [ ] Milk", "- [x] Milk"),
                                  "family/notes/undated.md": UNDATED},
                                 by="marge", at=f"{TODAY}T09:00:00")
        yield memory


def ask(memory: RunningMemory, view: str, audience: str = "marge") -> tuple[int, dict]:
    """(status, answer) of `GET /overview/<view>?audience=<audience>`;
    `view` may carry its own query (`kind/note?part=2`)."""
    joined = "&" if "?" in view else "?"
    url = f"{memory.curator}/overview/{view}" + (f"{joined}audience={audience}" if audience else "")
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def overview(memory: RunningMemory, view: str, audience: str = "marge") -> dict:
    status, answer = ask(memory, view, audience)
    assert status == 200, answer
    return answer


def ids(part: dict | list) -> list[str]:
    """The card ids of a section's items, in order."""
    items = part["items"] if isinstance(part, dict) else part
    return [i["id"] for i in items]


# ── My memory ─────────────────────────────────────────────────────────────

def test_my_memory_holds_marges_sections(simpsons):
    """Given the audience Marge, when "My memory" is asked, then it holds
    what comes up for her, her lists, her topics, what changed in two
    weeks, her documents by kind, their senders, the links and the counts.

    It is the landing page of her phone: one answer has all of it.
    """
    me = overview(simpsons, "me")

    assert me["person"] == {"id": "marge", "card": "person-marge", "title": "Marge Simpson"}
    assert [(i["id"], i["title"], i["day"]) for i in me["up_next"]["items"]] == [
        ("list-groceries", "Pumpkin for the party", "2026-10-05"),
        ("list-party", "Cake", "2026-10-08"),
        ("note-play", "Lisa's school play", "2026-10-20")]
    assert [(lst["id"], lst["open"]) for lst in me["lists"]] == [("list-groceries", 2),
                                                                ("list-party", 2)]
    assert [(t["id"], t["own"]) for t in me["topics"]] == [("topic-camping", False),
                                                          ("topic-garden", True)]
    assert [(i["id"], i["by"], i["on"]) for i in me["changed"]["items"]] == [
        ("note-undated", "marge", TODAY), ("note-tent", "marge", "2026-09-28")]
    assert ids(me["bookmarks"]) == ["bookmark-pie"]
    assert [(k["kind"], k["count"]) for k in me["documents"]["kinds"]] == [("Policy", 1), ("Receipt", 1)]
    assert {s["id"] for s in me["senders"]} == {"correspondent-springfield-insurance",
                                                "correspondent-kwik-e-mart"}
    assert [(p["id"], p["count"]) for p in me["links"]["people"]] == [("lisa", 1)]
    # Her two lists, as the lists section shows them: the counts agree with it.
    assert me["counts"] == {"note": 4, "document": 2, "bookmark": 1, "list": 2}


def test_a_bots_change_is_not_what_changed(simpsons):
    """Given the memory bot changed the chores yesterday, then the chores
    are not in what changed: no person changed them in two weeks.

    A migration touches every card; it would bury what people did.
    """
    family = overview(simpsons, "family")

    assert "list-chores" not in ids(family["changed"])


def test_a_bots_rebuild_in_a_persons_name_is_not_what_changed(simpsons):
    """Given the memory bot rebuilt Marge's roses note with Marge as the
    author, then it is not in her what changed, as `/history` calls the
    same commit the machine's.

    A page and the activity widget must not disagree about who did what.
    """
    me = overview(simpsons, "me")
    history = json.loads(urllib.request.urlopen(f"{simpsons.curator}/history?audience=marge").read())

    [rebuild] = [c for c in history["changes"] if c["subject"] == "update: diary entry rebuilt"]
    assert (rebuild["who"], rebuild["machine"]) == ("marge", True)
    assert "note-roses" not in ids(me["changed"])


# ── Up next ───────────────────────────────────────────────────────────────

def test_up_next_takes_only_a_cards_own_date(simpsons):
    """Given an undated note naming Marge and the groceries, both changed
    today, then neither is up next as a card: only a date of the card's own
    or a list item's due day is.

    The graph gives an undated card the day it last changed; that says when
    it was written, not when it happens.
    """
    up_next = overview(simpsons, "me")["up_next"]["items"]

    assert "note-undated" not in [i["id"] for i in up_next]
    assert [i["title"] for i in up_next if i["id"] == "list-groceries"] == ["Pumpkin for the party"]


def test_a_shared_lists_items_are_each_persons_own(simpsons):
    """Given the party list with the cake for Marge and the balloons for
    Bart, then Marge's up next holds the cake and Bart's the balloons, and
    neither the other's. An item for no one is everyone's."""
    marge = [i["title"] for i in overview(simpsons, "me")["up_next"]["items"]]
    bart = [i["title"] for i in overview(simpsons, "me", audience="bart")["up_next"]["items"]]

    assert ("Cake" in marge, "Balloons" in marge) == (True, False)
    assert ("Cake" in bart, "Balloons" in bart) == (False, True)
    assert "Pumpkin for the party" in marge and "Pumpkin for the party" in bart


# ── Who may see what ──────────────────────────────────────────────────────

@pytest.mark.parametrize("view", VIEWS)
def test_nothing_of_homers_own_folder_reaches_marge(simpsons, view):
    """Given cards in Homer's own folder, two of which name Marge, when any
    view is answered for Marge, then no item, link or count comes from them.

    A private card leaks as much through a count or a sender's name as
    through its title.
    """
    status, answer = ask(simpsons, view)

    text = json.dumps(answer)
    assert [leak for leak in HOMERS_OWN if leak in text] == []
    assert status in (200, 404)


def test_counts_hold_only_what_the_audience_may_read(simpsons):
    """Given Homer's own notes, then the family's counts for Marge leave
    them out, and Homer's own counts hold them."""
    for_marge = overview(simpsons, "family")["counts"]
    for_homer = overview(simpsons, "family", audience="homer")["counts"]

    assert for_marge == {"note": 5, "document": 2, "bookmark": 1, "list": 3}
    assert for_homer == {"note": 6, "document": 3, "bookmark": 1, "list": 3}


@pytest.mark.parametrize("view", ["person/nobody", "topic/topic-bowling", "topic/topic-nowhere",
                                  "sender/correspondent-nobody", "topic/person-bart",
                                  "calendar", "person", "documents/Nonsense", "kind/email",
                                  "kind/person", "kind/topic", "kind/nonsense", "kind",
                                  "senders/correspondent-kwik-e-mart", "everything/note"])
def test_a_page_the_audience_may_not_read_is_not_found(simpsons, view):
    """Given a topic in Homer's own folder, a person or topic that does not
    exist, a document kind with no document, or a view that is no page, then the answer is 404, the same for
    each, so a 404 does not tell that a hidden one exists."""
    status, _ = ask(simpsons, view)

    assert status == 404


def test_my_memory_needs_one_person(simpsons):
    """Given no audience (the admin's view), then there is no "me": 404."""
    status, _ = ask(simpsons, "me", audience="")

    assert status == 404


# ── Lists ─────────────────────────────────────────────────────────────────

def test_a_list_naming_no_one_is_everyones_and_one_naming_bart_is_his(simpsons):
    """Given the groceries naming no one and the chores naming only Bart,
    then both are Bart's, and only the groceries are Marge's. The party
    names both, so it is each one's."""
    assert [lst["id"] for lst in overview(simpsons, "me")["lists"]] == ["list-groceries", "list-party"]
    assert [lst["id"] for lst in overview(simpsons, "me", audience="bart")["lists"]] == [
        "list-chores", "list-groceries", "list-party"]
    assert [lst["id"] for lst in overview(simpsons, "person/bart")["lists"]] == [
        "list-chores", "list-groceries", "list-party"]


def test_a_list_carries_its_open_count_never_its_items(simpsons):
    """Given the groceries with two open items and two ticked, then the
    list says 2 open and carries no item."""
    groceries = overview(simpsons, "me")["lists"][0]

    assert set(groceries) == {"id", "type", "title", "day", "open"}
    assert (groceries["title"], groceries["open"]) == ("Groceries", 2)


# ── Topics ────────────────────────────────────────────────────────────────

def test_a_topics_count_is_all_its_cards_with_those_about_the_person(simpsons):
    """Given camping with Marge's tent and Homer's stove, then camping
    counts 2 cards, 1 of them about Marge, as the topic's page counts."""
    camping = next(t for t in overview(simpsons, "me")["topics"] if t["id"] == "topic-camping")
    page = overview(simpsons, "topic/topic-camping")

    assert (camping["cards"], camping["about"]) == (2, 1)
    assert page["cards"]["count"] == camping["cards"]
    assert camping["latest"]["id"] == "note-stove"


# ── Each view, its sections ───────────────────────────────────────────────

SECTIONS = {
    "me": {"person", "up_next", "lists", "topics", "changed", "bookmarks", "documents", "senders",
           "links", "counts"},
    "family": {"people", "up_next", "lists", "changed", "documents", "links", "counts"},
    "person/bart": {"person", "up_next", "lists", "topics", "changed", "documents", "cards", "links",
                    "counts"},
    "topic/topic-camping": {"topic", "up_next", "cards", "links", "counts"},
    "sender/correspondent-kwik-e-mart": {"sender", "cards", "links", "counts"},
    "documents": {"documents", "senders", "links", "counts"},
    "everything": {"kinds", "senders", "counts"},
    "kind/note": {"cards", "links", "counts"},
    "senders": {"senders", "counts"},
}


@pytest.mark.parametrize("view", list(SECTIONS))
def test_each_view_answers_only_its_pages_sections(simpsons, view):
    """Given each view, then it answers the sections its page shows and no other."""
    answer = overview(simpsons, view)

    assert set(answer) - {"view", "today", "total"} == SECTIONS[view]
    assert (answer["view"], answer["today"]) == (view, TODAY)


@pytest.mark.parametrize("view", list(SECTIONS))
def test_each_view_answers_how_many_cards_it_is_about(simpsons, view):
    """Given each view, then `total` is how many cards it is about, all its
    counts together, so a page shows the number without adding it up."""
    answer = overview(simpsons, view)

    assert answer["total"] == sum(answer["counts"].values())


def test_the_documents_answer_how_many_there_are_of_every_kind(simpsons):
    """Given the documents page narrowed to receipts, then `total` counts
    the documents of every kind, for the tile "All documents"."""
    documents = overview(simpsons, "documents/Receipt")["documents"]

    assert (documents["count"], documents["total"]) == (1, 2)


def test_a_topics_owner_is_the_person_whose_own_folder_holds_it(simpsons):
    """Given Marge's garden topic in her own folder and the family's
    camping, then the garden's page names her as its owner and camping
    names none; on the Mac, Homer's bowling topic names Homer."""
    assert overview(simpsons, "topic/topic-garden")["topic"]["owner"] == "marge"
    assert overview(simpsons, "topic/topic-camping")["topic"]["owner"] == ""
    assert overview(simpsons, "topic/topic-bowling", audience="")["topic"]["owner"] == "homer"


def test_the_sender_page_lists_its_documents(simpsons):
    """Given the Kwik-E-Mart's receipt, then its page holds the receipt."""
    sender = overview(simpsons, "sender/correspondent-kwik-e-mart")

    assert sender["sender"] == {"id": "correspondent-kwik-e-mart", "title": "Kwik-E-Mart",
                                "kinds": ["Receipt"]}
    assert ids(sender["cards"]) == ["document-receipt"]


def test_documents_of_one_kind_keep_every_kind(simpsons):
    """Given the documents page narrowed to receipts, then its items and its
    count are the receipts, and its kinds still name every kind, for the
    page's tiles."""
    documents = overview(simpsons, "documents/Receipt")["documents"]

    assert ids(documents) == ["document-receipt"]
    assert [k["kind"] for k in documents["kinds"]] == ["Policy", "Receipt"]
    assert documents["count"] == 1


# ── Everything: every kind of card, and the senders ───────────────────────

def test_everything_counts_each_kind_and_the_senders_the_audience_may_read(simpsons):
    """Given Marge's and Homer's audiences, when the menu's "Everything" is
    asked, then each answers the kinds of card it has cards of, in the
    menu's fixed order, each with its count, and how many senders sent it
    documents. Homer's own payslip adds a document and its sender for him
    alone.

    The menu shows these numbers on every page, so a count must never
    hold what the device may not read.
    """
    marge = overview(simpsons, "everything")
    homer = overview(simpsons, "everything", audience="homer")

    assert [(k["type"], k["count"]) for k in marge["kinds"]] == [
        ("document", 2), ("note", 5), ("bookmark", 1), ("list", 3)]
    assert marge["senders"] == {"count": 2}
    assert [(k["type"], k["count"]) for k in homer["kinds"]] == [
        ("document", 3), ("note", 6), ("bookmark", 1), ("list", 3)]
    assert homer["senders"] == {"count": 3}


def test_everythings_counts_are_the_counts_of_each_kinds_page(simpsons):
    """Given every kind in the menu, then the count it shows is the count
    the kind's page answers, so the menu and the page agree."""
    for kind in overview(simpsons, "everything")["kinds"]:
        assert overview(simpsons, f"kind/{kind['type']}")["cards"]["count"] == kind["count"]


def test_a_kind_lists_its_cards_newest_first(simpsons):
    """Given Marge's notes, then the page of the kind "note" lists them
    newest first, an undated note by the day it last changed, with the
    people and topics they name."""
    notes = overview(simpsons, "kind/note")

    assert ids(notes["cards"]) == ["note-play", "note-undated", "note-roses", "note-stove",
                                   "note-tent"]
    assert notes["counts"] == {"note": 5}
    assert [t["id"] for t in notes["links"]["topics"]] == ["topic-camping", "topic-garden"]


def test_the_senders_list_each_sender_with_its_documents(simpsons):
    """Given a receipt from the Kwik-E-Mart and a policy from Springfield
    Insurance, then the senders answer both, each with its documents'
    count and kinds; the Power Plant sent only Homer's own payslip, so
    Marge's answer does not name it."""
    senders = overview(simpsons, "senders")

    assert [(s["id"], s["count"], s["kinds"]) for s in senders["senders"]] == [
        ("correspondent-kwik-e-mart", 1, ["Receipt"]),
        ("correspondent-springfield-insurance", 1, ["Policy"])]
    assert senders["counts"] == {"document": 2}


def test_every_item_carries_its_card_id_type_title_and_day(simpsons):
    """Given every view, then each item in it names its card, so a page can
    link it to the card viewer."""
    for view in SECTIONS:
        answer = overview(simpsons, view)
        for item in items_of(answer):
            assert {"id", "type", "title", "day"} <= set(item), (view, item)


def items_of(answer: dict) -> list[dict]:
    """Every item of an answer: in a part that lists items, in its kinds,
    and the newest of a topic."""
    found = []
    for part in answer.values():
        if isinstance(part, dict) and "items" in part:
            found += part["items"]
            found += [k["newest"] for k in part.get("kinds", [])]
        if isinstance(part, list):
            found += [t["latest"] for t in part if t.get("latest")]
    return found


# ── Where a name leads ────────────────────────────────────────────────────

# A family note names Marge by her alias, Homer, and Grampa, whose person
# card is in Homer's own folder; a receipt names its sender.
NAMED = {
    "family/people/homer.md": person("homer", "Homer Simpson"),
    "family/people/marge.md": person("marge", "Marge Simpson").replace(
        "account: marge", "aliases:\n  - Mom\naccount: marge"),
    "homer/people/grampa.md": person("grampa", "Abe Simpson"),
    "family/notes/pot-roast.md": card("note", "note-pot-roast", "Pot roast", "2026-09-20",
                                      ["Mom", "homer", "grampa"]),
    "family/documents/receipt.md": card("document", "document-receipt", "Groceries receipt",
                                        "2026-09-05", ["marge"], document_type="Receipt",
                                        correspondent="Kwik-E-Mart"),
    "family/correspondents/kwik-e-mart.md": correspondent("kwik-e-mart", "Kwik-E-Mart"),
}


@pytest.fixture(scope="module")
def named(tmp_path_factory) -> Iterator[RunningMemory]:
    with pytest.MonkeyPatch.context() as clock, \
            running_memory(tmp_path_factory.mktemp("named"), NAMED, seeded_at=SEEDED) as memory:
        clock.setenv("STACK_TODAY", TODAY)
        yield memory


def test_an_item_names_the_page_of_each_person_the_audience_may_read(named):
    """Given a note naming "Mom", Homer and Grampa, then each item of it
    names the page each name opens: Marge's for "Mom", as the memory links
    the note to her, Homer's, and none for Grampa, whose card Marge may not
    read. On Homer's audience, Grampa's page is there."""
    for_marge = next(i for i in overview(named, "person/marge")["cards"]["items"]
                     if i["id"] == "note-pot-roast")
    for_homer = next(i for i in overview(named, "person/homer", audience="homer")["cards"]["items"]
                     if i["id"] == "note-pot-roast")

    assert for_marge["person_pages"] == {"Mom": "marge", "homer": "homer", "grampa": ""}
    assert for_homer["person_pages"]["grampa"] == "grampa"


def test_a_documents_item_names_its_senders_page(named):
    """Given a receipt from the Kwik-E-Mart, which has a sender card, then
    its item names the sender's page by the card's id."""
    receipt = overview(named, "documents")["documents"]["items"][0]

    assert (receipt["sender"], receipt["sender_page"]) == ("Kwik-E-Mart", "correspondent-kwik-e-mart")


# ── A long list, part by part ─────────────────────────────────────────────

# 45 notes in the garden topic and 45 receipts from the Kwik-E-Mart, one
# a day, all naming Marge: more than a page lists at once (40), so each
# long list has two parts, and Marge's 90 cards three. Homer keeps one
# note in his own folder, the newest, which Marge may not read.
MANY = 45


def day(n: int) -> str:
    return f"2026-0{7 + n // 30}-{1 + n % 30:02d}"


LONG_LISTS = {
    "family/people/marge.md": person("marge", "Marge Simpson"),
    "family/correspondents/kwik-e-mart.md": correspondent("kwik-e-mart", "Kwik-E-Mart"),
    "family/garden/topic.md": topic("garden", "Garden"),
    "homer/notes/bet.md": card("note", "note-bet", "A bet on the game", "2026-09-30", ["homer"]),
    **{f"family/garden/notes/{n:02d}.md": card("note", f"note-{n:02d}", f"Note {n}", day(n), ["marge"])
       for n in range(MANY)},
    **{f"family/documents/{n:02d}.md": card("document", f"document-{n:02d}", f"Receipt {n}", day(n),
                                            ["marge"], document_type="Receipt", correspondent="Kwik-E-Mart")
       for n in range(MANY)},
}

LONG_VIEWS = {"kind/note": "cards", "topic/topic-garden": "cards", "documents": "documents",
              "documents/Receipt": "documents", "sender/correspondent-kwik-e-mart": "cards"}


@pytest.fixture(scope="module")
def long_lists(tmp_path_factory) -> Iterator[RunningMemory]:
    with pytest.MonkeyPatch.context() as clock, \
            running_memory(tmp_path_factory.mktemp("long"), LONG_LISTS, seeded_at=SEEDED) as memory:
        clock.setenv("STACK_TODAY", TODAY)
        yield memory


@pytest.mark.parametrize("view", LONG_VIEWS)
def test_a_long_list_comes_in_parts_newest_first(long_lists, view):
    """Given 45 notes in a topic and 45 receipts from one sender, then the
    page of the notes, of the topic, of the documents, of the receipts and
    of the sender each answer
    the newest 40 as part 1 of 2, and `part=2` the 5 oldest; the count is
    every card on both parts, so the page says how many there are."""
    first = overview(long_lists, view)[LONG_VIEWS[view]]
    second = overview(long_lists, f"{view}?part=2")[LONG_VIEWS[view]]

    newest = sorted(range(MANY), reverse=True)
    assert [int(i["id"][-2:]) for i in first["items"]] == newest[:40]
    assert [int(i["id"][-2:]) for i in second["items"]] == newest[40:]
    assert (first["part"], first["parts"], second["part"], second["parts"]) == (1, 2, 2, 2)
    assert first["count"] == second["count"] == MANY


@pytest.mark.parametrize("query", ["part=3", "part=0", "part=-1", "part=abc", "part=%C2%B2",
                                   "part=%D9%A3", pytest.param("part=" + "9" * 5000, id="part=5000-digits")])
def test_a_part_past_the_list_is_not_found(long_lists, query):
    """Given two parts of notes, then a part before the first or after the
    last is no page: 404, as an address that names nothing. So is a part
    that is no number in ASCII digits (`abc`, `²`, `٣`) or too long to be
    one: never part 1 or part 3 under a second address."""
    assert ask(long_lists, f"kind/note?{query}")[0] == 404


def test_the_parts_leave_out_what_the_audience_may_not_read(long_lists):
    """Given 45 family notes and Homer's own newest note, then Marge's
    notes come in two parts that count 45 and never hold Homer's note;
    Homer's count 46, his note first, so his parts are cut from his
    own, larger list."""
    marge = [overview(long_lists, f"kind/note?part={n}")["cards"] for n in (1, 2)]
    homer = [overview(long_lists, f"kind/note?part={n}", audience="homer")["cards"] for n in (1, 2)]

    assert {(p["count"], p["parts"]) for p in marge} == {(MANY, 2)}
    assert "note-bet" not in [i["id"] for p in marge for i in p["items"]]
    assert [len(p["items"]) for p in marge] == [40, 5]
    assert {(p["count"], p["parts"]) for p in homer} == {(MANY + 1, 2)}
    assert homer[0]["items"][0]["id"] == "note-bet" and len(homer[1]["items"]) == 6


def test_a_persons_cards_come_in_parts_and_the_rest_of_the_page_stays_whole(long_lists):
    """Given 90 cards naming Marge, then her page answers them in three
    parts, the last holding the 10 oldest, and every part keeps the
    page's other sections, so each part still shows her documents."""
    parts = [overview(long_lists, f"person/marge?part={n}") for n in (1, 2, 3)]

    assert [len(p["cards"]["items"]) for p in parts] == [40, 40, 10]
    assert {(p["cards"]["count"], p["cards"]["parts"]) for p in parts} == {(2 * MANY, 3)}
    assert len({i["id"] for p in parts for i in p["cards"]["items"]}) == 2 * MANY
    assert parts[0]["documents"] == parts[2]["documents"]
    assert ask(long_lists, "person/marge?part=4")[0] == 404


@pytest.mark.parametrize("view", ["me", "family", "senders", "everything"])
def test_a_page_that_is_not_one_list_has_no_second_part(long_lists, view):
    """Given a page that shows a few of many things, then it has no parts:
    `part=2` is not found, so no address shows the same page twice."""
    assert ask(long_lists, f"{view}?part=2")[0] == 404


def test_a_short_list_is_one_part(simpsons):
    """Given five notes, then their page is part 1 of 1."""
    notes = overview(simpsons, "kind/note")["cards"]

    assert (notes["part"], notes["parts"]) == (1, 1)


# ── Nothing in it ─────────────────────────────────────────────────────────

def test_an_empty_memory_answers_empty_sections(tmp_path, monkeypatch):
    """Given a memory with only Marge's person card and a topic without
    cards, then "My memory", the topic's page, "Everything" and the
    senders answer every section empty, not an error."""
    monkeypatch.setenv("STACK_TODAY", TODAY)
    files = {"family/people/marge.md": person("marge", "Marge Simpson"),
             "family/camping/topic.md": topic("camping", "Camping")}
    with running_memory(tmp_path, files) as memory:
        me = overview(memory, "me")
        camping = overview(memory, "topic/topic-camping")
        everything = overview(memory, "everything")
        senders = overview(memory, "senders")

    assert everything["kinds"] == [] and everything["senders"] == {"count": 0}
    assert senders["senders"] == [] and senders["total"] == 0
    assert me["lists"] == me["topics"] == me["senders"] == []
    assert me["counts"] == {} and me["links"] == {"people": [], "topics": [], "senders": []}
    assert me["up_next"] == me["changed"] == me["bookmarks"] == {"count": 0, "items": []}
    assert me["documents"] == {"count": 0, "total": 0, "kinds": [], "items": []}
    assert camping["cards"] == {"count": 0, "items": [], "part": 1, "parts": 1}
