"""ai on_start hook: the speech container the stacklet used to run goes.

The stack's voices speak through oMLX, so the ai stacklet has no container
of its own. An instance that ran the old one, `stack-ai-speech` (Piper),
still has it after the update: `stack up ai` removes it and its image,
once, and only when Docker says it belongs to the ai stacklet's compose
project. With a configured provider and an engine that answers, the hook
does no other docker or network work, so this runs fast and offline.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))


_STATE_DIR = None


def _load_on_start():
    # Afresh for each test, as the hook runner loads it: `STATE_DIR` is set below.
    from stack.stacklet_code import load
    mod = load(REPO / "stacklets" / "ai", REPO / "stacklets" / "ai" / "hooks" / "on_start.py")
    # Its state markers live in the checkout; keep the test out of it.
    mod.STATE_DIR = _STATE_DIR
    return mod


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    global _STATE_DIR
    _STATE_DIR = tmp_path / "state"
    # oMLX's settings live in the home directory; keep the test out of it.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    yield
    _STATE_DIR = None


def _ctx(make_stack, env, engine_url):
    from stack.hooks import StackContext

    stck = make_stack()
    # Managed with an engine that answers, so on_start neither bails
    # early, nor asks about leaving a remote endpoint, nor runs
    # `brew services start omlx` for real.
    stck._set_cfg("ai", "provider", "managed")
    stck._set_cfg("ai", "openai_url", engine_url)
    return StackContext(stck, "ai", env)


@pytest.fixture
def engine_url(httpserver):
    httpserver.expect_request("/v1/models").respond_with_json({"data": []})
    return httpserver.url_for("/v1")


@pytest.fixture(autouse=True)
def _engine_installed(monkeypatch):
    """Pretend oMLX is on the machine, because that is not what these cover.

    on_start refuses to start a managed provider whose oMLX binary has gone
    missing; that behaviour has its own tests. Without this stub these would
    pass or fail on whether the machine running the suite happens to have
    oMLX installed -- green on a developer Mac, red on CI, for reasons that
    have nothing to do with compose profiles.
    """
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/local/bin/omlx")


# `docker` with one container, `stack-ai-speech`, in the compose project
# `$PROJECT`. It answers `ps` only for a filter on that project, as the
# real client does, and writes every other command it runs to `$CALLS`.
DOCKER = """#!/bin/sh
[ "$1" = "--context" ] && shift 2
case "$1" in
  ps)      [ "$4" = "label=com.docker.compose.project=$PROJECT" ] &&
             printf 'stack-ai-speech\\t%s\\trunning\\tUp 2 hours\\n' "$PROJECT" ;;
  inspect) echo "sha256:0ld1mage" ;;
  rm|image) echo "$*" >> "$CALLS" ;;
  *)       echo "unexpected: docker $*" >&2; exit 125 ;;
esac
"""


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """The stand-in first on PATH; the commands it was asked to run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(DOCKER)
    (bin_dir / "docker").chmod(0o755)
    calls = tmp_path / "calls"
    monkeypatch.setenv("PATH", str(bin_dir), prepend=":")
    monkeypatch.setenv("CALLS", str(calls))
    return lambda: calls.read_text().splitlines() if calls.exists() else []


def test_an_updated_instance_loses_the_old_speech_container_and_its_image(
        make_stack, engine_url, docker, monkeypatch):
    monkeypatch.setenv("PROJECT", "stack-ai")

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert docker() == ["rm -f stack-ai-speech", "image rm sha256:0ld1mage"]


def test_a_container_of_that_name_in_another_project_stays(make_stack, engine_url, docker,
                                                           monkeypatch):
    monkeypatch.setenv("PROJECT", "someone-else")

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert docker() == []


# ── oMLX's memory guard on a small Mac ───────────────────────────────────
#
# oMLX refuses or aborts what does not fit under its memory guard's ceiling.
# Its default tier, "balanced", computes the ceiling from the memory free at
# that moment: on a 16 GB Mac with Docker's VM and Whisper resident, that was
# under the 5.8 GB of the default model. A fixed 9 GB ceiling and chunked
# prefill let a contract-sized request through. `sysctl` and `brew` are
# stand-ins that report the Mac's memory and log what they are asked to run.

GB = 1024 ** 3


@pytest.fixture
def small_mac(tmp_path, monkeypatch, docker):
    """A Mac with `ram` GB whose oMLX settings hold `tier`; returns a
    function that sets both and gives back the settings and brew's calls."""
    import json

    bin_dir = tmp_path / "bin"
    brew_calls = tmp_path / "brew-calls"
    (bin_dir / "brew").write_text(f'#!/bin/sh\necho "$*" >> "{brew_calls}"\n')
    (bin_dir / "brew").chmod(0o755)
    settings = tmp_path / "home" / ".omlx" / "settings.json"
    settings.parent.mkdir(parents=True)
    monkeypatch.setenv("PROJECT", "stack-ai")

    def mac(ram_gb: int, tier: str | None, chunked: bool = False):
        (bin_dir / "sysctl").write_text(f"#!/bin/sh\necho {ram_gb * GB}\n")
        (bin_dir / "sysctl").chmod(0o755)
        memory = {"memory_guard_tier": tier} if tier else {}
        settings.write_text(json.dumps({"server": {"host": "127.0.0.1"}, "memory": memory,
                                        "scheduler": {"chunked_prefill": chunked}}))

    def state():
        written = json.loads(settings.read_text())
        calls = brew_calls.read_text().splitlines() if brew_calls.exists() else []
        memory = written.get("memory", {})
        return (memory.get("memory_guard_tier"), memory.get("memory_guard_custom_ceiling_gb"),
                written.get("scheduler", {}).get("chunked_prefill"), calls)

    mac.state = state
    return mac


@pytest.mark.parametrize("tier", ["balanced", None])
def test_a_16_gb_mac_gets_a_fixed_9_gb_ceiling_chunked_prefill_and_one_restart(
        make_stack, engine_url, small_mac, tier):
    small_mac(16, tier)

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.state() == ("custom", 9.0, True, ["services restart omlx"])


def test_the_second_start_changes_nothing(make_stack, engine_url, small_mac):
    small_mac(16, "balanced")
    _load_on_start().run(_ctx(make_stack, {}, engine_url))
    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.state() == ("custom", 9.0, True, ["services restart omlx"])


def test_a_tier_the_admin_chose_stays_and_the_prefill_is_still_chunked(make_stack, engine_url,
                                                                       small_mac):
    small_mac(16, "aggressive")

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.state() == ("aggressive", None, True, ["services restart omlx"])


def test_a_larger_mac_keeps_omlx_as_it_is(make_stack, engine_url, small_mac):
    small_mac(32, "balanced")

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.state() == ("balanced", None, False, [])
