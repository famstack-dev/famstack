"""Is this message part of a conversation the agent is already having?

`name_trigger.py` answers "was the agent addressed *in this sentence*".
That is the right question for the main timeline, where every message
lands next to every other one and the only thing tying a request to a
responder is the name in it. Inside a Matrix thread it is the wrong
question. A thread *is* the tie: it is a bounded conversation with a
root, and nobody says the other person's name on every line of one.

    Stacky, what do we still need for camping?
      └─ thread
         Stacky: Tent poles, the gas cartridge, ...
         and the sleeping mats?              <- addressed, no name
         put those on the list too           <- addressed, no name

So this module supplies the other half of the gate: a message in a
thread the agent is part of counts as being spoken to.

WHICH THREADS

Not all of them, and that boundary is the entire design. famstack's
other bots thread as well -- the archivist answers a filing under the
document that was uploaded, the mail bot posts an email body under its
card -- and those threads are *their* conversations, in the same rooms.
An agent that claimed every thread would answer into every filing
discussion in the house, breaking the rule the archivist already applies
to itself: exactly one component responds to a message.

A thread is the agent's when the agent participates in it, which is two
facts on the homeserver:

  1. the thread hangs off something the agent said, or
  2. the agent has posted in the thread.

(1) is the ordinary case -- the agent answers, someone opens a thread on
that answer. (2) is how the agent joins a thread that started as
something else: named inside an archivist filing thread, it replies
there, and from then on the thread is a conversation with it.

Both are read from Matrix rather than remembered, so a restart loses
nothing: the agent's own message is still in the thread afterwards.

WHY THE ANSWER IS CACHED ONE WAY ONLY

A positive is permanent -- a message cannot leave a thread -- so it is
kept, and a busy thread costs one lookup instead of one per line. A
negative is not cached, because a thread the agent has nothing to do
with at 10:00 is one it was invited into at 10:01.

RELATION TO `MicroBot.get_thread_root`

The bot framework reads the same `m.thread` relation, and scans thread
children the same way in `_thread_envelopes`. It is not imported here:
the agent is a separate image running the nanobot harness, with neither
`microbot.py` (which needs aiohttp, markdown, loguru and the bot
framework's own room context) nor `lib/stack` mounted -- and mounting
`lib/stack` pulls the whole CLI framework in through its package init
for the sake of six lines. What is duplicated is a read of the Matrix
spec, not of anyone's implementation, which is why the duplication is
acceptable and the tests state the shared contract. Sharing the
matcher properly is tracked as step 4 of docs/design/brain/write-layer.md.
"""

from __future__ import annotations

import logging

_log = logging.getLogger("agent.runtime.thread")

# How many of a thread's messages to read looking for one of the agent's
# own. Newest first, so a conversation the agent is in is found in the
# first few; the cap is what stops one chat message in a thread with
# hundreds of replies from becoming hundreds of API calls.
_SCAN_LIMIT = 20


def thread_root(event) -> str | None:
    """The id of the thread `event` belongs to, or None if it is top-level.

    Reads the relation the sender's own client wrote, so there is no
    round trip and no bookkeeping: Matrix is the ledger. A plain reply
    (`m.in_reply_to` with no `rel_type`) is a quote, not a conversation,
    and is deliberately not a thread. An event of an unexpected shape
    simply is not in one -- routing must never raise on it.
    """
    source = getattr(event, "source", None)
    content = source.get("content") if isinstance(source, dict) else None
    relation = content.get("m.relates_to") if isinstance(content, dict) else None
    if not isinstance(relation, dict) or relation.get("rel_type") != "m.thread":
        return None
    root = relation.get("event_id")
    return root if isinstance(root, str) and root else None


class AgentThreads:
    """The threads this agent is a participant in.

    One instance per running channel. `observe` asks the homeserver
    about a thread the first time a message arrives in it and remembers
    a yes; `includes` is the cheap read the message gate uses, so the
    part of this that runs on the synchronous routing path is a set
    lookup and nothing else.
    """

    def __init__(self) -> None:
        self._ours: set[str] = set()

    def includes(self, root: str) -> bool:
        """Whether `root` is known to be a thread the agent is in."""
        return root in self._ours

    async def observe(
        self, client, room_id: str, root: str, agent_user_id: str,
        *, limit: int = _SCAN_LIMIT,
    ) -> bool:
        """Settle whether the thread at `root` is the agent's, and record it.

        Best-effort by construction: every homeserver failure reads as
        "not the agent's thread". Losing a threaded follow-up costs the
        family one repeated name; an exception escaping here would come
        out of the channel's message handler and take the agent off
        Matrix for every room at once.
        """
        if root in self._ours:
            return True
        if await self._agent_in_thread(client, room_id, root, agent_user_id, limit):
            self._ours.add(root)
            return True
        return False

    async def _agent_in_thread(
        self, client, room_id: str, root: str, agent_user_id: str, limit: int,
    ) -> bool:
        # Cheapest question first: a thread rooted at the agent's own
        # message is the agent's, and that is one fetch with no paging.
        try:
            resp = await client.room_get_event(room_id, root)
            if getattr(getattr(resp, "event", None), "sender", None) == agent_user_id:
                return True
        except Exception:
            # Fall through rather than return: the thread scan below can
            # still answer yes, and it is the more informative of the two.
            _log.debug("thread root fetch failed for %s", root, exc_info=True)

        try:
            examined = 0
            async for related in client.room_get_event_relations(
                room_id, root, _thread_relationship(),
            ):
                examined += 1
                if examined > limit:
                    break
                if getattr(related, "sender", None) == agent_user_id:
                    return True
        except Exception:
            _log.debug("thread relations fetch failed for %s", root, exc_info=True)
        return False


def _thread_relationship():
    """nio's `RelationshipType.thread`, or the wire value if nio is absent.

    The tests drive this module with a hand-written client so they can
    state the rule without a homeserver, and that client does not need
    nio installed to do it. Importing lazily keeps the dependency where
    it belongs -- in the container -- instead of in the spec.
    """
    try:
        from nio.api import RelationshipType

        return RelationshipType.thread
    except Exception:
        return "m.thread"
