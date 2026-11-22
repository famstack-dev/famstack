"""The archivist in a chat room, for tests that drive it from a Matrix event.

    chat = ChatRoom(tmp_path, memory, httpserver, ModelAnswers(FILED))
    await chat.says("@marge:simpson", "Our grill needs new charcoal ...")
    [notice] = chat.notices()           # what the room read, with its envelope
    card = memory.text(path_of(notice))  # what the family memory holds

It builds the archivist as the bot runner loads it (`ArchivistBot`, its
collaborators, its capture pipeline and mirror) and hands it Matrix events
through its own handlers (`_on_text`, `_on_file`, `_on_reaction`). Filing
goes through the real pipeline into the real curator of memory's test
instance (`running_memory`), so a test asserts the card on file and the
notice in the room, which are what a person sees.

What stands in is outside the archivist:

- the homeserver: `Homeserver`, the slice of `nio.AsyncClient` the bot calls.
  It keeps every event the bot sends (answers, reactions) and returns them
  as a client reads them, so a reply in the thread of a notice finds the
  notice's envelope, as it does on Synapse;
- the media repository: the test's `httpserver` serves each upload's bytes
  at the authenticated download address;
- the model: `ModelAnswers`, which answers the classifier's calls in turn;
- the page behind a link: `Pages`, which counts how often it was read.

Why it is a module of its own: the capture path was tested from the
pipeline down, and no test started at the message. Each test of that path
needs the same room, bot and homeserver, and two setups of them would
drift apart (docs/agent/dev.md, the testing skill § 3).
"""

from __future__ import annotations

import base64
import itertools
from types import SimpleNamespace

import aiohttp

from stack.memory import MemoryAPI
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.capture_pipeline import CapturePipeline
from stacklets.docs.bot.extractors import TextExtractor
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.pipeline import LLMTimeoutError
from stacklets.docs.bot.vault_context import VaultContext

BOT_ID = "@archivist-bot:simpson"
MARGE = "@marge:simpson"
ROOM_ID = "!marge-notes:simpson"
WELCOME = "$welcome"
# The marks the bot puts on a message: the outcome signal of a filing.
# Tests name them here so that none of them pins the symbol.
FILED_MARK = "\u2705"
FAILED_MARK = "\u274c"
THE_DAY = 1_763_800_000_000  # 2025-11-22 in ms, the time every message of a test is sent at


# A 1x1 PNG, the photo Marge sends.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def a_pdf(words: str) -> bytes:
    """A one-page PDF whose text layer is `words`, as a program that prints one makes it."""
    stream = f"BT /F1 12 Tf 72 720 Td ({words}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    table = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, table)


# ── The homeserver ───────────────────────────────────────────────────────

class Homeserver:
    """The slice of `nio.AsyncClient` the archivist calls, over the events of one room.

    `room_send` keeps what the bot sends and answers with the new event's id.
    `room_get_event`, `room_get_event_relations` and `room_messages` read
    them back, as Synapse answers them. `redacted` lists the events the bot
    took back (its 👀 once the result is on a message).
    """

    access_token = "token"

    def __init__(self, room):
        self.rooms = {room.room_id: room}
        self.events: dict[str, SimpleNamespace] = {}
        self.threads: dict[str, list[str]] = {}
        self.redacted: list[str] = []
        self._ids = itertools.count(1)

    def add(self, event_id: str, sender: str, content: dict, *, type: str = "m.room.message",
            reacts_to: str | None = None) -> SimpleNamespace:
        """An event of the room, as nio shows it to a callback."""
        source = {"type": type, "event_id": event_id, "sender": sender, "content": content}
        event = SimpleNamespace(event_id=event_id, sender=sender, source=source,
                                body=content.get("body", ""), server_timestamp=THE_DAY,
                                type=type, reacts_to=reacts_to, key="")
        self.events[event_id] = event
        relation = content.get("m.relates_to") or {}
        if type == "m.reaction":
            event.key, event.reacts_to = relation.get("key", ""), relation.get("event_id")
        if relation.get("rel_type") == "m.thread":
            self.threads.setdefault(relation["event_id"], []).append(event_id)
        return event

    async def room_send(self, room_id, message_type, content, **_):
        event_id = f"$bot-{next(self._ids)}"
        self.add(event_id, BOT_ID, content, type=message_type)
        return SimpleNamespace(event_id=event_id)

    async def room_redact(self, room_id, event_id, **_):
        self.redacted.append(event_id)
        return SimpleNamespace(event_id=f"$redaction-{event_id}")

    async def room_typing(self, *_, **__):
        return SimpleNamespace()

    async def room_get_event(self, room_id, event_id):
        return SimpleNamespace(event=self.events.get(event_id))

    async def room_get_event_relations(self, room_id, event_id, rel_type=None, direction=None, **_):
        from nio.api import MessageDirection
        children = self.threads.get(event_id, [])
        if direction is not MessageDirection.front:
            children = reversed(children)
        for child_id in children:
            yield self.events[child_id]

    async def room_messages(self, room_id, limit=10, message_filter=None, **_):
        """The newest events matching the filter's senders and types."""
        wanted = message_filter or {}
        matching = [e for e in reversed(list(self.events.values()))
                    if e.sender in wanted.get("senders", [e.sender])
                    and e.type in wanted.get("types", [e.type])]
        return SimpleNamespace(chunk=matching[:limit])


