"""Tools HTTP server — OpenAPI-compatible endpoints for Open WebUI.

Bridges the famstack ecosystem to LLM tool calling. Open WebUI discovers
these endpoints via the OpenAPI spec and exposes them as tools the AI
can invoke during conversations.

Each tool wraps an existing service API (Paperless, Immich) or the
famstack TCP API for host-side CLI control. No business logic here —
just translation between what the LLM needs and what the services provide.

It listens three times (see access.py): an internal port for the stack's
own containers, the home address, which serves the persistent links, the
dashboard, the setup and the card pages to read, and the Mac's address on
this Mac, which serves everything, the host's path for writes to the
memory included. Whether a page may show what is for the Mac alone is
`access.here`, read from the listener and never from a header.
"""

import asyncio
import contextlib
import json
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

import activity_widget
import browser_checks
import cards_page as cards_page_module
import dashboard_page
import devices
import family_view
import first_words_step
import live_routes
import memory_ui
import phone_page
import phone_reach
import service_card
import sign_in_guide
import setup_page
import status_pills
import wiki_door
from access import (CARD_PREFIX, CARDS_PORT, DASHBOARD_PREFIX, INTERNAL_PORT, PUBLIC_PORT, SETUP_PATH,
                    WIKI_PORT, OnlyOnTheMac, here, mac_only, serves)
from memory_ui.addresses import card_address
from resolver import build_redirect
from stack.cards import is_card_id
from stack.cards.lookup import card_id_at, find_card, find_document, find_list
from stack.live import client


# ── Config from environment ──────────────────────────────────────────────
# All injected via docker-compose env_file from core's .env

# Paperless — container-to-container on stack network
PAPERLESS_URL = os.environ.get("PAPERLESS_URL", "")
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_TOKEN", "")

# Immich — container-to-container on stack network
IMMICH_URL = os.environ.get("IMMICH_URL", "")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")

# ── Link resolver — public bases for the /<prefix>/ persistent links ──────
# These are the URLs a *browser* uses, not the container-internal ones above.
# `_public_url` already makes them mode-correct (domain → host.domain, port →
# ip:port). The wiki is the one exception: it serves at the vanity `wiki.`
# subdomain in domain mode while its stacklet id is `memory`, so we swap the
# host. In port mode the URL is ip:port with no subdomain and the swap is a
# no-op. (The mismatch between the memory stacklet id and its `wiki.` route is
# pre-existing — noted, not fixed here.)
DOCS_PUBLIC_URL = os.environ.get("PAPERLESS_PUBLIC_URL", "")
WIKI_PUBLIC_URL = os.environ.get("MEMORY_PUBLIC_URL", "").replace(
    "://memory.", "://wiki.", 1)
LINK_SHARED_BUCKET = os.environ.get("SHARED_BUCKET", "family")
# The one knob: the path namespace persistent links live under (home.tld/go/…).
LINK_PREFIX = os.environ.get("LINK_PREFIX", "go").strip("/")
# The brain projection, read-only: the same tree the wiki serves. Only the
# links by another id than the card's need it (`/go/capture/<id>`,
# `/go/diary/<id>`, `/go/docs/<n>`, `/go/list/<name>`), to find which card a
# link names now. This is the mount point, not
# `BRAIN_REPO_DIR`: that variable is the path the *bot-runner* sees, and
# pointing this at it would name a directory that does not exist in this
# container. Unmounted or empty (memory not installed), a card link finds
# no card, and its page says the memory is not set up yet.
BRAIN_DIR = Path("/brain")
# The memory repo, read for its history: when a card no file carries any
# more was removed (`memory_ui.Reads.removal`). Unmounted, its page is a
# plain 404.
VAULT_DIR = Path("/vault")



@contextlib.asynccontextmanager
async def _lifespan(_app):
    """For the life of the process: follow every run on the Mac (live_routes)."""
    with live_routes.watching():
        yield


app = FastAPI(
    lifespan=_lifespan,
    title="famstack Tools",
    description="Family server tools for AI assistants — search documents, find photos, check server status.",
    version="0.2.1",
)


# Jobs a screen follows, and the event stream it follows them on (ADR-020).
app.include_router(live_routes.router)


# The wiki's door (`wiki_door`): registered before the gate, so it runs
# after it and forwards only a paired device's request to the wiki.
app.middleware("http")(wiki_door.forward)
# The home address opens only for paired devices (`devices`): registered
# before the listener rule below, so it runs after it, and a path the home
# address does not serve stays a plain 404.
app.middleware("http")(devices.gate)
# The Mac's pairing pages: "Add a phone", Devices, this browser (ADR-023 D12).
devices.routes(app, phone_link=lambda: _phone_link())


@app.middleware("http")
async def links_only_from_the_network(request, call_next):
    """Answer the published port with the links and the dashboard. A path
    that is not served there gets the same 404 as one that does not exist,
    so the network cannot tell which endpoints the server has. The listener
    a request arrived on is in the ASGI scope; without one it counts as the
    network."""
    listener = (request.scope.get("server") or (None, None))[1]
    if not serves(listener, request.url.path, LINK_PREFIX):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


@app.exception_handler(OnlyOnTheMac)
async def only_on_the_mac(request: Request, _refused: OnlyOnTheMac):
    """Answer a change through the home address (`access.mac_only`) with
    403 and where to go: the page "This happens on the Mac that runs
    famstack" for a browser that opens it, the same words as JSON for a
    page's script that posts. Nothing ran: the route never started."""
    language = _language(request)
    if request.method in _READS:
        return Response(setup_page.only_on_the_mac(language, base=DASHBOARD_PREFIX),
                        status_code=403, media_type="text/html; charset=utf-8")
    said = setup_page.words(language)["only_on_the_mac"]
    return JSONResponse({"error": f'{said["title"]}. {said["line"]}', "mac": setup_page.MAC_SETUP},
                        status_code=403)


# The dashboard's writes take a body only when it is sent as JSON. A page
# from any site, opened in a browser on the Mac, could post to the Mac's
# address without a CORS preflight: a string goes out as text/plain, a
# Blob without a type with no Content-Type, and Starlette's `request.json()` reads either. Sent as JSON, the browser asks
# first, and this server answers no preflight, so the post never leaves it.
_READS = ("GET", "HEAD", "OPTIONS")


