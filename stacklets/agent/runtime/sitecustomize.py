"""Runtime shims: keep nanobot's per-turn context correct and lean, no fork.

Python auto-imports `sitecustomize` at interpreter startup for any module on
`sys.path`, so placing this on `PYTHONPATH` (see the Dockerfile) patches every
`nanobot` invocation in the container — the gateway and one-shot `nanobot agent`
alike — with no fork.

Two kinds of patch live here, each a thin monkeypatch over a pure module.

First, the context shims. History length and old tool results are left to
nanobot's own mechanisms (replay window, idle autocompact, microcompact, token
consolidation); see config.json and compact_tools.py.

1. brief (brief.py) — prepends a per-turn family briefing (who is speaking, the
   topic) to nanobot's runtime lines. Injected late (after the stable prompt and
   the user's text) so it stays KV-cache-friendly: measured 4096/4746 prompt
   tokens cached with late injection vs 0 injecting the same content early via
   USER.md.

2. state_log (state_log.py) — with AGENT_STATE_LOG=1, writes the message list
   of each turn to ~/.nanobot/llm-state.log. Off by default. It does not change
   the message list.

Second, the vault tools, which add capability rather than reshaping context.
They are *tools* and not lines in a skill because that is the difference
between a capability the model chooses and one it has to remember: told in
prose to run `stack memory history`, it called `memory_search` four times
instead and never ran it once.

3. memory_tool (memory_tool.py) — a `memory_search` tool over `stack memory search`.
4. person_tool (person_tool.py) — a `memory_person` tool for exact profile reads.
5. history_tool (history_tool.py) — a `memory_history` tool for questions with
   time in them. Search ranks pages by what they say now, so "lately", "since
   when" and "who changed this" are unanswerable by it, silently.
6. compact_tools (compact_tools.py) — adds the vault read tools to nanobot's
   microcompact set, so their old results are shortened like nanobot's own.

Third, one shim that widens when the agent is allowed to answer at all:

7. name_trigger (name_trigger.py) — a group-room message that addresses the
   agent by its configured name counts as a mention, not just an autocompleted
   pill. Families type "Stacky, what's on our list?".

8. thread_trigger (thread_trigger.py) — a message inside a thread the agent is
   part of counts as a mention too. A thread is a conversation; nobody repeats
   the name on every line of one. Scoped to threads the agent participates in,
   because the archivist and mail bot thread in the same rooms.

9. join_greeting (join_greeting.py) — on being invited, take one turn and
   introduce the room's topic instead of joining in silence.

WHY SHIMS AND NOT A FORK
    nanobot has no plugin seam for per-turn context injection or state shaping.
    Shims keep us on upstream `nanobot-ai` (updates included) with the change
    contained in this stacklet. Tradeoff: they patch internals, so a nanobot
    refactor breaks them — loudly, since we log on failure. When these grow past
    a couple of hooks, fold the pure modules into a fork (each is already a clean
    function) and upstream real context-provider / state-shaping APIs.

TO REMOVE
    Delete this stacklet's `runtime/` dir and drop `PYTHONPATH` from the
    Dockerfile. nanobot reverts to stock behaviour with no other change.

PIN / RECHECK ON UPGRADE (re-verify after any `nanobot-ai` version bump)
    brief:       `nanobot.agent.context.runtime_lines(state, msg, workspace, *, skip=False) -> list[str]`
    state_log:   `nanobot.agent.context.ContextBuilder.build_messages(...) -> list[dict]`
    memory_tool: `nanobot.agent.tools.loader.ToolLoader.discover(self) -> list[type[Tool]]`
                 `nanobot.agent.tools.base.Tool`, `nanobot.agent.tools.base.tool_parameters`
                 `nanobot.agent.tools.schema.{StringSchema, IntegerSchema, tool_parameters_schema}`
    person_tool: same symbols as memory_tool
    history_tool: same symbols as memory_tool
    compact_tools: `nanobot.agent.runner._COMPACTABLE_TOOLS` (frozenset of tool names)
    vault_write: `nanobot.agent.tools.filesystem.WriteFileTool.execute(self, path, content) -> str`
                 `nanobot.agent.tools.filesystem.EditFileTool.execute(self, path, ...) -> str`
                 `nanobot.agent.tools.apply_patch.ApplyPatchTool.execute(self, edits, ...) -> str`
                 (all three are `async def`; a sync replacement returns a str
                 into the loop's `await` and the tool call dies with TypeError)
    name_trigger: `nanobot.channels.matrix.MatrixChannel._is_bot_mentioned(self, event) -> bool`
    thread_trigger: `MatrixChannel._is_bot_mentioned` (same symbol, wrapped after it)
                 `MatrixChannel._on_message(self, room, event)` (async)
                 `MatrixChannel._on_media_message(self, room, event)` (async)
                 `MatrixChannel.client` (nio AsyncClient), `MatrixChannel.config.user_id`
                 — and that `groupPolicy: mention` still routes through
                 `_is_bot_mentioned` (config.json sets that policy)
    join_greeting: `nanobot.channels.matrix.MatrixChannel._on_room_invite(self, room, event)`
                 `MatrixChannel._handle_message(sender_id, chat_id, content, metadata, is_dm)`

    `tests/unit/stacklets/test_agent_runtime_shims.py` asserts every one of these is
    attached — but against a *stub* nanobot this repo hand-writes, so read what
    that does and does not buy. It catches our own mistakes: a shim that stops
    attaching, or one whose failure takes another down with it. It cannot catch
    upstream moving a symbol, because the stub still has the old one and the
    lane stays green while production is broken. The version pin in the
    Dockerfile is what actually holds this line; the tests guard the shims, the
    pin guards the assumption underneath them.

    So when bumping `nanobot-ai`, re-verify this list against the installed
    package, not against a passing unit lane.
"""

