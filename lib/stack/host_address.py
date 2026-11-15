"""An address from stack.toml, as a container reaches it.

    for_containers("http://localhost:8888/v1")   # "http://host.docker.internal:8888/v1"
    for_containers("http://ai.lan:abc/v1")       # "http://ai.lan:abc/v1": unchanged
    usable("http://ai.lan:abc/v1")               # False
    on_this_mac("http://127.0.0.2:8000/v1")      # True

`[ai]` names its servers as the Mac sees them, often on the Mac's own
loopback. Inside a container, and inside the proxy, the loopback is the
container itself; Docker's host gateway (`host.docker.internal`) is the
way to the Mac. Every address of the Mac's own (`localhost`, the whole
loopback range `127.0.0.0/8` and `::1`, and `0.0.0.0`) is mapped to it.
The rule is one (`on_this_mac`), so it is the same for the containers'
environment (`{ai_openai_url_docker}`), the proxy's routes
(`stack.caddy.upstream`) and the CLI, which lists such a server as
`localhost` (`stack.ai.probe`).

An address a person edited by hand can be unusable: no scheme, a port
that is no number, a broken IPv6 address. `for_containers` then returns
it unchanged, never an exception, because every `stack` command renders
the template variables that read it. It never returns "" for it either:
a client given an empty address may fall back to a public server, and an
address that fails to connect keeps the family's text at home. The
proxy asks `usable` and leaves its route out instead.
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse

__all__ = ["HOST_GATEWAY", "for_containers", "usable", "on_this_mac"]

# Docker's route from a container to the Mac.
HOST_GATEWAY = "host.docker.internal"

# `0.0.0.0` is not a loopback address, but a server bound to it answers
# on the loopback too, and people copy it from the server's own start
# message.
_ANY_ADDRESS = "0.0.0.0"

_SCHEMES = ("http", "https")

# A host name: letters, digits, dots and hyphens. Anything else (a space,
# a brace) is a typo, and in the proxy's file a brace would start a
# Caddy placeholder or end a block.
_HOST_NAME = re.compile(r"[A-Za-z0-9.-]+")


def for_containers(url: str) -> str:
    """`url` with a loopback host replaced by the host gateway.

    The scheme, user, port, path and query stay. An IPv6 host keeps its
    brackets. An address that is not `usable` comes back unchanged, so a
    client fails to connect instead of falling back to another server.

        for_containers("http://[::1]:8888/v1")     # "http://host.docker.internal:8888/v1"
        for_containers("http://[fd00::5]:8000/v1") # "http://[fd00::5]:8000/v1"
        for_containers("localhost:8000/v1")        # "localhost:8000/v1"
    """
    parts = _parts(url)
    if parts is None:
        return url
    scheme, userinfo, host, port, rest = parts
    netloc = _host(host) + (f":{port}" if port is not None else "")
    if userinfo:
        netloc = f"{userinfo}@{netloc}"
    return urllib.parse.urlunsplit((scheme, netloc, *rest))


def usable(url: str) -> bool:
    """Whether `url` is an http or https address with a valid host and port.

    False for "", for no scheme (`localhost:8000/v1` reads as the scheme
    `localhost`), for a port that is no number or out of range, for a
    broken IPv6 address, and for a host with characters other than
    letters, digits, dots and hyphens (`ai lan`, `ai.lan}`).

        usable("http://localhost:8000/v1")    # True
        usable("http://ai.lan:70000/v1")      # False
    """
    return _parts(url) is not None


def on_this_mac(url: str) -> bool:
    """Whether `url` names this Mac: `localhost`, a loopback address or
    `0.0.0.0`. Decided from the address as written, without a DNS lookup,
    so it is cheap enough for a status line. False for an address that is
    not `usable`.

        on_this_mac("http://LOCALHOST:8000/v1")      # True
        on_this_mac("http://127.0.0.2:8000/v1")      # True
        on_this_mac("http://192.168.1.20:8000/v1")   # False
    """
    parts = _parts(url)
    return parts is not None and _is_this_mac(parts[2])


# ── Reading an address ────────────────────────────────────────────────────


def _parts(url: str):
    """`(scheme, userinfo, host, port, (path, query, fragment))` of a
    usable `url`, or None."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if parts.scheme not in _SCHEMES or not host or not _valid_host(host):
        return None
    userinfo = parts.netloc.rpartition("@")[0]
    return parts.scheme, userinfo, host, port, (parts.path, parts.query, parts.fragment)


def _valid_host(host: str) -> bool:
    """A host name, or an IPv6 address (urlsplit drops its brackets)."""
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return False
        return True
    return bool(_HOST_NAME.fullmatch(host))


def _host(host: str) -> str:
    """The host as a container writes it: the gateway for the loopback,
    brackets around any other IPv6 address."""
    if _is_this_mac(host):
        return HOST_GATEWAY
    return f"[{host}]" if ":" in host else host


def _is_this_mac(host: str) -> bool:
    """Whether a host from `_parts` (lowercase, IPv6 without brackets) is
    this Mac."""
    if host in ("localhost", _ANY_ADDRESS):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
