"""What the tools server answers on which listener.

The server listens twice. The internal listener is never published: only the
stack's containers reach it, over the Docker network, and it serves
everything. The public listener is the published port (42000). Docker's port
forwarding gives every request through it the same source address, whether
it comes from this Mac or from a phone on the Wi-Fi, so an address says
nothing about who is calling. The listener does.

On the public listener the persistent links (`/<prefix>/...`) exist,
because the browsers in the house open them, the dashboard (`/dashboard`)
with what it loads, the setup page (`/setup`), and the card pages
(`/card/<card id>`, `memory_ui`) with their media and fonts
(`cards_page.BROWSER_ROUTES`). Search, logs and status are for the
stack's containers. A third listener, published on this Mac only, serves the card
browser's old address (`/cards`), which sends a browser on to `/card/<card id>`,
and the host's path for writes to the memory (`/cards/api`); it serves
`/card` too, so the old address forwards on the same origin.

Pure: no web framework, so the rule is tested on its own.
"""

from __future__ import annotations

INTERNAL_PORT = 8000
PUBLIC_PORT = 8001
# The family memory's card browser. Published on 127.0.0.1 only, so only
# a browser on this Mac reaches it: nothing on it knows who is looking yet.
CARDS_PORT = 8002
CARDS_PREFIX = "/cards"
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

    Only the internal listener is trusted by name; any other port counts as
    the network, so a listener added later is closed until said otherwise.
    """
    if listener_port == INTERNAL_PORT:
        return True
    if listener_port == CARDS_PORT:
        return _under(path, CARDS_PREFIX) or _under(path, CARD_PREFIX)
    # The dashboard is open to the network until the login lands: anyone on
    # the home network sees what changed in the memory, card titles and who
    # changed them included. Decided 2026-10-21 for a developer machine.
    # Since 2026-10-26 anyone there can also add a stacklet from it
    # (`POST /dashboard/api/jobs`, live_routes.py), and since 2026-10-27 set
    # the family (`POST /dashboard/api/family`, server.py), until the backlog
    # entry "Basic security for the tools server" closes both. A page from
    # another site cannot post to them: they take only JSON (server.py).
    # The card pages are open to read on the home network for the
    # same reason; they take no write.
    return (path.startswith(f"/{link_prefix}/") or _under(path, DASHBOARD_PREFIX)
            or path == SETUP_PATH or _under(path, CARD_PREFIX))


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")
