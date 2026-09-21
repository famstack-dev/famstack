"""Synapse rate limits relaxed for a family server, on every install.

Synapse's defaults are sized for a public homeserver. The one that hurt:
an account may receive five invites in a burst and then one every 333
seconds, so the sixth room a bot sets up for the same person hangs for
five and a half minutes. The e2e rig paid that four times a run.

`homeserver.yaml` is written once, at install, so a new default in the
installer never reaches an existing server. `on_start` adds the limits
famstack relaxes on every start instead, and only where the file has
no value of its own: an admin's setting always wins.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

_spec = importlib.util.spec_from_file_location(
    "messages_hooks_on_start",
    _REPO_ROOT / "stacklets" / "messages" / "hooks" / "on_start.py")
on_start = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(on_start)


def _server(tmp_path, config) -> Path:
    conf = tmp_path / "messages" / "synapse" / "homeserver.yaml"
    conf.parent.mkdir(parents=True)
    conf.write_text(config if isinstance(config, str) else json.dumps(config))
    return conf


def _start(tmp_path):
    return on_start.run(SimpleNamespace(stack=SimpleNamespace(data=tmp_path)))


def test_an_existing_server_gains_the_invite_limit(tmp_path):
    conf = _server(tmp_path, {"server_name": "simpson"})
    assert _start(tmp_path)["ok"]
    config = json.loads(conf.read_text())
    assert config["server_name"] == "simpson"
    assert config["rc_invites"]["per_user"]["burst_count"] >= 20
    assert "rc_message" in config and "rc_login" in config


def test_an_admins_own_value_is_kept(tmp_path):
    own = {"per_user": {"per_second": 0.01, "burst_count": 3}}
    conf = _server(tmp_path, {"server_name": "simpson", "rc_invites": own})
    _start(tmp_path)
    assert json.loads(conf.read_text())["rc_invites"] == own


def test_a_complete_config_is_not_rewritten(tmp_path):
    conf = _server(tmp_path, {"server_name": "simpson"})
    _start(tmp_path)
    before = conf.stat().st_mtime_ns
    _start(tmp_path)
    assert conf.stat().st_mtime_ns == before


def test_a_hand_written_yaml_config_is_left_alone(tmp_path):
    """Synapse reads YAML; the installer writes JSON, which is a subset.
    An admin who rewrote it as YAML keeps their file byte for byte, and
    the start goes ahead."""
    text = "server_name: simpson\nrc_message:\n  per_second: 1\n"
    conf = _server(tmp_path, text)
    assert _start(tmp_path)["ok"]
    assert conf.read_text() == text
