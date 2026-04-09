"""Scribe — voice message transcription bot.

Send a voice message in any room where Scribe is present, and it replies
with the transcribed text. Uses whisper.cpp running natively on the host
for Metal GPU acceleration — a 5-minute voice memo transcribes in ~20
seconds, vs 10+ minutes CPU-only inside Docker.

The transcription API is OpenAI-compatible (/v1/audio/transcriptions),
so this bot works with any backend that serves that endpoint.
"""

import os
import tempfile
from pathlib import Path

import aiohttp
from loguru import logger
from nio import (
    AsyncClient,
    DownloadResponse,
    RoomMessageAudio,
)

from microbot import MicroBot

# Default — overridden by bots.json config
DEFAULT_WHISPER_URL = "http://host.docker.internal:42062/v1/audio/transcriptions"


async def _transcribe(whisper_url: str, audio_bytes: bytes, filename: str) -> str:
    """POST audio to whisper-server, return transcribed text."""
    suffix = Path(filename).suffix or ".ogg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field(
                "file",
                open(tmp_path, "rb"),
                filename=filename,
                content_type="application/octet-stream",
            )
            data.add_field("response_format", "json")

            async with session.post(
                whisper_url, data=data, timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()
                return result.get("text", "").strip()
    except Exception as e:
        logger.error("[scribe] Transcription failed: {}", e)
        return ""
    finally:
        Path(tmp_path).unlink(missing_ok=True)


class ScribeBot(MicroBot):
    name = "scribe-bot"

    def __init__(self, homeserver, user_id, password, session_dir, **config):
        super().__init__(homeserver, user_id, password, session_dir, **config)
        self.whisper_url = os.environ.get("WHISPER_URL", config.get("whisper_url", DEFAULT_WHISPER_URL))

    def register_callbacks(self, client: AsyncClient) -> None:
        self.add_event_callback(self._on_voice, RoomMessageAudio)

    async def _on_voice(self, room, event: RoomMessageAudio) -> None:
        if event.sender == self.user_id:
            return

        logger.info("[scribe] Voice from {} in {}", event.sender, room.room_id)
        await self._client.room_typing(room.room_id, typing_state=True, timeout=30000)

        resp = await self._client.download(event.url)
        if not isinstance(resp, DownloadResponse):
            await self._client.room_typing(room.room_id, typing_state=False)
            logger.error("[scribe] Download failed: {}", resp)
            return

        filename = event.body if event.body else "voice.ogg"
        text = await _transcribe(self.whisper_url, resp.body, filename)
        await self._client.room_typing(room.room_id, typing_state=False)

        if text:
            logger.info("[scribe] Transcribed: {}...", text[:80])
            await self._client.room_send(
                room_id=room.room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.text",
                    "body": f"**Transcription:**\n\n{text}",
                    "format": "org.matrix.custom.html",
                    "formatted_body": f"<strong>Transcription:</strong><br><br>{text}",
                    "m.relates_to": {
                        "m.in_reply_to": {"event_id": event.event_id},
                    },
                },
            )
        else:
            await self._client.room_send(
                room_id=room.room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.text",
                    "body": "Sorry, I couldn't transcribe that audio.",
                },
            )

