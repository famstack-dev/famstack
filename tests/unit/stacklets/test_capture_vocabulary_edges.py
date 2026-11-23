# ruff: noqa: F811  (a fixture imported from another test module is the test's parameter)
"""The edges of a capture's vocabulary: who may see what, what the reply shows, what a correction does.

`test_capture_names_people_and_tags.py` states which people and tags a note
gets. This file states what surrounds it, one test per case, all through the
real path (the capture pipeline as the archivist builds it, the mirror, the
real curator over a bare repository) and one model for both engines
(`tests.household`). The household is the Simpsons.

A test of behavior that is wrong today carries a marker of `tests.household`
(a strict expected failure whose reason states the wanted behavior); the
mark goes when the behavior is built. A test without a marker protects
behavior that is right.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import stack.ai.models as ai_models
from stack.bot import context_for
from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.card_changes import Change
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.pipeline import Classifier
from stacklets.docs.bot.vault_context import VaultContext
from tests.household import HOUSEHOLD_FILES, ONTOLOGY
from tests.memory.unit.instance import running_memory
from tests.unit.stacklets.test_capture_names_people_and_tags import (  # noqa: F401  (fixtures)
    ABOUT, CAMPING, archivist, files, household, memory, mirror, pipeline, vault,
)


# ── Who sees what ────────────────────────────────────────────────────────

def test_homers_prompt_shows_nothing_of_marges_personal_topic(archivist, memory, household):
    """Given Marge's personal topic card "Homers 40th", when Homer files a
    note into the family's bucket, then the prompt the model reads for Homer shows no word of it."""
    household.says(persons=["Homer"], tags=["Camper Van"])

    files(archivist, memory, by="homer", bucket="family")

    assert "40th" not in household.prompts[-1]


def test_homers_card_never_carries_marges_personal_topic(archivist, memory, household):
    """Given Marge's personal topic card `surprise-party`, titled "Homers
    40th", when the model answers "Homers 40th" for Homer's note in the
    family's bucket, then the card keeps the plain keyword `homers-40th`,
    never her topic's id: the family's bucket is not hers. The curator
    files it."""
    household.says(persons=["Homer"], tags=["Homers 40th", "Camper Van"])

    card = files(archivist, memory, by="homer", bucket="family")

    assert card["path"].startswith("family/")
    assert card["tags"] == ["homers-40th", "van"]


def test_homers_card_never_carries_the_id_of_marges_personal_topic(archivist, memory, household):
    """Given Marge's personal topic `surprise-party`, when the model answers
    that id as a keyword for Homer's note in the family's bucket, then the
    note is filed without it: the curator refuses another bucket's topic id
    on a family card, so the writer leaves it out instead of losing the note."""
    household.says(persons=["Homer"], tags=["surprise-party", "Camper Van"], topics=[])

    card = files(archivist, memory, by="homer", bucket="family")

    assert card["tags"] == ["van"]


def test_marges_correction_of_a_family_note_uses_the_notes_topics(archivist, memory, household):
    """Given Homer's note in the family's bucket, when Marge replies in its
    thread and the model answers "Homers 40th", then the corrected card
    has the keyword `homers-40th`: a correction reads the topics of the
    card's place, never the corrector's."""
    household.says(persons=["Homer"], tags=["Camper Van"])
    note = files(archivist, memory, by="homer", bucket="family")
    household.says(persons=["Homer"], tags=["Camper Van", "Homers 40th"])

    asyncio.run(archivist.reprocess(
        vault_path=note["path"], user_hint="it is for the party", sender_mxid="@marge:simpson",
        reply_text="it is for the party", reply_id="$reply:simpson",
        reply_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))

    assert parse(memory.page(note["path"]))["tags"] == ["van", "homers-40th"]