# ── The model and the page behind a link ─────────────────────────────────

class ModelAnswers:
    """The classifier's model: each call is answered in turn, the last answer again after that.

    `calls` keeps what each call was asked (`text`, `images`, `user_hint`,
    `kind`), so a test can say what the model was shown.
    """

    def __init__(self, *answers: dict):
        self.answers, self.calls = list(answers), []

    async def classify_capture(self, **asked):
        self.calls.append(asked)
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        words = asked.get("text") or ""
        # A note in its writer's own words is answered with a clean version of them.
        return {"clean": words, **answer} if asked.get("clean_words") else dict(answer)


    async def reformat(self, text, **_):
        """A PDF's text read back as the model would clean it: unchanged."""
        return text


    async def correct_note(self, *, words, corrections):
        """A note's words with the corrections worked in: the model writes both down."""
        return {"clean": f"{words}\n\n{corrections}", "prompt_template": "correction"}

    async def rebuild_note(self, *, writer, words, prior, events):
        """A hand-edited note's clean version with the events worked in."""
        return {"clean": "\n\n".join([prior, *events]), "prompt_template": "conversation"}


class ModelDown:
    """A model that times out on every call."""

    def __getattr__(self, name):
        async def times_out(*_args, **_kwargs):
            raise LLMTimeoutError("the model took too long")
        return times_out


class Pages:
    """The web: one page per address, and how often each was read."""

    def __init__(self, **pages: str):
        self.pages, self.reads = pages, []

    async def fetch(self, url):
        self.reads.append(url)
        if url not in self.pages:
            return SimpleNamespace(url=url, verdict=SimpleNamespace(name="unreachable"), content=None)
        content = SimpleNamespace(text=self.pages[url], source_uri=url, title_hint=url.rsplit("/", 1)[-1],
                                  mime="text/html")
        return SimpleNamespace(url=url, verdict=None, content=content)


class NoTags:
    """The capture tag vocabulary: empty, and it keeps nothing."""

    def top(self, n):
        return []

    def record(self, tags, when):
        pass

    def save(self):
        pass


# ── The room ─────────────────────────────────────────────────────────────

