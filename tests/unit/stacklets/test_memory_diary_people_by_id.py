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

The memory repo is a real git repository and Forgejo a real HTTP server
that applies each commit to it (`test_memory_card_links`), so a second
run reads what the first one wrote.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).parent))

from stacklets.memory.bot import diary_card  # noqa: E402
from stacklets.memory.lib import BOT_USERNAME, name_diary_people_by_id  # noqa: E402
from stack.frontmatter import parse  # noqa: E402
from test_memory_card_links import _clone, _commits, _git, _read, _instance, curator# noqa: E402, F401


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
def memory(tmp_path) -> Path:
    """The family/memory repository on Forgejo, holding the fabricated vault."""
    repo = tmp_path / "forgejo" / "memory"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    for path, text in VAULT.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed the vault")
    _git(repo, "update-server-info")
    return repo


class TestDiaryPeopleByIdOnce:
    TRAILER = "Migration: memory/0007"

    @pytest.fixture
    def found(self, memory, curator, tmp_path):  # noqa: F811
        found, sha = name_diary_people_by_id(curator.api, curator.clone,
                                             trailer=self.TRAILER)
        assert sha == _git(memory, "rev-parse", "HEAD")
        return found

    def test_one_commit_by_the_memory_bot(self, found, memory):
        assert _commits(memory) == 1
        assert _git(memory, "log", "-1", "--format=%an|%B").strip() == \
            f"{BOT_USERNAME}|cards: name people by id in 3 diary card(s)\n\n{self.TRAILER}"

    def test_a_card_the_compile_owns_is_what_the_compile_writes_today(self, found, memory):
        """Ids by any name a person goes by, no `Person:` tags, signed again:
        the next compile finds nothing to rewrite."""
        text = _read(memory, _path(ABOUT_BOTH))
        assert text == diary_card.render(replace(
            ABOUT_BOTH, persons=["bart", "abraham"], tags=["Education"]))
        assert not diary_card.edited_by_hand(text)

    def test_a_card_whose_only_tags_named_people_has_no_tags(self, found, memory):
        assert _read(memory, _path(ONLY_PEOPLE_TAGS)) == diary_card.render(
            replace(ONLY_PEOPLE_TAGS, persons=["marge"], tags=[]))

    def test_a_name_nobody_goes_by_stays_and_is_reported(self, found, memory):
        text = _read(memory, _path(STRANGER))
        assert parse(text)["persons"] == ["bart", "Mr. Burns"] and "tags" not in parse(text)
        assert not diary_card.edited_by_hand(text)
        assert f"{_path(STRANGER)}: no declared person goes by 'Mr. Burns'" in found.left

    def test_a_card_a_person_changed_is_left_alone_and_reported(self, found, memory):
        assert _read(memory, _path(EDITED)) == EDITED_TEXT
        assert _read(memory, _path(CORRECTED)) == VAULT[_path(CORRECTED)]
        for card in (EDITED, CORRECTED):
            assert f"{_path(card)}: a person changed it, so it keeps its names and tags" in found.left

    def test_what_is_left_is_what_it_reported(self, found, memory):
        """The gauge: no `Person:` tag and no name in place of an id on a diary
        card, apart from the ones it named."""
        ids = {"bart", "marge", "abraham"}
        left = {line.split(": ", 1)[0] for line in found.left}
        for path in VAULT:
            fields = parse(_read(memory, path))
            if fields.get("type") != "diary":
                continue
            names = [p for p in fields.get("persons") or [] if p not in ids]
            person_tags = [t for t in fields.get("tags") or [] if t.startswith("Person:")]
            assert not (names or person_tags) or path in left, path
        assert left == {_path(STRANGER), _path(EDITED), _path(CORRECTED)}

    def test_a_card_in_this_shape_is_left_alone(self, found, memory):
        assert _read(memory, _path(TODAY)) == VAULT[_path(TODAY)]

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory, curator,  # noqa: F811
                                                          tmp_path):
        head = _git(memory, "rev-parse", "HEAD")
        again, sha = name_diary_people_by_id(curator.api, curator.clone,
                                             trailer=self.TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert _commits(memory) == 1 and _git(memory, "rev-parse", "HEAD") == head


def test_it_waits_for_the_person_files(memory, curator, tmp_path):  # noqa: F811
    """Without them no name can become an id; the next `stack up memory` tries again."""
    clone = _clone(memory, tmp_path, "run")
    for person in ("bart", "marge", "abraham"):
        (clone / "family" / "people" / f"{person}.md").unlink()
    _git(clone, "commit", "-qam", "no people yet")
    with pytest.raises(RuntimeError, match="no person files"):
        name_diary_people_by_id(curator.api, clone)


def test_stack_up_memory_runs_it_once_and_records_its_commit(memory, curator, tmp_path):  # noqa: F811
    """`0007_name_diary_people_by_id.py`, run by the framework's runner the
    way `stack up memory` runs it: through the curator, one commit."""
    from stack.migrations import MigrationContext, Migrations

    stck = _instance(tmp_path, curator)
    migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory", tmp_path / "data" / "memory")
    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=stck, stacklet_id="memory",
                                   env={}),
        lambda msg: None)

    assert "memory/0007" in outcome["applied"]
    assert migrations.record().applied["0007"]["commit"] == _git(
        memory, "log", "-1", "--format=%H", "--grep=Migration: memory/0007")
    assert parse(_read(memory, _path(ABOUT_BOTH)))["persons"] == ["bart", "abraham"]
