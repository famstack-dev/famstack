"""The oMLX the stack downloads models onto, and whether it asks first.

The stack fetches two kinds of model onto an oMLX: the default chat model
on the AI's first start (`backend.ensure_model`) and the house voice's
speech model (`voices.download_house_voice`). Any oMLX answers the same
admin API, the one the stack installs and the oMLX app alike, so both go
to whichever oMLX `[ai] openai_url` names. Where it runs decides how:

    [ai] openai_url                       oMLX?   downloads
    http://localhost:42060/v1 (managed)   yes     as the stack's own engine
    http://localhost:8000/v1  (the app)   yes     as the stack's own engine
    http://192.168.1.20:8000/v1           yes     only after a yes, default no
    https://api.example.com/v1            no      none: its admin loads models

A server on another machine belongs to someone else's disk and memory:
"This downloads 5.8 GB onto the AI server at 192.168.1.20. Download it
there?" A no, or no terminal to answer, leaves it as it is.

    server = model_server("external", "http://localhost:8000/v1", "sk-omlx-key")
    server.asks_first                    # False: the oMLX app on this Mac
    server.download_question("5.8 GB")   # 'Download from HuggingFace?'

What it does not do: change a server's settings. The stack changes them
only on an oMLX on this Mac (`local`): the one it installed, and the
oMLX app, whose memory guard, prefill and idle unload a small Mac's start
fits to its memory (`omlx_fit`). A server on another machine keeps its
settings.
"""

from __future__ import annotations

import importlib
import urllib.parse
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
    """An oMLX the stack may download models onto.

    `managed` is the engine the stack installed; `local` says the server
    runs on this Mac. A server elsewhere is asked about first, with no as
    the default.

        server = ModelServer("http://192.168.1.20:8000", "sk-key", managed=False, local=False)
        server.download_question("5.8 GB")
        # 'This downloads 5.8 GB onto the AI server at 192.168.1.20. Download it there?'
    """

    admin_url: str
    key: str
    managed: bool
    local: bool

    @property
    def asks_first(self) -> bool:
        """Whether a download needs the admin's yes, default no: a server
        on another machine."""
        return not self.local

    @property
    def host(self) -> str:
        """The server's host as the admin wrote it: `192.168.1.20`."""
        return urllib.parse.urlsplit(self.admin_url).hostname or self.admin_url

    def client(self) -> OMLXClient:
        """A client for the server's admin API, not yet logged in."""
        return OMLXClient(self.admin_url, self.key)

    def download_question(self, size: str) -> str:
        """The yes/no question before a download of `size` ("5.8 GB")."""
        if self.asks_first:
            return f"This downloads {size} onto the AI server at {self.host}. Download it there?"
        return "Download from HuggingFace?"

    def load_question(self) -> str:
        """The yes/no question before loading a model already downloaded."""
        if self.asks_first:
            return f"Load it on the AI server at {self.host}?"
        return "Load it now?"


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
