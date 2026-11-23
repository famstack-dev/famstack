"""The overview's names, its long lists in parts, and an empty memory.

Driven over real HTTP against memory's test instance (`running_memory`), as
`test_memory_overview.py` is; the shared builders and reads are in
`overview_world.py`. What to assert: which page a name leads to, that a long
list comes 40 cards at a time and a part outside it is no page, and that an
empty memory answers empty sections.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.memory.unit.instance import RunningMemory, running_memory
from tests.memory.unit.overview_world import (SEEDED, TODAY, ask, card, correspondent, overview, person,
                                              topic)


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