def _sent_as_json(content_type: str) -> bool:
    """Whether a Content-Type says JSON: `application/json` or a `+json`
    type, with or without a charset."""
    kind = content_type.split(";", 1)[0].strip().lower()
    return kind == "application/json" or (kind.startswith("application/") and kind.endswith("+json"))


@app.middleware("http")
async def dashboard_writes_only_as_json(request, call_next):
    """Refuse a write to the dashboard whose body is not sent as JSON, with
    415, before a route reads it or asks the host API anything."""
    if (request.method not in _READS and request.url.path.startswith(f"{DASHBOARD_PREFIX}/")
            and not _sent_as_json(request.headers.get("content-type", ""))):
        return JSONResponse({"error": "Send the body as JSON, with Content-Type: application/json."},
                            status_code=415)
    return await call_next(request)


# Added last, so it runs first: a request by a name the listener does not
# answer to, or a write from another site's page, is refused before the
# checks above or any route run; and every answer, theirs included, gets
# the security headers (`browser_checks.py`).
app.add_middleware(browser_checks.BrowserChecks, home_url=lambda: os.environ.get("LINK_BASE_URL", ""))


def _error(msg: str, status: int = 503) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


# ── Stack API (host CLI bridge) ──────────────────────────────────────────
# The host API, through `stack.live.client`: one JSON request, one reply.
# It reads STACK_API_HOST and STACK_API_PORT from core's env.

# How long a reply from the host API may take before the tool says so.
STACK_API_TIMEOUT_SECS = 30


# ── Discovery ──────────────────────────────────────────────────────────────

DISCOVERY = {
    "version": "0.2.1",
    "commands": {
        "status":   {"needs_stacklet": False, "description": "Server status overview"},
        "list":     {"needs_stacklet": False, "description": "List all stacklets and their state"},
        "config":   {"needs_stacklet": False, "description": "Show stack.toml configuration"},
        "up":       {"needs_stacklet": True,  "description": "Start a stacklet", "params": ["stacklet"]},
        "down":     {"needs_stacklet": True,  "description": "Stop a stacklet", "params": ["stacklet"]},
        "restart":  {"needs_stacklet": True,  "description": "Restart a stacklet", "params": ["stacklet"]},
        "env":      {"needs_stacklet": True,  "description": "Render environment variables", "params": ["stacklet"]},
        "logs":     {"needs_stacklet": True,  "description": "Get container logs", "params": ["stacklet", "tail", "grep"]},
    },
}


@app.get("/", summary="API discovery")
async def discover():
    """Return the API surface — commands, their signatures, and requirements."""
    return DISCOVERY


# ── Persistent links ─────────────────────────────────────────────────────────
#
# Stable `/<prefix>/docs/<id>`, `/<prefix>/topic/<name>`, and
# `/<prefix>/person/<name>` links that a chat message can carry forever. They
# re-resolve at click time, so a rename, a hosting-mode switch, or a moved
# backend never breaks a link already frozen in Matrix history. Entities are
# explicit nouns — the noun says which kind, no roster guessing. The mapping is
# the pure `resolver` module; these routes are just the HTTP edge that turns a
# hit into a 302. A link that names one card lands on the card's page
# (`/card/<card id>`), and that page says why when no card answers to it:
# the card was removed, or the memory is not set up. In domain mode Caddy
# forwards only this `/<prefix>/*` and `/card/*` to core, so the ops
# endpoints stay internal.

async def _resolved(kind: str, rest: list[str]) -> Response | None:
    """The redirect for a `/<prefix>/<kind>/...` link, None when it names
    nothing. The card lookups scan the brain, so they run in a worker
    thread, not on the loop."""
    url = await asyncio.to_thread(
        build_redirect, kind, rest, docs_base=DOCS_PUBLIC_URL, wiki_base=WIKI_PUBLIC_URL,
        shared_bucket=LINK_SHARED_BUCKET,
        find_card=lambda kind, cid: _card_at(find_card(kind, cid, root=BRAIN_DIR)),
        find_document=lambda n: _card_at(find_document(n, root=BRAIN_DIR)),
        find_list=lambda slug: _card_at(
            find_list(slug, root=BRAIN_DIR, shared_bucket=LINK_SHARED_BUCKET)),
    )
    return RedirectResponse(url, status_code=302) if url else None


def _card_at(path: str | None) -> str | None:
    """Where the card at `path` lives: its card id, so the link lands on
    its address in one redirect; its path while it has no id."""
    return (card_id_at(path, root=BRAIN_DIR) or path) if path else None


async def _go(kind: str, rest: list[str]) -> Response:
    """A link to a topic, a person, a list or a document: the redirect, or a 404."""
    return await _resolved(kind, rest) or _error("no such resource", status=404)


async def _go_card(kind: str, card_id: str, request: Request) -> Response:
    """A link to one card by an id of its kind: its page, or why there is none."""
    return await _resolved(kind, [card_id]) or await _no_card(request)


async def _no_card(request: Request) -> Response:
    """No card answers to a card link: a page that says the memory is not
    set up yet, or that no card has the address. Both are a 404."""
    return _answer(await memory_ui.no_card(language=_language(request),
                                           never_set_up=_memory_never_set_up))


@app.get(f"/{LINK_PREFIX}/docs/{{doc_id}}", summary="Resolve a document link")
async def go_docs(doc_id: str):
    """Redirect a stable doc link to the page of the document's card, or to
    the document in Paperless when no card was filed from it."""
    return await _go("docs", [doc_id])


@app.get(f"/{LINK_PREFIX}/topic/{{name:path}}", summary="Resolve a topic link")
async def go_topic(name: str):
    """Redirect a stable topic link to the topic's current wiki page."""
    return await _go("topic", [s for s in name.split("/") if s])


@app.get(f"/{LINK_PREFIX}/person/{{name:path}}", summary="Resolve a person link")
async def go_person(name: str):
    """Redirect a stable person link to that member's current wiki page."""
    return await _go("person", [s for s in name.split("/") if s])


