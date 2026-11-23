"""A correction refiles every message of a note: what a reply in its thread rebuilds and keeps.

A note's sources are its record (ADR-018): a reply in the note's thread adds one, and the note is
filed again from all of them. These tests start where a person does, a Matrix event given to the
archivist as the bot runner loads it, and assert what the memory holds afterwards and what the room
shows: the sources in the sidecar, the card's form and fields, who committed, the done or failed mark.

- every original of a note is read by the rule of its kind, and its form is read from the card;
- a change made while the model answers, or a history that cannot be read, loses no edit;
- a reply is a source by its event id, once, in order, and the note's filer stays its filer;
- a sidecar that does not pair with its card, or holds no original, is not rebuilt from.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from stack.cards.sources import ORIGINAL, Source, quotes_of, source_from
from stack.frontmatter import parse
from stack.memory import MemoryAPI, Refused
from stacklets.docs.bot.extractors import SourceContent
from tests.archivist_chat import FAILED_MARK, FILED_MARK, MARGE, PIXEL, ROOM_ID, ChatRoom, ModelAnswers
from tests.memory.unit.instance import running_memory

PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
          for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"),
                            ("bart", "Bart Simpson"), ("lisa", "Lisa Simpson"))}
FILED = {"title": "Grill charcoal", "persons": ["Marge"], "tags": [], "summary": "Charcoal and propane."}
GRILL = ("The grill needs new charcoal before Saturday, and the propane tank is nearly empty "
         "too, so buy both on the way home.")
LISAS_TAG = "bbq"


@pytest.fixture
def memory(tmp_path, monkeypatch):
    with running_memory(tmp_path / "data", PEOPLE, seeded_by="memory-bot", seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(memory.media))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


def sidecar(memory, card: str) -> dict:
    return json.loads(memory.text(card.removesuffix(".md") + ".json"))


def the_card_of(chat, memory, notice) -> tuple[str, dict, str]:
    path = chat.envelope(notice)["data"]["vault_path"]
    text = memory.text(path)
    return path, parse(text), text


class ModelThatIsInterrupted(ModelAnswers):
    """The model. `meanwhile` runs once, during the next answer it gives; `slow` makes each answer take a moment."""

    def __init__(self, *answers: dict, slow: float = 0.0):
        super().__init__(*answers)
        self.meanwhile, self.slow = None, slow

    async def _answering(self) -> None:
        if self.meanwhile is not None:
            act, self.meanwhile = self.meanwhile, None
            act()
        if self.slow:
            await asyncio.sleep(self.slow)

    async def classify_capture(self, **asked):
        await self._answering()
        return await super().classify_capture(**asked)

    async def correct_note(self, **asked):
        await self._answering()
        return await super().correct_note(**asked)

    async def rebuild_note(self, **asked):
        await self._answering()
        return await super().rebuild_note(**asked)


class HistoryDown(MemoryAPI):
    """The curator's API, whose answer for a card's history can be made a server fault."""

    unreadable = False

    def versions(self, card_id):
        if self.unreadable:
            raise Refused(500, "unknown", "the history could not be read")
        return super().versions(card_id)


@pytest.fixture
async def room(tmp_path, memory, httpserver):
    """Marge's capture room with a model that Lisa's edit can interrupt."""
    model = ModelThatIsInterrupted(FILED)
    chat = ChatRoom(tmp_path, memory, httpserver, model)
    yield chat
    await chat.close()


async def a_filed_grill_note(chat, memory):
    await chat.says(MARGE, GRILL, event_id="$grill")
    [notice] = chat.notices()
    path, _, _ = the_card_of(chat, memory, notice)
    return path


def lisa_adds_the_tag(memory, path: str) -> None:
    """Lisa adds a keyword to the card in Forgejo, the edit a refile keeps (ADR-018 section 7)."""
    memory.edited_in_forgejo(
        {path: memory.text(path).replace("persons:", f"tags:\n  - {LISAS_TAG}\npersons:", 1)}, by="lisa")


def the_sidecar_is_edited_in_forgejo(memory, path: str, change) -> None:
    """Someone changes the note's sidecar in Forgejo: `change` takes its JSON and returns the new one."""
    side = path.removesuffix(".md") + ".json"
    memory.edited_in_forgejo({side: json.dumps(change(sidecar(memory, path)), indent=2) + "\n"}, by="lisa")


def tags_of(memory, path: str) -> list[str]:
    return list(parse(memory.text(path)).get("tags") or [])


# ── A note of several messages: every one is read ──────────────────────

