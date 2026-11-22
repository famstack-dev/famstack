"""What the archivist files when Marge sends something to her capture room, and how it answers.

A person hands famstack something to keep: a pasted text, a voice message,
a photo, a PDF, a link. The archivist files each as one card in the family
memory, on the version of the memory it found, and answers in the room
with the card at a glance. A reply in the thread of that answer corrects
the card and keeps what was sent.

These tests start where a person does: a Matrix event given to the
archivist as the bot runner loads it (`ChatRoom`, `tests/archivist_chat.py`).
Filing runs through the real pipeline into the real curator (`running_memory`).
What a test asserts is what Marge sees (the notice and the reactions in the
room) and what the memory holds (the card, its sources, its commits). The
model, the homeserver, the media repository and the web stand in at the
boundary. The household is the Simpsons.

A row of the wanted behavior that does not hold yet is marked with
`THE_FILING_IS_ONE_PATH` or `A_REFILE_READS_ALL_ITS_SOURCES`: each is a
strict `xfail`, so the test fails the day the behavior arrives, and the
marker is removed then.
"""

from __future__ import annotations

import json

import pytest

from stack.frontmatter import parse
from tests.archivist_chat import FAILED_MARK, FILED_MARK, MARGE, PIXEL, ChatRoom, ModelAnswers, ModelDown, Pages, a_pdf
from tests.loopback import a_closed_port
from tests.memory.unit.instance import git, running_memory
from tests.model import Model

PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
          for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"),
                            ("bart", "Bart Simpson"), ("lisa", "Lisa Simpson"))}
# What the filing core makes true: one path for every capture, one card per thing sent.
THE_FILING_IS_ONE_PATH = pytest.mark.xfail(strict=True, reason=(
    "two notes that carry the same link stay two cards, because a note is "
    "identified by what was sent and not by the page it names"))
# What the refile makes true: a refile reads every message a note is made of.
A_REFILE_READS_ALL_ITS_SOURCES = pytest.mark.xfail(strict=True, reason=(
    "a correction or a refile of a note made of several messages keeps every "
    "message and adds the correction"))

GRILL = ("The grill needs new charcoal before Saturday, and the propane tank is nearly empty "
         "too, so buy both on the way home.")
FILED = {"title": "Grill charcoal", "persons": ["Marge"], "tags": [], "summary": "Charcoal and propane."}


@pytest.fixture
def memory(tmp_path, monkeypatch):
    """The Simpsons' memory, and the media store the archivist keeps uploads in."""
    with running_memory(tmp_path / "data", PEOPLE, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(memory.media))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


@pytest.fixture
async def chat(tmp_path, memory, httpserver):
    room = ChatRoom(tmp_path, memory, httpserver, ModelAnswers(FILED), pages=Pages(
        **{"https://krusty.test/tent": "Krusty Burger tent sale: four-person tents, half price."}))
    yield room
    await room.close()


def cards(memory, folder: str) -> list[str]:
    """The card files in Forgejo under `folder` (their sidecars are not cards)."""
    listed = git(memory.forgejo.repo, "ls-tree", "-r", "--name-only", "main").splitlines()
    return [p for p in listed if p.startswith(folder) and p.endswith(".md")]


def sidecar(memory, card: str) -> dict:
    """The JSON beside a card: what it was made from (ADR-018)."""
    return json.loads(memory.text(card.removesuffix(".md") + ".json"))


def the_card_of(chat, memory, notice) -> tuple[str, dict, str]:
    """The card a notice names: its path, its parsed head and its text."""
    path = chat.envelope(notice)["data"]["vault_path"]
    text = memory.text(path)
    return path, parse(text), text


# ── What Marge sends ─────────────────────────────────────────────────────