@app.get(f"/{LINK_PREFIX}/list/{{slug}}", summary="Resolve a list link")
async def go_list(slug: str, request: Request):
    """Redirect a stable list link to the list's card page: the family's
    list of that name, else a person's own."""
    return await _go_card("list", slug, request)


@app.get(f"/{LINK_PREFIX}/capture/{{capture_id:path}}",
         summary="Resolve a capture link")
async def go_capture(capture_id: str, request: Request):
    """Redirect a captured note or bookmark to its card's page.

    Keyed by the capture's id rather than its path, so re-scoping it,
    renaming its topic, or correcting its title all leave the link
    working. 404 when no file carries that id — better than showing
    someone a different note.
    """
    return await _go_card("capture", capture_id, request)


@app.get(f"/{LINK_PREFIX}/diary/{{entry_id}}", summary="Resolve a diary card link")
async def go_diary(entry_id: str, request: Request):
    """Redirect a diary card to its card's page, by its entry id.

    A family member's correction can move a card to another day, and so
    to another folder; the entry id stays. 404 when no card carries it.
    """
    return await _go_card("diary", entry_id, request)


@app.get(f"/{LINK_PREFIX}/{phone_page.LINK}", include_in_schema=False)
async def go_app(request: Request):
    """The phone page (`phone_page.py`): the chat app for the device that
    opened it, and the sign-in: on the request that paired a device as a
    person, that person's username and stored password, once; on a later
    visit the username and "Your sign-in ›" to the sign-in guide; on a
    shared device none; on the Mac's address the admin's username
    without a password, which the setup page shows. Never cached (`devices.NO_STORE`). Opened from another
    device, it marks step 4's "Your phone reaches famstack" and tells the
    setup page on the Mac (`phone_reach.py`). Declared before the card
    link below, which would take `app` for a card id."""
    listing, family = await asyncio.gather(_listing(), _family())
    if phone_reach.record(here=here(request), now=datetime.now(timezone.utc)):
        live_routes.hub.publish(phone_reach.EVENT, {}, subject=phone_reach.SUBJECT)
    messages = next((s for s in (listing or {}).get("stacklets", []) if s["id"] == "messages"), {})
    looking = devices.who(request)
    person = looking.person_of(family)
    paired_now = getattr(request.state, "paired_now", False)
    view = phone_page.view(
        device=phone_page.device_of(request.headers.get("user-agent", "")),
        language=_language(request),
        server=service_card.chat_server(messages, here=False) if _runs(listing, "messages") else "",
        username=person["id"] if person else "",
        browser=(messages.get("urls") or {}).get("element", ""),
        # The person's own password shows once, at the end of pairing; a
        # later visit links to the sign-in guide instead.
        password=await devices.own_password(looking, person) if paired_now else None,
        guide=f"/{LINK_PREFIX}/{phone_page.SIGN_IN}"
        if person and looking.sees_password_of(person["id"]) else "",
        paired=devices.pages.paired(person["name"], _language(request))
        if person and paired_now else None)
    return Response(phone_page.html(view, base=DASHBOARD_PREFIX), media_type="text/html; charset=utf-8",
                    headers=devices.NO_STORE)


@app.get(f"/{LINK_PREFIX}/{phone_page.SIGN_IN}", include_in_schema=False)
async def go_app_sign_in(request: Request):
    """The sign-in guide (`sign_in_guide.py`): the steps for the chat app
    the phone page offered this device, with the chat server, and on a
    device paired as a person that person's username and stored password;
    on a shared device no username and no password. Never cached
    (`devices.NO_STORE`)."""
    listing, family = await asyncio.gather(_listing(), _family())
    messages = next((s for s in (listing or {}).get("stacklets", []) if s["id"] == "messages"), {})
    looking = devices.who(request)
    person = looking.person_of(family)
    view = sign_in_guide.view(
        device=phone_page.device_of(request.headers.get("user-agent", "")),
        language=_language(request),
        server=service_card.chat_server(messages, here=False) if _runs(listing, "messages") else "",
        username=person["id"] if person else "", password=await devices.own_password(looking, person))
    return Response(sign_in_guide.html(view, base=DASHBOARD_PREFIX),
                    media_type="text/html; charset=utf-8", headers=devices.NO_STORE)


@app.get(f"/{LINK_PREFIX}/card/{{card_id}}", summary="Resolve a link to any card by its card id")
async def go_card(card_id: str, request: Request):
    """Redirect `/<prefix>/card/<card_id>` to the card's page, `/card/<card id>`.

    The card id is the card's address, so it goes there without a lookup.
    The page decides which card the id names: a former id goes on to the
    card that carries it, a removed card answers 410 with who removed it
    and when, an id no card ever carried is a 404 (`memory_ui.open_card`).
    Text that is no card id is a 404 here.

    A link names its kind, as `/<prefix>/docs/<n>` does: `/<prefix>/<card
    id>` without `card/` was never written by a released version and is a
    404 like any unknown path.
    """
    if not is_card_id(card_id):
        return await _no_card(request)
    return RedirectResponse(card_address(card_id), status_code=302)


# ── Logs ───────────────────────────────────────────────────────────────────

@app.get("/logs", summary="Get container logs")
async def logs(stacklet: str, tail: int = 200, grep: str = ""):
    """Get recent container logs for a stacklet, optionally filtered by grep."""
    if not stacklet:
        return _error("'stacklet' query parameter is required", status=400)

    result = client.call({"cmd": "logs", "stacklet": stacklet, "tail": tail, "grep": grep or None},
                         timeout=STACK_API_TIMEOUT_SECS)
    if "error" in result:
        return _error(result["error"], status=503)

    return {
        "stacklet": stacklet,
        "tail": tail,
        "grep": grep or None,
        "lines": result.get("lines", []),
        "count": result.get("count", 0),
    }


@app.get("/tools/stack/status", summary="Get server status",
         description="Returns the current status of all famstack services including which are online, stopped, or failing.")
async def stack_status():
    """Check the status of all services on the family server."""
    return client.call({"cmd": "status"}, timeout=STACK_API_TIMEOUT_SECS)


@app.get("/tools/stack/services", summary="List all services",
         description="Lists all available and running services (stacklets) on the family server.")
async def stack_list():
    """List all available services and their current state."""
    return client.call({"cmd": "list"}, timeout=STACK_API_TIMEOUT_SECS)


