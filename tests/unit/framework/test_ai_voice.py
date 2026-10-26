"""`stack.ai.voice`: the speech request a bot sends for a voice message.

`stack ai say` is the surface that speaks on the Mac, and its tests pin
what reaches oMLX. A bot's voice message has no such surface in this
repo: it builds the request with this module and posts it itself. What it
relies on is that the same text gets the same voice as `ai say`, and that
it can ask for Ogg Opus, the format Matrix voice messages use.
"""

from __future__ import annotations

from stack.ai.voice import speech_request, voice_for, voices


class TestAVoiceMessage:

    def test_is_spoken_in_the_voice_ai_say_uses_for_the_same_text(self):
        for language in ("en", "de", "fr"):
            voice = voice_for(voices(None), language)
            message = speech_request("Der Timer ist abgelaufen.", voice,
                                     response_format="opus")
            spoken = speech_request("Der Timer ist abgelaufen.", voice)

            assert {k: v for k, v in message.items() if k != "response_format"} == \
                   {k: v for k, v in spoken.items() if k != "response_format"}

    def test_is_asked_for_as_ogg_opus(self):
        assert speech_request("Bananas are on the list.", voice_for(voices(None), "en"),
                              response_format="opus")["response_format"] == "opus"

    def test_a_regional_language_code_is_its_language(self):
        """The house writes `de-AT`; it is German all the same."""
        assert voice_for(voices(None), "de-AT") == voice_for(voices(None), "de")
