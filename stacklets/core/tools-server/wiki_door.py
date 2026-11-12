"""wiki_door.py: core at the family wiki's door, which opens it only for
paired devices.

    app.middleware("http")(wiki_door.forward)   # before devices.gate (server.py)
    GET simpson.local:42070/people/homer        # Homer's phone: the wiki's page
    GET simpson.local:42070/people/homer        # a guest: "This browser does not know you yet"

The wiki (Quartz, the container `stack-memory-wiki`) shows the text of
every document and diary entry, and had no login. It keeps its address:
port 42070 in port mode, `memory.<domain>` and `wiki.<domain>` in domain
mode. Core answers there with a listener of its own (`access.WIKI_PORT`),
and the wiki container publishes no port any more.

    browser ──▶ :42070 ──▶ devices.gate ──▶ forward ──▶ stack-memory-wiki:8080
                           no key: the       a paired     the same path and
                           unknown page      device       query, at the root

The device key reaches port 42070 because a browser sends a host's cookies
to every port of it (RFC 6265, section 8.5); in domain mode the key's
cookie names the family's domain (`browser_checks.cookie_flags`), so the
wiki's names get it too. The wiki is forwarded at the root, so Quartz
needs no base path and its links, search and popovers work as they did.

Why a door in core and not `/wiki` on the home address: Quartz makes its
links from its root, and every one of them would need rewriting; links
to the wiki in the chat would break. ADR-023 D11.

What it does not do: decide who the browser is (the gate does, and this
module only asks `devices.who`), or give rights per person inside the
wiki. Core keeps the dashboard's paths on this listener, because the
unknown page loads its styles and its join state from there.
"""

from __future__ import annotations

import os

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

import devices
from access import DASHBOARD_PREFIX, WIKI_PORT

__all__ = ["forward", "WIKI_UPSTREAM"]

# The wiki container on the stack's network, at Quartz's own port. A test
# points `WIKI_UPSTREAM` at a stand-in on localhost.
WIKI_UPSTREAM = "http://stack-memory-wiki:8080"
# Who may open the wiki: a person's device or a shared one.
_PAIRED = ("person", "household")
# What the wiki gets from the browser's request. Never the cookies: they
# hold the device key, which stays between the browser and core.
_REQUEST_HEADERS = ("accept", "accept-language", "if-none-match", "if-modified-since", "range",
                    "user-agent")
# What the browser gets of the wiki's answer. Not the hop-by-hop headers
# of the connection between core and the wiki.
_HOP_BY_HOP = frozenset({"connection", "keep-alive", "transfer-encoding", "te", "trailer",
                         "upgrade", "proxy-authenticate", "proxy-authorization"})
# The wiki's pages may not be framed by another site. Core's own policy
# (`browser_checks.POLICY`) runs only core's scripts, and Quartz's pages
# carry their own, so they get this one instead of core's.
_WIKI_POLICY = "frame-ancestors 'none'"
_TIMEOUT_SECS = 30


async def forward(request: Request, call_next):
    """The middleware of the wiki's door: on the wiki's listener, a
    paired device's request goes on to the wiki at the same path and
    query, and the wiki's answer comes back as it is; any other listener,
    and the dashboard's paths, go on to core's routes.

    Runs after `devices.gate`, which has already answered a browser
    without a key with the unknown page. Expects `WIKI_UPSTREAM` in the
    environment, or the wiki container's address. Answers 502 when the
    wiki does not answer (memory is not up).

        GET simpson.local:42070/people/homer?from=chat   # 200, the wiki's page
        GET simpson.local:42070/dashboard/static/x.css   # core's own file
    """
    listener = (request.scope.get("server") or (None, None))[1]
    path = request.url.path
    if listener != WIKI_PORT or path == DASHBOARD_PREFIX or path.startswith(DASHBOARD_PREFIX + "/"):
        return await call_next(request)
    # SAFETY: the wiki shows every document and diary entry of the family.
    # It opens for a paired device only, whatever the gate did before.
    if devices.who(request).kind not in _PAIRED:
        return JSONResponse({"error": "This browser does not know you yet."}, status_code=401)
    return await _from_the_wiki(request)


# ── Forwarding ───────────────────────────────────────────────────────────

async def _from_the_wiki(request: Request) -> Response:
    """The wiki's answer to `request`, streamed, so a large photo of the
    media archive is not held in core's memory."""
    client = httpx.AsyncClient(timeout=_TIMEOUT_SECS)
    sent = client.build_request(
        request.method, _upstream() + request.url.path, params=request.url.query or None,
        headers={k: v for k, v in request.headers.items() if k in _REQUEST_HEADERS},
        content=await request.body())
    try:
        answer = await client.send(sent, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"error": "The wiki does not answer. Is memory up?"}, status_code=502)
    headers = {k: v for k, v in answer.headers.items() if k.lower() not in _HOP_BY_HOP}
    if answer.headers.get("content-type", "").startswith("text/html"):
        headers["content-security-policy"] = _WIKI_POLICY
    return StreamingResponse(answer.aiter_raw(), status_code=answer.status_code, headers=headers,
                             background=BackgroundTask(_close, answer, client))


async def _close(answer: httpx.Response, client: httpx.AsyncClient) -> None:
    await answer.aclose()
    await client.aclose()


def _upstream() -> str:
    """The wiki's address, read on each request like core's other settings."""
    return os.environ.get("WIKI_UPSTREAM", WIKI_UPSTREAM).rstrip("/")
