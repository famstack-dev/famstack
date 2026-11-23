"""What the archivist does with each message sent while a scan is open.

A person types `(`, sends the pages of a letter as photos, and types `)`:
the photos are filed as one document. Between `(` and `)` the same person
can send other messages too, a line of text or a voice message. Each of
them is filed as it is outside a scan, and gets a reply, because a message
that nobody answers reads as taken. A typed line during a scan is handed to
the archivist on purpose, as a voice message is, so it is kept as a note
even when it is short, and the reply says that the scan is still open.

These tests drive the archivist's message handlers, as the bot runner
calls them, through its real capture pipeline and mirror into the curator
of memory's test instance (`running_memory`). A voice message arrives as
the framework hands it on with speech on: a text event that carries its
transcript (`stack.bot.voice`). Paperless and the model are stand-ins:
the scan's document is recorded where it would go to Paperless, and the
classifier names a title. The Matrix side records what the archivist sent.
The household is the Simpsons.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from stack.bot import voice
from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.capture_pipeline import CapturePipeline
from stacklets.docs.bot.extractors import TextExtractor
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.vault_context import VaultContext
from tests.memory.unit.instance import running_memory

BOT_ID = "@archivist-bot:simpson"
HOMER = "@homer:simpson"
FILES = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
         for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"),
                           ("bart", "Bart Simpson"))}
# Short, so outside a scan a typed line like it is chat and is not kept.
LINE = "Bart needs new shoes"
SPOKEN = "Pick up the dry cleaning on Friday"


def _photo() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return buffer.getvalue()


class Classifier:
    """The model's stand-in: it names each note after its first words."""

    async def classify_capture(self, *, text="", **_):
        return {"title": text.split(".")[0][:40], "persons": [], "tags": [], "summary": "s"}


@pytest.fixture
def memory(tmp_path, monkeypatch):
    with running_memory(tmp_path / "data", FILES, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


@pytest.fixture
async def archivist(tmp_path, memory):
    """The archivist with its real capture pipeline and mirror. It records
    what it sends, reacts and answers, and what its scan hands to Paperless.
    Its own network session (the room settings it asks for) closes after
    the test, as the bot closes it on shutdown."""
    vault = VaultContext(language="en", shared_bucket="family")
    mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                       memory=MemoryAPI.from_container(memory.environ))
    capture = CapturePipeline(
        url_extractor=None, text_extractor=TextExtractor(), classifier=Classifier(),
        mirror=mirror, paperless=None, bot_name="archivist-bot",
        classify_max_chars=10000, capture_keep_body=False,
        vault=vault)
    bot = ArchivistBot(homeserver="http://h", user_id=BOT_ID, password="x",
                       session_dir=tmp_path / "session",
                       services=SimpleNamespace(capture=capture, vault=vault))
    said: list[tuple[str, str]] = []
    documents: list[dict] = []
    searched: list[tuple[str, str]] = []

    async def _quiet(*_args, **_kwargs):
        return None

    async def _download(url):
        return _photo()

    async def _to_paperless(room_id, filename, display_name, data, reply_to, **kw):
        documents.append({"filename": filename, "data": data, "reply_to": reply_to,
                          "hint": kw.get("user_hint")})

    async def _search(room_id, query, reply_to=None, **_):
        searched.append((query, reply_to))

    async def _send(room_id, text, reply_to=None, *a, **kw):
        said.append(("send", reply_to))

    async def _answer(room_id, text, reply_to=None, *a, **kw):
        said.append(("answer", reply_to))

    async def _react(room_id, event_id, key, *a, **kw):
        said.append(("react", event_id))

    bot._send, bot._answer, bot._react = _send, _answer, _react
    bot._rooms.welcome_if_needed = _quiet
    bot._set_typing = _quiet
    bot._download_media = _download
    bot._documents.file = _to_paperless
    # Search asks Paperless, a service this lane does not run.
    bot._search.search = _search
    yield SimpleNamespace(bot=bot, said=said, documents=documents, searched=searched)
    await bot._aclose()


ROOM = SimpleNamespace(room_id="!notes:simpson", canonical_alias=None, name="Homer's notes",
                       users={BOT_ID: object(), HOMER: object()})
DOCUMENTS = SimpleNamespace(room_id="!documents:simpson", canonical_alias="#documents:simpson",
                            name="Documents", users={BOT_ID: object(), HOMER: object()})
FAMILY = SimpleNamespace(room_id="!family:simpson", canonical_alias=None, name="Family Room",
                         users={BOT_ID: object(), HOMER: object(), "@marge:simpson": object()})


def _text(event_id: str, body: str, *, spoken: bool = False):
    content: dict = {"msgtype": "m.text", "body": body}
    if spoken:
        content[voice.TRANSCRIPT_KEY] = {"source_msgtype": "m.audio", "url": "mxc://simpson/v1"}
    return SimpleNamespace(body=body, sender=HOMER, event_id=event_id,
                           server_timestamp=1_763_900_000_000, source={"content": content})


def _photo_event(event_id: str, caption: str = ""):
    content = {"msgtype": "m.image", "body": caption or "IMG_0412.jpg", "url": "mxc://simpson/p1"}
    if caption:
        content["filename"] = "IMG_0412.jpg"
    return SimpleNamespace(body="IMG_0412.jpg", sender=HOMER, event_id=event_id,
                           server_timestamp=1_763_900_000_000, source={"content": content})


