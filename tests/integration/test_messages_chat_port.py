"""The chat port, as a phone app meets it: a TLS hello ends at once, HTTP reaches Synapse.

The chat apps sign in at `http://<mac>:42031`, the port the messages
stacklet publishes for Synapse. Element X on Android sends TLS hellos to
that address before it speaks plain HTTP, although the address the person
typed starts with `http://`. Synapse's plain HTTP listener reads a TLS
hello as the start of a request line and waits for the rest, which never
comes, so each hello waits for the app's own timeout (about 90 seconds)
before the app falls back to plain HTTP. A front that ends a TLS hello at
once lets the sign-in screen answer within seconds.

The same nginx serves Element Web. In port mode a browser sends it every
cookie of localhost, so both ports must take a large Cookie header.

Only the containers can say how the port answers, so this module starts
them: the services of the shipped `docker-compose.yml`, as `docker compose
config` resolves them, with the images, mounts and port it names. Each runs
under a name of its own on a network of its own, with its compose name as
the network alias, and Docker picks the host port. The rig's `stack-messages-*`
containers and the `stack` network are never touched. Postgres is not
started: Synapse runs on the SQLite config its image generates, because how
the port answers does not depend on the database.

    uv run --extra test pytest -q tests/integration/test_messages_chat_port.py

Starting Synapse takes about ten seconds, once for the module.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.integration.forgejo_container import needs_docker

pytestmark = needs_docker

REPO_ROOT = Path(__file__).parent.parent.parent
MESSAGES = REPO_ROOT / "stacklets" / "messages"

# The port the phone apps sign in at (`[ports] synapse` in stacklet.toml).
CHAT_PORT = 42031
# The port people open Element Web at (`[ports] element` in stacklet.toml).
ELEMENT_PORT = 42030
SYNAPSE = "stack-messages-synapse"
DATABASE = "stack-messages-db"

# A front that ends a TLS hello does so within milliseconds; the hang it
# replaces lasts until the client gives up. Two seconds tells them apart
# on a loaded machine.
TLS_ANSWER_SECONDS = 2.0

# Larger than nginx's default request body limit (1 MB), smaller than
# Synapse's own upload limit (100 MB, hooks/on_install.sh): a photo of
# this size must reach Synapse, which then answers for itself.
PHOTO_BYTES = 5 * 1024 * 1024

# In port mode, every app on localhost shares one cookie jar. The browser
# sends all of it to every port. 10 KB is more than the default limit of
# nginx for one header line (8 KB). A few signed-in apps reach that size.
COOKIES = "; ".join(f"c{i}=" + "x" * 400 for i in range(25))


# ── The tests ────────────────────────────────────────────────────────────

def test_a_tls_hello_to_the_chat_port_ends_at_once(chat_port):
    """Given the messages containers as the stacklet ships them,
    when an app opens a TLS handshake on the plain-HTTP chat port,
    then the handshake fails within two seconds instead of hanging.

    Element X tries TLS first; each hello that hangs costs a first sign-in
    about 90 seconds before the app falls back to plain HTTP.
    """
    took = _tls_hello_seconds(chat_port)

    assert took < TLS_ANSWER_SECONDS, f"the TLS hello hung for {took:.1f}s"


def test_plain_http_on_the_chat_port_reaches_synapse(chat_port):
    """Given the messages containers, when an app asks the chat port for
    the Matrix versions over plain HTTP, then Synapse answers.

    This is the request every Matrix app sends first to a homeserver.
    """
    with urllib.request.urlopen(f"http://127.0.0.1:{chat_port}/_matrix/client/versions",
                                timeout=5) as resp:
        answer = json.load(resp)

    assert "v1.1" in answer["versions"]


def test_a_photo_upload_on_the_chat_port_reaches_synapse(chat_port):
    """Given the messages containers, when an app uploads a 5 MB photo to
    the chat port without signing in, then Synapse itself refuses it for
    the missing token, not a front for its size.

    Families send photos and videos; Synapse's own limit is 100 MB.
    """
    request = urllib.request.Request(
        f"http://127.0.0.1:{chat_port}/_matrix/media/v3/upload?filename=photo.jpg",
        data=b"\xff" * PHOTO_BYTES, method="POST",
        headers={"Content-Type": "image/jpeg"})

    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=30)

    assert json.load(refused.value)["errcode"] == "M_MISSING_TOKEN"


def test_element_web_opens_with_many_localhost_cookies(published_ports):
    """Given a browser that holds 10 KB of localhost cookies, when it opens
    Element Web, then the page loads instead of nginx's 400 "Request
    Header Or Cookie Too Large". A browser without cookies loads it too."""
    element = f"http://127.0.0.1:{published_ports[ELEMENT_PORT]}/"

    with urllib.request.urlopen(element, timeout=5) as resp:
        assert resp.status == 200
    request = urllib.request.Request(element, headers={"Cookie": COOKIES})
    with urllib.request.urlopen(request, timeout=5) as resp:
        assert resp.status == 200


