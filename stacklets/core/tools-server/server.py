"""Tools HTTP server — OpenAPI-compatible endpoints for Open WebUI.

Bridges the famstack ecosystem to LLM tool calling. Open WebUI discovers
these endpoints via the OpenAPI spec and exposes them as tools the AI
can invoke during conversations.

Each tool wraps an existing service API (Paperless, Immich) or the
famstack TCP API for host-side CLI control. No business logic here —
just translation between what the LLM needs and what the services provide.

It listens twice (see access.py): an internal port for the stack's own
containers, and the published port, which serves only the persistent links.
"""

import json
import os
import socket
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

import cards_page as cards_page_module
from access import CARDS_PORT, INTERNAL_PORT, PUBLIC_PORT, serves
from resolver import build_redirect, card_link_answer
from stack.card_lookup import find_by_card_id, find_card, removal_of


# ── Config from environment ──────────────────────────────────────────────
# All injected via docker-compose env_file from core's .env

STACK_API_HOST = os.environ.get("STACK_API_HOST", "host.docker.internal")
STACK_API_PORT = int(os.environ.get("STACK_API_PORT", "42001"))

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


@app.middleware("http")
async def links_only_from_the_network(request, call_next):
    """Answer the published port with the links and nothing else. A path
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
# Reuses the existing TCP socket API on the host. Same protocol the
# bot runner uses — proven path, no socket file mounting needed.

def _stack_api(cmd: str, stacklet: str = "", **kwargs) -> dict:
    """Send a command to the famstack TCP API on the host."""
    request = {"cmd": cmd}
    if stacklet:
        request["stacklet"] = stacklet
    for k, v in kwargs.items():
        if v is not None:
            request[k] = v
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(30)
    try:
        s.connect((STACK_API_HOST, STACK_API_PORT))
        s.sendall((json.dumps(request) + "\n").encode())
        chunks = []
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        return json.loads(b"".join(chunks).decode().strip())
    except ConnectionRefusedError:
        return {"error": "famstack API is not running on the host"}
    except socket.timeout:
        return {"error": f"famstack API timed out ({cmd})"}
    except json.JSONDecodeError:
        return {"error": "famstack API returned invalid response"}
    except OSError as e:
        return {"error": f"Cannot reach famstack API: {e}"}
    finally:
        s.close()


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

    result = _stack_api("logs", stacklet, tail=tail, grep=grep or None)
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
    return _stack_api("status")


@app.get("/tools/stack/services", summary="List all services",
         description="Lists all available and running services (stacklets) on the family server.")
async def stack_list():
    """List all available services and their current state."""
    return _stack_api("list")


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
# The page and its two files, and its reads passed through to the memory
# stacklet's cards API, GET only (`cards_page.py`).

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
_PASSED_ON = ("cache-control", "x-content-type-options", "content-disposition")


@app.get("/cards/api/{path:path}", include_in_schema=False)
async def cards_api(path: str, request: Request):
    url = cards_page_module.upstream(path, dict(request.query_params),
                                     os.environ.get("MEMORY_CARDS_URL", cards_page_module.MEMORY_CARDS_URL))
    if url is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url)
    except httpx.HTTPError:
        return _error("the family memory does not answer; is the memory stacklet running?")
    headers = {k: r.headers[k] for k in _PASSED_ON if k in r.headers}
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"), headers=headers)


if __name__ == "__main__":
    # One server on every socket, so each request's scope names the
    # listener it came in on, which is what the access rule reads.
    import uvicorn

    uvicorn.Server(uvicorn.Config(app, log_level="info")).run(
        sockets=[_listen(INTERNAL_PORT), _listen(PUBLIC_PORT), _listen(CARDS_PORT)])
