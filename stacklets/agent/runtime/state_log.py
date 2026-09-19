"""Render the message list nanobot builds for a turn, for the debug log.

The transcript is the Matrix room. The state is the message list that
`ContextBuilder.build_messages` assembles for the model. This module
formats that list as one line per message, so the two can be compared.

The log shows the list before nanobot's per-call steps in
`AgentRunner` (microcompact, tool-result budget, history snip). Those
steps change only tool results and the oldest messages.
"""

from __future__ import annotations

from typing import Any

_PREVIEW_CAP = 320


def format_state_for_log(messages: list[dict[str, Any]]) -> str:
    """Render the message list as a compact transcript for the log.

    One line per message: ROLE and a clipped, single-line preview. Debug
    aid only; never in the model's context.
    """
    lines = []
    for m in messages:
        role = str(m.get("role", "?")).upper()
        content = m.get("content")
        if isinstance(content, list):  # multimodal parts -> just the text
            content = " ".join(p.get("text", "") for p in content
                               if isinstance(p, dict))
        content = (content or "").replace("\n", " / ")
        if len(content) > _PREVIEW_CAP:
            content = content[:_PREVIEW_CAP] + "..."
        if calls := m.get("tool_calls"):
            names = ", ".join((c.get("function") or {}).get("name", "?") for c in calls)
            content = f"->calls {names}  {content}".rstrip()
        lines.append(f"  {role:9} {content}")
    return "\n".join(lines)
