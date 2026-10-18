"""Shared TTS and LLM utilities for the AI stacklet."""

import fcntl
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


# The languages the speech server has a voice of their own for.
VOICED = {"en": "alloy", "de": "onyx"}


def default_voice(language: str) -> str:
    """The voice for a language: German has its own, the rest English."""
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


# Text for the ear: one function for everything that speaks, the bots too.
from stack.ai.speakable import speakable  # noqa: E402,F401


def synthesize(text: str, voice: str = "alloy", speed: float = SPEED, *,
               url: str = "") -> bytes:
    """The text spoken as MP3. Raises OSError when the server fails or
    does not answer (`urllib.error.HTTPError` for an answer that is not 2xx).

    `url` is a speech server the stack does not run (`[ai] speech_url`);
    otherwise `STACK_SPEECH_URL`, otherwise the ai stacklet's own.
    """
    url = (url or os.environ.get("STACK_SPEECH_URL", _SPEECH_URL)).rstrip("/")
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


def play(audio: bytes, volume: int = 100) -> None:
    """Play MP3 audio on the Mac's speakers and wait until it is done.

    `volume` is a percentage of the Mac's own output volume, capped at 100:
    the system volume is never changed, so a muted Mac stays silent.
    """
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
        tmp = f.name
    share = max(0, min(int(volume), 100)) / 100
    try:
        subprocess.run(["afplay", "-v", str(share), tmp], timeout=60)
    finally:
        Path(tmp).unlink(missing_ok=True)


# ── Speaking a longer text ───────────────────────────────────────────────
# A text is spoken in parts, each synthesized while the one before plays,
# so the first sentence is heard after its own synthesis, not the whole
# text's (1.8 s for one sentence of 58 characters on the rig).

# A sentence ends at . ! ? or … before a space and no lowercase letter, so
# "21.00" and "z.B. mit" stay whole.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?![a-zäöüß])")
# Each part is one request to the speech server; a short sentence goes
# with the next.
PART_MIN_CHARS = 25
# One speaker at a time on this Mac: a second `say` waits for the first to
# finish rather than talking over it.
SPEAKER_LOCK = Path(tempfile.gettempdir()) / "stack-speaker.lock"


class NoAudio(Exception):
    """The speech server answered, with nothing to play."""


def parts(text: str) -> list[str]:
    """`text` as the parts it is spoken in, in order."""
    out: list[str] = []
    for sentence in (s.strip() for s in _SENTENCE_END.split(text)):
        if not sentence:
            continue
        if out and len(out[-1]) < PART_MIN_CHARS:
            out[-1] += " " + sentence
        else:
            out.append(sentence)
    return out


def say_aloud(text_parts: list[str], voice: str, *, volume: int = 100, url: str = "",
              wait: bool = True) -> None:
    """Speak the parts on the Mac's speakers, one after the other.

    The first part is synthesized before anything plays, so a speech server
    that fails or does not answer raises here (OSError), as `synthesize`
    does, and one that sends no audio raises NoAudio. A later part that fails ends the speaking after the part before.
    With `wait=False` it returns once the last part has started playing;
    the player keeps the speaker until it is done.
    """
    share = str(max(0, min(int(volume), 100)) / 100)
    audio = synthesize(text_parts[0], voice, url=url)
    if not audio:
        raise NoAudio(text_parts[0])
    with SPEAKER_LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for i, _ in enumerate(text_parts):
            file = _audio_file(audio)
            if i == len(text_parts) - 1:
                _play_last(file, share, lock, wait=wait)
                return
            player = subprocess.Popen(["afplay", "-v", share, file])
            try:
                audio = synthesize(text_parts[i + 1], voice, url=url)
            except OSError:
                audio = b""
            player.wait(timeout=120)
            Path(file).unlink(missing_ok=True)
            if not audio:
                return


def _audio_file(audio: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
        return f.name


def _play_last(file: str, share: str, lock, *, wait: bool) -> None:
    if wait:
        subprocess.run(["afplay", "-v", share, file], timeout=120)
        Path(file).unlink(missing_ok=True)
        return
    # The player inherits the locked file, so the speaker stays taken until
    # it has finished, after this process is gone.
    subprocess.Popen(["/bin/sh", "-c", 'afplay -v "$1" "$2"; rm -f "$2"', "sh", share, file],
                     pass_fds=(lock.fileno(),), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
