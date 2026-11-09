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

What it does not do: download models onto that server, take speech from
it (whisper.cpp is installed as before), or look at other servers'
default ports. A further known address is one more entry in
`RUNNING_SERVERS`.
"""

from __future__ import annotations

from stack.ai.probe import probe
from stack.forms import Form

__all__ = ["RUNNING_SERVERS", "running_server", "use_form", "key_form", "needs_key", "use"]

# Where an AI server the stack does not manage answers on this Mac: the
# oMLX app's default address. Read at each call, so a test can point it at
# its own stand-in.
RUNNING_SERVERS = ("http://localhost:8000/v1",)

# A server on this Mac answers in milliseconds; a port that holds a
# connection open must not hold up the first start for long.
PROBE_SECONDS = 2.0


# ── Finding it ───────────────────────────────────────────────────────────

def running_server() -> str | None:
    """The first address in `RUNNING_SERVERS` where an AI server answers,
    or None. A server that wants a key counts: it is there.

        running_server()    # 'http://localhost:8000/v1' with the oMLX app open
    """
    for url in RUNNING_SERVERS:
        found = probe(url, timeout=PROBE_SECONDS)
        if found.reachable or found.needs_auth:
            return url
    return None


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
              "The oMLX app shows it in its settings."),
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
            "the next 'stack up ai' asks again.")
    switched = ctx.stack.run_cli_command("ai", "switch", [url, *(["--key", key] if key else [])])
    if switched and "error" in switched:
        raise RuntimeError(
            f"{switched['error']}\nNothing was stored. To choose the model, run "
            f"'./stack ai switch {url} --key <its API key> --model <id>', "
            "then 'stack up ai' again.")
