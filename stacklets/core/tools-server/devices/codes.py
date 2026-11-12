"""Pairing codes and join requests: the two ways a browser becomes a
paired device, both kept in memory only.

Pairing codes are the one-time addresses the Mac shows as a QR code:

    code = CODES.make("homer")           # on the Mac's address only
    # "q0JbZ3...": 128 random bits, in the address /go/app?pair=<code>
    CODES.take(code)                     # the phone opened it: "homer"
    CODES.take(code)                     # a second time: None
    CODES.added(code, "iPhone")          # what the Mac's page then says
    CODES.state(code)                    # {"state": "added", "device": "iPhone"}
    CODES.end(code)                      # the Mac gave up on it: it works no more

A code names one person, works once and for 10 minutes. It lives only in
the tools server's memory: a restart of core ends every code, and the
phone then sees "This code has expired", as for an old one. Nothing of a
code is written to disk, because it is worth a paired device until used.

Join requests go the other way (the device flow of RFC 8628, as a TV
signs in): a browser the home address does not know asks to join, and a
paired device allows it.

    secret, request = JOINS.ask("Chrome on a Mac")   # the waiting browser keeps `secret`
    request.code                                     # "K7MP4XQD", shown as "K7MP-4XQD"
    JOINS.waiting("k7mp-4xqd")                       # the approver typed it: the request
    JOINS.allow("K7MP4XQD", "homer", "Chrome on a Mac")
    JOINS.collect(secret)                            # the waiting browser: the allowed request, once

The code only names the request; the waiting browser holds a second,
secret value (256 random bits, in a cookie), and only `collect` with that
secret turns an allowed request into a device. So a person who reads the
code over a shoulder and types it into another browser gets nothing: that
browser has no secret. The code is 8 symbols from 31 letters and digits
without look-alikes (no 0, O, 1, I, L), about 40 bits; a request lives 10
minutes, and an approver who sends ten wrong codes in a minute waits a
minute (`Joins.wrong`), so guessing a code that waits is out of reach.

`clock` is the time the codes and requests are measured by; a test sets it.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

__all__ = ["CODE_SECS", "Codes", "CODES", "Join", "Joins", "JOINS", "shown"]

# How long a code works after the Mac made it.
CODE_SECS = 10 * 60
# The symbols of a join code: letters and digits a person cannot mistake
# for one another when they read them off a screen (no 0/O, 1/I/L).
_JOIN_SYMBOLS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_JOIN_LENGTH = 8
# Ten codes that were not waiting, within a minute, make an approver wait.
_WRONG_MOST = 10
_WRONG_SECS = 60
# Codes and requests kept at most: a setup page that is reloaded makes a new one each
# time, and the oldest go first.
_KEPT_MOST = 200


class Codes:
    """The pairing codes of one tools server process.

        codes = Codes()
        codes.state(codes.make("marge"))   # {"state": "waiting", "device": ""}
    """

    def __init__(self) -> None:
        self.clock = time.time
        self._codes: dict[str, _Pairing] = {}

    def make(self, person: str) -> str:
        """A new code for `person` (a user id, or "" for the household)."""
        self._prune()
        code = secrets.token_urlsafe(16)
        self._codes[code] = _Pairing(person, self.clock())
        return code

    def take(self, code: str) -> str | None:
        """The person of `code`, once: None for a code that is unknown,
        older than 10 minutes, or taken before."""
        pairing = self._codes.get(code)
        if pairing is None or pairing.taken or self._expired(pairing):
            return None
        pairing.taken = True
        return pairing.person

    def give_back(self, code: str) -> None:
        """Make a taken code usable again: the device could not be added."""
        if code in self._codes:
            self._codes[code].taken = False

    def added(self, code: str, device: str) -> None:
        """Note that `code` paired the device named `device`."""
        if code in self._codes:
            self._codes[code].device = device

    def end(self, code: str) -> None:
        """End `code` before its 10 minutes: the Mac stopped waiting for
        it (Ctrl-C in `stack devices pair`). A phone that opens it then
        sees "This code has expired"; a code that paired a device keeps
        its device."""
        pairing = self._codes.get(code)
        if pairing is not None and not pairing.device:
            del self._codes[code]

    def ends_at(self, code: str) -> float:
        """When `code` stops working, in seconds since the epoch (the
        `clock`'s time); 0 for an unknown code."""
        pairing = self._codes.get(code)
        return pairing.made + CODE_SECS if pairing else 0

    def state(self, code: str) -> dict:
        """What the Mac's page shows for `code`: `waiting`, `added` (with
        the device's name) or `expired` (also for an unknown code). A
        code a phone opened stays `waiting` while the Mac adds the
        device, so a page that asks in that moment does not give up."""
        pairing = self._codes.get(code)
        if pairing is not None and pairing.device:
            return {"state": "added", "device": pairing.device}
        if pairing is None or self._expired(pairing):
            return {"state": "expired", "device": ""}
        return {"state": "waiting", "device": ""}

    def person_of(self, code: str) -> str:
        """The person `code` was made for, "" for an unknown code."""
        pairing = self._codes.get(code)
        return pairing.person if pairing else ""

    def forget(self) -> None:
        """Drop every code, as a restart does."""
        self._codes.clear()

    def _expired(self, pairing: _Pairing) -> bool:
        return self.clock() - pairing.made >= CODE_SECS

    def _prune(self) -> None:
        for code in [c for c, p in self._codes.items() if self._expired(p)]:
            del self._codes[code]
        while len(self._codes) >= _KEPT_MOST:
            del self._codes[next(iter(self._codes))]


CODES = Codes()


@dataclass
class Join:
    """One browser's request to join: its code, the device's name from
    its browser ("Chrome on a Mac"), when it asked, and, once a paired
    device allowed it, for whom (`person`, a user id or "" for the
    household) and under which name.

        Join("K7MP4XQD", "Chrome on a Mac", 1762892040.0).allowed   # False
    """

    code: str
    device: str
    asked: float
    allowed: bool = False
    person: str = ""
    name: str = ""
    # The device that allowed it (its id, "mac" for the Mac): the Server
    # room's notice says who added the device.
    by: str = ""


class Joins:
    """The open join requests of one tools server process.

        joins = Joins()
        secret, request = joins.ask("Firefox on Linux")
        joins.state(secret)                              # "waiting"
        joins.allow(request.code, "", "Living room")
        joins.collect(secret).name                       # "Living room"
        joins.state(secret)                              # "expired": collected once
    """

    def __init__(self) -> None:
        self.clock = time.time
        self._by_secret: dict[str, Join] = {}
        self._wrong: dict[str, list[float]] = {}

    def ask(self, device: str) -> tuple[str, Join]:
        """A new request for the browser named `device`: its secret, which
        only that browser keeps, and the request with its code."""
        self._prune()
        secret = secrets.token_urlsafe(32)
        codes = {j.code for j in self._by_secret.values()}
        code = _new_join_code()
        while code in codes:
            code = _new_join_code()
        self._by_secret[secret] = Join(code, device, self.clock())
        return secret, self._by_secret[secret]

    def mine(self, secret: str) -> Join | None:
        """The request `secret` belongs to while it still waits, else None."""
        found = self._by_secret.get(secret)
        if found is None or found.allowed or self._expired(found):
            return None
        return found

    def waiting(self, typed: str) -> Join | None:
        """The waiting request whose code is `typed`, as a person typed it
        ("k7mp-4xqd", "K7MP 4XQD"); None for a code that does not wait."""
        code = _typed(typed)
        return next((j for j in self._by_secret.values()
                     if j.code == code and not j.allowed and not self._expired(j)), None)

    def all_waiting(self) -> list[Join]:
        """Every request that still waits, the oldest first: the Mac's
        Devices page lists them."""
        return [j for j in self._by_secret.values() if not j.allowed and not self._expired(j)]

    def allow(self, code: str, person: str, name: str, by: str = "") -> Join | None:
        """Allow the waiting request `code` for `person` (a user id, or ""
        for the household) as the device `name`, by the device `by` (its
        id, or "mac"). Returns the request, or None when the code does
        not wait. Makes no key: the waiting browser collects the
        allowance itself (`collect`)."""
        found = self.waiting(code)
        if found is not None:
            found.allowed, found.person, found.name, found.by = True, person, name, by
        return found

    def decline(self, code: str) -> bool:
        """End the waiting request `code` ("Not me", "Not now"); False when
        it does not wait."""
        found = self.waiting(code)
        if found is None:
            return False
        self._by_secret = {s: j for s, j in self._by_secret.items() if j is not found}
        return True

    def state(self, secret: str) -> str:
        """What the waiting browser's page shows: "waiting", "allowed", or
        "expired" (also for an unknown or collected secret)."""
        found = self._by_secret.get(secret)
        if found is None or self._expired(found):
            return "expired"
        return "allowed" if found.allowed else "waiting"

    def collect(self, secret: str) -> Join | None:
        """The allowed request `secret` belongs to, once, with its person
        and name; None while it waits, or when it ended. The request is
        gone after."""
        found = self._by_secret.get(secret)
        if found is None or not found.allowed or self._expired(found):
            return None
        del self._by_secret[secret]
        return found

    def give_back(self, secret: str, join: Join) -> None:
        """Put a collected request back: the Mac did not add the device, so
        the next page of the waiting browser tries again."""
        self._by_secret[secret] = join

    def wrong(self, approver: str) -> None:
        """Count one code from `approver` (a device id, or "mac") that was
        not waiting."""
        self._wrong.setdefault(approver, []).append(self.clock())

    def too_many_wrong(self, approver: str) -> bool:
        """Whether `approver` sent ten codes that were not waiting in the
        last minute: then it waits a minute."""
        now = self.clock()
        recent = [t for t in self._wrong.get(approver, []) if now - t < _WRONG_SECS]
        self._wrong[approver] = recent
        return len(recent) >= _WRONG_MOST

    def forget(self) -> None:
        """Drop every request, as a restart does."""
        self._by_secret.clear()
        self._wrong.clear()

    def _expired(self, join: Join) -> bool:
        return self.clock() - join.asked >= CODE_SECS

    def _prune(self) -> None:
        self._by_secret = {s: j for s, j in self._by_secret.items() if not self._expired(j)}
        while len(self._by_secret) >= _KEPT_MOST:
            del self._by_secret[next(iter(self._by_secret))]


JOINS = Joins()


def shown(code: str) -> str:
    """A join code as the page shows it, in two groups of four.

        shown("K7MP4XQD")   # "K7MP-4XQD"
    """
    return f"{code[:4]}-{code[4:]}"


# ── One code ─────────────────────────────────────────────────────────────

@dataclass
class _Pairing:
    """What one code holds: for whom, when it was made, whether a phone
    opened it, and the name of the device it paired."""

    person: str
    made: float
    taken: bool = False
    device: str = ""


# ── One join code ────────────────────────────────────────────────────────

def _new_join_code() -> str:
    return "".join(secrets.choice(_JOIN_SYMBOLS) for _ in range(_JOIN_LENGTH))


def _typed(typed: str) -> str:
    """A code as a person typed it, in the form the requests keep."""
    return "".join(c for c in typed.upper() if c.isalnum())
