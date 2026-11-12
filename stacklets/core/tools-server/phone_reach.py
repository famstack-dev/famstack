"""The phone's reach: whether a device on the home network has opened famstack's phone page.

Step 4 of the setup page, "Chat on your phone", has two parts. Part 1,
"Your phone reaches famstack", is done when the page behind the step's QR
code (`/go/app`, `phone_page.py`) has loaded on another device: that
proves the home network carries the phone to this Mac, before any app is
installed. This module keeps that fact for the setup page.

    # A phone opens /go/app on the home address:
    seen = record(here=False, now=now)                  # True: a reach
    reached()                                           # now
    # The Mac opens it on the Mac's address: that proves nothing about the network.
    record(here=True, now=later)                        # False, nothing kept

    phone ── /go/app ──> record() ──> event "phone.reached" ──> /setup reads again
                                                                (setup-page.js)

Why it is kept this way:

- **The listener decides, not the address or a header.** Docker's port
  forwarding gives every request the same source address, so the
  listener a request came through is the sign (`access.here`): a request
  through the home address (42000) is a reach, one through the Mac's
  address (42002) is not. A browser on this Mac that opens the home
  address counts too; it is rare, and it does prove the address answers.
- **In memory, and in the admin's browser.** The tools server keeps the
  time of the last reach while it runs. Core is recreated now and then
  during the install, which forgets it, so the setup page also writes it
  into a cookie of the admin's browser (`COOKIE`), as it keeps the
  admin's answer about the phone. Nothing is written to disk.
- **An event, so the Mac's page updates.** `record` publishes `EVENT` with
  the subject `SUBJECT` on the dashboard's live stream; the setup page's
  script reads the page again when it sees it (ADR-020).

It records that some device reached the Mac, not whose: the page asks
for no sign-in. Whether the admin signed in with the app is part 2 of
the step, not this module.

Pure but for the one value it keeps: no web framework, no I/O.
"""

from __future__ import annotations

from datetime import datetime

__all__ = ["COOKIE", "EVENT", "SUBJECT", "record", "reached", "forget"]

# The setup page's cookie that keeps a reach across a restart of core.
COOKIE = "fs-setup-phone-reached"
# The live event of a reach, and its subject, which a screen subscribes to.
EVENT = "phone.reached"
SUBJECT = "setup/phone"

_last: datetime | None = None


def record(*, here: bool, now: datetime) -> bool:
    """Keep `now` as the last reach when the page was opened through the
    home address; returns whether it counted.

    `here` is `access.here(request)`. A page opened on the Mac's address
    is no reach and changes nothing.

        record(here=False, now=now)   # True
        record(here=True, now=now)    # False
    """
    global _last
    if here:
        return False
    _last = now
    return True


def reached() -> datetime | None:
    """When a device last opened the phone page, since the server started; None before.

        reached()                                        # None
        record(here=False, now=now)
        reached()                                        # now
    """
    return _last


def forget() -> None:
    """Forget the last reach, as a fresh start of the server does. For tests
    and the preview, which share one server between them.

        forget()
        reached()                                        # None
    """
    global _last
    _last = None
