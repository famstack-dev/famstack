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
from datetime import datetime, timezone
from types import SimpleNamespace


import stack.ai.models as ai_models
from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.pipeline import Classifier
from stacklets.docs.bot.vault_context import VaultContext
from tests.household import ONTOLOGY, note_people, renamed_topics
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


@note_people
def test_a_person_the_text_names_joins_the_card_though_the_model_left_her_out(archivist, memory, household):
    """Given a note whose text names Lisa and a model that names only Homer,
    then Lisa is on the card as well, as on a diary entry, whose people are
    those the model listed and those its title names.

    Today only the model's list counts."""
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


@note_people
def test_the_reply_is_built_from_the_cards_people_and_tags(archivist, memory, household):
    """Given the model names "Mr. Burns", whom the household does not know,
    when the note is filed, then the classification the reply reads holds
    the card's person ids and tags, not the model's raw names.

    Today it holds the model's answer as it came."""
    outcome, card = filed_with_unknown_name(archivist, memory, household)

    assert outcome.classification["persons"] == card["persons"]
    assert outcome.classification["tags"] == card["tags"]


@note_people
def test_the_capture_event_carries_the_cards_people_and_tags(archivist, memory, household):
    """Given the same note, then the event the archivist emits has the card's
    people and tags in `data.persons` and `data.tags`.

    Today they are the model's names and words."""
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


def archivist_in(tmp_path, rooms: dict[str, list[str]]) -> tuple[ArchivistBot, Chat]:
    """The archivist bot in `rooms` ({room id: member ids}), recording what it sends."""
    bot = ArchivistBot(homeserver="http://h", user_id="@archivist-bot:simpson", password="x",
                       session_dir=tmp_path / "archivist-session",
                       services=SimpleNamespace(vault=VaultContext(language="en", shared_bucket="family")))
    bot.shared_bucket = "family"
    bot._client = SimpleNamespace(rooms={room: SimpleNamespace(users={u: object() for u in members})
                                         for room, members in rooms.items()})
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
    topic and not hers. Why: every member's client and every bot in the
    room reads the event, not only the text."""
    outcome = marges_note_in_family(archivist, household)
    bot, chat = archivist_in(tmp_path, {FAMILY_ROOM: ["@marge:simpson", "@homer:simpson", "@archivist-bot:simpson"]})

    asyncio.run(bot._captures.reply(FAMILY_ROOM, outcome, "$note"))

    [event] = chat.events
    assert event["data"]["card_id"] == outcome.envelope["data"]["card_id"]
    assert "Camper Van" in event["data"]["tags"]
    assert "40th" not in json.dumps(event)
    assert "Homers 40th" in outcome.envelope["data"]["tags"], "the outcome itself is not changed"


def test_the_event_in_marges_own_room_keeps_her_topic(archivist, memory, household, tmp_path):
    """Given the same note, when the archivist answers in Marge's room with
    the archivist alone, then the event keeps her topic: she may read it."""
    outcome = marges_note_in_family(archivist, household)
    bot, chat = archivist_in(tmp_path, {"!marge:simpson": ["@marge:simpson", "@archivist-bot:simpson"]})

    asyncio.run(bot._captures.reply("!marge:simpson", outcome, "$note"))

    [event] = chat.events
    assert "Homers 40th" in event["data"]["tags"]


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


# ── A correction of an old note ──────────────────────────────────────────

@note_people
def test_a_reply_that_corrects_a_note_leaves_the_card_in_the_vocabulary(archivist, memory, household):
    """Given an old note, when someone replies in its thread and the model
    names "Mr. Burns", then the corrected card's people are the declared
    ones and its tags are the topic card's id and the keywords as spelled.

    Today the correction writes the name Mr. Burns on the card."""
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

@renamed_topics
def test_a_tag_naming_a_renamed_topic_gets_the_new_id(archivist, memory, household):
    """Given the topic "Quilt for Lisa" was renamed "Lisa's Quilt" (id
    `lisas-quilt`), when the model still answers the old title and the old id,
    then the card is tagged with the new id once.

    Today the old id is kept as a keyword, and the tag names a topic that no
    longer exists."""
    renamed = memory.person("marge").runs("memory", "topic", ["rename", "quilt-for-lisa", "lisas-quilt",
                                                                "Lisa's Quilt"])
    assert renamed.get("committed") is True, renamed
    household.says(persons=["Lisa"], tags=["Quilt for Lisa", "quilt-for-lisa"])

    assert files(archivist, memory, "Fabric for the quilt")["tags"] == ["lisas-quilt"]
