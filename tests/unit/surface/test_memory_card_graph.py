"""`stack memory card` and `links`: cards linked by their card ids (ADR-016 §2).

Every card carries a `card_id`, and every link the commands show names a
card by it. A topic is a folder of one bucket, so a note filed into
`homer/camping/` belongs to Homer's camping topic and not to the family's
`family/camping/`, though the two folders share a name and so did their
topics before card ids (`topic/camping`). The same holds for a list's
monthly archive and the list beside it.

The name from before card ids stays an alias: `topic/camping` opens the
card whose id it spells, `topic-camping`, exactly as that id does, however
many buckets have a camping folder. So does an id a card had before
(`former_card_ids`), unless another card carries it as its own now.

Driven as `./stack memory card` and `./stack memory links` run, through
`Stack.run_cli_command`, against a real git repository at the clone's
place in the data dir. The data is the Simpsons'.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from stack.cards import is_card_id

from .conftest import make_stack, write_instance
from .dsl import Memory, Person

NOW = datetime.now().astimezone().replace(microsecond=0)

FAMILYS_NOTE = "note-2b3c4d5e6f"
HOMERS_NOTE = "note-1a2b3c4d5e"
BARTS_NOTE = "note-3c4d5e6f7a"


def card(card_type: str, card_id: str, title: str, extra: str = "", body: str = "") -> str:
    return f"---\ntype: {card_type}\ncard_id: {card_id}\ntitle: {title}\n{extra}---\n{body}"


# Each bucket's camping topic is numbered as `new_card_id` numbers a slug
# another bucket took first: the family's came first, then Homer's, then Bart's.
FAMILY = {
    "family/people/homer.md": card("person", "person-homer", "Homer Simpson"),
    "family/camping/topic.md": card("topic", "topic-camping", "Camping"),
    "family/camping/notes/2026/10/lantern.md":
        card("note", FAMILYS_NOTE, "Lantern for the lake", body="\nA lantern for the jetty.\n"),
    "family/lists/market.md": card("list", "list-market", "Farmers market", body="\n- [ ] Corn\n"),
}
HOMERS = {
    "homer/camping/topic.md": card("topic", "topic-camping-2", "Camping"),
    "homer/camping/notes/2026/10/tent.md":
        card("note", HOMERS_NOTE, "Tent pegs bent", "persons:\n  - homer\n",
             body="\nThe pegs bent on the rocks.\n"),
    "homer/lists/market.md": card("list", "list-market-2", "Market", body="\n- [ ] Donuts\n"),
    "homer/lists/archive/market/2026-10.md":
        card("list", "list-market-2026-10", "Market, 2026-10", "kind: archive\n",
             body="\n- [x] Duff\n"),
    "homer/lists/packing.md":
        card("list", "list-packing", "Packing", "topics:\n  - camping\n", body="\n- [ ] Tent\n"),
}
BARTS = {
    "bart/camping/topic.md": card("topic", "topic-camping-3", "Camping"),
    "bart/camping/notes/2026/10/marshmallows.md":
        card("note", BARTS_NOTE, "Marshmallows burnt", body="\nAll of them.\n"),
}
# A note refiled as a document is a new card, and lists the note's id
# among its former ids (ADR-016 §2).
FORMER_ID = "note-0a1b2c3d4e"
SLIP = "document-9e8d7c6b5a"
REFILED = {
    "family/documents/2026/10/permission-slip.md":
        card("document", SLIP, "Permission slip",
             f"former_card_ids:\n  - {FORMER_ID}\npersons:\n  - homer\n",
             body="\nSigned for the school trip.\n"),
}


@pytest.fixture
def stack(tmp_path, as_stack_runs_it):
    return make_stack(write_instance(tmp_path))


@pytest.fixture
def memory(stack) -> Memory:
    """The memory clone, with a camping folder in three buckets, each with
    its topic card and a note filed into it, a market list in two, and a
    note refiled as a document."""
    vault = Memory.new(stack.data / "memory" / "vault")
    vault.changes("topic: camping", by="marge", at=NOW - timedelta(days=3), files=FAMILY)
    vault.changes("topic: camping, homer's own", by="homer", at=NOW - timedelta(days=2),
                  files=HOMERS)
    vault.changes("topic: camping, bart's own", by="bart", at=NOW - timedelta(days=1), files=BARTS)
    vault.changes("docs: refile the permission slip", by="marge", at=NOW - timedelta(hours=1),
                  files=REFILED)
    return vault


@pytest.fixture
def homer(stack, memory, tmp_path) -> Person:
    return Person("homer", stack, tmp_path)


def linked(person: Person, ref: str) -> set[str]:
    """The ids `stack memory links <ref>` names: the cards one link away."""
    return set(person.runs("memory", "links", [ref])["cards"])


class TestARecordBelongsToTheTopicOfItsOwnBucket:

    def test_homers_note_links_his_camping_topic_only(self, homer):
        """Given camping folders in three buckets, when Homer follows the
        links of his note, then it links his topic and neither of the others."""
        around = linked(homer, HOMERS_NOTE)

        assert "topic-camping-2" in around
        assert not {"topic-camping", "topic-camping-3"} & around

    def test_the_familys_note_links_the_familys_topic_only(self, homer):
        around = linked(homer, FAMILYS_NOTE)

        assert "topic-camping" in around
        assert not {"topic-camping-2", "topic-camping-3"} & around

    def test_each_topic_is_linked_from_its_own_buckets_cards(self, homer):
        """Homer's packing list names `camping`: the camping of his own bucket."""
        assert linked(homer, "topic-camping") == {FAMILYS_NOTE}
        assert linked(homer, "topic-camping-2") == {HOMERS_NOTE, "list-packing"}
        assert linked(homer, "topic-camping-3") == {BARTS_NOTE}


