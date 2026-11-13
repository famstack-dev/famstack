"""Voice messages transcribed by an oMLX the admin runs, without whisper.cpp.

    from ..speech_on_omlx import speech_on_omlx

    if not speech_on_omlx(ctx):          # the AI's first start, speech on
        _install_whisper(ctx, ...)       # whisper.cpp, as before

With speech on, the AI's first start builds whisper.cpp as a dedicated
speech server: Homebrew build tools, a clone, a build, a 1.5 GB model and
a LaunchAgent, then about 1.6 GB kept in memory. An oMLX transcribes as
well, through the OpenAI route `/v1/audio/transcriptions`. Measured with
the oMLX app on ten German and English memos, `SPEECH_MODEL` had the same
word error rate and speed as whisper.cpp large-v3-turbo, and answered the
`verbose_json` fields the stack's quality check reads.

So when the stack's AI server is an oMLX the stack does not manage
(`[ai] provider = "external"`, `model_server` finds its admin API) and it
already has that model, the first start uses it instead:

    oMLX lists the model?   -> no: whisper.cpp on this Mac, as before
    then one transcription  -> 1 s of silence; the oMLX must answer 200
    then                    -> [ai] whisper_model = "whisper-large-v3-turbo-asr-fp16"

famstack downloads nothing onto a server the admin runs (route 2): the
setup promises that nothing changes there. The admin may download the
model there with `stack ai download`; `stack ai switch` then finds it.

`[ai] whisper_url` stays unset, so the framework sends voice messages to
the AI server with that model (`Stack._build_template_vars`). A later
`stack ai switch` keeps the model, because it lists as a speech model.

A missing model, a failed login or a failed transcription returns False
with a line that says why, and the caller builds whisper.cpp: voice
messages work either way.

What it does not do: touch the engine the stack runs (`provider =
"managed"` keeps whisper.cpp), a speech server the admin named with
`[ai] whisper_url`, or an instance already transcribing through
whisper.cpp. It runs only on the first start.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import uuid
import wave


from .model_server import ModelServer, model_server
from .omlx import repo_id_to_model_id

__all__ = ["SPEECH_MODEL", "speech_on_omlx"]

# The speech-to-text model put on an oMLX. Only the `-asr-` builds of
# large-v3-turbo carry the processor and tokenizer files oMLX needs: the
# most downloaded `mlx-community/whisper-large-v3-turbo` fails to load
# there with "missing the HuggingFace processor / feature-extractor
# configuration". This one matched whisper.cpp large-v3-turbo on word
# error rate and speed (2026-11-09, ten German and English memos).
SPEECH_MODEL = "mlx-community/whisper-large-v3-turbo-asr-fp16"

# One transcription of a short silence: a model that loads answers in
# about 2 s; the first load of 1.6 GB from disk takes longer.
TRANSCRIBE_TIMEOUT_SECONDS = 60


def speech_on_omlx(ctx) -> bool:
    """Make the oMLX the stack talks to transcribe voice messages; True
    when it does and `[ai] whisper_model` names the model.

    False, with nothing written, when the route does not apply (the
    stack's own engine, a speech server in `[ai] whisper_url`, a server
    that is not an oMLX); False with a line that says why when the oMLX
    lacks the model, or the login or the test transcription fails. Expects speech to be on; the caller checks that.

        speech_on_omlx(ctx)   # the oMLX app on this Mac, which has the model
        # -> True; stack.toml: [ai] whisper_model = "whisper-large-v3-turbo-asr-fp16"
    """
    server = _admins_omlx(ctx)
    if server is None:
        return False
    client = server.client()
    if not client.login():
        ctx.warn("Could not log in to oMLX with [ai] openai_key, so voice messages "
                 "go to whisper.cpp on this Mac.")
        return False

    model_id = repo_id_to_model_id(SPEECH_MODEL)
    downloaded = {m.get("id", "") for m in client.list_downloaded()}
    if model_id not in downloaded:
        # The admin's server is the admin's to fill: famstack downloads
        # nothing onto it (route 2).
        ctx.step(f"The AI server has no {model_id}, so voice messages go to whisper.cpp "
                 "on this Mac.")
        return False
    if not _transcribes(server, model_id):
        ctx.warn(f"oMLX did not transcribe with {model_id}, so voice messages "
                 "go to whisper.cpp on this Mac.")
        return False

    ctx.cfg("whisper_model", model_id)
    ctx.step(f"Voice messages are transcribed by oMLX with {model_id}")
    return True


# ── Where the route applies ──────────────────────────────────────────────

def _admins_omlx(ctx) -> ModelServer | None:
    """The oMLX the admin runs and the stack talks to, or None.

    A speech server the admin named (`[ai] whisper_url`) is their choice
    and stays; the engine the stack runs keeps its whisper.cpp.
    """
    if ctx.cfg("provider", default="") != "external" or ctx.cfg("whisper_url"):
        return None
    return model_server("external", ctx.cfg("openai_url", default=""),
                        ctx.cfg("openai_key", default=""))


# ── The test transcription ───────────────────────────────────────────────

def _transcribes(server: ModelServer, model_id: str) -> bool:
    """Whether the oMLX answers one transcription of a short silence with
    `model_id`: 200 and a JSON body with `text`. A model that downloads
    but does not load (a repo without the processor files) fails here."""
    boundary = uuid.uuid4().hex
    body = _multipart(boundary, {"model": model_id, "response_format": "json"},
                      ("file", "silence.wav", _silence()))
    request = urllib.request.Request(
        f"{server.admin_url}/v1/audio/transcriptions", data=body, method="POST",
        headers={"Authorization": f"Bearer {server.key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=TRANSCRIBE_TIMEOUT_SECONDS) as response:
            return "text" in json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _silence(seconds: float = 1.0, rate: int = 16_000) -> bytes:
    """A mono 16-bit WAV of silence, the smallest audio a speech model takes."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


def _multipart(boundary: str, fields: dict[str, str], file: tuple[str, str, bytes]) -> bytes:
    """A multipart/form-data body with `fields` and one file, as the
    OpenAI transcription route takes it. The host CLI is stdlib only, so
    there is no HTTP library to build it."""
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
             .encode() for name, value in fields.items()]
    name, filename, data = file
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                 f'filename="{filename}"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
                 + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)
