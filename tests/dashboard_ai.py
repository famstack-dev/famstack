"""The AI as the setup page's AI step reaches it, for a Mac of 16 or 32 GB.

Step 4 of `/setup` asks the AI stacklet for its plan (`stack ai
recommended show`): this Mac's memory, its memory profile, the speech
choice and the recommended models, and the AI server `[ai]` names. Its
first words (4f) ask the AI for an introduction and for speech. This
module stands in for an oMLX on a loopback port, the admin API from
`omlx_stand_in.py` plus the chat and speech routes, and puts the instance
into one situation at a time. The dashboard preview
(`tools/dashboard_preview.py`) uses it for its screenshots and for the
setup page's controls (`tools/setup_controls.py`):

    ai = AIStandIn(host)                          # host: a DashboardHost
    ai.enter()                                    # nothing in [ai]: the two routes
    ai.enter(ai="external", memory=32)            # [ai] names the oMLX, speech on
    ai.enter(ai="managed")                        # the oMLX the stack manages
    ai.close()

Why one situation per Mac size: the screens of step 4 must agree with
each other and with what the AI stacklet recommends. A 16 GB Mac gets the
light chat model and speech off; a 32 GB Mac the larger chat model and
speech on. `speech` overrides the size's choice.

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

def introduction(memory: int) -> str:
    """The AI's introduction as the recorded clip for a Mac of `memory` GB
    speaks it: a fixed evening, so the words and the voice match.

        introduction(32)    # "Good evening, Homer. ... on your Mac with 32 GB, ..."
    """
    return ("Good evening, Homer. It's Monday, 9 November, 21:30. I'm famstack's AI, "
            f"on your Mac with {memory} GB, and I'm ready to read your family's letters.")


class AIStandIn:
    """An oMLX on a loopback port, and the instance's `[ai]` and
    `[core] profile` set for one situation of step 4.

    `host` is the `DashboardHost` whose instance the AI stacklet reads.

        ai = AIStandIn(host)
        ai.enter(ai="external", memory=32, speech=False)
    """

    def __init__(self, host):
        from pytest_httpserver import HTTPServer

        self.host = host
        self.server = HTTPServer(host="127.0.0.1", port=_free_port())
        self.server.start()
        self._toml = host.root / "stack.toml"
        self._base = self._toml.read_text()
        self.size = MAC_SIZES[16]

    def enter(self, *, ai: str = "", memory: int = 16, speech: bool | None = None) -> None:
        """Put the oMLX and the instance into one situation. `ai` is what
        `[ai] provider` says of the oMLX: "external", a server the stack
        does not manage, as `stack ai switch <address>` writes it;
        "managed", the stack's own; "" leaves `[ai]` out. `speech` None is
        the Mac size's choice."""
        from stacklets.ai import recommended
        from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

        self.size = MAC_SIZES[memory]
        speech = self.size.speech if speech is None else speech
        recommended.memory_gb = lambda: float(memory)
        self.server.clear()
        OMLX(self.server, wants_key=True, holds=True)
        self._first_words()
        url = self.server.url_for("/v1")
        self._toml.write_text(self._config(url, KEY, provider=ai, speech=speech))

    def close(self) -> None:
        """Stop the oMLX's server."""
        self.server.stop()

    # ── The instance's settings ─────────────────────────────────────────

    def _config(self, url: str, key: str, *, provider: str, speech: bool) -> str:
        """`stack.toml` for the situation: the profile always, `[ai]` with
        a provider, with the speech choice."""
        toml = self._base.replace("[core]\n", f'[core]\nprofile = "{self.size.profile}"\n', 1)
        if not provider:
            return toml
        return (toml + f'\n[ai]\nprovider = "{provider}"\nopenai_url = "{url}"\n'
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
