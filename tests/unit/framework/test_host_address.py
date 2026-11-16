"""An `[ai]` address as a container reaches it.

Containers and the proxy reach a server on the Mac through Docker's host
gateway; `localhost` inside a container is the container itself. An
address a person typed by hand into stack.toml can be unusable. It then
reaches the containers unchanged, never empty: a client given an empty
address may fall back to a public server, and the family's text must not
leave the house for a typo. Only the proxy leaves its route out.
"""

from pathlib import Path

import pytest

from stack import Stack
from stack.host_address import for_containers, on_this_mac, usable

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("url, expected", [
    ("http://localhost:8888/v1", "http://host.docker.internal:8888/v1"),
    ("http://LOCALHOST:8888/v1", "http://host.docker.internal:8888/v1"),
    ("http://127.0.0.1:9000/v1", "http://host.docker.internal:9000/v1"),
    ("http://[::1]:8888/v1", "http://host.docker.internal:8888/v1"),
    ("http://0.0.0.0:8000/v1", "http://host.docker.internal:8000/v1"),
    ("http://127.0.0.2:8000/v1", "http://host.docker.internal:8000/v1"),
    ("http://[fd00::5]:8000/v1", "http://[fd00::5]:8000/v1"),
    ("https://ai.example.lan/v1", "https://ai.example.lan/v1"),
    ("http://192.168.1.20:11434/v1", "http://192.168.1.20:11434/v1"),
    ("http://user:pw@localhost:8000/v1", "http://user:pw@host.docker.internal:8000/v1"),
])
def test_the_mac_is_reached_through_the_host_gateway(url, expected):
    assert for_containers(url) == expected


@pytest.mark.parametrize("url", [
    "http://localhost:8888/v1", "http://LOCALHOST:8888/v1", "http://127.0.0.1:9000/v1",
    "http://127.0.0.2:8000/v1", "http://[::1]:8888/v1", "http://0.0.0.0:8000/v1",
    "http://192.168.1.20:11434/v1", "https://ai.example.lan/v1", "http://[fd00::5]:8000/v1",
])
def test_the_stack_and_its_containers_agree_on_what_is_this_mac(url):
    """The CLI's `localhost` label and the containers' gateway follow one
    loopback rule: an address is on this Mac for both, or for neither."""
    assert on_this_mac(url) == (for_containers(url) != url)


UNUSABLE = [
    "localhost:8000/v1",        # no scheme
    "http://ai.lan:abc/v1",     # a port that is no number
    "http://ai.lan:70000/v1",   # a port out of range
    "http://[::1/v1",           # a broken IPv6 address
    "ftp://ai.lan/v1",
    "http://ai lan/v1",         # not a host name
    "http://a{env.X}b/v1",      # a Caddy placeholder in the proxy's file
    "http://ai.lan}/v1",
]


@pytest.mark.parametrize("url", ["", *UNUSABLE])
def test_an_unusable_address_is_not_usable(url):
    assert not usable(url)


@pytest.mark.parametrize("url", UNUSABLE)
def test_an_unusable_address_reaches_the_containers_unchanged(url):
    assert for_containers(url) == url


@pytest.mark.parametrize("stacklet, variable", [
    ("chatai", "OPENAI_API_URL"), ("agent", "AGENT_OPENAI_URL")])
def test_a_client_is_never_given_an_empty_ai_address(tmp_path, stacklet, variable):
    """Open WebUI and the agent are the clients that could fall back to a
    public server on an empty address."""
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n'
        '[ai]\nopenai_url = "localhost:8000/v1"\n')
    (tmp_path / ".stack").mkdir()
    stck = Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)

    assert stck.env(stacklet)[variable] == "localhost:8000/v1"
