"""Preparing famstack's oMLX: the long part of the AI's first start, outside the queue.

`stack ai prepare` (and `stack up ai`, before its turn) runs the ai
stacklet's `on_prepare` hook: Homebrew's oMLX, famstack's oMLX running in
its own folder, the language model `[ai] default`, the house voice, and
with speech on Whisper and its model. Each step that is done is skipped,
so a preparation that stopped goes on where it stopped, and `stack up ai`
afterwards only starts.

The hook runs as the framework runs it (`Stack.prepare`, which both
commands call), on a throwaway instance. Stand-ins first on PATH: `brew`
(oMLX installed once `brew install` ran), `launchctl` (a launchd that
remembers its jobs), `pgrep` (the oMLX app runs only when the test says
so). oMLX's admin API is a real HTTP server answering as oMLX does
(`omlx_stand_in.py`), at `[ai] openai_url`. The output is the one a
screen reads: the run's events.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
import tomllib
from pathlib import Path

import pytest

from stack import Stack
from stack.output import CollectorOutput, EventOutput

from stacklets.ai import model_download, omlx_app, prepare
from stacklets.ai.omlx import OMLXClient

from tests.unit.stacklets.launchd_stand_in import launchd
from tests.unit.stacklets.omlx_stand_in import KEY, OMLX, SIZE

REPO_ROOT = Path(__file__).resolve().parents[3]

LANGUAGE_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"
HOUSE_VOICE = "mlx-community/Kokoro-82M-bf16"

# Homebrew: `list --versions omlx` names a release once `install` ran;
# the build tools of Whisper are always there.
BREW = """#!/bin/sh
echo "brew $*" >> "$CALLS"
case "$1 $2" in
  "list --versions") [ -f "$BREW_STATE/omlx" ] && echo "omlx 0.8.0" && exit 0; exit 1 ;;
esac
case "$1" in
  install) touch "$BREW_STATE/omlx" ;;
esac
exit 0
"""

# A short stand-in for Whisper's model file.
WHISPER_BYTES = b"ggml" + bytes(range(256)) * 40


class Mac:
    """A Mac with a throwaway instance whose `[ai]` names famstack's oMLX."""

    def __init__(self, tmp_path: Path, monkeypatch, httpserver, omlx: OMLX):
        self.tmp_path = tmp_path
        self.omlx = omlx
        self.httpserver = httpserver
        bin_dir, _jobs = launchd(tmp_path, monkeypatch)
        (bin_dir / "brew").write_text(BREW)
        (bin_dir / "brew").chmod(0o755)
        (tmp_path / "brew-state").mkdir()
        monkeypatch.setenv("BREW_STATE", str(tmp_path / "brew-state"))
        home = tmp_path / "home"
        (home / ".omlx").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        self.data = tmp_path / "data"
        self.write_ai(speech="false")
        # famstack's oMLX was chosen: its settings file holds the key the
        # stand-in takes, and `[ai]` a copy (`omlx.own_key`).
        settings = self.data / "ai" / "omlx" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"auth": {"api_key": KEY}}))
        (tmp_path / ".stack").mkdir()
        self.events_stream = io.StringIO()
        self.stack = Stack(REPO_ROOT, self.data, instance_dir=tmp_path,
                           output=EventOutput(CollectorOutput(), self.events_stream))

    def write_ai(self, provider: str = "managed", speech: str = "false") -> None:
        (self.tmp_path / "stack.toml").write_text(
            f'[core]\ndata_dir = "{self.data}"\nlanguage = "en"\n\n'
            f'[ai]\nprovider = "{provider}"\n'
            f'openai_url = "{self.httpserver.url_for("/v1")}"\n'
            f'openai_key = "{KEY}"\ndefault = "{LANGUAGE_MODEL}"\nlanguage = "en"\n'
            f'speech = {speech}\n')

    def prepares(self) -> dict:
        """The preparation, as `stack ai prepare` runs it."""
        return self.stack.prepare("ai")

    def calls(self) -> list[str]:
        calls = self.tmp_path / "calls"
        return calls.read_text().splitlines() if calls.exists() else []

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.events_stream.getvalue().splitlines()]

    def ai(self) -> dict:
        return tomllib.loads((self.tmp_path / "stack.toml").read_text())["ai"]

    def errors(self) -> str:
        return " ".join(e["data"]["text"] for e in self.events() if e["type"] == "step.failed")


@pytest.fixture
def mac(tmp_path, monkeypatch, httpserver):
    return Mac(tmp_path, monkeypatch, httpserver, OMLX(httpserver))


def brew_installs(mac: Mac) -> list[str]:
    return [c for c in mac.calls() if c.startswith("brew install")]


