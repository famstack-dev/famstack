"""Runtime shims: keep nanobot's per-turn context correct and lean, no fork.

Python auto-imports `sitecustomize` at interpreter startup for any module on
`sys.path`, so placing this on `PYTHONPATH` (see the Dockerfile) patches every
`nanobot` invocation in the container — the gateway and one-shot `nanobot agent`
alike — with no fork.

Two kinds of patch live here, each a thin monkeypatch over a pure module.

First, the context. History length and old tool results are left to
nanobot's own context compaction; see config.json.

1. brief (brief.py) — a per-turn family briefing (who is speaking, the topic),
   registered as one of nanobot's runtime-context providers. nanobot appends it
   to the user's message and keeps it with that message in the history, so the
   prompt prefix stays the same from one turn to the next: measured 4096/4746
   prompt tokens cached with late injection vs 0 injecting the same content
   early via USER.md.

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
Third, one shim that widens when the agent is allowed to answer at all:

6. name_trigger (name_trigger.py) — a group-room message that addresses the
   agent by its configured name counts as a mention, not just an autocompleted
   pill. Families type "Stacky, what's on our list?".

7. thread_trigger (thread_trigger.py) — a message inside a thread the agent is
   part of counts as a mention too. A thread is a conversation; nobody repeats
   the name on every line of one. Scoped to threads the agent participates in,
   because the archivist and mail bot thread in the same rooms. A plain reply
   to one of the agent's own messages counts too.

8. join_greeting (join_greeting.py) — on being invited, take one turn and
   introduce the room's topic instead of joining in silence.

9. voice_trigger (voice_trigger.py) — a voice message in a shared room goes
   through nanobot's gate, is transcribed, and reaches the agent only if the
   transcript addresses it by name, like typed text. The answer starts with
   what was heard, quoted.

10. open_question (open_question.py) — while the agent's last message in a room
   is a question, the asked person's next message there reaches it without
   its name.

11. status_trace (status_trace.py) — a slow turn gets one status notice that
   grows a line as the agent moves on (looking, looking closer, writing it
   down); the answer comes as its own message, the notice stays as a trace.

WHY SHIMS AND NOT A FORK
    nanobot 0.3 has a seam for per-turn context (runtime-context providers) and
    keeps a session per Matrix thread, which retired the two shims that needed
    one most. What still patches internals is the Matrix addressing gate and the
    write tools. See docs/adr/adr-012-nanobot-fork.md, update 2026-10-12.

TO REMOVE
    Delete this stacklet's `runtime/` dir and drop `PYTHONPATH` from the
    Dockerfile. nanobot reverts to stock behaviour with no other change.

PIN / RECHECK ON UPGRADE (re-verify after any `nanobot-ai` version bump)
    brief:       `nanobot.agent.loop.AgentLoop.__init__` (wrapped to register a provider),
                 `AgentLoop.register_runtime_context_provider(provider)`, where a provider is
                 `async (RequestContext) -> RuntimeContextBlock | None` and RequestContext
                 carries `sender_id`, `metadata`, `workspace`;
                 `nanobot.runtime_context.{RuntimeContextBlock, wrap_runtime_context_lines}`
    state_log:   `nanobot.agent.context.ContextBuilder.build_messages(...) -> list[dict]`
    memory_tool: `nanobot.agent.tools.loader.ToolLoader.discover(self) -> list[type[Tool]]`
                 `nanobot.agent.tools.base.Tool`, `nanobot.agent.tools.base.tool_parameters`
                 `nanobot.agent.tools.schema.{StringSchema, IntegerSchema, tool_parameters_schema}`
                 (IntegerSchema takes keyword arguments only)
    person_tool: same symbols as memory_tool
    history_tool: same symbols as memory_tool
    vault_write: `nanobot.agent.tools.filesystem.WriteFileTool.execute(self, path, content) -> str`
                 `nanobot.agent.tools.filesystem.EditFileTool.execute(self, path, ...) -> str`
                 `nanobot.agent.tools.apply_patch.ApplyPatchTool.execute(self, edits, ...) -> str`
                 (all three are `async def`; a sync replacement returns a str
                 into the loop's `await` and the tool call dies with TypeError)
    name_trigger: `nanobot.channels.matrix.runtime.MatrixChannel._is_bot_mentioned(self, event) -> bool`
    thread_trigger: `MatrixChannel._is_bot_mentioned` (same symbol, wrapped after it)
                 `MatrixChannel._on_message(self, room, event)` (async)
                 `MatrixChannel._on_media_message(self, room, event)` (async)
                 `MatrixChannel.client` (nio AsyncClient), `MatrixChannel.config.user_id`
                 — and that `groupPolicy: mention` still routes through
                 `_is_bot_mentioned` (config.json sets that policy)
    voice_trigger: `MatrixChannel._should_process_message(self, room, event) -> bool` (sync),
                 `MatrixChannel._handle_message(sender_id, chat_id, content, ..., metadata=)`
                 with `metadata["event_id"]`, `MatrixChannel._stop_typing_keepalive(room_id,
                 clear_typing=True)`, `MatrixChannel.is_allowed(sender)`, and that
                 `_on_media_message` passes the transcript as `[transcription: <text>]`;
                 `MatrixChannel.send(self, msg: OutboundMessage)` (async, a dataclass whose
                 `metadata["event_id"]` is the message being answered)
    open_question: the voice_trigger symbols, plus `room.room_id` and `event.sender` in the gate
    answers:     an answer is an `OutboundMessage` with `event` None or
                 `nanobot.bus.outbound_events.StreamedResponseEvent`; every notice
                 (progress, compaction, retry wait) carries its own event (`_is_answer`)
    status_trace: `nanobot.bus.outbound_events.ProgressEvent` (`tool_hint`, `tool_events[].name`),
                 `nanobot.events.ContextCompactionEvent` (`phase`) reaching `MatrixChannel.send`,
                 reaching `MatrixChannel.send` when `sendToolHints` is on,
                 `MatrixChannel._send_room_content(room_id, content)` returning a response
                 with `event_id`, `MatrixChannel._build_thread_relates_to(metadata)` (static)
    join_greeting: `nanobot.channels.matrix.runtime.MatrixChannel._on_room_invite(self, room, event)`
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
    import datetime as _datetime

    import nanobot.agent.loop as _loop
    from brief import brief_lines as _brief_lines
    from brief import when_sent as _when_sent
    from nanobot.runtime_context import RuntimeContextBlock as _Block
    from nanobot.runtime_context import wrap_runtime_context_lines as _wrap

    async def _family_brief(request):
        lines = [_when_sent(_datetime.datetime.now().astimezone())]
        try:
            lines += _brief_lines(request, request.workspace)
        except Exception:
            # A briefing is an optimisation, never a dependency — never break a turn.
            _log.exception("brief failed; continuing without a briefing")
        return _Block(source="famstack-brief", content=_wrap(lines))

    _orig_loop_init = _loop.AgentLoop.__init__

    def _loop_init(self, *args, **kwargs):
        _orig_loop_init(self, *args, **kwargs)
        self.register_runtime_context_provider(_family_brief)

    _loop.AgentLoop.__init__ = _loop_init
    _log.info("brief runtime-context provider active")
except Exception:
    # If the internal symbol moved (nanobot upgrade), fail loudly in the log but
    # do not stop the agent from starting.
    _log.exception("brief shim could not attach (nanobot internals changed?)")


def _is_progress(msg) -> bool:
    """True for nanobot's progress messages (tool hints, progress text), not an answer."""
    try:
        from nanobot.bus.outbound_events import ProgressEvent
    except Exception:
        return False
    return isinstance(getattr(msg, "event", None), ProgressEvent)


