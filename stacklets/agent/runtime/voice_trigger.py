"""Is this voice message for the agent? Decided on what was said.

A family member walking through the kitchen says "Stacky, wir brauchen
Milch und Eier" into the phone. In a room with several people nanobot
decides whether a message is for the agent before it transcribes
anything, from the event alone: a pill mention, or (with our name
trigger) the agent's name in the text. A voice message has neither, its
text is "Voice message", so in a shared room it never reached the agent.

So a voice message in a shared room is let through nanobot's gate,
transcribed by nanobot as usual, and only then held to the rule typed
text meets, with one difference: the transcript has to address the agent
by name, but the name counts by its sound, since whisper writes "Stocky"
or "Stacke" for "Stacky" (`name_trigger.addressed_by_voice`, the rule the
bot framework uses too). One that does not is dropped without an answer,
exactly like a typed message that does not. In a direct chat, or in a thread the agent is part of,
nanobot's own gate already lets it through and nothing here applies.

The price is a transcription for every voice message in a shared room
the agent is in, also the ones meant for someone else. The transcript
is not kept and not answered.

nanobot hands the transcript to the agent as `[transcription: <text>]`
after the event's own text (`channels/matrix/runtime.py`,
`_on_media_message`); `spoken_text` reads it back from there.
"""

from __future__ import annotations

import re
from typing import Any

# The Matrix message type of a voice message (and any other audio file).
VOICE_MSGTYPE = "m.audio"

_TRANSCRIPTION = re.compile(r"\[transcription: (?P<text>.*)\]\s*$", re.DOTALL)


def is_voice(event: Any) -> bool:
    """True for an audio message, the kind phones send as voice messages."""
    source = getattr(event, "source", None)
    content = source.get("content") if isinstance(source, dict) else None
    return isinstance(content, dict) and content.get("msgtype") == VOICE_MSGTYPE


def spoken_text(content: str) -> str:
    """What was said, from the text nanobot built for a voice message; "" if nothing was heard."""
    match = _TRANSCRIPTION.search(content or "")
    return match["text"].strip() if match else ""


def quoted(heard: str, answer: str) -> str:
    """The answer to a voice message, after what was heard as a quote.

    The quote is what the agent acted on, so a word Whisper got wrong is
    visible next to what it caused. Markdown, which reads the same in
    every household language.
    """
    quote = "\n".join(f"> {line}" for line in heard.splitlines() if line.strip())
    return f"{quote}\n\n{answer}" if quote and answer else answer


class PendingVoice:
    """Voice messages let through the gate, waiting for their transcript.

    The gate (`_should_process_message`) is synchronous and runs before the
    transcription; the transcript arrives later in `_handle_message`. This
    remembers, by event id, which messages still have to prove they were
    addressed to the agent.

        pending = PendingVoice()
        pending.hold("$event")          # in the gate
        pending.release("$event")       # True once, in the handler

    It also keeps what was heard in each voice message the agent answers,
    until the answer goes out (`heard` / `answering`).
    """

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._heard: dict[str, str] = {}

    def heard(self, event_id: str, text: str) -> None:
        if event_id and text:
            self._heard[event_id] = text

    def answering(self, event_id: str) -> str:
        """What was heard in the message this answer is for, once; "" for any other."""
        return self._heard.pop(event_id, "")

    def hold(self, event_id: str) -> None:
        if event_id:
            self._held.add(event_id)

    def release(self, event_id: str) -> bool:
        """True if this message was held, so its transcript decides."""
        if event_id in self._held:
            self._held.discard(event_id)
            return True
        return False
