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

    # The setup page instead of the dashboard:
    uv run --extra test --extra browser python tools/dashboard_preview.py signed --page setup

    # The setup page with the panel that clicks it through its states
    # (`setup_controls.py`): a step, the oMLX app, the memory, speech, the device:
    uv run --extra test --extra browser python tools/dashboard_preview.py services --controls

    # Screenshots at 390 and 1280 px, for an agent to read, then exit:
    uv run --extra test --extra browser python tools/dashboard_preview.py all --shots /tmp/shots

The states are the stand-in's, in the order a family goes through them:
fresh, chat, signed, services, done. In a browser, Start on a tile plays a
short run with steps, and the stacklet runs after it. Screenshots take both
pages: the dashboard as `<state>-...png`, the setup page as
`setup-<state>-...png`, plus the steps a state hides behind the current
one (`SETUP_SHOTS`): the chat's service card once it runs, the three services before and
after they run, "Your stack", the AI's terminal box, each part of the AI step against a stand-in for an oMLX
(`AI_SHOTS`, on the AI's stand-in `tests/dashboard_ai.py`: a 16 GB Mac
with speech off, a 32 GB Mac with speech on), the AI's check of its
answer: not asked yet, failed and passed (`AI_ANSWERED`), "Ready" once the
phone is confirmed,
and the chat on the phone: step 3 before and after a phone opened the
phone page (`PHONE_REACHED`), while the Mac's firewall keeps phones out
(`FIREWALL_BLOCKS`), once the admin signed in with the phone app
(`PHONE_APP`), and that page (`/go/app`) and the sign-in guide
(`/go/app/sign-in`) as an Android phone and as a laptop see them.

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

# This script's own folder, importable as the script runs and as uvicorn loads it.
from setup_controls import ANDROID

REPO = Path(__file__).resolve().parents[1]
TOOLS_SERVER = REPO / "stacklets" / "core" / "tools-server"
DEFAULT_PORT = 42900
# How the page is addressed from a phone on the home network: not this Mac,
# so links and hints are the ones a phone gets.
PHONE_HOST = "192.168.1.20:42000"
# The Mac's address on the home network, in `[core] host`: the chat's
# address on step 3 and the phone's page is the one a phone gets.
HOME_NETWORK = PHONE_HOST.split(":")[0]
WIDTHS = {"390": {"width": 390, "height": 844}, "1280": {"width": 1280, "height": 900}}
# Long enough for the widgets' scripts to define their elements and fill in.
RENDER_SECS = 0.6
# The setup page's addresses a state is shot at, by a name for the file:
# its current step, and a step that only shows when asked for.
SETUP_SHOTS = {"chat": {"": "/setup", "family": "/setup?step=family", "chat": "/setup?step=chat"},
               "signed": {"": "/setup", "services": "/setup?step=services",
                          "phone": "/setup?step=phone", "phone-reached": "/setup?step=phone",
                          "phone-firewall": "/setup?step=phone",
                          "phone-app": "/setup?step=phone",
                          "app-android": "/go/app", "app-laptop": "/go/app",
                          "sign-in-android": "/go/app/sign-in", "sign-in-laptop": "/go/app/sign-in"},
               "services": {"": "/setup", "ai": "/setup?step=ai",
                            "ai-install": "/setup?step=ai&ai=install", "ai-other": "/setup?step=ai&ai=other",
                            "ai-managed-pending": "/setup?step=ai",
                            "services": "/setup?step=services", "stack": "/setup?step=stack"},
               "done": {"": "/setup", "ai-not-asked": "/setup?step=ai",
                        "ai-no-answer": "/setup?step=ai", "ai-ready": "/setup?step=ai",
                        "ai-server-not-asked": "/setup?step=ai",
                        "ai-server-no-answer": "/setup?step=ai",
                        "ai-server-ready": "/setup?step=ai", "ai-server-ready-32": "/setup?step=ai",
                        "ai-first-words": "/setup?step=ai", "ready": "/setup?phone=signed"}}

