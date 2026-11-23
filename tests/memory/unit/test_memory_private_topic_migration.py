"""The memory's migrations tag a card only with the topics its place may show: a private topic stays with its person.

Marge keeps a personal topic, "Homers 40th" (`marge/surprise-party/`), a
surprise for Homer. Two shipped migrations tagged every card against the
topics of every bucket: memory/0005 (topic ids) and memory/0011 (older
cards brought into the format). A family card that said "Homers 40th" got
`surprise-party`, which shows Marge's topic to Homer wherever the topics of
every bucket are read. An update from 0.4.x runs both again.

- The chain from 0.4.x now tags each card with its place's vocabulary
  (`stacklets.memory.place_tags.vocabularies`): the title stays a plain keyword there.
- memory/0013 repairs a memory those migrations already tagged: it takes
  another person's private topic id off every card outside that person's
  bucket. A tag a person set by hand stays, and the output names the card.

The memory is memory's test instance (`running_memory`): Forgejo, the
curator's clone, and the curator, which checks and commits each change.
The household is the Simpsons (`tests.household`).
"""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import md5
from pathlib import Path

import pytest

from stack.digest import changed_by_hand, signed
from stack.frontmatter import parse
from stack.memory import Refused
from stack.migrations import MigrationContext, Migrations
from stacklets.memory.lib import commit_found
from stacklets.memory.place_tags import private_topic_repair_for, repair_private_topic_tags
from tests.household import HOUSEHOLD_FILES
from tests.memory.unit.instance import RunningMemory, git, running_memory

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MEMORY_STACKLET = _REPO_ROOT / "stacklets" / "memory"
PRIVATE = "surprise-party"          # Marge's topic, titled "Homers 40th"
TRAILER = "Migration: memory/0013"


def _card(path: str, kind: str, title: str, tags: list[str], *, persons: tuple[str, ...] = (),
          body: str = "") -> tuple[str, str]:
    listed = "".join(f"  - {t}\n" for t in tags)
    about = "".join(f"  - {p}\n" for p in persons)
    return path, (f"---\ntype: {kind}\ncard_id: {kind}-{md5(title.encode()).hexdigest()[:10]}\n"
                  f"title: {title}\n" + (f"persons:\n{about}" if persons else "")
                  + f"tags:\n{listed}timestamp: \"2026-11-13T21:12:08Z\"\n---\n\n# {title}\n{body}")


def tags_of(memory: RunningMemory, path: str) -> list[str]:
    return parse(memory.page(path))["tags"]


# ── An update from 0.4.x ─────────────────────────────────────────────────

# Cards as a 0.4.x memory holds them: tags as the model and Paperless wrote them.
OLD_LETTER, OLD_LETTER_TEXT = _card("family/documents/2026/11/2026-11-13-party-shop-p302.md", "document",
                                    "Party shop invoice", ["Homers 40th", "Camper Van", "Health"])
OLD_EMAIL, OLD_EMAIL_TEXT = _card("homer/emails/2026/11/lane-booking-cddb1b.md", "email",
                                  "Lane booking", ["Homers 40th", "Bowling"])
OLD_OWN, OLD_OWN_TEXT = _card("marge/notes/2026/11/balloons-9a1c2e.md", "note",
                              "Balloons", ["Homers 40th"])
OLD_VAULT = {**HOUSEHOLD_FILES, OLD_LETTER: OLD_LETTER_TEXT, OLD_EMAIL: OLD_EMAIL_TEXT, OLD_OWN: OLD_OWN_TEXT}


