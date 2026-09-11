"""Voice messages reach handlers as text.

Speech and typing differ only in encoding, so the decode happens in the
transport and handlers see an ordinary text event: same sender, same
event id, same thread, having passed the same gates.

The whisper call is stubbed throughout. What is under test is the
framework wiring, not the speech model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nio.events.room_events import RoomMessage, RoomMessageAudio, RoomMessageText

_RUNNER = Path(__file__).resolve().parent.parent.parent / "stacklets" / "core" / "bot-runner"
sys.path.insert(0, str(_RUNNER))
# `lib/` hosts `stack.ai.client`, which the framework imports for the
# Transcriber type.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

import voice  # noqa: E402
from microbot import MicroBot  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────────

VOICE_SOURCE = {
    "type": "m.room.message",
    "event_id": "$voice1",
    "sender": "@homer:simpson",
    "origin_server_ts": 1700000000000,
    "content": {
        "msgtype": "m.audio",
        "body": "Voice message",
        "url": "mxc://simpson/abc123",
        "info": {"mimetype": "audio/ogg", "duration": 4200, "size": 9001},
    },
}


def _voice_event(**overrides) -> RoomMessageAudio:
    src = {**VOICE_SOURCE, **overrides}
    if "content" in overrides:
        src["content"] = {**VOICE_SOURCE["content"], **overrides["content"]}
    return RoomMessage.parse_event(src)


def _text_event(body: str = "typed words") -> RoomMessageText:
    return RoomMessage.parse_event({
        "type": "m.room.message",
        "event_id": "$typed1",
        "sender": "@marge:simpson",
        "origin_server_ts": 1700000000001,
        "content": {"msgtype": "m.text", "body": body},
    })


class _Room:
    room_id = "!kitchen:simpson"


class _FakeClient:
    def __init__(self):
        self.rooms = {"!kitchen:simpson": _Room()}
        self.typing: list[bool] = []

    async def room_typing(self, room_id, typing_state, timeout=None):
        self.typing.append(typing_state)


class _StubTranscriber:
    """Stand-in for `stack.ai.client.Transcriber` — the raw whisper half."""

    def __init__(self, result: str = "buy milk on the way home",
                 error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    async def transcribe(self, audio: bytes, *, filename: str = "voice.ogg",
                         model: str | None = None, cleanup_with=None) -> str:
        self.calls.append({"audio": audio, "filename": filename,
                           "cleanup_with": cleanup_with})
        if self.error is not None:
            raise self.error
        return self.result


class _StubLLM:
    """Stand-in for the polish pass: records what it was asked to fix."""

    def __init__(self, result: str = "Buy milk on the way home."):
        self.result = result
        self.seen: list[str] = []
        self.kwargs: list[dict] = []

    async def complete(self, role: str, prompt: str, **kw) -> str:
        self.seen.append(prompt)
        self.kwargs.append(kw)
        return self.result


class _Bot(MicroBot):
    name = "test-bot"

    def register_callbacks(self, client):
        pass


def _bot(tmp_path, *, transcriber=None, name="test-bot", cleanup=None) -> _Bot:
    bot = _Bot(homeserver="http://hs", user_id="@test-bot:simpson",
               password="x", session_dir=str(tmp_path))
    bot.name = name
    bot._client = _FakeClient()
    bot._transcriber = transcriber
    bot._transcript_cleanup = cleanup

    async def _download(mxc_url):
        return b"OggS-fake-audio"

    bot._download_media = _download
    return bot


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """The store is process-global and durable by design; give each test
    its own directory so they do not inherit each other's transcripts."""
    monkeypatch.setattr(
        voice, "TRANSCRIPTS", voice.TranscriptStore(tmp_path / "transcripts"),
    )


def _collect(bot, event_type=RoomMessageText) -> list:
    """Register a handler of `event_type` and return the list it fills."""
    seen: list = []

    async def handler(room, event):
        seen.append(event)

    bot.add_event_callback(handler, event_type)
    return seen


# ── The contract ─────────────────────────────────────────────────────────

