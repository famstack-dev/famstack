"""Download the speech models of the voices in force into the oMLX the stack runs.

The stack speaks through oMLX by default (`stack.ai.voice`): Kokoro for
English, Qwen3-TTS for German, unless `[ai.voices]` names other models.
oMLX answers a speech request for a model it has not downloaded with
404, so the models are fetched the way language models are, with oMLX's
downloader (`omlx.py`). Every language's, whatever the house speaks: the
voice follows the text, and a German sentence in an English house is
read in the German voice. The stack's own two are about 2.3 GB.

A model is fetched when the stack knows where from: its own models, and
one `[ai.voices]` writes as a Hugging Face repo. Only the engine the
stack runs (`[ai] provider = "managed"`, no `[ai] speech_url`) gets
them: another server is its admin's to fill. A model already downloaded
is not fetched again. One that cannot be downloaded, or an `[ai.voices]`
entry the stack cannot use, is a warning that names it, never a failed
`up`: chat and transcription work without it.

Two hooks call this: `on_install_success`, so the first greeting can be
spoken, and `on_start_ready`, on every start.
"""

import os
import time

from stack.ai.voice import voices

from .omlx import DownloadTask, OMLXClient, repo_id_to_model_id

# How often a running download is asked for its progress.
POLL_SECONDS = 1.0
# A download's end states, as oMLX names them.
_FINISHED = ("completed", "failed", "cancelled")


def download_speech_models(ctx) -> None:
    """Fetch every speech model of the voices in force that the engine
    the stack runs is missing."""
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
    if not client.login():
        ctx.warn("Could not log in to oMLX with [ai] openai_key, so its speech models "
                 "were not checked. Speaking may fail until the key matches oMLX's.")
        return

    downloaded = {m.get("id", "") for m in client.list_downloaded()}
    for repo in dict.fromkeys(voice.repo for voice in table.values() if voice.repo):
        if repo_id_to_model_id(repo) not in downloaded:
            _download(ctx, client, repo)


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
