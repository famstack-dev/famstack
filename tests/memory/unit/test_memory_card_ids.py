"""Every card in the memory repo gets an id of its own: migration memory/0002
and the curator.

The migration gives one to every card that has none (`give_card_ids`), the
curator to every file pushed without one (`curator.give_ids_since`), and
both run one pass over the curator's clone of the repo (`card_ids_for`)
and write through its card store. The memory is memory's test instance
(`running_memory`) holding a small fabricated vault: Forgejo, a bare
repository, and the curator in front of it, which refuses a file whose
version changed since it was read. So a second run reads what the first
one wrote, as it would on an instance.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from stack.frontmatter import parse
from stacklets.memory.lib import BOT_USERNAME, give_card_ids
from stacklets.memory.memory_api import Refused
from tests.memory.unit.instance import RunningMemory, git, running_memory

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

TRAILER = "Migration: memory/0002"

DOCUMENT = "family/documents/1989/12/1989-12-17-christmas-card-p12.md"
NOTE = "marge/notes/2026/10/art-supplies-for-next-time-5f9740.md"
DIARY = "family/diary/entries/2024/03/2024-03-09-b48902f594.md"
PERSON = "family/people/homer.md"
LIST = "family/lists/markt.md"
ARCHIVE = "family/lists/archive/markt/2026-10.md"
BY_HAND = "homer/donut-shops.md"
ODD = "family/recipes/pretzels.md"

VAULT = {
    "README.md": "# Memory\n\nThe family's memory.\n",
    "index.md": "---\ntitle: Family Memory\n---\n\n# Family Memory\n",
    "family/correspondents/README.md": "# Correspondents\n",
    DOCUMENT: ("---\ntype: document\ntitle: Christmas card from Patty and Selma\n"
               "paperless_id: 12\nsource: paperless\ntimestamp: \"2026-10-01T10:00:00Z\"\n---\n\n"
               "# Christmas card from Patty and Selma\n\nSeason's greetings.\n"),
    NOTE: ("---\ntype: note\ntitle: Art supplies for next time\n"
           "capture_id: $r-PlkCYBsNEjgZDfzCpcAoDaY66uCHNc7tJjDWrY8P4\n"
           "timestamp: \"2026-10-13T21:12:08Z\"\n---\n\n# Art supplies for next time\n\n"
           "Brushes and paper.  \nTrailing spaces stay.\n"),
    DIARY: ("---\ntype: diary\ntitle: Bart's first skateboard\ndate: \"2024-03-09\"\n"
            "entry_id: b48902f594\ntimestamp: \"2024-03-09T18:00:00.000Z\"\n---\n\n"
            "# Bart's first skateboard\n"),
    PERSON: "---\ntype: person\ntitle: Homer Simpson\naccount: homer\n---\n\n# Homer Simpson\n",
    LIST: "---\ntype: list\ntitle: Markt\n---\n\n- [ ] Äpfel\n",
    ARCHIVE: "---\ntype: list\ntitle: Markt, 2026-10\n---\n\n- [x] Milch ✅ 2026-10-02\n",
    BY_HAND: "Lard Lad, Springfield Mall.\n\n## Donut shops worth the drive\n\n- Lard Lad\n",
    ODD: "---\ntype: recipe\ntitle: Pretzels\n---\n\nKnot them.\n",
}

NO_CARDS = ("README.md", "index.md", "family/correspondents/README.md")


# ── The memory ───────────────────────────────────────────────────────────

# When the vault was seeded; a test's own pushes come after it.
SEEDED = "2026-10-01T10:00:00"


@pytest.fixture
def memory(tmp_path) -> Iterator[RunningMemory]:
    """The memory, holding the fabricated vault, its data dir `tmp_path / "data"`."""
    with running_memory(tmp_path / "data", VAULT, seeded_by="marge", seeded_as="seed the vault",
                        seeded_at=SEEDED) as running:
        yield running


def _push(memory: RunningMemory, files: dict[str, str], message: str, *,
          when: str | None = None, removed: tuple[str, ...] = ()) -> str:
    """A commit as a person pushes it, from Obsidian or by hand, which the
    curator has not pulled yet; the commit Forgejo is at."""
    return memory.edited_in_forgejo(files, removed=removed, by="marge", subject=message, at=when,
                                    pulled=False)


def _pulled(memory: RunningMemory) -> Path:
    """The curator's clone, brought up to Forgejo first, as the migration
    reads it (`memory_for_migration`)."""
    memory.api.sync()
    return memory.vault


def _card_id(memory: RunningMemory, path: str) -> str:
    return str(parse(memory.page(path)).get("card_id") or "")


# ── Migration 0002 ───────────────────────────────────────────────────────

class TestTheMigration:
    def test_gives_every_card_an_id_in_one_commit_by_the_memory_bot(
            self, memory, tmp_path):
        found, sha = give_card_ids(memory.api, _pulled(memory), trailer=TRAILER)
        assert sha == memory.forgejo.head() and len(memory.written) == 1
        assert git(memory.forgejo.repo, "log", "-1", "--format=%an|%B").strip() == \
            f"{BOT_USERNAME}|cards: give 7 card(s) an id of their own\n\n{TRAILER}"

        for path, card_type in ((DOCUMENT, "document"), (NOTE, "note"), (DIARY, "diary")):
            assert re.fullmatch(rf"{card_type}-[0-9a-f]{{10}}", _card_id(memory, path)), path
        assert _card_id(memory, PERSON) == "person-homer"
        assert _card_id(memory, LIST) == "list-markt"
        assert _card_id(memory, ARCHIVE) == "list-markt-2026-10"
        assert found.left == [f"{ODD}: `type: recipe` is no card type"]

    def test_adds_only_the_id_line_and_leaves_every_other_byte(self, memory, tmp_path):
        give_card_ids(memory.api, _pulled(memory), trailer=TRAILER)
        for path in (DOCUMENT, NOTE, DIARY, PERSON, LIST, ARCHIVE):
            after = memory.page(path)
            assert after.replace(f"card_id: {_card_id(memory, path)}\n", "", 1) == VAULT[path]

    def test_leaves_navigation_pages_and_what_it_cannot_place_alone(
            self, memory, tmp_path):
        give_card_ids(memory.api, _pulled(memory), trailer=TRAILER)
        for path in (*NO_CARDS, ODD):
            assert memory.page(path) == VAULT[path]

    def test_makes_a_file_written_by_hand_a_note(self, memory, tmp_path):
        give_card_ids(memory.api, _pulled(memory), trailer=TRAILER)
        after = memory.page(BY_HAND)
        fields = parse(after)
        assert (fields["type"], fields["title"]) == ("note", "Donut shops worth the drive")
        assert re.fullmatch(r"note-[0-9a-f]{10}", fields["card_id"])
        assert after.endswith(f"---\n{VAULT[BY_HAND]}")

    def test_run_again_changes_nothing_and_makes_no_commit(
            self, memory, tmp_path):
        give_card_ids(memory.api, _pulled(memory), trailer=TRAILER)
        head = memory.forgejo.head()
        found, sha = give_card_ids(memory.api, _pulled(memory), trailer=TRAILER)
        assert (found.changes, sha) == ({}, None)
        assert len(memory.written) == 1 and memory.forgejo.head() == head

    def test_writes_nothing_over_an_edit_pushed_after_it_read(self, memory):
        """The curator refuses the commit; the next `stack up memory` reads again."""
        stale = memory.vault
        edited = VAULT[NOTE] + "Marge added a line meanwhile.\n"
        head = _push(memory, {NOTE: edited}, "edit the note")
        with pytest.raises(Refused):
            give_card_ids(memory.api, stale, trailer=TRAILER)
        assert memory.forgejo.head() == head
        assert memory.page(NOTE) == edited


class TestStackUpMemory:
    """`0002_give_every_card_an_id.py`, run by the framework's runner the way
    `stack up memory` runs it: through the curator, one commit."""

    def _up(self, memory):
        from stack.migrations import MigrationContext, Migrations

        migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory",
                                memory.stack.data / "memory")
        outcome = migrations.run(
            lambda m: MigrationContext(m, stack=memory.stack, stacklet_id="memory", env={}),
            lambda msg: None)
        return outcome, migrations.record()

    def test_gives_the_ids_and_records_its_commit(self, memory, tmp_path):
        # 0001 runs first and finds no old todo lists to move.
        outcome, record = self._up(memory)
        assert outcome["applied"][:2] == ["memory/0001", "memory/0002"]
        # Later migrations commit after it: its commit is the one with its trailer.
        assert record.applied["0002"]["commit"] == git(memory.forgejo.repo, "log", "-1", "--format=%H",
                                                        f"--grep={TRAILER}")
        assert _card_id(memory, PERSON) == "person-homer"


class TestTwoFilesWithOneId:
    ORIGINAL = "marge/notes/zz-original.md"
    COPY = "marge/notes/aa-copy.md"
    TEXT = "---\ntype: note\ncard_id: note-7f3a9c21e4\ntitle: Moe's opening hours\n---\n\nAlways.\n"

    def test_the_one_first_committed_later_gets_a_new_id(self, memory, tmp_path):
        _push(memory, {self.ORIGINAL: self.TEXT}, "a note", when="2026-10-02T10:00:00")
        _push(memory, {self.COPY: self.TEXT}, "copy it", when="2026-10-03T10:00:00")
        give_card_ids(memory.api, _pulled(memory), trailer=TRAILER)
        assert _card_id(memory, self.ORIGINAL) == "note-7f3a9c21e4"
        copied = _card_id(memory, self.COPY)
        assert re.fullmatch(r"note-[0-9a-f]{10}", copied) and copied != "note-7f3a9c21e4"
        assert memory.page(self.COPY) == \
            self.TEXT.replace("note-7f3a9c21e4", copied)

    def test_an_original_moved_to_a_new_name_keeps_its_age(self, memory, tmp_path):
        """The archivist renames a document when its title changes; it is still the first."""
        moved = "marge/notes/zz-original-renamed.md"
        _push(memory, {self.ORIGINAL: self.TEXT}, "a note", when="2026-10-02T10:00:00")
        _push(memory, {self.COPY: self.TEXT}, "copy it", when="2026-10-03T10:00:00")
        _push(memory, {moved: self.TEXT}, "rename", removed=(self.ORIGINAL,),
              when="2026-10-04T10:00:00")
        give_card_ids(memory.api, _pulled(memory), trailer=TRAILER)
        assert _card_id(memory, moved) == "note-7f3a9c21e4"
        assert _card_id(memory, self.COPY) != "note-7f3a9c21e4"


# ── The curator, on a push ───────────────────────────────────────────────

class TestTheCuratorLoop:
    """`curator.give_ids_since`, what the curator runs after every pull,
    writing through its card store."""

    @pytest.fixture
    def the_curator(self):
        from stacklets.memory.bot import curator as the_curator
        return the_curator

    @staticmethod
    def _store(memory: RunningMemory):
        """The curator's card store, once it pulled what Forgejo has."""
        memory.store.sync()
        return memory.store

    @pytest.fixture
    def migrated(self, memory) -> str:  # noqa: F811
        """The memory after migration 0002, the commit the curator then starts from."""
        give_card_ids(memory.api, _pulled(memory), trailer=TRAILER)
        return memory.forgejo.head()

    def test_is_done_once_the_pushed_cards_have_their_ids(self, the_curator, memory, tmp_path):
        since = memory.forgejo.head()
        _push(memory, {"marge/lisa-birthday.md": "# Ideas for Lisa's birthday\n"}, "Marge")
        assert the_curator.give_ids_since(self._store(memory), since) is True
        assert re.fullmatch(r"note-[0-9a-f]{10}", _card_id(memory, "marge/lisa-birthday.md"))
        assert git(memory.forgejo.repo, "log", "-1", "--format=%an %cn %s") == (
            "memory-bot memory-bot cards: give 1 card(s) an id of their own")

    def test_a_file_pushed_without_an_id_gets_one_and_nothing_else_does(
            self, the_curator, memory, tmp_path, migrated):
        _push(memory, {"marge/lisa-birthday.md": "# Ideas for Lisa's birthday\n\n- a reed\n",
                       ODD: VAULT[ODD] + "More salt.\n"}, "Marge's notes")
        assert the_curator.give_ids_since(self._store(memory), migrated) is True
        assert git(memory.forgejo.repo, "show", "--name-only", "--format=", "main").split() == ["marge/lisa-birthday.md"]
        assert parse(memory.page("marge/lisa-birthday.md"))["type"] == "note"
        assert git(memory.forgejo.repo, "log", "-1", "--format=%an") == BOT_USERNAME
        assert "Migration:" not in git(memory.forgejo.repo, "log", "-1", "--format=%B")

    def test_a_copied_note_gets_a_new_id_and_the_original_keeps_its(
            self, the_curator, memory, tmp_path, migrated):
        """A note in the format, as every note is once the migration chain
        has run: the curator's write is checked against the schema, which a
        migration's is not."""
        note, text = TestTwoFilesWithOneId.ORIGINAL, TestTwoFilesWithOneId.TEXT
        _push(memory, {note: text}, "a note", when="2026-10-19T10:00:00")
        _push(memory, {"marge/notes/copy.md": text}, "copy", when="2026-10-20T10:00:00")
        assert the_curator.give_ids_since(self._store(memory), migrated) is True
        assert _card_id(memory, note) == "note-7f3a9c21e4" != _card_id(memory, "marge/notes/copy.md")

    def test_a_push_of_cards_that_have_their_ids_is_left_alone(
            self, the_curator, memory, tmp_path, migrated):
        """The resting state: no commit, nothing to log."""
        edited = memory.page(NOTE) + "One more brush.\n"
        head = _push(memory, {NOTE: edited}, "Marge edits her note")
        assert the_curator.give_ids_since(self._store(memory), migrated) is True
        assert memory.forgejo.head() == head

    def test_tries_again_when_a_card_changed_after_the_pull(self, the_curator, memory, tmp_path):
        """An edit pushed after the pull: nothing is written, and the same
        range is looked at next cycle."""
        since = memory.forgejo.head()
        _push(memory, {"marge/lisa-birthday.md": "# Ideas for Lisa's birthday\n"}, "Marge")
        stale = self._store(memory)
        _push(memory, {"marge/lisa-birthday.md": "# Ideas for Lisa's birthday\n\n- a reed\n"}, "again")
        assert the_curator.give_ids_since(stale, since) is False
        assert git(memory.forgejo.repo, "log", "-1", "--format=%s", "main") == "again"
