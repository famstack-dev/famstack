"""A thread is a conversation. Whose?

Matrix threads are how a family has a back-and-forth without flooding the
room, and nobody re-types "Stacky," on every line of one. So a message
posted inside a thread the agent is having counts as talking to the
agent, the same way the second sentence of a phone call does not need
the other person's name in it.

Read this file as the spec for *which* threads those are, because the
answer is not "all of them". Other famstack bots thread too: the
archivist answers a filing under the upload it filed, the mail bot puts
an email's body under its card. Those threads are their conversations.
Treating every thread as the agent's would put it in the middle of every
document the house files, which is the opposite of the rule the archivist
already applies to itself -- exactly one component responds to a message.

The test for "is this ours" is participation: the thread hangs off
something the agent said, or the agent has spoken in it. Both are facts
on the homeserver, so nothing here depends on the agent remembering
anything across a restart.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "agent" / "runtime"))

from thread_trigger import AgentThreads, thread_root  # noqa: E402

AGENT = "@stacky-bot:home.local"
ROOM = "!family:home.local"


def _in_thread(root: str):
    """A message a family member typed inside the thread rooted at `root`."""
    return SimpleNamespace(
        sender="@marge:home.local",
        source={"content": {
            "body": "and the tent poles?",
            "m.relates_to": {
                "rel_type": "m.thread",
                "event_id": root,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": "$whatever:home.local"},
            },
        }},
    )


def _said_by(sender: str):
    return SimpleNamespace(sender=sender, source={"content": {"body": "..."}})


class _FakeClient:
    """The homeserver, as much of it as this decision reads.

    Two lookups: fetch one event, and iterate a thread's children. Both
    are what nio offers and what the archivist already uses for the same
    question, so the shape here is the real one, not a convenience.
    """

    def __init__(self):
        self.events: dict[str, object] = {}
        self.children: dict[str, list] = {}
        self.event_lookups: list[str] = []
        self.relation_lookups: list[str] = []
        self.get_event_raises: Exception | None = None
        self.relations_raise: Exception | None = None

    async def room_get_event(self, room_id, event_id):
        self.event_lookups.append(event_id)
        if self.get_event_raises:
            raise self.get_event_raises
        return SimpleNamespace(event=self.events.get(event_id))

    def room_get_event_relations(self, room_id, event_id, rel_type=None, **kwargs):
        self.relation_lookups.append(event_id)
        outer = self

        async def _iter():
            if outer.relations_raise:
                raise outer.relations_raise
            for event in outer.children.get(event_id, []):
                yield event

        return _iter()


class TestReadingTheThreadRelation:
    """`thread_root` — which thread an event belongs to, straight off the
    event. No round trip: the relation the sender's client wrote is the
    authoritative answer.

    The sibling of `MicroBot.get_thread_root`, which cannot be imported
    here (the agent runs its own image with neither the bot framework nor
    `lib/stack` mounted). The two read the same Matrix relation, so this
    class is also the check that they agree.
    """

    def test_a_threaded_message_names_its_root(self):
        assert thread_root(_in_thread("$root:home.local")) == "$root:home.local"

    def test_a_plain_reply_is_not_a_thread(self):
        # Quoting a message is not joining a conversation. Element sends
        # `m.in_reply_to` without a `rel_type` for that.
        event = SimpleNamespace(source={"content": {
            "m.relates_to": {"m.in_reply_to": {"event_id": "$x:home.local"}},
        }})
        assert thread_root(event) is None

    def test_a_top_level_message_has_no_root(self):
        assert thread_root(SimpleNamespace(source={"content": {"body": "hi"}})) is None

    def test_an_eventless_argument_is_safe(self):
        # Routing must never raise on a shape we did not expect.
        assert thread_root(None) is None
        assert thread_root(SimpleNamespace(source="not a dict")) is None


class TestThreadsTheAgentIsPartOf:

    @pytest.mark.asyncio
    async def test_a_thread_hanging_off_the_agents_answer_is_its_conversation(self):
        """The common case: the agent answers, someone opens a thread on
        that answer to follow up. The follow-up is obviously for the agent
        and must not need the name again."""
        client = _FakeClient()
        client.events["$stacky-answer"] = _said_by(AGENT)
        threads = AgentThreads()

        assert await threads.observe(client, ROOM, "$stacky-answer", AGENT)

    @pytest.mark.asyncio
    async def test_a_thread_the_agent_has_spoken_in_is_its_conversation(self):
        """The other way in: someone names the agent inside a thread that
        started as something else (a filed document, an email), the agent
        replies there, and the conversation continues. Its own message in
        the thread is what makes the rest of it addressed."""
        client = _FakeClient()
        client.events["$archivist-card"] = _said_by("@archivist-bot:home.local")
        client.children["$archivist-card"] = [
            _said_by("@homer:home.local"), _said_by(AGENT),
        ]
        threads = AgentThreads()

        assert await threads.observe(client, ROOM, "$archivist-card", AGENT)

    @pytest.mark.asyncio
    async def test_another_bots_thread_is_not_the_agents_to_answer(self):
        """The whole reason this is not "any thread": the archivist files
        a document and answers under it, and the family talks back in that
        thread. Those messages belong to the archivist. An agent that
        answered them too would make every filing a two-bot argument."""
        client = _FakeClient()
        client.events["$archivist-card"] = _said_by("@archivist-bot:home.local")
        client.children["$archivist-card"] = [_said_by("@homer:home.local")]
        threads = AgentThreads()

        assert not await threads.observe(client, ROOM, "$archivist-card", AGENT)

    @pytest.mark.asyncio
    async def test_a_thread_between_people_is_not_the_agents_either(self):
        # Two parents planning in a thread are not asking anyone anything.
        client = _FakeClient()
        client.events["$marge-note"] = _said_by("@marge:home.local")
        client.children["$marge-note"] = [_said_by("@homer:home.local")]

        assert not await AgentThreads().observe(client, ROOM, "$marge-note", AGENT)


class TestWhatItCostsToAsk:

    @pytest.mark.asyncio
    async def test_a_known_thread_is_never_looked_up_twice(self):
        """Every message in a busy thread would otherwise re-ask the
        homeserver the same question. A thread the agent is in stays one:
        its message cannot leave the thread, so the answer cannot change."""
        client = _FakeClient()
        client.events["$stacky-answer"] = _said_by(AGENT)
        threads = AgentThreads()

        await threads.observe(client, ROOM, "$stacky-answer", AGENT)
        await threads.observe(client, ROOM, "$stacky-answer", AGENT)

        assert client.event_lookups == ["$stacky-answer"]
        assert threads.includes("$stacky-answer")

    @pytest.mark.asyncio
    async def test_the_agents_own_root_settles_it_without_reading_the_thread(self):
        # Cheapest answer first: one fetch, no relation paging.
        client = _FakeClient()
        client.events["$stacky-answer"] = _said_by(AGENT)

        await AgentThreads().observe(client, ROOM, "$stacky-answer", AGENT)

        assert client.relation_lookups == []

    @pytest.mark.asyncio
    async def test_a_long_thread_cannot_turn_into_unbounded_paging(self):
        """One chat message must cost a bounded number of API calls, even
        in a thread with hundreds of replies."""
        client = _FakeClient()
        client.events["$root"] = _said_by("@marge:home.local")
        client.children["$root"] = [
            *[_said_by("@homer:home.local") for _ in range(50)], _said_by(AGENT),
        ]

        assert not await AgentThreads().observe(client, ROOM, "$root", AGENT, limit=5)

    @pytest.mark.asyncio
    async def test_a_thread_not_ours_is_re_checked_later(self):
        """The negative is not cached, deliberately: the agent can join a
        thread it was not in a minute ago, and the next message in it has
        to see that."""
        client = _FakeClient()
        client.events["$root"] = _said_by("@marge:home.local")
        threads = AgentThreads()

        assert not await threads.observe(client, ROOM, "$root", AGENT)
        client.children["$root"] = [_said_by(AGENT)]
        assert await threads.observe(client, ROOM, "$root", AGENT)


class TestWhenTheHomeserverIsUnhappy:
    """A lookup failure must read as "not addressed", never as an
    exception out of the routing path. Losing a threaded follow-up is a
    disappointment; a raised exception in the message handler takes the
    agent off Matrix for every room."""

    @pytest.mark.asyncio
    async def test_a_failed_root_fetch_falls_through_to_the_thread_scan(self):
        client = _FakeClient()
        client.get_event_raises = ConnectionError("synapse down")
        client.children["$root"] = [_said_by(AGENT)]

        assert await AgentThreads().observe(client, ROOM, "$root", AGENT)

    @pytest.mark.asyncio
    async def test_a_total_failure_is_simply_not_addressed(self):
        client = _FakeClient()
        client.get_event_raises = ConnectionError("synapse down")
        client.relations_raise = ConnectionError("synapse down")

        assert not await AgentThreads().observe(client, ROOM, "$root", AGENT)

    @pytest.mark.asyncio
    async def test_an_unknown_root_is_not_addressed(self):
        # The homeserver answers, it just has nothing for that id.
        assert not await AgentThreads().observe(_FakeClient(), ROOM, "$gone", AGENT)
