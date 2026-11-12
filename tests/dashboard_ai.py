"""The AI as the setup page's AI step reaches it, for a Mac of 16 or 32 GB.

Step 5 of `/setup` asks the AI stacklet for its plan (`stack ai
recommended show`): whether the oMLX app runs on this Mac, whether the
stack is connected to it, this Mac's memory, the speech choice, and a row
per model with its size. Its first words (5f) ask the same app for an
introduction and for speech. This module stands in for the app on a
loopback port, the admin API from `omlx_stand_in.py` plus the chat and
speech routes, and puts the instance into one situation at a time. The
dashboard preview (`tools/dashboard_preview.py`) uses it for its
screenshots and for the setup page's controls (`tools/setup_controls.py`):

    ai = AIStandIn(host)                          # host: a DashboardHost
    ai.enter(app=False)                           # 5a: "Get the oMLX app"
    ai.enter(connected=True, memory=32)           # 5c: three rows, speech on
    ai.enter(connected=True, downloading=True)    # 5d: a bar for the chat model only
    ai.close()

Why one situation per Mac size: the screens of step 5 must agree with
each other and with what the AI stacklet does. A 16 GB Mac gets the light
chat model and speech off, so it downloads the chat model alone
(`stacklets/ai/recommended.py` downloads the speech models only with
speech on). A 32 GB Mac gets the larger chat model and speech on, so it
downloads three. Each model has its own size, as the oMLX app reports
them; the larger chat model's size is an estimate. `speech` overrides the
size's choice, as the step's switch does.

The first words are famstack's own voice, recorded: the speech route
answers the welcome with `preview_voice/voice-hello.mp3` and the
introduction with the clip for the Mac's memory, and the chat route
answers the introduction those clips speak. The clips are English, so a
German page plays English words.

This Mac's memory is stood in on the AI stacklet's module
(`stacklets.ai.recommended.memory_gb`), because the plan reads it from
`sysctl` and the preview must not show the memory of the Mac it runs on.
Nothing here reaches the network or runs Docker.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path

from werkzeug import Response

__all__ = ["AIStandIn", "MAC_SIZES", "MacSize", "introduction"]

VOICE = Path(__file__).parent / "preview_voice"
GB = 1024 ** 3


@dataclass(frozen=True)
class MacSize:
    """What a Mac of one memory size gets: its memory profile
    (`stack.profile`), whether speech is on, and the clip of its
    introduction.

        MAC_SIZES[16].speech    # False
    """
    memory: int
    profile: str
    speech: bool

    @property
    def intro_clip(self) -> Path:
        """The recorded introduction that names this memory: `voice-intro-32.mp3`."""
        return VOICE / f"voice-intro-{self.memory}.mp3"


MAC_SIZES = {16: MacSize(16, "small", speech=False), 32: MacSize(32, "large", speech=True)}

# Each model's size as the oMLX app reports it, by its repo. The light chat
# model and the speech model are measured; the larger chat model (20.4 GB)
# and the English house voice (0.3 GB) are the setup mock's estimates.
SIZES = {
    "mlx-community/Qwen3.5-9B-MLX-4bit": 6_227_702_579,
    "unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit": round(20.4 * GB),
    "mlx-community/whisper-large-v3-turbo-asr-fp16": 1_618_636_172,
    "mlx-community/Kokoro-82M-bf16": round(0.3 * GB),
}


def introduction(memory: int) -> str:
    """The AI's introduction as the recorded clip for a Mac of `memory` GB
    speaks it: a fixed evening, so the words and the voice match.

        introduction(32)    # "Good evening, Homer. ... on your Mac with 32 GB, ..."
    """
    return ("Good evening, Homer. It's Monday, 9 November, 21:30. I'm famstack's AI, "
            f"on your Mac with {memory} GB, and I'm ready to read your family's letters.")


class AIStandIn:
    """The oMLX app on a loopback port, and the instance's `[ai]` and
    `[core] profile` set for one situation of step 5.

    `host` is the `DashboardHost` whose instance the AI stacklet reads;
    a download is started through it, as the page starts one.

        ai = AIStandIn(host)
        ai.enter(connected=True, memory=32, speech=False)
    """

    def __init__(self, host):
        from pytest_httpserver import HTTPServer

        self.host = host
        self.server = HTTPServer(host="127.0.0.1", port=_free_port())
        self.server.start()
        self._toml = host.root / "stack.toml"
        self._base = self._toml.read_text()
        self.size = MAC_SIZES[16]

    def enter(self, *, app: bool = True, connected: bool = False, downloading: bool = False,
              memory: int = 16, speech: bool | None = None, installed: bool | None = None,
              on_mac: bool = False) -> None:
        """Put the app and the instance into one situation. `app` says
        whether the oMLX app runs on this Mac (it does whenever the stack is
        `connected` to it); `installed` whether it is in Applications (None:
        whenever it runs); `downloading` starts the recommended download,
        which stays at half; `speech` None is the Mac size's choice;
        `on_mac` puts every model on the app already, so 5c has nothing to
        download and offers Continue."""
        import shutil

        from stacklets.ai import homebrew_omlx, omlx_app, recommended, running_server
        from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

        self.size = MAC_SIZES[memory]
        speech = self.size.speech if speech is None else speech
        recommended.memory_gb = lambda: float(memory)
        self.server.clear()
        OMLX(self.server, wants_key=True, holds=True, sizes=SIZES,
             downloaded=[repo.split("/")[-1] for repo in SIZES] if on_mac else ())
        self._first_words()
        url = self.server.url_for("/v1")
        running_server.RUNNING_SERVERS = ((url,) if app or connected
                                          else (f"http://127.0.0.1:{_free_port()}/v1",))
        # The stack engine's address, where the rig's own AI may listen: closed.
        running_server.STACK_ENGINE_ADDRESSES = (f"http://127.0.0.1:{_free_port()}/v1",)
        # Homebrew has no oMLX in the made-up Mac, whatever this Mac has.
        homebrew_omlx.LIST_COMMAND = ("false",)
        # The app's bundle in the made-up Mac's own Applications folder.
        omlx_app.APP_BUNDLE = self._toml.parent / "Applications" / "oMLX.app"
        shutil.rmtree(omlx_app.APP_BUNDLE, ignore_errors=True)
        if (app or connected) if installed is None else installed:
            omlx_app.APP_BUNDLE.mkdir(parents=True)
        self._toml.write_text(self._config(url, KEY, connected=connected, speech=speech))
        if connected and downloading:
            self.host.answer({"cmd": "ai_download", "speech": speech})

    def close(self) -> None:
        """Stop the app's server."""
        self.server.stop()

    # ── The instance's settings ─────────────────────────────────────────

    def _config(self, url: str, key: str, *, connected: bool, speech: bool) -> str:
        """`stack.toml` for the situation: the profile always, `[ai]` once
        connected, with the speech choice the step's switch stores."""
        toml = self._base.replace("[core]\n", f'[core]\nprofile = "{self.size.profile}"\n', 1)
        if not connected:
            return toml
        return (toml + f'\n[ai]\nprovider = "external"\nopenai_url = "{url}"\n'
                f'openai_key = "{key}"\nspeech = {"true" if speech else "false"}\n')

    # ── The first words ─────────────────────────────────────────────────

    def _first_words(self) -> None:
        """The chat route answers the introduction, the speech route the
        recorded clip of what it is asked to say."""
        self.server.expect_request("/v1/chat/completions", method="POST").respond_with_json(
            {"choices": [{"message": {"content": introduction(self.size.memory)}}]})
        self.server.expect_request("/v1/audio/speech", method="POST").respond_with_handler(
            self._speak)

    def _speak(self, request):
        said = json.loads(request.data).get("input", "")
        clip = (self.size.intro_clip if said == introduction(self.size.memory)
                else VOICE / "voice-hello.mp3")
        return Response(clip.read_bytes(), content_type="audio/mpeg")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