# ── A first preparation, and a second ────────────────────────────────────

def test_a_first_preparation_installs_omlx_and_downloads_the_language_model(mac):
    """Given `[ai]` names famstack's oMLX on a Mac without it, when the
    preparation runs, then Homebrew installs oMLX, famstack's oMLX runs
    under its LaunchAgent, and the language model is downloaded."""
    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert any("omlx" in c for c in brew_installs(mac))
    assert any(c.startswith("launchctl load") for c in mac.calls())
    assert mac.omlx.requested == [LANGUAGE_MODEL]


def test_a_second_preparation_skips_what_is_done(mac):
    """Given the preparation ran to its end, when it runs again, then it
    installs nothing and downloads nothing: `stack up ai` after it costs
    a few checks."""
    mac.prepares()
    installs, requested = brew_installs(mac), list(mac.omlx.requested)

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert brew_installs(mac) == installs
    assert mac.omlx.requested == requested


def test_a_download_still_running_in_omlx_is_followed_with_its_percent(
        tmp_path, monkeypatch, httpserver):
    """Given the preparation stopped while oMLX downloaded the model (the
    download runs on in oMLX), when it runs again, then it follows that
    download instead of starting a second one, and reports its percent as
    the progress of one running step, which a job on the setup page shows."""
    omlx = OMLX(httpserver, holds=True)
    mac = Mac(tmp_path, monkeypatch, httpserver, omlx)
    client = OMLXClient(httpserver.url_for("/"), KEY)
    assert client.login() and client.start_download(LANGUAGE_MODEL)
    monkeypatch.setattr(model_download, "POLL_SECONDS", 0.05)
    finishing = threading.Thread(target=finish_once_followed, args=(mac,))
    finishing.start()

    result = mac.prepares()
    finishing.join()

    assert result.get("ok"), mac.errors()
    assert omlx.requested == [LANGUAGE_MODEL]
    events = mac.events()
    progress = [e for e in events if e["type"] == "step.progress"]
    assert progress and progress[0]["data"]["percent"] == 50.0
    started = next(e for e in events if e["id"] == progress[0]["data"]["started"])
    assert started["type"] == "step.started"
    assert any(e["type"] == "step.done" and e["data"].get("started") == started["id"]
               for e in events)


def finish_once_followed(mac: Mac) -> None:
    """Let oMLX finish the held download once the preparation reported
    its percent, or after 10 s, so the test ends either way."""
    deadline = time.monotonic() + 10
    while "step.progress" not in mac.events_stream.getvalue() and time.monotonic() < deadline:
        time.sleep(0.02)
    mac.omlx.finish()


# ── What refuses ─────────────────────────────────────────────────────────

def test_the_preparation_refuses_while_the_omlx_app_runs(mac, monkeypatch):
    """Two MLX servers on one graphics chip can crash the Mac: with the
    oMLX app open the preparation stops before anything is installed."""
    monkeypatch.setattr(omlx_app, "PROCESS_COMMAND", ("true",))

    result = mac.prepares()

    assert result.get("cancelled") == omlx_app.QUIT_FIRST
    assert brew_installs(mac) == []
    assert mac.omlx.requested == []


def test_a_disk_too_small_for_the_model_stops_before_the_download(mac, monkeypatch):
    """Given less free space than the model needs, then the preparation
    stops before the download starts and names the size it needs."""
    monkeypatch.setattr(model_download, "free_space", lambda _path: 1024 ** 3)

    result = mac.prepares()

    assert "error" in result
    assert mac.omlx.requested == []
    assert SIZE["size_formatted"] in mac.errors()


def test_too_little_disk_tells_a_screen_how_much_to_free(mac, monkeypatch):
    """Given less free space than the model needs, then the run's result
    carries the key a screen says it with (`ai.no_room`) and the gigabytes
    to free: freeing them is enough, a tenth less is not."""
    gb = 1024 ** 3
    monkeypatch.setattr(model_download, "free_space", lambda _path: gb)

    result = mac.prepares()

    assert result.get("key") == "ai.no_room", result
    to_free = result["values"]["gb"]
    needs = SIZE["size"] + model_download.SPACE_MARGIN_BYTES
    assert gb + to_free * gb >= needs > gb + (to_free - 0.1) * gb


def test_the_downloads_step_carries_its_key(mac):
    """The language model's download is a step a screen can say in its own
    language: its events carry the step's key."""
    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert any(e["type"] == "step.started" and e["data"].get("key") == "ai.download.language_model"
               for e in mac.events())


