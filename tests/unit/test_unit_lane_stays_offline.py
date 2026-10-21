"""The unit lane stays off the network.

A unit test once sent a real write request to the test rig's Forgejo on a
LAN address of the Mac running the suite: `./stack`'s plugin loader loads a
fresh copy of a stacklet's modules for each command, so a stand-in patched
onto one copy never reached the next command, and the command went to the
address its configuration named. Only the rig's 401 kept it from writing.

`tests/unit/conftest.py` makes that a failure instead of a hazard. These
tests run a small test file under that conftest, the way the unit lane runs
every test, and read what pytest reports for it.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

pytest_plugins = ["pytester"]

UNIT_LANE_CONFTEST = Path(__file__).with_name("conftest.py")
STACKLETS = Path(__file__).resolve().parents[2] / "stacklets"

# TEST-NET-1 (RFC 5737): reserved for documentation, never a real host.
ELSEWHERE = "192.0.2.1"

# A stand-in HTTP server on the first of `ports` that is free, for the
# tests below that need one on a port of their choosing.
STAND_IN = """
import http.server
import threading
import urllib.request

import pytest


class Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"hello")

    def log_message(self, *args):
        pass


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = False  # never share a port something else holds


def serve_on_first_free(ports):
    for port in ports:
        try:
            server = Server(("127.0.0.1", port), Hello)
        except OSError:
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    pytest.skip(f"no free port among {list(ports)}")


def ask(server):
    port = server.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        return r.read()
"""


def run_in_unit_lane(pytester, test_source: str):
    """Run `test_source` as a test file in the unit lane of a checkout:
    in `tests/unit/` under the real conftest, next to the shipped stacklets
    it reads the published ports from."""
    lane = pytester.path / "tests" / "unit"
    lane.mkdir(parents=True)
    (lane / "conftest.py").write_text(UNIT_LANE_CONFTEST.read_text(encoding="utf-8"))
    (pytester.path / "stacklets").symlink_to(STACKLETS, target_is_directory=True)
    (lane / "test_case.py").write_text(textwrap.dedent(test_source))
    return pytester.inline_run("-p", "no:cacheprovider", str(lane))


def call_report(recorder, name: str):
    return recorder.matchreport(name, when="call")


def test_a_connection_to_another_host_fails_the_test_at_once(pytester):
    """Given a test that opens a connection to a host that is not this one,
    then the test fails naming host and port, without waiting for a timeout.

    The message has to say where the test reached and what to use instead,
    because the test that trips it was not written to reach anything."""
    recorder = run_in_unit_lane(pytester, f"""
        import socket

        def test_reaches_out():
            socket.create_connection(("{ELSEWHERE}", 42040), timeout=5)
    """)

    report = call_report(recorder, "test_reaches_out")
    assert report.failed
    assert f"{ELSEWHERE}:42040" in report.longreprtext
    assert "httpserver" in report.longreprtext
    assert report.duration < 1


def test_a_refusal_the_code_under_test_catches_still_fails_the_test(pytester):
    """Given code that catches every error from its HTTP call, as a command
    turning a failed request into an error answer does, then the test still
    fails.

    The request that reached the rig was such a call: its 401 became an
    answer, and an answer is what the test was asserting on."""
    recorder = run_in_unit_lane(pytester, f"""
        import urllib.request

        def test_swallows_the_error():
            try:
                urllib.request.urlopen("http://{ELSEWHERE}:42040/api/v1/repos", timeout=5)
            except Exception:
                pass
    """)

    report = call_report(recorder, "test_swallows_the_error")
    assert report.failed
    assert f"{ELSEWHERE}:42040" in report.longreprtext


def test_a_refusal_in_another_thread_still_fails_the_test(pytester):
    """Given code that reaches out from a worker thread, where an error never
    reaches the test, then the test still fails when it returns. Bots and
    servers do their I/O off the test's thread."""
    recorder = run_in_unit_lane(pytester, f"""
        import socket
        import threading

        def test_reaches_out_in_a_thread():
            worker = threading.Thread(
                target=socket.create_connection, args=(("{ELSEWHERE}", 42040), 5))
            worker.start()
            worker.join()
    """)

    report = call_report(recorder, "test_reaches_out_in_a_thread")
    assert report.failed
    assert f"{ELSEWHERE}:42040" in report.longreprtext


