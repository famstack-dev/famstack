"""What the tools server answers on which listener.

The server listens twice. The internal listener is never published: only the
stack's containers reach it, over the Docker network, and it serves
everything. The public listener is the published port (42000). Docker's port
forwarding gives every request through it the same source address, whether
it comes from this Mac or from a phone on the Wi-Fi, so an address says
nothing about who is calling. The listener does.

On the public listener the persistent links (`/<prefix>/...`) exist,
because the browsers in the house open them, and the dashboard
(`/dashboard`) with what it loads. Search, logs and status are for the
stack's containers. A third listener serves the card browser (`/cards`),
published on this Mac only.

Pure: no web framework, so the rule is tested on its own.
"""

from __future__ import annotations

INTERNAL_PORT = 8000
PUBLIC_PORT = 8001
# The family memory's card browser. Published on 127.0.0.1 only, so only
# a browser on this Mac reaches it: nothing on it knows who is looking yet.
CARDS_PORT = 8002
CARDS_PREFIX = "/cards"
# The dashboard: its page, its files and its widgets' data, every route under
# this one prefix (`server.py` and the page take it from here).
DASHBOARD_PREFIX = "/dashboard"


def serves(listener_port: int | None, path: str, link_prefix: str = "go") -> bool:
    """Whether a request on `listener_port` for `path` is answered.

    Only the internal listener is trusted by name; any other port counts as
    the network, so a listener added later is closed until said otherwise.
    """
    if listener_port == INTERNAL_PORT:
        return True
    if listener_port == CARDS_PORT:
        return _under(path, CARDS_PREFIX)
    # The dashboard is open to the network until the login lands: anyone on
    # the home network sees what changed in the memory, card titles and who
    # changed them included. Decided 2026-10-21 for a developer machine.
    return path.startswith(f"/{link_prefix}/") or _under(path, DASHBOARD_PREFIX)


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")
