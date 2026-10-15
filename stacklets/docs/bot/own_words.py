"""Whether a note is in the writer's own words, the only notes that become todos.

A note is anything a family member sends that is not a link or a file:
a reminder they typed, something they said into the phone, or text they
copied from somewhere else. Only the first two are things the family
means to do. Copied text carries the plans and advice of whoever wrote
it ("try this framework", "book early"), and a model asked to find the
tasks in it finds them, so a pasted article used to put its author's
suggestions on the family's list.

So the decision is made here, in code, before the model sees the note:

- A voice message is the speaker's own words.
- A typed message is, when it reads like one: a single paragraph, no
  link, short enough to have been typed into a chat.
- Anything longer, or with a link in it, is treated as copied text and
  yields no todos at all, whatever the model would make of it.

The cost is a long, hand-typed note with a task buried in it. That task
is missed, and one sentence to the agent ("put it on the list") fixes
it. The other way round, noise on the list, makes the family stop
trusting the list. What passes this gate still meets the prompt's own
rule: only what the writer says they or the family must do.

    >>> in_own_words("wir sollten bis Freitag den Müll rausbringen", spoken=False)
    True
    >>> in_own_words("Check this out: https://example.com/framework", spoken=False)
    False

Pure functions, no I/O.
"""

from __future__ import annotations

import re

# A chat message somebody typed fits in a few lines. Copied text that
# short still has to pass the prompt's rule, so the limit only needs to
# keep articles, threads and documents out.
MAX_TYPED_CHARS = 300

_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def in_own_words(text: str, *, spoken: bool) -> bool:
    """True for a voice message, or for a typed message that reads like one."""
    if spoken:
        return True
    body = (text or "").strip()
    return (
        bool(body)
        and len(body) <= MAX_TYPED_CHARS
        and not _PARAGRAPH_BREAK.search(body)
        and not _LINK.search(body)
    )
