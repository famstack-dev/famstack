"""Which AI the memory's pages name for ✦: famstack's, the family's own server, or just an AI.

    from memory_ui import ai_route

    route = await ai_route.read(ai_plan)       # "managed", "external" or ""
    ai_route.of({"provider": "external"})     # "external"; the answer of `stack ai recommended show`

A note's text is written by the AI server `[ai] provider` names. The stack
runs that server itself (`managed`), or the family points it at a server
of its own (`external`). A family with its own server is not told that
famstack's AI wrote its notes, so the words beside ✦ follow the route
(`entry.made.ai_hint`, `entry.made.key_line`). Any other answer (`none`,
nothing set, a host API that did not answer) names just "AI", which is
true for every route.

A page reads the route once, through the host API's
`stack ai recommended show`, which writes nothing. The read runs beside
the page's own reads and gives up after `READ_SECS`: the route only
changes a few words, so a host API that hangs never holds a page, it only
makes ✦ say "Summarised by AI". The card page (`pages.open_card`) and a
topic's page (`memory_home.graph_pages.open_topic`) both read it here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

__all__ = ["AiPlan", "ROUTES", "READ_SECS", "of", "read"]

# The host API's read of `stack ai recommended show`: its answer, or None.
AiPlan = Callable[[], Awaitable[dict[str, object] | None]]

# The routes that have words of their own; any other is "" (just an AI).
ROUTES = ("managed", "external")

# How long a page waits for the route. Other host API reads wait up to
# 30 s, which a page cannot afford for a few words.
READ_SECS = 2.0


def of(plan: Mapping[str, object] | None) -> str:
    """The route of the AI from `stack ai recommended show`'s answer:
    "managed", "external", or "" for any other provider and for no answer.

        >>> of({"provider": "managed"}), of({"provider": "none"}), of(None)
        ('managed', '', '')
    """
    provider = str((plan or {}).get("provider") or "")
    return provider if provider in ROUTES else ""


async def read(ai_plan: AiPlan | None) -> str:
    """The route, read once through `ai_plan`; "" when there is no
    `ai_plan`, when it fails, or when it takes longer than `READ_SECS`.
    Never raises, because a page answers whatever the host API does."""
    if ai_plan is None:
        return ""
    try:
        return of(await asyncio.wait_for(ai_plan(), READ_SECS))
    except Exception:  # a timeout or a failed read: the words name just an AI
        return ""
