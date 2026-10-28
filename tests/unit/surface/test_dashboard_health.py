"""Core counts as up only when its dashboard answers.

The installer prints the dashboard's address and opens the browser once
`stack up core` is done, so done has to mean the dashboard answers: on a
fresh install the address was printed while the tools server was still
starting, and the first request to it was reset. Core's health check asks
for a path the published port serves, one that asks the host API and the
memory nothing, so it is cheap and says only that the server is there.

Driven through the tools server's FastAPI app (the Browser DSL) as a phone
on the home network opens it, with a host API that fails the test when
asked, and through `Stack`'s own reading of core's manifest.
"""

from __future__ import annotations

import re
import sys
from urllib.parse import urlsplit

from .conftest import LIB, REPO, STACKLETS
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402


def phone() -> Browser:
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


def asked_nothing(request, timeout):
    raise AssertionError(f"the health check asked the host API: {request}")


def test_the_dashboard_answers_its_health_check_without_asking_the_stack(monkeypatch):
    monkeypatch.setattr(client, "call", asked_nothing)

    assert phone().reads("/dashboard/api/health") == (200, {"ok": True})


def test_core_is_up_only_when_its_dashboard_answers(tmp_path, monkeypatch):
    """Core's health check is that path, on the port core publishes for the
    public listener, on this Mac in either mode."""
    from stack import Stack

    monkeypatch.setattr(client, "call", asked_nothing)
    (tmp_path / "stack.toml").write_text(f'[core]\ndata_dir = "{tmp_path / "data"}"\n')
    stck = Stack(REPO, tmp_path / "data", instance_dir=tmp_path)
    core = stck._find_stacklet("core")
    checks = stck._resolve_health_checks(core["manifest"], stck._build_template_vars())

    compose = (STACKLETS / "core" / "docker-compose.yml").read_text()
    published = {int(host) for host, inside in re.findall(r":(\d+):(\d+)\"", compose)
                 if int(inside) == PUBLIC_PORT}
    assert checks, "core declares no health check"
    for check in checks:
        url = urlsplit(check["url"])
        assert (url.hostname, url.port in published) == ("localhost", True), check["url"]
        assert phone().reads(url.path)[0] == 200
