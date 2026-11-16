"""prepare.py: what famstack's oMLX needs on this Mac before it starts.

    ./stack ai prepare        # on its own: the AI's lock only
    ./stack up ai             # prepares first when that was not done

    from .prepare import prepare
    prepare(ctx)              # the `on_prepare` hook's whole body

The long part of the AI's first start, 20 to 30 minutes on a new Mac:

    1  oMLX from Homebrew            brew tap, trust, install
    2  famstack's oMLX running       `omlx_start.running`: its settings,
                                     key and LaunchAgent (the downloads go
                                     through oMLX's own downloader)
    3  the language model            `[ai] default`, with its percent
    4  the house voice               speech on (`voices.py`)
    5  Whisper and its model         speech on: build tools, source,
                                     build, 1.5 GB model, with its percent

None of it touches Docker, core or a migration. So the framework runs it
holding only the ai stacklet's run lock, outside the queue in which one
run of the stack goes at a time (`stack.runs`): Photos, Documents and
Memory start meanwhile. `stack up ai` then only starts famstack's oMLX
and Whisper, checks the answer and refreshes core, in seconds.

**It resumes.** Each step that is done is skipped: oMLX installed, the
settings as they should be, a model oMLX has, Whisper built, its model
complete. A download that stopped goes on where it stopped: oMLX keeps
the files its downloader finished and runs a download on after the
command that started it ended (`model_download.py`); Whisper's model
goes to a `.partial` file, asked for from the byte where it ends (an HTTP
range request, which Hugging Face's CDN keeps through its redirect), and
takes its own name only at the size the server states for the file.

**What stops it**, before anything is installed: the oMLX app running
(`omlx_app.QUIT_FIRST`), a settings file of famstack's oMLX it cannot
read, Homebrew missing. Before a download: too little free disk for the
model (`model_download.NoSpace`, with the size it needs).

**Your own server** (`[ai] provider = "external"`) gets nothing: famstack
installs and downloads nothing for it. With speech on, Whisper is built
on this Mac all the same, as step 5, unless a speech server of your own
(`[ai] whisper_url`) or your server (`[ai] whisper_model`,
`speech_on_omlx.py`) transcribes voice messages.

**A failure takes back the oMLX it started.** famstack's oMLX runs for
the downloads (`omlx_start.running`). When a later step fails, a stopped
download or a Whisper build included, the oMLX this run started is
stopped and its LaunchAgent removed, so it does not start at the next
login; one that ran before the run keeps running.

**What it does not do.** With no route chosen yet or no AI for now, it
installs nothing and says why. On
an instance whose oMLX still runs as Homebrew's service (set up by an
earlier release), it leaves oMLX to the start, which moves it into
famstack's folder (`hooks/on_start.py`, `homebrew_service.py`); that
instance has its models already. It never loads a model: oMLX loads one
when it is first asked.
"""

from __future__ import annotations

import http.client
import shlex
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from stack.said import Said
from stack.prompt import dim, done, out, section

from . import homebrew_omlx, homebrew_service, model_download, off, omlx_app, omlx_service
from . import whisper_service
from .omlx import OMLXClient
from .omlx_start import OMLX_URL, running
from .speech_on_omlx import speech_on_omlx
from .speech_setting import speech_is_off
from .voices import download_house_voice

__all__ = ["prepare", "install_omlx", "install_whisper", "WHISPER_MODEL"]

# What `[ai]` says instead of famstack's oMLX, and why nothing is prepared.
NOTHING_TO_PREPARE = {
    "": "No AI route is chosen yet ([ai] provider), so there is nothing to prepare.",
}

# ── oMLX ─────────────────────────────────────────────────────────────────
# Homebrew 6 refuses to load a formula from a third-party tap until that
# tap is trusted, so the trust step in `_install_omlx_formula` is what
# stands between a working preparation and one that dies at the first brew
# command. Homebrew 7 asks before an install that brings dependencies, and
# the question is buried in the run's output, where a script or an agent
# waits on it for good. The admin already said yes to setting up AI. Older
# Homebrew ignores the variable, where it would refuse `--yes`.
BREW_INSTALL = "HOMEBREW_NO_ASK=1 brew install"
OMLX_TAP = "jundot/omlx"
OMLX_TAP_URL = "https://github.com/jundot/omlx"
# How long the preparation waits for its oMLX to list its models: the
# guard's wait before every start (10 s) and oMLX's own start.
OMLX_START_SECONDS = 60
NO_HOMEBREW = (
    "Homebrew is required to install oMLX. Install it with "
    '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)", '
    "then run './stack ai prepare' again.")

