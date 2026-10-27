"""Tools HTTP server — OpenAPI-compatible endpoints for Open WebUI.

Bridges the famstack ecosystem to LLM tool calling. Open WebUI discovers
these endpoints via the OpenAPI spec and exposes them as tools the AI
can invoke during conversations.

Each tool wraps an existing service API (Paperless, Immich) or the
famstack TCP API for host-side CLI control. No business logic here —
just translation between what the LLM needs and what the services provide.

It listens three times (see access.py): an internal port for the stack's
own containers, the published port, which serves the persistent links and
the dashboard, and the card browser's port on this Mac.
"""

import asyncio
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

import activity_widget
import cards_page as cards_page_module
import dashboard_page
import family_view
import live_routes
import setup_forms
import setup_widget
import stack_widget
from access import CARDS_PORT, DASHBOARD_PREFIX, INTERNAL_PORT, PUBLIC_PORT, serves
from resolver import build_redirect, card_link_answer
from stack.cards.lookup import find_by_card_id, find_card, removal_of
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
# card links need it (`/go/capture/<id>`, `/go/diary/<id>`), to find where a
# card sits now. This is the mount point, not
# `BRAIN_REPO_DIR`: that variable is the path the *bot-runner* sees, and
# pointing this at it would name a directory that does not exist in this
# container. Unmounted (memory not installed) simply means card links 404.
BRAIN_DIR = Path("/brain")
# The memory repo, read for its history: when a card no file carries any
# more was removed. Unmounted, such a link is a plain 404.
VAULT_DIR = Path("/vault")



app = FastAPI(
    title="famstack Tools",
    description="Family server tools for AI assistants — search documents, find photos, check server status.",
    version="0.2.1",
)


# Jobs a screen follows, and the event stream it follows them on (ADR-020).
app.include_router(live_routes.router)


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
# hit into a 302. In domain mode Caddy forwards only this `/<prefix>/*`
# namespace to core, so the ops endpoints stay internal.

def _go(kind: str, rest: list[str]):
    url = build_redirect(
        kind, rest, docs_base=DOCS_PUBLIC_URL, wiki_base=WIKI_PUBLIC_URL,
        shared_bucket=LINK_SHARED_BUCKET,
        find_card=lambda kind, cid: find_card(kind, cid, root=BRAIN_DIR),
    )
    if not url:
        return _error("no such resource", status=404)
    return RedirectResponse(url, status_code=302)


@app.get(f"/{LINK_PREFIX}/docs/{{doc_id}}", summary="Resolve a document link")
async def go_docs(doc_id: str):
    """Redirect a stable doc link to the document in Paperless."""
    return _go("docs", [doc_id])


@app.get(f"/{LINK_PREFIX}/topic/{{name:path}}", summary="Resolve a topic link")
async def go_topic(name: str):
    """Redirect a stable topic link to the topic's current wiki page."""
    return _go("topic", [s for s in name.split("/") if s])


@app.get(f"/{LINK_PREFIX}/person/{{name:path}}", summary="Resolve a person link")
async def go_person(name: str):
    """Redirect a stable person link to that member's current wiki page."""
    return _go("person", [s for s in name.split("/") if s])


@app.get(f"/{LINK_PREFIX}/list/{{slug}}", summary="Resolve a list link")
async def go_list(slug: str):
    """Redirect a stable list link to the list's wiki page."""
    return _go("list", [slug])


@app.get(f"/{LINK_PREFIX}/capture/{{capture_id:path}}",
         summary="Resolve a capture link")
async def go_capture(capture_id: str):
    """Redirect a captured note or bookmark to wherever it is filed now.

    Keyed by the capture's id rather than its path, so re-scoping it,
    renaming its topic, or correcting its title all leave the link
    working. 404 when no file carries that id — better than showing
    someone a different note.
    """
    return _go("capture", [capture_id])


@app.get(f"/{LINK_PREFIX}/diary/{{entry_id}}", summary="Resolve a diary card link")
async def go_diary(entry_id: str):
    """Redirect a diary card to wherever it is filed now, by its entry id.

    A family member's correction can move a card to another day, and so
    to another folder; the entry id stays. 404 when no card carries it.
    """
    return _go("diary", [entry_id])


@app.get(f"/{LINK_PREFIX}/{{card_id}}", summary="Resolve a link to any card by its card id")
def go_card(card_id: str):
    """Redirect `/<prefix>/<card_id>` to the page that shows the card now.

    A former id (`former_card_ids`) lands on the card that carries it. An
    id no card carries any more, but one did, answers 410 with the commit
    that removed it; an id no card ever carried is a 404. Declared without
    `async`, so the scan and git run in a worker thread, not on the loop.
    """
    answer = card_link_answer(
        card_id, wiki_base=WIKI_PUBLIC_URL,
        find=lambda cid: find_by_card_id(cid, root=BRAIN_DIR),
        removal=lambda cid: removal_of(cid, repo=VAULT_DIR),
    )
    if answer is None:
        return _error("no such resource", status=404)
    status, body = answer
    if status == 410:
        return PlainTextResponse(body, status_code=410)
    return RedirectResponse(body, status_code=status)


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


