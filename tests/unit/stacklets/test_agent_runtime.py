"""`[agent] runtime`: who answers as the agent's account.

By default nanobot does, from the agent stacklet's own container. An
admin can hand the account to another runtime, a stacklet that ships a
`bot/agent.py`, which the bot runner then runs as the agent:

    [agent]
    runtime = "kettle"

The account stays the agent stacklet's either way (its handle, password,
rooms and login state); the bot runner's side of that is pinned in
`tests/unit/surface/test_every_bot_loads.py`. Pinned here is what
`stack up agent` does about it:

  1. The other runtime is told what `[agent] allow` grants, in the same
     place it finds the account's login state.
  2. A runtime the bot runner could not run stops `stack up agent` with
     what to set instead, rather than leaving the agent silent.
  3. nanobot does not sign in as well while another runtime answers.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from stack.stack import Stack
from stack.stacklet_code import load

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT = REPO_ROOT / "stacklets" / "agent"

KETTLE_STATUS = '''\
"""stack kettle status: whether the kettle is on."""
HELP = "Show whether the kettle is on"


def run(args, stacklet, config):
    return {"on": False}
'''


@pytest.fixture
def instance(tmp_path):
    """A throwaway instance whose extension folder holds `kettle`, a
    stacklet with one command and a `bot/agent.py`.

    Returns a function that writes stack.toml's `[agent]` section and
    answers the `ctx` the agent's start hook gets on `stack up agent`.
    """
    extensions = tmp_path / "ext"
    kettle = extensions / "kettle"
    (kettle / "cli").mkdir(parents=True)
    (kettle / "bot").mkdir()
    (kettle / "stacklet.toml").write_text(
        'id = "kettle"\nname = "Kettle"\ndescription = "The kitchen kettle"\n'
        'version = "0.1.0"\ncategory = "automation"\ntype = "host"\n')
    (kettle / "cli" / "status.py").write_text(KETTLE_STATUS)
    (kettle / "bot" / "agent.py").write_text("class AgentBot:\n    pass\n")
    (tmp_path / ".stack").mkdir()

    def configure(agent: str, extension_dirs: list[Path] | None = None):
        listed = ", ".join(f'"{d}"' for d in extension_dirs or [extensions])
        (tmp_path / "stack.toml").write_text(
            f'[core]\ndata_dir = "{tmp_path / "data"}"\nextension_dirs = [{listed}]\n\n'
            f'[agent]\n{agent}\n')
        stack = Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)
        env = stack.env("agent")

        # No AI server: the hook stops there, after what is pinned here.
        return SimpleNamespace(stack=stack, env={
            **env, "AGENT_DATA_DIR": str(tmp_path / "data" / "agent"), "AGENT_OPENAI_URL": ""})

    return configure


def start_agent(ctx):
    """`stack up agent`'s start hook, as far as it gets without an AI server."""
    on_start = load(AGENT, AGENT / "hooks" / "on_start.py")
    with pytest.raises(RuntimeError, match="No AI server"):
        on_start.run(ctx)


def allowed_for_the_runtime(ctx) -> Path:
    return Path(ctx.env["AGENT_DATA_DIR"]) / "bot" / "allowed-commands.json"


# ── the other runtime is told what it may run ──────────────────────────────

def test_another_runtime_reads_the_granted_commands_with_their_help(instance):
    """The runtime puts them in its `stack` tool. It reads them next to the
    account's login state, the one data dir the bot runner gives it."""
    ctx = instance('runtime = "kettle"\nallow = ["kettle"]')

    start_agent(ctx)

    granted = json.loads(allowed_for_the_runtime(ctx).read_text())
    assert granted == [["stack kettle status", "Show whether the kettle is on"]]


def test_with_nothing_allowed_the_runtime_gets_no_list(instance):
    ctx = instance('runtime = "kettle"\nallow = ["kettle"]')
    start_agent(ctx)

    ctx = instance('runtime = "kettle"\nallow = []')
    start_agent(ctx)

    assert not allowed_for_the_runtime(ctx).exists()


# ── a runtime the bot runner could not run ─────────────────────────────────

@pytest.mark.parametrize("runtime", ["toaster", "messages"])
def test_a_runtime_that_ships_no_agent_stops_the_start(instance, runtime):
    """`toaster` is no stacklet at all; `messages` is one without a
    `bot/agent.py`. Either way nanobot would idle and nothing would answer
    as the agent, so the start stops and names what to set."""
    ctx = instance(f'runtime = "{runtime}"')
    on_start = load(AGENT, AGENT / "hooks" / "on_start.py")

    with pytest.raises(RuntimeError) as refused:
        on_start.run(ctx)

    assert f"runtime = '{runtime}'" in str(refused.value)
    assert "nanobot" in str(refused.value)


def test_a_runtime_outside_the_folder_the_bot_runner_sees_stops_the_start(instance, tmp_path):
    """Only the first extension folder is mounted into the bot runner."""
    first = tmp_path / "dev"
    first.mkdir()
    ctx = instance('runtime = "kettle"', extension_dirs=[first, tmp_path / "ext"])
    on_start = load(AGENT, AGENT / "hooks" / "on_start.py")

    with pytest.raises(RuntimeError) as refused:
        on_start.run(ctx)

    assert str(first) in str(refused.value)


def test_nanobot_needs_no_stacklet(instance):
    """The default: the start goes on to its AI server check."""
    start_agent(instance(""))


# ── nanobot stands aside ──────────────────────────────────────────────────

def test_nanobot_does_not_sign_in_while_another_runtime_answers(tmp_path):
    """Two clients signed in as one account would both answer. While
    another runtime answers, the agent container waits instead of starting
    nanobot, and stays up, as `stack up agent` expects of it.

    The entrypoint runs with stand-ins for what it may start, `sleep` (the
    wait) and `nanobot`, each of which only says it was started.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("sleep", "nanobot"):
        (bin_dir / name).write_text(f"#!/bin/sh\necho started {name}\n")
        (bin_dir / name).chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
           "AGENT_RUNTIME": "kettle"}

    result = subprocess.run(["sh", str(AGENT / "entrypoint.sh")], capture_output=True,
                            text=True, env=env, cwd=tmp_path, timeout=30)

    assert "started sleep" in result.stdout, result.stdout + result.stderr
    assert "started nanobot" not in result.stdout
