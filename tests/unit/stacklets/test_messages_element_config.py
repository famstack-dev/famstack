"""Element follows the homeserver URL the stack hands out.

Element Web finds Synapse through `base_url` in `element-config.json`,
which the installer writes once. The URL it holds changes when the
instance moves to domain mode, and again when `dns_provider` turns on
HTTPS: an Element served over https that still calls an http homeserver
is blocked by the browser as mixed content, and the family sees a login
page that cannot log in. `on_start` sets `base_url` to the rendered
`SYNAPSE_PUBLIC_URL` on every start. Everything else in the file is left
as the admin has it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

_spec = importlib.util.spec_from_file_location(
    "messages_hooks_on_start_element",
    _REPO_ROOT / "stacklets" / "messages" / "hooks" / "on_start.py")
assert _spec and _spec.loader
on_start = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(on_start)

PORT_MODE_URL = "http://192.0.2.10:42031"
DOMAIN_URL = "https://messages.home.example.family"


def _element_config(tmp_path, base_url: str) -> Path:
    conf = tmp_path / "messages" / "synapse" / "element-config.json"
    conf.parent.mkdir(parents=True)
    conf.write_text(json.dumps({
        "default_server_config": {
            "m.homeserver": {"base_url": base_url, "server_name": "simpson"}},
        "brand": "Simpsons Chat",
        "default_theme": "light",
    }))
    return conf


def _start(tmp_path, synapse_url: str):
    return on_start.run(SimpleNamespace(
        stack=SimpleNamespace(data=tmp_path),
        env={"SYNAPSE_PUBLIC_URL": synapse_url}))


def test_element_follows_the_homeserver_to_domain_mode(tmp_path):
    conf = _element_config(tmp_path, PORT_MODE_URL)
    _start(tmp_path, DOMAIN_URL)
    homeserver = json.loads(conf.read_text())["default_server_config"]["m.homeserver"]
    assert homeserver["base_url"] == DOMAIN_URL
    assert homeserver["server_name"] == "simpson"


def test_the_rest_of_the_config_stays_the_admins(tmp_path):
    conf = _element_config(tmp_path, PORT_MODE_URL)
    _start(tmp_path, DOMAIN_URL)
    config = json.loads(conf.read_text())
    assert config["brand"] == "Simpsons Chat"
    assert config["default_theme"] == "light"


def test_before_the_installer_has_written_it_nothing_is_created(tmp_path):
    _start(tmp_path, DOMAIN_URL)
    assert not (tmp_path / "messages" / "synapse" / "element-config.json").exists()