# What `[ai]` names in each AI shot (`AIStandIn.enter`): the oMLX the stack
# manages or a server elsewhere, a 16 GB Mac unless named. A shot not named
# here has nothing in `[ai]`: the two routes.
AI_SHOTS = {"ai-managed-pending": {"ai": "managed"},
            "ai-not-asked": {"ai": "managed"},
            "ai-no-answer": {"ai": "managed"},
            "ai-ready": {"ai": "managed"},
            "ai-first-words": {"ai": "managed", "memory": 32},
            "ai-server-not-asked": {"ai": "external"},
            "ai-server-no-answer": {"ai": "external"},
            "ai-server-ready": {"ai": "external"},
            "ai-server-ready-32": {"ai": "external", "memory": 32}}
# What the AI answered the stack's one short question, kept in the instance
# (`stacklets/ai/answer.py`) as its run keeps it, in each shot of a
# finished setup: the dashboard ("done"), "Ready" and the AI's parts. A shot
# not named here has no answer for its `[ai]`: "Ask your AI a first question".
ANSWERED = {"ok": True, "model": "mlx-community/Qwen3.5-9B-MLX-4bit"}
NO_ANSWER = {"ok": False, "reason": "server", "model": ANSWERED["model"],
             "server_error": "HTTP 500: Failed to load the default metallib. library not found"}
AI_ANSWERED = {"done": ANSWERED, "ready": ANSWERED, "ai-ready": ANSWERED,
               "ai-first-words": ANSWERED, "ai-server-ready": ANSWERED,
               "ai-server-ready-32": ANSWERED,
               "ai-no-answer": NO_ANSWER, "ai-server-no-answer": NO_ANSWER}
PAGES = {"dashboard": "/dashboard", "setup": "/setup"}
# The shots taken after a phone opened the phone page (`phone_reach.py`).
PHONE_REACHED = {"phone-reached", "phone-app"}
# The shots taken while the Mac's firewall keeps phones out (`stack core firewall`).
FIREWALL_BLOCKS = {"phone-firewall"}
# The shots taken after the admin signed in with Element X on a phone, as
# `stack messages users` lists that sign-in.
PHONE_APP = {"phone-app"}
# The device a shot's browser names, by the shot's step; a desktop Chrome otherwise.
USER_AGENTS = {"app-android": ANDROID, "sign-in-android": ANDROID}


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
    parser.add_argument("--page", choices=list(PAGES), default="dashboard",
                        help="the page a browser opens (screenshots take both)")
    parser.add_argument("--shots", metavar="DIR", help="write screenshots there and exit")
    parser.add_argument("--controls", action="store_true",
                        help="a panel on the setup pages that sets the state they show")
    args = parser.parse_args(argv)

    states = list(STATES) if args.state == "all" else [args.state]
    if not args.shots and len(states) > 1:
        parser.error("'all' is for --shots; a browser shows one state")
    host = _stand_in(states[0], language="de" if args.de else "", phone=args.phone)
    if args.controls and not args.shots:
        _controls(host, states[0])
    if args.shots:
        for path in _shots(host, states, Path(args.shots), language="de" if args.de else "en",
                           view="phone" if args.phone else "mac"):
            print(path)
        return 0
    _serve(args.port, PAGES[args.page])
    return 0


def create_app():
    """The tools server's app, as the listener of the address the preview
    shows: the home address with a phone's Host (`PREVIEW_HOST` set), else
    the Mac's address; with the browser's language (`PREVIEW_LANGUAGE`),
    and the setup controls' panel when the stand-in serves them
    (`PREVIEW_CONTROLS_URL`). The uvicorn factory, so a restart builds it
    again from the code on disk."""
    _paths()
    import server
    import setup_controls
    from access import CARDS_PORT, PUBLIC_PORT

    room = os.environ.get("PREVIEW_CONTROLS_URL", "")
    app = setup_controls.Panel(server.app, room) if room else server.app
    phone_host = os.environ.get("PREVIEW_HOST", "")
    return _Headers(app, host=phone_host, language=os.environ.get("PREVIEW_LANGUAGE", ""),
                    listener=PUBLIC_PORT if phone_host else CARDS_PORT,
                    device_key=os.environ.get("PREVIEW_DEVICE_KEY", ""),
                    admin_key=os.environ.get("PREVIEW_ADMIN_KEY", ""))


