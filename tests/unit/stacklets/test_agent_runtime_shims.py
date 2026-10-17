"""What the agent's nanobot shims promise: they are actually attached.

`sitecustomize.py` patches nanobot internals at interpreter startup. Every
patch is wrapped in try/except-and-log, deliberately, because a broken shim
must never stop the agent answering. The cost of that design is that a shim
which fails to attach looks exactly like one that worked: the agent starts,
nothing raises, and the capability is simply absent. Three tools sat dead in
the image for weeks that way.

So these tests assert the *attached state*, never "it did not raise". A shim
that swallows its own failure leaves the original symbol in place, and the
assertions below fail on that. That is the whole point of the file.

They also make `sitecustomize.py`'s PIN / RECHECK list executable. Bump
`nanobot-ai`, move one of those symbols, and the unit lane goes red here
instead of the breakage reaching the rig as a log line nobody reads.
"""

from __future__ import annotations

import importlib
import sys

import pytest

SHIMMED_MODULES = ("sitecustomize", "brief", "state_log",
                   "memory_tool", "person_tool", "history_tool",
                   "name_trigger", "thread_trigger", "join_greeting", "vault_write",
                   "list_tool", "tool_trim", "voice_trigger", "open_question",
                   "status_trace", "turn")


# The stub nanobot itself lives in conftest as `nanobot_stub`, shared with
# the vault-tool tests that drive the tools these shims register.

@pytest.fixture
def nanobot(monkeypatch, nanobot_stub):
    """Install a stub nanobot and import `sitecustomize` against it.

    Returns a callable so a test can drop a symbol first and watch what
    survives. Modules are purged before each import so the shims re-run
    rather than returning a cached, already-patched module.
    """
    def _load(drop: str | None = None):
        mods = nanobot_stub()
        if drop:
            module_name, _, attr = drop.rpartition(".")
            delattr(mods[module_name], attr)
        for name, module in mods.items():
            monkeypatch.setitem(sys.modules, name, module)
        for name in SHIMMED_MODULES:
            monkeypatch.delitem(sys.modules, name, raising=False)
        # The container mounts lib/stack/name_trigger.py as `name_trigger`.
        monkeypatch.setitem(sys.modules, "name_trigger",
                            importlib.import_module("stack.name_trigger"))
        importlib.import_module("sitecustomize")
        return mods

    yield _load

    for name in SHIMMED_MODULES:
        sys.modules.pop(name, None)


def _discovered(mods) -> set[str]:
    loader = mods["nanobot.agent.tools.loader"].ToolLoader()
    return {t.__name__ for t in loader.discover()}


# ── the tools are reachable by the agent ─────────────────────────────────

def test_vault_tools_are_registered(nanobot):
    """Without this, the agent cannot search the vault or read a profile.

    `install()` appends to `ToolLoader.discover`, so the check is what a
    freshly built loader hands back, which is what nanobot itself asks for.
    """
    mods = nanobot()
    assert _discovered(mods) == {"MemorySearchTool", "MemoryPersonTool",
                                 "MemoryHistoryTool", "ListEditTool", "CalendarTool"}


def test_asking_the_vault_when_something_happened_is_a_tool(nanobot):
    """Not a line in a skill, which is what it was and why it did nothing.

    Asked what Homer had been up to lately, the agent called
    `memory_search` four times with progressively vaguer queries and never
    ran the command the skill told it to. A model picks from the tools it
    can see; prose about a shell command is something it has to remember
    to remember. So the registration itself is the behaviour under test.
    """
    mods = nanobot()

    assert "MemoryHistoryTool" in _discovered(mods)


