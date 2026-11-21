"""A diary card whose header does not parse: the diary leaves it out and goes on.

A person can break a card's header by hand, in Forgejo or a clone. The
diary names that card's path and leaves it as it is; it does not repair
it, and it does not stop for it. The compile tests' harness drives the
diary as the admin runs it (`./stack memory diary`), with the room on the
stand-in Synapse and memory's test instance. The family is the Simpsons.
"""

# ruff: noqa: F811  (the imported fixtures are used as test parameters)
from __future__ import annotations

from datetime import date

from stacklets.memory.bot.diary.card import files as card_files
from stacklets.memory.bot.diary.plan import plan
from tests.memory.unit.diary_cards import diary_card_of
from tests.memory.unit.test_memory_diary_compile import (  # noqa: F401  (fixtures)
    ENTRIES, LISA, SKATEBOARD, TREEHOUSE, bot_command, bot_runner, brain, commits_since,
    diary, head, memory, model, room,
)


def broken_by_hand(text: str) -> str:
    """`text` with a header line that has no `key:`, as a slip in an editor leaves it."""
    return text.replace("---\n", "---\nMarge was here\n", 1)


def test_a_diary_card_that_does_not_parse_is_named_and_the_diary_goes_on(
        memory, room, diary, brain, model):
    """Given two diary cards, one of them with a header a person broke by
    hand, and a new entry since, when the diary compiles, then the new
    entry gets its card, the progress names the broken card's path, and
    the broken file keeps its bytes. `diary pages` then writes the month
    without the broken card, and a correction to the other entry is filed.

    One broken file used to stop every diary run until a person repaired
    it: no new cards, no corrections, no month pages."""
    room.says("marge", LISA)
    room.says("homer", TREEHOUSE)
    diary.runs()
    [lisa_path] = [p for p, t in memory.pages(ENTRIES).items() if LISA in t]
    [treehouse_path] = [p for p, t in memory.pages(ENTRIES).items() if TREEHOUSE in t]
    broken = broken_by_hand(memory.forgejo.file(lisa_path))
    memory.edited_in_forgejo({lisa_path: broken}, by="marge")
    room.says("bart", SKATEBOARD)

    diary.runs()

    assert diary.answer.get("exit") in (None, 0), diary.progress
    assert any(SKATEBOARD in text for text in memory.pages(ENTRIES).values())
    assert lisa_path in diary.progress
    assert memory.forgejo.file(lisa_path) == broken

    diary.runs("pages", "--fast")

    assert diary.answer.get("exit") in (None, 0), diary.progress
    month = "\n".join(text for path, text in brain.pages().items() if "/diary/" in path)
    assert "skateboard" in month and "treehouse" in month
    assert "saxophone" not in month

    entry_id = treehouse_path.removesuffix(".md").rsplit("-", 1)[-1]
    mark = head(memory)
    diary.runs("correct", entry_id, "--by", "marge", "--event", "$fix1:simpson",
               "--text", "Bart helped too.")

    assert diary.answer.get("exit") in (None, 0), diary.progress
    assert len(model.corrections) == 1
    assert len(commits_since(memory, mark)) == 1


def test_the_compile_leaves_a_file_it_could_not_read_as_a_card_and_names_it(capsys):
    """Given a file at a new card's path that is not read as a diary card,
    when the compile plans, then it writes nothing to that path and names
    the path on stderr.

    The file is a person's: a card for that entry would replace it."""
    card = diary_card_of("b48902f591", sender="bart", at=1709985600001,
                         event_ids=["$event1"], body="Bart fell off his skateboard.",
                         on=date(2024, 3, 2), kind="text", title="Skateboard")
    [path] = [p for p in card_files(card, bucket="family") if p.endswith(".md")]

    planned = plan({path: "---\ntype: note\n---\n\nBart's own page.\n"}, [card],
                   bucket="family", complete=True)

    assert path not in planned.writes and planned.deletes == []
    assert path in capsys.readouterr().err
