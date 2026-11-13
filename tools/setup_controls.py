"""The setup page's controls in the dashboard preview: click `/setup` through its states.

The preview (`tools/dashboard_preview.py`) serves the real setup page
against a stand-in stack. With `--controls` each page of the setup gets a
panel in the bottom corner, and each button of the panel puts the stand-in
into another state, so the real page shows it:

    jump to     a step: 1 to 8, the phone page (4p), the parts of the AI
                step (5a to 5f) and "Your stack"
    oMLX app    found on this Mac or not
    memory      a 16 or a 32 GB Mac
    speech      on or off
    device      this Mac, an iPhone, an Android phone or another Mac

    uv run --extra test --extra browser python tools/dashboard_preview.py services --controls

The panel is a plain form: a button posts its one control to
`/_preview/controls`, the preview sets the stand-in and answers with the
page of the jump. A worked case: on step 5a, "32 GB" then "5c" shows
"Recommended for this Mac (32 GB)" with three models and speech on;
"Speech off" then leaves the models and turns the switch off.

Why two places hold the state: the stand-in runs in the preview's own
process (the host API and the oMLX app, `tests/dashboard_host.py` and
`tests/dashboard_ai.py`), while the page is served by a server process
that restarts on every change to the code. So `ControlRoom` keeps the
stack's state in the stand-in's process, behind a small route on a
loopback port, and the page's server asks it (`Panel`). The device is not
the stack's state but the browser's, so it is a cookie of the browser,
and `Panel` gives each request the Host and the User-Agent of that device:
the page tells this Mac from another device by them, as on a real install.

The labels are English only: the panel is the preview's own, never part
of a page that ships. Without `--controls` nothing here runs, and the
preview serves the pages as before.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__all__ = ["JUMPS", "DEVICES", "ROUTE", "Jump", "Controls", "ControlRoom", "Panel", "panel"]

# Where the panel's form posts, on the page's server. Outside every path
# the tools server serves.
ROUTE = "/_preview/controls"
# The device's cookie, in the browser that looks.
DEVICE_COOKIE = "fs-preview-device"


# ── The controls ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Jump:
    """Where a jump leads: the family's state (`dashboard_host.STATES`),
    the page's address, and the AI's situation (`AIStandIn.enter`)."""
    state: str
    path: str
    ai: dict


# The steps as the setup page numbers them. Each shows its step in the first
# state where it has something to show: step 2 before the chat runs, step
# 5 once the services run, step 7 once Documents filed a letter.
JUMPS = {
    "1": Jump("chat", "/setup?step=family", {}),
    "2": Jump("fresh", "/setup?step=chat", {}),
    "3": Jump("chat", "/setup?step=mac", {}),
    "4": Jump("signed", "/setup?step=phone", {}),
    "4p": Jump("signed", "/go/app", {}),
    "5a": Jump("services", "/setup?step=ai", {}),
    "5b": Jump("services", "/setup?step=ai&ai=connect", {}),
    "5c": Jump("services", "/setup?step=ai", {"connected": True}),
    "5d": Jump("services", "/setup?step=ai", {"connected": True, "downloading": True}),
    "5e": Jump("done", "/setup?step=ai", {"connected": True}),
    "5f": Jump("done", "/setup?step=ai", {"connected": True, "speech": True}),
    "6": Jump("services", "/setup?step=services", {}),
    "7": Jump("done", "/setup?step=try", {}),
    "8": Jump("done", "/setup?phone=signed", {}),
    "stack": Jump("services", "/setup?step=stack", {}),
}

IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
# The Mac's address on the home network, as another device opens the page.
HOME_NETWORK = "192.168.1.20:42000"
# Each device's label, and the Host and User-Agent its requests carry;
# "" keeps the browser's own. This Mac opens the page at its own address.
DEVICES = {
    "mac": ("This Mac", "", ""),
    "iphone": ("iPhone", HOME_NETWORK, IPHONE),
    "android": ("Android", HOME_NETWORK, ANDROID),
    "othermac": ("Another Mac", HOME_NETWORK, ""),
}


@dataclass(frozen=True)
class Controls:
    """The stack's state the panel sets: the jump ("" for the state the
    preview started in), the oMLX app, the Mac's memory and speech (None:
    the memory's own choice). The device is the browser's (a cookie).

        Controls().changed({"jump": "5c", "memory": "32"})
        # Controls(jump="5c", app=True, memory=32, speech=None)
    """
    jump: str = ""
    app: bool = True
    memory: int = 16
    speech: bool | None = None

    def changed(self, form: dict) -> "Controls":
        """These controls with the ones in `form` (the panel's names and
        values) changed. A new memory takes its own speech choice again,
        unless `form` sets speech too. Unknown values are left out."""
        new = {}
        if form.get("jump") in JUMPS:
            new["jump"] = form["jump"]
        if form.get("app") in ("on", "off"):
            new["app"] = form["app"] == "on"
        if form.get("memory") in ("16", "32"):
            new["memory"], new["speech"] = int(form["memory"]), None
        if form.get("speech") in ("on", "off"):
            new["speech"] = form["speech"] == "on"
        return replace(self, **new)


class ControlRoom:
    """The controls' state in the stand-in's process, and what applies it:
    the family's state on `host` (a `DashboardHost`) and the AI's
    situation on `ai` (an `AIStandIn`).

        room = ControlRoom(host, ai, state="services")
        room.set({"jump": "5c", "memory": "32"})    # "/setup?step=ai"
        url = room.serve()                           # for `Panel`
    """

    def __init__(self, host, ai, *, state: str):
        self.host, self.ai, self.state = host, ai, state
        self.controls = Controls()
        self._lock = threading.Lock()

    def set(self, form: dict) -> str:
        """Change the controls named in `form`, apply them all, and return
        the address of the page to show."""
        with self._lock:
            self.controls = self.controls.changed(form)
            jump = JUMPS.get(self.controls.jump, Jump(self.state, "/setup", {}))
            self.host.jump(jump.state)
            self.ai.enter(**{"app": self.controls.app, "memory": self.controls.memory,
                             "speech": self.controls.speech, **jump.ai})
            return jump.path

    def serve(self) -> str:
        """Answer `GET /` with the controls and `POST /` with a form's
        changes (JSON in, `{"controls", "path"}` out) on a loopback port,
        in daemon threads. Returns its address."""
        room = self

        class Route(BaseHTTPRequestHandler):
            def do_GET(self):
                self._send({"controls": asdict(room.controls)})

            def do_POST(self):
                form = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                path = room.set(form)
                self._send({"controls": asdict(room.controls), "path": path})

            def _send(self, answer: dict):
                body = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # quiet
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Route)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}"


# ── The panel on the page ────────────────────────────────────────────────

class Panel:
    """ASGI middleware for the page's server: it answers the panel's form
    (`ROUTE`), puts the panel into each setup page, and gives each request
    the Host and User-Agent of the device the browser plays.

        app = Panel(server.app, room_url)    # room_url: ControlRoom.serve()
    """

    def __init__(self, app, room_url: str):
        self.app, self.room_url = app, room_url

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        device = _cookie(scope, DEVICE_COOKIE)
        if scope["path"] == ROUTE and scope["method"] == "POST":
            await self._answer_form(receive, send, device)
            return
        scope = _as_device(scope, device)
        if scope["method"] == "GET" and _is_setup(scope["path"]):
            await self._with_panel(scope, receive, send, device)
            return
        await self.app(scope, receive, send)

    async def _answer_form(self, receive, send, device: str) -> None:
        """Apply the posted controls in the stand-in's process and send the
        browser on to the jump's page, with the device's cookie."""
        form = dict(urllib.parse.parse_qsl((await _body(receive)).decode()))
        answer = await asyncio.to_thread(_ask, self.room_url, form)
        device = form["device"] if form.get("device") in DEVICES else device
        await send({"type": "http.response.start", "status": 303, "headers": [
            (b"location", answer["path"].encode()),
            (b"set-cookie", f"{DEVICE_COOKIE}={device or 'mac'}; Path=/; SameSite=Lax".encode())]})
        await send({"type": "http.response.body", "body": b""})

    async def _with_panel(self, scope, receive, send, device: str) -> None:
        """The page as the app sends it, with the panel before `</body>`
        when it is HTML."""
        start, chunks = {}, []

        async def keep(message):
            if message["type"] == "http.response.start":
                start.update(message)
            else:
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, keep)
        body = b"".join(chunks)
        headers = [(k, v) for k, v in start.get("headers", []) if k != b"content-length"]
        if any(k == b"content-type" and v.startswith(b"text/html") for k, v in headers):
            controls = Controls(**(await asyncio.to_thread(_ask, self.room_url, None))["controls"])
            body = body.replace(b"</body>", panel(controls, device or "mac").encode() + b"</body>", 1)
        await send({**start, "headers": [*headers, (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def panel(controls: Controls, device: str) -> str:
    """The panel's HTML: one form, a button per control, the current ones
    pressed. Its styles come with it, on the dashboard's tokens.

        panel(Controls(jump="5c"), "mac")    # '<details class="preview-controls" ...'
    """
    # Speech left to the memory is on for 32 GB and off for 16 GB (`MAC_SIZES`).
    speech = "on" if (controls.memory == 32 if controls.speech is None else controls.speech) else "off"
    groups = [
        ("Jump to", "jump", [(j, j) for j in JUMPS], controls.jump),
        ("Who looks at the page", "device", [(d, label) for d, (label, _, _) in DEVICES.items()],
         device),
        ("Step 5, the AI: oMLX app found", "app", [("on", "Yes"), ("off", "No")],
         "on" if controls.app else "off"),
        ("The Mac", "memory", [("16", "16 GB Mac"), ("32", "32 GB Mac")], str(controls.memory)),
        ("Speech", "speech", [("on", "On"), ("off", "Off")], speech),
    ]
    body = "".join(_group(*group) for group in groups)
    return (f'<details class="preview-controls" id="preview-controls" open>'
            f'<summary>Preview controls</summary>'
            f'<p class="preview-note">Not part of the design. Sets the state the page shows.</p>'
            f'<form method="post" action="{ROUTE}">{body}</form></details>{_STYLE}')


# ── Details ──────────────────────────────────────────────────────────────

def _group(label: str, name: str, options: list[tuple[str, str]], current: str) -> str:
    buttons = "".join(
        f'<button class="preview-b" type="submit" name="{name}" value="{escape(value)}" '
        f'aria-pressed="{"true" if value == current else "false"}">{escape(text)}</button>'
        for value, text in options)
    return f'<div class="preview-group"><span>{escape(label)}</span><div>{buttons}</div></div>'


def _is_setup(path: str) -> bool:
    """The pages the panel goes on: the setup page and the phone page's."""
    return path == "/setup" or path.startswith("/go/app")


