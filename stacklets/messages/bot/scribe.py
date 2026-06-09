"""Scribe — voice message transcription bot.

Send a voice message in any room where Scribe is present, and it replies
with the transcribed text. Uses whisper.cpp running natively on the host
for Metal GPU acceleration — a 5-minute voice memo transcribes in ~20
seconds, vs 10+ minutes CPU-only inside Docker.

The transcription API call is delegated to the shared `Transcriber`
capability on the AI client; the bot is just the Matrix-side wiring —
download audio, hand bytes to the Transcriber, post the result.
"""

from loguru import logger
from nio import (
    AsyncClient,
    RoomMessageAudio,
)

from microbot import MicroBot
from stack.ai.client import LLM, LLMError, LLMUnavailableError, Transcriber


class ScribeBot(MicroBot):
    name = "scribe-bot"

    def __init__(self, homeserver, user_id, password, session_dir, **config):
        super().__init__(homeserver, user_id, password, session_dir, **config)
        # The Transcriber owns the HTTP client; we don't have a clean
        # shutdown hook in the MicroBot base, so we accept that the
        # underlying client lives for the lifetime of the bot process.
        #
        # When WHISPER_URL isn't set (e.g. the AI stacklet hasn't been
        # installed yet) the constructor would otherwise crash here. We
        # degrade to a no-op bot instead: still in the room, still
        # joinable, but silent on voice messages until whisper is wired.
        try:
            self._transcriber = Transcriber.from_env(namespace=self.name)
        except LLMUnavailableError as e:
            logger.warning(
                "[scribe] transcription disabled: {} — "
                "voice messages will be ignored until WHISPER_URL is set",
                e,
            )
            self._transcriber = None

        # The LLM is optional: when present, it polishes raw whisper
        # output into a punctuated paragraph (the Transcriber owns the
        # prompt). When absent, scribe still posts the raw transcript --
        # less readable but never a blocker.
        try:
            self._llm = LLM.from_env(namespace=self.name)
        except LLMUnavailableError as e:
            logger.warning(
                "[scribe] transcript cleanup disabled: {} — "
                "voice messages will post the raw whisper output",
                e,
            )
            self._llm = None

    def register_callbacks(self, client: AsyncClient) -> None:
        self.add_event_callback(self._on_voice, RoomMessageAudio)

    async def _on_voice(self, room, event: RoomMessageAudio) -> None:
        if event.sender == self.user_id:
            return
        if self._transcriber is None:
            # The startup warning already told the admin why; don't
            # spam a per-message error reply or a typing indicator that
            # leads nowhere.
            return

        logger.info("[scribe] Voice from {} in {}", event.sender, room.room_id)
        await self._set_typing(room.room_id, on=True)

        audio = await self._download_media(event.url)
        if audio is None:
            await self._set_typing(room.room_id, on=False)
            logger.error("[scribe] Download failed for {}", event.url)
            return

        filename = event.body if event.body else "voice.ogg"
        text = ""
        try:
            text = await self._transcriber.transcribe(
                audio, filename=filename, cleanup_with=self._llm,
            )
        except LLMError as e:
            # The Transcriber maps every transport / API failure to an
            # LLMError; we log and fall through to the empty-text branch
            # so the family sees a friendly reply instead of silence.
            logger.error("[scribe] Transcription failed: {}", e)
        await self._set_typing(room.room_id, on=False)

        if text:
            logger.info("[scribe] Transcribed: {}...", text[:80])
            await self._send(
                room.room_id, f"**Transcription:**\n\n{text}",
                reply_to=event.event_id,
            )
        else:
            await self._send(
                room.room_id, "Sorry, I couldn't transcribe that audio.",
            )
