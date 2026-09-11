"""Scribe — retired, and saying so.

Transcribing voice messages used to be this bot's whole job. It is the
transport's job now: a voice message is decoded before any handler sees
it, so every bot in every room gets the words without asking, and nothing
needs Scribe to be present.

This shell exists for one release only, because the framework has no way
to deprovision a bot that goes away. Deleting the declaration stops the
runner from launching it, but the Matrix account survives — still joined
to whatever rooms it was invited to, listed as a member, answering
nothing, forever. Somebody would eventually go looking for why.

So instead of vanishing it explains itself once per room and leaves. The
people this reaches are the only ones it can have affected: Scribe
declared no room of its own, so it was never in a room unless a person
went and invited it by hand. Those are exactly the people who would
notice it go quiet.

Delete this file and its bot.toml one release after it ships.
"""

import asyncio
import os

from loguru import logger
from nio import AsyncClient

from microbot import MicroBot


# Kept inline rather than in a message catalogue: this is two strings with
# a known expiry, and a catalogue would outlive the bot that uses it.
_GOODBYE = {
    "en": (
        "**Voice messages are transcribed automatically now.**\n\n"
        "famstack does it for every room, so you no longer need me here. "
        "Keep sending voice messages exactly as you always have. They are "
        "transcribed the moment they arrive, and the other bots read the "
        "words rather than the recording.\n\n"
        "Nothing is lost and there is nothing to set up. I am leaving this "
        "room; you can remove my account whenever you like."
    ),
    "de": (
        "**Sprachnachrichten werden jetzt automatisch transkribiert.**\n\n"
        "famstack übernimmt das für jeden Raum, ihr braucht mich hier also "
        "nicht mehr. Schickt Sprachnachrichten weiter wie bisher. Sie "
        "werden sofort transkribiert, und die anderen Bots lesen den Text "
        "statt der Aufnahme.\n\n"
        "Es geht nichts verloren und es ist nichts einzurichten. Ich "
        "verlasse diesen Raum; mein Konto könnt ihr jederzeit löschen."
    ),
}


class ScribeBot(MicroBot):
    name = "scribe-bot"

    def register_callbacks(self, client: AsyncClient) -> None:
        """Register nothing, and start the retirement sweep.

        No message handlers at all: this bot answers nothing. The sweep
        runs on every launch rather than once, so a room it could not
        leave (homeserver hiccup, lost network) is retried next boot
        instead of keeping a silent member forever.

        Scheduled as a task because `register_callbacks` is sync and runs
        inside `start()`'s event loop, after the initial sync has
        populated `client.rooms`.
        """
        asyncio.create_task(self.retire_everywhere())

    async def on_room_joined(self, room_id: str) -> None:
        """Someone followed an older guide and invited it. Same answer,
        so an invite never leaves a silent member behind."""
        await self._retire_from(room_id)

    async def retire_everywhere(self) -> None:
        """Say goodbye in every room this account is still in, and leave."""
        room_ids = list(self.client.rooms.keys())
        if room_ids:
            logger.info(
                "[{}] retiring from {} room(s)", self.name, len(room_ids),
            )
        for room_id in room_ids:
            await self._retire_from(room_id)

    async def _retire_from(self, room_id: str) -> None:
        """Explain, then leave. A failure to leave is logged and dropped:
        the goodbye still landed, and the next launch tries again."""
        lang = os.environ.get("LANGUAGE", "en")
        await self._send(
            room_id, _GOODBYE.get(lang, _GOODBYE["en"]), msgtype="m.notice",
        )
        try:
            await self.client.room_leave(room_id)
        except Exception as e:
            logger.warning(
                "[{}] could not leave {}: {} — will retry on next launch",
                self.name, room_id, e,
            )