import importlib
import logging

_log = logging.getLogger("agent.runtime.shim")

try:
    import nanobot.agent.context as _ctx
    from brief import brief_lines as _brief_lines

    _orig_runtime_lines = _ctx.runtime_lines

    def _runtime_lines(state, msg, workspace, *, skip=False):
        base = _orig_runtime_lines(state, msg, workspace, skip=skip)
        if skip:  # subagents skip runtime context; respect that
            return base
        try:
            extra = _brief_lines(msg, workspace)
        except Exception:
            # A briefing is an optimisation, never a dependency — never break a turn.
            _log.exception("brief failed; continuing without a briefing")
            extra = []
        return list(extra) + list(base)

    _ctx.runtime_lines = _runtime_lines
    _log.info("brief runtime-context shim active")
except Exception:
    # If the internal symbol moved (nanobot upgrade), fail loudly in the log but
    # do not stop the agent from starting.
    _log.exception("brief shim could not attach (nanobot internals changed?)")


# ── state_log: the message list per turn, for debugging (opt-in) ─────────────
# AGENT_STATE_LOG=1 writes what nanobot built for each call to llm-state.log,
# one line per message, to compare against the Matrix chat. Off by default:
# nanobot also calls build_messages to estimate tokens, so the file grows by
# several blocks per turn.
try:
    import os as _os

    if _os.environ.get("AGENT_STATE_LOG", "0") == "1":
        import datetime as _dt

        import nanobot.agent.context as _ctx_ls
        from state_log import format_state_for_log as _format_state

        _orig_build_messages = _ctx_ls.ContextBuilder.build_messages
        # Bind-mounted home (~/.nanobot -> famstack-data/agent), so this file
        # is readable on the host for analysis.
        _STATE_LOG = _os.path.expanduser("~/.nanobot/llm-state.log")

        def _build_messages_logged(self, *args, **kwargs):
            messages = _orig_build_messages(self, *args, **kwargs)
            try:  # a debug view; never worth breaking a turn over
                stamp = _dt.datetime.now().isoformat(timespec="seconds")
                with open(_STATE_LOG, "a", encoding="utf-8") as fh:
                    fh.write(f"\n===== {stamp}  {len(messages)} messages =====\n"
                             + _format_state(messages) + "\n")
            except Exception:
                pass
            return messages

        _ctx_ls.ContextBuilder.build_messages = _build_messages_logged
        _log.info("state-log shim active")
except Exception:
    _log.exception("state-log shim could not attach (nanobot internals changed?)")


# ── vault tools: memory_search, memory_person, memory_history, writes ────────
# These add capability rather than reshaping context, but attach the same way.
# Each is installed in its own try so one tool failing costs only itself; a
# single shared block would let one moved nanobot symbol take the others down.
for _module_name, _what in (
    ("memory_tool", "memory_search tool"),
    ("person_tool", "memory_person tool"),
    ("history_tool", "memory_history tool"),
    ("vault_write", "write_file on a vault page -> stack memory write"),
    ("list_tool", "list_edit item tool -> stack memory list-edit"),
    ("tool_trim", "unused-tool trim (AGENT_TOOL_TRIM=0 to disable)"),
    ("compact_tools", "vault tools in nanobot's microcompact set"),
    ("thread_session", "thread-scoped sessions (AGENT_THREAD_SESSIONS=0 to disable)"),
):
    try:
        importlib.import_module(_module_name).install()
        _log.info("%s active", _what)
    except Exception:
        _log.exception("%s could not attach (nanobot internals changed?)", _what)


