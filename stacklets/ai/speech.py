"""Shared TTS and LLM utilities for the AI stacklet."""

import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

_SSL = __import__("ssl").create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = __import__("ssl").CERT_NONE

# The speech container's published port. `STACK_SPEECH_URL` points the
# helpers at another server, which is how the tests reach a stand-in.
_SPEECH_URL = "http://localhost:42063"

# The welcome message's pace, chosen by ear: 1.0 sounds rushed. Every
# command that speaks uses it, so they all sound like `stack ai hello`.
SPEED = 0.8

# The map the speech container mounts: the voice names it accepts.
VOICE_MAP = Path(__file__).parent / "config" / "voice_to_speaker.yaml"


def default_voice(language: str) -> str:
    """The voice for `[ai] language`: German has its own, the rest English."""
    return "onyx" if language.startswith("de") else "alloy"


def known_voices() -> list[str]:
    """The voices the speech server accepts for `tts-1`, in map order.

    Read as text because the host CLI is stdlib-only: each voice is a key
    indented one level under the `tts-1:` line.
    """
    voices, in_tts1 = [], False
    for line in VOICE_MAP.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            in_tts1 = line.rstrip() == "tts-1:"
        elif in_tts1 and not line.startswith("   ") and line.rstrip().endswith(":"):
            voices.append(line.strip()[:-1])
    return voices


# A Piper model as the voice map names it, `model: voices/de_DE-thorsten-high.onnx`.
# Piper names every model after its locale, so the part before `_` is the
# language it speaks. A pattern rather than a YAML parser, because the host
# CLI is stdlib-only.
_PIPER_MODEL = re.compile(
    r"^\s+model:\s*voices/(?P<name>(?P<language>[a-z]{2,3})_[A-Za-z]{2}-[\w.-]+)\.onnx\s*$")


def voice_models(voice_map: str, languages) -> list[str]:
    """The Piper models the voice map uses for `tts-1` in these languages,
    each once, in map order.

    The speech server reads a model only from disk, and its image
    downloads two English ones by itself. The rest are what a house
    speaking these languages has to fetch. A language is matched on its
    first part, so `de`, `de-DE` and `de_AT` all mean German.
    """
    wanted = {lang.lower().replace("_", "-").split("-")[0]
              for lang in languages if lang}
    models, in_tts1 = [], False
    for line in voice_map.splitlines():
        if line.strip() and not line.startswith((" ", "#")):
            in_tts1 = line.rstrip() == "tts-1:"
        match = _PIPER_MODEL.match(line) if in_tts1 else None
        if match and match["language"] in wanted and match["name"] not in models:
            models.append(match["name"])
    return models


def synthesize(text: str, voice: str = "alloy", speed: float = SPEED) -> bytes:
    """The text spoken as MP3. Raises OSError when the server fails or
    does not answer (`urllib.error.HTTPError` for an answer that is not 2xx)."""
    url = os.environ.get("STACK_SPEECH_URL", _SPEECH_URL).rstrip("/")
    req = urllib.request.Request(
        f"{url}/v1/audio/speech",
        data=json.dumps({
            "model": "tts-1", "input": text,
            "voice": voice, "speed": speed,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


def play(audio: bytes) -> None:
    """Play MP3 audio on the Mac's speakers and wait until it is done."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
        tmp = f.name
    try:
        subprocess.run(["afplay", tmp], timeout=60)
    finally:
        Path(tmp).unlink(missing_ok=True)


def speak(text: str, voice: str = "alloy", speed: float = SPEED) -> bool:
    """Send text to TTS and play it. Returns True on success."""
    try:
        audio = synthesize(text, voice, speed)
        if not audio:
            return False
        play(audio)
        return True
    except Exception:
        return False



def ask_llm(url: str, key: str, prompt: str, model: str = "") -> str | None:
    """One-shot LLM call. Returns the response text or None."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    prompt = prompt + "\nReply with only your spoken words. No markdown, no formatting."
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 150,
        "temperature": 0.7,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/chat/completions",
            data=body, headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        text = data["choices"][0]["message"]["content"].strip()
        return text
    except Exception:
        return None
