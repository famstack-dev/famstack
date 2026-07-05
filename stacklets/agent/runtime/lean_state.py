"""Keep the agent's *state* lean, as distinct from the chat *transcript*.

The transcript is the Matrix room: complete, immutable, always replayable. The
state is the far smaller working memory we feed the model each turn. By default
nanobot's context is a near-verbatim replay of the transcript, so a stale tool
result rides along and the model answers "what's still open?" by reciting an old
list instead of re-fetching.

This module trims exactly one thing, well: tool results from *previous* turns.
It replaces the result body with the very call that produced it -- `name(args)` --
so the state carries a cheap, self-documenting pointer (the model can re-issue
that call to refresh) instead of a fat, stale payload. The *current* turn's own
tool results are left whole, so the model still reasons over what it just
fetched.

Portability: `lean_messages` is a pure transform over the OpenAI-style message
list. When we fork nanobot, call it as the last step of
`ContextBuilder.build_messages` and delete the monkeypatch in sitecustomize.py.
Nothing else moves.
"""

from __future__ import annotations

from typing import Any

# Keep the re-fetch pointer short even when the original call carried big args
# (a write_file, a long command). We name the call, not its payload.
_ARGS_CAP = 200


def _last_user_index(messages: list[dict[str, Any]]) -> int:
    """Index of the last user message: the boundary of the current turn.

    At or after it is this turn -- its fresh tool results must stay. Before it is
    a previous turn -- those tool results get pointered. Anchoring on the current
    user message is correct whether ``build_messages`` runs once per turn or once
    per tool-loop iteration, so we never trim results the model is mid-reasoning
    over.
    """
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return i
    return len(messages)  # no user message -> treat everything as prior


def _call_labels(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Map each ``tool_call_id`` to a short ``name(arguments)`` label, read from
    the assistant messages that requested the calls."""
    labels: dict[str, str] = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            tcid = tc.get("id")
            if not tcid:
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or "tool"
            args = (fn.get("arguments") or "").strip()
            if len(args) > _ARGS_CAP:
                args = args[:_ARGS_CAP] + "..."
            labels[tcid] = f"{name}({args})" if args else f"{name}()"
    return labels


def lean_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse previous-turn *derived data* to pointers, keeping conversation.

    Two kinds of stale projection are replaced by a pointer that names the call
    that produced them, so the model re-fetches instead of reciting:
      - a tool **result** from a previous turn, and
      - the assistant's synthesized **answer** in a turn that used a tool (a list
        it read out, a count it reported).
    Assistant messages in turns that used no tool are pure conversation and are
    left verbatim, as is everything from the current turn (anchored on the last
    user message). Only ``content`` is rewritten; the ``tool_call_id`` pairing
    with the assistant's ``tool_calls`` is preserved, so the list stays valid.
    """
    boundary = _last_user_index(messages)
    labels = _call_labels(messages)
    out: list[dict[str, Any]] = []
    turn_calls: list[str] = []   # tool calls issued in the turn being scanned
    since_tool = False           # have we passed a tool result in this turn?
    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "user":
            turn_calls, since_tool = [], False
        for tc in m.get("tool_calls") or []:
            if label := labels.get(tc.get("id")):
                turn_calls.append(label)
        prior = i < boundary
        if role == "tool":
            since_tool = True
            if prior:
                call = labels.get(m.get("tool_call_id"), "the tool")
                m = {**m, "content": f"[prior result of {call}; re-run for the current value]"}
        elif prior and role == "assistant" and since_tool and not m.get("tool_calls"):
            # The synthesized answer of a tool-using turn is itself a stale
            # projection -- decay it too, naming the call, so the model
            # re-derives instead of reciting an old conclusion. Answers from
            # turns that used no tool (pure conversation) are left untouched.
            calls = "; ".join(dict.fromkeys(turn_calls)) or "a tool call"
            m = {**m, "content": f"[earlier answer from {calls}; re-run for the current value]"}
        out.append(m)
    return out


_PREVIEW_CAP = 320


def format_state_for_log(messages: list[dict[str, Any]]) -> str:
    """Render the message list as a compact transcript for the log.

    One line per message -- ROLE + a clipped, single-line preview -- so the state
    we actually send the model can be eyeballed against the Matrix chat: is the
    system prompt what we think, did the stale results collapse to pointers, is
    the current turn intact. Debug aid only; never in the model's context.
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
