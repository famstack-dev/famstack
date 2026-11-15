"""`./stack restart ai` keeps famstack's oMLX running.

A restart is a stop and a start within the same second. launchd lets a
removed job go only once its process has ended: right after `launchctl
remove` it still lists the job, and a `launchctl load` in that moment
prints `Load failed: 5: Input/output error`, loads nothing, and exits 0.
A start that trusted the exit code reported oMLX as loaded while launchd
had dropped it, and the AI stayed down until the next `stack up ai`.

The commands run through `./stack` in this process (`StackCLI`), on a
Simpsons instance whose `[ai]` names famstack's oMLX, prepared: Homebrew
has oMLX (`brew` stood in), oMLX has the language model and the house
voice (oMLX's admin API stood in, `omlx_stand_in.py`, at `[ai]
openai_url`). `launchctl` is a launchd that remembers its jobs and lets a
removed one go as a real one does (`launchd_stand_in.py`). Speech is off:
Whisper's health check would ask a port on this Mac, and Whisper starts
through the same code as oMLX (`host_services.start`).
"""

from __future__ import annotations

import json

import pytest

from tests.unit.stacklets.launchd_stand_in import launchd
from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

from .conftest import StackCLI, write_instance

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

OMLX_JOB = "dev.famstack.omlx"
WHISPER_JOB = "dev.famstack.whisper"

# Homebrew with oMLX installed.
BREW = """#!/bin/sh
case "$1 $2" in
  "list --versions") echo "omlx 0.8.0rc1" ;;
esac
exit 0
"""

# Docker with the stack's network and no container: the ai stacklet runs
# none, but every start makes sure the network exists.
# The stack calls it as `docker --context <name> network inspect stack`.
DOCKER = """#!/bin/sh
[ "$3 $4" = "network inspect" ] && exit 0
exec "{stand_in}/docker" "$@"
"""


class Mac:
    """The Simpsons' Mac: `./stack` on its instance, and what launchd has."""

    def __init__(self, stack: StackCLI, jobs, home):
        self.stack, self.jobs, self.home = stack, jobs, home

    def launchd_runs_omlx(self) -> bool:
        return (self.jobs / OMLX_JOB).exists()

    def launchd_lists(self) -> list[str]:
        """The labels launchd lists, leaving ones too, without asking
        `launchctl list`, which would move a leaving job on."""
        return sorted({p.name.removesuffix(".leaving") for p in self.jobs.iterdir()})

    def omlx_agent_file(self):
        return self.home / "Library" / "LaunchAgents" / f"{OMLX_JOB}.plist"


@pytest.fixture
def mac(tmp_path, monkeypatch, as_stack_runs_it, _stand_in_folder, httpserver) -> Mac:
    from stacklets.ai import omlx_app

    bin_dir, jobs = launchd(tmp_path, monkeypatch)
    (bin_dir / "brew").write_text(BREW)
    (bin_dir / "docker").write_text(DOCKER.format(stand_in=_stand_in_folder))
    for tool in ("brew", "docker"):
        (bin_dir / tool).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{_stand_in_folder}:/usr/bin:/bin")
    # The oMLX app does not run, whatever the Mac that runs the tests has.
    monkeypatch.setattr(omlx_app, "PROCESS_COMMAND", ("false",))
    home = tmp_path / "home"
    (home / ".omlx").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    OMLX(httpserver, downloaded=["Qwen3.5-9B-MLX-4bit", "Kokoro-82M-bf16"])
    instance = write_instance(tmp_path, extra=(
        f'\n[ai]\nprovider = "managed"\nopenai_url = "{httpserver.url_for("/v1")}"\n'
        f'openai_key = "{KEY}"\ndefault = "mlx-community/Qwen3.5-9B-MLX-4bit"\n'
        'speech = false\n'))
    settings = tmp_path / "data" / "ai" / "omlx" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"auth": {"api_key": KEY}}))
    return Mac(StackCLI(instance, monkeypatch), jobs, home)


def test_a_restart_keeps_omlx_running(mac):
    """Given famstack's oMLX runs, when `./stack restart ai` runs twice in
    a row, then launchd runs oMLX after each, and its agent file is there
    for the next login. A restart is what `./stack restart` runs after
    every update that changes the stack's code."""
    up = mac.stack("up", "ai")
    assert up.code == 0, up.err + up.out
    assert mac.launchd_runs_omlx()

    for _ in range(2):
        restarted = mac.stack("restart", "ai")

        assert restarted.code == 0, restarted.err + restarted.out
        assert mac.launchd_runs_omlx()
        assert mac.omlx_agent_file().exists()


