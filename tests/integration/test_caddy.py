"""The assembled Caddyfile, as a real Caddy reads it, and the proxy running.

The unit tests pin which snippets the framework puts in the file and when.
Whether Caddy accepts the result, and what it serves from it, only Caddy
can say, so these hand it the snippets that ship in the repo and read the
JSON config it adapts them to. The last part runs `stack up infra` on a
throwaway instance and talks to the proxy the way a device on the LAN
would.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "unit" / "framework" / "fixtures"
sys.path.insert(0, str(REPO_ROOT / "lib"))

from stack.caddy import DNS_PROVIDERS, assemble  # noqa: E402

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


# Never a real credential: nothing here reaches a DNS provider. Shaped
# like a Cloudflare token, because its plugin refuses to load a config
# whose token does not look like one.
FAKE_TOKEN = "fake" + "0" * 36


def _caddy(image: str, caddyfile: Path, *args: str) -> subprocess.CompletedProcess:
    """Run `caddy <args>` against this Caddyfile, as the proxy container would."""
    return subprocess.run(
        ["docker", "run", "--rm",
         "-e", f"STACK_DOMAIN={DOMAIN}",
         "-e", f"DNS_API_TOKEN={FAKE_TOKEN}",
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


# ── The infra image: TLS through each DNS provider ────────────────────────

INFRA_IMAGE = "stack-infra-caddy:local"


@pytest.fixture(scope="module")
def infra_image() -> str:
    """The image `stack up infra` builds, under the tag its compose file uses."""
    result = subprocess.run(
        ["docker", "build", "-q", "-t", INFRA_IMAGE, str(REPO_ROOT / "stacklets" / "infra")],
        capture_output=True, text=True, timeout=900,
    )
    assert result.returncode == 0, result.stderr
    return INFRA_IMAGE


def _tls_caddyfile(tmp_path: Path, provider: str) -> Path:
    path = tmp_path / "Caddyfile"
    path.write_text(assemble(_shipped_snippets(), dns_provider=provider))
    return path


@pytest.mark.parametrize("provider", sorted(DNS_PROVIDERS))
def test_the_image_has_a_plugin_for_every_supported_provider(infra_image, tmp_path, provider):
    result = _caddy(infra_image, _tls_caddyfile(tmp_path, provider), "validate")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("provider", sorted(DNS_PROVIDERS))
def test_every_certificate_is_requested_through_the_providers_dns(infra_image, tmp_path, provider):
    config = _adapt(infra_image, _tls_caddyfile(tmp_path, provider))
    challenges = [issuer["challenges"]["dns"]
                  for policy in config["apps"]["tls"]["automation"]["policies"]
                  for issuer in policy["issuers"]]
    assert challenges
    assert {c["provider"]["name"] for c in challenges} == {provider}
    # Still the placeholder: the token is read from the environment at
    # request time, so it is not in the config Caddy's admin endpoint serves.
    assert FAKE_TOKEN not in json.dumps(config)


def test_caddy_asks_for_the_wildcard_and_the_bare_domain_and_nothing_else(infra_image, tmp_path):
    """Two certificates cover every site: subdomain sites reuse the
    wildcard, and the bare domain, which the wildcard does not cover, gets
    its own. Anything more would put every subdomain in public
    Certificate Transparency logs and spend Let's Encrypt's rate limit.

    The ACME directory is pointed at a closed local port, so the requests
    Caddy starts fail at once and nothing leaves this machine.
    """
    caddyfile = _tls_caddyfile(tmp_path, "cloudflare")
    caddyfile.write_text(caddyfile.read_text().replace(
        "cert_issuer acme {", "cert_issuer acme http://127.0.0.1:9/directory {"))

    name = "stack-test-caddy-certs"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
    subprocess.run(
        ["docker", "run", "-d", "--name", name,
         "-e", f"STACK_DOMAIN={DOMAIN}", "-e", f"DNS_API_TOKEN={FAKE_TOKEN}",
         "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro", infra_image],
        check=True, capture_output=True, timeout=60,
    )
    try:
        deadline = time.monotonic() + 20
        while len(_certificates_requested(name)) < 2 and time.monotonic() < deadline:
            time.sleep(1)
        time.sleep(2)  # room for a third request, if Caddy were to make one
        requested = _certificates_requested(name)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)

    assert requested == {DOMAIN, f"*.{DOMAIN}"}


def _certificates_requested(container: str) -> set[str]:
    """Names Caddy has started obtaining a certificate for, from its log."""
    logs = subprocess.run(["docker", "logs", container],
                          capture_output=True, text=True, timeout=30).stderr
    entries = [json.loads(line) for line in logs.splitlines() if line.startswith("{")]
    return {e["identifier"] for e in entries if e.get("msg") == "obtaining certificate"}


# ── stack up infra ────────────────────────────────────────────────────────


class Instance:
    """A throwaway instance: the repo's infra, the Alpine test stacklet
    with a route, domain mode, no TLS."""

    def __init__(self, root: Path):
        self.root = root

    def stack(self, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "stack", *args],
            cwd=str(self.root), capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONPATH": str(self.root / "lib")},
        )

    def ok(self, *args: str) -> None:
        result = self.stack(*args)
        assert result.returncode == 0, (
            f"`stack {' '.join(args)}` exited {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")


@pytest.fixture(scope="module")
def instance(tmp_path_factory):
    root = tmp_path_factory.mktemp("domain") / "stack"
    (root / "stacklets").mkdir(parents=True)
    (root / "lib").symlink_to(REPO_ROOT / "lib")
    shutil.copytree(REPO_ROOT / "stacklets" / "infra", root / "stacklets" / "infra")
    shutil.copytree(FIXTURES_DIR / "test", root / "stacklets" / "test")
    (root / "stacklets" / "test" / "caddy.snippet").write_text(
        "test.{$STACK_DOMAIN} {\n\treverse_proxy stack-test:8080\n}\n")

    (root / "stack.toml").write_text(
        f'[core]\nname = "teststack"\ndomain = "{DOMAIN}"\ndns_provider = ""\n'
        f'data_dir = "{root.parent / "data"}"\n'
        f'extension_dirs = ["{root / "extensions"}"]\ntimezone = "Europe/Berlin"\n')
    (root / "users.toml").write_text(
        '[[users]]\nname = "Test Admin"\nemail = "admin@test.local"\n'
        'password = "testpass"\nrole = "admin"\n')
    (root / ".stack").mkdir()
    (root / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "testpass"\n')

    inst = Instance(root)
    yield inst

    inst.stack("destroy", "test", "--yes")
    inst.stack("destroy", "infra", "--yes")
    # `destroy` skips containers of a stacklet whose first `up` failed, so
    # a failed run would otherwise leave these holding their ports.
    subprocess.run(["docker", "rm", "-f", "stack-test", "stack-infra-caddy",
                    "stack-infra-adguard"], capture_output=True, timeout=60)


def _get(host: str) -> tuple[int, str]:
    """GET / through the proxy on the host's port 80, as `host`."""
    request = urllib.request.Request("http://127.0.0.1/", headers={"Host": host})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _host_ips(container: str) -> dict[str, str]:
    """Published container port -> host address it is bound to."""
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{json .HostConfig.PortBindings}}", container],
        capture_output=True, text=True, timeout=30, check=True)
    return {port: bindings[0]["HostIp"]
            for port, bindings in json.loads(result.stdout).items()}


@pytest.mark.container_lifecycle
def test_the_proxy_and_dns_face_the_lan_and_nothing_else_does(instance):
    instance.ok("up", "infra")

    assert _host_ips("stack-infra-caddy") == {"80/tcp": "0.0.0.0", "443/tcp": "0.0.0.0"}
    adguard = _host_ips("stack-infra-adguard")
    assert adguard["53/udp"] == adguard["53/tcp"] == "0.0.0.0"
    assert adguard["80/tcp"] == adguard["3000/tcp"] == "127.0.0.1"


@pytest.mark.container_lifecycle
def test_a_stacklet_is_served_from_the_moment_it_is_up_until_it_is_down(instance):
    instance.ok("up", "infra")
    assert _get(f"test.{DOMAIN}")[0] == 404

    instance.ok("up", "test")
    assert _get(f"test.{DOMAIN}") == (200, "ok")

    instance.ok("down", "test")
    status, body = _get(f"test.{DOMAIN}")
    assert (status, body) == (404, f"No service at test.{DOMAIN}")
