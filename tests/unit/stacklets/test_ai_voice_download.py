"""`stack up ai` downloads the voices of the house's languages.

The voice map gives the German voices (onyx, fable, shimmer) a Piper
model the speech image never downloads by itself, and Piper answers a
voice without its model with a silent MP3. Once the speech container is
up, the ai stacklet fetches the models the map uses for `[ai] language`
and `[core] language` with the image's own download script, and nothing
in a language the house does not speak: the German model is 114 MB.

The ready step runs as `stack up ai` runs it, against the real ai
stacklet, its real voice map and a throwaway instance. A `docker` on
PATH stands in for the speech container: it records what it was asked
to run and writes the model into the voices directory the container
mounts, or fails as Piper does for a model it cannot fetch. No test
touches the network or a real container.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from stack import Stack
from stack.hooks import HookResolver, build_hook_ctx
from stack.output import CollectorOutput

REPO_ROOT = Path(__file__).resolve().parents[3]
AI = REPO_ROOT / "stacklets" / "ai"

GERMAN = "de_DE-thorsten-high"


def _instance(tmp_path, ai_language, core_language):
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nlanguage = "{core_language}"\n\n'
        f'[ai]\nlanguage = "{ai_language}"\n')
    (tmp_path / ".stack").mkdir()
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path,
                 output=CollectorOutput())


def _voices(tmp_path):
    """The speech container's /app/voices, as the compose file mounts it."""
    return tmp_path / "data" / "ai" / "speech" / "voices"


@pytest.fixture(autouse=True)
def voice_on(monkeypatch):
    monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """A `docker` that downloads every model it is asked for. Returns
    the file where it records each command line."""
    return _docker(tmp_path, monkeypatch, fetches=True)


@pytest.fixture
def offline_docker(tmp_path, monkeypatch):
    """A `docker` whose speech container cannot reach Hugging Face."""
    return _docker(tmp_path, monkeypatch, fetches=False)


def _docker(tmp_path, monkeypatch, fetches):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls"
    voices = _voices(tmp_path)
    # Called as: docker exec <container> bash <script> <model>
    outcome = (f'mkdir -p "{voices}" && touch "{voices}/$5.onnx"' if fetches else
               'echo "piper.download.VoiceNotFoundError: voices/$5.onnx" >&2; exit 1')
    script = bin_dir / "docker"
    script.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n{outcome}\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return calls


def _start(stack) -> bool:
    """The ready step of `stack up ai`, the way the CLI runs it."""
    ctx = build_hook_ctx("ai", env=stack.env("ai"),
                         step_fn=stack.output.step, stack=stack)
    return HookResolver(AI).run("on_start_ready", ctx)


def _downloads(calls: Path) -> list[str]:
    return calls.read_text().splitlines() if calls.exists() else []


class TestTheHouseLanguagesAreDownloaded:

    def test_a_german_house_gets_the_german_voice(self, tmp_path, docker):
        """The fix for German speech being silent: the model onyx, fable
        and shimmer use is fetched in the speech container, with the
        script the image downloads its own voices with."""
        assert _start(_instance(tmp_path, "de", "de"))

        assert _downloads(docker) == [
            f"exec stack-ai-speech bash download_voices_tts-1.sh {GERMAN}"]
        assert (_voices(tmp_path) / f"{GERMAN}.onnx").exists()

    def test_a_second_start_downloads_nothing(self, tmp_path, docker):
        stack = _instance(tmp_path, "de", "de")
        _start(stack)

        _start(stack)

        assert len(_downloads(docker)) == 1

    def test_an_english_house_downloads_no_german_voice(self, tmp_path, docker):
        """English needs no step: the image fetches its English voice
        itself when it starts, before the speech server answers."""
        voices = _voices(tmp_path)
        voices.mkdir(parents=True)
        (voices / "en_US-libritts_r-medium.onnx").touch()

        assert _start(_instance(tmp_path, "en", "en"))

        assert _downloads(docker) == []

    def test_the_family_language_counts_when_the_stack_speaks_english(
            self, tmp_path, docker):
        """`[ai] language = "en"` in a German household: the family may
        still ask for a German voice, so it is there."""
        assert _start(_instance(tmp_path, "en", "de"))

        assert any(call.endswith(GERMAN) for call in _downloads(docker))

    def test_a_regional_language_code_is_its_language(self, tmp_path, docker):
        assert _start(_instance(tmp_path, "de-AT", "de-AT"))

        assert any(call.endswith(GERMAN) for call in _downloads(docker))


class TestAFailedDownloadDoesNotStopTheStart:

    def test_the_admin_is_told_which_model_is_missing(
            self, tmp_path, offline_docker):
        """Chat, transcription and the other voices work without it, so
        the start goes on, and the warning says what is missing."""
        stack = _instance(tmp_path, "de", "de")

        assert _start(stack)

        [warning] = stack.output.warnings
        assert GERMAN in warning
        assert stack.output.errors == []

    def test_the_next_start_tries_again(self, tmp_path, offline_docker):
        stack = _instance(tmp_path, "de", "de")
        _start(stack)

        _start(stack)

        assert len(_downloads(offline_docker)) == 2


class TestWithoutTheSpeechContainer:

    def test_no_voice_downloads_nothing(self, tmp_path, docker, monkeypatch):
        """`stack up ai --no-voice` starts no speech container, so there
        is nothing to download into and nothing to warn about."""
        monkeypatch.setenv("STACK_AI_NO_VOICE", "1")
        stack = _instance(tmp_path, "de", "de")

        assert _start(stack)

        assert _downloads(docker) == []
        assert stack.output.warnings == []


class TestTheFirstGreetingSpeaksTheHouseLanguage:

    def test_the_first_install_fetches_the_voice_before_the_greeting(
            self, tmp_path, docker):
        """On the first `stack up ai` the greeting plays in the install
        step, before the ready step runs, so the install step fetches the
        voice first. A German Mac would otherwise greet with silence."""
        stack = _instance(tmp_path, "de", "de")
        ctx = build_hook_ctx("ai", env=stack.env("ai"),
                             step_fn=stack.output.step, stack=stack)

        HookResolver(AI).run("on_install_success", ctx)

        assert (_voices(tmp_path) / f"{GERMAN}.onnx").exists()
        _start(stack)
        assert len(_downloads(docker)) == 1