def test_a_name_lookup_of_another_host_fails_the_test(pytester):
    """Given a test that resolves a host name other than localhost, then the
    test fails naming that host. A lookup is the first step of reaching it,
    and on a home network it already leaves the machine."""
    recorder = run_in_unit_lane(pytester, """
        import socket

        def test_looks_up():
            socket.getaddrinfo("forgejo.example", 443)
    """)

    report = call_report(recorder, "test_looks_up")
    assert report.failed
    assert "forgejo.example:443" in report.longreprtext


def test_a_name_under_invalid_does_not_resolve_without_asking_the_network(pytester):
    """Given a test that looks up a name under `.invalid`, then the lookup
    fails the way a resolver answers a name that does not exist, and the
    test decides what that means. RFC 6761 reserves `.invalid` never to
    resolve, so the answer needs no network."""
    recorder = run_in_unit_lane(pytester, """
        import socket
        import pytest

        def test_looks_up_a_name_that_does_not_exist():
            with pytest.raises(socket.gaierror):
                socket.getaddrinfo("nowhere.invalid", 443)
    """)

    recorder.assertoutcome(passed=1)


def test_a_port_a_running_instance_listens_on_is_refused(pytester):
    """Given a test that connects to a port a shipped stacklet publishes,
    with no stand-in of its own there, then the test fails naming the
    stacklet. On a Mac running the test rig, that port is the rig."""
    recorder = run_in_unit_lane(pytester, """
        import socket

        def test_reaches_forgejo():
            socket.create_connection(("127.0.0.1", 42040), timeout=5)
    """)

    report = call_report(recorder, "test_reaches_forgejo")
    assert report.failed
    assert "127.0.0.1:42040" in report.longreprtext
    assert "code stacklet" in report.longreprtext


def test_a_stand_in_on_a_42xxx_port_no_stacklet_publishes_still_answers(pytester):
    """Given a stand-in on a 42xxx port no stacklet publishes, then it
    answers. On Linux a random free port can fall anywhere in that range."""
    recorder = run_in_unit_lane(pytester, STAND_IN + """

def test_asks_the_stand_in():
    # No stacklet publishes a port above 42100.
    assert ask(serve_on_first_free(range(42900, 43000))) == b"hello"
""")

    recorder.assertoutcome(passed=1)


def test_a_stand_in_that_got_a_published_port_still_answers(pytester):
    """Given a stand-in that landed on a port a stacklet publishes, then it
    answers: this process listens there, so the port is not the rig's. A
    random free port can be one of them on Linux, so without this the lane
    would fail now and then."""
    recorder = run_in_unit_lane(pytester, STAND_IN + """

def test_asks_the_stand_in():
    # Ports the shipped stacklets publish; the first one free here is used.
    assert ask(serve_on_first_free([42061, 42063, 42081, 42090, 42070, 42010])) == b"hello"
""")

    recorder.assertoutcome(passed=1)


def test_a_stand_in_on_localhost_still_answers(pytester):
    """Given a test that talks to a pytest-httpserver stand-in, then it
    passes: stand-ins listen on localhost, and that is how a unit test
    reaches a service."""
    recorder = run_in_unit_lane(pytester, """
        import json
        import urllib.request

        def test_asks_the_stand_in(httpserver):
            httpserver.expect_request("/api/v1/version").respond_with_json({"version": "1.0"})
            with urllib.request.urlopen(httpserver.url_for("/api/v1/version"), timeout=5) as r:
                assert json.load(r) == {"version": "1.0"}
    """)

    recorder.assertoutcome(passed=1)
