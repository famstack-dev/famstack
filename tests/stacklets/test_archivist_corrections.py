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

Reading the thread answers *which* filing a message corrects. It does
not answer whether the message was for us at all -- other bots thread in
the same rooms -- so `TestOnlyOurOwnThreads` states the gate in front of
all of this: inside a thread, the archivist acts only in its own.
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


def _filed_bare(paperless_id=DOC_ID):
    """The envelope a filing with nothing to say still carries.

    A scan with no text layer reaches Paperless and the classifier
    produces nothing, so every field is empty except the one that
    matters: which document this message is about.
    """
    return {
        "source": "docs", "type": "document.filed",
        "summary": f"Document #{paperless_id} filed",
        "data": {
            "paperless_id": paperless_id, "title": "", "date": None,
            "topics": [], "persons": [], "correspondent": None,
            "document_type": None, "summary": "", "facts": [],
            "action_items": [],
        },
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
    hanging off it in timeline order. `room_get_event_relations` is an
    async iterator honouring `direction`: newest-first for the default
    backwards direction, which is what Synapse returns, and oldest-first
    for `front` — the order that says which reply came first.
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
        self, room_id, event_id, rel_type=None, direction=None, **kwargs,
    ):
        from nio.api import MessageDirection
        self.relation_calls.append(event_id)
        children = self.threads.get(event_id, [])
        if direction is not MessageDirection.front:
            children = reversed(children)
        for child_id in children:
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
    async def test_a_filing_with_nothing_to_say_is_still_correctable(self, bot):
        """The case that matters most, and the one that was broken.

        A scan with no text layer files fine and the archivist has
        nothing to add: "no text recognised, please tag it manually in
        Paperless". That is exactly the moment a person types the
        classification in themselves. Their words are the only
        description the document will ever have, so the reply has to
        reach reprocess and carry them as the hint.

        It used to run as a search, which answered a question nobody
        asked and quietly lost the correction.
        """
        client = bot._client
        client.add(_message("$scan", HOMER, "Gescannt_20260806-1532.pdf"))
        client.add(
            _message("$filed", BOT_ID,
                     "Filed: Gescannt_20260806-1532.pdf — no text recognised",
                     content=_thread_relation("$scan"),
                     envelope=_filed_bare()),
            thread_root="$scan",
        )
        hint = ('Classify as "Grundriss". It is a house plan for the '
                'Mühlenstr. Tag it "haus" and "mühlenstr".')
        event = _message(
            "$correction", HOMER, hint,
            content=_thread_relation("$scan", falls_back_to="$filed"),
        )
        await bot._on_text(_docs_room(), event)
        assert [r[0] for r in bot.routed] == ["reprocess"], \
            f"expected a reclassification, got {bot.routed}"
        assert bot.routed[0][1] == DOC_ID
        assert bot.routed[0][2] == hint, \
            "the user's own words are the prompt input for the reclassify"

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


class TestOnlyOurOwnThreads:
    """Reading the thread is what makes a correction findable, and it is
    also what let the archivist walk into conversations it has no part in.

    The family agent lives in the same rooms and answers in threads, and
    nobody repeats its name on every line of one. Those lines reached
    `_on_text` looking like free-typed material: a long one got filed as
    a note, and from then on the thread held one of our filing cards, so
    every reply after it read as a correction to that note. One paste
    became an unstoppable reclassification loop.

    So the thread gate is ownership, not content: inside a thread the
    archivist acts only where it answered first. `thread_owner` has the
    framework-level tests; these state what the family sees.
    """

    AGENT = "@merlin-bot:server"

    @staticmethod
    def _family_room():
        """A topic room -- not the documents room, so free text is
        capture-or-nothing rather than search."""
        return SimpleNamespace(
            room_id="!camping:server", canonical_alias="#camping:server",
            name="Camping",
            users={uid: object() for uid in (BOT_ID, HOMER, "@merlin-bot:server")},
        )

    @pytest.fixture
    def agent_thread(self, bot):
        """Homer asking the agent something, and the agent answering in a
        thread on his question -- the ordinary shape of talking to it."""
        client = bot._client
        client.add(_message("$ask", HOMER, "Merlin, what is still missing for camping?"))
        client.add(
            _message("$agent", self.AGENT, "The gas cartridge and the sleeping mats.",
                     content=_thread_relation("$ask")),
            thread_root="$ask",
        )
        return client

    @pytest.mark.asyncio
    async def test_a_paste_in_the_agents_thread_is_not_filed(self, bot, agent_thread):
        """The first wrong turn. Homer pastes the list he is working on
        into the conversation; it is for the agent to act on, not for us
        to file as a note."""
        event = _message(
            "$paste", HOMER,
            "Error saving the packing list, please clean it up and save it "
            "again because the shoe rack entry is still duplicated in there.",
            content=_thread_relation("$ask", falls_back_to="$agent"),
        )
        await bot._on_text(self._family_room(), event)
        assert bot.routed == []

    @pytest.mark.asyncio
    async def test_a_reply_in_the_agents_thread_is_not_a_correction(
        self, bot, agent_thread,
    ):
        """The cascade. Even once one of our cards sits in the thread --
        which is exactly how the loop sustained itself -- Homer's next
        words are still aimed at the agent."""
        agent_thread.add(
            _message("$filed", BOT_ID, "Saved: error saving the packing list",
                     content=_thread_relation("$ask"),
                     envelope=_filed(topics=["Camping"])),
            thread_root="$ask",
        )
        event = _message(
            "$reply", HOMER, "no it is not, write the file!",
            content=_thread_relation("$ask", falls_back_to="$filed"),
        )
        await bot._on_text(self._family_room(), event)
        assert bot.routed == []

    @pytest.mark.asyncio
    async def test_a_mention_in_the_agents_thread_still_reaches_us(
        self, bot, agent_thread,
    ):
        """Ownership is ambient; an @-mention is deliberate address, and
        that beats it -- the same rule corrections already follow."""
        event = _message(
            "$q", HOMER, f"{BOT_ID} what did we pack last summer",
            content={
                "m.mentions": {"user_ids": [BOT_ID]},
                **_thread_relation("$ask", falls_back_to="$agent"),
            },
        )
        await bot._on_text(self._family_room(), event)
        assert bot.routed == [("search", "what did we pack last summer")]

    @pytest.mark.asyncio
    async def test_two_people_talking_in_a_thread_are_left_alone(self, bot):
        """A thread no bot answered in belongs to nobody. Two family
        members working something out is a conversation, not material
        dropped for filing."""
        client = bot._client
        client.add(_message("$plan", HOMER, "when are we leaving on Friday?"))
        client.add(
            _message("$marge", "@marge:server", "after Lisa's rehearsal",
                     content=_thread_relation("$plan")),
            thread_root="$plan",
        )
        event = _message(
            "$paste", HOMER,
            "Right, so the plan is to load the car at four, leave by five, and "
            "stop at the halfway services for dinner around seven in the evening.",
            content=_thread_relation("$plan"),
        )
        await bot._on_text(self._family_room(), event)
        assert bot.routed == []

    @pytest.mark.asyncio
    async def test_the_main_timeline_is_untouched(self, bot, agent_thread):
        """The gate is about threads only. Dropping something into the
        room itself is still how you hand the archivist material, even
        while a conversation with the agent is open alongside it."""
        event = _message(
            "$paste", HOMER,
            "Campsite booking reference DUFF-4417, arrival Friday after six, "
            "pitch 12 by the water, cancellation free up to two days before.",
        )
        await bot._on_text(self._family_room(), event)
        assert [r[0] for r in bot.routed] == ["capture_text"]


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
