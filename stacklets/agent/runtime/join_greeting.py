"""What the agent says the moment it is invited into a room.

Being added to a room is the one moment a family is actually looking at
the agent and wondering what it is for. nanobot's stock invite handler
joins and says nothing, so the answer they get is silence, and the next
question is "is it broken?".

This module holds the *prompt* half of the join greeting: the turn the
agent is asked to take once it has joined. The wiring lives in
`sitecustomize.py`; keeping the words here means they can be read and
changed without touching a monkeypatch, and asserted in a unit test.

WHY A GENERATED GREETING AND NOT A CANNED ONE

The archivist's welcome is a fixed block of text, and rightly so: it
explains a fixed set of commands. This one has to say what *this room's
topic* is about, which is different in every room and already written
down in the topic's `about.md`. A canned string cannot do that, and
hand-rolling a summariser next to an agent that summarises for a living
would be the wrong kind of simple.

So the agent takes an ordinary turn. It already has the brain
projection mounted and the per-turn briefing naming the room's topic
(see `brief.py`), which means the greeting is composed the same way
every other answer is, with no second retrieval path to keep correct.

WHAT IT MUST NOT DO

Recite. The topic page and the todo list both go stale, and a greeting
that pastes today's list is wrong by tomorrow and still sitting in the
timeline. It reads the live page at greeting time and points at the
commands for the rest, which is the same pointers-not-dumps rule the
briefing follows.
"""

from __future__ import annotations

# Framed as an instruction to the agent, not as something a person said,
# because it is injected into the turn loop rather than posted to the
# room. Only the agent's reply is visible to the family.
_PREAMBLE = (
    "[You have just been invited into this room and have joined it. "
    "No one has spoken to you yet.]\n\n"
)

# Shared across both greetings. The "look first, then write once" rule is
# not style: the model otherwise posts "let me check what this room is
# about" as a message of its own and then either repeats itself or, in a
# room with nothing to look up, never speaks again. Both were observed.
_STYLE = (
    "\n\nLook anything up BEFORE you write, and then send exactly one "
    "message. Do not announce what you are about to do, and do not "
    "narrate your own tool use. Keep it to a few lines. No headings, no "
    "bullet list longer than two items. Do not end with an offer of "
    "further help or a sign-off. Sound like a person joining a "
    "conversation, not a manual."
)

_TOPIC_GREETING = (
    "Introduce yourself in one short line, then say what this room's "
    "topic '{topic}' is about, in your own words, from its page in the "
    "vault. If it has open todos, say how many and name one or two, then "
    "say you can add items, tick them off, or change them."
)

_PLAIN_GREETING = (
    "This room has no topic page, so do not describe one and do not go "
    "looking for it. Introduce yourself in one short line and say "
    "plainly what you can do: answer questions from the family's own "
    "notes and documents, and keep topic todo lists."
)


def greeting_prompt(topic: str = "") -> str:
    """The turn the agent takes on joining a room.

    `topic` is the vault slug the room maps to, or "" for a DM or any
    other room that has no topic page. The distinction matters more than
    it looks: asked to describe a topic that does not exist, the model
    says "let me check what this room is about" and then stops, which is
    a worse first impression than no greeting at all.
    """
    body = _TOPIC_GREETING.format(topic=topic) if topic else _PLAIN_GREETING
    return _PREAMBLE + body + _STYLE