async def test_a_pasted_text_is_one_note_with_her_words_as_its_source(chat, memory):
    """Given Marge pastes a text in her own capture room, then the memory
    has one note, filed by her, whose one source is her words with her
    event id and the time she sent them, and the room shows its card in
    one answer, under the filed mark on her message."""
    sent = await chat.says(MARGE, GRILL, event_id="$grill")

    [notice] = chat.notices()
    path, head, text = the_card_of(chat, memory, notice)
    assert head["type"] == "note" and cards(memory, "marge/") == [path]
    assert chat.envelope(notice)["data"]["card_id"] == head["card_id"]
    assert GRILL in text.split("## Sources", 1)[1]
    [source] = sidecar(memory, path)["sources"]
    assert (source["person"], source["id"], source["kind"], source["channel"]) == (
        "marge", "$grill", "text", "chat")
    assert source["time"].startswith("2025-11-22")
    assert chat.reactions_on(sent.event_id) == [FILED_MARK]
    assert chat.thread_of(notice) == "$grill"


async def test_a_voice_message_is_one_note_with_its_transcript_as_its_source(chat, memory):
    """Given Marge sends a voice message that was transcribed, then the
    memory has one note whose source is the transcript, of the kind
    `voice`, and the answer quotes the words so she can catch a
    mishearing."""
    spoken = "Remind me to call the dentist about Lisa's braces on Monday morning."
    await chat.speaks(MARGE, spoken, event_id="$memo")

    [notice] = chat.notices()
    path, head, text = the_card_of(chat, memory, notice)
    assert head["type"] == "note"
    assert spoken in text.split("## Sources", 1)[1]
    [source] = sidecar(memory, path)["sources"]
    assert (source["id"], source["kind"]) == ("$memo", "voice")
    assert spoken in notice.source["content"]["body"]


async def test_a_photo_with_a_caption_is_one_note_that_keeps_the_picture_and_the_caption(
        chat, memory):
    """Given Marge sends a photo with a caption, then the memory has one
    note; the photo is in the media archive and embedded in the note's
    source, and her caption is kept as she wrote it."""
    sent = await chat.sends_file(MARGE, PIXEL, "tent.png", msgtype="m.image", mimetype="image/png",
                                 caption="The tent Bart wants for the camping trip", event_id="$tent")

    [notice] = chat.notices()
    path, head, text = the_card_of(chat, memory, notice)
    assert head["type"] == "note" and cards(memory, "marge/") == [path]
    assert "The tent Bart wants for the camping trip" in text
    assert "![" in text.split("## Sources", 1)[1]
    [source] = sidecar(memory, path)["sources"]
    assert (source["id"], source["kind"]) == ("$tent", "image")
    assert any(f.suffix == ".png" for f in memory.media.rglob("*"))
    assert chat.reactions_on(sent.event_id) == [FILED_MARK]


async def test_a_pdf_is_one_note_with_its_text_and_the_file_kept(chat, memory):
    """Given Marge sends a PDF with a text layer, then the memory has one
    note with the PDF's text, and the file is kept in the media archive."""
    await chat.sends_file(MARGE, a_pdf("Permission slip for the zoo trip on Friday"), "slip.pdf",
                          mimetype="application/pdf", event_id="$slip")

    [notice] = chat.notices()
    path, head, text = the_card_of(chat, memory, notice)
    assert head["type"] == "note" and cards(memory, "marge/") == [path]
    assert "Permission slip for the zoo trip" in text
    assert any(f.suffix == ".pdf" for f in memory.media.rglob("*"))
    assert sidecar(memory, path)["sources"][0]["id"] == "$slip"


async def test_a_link_is_one_bookmark_and_its_page_is_read_once(chat, memory):
    """Given Marge sends a link, then the memory has one bookmark of that
    address, and the page was fetched once."""
    sent = await chat.says(MARGE, "https://krusty.test/tent", event_id="$link")

    [notice] = chat.notices()
    path, head, _ = the_card_of(chat, memory, notice)
    assert head["type"] == "bookmark" and cards(memory, "marge/") == [path]
    assert "krusty.test/tent" in memory.text(path)
    assert chat.pages.reads == ["https://krusty.test/tent"]
    assert chat.reactions_on(sent.event_id) == [FILED_MARK]