def test_the_chat_port_answers_with_many_localhost_cookies(chat_port):
    """Given a browser that holds 10 KB of localhost cookies, when Element
    Web asks the chat port for the Matrix versions, then Synapse answers."""
    request = urllib.request.Request(f"http://127.0.0.1:{chat_port}/_matrix/client/versions",
                                     headers={"Cookie": COOKIES})

    with urllib.request.urlopen(request, timeout=5) as resp:
        assert "v1.1" in json.load(resp)["versions"]


# ── The containers ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def chat_port(published_ports) -> int:
    """The host port that stands for the chat port, once Synapse answers behind it."""
    return published_ports[CHAT_PORT]


@pytest.fixture(scope="module")
def published_ports(tmp_path_factory) -> Iterator[dict[int, int]]:
    """Each port the messages containers publish, mapped to the host port
    that stands for it in this run, once Synapse answers behind the chat
    port. Every container and the network go on the way out."""
    run = f"stack-test-chat-port-{uuid.uuid4().hex[:8]}"
    synapse_data = tmp_path_factory.mktemp("synapse")
    services = _compose_services(synapse_data)
    _generate_synapse_config(services[SYNAPSE]["image"], synapse_data)
    (synapse_data / "element-config.json").write_text("{}")

    _docker("network", "create", run)
    started: list[str] = []
    try:
        for name, service in services.items():
            if name != DATABASE:
                started.append(_start(run, name, service))
        ports = _published(run, services)
        _wait_for_synapse(ports[CHAT_PORT], started)
        yield ports
    finally:
        for container in started:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=60)
        subprocess.run(["docker", "network", "rm", run], capture_output=True, timeout=60)


def _compose_services(synapse_data: Path) -> dict:
    """The messages services as `docker compose config` resolves the shipped file."""
    env = {**os.environ, "SYNAPSE_DATA": str(synapse_data),
           "MESSAGES_DB_DATA": str(synapse_data / "unused-postgres"),
           "DB_USER": "synapse", "DB_PASSWORD": "unused", "DB_NAME": "synapse",
           "PORT_BIND_IP": "127.0.0.1"}
    config = subprocess.run(
        ["docker", "compose", "-f", str(MESSAGES / "docker-compose.yml"),
         "config", "--format", "json"],
        check=True, capture_output=True, text=True, env=env, timeout=60).stdout
    return json.loads(config)["services"]


def _generate_synapse_config(image: str, data: Path) -> None:
    """A homeserver.yaml as Synapse's image writes it: SQLite, plain HTTP on 8008."""
    _docker("run", "--rm", "-v", f"{data}:/data",
            "-e", "SYNAPSE_SERVER_NAME=simpson", "-e", "SYNAPSE_REPORT_STATS=no",
            image, "generate", timeout=300)


def _start(run: str, name: str, service: dict) -> str:
    """Start one compose service under a name of this run; returns the container's name.

    Its ports go to a host port Docker picks on 127.0.0.1, so nothing here
    can take a port the rig listens on.
    """
    container = f"{run}-{name}"
    args = ["run", "-d", "--name", container, "--network", run, "--network-alias", name]
    for mount in service.get("volumes", []):
        suffix = ":ro" if mount.get("read_only") else ""
        args += ["-v", f"{mount['source']}:{mount['target']}{suffix}"]
    for port in service.get("ports", []):
        args += ["-p", f"127.0.0.1::{port['target']}"]
    _docker(*args, service["image"], timeout=300)
    return container


def _published(run: str, services: dict) -> dict[int, int]:
    """Each port the compose file publishes, mapped to the host port Docker
    picked for it in this run. Fails when the chat port is not among them."""
    ports: dict[int, int] = {}
    for name, service in services.items():
        for port in service.get("ports", []):
            address = _docker("port", f"{run}-{name}", f"{port['target']}/tcp")
            ports[int(port["published"])] = int(address.splitlines()[0].rsplit(":", 1)[1])
    if CHAT_PORT not in ports:
        raise AssertionError(f"no messages service publishes port {CHAT_PORT}")
    return ports


def _wait_for_synapse(port: int, containers: list[str]) -> None:
    """Return once the chat port answers the Matrix versions request with 200."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_matrix/client/versions",
                                        timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    logs = "\n".join(_docker("logs", c)[-1500:] for c in containers)
    raise AssertionError(f"Synapse did not answer on the chat port:\n{logs}")


# ── Probes ───────────────────────────────────────────────────────────────

def _tls_hello_seconds(port: int) -> float:
    """Seconds until a TLS handshake on `port` ends, as a client sees it.

    A plain HTTP server cannot complete the handshake, so it always ends in
    an error: a closed connection or bytes that are not TLS. A handshake
    that never ends is cut at twice the limit the test allows.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", port), timeout=2 * TLS_ANSWER_SECONDS) as raw:
        try:
            context.wrap_socket(raw, server_hostname="simpson")
        except (ssl.SSLError, ConnectionError, TimeoutError):
            pass
    return time.monotonic() - started


def _docker(*args: str, timeout: int = 120) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()