# ── The stand-in ─────────────────────────────────────────────────────────

def _stand_in(state: str, *, language: str, phone: bool):
    """Start the host API and the history in this process and point the
    tools server at them through the environment, which a restarted server
    inherits."""
    from tests.dashboard_host import DashboardHost, serve_history

    host = DashboardHost.in_state(state, root=Path(tempfile.mkdtemp(prefix="dashboard-preview-")),
                                  mac=HOME_NETWORK)
    os.environ["STACK_API_HOST"] = "127.0.0.1"
    os.environ["STACK_API_PORT"] = str(host.serve())
    os.environ["MEMORY_CARDS_URL"] = serve_history(lambda: host.history)
    os.environ["PREVIEW_HOST"] = PHONE_HOST if phone else ""
    os.environ["PREVIEW_LANGUAGE"] = language
    # Core's link base, as its `.env` renders it: step 3's QR code holds it.
    os.environ["LINK_BASE_URL"] = f"http://{PHONE_HOST}/go"
    # The list of paired devices, on the throwaway instance. The phone is
    # the admin's, paired before: the home address opens for paired
    # devices only.
    os.environ["DEVICES_FILE"] = str(host.root / ".stack" / "devices" / "devices.toml")
    # The admin's phone is paired either way: `--phone` shows every page as
    # it, and the setup controls' phones carry its key too.
    os.environ["PREVIEW_PHONE_KEY"] = _paired_phone(host.root)
    os.environ["PREVIEW_DEVICE_KEY"] = os.environ["PREVIEW_PHONE_KEY"] if phone else ""
    # The Mac's address opens to a browser with the admin key only, as
    # the menu bar app's "Setup…" leaves it in the cookie.
    from stack.devices import admin_key
    os.environ["PREVIEW_ADMIN_KEY"] = "" if phone else admin_key.ensure(host.root)
    return host


def _paired_phone(root: Path) -> str:
    """Pair a phone for the admin on the instance at `root`, as `stack
    devices add` does after the Mac's QR code was opened; its key."""
    import secrets
    from datetime import datetime

    from stack import devices
    from tests.dashboard_host import FAMILY

    key = secrets.token_urlsafe(32)
    admin = next(p["id"] for p in FAMILY["people"] if p["role"] == "admin")
    devices.add(root, person=admin, name="iPhone", key_hash=devices.hash_of(key),
                now=datetime.now().astimezone())
    return key


def _controls(host, state: str) -> None:
    """Stand the AI in and serve the controls' state in this process,
    where the stand-in runs; the page's server reaches it through the
    environment (`setup_controls.py`)."""
    from setup_controls import ControlRoom
    from tests.dashboard_ai import AIStandIn

    room = ControlRoom(host, AIStandIn(host), state=state)
    room.set({})
    os.environ["PREVIEW_CONTROLS_URL"] = room.serve()


# ── A browser ────────────────────────────────────────────────────────────

def _serve(port: int, path: str) -> None:
    """Serve until Ctrl-C, restarting on a change to the Python it runs."""
    import uvicorn

    print(f"The page: http://127.0.0.1:{port}{path}  (Ctrl-C ends it)", flush=True)
    uvicorn.run("dashboard_preview:create_app", factory=True, host="127.0.0.1", port=port,
                app_dir=str(REPO / "tools"), reload=True, log_level="warning",
                reload_dirs=[str(TOOLS_SERVER), str(REPO / "lib" / "stack")])


# ── Screenshots ──────────────────────────────────────────────────────────

