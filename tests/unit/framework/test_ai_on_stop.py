"""ai on_stop hook: a stop of the AI also removes 0.4's speech container.

An instance updated from 0.4 can still run `stack-ai-speech` (Piper, in the
ai stacklet's compose project). The ai stacklet has no compose file of its
own any more, so `stack down ai` stops only the host services, and the
container kept the AI counted as up: `stack restart --with-snapshot` then
refused the snapshot and left every stacklet down. The stop hook removes
the container and its image, as the start hook does. launchd and docker are
stand-ins first on PATH, so the hook touches neither on the Mac running
the suite.
"""

from pathlib import Path

import pytest

from tests.unit.framework.test_ai_on_start import DOCKER
from tests.unit.stacklets.launchd_stand_in import launchd as launchd_stand_in

REPO = Path(__file__).parent.parent.parent.parent


def _load_on_stop():
    # Afresh for each test, as the hook runner loads it.
    from stack.stacklet_code import load
    return load(REPO / "stacklets" / "ai", REPO / "stacklets" / "ai" / "hooks" / "on_stop.py")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    # LaunchAgents and markers live in the home directory; keep the test out of it.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


@pytest.fixture(autouse=True)
def launchd(tmp_path, monkeypatch):
    """A launchd first on PATH with nothing loaded, so the stop of oMLX
    and Whisper finds nothing to stop."""
    launchd_stand_in(tmp_path, monkeypatch)


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """The docker stand-in of the start hook's tests first on PATH; the
    commands it was asked to run, without launchd's lines."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(DOCKER)
    (bin_dir / "docker").chmod(0o755)
    calls = tmp_path / "calls"
    monkeypatch.setenv("PATH", str(bin_dir), prepend=":")
    monkeypatch.setenv("CALLS", str(calls))
    return lambda: [line for line in (calls.read_text().splitlines() if calls.exists() else [])
                    if not line.startswith("launchctl ")]


def _stop(make_stack):
    from stack.hooks import StackContext
    steps: list[str] = []
    ctx = StackContext(make_stack(), "ai", {})
    ctx.step = steps.append
    _load_on_stop().run(ctx)
    return steps


def test_a_stop_of_the_ai_removes_the_old_speech_container_and_its_image(
        make_stack, docker, monkeypatch):
    monkeypatch.setenv("PROJECT", "stack-ai")

    steps = _stop(make_stack)

    assert docker() == ["rm -f stack-ai-speech", "image rm sha256:0ld1mage"]
    assert steps == ["Removing the old speech container"]


def test_an_instance_without_it_stops_as_before(make_stack, docker, monkeypatch):
    monkeypatch.setenv("PROJECT", "someone-else")

    steps = _stop(make_stack)

    assert docker() == []
    assert steps == []
