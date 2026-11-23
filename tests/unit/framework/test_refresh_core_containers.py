"""Which of core's containers a start of another stacklet recreates.

At the end of `stack up <id>` the stack renders core's env again and
recreates core's containers that run with values that are no longer
current, and the bot runner after a first run, which finds new bots only
when it starts. Core's tools server serves the setup page, so it is
recreated only when a value it reads changed: on a fresh install the
setup page stays up while the stacklets the admin picked finish their
first runs.

Driven through the real entry point, `python -m stack` as a process of
its own, from a checkout in `tmp_path` that has core's real
manifest and compose file, and `helper`, a fabricated stacklet with a bot
and nothing core's env reads, as the agent and the AI are. Core's health
check is left out of the manifest, because it asks the port the instance
on this Mac serves. Docker is the `docker_with_projects` stand-in: it
resolves each service's environment from the compose file and core's
`.env`, and keeps what each `compose up` created each container with.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "stacklets" / "core"

BOT_RUNNER = "stack-core-bot-runner"
TOOLS = "stack-core-tools"

# `[ai]` as the installer writes it: no route yet. The setup page's AI step
# picks one later.
NO_ROUTE = '[ai]\nprovider = ""\nopenai_url = ""\nopenai_key = ""\n'

# `[ai]` as the setup page's "Install oMLX" writes it
# (`stack ai switch managed --no-start`), with core still running. It
# changes OPENAI_URL, OPENAI_KEY, WHISPER_URL, WHISPER_KEY and SPEECH_KEY
# in core's env, none of which the tools server reads.
MANAGED = ('[ai]\nprovider = "managed"\nopenai_url = "http://localhost:42060/v1"\n'
           'openai_key = "7fk2-pq9w-m3xd-h8ra"\nwhisper_url = "http://localhost:42062/v1"\n'
           'whisper_key = ""\n')


@pytest.fixture
def docker(docker_with_projects):
    """Docker with core running and helper started before, as the stack's
    own `compose up` of core leaves it once the fixture `checkout` ran."""
    return docker_with_projects(core="running", helper="running")


@pytest.fixture
def checkout(tmp_path, monkeypatch, docker):
    """A checkout with core up, the AI's route not chosen yet, and helper
    never started. Returns a function that writes `stack.toml` with the
    `[ai]` and `[core]` lines it is given."""
    def config(ai: str = NO_ROUTE, timezone: str = "Europe/Berlin") -> None:
        (tmp_path / "stack.toml").write_text(
            f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "{timezone}"\n'
            f'language = "de"\n{ai}[messages]\nserver_name = "simpson"\n')

    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')

    core = tmp_path / "stacklets" / "core"
    core.mkdir(parents=True)
    manifest = (CORE / "stacklet.toml").read_text()
    (core / "stacklet.toml").write_text(re.sub(r"(?ms)^\[health\]\n.*?(?=^\[)", "", manifest))
    (core / "docker-compose.yml").write_text((CORE / "docker-compose.yml").read_text())

    helper = tmp_path / "stacklets" / "helper"
    (helper / "bot").mkdir(parents=True)
    (helper / "stacklet.toml").write_text('id = "helper"\nname = "Helper"\n')
    (helper / "docker-compose.yml").write_text("services:\n  helper:\n    image: busybox\n")
    (helper / "bot" / "bot.toml").write_text('id = "helper-bot"\nname = "Helper"\n')

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)

    config()
    assert stack("up", "core") == 0
    docker.forget()
    return config


def stack(*args: str) -> int:
    """`./stack <args>` in the checkout, as a process of its own, with the
    stand-in `docker` first on `PATH`; returns its exit code and puts its
    output in the failure message when it is not 0. A notification to the
    Server room finds no `./stack` in the checkout and is left out."""
    env = {**os.environ, "PYTHONPATH": str(REPO / "lib")}
    done = subprocess.run([sys.executable, "-m", "stack", *args], cwd=Path.cwd(), env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.returncode


def test_a_first_run_after_the_ai_route_recreates_only_the_bot_runner(checkout, docker):
    """Given core is up and the setup page's AI step wrote `[ai]`, which
    changes five AI keys in core's env and restarts nothing,
    when a stacklet's first run ends (`stack up helper`),
    then only the bot runner is recreated, with the new AI address; the
    tools server keeps running with what it had.

    The tools server serves the setup page the admin has open; its recreate
    took the page away in the middle of the setup."""
    tools_before = docker.container_env(TOOLS)
    checkout(ai=MANAGED)

    assert stack("up", "helper") == 0

    assert docker.recreated("core") == [[BOT_RUNNER]]
    assert docker.container_env(BOT_RUNNER)["OPENAI_URL"] == "http://host.docker.internal:42060/v1"
    assert docker.container_env(TOOLS) == tools_before


def test_refresh_core_recreates_the_tools_server_when_a_value_it_reads_changed(checkout, docker):
    """Given core and helper are up,
    when the time zone changes, which the tools server reads, and the admin
    runs `stack up helper` again,
    then the bot runner and the tools server are recreated with the new
    value; watchtower, which reads nothing of it, keeps running.

    A container that keeps an old value answers with it until its next
    recreate, which may be weeks later."""
    assert stack("up", "helper") == 0
    docker.forget()
    checkout(timezone="America/Chicago")

    assert stack("up", "helper") == 0

    assert docker.recreated("core") == [[BOT_RUNNER, TOOLS]]
    assert docker.container_env(TOOLS)["TIMEZONE"] == "America/Chicago"


def test_refresh_core_after_a_second_up_with_nothing_changed_recreates_nothing(checkout, docker):
    """Given core and helper are up, and nothing changed since,
    when the admin runs `stack up helper` again,
    then no container of core is recreated."""
    assert stack("up", "helper") == 0
    docker.forget()

    assert stack("up", "helper") == 0

    assert docker.recreated("core") == []


def test_up_core_recreates_every_container_of_core(checkout, docker):
    """Given core is up and nothing it reads changed,
    when the admin runs `stack up core`,
    then every container of core is recreated: `stack up` of a stacklet
    always applies it."""
    assert stack("up", "core") == 0

    assert docker.recreated("core") == [[BOT_RUNNER, TOOLS, "stack-core-watchtower"]]