def test_a_resumed_download_needs_room_only_for_what_is_missing(mac, monkeypatch):
    """Given half the model is on disk from a download that stopped, and
    the disk has room for the other half, then the download goes on: the
    space check counts only the bytes still to come."""
    from stacklets.ai.omlx_service import models_folder
    half = SIZE["size"] // 2
    folder = models_folder() / LANGUAGE_MODEL
    folder.mkdir(parents=True)
    with open(folder / "model-00001-of-00002.safetensors", "wb") as shard:
        shard.truncate(half)                                  # sparse
    room = SIZE["size"] - half + model_download.SPACE_MARGIN_BYTES + 1024 ** 2
    monkeypatch.setattr(model_download, "free_space", lambda _path: room)

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert mac.omlx.requested == [LANGUAGE_MODEL]


def test_a_mac_that_swaps_already_hears_it_before_the_download(mac, monkeypatch):
    """Given macOS already swaps 2 GB, then the preparation says so as a
    warning before the model's download, which a screen shows too: a
    large model on such a Mac answers slowly."""
    monkeypatch.setattr(model_download, "SWAP_COMMAND", (
        "echo", "vm.swapusage: total = 4096.00M  used = 2048.00M  free = 2048.00M"))

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert [e for e in mac.events() if e["type"] == "warning"
            and "2.0 GB" in e["data"]["text"]]


# ── Resting ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider", ["", "external", "none"])
def test_without_famstacks_omlx_the_preparation_does_nothing(mac, provider):
    """Given `[ai]` names no route, your own server, or no AI, then the
    preparation installs, starts and downloads nothing, and says why."""
    mac.write_ai(provider=provider)

    result = mac.prepares()

    assert result.get("ok")
    assert mac.calls() == []
    assert mac.omlx.requested == []
    assert any(e["type"] == "step.done" for e in mac.events())


# ── With speech on ───────────────────────────────────────────────────────

@pytest.fixture
def whisper_model(mac, monkeypatch, httpserver):
    """Whisper's model on a server that answers a range request as Hugging
    Face does, and a Whisper that is built already. Returns the model's path."""
    from werkzeug import Response

    def serve(request):
        first = 0
        if ranged := request.headers.get("Range"):
            first = int(ranged.removeprefix("bytes=").rstrip("-"))
        if first >= len(WHISPER_BYTES):
            return Response(b"", status=416,
                            headers={"Content-Range": f"bytes */{len(WHISPER_BYTES)}"})
        body = WHISPER_BYTES[first:]
        headers = {"Content-Length": str(len(body))}
        if first:
            headers["Content-Range"] = f"bytes {first}-{len(WHISPER_BYTES) - 1}/{len(WHISPER_BYTES)}"
        return Response(body, status=206 if first else 200, headers=headers)

    httpserver.expect_request("/whisper-model").respond_with_handler(serve)
    monkeypatch.setattr(prepare, "WHISPER_MODEL_URL", httpserver.url_for("/whisper-model"))
    server = mac.data / "ai" / "whisper.cpp" / "build" / "bin" / "whisper-server"
    server.parent.mkdir(parents=True)
    server.write_text("")
    mac.write_ai(speech="true")
    return mac.data / "ai" / "whisper-models" / prepare.WHISPER_MODEL


def requested_ranges(httpserver) -> list:
    """The Range header of each request for Whisper's model, None for none."""
    return [r.headers.get("Range") for r, _ in httpserver.log if r.path == "/whisper-model"]


def test_with_speech_on_it_downloads_the_house_voice_and_whispers_model(mac, whisper_model):
    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert HOUSE_VOICE in mac.omlx.requested
    assert whisper_model.read_bytes() == WHISPER_BYTES
    assert mac.ai()["whisper_url"] == "http://localhost:42062/v1"


def test_a_whisper_model_cut_short_is_downloaded_on_from_where_it_stopped(
        mac, whisper_model, httpserver):
    """Given a download of Whisper's model stopped halfway, when the
    preparation runs again, then it asks only for the missing part."""
    partial = whisper_model.with_name(whisper_model.name + ".partial")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(WHISPER_BYTES[:1000])

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert whisper_model.read_bytes() == WHISPER_BYTES
    assert not partial.exists()
    ranges = [r.headers.get("Range") for r, _ in httpserver.log if r.path == "/whisper-model"]
    assert ranges == ["bytes=1000-"]


def test_the_model_is_complete_at_the_size_the_server_states(mac, whisper_model):
    """Given the server states a size for the model that no release knows
    (a new upload), then the download completes at that size: the
    preparation takes the size from the server's answer, never from a
    number in the code."""
    assert len(WHISPER_BYTES) != 1_624_555_275

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert whisper_model.read_bytes() == WHISPER_BYTES


