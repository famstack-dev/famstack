"""The stack's voice: which model speaks a text, and where the request goes.

The stack speaks through an OpenAI-compatible speech server, `POST
/v1/audio/speech`: by default oMLX, the engine that serves its language
model. This module chooses the voice for a text, by its language or by
name, builds the request's body, and names the server that gets it and
the key it carries. `stack ai say` and the bots' voice messages both
build their requests here, so the same text is spoken in the same voice
wherever it is said:

    url, key = speech_server(ai, openai_url, openai_key)
    table = voices(ai.get("voices"))        # a bot: json.loads(SPEECH_VOICES_JSON)
    voice = voice_for(table, "de")          # or voice_named(table, "stacky-de")
    body = speech_request("Der Timer ist abgelaufen.", voice, response_format="opus")
    # POST {url}/v1/audio/speech, Authorization: Bearer {key}, JSON body

The stack's own voices, decided by Arthur on 2026-10-26 after comparing
them by ear with Piper's German voices and Qwen3-TTS's built-in
speakers, which sounded robotic or foreign in German:

- **English**: Kokoro 82M with its `af_heart` speaker. Kokoro speaks no
  German.
- **German**: Qwen3-TTS 0.6B Base, cloning one voice that Qwen3-TTS
  VoiceDesign drew once from a description. oMLX keeps no stored voices,
  so the reference clip and its transcript go with every German request.
  VoiceDesign never draws the same voice twice, so the clip in `voices/`
  is the voice and is kept like a logo.

`[ai.voices.<lang>]` in stack.toml names another voice for a language,
for a server with other models (an OpenAI-compatible one that cannot
clone, say): the model and voice to ask for, and optionally a clip the
stack ships. An entry replaces that language's voice whole. Every
language without a voice of its own is read by the English one.

Standard library only: the host CLI imports it, and the bot runner
mounts `lib/stack`, clips included. The bots get the voice table as
JSON in their environment, because containers do not read stack.toml.
"""

from __future__ import annotations

import base64
import functools
from dataclasses import dataclass
from pathlib import Path

from .language import language_code, language_name

# The welcome message's pace, chosen by ear: 1.0 sounded rushed. Every
# command that speaks uses it. Kokoro honours it; Qwen3-TTS ignores it
# (mlx-audio 0.4: "not directly supported yet") and keeps its own pace.
SPEED = 0.8

# The engine `stack up ai` installs, where speech goes when `[ai]` names
# no server at all.
MANAGED_URL = "http://localhost:42060"

# The clips the stack ships, each `voices/<name>.wav`, with the words
# spoken in it: a clone needs both, and they must match.
CLIPS = {
    "stacky-de": ("Stell dir vor, du machst einfach ein Foto von einem Dokument und "
                  "schickst es in deinen privaten Messenger. Wie WhatsApp, aber lokal."),
}

# The stack's own voice for each language `[ai.voices]` leaves out, as an
# entry there would name it. oMLX serves a model under its folder name.
DEFAULT_VOICES = {
    "en": {"model": "Kokoro-82M-bf16", "voice": "af_heart"},
    "de": {"model": "Qwen3-TTS-12Hz-0.6B-Base-8bit", "clip": "stacky-de"},
}

# Where oMLX downloads the stack's own models from, by the name a request
# gives them. A model written as a Hugging Face repo is its own source.
REPOS = {
    "Kokoro-82M-bf16": "mlx-community/Kokoro-82M-bf16",
    "Qwen3-TTS-12Hz-0.6B-Base-8bit": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
}

_CLIPS = Path(__file__).parent / "voices"