def test_marges_own_note_carries_her_topic(archivist, memory, household):
    """Given Marge's topic "Homers 40th", when she files a note into her own
    bucket and the model answers its title, then her card has her topic's id."""
    household.says(persons=["Homer"], tags=["Homers 40th"])

    card = files(archivist, memory, by="marge")

    assert card["path"].startswith("marge/")
    assert card["tags"] == ["surprise-party"]


# ── Who a note is about ──────────────────────────────────────────────────

def test_a_spelling_from_a_persons_page_is_that_person(archivist, memory, household):
    """Given Homer's page lists "Mr. Simpson", when the model names
    "Mr. Simpson", then the card is about Homer."""
    household.says(persons=["Mr. Simpson"])

    assert files(archivist, memory)["persons"] == ["homer"]


def test_a_person_the_text_names_joins_the_card_though_the_model_left_her_out(archivist, memory, household):
    """Given a note whose text names Lisa and a model that names only Homer,
    then Lisa is on the card as well, as on a diary entry, whose people are
    those the model listed and those its title names."""
    household.says(persons=["Homer"])

    card = files(archivist, memory, "Lisa baked a cake for Homer")

    assert card["persons"] == ["homer", "lisa"]


# ── What the reply shows ─────────────────────────────────────────────────

def filed_with_unknown_name(archivist, memory, household):
    """Marge files a note for which the model names Homer, Mr. Burns (whom
    the household does not know) and the keywords "LLMs" and "llm"; returns
    the outcome and the card the memory holds."""
    household.says(persons=["Homer", "Mr. Burns"], tags=["LLMs", "llm"])
    outcome = asyncio.run(archivist.capture_text(text=CAMPING, sender_mxid="@marge:simpson"))
    return outcome, parse(memory.page(outcome.vault_path))


def test_the_reply_is_built_from_the_cards_people_and_tags(archivist, memory, household):
    """Given the model names "Mr. Burns", whom the household does not know,
    when the note is filed, then the classification the reply reads holds
    the card's person ids and tags, not the model's raw names."""
    outcome, card = filed_with_unknown_name(archivist, memory, household)

    assert outcome.classification["persons"] == card["persons"]
    assert outcome.classification["tags"] == card["tags"]


def test_the_capture_event_carries_the_cards_people_and_tags(archivist, memory, household):
    """Given the same note, then the event the archivist emits has the card's
    people and tags in `data.persons` and `data.tags`."""
    outcome, card = filed_with_unknown_name(archivist, memory, household)

    assert outcome.envelope["data"]["persons"] == card["persons"]
    assert outcome.envelope["data"]["tags"] == card["tags"]


def about_line(page: str) -> str:
    """The line that says who a document is about (`**About:**` in its header line)."""
    return next(line for line in page.splitlines() if "About" in line)


# ── What a room is shown ─────────────────────────────────────────────────
#
# Marge's note in #family files into her own bucket and may carry her topic
# "Homers 40th". The reply is read by everyone in the room, so it shows only
# the topics they may all read.

FAMILY_ROOM = "!family:simpson"


class Chat:
    """The Matrix side of the archivist: the text of each message it sent,
    and the `dev.famstack.event` each carried (None for a message without one)."""

    def __init__(self):
        self.sent: list[str] = []
        self.events: list[dict | None] = []


def archivist_in(tmp_path, rooms: dict[str, list[str]], *, capture=None) -> tuple[ArchivistBot, Chat]:
    """The archivist bot in `rooms` ({room id: member ids}), recording what
    it sends; `capture` is its capture pipeline, for a test that sends it a
    note. No room is a topic room: none holds topic state."""
    bot = ArchivistBot(homeserver="http://h", user_id="@archivist-bot:simpson", password="x",
                       session_dir=tmp_path / "archivist-session",
                       services=SimpleNamespace(vault=VaultContext(language="en", shared_bucket="family"),
                                                capture=capture))
    bot.shared_bucket = "family"

    async def room_messages(room_id, **_):
        return SimpleNamespace(chunk=[])

    bot._client = SimpleNamespace(
        rooms={room: SimpleNamespace(room_id=room, name="Chat", display_name="Chat",
                                     users={u: object() for u in members})
               for room, members in rooms.items()},
        room_messages=room_messages)
    chat = Chat()

    async def _send(room_id, text, reply_to=None, *, metadata=None, **_):
        chat.sent.append(text)
        chat.events.append((metadata or {}).get("dev.famstack.event"))

    async def _react(room_id, event_id, key):
        pass

    bot._send, bot._react = _send, _react
    return bot, chat


