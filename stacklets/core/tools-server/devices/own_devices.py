"""Whose devices a request may remove, and the old address of "Your devices".

    own_devices.routes(app)                        # from devices.routes
    own_devices.owns(who(request), "d-3f9a2c1b")   # True for Homer's iPhone on Homer's phone
    await own_devices.may_remove(request, "d-3f9a2c1b")

    GET simpson.local:42000/dashboard/devices   303 to /dashboard/me#devices

A lost phone is removed, not a password changed (design, "Removing a
device"), and the person who lost it may not be near the Mac. So each
person removes their own devices on any of them, from their profile
(`person_profile`), and an admin's device removes any person's from that
person's profile, as the Mac's Devices page does; the key stops counting
at once, because the gate reads the list on every request. A shared
device removes none, and no device but the Mac removes a shared one.

Ownership is read from the list the gate reads (`store.current()`),
which `stack devices` writes; the removal itself goes through the host
API to that one writer.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from access import DASHBOARD_PREFIX

from . import joining, store
from .who import Who

__all__ = ["YOUR_DEVICES", "owns", "may_remove", "routes"]

# The old page "Your devices", on the home address; the devices are now
# on one's own profile.
YOUR_DEVICES = f"{DASHBOARD_PREFIX}/devices"


def owns(looking: Who, device_id: str) -> bool:
    """Whether `device_id` is a device of the person `looking` is from.
    False for a shared device, the Mac and nobody: they own none.

        owns(Who("person", "homer", device), "d-3f9a2c1b")   # True for Homer's iPhone
    """
    if looking.kind != "person":
        return False
    return any(d.id == device_id and d.person == looking.person for d in store.current().all())


async def may_remove(request: Request, device_id: str) -> bool:
    """Whether `request` may remove the device `device_id`: the Mac any;
    a person's device its own; an admin's device any person's. False for
    a shared device, nobody, and a device that is not in the list.

        await may_remove(request, "d-3f9a2c1b")   # on Bart's phone, Homer's iPhone: False
    """
    by = await joining.approver(request)
    if by is None:
        return False
    if by.who.kind == "mac" or owns(by.who, device_id):
        return True
    # SAFETY: an admin's phone sees every person's devices on their
    # profiles and removes them there; a shared device (no person) is
    # removed only on the Mac's Devices page.
    return by.admin and any(d.id == device_id and not d.shared for d in store.current().all())


def routes(app: FastAPI) -> None:
    """Add the old address of "Your devices" to `app`: it sends every
    device to its own profile's devices.

        GET /dashboard/devices   303 to /dashboard/me#devices
    """

    @app.get(YOUR_DEVICES, include_in_schema=False)
    async def your_devices():
        return RedirectResponse(f"{DASHBOARD_PREFIX}/me#devices", status_code=303)
