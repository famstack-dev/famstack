"""Reachability probe for an OpenAI-compatible endpoint, and one question to it.

    probe("http://localhost:8000/v1", "omlx-key")
    # ProbeResult(reachable=True, needs_auth=False, models=["Qwen3.5-9B-MLX-4bit"])
    answers("http://localhost:8000/v1", "omlx-key", "Qwen3.5-9B-MLX-4bit")
    # Reply(text="OK", error="")
    # Reply(text="", error="HTTP 500: Failed to load the default metallib. ...")

Stdlib only (urllib) so the host CLI can call it during setup without
pulling the OpenAI SDK. ``probe`` hits ``{base_url}/models`` and reports
whether the endpoint is up, whether it needs auth, and which model ids it
lists.

A server that lists its models can still fail every completion: an oMLX
built for a newer macOS does, when it loads its Metal kernels. So
``answers`` asks the server one tiny question, and only a reply counts as
an AI that answers (`stack ai check`). ``ask`` is the one call behind it,
and behind the host commands that want a model's words (`stack ai
intro`): the bots use the OpenAI SDK in their containers
(`stack.ai.client`), the host has only this.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

__all__ = ["ProbeResult", "Reply", "ANSWER_SECONDS", "probe", "answers", "ask", "transcribes",
           "on_this_mac", "stays_home"]

# LAN endpoints often serve self-signed TLS; the AI endpoint is trusted by
# configuration (the user pointed us at it), so skip verification here.
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


@dataclasses.dataclass
class ProbeResult:
    reachable: bool
    needs_auth: bool = False
    models: list = dataclasses.field(default_factory=list)


def probe(url: str, key: str = "", *, timeout: float = 3.0) -> ProbeResult:
    """Hit ``{url}/models`` and report what we find.

    Never raises — an unreachable endpoint is a normal, expected state
    during setup (AI not up yet), not an error the caller must handle.
    """
    models_url = f"{url.rstrip('/')}/models"
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(models_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
            data = json.loads(resp.read().decode())
            model_ids = [m.get("id", "") for m in data.get("data", [])]
            return ProbeResult(reachable=True, models=model_ids)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ProbeResult(reachable=False, needs_auth=True)
        return ProbeResult(reachable=False)
    except Exception:
        return ProbeResult(reachable=False)


@dataclasses.dataclass
class Reply:
    """What a server said to one question: its words, or why there are none.

        Reply(text="OK")                                        # it answered
        Reply(error="HTTP 500: library not found")              # it failed
    """
    text: str = ""
    # The server's own error, or what kept the request from it, in one line.
    error: str = ""


# One question costs a model load when the model is not in memory yet: a
# 9B model on a 16 GB Mac loads in well under a minute.
ANSWER_SECONDS = 90.0
# The question `answers` asks, and how long its reply may be: enough for a
# model to say a word, too little to keep it busy.
QUESTION = "Reply with OK."
QUESTION_TOKENS = 8
# How much of a server's error a reply keeps: one line on a page.
ERROR_CHARS = 200


def answers(url: str, key: str = "", model: str = "", *,
            timeout: float = ANSWER_SECONDS) -> Reply:
    """Ask the server at `url` one tiny question with `model`: a `Reply`
    with its words when it answered, with its error when it did not.

    Never raises. Sends nothing a server could refuse as unknown (no
    template switches), so a hosted service answers as an oMLX does.

        answers("http://localhost:8000/v1", "omlx-key", "Qwen3.5-9B-MLX-4bit")
        # Reply(text="OK", error="")
    """
    return ask(url, key, QUESTION, model=model, max_tokens=QUESTION_TOKENS, timeout=timeout)


def ask(url: str, key: str, prompt: str, *, model: str = "", max_tokens: int,
        timeout: float, options: dict | None = None) -> Reply:
    """One chat completion at ``{url}/chat/completions``: a `Reply` with the
    first choice's text, or with the error in one line (``HTTP 500: <the
    server's message>``, or what kept the request from the server).

    `options` go into the request as they are (``temperature``, a model
    template's switches). Never raises.

        ask(url, key, "Say hello.", model="qwen3:8b", max_tokens=20, timeout=30)
        # Reply(text="Hello!", error="")
    """
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, **(options or {})}
    req = urllib.request.Request(f"{url.rstrip('/')}/chat/completions",
                                 data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
            data = json.loads(resp.read().decode())
        return Reply(text=(data["choices"][0]["message"].get("content") or "").strip())
    except urllib.error.HTTPError as e:
        return Reply(error=_one_line(f"HTTP {e.code}: {_said(e)}"))
    except urllib.error.URLError as e:
        return Reply(error=_one_line(f"No answer at {url}: {e.reason}"))
    except TimeoutError:
        return Reply(error=f"No answer at {url} within {timeout:g} seconds")
    except (OSError, ValueError, LookupError, TypeError) as e:
        return Reply(error=_one_line(f"No usable answer at {url}: {e}"))


def transcribes(url: str, key: str = "", *, timeout: float = 3.0) -> bool:
    """Whether ``{url}/audio/transcriptions`` exists.

    Asked with an empty POST, so nothing is uploaded and nothing is
    transcribed. A server with the endpoint rejects the request for the
    missing file (400, 415 or 422); one without it answers 404 or 405.
    """
    req = urllib.request.Request(
        f"{url.rstrip('/')}/audio/transcriptions", data=b"", method="POST",
        headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL):
            return True
    except urllib.error.HTTPError as e:
        return e.code in (400, 415, 422)
    except Exception:
        return False


# Tailscale hands out addresses from the carrier-grade NAT range, which
# Python does not count as private.
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def on_this_mac(url: str) -> bool:
    """Whether ``url`` names this machine: ``localhost`` or a loopback address.

    Decided from the address as written, without a DNS lookup, so it is
    cheap enough for a status line.
    """
    host = urllib.parse.urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def stays_home(url: str) -> bool:
    """Whether every address the host of ``url`` resolves to is on the
    home network: loopback, private, link-local or a Tailscale tailnet.

    Anything else is a server run by someone else, and what the stack
    sends it (document text, notes, voice) leaves the house. A name
    that does not resolve is not assumed to be at home.
    """
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip in _TAILNET):
            return False
    return bool(infos)


# ── Reading an error answer ──────────────────────────────────────────────

def _said(e: urllib.error.HTTPError) -> str:
    """The server's own message in an error answer: OpenAI's
    ``{"error": {"message"}}``, FastAPI's ``{"detail"}``, or its text."""
    try:
        raw = e.read().decode(errors="replace")
    except OSError:
        return str(e.reason)
    try:
        data = json.loads(raw)
    except ValueError:
        return raw or str(e.reason)
    if isinstance(data, dict):
        said = data.get("error", data.get("detail", raw))
        if isinstance(said, dict):
            said = said.get("message", raw)
        return str(said)
    return raw


def _one_line(text: str) -> str:
    """`text` on one line, at most `ERROR_CHARS` long."""
    line = " ".join(text.split())
    return line if len(line) <= ERROR_CHARS else line[:ERROR_CHARS - 1] + "…"