def _is_answer(msg) -> bool:
    """True for the agent speaking to the family, not one of nanobot's notices.

    A notice carries text too ("Compressing context…", a tool hint, a retry
    wait), so text alone does not make an answer. An answer carries no
    event, or the one that marks it as streamed.
    """
    if not msg.content:
        return False
    event = getattr(msg, "event", None)
    if event is None:
        return True
    try:
        from nanobot.bus.outbound_events import StreamedResponseEvent
    except Exception:
        return False
    return isinstance(event, StreamedResponseEvent)


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
    ("list_tool", "list_edit item tool -> stack memory list"),
    ("tool_trim", "unused-tool trim (AGENT_TOOL_TRIM=0 to disable)"),
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

    import nanobot.channels.matrix.runtime as _matrix
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


# ── thread_trigger: a message in the agent's thread, or a reply to it, is for it ──
# The threaded half of the same gate, in two parts because nanobot's gate is
# synchronous and the question is not: `_on_message` (async) settles whether the
# thread is ours and remembers it, `_is_bot_mentioned` (sync) reads that answer.
# Wraps whatever `_is_bot_mentioned` is by now, so pill mentions and the name
# matcher keep working and this only gets a say when both said no — and so a
# name-trigger that failed to attach costs only itself.
try:
    import nanobot.channels.matrix.runtime as _matrix_thread
    from thread_trigger import AgentMessages as _AgentMessages
    from thread_trigger import AgentThreads as _AgentThreads
    from thread_trigger import replied_to as _replied_to
    from thread_trigger import thread_root as _thread_root

    _agent_threads = _AgentThreads()
    _agent_messages = _AgentMessages()

    def _learn_threads(orig):
        """Settle the thread and reply questions before nanobot's gate asks them."""
        async def _wrapped(self, room, event):
            # Both swallow their own failures; a homeserver hiccup must not
            # stop the message being routed by the other rules.
            root = _thread_root(event)
            if root and not _agent_threads.includes(root):
                await _agent_threads.observe(
                    self.client, room.room_id, root, self.config.user_id,
                )
            target = _replied_to(event)
            if target and not _agent_messages.includes(target):
                await _agent_messages.observe(
                    self.client, room.room_id, target, self.config.user_id,
                )
            return await orig(self, room, event)
        return _wrapped

    _orig_mentioned_pre_thread = _matrix_thread.MatrixChannel._is_bot_mentioned

    def _is_bot_mentioned_or_our_thread(self, event):
        if _orig_mentioned_pre_thread(self, event):
            return True
        try:
            root, target = _thread_root(event), _replied_to(event)
            return bool(root and _agent_threads.includes(root)
                        or target and _agent_messages.includes(target))
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