def test_the_briefing_is_one_of_nanobots_runtime_context_providers(nanobot, tmp_path):
    """Every turn, nanobot asks its providers for context and appends it.

    The briefing is how a shared room knows who is speaking. Through the
    provider seam it rides on the user's message, and nanobot keeps it
    with that message in the history, so the prompt the model cached last
    turn is still the start of this one.
    """
    import asyncio
    import types as _types

    profile = tmp_path / "vault" / "homer" / "about.md"
    profile.parent.mkdir(parents=True)
    profile.write_text("> Safety inspector at the plant\n")
    loop = nanobot()["nanobot.agent.loop"].AgentLoop()
    request = _types.SimpleNamespace(sender_id="@homer:simpson", metadata={},
                                     workspace=tmp_path)

    [provider] = loop._runtime_context_providers
    block = asyncio.run(provider(request))

    assert block.source == "famstack-brief"
    assert block.content.splitlines()[1].startswith("Sent "), "the date comes first"
    assert "You are speaking with homer (@homer). Safety inspector at the plant" in block.content
    assert block.content.startswith("[Runtime Context")


def test_the_message_list_stays_nanobots_own(nanobot):
    mods = nanobot()
    assert mods["nanobot.agent.context"].ContextBuilder.build_messages.__name__ == "build_messages"


def test_state_log_is_opt_in(nanobot, monkeypatch):
    monkeypatch.setenv("AGENT_STATE_LOG", "1")
    mods = nanobot()
    ctx = mods["nanobot.agent.context"]
    assert ctx.ContextBuilder.build_messages.__name__ == "_build_messages_logged"


def test_being_named_counts_as_a_mention(nanobot, monkeypatch):
    """Without this shim the agent ignores everyone who does not use a pill.

    Driven through nanobot's own gate rather than the matcher directly:
    the matcher is specified in `tests/unit/framework/test_name_trigger.py`, and what
    is at stake here is that nanobot actually asks it.
    """
    monkeypatch.setenv("AGENT_NAME", "Stacky")
    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()

    class _Event:
        pill_mention = False
        body = "Stacky, what's on our list?"

    assert channel._is_bot_mentioned(_Event())


def test_a_pill_mention_still_wins_on_the_original_path(nanobot, monkeypatch):
    """The shim widens the gate; it must never narrow it.

    A pill from someone who never says the name has to keep working
    even when the name matcher would say no.
    """
    monkeypatch.setenv("AGENT_NAME", "Stacky")
    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()

    class _Event:
        pill_mention = True
        body = "what's on our list?"

    assert channel._is_bot_mentioned(_Event())


def _threaded(root, sender="@marge:home.local"):
    import types as _types

    return _types.SimpleNamespace(sender=sender, source={"content": {
        "body": "and the sleeping mats?",
        "m.relates_to": {"rel_type": "m.thread", "event_id": root},
    }})


def _room_with_thread(channel, *, root_sender, replies=()):
    """Give the stub channel a homeserver holding one thread."""
    import types as _types

    async def room_get_event(room_id, event_id):
        return _types.SimpleNamespace(
            event=_types.SimpleNamespace(sender=root_sender),
        )

    def room_get_event_relations(room_id, event_id, rel_type=None, **kwargs):
        async def _iter():
            for sender in replies:
                yield _types.SimpleNamespace(sender=sender)
        return _iter()

    channel.client.room_get_event = room_get_event
    channel.client.room_get_event_relations = room_get_event_relations
    return _types.SimpleNamespace(room_id="!family:home.local")


def test_a_reply_in_the_agents_own_thread_is_processed(nanobot):
    """Without this shim a thread with the agent stalls after one turn.

    Driven through `_on_message` rather than the matcher, because the
    shim is in two halves — an async lookup and a sync gate — and only
    the whole path proves they are wired to each other. The message
    carries no pill and no name: the thread is the entire signal.
    """
    import asyncio

    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()
    room = _room_with_thread(channel, root_sender=channel.config.user_id)
    event = _threaded("$stacky-answer")

    asyncio.run(channel._on_message(room, event))

    assert channel.processed == [event]


def test_a_reply_to_the_agents_message_is_processed(nanobot):
    """Reply to Stacky's question, no name, no pill: the reply is the address."""
    import asyncio
    import types as _types

    channel = nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()
    room = _room_with_thread(channel, root_sender=channel.config.user_id)
    event = _types.SimpleNamespace(sender="@marge:home.local", source={"content": {
        "body": "the oat one", "m.relates_to": {"m.in_reply_to": {"event_id": "$which"}}}})

    asyncio.run(channel._on_message(room, event))

    assert channel.processed == [event]


