"""Reading a voice message back: is it audio, and what was said.

nanobot builds the agent's text for a voice message as the event's own
text followed by `[transcription: <text>]` (`channels/matrix/runtime.py`,
`_on_media_message`, nanobot 0.3.5). The address rule needs the spoken
part alone, because "Voice message" comes first.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stacklets" / "agent" / "runtime"))

from voice_trigger import PendingVoice, is_voice, quoted, spoken_text  # noqa: E402


def test_what_was_said_is_read_from_after_the_event_text():
    assert spoken_text("Voice message\n[transcription: Stacky, wir brauchen Milch.]") == \
        "Stacky, wir brauchen Milch."


def test_a_voice_message_nobody_could_hear_says_nothing():
    assert spoken_text("Voice message\n[Audio: voice.ogg]") == ""


def test_only_audio_counts_as_voice():
    audio = SimpleNamespace(source={"content": {"msgtype": "m.audio"}})
    photo = SimpleNamespace(source={"content": {"msgtype": "m.image"}})
    assert is_voice(audio) and not is_voice(photo) and not is_voice(SimpleNamespace())


def test_a_held_message_is_released_once():
    pending = PendingVoice()
    pending.hold("$a")
    assert pending.release("$a") and not pending.release("$a") and not pending.release("$b")


def test_what_was_heard_is_quoted_line_by_line_above_the_answer():
    assert quoted("Stacky, Milch\nund Eier", "Erledigt.") == "> Stacky, Milch\n> und Eier\n\nErledigt."
    assert quoted("", "Erledigt.") == "Erledigt."
