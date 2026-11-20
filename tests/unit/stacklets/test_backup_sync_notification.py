"""`stack backup sync` posts its result to the chat server on 127.0.0.1.

After each target the command signs in to Synapse as the stacker bot and
posts the result. It reads Synapse's port from the messages stacklet's
manifest. The test runs the command over a fabricated checkout: one
stacklet with an archive, a stand-in engine that writes a run to the
history, and a messages manifest that names the stand-in Synapse's port.
pytest-httpserver stands in for Synapse; it answers the sign-in and
nothing else, so the post itself fails softly, as it does when the room
is missing.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from stacklets.backup.cli import sync

BOT_PASSWORD = "stacker-secret"

# A stand-in engine: it records one successful run, as the real engine
# does at its end, and touches no disk.
ENGINE = textwrap.dedent("""
    import json, os, pathlib
    logs = pathlib.Path(os.environ["BACKUP_DATA_DIR"]) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "history.jsonl").open("a") as f:
        f.write(json.dumps({"success": True, "duration_seconds": 1}) + "\\n")
""")


@pytest.fixture
def checkout(tmp_path, httpserver):
    """A checkout with the notes stacklet set up, its archive declared,
    the stand-in engine `fake`, and Synapse at the stand-in's port."""
    repo = tmp_path / "repo"
    (repo / "stacklets" / "notes").mkdir(parents=True)
    (repo / "stacklets" / "notes" / "stacklet.toml").write_text(textwrap.dedent("""
        id = "notes"
        [[backup.archive]]
        name = "files"
        path = "{data_dir}/notes"
    """))
    (repo / "stacklets" / "messages").mkdir()
    (repo / "stacklets" / "messages" / "stacklet.toml").write_text(
        f'id = "messages"\n[ports]\nsynapse = {httpserver.port}\n')
    engine = repo / "stacklets" / "backup" / "engines" / "fake" / "sync.py"
    engine.parent.mkdir(parents=True)
    engine.write_text(ENGINE)
    (repo / ".stack").mkdir()
    (repo / ".stack" / "notes.setup-done").touch()
    return repo


def _sync(checkout, data_dir):
    """Run `stack backup sync` as the framework runs it."""
    config = {
        "repo_root": str(checkout),
        "instance_dir": str(checkout),
        "data_dir": str(data_dir),
        "stack": {"backup": {"targets": {"disk": {"engine": "fake"}}}},
        "secrets": {"core__STACKER_BOT_PASSWORD": BOT_PASSWORD},
    }
    return sync.run([], {}, config)


def test_the_result_goes_to_synapse_when_its_port_is_taken_on_ipv6(
        checkout, tmp_path, httpserver, other_server_on_ipv6):
    """Given another process holds Synapse's port on ::1, when a sync runs,
    then the stacker bot signs in at Synapse on 127.0.0.1. With
    `localhost` the bot's password would go to the other process."""
    httpserver.expect_request("/_matrix/client/v3/login", method="POST").respond_with_json(
        {"access_token": "t", "user_id": "@stacker-bot:home", "device_id": "D"})

    result = _sync(checkout, tmp_path / "data")

    assert result["ok"] is True
    logins = [json.loads(r.data) for r, _ in httpserver.log
              if r.path == "/_matrix/client/v3/login"]
    assert [login["password"] for login in logins] == [BOT_PASSWORD]
    assert not other_server_on_ipv6.log, "a request went to the process on ::1"
