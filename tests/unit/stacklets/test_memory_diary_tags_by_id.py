"""A diary card tags a topic by its id: migration memory/0010.

ADR-017 §4: `tags` holds the ontology's topic ids (`medical`) and
keywords, on a diary card as on every other card. The label a person
reads comes from the ontology, in the household's language. The diary
compile writes new cards that way; this migration brings the cards it
wrote before, tagged by the topic's label (`Gesundheit`), to the same
shape with the same rule (`stack.tags.tag_for`).

A diary card is signed with a digest of its text (`stack.digest`). A card
the compile still owns is signed again, so it is byte for byte what the
compile writes today and the next compile finds nothing to rewrite. A
card a person changed has its tags rewritten too and stays theirs: it is
not signed.

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
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(Path(__file__).parent))

import diary_card  # noqa: E402
from lib import BOT_USERNAME, tag_diary_topics_by_id  # noqa: E402
from stack.frontmatter import parse  # noqa: E402
from test_memory_card_links import CONTENTS, _clone, _commits, _git, _read, forgejo  # noqa: E402, F401

# The family's ontology. It has no topic for family life: a card an older
# ontology tagged with one keeps it as a keyword.
ONTOLOGY = """\
[topic.medical]
names = { de = "Gesundheit", en = "Medical" }