# ── The card browser (`/cards`, this Mac only) ──────────────────────────
# The page and its two files, its reads passed through to the memory
# stacklet's cards API, and a change to one card passed on the same way,
# with the headers it is decided by (`cards_page.py`).

@app.get("/cards", include_in_schema=False)
async def cards_page():
    return Response(cards_page_module.page(), media_type="text/html; charset=utf-8")


@app.get("/cards/static/{name}", include_in_schema=False)
async def cards_asset(name: str):
    found = cards_page_module.asset(name)
    if found is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    kind, body = found
    return Response(body, media_type=kind)


# What the cards API says about a file that core repeats: how long to keep
# it, and that a media file is never sniffed into a page or shown inline
# when the API sends it to download.
# A card's version (`etag`) is what a client sends back to change it.
_PASSED_ON = ("cache-control", "x-content-type-options", "content-disposition", "etag")


def _memory_cards_url() -> str:
    return os.environ.get("MEMORY_CARDS_URL", cards_page_module.MEMORY_CARDS_URL)


@app.get("/cards/api/{path:path}", include_in_schema=False)
async def cards_api(path: str, request: Request):
    url = cards_page_module.upstream(path, dict(request.query_params), _memory_cards_url())
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


def _memory_url() -> str:
    return os.environ.get("MEMORY_CARDS_URL", cards_page_module.MEMORY_CARDS_URL)


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


async def _history(limit: int, audience: str = "") -> dict | None:
    """The memory's recent changes from its cards API; None when it did not answer."""
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(dashboard_page.history_url(_memory_url(), limit=limit, audience=audience))
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


async def _activity(request: Request, listing: dict | None = None) -> dict:
    """The activity widget's view for this request: its audience and length,
    in the browser's language, with card links for where it is. When the
    memory does not answer, `listing` (read here if not given) says whether
    it was ever set up: without it the widget is quiet, not an error."""
    query = request.query_params
    limit = dashboard_page.limit_of(query.get("limit"), activity_widget.LIMIT, activity_widget.MOST)
    audience = query.get("audience", "")
    history = await _history(limit, audience)
    no_memory = history is None and _never_set_up(listing or await _listing(), "memory")
    zone = _zone()
    return activity_widget.view(
        history, now=datetime.now(zone) if zone else datetime.now().astimezone(),
        language=_language(request),
        href=dashboard_page.card_link(request.headers.get("host", ""), LINK_PREFIX),
        base=DASHBOARD_PREFIX, limit=limit, audience=audience, size=query.get("size", ""),
        no_memory=no_memory)


def _stack(request: Request, listing: dict | None, *, setting_up: bool) -> dict:
    """The stack widget's view: the stack's state, from the host API's `list`."""
    return stack_widget.view(
        listing, jobs=[job.snapshot() for job in live_routes.jobs.recent()],
        now=datetime.now(timezone.utc), language=_language(request),
        base=DASHBOARD_PREFIX, size=request.query_params.get("size", ""), setting_up=setting_up,
        here=_here(request))


def _here(request: Request) -> bool:
    """Whether the page was opened on this Mac, where a stacklet opens at localhost."""
    return dashboard_page.on_this_mac(request.headers.get("host", ""))


# How far back the setup looks for the first letter Documents filed. A
# family that many changes past it has long hidden the setup.
FIRST_LETTER_WITHIN = 100


async def _setup(request: Request, listing: dict | None, family: dict | None) -> dict:
    """The "Setting up" widget's view: the steps, from the listing, the
    family, who is in the chat (only while it runs) and, once Documents is
    set up, the memory's history."""
    chat = None
    if _runs(listing, "messages"):
        answer = await asyncio.to_thread(client.call, {"cmd": "chat_users"},
                                         timeout=STACK_API_TIMEOUT_SECS)
        chat = answer if "users" in answer else None
    history = (await _history(FIRST_LETTER_WITHIN)
               if not _never_set_up(listing, "docs") and listing else None)
    return setup_widget.view(listing, family, chat, history=history, language=_language(request),
                             base=DASHBOARD_PREFIX, now=datetime.now(timezone.utc),
                             jobs=[job.snapshot() for job in live_routes.jobs.recent()],
                             here=_here(request))


async def _family() -> dict | None:
    """What `stack family` shows, through the host API; None when it did not answer."""
    family = await asyncio.to_thread(client.call, {"cmd": "family"}, timeout=STACK_API_TIMEOUT_SECS)
    return family if "people" in family else None


