#!/usr/bin/env python3
"""See the dashboard as a family would, with no stack running.

The dashboard is core's tools server. This tool serves the real one, its
Python, HTML, CSS and JS as they are on disk, against a made-up family
(the Simpsons) in a chosen state of their setup. Nothing else runs: the
stack it asks is a stand-in on loopback ports (`tests/dashboard_host.py`,
the same one the first-run browser test drives), so there is no Docker,
no instance and no network. It is for iterating on a screen's design, by a
person in a browser or by an agent through screenshots.

    # A browser on http://127.0.0.1:42900/dashboard, the chat running:
    uv run --extra test --extra browser python tools/dashboard_preview.py chat

    # As a phone on the home network sees it, in German:
    uv run --extra test --extra browser python tools/dashboard_preview.py signed --phone --de

    # Screenshots at 390 and 1280 px, for an agent to read, then exit:
    uv run --extra test --extra browser python tools/dashboard_preview.py all --shots /tmp/shots

The states are the stand-in's, in the order a family goes through them:
fresh, chat, signed, services, done. In a browser, Start on a tile plays a
short run with steps, and the stacklet runs after it.

A change shows on the next reload: CSS, JS and HTML are read from disk on
every request, and the server restarts itself when a Python file of the
tools server or of `lib/stack` changes. Screenshots wait for the widgets
to render, not for the network to fall quiet: the page keeps a live
connection open.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS_SERVER = REPO / "stacklets" / "core" / "tools-server"
DEFAULT_PORT = 42900
# How the page is addressed from a phone on the home network: not this Mac,
# so links and hints are the ones a phone gets.
PHONE_HOST = "192.168.1.20:42000"
WIDTHS = {"390": {"width": 390, "height": 844}, "1280": {"width": 1280, "height": 900}}
# Long enough for the widgets' scripts to define their elements and fill in.
RENDER_SECS = 0.6


# ── The command ──────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    _paths()
    from tests.dashboard_host import STATES

    parser = argparse.ArgumentParser(description="Serve the dashboard with a made-up family.")
    parser.add_argument("state", nargs="?", default="chat", choices=[*STATES, "all"],
                        help="the family's setup state (all: every state, for --shots)")
    parser.add_argument("--de", action="store_true", help="in German")
    parser.add_argument("--phone", action="store_true", help="as a phone on the home network sees it")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="the browser's port")
    parser.add_argument("--shots", metavar="DIR", help="write screenshots there and exit")
    args = parser.parse_args(argv)

    states = list(STATES) if args.state == "all" else [args.state]
    if not args.shots and len(states) > 1:
        parser.error("'all' is for --shots; a browser shows one state")
    host = _stand_in(states[0], language="de" if args.de else "", phone=args.phone)
    if args.shots:
        for path in _shots(host, states, Path(args.shots), language="de" if args.de else "en",
                           view="phone" if args.phone else "mac"):
            print(path)
        return 0
    _serve(args.port)
    return 0


def create_app():
    """The tools server's app, with the request headers the preview asks
    for: the Host of a phone (`PREVIEW_HOST`) and the browser's language
    (`PREVIEW_LANGUAGE`). The uvicorn factory, so a restart builds it
    again from the code on disk."""
    _paths()
    import server

    return _Headers(server.app, host=os.environ.get("PREVIEW_HOST", ""),
                    language=os.environ.get("PREVIEW_LANGUAGE", ""))


# ── The stand-in ─────────────────────────────────────────────────────────

def _stand_in(state: str, *, language: str, phone: bool):
    """Start the host API and the history in this process and point the
    tools server at them through the environment, which a restarted server
    inherits."""
    from tests.dashboard_host import DashboardHost, serve_history

    host = DashboardHost.in_state(state, root=Path(tempfile.mkdtemp(prefix="dashboard-preview-")))
    os.environ["STACK_API_HOST"] = "127.0.0.1"
    os.environ["STACK_API_PORT"] = str(host.serve())
    os.environ["MEMORY_CARDS_URL"] = serve_history(lambda: host.history)
    os.environ["PREVIEW_HOST"] = PHONE_HOST if phone else ""
    os.environ["PREVIEW_LANGUAGE"] = language
    return host


# ── A browser ────────────────────────────────────────────────────────────

def _serve(port: int) -> None:
    """Serve until Ctrl-C, restarting on a change to the Python it runs."""
    import uvicorn

    print(f"The dashboard: http://127.0.0.1:{port}/dashboard  (Ctrl-C ends it)", flush=True)
    uvicorn.run("dashboard_preview:create_app", factory=True, host="127.0.0.1", port=port,
                app_dir=str(REPO / "tools"), reload=True, log_level="warning",
                reload_dirs=[str(TOOLS_SERVER), str(REPO / "lib" / "stack")])


# ── Screenshots ──────────────────────────────────────────────────────────

def _shots(host, states: list[str], out: Path, *, language: str, view: str) -> list[Path]:
    """One full-page screenshot per state and width, named
    `<state>-<language>-<view>-<width>.png`; the paths, in order."""
    import uvicorn
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    running = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port,
                                            log_level="warning"))
    threading.Thread(target=running.run, daemon=True).start()
    while not running.started:
        time.sleep(0.05)
    written = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            for state in states:
                host.enter(state)
                for width, size in WIDTHS.items():
                    page = browser.new_page(viewport=size)
                    page.goto(f"http://127.0.0.1:{port}/dashboard", wait_until="domcontentloaded")
                    page.wait_for_selector("fs-stack")
                    page.wait_for_timeout(RENDER_SECS * 1000)
                    path = out / f"{state}-{language}-{view}-{width}.png"
                    page.screenshot(path=str(path), full_page=True)
                    page.close()
                    written.append(path)
            browser.close()
    finally:
        running.should_exit = True
    return written


# ── Plumbing ─────────────────────────────────────────────────────────────

class _Headers:
    """ASGI middleware that sets the Host and Accept-Language of every
    request, when given: what a phone on the home network, or a German
    browser, would send."""

    def __init__(self, app, *, host: str, language: str):
        self.app, self.host, self.language = app, host, language

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and (self.host or self.language):
            replaced = {b"host": self.host, b"accept-language": self.language}
            headers = [(k, v) for k, v in scope["headers"] if not (k in replaced and replaced[k])]
            headers += [(k, v.encode()) for k, v in replaced.items() if v]
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


def _paths() -> None:
    """The repository (for the stand-in), the stack's library and the tools
    server's modules, importable as the tools server's container has them."""
    for path in (REPO, REPO / "lib", TOOLS_SERVER):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    sys.exit(main())
