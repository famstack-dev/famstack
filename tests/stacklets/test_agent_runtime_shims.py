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

SHIMMED_MODULES = ("sitecustomize", "brief", "lean_state",
                   "memory_tool", "person_tool", "grep_tool", "name_trigger",
                   "join_greeting")


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
    assert _discovered(mods) == {"MemorySearchTool", "MemoryPersonTool"}


def test_vault_greps_are_routed_through_memory_search(nanobot):
    """A grep under `vault/` must no longer hit the stock literal matcher.

    The vault is prose. Literal grep over it answers almost nothing, which
    is why this routing exists.
    """
    mods = nanobot()
    grep = mods["nanobot.agent.tools.search"].GrepTool
    assert grep.execute.__name__ == "execute_with_memory"


def test_context_shims_are_attached(nanobot):
    """The two older shims, pinned the same way as the new tools."""
    mods = nanobot()
    ctx = mods["nanobot.agent.context"]
    assert ctx.runtime_lines.__name__ == "_runtime_lines"
    assert ctx.ContextBuilder.build_messages.__name__ == "_build_messages_lean"


def test_being_named_counts_as_a_mention(nanobot, monkeypatch):
    """Without this shim the agent ignores everyone who does not use a pill.

    Driven through nanobot's own gate rather than the matcher directly:
    the matcher is specified in `test_agent_name_trigger.py`, and what
    is at stake here is that nanobot actually asks it.
    """
    monkeypatch.setenv("AGENT_NAME", "Stacky")
    mods = nanobot()
    channel = mods["nanobot.channels.matrix"].MatrixChannel()

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
    channel = mods["nanobot.channels.matrix"].MatrixChannel()

    class _Event:
        pill_mention = True
        body = "what's on our list?"

    assert channel._is_bot_mentioned(_Event())


def test_an_invite_produces_a_greeting_turn(nanobot):
    """Joining in silence is the behaviour this replaces.

    Asserts the agent is actually driven — the room is joined *and* a
    turn is taken for it — because a shim that joined and then dropped
    the turn would look identical from the room's side to stock nanobot.
    """
    import asyncio
    import types as _types

    mods = nanobot()
    channel = mods["nanobot.channels.matrix"].MatrixChannel()
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


# ── failure is contained, and visible ────────────────────────────────────

def test_a_moved_symbol_does_not_take_the_others_down(nanobot):
    """One missing nanobot symbol must cost only its own tool.

    This is why each install runs in its own try. Sharing one block would
    mean a renamed GrepTool silently removed memory_search too, and the
    agent would lose vault access over an unrelated upgrade.
    """
    mods = nanobot(drop="nanobot.agent.tools.search.GrepTool")

    assert _discovered(mods) == {"MemorySearchTool", "MemoryPersonTool"}


def test_the_stub_can_actually_express_a_detached_shim(nanobot):
    """Guards the guard: prove these assertions can fail.

    A test suite that cannot distinguish attached from detached would pass
    against the very bug this file exists to catch, which is the state the
    codebase was in before it was written.
    """
    mods = nanobot(drop="nanobot.agent.tools.loader.ToolLoader")

    tools = mods["nanobot.agent.tools.loader"]
    assert not hasattr(tools, "ToolLoader"), "the drop hook must really remove it"

    # Nothing to append to, so neither tool can have registered anywhere.
    grep = mods["nanobot.agent.tools.search"].GrepTool
    assert grep.execute.__name__ == "execute_with_memory", (
        "grep routing is independent of the loader and should still attach"
    )
