"""`stack list` says which stacklets ask questions on their first `up`.

Some stacklets ask the admin something before they install (`backup`
which disk, `ai` which model): their first `up` needs a terminal. A
screen that offers to add a stacklet cannot answer those questions yet,
so it offers the terminal command for these instead of a button, and it
learns which they are from `stack list --json` (`asks_on_first_up`),
since it cannot read the stacklets' directories.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import stack.docker
from stack import Stack

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def rows(tmp_path, monkeypatch) -> dict:
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nhost = "server.lan"\n'
        '\n[messages]\nserver_name = "simpson"\n')
    (tmp_path / ".stack").mkdir()
    # No Docker in the unit lane: every stacklet reads as not running.
    monkeypatch.setattr(stack.docker, "project_states", lambda: {})
    listed = Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path).list()
    return {s["id"]: s for s in listed["stacklets"]}


@pytest.mark.parametrize("sid", ["ai", "backup", "messages", "infra"])
def test_a_stacklet_that_asks_on_its_first_up_says_so(rows, sid):
    assert rows[sid]["asks_on_first_up"] is True


@pytest.mark.parametrize("sid", ["photos", "docs", "code", "memory"])
def test_a_stacklet_that_just_installs_says_it_does_not_ask(rows, sid):
    assert rows[sid]["asks_on_first_up"] is False
