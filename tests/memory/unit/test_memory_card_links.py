"""Every record links the cards it names: migrations memory/0003 and memory/0004.

ADR-017 §3, §4 and §10: a record names its people by id and links each
person's card, links its correspondent's card, and a topic it is filed
into has a card of its own. The archivist files new records that way;
these two migrations bring the files that were there before to the same
shape, with the archivist's own functions (`stack.cards.links`).

The memory is memory's test instance (`running_memory`) holding a small
fabricated vault: Forgejo, a bare repository, and the curator in front of
it, its card store over its clone of the repository, its HTTP API and
core's card door. A migration reads the cards in the curator's clone and
writes through the curator, which refuses a file whose version changed
since it was read, and a new file that is there already. So a second run
reads what the first one wrote, as it would on an instance.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from stack.frontmatter import parse
from stacklets.memory.lib import BOT_EMAIL, BOT_USERNAME, declare_cards, name_people_by_id
from stacklets.memory.memory_api import Refused
from tests.memory.unit.instance import RunningMemory, git, running_memory
from tests.springfield import REPO

HOMER = "family/people/homer.md"
BART = "family/people/bart.md"
ABRAHAM = "family/people/abraham.md"
LETTER = "family/documents/2026/10/2026-10-09-springfield-elementary-school-notice-p72.md"
TAB = "family/documents/2026/10/2026-10-13-tab-p31.md"
NOTE = "family/itchy-scratchy-land/notes/2026/10/pick-up-grampa-0c37f8.md"
DIARY = "family/diary/entries/2024/03/2024-03-09-b48902f594.md"
LIST = "family/lists/education.md"
MOE = "family/correspondents/moe-s-tavern.md"
SCHOOL = "family/correspondents/springfield-elementary-school.md"
KRUSTY = "family/correspondents/krusty-burger.md"
TOPIC = "family/itchy-scratchy-land/topic.md"


def _person(title: str, pid: str, *aliases: str) -> str:
    names = "".join(f"  - {a}\n" for a in aliases)
    return (f"---\ntype: person\ncard_id: person-{pid}\ntitle: {title}\n"
            + (f"aliases:\n{names}" if names else "") + f"---\n\n# {title}\n")


VAULT = {
    HOMER: _person("Homer Simpson", "homer"),
    BART: _person("Bart Simpson", "bart"),
    ABRAHAM: _person("Abraham Simpson", "abraham", "Grampa"),
    # A school letter as the archivist filed it before cards: names in
    # `persons`, a `Person:` tag, links to the people's compiled pages.
    LETTER: ("---\ntype: document\ncard_id: document-671acc558d\ntitle: Springfield Elementary School Notice\n"
             "date: \"2026-10-09\"\ncorrespondent: Springfield Elementary School\npersons:\n  - Bart\n"
             "  - Mr. Burns\ntags:\n  - Education\n  - \"Person: Bart\"\npaperless_id: 72\nsource: paperless\n"
             "timestamp: \"2026-10-11T14:00:39Z\"\n---\n\n# Springfield Elementary School Notice\n\n"
             "> **From:** [Springfield Elementary School](../../../correspondents/springfield-elementary-school.md)"
             " · **About:** [Bart](../../../../bart/about.md), [Mr. Burns](../../../../mr/about.md)\n\n"
             "> [!summary]\n> A field trip. Bart's page is [here](../../../../bart/about.md).\n"),
    TAB: ("---\ntype: document\ncard_id: document-0b6e2d91aa\ntitle: Tab\ncorrespondent: Moe's Tavern\n"
          "paperless_id: 31\nsource: paperless\ntimestamp: \"2026-10-13T20:00:00Z\"\n---\n\n# Tab\n\n"
          "> **From:** [Moe's Tavern](../../../correspondents/moe-s-tavern.md)\n"),
    NOTE: ("---\ntype: note\ncard_id: note-7f3a9c21e4\ntitle: Pick up Grampa\npersons:\n  - Grampa\n"
           "tags:\n  - itchy-scratchy-land\n  - \"Person: Grampa\"\ntimestamp: \"2026-10-13T21:12:08Z\"\n---\n\n"
           "# Pick up Grampa\n\n> **About** [Grampa](../../../../../grampa/about.md)\n"
           "> **Captured** 2026-10-13 · **Kind** note\n\nBefore we leave.  \nTrailing spaces stay.\n"),
    # Signed with a digest: an edit from outside the compile would make it the family's.
    DIARY: ("---\ntype: diary\ncard_id: diary-3f9a0c1b2d\ntitle: Bart's first skateboard\ndate: \"2024-03-09\"\n"
            "persons:\n  - Bart\ntags:\n  - \"Person: Bart\"\nentry_id: b48902f594\n"
            "timestamp: \"2024-03-09T18:00:00.000Z\"\ndigest: 0123456789abcdef\n---\n\n# Bart's first skateboard\n"),
    # Items that link the letter under the title it had before a refile,
    # and one that names a document no file answers to.
    LIST: ("---\ntype: list\ncard_id: list-education\ntitle: Education\n---\n\n"
           "- [ ] Pay the trip fee [Permission Slip](../documents/2026/10/2026-10-09-permission-slip-p72.md)\n"
           "- [ ] Find the report card [Report Card](../documents/2026/06/2026-06-20-report-card-p99.md)\n"),
    # A correspondent the family wrote in the shape from before cards.
    KRUSTY: "---\nkind: correspondent\ncanonical: Krusty Burger\n---\n\nThe kids' favourite.\n",
    # One declared already: never rewritten.
    MOE: ("---\ntype: correspondent\ncard_id: correspondent-moe-s-tavern\ntitle: Moe's Tavern\n"
          "aliases:\n  - Moe's\n---\n\n# Moe's Tavern\n\nHomer's second home.\n"),
}


# ── The memory ───────────────────────────────────────────────────────────

@pytest.fixture
def memory(tmp_path) -> Iterator[RunningMemory]:
    """The memory, holding the fabricated vault, its data dir `tmp_path / "data"`."""
    with running_memory(tmp_path / "data", VAULT, seeded_by="marge",
                        seeded_as="seed the vault") as running:
        yield running


# ── Migration 0003: people by id ─────────────────────────────────────────

class TestPeopleByIdOnce:
    """The first run: one commit by the memory bot, with the trailer."""

    TRAILER = "Migration: memory/0003"

    @pytest.fixture
    def found(self, memory):
        found, sha = name_people_by_id(memory.api, memory.vault, trailer=self.TRAILER)
        assert sha == memory.forgejo.head()
        return found

    def test_one_commit_by_the_memory_bot(self, found, memory):
        assert len(memory.written) == 1
        assert git(memory.forgejo.repo, "log", "-1", "--format=%an <%ae>|%B").strip() == \
            f"{BOT_USERNAME} <{BOT_EMAIL}>|cards: name people by id in 3 card(s)\n\n{self.TRAILER}"

    def test_a_name_a_person_goes_by_becomes_their_id(self, found, memory):
        assert parse(memory.page(LETTER))["persons"] == ["bart", "Mr. Burns"]
        assert parse(memory.page(NOTE))["persons"] == ["abraham"]  # Grampa is Abraham

    def test_a_link_to_a_compiled_page_links_the_persons_card(self, found, memory):
        assert "> **About** [Grampa](../../../../people/abraham.md)\n" in memory.page(NOTE)
        assert " · **About:** [Bart](../../../people/bart.md), " in memory.page(LETTER)

    def test_person_tags_go(self, found, memory):
        assert parse(memory.page(LETTER))["tags"] == ["Education"]
        assert parse(memory.page(NOTE))["tags"] == ["itchy-scratchy-land"]

    def test_a_name_nobody_goes_by_stays_with_its_link_and_is_reported(self, found, memory):
        assert "[Mr. Burns](../../../../mr/about.md)" in memory.page(LETTER)
        assert f"{LETTER}: no declared person goes by 'Mr. Burns'" in found.left

    def test_a_link_to_a_moved_document_follows_it_by_its_paperless_id(self, found, memory):
        assert ("[Permission Slip](../documents/2026/10/"
                "2026-10-09-springfield-elementary-school-notice-p72.md)") in memory.page(LIST)

    def test_a_link_no_one_document_answers_is_reported_and_kept(self, found, memory):
        assert "(../documents/2026/06/2026-06-20-report-card-p99.md)" in memory.page(LIST)
        assert any(line.startswith(f"{LIST}: ../documents/2026/06/") for line in found.left)

    def test_only_the_lines_it_is_about_change(self, found, memory):
        # Outside the meta block a link to a compiled page is the family's words.
        assert "Bart's page is [here](../../../../bart/about.md)." in memory.page(LETTER)
        assert memory.page(NOTE).endswith("\n\nBefore we leave.  \nTrailing spaces stay.\n")
        for path in (DIARY, TAB, MOE, KRUSTY, HOMER):
            assert memory.page(path) == VAULT[path], path

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory):
        head = memory.forgejo.head()
        again, sha = name_people_by_id(memory.api, memory.vault, trailer=self.TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert len(memory.written) == 1 and memory.forgejo.head() == head


# ── Migration 0004: correspondents and topics ────────────────────────────

class TestDeclareCardsOnce:
    TRAILER = "Migration: memory/0004"

    @pytest.fixture
    def found(self, memory):
        found, sha = declare_cards(memory.api, memory.vault, trailer=self.TRAILER)
        assert sha == memory.forgejo.head()
        return found

    def test_one_commit_by_the_memory_bot(self, found, memory):
        assert len(memory.written) == 1
        assert git(memory.forgejo.repo, "log", "-1", "--format=%an <%ae>|%B").strip() == \
            f"{BOT_USERNAME} <{BOT_EMAIL}>|cards: declare 3 correspondent and topic card(s)\n\n{self.TRAILER}"

    def test_a_correspondent_a_document_names_gets_its_card(self, found, memory):
        assert parse(memory.page(SCHOOL)) == {
            "type": "correspondent", "card_id": "correspondent-springfield-elementary-school",
            "title": "Springfield Elementary School"}

    def test_a_topic_folder_gets_its_card(self, found, memory):
        assert parse(memory.page(TOPIC)) == {
            "type": "topic", "card_id": "topic-itchy-scratchy-land", "title": "Itchy Scratchy Land"}

    def test_a_declared_correspondent_is_never_rewritten(self, found, memory):
        assert memory.page(MOE) == VAULT[MOE]

    def test_an_old_correspondent_file_gets_its_type_and_id_and_nothing_else(self, found, memory):
        assert memory.page(KRUSTY) == ("---\ntype: correspondent\ncard_id: correspondent-krusty-burger\n"
                                         + VAULT[KRUSTY][len("---\n"):])

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory):
        head = memory.forgejo.head()
        again, sha = declare_cards(memory.api, memory.vault, trailer=self.TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert len(memory.written) == 1 and memory.forgejo.head() == head


class TestStackUpMemory:
    """Both migrations, run by the framework's runner the way `stack up memory`
    runs it: each reads the curator's clone and makes one commit through it."""

    def test_each_runs_once_and_records_its_commit(self, memory):
        from stack.migrations import MigrationContext, Migrations

        # 0001 finds no old todo lists to move; 0002 finds every card with an id.
        stck = memory.stack
        migrations = Migrations("memory", REPO / "stacklets" / "memory", stck.data / "memory")
        outcome = migrations.run(
            lambda m: MigrationContext(m, stack=stck, stacklet_id="memory", env={}),
            lambda msg: None)

        assert outcome["applied"][:4] == ["memory/0001", "memory/0002", "memory/0003", "memory/0004"]
        log = git(memory.forgejo.repo, "log", "--format=%H %(trailers:key=Migration,valueonly)").splitlines()
        commits = dict(reversed(line.split()) for line in log if len(line.split()) == 2)
        record = migrations.record().applied
        assert record["0003"]["commit"] == commits["memory/0003"]
        assert record["0004"]["commit"] == commits["memory/0004"]
        assert parse(memory.page(NOTE))["persons"] == ["abraham"]
        assert memory.forgejo.file(TOPIC) is not None


def test_a_card_that_appeared_meanwhile_makes_the_curator_refuse_the_commit(memory):
    """Given Marge declaring the topic in Forgejo after the migration read the
    clone, when the migration writes, then the curator refuses it and the
    card Marge wrote stays; the next `stack up memory` reads again."""
    marges = "---\ntype: topic\ntitle: Itchy & Scratchy Land\n---\n"
    memory.edited_in_forgejo({TOPIC: marges}, subject="Marge declared the topic", pulled=False)

    with pytest.raises(Refused):
        declare_cards(memory.api, memory.vault)
    assert memory.page(TOPIC) == marges
    assert re.search(r"Itchy & Scratchy", memory.page(TOPIC))