class ChatRoom:
    """Marge's own capture room (she and the archivist), with the archivist in it.

    `memory` is memory's test instance, `httpserver` serves the uploads,
    `model` answers the classifier. `bot`, `homeserver`, `pages` and
    `model` are open for a test that needs them.
    """

    def __init__(self, tmp_path, memory, httpserver, model: ModelAnswers, *, pages: Pages | None = None,
                 memory_api: MemoryAPI | None = None):
        self.room = SimpleNamespace(
            room_id=ROOM_ID, canonical_alias=None, name=None,
            users={BOT_ID: object(), MARGE: object()})
        self.homeserver = Homeserver(self.room)
        self.httpserver, self.model, self.pages = httpserver, model, pages or Pages()
        vault = VaultContext(language="en", shared_bucket="family")
        self.mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                                memory=memory_api or MemoryAPI.from_container(memory.environ))
        self.pipeline = CapturePipeline(
            url_extractor=self.pages, text_extractor=TextExtractor(), classifier=model,
            mirror=self.mirror, capture_tags=NoTags(), paperless=None, bot_name="archivist-bot",
            classify_max_chars=10_000, capture_keep_body=False, capture_tag_prompt_size=50, vault=vault)
        self.bot = ArchivistBot(
            homeserver=httpserver.url_for(""), user_id=BOT_ID, password="x",
            session_dir=tmp_path / "session",
            services=SimpleNamespace(capture=self.pipeline, mirror=self.mirror, vault=vault,
                                     classifier=model, paperless=None, search=None))
        self.bot._client = self.homeserver
        self._events = itertools.count(1)
        self._uploads: dict[str, bytes] = {}
        # The room is not new: the archivist greeted it earlier, so its first filing is not preceded by a welcome.
        self.homeserver.add(WELCOME, BOT_ID, {"msgtype": "m.text", "body": "Hi! I'm Archivist."})

    async def close(self) -> None:
        """Close the bot's HTTP session, which the first download opened."""
        http = getattr(self.bot, "_http", None)
        if isinstance(http, aiohttp.ClientSession):
            await http.close()

    # ── What a person does ──

    def _message(self, sender: str, content: dict, event_id: str | None = None) -> SimpleNamespace:
        return self.homeserver.add(event_id or f"$msg-{next(self._events)}", sender, content)

    async def says(self, sender: str, words: str, *, event_id: str | None = None,
                   thread: str | None = None, extra: dict | None = None) -> SimpleNamespace:
        """`sender` writes `words` in the room (in the thread at `thread`, when given)."""
        content = {"msgtype": "m.text", "body": words, **(extra or {})}
        if thread:
            content["m.relates_to"] = {"rel_type": "m.thread", "event_id": thread,
                                       "is_falling_back": True, "m.in_reply_to": {"event_id": thread}}
        event = self._message(sender, content, event_id)
        await self.bot._on_text(self.room, event)
        return event

    async def speaks(self, sender: str, transcript: str, *, event_id: str | None = None) -> SimpleNamespace:
        """`sender` records a voice message that the transport transcribed as `transcript`.

        The event is what `stack.bot.voice` makes of the audio: the same id,
        sender and time, the words as its body, and the transcript's provenance.
        """
        from stack.bot import voice
        audio = {"event_id": event_id or f"$msg-{next(self._events)}", "sender": sender,
                 "content": {"msgtype": "m.audio", "body": "Voice message.ogg",
                             "url": "mxc://simpson/voice", "info": {"mimetype": "audio/ogg"}}}
        source = voice.transcribed_source(audio, transcript)
        event = self.homeserver.add(audio["event_id"], sender, source["content"])
        await self.bot._on_text(self.room, event)
        return event

    async def sends_file(self, sender: str, data: bytes, filename: str, *, msgtype: str = "m.file",
                         mimetype: str = "application/octet-stream", caption: str = "",
                         event_id: str | None = None, available: bool = True) -> SimpleNamespace:
        """`sender` uploads `data`; the media repository serves it under its `mxc://` address.

        With `available=False` the repository does not have the file yet
        (`serves(event)` gives it later), as when the upload had not finished.
        """
        media_id = f"media{next(self._events)}"
        self._uploads[media_id] = data
        if available:
            self._serve(media_id)
        content = {"msgtype": msgtype, "body": caption or filename, "filename": filename,
                   "url": f"mxc://simpson/{media_id}", "info": {"mimetype": mimetype, "size": len(data)}}
        if caption:
            content["filename"] = filename
        event = self._message(sender, content, event_id)
        await self.bot._on_file(self.room, event)
        return event

    def _serve(self, media_id: str) -> None:
        self.httpserver.expect_request(
            f"/_matrix/client/v1/media/download/simpson/{media_id}").respond_with_data(
            self._uploads[media_id])

    def serves(self, upload) -> None:
        """The media repository now has the file of `upload`."""
        self._serve(upload.source["content"]["url"].rsplit("/", 1)[-1])

    async def reacts(self, sender: str, key: str, to: str) -> SimpleNamespace:
        """`sender` reacts to the event `to` with `key`."""
        content = {"m.relates_to": {"rel_type": "m.annotation", "event_id": to, "key": key}}
        event = self.homeserver.add(f"$reaction-{next(self._events)}", sender, content,
                                    type="m.reaction", reacts_to=to)
        await self.bot._on_reaction(self.room, event)
        return event

    async def has_a_note_of(self, *messages: str) -> SimpleNamespace:
        """A note made of several messages, filed and answered in the room.

        The chat cannot make one yet: each message files a note of its own.
        The note is made as the pipeline makes one from its sources, each
        message an original, then answered as any filing is, in a thread
        under the first message. Returns that answer.
        """
        from stack.cards.sources import ORIGINAL, Source
        from stacklets.docs.bot.extractors import SourceContent
        first = self._message(MARGE, {"msgtype": "m.text", "body": messages[0]}, "$first")
        sources = [Source(ORIGINAL, "marge", f"2025-11-22T08:2{n}:00+00:00", "chat",
                          "text", "$first" if n == 0 else f"$more-{n}", quote=words)
                   for n, words in enumerate(messages)]
        outcome = await self.pipeline._publish(
            source=SourceContent(text="\n\n".join(messages), mime="text/plain", title_hint=None, source_uri=None),
            kind="note", sender_mxid=MARGE, display_link="(pasted text)", actor=MARGE,
            capture_id="$first", sources=sources, clean_words=True)
        await self.bot._captures.reply(ROOM_ID, outcome, first.event_id)
        return self.notices()[-1]

    # ── What the room shows ──

    def notices(self) -> list[SimpleNamespace]:
        """The archivist's answers in the room, oldest first (its reactions are not among them)."""
        return [e for e in self.homeserver.events.values()
                if e.sender == BOT_ID and e.type == "m.room.message" and e.event_id != WELCOME]

    def reactions_on(self, event_id: str) -> list[str]:
        """The archivist's reactions on a message, as the room shows them now (a 👀 it took back is gone)."""
        return [e.key for e in self.homeserver.events.values()
                if e.sender == BOT_ID and e.type == "m.reaction" and e.reacts_to == event_id
                and e.event_id not in self.homeserver.redacted]

    @staticmethod
    def envelope(notice) -> dict:
        """The filing envelope a notice carries (`dev.famstack.event`), {} when it has none."""
        return notice.source["content"].get("dev.famstack.event") or {}

    def thread_of(self, event) -> str | None:
        """The thread root a notice sits in."""
        return self.bot.get_thread_root(event)
