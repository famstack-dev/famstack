"""The agent's container holds its own two secrets and none of the others.

The agent has a shell and a file tool, so it can read every file in its
container. The stack's secret store (`.stack/secrets.toml`) holds every
person's password, the admin's, the databases' and the host API's tokens.
So nothing under `.stack` is mounted into the agent. Its chat password
reaches it through its environment, as its host API token does.

Driven through the real entry point, `stack.cli.main` with `stack up agent`
in `sys.argv`, from a checkout in `tmp_path` that holds a copy of the agent
stacklet the repo ships. The copy keeps the `.env` the start writes out of
the repo, where the rig's containers would read it. Docker is stood in for
in process, as in `test_core_refresh.py`: a build and `compose_up` succeed.
The test reads the compose file as compose reads it, with the `.env` the
start wrote, and checks what the container would get.
"""

from __future__ import annotations

import re
import shutil
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from stack import cli, env_file

from .conftest import STACKLETS, a_closed_port

SERVICE = "stack-agent"

# The variable the agent's entrypoint and its sign-in read the password from.
PASSWORD_VARIABLE = "AGENT_MATRIX_PASSWORD"

# The key the agent's install writes and the bot runner creates the account with.
PASSWORD_SECRET = "agent__AGENT_BOT_PASSWORD"


@pytest.fixture
def docker_that_starts_anything():
    """Docker stood in for: every compose build and start succeeds,
    nothing runs."""
    with patch("stack.docker.active_context", return_value="default"), \
         patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=None), \
         patch("stack.docker.compose_build", return_value=None), \
         patch("stack.docker.compose_up", return_value=(0, "")), \
         patch("stack.docker.running_project_ids", return_value=set()):
        yield


@pytest.fixture
def checkout(tmp_path, monkeypatch, docker_that_starts_anything):
    """A checkout with messages set up and the agent stacklet the repo
    ships, on a large Mac, with an AI server address nothing answers on."""
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "Europe/Berlin"\n'
        f'profile = "large"\n'
        f'[ai]\nopenai_url = "http://localhost:{a_closed_port()}/v1"\n'
        f'[messages]\nserver_name = "springfield.example"\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "donuts"\n')
    (tmp_path / ".stack" / "messages.setup-done").touch()
    messages = tmp_path / "stacklets" / "messages"
    messages.mkdir(parents=True)
    (messages / "stacklet.toml").write_text('id = "messages"\nname = "Messages"\n')
    shutil.copytree(STACKLETS / "agent", tmp_path / "stacklets" / "agent",
                    ignore=shutil.ignore_patterns(".env", "__pycache__"))

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    # A notification goes to the server room through `./stack messages`,
    # which this checkout does not have.
    monkeypatch.setattr(cli, "_notify", lambda _stck, _msg: None)
    return tmp_path


def stack(*args: str) -> int:
    """`./stack <args>` through the real entry point; returns its exit code."""
    with patch.object(sys, "argv", ["stack", *args]):
        try:
            cli.main()
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1
    return 0


def secrets(checkout: Path) -> dict:
    return tomllib.loads((checkout / ".stack" / "secrets.toml").read_text())


# ── The container as compose would start it ──────────────────────────────

_VARIABLE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _substitute(value: str, env: dict[str, str]) -> str:
    """`${NAME}` and `${NAME:-default}` filled in from `env`, as compose does."""
    return _VARIABLE.sub(lambda m: env.get(m.group(1)) or (m.group(2) or ""), value)


def the_container(checkout: Path) -> tuple[dict[str, str], list[Path]]:
    """The agent container's environment and the host paths it mounts,
    from its compose file and the `.env` the start wrote next to it."""
    stacklet = checkout / "stacklets" / "agent"
    env = env_file.read(stacklet / ".env")
    service = yaml.safe_load((stacklet / "docker-compose.yml").read_text())["services"][SERVICE]
    environment = {k: _substitute(str(v), env) for k, v in service["environment"].items()}
    mounts = [(stacklet / _substitute(volume, env).split(":")[0]).resolve()
              for volume in service["volumes"]]
    return environment, mounts


def mounts_under_the_secret_store(checkout: Path, mounts: list[Path]) -> list[Path]:
    store = (checkout / ".stack").resolve()
    return [m for m in mounts if m == store or store in m.parents]


# ── Behaviour ────────────────────────────────────────────────────────────

def test_a_fresh_agent_gets_its_password_and_no_secret_store(checkout):
    """Given a fresh install, when `stack up agent` runs, then the container
    gets the password the install wrote, and mounts nothing under `.stack`.

    The first start renders the environment before the install writes the
    password, so this also pins that the `.env` holds it after the install."""
    assert stack("up", "agent") == 0

    environment, mounts = the_container(checkout)
    assert mounts_under_the_secret_store(checkout, mounts) == []
    assert environment[PASSWORD_VARIABLE]
    assert environment[PASSWORD_VARIABLE] == secrets(checkout)[PASSWORD_SECRET]


def test_an_agent_from_beta_1_keeps_its_password(checkout):
    """Given an agent set up on beta.1, with its password in the secret store,
    when `stack up agent` runs after the update, then the container gets that
    same password and mounts nothing under `.stack`. No password changes,
    because the bot runner created the chat account with this one."""
    store = checkout / ".stack" / "secrets.toml"
    store.write_text(store.read_text() + f'{PASSWORD_SECRET} = "from-beta-1"\n')
    (checkout / ".stack" / "agent.setup-done").touch()

    assert stack("up", "agent") == 0

    environment, mounts = the_container(checkout)
    assert mounts_under_the_secret_store(checkout, mounts) == []
    assert environment[PASSWORD_VARIABLE] == "from-beta-1"
    assert secrets(checkout)[PASSWORD_SECRET] == "from-beta-1"
