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
- the published listener serves the links and nothing else
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))

from access import INTERNAL_PORT, PUBLIC_PORT, serves  # noqa: E402

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
