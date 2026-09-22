"""Every caddy.snippet points at something that exists.

A snippet names its backends by container name and its sites by host
name, and nothing checks either until a browser gets a 502 or a 404.
Two snippets targeted container names that no compose file used
(`immich_server`, `chatai`), and two stacklets were served on a host
other than the one the stack hands out as their URL. These checks read
the repo's own files, so they fail the moment a container is renamed or
a stacklet starts advertising a URL no snippet serves.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Native services on the Mac, reached from the proxy container through
# Docker's host gateway, not by container name.
HOST_GATEWAY = "host.docker.internal"


def _snippets() -> dict[str, str]:
    return {p.parent.name: p.read_text()
            for p in sorted(REPO_ROOT.glob("stacklets/*/caddy.snippet"))}


def _container_names() -> set[str]:
    return {name
            for compose in REPO_ROOT.glob("stacklets/*/docker-compose*.yml")
            for name in re.findall(r"^\s*container_name:\s*(\S+)", compose.read_text(), re.M)}


def _upstreams(snippet: str) -> list[str]:
    """Hosts of every `reverse_proxy` upstream. Matchers (`/path`, `@name`)
    and the block brace are skipped."""
    hosts = []
    for line in snippet.splitlines():
        tokens = line.split("#", 1)[0].split()
        if not tokens or tokens[0] != "reverse_proxy":
            continue
        for token in tokens[1:]:
            if token.startswith(("/", "@", "{")):
                continue
            hosts.append(token.rsplit(":", 1)[0])
    return hosts


def _site_addresses(snippet: str) -> set[str]:
    """Addresses of the top-level site blocks: unindented lines ending in `{`."""
    addresses = set()
    for line in snippet.splitlines():
        if line[:1] in ("", " ", "\t", "#") or not line.rstrip().endswith("{"):
            continue
        addresses.update(a.strip() for a in line.rstrip()[:-1].split(",") if a.strip())
    return addresses


def test_snippets_are_found():
    # Guard the guard: an empty glob would make every check below pass.
    assert len(_snippets()) >= 5


def test_every_upstream_is_a_container_some_stacklet_runs():
    containers = _container_names()
    unknown = {sid: host for sid, snippet in _snippets().items()
               for host in _upstreams(snippet)
               if host != HOST_GATEWAY and host not in containers}
    assert not unknown, f"reverse_proxy targets no compose file names: {unknown}"


def test_the_url_a_stacklet_hands_out_is_served_by_its_snippet():
    """In domain mode `{url}` renders as `<id>.<domain>`. A stacklet that
    shows it to the family (a hint, a welcome message, a link another
    stacklet builds) needs a site at exactly that host."""
    snippets = _snippets()
    unserved = []
    for manifest_path in sorted(REPO_ROOT.glob("stacklets/*/stacklet.toml")):
        sid = manifest_path.parent.name
        if "{url}" not in manifest_path.read_text():
            continue
        if f"{sid}.{{$STACK_DOMAIN}}" not in _site_addresses(snippets.get(sid, "")):
            unserved.append(sid)
    assert not unserved, f"no site at <id>.{{$STACK_DOMAIN}} for: {unserved}"
