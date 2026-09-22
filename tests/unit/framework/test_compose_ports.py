"""Host ports follow the 42xxx convention and never collide (adr-007).

A Mac running famstack usually runs other things too, and 3000, 5000 and
8080 are where development servers live. A stacklet that publishes one of
those fails to start the day its owner opens a Node project, with an
error that names the port and not the stacklet. The same goes for two
stacklets claiming one port: each works alone, and the second one up
fails.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Ports a protocol fixes, which no convention can move: DNS clients ask on
# 53, browsers on 80 and 443. Forgejo's SSH sits on 222 because macOS
# keeps 22 for itself.
PROTOCOL_PORTS = {53, 80, 443, 222}

# `- "[ip:]host:container[/proto]"`, where ip and host may be `${VAR:-default}`.
_PORT_RE = re.compile(
    r'^\s*-\s*"(?:(?:\$\{[^}]+\}|[\d.]+):)?'
    r'(?:\$\{\w+:-(?P<default>\d+)\}|(?P<host>\d+))'
    r':\d+(?:/(?P<proto>tcp|udp))?"',
    re.MULTILINE,
)


def _published() -> list[tuple[str, int, str]]:
    """(stacklet, host port, protocol) for every port any stacklet publishes."""
    found = []
    for compose in sorted(REPO_ROOT.glob("stacklets/*/docker-compose*.yml")):
        for m in _PORT_RE.finditer(compose.read_text()):
            port = int(m["host"] or m["default"])
            found.append((compose.parent.name, port, m["proto"] or "tcp"))
    return found


def test_published_ports_are_found():
    # Guard the guard: a pattern that matched nothing would pass both tests.
    assert len(_published()) > 10


def test_every_host_port_is_in_the_42xxx_range_or_fixed_by_its_protocol():
    offenders = [(sid, port) for sid, port, _ in _published()
                 if port not in PROTOCOL_PORTS and not 42000 <= port <= 42999]
    assert not offenders, f"outside 42000-42999 (adr-007): {offenders}"


def test_no_two_stacklets_publish_the_same_host_port():
    owners: dict[tuple[int, str], set[str]] = {}
    for sid, port, proto in _published():
        owners.setdefault((port, proto), set()).add(sid)
    clashes = {key: sids for key, sids in owners.items() if len(sids) > 1}
    assert not clashes, f"host ports claimed by more than one stacklet: {clashes}"