def test_a_reply_to_someone_else_is_left_alone(nanobot):
    import asyncio
    import types as _types

    channel = nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()
    room = _room_with_thread(channel, root_sender="@homer:home.local")
    event = _types.SimpleNamespace(sender="@marge:home.local", source={"content": {
        "body": "the oat one", "m.relates_to": {"m.in_reply_to": {"event_id": "$homers"}}}})

    asyncio.run(channel._on_message(room, event))

    assert channel.processed == []


def test_another_bots_thread_is_left_alone(nanobot):
    """The shim widens the gate; it must not open it.

    The archivist answers a filing under the uploaded document, in the
    same family room. Every reply there would reach the agent too if
    "in a thread" were the rule, and the family would get two bots
    talking over one receipt.
    """
    import asyncio

    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()
    room = _room_with_thread(
        channel, root_sender="@archivist-bot:home.local",
        replies=["@homer:home.local"],
    )

    asyncio.run(channel._on_message(room, _threaded("$archivist-card")))

    assert channel.processed == []


def test_a_top_level_message_still_needs_addressing(nanobot):
    """Threads change nothing about the main timeline. A message with no
    thread, no pill and no name is not for the agent."""
    import asyncio
    import types as _types

    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()
    room = _types.SimpleNamespace(room_id="!family:home.local")
    event = _types.SimpleNamespace(
        sender="@marge:home.local", body="dinner at seven",
        source={"content": {"body": "dinner at seven"}},
    )

    asyncio.run(channel._on_message(room, event))

    assert channel.processed == []


def test_an_invite_produces_a_greeting_turn(nanobot):
    """Joining in silence is the behaviour this replaces.

    Asserts the agent is actually driven — the room is joined *and* a
    turn is taken for it — because a shim that joined and then dropped
    the turn would look identical from the room's side to stock nanobot.
    """
    import asyncio
    import types as _types

    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()
    room = _types.SimpleNamespace(room_id="!r:simpson", display_name="Topic: Camping")
    channel.client.rooms = {"!r:simpson": room}
    event = _types.SimpleNamespace(sender="@homer:simpson")

    asyncio.run(channel._on_room_invite(room, event))

    assert channel.joined == ["!r:simpson"], "the original join must still happen"
    assert len(channel.handled) == 1, "the invite should drive exactly one turn"
    turn = channel.handled[0]
    assert turn["chat_id"] == "!r:simpson"
    # The briefing resolves the topic from this label, so a greeting that
    # loses it cannot mention what the room is about — the whole point.
    assert turn["metadata"]["room"] == "Topic: Camping"


# ── a voice message is addressed by what was said ────────────────────────

def _voice(event_id="$voice"):
    class _Event:
        sender = "@homer:simpson"
        body = "Voice message"
        pill_mention = False
        source = {"content": {"msgtype": "m.audio", "body": "Voice message"}}
    event = _Event()
    event.event_id = event_id
    return event


def _hand_over(channel, transcript, event_id="$voice"):
    import asyncio
    asyncio.run(channel._handle_message(
        sender_id="@homer:simpson", chat_id="!kitchen", media=["/tmp/voice.ogg"],
        content=f"Voice message\n[transcription: {transcript}]",
        metadata={"event_id": event_id, "room": "Küche"}))


def test_a_voice_message_that_names_the_agent_reaches_it(nanobot, monkeypatch):
    """In a shared room the agent hears what was said, not "Voice message"."""
    monkeypatch.setenv("AGENT_NAME", "Stacky")
    channel = nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()

    assert channel._should_process_message(None, _voice()), "let through to be transcribed"
    _hand_over(channel, "Stacky, wir brauchen Milch und Eier.")

    assert [h["content"] for h in channel.handled] == [
        "Voice message\n[transcription: Stacky, wir brauchen Milch und Eier.]"]


