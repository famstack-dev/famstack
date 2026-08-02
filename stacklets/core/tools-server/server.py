"""Tools HTTP server — OpenAPI-compatible endpoints for Open WebUI.

Bridges the famstack ecosystem to LLM tool calling. Open WebUI discovers
these endpoints via the OpenAPI spec and exposes them as tools the AI
can invoke during conversations.

Each tool wraps an existing service API (Paperless, Immich) or the
famstack TCP API for host-side CLI control. No business logic here —
just translation between what the LLM needs and what the services provide.
"""

import json
import os
import socket
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse

from capture_index import find_capture
from resolver import build_redirect


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
# The brain projection, read-only — the same tree the wiki serves. Only
# `/go/capture/<id>` needs it, to find where a capture sits now. This is the
# mount point, not `BRAIN_REPO_DIR`: that variable is the path the *bot-runner*
# sees, and pointing this at it would name a directory that does not exist in
# this container. Unmounted (memory not installed) simply means capture
# links 404.
BRAIN_DIR = Path("/brain")


app = FastAPI(
    title="famstack Tools",
    description="Family server tools for AI assistants — search documents, find photos, check server status.",
    version="0.2.1",
)


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
        find_capture=lambda cid: find_capture(cid, brain_dir=BRAIN_DIR),
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
