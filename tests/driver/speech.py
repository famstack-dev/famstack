"""Text turned into a voice message, the way a phone records one.

Test material, not product: an OpenAI-compatible TTS server (DRIVER_TTS_URL)
speaks the text, and ffmpeg, when present, turns it into ogg/opus like a
phone's recording. Without ffmpeg the mp3 is sent as it is.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# The TTS server this household uses answers a few requests with 502 before
# serving them; three tries cover that without hiding a server that is down.
ATTEMPTS = 3


def voice_message(text: str) -> Path:
    mp3 = Path(tempfile.mkdtemp(prefix="driver-voice-")) / "voice.mp3"
    mp3.write_bytes(_synthesize(text))
    return _as_phone_recording(mp3)


def _synthesize(text: str) -> bytes:
    url = os.environ.get("DRIVER_TTS_URL", "").rstrip("/")
    if not url:
        sys.exit("driver: set DRIVER_TTS_URL to an OpenAI-compatible TTS server, or pass --file")
    body = json.dumps({"model": os.environ.get("DRIVER_TTS_MODEL", "tts-1"),
                       "voice": os.environ.get("DRIVER_TTS_VOICE", "nova"),
                       "input": text, "response_format": "mp3"}).encode()
    request = urllib.request.Request(f"{url}/v1/audio/speech", data=body,
                                     headers={"Content-Type": "application/json"})
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.URLError:
            if attempt == ATTEMPTS:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def _as_phone_recording(mp3: Path) -> Path:
    if not shutil.which("ffmpeg"):
        return mp3
    ogg = mp3.with_suffix(".ogg")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp3),
                    "-c:a", "libopus", "-b:a", "32k", str(ogg)], check=True)
    return ogg