def test_the_answer_to_a_voice_message_starts_with_what_was_heard(nanobot, monkeypatch):
    """A word Whisper got wrong shows next to what the agent did with it."""
    import asyncio
    import dataclasses

    @dataclasses.dataclass
    class _Outbound:
        chat_id: str
        content: str
        metadata: dict
        event: object = None

    monkeypatch.setenv("AGENT_NAME", "Stacky")
    channel = nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()
    channel._should_process_message(None, _voice())
    _hand_over(channel, "Stacky please add bread to the shopping list.")

    asyncio.run(channel.send(_Outbound("!kitchen", "Brot steht jetzt auf der Einkaufsliste.",
                                       {"event_id": "$voice"})))
    asyncio.run(channel.send(_Outbound("!kitchen", "Sonst noch was?", {"event_id": "$voice"})))

    assert [m.content for m in channel.sent] == [
        "> Stacky please add bread to the shopping list.\n\n"
        "Brot steht jetzt auf der Einkaufsliste.",
        "Sonst noch was?"]


def test_a_tool_hint_before_the_answer_does_not_take_the_quote(nanobot, monkeypatch):
    import asyncio

    monkeypatch.setenv("AGENT_NAME", "Stacky")
    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()
    channel._should_process_message(None, _voice())
    _hand_over(channel, "Stacky, wir brauchen Milch.")

    asyncio.run(channel.send(_outbound("$ list_edit", event=_hint(mods, "list_edit"),
                                       metadata={"event_id": "$voice"})))
    asyncio.run(channel.send(_outbound("Milch steht jetzt auf der Einkaufsliste.",
                                       metadata={"event_id": "$voice"})))

    assert [m.content for m in channel.sent] == [
        "> Stacky, wir brauchen Milch.\n\nMilch steht jetzt auf der Einkaufsliste."]


def test_a_voice_message_for_someone_else_gets_no_answer(nanobot, monkeypatch):
    """The same rule as typed text: no name, no answer, and no typing left on."""
    monkeypatch.setenv("AGENT_NAME", "Stacky")
    channel = nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()

    channel._should_process_message(None, _voice())
    _hand_over(channel, "Marge, holst du Bart vom Training ab?")

    assert channel.handled == []
    assert channel.typing_stopped == ["!kitchen"]


def test_a_typed_message_is_not_held_for_a_transcript(nanobot, monkeypatch):
    """Only voice gets past the gate unaddressed; text still needs the name."""
    monkeypatch.setenv("AGENT_NAME", "Stacky")
    channel = nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()

    class _Text:
        sender = "@homer:simpson"
        body = "wir brauchen Milch"
        pill_mention = False
        source = {"content": {"msgtype": "m.text", "body": "wir brauchen Milch"}}

    assert not channel._should_process_message(None, _Text())


# ── the answer to the agent's own question ───────────────────────────────

def _said(text, sender="@homer:simpson"):
    class _Text:
        body = text
        pill_mention = False
        source = {"content": {"msgtype": "m.text", "body": text}}
    event = _Text()
    event.sender = sender
    return event


def _asks_homer(channel, *, event_id="$ask"):
    """Homer asks the agent something, and the agent answers with a question."""
    import asyncio
    asyncio.run(channel._handle_message(
        sender_id="@homer:simpson", chat_id="!kitchen",
        content="Stacky, lösch die Partyliste", metadata={"event_id": event_id}))
    asyncio.run(channel.send(_outbound(
        "Die Partyliste hat noch 2 offene Punkte. Sollen die auch weg?",
        metadata={"event_id": event_id})))


def test_the_answer_to_the_agents_question_needs_no_name(nanobot, monkeypatch):
    """"Sollen die auch weg?" "Ja, die können weg." reaches the agent; once."""
    import types as _types

    monkeypatch.setenv("AGENT_NAME", "Stacky")
    channel = nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()
    _asks_homer(channel)

    kitchen = _types.SimpleNamespace(room_id="!kitchen")
    assert channel._should_process_message(kitchen, _said("Ja, die können weg."))
    assert not channel._should_process_message(kitchen, _said("Ja, die können weg.")), \
        "only the first answer"


