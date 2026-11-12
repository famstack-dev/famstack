"""The sign-in a page shows, and the top bar that names who looks.

    person = who.person_of(family)                    # whose sign-in
    password = await own_password(who, person)        # "otter-maple-cloud-lamp" or None
    looked = looking(who, family, "en")               # the top bar
    looked.name, looked.greeting, looked.menu         # "Homer Simpson", "Homer", [...]
    Response(body, headers=NO_STORE)

The phone page, the sign-in guide, the setup page and the dashboard all
show a sign-in, and the dashboard names who looks in its top bar. Each
answer starts from the request's viewer (`devices.who`); this module
holds what follows from it, so the server's routes ask one place instead
of taking the viewer apart again:

    viewer            sign-in shown          password shown      top bar
    Homer's phone     Homer's                his own, here only  "Homer", his links
    shared device     nobody's               none                the device's name
    the Mac           the admin's            the setup's pages   nothing
"""

from __future__ import annotations

from dataclasses import dataclass, field

import family_view
from access import DASHBOARD_PREFIX

from . import host, pages
from .who import Who

__all__ = ["NO_STORE", "Looking", "looking", "passwords", "own_password"]

# SAFETY: every answer that can carry a password (the phone page, the
# sign-in guide, the setup page, the dashboard and its pills' JSON) is sent
# with `no-store`, so no browser or proxy keeps a copy of it on disk or for
# the back button.
NO_STORE = {"Cache-Control": "no-store"}


# ── The top bar ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Looking:
    """Who the dashboard names as looking: the top bar's `name`, the first
    name the welcome greets (`greeting`) and the (label, link) pairs of the
    top bar's `menu`.

        Looking("Homer Simpson", "Homer", [("Your devices", "/dashboard/devices"), ...])
    """

    name: str = ""
    greeting: str = ""
    menu: list[tuple[str, str]] = field(default_factory=list)


def looking(who: Who, family: dict | None, language: str) -> Looking:
    """The top bar and the greeting for `who`: a device paired as a person
    is named and greeted as that person and gets "Your devices" and "Add a
    device"; a shared device shows its own name ("Shared" when it has
    none); the Mac's address and nobody get nothing. The links come
    with the device, so they stay while the family does not answer or no
    longer has the person; the name and the greeting then stay empty.

        looking(Who("person", "homer"), SIMPSONS, "en")
        # Looking("Homer Simpson", "Homer", [("Your devices", "/dashboard/devices"),
        #                                    ("Add a device", "/dashboard/devices/add")])
        looking(MAC, SIMPSONS, "en")      # Looking()
    """
    w = pages.words(language)
    if who.kind == "household":
        return Looking(who.device.name if who.device else w["shared"])
    if who.kind != "person":
        return Looking()
    name = (who.person_of(family) or {}).get("name", "")
    return Looking(name, family_view.first_name(name),
                   [(w["your_devices"], f"{DASHBOARD_PREFIX}/devices"),
                    (w["add_device_heading"], f"{DASHBOARD_PREFIX}/devices/add")])


# ── Passwords ─────────────────────────────────────────────────────────────

async def passwords(people: list[str]) -> dict[str, str]:
    """The stored password of each of `people`, by id, from the host
    API's `passwords` (`stack.passwords.of_people`); {} when it does not
    answer. A person without one is left out.

        await passwords(["homer"])     # {"homer": "otter-maple-cloud-lamp"}

    SAFETY: the one way the tools server gets a person's password, and it
    has no other: the secrets stay on the Mac. Ask only for the people
    the page may show (every person on the Mac's address, a paired
    device's own person, no one on a shared device), and never log or
    keep the answer beyond the page it is for."""
    if not people:
        return {}
    found = (await host.call({"cmd": "passwords", "people": people})).get("passwords")
    return found if isinstance(found, dict) else {}


async def own_password(who: Who, person: dict | None) -> str | None:
    """The password a page on the home address may show: `person`'s own
    stored one, on their own paired device; None anywhere else, the Mac's
    address (whose setup page shows it) and a shared device included.

        await own_password(Who("person", "homer"), homer)   # "otter-maple-cloud-lamp"
        await own_password(MAC, homer)                      # None
    """
    if person and who.sees_password_of(person["id"]):
        return (await passwords([person["id"]])).get(person["id"])
    return None
