"""The address at which the host reaches a service of the stack on this Mac.

    own_service_url(42040)    # "http://127.0.0.1:42040"
    OWN_SERVICE_HOST          # "127.0.0.1", for a socket or a client that takes host and port

A stack command runs on the host and calls a service of the stack, such
as Forgejo, Immich or Synapse, at the port that the stacklet publishes.
This module is the one place that builds that address, so every command
reaches its service the same way.

The host is 127.0.0.1, never `localhost`. A stacklet publishes its port
on an IPv4 address only (`PORT_BIND_IP` in its compose file: 0.0.0.0 or
127.0.0.1). `localhost` resolves to ::1 first, and the same port number
on ::1 can belong to another process: a development server, or another
test's stand-in in a parallel test run. That process would then get the
request, the admin's password included.

The module does not cover an address that a person or the stack writes
into `stack.toml` (`[ai] openai_url`): that is configuration, and
`stack.host_address` reads it. Nor does it cover a link shown to a
person, which a browser opens.
"""
from __future__ import annotations

__all__ = ["OWN_SERVICE_HOST", "own_service_url"]

# The IPv4 loopback, where every port of the stack is published.
OWN_SERVICE_HOST = "127.0.0.1"


def own_service_url(port: int) -> str:
    """The base URL of the stack's service at `port` on this Mac, as the
    host reaches it: plain HTTP on the IPv4 loopback, no trailing slash."""
    return f"http://{OWN_SERVICE_HOST}:{port}"