# ── status_trace: a slow turn shows what the agent is doing ──────────────────
# One status notice per slow turn, a line appended as the agent moves on; the
# answer comes as its own message and the notice stays as a trace. nanobot's
# tool hints (config.json `sendToolHints`) are the signal and never reach the
# chat themselves. Installed before the voice and question gates, so a message
# they drop never starts a status. See status_trace.py.
try:
    import asyncio as _asyncio_trace
    import os as _os_trace
    import time as _time_trace

    import nanobot.channels.matrix.runtime as _matrix_trace
    import status_trace as _status_trace
    from nanobot.events import ContextCompactionEvent as _Compaction

    # How often a running trace looks whether its status should change.
    _TRACE_TICK = 1.0
    _traces: dict[tuple[str, str], "_Running"] = {}

    class _Running:
        """One turn's trace: its status notice in Matrix, and the loop keeping it current."""

        def __init__(self, channel, room_id: str, metadata: dict) -> None:
            self.trace = _status_trace.Trace(started=_time_trace.monotonic(),
                                             lang=_os_trace.environ.get("AGENT_LANGUAGE", ""))
            self.channel, self.room_id, self.metadata = channel, room_id, metadata
            self.notice_id = ""
            self.task = _asyncio_trace.create_task(self._keep_current())

        async def _keep_current(self) -> None:
            while True:
                await _asyncio_trace.sleep(_TRACE_TICK)
                lines = self.trace.due(at=_time_trace.monotonic())
                if lines:
                    await self._show("\n".join(lines))

        async def _show(self, text: str) -> None:
            notice = {"msgtype": "m.notice", "body": text}
            if self.notice_id:
                content = {**notice, "body": f"* {text}", "m.new_content": notice,
                           "m.relates_to": {"rel_type": "m.replace", "event_id": self.notice_id}}
            else:
                thread = self.channel._build_thread_relates_to(self.metadata)
                content = {**notice, "m.relates_to": thread} if thread else notice
            try:
                response = await self.channel._send_room_content(self.room_id, content)
            except Exception:
                _log.debug("status notice not sent", exc_info=True)
                return
            self.notice_id = self.notice_id or getattr(response, "event_id", "") or ""

    def _trace_key(chat_id: str, metadata) -> tuple[str, str]:
        return chat_id, (metadata or {}).get("thread_root_event_id", "") or ""

    _orig_handle_trace = _matrix_trace.MatrixChannel._handle_message
    _orig_send_trace = _matrix_trace.MatrixChannel.send

    async def _handle_and_trace(self, sender_id, chat_id, content, *args, **kwargs):
        metadata = kwargs.get("metadata") or {}
        key = _trace_key(chat_id, metadata)
        if key in _traces:
            _traces.pop(key).task.cancel()
        _traces[key] = _Running(self, chat_id, metadata)
        return await _orig_handle_trace(self, sender_id, chat_id, content, *args, **kwargs)

    async def _send_traced(self, msg):
        key = _trace_key(msg.chat_id, msg.metadata)
        running = _traces.get(key)
        if _is_progress(msg) and msg.event.tool_hint:
            # The signal, not a message: what the agent started doing.
            if running:
                running.trace.tools([e.get("name", "") for e in msg.event.tool_events or []])
            return None
        if isinstance(msg.event, _Compaction):
            # A line in the family's words while someone waits; between turns
            # nobody does, and a notice out of nowhere only puzzles.
            if running and msg.event.phase == "started":
                running.trace.tidying()
            return None
        if running and _is_answer(msg):
            _traces.pop(key).task.cancel()
        return await _orig_send_trace(self, msg)

    _matrix_trace.MatrixChannel._handle_message = _handle_and_trace
    _matrix_trace.MatrixChannel.send = _send_traced
    _log.info("status-trace shim active")
except Exception:
    _log.exception("status-trace shim could not attach (nanobot internals changed?)")


