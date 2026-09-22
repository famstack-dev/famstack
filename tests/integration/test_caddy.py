"""The assembled Caddyfile, as a real Caddy reads it.

The unit tests pin which snippets the framework puts in the file and when.
Whether Caddy accepts the result, and what it serves from it, only Caddy
can say, so these hand it the snippets that ship in the repo and read the
JSON config it adapts them to.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from stack.caddy import assemble  # noqa: E402

try:
    subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
    HAS_DOCKER = True
except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
    HAS_DOCKER = False

pytestmark = pytest.mark.skipif(not HAS_DOCKER, reason="Docker not available")

STOCK_CADDY = "caddy:2"
DOMAIN = "home.example.family"


def _shipped_snippets() -> list[tuple[str, str]]:
    return [(path.parent.name, path.read_text())
            for path in sorted(REPO_ROOT.glob("stacklets/*/caddy.snippet"))]


def _caddy(image: str, caddyfile: Path, *args: str) -> subprocess.CompletedProcess:
    """Run `caddy <args>` against this Caddyfile, as the proxy container would."""
    return subprocess.run(
        ["docker", "run", "--rm",
         "-e", f"STACK_DOMAIN={DOMAIN}",
         "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro",
         image, "caddy", *args, "--config", "/etc/caddy/Caddyfile"],
        capture_output=True, text=True, timeout=180,
    )


def _adapt(image: str, caddyfile: Path) -> dict:
    result = _caddy(image, caddyfile, "adapt")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _hosts(config: dict) -> set[str]:
    return {
        host
        for server in config["apps"]["http"]["servers"].values()
        for route in server.get("routes", [])
        for match in route.get("match", [])
        for host in match.get("host", [])
    }


def _listen(config: dict) -> set[str]:
    return {addr
            for server in config["apps"]["http"]["servers"].values()
            for addr in server["listen"]}


@pytest.fixture
def plain_caddyfile(tmp_path) -> Path:
    path = tmp_path / "Caddyfile"
    path.write_text(assemble(_shipped_snippets()))
    return path


def test_every_shipped_snippet_assembles_into_a_valid_config(plain_caddyfile):
    result = _caddy(STOCK_CADDY, plain_caddyfile, "validate")
    assert result.returncode == 0, result.stderr


def test_the_bare_domain_and_the_wildcard_are_both_served(plain_caddyfile):
    """The bare domain is core's home and `/go` links. The wildcard answers
    every other name, and covers neither the bare name's DNS record nor its
    certificate, so both have to be sites of their own."""
    hosts = _hosts(_adapt(STOCK_CADDY, plain_caddyfile))
    assert DOMAIN in hosts
    assert f"*.{DOMAIN}" in hosts


def test_without_tls_every_site_is_plain_http_on_port_80(plain_caddyfile):
    config = _adapt(STOCK_CADDY, plain_caddyfile)
    assert _listen(config) == {":80"}
    assert "tls" not in config.get("apps", {})
