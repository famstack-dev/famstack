"""What the device package asks the stack, and how it answers a browser.

    await host.call({"cmd": "devices"})   # {"devices": [...]}, or {} when it did not answer
    await host.family()                   # what `stack family` shows, None when unknown
    host.html(body, status=401)

The host API (`stack.live.client`) is the only way the tools server reads
or changes the list of devices and the family: the list has one writer,
`stack devices` on the Mac (ADR-023 D9). One module, so the gate, the
pairing routes and the join routes ask the same way.
"""

from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import Response

import dashboard_page
from stack.live import client

__all__ = ["call", "family", "person_of", "language_of", "html", "HOST_API_SECS"]

# How long a call to the host API may take.
HOST_API_SECS = 30


async def call(request: dict) -> dict:
    """One request to the host API; {} when it did not answer as JSON."""
    answer = await asyncio.to_thread(client.call, request, timeout=HOST_API_SECS)
    return answer if isinstance(answer, dict) else {}


async def family() -> dict | None:
    """The family as `stack family` shows it; None when the host API did
    not answer with one."""
    answer = await call({"cmd": "family"})
    return answer if "people" in answer else None


def person_of(family: dict | None, person_id: str) -> dict | None:
    """The person of `family` with the id `person_id`, None for nobody.

        person_of({"people": [{"id": "homer", "name": "Homer"}]}, "homer")["name"]   # "Homer"
    """
    return next((p for p in (family or {}).get("people", []) if p["id"] == person_id), None)


def language_of(request: Request) -> str:
    """The language a page answers `request` in: "de" or "en"."""
    return dashboard_page.language_of(request.headers.get("accept-language", ""))


def html(body: str, *, status: int = 200) -> Response:
    """`body` as an HTML answer with `status`."""
    return Response(body, status_code=status, media_type="text/html; charset=utf-8")

