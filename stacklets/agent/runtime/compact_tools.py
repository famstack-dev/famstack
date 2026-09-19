"""Let nanobot's microcompact shorten the results of our vault tools.

Before each model call, nanobot's `AgentRunner._microcompact` keeps the
10 most recent results of the tools in `_COMPACTABLE_TOOLS` and replaces
older ones (500 characters or more) with `[<tool> result omitted from
context]`. The set lists nanobot's own read tools only. Our vault tools
return results of about 1k tokens each, so without this they stay in
the context in full for as long as the session replays them.

PIN: `nanobot.agent.runner._COMPACTABLE_TOOLS` (a frozenset of tool
names, read at call time). Re-verify on a nanobot bump.
"""

from __future__ import annotations

# Tool names as the model sees them. list_edit is not here: its results
# are one line ("added 3") and stay under the 500-character floor.
VAULT_READ_TOOLS = frozenset({
    "memory_search",
    "memory_person",
    "memory_history",
})


def install() -> None:
    import nanobot.agent.runner as runner

    runner._COMPACTABLE_TOOLS = frozenset(runner._COMPACTABLE_TOOLS) | VAULT_READ_TOOLS
