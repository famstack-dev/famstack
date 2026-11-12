"""What the core tools server answers, and to whom.

The tools server answers document search, photo search, logs and server
status for the stack's own containers (Open WebUI's tool calls), and the
family's persistent links (`/go/...`) for every browser in the house. Its
published port is on the LAN in port mode, and Docker's port forwarding
shows every request through it with the same address, whether it comes
from this Mac or from any phone on the Wi-Fi. So the server tells callers
apart by the listener a request arrives on, never by address:

- the internal listener is reachable only over the Docker network and
  serves everything
- the published listener serves the links, the dashboard and the memory's
  pages to read, nothing else
- the Mac's address, published on 127.0.0.1 only, serves everything, and
  only a request through it comes from the Mac (`here`)
"""

from __future__ import annotations

from types import SimpleNamespace

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))

from access import CARDS_PORT, INTERNAL_PORT, PUBLIC_PORT, here, serves  # noqa: E402

PRIVATE = ("/tools/documents/search", "/tools/photos/search", "/tools/stack/status",
           "/logs", "/openapi.json", "/docs", "/")


def test_the_stacks_own_containers_get_everything():
    for path in PRIVATE:
        assert serves(INTERNAL_PORT, path)


def test_the_network_gets_the_family_links():
    for path in ("/go/docs/42", "/go/topic/family/camping/todo", "/go/person/lisa"):
        assert serves(PUBLIC_PORT, path)


def test_the_network_gets_nothing_else():
    for path in PRIVATE:
        assert not serves(PUBLIC_PORT, path)


def test_a_path_that_only_starts_like_a_link_is_not_one():
    assert not serves(PUBLIC_PORT, "/gopher")
    assert not serves(PUBLIC_PORT, "/go")  # the bare prefix resolves nothing


def test_the_link_prefix_follows_its_setting():
    assert serves(PUBLIC_PORT, "/l/docs/42", link_prefix="l")
    assert not serves(PUBLIC_PORT, "/go/docs/42", link_prefix="l")


def test_an_unknown_listener_is_treated_as_the_network():
    # Fail closed: only the internal listener is trusted by name.
    assert not serves(9999, "/tools/stack/status")
    assert not serves(None, "/logs")


def test_the_card_page_listener_serves_the_card_page():
    """Published on this Mac only (127.0.0.1): the family memory's card browser."""
    assert serves(CARDS_PORT, "/cards")
    assert serves(CARDS_PORT, "/cards/api/find")


def test_the_macs_address_serves_every_route():
    """Published on this Mac only (127.0.0.1:42002): the Mac's own browser
    and the menu bar app open the setup, the dashboard and the links there."""
    for path in ("/setup", "/dashboard", "/dashboard/api/stack", "/go/topic/camping", "/cards"):
        assert serves(CARDS_PORT, path)


def test_the_network_does_not_get_the_card_page():
    assert not serves(PUBLIC_PORT, "/cards")


def test_the_network_gets_the_dashboard():
    """Open on the network until the login lands (access.py)."""
    for path in ("/dashboard", "/dashboard/api/activity", "/dashboard/static/tokens.css"):
        assert serves(PUBLIC_PORT, path)
    assert not serves(PUBLIC_PORT, "/dashboardx")


def test_the_network_gets_the_memorys_pages():
    """Read only: no route under `/card` takes a write (memory_ui)."""
    for path in ("/card", "/card/note-0b6e2d91aa", "/card/api/find", "/card/static/cards.js"):
        assert serves(PUBLIC_PORT, path)
    assert not serves(PUBLIC_PORT, "/memoryx")


class _Request:
    """What `here` reads of a request: its ASGI scope, its headers, and
    who the gate found it to be (`kind`: "mac" for the admin key)."""

    def __init__(self, listener: int | None, host: str, kind: str = "mac"):
        self.scope = {"server": ("172.18.0.5", listener)} if listener else {}
        self.headers = {"host": host}
        self.state = SimpleNamespace(who=SimpleNamespace(kind=kind))


def test_only_a_request_through_the_macs_address_is_here():
    assert here(_Request(CARDS_PORT, "127.0.0.1:42002"))


def test_the_macs_address_without_the_admin_key_is_not_here():
    """Every container reaches the Mac's listener too; the gate found no
    admin key, so the request is nobody's."""
    assert not here(_Request(CARDS_PORT, "localhost:42002", kind="nobody"))


def test_a_host_header_does_not_make_a_request_here():
    """Any device on the home network can send the Mac's name as its Host header."""
    for host in ("localhost:42000", "127.0.0.1:42000", "[::1]:42000"):
        assert not here(_Request(PUBLIC_PORT, host))


def test_the_stacks_containers_and_an_unknown_listener_are_not_here():
    assert not here(_Request(INTERNAL_PORT, "localhost:8000"))
    assert not here(_Request(None, "127.0.0.1:42002"))