class TestVoiceIsJustAMessage:
    """A handler that was written for typed text handles speech too,
    without knowing speech exists."""

    @pytest.mark.asyncio
    async def test_a_text_handler_receives_the_spoken_words(self, tmp_path):
        bot = _bot(tmp_path, transcriber=_StubTranscriber("buy milk"))
        seen = _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert len(seen) == 1
        assert isinstance(seen[0], RoomMessageText)
        assert seen[0].body == "buy milk"

    @pytest.mark.asyncio
    async def test_the_speaker_remains_the_sender(self, tmp_path):
        """Replies, reactions and thread ownership are keyed on the
        sender and event id. Re-attributing the words to the framework
        would detach all three from the visible message."""
        bot = _bot(tmp_path, transcriber=_StubTranscriber())
        seen = _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert seen[0].sender == "@homer:simpson"
        assert seen[0].event_id == "$voice1"
        assert seen[0].server_timestamp == 1700000000000

    @pytest.mark.asyncio
    async def test_a_memo_sent_into_a_thread_stays_in_it(self, tmp_path):
        bot = _bot(tmp_path, transcriber=_StubTranscriber())
        seen = _collect(bot)
        relates = {"rel_type": "m.thread", "event_id": "$root9",
                   "is_falling_back": True,
                   "m.in_reply_to": {"event_id": "$root9"}}

        await bot._dispatch(
            "!kitchen:simpson",
            _voice_event(content={"m.relates_to": relates}),
        )

        assert seen[0].source["content"]["m.relates_to"] == relates

    @pytest.mark.asyncio
    async def test_the_words_are_marked_as_transcribed(self, tmp_path):
        """Transcription can be wrong in ways typing cannot, so the
        decoded event records the audio it came from and the reply layer
        can quote the words back."""
        bot = _bot(tmp_path, transcriber=_StubTranscriber())
        seen = _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        content = seen[0].source["content"]
        assert voice.was_transcribed(content)
        block = content[voice.TRANSCRIPT_KEY]
        assert block["url"] == "mxc://simpson/abc123"
        assert block["source_msgtype"] == "m.audio"
        assert block["duration"] == 4200
        # The audio payload itself must not masquerade as a text body.
        assert "url" not in content
        assert content["msgtype"] == "m.text"

    @pytest.mark.asyncio
    async def test_audio_handlers_no_longer_see_it(self, tmp_path):
        """The decode replaces the event rather than duplicating it, so
        no handler is left holding raw audio to transcribe again."""
        bot = _bot(tmp_path, transcriber=_StubTranscriber())
        audio_seen = _collect(bot, RoomMessageAudio)
        text_seen = _collect(bot, RoomMessageText)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert audio_seen == []
        assert len(text_seen) == 1

    @pytest.mark.asyncio
    async def test_typed_messages_pass_through_untouched(self, tmp_path):
        bot = _bot(tmp_path, transcriber=_StubTranscriber())
        seen = _collect(bot)

        typed = _text_event("typed words")
        await bot._dispatch("!kitchen:simpson", typed)

        assert seen == [typed]
        assert not voice.was_transcribed(typed.source["content"])


class TestWhenTheWordsCannotBeRecovered:
    """Speech that cannot be decoded is dispatched to no handler and
    draws no reply. The recording stays visible in the room."""

    @pytest.mark.asyncio
    async def test_without_whisper_nothing_is_dispatched(self, tmp_path):
        bot = _bot(tmp_path, transcriber=None)
        seen = _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert seen == []

    @pytest.mark.asyncio
    async def test_a_transcription_failure_is_not_dispatched(self, tmp_path):
        from stack.ai.client import LLMError

        bot = _bot(tmp_path, transcriber=_StubTranscriber(
            error=LLMError("whisper unreachable")))
        seen = _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert seen == []

    @pytest.mark.asyncio
    async def test_silence_is_not_dispatched(self, tmp_path):
        bot = _bot(tmp_path, transcriber=_StubTranscriber("   "))
        seen = _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert seen == []

    @pytest.mark.asyncio
    async def test_a_failed_download_is_not_dispatched(self, tmp_path):
        bot = _bot(tmp_path, transcriber=_StubTranscriber())
        seen = _collect(bot)

        async def _no_bytes(mxc_url):
            return None

        bot._download_media = _no_bytes
        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert seen == []


