"""A person's own devices: "Your devices" on any of them.

    own_devices.routes(app)                    # from devices.routes
    own_devices.owns(who(request), "d-3f9a2c1b")

    GET simpson.local:42000/dashboard/devices   on Homer's laptop:
    # "Your devices: iPhone · added 10 November · seen today · Remove
    #                Chrome on a Mac · This device · added today · Remove
    #  Add a device"

A lost phone is removed, not a password changed (design, "Removing a
device"), and the person who lost it may not be near the Mac. So each
person sees their own devices on any of them, and removes one there; the
key stops counting at once, because the gate reads the list on every
request. A person sees only their own: the list names the family's
devices, and a child has no need to see a parent's. An admin's device
also offers "Add a phone" for the family's other people, as the Mac's
"Your family" does (ADR-023 D20).

A shared device has no person and no list of its own; the Mac's Devices
page lists every device. Ownership is read from the list the gate reads
(`store.current()`), which `stack devices` writes; the removal itself goes
through the host API to that one writer.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from access import DASHBOARD_PREFIX
from stack import clock

from . import host, joining, pages, store
from .who import Who, who

__all__ = ["YOUR_DEVICES", "owns", "routes"]

# The page "Your devices", on the home address.
YOUR_DEVICES = f"{DASHBOARD_PREFIX}/devices"


def owns(looking: Who, device_id: str) -> bool:
    """Whether `device_id` is a device of the person `looking` is from.
    False for a shared device, the Mac and nobody: they own none.

        owns(Who("person", "homer", device), "d-3f9a2c1b")   # True for Homer's iPhone
    """
    if looking.kind != "person":
        return False
    return any(d.id == device_id and d.person == looking.person for d in store.current().all())


def routes(app: FastAPI) -> None:
    """Add "Your devices" to `app`: on a person's paired device their own
    list; the Mac's address goes on to the Devices page; a shared device
    is refused.

        GET /dashboard/devices   "Your devices", "This device" marked, Remove, "Add a device"
    """

    @app.get(YOUR_DEVICES, include_in_schema=False)
    async def your_devices(request: Request):
        looking = who(request)
        language = host.language_of(request)
        if looking.kind == "mac":
            return RedirectResponse("/setup/devices", status_code=303)
        by = await joining.approver(request)
        if by is None:
            return host.html(pages.only_admin(language), status=403)
        listed, family = await host.call({"cmd": "devices"}), await host.family()
        mine = [d for d in listed.get("devices", []) if d.get("person") == looking.person]
        others = ([{"id": p["id"], "name": p["name"]} for p in (family or {}).get("people", [])
                   if p["id"] != looking.person] if by.admin else [])
        this = looking.device.id if looking.device else ""
        return host.html(pages.your_devices(mine, this=this, others=others, language=language,
                                            today=clock.today(), base=DASHBOARD_PREFIX))
