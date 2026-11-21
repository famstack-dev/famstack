"""A test's stand-in servers and closed ports belong to that test alone.

Tests stand in for the host API and other services on loopback ports, and
point a client at a port where nothing listens to see a service that does
not answer. The unit lane runs on four workers, each a process that runs
many tests one after another, and all four share the Mac's ports. So a
stand-in that still listens after its test, or a "closed" port given back
while the test still points at it, is reached by a client it was never
meant for, and that client fails with another server's answer:

    a stand-in, stopped        its port refuses every later client
    a closed port              no other test's server can get it

The first is not a question of a slow stop. On Linux, where CI runs the
lane, a listener closed while its thread waits in `accept()` keeps
listening for as long as that thread waits, and a thread that is about to
call `accept()` when the listener closes calls it on whatever socket gets
the same descriptor number next. Both let the host API's handler answer
the memory curator's clients in a later test.
"""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

from stack import stacklet_code

from tests.dashboard_host import DashboardHost
from tests.loopback import serving

from .conftest import STACKLETS, a_closed_port


def asks(port: int, request: dict) -> dict:
    """One request to a host API at `port`, as core's tools server sends
    it, and the first line of its reply."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall((json.dumps(request) + "\n").encode())
        with conn.makefile("rb") as reply:
            return json.loads(reply.readline())


def refuses(port: int) -> bool:
    """True when nothing on 127.0.0.1 takes a connection at `port`."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except ConnectionRefusedError:
        return True
    return False


# ── A stand-in, stopped ──────────────────────────────────────────────────

def test_a_stopped_host_api_stand_in_answers_no_later_client(tmp_path):
    """Given the host API's stand-in answered a request and its test
    stopped it, when a later client connects to its port, then nothing
    takes the connection: the client fails naming its own address, never
    with this stand-in's answer."""
    host = DashboardHost.in_state("fresh", root=tmp_path / "instance")
    port = host.serve()
    asks(port, {"cmd": "family"})
    assert [r["cmd"] for r in host.asked] == ["family"]

    host.close()

    assert refuses(port)
    assert [r["cmd"] for r in host.asked] == ["family"]


def test_the_host_api_served_for_a_test_answers_no_client_after_it(tmp_path):
    """Given the host API's own connection handler served on loopback for a
    block, as the host API tests serve it, when a client connects to that
    port after the block, then nothing takes the connection."""
    api = _host_api()
    with serving(api.handle_client) as port:
        refusal = asks(port, {"cmd": "list"})
        assert "error" in refusal

    assert refuses(port)


def _host_api():
    """The host API module of this checkout, a fresh copy as the framework
    loads a stacklet's code."""
    core = STACKLETS / "core"
    return stacklet_code.load(core, core / "famstack-api.py")


# ── A closed port, in use ────────────────────────────────────────────────

def test_a_closed_port_is_never_one_another_server_gets():
    """Given a test points a client at a closed port, then the connection is
    refused, and the port is outside the range the system hands out to a
    server that asks for a free one, so no other test's server listens
    there while the client asks."""
    closed_port = a_closed_port()

    assert refuses(closed_port)
    first, last = _free_port_range()
    assert not first <= closed_port <= last


def _free_port_range() -> tuple[int, int]:
    """The ports this system hands out for a bind to port 0, as it reports them."""
    linux = Path("/proc/sys/net/ipv4/ip_local_port_range")
    if linux.exists():
        first, last = linux.read_text().split()
        return int(first), int(last)
    first, last = (subprocess.run(["sysctl", "-n", f"net.inet.ip.portrange.{end}"],
                                  capture_output=True, text=True, check=True).stdout
                   for end in ("first", "last"))
    return int(first), int(last)
