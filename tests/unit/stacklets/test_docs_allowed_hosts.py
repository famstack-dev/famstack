"""Paperless answers the hosts the stack calls it by, and no other.

Paperless 3.5 checks the Host header of every request. With `PAPERLESS_URL`
set it allows only `localhost`, the host of that URL and the hosts in
`PAPERLESS_ALLOWED_HOSTS`. The stack itself calls Paperless by its
container name (core's `PAPERLESS_URL`, the archivist, the tools server), so
that name has to be in the list, or every upload of a letter gets 400.

The Host check stays on: `*` would let any name reach Paperless, so the list
names the container and nothing else. The browser's hosts come from
`PAPERLESS_URL` (the LAN address in port mode, `docs.<domain>` in domain
mode, which Caddy passes through unchanged) and need no entry here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent.parent.parent
DOCS_COMPOSE = REPO / "stacklets" / "docs" / "docker-compose.yml"
CORE_MANIFEST = REPO / "stacklets" / "core" / "stacklet.toml"

# `PAPERLESS_ALLOWED_HOSTS: <value>` in the Paperless service's environment.
_ALLOWED_RE = re.compile(r"^\s*PAPERLESS_ALLOWED_HOSTS:\s*[\"']?([^\"'#\n]*)", re.MULTILINE)


def _allowed_hosts() -> list[str]:
    """The hosts the docs compose file adds to Paperless's Host check."""
    found = _ALLOWED_RE.search(DOCS_COMPOSE.read_text(encoding="utf-8"))
    if not found:
        return []
    return [h.strip() for h in found.group(1).split(",") if h.strip()]


def _internal_host() -> str:
    """The host the stack's own services use to call Paperless."""
    core = tomllib.loads(CORE_MANIFEST.read_text(encoding="utf-8"))
    return urlparse(core["env"]["defaults"]["PAPERLESS_URL"]).hostname or ""


def test_paperless_allows_the_host_the_stack_calls_it_by():
    """Given core's address for Paperless, when the archivist uploads a
    letter, then Paperless accepts the request's host. Without it every
    filing fails with 400."""
    assert _internal_host() in _allowed_hosts()


def test_paperless_keeps_its_host_check():
    """Given the allowed hosts, then none of them is a wildcard, so a host
    the stack does not use still gets 400."""
    hosts = _allowed_hosts()
    assert hosts, "PAPERLESS_ALLOWED_HOSTS is not set in the docs compose file"
    assert not any("*" in h for h in hosts)


def test_paperless_learns_the_browsers_host_from_its_url():
    """Given the docs compose file, then it passes `PAPERLESS_URL` to
    Paperless. The browser's host is allowed only through that URL, so
    without it every browser on the LAN address gets 400."""
    compose = DOCS_COMPOSE.read_text(encoding="utf-8")
    assert re.search(r"^\s*PAPERLESS_URL:\s*\$\{PAPERLESS_URL\}", compose, re.MULTILINE)