@dataclass(frozen=True)
class Voice:
    """One voice: the model that speaks, and how it is asked.

    A voice is a speaker the model has (`speaker`), a clone of a clip the
    stack ships (`clip`, told the `language` it speaks), or both.

        voices(None)["en"].model  # "Kokoro-82M-bf16"
    """
    model: str
    speaker: str = ""
    clip: str = ""
    language: str = ""

    @property
    def name(self) -> str:
        """What a person calls it: the speaker, or the clip's name."""
        return self.speaker or self.clip

    @property
    def repo(self) -> str:
        """The Hugging Face repo oMLX downloads the model from, or "" when
        the stack does not know one."""
        return self.model if "/" in self.model else REPOS.get(self.model, "")


def voice_table(configured: dict | None) -> dict[str, dict]:
    """The voice entries in force: `[ai.voices]` over the stack's own, by
    two-letter language code. What the bots get as `SPEECH_VOICES_JSON`."""
    return {**DEFAULT_VOICES,
            **{language_code(lang): dict(entry) for lang, entry in (configured or {}).items()}}


def voices(configured: dict | None) -> dict[str, Voice]:
    """The voices in force, by two-letter language code.

    `configured` is `[ai.voices]`, or the table a bot read from its
    environment. Raises ValueError for an entry that names no model, or a
    clip the stack does not ship, saying which entry it is.
    """
    return {lang: _voice(lang, entry) for lang, entry in voice_table(configured).items()}


def _voice(language: str, entry: dict) -> Voice:
    model = str(entry.get("model", "")).strip()
    if not model:
        raise ValueError(f"[ai.voices.{language}] names no model.")
    clip = str(entry.get("clip", ""))
    if clip and clip not in CLIPS:
        raise ValueError(f"[ai.voices.{language}] names the clip '{clip}', which the stack "
                         f"does not ship. Clips: {', '.join(CLIPS)}")
    return Voice(model=model, speaker=str(entry.get("voice", "")), clip=clip,
                 language=language_name(language).lower() if clip else "")


def voice_for(table: dict[str, Voice], language: str) -> Voice:
    """The voice for a language: its own when it has one, else English."""
    return table.get(language_code(language)) or table["en"]


def voice_named(table: dict[str, Voice], name: str) -> Voice | None:
    """The voice a person names, `af_heart` or `stacky-de` unless
    `[ai.voices]` names others, or None when there is no such voice."""
    return next((voice for voice in table.values() if voice.name == name), None)


def speech_request(text: str, voice: Voice, *, response_format: str = "mp3",
                   speed: float = SPEED) -> dict:
    """The JSON body of a speech request for `text` in `voice`: the model
    and voice exactly as named, and a clip only for a voice that has one.

    `response_format` is `mp3` for the Mac's player, `opus` (Ogg Opus)
    for a Matrix voice message. `text` should already be written for the
    ear (`stack.ai.speakable`).
    """
    body = {"model": voice.model, "input": text, "speed": speed,
            "response_format": response_format}
    if voice.speaker:
        body["voice"] = voice.speaker
    if voice.clip:
        body["language"] = voice.language
        body["ref_audio"] = _clip(voice.clip)
        body["ref_text"] = CLIPS[voice.clip]
    return body


@functools.cache
def _clip(name: str) -> str:
    """A shipped clip, base64 as oMLX takes it. Read once per process:
    a German text is several requests, one per sentence."""
    return base64.b64encode((_CLIPS / f"{name}.wav").read_bytes()).decode()


def speech_server(ai: dict, openai_url: str, openai_key: str) -> tuple[str, str]:
    """Where speech goes, as the server's root address, and the key it carries.

    `[ai] speech_url` with `[ai] speech_key` when it names a server of
    its own: that server gets the requests, and never the AI server's
    key. Otherwise the AI server itself, `openai_url` with `openai_key`
    as the stack resolved them, since oMLX speaks as well as it writes.
    An empty key means none is sent. The `/v1` of an address copied from
    `openai_url` is dropped, because requests add it.
    """
    if ai.get("speech_url"):
        return _root(ai["speech_url"]), ai.get("speech_key", "")
    return _root(openai_url or MANAGED_URL), openai_key


def _root(url: str) -> str:
    url = url.strip().rstrip("/")
    return url.removesuffix("/v1")