# ── name_trigger: being spoken to by name counts as a mention ────────────────
# Widens nanobot's group-room gate rather than replacing it: a real pill mention
# still wins on the original code path, and this only gets a say when that said
# no. `AGENT_NAME` is read per call, so renaming the agent takes effect on the
# next restart with no rebuild.
try:
    import os as _os

    import nanobot.channels.matrix as _matrix
    from name_trigger import addressed_by_name as _addressed_by_name

    _orig_is_bot_mentioned = _matrix.MatrixChannel._is_bot_mentioned

    def _is_bot_mentioned(self, event):
        if _orig_is_bot_mentioned(self, event):
            return True
        try:
            return _addressed_by_name(
                getattr(event, "body", "") or "", _os.environ.get("AGENT_NAME", ""),
            )
        except Exception:
            # Never let a matching bug make the agent unreachable: fall back
            # to stock behaviour, which is pill mentions only.
            _log.exception("name trigger failed; pill mentions still work")
            return False

    _matrix.MatrixChannel._is_bot_mentioned = _is_bot_mentioned
    _log.info("name-trigger mention shim active")
except Exception:
    _log.exception("name-trigger shim could not attach (nanobot internals changed?)")


# ── thread_trigger: a reply inside the agent's own thread is addressed to it ──
# The threaded half of the same gate, in two parts because nanobot's gate is
# synchronous and the question is not: `_on_message` (async) settles whether the
# thread is ours and remembers it, `_is_bot_mentioned` (sync) reads that answer.
# Wraps whatever `_is_bot_mentioned` is by now, so pill mentions and the name
# matcher keep working and this only gets a say when both said no — and so a
# name-trigger that failed to attach costs only itself.
try:
    import nanobot.channels.matrix as _matrix_thread
    from thread_trigger import AgentThreads as _AgentThreads
    from thread_trigger import thread_root as _thread_root

    _agent_threads = _AgentThreads()

    def _learn_threads(orig):
        """Settle the thread question before nanobot's gate asks it."""
        async def _wrapped(self, room, event):
            root = _thread_root(event)
            if root and not _agent_threads.includes(root):
                # Swallows its own failures; a homeserver hiccup must not
                # stop the message being routed by the other rules.
                await _agent_threads.observe(
                    self.client, room.room_id, root, self.config.user_id,
                )
            return await orig(self, room, event)
        return _wrapped

    _orig_mentioned_pre_thread = _matrix_thread.MatrixChannel._is_bot_mentioned

    def _is_bot_mentioned_or_our_thread(self, event):
        if _orig_mentioned_pre_thread(self, event):
            return True
        try:
            root = _thread_root(event)
            return bool(root and _agent_threads.includes(root))
        except Exception:
            _log.exception("thread trigger failed; addressing by name still works")
            return False

    _matrix_thread.MatrixChannel._is_bot_mentioned = _is_bot_mentioned_or_our_thread
    _matrix_thread.MatrixChannel._on_message = _learn_threads(
        _matrix_thread.MatrixChannel._on_message,
    )
    _matrix_thread.MatrixChannel._on_media_message = _learn_threads(
        _matrix_thread.MatrixChannel._on_media_message,
    )
    _log.info("thread-trigger mention shim active")
except Exception:
    _log.exception("thread-trigger shim could not attach (nanobot internals changed?)")


# ── join_greeting: say something useful the moment you are invited ───────────
# Stock nanobot joins an invite silently. In a topic room that silence is the
# family's first impression of the agent, so it takes one ordinary turn instead
# (see join_greeting.py for why generated rather than canned).
try:
    import asyncio as _asyncio
    import os.path as _ospath
    from pathlib import Path as _Path

    import nanobot.channels.matrix as _matrix_join
    from brief import topic_for_room_label as _topic_for_room_label
    from join_greeting import greeting_prompt as _greeting_prompt

    # Same workspace nanobot mounts the projection into; `state_log`
    # above resolves its log the same way.
    _WORKSPACE = _Path(_ospath.expanduser("~/.nanobot/workspace"))

    _orig_on_room_invite = _matrix_join.MatrixChannel._on_room_invite

    async def _on_room_invite(self, room, event):
        await _orig_on_room_invite(self, room, event)
        try:
            # The room's name arrives with the state sync that follows the
            # join, not with the invite. Greeting before it lands would cost
            # the briefing its topic line — the whole point of greeting at
            # all — so wait briefly for a display name to appear.
            label = ""
            for _ in range(10):
                joined = (getattr(self.client, "rooms", {}) or {}).get(room.room_id)
                label = getattr(joined, "display_name", "") or ""
                if label and label != room.room_id:
                    break
                await _asyncio.sleep(1)

            topic = _topic_for_room_label(label, _WORKSPACE / "vault")
            await self._handle_message(
                sender_id=event.sender,
                chat_id=room.room_id,
                content=_greeting_prompt(topic),
                metadata={"room": label or getattr(room, "room_id", "")},
                is_dm=False,
            )
        except Exception:
            # A missing greeting is a disappointment; a raised exception in
            # the invite callback would leave the bot joined and deaf.
            _log.exception("join greeting failed; the room is still joined")

    _matrix_join.MatrixChannel._on_room_invite = _on_room_invite
    _log.info("join-greeting shim active")
except Exception:
    _log.exception("join-greeting shim could not attach (nanobot internals changed?)")