def marges_note_in_family(archivist, household):
    """Marge's note in #family, for which the model names her topic "Homers
    40th" and the family's "Camper Van"; returns the outcome."""
    household.says(persons=["Homer"], tags=["Homers 40th", "Camper Van"])
    outcome = asyncio.run(archivist.capture_text(text="Order the cake", sender_mxid="@marge:simpson"))
    assert outcome.vault_path.startswith("marge/")
    return outcome


def test_a_reply_in_a_family_room_shows_no_private_topic(archivist, memory, household, tmp_path):
    """Given Marge's note in #family, filed into her bucket with her topic,
    when the archivist answers in the room, then the answer shows the
    family's topic and not hers, while her card keeps both."""
    outcome = marges_note_in_family(archivist, household)
    bot, chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@homer:simpson", "@archivist-bot:simpson"]})

    asyncio.run(bot._captures.reply(FAMILY_ROOM, outcome, "$note"))

    assert parse(memory.page(outcome.vault_path))["tags"] == ["surprise-party", "van"]
    [answer] = chat.sent
    assert "Camper Van" in answer
    assert "Homers 40th" not in answer


def test_a_correction_answered_in_a_family_room_shows_no_private_topic(archivist, memory, household, tmp_path):
    """Given the same note, when Lisa corrects it in #family, then the
    answer with what changed shows no word of Marge's topic."""
    outcome = marges_note_in_family(archivist, household)
    corrected = asyncio.run(archivist.reprocess(
        vault_path=outcome.vault_path, user_hint="the cake is chocolate", sender_mxid="@lisa:simpson",
        reply_text="the cake is chocolate", reply_id="$reply:simpson",
        reply_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))
    bot, chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@lisa:simpson"]})

    asyncio.run(bot._captures.reply(FAMILY_ROOM, corrected, "$reply",
                                    corrected_from=outcome.envelope["data"]))

    [answer] = chat.sent
    assert "40th" not in answer
    assert "surprise" not in answer.lower()


def test_a_corrections_change_line_names_a_topic_by_its_title(archivist, memory, household, tmp_path):
    """Given a note in #family with the keyword "lake", when Homer corrects
    it so that the model now chooses the topic "camper van", then the line
    that says what changed names the topic by its title "Camper Van", as the
    card under it does, never by its id: whether the memory reports the
    change or the answer works it out from the two cards."""
    household.says(persons=["Homer"], tags=["lake"], topics=[])
    outcome = asyncio.run(archivist.capture_text(text="Two nights by the water", sender_mxid="@homer:simpson",
                                                 bucket="family"))
    household.says(persons=["Homer"], tags=["lake"], topics=["camper van"])
    corrected = asyncio.run(archivist.reprocess(
        vault_path=outcome.vault_path, user_hint="we went in the van", sender_mxid="@homer:simpson",
        reply_text="we went in the van", reply_id="$reply:simpson",
        reply_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))
    bot, chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@homer:simpson"]})

    reported = replace(corrected, changes=(Change(field="tag", kind="added", value="camper-van"),))

    for answered in (corrected, reported):
        asyncio.run(bot._captures.reply(FAMILY_ROOM, answered, "$reply", corrected_from=outcome.envelope["data"]))

    for answer in chat.sent:
        change_line = answer.splitlines()[0]
        assert "Camper Van" in change_line
        assert "van" not in change_line.replace("Camper Van", "").split()


