"""The Server room's notice of a new device, from the tools server's side:
which fields go to the Mac, and at most one "a browser waits" in 10
minutes.

    await NOTICES.waits(user_agent, join)   # a browser started to wait: one notice, if due
    await NOTICES.added("d-3f9a2c1b", "d-77aa01c2")   # a device was added: always one
    NOTICES.heard(join.code)                # did the admins hear of this browser?

Every admin hears of every new device (ADR-023
D20): a browser that waits shows its code, and the admins' phones ring
with a chat message in the Server room that carries the code and an
"Allow it" link. A guest's browser that reloads twenty times, or three
browsers that wait at once, make one message in 10 minutes, so the room
is not filled; the others still show their code, and the Mac's Devices
page lists them all.

    a browser waits ──▶ NOTICES.waits ── due? ──▶ host API join_notice
                         (one in 10 min)          {kind: waits, browser: "Chrome",
                                                   system: "Mac", code, since}
                                                  ──▶ stack devices notice (the text)

Why only fields. A browser chooses its User-Agent, so this module sends
names from fixed lists (`names.parts_of`), the code and the time, and
the Mac writes the text (`stack.devices.notice`), which refuses anything
else (ADR-023 D10). A device that was added is sent as its id in the
Mac's list and the id of the device that allowed it.

The count lives in this process's memory, as the join requests do: a
restart of core starts it again. `heard(code)` tells the waiting page
whether to say that the admins heard in the chat of this browser: only for
the code the notice named, and not when the chat does not run. The other
browsers of those 10 minutes say how else to get in, because the message
the admins read names a different code.
`clock` is the time the 10 minutes are measured by; a test sets it.
"""

from __future__ import annotations

import time

from . import host, names, pages
from .codes import Join

__all__ = ["NOTICE_SECS", "Notices", "NOTICES"]

# At most one "a browser waits" notice in this time.
NOTICE_SECS = 10 * 60


class Notices:
    """The Server room's notices of one tools server process.

        notices = Notices()
        await notices.waits(chrome_on_a_mac, join)   # sent: the chat took it
        await notices.waits(firefox, other_join)     # within 10 minutes: nothing
        notices.heard(join.code)                     # True: the message named it
        notices.heard(other_join.code)               # False: it was not named
    """

    def __init__(self) -> None:
        self.clock = time.time
        self._last: float | None = None
        self._named = ""   # the code of the last notice the chat took, "" for none

    async def waits(self, user_agent: str, join: Join) -> None:
        """Tell the Server room that the browser of `user_agent` waits
        with `join`'s code, unless a notice went out in the last 10
        minutes. A notice the chat did not take counts too, so a browser
        without cookies cannot make the Mac try again and again."""
        now = self.clock()
        if self._last is not None and now - self._last < NOTICE_SECS:
            return
        self._last = now
        browser, system = names.parts_of(user_agent)
        answer = await host.call({"cmd": "join_notice", "kind": "waits", "browser": browser,
                                  "system": system, "code": join.code,
                                  "since": pages.since(join.asked)})
        self._named = join.code if answer.get("sent") is True else ""

    async def added(self, device_id: str, by: str) -> None:
        """Tell the Server room that the device `device_id` was added,
        allowed by the device `by` (an id, "mac", or "" for a pairing
        code). Not limited: only a paired device or the Mac adds one."""
        await host.call({"cmd": "join_notice", "kind": "added", "device": device_id, "by": by})

    def heard(self, code: str) -> bool:
        """Whether the admins heard in the chat, in the last 10 minutes,
        that the browser with `code` waits: True only for the code the
        last notice named and the chat took; False for every other code,
        before any notice, and when the chat did not take it."""
        return (bool(code) and code == self._named and self._last is not None
                and self.clock() - self._last < NOTICE_SECS)

    def forget(self) -> None:
        """Start the count again, as a restart of core does."""
        self._last, self._named = None, ""


NOTICES = Notices()
