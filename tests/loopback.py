"""Loopback listeners for test stand-ins, stopped without leaving anything behind.

A stand-in that speaks a socket protocol (the host API's line protocol, a
reply that is not HTTP) listens here:

    with serving(api.handle_client) as port:    # each connection to the handler
        ...                                     # 127.0.0.1:<port> answers
    # nothing listens on <port> any more, and no thread of it waits

    listener = Listener(reply)                  # the same, started and stopped apart
    listener.port
    listener.stop()

    listener = Listener(reply, listening=False) # the port is the stand-in's, closed
    listener.listen()                           # from now on it answers

    a_closed_port()                             # nothing listens there, in any test

Why it exists: the stand-ins used to run `while True: listener.accept()`
in a thread and stop with `listener.close()` from the test. On Linux, where
CI runs the unit lane, that leaves the listener behind in two ways. A
thread waiting in `accept()` keeps the closed listener listening on its
port for as long as it waits, so a later client of that port is answered
by it. And a thread that has read the listener's descriptor number but not
yet called `accept()` when the test closes it calls `accept()` on the next
socket of the process that gets that number: another test's server, whose
connections it then answers. The unit lane's workers each run many tests
in one process, so the host API's handler once answered the memory
curator's clients in a later test.

So a listener here is a `socketserver` server. Its loop waits in
`select()` with a short timeout; `stop()` asks the loop to end, waits until
it has, and only then closes the socket. Each connection runs in a daemon
thread of its own, which touches only its own connection, as the host
API's `main` runs one. The `stop` in `tests/memory/unit/instance.py` does
the same for the memory's HTTP servers.

A client that must find nothing listening uses `a_closed_port()`, never a
free port the system handed out and the test gave back at once: that port
is free for any socket of the machine, and the lane's other workers take
free ports all the time, so the client would reach another test's server.
"""

from __future__ import annotations

import contextlib
import socket
import socketserver
import threading
from collections.abc import Callable, Iterator

__all__ = ["Listener", "a_closed_port", "serving"]

# Loopback, any free port.
LOOPBACK = ("127.0.0.1", 0)

# How long the loop waits in select() before it looks for a stop: the most
# `stop()` waits. Short, because every test that serves a stand-in stops it.
POLL_SECONDS = 0.01

Handler = Callable[[socket.socket], None]

# Port 1 is below the range the system hands out for a bind to port 0, on
# Linux only a privileged process may listen on it, and no test names it.
# A socket that is bound and does not listen is no alternative: macOS lets
# a connection to it wait until its timeout instead of refusing it.
CLOSED_PORT = 1


# ── The listener ─────────────────────────────────────────────────────────

class Listener:
    """A stand-in's listener: every connection goes to `handle(conn)` in a
    daemon thread of its own, from `listen()` until `stop()`.

    `address` is where it binds, loopback on a free port by default; an
    IPv6 address binds IPv6 only. With `listening=False` the port is bound
    and refuses connections until `listen()`, so no other socket takes it
    meanwhile. `handle` may close the connection; it is closed after it
    returns in any case.

        listener = Listener(api.handle_client)
        port = listener.port
        listener.stop()
    """

    def __init__(self, handle: Handler, address: tuple = LOOPBACK, *, listening: bool = True):
        self._server = _server_for(address, handle)
        self._thread: threading.Thread | None = None
        self.port: int = self._server.server_address[1]
        if listening:
            self.listen()

    def listen(self) -> None:
        """Take connections from now on. Once only."""
        self._server.server_activate()
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": POLL_SECONDS}, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """End the loop, wait until it has ended, then close the socket.

        In this order no thread uses the socket's descriptor once it is
        closed, so the number can go to the next socket of the process
        safely. Connections still being answered go on in their threads."""
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join()
        self._server.server_close()


@contextlib.contextmanager
def serving(handle: Handler, address: tuple = LOOPBACK) -> Iterator[int]:
    """`handle` on a `Listener` while the block runs; its port."""
    listener = Listener(handle, address)
    try:
        yield listener.port
    finally:
        listener.stop()


def a_closed_port() -> int:
    """A port on 127.0.0.1 that refuses every connection, for the whole test."""
    return CLOSED_PORT


# ── The socketserver underneath ──────────────────────────────────────────

class _Server(socketserver.ThreadingTCPServer):
    # As `socket.create_server` binds, so a port in TIME_WAIT is no obstacle.
    allow_reuse_address = True
    # One thread per connection, not waited for at the stop: a stand-in's
    # connection lives as long as its client keeps it, as the host API's do.
    daemon_threads = True
    block_on_close = False
    # The backlog `socket.create_server` gives, so a page's requests at once queue.
    request_queue_size = socket.SOMAXCONN


class _Server6(_Server):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        # IPv6 only, so the stand-in can share a port number with an IPv4 one.
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def _server_for(address: tuple, handle: Handler) -> _Server:
    """A server bound to `address`, not yet listening (that is
    `server_activate()`), whose connections go to `handle`."""
    class Connection(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            handle(self.request)

    kind = _Server6 if ":" in address[0] else _Server
    server = kind(address, Connection, bind_and_activate=False)
    try:
        server.server_bind()
    except OSError:
        server.server_close()
        raise
    return server
