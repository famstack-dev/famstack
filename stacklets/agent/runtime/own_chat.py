"""What one chat leaves behind reaches no other chat.

    own_chat.install()      # sitecustomize, before vault_write; stops the agent if it fails

Stacky answers many chats from one nanobot process, and every chat's tools
see the same files. `config.json` keeps the file tools inside the agent's
workspace (`restrict_to_workspace`) and exec to plain `stack` lines
(`allow_patterns`), so the sessions, the matrix store and the rest of the
agent's home are out of reach. Three things nanobot does itself would still
carry one chat's words into another, and this module stops each:

    the shared history    nanobot's consolidation appends a summary of every
                          chat to `workspace/memory/history.jsonl`, and its
                          file tools allow that file by name. Only Dream reads
                          it, and Dream is off; each chat keeps its own
                          summary in its own session. So nothing is written.
    attachments           every chat's files land in one media folder, which
                          nanobot's file tools and message tool allow outside
                          the workspace. A chat now reaches only the files of
                          its own conversation: the ones the Matrix channel
                          downloaded for it, recorded in its session.
    writes                `write_file`, `edit_file` and `apply_patch` on a plain
                          path leave a file in the workspace that every chat
                          reads, and a skill or profile written there goes into
                          every chat's prompt. They are refused. `vault_write`,
                          installed after this, still routes a memory page to
                          `stack memory write`.

SAFETY: these are gates, not features. If one cannot attach (nanobot moved a
symbol), `install` raises and sitecustomize stops the process, because an
agent that answers with a gate missing hands one chat's words to another.
`runtime/chat_aside.py` moves what an earlier release already wrote.

The tests drive nanobot's own tools with this runtime loaded
(`tests/unit/stacklets/nanobot_agent.py`).
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

from turn import current as current_turn

__all__ = ["install", "WRITE_REFUSAL"]

_log = logging.getLogger("agent.runtime.own_chat")

WRITE_REFUSAL = (
    "Error: I do not write files into my workspace, because every chat reads it. "
    "A memory page is written as vault/<path>.md; anything else goes into the memory "
    "with the memory tools.")

# The one loop's sessions, kept when nanobot builds it (one AgentLoop per process).
_sessions = None


# The session metadata key that lists the files a conversation was sent.
SENT_FILES = "stack_sent_files"


def install() -> None:
    """Attach the three gates; raise if any of them cannot attach."""
    _keep_the_sessions()
    _record_sent_files()
    _write_no_shared_history()
    _own_attachments_only()
    _refuse_plain_writes()


# ── The shared history ──────────────────────────────────────────────────

def _write_no_shared_history() -> None:
    from nanobot.agent.memory import MemoryStore

    def append_history(self, entry, *, max_chars=None, session_key=None) -> int:
        """Keep nothing: the summary stays in its own session only."""
        return 0

    MemoryStore.append_history = append_history


# ── Attachments ─────────────────────────────────────────────────────────

def _keep_the_sessions() -> None:
    from nanobot.agent.loop import AgentLoop

    original = AgentLoop.__init__

    @functools.wraps(original)
    def init(self, *args, **kwargs):
        global _sessions
        original(self, *args, **kwargs)
        _sessions = getattr(self, "sessions", None)

    AgentLoop.__init__ = init


def _record_sent_files() -> None:
    """Record the files of each inbound message in its conversation's session.

    The Matrix channel downloads a message's files into the media folder and
    hands their paths to the loop as the message's `media`; the photo sent a
    moment before the words arrives there too (`photo_before`). nanobot's
    restore stage then keeps only the images under `media` and writes every
    other file into the message's text as `[Attachment: <path>]`, a line
    anybody can type. So the paths are taken here, before that stage, and
    kept in the session's metadata, which nanobot saves with the session: the
    record survives a restart of the agent, and goes with the session when
    the session is deleted.
    """
    from nanobot.agent.loop import AgentLoop, TurnKind

    original = AgentLoop._restore_turn

    @functools.wraps(original)
    async def restore(self, ctx):
        sent = list(ctx.msg.media or []) if ctx.kind is TurnKind.USER else []
        await original(self, ctx)
        if sent and ctx.session is not None:
            known = ctx.session.metadata.get(SENT_FILES) or []
            new = [p for p in map(_resolved, sent) if p not in known]
            ctx.session.metadata[SENT_FILES] = [*known, *new]

    AgentLoop._restore_turn = restore


def _resolved(path) -> str:
    """`path` as one absolute spelling, so a record and a request compare."""
    return str(Path(str(path)).expanduser().resolve())


def _in_this_conversation(path: Path) -> bool:
    """Whether the current turn's conversation was sent `path`.

    Only the files the channel downloaded for the conversation count
    (`_record_sent_files`), never a path in a message's text, because a
    person or the model writes that text. Outside a turn, or with the
    sessions unknown, nothing is this conversation's.
    """
    conversation = current_turn().conversation
    if not conversation or _sessions is None:
        return False
    session = _sessions.get_or_create(conversation)
    return _resolved(path) in (session.metadata.get(SENT_FILES) or [])


def _own_attachments_only() -> None:
    """Resolve the file and message tools' paths without the shared media
    folder, then let a refused path through only when it is an attachment of
    this conversation."""
    import nanobot.agent.tools.filesystem as filesystem
    import nanobot.agent.tools.message as message
    from nanobot.agent.tools.path_utils import resolve_workspace_path
    from nanobot.security.workspace_policy import WorkspaceBoundaryError

    def resolve(path, workspace=None, allowed_dir=None, extra_allowed_dirs=None,
                extra_allowed_files=None, include_media_dir=True):
        try:
            return resolve_workspace_path(path, workspace, allowed_dir, extra_allowed_dirs,
                                          extra_allowed_files, include_media_dir=False)
        except WorkspaceBoundaryError:
            candidate = Path(str(path)).expanduser()
            if include_media_dir and candidate.is_absolute() and _in_this_conversation(candidate):
                return candidate.resolve()
            raise

    filesystem.resolve_workspace_path = resolve
    message.resolve_workspace_path = resolve


# ── Writes ──────────────────────────────────────────────────────────────

def _refuse_plain_writes() -> None:
    from nanobot.agent.tools.apply_patch import ApplyPatchTool
    from nanobot.agent.tools.base import ToolResult
    from nanobot.agent.tools.filesystem import EditFileTool, WriteFileTool

    async def refuse(self, *args, **kwargs):
        _log.info("refused %s: the workspace is read by every chat", type(self).__name__)
        return ToolResult.error(WRITE_REFUSAL)

    for tool in (WriteFileTool, EditFileTool, ApplyPatchTool):
        tool.execute = refuse
