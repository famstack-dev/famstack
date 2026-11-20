"""The words of the dashboard's pill panel, added to the profile's JSON.

    panel(found, language="en")     # what GET /dashboard/api/person/homer answers

    # On Marge's phone:
    # {"id": "homer", "name": "Homer Simpson", ..., "devices": null, "waiting": null,
    #  "said": {"admin": "Looks after famstack", "memory": "Homer's memory",
    #           "heading": null, "empty": null,
    #           "why": "Homer's devices show on Homer's own phone.",
    #           "waiting": null, "route": null, "this_device": "This device", "close": "Close"}}

A click on a family pill opens a short, read-only profile under the pills
(`dashboard/components/pill-panel.js`). The script draws only what this
answer holds. So the answer carries the panel's words, already said for
this viewer and this person, next to the facts of `profile.answer`: the
German genitive, the relative days and the line why stay on the server,
in the same words as the page (`person_profile.words`).

A word is null where its part is not shown: no device words where the
viewer may not see the devices (`sees.devices`), no waiting line unless
a browser waits and the viewer may allow it, and the link "Settings and
devices" only where the page holds more for this viewer than the panel
(`sees.settings`: one's own profile, an admin's device, the Mac). Each
listed device gets `label`, its name as the page says it, and `when`,
its seen line as its row on the page says it.
Pure: no request, no I/O.
"""

from __future__ import annotations

import family_view
from devices import names, pages

from .profile import Profile, answer
from .words import genitive, seen, words

__all__ = ["panel"]


def panel(profile: Profile, *, language: str) -> dict:
    """`profile.answer(profile)` with the panel's words (`said`) and each
    listed device's name and seen line as the page says them (`label`,
    `when`), in `language`.

        panel(lisas_profile_on_homers_phone, language="en")["said"]["waiting"]
        # "1 device waits to join"
    """
    read = answer(profile)
    if read["devices"] is not None:
        by_id = {d["id"]: d for d in profile.devices or []}
        read["devices"] = [{**d, "label": names.said(d["name"], language),
                            "when": seen(by_id[d["id"]], this=profile.this, today=profile.today,
                                         language=language)} for d in read["devices"]]
    read["said"] = _said(profile, language)
    return read


# ── The words ────────────────────────────────────────────────────────────

def _said(profile: Profile, language: str) -> dict:
    """The panel's words for this viewer of `profile`; None for a part the
    panel does not show."""
    w, d, shown = words(language), pages.words(language), profile.sees
    first = family_view.first_name(profile.person.get("name", ""))
    names = {"name": first, "genitive": genitive(first)}
    devices = shown.devices and profile.devices is not None
    return {
        "admin": w["admin"] if profile.person.get("role") == "admin" else None,
        "memory": w["memory_own"] if shown.own else w["memory_other"].format(**names),
        "heading": (d["your_devices"] if shown.own else w["devices_other"].format(**names))
                   if devices else None,
        "empty": (w["empty_own"] if shown.own else w["empty_other"].format(**names))
                 if devices else None,
        "why": _why(shown.why, names, w),
        "waiting": _waiting(len(profile.waiting), w) if shown.waiting else None,
        "route": w["panel_route"] if shown.settings else None,
        "this_device": d["this_device"],
        "close": w["panel_close"],
    }


def _why(why: str, names: dict, w: dict) -> str | None:
    """The line in place of the devices: whose phone shows them."""
    if why == "person":
        return w["panel_why_person"].format(**names)
    if why == "shared":
        return w["panel_why_shared"]
    return None


def _waiting(count: int, w: dict) -> str | None:
    """"1 device waits to join", "2 devices wait to join"; None for none."""
    if count < 1:
        return None
    return w["panel_waiting_one"] if count == 1 else w["panel_waiting_many"].format(count=count)
