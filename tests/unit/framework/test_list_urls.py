"""`stack list` names the address a person opens for each stacklet.

A client on another machine (the menu bar app over SSH, a script) cannot
use `localhost`, and should not rebuild the address rule itself: the
stack already knows it, because it hands the same URL to the stacklets as
`{url}`. In port mode that is this Mac's LAN address and the port; in
domain mode the stacklet's name under the domain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import stack.docker
from stack import Stack

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def instance(tmp_path, monkeypatch):
    def make(core: str) -> Stack:
        (tmp_path / "stack.toml").write_text(
            f'[core]\ndata_dir = "{tmp_path / "data"}"\n{core}\n'
            '\n[messages]\nserver_name = "simpson"\n')
        (tmp_path / ".stack").mkdir(exist_ok=True)
        return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)
    # No Docker in the unit lane: every stacklet reads as not running.
    monkeypatch.setattr(stack.docker, "project_states", lambda: {})
    return make


def _rows(stck: Stack) -> dict:
    return {s["id"]: s for s in stck.list()["stacklets"]}


def test_port_mode_names_this_macs_address_and_the_port(instance):
    rows = _rows(instance('host = "server.lan"'))
    assert rows["photos"]["url"] == "http://server.lan:42010"
    assert rows["docs"]["url"] == "http://server.lan:42020"


def test_domain_mode_names_the_stacklet_under_the_domain(instance):
    rows = _rows(instance('domain = "home.example"'))
    assert rows["photos"]["url"] == "http://photos.home.example"


def test_a_stacklet_without_a_web_port_has_no_address(instance):
    rows = _rows(instance('host = "server.lan"'))
    assert "url" not in rows["core"]
