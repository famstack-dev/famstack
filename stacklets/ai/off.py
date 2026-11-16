"""No AI for now: what the ai stacklet does while `[ai] provider = "none"`.

A family can run the stack without an AI and add it later
(`stack.stack.AI_OFF`). Then nothing of the ai stacklet runs: its
install builds nothing, its start starts nothing and `stack ai check`
asks no server, even one `[ai]` still names from before. Each says the
same thing: the stack runs without an AI, and the two routes turn it on.
A route (`stack ai switch`) replaces the provider, and from then on the
stacklet runs as on a first install.

    from stacklets.ai import off

    off.refuse(ctx.stack.config)    # raises RuntimeError while the AI is off
    off.said(config)                # the same sentence, or "" while it is on
"""

from __future__ import annotations

from stack.stack import ai_off

__all__ = ["TURN_ON", "said", "refuse"]

TURN_ON = ("The stack runs without an AI ([ai] provider = \"none\"). Turn it on with "
           "'./stack ai switch managed' or './stack ai switch <url>'.")


def said(config: dict) -> str:
    """`TURN_ON` while `config` (stack.toml) says no AI for now, else ""."""
    return TURN_ON if ai_off(config) else ""


def refuse(config: dict) -> None:
    """Raise RuntimeError with `TURN_ON` while the AI is off, so a hook
    stops before it installs or starts anything; return otherwise."""
    if ai_off(config):
        raise RuntimeError(TURN_ON)