class TestOneWhisperRunPerMessage:
    """Bots in a room drain the same timeline in one process, so without
    sharing a long recording would be sent to whisper once per bot."""

    @pytest.mark.asyncio
    async def test_two_bots_transcribe_the_same_memo_once(self, tmp_path):
        shared = _StubTranscriber("buy milk")
        archivist = _bot(tmp_path / "a", transcriber=shared, name="archivist-bot")
        stacker = _bot(tmp_path / "b", transcriber=shared, name="stacker-bot")
        heard_a, heard_b = _collect(archivist), _collect(stacker)

        event = _voice_event()
        await archivist._dispatch("!kitchen:simpson", event)
        await stacker._dispatch("!kitchen:simpson", event)

        assert len(shared.calls) == 1
        # Both still get the words: sharing the work, not the delivery.
        assert heard_a[0].body == "buy milk"
        assert heard_b[0].body == "buy milk"

    @pytest.mark.asyncio
    async def test_a_stored_transcript_is_not_produced_again(self, tmp_path):
        """The drain and a backfill are separate processes over one
        store, so a transcript already produced is reused."""
        first = _StubTranscriber("buy milk")
        bot = _bot(tmp_path, transcriber=first)
        _collect(bot)
        await bot._dispatch("!kitchen:simpson", _voice_event())
        assert len(first.calls) == 1

        # A fresh bot, as after a restart — same store on disk.
        second = _StubTranscriber("should never run")
        restarted = _bot(tmp_path / "restarted", transcriber=second)
        seen = _collect(restarted)
        await restarted._dispatch("!kitchen:simpson", _voice_event())

        assert second.calls == []
        assert seen[0].body == "buy milk"

    @pytest.mark.asyncio
    async def test_a_failure_is_not_remembered(self, tmp_path):
        """Outages are transient and the drain retries, so a failure is
        not retained."""
        from stack.ai.client import LLMError

        broken = _StubTranscriber(error=LLMError("whisper down"))
        bot = _bot(tmp_path, transcriber=broken)
        seen = _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())
        assert seen == []

        bot._transcriber = _StubTranscriber("buy milk")
        await bot._dispatch("!kitchen:simpson", _voice_event())
        assert [e.body for e in seen] == ["buy milk"]


