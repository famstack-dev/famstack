"""Download the house voice's speech model onto the engine the stack runs.

The stack speaks through oMLX by default (`stack.ai.voice`): Kokoro for
English, Qwen3-TTS for German, unless `[ai.voices]` names other models.
oMLX answers a speech request for a model it has not downloaded with
404, so the model is fetched the way language models are, with oMLX's
downloader (`omlx.py`). Only the house voice's, the voice of `[ai]
language`: another language's is the admin's to
add with `stack ai download`, and `stack ai say` reads that language in
the house voice until then. A model downloaded before stays.

A model is fetched when the stack knows where from: its own models, and
one `[ai.voices]` writes as a Hugging Face repo. It goes only onto the
engine the stack runs (`[ai] provider = "managed"`). An AI server the
admin runs, such as the oMLX app or one on another machine, is the
admin's to fill (route 2): famstack downloads nothing onto it and asks
nothing, because the setup promises that nothing changes there. `stack ai
download` fetches a model onto it when the admin asks for one.

A model already downloaded is not fetched again. One that cannot be
downloaded, or an `[ai.voices]` entry the stack cannot use, is a warning
that names it, never a failed `up`: chat and transcription work without
it. So is an oMLX the stack runs that is too old to speak with the
stack's voices, with the command that updates it: the stack never
updates oMLX itself.

Three hooks call this: `on_prepare`, the long part of the first setup
(`prepare.py`); `on_install_success`, so the first greeting can be
spoken; and `on_start_ready`, on every start. The download itself, with
its percent and its resume, is `model_download.py`'s.
"""

import re

from stack.ai.voice import voice_for, voices

from .model_download import fetch
from .model_server import model_server
from .omlx import OMLXClient, repo_id_to_model_id
from .speech_setting import speech_is_off

# The oldest oMLX that speaks with the stack's voices: speech since 0.3.0,
# a cloned voice (`ref_audio`) since 0.3.5, Homebrew's Kokoro since 0.5.0
# (oMLX's release notes, checked 2026-10-27).
SPEECH_OMLX = (0, 5, 0)


def download_house_voice(ctx) -> None:
    """Fetch the house voice's speech model when the engine the stack runs
    is missing it, and say when that engine is too old to speak. Does
    nothing for an AI server the stack does not manage, or with speech
    off or a speech server of its own (`[ai] speech_url`).

        download_house_voice(ctx)    # Kokoro onto famstack's oMLX, once
    """
    if speech_is_off(ctx.stack.config) or ctx.cfg("speech_url"):
        return
    if ctx.cfg("provider", default="managed") != "managed":
        return
    try:
        table = voices(ctx.stack.config.get("ai", {}).get("voices"))
    except ValueError as e:
        ctx.warn(f"{e} No speech model was downloaded.")
        return

    server = model_server("managed", ctx.cfg("openai_url", default="http://localhost:42060/v1"),
                          ctx.cfg("openai_key"))
    client = server.client()
    if too_old := omlx_too_old(client.version()):
        ctx.warn(too_old)
    if not client.login():
        ctx.warn("Could not log in to oMLX with [ai] openai_key, so its speech models "
                 "were not checked. Speaking may fail until the key matches oMLX's.")
        return

    repo = voice_for(table, ctx.cfg("language", default="en")).repo
    downloaded = {m.get("id", "") for m in client.list_downloaded()}
    if repo and repo_id_to_model_id(repo) not in downloaded:
        _download(ctx, client, repo)


def omlx_too_old(version: str) -> str:
    """What to tell the admin about an oMLX `version` older than
    SPEECH_OMLX, or "" when it is not, or the version is unknown."""
    release = _release(version)
    if release is None or release >= SPEECH_OMLX:
        return ""
    floor = ".".join(map(str, SPEECH_OMLX))
    return (f"Speech needs oMLX {floor} or newer, and this Mac runs oMLX {version}. "
            "Update it: brew upgrade omlx, then ./stack restart ai")


def _release(version: str) -> tuple[int, ...] | None:
    """A version such as 0.6.4 as (0, 6, 4), for comparing; None for
    anything else."""
    found = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    return tuple(map(int, found.groups())) if found else None


def _download(ctx, client: OMLXClient, repo: str) -> None:
    """Fetch one model and wait for it, reporting its percent
    (`model_download`), or warn and name it."""
    try:
        task = fetch(ctx.stack.output, client, repo, f"speech model {repo}")
    except RuntimeError as e:   # NoSpace included: the voice is not worth a failed run
        return _missing(ctx, repo, str(e))
    if task is not None and task.status != "completed":
        _missing(ctx, repo, task.error or task.status)


def _missing(ctx, repo: str, reason: str) -> None:
    ctx.warn(f"Speech model {repo} could not be downloaded ({reason}). The voice that "
             "uses it stays silent until './stack up ai' downloads it.")
