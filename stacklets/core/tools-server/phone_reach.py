"""The phone's reach: whether a device on the home network has opened famstack's phone page.

Step 4 of the setup page, "Chat on your phone", has two parts. Part 1,
"Your phone reaches famstack", is done when the page behind the step's QR
code (`/go/app`, `phone_page.py`) has loaded on another device: that
proves the home network carries the phone to this Mac, before any app is
installed. This module keeps that fact for the setup page.

    # A phone opens /go/app at the Mac's address:
    seen = record(host="192.168.1.20:42000", now=now)    # True: a reach
    reached()                                           # now
    # The Mac opens it by localhost: that proves nothing about the network.
    record(host="localhost:42000", now=later)           # False, nothing kept

    phone ── /go/app ──> record() ──> event "phone.reached" ──> /setup reads again
                                                                (setup-page.js)

Why it is kept this way:

- **The Host header decides, not the address.** Docker's port forwarding
  gives every request the same source address (`access.py`), so the
  address bar is the only sign of where a browser is: `localhost` on this
  Mac, the Mac's home-network address or name everywhere else. A browser
  on this Mac that uses the home-network address counts too; it is rare,
  and it does prove the address answers.
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

from dashboard_page import on_this_mac

__all__ = ["COOKIE", "EVENT", "SUBJECT", "record", "reached", "forget"]

# The setup page's cookie that keeps a reach across a restart of core.
COOKIE = "fs-setup-phone-reached"
# The live event of a reach, and its subject, which a screen subscribes to.
EVENT = "phone.reached"
SUBJECT = "setup/phone"

_last: datetime | None = None


def record(*, host: str, now: datetime) -> bool:
    """Keep `now` as the last reach when the page was opened at `host`
    from another device; returns whether it counted.

    `host` is the request's Host header. One opened on this Mac
    (`localhost`, `127.0.0.1`) is no reach and changes nothing.

        record(host="192.168.1.20:42000", now=now)   # True
        record(host="localhost:42000", now=now)      # False
    """
    global _last
    if on_this_mac(host):
        return False
    _last = now
    return True


def reached() -> datetime | None:
    """When a device last opened the phone page, since the server started; None before.

        reached()                                        # None
        record(host="192.168.1.20:42000", now=now)
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
