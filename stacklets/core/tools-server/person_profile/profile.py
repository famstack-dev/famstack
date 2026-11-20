"""One person's profile as one viewer may see it, and the panel's JSON of it.

    found = await profile_of(request, "homer", language="en")   # None: nobody of that id
    answer(found)       # what GET /dashboard/api/person/homer answers

    # On Marge's phone:
    # {"id": "homer", "name": "Homer", "relation": "Partner", "admin": true,
    #  "memory": "/memory/person/homer", "profile": "/dashboard/person/homer",
    #  "own": false, "devices": null, "waiting": null}

The page (`person_profile.page`) and the panel's JSON both start from the
`Profile` this module gathers, and the profile holds only what the
viewer may see (`person_profile.sees`): devices the viewer may not see are
not even asked for, and the password is asked for only on the person's
own device (`devices.sign_in.own_password`). So neither the page nor the
JSON can show more than the rule allows. The JSON never carries a
password: the panel shows none.

The family and the devices come from the host API (`stack family`,
`stack devices list`), the waiting browsers from this process's join
requests (`devices.joining.waiting_list`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from fastapi import Request

import family_view
from access import DASHBOARD_PREFIX
from devices import host, joining
from devices.sign_in import own_password
from devices.who import who
from stack import clock

from .sees import Sees, sees

__all__ = ["STALE_DAYS", "Profile", "profile_of", "answer", "address"]

# A device not seen for this many days or more reads "not seen since".
STALE_DAYS = 30


@dataclass(frozen=True)
class Profile:
    """One person's profile for one viewer. `person` is the family entry
    (`id`, `name`, `relation`, `words`, `role`, `email`), `relation` the
    word for them as this viewer says it ("partner"), `sees` what this
    viewer sees. `devices` are the person's devices as `stack devices
    list` shows them, each with `stale` (not seen for `STALE_DAYS`); []
    when the viewer may not see them, as `waiting` (the browsers that
    wait to join) is. `this` is the id of the device that looks, "" on the
    Mac and a shared device. `password` is the person's own password, on
    their own device only.

        Profile(person=HOMER, relation="partner", sees=..., today=date(2026, 11, 20))
    """

    person: dict
    relation: str
    sees: Sees
    today: date
    devices: list[dict] = field(default_factory=list)
    waiting: list[dict] = field(default_factory=list)
    this: str = ""
    password: str | None = None

    @property
    def id(self) -> str:
        """The person's id, as the address and the JSON name it: "homer"."""
        return self.person["id"]


async def profile_of(request: Request, person_id: str, *, language: str) -> Profile | None:
    """The profile of `person_id` for the viewer of `request`; None when
    the family has nobody of that id (or does not answer). Asks the host
    API for the family, and for the devices and the password only when
    the viewer may see them.

        await profile_of(request, "marge", language="en")   # on Homer's phone: her devices, no password
    """
    family = await host.family()
    person = host.person_of(family, person_id)
    if person is None:
        return None
    looking = who(request)
    by = await joining.approver(request)
    shown = sees(looking, person_id, admin=bool(by and by.admin))
    viewer = looking.person if looking.kind == "person" else ""
    today = clock.today()
    return Profile(
        person=person,
        relation=family_view.kin(viewer, person, (family or {}).get("people", []), language),
        sees=shown, today=today,
        devices=await _devices_of(person_id, today) if shown.devices else [],
        waiting=joining.waiting_list() if shown.waiting else [],
        this=looking.device.id if looking.device else "",
        password=await own_password(looking, person) if shown.password else None)


def answer(profile: Profile) -> dict:
    """The panel's JSON of `profile`: the header block, the devices
    (`null` where the viewer may not see them) and how many browsers wait
    (`null` for a viewer who may not allow one). No password, ever.

        answer(homers_profile_on_marges_phone)["devices"]   # None
    """
    shown = profile.sees
    return {"id": profile.id, "name": profile.person.get("name", ""),
            "relation": profile.relation[:1].upper() + profile.relation[1:],
            "admin": profile.person.get("role") == "admin",
            "memory": f"/memory/person/{profile.id}", "profile": address(profile.id),
            "own": shown.own,
            "devices": ([{"id": d["id"], "name": d["name"], "seen": d["seen"], "stale": d["stale"],
                          "this": d["id"] == profile.this} for d in profile.devices]
                        if shown.devices else None),
            "waiting": len(profile.waiting) if shown.waiting else None}


def address(person_id: str) -> str:
    """The stable address of a person's profile: `/dashboard/person/homer`."""
    return f"{DASHBOARD_PREFIX}/person/{person_id}"


# ── The devices ──────────────────────────────────────────────────────────

async def _devices_of(person_id: str, today: date) -> list[dict]:
    """The devices of `person_id` from `stack devices list`, oldest first,
    each with `stale`."""
    listed = await host.call({"cmd": "devices"})
    return [{**d, "stale": _stale(d.get("seen", ""), today)}
            for d in listed.get("devices", []) if d.get("person") == person_id]


def _stale(seen: str, today: date) -> bool:
    try:
        return (today - date.fromisoformat(seen)).days >= STALE_DAYS
    except ValueError:
        return False