def _shots(host, states: list[str], out: Path, *, language: str, view: str) -> list[Path]:
    """Full-page screenshots per state and width: the dashboard as
    `<state>-<language>-<view>-<width>.png`, the setup page as
    `setup-<state>[-<step>]-...png`; the paths, in order."""
    import uvicorn
    from datetime import datetime, timezone
    from playwright.sync_api import sync_playwright

    import tomllib

    import phone_reach
    from stacklets.ai import answer
    from tests import dashboard_host

    out.mkdir(parents=True, exist_ok=True)
    from tests.dashboard_ai import AIStandIn

    ai = AIStandIn(host)
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
                shots = {state: ("/dashboard", "fs-status")}
                agents = {}
                for step, path in SETUP_SHOTS.get(state, {"": "/setup"}).items():
                    name = f"setup-{state}" + (f"-{step}" if step else "")
                    # The home address answers the setup with "This happens on the Mac".
                    setup_ready = ".setup-rail" if view == "mac" else ".only-on-the-mac"
                    shots[name] = (path, ".phone-main" if path.startswith("/go/app") else setup_ready)
                    agents[name] = USER_AGENTS.get(step, "")
                for name, (path, ready) in shots.items():
                    phone_reach.forget()
                    if any(name.endswith(f"-{step}") for step in PHONE_REACHED):
                        phone_reach.record(here=False, now=datetime.now(timezone.utc))
                    blocked = any(name.endswith(f"-{step}") for step in FIREWALL_BLOCKS)
                    host.firewall = dashboard_host.FIREWALL_BLOCKS if blocked else dashboard_host.FIREWALL_OFF
                    signed = [dashboard_host.SIGN_INS["browser"], dashboard_host.SIGN_INS["phone"]]
                    host.sign_ins = ({"homer": signed}
                                     if any(name.endswith(f"-{step}") for step in PHONE_APP) else {})
                    step = name.removeprefix(f"setup-{state}-")
                    ai.enter(**AI_SHOTS.get(step, {}))
                    (host.root / answer.FILE).unlink(missing_ok=True)
                    if step in AI_ANSWERED:
                        toml = tomllib.loads((host.root / "stack.toml").read_text())
                        answer.keep(host.root, toml.get("ai", {}), AI_ANSWERED[step])
                    for width, size in WIDTHS.items():
                        written.append(_shot(browser, f"http://127.0.0.1:{port}{path}", ready, size,
                                             out / f"{name}-{language}-{view}-{width}.png",
                                             user_agent=agents.get(name, "")))
            browser.close()
    finally:
        running.should_exit = True
        ai.close()
    return written


def _shot(browser, url: str, ready: str, size: dict, path: Path, *, user_agent: str = "") -> Path:
    """One full-page screenshot of `url` once `ready` is on the page, in a
    page of its own, so no cookie carries over from one shot to the next.
    `user_agent` names the device the browser claims to be ("" for Chrome's own)."""
    context = browser.new_context(viewport=size, **({"user_agent": user_agent} if user_agent else {}))
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector(ready)
    page.wait_for_timeout(RENDER_SECS * 1000)
    page.screenshot(path=str(path), full_page=True)
    context.close()
    return path


# ── Plumbing ─────────────────────────────────────────────────────────────

class _Headers:
    """ASGI middleware that makes every request look as it does on one of
    the tools server's addresses: it came through `listener` (the home
    address's or the Mac's, which `access.here` reads), with the Host and
    Accept-Language given: what a phone on the home network, or a German
    browser, would send; on the home address with the paired phone's
    device key as its cookie, on the Mac's address with the admin key."""

    def __init__(self, app, *, host: str, language: str, listener: int, device_key: str = "",
                 admin_key: str = ""):
        self.app, self.host, self.language, self.listener = app, host, language, listener
        self.cookie = (f"stack-device={device_key}" if device_key
                       else f"stack-admin={admin_key}" if admin_key else "")

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            replaced = {b"host": self.host, b"accept-language": self.language}
            headers = [(k, v) for k, v in scope["headers"] if not (k in replaced and replaced[k])]
            headers += [(k, v.encode()) for k, v in replaced.items() if v]
            # The key goes beside the browser's own cookies, which keep the
            # setup controls' device (`setup_controls.DEVICE_COOKIE`).
            if self.cookie:
                cookies = [v.decode() for k, v in headers if k == b"cookie"] + [self.cookie]
                headers = [(k, v) for k, v in headers if k != b"cookie"]
                headers.append((b"cookie", "; ".join(cookies).encode()))
            server = (scope.get("server") or ("127.0.0.1", None))[0]
            scope = {**scope, "headers": headers, "server": (server, self.listener)}
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