def test_a_stop_returns_once_launchd_let_omlx_go(mac):
    """Given famstack's oMLX runs, when `./stack down ai` ends, then
    launchd no longer lists it: whatever starts next finds the port and
    the label free."""
    mac.stack("up", "ai")

    down = mac.stack("down", "ai")

    assert down.code == 0, down.err + down.out
    assert mac.launchd_lists() == []


def test_an_ai_that_does_not_answer_after_its_start_is_not_called_running(mac, monkeypatch):
    """Given famstack's oMLX started and then stopped answering, when the
    AI's health check fails, then the start does not say the AI runs: it
    names the check that failed and the command that asks the AI. The
    exit code stays as for every stacklet that started but does not
    answer. The health check waits out its timeout as on a Mac, so the
    test stands in its result."""
    import stack.docker

    monkeypatch.setattr(stack.docker, "wait_for_health", lambda *_a, **_k: "timeout")

    up = mac.stack("up", "ai")

    assert up.code == 0, up.err + up.out
    assert "AI is running" not in up.out
    assert "./stack ai check" in up.out


def _whisper_runs(mac) -> None:
    """famstack's Whisper runs beside oMLX: launchd has its job, and its
    agent file is there."""
    (mac.jobs / WHISPER_JOB).touch()
    agent = mac.home / "Library" / "LaunchAgents" / f"{WHISPER_JOB}.plist"
    agent.write_text("<plist/>")


def test_a_stop_that_fails_stops_the_other_services_and_says_so(mac, monkeypatch):
    """Given oMLX's process does not end after its stop, when `./stack
    down ai` runs, then Whisper still stops, the run fails and names oMLX,
    and the AI still counts as up. A stop that only reported success would
    leave a service running that the stack no longer accounts for."""
    from stack import launchd as launchd_module
    mac.stack("up", "ai")
    _whisper_runs(mac)
    monkeypatch.setenv("LAUNCHD_STUCK", OMLX_JOB)
    monkeypatch.setattr(launchd_module, "STOP_SECONDS", 0.3)

    down = mac.stack("down", "ai")

    assert down.code != 0
    assert OMLX_JOB in down.err + down.out
    assert WHISPER_JOB not in mac.launchd_lists()
    assert (mac.stack.instance / ".stack" / "ai.up").exists()


def test_a_destroy_keeps_the_data_while_a_service_still_runs(mac, monkeypatch):
    """Given oMLX's process does not end after its stop, when `./stack
    destroy ai --yes` runs, then the AI's data folder and secrets stay,
    and the run fails and names oMLX: deleting them under a running
    service leaves it without its settings."""
    from stack import launchd as launchd_module
    mac.stack("up", "ai")
    monkeypatch.setenv("LAUNCHD_STUCK", OMLX_JOB)
    monkeypatch.setattr(launchd_module, "STOP_SECONDS", 0.3)
    data = mac.stack.instance.parent / "data" / "ai"
    secrets = (mac.stack.instance / ".stack" / "secrets.toml").read_text()

    destroyed = mac.stack("destroy", "ai", "--yes")

    assert destroyed.code != 0
    assert OMLX_JOB in destroyed.err + destroyed.out
    assert data.exists()
    assert (mac.stack.instance / ".stack" / "secrets.toml").read_text() == secrets


def test_an_uninstall_names_a_stacklet_whose_service_still_runs(mac, monkeypatch):
    """Given oMLX's process does not end after its stop, when `./stack
    uninstall --yes` runs, then it marks the AI as not uninstalled, goes on
    with the other stacklets, keeps the AI's data and the stack's secrets,
    and fails at the end naming the AI. A run that printed "uninstalled"
    for it would leave a service running that nothing accounts for."""
    from stack import launchd as launchd_module
    mac.stack("up", "ai")
    monkeypatch.setenv("LAUNCHD_STUCK", OMLX_JOB)
    monkeypatch.setattr(launchd_module, "STOP_SECONDS", 0.3)
    secrets = mac.stack.instance / ".stack" / "secrets.toml"
    kept = secrets.read_text()

    uninstalled = mac.stack("uninstall", "--yes")

    said = uninstalled.err + uninstalled.out
    assert uninstalled.code != 0
    assert "AI uninstalled" not in said
    assert OMLX_JOB in said
    assert (mac.stack.instance.parent / "data" / "ai").exists()
    assert secrets.read_text() == kept