def test_a_notice_from_nanobot_leaves_the_question_open(nanobot, monkeypatch):
    """nanobot's notices carry text ("Compressing context…") but are not the agent speaking."""
    import asyncio
    import types as _types

    monkeypatch.setenv("AGENT_NAME", "Stacky")
    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()
    _asks_homer(channel)
    compaction = mods["nanobot.events"].ContextCompactionEvent
    asyncio.run(channel.send(_outbound("Context compacted.",
                                       event=compaction(phase="succeeded"))))

    kitchen = _types.SimpleNamespace(room_id="!kitchen")
    assert channel._should_process_message(kitchen, _said("Ja, die können weg."))


def test_a_spoken_answer_to_the_agents_question_needs_no_name(nanobot, monkeypatch):
    import types as _types

    monkeypatch.setenv("AGENT_NAME", "Stacky")
    channel = nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()
    _asks_homer(channel)

    kitchen = _types.SimpleNamespace(room_id="!kitchen")
    assert channel._should_process_message(kitchen, _voice())
    _hand_over(channel, "Ja, die können weg.")

    assert channel.handled[-1]["content"] == "Voice message\n[transcription: Ja, die können weg.]"


def test_the_question_is_for_the_person_whose_message_it_answers(nanobot, monkeypatch):
    """Bart writing while Homer's answer is on its way does not make it Bart's question."""
    import asyncio
    import types as _types

    monkeypatch.setenv("AGENT_NAME", "Stacky")
    channel = nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()
    asyncio.run(channel._handle_message(
        sender_id="@homer:simpson", chat_id="!kitchen",
        content="Stacky, lösch die Partyliste", metadata={"event_id": "$homer"}))
    asyncio.run(channel._handle_message(
        sender_id="@bart:simpson", chat_id="!kitchen",
        content="Stacky, was gibt es heute?", metadata={"event_id": "$bart"}))
    asyncio.run(channel.send(_outbound("Die Partyliste hat noch offene Punkte. Sollen die weg?",
                                       metadata={"event_id": "$homer"})))

    kitchen = _types.SimpleNamespace(room_id="!kitchen")
    assert not channel._should_process_message(kitchen, _said("Ja", "@bart:simpson"))
    assert channel._should_process_message(kitchen, _said("Ja"))


def test_a_notice_from_nanobot_does_not_take_the_quote(nanobot, monkeypatch):
    import asyncio

    monkeypatch.setenv("AGENT_NAME", "Stacky")
    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()
    channel._should_process_message(None, _voice())
    _hand_over(channel, "Stacky, wir brauchen Milch.")

    compaction = mods["nanobot.events"].ContextCompactionEvent
    asyncio.run(channel.send(_outbound("Compressing context…", event=compaction(),
                                       metadata={"event_id": "$voice"})))
    asyncio.run(channel.send(_outbound("Milch steht jetzt auf der Einkaufsliste.",
                                       metadata={"event_id": "$voice"})))

    assert channel.sent[-1].content == \
        "> Stacky, wir brauchen Milch.\n\nMilch steht jetzt auf der Einkaufsliste."


# ── a slow turn shows what the agent is doing ────────────────────────────

def _outbound(content, *, event=None, metadata=None):
    import dataclasses

    @dataclasses.dataclass
    class _Outbound:
        chat_id: str
        content: str
        metadata: dict
        event: object = None

    return _Outbound("!kitchen", content, metadata or {}, event)


def _hint(mods, *names):
    progress = mods["nanobot.bus.outbound_events"].ProgressEvent
    return progress(content="$ stack ...", tool_hint=True,
                    tool_events=[{"name": n} for n in names])


def _slow_turn(nanobot, monkeypatch, steps):
    """Run a turn whose status may change at once, step by step; return the channel."""
    import asyncio
    import sys as _sys

    mods = nanobot()
    monkeypatch.setattr(_sys.modules["status_trace"], "FIRST_AFTER", 0.0)
    monkeypatch.setattr(_sys.modules["status_trace"], "GAP", 0.0)
    monkeypatch.setattr(_sys.modules["sitecustomize"], "_TRACE_TICK", 0.01)
    monkeypatch.setenv("AGENT_LANGUAGE", "de")
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()

    async def _run():
        await channel._handle_message(sender_id="@homer:simpson", chat_id="!kitchen",
                                      content="Stacky, was fehlt noch?", metadata={})
        for step in steps(mods):
            await asyncio.sleep(0.05)
            await channel.send(step)
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    return channel


