"""Runtime shims: keep nanobot's per-turn context correct and lean, no fork.

Python auto-imports `sitecustomize` at interpreter startup for any module on
`sys.path`, so placing this on `PYTHONPATH` (see the Dockerfile) patches every
`nanobot` invocation in the container — the gateway and one-shot `nanobot agent`
alike — with no fork.

Two kinds of patch live here, each a thin monkeypatch over a pure module.

First, two context shims that reshape what the model sees per turn:

1. brief (brief.py) — prepends a per-turn family briefing (who is speaking, the
   topic) to nanobot's runtime lines. Injected late (after the stable prompt and
   the user's text) so it stays KV-cache-friendly: measured 4096/4746 prompt
   tokens cached with late injection vs 0 injecting the same content early via
   USER.md.

2. lean_state (lean_state.py) — replaces previous-turn tool results with the
   call that produced them (`name(args)`), so the agent re-fetches instead of
   reciting stale data. The transcript (Matrix) keeps the full result; the state
   we feed the model keeps only a cheap pointer.

Second, three vault tools, which add capability rather than reshaping context:

3. memory_tool (memory_tool.py) — a `memory_search` tool over `stack memory search`.
4. person_tool (person_tool.py) — a `memory_person` tool for exact profile reads.
5. grep_tool (grep_tool.py) — routes greps under `vault/` into memory_search, so
   the agent gets semantic hits instead of literal matches on a corpus where the
   words it greps for are rarely the words on disk.

Third, one shim that widens when the agent is allowed to answer at all:

6. name_trigger (name_trigger.py) — a group-room message that addresses the
   agent by its configured name counts as a mention, not just an autocompleted
   pill. Families type "Stacky, what's on our list?".

7. join_greeting (join_greeting.py) — on being invited, take one turn and
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
    lean_state:  `nanobot.agent.context.ContextBuilder.build_messages(...) -> list[dict]`
    memory_tool: `nanobot.agent.tools.loader.ToolLoader.discover(self) -> list[type[Tool]]`
                 `nanobot.agent.tools.base.Tool`, `nanobot.agent.tools.base.tool_parameters`
                 `nanobot.agent.tools.schema.{StringSchema, IntegerSchema, tool_parameters_schema}`
    person_tool: same symbols as memory_tool
    grep_tool:   `nanobot.agent.tools.search.GrepTool.execute(...) -> str`
    name_trigger: `nanobot.channels.matrix.MatrixChannel._is_bot_mentioned(self, event) -> bool`
    join_greeting: `nanobot.channels.matrix.MatrixChannel._on_room_invite(self, room, event)`
                 `MatrixChannel._handle_message(sender_id, chat_id, content, metadata, is_dm)`

    `tests/stacklets/test_agent_runtime_shims.py` asserts every one of these is
    attached against a stub nanobot, so this list is executable rather than
    aspirational: a moved symbol fails the unit lane instead of silently
    reaching production as a logged warning nobody reads.
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


# ── lean_state: previous-turn tool results -> a pointer naming the call ──────
# Also the single place to see the *state* (what the model receives) next to the
# *transcript* (the Matrix room): every turn logs the leaned message list, one
# line per message, greppable by "[llm-state]" in `docker logs stack-agent`.
try:
    import datetime as _dt
    import os as _os

    import nanobot.agent.context as _ctx_ls
    from lean_state import format_state_for_log as _format_state
    from lean_state import lean_messages as _lean_messages

    _orig_build_messages = _ctx_ls.ContextBuilder.build_messages
    # Bind-mounted home (~/.nanobot -> famstack-data/agent), so this file is
    # readable on the host for analysis, one appended block per turn.
    _STATE_LOG = _os.path.expanduser("~/.nanobot/llm-state.log")

    def _build_messages_lean(self, *args, **kwargs):
        # Post-process the assembled message list: stale prior-turn derived data
        # (tool results and tool-synthesized answers) become pointers; the
        # current turn stays intact.
        messages = _lean_messages(_orig_build_messages(self, *args, **kwargs))
        try:  # a debug view; never worth breaking a turn over
            stamp = _dt.datetime.now().isoformat(timespec="seconds")
            with open(_STATE_LOG, "a", encoding="utf-8") as fh:
                fh.write(f"\n===== {stamp}  {len(messages)} messages =====\n"
                         + _format_state(messages) + "\n")
            print(f"[llm-state] {len(messages)} msgs -> llm-state.log", flush=True)
        except Exception:
            pass
        return messages

    _ctx_ls.ContextBuilder.build_messages = _build_messages_lean
    _log.info("lean-state message shim active")
except Exception:
    _log.exception("lean-state shim could not attach (nanobot internals changed?)")


# ── vault tools: memory_search, memory_person, and grep routed through them ──
# These add capability rather than reshaping context, but attach the same way.
# Each is installed in its own try so one tool failing costs only itself; a
# single shared block would let a moved GrepTool symbol take memory_search down
# with it. memory_tool goes first because grep_tool routes into it.
for _module_name, _what in (
    ("memory_tool", "memory_search tool"),
    ("person_tool", "memory_person tool"),
    ("grep_tool", "vault grep -> memory_search routing"),
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

    # Same workspace nanobot mounts the projection into; `lean_state`
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
