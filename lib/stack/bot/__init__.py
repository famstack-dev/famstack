"""The bot runtime: what every bot the bot runner loads is built on.

A bot is a `MicroBot` subclass in `stacklets/<id>/bot/<name>.py` (the bot
contract in docs/agent/dev.md). The runtime logs it in, keeps its session
and keys across restarts, accepts invitations, runs the sync loop, turns a
voice message into text before a handler sees it, and tells each handler
which room a message came from:

    from stack.bot import MicroBot, RoomContext

    class ScribeBot(MicroBot):
        name = "scribe"

        def register_callbacks(self, client):
            ...

`voice` is the decoding of voice messages (`stack.bot.voice`), for a bot
that reads a transcript back. `EYES`, `CHECK` and `CROSS` are the
reactions bots answer with: seen, done, failed.

The runtime only loads inside the bot runner's container, which installs
its packages (`nio`, `aiohttp`, `loguru`); the host CLI never imports it.
"""

from . import voice
from .microbot import CHECK, CROSS, EYES, MicroBot
from .room_context import RoomContext, context_for, normalize_alias

__all__ = ["CHECK", "CROSS", "EYES", "MicroBot", "RoomContext", "context_for",
           "normalize_alias", "voice"]
