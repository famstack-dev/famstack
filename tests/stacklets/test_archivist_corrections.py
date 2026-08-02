"""Correcting a filing from chat: which messages mean "you got it wrong".

The archivist answers a filing in a thread hung off the uploaded
message. When a family member then writes in that thread ("this is
Marge's, not Homer's"), they are correcting the filing, and the bot
must re-run classification with their words as an authoritative hint.
Anything else free-typed in the documents room is a search.

Telling the two apart is a reading of the Matrix relation on the
incoming event, so these tests drive `_on_text` from the outside with
relation shapes taken from the **spec**, not from what our own sender
happens to produce:

  * a message typed inside a thread carries `rel_type: m.thread` with
    the thread root, and clients that support rich replies also add a
    *falling back* `m.in_reply_to` pointing at the newest event in the
    thread, flagged `is_falling_back: true` (Matrix v1.4, threads).
    That pointer is a rendering aid for thread-blind clients, not the
    event the human chose;
  * some clients send the thread relation without any `m.in_reply_to`;
  * a plain rich reply carries only `m.in_reply_to`.

A fixture built from our own `_send` output would only prove we agree
with ourselves. The bug these tests pin was exactly a disagreement
between our sender (thread-aware) and our reader (single reply hop).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from archivist import ArchivistBot  # noqa: E402


BOT_ID = "@archivist-bot:server"
HOMER = "@homer:server"
ROOM_ID = "!docs:server"
DOC_ID = 42


# ── Matrix wire shapes ───────────────────────────────────────────────────
#
# Written from the spec rather than from `MicroBot._send`, so a change in
# how we *send* can never quietly make these agree with how we *read*.


def _thread_relation(root: str, *, falls_back_to: str | None = None) -> dict:
    """`m.relates_to` for a message typed inside the thread at `root`.

    `falls_back_to` is the client's reply fallback: Element points it at
    the newest event in the thread so thread-blind clients still show
    the message in context, and marks it `is_falling_back`. Leaving it
    None is the equally valid shape other clients send.
    """
    relation = {"rel_type": "m.thread", "event_id": root}
    if falls_back_to:
        relation["is_falling_back"] = True
        relation["m.in_reply_to"] = {"event_id": falls_back_to}
    return {"m.relates_to": relation}


def _reply_relation(target: str) -> dict:
    """`m.relates_to` for a rich reply to `target`, no thread involved."""
    return {"m.relates_to": {"m.in_reply_to": {"event_id": target}}}


def _message(event_id, sender, body, *, content=None, envelope=None):
    """A minimal `m.room.message` as the bot receives it from nio."""
    payload = {"msgtype": "m.text", "body": body, **(content or {})}
    if envelope is not None:
        payload["dev.famstack.event"] = envelope
    return SimpleNamespace(
        event_id=event_id, sender=sender, body=body,
        server_timestamp=1_700_000_000_000,
        source={"content": payload},
    )


def _filed(paperless_id=DOC_ID, **data):
    return {
        "source": "docs", "type": "document.filed",
        "data": {"paperless_id": paperless_id, **data},
    }


def _reclassified(paperless_id=DOC_ID, **data):
    return {
        "source": "docs", "type": "document.reclassified",
        "data": {"paperless_id": paperless_id, **data},
    }


# ── Homeserver stand-in ──────────────────────────────────────────────────


class FakeMatrix:
    """The slice of `nio.AsyncClient` the correction path reads.

    Holds a room's events and, per thread root, the ids of the events
    hanging off it. `room_get_event_relations` is an async iterator that
    yields newest-first, which is what Synapse returns for the default
    backwards direction.
    """

    def __init__(self):
        self.events: dict[str, object] = {}
        self.threads: dict[str, list[str]] = {}
        self.relation_calls: list[str] = []

    def add(self, event, *, thread_root: str | None = None):
        self.events[event.event_id] = event
        if thread_root:
            self.threads.setdefault(thread_root, []).append(event.event_id)
        return event

    async def room_get_event(self, room_id, event_id):
        return SimpleNamespace(event=self.events.get(event_id))

    async def room_get_event_relations(
        self, room_id, event_id, rel_type=None, **kwargs,
    ):
        self.relation_calls.append(event_id)
        for child_id in reversed(self.threads.get(event_id, [])):
            yield self.events[child_id]


# ── Bot under test ───────────────────────────────────────────────────────


@pytest.fixture
def bot(tmp_path):
    """An archivist wired to a fake homeserver, recording where each
    message was routed. Handlers are replaced with recorders: the
    dispatch decision is what these tests pin, and the handlers have
    their own coverage."""
    bot = ArchivistBot(
        homeserver="http://homeserver", user_id=BOT_ID, password="x",
        session_dir=tmp_path,
    )
    bot._client = FakeMatrix()
    bot.routed: list[tuple] = []

    async def _search(room_id, query, reply_to=None, *, sender=None):
        bot.routed.append(("search", query))

    async def _reprocess(room_id, doc_id, user_hint, reply_to, *,
                         date_filed=None, initial_classification=None):
        bot.routed.append(("reprocess", doc_id, user_hint, initial_classification))

    async def _capture_reprocess(room_id, vault_path, user_hint, sender_mxid,
                                 reply_to, *, initial_classification=None):
        bot.routed.append(("capture_reprocess", vault_path, user_hint))

    async def _text_capture(room_id, text, sender, reply_to=None, *, capture_id=None):
        bot.routed.append(("capture_text", text))

    async def _send(room_id, text, *a, **kw):
        bot.routed.append(("send", text))

    async def _noop(*a, **kw):
        return None

    bot._handle_search = _search
    bot._handle_reply_reprocess = _reprocess
    bot._handle_reply_capture_reprocess = _capture_reprocess
    bot._handle_text_capture = _text_capture
    bot._send = _send
    # The per-room welcome and the room-mode read run ahead of routing
    # and are covered elsewhere; keep the recorded list to routing only.
    bot._send_room_welcome_if_needed = _noop
    bot._room_mode_allows_react = lambda _ctx: _true_coro()
    return bot


async def _true_coro():
    return True


def _docs_room():
    return SimpleNamespace(
        room_id=ROOM_ID,
        canonical_alias="#documents:server",
        name=None,
        users={uid: object() for uid in (BOT_ID, HOMER, "@marge:server")},
    )


@pytest.fixture
def filed_thread(bot):
    """The room after a filing: Homer's upload, the archivist's threaded
    confirmation carrying the `document.filed` envelope, and a later bot
    message in the same thread that carries no envelope.

    That trailing message is the reported trigger: it is what a client's
    reply fallback points at, so a reader that only follows one reply hop
    lands on a message with nothing to correct.
    """
    client = bot._client
    client.add(_message("$upload", HOMER, "invoice.pdf"))
    client.add(
        _message("$filed", BOT_ID, "Filed: Duff Insurance invoice (#42)",
                 content=_thread_relation("$upload"),
                 envelope=_filed(topics=["Insurance"], persons=["Homer"])),
        thread_root="$upload",
    )
    client.add(
        _message("$todo", BOT_ID, "Added 1 todo: pay by 2026-03-15",
                 content=_thread_relation("$upload", falls_back_to="$filed")),
        thread_root="$upload",
    )
    return client


# ── Corrections typed inside the filing thread ───────────────────────────


class TestCorrectionInsideAThread:
    """A message in the filing's thread is about that filing. The thread
    is the relation the user's client states deliberately; the reply
    fallback inside it is not."""

    @pytest.mark.asyncio
    async def test_reply_fallback_pointing_at_a_later_message_still_corrects(
        self, bot, filed_thread,
    ):
        """The reported bug. Homer types in the thread; Element attaches a
        falling-back `m.in_reply_to` to the newest event there, which is
        the bot's todo line, not the filing. Following that one hop finds
        no envelope. The thread does."""
        event = _message(
            "$correction", HOMER, "this is Marge's, not Homer's",
            content=_thread_relation("$upload", falls_back_to="$todo"),
        )
        await bot._on_text(_docs_room(), event)
        assert bot.routed == [
            ("reprocess", DOC_ID, "this is Marge's, not Homer's",
             {"paperless_id": DOC_ID, "topics": ["Insurance"], "persons": ["Homer"]}),
        ]

    @pytest.mark.asyncio
    async def test_thread_message_without_a_reply_relation_corrects(
        self, bot, filed_thread,
    ):
        """Clients may send the thread relation with no `m.in_reply_to` at
        all. There is no reply hop to follow, so a reader built on one
        cannot see this message; the thread relation is enough."""
        event = _message(
            "$correction", HOMER, "wrong year, it is 2025",
            content=_thread_relation("$upload"),
        )
        await bot._on_text(_docs_room(), event)
        assert [r[0] for r in bot.routed] == ["reprocess"]
        assert bot.routed[0][2] == "wrong year, it is 2025"

    @pytest.mark.asyncio
    async def test_reply_to_the_users_own_upload_corrects(self, bot, filed_thread):
        """Replying to the message that started it all is the obvious
        gesture, and the one the reporter used. The upload is Homer's own
        event and carries no envelope, so only the thread hanging off it
        identifies the document."""
        event = _message(
            "$correction", HOMER, "the correspondent is Globex",
            content=_thread_relation("$upload", falls_back_to="$upload"),
        )
        await bot._on_text(_docs_room(), event)
        assert [r[0] for r in bot.routed] == ["reprocess"]

    @pytest.mark.asyncio
    async def test_plain_reply_to_the_upload_corrects(self, bot, filed_thread):
        """Same gesture from the main timeline: a client that replies to
        the upload without joining the thread sends only `m.in_reply_to`.
        The replied-to event is the root of the filing's thread, so the
        filing is still reachable."""
        event = _message(
            "$correction", HOMER, "the correspondent is Globex",
            content=_reply_relation("$upload"),
        )
        await bot._on_text(_docs_room(), event)
        assert [r[0] for r in bot.routed] == ["reprocess"]

    @pytest.mark.asyncio
    async def test_latest_classification_in_the_thread_wins(self, bot, filed_thread):
        """A correction applies to the state the user is looking at. When
        the thread already holds a reclassification, that is the anchor
        handed to the pipeline, not the original filing."""
        filed_thread.add(
            _message("$reclass", BOT_ID, "Reclassified (#42)",
                     content=_thread_relation("$upload", falls_back_to="$todo"),
                     envelope=_reclassified(persons=["Marge"])),
            thread_root="$upload",
        )
        filed_thread.add(
            _message("$todo2", BOT_ID, "Todo updated",
                     content=_thread_relation("$upload", falls_back_to="$reclass")),
            thread_root="$upload",
        )
        event = _message(
            "$correction", HOMER, "and the type is Contract",
            content=_thread_relation("$upload", falls_back_to="$todo2"),
        )
        await bot._on_text(_docs_room(), event)
        assert bot.routed[0][0] == "reprocess"
        assert bot.routed[0][3] == {"paperless_id": DOC_ID, "persons": ["Marge"]}

    @pytest.mark.asyncio
    async def test_capture_thread_reaches_the_capture_pipeline(self, bot):
        """Captures thread the same way and correct the same way; the
        archivist reads the envelope kind, not the room."""
        client = bot._client
        client.add(_message("$note", HOMER, "a long pasted note"))
        client.add(
            _message("$capfiled", BOT_ID, "Saved as a note",
                     content=_thread_relation("$note"),
                     envelope={"source": "docs", "type": "capture.filed",
                               "data": {"vault_path": "homer/notes/x.md"}}),
            thread_root="$note",
        )
        event = _message(
            "$correction", HOMER, "file this under school, not work",
            content=_thread_relation("$note"),
        )
        await bot._on_text(_docs_room(), event)
        assert bot.routed == [
            ("capture_reprocess", "homer/notes/x.md",
             "file this under school, not work"),
        ]


# ── What must keep working ───────────────────────────────────────────────


class TestNotACorrection:
    """The other direction of the same decision. Reading the thread must
    not turn ordinary messages into corrections."""

    @pytest.mark.asyncio
    async def test_plain_message_in_the_documents_room_is_a_search(
        self, bot, filed_thread,
    ):
        """No relation at all: the documents room's default is recall."""
        event = _message("$q", HOMER, "Duff Insurance")
        await bot._on_text(_docs_room(), event)
        assert bot.routed == [("search", "Duff Insurance")]

    @pytest.mark.asyncio
    async def test_thread_without_a_filing_is_a_search(self, bot):
        """A thread hung off an ordinary conversation has nothing to
        correct, so its messages route normally."""
        client = bot._client
        client.add(_message("$chat", HOMER, "did we ever insure the car?"))
        client.add(
            _message("$answer", BOT_ID, "I found 3 documents",
                     content=_thread_relation("$chat")),
            thread_root="$chat",
        )
        event = _message("$q", HOMER, "what about the boat",
                         content=_thread_relation("$chat"))
        await bot._on_text(_docs_room(), event)
        assert bot.routed == [("search", "what about the boat")]

    @pytest.mark.asyncio
    async def test_mention_inside_a_filing_thread_is_a_search(
        self, bot, filed_thread,
    ):
        """An @-mention is the user addressing the bot on purpose, and it
        outranks the ambient thread: inside a filing thread, "@archivist
        what else is from Duff?" is a question, not a correction.

        This also keeps the existing guard honest. Element X attaches an
        `m.in_reply_to` to mentioned messages the user never aimed, so
        mention-means-conversation is what stops ordinary searches being
        swallowed by the reprocess path.
        """
        event = _message(
            "$q", HOMER, f"{BOT_ID} what else is from Duff Insurance",
            content={
                "m.mentions": {"user_ids": [BOT_ID]},
                **_thread_relation("$upload", falls_back_to="$todo"),
            },
        )
        await bot._on_text(_docs_room(), event)
        assert bot.routed == [("search", "what else is from Duff Insurance")]

    @pytest.mark.asyncio
    async def test_reply_to_another_users_message_is_not_a_correction(self, bot):
        """Only our own filing messages are correction targets. A reply to
        a family member's message must not reach the pipeline even when
        that message carries a look-alike envelope."""
        client = bot._client
        client.add(
            _message("$spoof", "@bart:server", "Filed: homework (#99)",
                     envelope=_filed(99)),
        )
        event = _message("$q", HOMER, "nice try",
                         content=_reply_relation("$spoof"))
        await bot._on_text(_docs_room(), event)
        assert bot.routed == [("search", "nice try")]