def test_a_slow_turn_shows_one_status_that_grows(nanobot, monkeypatch):
    channel = _slow_turn(nanobot, monkeypatch, lambda mods: [
        _outbound("$ memory_search", event=_hint(mods, "memory_search")),
        _outbound("$ list_edit", event=_hint(mods, "list_edit")),
        _outbound("Milch steht jetzt auf der Einkaufsliste."),
    ])

    first, *edits = [content for _, content in channel.room_contents]
    assert first["msgtype"] == "m.notice", "a notice: Matrix does not notify about it"
    assert all(e["m.relates_to"] == {"rel_type": "m.replace", "event_id": "$notice1"}
               for e in edits), "every change edits the one status"
    assert len(edits[-1]["m.new_content"]["body"].splitlines()) >= 2
    assert [m.content for m in channel.sent] == ["Milch steht jetzt auf der Einkaufsliste."], \
        "tool hints never reach the chat; the answer is a message of its own"


def test_compacting_shows_as_a_status_line_not_as_nanobots_notice(nanobot, monkeypatch):
    def steps(mods):
        compaction = mods["nanobot.events"].ContextCompactionEvent
        return [_outbound("Compressing context…", event=compaction(phase="started")),
                _outbound("Context compacted.", event=compaction(phase="succeeded")),
                _outbound("Erledigt.")]
    channel = _slow_turn(nanobot, monkeypatch, steps)

    shown = channel.room_contents[-1][1]
    body = (shown.get("m.new_content") or shown)["body"]
    assert "Notizen" in body
    assert [m.content for m in channel.sent] == ["Erledigt."]


def test_the_status_stops_with_the_answer(nanobot, monkeypatch):
    import asyncio

    channel = _slow_turn(nanobot, monkeypatch, lambda mods: [_outbound("Erledigt.")])
    shown = len(channel.room_contents)
    asyncio.run(asyncio.sleep(0.05))
    assert len(channel.room_contents) == shown


def test_a_status_whose_answer_never_comes_stops_by_itself(nanobot, monkeypatch):
    """A cancelled turn sends nothing; its status stops and lets go of the room."""
    import asyncio
    import sys as _sys

    mods = nanobot()
    trace, shim = _sys.modules["status_trace"], _sys.modules["sitecustomize"]
    monkeypatch.setattr(trace, "FIRST_AFTER", 0.0)
    monkeypatch.setattr(trace, "GIVE_UP_AFTER", 0.1)
    monkeypatch.setattr(shim, "_TRACE_TICK", 0.01)
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()

    async def _run():
        await channel._handle_message(sender_id="@homer:simpson", chat_id="!kitchen",
                                      content="Stacky, was fehlt noch?", metadata={})
        await asyncio.sleep(0.2)
        return dict(shim._traces)

    assert asyncio.run(_run()) == {}
    assert len(channel.room_contents) == 1, "the status it showed stays, nothing more"


def test_a_message_that_fails_to_reach_the_agent_starts_no_status(nanobot, monkeypatch):
    import asyncio
    import sys as _sys

    mods = nanobot()
    channel = mods["nanobot.channels.matrix.runtime"].MatrixChannel()

    async def _broken(*_args, **_kwargs):
        raise RuntimeError("bus closed")
    monkeypatch.setattr(_sys.modules["sitecustomize"], "_orig_handle_trace", _broken)
    with pytest.raises(RuntimeError):
        asyncio.run(channel._handle_message(sender_id="@homer:simpson", chat_id="!kitchen",
                                            content="Stacky, was fehlt noch?", metadata={}))
    assert _sys.modules["sitecustomize"]._traces == {}


# ── failure is contained, and visible ────────────────────────────────────