# ── Whisper ──────────────────────────────────────────────────────────────
WHISPER_MODEL = whisper_service.MODEL
WHISPER_URL = f"http://localhost:{whisper_service.PORT}/v1"
WHISPER_MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{WHISPER_MODEL}"
WHISPER_SOURCE = "https://github.com/ggml-org/whisper.cpp.git"
# Earlier releases downloaded the model straight under its own name, so a
# file there may be one they cut short. Anything smaller than this is; the
# model is about 1.6 GB. New downloads end at the size the server states.
WHISPER_MODEL_MIN_BYTES = 1_000_000_000
# One read from the download, and how much of it makes a percent worth
# reporting.
CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60


def prepare(ctx) -> None:
    """Install and download what the AI's route needs on this Mac,
    skipping each step that is done:

    - famstack's oMLX (`managed`): oMLX, the language model, the house
      voice, and with speech on Whisper and its model;
    - your own server (`external`), speech on: Whisper and its model,
      unless a speech server of your own (`[ai] whisper_url`) or your
      server transcribes (`[ai] whisper_model`, `speech_on_omlx.py`).
      Your server itself gets nothing.

    Does nothing, and says why, with no route chosen or no AI for now.

    Raises `stack.hooks.Cancelled` with `omlx_app.QUIT_FIRST` while the
    oMLX app runs, `model_download.NoSpace` when a model does not fit on
    the disk, and RuntimeError for anything else that stops it (Homebrew
    missing, a failed download). Writes `[ai] openai_key` (the key in
    famstack's oMLX's settings file, `omlx.own_key`) and, for Whisper,
    `[ai] whisper_url`.

        prepare(ctx)   # a new Mac: oMLX, the model, the voice, Whisper
        prepare(ctx)   # again: "oMLX already installed", nothing downloaded
    """
    if turned_off := off.said(ctx.stack.config):   # no AI for now
        ctx.step(turned_off)
        return
    provider = ctx.cfg("provider", default="")
    if provider == "managed":
        install_omlx(ctx)
        if homebrew_service.was_famstacks():   # an instance an earlier release set up
            homebrew_service.move(ctx)
        with running(ctx, answer_seconds=OMLX_START_SECONDS):
            _language_model(ctx)
            download_house_voice(ctx)
            _speech(ctx, provider)
        return
    elif provider != "external":
        ctx.step(NOTHING_TO_PREPARE.get(provider, f"[ai] provider is {provider!r}, "
                                                  "so there is nothing to prepare."))
        return
    _speech(ctx, provider)


def install_omlx(ctx) -> None:
    """Install oMLX with Homebrew when it is missing. Starts nothing:
    `omlx_start.running` runs famstack's oMLX.

    Refuses before Homebrew runs: `stack.hooks.Cancelled` while the oMLX
    app runs, `omlx_service.UnreadableSettings` for famstack's settings
    file it cannot read, RuntimeError without Homebrew.

        install_omlx(ctx)   # "oMLX 0.8.0"
    """
    section("oMLX", "MLX inference (Metal GPU)")
    # Never beside the oMLX app: two MLX servers on one graphics chip can
    # crash the Mac. Stops before anything is installed (`omlx_app`).
    omlx_app.refuse_while_running()
    # A settings file it cannot read stops it before Homebrew runs: it
    # holds the key the family's apps use (`omlx_service.read_settings`).
    omlx_service.read_settings(omlx_service.settings_file(Path(ctx.stack.data)))
    if shutil.which("brew") is None:
        raise RuntimeError(NO_HOMEBREW)

    # The program only: an oMLX the admin installed with Homebrew keeps its
    # own settings and service. famstack runs it in a folder of its own.
    if homebrew_omlx.installed():
        done("oMLX already installed with Homebrew: famstack uses the program")
    else:
        _install_omlx_formula(ctx)
    # Homebrew installs the release its tap offers, which can be a release
    # candidate. The run says which one, and the answer check of the start
    # catches a build that cannot answer.
    done(f"oMLX {homebrew_omlx.version() or '(version unknown)'}")


def install_whisper(ctx, data_dir: Path) -> None:
    """Build Whisper's server and download its model under `data_dir`,
    each only when it is missing; a model file cut short is downloaded on
    from where it stopped. Starts nothing: `stack up ai` starts it
    (`whisper_service.start`).

    Raises RuntimeError when the build or the download fails, and
    `model_download.NoSpace` when the model does not fit on the disk.

        install_whisper(ctx, Path("/Users/homer/famstack-data"))
    """
    section("Whisper", "Speech-to-text (Metal GPU)")
    _build_whisper(ctx, data_dir)
    _whisper_model(ctx, whisper_service.model_file(data_dir))


# ── The route ────────────────────────────────────────────────────────────

