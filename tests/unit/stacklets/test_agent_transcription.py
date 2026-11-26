"""Where Stacky on nanobot sends a voice message, with speech on and off.

nanobot transcribes a voice message with the `openai` provider of the
agent's `config.json`. The stack renders its address as
`AGENT_WHISPER_URL`, empty with speech off. nanobot reads an empty address
as the provider's default, api.openai.com, so the entrypoint's config step
(`runtime/agent_config.py`) switches transcription off instead.

These run the config step as the entrypoint runs it, then load the config
and transcribe with the real nanobot at the image's pin. The unit lane
refuses a connection anywhere but localhost, so a request to
api.openai.com fails the test; a speech server on localhost stands in for
Whisper.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from tests.unit.stacklets.nanobot_agent import START_STEPS

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "stacklets" / "agent"


def _transcribe(tmp_path, monkeypatch, whisper_url: str) -> str:
    """What nanobot transcribes a voice message to, after the entrypoint's
    config step ran with this `AGENT_WHISPER_URL`."""
    from nanobot.audio.transcription import resolve_transcription_config, transcribe_audio_file
    from nanobot.config.loader import load_config

    config = tmp_path / "config.json"
    config.write_text((AGENT_DIR / "config.json").read_text())
    env = {"AGENT_WHISPER_URL": whisper_url, "AGENT_WHISPER_KEY": "sk-the-ai-key",
           "AGENT_WHISPER_MODEL": "whisper-1", "AGENT_LANGUAGE": "de"}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    subprocess.run([sys.executable, str(AGENT_DIR / "runtime" / "agent_config.py"), str(config)],
                   check=True, env={"PATH": "/usr/bin:/bin", **env})
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"OggS fabricated voice message")

    effective = resolve_transcription_config(load_config(config))
    return asyncio.run(transcribe_audio_file(audio, effective))


def test_with_speech_off_no_voice_message_leaves_the_house(tmp_path, monkeypatch, httpserver):
    said = _transcribe(tmp_path, monkeypatch, whisper_url="")

    assert said == ""
    assert httpserver.log == []
    written = json.loads((tmp_path / "config.json").read_text())
    assert "api_key" not in written["providers"]["openai"], "the AI's key is not handed on"


def test_with_speech_on_the_voice_message_goes_to_the_speech_server(
        tmp_path, monkeypatch, httpserver):
    httpserver.expect_request("/v1/audio/transcriptions", method="POST").respond_with_json(
        {"text": "Milch kaufen"})

    said = _transcribe(tmp_path, monkeypatch, whisper_url=httpserver.url_for("/v1"))

    assert said == "Milch kaufen"
    assert len(httpserver.log) == 1


def test_the_entrypoint_runs_each_start_step_before_nanobot_in_order():
    """The switch only keeps voice messages in the house if the container
    runs it. The agent harness runs the same steps (`START_STEPS`), so a
    step missing from the entrypoint fails here, not in a family's chat."""
    entrypoint = (AGENT_DIR / "entrypoint.sh").read_text()

    places = [entrypoint.index(f"/app/runtime/{script}") for script, *_ in START_STEPS]

    assert START_STEPS[0][0] == "agent_config.py"
    assert places == sorted(places)
    assert places[-1] < entrypoint.index("exec nanobot gateway")
