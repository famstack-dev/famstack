"""Synapse fetches no home-network address for a link preview.

Synapse fetches every link in a message to show a preview. Its list of
addresses it must not fetch was empty, so it fetched any address a
message named: the Mac, the router, other devices at home. The messages
stacklet's start hook now writes the private and local ranges into that
list, on a new install and on a server installed before, and leaves
previews of links on the internet as they were.

The tests start the hook as the framework does, on a `homeserver.yaml`
in a temporary data dir, and read the config Synapse would load.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

on_start = importlib.import_module("stacklets.messages.hooks.on_start")

PRIVATE_RANGES = ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                  "100.64.0.0/10", "169.254.0.0/16", "::1/128", "fe80::/10", "fc00::/7"]


def _server(tmp_path, config) -> Path:
    conf = tmp_path / "messages" / "synapse" / "homeserver.yaml"
    conf.parent.mkdir(parents=True)
    conf.write_text(json.dumps(config))
    return conf


def _start(tmp_path) -> dict:
    result = on_start.run(SimpleNamespace(stack=SimpleNamespace(data=tmp_path), env={}))
    assert result["ok"]
    return json.loads((tmp_path / "messages" / "synapse" / "homeserver.yaml").read_text())


def test_a_server_from_before_fetches_no_home_address(tmp_path):
    # What the installer wrote before: previews on, nothing blocked.
    _server(tmp_path, {"server_name": "simpson", "url_preview_enabled": True,
                       "url_preview_ip_range_blacklist": []})
    config = _start(tmp_path)
    blocked = config["url_preview_ip_range_blacklist"]
    assert set(PRIVATE_RANGES) <= set(blocked)
    assert config["url_preview_enabled"] is True
    assert config["server_name"] == "simpson"


def test_a_new_install_fetches_no_home_address(tmp_path):
    # The installer writes previews on and leaves the list to the start hook.
    _server(tmp_path, {"server_name": "simpson", "url_preview_enabled": True})
    config = _start(tmp_path)
    assert "192.168.0.0/16" in config["url_preview_ip_range_blacklist"]
    assert config["url_preview_enabled"] is True


def test_an_admins_own_list_is_kept(tmp_path):
    own = ["10.0.0.0/8", "203.0.113.0/24"]
    _server(tmp_path, {"server_name": "simpson", "url_preview_enabled": True,
                       "url_preview_ip_range_blacklist": own})
    assert _start(tmp_path)["url_preview_ip_range_blacklist"] == own


def test_nothing_else_in_the_config_changes(tmp_path):
    # A server from before that already has every other setting the hook
    # adds: after the start only the preview list differs.
    rest = {"server_name": "simpson", "url_preview_enabled": True,
            **on_start.RATE_LIMITS, **on_start.USER_DIRECTORY}
    _server(tmp_path, {**rest, "url_preview_ip_range_blacklist": []})
    config = _start(tmp_path)
    assert config.pop("url_preview_ip_range_blacklist")
    assert config == rest


def test_no_preview_reaches_the_mac_by_its_container_address(tmp_path):
    """OrbStack gives the Mac an address in 0.0.0.0/8 inside containers
    (`host.docker.internal`), so a link to it is a link into the home."""
    _server(tmp_path, {"server_name": "simpson", "url_preview_enabled": True,
                       "url_preview_ip_range_blacklist": []})
    assert "0.0.0.0/8" in _start(tmp_path)["url_preview_ip_range_blacklist"]