MARGES_ROOM = "!marge:simpson"


def sends_note(bot, room_id: str, words: str, sender: str = "@marge:simpson") -> None:
    """`words` sent by `sender` to `room_id`, as the archivist receives a note."""
    room = bot._client.rooms[room_id]
    asyncio.run(bot._captures.capture_text(room_id, words, sender, "$note",
                                           ctx=context_for(room, bot_user_id=bot.user_id, config={})))


def test_a_note_from_a_room_of_several_people_is_shown_no_private_topic(archivist, memory, household,
                                                                         tmp_path):
    """Given Marge's private topic "Homers 40th", when she sends a note to
    #family, then the model is not shown its title: whatever it writes
    from the list reaches a reply everyone in the room reads. The family's
    topics are still offered."""
    bot, _chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@homer:simpson"]}, capture=archivist)

    sends_note(bot, FAMILY_ROOM, "Ordered 40 balloons")

    prompt = household.prompts[-1]
    assert "Camper Van" in prompt
    assert "Homers 40th" not in prompt


def test_a_correction_from_a_room_of_several_people_is_shown_no_private_topic(archivist, memory, household,
                                                                               tmp_path):
    """Given Marge's note filed into her bucket with her topic "Homers 40th",
    when Lisa corrects it in #family, then the model writing the corrected
    card is not shown that title: the answer in the room quotes what it
    wrote."""
    outcome = marges_note_in_family(archivist, household)
    bot, _chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@lisa:simpson"]}, capture=archivist)

    asyncio.run(bot._corrections.reprocess_capture(
        FAMILY_ROOM, outcome.vault_path, "the cake is chocolate", "@lisa:simpson", "$reply",
        ctx=context_for(bot._client.rooms[FAMILY_ROOM], bot_user_id=bot.user_id, config={}),
        initial_classification=outcome.envelope["data"], reply_text="the cake is chocolate",
        reply_id="$reply:simpson", reply_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))

    prompt = household.prompts[-1]
    assert "the cake is chocolate" in prompt
    assert "Homers 40th" not in prompt
    assert "surprise-party" not in prompt, "the card's own tags pass the room's view too"


def corrects(bot, room_id: str, outcome, words: str, by: str = "@lisa:simpson") -> None:
    """`by` replies `words` to the archivist's answer about `outcome` in
    `room_id`, as the archivist receives a correction."""
    asyncio.run(bot._corrections.reprocess_capture(
        room_id, outcome.vault_path, words, by, "$reply",
        ctx=context_for(bot._client.rooms[room_id], bot_user_id=bot.user_id, config={}),
        initial_classification=outcome.envelope["data"], reply_text=words,
        reply_id="$reply:simpson", reply_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))


def test_a_correction_in_marges_own_room_is_shown_her_topic(archivist, memory, household, tmp_path):
    """Given the same note, when Marge corrects it in her own room with the
    archivist, then the model is shown her topic, by its title and among
    the card's tags: she may read it, so the room's view holds it back
    only where other people read the answer."""
    outcome = marges_note_in_family(archivist, household)
    bot, _chat = archivist_in(tmp_path, {MARGES_ROOM: ["@marge:simpson", "@archivist-bot:simpson"]},
                              capture=archivist)

    corrects(bot, MARGES_ROOM, outcome, "the cake is chocolate", by="@marge:simpson")

    prompt = household.prompts[-1]
    assert "Homers 40th" in prompt
    assert "surprise-party" in prompt


