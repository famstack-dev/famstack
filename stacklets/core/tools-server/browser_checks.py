"""The checks a browser's request passes before any route of the tools
server runs, and the headers every answer carries.

One ASGI middleware, added once in `server.py`, around every listener:

    app.add_middleware(BrowserChecks, home_url=lambda: os.environ.get("LINK_BASE_URL", ""))

It does three things, in this order:

1. **Host.** Each listener answers only the names it is reached by
   (`allowlist`). A page on another site can point its own name at the
   Mac's address (DNS rebinding); its requests then carry that name, and
   are refused with 400 before any route runs.
2. **Origin.** A write (any method but GET, HEAD and OPTIONS) that a
   browser sends from a page of another origin is refused with 403. A
   browser sends `Origin` with every write; a program such as `./stack`
   sends none, and passes.
3. **Headers.** Every answer carries `X-Content-Type-Options: nosniff` and
   `Referrer-Policy: no-referrer` (the allow page's and the phone page's
   addresses carry a code, which a link must not pass on), and every HTML page a Content Security Policy (`POLICY`) that runs only
   core's own script files, in no frame (`frame-ancestors 'none'`).

Worked example, a phone on the home network and a page on the internet:

    GET  /dashboard  Host: 192.168.1.20:42000                     200, with the policy
    GET  /dashboard  Host: rebind.example:42000                   400
    POST /dashboard/api/family  Host: localhost:42002
         Origin: http://other.example                             403, nothing ran

Why a middleware of its own and not `access.py`: the listener decides who a
request is (`access.here`), and these checks never grant anything. A header
is what the caller says about itself, so it may only refuse: a request that
passes here is still only as trusted as its listener makes it.

The names of each listener:

    the home address (42000)   the Mac's address on the home network and the
                               name core's links use (`LINK_BASE_URL`: the
                               `[core] host` or the domain), `localhost`, any
                               IP address, and any `.local` name
    the wiki's door (42070)    the names of the home address, and in domain
                               mode the wiki's names, `memory.<domain>` and
                               `wiki.<domain>` (`wiki_door.py`)
    the Mac's address (42002)  localhost, 127.0.0.1, [::1]
    the internal listener      stack-core-tools, the container's name on the
                               stack's network

The home address takes any IP address and any `.local` name. Rebinding
needs a name its owner controls; an address is no name, and `.local` names
are answered only on the home network (Bonjour). The container cannot know
the Mac's Bonjour name, and the Mac's address on the Wi-Fi can change
before core is started again, so an exact list would refuse the family's
own phones. `localhost` stays on the home address because core's health
check (`stacklet.toml`, `[health]`) and old bookmarks on the Mac use it;
`access.here` stays false for it there.

`cookie_flags` is the one place that says how core sets a cookie:
HttpOnly and SameSite=Lax always, Secure when the browser reached core
over HTTPS, and in domain mode `Domain=<domain>`, so the wiki's names get
the device key too (ADR-023 D11). No script of core reads a cookie.

The cap on open event streams is next to the streams (`live_routes.py`).
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from access import CARDS_PORT, INTERNAL_PORT, WIKI_PORT

__all__ = ["BrowserChecks", "Allowlist", "allowlist", "POLICY", "cookie_flags"]

# What a page of core may load and run: its own files only. Styles may be
# inline (the pages set `style` attributes); scripts may not, so a script
# that reached a page through its text never runs. `data:` images and audio
# are the page's own: the empty favicon and the AI's first words as MP3.
POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "media-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


# ── The middleware ───────────────────────────────────────────────────────

class BrowserChecks:
    """The ASGI middleware: refuse a request by its Host or its Origin, and
    add the security headers to every answer.

    `home_url` returns core's link base (`LINK_BASE_URL`, such as
    `http://192.168.1.20:42000/go`), read on each request like the rest of
    core's settings, so the home address answers by the name core links to.

        app.add_middleware(BrowserChecks, home_url=lambda: os.environ.get("LINK_BASE_URL", ""))
    """

    def __init__(self, app, *, home_url: Callable[[], str]):
        self.app = app
        self.home_url = home_url

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        listener = (scope.get("server") or (None, None))[1]
        host = headers.get("host", "")
        # SAFETY: a request by a name the listener does not answer to, or a
        # write from another site's page, is answered here and never reaches
        # a route: no `stack` command, no read of the family's data.
        if not allowlist(listener, self.home_url()).allows(host):
            await _refuse(send, 400, "This server does not answer to that name.")
            return
        if scope["method"] not in _READS and not _same_origin(headers.get("origin"), host):
            await _refuse(send, 403, "A page of another site cannot change this server.")
            return
        await self.app(scope, receive, _with_headers(send))


# ── Host: the names each listener answers to ─────────────────────────────

@dataclass(frozen=True)
class Allowlist:
    """The names one listener answers to: `names` exactly, and with
    `addresses_and_local` also any IP address and any `.local` name.

        Allowlist(frozenset({"localhost"})).allows("localhost:42002")      # True
        Allowlist(frozenset({"localhost"})).allows("rebind.example:42002") # False
    """

    names: frozenset[str]
    addresses_and_local: bool = False

    def allows(self, host: str) -> bool:
        """Whether a request whose Host header is `host` is answered. The
        port is not compared: Docker publishes each listener on another
        port than it listens on, and the port of a name says nothing about
        who chose the name. An empty or malformed Host is refused."""
        name = _hostname(host)
        if not name:
            return False
        if name in self.names:
            return True
        return self.addresses_and_local and (_is_address(name) or name.endswith(".local"))


def allowlist(listener: int | None, home_url: str) -> Allowlist:
    """The names `listener` answers to; `home_url` is core's link base,
    whose host names the home address.

        allowlist(CARDS_PORT, "")                                  # localhost, 127.0.0.1, ::1
        allowlist(PUBLIC_PORT, "https://simpson.family/go").names  # simpson.family, localhost
        allowlist(INTERNAL_PORT, "").names                         # stack-core-tools
        allowlist(WIKI_PORT, "https://simpson.family/go").names    # also memory.simpson.family,
                                                                   # wiki.simpson.family

    Any listener but the Mac's address and the internal one counts as the
    home address, as `access.serves` counts it as the network.
    """
    if listener == CARDS_PORT:
        return Allowlist(MAC_NAMES)
    if listener == INTERNAL_PORT:
        return Allowlist(INTERNAL_NAMES)
    linked = urlsplit(home_url).hostname or ""
    names = set(HOME_NAMES) | ({linked} if linked else set())
    if listener == WIKI_PORT and linked:
        names |= {f"{sub}.{linked}" for sub in WIKI_NAMES}
    return Allowlist(frozenset(names), addresses_and_local=True)


# ── Cookies ──────────────────────────────────────────────────────────────

def cookie_flags(request, home_url: str = "") -> dict:
    """The flags of every cookie core sets, as `Response.set_cookie` takes
    them: HttpOnly and SameSite=Lax always, Secure when the browser came
    over HTTPS (the proxy in domain mode says so in `X-Forwarded-Proto`).
    `home_url` is core's link base, `LINK_BASE_URL` when not given.

        response.set_cookie("fs-setup-phone", "signed", path="/", **cookie_flags(request))

    In domain mode (the link base is a name without a port, and the
    browser came by that name) the cookie names the family's domain, so
    the wiki's names, `memory.<domain>` and `wiki.<domain>`, get it too:

        cookie_flags(request at simpson.family, "https://simpson.family/go")
        # {..., "domain": "simpson.family"}

    The forwarded scheme is a header, and a header may only take away: a
    caller that claims HTTPS gets a cookie its browser keeps for HTTPS.
    """
    over_https = (request.url.scheme == "https"
                  or request.headers.get("x-forwarded-proto", "").lower() == "https")
    flags = {"httponly": True, "samesite": "lax", "secure": over_https}
    domain = _family_domain(home_url or os.environ.get("LINK_BASE_URL", ""))
    asked = _hostname(request.headers.get("host", ""))
    # A browser refuses a cookie for a domain the page is not under, so the
    # domain is named only for a request by the family's domain itself.
    if domain and (asked == domain or asked.endswith("." + domain)):
        flags["domain"] = domain
    return flags


def _family_domain(home_url: str) -> str:
    """The family's domain in domain mode: the link base's host when it is
    a name without a port; "" in port mode (an address, a `.local` name,
    `localhost`, or any name with a port)."""
    parts = urlsplit(home_url)
    try:
        name, port = parts.hostname or "", parts.port
    except ValueError:
        return ""
    if not name or port or _is_address(name) or name.endswith(".local") or name in HOME_NAMES:
        return ""
    return name


# ── The names, and how a Host header is read ─────────────────────────────

# The Mac's address is published on 127.0.0.1 only.
MAC_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
# The container's name on the stack's network (core's compose file), which
# Open WebUI's tool calls use.
INTERNAL_NAMES = frozenset({"stack-core-tools"})
# The home address: the health check and the Mac's browser use localhost.
HOME_NAMES = frozenset({"localhost"})
# The wiki's names under the family's domain (memory's `caddy.snippet`).
WIKI_NAMES = ("memory", "wiki")


def _hostname(host: str) -> str:
    """The name in a Host header, lowercased, without the port or the
    brackets of an IPv6 address: `[::1]:42002` gives `::1`."""
    try:
        return (urlsplit(f"//{host.strip()}").hostname or "").rstrip(".")
    except ValueError:
        return ""


def _is_address(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


# ── Origin: a write comes from a page of this server ─────────────────────

_READS = ("GET", "HEAD", "OPTIONS")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _same_origin(origin: str | None, host: str) -> bool:
    """Whether a write with this `Origin` header may run on a request to
    `host`. No Origin: a program, not a page, so yes. `null` (a sandboxed
    frame, a file) or another host and port: no. The scheme is not
    compared, because behind the proxy that serves HTTPS the server itself
    is reached over HTTP."""
    if origin is None:
        return True
    parts = urlsplit(origin.strip())
    if not parts.hostname or parts.scheme not in _DEFAULT_PORTS:
        return False
    try:
        origin_port = parts.port or _DEFAULT_PORTS[parts.scheme]
        sent = urlsplit(f"//{host.strip()}")
        host_port = sent.port or _DEFAULT_PORTS[parts.scheme]
    except ValueError:
        return False
    return (parts.hostname, origin_port) == (sent.hostname, host_port)


# ── Headers: what every answer carries ───────────────────────────────────

def _with_headers(send):
    """`send`, with `nosniff` and `no-referrer` on every answer and the
    policy on every HTML page. A header the route set already (the card door passes on the
    memory's own `nosniff`) is kept as it is."""
    async def sending(message):
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            present = {name.lower() for name, _ in headers}
            kind = dict(headers).get(b"content-type", b"").lower()
            if b"x-content-type-options" not in present:
                headers.append((b"x-content-type-options", b"nosniff"))
            if b"referrer-policy" not in present:
                headers.append((b"referrer-policy", b"no-referrer"))
            if kind.startswith(b"text/html") and b"content-security-policy" not in present:
                headers.append((b"content-security-policy", POLICY.encode()))
            message = {**message, "headers": headers}
        await send(message)
    return sending


def _headers(scope) -> dict[str, str]:
    return {name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers", [])}


async def _refuse(send, status: int, error: str) -> None:
    body = ('{"error": "%s"}' % error).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"x-content-type-options", b"nosniff")]})
    await send({"type": "http.response.body", "body": body})