# ── Document search (Paperless-ngx) ──────────────────────────────────────
# Paperless provides full-text search across OCR'd documents. The family
# can ask "find the car insurance" and the LLM searches the archive.

@app.get("/tools/documents/search", summary="Search family documents",
         description="Search through all scanned and uploaded documents (receipts, letters, contracts, etc.) using full-text search powered by OCR.")
async def search_documents(query: str, limit: int = 5):
    """Search the family document archive for matching documents."""
    if not PAPERLESS_URL or not PAPERLESS_TOKEN:
        return _error("Documents service is not configured — install docs first")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PAPERLESS_URL}/api/documents/",
                params={"query": query, "page_size": limit},
                headers={"Authorization": f"Token {PAPERLESS_TOKEN}"},
                timeout=15,
            )
    except httpx.ConnectError:
        return _error("Cannot reach Paperless — is the docs stacklet running?")
    except httpx.TimeoutException:
        return _error("Paperless search timed out — try a shorter query")

    if resp.status_code == 401:
        return _error("Paperless API token is invalid — re-run 'stack up docs'")
    if resp.status_code != 200:
        return _error(f"Paperless returned HTTP {resp.status_code}")

    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError):
        return _error("Paperless returned an invalid response")
    results = []
    for doc in data.get("results", []):
        results.append({
            "id": doc.get("id"),
            "title": doc.get("title", ""),
            "created": doc.get("created", ""),
            "correspondent": doc.get("correspondent_name", ""),
            "document_type": doc.get("document_type_name", ""),
            "content_preview": (doc.get("content", "") or "")[:300],
        })
    return {"query": query, "count": data.get("count", 0), "results": results}


# ── Photo search (Immich) ────────────────────────────────────────────────
# Immich has smart search (CLIP-based) that understands natural language.
# "Photos of the kids at the beach" actually works.

@app.get("/tools/photos/search", summary="Search family photos",
         description="Search the family photo library using natural language. Understands people, places, objects, and scenes.")
async def search_photos(query: str, limit: int = 10):
    """Search family photos using natural language descriptions."""
    if not IMMICH_URL or not IMMICH_API_KEY:
        return _error("Photos service is not configured — install photos first")

    try:
        async with httpx.AsyncClient() as client:
            # Immich smart search uses CLIP embeddings for natural language
            resp = await client.post(
                f"{IMMICH_URL}/api/search/smart",
                json={"query": query, "page": 1, "size": limit},
                headers={"x-api-key": IMMICH_API_KEY},
                timeout=15,
            )
    except httpx.ConnectError:
        return _error("Cannot reach Immich — is the photos stacklet running?")
    except httpx.TimeoutException:
        return _error("Immich search timed out — try a shorter query")

    if resp.status_code == 401:
        return _error("Immich API key is invalid — re-run 'stack up photos'")
    if resp.status_code != 200:
        return _error(f"Immich returned HTTP {resp.status_code}")

    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError):
        return _error("Immich returned an invalid response")
    assets = data.get("items", data.get("assets", {}).get("items", []))
    results = []
    for asset in assets:
        results.append({
            "id": asset.get("id"),
            "type": asset.get("type", "IMAGE"),
            "date": asset.get("fileCreatedAt", ""),
            "city": asset.get("exifInfo", {}).get("city", ""),
            "description": asset.get("exifInfo", {}).get("description", ""),
        })
    return {"query": query, "count": len(results), "results": results}


# ── Serving ──────────────────────────────────────────────────────────────