def test_a_correction_from_a_room_of_several_people_keeps_the_cards_private_topic(
        archivist, memory, household, tmp_path):
    """Given Marge's note with her topic `surprise-party`, when Lisa corrects
    it in #family and the model, not shown that topic, answers only the
    family's "Camper Van", then the card in Marge's bucket still carries
    her topic: what the room may not see is held back from the prompt, not
    taken off her card, where it was in the card's order."""
    outcome = marges_note_in_family(archivist, household)
    bot, _chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@lisa:simpson"]}, capture=archivist)
    household.says(persons=["Homer"], tags=["Camper Van"])

    corrects(bot, FAMILY_ROOM, outcome, "the cake is chocolate")

    assert parse(memory.page(outcome.vault_path))["tags"] == ["surprise-party", "van"]


MARGES_BOOKMARK = "marge/bookmarks/2026/11/cake-shop-7c1d2e.md"
BOOKMARK_CARD = ("---\ntype: bookmark\ncard_id: bookmark-7c1d2e3f4a\ntitle: Cake shop\ndate: \"2026-11-20\"\n"
                 "resource: https://springfield.example/cakes\npersons:\n  - marge\n"
                 "tags:\n  - surprise-party\n  - van\n---\n\n# Cake shop\n\n"
                 "> [!summary]\n> A bakery that delivers.\n")


@pytest.mark.parametrize("memory", [{**HOUSEHOLD_FILES, MARGES_BOOKMARK: BOOKMARK_CARD}], indirect=True)
def test_a_bookmarks_correction_from_a_room_of_several_people_is_shown_no_private_topic(
        archivist, memory, household, tmp_path):
    """Given Marge's bookmark in her bucket tagged with her topic
    `surprise-party`, when Lisa corrects it in #family, then the text the
    model reads holds the family's `van` among the entry's tags and not
    her topic, and the card keeps both."""
    bot, _chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@lisa:simpson"]}, capture=archivist)
    household.says(persons=["Marge"], tags=["Camper Van"])

    asyncio.run(bot._corrections.reprocess_capture(
        FAMILY_ROOM, MARGES_BOOKMARK, "they also bake for vans", "@lisa:simpson", "$reply",
        ctx=context_for(bot._client.rooms[FAMILY_ROOM], bot_user_id=bot.user_id, config={}),
        reply_text="they also bake for vans", reply_id="$reply:simpson",
        reply_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))

    prompt = household.prompts[-1]
    assert "they also bake for vans" in prompt
    [tags_line] = [line for line in prompt.splitlines() if line.startswith("Existing tags on this entry")]
    assert "van" in tags_line
    assert "surprise-party" not in prompt
    assert parse(memory.page(MARGES_BOOKMARK))["tags"] == ["surprise-party", "van"]


def test_a_note_filed_with_the_capture_command_is_shown_no_private_topic(memory, household):
    """Given Marge's private topic "Homers 40th", when a note is filed for
    her with `stack memory capture --by marge`, then the model is not shown
    its title: the command has no room, and the agent may relay its receipt
    into a room of several people. The family's topics are still offered."""
    try:
        memory.person("marge").runs("memory", "capture", ["Ordered 40 balloons", "--by", "marge"])
    except SystemExit as ended:
        assert not ended.code

    prompt = household.prompts[-1]
    assert "Camper Van" in prompt
    assert "Homers 40th" not in prompt


def test_a_note_in_marges_own_room_is_shown_her_topic(archivist, memory, household, tmp_path):
    """Given the same topic, when Marge sends a note to her own room with
    the archivist, then the model is shown its title, and choosing it puts
    her topic on her card."""
    household.says(persons=["Homer"], tags=["balloons"], topics=["Homers 40th"])
    bot, chat = archivist_in(tmp_path, {MARGES_ROOM: ["@marge:simpson", "@archivist-bot:simpson"]},
                             capture=archivist)

    sends_note(bot, MARGES_ROOM, "Ordered 40 balloons")

    assert "Homers 40th" in household.prompts[-1]
    [(path, page)] = [(p, t) for p, t in memory.pages("marge/notes").items()]
    assert "surprise-party" in parse(page)["tags"]


