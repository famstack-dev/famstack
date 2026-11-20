"""A person's profile: one page per person of the family, at a stable address.

    person_profile.routes(app)              # from server.py, after devices.routes

    GET  /dashboard/person/homer            Homer's profile, drawn for whoever looks
    GET  /dashboard/me                      303 to one's own profile; on the Mac, the page
                                            that opens the person picked in this browser
    GET  /dashboard/api/person/homer        the panel's JSON of the same profile
    GET  /dashboard/person/ned              404, "Nobody here by that name"

Each person has a profile on the home address and the Mac's address
alike: the header block, the browsers that wait to join, Settings, the
person's devices, and on the Mac "On this Mac". It is where the devices
live, so they no longer hide behind the Mac's setup step: the old
`/dashboard/devices` leads to `/dashboard/me#devices`, a chat answer or
the Server room's notice links straight to a section (`#devices`,
`#password`, `#waiting`, `#settings`), and the memory's person page
links to the profile and back.

What a profile shows depends only on who looks (`devices.who`), decided
once by `sees.sees` for the page and the JSON alike:

    Marge's phone on /dashboard/person/homer
        header block, "Homer's devices and password show on Homer's own phone."
    Homer's phone (an admin) on /dashboard/person/marge
        waiting browsers (Allow as Marge), Settings without a password,
        Marge's devices with Remove, "Add Marge's phone"

Module map: `sees.py` is the rule, `profile.py` gathers what the rule
allows and shapes the JSON, `page.py` draws the page, `words.py` holds
its labels. Writes (Allow, Dismiss, Remove, Not me) go to the device
routes (`devices.joining`, `devices.own_devices`), which check the
viewer again.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from access import DASHBOARD_PREFIX
from devices import host
from devices.sign_in import NO_STORE
from devices.who import who

from . import page as drawing
from .profile import address, answer, profile_of

__all__ = ["routes"]


def routes(app: FastAPI) -> None:
    """Add the profile's page, `/dashboard/me` and the panel's JSON to
    `app`. Every answer is `no-store`: one's own page carries the password.

        GET /dashboard/person/homer      on Marge's phone: the header block and the line why
    """

    @app.get(f"{DASHBOARD_PREFIX}/person/{{person_id}}", include_in_schema=False)
    async def profile_page(request: Request, person_id: str):
        language = host.language_of(request)
        found = await profile_of(request, person_id, language=language)
        if found is None:
            return _html(drawing.not_found(language), status=404)
        return _html(drawing.page(found, language=language))

    @app.get(f"{DASHBOARD_PREFIX}/me", include_in_schema=False)
    async def own_profile(request: Request):
        looking = who(request)
        if looking.kind == "mac":
            return _html(drawing.me_on_the_mac(host.language_of(request)))
        if looking.kind == "person":
            return RedirectResponse(address(looking.person), status_code=303)
        return RedirectResponse(DASHBOARD_PREFIX, status_code=303)

    @app.get(f"{DASHBOARD_PREFIX}/api/person/{{person_id}}", include_in_schema=False)
    async def profile_answer(request: Request, person_id: str):
        found = await profile_of(request, person_id, language=host.language_of(request))
        if found is None:
            return JSONResponse({"error": f"Nobody of the family is called {person_id}."},
                                status_code=404, headers=NO_STORE)
        return JSONResponse(answer(found), headers=NO_STORE)


def _html(body: str, *, status: int = 200) -> Response:
    return Response(body, status_code=status, media_type="text/html; charset=utf-8",
                    headers=NO_STORE)