@pytest.fixture
def updated(tmp_path) -> Iterator[RunningMemory]:
    """The 0.4.x memory after `stack up memory` ran every migration, as after `stack update`."""
    with running_memory(tmp_path / "data", OLD_VAULT, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        stck = memory.stack
        migrations = Migrations("memory", MEMORY_STACKLET, tmp_path / "data" / "memory")
        outcome = migrations.run(
            lambda m: MigrationContext(m, stack=stck, stacklet_id="memory", env={}),
            lambda msg: None)
        assert not outcome.get("failed"), outcome
        yield memory


def test_an_update_from_04_puts_no_private_topic_id_on_a_family_card(updated):
    """Given a 0.4.x family letter tagged "Homers 40th", when the update's
    migrations run, then the letter carries the plain keyword, never Marge's
    topic id, and its other tags are tagged as the family's vocabulary has them."""
    assert tags_of(updated, OLD_LETTER) == ["homers-40th", "van", "health"]


def test_an_update_from_04_puts_no_private_topic_id_on_another_persons_card(updated):
    """Given a 0.4.x email in Homer's bucket tagged "Homers 40th", when the
    update's migrations run, then it carries the plain keyword: Homer's bucket is not Marge's."""
    assert tags_of(updated, OLD_EMAIL) == ["homers-40th", "bowling"]


def test_an_update_from_04_tags_marges_own_card_with_her_topic(updated):
    """Given a 0.4.x note in Marge's own bucket tagged "Homers 40th", when the
    update's migrations run, then it carries her topic's id, as today."""
    assert tags_of(updated, OLD_OWN) == [PRIVATE]


# ── The repair of a memory already tagged ────────────────────────────────

# Cards as memory/0005 and memory/0011 left them, written by the memory bot.
CAKE, CAKE_TEXT = _card("family/notes/2026/11/cake-4b2d1a.md", "note", "Cake for Saturday",
                        [PRIVATE, "van", "cake"], persons=("homer",), body="\nChocolate, three layers.\n")
LANES, LANES_TEXT = _card("homer/emails/2026/11/lanes-7c3e9f.md", "email", "Lanes",
                          [PRIVATE, "health"])
BALLOONS, BALLOONS_TEXT = _card("marge/notes/2026/11/balloons-9a1c2e.md", "note", "Balloons",
                                [PRIVATE, "van"])
BOWLING, BOWLING_TEXT = _card("family/notes/2026/11/bowling-1f0e2d.md", "note", "Bowling night",
                              ["bowling"])
TAGGED = {**HOUSEHOLD_FILES, CAKE: CAKE_TEXT, LANES: LANES_TEXT, BALLOONS: BALLOONS_TEXT,
          BOWLING: BOWLING_TEXT}


@pytest.fixture
def tagged(tmp_path) -> Iterator[RunningMemory]:
    """A memory the shipped migrations tagged; Homer then added Marge's topic
    id to the bowling note by hand in Forgejo."""
    with running_memory(tmp_path / "data", TAGGED, seeded_by="memory-bot",
                        seeded_as="cards: tag topics by id") as memory:
        memory.edited_in_forgejo({BOWLING: BOWLING_TEXT.replace("  - bowling\n", "  - bowling\n  - "
                                                                 f"{PRIVATE}\n")}, by="homer")
        yield memory


def test_the_repair_takes_marges_id_off_a_family_card_and_keeps_the_rest(tagged):
    """Given a family note the migrations tagged with Marge's topic id, when
    the repair runs, then the id is gone and the note's other tags, its
    people and its text are as they were."""
    repair_private_topic_tags(tagged.api, tagged.vault, shared_bucket="family", trailer=TRAILER)

    assert tagged.page(CAKE) == CAKE_TEXT.replace(f"  - {PRIVATE}\n", "")


def test_the_repair_takes_marges_id_off_homers_card(tagged):
    """Given an email in Homer's bucket tagged with Marge's topic id, when
    the repair runs, then the id is gone and its other tags stay."""
    repair_private_topic_tags(tagged.api, tagged.vault, shared_bucket="family", trailer=TRAILER)

    assert tags_of(tagged, LANES) == ["health"]


def test_the_repair_leaves_marges_own_card(tagged):
    """Given a note in Marge's own bucket with her topic id, when the repair
    runs, then the note is as it was: her bucket may carry her topics."""
    repair_private_topic_tags(tagged.api, tagged.vault, shared_bucket="family", trailer=TRAILER)

    assert tagged.page(BALLOONS) == BALLOONS_TEXT


def test_the_repair_keeps_a_tag_a_person_set_by_hand_and_names_it(tagged):
    """Given a family note to which Homer added Marge's topic id by hand,
    when the repair runs, then the tag stays and the output names the card,
    so the admin decides: the repair never drops a person's own tag."""
    found, _ = repair_private_topic_tags(tagged.api, tagged.vault, shared_bucket="family",
                                         trailer=TRAILER)

    assert tags_of(tagged, BOWLING) == ["bowling", PRIVATE]
    assert any(BOWLING in line and PRIVATE in line for line in found.left)


def test_the_repair_is_one_commit_by_the_memory_bot(tagged):
    """Given two cards to repair, when the repair runs, then it is one commit
    by the memory bot with the migration's trailer, so one revert undoes it."""
    before = tagged.forgejo.head()

    found, sha = repair_private_topic_tags(tagged.api, tagged.vault, shared_bucket="family",
                                           trailer=TRAILER)

    assert sorted(found.changes) == sorted([CAKE, LANES])
    assert git(tagged.forgejo.repo, "rev-list", "--count", f"{before}..main") == "1"
    assert sha == tagged.forgejo.head()
    assert TRAILER in git(tagged.forgejo.repo, "log", "-1", "--format=%B")
    assert git(tagged.forgejo.repo, "log", "-1", "--format=%an") == "memory-bot"


def test_a_second_repair_changes_nothing(tagged):
    """Given the repair ran, when it runs again, then it finds nothing to
    change and writes no commit."""
    repair_private_topic_tags(tagged.api, tagged.vault, shared_bucket="family", trailer=TRAILER)
    tagged.wait_until_pushed()
    head = tagged.forgejo.head()

    again, sha = repair_private_topic_tags(tagged.api, tagged.vault, shared_bucket="family",
                                           trailer=TRAILER)

    assert again.changes == {} and sha is None
    assert tagged.forgejo.head() == head


def test_an_edit_made_during_the_repair_makes_the_curator_refuse_it(tagged):
    """Given the repair read the family note, when Marge edits that note in
    Forgejo before the repair commits, then the curator refuses the commit
    and her edit stays: the repair writes only on the version it read."""
    edited = CAKE_TEXT.replace("Chocolate, three layers.", "Lemon, two layers.")

    def set_by_while_marge_edits(card_id: str, tag: str) -> bool:
        # The read of the note's history is the window between its read and the commit.
        if card_id == parse(CAKE_TEXT)["card_id"]:
            tagged.edited_in_forgejo({CAKE: edited}, by="marge")
        return False

    found = private_topic_repair_for(tagged.vault, shared_bucket="family", set_by=set_by_while_marge_edits)
    with pytest.raises(Refused):
        commit_found(tagged.api, tagged.vault, found, title="take another person's topic off",
                     trailer=TRAILER)
    tagged.wait_until_pushed()

    assert tagged.text(CAKE) == edited


# ── Cards the repair leaves for a person ─────────────────────────────────

DIARY, DIARY_TEXT = "family/diary/2026/11/2026-11-14.md", signed(
    _card("family/diary/2026/11/2026-11-14.md", "diary", "Friday", [PRIVATE, "cake"])[1])
NO_ID = "family/notes/2026/11/streamers-2b7d.md"
NO_ID_TEXT = ("---\ntype: note\ntitle: Streamers\ntags:\n  - surprise-party\n  - van\n---\n\n"
              "# Streamers\n")
UNKNOWN, UNKNOWN_TEXT = _card("family/notes/2026/11/candles-5e8a.md", "note", "Candles", [PRIVATE])
# An id written by hand that has no card id's shape: the curator finds no card by it.
UNKNOWN_TEXT = UNKNOWN_TEXT.replace(f"card_id: {parse(UNKNOWN_TEXT)['card_id']}", "card_id: Candles 2026")
LEFT = {**HOUSEHOLD_FILES, DIARY: DIARY_TEXT, NO_ID: NO_ID_TEXT, UNKNOWN: UNKNOWN_TEXT}


@pytest.fixture
def left(tmp_path) -> Iterator[RunningMemory]:
    """A family diary card the compile owns, a note without a card id, and
    a note whose id the curator does not know, each with Marge's topic id."""
    with running_memory(tmp_path / "data", LEFT, seeded_by="memory-bot",
                        seeded_as="cards: tag topics by id") as memory:
        yield memory


def test_the_repair_takes_marges_id_off_a_diary_card_and_signs_it_again(left):
    """Given a family diary card the compile owns, tagged with Marge's
    topic id, when the repair runs, then the id is gone and the card is
    signed again, so the next compile still owns it."""
    repair_private_topic_tags(left.api, left.vault, shared_bucket="family", trailer=TRAILER)

    assert tags_of(left, DIARY) == ["cake"]
    assert not changed_by_hand(left.page(DIARY))


def test_the_repair_keeps_the_id_on_a_card_without_a_card_id_and_names_it(left):
    """Given a family note without a card id, when the repair runs, then
    the tag stays and the output names the card: without a history, the
    repair cannot tell whether a person set it."""
    found, _ = repair_private_topic_tags(left.api, left.vault, shared_bucket="family", trailer=TRAILER)

    assert left.page(NO_ID) == NO_ID_TEXT
    assert any(NO_ID in line for line in found.left)


def test_the_repair_keeps_the_id_when_the_curator_gives_no_history_and_names_it(left):
    """Given a family note whose id the curator finds no card by, so it
    gives no history, when the repair runs, then the tag stays and the
    output names the card."""
    found, _ = repair_private_topic_tags(left.api, left.vault, shared_bucket="family", trailer=TRAILER)

    assert left.page(UNKNOWN) == UNKNOWN_TEXT
    assert any(UNKNOWN in line for line in found.left)
