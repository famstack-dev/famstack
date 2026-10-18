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


# ── Text for the ear ──────────────────────────────────────────────────────
# Speech engines read markup literally ("asterisk asterisk"), so text that
# may be markdown is said the way a person would say it before it is spoken.

_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://([^/\s]+)\S*")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(\S(?:.*?\S)?)\1")
_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_ITEM = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")
_ENDS_SENTENCE = (".", "!", "?", ":", ";")


def speakable(text: str) -> str:
    """`text`, maybe markdown, as a person would say it: one line of sentences.

    Emphasis and code marks go, a link becomes its text, a bare URL its
    domain, emoji go; a heading, a list item or a table row becomes a
    sentence of its own (a row as "column: value, ...").
    """
    lines, header = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _RULE.match(line):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if header is None:
                header = cells
                continue
            line = ", ".join(f"{h}: {c}" for h, c in zip(header, cells))
        line = _ITEM.sub("", _HEADING.sub("", line))
        line = _URL.sub(r"\1", _LINK.sub(r"\1", line))
        line = _EMPHASIS.sub(r"\2", _CODE.sub(r"\1", line))
        line = re.sub(r"\s+([.!?,:;])", r"\1", _EMOJI.sub("", line)).strip()
        if line:
            lines.append(line)
    # A line that ran into the next gets a full stop; the last stays as written.
    stopped = [ln if ln.endswith(_ENDS_SENTENCE) else ln + "." for ln in lines[:-1]]
    return " ".join(stopped + lines[-1:])


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