async def a_photo(chat, memory, filename: str, event_id: str) -> tuple[str, Source]:
    """Marge sends a photo, filed as a note of its own: its path and its source as the sidecar keeps it."""
    await chat.sends_file(MARGE, PIXEL, filename, msgtype="m.image", mimetype="image/png",
                          caption="The tent Bart wants", event_id=event_id)
    path = chat.envelope(chat.notices()[-1])["data"]["vault_path"]
    return path, source_from(sidecar(memory, path)["sources"][0], quotes_of(memory.text(path))[0])


async def a_note_of_a_photo_and(chat, memory, *more: Source) -> str:
    """A note of the photo `$tent` and the messages `more` after it, as the pipeline makes one from its
    sources (`ChatRoom.has_a_note_of` makes text only), answered in the thread of `$tent`."""
    path, photo = await a_photo(chat, memory, "tent.png", "$tent")
    outcome = await chat.pipeline._publish(
        source=SourceContent(text="The tent Bart wants", mime="text/plain", title_hint=None, source_uri=None),
        kind="note", sender_mxid=MARGE, display_link="(capture)", actor=MARGE, capture_id="$tent",
        existing_path=path, reclassified=True, sources=[photo, *more])
    await chat.bot._captures.reply(ROOM_ID, outcome, "$tent")
    return path


def a_line(words: str, event_id: str = "$line") -> Source:
    return Source(ORIGINAL, "marge", "2025-11-22T08:22:00+00:00", "chat", "text", event_id, quote=words)


async def test_a_correction_of_a_note_of_a_photo_a_voice_message_and_a_line_shows_the_model_all_three(
        room, memory):
    """Given a note made of a photo, a voice message and a line, when Marge replies in its thread,
    then the model is shown the photo and the words of the voice message and of the line."""
    voice, line = "Call the camping shop on Monday.", "The big one costs 120 dollars."
    memo = Source(ORIGINAL, "marge", "2025-11-22T08:21:00+00:00", "chat", "voice", "$memo",
                  quote=voice, text={"form": "transcript"})
    await a_note_of_a_photo_and(room, memory, memo, a_line(line))
    asked = len(room.model.calls)

    await room.says(MARGE, "Call it Tent day", event_id="$fix", thread="$tent")

    shown = room.model.calls[asked:]
    assert any(call.get("images") for call in shown)
    assert all(any(words in (call.get("text") or "") for call in shown) for words in (voice, line))


async def test_a_correction_of_a_note_of_two_photos_and_a_line_shows_the_model_both_photos(room, memory):
    """Given a note made of two photos and a line, when Lisa replies in its thread, then the model is
    shown both photos: the second photo is as much the note as the first."""
    _, stove = await a_photo(room, memory, "stove.png", "$stove")
    await a_note_of_a_photo_and(room, memory, stove, a_line("Both fit in the car."))
    asked = len(room.model.calls)

    await room.says("@lisa:simpson", "Call it Tents", event_id="$fix", thread="$tent")

    assert [len(call.get("images") or []) for call in room.model.calls[asked:]] == [2]


async def test_a_reply_to_a_photo_note_that_changes_its_title_is_given_to_the_model(room, memory):
    """Given Marge's photo, filed as a note that shows a summary, when she replies with a new title, then
    the model reads her reply with the photo, and the card takes the title it answers."""
    room.model.answers.append({**FILED, "title": "Bart's tent"})
    path, _ = await a_photo(room, memory, "tent.png", "$tent")
    asked = len(room.model.calls)

    await room.says(MARGE, "Call it Bart's tent", event_id="$fix", thread="$tent")

    [call] = room.model.calls[asked:]
    assert "Call it Bart's tent" in (call.get("user_hint") or "")
    assert call.get("images")
    assert parse(memory.text(path))["title"] == "Bart's tent"


# ── The form is read from the card ────────────────────────────────────

LONG = " ".join(f"Bart wants tent number {n} for the long camping trip." for n in range(1, 41))[:2100]


async def test_a_clean_version_of_2100_characters_is_still_a_clean_version_after_a_correction(room, memory):
    """Given a note whose card shows a clean version of 2,100 characters, when Marge replies in its
    thread, then the card still shows a clean version (no summary callout)."""
    assert len(LONG) == 2100
    notice = await room.has_a_note_of(LONG)
    path, _, text = the_card_of(room, memory, notice)
    assert "> [!summary]" not in text.split("## Sources", 1)[0]

    await room.says(MARGE, "Call it Camping tents", event_id="$fix", thread="$first")

    _, _, text = the_card_of(room, memory, room.notices()[-1])
    assert "> [!summary]" not in text.split("## Sources", 1)[0]


# ── A change lands while the model answers ────────────────────────────