def _notes(memory) -> dict[str, dict]:
    return {path: parse(text) for path, text in memory.pages("homer/notes").items()}


def _replied_to(archivist, event_id: str) -> list[str]:
    return [kind for kind, reply_to in archivist.said if reply_to == event_id]


async def test_a_line_and_a_voice_message_in_a_scan_are_notes_and_the_photo_is_its_document(
        archivist, memory):
    """Given an open scan, when Homer sends a photo, a line and a voice
    message, then `)` files the photo as one document; the line and the
    voice message are each a note in Homer's notes, and each message got
    a reply. Before, the line was filed nowhere and nobody answered it."""
    bot = archivist.bot
    await bot._on_text(ROOM, _text("$open", "("))
    await bot._on_file(ROOM, _photo_event("$page"))
    await bot._on_text(ROOM, _text("$line", LINE))
    await bot._on_text(ROOM, _text("$voice", SPOKEN, spoken=True))
    await bot._on_text(ROOM, _text("$close", ")"))

    [document] = archivist.documents
    assert document["data"] == _photo() and document["reply_to"] == "$close"
    bodies = sorted(text for text in memory.pages("homer/notes").values())
    assert len(bodies) == 2
    assert any(LINE in body for body in bodies) and any(SPOKEN in body for body in bodies)
    for event_id in ("$open", "$page", "$line", "$voice", "$close"):
        assert _replied_to(archivist, event_id), event_id


async def test_the_reply_to_a_line_says_the_scan_is_still_open(archivist, memory):
    """Given an open scan, when Homer sends a line, then besides the note's
    reply the archivist says the scan is open, and the scan takes the next
    photo as its page: the line did not close it."""
    bot = archivist.bot
    await bot._on_text(ROOM, _text("$open", "("))
    await bot._on_text(ROOM, _text("$line", LINE))

    assert bot._scan.is_open(HOMER)
    assert "send" in _replied_to(archivist, "$line")
    await bot._on_file(ROOM, _photo_event("$page"))
    await bot._on_text(ROOM, _text("$close", ")"))
    assert len(archivist.documents) == 1


async def test_outside_a_scan_a_short_line_stays_chat(archivist, memory):
    """Given no open scan, when Homer types the same short line, then it is
    not kept: only a scan makes a short line a handover."""
    await archivist.bot._on_text(ROOM, _text("$line", LINE))

    assert _notes(memory) == {}
    assert archivist.said == []


async def test_a_scan_of_photos_only_is_filed_as_one_document(archivist, memory):
    """Given an open scan, when Homer sends two photos and `)`, then they
    are one document in Paperless, a PDF of two pages, and no note."""
    bot = archivist.bot
    await bot._on_text(ROOM, _text("$open", "("))
    await bot._on_file(ROOM, _photo_event("$p1"))
    await bot._on_file(ROOM, _photo_event("$p2"))
    await bot._on_text(ROOM, _text("$close", ")"))

    [document] = archivist.documents
    assert document["filename"].endswith("-2p.pdf") and document["data"].startswith(b"%PDF")
    assert _notes(memory) == {}


async def test_in_the_documents_room_a_line_during_a_scan_is_a_search(archivist, memory):
    """Given a scan open in the Documents Room, when Homer types a line,
    then it is a search, as outside a scan, and no note: in the Documents
    Room any text is a search. The scan still files its photo."""
    bot = archivist.bot
    await bot._on_text(DOCUMENTS, _text("$open", "("))
    await bot._on_file(DOCUMENTS, _photo_event("$page"))
    await bot._on_text(DOCUMENTS, _text("$line", LINE))
    await bot._on_text(DOCUMENTS, _text("$close", ")"))

    assert archivist.searched == [(LINE, "$line")]
    assert _notes(memory) == {}
    assert "send" not in _replied_to(archivist, "$line")
    assert len(archivist.documents) == 1


async def test_a_line_in_another_room_is_read_as_if_no_scan_were_open(archivist, memory):
    """Given Homer's scan open in his notes room, when he writes a short
    line in the family room, then it stays chat there: no note and no
    "still open" reply. A scan counts only in the room where it was opened."""
    bot = archivist.bot
    await bot._on_text(ROOM, _text("$open", "("))
    await bot._on_text(FAMILY, _text("$line", LINE))

    assert _notes(memory) == {}
    assert _replied_to(archivist, "$line") == []
    assert bot._scan.is_open(HOMER)


async def test_a_caption_on_a_page_stays_the_pages_caption(archivist, memory):
    """Given an open scan, when Homer sends a photo with a caption, then
    the caption reaches the document's filing as its hint, and no note is
    made of it: a caption belongs to its page, not to the lines between."""
    bot = archivist.bot
    await bot._on_text(ROOM, _text("$open", "("))
    await bot._on_file(ROOM, _photo_event("$page", caption="new passports"))
    await bot._on_text(ROOM, _text("$close", ")"))

    [document] = archivist.documents
    assert document["hint"] == "new passports"
    assert _notes(memory) == {}