def test_a_moved_symbol_does_not_take_the_others_down(nanobot):
    """One missing nanobot symbol must cost only its own tool.

    This is why each install runs in its own try. Sharing one block would
    mean a moved agent loop silently removed memory_search too, and
    the agent would lose vault access over an unrelated upgrade.
    """
    mods = nanobot(drop="nanobot.agent.loop.AgentLoop")

    assert _discovered(mods) == {"MemorySearchTool", "MemoryPersonTool",
                                 "MemoryHistoryTool", "ListEditTool", "CalendarTool"}


def test_the_stub_can_actually_express_a_detached_shim(nanobot):
    """Guards the guard: prove these assertions can fail.

    A test suite that cannot distinguish attached from detached would pass
    against the very bug this file exists to catch, which is the state the
    codebase was in before it was written.
    """
    mods = nanobot(drop="nanobot.agent.tools.loader.ToolLoader")

    tools = mods["nanobot.agent.tools.loader"]
    assert not hasattr(tools, "ToolLoader"), "the drop hook must really remove it"

    # The briefing does not depend on the loader and still attaches.
    assert mods["nanobot.agent.loop"].AgentLoop.__init__.__name__ == "_loop_init", (
        "the briefing is independent of the loader and should still attach"
    )


# ── writing to a vault page ──────────────────────────────────────────────

def _write_tools(mods):
    fs = mods["nanobot.agent.tools.filesystem"]
    return (fs.WriteFileTool, fs.EditFileTool,
            mods["nanobot.agent.tools.apply_patch"].ApplyPatchTool)


def test_every_way_to_change_a_file_is_routed(nanobot):
    """All three write tools, or the model finds the unguarded one.

    nanobot offers three: `write_file`, `edit_file`, and `apply_patch` —
    which it advertises as the *default* editor for edits. Shimming only
    the first leaves the default aimed straight at a read-only mount, and
    the model has no reason to prefer the one door that works.
    """
    for tool in _write_tools(nanobot()):
        assert tool.execute.__qualname__.startswith("install."), (
            f"{tool.__name__} is unshimmed; a page edit through it bypasses "
            f"the memory store"
        )


def test_the_shims_are_awaitable_like_the_tools_they_replace(nanobot):
    """nanobot awaits `execute`, so a sync replacement is a broken tool.

    Pinned as its own case because the failure is invisible from the
    attachment check above: the shim is installed, the log is clean, and
    every vault write dies in the tool loop on `await` receiving a `str`.
    """
    import inspect

    for tool in _write_tools(nanobot()):
        assert inspect.iscoroutinefunction(tool.execute), (
            f"{tool.__name__}.execute must stay `async def`"
        )


def test_a_vault_page_goes_to_the_memory_store_not_the_mount(nanobot, monkeypatch):
    """The point of the shim: the write leaves via `stack memory write`.

    The vault is mounted read-only, so a write that reaches the filesystem
    is a write that did not happen. Asserted through the tool's own
    `execute`, which is the only surface the model can reach.
    """
    import asyncio

    mods = nanobot()
    import vault_write

    seen = {}

    def _fake_write(page, content):
        seen["page"], seen["content"] = page, content
        return "ticked off 1: Kühlbox"

    monkeypatch.setattr(vault_write, "write_page", _fake_write)

    write_file = mods["nanobot.agent.tools.filesystem"].WriteFileTool()
    answer = asyncio.run(write_file.execute(
        path="vault/family/camping/todos.md", content="- [x] Kühlbox\n"))

    assert seen["page"] == "family/camping/todos.md"
    assert seen["content"] == "- [x] Kühlbox\n"
    # Verbatim, because what the store says it did is what the model reports
    # to the family. Flattening it to "ok" is how a silent loss gets told
    # as a success.
    assert answer == "ticked off 1: Kühlbox"