[topic.education]
names = { de = "Bildung", en = "Education" }
"""


def _card(n: int, tags: list[str]) -> diary_card.Card:
    return diary_card.Card(
        entry_id=f"b48902f59{n}", on=date(2024, 3, n + 1), confidence="sent", kind="voice",
        sender="marge", at=1709985600000 + n, event_ids=[f"$event{n}"],
        media=[f"/media/2024/03/event{n}.m4a"], body="Bart fell off his skateboard.",
        title=f"Skateboard {n}", summary="Marge tells how Bart hurt his knee.",
        persons=["bart"], tags=tags, card_id=f"diary-3f9a0c1b{n}0")


def _path(card: diary_card.Card) -> str:
    return diary_card.card_path(card, bucket="family")


# As the compile wrote them before ids: the topic's label in the household's language.
GERMAN = _card(1, ["Gesundheit"])
ENGLISH = _card(2, ["Medical", "Education"])
# A topic this ontology does not have.
FAMILY_LIFE = _card(3, ["Gesundheit", "Family Life"])
# Marge fixed a word in Forgejo after the compile signed it: hers now.
EDITED = _card(4, ["Gesundheit"])
EDITED_TEXT = diary_card.render(EDITED).replace("his knee", "his left knee")
# Corrected through a reply in the chat before people had ids: written
# unsigned, the family's by design, and it kept its `Person:` tag (0007).
CORRECTED = _card(5, ["Bildung", "Person: Bart"])
# Written by the compile today.
TODAY = _card(6, ["medical"])
# Not a diary card: its tags are migration 0005's.
NOTE = "family/notes/2024/03/knee.md"
NOTE_TEXT = ("---\ntype: note\ncard_id: note-0c37f8a1b2\ntitle: Knee\ntags:\n  - Gesundheit\n"
             "timestamp: \"2024-03-09T12:00:00Z\"\n---\n\n# Knee\n")

# Bart's person file: without one, migration 0007 stops the chain before 0010.
BART = "family/people/bart.md"

VAULT = {
    "ontology.toml": ONTOLOGY,
    BART: "---\ntype: person\ncard_id: person-bart\ntitle: Bart Simpson\n---\n\n# Bart Simpson\n",
    _path(GERMAN): diary_card.render(GERMAN),
    _path(ENGLISH): diary_card.render(ENGLISH),
    _path(FAMILY_LIFE): diary_card.render(FAMILY_LIFE),
    _path(EDITED): EDITED_TEXT,
    _path(CORRECTED): diary_card.render(CORRECTED, family_owned=True),
    _path(TODAY): diary_card.render(TODAY),
    NOTE: NOTE_TEXT,
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


class TestDiaryTopicsByIdOnce:
    TRAILER = "Migration: memory/0010"

    @pytest.fixture
    def found(self, memory, forgejo, tmp_path):  # noqa: F811
        found, sha = tag_diary_topics_by_id(forgejo, _clone(memory, tmp_path, "run1"),
                                            trailer=self.TRAILER)
        assert sha == _git(memory, "rev-parse", "HEAD")
        return found

    def test_one_commit_by_the_memory_bot(self, found, memory, httpserver):
        assert _commits(httpserver) == 1
        assert _git(memory, "log", "-1", "--format=%an|%B").strip() == \
            f"{BOT_USERNAME}|cards: tag topics by id in 5 diary card(s)\n\n{self.TRAILER}"

    def test_a_card_the_compile_owns_is_what_the_compile_writes_today(self, found, memory):
        """A label in any language becomes the topic's id, and the card is
        signed again: the next compile finds nothing to rewrite."""
        for card, ids in ((GERMAN, ["medical"]), (ENGLISH, ["medical", "education"])):
            text = _read(memory, _path(card))
            assert text == diary_card.render(replace(card, tags=ids)), _path(card)
            assert not diary_card.edited_by_hand(text)

    def test_a_topic_the_ontology_does_not_know_stays_as_the_tag_rule_writes_it(self, found, memory):
        text = _read(memory, _path(FAMILY_LIFE))
        assert text == diary_card.render(replace(FAMILY_LIFE, tags=["medical", "family-life"]))

    def test_a_card_a_person_changed_gets_ids_and_stays_theirs(self, found, memory):
        """Its tags are ids like every other card's, and it is not signed:
        the compile still leaves it alone."""
        edited = _read(memory, _path(EDITED))
        assert edited == EDITED_TEXT.replace("\n  - Gesundheit\n", "\n  - medical\n")
        assert diary_card.edited_by_hand(edited)

        corrected = _read(memory, _path(CORRECTED))
        assert parse(corrected)["tags"] == ["education", "Person: Bart"]
        assert "digest" not in parse(corrected)

    def test_a_tag_no_rule_reads_stays_and_is_reported(self, found, memory):
        assert found.left == [f"{_path(CORRECTED)}: tag 'Person: Bart' is no topic and no keyword"]

    def test_a_card_in_this_shape_and_every_other_card_are_left_alone(self, found, memory):
        for path in (_path(TODAY), NOTE, BART, "ontology.toml"):
            assert _read(memory, path) == VAULT[path], path

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory, forgejo,  # noqa: F811
                                                          httpserver, tmp_path):
        head = _git(memory, "rev-parse", "HEAD")
        again, sha = tag_diary_topics_by_id(forgejo, _clone(memory, tmp_path, "run2"),
                                            trailer=self.TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert _commits(httpserver) == 1 and _git(memory, "rev-parse", "HEAD") == head


def test_stack_up_memory_runs_it_once_and_records_its_commit(memory, forgejo, tmp_path,  # noqa: F811
                                                             httpserver):
    """`0010_tag_diary_topics_by_id.py`, run by the framework's runner the
    way `stack up memory` runs it: a fresh clone over HTTP, one commit."""
    from stack import Stack
    from stack.migrations import MigrationContext, Migrations
    from stack.output import CollectorOutput

    # 0001 finds no old todo lists to move.
    httpserver.expect_request(f"{CONTENTS}/family", method="GET").respond_with_json([])
    stck = Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput())
    stck.secrets.set("memory", "MEMORY_BOT_TOKEN", "bottoken")
    migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory", tmp_path / "data" / "memory")
    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=stck, stacklet_id="memory",
                                   env={"CODE_URL": httpserver.url_for("")}),
        lambda msg: None)

    assert "memory/0010" in outcome["applied"], outcome
    assert migrations.record().applied["0010"]["commit"] == _git(
        memory, "log", "-1", "--format=%H", "--grep=Migration: memory/0010")
    assert parse(_read(memory, _path(GERMAN)))["tags"] == ["medical"]
