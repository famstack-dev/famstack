"""A message for people that a screen can say in its own language.

    from stack.said import Said, fields

    step = Said("Downloading the language model", "ai.download.language_model")
    ctx.step(step)                      # the terminal prints the English text
    fields(step)                        # {"key": "ai.download.language_model"}

    raise Said.error("Not enough free disk space ...", "ai.no_room", gb=6.2)

The command line speaks English. A screen (the setup page, the dashboard)
speaks the family's language, and it shows the same run: its steps, and
the reason a run failed. A `Said` is a `str`, so every output, log and
exception takes it as it takes any text, and the terminal is unchanged.
Beside the text it carries a `key` and `values`; the run's events and its
result carry both (`fields`), and the screen looks the key up in its own
words. A key it does not know, or a text without one, is shown as it is.

The words for the keys live with the screen that shows them, never here:
the framework only carries the key.
"""

from __future__ import annotations

__all__ = ["Said", "SaidError", "fields"]


class Said(str):
    """A text with a key a screen can translate, and the values it names.

        Said("Downloading Whisper's model", "ai.download.whisper_model")
        Said("Not enough free disk space ...", "ai.no_room", {"gb": 6.2})
    """

    key: str
    values: dict

    def __new__(cls, text: str, key: str, values: dict | None = None):
        said = super().__new__(cls, text)
        said.key = key
        said.values = dict(values or {})
        return said

    @staticmethod
    def error(text: str, key: str, **values) -> SaidError:
        """An exception whose message is a `Said`: a hook raises it, and the
        run's result carries its key (`fields`)."""
        return SaidError(Said(text, key, values))


class SaidError(RuntimeError):
    """An error a screen can say in its own language: `str(e)` is the
    English text, `fields(e)` its key and values."""

    def __init__(self, said: Said):
        super().__init__(said)
        self.said = said


def fields(message: object) -> dict:
    """The key and values `message` carries for screens, or {} for a plain
    text: `{"key": "ai.no_room", "values": {"gb": 6.2}}`. Takes a `Said`
    or an exception that carries one.

        fields(Said("Starting oMLX", "ai.start"))   # {"key": "ai.start"}
        fields("Starting oMLX")                     # {}
    """
    said = getattr(message, "said", message)
    if not isinstance(said, Said):
        return {}
    return {"key": said.key, **({"values": said.values} if said.values else {})}