class TestChainedCorrections:
    """Correcting a correction. Each round adds a user turn and a bot
    confirmation; the pipeline gets every human turn back to the original
    filing, plus the classification the user was looking at."""

    @pytest.mark.asyncio
    async def test_direct_reply_to_the_filing_still_corrects(self, bot, filed_thread):
        """The single reply hop is still the path for a client that quotes
        the filing message itself."""
        event = _message("$c1", HOMER, "this is Marge's",
                         content=_reply_relation("$filed"))
        await bot._on_text(_docs_room(), event)
        assert bot.routed == [
            ("reprocess", DOC_ID, "this is Marge's",
             {"paperless_id": DOC_ID, "topics": ["Insurance"], "persons": ["Homer"]}),
        ]

    @pytest.mark.asyncio
    async def test_reply_to_the_latest_reclassification_folds_earlier_turns(
        self, bot, filed_thread,
    ):
        """Round two. Homer replies to the confirmation of round one; the
        hint the pipeline sees carries both of his turns, most recent
        first, and the anchor is round one's classification."""
        client = bot._client
        client.add(_message("$c1", HOMER, "this is Marge's",
                            content=_reply_relation("$filed")))
        client.add(_message("$reclass", BOT_ID, "Reclassified (#42)",
                            content=_reply_relation("$c1"),
                            envelope=_reclassified(persons=["Marge"])))

        event = _message("$c2", HOMER, "and it is a contract",
                         content=_reply_relation("$reclass"))
        await bot._on_text(_docs_room(), event)

        kind, doc_id, hint, initial = bot.routed[0]
        assert (kind, doc_id) == ("reprocess", DOC_ID)
        assert "and it is a contract" in hint
        assert "this is Marge's" in hint
        assert hint.index("and it is a contract") < hint.index("this is Marge's")
        assert initial == {"paperless_id": DOC_ID, "persons": ["Marge"]}


