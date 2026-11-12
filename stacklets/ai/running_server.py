"""Offer an AI server that already runs on this Mac, before installing oMLX.

The first `stack up ai` on a fresh instance would install oMLX from
Homebrew. A Mac that already runs the oMLX app then gets a second engine
next to it: two servers, two models in memory. So before that question,
the hook `on_configure` looks at the addresses where such a server
answers and, when one does, offers it:

    An AI server already runs on this Mac at http://localhost:8000. Use it?   [yes]
    Its API key: ********                     (only when the server wants one)

A yes stores the server exactly as `stack ai switch <address> --key <key>`
does, by running that command: `[ai] provider` becomes "external", and
the install that follows sets up only the voice services, no oMLX. A no
goes on to the oMLX question as before. A key the server refuses stores
nothing, so the next `stack up ai` asks again.

Both questions are setup forms (ADR-021). Without a terminal they are
answered with `--answer use_running_server=yes --answer api_key=<key>`.
The key is a secret field, so a screen on another device sends the
admin to the terminal.

Speech comes from that server when it transcribes, and from whisper.cpp
otherwise (`speech_on_omlx.py`). What it does not do: look at other
servers' default ports. The first
start that follows offers the chat model and the house voice for it, as
for the stack's own oMLX (`model_server.py`). A further known address is one more entry in
`RUNNING_SERVERS`.

Which server is the oMLX app is decided here, once, by what answers and
not by the port (`app_at`): an address on this Mac where oMLX answers as
oMLX does (`omlx.omlx_at`). The app reads its port from the settings
file the Homebrew oMLX shares, so it can listen on 42060 as well as on
8000. Finding a running server here (`running_server`), the plan of the
AI step (`recommended.py`), the first start (`on_configure`) and the
setup page (through `stack ai recommended app`) all ask it:

    [ai] openai_url                   app_at
    http://localhost:8000/v1          True: the oMLX app at its default port
    http://localhost:42060/v1         True: the app on the stack's old port
    http://localhost:8000/v1          False: vLLM there, no oMLX admin API
    http://localhost:11434/v1         False: Ollama
    http://192.168.1.20:8000/v1       False: another machine
    http://localhost:8000/v1          None: nothing answers, the app may be closed
"""

from __future__ import annotations

from stack.ai.probe import on_this_mac, probe
from stack.forms import Form

from .local_mode import MANAGED_ENGINE
from .omlx import omlx_at

__all__ = ["RUNNING_SERVERS", "STACK_ENGINE_ADDRESSES", "running_server", "app_at",
           "is_the_app", "app_address", "app_elsewhere", "use_form", "key_form", "needs_key",
           "use"]

# Where an AI server the stack does not manage answers on this Mac: the
# oMLX app's default address. Read at each call, so a test can point it at
# its own stand-in.
RUNNING_SERVERS = ("http://localhost:8000/v1",)

# Where the stack's own engine answers. The app reads its port from the
# settings file that engine shares (`~/.omlx/settings.json`), so on a Mac
# where the stack once ran that engine the app answers here. Only the
# AI step's check looks here (`app_elsewhere`), to fill in the address.
STACK_ENGINE_ADDRESSES = (MANAGED_ENGINE["openai_url"],)

# A server on this Mac answers in milliseconds; a port that holds a
# connection open must not hold up the first start for long.
PROBE_SECONDS = 2.0


# ── Finding it ───────────────────────────────────────────────────────────

def running_server() -> str | None:
    """The first address in `RUNNING_SERVERS` where the oMLX app answers
    (`is_the_app`), or None. An app that wants a key counts: it is there.
    Another server on that port (vLLM, LM Studio) does not.

        running_server()    # 'http://localhost:8000/v1' with the oMLX app open
    """
    return next((url for url in RUNNING_SERVERS if is_the_app(url)), None)


def app_at(url: str) -> bool | None:
    """Whether `url` is the oMLX app: True when an oMLX answers there on
    this Mac (`omlx.omlx_at`), False when it is on another machine or a
    server that is not oMLX answers, None when nothing answers on this
    Mac, so a closed app cannot be told from another server. Ask it of a
    server the stack does not manage; the engine the stack installs
    answers the same API.

        app_at("http://localhost:42060/v1")    # True: the app answers there
        app_at("http://localhost:11434/v1")    # False: Ollama
        app_at("http://localhost:8000/v1")     # None: the app is closed
    """
    if not url or not on_this_mac(url):
        return False
    return omlx_at(url)