async def test_a_link_with_her_words_is_a_bookmark_that_keeps_them_as_its_hint(chat, memory):
    """Given Marge sends "look at this gear list <link>", then the memory
    has one bookmark, and the model was shown her words as the hint."""
    await chat.says(MARGE, "look at this gear list https://krusty.test/tent", event_id="$gear")

    [notice] = chat.notices()
    path, head, _ = the_card_of(chat, memory, notice)
    assert head["type"] == "bookmark" and cards(memory, "marge/") == [path]
    assert chat.model.calls[-1]["user_hint"] == "look at this gear list"


async def test_the_same_event_handed_over_twice_is_one_card(chat, memory):
    """Given the drain hands the archivist the same event twice (a crash
    before its cursor moved), then the memory has one card."""
    sent = await chat.says(MARGE, GRILL, event_id="$grill")
    await chat.bot._on_text(chat.room, sent)

    assert len(cards(memory, "marge/")) == 1


# ── A note and its link are two cards ────────────────────────────────────

async def test_a_note_that_carries_a_link_and_the_bookmark_of_that_link_are_two_cards(chat, memory):
    """Given Marge's bookmark of a page exists, when a note whose text
    carries the page's address is filed, then the memory has the note and
    the bookmark, two cards, and the bookmark is as it was.

    The chat reads a message with a link in it as a bookmark, so the note
    comes in through the pipeline, as the command line files one."""
    await chat.says(MARGE, "https://krusty.test/tent", event_id="$link")
    [bookmark] = cards(memory, "marge/")
    before = memory.text(bookmark)

    await chat.pipeline.capture_text(
        text="Bart wants the tent on https://krusty.test/tent for the camping trip next month.",
        sender_mxid=MARGE, capture_id="cli-note-1")

    assert len(cards(memory, "marge/")) == 2
    assert memory.text(bookmark) == before


@THE_FILING_IS_ONE_PATH
async def test_two_notes_that_carry_the_same_link_are_two_cards(chat, memory):
    """Given a note whose text carries a page's address, when Marge files a
    second note about the same page in other words, then the memory has
    both notes: a note is identified by what was sent, not by the page it
    names."""
    for n, words in enumerate(("Bart wants the tent on https://krusty.test/tent for the camping trip.",
                               "Lisa found https://krusty.test/tent too and says it is better value.")):
        await chat.pipeline.capture_text(text=words, sender_mxid=MARGE, capture_id=f"cli-note-{n}")

    assert len(cards(memory, "marge/")) == 2


async def test_pinning_a_message_that_is_already_a_card_files_no_second_card(chat, memory):
    """Given Marge's pasted text is a card, when she pins the same message
    with 📌, then the memory still has one card, even when the model now
    gives it another title."""
    sent = await chat.says(MARGE, GRILL, event_id="$grill")
    assert len(cards(memory, "marge/")) == 1
    chat.model.answers.append({**FILED, "title": "Something else"})

    await chat.reacts(MARGE, "\U0001F4CC", to=sent.event_id)

    assert len(cards(memory, "marge/")) == 1


async def test_retrying_a_photo_whose_filing_failed_files_it_once(chat, memory):
    """Given Marge's photo could not be read from the media repository, so
    nothing was filed, when she reacts 🔁 after the file is there, then the
    memory has one note of the photo."""
    photo = await chat.sends_file(MARGE, PIXEL, "tent.png", msgtype="m.image", mimetype="image/png",
                                  event_id="$tent", available=False)
    assert cards(memory, "marge/") == []

    chat.serves(photo)
    await chat.reacts(MARGE, "\U0001F501", to=photo.event_id)

    [path] = cards(memory, "marge/")
    assert sidecar(memory, path)["sources"][0]["id"] == "$tent"
    assert chat.reactions_on(photo.event_id) == [FILED_MARK]


# ── A reply in the thread of the notice corrects the note ────────────────