async def test_a_keyword_lisa_added_before_marges_reply_stays(room, memory):
    """Given Marge's note that Lisa tagged `bbq` in Forgejo, when Marge replies in its thread, then the
    card still has the keyword. The control of the next test: the same edit, a moment earlier."""
    path = await a_filed_grill_note(room, memory)
    lisa_adds_the_tag(memory, path)

    await room.says(MARGE, "Call it Charcoal and propane", event_id="$fix", thread="$grill")

    assert LISAS_TAG in tags_of(memory, path)


async def test_a_keyword_lisa_adds_while_the_model_answers_marges_reply_is_not_lost(room, memory):
    """Given Marge's note, when Lisa adds the keyword `bbq` in Forgejo while the model answers Marge's
    reply, then the card still has the keyword (the reply is written on top of it, or refused)."""
    path = await a_filed_grill_note(room, memory)
    room.model.meanwhile = lambda: lisa_adds_the_tag(memory, path)

    await room.says(MARGE, "Call it Charcoal and propane", event_id="$fix", thread="$grill")

    assert LISAS_TAG in tags_of(memory, path)


async def test_a_sidecar_lisa_edits_while_the_model_answers_keeps_her_edit(room, memory):
    """Given Marge's note, when Lisa corrects in Forgejo the time its sidecar gives the message while the
    model answers Marge's reply, then the sidecar keeps Lisa's time: a write made on the sidecar as it
    was read would put the old time back."""
    path = await a_filed_grill_note(room, memory)
    lisas_time = "2025-11-21T19:30:00+00:00"
    room.model.meanwhile = lambda: the_sidecar_is_edited_in_forgejo(memory, path, lambda side: {
        **side, "sources": [{**side["sources"][0], "time": lisas_time}, *side["sources"][1:]]})

    await room.says(MARGE, "Call it Charcoal and propane", event_id="$fix", thread="$grill")

    assert sidecar(memory, path)["sources"][0]["time"] == lisas_time


# ── The card's history cannot be read ─────────────────────────────────

async def test_a_reply_while_the_history_cannot_be_read_writes_nothing_and_loses_no_edit(
        tmp_path, memory, httpserver):
    """Given Marge's note that Lisa tagged, when the curator cannot give the card's history (a server
    fault) and Marge replies, then nothing is written, Lisa's keyword stays, and the reply gets the failed mark."""
    api = HistoryDown.from_container(memory.environ)
    chat = ChatRoom(tmp_path, memory, httpserver, ModelAnswers(FILED), memory_api=api)
    try:
        path = await a_filed_grill_note(chat, memory)
        lisa_adds_the_tag(memory, path)
        api.unreadable = True
        commits = len(memory.authors())

        await chat.says(MARGE, "Call it Charcoal and propane", event_id="$fix", thread="$grill")

        assert LISAS_TAG in tags_of(memory, path) and len(memory.authors()) == commits
        assert chat.reactions_on("$fix") == [FAILED_MARK]
    finally:
        await chat.close()


# ── A reply is a source by its id ─────────────────────────────────────

async def test_a_second_reply_with_the_same_words_is_a_second_source(room, memory):
    """Given Marge replied "Call it Barbecue" in the thread, when she sends the same words again as
    another message, then the sidecar holds the original and both replies."""
    path = await a_filed_grill_note(room, memory)
    await room.says(MARGE, "Call it Barbecue", event_id="$fix1", thread="$grill")

    await room.says(MARGE, "Call it Barbecue", event_id="$fix2", thread="$grill")

    ids = [s["id"] for s in sidecar(memory, path)["sources"]]
    assert ids == ["$grill", "$fix1", "$fix2"]


async def test_the_same_reply_handed_over_twice_is_one_source(room, memory):
    """Given Marge's reply was handled, when the drain hands the archivist the same event again, then
    the note holds the reply once."""
    path = await a_filed_grill_note(room, memory)
    reply = await room.says(MARGE, "Call it Barbecue", event_id="$fix", thread="$grill")

    await room.bot._on_text(room.room, reply)

    assert [s["id"] for s in sidecar(memory, path)["sources"]] == ["$grill", "$fix"]


# ── A reply to a reply, two replies at once ───────────────────────────

async def test_a_reply_to_the_archivists_answer_to_a_reply_is_a_second_source_in_order(room, memory):
    """Given Marge corrected her note and the archivist answered, when Lisa replies to that answer,
    then the sidecar holds the original, Marge's reply and Lisa's reply, in that order."""
    path = await a_filed_grill_note(room, memory)
    await room.says(MARGE, "Call it Barbecue", event_id="$fix1", thread="$grill")
    answer = room.notices()[-1]

    await room.says("@lisa:simpson", "Also the charcoal is for Saturday", event_id="$fix2",
                    extra={"m.relates_to": {"m.in_reply_to": {"event_id": answer.event_id}}})

    assert [s["id"] for s in sidecar(memory, path)["sources"]] == ["$grill", "$fix1", "$fix2"]


