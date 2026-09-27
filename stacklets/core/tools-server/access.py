"""What the tools server answers on which listener.

The server listens twice. The internal listener is never published: only the
stack's containers reach it, over the Docker network, and it serves
everything. The public listener is the published port (42000). Docker's port
forwarding gives every request through it the same source address, whether
it comes from this Mac or from a phone on the Wi-Fi, so an address says
nothing about who is calling. The listener does.

On the public listener only the persistent links (`/<prefix>/...`) exist,
because family members' browsers open them. Search, logs and status are for
the stack's containers.

Pure: no web framework, so the rule is tested on its own.
"""

from __future__ import annotations

INTERNAL_PORT = 8000
PUBLIC_PORT = 8001


def serves(listener_port: int | None, path: str, link_prefix: str = "go") -> bool:
    """Whether a request on `listener_port` for `path` is answered.

    Only the internal listener is trusted by name; any other port counts as
    the network, so a listener added later is closed until said otherwise.
    """
    if listener_port == INTERNAL_PORT:
        return True
    return path.startswith(f"/{link_prefix}/")