async def test_a_reply_in_the_notices_thread_corrects_the_note_and_keeps_what_was_sent(chat, memory):
    """Given Marge's note and its notice, when she replies in the notice's
    thread asking for another title, then the note has the new title, its
    sources are the original and her reply with its time, the correction
    is Marge's commit, and the room has a notice in the same thread."""
    chat.model.answers.append({**FILED, "title": "Charcoal and propane"})
    await chat.says(MARGE, GRILL, event_id="$grill")
    [notice] = chat.notices()
    commits = len(memory.authors())

    reply = await chat.says(MARGE, "Call it Charcoal and propane", event_id="$fix", thread="$grill")

    [_, corrected] = chat.notices()
    path, head, text = the_card_of(chat, memory, corrected)
    assert head["title"] == "Charcoal and propane"
    assert head["card_id"] == chat.envelope(notice)["data"]["card_id"]
    original, reply_source = sidecar(memory, path)["sources"]
    assert (original["role"], original["id"]) == ("original", "$grill")
    assert (reply_source["role"], reply_source["person"], reply_source["id"]) == ("reply", "marge", "$fix")
    assert reply_source["time"].startswith("2025-11-22")
    assert GRILL in text
    assert memory.authors()[commits:] == ["marge <marge@simpson>"]
    assert chat.thread_of(corrected) == "$grill"
    assert reply.event_id == "$fix"


async def test_a_reply_to_a_note_a_person_edited_by_hand_keeps_the_edit(chat, memory):
    """Given Marge's note, which Lisa then edited in Forgejo by adding a
    line of her own, when Marge replies in the notice's thread, then the
    card still has Lisa's line, and Marge's reply is a source."""
    chat.model.answers.append({**FILED, "title": "Charcoal and propane"})
    await chat.says(MARGE, GRILL, event_id="$grill")
    [notice] = chat.notices()
    path, _, text = the_card_of(chat, memory, notice)
    memory.edited_in_forgejo(
        {path: text.replace("## Sources", "Lisa: the lighter fluid is gone too.\n\n## Sources", 1)},
        by="lisa")

    await chat.says(MARGE, "Call it Charcoal and propane", event_id="$fix", thread="$grill")

    [_, corrected] = chat.notices()
    _, _, text = the_card_of(chat, memory, corrected)
    assert "Lisa: the lighter fluid is gone too." in text
    assert [s["role"] for s in sidecar(memory, path)["sources"]] == ["original", "reply"]


# ── A note made of several messages ──────────────────────────────────────

THREE = ("The grill needs new charcoal before Saturday and the propane tank is nearly empty too.",
         "Also the neighbours bring the folding chairs.",
         "Lisa will make the salad.")


@A_REFILE_READS_ALL_ITS_SOURCES
async def test_a_correction_of_a_note_made_of_three_messages_keeps_all_three(chat, memory):
    """Given a note made of three messages, when Marge replies in its
    thread, then the rebuilt note's text has all three messages, and its
    sources are the three and the reply."""
    notice = await chat.has_a_note_of(*THREE)
    chat.model.answers.append({**FILED, "title": "Barbecue on Saturday"})

    await chat.says(MARGE, "Call it Barbecue on Saturday", event_id="$fix", thread="$first")

    path, head, text = the_card_of(chat, memory, chat.notices()[-1])
    assert head["title"] == "Barbecue on Saturday"
    note = text.split("## Sources", 1)[0]
    assert all(words in note for words in THREE)
    assert len(sidecar(memory, path)["sources"]) == 4 and notice


# ── When the model or the memory does not answer ─────────────────────────