# ── voice_trigger: a voice message is addressed by what was said ─────────────
# nanobot's gate runs before transcription, and a voice message's text is
# "Voice message", so in a shared room it never reached the agent. Let it
# through the gate, and once nanobot has transcribed it, hold the transcript
# to the rule typed text meets: it has to address the agent by name. See
# voice_trigger.py.
try:
    import dataclasses as _dataclasses_voice
    import os as _os_voice

    import nanobot.channels.matrix.runtime as _matrix_voice
    from name_trigger import addressed_by_name as _addressed_by_name_voice
    from voice_trigger import PendingVoice as _PendingVoice
    from voice_trigger import is_voice as _is_voice
    from voice_trigger import quoted as _quoted
    from voice_trigger import spoken_text as _spoken_text

    _pending_voice = _PendingVoice()
    _orig_should_process = _matrix_voice.MatrixChannel._should_process_message
    _orig_handle_voice = _matrix_voice.MatrixChannel._handle_message

    def _should_process_message(self, room, event):
        if _orig_should_process(self, room, event):
            return True
        if not (_is_voice(event) and self.is_allowed(event.sender)):
            return False
        _pending_voice.hold(getattr(event, "event_id", "") or "")
        return True

    async def _handle_message(self, sender_id, chat_id, content, *args, **kwargs):
        event_id = (kwargs.get("metadata") or {}).get("event_id", "")
        spoken = _spoken_text(content)
        if _pending_voice.release(event_id) and not _addressed_by_name_voice(
                spoken, _os_voice.environ.get("AGENT_NAME", "")):
            # Not for the agent: no answer, and no typing indicator left on.
            await self._stop_typing_keepalive(chat_id, clear_typing=True)
            return None
        _pending_voice.heard(event_id, spoken)
        return await _orig_handle_voice(self, sender_id, chat_id, content, *args, **kwargs)

    _orig_send_voice = _matrix_voice.MatrixChannel.send

    async def _send(self, msg):
        # The answer carries the voice message's event id for threading; the
        # first answer gets what was heard in front of it, not a notice.
        heard = _pending_voice.answering((msg.metadata or {}).get("event_id", "")) \
            if _is_answer(msg) else ""
        if heard:
            msg = _dataclasses_voice.replace(msg, content=_quoted(heard, msg.content))
        return await _orig_send_voice(self, msg)

    _matrix_voice.MatrixChannel._should_process_message = _should_process_message
    _matrix_voice.MatrixChannel._handle_message = _handle_message
    _matrix_voice.MatrixChannel.send = _send
    _log.info("voice-trigger shim active")
except Exception:
    _log.exception("voice-trigger shim could not attach (nanobot internals changed?)")


# ── open_question: the answer to the agent's own question needs no name ─────
# While the agent's last message in a room asks someone something, that
# person's next message there reaches the agent without its name. Who the
# agent is talking to comes from the message it handles, the question from
# the message it sends. See open_question.py.
try:
    import time as _time_question

    import nanobot.channels.matrix.runtime as _matrix_question
    from open_question import OpenQuestions as _OpenQuestions
    from open_question import asks as _asks

    _open_questions = _OpenQuestions()
    _talking_to: dict[str, str] = {}
    _orig_should_process_q = _matrix_question.MatrixChannel._should_process_message
    _orig_handle_q = _matrix_question.MatrixChannel._handle_message
    _orig_send_q = _matrix_question.MatrixChannel.send

    def _should_process_or_answer(self, room, event):
        if _orig_should_process_q(self, room, event):
            return True
        return _open_questions.answers(getattr(room, "room_id", ""), event.sender,
                                       at=_time_question.monotonic())

    async def _handle_and_remember(self, sender_id, chat_id, content, *args, **kwargs):
        _talking_to[chat_id] = sender_id
        return await _orig_handle_q(self, sender_id, chat_id, content, *args, **kwargs)

    async def _send_and_remember(self, msg):
        if _is_answer(msg):
            if _asks(msg.content):
                _open_questions.asked(msg.chat_id, _talking_to.get(msg.chat_id, ""),
                                      at=_time_question.monotonic())
            else:
                _open_questions.closed(msg.chat_id)
        return await _orig_send_q(self, msg)

    _matrix_question.MatrixChannel._should_process_message = _should_process_or_answer
    _matrix_question.MatrixChannel._handle_message = _handle_and_remember
    _matrix_question.MatrixChannel.send = _send_and_remember
    _log.info("open-question shim active")
except Exception:
    _log.exception("open-question shim could not attach (nanobot internals changed?)")


# ── join_greeting: say something useful the moment you are invited ───────────
# Stock nanobot joins an invite silently. In a topic room that silence is the
# family's first impression of the agent, so it takes one ordinary turn instead
# (see join_greeting.py for why generated rather than canned).
try:
    import asyncio as _asyncio
    import os.path as _ospath
    from pathlib import Path as _Path

    import nanobot.channels.matrix.runtime as _matrix_join
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
