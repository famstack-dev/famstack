"""Download the house voice's speech model into the oMLX the stack runs.

The stack speaks through oMLX by default (`stack.ai.voice`): Kokoro for
English, Qwen3-TTS for German, unless `[ai.voices]` names other models.
oMLX answers a speech request for a model it has not downloaded with
404, so the model is fetched the way language models are, with oMLX's
downloader (`omlx.py`). Only the house voice's, the voice of `[ai]
language` (Arthur, 2026-10-27): another language's is the admin's to
add with `stack ai download`, and `stack ai say` reads that language in
the house voice until then. A model downloaded before stays.

A model is fetched when the stack knows where from: its own models, and
one `[ai.voices]` writes as a Hugging Face repo. Only the engine the
stack runs (`[ai] provider = "managed"`, no `[ai] speech_url`) gets
it: another server is its admin's to fill. A model already downloaded
is not fetched again. One that cannot be downloaded, or an `[ai.voices]`
entry the stack cannot use, is a warning that names it, never a failed
`up`: chat and transcription work without it. So is an oMLX too old to
speak with the stack's voices, with the command that updates it: the
stack never updates oMLX itself.

Two hooks call this: `on_install_success`, so the first greeting can be
spoken, and `on_start_ready`, on every start.
"""

import os
import re
import time

from stack.ai.voice import voice_for, voices

from .omlx import DownloadTask, OMLXClient, repo_id_to_model_id

# How often a running download is asked for its progress.
POLL_SECONDS = 1.0
# A download's end states, as oMLX names them.
_FINISHED = ("completed", "failed", "cancelled")
# The oldest oMLX that speaks with the stack's voices: speech since 0.3.0,
# a cloned voice (`ref_audio`) since 0.3.5, Homebrew's Kokoro since 0.5.0
# (oMLX's release notes, checked 2026-10-27; 0.6.4 is the one tested).
SPEECH_OMLX = (0, 5, 0)


def download_house_voice(ctx) -> None:
    """Fetch the house voice's speech model when the engine the stack
    runs is missing it, and say when that engine is too old to speak."""
    # `--no-voice` is for working on the language model alone.
    if os.environ.get("STACK_AI_NO_VOICE") == "1":
        return
    if ctx.cfg("provider", default="managed") != "managed" or ctx.cfg("speech_url"):
        return
    try:
        table = voices(ctx.stack.config.get("ai", {}).get("voices"))
    except ValueError as e:
        ctx.warn(f"{e} No speech model was downloaded.")
        return

    url = ctx.cfg("openai_url", default="http://localhost:42060/v1")
    client = OMLXClient(url.rstrip("/").removesuffix("/v1"), ctx.cfg("openai_key"))
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
            "Update it: brew upgrade omlx, then stack restart ai")


def _release(version: str) -> tuple[int, ...] | None:
    """A version such as 0.6.4 as (0, 6, 4), for comparing; None for
    anything else."""
    found = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    return tuple(map(int, found.groups())) if found else None


def _download(ctx, client: OMLXClient, repo: str) -> None:
    """Fetch one model and wait for it, or warn and name it."""
    ctx.step(f"Downloading speech model {repo}")
    task = client.start_download(repo)
    if task is None:
        return _missing(ctx, repo, "oMLX did not start the download")
    task = _finished(ctx, client, task)
    if task is None:
        return _missing(ctx, repo, "oMLX lost track of the download")
    if task.status != "completed":
        _missing(ctx, repo, task.error or task.status)


def _finished(ctx, client: OMLXClient, task: DownloadTask) -> DownloadTask | None:
    """The task once it has ended, or None when oMLX no longer lists it.
    Reports each quarter of the download as it passes."""
    reported = 0
    while True:
        current = client.get_task(task.task_id)
        if current is None or current.status in _FINISHED:
            return current
        quarter = int(current.progress // 25) * 25
        if 0 < quarter < 100 and quarter > reported:
            ctx.step(f"  {repo_id_to_model_id(task.repo_id)}: {quarter}%")
            reported = quarter
        time.sleep(POLL_SECONDS)


def _missing(ctx, repo: str, reason: str) -> None:
    ctx.warn(f"Speech model {repo} could not be downloaded ({reason}). The voice that "
             "uses it stays silent until './stack up ai' downloads it.")