def _speech(ctx, provider: str) -> None:
    """Whisper and its model when voice messages need them on this Mac."""
    if speech_is_off(ctx.stack.config):
        ctx.step("Speech is off: no Whisper")
        return
    if _needs_whisper(ctx, provider):
        install_whisper(ctx, Path(ctx.stack.data))
        # A dedicated speech server. Without it voice messages go to the
        # AI server, which may not transcribe.
        ctx.cfg("whisper_url", WHISPER_URL)
    elif provider == "external":
        ctx.step("Voice messages go to your own server: famstack installs nothing for it.")


def _needs_whisper(ctx, provider: str) -> bool:
    """Whether voice messages need Whisper on this Mac: not with a speech
    server of the admin's own in `[ai] whisper_url`, nor when the admin's
    AI server transcribes (`[ai] whisper_model`, or found so now by
    `speech_on_omlx`, which then writes it)."""
    whisper_url = ctx.cfg("whisper_url", default="")
    if whisper_url:
        return whisper_url == WHISPER_URL
    if provider != "external":
        return True
    return not (ctx.cfg("whisper_model", default="") or speech_on_omlx(ctx))


# ── oMLX, step by step ───────────────────────────────────────────────────

def _install_omlx_formula(ctx) -> None:
    """Tap, trust, and install oMLX, saying out loud what is being trusted.

    Homebrew 6 will not load a formula from a tap outside its own
    repositories until that tap has been trusted, and the refusal is
    fatal. So the trust is not an optimisation, it is the install, and it
    widens what Homebrew will run on this Mac: the admin should see it
    happen and be able to undo it.

    Three commands, not one chained with `&&`: chained, a failure anywhere
    reported the whole string, so the trust refusal looked like a tap
    failure. `brew trust` arrived in Homebrew 6; on older versions there is
    no gate to clear, so a missing subcommand means the job is done.
    """
    ctx.step(Said("Installing oMLX via Homebrew...", "ai.install_omlx"))
    ctx.shell_live(f"brew tap {OMLX_TAP} {OMLX_TAP_URL}")

    out(f"Trusting the {OMLX_TAP} Homebrew tap")
    dim("  Homebrew asks before running formulas from outside its own")
    dim("  repositories. This one publishes oMLX, the engine that runs")
    dim("  language models on your Mac's GPU.")
    dim(f"  To undo later: brew untrust {OMLX_TAP}")
    try:
        ctx.shell(f"brew trust {OMLX_TAP}")
    except RuntimeError:
        dim("  (this Homebrew has no trust gate: nothing to do)")

    ctx.shell_live(f"{BREW_INSTALL} omlx --with-grammar")


def _language_model(ctx) -> None:
    """Download `[ai] default` onto famstack's oMLX when it does not have
    it. A model named without its Hugging Face repo cannot be downloaded:
    that is said, not raised. Raises when the download fails."""
    repo = ctx.cfg("default", default="")
    if "/" not in repo:
        dim(f"[ai] default names no Hugging Face repo ({repo or 'none'}): "
            "no language model downloaded.")
        return
    # famstack's oMLX: its admin API is at the root of its address.
    url = ctx.cfg("openai_url", default=OMLX_URL).rstrip("/").removesuffix("/v1")
    client = OMLXClient(url, ctx.cfg("openai_key"))
    if not client.login():
        raise RuntimeError("Could not log in to famstack's oMLX with [ai] openai_key.")
    finished = model_download.fetch(ctx.stack.output, client, repo,
                                     Said("the language model", "language_model"))
    if finished is None:
        done(f"Language model ready: {repo}")
    elif finished.status != "completed":
        raise RuntimeError(f"The download of {repo} did not finish ({finished.error or finished.status}). "
                           "Run './stack ai prepare' again to go on.")


# ── Whisper, step by step ────────────────────────────────────────────────

def _build_whisper(ctx, data_dir: Path) -> None:
    """Homebrew's build tools, whisper.cpp's source and its build, each
    only when missing. The source is cloned into a folder of its own and
    moved into place once complete, so a clone that stopped is never taken
    for the source."""
    server = whisper_service.server(data_dir)
    if server.exists():
        done("whisper-server already built")
        return
    missing = []
    for tool in ("cmake", "ffmpeg"):
        try:
            ctx.shell(f"brew list {tool}")
        except RuntimeError:
            missing.append(tool)
    if missing:
        ctx.step(Said(f"Installing build tools: {' '.join(missing)}", "ai.build_tools"))
        ctx.shell_live(f"{BREW_INSTALL} {' '.join(missing)}")

    source = data_dir / "ai" / "whisper.cpp"
    if not source.exists():
        cloning = source.with_name("whisper.cpp.partial")
        shutil.rmtree(cloning, ignore_errors=True)   # a clone that stopped, made only here
        ctx.step(Said("Downloading whisper.cpp's source", "ai.whisper_source"))
        ctx.shell_live(shlex.join(["git", "clone", "--depth", "1", WHISPER_SOURCE, str(cloning)]))
        cloning.rename(source)

    ctx.step(Said("Building whisper-server (1 to 2 minutes, on every CPU core)",
                  "ai.whisper_build"))
    build = source / "build"
    ctx.shell(shlex.join(["cmake", "-B", str(build), "-S", str(source),
                          "-DWHISPER_BUILD_SERVER=ON", "-DGGML_METAL=ON"]))
    ctx.shell_live(shlex.join(["cmake", "--build", str(build), "-j", "--config", "Release"]))
    if not server.exists():
        raise RuntimeError(f"The Whisper build failed: see {build} for its errors.")
    done("whisper-server built")