def test_a_reply_names_a_topic_by_its_title(archivist, memory, household, tmp_path):
    """Given a note for which the model chose the topic "camper van" and the
    keyword "lake", when the archivist answers in #family, then the answer
    names the topic by its card's title "Camper Van", never by its id, and
    the keyword as it is written."""
    household.says(persons=["Homer"], tags=["lake"], topics=["camper van"])
    outcome = asyncio.run(archivist.capture_text(text="Two nights by the water", sender_mxid="@homer:simpson",
                                                 bucket="family"))
    bot, chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@homer:simpson", "@archivist-bot:simpson"]})

    asyncio.run(bot._captures.reply(FAMILY_ROOM, outcome, "$note"))

    [answer] = chat.sent
    tag_line = next(line for line in answer.splitlines() if "Camper Van" in line)
    assert "lake" in tag_line.split()
    assert "van" not in tag_line.replace("Camper Van", "").split()


def test_a_reply_in_marges_own_room_shows_her_topic(archivist, memory, household, tmp_path):
    """Given the same note, when the archivist answers in Marge's room with
    the archivist alone, then the answer shows her topic: she may read it."""
    outcome = marges_note_in_family(archivist, household)
    bot, chat = archivist_in(tmp_path, {"!marge:simpson": ["@marge:simpson", "@archivist-bot:simpson"]})

    asyncio.run(bot._captures.reply("!marge:simpson", outcome, "$note"))

    [answer] = chat.sent
    assert "Homers 40th" in answer


def test_the_event_a_family_room_receives_carries_no_private_topic(archivist, memory, household, tmp_path):
    """Given Marge's note in #family, when the archivist answers in the
    room, then the capture event the message carries names the family's
    topic and not hers, by the ids the card is written with. Why: every
    member's client and every bot in the room reads the event, not only
    the text."""
    outcome = marges_note_in_family(archivist, household)
    bot, chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@homer:simpson", "@archivist-bot:simpson"]})

    asyncio.run(bot._captures.reply(FAMILY_ROOM, outcome, "$note"))

    [event] = chat.events
    assert event["data"]["card_id"] == outcome.envelope["data"]["card_id"]
    assert "van" in event["data"]["tags"]
    assert "surprise-party" not in json.dumps(event) and "40th" not in json.dumps(event)
    assert "surprise-party" in outcome.envelope["data"]["tags"], "the outcome itself is not changed"


def test_the_event_in_marges_own_room_keeps_her_topic(archivist, memory, household, tmp_path):
    """Given the same note, when the archivist answers in Marge's room with
    the archivist alone, then the event keeps her topic: she may read it."""
    outcome = marges_note_in_family(archivist, household)
    bot, chat = archivist_in(tmp_path, {"!marge:simpson": ["@marge:simpson", "@archivist-bot:simpson"]})

    asyncio.run(bot._captures.reply("!marge:simpson", outcome, "$note"))

    [event] = chat.events
    assert "surprise-party" in event["data"]["tags"]


# ── A document ───────────────────────────────────────────────────────────

def test_a_document_keeps_its_people_and_tags(mirror, memory):
    """Given a document Paperless read, with the people Homer and Mr. Burns
    and the tags "Camper Van", "Health" and "Invoice", when the archivist
    files it, then its card has Homer's id, the ids of the two topics and
    the keyword, and the About line keeps the name nobody goes by as
    written. Pinned to protect the documents path while captures change."""
    filed = asyncio.run(mirror.publish(
        paperless_id=301, body_text="Two cases of Duff.", processing="ai_formatted", model=None,
        paperless_url="http://docs.home.test", summary=None, tags=["Camper Van", "Health", "Invoice"],
        classification={"title": "Duff Beer invoice", "date": "2026-10-13",
                        "persons": ["Homer", "Mr. Burns"]}))
    page = memory.page(next(iter(memory.pages("family/documents"))))

    assert filed.card_id.startswith("document-")
    assert parse(page)["persons"] == ["homer"]
    assert parse(page)["tags"] == ["van", "health", "invoice"]
    assert "Mr. Burns" in about_line(page)


