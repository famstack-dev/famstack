"""The oMLX the stack talks to, and whether the stack runs it.

The stack fetches two kinds of model onto the engine it runs: the
default chat model on the AI's first start (`backend.ensure_model`) and
the house voice's speech model (`voices.download_house_voice`). An oMLX
the admin runs, the oMLX app or one on another machine, answers the same
admin API, and the stack reads it there (which models it has, one test
transcription, `speech_on_omlx`), but downloads and loads nothing onto
it (route 2):

    [ai] openai_url                       oMLX?   downloads
    http://localhost:42060/v1 (managed)   yes     as the stack's own engine
    http://localhost:8000/v1  (the app)   yes     none: its admin loads models
    http://192.168.1.20:8000/v1           yes     none
    https://api.example.com/v1            no      none

    server = model_server("external", "http://localhost:8000/v1", "sk-omlx-key")
    server.managed                       # False: the oMLX app on this Mac
    server.client().list_downloaded()    # what it has

What it does not do: change a server's settings. The stack changes them
only on the oMLX it installed (`omlx_fit`).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from .omlx import OMLXClient, is_omlx

# The probe module, by its import path: `stack.ai` binds its name `probe`
# to the probe function once it has loaded it, so `from stack.ai import
# probe` gave the function or the module depending on what was imported
# first.
_probe = importlib.import_module("stack.ai.probe")

__all__ = ["ModelServer", "model_server"]


@dataclass(frozen=True)
class ModelServer:
    """An oMLX the stack talks to through its admin API.

    `managed` is the engine the stack installed; `local` says the server
    runs on this Mac.

        server = ModelServer("http://192.168.1.20:8000", "sk-key", managed=False, local=False)
        server.client().login()    # True with the right key
    """

    admin_url: str
    key: str
    managed: bool
    local: bool

    def client(self) -> OMLXClient:
        """A client for the server's admin API, not yet logged in."""
        return OMLXClient(self.admin_url, self.key)


def model_server(provider: str, openai_url: str, key: str) -> ModelServer | None:
    """The oMLX behind `openai_url`, or None when the server is not one.

    The engine the stack installed (`provider = "managed"`) is an oMLX
    without asking it. A server the stack does not manage counts when it
    answers oMLX's admin API: one probe of `/admin/api/models`.

        model_server("external", "https://api.example.com/v1", "sk-1")   # None
    """
    admin_url = openai_url.rstrip("/").removesuffix("/v1")
    managed = provider == "managed"
    if not managed and not is_omlx(admin_url):
        return None
    return ModelServer(admin_url, key, managed=managed, local=_probe.on_this_mac(admin_url))
