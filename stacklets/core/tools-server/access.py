"""What the tools server answers on which listener, and whether a request
came from the Mac that runs famstack.

The server listens four times. The internal listener is never published:
only the stack's containers reach it, over the Docker network, and it
serves everything. The public listener is the home address, the published
port (42000). Docker's port forwarding gives every request through it the
same source address, whether it comes from this Mac or from a phone on the
Wi-Fi, so an address says nothing about who is calling. The listener does.

The Mac's address is the third listener, published on 127.0.0.1:42002
only. A browser on this Mac reaches it, and so does every container of
the stack, through `host.docker.internal`. So the listener alone is not
the proof: a request through it is the Mac's when it carries the admin
key as well (`devices/admin_key.py`, ADR-023 D23), and anything else
gets the page that asks for the key. It serves every route:

    here(request)      # True only through 127.0.0.1:42002, with the admin key
    serves(PUBLIC_PORT, "/setup")            # True
    serves(PUBLIC_PORT, "/tools/stack/status")  # False: for containers

Why the listener and not the Host header: a header is what the caller
says about itself. Any device on the home network can send
`Host: localhost:42000` to the home address, so a page that showed the
family's passwords for that header showed them to any device. The listener
is a fact of the connection. A request through the home address is never
`here`, the Mac's own browser on an old bookmark of `localhost:42000`
included; that page links to the Mac's address instead.

The wiki's door is the fourth listener, published on the wiki's own
address (port 42070, or the wiki's name in domain mode). It is a home
address too: the same gate asks for a paired device. Core answers every
`/dashboard` path there, the files the unknown page loads included; every
other path goes on to the wiki container (`wiki_door.py`, ADR-023 D11):

    serves(WIKI_PORT, "/people/homer")       # True: forwarded to the wiki

On the home address the persistent links (`/<prefix>/...`) exist, because
the browsers in the house open them, the dashboard (`/dashboard`) with what
it loads, the setup page (`/setup`), and the card pages (`/card/<card id>`,
`memory_ui`) to read, with their media and fonts only
(`cards_page.BROWSER_ROUTES`). Search, logs and status are for the stack's
containers. The Mac's address also serves the card browser's old address
(`/cards`), which sends a browser on to `/card/<card id>`, and the card
door the `./stack` commands use (`/cards/api`).

Every change to the server answers only on the Mac's address: starting a
stacklet, setting the family, answering setup questions, starting the AI's
downloads and asking for the AI's first words, and the setup page that
makes them. A route declares it with `mac_only`, which runs before the
route asks the host API anything:

    @app.post("/dashboard/api/family", dependencies=[Depends(mac_only)])

Through the home address `mac_only` raises `OnlyOnTheMac`; the server
answers it with 403: the page "This happens on the Mac that runs
famstack" to a browser, and `{"error", "mac": "<the Mac's setup page>"}`
to a post, whatever Host header the request sends. The stacker bot starts
stacklets through the host API, not through these routes, so the rule
does not touch it. Reads stay open there to a paired device, so each of the
family's devices sees the stack's state, and a start on the Mac shows live
on each of them.

Pure but for one import: Starlette's `Request`, the annotation by which
FastAPI hands a dependency the request. No route, no app, so the rule is
tested on its own.
"""

from __future__ import annotations

from starlette.requests import Request

__all__ = ["INTERNAL_PORT", "PUBLIC_PORT", "CARDS_PORT", "WIKI_PORT", "CARDS_PREFIX", "DASHBOARD_PREFIX",
           "SETUP_PATH", "serves", "here", "mac_only", "OnlyOnTheMac"]

