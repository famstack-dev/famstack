"""A diary card names its people by id: migration memory/0007.

ADR-017 §4: `persons` holds the ids of declared people and there are no
`Person: <name>` tags, on a diary card as on every other record. The
diary compile writes new cards that way; this migration brings the
cards written before to the same shape.

A diary card is signed with a digest of its text (`stack.digest`). The
migration changes only the cards the compile still owns and signs them
again, so each is byte for byte what the compile writes today and the
next compile finds nothing to rewrite. A card a person changed is theirs:
it is left as it is and reported.

The memory is memory's test instance (`running_memory`): Forgejo, a bare
repository, and the curator in front of it, which commits each change and
pushes it, so a second run reads what the first one wrote.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from stacklets.memory.bot import diary_card  # noqa: E402
from stacklets.memory.lib import BOT_USERNAME, name_diary_people_by_id  # noqa: E402
from stack.frontmatter import parse  # noqa: E402
from tests.memory.unit.instance import RunningMemory, clone_of, git, running_memory  # noqa: E402


def _person(title: str, pid: str, *aliases: str) -> str:
    names = "".join(f"  - {a}\n" for a in aliases)
    return (f"---\ntype: person\ncard_id: person-{pid}\ntitle: {title}\n"
            + (f"aliases:\n{names}" if names else "") + f"---\n\n# {title}\n")


def _card(n: int, persons: list[str], tags: list[str]) -> diary_card.Card:
    return diary_card.Card(
        entry_id=f"b48902f59{n}", on=date(2024, 3, n + 1), confidence="sent", kind="voice",
        sender="marge", at=1709985600000 + n, event_ids=[f"$event{n}"],
        media=[f"/media/2024/03/event{n}.m4a"], body="Bart rode his skateboard to school.",
        title=f"Skateboard {n}", summary="Marge tells how Bart learned to skate.",
        persons=persons, tags=tags, card_id=f"diary-3f9a0c1b{n}0")


def _path(card: diary_card.Card) -> str:
    return diary_card.card_path(card, bucket="family")


# As the compile wrote them before ids: first names in `persons`, a tag per person.
ABOUT_BOTH = _card(1, ["Bart", "Grampa"], ["Education", "Person: Bart", "Person: Grampa"])
STRANGER = _card(2, ["Bart", "Mr. Burns"], ["Person: Bart", "Person: Mr. Burns"])
ONLY_PEOPLE_TAGS = _card(3, ["Marge"], ["Person: Marge"])
# Marge fixed a word in Forgejo after the compile signed it: hers now.
EDITED = _card(4, ["Bart"], ["Person: Bart"])
EDITED_TEXT = diary_card.render(EDITED).replace("to school", "to Springfield Elementary")
# Corrected through a reply in the chat: written unsigned, the family's by design.
CORRECTED = _card(5, ["Marge"], ["Person: Marge"])
# Written by the compile today.
TODAY = _card(6, ["bart"], ["Education"])

VAULT = {
    "family/people/bart.md": _person("Bart Simpson", "bart"),
    "family/people/marge.md": _person("Marge Simpson", "marge"),
    "family/people/abraham.md": _person("Abraham Simpson", "abraham", "Grampa"),
    _path(ABOUT_BOTH): diary_card.render(ABOUT_BOTH),
    _path(STRANGER): diary_card.render(STRANGER),
    _path(ONLY_PEOPLE_TAGS): diary_card.render(ONLY_PEOPLE_TAGS),
    _path(EDITED): EDITED_TEXT,
    _path(CORRECTED): diary_card.render(CORRECTED, family_owned=True),
    _path(TODAY): diary_card.render(TODAY),
}


@pytest.fixture
def memory(tmp_path) -> Iterator[RunningMemory]:
    """The memory, holding the fabricated vault, its data dir `tmp_path / "data"`."""
    with running_memory(tmp_path / "data", VAULT, seeded_by="marge",
                        seeded_as="seed the vault") as running:
        yield running


class TestDiaryPeopleByIdOnce:
    TRAILER = "Migration: memory/0007"

    @pytest.fixture
    def found(self, memory, tmp_path):
        found, sha = name_diary_people_by_id(memory.api, memory.vault,
                                             trailer=self.TRAILER)
        assert sha == memory.forgejo.head()
        return found

    def test_one_commit_by_the_memory_bot(self, found, memory):
        assert len(memory.written) == 1
        assert git(memory.forgejo.repo, "log", "-1", "--format=%an|%B").strip() == \
            f"{BOT_USERNAME}|cards: name people by id in 3 diary card(s)\n\n{self.TRAILER}"

    def test_a_card_the_compile_owns_is_what_the_compile_writes_today(self, found, memory):
        """Ids by any name a person goes by, no `Person:` tags, signed again:
        the next compile finds nothing to rewrite."""
        text = memory.page(_path(ABOUT_BOTH))
        assert text == diary_card.render(replace(
            ABOUT_BOTH, persons=["bart", "abraham"], tags=["Education"]))
        assert not diary_card.edited_by_hand(text)

    def test_a_card_whose_only_tags_named_people_has_no_tags(self, found, memory):
        assert memory.page(_path(ONLY_PEOPLE_TAGS)) == diary_card.render(
            replace(ONLY_PEOPLE_TAGS, persons=["marge"], tags=[]))

    def test_a_name_nobody_goes_by_stays_and_is_reported(self, found, memory):
        text = memory.page(_path(STRANGER))
        assert parse(text)["persons"] == ["bart", "Mr. Burns"] and "tags" not in parse(text)
        assert not diary_card.edited_by_hand(text)
        assert f"{_path(STRANGER)}: no declared person goes by 'Mr. Burns'" in found.left

    def test_a_card_a_person_changed_is_left_alone_and_reported(self, found, memory):
        assert memory.page(_path(EDITED)) == EDITED_TEXT
        assert memory.page(_path(CORRECTED)) == VAULT[_path(CORRECTED)]
        for card in (EDITED, CORRECTED):
            assert f"{_path(card)}: a person changed it, so it keeps its names and tags" in found.left

    def test_what_is_left_is_what_it_reported(self, found, memory):
        """The gauge: no `Person:` tag and no name in place of an id on a diary
        card, apart from the ones it named."""
        ids = {"bart", "marge", "abraham"}
        left = {line.split(": ", 1)[0] for line in found.left}
        for path in VAULT:
            fields = parse(memory.page(path))
            if fields.get("type") != "diary":
                continue
            names = [p for p in fields.get("persons") or [] if p not in ids]
            person_tags = [t for t in fields.get("tags") or [] if t.startswith("Person:")]
            assert not (names or person_tags) or path in left, path
        assert left == {_path(STRANGER), _path(EDITED), _path(CORRECTED)}

    def test_a_card_in_this_shape_is_left_alone(self, found, memory):
        assert memory.page(_path(TODAY)) == VAULT[_path(TODAY)]

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory,
                                                          tmp_path):
        head = memory.forgejo.head()
        again, sha = name_diary_people_by_id(memory.api, memory.vault,
                                             trailer=self.TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert len(memory.written) == 1 and memory.forgejo.head() == head


def test_it_waits_for_the_person_files(memory, tmp_path):
    """Without them no name can become an id; the next `stack up memory` tries again."""
    clone = clone_of(memory.forgejo, tmp_path / "run")
    for person in ("bart", "marge", "abraham"):
        (clone / "family" / "people" / f"{person}.md").unlink()
    git(clone, "commit", "-qam", "no people yet")
    with pytest.raises(RuntimeError, match="no person files"):
        name_diary_people_by_id(memory.api, clone)


def test_stack_up_memory_runs_it_once_and_records_its_commit(memory, tmp_path):
    """`0007_name_diary_people_by_id.py`, run by the framework's runner the
    way `stack up memory` runs it: through the curator, one commit."""
    from stack.migrations import MigrationContext, Migrations

    stck = memory.stack
    migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory", tmp_path / "data" / "memory")
    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=stck, stacklet_id="memory",
                                   env={}),
        lambda msg: None)

    assert "memory/0007" in outcome["applied"]
    assert migrations.record().applied["0007"]["commit"] == git(
        memory.forgejo.repo, "log", "-1", "--format=%H", "--grep=Migration: memory/0007")
    assert parse(memory.page(_path(ABOUT_BOTH)))["persons"] == ["bart", "abraham"]
