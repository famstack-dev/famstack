"""Git over SSH works in both hosting modes.

Forgejo shows an SSH clone URL on every repository page, and the code
stacklet's hint prints one. In domain mode the port was published on
the Mac's loopback only, like every service behind the proxy, and the
URL named the LAN IP rather than the host the web UI is on. Neither
worked from another machine.

SSH does not go through Caddy: it is encrypted and authenticated on its
own, so it is published on every interface in both modes, and the clone
URL names the same host as the stacklet's web URL.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))

from stack import Stack  # noqa: E402


@pytest.fixture
def stack(tmp_path):
    """The repo's stacklets over a throwaway instance."""
    def _make(core: str) -> Stack:
        (tmp_path / "stack.toml").write_text(
            f'[core]\n{core}\nhost = "192.0.2.10"\n'
            f'extension_dirs = ["{tmp_path / "ext"}"]\n')
        (tmp_path / ".stack").mkdir(exist_ok=True)
        return Stack(root=REPO, data=tmp_path / "data", instance_dir=tmp_path)
    return _make


def test_in_domain_mode_the_clone_url_names_the_web_host(stack):
    env = stack('domain = "home.example.family"').env("code")
    assert env["FORGEJO__server__SSH_DOMAIN"] == "code.home.example.family"


def test_in_port_mode_it_names_the_macs_address(stack):
    env = stack('domain = ""').env("code")
    assert env["FORGEJO__server__SSH_DOMAIN"] == "192.0.2.10"


def test_ssh_is_published_on_every_interface():
    compose = (REPO / "stacklets" / "code" / "docker-compose.yml").read_text()
    (ssh,) = re.findall(r'-\s*"([^"]*):22"', compose)
    assert ssh.startswith("0.0.0.0:")