# ── The other half of the relation: where the answer lands ───────────────


class TestTheAnswerStaysInTheThread:
    """A correction is answered inside the thread it came from.

    The reader tests above are handed a thread that already contains a
    `document.reclassified` message. This class checks the bot actually
    produces one there. Without it those tests would agree with a bot
    that answers outside the thread: the fixture would supply the
    placement the implementation never creates, and chained corrections
    would silently anchor to the original filing forever.
    """

    @pytest.fixture
    def bot(self, tmp_path):
        """An archivist with the REAL reprocess handler and a stub
        pipeline, recording every send with its relation."""
        bot = ArchivistBot(
            homeserver="http://homeserver", user_id=BOT_ID, password="x",
            session_dir=tmp_path,
        )
        bot._client = FakeMatrix()
        bot.sent: list[dict] = []

        async def _room_send(room_id, message_type, content, **kw):
            bot.sent.append(content)
            return SimpleNamespace(event_id="$answer")

        bot._client.room_send = _room_send
        bot._pipeline = SimpleNamespace()
        return bot

    def _outcome(self, status="reclassified"):
        return SimpleNamespace(
            status=status, doc_id=DOC_ID, llm_error=("timeout", "took too long"),
            title="Auto Insurance Policy 2026", resolved_topics=["insurance"],
            resolved_persons=["Marge"], resolved_type="contract",
            resolved_correspondent="Duff Insurance",
            envelope=_reclassified(persons=["Marge"]),
        )

    @pytest.mark.asyncio
    async def test_reclassified_confirmation_joins_the_correction_thread(self, bot):
        """The confirmation carries an `m.thread` relation rooted where
        the correction was, so the next correction can find it."""
        bot._client.add(_message("$upload", HOMER, "policy.pdf"))
        bot._client.add(
            _message("$correction", HOMER, "this is Marge's",
                     content=_thread_relation("$upload", falls_back_to="$filed")),
            thread_root="$upload",
        )

        async def _reprocess(**kw):
            return self._outcome()

        bot._pipeline.reprocess = _reprocess
        await bot._handle_reply_reprocess(
            "!docs:server", DOC_ID, "this is Marge's", "$correction",
        )

        assert bot.sent, "the reprocess produced no message at all"
        relation = bot.sent[-1].get("m.relates_to", {})
        assert relation.get("rel_type") == "m.thread", (
            "the confirmation was posted outside the thread, so the next "
            "correction would anchor to the original filing"
        )
        assert relation.get("event_id") == "$upload"

    @pytest.mark.asyncio
    async def test_a_failed_reprocess_answers_in_the_thread_too(self, bot):
        """An error is a reply to what the user just typed, so it belongs
        where they typed it. Otherwise a correction that failed looks
        like nothing happened."""
        bot._client.add(_message("$upload", HOMER, "policy.pdf"))
        bot._client.add(
            _message("$correction", HOMER, "this is Marge's",
                     content=_thread_relation("$upload", falls_back_to="$filed")),
            thread_root="$upload",
        )

        async def _reprocess(**kw):
            return self._outcome(status="llm_error")

        bot._pipeline.reprocess = _reprocess
        await bot._handle_reply_reprocess(
            "!docs:server", DOC_ID, "this is Marge's", "$correction",
        )

        relation = bot.sent[-1].get("m.relates_to", {})
        assert relation.get("rel_type") == "m.thread"
        assert relation.get("event_id") == "$upload"
