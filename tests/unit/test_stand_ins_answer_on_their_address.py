"""A request to a stand-in reaches the stand-in.

pytest-httpserver's stand-ins take a random free port. Free on 127.0.0.1
says nothing about ::1: another process can listen there on the same
number (on the Mac that runs the suite, OrbStack and Node do). A URL that
names `localhost` leaves the client to pick, and urllib and git's curl
both try ::1 first. Then a clone fails with "Received HTTP/0.9 when not
allowed", or a commit goes to the other process and never arrives, once
in a few runs of the whole lane. `httpserver_listen_address` in the unit
lane's rules (`tests/lanes/unit.py`) gives the stand-ins an address instead.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import urllib.request

import pytest


@pytest.fixture
def another_process_on_ipv6(httpserver):
    """A listener on ::1 at the stand-in's port number, answering what is not HTTP."""
    try:
        listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("::1", httpserver.port))
    except OSError as e:
        pytest.skip(f"cannot listen on [::1]:{httpserver.port}: {e}")
    listener.listen()

    def answer():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            conn.recv(65536)
            conn.sendall(b"not http at all\n")
            conn.close()

    threading.Thread(target=answer, daemon=True).start()
    yield
    listener.close()


def test_a_request_reaches_the_stand_in_when_its_port_is_taken_on_ipv6(
        httpserver, another_process_on_ipv6):
    httpserver.expect_request("/api/v1/version").respond_with_json({"version": "14"})

    with urllib.request.urlopen(httpserver.url_for("/api/v1/version"), timeout=5) as r:
        assert json.loads(r.read()) == {"version": "14"}


def test_a_git_clone_reaches_the_stand_in_when_its_port_is_taken_on_ipv6(
        httpserver, another_process_on_ipv6):
    httpserver.expect_request("/family/memory.git/info/refs").respond_with_data(
        "", status=404)

    done = subprocess.run(["git", "ls-remote", httpserver.url_for("/family/memory.git")],
                          capture_output=True, text=True, timeout=15)

    assert "HTTP/0.9" not in done.stderr
    assert "404" in done.stderr or "not found" in done.stderr.lower()
