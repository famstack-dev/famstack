"""ai on_stop hook: a stop of the AI also removes 0.4's speech container.

An instance updated from 0.4 can still run `stack-ai-speech` (Piper, in the
ai stacklet's compose project). The ai stacklet has no compose file of its
own any more, so `stack down ai` stops only the host services, and the
container kept the AI counted as up: `stack restart --with-snapshot` then
refused the snapshot and left every stacklet down. The stop hook removes
the container and its image, as the start hook does, and a removal that
fails makes `stack down ai` fail. launchd and docker are stand-ins first
on PATH, so the hook touches neither on the Mac running the suite.
"""

from pathlib import Path

import pytest

from tests.unit.stacklets.launchd_stand_in import launchd as launchd_stand_in
from tests.unit.stacklets.old_speech_docker import old_speech_docker

REPO = Path(__file__).parent.parent.parent.parent
REMOVED = ["rm -f stack-ai-speech", "image rm sha256:0ld1mage"]


def _load_on_stop():
    # Afresh for each test, as the hook runner loads it.
    from stack.stacklet_code import load
    return load(REPO / "stacklets" / "ai", REPO / "stacklets" / "ai" / "hooks" / "on_stop.py")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    # LaunchAgents and markers live in the home directory; keep the test out of it.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def launchd(tmp_path, monkeypatch):
    """A launchd first on PATH with nothing loaded, so the stop of oMLX
    and Whisper finds nothing to stop."""
    launchd_stand_in(tmp_path, monkeypatch)


def _stop(make_stack):
    from stack.hooks import StackContext
    _load_on_stop().run(StackContext(make_stack(), "ai", {}))


def test_a_stop_of_the_ai_removes_the_old_speech_container_and_its_image(
        make_stack, tmp_path, monkeypatch):
    calls = old_speech_docker(tmp_path, monkeypatch, project="stack-ai")

    _stop(make_stack)

    assert calls() == REMOVED


def test_an_instance_without_it_stops_as_before(make_stack, tmp_path, monkeypatch):
    calls = old_speech_docker(tmp_path, monkeypatch, project="someone-else")

    _stop(make_stack)

    assert calls() == []


def test_stack_down_ai_fails_when_docker_does_not_remove_it(make_stack, tmp_path, monkeypatch):
    calls = old_speech_docker(tmp_path, monkeypatch, project="stack-ai", rm_fails=True)
    stck = make_stack()
    (tmp_path / "stacklets" / "ai").symlink_to(REPO / "stacklets" / "ai")

    result = stck.down("ai")

    assert "stack-ai-speech" in result["error"]
    assert calls() == ["rm -f stack-ai-speech"]
