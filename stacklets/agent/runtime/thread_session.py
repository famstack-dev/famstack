"""Thread-scoped sessions: one context per Matrix thread.

nanobot keys a session as `channel:chat_id`, so every thread and every
speaker in a room shares one transcript. That is the measured context
pollution (docs/design/agent/review-2026-09.md section 2). nanobot
0.2.2 already has the seam: `InboundMessage.session_key_override`.
This shim sets it for threaded messages, so a thread gets its own
session. Replies still route by `chat_id`; the room id stays pure.

Known v1 gap: the thread's root message was handled in the room
session, so a new thread session starts without the root's text. The
model sees the first threaded reply only.

Set AGENT_THREAD_SESSIONS=0 to fall back to room-scoped sessions.

PIN: patches MatrixChannel._handle_message (inherited from
channels/base.py). Re-verify on a nanobot bump.
"""

from __future__ import annotations

import os


def install() -> None:
    if os.environ.get("AGENT_THREAD_SESSIONS", "1") == "0":
        return
    import nanobot.channels.matrix as matrix

    original = matrix.MatrixChannel._handle_message

    async def handle_with_thread_session(
        self, sender_id, chat_id, content,
        media=None, metadata=None, session_key=None, is_dm=False,
    ):
        root = (metadata or {}).get("thread_root_event_id")
        if root and not session_key:
            session_key = f"{self.name}:{chat_id}#thread:{root}"
        return await original(
            self, sender_id, chat_id, content,
            media=media, metadata=metadata,
            session_key=session_key, is_dm=is_dm,
        )

    matrix.MatrixChannel._handle_message = handle_with_thread_session