def test_a_family_document_is_filed_without_a_private_topics_id(mirror, memory):
    """Given Marge's topic "Homers 40th", when Paperless gives a family
    document that tag, then the document is filed with the title as a plain
    keyword and without her topic's id, as a note is: the curator refuses
    that id on a family card, and the document would otherwise not reach
    the memory at all."""
    filed = asyncio.run(mirror.publish(
        paperless_id=302, body_text="Balloons, 40 pieces.", processing="ai_formatted", model=None,
        paperless_url="http://docs.home.test", summary=None, tags=["Homers 40th", "Invoice"],
        classification={"title": "Party shop invoice", "date": "2026-11-13", "persons": ["Marge"]}))

    assert filed is not None
    [page] = memory.pages("family/documents").values()
    assert parse(page)["tags"] == ["homers-40th", "invoice"]


# ── A correction of an old note ──────────────────────────────────────────

def test_a_reply_that_corrects_a_note_leaves_the_card_in_the_vocabulary(archivist, memory, household):
    """Given an old note, when someone replies in its thread and the model
    names "Mr. Burns", then the corrected card's people are the declared
    ones and its tags are the topic card's id and the keywords as spelled."""
    household.says(persons=["Homer"], tags=["Camper Van"])
    note = files(archivist, memory)
    household.says(persons=["Homer", "Mr. Burns"], tags=["Camper Van", "LLMs", "llm"])

    asyncio.run(archivist.reprocess(
        vault_path=note["path"], user_hint="Mr. Burns drove too", sender_mxid="@lisa:simpson",
        reply_text="Mr. Burns drove too", reply_id="$reply:simpson",
        reply_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))

    card = parse(memory.page(note["path"]))
    assert card["persons"] == ["homer"]
    assert card["tags"] == ["van", "llms", "llm"]
    assert "Mr. Burns" not in memory.page(note["path"]).split("## Sources")[0]


# ── A household with no person files ─────────────────────────────────────

class _Paperless:
    """Paperless with no `Person:` tag: the archivist asks it for names when the memory has none."""

    async def get_tags(self):
        return []


def test_without_person_files_the_sender_stays_the_cards_person(tmp_path, household, monkeypatch):
    """Given a household that declared no person yet, when Marge files a
    note, then the card is about Marge: her name is in the About line. (Its
    `persons` field is empty, as no id exists to put in it.)"""
    with running_memory(tmp_path / "data", {"ontology.toml": ONTOLOGY}, seeded_by="memory-bot",
                        seeded_as="seed: no people", model=household.url) as running:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(running.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        monkeypatch.setitem(ai_models._MODELS, "archivist-bot", "stand-in")
        vault = VaultContext(language="en", shared_bucket="family")
        mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                           memory=MemoryAPI.from_container(running.environ))
        archivist = pipeline(mirror, vault, Classifier(household.llm(), language="en"),
                             paperless=_Paperless())

        card = files(archivist, running)

    assert "marge" in card["about"].lower()


# ── A renamed topic ──────────────────────────────────────────────────────

def test_a_tag_naming_a_renamed_topic_gets_the_new_id(archivist, memory, household):
    """Given the topic "Quilt for Lisa" was renamed "Lisa's Quilt" (id
    `lisas-quilt`), when the model still answers the old title and the old id,
    as keywords and as the topics it chose, then the card is tagged with the
    new id once."""
    renamed = memory.person("marge").runs("memory", "topic", ["rename", "quilt-for-lisa", "lisas-quilt",
                                                                "Lisa's Quilt"])
    assert renamed.get("committed") is True, renamed
    household.says(persons=["Lisa"], tags=["Quilt for Lisa", "quilt-for-lisa"])

    assert files(archivist, memory, "Fabric for the quilt")["tags"] == ["lisas-quilt"]
