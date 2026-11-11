"""The update of an instance as beta.1 left it: `stack restart ai` changes nothing the family uses.

An update brings new code and new defaults, such as the memory profile's
speech off on a Mac under 24 GB. A fresh install stores those; an instance
installed before them has no such key, and the update must not decide for
it. So after `./stack update` and the `./stack restart ai` it asks for, the
instance runs as it ran before: speech stays on, Whisper is started and
checked, and `stack.toml` is the same file, byte for byte. What the new
profile would change arrives as advice from `stack doctor`, never as a
change.

The instance is written as beta.1 wrote it on a 16 GB Mac: the ai stacklet
set up with the managed oMLX and Whisper, an `[ai]` section without the
speech key, and no `.stack/runs/`, which the run lock brought later. Only
the address and key of oMLX differ: they are a stand-in's on localhost
(`tests/unit/stacklets/omlx_stand_in.py`), because the unit lane may not
reach the port the real oMLX listens on.

Driven through `./stack restart ai` and `./stack doctor --json` in this
process (`StackCLI`), as the admin runs them after the update. The host's
side of a start is stood in on `PATH`, as on the Mac: `brew` and
`launchctl` record what they are asked to run, `sysctl` reports 16 GB,
`omlx` is on the path as Homebrew puts it there, `curl` reaches Whisper
once it is loaded, and Docker answers that nothing runs. The home directory is the test's own, so oMLX's settings
and the Whisper LaunchAgent are never the Mac's. The health checks go
through `stack.docker.probe_health` and `check_health`, which record the
address and answer healthy: nothing listens on Whisper's port here, and
the unit lane may not reach it.

A restart takes about 3 seconds: its hooks, shell commands and stand-ins
run as processes, as they do on the Mac.
"""

from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

from .conftest import REPO, StackCLI, write_instance

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

GB = 1024 ** 3

# The English house voice, already on oMLX, so the start downloads nothing.
HOUSE_VOICE = "Kokoro-82M-bf16"

# Whisper's port, as the ai stacklet's manifest publishes it.
WHISPER_PORT = 42062

# The ai stacklet keeps its "famstack manages this" markers in the
# checkout, next to its code, and a start writes them there.
AI_STATE = REPO / "stacklets" / "ai" / ".state"


# ── The instance beta.1 left ──────────────────────────────────────────────

def beta1_ai(omlx_url: str) -> str:
    """The `[ai]` section beta.1's install wrote for the managed oMLX and
    Whisper, with the stand-in's address and key. No speech key: beta.1
    had none, so speech is on."""
    return (f'[ai]\nprovider = "managed"\nopenai_url = "{omlx_url}/v1"\n'
            f'openai_key = "{KEY}"\nwhisper_url = "http://localhost:{WHISPER_PORT}/v1"\n')


def install_whisper(data_dir: Path) -> None:
    """The Whisper beta.1 built and downloaded into the ai stacklet's data:
    the server binary and its model. Their bytes do not matter here."""
    from stacklets.ai.hooks.on_install import WHISPER_MODEL

    server = data_dir / "ai" / "whisper.cpp" / "build" / "bin" / "whisper-server"
    model = data_dir / "ai" / "whisper-models" / WHISPER_MODEL
    for path in (server, model):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def stand_in_host(bin_dir: Path, calls: Path) -> None:
    """`brew`, `launchctl`, `sysctl`, `omlx` and `curl` as on a 16 GB Mac
    with the managed oMLX and Whisper: `brew` and `launchctl` append their
    arguments to `calls`, `curl` reaches Whisper's port only."""
    bin_dir.mkdir()
    scripts = {
        "brew": f'#!/bin/sh\necho "brew $*" >> "{calls}"\n',
        "launchctl": f'#!/bin/sh\necho "launchctl $*" >> "{calls}"\n',
        "sysctl": f"#!/bin/sh\necho {16 * GB}\n",
        "omlx": "#!/bin/sh\nexit 0\n",
        # Whisper answering on its port once launchd loaded it.
        "curl": f'#!/bin/sh\ncase "$*" in *:{WHISPER_PORT}/*) exit 0 ;; esac\nexit 7\n',
    }
    for name, script in scripts.items():
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)


@pytest.fixture
def ai_state_left_as_found(tmp_path_factory):
    """The checkout's ai markers as they were before the test: the ones
    the test's start wrote are removed again, so no later `stack down ai`
    of an instance on this checkout stops a service it was never given.

    The markers are one folder for every worker of the lane, so the tests
    that write it take turns: each holds a lock on a file next to the
    lane's temporary folders while it runs, and what it removes is then
    only what it wrote."""
    lock = tmp_path_factory.getbasetemp().parent / "ai-state.lock"
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        existed = AI_STATE.exists()
        before = set(AI_STATE.iterdir()) if existed else set()
        yield
        if AI_STATE.exists():
            for path in set(AI_STATE.iterdir()) - before:
                path.unlink()
            if not existed:
                AI_STATE.rmdir()