class TestAListArchiveBelongsToTheListOfItsOwnBucket:

    def test_homers_archive_links_his_market_list_not_the_familys(self, homer):
        assert linked(homer, "list-market-2026-10") == {"list-market-2"}


class TestEveryLinkNamesACardByItsId:

    def test_no_link_is_a_name_from_before_card_ids(self, homer):
        """Given cards that all carry a card id, when any of them is
        followed, then each card it reaches is named by its card id."""
        cards_with_links = ["person-homer", "topic-camping", "topic-camping-2", "topic-camping-3",
                            FAMILYS_NOTE, HOMERS_NOTE, BARTS_NOTE, "list-market-2",
                            "list-market-2026-10", "list-packing", SLIP]

        for ref in cards_with_links:
            around = linked(homer, ref)
            assert around and all(is_card_id(other) for other in around), (ref, around)


class TestANameFromBeforeCardIdsOpensTheCardItsIdSpells:

    def test_topic_camping_opens_the_familys_topic_as_its_card_id_does(self, homer):
        """Three buckets have a camping topic, and Bart's folder comes first
        by path. The old name is the family's topic, `topic-camping`: the
        command answers and shows exactly what it does for that id."""
        by_name = homer.runs("memory", "card", ["topic/camping"]), homer.told
        by_id = homer.runs("memory", "card", ["topic-camping"]), homer.told

        assert by_name == by_id
        assert by_name[0]["cards"] == ["topic-camping"]

    def test_person_homer_opens_homers_card_as_its_card_id_does(self, homer):
        by_name = homer.runs("memory", "card", ["person/homer"]), homer.told
        by_id = homer.runs("memory", "card", ["person-homer"]), homer.told

        assert by_name == by_id
        assert by_name[0]["cards"] == ["person-homer"]

    def test_the_links_of_an_old_name_are_those_of_its_card_id(self, homer):
        assert linked(homer, "topic/camping") == linked(homer, "topic-camping") == {FAMILYS_NOTE}


class TestAFormerIdOpensTheCardThatCarriesItNow:

    def test_the_links_of_a_former_id_are_those_of_the_card_that_had_it(self, homer):
        """Given a note refiled as a document, when the note's id is
        followed, then the links are the document's, as for its own id."""
        assert linked(homer, FORMER_ID) == linked(homer, SLIP) == {"person-homer"}

    def test_a_card_that_carries_the_id_as_its_own_wins(self, homer, memory):
        """A note written later with that id (copied by hand, say) is the
        card the id names; the document only had it once."""
        memory.changes("note: the old lantern", by="marge", at=NOW, files={
            "family/camping/notes/2026/10/old-lantern.md": card("note", FORMER_ID, "Old lantern")})

        assert linked(homer, FORMER_ID) == {"topic-camping"}


class TestAPersonIsFoundByTheirCardId:

    def test_find_person_takes_the_card_id_as_it_takes_their_name(self, homer):
        """`--person` narrows what is found to the cards about one person,
        named by their card id as well as by their id or any name."""
        by_id = homer.runs("memory", "find", ["--person", "person-homer"])["cards"]

        assert by_id == homer.runs("memory", "find", ["--person", "homer"])["cards"]
        assert HOMERS_NOTE in by_id