class TestTranscriberWiring:
    """Built from the environment at construction, like every other
    framework capability; absent whisper is a normal state, not a crash."""

    def test_absent_whisper_leaves_the_bot_running(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WHISPER_URL", raising=False)
        bot = _Bot(homeserver="http://hs", user_id="@t:simpson",
                   password="x", session_dir=str(tmp_path))
        assert bot._transcriber is None

    def test_whisper_url_builds_a_transcriber(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHISPER_URL", "http://localhost:42062/v1")
        bot = _Bot(homeserver="http://hs", user_id="@t:simpson",
                   password="x", session_dir=str(tmp_path))
        assert bot._transcriber is not None


class TestTranscribedSource:
    """The pure rewrite, exercised directly: given a voice event's raw
    source dict and the words, produce the text event it becomes."""

    def test_keeps_identity_and_replaces_the_body(self):
        out = voice.transcribed_source(VOICE_SOURCE, "buy milk")
        assert out["event_id"] == VOICE_SOURCE["event_id"]
        assert out["sender"] == VOICE_SOURCE["sender"]
        assert out["content"]["body"] == "buy milk"
        assert out["content"]["msgtype"] == "m.text"

    def test_does_not_mutate_the_original(self):
        before = VOICE_SOURCE["content"]["msgtype"]
        voice.transcribed_source(VOICE_SOURCE, "buy milk")
        assert VOICE_SOURCE["content"]["msgtype"] == before
        assert "url" in VOICE_SOURCE["content"]


class TestIsVoice:
    def test_audio_with_a_payload(self):
        assert voice.is_voice(_voice_event())

    def test_plain_text(self):
        assert not voice.is_voice(_text_event())

    def test_audio_without_a_payload_is_not_decodable(self):
        assert not voice.is_voice(_voice_event(content={"url": ""}))


class TestThePolishPass:
    """whisper returns an unpunctuated run of words and the polish pass
    restores sentence boundaries. The prompt forbids changing any word,
    so the result stays verbatim."""

    @pytest.mark.asyncio
    async def test_handlers_receive_the_polished_text(self, tmp_path):
        bot = _bot(
            tmp_path,
            transcriber=_StubTranscriber("buy milk on the way home"),
            cleanup=_StubLLM("Buy milk on the way home."),
        )
        seen = _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert seen[0].body == "Buy milk on the way home."

    @pytest.mark.asyncio
    async def test_the_raw_transcript_is_kept_alongside_the_polished_one(
        self, tmp_path,
    ):
        """Re-polishing is cheap and re-transcribing is not, so the raw
        output is kept alongside the polished text."""
        bot = _bot(
            tmp_path,
            transcriber=_StubTranscriber("buy milk on the way home"),
            cleanup=_StubLLM("Buy milk on the way home."),
        )
        _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        record = voice.TRANSCRIPTS.read("$voice1")
        assert record["raw"] == "buy milk on the way home"
        assert record["text"] == "Buy milk on the way home."
        assert record["url"] == "mxc://simpson/abc123"

    @pytest.mark.asyncio
    async def test_without_an_llm_the_raw_transcript_still_lands(self, tmp_path):
        """Polish is optional; without an LLM the raw output is used."""
        bot = _bot(tmp_path, transcriber=_StubTranscriber("buy milk"),
                   cleanup=None)
        seen = _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert seen[0].body == "buy milk"
        assert voice.TRANSCRIPTS.read("$voice1")["raw"] == "buy milk"


class TestTranscriptStore:
    """Durable and shared, because a backfill runs in a separate process
    from the bots and covers a room's whole history."""

    def test_a_record_survives_a_new_store_over_the_same_directory(self, tmp_path):
        store = voice.TranscriptStore(tmp_path / "t")
        store.write("$evt1", {"raw": "hello", "text": "Hello."})

        reopened = voice.TranscriptStore(tmp_path / "t")
        assert reopened.read("$evt1")["text"] == "Hello."

    def test_an_unknown_event_reads_as_none(self, tmp_path):
        assert voice.TranscriptStore(tmp_path / "t").read("$nope") is None

    def test_event_ids_with_awkward_characters_round_trip(self, tmp_path):
        """Matrix event ids are base64 and carry `/` and `+`, which would
        otherwise land as directory separators."""
        store = voice.TranscriptStore(tmp_path / "t")
        awkward = "$a/b+c=d:simpson"
        store.write(awkward, {"text": "fine"})
        assert store.read(awkward)["text"] == "fine"

    def test_an_unwritable_store_does_not_break_the_message(self, tmp_path):
        """An unwritable store costs a later re-transcription, not the
        message itself."""
        blocked = tmp_path / "afile"
        blocked.write_text("not a directory")
        store = voice.TranscriptStore(blocked / "t")
        store.write("$evt1", {"text": "hi"})
        assert store.read("$evt1") is None


class TestTheWorkingIndicator:
    """Transcription runs before any handler could raise the typing
    indicator, so the framework raises it. It must also clear it on the
    paths where no handler runs, or it persists for the full timeout."""

    @pytest.mark.asyncio
    async def test_typing_stops_when_no_handler_wants_the_message(self, tmp_path):
        bot = _bot(tmp_path, transcriber=_StubTranscriber("buy milk"))
        # A bot that only handles audio now matches nothing: the message
        # reaches dispatch as text.
        _collect(bot, RoomMessageAudio)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert bot._client.typing == [True, False]

    @pytest.mark.asyncio
    async def test_typing_stops_when_the_words_cannot_be_recovered(self, tmp_path):
        from stack.ai.client import LLMError

        bot = _bot(tmp_path, transcriber=_StubTranscriber(
            error=LLMError("whisper down")))
        _collect(bot)

        await bot._dispatch("!kitchen:simpson", _voice_event())

        assert bot._client.typing == [True, False]

    @pytest.mark.asyncio
    async def test_a_typed_message_never_raises_it(self, tmp_path):
        """Only the decode raises it. The handler wrap still clears it on
        the way out, as it does for every message."""
        bot = _bot(tmp_path, transcriber=_StubTranscriber())
        _collect(bot)

        await bot._dispatch("!kitchen:simpson", _text_event())

        assert True not in bot._client.typing
