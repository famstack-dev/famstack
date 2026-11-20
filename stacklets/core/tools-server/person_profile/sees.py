"""What a viewer sees of one person's profile: the one rule for the page and its JSON.

    sees(who(request), "homer", admin=True)
    # Sees(own=True, devices=True, settings=True, password=True, waiting=True,
    #      this_mac=False, why="")

A profile shows a person's devices, their settings and their password to
some viewers and not to others. The page (`person_profile.page`) and the
dashboard panel's JSON (`person_profile.profile.answer`) both draw from
this one answer, so the panel can never show what the page hides:

    who looks                  own page                    another person's page
    an admin's phone           everything                  everything but the password
    a person's phone           settings, password, devices header block, why = "person"
    a shared device            (none)                      header block, why = "shared"
    the Mac's address          everything but the password everything but the password

The viewer is always `devices.who(request)`, which the gate found from
the listener and the device key. Nothing in the address or the query
takes part: a forged `?as=homer` reads the same page as no query. The
password follows `Who.sees_password_of`, the same rule as the sign-in
pages. Pure: no request, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from devices.who import Who

__all__ = ["Sees", "sees"]


@dataclass(frozen=True)
class Sees:
    """The parts of one profile that one viewer sees. `own` is True on the
    person's own paired device. `why` names the line that replaces the
    hidden parts: "person" (they show on that person's own phone),
    "shared" (they show on each person's own phone), or "" when nothing
    is hidden.

        Sees(own=False, devices=False, settings=False, password=False,
             waiting=False, this_mac=False, why="person")   # Marge on Homer's page
    """

    own: bool
    devices: bool
    settings: bool
    password: bool
    waiting: bool
    this_mac: bool
    why: str


def sees(looking: Who, person: str, *, admin: bool) -> Sees:
    """What `looking` sees of the profile of `person` (an id of the
    family). `admin` is True for an admin's paired device and for the
    Mac (`devices.joining.approver`): they see and remove every person's
    devices and allow waiting browsers. The devices, the Remove buttons
    and Settings go together; the password only to the person's own
    device; "On this Mac" only on the Mac.

        sees(Who("person", "marge"), "homer", admin=False).devices   # False
        sees(Who("person", "homer"), "marge", admin=True).password   # False
        sees(MAC, "homer", admin=True).this_mac                      # True
    """
    own = looking.kind == "person" and looking.person == person
    # SAFETY: another person's devices (and their Remove buttons) show
    # only to the person, an admin's device or the Mac; the password only
    # on the person's own device. A shared device is no person's.
    full = own or (admin and looking.kind in ("person", "mac"))
    if not full:
        return Sees(own=False, devices=False, settings=False, password=False, waiting=False,
                    this_mac=False, why="person" if looking.kind == "person" else "shared")
    return Sees(own=own, devices=True, settings=True,
                password=looking.sees_password_of(person),
                waiting=admin, this_mac=looking.kind == "mac", why="")
