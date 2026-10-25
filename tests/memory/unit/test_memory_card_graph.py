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

from tests.springfield import make_stack, write_instance
from tests.memory.unit.dsl import Memory, Person

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
def stack(tmp_path):
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


# ── A topic card declares a topic, which tags name by its id ─────────────
#
# A topic card is the household's own topic on top of the ontology: its
# folder is the topic's id (`duff-gardens`), its title what that id reads
# as, the way an ontology label reads as the ontology's id. A record filed
# elsewhere names the topic by its id among its tags. The family folder
# `medical` has a topic card too, but `medical` is the ontology's id, and
# the ontology's meaning wins.

TICKETS = "document-4d5e6f7a8b"
RIDE = "note-5e6f7a8b9c"
DUFF_GARDENS = {
    "family/duff-gardens/topic.md": card("topic", "topic-duff-gardens", "Summer Trip to Duff Gardens"),
    "family/medical/topic.md": card("topic", "topic-medical", "Arztbesuche"),
    "family/documents/2026/10/day-passes.md":
        card("document", TICKETS, "Two day passes",
             "persons:\n  - homer\ntags:\n  - duff-gardens\n  - medical\n",
             body="\nPaid at the gate, kids half price.\n"),
    "bart/notes/2026/10/rollercoaster.md":
        card("note", RIDE, "The rollercoaster", "persons:\n  - homer\ntags:\n  - duff-gardens\n",
             body="\nThree times in a row, Dad screamed.\n"),
}


@pytest.fixture
def duff_gardens(memory) -> Memory:
    memory.changes("topic: duff gardens", by="marge", at=NOW - timedelta(minutes=30),
                   files=DUFF_GARDENS)
    return memory


class TestATagNamesATopicOfTheRecordsOwnBucket:

    def test_a_record_tagged_with_a_topics_id_links_its_card(self, homer, duff_gardens):
        """Given the family's Duff Gardens topic and the day passes filed
        among the family's documents with its id among their tags, when
        Homer follows the links of the passes, then they reach the topic's
        card, as a record filed into the topic's folder does."""
        assert "topic-duff-gardens" in linked(homer, TICKETS)
        assert TICKETS in linked(homer, "topic-duff-gardens")

    def test_a_record_of_another_bucket_does_not_reach_the_familys_topic(self, homer, duff_gardens):
        """Bart has no Duff Gardens topic of his own: his note's tag links nothing."""
        assert "topic-duff-gardens" not in linked(homer, RIDE)

    def test_a_tag_the_ontology_has_keeps_the_ontologys_meaning(self, homer, duff_gardens):
        """`medical` is the ontology's topic: the family's `medical` folder
        does not take it over, so the passes do not link that folder's card."""
        assert "topic-medical" not in linked(homer, TICKETS)


class TestFindReadsATopicsTitleAsItsLabel:

    def test_a_word_of_the_title_finds_the_records_tagged_with_its_id(self, homer, duff_gardens):
        """Given the day passes tagged `duff-gardens`, which never say
        "summer", when Homer finds "summer", then the passes are found, as
        "Gesundheit" finds a record tagged `medical` (ADR-016 §6)."""
        found = homer.runs("memory", "find", ["summer"])["cards"]

        assert TICKETS in found and "topic-duff-gardens" in found