def test_a_patch_reaches_the_store_with_its_edits_intact(nanobot, monkeypatch):
    """The edits go to the store, not to the read-only mount.

    Sending them on rather than applying them here is the point: the store
    matches `old_text` against the page as it currently stands, so an edit
    written against a copy somebody else has since changed is refused by
    name instead of quietly reverting them.
    """
    import asyncio

    mods = nanobot()
    import vault_write

    seen = {}

    def _fake_patch(page, edits, *, dry_run=False):
        seen["page"], seen["edits"], seen["dry_run"] = page, edits, dry_run
        return "ticked off 1: Wetter checken"

    monkeypatch.setattr(vault_write, "patch_page", _fake_patch)

    _, _, apply_patch = _write_tools(mods)
    edit = {"path": "vault/family/camping/todos.md", "action": "replace",
            "old_text": "- [ ] Wetter checken", "new_text": "- [x] Wetter checken"}
    answer = asyncio.run(apply_patch().execute(edits=[edit]))

    assert seen["page"] == "family/camping/todos.md"
    assert seen["edits"] == [edit], "the edits must arrive unaltered"
    assert seen["dry_run"] is False
    assert answer == "ticked off 1: Wetter checken"


def test_a_preview_stays_a_preview(nanobot, monkeypatch):
    """`dry_run` has to survive the trip, or a preview silently commits.

    The model is told it can validate without writing. Dropping the flag
    on the way to the store turns "show me what this would do" into a
    change to the family's list.
    """
    import asyncio

    mods = nanobot()
    import vault_write

    seen = {}
    monkeypatch.setattr(vault_write, "patch_page",
                        lambda page, edits, *, dry_run=False:
                        seen.update(dry_run=dry_run) or "would tick off 1")

    _, _, apply_patch = _write_tools(mods)
    asyncio.run(apply_patch().execute(dry_run=True, edits=[
        {"path": "vault/family/camping/todos.md", "action": "replace",
         "old_text": "a", "new_text": "b"}]))

    assert seen["dry_run"] is True


def test_one_patch_may_touch_a_page_and_an_ordinary_file(nanobot, monkeypatch):
    """Mixed edits are normal, and neither half may be dropped.

    A patch that silently ignored its non-vault edits (or its vault ones)
    would report success for work it never did.
    """
    import asyncio

    mods = nanobot()
    import vault_write

    monkeypatch.setattr(vault_write, "patch_page",
                        lambda page, edits, *, dry_run=False: f"stored {page}")

    _, _, apply_patch = _write_tools(mods)
    answer = asyncio.run(apply_patch().execute(edits=[
        {"path": "vault/family/camping/todos.md", "action": "add", "new_text": "x"},
        {"path": "memory/notes.md", "action": "add", "new_text": "y"},
    ]))

    assert "stored family/camping/todos.md" in answer, "the page must reach the store"

    # The stub echoes the edits it was handed, so its line says what the
    # filesystem was asked to do — which must be the ordinary file and
    # nothing else. Writing a page through both paths would double-apply it.
    stock = next(line for line in answer.splitlines() if line.startswith("stock patch"))
    assert "memory/notes.md" in stock
    assert "camping" not in stock


def test_an_edit_file_is_pointed_at_the_two_that_work(nanobot):
    """`edit_file` is the redundant third spelling, so it declines.

    A bare refusal would just make the model try the next tool, so it
    names what to use instead.
    """
    import asyncio

    _, edit_file, _ = _write_tools(nanobot())
    answer = asyncio.run(edit_file().execute(path="vault/family/camping/todos.md"))

    assert "write_file" in answer
    assert "family/camping/todos.md" in answer


def test_files_outside_the_vault_keep_stock_behaviour(nanobot):
    """The agent's own workspace notes are not the family's memory.

    A shim that swallowed every write would break scratch files and cron
    scripts, so the routing has to be narrow and this pins that it is.
    """
    import asyncio

    mods = nanobot()
    write_file, edit_file, apply_patch = _write_tools(mods)

    assert asyncio.run(write_file().execute(
        path="memory/notes.md", content="x")) == "stock write memory/notes.md"
    assert asyncio.run(edit_file().execute(
        path="memory/notes.md")) == "stock edit memory/notes.md"
    assert "stock patch" in asyncio.run(apply_patch().execute(
        edits=[{"path": "memory/notes.md", "action": "replace"}]))
