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
                   "memory_tool", "person_tool", "history_tool", "grep_tool",
                   "name_trigger", "thread_trigger", "join_greeting", "vault_write")


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
    assert _discovered(mods) == {"MemorySearchTool", "MemoryPersonTool",
                                 "MemoryHistoryTool"}


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
    channel = mods["nanobot.channels.matrix"].MatrixChannel()
    room = _room_with_thread(channel, root_sender=channel.config.user_id)
    event = _threaded("$stacky-answer")

    asyncio.run(channel._on_message(room, event))

    assert channel.processed == [event]


def test_another_bots_thread_is_left_alone(nanobot):
    """The shim widens the gate; it must not open it.

    The archivist answers a filing under the uploaded document, in the
    same family room. Every reply there would reach the agent too if
    "in a thread" were the rule, and the family would get two bots
    talking over one receipt.
    """
    import asyncio

    mods = nanobot()
    channel = mods["nanobot.channels.matrix"].MatrixChannel()
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
    channel = mods["nanobot.channels.matrix"].MatrixChannel()
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

    assert _discovered(mods) == {"MemorySearchTool", "MemoryPersonTool",
                                 "MemoryHistoryTool"}


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
