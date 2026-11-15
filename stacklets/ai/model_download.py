"""model_download.py: fetch one model onto famstack's oMLX, with its percent.

    from .model_download import fetch, NoSpace

    fetch(ctx.stack.output, client, "mlx-community/Qwen3.5-9B-MLX-4bit", "the language model")
    # a running step "Downloading the language model" that reports its
    # percent, done once oMLX has the model; nothing when it has it already

The stack's models reach famstack's oMLX through oMLX's own downloader
(its admin API, `omlx.OMLXClient`), into the models folder it shares with
the oMLX app. A download runs inside oMLX, not in the command that
started it. So a preparation that stopped (Ctrl-C, a closed terminal, the
Mac asleep) leaves the download running, or stopped with its finished
files on disk:

- **A model oMLX has is skipped.**
- **A download of it that still runs is followed**, never started again:
  oMLX refuses a second download of a repo in progress.
- **Otherwise a new download starts.** oMLX's downloader keeps the files it
  finished, so the new one goes on from them.
- **Before a new download, the free space is checked** against the
  model's size from Hugging Face, less what a download that stopped
  left in its folder, plus `SPACE_MARGIN_BYTES`. Too little
  raises `NoSpace`, which names the size, before anything is downloaded.
- **A Mac that swaps already is told so** (`SWAP_WARNING_MB`), as a
  warning before the download: a model that does not fit beside what
  runs answers slowly. The download goes on all the same.

Progress goes through the run's output as one running step
(`spinner.progress`): the terminal shows its percent, and a screen gets
it as `step.progress` events (`stack.output.EventOutput`).

Callers: `prepare.py` (the language model), `voices.py` (the house
voice) and `backend.ensure_model` (`stack ai download`). Not here: which models the stack wants, or loading one.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from .omlx import DownloadTask, OMLXClient, repo_id_to_model_id
from .omlx_service import models_folder

__all__ = ["NoSpace", "fetch", "has", "room_for", "free_space"]

# How often a running download is asked for its progress.
POLL_SECONDS = 1.0
# A download's end states, as oMLX names them.
FINISHED = ("completed", "failed", "cancelled")
# Room the Mac keeps beyond a model: macOS, the swap file and the family's
# other services need disk too.
SPACE_MARGIN_BYTES = 2 * 1024 ** 3
# How macOS says how much it swaps; above `SWAP_WARNING_MB` the download
# comes with a warning.
SWAP_COMMAND = ("sysctl", "vm.swapusage")
SWAP_WARNING_MB = 1024


class NoSpace(RuntimeError):
    """Too little free disk for a download; the message names what it needs."""


def has(client: OMLXClient, repo: str) -> bool:
    """Whether oMLX has the model of `repo` downloaded.

        has(client, "mlx-community/Kokoro-82M-bf16")    # True once it is on disk
    """
    return repo_id_to_model_id(repo) in {m.get("id", "") for m in client.list_downloaded()}


def fetch(output, client: OMLXClient, repo: str, what: str) -> DownloadTask | None:
    """Make oMLX have the model of `repo`; `what` names it for people
    ("the language model"). Returns the finished task (its `status`
    `completed`, `failed` or `cancelled`, with oMLX's `error`), or None
    when oMLX had the model already.

    Follows a download of `repo` oMLX runs already; starts one otherwise,
    after the space check, which raises `NoSpace`, and the swap warning.
    Raises RuntimeError when oMLX does not start the download or loses
    track of it. `output` is the run's (`ctx.stack.output`); `client` is
    logged in.

        fetch(ctx.stack.output, client, "mlx-community/Kokoro-82M-bf16", "the house voice")
        # DownloadTask(status="completed", progress=100.0, ...); None the second time
    """
    if has(client, repo):
        return None
    task = _running(client, repo) or _started(output, client, repo, what)
    with output.spinner(f"Downloading {what}") as step:
        finished = _followed(client, task, step)
        if finished.status != "completed":
            step.fail(finished.error or finished.status)
    return finished


def room_for(size: int, what: str, folder: Path | None = None) -> None:
    """Raise `NoSpace` when the disk of `folder` (the models folder by
    default) has less free space than `size` bytes and
    `SPACE_MARGIN_BYTES`; the message names the size `what` needs.

        room_for(6_227_702_579, "the language model")   # returns, or raises
        # NoSpace("Not enough free disk space for the language model: it needs 5.8 GB ...")
    """
    free = free_space(folder or models_folder())
    if free < size + SPACE_MARGIN_BYTES:
        raise NoSpace(
            f"Not enough free disk space for {what}: it needs {size / 1024 ** 3:.1f} GB and "
            f"{SPACE_MARGIN_BYTES // 1024 ** 3} GB to spare, and {free / 1024 ** 3:.1f} GB "
            "are free. Free some space, then run './stack ai prepare' again.")


def free_space(path: Path) -> int:
    """The free bytes on the disk that holds `path`, or the nearest folder
    above it that exists.

        free_space(Path.home() / ".omlx" / "models")    # 412_316_860_416
    """
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free


# ── Starting and following ───────────────────────────────────────────────

def _running(client: OMLXClient, repo: str) -> DownloadTask | None:
    """The download of `repo` that oMLX still runs, if any."""
    return next((t for t in client.get_download_tasks()
                 if t.repo_id == repo and t.status not in FINISHED), None)


def _started(output, client: OMLXClient, repo: str, what: str) -> DownloadTask:
    """A new download of `repo`, once the disk has room for it; warns
    first when the Mac swaps already."""
    info = client.get_model_info(repo)
    if info is not None:
        room_for(max(info.size - _on_disk(repo), 0), what)
    if (swapping := _swap_mb()) > SWAP_WARNING_MB:
        output.warn(f"This Mac already uses {swapping / 1024:.1f} GB of swap. {what.capitalize()} "
                    "may answer slowly; closing apps or a smaller model helps.")
    task = client.start_download(repo)
    if task is None:
        raise RuntimeError(f"oMLX did not start the download of {repo}.")
    return task


def _on_disk(repo: str) -> int:
    """The bytes of `repo` in the models folder already: what a download
    that stopped left there, and a new one goes on from. oMLX downloads a
    repo into `<models folder>/<repo>`.

        _on_disk("mlx-community/Qwen3.5-9B-MLX-4bit")   # 3_113_851_289 halfway
    """
    folder = models_folder() / repo
    if not folder.is_dir():
        return 0
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def _swap_mb() -> float:
    """How many MB macOS swaps now; 0 when it cannot be read.

        _swap_mb()   # 2048.0 for "vm.swapusage: total = 4096.00M  used = 2048.00M ..."
    """
    try:
        said = subprocess.run(SWAP_COMMAND, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    used = re.search(r"used\s*=\s*([\d.]+)M", said)
    return float(used.group(1)) if used else 0.0


def _followed(client: OMLXClient, task: DownloadTask, step) -> DownloadTask:
    """The task once it ended; raises when oMLX no longer lists it.
    Reports each change of its percent on `step`."""
    reported = None
    while True:
        current = client.get_task(task.task_id)
        if current is None:
            raise RuntimeError(f"oMLX lost track of the download of {task.repo_id}.")
        if current.status in FINISHED:
            return current
        if current.progress != reported:
            step.progress(current.progress)
            reported = current.progress
        time.sleep(POLL_SECONDS)
