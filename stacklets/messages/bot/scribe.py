"""Scribe, retired.

Transcribing voice messages was this bot's job. The transport does it
now, so nothing requires Scribe to be present in a room.

It ships for one more release because the framework has no way to
deprovision a bot. Removing the declaration stops the runner launching
it, but the Matrix account remains, still joined to whatever rooms it was
invited to and answering nothing. Rather than going silent it explains
the change once per room and leaves.

Delete this file and its bot.toml one release after it ships.
"""

import asyncio
import os

from loguru import logger
from nio import AsyncClient

from microbot import MicroBot


# Kept inline rather than in the message catalogue: two strings with a
# known expiry, which a catalogue entry would outlive.
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
        """Register no handlers and start the retirement sweep.

        The sweep runs on every launch rather than once, so a room it
        failed to leave is retried instead of keeping a silent member
        indefinitely. It is scheduled as a task because this method is
        synchronous and runs inside `start()`'s event loop, after the
        initial sync has populated `client.rooms`.
        """
        asyncio.create_task(self.retire_everywhere())

    async def on_room_joined(self, room_id: str) -> None:
        """Handle an invite from someone following older documentation.

        Same response as the boot sweep, so an invite does not leave a
        silent member behind either.
        """
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
        """Post the notice, then leave the room.

        A failure to leave is logged and otherwise ignored: the notice
        has landed, and the next launch tries again.
        """
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
