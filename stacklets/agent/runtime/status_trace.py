"""What the agent is doing, in one status message that grows while it works.

A turn that needs a search, a page and a list takes Stacky ten to thirty
seconds. A typing indicator alone reads as "stuck" after a few of them.
So a turn that is not answered quickly gets one status message, and
while the agent works, a short line is appended to it now and then:

    Moment, ich schau mal nach …
    Hab was gefunden, ich prüf es noch genauer …
    Ich trag das ein …

The answer comes as a message of its own; the status stays above it as a
trace of what the agent did. It is a notice (`m.notice`), which Matrix's
default push rules do not notify about and Element shows greyed out, so
the trace never buzzes a phone. Only the answer does.

The lines are ours, not the model's: nanobot's raw tool hints read like a
shell (`$ stack memory list show einkaufsliste`), and the model's own
progress text is unpredictable. When nanobot compacts the conversation
during a turn ("Compressing context…"), that is a line too, in the
family's words. Each tool round is mapped to a phase
(looking, looking again, writing down) and a phrase for it is picked at
random from a few, in the household's language, never the same twice in
one trace.

`Trace` decides what the status says and when it changes, from the times
it is given; the caller posts and edits the message.

    trace = Trace(started=0.0, lang="de")
    trace.tools(["memory_search"])
    trace.due(at=5.0)     # None: answered this fast, there is no status
    trace.due(at=6.0)     # ["Moment, ich schau mal nach …"]
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

# A turn answered within this many seconds needs no status at all.
FIRST_AFTER = 6.0
# At most one new line this often, so the status does not chatter.
GAP = 10.0
# With nothing new for this long, one line saying it is still at it.
STILL_AFTER = 20.0
# A status longer than this stops growing; the answer is what matters.
MAX_LINES = 5
# Every turn ends in an answer, even a failed one ("Sorry, I encountered an
# error."), except one that is cancelled. Far longer than any turn takes,
# so the status of such a turn stops at some point instead of never.
GIVE_UP_AFTER = 600.0

LOOK, CHECK, WRITE, THINK, STILL, TIDY = "look", "check", "write", "think", "still", "tidy"

# Tools that change a list or a page; everything else reads.
_WRITERS = {"list_edit", "write_file", "edit_file", "apply_patch"}

PHRASES: dict[str, dict[str, tuple[str, ...]]] = {
    "de": {
        LOOK: ("Moment, ich schau mal nach …", "Lass mich kurz nachsehen …",
               "Ich such das gerade raus …"),
        CHECK: ("Hab was gefunden, ich prüf es noch genauer …",
                "Da ist was, ich schau es mir genauer an …",
                "Ich seh noch an einer Stelle nach …"),
        WRITE: ("Ich trag das ein …", "Ich kümmer mich drum …", "Ich schreib das auf …"),
        THINK: ("Moment …", "Ich denk kurz nach …"),
        STILL: ("Dauert noch einen Moment …", "Gleich hab ich's …"),
        TIDY: ("Moment, ich sortier kurz meine Gedanken und Notizen …",
               "Ich bring kurz meine Gedanken und Notizen in Ordnung …"),
    },
    "en": {
        LOOK: ("One moment, let me check …", "Let me look that up …",
               "Looking for it …"),
        CHECK: ("Found something, checking the details …",
                "There is something, taking a closer look …",
                "Checking one more place …"),
        WRITE: ("Writing that down …", "On it …", "Putting it on the list …"),
        THINK: ("One moment …", "Thinking it over …"),
        STILL: ("Still on it …", "Almost there …"),
        TIDY: ("Hang on, let me sort my thoughts and notes …",
               "Tidying up my thoughts and notes for a second …"),
    },
}


def phase_of(tools: Sequence[str], *, looked_before: bool) -> str:
    """The phase a round of tool calls belongs to."""
    if any(name in _WRITERS for name in tools):
        return WRITE
    return CHECK if looked_before else LOOK


class Trace:
    """The lines of one turn's status, and when they change.

    `tools` records what the agent started doing; `due` answers, at a given
    time, the whole status to show if it should change now, or None; `over`
    says when it will not change again.
    """

    def __init__(self, *, started: float, lang: str,
                 pick: Callable[[Sequence[str]], str] = random.choice) -> None:
        self._started = started
        self._phrases = PHRASES.get((lang or "")[:2].lower(), PHRASES["en"])
        self._pick = pick
        self._lines: list[str] = []
        self._phases: list[str] = []
        self._pending = THINK
        self._looked = False
        self._last_at = started

    def tools(self, names: Sequence[str]) -> None:
        phase = phase_of(names, looked_before=self._looked)
        self._looked = self._looked or phase in (LOOK, CHECK)
        self._pending = phase

    def tidying(self) -> None:
        """nanobot started compacting the conversation, which can take a while."""
        self._pending = TIDY

    def over(self, *, at: float) -> bool:
        """True once the status is full, or the turn has run past all patience."""
        return len(self._lines) >= MAX_LINES or at - self._started >= GIVE_UP_AFTER

    def due(self, *, at: float) -> list[str] | None:
        if at - self._started < FIRST_AFTER or self.over(at=at):
            return None
        if not self._lines:
            return self._add(self._pending, at)
        if at - self._last_at < GAP:
            return None
        if self._pending != self._phases[-1]:
            return self._add(self._pending, at)
        if at - self._last_at >= STILL_AFTER and self._phases[-1] != STILL:
            return self._add(STILL, at)
        return None

    def _add(self, phase: str, at: float) -> list[str]:
        fresh = [p for p in self._phrases[phase] if p not in self._lines]
        self._lines.append(self._pick(fresh or self._phrases[phase]))
        self._phases.append(phase)
        self._pending = phase
        self._last_at = at
        return list(self._lines)