def _listen(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    return sock


# ── The card browser's old address (`/cards`, this Mac only) ─────────────
# The page that sends a browser on to `/card` (`forwarding_page`), the
# memory's reads passed through to the memory stacklet's cards API, and a
# change to one card passed on the same way, with the headers it is
# decided by (`cards_page.py`). The host's commands use `/cards/api`.

@app.get("/cards", include_in_schema=False)
async def cards_page():
    return Response(cards_page_module.forwarding_page(), media_type="text/html; charset=utf-8")


# What the cards API says about a file that core repeats: how long to keep
# it, and that a media file is never sniffed into a page or shown inline
# when the API sends it to download.
# A card's version (`etag`) is what a client sends back to change it.
_PASSED_ON = ("cache-control", "x-content-type-options", "content-disposition", "etag")


def _memory_cards_url() -> str:
    return os.environ.get("MEMORY_CARDS_URL", cards_page_module.MEMORY_CARDS_URL)


@app.get("/cards/api/{path:path}", include_in_schema=False)
async def cards_api(path: str, request: Request):
    return await _pass_read(path, request, cards_page_module.MAC_ROUTES)


# A browser's reads on the home network: a page's media and fonts only.
@app.get(f"{CARD_PREFIX}/api/{{path:path}}", include_in_schema=False)
async def card_api(path: str, request: Request):
    return await _pass_read(path, request, cards_page_module.BROWSER_ROUTES)


async def _pass_read(path: str, request: Request, routes: re.Pattern[str]) -> Response:
    """Pass a read of `path` on to the memory when `routes` holds it; 404, asking
    nothing of the memory, when not."""
    url = cards_page_module.upstream(path, dict(request.query_params), _memory_cards_url(),
                                     routes=routes)
    if url is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await _pass_on("GET", url, request)


@app.api_route("/cards/api/{path:path}", methods=["PATCH", "POST"], include_in_schema=False)
async def cards_api_write(path: str, request: Request):
    url = cards_page_module.write_upstream(request.method, path, _memory_cards_url())
    if url is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await _pass_on(request.method, url, request, body=await request.body())


async def _pass_on(method: str, url: str, request: Request, *, body: bytes = b"") -> Response:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(method, url, content=body or None,
                                     headers=cards_page_module.forwarded(request.headers))
    except httpx.HTTPError:
        return _error("the family memory does not answer; is the memory stacklet running?")
    headers = {k: r.headers[k] for k in _PASSED_ON if k in r.headers}
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"), headers=headers)


# ── The card pages (`/card`, on the network) ─────────────────────────────
# Every card's page, rendered here from the memory's cards API
# (`memory_ui`), their stylesheet and script, and a card's history and a
# note's full text, which the page asks for when "History" or "Full text"
# is opened. A browser's own reads of the memory, its media and fonts, go
# through `/card/api`, above; no route under `/card` takes a write, and
# `/card` itself is no page. Each page answers from `memory_ui`
# (`memory_ui/pages.py`): the routes pass the language, the time and
# whether the memory was ever set up, and send the answer.

@app.get(f"{CARD_PREFIX}/static/{{name}}", include_in_schema=False)
async def card_asset(name: str):
    found = cards_page_module.asset(name)
    if found is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    kind, body = found
    return Response(body, media_type=kind)


@app.get(CARD_PREFIX, include_in_schema=False)
async def no_card_page(request: Request):
    """`/card` alone names no card: a 404 that says so, never a redirect to
    `/card/`, which FastAPI would otherwise answer with."""
    return await _no_card(request)


@app.get(f"{CARD_PREFIX}/{{card_id}}/history", include_in_schema=False)
async def card_history(card_id: str, request: Request):
    return _answer(await memory_ui.open_history(card_id, _reads(), now=_now(),
                                                language=_language(request)))


@app.get(f"{CARD_PREFIX}/{{card_id}}/text", include_in_schema=False)
async def card_text(card_id: str, request: Request):
    return _answer(await memory_ui.open_text(card_id, _reads(), language=_language(request)))


@app.get(f"{CARD_PREFIX}/{{ref:path}}", include_in_schema=False)
async def card_page(ref: str, request: Request):
    return _answer(await memory_ui.open_card(ref, _reads(), now=_now(), language=_language(request),
                                             paperless=DOCS_PUBLIC_URL,
                                             never_set_up=_memory_never_set_up))


def _reads() -> memory_ui.Reads:
    """The memory's cards API, read for the pages and the dashboard."""
    return memory_ui.Reads(_memory_cards_url(), vault=VAULT_DIR)


async def _memory_never_set_up() -> bool:
    """Whether the memory was never set up here, asked of the host."""
    return _never_set_up(await _listing(), "memory")


def _answer(answer: memory_ui.Answer) -> Response:
    """A page's answer as the response: a redirect, or its HTML."""
    if answer.location:
        return RedirectResponse(answer.location, status_code=answer.status)
    return _html(answer.html, status=answer.status)


def _html(body: str, status: int = 200) -> Response:
    return Response(body, status_code=status, media_type="text/html; charset=utf-8")


def _now() -> datetime:
    """Now, in the instance's time zone, which decides what "today" is."""
    zone = _zone()
    return datetime.now(zone) if zone else datetime.now().astimezone()


# ── The dashboard (`/dashboard`, on the network) ───────────────────────────
# The page with its widgets already rendered, each widget's data for its
# script, and the files the page loads (`dashboard_page.py`). Its reads go
# to the memory stacklet's cards API, GET only, like the card browser's.

def _zone():
    """The instance's time zone, which decides what "today" is."""
    try:
        return ZoneInfo(os.environ.get("TIMEZONE", ""))
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _language(request: Request) -> str:
    return dashboard_page.language_of(request.headers.get("accept-language", ""))


async def _listing() -> dict | None:
    """`stack list --json` through the host API; None when it did not answer.
    It runs every health check, so a page reads it once for all its widgets."""
    listing = await asyncio.to_thread(client.call, {"cmd": "list"}, timeout=STACK_API_TIMEOUT_SECS)
    return listing if "stacklets" in listing else None


def _runs(listing: dict | None, sid: str) -> bool:
    """Set up here and online; online alone is Docker's word for a container
    by its name, which another instance's can share."""
    return any(s["id"] == sid and s.get("enabled") and s.get("online")
               for s in (listing or {}).get("stacklets", []))


def _never_set_up(listing: dict | None, sid: str) -> bool:
    """Known not to be set up: false while the stack does not answer."""
    return listing is not None and not any(s["id"] == sid and s.get("enabled")
                                           for s in listing["stacklets"])


async def _activity(request: Request, listing: dict | None = None) -> dict:
    """The activity widget's view for this request: its audience and length,
    in the browser's language, each card with its `/go` link. When the
    memory does not answer, `listing` (read here if not given) says whether
    it was ever set up: without it the widget is quiet, not an error."""
    query = request.query_params
    limit = dashboard_page.limit_of(query.get("limit"), activity_widget.LIMIT, activity_widget.MOST)
    audience = query.get("audience", "")
    history = await _reads().history(limit, audience)
    no_memory = history is None and _never_set_up(listing or await _listing(), "memory")
    zone = _zone()
    return activity_widget.view(
        history, now=datetime.now(zone) if zone else datetime.now().astimezone(),
        language=_language(request),
        href=dashboard_page.card_link(LINK_PREFIX),
        base=DASHBOARD_PREFIX, limit=limit, audience=audience, size=query.get("size", ""),
        no_memory=no_memory)


async def _status(request: Request, listing: dict | None, family: dict | None) -> dict:
    """The status pills' view: the stack's state from the host API's `list`,
    with the setup's current step for the link to what is left. The phone's
    answer is the setup page's cookie, which this browser sends here too."""
    chat, history = (await _chat_and_letters(listing)) if family else (None, None)
    now = datetime.now(timezone.utc)
    current = setup_page.current_step(listing, family, chat, history=history,
                                      language=_language(request), now=now,
                                      phone=request.cookies.get(setup_page.PHONE_COOKIE, ""),
                                      ai_app=await _ai_app(listing))
    looking = devices.who(request)
    admin = looking.person_of(family) if looking.here else None
    return status_pills.view(
        listing, jobs=[job.snapshot() for job in live_routes.jobs.recent()], now=now,
        language=_language(request), base=DASHBOARD_PREFIX, who=looking, family=family,
        current=current, device=service_card.device_of(request.headers.get("user-agent", "")),
        placeholder_key=await _placeholder_key(request, listing),
        passwords=await devices.passwords([admin["id"]]) if admin else {},
        guide=f"/{LINK_PREFIX}/{phone_page.SIGN_IN}")


# How far back the setup looks for the first letter Documents filed. A
# family that many changes past it has long hidden the setup.
FIRST_LETTER_WITHIN = 100


async def _chat_and_letters(listing: dict | None) -> tuple[dict | None, dict | None]:
    """Who is in the chat (only while it runs) and, once Documents is set up,
    the memory's history, where the first letter shows; None for either
    while it cannot be known."""
    chat = None
    if _runs(listing, "messages"):
        answer = await asyncio.to_thread(client.call, {"cmd": "chat_users"},
                                         timeout=STACK_API_TIMEOUT_SECS)
        chat = answer if "users" in answer else None
    history = (await _reads().history(FIRST_LETTER_WITHIN)
               if not _never_set_up(listing, "docs") and listing else None)
    return chat, history


async def _family() -> dict | None:
    """What `stack family` shows, through the host API; None when it did not answer."""
    family = await asyncio.to_thread(client.call, {"cmd": "family"}, timeout=STACK_API_TIMEOUT_SECS)
    return family if "people" in family else None


@app.get(DASHBOARD_PREFIX, include_in_schema=False)
async def dashboard(request: Request):
    listing, family = await asyncio.gather(_listing(), _family())
    activity, status = await asyncio.gather(_activity(request, listing),
                                            _status(request, listing, family))
    size = request.query_params.get("size", "")
    # The pills re-read when a job starts or ends and on a resync, never on a
    # timer: reading the state runs every health check.
    widgets = [dashboard_page.shell(status, status_pills.html(status), name="status", size=size,
                                    refresh=0),
               dashboard_page.shell(activity, activity_widget.html(activity), name="activity",
                                    size=size)]
    looking = devices.who(request)
    body = dashboard_page.page(widgets, base=DASHBOARD_PREFIX, language=activity["language"],
                               who=looking,
                               looking=devices.looking(looking, family, activity["language"]),
                               family=family, chat_runs=listing is None or _runs(listing, "messages"))
    # The Mac's dashboard carries the admin's password in its service cards.
    return Response(body, media_type="text/html; charset=utf-8", headers=devices.NO_STORE)


# The setup makes the changes, so it is on the Mac's address only; the home
# address answers with where to go (`only_on_the_mac`).
@app.get(SETUP_PATH, include_in_schema=False, dependencies=[Depends(mac_only)])
async def setup(request: Request):
    """The setup page at the step asked for (`?step=ai`), or the current one.
    `?phone=signed` or `?phone=skipped` is the admin's answer about the
    phone: kept in a cookie of this browser, because nothing else knows it.
    While the family is known to be empty, the first run on the dashboard
    comes first."""
    listing, family = await asyncio.gather(_listing(), _family())
    if family_view.is_empty(family):
        return RedirectResponse(DASHBOARD_PREFIX, status_code=303)
    query = request.query_params
    answered = query.get("phone", "")
    phone = answered if answered in setup_page.PHONE_ANSWERS else \
        request.cookies.get(setup_page.PHONE_COOKIE, "")
    reached = phone_reach.reached() is not None or bool(request.cookies.get(phone_reach.COOKIE))
    (chat, history), firewall = await asyncio.gather(
        _chat_and_letters(listing) if family else _nothing((None, None)),
        _firewall(listing) if family and not reached else _nothing(""))
    step = query.get("step", "")
    now = datetime.now(timezone.utc)
    ai_app = await _ai_app(listing)
    # The AI stacklet is asked only for its own step: it looks for the oMLX
    # app on this Mac and asks oMLX where its models are.
    shown = step or setup_page.current_step(
        listing, family, chat, history=history, language=_language(request), now=now,
        phone=phone, ai_app=ai_app)
    at_ai = shown == "ai"
    ai = await _ai_plan() if at_ai else None
    placeholder_key = await _placeholder_key(request, listing)
    passwords = await devices.passwords([p["id"] for p in (family or {}).get("people", [])]) \
        if here(request) else {}
    view = setup_page.view(listing, family, chat, history=history, language=_language(request),
                           base=DASHBOARD_PREFIX, now=datetime.now(timezone.utc),
                           jobs=[job.snapshot() for job in live_routes.jobs.recent()],
                           who=devices.who(request), step=step, phone=phone,
                           device=service_card.device_of(request.headers.get("user-agent", "")),
                           reached=reached, phone_link=_phone_link(), firewall=firewall,
                           pairing=_admin_pairing(family, _language(request))
                           if shown == "phone" else "",
                           ai=ai, ai_choice=query.get("ai", ""),
                           placeholder_key=placeholder_key, passwords=passwords,
                           ai_app=ai_app)
    response = Response(setup_page.page(view, base=DASHBOARD_PREFIX),
                        media_type="text/html; charset=utf-8", headers=devices.NO_STORE)
    if reached:
        # Core forgets the reach when it is recreated; this browser keeps it.
        response.set_cookie(phone_reach.COOKIE, "1", path="/",
                            max_age=PHONE_ANSWER_KEPT_SECS,
                            **browser_checks.cookie_flags(request))
    if answered in setup_page.PHONE_ANSWERS:
        # Kept for the whole server: the dashboard's link to what is left reads it too.
        response.set_cookie(setup_page.PHONE_COOKIE, answered, path="/",
                            max_age=PHONE_ANSWER_KEPT_SECS,
                            **browser_checks.cookie_flags(request))
    return response


@app.get(setup_page.SIGNED_IN, include_in_schema=False, dependencies=[Depends(mac_only)])
async def setup_signed_in():
    """Whether the family's admin has signed in to the chat: `{"signed_in":
    true}` once the chat lists a sign-in. Step 3's open page asks it every
    few seconds while it waits (`setup_page.SIGNED_IN`). It reads the family
    and the chat's users, never `stack list`: false while the chat does not
    run or the stack does not answer."""
    family, chat = await asyncio.gather(
        _family(), asyncio.to_thread(client.call, {"cmd": "chat_users"},
                                     timeout=STACK_API_TIMEOUT_SECS))
    return {"signed_in": setup_page.signed_in(family, chat)}


async def _firewall(listing: dict | None) -> str:
    """What keeps phones out, as `stack core firewall` names it, for step 4
    while it waits for a phone; "" when nothing does or it cannot be told.
    Read on the Mac through the host API, because a container cannot see
    the Mac's firewall, and only while the chat runs: before, there is no
    phone to keep out."""
    if not _runs(listing, "messages"):
        return ""
    answer = await asyncio.to_thread(client.call, {"cmd": "firewall"}, timeout=STACK_API_TIMEOUT_SECS)
    return answer.get("blocks", "") if isinstance(answer.get("blocks"), str) else ""


async def _nothing(value):
    """`value`, as an awaitable, for a read a request does not need."""
    return value


async def _ai_plan() -> dict | None:
    """`stack ai recommended show` through the host API: the oMLX app on
    this Mac and the models recommended for it; None when it did not answer."""
    plan = await asyncio.to_thread(client.call, {"cmd": "ai_recommended"},
                                   timeout=STACK_API_TIMEOUT_SECS)
    return plan if "connected" in plan else None


@app.get(f"{DASHBOARD_PREFIX}/api/ai", include_in_schema=False)
async def dashboard_ai():
    """The AI step's plan, for its download bars: 200, or 503 while the
    stack does not answer."""
    plan = await _ai_plan()
    return plan if plan is not None else _error("The stack did not answer.")


# A change: it writes `[ai]` into `stack.toml` and starts gigabytes of
# downloads. The key's field of the same step is Mac-only too.
@app.post(f"{DASHBOARD_PREFIX}/api/ai/download", include_in_schema=False,
          dependencies=[Depends(mac_only)])
async def dashboard_ai_download(request: Request):
    """Record the speech choice and start the recommended models'
    downloads (`stack ai recommended download --no-wait`): 200 with the
    plan, 403 through the home address (`mac_only`), 400 without a
    boolean `speech`, 503 with the reason while the app is not connected
    or the stack does not answer."""
    try:
        sent = await request.json()
    except ValueError:
        sent = None
    speech = sent.get("speech") if isinstance(sent, dict) else None
    if not isinstance(speech, bool):
        return JSONResponse({"error": "Send {\"speech\": true} or {\"speech\": false}."},
                            status_code=400)
    plan = await asyncio.to_thread(client.call, {"cmd": "ai_download", "speech": speech},
                                   timeout=STACK_API_TIMEOUT_SECS)
    if "connected" in plan:
        return plan
    return _error(plan.get("error", "The stack did not answer."))


async def _ai_app(listing: dict | None) -> bool | None:
    """Whether the AI server the stack uses is the oMLX app on this Mac
    (`stack ai recommended app`): the setup counts the AI as reading only
    once that app has its models and the AI has started. The host decides
    it by what answers at the address, which this container cannot probe.
    Asked only while it decides the AI step (`setup_page.asks_ai_app`).
    None, which never finishes the step, when nothing answers there or
    the stack does not answer."""
    if not setup_page.asks_ai_app(listing):
        return None
    answer = await asyncio.to_thread(client.call, {"cmd": "ai_app"},
                                     timeout=STACK_API_TIMEOUT_SECS)
    app = answer.get("app")
    return app if isinstance(app, bool) else None


async def _placeholder_key(request: Request, listing: dict | None) -> bool:
    """Whether the AI the stack manages still has the key `local`
    (`stack ai key show`), which its service card names on this Mac.
    Asked only on the Mac's address and while the AI runs; False when it
    is not, or when the stack does not answer."""
    if not here(request) or not _runs(listing, "ai"):
        return False
    answer = await asyncio.to_thread(client.call, {"cmd": "ai_key"},
                                     timeout=STACK_API_TIMEOUT_SECS)
    return answer.get("placeholder") is True


# A change, and its answer is a key: on the Mac's address only.
@app.post(f"{DASHBOARD_PREFIX}/api/ai/key", include_in_schema=False,
          dependencies=[Depends(mac_only)])
async def dashboard_ai_key():
    """"Make a new key" on the AI's service card (`stack ai key new`):
    200 with `{"key"}`, once; the stacklets that use the AI restart on
    their own, core among them, so the page reconnects a moment later.
    403 through the home address; 503 with the reason when the stack did
    not make one."""
    answer = await asyncio.to_thread(client.call, {"cmd": "ai_new_key"},
                                     timeout=STACK_API_TIMEOUT_SECS)
    if "key" in answer:
        return {"key": answer["key"]}
    return _error(answer.get("error", "The stack did not answer."))


# A change that carries an API key and writes `[ai]` into `stack.toml`:
# on the Mac's address only.
@app.post(f"{DASHBOARD_PREFIX}/api/ai/switch", include_in_schema=False,
          dependencies=[Depends(mac_only)])
async def dashboard_ai_switch(request: Request):
    """Use an AI server the stack does not manage, as `stack ai switch
    <address> --key <key> [--model <id>]` does: 200 with the command's
    answer (`openai_url`, `model`, `whisper_model`, `warnings`), 403
    through the home address, 400 without a text `address`, 422 with the
    command's `reason` (`key`, `unreachable`, `model` with `models`) when
    it wrote nothing, 503 while the stack does not answer."""
    try:
        sent = await request.json()
    except ValueError:
        sent = None
    sent = sent if isinstance(sent, dict) else {}
    address, key, model = sent.get("address"), sent.get("key", ""), sent.get("model", "")
    if not isinstance(address, str) or not address.strip() or not isinstance(key, str) \
            or not isinstance(model, str):
        return JSONResponse({"error": "Send {\"address\": \"...\", \"key\": \"...\"}."},
                            status_code=400)
    answer = await asyncio.to_thread(
        client.call, {"cmd": "ai_switch", "address": address.strip(), "key": key.strip(),
                      "model": model}, timeout=STACK_API_TIMEOUT_SECS)
    if "openai_url" in answer:
        return answer
    if "reason" in answer:
        return JSONResponse(answer, status_code=422)
    return _error(answer.get("error", "The stack did not answer."))


# The AI's first words take a model's answer and its speech: the
# introduction is written, then spoken, which takes up to about a minute on
# a 16 GB Mac while the voice loads.
FIRST_WORDS_TIMEOUT_SECS = 120


# Mac-only like the setup page that asks for them: each runs up to two
# minutes of the AI and its speech on the Mac.
@app.post(f"{DASHBOARD_PREFIX}/api/ai/first-words/{{part}}", include_in_schema=False,
          dependencies=[Depends(mac_only)])
async def dashboard_ai_first_words(part: str):
    """The AI's welcome (`welcome`) or introduction (`intro`) for step 5f
    of the setup page (`first_words_step.py`): 200 with `{"text", "audio"}`,
    the MP3 as base64 in famstack's own voice; 403 through the home
    address; 404 for another part; 503 with the reason while speech or the
    stack does not answer."""
    cmd = first_words_step.PARTS.get(part)
    if cmd is None:
        return JSONResponse({"error": f"No first words called {part!r}."}, status_code=404)
    answer = await asyncio.to_thread(client.call, {"cmd": cmd},
                                     timeout=FIRST_WORDS_TIMEOUT_SECS)
    if "audio" in answer:
        return {"text": answer.get("text", ""), "audio": answer["audio"]}
    return _error(answer.get("error", "The stack did not answer."))


# How long this browser keeps the admin's answer about the phone: a year,
# longer than any setup takes.
PHONE_ANSWER_KEPT_SECS = 365 * 24 * 3600


def _phone_link() -> str:
    """The phone page's address on the home network, for step 4's QR code:
    core's link base (`LINK_BASE_URL`, `{home_url}/go`, rendered from
    `[core]` on every start) and the page's path. Read on each request,
    like the rest of core's settings; "" when core has none."""
    base = os.environ.get("LINK_BASE_URL", "").rstrip("/")
    return f"{base}/{phone_page.LINK}" if base else ""


def _admin_pairing(family: dict | None, language: str) -> str:
    """The setup's phone step's QR code: a pairing code for the admin's
    own phone, with its waiting, added and expired states; "" while core
    has no link base or the family has no admin."""
    admin = family_view.admin_of(family)
    return devices.pairing_panel(_phone_link(), admin["id"], again="/setup?step=phone", done="",
                                 language=language) if admin else ""


# Core's health check (core/stacklet.toml): `stack up core`, and so the
# installer, counts core as up once this answers on the published port. It
# asks the host API and the memory nothing, so it is cheap and says only
# that the server is there.
@app.get(f"{DASHBOARD_PREFIX}/api/health", include_in_schema=False)
def dashboard_health():
    return {"ok": True}


@app.get(f"{DASHBOARD_PREFIX}/api/family", include_in_schema=False)
async def dashboard_family(request: Request):
    """The family for the page's scripts: the pills, the greeting."""
    family = await _family()
    if family is None:
        return _error("The stack did not answer; the family is unknown for now.")
    return family_view.answer(family, dashboard_page.language_of(request.headers.get("accept-language", "")))


# A change, and the one that says who is an admin: Mac-only, so no device
# on the home network sets the family or makes itself an admin.
@app.post(f"{DASHBOARD_PREFIX}/api/family", include_in_schema=False,
          dependencies=[Depends(mac_only)])
async def dashboard_family_set(request: Request):
    """Write the family the page sends, as `stack family set` does: 200 with
    the family as written, 403 through the home address, 422 with every
    problem by its field, or 503."""
    try:
        sent = await request.json()
    except ValueError:
        sent = None
    if not isinstance(sent, dict):
        return JSONResponse({"error": "Send the family as a JSON object."}, status_code=400)
    result = await asyncio.to_thread(client.call, {"cmd": "family", "action": "set", "family": sent},
                                     timeout=STACK_API_TIMEOUT_SECS)
    if "problems" in result:
        return JSONResponse(result, status_code=422)
    if "error" in result:
        return _error(result["error"])
    return family_view.answer(result, dashboard_page.language_of(request.headers.get("accept-language", "")))


# A change, Mac-only: the answers, secrets included, are relayed as they
# are, because only the Mac's own browser reaches this route.
@app.post(f"{DASHBOARD_PREFIX}/api/configure/{{stacklet}}", include_in_schema=False,
          dependencies=[Depends(mac_only)])
async def dashboard_configure(stacklet: str, request: Request):
    """The next setup form for `stacklet`, the same form with a reason under a
    field, or ready once the answers are applied; 403 through the home
    address."""
    if not live_routes.TARGET.fullmatch(stacklet):
        return JSONResponse({"error": f"{stacklet!r} is not a stacklet id"}, status_code=400)
    try:
        sent = await request.json()
    except ValueError:
        sent = None
    answers = sent.get("answers") if isinstance(sent, dict) else None
    if not isinstance(answers, dict):
        return JSONResponse({"error": "Send the answers as {\"answers\": {...}}."}, status_code=400)
    result = await asyncio.to_thread(
        client.call, {"cmd": "configure", "stacklet": stacklet, "answers": answers},
        timeout=STACK_API_TIMEOUT_SECS)
    if "error" in result and "form" not in result:
        return _error(result["error"])
    return result


@app.get(f"{DASHBOARD_PREFIX}/api/stack", include_in_schema=False)
async def dashboard_stack(request: Request):
    listing, family = await asyncio.gather(_listing(), _family())
    view = await _status(request, listing, family)
    return JSONResponse(view, status_code=503 if view["state"] == "error" else 200,
                        headers=devices.NO_STORE)


@app.get(f"{DASHBOARD_PREFIX}/api/activity", include_in_schema=False)
async def dashboard_activity(request: Request):
    view = await _activity(request)
    return JSONResponse(view, status_code=503 if view["state"] == "error" else 200)


@app.get(f"{DASHBOARD_PREFIX}/static/{{path:path}}", include_in_schema=False)
async def dashboard_asset(path: str):
    found = dashboard_page.asset(path)
    if found is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    kind, body = found
    return Response(body, media_type=kind)


@app.get(f"{DASHBOARD_PREFIX}/fonts/{{name}}", include_in_schema=False)
async def dashboard_font(name: str):
    url = dashboard_page.font_url(_memory_cards_url(), name)
    if url is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url)
    except httpx.HTTPError:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "font/woff2"),
                    headers={"cache-control": r.headers.get("cache-control", "max-age=604800")})


if __name__ == "__main__":
    # One server on every socket, so each request's scope names the
    # listener it came in on, which is what the access rule reads.
    import uvicorn

    uvicorn.Server(uvicorn.Config(app, log_level="info")).run(
        sockets=[_listen(INTERNAL_PORT), _listen(PUBLIC_PORT), _listen(CARDS_PORT),
                 _listen(WIKI_PORT)])