def _as_device(scope: dict, device: str) -> dict:
    """The request as `device` sends it: its Host and User-Agent."""
    _, host, agent = DEVICES.get(device, DEVICES["mac"])
    replaced = {b"host": host, b"user-agent": agent}
    if not host and not agent:
        return scope
    headers = [(k, v) for k, v in scope["headers"] if not replaced.get(k)]
    headers += [(k, v.encode()) for k, v in replaced.items() if v]
    return {**scope, "headers": headers}


def _cookie(scope: dict, name: str) -> str:
    for key, value in scope["headers"]:
        if key == b"cookie":
            for part in value.decode().split(";"):
                k, _, v = part.strip().partition("=")
                if k == name:
                    return v
    return ""


async def _body(receive) -> bytes:
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            return body


def _ask(url: str, form: dict | None) -> dict:
    """The control room's answer: its controls, or with `form` the
    controls after the change and the page to show."""
    request = urllib.request.Request(url, data=None if form is None else json.dumps(form).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


# The mock panel's look: a dashed highlight border and a mono face, so no
# reviewer reads it as part of the design.
_STYLE = """<style>
.preview-controls { position: fixed; right: 12px; bottom: 12px; z-index: 50;
  width: min(330px, calc(100vw - 24px)); max-height: calc(100vh - 24px); overflow: auto;
  border: 2px dashed var(--highlight); border-radius: 14px; padding: 0 12px;
  background: var(--glass-solid); color: var(--text); box-shadow: var(--glass-shadow);
  font: 500 12px/1.4 var(--mono); }
.preview-controls:not([open]) { width: auto; }
.preview-controls summary { min-height: 40px; display: flex; align-items: center; cursor: pointer;
  font: 700 12px/1 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; }
.preview-controls form { display: grid; gap: 10px; padding-bottom: 12px; }
.preview-note { margin: 0 0 8px; color: var(--text-muted); }
.preview-group { display: grid; gap: 6px; }
.preview-group > span { color: var(--text-muted); }
.preview-group > div { display: flex; flex-wrap: wrap; gap: 4px; }
.preview-controls button.preview-b { min-width: 34px; min-height: 30px; padding: 0 8px;
  border-radius: 8px; cursor: pointer; border: 1px solid var(--line); background: var(--fill);
  color: var(--text); font: 600 12px/1 var(--mono); }
.preview-controls button.preview-b:hover { background: var(--fill-strong); }
.preview-controls button.preview-b[aria-pressed="true"] { background: var(--text);
  color: var(--canvas); }
</style>"""