def is_the_app(url: str) -> bool:
    """Whether the oMLX app answers at `url` (`app_at` is True).

        is_the_app("http://localhost:42060/v1")    # True: the app answers there
    """
    return app_at(url) is True


def app_address(ai: dict) -> str:
    """The address of the oMLX app on this Mac, "" when none answers:
    the server `[ai]` names when it is the app (`is_the_app`), else the
    first address of `RUNNING_SERVERS` where a server answers.

        app_address({"provider": "external", "openai_url": "http://localhost:42060/v1"})
        # 'http://localhost:42060/v1' once the app answers there
    """
    stored = str(ai.get("openai_url", "")).rstrip("/")
    if ai.get("provider") == "external" and is_the_app(stored):
        return stored
    return running_server() or ""


def app_elsewhere() -> str:
    """The first of `STACK_ENGINE_ADDRESSES` where the oMLX app answers,
    "" when it answers at none. The AI step's check fills in this address
    when nothing answers at the app's default one.

        app_elsewhere()    # 'http://localhost:42060/v1' on a Mac whose settings say 42060
    """
    return next((url for url in STACK_ENGINE_ADDRESSES if is_the_app(url)), "")


def needs_key(url: str) -> bool:
    """Whether the server at `url` refuses a request without an API key.

        needs_key("http://localhost:8000/v1")   # True: the oMLX app answers 401
    """
    return probe(url, timeout=PROBE_SECONDS).needs_auth


# ── The questions ────────────────────────────────────────────────────────

def use_form(url: str) -> Form:
    """Whether the stack uses the server at `url` instead of installing oMLX.

        use_form("http://localhost:8000/v1").text
        # 'An AI server already runs on this Mac at http://localhost:8000. Use it? ...'
    """
    return Form(
        id="ai.running",
        title="AI Engine",
        text=(f"An AI server already runs on this Mac at {url.removesuffix('/v1')}. Use it?\n\n"
              "Yes points the stack at it and installs no second engine. "
              "No goes on to set up oMLX."),
        schema={
            "type": "object",
            "properties": {
                "use_running_server": {"type": "boolean", "title": "Use it?", "default": True},
            },
            "required": ["use_running_server"],
        },
    )


def key_form(url: str) -> Form:
    """The API key of the server at `url`, asked as a secret (`writeOnly`).

        key_form("http://localhost:8000/v1").pending({"api_key": "omlx-app-key"})   # None
    """
    return Form(
        id="ai.running-key",
        title="AI Engine",
        text=(f"The AI server at {url.removesuffix('/v1')} wants an API key. "
              "The oMLX app shows it under Settings, Security, API Key."),
        schema={
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "title": "API key", "writeOnly": True,
                            "minLength": 1},
            },
            "required": ["api_key"],
        },
    )


# ── Using it ─────────────────────────────────────────────────────────────

def use(ctx, url: str, key: str = "") -> None:
    """Store the server at `url` as the stack's AI server, with `key`.

    Runs `stack ai switch <url> --key <key>`, so the server is checked,
    a model it lists is chosen and `[ai]` is written the one way the
    stack writes it. Raises RuntimeError, with nothing stored, when the
    server refuses the key or the switch fails. The key never appears
    in the error.

        use(ctx, "http://localhost:8000/v1", "omlx-app-key")
        # [ai] provider = "external", openai_url = that address,
        #      openai_key = the key, default = a model the server lists
    """
    if key and probe(url, key, timeout=PROBE_SECONDS).needs_auth:
        raise RuntimeError(
            f"The AI server at {url} refused the API key. Nothing was stored; "
            "the next './stack up ai' asks again.")
    switched = ctx.stack.run_cli_command("ai", "switch", [url, *(["--key", key] if key else [])])
    if switched and "error" in switched:
        raise RuntimeError(
            f"{switched['error']}\nNothing was stored. To choose the model, run "
            f"'./stack ai switch {url} --key <its API key> --model <id>', "
            "then './stack up ai' again.")