async def test_a_text_sent_while_the_model_is_down_is_filed_with_her_words(
        tmp_path, memory, httpserver):
    """Given the model times out, when Marge sends a text, then the note is
    filed with her words as its source, and the room shows it. A correction
    afterwards is refused today: nothing is written, and the room says so."""
    chat = ChatRoom(tmp_path, memory, httpserver, ModelDown())
    try:
        await chat.says(MARGE, GRILL, event_id="$grill")
        [notice] = chat.notices()
        path, head, text = the_card_of(chat, memory, notice)
        assert head["type"] == "note" and GRILL in text.split("## Sources", 1)[1]
        assert chat.reactions_on("$grill") == [FILED_MARK]
        commits = len(memory.authors())

        await chat.says(MARGE, "Call it Charcoal and propane", event_id="$fix", thread="$grill")

        assert len(memory.authors()) == commits and parse(memory.text(path)) == head
        assert chat.reactions_on("$fix") == [FAILED_MARK]
    finally:
        await chat.close()


async def test_a_text_the_memory_refuses_leaves_no_card_and_says_it_was_not_filed(
        tmp_path, memory, httpserver):
    """Given the curator does not answer, when Marge sends a text, then no
    card exists, the message gets the failed mark, and the room's answer carries no
    filing."""
    from stack.memory import MemoryAPI
    chat = ChatRoom(tmp_path, memory, httpserver, ModelAnswers(FILED), memory_api=MemoryAPI(
        f"http://127.0.0.1:{a_closed_port()}", token="t", where="the curator"))
    try:
        await chat.says(MARGE, GRILL, event_id="$grill")

        assert cards(memory, "marge/") == []
        assert chat.reactions_on("$grill") == [FAILED_MARK]
        [notice] = chat.notices()
        assert chat.envelope(notice) == {}
    finally:
        await chat.close()


# ── The command line files the same way ──────────────────────────────────
# `stack memory capture` and `stack memory reprocess` run the archivist's
# pipeline in the bot runner, in a process of their own. The model is an
# HTTP server for them, the page of a link and Paperless' tags too.

@pytest.fixture
def model(httpserver) -> Model:
    return Model(httpserver)


@pytest.fixture
def memory_for_commands(tmp_path, httpserver, model, monkeypatch):
    """The Simpsons' memory, with the model and Paperless where the bot runner reaches them."""
    httpserver.expect_request("/paperless/api/tags/").respond_with_json({"results": []})
    with running_memory(tmp_path / "data", PEOPLE, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons", model=model.url,
                        paperless=httpserver.url_for("/paperless")) as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


async def test_the_same_text_through_the_command_line_is_the_same_note(memory_for_commands, model):
    """Given the text Marge pasted in the chat, when she files it with
    `stack memory capture`, then the memory has the same kind of note: her
    words as its one source, now with the command line's own id (`cli:`) and its
    channel `cli`."""
    memory = memory_for_commands
    model.answers({**FILED, "clean": GRILL})

    answer = memory.person("marge").runs("memory", "capture", [GRILL, "--by", "marge"])

    assert "error" not in answer, answer
    [path] = cards(memory, "marge/")
    assert parse(memory.text(path))["type"] == "note"
    assert GRILL in memory.text(path).split("## Sources", 1)[1]
    [source] = sidecar(memory, path)["sources"]
    assert source["id"].startswith("cli:") and (source["channel"], source["kind"]) == ("cli", "text")
    assert source["person"] == "marge"


@A_REFILE_READS_ALL_ITS_SOURCES
async def test_a_refile_of_a_note_made_of_three_messages_reads_all_three(
        tmp_path, memory_for_commands, model, httpserver):
    """Given a note made of three messages, when `stack memory reprocess`
    refiles it with a correction, then the model is asked to correct a note
    that holds all three messages (the first request it gets)."""
    memory = memory_for_commands
    chat = ChatRoom(tmp_path, memory, httpserver, ModelAnswers(FILED))
    try:
        notice = await chat.has_a_note_of(*THREE)
    finally:
        await chat.close()
    card_id = chat.envelope(notice)["data"]["card_id"]
    model.answers({**FILED, "title": "Barbecue on Saturday", "clean": " ".join(THREE)})

    memory.person("marge").refiles(card_id, correction="Call it Barbecue on Saturday")

    [correction_prompt] = model.requests[0]
    assert all(words in correction_prompt["content"] for words in THREE)
