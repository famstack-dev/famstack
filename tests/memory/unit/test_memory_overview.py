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
import urllib.request

import pytest

from tests.memory.unit.overview_world import HOMERS_OWN, TODAY, VIEWS, ask, ids, overview

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
    # What each change was: a hand edit reads "changed", as `stack.cards.commits.kind_of` says.
    assert {i["what"] for i in me["changed"]["items"]} == {"changed"}
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
    "topic/topic-camping": {"topic", "up_next", "cards", "links", "counts", "bookmarks", "documents",
                            "lists", "changed", "entries"},
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


def test_a_short_list_is_one_part(simpsons):
    """Given five notes, then their page is part 1 of 1."""
    notes = overview(simpsons, "kind/note")["cards"]

    assert (notes["part"], notes["parts"]) == (1, 1)


def test_a_topic_in_anothers_own_folder_is_refused(simpsons):
    """Given Homer's own topic, when Marge asks, then it is not found."""
    assert ask(simpsons, "topic/topic-bowling")[0] == 404