@app.get(DASHBOARD_PREFIX, include_in_schema=False)
async def dashboard(request: Request):
    listing, family = await asyncio.gather(_listing(), _family())
    activity, setup = await asyncio.gather(_activity(request, listing),
                                           _setup(request, listing, family))
    setting_up = setup_widget.setting_up(setup)
    stack = _stack(request, listing, setting_up=setting_up)
    size = request.query_params.get("size", "")
    # The stack and setup widgets re-read when a job ends, on a resync or on
    # "Check again", never on a timer: reading the state runs every health check.
    widgets = ([dashboard_page.shell(setup, setup_widget.html(setup), name="setup", size=size,
                                     refresh=0)] if setting_up else [])
    widgets += [dashboard_page.shell(stack, stack_widget.html(stack), name="stack", size=size,
                                     refresh=0),
                dashboard_page.shell(activity, activity_widget.html(activity), name="activity",
                                     size=size)]
    body = dashboard_page.page(widgets, base=DASHBOARD_PREFIX, language=activity["language"],
                               family=family, chat_runs=listing is None or _runs(listing, "messages"))
    return Response(body, media_type="text/html; charset=utf-8")


@app.get(f"{DASHBOARD_PREFIX}/api/setup", include_in_schema=False)
async def dashboard_setup(request: Request):
    listing, family = await asyncio.gather(_listing(), _family())
    view = await _setup(request, listing, family)
    return JSONResponse(view, status_code=503 if view["state"] == "error" else 200)


@app.get(f"{DASHBOARD_PREFIX}/api/family", include_in_schema=False)
async def dashboard_family(request: Request):
    """The family for the page's scripts: the pills, the greeting."""
    family = await _family()
    if family is None:
        return _error("The stack did not answer; the family is unknown for now.")
    return family_view.answer(family, dashboard_page.language_of(request.headers.get("accept-language", "")))


# Under /dashboard, so the first run in the browser reaches it: it is open on
# the home network for now, like the job route (decided 2026-10-27, the
# feature first). Anyone there can set up or change the family until the
# backlog entry "Basic security for the tools server" closes it to this Mac
# or to a signed-in admin.
@app.post(f"{DASHBOARD_PREFIX}/api/family", include_in_schema=False)
async def dashboard_family_set(request: Request):
    """Write the family the page sends, as `stack family set` does: 200 with
    the family as written, 422 with every problem by its field, or 503."""
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


# Under /dashboard, so a tile's Add reaches it: it is open on the home
# network for now, like the job and family routes (decided 2026-10-27, the
# feature first). Anyone there can answer a stacklet's setup questions,
# secrets excepted (setup_forms.walk), until the backlog entry "Basic
# security for the tools server" closes it to this Mac or a signed-in admin.
@app.post(f"{DASHBOARD_PREFIX}/api/configure/{{stacklet}}", include_in_schema=False)
async def dashboard_configure(stacklet: str, request: Request):
    """The next setup form for `stacklet`, the same form with a reason under a
    field, or ready once the answers are applied; from another device than
    this Mac, the terminal command at a form that asks for a secret."""
    if not live_routes.TARGET.fullmatch(stacklet):
        return JSONResponse({"error": f"{stacklet!r} is not a stacklet id"}, status_code=400)
    try:
        sent = await request.json()
    except ValueError:
        sent = None
    answers = sent.get("answers") if isinstance(sent, dict) else None
    if not isinstance(answers, dict):
        return JSONResponse({"error": "Send the answers as {\"answers\": {...}}."}, status_code=400)

    def call(given: dict) -> dict:
        return client.call({"cmd": "configure", "stacklet": stacklet, "answers": given},
                           timeout=STACK_API_TIMEOUT_SECS)

    here = dashboard_page.on_this_mac(request.headers.get("host", ""))
    result = await asyncio.to_thread(setup_forms.relay, stacklet, answers, call, here=here)
    if "terminal" in result:
        words = stack_widget.words(_language(request))["forms"]
        return {**result, "message": words["on_this_mac"]}
    if "error" in result and "form" not in result:
        return _error(result["error"])
    return result


@app.get(f"{DASHBOARD_PREFIX}/api/stack", include_in_schema=False)
async def dashboard_stack(request: Request):
    listing, family = await asyncio.gather(_listing(), _family())
    setup = await _setup(request, listing, family)
    view = _stack(request, listing, setting_up=setup_widget.setting_up(setup))
    return JSONResponse(view, status_code=503 if view["state"] == "error" else 200)


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
    url = dashboard_page.font_url(_memory_url(), name)
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
        sockets=[_listen(INTERNAL_PORT), _listen(PUBLIC_PORT), _listen(CARDS_PORT)])