async def test_two_replies_at_once_leave_no_reply_the_room_called_taken_out_of_the_sources(
        tmp_path, memory, httpserver):
    """Given Marge's note, when Marge and Lisa reply in its thread at the same moment, then every reply
    that got the done mark is a source of the note, and the other got the failed mark."""
    model = ModelThatIsInterrupted(FILED, slow=0.05)
    chat = ChatRoom(tmp_path, memory, httpserver, model)
    try:
        path = await a_filed_grill_note(chat, memory)

        await asyncio.gather(
            chat.says(MARGE, "Call it Barbecue", event_id="$fix1", thread="$grill"),
            chat.says("@lisa:simpson", "Add the lighter fluid", event_id="$fix2", thread="$grill"))

        held = {s["id"] for s in sidecar(memory, path)["sources"]}
        for reply in ("$fix1", "$fix2"):
            assert (reply in held) == (chat.reactions_on(reply)[-1:] == [FILED_MARK])
    finally:
        await chat.close()


# ── Who filed the note stays who filed it ─────────────────────────────

async def test_lisas_reply_in_marges_note_leaves_marge_as_the_one_who_filed_it(room, memory):
    """Given Marge's note, when Lisa replies in its thread, then the sidecar still says Marge filed it,
    and the commit of the reply is Lisa's."""
    path = await a_filed_grill_note(room, memory)

    await room.says("@lisa:simpson", "Also the charcoal is for Saturday", event_id="$fix", thread="$grill")

    assert sidecar(memory, path)["filed_by"] == "marge"
    assert memory.authors()[-1].startswith("lisa ")


# ── A sidecar the refile cannot rebuild from ──────────────────────────

async def a_reply_writes_nothing(chat, memory, path: str) -> None:
    before, commits = memory.text(path), len(memory.authors())

    await chat.says(MARGE, "Call it Charcoal and propane", event_id="$fix", thread="$grill")

    assert memory.text(path) == before and len(memory.authors()) == commits
    assert chat.reactions_on("$fix") == [FAILED_MARK]


async def test_a_reply_in_a_note_whose_sidecar_names_a_message_the_card_does_not_quote_writes_nothing(
        room, memory):
    """Given Marge's note whose sidecar names a second message that its card does not quote, when Marge
    replies in its thread, then nothing is written and the reply gets the failed mark: the card and its
    sources do not tell the same story, and a rebuild from either would lose what the other holds."""
    path = await a_filed_grill_note(room, memory)
    the_sidecar_is_edited_in_forgejo(memory, path, lambda side: {
        **side, "sources": side["sources"] + [{**side["sources"][0], "id": "$more"}]})

    await a_reply_writes_nothing(room, memory, path)


async def test_a_reply_in_a_note_whose_sidecar_holds_no_original_writes_nothing(room, memory):
    """Given Marge's note whose sidecar holds no message she sent, only a note, when Marge replies in its
    thread, then nothing is written and the reply gets the failed mark: there is nothing to rebuild from."""
    path = await a_filed_grill_note(room, memory)
    the_sidecar_is_edited_in_forgejo(memory, path, lambda side: {
        **side, "sources": [{**source, "role": "note"} for source in side["sources"]]})

    await a_reply_writes_nothing(room, memory, path)


# ── A correction that names nobody keeps the note's people ────────────

async def test_a_reply_that_fixes_only_the_title_keeps_the_people_the_note_is_about(
        tmp_path, memory, httpserver):
    """Given Marge's note about Bart, when she replies with a new title and the model's answer names
    nobody, then the note is still about Bart: a correction changes what it names, and a person the
    note was about is not dropped for a reply that did not mention them."""
    about_bart = {**FILED, "persons": ["Bart"]}
    retitled = {"title": "Charcoal and propane", "persons": [], "tags": [], "summary": ""}
    chat = ChatRoom(tmp_path, memory, httpserver, ModelAnswers(about_bart, retitled))
    try:
        path = await a_filed_grill_note(chat, memory)
        assert parse(memory.text(path))["persons"] == ["bart"]

        await chat.says(MARGE, "Call it Charcoal and propane", event_id="$fix", thread="$grill")

        card = parse(memory.text(path))
        assert card["title"] == "Charcoal and propane"
        assert card["persons"] == ["bart"]
    finally:
        await chat.close()
