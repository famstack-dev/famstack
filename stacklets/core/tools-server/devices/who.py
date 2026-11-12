"""Who a request is from: a person's device, the household's, the Mac, or
nobody.

    who(request)                        # Who(kind="person", person="homer", device=...)
    who(request).here                   # False: not the Mac's own address
    who(request).person_of(family)      # Homer's entry in the family
    who(request).sees_password_of("homer")

The gate (`devices.gate`) finds it once per request, from the listener and
the device key or the admin key, and leaves it on the request; every route asks it here
instead of reading a header again. The pages take this one value and
ask it, never a flag beside it for the Mac, a shared device or the
person. Kept apart from the gate so the parts
of the package that act for a device (joining, the pages) can ask it
without importing the gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

import family_view
from .store import Device

__all__ = ["Who", "who", "MAC", "NOBODY"]


@dataclass(frozen=True)
class Who:
    """Who a request is from. `kind` is "person" (`person` names them),
    "household" (a shared device), "mac" (the Mac's own address) or
    "nobody". `device` is the paired device, None for the Mac and nobody.

        Who("person", "homer", device).sees_password_of("homer")   # True
        Who("household", "", device).sees_password_of("homer")     # False
    """

    kind: str
    person: str = ""
    device: Device | None = None

    @property
    def here(self) -> bool:
        """Whether the request came through the Mac's own address, where
        every page and every change opens: `MAC.here` is True."""
        return self.kind == "mac"

    def person_of(self, family: dict | None) -> dict | None:
        """Whose sign-in the pages show, as an entry of `family` (what
        `stack family` shows): the person of a device paired as a person;
        the family's admin on the Mac's address, who sets the stack up
        there; None on a shared device, for nobody, and for a person no
        longer in the family.

            Who("person", "lisa").person_of(SIMPSONS)["id"]   # "lisa"
            MAC.person_of(SIMPSONS)["id"]                     # "homer", the admin
            Who("household").person_of(SIMPSONS)              # None
        """
        if self.kind == "mac":
            return family_view.admin_of(family)
        if self.kind == "person":
            return next((p for p in (family or {}).get("people", [])
                         if p["id"] == self.person), None)
        return None

    def sees_password_of(self, person: str) -> bool:
        """Whether a page for this request may show `person`'s own
        password: only on a device paired as that person."""
        # SAFETY: a person's password shows on their own paired device and
        # nowhere else on the home address; every person's together stays
        # on the Mac's pages.
        return self.kind == "person" and bool(person) and self.person == person


MAC = Who("mac")
NOBODY = Who("nobody")


def who(request: Request) -> Who:
    """Who `request` is from, as the gate found it: the Mac for the Mac's
    address with the admin key, the paired device's person or the
    household on the home address, nobody otherwise. Reads what the gate
    left on the request, never a header.

        who(request)   # through 127.0.0.1:42002 with the admin key: Who(kind="mac")
    """
    # SAFETY: only the gate makes `MAC`, from the listener and the admin
    # key (`admin_key.door`); a request it did not see is nobody.
    found = getattr(request.state, "who", None)
    return found if found is not None else NOBODY
