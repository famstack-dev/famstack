"""An answer to the agent's own question needs no name.

The agent asks before it acts on anything unclear: two items fit what
was said, a list to delete still has open items. In a room with several
people only a message that starts with the agent's name reaches it
(`name_trigger.py`), so the natural answer, "Ja, die können weg.", went
nowhere and the conversation the agent started died silently.

So while the agent's last message in a room is a question to someone,
that person's next message in the room counts as addressed, if it comes
within `WINDOW_SECONDS`. Only that one message: after it, the name is
needed again. A message from anyone else, or one that comes later, is
treated as before. Whatever else the agent says in the room closes the
question, since it is no longer the last thing asked.

The question is for whoever sent the message it answers, which is not
always the last person to write in the room.

    questions = OpenQuestions()
    questions.heard("$ask", "@homer:simpson")
    questions.replied("!kitchen", "$ask", "Sollen die auch weg?", at=100.0)
    questions.answers("!kitchen", "@marge:simpson", at=110.0)   # False
    questions.answers("!kitchen", "@homer:simpson", at=120.0)   # True, once
"""

from __future__ import annotations

import re

# How long a question stays open: long enough to read it and type an
# answer, short enough that a message an hour later about something else
# is not taken for one.
WINDOW_SECONDS = 300

# Senders of messages the agent handled, kept for their answers. Far more
# than are ever in flight at once in a household.
_REMEMBERED = 100

# The end of a message, past trailing quotes, brackets, emoji and spaces.
_TRAILING = re.compile(r"[\s\"'”“»«)\]*_~\U0001F300-\U0001FAFF☀-➿]+$")


def asks(text: str) -> bool:
    """True when `text` ends with a question."""
    return _TRAILING.sub("", text or "").endswith("?")


class OpenQuestions:
    """Per room: whom the agent asked, and when. Room ids and person ids as Matrix has them."""

    def __init__(self, window: float = WINDOW_SECONDS) -> None:
        self._window = window
        self._open: dict[str, tuple[str, float]] = {}
        self._senders: dict[str, str] = {}

    def heard(self, event_id: str, person: str) -> None:
        """The agent is handling `event_id`, sent by `person`."""
        if event_id and person:
            self._senders[event_id] = person
        while len(self._senders) > _REMEMBERED:
            del self._senders[next(iter(self._senders))]

    def replied(self, room: str, event_id: str, text: str, *, at: float) -> None:
        """The agent said `text` in answer to `event_id`: a question opens, anything else closes."""
        person = self._senders.get(event_id, "")
        if asks(text) and person:
            self.asked(room, person, at=at)
        else:
            self.closed(room)

    def asked(self, room: str, person: str, *, at: float) -> None:
        if room and person:
            self._open[room] = (person, at)

    def closed(self, room: str) -> None:
        self._open.pop(room, None)

    def answers(self, room: str, person: str, *, at: float) -> bool:
        """True once, for the asked person's first message within the window."""
        asked = self._open.get(room)
        if not asked or asked[0] != person:
            return False
        self._open.pop(room)
        return at - asked[1] <= self._window