INTERNAL_PORT = 8000
PUBLIC_PORT = 8001
# The Mac's address: published on 127.0.0.1 only (core's compose file), so
# only a browser or a command on this Mac reaches it. It serves every route,
# and a request through it is `here`. Named for the card browser it served
# first.
CARDS_PORT = 8002
CARDS_PREFIX = "/cards"
# The wiki's door: published on the wiki's address (42070), so a browser's
# device key for the Mac's name reaches it (a cookie holds for every port
# of its host). Behind the same gate as the home address.
WIKI_PORT = 8070
# The card pages, one per card of the family memory, read only: no route
# under it takes a write (`memory_ui`). The prefix names the kind, as the
# chat link `/go/card/<card id>` does, and leaves "memory" free for the
# wiki and a memory dashboard.
CARD_PREFIX = "/card"
# The dashboard: its page, its files and its widgets' data, every route under
# this one prefix (`server.py` and the page take it from here).
DASHBOARD_PREFIX = "/dashboard"
# The setup page (`setup_page.py`): the admin's page, apart from the
# family's dashboard, so it can be limited to this Mac or the admin later
# without touching the family's page. It loads the dashboard's files.
SETUP_PATH = "/setup"


def serves(listener_port: int | None, path: str, link_prefix: str = "go") -> bool:
    """Whether a request on `listener_port` for `path` is answered.

    The internal listener and the Mac's address serve every path; any
    other port counts as the network, so a listener added later is closed
    until said otherwise.

        serves(CARDS_PORT, "/setup")             # True
        serves(PUBLIC_PORT, "/go/docs/42")       # True
        serves(PUBLIC_PORT, "/cards")            # False
    """
    if listener_port == INTERNAL_PORT:
        return True
    if listener_port == CARDS_PORT:
        return True
    # The wiki's door: core keeps the dashboard's paths and forwards every
    # other one to the wiki, only for a paired device (`wiki_door`).
    if listener_port == WIKI_PORT:
        return True
    # The dashboard's reads and the card pages are answered here, to a
    # paired device only (the gate in `devices`). The dashboard's changes
    # are not: each one declares `mac_only`, so the home address only
    # shows. The card pages take no write.
    return (path.startswith(f"/{link_prefix}/") or _under(path, DASHBOARD_PREFIX)
            or path == SETUP_PATH or _under(path, CARD_PREFIX))


def here(request) -> bool:
    """Whether `request` came from the admin's browser on the Mac that runs
    famstack: true only for a request through the Mac's address
    (127.0.0.1:42002) that the gate let in with the admin key
    (`request.state.who`, `devices.admin_key`).

    Reads the listener from the request's ASGI scope (`server`, the
    address and port the connection was accepted on); a request without
    one is not here. Never reads a header: the caller sets those.

        here(request)    # through 127.0.0.1:42002 with the admin key: True
        here(request)    # through <mac>:42000, any Host header: False
    """
    # SAFETY: this decides whether a page shows the family's passwords and
    # takes secret answers. The listener and the admin key decide it, never
    # a header: every container reaches the Mac's listener too, so the
    # gate's verdict (`devices.admin_key`) is asked as well.
    listener = (request.scope.get("server") or (None, None))[1]
    verdict = getattr(getattr(request, "state", None), "who", None)
    return listener == CARDS_PORT and getattr(verdict, "kind", "") == "mac"


class OnlyOnTheMac(Exception):
    """A change that came through the home address: raised by `mac_only`,
    answered by the server with 403 and the page that says where to go.

        POST <mac>:42000/dashboard/api/family    # OnlyOnTheMac("/dashboard/api/family")
    """


def mac_only(request: Request) -> None:
    """Let a request through only when it came from the Mac that runs
    famstack (`here`); raise `OnlyOnTheMac` for any other. A route
    dependency, so it runs before the route reads its body or asks the
    host API anything.

        mac_only(request)    # through 127.0.0.1:42002 with the admin key: returns None
        mac_only(request)    # through <mac>:42000, any Host header: raises OnlyOnTheMac
    """
    # SAFETY: every change to the server goes through here. Through the
    # home address nothing may run: no `stack` command, no file, no speech.
    if not here(request):
        raise OnlyOnTheMac(request.url.path)


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")