class Beta1Instance:
    """The instance beta.1 left on a 16 GB Mac, and what its start did.

    `stack` runs `./stack` against it; `toml` is its `stack.toml` as
    beta.1 wrote it; `host_calls()` are the `brew` and `launchctl` calls;
    `health_checks` the addresses the start checked.
    """

    def __init__(self, tmp_path: Path, monkeypatch, omlx_url: str):
        data_dir = tmp_path / "data"
        self.instance = write_instance(tmp_path, extra=beta1_ai(omlx_url))
        (self.instance / ".stack" / "ai.setup-done").touch()
        install_whisper(data_dir)
        self.toml = (self.instance / "stack.toml").read_bytes()

        self._calls = tmp_path / "host-calls"
        stand_in_host(tmp_path / "bin", self._calls)
        monkeypatch.setenv("PATH", str(tmp_path / "bin"), prepend=":")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)

        self.health_checks: list[str] = []
        monkeypatch.setattr("stack.docker.probe_health", self._ready)
        monkeypatch.setattr("stack.docker.check_health", self._healthy)
        # The `stack` network every start makes sure of; Docker's answer.
        monkeypatch.setattr("stack.docker.ensure_network", lambda *_: ("mocked", None))
        self.stack = StackCLI(self.instance, monkeypatch)

    def _ready(self, url: str, headers: dict | None = None, timeout: float = 3) -> str:
        """`stack.docker.probe_health`, which a start's health checks poll."""
        self.health_checks.append(url)
        return "ready"

    def _healthy(self, url: str, headers: dict | None = None) -> bool:
        """`stack.docker.check_health`, the quick probe of `status`."""
        self.health_checks.append(url)
        return True

    def host_calls(self) -> list[str]:
        return self._calls.read_text().splitlines() if self._calls.exists() else []

    def runs(self) -> Path:
        return self.instance / ".stack" / "runs"


@pytest.fixture
def beta1(tmp_path, monkeypatch, httpserver, as_stack_runs_it, ai_state_left_as_found):
    """The beta.1 instance, with oMLX answering as it does after the update
    and the house voice already downloaded."""
    OMLX(httpserver, downloaded=[HOUSE_VOICE])
    return Beta1Instance(tmp_path, monkeypatch, httpserver.url_for("").rstrip("/"))


# ── The restart the update asks for ───────────────────────────────────────

def test_a_restart_of_the_ai_keeps_speech_on_and_stack_toml_as_it_was(beta1):
    """Given an instance as beta.1 left it on a 16 GB Mac, when the admin
    runs `stack restart ai` after the update, then it succeeds, Whisper is
    started and its health check runs, and `stack.toml` is unchanged.

    The memory profile turns speech off on a fresh install of this Mac; an
    update that did the same would take voice messages away from a family
    that uses them, without a word."""
    ran = beta1.stack("restart", "ai")

    assert ran.code == 0, ran.err
    assert (beta1.instance / "stack.toml").read_bytes() == beta1.toml
    assert any(f":{WHISPER_PORT}" in url for url in beta1.health_checks)
    whisper_loads = [c for c in beta1.host_calls()
                     if c.startswith("launchctl load") and "whisper" in c]
    assert whisper_loads


def test_the_restart_takes_the_run_lock_and_writes_the_run_log(beta1):
    """Given no `.stack/runs/` (beta.1 had no run lock), when `stack
    restart ai` runs, then the run's lock and log appear for the ai
    stacklet, and the log ends with the run's successful result."""
    import json

    assert not beta1.runs().exists()

    ran = beta1.stack("restart", "ai")

    assert ran.code == 0, ran.err
    assert (beta1.runs() / "ai.lock").exists()
    events = [json.loads(line)
              for line in (beta1.runs() / "ai.jsonl").read_text().splitlines()]
    assert events[-1]["type"] == "result"
    assert events[-1]["data"]["ok"] is True


def test_doctor_then_gives_the_profiles_advice_and_changes_nothing(beta1):
    """Given the restarted beta.1 instance, when the admin runs `stack
    doctor`, then it warns what speech costs on this Mac, and `stack.toml`
    is still the file beta.1 wrote: the advice is the admin's to take."""
    assert beta1.stack("restart", "ai").code == 0

    ran = beta1.stack("doctor", "--json")

    speech = [f for f in ran.answer["findings"] if "speech" in f["title"]]
    assert [f["level"] for f in speech] == ["warn"]
    assert (beta1.instance / "stack.toml").read_bytes() == beta1.toml