def _whisper_model(ctx, path: Path) -> None:
    """Make `path` hold Whisper's complete model, reporting its percent.

    The download goes to `<path>.partial` and is renamed to `path` only
    when it holds the size the server states for the file, so a model
    under its own name is complete and is not asked for again. Earlier
    releases downloaded straight into `path`, so a file there smaller than
    `WHISPER_MODEL_MIN_BYTES` is one they cut short: it is deleted and
    downloaded again. A half
    file would load far enough to report "not all tensors loaded" and
    then abort, so Whisper would die on every start. A `.partial` is
    downloaded on from where it ends."""
    if path.exists() and path.stat().st_size >= WHISPER_MODEL_MIN_BYTES:
        done("Whisper model ready (large-v3-turbo)")
        return
    # A smaller one is a download an earlier release cut short.
    path.unlink(missing_ok=True)
    partial = path.with_name(path.name + ".partial")
    path.parent.mkdir(parents=True, exist_ok=True)
    with ctx.stack.output.spinner(Said("Downloading Whisper's model (about 1.5 GB)",
                                                "ai.download.whisper_model")) as step:
        try:
            _download_from(WHISPER_MODEL_URL, partial, step.progress)
        except (OSError, http.client.HTTPException, IncompleteDownload) as e:
            step.fail(str(e) or type(e).__name__)
            raise RuntimeError(f"The download of Whisper's model stopped ({e or type(e).__name__}). "
                               "Run './stack ai prepare' again to go on from there.") from None
    partial.replace(path)


class IncompleteDownload(Exception):
    """A download that ended short of the size the server stated."""


def _download_from(url: str, path: Path, progress) -> None:
    """Make `path` hold the whole file `url` serves: ask with a range
    request for what lies beyond the bytes it has and append them; a
    server that answers with the whole file (200) starts it again. The
    file's size is the one the server states: the total in
    `Content-Range` on a resume, the `Content-Length` of the whole file.
    Checks the disk has room for what is still to come
    (`model_download.NoSpace`) and calls `progress(percent)` at each new
    percent.

    A `path` larger than the stated size (the file was replaced upstream)
    starts over from the first byte, once. Raises `IncompleteDownload`
    when the file then holds another size than the server stated (a
    connection cut off), and OSError or `http.client.HTTPException` when
    the connection fails. The bytes received stay in `path` for the next
    run.

        _download_from(WHISPER_MODEL_URL, partial, step.progress)
        # partial: 1_624_555_275 bytes, as Hugging Face states it
    """
    have = path.stat().st_size if path.exists() else 0
    size = _append(url, path, have, progress)
    if size is None:     # past the end of the file the server has now
        path.unlink()
        size = _append(url, path, 0, progress)
    got = path.stat().st_size if path.exists() else 0
    if size is None or got != size:
        raise IncompleteDownload(f"{got} of {size} bytes")


def _append(url: str, path: Path, have: int, progress) -> int | None:
    """Write what `url` holds beyond byte `have` into `path`; the file's
    size as the server states it, or None when `have` is past its end."""
    headers = {"Range": f"bytes={have}-"} if have else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as refused:
        if refused.code != 416:
            raise
        # `bytes */<size>`: the range starts at or past the end.
        size = _stated_total(refused.headers.get("Content-Range", ""))
        return have if size == have else None
    with response:
        resumed = response.status == 206
        size = (_stated_total(response.headers.get("Content-Range", "")) if resumed
                else int(response.headers.get("Content-Length") or 0)) or 0
        written = have if resumed else 0
        model_download.room_for(max(size - written, 0), "Whisper's model", path.parent)
        reported = -1
        with open(path, "ab" if resumed else "wb") as file:
            while chunk := response.read(CHUNK_BYTES):
                file.write(chunk)
                written += len(chunk)
                percent = min(int(written * 100 / size), 100) if size else 0
                if percent != reported:
                    progress(percent)
                    reported = percent
    return size


def _stated_total(content_range: str) -> int | None:
    """The total of a `Content-Range` header, or None without one.

        _stated_total("bytes 1000-1999/77691713")   # 77691713
        _stated_total("bytes */77691713")           # 77691713
    """
    total = content_range.rpartition("/")[2]
    return int(total) if total.isdigit() else None