def test_a_partial_larger_than_the_model_starts_over_once(mac, whisper_model, httpserver):
    """Given a `.partial` larger than the size the server states (the model
    was replaced by a smaller one upstream), then the download starts over
    from the first byte, once, and completes."""
    partial = whisper_model.with_name(whisper_model.name + ".partial")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(WHISPER_BYTES + b"x" * 100)

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert whisper_model.read_bytes() == WHISPER_BYTES
    assert requested_ranges(httpserver) == [f"bytes={len(WHISPER_BYTES) + 100}-", None]


def sparse(path: Path, size: int) -> None:
    """A file of `size` bytes that takes no disk space."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as file:
        file.truncate(size)


def test_a_model_under_its_own_name_is_not_downloaded_again(mac, whisper_model, httpserver):
    """Given the model under its own name, then nothing is downloaded: the
    file takes that name only once it is complete."""
    sparse(whisper_model, 1_600_000_000)

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert requested_ranges(httpserver) == []


def test_a_model_an_earlier_release_cut_short_is_downloaded_again(
        mac, whisper_model, httpserver):
    """Given an update finds a 500 MB model under its own name (an earlier
    release downloaded straight into it and was cut off), then it is
    deleted and downloaded again: Whisper would abort on a half model."""
    sparse(whisper_model, 500_000_000)

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert whisper_model.read_bytes() == WHISPER_BYTES
    assert requested_ranges(httpserver) == [None]


def test_a_connection_cut_off_fails_the_step_and_the_next_run_goes_on(
        mac, whisper_model, monkeypatch, httpserver):
    """Given the server promises the whole model and closes the connection
    halfway, then the preparation fails and leaves no model under its own
    name; the next run asks for the rest and completes it."""
    half = len(WHISPER_BYTES) // 2
    cut = socket.socket()
    cut.bind(("127.0.0.1", 0))
    cut.listen(1)

    def serve_half():
        connection, _ = cut.accept()
        connection.recv(4096)
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(WHISPER_BYTES)
                           + WHISPER_BYTES[:half])
        connection.close()

    server = threading.Thread(target=serve_half)
    server.start()
    with monkeypatch.context() as cutting:
        cutting.setattr(prepare, "WHISPER_MODEL_URL", f"http://127.0.0.1:{cut.getsockname()[1]}/m")
        failed = mac.prepares()
    server.join()
    cut.close()

    assert "error" in failed
    assert not whisper_model.exists()

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert whisper_model.read_bytes() == WHISPER_BYTES
    ranges = [r.headers.get("Range") for r, _ in httpserver.log if r.path == "/whisper-model"]
    assert ranges == [f"bytes={half}-"]


def test_your_own_server_without_speech_gets_whisper_prepared(mac, whisper_model):
    """Given `[ai]` names your own server, which does not transcribe, and
    speech is on, then the preparation builds Whisper and downloads its
    model, so no first `stack up ai` builds it inside the queue. Your
    server gets nothing: famstack installs and downloads nothing for it."""
    mac.write_ai(provider="external", speech="true")

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert whisper_model.read_bytes() == WHISPER_BYTES
    assert mac.ai()["whisper_url"] == "http://localhost:42062/v1"
    assert brew_installs(mac) == []
    assert mac.omlx.requested == []


def test_your_own_speech_server_gets_no_whisper(mac, whisper_model, httpserver):
    """Given `[ai] whisper_url` names a speech server of your own, then the
    preparation builds and downloads nothing for speech."""
    mac.write_ai(provider="external", speech="true")
    toml = mac.tmp_path / "stack.toml"
    toml.write_text(toml.read_text() + 'whisper_url = "http://192.168.1.20:42062/v1"\n')

    result = mac.prepares()

    assert result.get("ok"), mac.errors()
    assert not whisper_model.exists()
    assert [r for r, _ in httpserver.log if r.path == "/whisper-model"] == []


# ── `stack up ai` afterwards ─────────────────────────────────────────────

def test_the_start_after_the_preparation_installs_and_downloads_nothing(mac):
    """Given the preparation is done, when `stack up ai` runs, then it
    only starts: no Homebrew install and no download inside the run."""
    bin_dir = mac.tmp_path / "bin"
    (bin_dir / "docker").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "docker").chmod(0o755)
    (bin_dir / "curl").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "curl").chmod(0o755)
    mac.prepares()
    installs, requested = brew_installs(mac), list(mac.omlx.requested)

    result = mac.stack.up("ai")

    assert "error" not in result, mac.errors()
    assert brew_installs(mac) == installs
    assert mac.omlx.requested == requested
    assert os.path.exists(mac.data / "ai" / "omlx" / "settings.json")
